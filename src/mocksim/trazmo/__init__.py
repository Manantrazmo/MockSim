"""
mocksim.trazmo — direct integration with trazmo-platform's postgres.

Used by the cross-system onboarding endpoint (POST /admin/onboard-sme):
when an operator drives MockSim's UI to create an SME, we write the
canonical entity / sme_profile / merchant_profile / acquirer_mapping
rows into trazmo-platform's own database in the same transaction as
MockSim's own merchants row.

Why direct PG and not trazmo's API:
  - Trazmo's entity creation endpoint is auth-gated (SUPER_ADMIN), and
    minting service-account JWTs from MockSim is out of scope for a sim.
  - The shape of rows we write here is the same as trazmo's own
    `scripts/seed_mock_gmv.py` — see references below — so we stay
    in lockstep with what trazmo's seeds produce.
  - Single PG transaction = atomic onboarding. A trazmo API hop would
    require coordinating idempotency across three endpoints.

This module is a pragmatic coupling: when trazmo's schema changes in a
breaking way, this module is the first thing to update. See
trazmo-platform/scripts/seed_mock_gmv.py lines 540–717 for the
authoritative row shapes.
"""
