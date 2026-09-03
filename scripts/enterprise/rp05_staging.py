#!/usr/bin/env python3
"""Fail-closed host orchestrator for an immutable RP-05 release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


SCHEMA = "mentorflow.rp05-release.v1"
ROOT = Path("/opt/mentorflow")
REQUIRED_DIRS = ("releases", "config", "secrets", "data", "backups", "evidence", "logs")
REQUIRED_SECRETS = (
    "postgres_admin_password",
    "postgres_migrator_password",
    "postgres_app_password",
    "keycloak_db_password",
    "keycloak_admin_password",
    "bff_client_secret",
    "worker_client_secret",
    "tls.crt",
    "tls.key",
)
DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/:@-]*@sha256:[0-9a-f]{64}$")
RESIDENT = {
    "postgres",
    "keycloak",
    "otel-collector",
    "backend",
    "oidc-loopback-proxy",
    "worker",
    "frontend",
    "edge",
}
HOST_BIND_UIDS = {"document-import": 10001}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_release(release: Path) -> dict:
    release = release.resolve()
    manifest_path = release / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA:
        raise RuntimeError("release manifest schema is invalid")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("release file manifest is empty")
    actual = {path.name for path in release.iterdir() if path.is_file() and path.name != "manifest.json"}
    if actual != set(files):
        raise RuntimeError("release file set does not match the manifest")
    for name, expected in files.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected)) or _sha(release / name) != expected:
            raise RuntimeError(f"release asset hash mismatch: {name}")
    images = manifest.get("image_references")
    if not isinstance(images, dict) or any(not DIGEST.fullmatch(str(item)) for item in images.values()):
        raise RuntimeError("release contains a mutable image reference")
    return manifest


def _compose(release: Path, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    command = [
        "docker", "compose", "--env-file", str(release / "runtime.env"),
        "-f", str(release / "compose.minimal.yml"), *args,
    ]
    return subprocess.run(command, check=True, text=True, capture_output=capture)


def _preflight(release: Path) -> dict:
    manifest = _load_release(release)
    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise RuntimeError("host execution requires Linux")
    if shutil.which("docker") is None:
        raise RuntimeError("Docker is unavailable")
    for name in REQUIRED_DIRS:
        path = ROOT / name
        if not path.is_dir():
            raise RuntimeError(f"required host directory is missing: {path}")
    if ROOT not in release.parents or release.parent != ROOT / "releases":
        raise RuntimeError("release must be a direct child of /opt/mentorflow/releases")
    for name in REQUIRED_SECRETS:
        path = ROOT / "secrets" / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"required secret file is missing or empty: {name}")
        if name != "tls.crt" and path.stat().st_mode & 0o077:
            raise RuntimeError(f"secret permissions are too broad: {name}")
    for name, expected_uid in HOST_BIND_UIDS.items():
        path = ROOT / "data" / name
        if not path.is_dir():
            raise RuntimeError(f"required host bind directory is missing: {path}")
        stat = path.stat()
        if stat.st_uid != expected_uid or stat.st_mode & 0o700 != 0o700:
            raise RuntimeError(
                f"host bind directory ownership is invalid: {name} requires UID {expected_uid} mode u+rwx"
            )
    _compose(release, "config", "--quiet")
    return manifest


def _readiness(release: Path) -> dict:
    manifest = _preflight(release)
    running = set(
        _compose(release, "ps", "--status", "running", "--services", capture=True).stdout.splitlines()
    )
    missing = sorted(RESIDENT - running)
    if missing:
        raise RuntimeError(f"resident services are not running: {missing}")
    checks = {
        "postgres": ["exec", "-T", "postgres", "pg_isready", "-U", "postgres", "-d", "postgres"],
        "migration": ["run", "--rm", "--no-deps", "migration-ready"],
        "backend_startup": ["exec", "-T", "backend", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health/startup',timeout=3).read()"],
        "oidc_endpoint_contract": ["exec", "-T", "backend", "python", "/opt/mentorflow/rp05/runtime-entrypoint.py", "oidc-ready"],
        "otel_health": ["exec", "-T", "backend", "python", "-c", "import urllib.request;urllib.request.urlopen('http://otel-collector:13133/health',timeout=3).read()"],
        "worker": ["exec", "-T", "worker", "python", "/opt/mentorflow/rp05/runtime-entrypoint.py", "worker-health"],
        "https_edge": ["exec", "-T", "edge", "wget", "-q", "--no-check-certificate", "-O", "/dev/null", "https://127.0.0.1:8443/health/startup"],
    }
    for args in checks.values():
        _compose(release, *args)
    public_probe = str(manifest["public_origin"]).rstrip("/") + "/health/startup"
    with urlopen(public_probe, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("public HTTPS startup probe did not return HTTP 200")
        try:
            payload = json.loads(response.read())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "public HTTPS startup probe returned a non-JSON response"
            ) from exc
        if not isinstance(payload, dict) or not payload.get("status"):
            raise RuntimeError("public HTTPS startup probe returned an invalid health payload")
    statuses = {name: "PASS" for name in checks}
    statuses["public_tls_https"] = "PASS"
    pkce_query = urlencode(
        {
            "client_id": "mentorflow-web",
            "redirect_uri": str(manifest["public_origin"]).rstrip("/")
            + "/api/enterprise/auth/callback",
            "response_type": "code",
            "scope": "openid profile email",
            "code_challenge": "A" * 43,
            "code_challenge_method": "S256",
            "state": "mentorflow-rp05-readiness-state",
            "nonce": "mentorflow-rp05-readiness-nonce",
        }
    )
    pkce_probe = (
        str(manifest["public_origin"]).rstrip("/")
        + "/realms/mentorflow/protocol/openid-connect/auth?"
        + pkce_query
    )
    with urlopen(pkce_probe, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("public PKCE browser flow probe did not reach the login page")
    statuses["pkce_browser_flow_url"] = "PASS"
    return {"resident_services": sorted(running), "checks": statuses}


def _evidence(release: Path) -> Path:
    manifest = _preflight(release)
    readiness = _readiness(release)
    payload = {
        "schema_version": "mentorflow.rp05-deployment-evidence.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release_manifest_sha256": _sha(release / "manifest.json"),
        "source": manifest["source"],
        "image_references": manifest["image_references"],
        "readiness": readiness,
        "canonical_full_product_readiness": "PENDING_OBJECT_STORAGE_INTEGRATION",
        "real_tls_staging_gate": "PASS",
        "real_minimal_stack_staging_gate": "PASS",
        "secrets_exposed": False,
    }
    target = ROOT / "evidence" / f"rp05-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, target)
    return target


def _restart_readiness(release: Path) -> dict:
    checkpoints = {"initial": _readiness(release)}
    phases = (
        ("backend", ("backend", "oidc-loopback-proxy")),
        ("worker", ("worker",)),
        ("frontend", ("frontend",)),
        ("edge", ("edge",)),
    )
    for name, services in phases:
        _compose(
            release,
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "240",
            *services,
        )
        checkpoints[name] = _readiness(release)
    return {
        "schema_version": "mentorflow.rp05-restart-readiness.v1",
        "status": "PASS",
        "checkpoints": {name: "PASS" for name in checkpoints},
    }


def _fresh_postgres(release: Path) -> dict:
    manifest = _preflight(release)
    image = str(manifest["image_references"]["POSTGRES_IMAGE"])
    name = f"mentorflow-rp05-fresh-postgres-{os.getpid()}-{secrets.token_hex(4)}"
    secret_path = ROOT / "secrets" / "postgres_admin_password"
    run_command = [
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--read-only",
        "--network",
        "none",
        "--memory",
        "768m",
        "--cpus",
        "0.75",
        "--cap-drop",
        "ALL",
    ]
    for capability in ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"):
        run_command.extend(("--cap-add", capability))
    run_command.extend(
        (
            "--tmpfs",
            "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m,mode=0700",
            "--tmpfs",
            "/var/run/postgresql:rw,noexec,nosuid,size=64m,mode=1777",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
            "--mount",
            f"type=bind,src={secret_path},dst=/run/secrets/postgres_admin_password,readonly",
            "--env",
            "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_admin_password",
            "--env",
            "POSTGRES_DB=postgres",
            "--env",
            "POSTGRES_USER=postgres",
            image,
        )
    )
    started = False
    try:
        result = subprocess.run(
            run_command,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "fresh-volume PostgreSQL container failed to start: "
                + result.stderr[-500:]
            )
        started = True
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    name,
                    "pg_isready",
                    "--host",
                    "/var/run/postgresql",
                    "--username",
                    "postgres",
                    "--dbname",
                    "postgres",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            state = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", name],
                check=False,
                text=True,
                capture_output=True,
            )
            if state.returncode != 0 or state.stdout.strip() != "true":
                raise RuntimeError("fresh-volume PostgreSQL exited before readiness")
            time.sleep(2)
        else:
            raise RuntimeError("fresh-volume PostgreSQL readiness deadline expired")
        version = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "psql",
                "--no-password",
                "--tuples-only",
                "--username",
                "postgres",
                "--dbname",
                "postgres",
                "--command",
                "SELECT current_setting('server_version_num')",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if version.returncode != 0 or not version.stdout.strip().isdigit():
            raise RuntimeError("fresh-volume PostgreSQL SQL verification failed")
        return {
            "schema_version": "mentorflow.rp05-postgres-fresh-volume.v1",
            "status": "PASS",
            "image": image,
            "persistent_data_touched": False,
        }
    finally:
        if started:
            active_error = sys.exc_info()[0] is not None
            cleanup = subprocess.run(
                ["docker", "rm", "--force", name],
                check=False,
                text=True,
                capture_output=True,
            )
            if cleanup.returncode != 0 and not active_error:
                raise RuntimeError("fresh-volume PostgreSQL cleanup failed")


def _activate(release: Path) -> None:
    _readiness(release)
    current = ROOT / "current"
    temporary = ROOT / ".current.rp05.tmp"
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, current)


def _rollback(name: str) -> None:
    if Path(name).name != name or name in (".", ".."):
        raise RuntimeError("rollback release name is invalid")
    target = (ROOT / "releases" / name).resolve()
    _preflight(target)
    _activate(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "config", "pull", "fresh-postgres", "migrate", "up", "readiness", "restart-readiness", "evidence", "activate", "rollback", "down"))
    parser.add_argument("--release", type=Path)
    parser.add_argument("--rollback-release")
    args = parser.parse_args()
    if args.action == "rollback":
        if not args.rollback_release:
            parser.error("--rollback-release is required")
        _rollback(args.rollback_release)
        print('{"schema_version":"mentorflow.rp05-orchestrator.v1","status":"PASS","action":"rollback"}')
        return 0
    if args.release is None:
        parser.error("--release is required")
    release = args.release.resolve()
    if args.action == "preflight":
        _preflight(release)
    elif args.action == "config":
        _preflight(release)
        _compose(release, "config", "--quiet")
    elif args.action == "pull":
        _preflight(release)
        _compose(release, "pull", "--policy", "always")
    elif args.action == "fresh-postgres":
        print(
            json.dumps(
                _fresh_postgres(release),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    elif args.action == "migrate":
        _preflight(release)
        _compose(release, "up", "--no-build", "--exit-code-from", "migration-ready", "migration-ready")
    elif args.action == "up":
        _preflight(release)
        _compose(release, "up", "-d", "--no-build")
    elif args.action == "readiness":
        _readiness(release)
    elif args.action == "restart-readiness":
        print(
            json.dumps(
                _restart_readiness(release),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    elif args.action == "evidence":
        print(_evidence(release))
    elif args.action == "activate":
        _activate(release)
    elif args.action == "down":
        _preflight(release)
        _compose(release, "stop")
    print(json.dumps({"schema_version": "mentorflow.rp05-orchestrator.v1", "status": "PASS", "action": args.action}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"schema_version": "mentorflow.rp05-orchestrator.v1", "status": "FAIL", "error": str(exc)[:300]}, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        raise SystemExit(1)
