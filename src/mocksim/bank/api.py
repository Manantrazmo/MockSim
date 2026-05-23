"""
Bank Mock API (DESIGN.md §5).

Phase 0: Account CRUD + webhook subscription + payment initiation skeleton.
         Full payment lifecycle, mandates, instant transfers wired in Phase 1.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mocksim.bank.mandates import (
    CreateMandateRequest,
    MandateResponse,
    CollectMandateRequest,
    CollectMandateResponse,
    create_mandate_handler,
    collect_mandate_handler,
    get_mandate_handler,
)
from mocksim.bank.payments import schedule_payment_processing, _INSTANT_RAILS
from mocksim.bank.regions import get_region
from mocksim.core.errors import ErrorCode, MockSimError, not_found, not_implemented_yet, bad_request
from mocksim.core.idempotency import get_or_start, complete, require_idempotency_key
from mocksim.core.identifiers import gen_iban, gen_bic, new_ulid
from mocksim.core.money import Money
from mocksim.core.webhook import _validate_target_url
from mocksim.persistence.database import get_session, current_mock_tenant_id
from mocksim.persistence.models import Account, PaymentInstruction, WebhookSubscription

router = APIRouter(prefix="/bank", tags=["Bank"])


# ── Request / Response schemas ────────────────────────────────────

class CreateAccountRequest(BaseModel):
    account_type: str = Field(..., pattern="^(pool|merchant_van|external)$")
    owner_name: str
    region: str = Field(..., pattern="^[A-Z]{2}$")
    currency: str = Field(..., min_length=3, max_length=3)
    sharia_flag: bool = False
    parent_iban: str | None = None  # required for merchant_van
    seed: str | None = None         # optional seed for deterministic IBAN


class AccountResponse(BaseModel):
    id: str
    iban: str
    bic: str
    account_type: str
    owner_name: str
    region: str
    currency: str
    balance: str            # minor-units string
    available_balance: str
    status: str
    sharia_flag: bool
    parent_iban: str | None
    created_at: datetime


class PaymentInitiateRequest(BaseModel):
    """pain.001 wrapper — single payment instruction for simplicity."""
    message_id: str
    end_to_end_id: str = Field(..., max_length=35)
    instruction_id: str
    amount: Money
    debtor_iban: str
    creditor_iban: str
    creditor_name: str
    rail: str
    sharia_compliant: bool = False
    remittance_info: str | None = None


class PaymentInitiateResponse(BaseModel):
    end_to_end_id: str
    status: str   # pending | accepted | rejected
    message: str
    created_at: datetime


class WebhookSubscriptionRequest(BaseModel):
    url: str
    secret: str = Field(..., min_length=16)
    event_types: list[str] = Field(default_factory=list)
    format: str = Field(default="per_event", pattern="^(per_event|trazmo_settlement)$")


class WebhookSubscriptionResponse(BaseModel):
    id: str
    surface: str
    target_url: str
    event_types: list[str]
    format: str
    status: str
    created_at: datetime


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/accounts", status_code=201, response_model=AccountResponse)
async def create_account(
    body: CreateAccountRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str = Depends(require_idempotency_key),
) -> AccountResponse:
    tenant_id = current_mock_tenant_id.get()
    assert tenant_id is not None

    cached = await get_or_start(session, tenant_id, idempotency_key, body.model_dump_json().encode(), "non_money")
    if cached:
        return AccountResponse(**cached.response_body)  # type: ignore[arg-type]

    region_cfg = get_region(body.region)

    if body.account_type == "merchant_van" and not body.parent_iban:
        raise bad_request(ErrorCode.INVALID_IBAN, "parent_iban is required for merchant_van accounts")

    iban = gen_iban(body.region, seed=body.seed)
    bic = gen_bic(body.region)
    now = datetime.now(timezone.utc)

    account = Account(
        id=uuid.uuid4(),
        mock_tenant_id=tenant_id,
        trazmo_tenant_id=getattr(request.state, "trazmo_tenant_id", None),
        iban=iban,
        bic=bic,
        account_type=body.account_type,
        currency=body.currency.upper(),
        balance=0,
        available_balance=0,
        parent_iban=body.parent_iban,
        status="active",
        sharia_flag=body.sharia_flag,
        owner_name=body.owner_name,
        region=body.region.upper(),
        created_at=now,
        updated_at=now,
    )
    session.add(account)
    await session.flush()

    resp = AccountResponse(
        id=str(account.id),
        iban=iban,
        bic=bic,
        account_type=body.account_type,
        owner_name=body.owner_name,
        region=body.region.upper(),
        currency=body.currency.upper(),
        balance="0",
        available_balance="0",
        status="active",
        sharia_flag=body.sharia_flag,
        parent_iban=body.parent_iban,
        created_at=now,
    )
    await complete(session, tenant_id, idempotency_key, 201, resp.model_dump(mode="json"))
    return resp


@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts(
    session: AsyncSession = Depends(get_session),
) -> list[AccountResponse]:
    """List all accounts for the current tenant."""
    tenant_id = current_mock_tenant_id.get()
    result = await session.execute(
        select(Account)
        .where(Account.mock_tenant_id == tenant_id)
        .order_by(Account.created_at.desc())
    )
    accounts = result.scalars().all()
    return [
        AccountResponse(
            id=str(a.id), iban=a.iban, bic=a.bic, account_type=a.account_type,
            owner_name=a.owner_name, region=a.region, currency=a.currency,
            balance=str(a.balance), available_balance=str(a.available_balance),
            status=a.status, sharia_flag=a.sharia_flag,
            parent_iban=a.parent_iban, created_at=a.created_at,
        )
        for a in accounts
    ]


@router.get("/accounts/{iban}", response_model=AccountResponse)
async def get_account(
    iban: str,
    session: AsyncSession = Depends(get_session),
) -> AccountResponse:
    result = await session.execute(select(Account).where(Account.iban == iban))
    account = result.scalar_one_or_none()
    if not account:
        raise not_found(ErrorCode.ACCOUNT_NOT_FOUND, f"Account {iban} not found")
    return AccountResponse(
        id=str(account.id),
        iban=account.iban,
        bic=account.bic,
        account_type=account.account_type,
        owner_name=account.owner_name,
        region=account.region,
        currency=account.currency,
        balance=str(account.balance),
        available_balance=str(account.available_balance),
        status=account.status,
        sharia_flag=account.sharia_flag,
        parent_iban=account.parent_iban,
        created_at=account.created_at,
    )


@router.get("/accounts/{iban}/balance")
async def get_balance(iban: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    result = await session.execute(select(Account).where(Account.iban == iban))
    account = result.scalar_one_or_none()
    if not account:
        raise not_found(ErrorCode.ACCOUNT_NOT_FOUND, f"Account {iban} not found")
    return {
        "iban": iban,
        "currency": account.currency,
        "balance": str(account.balance),
        "available_balance": str(account.available_balance),
        "status": account.status,
    }


@router.get("/accounts/{iban}/statement")
async def get_statement(
    iban: str,
    from_date: str | None = None,
    to_date: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Cursor-paginated camt.053 account statement."""
    from mocksim.persistence.models import AccountEntry
    from sqlalchemy import and_, desc
    import base64

    result = await session.execute(select(Account).where(Account.iban == iban))
    account = result.scalar_one_or_none()
    if not account:
        raise not_found(ErrorCode.ACCOUNT_NOT_FOUND, f"Account {iban} not found")

    # Build entry query
    filters = [AccountEntry.account_iban == iban]

    if from_date:
        from datetime import date as _date
        try:
            filters.append(AccountEntry.value_date >= _date.fromisoformat(from_date))
        except ValueError:
            raise bad_request(ErrorCode.VALIDATION_ERROR, f"Invalid from_date: {from_date}")

    if to_date:
        from datetime import date as _date
        try:
            filters.append(AccountEntry.value_date <= _date.fromisoformat(to_date))
        except ValueError:
            raise bad_request(ErrorCode.VALIDATION_ERROR, f"Invalid to_date: {to_date}")

    # Cursor: base64-encoded ISO datetime (booking_datetime of last seen entry)
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(base64.b64decode(cursor).decode())
            filters.append(AccountEntry.booking_datetime < cursor_dt)
        except Exception:
            raise bad_request(ErrorCode.VALIDATION_ERROR, "Invalid cursor")

    limit = max(1, min(limit, 200))
    entries_result = await session.execute(
        select(AccountEntry)
        .where(and_(*filters))
        .order_by(desc(AccountEntry.booking_datetime))
        .limit(limit + 1)
    )
    entries = list(entries_result.scalars().all())

    next_cursor: str | None = None
    if len(entries) > limit:
        entries = entries[:limit]
        last_entry = entries[-1]
        next_cursor = base64.b64encode(last_entry.booking_datetime.isoformat().encode()).decode()

    return {
        "message_id": new_ulid(),
        "account_iban": iban,
        "account_currency": account.currency,
        "balance": str(account.balance),
        "entries": [
            {
                "entry_ref": str(e.id),
                "amount": str(e.amount),
                "currency": e.currency,
                "credit_debit": e.credit_debit,
                "booking_datetime": e.booking_datetime.isoformat(),
                "value_date": e.value_date.isoformat(),
                "narration": e.narration,
                "counterparty_name": e.counterparty_name,
                "counterparty_iban": e.counterparty_iban,
                "balance_after": str(e.balance_after),
                "ref_codes": e.ref_codes,
            }
            for e in entries
        ],
        "total_in_page": len(entries),
        "next_cursor": next_cursor,
    }


