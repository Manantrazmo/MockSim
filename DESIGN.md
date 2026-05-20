# MockSim — Design Document

A simulation harness for testing **Trazmo**'s lending infrastructure end-to-end
before integrating real bank and acquirer partners. Two simulators in one
service:

1. **POS GMV Generator** — emits credit/debit card sale, refund, and
   chargeback events the way a payment acquirer would, so Trazmo can ingest
   merchant GMV as input to underwriting and recovery logic.
2. **Bank Mock** — exposes core-banking and payment-rail behaviour (account
   balances, disbursement, statements, mandates, direct debits, instant
   credit notifications) so Trazmo can drive disbursement into pool accounts
   and recovery from merchant virtual accounts.

Target markets: **Pakistan, UAE, KSA, Egypt** (extensible to wider MENA).

---

## 1. Guiding principles

| # | Principle | Why |
|---|-----------|-----|
| 1 | **Wire format = ISO 20022 JSON**, not vendor JSON | Every regional rail (Aani, Sarie+, PRISM+, InstaPay) is ISO 20022 native or migrating. Building to the standard means new integrations are mapping work, not rewrites. |
| 2 | **Adapter layer lives in Trazmo, not the mock** | The mock is "one provider." The day Trazmo plugs in Network International or HBL, the change is a new adapter, not a refactor of business logic. |
| 3 | **Clock is controllable** | Lending cycles are 30/60/90 days. The sim must compress them. A wall-clock-only mock is useless after the first sprint. |
| 4 | **Failure injection is first class** | Real-world bugs hide in unhappy paths. Every endpoint accepts an `X-Inject-Scenario` header. |
| 5 | **Idempotency, webhook signing, retries from day one** | These are the integration tax. Get them right in the mock so Trazmo learns them once. |
| 6 | **Two delivery modes for every event**: real-time webhook **and** batch file drop | Mature banks still drop SFTP files. Newer acquirers push webhooks. Trazmo must consume both. |
| 7 | **Don't model what you won't use** | No ISO 8583 binary, no SWIFT MT, no card tokenization vault. Pure JSON-over-HTTPS + CSV/XML batch files. |
| 8 | **Canonical JSON, not "ISO 20022 JSON"** | Wire format is a Trazmo-internal canonical JSON *modelled on* ISO 20022 semantics. ISO 20022 is XML-native; there is no published canonical JSON binding. MockSim emits canonical JSON to Trazmo and serializes to XML (camt.053, pain.001) only when a provider profile requires it. |
| 9 | **Money in minor units strings** | All monetary amounts in API payloads are decimal-free integer strings in the currency's minor units (paisas/fils/halalah) per Trazmo CLAUDE.md rule 13. PKR 12,500 → `"1250000"` (× 100), JOD 1.5 → `"1500"` (× 1000), JPY 100 → `"100"` (× 1). Provider-flavoured fixtures may carry major-units decimals where the real provider sends that — the adapter layer reconciles. |

---

## 2. Regional standards landscape (what the mock must match)

### Pakistan
- **RTGS**: PRISM (SBP-operated, migrating to **PRISM+** on ISO 20022)
- **Instant payments**: **RAAST** (SBP), aliases on mobile/CNIC, RTP supported
- **Retail clearing**: 1LINK (switch), NIFT (batch)
- **Card schemes**: PayPak (domestic), Visa, Mastercard, UnionPay
- **Acquirers**: HBL, UBL, Bank Alfalah, Meezan, Keenu, NayaPay
- **Recovery rail**: 1LINK Direct Debit / RAAST RTP
- **IBAN**: 24 chars, `PK` prefix (e.g. `PK36SCBL0000001123456702`)
- **Currency**: PKR

### UAE
- **RTGS**: UAEFTS (CBUAE)
- **Instant payments**: **Aani** (ISO 20022 native, launched 2023)
- **Direct debits**: UAEDDS (UAE Direct Debit System) — primary recovery rail
- **Card schemes**: UAE Switch, Visa, Mastercard
- **Acquirers**: Network International, Magnati, Mashreq Neo, Telr
- **WPS**: Wage Protection System (for payroll-linked lending)
- **IBAN**: 23 chars, `AE` prefix
- **Currency**: AED

### KSA
- **RTGS**: SARIE (migrating to **Sarie+** ISO 20022)
- **Instant payments**: IPS (SAMA, ISO 20022)
- **Card scheme**: **mada** (domestic, mandatory routing for in-KSA transactions), Visa/MC for cross-border
- **Bill payments**: SADAD
- **Acquirers**: Geidea, HyperPay, PayTabs, Network International KSA
- **VAT**: 15% on MDR — must appear in settlement payload
- **IBAN**: 24 chars, `SA` prefix
- **Currency**: SAR

### Egypt
- **RTGS**: RTGS (CBE)
- **Instant payments**: **InstaPay** (ISO 20022)
- **Card scheme**: **Meeza** (domestic), Visa, Mastercard
- **Acquirers**: Paymob, Fawry, ClickPay, NBE
- **IBAN**: 29 chars, `EG` prefix
- **Currency**: EGP

### Regional commonalities
- Working week: **Sun–Thu** (KSA, Egypt) vs **Mon–Fri** (Pakistan, UAE moved 2022). Settlement windows shift accordingly — model it.
- **Islamic-finance flags** (`sharia_compliant: true|false`, profit-rate vs interest-rate) — many MENA lenders need both modes. Carry the flag on disbursement.
- **VAT on MDR**: KSA 15%, UAE 5%. Settlement payload must split principal / MDR / VAT.
- **WHT** (withholding tax) on certain B2B settlements — Pakistan especially.

---

## 3. Architecture

```
                                  ┌────────────────────────────────┐
                                  │           Trazmo               │
                                  │  ┌────────────┐  ┌──────────┐  │
                                  │  │ Acquirer   │  │ Bank     │  │
                                  │  │ Adapter IF │  │ Adapter  │  │
                                  │  └─────▲──────┘  └────▲─────┘  │
                                  └────────│──────────────│────────┘
                                           │ signed       │ signed
                                           │ webhooks +   │ webhooks +
                                           │ REST polls   │ REST calls
                            ┌──────────────┴──────┐  ┌────┴──────────────┐
                            │  MockSim — POS      │  │  MockSim — Bank   │
                            │  /pos/*             │  │  /bank/*          │
                            └────────┬────────────┘  └────────┬──────────┘
                                     │                        │
                                     └──────────┬─────────────┘
                                                ▼
              ┌────────────────────────────────────────────────────────────┐
              │  Shared core                                               │
              │  • SimClock (controllable, single source of "now")         │
              │  • SimScheduler (sim-time jobs: EOD batch, chargeback     │
              │     maturation, statement) — fires on SimClock.advance()  │
              │  • APScheduler (real-time jobs ONLY: webhook outbox       │
              │     poller backoff, real HTTP timeouts) — wall-clock      │
              │  • Idempotency store (Postgres, SELECT FOR UPDATE)         │
              │  • Accounts (balance + ordered entries — NOT double-entry; │
              │     Trazmo's Vertex is the authoritative ledger)           │
              │  • Webhook outbox (Postgres table, same txn as accounts;   │
              │     separate poller, at-least-once, per-aggregate ordered) │
              │  • Scenario engine (middleware + decorator pattern)        │
              │  • Persistence (Postgres only, Alembic)                    │
              │  • Multi-tenancy (API key → mock_tenant_id; passthrough    │
              │     header X-Trazmo-Tenant-Id for end-to-end isolation     │
              │     testing in Trazmo's own tenancy)                       │
              └────────────────────────────────────────────────────────────┘
                                                │
                                  ┌─────────────┴─────────────┐
                                  │  Admin UI / CLI           │
                                  │  /admin/*                 │
                                  │  • Advance clock (sliced) │
                                  │  • Bulk seed merchants    │
                                  │  • Inject scenarios       │
                                  │  • Browse accounts        │
                                  │  • Replay dead webhooks   │
                                  └───────────────────────────┘
```

