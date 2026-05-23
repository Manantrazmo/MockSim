"""
mocksim.trazmo.client — async helpers that read & write trazmo-platform's
postgres directly, used by the cross-system onboarding flow.

Public surface (every function takes its own asyncpg connection so the
caller can run them inside a single transaction):

  • resolve_bootstrap(conn, partner_code)   → BootstrapHandles
        Fast lookup of (tenant_id, partner_entity_id, pool_id,
        currency_id, entity_type_sme_id). Errors if the partner isn't
        set up — caller should run trazmo's seed_dev + seed_mock_gmv
        first (or the MockSim seed_e2e --run-trazmo-seeds path).

  • list_lenders(conn)                       → list[LenderRow]
        For MockSim UI lender dropdown.

  • list_sme_entities(conn, partner_code)    → list[SmeRow]
        For MockSim UI "existing SMEs" panel — shows the union of what
        was created on either side, by walking the acquirer_mapping table.

  • onboard_sme(conn, **kw)                  → OnboardedSme
        Idempotent insertion of entity + sme_profile + merchant_profile +
        acquirer_mapping. Returns the IDs the caller needs to mirror
        the row into MockSim.

Row shapes mirror trazmo-platform/scripts/seed_mock_gmv.py (the only
sanctioned source of truth for this layout). When trazmo updates that
seed, this module needs the same update.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import asyncpg
import structlog

log = structlog.get_logger(__name__)


# ── Lookup result types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class BootstrapHandles:
    tenant_id: uuid.UUID
    partner_entity_id: uuid.UUID
    pool_id: uuid.UUID
    currency_id: uuid.UUID
    entity_type_sme_id: uuid.UUID
    country_code: str
    timezone: str


@dataclass(frozen=True)
class LenderRow:
    entity_id: uuid.UUID
    code: str
    legal_name: str


@dataclass(frozen=True)
class SmeRow:
    entity_id: uuid.UUID
    code: str
    legal_name: str
    acquirer_merchant_id: str | None
    mcc: str | None
    status: str


@dataclass(frozen=True)
class OnboardedSme:
    entity_id: uuid.UUID
    sme_profile_id: uuid.UUID
    merchant_profile_id: uuid.UUID | None
    acquirer_merchant_id: str
    acquirer_mapping_id: uuid.UUID


# ── Errors ───────────────────────────────────────────────────────────────────


class TrazmoNotBootstrapped(Exception):
    """Raised when the trazmo side hasn't been seeded for this partner_code."""


# ── Resolvers ────────────────────────────────────────────────────────────────


async def resolve_bootstrap(
    conn: asyncpg.Connection,
    *,
    partner_code: str,
    country_code: str = "PK",
    timezone: str = "Asia/Karachi",
) -> BootstrapHandles:
    """Resolve every ID our writes need, in one round trip per row."""
    partner_row = await conn.fetchrow(
        """
        SELECT pp.entity_id        AS partner_entity_id,
               pp.tenant_id        AS tenant_id,
               pp.country_code     AS country_code
          FROM partner_profile pp
         WHERE pp.code = $1
         LIMIT 1
        """,
        partner_code,
    )
    if partner_row is None:
        raise TrazmoNotBootstrapped(
            f"partner_profile with code={partner_code!r} not found in trazmo. "
            "Run `docker compose exec mocksim python scripts/seed_e2e.py "
            "--run-trazmo-seeds --partner-code <code>` first."
        )

    pool_row = await conn.fetchrow(
        "SELECT id FROM merchant_pool "
        "WHERE tenant_id = $1 AND partner_entity_id = $2 LIMIT 1",
        partner_row["tenant_id"], partner_row["partner_entity_id"],
    )
    if pool_row is None:
        raise TrazmoNotBootstrapped(
            f"merchant_pool for partner {partner_code} not found — re-run "
            "trazmo's seed_mock_gmv to (re)create it."
        )

    # PKR by default — the demo currency. Other currencies live alongside.
    currency_row = await conn.fetchrow(
        "SELECT id FROM currency WHERE code = $1",
        "PKR",
    )
    if currency_row is None:
        raise TrazmoNotBootstrapped(
            "currency row for PKR not found — run trazmo's seed_dev first."
        )

    sme_type_row = await conn.fetchrow(
        "SELECT id FROM entity_type WHERE code = 'SME'",
    )
    if sme_type_row is None:
        raise TrazmoNotBootstrapped("entity_type SME missing — run seed_dev")

    return BootstrapHandles(
        tenant_id=partner_row["tenant_id"],
        partner_entity_id=partner_row["partner_entity_id"],
        pool_id=pool_row["id"],
        currency_id=currency_row["id"],
        entity_type_sme_id=sme_type_row["id"],
        country_code=partner_row["country_code"] or country_code,
        timezone=timezone,
    )


async def list_lenders(conn: asyncpg.Connection) -> list[LenderRow]:
    rows = await conn.fetch(
        """
        SELECT e.id, e.code, e.legal_name
          FROM entity e
          JOIN entity_type t ON t.id = e.entity_type_id
         WHERE t.code = 'LENDER'
           AND e.status = 'ACTIVE'
         ORDER BY e.created_at
        """
    )
    return [LenderRow(entity_id=r["id"], code=r["code"], legal_name=r["legal_name"]) for r in rows]


