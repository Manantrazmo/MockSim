"""
Unit tests for the POS GMV generator.

All tests here are pure-function (no DB required) — they test the
statistical helpers, card routing, fee arithmetic, and schedule logic.

DB integration tests live in tests/scenarios/test_gmv_integration.py
(require PostgreSQL; skipped in CI without TEST_DATABASE_URL env var).
"""
from __future__ import annotations

import math
import random
import statistics
from datetime import date, timedelta

import pytest

from mocksim.pos.generator import (
    _calc_fees,
    _expected_settlement_date,
    _lognormal_amount,
    _pick_card_network,
    _poisson_approx,
    _spread_timestamps,
)
from mocksim.pos.regions import get_region


# ── Poisson approximation ─────────────────────────────────────────

class TestPoissonApprox:
    def test_zero_lambda_returns_zero(self) -> None:
        rng = random.Random(1)
        assert _poisson_approx(rng, 0.0) == 0

    def test_negative_lambda_returns_zero(self) -> None:
        rng = random.Random(1)
        assert _poisson_approx(rng, -5.0) == 0

    def test_always_non_negative(self) -> None:
        rng = random.Random(42)
        for lam in [0.5, 1, 5, 10, 30, 50, 100, 500]:
            for _ in range(50):
                assert _poisson_approx(rng, lam) >= 0

    def test_mean_close_to_lambda_small(self) -> None:
        """For small λ (Knuth path), sample mean ≈ λ."""
        rng = random.Random(7)
        lam = 5.0
        samples = [_poisson_approx(rng, lam) for _ in range(2000)]
        mean = statistics.mean(samples)
        assert abs(mean - lam) < 0.5, f"Mean {mean:.2f} too far from λ={lam}"

    def test_mean_close_to_lambda_large(self) -> None:
        """For large λ (normal approx path), sample mean ≈ λ."""
        rng = random.Random(7)
        lam = 100.0
        samples = [_poisson_approx(rng, lam) for _ in range(2000)]
        mean = statistics.mean(samples)
        assert abs(mean - lam) < 2.0, f"Mean {mean:.2f} too far from λ={lam}"

    def test_deterministic_for_same_rng_state(self) -> None:
        rng1 = random.Random(99)
        rng2 = random.Random(99)
        assert _poisson_approx(rng1, 25.0) == _poisson_approx(rng2, 25.0)


# ── Lognormal amount generation ───────────────────────────────────

class TestLognormalAmount:
    def test_always_positive(self) -> None:
        rng = random.Random(1)
        for _ in range(500):
            assert _lognormal_amount(rng, 50000) >= 1

    def test_capped_at_10x_median(self) -> None:
        rng = random.Random(2)
        median = 50000
        for _ in range(1000):
            amt = _lognormal_amount(rng, median)
            assert amt <= median * 10, f"{amt} > 10× {median}"

    def test_zero_median_returns_one(self) -> None:
        rng = random.Random(3)
        assert _lognormal_amount(rng, 0) == 1

    def test_negative_median_returns_one(self) -> None:
        rng = random.Random(3)
        assert _lognormal_amount(rng, -100) == 1

    def test_median_is_roughly_central(self) -> None:
        """~50% of samples should be ≥ median (lognormal property)."""
        rng = random.Random(5)
        median = 100000
        samples = [_lognormal_amount(rng, median) for _ in range(1000)]
        above = sum(1 for s in samples if s >= median)
        # Should be between 35% and 65%
        assert 350 <= above <= 650, f"{above}/1000 samples above median"


# ── Card network selection ────────────────────────────────────────

class TestPickCardNetwork:
    @pytest.mark.parametrize("region,domestic,expected_min_pct", [
        ("PK", "PayPak", 0.50),
        ("SA", "mada", 0.70),
        ("BH", "BENEFIT", 0.55),
    ])
    def test_domestic_routing_met(
        self, region: str, domestic: str, expected_min_pct: float
    ) -> None:
        """Domestic network should be chosen at approximately domestic_routing_pct."""
        cfg = get_region(region)
        rng = random.Random(42)
        n = 2000
        domestic_count = sum(
            1 for _ in range(n)
            if _pick_card_network(rng, cfg) == domestic
        )
        actual_pct = domestic_count / n
        # Allow ±10% tolerance for statistical noise
        assert abs(actual_pct - cfg.domestic_routing_pct) < 0.10, (
            f"{region}: expected ~{cfg.domestic_routing_pct:.0%} domestic, "
            f"got {actual_pct:.0%}"
        )

    def test_ae_no_domestic_network(self) -> None:
        """AE has no mandatory domestic scheme."""
        cfg = get_region("AE")
        assert cfg.domestic_network is None
        rng = random.Random(1)
        networks = {_pick_card_network(rng, cfg) for _ in range(100)}
        assert networks.issubset(set(cfg.card_networks))

    def test_result_always_in_card_networks(self) -> None:
        for region in ["PK", "AE", "SA", "EG", "BH"]:
            cfg = get_region(region)
            rng = random.Random(region.__hash__())
            for _ in range(100):
                net = _pick_card_network(rng, cfg)
                assert net in cfg.card_networks, f"{region}: {net} not in {cfg.card_networks}"


# ── Fee arithmetic ────────────────────────────────────────────────

