"""
SimClock — single source of simulated time.

All business logic calls clock.now(). datetime.now() / datetime.utcnow() are
banned outside this module (enforced by a lint rule: grep for the pattern).
"""
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Callable
import threading


_sim_time_override: ContextVar[datetime | None] = ContextVar("_sim_time_override", default=None)


class SimClock:
    """
    Thread-safe controllable simulation clock.

    Intentionally does NOT drive scheduling — that is SimScheduler's job.
    This class only tracks "what time does the simulation think it is."
    """

    def __init__(self, start: datetime | None = None) -> None:
        self._t: datetime = start or datetime.now(timezone.utc)
        self._lock = threading.Lock()
        self._on_advance: list[Callable[[datetime, datetime], None]] = []

    def now(self) -> datetime:
        """Return current sim time. The only way to get time in business logic."""
        override = _sim_time_override.get()
        if override is not None:
            return override
        with self._lock:
            return self._t

    def set(self, target: datetime) -> datetime:
        """Pin the clock to a specific datetime. Returns new time."""
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        with self._lock:
            self._t = target
            return self._t

    def advance(self, duration: timedelta) -> datetime:
        """
        Advance sim time by duration. Fires registered callbacks with (old, new).

        Do NOT call this directly from business logic — use SimScheduler.advance()
        which handles per-slice Postgres transactions and job firing.
        This bare method is only for SimScheduler's internal use.
        """
        with self._lock:
            old = self._t
            self._t = self._t + duration
            new = self._t
        for cb in self._on_advance:
            cb(old, new)
        return new

    def register_advance_callback(self, cb: Callable[[datetime, datetime], None]) -> None:
        """Register a function to call whenever the clock advances (old_time, new_time)."""
        self._on_advance.append(cb)

    def pin(self, t: datetime) -> "_PinnedClock":
        """Context manager: temporarily override clock to t (for tests only)."""
        return _PinnedClock(t)

    def __repr__(self) -> str:
        return f"SimClock(now={self.now().isoformat()})"


class _PinnedClock:
    """Temporarily pins sim time via a contextvar — safe across async boundaries."""

    def __init__(self, t: datetime) -> None:
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        self._t = t
        self._token = None

    def __enter__(self) -> "_PinnedClock":
        self._token = _sim_time_override.set(self._t)
        return self

    def __exit__(self, *_: object) -> None:
        if self._token is not None:
            _sim_time_override.reset(self._token)


# Module-level singleton — import and use everywhere:
#   from mocksim.clock import clock
#   ts = clock.now()
clock = SimClock()
