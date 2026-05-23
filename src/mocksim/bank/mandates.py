"""
Bank mandate lifecycle — pain.009 creation + pain.008 direct debit collection
(DESIGN.md §5.3).

Mandate creation (POST /bank/mandates):
  Validates debtor + creditor accounts, persists Mandate with status=active.

Mandate collection (POST /bank/mandates/{mandate_id}/collect):
  Validates mandate is active, creates a PaymentInstruction on the region's
  DD rail, and schedules bank.process_payment (reuses the payment pipeline).

Bounce semantics:
  All bank.payment.rejected webhooks carry a bounce_reason field from
  bank/regions.py#normalize_bounce_reason.

Public entry points
-------------------
  create_mandate_handler(session, body, tenant_id, trazmo_tenant_id) -> Mandate
  collect_mandate_handler(session, mandate_id, body, tenant_id) -> PaymentInstruction
  get_mandate_handler(session, mandate_id, tenant_id) -> Mandate
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mocksim.bank.regions import get_region
from mocksim.core.errors import ErrorCode, MockSimError, not_found, bad_request
from mocksim.core.identifiers import gen_mandate_id, new_ulid
from mocksim.core.money import Money, CURRENCY_DECIMALS
from mocksim.persistence.models import Account, Mandate, PaymentInstruction


# ── Request / Response schemas ────────────────────────────────────

class CreateMandateRequest(BaseModel):
    debtor_iban: str
    creditor_iban: str
    debtor_name: str
    max_amount: str | None = None   # minor-units string; None = unlimited
    currency: str
    region: str = Field(..., pattern="^[A-Z]{2}$")
    expires_at: datetime | None = None


class MandateResponse(BaseModel):
    id: str
    debtor_iban: str
    creditor_iban: str
    debtor_name: str
    max_amount: str | None
    currency: str
    region: str
    status: str
    created_at: datetime
    expires_at: datetime | None


class CollectMandateRequest(BaseModel):
    end_to_end_id: str = Field(..., max_length=35)
    instruction_id: str
    message_id: str
    amount: Money
    remittance_info: str | None = None


class CollectMandateResponse(BaseModel):
    end_to_end_id: str
    mandate_id: str
    status: str
    message: str
    created_at: datetime


# ── Handlers ──────────────────────────────────────────────────────

async def create_mandate_handler(
    session: AsyncSession,
    body: CreateMandateRequest,
    mock_tenant_id: uuid.UUID,
    trazmo_tenant_id: str | None,
) -> MandateResponse:
    """
    Validate accounts and persist a new Mandate.
    """
    # Validate region
    try:
        region_cfg = get_region(body.region)
    except ValueError as exc:
        raise bad_request(ErrorCode.VALIDATION_ERROR, str(exc))

    # Validate currency matches region
    if body.currency.upper() != region_cfg.currency:
        raise bad_request(
            ErrorCode.CURRENCY_MISMATCH,
            f"Currency {body.currency} does not match region {body.region} "
            f"(expected {region_cfg.currency})",
        )

    if body.currency not in CURRENCY_DECIMALS:
        raise bad_request(ErrorCode.CURRENCY_MISMATCH, f"Unsupported currency: {body.currency}")

    # Validate debtor account exists and is active
    res = await session.execute(select(Account).where(Account.iban == body.debtor_iban))
    debtor = res.scalar_one_or_none()
    if debtor is None:
        raise not_found(ErrorCode.ACCOUNT_NOT_FOUND, f"Debtor account not found: {body.debtor_iban}")
    if debtor.status == "closed":
        raise MockSimError(422, ErrorCode.ACCOUNT_CLOSED, "Debtor account is closed")
    if debtor.status == "dormant":
        raise MockSimError(422, ErrorCode.ACCOUNT_DORMANT, "Debtor account is dormant")

    # Validate creditor account exists
    res = await session.execute(select(Account).where(Account.iban == body.creditor_iban))
    creditor = res.scalar_one_or_none()
    if creditor is None:
        raise not_found(ErrorCode.ACCOUNT_NOT_FOUND, f"Creditor account not found: {body.creditor_iban}")

    # Validate max_amount if provided
    max_amount_int: int | None = None
    if body.max_amount is not None:
        if not body.max_amount.lstrip("-").isdigit():
            raise bad_request(ErrorCode.VALIDATION_ERROR, "max_amount must be a minor-units integer string")
        max_amount_int = int(body.max_amount)
        if max_amount_int <= 0:
            raise bad_request(ErrorCode.VALIDATION_ERROR, "max_amount must be positive")

    now = datetime.now(timezone.utc)
    mandate_id = gen_mandate_id(body.region)

    mandate = Mandate(
        id=mandate_id,
        mock_tenant_id=mock_tenant_id,
        debtor_iban=body.debtor_iban,
        creditor_iban=body.creditor_iban,
        debtor_name=body.debtor_name,
        max_amount=max_amount_int,
        currency=body.currency.upper(),
        region=body.region.upper(),
        status="active",
        scenario_name=None,
        created_at=now,
        expires_at=body.expires_at,
    )
    session.add(mandate)

    return MandateResponse(
        id=mandate_id,
        debtor_iban=body.debtor_iban,
        creditor_iban=body.creditor_iban,
        debtor_name=body.debtor_name,
        max_amount=body.max_amount,
        currency=body.currency.upper(),
        region=body.region.upper(),
        status="active",
        created_at=now,
        expires_at=body.expires_at,
    )


async def collect_mandate_handler(
    session: AsyncSession,
    mandate_id: str,
    body: CollectMandateRequest,
    mock_tenant_id: uuid.UUID,
) -> CollectMandateResponse:
    """
    Initiate a direct debit collection against a mandate.

    Validates:
    - Mandate exists and is active (not expired / cancelled)
    - Collection amount ≤ mandate max_amount (if set)
    - Currencies match

    Then creates a PaymentInstruction on the DD rail and schedules processing.
    """
    # Load mandate
    res = await session.execute(
        select(Mandate).where(
            Mandate.id == mandate_id,
            Mandate.mock_tenant_id == mock_tenant_id,
        )
    )
    mandate = res.scalar_one_or_none()
    if mandate is None:
        raise not_found(ErrorCode.MANDATE_NOT_FOUND, f"Mandate {mandate_id} not found")

    if mandate.status != "active":
        raise MockSimError(
            422, ErrorCode.MANDATE_NOT_ACTIVE,
            f"Mandate {mandate_id} is {mandate.status}",
        )

    # Check expiry
    now = datetime.now(timezone.utc)
    if mandate.expires_at and mandate.expires_at < now:
        mandate.status = "expired"
        raise MockSimError(422, ErrorCode.MANDATE_EXPIRED, f"Mandate {mandate_id} has expired")

    # Currency check
    if body.amount.currency.upper() != mandate.currency:
        raise bad_request(
            ErrorCode.CURRENCY_MISMATCH,
            f"Collection currency {body.amount.currency} != mandate currency {mandate.currency}",
        )

    # Amount check
    if mandate.max_amount is not None and body.amount.minor_units > mandate.max_amount:
        raise MockSimError(
            422, ErrorCode.AMOUNT_EXCEEDS_LIMIT,
            f"Collection amount {body.amount.minor_units} exceeds mandate limit {mandate.max_amount}",
        )

    # Region → DD rail
    region_cfg = get_region(mandate.region)
    dd_rail = region_cfg.dd_scheme

    # Create PaymentInstruction (DD collection)
    instruction = PaymentInstruction(
        id=body.end_to_end_id,
        mock_tenant_id=mock_tenant_id,
        message_id=body.message_id,
        instruction_id=body.instruction_id,
        debtor_iban=mandate.debtor_iban,
        creditor_iban=mandate.creditor_iban,
        creditor_name=mandate.debtor_name,   # debtor name as counterparty label
        amount=body.amount.minor_units,
        currency=body.amount.currency.upper(),
        rail=dd_rail,
        sharia_compliant=False,
        remittance_info=body.remittance_info,
        status="accepted",
        created_at=now,
    )
    session.add(instruction)
    await session.flush()

    # Schedule payment processing (reuses the same pipeline as pain.001)
    from mocksim.bank.payments import schedule_payment_processing
    await schedule_payment_processing(session, mock_tenant_id, instruction, region_cfg)

    return CollectMandateResponse(
        end_to_end_id=body.end_to_end_id,
        mandate_id=mandate_id,
        status="accepted",
        message=f"Direct debit collection accepted on {dd_rail}",
        created_at=now,
    )


async def get_mandate_handler(
    session: AsyncSession,
    mandate_id: str,
    mock_tenant_id: uuid.UUID,
) -> MandateResponse:
    """Return a mandate by ID."""
    res = await session.execute(
        select(Mandate).where(
            Mandate.id == mandate_id,
            Mandate.mock_tenant_id == mock_tenant_id,
        )
    )
    mandate = res.scalar_one_or_none()
    if mandate is None:
        raise not_found(ErrorCode.MANDATE_NOT_FOUND, f"Mandate {mandate_id} not found")

    return MandateResponse(
        id=mandate.id,
        debtor_iban=mandate.debtor_iban,
        creditor_iban=mandate.creditor_iban,
        debtor_name=mandate.debtor_name,
        max_amount=str(mandate.max_amount) if mandate.max_amount is not None else None,
        currency=mandate.currency,
        region=mandate.region,
        status=mandate.status,
        created_at=mandate.created_at,
        expires_at=mandate.expires_at,
    )
