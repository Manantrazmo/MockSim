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


# ── Recon ─────────────────────────────────────────────────────────

@router.get("/recon/run")
async def run_recon(date: str | None = None) -> dict[str, Any]:
    """Reconciliation report — Phase 2."""
    from mocksim.core.errors import not_implemented_yet
    raise not_implemented_yet("GET /admin/recon/run")
