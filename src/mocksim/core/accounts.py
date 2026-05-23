"""
Account operations — balance reads and entry writes (DESIGN.md §5.1).

NOT double-entry. MockSim mirrors what a bank externally exposes:
per-account balance + ordered entries. Trazmo's Vertex is the
authoritative double-entry ledger.

Pool invariant (F3): pool.balance == sum(van.balance for van in pool.children)
asserted after every operation touching a parent or its children.
"""
from __future__ import annotations
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mocksim.clock import clock
from mocksim.core.errors import (
    ErrorCode,
    MockSimError,
    bad_request,
    not_found,
)
from mocksim.core.money import Money
from mocksim.persistence.models import Account, AccountEntry


async def get_account(session: AsyncSession, iban: str) -> Account:
    result = await session.execute(select(Account).where(Account.iban == iban))
    account = result.scalar_one_or_none()
    if account is None:
        raise not_found(ErrorCode.ACCOUNT_NOT_FOUND, f"Account not found: {iban}")
    return account


async def assert_account_active(account: Account) -> None:
    if account.status == "closed":
        raise MockSimError(422, ErrorCode.ACCOUNT_CLOSED, f"Account {account.iban} is closed")
    if account.status == "dormant":
        raise MockSimError(422, ErrorCode.ACCOUNT_DORMANT, f"Account {account.iban} is dormant")


async def write_entry(
    session: AsyncSession,
    account: Account,
    amount: Money,
    credit_debit: str,  # 'CRDT' | 'DBIT'
    narration: str,
    value_date: date | None = None,
    counterparty_name: str | None = None,
    counterparty_iban: str | None = None,
    ref_codes: dict[str, Any] | None = None,
    transfer_id: uuid.UUID | None = None,
) -> AccountEntry:
    """
    Write a ledger entry and update balance atomically.
    Call this inside the same transaction as the business operation + outbox enqueue.
    """
    if amount.currency != account.currency:
        raise bad_request(
            ErrorCode.CURRENCY_MISMATCH,
            f"Entry currency {amount.currency} != account currency {account.currency}",
        )

    delta = amount.minor_units if credit_debit == "CRDT" else -amount.minor_units
    new_balance = account.balance + delta

    if credit_debit == "DBIT" and new_balance < 0:
        raise MockSimError(
            422,
            ErrorCode.INSUFFICIENT_FUNDS,
            f"Insufficient funds: balance {account.balance}, debit {amount.minor_units}",
        )

    account.balance = new_balance
    account.available_balance = new_balance
    account.updated_at = datetime.now(timezone.utc)

    now = clock.now()
    entry = AccountEntry(
        id=uuid.uuid4(),
        mock_tenant_id=account.mock_tenant_id,
        account_iban=account.iban,
        amount=abs(delta),
        credit_debit=credit_debit,
        currency=amount.currency,
        balance_after=new_balance,
        booking_datetime=now,
        value_date=value_date or now.date(),
        narration=narration,
        counterparty_name=counterparty_name,
        counterparty_iban=counterparty_iban,
        ref_codes=ref_codes,
        transfer_id=transfer_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(entry)
    return entry


async def internal_transfer(
    session: AsyncSession,
    src_iban: str,
    dst_iban: str,
    amount: Money,
    narration: str,
    ref_codes: dict[str, Any] | None = None,
) -> tuple[AccountEntry, AccountEntry]:
    """
    Transfer between two MockSim-owned accounts.
    Writes paired entries (one debit, one credit) linked by transfer_id.
    Asserts pool invariant if either account is a pool or VAN.
    """
    src = await get_account(session, src_iban)
    dst = await get_account(session, dst_iban)

    await assert_account_active(src)
    await assert_account_active(dst)

    if src.currency != dst.currency:
        raise bad_request(
            ErrorCode.CURRENCY_MISMATCH,
            f"Cannot transfer between accounts of different currencies: {src.currency} vs {dst.currency}",
        )

    transfer_id = uuid.uuid4()

    debit_entry = await write_entry(
        session, src, amount, "DBIT", narration,
        counterparty_iban=dst_iban, transfer_id=transfer_id, ref_codes=ref_codes,
    )
    credit_entry = await write_entry(
        session, dst, amount, "CRDT", narration,
        counterparty_iban=src_iban, transfer_id=transfer_id, ref_codes=ref_codes,
    )

    await _assert_pool_invariant(session, src)
    await _assert_pool_invariant(session, dst)

    return debit_entry, credit_entry


async def _assert_pool_invariant(session: AsyncSession, account: Account) -> None:
    """
    Pool invariant (F3): pool.balance == sum(children.balance).
    Checked after every operation on a pool or its VAN children.
    """
    if account.account_type == "merchant_van" and account.parent_iban:
        pool = await get_account(session, account.parent_iban)
        await _check_pool_sum(session, pool)
    elif account.account_type == "pool":
        await _check_pool_sum(session, account)


async def _check_pool_sum(session: AsyncSession, pool: Account) -> None:
    result = await session.execute(
        select(Account).where(Account.parent_iban == pool.iban)
    )
    children = result.scalars().all()
    child_sum = sum(c.balance for c in children)

    if children and pool.balance != child_sum:
        # This is a P0 data integrity violation — log loudly and raise
        import structlog
        log = structlog.get_logger(__name__)
        log.critical(
            "accounts.pool_invariant_violated",
            pool_iban=pool.iban,
            pool_balance=pool.balance,
            child_sum=child_sum,
            delta=pool.balance - child_sum,
        )
        raise MockSimError(
            500,
            ErrorCode.INTERNAL_ERROR,
            "Pool balance invariant violated — data integrity error",
        )