### Stack
- **Python 3.12 + FastAPI** (chosen)
- **Pydantic v2** for canonical schemas → automatic OpenAPI
- **Postgres only** (via SQLAlchemy 2.x + Alembic). No SQLite. §9 row 2 is authoritative.
- **SimScheduler** (custom, ~80 lines) for sim-time jobs — registers callbacks keyed to `SimClock.now()` and fires them when the clock advances. **APScheduler** strictly for real-wall-clock jobs (outbox poller backoff, real HTTP timeouts). The two are intentionally separate primitives — conflating them is the bug class this design avoids.
- **httpx** for outbound webhook delivery, **tenacity** for retry, outbox poller drives both
- **Faker** + custom MENA/PK data providers (realistic merchant/customer/IBAN/CNIC generation, seeded per `(mock_tenant_id, merchant_id, sim_date)` for determinism)
- **structlog** + OpenTelemetry for observability (the same hooks Trazmo needs in production)
- **testcontainers-postgres** for CI parity

### Folder layout
```
mocksim/
├── DESIGN.md                  # this file
├── README.md                  # quickstart
├── TODOS.md                   # deferred work — see end of doc
├── pyproject.toml
├── docker-compose.yml         # mocksim + postgres + ngrok (dev-only, for webhooks)
├── alembic/                   # migrations
├── src/mocksim/
│   ├── main.py                # FastAPI app entry
│   ├── config.py              # env config, region toggles
│   ├── clock.py               # SimClock — single source of "now"
│   ├── core/
│   │   ├── accounts.py        # balance + ordered entries (NOT double-entry)
│   │   ├── idempotency.py     # Idempotency-Key handling (txn-bound, SELECT FOR UPDATE)
│   │   ├── webhook.py         # signed delivery + outbox poller
│   │   ├── outbox.py          # outbox table model + dispatcher
│   │   ├── sim_scheduler.py   # sim-time job registry (fires on SimClock.advance)
│   │   ├── scheduler.py       # APScheduler wiring (real-wall-clock only)
│   │   ├── scenarios.py       # failure injection engine (middleware + decorator)
│   │   ├── tenancy.py         # API key → mock_tenant_id; session middleware
│   │   ├── money.py           # Money type, currency-aware minor-units conversion
│   │   ├── identifiers.py     # IBAN/RRN/UETR/ARN/STAN/auth_code/mandate_id/VAN/batch_id generation
│   │   └── errors.py          # error envelope + pain.002 reject mapping
│   ├── iso20022/              # canonical message models, grouped by family
│   │   ├── pain.py            # pain.001, pain.002, pain.008, pain.009, pain.013, pain.014
│   │   ├── pacs.py            # pacs.002, pacs.003, pacs.008
│   │   └── camt.py            # camt.052, camt.053, camt.054
│   ├── pos/
│   │   ├── api.py             # /pos/*
│   │   ├── generator.py       # streaming GMV simulation, seeded RNG
│   │   ├── chargeback.py      # delayed chargeback lifecycle
│   │   ├── settlement.py      # T+1/T+2 settlement file builder (JSON/CSV/camt.053)
│   │   └── regions.py         # RegionConfig dataclass + per-region overrides (PK/AE/SA/EG)
│   ├── bank/
│   │   ├── api.py             # /bank/*
│   │   ├── accounts.py        # pool, merchant, virtual accounts (uses core/accounts.py)
│   │   ├── payments.py        # initiate/status (pain.001/002)
│   │   ├── statements.py      # camt.053/052
│   │   ├── notifications.py   # camt.054 push
│   │   ├── mandates.py        # UAEDDS / 1LINK DD / SEPA-style
│   │   ├── instant.py         # Aani / RAAST / IPS / InstaPay
│   │   └── regions.py         # RegionConfig + rail/scheme overrides
│   ├── admin/
│   │   └── api.py             # /admin/* — clock, seed (bulk), inject, replay, recon
│   └── persistence/
│       ├── models.py
│       └── migrations/        # Alembic auto-runs on container startup with advisory lock
└── tests/
    ├── contract/              # Pydantic round-trip + camt.053 XSD + fixture parse + OpenAPI ↔ actual
    ├── scenarios/             # named end-to-end scenarios (7+ in Phase 1; see §8.5)
    ├── property/              # hypothesis property-based: IBAN checksum, money math, idempotency
    └── golden/                # frozen sample payloads (HBL/NI/Geidea/Paymob; one per provider/region)
```

### 3.1 Clock & Scheduling

Two independent primitives. **Conflating them is the bug class this design avoids.**

| Primitive | Drives | Use for |
|---|---|---|
| **SimClock** | Caller-controlled sim time. `now()` returns `SimClock._t`. `advance(duration)` walks `_t` forward in slices (default 24h sim-time per slice). `set(target)` pins. `run(speed)` enables real-time playback at N sim-seconds per real-second. | All business-logic timestamps. Lending cycle simulation. |
| **SimScheduler** | Sim-time event queue. Jobs register `(sim_target_time, callback)`. On `clock.advance(d)`, scheduler iterates due jobs in sim-time order and fires them, one slice at a time. Single-worker deterministic. | EOD batch settlement, daily statement generation, chargeback maturation, mandate retry maturation. |
| **APScheduler** | Real-wall-clock. | Webhook outbox poller backoff, real HTTP timeouts, garbage-collection of expired idempotency entries. **Nothing sim-time touches APScheduler.** |

**Multi-day advance semantics** (F8): `clock.advance(7d)` runs as 7 × 1-day slices. Each slice runs in its own Postgres transaction. Jobs scheduled within a slice fire in sim-time order; ties broken by registration order. If a slice's jobs exceed a configurable wall-clock budget (default 30s), the advance returns `202 Accepted` with a `job_id` and continues asynchronously — caller polls `GET /admin/clock/advance/{job_id}`. Advances over 7d sim-time always return 202 immediately. This protects against `advance(365d)` OOM.

**Determinism contract:** Given the same starting state and the same `clock.advance(d)`, MockSim produces the same outputs every time. RNG is seeded per `(mock_tenant_id, merchant_id, sim_date)` so GMV generation is reproducible. Tests rely on this.

### 3.2 Inline ASCII diagram targets

When implementation begins, these files get inline diagrams (per Trazmo CLAUDE.md preference on diagram maintenance):

- `core/accounts.py` — account state transitions, parent/child VAN relationships
- `core/outbox.py` — outbox state machine (pending → in_flight → delivered | retrying → dead_letter)
- `core/sim_scheduler.py` — clock-advance flow with slice/budget/202 transitions
- `pos/chargeback.py` — chargeback lifecycle with 14–45d branching
- `bank/mandates.py` — mandate lifecycle, bounce reason paths

---

## 4. POS GMV Generator — API contract

### 4.1 Data model

A **Sale** event in canonical (provider-neutral) shape:

