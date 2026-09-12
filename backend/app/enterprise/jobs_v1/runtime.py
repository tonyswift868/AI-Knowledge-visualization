"""Production Procrastinate runtime for the existing durable workflow ledger."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

import procrastinate
import sqlalchemy as sa
from sqlalchemy.engine import Engine, URL

from app.observability.runtime import business_span
from app.enterprise_tenancy.context import require_safe_application_role
from app.enterprise_tenancy.schema import ENTERPRISE_MIGRATION_REVISION

from .learning_runs import LearningRunOrchestrator
from .models import FailureClass, JobRecord, JobStatus, WorkflowKind
from .ports import TenantContextRevalidatorPort, WorkflowHandlerPort
from .procrastinate_adapter import (
    PROCRASTINATE_EVALUATED_VERSION,
    ProcrastinateOutboxDispatcher,
    ProcrastinateWorkerPolicy,
)
from .repository import WorkflowRepository
from .retry import WorkflowFailure
from .service import (
    OutboxPublisher,
    TenantRevalidationRejected,
    WorkflowCancellationRequested,
    WorkflowService,
)


logger = logging.getLogger(__name__)
TASK_NAME = "mentorflow.enterprise.execute_workflow"


class WorkerConfigurationError(RuntimeError):
    """Configuration/readiness failure that never includes a database URL."""


@dataclass(frozen=True, slots=True)
class DurableWorkerDependencies:
    revalidator: TenantContextRevalidatorPort
    handlers: Mapping[WorkflowKind, WorkflowHandlerPort]

    @property
    def missing_workflow_kinds(self) -> tuple[WorkflowKind, ...]:
        registered = {
            kind
            for kind in self.handlers
            if type(kind) is WorkflowKind
        }
        return tuple(kind for kind in WorkflowKind if kind not in registered)

    @property
    def unsupported_workflow_kinds(self) -> tuple[object, ...]:
        return tuple(
            kind for kind in self.handlers if type(kind) is not WorkflowKind
        )

    @property
    def invalid_workflow_handlers(self) -> tuple[WorkflowKind, ...]:
        return tuple(
            kind
            for kind, handler in self.handlers.items()
            if type(kind) is WorkflowKind
            and not callable(getattr(handler, "execute", None))
        )


@dataclass(frozen=True, slots=True)
class WorkerRuntimeConfig:
    database_url: URL
    runtime_factory: str
    policy: ProcrastinateWorkerPolicy = ProcrastinateWorkerPolicy()
    worker_name: str = "mentorflow-worker"
    maintenance_interval_seconds: float = 2.0
    shutdown_graceful_timeout_seconds: float = 30.0
    job_lease_seconds: int = 60
    readiness_path: Path = Path("/run/mentorflow/worker-readiness.json")

    def __post_init__(self) -> None:
        if self.database_url.drivername != "postgresql+psycopg":
            raise WorkerConfigurationError(
                "worker database URL must use postgresql+psycopg"
            )
        if self.database_url.username != "mentorflow_app":
            raise WorkerConfigurationError(
                "worker database URL must use the non-owner mentorflow_app role"
            )
        module_name, separator, attribute = self.runtime_factory.partition(":")
        if (
            not separator
            or not module_name.strip()
            or not attribute.strip()
            or ":" in attribute
        ):
            raise WorkerConfigurationError(
                "MENTORFLOW_WORKER_RUNTIME_FACTORY must be module:callable"
            )
        if (
            not 1 <= len(self.worker_name) <= 120
            or not self.worker_name.isprintable()
        ):
            raise WorkerConfigurationError("worker name is invalid")
        if not 0.2 <= self.maintenance_interval_seconds <= 60:
            raise WorkerConfigurationError("worker maintenance interval is invalid")
        if not 10 <= self.shutdown_graceful_timeout_seconds <= 300:
            raise WorkerConfigurationError("worker shutdown timeout is invalid")
        if not 15 <= self.job_lease_seconds <= 3600:
            raise WorkerConfigurationError("worker job lease is invalid")
        if not (
            self.readiness_path.is_absolute()
            or self.readiness_path.as_posix().startswith("/")
        ):
            raise WorkerConfigurationError("worker readiness path must be absolute")

    @property
    def conninfo(self) -> str:
        return self.database_url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )


def load_worker_dependencies(
    dotted_path: str,
    *,
    engine: Engine,
    require_complete: bool = True,
) -> DurableWorkerDependencies:
    module_name, separator, attribute = dotted_path.partition(":")
    if not separator or not module_name or not attribute:
        raise WorkerConfigurationError("worker runtime factory path is invalid")
    try:
        factory = getattr(importlib.import_module(module_name), attribute)
        dependencies = factory(engine=engine)
    except WorkerConfigurationError:
        raise
    except Exception as exc:
        raise WorkerConfigurationError("worker runtime factory failed to load") from exc
    if not isinstance(dependencies, DurableWorkerDependencies):
        raise WorkerConfigurationError(
            "worker runtime factory returned an invalid dependency bundle"
        )
    if not isinstance(dependencies.handlers, Mapping):
        raise WorkerConfigurationError(
            "worker runtime factory returned an invalid handler registry"
        )
    if not callable(getattr(dependencies.revalidator, "revalidate", None)):
        raise WorkerConfigurationError(
            "worker runtime factory returned an invalid tenant revalidator"
        )
    if dependencies.unsupported_workflow_kinds:
        raise WorkerConfigurationError(
            "worker runtime factory registered an unknown workflow kind"
        )
    if dependencies.invalid_workflow_handlers:
        raise WorkerConfigurationError(
            "worker runtime factory registered an invalid workflow handler"
        )
    if require_complete and dependencies.missing_workflow_kinds:
        raise WorkerConfigurationError(
            "worker runtime factory does not register every workflow kind"
        )
    return dependencies


def verify_worker_database(engine: Engine) -> None:
    """Require canonical Alembic, safe role, grants, and Procrastinate schema."""

    from app.enterprise_tenancy.import_ledger_schema import require_account_import_ledger_schema

    if engine.dialect.name != "postgresql":
        raise WorkerConfigurationError("production worker requires PostgreSQL")
    try:
        with engine.connect() as connection:
            require_safe_application_role(connection)
            revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            if revision != ENTERPRISE_MIGRATION_REVISION:
                raise WorkerConfigurationError(
                    "worker database is not at canonical Alembic head"
                )
            require_account_import_ledger_schema(connection)
            connection.execute(sa.text("SELECT job_id FROM enterprise_jobs.jobs LIMIT 0"))
            connection.execute(sa.text("SELECT id FROM procrastinate_jobs LIMIT 0"))
            connection.execute(sa.text("SELECT id FROM procrastinate_workers LIMIT 0"))
    except WorkerConfigurationError:
        raise
    except Exception as exc:
        raise WorkerConfigurationError(
            "worker database schema or runtime grants are unavailable"
        ) from exc


class _ClaimHeartbeat:
    def __init__(
        self,
        engine: Engine,
        repository: WorkflowRepository,
        *,
        job_id: str,
        claim_token: str,
        lease_seconds: int,
    ) -> None:
        self.engine = engine
        self.repository = repository
        self.job_id = job_id
        self.claim_token = claim_token
        self.lease_seconds = lease_seconds
        self.stop_event = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name=f"mentorflow-job-heartbeat-{job_id[-8:]}",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while not self.stop_event.wait(interval):
            try:
                with self.engine.begin() as connection:
                    alive = self.repository.heartbeat(
                        connection,
                        self.job_id,
                        self.claim_token,
                        now=datetime.now(timezone.utc),
                        lease_seconds=self.lease_seconds,
                    )
                if not alive:
                    self.lost.set()
                    return
            except Exception as exc:
                self.lost.set()
                logger.error(
                    "Durable job heartbeat failed",
                    extra={
                        "action": "job_heartbeat_failed",
                        "error_code": type(exc).__name__[:120],
                    },
                )
                return


class DurableJobExecutor:
    """Execute one Procrastinate delivery against the canonical job ledger."""

    def __init__(
        self,
        engine: Engine,
        dependencies: DurableWorkerDependencies,
        *,
        repository: WorkflowRepository | None = None,
        service: WorkflowService | None = None,
        learning_run_orchestrator: LearningRunOrchestrator | None = None,
        job_lease_seconds: int = 60,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.engine = engine
        self.dependencies = dependencies
        self.repository = repository or WorkflowRepository()
        self.service = service or WorkflowService(repository=self.repository)
        self.learning_run_orchestrator = (
            learning_run_orchestrator
            or LearningRunOrchestrator(repository=self.repository)
        )
        self.job_lease_seconds = job_lease_seconds
        self.clock = clock

    def _current(self, job_id: str) -> JobRecord | None:
        with self.engine.begin() as connection:
            return self.repository.get(connection, job_id)

    def execute(
        self,
        *,
        job_id: str,
        dispatch_generation: int,
        abort_requested: Callable[[], bool] = lambda: False,
    ) -> JobRecord | None:
        denied = False
        job: JobRecord | None = None
        with self.engine.begin() as connection:
            try:
                job = self.service.claim_and_revalidate(
                    connection,
                    job_id,
                    revalidator=self.dependencies.revalidator,
                    now=self.clock(),
                    lease_seconds=self.job_lease_seconds,
                    expected_dispatch_generation=dispatch_generation,
                )
            except TenantRevalidationRejected:
                # Catch inside the transaction so the dead-letter transition commits.
                denied = True
        if denied or job is None:
            return self._current(job_id)

        claim_token = job.claim_token or ""
        handler = self.dependencies.handlers.get(job.workflow_kind)
        if handler is None:
            with self.engine.begin() as connection:
                return self.service.fail(
                    connection,
                    job,
                    WorkflowFailure(
                        FailureClass.NON_RETRYABLE,
                        "WORKFLOW_HANDLER_NOT_REGISTERED",
                        "Workflow handler is not registered",
                    ),
                    now=self.clock(),
                )

        heartbeat = _ClaimHeartbeat(
            self.engine,
            self.repository,
            job_id=job.job_id,
            claim_token=claim_token,
            lease_seconds=self.job_lease_seconds,
        )

        def ensure_not_cancelled() -> None:
            if heartbeat.lost.is_set() or abort_requested():
                raise WorkflowFailure(
                    FailureClass.WORKER_LOST,
                    "WORKER_ABORT_REQUESTED",
                    "Worker stopped before workflow completion",
                )
            with self.engine.begin() as connection:
                if self.repository.is_cancel_requested(connection, job.job_id):
                    raise WorkflowCancellationRequested(job.job_id)

        def checkpoint(phase: str, value: dict[str, Any]) -> None:
            ensure_not_cancelled()
            with self.engine.begin() as connection:
                self.repository.record_checkpoint(
                    connection,
                    job.job_id,
                    claim_token,
                    phase=phase,
                    checkpoint=value,
                    evidence=None,
                    now=self.clock(),
                )

        heartbeat.start()
        try:
            ensure_not_cancelled()
            result = handler.execute(
                job=job,
                checkpoint=checkpoint,
                ensure_not_cancelled=ensure_not_cancelled,
            )
            ensure_not_cancelled()
            with self.engine.begin() as connection:
                if self.learning_run_orchestrator.manages(job):
                    return self.learning_run_orchestrator.complete_stage(
                        connection,
                        job,
                        result=result,
                        now=self.clock(),
                    )
                return self.repository.mark_succeeded(
                    connection,
                    job.job_id,
                    claim_token,
                    result=result,
                    now=self.clock(),
                )
        except WorkflowCancellationRequested:
            with self.engine.begin() as connection:
                current = self.repository.get(connection, job.job_id)
                if (
                    current is not None
                    and current.status is JobStatus.RUNNING
                    and current.claim_token == claim_token
                ):
                    return self.repository.finalize_cancelled(
                        connection, job.job_id, claim_token, now=self.clock()
                    )
            return self._current(job.job_id)
        except WorkflowFailure as failure:
            with self.engine.begin() as connection:
                current = self.repository.get(connection, job.job_id)
                if (
                    current is not None
                    and current.status is JobStatus.RUNNING
                    and current.claim_token == claim_token
                ):
                    return self.service.fail(
                        connection, current, failure, now=self.clock()
                    )
            return self._current(job.job_id)
        except Exception as exc:
            logger.error(
                "Workflow handler failed",
                extra={
                    "action": "workflow_handler_failed",
                    "error_code": type(exc).__name__[:120],
                },
            )
            failure = WorkflowFailure(
                FailureClass.RETRYABLE,
                "WORKFLOW_HANDLER_ERROR",
                "Workflow handler failed",
            )
            with self.engine.begin() as connection:
                current = self.repository.get(connection, job.job_id)
                if (
                    current is not None
                    and current.status is JobStatus.RUNNING
                    and current.claim_token == claim_token
                ):
                    return self.service.fail(
                        connection, current, failure, now=self.clock()
                    )
            return self._current(job.job_id)
        finally:
            heartbeat.stop()


def build_procrastinate_app(
    config: WorkerRuntimeConfig,
    executor: DurableJobExecutor,
) -> tuple[procrastinate.App, Any]:
    if metadata.version("procrastinate") != PROCRASTINATE_EVALUATED_VERSION:
        raise WorkerConfigurationError("unexpected Procrastinate runtime version")
    connector = procrastinate.PsycopgConnector(
        conninfo=config.conninfo,
        min_size=1,
        max_size=max(2, config.policy.concurrency + 1),
    )
    app = procrastinate.App(connector=connector)
    defer_app = procrastinate.App(
        connector=procrastinate.SyncPsycopgConnector(
            conninfo=config.conninfo,
            min_size=1,
            max_size=1,
        )
    )

    async def execute_workflow_impl(
        context: procrastinate.JobContext,
        *,
        job_id: str,
        dispatch_generation: int,
    ) -> str:
        with business_span(
            "enterprise_jobs.execute",
            {"mentorflow.dispatch_generation": dispatch_generation},
        ):
            record = await asyncio.to_thread(
                executor.execute,
                job_id=job_id,
                dispatch_generation=dispatch_generation,
                abort_requested=context.should_abort,
            )
        return record.status.value if record is not None else "MISSING"

    app.task(name=TASK_NAME, pass_context=True, queue=config.policy.queues[0])(
        execute_workflow_impl
    )
    defer_task = defer_app.task(
        name=TASK_NAME,
        pass_context=True,
        queue=config.policy.queues[0],
    )(execute_workflow_impl)
    return app, defer_task


class WorkerSupervisor:
    """Maintain outbox publication, recovery, and a non-static readiness file."""

    def __init__(
        self,
        config: WorkerRuntimeConfig,
        engine: Engine,
        task: Any,
        *,
        repository: WorkflowRepository | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.repository = repository or WorkflowRepository()
        self.publisher = OutboxPublisher(repository=self.repository)
        self.service = WorkflowService(repository=self.repository)
        self.dispatcher = ProcrastinateOutboxDispatcher(task)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name="mentorflow-worker-maintenance",
            daemon=True,
        )
        self.last_error_code: str | None = None

    def run_once(self) -> tuple[int, int]:
        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            recovered = self.service.recover_expired(connection, now=now)
        with self.engine.begin() as connection:
            published = self.publisher.publish_batch(
                connection, self.dispatcher, now=now
            )
        return len(recovered), len(published)

    def _write_readiness(self, status: str) -> None:
        payload = {
            "schema_version": "mentorflow.worker-readiness.v1",
            "status": status,
            "pid": os.getpid(),
            "worker": self.config.worker_name,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "error_code": self.last_error_code,
        }
        path = self.config.readiness_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def start(self) -> None:
        self.run_once()
        self.last_error_code = None
        self._write_readiness("READY")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=10)
        try:
            self.config.readiness_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "Worker readiness cleanup failed",
                extra={
                    "action": "worker_readiness_cleanup_failed",
                    "error_code": type(exc).__name__[:120],
                },
            )

    def _run(self) -> None:
        while not self.stop_event.wait(self.config.maintenance_interval_seconds):
            try:
                self.run_once()
                self.last_error_code = None
                self._write_readiness("READY")
            except Exception as exc:
                self.last_error_code = type(exc).__name__[:120]
                self._write_readiness("DEGRADED")
                logger.error(
                    "Worker maintenance failed",
                    extra={
                        "action": "worker_maintenance_failed",
                        "error_code": self.last_error_code,
                    },
                )


def read_readiness(path: Path, *, maximum_age_seconds: float = 30) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated_at = datetime.fromisoformat(str(payload["updated_at"]))
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
    except Exception as exc:
        raise WorkerConfigurationError("worker readiness evidence is unavailable") from exc
    if payload.get("status") != "READY" or age < 0 or age > maximum_age_seconds:
        raise WorkerConfigurationError("worker readiness evidence is stale or degraded")
    return payload


__all__ = [
    "DurableJobExecutor",
    "DurableWorkerDependencies",
    "TASK_NAME",
    "WorkerConfigurationError",
    "WorkerRuntimeConfig",
    "WorkerSupervisor",
    "build_procrastinate_app",
    "load_worker_dependencies",
    "read_readiness",
    "verify_worker_database",
]
