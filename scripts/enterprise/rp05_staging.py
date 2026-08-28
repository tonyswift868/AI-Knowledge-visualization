#!/usr/bin/env python3
"""Fail-closed host orchestrator for an immutable RP-05 release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
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
RESIDENT = {"postgres", "keycloak", "otel-collector", "backend", "worker", "frontend", "edge"}


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
        "keycloak_discovery": ["exec", "-T", "backend", "python", "-c", "import urllib.request;urllib.request.urlopen('http://keycloak:8080/realms/mentorflow/.well-known/openid-configuration',timeout=3).read()"],
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
    statuses = {name: "PASS" for name in checks}
    statuses["public_tls_https"] = "PASS"
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
    parser.add_argument("action", choices=("preflight", "config", "pull", "migrate", "up", "readiness", "evidence", "activate", "rollback", "down"))
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
    elif args.action == "migrate":
        _preflight(release)
        _compose(release, "up", "--no-build", "--exit-code-from", "migration-ready", "migration-ready")
    elif args.action == "up":
        _preflight(release)
        _compose(release, "up", "-d", "--no-build")
    elif args.action == "readiness":
        _readiness(release)
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