```json
{
  "event_id": "evt_01HXYZ...",            // ULID, mock-issued
  "event_type": "sale",                    // sale|refund|chargeback|reversal
  "event_timestamp": "2026-05-20T14:23:11+05:00",
  "region": "PK",
  "acquirer": {
    "name": "mocksim-acquirer",
    "merchant_id": "MID_000123",           // acquirer's MID for merchant
    "sub_merchant_id": "SUB_000123_01",
    "terminal_id": "TID_0099",
    "mcc": "5812"                          // ISO 18245
  },
  "transaction": {
    "txn_id": "txn_01HXYZ...",
    "rrn": "612345789012",                 // 12 digits, ISO 8583 F37
    "stan": "045123",                      // 6 digits, ISO 8583 F11
    "auth_code": "A12B34",                 // ISO 8583 F38
    "arn": "74999996140000000001234",      // 23 digits, acquirer reference number
    "response_code": "00"                  // ISO 8583 F39 (00 = approved)
  },
  "card": {
    "bin": "588845",                       // first 6
    "last4": "1234",
    "network": "PayPak",                   // Visa|Mastercard|PayPak|mada|Meeza
    "country": "PK",
    "is_domestic": true
  },
  "amount": {                              // ALL amounts: minor-units integer strings (rule 9)
    "value": "1250000",                    // PKR 12,500.00 → ×100 (paisa)
    "currency": "PKR",
    "mdr": "18750",                        // 1.5% MDR
    "vat_on_mdr": "0",                     // 0 for PK; 2813 for KSA at 15%
    "wht": "0",                            // withholding if applicable
    "net_settlement": "1231250"            // PKR 12,312.50
  },
  "settlement": {
    "expected_date": "2026-05-21",         // T+1 typical
    "batch_id": null,                      // filled when settled
    "status": "pending"                    // pending|settled|reversed
  },
  "device": {
    "entry_mode": "chip",                  // chip|contactless|mag|ecom|qr
    "is_cnp": false
  }
}
```

### 4.2 Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/pos/merchants` | Onboard a merchant (region, MCC, expected GMV, risk tier) |
| `GET`  | `/pos/merchants/{mid}` | Fetch merchant profile |
| `POST` | `/pos/transactions` | Manually inject a sale/refund/chargeback (admin / test) |
| `GET`  | `/pos/transactions?cursor=&limit=&merchant_id=&from=&to=&status=` | Query — cursor-paginated (Trazmo rule 13) |
| `GET`  | `/pos/settlements/{batch_id}` | Settlement batch detail (JSON) |
| `GET`  | `/pos/settlements/{batch_id}/file?format=csv|mt940|camt053` | Download settlement file |
| `POST` | `/pos/webhooks/subscriptions` | Register a webhook (URL, secret, event types) |

### 4.3 Webhook events pushed to Trazmo

| Event | When | Carries |
|---|---|---|
| `pos.transaction.authorized` | At sale time | Full sale payload above |
| `pos.transaction.settled` | At T+1 EOD batch | Per-txn settlement detail |
| `pos.batch.settled` | Once per merchant per day | Batch summary + per-txn array |
| `pos.refund.initiated` | Refund issued | Refund payload |
| `pos.chargeback.opened` | 30–90d post-sale, configurable | Chargeback with reason code |
| `pos.chargeback.represented` | Lifecycle progression | Updated dispute state |
| `pos.chargeback.finalized` | Lifecycle end | Win/loss + amount |

### 4.4 GMV generation algorithm

Per merchant per simulated day:
1. Daily **txn count** ~ Poisson(λ = `expected_daily_txns` × weekday_mult × seasonality_mult)
2. Per-txn **amount** ~ Lognormal(μ, σ) fit to merchant's average ticket size and dispersion
3. **Refund rate**: Bernoulli(0.01–0.03); refund posted 0–14 days later
4. **Chargeback rate**: Bernoulli(0.001–0.005); chargeback opens 30–90 days later, then 14–45 day dispute lifecycle
5. **Decline rate**: Bernoulli per region (PayPak/mada have lower CNP success than international)
6. **Card mix**: per region — KSA domestic txns must route ≥80% mada (real rule)
7. **Working-week awareness**: KSA/Egypt weekend = Fri/Sat → lower volume
8. **Month-end spike** for B2B-heavy MCCs

All knobs exposed as merchant config; defaults sane per MCC.

**Streaming generation (4B):** Generator is implemented as a Python generator function — yields one txn at a time. Each yielded txn is persisted + outbox-enqueued in a small batch transaction (default batch=100). The generator never buffers a full day's txns in memory. A 90-day advance for 100 merchants at λ=50 = ~450k txns processed as a stream, not a list.

**Deterministic RNG (F11):** Seeded `random.Random(seed)` per `(mock_tenant_id, merchant_id, sim_date)`. Seed = `hash((mock_tenant_id, merchant_id, sim_date_iso))`. Same starting state + same `advance(d)` → same GMV every time. E2E tests in §8.5 rely on this.

### 4.5 Settlement file formats
- **JSON** (canonical) — what Trazmo's adapter consumes
- **CSV** — what most regional acquirers actually drop (one row per txn + a footer)
- **camt.053 XML** — for banks that route via swift-style messaging
- **Provider-flavoured JSON** — one fixture each for Network International, Geidea, Paymob, HBL POS, so Trazmo's adapter layer is exercised against real-shaped payloads

---

## 5. Bank Mock — API contract

### 5.1 Accounts model

**Not double-entry.** Real banks don't expose their internal chart-of-accounts. They expose per-account balance + ordered statement entries. MockSim mirrors that external view. Trazmo's Vertex is the authoritative double-entry ledger (per Trazmo CLAUDE.md rule 1). MockSim does **not** duplicate it.

Three account types, each holding `(balance, currency, sharia_flag, status, entries[])`:

| Type | Owner | Purpose | Parent? |
|---|---|---|---|
| **Pool account** | Trazmo | Holds disbursement funds; receives consolidated acquirer settlement | — |
| **Merchant settlement (virtual) account** | Per merchant | Sub-account of pool; recovery routes here based on narration / VAN | Pool |
| **Merchant external account** | Per merchant | The merchant's actual bank account at another bank (destination of net payouts) | — (off-bank) |

All accounts have IBAN (region-correct, mod-97 valid), BIC, currency, `sharia_flag`, status.

**Account invariant (F3):** `pool.balance == sum(van.balance for van in pool.children)` is asserted after every operation touching a parent or its children. This catches the most likely race condition class (VAN updated, pool not updated) without modeling the bank's full chart of accounts.

**Internal transfers** (between two MockSim-owned accounts) write **paired entries** — a `-amount` entry on source, `+amount` on destination, both linked by `transfer_id`. Gives audit-trail symmetry without enforcing cross-bank double-entry.

**External transfers** (to a merchant external account at another bank): MockSim only updates the source account and emits `bank.debit.notification` (camt.054). The external bank's books are out of scope.

**Currency model (F4):**
- Each account has **exactly one** currency (set at creation, immutable)
- A `pain.001` with `creditor_account.currency != amount.currency` is **rejected** with pain.002 RJCT reason `AC02` (invalid creditor account for instructed currency). MockSim does **not** auto-FX. Trazmo's adapter resolves FX upstream.
- For multi-region tenants: each region needs its own pool account in that region's currency. Pool selection is the caller's job (pain.001 specifies `debtor_account.iban`).
- A future scenario `cross_currency_fx_quote` may simulate FX rails; out of scope v1.

### 5.2 Endpoints (ISO 20022-aligned)

| Method | Path | ISO 20022 equivalent | Purpose |
|---|---|---|---|
| `POST` | `/bank/accounts` | — | Create account (admin) |
| `GET`  | `/bank/accounts/{iban}` | acmt | Account details + status |
| `GET`  | `/bank/accounts/{iban}/balance` | camt.052 | Current + available balance |
| `GET`  | `/bank/accounts/{iban}/statement?from=&to=&cursor=&limit=&format=json|camt053` | camt.053 | Statement — cursor-paginated |
| `POST` | `/bank/payments/initiate` | **pain.001** | Disbursement instruction |
| `GET`  | `/bank/payments/{e2e_id}/status` | **pain.002** | Payment status |
| `POST` | `/bank/instant/credit-transfer` | **pacs.008** over RAAST/Aani/IPS/InstaPay | Instant payment |
| `POST` | `/bank/instant/rtp` | pain.013 | Request-to-Pay (recovery via RAAST) |
| `POST` | `/bank/mandates` | pain.009 / UAEDDS / 1LINK DD form | Create a debit mandate |
| `POST` | `/bank/mandates/{id}/collect` | pain.008 | Trigger a direct debit |
| `GET`  | `/bank/mandates/{id}` | — | Mandate state |
| `POST` | `/bank/virtual-accounts` | — | Allocate a VAN under pool account |
| `POST` | `/bank/webhooks/subscriptions` | — | Register Trazmo's webhook URL |

