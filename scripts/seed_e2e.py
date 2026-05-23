"""
seed_e2e.py — Unified MockSim ↔ trazmo-platform E2E seed orchestrator.

What this does, end to end:

  1. Verify both stacks are reachable
       - MockSim       :8080
       - trazmo-platform postgres  :5433
       - (optional) trazmo-platform API :8000  — only needed if --run-trazmo-seeds

  2. Foundation on trazmo (optional, --run-trazmo-seeds):
       - python -m scripts.seed_dev              (currencies, entity_types, tenants)
       - python scripts/seed_mock_gmv.py
             --num-merchants 25 --history-months 6
         …which creates 25 entities + sme_profile + merchant_profile +
         acquirer_merchant_mapping rows AND a 6-month POS history (the
         backlog Flux's risk scanner uses to compute eligibility).

  3. Mirror trazmo's master data into MockSim:
       - Read tenant.id, partner_profile.code, and the 25
         (entity_id, acquirer_merchant_id, sme_profile.legal_name, currency)
         tuples directly from trazmo's postgres.
       - Create/reuse a MockSim tenant whose `partner_code` matches trazmo's
         partner_profile.code (this is the bridge ID).
       - Create one MockSim merchant per trazmo merchant, passing the SAME
         acquirer_merchant_id and external_entity_id.

  4. Wire the settlement webhook:
       - Subscribe MockSim's POS surface to
         http://host.docker.internal:8000/api/v1/acquirer/webhooks/settlement
         with format=trazmo_settlement, the shared HMAC secret, and the
         `X-Trazmo-Tenant-Id` header (forwarded as X-Tenant-ID at dispatch).
       - From this point on, every sim-day settlement in MockSim will POST
         to trazmo with the exact AcquirerSettlementPayload shape trazmo's
         receiver expects.

  5. Prime the generator (optional, --advance-days N):
       - Bump MockSim's sim clock by N days so the generator emits new
         transactions and at least one batched settlement fires through to
         trazmo. Default N=1 — enough for a smoke verification.

Idempotent by design. Re-running:
  - The MockSim tenant + merchants reuse existing rows via the same
    `(acquirer_merchant_id, mock_tenant_id)` unique constraint introduced
    in migration 0002.
  - The webhook subscription uses a deterministic Idempotency-Key, so
    re-runs return the existing subscription ID.
  - trazmo's seeds are themselves idempotent per their docstrings.

Usage:
    python scripts/seed_e2e.py                              # mirror only
    python scripts/seed_e2e.py --run-trazmo-seeds           # also drive trazmo
    python scripts/seed_e2e.py --advance-days 7             # 7 days of GMV
    python scripts/seed_e2e.py --json                       # machine output
    python scripts/seed_e2e.py --trazmo-pg postgresql://trazmo:trazmo@localhost:5433/trazmo_v4

Requirements on host (or in the mocksim container):
    pip install httpx asyncpg

Exits non-zero on hard errors (unreachable services, schema mismatches);
prints a clear "what to do next" hint each time.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    sys.stderr.write("Missing dep: pip install httpx (or run via docker compose exec mocksim)\n")
    raise SystemExit(2)

try:
    import asyncpg
except ImportError:
    sys.stderr.write("Missing dep: pip install asyncpg\n")
    raise SystemExit(2)


# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_MOCKSIM_URL = "http://localhost:8080"
DEFAULT_TRAZMO_URL = "http://host.docker.internal:8000"  # from inside container
DEFAULT_TRAZMO_PG = "postgresql://trazmo:trazmo@localhost:5433/trazmo_v4"
DEFAULT_TENANT_NAME = "mocksim-e2e-tenant"
DEFAULT_API_KEY = "mocksim-e2e-tenant-key-do-not-use-in-prod-aaa"
DEFAULT_WEBHOOK_SECRET = "mocksim-e2e-webhook-secret-rotate-before-shared"
SETTLEMENT_PATH = "/api/v1/acquirer/webhooks/settlement"


# ── Result tracking ───────────────────────────────────────────────────────────

@dataclass
class E2EReport:
    mocksim_tenant_id: str = ""
    mocksim_api_key: str = ""
    trazmo_tenant_id: str = ""
    partner_code: str = ""
    partner_entity_id: str = ""
    mirrored_merchants: list[dict[str, str]] = field(default_factory=list)
    subscription_id: str = ""
    sim_time_before: str = ""
    sim_time_after: str = ""
    warnings: list[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def tenant_headers(api_key: str, idem: str | None = None,
                   trazmo_tenant_id: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if idem:
        h["Idempotency-Key"] = idem
    if trazmo_tenant_id:
        # MockSim's TenancyMiddleware records this on every row created
        # during the request. Crucial for trazmo_settlement subscriptions
        # because that's the X-Tenant-ID trazmo's receiver expects.
        h["X-Trazmo-Tenant-Id"] = trazmo_tenant_id
    return h


def stable_idem(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mocksim-seed-e2e/{seed}"))


def load_dotenv(path: Path) -> dict[str, str]:
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


def _is_in_container() -> bool:
    return Path("/.dockerenv").exists()


def normalize_trazmo_pg_url(url: str) -> str:
    """
    When this script runs *inside* the mocksim container, `localhost`
    points at the container itself, not the host. Rewrite to
    host.docker.internal so we can reach trazmo's postgres on the host's
    :5433. (The seed_default.py script does the same dance for HTTP URLs.)
    """
    if _is_in_container() and "localhost" in url:
        return url.replace("localhost", "host.docker.internal")
    return url


# ── Step 1: Reachability ──────────────────────────────────────────────────────

def wait_for_mocksim(url: str, timeout_s: int = 30) -> None:
    import time
    deadline = time.monotonic() + timeout_s
    last_err = ""
    with httpx.Client() as client:
        while time.monotonic() < deadline:
            try:
                r = client.get(f"{url}/health", timeout=2.0)
                if r.status_code == 200:
                    print(f"  ✓ MockSim reachable          {url}  sim_time={r.json()['sim_time']}")
                    return
                last_err = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                last_err = str(exc)
            time.sleep(0.5)
    raise SystemExit(
        f"MockSim not reachable at {url} ({last_err}). "
        "Bring it up first: `docker compose up -d` in the MockSim repo."
    )


async def verify_trazmo_pg(dsn: str) -> dict[str, Any]:
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=5)
    except Exception as exc:
        raise SystemExit(
            f"Trazmo postgres not reachable at {dsn}: {exc}\n"
            "Start it from the trazmo-platform repo:\n"
            "    cd D:\\Trazmo\\trazmo-platform\n"
            "    docker-compose up -d db redis tigerbeetle"
        )
    try:
        # Confirm trazmo's schema is in place — quick sanity check.
        row = await conn.fetchrow(
            "SELECT to_regclass('public.tenant') AS t, "
            "to_regclass('public.acquirer_merchant_mapping') AS m"
        )
        if row["t"] is None or row["m"] is None:
            raise SystemExit(
                "Trazmo postgres reached, but expected tables are missing. "
                "Run trazmo's alembic upgrade head first."
            )
        return {"ok": True}
    finally:
        await conn.close()


def verify_trazmo_api_if_needed(url: str, run_seeds: bool) -> None:
    if not run_seeds:
        return
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get(f"{url}/health")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        print(f"  ✓ Trazmo API reachable       {url}")
    except Exception as exc:
        raise SystemExit(
            f"--run-trazmo-seeds was passed, but trazmo API at {url} isn't reachable ({exc}).\n"
            "Start trazmo's API in another terminal:\n"
            "    cd D:\\Trazmo\\trazmo-platform && venv\\Scripts\\activate && uvicorn main:app --port 8000"
        )


# ── Step 2: Run trazmo seeds (optional) ───────────────────────────────────────

def run_trazmo_seeds(trazmo_repo: Path, partner_code: str, num_merchants: int,
                     history_months: int) -> None:
    """
    Drive trazmo-platform's existing seed scripts via subprocess. We don't
    import them — they own their own SQLAlchemy engine and a DATABASE_URL.

    seed_dev is the foundation (idempotent — safe to re-run).
    seed_mock_gmv creates the merchants + acquirer mappings + historical GMV.
    """
    venv_python = trazmo_repo / "venv" / "Scripts" / "python.exe"
    python = str(venv_python) if venv_python.exists() else sys.executable
    env = {**os.environ, "PYTHONPATH": str(trazmo_repo)}

    print(f"  → trazmo seed_dev (foundation)")
    subprocess.run(
        [python, "-m", "scripts.seed_dev"],
        cwd=str(trazmo_repo), env=env, check=True,
    )

    print(f"  → trazmo seed_mock_gmv "
          f"(--num-merchants {num_merchants} --history-months {history_months} "
          f"--partner-code {partner_code})")
    subprocess.run(
        [
            python, "scripts/seed_mock_gmv.py",
            "--num-merchants", str(num_merchants),
            "--history-months", str(history_months),
            "--partner-code", partner_code,
        ],
        cwd=str(trazmo_repo), env=env, check=True,
    )
    print("  ✓ Trazmo seeds completed")


# ── Step 3: Read trazmo master data ───────────────────────────────────────────

async def read_trazmo_merchants(dsn: str, partner_code: str) -> dict[str, Any]:
    """
    Pull the (tenant, partner, [merchants]) tuple that the MockSim mirror needs.

    Schema reference:
      - tenant(id, code, ...)
      - entity(id, code, ...)  — partner_profile.entity_id and sme_profile.entity_id
      - partner_profile(entity_id, code, ...)
      - sme_profile(entity_id, legal_name, ...)
      - acquirer_merchant_mapping(tenant_id, trazmo_entity_id, partner_entity_id, acquirer_merchant_id, ...)
    """
    conn = await asyncpg.connect(dsn=dsn)
    try:
        # Locate the partner row by code → entity_id, tenant_id.
        partner_row = await conn.fetchrow(
            """
            SELECT pp.entity_id     AS partner_entity_id,
                   pp.code          AS partner_code,
                   amm.tenant_id    AS tenant_id
              FROM partner_profile pp
              JOIN acquirer_merchant_mapping amm
                ON amm.partner_entity_id = pp.entity_id
             WHERE pp.code = $1
             LIMIT 1
            """,
            partner_code,
        )
        if partner_row is None:
            raise SystemExit(
                f"No partner_profile/acquirer mapping found for partner_code={partner_code!r}. "
                "Either pass a different --partner-code, or re-run with --run-trazmo-seeds "
                "so the partner is created."
            )

        # All merchants mapped to that partner.
        merchants = await conn.fetch(
            """
            SELECT amm.trazmo_entity_id   AS entity_id,
                   amm.acquirer_merchant_id,
                   COALESCE(sme.legal_name, e.code)  AS name,
                   COALESCE(mp.operating_currency_id::text, '')  AS currency_id
              FROM acquirer_merchant_mapping amm
              JOIN entity e            ON e.id = amm.trazmo_entity_id
         LEFT JOIN sme_profile sme     ON sme.entity_id = amm.trazmo_entity_id
         LEFT JOIN merchant_profile mp ON mp.entity_id  = amm.trazmo_entity_id
             WHERE amm.partner_entity_id = $1
               AND amm.tenant_id         = $2
             ORDER BY amm.acquirer_merchant_id
            """,
            partner_row["partner_entity_id"], partner_row["tenant_id"],
        )

        # Resolve currency_id → currency.code (PKR/USD/...) once.
        currency_codes: dict[str, str] = {}
        currency_ids = {m["currency_id"] for m in merchants if m["currency_id"]}
        if currency_ids:
            rows = await conn.fetch(
                "SELECT id::text AS id, code FROM currency WHERE id::text = ANY($1::text[])",
                list(currency_ids),
            )
            currency_codes = {r["id"]: r["code"] for r in rows}

        return {
            "tenant_id": str(partner_row["tenant_id"]),
            "partner_code": partner_row["partner_code"],
            "partner_entity_id": str(partner_row["partner_entity_id"]),
            "merchants": [
                {
                    "entity_id": str(m["entity_id"]),
                    "acquirer_merchant_id": m["acquirer_merchant_id"],
                    "name": m["name"] or m["acquirer_merchant_id"],
                    "currency": currency_codes.get(m["currency_id"], "PKR"),
                }
                for m in merchants
            ],
        }
    finally:
        await conn.close()


# ── Step 4: Mirror into MockSim ───────────────────────────────────────────────

def ensure_mocksim_tenant(client: httpx.Client, mocksim_url: str, admin_token: str,
                          name: str, api_key: str, partner_code: str,
                          report: E2EReport) -> None:
    r = client.post(
        f"{mocksim_url}/api/v1/admin/tenants",
        headers=admin_headers(admin_token),
        json={
            "name": name,
            "api_key": api_key,
            "partner_code": partner_code,
            "scopes": ["pos.read", "pos.write", "bank.read", "bank.write"],
        },
        timeout=10.0,
    )
    if r.status_code not in (200, 201):
        raise SystemExit(f"Tenant create failed: HTTP {r.status_code} {r.text[:300]}")
    body = r.json()
    report.mocksim_tenant_id = body["tenant_id"]
    report.mocksim_api_key = api_key
    existed = r.status_code == 200 or body.get("existed", False)
    print(f"  ✓ MockSim tenant {'reused' if existed else 'created'}  "
          f"id={report.mocksim_tenant_id}  partner_code={partner_code}")


def mirror_merchants(client: httpx.Client, mocksim_url: str, api_key: str,
                     trazmo_tenant_id: str, merchants: list[dict[str, str]],
                     report: E2EReport) -> None:
    print(f"  → Mirroring {len(merchants)} merchants from trazmo into MockSim…")
    for m in merchants:
        # Region inferred from currency for now — PK/PKR, AE/AED, SA/SAR, EG/EGP, BH/BHD.
        # Defaulting to PK if unknown, since trazmo's seed_mock_gmv ships PKR.
        currency = m.get("currency", "PKR")
        region = {"PKR": "PK", "AED": "AE", "SAR": "SA", "EGP": "EG", "BHD": "BH"}.get(currency, "PK")

        idem = stable_idem(f"merchant/{m['acquirer_merchant_id']}")
        r = client.post(
            f"{mocksim_url}/api/v1/pos/merchants",
            headers=tenant_headers(api_key, idem, trazmo_tenant_id=trazmo_tenant_id),
            json={
                "name": m["name"],
                "region": region,
                "mcc": "5411",   # default grocery — trazmo's seed doesn't expose mcc
                "expected_daily_txns": 80,
                "avg_ticket_major_units": 1500.0,
                "risk_tier": "standard",
                "acquirer_merchant_id": m["acquirer_merchant_id"],
                "external_entity_id": m["entity_id"],
            },
            timeout=10.0,
        )
        if r.status_code == 201:
            mm = r.json()
            report.mirrored_merchants.append({
                "mocksim_id": mm["id"],
                "acquirer_merchant_id": m["acquirer_merchant_id"],
                "entity_id": m["entity_id"],
                "name": m["name"],
            })
        else:
            report.warnings.append(
                f"Mirror {m['acquirer_merchant_id']} HTTP {r.status_code}: {r.text[:200]}"
            )
    print(f"  ✓ Mirrored {len(report.mirrored_merchants)}/{len(merchants)} merchants")


def subscribe_settlement_webhook(client: httpx.Client, mocksim_url: str, api_key: str,
                                 trazmo_url: str, trazmo_tenant_id: str,
                                 secret: str, report: E2EReport) -> None:
    target = trazmo_url.rstrip("/") + SETTLEMENT_PATH
    idem = stable_idem(f"sub/trazmo_settlement/{target}/{trazmo_tenant_id}")
    r = client.post(
        f"{mocksim_url}/api/v1/pos/webhooks/subscriptions",
        headers=tenant_headers(api_key, idem, trazmo_tenant_id=trazmo_tenant_id),
        json={
            "url": target,
            "secret": secret,
            "event_types": ["pos.batch.settled"],
            "format": "trazmo_settlement",
        },
        timeout=10.0,
    )
    if r.status_code == 201:
        report.subscription_id = r.json()["id"]
        print(f"  ✓ Subscribed trazmo_settlement  → {target}")
        print(f"      X-Tenant-ID header will be set to: {trazmo_tenant_id}")
    else:
        report.warnings.append(
            f"Subscription HTTP {r.status_code}: {r.text[:200]}"
        )


# ── Step 5: Advance the sim clock ─────────────────────────────────────────────

def advance_clock(client: httpx.Client, mocksim_url: str, admin_token: str, days: int,
                  report: E2EReport) -> None:
    before = client.get(f"{mocksim_url}/api/v1/admin/clock",
                        headers=admin_headers(admin_token)).json()
    report.sim_time_before = before["sim_time"]
    if days <= 0:
        report.sim_time_after = report.sim_time_before
        return
    r = client.post(
        f"{mocksim_url}/api/v1/admin/clock/advance",
        headers=admin_headers(admin_token),
        json={"days": days, "hours": 0, "minutes": 0},
        timeout=120.0,
    )
    if r.status_code in (200, 202):
        report.sim_time_after = r.json().get("sim_time", "")
        print(f"  ✓ Sim clock advanced {days} day(s)  "
              f"{report.sim_time_before} → {report.sim_time_after}")
    else:
        report.warnings.append(f"Clock advance HTTP {r.status_code}: {r.text[:200]}")


# ── Output ────────────────────────────────────────────────────────────────────

def print_report(report: E2EReport, mocksim_url: str, trazmo_url: str) -> None:
    bar = "─" * 72
    print()
    print(bar)
    print("End-to-end seed complete.")
    print(bar)
    print()
    print("  MockSim:")
    print(f"    Dashboard:        {mocksim_url}/ui/")
    print(f"    Tenant ID:        {report.mocksim_tenant_id}")
    print(f"    API key:          {report.mocksim_api_key}")
    print(f"    partner_code:     {report.partner_code}")
    print(f"    Mirrored:         {len(report.mirrored_merchants)} merchant(s)")
    print()
    print("  Trazmo:")
    print(f"    API:              {trazmo_url}")
    print(f"    Tenant ID:        {report.trazmo_tenant_id}")
    print(f"    Partner entity:   {report.partner_entity_id}")
    print(f"    Webhook target:   {trazmo_url}{SETTLEMENT_PATH}")
    print()
    print("  Wire:")
    print(f"    Subscription ID:  {report.subscription_id}")
    print(f"    Sim clock:        {report.sim_time_before}  →  {report.sim_time_after}")
    print()
    if report.mirrored_merchants[:5]:
        print("  First few mirrored merchants:")
        for m in report.mirrored_merchants[:5]:
            print(f"    • {m['acquirer_merchant_id']:<12}  "
                  f"mocksim={m['mocksim_id']}  entity={m['entity_id'][:8]}…")
        if len(report.mirrored_merchants) > 5:
            print(f"    … plus {len(report.mirrored_merchants) - 5} more")
    if report.warnings:
        print()
        print("  Warnings (non-fatal):")
        for w in report.warnings:
            print(f"    ! {w}")
    print()
    print("  Next:")
    print(f"    1. Watch outbox:  curl -H 'Authorization: Bearer <admin>' "
          f"{mocksim_url}/api/v1/admin/outbox?status=delivered | jq")
    print(f"    2. Watch trazmo's deductions table for new rows.")
    print(f"    3. Open Flux at http://localhost:5173/workflows for offer/scan UI.")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    env = load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mocksim", default=os.environ.get("MOCKSIM_URL", DEFAULT_MOCKSIM_URL))
    p.add_argument("--trazmo", default=os.environ.get("TRAZMO_WEBHOOK_BASE_URL", DEFAULT_TRAZMO_URL))
    p.add_argument("--trazmo-pg", default=os.environ.get("TRAZMO_DATABASE_URL", DEFAULT_TRAZMO_PG),
                   help="Trazmo postgres DSN. Use postgresql://… (asyncpg driver inferred)")
    p.add_argument("--trazmo-repo", default=os.environ.get("TRAZMO_REPO", str(Path("D:/Trazmo/trazmo-platform"))),
                   help="Path to trazmo-platform checkout (only used with --run-trazmo-seeds)")
    p.add_argument("--admin-token", default=env.get("MOCKSIM_ADMIN_TOKEN"))
    p.add_argument("--tenant-name", default=DEFAULT_TENANT_NAME)
    p.add_argument("--api-key", default=DEFAULT_API_KEY)
    p.add_argument("--webhook-secret", default=env.get("ACQUIRER_WEBHOOK_SECRET") or DEFAULT_WEBHOOK_SECRET,
                   help="Shared HMAC secret. Must match trazmo's ACQUIRER_WEBHOOK_SECRET.")
    p.add_argument("--partner-code", default="MOCKSIM_PK_POS",
                   help="trazmo partner_profile.code to mirror (and create if --run-trazmo-seeds).")
    p.add_argument("--num-merchants", type=int, default=25)
    p.add_argument("--history-months", type=int, default=6,
                   help="Months of historical GMV trazmo's seed should backfill into its own DB.")
    p.add_argument("--run-trazmo-seeds", action="store_true",
                   help="Run trazmo's seed_dev + seed_mock_gmv before mirroring.")
    p.add_argument("--advance-days", type=int, default=1,
                   help="Sim-days to advance MockSim after mirroring (0 = no advance).")
    p.add_argument("--json", action="store_true")
    return p.parse_args()


async def amain() -> int:
    args = parse_args()
    if not args.admin_token:
        sys.stderr.write("MockSim admin token missing — pass --admin-token or set in .env\n")
        return 2

    args.trazmo_pg = normalize_trazmo_pg_url(args.trazmo_pg)

    print(f"E2E seed orchestration")
    print(f"  MockSim:   {args.mocksim}")
    print(f"  Trazmo:    {args.trazmo}  (pg: {args.trazmo_pg})")
    print(f"  Partner:   {args.partner_code}")
    print(f"  Mode:      {'run trazmo seeds + mirror' if args.run_trazmo_seeds else 'mirror only'}")
    print()

    report = E2EReport(partner_code=args.partner_code)

    # 1. Reachability
    wait_for_mocksim(args.mocksim)
    await verify_trazmo_pg(args.trazmo_pg)
    verify_trazmo_api_if_needed(args.trazmo, args.run_trazmo_seeds)

    # 2. Trazmo seeds (optional)
    if args.run_trazmo_seeds:
        run_trazmo_seeds(
            trazmo_repo=Path(args.trazmo_repo),
            partner_code=args.partner_code,
            num_merchants=args.num_merchants,
            history_months=args.history_months,
        )

    # 3. Read master data
    print(f"  → Reading master data from trazmo pg…")
    master = await read_trazmo_merchants(args.trazmo_pg, args.partner_code)
    report.trazmo_tenant_id = master["tenant_id"]
    report.partner_entity_id = master["partner_entity_id"]
    print(f"  ✓ Found tenant={master['tenant_id']}  "
          f"partner_entity={master['partner_entity_id']}  "
          f"merchants={len(master['merchants'])}")

    if not master["merchants"]:
        sys.stderr.write(
            "No merchants found on trazmo's side. Re-run with --run-trazmo-seeds, "
            "or run trazmo's seed_mock_gmv.py manually first.\n"
        )
        return 1

    # 4. Mirror & subscribe (synchronous httpx, simpler than mixing async stacks)
    with httpx.Client() as client:
        ensure_mocksim_tenant(client, args.mocksim, args.admin_token,
                              name=args.tenant_name, api_key=args.api_key,
                              partner_code=master["partner_code"], report=report)

        mirror_merchants(client, args.mocksim, report.mocksim_api_key,
                         master["tenant_id"], master["merchants"], report)

        subscribe_settlement_webhook(client, args.mocksim, report.mocksim_api_key,
                                     args.trazmo, master["tenant_id"],
                                     args.webhook_secret, report)

        # 5. Optional clock advance
        advance_clock(client, args.mocksim, args.admin_token,
                      args.advance_days, report)

    if args.json:
        print(json.dumps({
            "mocksim_tenant_id": report.mocksim_tenant_id,
            "mocksim_api_key": report.mocksim_api_key,
            "trazmo_tenant_id": report.trazmo_tenant_id,
            "partner_code": report.partner_code,
            "partner_entity_id": report.partner_entity_id,
            "subscription_id": report.subscription_id,
            "mirrored": report.mirrored_merchants,
            "sim_time_before": report.sim_time_before,
            "sim_time_after": report.sim_time_after,
            "warnings": report.warnings,
        }, indent=2))
    else:
        print_report(report, args.mocksim, args.trazmo)

    return 0


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
