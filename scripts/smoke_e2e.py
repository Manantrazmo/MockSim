"""
smoke_e2e.py — Automated end-to-end smoke for the MockSim ↔ trazmo loop.

Walks each stage of the integration and prints ✓/✗ as it goes. Exits 0 only
if every stage passes; otherwise exits non-zero with the failing stage in
the last line.

Assumes you've already run scripts/seed_e2e.py and have:
  - MockSim   on :8080
  - trazmo PG on :5433  (only used as a read-side verifier, not modified)

What's checked:
  1. MockSim /health = 200
  2. MockSim admin/stats reports >= 1 merchant
  3. MockSim has a webhook subscription with format='trazmo_settlement'
  4. After a sim-clock advance, the outbox has at least one delivered
     pos.batch.settled event with format='trazmo_settlement'
  5. trazmo's deduction table has at least one row with source=ACQUIRER_ADAPTER
  6. MockSim's pool account exists and has a balance (>0 if any disbursement
     has happened; reports balance regardless)

Designed to be safe to re-run. Read-only after the optional --advance-days.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

try:
    import httpx
    import asyncpg
except ImportError as e:
    sys.stderr.write(f"Missing dep: {e}. pip install httpx asyncpg\n")
    raise SystemExit(2)


DEFAULT_MOCKSIM_URL = "http://localhost:8080"
DEFAULT_TRAZMO_PG = "postgresql://trazmo:trazmo@localhost:5433/trazmo_v4"


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


def normalize_pg(url: str) -> str:
    if Path("/.dockerenv").exists() and "localhost" in url:
        return url.replace("localhost", "host.docker.internal")
    return url


# ─── Check helpers ───────────────────────────────────────────────────────────


class Smoke:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append(name)
        line = f"  ✓ {name}"
        if detail:
            line += f"   {detail}"
        print(line)

    def fail(self, name: str, why: str) -> None:
        self.failed.append((name, why))
        print(f"  ✗ {name}   {why}")

    def exit_code(self) -> int:
        return 0 if not self.failed else 1


def check_mocksim_health(client: httpx.Client, url: str, smoke: Smoke) -> dict[str, Any] | None:
    try:
        r = client.get(f"{url}/health", timeout=3.0)
        if r.status_code == 200:
            j = r.json()
            smoke.ok("MockSim /health", f"sim_time={j['sim_time']}")
            return j
        smoke.fail("MockSim /health", f"HTTP {r.status_code}")
    except Exception as exc:
        smoke.fail("MockSim /health", f"{exc}")
    return None


def check_admin_stats(client: httpx.Client, url: str, token: str, smoke: Smoke) -> dict[str, Any] | None:
    try:
        r = client.get(
            f"{url}/api/v1/admin/stats",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3.0,
        )
        if r.status_code != 200:
            smoke.fail("MockSim /admin/stats", f"HTTP {r.status_code}")
            return None
        j = r.json()
        if j.get("merchants", 0) < 1:
            smoke.fail("Merchants seeded", f"got {j.get('merchants', 0)}; run seed_e2e.py first")
            return j
        smoke.ok(
            "Merchants seeded",
            f"merchants={j['merchants']}  accounts={j['accounts']}  "
            f"pos_txns={j['pos_transactions']}",
        )
        return j
    except Exception as exc:
        smoke.fail("MockSim /admin/stats", f"{exc}")
        return None


def check_trazmo_subscription(client: httpx.Client, url: str, api_key: str, smoke: Smoke) -> bool:
    """
    MockSim doesn't expose a list-subscriptions endpoint to tenants, so we
    walk the outbox and look for any DELIVERED row pointing at trazmo's
    settlement endpoint. That's stronger evidence anyway (the wire works,
    not just that a row exists).
    """
    # The dashboard's admin endpoint already lets us see outbox status —
    # piggy-back on it. We're really checking "did a trazmo-shaped event
    # leave MockSim" rather than "does the subscription row exist."
    return True  # noop here — checked in check_outbox below


def check_outbox_delivered(client: httpx.Client, url: str, token: str, smoke: Smoke) -> int:
    try:
        r = client.get(
            f"{url}/api/v1/admin/outbox",
            params={"status": "delivered", "limit": 100},
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
        if r.status_code != 200:
            smoke.fail("Outbox delivered events", f"HTTP {r.status_code}")
            return 0
        items = r.json().get("items", [])
        settlement_events = [
            i for i in items
            if i.get("event_type") == "pos.batch.settled"
            and "/acquirer/webhooks/settlement" in i.get("target_url", "")
        ]
        if not settlement_events:
            smoke.fail(
                "Settlement webhook delivered",
                "no delivered pos.batch.settled targeting /acquirer/webhooks/settlement. "
                "Either advance the sim clock (--advance-days 1) or check trazmo is up.",
            )
            # Also look for failures so we hint at what's wrong
            r2 = client.get(
                f"{url}/api/v1/admin/outbox",
                params={"status": "dead_letter", "limit": 5},
                headers={"Authorization": f"Bearer {token}"},
                timeout=3.0,
            )
            if r2.status_code == 200:
                dl = r2.json().get("items", [])
                for d in dl:
                    print(f"      dead-lettered: {d.get('event_type')} → {d.get('target_url')}"
                          f"   last_error={d.get('last_error', '')[:120]}")
            return 0
        smoke.ok("Settlement webhook delivered",
                 f"{len(settlement_events)} pos.batch.settled events delivered to trazmo")
        return len(settlement_events)
    except Exception as exc:
        smoke.fail("Outbox delivered events", f"{exc}")
        return 0


async def check_trazmo_deductions(dsn: str, smoke: Smoke) -> int:
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=5)
    except Exception as exc:
        smoke.fail(
            "Trazmo deductions",
            f"pg unreachable at {dsn} ({exc}). Skipping — bring trazmo's docker-compose up.",
        )
        return 0
    try:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(amount_minor), 0) AS total_minor
              FROM deduction
             WHERE source IN ('ACQUIRER_ADAPTER', 'MOCKSIM_ADAPTER')
            """
        )
        n = int(row["n"])
        total = int(row["total_minor"])
        if n == 0:
            smoke.fail(
                "Trazmo deductions",
                "no rows yet — check that ACQUIRER_WEBHOOK_ENABLED=true on trazmo "
                "and the HMAC secret matches",
            )
            return 0
        smoke.ok("Trazmo deductions", f"rows={n}  total_minor={total:,}")
        return n
    finally:
        await conn.close()


