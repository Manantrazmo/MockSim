"""
POS GMV Generator API (DESIGN.md §4).

Phase 0: Merchant CRUD + webhook subscription endpoints wired up.
         GMV generation engine (Phase 1) stubs return 501.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mocksim.clock import clock
from mocksim.core.errors import ErrorCode, MockSimError, not_found, not_implemented_yet
from mocksim.core.idempotency import get_or_start, complete, require_idempotency_key
from mocksim.core.identifiers import new_ulid
from mocksim.core.webhook import _validate_target_url
from mocksim.persistence.database import get_session, current_mock_tenant_id
from mocksim.persistence.models import Merchant, WebhookSubscription
from mocksim.pos.generator import schedule_initial_generation
from mocksim.pos.regions import get_region

router = APIRouter(prefix="/pos", tags=["POS"])


# ── Request / Response schemas ────────────────────────────────────

class CreateMerchantRequest(BaseModel):
    name: str
    region: str = Field(..., pattern="^[A-Z]{2}$")
    mcc: str = Field(..., pattern=r"^\d{4}$")
    expected_daily_txns: int = Field(50, ge=1, le=10000)
    avg_ticket_major_units: float = Field(..., gt=0)
    risk_tier: str = Field("standard", pattern="^(low|standard|high)$")


class MerchantResponse(BaseModel):
    id: str
    name: str
    region: str
    mcc: str
    currency: str
    expected_daily_txns: int
    avg_ticket_minor_units: int
    risk_tier: str
    status: str
    created_at: datetime


class WebhookSubscriptionRequest(BaseModel):
    url: str
    secret: str = Field(..., min_length=16)
    event_types: list[str] = Field(default_factory=list)


class WebhookSubscriptionResponse(BaseModel):
    id: str
    surface: str
    target_url: str
    event_types: list[str]
    status: str
    created_at: datetime


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/merchants", status_code=201, response_model=MerchantResponse)
async def create_merchant(
    body: CreateMerchantRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str = Depends(require_idempotency_key),
) -> MerchantResponse:
    tenant_id = current_mock_tenant_id.get()
    assert tenant_id is not None

    cached = await get_or_start(session, tenant_id, idempotency_key, body.model_dump_json().encode(), "non_money")
    if cached:
        return MerchantResponse(**cached.response_body)  # type: ignore[arg-type]

    region_cfg = get_region(body.region)
    decimals = 2 if region_cfg.currency != "BHD" else 3
    avg_minor = int(body.avg_ticket_major_units * (10 ** decimals))
    mid = f"MID_{new_ulid()[:8].upper()}"
    now = datetime.now(timezone.utc)

    merchant = Merchant(
        id=mid,
        mock_tenant_id=tenant_id,
        trazmo_tenant_id=getattr(request.state, "trazmo_tenant_id", None),
        region=body.region.upper(),
        name=body.name,
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

    # Schedule first GMV generation job for today's sim date
    await schedule_initial_generation(session, tenant_id, mid, clock.now().date())

    resp = MerchantResponse(
        id=mid,
        name=body.name,
        region=body.region.upper(),
        mcc=body.mcc,
        currency=region_cfg.currency,
        expected_daily_txns=body.expected_daily_txns,
        avg_ticket_minor_units=avg_minor,
        risk_tier=body.risk_tier,
        status="active",
        created_at=now,
    )
    await complete(session, tenant_id, idempotency_key, 201, resp.model_dump(mode="json"))
    return resp


@router.get("/merchants", response_model=list[MerchantResponse])
async def list_merchants(
    session: AsyncSession = Depends(get_session),
) -> list[MerchantResponse]:
    """List all merchants for the current tenant."""
    tenant_id = current_mock_tenant_id.get()
    result = await session.execute(
        select(Merchant)
        .where(Merchant.mock_tenant_id == tenant_id)
        .order_by(Merchant.created_at.desc())
    )
    merchants = result.scalars().all()
    return [
        MerchantResponse(
            id=m.id, name=m.name, region=m.region, mcc=m.mcc,
            currency=m.currency, expected_daily_txns=m.expected_daily_txns,
            avg_ticket_minor_units=m.avg_ticket_minor_units,
            risk_tier=m.risk_tier, status=m.status, created_at=m.created_at,
        )
        for m in merchants
    ]


@router.get("/merchants/{mid}", response_model=MerchantResponse)
async def get_merchant(
    mid: str,
    session: AsyncSession = Depends(get_session),
) -> MerchantResponse:
    result = await session.execute(select(Merchant).where(Merchant.id == mid))
    merchant = result.scalar_one_or_none()
    if not merchant:
        raise not_found(ErrorCode.MERCHANT_NOT_FOUND, f"Merchant {mid} not found")
    return MerchantResponse(
        id=merchant.id,
        name=merchant.name,
        region=merchant.region,
        mcc=merchant.mcc,
        currency=merchant.currency,
        expected_daily_txns=merchant.expected_daily_txns,
        avg_ticket_minor_units=merchant.avg_ticket_minor_units,
        risk_tier=merchant.risk_tier,
        status=merchant.status,
        created_at=merchant.created_at,
    )


@router.post("/transactions", status_code=201)
async def inject_transaction(request: Request) -> dict[str, Any]:
    """Manually inject a POS transaction — Phase 2 (granular scenario control)."""
    raise not_implemented_yet("POST /pos/transactions")


@router.get("/transactions")
async def list_transactions(
    merchant_id: str | None = None,
    sim_date: str | None = None,
    settlement_status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Cursor-paginated POS transaction query."""
    from mocksim.persistence.models import PosTransaction
    from sqlalchemy import and_, desc
    import base64

    tenant_id = current_mock_tenant_id.get()
    filters: list[Any] = [PosTransaction.mock_tenant_id == tenant_id]

    if merchant_id:
        filters.append(PosTransaction.merchant_id == merchant_id)

    if sim_date:
        from datetime import date as _date
        try:
            filters.append(PosTransaction.sim_date == _date.fromisoformat(sim_date))
        except ValueError:
            from mocksim.core.errors import bad_request as _bad
            raise _bad(ErrorCode.VALIDATION_ERROR, f"Invalid sim_date: {sim_date}")

    if settlement_status:
        filters.append(PosTransaction.settlement_status == settlement_status)

    if cursor:
        try:
            cursor_id = base64.b64decode(cursor).decode()
            # Cursor is the last seen txn ID (ULID, lexicographically sortable)
            filters.append(PosTransaction.id < cursor_id)
        except Exception:
            from mocksim.core.errors import bad_request as _bad
            raise _bad(ErrorCode.VALIDATION_ERROR, "Invalid cursor")

    limit = max(1, min(limit, 200))
    result = await session.execute(
        select(PosTransaction)
        .where(and_(*filters))
        .order_by(desc(PosTransaction.id))
        .limit(limit + 1)
    )
    txns = list(result.scalars().all())

    next_cursor: str | None = None
    if len(txns) > limit:
        txns = txns[:limit]
        next_cursor = base64.b64encode(txns[-1].id.encode()).decode()

    return {
        "items": [
            {
                "id": t.id,
                "merchant_id": t.merchant_id,
                "region": t.region,
                "event_type": t.event_type,
                "amount": str(t.amount),
                "currency": t.currency,
                "mdr": str(t.mdr),
                "vat_on_mdr": str(t.vat_on_mdr),
                "net_settlement": str(t.net_settlement),
                "card_network": t.card_network,
                "rrn": t.rrn,
                "auth_code": t.auth_code,
                "response_code": t.response_code,
                "settlement_status": t.settlement_status,
                "settlement_batch_id": t.settlement_batch_id,
                "expected_settlement_date": t.expected_settlement_date.isoformat() if t.expected_settlement_date else None,
                "sim_date": t.sim_date.isoformat(),
                "event_timestamp": t.event_timestamp.isoformat(),
            }
            for t in txns
        ],
        "total_in_page": len(txns),
        "next_cursor": next_cursor,
    }


