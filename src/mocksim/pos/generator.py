"""
POS GMV Generator (DESIGN.md §4.2).

Streaming, seeded-RNG sale generator:
  • Poisson txn count per merchant per day (scaled by weekend factor)
  • Lognormal ticket amounts centred on merchant.avg_ticket_minor_units
  • Card network mix respects regional domestic_routing_pct
  • MDR / VAT / WHT calculated per PosRegionConfig
  • Writes PosTransaction rows + outbox events in batches of 100
  • Schedules next-day job when schedule_next=True (default)

Public entry points
-------------------
  generate_merchant_day(mock_tenant_id, merchant_id, sim_date) -> int
      Generate one full sim-day for one merchant. Returns txn count.

  register_handlers()
      Register SimScheduler "pos.generate_gmv" handler. Call once from main.py.
"""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mocksim.core import outbox as outbox_module
from mocksim.core.identifiers import (
    gen_arn,
    gen_auth_code,
    gen_rrn,
    gen_stan,
    new_ulid,
    rng_for,
)
from mocksim.persistence.database import async_session_factory
from mocksim.persistence.models import Merchant, PosTransaction, WebhookSubscription
from mocksim.pos.regions import PosRegionConfig, get_region

log = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────

_BATCH_SIZE = 100  # PosTransaction rows per Postgres transaction

# Synthetic 6-digit BINs per card network (for realistic-looking test data)
_CARD_BINS: dict[str, str] = {
    "Visa": "412345",
    "Mastercard": "512345",
    "PayPak": "636789",
    "UnionPay": "620123",
    "mada": "588845",
    "BENEFIT": "558844",
    "Meeza": "507803",
    "UAE Switch": "586824",
    "American Express": "372345",
}
_DEFAULT_BIN = "999999"

# Rough international card-network market-share weights (used when domestic
# network is not chosen).  Normalised at runtime per available networks.
_INTL_WEIGHTS: dict[str, float] = {
    "Visa": 0.55,
    "Mastercard": 0.35,
    "American Express": 0.05,
    "UnionPay": 0.05,
}

# Mock acquirer terminal prefix per region (feeds STAN generation)
_TERMINAL_PREFIX: dict[str, str] = {
    "PK": "PK01",
    "AE": "AE01",
    "SA": "SA01",
    "EG": "EG01",
    "BH": "BH01",
}


# ── Statistical helpers ───────────────────────────────────────────

def _poisson_approx(rng: Any, lam: float) -> int:
    """
    Generate a Poisson-distributed integer.

    Uses Knuth's exact algorithm for λ ≤ 30, normal approximation for λ > 30.
    Both paths consume RNG draws, preserving determinism when lam is constant.
    """
    if lam <= 0:
        return 0
    if lam > 30:
        # Normal approximation: Poisson(λ) ≈ Normal(λ, √λ) for large λ
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    # Knuth algorithm — exact
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


def _lognormal_amount(rng: Any, median_minor_units: int, sigma: float = 0.5) -> int:
    """
    Sample a lognormal-distributed ticket amount.

    median = median_minor_units (exp(mu) = median → mu = ln(median)).
    sigma = 0.5 gives a coefficient of variation of ~53%, realistic for retail.
    Capped at 10 × median to prevent extreme outliers skewing settlement.
    """
    if median_minor_units <= 0:
        return 1
    mu = math.log(median_minor_units)
    raw = math.exp(rng.gauss(mu, sigma))
    cap = median_minor_units * 10
    return max(1, int(min(raw, cap)))


def _pick_card_network(rng: Any, region_cfg: PosRegionConfig) -> str:
    """
    Pick a card network for this transaction.

    Step 1: with probability domestic_routing_pct, return domestic_network.
    Step 2: otherwise sample from remaining networks using _INTL_WEIGHTS.
    """
    if region_cfg.domestic_network and rng.random() < region_cfg.domestic_routing_pct:
        return region_cfg.domestic_network

    # Non-domestic pool (exclude the domestic network to avoid double-routing)
    intl_nets = [
        n for n in region_cfg.card_networks if n != region_cfg.domestic_network
    ]
    if not intl_nets:
        return region_cfg.card_networks[0]
    if len(intl_nets) == 1:
        return intl_nets[0]

    weights = [_INTL_WEIGHTS.get(n, 0.05) for n in intl_nets]
    total = sum(weights)
    weights = [w / total for w in weights]

    r = rng.random()
    cumulative = 0.0
    for net, w in zip(intl_nets, weights):
        cumulative += w
        if r <= cumulative:
            return net
    return intl_nets[-1]


