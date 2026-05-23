"""
ISO 20022 pain.* message schemas (canonical JSON form).

pain.001 — Customer Credit Transfer Initiation (disbursement)
pain.002 — Payment Status Report (accept / reject)
pain.008 — Direct Debit Initiation (mandate collection)
pain.009 — Mandate Initiation Request
pain.013 — Creditor Payment Activation Request (RTP)
pain.014 — Creditor Payment Activation Request Status Report

Wire format is Trazmo-internal canonical JSON modelled on ISO 20022 semantics.
Money amounts use minor-units integer strings (DESIGN.md §1 principle 9).
"""
from __future__ import annotations
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from mocksim.core.money import Money


# ── pain.001 — Credit Transfer Initiation ────────────────────────

class Pain001Party(BaseModel):
    name: str
    id: str | None = None


class Pain001Account(BaseModel):
    iban: str
    currency: str | None = None  # optional; used for currency-mismatch validation


class Pain001PaymentInstruction(BaseModel):
    end_to_end_id: str = Field(..., max_length=35)
    instruction_id: str
    amount: Money
    debtor_account: Pain001Account
    creditor_account: Pain001Account
    creditor: Pain001Party
    remittance_info: str | None = None
    rail: str  # RAAST | PRISM | 1LINK | Aani | UAEFTS | IPS | Sarie | InstaPay | EFTS
    sharia_compliant: bool = False
    requested_execution_date: str | None = None  # YYYY-MM-DD; None = immediate


class Pain001(BaseModel):
    """Customer Credit Transfer Initiation."""
    message_id: str
    creation_datetime: datetime
    initiating_party: Pain001Party
    payments: list[Pain001PaymentInstruction]


# ── pain.002 — Payment Status Report ─────────────────────────────

class Pain002StatusDetail(BaseModel):
    reason_code: str  # ISO 20022 External Code e.g. AC01, AM04
    additional_info: str | None = None


class Pain002(BaseModel):
    """Payment Status Report — MockSim issues this as a webhook payload."""
    message_id: str
    creation_datetime: datetime
    original_message_id: str
    original_end_to_end_id: str
    transaction_status: str  # ACCP | ACSC | RJCT | PDNG
    status_reason: Pain002StatusDetail | None = None
    accepted_at: datetime | None = None
    settled_at: datetime | None = None


# ── pain.008 — Direct Debit Initiation ───────────────────────────

class Pain008(BaseModel):
    """Direct Debit Initiation — used for mandate collection."""
    message_id: str
    creation_datetime: datetime
    mandate_id: str
    sequence_type: str = "RCUR"  # FRST | RCUR | FNAL | OOFF
    requested_collection_date: str  # YYYY-MM-DD
    amount: Money
    creditor_account: Pain001Account
    debtor_account: Pain001Account
    debtor_name: str


# ── pain.009 — Mandate Initiation Request ────────────────────────

class Pain009(BaseModel):
    """Mandate Initiation Request — create a direct debit mandate."""
    message_id: str
    creation_datetime: datetime
    mandate_request_id: str
    creditor_account: Pain001Account
    debtor_account: Pain001Account
    debtor_name: str
    max_amount: Money | None = None
    frequency: str = "MNTH"  # DAIL | WEEK | MNTH | YEAR
    first_collection_date: str | None = None  # YYYY-MM-DD
    final_collection_date: str | None = None
    sharia_compliant: bool = False


# ── pain.013 — Request-to-Pay ────────────────────────────────────

class Pain013(BaseModel):
    """Creditor Payment Activation Request — RTP (recovery via RAAST / IPS)."""
    message_id: str
    creation_datetime: datetime
    end_to_end_id: str
    creditor_account: Pain001Account
    debtor_account: Pain001Account
    debtor_name: str
    amount: Money
    purpose: str | None = None  # e.g. "LOAN_RECOVERY"
    expiry_datetime: datetime | None = None


# ── pain.014 — RTP Status Report ─────────────────────────────────

class Pain014(BaseModel):
    """RTP response from debtor bank (pay / decline)."""
    message_id: str
    creation_datetime: datetime
    original_end_to_end_id: str
    status: str  # ACTC (accepted) | RJCT (declined) | PDNG
    reject_reason: str | None = None
    response_datetime: datetime | None = None
