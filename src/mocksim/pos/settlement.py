"""
POS Settlement Engine (DESIGN.md §4.3).

On each sim-day advance, when the clock passes midnight of a settlement_date:
  1. Query all pending POS transactions for the merchant with that expected_settlement_date.
  2. Create a SettlementBatch row (BATCH_YYYYMMDD_NNN).
  3. Mark each txn settlement_status → 'settled', settlement_batch_id → batch.id.
  4. Enqueue pos.batch.settled webhook (Camt054-like payload) in the same transaction.

SimScheduler job type: "pos.settle_batch"
  Payload: {merchant_id, settlement_date, mock_tenant_id}

Public entry points
-------------------
  settle_merchant_day(mock_tenant_id, merchant_id, settlement_date) -> SettlementBatch | None

  schedule_settlement(session, mock_tenant_id, merchant_id, settlement_date)
      Persist the job; call inside generate_merchant_day's transaction.

  register_handlers()
      Register "pos.settle_batch" with SimScheduler. Called from main.py.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mocksim.core import outbox as outbox_module
from mocksim.core.identifiers import gen_batch_id, new_ulid
from mocksim.persistence.database import async_session_factory
from mocksim.persistence.models import (
    Merchant,
    PosTransaction,
    SettlementBatch,
    WebhookSubscription,
)

log = structlog.get_logger(__name__)


# ── Settlement entry point ────────────────────────────────────────

async def settle_merchant_day(
    mock_tenant_id: uuid.UUID,
    merchant_id: str,
    settlement_date: date,
) -> SettlementBatch | None:
    """
    Process settlement for one merchant on one settlement_date.

    Finds all pending txns with expected_settlement_date == settlement_date,
    creates a SettlementBatch, marks txns settled, fires pos.batch.settled webhook.

    Returns the SettlementBatch, or None if there were no pending txns.
    """
    # ── Load pending txns ─────────────────────────────────────────
    async with async_session_factory() as session:
        result = await session.execute(
            select(PosTransaction).where(
                PosTransaction.mock_tenant_id == mock_tenant_id,
                PosTransaction.merchant_id == merchant_id,
                PosTransaction.expected_settlement_date == settlement_date,
                PosTransaction.settlement_status == "pending",
            ).with_for_update(skip_locked=True)
        )
        txns = list(result.scalars().all())

        if not txns:
            log.debug(
                "settlement.no_pending_txns",
                merchant_id=merchant_id,
                settlement_date=settlement_date.isoformat(),
            )
            return None

        # ── Load merchant & subscriptions ─────────────────────────
        m_result = await session.execute(
            select(Merchant).where(
                Merchant.id == merchant_id,
                Merchant.mock_tenant_id == mock_tenant_id,
            )
        )
        merchant = m_result.scalar_one_or_none()
        if merchant is None:
            log.warning("settlement.merchant_not_found", merchant_id=merchant_id)
            return None

        subs = await _load_subscriptions(session, mock_tenant_id)

        # ── Aggregate totals ──────────────────────────────────────
        txn_count = len(txns)
        gross_amount = sum(t.amount for t in txns)
        total_mdr = sum(t.mdr for t in txns)
        total_vat = sum(t.vat_on_mdr for t in txns)
        net_amount = sum(t.net_settlement for t in txns)
        currency = txns[0].currency  # all txns share the merchant's currency

        # ── Create SettlementBatch ────────────────────────────────
        now = datetime.now(timezone.utc)
        batch_id = _unique_batch_id(settlement_date)
        batch = SettlementBatch(
            id=batch_id,
            mock_tenant_id=mock_tenant_id,
            merchant_id=merchant_id,
            region=merchant.region,
            settlement_date=settlement_date,
            txn_count=txn_count,
            gross_amount=gross_amount,
            total_mdr=total_mdr,
            total_vat=total_vat,
            net_amount=net_amount,
            currency=currency,
            status="settled",
            created_at=now,
        )
        session.add(batch)

        # ── Mark txns settled ─────────────────────────────────────
        txn_ids = [t.id for t in txns]
        await session.execute(
            update(PosTransaction)
            .where(PosTransaction.id.in_(txn_ids))
            .values(
                settlement_status="settled",
                settlement_batch_id=batch_id,
            )
        )

        # ── Enqueue pos.batch.settled webhook ─────────────────────
        payload = _build_settlement_payload(batch, merchant)
        for sub in subs:
            if _should_deliver(sub, "pos.batch.settled"):
                outbox_module.enqueue(
                    session,
                    mock_tenant_id,
                    merchant_id,            # partition_key
                    "pos.batch.settled",
                    payload,
                    sub.target_url,
                    sub.target_secret,
                )

        await session.commit()

    log.info(
        "settlement.batch_created",
        batch_id=batch_id,
        merchant_id=merchant_id,
        settlement_date=settlement_date.isoformat(),
        txn_count=txn_count,
        gross=gross_amount,
        net=net_amount,
        currency=currency,
    )
    return batch


# ── Scheduling ────────────────────────────────────────────────────

async def schedule_settlement(
    session: AsyncSession,
    mock_tenant_id: uuid.UUID,
    merchant_id: str,
    settlement_date: date,
) -> None:
    """
    Persist a pos.settle_batch SimScheduler job.
    Call this inside the same transaction as the generating batch
    (e.g., at the end of generate_merchant_day).
    """
    from datetime import timedelta
    from mocksim.core.sim_scheduler import schedule_at

    # Fire at midnight UTC of settlement_date so it runs during the clock
    # advance that first passes that date boundary.
    fire_at = datetime(
        settlement_date.year,
        settlement_date.month,
        settlement_date.day,
        0, 0, 0,
        tzinfo=timezone.utc,
    )
    await schedule_at(
        session,
        mock_tenant_id,
        fire_at,
        "pos.settle_batch",
        {
            "merchant_id": merchant_id,
            "settlement_date": settlement_date.isoformat(),
            "mock_tenant_id": str(mock_tenant_id),
        },
    )


# ── SimScheduler integration ──────────────────────────────────────

async def _job_handler(fire_at: datetime, payload: dict[str, Any]) -> None:
    """
    SimScheduler callback for "pos.settle_batch" job type.

    Payload keys
    ------------
    merchant_id     : str
    settlement_date : ISO date string (YYYY-MM-DD)
    mock_tenant_id  : UUID string
    """
    merchant_id: str = payload["merchant_id"]
    settlement_date: date = date.fromisoformat(payload["settlement_date"])
    tenant_id: uuid.UUID = uuid.UUID(payload["mock_tenant_id"])

    await settle_merchant_day(
        mock_tenant_id=tenant_id,
        merchant_id=merchant_id,
        settlement_date=settlement_date,
    )


def register_handlers() -> None:
    """
    Register all settlement SimScheduler job-type handlers.
    Called from main.py lifespan.
    """
    from mocksim.core.sim_scheduler import register_job_type

    register_job_type("pos.settle_batch", _job_handler)
    log.debug("pos.settlement.handlers_registered")


# ── Private helpers ───────────────────────────────────────────────

async def _load_subscriptions(
    session: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[WebhookSubscription]:
    result = await session.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.mock_tenant_id == tenant_id,
            WebhookSubscription.surface == "pos",
            WebhookSubscription.status == "active",
        )
    )
    return list(result.scalars().all())


def _should_deliver(sub: WebhookSubscription, event_type: str) -> bool:
    return not sub.event_types or event_type in sub.event_types


def _unique_batch_id(settlement_date: date) -> str:
    """
    Generate a settlement batch ID: BATCH_YYYYMMDD_<random-6-hex>.
    The random suffix prevents collisions when multiple merchants settle on
    the same date (each gets a distinct batch ID).
    """
    suffix = uuid.uuid4().hex[:6].upper()
    return f"BATCH_{settlement_date.strftime('%Y%m%d')}_{suffix}"


def _build_settlement_payload(
    batch: SettlementBatch,
    merchant: Merchant,
) -> dict[str, Any]:
    """
    Build the pos.batch.settled webhook payload.

    Structured as a Camt054-style notification: the key fields Trazmo needs
    to credit the merchant's wallet are gross_amount, net_amount, and fees.
    """
    return {
        "notification_id": new_ulid(),
        "batch_id": batch.id,
        "merchant_id": batch.merchant_id,
        "merchant_name": merchant.name,
        "region": batch.region,
        "settlement_date": batch.settlement_date.isoformat(),
        "txn_count": batch.txn_count,
        "gross_amount": str(batch.gross_amount),
        "total_mdr": str(batch.total_mdr),
        "total_vat_on_mdr": str(batch.total_vat),
        "net_amount": str(batch.net_amount),
        "currency": batch.currency,
        "status": "settled",
        "created_at": batch.created_at.isoformat(),
        # camt.054-style entry for the net credit
        "entry": {
            "amount": str(batch.net_amount),
            "credit_debit": "CRDT",
            "booking_datetime": batch.created_at.isoformat(),
            "value_date": batch.settlement_date.isoformat(),
            "narration": f"POS settlement batch {batch.id}",
            "ref_codes": {
                "batch_id": batch.id,
                "merchant_id": batch.merchant_id,
            },
        },
    }
