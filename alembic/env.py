"""
Alembic env.py — async SQLAlchemy with Postgres advisory lock.

The advisory lock (pg_advisory_lock) prevents two concurrent Cloud Run
revisions from running migrations simultaneously on startup.
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from mocksim.config import settings
from mocksim.persistence.database import Base

# Alembic Config object
config = context.config

# Use DATABASE_URL from settings (keep the +asyncpg driver — we use
# async_engine_from_config below, which requires an async driver).
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all models so Alembic can see them for autogenerate
import mocksim.persistence.models  # noqa: F401

target_metadata = Base.metadata

_ADVISORY_LOCK_ID = 202604  # arbitrary unique int for this app


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations with a Postgres advisory lock to prevent concurrent runs."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.begin() as conn:
        # Acquire advisory lock — blocks until lock is available
        await conn.execute(text(f"SELECT pg_advisory_lock({_ADVISORY_LOCK_ID})"))
        try:
            await conn.run_sync(do_run_migrations)
        finally:
            await conn.execute(text(f"SELECT pg_advisory_unlock({_ADVISORY_LOCK_ID})"))

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
