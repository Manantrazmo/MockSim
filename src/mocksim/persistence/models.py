"""
SQLAlchemy ORM models for MockSim.

All domain tables that carry mock_tenant_id inherit TenantScoped so the
session-level tenant filter applies automatically to every SELECT.
"""
from __future__ import annotations
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mocksim.persistence.database import Base, TenantScoped


# ── Tenants & API keys ────────────────────────────────────────────

class MockTenant(Base):
    __tablename__ = "mock_tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"

    key_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mock_tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rate_limit_profile: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_from: Mapped[str | None] = mapped_column(
        Text, ForeignKey("api_keys.key_hash"), nullable=True
    )

    tenant: Mapped[MockTenant] = relationship("MockTenant", lazy="select")

    __table_args__ = (Index("ix_api_keys_tenant", "mock_tenant_id"),)


# ── Idempotency ───────────────────────────────────────────────────

class IdempotencyRecord(Base, TenantScoped):
    __tablename__ = "idempotency_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    body_hash: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_class: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # 'money' | 'non_money'
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | complete | conflict
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("mock_tenant_id", "idempotency_key", name="uq_idempotency_tenant_key"),
        Index("ix_idempotency_expires", "expires_at"),
    )


# ── Webhook subscriptions ─────────────────────────────────────────

class WebhookSubscription(Base, TenantScoped):
    __tablename__ = "webhook_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trazmo_tenant_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    surface: Mapped[str] = mapped_column(String(10), nullable=False)  # 'pos' | 'bank'
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    target_secret: Mapped[str] = mapped_column(Text, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_webhook_sub_tenant", "mock_tenant_id", "surface"),)


# ── Webhook outbox ────────────────────────────────────────────────

class WebhookOutbox(Base, TenantScoped):
    """
    Outbox pattern: every event is written here in the same Postgres transaction
    as the account update that caused it. A separate poller delivers the event.
    Guarantees at-least-once delivery even if the process crashes between
    commit and HTTP POST.

    State machine: pending → in_flight → delivered
                                       ↓ (on 5xx/timeout)
                              retrying ←→ (backoff)
                                       ↓ (after 7 attempts)
                              dead_letter
    """
    __tablename__ = "webhook_outbox"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)  # ULID
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    partition_key: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    target_secret: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending|in_flight|delivered|retrying|dead_letter
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # Work queue — only undelivered rows
        Index(
            "ix_outbox_work_queue",
            "status",
            "next_attempt_at",
            postgresql_where="delivered_at IS NULL AND status IN ('pending','retrying')",
        ),
        # In-order dispatch per partition
        Index("ix_outbox_partition_order", "partition_key", "created_at"),
        # Retry scheduler
        Index(
            "ix_outbox_retry",
            "next_attempt_at",
            postgresql_where="status = 'retrying'",
        ),
        # Archival pruning — delivered rows older than 30d
        Index("ix_outbox_delivered_at", "delivered_at"),
    )


# ── Accounts ──────────────────────────────────────────────────────

class Account(Base, TenantScoped):
    """
    Balance + ordered entries model. NOT double-entry.
    Mirrors what a bank externally exposes. Trazmo's Vertex is the
    authoritative ledger; MockSim does not duplicate it.
    """
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trazmo_tenant_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    iban: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    bic: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # pool | merchant_van | external
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # minor units
    available_balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    parent_iban: Mapped[str | None] = mapped_column(
        Text, ForeignKey("accounts.iban"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # active | dormant | closed
    sharia_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_name: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    entries: Mapped[list[AccountEntry]] = relationship(
        "AccountEntry", back_populates="account", lazy="select", order_by="AccountEntry.created_at"
    )
    children: Mapped[list[Account]] = relationship(
        "Account", back_populates="parent", lazy="select"
    )
    parent: Mapped[Account | None] = relationship(
        "Account", back_populates="children", remote_side="Account.iban", lazy="select"
    )

    __table_args__ = (
        Index("ix_accounts_tenant", "mock_tenant_id"),
        Index("ix_accounts_iban", "iban"),
    )


class AccountEntry(Base, TenantScoped):
    __tablename__ = "account_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    account_iban: Mapped[str] = mapped_column(
        Text, ForeignKey("accounts.iban"), nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # positive=credit, neg=debit
    credit_debit: Mapped[str] = mapped_column(String(4), nullable=False)  # CRDT | DBIT
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    booking_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value_date: Mapped[date] = mapped_column(Date, nullable=False)
    narration: Mapped[str] = mapped_column(Text, nullable=False)
    counterparty_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterparty_iban: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref_codes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # links paired internal entries
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    account: Mapped[Account] = relationship("Account", back_populates="entries", lazy="select")

    __table_args__ = (
        Index("ix_entries_account_iban", "account_iban", "booking_datetime"),
        Index("ix_entries_tenant", "mock_tenant_id"),
    )


# ── Sim scheduler jobs ────────────────────────────────────────────

class SimSchedulerJob(Base):
    """Registered sim-time jobs. Fired when SimClock.advance() passes fire_at."""
    __tablename__ = "sim_scheduler_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mock_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # None = system job
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | fired | failed
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_sim_jobs_pending", "fire_at", postgresql_where="status = 'pending'"),
    )


class ClockAdvanceJob(Base):
    """Tracks async clock advance operations (those returning 202 Accepted)."""
    __tablename__ = "clock_advance_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)  # API key name
    target_sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | running | complete | failed
    slices_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    slices_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── Scenario engine ───────────────────────────────────────────────

class ScenarioEngineStatus(Base):
    """Singleton row (id=1). Global kill-switch for the scenario engine."""
    __tablename__ = "scenario_engine_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(Text, nullable=False, default="system")


class ScenarioConfig(Base, TenantScoped):
    """Per-entity scenario configuration (persisted via admin API)."""
    __tablename__ = "scenario_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # merchant | account | mandate
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_name: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_scenario_config_entity", "mock_tenant_id", "entity_type", "entity_id"),
    )