def _calc_fees(
    amount: int,
    region_cfg: PosRegionConfig,
) -> tuple[int, int, int, int]:
    """
    Compute MDR, VAT on MDR, WHT, net settlement (all in minor units).

    MDR = amount × mdr_bps / 10 000
    VAT = MDR × vat_on_mdr_pct
    WHT = amount × wht_pct (withholding tax, PK B2B)
    net = amount − MDR − VAT − WHT
    """
    mdr = int(amount * region_cfg.default_mdr_bps / 10_000)
    vat = int(mdr * region_cfg.vat_on_mdr_pct)
    wht = int(amount * region_cfg.wht_pct)
    net = amount - mdr - vat - wht
    return mdr, vat, wht, net


def _expected_settlement_date(
    sim_date: date,
    settlement_days: int,
    weekend_days: set[int],
) -> date:
    """Advance sim_date by settlement_days working days, skipping weekends."""
    result = sim_date
    added = 0
    while added < settlement_days:
        result += timedelta(days=1)
        if result.isoweekday() not in weekend_days:
            added += 1
    return result


def _spread_timestamps(
    rng: Any,
    sim_date: date,
    count: int,
) -> list[datetime]:
    """
    Generate `count` sorted UTC datetimes within business hours (08:00–22:00)
    on sim_date. Uniform distribution; sorted ascending.
    """
    start_s = 8 * 3600   # 08:00 UTC
    end_s = 22 * 3600    # 22:00 UTC
    span = end_s - start_s
    offsets = sorted(rng.randint(0, span) for _ in range(count))
    base = datetime(sim_date.year, sim_date.month, sim_date.day, tzinfo=timezone.utc)
    return [base + timedelta(seconds=start_s + s) for s in offsets]


# ── DB helpers ────────────────────────────────────────────────────

async def _load_subscriptions(
    session: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[WebhookSubscription]:
    """Load active POS webhook subscriptions. Runs inside a caller-owned session."""
    result = await session.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.mock_tenant_id == tenant_id,
            WebhookSubscription.surface == "pos",
            WebhookSubscription.status == "active",
        )
    )
    return list(result.scalars().all())


def _should_deliver(sub: WebhookSubscription, event_type: str) -> bool:
    """Return True if this subscription wants event_type (empty list = wildcard)."""
    return not sub.event_types or event_type in sub.event_types


def _build_webhook_payload(
    txn: PosTransaction,
    merchant: Merchant,
) -> dict[str, Any]:
    """Serialise a PosTransaction to the pos.transaction.authorized payload."""
    return {
        "transaction_id": txn.id,
        "merchant_id": txn.merchant_id,
        "merchant_name": merchant.name,
        "mcc": merchant.mcc,
        "region": txn.region,
        "event_type": "sale",
        "amount": str(txn.amount),
        "currency": txn.currency,
        "mdr": str(txn.mdr),
        "vat_on_mdr": str(txn.vat_on_mdr),
        "wht": str(txn.wht),
        "net_settlement": str(txn.net_settlement),
        "card_network": txn.card_network,
        "card_bin": txn.card_bin,
        "card_last4": txn.card_last4,
        "rrn": txn.rrn,
        "stan": txn.stan,
        "auth_code": txn.auth_code,
        "arn": txn.arn,
        "response_code": txn.response_code,
        "expected_settlement_date": (
            txn.expected_settlement_date.isoformat()
            if txn.expected_settlement_date
            else None
        ),
        "sim_date": txn.sim_date.isoformat(),
        "event_timestamp": txn.event_timestamp.isoformat(),
    }


# ── Core generation function ──────────────────────────────────────

