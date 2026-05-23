"""
Webhook outbox — enqueue and dispatch (DESIGN.md §6.2.1).

Write path: outbox row inserted in same transaction as account update.
Dispatch: APScheduler calls dispatch_batch() every 5s real-time.

Ordering contract: within a partition_key, events arrive at Trazmo in
creation order. SKIP LOCKED + per-partition ordering enforces this.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mocksim.config import settings
from mocksim.core.identifiers import new_ulid
from mocksim.persistence.database import async_session_factory
from mocksim.persistence.models import WebhookOutbox

log = structlog.get_logger(__name__)

_MAX_ATTEMPTS = 7  # matches len(WEBHOOK_RETRY_SCHEDULE) + 1


def enqueue(
    session: AsyncSession,
    mock_tenant_id: uuid.UUID,
    partition_key: str,
    event_type: str,
    payload: dict[str, Any],
    target_url: str,
    target_secret: str,
    *,
    format: str = "per_event",
    extra_headers: dict[str, str] | None = None,
) -> WebhookOutbox:
    """
    Enqueue a webhook event. MUST be called within the same Postgres transaction
    as the business operation that caused this event (outbox pattern).

    `format` and `extra_headers` let a single subscription speak protocols other
    than MockSim's native one — e.g., 'trazmo_settlement' with X-Tenant-ID.
    """
    event_id = new_ulid()
    now = datetime.now(timezone.utc)
    row = WebhookOutbox(
        event_id=event_id,
        mock_tenant_id=mock_tenant_id,
        partition_key=partition_key,
        event_type=event_type,
        payload=payload,
        target_url=target_url,
        target_secret=target_secret,
        format=format,
        extra_headers=extra_headers,
        status="pending",
        attempt_count=0,
        created_at=now,
    )
    session.add(row)
    return row


async def dispatch_batch(batch_size: int = 50) -> None:
    """
    Pull pending/retrying events and attempt delivery.
    Called by APScheduler every 5s. Single consumer per partition (SKIP LOCKED).
    """
    from mocksim.core.webhook import post_webhook

    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        result = await session.execute(
            select(WebhookOutbox)
            .where(
                WebhookOutbox.status.in_(["pending", "retrying"]),
                (WebhookOutbox.next_attempt_at == None)  # noqa: E711
                | (WebhookOutbox.next_attempt_at <= now),
            )
            .order_by(WebhookOutbox.partition_key, WebhookOutbox.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        rows = result.scalars().all()

        if not rows:
            return

        for row in rows:
            row.status = "in_flight"
        await session.commit()

    # Deliver outside the lock so we don't hold it during HTTP calls
    for row in rows:
        await _deliver(row)


async def _deliver(row: WebhookOutbox) -> None:
    from mocksim.core.webhook import post_webhook

    async with async_session_factory() as session:
        result = await session.execute(
            select(WebhookOutbox)
            .where(WebhookOutbox.event_id == row.event_id)
            .with_for_update()
        )
        outbox_row = result.scalar_one_or_none()
        if outbox_row is None:
            return

        success, error = await post_webhook(
            url=outbox_row.target_url,
            secret=outbox_row.target_secret,
            event_id=outbox_row.event_id,
            event_type=outbox_row.event_type,
            partition_key=outbox_row.partition_key,
            payload=outbox_row.payload,
            format=outbox_row.format,
            extra_headers=outbox_row.extra_headers,
        )

        now = datetime.now(timezone.utc)
        outbox_row.attempt_count += 1

        if success:
            outbox_row.status = "delivered"
            outbox_row.delivered_at = now
            log.info(
                "outbox.delivered",
                event_id=outbox_row.event_id,
                event_type=outbox_row.event_type,
                attempts=outbox_row.attempt_count,
            )
        else:
            if outbox_row.attempt_count >= _MAX_ATTEMPTS:
                outbox_row.status = "dead_letter"
                log.error(
                    "outbox.dead_letter",
                    event_id=outbox_row.event_id,
                    event_type=outbox_row.event_type,
                    attempts=outbox_row.attempt_count,
                    last_error=error,
                )
            else:
                delay = _backoff(outbox_row.attempt_count)
                outbox_row.status = "retrying"
                outbox_row.next_attempt_at = now.replace(microsecond=0).__class__.fromtimestamp(
                    now.timestamp() + delay, tz=timezone.utc
                )
                outbox_row.last_error = error
                log.warning(
                    "outbox.retry_scheduled",
                    event_id=outbox_row.event_id,
                    attempt=outbox_row.attempt_count,
                    retry_in_seconds=delay,
                )

        await session.commit()


def _backoff(attempt: int) -> int:
    """Return retry delay in seconds for the given attempt number (1-based)."""
    schedule = settings.webhook_retry_schedule
    idx = min(attempt - 1, len(schedule) - 1)
    return schedule[idx]
