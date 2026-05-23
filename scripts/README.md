# MockSim — Scripts

Operational scripts you run from your dev machine against a locally-running
MockSim stack (`docker compose up`). Each script is self-contained — no
package install needed beyond `httpx`.

## `seed_default.py` — bootstrap a working dev environment

Populates MockSim with a sensible default world so the dashboard isn't empty
and so you have something to point trazmo-platform at:

- A default tenant with a known API key
- One merchant per enabled region (PK / AE / SA / EG / BH)
- A disbursement pool account + two merchant virtual accounts (VANs)
- Webhook subscriptions for the POS and Bank surfaces pointing at trazmo-platform
- Scenario engine enabled (so you can inject failures)
- One sample POS sale (optional; skip with `--no-sample-txn`)

**Idempotent.** All MockSim write endpoints require an `Idempotency-Key` header,
and the script supplies a deterministic UUIDv5 derived from the resource name —
re-running just returns the cached response. Use `--reset` to wipe and start
clean.

### Run

```bash
# From the host, against the running docker-compose stack:
pip install httpx
python scripts/seed_default.py

# Or, no host install — run inside the container:
docker compose exec mocksim python scripts/seed_default.py
```

By default the script reads `MOCKSIM_ADMIN_TOKEN`, `ENABLED_REGIONS`,
`TRAZMO_WEBHOOK_BASE_URL`, `TRAZMO_POS_WEBHOOK_PATH` and
`TRAZMO_BANK_WEBHOOK_PATH` from `.env`. Override any of them with CLI flags:

```bash
python scripts/seed_default.py \
  --mocksim http://localhost:8080 \
  --trazmo http://localhost:8000 \
  --regions PK,AE,SA \
  --reset
```

### Output

The pretty (default) output ends with a paste-ready credentials block:

```
────────────────────────────────────────────────────────────────────────
Seed complete.
────────────────────────────────────────────────────────────────────────

  Dashboard:     http://localhost:8080/ui/
  API docs:      http://localhost:8080/docs
  Health:        http://localhost:8080/health

  Paste these into the Settings page of the dashboard:
    Admin Token       = dev-admin-token-mocksim-2026
    Tenant API Key    = mocksim-default-tenant-key-do-not-use-in-prod-32c
  …
```

For pipelines / IDE integrations, use `--json` to get a machine-readable
summary on stdout instead.

---

## Wiring MockSim → trazmo-platform

MockSim is the simulated bank/acquirer; trazmo-platform is the lender that
consumes its events.

### What the seed script already wires

1. **Tenant + API key on MockSim** — trazmo-platform will use this key as
   `Authorization: Bearer …` when it calls MockSim's bank APIs
   (`POST /api/v1/bank/payments/initiate`, `GET /api/v1/bank/accounts/{iban}/balance`,
   etc.). Configure on trazmo's side as `MOCKSIM_API_KEY`.
2. **Outbound webhook subscriptions on MockSim** — POS and Bank events will be
   POSTed to `${TRAZMO_WEBHOOK_BASE_URL}${path}` with an HMAC-SHA256 signature.
   The signing secret is the `--webhook-secret` you passed (default in
   `seed_default.py`) and must match `ACQUIRER_WEBHOOK_SECRET` (or whatever
   per-surface secret you set) on trazmo's side.

### What it doesn't wire — known gap

trazmo-platform's Phase-2 settlement receiver lives at:

    POST /api/v1/acquirer/webhooks/settlement
    Headers: X-Acquirer-Signature, X-Tenant-ID
    Body:    { "partner_code": str, "settlements": [...] }

and is gated by `ACQUIRER_WEBHOOK_ENABLED=true` on the trazmo side
(`shared/config.py:264`). MockSim's outbound webhook envelope (per surface
event) does **not** yet match that settlement schema. Until a translator
layer is added (either a settlement-batching emitter inside MockSim, or a
small adapter service in front of trazmo's receiver), trazmo will return
422 to settlement deliveries. The subscriptions still flow through MockSim's
outbox / retry / replay machinery, so the rest of the loop is exercised end
to end.

### Trazmo-platform env file additions (manual, not wired by seed script)

Add to trazmo-platform's `.env.local`:

```bash
# Talk to MockSim as if it were the real bank/acquirer
MOCKSIM_BASE_URL=http://host.docker.internal:8080
MOCKSIM_API_KEY=mocksim-default-tenant-key-do-not-use-in-prod-32c

# Receive settlement webhooks from MockSim (Phase 2 receiver)
ACQUIRER_WEBHOOK_ENABLED=true
ACQUIRER_WEBHOOK_SECRET=default-webhook-secret-rotate-before-shared-use

# Use the acquirer-style adapter so disbursement requests actually call out
REPAYMENT_ADAPTER=acquirer
```

(The `MOCKSIM_BASE_URL` / `MOCKSIM_API_KEY` keys are not yet read by any
trazmo code — they're added in anticipation of the outbound HTTP client
work tracked in trazmo-platform's TODOs. The settlement-receiver keys
*are* live today.)

### Network notes

When MockSim runs in docker-compose and trazmo-platform runs natively on
the host (or vice versa), they reach each other via `host.docker.internal`
on Docker Desktop. On Linux without Docker Desktop, add
`extra_hosts: ["host.docker.internal:host-gateway"]` to the mocksim service
in `docker-compose.yml`, or use the host's LAN IP. If both stacks run in
Compose, attach them to a shared external network.