async def generate_merchant_day(
    mock_tenant_id: uuid.UUID,
    merchant_id: str,
    sim_date: date,
    schedule_next: bool = True,
) -> int:
    """
    Generate a full day of POS transactions for one merchant.

    Algorithm
    ---------
    1. Load the merchant; skip if inactive or not found.
    2. Compute Poisson(λ) txn count scaled by weekend factor.
    3. Pre-generate all timestamps for the day using the seeded RNG.
    4. Write PosTransaction rows + outbox events in batches of _BATCH_SIZE.
    5. Optionally schedule the next-day pos.generate_gmv job.

    All batches share the SAME rng instance so the sequence of random draws
    is fully deterministic: same (tenant, merchant, sim_date) → same output.

    Returns
    -------
    Total number of PosTransaction rows written.
    """
    # ── Load merchant ─────────────────────────────────────────────
    async with async_session_factory() as session:
        result = await session.execute(
            select(Merchant).where(
                Merchant.id == merchant_id,
                Merchant.mock_tenant_id == mock_tenant_id,
            )
        )
        merchant = result.scalar_one_or_none()

    if merchant is None:
        log.warning(
            "gmv.merchant_not_found",
            merchant_id=merchant_id,
            tenant_id=str(mock_tenant_id),
        )
        return 0

    if merchant.status != "active":
        log.debug(
            "gmv.merchant_skipped",
            merchant_id=merchant_id,
            status=merchant.status,
            sim_date=sim_date.isoformat(),
        )
        if schedule_next:
            await _schedule_next_day(mock_tenant_id, merchant_id, sim_date)
        return 0

    region_cfg = get_region(merchant.region)

    # ── Txn count (Poisson, weekend-scaled) ──────────────────────
    rng = rng_for(str(mock_tenant_id), merchant_id, sim_date)
    iso_weekday = sim_date.isoweekday()
    weekend_factor = 0.6 if iso_weekday in region_cfg.weekend_days else 1.0
    lambda_ = merchant.expected_daily_txns * weekend_factor
    txn_count = _poisson_approx(rng, lambda_)

    if txn_count == 0:
        log.debug(
            "gmv.zero_txns",
            merchant_id=merchant_id,
            sim_date=sim_date.isoformat(),
        )
        if schedule_next:
            await _schedule_next_day(mock_tenant_id, merchant_id, sim_date)
        return 0

    # ── Pre-generate timestamps (sorted, deterministic) ──────────
    timestamps = _spread_timestamps(rng, sim_date, txn_count)
    terminal_id = _TERMINAL_PREFIX.get(merchant.region, "SIM0")
    # Pick acquirer once per day for consistency (rotate by RNG)
    acquirer = region_cfg.acquirers[rng.randint(0, len(region_cfg.acquirers) - 1)]
    settle_date = _expected_settlement_date(
        sim_date, region_cfg.settlement_days, region_cfg.weekend_days
    )

    total_generated = 0

    # ── Batch commits ─────────────────────────────────────────────
    for batch_start in range(0, txn_count, _BATCH_SIZE):
        batch_end = min(batch_start + _BATCH_SIZE, txn_count)
        batch_ts = timestamps[batch_start:batch_end]

        async with async_session_factory() as session:
            # Load subs inside each batch session — they're small and rarely change
            subs = await _load_subscriptions(session, mock_tenant_id)
            now_wall = datetime.now(timezone.utc)

            for ts in batch_ts:
                # --- generate one txn (all draws from same rng) ---
                amount = _lognormal_amount(rng, merchant.avg_ticket_minor_units)
                network = _pick_card_network(rng, region_cfg)
                card_bin = _CARD_BINS.get(network, _DEFAULT_BIN)
                card_last4 = f"{rng.randint(0, 9999):04d}"
                rrn = gen_rrn(sim_date, rng)
                stan = gen_stan(terminal_id, sim_date, rng)
                auth_code = gen_auth_code(rng)
                arn = gen_arn(acquirer, sim_date, rng)
                mdr, vat, wht, net = _calc_fees(amount, region_cfg)
                txn_id = new_ulid()

                txn = PosTransaction(
                    id=txn_id,
                    mock_tenant_id=mock_tenant_id,
                    merchant_id=merchant_id,
                    region=merchant.region,
                    event_type="sale",
                    amount=amount,
                    currency=merchant.currency,
                    mdr=mdr,
                    vat_on_mdr=vat,
                    wht=wht,
                    net_settlement=net,
                    card_network=network,
                    card_bin=card_bin,
                    card_last4=card_last4,
                    rrn=rrn,
                    stan=stan,
                    auth_code=auth_code,
                    arn=arn,
                    response_code="00",
                    settlement_status="pending",
                    settlement_batch_id=None,
                    expected_settlement_date=settle_date,
                    sim_date=sim_date,
                    event_timestamp=ts,
                    created_at=now_wall,
                )
                session.add(txn)

                # Enqueue webhook for every eligible subscription
                webhook_payload = _build_webhook_payload(txn, merchant)
                for sub in subs:
                    if _should_deliver(sub, "pos.transaction.authorized"):
                        outbox_module.enqueue(
                            session,
                            mock_tenant_id,
                            merchant_id,            # partition_key → in-order per merchant
                            "pos.transaction.authorized",
                            webhook_payload,
                            sub.target_url,
                            sub.target_secret,
                        )

            await session.commit()
            total_generated += len(batch_ts)

    log.info(
        "gmv.day_generated",
        tenant_id=str(mock_tenant_id),
        merchant_id=merchant_id,
        region=merchant.region,
        sim_date=sim_date.isoformat(),
        txn_count=total_generated,
    )

    # ── Schedule next day + settlement ───────────────────────────
    if schedule_next:
        await _schedule_next_day(mock_tenant_id, merchant_id, sim_date)
        # Schedule settlement job for the expected settlement date of today's txns
        await _schedule_settlement(mock_tenant_id, merchant_id, settle_date)

    return total_generated


