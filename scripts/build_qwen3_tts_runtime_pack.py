#!/usr/bin/env python3
"""Build and verify a relocatable MentorFlow Qwen3-TTS runtime directory.

The command is intentionally non-mutating by default: supplying only
``--spec`` performs the same operation as ``--plan``.  ``--build`` is the
only mode that copies files.  Every copied byte must come from an explicit
component allowlist and every component must have a pinned tree digest,
provenance receipt, and redistributable-license evidence.

This builder does not download Python, models, wheels, or licenses and does
not start a TTS service.  A release engineer must prepare those inputs first.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import uuid
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlparse

try:
    from backend.app.plain_file_copy import (
        PlainFileCopyError,
        copy_plain_file_exact,
    )
    from backend.app.windows_install_path_budget import (
        LEGACY_VISIBLE_PATH_LIMIT_CHARS as WINDOWS_LEGACY_VISIBLE_PATH_LIMIT,
        SCHEMA as INSTALL_PATH_BUDGET_SCHEMA,
        WindowsInstallPathBudgetError,
        calculate_windows_install_path_budget,
        validate_windows_install_root_budget,
    )
    from backend.app.windows_safe_relative import (
        WindowsSafeRelativePathError,
        validate_windows_safe_relative,
        windows_casefold_key,
    )
except ModuleNotFoundError:  # Direct ``python scripts/build_*.py`` execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.app.plain_file_copy import (  # type: ignore[no-redef]
        PlainFileCopyError,
        copy_plain_file_exact,
    )
    from backend.app.windows_install_path_budget import (  # type: ignore[no-redef]
        LEGACY_VISIBLE_PATH_LIMIT_CHARS as WINDOWS_LEGACY_VISIBLE_PATH_LIMIT,
        SCHEMA as INSTALL_PATH_BUDGET_SCHEMA,
        WindowsInstallPathBudgetError,
        calculate_windows_install_path_budget,
        validate_windows_install_root_budget,
    )
    from backend.app.windows_safe_relative import (  # type: ignore[no-redef]
        WindowsSafeRelativePathError,
        validate_windows_safe_relative,
        windows_casefold_key,
    )


SPEC_SCHEMA = "mentorflow.qwen3-tts-runtime-build-spec.v1"
ENGINE_SCHEMA = "mentorflow.qwen-engine-manifest.v1"
ENGINE_KIND = "qwen3-tts-0.6b-base"
NOTICES_SCHEMA = "mentorflow.third-party-notices-index.v1"
PLATFORM = "win-x64"

TRUSTED_QWEN_SOURCE_REPOSITORY = "https://github.com/QwenLM/Qwen3-TTS"
TRUSTED_QWEN_SOURCE_REVISION = "022e286b98fbec7e1e916cb940cdf532cd9f488e"
TRUSTED_QWEN_LICENSE_SHA256 = "a44a6081c73ad75f0255bb2bb5cab74ef1829565a895a24e53a4f11290ab7655"
TRUSTED_QWEN_SOURCE_FILES = {
    "LICENSE": (11343, "a44a6081c73ad75f0255bb2bb5cab74ef1829565a895a24e53a4f11290ab7655"),
    "README.md": (60501, "51f4497e70e9ae024b10ee4bbcb470366f10592edeab56c58d2194ba2dadb745"),
    "pyproject.toml": (1099, "3e82fcb21b9d48bba789a7c0a2c2dcb88ad60c4d297d60ae4575ee3eb4e4d5aa"),
    "finetuning/README.md": (3165, "7a0bb1d3396ff4a41ff5958ba5db0b87547ac05df91874208d530e73e8be476f"),
    "finetuning/dataset.py": (8664, "fd15ee9e71822d9fc1cdf8eb6d6f042836d186e95de5920087d80828cf98a1cb"),
    "finetuning/prepare_data.py": (2410, "d5ed3611e85aadae0ade2b2ab4fa921bb0fdcd8dad8f62c518b75497fcb02b9c"),
    "finetuning/sft_12hz.py": (6885, "74d4359bff1ac5eddaca99f0cb5a8b76a55ffbd94af0ab50bffd49aee0e5c473"),
}

from backend.app.model_material_catalog import (
    TRUSTED_MODEL_REPOSITORY, TRUSTED_MODEL_REVISION,
    TRUSTED_MODEL_FILES, TRUSTED_MODEL_TREE_SHA256,
)
TRUSTED_WHEEL_HOSTS = {"files.pythonhosted.org", "download.pytorch.org"}
LICENSE_SUPPLEMENT_SCHEMA = "mentorflow.python-license-supplement-receipt.v1"
RUNTIME_TESTLIKE_TRACE_SCHEMA = "mentorflow.qwen-runtime-required-testlike-contract.v1"
SOURCE_OVERLAY_SCHEMA = "mentorflow.qwen-12hz-source-overlay.v1"
PORTABLE_PYTHON_RECEIPT_SCHEMA = "mentorflow.python-org-embeddable-source-receipt.v2"
PORTABLE_PYTHON_AUTHENTICODE_SCHEMA = "mentorflow.windows-authenticode-file-set.v1"
PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA = (
    "mentorflow.portable-python-material-inventory.v2"
)
PORTABLE_PYTHON_COMPONENT_SCHEMA = "mentorflow.portable-python-builder-component.v2"
RUNNER_SOURCE_RECEIPT_SCHEMA = "mentorflow.qwen-runner-source-receipt.v1"
TRUSTED_PORTABLE_PYTHON_VERSION = "3.12.10"
TRUSTED_PORTABLE_PYTHON_ARCHIVE_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
TRUSTED_PORTABLE_PYTHON_TREE_SHA256 = "d49c69b424a15abe8a1c3c2bbb74599d1257933c6dc388f7a70a8f4392838f27"
TRUSTED_PORTABLE_PYTHON_LICENSE_SHA256 = "e502c6b880ff58d614901495a9009c136539cd0b1e2a2abb8fc00b934c203419"
TRUSTED_PORTABLE_PYTHON_NOTICE_SHA256 = "e73738b44f7568f83b05ee0e1efe1134d78116194be06c31fa6111b8da4c83d3"
TRUSTED_PORTABLE_PYTHON_PUBLISHERS = frozenset(
    {"Python Software Foundation", "Microsoft Corporation"}
)
TRUSTED_QWEN_WHEEL_SHA256 = "11a290d8dabc7ef91a90c54478c8ab19b3edb1d85c0882313721892bdc4af15d"
TRUSTED_QWEN_OVERLAY_INPUTS = {
    "qwen_tts/core/__init__.py": "1b380d9de843b6d585d938c339d066136567ca7125412674234204af4386679e",
    "qwen_tts/inference/qwen3_tts_tokenizer.py": "ac2d855022a1bd21d33ab7b267ec952eef71f81d3f8d969a306139ff1a929515",
}
TRUSTED_RUNTIME_TESTLIKE_MEMBERS = frozenset(
    {
        "numpy/testing/__init__.py",
        "numpy/testing/_private/__init__.py",
        "numpy/testing/_private/extbuild.py",
        "numpy/testing/_private/utils.py",
        "numpy/testing/overrides.py",
        "scipy/stats/_bws_test.py",
        "scipy/stats/_page_trend_test.py",
        "torch/_inductor/test_operators.py",
        "torch/testing/__init__.py",
        "torch/testing/_comparison.py",
        "torch/testing/_creation.py",
        "torch/testing/_internal/__init__.py",
        "torch/testing/_internal/distributed/__init__.py",
        "torch/testing/_internal/distributed/fake_pg.py",
        "torch/testing/_internal/logging_tensor.py",
        "torch/testing/_utils.py",
    }
)

TARGET_ROOTS = {
    "portable_python": "runtime/python",
    "runner": "runtime/runner",
    "model": "runtime/models/qwen3-tts-12hz-0.6b-base",
    "dependency": "runtime/python/Lib/site-packages",
}

FORBIDDEN_SEGMENTS = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    "__pycache__",
    "cache",
    "caches",
    "finetuning",
    "gradio",
    "gradio_client",
    "hf-gradio",
    "test",
    "tests",
    "training",
}
FORBIDDEN_FILENAMES = {
    ".env",
    "cachedir.tag",
    "pyvenv.cfg",
}
TEXT_SUFFIXES = {
    ".cfg",
    ".cmd",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".pth",
    ".py",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")
PACK_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
WINDOWS_ABSOLUTE = re.compile(r"(?i)(?:^|[\"'\s])(?:[a-z]:\\|\\\\)")
USER_HOME_TEXT = re.compile(r"(?i)(?:[a-z]:\\users\\[^\\\s]+|/home/[^/\s]+)")
SECRET_TEXT = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{16,}|(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[\"']?[a-z0-9_./+=-]{16,})"
)
class BuildError(RuntimeError):
    """Fail-closed validation error with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FileRecord:
    component_id: str
    kind: str
    source: Path
    source_relative: str
    target: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ComponentPlan:
    component: dict[str, Any]
    files: tuple[FileRecord, ...]
    tree_sha256: str
    license_records: tuple[dict[str, Any], ...]
    receipt_record: dict[str, Any]
    license_supplement_record: dict[str, Any] | None
    wheel_exclusions: tuple[dict[str, Any], ...]
    runtime_required_testlike: tuple[dict[str, Any], ...]
    runtime_import_trace_record: dict[str, Any] | None
    source_overlay_record: dict[str, Any] | None


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise BuildError("SOURCE_READ_FAILED", f"cannot read {path}: {exc}") from exc
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")


def _safe_relative(value: Any, *, field: str) -> str:
    try:
        return validate_windows_safe_relative(value).as_posix()
    except WindowsSafeRelativePathError as exc:
        text = value if isinstance(value, str) else ""
        structural = (
            not text
            or "\\" in text
            or text.startswith("/")
            or text.endswith("/")
            or "//" in text
            or any(part in {"", ".", ".."} for part in text.split("/"))
        )
        raise BuildError(
            "UNSAFE_PATH" if structural else "UNSAFE_WINDOWS_PATH",
            f"{field} is not an exact Windows-safe relative POSIX path: {value!r} ({exc})",
        ) from exc


def _safe_pack_token(value: Any, *, field: str) -> str:
    token = str(value or "")
    if not PACK_TOKEN.fullmatch(token):
        raise BuildError("UNSAFE_ID", f"{field} must be a stable lowercase token")
    _safe_relative(token, field=field)
    return token


def calculate_install_path_budget(relative_paths: Iterable[str]) -> dict[str, Any]:
    """Return the classic-Windows-safe root budget for an exact file set."""
    try:
        return calculate_windows_install_path_budget(relative_paths)
    except WindowsInstallPathBudgetError as exc:
        raise BuildError(exc.code, str(exc)) from exc


