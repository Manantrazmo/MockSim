# MockSim — TODOs (Deferred Work)

Items considered during design review and explicitly deferred. Each item has enough context to be picked up cold months from now.

---

## T1 — Provider sandbox response capture / golden-fixture sync lifecycle

**What.** Once Trazmo signs its first real bank/acquirer integration (HBL, Network International, Geidea, Paymob, etc.), run the partner's test suite through both their sandbox AND MockSim, diff the responses, and encode the diffs as updated `tests/golden/{provider}-{event_type}.json` fixtures.

**Why.** §11 Risks: "Mock semantics drift from real providers" is mitigated by golden fixtures, but those need to be sourced from actual providers, not invented from public docs. Without this, MockSim's "realism" decays as providers update their APIs.

**Pros.**
- Closes the realism gap between mock and real sandbox
- CI catches provider drift on weekly real-sandbox runs
- New region adapters get free reference payloads when a new partner is added

**Cons.**
- Requires sandbox access at integration time (may have onboarding friction)
- Ongoing maintenance burden as providers update

**Context.** Process is roughly: spin up a sandbox account → drive Trazmo's adapter against the real sandbox → capture canonical events → diff against MockSim output for the same input → either fix MockSim or update the golden fixture, with PR review. Weekly CI run against the sandbox catches silent drift.

**Depends on.** Trazmo signing first real partner integration.

---

## T2 — OpenAPI client generation + publish

**What.** Auto-generate the Python client (`mocksim-client`, Pydantic models) and TypeScript client from MockSim's OpenAPI spec on every release. Publish to Trazmo's internal package registry. Pin in Trazmo's `pyproject.toml` / `package.json` with Renovate auto-PRs.

**Why.** §9 row 1 commits to a Python client package + TypeScript codegen but doesn't schedule it. §7.1 defines the versioning policy but assumes the publish pipeline exists.

**Pros.**
- Zero hand-written client code in Trazmo (no drift between MockSim API and Trazmo's view of it)
- Type drift caught at MockSim build time, not Trazmo runtime
- Renovate auto-PRs surface MINOR/PATCH bumps; MAJOR bumps get human review

**Cons.**
- Internal package registry must exist (or be set up — modest infra)
- One more thing to maintain in CI

**Context.** `openapi-python-client` and `openapi-typescript` are both mature. Add a GitHub Actions workflow that: (1) runs on tag push, (2) generates both clients, (3) publishes to the registry, (4) updates `mocksim-client/COMPAT.md` with the server version it speaks to.

**Depends on.** MockSim Phase 1 OpenAPI surface stable (so generated clients aren't churning).

---

## T5 — ReconAI mismatch testing harness

**What.** Extend MockSim's existing `/admin/recon/run` (§6.8) so Trazmo's ReconAI agent has a target it can exercise. Expose `/admin/recon/expected-mismatches?date=` returning the ground-truth set of *injected* mismatches when scenario `recon_drift` is active. ReconAI runs its detection algorithm and its output is diffed against the ground truth.

**Why.** ReconAI needs ground truth to validate against during development. MockSim is the natural ground truth because it owns the mismatches it injects. Otherwise ReconAI testing has to either run against production data (slow + risky) or hand-curated test cases (won't cover the long tail).

**Pros.**
- Closes the loop between MockSim's recon endpoint and Trazmo's ReconAI agent
- ReconAI improvements become measurable: precision/recall against a known ground truth
- Lets ReconAI ship with a regression test suite before any real recon data exists

**Cons.**
- Couples MockSim to Trazmo's agent test infra (mild — it's just an admin endpoint)
- Adds an admin endpoint that exposes ground truth; must be auth-gated against non-admin tenants

**Context.** Endpoint shape: `GET /admin/recon/expected-mismatches?date=YYYY-MM-DD` returns `[{txn_id, mismatch_type, expected_side, actual_side, ...}]`. Admin scope only. ReconAI runs its detection on the same date, computes its own mismatch set, diffs against expected, and emits precision/recall metrics.

**Depends on.** Trazmo's ReconAI being real code (currently "Planned" per Trazmo CLAUDE.md). Revisit when ReconAI starts.

---

## Considered and skipped (not in TODOs)

- **Bahrain / Kuwait / Qatar / Oman / Iraq region expansion** — Defer until concrete market signal. Adding regions is cheap after Phase 3 region abstractions are real (~½ day per region).
- **Murabaha / Tawarruq full narration templates + capped late-payment math** — Defer per §9 row 4. Requires Sharia board reference material; revisit when Trazmo onboards an Islamic-bank product.
