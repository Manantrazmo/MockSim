"""
Auth API — /auth/login, /auth/logout, /auth/me.

Uses Starlette's SessionMiddleware (signed itsdangerous cookies). The
session payload is just `{user_id, username}` — enough to identify the
caller; the actual user row lives in `admin_users`. SessionMiddleware
re-signs the cookie on every response so the rolling expiration is
straightforward.

Why cookie-based and not JWT bearer:
  - Browser JS can't read HTTP-only cookies → XSS can't steal the
    session.
  - No client-side token management — the browser handles it.
  - Same-origin (`/ui/` and `/api/` served from :8080) means SameSite=Lax
    is sufficient; no CORS gymnastics.

Service callers (trazmo, scripts) keep using Authorization: Bearer.
The two paths converge in TenancyMiddleware — see there for the
acceptance order.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from mocksim.auth.password import verify_password
from mocksim.persistence.database import async_session_factory
from mocksim.persistence.models import AdminUser

router = APIRouter(prefix="/auth", tags=["Auth"])
log = structlog.get_logger(__name__)


# ── Schemas ──────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)


class MeResponse(BaseModel):
    user_id: str
    username: str
    full_name: str | None
    last_login_at: datetime | None


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/login", response_model=MeResponse)
async def login(body: LoginRequest, request: Request) -> MeResponse:
    """
    Verify credentials, write the session cookie, return the user.

    No rate-limiting here — relies on TenancyMiddleware's ambient
    handling and the fact that MockSim is a sim, not a production
    service. Add a token-bucket if this ever goes external.
    """
    async with async_session_factory() as session:
        row = await session.execute(
            select(AdminUser).where(
                AdminUser.username == body.username,
                AdminUser.is_active.is_(True),
            )
        )
        user = row.scalar_one_or_none()
        if user is None or not verify_password(body.password, user.password_hash):
            log.warning("auth.login.failed", username=body.username)
            raise HTTPException(status_code=401, detail="Invalid username or password")

        user.last_login_at = datetime.now(timezone.utc)
        await session.commit()

        # Detach a copy of what we need before the session closes.
        user_id = str(user.id)
        username = user.username
        full_name = user.full_name
        last_login_at = user.last_login_at

    # Set the session — SessionMiddleware re-signs on response.
    request.session["user_id"] = user_id
    request.session["username"] = username

    log.info("auth.login.ok", user_id=user_id, username=username)
    return MeResponse(
        user_id=user_id,
        username=username,
        full_name=full_name,
        last_login_at=last_login_at,
    )


@router.post("/logout")
async def logout(request: Request) -> dict[str, Any]:
    """Drop the session. Idempotent — calling without an active session is fine."""
    had_session = bool(request.session.get("user_id"))
    request.session.clear()
    return {"logged_out": had_session}


@router.get("/me", response_model=MeResponse)
async def me(request: Request) -> MeResponse:
    """
    Return the current operator. Returns 401 when not logged in — the
    dashboard uses this on mount to decide whether to render the login
    screen or the app shell.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async with async_session_factory() as session:
        row = await session.execute(
            select(AdminUser).where(AdminUser.id == uuid.UUID(user_id))
        )
        user = row.scalar_one_or_none()
        if user is None or not user.is_active:
            request.session.clear()  # stale session
            raise HTTPException(status_code=401, detail="User no longer active")
        return MeResponse(
            user_id=str(user.id),
            username=user.username,
            full_name=user.full_name,
            last_login_at=user.last_login_at,
        )