def validate_install_root_budget(
    install_root: Path,
    path_budget: Mapping[str, Any],
    *,
    relative_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Recompute an optional declaration and reject roots beyond its budget."""

    try:
        return validate_windows_install_root_budget(
            install_root,
            path_budget,
            relative_paths=relative_paths,
        )
    except WindowsInstallPathBudgetError as exc:
        raise BuildError(exc.code, str(exc)) from exc


def _is_testlike_member_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    parts = tuple(part.casefold() for part in path.parts)
    filename = path.name.casefold()
    return any(
        part in {"test", "tests", "testing"} or part.startswith("test_")
        for part in parts
    ) or filename.startswith("test_") or filename.endswith("_test.py")


def _is_locked_runtime_testlike_path(relative: str) -> bool:
    normalized = PurePosixPath(relative).as_posix()
    prefix = "runtime/python/Lib/site-packages/"
    member = normalized[len(prefix):] if normalized.startswith(prefix) else normalized
    return member in TRUSTED_RUNTIME_TESTLIKE_MEMBERS


def _validate_payload_path(relative: str, *, allow_exact_testlike: bool = False) -> None:
    allow_exact_testlike = allow_exact_testlike or _is_locked_runtime_testlike_path(relative)
    parts = tuple(part.casefold() for part in PurePosixPath(relative).parts)
    forbidden = {part for part in parts if part in FORBIDDEN_SEGMENTS}
    if forbidden and not (
        allow_exact_testlike
        and forbidden.issubset({"test", "tests"})
        and _is_testlike_member_path(relative)
    ):
        raise BuildError("FORBIDDEN_PAYLOAD_PATH", f"forbidden directory in payload path: {relative}")
    name = parts[-1]
    if name in FORBIDDEN_FILENAMES or (
        (name.startswith("test_") or name.endswith("_test.py"))
        and not allow_exact_testlike
    ):
        raise BuildError("FORBIDDEN_PAYLOAD_PATH", f"forbidden file in payload path: {relative}")
    allowed_public_ca_bundle = (
        relative.casefold()
        in {
            "certifi/cacert.pem",
            "runtime/python/lib/site-packages/certifi/cacert.pem",
        }
    )
    if name.endswith((".pem", ".key", ".pfx", ".p12")) and not allowed_public_ca_bundle:
        raise BuildError("PRIVATE_MATERIAL_FORBIDDEN", f"key-like file cannot be packaged: {relative}")


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError as exc:
        raise BuildError("SOURCE_STAT_FAILED", f"cannot stat {path}: {exc}") from exc
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _is_hardlink(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_nlink > 1
    except OSError as exc:
        raise BuildError("SOURCE_STAT_FAILED", f"cannot stat {path}: {exc}") from exc


def _resolve_external(spec_dir: Path, value: Any, *, field: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise BuildError("MISSING_PATH", f"{field} is required")
    path = Path(raw)
    if not path.is_absolute():
        path = spec_dir / path
    return path.absolute()


def _ensure_plain_file(path: Path, *, code: str) -> None:
    if not path.is_file():
        raise BuildError(code, f"required file is missing: {path}")
    if _is_reparse_point(path):
        raise BuildError("REPARSE_POINT_FORBIDDEN", f"reparse point is forbidden: {path}")
    if _is_hardlink(path):
        raise BuildError("HARDLINK_FORBIDDEN", f"hardlink is forbidden: {path}")


def _read_pinned_file(
    spec_dir: Path,
    declaration: Any,
    *,
    field: str,
) -> tuple[Path, bytes, str]:
    if not isinstance(declaration, dict):
        raise BuildError("MISSING_EVIDENCE", f"{field} must be an object")
    path = _resolve_external(spec_dir, declaration.get("path"), field=f"{field}.path")
    _ensure_plain_file(path, code="MISSING_EVIDENCE")
    expected = str(declaration.get("sha256") or "").strip().casefold()
    if not HEX64.fullmatch(expected):
        raise BuildError("MISSING_EVIDENCE_HASH", f"{field}.sha256 must be a lowercase SHA256")
    content = path.read_bytes()
    actual = _sha256_bytes(content)
    if actual != expected:
        raise BuildError("EVIDENCE_HASH_MISMATCH", f"{field} hash mismatch: expected {expected}, got {actual}")
    return path, content, actual


def _scan_evidence_content(content: bytes, *, field: str, reject_absolute_windows_path: bool) -> None:
    if len(content) > 4 * 1024 * 1024:
        raise BuildError("EVIDENCE_TOO_LARGE", f"evidence file is unexpectedly large: {field}")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError("EVIDENCE_NOT_UTF8", f"evidence must be UTF-8 text: {field}") from exc
    if USER_HOME_TEXT.search(text):
        raise BuildError("USER_PATH_FORBIDDEN", f"user-home path embedded in {field}")
    if SECRET_TEXT.search(text):
        raise BuildError("SECRET_FORBIDDEN", f"secret-like value embedded in {field}")
    if reject_absolute_windows_path and WINDOWS_ABSOLUTE.search(text):
        raise BuildError("ABSOLUTE_RECEIPT_PATH", f"absolute Windows path embedded in {field}")


def _parse_runtime_testlike_entries(raw: Any, *, component_id: str) -> tuple[dict[str, Any], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise BuildError(
            "RUNTIME_TESTLIKE_INVALID",
            f"{component_id}.runtime_required_testlike must be a non-empty list",
        )
    parsed = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256", "reason"}:
            raise BuildError(
                "RUNTIME_TESTLIKE_INVALID",
                f"{component_id}.runtime_required_testlike[{index}] fields are invalid",
            )
        path = _safe_relative(
            item.get("path"),
            field=f"{component_id}.runtime_required_testlike[{index}].path",
        )
        size = item.get("bytes")
        digest = str(item.get("sha256") or "")
        reason = item.get("reason")
        folded = windows_casefold_key(path)
        if (
            reason != "runtime-import-trace"
            or not _is_testlike_member_path(path)
            or path not in TRUSTED_RUNTIME_TESTLIKE_MEMBERS
            or not isinstance(size, int)
            or size < 0
            or not HEX64.fullmatch(digest)
            or folded in seen
        ):
            raise BuildError(
                "RUNTIME_TESTLIKE_NOT_ALLOWED",
                f"{component_id} runtime test-like member is not an exact traced file: {path}",
            )
        seen.add(folded)
        parsed.append({"path": path, "bytes": size, "sha256": digest, "reason": reason})
    if parsed != sorted(parsed, key=lambda item: item["path"]):
        raise BuildError(
            "RUNTIME_TESTLIKE_INVALID",
            f"{component_id} runtime test-like members must be path-sorted",
        )
    return tuple(parsed)


def _read_runtime_testlike_trace(
    spec_dir: Path,
    declaration: Any,
    *,
    component_id: str,
) -> dict[str, Any]:
    path, content, digest = _read_pinned_file(
        spec_dir,
        declaration,
        field=f"{component_id}.runtime_import_trace",
    )
    _scan_evidence_content(
        content,
        field=f"{component_id}.runtime_import_trace",
        reject_absolute_windows_path=True,
    )
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(
            "RUNTIME_TESTLIKE_TRACE_INVALID",
            f"runtime import trace must be UTF-8 JSON for {component_id}",
        ) from exc
    expected_fields = {"schema", "source_trace", "scope", "safety", "entries"}
    source_fields = {"schema", "sha256"}
    safety_fields = {
        "network_connect_attempts",
        "listener_attempts",
        "subprocess_attempts",
        "model_loaded",
        "tree_modified",
        "cuda_api_called",
        "sox_probe_suppressed",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_fields
        or payload.get("schema") != RUNTIME_TESTLIKE_TRACE_SCHEMA
        or payload.get("scope") != "guarded official-wheel runtime import closure"
        or not isinstance(payload.get("source_trace"), dict)
        or set(payload["source_trace"]) != source_fields
        or payload["source_trace"].get("schema")
        != "mentorflow.qwen-runtime-testlike-import-trace.v1"
        or not HEX64.fullmatch(str(payload["source_trace"].get("sha256") or ""))
        or not isinstance(payload.get("safety"), dict)
        or set(payload["safety"]) != safety_fields
        or payload["safety"]
        != {
            "network_connect_attempts": 0,
            "listener_attempts": 1,
            "subprocess_attempts": 0,
            "model_loaded": False,
            "tree_modified": False,
            "cuda_api_called": False,
            "sox_probe_suppressed": True,
        }
        or not isinstance(payload.get("entries"), list)
        or not payload["entries"]
    ):
        raise BuildError(
            "RUNTIME_TESTLIKE_TRACE_INVALID",
            f"runtime import trace contract is invalid for {component_id}",
        )
    parsed_entries = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(payload["entries"]):
        fields = {"package", "path", "bytes", "sha256", "wheel_sha256", "modules", "stages"}
        if not isinstance(item, dict) or set(item) != fields:
            raise BuildError(
                "RUNTIME_TESTLIKE_TRACE_INVALID",
                f"runtime import trace entry fields are invalid at {index}",
            )
        package = _safe_pack_token(item.get("package"), field=f"runtime_trace.entries[{index}].package")
        member = _safe_relative(item.get("path"), field=f"runtime_trace.entries[{index}].path")
        size = item.get("bytes")
        member_hash = str(item.get("sha256") or "")
        wheel_hash = str(item.get("wheel_sha256") or "")
        modules = item.get("modules")
        stages = item.get("stages")
        key = (package, member)
        if (
            not _is_testlike_member_path(member)
            or member not in TRUSTED_RUNTIME_TESTLIKE_MEMBERS
            or not isinstance(size, int)
            or size < 0
            or not HEX64.fullmatch(member_hash)
            or not HEX64.fullmatch(wheel_hash)
            or not isinstance(modules, list)
            or not modules
            or modules != sorted(set(modules))
            or not all(isinstance(module, str) and re.fullmatch(r"[A-Za-z0-9_.]+", module) for module in modules)
            or not isinstance(stages, list)
            or not stages
            or stages != sorted(set(stages))
            or not all(stage in {"torch", "accelerate", "transformers", "qwen_tts", "runner"} for stage in stages)
            or key in seen
        ):
            raise BuildError(
                "RUNTIME_TESTLIKE_TRACE_INVALID",
                f"runtime import trace entry is invalid at {index}: {member}",
            )
        seen.add(key)
        parsed_entries.append(item)
    if parsed_entries != sorted(parsed_entries, key=lambda item: (item["package"], item["path"])):
        raise BuildError("RUNTIME_TESTLIKE_TRACE_INVALID", "runtime import trace entries must be package/path sorted")
    return {"source": path, "content": content, "sha256": digest, "payload": payload}


def _read_source_overlay_contract(
    spec_dir: Path,
    declaration: Any,
    *,
    component_id: str,
) -> dict[str, Any]:
    path, content, digest = _read_pinned_file(
        spec_dir,
        declaration,
        field=f"{component_id}.source_overlay",
    )
    _scan_evidence_content(
        content,
        field=f"{component_id}.source_overlay",
        reject_absolute_windows_path=True,
    )
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("SOURCE_OVERLAY_INVALID", "source overlay contract must be UTF-8 JSON") from exc
    expected_fields = {
        "schema",
        "policy",
        "application",
        "upstream_wheel",
        "transforms",
        "model_identity",
        "forbidden_imports",
        "upstream_wheel_bytes_modified",
    }
    wheel = payload.get("upstream_wheel") if isinstance(payload, dict) else None
    transforms = payload.get("transforms") if isinstance(payload, dict) else None
    identity = payload.get("model_identity") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_fields
        or payload.get("schema") != SOURCE_OVERLAY_SCHEMA
        or payload.get("policy") != "qwen3-tts-12hz-only-no-sox"
        or payload.get("application") != "verified-in-memory-import-loader"
        or payload.get("upstream_wheel_bytes_modified") is not False
        or not isinstance(wheel, dict)
        or wheel
        != {"name": "qwen-tts", "version": "0.1.1", "sha256": TRUSTED_QWEN_WHEEL_SHA256}
        or not isinstance(transforms, list)
        or len(transforms) != 2
        or not isinstance(identity, dict)
        or payload.get("forbidden_imports")
        != ["qwen_tts.core.tokenizer_25hz", "sox"]
    ):
        raise BuildError("SOURCE_OVERLAY_INVALID", "source overlay contract identity or fields are invalid")
    expected_operations = {
        "qwen_tts/core/__init__.py": ["remove-v1-imports"],
        "qwen_tts/inference/qwen3_tts_tokenizer.py": [
            "remove-v1-imports",
            "remove-v1-registrations",
            "require-12hz-config-before-load",
            "require-12hz-model-after-load",
            "require-12hz-encode-decode",
        ],
    }
    parsed_paths = set()
    for index, transform in enumerate(transforms):
        fields = {"module", "path", "input_sha256", "output_sha256", "patch_sha256", "operations"}
        if not isinstance(transform, dict) or set(transform) != fields:
            raise BuildError("SOURCE_OVERLAY_INVALID", f"source overlay transform {index} fields are invalid")
        relative = _safe_relative(transform.get("path"), field=f"source_overlay.transforms[{index}].path")
        expected_input = TRUSTED_QWEN_OVERLAY_INPUTS.get(relative)
        expected_module = relative.removesuffix("/__init__.py").removesuffix(".py").replace("/", ".")
        if (
            expected_input is None
            or transform.get("module") != expected_module
            or transform.get("input_sha256") != expected_input
            or not HEX64.fullmatch(str(transform.get("output_sha256") or ""))
            or not HEX64.fullmatch(str(transform.get("patch_sha256") or ""))
            or transform.get("operations") != expected_operations[relative]
            or relative in parsed_paths
        ):
            raise BuildError("SOURCE_OVERLAY_INVALID", f"source overlay transform {index} is not the locked patch")
        parsed_paths.add(relative)
    if parsed_paths != set(TRUSTED_QWEN_OVERLAY_INPUTS):
        raise BuildError("SOURCE_OVERLAY_INVALID", "source overlay must contain exactly two locked transforms")
    expected_identity = {
        "main_config": {
            "path": "config.json",
            "sha256": TRUSTED_MODEL_FILES["config.json"][1],
            "required_fields": {
                "model_type": "qwen3_tts",
                "tokenizer_type": "qwen3_tts_tokenizer_12hz",
                "tts_model_size": "0b6",
                "tts_model_type": "base",
            },
        },
        "speech_tokenizer_config": {
            "path": "speech_tokenizer/config.json",
            "sha256": TRUSTED_MODEL_FILES["speech_tokenizer/config.json"][1],
            "required_fields": {"model_type": "qwen3_tts_tokenizer_12hz"},
        },
    }
    if identity != expected_identity:
        raise BuildError("SOURCE_OVERLAY_INVALID", "source overlay model identity gates are invalid")
    return {"source": path, "content": content, "sha256": digest, "payload": payload}


def _receipt_file_map(payload: dict[str, Any], *, field: str) -> dict[str, tuple[int, str]]:
    rows = payload.get("files")
    if not isinstance(rows, list) or not rows:
        raise BuildError("PROVENANCE_RECEIPT_INVALID", f"{field}.files must be a non-empty list")
    result: dict[str, tuple[int, str]] = {}
    casefold_paths: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BuildError("PROVENANCE_RECEIPT_INVALID", f"{field}.files[{index}] must be an object")
        relative = _safe_relative(row.get("path"), field=f"{field}.files[{index}].path")
        size = row.get("bytes")
        digest = str(row.get("sha256") or "").casefold()
        if not isinstance(size, int) or size < 0 or not HEX64.fullmatch(digest):
            raise BuildError("PROVENANCE_RECEIPT_INVALID", f"invalid bytes/SHA256 in {field}.files[{index}]")
        if windows_casefold_key(relative) in casefold_paths:
            raise BuildError("PROVENANCE_RECEIPT_INVALID", f"duplicate path in {field}: {relative}")
        casefold_paths.add(windows_casefold_key(relative))
        result[relative] = (size, digest)
    return result


def _validate_trusted_source_receipt(spec_dir: Path, declaration: Any) -> dict[str, Any]:
    path, content, digest = _read_pinned_file(
        spec_dir,
        declaration,
        field="trusted_qwen_source_receipt",
    )
    _scan_evidence_content(
        content,
        field="trusted_qwen_source_receipt",
        reject_absolute_windows_path=True,
    )
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("TRUSTED_SOURCE_RECEIPT_INVALID", "trusted Qwen source receipt is invalid JSON") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "mentorflow.pinned-source-receipt.v1"
        or payload.get("repository") != TRUSTED_QWEN_SOURCE_REPOSITORY
        or payload.get("commit") != TRUSTED_QWEN_SOURCE_REVISION
    ):
        raise BuildError("TRUSTED_SOURCE_RECEIPT_INVALID", "Qwen source repository/commit/schema is not trusted")
    if _receipt_file_map(payload, field="trusted_qwen_source_receipt") != TRUSTED_QWEN_SOURCE_FILES:
        raise BuildError("TRUSTED_SOURCE_RECEIPT_INVALID", "Qwen source receipt file list does not match the built-in trust set")
    return {"source": path, "content": content, "sha256": digest, "payload": payload}


def _canonical_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.strip()).casefold()


def _canonical_pypi_purl(name: str, version: str) -> str:
    return f"pkg:pypi/{_canonical_distribution_name(name)}@{quote(version, safe='.')}"


def _walk_allowlist_entry(root: Path, entry: str) -> Iterable[tuple[Path, str]]:
    candidate = (root / Path(*PurePosixPath(entry).parts)).absolute()
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise BuildError("SOURCE_ESCAPE", f"allowlist entry escapes or is missing: {entry}") from exc
    if _is_reparse_point(candidate):
        raise BuildError("REPARSE_POINT_FORBIDDEN", f"reparse point is forbidden: {candidate}")
    if candidate.is_file():
        if _is_hardlink(candidate):
            raise BuildError("HARDLINK_FORBIDDEN", f"hardlink is forbidden: {candidate}")
        relative = _safe_relative(candidate.relative_to(root).as_posix(), field="source.relative_path")
        yield candidate, relative
        return
    if not candidate.is_dir():
        raise BuildError("SOURCE_MISSING", f"allowlist entry is not a file or directory: {candidate}")
    for current, directories, filenames in os.walk(candidate, topdown=True, followlinks=False):
        current_path = Path(current)
        if _is_reparse_point(current_path):
            raise BuildError("REPARSE_POINT_FORBIDDEN", f"reparse point is forbidden: {current_path}")
        for name in tuple(directories):
            child = current_path / name
            if _is_reparse_point(child):
                raise BuildError("REPARSE_POINT_FORBIDDEN", f"reparse point is forbidden: {child}")
        for name in filenames:
            child = current_path / name
            if _is_reparse_point(child):
                raise BuildError("REPARSE_POINT_FORBIDDEN", f"reparse point is forbidden: {child}")
            if _is_hardlink(child):
                raise BuildError("HARDLINK_FORBIDDEN", f"hardlink is forbidden: {child}")
            if not child.is_file():
                raise BuildError("SOURCE_NOT_REGULAR_FILE", f"not a regular file: {child}")
            relative = _safe_relative(child.relative_to(root).as_posix(), field="source.relative_path")
            yield child, relative


def _component_tree_digest(files: Iterable[FileRecord]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda record: record.source_relative):
        digest.update(f"{item.source_relative}\0{item.size}\0{item.sha256}\n".encode("utf-8"))
    return digest.hexdigest()


def calculate_allowlist_tree_sha256(
    root: Path,
    include: Iterable[str],
    *,
    runtime_required_testlike: Iterable[dict[str, Any]] | None = None,
) -> str:
    """Public helper for producing the digest pinned in a build specification."""

    root = root.absolute()
    if not root.is_dir() or _is_reparse_point(root):
        raise BuildError("SOURCE_ROOT_INVALID", f"source root is missing or a reparse point: {root}")
    declarations = _parse_runtime_testlike_entries(
        list(runtime_required_testlike) if runtime_required_testlike is not None else None,
        component_id="digest",
    )
    declared = {item["path"]: item for item in declarations}
    records: dict[str, FileRecord] = {}
    for raw_entry in include:
        entry = _safe_relative(raw_entry, field="include")
        for source, relative in _walk_allowlist_entry(root, entry):
            exact_testlike = relative in declared
            if _is_testlike_member_path(relative) and not exact_testlike:
                raise BuildError(
                    "RUNTIME_TESTLIKE_UNDECLARED",
                    f"test-like runtime file is not exactly declared: {relative}",
                )
            _validate_payload_path(relative, allow_exact_testlike=exact_testlike)
            records.setdefault(
                relative,
                FileRecord("digest", "digest", source, relative, relative, source.stat().st_size, sha256_file(source)),
            )
    if not records:
        raise BuildError("EMPTY_ALLOWLIST", "component allowlist selected no files")
    selected_testlike = {path for path in records if _is_testlike_member_path(path)}
    if selected_testlike != set(declared):
        raise BuildError(
            "RUNTIME_TESTLIKE_FILE_SET_MISMATCH",
            f"runtime test-like declaration mismatch; missing={sorted(set(declared) - selected_testlike)}, "
            f"undeclared={sorted(selected_testlike - set(declared))}",
        )
    for relative, declaration in declared.items():
        record = records[relative]
        if record.size != declaration["bytes"] or record.sha256 != declaration["sha256"]:
            raise BuildError(
                "RUNTIME_TESTLIKE_HASH_MISMATCH",
                f"runtime test-like member differs from its exact declaration: {relative}",
            )
    return _component_tree_digest(records.values())


def _scan_text(record: FileRecord) -> None:
    if record.source.suffix.casefold() not in TEXT_SUFFIXES or record.size > 2 * 1024 * 1024:
        return
    try:
        text = record.source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    if record.kind != "dependency" and USER_HOME_TEXT.search(text):
        raise BuildError("USER_PATH_FORBIDDEN", f"user-home path embedded in {record.source_relative}")
    # Exact official wheel bytes are provenance- and RECORD-bound below. Third
    # party client libraries legitimately contain token argument examples and
    # environment-variable names, so the broad secret heuristic is not a sound
    # classifier for dependency source. Keep it for authored/runtime material
    # and all evidence receipts, where an embedded credential is never valid.
    if record.kind != "dependency" and SECRET_TEXT.search(text):
        raise BuildError("SECRET_FORBIDDEN", f"secret-like value embedded in {record.source_relative}")
    if record.kind == "runner" and WINDOWS_ABSOLUTE.search(text):
        raise BuildError("ABSOLUTE_RUNNER_PATH", f"absolute Windows path embedded in runner {record.source_relative}")


def _plan_component(spec_dir: Path, component: Any) -> ComponentPlan:
    if not isinstance(component, dict):
        raise BuildError("INVALID_COMPONENT", "each component must be an object")
    component_id = _safe_pack_token(component.get("id"), field="component.id")
    kind = str(component.get("kind") or "").strip()
    if kind not in TARGET_ROOTS:
        raise BuildError("INVALID_COMPONENT_KIND", f"unsupported component kind for {component_id}: {kind}")
    target_root = _safe_relative(component.get("target_root"), field=f"{component_id}.target_root")
    if target_root != TARGET_ROOTS[kind]:
        raise BuildError(
            "INVALID_TARGET_ROOT",
            f"{component_id} target must be {TARGET_ROOTS[kind]}, got {target_root}",
        )
    root = _resolve_external(spec_dir, component.get("root"), field=f"{component_id}.root")
    if not root.is_dir():
        raise BuildError("SOURCE_ROOT_INVALID", f"source root is missing for {component_id}: {root}")
    if _is_reparse_point(root):
        raise BuildError("REPARSE_POINT_FORBIDDEN", f"component root is a reparse point: {root}")
    include = component.get("include")
    if not isinstance(include, list) or not include:
        raise BuildError("EMPTY_ALLOWLIST", f"{component_id}.include must be a non-empty list")

    runtime_required_testlike = _parse_runtime_testlike_entries(
        component.get("runtime_required_testlike"),
        component_id=component_id,
    )
    if runtime_required_testlike and kind != "dependency":
        raise BuildError(
            "RUNTIME_TESTLIKE_KIND_INVALID",
            f"only dependency components may select traced test-like members: {component_id}",
        )
    runtime_trace_record = None
    trace_declaration = component.get("runtime_import_trace")
    if runtime_required_testlike:
        if trace_declaration is None:
            raise BuildError(
                "RUNTIME_TESTLIKE_TRACE_MISSING",
                f"runtime_import_trace is required for {component_id}",
            )
        runtime_trace_record = _read_runtime_testlike_trace(
            spec_dir,
            trace_declaration,
            component_id=component_id,
        )
    elif trace_declaration is not None:
        raise BuildError(
            "RUNTIME_TESTLIKE_TRACE_UNBOUND",
            f"runtime_import_trace has no selected test-like members for {component_id}",
        )
    source_overlay_record = None
    overlay_declaration = component.get("source_overlay")
    if overlay_declaration is not None:
        if kind != "runner":
            raise BuildError(
                "SOURCE_OVERLAY_KIND_INVALID",
                f"only the runner component may declare the verified source overlay: {component_id}",
            )
        source_overlay_record = _read_source_overlay_contract(
            spec_dir,
            overlay_declaration,
            component_id=component_id,
        )

    records: dict[str, FileRecord] = {}
    record_casefold: set[str] = set()
    runtime_testlike_by_path = {item["path"]: item for item in runtime_required_testlike}
    for raw_entry in include:
        entry = _safe_relative(raw_entry, field=f"{component_id}.include")
        for source, source_relative in _walk_allowlist_entry(root, entry):
            target = f"{target_root}/{source_relative}"
            exact_testlike = source_relative in runtime_testlike_by_path
            if _is_testlike_member_path(source_relative) and not exact_testlike:
                raise BuildError(
                    "RUNTIME_TESTLIKE_UNDECLARED",
                    f"test-like runtime file is not exactly declared for {component_id}: {source_relative}",
                )
            _validate_payload_path(target, allow_exact_testlike=exact_testlike)
            if source_relative in records:
                continue
            if windows_casefold_key(source_relative) in record_casefold:
                raise BuildError("WINDOWS_PATH_COLLISION", f"component has a case-insensitive path collision: {source_relative}")
            record = FileRecord(
                component_id=component_id,
                kind=kind,
                source=source,
                source_relative=source_relative,
                target=target,
                size=source.stat().st_size,
                sha256=sha256_file(source),
            )
            _scan_text(record)
            records[source_relative] = record
            record_casefold.add(windows_casefold_key(source_relative))
    if not records:
        raise BuildError("EMPTY_ALLOWLIST", f"{component_id} allowlist selected no files")
    selected_testlike = {
        path for path in records if _is_testlike_member_path(path)
    }
    if selected_testlike != set(runtime_testlike_by_path):
        raise BuildError(
            "RUNTIME_TESTLIKE_FILE_SET_MISMATCH",
            f"{component_id} runtime test-like declaration mismatch; "
            f"missing={sorted(set(runtime_testlike_by_path) - selected_testlike)}, "
            f"undeclared={sorted(selected_testlike - set(runtime_testlike_by_path))}",
        )
    for relative, declaration in runtime_testlike_by_path.items():
        record = records[relative]
        if record.size != declaration["bytes"] or record.sha256 != declaration["sha256"]:
            raise BuildError(
                "RUNTIME_TESTLIKE_HASH_MISMATCH",
                f"{component_id} runtime test-like member differs from its declaration: {relative}",
            )
    if runtime_trace_record is not None:
        trace_entries = {
            (item["package"], item["path"]): item
            for item in runtime_trace_record["payload"]["entries"]
        }
        package = _safe_pack_token(component.get("name"), field=f"{component_id}.name")
        for relative, declaration in runtime_testlike_by_path.items():
            traced = trace_entries.get((package, relative))
            if traced is None or any(
                traced[field] != declaration[field]
                for field in ("path", "bytes", "sha256")
            ):
                raise BuildError(
                    "RUNTIME_TESTLIKE_TRACE_MISMATCH",
                    f"{component_id} selected member is not present with identical bytes in the trace: {relative}",
                )

    expected_tree = str(component.get("expected_tree_sha256") or "").strip().casefold()
    if not HEX64.fullmatch(expected_tree):
        raise BuildError("MISSING_COMPONENT_HASH", f"{component_id}.expected_tree_sha256 is required")
    actual_tree = _component_tree_digest(records.values())
    if actual_tree != expected_tree:
        raise BuildError(
            "COMPONENT_HASH_MISMATCH",
            f"{component_id} tree hash mismatch: expected {expected_tree}, got {actual_tree}",
        )

    name = str(component.get("name") or "").strip()
    version = str(component.get("version") or "").strip()
    supplier = str(component.get("supplier") or "").strip()
    license_expression = str(component.get("license_expression") or "").strip()
    if not all((name, version, supplier)):
        raise BuildError("INCOMPLETE_COMPONENT_METADATA", f"name/version/supplier are required for {component_id}")
    if not license_expression or license_expression.casefold() in {"unknown", "noassertion", "none"}:
        raise BuildError("LICENSE_UNRESOLVED", f"a resolved license expression is required for {component_id}")
    license_files = component.get("license_files")
    if not isinstance(license_files, list) or not license_files:
        raise BuildError("LICENSE_EVIDENCE_MISSING", f"license evidence is required for {component_id}")
    license_records = []
    for index, declaration in enumerate(license_files, start=1):
        path, content, digest = _read_pinned_file(
            spec_dir, declaration, field=f"{component_id}.license_files[{index - 1}]"
        )
        _scan_evidence_content(
            content,
            field=f"{component_id}.license_files[{index - 1}]",
            reject_absolute_windows_path=False,
        )
        if not content.strip():
            raise BuildError("LICENSE_EVIDENCE_EMPTY", f"license evidence is empty for {component_id}: {path}")
        license_records.append({"source": path, "content": content, "sha256": digest, "index": index})
    receipt_path, receipt_content, receipt_hash = _read_pinned_file(
        spec_dir, component.get("receipt"), field=f"{component_id}.receipt"
    )
    _scan_evidence_content(
        receipt_content,
        field=f"{component_id}.receipt",
        reject_absolute_windows_path=True,
    )
    try:
        receipt_payload = json.loads(receipt_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("PROVENANCE_RECEIPT_INVALID", f"receipt must be UTF-8 JSON for {component_id}") from exc
    if not isinstance(receipt_payload, dict) or not str(receipt_payload.get("schema") or "").strip():
        raise BuildError("PROVENANCE_RECEIPT_INVALID", f"receipt schema is required for {component_id}")
    receipt_record = {
        "source": receipt_path,
        "content": receipt_content,
        "sha256": receipt_hash,
        "payload": receipt_payload,
    }
    supplement_record = None
    supplement_declaration = component.get("license_supplement")
    if supplement_declaration is not None:
        if kind != "dependency":
            raise BuildError(
                "LICENSE_SUPPLEMENT_KIND_INVALID",
                f"only dependency components may use a license supplement: {component_id}",
            )
        supplement_path, supplement_content, supplement_hash = _read_pinned_file(
            spec_dir,
            supplement_declaration,
            field=f"{component_id}.license_supplement",
        )
        _scan_evidence_content(
            supplement_content,
            field=f"{component_id}.license_supplement",
            reject_absolute_windows_path=True,
        )
        try:
            supplement_payload = json.loads(supplement_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BuildError(
                "LICENSE_SUPPLEMENT_INVALID",
                f"license supplement must be UTF-8 JSON for {component_id}",
            ) from exc
        if not isinstance(supplement_payload, dict):
            raise BuildError(
                "LICENSE_SUPPLEMENT_INVALID",
                f"license supplement must be an object for {component_id}",
            )
        supplement_record = {
            "source": supplement_path,
            "content": supplement_content,
            "sha256": supplement_hash,
            "payload": supplement_payload,
        }
    wheel_exclusions: tuple[dict[str, Any], ...] = ()
    raw_exclusions = component.get("wheel_exclusions")
    if raw_exclusions is not None:
        if kind != "dependency" or not isinstance(raw_exclusions, list) or not raw_exclusions:
            raise BuildError(
                "WHEEL_EXCLUSIONS_INVALID",
                f"wheel_exclusions must be a non-empty list on a dependency: {component_id}",
            )
        parsed_exclusions = []
        seen_exclusions: set[str] = set()
        for index, item in enumerate(raw_exclusions):
            if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256", "reason"}:
                raise BuildError(
                    "WHEEL_EXCLUSIONS_INVALID",
                    f"{component_id}.wheel_exclusions[{index}] fields are invalid",
                )
            path = _safe_relative(
                item.get("path"),
                field=f"{component_id}.wheel_exclusions[{index}].path",
            )
            folded = windows_casefold_key(path)
            size = item.get("bytes")
            digest = str(item.get("sha256") or "")
            reason = item.get("reason")
            parts = [part.casefold() for part in PurePosixPath(path).parts]
            filename = PurePosixPath(path).name.casefold()
            allowed_test_path = any(
                part in {"test", "tests", "testing"} or part.startswith("test_")
                for part in parts
            ) or filename.endswith("_test.py")
            is_license = _is_license_like_member(PurePosixPath(path))
            is_required_metadata = filename in {"metadata", "record"} and any(
                part.endswith(".dist-info") for part in parts[:-1]
            )
            if (
                reason != "non-runtime-test"
                or not allowed_test_path
                or is_license
                or is_required_metadata
                or not isinstance(size, int)
                or size < 0
                or not HEX64.fullmatch(digest)
                or folded in seen_exclusions
            ):
                raise BuildError(
                    "WHEEL_EXCLUSION_NOT_ALLOWED",
                    f"{component_id} wheel exclusion is not an exact allowed test member: {path}",
                )
            seen_exclusions.add(folded)
            parsed_exclusions.append(
                {"path": path, "bytes": size, "sha256": digest, "reason": reason}
            )
        if parsed_exclusions != sorted(parsed_exclusions, key=lambda item: item["path"]):
            raise BuildError(
                "WHEEL_EXCLUSIONS_INVALID",
                f"{component_id} wheel exclusions must be path-sorted",
            )
        wheel_exclusions = tuple(parsed_exclusions)
    return ComponentPlan(
        component,
        tuple(sorted(records.values(), key=lambda item: item.target)),
        actual_tree,
        tuple(license_records),
        receipt_record,
        supplement_record,
        wheel_exclusions,
        runtime_required_testlike,
        runtime_trace_record,
        source_overlay_record,
    )


def _validate_model_component(model_plan: ComponentPlan) -> None:
    model = model_plan.component.get("model")
    if not isinstance(model, dict):
        raise BuildError("MODEL_IDENTITY_MISSING", "model metadata is required")
    if (
        model.get("repository") != TRUSTED_MODEL_REPOSITORY
        or model.get("revision") != TRUSTED_MODEL_REVISION
    ):
        raise BuildError("MODEL_IDENTITY_INVALID", "model repository/revision does not match the built-in trust set")
    actual_files = {
        record.source_relative: (record.size, record.sha256)
        for record in model_plan.files
    }
    if actual_files != TRUSTED_MODEL_FILES:
        missing = sorted(set(TRUSTED_MODEL_FILES) - set(actual_files))
        extra = sorted(set(actual_files) - set(TRUSTED_MODEL_FILES))
        mismatched = sorted(
            path
            for path in set(actual_files) & set(TRUSTED_MODEL_FILES)
            if actual_files[path] != TRUSTED_MODEL_FILES[path]
        )
        raise BuildError(
            "MODEL_TRUST_SET_MISMATCH",
            f"model files do not match the built-in 11-file trust set; missing={missing}, extra={extra}, mismatched={mismatched}",
        )
    if model_plan.tree_sha256 != TRUSTED_MODEL_TREE_SHA256:
        raise BuildError("MODEL_TRUST_SET_MISMATCH", "model tree hash does not match the built-in trust set")
    if {str(record["sha256"]) for record in model_plan.license_records} != {
        TRUSTED_QWEN_LICENSE_SHA256
    }:
        raise BuildError("MODEL_LICENSE_UNTRUSTED", "model license evidence does not match the trusted Qwen Apache-2.0 license")
    receipt = model_plan.receipt_record["payload"]
    expected_receipt_keys = {
        "schema",
        "repository",
        "revision",
        "license",
        "tree_sha256",
        "cache_metadata_excluded",
        "files",
    }
    if (
        set(receipt) != expected_receipt_keys
        or receipt.get("schema") != "mentorflow.trusted-huggingface-model-receipt.v1"
        or receipt.get("repository") != TRUSTED_MODEL_REPOSITORY
        or receipt.get("revision") != TRUSTED_MODEL_REVISION
        or receipt.get("tree_sha256") != TRUSTED_MODEL_TREE_SHA256
        or receipt.get("license") != "Apache-2.0"
        or receipt.get("cache_metadata_excluded") is not True
    ):
        raise BuildError("MODEL_RECEIPT_UNTRUSTED", "model receipt identity/license/tree is not trusted")
    if _receipt_file_map(receipt, field="model.receipt") != TRUSTED_MODEL_FILES:
        raise BuildError("MODEL_RECEIPT_UNTRUSTED", "model receipt does not contain the exact trusted 11-file list")


def _read_pinned_binary(spec_dir: Path, declaration: Any, *, field: str) -> tuple[Path, int, str]:
    if not isinstance(declaration, dict):
        raise BuildError("MISSING_EVIDENCE", f"{field} must be an object")
    path = _resolve_external(spec_dir, declaration.get("path"), field=f"{field}.path")
    _ensure_plain_file(path, code="MISSING_EVIDENCE")
    expected_hash = str(declaration.get("sha256") or "").casefold()
    expected_bytes = declaration.get("bytes")
    if not HEX64.fullmatch(expected_hash) or not isinstance(expected_bytes, int) or expected_bytes < 1:
        raise BuildError("MISSING_EVIDENCE_HASH", f"{field} requires bytes and lowercase SHA256")
    actual_bytes = path.stat().st_size
    if actual_bytes != expected_bytes:
        raise BuildError("EVIDENCE_SIZE_MISMATCH", f"{field} size mismatch: expected {expected_bytes}, got {actual_bytes}")
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise BuildError("EVIDENCE_HASH_MISMATCH", f"{field} hash mismatch: expected {expected_hash}, got {actual_hash}")
    return path, actual_bytes, actual_hash


def _zip_member_sha256(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info, "r") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _wheel_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    casefold_paths: set[str] = set()
    for info in archive.infolist():
        # ZIP directory entries carry no installed file bytes and are excluded
        # from the wheel/RECORD closure by definition.
        if info.is_dir():
            continue
        relative = _safe_relative(info.filename, field="wheel.member")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise BuildError("WHEEL_MEMBER_LINK_FORBIDDEN", f"wheel contains a symlink: {relative}")
        if windows_casefold_key(relative) in casefold_paths:
            raise BuildError("WHEEL_INVALID", f"wheel contains a duplicate member: {relative}")
        result[relative] = info
        casefold_paths.add(windows_casefold_key(relative))
    if not result:
        raise BuildError("WHEEL_INVALID", "wheel contains no files")
    return result


def _is_license_like_member(path: PurePosixPath) -> bool:
    """Recognize exact legal-notice files without classifying code directories."""

    lowered_parts = [part.casefold() for part in path.parts]
    dist_info_license = any(
        part.endswith(".dist-info")
        and index + 1 < len(lowered_parts)
        and lowered_parts[index + 1] == "licenses"
        for index, part in enumerate(lowered_parts)
    )
    normalized_name = re.sub(r"[^a-z0-9]", "", path.name.casefold())
    return dist_info_license or normalized_name.startswith(
        (
            "license",
            "licence",
            "copying",
            "notice",
            "thirdpartynotice",
            "copyright",
        )
    )


def _record_map(record: FileRecord) -> dict[str, tuple[str | None, int | None]]:
    try:
        with record.source.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        result: dict[str, tuple[str | None, int | None]] = {}
        casefold_paths: set[str] = set()
        for index, row in enumerate(rows, start=1):
            if len(row) != 3:
                raise BuildError("WHEEL_RECORD_INVALID", f"invalid RECORD row {index}")
            relative = _safe_relative(row[0], field=f"wheel.RECORD[{index}].path")
            digest = row[1] or None
            size: int | None = None
            if row[2]:
                try:
                    size = int(row[2])
                except ValueError as exc:
                    raise BuildError("WHEEL_RECORD_INVALID", f"invalid RECORD size at row {index}") from exc
                if size < 0:
                    raise BuildError("WHEEL_RECORD_INVALID", f"negative RECORD size at row {index}")
            if windows_casefold_key(relative) in casefold_paths:
                raise BuildError("WHEEL_RECORD_INVALID", f"duplicate RECORD path: {relative}")
            result[relative] = (digest, size)
            casefold_paths.add(windows_casefold_key(relative))
        return result
    except UnicodeDecodeError as exc:
        raise BuildError("WHEEL_RECORD_INVALID", "wheel RECORD is not UTF-8") from exc


def _record_sha256_token(digest: str) -> str:
    return "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=").decode("ascii")


def _trusted_github_repository(value: Any, *, field: str) -> tuple[str, str, str]:
    repository = str(value or "")
    parsed = urlparse(repository)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
        or parsed.path != f"/{parts[0]}/{parts[1]}"
        or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
        or any(part in {".", ".."} for part in parts)
        or parts[1].casefold().endswith(".git")
    ):
        raise BuildError(
            "LICENSE_SUPPLEMENT_REPOSITORY_UNTRUSTED",
            f"{field} must be an exact https://github.com/<owner>/<repo> URL",
        )
    return repository, parts[0], parts[1]


def _validate_dependency_license_supplement(
    *,
    dependency: ComponentPlan,
    metadata: Any,
    metadata_record: FileRecord,
    wheel_path: Path,
    wheel_hash: str,
) -> None:
    component = dependency.component
    component_id = str(component["id"])
    name = str(component["name"])
    version = str(component["version"])
    purl = str(component["purl"])
    supplement = dependency.license_supplement_record
    if supplement is None:
        raise BuildError(
            "DEPENDENCY_LICENSE_NOT_FROM_WHEEL",
            f"{component_id} license evidence is not present in the pinned wheel",
        )
    if len(dependency.license_records) != 1:
        raise BuildError(
            "LICENSE_SUPPLEMENT_EVIDENCE_INVALID",
            f"{component_id} requires exactly one externally supplemented license file",
        )
    payload = supplement["payload"]
    expected_top = {
        "schema",
        "package",
        "pypi_metadata_binding",
        "upstream_release",
        "license_file",
    }
    if set(payload) != expected_top or payload.get("schema") != LICENSE_SUPPLEMENT_SCHEMA:
        raise BuildError(
            "LICENSE_SUPPLEMENT_SCHEMA_INVALID",
            f"{component_id} license supplement schema or fields are invalid",
        )
    package = payload.get("package")
    expected_package = {
        "name": _canonical_distribution_name(name),
        "version": version,
        "purl": purl,
        "wheel_filename": wheel_path.name,
        "wheel_sha256": wheel_hash,
        "metadata_sha256": metadata_record.sha256,
    }
    if not isinstance(package, dict) or set(package) != set(expected_package) or any(
        package.get(key) != value for key, value in expected_package.items()
    ):
        raise BuildError(
            "LICENSE_SUPPLEMENT_PACKAGE_MISMATCH",
            f"{component_id} supplement does not match the exact package, wheel and METADATA",
        )

    binding = payload.get("pypi_metadata_binding")
    if not isinstance(binding, dict) or set(binding) != {"project_urls", "repository"}:
        raise BuildError("LICENSE_SUPPLEMENT_SOURCE_MISMATCH", f"{component_id} source binding is invalid")
    project_urls = list(metadata.get_all("Project-URL") or ())
    if binding.get("project_urls") != project_urls:
        raise BuildError(
            "LICENSE_SUPPLEMENT_SOURCE_MISMATCH",
            f"{component_id} Project-URL values differ from the pinned wheel METADATA",
        )
    repository, owner, repository_name = _trusted_github_repository(
        binding.get("repository"),
        field=f"{component_id}.license_supplement.pypi_metadata_binding.repository",
    )
    if f"Source, {repository}" not in project_urls:
        raise BuildError(
            "LICENSE_SUPPLEMENT_SOURCE_MISMATCH",
            f"{component_id} repository is not the exact Source Project-URL in wheel METADATA",
        )

    upstream = payload.get("upstream_release")
    if not isinstance(upstream, dict) or set(upstream) != {
        "repository",
        "tag",
        "tag_api_url",
        "resolved_commit",
        "commit_url",
    }:
        raise BuildError("LICENSE_SUPPLEMENT_RELEASE_INVALID", f"{component_id} upstream release is invalid")
    tag = str(upstream.get("tag") or "")
    commit = str(upstream.get("resolved_commit") or "")
    if upstream.get("repository") != repository:
        raise BuildError("LICENSE_SUPPLEMENT_RELEASE_INVALID", f"{component_id} repository binding differs")
    if tag != f"v{version}" or not re.fullmatch(r"v[A-Za-z0-9._+-]+", tag):
        raise BuildError(
            "LICENSE_SUPPLEMENT_TAG_INVALID",
            f"{component_id} release tag must be exactly v<package-version>",
        )
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise BuildError("LICENSE_SUPPLEMENT_COMMIT_INVALID", f"{component_id} commit must be 40 lowercase hex")
    expected_tag_api = f"https://api.github.com/repos/{owner}/{repository_name}/commits/{tag}"
    expected_commit_url = f"https://github.com/{owner}/{repository_name}/commit/{commit}"
    if upstream.get("tag_api_url") != expected_tag_api or upstream.get("commit_url") != expected_commit_url:
        raise BuildError(
            "LICENSE_SUPPLEMENT_RELEASE_INVALID",
            f"{component_id} tag API or immutable commit URL is not exact",
        )

    license_file = payload.get("license_file")
    if not isinstance(license_file, dict) or set(license_file) != {
        "repository_path",
        "immutable_url",
        "bytes",
        "sha256",
        "saved_relative_path",
    }:
        raise BuildError("LICENSE_SUPPLEMENT_FILE_INVALID", f"{component_id} license file fields are invalid")
    repository_path = _safe_relative(
        license_file.get("repository_path"),
        field=f"{component_id}.license_supplement.license_file.repository_path",
    )
    encoded_path = "/".join(quote(part, safe="") for part in PurePosixPath(repository_path).parts)
    expected_raw_url = (
        f"https://raw.githubusercontent.com/{owner}/{repository_name}/{commit}/{encoded_path}"
    )
    raw_url = str(license_file.get("immutable_url") or "")
    parsed_raw = urlparse(raw_url)
    if (
        raw_url != expected_raw_url
        or parsed_raw.scheme != "https"
        or parsed_raw.hostname != "raw.githubusercontent.com"
        or parsed_raw.query
        or parsed_raw.fragment
    ):
        raise BuildError(
            "LICENSE_SUPPLEMENT_URL_INVALID",
            f"{component_id} license URL must use the exact immutable raw GitHub commit path",
        )
    expected_saved = _safe_relative(
        f"license-supplements/{_canonical_distribution_name(name)}=={version}/{PurePosixPath(repository_path).name}",
        field=f"{component_id}.license_supplement.license_file.saved_relative_path",
    )
    if license_file.get("saved_relative_path") != expected_saved:
        raise BuildError(
            "LICENSE_SUPPLEMENT_FILE_INVALID",
            f"{component_id} saved supplement path is not canonical",
        )
    record = dependency.license_records[0]
    expected_size = license_file.get("bytes")
    expected_hash = str(license_file.get("sha256") or "")
    if (
        not isinstance(expected_size, int)
        or not 1 <= expected_size <= 1024 * 1024
        or not HEX64.fullmatch(expected_hash)
        or record["source"].name != PurePosixPath(repository_path).name
        or len(record["content"]) != expected_size
        or record["sha256"] != expected_hash
    ):
        raise BuildError(
            "LICENSE_SUPPLEMENT_HASH_MISMATCH",
            f"{component_id} supplement license bytes/hash/path do not match the pinned file",
        )


def _validate_dependency_component(
    spec_dir: Path,
    dependency: ComponentPlan,
    lock: dict[str, Any],
) -> None:
    component = dependency.component
    component_id = str(component["id"])
    name = str(component["name"])
    version = str(component["version"])
    purl = str(component.get("purl") or "")
    expected_purl = _canonical_pypi_purl(name, version)
    if purl != expected_purl:
        raise BuildError("DEPENDENCY_PURL_INVALID", f"{component_id} purl must be canonical and version-pinned: {expected_purl}")

    metadata_records = [
        record
        for record in dependency.files
        if len(PurePosixPath(record.source_relative).parts) == 2
        and record.source_relative.casefold().endswith(".dist-info/metadata")
    ]
    record_records = [
        record
        for record in dependency.files
        if len(PurePosixPath(record.source_relative).parts) == 2
        and record.source_relative.casefold().endswith(".dist-info/record")
    ]
    if len(metadata_records) != 1 or len(record_records) != 1:
        raise BuildError("DEPENDENCY_DIST_INFO_MISSING", f"{component_id} requires exactly one dist-info/METADATA and RECORD")
    metadata_record = metadata_records[0]
    record_record = record_records[0]
    if PurePosixPath(metadata_record.source_relative).parent != PurePosixPath(record_record.source_relative).parent:
        raise BuildError("DEPENDENCY_DIST_INFO_MISMATCH", f"{component_id} METADATA and RECORD use different dist-info directories")
    metadata = BytesParser(policy=email_policy).parsebytes(metadata_record.source.read_bytes())
    metadata_name = str(metadata.get("Name") or "")
    metadata_version = str(metadata.get("Version") or "")
    if _canonical_distribution_name(metadata_name) != _canonical_distribution_name(name) or metadata_version != version:
        raise BuildError("DEPENDENCY_METADATA_MISMATCH", f"{component_id} METADATA name/version does not match the lock")

    record_map = _record_map(record_record)
    selected_files = {file_record.source_relative: file_record for file_record in dependency.files}
    wheel_path, wheel_bytes, wheel_hash = _read_pinned_binary(
        spec_dir,
        component.get("wheel"),
        field=f"{component_id}.wheel",
    )
    if dependency.runtime_required_testlike:
        if dependency.runtime_import_trace_record is None:
            raise BuildError("RUNTIME_TESTLIKE_TRACE_MISSING", f"runtime import trace is missing for {component_id}")
        traced = {
            (item["package"], item["path"]): item
            for item in dependency.runtime_import_trace_record["payload"]["entries"]
        }
        for declaration in dependency.runtime_required_testlike:
            trace_item = traced.get((name, declaration["path"]))
            if trace_item is None or trace_item["wheel_sha256"] != wheel_hash:
                raise BuildError(
                    "RUNTIME_TESTLIKE_WHEEL_MISMATCH",
                    f"{component_id} traced runtime member is not bound to the pinned wheel: {declaration['path']}",
                )
    try:
        with zipfile.ZipFile(wheel_path, "r") as archive:
            members = _wheel_members(archive)
            selected_paths = set(selected_files)
            wheel_paths = set(members)
            record_paths = set(record_map)
            exclusions = {item["path"]: item for item in dependency.wheel_exclusions}
            excluded_paths = set(exclusions)
            if selected_paths.intersection(excluded_paths):
                raise BuildError(
                    "DEPENDENCY_WHEEL_EXCLUSION_OVERLAP",
                    f"{component_id} selected and excluded wheel members overlap",
                )
            if not (selected_paths.union(excluded_paths) == wheel_paths == record_paths):
                raise BuildError(
                    "DEPENDENCY_WHEEL_CLOSURE_MISMATCH",
                    (
                        f"{component_id} selected plus explicitly excluded files, non-directory wheel members, and RECORD rows must match exactly; "
                        f"selected_only={sorted(selected_paths - wheel_paths)}, "
                        f"wheel_only={sorted(wheel_paths - selected_paths - excluded_paths)}, "
                        f"excluded_only={sorted(excluded_paths - wheel_paths)}, "
                        f"record_only={sorted(record_paths - wheel_paths)}, "
                        f"wheel_without_record={sorted(wheel_paths - record_paths)}"
                    ),
                )
            for relative, file_record in selected_files.items():
                record_digest, record_size = record_map[relative]
                if relative == record_record.source_relative:
                    if record_digest is not None or record_size is not None:
                        raise BuildError(
                            "WHEEL_RECORD_INVALID",
                            "RECORD must leave its own hash and size empty",
                        )
                elif (
                    record_digest != _record_sha256_token(file_record.sha256)
                    or record_size != file_record.size
                ):
                    raise BuildError(
                        "DEPENDENCY_RECORD_MISMATCH",
                        f"{component_id} RECORD hash/size mismatch for {relative}",
                    )
                info = members[relative]
                if info.file_size != file_record.size or _zip_member_sha256(archive, info) != file_record.sha256:
                    raise BuildError(
                        "DEPENDENCY_NOT_FROM_WHEEL",
                        f"{component_id} installed bytes differ from the pinned wheel: {relative}",
                    )
            for relative, exclusion in exclusions.items():
                record_digest, record_size = record_map[relative]
                info = members[relative]
                wheel_member_hash = _zip_member_sha256(archive, info)
                if (
                    info.file_size != exclusion["bytes"]
                    or wheel_member_hash != exclusion["sha256"]
                    or record_digest != _record_sha256_token(exclusion["sha256"])
                    or record_size != exclusion["bytes"]
                ):
                    raise BuildError(
                        "DEPENDENCY_WHEEL_EXCLUSION_MISMATCH",
                        f"{component_id} excluded member differs from wheel/RECORD: {relative}",
                    )
            wheel_license_hashes = set()
            wheel_license_paths = set()
            for path, info in members.items():
                pure = PurePosixPath(path)
                if _is_license_like_member(pure):
                    wheel_license_paths.add(path)
                    wheel_license_hashes.add(_zip_member_sha256(archive, info))
    except zipfile.BadZipFile as exc:
        raise BuildError("WHEEL_INVALID", f"invalid wheel archive for {component_id}") from exc

    license_hashes = sorted({str(record["sha256"]) for record in dependency.license_records})
    declared_license_sources = {
        os.path.normcase(os.path.abspath(str(record["source"])))
        for record in dependency.license_records
    }
    declared_wheel_license_paths = {
        relative
        for relative, file_record in selected_files.items()
        if os.path.normcase(os.path.abspath(str(file_record.source)))
        in declared_license_sources
    }
    if dependency.license_supplement_record is not None:
        if wheel_license_hashes:
            raise BuildError(
                "LICENSE_SUPPLEMENT_REPLACES_WHEEL_LICENSE",
                f"{component_id} cannot replace an in-wheel license with a supplement",
            )
        _validate_dependency_license_supplement(
            dependency=dependency,
            metadata=metadata,
            metadata_record=metadata_record,
            wheel_path=wheel_path,
            wheel_hash=wheel_hash,
        )
    else:
        if not set(license_hashes).issubset(wheel_license_hashes):
            raise BuildError("DEPENDENCY_LICENSE_NOT_FROM_WHEEL", f"{component_id} license evidence is not present in the pinned wheel")
        if declared_wheel_license_paths != wheel_license_paths:
            raise BuildError(
                "DEPENDENCY_LICENSE_INVENTORY_MISMATCH",
                f"{component_id} must declare every license-like wheel member exactly; "
                f"missing={sorted(wheel_license_paths - declared_wheel_license_paths)}, "
                f"extra={sorted(declared_wheel_license_paths - wheel_license_paths)}",
            )
    receipt = dependency.receipt_record["payload"]
    source_url = str(receipt.get("source_url") or "")
    parsed_url = urlparse(source_url)
    expected_receipt = {
        "schema": "mentorflow.python-wheel-receipt.v1",
        "name": name,
        "version": version,
        "purl": purl,
        "wheel_filename": wheel_path.name,
        "wheel_bytes": wheel_bytes,
        "wheel_sha256": wheel_hash,
        "record_path": record_record.source_relative,
        "record_sha256": record_record.sha256,
        "tree_sha256": dependency.tree_sha256,
        "license_sha256": license_hashes,
        "source_url": source_url,
    }
    if dependency.wheel_exclusions:
        expected_receipt["wheel_exclusions"] = list(dependency.wheel_exclusions)
    if dependency.runtime_required_testlike:
        expected_receipt["runtime_required_testlike"] = list(dependency.runtime_required_testlike)
        expected_receipt["runtime_import_trace_sha256"] = dependency.runtime_import_trace_record["sha256"]
    if parsed_url.scheme != "https" or parsed_url.hostname not in TRUSTED_WHEEL_HOSTS:
        raise BuildError("WHEEL_SOURCE_UNTRUSTED", f"{component_id} wheel source URL is not trusted")
    if set(receipt) != set(expected_receipt) or any(
        receipt.get(key) != value for key, value in expected_receipt.items()
    ):
        raise BuildError("DEPENDENCY_RECEIPT_MISMATCH", f"{component_id} wheel receipt does not match actual wheel/RECORD/tree/license")
    expected_lock = {
        "component_id": component_id,
        "name": name,
        "version": version,
        "purl": purl,
        "tree_sha256": dependency.tree_sha256,
        "record_path": record_record.source_relative,
        "record_sha256": record_record.sha256,
        "wheel_sha256": wheel_hash,
        "license_sha256": license_hashes,
    }
    if dependency.wheel_exclusions:
        expected_lock["wheel_exclusions"] = list(dependency.wheel_exclusions)
    if dependency.runtime_required_testlike:
        expected_lock["runtime_required_testlike"] = list(dependency.runtime_required_testlike)
        expected_lock["runtime_import_trace_sha256"] = dependency.runtime_import_trace_record["sha256"]
    if lock != expected_lock:
        raise BuildError("DEPENDENCY_LOCK_MISMATCH", f"dependency lock entry does not match actual artifacts for {component_id}")


def _inventory_record_map(
    value: Any,
    *,
    field: str,
) -> tuple[list[dict[str, Any]], dict[str, tuple[int, str]]]:
    if not isinstance(value, list):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            f"{field} must be an exact file-record list",
        )
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(value):
        if not isinstance(row, Mapping) or set(row) != {"path", "bytes", "sha256"}:
            raise BuildError(
                "PORTABLE_PYTHON_INVENTORY_INVALID",
                f"{field}[{index}] fields are invalid",
            )
        relative = _safe_relative(row.get("path"), field=f"{field}[{index}].path")
        folded = windows_casefold_key(relative)
        size = row.get("bytes")
        digest = str(row.get("sha256") or "")
        if (
            folded in seen
            or not isinstance(size, int)
            or size < 0
            or not HEX64.fullmatch(digest)
        ):
            raise BuildError(
                "PORTABLE_PYTHON_INVENTORY_INVALID",
                f"{field}[{index}] is duplicated or has an invalid pin",
            )
        seen.add(folded)
        records.append({"path": relative, "bytes": size, "sha256": digest})
    return records, {
        windows_casefold_key(item["path"]): (item["bytes"], item["sha256"])
        for item in records
    }


def _validate_inventory_file_records(
    value: Any,
    *,
    root: Path,
    field: str,
) -> list[dict[str, Any]]:
    records, record_map = _inventory_record_map(value, field=field)
    canonical = sorted(
        records,
        key=lambda item: (windows_casefold_key(item["path"]), item["path"]),
    )
    if records != canonical:
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            f"{field} must use canonical Windows-path ordering",
        )
    actual = [
        {"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path, relative in _walk_allowlist_entry(root, ".")
    ]
    actual.sort(
        key=lambda item: (windows_casefold_key(item["path"]), item["path"])
    )
    actual_map = {
        windows_casefold_key(item["path"]): (item["bytes"], item["sha256"])
        for item in actual
    }
    if record_map != actual_map or records != actual:
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_MISMATCH",
            f"{field} does not enumerate the exact file tree at {root}",
        )
    return records


def _read_portable_inventory_pin(
    material_root: Path,
    declaration: Any,
    *,
    field: str,
    expected_path: str,
    expected_schema: str | None = None,
) -> tuple[Path, bytes, dict[str, Any]]:
    required_fields = {"path", "bytes", "sha256"}
    if expected_schema is not None:
        required_fields.add("schema")
    if not isinstance(declaration, dict) or set(declaration) != required_fields:
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            f"{field} pin fields are invalid",
        )
    path, content, _digest = _read_pinned_file(material_root, declaration, field=field)
    if (
        path != (material_root / expected_path).absolute()
        or declaration.get("bytes") != len(content)
        or (expected_schema is not None and declaration.get("schema") != expected_schema)
    ):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            f"{field} does not bind the exact expected material",
        )
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            f"{field} is not UTF-8 JSON",
        ) from exc
    if not isinstance(payload, dict) or (
        expected_schema is not None and payload.get("schema") != expected_schema
    ):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            f"{field} JSON schema is invalid",
        )
    return path, content, payload


def _validate_portable_python_material_inventory(
    spec_dir: Path,
    python_plan: ComponentPlan,
) -> None:
    declaration = python_plan.component.get("material_inventory")
    if (
        not isinstance(declaration, dict)
        or set(declaration) != {"path", "bytes", "sha256", "schema"}
        or declaration.get("schema") != PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA
    ):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            "portable Python material inventory pin fields are invalid",
        )
    inventory_path, inventory_content, _inventory_digest = _read_pinned_file(
        spec_dir,
        declaration,
        field="portable_python.material_inventory",
    )
    if (
        inventory_path.name != "MATERIAL_INVENTORY.v2.json"
        or declaration.get("bytes") != len(inventory_content)
    ):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            "portable Python material inventory path or byte count is invalid",
        )
    try:
        inventory = json.loads(inventory_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            "portable Python material inventory is not UTF-8 JSON",
        ) from exc
    if not isinstance(inventory, dict):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            "portable Python material inventory must be an object",
        )
    material_root = inventory_path.parent
    expected_component_root = (material_root / "builder-input").absolute()
    if Path(str(python_plan.component.get("root") or "")).absolute() != expected_component_root:
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_MISMATCH",
            "portable Python component root is not the inventory builder-input tree",
        )
    if (
        set(inventory) != {"schema", "component", "trees", "evidence"}
        or inventory.get("schema") != PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA
        or inventory.get("component")
        != {
            "id": "python-3.12.10-embed-amd64",
            "version": TRUSTED_PORTABLE_PYTHON_VERSION,
            "distribution": "python.org-embeddable",
            "platform": "win-x64",
            "abi": "cp312",
        }
    ):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            "portable Python v2 inventory identity or fields are not exact",
        )
    trees = inventory.get("trees")
    tree_roots = {
        "official_runtime": "runtime",
        "builder_input": "builder-input",
        "source_artifacts": "receipts",
    }
    if not isinstance(trees, dict) or set(trees) != set(tree_roots):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            "portable Python inventory tree set is not exact",
        )
    tree_records: dict[str, list[dict[str, Any]]] = {}
    for field, relative_root in tree_roots.items():
        tree = trees.get(field)
        if (
            not isinstance(tree, dict)
            or set(tree)
            != {"root", "file_count", "total_bytes", "tree_sha256", "files"}
            or tree.get("root") != relative_root
        ):
            raise BuildError(
                "PORTABLE_PYTHON_INVENTORY_INVALID",
                f"portable Python inventory tree declaration is invalid: {field}",
            )
        records = _validate_inventory_file_records(
            tree.get("files"),
            root=material_root / relative_root,
            field=f"portable_python.inventory.trees.{field}.files",
        )
        tree_digest = hashlib.sha256()
        for row in sorted(records, key=lambda item: item["path"]):
            tree_digest.update(
                f"{row['path']}\0{row['bytes']}\0{row['sha256']}\n".encode("utf-8")
            )
        if (
            tree.get("file_count") != len(records)
            or tree.get("total_bytes") != sum(row["bytes"] for row in records)
            or tree.get("tree_sha256") != tree_digest.hexdigest()
        ):
            raise BuildError(
                "PORTABLE_PYTHON_INVENTORY_MISMATCH",
                f"portable Python inventory tree summary is invalid: {field}",
            )
        tree_records[field] = records
    builder_map = {
        row["path"]: (row["bytes"], row["sha256"])
        for row in tree_records["builder_input"]
    }
    component_map = {
        record.source_relative: (record.size, record.sha256)
        for record in python_plan.files
    }
    if (
        builder_map != component_map
        or trees["builder_input"]["tree_sha256"] != python_plan.tree_sha256
    ):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_MISMATCH",
            "portable Python inventory builder tree does not equal the component allowlist closure",
        )
    receipt_payload = python_plan.receipt_record.get("payload")
    derivative = (
        receipt_payload.get("builder_derivative")
        if isinstance(receipt_payload, dict)
        else None
    )
    official_map = {
        row["path"]: (row["bytes"], row["sha256"])
        for row in tree_records["official_runtime"]
    }
    if (
        not isinstance(derivative, dict)
        or set(official_map) != set(builder_map)
        or derivative.get("changes_from_official_tree") != 1
        or derivative.get("changed_path") != "python312._pth"
        or derivative.get("official_sha256")
        != official_map.get("python312._pth", (None, None))[1]
        or derivative.get("builder_sha256")
        != builder_map.get("python312._pth", (None, None))[1]
        or official_map.get("python312._pth") == builder_map.get("python312._pth")
        or any(
            official_map[path] != builder_map[path]
            for path in builder_map
            if path != "python312._pth"
        )
    ):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_MISMATCH",
            "official and builder Python trees do not match the one-file derivative contract",
        )
    archive = receipt_payload.get("archive") if isinstance(receipt_payload, dict) else None
    filename = str(archive.get("filename") or "") if isinstance(archive, dict) else ""
    expected_artifacts = {
        filename,
        f"{filename}.asc",
        f"{filename}.crt",
        f"{filename}.sig",
        f"{filename}.sigstore",
        f"{filename}.spdx.json",
    }
    artifact_map = {
        row["path"]: (row["bytes"], row["sha256"])
        for row in tree_records["source_artifacts"]
    }
    if (
        not filename
        or set(artifact_map) != expected_artifacts
        or artifact_map.get(filename)
        != (archive.get("bytes"), archive.get("sha256"))
    ):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_MISMATCH",
            "portable Python source artifacts are not the exact archive plus five sidecars",
        )
    evidence = inventory.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != {
        "source_receipt",
        "psf_license",
        "notice",
    }:
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            "portable Python inventory evidence set is not exact",
        )
    source_path, source_raw, _source_payload = _read_portable_inventory_pin(
        material_root,
        evidence.get("source_receipt"),
        field="portable_python.inventory.evidence.source_receipt",
        expected_path="SOURCE_RECEIPT.json",
        expected_schema=PORTABLE_PYTHON_RECEIPT_SCHEMA,
    )
    if (
        source_path != python_plan.receipt_record["source"]
        or source_raw != python_plan.receipt_record["content"]
        or evidence["source_receipt"]["sha256"]
        != python_plan.receipt_record["sha256"]
    ):
        raise BuildError(
            "PORTABLE_PYTHON_INVENTORY_MISMATCH",
            "portable Python inventory does not bind the component source receipt",
        )
    license_by_name = {
        record["source"].name: record for record in python_plan.license_records
    }
    for field, filename in (
        ("psf_license", "PSF-LICENSE.txt"),
        ("notice", "NOTICE.txt"),
    ):
        path, content, digest = _read_pinned_file(
            material_root,
            evidence.get(field),
            field=f"portable_python.inventory.evidence.{field}",
        )
        row = evidence.get(field)
        record = license_by_name.get(filename)
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "bytes", "sha256"}
            or path != (material_root / filename).absolute()
            or row.get("bytes") != len(content)
            or record is None
            or path != record["source"]
            or digest != record["sha256"]
        ):
            raise BuildError(
                "PORTABLE_PYTHON_INVENTORY_MISMATCH",
                f"portable Python inventory does not bind {filename}",
            )


def _validate_portable_python_component(
    python_plan: ComponentPlan,
    *,
    spec_dir: Path | None = None,
    require_material_inventory: bool = False,
) -> None:
    component = python_plan.component
    if require_material_inventory:
        expected_fields = {
            "schema",
            "id",
            "kind",
            "name",
            "version",
            "supplier",
            "root",
            "target_root",
            "include",
            "expected_tree_sha256",
            "license_expression",
            "license_files",
            "receipt",
            "distribution",
            "homepage",
            "material_inventory",
        }
        if (
            set(component) != expected_fields
            or component.get("schema") != PORTABLE_PYTHON_COMPONENT_SCHEMA
        ):
            raise BuildError(
                "PORTABLE_PYTHON_COMPONENT_INVALID",
                "portable Python v2 component schema or exact field set is invalid",
            )
        include = component.get("include")
        if (
            not isinstance(include, list)
            or any(not isinstance(item, str) for item in include)
            or len(include) != len(set(include))
            or set(include) != {record.source_relative for record in python_plan.files}
        ):
            raise BuildError(
                "PORTABLE_PYTHON_COMPONENT_INVALID",
                "portable Python v2 include list must uniquely enumerate the inventory builder tree",
            )
        license_declarations = component.get("license_files")
        if (
            not isinstance(license_declarations, list)
            or len(license_declarations) != 2
            or any(
                not isinstance(row, dict)
                or set(row) != {"path", "bytes", "sha256"}
                for row in license_declarations
            )
            or len(python_plan.license_records) != 2
            or any(
                declaration.get("bytes") != len(record["content"])
                for declaration, record in zip(
                    license_declarations,
                    python_plan.license_records,
                    strict=True,
                )
            )
        ):
            raise BuildError(
                "PORTABLE_PYTHON_COMPONENT_INVALID",
                "portable Python v2 license declarations are not the exact two-file evidence set",
            )
        receipt_declaration = component.get("receipt")
        if (
            not isinstance(receipt_declaration, dict)
            or set(receipt_declaration) != {"path", "bytes", "sha256"}
            or receipt_declaration.get("bytes")
            != len(python_plan.receipt_record["content"])
        ):
            raise BuildError(
                "PORTABLE_PYTHON_COMPONENT_INVALID",
                "portable Python v2 receipt declaration fields or byte count are invalid",
            )
    if (
        component.get("id") != "python-3.12.10-embed-amd64"
        or component.get("kind") != "portable_python"
        or component.get("name") != "CPython Windows embeddable package (64-bit)"
        or component.get("version") != TRUSTED_PORTABLE_PYTHON_VERSION
        or component.get("supplier") != "Python Software Foundation"
        or component.get("distribution") != "python.org-embeddable"
        or component.get("license_expression") != "PSF-2.0"
        or component.get("target_root") != TARGET_ROOTS["portable_python"]
        or python_plan.tree_sha256 != TRUSTED_PORTABLE_PYTHON_TREE_SHA256
    ):
        raise BuildError(
            "PORTABLE_PYTHON_PROVENANCE_INVALID",
            "portable Python identity, PSF license expression, distribution, or tree is not the locked CPython release",
        )

    license_by_name = {
        record["source"].name: record for record in python_plan.license_records
    }
    if set(license_by_name) != {"PSF-LICENSE.txt", "NOTICE.txt"}:
        raise BuildError(
            "PORTABLE_PYTHON_LICENSE_INVALID",
            "portable Python requires the exact PSF-LICENSE.txt and NOTICE.txt evidence set",
        )
    if (
        license_by_name["PSF-LICENSE.txt"]["sha256"]
        != TRUSTED_PORTABLE_PYTHON_LICENSE_SHA256
        or license_by_name["NOTICE.txt"]["sha256"]
        != TRUSTED_PORTABLE_PYTHON_NOTICE_SHA256
    ):
        raise BuildError(
            "PORTABLE_PYTHON_LICENSE_INVALID",
            "portable Python PSF license or NOTICE digest is not trusted",
        )
    file_map = {
        record.source_relative: (record.size, record.sha256)
        for record in python_plan.files
    }
    if file_map.get("LICENSE.txt", (None, None))[1] != TRUSTED_PORTABLE_PYTHON_LICENSE_SHA256:
        raise BuildError(
            "PORTABLE_PYTHON_LICENSE_INVALID",
            "the official extracted LICENSE.txt does not match the pinned PSF license",
        )

    if require_material_inventory:
        if spec_dir is None:
            raise BuildError(
                "PORTABLE_PYTHON_INVENTORY_INVALID",
                "portable Python material inventory validation requires its specification directory",
            )
        _validate_portable_python_material_inventory(spec_dir, python_plan)

    receipt = python_plan.receipt_record["payload"]
    top_fields = {
        "schema",
        "component",
        "selection",
        "upstream",
        "archive",
        "verification",
        "extraction",
        "builder_derivative",
        "authenticode",
    }
    if set(receipt) != top_fields or receipt.get("schema") != PORTABLE_PYTHON_RECEIPT_SCHEMA:
        raise BuildError(
            "PORTABLE_PYTHON_RECEIPT_INVALID",
            "portable Python source receipt schema or exact field set is invalid",
        )
    expected_component = {
        "distribution": "python.org-embeddable",
        "name": "CPython Windows embeddable package (64-bit)",
        "platform": "win-x64",
        "version": TRUSTED_PORTABLE_PYTHON_VERSION,
        "abi": "cp312",
        "supplier": "Python Software Foundation",
        "license": "PSF-2.0",
    }
    if receipt.get("component") != expected_component:
        raise BuildError("PORTABLE_PYTHON_RECEIPT_INVALID", "portable Python receipt component is not exact")
    selection = receipt.get("selection")
    if (
        not isinstance(selection, dict)
        or set(selection)
        != {
            "checked_on",
            "current_3_12_security_release",
            "current_3_12_security_release_binary_installers",
            "selected_binary_release",
            "reason",
        }
        or selection.get("selected_binary_release") != TRUSTED_PORTABLE_PYTHON_VERSION
        or selection.get("current_3_12_security_release_binary_installers") is not False
        or not str(selection.get("reason") or "").strip()
    ):
        raise BuildError("PORTABLE_PYTHON_RECEIPT_INVALID", "portable Python release selection evidence is invalid")
    upstream = receipt.get("upstream")
    expected_base = f"https://www.python.org/ftp/python/{TRUSTED_PORTABLE_PYTHON_VERSION}/python-{TRUSTED_PORTABLE_PYTHON_VERSION}-embed-amd64.zip"
    if (
        not isinstance(upstream, dict)
        or set(upstream)
        != {
            "release_page",
            "latest_3_12_release_page",
            "archive_url",
            "spdx_url",
            "sigstore_bundle_url",
            "detached_signature_url",
            "signing_certificate_url",
            "openpgp_signature_url",
        }
        or upstream.get("release_page")
        != f"https://www.python.org/downloads/release/python-{TRUSTED_PORTABLE_PYTHON_VERSION.replace('.', '')}/"
        or upstream.get("archive_url") != expected_base
        or upstream.get("spdx_url") != expected_base + ".spdx.json"
        or upstream.get("sigstore_bundle_url") != expected_base + ".sigstore"
        or upstream.get("detached_signature_url") != expected_base + ".sig"
        or upstream.get("signing_certificate_url") != expected_base + ".crt"
        or upstream.get("openpgp_signature_url") != expected_base + ".asc"
    ):
        raise BuildError("PORTABLE_PYTHON_RECEIPT_INVALID", "portable Python upstream URLs are not exact python.org inputs")
    archive = receipt.get("archive")
    if (
        not isinstance(archive, dict)
        or set(archive)
        != {
            "filename",
            "bytes",
            "sha256",
            "official_spdx_sha256",
            "official_release_page_md5",
            "http_status",
            "final_https_host",
            "content_length",
            "last_modified_utc",
            "etag",
        }
        or archive.get("filename") != f"python-{TRUSTED_PORTABLE_PYTHON_VERSION}-embed-amd64.zip"
        or archive.get("sha256") != TRUSTED_PORTABLE_PYTHON_ARCHIVE_SHA256
        or archive.get("official_spdx_sha256") != TRUSTED_PORTABLE_PYTHON_ARCHIVE_SHA256
        or archive.get("http_status") != 200
        or archive.get("final_https_host") != "www.python.org"
        or archive.get("bytes") != archive.get("content_length")
        or not isinstance(archive.get("bytes"), int)
        or archive.get("bytes", 0) <= 0
    ):
        raise BuildError("PORTABLE_PYTHON_RECEIPT_INVALID", "portable Python archive evidence is invalid")
    verification = receipt.get("verification")
    required_true = {
        "official_spdx_sha256_matches",
        "official_release_page_md5_matches",
        "sigstore_bundle_digest_matches",
        "sigstore_rekor_body_digest_matches",
        "detached_signature_matches_bundle",
        "detached_certificate_matches_bundle",
        "detached_ecdsa_signature_valid",
    }
    if (
        not isinstance(verification, dict)
        or set(verification)
        != required_true
        | {
            "signer_certificate_san",
            "full_sigstore_trust_root_and_transparency_log_verification",
            "openpgp_signature_verified",
            "verification_limit",
        }
        or any(verification.get(field) is not True for field in required_true)
        or verification.get("full_sigstore_trust_root_and_transparency_log_verification") is not False
        or verification.get("openpgp_signature_verified") is not False
        or not str(verification.get("signer_certificate_san") or "").strip()
        or not str(verification.get("verification_limit") or "").strip()
    ):
        raise BuildError("PORTABLE_PYTHON_RECEIPT_INVALID", "portable Python verification evidence is invalid")
    extraction = receipt.get("extraction")
    if (
        not isinstance(extraction, dict)
        or set(extraction)
        != {
            "zip_entries",
            "unsafe_entries",
            "official_tree",
            "license_path",
            "license_bytes",
            "license_sha256",
        }
        or extraction.get("unsafe_entries") != 0
        or extraction.get("official_tree") != "runtime"
        or extraction.get("license_path") != "runtime/LICENSE.txt"
        or extraction.get("license_sha256") != TRUSTED_PORTABLE_PYTHON_LICENSE_SHA256
        or extraction.get("license_bytes") != len(license_by_name["PSF-LICENSE.txt"]["content"])
    ):
        raise BuildError("PORTABLE_PYTHON_RECEIPT_INVALID", "portable Python extraction/license evidence is invalid")
    derivative = receipt.get("builder_derivative")
    pth = file_map.get("python312._pth")
    if (
        not isinstance(derivative, dict)
        or set(derivative)
        != {
            "root",
            "changes_from_official_tree",
            "changed_path",
            "official_sha256",
            "builder_sha256",
            "change_summary",
            "expected_tree_sha256",
        }
        or derivative.get("root") != "builder-input"
        or derivative.get("changes_from_official_tree") != 1
        or derivative.get("changed_path") != "python312._pth"
        or derivative.get("builder_sha256") != (pth or (None, None))[1]
        or derivative.get("expected_tree_sha256") != python_plan.tree_sha256
        or not HEX64.fullmatch(str(derivative.get("official_sha256") or ""))
        or not str(derivative.get("change_summary") or "").strip()
    ):
        raise BuildError("PORTABLE_PYTHON_RECEIPT_INVALID", "portable Python builder derivative is not exact")

    authenticode = receipt.get("authenticode")
    expected_signed = {
        path: value
        for path, value in file_map.items()
        if PurePosixPath(path).suffix.casefold() in {".exe", ".dll", ".pyd", ".cat"}
    }
    if (
        not isinstance(authenticode, dict)
        or set(authenticode) != {"schema", "status", "verifier", "files"}
        or authenticode.get("schema") != PORTABLE_PYTHON_AUTHENTICODE_SCHEMA
        or authenticode.get("status") != "passed"
        or authenticode.get("verifier") != "PowerShell 7 Get-AuthenticodeSignature"
        or not isinstance(authenticode.get("files"), list)
    ):
        raise BuildError("PORTABLE_PYTHON_AUTHENTICODE_INVALID", "portable Python Authenticode receipt is invalid")
    signed_rows: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(authenticode["files"]):
        if not isinstance(row, dict) or set(row) != {
            "path",
            "bytes",
            "sha256",
            "status",
            "publisher",
            "publisher_subject",
            "certificate_thumbprint",
        }:
            raise BuildError("PORTABLE_PYTHON_AUTHENTICODE_INVALID", f"Authenticode row {index} fields are invalid")
        path = _safe_relative(row.get("path"), field=f"portable.authenticode.files[{index}].path")
        publisher = str(row.get("publisher") or "")
        if (
            path in signed_rows
            or path not in expected_signed
            or (row.get("bytes"), row.get("sha256")) != expected_signed[path]
            or row.get("status") != "Valid"
            or publisher not in TRUSTED_PORTABLE_PYTHON_PUBLISHERS
            or f"O={publisher}" not in str(row.get("publisher_subject") or "")
            or not re.fullmatch(r"[0-9A-F]{40}", str(row.get("certificate_thumbprint") or ""))
        ):
            raise BuildError("PORTABLE_PYTHON_AUTHENTICODE_INVALID", f"Authenticode row {index} is not trusted")
        signed_rows[path] = row
    if set(signed_rows) != set(expected_signed):
        raise BuildError(
            "PORTABLE_PYTHON_AUTHENTICODE_INVALID",
            "Authenticode receipt must cover every EXE/DLL/PYD/CAT in the portable tree exactly",
        )


def _validate_runner_component(runner_plan: ComponentPlan) -> None:
    component = runner_plan.component
    license_expression = str(component.get("license_expression") or "").strip()
    if (
        component.get("id") != "mentorflow-qwen-runner"
        or component.get("kind") != "runner"
        or component.get("name") != "MentorFlow Qwen Base Clone runner"
        or component.get("supplier") != "MentorFlow"
        or not license_expression
        or license_expression.casefold() in {"unknown", "noassertion", "none"}
        or component.get("target_root") != TARGET_ROOTS["runner"]
        or len(runner_plan.files) != 1
    ):
        raise BuildError("RUNNER_PROVENANCE_INVALID", "runner identity, source cardinality, or license expression is invalid")
    source = runner_plan.files[0]
    license_by_name = {record["source"].name: record for record in runner_plan.license_records}
    if set(license_by_name) != {"MENTORFLOW-RUNTIME-LICENSE.txt", "NOTICE.txt"}:
        raise BuildError(
            "RUNNER_LICENSE_INVALID",
            "runner requires an independent MENTORFLOW-RUNTIME-LICENSE.txt and NOTICE.txt",
        )
    if any(not record["content"].strip() for record in license_by_name.values()) or any(
        record["sha256"] == TRUSTED_QWEN_LICENSE_SHA256 for record in license_by_name.values()
    ):
        raise BuildError("RUNNER_LICENSE_INVALID", "runner license/NOTICE cannot reuse the Qwen model license")
    receipt = runner_plan.receipt_record["payload"]
    if set(receipt) != {"schema", "component", "source", "license", "notice"} or receipt.get("schema") != RUNNER_SOURCE_RECEIPT_SCHEMA:
        raise BuildError("RUNNER_RECEIPT_INVALID", "runner source receipt schema or exact field set is invalid")
    expected_component = {
        "id": "mentorflow-qwen-runner",
        "name": "MentorFlow Qwen Base Clone runner",
        "version": component.get("version"),
        "supplier": "MentorFlow",
        "license_expression": license_expression,
    }
    if receipt.get("component") != expected_component:
        raise BuildError("RUNNER_RECEIPT_INVALID", "runner receipt component identity is invalid")
    expected_source = {
        "path": source.source_relative,
        "bytes": source.size,
        "sha256": source.sha256,
        "tree_sha256": runner_plan.tree_sha256,
    }
    if receipt.get("source") != expected_source:
        raise BuildError("RUNNER_RECEIPT_INVALID", "runner receipt does not bind the exact source bytes/tree")
    for field, name in (("license", "MENTORFLOW-RUNTIME-LICENSE.txt"), ("notice", "NOTICE.txt")):
        record = license_by_name[name]
        expected = {
            "path": name,
            "bytes": len(record["content"]),
            "sha256": record["sha256"],
        }
        if receipt.get(field) != expected:
            raise BuildError("RUNNER_RECEIPT_INVALID", f"runner receipt {field} binding is invalid")


def _require_component_contracts(
    spec_dir: Path,
    spec: dict[str, Any],
    plans: list[ComponentPlan],
) -> None:
    by_kind: dict[str, list[ComponentPlan]] = {kind: [] for kind in TARGET_ROOTS}
    by_id: dict[str, ComponentPlan] = {}
    targets: dict[str, str] = {}
    for plan in plans:
        component_id = str(plan.component["id"])
        if component_id in by_id:
            raise BuildError("DUPLICATE_COMPONENT", f"duplicate component id: {component_id}")
        by_id[component_id] = plan
        by_kind[str(plan.component["kind"])].append(plan)
        for record in plan.files:
            previous = targets.setdefault(windows_casefold_key(record.target), component_id)
            if previous != component_id:
                raise BuildError("TARGET_COLLISION", f"{record.target} is selected by {previous} and {component_id}")
    for singleton in ("portable_python", "runner", "model"):
        if len(by_kind[singleton]) != 1:
            raise BuildError("COMPONENT_CARDINALITY", f"exactly one {singleton} component is required")

    python_plan = by_kind["portable_python"][0]
    if str(python_plan.component.get("distribution") or "") != "python.org-embeddable":
        raise BuildError("PORTABLE_PYTHON_REQUIRED", "portable Python must declare distribution=python.org-embeddable")
    python_names = {PurePosixPath(item.source_relative).name.casefold() for item in python_plan.files}
    if "python.exe" not in python_names or not any(name.startswith("python") and name.endswith(".dll") for name in python_names):
        raise BuildError("PORTABLE_PYTHON_INCOMPLETE", "portable Python must contain python.exe and a versioned Python DLL")
    pth_records = [
        item
        for item in python_plan.files
        if item.source.name.casefold().startswith("python")
        and item.source.name.casefold().endswith("._pth")
    ]
    if len(pth_records) != 1:
        raise BuildError("PORTABLE_PYTHON_INCOMPLETE", "portable Python must contain exactly one python*._pth file")
    pth_text = pth_records[0].source.read_text(encoding="utf-8")
    if "import site" not in pth_text or "Lib/site-packages" not in pth_text.replace("\\", "/"):
        raise BuildError("PORTABLE_PYTHON_NOT_RELOCATABLE", "python*._pth must enable site and a relative Lib/site-packages")
    if WINDOWS_ABSOLUTE.search(pth_text) or USER_HOME_TEXT.search(pth_text):
        raise BuildError("PORTABLE_PYTHON_NOT_RELOCATABLE", "python*._pth contains an absolute path")
    _validate_portable_python_component(
        python_plan,
        spec_dir=spec_dir,
        require_material_inventory=True,
    )

    runner_plan = by_kind["runner"][0]
    _validate_runner_component(runner_plan)

    _validate_model_component(by_kind["model"][0])

    dependency_lock = spec.get("dependency_lock")
    if not isinstance(dependency_lock, list) or not dependency_lock:
        raise BuildError("DEPENDENCY_LOCK_MISSING", "dependency_lock must be a non-empty exact artifact lock")
    lock_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(dependency_lock):
        if not isinstance(item, dict):
            raise BuildError("DEPENDENCY_LOCK_INVALID", f"dependency_lock[{index}] must be an object")
        component_id = str(item.get("component_id") or "")
        try:
            component_id = _safe_pack_token(component_id, field=f"dependency_lock[{index}].component_id")
        except BuildError as exc:
            raise BuildError("DEPENDENCY_LOCK_INVALID", str(exc)) from exc
        if component_id in lock_by_id:
            raise BuildError("DEPENDENCY_LOCK_INVALID", f"invalid or duplicate dependency lock id: {component_id!r}")
        lock_by_id[component_id] = item
    actual = {str(item.component["id"]) for item in by_kind["dependency"]}
    if set(lock_by_id) != actual:
        raise BuildError(
            "DEPENDENCY_LOCK_MISMATCH",
            f"dependency lock set mismatch; missing={sorted(actual - set(lock_by_id))}, undeclared={sorted(set(lock_by_id) - actual)}",
        )
    for dependency in by_kind["dependency"]:
        purl = str(dependency.component.get("purl") or "")
        if "gradio" in purl.casefold() or "gradio" in str(dependency.component.get("name") or "").casefold():
            raise BuildError("GRADIO_FORBIDDEN", "Gradio is not part of the inference runtime")
        _validate_dependency_component(
            spec_dir,
            dependency,
            lock_by_id[str(dependency.component["id"])],
        )

    traced_dependencies = [item for item in by_kind["dependency"] if item.runtime_required_testlike]
    if traced_dependencies:
        trace_hashes = {
            str(item.runtime_import_trace_record["sha256"])
            for item in traced_dependencies
            if item.runtime_import_trace_record is not None
        }
        if len(trace_hashes) != 1:
            raise BuildError(
                "RUNTIME_TESTLIKE_TRACE_SET_MISMATCH",
                "all traced runtime test-like members must use one identical sanitized trace contract",
            )
        trace_payload = traced_dependencies[0].runtime_import_trace_record["payload"]
        declared = {
            (str(item.component["name"]), entry["path"]): {
                **entry,
                "wheel_sha256": item.receipt_record["payload"]["wheel_sha256"],
            }
            for item in traced_dependencies
            for entry in item.runtime_required_testlike
        }
        traced = {
            (entry["package"], entry["path"]): {
                "path": entry["path"],
                "bytes": entry["bytes"],
                "sha256": entry["sha256"],
                "reason": "runtime-import-trace",
                "wheel_sha256": entry["wheel_sha256"],
            }
            for entry in trace_payload["entries"]
        }
        if declared != traced:
            raise BuildError(
                "RUNTIME_TESTLIKE_TRACE_SET_MISMATCH",
                f"selected runtime test-like set does not exactly equal the trace; "
                f"selected_only={sorted(set(declared) - set(traced))}, "
                f"trace_only={sorted(set(traced) - set(declared))}",
            )

    overlay = by_kind["runner"][0].source_overlay_record
    if overlay is not None:
        qwen_dependency = by_id.get("qwen-tts")
        if qwen_dependency is None or qwen_dependency.receipt_record["payload"].get("wheel_sha256") != TRUSTED_QWEN_WHEEL_SHA256:
            raise BuildError(
                "SOURCE_OVERLAY_WHEEL_MISMATCH",
                "12Hz source overlay requires the exact trusted qwen-tts 0.1.1 wheel",
            )

    gates = spec.get("runtime_gates")
    if not isinstance(gates, dict) or gates.get("root_resolution") != "pack-relative":
        raise BuildError("RUNTIME_GATES_MISSING", "runtime_gates.root_resolution must be pack-relative")
    env_fields = ("root_env", "host_env", "port_env", "device_env", "driver_minimum_env")
    env_values = []
    for field in env_fields:
        value = str(gates.get(field) or "")
        if not ENV_NAME.fullmatch(value):
            raise BuildError("RUNTIME_GATES_MISSING", f"runtime_gates.{field} must be a configurable environment variable")
        env_values.append(value)
    default_port = gates.get("default_port")
    if not isinstance(default_port, int) or not 1024 <= default_port <= 65535:
        raise BuildError("RUNTIME_GATES_INVALID", "runtime_gates.default_port must be between 1024 and 65535")
    if gates.get("driver_vendor") != "nvidia" or gates.get("on_driver_unsupported") != "fail":
        raise BuildError("DRIVER_GATE_INVALID", "NVIDIA driver gate must fail closed")
    if not re.fullmatch(r"\d{3,4}\.\d{1,3}(?:\.\d{1,3})?", str(gates.get("default_minimum_driver") or "")):
        raise BuildError("DRIVER_GATE_INVALID", "a configurable default_minimum_driver version is required")
    entrypoint = _safe_relative(gates.get("entrypoint"), field="runtime_gates.entrypoint")
    runner_records = {item.target: item for item in runner_plan.files}
    if entrypoint not in runner_records:
        raise BuildError("RUNNER_ENTRYPOINT_MISSING", f"runner entrypoint is not allowlisted: {entrypoint}")
    entrypoint_text = runner_records[entrypoint].source.read_text(encoding="utf-8")
    missing_envs = [value for value in env_values if value not in entrypoint_text]
    if missing_envs:
        raise BuildError("RUNNER_NOT_CONFIGURABLE", f"runner does not reference required environment gates: {missing_envs}")


def _load_public_key(path: Path):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise BuildError("CRYPTOGRAPHY_REQUIRED", "cryptography is required for Ed25519 pack signing") from exc
    raw = path.read_bytes()
    try:
        if raw.startswith(b"-----BEGIN"):
            key = serialization.load_pem_public_key(raw)
        else:
            try:
                decoded = base64.b64decode(raw.strip(), validate=True)
            except ValueError:
                decoded = raw
            key = Ed25519PublicKey.from_public_bytes(decoded)
    except (TypeError, ValueError) as exc:
        raise BuildError("TRUSTED_KEY_INVALID", f"invalid Ed25519 public key: {path}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise BuildError("TRUSTED_KEY_INVALID", "trusted key is not Ed25519")
    return key


def _public_key_raw(key: Any) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def _load_signing(spec_dir: Path, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("algorithm") != "ed25519":
        raise BuildError("SIGNING_CONFIG_MISSING", "trusted Ed25519 signing configuration is required")
    try:
        key_id = _safe_pack_token(value.get("key_id"), field="signing.key_id")
    except BuildError as exc:
        raise BuildError("SIGNING_CONFIG_MISSING", "a stable signing key_id is required") from exc
    private_decl = value.get("private_key")
    public_decl = value.get("trusted_public_key")
    private_path, private_bytes, _ = _read_pinned_file(spec_dir, private_decl, field="signing.private_key")
    public_path, _public_bytes, _ = _read_pinned_file(spec_dir, public_decl, field="signing.trusted_public_key")
    expected_public = str((public_decl or {}).get("public_key_sha256") or "").casefold()
    if not HEX64.fullmatch(expected_public):
        raise BuildError("SIGNING_CONFIG_MISSING", "trusted_public_key.public_key_sha256 is required")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise BuildError("CRYPTOGRAPHY_REQUIRED", "cryptography is required for Ed25519 pack signing") from exc
    try:
        private_key = serialization.load_pem_private_key(private_bytes, password=None)
    except (TypeError, ValueError) as exc:
        raise BuildError("PRIVATE_KEY_INVALID", "private signing key must be an unencrypted PEM Ed25519 key") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise BuildError("PRIVATE_KEY_INVALID", "private signing key is not Ed25519")
    public_key = _load_public_key(public_path)
    public_fingerprint = _sha256_bytes(_public_key_raw(public_key))
    if public_fingerprint != expected_public:
        raise BuildError("TRUSTED_KEY_HASH_MISMATCH", "trusted public-key fingerprint does not match configuration")
    if _public_key_raw(private_key.public_key()) != _public_key_raw(public_key):
        raise BuildError("SIGNING_KEY_MISMATCH", "private key does not match the trusted public key")
    return {
        "key_id": key_id,
        "private_key": private_key,
        "public_key": public_key,
        "public_key_sha256": public_fingerprint,
        "private_key_path": private_path,
    }


def _load_spec(path: Path) -> tuple[dict[str, Any], bytes]:
    _ensure_plain_file(path, code="SPEC_MISSING")
    try:
        raw = path.read_bytes()
        spec = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("SPEC_INVALID", f"invalid UTF-8 JSON spec: {exc}") from exc
    if not isinstance(spec, dict) or spec.get("schema") != SPEC_SCHEMA:
        raise BuildError("SPEC_SCHEMA_INVALID", f"spec.schema must be {SPEC_SCHEMA}")
    return spec, raw


def plan_from_spec(spec_path: Path) -> dict[str, Any]:
    spec_path = spec_path.absolute()
    spec, raw_spec = _load_spec(spec_path)
    pack = spec.get("pack")
    if not isinstance(pack, dict):
        raise BuildError("PACK_METADATA_MISSING", "pack metadata is required")
    try:
        pack_id = _safe_pack_token(pack.get("id"), field="pack.id")
        version = _safe_pack_token(pack.get("version"), field="pack.version")
    except BuildError as exc:
        raise BuildError("PACK_METADATA_INVALID", "pack id and version must be Windows-safe lowercase tokens") from exc
    if pack.get("platform") != PLATFORM or pack.get("architecture") != "x86_64":
        raise BuildError("PACK_PLATFORM_INVALID", "pack platform must be win-x64/x86_64")
    created = str(pack.get("created_at_utc") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created):
        raise BuildError("SOURCE_DATE_MISSING", "pack.created_at_utc must be a pinned UTC second")
    components = spec.get("components")
    if not isinstance(components, list) or not components:
        raise BuildError("COMPONENTS_MISSING", "components must be a non-empty list")
    trusted_source_receipt = _validate_trusted_source_receipt(
        spec_path.parent,
        spec.get("trusted_qwen_source_receipt"),
    )
    plans = [_plan_component(spec_path.parent, component) for component in components]
    _require_component_contracts(spec_path.parent, spec, plans)
    signing = _load_signing(spec_path.parent, spec.get("signing"))
    install_relative_paths = _planned_install_relative_paths(plans)
    path_budget = calculate_install_path_budget(install_relative_paths)
    return {
        "status": "ready",
        "mode": "plan",
        "copies_files": False,
        "spec_path": spec_path,
        "spec": spec,
        "spec_sha256": _sha256_bytes(raw_spec),
        "pack_id": pack_id,
        "version": version,
        "created_at_utc": created,
        "plans": plans,
        "trusted_source_receipt": trusted_source_receipt,
        "signing": signing,
        "install_relative_paths": install_relative_paths,
        "path_budget": path_budget,
        "file_count": sum(len(item.files) for item in plans),
        "bytes": sum(record.size for item in plans for record in item.files),
    }


def _planned_install_relative_paths(
    plans: Iterable[ComponentPlan],
) -> tuple[str, ...]:
    plans = tuple(plans)
    paths = {
        "runtime-config.json",
        "metadata/dependency-lock.json",
        "metadata/receipts/upstream-qwen-source.json",
        "THIRD_PARTY_NOTICES_INDEX.json",
        "THIRD_PARTY_NOTICES.txt",
        "SBOM.spdx.json",
        "FILES.sha256",
        "engine-manifest.json",
        "engine-manifest.sig",
    }
    for component_plan in plans:
        component_id = str(component_plan.component["id"])
        paths.update(record.target for record in component_plan.files)
        for record in component_plan.license_records:
            suffix = record["source"].suffix or ".txt"
            paths.add(f"metadata/licenses/{component_id}/{record['index']:02d}{suffix.casefold()}")
        paths.add(f"metadata/receipts/{component_id}.json")
        if component_plan.license_supplement_record is not None:
            paths.add(f"metadata/receipts/{component_id}.license-supplement.json")
        if component_plan.wheel_exclusions:
            paths.add(f"metadata/exclusions/{component_id}.json")
        if component_plan.runtime_required_testlike:
            trace = component_plan.runtime_import_trace_record
            if trace is None:
                raise BuildError("RUNTIME_TESTLIKE_TRACE_MISSING", f"trace disappeared for {component_id}")
            paths.add(f"metadata/receipts/runtime-import-trace-{trace['sha256'][:16]}.json")
            paths.add(f"metadata/runtime-required-testlike/{component_id}.json")
        if component_plan.source_overlay_record is not None:
            paths.add("metadata/source-overlays/qwen-12hz-only.json")
    return tuple(sorted(_safe_relative(path, field="planned_install.relative_path") for path in paths))


def _copy_exact(
    source: Path,
    destination: Path,
    expected_bytes: int,
    expected_sha256: str,
    *,
    pack_root: Path,
) -> None:
    _ensure_plain_file(source, code="SOURCE_MISSING")
    try:
        relative = destination.absolute().relative_to(pack_root.absolute()).as_posix()
    except ValueError as exc:
        raise BuildError("COPY_TARGET_ESCAPE", f"copy target escapes staging root: {destination}") from exc
    relative = _safe_relative(relative, field="copy.target")
    _validate_payload_path(relative)
    try:
        copy_plain_file_exact(
            source,
            destination,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
            source_root=Path(source.anchor),
            destination_root=pack_root,
        )
    except PlainFileCopyError as exc:
        raise BuildError(exc.code, str(exc)) from exc


def _spdx_id(value: str) -> str:
    return "SPDXRef-" + re.sub(r"[^A-Za-z0-9.-]", "-", value)


def _file_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.casefold()
    return "SOURCE" if suffix in {".py", ".ps1", ".cmd"} else "BINARY"


def _material_records(root: Path, *, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    excluded = exclude or set()
    records = []
    windows_paths: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = _safe_relative(path.relative_to(root).as_posix(), field="material.relative_path")
        folded = windows_casefold_key(relative)
        previous = windows_paths.get(folded)
        if previous is not None and previous != relative:
            raise BuildError(
                "WINDOWS_PATH_COLLISION",
                f"pack has a case-insensitive path collision: {previous!r} and {relative!r}",
            )
        windows_paths[folded] = relative
        if path.is_dir():
            if _is_reparse_point(path):
                raise BuildError("REPARSE_POINT_FORBIDDEN", f"reparse point found in pack: {path}")
            _validate_payload_path(relative)
            continue
        if not path.is_file() or _is_reparse_point(path) or _is_hardlink(path):
            raise BuildError("PACK_FILE_INVALID", f"non-regular file found in pack: {path}")
        if relative in excluded:
            continue
        _validate_payload_path(relative)
        records.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return records


def _write_notices(staging: Path, plan: dict[str, Any]) -> None:
    index_items = []
    grouped_text: dict[str, dict[str, Any]] = {}
    exclusion_sections: list[str] = []
    testlike_sections: list[str] = []
    overlay_sections: list[str] = []
    copied_trace_targets: dict[str, str] = {}
    for component_plan in plan["plans"]:
        component = component_plan.component
        component_id = str(component["id"])
        copied_paths = []
        for record in component_plan.license_records:
            suffix = record["source"].suffix or ".txt"
            target = _safe_relative(
                f"metadata/licenses/{component_id}/{record['index']:02d}{suffix.casefold()}",
                field=f"{component_id}.license_target",
            )
            _validate_payload_path(target)
            destination = staging / Path(*PurePosixPath(target).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(record["content"])
            copied_paths.append({"path": target, "sha256": record["sha256"]})
            grouped = grouped_text.setdefault(
                record["sha256"],
                {"content": record["content"], "components": [], "licenses": []},
            )
            grouped["components"].append(component_id)
            grouped["licenses"].append(str(component["license_expression"]))
        receipt_target = _safe_relative(
            f"metadata/receipts/{component_id}.json",
            field=f"{component_id}.receipt_target",
        )
        _validate_payload_path(receipt_target)
        receipt_destination = staging / Path(*PurePosixPath(receipt_target).parts)
        receipt_destination.parent.mkdir(parents=True, exist_ok=True)
        receipt_destination.write_bytes(component_plan.receipt_record["content"])
        index_item = {
            "component_id": component_id,
            "name": component["name"],
            "version": component["version"],
            "supplier": component["supplier"],
            "license_expression": component["license_expression"],
            "license_files": copied_paths,
            "provenance_receipt": {"path": receipt_target, "sha256": component_plan.receipt_record["sha256"]},
            "homepage": component.get("homepage"),
        }
        supplement = component_plan.license_supplement_record
        if supplement is not None:
            supplement_target = _safe_relative(
                f"metadata/receipts/{component_id}.license-supplement.json",
                field=f"{component_id}.license_supplement_target",
            )
            _validate_payload_path(supplement_target)
            supplement_destination = staging / Path(*PurePosixPath(supplement_target).parts)
            supplement_destination.parent.mkdir(parents=True, exist_ok=True)
            supplement_destination.write_bytes(supplement["content"])
            payload = supplement["payload"]
            index_item["license_supplement_receipt"] = {
                "path": supplement_target,
                "sha256": supplement["sha256"],
                "repository": payload["upstream_release"]["repository"],
                "tag": payload["upstream_release"]["tag"],
                "commit": payload["upstream_release"]["resolved_commit"],
                "license_sha256": payload["license_file"]["sha256"],
            }
        if component_plan.wheel_exclusions:
            exclusion_payload = {
                "schema": "mentorflow.python-wheel-exclusions.v1",
                "component_id": component_id,
                "name": component["name"],
                "version": component["version"],
                "wheel_sha256": component_plan.receipt_record["payload"]["wheel_sha256"],
                "entries": list(component_plan.wheel_exclusions),
            }
            exclusion_content = _canonical_json(exclusion_payload)
            exclusion_target = _safe_relative(
                f"metadata/exclusions/{component_id}.json",
                field=f"{component_id}.wheel_exclusions_target",
            )
            _validate_payload_path(exclusion_target)
            exclusion_destination = staging / Path(*PurePosixPath(exclusion_target).parts)
            exclusion_destination.parent.mkdir(parents=True, exist_ok=True)
            exclusion_destination.write_bytes(exclusion_content)
            exclusion_hash = _sha256_bytes(exclusion_content)
            index_item["wheel_exclusions"] = {
                "path": exclusion_target,
                "sha256": exclusion_hash,
                "count": len(component_plan.wheel_exclusions),
                "bytes_omitted": sum(item["bytes"] for item in component_plan.wheel_exclusions),
                "entries": list(component_plan.wheel_exclusions),
            }
            exclusion_sections.append(
                "=" * 78
                + f"\nExcluded non-runtime wheel tests: {component_id}\n"
                + f"Inventory SHA256: {exclusion_hash}\n"
                + "=" * 78
                + "\n"
                + "\n".join(
                    f"{item['sha256']}  {item['bytes']}  {item['path']}"
                    for item in component_plan.wheel_exclusions
                )
                + "\n"
            )
        if component_plan.runtime_required_testlike:
            trace = component_plan.runtime_import_trace_record
            if trace is None:
                raise BuildError("RUNTIME_TESTLIKE_TRACE_MISSING", f"trace disappeared for {component_id}")
            trace_target = copied_trace_targets.get(trace["sha256"])
            if trace_target is None:
                trace_target = _safe_relative(
                    f"metadata/receipts/runtime-import-trace-{trace['sha256'][:16]}.json",
                    field=f"{component_id}.runtime_import_trace_target",
                )
                trace_destination = staging / Path(*PurePosixPath(trace_target).parts)
                trace_destination.parent.mkdir(parents=True, exist_ok=True)
                trace_destination.write_bytes(trace["content"])
                copied_trace_targets[trace["sha256"]] = trace_target
            testlike_payload = {
                "schema": "mentorflow.python-runtime-required-testlike.v1",
                "component_id": component_id,
                "name": component["name"],
                "version": component["version"],
                "wheel_sha256": component_plan.receipt_record["payload"]["wheel_sha256"],
                "trace": {
                    "path": trace_target,
                    "sha256": trace["sha256"],
                    "source_trace_sha256": trace["payload"]["source_trace"]["sha256"],
                },
                "entries": list(component_plan.runtime_required_testlike),
            }
            testlike_content = _canonical_json(testlike_payload)
            testlike_target = _safe_relative(
                f"metadata/runtime-required-testlike/{component_id}.json",
                field=f"{component_id}.runtime_required_testlike_target",
            )
            testlike_destination = staging / Path(*PurePosixPath(testlike_target).parts)
            testlike_destination.parent.mkdir(parents=True, exist_ok=True)
            testlike_destination.write_bytes(testlike_content)
            testlike_hash = _sha256_bytes(testlike_content)
            index_item["runtime_required_testlike"] = {
                "path": testlike_target,
                "sha256": testlike_hash,
                "count": len(component_plan.runtime_required_testlike),
                "bytes": sum(item["bytes"] for item in component_plan.runtime_required_testlike),
                "trace_path": trace_target,
                "trace_sha256": trace["sha256"],
                "entries": list(component_plan.runtime_required_testlike),
            }
            testlike_sections.append(
                "=" * 78
                + f"\nRuntime-required test-like wheel members: {component_id}\n"
                + f"Inventory SHA256: {testlike_hash}\n"
                + f"Sanitized trace SHA256: {trace['sha256']}\n"
                + "=" * 78
                + "\n"
                + "\n".join(
                    f"{item['sha256']}  {item['bytes']}  {item['path']}"
                    for item in component_plan.runtime_required_testlike
                )
                + "\n"
            )
        overlay = component_plan.source_overlay_record
        if overlay is not None:
            overlay_target = _safe_relative(
                "metadata/source-overlays/qwen-12hz-only.json",
                field=f"{component_id}.source_overlay_target",
            )
            overlay_destination = staging / Path(*PurePosixPath(overlay_target).parts)
            overlay_destination.parent.mkdir(parents=True, exist_ok=True)
            overlay_destination.write_bytes(overlay["content"])
            index_item["source_overlay"] = {
                "path": overlay_target,
                "sha256": overlay["sha256"],
                "policy": overlay["payload"]["policy"],
                "upstream_wheel_sha256": overlay["payload"]["upstream_wheel"]["sha256"],
                "transforms": overlay["payload"]["transforms"],
            }
            overlay_sections.append(
                "=" * 78
                + "\nVerified 12Hz-only in-memory source overlay\n"
                + f"Contract SHA256: {overlay['sha256']}\n"
                + f"Upstream wheel SHA256: {overlay['payload']['upstream_wheel']['sha256']}\n"
                + "=" * 78
                + "\n"
                + "\n".join(
                    f"{item['input_sha256']} -> {item['output_sha256']}  {item['path']}  patch={item['patch_sha256']}"
                    for item in overlay["payload"]["transforms"]
                )
                + "\n"
            )
        index_items.append(index_item)
    trusted_source_target = _safe_relative(
        "metadata/receipts/upstream-qwen-source.json",
        field="trusted_qwen_source_receipt.target",
    )
    trusted_source_destination = staging / Path(*PurePosixPath(trusted_source_target).parts)
    trusted_source_destination.parent.mkdir(parents=True, exist_ok=True)
    trusted_source_destination.write_bytes(plan["trusted_source_receipt"]["content"])
    notice_index = {
        "schema": NOTICES_SCHEMA,
        "trusted_upstream_source_receipt": {
            "path": trusted_source_target,
            "sha256": plan["trusted_source_receipt"]["sha256"],
            "repository": TRUSTED_QWEN_SOURCE_REPOSITORY,
            "revision": TRUSTED_QWEN_SOURCE_REVISION,
        },
        "components": index_items,
    }
    _write_json(staging / "THIRD_PARTY_NOTICES_INDEX.json", notice_index)
    sections = ["MentorFlow Qwen3-TTS Runtime - Third Party Notices\n"]
    for digest, value in sorted(grouped_text.items()):
        header = (
            "=" * 78
            + "\nComponents: "
            + ", ".join(sorted(set(value["components"])))
            + "\nLicense expressions: "
            + ", ".join(sorted(set(value["licenses"])))
            + f"\nLicense SHA256: {digest}\n"
            + "=" * 78
            + "\n"
        )
        sections.append(header + value["content"].decode("utf-8", errors="replace").rstrip() + "\n")
    sections.extend(exclusion_sections)
    sections.extend(testlike_sections)
    sections.extend(overlay_sections)
    (staging / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(sections), encoding="utf-8", newline="\n")


def _write_sbom(staging: Path, plan: dict[str, Any], component_files: dict[str, list[FileRecord]]) -> None:
    packages = []
    files = []
    relationships = []
    for component_plan in plan["plans"]:
        component = component_plan.component
        component_id = str(component["id"])
        package_spdx = _spdx_id("Package-" + component_id)
        package = {
            "SPDXID": package_spdx,
            "name": component["name"],
            "versionInfo": component["version"],
            "supplier": f"Organization: {component['supplier']}",
            "downloadLocation": component.get("homepage") or "NOASSERTION",
            "filesAnalyzed": True,
            "licenseConcluded": component["license_expression"],
            "licenseDeclared": component["license_expression"],
            "copyrightText": "NOASSERTION",
        }
        package_comments = []
        if component.get("purl"):
            package["externalRefs"] = [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": component["purl"],
                }
            ]
        supplement = component_plan.license_supplement_record
        if supplement is not None:
            payload = supplement["payload"]
            package_comments.append(
                "License supplement provenance: "
                f"receipt-sha256={supplement['sha256']}; "
                f"repository={payload['upstream_release']['repository']}; "
                f"tag={payload['upstream_release']['tag']}; "
                f"commit={payload['upstream_release']['resolved_commit']}; "
                f"license-sha256={payload['license_file']['sha256']}"
            )
        if component_plan.wheel_exclusions:
            package_comments.append(
                "Excluded non-runtime wheel test members (exact original wheel bytes): "
                + json.dumps(
                    list(component_plan.wheel_exclusions),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        if component_plan.runtime_required_testlike:
            trace = component_plan.runtime_import_trace_record
            package_comments.append(
                "Runtime-required test-like members (exact official wheel bytes): "
                + json.dumps(
                    list(component_plan.runtime_required_testlike),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + f"; sanitized-trace-sha256={trace['sha256']}"
            )
        if component_plan.source_overlay_record is not None:
            overlay = component_plan.source_overlay_record
            package_comments.append(
                "Verified Qwen 12Hz-only in-memory source overlay: "
                f"contract-sha256={overlay['sha256']}; "
                f"upstream-wheel-sha256={overlay['payload']['upstream_wheel']['sha256']}; "
                "upstream-wheel-bytes-modified=false; transforms="
                + json.dumps(
                    overlay["payload"]["transforms"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        if package_comments:
            package["comment"] = "\n".join(package_comments)
        packages.append(package)
        relationships.append({"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": package_spdx})
        for index, record in enumerate(component_files[component_id], start=1):
            file_spdx = _spdx_id(f"File-{component_id}-{index}")
            files.append(
                {
                    "SPDXID": file_spdx,
                    "fileName": "./" + record.target,
                    "checksums": [{"algorithm": "SHA256", "checksumValue": record.sha256}],
                    "fileTypes": [_file_type(record.target)],
                    "licenseConcluded": component["license_expression"],
                    "copyrightText": "NOASSERTION",
                }
            )
            relationships.append({"spdxElementId": package_spdx, "relationshipType": "CONTAINS", "relatedSpdxElement": file_spdx})
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{plan['pack_id']}-{plan['version']}",
        "documentNamespace": f"https://mentorflow.local/spdx/{plan['pack_id']}/{plan['version']}/{plan['spec_sha256']}",
        "creationInfo": {"created": plan["created_at_utc"], "creators": ["Tool: build_qwen3_tts_runtime_pack.py"]},
        "packages": packages,
        "files": files,
        "relationships": relationships,
    }
    _write_json(staging / "SBOM.spdx.json", sbom)


def _engine_file_role(path: str) -> str:
    if path.startswith("runtime/models/"):
        return "model_asset"
    if path.startswith("runtime/"):
        return "runtime_asset"
    if path.startswith("metadata/licenses/") or path == "THIRD_PARTY_NOTICES.txt":
        return "license"
    return "metadata"


def _write_deterministic_archive(source_root: Path, destination: Path, created_at_utc: str) -> None:
    created = tuple(int(part) for part in re.match(
        r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z",
        created_at_utc,
    ).groups())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", allowZip64=True) as archive:
        for path in sorted(source_root.rglob("*")):
            if path.is_dir():
                continue
            relative = _safe_relative(path.relative_to(source_root).as_posix(), field="archive.member")
            _ensure_plain_file(path, code="PACK_FILE_INVALID")
            info = zipfile.ZipInfo(relative, date_time=created)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.file_size = path.stat().st_size
            with path.open("rb") as reader, archive.open(info, "w", force_zip64=True) as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)


def build_from_plan(plan: dict[str, Any], output_root: Path) -> dict[str, Any]:
    output_root = output_root.absolute()
    final = output_root / plan["pack_id"] / plan["version"]
    path_budget_acceptance = validate_install_root_budget(
        final,
        plan["path_budget"],
        relative_paths=plan["install_relative_paths"],
    )
    output_root.mkdir(parents=True, exist_ok=True)
    archive_final = output_root / plan["pack_id"] / f"{plan['version']}-win-x64.zip"
    catalog_final = output_root / plan["pack_id"] / f"{plan['version']}-engine-catalog.json"
    if any(path.exists() for path in (final, archive_final, catalog_final)):
        raise BuildError("OUTPUT_EXISTS", f"refusing to overwrite an existing pack release under: {final.parent}")
    staging = output_root / f".stage-{uuid.uuid4().hex[:8]}"
    archive_staging = output_root / f".archive-{uuid.uuid4().hex[:8]}.tmp"
    catalog_staging = output_root / f".catalog-{uuid.uuid4().hex[:8]}.tmp"
    staging.mkdir(parents=False, exist_ok=False)
    component_files: dict[str, list[FileRecord]] = {}
    try:
        for component_plan in plan["plans"]:
            component_id = str(component_plan.component["id"])
            component_files[component_id] = list(component_plan.files)
            for record in component_plan.files:
                target = _safe_relative(record.target, field="build.copy_target")
                _validate_payload_path(target)
                destination = staging / Path(*PurePosixPath(target).parts)
                _copy_exact(
                    record.source,
                    destination,
                    record.size,
                    record.sha256,
                    pack_root=staging,
                )
        runtime_config = {
            "schema": "mentorflow.qwen3-tts-runtime-config.v1",
            "offline": True,
            "requires_tts_api_key": False,
            "root_resolution": "pack-relative",
            "entrypoint": plan["spec"]["runtime_gates"]["entrypoint"],
            "environment": {
                field: plan["spec"]["runtime_gates"][field]
                for field in ("root_env", "host_env", "port_env", "device_env", "driver_minimum_env")
            },
            "defaults": {
                "host": plan["spec"]["runtime_gates"].get("default_host", "127.0.0.1"),
                "port": plan["spec"]["runtime_gates"]["default_port"],
                "device": plan["spec"]["runtime_gates"].get("default_device", "cuda:0"),
                "minimum_nvidia_driver": plan["spec"]["runtime_gates"].get("default_minimum_driver"),
            },
            "driver_gate": {"vendor": "nvidia", "on_unsupported": "fail"},
        }
        _write_json(staging / "runtime-config.json", runtime_config)
        dependency_lock_payload = {
            "schema": "mentorflow.qwen-runtime-dependency-lock.v1",
            "packages": plan["spec"]["dependency_lock"],
        }
        dependency_lock_target = staging / "metadata" / "dependency-lock.json"
        dependency_lock_target.parent.mkdir(parents=True, exist_ok=True)
        _write_json(dependency_lock_target, dependency_lock_payload)
        _write_notices(staging, plan)
        _write_sbom(staging, plan, component_files)

        pre_manifest_files = _material_records(staging)
        checksum_bytes = "".join(
            f"{item['sha256']}  {item['path']}\n" for item in pre_manifest_files
        ).encode("utf-8")
        (staging / "FILES.sha256").write_bytes(checksum_bytes)
        manifest_material = _material_records(staging)
        final_relative_paths = tuple(
            sorted(
                {
                    *(item["path"] for item in manifest_material),
                    "engine-manifest.json",
                    "engine-manifest.sig",
                }
            )
        )
        if final_relative_paths != tuple(plan["install_relative_paths"]):
            raise BuildError(
                "INSTALL_PATH_SET_MISMATCH",
                "planned install path set does not equal the exact final package file set",
            )
        final_path_budget = calculate_install_path_budget(final_relative_paths)
        if final_path_budget != plan["path_budget"]:
            raise BuildError(
                "INSTALL_PATH_BUDGET_MISMATCH",
                "planned install path budget changed while materializing the package",
            )
        manifest = {
            "schema": ENGINE_SCHEMA,
            "kind": ENGINE_KIND,
            "package_id": plan["pack_id"],
            "version": plan["version"],
            "install_path_budget": final_path_budget,
            "files": [
                {
                    "path": item["path"],
                    "role": _engine_file_role(item["path"]),
                    "bytes": item["bytes"],
                    "sha256": item["sha256"],
                }
                for item in manifest_material
            ],
        }
        raw_manifest = json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        (staging / "engine-manifest.json").write_bytes(raw_manifest)
        signature = plan["signing"]["private_key"].sign(raw_manifest)
        _write_json(
            staging / "engine-manifest.sig",
            {
                "algorithm": "ed25519",
                "key_id": plan["signing"]["key_id"],
                "signature": base64.b64encode(signature).decode("ascii"),
            },
        )
        verify_report = verify_pack(
            staging,
            plan["signing"]["public_key"],
            plan["signing"]["public_key_sha256"],
            expected_key_id=plan["signing"]["key_id"],
        )
        _write_deterministic_archive(staging, archive_staging, plan["created_at_utc"])
        archive_hash = sha256_file(archive_staging)
        catalog = {
            "schema": "mentorflow.qwen-engine-catalog.v1",
            "engines": [
                {
                    "kind": ENGINE_KIND,
                    "package_id": plan["pack_id"],
                    "version": plan["version"],
                    "archive": {
                        "bytes": archive_staging.stat().st_size,
                        "sha256": archive_hash,
                    },
                    "manifest": {
                        "bytes": len(raw_manifest),
                        "sha256": _sha256_bytes(raw_manifest),
                    },
                    "signing_key_id": plan["signing"]["key_id"],
                }
            ],
        }
        catalog_staging.write_bytes(_canonical_json(catalog))
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)
        os.replace(archive_staging, archive_final)
        os.replace(catalog_staging, catalog_final)
        return {
            "status": "built",
            "pack_root": str(final),
            "archive_path": str(archive_final),
            "archive_sha256": archive_hash,
            "catalog_path": str(catalog_final),
            "manifest_sha256": _sha256_bytes(raw_manifest),
            "verify": verify_report,
            "install_path_budget": plan["path_budget"],
            "install_root_path_acceptance": path_budget_acceptance,
        }
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        for temporary in (archive_staging, catalog_staging):
            if temporary.exists():
                temporary.unlink()
        raise


def _parse_checksums(value: bytes) -> dict[str, str]:
    records: dict[str, str] = {}
    casefold_paths: set[str] = set()
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError("CHECKSUM_LIST_INVALID", "FILES.sha256 is not UTF-8") from exc
    for number, line in enumerate(text.splitlines(), start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise BuildError("CHECKSUM_LIST_INVALID", f"invalid FILES.sha256 line {number}")
        relative = _safe_relative(match.group(2), field=f"FILES.sha256:{number}")
        _validate_payload_path(relative)
        if (
            relative in {"FILES.sha256", "engine-manifest.json", "engine-manifest.sig"}
            or windows_casefold_key(relative) in casefold_paths
        ):
            raise BuildError("CHECKSUM_LIST_INVALID", f"invalid or duplicate checksum target: {relative}")
        records[relative] = match.group(1)
        casefold_paths.add(windows_casefold_key(relative))
    if not records:
        raise BuildError("CHECKSUM_LIST_INVALID", "FILES.sha256 is empty")
    return records


def verify_pack(
    pack_root: Path,
    public_key: Any,
    public_key_sha256: str,
    *,
    expected_key_id: str | None = None,
) -> dict[str, Any]:
    pack_root = pack_root.absolute()
    if not pack_root.is_dir() or _is_reparse_point(pack_root):
        raise BuildError("PACK_ROOT_INVALID", f"pack root is missing or a reparse point: {pack_root}")
    checksum_path = pack_root / "FILES.sha256"
    signature_path = pack_root / "engine-manifest.sig"
    manifest_path = pack_root / "engine-manifest.json"
    for path in (checksum_path, signature_path, manifest_path, pack_root / "SBOM.spdx.json", pack_root / "THIRD_PARTY_NOTICES_INDEX.json"):
        _ensure_plain_file(path, code="PACK_METADATA_MISSING")
    checksum_bytes = checksum_path.read_bytes()
    try:
        signature = json.loads(signature_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("SIGNATURE_INVALID", "invalid engine-manifest.sig") from exc
    if set(signature) != {"algorithm", "key_id", "signature"} or signature.get("algorithm") != "ed25519":
        raise BuildError("SIGNATURE_METADATA_MISMATCH", "engine manifest signature metadata is invalid")
    if expected_key_id is not None and signature.get("key_id") != expected_key_id:
        raise BuildError("SIGNATURE_TRUST_MISMATCH", "engine manifest signing key id does not match the catalog trust binding")
    raw_manifest = manifest_path.read_bytes()
    try:
        signature_bytes = base64.b64decode(str(signature.get("signature") or ""), validate=True)
        public_key.verify(signature_bytes, raw_manifest)
    except Exception as exc:
        raise BuildError("SIGNATURE_INVALID", "engine manifest Ed25519 signature verification failed") from exc
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("ENGINE_MANIFEST_INVALID", "invalid engine-manifest.json") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {"schema", "kind", "package_id", "version", "install_path_budget", "files"}
        or manifest.get("schema") != ENGINE_SCHEMA
        or manifest.get("kind") != ENGINE_KIND
        or not isinstance(manifest.get("files"), list)
        or not manifest["files"]
    ):
        raise BuildError("ENGINE_MANIFEST_INVALID", "engine manifest does not match the canonical adapter contract")
    try:
        _safe_pack_token(manifest.get("package_id"), field="engine_manifest.package_id")
        _safe_pack_token(manifest.get("version"), field="engine_manifest.version")
    except BuildError as exc:
        raise BuildError("ENGINE_MANIFEST_INVALID", "engine manifest identity is not Windows-safe") from exc
    declared: dict[str, dict[str, Any]] = {}
    declared_casefold: set[str] = set()
    allowed_roles = {"license", "metadata", "model_asset", "runtime_asset"}
    for index, item in enumerate(manifest["files"]):
        if not isinstance(item, dict) or set(item) != {"path", "role", "bytes", "sha256"}:
            raise BuildError("ENGINE_MANIFEST_INVALID", f"invalid manifest file item {index}")
        relative = _safe_relative(item.get("path"), field=f"engine_manifest.files[{index}].path")
        _validate_payload_path(relative)
        if windows_casefold_key(relative) in declared_casefold or item.get("role") not in allowed_roles:
            raise BuildError("ENGINE_MANIFEST_INVALID", f"duplicate path or invalid role at manifest file item {index}")
        if not isinstance(item.get("bytes"), int) or item["bytes"] < 0 or not HEX64.fullmatch(str(item.get("sha256") or "")):
            raise BuildError("ENGINE_MANIFEST_INVALID", f"invalid size/hash at manifest file item {index}")
        declared[relative] = item
        declared_casefold.add(windows_casefold_key(relative))
    install_relative_paths = tuple(
        sorted({*declared, "engine-manifest.json", "engine-manifest.sig"})
    )
    install_root_path_acceptance = validate_install_root_budget(
        pack_root,
        manifest["install_path_budget"],
        relative_paths=install_relative_paths,
    )
    expected = _parse_checksums(checksum_bytes)
    if set(expected) != set(declared) - {"FILES.sha256"}:
        raise BuildError("CHECKSUM_LIST_INVALID", "FILES.sha256 must cover every signed manifest material except itself")
    actual_paths = {
        item["path"]
        for item in _material_records(pack_root, exclude={"engine-manifest.json", "engine-manifest.sig"})
    }
    if actual_paths != set(declared):
        raise BuildError(
            "PACK_FILE_SET_MISMATCH",
            f"pack file set mismatch; missing={sorted(set(declared) - actual_paths)}, extra={sorted(actual_paths - set(declared))}",
        )
    declared_directories: set[str] = set()
    for relative in declared:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            declared_directories.add(parent.as_posix())
            parent = parent.parent
    actual_directories = {
        _safe_relative(path.relative_to(pack_root).as_posix(), field="verify.directory")
        for path in pack_root.rglob("*")
        if path.is_dir()
    }
    if actual_directories != declared_directories:
        raise BuildError(
            "PACK_DIRECTORY_SET_MISMATCH",
            f"pack directory set mismatch; missing={sorted(declared_directories - actual_directories)}, extra={sorted(actual_directories - declared_directories)}",
        )
    total_bytes = 0
    for relative, item in declared.items():
        safe_relative = _safe_relative(relative, field="verify.checksum_target")
        path = pack_root / Path(*PurePosixPath(safe_relative).parts)
        actual_hash = sha256_file(path)
        if path.stat().st_size != item["bytes"] or actual_hash != item["sha256"]:
            raise BuildError("PACK_HASH_MISMATCH", f"hash mismatch for {relative}")
        if relative != "FILES.sha256" and expected.get(relative) != actual_hash:
            raise BuildError("CHECKSUM_LIST_INVALID", f"FILES.sha256 mismatch for {relative}")
        total_bytes += path.stat().st_size
    return {
        "status": "verified",
        "pack_id": manifest["package_id"],
        "version": manifest["version"],
        "manifest_sha256": _sha256_bytes(raw_manifest),
        "files": len(declared),
        "bytes": total_bytes,
        "install_path_budget": manifest["install_path_budget"],
        "install_root_path_acceptance": install_root_path_acceptance,
        "trusted_public_key_sha256": public_key_sha256,
    }


def _public_key_for_verify(path: Path, expected_fingerprint: str):
    _ensure_plain_file(path, code="TRUSTED_KEY_MISSING")
    if not HEX64.fullmatch(expected_fingerprint.casefold()):
        raise BuildError("TRUSTED_KEY_HASH_MISSING", "--trusted-public-key-sha256 is required")
    public_key = _load_public_key(path)
    actual = _sha256_bytes(_public_key_raw(public_key))
    if actual != expected_fingerprint.casefold():
        raise BuildError("TRUSTED_KEY_HASH_MISMATCH", "external trusted public-key fingerprint mismatch")
    return public_key, actual


def verify_catalog_release(
    *,
    catalog_path: Path,
    package_id: str,
    version: str,
    archive_path: Path,
    pack_root: Path,
    public_key: Any,
    public_key_sha256: str,
) -> dict[str, Any]:
    _ensure_plain_file(catalog_path, code="CATALOG_MISSING")
    _ensure_plain_file(archive_path, code="ARCHIVE_MISSING")
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("CATALOG_INVALID", "engine catalog is invalid UTF-8 JSON") from exc
    if not isinstance(catalog, dict) or set(catalog) != {"schema", "engines"} or catalog.get("schema") != "mentorflow.qwen-engine-catalog.v1":
        raise BuildError("CATALOG_INVALID", "engine catalog schema/fields are invalid")
    if not isinstance(catalog.get("engines"), list):
        raise BuildError("CATALOG_INVALID", "engine catalog entries must be a list")
    try:
        requested_package = _safe_pack_token(package_id, field="catalog.package_id")
        requested_version = _safe_pack_token(version, field="catalog.version")
    except BuildError as exc:
        raise BuildError("CATALOG_IDENTITY_INVALID", "package id and version must be Windows-safe tokens") from exc
    matches = []
    entry_fields = {"kind", "package_id", "version", "archive", "manifest", "signing_key_id"}
    digest_fields = {"bytes", "sha256"}
    for index, entry in enumerate(catalog["engines"]):
        if not isinstance(entry, dict) or set(entry) != entry_fields:
            raise BuildError("CATALOG_INVALID", f"catalog entry {index} fields are invalid")
        if entry.get("kind") != ENGINE_KIND:
            raise BuildError("CATALOG_INVALID", f"catalog entry {index} engine kind is unsupported")
        for field in ("archive", "manifest"):
            digest = entry.get(field)
            if (
                not isinstance(digest, dict)
                or set(digest) != digest_fields
                or not isinstance(digest.get("bytes"), int)
                or digest["bytes"] <= 0
                or not HEX64.fullmatch(str(digest.get("sha256") or ""))
            ):
                raise BuildError("CATALOG_INVALID", f"catalog entry {index} {field} digest is invalid")
        if (
            entry.get("package_id") == requested_package
            and entry.get("version") == requested_version
        ):
            matches.append(entry)
    if len(matches) != 1:
        raise BuildError("CATALOG_RELEASE_NOT_UNIQUE", "requested package/version is not uniquely cataloged")
    entry = matches[0]
    try:
        key_id = _safe_pack_token(entry.get("signing_key_id"), field="catalog.signing_key_id")
    except BuildError as exc:
        raise BuildError("CATALOG_INVALID", "catalog signing key id is invalid") from exc
    if archive_path.stat().st_size != entry["archive"]["bytes"] or sha256_file(archive_path) != entry["archive"]["sha256"]:
        raise BuildError("ARCHIVE_CATALOG_MISMATCH", "archive bytes/SHA256 do not match the catalog")
    manifest_path = pack_root / "engine-manifest.json"
    _ensure_plain_file(manifest_path, code="PACK_METADATA_MISSING")
    raw_manifest = manifest_path.read_bytes()
    if len(raw_manifest) != entry["manifest"]["bytes"] or _sha256_bytes(raw_manifest) != entry["manifest"]["sha256"]:
        raise BuildError("MANIFEST_CATALOG_MISMATCH", "manifest bytes/SHA256 do not match the catalog")
    report = verify_pack(
        pack_root,
        public_key,
        public_key_sha256,
        expected_key_id=key_id,
    )
    if report["pack_id"] != requested_package or report["version"] != requested_version:
        raise BuildError("MANIFEST_CATALOG_MISMATCH", "signed manifest identity does not match the catalog request")
    report["archive_sha256"] = entry["archive"]["sha256"]
    report["catalog_path"] = str(catalog_path)
    report["signing_key_id"] = key_id
    return report


def _serializable_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ready",
        "mode": "plan",
        "copies_files": False,
        "pack_id": plan["pack_id"],
        "version": plan["version"],
        "platform": PLATFORM,
        "file_count": plan["file_count"],
        "bytes": plan["bytes"],
        "spec_sha256": plan["spec_sha256"],
        "path_budget": plan["path_budget"],
        "signing": {"algorithm": "ed25519", "key_id": plan["signing"]["key_id"], "trusted_public_key_sha256": plan["signing"]["public_key_sha256"]},
        "components": [
            {
                "id": item.component["id"],
                "kind": item.component["kind"],
                "version": item.component["version"],
                "files": len(item.files),
                "bytes": sum(record.size for record in item.files),
                "tree_sha256": item.tree_sha256,
            }
            for item in plan["plans"]
        ],
        "outputs": ["engine-manifest.json", "engine-manifest.sig", "SBOM.spdx.json", "THIRD_PARTY_NOTICES_INDEX.json", "THIRD_PARTY_NOTICES.txt", "FILES.sha256", "win-x64.zip", "engine-catalog.json"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan, build, or verify a relocatable Qwen3-TTS 0.6B win-x64 runtime pack.")
    parser.add_argument("--spec", type=Path, help="pinned build specification (required for plan/build)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", action="store_true", help="validate and hash inputs without copying (default)")
    modes.add_argument("--build", action="store_true", help="explicitly copy allowlisted inputs and sign the pack")
    modes.add_argument("--verify-only", action="store_true", help="verify an already extracted pack without copying")
    parser.add_argument("--output-root", type=Path, help="build destination root; never inferred")
    parser.add_argument("--pack-root", type=Path, help="pack directory for --verify-only")
    parser.add_argument("--catalog", type=Path, help="trusted app engine catalog for --verify-only")
    parser.add_argument("--archive", type=Path, help="catalog-bound release archive for --verify-only")
    parser.add_argument("--package-id", default="", help="catalog package id for --verify-only")
    parser.add_argument("--version", default="", help="catalog version for --verify-only")
    parser.add_argument("--trusted-public-key", type=Path, help="external trusted Ed25519 public key for --verify-only")
    parser.add_argument("--trusted-public-key-sha256", default="", help="SHA256 fingerprint of raw trusted Ed25519 public key")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.verify_only:
            if not all((args.pack_root, args.catalog, args.archive, args.package_id, args.version, args.trusted_public_key)):
                raise BuildError(
                    "VERIFY_ARGUMENTS_MISSING",
                    "--pack-root, --catalog, --archive, --package-id, --version, and --trusted-public-key are required for --verify-only",
                )
            key, fingerprint = _public_key_for_verify(args.trusted_public_key.absolute(), args.trusted_public_key_sha256)
            result = verify_catalog_release(
                catalog_path=args.catalog.absolute(),
                package_id=args.package_id,
                version=args.version,
                archive_path=args.archive.absolute(),
                pack_root=args.pack_root,
                public_key=key,
                public_key_sha256=fingerprint,
            )
        else:
            if not args.spec:
                raise BuildError("SPEC_MISSING", "--spec is required for plan/build")
            plan = plan_from_spec(args.spec)
            if args.build:
                if not args.output_root:
                    raise BuildError("OUTPUT_ROOT_MISSING", "--output-root is required for --build")
                result = build_from_plan(plan, args.output_root)
            else:
                result = _serializable_plan(plan)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except BuildError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