### 5.3 Webhook events pushed to Trazmo

| Event | When | ISO 20022 equivalent |
|---|---|---|
| `bank.payment.accepted` | After pain.001 validation | pain.002 ACCP |
| `bank.payment.settled` | After rails settle | pain.002 ACSC |
| `bank.payment.rejected` | Validation/funds failure | pain.002 RJCT + reason code |
| `bank.credit.notification` | Funds land in our accounts | **camt.054** |
| `bank.debit.notification` | Funds leave our accounts | camt.054 |
| `bank.statement.available` | EOD per account | camt.053 with file link |
| `bank.mandate.created` | Mandate active | — |
| `bank.mandate.collection.success` | Direct debit collected | pacs.003 / camt.054 |
| `bank.mandate.collection.failed` | DD bounce | pacs.002 + bounce reason |
| `bank.rtp.responded` | RTP pay/decline | pain.014 |

### 5.4 Recovery flow (the one that matters most)

```
Day 0:  Trazmo → /bank/payments/initiate  (disburse to merchant external IBAN)
        ← pain.002 ACCP webhook
        ← pain.002 ACSC webhook  (settled)

Day 1+: Acquirer pushes pos.transaction.settled webhooks
        Net settlement amount lands in pool's merchant-VAN
        ← bank.credit.notification (camt.054) with VAN in narration
        Trazmo splits: keep recovery cut, push remainder to merchant external

Day N:  Trazmo → /bank/instant/credit-transfer (merchant net payout)
        OR  /bank/mandates/{id}/collect (if mandate-driven recovery)
        ← bank.credit.notification (merchant)
        ← bank.mandate.collection.success OR failed (with bounce reason)
```

**Bounce reasons** (model the real NPCI-equivalent codes):
- `INSUFFICIENT_FUNDS`, `ACCOUNT_DORMANT`, `ACCOUNT_CLOSED`, `MANDATE_EXPIRED`,
  `MANDATE_CANCELLED_BY_PAYER`, `AMOUNT_EXCEEDS_LIMIT`, `TECHNICAL_FAILURE`.

### 5.5 Sample `pain.001` request (canonical JSON form)

```json
{
  "message_id": "MSG-2026-05-20-0001",
  "creation_datetime": "2026-05-20T10:00:00Z",
  "initiating_party": { "name": "Trazmo", "id": "TRAZMO-PK" },
  "payments": [{
    "end_to_end_id": "E2E-LOAN-7741-DISB",
    "instruction_id": "INST-7741-1",
    "amount": { "value": "50000000", "currency": "PKR" },        // PKR 500,000 → ×100 paisa
    "debtor_account": { "iban": "PK36SCBL0000001123456702" },     // Trazmo pool
    "creditor_account": { "iban": "PK24HBLA0000007654321001" },   // merchant
    "creditor": { "name": "Acme Traders" },
    "remittance_info": "LOAN-7741 DISBURSEMENT",
    "rail": "RAAST",                                              // RAAST|PRISM|1LINK
    "sharia_compliant": true
  }]
}
```

### 5.6 Sample `camt.054` credit notification (canonical JSON)

```json
{
  "notification_id": "NTF-...",
  "account": { "iban": "PK36SCBL0000001123456702" },
  "virtual_account": "PK36SCBL...MERCHANT7741",
  "entry": {
    "amount": { "value": "1231250", "currency": "PKR" },        // PKR 12,312.50 → ×100 paisa
    "credit_debit": "CRDT",
    "booking_datetime": "2026-05-21T19:30:00+05:00",
    "value_date": "2026-05-21",
    "rail": "1LINK",
    "narration": "ACQUIRER-SETTLEMENT MID_000123 BATCH_20260520",
    "counterparty": { "name": "MockAcquirer Settlements" },
    "ref_codes": {
      "batch_id": "BATCH_20260520",
      "acquirer_mid": "MID_000123"
    }
  }
}
```

---

## 6. Cross-cutting concerns

### 6.1 Idempotency

Every `POST` requires `Idempotency-Key` header.

**Transaction boundary (1C):** the idempotency check, business-logic execution, and accounts write happen in **one** Postgres transaction with `SELECT ... FOR UPDATE` on the idempotency row keyed by `(mock_tenant_id, key)`. Concurrent same-key requests serialize at the lock. Same key + same body → cached response. Same key + different body → `409 Conflict`.

**TTL by endpoint class (F10):**

| Endpoint class | TTL | Rationale |
|---|---|---|
| Money endpoints — pain.001 initiate, pacs.008 instant transfer, mandate collect, refund, chargeback finalize | **Forever** (no expiry) | Trazmo CLAUDE.md rule 9: same key returns original response, never double-post. A retry 25h later must not double-debit. |
| Non-money endpoints — webhook subscriptions, scenario injection, admin reads | **24h** | Storage cost bound. |

Stored payload is the response body + status code + content-type. Idempotency rows for money endpoints are append-only and never garbage-collected.

### 6.2 Webhook delivery

- HTTPS POST, body signed with **HMAC-SHA256** over `timestamp + "." + body`
- Headers: `MockSim-Signature: t=...,v1=...`, `MockSim-Event-Id`, `MockSim-Event-Type`, `MockSim-Partition-Key`
- Retry: 1m, 5m, 15m, 1h, 6h, 24h, then dead-letter queue (admin retrievable via `POST /admin/webhooks/{event_id}/replay`)
- **At-least-once delivery, per-aggregate ordered** — consumer must dedupe on `MockSim-Event-Id`. Within a partition key, events arrive in production order. Across partitions, no ordering.

### 6.2.1 Webhook Outbox (1B, F5, 4C)

**Why an outbox.** In-memory webhook queues lose events on process crash between ledger commit and HTTP POST. Trazmo's adapter then sees a hung loan with no settlement event. The outbox pattern eliminates this class of bug at the cost of one extra DB row per event.

**Schema** (`webhook_outbox` table):

| Column | Type | Notes |
|---|---|---|
| `event_id` | ULID PK | also sent as `MockSim-Event-Id` |
| `mock_tenant_id` | UUID FK | scope |
| `partition_key` | text | for ordered delivery (e.g., `merchant_id` for POS events, `iban` for bank events, `mandate_id` for mandate events) |
| `event_type` | text | `pos.transaction.settled`, etc. |
| `payload` | jsonb | full event body |
| `target_url` | text | from subscription |
| `target_secret` | text | for HMAC, never logged |
| `status` | enum | `pending` \| `in_flight` \| `delivered` \| `retrying` \| `dead_letter` |
| `attempt_count` | int | |
| `next_attempt_at` | timestamptz | nullable; set during retry backoff |
| `delivered_at` | timestamptz | nullable; set on success |
| `last_error` | text | nullable |
| `created_at` | timestamptz | |

**Indexes:**
- Partial: `WHERE delivered_at IS NULL AND status IN ('pending','retrying')` — work queue
- `(partition_key, created_at)` — for in-order dispatch
- `(next_attempt_at)` partial `WHERE status='retrying'` — retry scheduler

**Write path (atomic with business operation):**
```
BEGIN;
  -- business logic (account balance update, statement entry write)
  INSERT INTO webhook_outbox (event_id, mock_tenant_id, partition_key, event_type, payload, target_url, ...) VALUES (...);
COMMIT;
```
Same transaction guarantees account-state-change ⇒ event-enqueued.

