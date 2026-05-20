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
                                           │ webhooks +   │ webhooks +
                                           │ REST polls   │ REST calls
                            ┌──────────────┴──────┐  ┌────┴──────────────┐
                            │  MockSim — POS      │  │  MockSim — Bank   │
                            │  /pos/*  /webhooks  │  │  /bank/*          │
                            └────────┬────────────┘  └────────┬──────────┘
                                     │                        │
                                     └──────────┬─────────────┘
                                                ▼
                                  ┌─────────────────────────────┐
                                  │  Shared core                │
                                  │  • SimClock (controllable)  │
                                  │  • Scheduler (APScheduler)  │
                                  │  • Webhook dispatcher       │
                                  │  • Idempotency store        │
                                  │  • Ledger (double-entry)    │
                                  │  • Scenario engine          │
                                  │  • Persistence (SQLite/PG)  │
                                  └─────────────────────────────┘
                                                │
                                  ┌─────────────┴─────────────┐
                                  │  Admin UI / CLI           │
                                  │  /admin/*                 │
                                  │  • Advance clock          │
                                  │  • Seed merchants         │
                                  │  • Inject scenarios       │
                                  │  • Browse ledger          │
                                  └───────────────────────────┘
```

### Stack
- **Python 3.12 + FastAPI** (chosen)
- **Pydantic v2** for ISO 20022 message schemas → automatic OpenAPI
- **SQLite** for dev / **Postgres** for shared environments (via SQLAlchemy 2.x)
- **APScheduler** for the sim clock + recurring jobs (daily statement, EOD batch drop, eNACH debit retries)
- **httpx** for outbound webhook delivery, **tenacity** for retry
- **Faker** + custom MENA/PK data providers for realistic merchant/customer/IBAN/CNIC generation
- **structlog** + OpenTelemetry for observability (the same hooks Trazmo will need in production)

### Folder layout
```
mocksim/
├── DESIGN.md                  # this file
├── README.md                  # quickstart
├── pyproject.toml
├── docker-compose.yml         # mocksim + postgres + ngrok (for webhooks)
├── src/mocksim/
│   ├── main.py                # FastAPI app entry
│   ├── config.py              # env config, region toggles
│   ├── clock.py               # SimClock — single source of "now"
│   ├── core/
│   │   ├── ledger.py          # double-entry ledger
│   │   ├── idempotency.py     # Idempotency-Key handling
│   │   ├── webhook.py         # signed delivery + retry queue
│   │   ├── scheduler.py       # APScheduler wiring
│   │   ├── scenarios.py       # failure injection engine
│   │   └── identifiers.py     # IBAN/RRN/UETR/EMVCo generation
│   ├── iso20022/              # canonical message models (pain, pacs, camt)
│   │   ├── pain001.py
│   │   ├── pain002.py
│   │   ├── pacs008.py
│   │   ├── camt052.py
│   │   ├── camt053.py
│   │   └── camt054.py
│   ├── pos/
│   │   ├── api.py             # /pos/*
│   │   ├── generator.py       # GMV simulation (lognormal, seasonality, MCC mix)
│   │   ├── chargeback.py      # delayed chargeback lifecycle
│   │   ├── settlement.py      # T+1/T+2 settlement file builder
│   │   └── regions/
│   │       ├── pk.py          # PayPak BINs, PKR behaviour
│   │       ├── ae.py          # UAE Switch
│   │       ├── sa.py          # mada routing + VAT
│   │       └── eg.py          # Meeza
│   ├── bank/
│   │   ├── api.py             # /bank/*
│   │   ├── accounts.py        # pool, merchant, virtual accounts
│   │   ├── payments.py        # initiate/status (pain.001/002)
│   │   ├── statements.py      # camt.053/052
│   │   ├── notifications.py   # camt.054 push
│   │   ├── mandates.py        # UAEDDS / 1LINK DD / SEPA-style
│   │   ├── instant.py         # Aani / RAAST / IPS / InstaPay
│   │   └── regions/
│   │       ├── pk.py          # PRISM, RAAST, 1LINK
│   │       ├── ae.py          # UAEFTS, Aani, UAEDDS
│   │       ├── sa.py          # SARIE, IPS, SADAD
│   │       └── eg.py          # RTGS, InstaPay
│   ├── admin/
│   │   └── api.py             # /admin/* — clock, seed, inject
│   └── persistence/
│       ├── models.py
│       └── migrations/
└── tests/
    ├── contract/              # contract tests against ISO 20022 schemas
    ├── scenarios/             # named end-to-end scenarios
    └── golden/                # frozen sample payloads (one per provider/region)
```

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
  "amount": {
    "value": "12500.00",
    "currency": "PKR",
    "mdr": "187.50",                       // 1.5% MDR for illustration
    "vat_on_mdr": "0.00",                  // 0 for PK; 28.13 for KSA at 15%
    "wht": "0.00",                         // withholding if applicable
    "net_settlement": "12312.50"
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
| `GET`  | `/pos/transactions` | Query — filter by merchant, date, status |
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

### 4.5 Settlement file formats
- **JSON** (canonical) — what Trazmo's adapter consumes
- **CSV** — what most regional acquirers actually drop (one row per txn + a footer)
- **camt.053 XML** — for banks that route via swift-style messaging
- **Provider-flavoured JSON** — one fixture each for Network International, Geidea, Paymob, HBL POS, so Trazmo's adapter layer is exercised against real-shaped payloads

---

## 5. Bank Mock — API contract

### 5.1 Accounts model

Three account types, all on the same ledger:

| Type | Owner | Purpose |
|---|---|---|
| **Pool account** | Trazmo | Holds disbursement funds; receives consolidated acquirer settlement |
| **Merchant settlement (virtual) account** | Per merchant | Sub-ledger of pool; recovery routes here based on narration / VAN |
| **Merchant external account** | Per merchant | The merchant's actual bank account at another bank (destination of net payouts) |

All accounts have IBAN (region-correct), BIC, currency, sharia_flag, status.

### 5.2 Endpoints (ISO 20022-aligned)

| Method | Path | ISO 20022 equivalent | Purpose |
|---|---|---|---|
| `POST` | `/bank/accounts` | — | Create account (admin) |
| `GET`  | `/bank/accounts/{iban}` | acmt | Account details + status |
| `GET`  | `/bank/accounts/{iban}/balance` | camt.052 | Current + available balance |
| `GET`  | `/bank/accounts/{iban}/statement?from=&to=&format=json|camt053` | camt.053 | Statement |
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
    "amount": { "value": "500000.00", "currency": "PKR" },
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
    "amount": { "value": "12312.50", "currency": "PKR" },
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
Every `POST` requires `Idempotency-Key` header. Same key + same body → cached
response. Same key + different body → `409 Conflict`. TTL 24h.

### 6.2 Webhook delivery
- HTTPS POST, body signed with **HMAC-SHA256** over `timestamp + "." + body`
- Headers: `MockSim-Signature: t=...,v1=...`, `MockSim-Event-Id`, `MockSim-Event-Type`
- Retry: 1m, 5m, 15m, 1h, 6h, 24h, then dead-letter queue (admin retrievable)
- At-least-once delivery — consumer must dedupe on `MockSim-Event-Id`

### 6.3 Failure injection
- Header `X-Inject-Scenario` on inbound calls, OR
- Per-merchant / per-account scenario config persisted in admin
- Scenarios: `insufficient_funds`, `account_dormant`, `mandate_revoked`,
  `webhook_5xx`, `webhook_timeout`, `duplicate_webhook`, `delayed_settlement`,
  `chargeback_after_settlement`, `partial_recovery`, `rail_downtime`,
  `vat_miscalc`, `narration_truncation`, `iban_checksum_invalid`,
  `clock_skew`, `out_of_order_webhook`.

### 6.4 Clock control
- `POST /admin/clock/advance` — advance simulated time by duration
- `POST /admin/clock/set` — pin to a date
- `POST /admin/clock/run` — set tick speed (e.g. 1 sim-day per real second)
- All timestamps in payloads come from `SimClock.now()`, never `datetime.now()`
- Scheduler jobs (daily statement at 23:59 sim-time, settlement T+1 06:00,
  chargeback maturation) fire off SimClock ticks

### 6.5 Identifiers
- **ULID** for internal event ids
- **IBAN** generated with valid mod-97 checksum, region-specific length and BBAN
- **RRN** 12 digits with realistic embedded date
- **UETR** UUIDv4 (ISO 20022 unique end-to-end ref)
- **End-to-End Id** caller-supplied, echoed everywhere

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
- `/admin/recon/run?date=` compares ledger vs settlement file vs camt.053
- Intentionally inject ~0.5% mismatches when scenario `recon_drift` is on
- Real production recon bugs surface here, not in staging

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

---

## 8. Phasing — what to build in what order

### Phase 0 — skeleton (½ day)
- FastAPI app, config, Postgres (via docker-compose for local dev), structlog
- SimClock, idempotency middleware, webhook dispatcher stub
- Per-tenant data scoping (since the instance is shared)
- Provider-profile rate-limiting middleware (configurable TPS, latency, 429s)
- Admin endpoints: clock, ping, tenant reset

### Phase 1 — Pakistan happy path (2–3 days)
- POS: merchants, sale generator, T+1 settlement, webhook delivery
- Bank: pool + merchant accounts, pain.001 → pain.002, camt.054 push on credit
- Mandate create + collect surface (so the EMI leg of the hybrid recovery works)
- One end-to-end test exercising **both** recovery legs:
  - Split-payment: % of each settlement routed to recovery via VAN narration
  - Mandate fallback: monthly DD if cumulative shortfall crosses threshold
- CSV settlement file drop
- Sharia flag on accounts + payment instructions; reject-on-mismatch scenario
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

Total: **~8–10 working days** for a sim Trazmo can run nightly.

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

## 10. Risks and how we mitigate them

| Risk | Mitigation |
|---|---|
| Mock semantics drift from real providers | One golden fixture per region per provider, kept in `tests/golden/`; CI compares mock output against them |
| Trazmo couples directly to mock payloads | Adapter interface enforced; review with that lens |
| Sim clock vs real clock confusion | All app code uses `clock.now()`; lint rule banning `datetime.now()` outside `clock.py` |
| ISO 20022 schema sprawl | Implement only the fields Trazmo actually reads; mark mock-extensions with `x_` prefix |
| Webhook reachability in local dev | Compose includes optional ngrok; admin endpoint can replay failed deliveries |
| State leakage between test scenarios | `POST /admin/reset?scope=tenant` wipes ledger + accounts per tenant |
