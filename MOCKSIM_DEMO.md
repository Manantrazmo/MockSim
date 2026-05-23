# MockSim ↔ trazmo-platform — End-to-End Demo Runbook

> One sitting. Bring up both stacks, run a single orchestrator command,
> see merchants onboarded in both systems, POS GMV flow into trazmo as
> daily settlements, Flux compute eligibility, an offer get accepted,
> a disbursement land on MockSim's bank surface, and the loop close.

This is the operator's runbook. For architecture, read
[`scripts/README.md`](scripts/README.md) and
[trazmo's DEMO_RUNBOOK.md](../trazmo-platform/scripts/DEMO_RUNBOOK.md).

---

## What this demo proves

| Stage | Source of truth | Verified by |
|---|---|---|
| Merchant onboarding | trazmo `entity` + `acquirer_merchant_mapping` | DB row count after `seed_e2e --run-trazmo-seeds` |
| Master data alignment | MockSim `merchants.acquirer_merchant_id` matches | `GET /api/v1/pos/merchants` returns same IDs |
| POS / GMV | MockSim sim-clock generator | `pos_transactions` row count grows as clock advances |
| Daily settlement webhook | MockSim outbox → trazmo `/acquirer/webhooks/settlement` | `webhook_outbox.status='delivered'` + new `deduction` rows |
| Offer create / accept | trazmo `prism/offers` | Flux UI |
| Disbursement | trazmo `mocksim` adapter → MockSim `payments/initiate` | MockSim pool account balance decreases |
| Repayment allocation | trazmo `deduction` + ledger journal | `advance.outstanding_principal_minor` decreases |

---

## 0. One-time setup

In `D:\Trazmo\trazmo-platform\.env.local` (create if missing) add:

```bash
# Tell trazmo to use the MockSim disbursement adapter
REPAYMENT_ADAPTER=mocksim

# Enable the settlement webhook receiver (Phase-2 endpoint)
ACQUIRER_WEBHOOK_ENABLED=true
ACQUIRER_WEBHOOK_SECRET=mocksim-e2e-webhook-secret-rotate-before-shared

# Point at MockSim
MOCKSIM_BASE_URL=http://host.docker.internal:8080
MOCKSIM_API_KEY=mocksim-e2e-tenant-key-do-not-use-in-prod-aaa
MOCKSIM_POOL_IBAN=             # will be filled in step 4
MOCKSIM_DEFAULT_CREDITOR_IBAN= # optional, defaults to merchant VAN
```

(The `MOCKSIM_*` envvars are read by [`modules/disbursement_adapters/mocksim_adapter.py`](../trazmo-platform/modules/disbursement_adapters/mocksim_adapter.py).
Empty `MOCKSIM_POOL_IBAN` is fine for `REPAYMENT_ADAPTER=bank` mode, but the
`mocksim` adapter will refuse to start without it — fail-fast on purpose.)

---

## 1. Boot both stacks

**Terminal A — MockSim:**

```bash
cd D:\Trazmo\MockSim
docker compose up -d
# Wait until /health returns 200
curl http://localhost:8080/health
```

**Terminal B — trazmo Postgres + TigerBeetle:**

```bash
cd D:\Trazmo\trazmo-platform
docker-compose up -d db redis tigerbeetle
```

**Terminal C — trazmo API:**

```bash
cd D:\Trazmo\trazmo-platform
venv\Scripts\activate
uvicorn main:app --port 8000 --reload
```

**Terminal D — trazmo Celery (powers Flux scan + offer dispatch):**

```bash
cd D:\Trazmo\trazmo-platform
venv\Scripts\activate
celery -A celery_app worker -Q critical,default,low --loglevel=info
```

**Terminal E — Flux UI:**

```bash
cd D:\Trazmo\trazmo-platform\frontends\flux
npm run dev   # http://localhost:5173
```

---

## 2. Seed everything in one command

```bash
cd D:\Trazmo\MockSim
docker compose exec mocksim python scripts/seed_e2e.py \
    --run-trazmo-seeds \
    --num-merchants 25 \
    --history-months 6 \
    --advance-days 1
```

What this does:

1. Verifies both stacks are reachable.
2. Runs trazmo's `seed_dev` (foundation) + `seed_mock_gmv` with **25 merchants
   × 6 months of historical GMV** — that's the backlog Flux's risk scanner
   uses to compute eligibility.
3. Reads the 25 `(entity_id, acquirer_merchant_id, partner_code)` tuples
   from trazmo's postgres.
4. Creates a MockSim tenant with the same `partner_code` and mirrors all 25
   merchants with matching `acquirer_merchant_id`s.
5. Subscribes MockSim's POS surface to
   `http://host.docker.internal:8000/api/v1/acquirer/webhooks/settlement`
   with `format=trazmo_settlement`. The `X-Tenant-ID` header is set to
   trazmo's tenant UUID.
6. Advances MockSim's sim clock 1 day — generating fresh POS transactions
   and firing a settlement webhook batch to trazmo.

Re-runnable. Pass `--advance-days 7` for a week of fresh GMV.

The output ends with a credentials block — paste the Admin Token and Tenant
API Key into MockSim's dashboard settings at <http://localhost:8080/ui/>.

---

## 3. Fill in the disbursement pool IBAN

The seed printed a pool account IBAN like `PK70SCBL5250147507663064`.
Copy it into trazmo's `.env.local` and restart trazmo's API:

```bash
# .env.local
MOCKSIM_POOL_IBAN=PK70SCBL5250147507663064
```

Restart `uvicorn` in Terminal C so the new env is picked up.

---

## 4. Watch the loop

### 4a. Confirm the settlement webhook delivered

```bash
curl -H "Authorization: Bearer $MOCKSIM_ADMIN_TOKEN" \
    "http://localhost:8080/api/v1/admin/outbox?status=delivered" | jq '.items[0]'
```

You should see a `pos.batch.settled` event with `target_url`
ending in `/acquirer/webhooks/settlement`.

### 4b. Confirm trazmo wrote deductions

In a psql connected to trazmo's db (port 5433):

```sql
SELECT settlement_date, COUNT(*), SUM(amount_minor)
  FROM deduction
 WHERE source = 'ACQUIRER_ADAPTER'
 GROUP BY settlement_date
 ORDER BY settlement_date DESC;
```

(Source is `ACQUIRER_ADAPTER` because the webhook receiver is part of the
acquirer adapter pipeline. The `MOCKSIM_ADAPTER` source applies only to
direct `record_repayment()` calls, not webhook-driven ones.)

### 4c. Run Flux's eligibility scan

In the Flux UI at <http://localhost:5173/workflows>:
1. Open the `MA_MERCHANT_SCAN_V1` risk workflow.
2. Click "Run scan". Watch the candidate list populate.
3. For any eligible merchant, click "Generate offer" — trazmo creates a
   `prism/offers` row in `CREATED` state.
4. Click "Accept on behalf" (demo shortcut) → offer transitions to
   `ACCEPTED`, advance is created in `PENDING_DISBURSEMENT`.

### 4d. Disburse — the loop closes

From the Flux advance detail page, click "Disburse now". trazmo's
`mocksim` adapter:

1. Sends `POST http://host.docker.internal:8080/api/v1/bank/payments/initiate`
2. MockSim queues the payment and debits the pool account.
3. trazmo records a `disbursement_request` row with
   `status=COMPLETED`, `adapter_kind=MOCKSIM`.
4. Advance status flips to `ACTIVE`.

Verify on MockSim:

```bash
curl -H "Authorization: Bearer $MOCKSIM_API_KEY" \
    "http://localhost:8080/api/v1/bank/accounts/$MOCKSIM_POOL_IBAN/balance"
```

Balance should be **down by the advance principal**.

### 4e. Repayment via further GMV

```bash
docker compose exec mocksim python scripts/seed_e2e.py --advance-days 7
```

Settlement webhooks for the next 7 sim-days flow into trazmo. trazmo's
allocation engine deducts from active advances. Watch
`advance.outstanding_principal_minor` decrease.

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `seed_e2e.py` "Trazmo postgres not reachable" | trazmo's `db` container isn't running | Terminal B step |
| Subscription HTTP 422 INVALID_TARGET_URL | localhost in URL (blocked by SSRF guard) | Use `host.docker.internal` |
| Settlement webhook 422 from trazmo | `ACQUIRER_WEBHOOK_ENABLED=false` in trazmo `.env.local` | Set to `true`, restart API |
| Settlement webhook 401 | HMAC secret mismatch | Make MockSim's `--webhook-secret` match `ACQUIRER_WEBHOOK_SECRET` |
| Disbursement returns ADAPTER_UNAVAILABLE | MockSim not running or wrong base URL | `docker compose up -d` in MockSim; check `MOCKSIM_BASE_URL` |
| "MOCKSIM_BASE_URL must be set" on trazmo startup | adapter selected but URL empty | Add to `.env.local`; or set `REPAYMENT_ADAPTER=bank` |
| Flux scan finds 0 eligible merchants | No historical GMV in trazmo | Re-run `seed_e2e.py --run-trazmo-seeds --history-months 6` |

---

## 6. Run the smoke check

For an automated end-to-end verification that prints ✓/✗ at each step:

```bash
docker compose exec mocksim python scripts/smoke_e2e.py
```

Exits 0 if every stage of the loop passes, non-zero otherwise. Run it after
the first `seed_e2e.py` to confirm wiring before driving the Flux UI.
