"""Fail-closed process entrypoint for the Enterprise durable worker."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app import orm
from app.enterprise.jobs_v1.postgres_schema import apply_worker_schema
from app.enterprise.jobs_v1.procrastinate_adapter import ProcrastinateWorkerPolicy
from app.enterprise.jobs_v1.runtime import (
    DurableJobExecutor,
    WorkerConfigurationError,
    WorkerRuntimeConfig,
    WorkerSupervisor,
    build_procrastinate_app,
    load_worker_dependencies,
    read_readiness,
    verify_worker_database,
)
from app.observability.runtime import (
    configure_worker_open_telemetry,
    force_flush_open_telemetry,
    shutdown_open_telemetry,
)
from app.knowledge_policy import CONVERSION_POLICY
from app.sources.document.preflight import probe_docling_dependency


logger = logging.getLogger(__name__)
OTEL_SHUTDOWN_TIMEOUT_MILLIS = 5_000


def _integer(
    source: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(source.get(name) or default))
    except ValueError as exc:
        raise WorkerConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise WorkerConfigurationError(f"{name} is outside the supported range")
    return value


def _number(
    source: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(str(source.get(name) or default))
    except ValueError as exc:
        raise WorkerConfigurationError(f"{name} must be numeric") from exc
    if not minimum <= value <= maximum:
        raise WorkerConfigurationError(f"{name} is outside the supported range")
    return value


def load_config(source: Mapping[str, str] | None = None) -> WorkerRuntimeConfig:
    env = os.environ if source is None else source
    database_url = orm.resolve_runtime_database_url(env)
    queues = tuple(
        value.strip()
        for value in str(
            env.get("MENTORFLOW_WORKER_QUEUES") or "mentorflow.enterprise"
        ).split(",")
        if value.strip()
    )
    if (
        not queues
        or len(set(queues)) != len(queues)
        or any(len(queue) > 120 or not queue.isprintable() for queue in queues)
    ):
        raise WorkerConfigurationError(
            "MENTORFLOW_WORKER_QUEUES must contain unique non-empty queues"
        )
    policy = ProcrastinateWorkerPolicy(
        queues=queues,
        concurrency=_integer(
            env, "MENTORFLOW_WORKER_CONCURRENCY", 4, minimum=1, maximum=64
        ),
        stalled_worker_timeout_seconds=_integer(
            env,
            "MENTORFLOW_WORKER_STALLED_TIMEOUT_SECONDS",
            30,
            minimum=10,
            maximum=3600,
        ),
    )
    return WorkerRuntimeConfig(
        database_url=database_url,
        runtime_factory=str(env.get("MENTORFLOW_WORKER_RUNTIME_FACTORY") or "").strip(),
        policy=policy,
        worker_name=str(env.get("MENTORFLOW_WORKER_NAME") or "mentorflow-worker").strip(),
        maintenance_interval_seconds=_number(
            env,
            "MENTORFLOW_WORKER_MAINTENANCE_INTERVAL_SECONDS",
            2.0,
            minimum=0.2,
            maximum=60.0,
        ),
        shutdown_graceful_timeout_seconds=_number(
            env,
            "MENTORFLOW_WORKER_SHUTDOWN_TIMEOUT_SECONDS",
            30.0,
            minimum=10.0,
            maximum=300.0,
        ),
        job_lease_seconds=_integer(
            env,
            "MENTORFLOW_WORKER_JOB_LEASE_SECONDS",
            60,
            minimum=15,
            maximum=3600,
        ),
        readiness_path=Path(
            str(
                env.get("MENTORFLOW_WORKER_READINESS_PATH")
                or "/run/mentorflow/worker-readiness.json"
            )
        ),
    )


def _json_status(status: str, code: str | None = None) -> None:
    payload: dict[str, Any] = {
        "schema_version": "mentorflow.worker-status.v1",
        "status": status,
    }
    if code is not None:
        payload["code"] = code
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _build_runtime(config: WorkerRuntimeConfig, engine: Any):
    dependencies = load_worker_dependencies(
        config.runtime_factory,
        engine=engine,
        require_complete=True,
    )
    executor = DurableJobExecutor(
        engine,
        dependencies,
        job_lease_seconds=config.job_lease_seconds,
    )
    app, task = build_procrastinate_app(config, executor)
    return app, task


def _require_document_runtime(source: Mapping[str, str]) -> None:
    configured = str(
        source.get("MENTORFLOW_WORKER_DOCUMENT_RUNTIME_REQUIRED") or "false"
    ).strip().casefold()
    if configured in {"", "0", "false", "no"}:
        return
    if configured not in {"1", "true", "yes"}:
        raise WorkerConfigurationError(
            "MENTORFLOW_WORKER_DOCUMENT_RUNTIME_REQUIRED must be true or false"
        )
    dependency = probe_docling_dependency()
    expected_version = str(CONVERSION_POLICY.docling.version)
    if not dependency.available or dependency.version != expected_version:
        raise WorkerConfigurationError(
            "required Docling runtime is unavailable or version-mismatched"
        )
    try:
        importlib.import_module("docling.document_converter")
    except Exception as exc:
        raise WorkerConfigurationError(
            "required Docling document converter is unavailable"
        ) from exc


def _check_connection(app: Any) -> None:
    try:
        with app.open():
            if not app.check_connection():
                raise WorkerConfigurationError(
                    "Procrastinate connection readiness check failed"
                )
    except WorkerConfigurationError:
        raise
    except Exception as exc:
        raise WorkerConfigurationError(
            "Procrastinate connection readiness check failed"
        ) from exc


def _stop_worker_runtime(supervisor: WorkerSupervisor | None, engine: Any) -> None:
    """Stop every runtime layer without leaking teardown exception details."""

    if supervisor is not None:
        try:
            supervisor.stop()
        except Exception as exc:
            logger.error(
                "Durable worker supervisor shutdown failed",
                extra={
                    "action": "durable_worker_supervisor_shutdown_failed",
                    "error_code": type(exc).__name__[:120],
                },
            )
    try:
        force_flush_open_telemetry(OTEL_SHUTDOWN_TIMEOUT_MILLIS)
    except Exception as exc:
        logger.error(
            "Durable worker telemetry flush failed",
            extra={
                "action": "durable_worker_telemetry_flush_failed",
                "error_code": type(exc).__name__[:120],
            },
        )
    try:
        shutdown_open_telemetry(OTEL_SHUTDOWN_TIMEOUT_MILLIS)
    except Exception as exc:
        logger.error(
            "Durable worker telemetry shutdown failed",
            extra={
                "action": "durable_worker_telemetry_shutdown_failed",
                "error_code": type(exc).__name__[:120],
            },
        )
    try:
        engine.dispose()
    except Exception as exc:
        logger.error(
            "Durable worker database disposal failed",
            extra={
                "action": "durable_worker_database_disposal_failed",
                "error_code": type(exc).__name__[:120],
            },
        )


def _run(source: Mapping[str, str]) -> int:
    config = load_config(source)
    _require_document_runtime(source)
    engine = orm.configure_enterprise_orm(source)
    supervisor: WorkerSupervisor | None = None
    try:
        configure_worker_open_telemetry(sqlalchemy_engine=engine)
        verify_worker_database(engine)
        app, task = _build_runtime(config, engine)
        _check_connection(app)
        supervisor = WorkerSupervisor(config, engine, task)
        supervisor.start()
        app.run_worker(
            queues=list(config.policy.queues),
            name=config.worker_name,
            concurrency=config.policy.concurrency,
            wait=True,
            shutdown_graceful_timeout=config.shutdown_graceful_timeout_seconds,
            stalled_worker_timeout=config.policy.stalled_worker_timeout_seconds,
            delete_jobs="never",
        )
        return 0
    finally:
        _stop_worker_runtime(supervisor, engine)


def _health(source: Mapping[str, str]) -> int:
    config = load_config(source)
    read_readiness(
        config.readiness_path,
        maximum_age_seconds=max(10.0, config.maintenance_interval_seconds * 3),
    )
    engine = orm.configure_enterprise_orm(source)
    try:
        verify_worker_database(engine)
        _json_status("READY")
        return 0
    finally:
        engine.dispose()


def _schema_apply(source: Mapping[str, str]) -> int:
    migration_url = orm.resolve_migration_database_url("", source)
    apply_worker_schema(migration_url)
    _json_status("SCHEMA_READY")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MentorFlow Enterprise durable worker")
    parser.add_argument(
        "command",
        choices=("run", "health", "check-config", "schema-apply"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "check-config":
            load_config(os.environ)
            _json_status("CONFIG_VALID")
            return 0
        if arguments.command == "schema-apply":
            return _schema_apply(os.environ)
        if arguments.command == "health":
            return _health(os.environ)
        return _run(os.environ)
    except Exception as exc:
        code = type(exc).__name__[:120]
        logger.error(
            "Durable worker command failed",
            extra={"action": "durable_worker_command_failed", "error_code": code},
        )
        _json_status("NOT_READY", code)
        return 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["load_config", "main"]
