"""Linux-staging CLI for the external PostgreSQL/object recovery contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from .recovery import (
    BackupPlan,
    CommandRunner,
    PostgresEndpoint,
    RestorePlan,
    create_backup,
    load_and_verify_manifest,
    restore_backup,
)


PLAN_SCHEMA = "mentorflow.enterprise-recovery-plan.v1"
_FORBIDDEN_PLAN_KEY = re.compile(
    r"(?:password(?!_env)|authorization|api.?key|secret|credential|cookie|database.?url|dsn)",
    re.IGNORECASE,
)


def _reject_sensitive_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _FORBIDDEN_PLAN_KEY.search(str(key)):
                raise ValueError("recovery plans must contain references, not sensitive values")
            _reject_sensitive_fields(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_sensitive_fields(item)


def _read_plan(path: Path) -> Mapping[str, object]:
    if not path.is_absolute():
        raise ValueError("recovery plan path must be absolute")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != PLAN_SCHEMA:
        raise ValueError("recovery plan schema is invalid")
    _reject_sensitive_fields(payload)
    return payload


def _absolute(payload: Mapping[str, object], name: str) -> Path:
    path = Path(str(payload.get(name) or ""))
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return path


def _endpoint(payload: object, component: str) -> PostgresEndpoint:
    if not isinstance(payload, dict):
        raise ValueError(f"{component} endpoint is missing")
    allowed = {"host", "port", "database", "username", "password_env"}
    if set(payload) != allowed:
        raise ValueError(f"{component} endpoint fields are invalid")
    return PostgresEndpoint(
        component=component,
        host=str(payload["host"]),
        port=int(payload["port"]),
        database=str(payload["database"]),
        username=str(payload["username"]),
        password_env=str(payload["password_env"]),
    )


def _backup_plan(payload: Mapping[str, object]) -> BackupPlan:
    return BackupPlan(
        backup_id=str(payload.get("backup_id") or ""),
        backup_root=_absolute(payload, "backup_root"),
        postgres=_endpoint(payload.get("postgres"), "postgres"),
        keycloak_database=_endpoint(payload.get("keycloak_database"), "keycloak-db"),
        object_source_root=_absolute(payload, "object_source_root"),
        keycloak_realm_export=_absolute(payload, "keycloak_realm_export"),
        pg_dump_binary=str(payload.get("pg_dump_binary") or "pg_dump"),
    )


def load_backup_plan(path: Path) -> BackupPlan:
    """Load the existing strict recovery plan as a backup operation."""

    return _backup_plan(_read_plan(path))


def _restore_plan(payload: Mapping[str, object]) -> RestorePlan:
    return RestorePlan(
        backup_directory=_absolute(payload, "backup_directory"),
        postgres=_endpoint(payload.get("postgres"), "postgres"),
        keycloak_database=_endpoint(payload.get("keycloak_database"), "keycloak-db"),
        object_restore_root=_absolute(payload, "object_restore_root"),
        keycloak_realm_restore=_absolute(payload, "keycloak_realm_restore"),
        pg_restore_binary=str(payload.get("pg_restore_binary") or "pg_restore"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MentorFlow enterprise backup and restore")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backup", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--plan", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--backup-directory", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None, *, runner: CommandRunner | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify":
        manifest = load_and_verify_manifest(args.backup_directory)
        result = {
            "schema_version": manifest["schema_version"],
            "backup_id": manifest["backup_id"],
            "status": "VERIFIED",
            "verified_artifacts": len(manifest["artifacts"]),
        }
    else:
        payload = _read_plan(args.plan)
        if args.command == "backup":
            destination = create_backup(_backup_plan(payload), runner)
            manifest = load_and_verify_manifest(destination)
            result = {
                "schema_version": manifest["schema_version"],
                "backup_id": manifest["backup_id"],
                "status": "BACKED_UP",
                "verified_artifacts": len(manifest["artifacts"]),
            }
        else:
            result = restore_backup(_restore_plan(payload), runner)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PLAN_SCHEMA", "load_backup_plan", "main"]