@router.post("/payments/initiate", status_code=202, response_model=PaymentInitiateResponse)
async def initiate_payment(
    body: PaymentInitiateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str = Depends(require_idempotency_key),
) -> PaymentInitiateResponse:
    """
    pain.001 disbursement instruction.
    Phase 0: validates accounts exist + sharia flag, persists instruction, returns ACCP.
    Phase 1: fires camt.054 credit notification + outbox webhook.
    """
    tenant_id = current_mock_tenant_id.get()
    assert tenant_id is not None

    cached = await get_or_start(session, tenant_id, idempotency_key, body.model_dump_json().encode(), "money")
    if cached:
        return PaymentInitiateResponse(**cached.response_body)  # type: ignore[arg-type]

    # Validate debtor account exists and is active
    result = await session.execute(select(Account).where(Account.iban == body.debtor_iban))
    debtor = result.scalar_one_or_none()
    if not debtor:
        raise not_found(ErrorCode.ACCOUNT_NOT_FOUND, f"Debtor account not found: {body.debtor_iban}")

    # Sharia validation: sharia_compliant=True payment → account must have sharia_flag
    if body.sharia_compliant and not debtor.sharia_flag:
        raise MockSimError(
            422, ErrorCode.SHARIA_VIOLATION,
            "Sharia-compliant payment cannot debit a non-sharia account (AC06)"
        )

    now = datetime.now(timezone.utc)
    instruction = PaymentInstruction(
        id=body.end_to_end_id,
        mock_tenant_id=tenant_id,
        message_id=body.message_id,
        instruction_id=body.instruction_id,
        debtor_iban=body.debtor_iban,
        creditor_iban=body.creditor_iban,
        creditor_name=body.creditor_name,
        amount=body.amount.minor_units,
        currency=body.amount.currency,
        rail=body.rail,
        sharia_compliant=body.sharia_compliant,
        remittance_info=body.remittance_info,
        status="accepted",
        created_at=now,
    )
    session.add(instruction)
    await session.flush()

    # Schedule payment processing job (fires at rail-appropriate sim-time)
    await schedule_payment_processing(session, tenant_id, instruction, get_region(debtor.region))

    resp = PaymentInitiateResponse(
        end_to_end_id=body.end_to_end_id,
        status="accepted",
        message="Payment instruction accepted (ACCP)",
        created_at=now,
    )
    await complete(session, tenant_id, idempotency_key, 202, resp.model_dump(mode="json"))
    return resp


