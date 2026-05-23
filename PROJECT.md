# Trazmo — End-to-End Demo Project

> **Living document.** Both Claude and humans append to this file across
> sessions. The intent: a single source of truth for *what we're building*,
> *what's working today*, and *what's the next move*. Keep edits
> surgical — add to the "Session log" section below, don't rewrite history.

**Owner:** Asad Khan (`asad.khan@trazmo.dev`)
**Demo recipient email** for test notifications: `asadkhan4230@gmail.com`
**Last updated:** 2026-05-23

---

## 1. The vision in one paragraph

Trazmo is a fintech lending platform that gives short-term Merchant Advances
to SMEs against their POS sales. The platform consists of two repos:

- **trazmo-platform** — the lender. Onboards SMEs, computes eligibility
  in Flux's risk workflow, generates offers, accepts loan applications,
  approves, disburses, and reconciles repayments. Modular monolith on
  FastAPI + Postgres + TigerBeetle ledger.
- **MockSim** — the simulated bank + acquirer. Pretends to be a card
  acquirer pumping settled POS receipts, and a bank holding the lender's
  disbursement pool. Lets us demo and test the *entire* loan lifecycle
  without touching a real bank rail.

The end-to-end story is: an admin onboards an SME → POS sales flow in →
risk engine decides the SME is eligible → offer goes out → SME accepts →
loan is disbursed from the lender's pool to the merchant's bank account →
ongoing settlements are automatically deducted to repay the loan → loan
closes.

---

## 2. System map (5 frontends + 2 backends + 4 infra services)

| # | System | URL | Role | Stack |
|---|---|---|---|---|
| 1 | **Orbit** | http://localhost:5174 | Platform admin — ledgers, COA, entities, tenants, lenders, partners | React 19 + Vite |
| 2 | **Flux** | http://localhost:5173 | Lender portal — workflows, scorecards, offers, advances, disbursements | React + Vite |
| 3 | **Leadflow** | http://localhost:5175 | Lead intake / partner upload | React + Vite |
| 4 | **SME** | http://localhost:5176 | Borrower portal — see offers, accept, sign | React + Vite |
| 5 | **MockSim UI** | http://localhost:8080/ui/ | Bank + acquirer simulator dashboard | React + Vite (bundled into FastAPI image) |
| 6 | **Trazmo API** | http://localhost:8000 (`/docs` for Swagger) | The lender's FastAPI monolith | FastAPI / SQLAlchemy 2.0 async |
| 7 | **MockSim API** | http://localhost:8080 (`/docs` for Swagger) | Bank + acquirer simulator | FastAPI |
| 8 | Trazmo Postgres | `localhost:5433` db=`trazmo_platform` user=`trazmo` | Lender DB | postgres:16 |
| 9 | Trazmo Redis | `localhost:6379` | Celery broker | redis:7 |
| 10 | TigerBeetle | `localhost:3001` | Authoritative ledger | tigerbeetle 0.17.2 |
| 11 | MockSim Postgres | `localhost:5432` db=`mocksim` user=`mocksim` | MockSim DB | postgres:15 |

---

## 3. The complete demo flow (the script)

### Stage A — Platform admin (Orbit)
**URL:** http://localhost:5174  •  **Persona:** "I'm the founder / platform admin"

What admin should be able to do (target state):

1. Log in (super-admin account from `seed_dev.py`)
2. **See all financial entities** — every Tenant, Lender, Partner, SME, Borrower
3. **See the Chart of Accounts** — every account in TigerBeetle, balances, currency
4. **See all Ledgers** — every journal entry, transfer, balance per account
5. **Manage Lenders & Partners** — list, view, edit, **add new**
6. **Manage Products** — loan products, scorecards, risk workflows (generic master data)
7. **Issue and view credentials** for any entity — generate a one-time login link, **email it to the entity's contact**
   - Demo: emails go to `asadkhan4230@gmail.com` regardless of stored address.
8. Audit log of everything that's been changed

