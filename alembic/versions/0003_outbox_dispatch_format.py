"""Outbox dispatch format — Phase B of MockSim ↔ trazmo-platform wiring.

Each outbox row now carries the protocol it should be delivered with. The
default 'per_event' preserves MockSim's native MockSim-Signature header
contract. 'trazmo_settlement' switches the dispatcher to trazmo-platform's
acquirer-webhook contract: X-Acquirer-Signature (plain hex), X-Tenant-ID
header (taken from extra_headers), and the batched
{partner_code, settlements: [...]} payload shape.

extra_headers is a small JSONB bag for per-dispatcher header values that
don't belong on the subscription itself (e.g., X-Tenant-ID changes per
trazmo tenant).

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhook_outbox",
        sa.Column(
            "format",
            sa.String(length=32),
            nullable=False,
            server_default="per_event",
        ),
    )
    op.add_column(
        "webhook_outbox",
        sa.Column("extra_headers", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhook_outbox", "extra_headers")
    op.drop_column("webhook_outbox", "format")
