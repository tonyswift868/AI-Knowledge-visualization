from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import threading
import urllib.request
import wave
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Mapping, Protocol
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException

from .release_trust_policy import (
    ReleaseTrustBinding,
    ReleaseTrustError,
    VerifiedReleaseTrust,
    secure_hash_release_file,
)
from .windows_safe_relative import (
    WindowsSafeRelativePathError,
    validate_windows_safe_relative,
    windows_casefold_key,
)
from .windows_install_path_budget import (
    WindowsInstallPathBudgetError,
    validate_windows_install_root_budget,
)
from .local_voice_runtime_supervisor import LocalVoiceRuntimeError
from .private_voice_release_registration import (
    PrivateVoiceReleaseRegistrationError,
    resolve_app_private_release,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACK_ROOT = PROJECT_ROOT / "data" / "voice" / "packs"
DEFAULT_CATALOG_PATH = (
    PROJECT_ROOT / "backend" / "config" / "voice" / "teacher_voice_pack_catalog.v1.json"
)
PACK_SCHEMA = "mentorflow.voice-pack.v1"
CATALOG_SCHEMA = "mentorflow.voice-pack-catalog.v1"
QWEN_BASE_CLONE_ADAPTER_ID = "mentorflow.qwen3-tts-base-clone.v1"
QWEN_CUSTOM_PRIVATE_ADAPTER_ID = "mentorflow.qwen3-tts-custom-private.v1"
QWEN_BASE_CLONE_ENGINE_KIND = "qwen3-tts-0.6b-base"
QWEN_BASE_CLONE_PREPARE_SCHEMA = "mentorflow.qwen-base-clone-prepare.v1"
QWEN_BASE_CLONE_SYNTHESIS_SCHEMA = "mentorflow.qwen-base-clone-synthesis.v1"
QWEN_BASE_CLONE_BINDING_CONTRACT_SCHEMA = "mentorflow.qwen-base-clone-pack-binding.v1"
QWEN_BASE_CLONE_PREPARE_RESULT_SCHEMA = "mentorflow.qwen-base-clone-prepare-result.v1"
QWEN_BASE_CLONE_AUDIO_SCOPE_SCHEMA = "mentorflow.qwen-base-clone-audio-scope.v1"
QWEN_ENGINE_CATALOG_SCHEMA = "mentorflow.qwen-engine-catalog.v1"
QWEN_ENGINE_MANIFEST_SCHEMA = "mentorflow.qwen-engine-manifest.v1"
GPT_SOVITS_ADAPTER_ID = "mentorflow.gpt-sovits.v1"
PRIVATE_LOCAL_SCOPE = "private_local_only_nonexportable"
REQUIRED_RIGHTS = {
    "training",
    "weight_distribution",
    "reference_audio_distribution",
    "commercial_synthesis",
}
REQUIRED_STYLES = {"lively", "relaxed"}
JOB_STATES = {"not_installed", "staging", "verified", "installed", "active", "failed"}
FALLBACK_POLICIES = {"off", "system_voice"}
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
MAX_MANIFEST_BYTES = 1024 * 1024
DEFAULT_MAX_UNPACKED_BYTES = 20 * 1024 * 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
MAX_HTTPS_REDIRECTS = 5
_STATE_LOCK = threading.RLock()
_ACTIVE_VERIFIED_CACHE_LOCK = threading.RLock()
_ACTIVE_VERIFIED_CACHE: dict[str, Any] | None = None

_QWEN_RUNTIME_FIELDS = {"adapter", "engine_dependency"}
_QWEN_ENGINE_DEPENDENCY_FIELDS = {
    "kind",
    "catalog_id",
    "version",
    "manifest_sha256",
}
_QWEN_ALLOWED_FILE_ROLES = {
    "reference_audio",
    "rights_evidence",
    "qa_evidence",
}
_QWEN_MANIFEST_FIELDS = {
    "schema",
    "pack_id",
    "version",
    "identity",
    "rights",
    "qa",
    "runtime",
    "styles",
    "files",
}
_QWEN_IDENTITY_FIELDS = {
    "kind",
    "display_name",
    "impersonates",
    "source_identity",
}
_QWEN_RIGHTS_FIELDS = {"status", "covers", "evidence"}
_QWEN_QA_FIELDS = {
    "status",
    "transcript_alignment_verified",
    "reference_audio_reviewed",
    "original_identity_reviewed",
    "evidence",
}
_QWEN_STYLES_FIELDS = {"lively", "relaxed"}
_QWEN_STYLE_FIELDS = {"reference_audio", "language", "transcript"}
_QWEN_FILE_FIELDS = {"path", "role", "bytes", "sha256"}
_QWEN_ENGINE_CATALOG_FIELDS = {"schema", "engines"}
_QWEN_ENGINE_CATALOG_ENTRY_FIELDS = {
    "kind",
    "package_id",
    "version",
    "archive",
    "manifest",
    "signing_key_id",
}
_QWEN_ENGINE_DIGEST_FIELDS = {"bytes", "sha256"}
_QWEN_ENGINE_MANIFEST_FIELDS = {
    "schema",
    "kind",
    "package_id",
    "version",
    "install_path_budget",
    "files",
}
_QWEN_ENGINE_FILE_FIELDS = {"path", "role", "bytes", "sha256"}
_QWEN_ENGINE_FILE_ROLES = {"license", "metadata", "model_asset", "runtime_asset"}
_QWEN_MAX_PACK_BYTES = 128 * 1024 * 1024
_QWEN_CUSTOM_PRIVATE_MAX_PACK_BYTES = 3 * 1024 * 1024 * 1024
_QWEN_CUSTOM_PRIVATE_FIELDS = {
    "schema", "pack_id", "version", "identity", "rights", "qa", "runtime",
    "styles", "files", "private_prerelease",
}
_QWEN_CUSTOM_PRIVATE_ROLES = {
    "model_asset", "checkpoint_receipt", "rights_evidence", "qa_evidence",
}
_QWEN_FILE_POLICIES: dict[str, tuple[str, set[str], int]] = {
    "reference_audio": ("references", {".wav"}, 60 * 1024 * 1024),
    "rights_evidence": ("rights", {".json", ".md", ".pdf", ".txt"}, 8 * 1024 * 1024),
    "qa_evidence": ("qa", {".csv", ".json", ".md", ".txt"}, 16 * 1024 * 1024),
}


class TeacherVoicePackError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


def _fail(
    code: str,
    message: str,
    *,
    status_code: int = 400,
    details: Mapping[str, Any] | None = None,
) -> None:
    raise TeacherVoicePackError(code, message, status_code=status_code, details=details)


def error_detail(exc: TeacherVoicePackError) -> dict[str, Any]:
    return {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
    }


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("VOICE_PACK_INVALID", f"{field} must be an object")
    return dict(value)


def _safe_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not SAFE_ID.fullmatch(text):
        _fail("VOICE_PACK_INVALID", f"{field} is invalid")
    return text


def _sha256(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA256_HEX.fullmatch(text):
        _fail("VOICE_PACK_INVALID", f"{field} must be a lowercase SHA-256 digest")
    return text


def _strict_object(
    value: Any,
    field: str,
    allowed_fields: set[str],
    *,
    code: str = "VOICE_PACK_QWEN_SCHEMA_INVALID",
) -> dict[str, Any]:
    """Parse one object with ``extra=forbid`` and no implicit defaults."""
    if not isinstance(value, Mapping):
        _fail(code, f"{field} must be an object")
    result = dict(value)
    if any(not isinstance(key, str) for key in result):
        _fail(code, f"{field} keys must be strings")
    present = set(result)
    unexpected = sorted(present - allowed_fields)
    missing = sorted(allowed_fields - present)
    if unexpected or missing:
        _fail(
            code,
            f"{field} must match its strict schema",
            details={"unexpected_fields": unexpected, "missing_fields": missing},
        )
    return result


def _qwen_engine_dependency_values(runtime: Mapping[str, Any]) -> tuple[str, str, str, str]:
    runtime = _strict_object(runtime, "runtime", _QWEN_RUNTIME_FIELDS)
    dependency = _strict_object(
        runtime.get("engine_dependency"),
        "runtime.engine_dependency",
        _QWEN_ENGINE_DEPENDENCY_FIELDS,
        code="VOICE_PACK_ENGINE_DEPENDENCY_INVALID",
    )
    kind = str(dependency.get("kind") or "").strip()
    if kind != QWEN_BASE_CLONE_ENGINE_KIND:
        _fail(
            "VOICE_PACK_ENGINE_DEPENDENCY_INVALID",
            f"Qwen engine kind must be {QWEN_BASE_CLONE_ENGINE_KIND}",
        )
    return (
        kind,
        _safe_id(dependency.get("catalog_id"), "runtime.engine_dependency.catalog_id"),
        _safe_id(dependency.get("version"), "runtime.engine_dependency.version"),
        _sha256(
            dependency.get("manifest_sha256"),
            "runtime.engine_dependency.manifest_sha256",
        ),
    )


def _safe_relative(value: Any, field: str) -> PurePosixPath:
    try:
        return validate_windows_safe_relative(value)
    except WindowsSafeRelativePathError as exc:
        _fail(
            "VOICE_PACK_PATH_INVALID",
            f"{field} is not an exact Windows-safe relative POSIX path: {exc}",
        )


def _join_relative(root: Path, relative: PurePosixPath) -> Path:
    # Keep the lexical path so later reparse-point checks can still inspect
    # every component.  Returning the resolved target here would erase an
    # in-root symlink/junction and make it indistinguishable from a normal
    # file even though the target remains inside the pack root.
    candidate = Path(os.path.abspath(root.joinpath(*relative.parts)))
    resolved_root = root.resolve(strict=False)
    if not candidate.resolve(strict=False).is_relative_to(resolved_root):
        _fail("VOICE_PACK_PATH_INVALID", "pack file escapes the pack root")
    return candidate


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        _fail(
            "VOICE_PACK_REPARSE_CHECK_FAILED",
            f"cannot safely inspect path metadata: {path}",
            status_code=500,
            details={"error": f"{type(exc).__name__}:{exc}"},
        )
    # Windows exposes every reparse tag (junctions, mount points, cloud
    # placeholders, and other tags) through this attribute.  Restricting the
    # check to is_symlink()/is_junction() would miss other traversable tags.
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(
        metadata.st_mode
    )


def _assert_no_reparse(path: Path, boundary: Path) -> None:
    boundary_absolute = Path(os.path.abspath(boundary))
    candidate = Path(os.path.abspath(path))
    resolved_boundary = boundary_absolute.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    if not resolved_candidate.is_relative_to(resolved_boundary):
        _fail("VOICE_PACK_PATH_INVALID", "path is outside the trusted pack root")
    while True:
        if _is_reparse_point(candidate):
            _fail("VOICE_PACK_REPARSE_FORBIDDEN", f"symlink or junction is forbidden: {candidate}")
        if candidate == boundary_absolute:
            return
        if candidate.parent == candidate:
            _fail("VOICE_PACK_PATH_INVALID", "path is outside the trusted pack root")
        candidate = candidate.parent


def _ensure_secure_directory(root: Path, directory: Path) -> None:
    root = Path(os.path.abspath(root))
    directory = Path(os.path.abspath(directory))
    if not directory.resolve(strict=False).is_relative_to(root.resolve(strict=False)):
        _fail("VOICE_PACK_PATH_INVALID", "directory is outside the trusted pack root")
    # Build one component at a time so a pre-positioned junction cannot be
    # traversed by mkdir(parents=True) before it is inspected.
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(exist_ok=True)
    _assert_no_reparse(root, root)
    current = root
    relative_parts = directory.relative_to(root).parts
    for part in relative_parts:
        current = current / part
        if current.exists() or _is_reparse_point(current):
            _assert_no_reparse(current, root)
            if not current.is_dir():
                _fail("VOICE_PACK_PATH_INVALID", f"expected directory: {current}")
        else:
            current.mkdir()
            _assert_no_reparse(current, root)


def _pack_root() -> Path:
    configured = str(os.getenv("MENTORFLOW_VOICE_PACK_ROOT") or "").strip()
    if configured:
        raw_root = Path(configured).expanduser()
    else:
        try:
            private_release = resolve_app_private_release()
        except PrivateVoiceReleaseRegistrationError as exc:
            _fail(
                "VOICE_PACK_PRIVATE_REGISTRATION_INVALID",
                "app private voice release registration is invalid",
                status_code=503,
                details={"error": str(exc)},
            )
        raw_root = private_release["pack_root"] if private_release else DEFAULT_PACK_ROOT
    raw_root = Path(os.path.abspath(raw_root))
    # Do not erase evidence that the configured root itself is a junction by
    # resolving it before inspection.
    candidate = raw_root
    while True:
        if _is_reparse_point(candidate):
            _fail("VOICE_PACK_REPARSE_FORBIDDEN", f"voice pack root uses a symlink or junction: {candidate}", status_code=500)
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    root = raw_root.resolve(strict=False)
    if root.parent == root:
        _fail("VOICE_PACK_ROOT_INVALID", "voice pack root cannot be a filesystem root", status_code=500)
    return root


def _catalog_path() -> Path:
    configured = str(os.getenv("MENTORFLOW_VOICE_PACK_CATALOG") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    try:
        private_release = resolve_app_private_release()
    except PrivateVoiceReleaseRegistrationError as exc:
        _fail(
            "VOICE_PACK_PRIVATE_REGISTRATION_INVALID",
            "app private voice release registration is invalid",
            status_code=503,
            details={"error": str(exc)},
        )
    return (private_release["catalog_path"] if private_release else DEFAULT_CATALOG_PATH).resolve(strict=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, *, missing: Any = None) -> Any:
    if not path.is_file():
        return missing
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("VOICE_PACK_STATE_INVALID", f"cannot read {path.name}: {exc}", status_code=500)


def _write_json_atomic(
    path: Path,
    payload: Mapping[str, Any],
    *,
    boundary: Path | None = None,
) -> None:
    if boundary is not None:
        _ensure_secure_directory(boundary, path.parent)
        _assert_no_reparse(path, boundary)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_pcm_wav_stream(source: Any, field: str) -> dict[str, int]:
    """Parse a complete mono 16-bit PCM WAV instead of trusting RIFF bytes."""
    try:
        with wave.open(source, "rb") as wav_file:
            channels = int(wav_file.getnchannels())
            sample_width = int(wav_file.getsampwidth())
            sample_rate = int(wav_file.getframerate())
            frame_count = int(wav_file.getnframes())
            compression = str(wav_file.getcomptype())
            frames = wav_file.readframes(frame_count + 1)
    except (EOFError, OSError, ValueError, wave.Error) as exc:
        _fail(
            "VOICE_PACK_FILE_INVALID",
            f"{field} is not a parseable WAV file",
            details={"error": f"{type(exc).__name__}:{exc}"},
        )
    if (
        compression != "NONE"
        or channels != 1
        or sample_width != 2
        or not 16_000 <= sample_rate <= 48_000
        or frame_count <= 0
        or len(frames) != frame_count * channels * sample_width
    ):
        _fail(
            "VOICE_PACK_FILE_INVALID",
            f"{field} must be complete mono 16-bit PCM WAV at 16-48 kHz",
            details={
                "channels": channels,
                "sample_width": sample_width,
                "sample_rate": sample_rate,
                "frame_count": frame_count,
                "compression": compression,
                "decoded_bytes": len(frames),
            },
        )
    return {
        "channels": channels,
        "sample_width": sample_width,
        "sample_rate": sample_rate,
        "frame_count": frame_count,
    }


def _validate_pcm_wav_path(path: Path, field: str) -> dict[str, int]:
    try:
        with path.open("rb") as handle:
            return _validate_pcm_wav_stream(handle, field)
    except TeacherVoicePackError:
        raise
    except OSError as exc:
        _fail(
            "VOICE_PACK_FILE_INVALID",
            f"cannot read {field}",
            details={"error": f"{type(exc).__name__}:{exc}"},
        )


def _validate_pcm_wav_bytes(audio: bytes, field: str) -> dict[str, int]:
    return _validate_pcm_wav_stream(io.BytesIO(audio), field)


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify via the maintained ``cryptography`` implementation or fail closed."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        _fail(
            "VOICE_PACK_SIGNATURE_VERIFIER_UNAVAILABLE",
            "Ed25519 verification dependency is unavailable",
            status_code=503,
            details={"dependency": "cryptography", "error": str(exc)},
        )
    if len(public_key) != 32 or len(signature) != 64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


def _decode_key(value: Any, field: str, expected_bytes: int) -> bytes:
    text = str(value or "").strip()
    try:
        decoded = bytes.fromhex(text) if re.fullmatch(r"[0-9a-fA-F]+", text or " ") else base64.b64decode(text, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        _fail("VOICE_PACK_SIGNATURE_INVALID", f"{field} is not valid hex/base64", details={"error": str(exc)})
    if len(decoded) != expected_bytes:
        _fail("VOICE_PACK_SIGNATURE_INVALID", f"{field} must contain {expected_bytes} bytes")
    return decoded


def _load_catalog() -> tuple[Path, dict[str, Any]]:
    path = _catalog_path()
    payload = _read_json(path)
    if payload is None:
        _fail("VOICE_PACK_CATALOG_MISSING", "teacher voice pack catalog is missing", status_code=503)
    catalog = _mapping(payload, "catalog")
    if catalog.get("schema") != CATALOG_SCHEMA:
        _fail("VOICE_PACK_CATALOG_INVALID", f"catalog schema must be {CATALOG_SCHEMA}", status_code=500)
    packs = catalog.get("packs")
    if not isinstance(packs, list):
        _fail("VOICE_PACK_CATALOG_INVALID", "catalog.packs must be a list", status_code=500)
    allowed_hosts = catalog.get("https_allowed_hosts", [])
    if not isinstance(allowed_hosts, list) or any(
        not isinstance(host, str) or not host.strip() for host in allowed_hosts
    ):
        _fail(
            "VOICE_PACK_CATALOG_INVALID",
            "catalog.https_allowed_hosts must be a list of host names",
            status_code=500,
        )
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(packs):
        entry = _mapping(raw, f"catalog.packs[{index}]")
        identity = (_safe_id(entry.get("pack_id"), "pack_id"), _safe_id(entry.get("version"), "version"))
        if identity in seen:
            _fail("VOICE_PACK_CATALOG_INVALID", f"duplicate catalog entry: {identity}", status_code=500)
        seen.add(identity)
        source = _mapping(entry.get("source"), "source")
        if source.get("kind") not in {"bundled", "https"}:
            _fail("VOICE_PACK_CATALOG_INVALID", "source.kind must be bundled or https", status_code=500)
        if entry.get("runtime_adapter") == QWEN_CUSTOM_PRIVATE_ADAPTER_ID:
            if (
                entry.get("scope") != "private_local_only_nonexportable"
                or entry.get("usage_scope") != "local_private"
                or entry.get("release_channel") != "prerelease"
                or entry.get("default") is not True
                or source.get("kind") != "bundled"
            ):
                _fail(
                    "VOICE_PACK_PRIVATE_CATALOG_INVALID",
                    "private custom voice catalog entries must be bundled and explicitly non-exportable",
                    status_code=500,
                )
        elif entry.get("runtime_adapter") == GPT_SOVITS_ADAPTER_ID and entry.get("usage_scope") == "local_private":
            if (
                entry.get("scope") != PRIVATE_LOCAL_SCOPE
                or entry.get("release_channel") != "prerelease"
                or source.get("kind") != "bundled"
            ):
                _fail(
                    "VOICE_PACK_PRIVATE_CATALOG_INVALID",
                    "private GPT-SoVITS catalog entries must be bundled and explicitly non-exportable",
                    status_code=500,
                )
        release_channel = str(entry.get("release_channel") or "stable")
        usage_scope = str(entry.get("usage_scope") or "public")
        if release_channel not in {"stable", "prerelease"}:
            _fail(
                "VOICE_PACK_CATALOG_INVALID",
                "release_channel must be stable or prerelease",
                status_code=500,
            )
        if usage_scope not in {"public", "local_private"}:
            _fail(
                "VOICE_PACK_CATALOG_INVALID",
                "usage_scope must be public or local_private",
                status_code=500,
            )
        archive = _mapping(entry.get("archive"), "archive")
        if not isinstance(archive.get("bytes"), int) or archive["bytes"] <= 0:
            _fail("VOICE_PACK_CATALOG_INVALID", "archive.bytes must be positive", status_code=500)
        _sha256(archive.get("sha256"), "archive.sha256")
        _safe_id(entry.get("signing_key_id"), "signing_key_id")
    return path, catalog


def _catalog_entries(catalog: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in catalog.get("packs", []) if isinstance(item, Mapping)]


def _catalog_entry(catalog: Mapping[str, Any], pack_id: str, version: str | None) -> dict[str, Any]:
    safe_pack_id = _safe_id(pack_id, "pack_id")
    candidates = [entry for entry in _catalog_entries(catalog) if entry.get("pack_id") == safe_pack_id]
    if version:
        safe_version = _safe_id(version, "version")
        candidates = [entry for entry in candidates if entry.get("version") == safe_version]
    if not candidates:
        _fail("VOICE_PACK_NOT_CONFIGURED", "voice pack is not present in the application catalog", status_code=404)
    if len(candidates) > 1:
        defaults = [entry for entry in candidates if entry.get("default") is True]
        if len(defaults) != 1:
            _fail("VOICE_PACK_VERSION_REQUIRED", "catalog contains multiple versions; choose one", status_code=409)
        return defaults[0]
    return candidates[0]


def _trusted_keys(catalog: Mapping[str, Any]) -> dict[str, bytes]:
    values: dict[str, Any] = {}
    raw_keys = catalog.get("trusted_keys", [])
    if not isinstance(raw_keys, list):
        _fail("VOICE_PACK_CATALOG_INVALID", "trusted_keys must be a list", status_code=500)
    for raw in raw_keys:
        item = _mapping(raw, "trusted_keys[]")
        values[_safe_id(item.get("key_id"), "trusted key id")] = item.get("public_key")
    environment = str(os.getenv("MENTORFLOW_VOICE_PACK_PUBLIC_KEYS") or "").strip()
    if environment:
        try:
            overrides = json.loads(environment)
        except json.JSONDecodeError as exc:
            _fail("VOICE_PACK_KEY_CONFIG_INVALID", "public-key environment JSON is invalid", status_code=500, details={"error": str(exc)})
        if not isinstance(overrides, Mapping):
            _fail("VOICE_PACK_KEY_CONFIG_INVALID", "public-key environment must be an object", status_code=500)
        values.update(overrides)
    return {key_id: _decode_key(value, f"trusted key {key_id}", 32) for key_id, value in values.items()}


def _https_allowed_hosts(catalog: Mapping[str, Any]) -> set[str]:
    hosts = {
        str(host).strip().casefold()
        for host in catalog.get("https_allowed_hosts", [])
        if str(host).strip()
    }
    configured = str(os.getenv("MENTORFLOW_VOICE_PACK_HTTPS_ALLOWED_HOSTS") or "").strip()
    if configured:
        hosts.update(part.strip().casefold() for part in configured.split(",") if part.strip())
    return hosts


def _validate_catalog_https_url(url: str, allowed_hosts: set[str], field: str) -> None:
    parsed = urlparse(url)
    host = str(parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not host
        or host not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        _fail(
            "VOICE_PACK_SOURCE_FORBIDDEN",
            f"{field} must remain HTTPS on a catalog-allowlisted host",
        )


class _PrevalidatedHTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate every redirect target before urllib is allowed to open it."""

    def __init__(self, allowed_hosts: set[str]) -> None:
        super().__init__()
        self.allowed_hosts = set(allowed_hosts)
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        target = urljoin(req.full_url, str(newurl or ""))
        self.redirect_count += 1
        try:
            if self.redirect_count > MAX_HTTPS_REDIRECTS:
                _fail(
                    "VOICE_PACK_REDIRECT_LIMIT",
                    f"voice pack download exceeded {MAX_HTTPS_REDIRECTS} redirects",
                )
            _validate_catalog_https_url(
                target,
                self.allowed_hosts,
                "redirected download source",
            )
        except TeacherVoicePackError:
            close = getattr(fp, "close", None)
            if callable(close):
                close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, target)


def _open_catalog_https(url: str, allowed_hosts: set[str]):  # type: ignore[no-untyped-def]
    _validate_catalog_https_url(url, allowed_hosts, "catalog download source")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MentorFlow-VoicePack/1"},
    )
    opener = urllib.request.build_opener(
        _PrevalidatedHTTPSRedirectHandler(allowed_hosts)
    )
    try:
        return opener.open(request, timeout=60)
    except TeacherVoicePackError:
        raise
    except (OSError, ValueError) as exc:
        _fail(
            "VOICE_PACK_DOWNLOAD_FAILED",
            "voice pack download failed",
            status_code=503,
            details={"error": str(exc)},
        )


def _validate_qwen_custom_private_manifest(
    manifest: Mapping[str, Any],
    *,
    payload_root: Path | None,
    verify_files: bool,
) -> dict[str, Any]:
    """Validate the explicitly non-exportable fine-tuned local prerelease.

    This is deliberately separate from the production/original voice-pack
    contract.  It cannot satisfy or weaken the public/commercial rights gate.
    """
    value = _strict_object(manifest, "manifest", _QWEN_CUSTOM_PRIVATE_FIELDS)
    _safe_id(value.get("pack_id"), "pack_id")
    _safe_id(value.get("version"), "version")
    private = _strict_object(
        value.get("private_prerelease"),
        "private_prerelease",
        {"scope", "exportable", "public_release", "commercial_use", "default_eligible"},
    )
    if private != {
        "scope": "private_local_only_nonexportable",
        "exportable": False,
        "public_release": False,
        "commercial_use": False,
        "default_eligible": True,
    }:
        _fail("VOICE_PACK_PRIVATE_SCOPE_INVALID", "private prerelease scope flags are invalid")
    identity = _strict_object(
        value.get("identity"),
        "identity",
        {"kind", "display_name", "impersonates", "source_identity"},
    )
    if (
        identity.get("kind") != "declared_third_party_character_voice"
        or identity.get("impersonates") is not True
        or identity.get("source_identity") != "椿"
        or not str(identity.get("display_name") or "").strip()
    ):
        _fail("VOICE_PACK_PRIVATE_IDENTITY_INVALID", "private prerelease identity declaration is invalid")
    rights = _strict_object(value.get("rights"), "rights", {"status", "scope", "grants", "evidence"})
    grants = _strict_object(
        rights.get("grants"),
        "rights.grants",
        {"training", "private_local_inference", "weight_distribution", "reference_audio_distribution", "commercial_synthesis", "public_release"},
    )
    if (
        rights.get("status") != "restricted"
        or rights.get("scope") != "private_local_only"
        or grants != {
            "training": True,
            "private_local_inference": True,
            "weight_distribution": False,
            "reference_audio_distribution": False,
            "commercial_synthesis": False,
            "public_release": False,
        }
    ):
        _fail("VOICE_PACK_PRIVATE_RIGHTS_INVALID", "private prerelease rights must remain non-exportable")
    runtime = _strict_object(value.get("runtime"), "runtime", {"adapter", "dependency"})
    if runtime.get("adapter") != QWEN_CUSTOM_PRIVATE_ADAPTER_ID:
        _fail("VOICE_PACK_PRIVATE_RUNTIME_INVALID", "private runtime adapter is invalid")
    dependency = _strict_object(
        runtime.get("dependency"),
        "runtime.dependency",
        {"kind", "registration_id", "portable_runtime_receipt_sha256"},
    )
    if dependency.get("kind") != "qwen3-tts-12hz-portable-private" or not SHA256_HEX.fullmatch(str(dependency.get("portable_runtime_receipt_sha256") or "")):
        _fail("VOICE_PACK_PRIVATE_RUNTIME_INVALID", "private portable runtime dependency is invalid")
    _safe_id(dependency.get("registration_id"), "runtime.dependency.registration_id")
    qa = _strict_object(
        value.get("qa"),
        "qa",
        {"status", "checkpoint_integrity_verified", "audio_format_verified", "human_listening_review", "evidence"},
    )
    if (
        qa.get("status") != "technical_pass_human_review_pending"
        or qa.get("checkpoint_integrity_verified") is not True
        or qa.get("audio_format_verified") is not True
        or qa.get("human_listening_review") != "pending"
    ):
        _fail("VOICE_PACK_PRIVATE_QA_INVALID", "private prerelease QA declaration is invalid")
    styles = _strict_object(value.get("styles"), "styles", {"lively", "relaxed"})
    for name in ("lively", "relaxed"):
        style = _strict_object(styles[name], f"styles.{name}", {"speaker", "language", "behavior"})
        if style != {"speaker": "tsubaki_teacher", "language": "Chinese", "behavior": "neutral_same_voice"}:
            _fail("VOICE_PACK_PRIVATE_STYLE_INVALID", f"styles.{name} must map explicitly to the neutral trained voice")

    raw_files = value.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        _fail("VOICE_PACK_PRIVATE_FILES_INVALID", "private prerelease files must be a non-empty list")
    files: dict[str, dict[str, Any]] = {}
    folded: set[str] = set()
    total = 0
    roles: dict[str, int] = {}
    for index, raw in enumerate(raw_files):
        item = _strict_object(raw, f"files[{index}]", {"path", "role", "bytes", "sha256"})
        relative = _safe_relative(item.get("path"), f"files[{index}].path").as_posix()
        if relative in files or relative.casefold() in folded:
            _fail("VOICE_PACK_PRIVATE_FILES_INVALID", f"duplicate private pack file: {relative}")
        role = _safe_id(item.get("role"), f"files[{index}].role")
        size = item.get("bytes")
        if role not in _QWEN_CUSTOM_PRIVATE_ROLES or not isinstance(size, int) or isinstance(size, bool) or size < 1:
            _fail("VOICE_PACK_PRIVATE_FILES_INVALID", f"private pack file declaration is invalid: {relative}")
        record = {"path": relative, "role": role, "bytes": size, "sha256": _sha256(item.get("sha256"), f"files[{index}].sha256")}
        files[relative] = record
        folded.add(relative.casefold())
        roles[role] = roles.get(role, 0) + 1
        total += size
    required_model = {
        "model/config.json", "model/generation_config.json", "model/merges.txt", "model/model.safetensors",
        "model/preprocessor_config.json", "model/tokenizer_config.json", "model/vocab.json",
        "model/speech_tokenizer/config.json", "model/speech_tokenizer/configuration.json",
        "model/speech_tokenizer/model.safetensors", "model/speech_tokenizer/preprocessor_config.json",
    }
    if not required_model.issubset(files) or any(files[path]["role"] != "model_asset" for path in required_model):
        _fail("VOICE_PACK_PRIVATE_MODEL_INVALID", "private pack does not contain the exact trained model closure")
    if total > _QWEN_CUSTOM_PRIVATE_MAX_PACK_BYTES or roles.get("checkpoint_receipt") != 3 or roles.get("rights_evidence", 0) < 1 or roles.get("qa_evidence", 0) < 1:
        _fail("VOICE_PACK_PRIVATE_FILES_INVALID", "private pack role counts or size are invalid")
    for field, expected_role in (("rights.evidence", "rights_evidence"), ("qa.evidence", "qa_evidence")):
        raw_evidence = rights["evidence"] if field.startswith("rights") else qa["evidence"]
        if not isinstance(raw_evidence, list) or not raw_evidence:
            _fail("VOICE_PACK_PRIVATE_EVIDENCE_INVALID", f"{field} must be non-empty")
        for index, raw_path in enumerate(raw_evidence):
            key = _safe_relative(raw_path, f"{field}[{index}]").as_posix()
            if key not in files or files[key]["role"] != expected_role:
                _fail("VOICE_PACK_PRIVATE_EVIDENCE_INVALID", f"{field} is not bound to a declared file")
    if verify_files:
        if payload_root is None:
            _fail("VOICE_PACK_INVALID", "payload_root is required for private file verification", status_code=500)
        root = payload_root.resolve(strict=True)
        _assert_no_reparse(root, root)
        for key, item in files.items():
            path = _join_relative(root, PurePosixPath(key))
            _assert_no_reparse(path, root)
            if not path.is_file() or int(path.stat().st_nlink) != 1:
                _fail("VOICE_PACK_FILE_MISSING", f"private pack file is missing or not plain: {key}")
            if path.stat().st_size != item["bytes"] or _hash_file(path) != item["sha256"]:
                _fail("VOICE_PACK_FILE_HASH_MISMATCH", f"private pack file differs from signed manifest: {key}")
        config = _read_json(root / "model" / "config.json")
        talker = config.get("talker_config") if isinstance(config, Mapping) else None
        if not isinstance(talker, Mapping) or talker.get("spk_id", {}).get("tsubaki_teacher") != 3000 or talker.get("spk_is_dialect", {}).get("tsubaki_teacher") is not False:
            _fail("VOICE_PACK_PRIVATE_MODEL_INVALID", "installed model speaker slot/config is invalid")
    return value


def validate_voice_pack_manifest(
    payload: Any,
    *,
    payload_root: Path | None = None,
    verify_files: bool = False,
) -> dict[str, Any]:
    manifest = _mapping(payload, "manifest")
    if manifest.get("schema") != PACK_SCHEMA:
        _fail("VOICE_PACK_INVALID", f"manifest schema must be {PACK_SCHEMA}")
    _safe_id(manifest.get("pack_id"), "pack_id")
    _safe_id(manifest.get("version"), "version")

    runtime = _mapping(manifest.get("runtime"), "runtime")
    adapter = _safe_id(runtime.get("adapter"), "runtime.adapter")
    if adapter not in _RUNTIME_ADAPTERS:
        _fail("VOICE_PACK_ADAPTER_UNTRUSTED", "runtime adapter is not built into this application")
    if adapter == QWEN_CUSTOM_PRIVATE_ADAPTER_ID:
        return _validate_qwen_custom_private_manifest(
            manifest,
            payload_root=payload_root,
            verify_files=verify_files,
        )

    is_private_gpt = adapter == GPT_SOVITS_ADAPTER_ID and "private_prerelease" in manifest
    qa: dict[str, Any] | None = None
    if is_private_gpt:
        private = _strict_object(
            manifest.get("private_prerelease"),
            "private_prerelease",
            {"scope", "exportable", "public_release", "commercial_use", "default_eligible"},
        )
        if private.get("scope") != PRIVATE_LOCAL_SCOPE or private.get("exportable") is not False or private.get("public_release") is not False or private.get("commercial_use") is not False or private.get("default_eligible") is not True:
            _fail("VOICE_PACK_PRIVATE_SCOPE_INVALID", "private GPT-SoVITS scope flags are invalid")
        identity = _strict_object(
            manifest.get("identity"),
            "identity",
            {"kind", "display_name", "impersonates", "source_identity"},
        )
        if identity.get("kind") != "declared_third_party_character_voice" or identity.get("impersonates") is not True or not str(identity.get("source_identity") or "").strip() or not str(identity.get("display_name") or "").strip():
            _fail("VOICE_PACK_PRIVATE_IDENTITY_INVALID", "private GPT-SoVITS identity declaration is invalid")
        rights = _strict_object(manifest.get("rights"), "rights", {"status", "scope", "grants", "evidence"})
        grants = _strict_object(
            rights.get("grants"),
            "rights.grants",
            {"training", "private_local_inference", "weight_distribution", "reference_audio_distribution", "commercial_synthesis", "public_release"},
        )
        if rights.get("status") != "restricted" or rights.get("scope") != "private_local_only" or grants != {
            "training": True,
            "private_local_inference": True,
            "weight_distribution": False,
            "reference_audio_distribution": False,
            "commercial_synthesis": False,
            "public_release": False,
        }:
            _fail("VOICE_PACK_PRIVATE_RIGHTS_INVALID", "private GPT-SoVITS rights must remain non-exportable")
        qa = _strict_object(
            manifest.get("qa"),
            "qa",
            {"status", "checkpoint_integrity_verified", "audio_format_verified", "human_listening_review", "evidence"},
        )
        if qa.get("status") != "technical_pass_human_review_pending" or qa.get("checkpoint_integrity_verified") is not True or qa.get("audio_format_verified") is not True or qa.get("human_listening_review") != "pending":
            _fail("VOICE_PACK_PRIVATE_QA_INVALID", "private GPT-SoVITS QA declaration is invalid")
    else:
        identity = _mapping(manifest.get("identity"), "identity")
        if identity.get("kind") != "original":
            _fail("VOICE_PACK_IDENTITY_FORBIDDEN", "identity.kind must be original")
        if identity.get("impersonates") is not False or identity.get("source_identity") not in {None, ""}:
            _fail("VOICE_PACK_IDENTITY_FORBIDDEN", "voice packs cannot impersonate an existing identity")
        if not str(identity.get("display_name") or "").strip():
            _fail("VOICE_PACK_INVALID", "identity.display_name is required")

        rights = _mapping(manifest.get("rights"), "rights")
        if rights.get("status") != "verified":
            _fail("VOICE_PACK_RIGHTS_INCOMPLETE", "rights.status must be verified")
        covers = rights.get("covers")
        if not isinstance(covers, list) or not REQUIRED_RIGHTS.issubset({str(item) for item in covers}):
            _fail(
                "VOICE_PACK_RIGHTS_INCOMPLETE",
                "rights must cover training, weight distribution, reference-audio distribution, and commercial synthesis",
            )

    if adapter == QWEN_BASE_CLONE_ADAPTER_ID:
        manifest = _strict_object(manifest, "manifest", _QWEN_MANIFEST_FIELDS)
        identity = _strict_object(
            identity,
            "identity",
            _QWEN_IDENTITY_FIELDS,
        )
        rights = _strict_object(rights, "rights", _QWEN_RIGHTS_FIELDS)
        runtime = _strict_object(runtime, "runtime", _QWEN_RUNTIME_FIELDS)
        _qwen_engine_dependency_values(runtime)

    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        _fail("VOICE_PACK_INVALID", "files must be a non-empty list")
    files: dict[str, dict[str, Any]] = {}
    casefolded_files: set[str] = set()
    total_bytes = 0
    for index, raw in enumerate(raw_files):
        item = _mapping(raw, f"files[{index}]")
        if adapter == QWEN_BASE_CLONE_ADAPTER_ID:
            item = _strict_object(
                item,
                f"files[{index}]",
                _QWEN_FILE_FIELDS,
            )
        relative = _safe_relative(item.get("path"), f"files[{index}].path")
        key = relative.as_posix()
        if key in files or key.casefold() in casefolded_files:
            _fail("VOICE_PACK_INVALID", f"duplicate pack file: {key}")
        size = item.get("bytes")
        if not isinstance(size, int) or size < 0:
            _fail("VOICE_PACK_INVALID", f"files[{index}].bytes must be a non-negative integer")
        role = _safe_id(item.get("role"), f"files[{index}].role")
        files[key] = {**item, "path": key, "bytes": size, "sha256": _sha256(item.get("sha256"), f"files[{index}].sha256"), "role": role}
        casefolded_files.add(key.casefold())
        total_bytes += size
    maximum = int(os.getenv("MENTORFLOW_VOICE_PACK_MAX_UNPACKED_BYTES") or DEFAULT_MAX_UNPACKED_BYTES)
    if total_bytes > maximum:
        _fail("VOICE_PACK_TOO_LARGE", "declared unpacked pack size exceeds the configured limit")
    role_counts: dict[str, int] = {}
    for item in files.values():
        role_counts[item["role"]] = role_counts.get(item["role"], 0) + 1
    if adapter == GPT_SOVITS_ADAPTER_ID:
        for role in ("gpt_weight", "sovits_weight"):
            if role_counts.get(role) != 1:
                _fail(
                    "VOICE_PACK_MODELS_INVALID",
                    f"GPT-SoVITS packs must declare exactly one {role} file",
                )
        if is_private_gpt and (role_counts.get("rights_evidence", 0) < 1 or role_counts.get("qa_evidence", 0) < 1):
            _fail(
                "VOICE_PACK_PRIVATE_FILES_INVALID",
                "private GPT-SoVITS packs must include rights and QA evidence",
            )
    elif adapter == QWEN_BASE_CLONE_ADAPTER_ID:
        unexpected_roles = sorted(set(role_counts) - _QWEN_ALLOWED_FILE_ROLES)
        if unexpected_roles:
            _fail(
                "VOICE_PACK_MODELS_FORBIDDEN",
                "Qwen reference-conditioned voice packs cannot bundle shared engine/model files",
                details={"unexpected_roles": unexpected_roles},
            )
        if total_bytes > _QWEN_MAX_PACK_BYTES:
            _fail(
                "VOICE_PACK_MODELS_FORBIDDEN",
                "Qwen reference-conditioned voice pack is too large for a data-only pack",
                details={
                    "declared_bytes": total_bytes,
                    "maximum_bytes": _QWEN_MAX_PACK_BYTES,
                },
            )
        for key, item in files.items():
            expected_parent, allowed_suffixes, maximum_bytes = _QWEN_FILE_POLICIES[
                item["role"]
            ]
            path = PurePosixPath(key)
            if (
                not path.parts
                or path.parts[0] != expected_parent
                or path.suffix.casefold() not in allowed_suffixes
                or item["bytes"] > maximum_bytes
            ):
                _fail(
                    "VOICE_PACK_FILE_POLICY_INVALID",
                    f"Qwen {item['role']} file violates the data-only pack policy: {key}",
                )
        if role_counts.get("reference_audio") != 2:
            _fail(
                "VOICE_PACK_STYLES_INVALID",
                "Qwen reference-conditioned packs must contain exactly two style references",
            )
        if role_counts.get("qa_evidence", 0) < 1:
            _fail(
                "VOICE_PACK_QA_INCOMPLETE",
                "Qwen reference-conditioned packs must include QA evidence",
            )

    evidence = rights.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        _fail("VOICE_PACK_RIGHTS_INCOMPLETE", "rights.evidence must list signed-pack evidence files")
    for index, value in enumerate(evidence):
        key = _safe_relative(value, f"rights.evidence[{index}]").as_posix()
        if key not in files or files[key]["role"] != "rights_evidence":
            _fail("VOICE_PACK_RIGHTS_INCOMPLETE", f"rights evidence is not declared as a payload file: {key}")

    if is_private_gpt:
        assert qa is not None
        qa_evidence = qa.get("evidence")
        if not isinstance(qa_evidence, list) or not qa_evidence:
            _fail("VOICE_PACK_QA_INCOMPLETE", "private GPT-SoVITS qa.evidence must list signed-pack QA files")
        for index, value in enumerate(qa_evidence):
            key = _safe_relative(value, f"qa.evidence[{index}]").as_posix()
            if key not in files or files[key]["role"] != "qa_evidence":
                _fail("VOICE_PACK_QA_INCOMPLETE", f"QA evidence is not declared as a payload file: {key}")

    if adapter == QWEN_BASE_CLONE_ADAPTER_ID:
        qa = _strict_object(manifest.get("qa"), "qa", _QWEN_QA_FIELDS)
        if qa.get("status") != "passed":
            _fail("VOICE_PACK_QA_INCOMPLETE", "qa.status must be passed")
        for flag in (
            "transcript_alignment_verified",
            "reference_audio_reviewed",
            "original_identity_reviewed",
        ):
            if qa.get(flag) is not True:
                _fail("VOICE_PACK_QA_INCOMPLETE", f"qa.{flag} must be true")
        qa_evidence = qa.get("evidence")
        if not isinstance(qa_evidence, list) or not qa_evidence:
            _fail("VOICE_PACK_QA_INCOMPLETE", "qa.evidence must list signed-pack QA files")
        for index, value in enumerate(qa_evidence):
            key = _safe_relative(value, f"qa.evidence[{index}]").as_posix()
            if key not in files or files[key]["role"] != "qa_evidence":
                _fail(
                    "VOICE_PACK_QA_INCOMPLETE",
                    f"QA evidence is not declared as a payload file: {key}",
                )

    styles = _mapping(manifest.get("styles"), "styles")
    if adapter == QWEN_BASE_CLONE_ADAPTER_ID:
        styles = _strict_object(styles, "styles", _QWEN_STYLES_FIELDS)
    if set(styles) != REQUIRED_STYLES:
        _fail("VOICE_PACK_STYLES_INVALID", "styles must contain exactly lively and relaxed")
    style_references: set[str] = set()
    style_reference_records: list[dict[str, Any]] = []
    for style_name in sorted(REQUIRED_STYLES):
        style = _mapping(styles[style_name], f"styles.{style_name}")
        if adapter == QWEN_BASE_CLONE_ADAPTER_ID:
            style = _strict_object(
                style,
                f"styles.{style_name}",
                _QWEN_STYLE_FIELDS,
            )
        reference = _safe_relative(style.get("reference_audio"), f"styles.{style_name}.reference_audio").as_posix()
        if reference not in files or files[reference]["role"] != "reference_audio":
            _fail("VOICE_PACK_STYLES_INVALID", f"{style_name} reference audio is not declared")
        if not str(style.get("transcript") or "").strip() or not str(style.get("language") or "").strip():
            _fail("VOICE_PACK_STYLES_INVALID", f"{style_name} transcript and language are required")
        if is_private_gpt and not str(style.get("target_language") or "").strip():
            _fail("VOICE_PACK_STYLES_INVALID", f"{style_name} target_language is required for private GPT-SoVITS packs")
        if adapter == QWEN_BASE_CLONE_ADAPTER_ID:
            if reference in style_references:
                _fail(
                    "VOICE_PACK_STYLES_INVALID",
                    "Qwen lively and relaxed styles must use distinct reference files",
                )
            if PurePosixPath(reference).suffix.casefold() != ".wav":
                _fail("VOICE_PACK_STYLES_INVALID", "Qwen reference audio must be WAV")
            if str(style.get("language") or "").strip().casefold() != "zh":
                _fail("VOICE_PACK_STYLES_INVALID", "Qwen reference transcript language must be zh")
            style_references.add(reference)
            style_reference_records.append(files[reference])

    if adapter == QWEN_BASE_CLONE_ADAPTER_ID:
        if len({item["sha256"] for item in style_reference_records}) != 2:
            _fail(
                "VOICE_PACK_STYLES_INVALID",
                "Qwen lively and relaxed references must have distinct SHA-256 digests",
            )
        if len({item["bytes"] for item in style_reference_records}) != 2:
            _fail(
                "VOICE_PACK_STYLES_INVALID",
                "Qwen lively and relaxed references must have distinct declared byte lengths",
            )

    if verify_files:
        if payload_root is None:
            _fail("VOICE_PACK_INVALID", "payload_root is required for file verification", status_code=500)
        root = payload_root.resolve(strict=True)
        _assert_no_reparse(root, root)
        for key, item in files.items():
            path = _join_relative(root, PurePosixPath(key))
            _assert_no_reparse(path, root)
            if not path.is_file():
                _fail("VOICE_PACK_FILE_MISSING", f"pack file is missing: {key}")
            if path.stat().st_size != item["bytes"]:
                _fail("VOICE_PACK_FILE_SIZE_MISMATCH", f"pack file size mismatch: {key}")
            if _hash_file(path) != item["sha256"]:
                _fail("VOICE_PACK_FILE_HASH_MISMATCH", f"pack file hash mismatch: {key}")
            if adapter == QWEN_BASE_CLONE_ADAPTER_ID and item["role"] == "reference_audio":
                _validate_pcm_wav_path(path, f"Qwen reference audio {key}")
    return manifest


def _verified_declared_file(
    item: Mapping[str, Any],
    installed_root: Path,
    *,
    field: str,
) -> Path:
    path = _join_relative(
        installed_root,
        _safe_relative(item.get("path"), f"{field}.path"),
    )
    _assert_no_reparse(path, installed_root)
    if not path.is_file():
        _fail("VOICE_PACK_FILE_MISSING", f"active pack file is missing: {item.get('path')}", status_code=409)
    if path.stat().st_size != item.get("bytes") or _hash_file(path) != item.get("sha256"):
        _fail("VOICE_PACK_FILE_HASH_MISMATCH", f"active pack file integrity failed: {item.get('path')}", status_code=409)
    return path.resolve(strict=True)


def _verified_file_for_role(
    manifest: Mapping[str, Any],
    installed_root: Path,
    role: str,
) -> Path:
    matches = [
        _mapping(item, "files[]")
        for item in manifest.get("files", [])
        if isinstance(item, Mapping) and item.get("role") == role
    ]
    if len(matches) != 1:
        _fail("VOICE_PACK_MODELS_INVALID", f"active pack must contain exactly one {role}")
    return _verified_declared_file(matches[0], installed_root, field=role)


def _parse_signature(raw: bytes, expected_key_id: str) -> bytes:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail("VOICE_PACK_SIGNATURE_INVALID", "voice-pack.sig is invalid JSON", details={"error": str(exc)})
    signature = _mapping(payload, "voice-pack.sig")
    if signature.get("algorithm") != "ed25519" or signature.get("key_id") != expected_key_id:
        _fail("VOICE_PACK_SIGNATURE_INVALID", "signature algorithm or key id does not match the catalog")
    return _decode_key(signature.get("signature"), "signature", 64)


def _verify_manifest_signature(
    raw_manifest: bytes,
    raw_signature: bytes,
    entry: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> None:
    key_id = _safe_id(entry.get("signing_key_id"), "signing_key_id")
    public_key = _trusted_keys(catalog).get(key_id)
    if public_key is None:
        _fail("VOICE_PACK_SIGNING_KEY_UNKNOWN", "catalog signing key is not trusted")
    signature = _parse_signature(raw_signature, key_id)
    if not verify_ed25519(public_key, raw_manifest, signature):
        _fail("VOICE_PACK_SIGNATURE_INVALID", "Ed25519 signature verification failed")


def _job_path(root: Path, job_id: str) -> Path:
    return root / ".state" / "jobs" / f"{_safe_id(job_id, 'job_id')}.json"


def _create_job(
    root: Path,
    action: str,
    pack_id: str,
    version: str,
    *,
    initial_status: str = "not_installed",
) -> dict[str, Any]:
    if initial_status not in JOB_STATES:
        _fail("VOICE_PACK_JOB_INVALID", f"unsupported initial job status: {initial_status}", status_code=500)
    job = {
        "job_id": uuid4().hex,
        "action": action,
        "pack_id": pack_id,
        "version": version,
        "status": initial_status,
        "created_at": _now(),
        "updated_at": _now(),
        "history": [{"status": initial_status, "at": _now()}],
        "error": None,
    }
    _write_json_atomic(_job_path(root, job["job_id"]), job, boundary=root)
    return job


def _update_job(root: Path, job: dict[str, Any], status: str, *, error: TeacherVoicePackError | None = None) -> dict[str, Any]:
    if status not in JOB_STATES:
        _fail("VOICE_PACK_JOB_INVALID", f"unsupported job status: {status}", status_code=500)
    updated = dict(job)
    updated["status"] = status
    updated["updated_at"] = _now()
    updated["history"] = [*list(job.get("history") or []), {"status": status, "at": _now()}]
    updated["error"] = (
        {"code": error.code, "message": error.message, "details": error.details} if error else None
    )
    _write_json_atomic(_job_path(root, updated["job_id"]), updated, boundary=root)
    return updated


def get_job(job_id: str) -> dict[str, Any]:
    root = _pack_root()
    path = _job_path(root, job_id)
    _assert_no_reparse(path, root)
    job = _read_json(path)
    if not isinstance(job, Mapping):
        _fail("VOICE_PACK_JOB_NOT_FOUND", "voice pack job was not found", status_code=404)
    return dict(job)


def _copy_catalog_archive(
    entry: Mapping[str, Any],
    catalog_path: Path,
    catalog: Mapping[str, Any],
    destination: Path,
) -> tuple[int, str]:
    source = _mapping(entry.get("source"), "source")
    expected = _mapping(entry.get("archive"), "archive")
    expected_bytes = int(expected["bytes"])
    digest = hashlib.sha256()
    written = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    kind = source.get("kind")
    if kind == "https" and str(os.getenv("MENTORFLOW_PRIVATE_VOICE_PACK_MODE") or "").strip() == "1":
        _fail("VOICE_PACK_PRIVATE_SOURCE_FORBIDDEN", "private voice mode forbids HTTPS catalog sources")
    if kind == "bundled":
        relative = _safe_relative(source.get("archive_path"), "source.archive_path")
        if not relative.parts or relative.parts[0] != "bundled":
            _fail("VOICE_PACK_SOURCE_FORBIDDEN", "bundled archives must live under the catalog bundled directory")
        source_path = _join_relative(catalog_path.parent, relative)
        _assert_no_reparse(source_path, catalog_path.parent)
        if not source_path.is_file():
            _fail("VOICE_PACK_SOURCE_MISSING", "bundled voice pack archive is missing", status_code=503)
        stream = source_path.open("rb")
    else:
        url = str(source.get("url") or "").strip()
        allowed_hosts = _https_allowed_hosts(catalog)
        stream = _open_catalog_https(url, allowed_hosts)
    try:
        with destination.open("xb") as output:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > expected_bytes:
                    _fail("VOICE_PACK_ARCHIVE_SIZE_MISMATCH", "archive exceeds its catalog size")
                digest.update(chunk)
                output.write(chunk)
    finally:
        stream.close()
    if written != expected_bytes:
        _fail("VOICE_PACK_ARCHIVE_SIZE_MISMATCH", "archive size does not match the catalog")
    actual_hash = digest.hexdigest()
    if actual_hash != _sha256(expected.get("sha256"), "archive.sha256"):
        _fail("VOICE_PACK_ARCHIVE_HASH_MISMATCH", "archive SHA-256 does not match the catalog")
    return written, actual_hash


def _zip_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries: dict[str, zipfile.ZipInfo] = {}
    casefolded: set[str] = set()
    for info in archive.infolist():
        relative = _safe_relative(info.filename.rstrip("/"), "archive member")
        key = relative.as_posix()
        if key in entries or key.casefold() in casefolded:
            _fail("VOICE_PACK_ARCHIVE_INVALID", f"duplicate archive member: {key}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            _fail("VOICE_PACK_SYMLINK_FORBIDDEN", f"archive symlink is forbidden: {key}")
        if not info.is_dir():
            entries[key] = info
            casefolded.add(key.casefold())
    return entries


def _extract_verified_archive(
    archive_path: Path,
    payload_root: Path,
    entry: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        _fail("VOICE_PACK_ARCHIVE_INVALID", "voice pack archive is not a valid ZIP", details={"error": str(exc)})
    with archive:
        entries = _zip_entries(archive)
        if "voice-pack.json" not in entries or "voice-pack.sig" not in entries:
            _fail("VOICE_PACK_ARCHIVE_INVALID", "archive must contain voice-pack.json and voice-pack.sig")
        if entries["voice-pack.json"].file_size > MAX_MANIFEST_BYTES:
            _fail("VOICE_PACK_ARCHIVE_INVALID", "voice-pack.json is too large")
        raw_manifest = archive.read(entries["voice-pack.json"])
        raw_signature = archive.read(entries["voice-pack.sig"])
        _verify_manifest_signature(raw_manifest, raw_signature, entry, catalog)
        try:
            parsed_manifest = json.loads(raw_manifest.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            _fail("VOICE_PACK_INVALID", "voice-pack.json is invalid JSON", details={"error": str(exc)})
        manifest = validate_voice_pack_manifest(parsed_manifest)
        if manifest.get("pack_id") != entry.get("pack_id") or manifest.get("version") != entry.get("version"):
            _fail("VOICE_PACK_CATALOG_MISMATCH", "signed manifest identity does not match the catalog")
        declared = {str(item["path"]): item for item in manifest["files"]}
        actual_payload = set(entries) - {"voice-pack.json", "voice-pack.sig"}
        if actual_payload != set(declared):
            _fail(
                "VOICE_PACK_ARCHIVE_INVALID",
                "archive payload must exactly match manifest.files",
                details={"missing": sorted(set(declared) - actual_payload), "unexpected": sorted(actual_payload - set(declared))},
            )
        payload_root.mkdir(parents=True, exist_ok=False)
        (payload_root / "voice-pack.json").write_bytes(raw_manifest)
        (payload_root / "voice-pack.sig").write_bytes(raw_signature)
        for key, item in declared.items():
            info = entries[key]
            if info.file_size != item["bytes"]:
                _fail("VOICE_PACK_FILE_SIZE_MISMATCH", f"archive member size mismatch: {key}")
            destination = _join_relative(payload_root, PurePosixPath(key))
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            with archive.open(info, "r") as source, destination.open("xb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > item["bytes"]:
                        _fail("VOICE_PACK_FILE_SIZE_MISMATCH", f"archive member exceeds declared size: {key}")
                    digest.update(chunk)
                    output.write(chunk)
            if written != item["bytes"] or digest.hexdigest() != item["sha256"]:
                _fail("VOICE_PACK_FILE_HASH_MISMATCH", f"archive member hash mismatch: {key}")
        validate_voice_pack_manifest(manifest, payload_root=payload_root, verify_files=True)
        return manifest, hashlib.sha256(raw_manifest).hexdigest()


def _atomic_promote(staged_payload: Path, final_path: Path, root: Path) -> None:
    _assert_no_reparse(staged_payload, root)
    _ensure_secure_directory(root, final_path.parent)
    _assert_no_reparse(final_path, root)
    if final_path.exists() or final_path.is_symlink():
        _fail("VOICE_PACK_VERSION_EXISTS", "installed voice pack versions are immutable", status_code=409)
    os.replace(staged_payload, final_path)
    _assert_no_reparse(final_path, root)


def _remove_staging_tree(root: Path, staging: Path) -> None:
    expected_parent = root / ".staging"
    if staging.parent != expected_parent or not staging.name:
        _fail("VOICE_PACK_PATH_INVALID", "refusing to remove an unexpected staging path", status_code=500)
    _assert_no_reparse(staging, root)
    shutil.rmtree(staging)


def install_voice_pack(pack_id: str, version: str | None = None) -> dict[str, Any]:
    catalog_path, catalog = _load_catalog()
    entry = _catalog_entry(catalog, pack_id, version)
    root = _pack_root()
    final_path = root / entry["pack_id"] / entry["version"]
    job: dict[str, Any] | None = None
    staging: Path | None = None
    with _STATE_LOCK:
        job = _create_job(root, "install", entry["pack_id"], entry["version"])
        try:
            if final_path.exists() or final_path.is_symlink():
                _fail("VOICE_PACK_VERSION_EXISTS", "installed voice pack versions are immutable", status_code=409)
            staging_parent = root / ".staging"
            _ensure_secure_directory(root, staging_parent)
            _assert_no_reparse(staging_parent, root)
            staging = staging_parent / job["job_id"]
            if staging.exists() or _is_reparse_point(staging):
                _fail("VOICE_PACK_REPARSE_FORBIDDEN", "staging job path already exists")
            staging.mkdir()
            _assert_no_reparse(staging, root)
            job = _update_job(root, job, "staging")
            archive_path = staging / "archive.zip"
            archive_bytes, archive_hash = _copy_catalog_archive(
                entry,
                catalog_path,
                catalog,
                archive_path,
            )
            payload_root = staging / "payload"
            manifest, manifest_hash = _extract_verified_archive(archive_path, payload_root, entry, catalog)
            receipt = {
                "schema": "mentorflow.voice-pack-install-receipt.v1",
                "pack_id": entry["pack_id"],
                "version": entry["version"],
                "archive_bytes": archive_bytes,
                "archive_sha256": archive_hash,
                "manifest_sha256": manifest_hash,
                "verified_at": _now(),
            }
            _write_json_atomic(
                payload_root / ".mentorflow-install.json",
                receipt,
                boundary=payload_root,
            )
            job = _update_job(root, job, "verified")
            _atomic_promote(payload_root, final_path, root)
            job = _update_job(root, job, "installed")
            return {"job": job, "pack": _public_pack(manifest, state="installed")}
        except TeacherVoicePackError as exc:
            if job is not None:
                exc.details.setdefault("job_id", job["job_id"])
                job = _update_job(root, job, "failed", error=exc)
            raise
        except Exception as exc:
            wrapped = TeacherVoicePackError(
                "VOICE_PACK_INSTALL_FAILED",
                "voice pack installation failed",
                status_code=500,
                details={
                    "error": f"{type(exc).__name__}:{exc}",
                    "job_id": job["job_id"] if job is not None else None,
                },
            )
            if job is not None:
                _update_job(root, job, "failed", error=wrapped)
            raise wrapped from exc
        finally:
            if staging is not None and staging.exists():
                _remove_staging_tree(root, staging)


def _installed_path(root: Path, pack_id: str, version: str) -> Path:
    return root / _safe_id(pack_id, "pack_id") / _safe_id(version, "version")


def _load_installed(
    root: Path,
    entry: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    verify_files: bool,
) -> dict[str, Any]:
    path = _installed_path(root, str(entry["pack_id"]), str(entry["version"]))
    _assert_no_reparse(path, root)
    if not path.is_dir():
        _fail("VOICE_PACK_NOT_INSTALLED", "voice pack version is not installed", status_code=404)
    return _load_pack_directory(path, entry, catalog, verify_files=verify_files)


def _load_pack_directory(
    path: Path,
    entry: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    verify_files: bool,
) -> dict[str, Any]:
    _assert_no_reparse(path, path)
    manifest_path, signature_path = path / "voice-pack.json", path / "voice-pack.sig"
    _assert_no_reparse(manifest_path, path)
    _assert_no_reparse(signature_path, path)
    if not manifest_path.is_file() or not signature_path.is_file():
        _fail("VOICE_PACK_INSTALL_CORRUPT", "installed voice pack metadata is missing", status_code=409)
    raw_manifest, raw_signature = manifest_path.read_bytes(), signature_path.read_bytes()
    _verify_manifest_signature(raw_manifest, raw_signature, entry, catalog)
    try:
        payload = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail("VOICE_PACK_INSTALL_CORRUPT", "installed voice pack manifest is invalid", details={"error": str(exc)})
    manifest = validate_voice_pack_manifest(payload, payload_root=path, verify_files=verify_files)
    if manifest.get("pack_id") != entry.get("pack_id") or manifest.get("version") != entry.get("version"):
        _fail("VOICE_PACK_CATALOG_MISMATCH", "installed voice pack no longer matches the catalog")
    adapter_id = _mapping(manifest.get("runtime"), "runtime").get("adapter")
    if adapter_id == QWEN_CUSTOM_PRIVATE_ADAPTER_ID and (
        entry.get("runtime_adapter") != QWEN_CUSTOM_PRIVATE_ADAPTER_ID
        or entry.get("scope") != "private_local_only_nonexportable"
        or entry.get("usage_scope") != "local_private"
        or entry.get("release_channel") != "prerelease"
        or entry.get("default") is not True
        or _mapping(entry.get("source"), "source").get("kind") != "bundled"
    ):
        _fail("VOICE_PACK_PRIVATE_CATALOG_INVALID", "private custom manifest/catalog binding is invalid")
    return manifest


def _mark_failed_job(
    root: Path,
    job: dict[str, Any],
    error: TeacherVoicePackError,
) -> None:
    error.details.setdefault("job_id", job["job_id"])
    try:
        _update_job(root, job, "failed", error=error)
    except Exception as persist_error:
        error.details["failed_job_persist_error"] = (
            f"{type(persist_error).__name__}:{persist_error}"
        )


def _unexpected_operation_error(
    code: str,
    message: str,
    exc: Exception,
    job: dict[str, Any],
) -> TeacherVoicePackError:
    return TeacherVoicePackError(
        code,
        message,
        status_code=500,
        details={
            "error": f"{type(exc).__name__}:{exc}",
            "job_id": job["job_id"],
        },
    )


def _restore_active_reference(root: Path, previous: Mapping[str, Any] | None) -> None:
    path = _active_path(root)
    _assert_no_reparse(path, root)
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        _write_json_atomic(path, dict(previous), boundary=root)


def _record_delete_cleanup_failure(
    root: Path,
    job: dict[str, Any],
    quarantine: Path,
    exc: Exception,
) -> dict[str, Any]:
    updated = dict(job)
    updated["updated_at"] = _now()
    updated["history"] = [
        *list(job.get("history") or []),
        {
            "status": "not_installed",
            "event": "cleanup_failed",
            "at": _now(),
        },
    ]
    updated["cleanup"] = {
        "status": "failed",
        "quarantine": quarantine.relative_to(root).as_posix(),
        "error": f"{type(exc).__name__}:{exc}",
    }
    try:
        _write_json_atomic(_job_path(root, updated["job_id"]), updated, boundary=root)
    except Exception as persist_error:
        _fail(
            "VOICE_PACK_DELETE_CLEANUP_STATE_FAILED",
            "voice pack remains quarantined after cleanup failed, but the cleanup failure could not be persisted",
            status_code=500,
            details={
                "job_id": updated["job_id"],
                "quarantine": quarantine.relative_to(root).as_posix(),
                "cleanup_error": f"{type(exc).__name__}:{exc}",
                "persistence_error": (
                    f"{type(persist_error).__name__}:{persist_error}"
                ),
            },
        )
    return updated


def _record_delete_cleanup_success(
    root: Path,
    job: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(job)
    updated["updated_at"] = _now()
    updated["history"] = [
        *list(job.get("history") or []),
        {
            "status": "not_installed",
            "event": "cleanup_removed",
            "at": _now(),
        },
    ]
    updated["cleanup"] = {"status": "removed"}
    _write_json_atomic(_job_path(root, updated["job_id"]), updated, boundary=root)
    return updated


def _active_path(root: Path) -> Path:
    return root / ".state" / "active.json"


def _active_reference(root: Path) -> dict[str, Any] | None:
    path = _active_path(root)
    _assert_no_reparse(path, root)
    if not path.exists():
        return None
    if not path.is_file():
        _fail(
            "VOICE_PACK_ACTIVE_STATE_INVALID",
            "active voice pack state is not a regular file",
            status_code=500,
        )
    value = _read_json(path)
    if not isinstance(value, Mapping):
        _fail(
            "VOICE_PACK_ACTIVE_STATE_INVALID",
            "active voice pack state must be an object",
            status_code=500,
        )
    if value.get("schema") != "mentorflow.active-voice-pack.v1":
        _fail(
            "VOICE_PACK_ACTIVE_STATE_INVALID",
            "active voice pack state schema is invalid",
            status_code=500,
        )
    try:
        return {
            **dict(value),
            "pack_id": _safe_id(value.get("pack_id"), "active.pack_id"),
            "version": _safe_id(value.get("version"), "active.version"),
            "manifest_sha256": _sha256(
                value.get("manifest_sha256"), "active.manifest_sha256"
            ),
        }
    except TeacherVoicePackError as exc:
        raise TeacherVoicePackError(
            "VOICE_PACK_ACTIVE_STATE_INVALID",
            "active voice pack state is semantically invalid",
            status_code=500,
            details={"cause": exc.code},
        ) from exc


def activate_voice_pack(pack_id: str, version: str | None = None) -> dict[str, Any]:
    _catalog_file, catalog = _load_catalog()
    entry = _catalog_entry(catalog, pack_id, version)
    root = _pack_root()
    with _STATE_LOCK:
        previous_active = _active_reference(root)
        job = _create_job(root, "activate", entry["pack_id"], entry["version"])
        active_write_attempted = False
        try:
            manifest = _load_installed(root, entry, catalog, verify_files=True)
            active = {
                "schema": "mentorflow.active-voice-pack.v1",
                "pack_id": entry["pack_id"],
                "version": entry["version"],
                "manifest_sha256": _hash_file(_installed_path(root, entry["pack_id"], entry["version"]) / "voice-pack.json"),
                "activated_at": _now(),
            }
            active_write_attempted = True
            _write_json_atomic(_active_path(root), active, boundary=root)
            job = _update_job(root, job, "active")
            return {"job": job, "pack": _public_pack(manifest, state="active")}
        except TeacherVoicePackError as exc:
            if active_write_attempted:
                try:
                    _restore_active_reference(root, previous_active)
                except Exception as rollback_error:
                    exc.details["active_rollback_error"] = (
                        f"{type(rollback_error).__name__}:{rollback_error}"
                    )
            _mark_failed_job(root, job, exc)
            raise
        except Exception as exc:
            wrapped = _unexpected_operation_error(
                "VOICE_PACK_ACTIVATE_FAILED",
                "voice pack activation failed",
                exc,
                job,
            )
            if active_write_attempted:
                try:
                    _restore_active_reference(root, previous_active)
                except Exception as rollback_error:
                    wrapped.details["active_rollback_error"] = (
                        f"{type(rollback_error).__name__}:{rollback_error}"
                    )
            _mark_failed_job(root, job, wrapped)
            raise wrapped from exc


def delete_voice_pack(pack_id: str, version: str) -> dict[str, Any]:
    _catalog_file, catalog = _load_catalog()
    entry = _catalog_entry(catalog, pack_id, version)
    root = _pack_root()
    with _STATE_LOCK:
        active = _active_reference(root)
        if active and active["pack_id"] == entry["pack_id"] and active["version"] == entry["version"]:
            _fail("VOICE_PACK_ACTIVE_DELETE_FORBIDDEN", "deactivate or activate another version before deletion", status_code=409)
        path = _installed_path(root, entry["pack_id"], entry["version"])
        _assert_no_reparse(path, root)
        if not path.is_dir():
            _fail("VOICE_PACK_NOT_INSTALLED", "voice pack version is not installed", status_code=404)
        job = _create_job(
            root,
            "delete",
            entry["pack_id"],
            entry["version"],
            initial_status="installed",
        )
        trash_parent = root / ".trash"
        quarantine = trash_parent / job["job_id"]
        moved = False
        try:
            _ensure_secure_directory(root, trash_parent)
            _assert_no_reparse(trash_parent, root)
            if quarantine.exists() or _is_reparse_point(quarantine):
                _fail("VOICE_PACK_REPARSE_FORBIDDEN", "delete quarantine path already exists")
            os.replace(path, quarantine)
            moved = True
            _assert_no_reparse(quarantine, root)
            # Persist the quarantine location as part of the logical delete
            # commit.  If recording the later cleanup outcome itself fails,
            # this durable `pending` record still leaves an operator-visible,
            # recoverable path instead of silently losing the orphan.
            job_with_cleanup = dict(job)
            job_with_cleanup["cleanup"] = {
                "status": "pending",
                "quarantine": quarantine.relative_to(root).as_posix(),
            }
            job = _update_job(root, job_with_cleanup, "not_installed")
        except TeacherVoicePackError as exc:
            if moved and not path.exists():
                try:
                    _assert_no_reparse(quarantine, root)
                    if not quarantine.is_dir():
                        _fail(
                            "VOICE_PACK_DELETE_ROLLBACK_FAILED",
                            "delete quarantine is not an intact directory",
                            status_code=500,
                        )
                    _load_pack_directory(quarantine, entry, catalog, verify_files=True)
                    os.replace(quarantine, path)
                except Exception as rollback_error:
                    exc.details["delete_rollback_error"] = (
                        f"{type(rollback_error).__name__}:{rollback_error}"
                    )
            _mark_failed_job(root, job, exc)
            raise
        except Exception as exc:
            wrapped = _unexpected_operation_error(
                "VOICE_PACK_DELETE_FAILED",
                "voice pack deletion failed",
                exc,
                job,
            )
            if moved and not path.exists():
                try:
                    _assert_no_reparse(quarantine, root)
                    if not quarantine.is_dir():
                        _fail(
                            "VOICE_PACK_DELETE_ROLLBACK_FAILED",
                            "delete quarantine is not an intact directory",
                            status_code=500,
                        )
                    _load_pack_directory(quarantine, entry, catalog, verify_files=True)
                    os.replace(quarantine, path)
                except Exception as rollback_error:
                    wrapped.details["delete_rollback_error"] = (
                        f"{type(rollback_error).__name__}:{rollback_error}"
                    )
            _mark_failed_job(root, job, wrapped)
            raise wrapped from exc

        cleanup: dict[str, Any] = {"status": "removed"}
        try:
            _assert_no_reparse(quarantine, root)
            shutil.rmtree(quarantine)
        except Exception as cleanup_error:
            job = _record_delete_cleanup_failure(
                root,
                job,
                quarantine,
                cleanup_error,
            )
            cleanup = dict(job["cleanup"])
        else:
            try:
                job = _record_delete_cleanup_success(root, job)
            except Exception as persist_error:
                # The committed job already records cleanup=pending and the
                # quarantine path.  Keep that durable recovery marker when
                # the final informational update cannot be persisted.
                cleanup["persistence_error"] = (
                    f"{type(persist_error).__name__}:{persist_error}"
                )
        return {
            "job": job,
            "pack_id": entry["pack_id"],
            "version": entry["version"],
            "status": "not_installed",
            "cleanup": cleanup,
        }


def _settings_path(root: Path) -> Path:
    return root / ".state" / "settings.json"


def get_voice_pack_settings() -> dict[str, Any]:
    root = _pack_root()
    path = _settings_path(root)
    _assert_no_reparse(path, root)
    value = _read_json(path, missing={})
    policy = str(value.get("fallback_policy") or "off") if isinstance(value, Mapping) else "off"
    if policy not in FALLBACK_POLICIES:
        policy = "off"
    return {"fallback_policy": policy}


def update_voice_pack_settings(fallback_policy: str) -> dict[str, Any]:
    policy = str(fallback_policy or "").strip()
    if policy not in FALLBACK_POLICIES:
        _fail("VOICE_PACK_SETTINGS_INVALID", "fallback_policy must be off or system_voice", status_code=422)
    payload = {"schema": "mentorflow.teacher-voice-settings.v1", "fallback_policy": policy, "updated_at": _now()}
    with _STATE_LOCK:
        root = _pack_root()
        _write_json_atomic(_settings_path(root), payload, boundary=root)
    return {"fallback_policy": policy}


def _public_pack(manifest: Mapping[str, Any], *, state: str) -> dict[str, Any]:
    identity = _mapping(manifest.get("identity"), "identity")
    private = manifest.get("private_prerelease")
    return {
        "pack_id": manifest.get("pack_id"),
        "version": manifest.get("version"),
        "display_name": identity.get("display_name"),
        "state": state,
        "styles": sorted(REQUIRED_STYLES),
        "runtime_adapter": _mapping(manifest.get("runtime"), "runtime").get("adapter"),
        "scope": private.get("scope") if isinstance(private, Mapping) else "production_release",
        "release_channel": "prerelease" if isinstance(private, Mapping) else "stable",
        "usage_scope": "local_private" if isinstance(private, Mapping) else "public",
    }


def list_voice_packs() -> list[dict[str, Any]]:
    _catalog_file, catalog = _load_catalog()
    root = _pack_root()
    active = _active_reference(root)
    result: list[dict[str, Any]] = []
    for entry in _catalog_entries(catalog):
        path = _installed_path(root, str(entry["pack_id"]), str(entry["version"]))
        _assert_no_reparse(path, root)
        state = "not_installed"
        if path.is_dir():
            state = "installed"
        if active and active["pack_id"] == entry["pack_id"] and active["version"] == entry["version"]:
            state = "active" if state == "installed" else "failed"
        result.append(
            {
                "pack_id": entry["pack_id"],
                "version": entry["version"],
                "display_name": entry.get("display_name") or entry["pack_id"],
                "archive_bytes": _mapping(entry.get("archive"), "archive").get("bytes"),
                "state": state,
                "default": entry.get("default") is True,
                "release_channel": str(entry.get("release_channel") or "stable"),
                "usage_scope": str(entry.get("usage_scope") or "public"),
            }
        )
    return result


class TeacherVoiceRuntimeAdapter(Protocol):
    def status(self, pack: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def prepare(
        self,
        pack: Mapping[str, Any],
        *,
        installed_root: Path,
        manifest_sha256: str,
    ) -> Mapping[str, Any]: ...

    async def synthesize(
        self,
        pack: Mapping[str, Any],
        *,
        text: str,
        style: str,
        reply_kind: str,
        installed_root: Path,
    ) -> "SynthesizedTeacherVoice": ...


@dataclass(frozen=True)
class SynthesizedTeacherVoice:
    audio: bytes
    media_type: str
    engine: str
    profile: str
    cache: str = "unknown"
    scope_receipt: Mapping[str, Any] | None = None


class GptSovitsLoopbackAdapter:
    """Trusted adapter: endpoint comes from operator config, never from a pack."""

    environment_key = "MENTORFLOW_VOICE_PACK_GPT_SOVITS_URL"

    def __init__(self) -> None:
        self._prepared_identity: tuple[str, str, str] | None = None

    def _url(self) -> str:
        value = str(os.getenv(self.environment_key) or "").strip().rstrip("/")
        if not value:
            return ""
        parsed = urlparse(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or not parsed.port
        ):
            _fail("VOICE_PACK_RUNTIME_CONFIG_INVALID", "trusted GPT-SoVITS URL must be a loopback origin", status_code=503)
        return value

    def status(self, pack: Mapping[str, Any]) -> Mapping[str, Any]:
        # Deliberately passive: no socket probe and no model start.
        identity = self._prepared_identity
        return {
            "state": "prepared" if identity and identity[:2] == (pack.get("pack_id"), pack.get("version")) else ("configured" if self._url() else "unavailable"),
            "starts_model": False,
            "prepared_pack": (
                {"pack_id": identity[0], "version": identity[1], "manifest_sha256": identity[2]}
                if identity
                else None
            ),
        }

    async def prepare(
        self,
        pack: Mapping[str, Any],
        *,
        installed_root: Path,
        manifest_sha256: str,
    ) -> Mapping[str, Any]:
        url = self._url()
        if not url:
            _fail("VOICE_PACK_RUNTIME_UNAVAILABLE", "active voice pack runtime is not configured", status_code=503)
        identity = (
            _safe_id(pack.get("pack_id"), "pack_id"),
            _safe_id(pack.get("version"), "version"),
            _sha256(manifest_sha256, "manifest_sha256"),
        )
        if self._prepared_identity == identity:
            return {"prepared": True, "cached": True, "identity": identity}

        # Clear the old identity before the first mutating load request.  If
        # either load fails, no subsequent synthesis can mistake a mixed model
        # state for the previously prepared pack.
        self._prepared_identity = None
        gpt_weight = _verified_file_for_role(pack, installed_root, "gpt_weight")
        sovits_weight = _verified_file_for_role(pack, installed_root, "sovits_weight")
        load_requests = (
            ("/set_gpt_weights", gpt_weight),
            ("/set_sovits_weights", sovits_weight),
        )
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=2.0),
                trust_env=False,
            ) as client:
                for endpoint, weight_path in load_requests:
                    response = await client.get(
                        f"{url}{endpoint}",
                        params={"weights_path": str(weight_path)},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, Mapping) or payload.get("message") != "success":
                        _fail(
                            "VOICE_PACK_RUNTIME_PREPARE_FAILED",
                            "GPT-SoVITS rejected a signed pack weight",
                            status_code=503,
                            details={"endpoint": endpoint},
                        )
        except TeacherVoicePackError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            _fail(
                "VOICE_PACK_RUNTIME_PREPARE_FAILED",
                "GPT-SoVITS could not load the active pack weights",
                status_code=503,
                details={"error": f"{type(exc).__name__}:{exc}"},
            )
        self._prepared_identity = identity
        return {"prepared": True, "cached": False, "identity": identity}

    def _tts_payload(
        self,
        pack: Mapping[str, Any],
        *,
        text: str,
        style: str,
        installed_root: Path,
    ) -> dict[str, Any]:
        style_config = _mapping(_mapping(pack.get("styles"), "styles").get(style), f"styles.{style}")
        reference_key = _safe_relative(
            style_config.get("reference_audio"),
            "reference_audio",
        ).as_posix()
        reference_entries = [
            _mapping(item, "files[]")
            for item in pack.get("files", [])
            if isinstance(item, Mapping)
            and item.get("path") == reference_key
            and item.get("role") == "reference_audio"
        ]
        if len(reference_entries) != 1:
            _fail(
                "VOICE_PACK_STYLES_INVALID",
                f"{style} reference audio is not declared",
            )
        reference = _verified_declared_file(
            reference_entries[0],
            installed_root,
            field=f"styles.{style}.reference_audio",
        )
        return {
            "text": text,
            "text_lang": str(style_config.get("target_language") or style_config.get("language") or "zh"),
            "ref_audio_path": str(reference),
            "prompt_lang": str(style_config.get("language")),
            "prompt_text": str(style_config.get("transcript")),
            # Keep MentorFlow's real path aligned with the B1.02 audition
            # profile.  Omitting these fields silently selects GPT-SoVITS'
            # higher-variance defaults and can reintroduce breath-like tails.
            "top_k": 20,
            "top_p": 0.75,
            "temperature": 0.70,
            "repetition_penalty": 1.25,
            "media_type": "wav",
            "text_split_method": "cut5",
            "batch_size": 1,
            "split_bucket": False,
            "parallel_infer": True,
            "speed_factor": 1.0,
            # Do not append the runtime's default 300 ms tail to every
            # sentence fragment. Punctuation already supplies the pause and
            # the extra tail is perceived as an audible breath.
            "fragment_interval": 0.0,
        }

    async def synthesize(
        self,
        pack: Mapping[str, Any],
        *,
        text: str,
        style: str,
        reply_kind: str,
        installed_root: Path,
    ) -> SynthesizedTeacherVoice:
        url = self._url()
        if not url:
            _fail("VOICE_PACK_RUNTIME_UNAVAILABLE", "active voice pack runtime is not configured", status_code=503)
        payload = self._tts_payload(
            pack,
            text=text,
            style=style,
            installed_root=installed_root,
        )
        payload["streaming_mode"] = False
        try:
            # GPT-SoVITS returns one complete WAV after the whole utterance is
            # synthesized.  Long teaching replies can legitimately take more
            # than 35 seconds, so keep the runtime request window aligned with
            # the model-prepare window instead of turning a slow synthesis into
            # a false "voice pack unavailable" error.
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=2.0), trust_env=False) as client:
                response = await client.post(f"{url}/tts", json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self._prepared_identity = None
            _fail("VOICE_PACK_RUNTIME_UNAVAILABLE", "active voice pack runtime is unavailable", status_code=503, details={"error": type(exc).__name__})
        media_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
        audio = bytes(response.content)
        if media_type not in {"audio/wav", "audio/x-wav", "audio/wave"} or len(audio) < 44 or audio[:4] != b"RIFF":
            _fail("VOICE_PACK_RUNTIME_INVALID_AUDIO", "voice runtime returned invalid WAV audio", status_code=502)
        return SynthesizedTeacherVoice(audio, "audio/wav", "gpt-sovits", style, str(response.headers.get("x-mentorflow-voice-cache") or "unknown"))

    async def stream(
        self,
        pack: Mapping[str, Any],
        *,
        text: str,
        style: str,
        reply_kind: str,
        installed_root: Path,
    ) -> AsyncIterator[bytes]:
        """Yield GPT-SoVITS audio fragments as soon as the runtime emits them."""
        del reply_kind
        url = self._url()
        if not url:
            _fail("VOICE_PACK_RUNTIME_UNAVAILABLE", "active voice pack runtime is not configured", status_code=503)
        payload = self._tts_payload(
            pack,
            text=text,
            style=style,
            installed_root=installed_root,
        )
        # GPT-SoVITS mode 2 keeps the WAV header and yields raw audio chunks.
        # The browser can consume this as a progressive audio response.
        payload["streaming_mode"] = 2
        # The runtime's default adds 300 ms of silence after every fragment.
        # That is useful for manually concatenated clips, but sounds like
        # repeated inhaling when the browser plays one continuous stream.
        payload["fragment_interval"] = 0.0
        payload["overlap_length"] = 2
        payload["min_chunk_length"] = 8
        payload["fixed_length_chunk"] = False
        saw_chunk = False
        try:
            timeout = httpx.Timeout(connect=2.0, read=120.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream("POST", f"{url}/tts", json=payload) as response:
                    response.raise_for_status()
                    media_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
                    if media_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
                        _fail("VOICE_PACK_RUNTIME_INVALID_AUDIO", "voice runtime returned a non-WAV stream", status_code=502)
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            saw_chunk = True
                            yield bytes(chunk)
        except TeacherVoicePackError:
            raise
        except httpx.HTTPError as exc:
            self._prepared_identity = None
            _fail(
                "VOICE_PACK_RUNTIME_UNAVAILABLE",
                "active voice pack runtime is unavailable",
                status_code=503,
                details={"error": type(exc).__name__},
            )
        if not saw_chunk:
            _fail("VOICE_PACK_RUNTIME_INVALID_AUDIO", "voice runtime returned an empty audio stream", status_code=502)


@dataclass(frozen=True)
class QwenBaseCloneEngineDependency:
    """Identity requested by a signed voice pack."""

    kind: str
    catalog_id: str
    version: str
    manifest_sha256: str

    @property
    def registry_key(self) -> tuple[str, str, str, str]:
        return (self.kind, self.catalog_id, self.version, self.manifest_sha256)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class QwenBaseCloneReference:
    style: str
    relative_path: str
    audio_path: Path
    audio_bytes: int
    audio_sha256: str
    transcript: str
    transcript_sha256: str
    language: str

    def identity_payload(self) -> dict[str, Any]:
        return {
            "style": self.style,
            "relative_path": self.relative_path,
            "audio_bytes": self.audio_bytes,
            "audio_sha256": self.audio_sha256,
            "transcript_sha256": self.transcript_sha256,
            "language": self.language,
        }


@dataclass(frozen=True)
class QwenBaseClonePackBinding:
    """Immutable active-pack identity passed to the local child contract."""

    pack_id: str
    version: str
    manifest_sha256: str
    engine: QwenBaseCloneEngineDependency
    references: tuple[QwenBaseCloneReference, ...]

    def reference_for(self, style: str) -> QwenBaseCloneReference:
        for reference in self.references:
            if reference.style == style:
                return reference
        _fail("VOICE_PACK_STYLE_INVALID", f"unsupported teacher voice style: {style}", status_code=422)

    @property
    def references_sha256(self) -> str:
        return _canonical_sha256(
            [reference.identity_payload() for reference in self.references]
        )

    @property
    def binding_sha256(self) -> str:
        return _canonical_sha256(
            {
                "engine": {
                    "kind": self.engine.kind,
                    "catalog_id": self.engine.catalog_id,
                    "version": self.engine.version,
                    "manifest_sha256": self.engine.manifest_sha256,
                },
                "pack": {
                    "pack_id": self.pack_id,
                    "version": self.version,
                    "manifest_sha256": self.manifest_sha256,
                },
                "references_sha256": self.references_sha256,
            }
        )


@dataclass(frozen=True)
class QwenBaseClonePrepareRequest:
    schema: str
    binding: QwenBaseClonePackBinding
    binding_sha256: str
    references_sha256: str


@dataclass(frozen=True)
class QwenBaseClonePrepareReceipt:
    schema: str
    ready: bool
    engine_kind: str
    engine_catalog_id: str
    engine_version: str
    engine_manifest_sha256: str
    pack_id: str
    pack_version: str
    pack_manifest_sha256: str
    binding_sha256: str
    references_sha256: str


@dataclass(frozen=True)
class QwenBaseCloneSynthesisRequest:
    """Fixed request and cache identity for one loopback synthesis."""

    schema: str
    binding: QwenBaseClonePackBinding
    reference: QwenBaseCloneReference
    binding_sha256: str
    engine_kind: str
    engine_catalog_id: str
    engine_version: str
    engine_manifest_sha256: str
    pack_id: str
    pack_version: str
    pack_manifest_sha256: str
    text: str
    text_sha256: str
    style: str
    reference_audio_sha256: str
    reference_transcript_sha256: str
    reply_kind: str
    request_sha256: str


@dataclass(frozen=True)
class QwenBaseCloneRuntimeAudio:
    """Audio plus an exact echoed release/request/cache binding."""

    schema: str
    audio: bytes
    media_type: str
    engine_kind: str
    engine_catalog_id: str
    engine_version: str
    engine_manifest_sha256: str
    pack_id: str
    pack_version: str
    pack_manifest_sha256: str
    binding_sha256: str
    style: str
    reference_audio_sha256: str
    reference_transcript_sha256: str
    request_text_sha256: str
    reply_kind: str
    request_sha256: str
    audio_sha256: str
    audio_bytes: int
    profile: str
    cache: str = "unknown"


def qwen_base_clone_binding_contract(
    binding: QwenBaseClonePackBinding,
) -> dict[str, Any]:
    if not isinstance(binding, QwenBaseClonePackBinding):
        _fail("VOICE_PACK_RUNTIME_BINDING_MISMATCH", "Qwen binding contract requires the real binding type")
    return {
        "schema": QWEN_BASE_CLONE_BINDING_CONTRACT_SCHEMA,
        "pack": {
            "pack_id": binding.pack_id,
            "version": binding.version,
            "manifest_sha256": binding.manifest_sha256,
        },
        "engine": {
            "kind": binding.engine.kind,
            "catalog_id": binding.engine.catalog_id,
            "version": binding.engine.version,
            "manifest_sha256": binding.engine.manifest_sha256,
        },
        "references": [
            {
                "style": reference.style,
                "relative_path": reference.relative_path,
                "audio_path": str(reference.audio_path),
                "audio_bytes": reference.audio_bytes,
                "audio_sha256": reference.audio_sha256,
                "transcript": reference.transcript,
                "transcript_sha256": reference.transcript_sha256,
                "language": reference.language,
            }
            for reference in binding.references
        ],
        "references_sha256": binding.references_sha256,
        "binding_sha256": binding.binding_sha256,
    }


def _qwen_prepare_request_contract(request: QwenBaseClonePrepareRequest) -> dict[str, Any]:
    return {
        "schema": request.schema,
        "binding_sha256": request.binding_sha256,
        "references_sha256": request.references_sha256,
    }


def _qwen_prepare_receipt_contract(receipt: QwenBaseClonePrepareReceipt) -> dict[str, Any]:
    return {
        "schema": receipt.schema,
        "ready": receipt.ready,
        "engine_kind": receipt.engine_kind,
        "engine_catalog_id": receipt.engine_catalog_id,
        "engine_version": receipt.engine_version,
        "engine_manifest_sha256": receipt.engine_manifest_sha256,
        "pack_id": receipt.pack_id,
        "pack_version": receipt.pack_version,
        "pack_manifest_sha256": receipt.pack_manifest_sha256,
        "binding_sha256": receipt.binding_sha256,
        "references_sha256": receipt.references_sha256,
    }


def _qwen_synthesis_request_contract(request: QwenBaseCloneSynthesisRequest) -> dict[str, Any]:
    return {
        "schema": request.schema,
        "binding_sha256": request.binding_sha256,
        "engine_kind": request.engine_kind,
        "engine_catalog_id": request.engine_catalog_id,
        "engine_version": request.engine_version,
        "engine_manifest_sha256": request.engine_manifest_sha256,
        "pack_id": request.pack_id,
        "pack_version": request.pack_version,
        "pack_manifest_sha256": request.pack_manifest_sha256,
        "text": request.text,
        "text_sha256": request.text_sha256,
        "style": request.style,
        "reference": {
            "style": request.reference.style,
            "relative_path": request.reference.relative_path,
            "audio_path": str(request.reference.audio_path),
            "audio_bytes": request.reference.audio_bytes,
            "audio_sha256": request.reference.audio_sha256,
            "transcript": request.reference.transcript,
            "transcript_sha256": request.reference.transcript_sha256,
            "language": request.reference.language,
        },
        "reference_audio_sha256": request.reference_audio_sha256,
        "reference_transcript_sha256": request.reference_transcript_sha256,
        "reply_kind": request.reply_kind,
        "request_sha256": request.request_sha256,
    }


def qwen_base_clone_audio_scope_contract(
    *,
    binding: QwenBaseClonePackBinding,
    request: QwenBaseCloneSynthesisRequest,
    result: QwenBaseCloneRuntimeAudio,
    audio: bytes,
) -> dict[str, Any]:
    return {
        "schema": QWEN_BASE_CLONE_AUDIO_SCOPE_SCHEMA,
        "binding": qwen_base_clone_binding_contract(binding),
        "request": _qwen_synthesis_request_contract(request),
        "runtime_result": {
            "schema": result.schema,
            "media_type": result.media_type,
            "engine_kind": result.engine_kind,
            "engine_catalog_id": result.engine_catalog_id,
            "engine_version": result.engine_version,
            "engine_manifest_sha256": result.engine_manifest_sha256,
            "pack_id": result.pack_id,
            "pack_version": result.pack_version,
            "pack_manifest_sha256": result.pack_manifest_sha256,
            "binding_sha256": result.binding_sha256,
            "style": result.style,
            "reference_audio_sha256": result.reference_audio_sha256,
            "reference_transcript_sha256": result.reference_transcript_sha256,
            "request_text_sha256": result.request_text_sha256,
            "reply_kind": result.reply_kind,
            "request_sha256": result.request_sha256,
            "audio_sha256": result.audio_sha256,
            "audio_bytes": result.audio_bytes,
            "profile": result.profile,
            "cache": result.cache,
        },
        "audio": {
            "bytes": len(audio),
            "sha256": hashlib.sha256(audio).hexdigest(),
            "media_type": "audio/wav",
            "profile": str(result.profile or request.style),
            "cache": str(result.cache or "unknown"),
        },
    }


class QwenBaseCloneRuntime(Protocol):
    """Trusted app-side implementation of the fixed loopback child contract.

    A future engine-pack manager registers an implementation only after it has
    verified the application engine catalog and engine manifest.  The voice
    pack cannot provide an implementation, origin, executable, or arguments.
    """

    async def prepare(
        self, request: QwenBaseClonePrepareRequest
    ) -> QwenBaseClonePrepareReceipt: ...

    async def synthesize(
        self, request: QwenBaseCloneSynthesisRequest
    ) -> QwenBaseCloneRuntimeAudio: ...


_QWEN_ENGINE_CAPABILITY_TOKEN = object()


class VerifiedQwenEngineRegistrationCapability:
    """Opaque proof returned only after release signature and file verification."""

    __slots__ = (
        "_token",
        "dependency",
        "package_root",
        "archive_sha256",
        "release_trust_binding",
        "_verified_files",
        "_verified_directories",
        "_install_path_budget",
    )

    def __init__(
        self,
        token: object,
        dependency: QwenBaseCloneEngineDependency,
        package_root: Path,
        archive_sha256: str,
        verified_files: tuple[tuple[str, int, str], ...] = (),
        verified_directories: tuple[str, ...] = (),
        install_path_budget: Mapping[str, Any] | None = None,
        release_trust_binding: ReleaseTrustBinding | None = None,
    ) -> None:
        if token is not _QWEN_ENGINE_CAPABILITY_TOKEN:
            raise TypeError("verified engine capabilities cannot be constructed directly")
        object.__setattr__(self, "_token", token)
        object.__setattr__(self, "dependency", dependency)
        object.__setattr__(self, "package_root", package_root)
        object.__setattr__(self, "archive_sha256", archive_sha256)
        object.__setattr__(self, "release_trust_binding", release_trust_binding)
        object.__setattr__(self, "_verified_files", verified_files)
        object.__setattr__(self, "_verified_directories", verified_directories)
        object.__setattr__(self, "_install_path_budget", dict(install_path_budget or {}))

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("verified engine capabilities are immutable")

    def is_valid(self) -> bool:
        return (
            self._token is _QWEN_ENGINE_CAPABILITY_TOKEN
            and bool(self._verified_files)
            and bool(self._install_path_budget)
            and (
                self.release_trust_binding is None
                or (
                    isinstance(self.release_trust_binding, ReleaseTrustBinding)
                    and self.release_trust_binding.is_valid()
                )
            )
        )

    def is_production_trusted(self) -> bool:
        return (
            self.is_valid()
            and isinstance(self.release_trust_binding, ReleaseTrustBinding)
            and self.release_trust_binding.is_valid()
            and self.release_trust_binding.trust.test_only is False
        )


def _trusted_engine_public_key(value: Any, key_id: str) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if len(raw) != 32:
            _fail(
                "VOICE_ENGINE_TRUST_CONFIG_INVALID",
                f"trusted engine key {key_id} must contain 32 bytes",
                status_code=500,
            )
        return raw
    return _decode_key(value, f"trusted engine key {key_id}", 32)


def _engine_regular_file_metadata(path: Path, root: Path) -> os.stat_result:
    _assert_no_reparse(path, root)
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        _fail(
            "VOICE_ENGINE_PACKAGE_INVALID",
            f"cannot inspect engine package file: {path}",
            details={"error": f"{type(exc).__name__}:{exc}"},
        )
    if not stat.S_ISREG(metadata.st_mode):
        _fail("VOICE_ENGINE_PACKAGE_INVALID", f"engine package entry is not a regular file: {path}")
    if int(getattr(metadata, "st_nlink", 1) or 1) > 1:
        _fail("VOICE_ENGINE_HARDLINK_FORBIDDEN", f"engine package hardlink is forbidden: {path}")
    return metadata


def _secure_engine_file_snapshot(
    path: Path,
    root: Path,
    *,
    capture: bool = False,
) -> tuple[int, str, bytes | None]:
    """Read one verified-root file while binding the opened handle to lstat.

    The pre-open, opened-handle, and post-read identities must agree. Every
    call computes SHA256 again from the actual opened handle, so a path checked
    during enumeration is never trusted as a substitute for the later read.
    """

    before = _engine_regular_file_metadata(path, root)
    digest = hashlib.sha256()
    chunks: list[bytes] | None = [] if capture else None
    total = 0
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or int(getattr(opened, "st_nlink", 1) or 1) > 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                _fail(
                    "VOICE_ENGINE_FILE_MISMATCH",
                    f"engine package file changed before open: {path}",
                )
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                total += len(chunk)
                if chunks is not None:
                    chunks.append(chunk)
    except TeacherVoicePackError:
        raise
    except OSError as exc:
        _fail(
            "VOICE_ENGINE_FILE_MISMATCH",
            f"cannot read engine package file: {path}",
            details={"error": f"{type(exc).__name__}:{exc}"},
        )
    after = _engine_regular_file_metadata(path, root)
    if (
        (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
        or after.st_size != opened.st_size
        or total != opened.st_size
        or getattr(after, "st_mtime_ns", None) != getattr(opened, "st_mtime_ns", None)
    ):
        _fail("VOICE_ENGINE_FILE_MISMATCH", f"engine package file changed while reading: {path}")
    return total, digest.hexdigest(), b"".join(chunks) if chunks is not None else None


def _enumerate_secure_package_files(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    windows_paths: dict[str, str] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        _assert_no_reparse(directory, root)
        try:
            children = list(directory.iterdir())
        except OSError as exc:
            _fail(
                "VOICE_ENGINE_PACKAGE_INVALID",
                "cannot enumerate the Qwen engine package",
                details={"error": f"{type(exc).__name__}:{exc}"},
            )
        for child in children:
            _assert_no_reparse(child, root)
            relative = _safe_relative(
                child.relative_to(root).as_posix(),
                "engine package entry",
            )
            relative_key = relative.as_posix()
            folded = windows_casefold_key(relative)
            previous = windows_paths.get(folded)
            if previous is not None and previous != relative_key:
                _fail(
                    "VOICE_ENGINE_PACKAGE_INVALID",
                    "engine package contains a Windows case-insensitive path collision",
                    details={"first": previous, "second": relative_key},
                )
            windows_paths[folded] = relative_key
            try:
                metadata = os.lstat(child)
            except OSError as exc:
                _fail(
                    "VOICE_ENGINE_PACKAGE_INVALID",
                    f"cannot inspect package entry: {child}",
                    details={"error": f"{type(exc).__name__}:{exc}"},
                )
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(relative_key)
                pending.append(child)
            elif stat.S_ISREG(metadata.st_mode):
                if int(getattr(metadata, "st_nlink", 1) or 1) > 1:
                    _fail(
                        "VOICE_ENGINE_HARDLINK_FORBIDDEN",
                        f"engine package hardlink is forbidden: {child}",
                    )
                files.add(relative_key)
            else:
                _fail(
                    "VOICE_ENGINE_PACKAGE_INVALID",
                    f"unsupported package entry: {child}",
                )
    return files, directories


def _reverify_qwen_engine_capability(
    capability: VerifiedQwenEngineRegistrationCapability,
) -> None:
    """Re-open and re-hash the exact verified engine root before use."""

    if not capability.is_valid():
        _fail(
            "VOICE_ENGINE_CAPABILITY_REQUIRED",
            "a complete verified Qwen engine capability is required",
            status_code=500,
        )
    root = Path(os.path.abspath(capability.package_root))
    _assert_no_reparse(root, root)
    actual_files, actual_directories = _enumerate_secure_package_files(root)
    expected_files = {item[0] for item in capability._verified_files}
    expected_directories = set(capability._verified_directories)
    if actual_files != expected_files or actual_directories != expected_directories:
        _fail(
            "VOICE_ENGINE_PACKAGE_INVALID",
            "verified Qwen engine package changed before use",
            details={
                "missing_files": sorted(expected_files - actual_files),
                "undeclared_files": sorted(actual_files - expected_files),
                "missing_directories": sorted(expected_directories - actual_directories),
                "undeclared_directories": sorted(actual_directories - expected_directories),
            },
        )
    try:
        validate_windows_install_root_budget(
            root,
            capability._install_path_budget,
            relative_paths=actual_files,
        )
    except WindowsInstallPathBudgetError as exc:
        _fail(
            "VOICE_ENGINE_INSTALL_PATH_BUDGET_INVALID",
            "verified Qwen engine package exceeds or changed its signed Windows path budget",
            details={"path_budget_error": exc.code},
        )
    for relative_key, expected_bytes, expected_sha256 in capability._verified_files:
        relative = _safe_relative(relative_key, "verified engine capability file")
        path = _join_relative(root, relative)
        actual_bytes, actual_sha256, _unused = _secure_engine_file_snapshot(path, root)
        if actual_bytes != expected_bytes or actual_sha256 != expected_sha256:
            _fail(
                "VOICE_ENGINE_FILE_MISMATCH",
                f"verified Qwen engine file changed before use: {relative_key}",
            )


def verify_qwen_base_clone_engine_release(
    *,
    catalog: Mapping[str, Any],
    package_id: str,
    version: str,
    archive_path: Path,
    package_root: Path,
    trusted_public_keys: Mapping[str, Any] | None = None,
    release_trust: VerifiedReleaseTrust | None = None,
    catalog_sha256: str = "",
    catalog_bytes: int = 0,
    catalog_path: Path | None = None,
) -> VerifiedQwenEngineRegistrationCapability:
    """Verify one app-catalog engine archive, signed manifest, and every file.

    Paths are supplied by trusted application code.  Neither the application
    catalog nor the signed engine manifest is allowed to carry a URL, command,
    module, executable, key, or launch argument.
    """
    catalog_object = _strict_object(
        catalog,
        "engine_catalog",
        _QWEN_ENGINE_CATALOG_FIELDS,
        code="VOICE_ENGINE_CATALOG_INVALID",
    )
    if catalog_object.get("schema") != QWEN_ENGINE_CATALOG_SCHEMA:
        _fail("VOICE_ENGINE_CATALOG_INVALID", "engine catalog schema is invalid")
    raw_entries = catalog_object.get("engines")
    if not isinstance(raw_entries, list):
        _fail("VOICE_ENGINE_CATALOG_INVALID", "engine_catalog.engines must be a list")
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw_entry in enumerate(raw_entries):
        entry = _strict_object(
            raw_entry,
            f"engine_catalog.engines[{index}]",
            _QWEN_ENGINE_CATALOG_ENTRY_FIELDS,
            code="VOICE_ENGINE_CATALOG_INVALID",
        )
        identity = (
            str(entry.get("kind") or "").strip(),
            _safe_id(entry.get("package_id"), "engine package_id"),
            _safe_id(entry.get("version"), "engine version"),
        )
        if identity in seen:
            _fail("VOICE_ENGINE_CATALOG_INVALID", "duplicate engine catalog identity")
        seen.add(identity)
        if identity[0] != QWEN_BASE_CLONE_ENGINE_KIND:
            _fail("VOICE_ENGINE_CATALOG_INVALID", "unsupported engine kind in catalog")
        entry["archive"] = _strict_object(
            entry.get("archive"),
            f"engine_catalog.engines[{index}].archive",
            _QWEN_ENGINE_DIGEST_FIELDS,
            code="VOICE_ENGINE_CATALOG_INVALID",
        )
        entry["manifest"] = _strict_object(
            entry.get("manifest"),
            f"engine_catalog.engines[{index}].manifest",
            _QWEN_ENGINE_DIGEST_FIELDS,
            code="VOICE_ENGINE_CATALOG_INVALID",
        )
        for digest_name in ("archive", "manifest"):
            digest = entry[digest_name]
            if not isinstance(digest.get("bytes"), int) or digest["bytes"] <= 0:
                _fail("VOICE_ENGINE_CATALOG_INVALID", f"{digest_name}.bytes must be positive")
            digest["sha256"] = _sha256(digest.get("sha256"), f"{digest_name}.sha256")
        entry["signing_key_id"] = _safe_id(entry.get("signing_key_id"), "signing_key_id")
        entries.append(entry)

    requested = (
        QWEN_BASE_CLONE_ENGINE_KIND,
        _safe_id(package_id, "engine package_id"),
        _safe_id(version, "engine version"),
    )
    matches = [
        entry
        for entry in entries
        if (entry["kind"], entry["package_id"], entry["version"]) == requested
    ]
    if len(matches) != 1:
        _fail(
            "VOICE_ENGINE_NOT_IN_CATALOG",
            "requested Qwen engine release is not uniquely present in the app catalog",
            status_code=404,
        )
    entry = matches[0]
    key_id = entry["signing_key_id"]
    release_binding: ReleaseTrustBinding | None = None
    if release_trust is not None:
        if (
            not isinstance(release_trust, VerifiedReleaseTrust)
            or not release_trust.is_valid()
            or release_trust.manifest_schema != QWEN_ENGINE_MANIFEST_SCHEMA
            or release_trust.leaf_key_id != key_id
        ):
            _fail(
                "VOICE_ENGINE_RELEASE_TRUST_INVALID",
                "engine release trust does not authorize the catalog signing key and manifest schema",
            )
        if (
            not SHA256_HEX.fullmatch(catalog_sha256)
            or isinstance(catalog_bytes, bool)
            or not isinstance(catalog_bytes, int)
            or catalog_bytes <= 0
            or catalog_path is None
        ):
            _fail(
                "VOICE_ENGINE_RELEASE_TRUST_INVALID",
                "trusted engine registration must bind the exact catalog bytes and path",
            )
        public_key = release_trust.raw_public_key
    else:
        keys = trusted_public_keys or {}
        if key_id not in keys:
            _fail(
                "VOICE_ENGINE_SIGNING_KEY_UNKNOWN",
                "engine signing key is not trusted by the application",
            )
        public_key = _trusted_engine_public_key(keys[key_id], key_id)

    raw_archive_path = Path(os.path.abspath(archive_path))
    _assert_no_reparse(raw_archive_path.parent, raw_archive_path.parent)
    _assert_no_reparse(raw_archive_path, raw_archive_path.parent)
    if not raw_archive_path.is_file():
        _fail("VOICE_ENGINE_ARCHIVE_MISSING", "Qwen engine archive is missing")
    archive_digest = entry["archive"]
    try:
        archive_bytes, archive_sha256 = secure_hash_release_file(
            raw_archive_path,
            label="Qwen engine archive",
            expected_bytes=int(archive_digest["bytes"]),
        )
    except ReleaseTrustError as exc:
        _fail(
            "VOICE_ENGINE_ARCHIVE_MISMATCH",
            "Qwen engine archive could not be verified from one fixed handle",
            details={"trust_error": exc.code},
        )
    if archive_bytes != archive_digest["bytes"]:
        _fail("VOICE_ENGINE_ARCHIVE_MISMATCH", "Qwen engine archive size does not match catalog")
    if archive_sha256 != archive_digest["sha256"]:
        _fail("VOICE_ENGINE_ARCHIVE_MISMATCH", "Qwen engine archive hash does not match catalog")

    raw_package_root = Path(os.path.abspath(package_root))
    _assert_no_reparse(raw_package_root, raw_package_root)
    if not raw_package_root.is_dir():
        _fail("VOICE_ENGINE_PACKAGE_INVALID", "Qwen engine package root is missing")
    actual_paths, actual_directories = _enumerate_secure_package_files(raw_package_root)
    manifest_path = raw_package_root / "engine-manifest.json"
    signature_path = raw_package_root / "engine-manifest.sig"
    if not {manifest_path.name, signature_path.name}.issubset(actual_paths):
        _fail("VOICE_ENGINE_PACKAGE_INVALID", "engine package metadata files are missing")
    manifest_digest = entry["manifest"]
    if manifest_digest["bytes"] > MAX_MANIFEST_BYTES:
        _fail("VOICE_ENGINE_MANIFEST_INVALID", "engine manifest exceeds the size limit")
    manifest_bytes, manifest_sha256, raw_manifest_value = _secure_engine_file_snapshot(
        manifest_path,
        raw_package_root,
        capture=True,
    )
    raw_manifest = raw_manifest_value or b""
    if manifest_bytes != manifest_digest["bytes"] or manifest_sha256 != manifest_digest["sha256"]:
        _fail("VOICE_ENGINE_MANIFEST_MISMATCH", "engine manifest does not match app catalog")
    signature_bytes, _signature_sha256, raw_signature_value = _secure_engine_file_snapshot(
        signature_path,
        raw_package_root,
        capture=True,
    )
    if signature_bytes > MAX_MANIFEST_BYTES:
        _fail("VOICE_ENGINE_SIGNATURE_INVALID", "engine manifest signature metadata exceeds the size limit")
    try:
        signature_object = json.loads((raw_signature_value or b"").decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "VOICE_ENGINE_SIGNATURE_INVALID",
            "engine manifest signature metadata is invalid",
            details={"error": f"{type(exc).__name__}:{exc}"},
        )
    signature_object = _strict_object(
        signature_object,
        "engine-manifest.sig",
        {"algorithm", "key_id", "signature"},
        code="VOICE_ENGINE_SIGNATURE_INVALID",
    )
    if signature_object["algorithm"] != "ed25519" or signature_object["key_id"] != key_id:
        _fail("VOICE_ENGINE_SIGNATURE_INVALID", "engine signature algorithm or key id is invalid")
    signature = _decode_key(signature_object["signature"], "engine signature", 64)
    if not verify_ed25519(public_key, raw_manifest, signature):
        _fail("VOICE_ENGINE_SIGNATURE_INVALID", "engine manifest Ed25519 signature is invalid")
    try:
        manifest_object = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "VOICE_ENGINE_MANIFEST_INVALID",
            "signed engine manifest JSON is invalid",
            details={"error": f"{type(exc).__name__}:{exc}"},
        )
    manifest_object = _strict_object(
        manifest_object,
        "engine_manifest",
        _QWEN_ENGINE_MANIFEST_FIELDS,
        code="VOICE_ENGINE_MANIFEST_INVALID",
    )
    if (
        manifest_object.get("schema") != QWEN_ENGINE_MANIFEST_SCHEMA
        or manifest_object.get("kind") != requested[0]
        or manifest_object.get("package_id") != requested[1]
        or manifest_object.get("version") != requested[2]
    ):
        _fail("VOICE_ENGINE_MANIFEST_INVALID", "signed engine identity does not match catalog")
    raw_files = manifest_object.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        _fail("VOICE_ENGINE_MANIFEST_INVALID", "signed engine files must be non-empty")
    declared_paths: set[str] = set()
    declared_casefold: set[str] = set()
    declared_files: dict[str, tuple[PurePosixPath, int, str]] = {}
    for index, raw_file in enumerate(raw_files):
        item = _strict_object(
            raw_file,
            f"engine_manifest.files[{index}]",
            _QWEN_ENGINE_FILE_FIELDS,
            code="VOICE_ENGINE_MANIFEST_INVALID",
        )
        relative = _safe_relative(item.get("path"), f"engine_manifest.files[{index}].path")
        relative_key = relative.as_posix()
        if relative_key in {"engine-manifest.json", "engine-manifest.sig"}:
            _fail(
                "VOICE_ENGINE_MANIFEST_INVALID",
                "engine manifest cannot declare its own metadata files as payload",
            )
        folded = windows_casefold_key(relative)
        if folded in declared_casefold:
            _fail("VOICE_ENGINE_MANIFEST_INVALID", "duplicate engine file path")
        declared_casefold.add(folded)
        declared_paths.add(relative_key)
        role = _safe_id(item.get("role"), f"engine_manifest.files[{index}].role")
        if role not in _QWEN_ENGINE_FILE_ROLES:
            _fail("VOICE_ENGINE_MANIFEST_INVALID", f"unsupported engine file role: {role}")
        size = item.get("bytes")
        if not isinstance(size, int) or size < 0:
            _fail("VOICE_ENGINE_MANIFEST_INVALID", "engine file bytes must be non-negative")
        expected_sha256 = _sha256(item.get("sha256"), "engine file sha256")
        declared_files[relative_key] = (relative, size, expected_sha256)
    expected_paths = declared_paths | {"engine-manifest.json", "engine-manifest.sig"}
    try:
        install_root_path_acceptance = validate_windows_install_root_budget(
            raw_package_root,
            manifest_object.get("install_path_budget"),
            relative_paths=expected_paths,
        )
    except WindowsInstallPathBudgetError as exc:
        _fail(
            "VOICE_ENGINE_INSTALL_PATH_BUDGET_INVALID",
            "signed engine install path budget is invalid or the package root is too long",
            details={"path_budget_error": exc.code},
        )
    if actual_paths != expected_paths:
        _fail(
            "VOICE_ENGINE_PACKAGE_INVALID",
            "engine package contains missing or undeclared files",
            details={
                "missing": sorted(expected_paths - actual_paths),
                "undeclared": sorted(actual_paths - expected_paths),
            },
        )
    expected_directories: set[str] = set()
    expected_directory_casefold: dict[str, str] = {}
    for relative_key in expected_paths:
        parent = PurePosixPath(relative_key).parent
        while parent != PurePosixPath("."):
            parent_key = parent.as_posix()
            folded = windows_casefold_key(parent)
            previous = expected_directory_casefold.get(folded)
            if previous is not None and previous != parent_key:
                _fail(
                    "VOICE_ENGINE_MANIFEST_INVALID",
                    "engine manifest contains a Windows case-insensitive directory collision",
                )
            expected_directory_casefold[folded] = parent_key
            expected_directories.add(parent_key)
            parent = parent.parent
    if actual_directories != expected_directories:
        _fail(
            "VOICE_ENGINE_PACKAGE_INVALID",
            "engine package contains missing or undeclared directories",
            details={
                "missing": sorted(expected_directories - actual_directories),
                "undeclared": sorted(actual_directories - expected_directories),
            },
        )

    # Structural verification happens before payload reads. Each declared file
    # is then opened again from the verified lexical root and SHA256 is
    # recomputed from that handle; enumeration metadata is never treated as a
    # substitute for the actual read used to accept the payload.
    for relative_key, (relative, expected_bytes, expected_sha256) in declared_files.items():
        path = _join_relative(raw_package_root, relative)
        actual_bytes, actual_sha256, _unused = _secure_engine_file_snapshot(
            path,
            raw_package_root,
        )
        if actual_bytes != expected_bytes or actual_sha256 != expected_sha256:
            _fail("VOICE_ENGINE_FILE_MISMATCH", f"engine file verification failed: {relative_key}")
    final_paths, final_directories = _enumerate_secure_package_files(raw_package_root)
    if final_paths != actual_paths or final_directories != actual_directories:
        _fail("VOICE_ENGINE_PACKAGE_INVALID", "engine package changed during verification")
    final_manifest_bytes, final_manifest_sha256, _unused = _secure_engine_file_snapshot(
        manifest_path,
        raw_package_root,
    )
    if (
        final_manifest_bytes != manifest_digest["bytes"]
        or final_manifest_sha256 != manifest_digest["sha256"]
    ):
        _fail("VOICE_ENGINE_MANIFEST_MISMATCH", "engine manifest changed during verification")
    dependency = QwenBaseCloneEngineDependency(
        kind=requested[0],
        catalog_id=requested[1],
        version=requested[2],
        manifest_sha256=manifest_digest["sha256"],
    )
    verified_files = tuple(
        sorted(
            [
                ("engine-manifest.json", manifest_digest["bytes"], manifest_digest["sha256"]),
                ("engine-manifest.sig", signature_bytes, _signature_sha256),
                *(
                    (relative_key, expected_bytes, expected_sha256)
                    for relative_key, (_relative, expected_bytes, expected_sha256) in declared_files.items()
                ),
            ]
        )
    )
    if release_trust is not None:
        try:
            release_binding = release_trust.bind_engine_release(
                catalog_sha256=catalog_sha256,
                catalog_bytes=catalog_bytes,
                archive_sha256=archive_sha256,
                archive_bytes=int(entry["archive"]["bytes"]),
                catalog_path=Path(catalog_path),
                archive_path=raw_archive_path,
            )
        except Exception as exc:
            _fail(
                "VOICE_ENGINE_RELEASE_TRUST_INVALID",
                "engine release trust binding could not be sealed",
                details={"error": f"{type(exc).__name__}:{exc}"},
            )
    return VerifiedQwenEngineRegistrationCapability(
        _QWEN_ENGINE_CAPABILITY_TOKEN,
        dependency,
        raw_package_root.resolve(strict=True),
        archive_sha256,
        verified_files,
        tuple(sorted(expected_directories)),
        install_path_budget=manifest_object["install_path_budget"],
        release_trust_binding=release_binding,
    )


@dataclass(frozen=True)
class _VerifiedQwenRuntimeRegistration:
    capability: VerifiedQwenEngineRegistrationCapability
    runtime: QwenBaseCloneRuntime


_QWEN_BASE_CLONE_RUNTIMES: dict[
    tuple[str, str, str, str], _VerifiedQwenRuntimeRegistration
] = {}
_QWEN_BASE_CLONE_REGISTRY_LOCK = threading.RLock()


def register_qwen_base_clone_runtime(
    *,
    capability: VerifiedQwenEngineRegistrationCapability,
    runtime: QwenBaseCloneRuntime,
) -> None:
    """Atomically register only a cryptographically verified engine release."""
    if not isinstance(capability, VerifiedQwenEngineRegistrationCapability) or not capability.is_valid():
        _fail(
            "VOICE_ENGINE_CAPABILITY_REQUIRED",
            "a verified Qwen engine registration capability is required",
            status_code=500,
        )
    if not callable(getattr(runtime, "prepare", None)) or not callable(
        getattr(runtime, "synthesize", None)
    ):
        _fail(
            "VOICE_PACK_RUNTIME_CONFIG_INVALID",
            "trusted Qwen runtime must implement prepare and synthesize",
            status_code=500,
        )
    _reverify_qwen_engine_capability(capability)
    registration = _VerifiedQwenRuntimeRegistration(capability, runtime)
    with _QWEN_BASE_CLONE_REGISTRY_LOCK:
        existing = _QWEN_BASE_CLONE_RUNTIMES.get(capability.dependency.registry_key)
        if existing is not None:
            if existing.capability is capability and existing.runtime is runtime:
                return
            _fail(
                "VOICE_ENGINE_REGISTRATION_CONFLICT",
                "a Qwen runtime is already registered for this verified engine identity",
                status_code=409,
            )
        _QWEN_BASE_CLONE_RUNTIMES[capability.dependency.registry_key] = registration


def unregister_qwen_base_clone_runtime(
    *,
    capability: VerifiedQwenEngineRegistrationCapability,
    runtime: QwenBaseCloneRuntime,
) -> bool:
    """Remove only the exact registration being shut down by its owner."""
    if not isinstance(capability, VerifiedQwenEngineRegistrationCapability):
        return False
    with _QWEN_BASE_CLONE_REGISTRY_LOCK:
        registration = _QWEN_BASE_CLONE_RUNTIMES.get(capability.dependency.registry_key)
        if (
            registration is None
            or registration.capability is not capability
            or registration.runtime is not runtime
        ):
            return False
        del _QWEN_BASE_CLONE_RUNTIMES[capability.dependency.registry_key]
        return True


def _qwen_runtime_registration(
    dependency: QwenBaseCloneEngineDependency,
) -> _VerifiedQwenRuntimeRegistration | None:
    with _QWEN_BASE_CLONE_REGISTRY_LOCK:
        registration = _QWEN_BASE_CLONE_RUNTIMES.get(dependency.registry_key)
        if registration is None:
            return None
        if (
            not isinstance(registration, _VerifiedQwenRuntimeRegistration)
            or not registration.capability.is_valid()
            or registration.capability.dependency != dependency
        ):
            return None
        return registration


def _qwen_engine_dependency(pack: Mapping[str, Any]) -> QwenBaseCloneEngineDependency:
    runtime = _mapping(pack.get("runtime"), "runtime")
    kind, catalog_id, version, manifest_sha256 = _qwen_engine_dependency_values(runtime)
    return QwenBaseCloneEngineDependency(kind, catalog_id, version, manifest_sha256)


def _qwen_pack_binding(
    pack: Mapping[str, Any],
    installed_root: Path,
    manifest_sha256: str,
) -> QwenBaseClonePackBinding:
    references: list[QwenBaseCloneReference] = []
    styles = _mapping(pack.get("styles"), "styles")
    for style_name in sorted(REQUIRED_STYLES):
        style = _mapping(styles.get(style_name), f"styles.{style_name}")
        relative = _safe_relative(
            style.get("reference_audio"),
            f"styles.{style_name}.reference_audio",
        ).as_posix()
        declared = [
            _mapping(item, "files[]")
            for item in pack.get("files", [])
            if isinstance(item, Mapping)
            and item.get("role") == "reference_audio"
            and item.get("path") == relative
        ]
        if len(declared) != 1:
            _fail(
                "VOICE_PACK_STYLES_INVALID",
                f"{style_name} reference audio is not declared exactly once",
            )
        reference_path = _verified_declared_file(
            declared[0],
            installed_root,
            field=f"styles.{style_name}.reference_audio",
        )
        _validate_pcm_wav_path(reference_path, f"styles.{style_name}.reference_audio")
        transcript = str(style.get("transcript") or "").strip()
        references.append(
            QwenBaseCloneReference(
                style=style_name,
                relative_path=relative,
                audio_path=reference_path,
                audio_bytes=int(declared[0].get("bytes")),
                audio_sha256=_sha256(
                    declared[0].get("sha256"),
                    f"styles.{style_name}.reference_audio.sha256",
                ),
                transcript=transcript,
                transcript_sha256=_text_sha256(transcript),
                language=str(style.get("language") or "").strip(),
            )
        )
    return QwenBaseClonePackBinding(
        pack_id=_safe_id(pack.get("pack_id"), "pack_id"),
        version=_safe_id(pack.get("version"), "version"),
        manifest_sha256=_sha256(manifest_sha256, "manifest_sha256"),
        engine=_qwen_engine_dependency(pack),
        references=tuple(references),
    )


def _validate_qwen_prepare_receipt(
    receipt: Any,
    request: QwenBaseClonePrepareRequest,
) -> None:
    binding = request.binding
    if (
        not isinstance(receipt, QwenBaseClonePrepareReceipt)
        or receipt.schema != QWEN_BASE_CLONE_PREPARE_SCHEMA
        or receipt.ready is not True
        or receipt.engine_kind != binding.engine.kind
        or receipt.engine_catalog_id != binding.engine.catalog_id
        or receipt.engine_version != binding.engine.version
        or receipt.engine_manifest_sha256 != binding.engine.manifest_sha256
        or receipt.pack_id != binding.pack_id
        or receipt.pack_version != binding.version
        or receipt.pack_manifest_sha256 != binding.manifest_sha256
        or receipt.binding_sha256 != request.binding_sha256
        or receipt.references_sha256 != request.references_sha256
    ):
        _fail(
            "VOICE_PACK_RUNTIME_BINDING_MISMATCH",
            "Qwen runtime did not attest the exact active pack and engine release",
            status_code=503,
        )


def _qwen_synthesis_request(
    binding: QwenBaseClonePackBinding,
    *,
    text: str,
    style: str,
    reply_kind: str,
) -> QwenBaseCloneSynthesisRequest:
    reference = binding.reference_for(style)
    text_value = str(text)
    reply_kind_value = str(reply_kind or "")
    identity = {
        "binding_sha256": binding.binding_sha256,
        "engine_kind": binding.engine.kind,
        "engine_catalog_id": binding.engine.catalog_id,
        "engine_version": binding.engine.version,
        "engine_manifest_sha256": binding.engine.manifest_sha256,
        "pack_id": binding.pack_id,
        "pack_version": binding.version,
        "pack_manifest_sha256": binding.manifest_sha256,
        "style": style,
        "reference_audio_sha256": reference.audio_sha256,
        "reference_transcript_sha256": reference.transcript_sha256,
        "text_sha256": _text_sha256(text_value),
        "reply_kind": reply_kind_value,
    }
    return QwenBaseCloneSynthesisRequest(
        schema=QWEN_BASE_CLONE_SYNTHESIS_SCHEMA,
        binding=binding,
        reference=reference,
        binding_sha256=binding.binding_sha256,
        engine_kind=binding.engine.kind,
        engine_catalog_id=binding.engine.catalog_id,
        engine_version=binding.engine.version,
        engine_manifest_sha256=binding.engine.manifest_sha256,
        pack_id=binding.pack_id,
        pack_version=binding.version,
        pack_manifest_sha256=binding.manifest_sha256,
        text=text_value,
        text_sha256=identity["text_sha256"],
        style=style,
        reference_audio_sha256=reference.audio_sha256,
        reference_transcript_sha256=reference.transcript_sha256,
        reply_kind=reply_kind_value,
        request_sha256=_canonical_sha256(identity),
    )


class QwenBaseCloneVoicePackAdapter:
    """Reference-conditioned adapter backed only by verified engine registry."""

    def __init__(self) -> None:
        self._state_guard = threading.RLock()
        self._prepare_generation = 0
        self._prepared_binding: QwenBaseClonePackBinding | None = None
        self._prepared_registration: _VerifiedQwenRuntimeRegistration | None = None

    def _clear_prepared(self) -> None:
        with self._state_guard:
            self._prepare_generation += 1
            self._prepared_binding = None
            self._prepared_registration = None

    def _clear_if_generation(self, generation: int) -> None:
        with self._state_guard:
            if self._prepare_generation == generation:
                self._prepare_generation += 1
                self._prepared_binding = None
                self._prepared_registration = None

    def status(self, pack: Mapping[str, Any]) -> Mapping[str, Any]:
        dependency = _qwen_engine_dependency(pack)
        registration = _qwen_runtime_registration(dependency)
        with self._state_guard:
            prepared = self._prepared_binding
            same_pack = bool(
                prepared
                and prepared.pack_id == pack.get("pack_id")
                and prepared.version == pack.get("version")
                and prepared.engine == dependency
                and self._prepared_registration is registration
            )
        return {
            "state": "prepared" if same_pack else ("configured" if registration else "unavailable"),
            "starts_model": False,
            "engine_dependency": {
                "kind": dependency.kind,
                "catalog_id": dependency.catalog_id,
                "version": dependency.version,
                "manifest_sha256": dependency.manifest_sha256,
            },
            "prepared_pack": (
                {
                    "pack_id": prepared.pack_id,
                    "version": prepared.version,
                    "manifest_sha256": prepared.manifest_sha256,
                    "binding_sha256": prepared.binding_sha256,
                }
                if same_pack and prepared
                else None
            ),
        }

    async def prepare(
        self,
        pack: Mapping[str, Any],
        *,
        installed_root: Path,
        manifest_sha256: str,
    ) -> Mapping[str, Any]:
        binding = _qwen_pack_binding(pack, installed_root, manifest_sha256)
        registration = _qwen_runtime_registration(binding.engine)
        if registration is None:
            self._clear_prepared()
            _fail(
                "VOICE_PACK_RUNTIME_UNAVAILABLE",
                "the signed Qwen voice pack requires a verified engine release that is not registered",
                status_code=503,
                details={
                    "engine_catalog_id": binding.engine.catalog_id,
                    "engine_version": binding.engine.version,
                    "engine_manifest_sha256": binding.engine.manifest_sha256,
                },
            )
        # Never trust the adapter's process-local prepared marker as proof that
        # the supervised child still has prompts. Always call child prepare;
        # its prompt cache is generation-local. The supervisor performs the
        # expensive full engine inventory verification only when it has proved
        # that a new child generation must be launched.
        with self._state_guard:
            self._prepare_generation += 1
            generation = self._prepare_generation
            self._prepared_binding = None
            self._prepared_registration = None
        request = QwenBaseClonePrepareRequest(
            schema=QWEN_BASE_CLONE_PREPARE_SCHEMA,
            binding=binding,
            binding_sha256=binding.binding_sha256,
            references_sha256=binding.references_sha256,
        )
        try:
            receipt = await registration.runtime.prepare(request)
            _validate_qwen_prepare_receipt(receipt, request)
        except TeacherVoicePackError:
            raise
        except Exception as exc:
            _fail(
                "VOICE_PACK_RUNTIME_PREPARE_FAILED",
                "Qwen runtime could not prepare the active signed voice pack",
                status_code=503,
                details={"error": f"{type(exc).__name__}:{exc}"},
            )
        with self._state_guard:
            if generation != self._prepare_generation:
                _fail(
                    "VOICE_PACK_RUNTIME_PREPARE_SUPERSEDED",
                    "a newer Qwen pack prepare superseded this request",
                    status_code=409,
                )
            self._prepared_binding = binding
            self._prepared_registration = registration
        return {
            "schema": QWEN_BASE_CLONE_PREPARE_RESULT_SCHEMA,
            "prepared": True,
            "cached": False,
            "binding": qwen_base_clone_binding_contract(binding),
            "request": _qwen_prepare_request_contract(request),
            "runtime_receipt": _qwen_prepare_receipt_contract(receipt),
        }

    async def synthesize(
        self,
        pack: Mapping[str, Any],
        *,
        text: str,
        style: str,
        reply_kind: str,
        installed_root: Path,
    ) -> SynthesizedTeacherVoice:
        with self._state_guard:
            prepared = self._prepared_binding
            registration = self._prepared_registration
            generation = self._prepare_generation
        if prepared is None or registration is None:
            _fail(
                "VOICE_PACK_RUNTIME_NOT_PREPARED",
                "Qwen runtime has not prepared the active signed voice pack",
                status_code=503,
            )
        current = _qwen_pack_binding(pack, installed_root, prepared.manifest_sha256)
        current_registration = _qwen_runtime_registration(current.engine)
        if current != prepared or current_registration is not registration:
            self._clear_if_generation(generation)
            _fail(
                "VOICE_PACK_RUNTIME_BINDING_MISMATCH",
                "Qwen runtime binding changed before synthesis",
                status_code=409,
            )
        request = _qwen_synthesis_request(
            current,
            text=text,
            style=style,
            reply_kind=reply_kind,
        )
        try:
            result = await registration.runtime.synthesize(request)
        except TeacherVoicePackError:
            self._clear_if_generation(generation)
            raise
        except Exception as exc:
            self._clear_if_generation(generation)
            _fail(
                "VOICE_PACK_RUNTIME_UNAVAILABLE",
                "Qwen runtime synthesis failed",
                status_code=503,
                details={"error": f"{type(exc).__name__}:{exc}"},
            )
        with self._state_guard:
            if (
                generation != self._prepare_generation
                or self._prepared_binding != current
                or self._prepared_registration is not registration
            ):
                _fail(
                    "VOICE_PACK_RUNTIME_SYNTHESIS_SUPERSEDED",
                    "a Qwen pack switch superseded this synthesis request",
                    status_code=409,
                )
        if (
            not isinstance(result, QwenBaseCloneRuntimeAudio)
            or result.schema != QWEN_BASE_CLONE_SYNTHESIS_SCHEMA
            or result.engine_kind != request.engine_kind
            or result.engine_catalog_id != request.engine_catalog_id
            or result.engine_version != request.engine_version
            or result.engine_manifest_sha256 != request.engine_manifest_sha256
            or result.pack_id != request.pack_id
            or result.pack_version != request.pack_version
            or result.pack_manifest_sha256 != request.pack_manifest_sha256
            or result.binding_sha256 != request.binding_sha256
            or result.style != request.style
            or result.reference_audio_sha256 != request.reference_audio_sha256
            or result.reference_transcript_sha256 != request.reference_transcript_sha256
            or result.request_text_sha256 != request.text_sha256
            or result.reply_kind != request.reply_kind
            or result.request_sha256 != request.request_sha256
        ):
            self._clear_if_generation(generation)
            _fail(
                "VOICE_PACK_RUNTIME_BINDING_MISMATCH",
                "Qwen runtime synthesis result did not match the complete request binding",
                status_code=502,
            )
        try:
            audio = bytes(result.audio)
        except (TypeError, ValueError) as exc:
            self._clear_if_generation(generation)
            _fail(
                "VOICE_PACK_RUNTIME_INVALID_AUDIO",
                "Qwen runtime returned non-byte audio",
                status_code=502,
                details={"error": f"{type(exc).__name__}:{exc}"},
            )
        media_type = str(result.media_type or "").split(";", 1)[0].strip().lower()
        audio_sha256 = hashlib.sha256(audio).hexdigest()
        if (
            media_type not in {"audio/wav", "audio/x-wav", "audio/wave"}
            or result.audio_bytes != len(audio)
            or result.audio_sha256 != audio_sha256
        ):
            self._clear_if_generation(generation)
            _fail(
                "VOICE_PACK_RUNTIME_INVALID_AUDIO",
                "Qwen runtime audio bytes or digest did not match its receipt",
                status_code=502,
            )
        try:
            _validate_pcm_wav_bytes(audio, "Qwen runtime audio")
        except TeacherVoicePackError as exc:
            self._clear_if_generation(generation)
            _fail(
                "VOICE_PACK_RUNTIME_INVALID_AUDIO",
                "Qwen runtime returned invalid PCM WAV audio",
                status_code=502,
                details={"validation_error": exc.code},
            )
        return SynthesizedTeacherVoice(
            audio=audio,
            media_type="audio/wav",
            engine=QWEN_BASE_CLONE_ENGINE_KIND,
            profile=str(result.profile or style),
            cache=str(result.cache or "unknown"),
            scope_receipt=qwen_base_clone_audio_scope_contract(
                binding=current,
                request=request,
                result=result,
                audio=audio,
            ),
        )


class QwenCustomPrivateVoicePackAdapter:
    """Lazy direct-subprocess adapter for the private, non-exportable model."""

    def __init__(self) -> None:
        self._prepared: tuple[str, str, str, Path] | None = None

    def status(self, pack: Mapping[str, Any]) -> Mapping[str, Any]:
        from .qwen_custom_private_runtime import runtime_status

        return runtime_status()

    async def prepare(
        self,
        pack: Mapping[str, Any],
        *,
        installed_root: Path,
        manifest_sha256: str,
    ) -> Mapping[str, Any]:
        # The active-pack boundary already rehashed every signed file.  Prepare
        # records only that exact installed binding and never loads the model.
        config = _read_json(installed_root / "model" / "config.json")
        talker = config.get("talker_config") if isinstance(config, Mapping) else None
        if not isinstance(talker, Mapping) or talker.get("spk_id", {}).get("tsubaki_teacher") != 3000:
            _fail("VOICE_PACK_PRIVATE_MODEL_INVALID", "installed private speaker slot is invalid")
        self._prepared = (
            str(pack["pack_id"]), str(pack["version"]), manifest_sha256,
            installed_root.resolve(strict=True),
        )
        from .qwen_custom_private_runtime import PrivateQwenRuntimeError, get_or_create_runtime
        try:
            runtime, binding = await asyncio.to_thread(
                get_or_create_runtime, pack, installed_root, manifest_sha256,
            )
            receipt = await runtime.prepare(binding)
        except (PrivateQwenRuntimeError, LocalVoiceRuntimeError) as exc:
            self._prepared = None
            _fail("VOICE_PACK_PRIVATE_RUNTIME_FAILED", "private custom voice runtime prepare failed", status_code=503, details={"error": str(exc)})
        return {
            "schema": "mentorflow.qwen-custom-private-prepare.v1",
            "pack_id": pack["pack_id"], "version": pack["version"],
            "manifest_sha256": manifest_sha256,
            "model_loaded": receipt.get("model_loaded") is True,
        }

    async def synthesize(
        self,
        pack: Mapping[str, Any],
        *,
        text: str,
        style: str,
        reply_kind: str,
        installed_root: Path,
    ) -> SynthesizedTeacherVoice:
        if style not in REQUIRED_STYLES:
            _fail("VOICE_PACK_STYLE_INVALID", "private voice style must be lively or relaxed")
        current_root = installed_root.resolve(strict=True)
        if self._prepared is None or self._prepared[:2] != (str(pack["pack_id"]), str(pack["version"])) or self._prepared[3] != current_root:
            _fail("VOICE_PACK_RUNTIME_NOT_PREPARED", "private voice binding is not prepared", status_code=409)
        from .qwen_custom_private_runtime import PrivateQwenRuntimeError, get_or_create_runtime

        try:
            runtime, binding = await asyncio.to_thread(
                get_or_create_runtime, pack, current_root, self._prepared[2],
            )
            audio, receipt = await runtime.synthesize(
                binding, text=text, style=style, reply_kind=reply_kind,
            )
        except (PrivateQwenRuntimeError, LocalVoiceRuntimeError) as exc:
            self._prepared = None
            _fail("VOICE_PACK_PRIVATE_RUNTIME_FAILED", "private custom voice runtime failed", status_code=503, details={"error": str(exc)})
        _validate_pcm_wav_bytes(audio, "private custom voice output")
        return SynthesizedTeacherVoice(
            audio=audio,
            media_type="audio/wav",
            engine="qwen3-tts-0.6b-custom-private",
            profile="neutral",
            cache="request-generation",
            scope_receipt={
                "schema": "mentorflow.private-local-audio-scope.v1",
                "scope": "private_local_only_nonexportable",
                "pack_id": pack["pack_id"], "pack_version": pack["version"],
                "loaded_model_sha256": receipt["loaded_model_sha256"],
                "speaker_slot_exact": receipt["speaker_slot_exact"],
                "network_used": receipt["network_used"],
                "service_scope": receipt["service_scope"],
                "model_load_count": receipt["model_load_count"],
            },
        )


_RUNTIME_ADAPTERS: dict[str, TeacherVoiceRuntimeAdapter] = {
    "mentorflow.gpt-sovits.v1": GptSovitsLoopbackAdapter(),
    QWEN_BASE_CLONE_ADAPTER_ID: QwenBaseCloneVoicePackAdapter(),
    QWEN_CUSTOM_PRIVATE_ADAPTER_ID: QwenCustomPrivateVoicePackAdapter(),
}
_RUNTIME_ADAPTER_LOCKS: dict[str, asyncio.Lock] = {
    "mentorflow.gpt-sovits.v1": asyncio.Lock(),
    QWEN_BASE_CLONE_ADAPTER_ID: asyncio.Lock(),
    QWEN_CUSTOM_PRIVATE_ADAPTER_ID: asyncio.Lock(),
}


def register_teacher_voice_runtime_adapter(adapter_id: str, adapter: TeacherVoiceRuntimeAdapter) -> None:
    """Register application code, never manifest-supplied commands or imports."""
    safe_adapter_id = _safe_id(adapter_id, "adapter_id")
    _RUNTIME_ADAPTERS[safe_adapter_id] = adapter
    _RUNTIME_ADAPTER_LOCKS[safe_adapter_id] = asyncio.Lock()


def _active_manifest(
    *, verify_files: bool = False
) -> tuple[dict[str, Any], Path, str] | None:
    global _ACTIVE_VERIFIED_CACHE
    catalog_file, catalog = _load_catalog()
    root = _pack_root()
    active = _active_reference(root)
    if active is None:
        return None
    entry = _catalog_entry(catalog, active["pack_id"], active["version"])
    installed_root = _installed_path(root, active["pack_id"], active["version"])
    if verify_files:
        with _ACTIVE_VERIFIED_CACHE_LOCK:
            cached = _ACTIVE_VERIFIED_CACHE
        if cached is not None:
            try:
                cached_manifest = cached["manifest"]
                identity_paths = [
                    catalog_file, _active_path(root),
                    installed_root / "voice-pack.json", installed_root / "voice-pack.sig",
                    installed_root / ".mentorflow-install.json",
                    *[
                        installed_root / _safe_relative(item.get("path"), "files[].path")
                        for item in cached_manifest.get("files", [])
                        if isinstance(item, Mapping)
                    ],
                ]
                identities = tuple(
                    (
                        str(path.resolve(strict=True)), int(path.stat().st_dev),
                        int(path.stat().st_ino), int(path.stat().st_size),
                        int(path.stat().st_mtime_ns),
                    )
                    for path in identity_paths
                    if not _is_reparse_point(path) and path.is_file()
                )
                if (
                    len(identities) == len(identity_paths)
                    and cached.get("active") == (
                        active["pack_id"], active["version"], active["manifest_sha256"]
                    )
                    and cached.get("installed_root") == installed_root
                    and cached.get("identities") == identities
                ):
                    return cached_manifest, installed_root, active["manifest_sha256"]
            except (OSError, TeacherVoicePackError):
                pass
    manifest = _load_installed(root, entry, catalog, verify_files=verify_files)
    current_manifest_hash = _hash_file(installed_root / "voice-pack.json")
    if current_manifest_hash != active["manifest_sha256"]:
        _fail(
            "VOICE_PACK_ACTIVE_MANIFEST_MISMATCH",
            "active voice pack manifest no longer matches the activated release",
            status_code=409,
        )
    if verify_files:
        identity_paths = [
            catalog_file, _active_path(root),
            installed_root / "voice-pack.json", installed_root / "voice-pack.sig",
            installed_root / ".mentorflow-install.json",
            *[
                installed_root / _safe_relative(item.get("path"), "files[].path")
                for item in manifest.get("files", [])
                if isinstance(item, Mapping)
            ],
        ]
        identities = tuple(
            (
                str(path.resolve(strict=True)), int(path.stat().st_dev),
                int(path.stat().st_ino), int(path.stat().st_size),
                int(path.stat().st_mtime_ns),
            )
            for path in identity_paths
            if not _is_reparse_point(path) and path.is_file()
        )
        if len(identities) != len(identity_paths):
            _fail("VOICE_PACK_FILE_MISSING", "active voice pack identity snapshot is incomplete", status_code=409)
        with _ACTIVE_VERIFIED_CACHE_LOCK:
            _ACTIVE_VERIFIED_CACHE = {
                "active": (active["pack_id"], active["version"], active["manifest_sha256"]),
                "installed_root": installed_root,
                "identities": identities,
                "manifest": manifest,
            }
    return manifest, installed_root, current_manifest_hash


def get_teacher_voice_status() -> dict[str, Any]:
    """Read-only status.  It never creates state or asks an adapter to start."""
    active = _active_manifest(verify_files=False)
    active_public: dict[str, Any] | None = None
    runtime = {"state": "unavailable", "starts_model": False}
    if active is not None:
        manifest, _root, manifest_hash = active
        active_public = _public_pack(manifest, state="active")
        adapter_id = str(_mapping(manifest.get("runtime"), "runtime").get("adapter"))
        runtime = dict(_RUNTIME_ADAPTERS[adapter_id].status(manifest))
        runtime["adapter"] = adapter_id
        runtime["manifest_sha256"] = manifest_hash
    return {
        "schema": "mentorflow.teacher-voice-status.v1",
        "pack_root": str(_pack_root()),
        "fallback_policy": get_voice_pack_settings()["fallback_policy"],
        "active_pack": active_public,
        "runtime": runtime,
        "packs": list_voice_packs(),
    }


async def synthesize_active_teacher_voice(
    *, text: str, style: str, reply_kind: str = ""
) -> tuple[SynthesizedTeacherVoice, dict[str, Any]]:
    # Activation performs the expensive full signed-payload verification.
    # Warm clicks reuse that immutable snapshot only while every catalog,
    # active-state, manifest, receipt and declared-file identity is unchanged.
    active = _active_manifest(verify_files=True)
    if active is None:
        settings = get_voice_pack_settings()
        _fail(
            "VOICE_PACK_NOT_INSTALLED",
            "尚未安装并激活本地教师语音包",
            status_code=503,
            details={"fallback_policy": settings["fallback_policy"]},
        )
    manifest, installed_root, manifest_hash = active
    adapter_id = str(_mapping(manifest.get("runtime"), "runtime").get("adapter"))
    async with _RUNTIME_ADAPTER_LOCKS[adapter_id]:
        await _RUNTIME_ADAPTERS[adapter_id].prepare(
            manifest,
            installed_root=installed_root,
            manifest_sha256=manifest_hash,
        )
        result = await _RUNTIME_ADAPTERS[adapter_id].synthesize(
            manifest,
            text=text,
            style=style,
            reply_kind=reply_kind,
            installed_root=installed_root,
        )
    return result, _public_pack(manifest, state="active")


async def stream_active_teacher_voice(
    *, text: str, style: str, reply_kind: str = ""
) -> tuple[AsyncIterator[bytes], dict[str, Any]]:
    """Prepare the active pack and return a lock-guarded progressive stream."""
    active = _active_manifest(verify_files=True)
    if active is None:
        settings = get_voice_pack_settings()
        _fail(
            "VOICE_PACK_NOT_INSTALLED",
            "尚未安装并激活本地教师语音包",
            status_code=503,
            details={"fallback_policy": settings["fallback_policy"]},
        )
    manifest, installed_root, manifest_hash = active
    adapter_id = str(_mapping(manifest.get("runtime"), "runtime").get("adapter"))
    adapter = _RUNTIME_ADAPTERS[adapter_id]
    stream_method = getattr(adapter, "stream", None)
    if not callable(stream_method):
        _fail(
            "VOICE_PACK_STREAM_UNSUPPORTED",
            "当前教师声包运行时不支持流式朗读",
            status_code=501,
        )

    lock = _RUNTIME_ADAPTER_LOCKS[adapter_id]
    await lock.acquire()
    try:
        await adapter.prepare(
            manifest,
            installed_root=installed_root,
            manifest_sha256=manifest_hash,
        )
        upstream = stream_method(
            manifest,
            text=text,
            style=style,
            reply_kind=reply_kind,
            installed_root=installed_root,
        )
    except BaseException:
        lock.release()
        raise

    async def guarded_stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream:
                yield chunk
        finally:
            lock.release()

    return guarded_stream(), _public_pack(manifest, state="active")


router = APIRouter(prefix="/api/teacher-voice", tags=["teacher-voice"])


def _raise_http(exc: TeacherVoicePackError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=error_detail(exc)) from exc


def _request_version(body: dict[str, Any] | None) -> str | None:
    request = body or {}
    unexpected = sorted(set(request) - {"version"})
    if unexpected:
        _fail(
            "VOICE_PACK_REQUEST_INVALID",
            "install and activate requests may only select a catalog version",
            status_code=422,
            details={"unexpected_fields": unexpected},
        )
    return str(request.get("version") or "").strip() or None


@router.get("/status")
def teacher_voice_status_route() -> dict[str, Any]:
    try:
        return get_teacher_voice_status()
    except TeacherVoicePackError as exc:
        _raise_http(exc)


@router.get("/packs")
def teacher_voice_packs_route() -> dict[str, Any]:
    try:
        return {"packs": list_voice_packs()}
    except TeacherVoicePackError as exc:
        _raise_http(exc)


@router.get("/jobs/{job_id}")
def teacher_voice_job_route(job_id: str) -> dict[str, Any]:
    try:
        return get_job(job_id)
    except TeacherVoicePackError as exc:
        _raise_http(exc)


@router.post("/packs/{pack_id}/install")
def install_teacher_voice_pack_route(pack_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return install_voice_pack(pack_id, _request_version(body))
    except TeacherVoicePackError as exc:
        _raise_http(exc)


@router.post("/packs/{pack_id}/activate")
def activate_teacher_voice_pack_route(pack_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return activate_voice_pack(pack_id, _request_version(body))
    except TeacherVoicePackError as exc:
        _raise_http(exc)


@router.patch("/settings")
def teacher_voice_settings_route(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return update_voice_pack_settings(str(body.get("fallback_policy") or ""))
    except TeacherVoicePackError as exc:
        _raise_http(exc)


@router.get("/settings")
def get_teacher_voice_settings_route() -> dict[str, Any]:
    try:
        return get_voice_pack_settings()
    except TeacherVoicePackError as exc:
        _raise_http(exc)


@router.delete("/packs/{pack_id}/versions/{version}")
def delete_teacher_voice_pack_route(pack_id: str, version: str) -> dict[str, Any]:
    try:
        return delete_voice_pack(pack_id, version)
    except TeacherVoicePackError as exc:
        _raise_http(exc)
