"""Trazmo bridge identifiers — Phase A of MockSim ↔ trazmo-platform wiring.

Adds the columns the seed orchestrator needs to mirror trazmo's identity
scheme verbatim, so a POS sale on MockSim can be attributed back to the
right entity by trazmo's webhook receiver without any lookup table on the
MockSim side.

  mock_tenants.partner_code                  — trazmo partner_profile.code
                                               (1:1 with a MockSim tenant)
  merchants.acquirer_merchant_id             — trazmo acquirer_merchant_mapping
                                               .acquirer_merchant_id
  merchants.external_entity_id               — trazmo entity.id, kept only
                                               for traceability
  webhook_subscriptions.format               — 'per_event' (default) | 'trazmo_settlement'

All columns are nullable so existing rows survive the upgrade unchanged.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── mock_tenants.partner_code ────────────────────────────────────
    op.add_column(
        "mock_tenants",
        sa.Column("partner_code", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_mock_tenants_partner_code", "mock_tenants", ["partner_code"]
    )

    # ── merchants.acquirer_merchant_id + external_entity_id ──────────
    op.add_column(
        "merchants",
        sa.Column("acquirer_merchant_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "merchants",
        sa.Column("external_entity_id", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_merchants_tenant_acquirer_id",
        "merchants",
        ["mock_tenant_id", "acquirer_merchant_id"],
    )
    op.create_index(
        "ix_merchants_acquirer_id", "merchants", ["acquirer_merchant_id"]
    )

    # ── webhook_subscriptions.format ─────────────────────────────────
    # Server-side default 'per_event' keeps existing rows valid; we still
    # add nullable=False so future inserts must declare intent.
    op.add_column(
        "webhook_subscriptions",
        sa.Column(
            "format",
            sa.String(length=32),
            nullable=False,
            server_default="per_event",
        ),
    )


def downgrade() -> None:
    op.drop_column("webhook_subscriptions", "format")

    op.drop_index("ix_merchants_acquirer_id", table_name="merchants")
    op.drop_constraint(
        "uq_merchants_tenant_acquirer_id", "merchants", type_="unique"
    )
    op.drop_column("merchants", "external_entity_id")
    op.drop_column("merchants", "acquirer_merchant_id")

    op.drop_constraint(
        "uq_mock_tenants_partner_code", "mock_tenants", type_="unique"
    )
    op.drop_column("mock_tenants", "partner_code")
