"""Synthetic documents on merchants — Phase I.

Adds a JSONB column for storing dummy-but-plausible KYC documents
generated alongside each SME (CNIC, NTN, bank statement, business
registration). Each entry is `{type, number, issued_at, file_uri?}`.

Designed for the demo flow: the operator clicks "Onboard SME with
documents" in MockSim's UI and gets a merchant row with a realistic
CNIC + NTN + bank-account-number visible in both MockSim's dashboard
and (when the trazmo push lands in a follow-up) the lender's Flux
borrower-detail screen.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "merchants",
        sa.Column("synthetic_documents", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merchants", "synthetic_documents")
