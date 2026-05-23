"""
Failure injection engine (DESIGN.md §6.3).

Two integration patterns:
  1. ScenarioMiddleware — intercepts requests before business logic for
     pre-execution failures (webhook_5xx, rail_downtime, clock_skew, etc.)
  2. @scenario_aware decorator — opts an endpoint into post-execution or
     stateful scenarios (insufficient_funds, account_dormant, etc.)

Global kill-switch: POST /admin/scenarios/disable flips a Postgres flag +
process-level cache. All scenario evaluations short-circuit to no-op when disabled.

Domain code NEVER does `if scenario == "..."` inline.
"""
from __future__ import annotations
import functools
from typing import Any, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

log = structlog.get_logger(__name__)

# ── Named scenarios ───────────────────────────────────────────────
KNOWN_SCENARIOS = frozenset({
    "insufficient_funds",
    "account_dormant",
    "account_closed",
    "mandate_revoked",
    "webhook_5xx",
    "webhook_timeout",
    "duplicate_webhook",
    "delayed_settlement",
    "chargeback_after_settlement",
    "partial_recovery",
    "rail_downtime",
    "vat_miscalc",
    "narration_truncation",
    "iban_checksum_invalid",
    "clock_skew",
    "out_of_order_webhook",
    "recon_drift",
    "late_payment",
})

# Process-level cache of the kill-switch state (refreshed on toggle)
_engine_enabled: bool = True


def is_engine_enabled() -> bool:
    return _engine_enabled


def set_engine_enabled(enabled: bool) -> None:
    global _engine_enabled
    _engine_enabled = enabled
    log.info("scenario_engine.toggle", enabled=enabled)


def active_scenario(request: Request) -> str | None:
    """
    Return the active scenario name for this request, or None.
    Sources (priority order):
      1. X-Inject-Scenario header (per-request override)
      2. Entity-level config (loaded by business logic, passed in request.state)
    """
    if not _engine_enabled:
        return None
    header = request.headers.get("X-Inject-Scenario", "").strip()
    if header and header in KNOWN_SCENARIOS:
        return header
    return getattr(request.state, "entity_scenario", None)


# ── Middleware: pre-execution failures ────────────────────────────

class ScenarioMiddleware(BaseHTTPMiddleware):
    """
    Short-circuits requests for pre-execution failure scenarios
    before business logic runs.
    """
    _PRE_EXECUTION = {
        "rail_downtime",
        "iban_checksum_invalid",
        "clock_skew",
        "webhook_5xx",
        "webhook_timeout",
    }

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not _engine_enabled:
            return await call_next(request)

        scenario = request.headers.get("X-Inject-Scenario", "").strip()
        if not scenario or scenario not in self._PRE_EXECUTION:
            return await call_next(request)

        log.info("scenario.pre_execution_inject", scenario=scenario, path=request.url.path)

        if scenario == "rail_downtime":
            return _error_response(503, "RAIL_DOWNTIME", "Payment rail is temporarily unavailable")

        if scenario == "iban_checksum_invalid":
            from mocksim.core.errors import ErrorCode
            return _error_response(422, ErrorCode.INVALID_IBAN, "IBAN checksum validation failed (injected)")

        if scenario == "clock_skew":
            return _error_response(400, "CLOCK_SKEW", "Request timestamp outside acceptable window (injected)")

        if scenario in ("webhook_5xx", "webhook_timeout"):
            # These affect outbound webhooks, not inbound requests — pass through
            return await call_next(request)

        return await call_next(request)


# ── Decorator: stateful post-execution scenarios ──────────────────

def scenario_aware(supported_scenarios: list[str]):
    """
    Decorator for endpoint handlers that support named failure scenarios.

    Adds a `scenario_hint: str | None` keyword argument to the wrapped function.
    The function dispatches via its own strategy map — no inline `if scenario == ...`.

    Usage:
      @scenario_aware(["insufficient_funds", "account_dormant"])
      async def collect_mandate(mandate_id: str, ..., scenario_hint: str | None = None):
          handlers = {
              "insufficient_funds": _handle_insufficient_funds,
              "account_dormant":    _handle_account_dormant,
          }
          if scenario_hint and scenario_hint in handlers:
              return await handlers[scenario_hint](...)
          # normal path
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, request: Request | None = None, **kwargs: Any) -> Any:
            hint: str | None = None
            if _engine_enabled and request is not None:
                candidate = request.headers.get("X-Inject-Scenario", "").strip()
                if candidate in supported_scenarios:
                    hint = candidate
                    log.info(
                        "scenario.decorator_inject",
                        scenario=hint,
                        handler=fn.__name__,
                    )
            return await fn(*args, request=request, scenario_hint=hint, **kwargs)
        return wrapper
    return decorator


def _error_response(status: int, code: str, message: str) -> Response:
    import json
    body = json.dumps({"code": code, "message": message, "trace_id": "", "details": {}})
    return Response(content=body, status_code=status, media_type="application/json")
