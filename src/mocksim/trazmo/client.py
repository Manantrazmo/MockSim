"""
mocksim.trazmo.client — HTTP client for trazmo-platform's
/api/v1/_internal/mocksim/* service surface.

This module USED to write directly to trazmo's postgres via asyncpg.
That was wrong: anyone with network access to trazmo's :5433 could do
anything, with no audit, no validation, no access control. Phase H
replaces those raw INSERTs with calls to a service-token-protected
HTTP API on the trazmo side (see modules/_internal/mocksim_router.py
in trazmo-platform).

Two env vars drive everything:
  TRAZMO_API_URL        e.g. http://host.docker.internal:8000
  TRAZMO_SERVICE_TOKEN  shared secret with trazmo's MOCKSIM_SERVICE_TOKEN

Public surface (unchanged from the asyncpg version — caller doesn't
care that the implementation changed):

  resolve_bootstrap(partner_code)      → BootstrapHandles
  list_lenders()                        → list[LenderRow]
  list_sme_entities(partner_code)       → list[SmeRow]
  onboard_sme(...)                      → OnboardedSme

Errors:
  TrazmoNotBootstrapped — partner / pool / currency missing on the
                          trazmo side; caller should run trazmo's seeds.
  TrazmoServiceError    — any other non-2xx from the trazmo API,
                          carries the HTTP status and body for diagnosis.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger(__name__)


# ── Lookup result types (unchanged shape — callers don't recompile) ─────────


@dataclass(frozen=True)
class BootstrapHandles:
    tenant_id: uuid.UUID
    partner_entity_id: uuid.UUID
    pool_id: uuid.UUID
    currency_id: uuid.UUID
    entity_type_sme_id: uuid.UUID
    country_code: str
    timezone: str


@dataclass(frozen=True)
class LenderRow:
    entity_id: uuid.UUID
    code: str
    legal_name: str


@dataclass(frozen=True)
class SmeRow:
    entity_id: uuid.UUID
    code: str
    legal_name: str
    acquirer_merchant_id: str | None
    mcc: str | None
    status: str


@dataclass(frozen=True)
class OnboardedSme:
    entity_id: uuid.UUID
    sme_profile_id: uuid.UUID
    merchant_profile_id: uuid.UUID | None
    acquirer_merchant_id: str
    acquirer_mapping_id: uuid.UUID


# ── Errors ──────────────────────────────────────────────────────────────────


class TrazmoNotBootstrapped(Exception):
    """Trazmo side isn't seeded for the requested partner."""


class TrazmoServiceError(Exception):
    """Non-2xx from trazmo's service API. Carries status + body."""
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"trazmo /_internal/mocksim returned HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class TrazmoNotConfigured(Exception):
    """TRAZMO_API_URL or TRAZMO_SERVICE_TOKEN missing on MockSim side."""


# ── HTTP client wrapper ─────────────────────────────────────────────────────


@dataclass
class TrazmoClient:
    """
    Tiny stateless wrapper. Each call opens a short-lived httpx.AsyncClient;
    the call rate from the onboarding UI is low so connection pooling
    isn't worth the lifecycle complexity. If usage grows we'll switch to
    a long-lived pool stored on app state.
    """
    base_url: str
    service_token: str
    timeout_s: float = 10.0

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.service_token}",
            "Content-Type": "application/json",
        }

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        async with httpx.AsyncClient(timeout=self.timeout_s) as c:
            r = await c.get(f"{self.base_url}{path}", params=params, headers=self._headers())
        if r.status_code == 404:
            raise TrazmoNotBootstrapped(r.json().get("detail", "not found"))
        if not r.is_success:
            raise TrazmoServiceError(r.status_code, r.text)
        return r.json()

    async def _post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout_s) as c:
            r = await c.post(f"{self.base_url}{path}", json=body, headers=self._headers())
        if r.status_code == 404:
            raise TrazmoNotBootstrapped(r.json().get("detail", "not found"))
        if not r.is_success:
            raise TrazmoServiceError(r.status_code, r.text)
        return r.json()


# ── Module-level convenience that reads from settings ────────────────────────


