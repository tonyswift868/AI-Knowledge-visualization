"""Procrastinate adapter for the canonical transactional outbox.

The application uses SQLAlchemy with psycopg 3 while Procrastinate's external
connection API expects the underlying ``psycopg.Connection``.  Unwrapping is
therefore explicit and fail-closed: a SQLite connection, another DBAPI driver,
or a connection outside a transaction can never become a production defer.
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


def unwrap_psycopg_connection(connection: Connection) -> Any:
    """Return the transaction-owning psycopg 3 connection used by SQLAlchemy."""

    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise RuntimeError(
            "Procrastinate defer requires an active PostgreSQL transaction"
        )
    proxied = getattr(connection, "connection", None)
    raw = getattr(proxied, "driver_connection", None)
    module_name = type(raw).__module__.split(".", 1)[0]
    if raw is None or module_name != "psycopg":
        raise RuntimeError("Procrastinate defer requires the psycopg 3 driver")
    return raw


class ProcrastinateOutboxDispatcher:
    """Atomically defer one redacted outbox event through Procrastinate."""

    def __init__(
        self,
        task: ProcrastinateTaskPort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        connection_unwrapper: Callable[[Connection], Any] = unwrap_psycopg_connection,
    ) -> None:
        self.task = task
        self.clock = clock
        self.connection_unwrapper = connection_unwrapper

    def dispatch(self, connection: Connection, event: OutboxRecord) -> str:
        options: dict[str, Any] = {
            "connection": self.connection_unwrapper(connection),
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
    "unwrap_psycopg_connection",
]
