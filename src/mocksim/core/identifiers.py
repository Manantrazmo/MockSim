"""
Identifier generators for all mock-issued IDs.

All generators are deterministic when given a seed — seeded generators
are used in the GMV generator for reproducibility. Unseeded generators
use cryptographically random sources.
"""
from __future__ import annotations
import hashlib
import random
import re
import uuid
from datetime import date

from ulid import ULID

# ── IBAN ─────────────────────────────────────────────────────────
# Country → (total_length, bank_code_length, bban_length)
_IBAN_SPEC: dict[str, tuple[int, int]] = {
    "PK": (24, 20),  # PK + 2 check + 4 bank + 16 account
    "AE": (23, 19),  # AE + 2 check + 3 bank + 16 account
    "SA": (24, 20),  # SA + 2 check + 2 bank + 18 account
    "EG": (29, 25),  # EG + 2 check + 4 bank + 21 account
    "BH": (22, 18),  # BH + 2 check + 4 alpha-bank + 14 account
}

# Fixed bank codes per region (mock bank)
_MOCK_BANK_CODES: dict[str, str] = {
    "PK": "SCBL",   # Standard Chartered Pakistan
    "AE": "033",    # ADCB
    "SA": "10",     # Al Rajhi
    "EG": "0019",   # CIB Egypt
    "BH": "ITHMR",  # Ithmaar Bank (alpha for BH)
}


def _iban_checksum(country: str, bban: str) -> str:
    """Compute the 2-digit IBAN check digits for country + bban."""
    rearranged = bban + country + "00"
    numeric = ""
    for ch in rearranged.upper():
        if ch.isdigit():
            numeric += ch
        elif ch.isalpha():
            numeric += str(ord(ch) - ord("A") + 10)
    check = 98 - (int(numeric) % 97)
    return f"{check:02d}"


def gen_iban(region: str, seed: str | None = None) -> str:
    """Generate a region-correct, mod-97-valid IBAN."""
    if region not in _IBAN_SPEC:
        raise ValueError(f"Unsupported region for IBAN: {region}")
    total_len, bban_len = _IBAN_SPEC[region]
    bank_code = _MOCK_BANK_CODES[region]

    rng = random.Random(seed) if seed else random.SystemRandom()
    account_len = bban_len - len(bank_code)
    account_num = "".join(str(rng.randint(0, 9)) for _ in range(account_len))
    bban = bank_code + account_num

    check = _iban_checksum(region, bban)
    iban = region + check + bban
    assert len(iban) == total_len, f"IBAN length mismatch: {len(iban)} != {total_len}"
    return iban


def validate_iban(iban: str) -> bool:
    """Return True if IBAN passes mod-97 checksum."""
    if len(iban) < 4:
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = ""
    for ch in rearranged.upper():
        if ch.isdigit():
            numeric += ch
        elif ch.isalpha():
            numeric += str(ord(ch) - ord("A") + 10)
        else:
            return False
    try:
        return int(numeric) % 97 == 1
    except ValueError:
        return False


# ── BIC ──────────────────────────────────────────────────────────
_MOCK_BICS: dict[str, str] = {
    "PK": "SCBLPKKA",
    "AE": "ADCBAEAA",
    "SA": "RJHISARI",
    "EG": "CIBEEGCX",
    "BH": "ITHMBHBM",
}


def gen_bic(region: str) -> str:
    return _MOCK_BICS.get(region, "MOCKSIMXX")


# ── Transaction identifiers ───────────────────────────────────────
def new_ulid() -> str:
    """New ULID string — used as event_id, txn_id, etc."""
    return str(ULID())


def new_uuid() -> str:
    return str(uuid.uuid4())


def gen_rrn(sim_date: date, rng: random.Random | None = None) -> str:
    """
    12-digit Retrieval Reference Number (ISO 8583 F37).
    Format: YDDDHHMMSSNN where Y=year-digit, DDD=day-of-year, rest=random.
    """
    r = rng or random.SystemRandom()
    year_digit = str(sim_date.year)[-1]
    day_of_year = f"{sim_date.timetuple().tm_yday:03d}"
    seq = "".join(str(r.randint(0, 9)) for _ in range(6))
    rrn = year_digit + day_of_year + seq
    return rrn[:12].ljust(12, "0")


def gen_stan(terminal_id: str, sim_date: date, rng: random.Random | None = None) -> str:
    """6-digit System Trace Audit Number (ISO 8583 F11). Recycles per terminal per day."""
    r = rng or random.SystemRandom()
    return f"{r.randint(0, 999999):06d}"


def gen_auth_code(rng: random.Random | None = None) -> str:
    """6-char uppercase alphanumeric authorization code (ISO 8583 F38)."""
    r = rng or random.SystemRandom()
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(r.choice(chars) for _ in range(6))


def gen_arn(acquirer_id: str, sim_date: date, rng: random.Random | None = None) -> str:
    """23-digit Acquirer Reference Number."""
    r = rng or random.SystemRandom()
    prefix = "74999"  # mock acquirer prefix
    date_part = sim_date.strftime("%y%j")  # YYDDD = 5 chars
    seq = "".join(str(r.randint(0, 9)) for _ in range(13))
    return (prefix + date_part + seq)[:23]


def gen_mandate_id(region: str) -> str:
    """Mandate ID: MND-{REGION}-{ULID}"""
    return f"MND-{region.upper()}-{new_ulid()}"


def gen_van(pool_iban: str, merchant_id: str) -> str:
    """
    Virtual Account Number: same length as pool IBAN, deterministic per merchant.
    Last N chars replaced with a hash of merchant_id.
    """
    suffix_len = 8
    h = hashlib.sha256(merchant_id.encode()).hexdigest()[:suffix_len].upper()
    # Replace only numeric suffix to keep IBAN-like format; no checksum required for VANs
    return pool_iban[:-suffix_len] + h


def gen_batch_id(sim_date: date, seq: int = 1) -> str:
    """Settlement batch ID: BATCH_YYYYMMDD_NNN"""
    return f"BATCH_{sim_date.strftime('%Y%m%d')}_{seq:03d}"


def rng_for(mock_tenant_id: str, merchant_id: str, sim_date: date) -> random.Random:
    """
    Deterministic RNG seeded per (tenant, merchant, date).
    Same inputs → same GMV every run. Tests rely on this.
    """
    seed_str = f"{mock_tenant_id}:{merchant_id}:{sim_date.isoformat()}"
    seed = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed)