@router.get("/settlements/{batch_id}")
async def get_settlement(
    batch_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Settlement batch detail."""
    from mocksim.persistence.models import SettlementBatch

    tenant_id = current_mock_tenant_id.get()
    result = await session.execute(
        select(SettlementBatch).where(
            SettlementBatch.id == batch_id,
            SettlementBatch.mock_tenant_id == tenant_id,
        )
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise not_found(ErrorCode.MERCHANT_NOT_FOUND, f"Settlement batch {batch_id} not found")

    return {
        "id": batch.id,
        "merchant_id": batch.merchant_id,
        "region": batch.region,
        "settlement_date": batch.settlement_date.isoformat(),
        "txn_count": batch.txn_count,
        "gross_amount": str(batch.gross_amount),
        "total_mdr": str(batch.total_mdr),
        "total_vat_on_mdr": str(batch.total_vat),
        "net_amount": str(batch.net_amount),
        "currency": batch.currency,
        "status": batch.status,
        "created_at": batch.created_at.isoformat(),
    }


@router.get("/settlements/{batch_id}/file")
async def download_settlement_file(
    batch_id: str,
    format: str = "json",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Download settlement file (JSON/CSV) — CSV format Phase 2."""
    from mocksim.persistence.models import SettlementBatch, PosTransaction

    tenant_id = current_mock_tenant_id.get()
    result = await session.execute(
        select(SettlementBatch).where(
            SettlementBatch.id == batch_id,
            SettlementBatch.mock_tenant_id == tenant_id,
        )
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise not_found(ErrorCode.MERCHANT_NOT_FOUND, f"Settlement batch {batch_id} not found")

    txns_result = await session.execute(
        select(PosTransaction).where(
            PosTransaction.settlement_batch_id == batch_id,
            PosTransaction.mock_tenant_id == tenant_id,
        ).order_by(PosTransaction.event_timestamp)
    )
    txns = list(txns_result.scalars().all())

    return {
        "batch_id": batch.id,
        "format": "json",
        "transactions": [
            {
                "id": t.id,
                "amount": str(t.amount),
                "currency": t.currency,
                "mdr": str(t.mdr),
                "net_settlement": str(t.net_settlement),
                "card_network": t.card_network,
                "card_bin": t.card_bin,
                "card_last4": t.card_last4,
                "rrn": t.rrn,
                "arn": t.arn,
                "auth_code": t.auth_code,
                "event_timestamp": t.event_timestamp.isoformat(),
                "sim_date": t.sim_date.isoformat(),
            }
            for t in txns
        ],
    }


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
        surface="pos",
        target_url=body.url,
        target_secret=body.secret,
        event_types=body.event_types,
        status="active",
        created_at=now,
    )
    session.add(sub)
    await session.flush()

    resp = WebhookSubscriptionResponse(
        id=str(sub.id),
        surface="pos",
        target_url=body.url,
        event_types=body.event_types,
        status="active",
        created_at=now,
    )
    await complete(session, tenant_id, idempotency_key, 201, resp.model_dump(mode="json"))
    return resp