# ── POS: Merchants & Transactions ─────────────────────────────────

class Merchant(Base, TenantScoped):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # MID_XXXXXX
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trazmo_tenant_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mcc: Mapped[str] = mapped_column(String(4), nullable=False)
    expected_daily_txns: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    avg_ticket_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(10), nullable=False, default="standard")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    scenario_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_merchants_tenant", "mock_tenant_id"),)


class PosTransaction(Base, TenantScoped):
    __tablename__ = "pos_transactions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # ULID
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(Text, ForeignKey("merchants.id"), nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # sale|refund|chargeback|reversal
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # minor units
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    mdr: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    vat_on_mdr: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    wht: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    net_settlement: Mapped[int] = mapped_column(BigInteger, nullable=False)
    card_network: Mapped[str] = mapped_column(Text, nullable=False)
    card_bin: Mapped[str] = mapped_column(String(8), nullable=False)
    card_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    rrn: Mapped[str] = mapped_column(String(12), nullable=False)
    stan: Mapped[str] = mapped_column(String(6), nullable=False)
    auth_code: Mapped[str] = mapped_column(String(6), nullable=False)
    arn: Mapped[str] = mapped_column(String(23), nullable=False)
    response_code: Mapped[str] = mapped_column(String(2), nullable=False, default="00")
    settlement_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | settled | reversed
    settlement_batch_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sim_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_pos_txn_merchant", "mock_tenant_id", "merchant_id"),
        Index("ix_pos_txn_status", "settlement_status"),
        Index("ix_pos_txn_sim_date", "sim_date"),
    )


class SettlementBatch(Base, TenantScoped):
    __tablename__ = "settlement_batches"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # BATCH_YYYYMMDD_NNN
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(Text, ForeignKey("merchants.id"), nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    txn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gross_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_mdr: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_vat: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    net_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | settled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_batch_merchant_date", "mock_tenant_id", "merchant_id", "settlement_date"),
    )


# ── Bank: Payments & Mandates ─────────────────────────────────────

class PaymentInstruction(Base, TenantScoped):
    """pain.001 disbursement instruction."""
    __tablename__ = "payment_instructions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # end_to_end_id
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    message_id: Mapped[str] = mapped_column(Text, nullable=False)
    instruction_id: Mapped[str] = mapped_column(Text, nullable=False)
    debtor_iban: Mapped[str] = mapped_column(Text, nullable=False)
    creditor_iban: Mapped[str] = mapped_column(Text, nullable=False)
    creditor_name: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rail: Mapped[str] = mapped_column(Text, nullable=False)
    sharia_compliant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    remittance_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | accepted | settled | rejected
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_payment_tenant", "mock_tenant_id"),)


class Mandate(Base, TenantScoped):
    """Direct debit mandate (UAEDDS / 1LINK DD / SEPA-style)."""
    __tablename__ = "mandates"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # MND-{REGION}-{ULID}
    mock_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    debtor_iban: Mapped[str] = mapped_column(Text, nullable=False)
    creditor_iban: Mapped[str] = mapped_column(Text, nullable=False)
    debtor_name: Mapped[str] = mapped_column(Text, nullable=False)
    max_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # active | suspended | expired | cancelled
    scenario_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_mandate_tenant", "mock_tenant_id"),)
