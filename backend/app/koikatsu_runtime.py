"""Truthful, read-only KKS capability and runtime-manifest inspection.

The web process only inspects prebuilt local indexes and manifests.  It never
walks a game installation or starts Unity.  A runtime is marked ready only
when the artifacts advertised by its manifest actually exist and match their
recorded metadata; missing artifacts remain explicit partial/unavailable
states instead of being presented as a working native renderer.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_MANIFEST_PATH = Path(os.getenv("MENTORFLOW_KKS_RUNTIME_MANIFEST", str(PROJECT_ROOT / "data" / "kks-assets" / "runtime-manifest-v1.json")))
CARD_INDEX_PATH = Path(os.getenv("MENTORFLOW_KKS_CARD_INDEX", str(PROJECT_ROOT / "data" / "kks-assets" / "card-index-v1.sqlite3")))
ZIPMOD_INDEX_PATH = Path(os.getenv("MENTORFLOW_KKS_ZIPMOD_INDEX", str(PROJECT_ROOT / "data" / "kks-assets" / "zipmod-index-v1.csv")))
BASE_MODEL_PATH = Path(os.getenv("MENTORFLOW_KKS_BASE_MODEL", str(PROJECT_ROOT / "data" / "kks-assets" / "base-female.glb")))


def _path(value: Any, fallback: Path) -> Path:
    raw = str(value or "").strip()
    return Path(raw).expanduser() if raw else fallback


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _file_fingerprint(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False, "size": 0, "mtime_ns": 0}
    return {"path": str(path), "exists": path.is_file(), "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def runtime_version_hash(manifest: Mapping[str, Any] | None = None) -> str:
    """Return a stable version hash for the observed local runtime inputs."""
    value = dict(manifest or {})
    if not value:
        value = {
            "manifest": _file_fingerprint(RUNTIME_MANIFEST_PATH),
            "card_index": _file_fingerprint(CARD_INDEX_PATH),
            "zipmod_index": _file_fingerprint(ZIPMOD_INDEX_PATH),
            "base_model": _file_fingerprint(BASE_MODEL_PATH),
        }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_checks(source: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    paths = {
        "card_index": _path(source.get("card_index_path"), CARD_INDEX_PATH),
        "zipmod_index": _path(source.get("zipmod_index_path"), ZIPMOD_INDEX_PATH),
        "base_model": _path(source.get("base_model_path"), BASE_MODEL_PATH),
    }
    checks = {name: _file_fingerprint(path) for name, path in paths.items()}
    advertised = source.get("artifacts")
    if isinstance(advertised, list):
        for item in advertised:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or item.get("id") or "artifact").strip()
            path = _path(item.get("path"), PROJECT_ROOT / "__missing_kks_artifact__")
            check = _file_fingerprint(path)
            expected_size = item.get("size")
            if expected_size is not None:
                check["size_matches"] = check["exists"] and int(expected_size) == int(check["size"])
            checks[name] = check
    return checks


def runtime_manifest(*, manifest_path: Path | None = None) -> dict[str, Any]:
    """Build a live manifest from the local filesystem and optional manifest file."""
    path = manifest_path or RUNTIME_MANIFEST_PATH
    advertised = _read_json(path)
    source = {**advertised}
    checks = _artifact_checks(source)
    existing = [name for name, item in checks.items() if item.get("exists") and item.get("size_matches", True)]
    missing = [name for name, item in checks.items() if name not in existing]
    pipeline = str(advertised.get("pipeline_fingerprint") or advertised.get("pipeline_version") or "unknown")
    result = {
        "schema_version": str(advertised.get("schema_version") or "kks-runtime-v1"),
        "manifest_path": str(path),
        "manifest_exists": path.is_file(),
        "pipeline_fingerprint": pipeline,
        "checks": checks,
        "existing_artifacts": sorted(existing),
        "missing_artifacts": sorted(missing),
        "source": str(advertised.get("source") or "local-prebuilt-indexes"),
        "generated_at": advertised.get("generated_at") or None,
        "runtime_version_hash": runtime_version_hash({"advertised": advertised, "checks": checks}),
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
    }
    return result


def runtime_readiness(*, manifest_path: Path | None = None) -> dict[str, Any]:
    """Compute capability from observed files; never trust an advertised ready flag."""
    manifest = runtime_manifest(manifest_path=manifest_path)
    checks = manifest["checks"]
    present = len(manifest["existing_artifacts"])
    total = len(checks)
    # A real native renderer additionally requires the base model.  Index-only
    # installs are useful for inspection/prewarm but are intentionally partial.
    required = ("card_index", "zipmod_index", "base_model")
    ready = all(bool(checks.get(name, {}).get("exists")) for name in required)
    status = "ready" if ready else ("partial" if present else "unavailable")
    code = {"ready": "KKS_RUNTIME_READY", "partial": "KKS_RUNTIME_PARTIAL", "unavailable": "KKS_RUNTIME_UNAVAILABLE"}[status]
    return {
        "available": ready,
        "status": status,
        "code": code,
        "fallback": "teacher_without_avatar",
        "reason": "all required local artifacts are present" if ready else "missing: " + ", ".join(manifest["missing_artifacts"]) if manifest["missing_artifacts"] else "runtime manifest is invalid",
        "runtime_version_hash": manifest["runtime_version_hash"],
        "index_version": manifest["pipeline_fingerprint"],
        "manifest": manifest,
    }


def runtime_capability(*, manifest_path: Path | None = None) -> dict[str, Any]:
    """Public capability payload suitable for a future API route."""
    readiness = runtime_readiness(manifest_path=manifest_path)
    return {key: readiness[key] for key in ("available", "status", "code", "fallback", "reason", "runtime_version_hash", "index_version")}


# Explicit aliases make the integration point discoverable without coupling a
# route to implementation details.
get_runtime_manifest = runtime_manifest
get_runtime_readiness = runtime_readiness
get_runtime_capability = runtime_capability

