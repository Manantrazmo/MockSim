"""
Money type — all monetary values are minor-units integer strings.

PKR 12,500.00 → "1250000"  (×100 paisas)
AED 1.50      → "150"      (×100 fils)
BHD 1.500     → "1500"     (×1000 fils — 3-decimal currency)
JPY 100       → "100"      (×1 — 0-decimal currency)

Never use float. Arithmetic uses Python int.
"""
from __future__ import annotations
from pydantic import BaseModel, model_validator

# ISO 4217 decimal places for currencies MockSim handles.
# Source: https://www.six-group.com/en/products-services/financial-information/data-standards.html
CURRENCY_DECIMALS: dict[str, int] = {
    "PKR": 2,   # Pakistani Rupee
    "AED": 2,   # UAE Dirham
    "SAR": 2,   # Saudi Riyal
    "EGP": 2,   # Egyptian Pound
    "BHD": 3,   # Bahraini Dinar — 3-decimal
    "KWD": 3,   # Kuwaiti Dinar — 3-decimal
    "JOD": 3,   # Jordanian Dinar — 3-decimal
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,   # 0-decimal
}

SUPPORTED_CURRENCIES = set(CURRENCY_DECIMALS.keys())


class Money(BaseModel):
    """
    Canonical monetary value. value is always a minor-units integer string.
    No decimal point. Negative values are represented with a leading '-'.
    """
    value: str   # minor-units integer string e.g. "1250000"
    currency: str  # ISO 4217

    @model_validator(mode="after")
    def validate_fields(self) -> "Money":
        if self.currency not in CURRENCY_DECIMALS:
            raise ValueError(f"unsupported currency: {self.currency}")
        raw = self.value.lstrip("-")
        if not raw.isdigit() or not raw:
            raise ValueError(f"value must be a minor-units integer string, got: {self.value!r}")
        return self

    @property
    def minor_units(self) -> int:
        """Return value as Python int (minor units)."""
        return int(self.value)

    @property
    def decimals(self) -> int:
        return CURRENCY_DECIMALS[self.currency]

    def to_major(self) -> str:
        """Human-readable major-units string (display only — never use for arithmetic)."""
        v = self.minor_units
        d = self.decimals
        if d == 0:
            return str(v)
        major = v // (10 ** d)
        minor = abs(v) % (10 ** d)
        return f"{major}.{minor:0{d}d}"

    def __add__(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(value=str(self.minor_units + other.minor_units), currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(value=str(self.minor_units - other.minor_units), currency=self.currency)

    def __mul__(self, factor: int) -> "Money":
        if not isinstance(factor, int):
            raise TypeError("Money can only be multiplied by int, never float")
        return Money(value=str(self.minor_units * factor), currency=self.currency)

    def __neg__(self) -> "Money":
        return Money(value=str(-self.minor_units), currency=self.currency)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.minor_units == other.minor_units and self.currency == other.currency

    def __lt__(self, other: "Money") -> bool:
        self._assert_same_currency(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: "Money") -> bool:
        self._assert_same_currency(other)
        return self.minor_units <= other.minor_units

    def __repr__(self) -> str:
        return f"Money({self.to_major()} {self.currency})"

    def _assert_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")

    @classmethod
    def zero(cls, currency: str) -> "Money":
        return cls(value="0", currency=currency)

    @classmethod
    def from_minor(cls, amount: int, currency: str) -> "Money":
        return cls(value=str(amount), currency=currency)