> **Reality check** (see §6 Gaps): Orbit today has skeleton pages for some
> of this. The credential-email flow is **not built yet**. Manual entity
> create/edit exists for some entities but not all.

### Stage B — Lender baseline (Flux, empty state)
**URL:** http://localhost:5173  •  **Persona:** "I'm a lender ops user"

After Orbit setup, opening Flux should show:

- Master data (populated by `seed_dev` and `seed_merchant_advance_products`):
  - Loan products (e.g., `MERCHANT_ADVANCE_V1`)
  - Scorecards
  - Risk workflows (e.g., `MA_MERCHANT_SCAN_V1`)
  - Pools
- **Empty** sections:
  - No merchants
  - No offers
  - No advances
  - No deductions

### Stage C — Onboard one SME (MockSim UI)
**URL:** http://localhost:8080/ui/

What MockSim UI should do (target state):

1. Show **all lenders** loaded from trazmo (`GET /api/v1/admin/entities?type=LENDER`).
2. Form to **add a single SME** — name, region, MCC, CNIC/registration #, expected daily volume, contact email, plus upload one or two dummy docs (CNIC scan, bank statement).
3. "Generate" button — submits to trazmo's onboarding API
   (`POST /api/v1/leadflow/merchant-advance/merchant-profiles` +
   `acquirer-mappings`) AND mirrors the merchant into MockSim's own
   `merchants` table with matching `acquirer_merchant_id`.
4. The merchant immediately appears in Flux.

### Stage D — Onboard a batch (MockSim UI)

5. Bulk mode: pick a region + count → fire the same onboarding for N SMEs.
6. Some merchants intentionally left in "draft" state so Flux can show
   in-progress vs onboarded.

### Stage E — Generate POS data (MockSim UI)

7. Pick one or many existing SMEs → "Generate POS for next N days"
8. MockSim's sim-clock generator runs → daily settlement webhooks fire
   to trazmo's `/api/v1/acquirer/webhooks/settlement` → trazmo writes
   `deduction` rows tied back to the right entity via `acquirer_merchant_id`.
