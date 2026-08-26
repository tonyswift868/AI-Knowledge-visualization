"""Staging SLO targets and a bounded rolling error-budget read model."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class SloWindow:
    """A named rolling time window used by SLO and dashboard read models."""

    seconds: int

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ValueError("SLO window must be positive")

    def cutoff(self, observed_at: float) -> float:
        return float(observed_at) - self.seconds


@dataclass(frozen=True, slots=True)
class SLOTarget:
    name: str
    target: float
    window_seconds: int
    description: str

    @property
    def window(self) -> SloWindow:
        return SloWindow(self.window_seconds)


STAGING_SLOS = {
    target.name: target
    for target in (
        SLOTarget("non_ai_api_availability", 0.999, 30 * 24 * 3600, "Non-AI API requests below 500"),
        SLOTarget("non_ai_api_latency", 0.95, 30 * 24 * 3600, "Non-AI API requests completing within 500 ms"),
        SLOTarget("teacher_availability", 0.99, 30 * 24 * 3600, "Teacher turns completing without provider failure"),
        SLOTarget("job_success", 0.995, 7 * 24 * 3600, "Background jobs reaching succeeded"),
        SLOTarget("document_completion", 0.99, 7 * 24 * 3600, "Document batches completing or partially completing"),
        SLOTarget("auth_success", 0.995, 30 * 24 * 3600, "Valid authentication attempts succeeding"),
        SLOTarget("backup_freshness", 0.999, 30 * 24 * 3600, "Postgres, object, and Keycloak backups within 24 hours"),
    )
}


class ErrorBudgetReadModel:
    """Keep only success bits and timestamps; never retain request payload data."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._events: dict[str, deque[tuple[float, bool]]] = {
            name: deque() for name in STAGING_SLOS
        }
        self._lock = threading.RLock()

    def record(self, name: str, good: bool, *, observed_at: float | None = None) -> None:
        target = STAGING_SLOS[name]
        now = self._clock() if observed_at is None else float(observed_at)
        with self._lock:
            events = self._events[name]
            events.append((now, bool(good)))
            cutoff = target.window.cutoff(now)
            while events and events[0][0] < cutoff:
                events.popleft()

    def snapshot(self, name: str) -> dict[str, object]:
        target = STAGING_SLOS[name]
        now = self._clock()
        with self._lock:
            events = self._events[name]
            cutoff = target.window.cutoff(now)
            while events and events[0][0] < cutoff:
                events.popleft()
            total = len(events)
            good = sum(item[1] for item in events)
        bad = total - good
        allowed_bad = total * (1.0 - target.target)
        consumed = bad / allowed_bad if allowed_bad > 0 else 0.0
        remaining = max(0.0, 1.0 - consumed)
        attainment = good / total if total else None
        return {
            **asdict(target),
            "observed_total": total,
            "observed_good": good,
            "observed_bad": bad,
            "attainment": attainment,
            "error_budget_consumed_ratio": consumed,
            "error_budget_remaining_ratio": remaining,
            "burn_state": (
                "NO_DATA" if total == 0 else "EXHAUSTED" if remaining == 0 else "BURNING" if consumed > 0.5 else "HEALTHY"
            ),
        }

    def snapshots(self) -> list[dict[str, object]]:
        return [self.snapshot(name) for name in STAGING_SLOS]

    def reset(self) -> None:
        with self._lock:
            for events in self._events.values():
                events.clear()


error_budgets = ErrorBudgetReadModel()


__all__ = ["ErrorBudgetReadModel", "SloWindow", "SLOTarget", "STAGING_SLOS", "error_budgets"]