async def list_sme_entities(
    conn: asyncpg.Connection,
    *,
    partner_code: str,
) -> list[SmeRow]:
    rows = await conn.fetch(
        """
        SELECT e.id          AS entity_id,
               e.code        AS code,
               e.legal_name  AS legal_name,
               amm.acquirer_merchant_id,
               mp.mcc,
               e.status
          FROM entity e
          JOIN entity_type et            ON et.id = e.entity_type_id
     LEFT JOIN acquirer_merchant_mapping amm
                ON amm.trazmo_entity_id = e.id
     LEFT JOIN merchant_profile mp
                ON mp.entity_id = e.id
     LEFT JOIN partner_profile pp
                ON pp.entity_id = amm.partner_entity_id
         WHERE et.code = 'SME'
           AND (pp.code = $1 OR pp.code IS NULL)
         ORDER BY e.created_at DESC
         LIMIT 500
        """,
        partner_code,
    )
    return [
        SmeRow(
            entity_id=r["entity_id"],
            code=r["code"],
            legal_name=r["legal_name"],
            acquirer_merchant_id=r["acquirer_merchant_id"],
            mcc=r["mcc"],
            status=r["status"],
        )
        for r in rows
    ]


# ── Writes (idempotent — re-runnable) ────────────────────────────────────────


async def onboard_sme(
    conn: asyncpg.Connection,
    *,
    handles: BootstrapHandles,
    sme_code: str,
    legal_name: str,
    owner_name: str,
    mcc: str,
    acquirer_merchant_id: str,
    terminal_ids: list[str] | None = None,
) -> OnboardedSme:
    """
    Idempotent insertion. Re-running with the same `sme_code` returns
    the existing IDs without duplicating rows.

    Mirrors scripts/seed_mock_gmv.py:_ensure_merchant_entity (lines 530–599)
    plus the merchant_profile + acquirer_mapping API calls (lines 670–717).
    """
    # 1. entity (idempotent on code)
    entity_id = await conn.fetchval(
        "SELECT id FROM entity WHERE code = $1", sme_code,
    )
    if entity_id is None:
        entity_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO entity
              (id, tenant_id, entity_type_id, code, legal_name, display_name,
               metadata, status, created_at, updated_at, version)
            VALUES ($1, $2, $3, $4, $5, $5, '{}'::jsonb, 'ACTIVE',
                    now(), now(), 0)
            """,
            entity_id, handles.tenant_id, handles.entity_type_sme_id,
            sme_code, legal_name,
        )

    # 2. sme_profile (idempotent on entity_id)
    sme_profile_id = await conn.fetchval(
        "SELECT id FROM sme_profile WHERE entity_id = $1", entity_id,
    )
    if sme_profile_id is None:
        sme_profile_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO sme_profile
              (id, entity_id, owner_name, turnover, employees, status,
               created_at, updated_at, version)
            VALUES ($1, $2, $3, 1000000.00, 5, 'ACTIVE',
                    now(), now(), 0)
            """,
            sme_profile_id, entity_id, owner_name,
        )

    # 3. merchant_profile (idempotent on (tenant_id, entity_id))
    merchant_profile_id = await conn.fetchval(
        "SELECT id FROM merchant_profile WHERE tenant_id = $1 AND entity_id = $2",
        handles.tenant_id, entity_id,
    )
    if merchant_profile_id is None:
        merchant_profile_id = uuid.uuid4()
        # NB: merchant_profile has no `status` column (it tracks merchant-
        # advance eligibility via the allocation_strategy + pool linkage,
        # not a status flag). Keep this in sync with trazmo migrations.
        await conn.execute(
            """
            INSERT INTO merchant_profile
              (id, tenant_id, entity_id, sme_profile_id, pool_id,
               primary_gmv_source, mcc, country_code,
               operating_currency_id, operating_timezone,
               created_at, updated_at, version)
            VALUES ($1, $2, $3, $4, $5,
                    'MOCK', $6, $7,
                    $8, $9,
                    now(), now(), 0)
            """,
            merchant_profile_id, handles.tenant_id, entity_id, sme_profile_id,
            handles.pool_id, mcc, handles.country_code,
            handles.currency_id, handles.timezone,
        )

    # 4. acquirer_merchant_mapping (idempotent on
    #    (tenant_id, partner_entity_id, acquirer_merchant_id))
    mapping_id = await conn.fetchval(
        """
        SELECT id FROM acquirer_merchant_mapping
         WHERE tenant_id = $1
           AND partner_entity_id = $2
           AND acquirer_merchant_id = $3
        """,
        handles.tenant_id, handles.partner_entity_id, acquirer_merchant_id,
    )
    if mapping_id is None:
        mapping_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO acquirer_merchant_mapping
              (id, tenant_id, trazmo_entity_id, partner_entity_id,
               acquirer_merchant_id, terminal_ids, status,
               created_at, updated_at, version)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'ACTIVE',
                    now(), now(), 0)
            """,
            mapping_id, handles.tenant_id, entity_id, handles.partner_entity_id,
            acquirer_merchant_id,
            ('["' + '","'.join(terminal_ids) + '"]') if terminal_ids else "[]",
        )

    log.info(
        "trazmo.sme_onboarded",
        sme_code=sme_code,
        entity_id=str(entity_id),
        acquirer_merchant_id=acquirer_merchant_id,
    )
    return OnboardedSme(
        entity_id=entity_id,
        sme_profile_id=sme_profile_id,
        merchant_profile_id=merchant_profile_id,
        acquirer_merchant_id=acquirer_merchant_id,
        acquirer_mapping_id=mapping_id,
    )


# ── Connection helper ────────────────────────────────────────────────────────


async def connect(dsn: str) -> asyncpg.Connection:
    """
    Open a single asyncpg connection to trazmo-platform's postgres. Caller
    owns close. We don't use a pool here because the endpoint is low-volume
    (one onboarding click = one transaction) and avoiding a pool keeps the
    blast radius small.
    """
    return await asyncpg.connect(dsn=dsn, timeout=10)
