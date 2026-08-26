"""External-tool backup/restore seam with atomic manifests and hash gates."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .instrumentation import record_backup_freshness


MANIFEST_SCHEMA = "mentorflow.enterprise-backup.v1"


class RecoveryOperationError(RuntimeError):
    def __init__(self, code: str, component: str) -> None:
        super().__init__(f"{code}:{component}")
        self.code = code
        self.component = component


@dataclass(frozen=True, slots=True)
class PostgresEndpoint:
    component: str
    host: str
    port: int
    database: str
    username: str
    password_env: str

    def __post_init__(self) -> None:
        if self.component not in {"postgres", "keycloak-db"}:
            raise ValueError("unsupported PostgreSQL backup component")
        if not (1 <= int(self.port) <= 65535):
            raise ValueError("PostgreSQL port is invalid")
        for value in (self.host, self.database, self.username, self.password_env):
            if not str(value).strip() or len(str(value)) > 160:
                raise ValueError("PostgreSQL endpoint fields must be bounded")


@dataclass(frozen=True, slots=True)
class BackupPlan:
    backup_id: str
    backup_root: Path
    postgres: PostgresEndpoint
    keycloak_database: PostgresEndpoint
    object_source_root: Path
    keycloak_realm_export: Path
    pg_dump_binary: str = "pg_dump"


@dataclass(frozen=True, slots=True)
class RestorePlan:
    backup_directory: Path
    postgres: PostgresEndpoint
    keycloak_database: PostgresEndpoint
    object_restore_root: Path
    keycloak_realm_restore: Path
    pg_restore_binary: str = "pg_restore"


@dataclass(frozen=True, slots=True)
class ArtifactReceipt:
    component: str
    relative_path: str
    size_bytes: int
    sha256: str


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, env: Mapping[str, str]) -> int: ...


class SubprocessCommandRunner:
    """Run external PostgreSQL tools without capturing secret-bearing output."""

    def __init__(self, timeout_seconds: int = 900) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, argv: Sequence[str], *, env: Mapping[str, str]) -> int:
        completed = subprocess.run(
            list(argv),
            check=False,
            env={**os.environ, **dict(env)},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=self.timeout_seconds,
        )
        return int(completed.returncode)


def _safe_root(path: Path, label: str, *, must_exist: bool = False) -> Path:
    if not path.is_absolute():
        raise RecoveryOperationError("PATH_NOT_ABSOLUTE", label)
    resolved = path.resolve()
    if must_exist and not resolved.exists():
        raise RecoveryOperationError("SOURCE_NOT_FOUND", label)
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_receipts(root: Path) -> tuple[ArtifactReceipt, ...]:
    receipts: list[ArtifactReceipt] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "manifest.json":
            continue
        relative = path.relative_to(root).as_posix()
        component = relative.split("/", 1)[0]
        receipts.append(ArtifactReceipt(component, relative, path.stat().st_size, _sha256(path)))
    return tuple(receipts)


def _password_environment(endpoint: PostgresEndpoint) -> dict[str, str]:
    password = os.getenv(endpoint.password_env)
    if password is None:
        raise RecoveryOperationError("POSTGRES_PASSWORD_ENV_MISSING", endpoint.component)
    return {"PGPASSWORD": password}


def _pg_dump(endpoint: PostgresEndpoint, destination: Path, binary: str, runner: CommandRunner) -> None:
    argv = (
        binary,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host", endpoint.host,
        "--port", str(endpoint.port),
        "--username", endpoint.username,
        "--file", str(destination),
        endpoint.database,
    )
    if runner.run(argv, env=_password_environment(endpoint)) != 0:
        raise RecoveryOperationError("PG_DUMP_FAILED", endpoint.component)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RecoveryOperationError("PG_DUMP_EMPTY", endpoint.component)


def _copy_tree(source: Path, destination: Path, component: str) -> None:
    for path in source.rglob("*"):
        if path.is_symlink():
            raise RecoveryOperationError("SYMLINK_NOT_ALLOWED", component)
    shutil.copytree(source, destination)


def create_backup(plan: BackupPlan, runner: CommandRunner | None = None) -> Path:
    """Create a same-filesystem atomic backup directory and immutable manifest."""

    runner = runner or SubprocessCommandRunner()
    backup_root = _safe_root(plan.backup_root, "backup-root")
    object_root = _safe_root(plan.object_source_root, "objects", must_exist=True)
    realm_export = _safe_root(plan.keycloak_realm_export, "keycloak-realm", must_exist=True)
    if (
        object_root == backup_root
        or object_root in backup_root.parents
        or backup_root in object_root.parents
    ):
        raise RecoveryOperationError("BACKUP_SOURCE_OVERLAPS_DESTINATION", "objects")
    if not realm_export.is_file():
        raise RecoveryOperationError("SOURCE_NOT_FILE", "keycloak-realm")
    backup_root.mkdir(parents=True, exist_ok=True)
    destination = (backup_root / plan.backup_id).resolve()
    temporary = (backup_root / f".{plan.backup_id}.tmp").resolve()
    if destination.exists() or temporary.exists() or destination.parent != backup_root:
        raise RecoveryOperationError("BACKUP_DESTINATION_EXISTS", "backup-root")
    temporary.mkdir()
    (temporary / "postgres").mkdir()
    (temporary / "keycloak-db").mkdir()
    _pg_dump(plan.postgres, temporary / "postgres" / "database.dump", plan.pg_dump_binary, runner)
    _pg_dump(plan.keycloak_database, temporary / "keycloak-db" / "database.dump", plan.pg_dump_binary, runner)
    _copy_tree(object_root, temporary / "objects", "objects")
    shutil.copy2(realm_export, temporary / "keycloak-realm.json")
    artifacts = _artifact_receipts(temporary)
    payload = {
        "schema_version": MANIFEST_SCHEMA,
        "backup_id": plan.backup_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "components": ["postgres", "keycloak-db", "objects", "keycloak-realm.json"],
        "artifacts": [asdict(item) for item in artifacts],
    }
    manifest = temporary / "manifest.json"
    with manifest.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.rename(destination)
    for component in ("postgres", "objects", "keycloak"):
        record_backup_freshness(component, 0.0)
    return destination


def load_and_verify_manifest(backup_directory: Path) -> dict[str, object]:
    root = _safe_root(backup_directory, "backup-directory", must_exist=True)
    manifest_path = root / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RecoveryOperationError("MANIFEST_INVALID", "manifest") from exc
    if payload.get("schema_version") != MANIFEST_SCHEMA:
        raise RecoveryOperationError("MANIFEST_SCHEMA_INVALID", "manifest")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RecoveryOperationError("MANIFEST_ARTIFACTS_INVALID", "manifest")
    for item in artifacts:
        if not isinstance(item, dict):
            raise RecoveryOperationError("MANIFEST_ARTIFACT_INVALID", "manifest")
        relative = Path(str(item.get("relative_path") or ""))
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            raise RecoveryOperationError("ARTIFACT_MISSING", str(item.get("component") or "unknown"))
        if target.stat().st_size != int(item.get("size_bytes", -1)) or _sha256(target) != item.get("sha256"):
            raise RecoveryOperationError("ARTIFACT_HASH_MISMATCH", str(item.get("component") or "unknown"))
    return payload


def _pg_restore(endpoint: PostgresEndpoint, source: Path, binary: str, runner: CommandRunner) -> None:
    argv = (
        binary,
        "--clean",
        "--if-exists",
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
        "--host", endpoint.host,
        "--port", str(endpoint.port),
        "--username", endpoint.username,
        "--dbname", endpoint.database,
        str(source),
    )
    if runner.run(argv, env=_password_environment(endpoint)) != 0:
        raise RecoveryOperationError("PG_RESTORE_FAILED", endpoint.component)


def restore_backup(plan: RestorePlan, runner: CommandRunner | None = None) -> dict[str, object]:
    """Verify every artifact before restoring into empty disposable targets."""

    runner = runner or SubprocessCommandRunner()
    root = _safe_root(plan.backup_directory, "backup-directory", must_exist=True)
    payload = load_and_verify_manifest(root)
    object_target = _safe_root(plan.object_restore_root, "object-restore-root")
    realm_target = _safe_root(plan.keycloak_realm_restore, "keycloak-realm-restore")
    if object_target.exists() or realm_target.exists():
        raise RecoveryOperationError("RESTORE_TARGET_NOT_EMPTY", "restore-target")
    if object_target == realm_target or object_target in realm_target.parents or realm_target in object_target.parents:
        raise RecoveryOperationError("RESTORE_TARGETS_OVERLAP", "restore-target")
    object_target.parent.mkdir(parents=True, exist_ok=True)
    realm_target.parent.mkdir(parents=True, exist_ok=True)
    object_temporary = object_target.with_name(f".{object_target.name}.tmp")
    realm_temporary = realm_target.with_name(f".{realm_target.name}.tmp")
    if object_temporary.exists() or realm_temporary.exists():
        raise RecoveryOperationError("RESTORE_TEMPORARY_EXISTS", "restore-target")
    _pg_restore(plan.postgres, root / "postgres" / "database.dump", plan.pg_restore_binary, runner)
    _pg_restore(plan.keycloak_database, root / "keycloak-db" / "database.dump", plan.pg_restore_binary, runner)
    _copy_tree(root / "objects", object_temporary, "objects")
    shutil.copy2(root / "keycloak-realm.json", realm_temporary)
    object_temporary.rename(object_target)
    realm_temporary.rename(realm_target)
    return {
        "schema_version": "mentorflow.enterprise-restore-receipt.v1",
        "backup_id": payload["backup_id"],
        "status": "RESTORED",
        "verified_artifacts": len(payload["artifacts"]),
        "components": payload["components"],
    }


__all__ = [
    "ArtifactReceipt",
    "BackupPlan",
    "CommandRunner",
    "MANIFEST_SCHEMA",
    "PostgresEndpoint",
    "RecoveryOperationError",
    "RestorePlan",
    "SubprocessCommandRunner",
    "create_backup",
    "load_and_verify_manifest",
    "restore_backup",
]
