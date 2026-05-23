"""
SimScheduler — sim-time job registry (DESIGN.md §3.1).

Jobs register (sim_target_time, callback). On clock.advance(d), the scheduler
iterates due jobs in sim-time order and fires them.

Key invariants:
  - Single-worker, deterministic: same starting state + same advance(d) = same outputs
  - Separate from APScheduler: nothing sim-time-related touches APScheduler
  - Sliced advances: advance(7d) runs as 7×1d slices, each in its own Postgres txn
  - Budget guard: if a slice exceeds wall_clock_budget, return 202 + job_id and continue async
  - Advances > async_threshold_days always return 202 immediately
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import structlog

from mocksim.clock import clock
from mocksim.config import settings

log = structlog.get_logger(__name__)

# Callback type: receives sim_time at which it fires + optional payload
JobCallback = Callable[[datetime, dict[str, Any]], Awaitable[None]]

_registry: dict[str, JobCallback] = {}  # job_type → async function


def register_job_type(job_type: str, callback: JobCallback) -> None:
    """Register a handler for a sim-scheduler job type."""
    _registry[job_type] = callback


async def schedule_at(
    session: Any,  # AsyncSession
    mock_tenant_id: uuid.UUID | None,
    fire_at: datetime,
    job_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Persist a sim-time job to fire when clock advances past fire_at."""
    from mocksim.persistence.models import SimSchedulerJob

    job = SimSchedulerJob(
        id=uuid.uuid4(),
        mock_tenant_id=mock_tenant_id,
        fire_at=fire_at,
        job_type=job_type,
        payload=payload or {},
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    session.add(job)


async def advance(
    duration: timedelta,
    mock_tenant_id: uuid.UUID,
    requested_by: str = "api",
) -> dict[str, Any]:
    """
    Advance sim clock by duration, sliced into 1-day increments.

    Returns:
      {"status": "complete", "new_sim_time": "..."}       — sync, under budget
      {"status": "accepted", "job_id": "...", "poll_url"} — async, over budget / long advance
    """
    from mocksim.persistence.database import async_session_factory
    from mocksim.persistence.models import ClockAdvanceJob

    total_days = duration.days + (1 if duration.seconds > 0 else 0)
    always_async = total_days > settings.sim_clock_advance_async_threshold_days

    if always_async:
        job_id = await _create_advance_job(mock_tenant_id, duration, total_days, requested_by)
        asyncio.create_task(_run_advance_async(job_id, duration, mock_tenant_id))
        return {
            "status": "accepted",
            "job_id": str(job_id),
            "poll_url": f"/admin/clock/advance/{job_id}",
        }

    # Sync path — run slices, bail to async if budget exceeded
    slice_size = timedelta(days=settings.sim_clock_advance_slice_days)
    remaining = duration
    import time
    wall_start = time.monotonic()

    while remaining > timedelta(0):
        slice_dur = min(slice_size, remaining)
        await _run_one_slice(slice_dur, mock_tenant_id)
        remaining -= slice_dur

        elapsed = time.monotonic() - wall_start
        if elapsed > settings.sim_clock_advance_budget_seconds:
            # Budget exceeded mid-advance — hand off to background task
            job_id = await _create_advance_job(mock_tenant_id, remaining, int(remaining.days) + 1, requested_by)
            asyncio.create_task(_run_advance_async(job_id, remaining, mock_tenant_id))
            return {
                "status": "accepted",
                "job_id": str(job_id),
                "poll_url": f"/admin/clock/advance/{job_id}",
                "note": "Budget exceeded; remainder continuing async",
            }

    return {"status": "complete", "new_sim_time": clock.now().isoformat()}


async def _run_one_slice(duration: timedelta, mock_tenant_id: uuid.UUID) -> None:
    """Run one advance slice: advance clock, fire due jobs, all in one Postgres txn."""
    from mocksim.persistence.database import async_session_factory
    from mocksim.persistence.models import SimSchedulerJob
    from sqlalchemy import select

    slice_end = clock.now() + duration

    async with async_session_factory() as session:
        # Fetch jobs due in this slice, in sim-time order
        result = await session.execute(
            select(SimSchedulerJob)
            .where(
                SimSchedulerJob.fire_at <= slice_end,
                SimSchedulerJob.status == "pending",
                (SimSchedulerJob.mock_tenant_id == mock_tenant_id)
                | (SimSchedulerJob.mock_tenant_id == None),  # noqa: E711
            )
            .order_by(SimSchedulerJob.fire_at)
            .with_for_update(skip_locked=True)
        )
        jobs = result.scalars().all()

        clock.advance(duration)  # move the clock forward

        for job in jobs:
            handler = _registry.get(job.job_type)
            if handler is None:
                log.warning("sim_scheduler.unknown_job_type", job_type=job.job_type, job_id=str(job.id))
                job.status = "failed"
                job.error = f"No handler registered for job_type={job.job_type}"
                continue
            try:
                await handler(job.fire_at, job.payload)
                job.status = "fired"
                job.fired_at = datetime.now(timezone.utc)
            except Exception as exc:
                log.error("sim_scheduler.job_failed", job_type=job.job_type, error=str(exc))
                job.status = "failed"
                job.error = str(exc)

        await session.commit()


async def _create_advance_job(
    mock_tenant_id: uuid.UUID,
    remaining: timedelta,
    slices_total: int,
    requested_by: str,
) -> uuid.UUID:
    from mocksim.persistence.database import async_session_factory
    from mocksim.persistence.models import ClockAdvanceJob

    job_id = uuid.uuid4()
    target = clock.now() + remaining
    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        job = ClockAdvanceJob(
            id=job_id,
            mock_tenant_id=mock_tenant_id,
            requested_by=requested_by,
            target_sim_time=target,
            status="pending",
            slices_total=slices_total,
            slices_done=0,
            created_at=now,
        )
        session.add(job)
        await session.commit()

    return job_id


async def _run_advance_async(
    job_id: uuid.UUID, remaining: timedelta, mock_tenant_id: uuid.UUID
) -> None:
    from mocksim.persistence.database import async_session_factory
    from mocksim.persistence.models import ClockAdvanceJob
    from sqlalchemy import select

    slice_size = timedelta(days=settings.sim_clock_advance_slice_days)
    slices_done = 0

    async with async_session_factory() as session:
        result = await session.execute(select(ClockAdvanceJob).where(ClockAdvanceJob.id == job_id))
        job = result.scalar_one()
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

    try:
        while remaining > timedelta(0):
            slice_dur = min(slice_size, remaining)
            await _run_one_slice(slice_dur, mock_tenant_id)
            remaining -= slice_dur
            slices_done += 1

            async with async_session_factory() as session:
                result = await session.execute(select(ClockAdvanceJob).where(ClockAdvanceJob.id == job_id))
                job = result.scalar_one()
                job.slices_done = slices_done
                await session.commit()

        async with async_session_factory() as session:
            result = await session.execute(select(ClockAdvanceJob).where(ClockAdvanceJob.id == job_id))
            job = result.scalar_one()
            job.status = "complete"
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()

    except Exception as exc:
        async with async_session_factory() as session:
            result = await session.execute(select(ClockAdvanceJob).where(ClockAdvanceJob.id == job_id))
            job = result.scalar_one()
            job.status = "failed"
            job.error = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
        log.error("sim_scheduler.async_advance_failed", job_id=str(job_id), error=str(exc))