class TestCalcFees:
    def test_net_equals_amount_minus_deductions(self) -> None:
        for region in ["PK", "AE", "SA", "EG", "BH"]:
            cfg = get_region(region)
            for amount in [1, 100, 1000, 50000, 1_000_000]:
                mdr, vat, wht, net = _calc_fees(amount, cfg)
                assert net == amount - mdr - vat - wht, (
                    f"{region} amount={amount}: "
                    f"{amount} - {mdr} - {vat} - {wht} = {amount-mdr-vat-wht} ≠ {net}"
                )

    def test_all_fees_non_negative(self) -> None:
        for region in ["PK", "AE", "SA", "EG", "BH"]:
            cfg = get_region(region)
            mdr, vat, wht, net = _calc_fees(10000, cfg)
            assert mdr >= 0
            assert vat >= 0
            assert wht >= 0
            assert net > 0

    def test_pk_no_vat_no_wht(self) -> None:
        """PK has 0% VAT and 0% WHT."""
        cfg = get_region("PK")
        assert cfg.vat_on_mdr_pct == 0.0
        assert cfg.wht_pct == 0.0
        mdr, vat, wht, net = _calc_fees(10000, cfg)
        assert vat == 0
        assert wht == 0
        assert net == 10000 - mdr

    def test_sa_15pct_vat_on_mdr(self) -> None:
        """SA has 15% VAT on MDR."""
        cfg = get_region("SA")
        assert cfg.vat_on_mdr_pct == 0.15
        mdr, vat, wht, net = _calc_fees(10000, cfg)
        assert vat == int(mdr * 0.15)

    def test_ae_5pct_vat_on_mdr(self) -> None:
        cfg = get_region("AE")
        assert cfg.vat_on_mdr_pct == 0.05
        mdr, vat, wht, net = _calc_fees(10000, cfg)
        assert vat == int(mdr * 0.05)

    def test_mdr_uses_bps(self) -> None:
        """MDR = amount × default_mdr_bps / 10000."""
        cfg = get_region("PK")  # 150 bps
        mdr, _, _, _ = _calc_fees(10000, cfg)
        assert mdr == int(10000 * 150 / 10_000)  # = 150


# ── Settlement date calculation ───────────────────────────────────

class TestExpectedSettlementDate:
    def test_pk_t1_from_monday(self) -> None:
        """PK T+1 from Monday → Tuesday."""
        cfg = get_region("PK")
        monday = date(2026, 1, 5)
        assert monday.isoweekday() == 1  # Monday
        settle = _expected_settlement_date(monday, cfg.settlement_days, cfg.weekend_days)
        assert settle == date(2026, 1, 6)  # Tuesday

    def test_pk_t1_from_friday(self) -> None:
        """PK T+1 from Friday → Monday (skip Sat, Sun)."""
        cfg = get_region("PK")
        friday = date(2026, 1, 9)
        assert friday.isoweekday() == 5  # Friday
        settle = _expected_settlement_date(friday, cfg.settlement_days, cfg.weekend_days)
        assert settle == date(2026, 1, 12)  # Monday

    def test_sa_t1_from_thursday(self) -> None:
        """SA T+1, weekends Fri+Sat. Thursday → next Monday."""
        cfg = get_region("SA")  # weekend_days = {5, 6}
        thursday = date(2026, 1, 8)
        assert thursday.isoweekday() == 4  # Thursday
        settle = _expected_settlement_date(thursday, cfg.settlement_days, cfg.weekend_days)
        # +1 = Fri (weekend) → +2 = Sat (weekend) → +3 = Sun → Sunday is not in {5,6}
        assert settle == date(2026, 1, 11)  # Sunday

    def test_eg_t2_from_thursday(self) -> None:
        """EG T+2, weekends Fri+Sat. Thursday → 2 working days forward."""
        cfg = get_region("EG")  # settlement_days=2, weekend_days={5,6}
        thursday = date(2026, 1, 8)
        settle = _expected_settlement_date(thursday, cfg.settlement_days, cfg.weekend_days)
        # Skip Fri(5), Skip Sat(6): Thu→Fri skip→Sat skip→Sun(+1)→Mon(+2)
        assert settle == date(2026, 1, 12)  # Monday

    def test_settlement_days_zero_returns_same_day(self) -> None:
        cfg = get_region("PK")
        d = date(2026, 1, 5)
        settle = _expected_settlement_date(d, 0, cfg.weekend_days)
        assert settle == d


# ── Timestamp spreading ───────────────────────────────────────────

class TestSpreadTimestamps:
    def test_correct_count(self) -> None:
        rng = random.Random(1)
        ts = _spread_timestamps(rng, date(2026, 1, 5), 50)
        assert len(ts) == 50

    def test_all_on_correct_date(self) -> None:
        rng = random.Random(2)
        d = date(2026, 1, 5)
        ts = _spread_timestamps(rng, d, 30)
        for t in ts:
            assert t.date() == d

    def test_sorted_ascending(self) -> None:
        rng = random.Random(3)
        ts = _spread_timestamps(rng, date(2026, 1, 5), 100)
        assert ts == sorted(ts)

    def test_within_business_hours(self) -> None:
        """All timestamps should be between 08:00 and 22:00 UTC."""
        rng = random.Random(4)
        ts = _spread_timestamps(rng, date(2026, 1, 5), 200)
        for t in ts:
            assert 8 <= t.hour <= 21, f"Timestamp {t} outside business hours"

    def test_zero_count_returns_empty(self) -> None:
        rng = random.Random(5)
        assert _spread_timestamps(rng, date(2026, 1, 5), 0) == []

    def test_deterministic_with_same_seed(self) -> None:
        d = date(2026, 3, 15)
        rng1 = random.Random(999)
        rng2 = random.Random(999)
        assert _spread_timestamps(rng1, d, 20) == _spread_timestamps(rng2, d, 20)
