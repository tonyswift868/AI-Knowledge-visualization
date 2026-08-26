"""Fail-closed environment inventory for a real Linux staging host."""

from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Callable

from .adapters import CommandRunner, HttpClient, TcpProber, record_command
from .models import CheckResult, EnterpriseStagingEvidenceBundle, VerificationStatus, utc_now
from .settings import VerificationSettings


def _check(
    check_id: str,
    status: VerificationStatus,
    summary: str,
    *,
    details: dict[str, object] | None = None,
    started_at: str | None = None,
    started: float | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        category="preflight",
        status=status,
        started_at=started_at or utc_now(),
        finished_at=utc_now(),
        duration_ms=max(0, int((time.perf_counter() - (started or time.perf_counter())) * 1000)),
        summary=summary,
        details=details or {},
    )


def _executable_check(
    bundle: EnterpriseStagingEvidenceBundle,
    runner: CommandRunner,
    executable: str,
    version_argv: tuple[str, ...],
) -> CheckResult:
    started_at, started = utc_now(), time.perf_counter()
    resolved = runner.which(executable)
    if not resolved:
        return _check(
            f"preflight.executable.{executable}",
            VerificationStatus.BLOCKED,
            f"{executable} is missing",
            details={"present": False},
            started_at=started_at,
            started=started,
        )
    execution = record_command(bundle, runner, version_argv, capture_version=True)
    record = bundle.commands[-1]
    if execution.exit_code != 0:
        return _check(
            f"preflight.executable.{executable}",
            VerificationStatus.FAIL,
            f"{executable} version command failed",
            details={"present": True, "command_id": record.command_id},
            started_at=started_at,
            started=started,
        )
    if record.version_summary:
        bundle.versions[executable] = record.version_summary
    return _check(
        f"preflight.executable.{executable}",
        VerificationStatus.PASS,
        f"{executable} is available",
        details={"present": True, "command_id": record.command_id},
        started_at=started_at,
        started=started,
    )


def _endpoint_check(
    http: HttpClient,
    check_id: str,
    url: str,
    *,
    validator: Callable[[int, str], tuple[bool, dict[str, object]]] | None = None,
) -> CheckResult:
    started_at, started = utc_now(), time.perf_counter()
    response = http.request("GET", url, timeout_seconds=3.0)
    details: dict[str, object] = {"status_code": response.status_code}
    passed = response.status_code == 200
    if validator is not None and passed:
        passed, extra = validator(response.status_code, response.body)
        details.update(extra)
    return _check(
        check_id,
        VerificationStatus.PASS if passed else VerificationStatus.BLOCKED,
        "endpoint is ready" if passed else "endpoint is unavailable or invalid",
        details=details,
        started_at=started_at,
        started=started,
    )


def _grafana_health(_status: int, body: str) -> tuple[bool, dict[str, object]]:
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return False, {"json": False}
    database = str(payload.get("database") or "").casefold()
    details = {
        "json": True,
        "database": database,
        "version": str(payload.get("version") or "unknown")[:80],
    }
    return database == "ok", details


def run_preflight(
    settings: VerificationSettings,
    bundle: EnterpriseStagingEvidenceBundle,
    *,
    runner: CommandRunner,
    http: HttpClient,
    tcp: TcpProber,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    system = platform.system()
    linux = system.casefold() == "linux"
    results.append(
        _check(
            "preflight.linux",
            VerificationStatus.PASS if linux else VerificationStatus.BLOCKED,
            "Linux host detected" if linux else "verification requires Linux staging",
            details={"system": system, "release": platform.release()[:120]},
        )
    )

    for executable, argv in (
        ("docker", ("docker", "--version")),
        ("psql", ("psql", "--version")),
        ("pg_dump", ("pg_dump", "--version")),
        ("pg_restore", ("pg_restore", "--version")),
        ("k6", ("k6", "version")),
        ("promtool", ("promtool", "--version")),
    ):
        results.append(_executable_check(bundle, runner, executable, argv))

    docker_present = runner.which("docker") is not None
    if docker_present:
        started_at, started = utc_now(), time.perf_counter()
        execution = record_command(
            bundle,
            runner,
            ("docker", "compose", "version"),
            capture_version=True,
        )
        command = bundle.commands[-1]
        if command.version_summary:
            bundle.versions["docker_compose"] = command.version_summary
        results.append(
            _check(
                "preflight.docker_compose",
                VerificationStatus.PASS if execution.exit_code == 0 else VerificationStatus.BLOCKED,
                "Docker Compose is available" if execution.exit_code == 0 else "Docker Compose is unavailable",
                details={"command_id": command.command_id},
                started_at=started_at,
                started=started,
            )
        )
    else:
        results.append(
            _check(
                "preflight.docker_compose",
                VerificationStatus.BLOCKED,
                "Docker Compose cannot run without Docker",
                details={"docker_present": False},
            )
        )

    started_at, started = utc_now(), time.perf_counter()
    postgres_ready = tcp.reachable(settings.postgres_host, settings.postgres_port)
    results.append(
        _check(
            "preflight.postgres",
            VerificationStatus.PASS if postgres_ready else VerificationStatus.BLOCKED,
            "PostgreSQL TCP endpoint is reachable" if postgres_ready else "PostgreSQL TCP endpoint is unreachable",
            details={"reachable": postgres_ready, "port": settings.postgres_port},
            started_at=started_at,
            started=started,
        )
    )

    results.extend(
        (
            _endpoint_check(http, "preflight.otel_collector", settings.collector_health_url),
            _endpoint_check(http, "preflight.prometheus", f"{settings.prometheus_url}/-/ready"),
            _endpoint_check(http, "preflight.grafana", f"{settings.grafana_url}/api/health", validator=_grafana_health),
        )
    )

    started_at, started = utc_now(), time.perf_counter()
    if settings.object_store_root is not None:
        root = settings.object_store_root
        ready = root.exists() and root.is_dir()
        result = _check(
            "preflight.object_store",
            VerificationStatus.PASS if ready else VerificationStatus.BLOCKED,
            "object store directory is present" if ready else "object store directory is unavailable",
            details={"configured": True, "kind": "filesystem", "present": ready},
            started_at=started_at,
            started=started,
        )
    elif settings.object_store_health_url:
        result = _endpoint_check(
            http,
            "preflight.object_store",
            settings.object_store_health_url,
        )
    else:
        result = _check(
            "preflight.object_store",
            VerificationStatus.BLOCKED,
            "object store probe is not configured",
            details={"configured": False},
            started_at=started_at,
            started=started,
        )
    results.append(result)

    compose_ready = settings.compose_file.is_file()
    results.append(
        _check(
            "preflight.observability_compose_file",
            VerificationStatus.PASS if compose_ready else VerificationStatus.BLOCKED,
            "observability Compose file is present" if compose_ready else "observability Compose file is missing",
            details={"present": compose_ready},
        )
    )
    return results


__all__ = ["run_preflight"]
