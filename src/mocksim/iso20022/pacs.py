"""
ISO 20022 pacs.* message schemas (canonical JSON form).

pacs.002 — Payment Status Report (interbank)
pacs.003 — Direct Debit Customer Payment (interbank leg of mandate collection)
pacs.008 — Credit Transfer (interbank — used for instant payments: Aani/RAAST/IPS/InstaPay)
"""
from __future__ import annotations
from datetime import datetime

from pydantic import BaseModel

from mocksim.core.money import Money


class Pacs002(BaseModel):
    """Interbank Payment Status Report."""
    message_id: str
    creation_datetime: datetime
    original_message_id: str
    original_instruction_id: str
    original_end_to_end_id: str
    transaction_status: str  # ACSP | ACSC | RJCT
    reason_code: str | None = None  # ISO 20022 External Code List
    settlement_datetime: datetime | None = None


class Pacs003Party(BaseModel):
    name: str
    iban: str
    bic: str | None = None


class Pacs003(BaseModel):
    """
    Direct Debit Customer Payment — the interbank leg of a mandate collection.
    MockSim issues this as part of the bank.mandate.collection.success webhook payload.
    """
    message_id: str
    creation_datetime: datetime
    end_to_end_id: str
    mandate_id: str
    amount: Money
    collection_date: str  # YYYY-MM-DD
    creditor: Pacs003Party
    debtor: Pacs003Party
    remittance_info: str | None = None


class Pacs008Party(BaseModel):
    name: str
    iban: str
    bic: str | None = None


class Pacs008(BaseModel):
    """
    FI to FI Customer Credit Transfer — instant payment over Aani / RAAST / IPS / InstaPay.
    Trazmo uses this for instant payouts to merchants and instant recovery.
    """
    message_id: str
    creation_datetime: datetime
    instruction_id: str
    end_to_end_id: str
    uetr: str  # UUIDv4 — ISO 20022 unique end-to-end transaction reference
    amount: Money
    settlement_date: str  # YYYY-MM-DD
    debtor: Pacs008Party
    creditor: Pacs008Party
    rail: str  # Aani | RAAST | IPS | InstaPay | BENEFIT
    sharia_compliant: bool = False
    remittance_info: str | None = None
    purpose_code: str | None = None  # e.g. LOAN, SALA, CASH