**Dispatcher (per-partition single-consumer):**
A single poller process loops:
1. `SELECT * FROM webhook_outbox WHERE status IN ('pending','retrying') AND (next_attempt_at IS NULL OR next_attempt_at <= now()) ORDER BY partition_key, created_at FOR UPDATE SKIP LOCKED LIMIT N`
2. Group by `partition_key`; within each partition, process events in `created_at` order (oldest first)
3. POST to `target_url` with HMAC signature
4. On 2xx: `status='delivered', delivered_at=now()`
5. On 4xx (non-retryable): `status='dead_letter'`, alert
6. On 5xx / timeout: `status='retrying', attempt_count++, next_attempt_at = backoff(attempt_count)`
7. After 7 attempts (1m/5m/15m/1h/6h/24h/24h): `status='dead_letter'`

**Archive:** Rows with `status='delivered' AND delivered_at < now() - 30d` are pruned by a daily APScheduler job to keep the partial index small.

**Ordering contract:** Within a partition (e.g., `partition_key='merchant_id:MID_000123'`), events arrive at Trazmo in production order — `pos.transaction.settled` before `pos.batch.settled` for the same merchant. Across partitions there is no ordering. The poller's single-consumer-per-partition pattern (achieved via `ORDER BY partition_key, created_at FOR UPDATE SKIP LOCKED`) holds this guarantee under retries.

### 6.3 Failure injection
- Header `X-Inject-Scenario` on inbound calls, OR
- Per-merchant / per-account scenario config persisted in admin
- Scenarios: `insufficient_funds`, `account_dormant`, `mandate_revoked`,
  `webhook_5xx`, `webhook_timeout`, `duplicate_webhook`, `delayed_settlement`,
  `chargeback_after_settlement`, `partial_recovery`, `rail_downtime`,
  `vat_miscalc`, `narration_truncation`, `iban_checksum_invalid`,
  `clock_skew`, `out_of_order_webhook`.

**Integration pattern (1F):** Scenarios attach to the request lifecycle via two complementary mechanisms.

| Pattern | Scenario class | How |
|---|---|---|
| **Middleware** (`core/scenarios.py:ScenarioMiddleware`) | Pre-execution failures — `webhook_5xx`, `webhook_timeout`, `iban_checksum_invalid`, `rail_downtime`, `clock_skew` | Inspects `X-Inject-Scenario` header + entity config; short-circuits the request before business logic runs. |
| **Decorator** (`@scenario_aware(["insufficient_funds", "account_dormant", "delayed_settlement", "out_of_order_webhook"])`) | Stateful or post-execution scenarios that need entity context | Endpoint opts in; decorator passes scenario hint to business logic so it can produce the realistic shaped error or schedule the delay. |

Domain code never `if scenario == "..."` inline. The decorator hands the business function a `scenario_hint` parameter; the function dispatches via a strategy map. Keeps domain code clean.

Each of the ~14 scenarios has a dedicated test (3G) proving it injects exactly what it claims.

### 6.4 Clock control
- `POST /admin/clock/advance` — advance simulated time by duration. **Sliced** (4A, F8): walks forward in 1-sim-day slices (configurable). Each slice runs in its own Postgres transaction. SimScheduler jobs whose `sim_target_time` falls in the slice fire in sim-time order; ties broken by registration order. If wall-clock budget per request exceeds 30s, returns `202 Accepted` with `job_id` and continues async. Advances > 7d sim-time always return `202` immediately.
- `GET /admin/clock/advance/{job_id}` — poll status of async advance
- `POST /admin/clock/set` — pin to a date
- `POST /admin/clock/run` — set tick speed (e.g. 1 sim-day per real second)
- `POST /admin/seed` (4G, admin) — bulk-create merchants + historical txns directly (bypasses the GMV generator for fast test setup). Admin-only.
- All timestamps in payloads come from `SimClock.now()`, never `datetime.now()`
- Scheduler jobs (daily statement at 23:59 sim-time, settlement T+1 06:00, chargeback maturation) register with **SimScheduler** and fire on `SimClock.advance()`. Real-clock jobs (outbox poller, idempotency GC) register with **APScheduler**.

### 6.5 Identifiers & Money type

**Identifier table (2F):**

| Kind | Format | Length | Generator | Example |
|---|---|---|---|---|
| `event_id` (internal) | ULID | 26 chars | `ulid.new()` | `01HXYZABCDEF...` |
| `IBAN` | region-specific BBAN + mod-97 checksum | PK 24 / AE 23 / SA 24 / EG 29 | `gen_iban(region, bban_seed)` | `PK36SCBL0000001123456702` |
| `BIC` | 8 or 11 chars | — | `gen_bic(region, bank_code)` | `SCBLPKKA` |
| `RRN` | numeric, with embedded date+hour+sequence | 12 | `gen_rrn(sim_date)` | `612345789012` |
| `STAN` | numeric, recycles per terminal per day | 6 | `gen_stan(terminal_id, sim_date)` | `045123` |
| `auth_code` | uppercase alphanumeric | 6 | `gen_auth_code()` | `A12B34` |
| `ARN` | 23 numeric (acquirer ref number) | 23 | `gen_arn(acquirer_id, sim_date)` | `74999996140000000001234` |
| `UETR` | UUIDv4 (ISO 20022 unique end-to-end ref) | 36 | `uuid4()` | `b1f8c0...` |
| `end_to_end_id` | caller-supplied, echoed everywhere | ≤ 35 | (caller) | `E2E-LOAN-7741-DISB` |
| `mandate_id` | `MND-{region}-{ulid26}` | 30 | `gen_mandate_id(region)` | `MND-PK-01HX...` |
| `VAN` | merchant virtual account: `{pool_iban_prefix}{merchant_suffix}` | matches pool IBAN length | `gen_van(pool_iban, merchant_id)` | `PK36SCBL...MERCHANT7741` |
| `batch_id` | `BATCH_{YYYYMMDD}_{seq}` | 18 | `gen_batch_id(sim_date)` | `BATCH_20260520_001` |
| `webhook_event_id` (outbound) | same ULID as `event_id` | 26 | (reused) | `01HXYZ...` |
| `correlation_id` | UUIDv4 | 36 | `uuid4()` | `2e3b...` |

**Money type (2C):** `core/money.py:Money` is a Pydantic v2 model:

```python
class Money(BaseModel):
    value: str  # minor-units integer string, no decimals
    currency: str  # ISO 4217: PKR, AED, SAR, EGP, JOD, BHD, KWD, JPY, ...

    @model_validator
    def validate_minor_units(self):
        if not self.value.lstrip('-').isdigit():
            raise ValueError("value must be a minor-units integer string")
        decimals = CURRENCY_DECIMALS[self.currency]  # PKR/AED/SAR/EGP=2, JOD/BHD/KWD=3, JPY=0
        # value as integer count of minor units; no further validation needed
        return self
```

Arithmetic uses Python `int`; conversion to major units (display only) divides by `10**decimals`. **Never** uses `float`. Per-currency `decimals` lookup from `iso-4217` library.

### 6.6 Authentication
- Trazmo → MockSim: `Authorization: Bearer <api_key>` (per-tenant, rotatable)
- MockSim → Trazmo (webhooks): HMAC signature
- Admin UI: separate token + IP allowlist
- mTLS optional for "production-like" mode

### 6.7 Observability
- All requests/responses logged structured (JSON) with `correlation_id`
- Webhook delivery attempts logged with attempt number, response status
- Ledger entries queryable via `/admin/ledger`
- OpenTelemetry traces exported (Jaeger compose for local)

### 6.8 Reconciliation
- `/admin/recon/run?date=` compares accounts state vs settlement file vs camt.053
- Intentionally inject ~0.5% mismatches when scenario `recon_drift` is on
- Real production recon bugs surface here, not in staging
- `/admin/recon/expected-mismatches?date=` returns the *injected* set (ground truth) for ReconAI test-harnessing — see TODOS T5

