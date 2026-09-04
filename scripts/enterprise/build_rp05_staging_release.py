#!/usr/bin/env python3
"""Build an immutable, secret-free RP-05 minimal staging release directory."""

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
from pathlib import Path
from urllib.parse import urlparse


SCHEMA = "mentorflow.rp05-release.v1"
IMAGE_KEYS = {
    "POSTGRES_IMAGE",
    "KEYCLOAK_IMAGE",
    "MENTORFLOW_BACKEND_IMAGE",
    "MENTORFLOW_FRONTEND_IMAGE",
    "MENTORFLOW_WORKER_IMAGE",
    "OTEL_COLLECTOR_IMAGE",
}
PUBLIC_KEYS = {
    "MENTORFLOW_COMPOSE_PROJECT",
    "MENTORFLOW_PUBLIC_ORIGIN",
    "MENTORFLOW_PUBLIC_HOSTNAME",
    "MENTORFLOW_WORKER_RUNTIME_FACTORY",
    "MENTORFLOW_WORKER_HANDLER_DOCUMENT_INGESTION",
    "MENTORFLOW_WORKER_HANDLER_KNOWLEDGE_PIPELINE",
    "MENTORFLOW_WORKER_HANDLER_PLAN_CREATION",
    "MENTORFLOW_WORKER_CONCURRENCY",
}
PRODUCTION_FACTORY = "app.enterprise.jobs_v1.production:build"
PRODUCTION_HANDLERS = {
    "MENTORFLOW_WORKER_HANDLER_DOCUMENT_INGESTION": (
        "app.enterprise.jobs_v1.product_handlers:document_ingestion"
    ),
    "MENTORFLOW_WORKER_HANDLER_KNOWLEDGE_PIPELINE": (
        "app.enterprise.jobs_v1.product_handlers:knowledge_pipeline"
    ),
    "MENTORFLOW_WORKER_HANDLER_PLAN_CREATION": (
        "app.enterprise.jobs_v1.product_handlers:plan_creation"
    ),
}
DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/:@-]*@sha256:[0-9a-f]{64}$")
PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
FACTORY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
AUTHORITY = {
    "canonical_request_sha256": "70a2343ae1423ec9e10b9f025a7953c41c0784ce536a180b777e02f015f27aec",
    "owner_authority_sha256": "b43e1d3d2b1f306514f6a5a477d546f8969815235ea9b8d383d147b88e587032",
    "owner_authority_receipt_sha256": "dd88af2e92b354aab17f3ceb9f90f80de6cd0a86bf94092f0b3b3e1a132edb67",
}


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise ValueError(f"invalid or duplicate environment entry at line {line_number}")
        if key not in PUBLIC_KEYS:
            raise ValueError(f"unsupported environment key: {key}")
        if any(token in key.casefold() for token in ("password", "secret", "token", "private")):
            raise ValueError("secret-like environment keys are forbidden")
        values[key] = value.strip()
    missing = PUBLIC_KEYS - values.keys()
    if missing:
        raise ValueError(f"missing environment keys: {sorted(missing)}")
    if not PROJECT.fullmatch(values["MENTORFLOW_COMPOSE_PROJECT"]):
        raise ValueError("MENTORFLOW_COMPOSE_PROJECT is invalid")
    parsed = urlparse(values["MENTORFLOW_PUBLIC_ORIGIN"])
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("MENTORFLOW_PUBLIC_ORIGIN must be an HTTPS origin")
    if parsed.hostname != values["MENTORFLOW_PUBLIC_HOSTNAME"]:
        raise ValueError("public hostname must match public origin")
    if not FACTORY.fullmatch(values["MENTORFLOW_WORKER_RUNTIME_FACTORY"]):
        raise ValueError("worker runtime factory must be module:callable")
    if values["MENTORFLOW_WORKER_RUNTIME_FACTORY"] != PRODUCTION_FACTORY:
        raise ValueError("worker runtime factory must use the production factory")
    for key, expected in PRODUCTION_HANDLERS.items():
        if values[key] != expected:
            raise ValueError(f"{key} must use the production product adapter")
    concurrency = int(values["MENTORFLOW_WORKER_CONCURRENCY"])
    if not 1 <= concurrency <= 64:
        raise ValueError("worker concurrency must be between 1 and 64")
    return values


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def build(source_root: Path, images_path: Path, env_path: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("output release directory already exists")
    if _git(source_root, "status", "--porcelain"):
        raise ValueError("source checkout must be clean")
    branch = _git(source_root, "branch", "--show-current")
    head = _git(source_root, "rev-parse", "HEAD")
    tree = _git(source_root, "rev-parse", "HEAD^{tree}")
    if not branch.startswith("codex/"):
        raise ValueError("release source must be an owned codex branch")

    payload = _json(images_path)
    images = payload.get("images")
    if payload.get("schema_version") != "mentorflow.rp05-runtime-images.v1" or not isinstance(images, dict):
        raise ValueError("runtime image manifest schema is invalid")
    if set(images) != IMAGE_KEYS:
        raise ValueError("runtime image manifest must contain the exact RP-05 image set")
    for key, value in images.items():
        if not isinstance(value, str) or not DIGEST.fullmatch(value):
            raise ValueError(f"{key} is not an immutable image digest reference")
    public = _env(env_path)

    source_assets = source_root / "deploy" / "enterprise" / "staging" / "rp05"
    copies = {
        source_assets / "compose.minimal.yml": "compose.minimal.yml",
        source_assets / "edge-https.conf": "edge-https.conf",
        source_assets / "oidc-loopback-proxy.conf": "oidc-loopback-proxy.conf",
        source_assets / "keycloak-ready.sh": "keycloak-ready.sh",
        source_assets / "otel-collector.yml": "otel-collector.yml",
        source_assets / "postgres-provision.py": "postgres-provision.py",
        source_assets / "secret-materializer.py": "secret-materializer.py",
        source_assets / "runtime-entrypoint.py": "runtime-entrypoint.py",
        source_assets / "README.md": "README.md",
        source_root / "deploy" / "enterprise" / "config" / "keycloak-realm.json": "keycloak-realm.json",
        source_root / "scripts" / "enterprise" / "rp05_staging.py": "rp05_staging.py",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        for source, name in copies.items():
            if not source.is_file():
                raise ValueError(f"required release asset is missing: {source}")
            shutil.copyfile(source, stage / name)

        runtime = {
            **dict(sorted(images.items())),
            **dict(sorted(public.items())),
            "MENTORFLOW_DATA_DIR": "/opt/mentorflow/data",
            "MENTORFLOW_SECRETS_DIR": "/opt/mentorflow/secrets",
        }
        _write(stage / "runtime.env", "".join(f"{key}={value}\n" for key, value in sorted(runtime.items())))
        files = {path.name: _sha(path) for path in sorted(stage.iterdir()) if path.is_file()}
        manifest = {
            "schema_version": SCHEMA,
            "status": "READY_FOR_HOST_VALIDATION",
            "source": {"branch": branch, "head": head, "tree": tree},
            "authority": AUTHORITY,
            "files": files,
            "image_references": dict(sorted(images.items())),
            "public_origin": public["MENTORFLOW_PUBLIC_ORIGIN"],
            "host_layout": "/opt/mentorflow",
            "real_tls_staging_gate": "PENDING",
            "real_minimal_stack_staging_gate": "PENDING",
            "secrets_exposed": False,
        }
        _write(stage / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.replace(stage, output)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(
        args.source_root.resolve(), args.images.resolve(), args.env.resolve(), args.output.resolve()
    )
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"schema_version": "mentorflow.rp05-release-build.v1", "status": "FAIL", "error": str(exc)[:300]}, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        raise SystemExit(1)
