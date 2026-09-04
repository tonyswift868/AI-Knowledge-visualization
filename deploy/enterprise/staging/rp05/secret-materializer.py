"""Materialize Compose file secrets into consumer-isolated runtime volumes."""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


SOURCE_ROOT = Path("/run/secrets")
TARGET_ROOT = Path("/materialized")
MAX_SECRET_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class SecretTarget:
    secret_name: str
    consumer_service: str
    uid: int
    gid: int
    mode: int
    classification: str


TARGETS = (
    SecretTarget("postgres_admin_password", "postgres", 0, 0, 0o400, "private"),
    SecretTarget("postgres_admin_password", "postgres-provision", 10001, 10001, 0o400, "private"),
    SecretTarget("postgres_migrator_password", "postgres-provision", 10001, 10001, 0o400, "private"),
    SecretTarget("postgres_app_password", "postgres-provision", 10001, 10001, 0o400, "private"),
    SecretTarget("keycloak_db_password", "postgres-provision", 10001, 10001, 0o400, "private"),
    SecretTarget("keycloak_admin_password", "keycloak", 1000, 0, 0o400, "private"),
    SecretTarget("keycloak_db_password", "keycloak", 1000, 0, 0o400, "private"),
    SecretTarget("bff_client_secret", "keycloak", 1000, 0, 0o400, "private"),
    SecretTarget("worker_client_secret", "keycloak", 1000, 0, 0o400, "private"),
    SecretTarget("keycloak_admin_password", "keycloak-ready", 1000, 0, 0o400, "private"),
    SecretTarget("postgres_migrator_password", "migrate", 10001, 10001, 0o400, "private"),
    SecretTarget("postgres_app_password", "migrate", 10001, 10001, 0o400, "private"),
    SecretTarget("postgres_migrator_password", "migration-ready", 10001, 10001, 0o400, "private"),
    SecretTarget("postgres_app_password", "migration-ready", 10001, 10001, 0o400, "private"),
    SecretTarget("postgres_app_password", "backend", 10001, 10001, 0o400, "private"),
    SecretTarget("postgres_app_password", "worker", 10001, 10001, 0o400, "private"),
    SecretTarget("tls_certificate", "backend-tls-ready", 0, 0, 0o444, "public"),
    SecretTarget("tls_certificate", "oidc-loopback-proxy", 101, 101, 0o444, "public"),
    SecretTarget("tls_private_key", "oidc-loopback-proxy", 101, 101, 0o400, "private"),
    SecretTarget("tls_certificate", "edge", 101, 101, 0o444, "public"),
    SecretTarget("tls_private_key", "edge", 101, 101, 0o400, "private"),
)


class SecretMaterializationError(RuntimeError):
    """Stable failure that never includes secret content."""


def _read_source(name: str) -> bytes:
    source = SOURCE_ROOT / name
    try:
        info = source.lstat()
        if source.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise SecretMaterializationError(f"SECRET_SOURCE_INVALID:{name}")
        if info.st_size <= 0 or info.st_size > MAX_SECRET_BYTES:
            raise SecretMaterializationError(f"SECRET_SOURCE_SIZE_INVALID:{name}")
        payload = source.read_bytes()
    except SecretMaterializationError:
        raise
    except OSError as exc:
        raise SecretMaterializationError(f"SECRET_SOURCE_UNREADABLE:{name}") from exc
    if not payload:
        raise SecretMaterializationError(f"SECRET_SOURCE_EMPTY:{name}")
    return payload


def _write_target(target: SecretTarget, payload: bytes) -> dict[str, object]:
    directory = TARGET_ROOT / target.consumer_service
    if directory.is_symlink():
        raise SecretMaterializationError(
            f"SECRET_TARGET_DIRECTORY_INVALID:{target.consumer_service}"
        )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not directory.is_dir():
        raise SecretMaterializationError(
            f"SECRET_TARGET_DIRECTORY_INVALID:{target.consumer_service}"
        )
    os.chmod(directory, 0o700)
    destination = directory / target.secret_name
    temporary = directory / f".{target.secret_name}.{os.getpid()}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), target.mode)
        os.chown(temporary, target.uid, target.gid)
        os.replace(temporary, destination)
        os.chmod(directory, 0o555)
        info = destination.stat()
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise SecretMaterializationError(
            f"SECRET_TARGET_WRITE_FAILED:{target.consumer_service}:{target.secret_name}"
        ) from exc
    actual_mode = stat.S_IMODE(info.st_mode)
    if (info.st_uid, info.st_gid, actual_mode) != (target.uid, target.gid, target.mode):
        raise SecretMaterializationError(
            f"SECRET_TARGET_METADATA_INVALID:{target.consumer_service}:{target.secret_name}"
        )
    return {
        "secret_name": target.secret_name,
        "source_type": "compose_file_secret",
        "consumer_service": target.consumer_service,
        "consumer_uid": target.uid,
        "consumer_gid": target.gid,
        "effective_file_uid": info.st_uid,
        "effective_file_gid": info.st_gid,
        "effective_mode": f"{actual_mode:04o}",
        "required_access": "read",
        "private_or_public": target.classification,
    }


def materialize() -> list[dict[str, object]]:
    payloads = {name: _read_source(name) for name in {item.secret_name for item in TARGETS}}
    return [_write_target(target, payloads[target.secret_name]) for target in TARGETS]


def main() -> int:
    try:
        rows = materialize()
    except SecretMaterializationError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "mentorflow.rp05-secret-materialization.v1",
                    "status": "FAIL",
                    "code": str(exc)[:160],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "schema_version": "mentorflow.rp05-secret-materialization.v1",
                "status": "PASS",
                "secrets": rows,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
