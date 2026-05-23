"""
POS region configuration (DESIGN.md §2, §4.4).

Each RegionConfig defines the rules for generating realistic GMV
for a given market: card mix, working week, MDR, VAT, chargeback windows.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class PosRegionConfig:
    code: str              # ISO 3166-1 alpha-2
    currency: str          # ISO 4217
    card_networks: list[str]     # available networks, in priority order
    domestic_network: str | None # mandatory-routing domestic scheme (mada, PayPak, etc.)
    domestic_routing_pct: float  # % of domestic txns that must go via domestic_network
    default_mdr_bps: int         # basis points (100 bps = 1%)
    vat_on_mdr_pct: float        # 0.0 for PK, 0.15 for KSA, 0.05 for AE, 0.10 for BH
    wht_pct: float               # withholding tax % (PK B2B)
    settlement_days: int         # T+N settlement window
    chargeback_window_days: tuple[int, int]   # (min, max) days post-sale
    dispute_lifecycle_days: tuple[int, int]   # (min, max) dispute resolution
    weekend_days: set[int]       # ISO weekday numbers (Mon=1); Fri=5, Sat=6
    cnp_decline_rate: float      # card-not-present decline rate
    acquirers: list[str]         # mock acquirer names


_REGIONS: dict[str, PosRegionConfig] = {
    "PK": PosRegionConfig(
        code="PK",
        currency="PKR",
        card_networks=["PayPak", "Visa", "Mastercard", "UnionPay"],
        domestic_network="PayPak",
        domestic_routing_pct=0.60,
        default_mdr_bps=150,  # 1.5%
        vat_on_mdr_pct=0.0,
        wht_pct=0.0,
        settlement_days=1,
        chargeback_window_days=(30, 90),
        dispute_lifecycle_days=(14, 45),
        weekend_days={6, 7},  # Sat, Sun
        cnp_decline_rate=0.08,
        acquirers=["mocksim-hbl-pos", "mocksim-ub-pos"],
    ),
    "AE": PosRegionConfig(
        code="AE",
        currency="AED",
        card_networks=["Visa", "Mastercard", "UAE Switch"],
        domestic_network=None,
        domestic_routing_pct=0.0,
        default_mdr_bps=175,
        vat_on_mdr_pct=0.05,
        wht_pct=0.0,
        settlement_days=1,
        chargeback_window_days=(30, 120),
        dispute_lifecycle_days=(14, 45),
        weekend_days={6, 7},  # Sat, Sun (UAE moved to Mon-Fri in 2022)
        cnp_decline_rate=0.06,
        acquirers=["mocksim-network-intl", "mocksim-magnati"],
    ),
    "SA": PosRegionConfig(
        code="SA",
        currency="SAR",
        card_networks=["mada", "Visa", "Mastercard"],
        domestic_network="mada",
        domestic_routing_pct=0.80,  # ≥80% in-KSA domestic txns via mada (SAMA rule)
        default_mdr_bps=175,
        vat_on_mdr_pct=0.15,  # 15% VAT on MDR
        wht_pct=0.0,
        settlement_days=1,
        chargeback_window_days=(30, 120),
        dispute_lifecycle_days=(14, 45),
        weekend_days={5, 6},  # Fri, Sat
        cnp_decline_rate=0.07,
        acquirers=["mocksim-geidea", "mocksim-hyperpay"],
    ),
    "EG": PosRegionConfig(
        code="EG",
        currency="EGP",
        card_networks=["Meeza", "Visa", "Mastercard"],
        domestic_network="Meeza",
        domestic_routing_pct=0.50,
        default_mdr_bps=150,
        vat_on_mdr_pct=0.0,
        wht_pct=0.0,
        settlement_days=2,
        chargeback_window_days=(30, 90),
        dispute_lifecycle_days=(14, 45),
        weekend_days={5, 6},  # Fri, Sat
        cnp_decline_rate=0.10,
        acquirers=["mocksim-paymob", "mocksim-fawry"],
    ),
    "BH": PosRegionConfig(
        code="BH",
        currency="BHD",
        card_networks=["BENEFIT", "Visa", "Mastercard"],
        domestic_network="BENEFIT",
        domestic_routing_pct=0.65,
        default_mdr_bps=150,
        vat_on_mdr_pct=0.10,  # 10% VAT since 2022
        wht_pct=0.0,
        settlement_days=1,
        chargeback_window_days=(30, 120),
        dispute_lifecycle_days=(14, 45),
        weekend_days={5, 6},  # Fri, Sat
        cnp_decline_rate=0.07,
        acquirers=["mocksim-benefit", "mocksim-bani"],
    ),
}


def get_region(code: str) -> PosRegionConfig:
    cfg = _REGIONS.get(code.upper())
    if cfg is None:
        raise ValueError(f"Unsupported POS region: {code}")
    return cfg


def supported_regions() -> list[str]:
    return list(_REGIONS.keys())
