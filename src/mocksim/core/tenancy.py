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

# Path prefixes that serve static files / login-without-auth endpoints
_PUBLIC_PREFIXES = ("/ui", "/api/v1/auth/login", "/api/v1/auth/logout")

# Prefix for admin-protected routes (session OR bearer admin token)
_ADMIN_PREFIX = "/api/v1/admin"

# /auth/me reads session; it's its own special case — auth required but
# accepts only session, not bearer (operators only).
_AUTH_ME_PATH = "/api/v1/auth/me"


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

        # Static dashboard files + login/logout — no auth required
        if request.url.path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        # /auth/me — session-only path. Let it through; the handler
        # returns 401 itself if no session is present.
        if request.url.path == _AUTH_ME_PATH:
            return await call_next(request)

        # Admin endpoints: session OR bearer admin token.
        if request.url.path.startswith(_ADMIN_PREFIX):
            return await self._handle_admin(request, call_next)

        # Tenant endpoints: bearer tenant key (service callers) OR
        # admin session + X-Act-As-Tenant header (dashboard reads).
        return await self._handle_tenant(request, call_next)

    async def _handle_admin(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """
        /admin/* auth. Accepts EITHER an active session (humans via the
        dashboard) OR Authorization: Bearer <MOCKSIM_ADMIN_TOKEN> (scripts,
        curl, the seed CLI). Both pass through with no tenant filter.
        """
        from mocksim.config import settings

        # Path 1: session-authenticated operator
        if _has_active_session(request):
            request.state.mock_tenant_id = None
            request.state.trazmo_tenant_id = None
            request.state.admin_session = True
            return await call_next(request)

        # Path 2: bearer admin token (legacy / service callers)
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token and token == settings.mocksim_admin_token:
            request.state.mock_tenant_id = None
            request.state.trazmo_tenant_id = None
            request.state.admin_session = False
            return await call_next(request)

        return _auth_error("Admin authentication required", status_code=401)

    async def _handle_tenant(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """
        Tenant endpoint auth. Two routes in:

          (a) Authorization: Bearer <tenant-api-key>
              Standard service-to-service path. Looks up api_keys → tenant.

          (b) Admin session + X-Act-As-Tenant: <tenant_uuid>
              Dashboard path. The logged-in operator declares which tenant
              to read as; we trust them since they're an admin.
        """
        # Path (b) — admin session with explicit tenant override
        if _has_active_session(request):
            act_as = request.headers.get("X-Act-As-Tenant", "").strip()
            if not act_as:
                return _auth_error(
                    "X-Act-As-Tenant header required for tenant endpoints when using session auth",
                    status_code=400,
                )
            try:
                tenant_uuid = uuid.UUID(act_as)
            except ValueError:
                return _auth_error("X-Act-As-Tenant must be a UUID", status_code=400)

            # Admins get full scope set.
            token = current_mock_tenant_id.set(tenant_uuid)
            request.state.mock_tenant_id = tenant_uuid
            request.state.scopes = ["admin.*"]
            request.state.trazmo_tenant_id = request.headers.get("X-Trazmo-Tenant-Id")
            request.state.admin_session = True
            try:
                return await call_next(request)
            finally:
                current_mock_tenant_id.reset(token)

        # Path (a) — bearer tenant key
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

        token = current_mock_tenant_id.set(tenant_id)
        request.state.mock_tenant_id = tenant_id
        request.state.scopes = scopes
        request.state.trazmo_tenant_id = request.headers.get("X-Trazmo-Tenant-Id")
        request.state.admin_session = False

        try:
            response = await call_next(request)
        finally:
            current_mock_tenant_id.reset(token)
        return response


def _has_active_session(request: Request) -> bool:
    """True iff Starlette SessionMiddleware has a populated session for us."""
    try:
        return bool(request.session.get("user_id"))
    except (AssertionError, AttributeError):
        # SessionMiddleware not configured — should never happen in normal runs.
        return False


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
