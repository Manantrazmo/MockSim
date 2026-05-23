"""
Bank region configuration (DESIGN.md §2, §5).

Defines payment rails, instant payment schemes, direct-debit systems,
and bounce reason provider-code mappings per region.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class BankRegionConfig:
    code: str
    currency: str
    rtgs_rail: str              # Primary RTGS rail name
    instant_rail: str           # Primary instant payment rail
    dd_scheme: str              # Direct debit scheme name
    supported_rails: list[str]  # All rails MockSim can simulate for this region
    iban_length: int
    bic_suffix: str             # 2-char country suffix for BIC
    settlement_cutoff_hour: int # Local hour when T+0 RTGS cutoff occurs
    weekend_days: set[int]      # ISO weekday (1=Mon); 5=Fri, 6=Sat, 7=Sun
    # Provider bounce code → canonical bounce reason
    bounce_code_map: dict[str, str]


# Canonical bounce reasons (§5.4)
_CANONICAL_BOUNCE = {
    "INSUFFICIENT_FUNDS",
    "ACCOUNT_DORMANT",
    "ACCOUNT_CLOSED",
    "MANDATE_EXPIRED",
    "MANDATE_CANCELLED_BY_PAYER",
    "AMOUNT_EXCEEDS_LIMIT",
    "TECHNICAL_FAILURE",
}

_REGIONS: dict[str, BankRegionConfig] = {
    "PK": BankRegionConfig(
        code="PK",
        currency="PKR",
        rtgs_rail="PRISM",
        instant_rail="RAAST",
        dd_scheme="1LINK_DD",
        supported_rails=["RAAST", "PRISM", "1LINK", "NIFT"],
        iban_length=24,
        bic_suffix="PK",
        settlement_cutoff_hour=16,  # 4pm PKT
        weekend_days={6, 7},        # Sat, Sun
        bounce_code_map={
            # 1LINK NIFT codes → canonical
            "D05": "INSUFFICIENT_FUNDS",
            "D06": "ACCOUNT_DORMANT",
            "D07": "ACCOUNT_CLOSED",
            "D08": "MANDATE_EXPIRED",
            "D09": "MANDATE_CANCELLED_BY_PAYER",
            "D10": "AMOUNT_EXCEEDS_LIMIT",
            "D99": "TECHNICAL_FAILURE",
        },
    ),
    "AE": BankRegionConfig(
        code="AE",
        currency="AED",
        rtgs_rail="UAEFTS",
        instant_rail="Aani",
        dd_scheme="UAEDDS",
        supported_rails=["Aani", "UAEFTS"],
        iban_length=23,
        bic_suffix="AE",
        settlement_cutoff_hour=15,
        weekend_days={6, 7},  # Sat, Sun
        bounce_code_map={
            "R01": "INSUFFICIENT_FUNDS",
            "R02": "ACCOUNT_DORMANT",
            "R03": "ACCOUNT_CLOSED",
            "R04": "MANDATE_EXPIRED",
            "R05": "MANDATE_CANCELLED_BY_PAYER",
            "R06": "AMOUNT_EXCEEDS_LIMIT",
            "R99": "TECHNICAL_FAILURE",
        },
    ),
    "SA": BankRegionConfig(
        code="SA",
        currency="SAR",
        rtgs_rail="Sarie",
        instant_rail="IPS",
        dd_scheme="SADAD_DD",
        supported_rails=["IPS", "Sarie"],
        iban_length=24,
        bic_suffix="SA",
        settlement_cutoff_hour=15,
        weekend_days={5, 6},  # Fri, Sat
        bounce_code_map={
            "AC04": "ACCOUNT_CLOSED",
            "AM04": "INSUFFICIENT_FUNDS",
            "AC06": "ACCOUNT_DORMANT",
            "MD01": "MANDATE_CANCELLED_BY_PAYER",
            "MD02": "MANDATE_EXPIRED",
            "AM02": "AMOUNT_EXCEEDS_LIMIT",
            "MS03": "TECHNICAL_FAILURE",
        },
    ),
    "EG": BankRegionConfig(
        code="EG",
        currency="EGP",
        rtgs_rail="RTGS_CBE",
        instant_rail="InstaPay",
        dd_scheme="CBE_DD",
        supported_rails=["InstaPay", "RTGS_CBE"],
        iban_length=29,
        bic_suffix="EG",
        settlement_cutoff_hour=15,
        weekend_days={5, 6},  # Fri, Sat
        bounce_code_map={
            "01": "INSUFFICIENT_FUNDS",
            "02": "ACCOUNT_DORMANT",
            "03": "ACCOUNT_CLOSED",
            "04": "MANDATE_EXPIRED",
            "05": "MANDATE_CANCELLED_BY_PAYER",
            "06": "AMOUNT_EXCEEDS_LIMIT",
            "99": "TECHNICAL_FAILURE",
        },
    ),
    "BH": BankRegionConfig(
        code="BH",
        currency="BHD",
        rtgs_rail="EFTS",
        instant_rail="BENEFIT_Pay",
        dd_scheme="BENEFIT_DD",
        supported_rails=["BENEFIT_Pay", "EFTS", "FAWRI+"],
        iban_length=22,
        bic_suffix="BH",
        settlement_cutoff_hour=15,
        weekend_days={5, 6},  # Fri, Sat
        bounce_code_map={
            "B01": "INSUFFICIENT_FUNDS",
            "B02": "ACCOUNT_DORMANT",
            "B03": "ACCOUNT_CLOSED",
            "B04": "MANDATE_EXPIRED",
            "B05": "MANDATE_CANCELLED_BY_PAYER",
            "B06": "AMOUNT_EXCEEDS_LIMIT",
            "B99": "TECHNICAL_FAILURE",
        },
    ),
}


def get_region(code: str) -> BankRegionConfig:
    cfg = _REGIONS.get(code.upper())
    if cfg is None:
        raise ValueError(f"Unsupported bank region: {code}")
    return cfg


def normalize_bounce_reason(region_code: str, provider_code: str) -> dict[str, str]:
    """
    Map a provider-native bounce code to canonical reason + carry native code.
    Returns {"bounce_reason": "INSUFFICIENT_FUNDS", "provider_code": "D05", ...}
    """
    cfg = get_region(region_code)
    canonical = cfg.bounce_code_map.get(provider_code, "TECHNICAL_FAILURE")
    return {
        "bounce_reason": canonical,
        "provider_code": provider_code,
        "provider_message": _bounce_message(canonical),
    }


def _bounce_message(canonical: str) -> str:
    return {
        "INSUFFICIENT_FUNDS": "Account balance below required amount",
        "ACCOUNT_DORMANT": "Account is dormant",
        "ACCOUNT_CLOSED": "Account is closed",
        "MANDATE_EXPIRED": "Mandate has expired",
        "MANDATE_CANCELLED_BY_PAYER": "Mandate was cancelled by payer",
        "AMOUNT_EXCEEDS_LIMIT": "Amount exceeds mandate or account limit",
        "TECHNICAL_FAILURE": "Technical failure at destination bank",
    }.get(canonical, "Unknown failure")
