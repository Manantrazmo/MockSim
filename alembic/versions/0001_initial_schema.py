"""Initial schema — all Phase 0 + Phase 1 tables.

Revision ID: 0001
Revises:
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── mock_tenants ──────────────────────────────────────────────
    op.create_table(
        "mock_tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── api_keys ──────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("key_hash", sa.Text(), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("mock_tenants.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("rate_limit_profile", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_from", sa.Text(), sa.ForeignKey("api_keys.key_hash"), nullable=True),
    )
    op.create_index("ix_api_keys_tenant", "api_keys", ["mock_tenant_id"])

    # ── idempotency_records ───────────────────────────────────────
    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("body_hash", sa.Text(), nullable=False),
        sa.Column("endpoint_class", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint(
        "uq_idempotency_tenant_key", "idempotency_records", ["mock_tenant_id", "idempotency_key"]
    )
    op.create_index("ix_idempotency_expires", "idempotency_records", ["expires_at"])

    # ── webhook_subscriptions ─────────────────────────────────────
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trazmo_tenant_id", sa.Text(), nullable=True),
        sa.Column("surface", sa.String(10), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("target_secret", sa.Text(), nullable=False),
        sa.Column("event_types", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_sub_tenant", "webhook_subscriptions", ["mock_tenant_id", "surface"])

    # ── webhook_outbox ────────────────────────────────────────────
    op.create_table(
        "webhook_outbox",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("partition_key", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("target_secret", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outbox_partition_order", "webhook_outbox", ["partition_key", "created_at"])
    op.create_index("ix_outbox_delivered_at", "webhook_outbox", ["delivered_at"])

    # ── accounts ──────────────────────────────────────────────────
    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trazmo_tenant_id", sa.Text(), nullable=True),
        sa.Column("iban", sa.Text(), nullable=False, unique=True),
        sa.Column("bic", sa.Text(), nullable=False),
        sa.Column("account_type", sa.String(30), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("balance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("available_balance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("parent_iban", sa.Text(), sa.ForeignKey("accounts.iban"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("sharia_flag", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("owner_name", sa.Text(), nullable=False),
        sa.Column("region", sa.String(2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_accounts_tenant", "accounts", ["mock_tenant_id"])
    op.create_index("ix_accounts_iban", "accounts", ["iban"])

    # ── account_entries ───────────────────────────────────────────
    op.create_table(
        "account_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_iban", sa.Text(), sa.ForeignKey("accounts.iban"), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("credit_debit", sa.String(4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=False),
        sa.Column("booking_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("narration", sa.Text(), nullable=False),
        sa.Column("counterparty_name", sa.Text(), nullable=True),
        sa.Column("counterparty_iban", sa.Text(), nullable=True),
        sa.Column("ref_codes", postgresql.JSONB(), nullable=True),
        sa.Column("transfer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_entries_account_iban", "account_entries", ["account_iban", "booking_datetime"])
    op.create_index("ix_entries_tenant", "account_entries", ["mock_tenant_id"])

    # ── sim_scheduler_jobs ────────────────────────────────────────
    op.create_table(
        "sim_scheduler_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── clock_advance_jobs ────────────────────────────────────────
    op.create_table(
        "clock_advance_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("target_sim_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("slices_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("slices_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── scenario_engine_status ────────────────────────────────────
    op.create_table(
        "scenario_engine_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False, server_default="system"),
    )

    # ── scenario_configs ──────────────────────────────────────────
    op.create_table(
        "scenario_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("scenario_name", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_scenario_config_entity", "scenario_configs",
        ["mock_tenant_id", "entity_type", "entity_id"]
    )

    # ── merchants ─────────────────────────────────────────────────
    op.create_table(
        "merchants",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trazmo_tenant_id", sa.Text(), nullable=True),
        sa.Column("region", sa.String(2), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mcc", sa.String(4), nullable=False),
        sa.Column("expected_daily_txns", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("avg_ticket_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("risk_tier", sa.String(10), nullable=False, server_default="standard"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("scenario_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_merchants_tenant", "merchants", ["mock_tenant_id"])

    # ── pos_transactions ──────────────────────────────────────────
    op.create_table(
        "pos_transactions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", sa.Text(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("region", sa.String(2), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("mdr", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("vat_on_mdr", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("wht", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("net_settlement", sa.BigInteger(), nullable=False),
        sa.Column("card_network", sa.Text(), nullable=False),
        sa.Column("card_bin", sa.String(8), nullable=False),
        sa.Column("card_last4", sa.String(4), nullable=False),
        sa.Column("rrn", sa.String(12), nullable=False),
        sa.Column("stan", sa.String(6), nullable=False),
        sa.Column("auth_code", sa.String(6), nullable=False),
        sa.Column("arn", sa.String(23), nullable=False),
        sa.Column("response_code", sa.String(2), nullable=False, server_default="00"),
        sa.Column("settlement_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("settlement_batch_id", sa.Text(), nullable=True),
        sa.Column("expected_settlement_date", sa.Date(), nullable=True),
        sa.Column("sim_date", sa.Date(), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pos_txn_merchant", "pos_transactions", ["mock_tenant_id", "merchant_id"])
    op.create_index("ix_pos_txn_status", "pos_transactions", ["settlement_status"])
    op.create_index("ix_pos_txn_sim_date", "pos_transactions", ["sim_date"])

    # ── settlement_batches ────────────────────────────────────────
    op.create_table(
        "settlement_batches",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", sa.Text(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("region", sa.String(2), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column("txn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gross_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_mdr", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_vat", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("net_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_batch_merchant_date", "settlement_batches",
        ["mock_tenant_id", "merchant_id", "settlement_date"]
    )

    # ── payment_instructions ──────────────────────────────────────
    op.create_table(
        "payment_instructions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("instruction_id", sa.Text(), nullable=False),
        sa.Column("debtor_iban", sa.Text(), nullable=False),
        sa.Column("creditor_iban", sa.Text(), nullable=False),
        sa.Column("creditor_name", sa.Text(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rail", sa.Text(), nullable=False),
        sa.Column("sharia_compliant", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("remittance_info", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_payment_tenant", "payment_instructions", ["mock_tenant_id"])

    # ── mandates ──────────────────────────────────────────────────
    op.create_table(
        "mandates",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("mock_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("debtor_iban", sa.Text(), nullable=False),
        sa.Column("creditor_iban", sa.Text(), nullable=False),
        sa.Column("debtor_name", sa.Text(), nullable=False),
        sa.Column("max_amount", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("region", sa.String(2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("scenario_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mandate_tenant", "mandates", ["mock_tenant_id"])


def downgrade() -> None:
    tables = [
        "mandates", "payment_instructions", "settlement_batches",
        "pos_transactions", "merchants", "scenario_configs",
        "scenario_engine_status", "clock_advance_jobs",
        "sim_scheduler_jobs", "account_entries", "accounts",
        "webhook_outbox", "webhook_subscriptions",
        "idempotency_records", "api_keys", "mock_tenants",
    ]
    for table in tables:
        op.drop_table(table)
