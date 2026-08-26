"""Explicitly authorized staging incident drills with recovery evidence."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from .adapters import Clock, CommandRunner, HttpClient, record_command
from .models import CheckResult, EnterpriseStagingEvidenceBundle, VerificationStatus, utc_now
from .restore_drill import RestoreDrillError, compare_integrity, parse_integrity_snapshot
from .settings import VerificationSettings


INCIDENT_PLAN_SCHEMA = "mentorflow.enterprise-incident-plan.v1"
_SERVICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ENVIRONMENT_ID = re.compile(r"staging-[a-z0-9][a-z0-9-]{2,63}$")
_SCENARIOS = ("postgres", "object_store", "worker", "keycloak")


class IncidentDrillError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ServiceDrill:
    service: str
    probe_url: str


@dataclass(frozen=True, slots=True)
class IncidentDrillPlan:
    environment: str
    environment_id: str
    compose_file: Path
    approved_tool_root: Path
    services: Mapping[str, ServiceDrill]
    integrity_command: tuple[str, ...]
    expected_integrity: Mapping[str, object]
    timeout_seconds: int


def _absolute(payload: Mapping[str, object], name: str) -> Path:
    path = Path(str(payload.get(name) or ""))
    if not path.is_absolute():
        raise IncidentDrillError(f"{name.upper()}_NOT_ABSOLUTE")
    return path.resolve()


def _http_url(value: object) -> str:
    result = str(value or "").rstrip("/")
    parsed = urlparse(result)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise IncidentDrillError("PROBE_URL_INVALID")
    return result


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def load_incident_plan(path: Path) -> IncidentDrillPlan:
    if not path.is_absolute():
        raise IncidentDrillError("PLAN_PATH_NOT_ABSOLUTE")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise IncidentDrillError("PLAN_INVALID") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != INCIDENT_PLAN_SCHEMA:
        raise IncidentDrillError("PLAN_SCHEMA_INVALID")
    services_payload = payload.get("services")
    if not isinstance(services_payload, Mapping) or set(services_payload) != set(_SCENARIOS):
        raise IncidentDrillError("SERVICES_INVALID")
    services: dict[str, ServiceDrill] = {}
    for scenario in _SCENARIOS:
        item = services_payload[scenario]
        if not isinstance(item, Mapping) or set(item) != {"service", "probe_url"}:
            raise IncidentDrillError("SERVICE_FIELDS_INVALID")
        service = str(item["service"])
        if not _SERVICE_NAME.fullmatch(service):
            raise IncidentDrillError("SERVICE_NAME_INVALID")
        services[scenario] = ServiceDrill(service=service, probe_url=_http_url(item["probe_url"]))
    raw_command = payload.get("integrity_command")
    if (
        not isinstance(raw_command, list)
        or not raw_command
        or not all(isinstance(item, str) and item for item in raw_command)
    ):
        raise IncidentDrillError("INTEGRITY_COMMAND_INVALID")
    tool_root = _absolute(payload, "approved_tool_root")
    executable = Path(raw_command[0])
    if not executable.is_absolute() or not _within(executable.resolve(), tool_root):
        raise IncidentDrillError("COMMAND_OUTSIDE_APPROVED_TOOL_ROOT")
    expected = payload.get("expected_integrity")
    if not isinstance(expected, Mapping):
        raise IncidentDrillError("EXPECTED_INTEGRITY_MISSING")
    expected = parse_integrity_snapshot(json.dumps(dict(expected), separators=(",", ":")))
    timeout = int(payload.get("timeout_seconds") or 60)
    if not 5 <= timeout <= 300:
        raise IncidentDrillError("TIMEOUT_INVALID")
    return IncidentDrillPlan(
        environment=str(payload.get("environment") or "").casefold(),
        environment_id=str(payload.get("environment_id") or "").casefold(),
        compose_file=_absolute(payload, "compose_file"),
        approved_tool_root=tool_root,
        services=services,
        integrity_command=tuple(raw_command),
        expected_integrity=expected,
        timeout_seconds=timeout,
    )


def assert_incident_authorized(plan: IncidentDrillPlan, *, allow_disruption: bool) -> None:
    if not allow_disruption:
        raise IncidentDrillError("ALLOW_DISRUPTION_FLAG_REQUIRED")
    if plan.environment != "staging" or not _ENVIRONMENT_ID.fullmatch(plan.environment_id):
        raise IncidentDrillError("STAGING_ENVIRONMENT_REQUIRED")
    if str(os.getenv("MENTORFLOW_STAGING_INCIDENT_DRILLS") or "").casefold() not in {
        "1",
        "true",
        "yes",
    }:
        raise IncidentDrillError("INCIDENT_DRILL_ENV_UNLOCK_REQUIRED")
    if not plan.compose_file.is_file():
        raise IncidentDrillError("COMPOSE_FILE_MISSING")
    if not plan.approved_tool_root.is_dir() or not Path(plan.integrity_command[0]).is_file():
        raise IncidentDrillError("APPROVED_INTEGRITY_COMMAND_MISSING")


def _is_up(status_code: int) -> bool:
    return 200 <= status_code < 400


def _wait_for_state(
    http: HttpClient,
    clock: Clock,
    url: str,
    *,
    expected_up: bool,
    timeout_seconds: int,
) -> tuple[bool, int]:
    started = clock.monotonic()
    deadline = started + timeout_seconds
    while clock.monotonic() <= deadline:
        response = http.request("GET", url, timeout_seconds=2.0)
        if _is_up(response.status_code) is expected_up:
            return True, max(0, int((clock.monotonic() - started) * 1000))
        clock.sleep(1.0)
    return False, max(0, int((clock.monotonic() - started) * 1000))


def _integrity_check(
    plan: IncidentDrillPlan,
    bundle: EnterpriseStagingEvidenceBundle,
    runner: CommandRunner,
) -> tuple[bool, dict[str, object]]:
    execution = record_command(bundle, runner, plan.integrity_command, timeout_seconds=300)
    if execution.exit_code != 0:
        return False, {"error_code": "INTEGRITY_COMMAND_FAILED"}
    try:
        actual = parse_integrity_snapshot(execution.stdout)
        comparison = compare_integrity(plan.expected_integrity, actual)
    except RestoreDrillError as exc:
        return False, {"error_code": exc.code}
    return comparison.matched, comparison.to_dict()


def _service_drill(
    scenario: str,
    service: ServiceDrill,
    plan: IncidentDrillPlan,
    bundle: EnterpriseStagingEvidenceBundle,
    *,
    runner: CommandRunner,
    http: HttpClient,
    clock: Clock,
) -> CheckResult:
    started_at, started = utc_now(), time.perf_counter()
    baseline = http.request("GET", service.probe_url, timeout_seconds=2.0)
    if not _is_up(baseline.status_code):
        return CheckResult(
            check_id=f"incident.{scenario}",
            category="incident",
            status=VerificationStatus.BLOCKED,
            started_at=started_at,
            finished_at=utc_now(),
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            summary="incident drill baseline is not healthy",
            details={"scenario": scenario, "baseline_status_code": baseline.status_code},
        )
    prefix = ("docker", "compose", "-f", str(plan.compose_file))
    stop = record_command(bundle, runner, (*prefix, "stop", service.service), timeout_seconds=120)
    detection_ok = False
    detection_ms = 0
    start_exit_code: int | None = None
    recovery_ok = False
    recovery_ms = 0
    try:
        if stop.exit_code == 0:
            detection_ok, detection_ms = _wait_for_state(
                http,
                clock,
                service.probe_url,
                expected_up=False,
                timeout_seconds=plan.timeout_seconds,
            )
    finally:
        start = record_command(bundle, runner, (*prefix, "start", service.service), timeout_seconds=120)
        start_exit_code = start.exit_code
        if start.exit_code == 0:
            recovery_ok, recovery_ms = _wait_for_state(
                http,
                clock,
                service.probe_url,
                expected_up=True,
                timeout_seconds=plan.timeout_seconds,
            )
    integrity_ok, integrity = _integrity_check(plan, bundle, runner)
    passed = (
        stop.exit_code == 0
        and detection_ok
        and start_exit_code == 0
        and recovery_ok
        and integrity_ok
    )
    return CheckResult(
        check_id=f"incident.{scenario}",
        category="incident",
        status=VerificationStatus.PASS if passed else VerificationStatus.FAIL,
        started_at=started_at,
        finished_at=utc_now(),
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        summary="incident detected, recovered, and preserved data" if passed else "incident drill failed closed",
        details={
            "scenario": scenario,
            "detection_ms": detection_ms,
            "recovery_ms": recovery_ms,
            "stop_exit_code": stop.exit_code,
            "start_exit_code": start_exit_code,
            "detection_passed": detection_ok,
            "recovery_passed": recovery_ok,
            "data_integrity_passed": integrity_ok,
            "integrity": integrity,
        },
    )


def _provider_drill(
    settings: VerificationSettings,
    plan: IncidentDrillPlan,
    bundle: EnterpriseStagingEvidenceBundle,
    *,
    runner: CommandRunner,
    http: HttpClient,
) -> CheckResult:
    started_at, started = utc_now(), time.perf_counter()
    if not settings.staging_verify_token:
        return CheckResult(
            check_id="incident.provider_fake",
            category="incident",
            status=VerificationStatus.BLOCKED,
            started_at=started_at,
            finished_at=utc_now(),
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            summary="fake Provider drill token is not configured",
        )
    headers = {"Authorization": f"Bearer {settings.staging_verify_token}"}
    detection_started = time.perf_counter()
    outage = http.request(
        "POST",
        f"{settings.base_url}/internal/staging-verification/probe",
        headers=headers,
        json_body={"simulate_provider_outage": True},
        timeout_seconds=10.0,
    )
    detection_ms = max(0, int((time.perf_counter() - detection_started) * 1000))
    recovery_started = time.perf_counter()
    recovery = http.request(
        "POST",
        f"{settings.base_url}/internal/staging-verification/probe",
        headers=headers,
        json_body={"simulate_provider_outage": False},
        timeout_seconds=10.0,
    )
    recovery_ms = max(0, int((time.perf_counter() - recovery_started) * 1000))
    try:
        outage_body = json.loads(outage.body)
        recovery_body = json.loads(recovery.body)
    except (TypeError, ValueError):
        outage_body, recovery_body = {}, {}
    integrity_ok, integrity = _integrity_check(plan, bundle, runner)
    passed = (
        outage.status_code == 200
        and isinstance(outage_body, Mapping)
        and outage_body.get("provider_outage_simulated") is True
        and recovery.status_code == 200
        and isinstance(recovery_body, Mapping)
        and recovery_body.get("provider_outage_simulated") is False
        and integrity_ok
    )
    return CheckResult(
        check_id="incident.provider_fake",
        category="incident",
        status=VerificationStatus.PASS if passed else VerificationStatus.FAIL,
        started_at=started_at,
        finished_at=utc_now(),
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        summary="fake Provider outage detected and recovered" if passed else "fake Provider outage drill failed",
        details={
            "outage_status_code": outage.status_code,
            "recovery_status_code": recovery.status_code,
            "detection_ms": detection_ms,
            "recovery_ms": recovery_ms,
            "outage_observed": isinstance(outage_body, Mapping) and outage_body.get("provider_outage_simulated") is True,
            "recovery_observed": isinstance(recovery_body, Mapping) and recovery_body.get("provider_outage_simulated") is False,
            "data_integrity_passed": integrity_ok,
            "integrity": integrity,
        },
    )


def run_incident_drills(
    plan: IncidentDrillPlan,
    settings: VerificationSettings,
    bundle: EnterpriseStagingEvidenceBundle,
    *,
    runner: CommandRunner,
    http: HttpClient,
    clock: Clock,
    allow_disruption: bool,
) -> list[CheckResult]:
    try:
        assert_incident_authorized(plan, allow_disruption=allow_disruption)
    except IncidentDrillError as exc:
        return [
            CheckResult(
                check_id="incident.authorization_guard",
                category="incident",
                status=VerificationStatus.BLOCKED,
                started_at=utc_now(),
                finished_at=utc_now(),
                duration_ms=0,
                summary="incident drills refused before disruption",
                details={"error_code": exc.code},
            )
        ]
    results = [
        CheckResult(
            check_id="incident.authorization_guard",
            category="incident",
            status=VerificationStatus.PASS,
            started_at=utc_now(),
            finished_at=utc_now(),
            duration_ms=0,
            summary="staging incident authorization guard passed",
            details={"environment_id": plan.environment_id},
        )
    ]
    for scenario in _SCENARIOS:
        results.append(
            _service_drill(
                scenario,
                plan.services[scenario],
                plan,
                bundle,
                runner=runner,
                http=http,
                clock=clock,
            )
        )
    results.append(_provider_drill(settings, plan, bundle, runner=runner, http=http))
    return results


__all__ = [
    "INCIDENT_PLAN_SCHEMA",
    "IncidentDrillError",
    "IncidentDrillPlan",
    "ServiceDrill",
    "assert_incident_authorized",
    "load_incident_plan",
    "run_incident_drills",
]
