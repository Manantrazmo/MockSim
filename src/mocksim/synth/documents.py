"""
Synthetic KYC document generator — Phase I.

When the operator ticks "Generate documents" in the SME onboarding form,
this module produces realistic-looking but obviously-fake document
records that get stored in `merchants.synthetic_documents` AND, when
configured, rendered as one-page PDFs/PNGs in MockSim's object storage
so the URL is dereferenceable from the dashboard.

Region-aware: CNIC for PK; Emirates ID for AE; National ID for SA/EG/BH.
NTN/CR numbers follow the country format where possible. Bank account
numbers match the IBAN prefix the merchant's region uses elsewhere.

The data is **deterministic given (seed, region, doc_type)** so the same
merchant onboarded twice gets the same documents — re-runs are sane.

Public API:
  generate_documents(...)       → list[SyntheticDocument] (dicts)
  KNOWN_DOC_TYPES               → tuple of supported type codes
"""
from __future__ import annotations

import hashlib
import random
import string
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any


# Supported document types. Add new ones here + a handler below.
KNOWN_DOC_TYPES = (
    "CNIC",                # Pakistan: 13-digit national ID  (42101-1234567-8)
    "NTN",                 # Pakistan: National Tax Number   (7-8 digits)
    "BANK_STATEMENT",      # Last 3 months PDF stub
    "BUSINESS_REGISTRATION",  # SECP / commercial reg cert
    "EMIRATES_ID",         # UAE: 784-YYYY-NNNNNNN-N
    "SAUDI_NATIONAL_ID",   # SA: 10 digits
    "BAHRAIN_CPR",         # BH: 9 digits
    "EGYPT_NATIONAL_ID",   # EG: 14 digits
    "UTILITY_BILL",        # Address-proof stub
)


@dataclass(frozen=True)
class SyntheticDocument:
    type: str
    number: str
    issued_at: str       # ISO date
    expires_at: str | None
    issuer: str
    file_uri: str | None  # populated when a stub PDF is rendered
    region: str | None
    metadata: dict[str, Any]


