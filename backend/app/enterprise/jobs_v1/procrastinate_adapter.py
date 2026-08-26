"""Dependency-free seam for the leased future Procrastinate runtime.

Procrastinate is deliberately *not* imported here.  E1 dependency coordination
marks both Procrastinate and a PostgreSQL driver unleased.  The adapter accepts
an official-shape Task object and uses the 3.8+ external-connection API so the
queue INSERT and outbox acknowledgement share one transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from sqlalchemy.engine import Connection

from .models import OutboxRecord


PROCRASTINATE_MINIMUM_VERSION = "3.8.0"
PROCRASTINATE_EVALUATED_VERSION = "3.9.0"


class _ConfiguredTask(Protocol):
    def defer(self, **task_kwargs: Any) -> int: ...


class ProcrastinateTaskPort(Protocol):
    def configure(self, **options: Any) -> _ConfiguredTask: ...


@dataclass(frozen=True, slots=True)
class ProcrastinateWorkerPolicy:
    queues: tuple[str, ...] = ("mentorflow.enterprise",)
    concurrency: int = 4
    stalled_worker_timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if not self.queues or self.concurrency < 1 or self.concurrency > 64:
            raise ValueError("invalid Procrastinate worker policy")
        if self.stalled_worker_timeout_seconds < 10:
            raise ValueError("stalled worker timeout must be at least 10 seconds")


class ProcrastinateOutboxDispatcher:
    """Atomically defer one redacted outbox event through Procrastinate."""

    def __init__(
        self,
        task: ProcrastinateTaskPort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.task = task
        self.clock = clock

    def dispatch(self, connection: Connection, event: OutboxRecord) -> str:
        options: dict[str, Any] = {
            "connection": connection,
            "queue": event.queue,
            "lock": event.lock_key,
            "queueing_lock": f"mentorflow:{event.job_id}:{event.dispatch_generation}",
        }
        now = self.clock()
        available_at = event.available_at
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=timezone.utc)
        if available_at > now:
            options["schedule_at"] = available_at
        procrastinate_job_id = self.task.configure(**options).defer(
            job_id=event.job_id,
            dispatch_generation=event.dispatch_generation,
        )
        return str(procrastinate_job_id)


__all__ = [
    "PROCRASTINATE_EVALUATED_VERSION",
    "PROCRASTINATE_MINIMUM_VERSION",
    "ProcrastinateOutboxDispatcher",
    "ProcrastinateTaskPort",
    "ProcrastinateWorkerPolicy",
]
