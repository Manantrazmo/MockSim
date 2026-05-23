"""
Async SQLAlchemy engine + session factory.

Tenant isolation: every request has mock_tenant_id stored in a contextvar.
A do_orm_execute event listener injects WHERE mock_tenant_id = :tenant on every
SELECT touching a TenantScoped model. This is enforced at the DB layer, not the
application layer, so a bug in business logic cannot leak cross-tenant data.
"""
from __future__ import annotations
import uuid
from contextvars import ContextVar
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, with_loader_criteria

from mocksim.config import settings

# ── Contextvar: set by TenancyMiddleware on every request ─────────
current_mock_tenant_id: ContextVar[uuid.UUID | None] = ContextVar(
    "current_mock_tenant_id", default=None
)


# ── SQLAlchemy base ───────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


class TenantScoped:
    """Mixin marker — models that carry mock_tenant_id and must be filtered."""
    pass


# ── Engine + session factory ──────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ── Tenant isolation event hook ───────────────────────────────────
# Discovered once on first use: every concrete mapped class that mixes in
# TenantScoped. We can't pass the TenantScoped mixin itself to
# with_loader_criteria because it isn't an instrumented SQLAlchemy mixin
# (mock_tenant_id is declared per-model, not via @declared_attr on the
# mixin), so SQLAlchemy would invoke our criteria lambda with cls=TenantScoped
# and fail to find the column.
_tenant_scoped_mappers: list[type] | None = None


def _get_tenant_scoped_mappers() -> list[type]:
    global _tenant_scoped_mappers
    if _tenant_scoped_mappers is None:
        # Import models so all mappers are registered before we walk the registry.
        from mocksim.persistence import models as _models  # noqa: F401

        _tenant_scoped_mappers = [
            mapper.class_
            for mapper in Base.registry.mappers
            if issubclass(mapper.class_, TenantScoped) and mapper.class_ is not TenantScoped
        ]
    return _tenant_scoped_mappers


@event.listens_for(Session, "do_orm_execute")
def _inject_tenant_filter(execute_state: object) -> None:
    """
    Automatically append WHERE mock_tenant_id = :tenant to every SELECT
    that touches a TenantScoped model. Runs synchronously in the ORM layer.
    """
    state = execute_state  # type: ignore[assignment]
    if not (state.is_select and not state.is_column_load and not state.is_relationship_load):  # type: ignore[attr-defined]
        return

    tenant_id = current_mock_tenant_id.get()
    if tenant_id is None:
        return  # admin / system context — no filter

    # Apply one criteria per concrete tenant-scoped class. SQLAlchemy only
    # injects the WHERE for classes actually referenced by the statement,
    # so the per-class loop is cheap.
    options = [
        with_loader_criteria(
            cls,
            cls.mock_tenant_id == tenant_id,  # type: ignore[attr-defined]
            include_aliases=True,
        )
        for cls in _get_tenant_scoped_mappers()
    ]
    if options:
        state.statement = state.statement.options(*options)  # type: ignore[attr-defined]


# ── FastAPI dependency ────────────────────────────────────────────
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a scoped async session for use in FastAPI route dependencies."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
