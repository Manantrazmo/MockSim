"""
Admin API — clock control, tenant management, scenario engine, observability.
All /admin/* endpoints require the admin token (not a tenant API key).
"""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text

from mocksim.clock import clock
from mocksim.core import scenarios as scenario_engine
from mocksim.core.errors import not_found, ErrorCode
from mocksim.core.identifiers import new_ulid
from mocksim.persistence.database import async_session_factory
from mocksim.persistence.models import (
    ApiKey,
    ClockAdvanceJob,
    Merchant,
    MockTenant,
    ScenarioEngineStatus,
    WebhookOutbox,
)

router = APIRouter(prefix="/admin", tags=["Admin"])
log = structlog.get_logger(__name__)


# ── Ping ──────────────────────────────────────────────────────────

@router.get("/ping")
async def ping() -> dict[str, Any]:
    return {"status": "ok", "sim_time": clock.now().isoformat()}


# ── Clock control ─────────────────────────────────────────────────

class AdvanceClockRequest(BaseModel):
    days: int = Field(0, ge=0)
    hours: int = Field(0, ge=0)
    minutes: int = Field(0, ge=0)


class SetClockRequest(BaseModel):
    target: datetime


@router.post("/clock/advance")
async def advance_clock(body: AdvanceClockRequest, request: Request) -> dict[str, Any]:
    """
    Advance the simulation clock. Sliced into 1-day increments.
    Returns 202 + job_id for advances > 7 sim-days or when budget exceeded.
    """
    from mocksim.core import sim_scheduler

    duration = timedelta(days=body.days, hours=body.hours, minutes=body.minutes)
    if duration.total_seconds() <= 0:
        return {"status": "noop", "sim_time": clock.now().isoformat()}

    # Admin context has no tenant; use a system UUID
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    return await sim_scheduler.advance(duration, tenant_id, requested_by="admin")


@router.get("/clock/advance/{job_id}")
async def poll_advance_job(job_id: str) -> dict[str, Any]:
    """Poll the status of an async clock advance."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ClockAdvanceJob).where(ClockAdvanceJob.id == uuid.UUID(job_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            raise not_found(ErrorCode.INTERNAL_ERROR, f"Advance job {job_id} not found")
        return {
            "job_id": str(job.id),
            "status": job.status,
            "slices_done": job.slices_done,
            "slices_total": job.slices_total,
            "target_sim_time": job.target_sim_time.isoformat(),
            "current_sim_time": clock.now().isoformat(),
            "error": job.error,
        }


@router.post("/clock/set")
async def set_clock(body: SetClockRequest) -> dict[str, Any]:
    """Pin the simulation clock to a specific datetime."""
    new_time = clock.set(body.target)
    log.info("admin.clock.set", sim_time=new_time.isoformat())
    return {"status": "ok", "sim_time": new_time.isoformat()}


@router.get("/clock")
async def get_clock() -> dict[str, Any]:
    return {"sim_time": clock.now().isoformat()}


# ── Tenant management ─────────────────────────────────────────────

class CreateTenantRequest(BaseModel):
    name: str
    api_key: str = Field(..., min_length=32)
    scopes: list[str] = Field(default_factory=lambda: ["pos.read", "pos.write", "bank.read", "bank.write"])
    # Optional: the trazmo partner_profile.code this MockSim tenant impersonates.
    # When set, the trazmo_settlement webhook emitter will use this in the
    # outbound payload's `partner_code` field.
    partner_code: str | None = Field(default=None, max_length=64)


@router.get("/tenants")
async def list_tenants() -> dict[str, Any]:
    """List all MockSim tenants — feeds the dashboard's tenant selector."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(MockTenant).order_by(MockTenant.created_at)
        )
        rows = result.scalars().all()
    return {
        "tenants": [
            {
                "id": str(t.id),
                "name": t.name,
                "partner_code": t.partner_code,
                "created_at": t.created_at.isoformat(),
            }
            for t in rows
        ]
    }


