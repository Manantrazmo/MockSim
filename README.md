# MockSim

A simulation harness for testing **Trazmo**'s lending infrastructure
end-to-end before integrating real bank and acquirer partners.

Two simulators in one service:

- **POS GMV Generator** — emits credit/debit card sale, refund, and
  chargeback events the way a payment acquirer would.
- **Bank Mock** — exposes core-banking and payment-rail behaviour
  (account balances, disbursement, statements, mandates, direct debits,
  instant credit notifications) so Trazmo can drive disbursement into
  pool accounts and recovery from merchant virtual accounts.

Target markets: **Pakistan, UAE, KSA, Egypt** (extensible to wider MENA).

See [`DESIGN.md`](DESIGN.md) for the full design.

## Status

Pre-implementation. The design is being reviewed; build starts after sign-off.

## Stack

- Python 3.12 + FastAPI
- Pydantic v2 (ISO 20022 message schemas)
- SQLite (dev) / Postgres (shared)
- APScheduler for the simulated clock
- httpx + tenacity for signed webhook delivery