async def schedule_initial_generation(
    session: AsyncSession,
    mock_tenant_id: uuid.UUID,
    merchant_id: str,
    first_sim_date: date,
) -> None:
    """
    Schedule the first pos.generate_gmv job for a newly created merchant.
    Call this inside the same transaction as Merchant.insert().

    The job fires at midnight UTC of first_sim_date so it runs during the
    next clock advance that passes that midnight boundary.
    """
    from mocksim.core.sim_scheduler import schedule_at

    fire_at = datetime(
        first_sim_date.year,
        first_sim_date.month,
        first_sim_date.day,
        0, 0, 0,
        tzinfo=timezone.utc,
    )
    await schedule_at(
        session,
        mock_tenant_id,
        fire_at,
        "pos.generate_gmv",
        {
            "merchant_id": merchant_id,
            "sim_date": first_sim_date.isoformat(),
            "mock_tenant_id": str(mock_tenant_id),
        },
    )


# ── SimScheduler integration ──────────────────────────────────────

async def _job_handler(fire_at: datetime, payload: dict[str, Any]) -> None:
    """
    SimScheduler callback for "pos.generate_gmv" job type.

    Payload keys
    ------------
    merchant_id    : str
    sim_date       : ISO date string (YYYY-MM-DD)
    mock_tenant_id : UUID string
    """
    merchant_id: str = payload["merchant_id"]
    sim_date: date = date.fromisoformat(payload["sim_date"])
    tenant_id: uuid.UUID = uuid.UUID(payload["mock_tenant_id"])

    await generate_merchant_day(
        mock_tenant_id=tenant_id,
        merchant_id=merchant_id,
        sim_date=sim_date,
        schedule_next=True,
    )


async def _schedule_settlement(
    mock_tenant_id: uuid.UUID,
    merchant_id: str,
    settlement_date: date,
) -> None:
    """Schedule a pos.settle_batch job for settlement_date."""
    from mocksim.pos.settlement import schedule_settlement as _sched

    async with async_session_factory() as session:
        await _sched(session, mock_tenant_id, merchant_id, settlement_date)
        await session.commit()


async def _schedule_next_day(
    mock_tenant_id: uuid.UUID,
    merchant_id: str,
    current_sim_date: date,
) -> None:
    """Persist a pos.generate_gmv job for (current_sim_date + 1 day)."""
    from mocksim.core.sim_scheduler import schedule_at

    next_date = current_sim_date + timedelta(days=1)
    fire_at = datetime(
        next_date.year, next_date.month, next_date.day,
        0, 0, 0, tzinfo=timezone.utc,
    )
    async with async_session_factory() as session:
        await schedule_at(
            session,
            mock_tenant_id,
            fire_at,
            "pos.generate_gmv",
            {
                "merchant_id": merchant_id,
                "sim_date": next_date.isoformat(),
                "mock_tenant_id": str(mock_tenant_id),
            },
        )
        await session.commit()


def register_handlers() -> None:
    """
    Register all POS SimScheduler job-type handlers.
    Call once during application startup (main.py lifespan).
    """
    from mocksim.core.sim_scheduler import register_job_type

    register_job_type("pos.generate_gmv", _job_handler)
    log.debug("pos.generator.handlers_registered")