def _client_from_settings() -> TrazmoClient:
    from mocksim.config import settings
    if not settings.trazmo_api_url or not settings.trazmo_service_token:
        raise TrazmoNotConfigured(
            "Set TRAZMO_API_URL and TRAZMO_SERVICE_TOKEN in MockSim's env so "
            "the cross-system onboarding endpoint can call trazmo. The token "
            "must match trazmo's MOCKSIM_SERVICE_TOKEN."
        )
    return TrazmoClient(
        base_url=settings.trazmo_api_url.rstrip("/"),
        service_token=settings.trazmo_service_token,
    )


# ── Public functions (kept signature-compatible with the asyncpg version) ──


async def resolve_bootstrap(
    *,
    partner_code: str,
    country_code: str = "PK",
    timezone: str = "Asia/Karachi",
) -> BootstrapHandles:
    client = _client_from_settings()
    data = await client._get("/api/v1/_internal/mocksim/bootstrap",
                             params={"partner_code": partner_code})
    return BootstrapHandles(
        tenant_id=uuid.UUID(data["tenant_id"]),
        partner_entity_id=uuid.UUID(data["partner_entity_id"]),
        pool_id=uuid.UUID(data["pool_id"]),
        currency_id=uuid.UUID(data["currency_id"]),
        entity_type_sme_id=uuid.UUID(data["entity_type_sme_id"]),
        country_code=data.get("country_code", country_code),
        timezone=timezone,
    )


async def list_lenders() -> list[LenderRow]:
    client = _client_from_settings()
    rows = await client._get("/api/v1/_internal/mocksim/lenders")
    assert isinstance(rows, list)
    return [
        LenderRow(entity_id=uuid.UUID(r["id"]), code=r["code"], legal_name=r["legal_name"])
        for r in rows
    ]


async def list_sme_entities(*, partner_code: str) -> list[SmeRow]:
    client = _client_from_settings()
    rows = await client._get("/api/v1/_internal/mocksim/smes",
                             params={"partner_code": partner_code})
    assert isinstance(rows, list)
    return [
        SmeRow(
            entity_id=uuid.UUID(r["entity_id"]),
            code=r["code"],
            legal_name=r["legal_name"],
            acquirer_merchant_id=r.get("acquirer_merchant_id"),
            mcc=r.get("mcc"),
            status=r["status"],
        )
        for r in rows
    ]


async def onboard_sme(
    *,
    partner_code: str,
    sme_code: str,
    legal_name: str,
    owner_name: str,
    mcc: str,
    country_code: str = "PK",
    timezone: str = "Asia/Karachi",
    acquirer_merchant_id: str | None = None,
    terminal_ids: list[str] | None = None,
    visibility: str = "private",
    lender_entity_id: str | None = None,
) -> OnboardedSme:
    """
    Single-call cross-system onboarding. Trazmo creates entity +
    sme_profile + merchant_profile + acquirer_mapping atomically and
    emits the relevant audit event. We just unbox the IDs.

    Phase J adds visibility + lender_entity_id. Private mode requires
    an explicit lender; public is reserved (server 501s).
    """
    client = _client_from_settings()
    body = {
        "partner_code": partner_code,
        "sme_code": sme_code,
        "legal_name": legal_name,
        "owner_name": owner_name,
        "mcc": mcc,
        "country_code": country_code,
        "timezone": timezone,
        "acquirer_merchant_id": acquirer_merchant_id,
        "terminal_ids": terminal_ids,
        "visibility": visibility,
        "lender_entity_id": lender_entity_id,
    }
    data = await client._post("/api/v1/_internal/mocksim/onboard-merchant", body)
    log.info(
        "trazmo.sme_onboarded",
        sme_code=sme_code,
        entity_id=data["entity_id"],
        acquirer_merchant_id=data["acquirer_merchant_id"],
        created=[
            k.removeprefix("created_") for k in data.keys()
            if k.startswith("created_") and data[k]
        ],
    )
    return OnboardedSme(
        entity_id=uuid.UUID(data["entity_id"]),
        sme_profile_id=uuid.UUID(data["sme_profile_id"]),
        merchant_profile_id=uuid.UUID(data["merchant_profile_id"]),
        acquirer_merchant_id=data["acquirer_merchant_id"],
        acquirer_mapping_id=uuid.UUID(data["acquirer_mapping_id"]),
    )