9. Trazmo's `business_metrics` API reflects the new GMV
   (this is what Flux's risk scanner reads).

### Stage F — Lender runs eligibility (Flux)

10. Open `MA_MERCHANT_SCAN_V1` workflow → "Run scan"
11. Workflow reads each merchant's GMV history, scores via scorecard,
    selects qualifying SMEs.
12. For each, click "Generate offer" → row appears in `prism.offer` with status `CREATED`.

### Stage G — SME accepts (SME portal)
**URL:** http://localhost:5176

13. SME logs in with creds emailed during Stage A/C (email to `asadkhan4230@gmail.com`)
14. Sees pending offer → reviews terms → "Accept"
15. Offer transitions `CREATED → ACCEPTED`; advance row created in `PENDING_DISBURSEMENT`
16. Loan application form opens (sections driven by product's `document_requirements`)
17. SME completes form, e-signs (OTP) → application transitions `DRAFT → SUBMITTED`

### Stage H — Lender approves + disburses (Flux)

18. Lender reviews application → "Approve"
19. Status → `APPROVED`
20. "Disburse now" button → trazmo's `mocksim` adapter calls
    `POST http://host.docker.internal:8080/api/v1/bank/payments/initiate`
21. MockSim debits the pool account → records the credit on the merchant's VAN
22. Disbursement row in trazmo → `COMPLETED`; advance → `ACTIVE`

### Stage I — Bank's view (MockSim UI)

23. MockSim dashboard shows pool balance decreased, merchant VAN credited
24. Statement view of the merchant VAN shows the incoming disbursement
25. Subsequent GMV → daily settlement → next webhook batch → deductions
    against the active advance → outstanding principal decreases
26. When `outstanding_principal_minor` hits 0 → advance → `CLOSED`

### Stage J — Reconciliation (Flux + Orbit)

27. Flux portfolio dashboard shows the advance lifecycle
28. Orbit Ledger view shows the matching TigerBeetle journal entries
29. Reports / P&L roll up the demo period

---

## 4. Boot order (what to start in what order)

When everything is freshly stopped:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  1. Trazmo infra (Postgres :5433, Redis :6379, TigerBeetle :3001)       │
│     cd D:\Trazmo\trazmo-platform                                        │
│     docker compose --profile init up tigerbeetle-init   # first time    │
│     docker compose up -d db redis tigerbeetle                           │
│                                                                          │
│  2. Trazmo migrations (idempotent)                                       │
│     venv\Scripts\python.exe -m alembic upgrade head                     │
│                                                                          │
│  3. Trazmo API (:8000)                                                   │
│     venv\Scripts\python.exe -m uvicorn main:app --port 8000             │
│                                                                          │
│  4. Trazmo Celery (powers Flux scans + offer dispatch)                  │
│     venv\Scripts\python.exe -m celery -A celery_app worker              │
│       -Q critical,default,low --loglevel=info -P solo                   │
│                                                                          │
│  5. MockSim (:8080)                                                      │
│     cd D:\Trazmo\MockSim                                                 │
│     docker compose up -d                                                 │
│                                                                          │
│  6. Frontends (parallel — npm workspace)                                 │
│     cd D:\Trazmo\trazmo-platform\frontends                              │
│     npm run dev:flux       # :5173                                       │
│     npm run dev:orbit      # :5174                                       │
│     npm run dev:leadflow   # :5175                                       │
│     npm run dev:sme        # :5176                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

> **Why this order:** TigerBeetle has to exist before alembic (some
> migrations register accounts on it). Migrations have to be at head before
> API boots (lifespan does a sanity check). Celery and the API both need
> Postgres + Redis but are otherwise independent. Frontends only need the
> API healthy. MockSim is standalone — boot it whenever; the only
> cross-dependency is the seed orchestrator wiring the two together.

### Quick health check (after boot)

```bash
curl http://localhost:8000/health    # trazmo
curl http://localhost:8080/health    # mocksim
for p in 5173 5174 5175 5176; do curl -s -o /dev/null -w "$p: %{http_code}\n" http://localhost:$p; done
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

### Seed everything in one shot

After all 6 services above are healthy:

```bash
cd D:\Trazmo\MockSim
docker compose exec mocksim python scripts/seed_e2e.py \
    --run-trazmo-seeds \
    --num-merchants 25 --history-months 6 --advance-days 1
```

This is the single command that connects the two systems. See
[`MockSim/MOCKSIM_DEMO.md`](MockSim/MOCKSIM_DEMO.md) for the full runbook.

---

## 5. Where we are today (current state, 2026-05-23)

### What's working end-to-end ✅

- **Both backends boot and pass tests** — trazmo (full suite green),
  MockSim (112/112).
- **MockSim seeds itself** with default tenant + merchant + pool account
  via `scripts/seed_default.py`.
- **Unified seed orchestrator** (`scripts/seed_e2e.py`) wires the two
  systems: drives trazmo's `seed_dev` + `seed_mock_gmv`, mirrors merchants
  into MockSim with matching `acquirer_merchant_id`, subscribes the
  trazmo-shaped settlement webhook.
- **Identity bridge** is real: `acquirer_merchant_id` is the single
  string that links a trazmo entity to a MockSim merchant to a settlement
  webhook payload.
- **Settlement webhook contract is byte-exact** — MockSim emits
  `{partner_code, settlements:[{acquirer_merchant_id, settlement_date_iso,
  gross_amount_minor, currency_code}]}` with `X-Acquirer-Signature` +
  `X-Tenant-ID` headers exactly as trazmo's
  `/api/v1/acquirer/webhooks/settlement` receiver expects. 4 contract tests
  guard this.
- **Disbursement adapter** (`REPAYMENT_ADAPTER=mocksim`) calls MockSim's
  `/api/v1/bank/payments/initiate` from trazmo. 4 unit + 4 DB-gated tests.
  Currently sitting on PR [#58](https://github.com/Trazmo/trazmo-platform/pull/58)
  in the trazmo repo.
- **SME Approve from Flux Onboarding Management** — branches the legacy
  bike-finance approval flow by `entity_type.code`. For SME borrowers, the
  flow writes an `onboarding_approval` audit row and stops there (no
  loan_application is created; that happens later when the SME accepts an
  offer in the SME portal). For INDIVIDUAL borrowers the original
  bike-product matcher path is unchanged. Live on PR
  [#59](https://github.com/Trazmo/trazmo-platform/pull/59) (commit
  `84ae874`). Toast copy distinguishes the two: "Offers will appear after
  the next risk-workflow scan" vs "Loan application was created".

### What's stubbed / partial 🟡

- **Email out of credentials** to entity contacts: not implemented.
  `asadkhan4230@gmail.com` is the demo target, no transport configured.
- **Orbit ledger / COA / entity views**: some skeleton screens exist;
  comprehensive "see every financial entity" view needs wiring.
- **MockSim UI "Add SME" form**: doesn't exist — today MockSim is mostly
  observed via Swagger + the dashboard's clock/outbox/transaction
  read-only views.
- **MockSim UI multi-select POS generation**: not exposed in UI; works
  via `scripts/seed_e2e.py --advance-days N`.
- **Settlement payload batching** across merchants: today one
  `settlements[]` entry per (merchant, day). Wire-correct but louder than
  a real acquirer.
- **Auto-discovery of pool IBAN** in trazmo: today operator copies the
  pool IBAN from `seed_e2e.py` output into trazmo's `.env.local`.

### What's missing 🔴

- **MockSim UI form to onboard one SME** with file upload — Stage C in
  the demo flow. Needs:
  - New page in `dashboard/src/pages/Onboarding.tsx`
  - File upload widget → MockSim's object-storage adapter
  - Submits to trazmo's `POST /api/v1/leadflow/merchant-advance/merchant-profiles`
    AND MockSim's `POST /api/v1/pos/merchants`
- **MockSim UI bulk onboarding** — Stage D
- **MockSim UI multi-select + "generate POS"** — Stage E. Needs:
  - Multi-row selector in `dashboard/src/pages/POSPage.tsx`
  - Calls `/api/v1/admin/clock/advance` (already exists)
- **Orbit: email credentials button** — Stage A point 7
- **SME portal offer-acceptance + signing flow** — Stage G points 13–17.
  Backend exists; UI may need polish.

---

## 6. Gaps & near-term roadmap

Priority bands. Within a band, ordered by smallest-effort-first.

### P0 — Block the demo flow

1. **MockSim UI: onboarding SME form** (Stage C). New page that POSTs to
   both trazmo + MockSim with matching `acquirer_merchant_id`. Use
   `seed_e2e.py`'s logic as the reference contract.
2. **MockSim UI: multi-merchant POS trigger** (Stage E). Page-level
   button that wraps `clock/advance` with a merchant filter.
3. **Orbit: entity master-data view** (Stage A points 2–6). At least:
   list of tenants, lenders, partners, SMEs with click-through to detail.
4. **Orbit: issue credentials → email to `asadkhan4230@gmail.com`**
   (Stage A point 7). MVP: don't even send real email; just print the
   one-time link and use a stub SMTP server (mailhog / mailpit) so we
   can show the email in a UI.

### P1 — Make the demo bulletproof

5. **Auto-resolve `MOCKSIM_POOL_IBAN`** so operator doesn't paste it.
   Either MockSim exposes a known well-formed pool IBAN per partner
   (e.g., always `PK70SCBL.../trazmo-pool-PK`), or trazmo's adapter
   discovers it via `GET /api/v1/bank/accounts?type=pool`.
6. **Email transport** — mailhog/mailpit on `localhost:8025` so the
   credential-email flow is visually demonstrable.
7. **Cross-merchant batching** of settlement payloads (one POST per
   partner per day, not per merchant).

### P2 — Polish

8. **TaskGet-style status page** in Orbit showing all 11 services'
   health at a glance.
9. **Demo data reset script** — single command to wipe both DBs and
   re-seed from scratch.
10. **Authentication wired into all four frontends** (auth-client exists
    but state across them needs alignment).

---

## 7. Identity scheme — the bridge

The single contract that links the two systems:

```
                  acquirer_merchant_id  (string, ≤ 64 chars)
                              │
       ┌──────────────────────┼──────────────────────┐
       ▼                      ▼                      ▼
  trazmo.entity         mocksim.merchants     settlement.webhook
  via acquirer_         (column on            payload.settlements[]
  merchant_mapping)     merchants table)      (every line item)
```

Examples used in the demo seed: `ACQ-00001`, `ACQ-00002`, … `ACQ-00025`
(trazmo's `seed_mock_gmv.py` convention).

Other useful IDs:
- `partner_code` (1:1 with `mock_tenants.partner_code`): names a partner
  programme — e.g., `MOCKSIM_PK_POS`
- `external_entity_id` on MockSim merchants: trazmo's `entity.id` UUID,
  stored for traceability, never resolved by MockSim

---

## 8. Repo layout

```
D:\Trazmo\
├── trazmo-platform\        ← the lender (this PR's other half)
│   ├── modules\disbursement_adapters\mocksim_adapter.py
│   ├── modules\prism\webhooks_acquirer.py      ← receives MockSim's events
│   ├── scripts\
│   │   ├── seed_dev.py                          ← foundation: currencies, tenants
│   │   ├── seed_mock_gmv.py                     ← 25 merchants + 6 months GMV
│   │   ├── seed_e2e_merchant_smoke.py           ← single-SME E2E setup
│   │   ├── DEMO_RUNBOOK.md                      ← pre-MockSim demo script
│   │   └── … (40+ seeds, see ls scripts\)
│   └── frontends\
│       ├── flux\      :5173
│       ├── orbit\     :5174
│       ├── leadflow\  :5175
│       └── sme\       :5176
│
├── MockSim\                ← the bank + acquirer simulator
│   ├── src\mocksim\
│   │   ├── pos\settlement.py                    ← emits trazmo-shaped webhook
│   │   ├── bank\api.py                          ← receives disbursement calls
│   │   ├── admin\api.py                         ← /admin/tenants /admin/stats etc
│   │   └── core\webhook.py                      ← HMAC + protocol switching
│   ├── scripts\
│   │   ├── seed_default.py                      ← single-tenant baseline
│   │   ├── seed_e2e.py                          ← THE orchestrator
│   │   └── smoke_e2e.py                         ← ✓/✗ verifier
│   ├── dashboard\        ← the React UI bundled at /ui/
│   ├── MOCKSIM_DEMO.md   ← runbook
│   └── DESIGN.md         ← full design doc
│
└── PROJECT.md              ← this file
```

---

## 9. Useful one-liners

```powershell
# Status of all containers
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Tail trazmo API log (if started via run_in_background, log path is in /tmp)
Get-Content /tmp/trazmo_api.log -Tail 30 -Wait

# MockSim outbox — what's been sent / failed
curl -H "Authorization: Bearer dev-admin-token-mocksim-2026" `
     "http://localhost:8080/api/v1/admin/outbox?status=delivered" | jq '.items[:5]'

# Trazmo deductions (from MockSim settlements)
docker exec trazmo_db psql -U trazmo -d trazmo_platform -c `
  "SELECT source, COUNT(*), SUM(amount_minor) FROM deduction GROUP BY source"

# Re-run the smoke
docker compose exec mocksim python scripts/smoke_e2e.py --advance-days 1
```

---

## 10. Session log (most recent first)

Each session appends one bullet here with the session date + the headline.
Detail goes in the section above it grows. Keep this terse.

- **2026-05-23 (session 7 — POS loop closed end-to-end)** — Five fixes
  to make MockSim's POS data actually land as GMV history on the trazmo
  side. Symptom: after generate-pos in MockSim, Flux still saw zero
  business metrics for the SME.
  Five root causes, each independent:
  (a) MockSim's `/admin/generate-pos` was generate-only — settlement
  batches were enqueued but never drained for backfilled past dates
  (the SimScheduler only fires when the sim clock advances). Fix: in
  backfill mode, after the generation loop, call
  `settle_merchant_day` inline for each (merchant, date+0..+2) to
  catch every T+1/T+2 cadence.
  (b) MockSim's MOCK_POS_1 tenant had no `webhook_subscriptions` row
  pointing at trazmo. Inserted via SQL — see manual step below; a
  proper seed script lives in `scripts/seed_e2e.py` and can be the
  permanent fix.
  (c) Trazmo's acquirer-webhook route was feature-flagged off via
  `os.environ.get("ACQUIRER_WEBHOOK_ENABLED")`. `os.environ` doesn't
  see `.env` (pydantic-settings does). Switched main.py to read from
  `settings.ACQUIRER_WEBHOOK_ENABLED`.
  (d) Trazmo's settlement handler was a Phase-2 scaffold that
  computed deductions but never persisted GMV history. Added an
  idempotent UPSERT into `merchant_daily_summary` per settlement
  line, BEFORE the deduction allocation. Risk scorer (which reads
  this table) now has real data.
  (e) Flux's Onboarding Management listing showed approved SMEs as
  unapproved when viewed by an unbound super-admin
  (`admin@trazmo.com`). The decision-lookup block was gated on
  `if subs and lid:` which never ran for null `lid`. Refactored to
  load decisions whenever subs exist, only applying the lender
  filter when lid is set.
  Also: bulk-cleaned both DBs (179 trazmo SME entities + descendants;
  39 MockSim merchants + 224 pos_txns) and bulk-onboarded 30 fresh
  "Clean SME 001..030" through MockSim — all 30 bridged, each with 5
  synthetic KYC docs, bound to lender ENT-FLUX-DEFAULT + partner
  MOCK_POS_1. Added an Acquirer-ID column + name/ACQ search box to
  MockSim's POS page so operators can line up merchants between Flux
  and MockSim visually.
  Trazmo commit: `8941218` on PR
  [#59](https://github.com/Trazmo/trazmo-platform/pull/59).
  MockSim commits: `3aa4e9c` (POS page UX) + `784944a` (generate-pos
  + payload).
  **Verified end-to-end:** Clean SME 003 → generate-pos 5 days backfill
  → 4 settlement batches → 4 outbox deliveries (200 OK) → 6 rows in
  trazmo's `merchant_daily_summary` (gross 4.99L–13.53L PKR per day,
  txn count 119–307). Visible at
  http://localhost:5173/business-metrics?tab=daily&entity=387f37b9-93d6-4e0d-9e19-9e84fdbf00e6

  **MORNING PICKUP:**
  1. Verify the stack is still up:
     `curl http://localhost:8000/health; curl http://localhost:8080/health`
     If trazmo is down, restart with the same uvicorn command (cwd
     trazmo-platform). The `.env` already has `ACQUIRER_WEBHOOK_ENABLED=1`
     so no inline env var needed anymore.
  2. The webhook subscription wiring `MOCK_POS_1 → trazmo` was inserted
     manually via SQL (UUID `26ff4ac0-69b9-4a00-9f6c-863d9683485b`).
     If MockSim's DB gets reset, re-insert by running:
     ```sql
     INSERT INTO webhook_subscriptions (id, mock_tenant_id, trazmo_tenant_id,
       surface, target_url, target_secret, event_types, status, format,
       created_at)
     VALUES (gen_random_uuid(),
       'b75cda46-19f0-4881-9ae5-999a17314316',
       '8f65c261-0fe0-4bd5-ac95-3ff46858b9d0',
       'pos',
       'http://host.docker.internal:8000/api/v1/acquirer/webhooks/settlement',
       'dev-signing-secret-change-in-prod-32c',
       '["pos.batch.settled"]'::jsonb,
       'active', 'trazmo_settlement', now());
     ```
     (Better: fold this into `seed_e2e.py` / `seed_default.py`.)
  3. Demo loop to drive next: fire generate-pos for the other 29 SMEs
     from MockSim's POS page (multi-select + Generate POS, 30 days
     backfill). Then in Flux → Risk Workflows → pick the workflow
     matching the SMEs' MCC → Run Auto-Scan Now. Eligible SMEs get
     `loan_offer(CREATED)` rows. Then SME portal :5176 → accept →
     loan_app → approve in Flux → disburse via mocksim adapter.
  4. Open issue for tomorrow: the legacy `seed_dev.py` WASL bike-finance
     leads (`Ali Raza / WASL-2026-0000{1,2,3}`) still show in Flux
     Onboarding Management. Filter them out or delete them too if
     they're cluttering the demo.

- **2026-05-23 (session 6 — SME approve unblocked)** — Approving any
  MockSim-onboarded SME from Flux 400'd with "No matching active
  lender_product for this lead's plan." Root cause: `run_portal_onboarding_approve`
  was a verbatim port of the legacy bike-finance flow and required a
  unique `lender_product` matchable on `(plan_model_name, tenure, DP,
  monthly)`. SMEs from MockSim don't have a plan picked at approve time
  — they get per-channel offers from the risk-workflow scanner later
  and the loan_application only materialises on SME acceptance. Fix:
  branch by `entity_type.code` in
  `modules/onboarding/services/approval_flow.py`. SME borrowers get an
  `OnboardingApproval` audit row (gate=POST_ONBOARDING, decision=APPROVED,
  conditions_json marks `next_step: risk_workflow_scan`) and the call
  returns — no loan_application, no financial_terms, no credit_ops_case.
  Bike-finance lead path unchanged. Flux toast now shows "Onboarded.
  Offers will appear after the next risk-workflow scan." for SMEs.
  Committed as `84ae874` on PR
  [#59](https://github.com/Trazmo/trazmo-platform/pull/59) and pushed.
  **Next session pickup:** the SME is now sitting in trazmo with status
  APPROVED but no offers yet. Either (a) wait for the periodic
  risk_workflow scanner Celery beat tick, or (b) hit "Run Auto-Scan Now"
  on the workflow's Flux detail page. That's how offers appear in the
  SME portal (`:5176`). After that → SME accepts → loan_app → approve →
  disburse → settlement webhooks → repayment ledger. See Stages F-J above.

- **2026-05-23 (session 5 — Phase I)** — Bulk onboard ULID collision +
  synthetic KYC documents. Two issues hit during the bulk-SME demo:
  (1) `merchants.id = f"MID_{new_ulid()[:8]}"` collided whenever two
  bulk inserts landed in the same millisecond — ULID's first 8 chars
  are timestamp-only. Fixed by using the random tail of the ULID
  instead. (2) Operator asked for dummy KYC documents on onboarding.
  New `mocksim.synth.documents` generator produces region-aware CNIC /
  Emirates-ID / Saudi-ID / Bahrain-CPR / Egypt-ID + NTN + bank
  statement metadata + business registration + utility bill, all with
  plausible numbers (PK CNIC format `42101-1234567-8`, NTN `1217679-2`,
  etc.) seeded by `acquirer_merchant_id` so re-runs are reproducible.
  Migration 0005 adds `merchants.synthetic_documents` JSONB. Frontend:
  "Generate dummy KYC documents" toggle + per-type override chips +
  expandable doc list in the recent-onboardings activity log. 112/112
  tests pass. Verified: bulk-5 onboard with documents produces unique
  IDs and 5 docs each.

- **2026-05-23 (session 4 — Phase H)** — Service auth for cross-system
  writes. Previously MockSim wrote directly to trazmo's postgres via
  asyncpg INSERTs — anyone with network access to :5433 could do
  anything, no audit, no validation. Replaced with an authenticated HTTP
  surface on trazmo: `/api/v1/_internal/mocksim/{bootstrap,lenders,smes,
  onboard-merchant}`, gated on `Authorization: Bearer
  <MOCKSIM_SERVICE_TOKEN>` with constant-time comparison. The router is
  *opt-in* — empty config returns 401 for every request, so a
  misconfigured deployment can't accidentally expose it. Trazmo's
  `create_merchant_profile` and `upsert_acquirer_mapping` service
  functions are called so audit events fire as if a human did it.
  MockSim's `mocksim.trazmo.client` rewritten to use httpx instead of
  asyncpg — same public dataclasses + function names, callers
  unchanged. Two new env vars: `MOCKSIM_SERVICE_TOKEN` on the trazmo
  side, `TRAZMO_API_URL` + `TRAZMO_SERVICE_TOKEN` on MockSim. End-to-end
  verified: 401 on no/wrong token, 200 on correct token, `ACQ-00027`
  created via the authenticated path in both systems. 112/112 tests
  pass. PR for trazmo side: claude/mocksim-service-api (stacks on
  PR #58).

- **2026-05-23 (session 3 — Phase G)** — Proper session auth. Replaced
  the "paste admin token + tenant API key into localStorage" UX with a
  username + password login that returns an HTTP-only signed cookie
  (Starlette SessionMiddleware + bcrypt + itsdangerous). New
  `admin_users` table (migration 0004), bootstrap creates default
  `admin`/`admin` on first start (configurable via
  `MOCKSIM_BOOTSTRAP_PASSWORD`). New endpoints `POST /auth/login`,
  `POST /auth/logout`, `GET /auth/me`. TenancyMiddleware accepts EITHER
  session cookie (humans, dashboard) OR bearer token (service callers,
  scripts, trazmo's adapter) — backward-compat preserved. For tenant
  endpoints, admins act-as via top-bar tenant picker → `X-Act-As-Tenant`
  header. Settings page rewritten to surface account info + live
  connectivity probes instead of token paste fields. End-to-end
  verified: login OK, session cookie set, `/admin/stats` works without
  bearer, `/pos/merchants` works via session+act-as header, bearer
  fallback still works for scripts. 112/112 tests pass.

- **2026-05-23 (session 2 — Phase F)** — Cross-system SME onboarding.
  New `mocksim.trazmo` backend module talks directly to trazmo's
  postgres. Three new admin endpoints: `GET /admin/tenants`,
  `POST /admin/onboard-sme` (creates entity+sme_profile+merchant_profile+
  acquirer_mapping in trazmo AND merchant row in MockSim, in one call),
  `POST /admin/generate-pos` (force-fires generator for selected
  merchants over N days, bypasses sim clock). New `OnboardingPage` in
  MockSim UI with single + bulk modes, lists existing trazmo SMEs in
  real time. POSPage augmented with multi-select checkboxes +
  "Generate POS" toolbar. End-to-end verified: one click in MockSim UI →
  entity appears in trazmo PG (ACQ-00025) → 224 txns generated across
  3 days. 112/112 tests pass.

- **2026-05-23 (session 1)** — Brought MockSim up from cold, fixed five
  bring-up bugs (hatchling editable install, pydantic-settings list parse,
  SQLAlchemy ORM event target, alembic async driver, tenant filter mixin),
  authored `seed_default.py` + `seed_e2e.py` + `smoke_e2e.py`, added
  trazmo bridge IDs (migrations 0002 + 0003), built trazmo settlement
  emitter, added MockSim disbursement adapter in trazmo-platform
  (PR [#58](https://github.com/Trazmo/trazmo-platform/pull/58)), wrote
  `MOCKSIM_DEMO.md` and this file. All 11 services up and responding.

