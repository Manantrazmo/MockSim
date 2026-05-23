"""
TenancyMiddleware — resolves mock_tenant_id from the Authorization header
and stores it in a contextvar for the duration of each request.

Two-level tenancy model (DESIGN.md §6.9):
  mock_tenant_id   — MockSim's own isolation (dev/CI/staging environments)
  trazmo_tenant_id — Trazmo's customer isolation, passed through as-is

mock_tenant_id is NEVER taken from the request body or query string.
It is ONLY resolved from the Authorization: Bearer <api_key> header.
"""
from __future__ import annotations
import hashlib
import uuid
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from mocksim.persistence.database import async_session_factory, current_mock_tenant_id
from mocksim.persistence.models import ApiKey

# Paths that don't require authentication
_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

# Path prefixes that serve static files — no auth required
_PUBLIC_PREFIXES = ("/ui",)

# Prefix for admin-token-protected routes
_ADMIN_PREFIX = "/api/v1/admin"


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw API key. Only the hash is stored in DB."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


class TenancyMiddleware(BaseHTTPMiddleware):
    """
    1. Reads Authorization: Bearer <key>
    2. Hashes key, looks up mock_tenant_id in api_keys table
    3. Sets current_mock_tenant_id contextvar
    4. Reads X-Trazmo-Tenant-Id header (passthrough, no validation)
    5. Stores both in request.state for downstream access
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # Static dashboard files — no auth required
        if request.url.path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        # Admin endpoints use a separate token
        if request.url.path.startswith(_ADMIN_PREFIX):
            return await self._handle_admin(request, call_next)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return _auth_error("Missing or malformed Authorization header")

        raw_key = auth.removeprefix("Bearer ").strip()
        if not raw_key:
            return _auth_error("Empty API key")

        key_hash = _hash_key(raw_key)
        resolved = await _resolve_tenant(key_hash)
        if resolved is None:
            return _auth_error("Invalid API key")

        tenant_id, scopes = resolved

        # Set contextvar — picked up by SQLAlchemy tenant filter + business logic
        token = current_mock_tenant_id.set(tenant_id)
        request.state.mock_tenant_id = tenant_id
        request.state.scopes = scopes  # used by require_scope() dependency
        request.state.trazmo_tenant_id = request.headers.get("X-Trazmo-Tenant-Id")

        try:
            response = await call_next(request)
        finally:
            current_mock_tenant_id.reset(token)

        return response

    async def _handle_admin(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        from mocksim.config import settings

        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token != settings.mocksim_admin_token:
            return _auth_error("Invalid admin token", status_code=403)

        # Admin context: no tenant filter (can see all tenants)
        request.state.mock_tenant_id = None
        request.state.trazmo_tenant_id = None
        return await call_next(request)


async def _resolve_tenant(key_hash: str) -> tuple[uuid.UUID, list[str]] | None:
    """Return (mock_tenant_id, scopes) or None if key not found / revoked."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ApiKey.mock_tenant_id, ApiKey.scopes).where(
                ApiKey.key_hash == key_hash,
                ApiKey.status == "active",
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return row.mock_tenant_id, row.scopes or []


def _auth_error(message: str, status_code: int = 401) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": "INVALID_API_KEY", "message": message, "trace_id": "", "details": {}},
    )


def require_scope(scope: str) -> Callable:
    """FastAPI dependency: raises 403 if the current API key lacks the required scope."""
    async def _check(request: Request) -> None:
        scopes: list[str] = getattr(request.state, "scopes", [])
        if scope not in scopes and "admin.*" not in scopes:
            from mocksim.core.errors import forbidden
            raise forbidden(f"Required scope: {scope}")
    return _check
