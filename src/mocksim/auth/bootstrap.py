"""
On startup, if `admin_users` is empty, create a default operator using
credentials from env. This makes a fresh install demo-able with zero
manual SQL: alembic migrates → bootstrap runs → user can log in to
the dashboard at /ui/login.

The first-time default credentials are intentionally weak (`admin` /
`admin`) — they're meant to be visible in startup logs so the operator
can change them immediately. The first thing the dashboard should
nudge a fresh install to do is rotate the password. (Phase H.)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select

from mocksim.auth.password import hash_password
from mocksim.persistence.database import async_session_factory
from mocksim.persistence.models import AdminUser

log = structlog.get_logger(__name__)


DEFAULT_USERNAME = "admin"


async def ensure_default_admin() -> None:
    """
    Create the bootstrap admin user if none exists. Idempotent — safe to
    call on every startup.

    Password source priority:
      1. MOCKSIM_BOOTSTRAP_PASSWORD env var (preferred — set this in
         secrets management so the credential isn't `admin`/`admin`).
      2. The literal string `admin` (dev-only fallback, logged loudly).
    """
    async with async_session_factory() as session:
        existing = (await session.execute(select(func.count(AdminUser.id)))).scalar() or 0
        if existing:
            log.debug("auth.bootstrap.skip", existing_users=existing)
            return

        password = os.environ.get("MOCKSIM_BOOTSTRAP_PASSWORD") or "admin"
        warn_default = (password == "admin")

        admin = AdminUser(
            username=DEFAULT_USERNAME,
            password_hash=hash_password(password),
            full_name="MockSim Default Admin",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        session.add(admin)
        await session.commit()

        if warn_default:
            log.warning(
                "auth.bootstrap.created_default_admin",
                username=DEFAULT_USERNAME,
                password="admin",
                hint="set MOCKSIM_BOOTSTRAP_PASSWORD before first start to use a real password",
            )
        else:
            log.info(
                "auth.bootstrap.created_default_admin",
                username=DEFAULT_USERNAME,
                hint="password was taken from MOCKSIM_BOOTSTRAP_PASSWORD",
            )
