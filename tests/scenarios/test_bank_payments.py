"""
Unit tests for the bank payment lifecycle helpers.

All tests here are pure-function (no DB required).

Tests:
  1. Rail timing — instant rails get 30s delay, RTGS gets next-day
  2. Fee/amount validation edge cases
  3. pacs.002 payload structure
  4. camt.054 payload structure
  5. _compute_fire_at for each rail type
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, date

import pytest

from mocksim.bank.payments import (
    _compute_fire_at,
    _build_pacs002_payload,
    _build_camt054_payload,
    _next_business_day,
    _INSTANT_RAILS,
    _RTGS_RAILS,
)
from mocksim.bank.regions import get_region
from mocksim.core.money import Money
from mocksim.persistence.models import PaymentInstruction


# ── Helpers ───────────────────────────────────────────────────────

def _make_instruction(
    rail: str = "RAAST",
    amount: int = 500000,
    currency: str = "PKR",
) -> PaymentInstruction:
    return PaymentInstruction(
        id="E2E-TEST-001",
        mock_tenant_id=None,  # type: ignore
        message_id="MSG-001",
        instruction_id="INSTR-001",
        debtor_iban="PK36SCBL0000001123456702",
        creditor_iban="PK36SCBL0000001123456703",
        creditor_name="Test Creditor",
        amount=amount,
        currency=currency,
        rail=rail,
        sharia_compliant=False,
        remittance_info=None,
        status="accepted",
        created_at=datetime.now(timezone.utc),
    )


# ── Rail timing ───────────────────────────────────────────────────

class TestComputeFireAt:
    def test_instant_rail_30s_delay(self) -> None:
        """All instant rails get a 30-second sim-time delay."""
        from mocksim.clock import clock

        cfg = get_region("PK")
        sim_now = clock.now()

        for rail in ["RAAST", "Aani", "IPS", "BENEFIT_Pay", "InstaPay", "FAWRI+"]:
            fire_at = _compute_fire_at(rail, cfg)
            delay_s = (fire_at - sim_now).total_seconds()
            # Should be close to 30s (within 5s tolerance for test execution)
            assert 25 <= delay_s <= 35, (
                f"{rail}: expected 30s delay, got {delay_s:.1f}s"
            )

    def test_rtgs_fires_next_business_day_at_cutoff(self) -> None:
        """RTGS fires at settlement_cutoff_hour on next business day."""
        cfg = get_region("PK")  # cutoff=16, weekend={6,7}
        fire_at = _compute_fire_at("PRISM", cfg)

        # Should be next business day
        next_biz = _next_business_day(datetime.now(timezone.utc).date(), cfg.weekend_days)
        assert fire_at.date() == next_biz
        assert fire_at.hour == cfg.settlement_cutoff_hour

    def test_ach_fires_next_business_day_at_9am(self) -> None:
        """ACH/other rails fire at 09:00 on next business day."""
        cfg = get_region("PK")
        fire_at = _compute_fire_at("NIFT", cfg)

        next_biz = _next_business_day(datetime.now(timezone.utc).date(), cfg.weekend_days)
        assert fire_at.date() == next_biz
        assert fire_at.hour == 9

    def test_instant_rails_set_correct(self) -> None:
        """Verify the instant rail set contains expected rails."""
        assert "RAAST" in _INSTANT_RAILS
        assert "Aani" in _INSTANT_RAILS
        assert "IPS" in _INSTANT_RAILS
        assert "InstaPay" in _INSTANT_RAILS

    def test_rtgs_rails_set_correct(self) -> None:
        assert "PRISM" in _RTGS_RAILS
        assert "Sarie" in _RTGS_RAILS
        assert "UAEFTS" in _RTGS_RAILS


# ── _next_business_day ────────────────────────────────────────────

class TestNextBusinessDay:
    def test_monday_from_sunday(self) -> None:
        """Sunday + 1 = Monday (PK weekend {6,7})."""
        pk_weekends = {6, 7}
        sunday = date(2026, 1, 4)  # isoweekday=7
        assert sunday.isoweekday() == 7
        result = _next_business_day(sunday, pk_weekends)
        assert result == date(2026, 1, 5)  # Monday

    def test_monday_from_saturday(self) -> None:
        """Saturday + 2 = Monday (skip Saturday, Sunday)."""
        pk_weekends = {6, 7}
        saturday = date(2026, 1, 3)
        result = _next_business_day(saturday, pk_weekends)
        assert result == date(2026, 1, 5)  # Monday

    def test_weekday_goes_to_next_day(self) -> None:
        pk_weekends = {6, 7}
        monday = date(2026, 1, 5)
        result = _next_business_day(monday, pk_weekends)
        assert result == date(2026, 1, 6)  # Tuesday

    def test_sa_thursday_to_sunday(self) -> None:
        """SA weekends Fri+Sat: Thursday → Sunday (skip Fri, Sat)."""
        sa_weekends = {5, 6}
        thursday = date(2026, 1, 8)
        result = _next_business_day(thursday, sa_weekends)
        assert result == date(2026, 1, 11)  # Sunday


# ── pacs.002 payload ──────────────────────────────────────────────

class TestBuildPacs002Payload:
    def test_settled_payload_structure(self) -> None:
        instr = _make_instruction()
        now = datetime.now(timezone.utc)
        payload = _build_pacs002_payload(instr, "ACSC", None, now)

        assert payload["tx_status"] == "ACSC"
        assert payload["end_to_end_id"] == "E2E-TEST-001"
        assert payload["original_message_id"] == "MSG-001"
        assert payload["reject_reason"] is None
        assert payload["settled_at"] == now.isoformat()
        assert payload["amount"] == "500000"
        assert payload["currency"] == "PKR"
        assert payload["rail"] == "RAAST"

    def test_rejected_payload_structure(self) -> None:
        instr = _make_instruction()
        now = datetime.now(timezone.utc)
        payload = _build_pacs002_payload(instr, "RJCT", "INSUFFICIENT_FUNDS", now)

        assert payload["tx_status"] == "RJCT"
        assert payload["reject_reason"] == "INSUFFICIENT_FUNDS"
        assert payload["settled_at"] is None

    def test_message_id_unique(self) -> None:
        """Each call generates a unique message_id."""
        instr = _make_instruction()
        now = datetime.now(timezone.utc)
        p1 = _build_pacs002_payload(instr, "ACSC", None, now)
        p2 = _build_pacs002_payload(instr, "ACSC", None, now)
        assert p1["message_id"] != p2["message_id"]


# ── camt.054 payload ──────────────────────────────────────────────

class TestBuildCamt054Payload:
    def test_payload_structure(self) -> None:
        instr = _make_instruction()
        amount = Money.from_minor(500000, "PKR")
        now = datetime.now(timezone.utc)
        payload = _build_camt054_payload(instr, amount, now)

        assert payload["account_iban"] == instr.creditor_iban
        assert payload["virtual_account"] is None
        assert "entry" in payload

        entry = payload["entry"]
        assert entry["credit_debit"] == "CRDT"
        assert entry["amount"] == "500000"
        assert entry["currency"] == "PKR"
        assert entry["rail"] == "RAAST"

    def test_notification_id_unique(self) -> None:
        instr = _make_instruction()
        amount = Money.from_minor(100, "PKR")
        now = datetime.now(timezone.utc)
        p1 = _build_camt054_payload(instr, amount, now)
        p2 = _build_camt054_payload(instr, amount, now)
        assert p1["notification_id"] != p2["notification_id"]


# ── Rail classification coverage ─────────────────────────────────

@pytest.mark.parametrize("rail,expected_type", [
    ("RAAST", "instant"),
    ("Aani", "instant"),
    ("IPS", "instant"),
    ("BENEFIT_Pay", "instant"),
    ("InstaPay", "instant"),
    ("FAWRI+", "instant"),
    ("PRISM", "rtgs"),
    ("Sarie", "rtgs"),
    ("UAEFTS", "rtgs"),
    ("RTGS_CBE", "rtgs"),
    ("EFTS", "rtgs"),
    ("NIFT", "ach"),
    ("1LINK", "ach"),
    ("UAEDDS", "ach"),
])
def test_rail_classification(rail: str, expected_type: str) -> None:
    if expected_type == "instant":
        assert rail in _INSTANT_RAILS
        assert rail not in _RTGS_RAILS
    elif expected_type == "rtgs":
        assert rail in _RTGS_RAILS
        assert rail not in _INSTANT_RAILS
    else:  # ach / other
        assert rail not in _INSTANT_RAILS
        assert rail not in _RTGS_RAILS
