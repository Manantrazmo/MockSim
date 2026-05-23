"""
MockSim FastAPI application entry point.

Middleware order (outermost → innermost):
  1. Structured logging (correlation_id)
  2. TenancyMiddleware (resolves mock_tenant_id from API key)
  3. ScenarioMiddleware (pre-execution failure injection)

APScheduler starts on app startup and runs real-wall-clock jobs:
  - Webhook outbox poller (every 5s)
  - Idempotency GC + outbox archival (daily)

The React control panel is served at /ui/ from dashboard/dist/ (built
during Docker image creation; skipped gracefully if dist/ is absent).
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any
import os
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mocksim.config import settings
from mocksim.core.errors import MockSimError
from mocksim.core.scenarios import ScenarioMiddleware
from mocksim.core.tenancy import TenancyMiddleware
from mocksim.core.scheduler import scheduler, setup_scheduler

log = structlog.get_logger(__name__)

_configure_logging_done = False


def _configure_logging() -> None:
    global _configure_logging_done
    if _configure_logging_done:
        return
    import logging
    import structlog

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configure_logging_done = True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _configure_logging()
    log.info("mocksim.startup", version="0.1.0", regions=settings.enabled_regions)

    # Run Alembic migrations with advisory lock (prevents concurrent runs)
    await _run_migrations()

    # Seed scenario engine status if not present
    await _ensure_scenario_engine_status()

    # Register SimScheduler job-type handlers (sim-time, not wall-clock)
    from mocksim.pos.generator import register_handlers as register_pos_handlers
    from mocksim.pos.settlement import register_handlers as register_settlement_handlers
    from mocksim.bank.payments import register_handlers as register_payment_handlers
    register_pos_handlers()
    register_settlement_handlers()
    register_payment_handlers()
    log.info("mocksim.sim_scheduler.handlers_registered")

    # Start APScheduler (real-wall-clock jobs only)
    setup_scheduler()
    scheduler.start()
    log.info("mocksim.scheduler.started")

    yield

    scheduler.shutdown(wait=False)
    log.info("mocksim.shutdown")


async def _run_migrations() -> None:
    """Run Alembic migrations on startup with a Postgres advisory lock."""
    import asyncio
    from alembic import command
    from alembic.config import Config

    def _run() -> None:
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
        command.upgrade(alembic_cfg, "head")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run)
    log.info("mocksim.migrations.done")


async def _ensure_scenario_engine_status() -> None:
    from datetime import datetime, timezone
    from mocksim.persistence.database import async_session_factory
    from mocksim.persistence.models import ScenarioEngineStatus
    from sqlalchemy import select

    async with async_session_factory() as session:
        result = await session.execute(select(ScenarioEngineStatus).where(ScenarioEngineStatus.id == 1))
        if not result.scalar_one_or_none():
            session.add(ScenarioEngineStatus(
                id=1, enabled=True,
                updated_at=datetime.now(timezone.utc),
                updated_by="system",
            ))
            await session.commit()


# ── App factory ───────────────────────────────────────────────────

def create_app() -> FastAPI:
    _configure_logging()

    app = FastAPI(
        title="MockSim",
        description="Simulation harness for Trazmo's lending infrastructure",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Middleware (added in reverse — last added = outermost)
    app.add_middleware(ScenarioMiddleware)
    app.add_middleware(TenancyMiddleware)

    # Routers
    from mocksim.pos.api import router as pos_router
    from mocksim.bank.api import router as bank_router
    from mocksim.admin.api import router as admin_router

    app.include_router(pos_router, prefix="/api/v1")
    app.include_router(bank_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")

    # Health check (unauthenticated)
    @app.get("/health", tags=["Meta"])
    async def health() -> dict[str, Any]:
        from mocksim.clock import clock
        return {
            "status": "ok",
            "sim_time": clock.now().isoformat(),
            "regions": settings.enabled_regions,
        }

    # ── Control-panel UI ──────────────────────────────────────────────
    # Serve the pre-built React SPA from dashboard/dist/ at /ui.
    # html=True enables SPA fallback: unknown paths return index.html.
    # Silently skipped when the dist/ directory hasn't been built yet
    # (e.g., local Python-only dev without running `npm run build`).
    _dashboard_dist = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../dashboard/dist")
    )
    if os.path.isdir(_dashboard_dist):
        from fastapi.staticfiles import StaticFiles
        app.mount("/ui", StaticFiles(directory=_dashboard_dist, html=True), name="ui")
        log.info("mocksim.dashboard.mounted", path=_dashboard_dist)

    # Global exception handler — converts MockSimError to error envelope
    @app.exception_handler(MockSimError)
    async def mocksim_error_handler(request: Request, exc: MockSimError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
        log.warning(
            "request.error",
            code=exc.error_code,
            status=exc.status_code,
            path=request.url.path,
            trace_id=trace_id,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.error_code,
                "message": exc.error_message,
                "trace_id": trace_id,
                "details": exc.error_details,
            },
        )

    # Unhandled exception handler — never leak internals
    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
        log.error("request.unhandled_error", error=str(exc), path=request.url.path, trace_id=trace_id)
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "trace_id": trace_id,
                "details": {},
            },
        )

    return app


app = create_app()
