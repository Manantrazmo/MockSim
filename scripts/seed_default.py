"""
seed_default.py — Bootstrap MockSim with a sensible default world.

After `docker compose up`, run this once to populate MockSim with:

  • A default tenant + API key (so the dashboard has someone to authenticate as)
  • One merchant per enabled region (PK/AE/SA/EG/BH) with reasonable MCC + daily volume
  • A pool account and a couple merchant virtual accounts (bank side)
  • Webhook subscriptions for the POS + Bank surfaces, pointing at trazmo-platform
  • Scenario engine enabled (so failure injection works)

The script is **idempotent** — re-running detects existing rows (by name)
and skips creation. Use `--reset` to wipe the default tenant first.

Trazmo-platform integration (read scripts/README.md for full context):
  • Default webhook target:  http://host.docker.internal:8000  (override with --trazmo)
  • trazmo-platform's Phase-2 settlement receiver lives at
    POST /api/v1/acquirer/webhooks/settlement and expects an HMAC-SHA256 signature
    in `X-Acquirer-Signature` plus `X-Tenant-ID`. The receiver is gated behind
    ACQUIRER_WEBHOOK_ENABLED=true on the trazmo side.
  • MockSim's outbound webhook envelope does not yet match trazmo's settlement
    schema (partner_code + settlements[]) — see scripts/README.md "Known gap".
    Until a translator is added, trazmo will 422 deliveries; the subscription
    still exercises retry/replay flows on the MockSim side.

Usage:
    python scripts/seed_default.py
    python scripts/seed_default.py --mocksim http://localhost:8080
    python scripts/seed_default.py --trazmo  http://localhost:8000
    python scripts/seed_default.py --reset
    python scripts/seed_default.py --no-sample-txn       # skip sample POS sale
    python scripts/seed_default.py --regions PK,AE       # override .env regions

Exits non-zero on hard failures (auth, network), but tolerates "already exists"
responses so the script is safe to run in CI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    sys.stderr.write(
        "httpx not installed. Either run inside the mocksim container:\n"
        "    docker compose exec mocksim python scripts/seed_default.py\n"
        "or install on host:\n"
        "    pip install httpx\n"
    )
    raise SystemExit(2)


# ── Defaults (override via CLI flags or env) ──────────────────────────────────

DEFAULT_MOCKSIM_URL = "http://localhost:8080"
DEFAULT_TRAZMO_URL = "http://host.docker.internal:8000"
DEFAULT_TENANT_NAME = "default-dev-tenant"
DEFAULT_API_KEY = "mocksim-default-tenant-key-do-not-use-in-prod-32c"
DEFAULT_WEBHOOK_SECRET = "default-webhook-secret-rotate-before-shared-use"

# Per-region merchant template. MCCs are standard ISO 18245 codes.
# expected_daily_txns is intentionally modest so the generator stays cheap.
REGION_MERCHANT_TEMPLATES: dict[str, dict[str, Any]] = {
    "PK": {"name": "Karachi Mart (PK)",       "mcc": "5411", "avg_ticket": 1500.0, "daily": 80},
    "AE": {"name": "Dubai Marina Cafe (AE)",  "mcc": "5812", "avg_ticket": 95.0,   "daily": 60},
    "SA": {"name": "Riyadh Electronics (SA)", "mcc": "5732", "avg_ticket": 450.0,  "daily": 40},
    "EG": {"name": "Cairo Pharmacy (EG)",     "mcc": "5912", "avg_ticket": 200.0,  "daily": 50},
    "BH": {"name": "Manama Boutique (BH)",    "mcc": "5651", "avg_ticket": 25.0,   "daily": 30},
}

POS_EVENT_TYPES = ["pos.sale.completed", "pos.refund.completed", "pos.chargeback.opened"]
BANK_EVENT_TYPES = [
    "bank.payment.accepted",
    "bank.payment.completed",
    "bank.payment.failed",
    "bank.instant.credit.received",
]


# ── Result tracking ───────────────────────────────────────────────────────────

@dataclass
class SeedReport:
    tenant_id: str = ""
    api_key: str = ""
    merchants: list[dict[str, str]] = None  # type: ignore[assignment]
    accounts: list[dict[str, str]] = None  # type: ignore[assignment]
    pos_subscription_id: str = ""
    bank_subscription_id: str = ""
    sample_txn_id: str | None = None
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.merchants = []
        self.accounts = []
        self.warnings = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def tenant_headers(api_key: str, idem: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if idem:
        h["Idempotency-Key"] = idem
    return h


def stable_idem(seed: str) -> str:
    """
    Deterministic idempotency key from a string seed. Re-running with the same
    seed returns the cached response — that's how we get idempotent seeding.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mocksim-seed/{seed}"))


