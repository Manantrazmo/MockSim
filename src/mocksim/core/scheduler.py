"""
APScheduler wiring — REAL wall-clock jobs ONLY (DESIGN.md §3.1).

Nothing sim-time-related belongs here. The only jobs wired here:
  - Webhook outbox poller (every 5 seconds real-time)
  - Idempotency record GC (daily real-time — prune expired non-money rows)
  - Webhook outbox archival (daily real-time — prune 30d-old delivered rows)

SimScheduler (sim_scheduler.py) handles all sim-time jobs.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

log = structlog.get_logger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


def setup_scheduler() -> None:
    """Register all real-wall-clock jobs. Called once on app startup."""

    scheduler.add_job(
        _outbox_poller_tick,
        trigger=IntervalTrigger(seconds=5),
        id="outbox_poller",
        replace_existing=True,
        max_instances=1,  # never overlap — per-partition ordering depends on single consumer
    )

    scheduler.add_job(
        _prune_idempotency_records,
        trigger=IntervalTrigger(hours=24),
        id="idempotency_gc",
        replace_existing=True,
    )

    scheduler.add_job(
        _archive_delivered_outbox,
        trigger=IntervalTrigger(hours=24),
        id="outbox_archive",
        replace_existing=True,
    )


async def _outbox_poller_tick() -> None:
    """Drive the webhook outbox dispatcher — fires pending + retrying events."""
    from mocksim.core.outbox import dispatch_batch
    try:
        await dispatch_batch()
    except Exception as exc:
        log.error("outbox_poller.error", error=str(exc))


async def _prune_idempotency_records() -> None:
    """Delete expired non-money idempotency records (TTL 24h)."""
    from mocksim.persistence.database import async_session_factory
    from mocksim.persistence.models import IdempotencyRecord
    from sqlalchemy import delete

    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        await session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.endpoint_class == "non_money",
                IdempotencyRecord.expires_at != None,  # noqa: E711
                IdempotencyRecord.expires_at <= now,
            )
        )
        await session.commit()
    log.info("idempotency_gc.done")


async def _archive_delivered_outbox() -> None:
    """Delete outbox rows delivered > 30 days ago."""
    from mocksim.persistence.database import async_session_factory
    from mocksim.persistence.models import WebhookOutbox
    from sqlalchemy import delete

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    async with async_session_factory() as session:
        result = await session.execute(
            delete(WebhookOutbox).where(
                WebhookOutbox.status == "delivered",
                WebhookOutbox.delivered_at != None,  # noqa: E711
                WebhookOutbox.delivered_at <= cutoff,
            )
        )
        await session.commit()
    log.info("outbox_archive.done", deleted=result.rowcount)
