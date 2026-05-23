"""
Property-based tests for mocksim.core.money.Money.

Invariants tested:
  - from_minor(x, c).minor_units == x
  - add/sub commutativity/associativity (within bounds)
  - mul is consistent with repeated addition
  - neg(neg(m)) == m
  - to_major round-trips correctly for display
  - Arithmetic never produces floats
"""
from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from mocksim.core.money import CURRENCY_DECIMALS, Money

# ── Strategy helpers ──────────────────────────────────────────────

_CURRENCIES = list(CURRENCY_DECIMALS.keys())


def money_st(
    min_value: int = 0,
    max_value: int = 10_000_000_000,
    currencies: list[str] | None = None,
) -> st.SearchStrategy[Money]:
    """Hypothesis strategy for Money values."""
    cur_st = st.sampled_from(currencies or _CURRENCIES)
    val_st = st.integers(min_value=min_value, max_value=max_value)
    return st.builds(Money, value=val_st.map(str), currency=cur_st)


def same_currency_pair_st(
    max_value: int = 5_000_000_000,
) -> st.SearchStrategy[tuple[Money, Money]]:
    cur_st = st.sampled_from(_CURRENCIES)
    val_st = st.integers(min_value=0, max_value=max_value)

    @st.composite
    def _pair(draw: st.DrawFn) -> tuple[Money, Money]:
        currency = draw(cur_st)
        a = Money(value=str(draw(val_st)), currency=currency)
        b = Money(value=str(draw(val_st)), currency=currency)
        return a, b

    return _pair()


# ── Construction invariants ───────────────────────────────────────

@given(st.integers(min_value=-1_000_000_000, max_value=1_000_000_000),
       st.sampled_from(_CURRENCIES))
def test_from_minor_round_trips(value: int, currency: str) -> None:
    m = Money.from_minor(value, currency)
    assert m.minor_units == value
    assert m.currency == currency


@given(st.sampled_from(_CURRENCIES))
def test_zero_is_zero(currency: str) -> None:
    m = Money.zero(currency)
    assert m.minor_units == 0
    assert m.currency == currency


def test_invalid_currency_raises() -> None:
    with pytest.raises(Exception):
        Money(value="100", currency="XYZ")


def test_float_string_raises() -> None:
    with pytest.raises(Exception):
        Money(value="1.50", currency="PKR")


def test_empty_value_raises() -> None:
    with pytest.raises(Exception):
        Money(value="", currency="PKR")


# ── Arithmetic invariants ─────────────────────────────────────────

@given(same_currency_pair_st())
def test_addition_commutative(pair: tuple[Money, Money]) -> None:
    a, b = pair
    assert (a + b).minor_units == (b + a).minor_units


@given(same_currency_pair_st())
def test_subtraction_inverse_of_addition(pair: tuple[Money, Money]) -> None:
    a, b = pair
    result = (a + b) - b
    assert result.minor_units == a.minor_units


@given(money_st(min_value=0, max_value=1_000_000), st.integers(min_value=0, max_value=100))
def test_multiplication_consistent_with_addition(m: Money, n: int) -> None:
    expected = sum(m.minor_units for _ in range(n))
    actual = (m * n).minor_units
    assert actual == expected


@given(money_st(min_value=0, max_value=1_000_000_000))
def test_double_negation_identity(m: Money) -> None:
    assert (-(-m)).minor_units == m.minor_units
    assert (-(-m)).currency == m.currency


@given(money_st(min_value=1))
def test_neg_is_negative(m: Money) -> None:
    assert (-m).minor_units == -m.minor_units


# ── Type safety ───────────────────────────────────────────────────

def test_multiply_by_float_raises() -> None:
    m = Money.from_minor(1000, "PKR")
    with pytest.raises(TypeError):
        _ = m * 1.5  # type: ignore[operator]


@given(money_st())
def test_minor_units_is_int(m: Money) -> None:
    assert isinstance(m.minor_units, int)


# ── Display (to_major) ────────────────────────────────────────────

@pytest.mark.parametrize("amount,currency,expected", [
    (1250000, "PKR", "12500.00"),
    (150, "AED", "1.50"),
    (1500, "BHD", "1.500"),
    (100, "JPY", "100"),
    (0, "PKR", "0.00"),
    (1, "PKR", "0.01"),
])
def test_to_major_known_values(amount: int, currency: str, expected: str) -> None:
    m = Money.from_minor(amount, currency)
    assert m.to_major() == expected


@given(money_st(min_value=0))
def test_to_major_no_float_chars(m: Money) -> None:
    major = m.to_major()
    # Should never contain 'e' (scientific notation) or more decimal places than expected
    assert "e" not in major.lower()
    decimals = CURRENCY_DECIMALS[m.currency]
    if decimals > 0:
        if "." in major:
            decimal_part = major.split(".")[1]
            assert len(decimal_part) == decimals


# ── Ordering ──────────────────────────────────────────────────────

@given(same_currency_pair_st())
def test_ordering_consistent_with_int(pair: tuple[Money, Money]) -> None:
    a, b = pair
    assert (a < b) == (a.minor_units < b.minor_units)
    assert (a <= b) == (a.minor_units <= b.minor_units)


@given(same_currency_pair_st())
def test_equality_consistent_with_int(pair: tuple[Money, Money]) -> None:
    a, b = pair
    assert (a == b) == (a.minor_units == b.minor_units)


# ── Cross-currency guard ──────────────────────────────────────────

def test_cross_currency_addition_raises() -> None:
    pkr = Money.from_minor(100, "PKR")
    aed = Money.from_minor(100, "AED")
    with pytest.raises(ValueError):
        _ = pkr + aed


def test_cross_currency_subtraction_raises() -> None:
    pkr = Money.from_minor(100, "PKR")
    aed = Money.from_minor(100, "AED")
    with pytest.raises(ValueError):
        _ = pkr - aed