def load_dotenv(path: Path) -> dict[str, str]:
    """Tiny .env reader — no python-dotenv dependency on the host."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# ── Steps ─────────────────────────────────────────────────────────────────────

def wait_for_health(client: httpx.Client, url: str, timeout_s: int = 30) -> None:
    """Block until /health returns 200 — handles container slow-starts."""
    deadline = time.monotonic() + timeout_s
    last_err = ""
    while time.monotonic() < deadline:
        try:
            r = client.get(f"{url}/health", timeout=2.0)
            if r.status_code == 200:
                print(f"  ✓ MockSim healthy at {url}  sim_time={r.json()['sim_time']}")
                return
            last_err = f"HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            last_err = str(exc)
        time.sleep(0.5)
    raise SystemExit(f"MockSim never became healthy at {url}/health ({last_err})")


def ensure_tenant(
    client: httpx.Client,
    mocksim_url: str,
    admin_token: str,
    name: str,
    api_key: str,
    report: SeedReport,
    *,
    reset: bool,
) -> None:
    # Reset path: find existing tenant by name and wipe it. The /admin/stats
    # endpoint doesn't expose tenants directly, so we just call /admin/reset
    # with the tenant_id we get *after* creating; "reset" then means "wipe and
    # recreate." See https://… (MockSim admin API).
    create_url = f"{mocksim_url}/api/v1/admin/tenants"
    r = client.post(
        create_url,
        headers=admin_headers(admin_token),
        json={
            "name": name,
            "api_key": api_key,
            "scopes": ["pos.read", "pos.write", "bank.read", "bank.write"],
        },
        timeout=10.0,
    )
    if r.status_code in (200, 201):
        body = r.json()
        report.tenant_id = body["tenant_id"]
        report.api_key = api_key
        existed = body.get("existed", False) or r.status_code == 200
        verb = "exists, reused" if existed else "created"
        print(f"  ✓ Tenant {verb:<14} id={report.tenant_id}  name={name}")
        return

    raise SystemExit(f"Tenant create failed: HTTP {r.status_code} {r.text[:300]}")


def ensure_scenarios_enabled(client: httpx.Client, mocksim_url: str, admin_token: str) -> None:
    r = client.post(
        f"{mocksim_url}/api/v1/admin/scenarios/enable",
        headers=admin_headers(admin_token),
        timeout=5.0,
    )
    if r.status_code == 200 and r.json().get("enabled"):
        print("  ✓ Scenario engine enabled")
    else:
        print(f"  ! Scenario enable returned HTTP {r.status_code} — non-fatal")


def create_merchant(
    client: httpx.Client,
    mocksim_url: str,
    api_key: str,
    region: str,
    report: SeedReport,
) -> None:
    tmpl = REGION_MERCHANT_TEMPLATES.get(region)
    if tmpl is None:
        report.warnings.append(f"No template for region {region!r} — skipped")
        return

    body = {
        "name": tmpl["name"],
        "region": region,
        "mcc": tmpl["mcc"],
        "expected_daily_txns": tmpl["daily"],
        "avg_ticket_major_units": tmpl["avg_ticket"],
        "risk_tier": "standard",
    }
    idem = stable_idem(f"merchant/{region}/{tmpl['name']}")
    r = client.post(
        f"{mocksim_url}/api/v1/pos/merchants",
        headers=tenant_headers(api_key, idem),
        json=body,
        timeout=10.0,
    )
    if r.status_code == 201:
        m = r.json()
        report.merchants.append({"id": m["id"], "name": m["name"], "region": region, "currency": m["currency"]})
        print(f"  ✓ Merchant         {m['id']}  region={region}  name={m['name']}")
    else:
        report.warnings.append(f"Merchant create ({region}) HTTP {r.status_code}: {r.text[:200]}")


def create_accounts(
    client: httpx.Client,
    mocksim_url: str,
    api_key: str,
    primary_region: str,
    report: SeedReport,
) -> None:
    """One pool account in the primary region, plus two merchant VANs hung off it."""
    region_currency = {"PK": "PKR", "AE": "AED", "SA": "SAR", "EG": "EGP", "BH": "BHD"}
    currency = region_currency.get(primary_region, "PKR")

    pool_idem = stable_idem(f"account/pool/{primary_region}")
    r = client.post(
        f"{mocksim_url}/api/v1/bank/accounts",
        headers=tenant_headers(api_key, pool_idem),
        json={
            "account_type": "pool",
            "owner_name": "Trazmo Disbursement Pool",
            "region": primary_region,
            "currency": currency,
            "sharia_flag": False,
            "seed": f"trazmo-pool-{primary_region}",  # deterministic IBAN
        },
        timeout=10.0,
    )
    if r.status_code != 201:
        report.warnings.append(f"Pool account create HTTP {r.status_code}: {r.text[:200]}")
        return

    pool = r.json()
    report.accounts.append({"iban": pool["iban"], "type": "pool", "owner": pool["owner_name"]})
    print(f"  ✓ Pool account     {pool['iban']}  region={primary_region}  currency={currency}")

    # Two merchant VANs hung off the pool
    for n in (1, 2):
        idem = stable_idem(f"account/van/{primary_region}/{n}")
        rv = client.post(
            f"{mocksim_url}/api/v1/bank/accounts",
            headers=tenant_headers(api_key, idem),
            json={
                "account_type": "merchant_van",
                "owner_name": f"Merchant VAN {n}",
                "region": primary_region,
                "currency": currency,
                "parent_iban": pool["iban"],
                "seed": f"trazmo-van-{primary_region}-{n}",
            },
            timeout=10.0,
        )
        if rv.status_code == 201:
            v = rv.json()
            report.accounts.append({"iban": v["iban"], "type": "merchant_van", "owner": v["owner_name"]})
            print(f"  ✓ Merchant VAN     {v['iban']}  parent={pool['iban'][-8:]}…")
        else:
            report.warnings.append(f"VAN create ({n}) HTTP {rv.status_code}: {rv.text[:200]}")


def _normalize_trazmo_url(url: str, report: SeedReport) -> str:
    """
    The MockSim SSRF guard blocks `localhost` outright. When the seed runs
    inside the mocksim container (the usual `docker compose exec` path),
    `localhost` would also point at mocksim itself, not the host's
    trazmo-platform. Rewrite to host.docker.internal in that case and warn.
    """
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1"}:
        new = parsed._replace(netloc=f"host.docker.internal:{parsed.port or 8000}")
        rewritten = urlunparse(new)
        report.warnings.append(
            f"Rewrote trazmo URL {url!r} → {rewritten!r} (SSRF guard blocks loopback). "
            "Set TRAZMO_WEBHOOK_BASE_URL=http://host.docker.internal:8000 in .env to silence this."
        )
        return rewritten
    return url


def create_subscriptions(
    client: httpx.Client,
    mocksim_url: str,
    api_key: str,
    trazmo_url: str,
    pos_path: str,
    bank_path: str,
    webhook_secret: str,
    report: SeedReport,
) -> None:
    trazmo_url = _normalize_trazmo_url(trazmo_url, report)
    # POS subscription
    pos_target = trazmo_url.rstrip("/") + pos_path
    pos_idem = stable_idem(f"subscription/pos/{pos_target}")
    r = client.post(
        f"{mocksim_url}/api/v1/pos/webhooks/subscriptions",
        headers=tenant_headers(api_key, pos_idem),
        json={"url": pos_target, "secret": webhook_secret, "event_types": POS_EVENT_TYPES},
        timeout=10.0,
    )
    if r.status_code == 201:
        report.pos_subscription_id = r.json()["id"]
        print(f"  ✓ POS webhook sub  → {pos_target}")
    else:
        report.warnings.append(f"POS subscription HTTP {r.status_code}: {r.text[:200]}")

    # Bank subscription
    bank_target = trazmo_url.rstrip("/") + bank_path
    bank_idem = stable_idem(f"subscription/bank/{bank_target}")
    r = client.post(
        f"{mocksim_url}/api/v1/bank/webhooks/subscriptions",
        headers=tenant_headers(api_key, bank_idem),
        json={"url": bank_target, "secret": webhook_secret, "event_types": BANK_EVENT_TYPES},
        timeout=10.0,
    )
    if r.status_code == 201:
        report.bank_subscription_id = r.json()["id"]
        print(f"  ✓ Bank webhook sub → {bank_target}")
    else:
        report.warnings.append(f"Bank subscription HTTP {r.status_code}: {r.text[:200]}")


def fire_sample_txn(
    client: httpx.Client,
    mocksim_url: str,
    api_key: str,
    report: SeedReport,
) -> None:
    """
    Inject a single sample POS sale via /pos/transactions. This is a Phase-1
    convenience — `POST /pos/transactions` is currently the "inject arbitrary
    txn for tests" endpoint (the autonomous GMV generator is scheduled per
    merchant on creation and will produce its own).
    """
    if not report.merchants:
        report.warnings.append("No merchants — skipping sample transaction")
        return

    m = report.merchants[0]
    idem = stable_idem(f"sample-txn/{m['id']}")
    r = client.post(
        f"{mocksim_url}/api/v1/pos/transactions",
        headers=tenant_headers(api_key, idem),
        json={
            "merchant_id": m["id"],
            "amount": {"value": "15000", "currency": m["currency"]},  # 150.00 in minor units
            "card_brand": "visa",
            "card_last4": "4242",
            "event_type": "sale",
        },
        timeout=10.0,
    )
    if r.status_code in (200, 201):
        report.sample_txn_id = r.json().get("id") or r.json().get("transaction_id")
        print(f"  ✓ Sample sale      {report.sample_txn_id or '(injected)'}")
    elif r.status_code == 501:
        # POST /pos/transactions may not be implemented in this build —
        # the generator handles real volume. Not a hard failure.
        report.warnings.append(
            "POST /pos/transactions not implemented (501) — autonomous generator "
            "will produce sim-clock-driven traffic instead."
        )
    else:
        report.warnings.append(f"Sample txn HTTP {r.status_code}: {r.text[:200]}")


# ── Output ────────────────────────────────────────────────────────────────────

def print_report(report: SeedReport, mocksim_url: str, admin_token: str) -> None:
    bar = "─" * 72
    print()
    print(bar)
    print("Seed complete.")
    print(bar)
    print()
    print(f"  Dashboard:     {mocksim_url}/ui/")
    print(f"  API docs:      {mocksim_url}/docs")
    print(f"  Health:        {mocksim_url}/health")
    print()
    print("  Paste these into the Settings page of the dashboard:")
    print(f"    Admin Token       = {admin_token}")
    print(f"    Tenant API Key    = {report.api_key}")
    print()
    print(f"  Tenant ID:          {report.tenant_id or '(see warnings)'}")
    print(f"  POS subscription:   {report.pos_subscription_id or '(see warnings)'}")
    print(f"  Bank subscription:  {report.bank_subscription_id or '(see warnings)'}")
    print()
    if report.merchants:
        print("  Merchants:")
        for m in report.merchants:
            print(f"    • {m['id']:<14}  {m['region']}  {m['currency']}  {m['name']}")
    if report.accounts:
        print("  Accounts:")
        for a in report.accounts:
            print(f"    • {a['iban']:<28}  {a['type']:<13}  {a['owner']}")
    if report.warnings:
        print()
        print("  Warnings (non-fatal):")
        for w in report.warnings:
            print(f"    ! {w}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    env = load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mocksim", default=os.environ.get("MOCKSIM_URL", DEFAULT_MOCKSIM_URL),
                   help="MockSim base URL (default: %(default)s)")
    p.add_argument("--trazmo", default=os.environ.get("TRAZMO_WEBHOOK_BASE_URL", DEFAULT_TRAZMO_URL),
                   help="trazmo-platform base URL for webhook subscriptions (default: %(default)s)")
    p.add_argument("--admin-token", default=env.get("MOCKSIM_ADMIN_TOKEN") or os.environ.get("MOCKSIM_ADMIN_TOKEN"),
                   help="MockSim admin token (default: read from .env)")
    p.add_argument("--tenant-name", default=DEFAULT_TENANT_NAME)
    p.add_argument("--api-key", default=DEFAULT_API_KEY,
                   help="API key to issue for the default tenant (>= 32 chars)")
    p.add_argument("--webhook-secret", default=DEFAULT_WEBHOOK_SECRET,
                   help="HMAC secret for outbound webhooks (>= 16 chars)")
    p.add_argument("--pos-path", default=env.get("TRAZMO_POS_WEBHOOK_PATH", "/webhooks/pos"))
    p.add_argument("--bank-path", default=env.get("TRAZMO_BANK_WEBHOOK_PATH", "/webhooks/bank"))
    p.add_argument("--regions", default=env.get("ENABLED_REGIONS", "PK"),
                   help="Comma-separated region codes (default: from .env)")
    p.add_argument("--reset", action="store_true",
                   help="Wipe the default tenant's data before seeding (calls /admin/reset)")
    p.add_argument("--no-sample-txn", action="store_true",
                   help="Skip the one-shot sample POS sale at the end")
    p.add_argument("--json", action="store_true",
                   help="Emit a machine-readable summary on stdout instead of pretty output")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.admin_token:
        sys.stderr.write("Admin token not provided — pass --admin-token or set MOCKSIM_ADMIN_TOKEN in .env\n")
        return 2

    regions = [r.strip().upper() for r in args.regions.split(",") if r.strip()]
    if not regions:
        sys.stderr.write("No regions specified — pass --regions PK,AE,...\n")
        return 2

    report = SeedReport()

    print(f"Seeding MockSim at {args.mocksim}")
    print(f"  Tenant:   {args.tenant_name}")
    print(f"  Regions:  {', '.join(regions)}")
    print(f"  Trazmo:   {args.trazmo}  (POS={args.pos_path}, Bank={args.bank_path})")
    print()

    with httpx.Client() as client:
        wait_for_health(client, args.mocksim)

        ensure_tenant(
            client, args.mocksim, args.admin_token,
            name=args.tenant_name, api_key=args.api_key,
            report=report, reset=args.reset,
        )
        if not report.api_key:
            sys.stderr.write("No tenant API key — cannot continue\n")
            return 1

        # On --reset, purge domain data so re-seed is from a clean state.
        # (We keep the tenant row + api_key so the dashboard's saved key
        # keeps working. Pass purge=true if you also want the tenant gone.)
        if args.reset and report.tenant_id:
            rr = client.post(
                f"{args.mocksim}/api/v1/admin/reset",
                headers=admin_headers(args.admin_token),
                params={"tenant_id": report.tenant_id, "purge": "false"},
                timeout=15.0,
            )
            if rr.status_code != 200:
                report.warnings.append(f"Reset HTTP {rr.status_code}: {rr.text[:200]}")
            else:
                print(f"  ✓ Tenant data reset for {report.tenant_id}")

        ensure_scenarios_enabled(client, args.mocksim, args.admin_token)

        for region in regions:
            create_merchant(client, args.mocksim, report.api_key, region, report)

        create_accounts(client, args.mocksim, report.api_key, regions[0], report)

        create_subscriptions(
            client, args.mocksim, report.api_key, args.trazmo,
            pos_path=args.pos_path, bank_path=args.bank_path,
            webhook_secret=args.webhook_secret, report=report,
        )

        if not args.no_sample_txn:
            fire_sample_txn(client, args.mocksim, report.api_key, report)

    if args.json:
        out = {
            "tenant_id": report.tenant_id,
            "api_key": report.api_key,
            "merchants": report.merchants,
            "accounts": report.accounts,
            "pos_subscription_id": report.pos_subscription_id,
            "bank_subscription_id": report.bank_subscription_id,
            "sample_txn_id": report.sample_txn_id,
            "warnings": report.warnings,
            "dashboard_url": f"{args.mocksim}/ui/",
            "docs_url": f"{args.mocksim}/docs",
        }
        print(json.dumps(out, indent=2))
    else:
        print_report(report, args.mocksim, args.admin_token)

    return 0


if __name__ == "__main__":
    sys.exit(main())