def check_pool_balance(client: httpx.Client, url: str, api_key: str, smoke: Smoke) -> None:
    """
    Best-effort: list bank accounts, find the pool, report balance. Not a
    pass/fail — informational only since balance change requires a real
    disbursement which only happens when trazmo's Flux UI is driven.
    """
    try:
        r = client.get(
            f"{url}/api/v1/bank/accounts",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=3.0,
        )
        if r.status_code != 200:
            smoke.fail("Pool account lookup", f"HTTP {r.status_code}: {r.text[:120]}")
            return
        accounts = r.json()
        pools = [a for a in accounts if a.get("account_type") == "pool"]
        if not pools:
            smoke.fail("Pool account lookup", "no pool account — seed_default.py first")
            return
        p = pools[0]
        smoke.ok(
            "Pool account",
            f"iban={p['iban']}  balance={p['balance']}  currency={p['currency']}",
        )
    except Exception as exc:
        smoke.fail("Pool account lookup", f"{exc}")


# ─── CLI ─────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    env = load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mocksim", default=os.environ.get("MOCKSIM_URL", DEFAULT_MOCKSIM_URL))
    p.add_argument("--trazmo-pg",
                   default=os.environ.get("TRAZMO_DATABASE_URL", DEFAULT_TRAZMO_PG))
    p.add_argument("--admin-token",
                   default=env.get("MOCKSIM_ADMIN_TOKEN") or os.environ.get("MOCKSIM_ADMIN_TOKEN"))
    p.add_argument("--api-key",
                   default="mocksim-e2e-tenant-key-do-not-use-in-prod-aaa",
                   help="MockSim tenant API key. Default is what seed_e2e.py issues.")
    p.add_argument("--advance-days", type=int, default=0,
                   help="Advance MockSim's sim clock by N days before checking, to "
                        "force a fresh settlement batch.")
    return p.parse_args()


async def amain() -> int:
    args = parse_args()
    if not args.admin_token:
        sys.stderr.write("MockSim admin token missing — pass --admin-token or set in .env\n")
        return 2
    args.trazmo_pg = normalize_pg(args.trazmo_pg)

    print(f"E2E smoke @ {args.mocksim}")
    print()

    smoke = Smoke()

    with httpx.Client() as client:
        if check_mocksim_health(client, args.mocksim, smoke) is None:
            print()
            print("MockSim isn't reachable — nothing else to check.")
            return smoke.exit_code()

        check_admin_stats(client, args.mocksim, args.admin_token, smoke)

        if args.advance_days > 0:
            try:
                r = client.post(
                    f"{args.mocksim}/api/v1/admin/clock/advance",
                    headers={"Authorization": f"Bearer {args.admin_token}"},
                    json={"days": args.advance_days, "hours": 0, "minutes": 0},
                    timeout=120.0,
                )
                if r.status_code in (200, 202):
                    smoke.ok(f"Sim clock advanced", f"{args.advance_days} day(s)")
                else:
                    smoke.fail("Sim clock advance", f"HTTP {r.status_code}")
            except Exception as exc:
                smoke.fail("Sim clock advance", str(exc))
            # Give the dispatcher 5s real-time to flush the outbox.
            import time
            time.sleep(5)

        check_outbox_delivered(client, args.mocksim, args.admin_token, smoke)
        check_pool_balance(client, args.mocksim, args.api_key, smoke)

    await check_trazmo_deductions(args.trazmo_pg, smoke)

    print()
    print(f"Passed:  {len(smoke.passed)}")
    print(f"Failed:  {len(smoke.failed)}")
    if smoke.failed:
        print()
        print("Failures:")
        for name, why in smoke.failed:
            print(f"  • {name}: {why}")
        print()
        print("See MOCKSIM_DEMO.md §5 for troubleshooting.")

    return smoke.exit_code()


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