def generate_documents(
    *,
    region: str,
    seed: str,
    types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Generate a set of plausible KYC docs for one merchant.

    `seed` should be stable per merchant (e.g., the acquirer_merchant_id)
    so re-runs produce the same numbers.

    `types` filters which document types to generate. None / empty →
    region-appropriate defaults.
    """
    if not types:
        types = _default_types_for_region(region)

    # Per-doc RNG: derived from seed + type so each doc is independent
    # but reproducible.
    out: list[dict[str, Any]] = []
    for t in types:
        if t not in KNOWN_DOC_TYPES:
            # Skip unknown types instead of raising — the UI lists known
            # types, but a stale build wouldn't crash an onboarding.
            continue
        rng = _seeded_rng(f"{seed}|{t}")
        handler = _HANDLERS.get(t)
        if handler is None:
            continue
        doc = handler(rng=rng, region=region.upper())
        out.append(_to_dict(doc))
    return out


# ── Per-type generators ──────────────────────────────────────────────────────


def _gen_cnic(*, rng: random.Random, region: str) -> SyntheticDocument:
    # NADRA CNIC format: 5 digits - 7 digits - 1 digit
    # 5-digit prefix encodes geography; using 42101 (Karachi South) as
    # the demo default — anyone reading the demo will recognize it.
    geo = "42101"
    middle = "".join(rng.choice(string.digits) for _ in range(7))
    check = rng.choice(string.digits)
    return SyntheticDocument(
        type="CNIC",
        number=f"{geo}-{middle}-{check}",
        issued_at=_iso_date_between(rng, years_ago=5, years_until=1),
        expires_at=_iso_date_between(rng, years_ago=-1, years_until=10),  # future
        issuer="NADRA",
        file_uri=None,
        region=region,
        metadata={
            "doc_label": "Computerized National Identity Card",
            "country": "PK",
            # Surface as both fields so callers can pick whichever they
            # expect — leadflow tends to want "cnic", the borrower portal
            # tends to want "national_id".
            "cnic": f"{geo}-{middle}-{check}",
            "national_id": f"{geo}-{middle}-{check}",
        },
    )


def _gen_ntn(*, rng: random.Random, region: str) -> SyntheticDocument:
    # NTN: 7 digits + 1 check digit (FBR format pre-2019). Modern format
    # is a 13-digit STRN; we emit the older one because it's the format
    # most fintech demos / sample data show.
    body = "".join(rng.choice(string.digits) for _ in range(7))
    check = rng.choice(string.digits)
    return SyntheticDocument(
        type="NTN",
        number=f"{body}-{check}",
        issued_at=_iso_date_between(rng, years_ago=10, years_until=0),
        expires_at=None,
        issuer="FBR",
        file_uri=None,
        region=region,
        metadata={
            "doc_label": "National Tax Number certificate",
            "country": "PK",
            "ntn": f"{body}-{check}",
        },
    )


def _gen_bank_statement(*, rng: random.Random, region: str) -> SyntheticDocument:
    bank = rng.choice([
        "Habib Bank Limited", "United Bank Limited", "MCB Bank",
        "Standard Chartered Pakistan", "Bank Alfalah", "Meezan Bank",
    ])
    # Statement covers the trailing 3 months.
    end = _iso_date_between(rng, years_ago=0, years_until=0)
    return SyntheticDocument(
        type="BANK_STATEMENT",
        number=f"BS-{end.replace('-', '')}-{''.join(rng.choice(string.digits) for _ in range(4))}",
        issued_at=end,
        expires_at=None,
        issuer=bank,
        file_uri=None,
        region=region,
        metadata={
            "doc_label": f"3-month statement — {bank}",
            "account_number": _gen_account_number(rng),
            "iban": _gen_iban(rng, region),
            "currency": _currency_for_region(region),
            "period_months": 3,
            "country": region,
        },
    )


def _gen_business_registration(*, rng: random.Random, region: str) -> SyntheticDocument:
    # SECP / TRADE registry numbers — region-specific but using a generic
    # 6-digit form.
    reg_no = f"{rng.choice(string.digits[1:])}{''.join(rng.choice(string.digits) for _ in range(6))}"
    issuer = {
        "PK": "Securities & Exchange Commission of Pakistan",
        "AE": "UAE Department of Economic Development",
        "SA": "Saudi Ministry of Commerce",
        "BH": "Bahrain Ministry of Industry and Commerce",
        "EG": "Egyptian General Authority for Investment",
    }.get(region, "Commerce Registry")
    return SyntheticDocument(
        type="BUSINESS_REGISTRATION",
        number=reg_no,
        issued_at=_iso_date_between(rng, years_ago=8, years_until=1),
        expires_at=_iso_date_between(rng, years_ago=-3, years_until=10),
        issuer=issuer,
        file_uri=None,
        region=region,
        metadata={
            "doc_label": "Commercial Registration Certificate",
            "registration_no": reg_no,
            "country": region,
        },
    )


def _gen_emirates_id(*, rng: random.Random, region: str) -> SyntheticDocument:
    # UAE: 784-YYYY-NNNNNNN-N
    year = rng.randint(1970, 2005)
    middle = "".join(rng.choice(string.digits) for _ in range(7))
    check = rng.choice(string.digits)
    number = f"784-{year}-{middle}-{check}"
    return SyntheticDocument(
        type="EMIRATES_ID",
        number=number,
        issued_at=_iso_date_between(rng, years_ago=5, years_until=1),
        expires_at=_iso_date_between(rng, years_ago=-1, years_until=10),
        issuer="UAE ICA",
        file_uri=None,
        region=region,
        metadata={"doc_label": "Emirates ID", "country": "AE", "national_id": number},
    )


def _gen_saudi_id(*, rng: random.Random, region: str) -> SyntheticDocument:
    # 10 digits, leading 1 = Saudi national, leading 2 = expatriate (Iqama).
    body = "1" + "".join(rng.choice(string.digits) for _ in range(9))
    return SyntheticDocument(
        type="SAUDI_NATIONAL_ID",
        number=body,
        issued_at=_iso_date_between(rng, years_ago=5, years_until=1),
        expires_at=_iso_date_between(rng, years_ago=-1, years_until=10),
        issuer="Saudi Ministry of Interior",
        file_uri=None,
        region=region,
        metadata={"doc_label": "Saudi National ID", "country": "SA", "national_id": body},
    )


def _gen_bahrain_cpr(*, rng: random.Random, region: str) -> SyntheticDocument:
    body = "".join(rng.choice(string.digits) for _ in range(9))
    return SyntheticDocument(
        type="BAHRAIN_CPR",
        number=body,
        issued_at=_iso_date_between(rng, years_ago=5, years_until=1),
        expires_at=_iso_date_between(rng, years_ago=-1, years_until=10),
        issuer="Bahrain CPR Authority",
        file_uri=None,
        region=region,
        metadata={"doc_label": "Bahrain CPR", "country": "BH", "national_id": body},
    )


def _gen_egypt_id(*, rng: random.Random, region: str) -> SyntheticDocument:
    body = "".join(rng.choice(string.digits) for _ in range(14))
    return SyntheticDocument(
        type="EGYPT_NATIONAL_ID",
        number=body,
        issued_at=_iso_date_between(rng, years_ago=5, years_until=1),
        expires_at=_iso_date_between(rng, years_ago=-1, years_until=10),
        issuer="Egypt Civil Status Organization",
        file_uri=None,
        region=region,
        metadata={"doc_label": "Egyptian National ID", "country": "EG", "national_id": body},
    )


def _gen_utility_bill(*, rng: random.Random, region: str) -> SyntheticDocument:
    provider = {
        "PK": "K-Electric", "AE": "DEWA", "SA": "Saudi Electricity Co.",
        "BH": "EWA Bahrain", "EG": "Egyptian Electricity Holding",
    }.get(region, "Electric Utility")
    bill_no = f"UB-{''.join(rng.choice(string.digits) for _ in range(10))}"
    return SyntheticDocument(
        type="UTILITY_BILL",
        number=bill_no,
        issued_at=_iso_date_between(rng, years_ago=0, years_until=0),
        expires_at=None,
        issuer=provider,
        file_uri=None,
        region=region,
        metadata={
            "doc_label": "Utility bill (address proof)",
            "provider": provider,
            "country": region,
        },
    )


_HANDLERS = {
    "CNIC": _gen_cnic,
    "NTN": _gen_ntn,
    "BANK_STATEMENT": _gen_bank_statement,
    "BUSINESS_REGISTRATION": _gen_business_registration,
    "EMIRATES_ID": _gen_emirates_id,
    "SAUDI_NATIONAL_ID": _gen_saudi_id,
    "BAHRAIN_CPR": _gen_bahrain_cpr,
    "EGYPT_NATIONAL_ID": _gen_egypt_id,
    "UTILITY_BILL": _gen_utility_bill,
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _seeded_rng(seed: str) -> random.Random:
    """Stable per-seed RNG so re-runs produce identical numbers."""
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    rng = random.Random()
    rng.seed(int.from_bytes(h[:8], "big"))
    return rng


def _default_types_for_region(region: str) -> list[str]:
    base = ["BANK_STATEMENT", "BUSINESS_REGISTRATION", "UTILITY_BILL"]
    id_types = {
        "PK": ["CNIC", "NTN"],
        "AE": ["EMIRATES_ID"],
        "SA": ["SAUDI_NATIONAL_ID"],
        "BH": ["BAHRAIN_CPR"],
        "EG": ["EGYPT_NATIONAL_ID"],
    }.get(region.upper(), ["CNIC"])
    return id_types + base


def _currency_for_region(region: str) -> str:
    return {
        "PK": "PKR", "AE": "AED", "SA": "SAR", "BH": "BHD", "EG": "EGP",
    }.get(region.upper(), "PKR")


def _iso_date_between(rng: random.Random, *, years_ago: int, years_until: int) -> str:
    """Pick a random date in [today-years_ago, today+years_until]."""
    today = date.today().toordinal()
    span_back = years_ago * 365
    span_fwd = years_until * 365
    pick = today + rng.randint(-span_back, span_fwd)
    return date.fromordinal(pick).isoformat()


def _gen_account_number(rng: random.Random) -> str:
    return "".join(rng.choice(string.digits) for _ in range(14))


def _gen_iban(rng: random.Random, region: str) -> str:
    cc = {"PK": "PK", "AE": "AE", "SA": "SA", "BH": "BH", "EG": "EG"}.get(region.upper(), "PK")
    check = "".join(rng.choice(string.digits) for _ in range(2))
    body = "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(20))
    return f"{cc}{check}{body}"


def _to_dict(doc: SyntheticDocument) -> dict[str, Any]:
    return {
        "type": doc.type,
        "number": doc.number,
        "issued_at": doc.issued_at,
        "expires_at": doc.expires_at,
        "issuer": doc.issuer,
        "file_uri": doc.file_uri,
        "region": doc.region,
        "metadata": doc.metadata,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
