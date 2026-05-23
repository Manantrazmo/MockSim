"""
Bank payment lifecycle processor (DESIGN.md §5.2).

After pain.001 initiation (status=accepted), a SimScheduler job fires to process
the payment:
  - Verify debtor account is active and has sufficient funds
  - Debit debtor, credit creditor (if creditor IBAN is MockSim-owned)
  - Update PaymentInstruction status → settled | rejected
  - Enqueue bank.payment.settled or bank.payment.rejected webhook with pacs.002 payload

Rail timing (sim-time delay from initiation to settlement):
  Instant  (RAAST, Aani, IPS, BENEFIT_Pay, InstaPay, FAWRI+): +30 s
  RTGS     (PRISM, Sarie, UAEFTS, RTGS_CBE, EFTS)            : next biz-day 15:00 UTC
  ACH/other (1LINK, NIFT, SADAD_DD, etc.)                     : next biz-day 09:00 UTC

Public entry points
-------------------
  process_payment(mock_tenant_id, end_to_end_id) -> dict
      Execute the payment lifecycle. Called by SimScheduler job handler.

  schedule_payment_processing(session, mock_tenant_id, instruction, region_cfg)
      Persist the bank.process_payment job. Call inside initiate_payment transaction.

  register_handlers()
      Register SimScheduler handlers. Called from main.py lifespan.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mocksim.bank.regions import BankRegionConfig, get_region
from mocksim.clock import clock
from mocksim.core import outbox as outbox_module
from mocksim.core.accounts import get_account, write_entry
from mocksim.core.errors import ErrorCode, MockSimError
from mocksim.core.identifiers import new_ulid
from mocksim.core.money import Money
from mocksim.persistence.database import async_session_factory
from mocksim.persistence.models import (
    Account,
    PaymentInstruction,
    WebhookSubscription,
)

log = structlog.get_logger(__name__)

# Rail sets for timing classification
_INSTANT_RAILS: frozenset[str] = frozenset({
    "RAAST", "Aani", "IPS", "BENEFIT_Pay", "BENEFIT", "InstaPay", "FAWRI+",
})
_RTGS_RAILS: frozenset[str] = frozenset({
    "PRISM", "Sarie", "UAEFTS", "RTGS_CBE", "EFTS",
})


# ── Public entry points ───────────────────────────────────────────

async def process_payment(
    mock_tenant_id: uuid.UUID,
    end_to_end_id: str,
) -> dict[str, Any]:
    """
    Execute the full payment lifecycle for a single PaymentInstruction.

    1. Load instruction (guard: skip if already terminal).
    2. Load debtor account; check active + sufficient funds.
    3. On failure → RJCT, enqueue bank.payment.rejected.
    4. On success → debit debtor, credit creditor (if known), ACSC,
       enqueue bank.payment.settled + camt.054 for creditor.
    """
    async with async_session_factory() as session:
        instr = await _load_instruction(session, mock_tenant_id, end_to_end_id)
        if instr is None:
            log.warning("payment.not_found", e2e_id=end_to_end_id)
            return {"status": "not_found"}

        if instr.status in ("settled", "rejected"):
            log.debug("payment.already_terminal", e2e_id=end_to_end_id, status=instr.status)
            return {"status": instr.status}

        subs = await _load_subscriptions(session, mock_tenant_id)
        now = datetime.now(timezone.utc)

        # ── Validate debtor account ───────────────────────────────
        reject_reason: str | None = await _check_debtor(session, instr)

        if reject_reason:
            # ── Reject ───────────────────────────────────────────
            instr.status = "rejected"
            instr.reject_reason = reject_reason

            payload = _build_pacs002_payload(instr, "RJCT", reject_reason, now)
            for sub in subs:
                if _should_deliver(sub, "bank.payment.rejected"):
                    outbox_module.enqueue(
                        session, mock_tenant_id, instr.debtor_iban,
                        "bank.payment.rejected", payload,
                        sub.target_url, sub.target_secret,
                    )

            await session.commit()
            log.info(
                "payment.rejected",
                e2e_id=end_to_end_id,
                reason=reject_reason,
            )
            return {"status": "rejected", "reason": reject_reason}

        # ── Settle ────────────────────────────────────────────────
        amount = Money.from_minor(instr.amount, instr.currency)
        narration = f"Payment {instr.id} via {instr.rail}"
        ref_codes = {
            "end_to_end_id": instr.id,
            "message_id": instr.message_id,
            "instruction_id": instr.instruction_id,
            "rail": instr.rail,
        }

        # Debit debtor
        debtor_acct = await get_account(session, instr.debtor_iban)
        await write_entry(
            session, debtor_acct, amount, "DBIT", narration,
            counterparty_name=instr.creditor_name,
            counterparty_iban=instr.creditor_iban,
            ref_codes=ref_codes,
        )

        # Credit creditor (only if creditor IBAN exists in MockSim)
        creditor_acct: Account | None = await _find_creditor(session, instr.creditor_iban)
        if creditor_acct is not None:
            await write_entry(
                session, creditor_acct, amount, "CRDT", narration,
                counterparty_iban=instr.debtor_iban,
                ref_codes=ref_codes,
            )

        instr.status = "settled"
        instr.settled_at = now

        # pacs.002 ACSC webhook
        payload = _build_pacs002_payload(instr, "ACSC", None, now)
        for sub in subs:
            if _should_deliver(sub, "bank.payment.settled"):
                outbox_module.enqueue(
                    session, mock_tenant_id, instr.debtor_iban,
                    "bank.payment.settled", payload,
                    sub.target_url, sub.target_secret,
                )

        # camt.054 credit notification for creditor (if MockSim-owned)
        if creditor_acct is not None:
            camt054 = _build_camt054_payload(instr, amount, now)
            for sub in subs:
                if _should_deliver(sub, "bank.camt054.credit"):
                    outbox_module.enqueue(
                        session, mock_tenant_id, instr.creditor_iban,
                        "bank.camt054.credit", camt054,
                        sub.target_url, sub.target_secret,
                    )

        await session.commit()

    log.info(
        "payment.settled",
        e2e_id=end_to_end_id,
        amount=instr.amount,
        currency=instr.currency,
        rail=instr.rail,
    )
    return {"status": "settled"}


async def schedule_payment_processing(
    session: AsyncSession,
    mock_tenant_id: uuid.UUID,
    instruction: PaymentInstruction,
    region_cfg: BankRegionConfig,
) -> None:
    """
    Persist a bank.process_payment SimScheduler job.
    Call inside the same transaction as PaymentInstruction.insert().
    """
    from mocksim.core.sim_scheduler import schedule_at

    fire_at = _compute_fire_at(instruction.rail, region_cfg)
    await schedule_at(
        session,
        mock_tenant_id,
        fire_at,
        "bank.process_payment",
        {
            "end_to_end_id": instruction.id,
            "mock_tenant_id": str(mock_tenant_id),
        },
    )


# ── SimScheduler integration ──────────────────────────────────────

async def _job_handler(fire_at: datetime, payload: dict[str, Any]) -> None:
    """SimScheduler callback for "bank.process_payment"."""
    e2e_id: str = payload["end_to_end_id"]
    tenant_id: uuid.UUID = uuid.UUID(payload["mock_tenant_id"])
    await process_payment(mock_tenant_id=tenant_id, end_to_end_id=e2e_id)


def register_handlers() -> None:
    """Register bank SimScheduler handlers. Called from main.py lifespan."""
    from mocksim.core.sim_scheduler import register_job_type

    register_job_type("bank.process_payment", _job_handler)
    log.debug("bank.payments.handlers_registered")


# ── Timing helpers ────────────────────────────────────────────────

def _compute_fire_at(rail: str, region_cfg: BankRegionConfig) -> datetime:
    """
    Determine when to fire the processing job based on rail type.
    Relative to current sim-clock time.
    """
    now = clock.now()

    if rail in _INSTANT_RAILS:
        return now + timedelta(seconds=30)

    # RTGS or ACH: fire at next business day morning/afternoon
    target_hour = region_cfg.settlement_cutoff_hour if rail in _RTGS_RAILS else 9
    next_biz = _next_business_day(now.date(), region_cfg.weekend_days)
    return datetime(
        next_biz.year, next_biz.month, next_biz.day,
        target_hour, 0, 0,
        tzinfo=timezone.utc,
    )


def _next_business_day(d: date, weekend_days: set[int]) -> date:
    result = d + timedelta(days=1)
    while result.isoweekday() in weekend_days:
        result += timedelta(days=1)
    return result


# ── Validation ────────────────────────────────────────────────────

async def _check_debtor(
    session: AsyncSession,
    instr: PaymentInstruction,
) -> str | None:
    """
    Validate debtor account for payment processing.
    Returns a reject_reason string on failure, or None on success.
    """
    result = await session.execute(
        select(Account).where(Account.iban == instr.debtor_iban)
    )
    debtor = result.scalar_one_or_none()

    if debtor is None:
        return "ACCOUNT_NOT_FOUND"
    if debtor.status == "closed":
        return "ACCOUNT_CLOSED"
    if debtor.status == "dormant":
        return "ACCOUNT_DORMANT"
    if debtor.balance < instr.amount:
        return "INSUFFICIENT_FUNDS"
    return None


async def _find_creditor(
    session: AsyncSession,
    creditor_iban: str,
) -> Account | None:
    """Return Account if creditor IBAN is MockSim-owned, else None."""
    result = await session.execute(
        select(Account).where(Account.iban == creditor_iban)
    )
    return result.scalar_one_or_none()


# ── DB helpers ────────────────────────────────────────────────────

async def _load_instruction(
    session: AsyncSession,
    mock_tenant_id: uuid.UUID,
    end_to_end_id: str,
) -> PaymentInstruction | None:
    result = await session.execute(
        select(PaymentInstruction)
        .where(
            PaymentInstruction.id == end_to_end_id,
            PaymentInstruction.mock_tenant_id == mock_tenant_id,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _load_subscriptions(
    session: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[WebhookSubscription]:
    result = await session.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.mock_tenant_id == tenant_id,
            WebhookSubscription.surface == "bank",
            WebhookSubscription.status == "active",
        )
    )
    return list(result.scalars().all())


def _should_deliver(sub: WebhookSubscription, event_type: str) -> bool:
    return not sub.event_types or event_type in sub.event_types


# ── Payload builders ──────────────────────────────────────────────

def _build_pacs002_payload(
    instr: PaymentInstruction,
    tx_status: str,            # "ACSC" | "RJCT"
    reject_reason: str | None,
    now: datetime,
) -> dict[str, Any]:
    """
    ISO 20022 pacs.002 status report (canonical JSON).
    Trazmo receives this as bank.payment.settled / bank.payment.rejected payload.
    """
    return {
        "message_id": new_ulid(),
        "creation_datetime": now.isoformat(),
        "original_message_id": instr.message_id,
        "original_instruction_id": instr.instruction_id,
        "end_to_end_id": instr.id,
        "tx_status": tx_status,
        "reject_reason": reject_reason,
        "amount": str(instr.amount),
        "currency": instr.currency,
        "debtor_iban": instr.debtor_iban,
        "creditor_iban": instr.creditor_iban,
        "creditor_name": instr.creditor_name,
        "rail": instr.rail,
        "settled_at": now.isoformat() if tx_status == "ACSC" else None,
        "remittance_info": instr.remittance_info,
    }


def _build_camt054_payload(
    instr: PaymentInstruction,
    amount: Money,
    now: datetime,
) -> dict[str, Any]:
    """
    ISO 20022 camt.054 credit notification for the creditor account.
    """
    return {
        "notification_id": new_ulid(),
        "creation_datetime": now.isoformat(),
        "account_iban": instr.creditor_iban,
        "virtual_account": None,
        "entry": {
            "amount": str(amount.minor_units),
            "currency": amount.currency,
            "credit_debit": "CRDT",
            "booking_datetime": now.isoformat(),
            "value_date": now.date().isoformat(),
            "rail": instr.rail,
            "narration": f"Payment {instr.id} via {instr.rail}",
            "counterparty_name": None,
            "ref_codes": {
                "end_to_end_id": instr.id,
                "message_id": instr.message_id,
            },
        },
    }
