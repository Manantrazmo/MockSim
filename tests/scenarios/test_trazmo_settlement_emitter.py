"""
Phase-B contract tests — MockSim emits the exact wire format that
trazmo-platform's POST /api/v1/acquirer/webhooks/settlement expects.

Two layers tested:

  1. Payload builder shape (`_build_trazmo_settlement_payload`) — verifies
     keys, types, and ISO-date format against trazmo's
     AcquirerSettlementPayload pydantic model.
  2. Outbound HMAC + headers (`post_webhook(format='trazmo_settlement')`) —
     verifies X-Acquirer-Signature is plain hex of the raw body (NOT
     timestamped) and X-Tenant-ID is forwarded from extra_headers.

These tests don't need a real trazmo server — respx mocks the receiver.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date

import httpx
import pytest
import respx

from mocksim.core.webhook import post_webhook
from mocksim.pos.settlement import _build_trazmo_settlement_payload


def test_payload_shape_matches_trazmo_contract() -> None:
    payload = _build_trazmo_settlement_payload(
        partner_code="MOCKSIM_PK_POS",
        acquirer_merchant_id="AM_PK_0001",
        settlement_date=date(2026, 5, 23),
        gross_amount_minor=12_500_000,
        currency_code="PKR",
    )

    # Top-level: exactly partner_code + settlements
    assert set(payload.keys()) == {"partner_code", "settlements"}
    assert payload["partner_code"] == "MOCKSIM_PK_POS"
    assert isinstance(payload["settlements"], list)
    assert len(payload["settlements"]) == 1

    line = payload["settlements"][0]
    # Per-line: exactly the four fields trazmo's _SettlementLine pydantic
    # model declares — extras would cause a 422 on trazmo's side.
    assert set(line.keys()) == {
        "acquirer_merchant_id",
        "settlement_date_iso",
        "gross_amount_minor",
        "currency_code",
    }
    assert line["acquirer_merchant_id"] == "AM_PK_0001"
    assert line["settlement_date_iso"] == "2026-05-23"
    assert isinstance(line["gross_amount_minor"], int)
    assert line["gross_amount_minor"] == 12_500_000
    assert line["currency_code"] == "PKR"


def test_payload_uses_iso_date_not_isoformat_with_time() -> None:
    # trazmo's _SettlementLine.settlement_date_iso is a `str` with format
    # YYYY-MM-DD. A datetime.isoformat() would be YYYY-MM-DDTHH:MM:SS and
    # break parsing.
    payload = _build_trazmo_settlement_payload(
        partner_code="P",
        acquirer_merchant_id="M",
        settlement_date=date(2026, 1, 1),
        gross_amount_minor=1,
        currency_code="PKR",
    )
    assert payload["settlements"][0]["settlement_date_iso"] == "2026-01-01"
    assert "T" not in payload["settlements"][0]["settlement_date_iso"]


@pytest.mark.asyncio
@respx.mock
async def test_post_webhook_trazmo_settlement_signs_and_sets_headers() -> None:
    secret = "trazmo-shared-secret-min-16-chars-xx"
    target = "http://trazmo.example.test/api/v1/acquirer/webhooks/settlement"
    tenant_id = "11111111-2222-3333-4444-555555555555"
    payload = _build_trazmo_settlement_payload(
        partner_code="MOCKSIM_PK_POS",
        acquirer_merchant_id="AM_PK_0001",
        settlement_date=date(2026, 5, 23),
        gross_amount_minor=100_000,
        currency_code="PKR",
    )

    route = respx.post(target).mock(return_value=httpx.Response(200, json={"ok": True}))

    success, error = await post_webhook(
        url=target,
        secret=secret,
        event_id="evt_test",
        event_type="pos.batch.settled",
        partition_key="MID_TEST",
        payload=payload,
        format="trazmo_settlement",
        extra_headers={"X-Tenant-ID": tenant_id},
    )

    assert success is True
    assert error is None
    assert route.called

    sent = route.calls.last.request
    body_bytes = bytes(sent.content)

    # ── Header contract ──────────────────────────────────────────────
    assert sent.headers["Content-Type"] == "application/json"
    assert "X-Acquirer-Signature" in sent.headers
    assert "X-Tenant-ID" in sent.headers
    assert sent.headers["X-Tenant-ID"] == tenant_id
    # MockSim-native signature MUST be absent — trazmo doesn't read it and
    # leaking it would confuse a recipient that thinks it's getting a
    # MockSim envelope.
    assert "MockSim-Signature" not in sent.headers

    # ── HMAC verification (plain hex, body-only, no timestamp prefix) ─
    expected = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    assert sent.headers["X-Acquirer-Signature"] == expected

    # ── Round-trip body ──────────────────────────────────────────────
    assert json.loads(body_bytes) == payload


@pytest.mark.asyncio
@respx.mock
async def test_post_webhook_per_event_keeps_native_signature() -> None:
    # Sanity check: switching the trazmo path on must not regress the
    # default per_event delivery.
    secret = "native-secret-16-or-more-chars-xx"
    target = "http://consumer.example.test/webhooks"
    payload = {"event": "ping"}

    route = respx.post(target).mock(return_value=httpx.Response(200))
    success, _ = await post_webhook(
        url=target,
        secret=secret,
        event_id="evt_native",
        event_type="pos.sale.completed",
        partition_key="MID_X",
        payload=payload,
        format="per_event",
    )

    assert success is True
    sent = route.calls.last.request
    assert "MockSim-Signature" in sent.headers
    assert sent.headers["MockSim-Signature"].startswith("t=")
    assert "X-Acquirer-Signature" not in sent.headers