@router.get("/payments")
async def list_payments(
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """List recent payment instructions for the current tenant."""
    tenant_id = current_mock_tenant_id.get()
    result = await session.execute(
        select(PaymentInstruction)
        .where(PaymentInstruction.mock_tenant_id == tenant_id)
        .order_by(PaymentInstruction.created_at.desc())
        .limit(min(limit, 200))
    )
    instrs = result.scalars().all()
    return [
        {
            "end_to_end_id": i.id, "message_id": i.message_id,
            "debtor_iban": i.debtor_iban, "creditor_iban": i.creditor_iban,
            "creditor_name": i.creditor_name, "amount": str(i.amount),
            "currency": i.currency, "rail": i.rail, "status": i.status,
            "reject_reason": i.reject_reason,
            "created_at": i.created_at.isoformat(),
            "settled_at": i.settled_at.isoformat() if i.settled_at else None,
        }
        for i in instrs
    ]


@router.get("/payments/{e2e_id}/status")
async def payment_status(e2e_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    result = await session.execute(
        select(PaymentInstruction).where(PaymentInstruction.id == e2e_id)
    )
    instr = result.scalar_one_or_none()
    if not instr:
        raise not_found(ErrorCode.PAYMENT_NOT_FOUND, f"Payment {e2e_id} not found")
    return {
        "end_to_end_id": instr.id,
        "status": instr.status,
        "created_at": instr.created_at.isoformat(),
        "settled_at": instr.settled_at.isoformat() if instr.settled_at else None,
        "reject_reason": instr.reject_reason,
    }


class InstantCreditTransferRequest(BaseModel):
    """pacs.008 instant credit transfer — simplified canonical form."""
    message_id: str
    instruction_id: str
    end_to_end_id: str = Field(..., max_length=35)
    uetr: str                  # UUIDv4 unique end-to-end transaction reference
    amount: Money
    debtor_iban: str
    debtor_name: str
    creditor_iban: str
    creditor_name: str
    rail: str                  # Must be an instant rail (RAAST, Aani, IPS, etc.)
    sharia_compliant: bool = False
    remittance_info: str | None = None
    purpose_code: str | None = None


@router.post("/instant/credit-transfer", status_code=202, response_model=PaymentInitiateResponse)
async def instant_credit_transfer(
    body: InstantCreditTransferRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str = Depends(require_idempotency_key),
) -> PaymentInitiateResponse:
    """pacs.008 instant payment — submits on instant rail with 30s sim-time settlement."""
    if body.rail not in _INSTANT_RAILS:
        raise bad_request(
            ErrorCode.VALIDATION_ERROR,
            f"rail '{body.rail}' is not an instant rail. "
            f"Supported: {', '.join(sorted(_INSTANT_RAILS))}",
        )

    tenant_id = current_mock_tenant_id.get()
    assert tenant_id is not None

    cached = await get_or_start(session, tenant_id, idempotency_key, body.model_dump_json().encode(), "money")
    if cached:
        return PaymentInitiateResponse(**cached.response_body)  # type: ignore[arg-type]

    # Validate debtor
    result = await session.execute(select(Account).where(Account.iban == body.debtor_iban))
    debtor = result.scalar_one_or_none()
    if not debtor:
        raise not_found(ErrorCode.ACCOUNT_NOT_FOUND, f"Debtor account not found: {body.debtor_iban}")

    if body.sharia_compliant and not debtor.sharia_flag:
        raise MockSimError(
            422, ErrorCode.SHARIA_VIOLATION,
            "Sharia-compliant transfer cannot debit a non-sharia account (AC06)",
        )

    now = datetime.now(timezone.utc)
    instruction = PaymentInstruction(
        id=body.end_to_end_id,
        mock_tenant_id=tenant_id,
        message_id=body.message_id,
        instruction_id=body.instruction_id,
        debtor_iban=body.debtor_iban,
        creditor_iban=body.creditor_iban,
        creditor_name=body.creditor_name,
        amount=body.amount.minor_units,
        currency=body.amount.currency,
        rail=body.rail,
        sharia_compliant=body.sharia_compliant,
        remittance_info=body.remittance_info,
        status="accepted",
        created_at=now,
    )
    session.add(instruction)
    await session.flush()

    await schedule_payment_processing(session, tenant_id, instruction, get_region(debtor.region))

    resp = PaymentInitiateResponse(
        end_to_end_id=body.end_to_end_id,
        status="accepted",
        message=f"Instant transfer accepted on {body.rail} (ACCP)",
        created_at=now,
    )
    await complete(session, tenant_id, idempotency_key, 202, resp.model_dump(mode="json"))
    return resp


@router.post("/instant/rtp", status_code=202)
async def request_to_pay(request: Request) -> dict[str, Any]:
    """pain.013 RTP — Phase 1."""
    raise not_implemented_yet("POST /bank/instant/rtp")


@router.post("/mandates", status_code=201, response_model=MandateResponse)
async def create_mandate(
    body: CreateMandateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str = Depends(require_idempotency_key),
) -> MandateResponse:
    """pain.009 mandate creation."""
    tenant_id = current_mock_tenant_id.get()
    assert tenant_id is not None

    cached = await get_or_start(session, tenant_id, idempotency_key, body.model_dump_json().encode(), "non_money")
    if cached:
        return MandateResponse(**cached.response_body)  # type: ignore[arg-type]

    resp = await create_mandate_handler(
        session, body, tenant_id,
        trazmo_tenant_id=getattr(request.state, "trazmo_tenant_id", None),
    )
    await complete(session, tenant_id, idempotency_key, 201, resp.model_dump(mode="json"))
    return resp


@router.post("/mandates/{mandate_id}/collect", status_code=202, response_model=CollectMandateResponse)
async def collect_mandate(
    mandate_id: str,
    body: CollectMandateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str = Depends(require_idempotency_key),
) -> CollectMandateResponse:
    """pain.008 direct debit collection."""
    tenant_id = current_mock_tenant_id.get()
    assert tenant_id is not None

    cached = await get_or_start(session, tenant_id, idempotency_key, body.model_dump_json().encode(), "money")
    if cached:
        return CollectMandateResponse(**cached.response_body)  # type: ignore[arg-type]

    resp = await collect_mandate_handler(session, mandate_id, body, tenant_id)
    await complete(session, tenant_id, idempotency_key, 202, resp.model_dump(mode="json"))
    return resp


@router.get("/mandates/{mandate_id}", response_model=MandateResponse)
async def get_mandate(
    mandate_id: str,
    session: AsyncSession = Depends(get_session),
) -> MandateResponse:
    """Mandate state."""
    tenant_id = current_mock_tenant_id.get()
    assert tenant_id is not None
    return await get_mandate_handler(session, mandate_id, tenant_id)


@router.post("/virtual-accounts", status_code=201)
async def create_virtual_account(
    body: CreateAccountRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str = Depends(require_idempotency_key),
) -> AccountResponse:
    """Allocate a VAN under a pool account. Delegates to create_account."""
    body.account_type = "merchant_van"
    return await create_account(body, request, session, idempotency_key)


@router.post("/webhooks/subscriptions", status_code=201, response_model=WebhookSubscriptionResponse)
async def create_webhook_subscription(
    body: WebhookSubscriptionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str = Depends(require_idempotency_key),
) -> WebhookSubscriptionResponse:
    tenant_id = current_mock_tenant_id.get()
    assert tenant_id is not None

    cached = await get_or_start(session, tenant_id, idempotency_key, body.model_dump_json().encode(), "non_money")
    if cached:
        return WebhookSubscriptionResponse(**cached.response_body)  # type: ignore[arg-type]

    try:
        _validate_target_url(body.url)
    except ValueError as exc:
        raise MockSimError(422, ErrorCode.INVALID_TARGET_URL, str(exc))

    now = datetime.now(timezone.utc)
    sub = WebhookSubscription(
        id=uuid.uuid4(),
        mock_tenant_id=tenant_id,
        trazmo_tenant_id=getattr(request.state, "trazmo_tenant_id", None),
        surface="bank",
        target_url=body.url,
        target_secret=body.secret,
        event_types=body.event_types,
        format=body.format,
        status="active",
        created_at=now,
    )
    session.add(sub)
    await session.flush()

    resp = WebhookSubscriptionResponse(
        id=str(sub.id),
        surface="bank",
        target_url=body.url,
        event_types=body.event_types,
        format=body.format,
        status="active",
        created_at=now,
    )
    await complete(session, tenant_id, idempotency_key, 201, resp.model_dump(mode="json"))
    return resp