### 6.9 Multi-tenancy (1D, F6)

**Two-level model.** MockSim's tenancy is environment-level (dev/CI/staging/Manan's-laptop). Trazmo's tenancy is customer-level (each Trazmo lender). The two must not be conflated.

| Concept | Owner | Purpose |
|---|---|---|
| `mock_tenant_id` (UUID) | MockSim | Isolates state between MockSim consumers (CI runs, dev machines, shared staging). Resolved from the `Authorization: Bearer <api_key>` header on every request — **never** from request body or query. Stored on every domain row. Every SQL query has `WHERE mock_tenant_id = :ctx_tenant` applied by SQLAlchemy session middleware. Cross-tenant data leakage = compliance breach. |
| `trazmo_tenant_id` (opaque string) | Trazmo | Passed through as `X-Trazmo-Tenant-Id` header on every Trazmo → MockSim call. MockSim stores it on entities as a **non-scoping** field — visible in admin views, included in webhook payloads, logged in observability. Lets Trazmo assert its own tenant-isolation end-to-end (e.g., "tenant-A's loan disbursement never produces a webhook whose `trazmo_tenant_id` is tenant-B"). |

**API key schema:**
```
api_keys (
  key_hash PK,
  mock_tenant_id UUID NOT NULL,
  name TEXT,
  scopes TEXT[],         -- e.g., ['pos.read', 'bank.write', 'admin.*']
  rate_limit_profile TEXT,
  status TEXT,
  created_at TIMESTAMPTZ,
  rotated_from KEY_HASH NULL
)
```

**Session middleware:** `core/tenancy.py:TenancyMiddleware` runs first in the request pipeline. It:
1. Reads `Authorization: Bearer <key>`, hashes, looks up `mock_tenant_id`
2. Stores `mock_tenant_id` in a contextvar
3. Reads `X-Trazmo-Tenant-Id` if present and stores in contextvar (no validation — pass-through)
4. SQLAlchemy session adds `WHERE mock_tenant_id = :ctx_tenant` to every query via event hook
5. `/admin/reset?tenant=<mock_tenant_id>` wipes only that tenant's data

**Test mandate (3F):** every money-path test must include a tenant-isolation case where tenant A's operation produces zero effect on tenant B's queries.

### 6.10 Error envelopes (2D)

HTTP errors return:
```json
{
  "code": "INVALID_IBAN",
  "message": "IBAN checksum failed",
  "trace_id": "01HXYZ...",
  "details": { "iban": "PK36INVALID", "checksum_calculated": "27", "checksum_provided": "36" }
}
```

ISO 20022 reject responses (pain.002 RJCT) use **ISO External Code List** reasons. Subset used by MockSim:

| Code | Meaning | Triggered by |
|---|---|---|
| `AC01` | Incorrect account number | malformed IBAN |
| `AC02` | Invalid creditor account | account currency ≠ amount currency |
| `AC04` | Closed account | `account_dormant` scenario or status=`closed` |
| `AC06` | Blocked account | sharia mismatch reject |
| `AM04` | Insufficient funds | `insufficient_funds` scenario or actual balance < amount |
| `BE01` | Inconsistent with end customer | partner-tenant routing mismatch |
| `RC07` | Invalid file format | `iban_checksum_invalid` scenario |
| `MS03` | Not specified | catch-all |

User-facing errors never leak internal field names, SQL state, or other tenants' data (Trazmo CLAUDE.md rule 10). All errors emit a structured log line with `trace_id`, `mock_tenant_id`, `trazmo_tenant_id`, `endpoint`, `error_code`.

### 6.11 Bounce reason normalization (2E)

Mandate collection failures normalize to a canonical enum + carry the provider's native code for traceability:

```json
{
  "bounce_reason": "INSUFFICIENT_FUNDS",   // canonical (one of 7 in §5.4)
  "provider_code": "D05",                  // 1LINK NIFT code (region-specific)
  "provider_message": "Account balance below required amount"
}
```

Canonical bounce enum: `INSUFFICIENT_FUNDS`, `ACCOUNT_DORMANT`, `ACCOUNT_CLOSED`, `MANDATE_EXPIRED`, `MANDATE_CANCELLED_BY_PAYER`, `AMOUNT_EXCEEDS_LIMIT`, `TECHNICAL_FAILURE`.

Provider code mapping tables live in `bank/regions.py`. Provider-flavoured fixtures emit native codes; the canonical mock emits both.

### 6.12 Sharia invariants (F9)

Trazmo CLAUDE.md rule 15: no product logic may calculate or store interest (riba). MockSim's runtime invariants:

1. Any account with `sharia_flag = true` MUST NOT receive a `pain.001` with `sharia_compliant = false`. Rejects with `AC06` + log a Sharia-violation alert.
2. Scenario `late_payment` on a sharia account MUST NOT emit any field named `late_interest_amount`, `interest_charged`, or similar. Instead emits `taawidh_amount` (capped, charity-routed per real Islamic-bank treatment) and a `taawidh_charity_account_iban` field. Test case in §8.5 enforces.
3. Field names in canonical JSON for sharia products use `profit_*` (not `interest_*`), `instalment_*` (not `emi_*`). Trazmo CLAUDE.md rule 15.

Murabaha/Tawarruq full narration templates and capped late-payment math are deferred per §9 row 4 (not in TODOS — revisit when Trazmo onboards an Islamic-bank product).

---

## 7. Trazmo-side adapter interface (the plug-and-play promise)

The mock is only useful if Trazmo treats it as **one implementation of a
generic interface**. Suggested shape on Trazmo's side:

```python
class AcquirerAdapter(Protocol):
    def list_merchants(self) -> list[Merchant]: ...
    def fetch_settlements(self, since: datetime) -> list[Settlement]: ...
    def on_webhook(self, event: AcquirerEvent) -> None: ...

class BankAdapter(Protocol):
    def initiate_payment(self, instr: PaymentInstruction) -> PaymentAck: ...
    def get_balance(self, iban: str) -> Balance: ...
    def get_statement(self, iban: str, since: date) -> Statement: ...
    def create_mandate(self, m: MandateRequest) -> Mandate: ...
    def collect_mandate(self, mandate_id: str, amount: Money) -> CollectionAck: ...
    def on_webhook(self, event: BankEvent) -> None: ...
```

`MockSimAcquirerAdapter` and `MockSimBankAdapter` implement these. The day
Trazmo integrates **HBL** or **Network International**, the work is writing
`HBLBankAdapter(BankAdapter)` — the consumer logic doesn't change.

This is the single most important architectural decision in this whole
exercise. If we skip it and let Trazmo couple directly to mock payloads,
"plug-and-play later" stays a slogan.

### 7.1 Client package versioning (F7)

MockSim publishes a Python client package (`mocksim-client`) with shared Pydantic models, plus a TypeScript client codegen'd from OpenAPI. Both are versioned by semver `MAJOR.MINOR.PATCH` and pinned in Trazmo's `pyproject.toml` / `package.json`.

**Breaking-change policy:**
- **PATCH** — bug fixes, doc-only changes, new optional fields on responses
- **MINOR** — additive only: new endpoints, new optional request fields, new event types, new enum values (consumer must dedupe unknown variants)
- **MAJOR** — anything else: removed field, changed semantics, removed enum value, new required field on request, response shape change

API versioning lives in the URL (`/api/v1/...` per Trazmo CLAUDE.md rule 13). A MAJOR client version bump corresponds to a `/api/v2/...` cutover. Both versions run in parallel for one Trazmo release cycle before `/v1` is removed.

**Compat matrix** lives in `mocksim-client/COMPAT.md`. Each `mocksim-client` release lists the MockSim server versions it speaks to. CI matrix-tests client × server pairs.