@router.post("/tenants")
async def create_tenant(body: CreateTenantRequest, response: Response) -> dict[str, Any]:
    """
    Create a new mock tenant with an initial API key.

    Idempotent: if the supplied api_key hash already exists, returns the
    existing tenant with HTTP 200 instead of 201. This lets bootstrap
    scripts re-run without --reset.
    """
    import hashlib

    key_hash = hashlib.sha256(body.api_key.encode()).hexdigest()

    async with async_session_factory() as session:
        # Idempotent path: same api_key already issued → return existing tenant.
        existing = await session.execute(
            select(ApiKey.mock_tenant_id).where(ApiKey.key_hash == key_hash)
        )
        existing_tenant_id = existing.scalar_one_or_none()
        if existing_tenant_id is not None:
            response.status_code = 200
            log.info("admin.tenant.exists", tenant_id=str(existing_tenant_id), name=body.name)
            return {
                "tenant_id": str(existing_tenant_id),
                "name": body.name,
                "api_key_hint": body.api_key[:8] + "...",
                "existed": True,
            }

        tenant = MockTenant(
            id=uuid.uuid4(),
            name=body.name,
            partner_code=body.partner_code,
            created_at=datetime.now(timezone.utc),
        )
        session.add(tenant)
        await session.flush()

        api_key = ApiKey(
            key_hash=key_hash,
            mock_tenant_id=tenant.id,
            name=f"{body.name} default key",
            scopes=body.scopes,
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        session.add(api_key)
        await session.commit()

    response.status_code = 201
    log.info("admin.tenant.created", tenant_id=str(tenant.id), name=body.name)
    return {
        "tenant_id": str(tenant.id),
        "name": body.name,
        "api_key_hint": body.api_key[:8] + "...",
        "existed": False,
    }


@router.post("/reset")
async def reset_tenant(tenant_id: str, purge: bool = False) -> dict[str, Any]:
    """
    Wipe all data for a mock_tenant_id.

    Default (purge=false) deletes only domain data (merchants, accounts,
    transactions, payments, mandates, webhooks, idempotency, scenarios).
    The tenant row and its api_keys are preserved so existing dashboards
    keep working.

    purge=true additionally deletes the MockTenant row + ApiKey rows,
    so the same api_key can be reissued from scratch by /admin/tenants.
    """
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        from mocksim.persistence.models import (
            Account, AccountEntry, ApiKey, IdempotencyRecord, Mandate,
            Merchant, MockTenant, PaymentInstruction, PosTransaction,
            ScenarioConfig, SettlementBatch, WebhookOutbox, WebhookSubscription,
        )
        # Domain data — order matters for FK constraints (children first).
        domain_models = [
            AccountEntry, Account, PaymentInstruction, Mandate,
            PosTransaction, SettlementBatch, WebhookOutbox,
            WebhookSubscription, IdempotencyRecord, ScenarioConfig,
            Merchant,
        ]
        for model in domain_models:
            await session.execute(
                delete(model).where(model.mock_tenant_id == tid)  # type: ignore[attr-defined]
            )
        if purge:
            await session.execute(delete(ApiKey).where(ApiKey.mock_tenant_id == tid))
            await session.execute(delete(MockTenant).where(MockTenant.id == tid))
        await session.commit()

    log.info("admin.tenant.reset", tenant_id=tenant_id, purge=purge)
    return {"status": "purged" if purge else "reset", "tenant_id": tenant_id}


# ── Scenario engine ───────────────────────────────────────────────

@router.get("/scenarios/status")
async def scenario_status() -> dict[str, Any]:
    enabled = scenario_engine.is_engine_enabled()
    return {"enabled": enabled, "known_scenarios": sorted(scenario_engine.KNOWN_SCENARIOS)}


@router.post("/scenarios/enable")
async def enable_scenarios() -> dict[str, Any]:
    await _persist_scenario_status(True)
    scenario_engine.set_engine_enabled(True)
    return {"enabled": True}


@router.post("/scenarios/disable")
async def disable_scenarios() -> dict[str, Any]:
    await _persist_scenario_status(False)
    scenario_engine.set_engine_enabled(False)
    return {"enabled": False}


async def _persist_scenario_status(enabled: bool) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(ScenarioEngineStatus).where(ScenarioEngineStatus.id == 1)
        )
        row = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if row:
            row.enabled = enabled
            row.updated_at = now
            row.updated_by = "admin"
        else:
            session.add(ScenarioEngineStatus(id=1, enabled=enabled, updated_at=now, updated_by="admin"))
        await session.commit()


