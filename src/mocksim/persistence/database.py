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
@event.listens_for(Session, "do_orm_execute")
def _inject_tenant_filter(execute_state: object) -> None:
    """
    Automatically append WHERE mock_tenant_id = :tenant to every SELECT
    that touches a TenantScoped model. Runs synchronously in the ORM layer.
    """
    from mocksim.persistence import models as m  # avoid circular import at module load

    state = execute_state  # type: ignore[assignment]
    if not (state.is_select and not state.is_column_load and not state.is_relationship_load):  # type: ignore[attr-defined]
        return

    tenant_id = current_mock_tenant_id.get()
    if tenant_id is None:
        return  # admin / system context — no filter

    state.statement = state.statement.options(  # type: ignore[attr-defined]
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.mock_tenant_id == tenant_id,  # type: ignore[attr-defined]
            include_aliases=True,
        )
    )


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
