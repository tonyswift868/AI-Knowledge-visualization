"""Disposable-only backup, restore, and integrity comparison drills."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ..recovery import (
    BackupPlan,
    PostgresEndpoint,
    RecoveryOperationError,
    RestorePlan,
    create_backup,
    load_and_verify_manifest,
    restore_backup,
)
from .adapters import CommandExecution, CommandRunner, record_command
from .models import (
    CheckResult,
    EnterpriseStagingEvidenceBundle,
    VerificationStatus,
    sanitize_evidence,
    utc_now,
)


RESTORE_DRILL_SCHEMA = "mentorflow.enterprise-restore-drill.v1"
DISPOSABLE_MARKER_SCHEMA = "mentorflow.disposable-restore-marker.v1"
_ENVIRONMENT_ID = re.compile(r"disposable-[a-z0-9][a-z0-9-]{2,63}$")
_FORBIDDEN_PLAN_KEY = re.compile(
    r"(?:password(?!_env)|authorization|api.?key|secret|credential|cookie|database.?url|dsn)",
    re.IGNORECASE,
)
_REQUIRED_SNAPSHOT_FIELDS = (
    "organizations",
    "users",
    "memberships",
    "workspaces",
    "documents",
    "knowledge_refs",
    "plans",
    "student_state",
    "audit",
    "jobs",
    "object_hashes",
)


class RestoreDrillError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RestoreDrillPlan:
    environment: str
    environment_id: str
    disposable_environment: bool
    disposable_root: Path
    marker_file: Path
    approved_tool_root: Path
    seed_command: tuple[str, ...]
    destroy_command: tuple[str, ...]
    compare_command: tuple[str, ...]
    backup: BackupPlan
    restore: RestorePlan


@dataclass(frozen=True, slots=True)
class ComparisonReceipt:
    matched: bool
    mismatches: tuple[str, ...]
    expected_counts: Mapping[str, int]
    actual_counts: Mapping[str, int]
    object_hash_count: int

    def to_dict(self) -> dict[str, object]:
        return sanitize_evidence(
            {
                "matched": self.matched,
                "mismatches": self.mismatches,
                "expected_counts": self.expected_counts,
                "actual_counts": self.actual_counts,
                "object_hash_count": self.object_hash_count,
            }
        )  # type: ignore[return-value]


def _assert_safe_plan(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _FORBIDDEN_PLAN_KEY.search(str(key)):
                raise RestoreDrillError("PLAN_CONTAINS_SENSITIVE_FIELD")
            _assert_safe_plan(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _assert_safe_plan(item)
    elif isinstance(value, str):
        sanitize_evidence(value)


def _absolute(payload: Mapping[str, object], name: str) -> Path:
    path = Path(str(payload.get(name) or ""))
    if not path.is_absolute():
        raise RestoreDrillError(f"{name.upper()}_NOT_ABSOLUTE")
    return path.resolve()


def _endpoint(payload: object, component: str) -> PostgresEndpoint:
    if not isinstance(payload, Mapping):
        raise RestoreDrillError(f"{component.upper()}_ENDPOINT_MISSING")
    allowed = {"host", "port", "database", "username", "password_env"}
    if set(payload) != allowed:
        raise RestoreDrillError(f"{component.upper()}_ENDPOINT_FIELDS_INVALID")
    try:
        return PostgresEndpoint(
            component=component,
            host=str(payload["host"]),
            port=int(payload["port"]),
            database=str(payload["database"]),
            username=str(payload["username"]),
            password_env=str(payload["password_env"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RestoreDrillError(f"{component.upper()}_ENDPOINT_INVALID") from exc


def _command(payload: Mapping[str, object], name: str) -> tuple[str, ...]:
    raw = payload.get(name)
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) and item for item in raw):
        raise RestoreDrillError(f"{name.upper()}_INVALID")
    return tuple(raw)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def load_restore_drill_plan(path: Path) -> RestoreDrillPlan:
    if not path.is_absolute():
        raise RestoreDrillError("PLAN_PATH_NOT_ABSOLUTE")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise RestoreDrillError("PLAN_INVALID") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != RESTORE_DRILL_SCHEMA:
        raise RestoreDrillError("PLAN_SCHEMA_INVALID")
    _assert_safe_plan(payload)
    backup_payload = payload.get("backup")
    restore_payload = payload.get("restore")
    if not isinstance(backup_payload, Mapping) or not isinstance(restore_payload, Mapping):
        raise RestoreDrillError("RECOVERY_PLAN_MISSING")
    backup = BackupPlan(
        backup_id=str(backup_payload.get("backup_id") or ""),
        backup_root=_absolute(backup_payload, "backup_root"),
        postgres=_endpoint(backup_payload.get("postgres"), "postgres"),
        keycloak_database=_endpoint(backup_payload.get("keycloak_database"), "keycloak-db"),
        object_source_root=_absolute(backup_payload, "object_source_root"),
        keycloak_realm_export=_absolute(backup_payload, "keycloak_realm_export"),
        pg_dump_binary=str(backup_payload.get("pg_dump_binary") or "pg_dump"),
    )
    restore = RestorePlan(
        backup_directory=_absolute(restore_payload, "backup_directory"),
        postgres=_endpoint(restore_payload.get("postgres"), "postgres"),
        keycloak_database=_endpoint(restore_payload.get("keycloak_database"), "keycloak-db"),
        object_restore_root=_absolute(restore_payload, "object_restore_root"),
        keycloak_realm_restore=_absolute(restore_payload, "keycloak_realm_restore"),
        pg_restore_binary=str(restore_payload.get("pg_restore_binary") or "pg_restore"),
    )
    return RestoreDrillPlan(
        environment=str(payload.get("environment") or "").casefold(),
        environment_id=str(payload.get("environment_id") or "").casefold(),
        disposable_environment=payload.get("disposable_environment") is True,
        disposable_root=_absolute(payload, "disposable_root"),
        marker_file=_absolute(payload, "marker_file"),
        approved_tool_root=_absolute(payload, "approved_tool_root"),
        seed_command=_command(payload, "seed_command"),
        destroy_command=_command(payload, "destroy_command"),
        compare_command=_command(payload, "compare_command"),
        backup=backup,
        restore=restore,
    )


def assert_disposable_restore(plan: RestoreDrillPlan, *, disposable_only: bool) -> None:
    """Reject before any process or destructive filesystem operation."""

    if not disposable_only:
        raise RestoreDrillError("DISPOSABLE_ONLY_FLAG_REQUIRED")
    if plan.environment != "staging":
        raise RestoreDrillError("STAGING_ENVIRONMENT_REQUIRED")
    if not plan.disposable_environment or not _ENVIRONMENT_ID.fullmatch(plan.environment_id):
        raise RestoreDrillError("DISPOSABLE_ENVIRONMENT_REQUIRED")
    if not plan.disposable_root.is_dir():
        raise RestoreDrillError("DISPOSABLE_ROOT_MISSING")
    if not plan.approved_tool_root.is_dir():
        raise RestoreDrillError("APPROVED_TOOL_ROOT_MISSING")
    if not _is_within(plan.marker_file, plan.disposable_root):
        raise RestoreDrillError("MARKER_OUTSIDE_DISPOSABLE_ROOT")
    try:
        marker = json.loads(plan.marker_file.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise RestoreDrillError("DISPOSABLE_MARKER_INVALID") from exc
    if not isinstance(marker, Mapping) or (
        marker.get("schema_version") != DISPOSABLE_MARKER_SCHEMA
        or marker.get("environment_id") != plan.environment_id
        or marker.get("allow_destructive_restore") is not True
    ):
        raise RestoreDrillError("DISPOSABLE_MARKER_INVALID")
    for endpoint in (plan.restore.postgres, plan.restore.keycloak_database):
        expected = "mentorflow_disposable_" if endpoint.component == "postgres" else "keycloak_disposable_"
        if not endpoint.database.startswith(expected):
            raise RestoreDrillError("RESTORE_DATABASE_NOT_DISPOSABLE")
    for target in (
        plan.backup.object_source_root,
        plan.backup.keycloak_realm_export,
        plan.restore.object_restore_root,
        plan.restore.keycloak_realm_restore,
    ):
        if not _is_within(target, plan.disposable_root):
            raise RestoreDrillError("RESTORE_TARGET_OUTSIDE_DISPOSABLE_ROOT")
    expected_backup = (plan.backup.backup_root / plan.backup.backup_id).resolve()
    if plan.restore.backup_directory != expected_backup:
        raise RestoreDrillError("BACKUP_RESTORE_PATH_MISMATCH")
    for command in (plan.seed_command, plan.destroy_command, plan.compare_command):
        executable = Path(command[0])
        if not executable.is_absolute() or not _is_within(executable.resolve(), plan.approved_tool_root):
            raise RestoreDrillError("COMMAND_OUTSIDE_APPROVED_TOOL_ROOT")
        if not executable.is_file():
            raise RestoreDrillError("APPROVED_COMMAND_MISSING")
        _assert_safe_plan(command)


def parse_integrity_snapshot(text: str) -> dict[str, object]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise RestoreDrillError("INTEGRITY_SNAPSHOT_INVALID") from exc
    if not isinstance(payload, Mapping) or set(payload) != set(_REQUIRED_SNAPSHOT_FIELDS):
        raise RestoreDrillError("INTEGRITY_SNAPSHOT_FIELDS_INVALID")
    counts: dict[str, object] = {}
    for field in _REQUIRED_SNAPSHOT_FIELDS[:-1]:
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RestoreDrillError("INTEGRITY_SNAPSHOT_COUNT_INVALID")
        counts[field] = value
    hashes = payload["object_hashes"]
    if not isinstance(hashes, Mapping):
        raise RestoreDrillError("INTEGRITY_OBJECT_HASHES_INVALID")
    normalized_hashes: dict[str, str] = {}
    for raw_path, raw_hash in hashes.items():
        object_path, digest = str(raw_path), str(raw_hash).casefold()
        relative = Path(object_path)
        if (
            not object_path
            or relative.is_absolute()
            or ".." in relative.parts
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise RestoreDrillError("INTEGRITY_OBJECT_HASH_INVALID")
        normalized_hashes[relative.as_posix()] = digest
    counts["object_hashes"] = dict(sorted(normalized_hashes.items()))
    sanitize_evidence(counts)
    return counts


def compare_integrity(expected: Mapping[str, object], actual: Mapping[str, object]) -> ComparisonReceipt:
    expected_parsed = parse_integrity_snapshot(json.dumps(dict(expected), separators=(",", ":")))
    actual_parsed = parse_integrity_snapshot(json.dumps(dict(actual), separators=(",", ":")))
    mismatches = tuple(
        field for field in _REQUIRED_SNAPSHOT_FIELDS if expected_parsed[field] != actual_parsed[field]
    )
    count_fields = _REQUIRED_SNAPSHOT_FIELDS[:-1]
    return ComparisonReceipt(
        matched=not mismatches,
        mismatches=mismatches,
        expected_counts={field: int(expected_parsed[field]) for field in count_fields},
        actual_counts={field: int(actual_parsed[field]) for field in count_fields},
        object_hash_count=len(expected_parsed["object_hashes"]),  # type: ignore[arg-type]
    )


class _RecoveryRunner:
    def __init__(self, runner: CommandRunner, bundle: EnterpriseStagingEvidenceBundle) -> None:
        self.runner = runner
        self.bundle = bundle

    def run(self, argv: Sequence[str], *, env: Mapping[str, str]) -> int:
        execution = record_command(
            self.bundle,
            self.runner,
            argv,
            env=env,
            timeout_seconds=900,
        )
        return execution.exit_code


def _run_snapshot_command(
    bundle: EnterpriseStagingEvidenceBundle,
    runner: CommandRunner,
    command: Sequence[str],
) -> tuple[CommandExecution, dict[str, object]]:
    execution = record_command(bundle, runner, command, timeout_seconds=300)
    if execution.exit_code != 0:
        raise RestoreDrillError("INTEGRITY_COMMAND_FAILED")
    return execution, parse_integrity_snapshot(execution.stdout)


def run_backup_automation(
    plan: BackupPlan,
    bundle: EnterpriseStagingEvidenceBundle,
    *,
    runner: CommandRunner,
) -> CheckResult:
    started_at, started = utc_now(), time.perf_counter()
    try:
        destination = create_backup(plan, _RecoveryRunner(runner, bundle))
        manifest = load_and_verify_manifest(destination)
        details = {
            "backup_id": manifest["backup_id"],
            "verified_artifacts": len(manifest["artifacts"]),
            "components": manifest["components"],
            "manifest_schema": manifest["schema_version"],
        }
        status, summary = VerificationStatus.PASS, "backup and hash manifest verified"
    except (RecoveryOperationError, OSError, ValueError) as exc:
        details = {"error_code": getattr(exc, "code", type(exc).__name__)}
        status, summary = VerificationStatus.FAIL, "backup automation failed closed"
    return CheckResult(
        check_id="backup.automation",
        category="backup",
        status=status,
        started_at=started_at,
        finished_at=utc_now(),
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        summary=summary,
        details=details,
    )


def run_restore_drill(
    plan: RestoreDrillPlan,
    bundle: EnterpriseStagingEvidenceBundle,
    *,
    runner: CommandRunner,
    disposable_only: bool,
) -> list[CheckResult]:
    started_at, started = utc_now(), time.perf_counter()
    results: list[CheckResult] = []
    try:
        assert_disposable_restore(plan, disposable_only=disposable_only)
    except RestoreDrillError as exc:
        return [
            CheckResult(
                check_id="restore.disposable_guard",
                category="restore",
                status=VerificationStatus.BLOCKED,
                started_at=started_at,
                finished_at=utc_now(),
                duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
                summary="destructive restore refused before execution",
                details={"error_code": exc.code},
            )
        ]
    results.append(
        CheckResult(
            check_id="restore.disposable_guard",
            category="restore",
            status=VerificationStatus.PASS,
            started_at=started_at,
            finished_at=utc_now(),
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            summary="disposable restore guard passed",
            details={"environment_id": plan.environment_id},
        )
    )
    workflow_started_at, workflow_started = utc_now(), time.perf_counter()
    try:
        _, expected = _run_snapshot_command(bundle, runner, plan.seed_command)
        destination = create_backup(plan.backup, _RecoveryRunner(runner, bundle))
        manifest = load_and_verify_manifest(destination)
        destroyed = record_command(bundle, runner, plan.destroy_command, timeout_seconds=300)
        if destroyed.exit_code != 0:
            raise RestoreDrillError("DESTROY_COMMAND_FAILED")
        restore_receipt = restore_backup(plan.restore, _RecoveryRunner(runner, bundle))
        _, actual = _run_snapshot_command(bundle, runner, plan.compare_command)
        comparison = compare_integrity(expected, actual)
        if not comparison.matched:
            raise RestoreDrillError("RESTORE_COMPARISON_MISMATCH")
        status, summary = VerificationStatus.PASS, "seed, backup, destroy, restore, and compare passed"
        details = {
            **comparison.to_dict(),
            "backup_id": manifest["backup_id"],
            "verified_artifacts": len(manifest["artifacts"]),
            "restore_status": restore_receipt["status"],
        }
    except (RestoreDrillError, RecoveryOperationError, OSError, ValueError) as exc:
        status, summary = VerificationStatus.FAIL, "disposable restore drill failed closed"
        details = {"error_code": getattr(exc, "code", type(exc).__name__)}
    results.append(
        CheckResult(
            check_id="restore.workflow",
            category="restore",
            status=status,
            started_at=workflow_started_at,
            finished_at=utc_now(),
            duration_ms=max(0, int((time.perf_counter() - workflow_started) * 1000)),
            summary=summary,
            details=details,
        )
    )
    return results


__all__ = [
    "DISPOSABLE_MARKER_SCHEMA",
    "RESTORE_DRILL_SCHEMA",
    "ComparisonReceipt",
    "RestoreDrillError",
    "RestoreDrillPlan",
    "assert_disposable_restore",
    "compare_integrity",
    "load_restore_drill_plan",
    "parse_integrity_snapshot",
    "run_backup_automation",
    "run_restore_drill",
]