**Release process:**
1. PR to `mocksim` updates the server + auto-regenerates client.
2. Client published to internal package registry on tag.
3. Trazmo's `mocksim-client` pin bumped in a follow-up PR (or auto-PR by Renovate).

---

## 8. Phasing — what to build in what order

### Phase 0 — skeleton (2–3 days)

(F12: revised up from ½ day after Section 1 architecture fixes landed — outbox, multi-tenancy middleware, idempotency-in-transaction, SimScheduler-vs-APScheduler split each take real time.)

- FastAPI app, config, Postgres (via docker-compose for local dev), structlog + OpenTelemetry from day 1
- SimClock + SimScheduler (sim-time)
- APScheduler wiring (real-time)
- Idempotency middleware with `SELECT FOR UPDATE` transaction boundary
- Webhook outbox table + Pydantic models + dispatcher poller skeleton
- Tenancy middleware (`mock_tenant_id` from API key + `trazmo_tenant_id` passthrough)
- SQLAlchemy session-level tenant filter
- Money type + per-currency precision
- Error envelope + pain.002 reject mapping
- Provider-profile rate-limiting middleware (configurable TPS, latency, 429s)
- Admin endpoints: clock advance (sliced), bulk seed, ping, tenant reset, webhook replay
- Alembic baseline + advisory-lock-on-startup migration runner

### Phase 1 — Pakistan happy path (3–4 days)
- POS: merchants, streaming sale generator (seeded RNG), T+1 settlement, webhook delivery via outbox
- Bank: pool + merchant accounts (flat balance + entries model), pain.001 → pain.002, camt.054 push on credit
- Mandate create + collect surface (so the instalment leg of the hybrid recovery works)
- **7 end-to-end test scenarios** (3E, see §8.5.1):
  1. Disburse → settle → split-recovery → merchant payout
  2. Disburse → shortfall → mandate fallback (monthly DD if cumulative shortfall crosses threshold)
  3. Sale → chargeback → finalize_loss → recovery impact
  4. Multi-tenant isolation across all surfaces
  5. Idempotency under concurrent retry
  6. Sim clock advance 30d → EOD batch fires correctly
  7. Webhook dead-letter recovery via admin replay
- CSV settlement file drop
- Sharia flag on accounts + payment instructions; reject-on-mismatch scenario (`AC06`)
- Sample provider fixtures: HBL POS + 1LINK shape

### Phase 2 — failure & lifecycle (2 days)
- Refunds and chargeback lifecycle
- Mandate flow (1LINK DD) + bounce reasons
- Failure injection engine
- Reconciliation endpoint

### Phase 3 — region expansion (2 days)
- UAE (Aani + UAEDDS), KSA (mada + Sarie+ + VAT), Egypt (Meeza + InstaPay)
- Working-week awareness
- VAT/WHT in settlement math
- One provider fixture per region

### Phase 4 — polish (1–2 days)
- camt.053 XML output
- Admin UI (read-only browser, scenario buttons)
- Docker compose with optional ngrok for webhook reachability
- Contract tests against ISO 20022 XSDs (subset)

Total: **~12–16 working days** for a sim Trazmo can run nightly. (Revised up from prior 8–10 estimate after the Section 1–4 architecture and test-strategy fixes landed. The outside-voice review pegged realistic at 18–25 days; this estimate assumes CC+gstack-accelerated implementation.)

### 8.5 Test strategy (3A)

| Layer | Framework | Target | What lives here |
|---|---|---|---|
| **Unit** | `pytest`, `pytest-asyncio` | Each public function in `core/`, `pos/`, `bank/`, `admin/` | Pure logic — money math, identifier generation, IBAN checksum, scenario routing, RNG seed determinism |
| **Property-based** | `hypothesis` | IBAN checksum (any valid BBAN → valid IBAN), money math (sums no float drift), idempotency (any concurrency → consistent result) | `tests/property/` |
| **Integration** | `pytest` + `testcontainers-postgres` (3H) | Endpoint × repository × Postgres. **No mocking of accounts/outbox** (Trazmo CLAUDE.md rule 12). | `tests/integration/` |
| **Contract** | `pytest` + ISO 20022 XSD subset + provider fixtures (3C) | (1) Pydantic ↔ canonical JSON round-trip, (2) camt.053 XML serializer validates against ISO XSD subset, (3) Provider-flavoured fixtures parse to canonical, (4) OpenAPI generated schema matches actual responses | `tests/contract/` |
| **E2E / scenarios** | `pytest` + `httpx` client + APScheduler-faked + `respx` for webhook receiver | Multi-endpoint flows; see §8.5.1 | `tests/scenarios/` |
| **Webhook receiver** | `respx` / `httpx-respx` | Verifies HMAC signature, per-partition ordering, retry behavior, dead-letter | both `tests/integration/` and `tests/scenarios/` |

**Coverage target:**
- Money-path lines: **100%** (Trazmo CLAUDE.md rule 12)
- Non-money-path lines: **90%**
- Branch coverage tracked via `coverage.py --branch`