# ── Webhook replay ────────────────────────────────────────────────

@router.get("/outbox")
async def list_outbox(
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List webhook outbox entries across all tenants (admin view)."""
    async with async_session_factory() as session:
        q = select(WebhookOutbox).order_by(WebhookOutbox.created_at.desc()).limit(min(limit, 500))
        if status:
            q = q.where(WebhookOutbox.status == status)
        result = await session.execute(q)
        rows = result.scalars().all()

    return {
        "items": [
            {
                "event_id": r.event_id,
                "event_type": r.event_type,
                "partition_key": r.partition_key,
                "status": r.status,
                "attempt_count": r.attempt_count,
                "target_url": r.target_url,
                "last_error": r.last_error,
                "created_at": r.created_at.isoformat(),
                "delivered_at": r.delivered_at.isoformat() if r.delivered_at else None,
                "next_attempt_at": r.next_attempt_at.isoformat() if r.next_attempt_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/stats")
async def system_stats() -> dict[str, Any]:
    """Quick system statistics for the dashboard overview panel."""
    from mocksim.persistence.models import (
        Merchant, PosTransaction, PaymentInstruction,
        WebhookOutbox, Account,
    )
    from sqlalchemy import func

    async with async_session_factory() as session:
        def _count(model: Any) -> Any:
            return select(func.count()).select_from(model)

        txn_total = (await session.execute(_count(PosTransaction))).scalar() or 0
        merchant_total = (await session.execute(_count(Merchant))).scalar() or 0
        account_total = (await session.execute(_count(Account))).scalar() or 0
        payment_total = (await session.execute(_count(PaymentInstruction))).scalar() or 0

        pending_wh = (await session.execute(
            _count(WebhookOutbox).where(WebhookOutbox.status.in_(["pending", "retrying"]))
        )).scalar() or 0
        delivered_wh = (await session.execute(
            _count(WebhookOutbox).where(WebhookOutbox.status == "delivered")
        )).scalar() or 0
        dead_wh = (await session.execute(
            _count(WebhookOutbox).where(WebhookOutbox.status == "dead_letter")
        )).scalar() or 0

    return {
        "sim_time": clock.now().isoformat(),
        "merchants": merchant_total,
        "accounts": account_total,
        "pos_transactions": txn_total,
        "payments": payment_total,
        "webhooks": {
            "pending": pending_wh,
            "delivered": delivered_wh,
            "dead_letter": dead_wh,
        },
    }


@router.post("/webhooks/{event_id}/replay")
async def replay_webhook(event_id: str) -> dict[str, Any]:
    """Reset a dead-lettered outbox event to 'pending' for re-delivery."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(WebhookOutbox).where(WebhookOutbox.event_id == event_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            raise not_found(ErrorCode.INTERNAL_ERROR, f"Webhook event {event_id} not found")
        row.status = "pending"
        row.attempt_count = 0
        row.next_attempt_at = None
        row.last_error = None
        await session.commit()

    log.info("admin.webhook.replay", event_id=event_id)
    return {"status": "requeued", "event_id": event_id}


# ── Ledger browse ─────────────────────────────────────────────────

@router.get("/ledger")
async def browse_ledger(
    tenant_id: str | None = None,
    iban: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Browse account entries across tenants (admin only)."""
    from mocksim.persistence.models import AccountEntry
    from sqlalchemy import select

    async with async_session_factory() as session:
        q = select(AccountEntry).order_by(AccountEntry.created_at.desc()).limit(limit)
        if tenant_id:
            q = q.where(AccountEntry.mock_tenant_id == uuid.UUID(tenant_id))
        if iban:
            q = q.where(AccountEntry.account_iban == iban)
        result = await session.execute(q)
        entries = result.scalars().all()

    return {
        "entries": [
            {
                "id": str(e.id),
                "account_iban": e.account_iban,
                "amount": e.amount,
                "credit_debit": e.credit_debit,
                "currency": e.currency,
                "balance_after": e.balance_after,
                "booking_datetime": e.booking_datetime.isoformat(),
                "narration": e.narration,
            }
            for e in entries
        ]
    }


# ── Seed ──────────────────────────────────────────────────────────

@router.post("/seed")
async def bulk_seed(request: Request) -> dict[str, Any]:
    """Bulk-create merchants + historical txns — Phase 1."""
    from mocksim.core.errors import not_implemented_yet
    raise not_implemented_yet("POST /admin/seed")


# ── Cross-system SME onboarding ──────────────────────────────────
# These endpoints write to BOTH trazmo's postgres and MockSim's own in
# a single click. The UI flow is: Operator opens MockSim dashboard →
# fills the "Add SME" form → backend creates the four trazmo rows
# (entity, sme_profile, merchant_profile, acquirer_mapping) AND the
# MockSim merchant row, all with the same acquirer_merchant_id. From
# that moment on, every settlement webhook MockSim emits gets attributed
# to the right entity on the trazmo side.


class OnboardSmeRequest(BaseModel):
    legal_name: str = Field(..., min_length=2, max_length=200)
    owner_name: str = Field(..., min_length=2, max_length=200)
    region: str = Field(..., pattern="^[A-Z]{2}$")
    mcc: str = Field(..., pattern=r"^\d{4}$")
    expected_daily_txns: int = Field(80, ge=1, le=10000)
    avg_ticket_major_units: float = Field(1500.0, gt=0)
    risk_tier: str = Field("standard", pattern="^(low|standard|high)$")
    contact_email: str | None = Field(default=None, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=50)
    # If omitted, server auto-generates the next ACQ-NNNNN slot.
    acquirer_merchant_id: str | None = Field(default=None, max_length=64)
    # Trazmo tenant context. mock_tenant.partner_code is required to be
    # set before this endpoint can be called.
    mock_tenant_id: str
    # Country/timezone — defaults match trazmo's PK seed.
    country_code: str = Field("PK", min_length=2, max_length=2)
    timezone: str = Field("Asia/Karachi", max_length=64)


class OnboardSmeResponse(BaseModel):
    mocksim_merchant_id: str
    acquirer_merchant_id: str
    trazmo_entity_id: str
    trazmo_sme_profile_id: str
    trazmo_merchant_profile_id: str
    trazmo_mapping_id: str
    onboarded: bool


@router.post("/onboard-sme", response_model=OnboardSmeResponse, status_code=201)
async def onboard_sme(body: OnboardSmeRequest) -> OnboardSmeResponse:
    """
    Cross-system SME onboarding — writes to trazmo's postgres AND MockSim's
    own merchants table in the same flow.

    Idempotent on either side via stable `sme_code` (slug of legal_name)
    and the unique constraint on (mock_tenant_id, acquirer_merchant_id).
    """
    from mocksim.config import settings as cfg
    from mocksim.core.identifiers import new_ulid
    from mocksim.pos.regions import get_region
    from mocksim.pos.generator import schedule_initial_generation
    from mocksim.trazmo import client as trazmo_client
    from mocksim.clock import clock

    if not cfg.trazmo_database_url:
        from mocksim.core.errors import MockSimError, ErrorCode
        raise MockSimError(
            503, ErrorCode.INTERNAL_ERROR,
            "Cross-system onboarding requires TRAZMO_DATABASE_URL to be set",
        )

    tenant_id = uuid.UUID(body.mock_tenant_id)

    # 1. Look up MockSim tenant + partner_code.
    async with async_session_factory() as session:
        t = await session.execute(
            select(MockTenant).where(MockTenant.id == tenant_id)
        )
        tenant = t.scalar_one_or_none()
        if tenant is None or not tenant.partner_code:
            from mocksim.core.errors import MockSimError, ErrorCode
            raise MockSimError(
                400, ErrorCode.INTERNAL_ERROR,
                f"MockSim tenant {tenant_id} has no partner_code — required for onboarding",
            )

    # 2. Pre-compute the stable SME code from the legal_name slug.
    slug = "".join(c if c.isalnum() else "_" for c in body.legal_name.upper())[:24].strip("_")
    sme_code = f"MS_{slug}"

    # 3. Connect to trazmo + write all four rows in one transaction.
    conn = await trazmo_client.connect(cfg.trazmo_database_url)
    try:
        async with conn.transaction():
            handles = await trazmo_client.resolve_bootstrap(
                conn,
                partner_code=tenant.partner_code,
                country_code=body.country_code,
                timezone=body.timezone,
            )

            # Allocate acquirer_merchant_id if not supplied. Walk the existing
            # ACQ-NNNNN sequence for this partner so collisions don't happen.
            acquirer_id = body.acquirer_merchant_id
            if not acquirer_id:
                next_n = await conn.fetchval(
                    """
                    SELECT COALESCE(MAX(
                      NULLIF(SUBSTRING(acquirer_merchant_id FROM '^ACQ-([0-9]+)$'), '')::int
                    ), 0) + 1
                      FROM acquirer_merchant_mapping
                     WHERE partner_entity_id = $1
                    """,
                    handles.partner_entity_id,
                )
                acquirer_id = f"ACQ-{int(next_n):05d}"

            onboarded = await trazmo_client.onboard_sme(
                conn,
                handles=handles,
                sme_code=sme_code,
                legal_name=body.legal_name,
                owner_name=body.owner_name,
                mcc=body.mcc,
                acquirer_merchant_id=acquirer_id,
                terminal_ids=[f"{acquirer_id}-T1"],
            )
    finally:
        await conn.close()

    # 4. Create / reuse the MockSim merchant row with matching IDs.
    region_cfg = get_region(body.region)
    decimals = 2 if region_cfg.currency != "BHD" else 3
    avg_minor = int(body.avg_ticket_major_units * (10 ** decimals))
    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        existing = await session.execute(
            select(Merchant).where(
                Merchant.mock_tenant_id == tenant_id,
                Merchant.acquirer_merchant_id == acquirer_id,
            )
        )
        merchant = existing.scalar_one_or_none()
        if merchant is None:
            mid = f"MID_{new_ulid()[:8].upper()}"
            merchant = Merchant(
                id=mid,
                mock_tenant_id=tenant_id,
                trazmo_tenant_id=None,
                acquirer_merchant_id=acquirer_id,
                external_entity_id=str(onboarded.entity_id),
                region=body.region,
                name=body.legal_name,
                mcc=body.mcc,
                expected_daily_txns=body.expected_daily_txns,
                avg_ticket_minor_units=avg_minor,
                currency=region_cfg.currency,
                risk_tier=body.risk_tier,
                status="active",
                created_at=now,
            )
            session.add(merchant)
            await session.flush()
            # Kick off the generator so this merchant produces GMV from today.
            await schedule_initial_generation(session, tenant_id, mid, clock.now().date())
            await session.commit()
            log.info("admin.sme_onboarded",
                     mocksim_merchant_id=mid,
                     trazmo_entity_id=str(onboarded.entity_id),
                     acquirer_merchant_id=acquirer_id)

    return OnboardSmeResponse(
        mocksim_merchant_id=merchant.id,
        acquirer_merchant_id=acquirer_id,
        trazmo_entity_id=str(onboarded.entity_id),
        trazmo_sme_profile_id=str(onboarded.sme_profile_id),
        trazmo_merchant_profile_id=str(onboarded.merchant_profile_id or ""),
        trazmo_mapping_id=str(onboarded.acquirer_mapping_id),
        onboarded=True,
    )


@router.get("/trazmo/lenders")
async def trazmo_lenders() -> dict[str, Any]:
    """List trazmo's lender entities — feeds the MockSim UI lender dropdown."""
    from mocksim.config import settings as cfg
    from mocksim.trazmo import client as trazmo_client
    if not cfg.trazmo_database_url:
        return {"lenders": [], "trazmo_configured": False}
    conn = await trazmo_client.connect(cfg.trazmo_database_url)
    try:
        rows = await trazmo_client.list_lenders(conn)
        return {
            "lenders": [
                {"id": str(r.entity_id), "code": r.code, "legal_name": r.legal_name}
                for r in rows
            ],
            "trazmo_configured": True,
        }
    finally:
        await conn.close()


class GeneratePosRequest(BaseModel):
    merchant_ids: list[str] = Field(..., min_length=1, max_length=100)
    days: int = Field(1, ge=1, le=180)
    # If set, generate retroactively from N days ago; otherwise from today
    # forward. Most demo flows want "give me last 7 days of GMV" → backfill=true.
    backfill: bool = True


@router.post("/generate-pos")
async def generate_pos_for_merchants(body: GeneratePosRequest) -> dict[str, Any]:
    """
    Force-fire the POS generator for a specific set of merchants over N
    consecutive sim-dates. Bypasses the sim clock so the operator can
    populate Flux with realistic GMV without waiting for the clock to tick.

    For each (merchant, date) we call pos.generator.generate_merchant_day
    directly. Returns counts of transactions produced per merchant.
    """
    from datetime import date as _date, timedelta
    from mocksim.clock import clock as _clock
    from mocksim.pos.generator import generate_merchant_day

    today = _clock.now().date()
    if body.backfill:
        start = today - timedelta(days=body.days - 1)
        dates = [start + timedelta(days=i) for i in range(body.days)]
    else:
        dates = [today + timedelta(days=i) for i in range(body.days)]

    results: dict[str, dict[str, Any]] = {}

    async with async_session_factory() as session:
        # Lookup tenant per merchant — admin endpoint, so we accept any tenant.
        merchants_q = await session.execute(
            select(Merchant).where(Merchant.id.in_(body.merchant_ids))
        )
        merchants = list(merchants_q.scalars().all())

    if not merchants:
        return {"results": {}, "total_txns": 0, "warning": "no matching merchants found"}

    total = 0
    for m in merchants:
        per_day: list[int] = []
        for d in dates:
            try:
                count = await generate_merchant_day(
                    mock_tenant_id=m.mock_tenant_id,
                    merchant_id=m.id,
                    sim_date=d,
                )
                per_day.append(count)
                total += count
            except Exception as exc:  # noqa: BLE001
                log.warning("admin.generate_pos.failed",
                            merchant_id=m.id, date=d.isoformat(), error=str(exc))
                per_day.append(0)
        results[m.id] = {
            "name": m.name,
            "acquirer_merchant_id": m.acquirer_merchant_id,
            "txns_per_day": per_day,
            "txns_total": sum(per_day),
        }

    return {
        "results": results,
        "total_txns": total,
        "dates": [d.isoformat() for d in dates],
    }


@router.get("/trazmo/smes")
async def trazmo_smes(partner_code: str) -> dict[str, Any]:
    """List trazmo's SME entities for one partner — drives the existing-SMEs panel."""
    from mocksim.config import settings as cfg
    from mocksim.trazmo import client as trazmo_client
    if not cfg.trazmo_database_url:
        return {"smes": [], "trazmo_configured": False}
    conn = await trazmo_client.connect(cfg.trazmo_database_url)
    try:
        rows = await trazmo_client.list_sme_entities(conn, partner_code=partner_code)
        return {
            "smes": [
                {
                    "id": str(r.entity_id),
                    "code": r.code,
                    "legal_name": r.legal_name,
                    "acquirer_merchant_id": r.acquirer_merchant_id,
                    "mcc": r.mcc,
                    "status": r.status,
                }
                for r in rows
            ],
            "trazmo_configured": True,
        }
    finally:
        await conn.close()


# ── Recon ─────────────────────────────────────────────────────────

@router.get("/recon/run")
async def run_recon(date: str | None = None) -> dict[str, Any]:
    """Reconciliation report — Phase 2."""
    from mocksim.core.errors import not_implemented_yet
    raise not_implemented_yet("GET /admin/recon/run")
