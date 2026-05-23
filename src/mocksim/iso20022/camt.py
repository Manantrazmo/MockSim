"""
ISO 20022 camt.* message schemas (canonical JSON form).

camt.052 — Bank to Customer Account Report (intraday balance)
camt.053 — Bank to Customer Statement (end-of-day)
camt.054 — Bank to Customer Debit/Credit Notification (real-time event)

These are what MockSim pushes to Trazmo as webhook payloads and
returns from statement/balance endpoints.

Money amounts use minor-units integer strings (rule 9).
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from mocksim.core.money import Money


# ── camt.052 — Intraday Balance ───────────────────────────────────

class Camt052Balance(BaseModel):
    balance_type: str  # OPBD (opening) | CLBD (closing) | ITBD (intraday)
    amount: Money
    credit_debit: str  # CRDT | DBIT
    date: str  # YYYY-MM-DD


class Camt052(BaseModel):
    """Account report — current and available balances (intraday)."""
    message_id: str
    creation_datetime: datetime
    account_iban: str
    account_currency: str
    balances: list[Camt052Balance]
    report_date: str  # YYYY-MM-DD


# ── camt.053 — End-of-Day Statement ──────────────────────────────

class StatementEntry(BaseModel):
    entry_ref: str
    amount: Money
    credit_debit: str  # CRDT | DBIT
    booking_datetime: datetime
    value_date: str  # YYYY-MM-DD
    narration: str
    counterparty_name: str | None = None
    counterparty_iban: str | None = None
    rail: str | None = None
    ref_codes: dict[str, Any] | None = None
    balance_after: Money | None = None


class Camt053(BaseModel):
    """End-of-day account statement."""
    message_id: str
    creation_datetime: datetime
    account_iban: str
    account_currency: str
    from_date: str   # YYYY-MM-DD
    to_date: str     # YYYY-MM-DD
    opening_balance: Money
    closing_balance: Money
    entries: list[StatementEntry]
    # Pagination
    total_entries: int
    cursor: str | None = None  # for next page


# ── camt.054 — Credit/Debit Notification ─────────────────────────

class Camt054Entry(BaseModel):
    amount: Money
    credit_debit: str  # CRDT | DBIT
    booking_datetime: datetime
    value_date: str  # YYYY-MM-DD
    rail: str | None = None
    narration: str
    counterparty_name: str | None = None
    ref_codes: dict[str, Any] | None = None


class Camt054(BaseModel):
    """
    Real-time credit/debit notification — pushed as a webhook.
    Trazmo receives this when settlement funds land in a pool or VAN.
    The `virtual_account` field carries the VAN IBAN when applicable,
    letting Trazmo identify which merchant the funds belong to.
    """
    notification_id: str
    creation_datetime: datetime
    account_iban: str               # pool or merchant external account
    virtual_account: str | None     # VAN IBAN if settlement into a VAN
    entry: Camt054Entry