**Money-path 4-pack (3F):** Every money path — `core/accounts.write_entry`, `bank/payments.initiate`, `bank/mandates.collect`, `pos/chargeback.finalize` — has four tests:
1. **Happy path**
2. **Duplicate request** (same idempotency key)
3. **Insufficient balance / boundary violation**
4. **Tenant isolation** (tenant A's op produces zero effect on tenant B)

**Scenario engine self-test (3G):** For each of the ~14 named scenarios in §6.3, a dedicated test injects the scenario header (or sets the entity scenario config) and asserts the exact failure shape (HTTP status, error code, log line, no side effect on accounts). Catches scenario-rename breakage.

**Golden fixtures (3D):**
- ≥ 8 fixtures: one per (region × provider). PK: HBL POS, 1LINK. AE: Network International. SA: Geidea. EG: Paymob. + extras as Trazmo's adapter exercises new shapes.
- **Sourcing:** hand-crafted from provider docs initially; replaced by real sandbox captures when Trazmo integrates first real partner (see TODOS T1)
- **Update:** PR review process. Each fixture lives at `tests/golden/{provider}-{event_type}.json`; CI diffs mock output against them. Fixture changes require explicit PR approval.
- **Drift detection:** weekly CI run against real provider sandbox if available; flagged as PR-required change

**CI parity:** All integration + E2E tests run against `testcontainers-postgres`. Never SQLite. Never an in-memory mock. CI runs the same Postgres major version as production.

### 8.5.1 Planned-coverage diagram

```
[+] core/accounts.py
  ├── write_entry(account, amount, counterparty, narration)  ★★★ 4-pack
  ├── balance(iban)                                          ★★  current + as_of_date
  ├── statement(iban, from, to, cursor)                      ★★  empty + populated + cross-currency
  └── transfer(src_iban, dst_iban, amount)                   ★★★ 4-pack + parent_invariant
[+] core/idempotency.py
  ├── check_or_acquire(key, body_hash)                       ★★★ new + duplicate + conflict + concurrent
  └── store_response(key, response)                          ★★  happy + ttl_money_forever + ttl_nonmoney_24h
[+] core/outbox.py + core/webhook.py
  ├── enqueue(event)                                         ★★★ atomic_with_accounts_txn
  ├── dispatcher_tick()                                      ★★★ deliver + retry_5xx + retry_timeout + dead_letter + partition_ordering
  └── verify_signature(headers, body, secret)                ★★★ valid + bad_sig + replay_attack
[+] core/sim_scheduler.py
  ├── schedule_at(sim_time, cb)                              ★★  basic + multiple_same_time
  ├── advance(duration)                                      ★★★ no_jobs + many_jobs + slice_budget_202 + ordering
  └── set(target)                                            ★★  forward + same_time
[+] core/tenancy.py
  ├── resolve_from_request()                                 ★★★ valid_key + missing + bad_key + scope_mismatch
  └── session_filter()                                       ★★★ tenant_iso_on_every_query
[+] core/money.py
  ├── Money(value, currency)                                 ★★★ valid + non_minor_units_reject + unknown_currency
  └── arithmetic                                             ★★★ add + sub + multiply_int + no_float
[+] pos/* + bank/* + admin/*                                 ★★  see Section 3 coverage diagram
USER FLOWS (E2E): 7 scenarios in Phase 1 + 4 region-specific + 3 cross-tenant + chargeback + mandate-bounce  [→E2E]
CONTRACT: Pydantic round-trip, camt.053 XSD, provider fixture parse, OpenAPI ↔ actual  ★★★
PROPERTY: IBAN checksum, money math, idempotency  ★★★
EVALS: N/A
```

---

## 9. Resolved decisions

| # | Question | Decision | Build implication |
|---|---|---|---|
| 1 | Trazmo runtime + language? | **React + FastAPI** | Trazmo backend is Python/FastAPI too — ship a Python client package with shared Pydantic models. TypeScript client codegen'd from OpenAPI for any direct frontend calls to MockSim admin. |
| 2 | Shared vs per-dev mock instance? | **Shared** | Postgres backend (not SQLite). Tenant-scoped data so multiple devs / CI runs don't trample each other. Explicit `POST /admin/reset?tenant=` per-tenant reset. |
| 3 | Recovery model? | **Hybrid (split-payment + mandate fallback)** | Mock exposes both surfaces fully. Phase 1 demo exercises both paths in one scenario. Both fixture sets shipped. |
| 4 | Sharia depth? | **Flag now, message shapes later** | Phase 1: `sharia_compliant` flag on accounts + payment instructions, reject scenario for interest-marked payment to Islamic account. Phase 3+: Murabaha/Tawarruq narration templates and late-payment treatment differences. |
| 5 | KYC stub? | **Skip for now** | No `kyc.completed` webhook. Accounts start in `active` state. Keep `account_status` field present so it can be added cleanly later. |
| 6 | Production-like rate limits / SLAs? | **Yes, mirror them** | Per-endpoint TPS limits (configurable per "provider profile"). Realistic latencies (200ms–2s) injected. 429 responses with `Retry-After`. Forces Trazmo's retry/backoff/circuit-breaker code to be exercised from day one. |

---

## 10. Deployment (1H, 4E, 4F)

| Aspect | Choice | Rationale |
|---|---|---|
| Compute | **GCP Cloud Run**, one service per environment (dev / staging) | Matches Trazmo's GCP runtime (per Trazmo CLAUDE.md). Cloud Run scales to zero off-hours, fine for a shared sim. |
| Concurrency | **Single instance, single worker** (`--max-instances=1 --concurrency=80`) | Determinism is the point. Single-worker means SimScheduler / idempotency / rate-limit counters all live in one process. Throughput is bounded but adequate for the sim's purpose. |
| Database | **Cloud SQL Postgres 15** (per-environment) | Replaces SQLite (per §9 row 2 + 1G). One small instance per env. |
| Migrations | **Alembic, runs on container startup with Postgres advisory lock** | Lock prevents concurrent migration if Cloud Run multi-revision deploys overlap. Run `SELECT pg_advisory_lock(...)` → migrate → release. |
| Secrets | **GCP Secret Manager** (per Trazmo CLAUDE.md rule 8) | API keys, webhook signing secrets, DB password. Never in `.env` committed to git. |
| Network | Trazmo dev/staging clusters → MockSim via internal DNS (`mocksim-{env}.internal`). No public ingress in v1. ngrok for local dev only. | Stops random internet from talking to the sim. |
| Observability | Cloud Logging + Cloud Trace (via OpenTelemetry SDK). `trace_id` propagates from Trazmo → MockSim → webhook callback. | Same hooks Trazmo runs in production. |

**Throughput targets (4F):**
- Sustained: **50 TPS** across all endpoints per environment
- Burst: **200 TPS** for ≤30s
- p50 latency: **< 100ms** (excluding intentional scenario delays)
- p99 latency: **< 500ms** (excluding intentional scenario delays)
- Webhook outbox dispatcher: drains 500 events/minute under nominal load

These are anchor numbers, revisable once first integration test hits a wall.

**Failure modes in deploy:**
- Cloud Run cold start: ≤3s with structured logging configured. Caller retries handle this transparently.
- Cloud SQL connection limit: pool sized to `min(80, max_connections/2)`. Excess requests get 503 + `Retry-After`.
- Concurrent-revision migration: advisory lock blocks the second revision until the first finishes. Acceptable for a shared sim.

---

## 11. Risks and how we mitigate them

| Risk | Mitigation |
|---|---|
| Mock semantics drift from real providers | One golden fixture per region per provider in `tests/golden/`; CI compares mock output against them. Sourcing process formalized in §8.5 (3D). See TODOS T1 for sandbox-capture lifecycle. |
| Trazmo couples directly to mock payloads | Adapter interface enforced (§7). Versioned client package with breaking-change policy (§7.1). |
| Sim clock vs real clock confusion | All app code uses `clock.now()`; lint rule bans `datetime.now()` outside `clock.py`. SimScheduler and APScheduler are *separate primitives*, not one tool wearing two hats (§3.1). |
| ISO 20022 schema sprawl | Implement only the fields Trazmo actually reads; mark mock-extensions with `x_` prefix. Wire format is canonical JSON modelled on ISO 20022 (§1 principle 8), not standards-compliant JSON. |
| Webhook reachability in local dev | Compose includes optional ngrok. Admin replay endpoint exists for dead-lettered events (§6.2.1). |
| State leakage between test scenarios | Multi-tenancy is enforced via SQLAlchemy session middleware (§6.9). `POST /admin/reset?tenant=` wipes only that mock_tenant_id. |
| MockSim ledger drifts from real bank model | MockSim does NOT keep a double-entry ledger. It mirrors what banks externally expose: per-account balance + ordered entries. Trazmo's Vertex is the authoritative ledger. ReconAI failures are real, not artifacts of MockSim modeling its own books wrong. |
| Webhook silent loss on process crash | Outbox pattern in same Postgres txn as accounts update (§6.2.1). Restart-safe. |
| Idempotency drift on retry past 24h | Money-endpoint idempotency rows live forever (§6.1, F10). Non-money rows expire at 24h. |
| Mock tenancy collides with Trazmo tenancy | Two-level model (§6.9, F6): `mock_tenant_id` scopes MockSim state; `trazmo_tenant_id` is passthrough so Trazmo can assert end-to-end isolation. |
| Phase estimate optimism | This revision raises Phase 0 from ½d to 2–3d and totals to 12–16d (§8). Outside-voice review (subagent) pegged realistic at 18–25d; this estimate assumes CC+gstack-accelerated implementation. Re-estimate at end of Phase 1. |

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 42 issues, 0 critical gaps remaining |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Design Review | `/plan-design-review` | UI/UX (n/a — backend) | 0 | — | — |
| DX Review | `/plan-devex-review` | DX (n/a — internal sim) | 0 | — | — |
| Outside Voice | `/codex` / Claude subagent | Independent challenge | 1 | issues_found_then_resolved | 12 findings, 11 folded into fixes, 1 (scope) prior-decision held |

- **OUTSIDE VOICE:** Claude subagent flagged 12 findings; 11 folded into DESIGN.md (F3 ledger model, F4-F12 spec gaps). F1 (scope) held — user chose full build twice with awareness of F1's argument.
- **CROSS-MODEL:** Subagent + this review agree on outbox necessity, multi-tenancy schema, money-path test rigor. Subagent extended the review with: ledger overengineering (F3), ordering guarantees (F5), tenant model collision (F6).
- **UNRESOLVED:** 0
- **VERDICT:** ENG CLEARED — ready to implement Phase 0.

