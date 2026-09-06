"""Build and verify a MentorFlow Windows package.

This module intentionally uses only the Python standard library.  The copy in
``SELF-CHECK`` is safe to run from a shipped package (or from the source tree)
and never opens a user database for writing.  Update downloads are opt-in and
are staged under E: so an archive can be reviewed and installed manually.
"""

from __future__ import annotations

import argparse
import hashlib
import functools
from contextlib import contextmanager
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE_NAME = "mentorflow-version.json"
MANIFEST_RELATIVE = Path("SELF-CHECK") / "package-manifest.json"
MANIFEST_SCHEMA = "mentorflow.windows-package-manifest.v1"
VERSION_SCHEMA = "mentorflow.version.v1"
VERSION_RE = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
USER_DATA_NAMES = {
    "app.db",
    "mentorflow_v2.db",
    "minimax_api_key.txt",
    "ui-review-manifest.json",
    "preset_settings.json",
    "sms_gateway.json",
    "development-data-import.json",
    "minimax_teacher_voice.local.json",
}
# A package may contain user-facing words such as ``teacher`` and ``voice``,
# so this is intentionally a small, conservative set of path components.  A
# release containing any of these is almost certainly a private export or a
# development fixture, and should fail with its exact relative path.
PRIVATE_PATH_COMPONENTS = {
    "private",
    "secrets",
    "secret",
    "credentials",
    "credential",
    "uploads",
    "upload",
    "logs",
    "cache",
    "caches",
    "backup",
    "backups",
    "chat",
    "chats",
    "user_data",
    "userdata",
    "user-data",
    "ui-review",
    "ui_review",
    "uireview",
    "dev_fixture_minimal",
    "dev-fixture",
    "dev_fixtures",
    "dev-fixtures",
    "e2e-dev-art-pack",
    "test_fixture",
    "test-fixture",
    "__pycache__",
}
PRIVATE_NAME_RE = re.compile(
    r"(?:^|[._-])(?:api[_-]?keys?|access[_-]?tokens?|auth[_-]?tokens?|tokens?|passwords?|passwd|secrets?|credentials?|private(?:[_-]?keys?)?|account|history|user[_-]?data|preset[_-]?settings|sms[_-]?gateway|development[_-]?data|ui[-_]?review)(?:$|[._-])",
    re.IGNORECASE,
)
PRIVATE_FILE_STEMS = {
    "account",
    "chat",
    "chats",
    "upload",
    "uploads",
    "private",
    "secret",
    "credential",
}
PRIVATE_FILE_NAMES = {
    "account.json",
    "chat_history.json",
    "credentials.json",
    "log.json",
    "logs.json",
    "local.json",
    "profile.json",
    "secrets.json",
    "user.json",
}
STALE_BYTECODE_SUFFIXES = {".pyc", ".pyo"}
# PyInstaller places third-party libraries under ``_internal``.  Generic
# words such as ``cache``, ``private`` and ``token`` are valid dependency
# names there, but package-owned data/content/config trees must remain
# fail-closed.  Only the immediate child is treated as an application-owned
# subtree so a dependency's own nested ``data`` directory is not overmatched.
APPLICATION_MANAGED_INTERNAL_ROOTS = {
    "content",
    "config",
    "configs",
    "data",
    "dev_fixture_minimal",
    "dev-fixture",
    "dev_fixtures",
    "dev-fixtures",
    "e2e-dev-art-pack",
    "test_fixture",
    "test-fixture",
    "ui-review",
    "ui_review",
    "uireview",
    "user_data",
    "userdata",
    "user-data",
    "user-state",
    "user_state",
}
STALE_PATH_COMPONENTS = {"__pycache__"}
SQLITE_MAGIC = b"SQLite format 3\x00"
CONFIG_SCAN_SUFFIXES = {".cfg", ".conf", ".env", ".ini", ".json", ".json5", ".toml", ".yaml", ".yml"}
MAX_CONFIG_SCAN_BYTES = 2 * 1024 * 1024
SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|authorization|bearer[_-]?token|client[_-]?secret|secret|credential|password|passwd|private[_-]?key|signing[_-]?key|encryption[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)
SECRET_PLACEHOLDER_RE = re.compile(
    r"^(?:$|none|null|nil|n/a|na|unknown|(?:example|sample|dummy|test)(?:[-_ ]?(?:only|api|access|auth|client|key|token|secret|password|credential|value))*|changeme|change[-_ ]?me|replace(?:[-_ ]?me)?|your(?:[-_ ]?(?:api|access|auth|client|secret|token|password|credential))?[-_ ]?(?:key|token|secret|password|credential)?|<[^>]+>|\$\{[^}]+\}|\{\{[^}]+\}\})$",
    re.IGNORECASE,
)
MAX_RELEASE_JSON_BYTES = 8 * 1024 * 1024
# Keep automatic update staging bounded even when an upstream API omits a
# Content-Length header.  This is large enough for the normal Windows bundle
# while preventing an unbounded response from filling the E: staging volume.
MAX_UPDATE_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
# Read-only catalogs shipped as application assets are not user data.  Keep
# this allowlist exact: any other SQLite file in the distributable remains a
# hard failure.
PACKAGED_STATIC_DATABASES = {
    "_internal/content/narrative_catalog/v2/narrative_catalog_v2.sqlite",
}


class PackageError(RuntimeError):
    """An actionable package/update validation error."""


def _json_load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"cannot read JSON manifest: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"JSON manifest must be an object: {path}")
    return value


def load_version(path: Path) -> dict[str, Any]:
    payload = _json_load(path)
    if payload.get("schema") != VERSION_SCHEMA:
        raise PackageError(f"unsupported version manifest schema in {path}")
    if payload.get("product") != "MentorFlow":
        raise PackageError(f"version manifest product is not MentorFlow: {path}")
    version = str(payload.get("version") or "")
    if not VERSION_RE.fullmatch(version):
        raise PackageError(f"invalid MentorFlow version: {version!r}")
    update = payload.get("update")
    if update is not None and not isinstance(update, dict):
        raise PackageError("version manifest update settings must be an object")
    return payload


_hash_buffers = threading.local()


def sha256_file(path: Path, *, chunk_size: int = 256 * 1024, prefix: bytearray | None = None) -> str:
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("hash chunk size must be a positive integer")
    # One reusable buffer per reader, not a new large allocation for each of
    # the thousands of tiny package files. Raw readinto also avoids buffering
    # and copying the same bytes twice. This never reuses a digest or file data.
    buffer = getattr(_hash_buffers, "buffer", None)
    if buffer is None or len(buffer) != chunk_size:
        buffer = _hash_buffers.buffer = bytearray(chunk_size)
    view = memoryview(buffer)
    digest = hashlib.sha256()
    if prefix is not None:
        prefix.clear()
    try:
        with path.open("rb", buffering=0) as handle:
            while size := handle.readinto(view):
                chunk = view[:size]
                if prefix is not None and len(prefix) < len(SQLITE_MAGIC):
                    prefix.extend(chunk[:len(SQLITE_MAGIC) - len(prefix)])
                digest.update(chunk)
    except OSError as exc:
        raise PackageError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _is_application_managed_path(relative: str) -> bool:
    """Return whether generic private-name rules apply to this package path.

    Top-level package paths are application-owned.  A path under PyInstaller's
    ``_internal`` root is dependency-owned unless its immediate child is one
    of the known application data/content/config/state or fixture roots.
    Content and config scans below those roots therefore remain strict while
    arbitrary third-party names such as ``torch/_vendor/quack/cache`` do not
    trigger a private-path finding by themselves.
    """
    parts = tuple(part.casefold() for part in Path(relative).parts)
    return _application_managed_parts(parts)


def _application_managed_parts(parts: tuple[str, ...]) -> bool:
    """Pure rule over already normalized/casefolded path components."""
    if not parts or parts[0] != "_internal":
        return True
    return len(parts) > 1 and parts[1] in APPLICATION_MANAGED_INTERNAL_ROOTS


def _validate_package_root(candidate: Path) -> Path:
    """Validate the package root before resolving it or scanning children."""
    candidate = candidate.expanduser().absolute()
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as exc:
        raise PackageError(f"package root does not exist: {candidate}") from exc
    except OSError as exc:
        raise PackageError(f"cannot inspect package root {candidate}: {exc}") from exc
    if candidate.is_symlink():
        raise PackageError(f"package root is a symlink/reparse point: {candidate}")
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if attributes & reparse_flag:
        raise PackageError(f"package root is a Windows reparse point: {candidate}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise PackageError(f"package root is not a directory: {candidate}")
    # A normal directory can still be reached through a junction/symlinked
    # parent.  Reject that alias before resolving the root used for manifest
    # paths so callers cannot silently verify a package outside their tree.
    current = candidate.parent
    anchor = Path(candidate.anchor)
    while current != anchor:
        try:
            parent_metadata = current.lstat()
        except OSError as exc:
            raise PackageError(f"cannot inspect package root parent {current}: {exc}") from exc
        if current.is_symlink():
            raise PackageError(f"package root parent is a symlink/reparse point: {current}")
        parent_attributes = int(getattr(parent_metadata, "st_file_attributes", 0) or 0)
        if parent_attributes & reparse_flag:
            raise PackageError(f"package root parent is a Windows reparse point: {current}")
        current = current.parent
    return candidate


def _validate_existing_parent_chain(candidate: Path, *, label: str) -> Path:
    """Validate every existing component before creating a staging path.

    ``Path.resolve`` follows junctions and symlinks, so it cannot be used as a
    safety check before a directory is created.  Walk the lexical path first
    and reject aliases/reparse points in both the leaf and all existing
    parents.  Missing components are safe to create once their existing
    ancestor chain has passed this check.
    """
    candidate = candidate.expanduser().absolute()
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    anchor = Path(candidate.anchor)
    current = candidate
    while True:
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            metadata = None
        except OSError as exc:
            raise PackageError(f"cannot inspect {label} path {current}: {exc}") from exc
        if metadata is not None:
            if current.is_symlink():
                raise PackageError(f"{label} path is a symlink/reparse point: {current}")
            attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
            if attributes & reparse_flag:
                raise PackageError(f"{label} path is a Windows reparse point: {current}")
            if current != candidate and not stat.S_ISDIR(metadata.st_mode):
                raise PackageError(f"{label} parent is not a directory: {current}")
        if current == anchor:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return candidate


def _validate_staging_entry(
    candidate: Path,
    root: Path,
    *,
    directory: bool,
    allow_missing: bool,
    label: str = "update staging",
) -> bool:
    """Validate one update staging entry without following aliases."""
    candidate = _validate_existing_parent_chain(candidate, label=label)
    root = root.expanduser().absolute()
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PackageError(f"cannot resolve {label} path {candidate}: {exc}") from exc
    if not _is_within(resolved, root):
        raise PackageError(f"{label} path resolves outside staging root: {candidate} -> {resolved}")
    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        if allow_missing:
            return False
        raise PackageError(f"{label} path is missing: {candidate}")
    except OSError as exc:
        raise PackageError(f"cannot inspect {label} path {candidate}: {exc}") from exc
    if candidate.is_symlink():
        raise PackageError(f"{label} path is a symlink/reparse point: {candidate}")
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if attributes & reparse_flag:
        raise PackageError(f"{label} path is a Windows reparse point: {candidate}")
    if directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise PackageError(f"{label} path is not a directory: {candidate}")
    else:
        if not stat.S_ISREG(metadata.st_mode):
            raise PackageError(f"{label} path is not a regular file: {candidate}")
        if int(getattr(metadata, "st_nlink", 1) or 1) != 1:
            raise PackageError(f"{label} path is an unsafe hardlink: {candidate}")
    return True


def _validate_package_entry(candidate: Path, package_root: Path, *, directory: bool, resolve_path: bool = True, cached_stat=None, cached_relative: str | None = None) -> None:
    """Fail closed for links, reparse points, special files, and escapes."""
    try:
        relative = _relative(candidate, package_root) if cached_relative is None else cached_relative
        relative_path = Path(relative)
        if relative_path.drive or relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("parent traversal is not a package entry")
        if cached_relative is not None and package_root / relative_path != candidate:
            raise ValueError("cached relative path does not identify candidate")
        resolved = candidate.resolve(strict=False) if resolve_path else candidate
        metadata = cached_stat if cached_stat is not None else candidate.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PackageError(f"cannot inspect package path {candidate}: {exc}") from exc
    # The caller supplies the validated, resolved package root. Do not resolve
    # both paths again for every file: this is a lexical comparison of the
    # already resolved path, not a weaker mtime/size trust decision.
    # The relative-path check above proves lexical containment. Only a resolved
    # path needs a second containment check (e.g. an alias escaping the root).
    if resolve_path and not resolved.is_relative_to(package_root):
        raise PackageError(f"package path resolves outside package root: {relative} -> {resolved}")
    if stat.S_ISLNK(metadata.st_mode):
        raise PackageError(f"package contains symlink/reparse path: {relative}")
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if attributes & reparse_flag:
        raise PackageError(f"package contains a Windows reparse point: {relative}")
    if directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise PackageError(f"package directory entry is not a directory: {relative}")
    else:
        if not stat.S_ISREG(metadata.st_mode):
            raise PackageError(f"package contains a special/non-regular file: {relative}")
        if int(getattr(metadata, "st_nlink", 1) or 1) != 1:
            raise PackageError(f"package contains a hardlink; copy the file instead: {relative}")
    parts = tuple(part.casefold() for part in relative_path.parts)
    if any(part in STALE_PATH_COMPONENTS for part in parts):
        raise PackageError(f"package contains stale Python bytecode path: {relative}")
    if _application_managed_parts(parts) and any(part in PRIVATE_PATH_COMPONENTS for part in parts):
        raise PackageError(f"package contains a private/development path: {relative}")
    if relative_path.suffix.casefold() in STALE_BYTECODE_SUFFIXES:
        raise PackageError(f"package contains stale Python bytecode: {relative}")


def _iter_package_entries(package_root: Path, *, resolve_paths: bool = True) -> Iterable[tuple[Path, bool]]:
    """Yield package entries without ever following links or reparse points."""
    pending = [package_root]
    while pending:
        current = pending.pop()
        try:
            children = sorted(os.scandir(current), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise PackageError(f"cannot scan package directory {current}: {exc}") from exc
        for entry in children:
            candidate = Path(entry.path)
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                raise PackageError(f"cannot inspect package path {candidate}: {exc}") from exc
            _validate_package_entry(candidate, package_root, directory=is_directory, resolve_path=resolve_paths)
            if is_directory:
                pending.append(candidate)
            else:
                yield candidate, False


def _ensure_e_drive(path: Path, *, label: str) -> None:
    """Reject accidental C: staging on Windows while remaining testable elsewhere."""
    if os.name == "nt" and path.drive and path.drive.upper() != "E:":
        raise PackageError(f"{label} must be on E: (got {path})")


def _normalise_secret_key(value: str) -> str:
    """Make common camelCase config keys comparable without exposing values."""
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[\s.]+", "_", value).strip("_")


def _secret_value_is_nonempty(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(text) and not SECRET_PLACEHOLDER_RE.fullmatch(text)


def _json_has_secret_value(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and SECRET_KEY_RE.search(_normalise_secret_key(key)):
                if _secret_value_is_nonempty(child):
                    return True
                # An object/list under a secret key is only suspicious when it
                # contains another non-empty secret-like field.  Empty public
                # schema objects therefore remain valid.
                if isinstance(child, (dict, list)) and _json_has_secret_value(child):
                    return True
            elif _json_has_secret_value(child):
                return True
    elif isinstance(value, list):
        return any(_json_has_secret_value(item) for item in value)
    return False


def _text_has_secret_value(text: str) -> bool:
    """Check simple KEY=VALUE/YAML config lines without scanning code or prose."""
    line_re = re.compile(
        r"^\s*[\"']?([A-Za-z][A-Za-z0-9_.-]*)[\"']?\s*[:=]\s*(.*?)\s*(?:,|#.*)?$"
    )
    for line in text.splitlines():
        match = line_re.match(line)
        if not match or not SECRET_KEY_RE.search(_normalise_secret_key(match.group(1))):
            continue
        raw_value = match.group(2).strip().strip("\\\"'").strip()
        if _secret_value_is_nonempty(raw_value):
            return True
    return False


def _config_contains_secret(candidate: Path) -> bool:
    """Return whether a bounded text/JSON config contains secret material.

    Only configuration-like suffixes are considered.  JavaScript, binaries and
    other large package resources are intentionally skipped to avoid broad
    content scanning and false positives from public schema names.
    """
    if candidate.suffix.casefold() not in CONFIG_SCAN_SUFFIXES:
        return False
    try:
        if candidate.stat().st_size > MAX_CONFIG_SCAN_BYTES:
            return False
        raw = candidate.read_bytes()
    except OSError as exc:
        raise PackageError(f"cannot inspect package config {candidate}: {exc}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    if candidate.suffix.casefold() in {".json", ".json5"}:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # JSON5/YAML-like files can still use a simple key/value form.
            return _text_has_secret_value(text)
        return _json_has_secret_value(payload)
    return _text_has_secret_value(text)


def _find_user_data_files(package_root: Path, *, validated_files: list[Path] | None = None, hashed_prefixes: dict[str, bytes] | None = None) -> list[str]:
    found: list[str] = []
    files = validated_files if validated_files is not None else (
        candidate for candidate, _ in _iter_package_entries(package_root)
    )
    for candidate in files:
        relative = _relative(candidate, package_root)
        if _is_application_managed_path(relative) and (
            candidate.name.casefold() in USER_DATA_NAMES
            or candidate.name.casefold() in PRIVATE_FILE_NAMES
            or candidate.stem.casefold() in PRIVATE_FILE_STEMS
            or PRIVATE_NAME_RE.search(candidate.name)
        ):
            found.append(relative)
        if _config_contains_secret(candidate):
            found.append(relative)
        if candidate.suffix.casefold() in {".db", ".sqlite", ".sqlite3"} and relative not in PACKAGED_STATIC_DATABASES:
            found.append(relative)
        # Do not trust an extension: a private SQLite database can be renamed
        # to an innocuous-looking asset before it reaches the package.
        if hashed_prefixes is not None:
            if relative not in hashed_prefixes:
                raise PackageError(f"missing hashed file prefix: {relative}")
            magic = hashed_prefixes[relative]
        else:
            try:
                with candidate.open("rb") as handle:
                    magic = handle.read(len(SQLITE_MAGIC))
            except OSError as exc:
                raise PackageError(f"cannot inspect package file {relative}: {exc}") from exc
        if magic == SQLITE_MAGIC and relative not in PACKAGED_STATIC_DATABASES:
            found.append(relative)
    return sorted(set(found))


def _iter_package_files(package_root: Path, manifest_path: Path) -> Iterable[Path]:
    # Entries are independently validated by the iterator (including resolved
    # containment, reparse points and hardlinks). Excluding the manifest by its
    # lexical name needs no second filesystem resolution for every payload file.
    manifest_absolute = manifest_path.absolute()
    for candidate, _ in _iter_package_entries(package_root):
        if candidate.absolute() != manifest_absolute:
            yield candidate


def build_manifest(package_root: Path, *, version_file: Path | None = None) -> Path:
    package_root = _validate_package_root(package_root).resolve(strict=False)
    version_path = version_file or package_root / VERSION_FILE_NAME
    if not version_path.is_file():
        raise PackageError(f"version manifest is missing: {version_path}")
    _validate_package_entry(version_path, package_root, directory=False)
    version = load_version(version_path)
    user_data = _find_user_data_files(package_root)
    if user_data:
        raise PackageError("package contains user data, database, or secret configuration files: " + ", ".join(user_data))
    app = package_root / "MentorFlow.exe"
    if not app.is_file():
        raise PackageError(f"frozen executable is missing: {app}")
    manifest_path = package_root / MANIFEST_RELATIVE
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for candidate in _iter_package_files(package_root, manifest_path):
        files.append(
            {
                "path": _relative(candidate, package_root),
                "size": candidate.stat().st_size,
                "sha256": sha256_file(candidate),
            }
        )
    files.sort(key=lambda item: item["path"])
    payload = {
        "schema": MANIFEST_SCHEMA,
        "product": "MentorFlow",
        "version": version["version"],
        "executable": "MentorFlow.exe",
        "files": files,
    }
    temporary = manifest_path.with_suffix(".json.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    return manifest_path


def verify_package(package_root: Path, *, data_root: Path | None, check_data: bool, smoke: bool, smoke_seconds: float) -> dict[str, Any]:
    package_root = _validate_package_root(package_root).resolve(strict=False)
    manifest_path = package_root / MANIFEST_RELATIVE
    if not manifest_path.is_file():
        raise PackageError(f"package manifest is missing: {manifest_path}")
    _validate_package_entry(manifest_path, package_root, directory=False)
    manifest = _json_load(manifest_path)
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("product") != "MentorFlow":
        raise PackageError(f"unsupported package manifest: {manifest_path}")
    version_path = package_root / VERSION_FILE_NAME
    _validate_package_entry(version_path, package_root, directory=False)
    version = load_version(version_path)
    if manifest.get("version") != version["version"]:
        raise PackageError(
            f"package version mismatch: manifest={manifest.get('version')!r}, version={version['version']!r}"
        )
    required = (
        Path("MentorFlow.exe"),
        Path(VERSION_FILE_NAME),
        MANIFEST_RELATIVE,
        Path("SELF-CHECK") / "MentorFlowSelfCheck.exe",
        Path("SELF-CHECK") / "verify_windows_package.py",
        Path("SELF-CHECK") / "mentorflow-check.bat",
        Path("SELF-CHECK") / "mentorflow-update.bat",
    )
    missing = [str(path) for path in required if not (package_root / path).is_file()]
    if missing:
        raise PackageError("required package files are missing: " + ", ".join(missing))
    # One complete resolved security traversal per verification. Reuse its
    # inventory for privacy checks; every listed payload is still
    # read and SHA-256 checked on every launch.
    validated_files = [candidate for candidate, _ in _iter_package_entries(package_root)]
    user_data = _find_user_data_files(package_root, validated_files=validated_files)
    if user_data:
        raise PackageError("package contains user data, database, or secret configuration files: " + ", ".join(user_data))
    mismatches: list[str] = []
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise PackageError("package manifest files must be a list")
    listed_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise PackageError("package manifest has an invalid file entry")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise PackageError(f"unsafe package manifest path: {entry['path']}")
        listed_paths.add(relative.as_posix())
        candidate = package_root / relative
        if not candidate.is_file():
            mismatches.append(f"missing:{entry['path']}")
            continue
        expected_size = entry.get("size")
        if not isinstance(expected_size, int) or expected_size < 0:
            raise PackageError(f"invalid file size in package manifest: {entry['path']}")
        try:
            actual_size = candidate.stat().st_size
        except OSError as exc:
            raise PackageError(f"cannot stat package file {entry['path']}: {exc}") from exc
        if actual_size != expected_size:
            mismatches.append(f"size:{entry['path']}:{actual_size}!={expected_size}")
        expected = str(entry.get("sha256") or "").lower()
        if not SHA256_RE.fullmatch(expected):
            raise PackageError(f"invalid SHA-256 in package manifest: {entry['path']}")
        actual = sha256_file(candidate)
        if actual != expected:
            mismatches.append(f"tampered:{entry['path']}")
    # Re-enumerate after hashing to reject late additions/replaced links. This
    # walk checks every component with lstat before descending, never follows
    # reparse points, and checks lexical containment under the revalidated root.
    # It needs no repeat filesystem resolution of every ancestor of every file.
    _validate_package_root(package_root)
    actual_paths = {
        _relative(candidate, package_root)
        for candidate, _ in _iter_package_entries(package_root, resolve_paths=False)
        if candidate != manifest_path
    }
    for extra in sorted(actual_paths - listed_paths):
        mismatches.append(f"unexpected:{extra}")
    if mismatches:
        raise PackageError("package integrity check failed: " + ", ".join(mismatches))
    data_report: dict[str, Any] = {"databases": 0, "errors": []}
    if check_data:
        data_report = check_sqlite_root(data_root or Path(r"E:\MentorFlowData"), package_root=package_root)
        if data_report["errors"]:
            raise PackageError("SQLite integrity check failed: " + "; ".join(data_report["errors"]))
    smoke_report: dict[str, Any] = {"checked": False}
    if smoke:
        smoke_report = smoke_launch(package_root / "MentorFlow.exe", package_root, seconds=smoke_seconds)
        if not smoke_report.get("ok"):
            raise PackageError(str(smoke_report.get("error") or "MentorFlow startup smoke failed"))
    return {
        "version": version["version"],
        "files": len(entries),
        "data": data_report,
        "smoke": smoke_report,
    }


def _validate_data_entry(candidate: Path, root: Path, *, directory: bool) -> None:
    """Validate an active data-root entry without following links or aliases."""
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PackageError(f"cannot inspect data path {candidate}: {exc}") from exc
    if not _is_within(resolved, root):
        raise PackageError(f"data path resolves outside data root: {candidate} -> {resolved}")
    if candidate.is_symlink():
        raise PackageError(f"data path is a symlink/reparse point: {candidate}")
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if attributes & reparse_flag:
        raise PackageError(f"data path is a Windows reparse point: {candidate}")
    if directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise PackageError(f"data path is not a directory: {candidate}")
    else:
        if not stat.S_ISREG(metadata.st_mode):
            raise PackageError(f"data path is not a regular file: {candidate}")
        if int(getattr(metadata, "st_nlink", 1) or 1) != 1:
            raise PackageError(f"data path is a hardlink; copy the database instead: {candidate}")


def _path_exists(candidate: Path) -> bool:
    try:
        candidate.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PackageError(f"cannot inspect data path {candidate}: {exc}") from exc
    return True


def _iter_data_entries(data_root: Path) -> Iterable[tuple[Path, bool]]:
    """Yield every data-root entry after validating it without following aliases."""
    pending = [data_root]
    while pending:
        current = pending.pop()
        try:
            children = sorted(os.scandir(current), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise PackageError(f"cannot scan data directory {current}: {exc}") from exc
        for entry in children:
            candidate = Path(entry.path)
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                raise PackageError(f"cannot inspect data path {candidate}: {exc}") from exc
            _validate_data_entry(candidate, data_root, directory=is_directory)
            if is_directory:
                pending.append(candidate)
            else:
                yield candidate, False


def check_sqlite_root(data_root: Path, *, package_root: Path | None = None) -> dict[str, Any]:
    data_root = data_root.expanduser()
    if _path_exists(data_root):
        _validate_data_entry(data_root, data_root.parent.resolve(strict=False), directory=True)
    data_root_resolved = data_root.resolve(strict=False)
    package_root_resolved = package_root.resolve(strict=False) if package_root else None
    if package_root_resolved and _is_within(data_root_resolved, package_root_resolved):
        raise PackageError("data root must not be inside the installer/package directory")
    _ensure_e_drive(data_root_resolved, label="data root")
    report: dict[str, Any] = {"root": str(data_root_resolved), "databases": 0, "errors": []}
    if not _path_exists(data_root):
        return report
    # A MentorFlow data root also contains immutable backups and disposable
    # UI-review copies.  Those are deliberately historical and must not make a
    # check of the *active* local databases fail.  When a standard root is
    # supplied, inspect only its canonical ``data`` directory; callers may
    # still pass that directory directly.
    data_subdir = data_root / "data"
    if _path_exists(data_subdir):
        _validate_data_entry(data_subdir, data_root_resolved, directory=True)
        database_root = data_subdir
    else:
        database_root = data_root
    database_root_resolved = database_root.resolve(strict=False)
    if not _is_within(database_root_resolved, data_root_resolved):
        raise PackageError(f"database root resolves outside data root: {database_root} -> {database_root_resolved}")
    report["database_root"] = str(database_root)
    candidates = []
    for pattern in ("*.db", "*.sqlite", "*.sqlite3"):
        candidates.extend(sorted(database_root.glob(pattern)))
    for candidate in candidates:
        if candidate.name.endswith(("-wal", "-shm")):
            continue
        _validate_data_entry(candidate, database_root_resolved, directory=False)
        report["databases"] += 1
        try:
            connection = sqlite3.connect(candidate.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                if result.lower() != "ok":
                    report["errors"].append(f"{candidate}: integrity_check={result}")
                foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
                if foreign:
                    report["errors"].append(f"{candidate}: foreign_key_check={len(foreign)}")
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            report["errors"].append(f"{candidate}: {exc}")
    return report


_FRESH_INSTALL_TABLES = {
    "workspaces": ("workspaces", "orm_workspaces", "active_workspace_settings"),
    "sources": ("sources", "orm_sources", "active_materials"),
    "knowledge_maps": ("orm_knowledge_map_revisions", "active_knowledge_points"),
    "chat_sessions": ("casual_chat_sessions", "active_teaching_sessions"),
    "chat_messages": ("active_teaching_messages",),
    "progress": ("orm_progress", "active_knowledge_progress_snapshots", "active_review_items"),
    "knowledge_progress": ("active_knowledge_progress_snapshots",),
    # Keep retired authentication tables in the fresh-install privacy report.
    # Their rows remain user state even when the current runtime no longer
    # reads them, and historical aliases must not be hidden by a one-table
    # special case.
    "local_auth": (
        "orm_local_users",
        "local_users",
        "users",
        "user_accounts",
        "orm_local_sessions",
        "local_sessions",
        "orm_workspace_ownership",
        "workspace_ownership",
        "orm_local_phone_challenges",
        "local_phone_challenges",
        "phone_challenges",
    ),
    "credential_state": (
        "orm_password_reset_challenges",
        "password_reset_challenges",
        "orm_login_throttle",
        "login_throttle",
    ),
}
_CREDENTIAL_TABLE_RE = re.compile(
    r"(?:^|_)(?:credential|credentials|secret|secrets|token|tokens|api[_-]?keys?|access[_-]?tokens?|passwords?)(?:_|$)",
    re.IGNORECASE,
)


def _sqlite_count(connection: sqlite3.Connection, table: str) -> int:
    quoted = '"' + table.replace('"', '""') + '"'
    return int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])


def _fresh_install_report(data_root: Path, *, package_root: Path) -> dict[str, Any]:
    """Inspect a disposable install without ever opening a DB read/write."""
    data_root = data_root.expanduser()
    report = check_sqlite_root(data_root, package_root=package_root)
    data_root = Path(report["root"])
    counts = {name: 0 for name in _FRESH_INSTALL_TABLES}
    errors = list(report.get("errors") or [])
    private_files: list[str] = []
    data_subdir = data_root / "data"
    database_root = data_subdir if _path_exists(data_subdir) else data_root
    if _path_exists(data_subdir):
        _validate_data_entry(data_subdir, data_root, directory=True)
    canonical_databases = {
        database_root / "app.db",
        database_root / "mentorflow_v2.db",
    }
    entries = list(_iter_data_entries(data_root)) if _path_exists(data_root) else []
    for candidate, _ in entries:
        relative = _relative(candidate, data_root)
        is_canonical_database = candidate in canonical_databases
        # The expected fresh-install databases are the only SQLite paths that
        # may exist.  Check both the name and magic so an innocuous extension
        # cannot hide an extra database.
        if not is_canonical_database:
            if candidate.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
                errors.append(f"fresh install contains noncanonical database: {relative}")
            try:
                with candidate.open("rb") as handle:
                    magic = handle.read(len(SQLITE_MAGIC))
            except OSError as exc:
                raise PackageError(f"cannot inspect data file {relative}: {exc}") from exc
            if magic == SQLITE_MAGIC:
                errors.append(f"fresh install contains noncanonical SQLite database: {relative}")
            if (
                candidate.name.casefold() in USER_DATA_NAMES
                or candidate.name.casefold() in PRIVATE_FILE_NAMES
                or candidate.stem.casefold() in PRIVATE_FILE_STEMS
                or PRIVATE_NAME_RE.search(candidate.name)
                or _config_contains_secret(candidate)
            ):
                # The database names above are expected fresh-install state;
                # all other credential/private names are release failures.
                private_files.append(relative)
    for database_name in ("app.db", "mentorflow_v2.db"):
        candidate = database_root / database_name
        if not _path_exists(candidate):
            errors.append(f"fresh install database is missing: {candidate}")
            continue
        _validate_data_entry(candidate, database_root.resolve(strict=False), directory=False)
        try:
            connection = sqlite3.connect(candidate.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                for key, tables in _FRESH_INSTALL_TABLES.items():
                    for table in tables:
                        if connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                        ).fetchone():
                            counts[key] += _sqlite_count(connection, table)
                for (table_name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall():
                    if table_name.casefold() == "alembic_version":
                        continue
                    if not _CREDENTIAL_TABLE_RE.search(str(table_name)):
                        continue
                    row_count = _sqlite_count(connection, str(table_name))
                    if row_count:
                        errors.append(
                            f"{candidate}: credential table is not empty: {table_name}={row_count}"
                        )
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            errors.append(f"{candidate}: read-only fresh-install inspection failed: {exc}")
    if private_files:
        errors.append("fresh install contains credential/private files: " + ", ".join(sorted(private_files)))
    nonzero = {name: value for name, value in counts.items() if value}
    if nonzero:
        errors.append("fresh install is not clean: " + ", ".join(f"{name}={value}" for name, value in sorted(nonzero.items())))
    report.update({"counts": counts, "private_files": sorted(private_files), "errors": errors, "clean": not errors})
    return report


def _package_hashes(package_root: Path) -> dict[str, str]:
    manifest_path = package_root / MANIFEST_RELATIVE
    hashes = {
        _relative(candidate, package_root): sha256_file(candidate)
        for candidate in _iter_package_files(package_root, manifest_path)
    }
    _validate_package_entry(manifest_path, package_root, directory=False)
    hashes[_relative(manifest_path, package_root)] = sha256_file(manifest_path)
    return hashes


def smoke_launch(executable: Path, package_root: Path, *, seconds: float = 5.0) -> dict[str, Any]:
    """Launch twice against one disposable E: root and inspect clean state.

    The canonical user root is never passed to the child.  ``desktop.main``
    honours ``MENTORFLOW_DESKTOP_SMOKE_SECONDS`` and exits cleanly after its
    local health check.  Both launches reuse the same empty root so a second
    startup exercises persistence and migration paths without touching a real
    user's databases.  SQLite is inspected only through ``mode=ro`` URIs.
    """
    if not executable.is_file():
        return {"ok": False, "error": f"executable is missing: {executable}"}
    staging = Path(os.getenv("MENTORFLOW_UPDATE_STAGING", r"E:\MentorFlowUpdates"))
    _ensure_e_drive(staging, label="smoke staging root")
    staging.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="mentorflow-package-smoke-", dir=str(staging)))
    environment = os.environ.copy()
    environment.update(
        {
            "MENTORFLOW_USER_DATA_ROOT": str(temporary),
            "MENTORFLOW_LOCAL_DATA_MODE": "1",
            "MENTORFLOW_AUTH_REQUIRED": "0",
            "MENTORFLOW_DESKTOP_SMOKE_SECONDS": str(max(1.0, seconds)),
            "MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE": "1",
            "MENTORFLOW_UI_REVIEW_MODE": "0",
        }
    )
    package_hashes_before = _package_hashes(package_root)
    runs: list[dict[str, Any]] = []
    try:
        for attempt in range(1, 3):
            process: subprocess.Popen[bytes] | None = None
            stderr_file = None
            stderr_path = temporary / f"smoke-stderr-{attempt}.log"
            started = time.monotonic()
            try:
                # Do not use PIPE here: Qt/WebEngine can emit enough startup
                # diagnostics to fill the Windows pipe buffer before the GUI
                # reaches its timed smoke exit.
                stderr_file = stderr_path.open("wb")
                process = subprocess.Popen(
                    [str(executable)],
                    cwd=str(package_root),
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_file,
                )
                result = process.wait(timeout=max(10.0, seconds + 8.0))
                if result != 0:
                    stderr_file.flush()
                    stderr_file.close()
                    stderr_file = None
                    stderr = stderr_path.read_bytes()[-4096:].decode(errors="replace").strip()
                    return {"ok": False, "attempt": attempt, "error": f"MentorFlow exited with code {result}: {stderr[-500:]}"}
            except subprocess.TimeoutExpired:
                return {"ok": False, "attempt": attempt, "error": "MentorFlow did not complete its health-aware smoke exit before timeout"}
            except OSError as exc:
                return {"ok": False, "attempt": attempt, "error": f"cannot launch MentorFlow: {exc}"}
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                if stderr_file is not None:
                    stderr_file.close()
            run_report = _fresh_install_report(temporary, package_root=package_root)
            run_report["attempt"] = attempt
            run_report["elapsed"] = round(time.monotonic() - started, 2)
            runs.append(run_report)
            if run_report.get("errors"):
                return {"ok": False, "attempt": attempt, "runs": runs, "error": "; ".join(run_report["errors"])}
        package_hashes_after = _package_hashes(package_root)
        if package_hashes_before != package_hashes_after:
            return {"ok": False, "runs": runs, "error": "package files changed during fresh-install smoke"}
        return {
            "ok": True,
            "runs": runs,
            "package_unchanged": True,
            "package_files": len(package_hashes_before),
        }
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _version_key(version: str) -> tuple[int, int, int, int, str]:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise PackageError(f"release tag is not a supported SemVer: {version}")
    pre = match.group("pre") or ""
    return (int(match.group("major")), int(match.group("minor")), int(match.group("patch")), 0 if pre else 1, pre)


def _release_version(tag: str) -> str:
    value = str(tag or "").strip()
    if value.lower().startswith("v"):
        value = value[1:]
    if not VERSION_RE.fullmatch(value):
        raise PackageError(f"GitHub release tag is not a supported SemVer: {tag!r}")
    return value


def _declared_content_length(response: Any, *, label: str) -> int | None:
    """Parse Content-Length strictly; absent is allowed, malformed is not."""
    headers = getattr(response, "headers", None)
    declared = headers.get("Content-Length") if headers is not None else None
    if declared is None:
        return None
    value = str(declared).strip()
    if not re.fullmatch(r"[0-9]+", value):
        raise PackageError(f"{label} returned an invalid Content-Length")
    try:
        declared_size = int(value)
    except (TypeError, ValueError) as exc:
        raise PackageError(f"{label} returned an invalid Content-Length") from exc
    if declared_size < 0:
        raise PackageError(f"{label} returned an invalid Content-Length")
    return declared_size


def _read_bounded_response(response: Any, *, limit: int, label: str) -> bytes:
    """Read an HTTP body with both header and streaming size limits."""
    declared_size = _declared_content_length(response, label=label)
    if declared_size is not None and declared_size > limit:
        raise PackageError(f"{label} exceeds the {limit} byte response limit")
    chunks: list[bytes] = []
    total = 0
    while True:
        read_size = min(1024 * 1024, limit - total + 1)
        chunk = response.read(read_size)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise PackageError(f"{label} exceeds the {limit} byte response limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _github_release(owner: str, repo: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise PackageError("GitHub owner/repo must contain only letters, digits, '.', '_' or '-'")
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "MentorFlow-update-checker/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = _read_bounded_response(response, limit=MAX_RELEASE_JSON_BYTES, label="GitHub Releases API response")
            payload = json.loads(body.decode("utf-8"))
    except PackageError:
        raise
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"GitHub Releases API request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise PackageError("GitHub Releases API returned a non-object response")
    return payload


def _find_release_asset(release: dict[str, Any], *, version: str, pattern: str) -> dict[str, Any]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise PackageError("GitHub release has no assets list")
    expected_name = pattern.format(version=version, tag=release.get("tag_name", ""))
    candidates = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == expected_name]
    if len(candidates) != 1:
        raise PackageError(f"required release asset is missing or ambiguous: {expected_name}")
    asset = candidates[0]
    digest = str(asset.get("digest") or "").strip().lower()
    if not digest.startswith("sha256:") or not SHA256_RE.fullmatch(digest[7:]):
        raise PackageError(f"release asset {expected_name} is missing a valid SHA-256 digest")
    if not str(asset.get("browser_download_url") or "").startswith("https://github.com/"):
        raise PackageError(f"release asset {expected_name} has no official GitHub download URL")
    declared_size = asset.get("size")
    if declared_size is not None:
        try:
            if int(declared_size) < 0 or int(declared_size) > MAX_UPDATE_DOWNLOAD_BYTES:
                raise PackageError(
                    f"release asset {expected_name} exceeds the {MAX_UPDATE_DOWNLOAD_BYTES} byte download limit"
                )
        except (TypeError, ValueError) as exc:
            raise PackageError(f"release asset {expected_name} has an invalid size") from exc
    return asset


def stage_update(version_file: Path, *, owner: str | None, repo: str | None, release_json: Path | None, staging_root: Path | None, download: bool) -> dict[str, Any]:
    current = load_version(version_file)
    config = current.get("update") if isinstance(current.get("update"), dict) else {}
    owner_value = (owner or os.getenv("MENTORFLOW_GITHUB_OWNER") or config.get("owner") or "").strip()
    repo_value = (repo or os.getenv("MENTORFLOW_GITHUB_REPO") or config.get("repo") or "").strip()
    if not owner_value or not repo_value or owner_value == "example-owner":
        raise PackageError("configure a real GitHub owner/repo before checking for updates")
    release = _json_load(release_json) if release_json else _github_release(owner_value, repo_value)
    if release.get("draft") or release.get("prerelease"):
        raise PackageError("latest release is draft/prerelease; refusing automatic staging")
    release_version = _release_version(str(release.get("tag_name") or ""))
    if _version_key(release_version) <= _version_key(str(current["version"])):
        return {"update": False, "current": current["version"], "latest": release_version}
    pattern = str(config.get("asset_name_pattern") or "MentorFlow-{version}-win-x64.zip")
    asset = _find_release_asset(release, version=release_version, pattern=pattern)
    digest = str(asset["digest"])[7:].lower()
    result: dict[str, Any] = {
        "update": True,
        "current": current["version"],
        "latest": release_version,
        "release_page": str(release.get("html_url") or ""),
        "asset": str(asset.get("name")),
        "sha256": digest,
    }
    if not download:
        result["action"] = "open release page and manually install after review"
        return result
    target_root = (staging_root or Path(str(config.get("staging_root") or r"E:\MentorFlowUpdates"))).expanduser().absolute()
    _ensure_e_drive(target_root, label="update staging root")
    # Validate the lexical path before creating anything.  Resolving first
    # would follow a junction/symlink and could silently stage outside the
    # configured E: root.  Reuse the package-root validator after creation as
    # a second check against races or Windows reparse aliases.
    _validate_existing_parent_chain(target_root, label="update staging root")
    if _path_exists(target_root):
        _validate_package_root(target_root)
    else:
        target_root.mkdir(parents=True, exist_ok=True)
        _validate_package_root(target_root)
    asset_name = Path(str(asset.get("name"))).name
    if asset_name != str(asset.get("name")) or asset_name in {"", ".", ".."}:
        raise PackageError("release asset name is unsafe")
    target_dir = target_root / release_version
    _validate_staging_entry(
        target_dir,
        target_root,
        directory=True,
        allow_missing=True,
        label="update staging target",
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    _validate_staging_entry(
        target_dir,
        target_root,
        directory=True,
        allow_missing=False,
        label="update staging target",
    )
    destination = target_dir / asset_name
    destination_exists = _validate_staging_entry(
        destination,
        target_root,
        directory=False,
        allow_missing=True,
    )
    if destination_exists:
        try:
            if destination.stat().st_size > MAX_UPDATE_DOWNLOAD_BYTES:
                raise PackageError(f"existing staged asset exceeds the {MAX_UPDATE_DOWNLOAD_BYTES} byte download limit: {destination}")
        except OSError as exc:
            raise PackageError(f"cannot inspect existing staged asset: {destination}: {exc}") from exc
        if sha256_file(destination) != digest:
            raise PackageError(f"existing staged asset checksum mismatch; refusing overwrite: {destination}")
    else:
        temporary = destination.with_suffix(destination.suffix + ".partial")
        temporary_exists = _validate_staging_entry(
            temporary,
            target_root,
            directory=False,
            allow_missing=True,
        )
        if temporary_exists:
            temporary.unlink()
        request = urllib.request.Request(
            str(asset["browser_download_url"]),
            headers={"Accept": "application/octet-stream", "User-Agent": "MentorFlow-update-checker/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                declared_length = _declared_content_length(
                    response,
                    label="release asset",
                )
                if declared_length is not None and declared_length > MAX_UPDATE_DOWNLOAD_BYTES:
                    raise PackageError(
                        f"release asset exceeds the {MAX_UPDATE_DOWNLOAD_BYTES} byte download limit"
                    )
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPDATE_DOWNLOAD_BYTES:
                        raise PackageError(
                            f"release asset exceeds the {MAX_UPDATE_DOWNLOAD_BYTES} byte download limit"
                        )
                    output.write(chunk)
        except (OSError, urllib.error.URLError) as exc:
            temporary.unlink(missing_ok=True)
            raise PackageError(f"release asset download failed: {exc}") from exc
        except PackageError:
            temporary.unlink(missing_ok=True)
            raise
        if sha256_file(temporary) != digest:
            temporary.unlink(missing_ok=True)
            raise PackageError("downloaded release asset SHA-256 does not match GitHub digest")
        _validate_staging_entry(
            temporary,
            target_root,
            directory=False,
            allow_missing=False,
        )
        temporary.replace(destination)
    metadata = {
        "schema": "mentorflow.update-staging.v1",
        "product": "MentorFlow",
        "current_version": current["version"],
        "release_version": release_version,
        "release_page": str(release.get("html_url") or ""),
        "asset": asset_name,
        "sha256": digest,
        "staged_path": str(destination),
        "staged_at": datetime.now(timezone.utc).isoformat(),
        "install": "Close MentorFlow, review this archive, then manually install. User data is never replaced by this tool.",
    }
    metadata_path = target_dir / "update-staging.json"
    _validate_staging_entry(
        metadata_path,
        target_root,
        directory=False,
        allow_missing=True,
    )
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_staging_entry(
        metadata_path,
        target_root,
        directory=False,
        allow_missing=False,
    )
    result["staged_path"] = str(destination)
    result["metadata"] = str(metadata_path)
    result["action"] = "review staged archive and manually install; no files were replaced"
    return result


_GATE_SCHEMA = "mentorflow.protected-package-gate.v1"
_RESOURCE_SUFFIXES = frozenset({".png", ".webp", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".wav", ".mp4", ".qm", ".sqlite", ".csv", ".txt", ".md"})


def package_file_category(relative: str) -> str:
    """Deny by default: code, loaders, configs and unknown formats stay critical."""
    path = Path(relative)
    if path.suffix.casefold() in _RESOURCE_SUFFIXES and path.parts[0].casefold() not in {"self-check", "config"}:
        return "IMMUTABLE_RESOURCE"
    return "CRITICAL_EXECUTABLE"


@functools.lru_cache(maxsize=1)
def _windows_gate_api():
    if os.name != "nt":
        raise PackageError("protected verification state requires Windows")
    import ctypes as c
    from ctypes import wintypes as w
    class Blob(c.Structure):
        _fields_ = [("size", w.DWORD), ("data", c.POINTER(c.c_ubyte))]
    class Basic(c.Structure):
        _fields_ = [("creation", c.c_longlong), ("access", c.c_longlong), ("write", c.c_longlong), ("change", c.c_longlong), ("attributes", w.DWORD)]
    crypt = c.WinDLL("crypt32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    crypt.CryptProtectData.argtypes = [c.POINTER(Blob), w.LPCWSTR, c.POINTER(Blob), c.c_void_p, c.c_void_p, w.DWORD, c.POINTER(Blob)]
    crypt.CryptProtectData.restype = w.BOOL
    crypt.CryptUnprotectData.argtypes = [c.POINTER(Blob), c.c_void_p, c.POINTER(Blob), c.c_void_p, c.c_void_p, w.DWORD, c.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = w.BOOL
    kernel.LocalFree.argtypes = [c.c_void_p]
    kernel.LocalFree.restype = c.c_void_p
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.GetFileInformationByHandleEx.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    return c, Blob, Basic, crypt, kernel


def _protect_gate_state(data: bytes, *, decrypt: bool = False) -> bytes:
    """Current-user DPAPI authenticated state; no plaintext PASS flag or key."""
    c, Blob, _basic, crypt, kernel = _windows_gate_api()
    buffer = c.create_string_buffer(data)
    source = Blob(len(data), c.cast(buffer, c.POINTER(c.c_ubyte)))
    output = Blob()
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not operation(c.byref(source), None, None, None, None, 1, c.byref(output)):
        raise PackageError("verification state protection failed")
    try:
        return c.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(c.cast(output.data, c.c_void_p))


def _resource_change_time(path: Path) -> int:
    """Include NTFS ChangeTime: restoring LastWriteTime is not a cache hit."""
    c, _blob, Basic, _crypt, kernel = _windows_gate_api()
    handle = kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0x00200000, None)
    if handle == c.c_void_p(-1).value:
        raise PackageError(f"cannot inspect resource change time: {path.name}")
    try:
        info = Basic()
        if not kernel.GetFileInformationByHandleEx(handle, 0, c.byref(info), c.sizeof(info)):
            raise PackageError("cannot inspect resource change time")
        if info.attributes & 0x400:
            raise PackageError("resource became a reparse point")
        return info.change
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def _watch_gate_directory(root: Path):
    """Fail closed on any in-flight mutation without a second directory walk."""
    c, _blob, _basic, _crypt, kernel = _windows_gate_api()
    from ctypes import wintypes as w
    class Overlapped(c.Structure):
        _fields_ = [("internal", c.c_size_t), ("internal_high", c.c_size_t), ("offset", w.DWORD), ("offset_high", w.DWORD), ("event", w.HANDLE)]
    kernel.CreateEventW.argtypes = [c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
    kernel.CreateEventW.restype = w.HANDLE
    kernel.ReadDirectoryChangesW.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, w.BOOL, w.DWORD, c.c_void_p, c.POINTER(Overlapped), c.c_void_p]
    kernel.ReadDirectoryChangesW.restype = w.BOOL
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.CancelIoEx.argtypes = [w.HANDLE, c.POINTER(Overlapped)]
    kernel.CancelIoEx.restype = w.BOOL
    kernel.GetOverlappedResult.argtypes = [w.HANDLE, c.POINTER(Overlapped), c.POINTER(w.DWORD), w.BOOL]
    kernel.GetOverlappedResult.restype = w.BOOL
    directory = kernel.CreateFileW(str(root), 1, 7, None, 3, 0x42000000, None)
    if directory == c.c_void_p(-1).value:
        raise PackageError("cannot watch immutable package directory")
    event = kernel.CreateEventW(None, True, False, None)
    if not event:
        kernel.CloseHandle(directory)
        raise PackageError("cannot protect package enumeration")
    overlapped = Overlapped(event=event)
    buffer = c.create_string_buffer(65536)
    pending = False
    try:
        if not kernel.ReadDirectoryChangesW(directory, buffer, len(buffer), True, 0x15f, None, c.byref(overlapped), None):
            raise PackageError("cannot protect package enumeration")
        pending = True
        def unchanged():
            if kernel.WaitForSingleObject(event, 0) != 258:
                raise PackageError("package changed during security verification")
        yield unchanged
        unchanged()
    finally:
        if pending:
            kernel.CancelIoEx(directory, c.byref(overlapped))
            transferred = w.DWORD()
            kernel.GetOverlappedResult(directory, c.byref(overlapped), c.byref(transferred), True)
        kernel.CloseHandle(event)
        kernel.CloseHandle(directory)


def verify_launch_gate(package_root: Path, *, state_path: Path, expected_manifest_sha256: str, hash_workers: int = 1) -> dict[str, Any]:
    started = time.perf_counter()
    if hash_workers not in {1, 2, 4}:
        raise PackageError("unsupported bounded hash worker count")
    root = _validate_package_root(package_root).resolve()
    with _watch_gate_directory(root) as unchanged:
        report = _verify_launch_gate(root, state_path=state_path, expected_manifest_sha256=expected_manifest_sha256, unchanged=unchanged, hash_workers=hash_workers)
    report["total_seconds"] = time.perf_counter() - started
    return report


def _hash_gate_entries(root: Path, keys: list[str], workers: int):
    def hash_one(key):
        prefix = bytearray()
        digest = sha256_file(root / key, prefix=prefix)
        return key, digest, bytes(prefix)
    if workers == 1:
        yield from map(hash_one, keys)
    else:
        # Opt-in only after a small disk benchmark; one process, bounded reads.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            yield from pool.map(hash_one, keys)


def _verify_launch_gate(package_root: Path, *, state_path: Path, expected_manifest_sha256: str, unchanged, hash_workers: int) -> dict[str, Any]:
    """Single enumeration and full payload hashes with a diagnostic receipt.

    Invoke only from the launcher which pins this helper and manifest. DPAPI
    state is not evidence of trusted creator provenance and never skips hashes.
    """
    started = time.perf_counter()
    package_root = _validate_package_root(package_root).resolve()
    state_path = Path(os.path.abspath(state_path))
    if state_path.is_relative_to(package_root):
        raise PackageError("verification state must remain outside immutable package")
    _validate_existing_parent_chain(state_path.parent, label="verification state")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _validate_staging_entry(state_path.parent, state_path.parent, directory=True, allow_missing=False)
    _validate_staging_entry(state_path, state_path.parent, directory=False, allow_missing=True)
    manifest_path = package_root / MANIFEST_RELATIVE
    _validate_package_entry(manifest_path, package_root, directory=False)
    manifest_hash = sha256_file(manifest_path)
    if not SHA256_RE.fullmatch(expected_manifest_sha256.lower()) or manifest_hash != expected_manifest_sha256.lower():
        raise PackageError("manifest trust anchor mismatch")
    manifest = _json_load(manifest_path)
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("product") != "MentorFlow":
        raise PackageError("unsupported package manifest")
    expected = {}
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise PackageError("invalid manifest file entry")
        relative = Path(entry["path"])
        key = relative.as_posix()
        if relative.is_absolute() or relative.drive or ".." in relative.parts or key in expected or not SHA256_RE.fullmatch(str(entry.get("sha256", ""))) or type(entry.get("size")) is not int:
            raise PackageError("unsafe manifest entry")
        expected[key] = entry
    inventory = {}
    categories = {"CRITICAL_EXECUTABLE": {"files": 0, "bytes": 0}, "IMMUTABLE_RESOURCE": {"files": 0, "bytes": 0}}
    pending = [package_root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                metadata = entry.stat(follow_symlinks=False)
                # On Windows DirEntry.stat deliberately omits file identity and
                # link count (zeros). One real lstat is required, then reused.
                if not metadata.st_ino or not metadata.st_nlink:
                    metadata = path.lstat()
                is_directory = stat.S_ISDIR(metadata.st_mode)
                _validate_package_entry(path, package_root, directory=is_directory, resolve_path=False, cached_stat=metadata)
                if is_directory:
                    pending.append(path)
                    continue
                key = _relative(path, package_root)
                if path == manifest_path:
                    continue
                if key not in expected:
                    raise PackageError(f"unexpected package file: {key}")
                if metadata.st_size != expected[key]["size"]:
                    raise PackageError(f"package size mismatch: {key}")
                category = package_file_category(key)
                categories[category]["files"] += 1
                categories[category]["bytes"] += metadata.st_size
                inventory[key] = [metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_dev, metadata.st_ino,
                    _resource_change_time(path) if category == "IMMUTABLE_RESOURCE" else 0]
    if set(inventory) != set(expected):
        raise PackageError("package file set mismatch: missing payload")
    required = {"MentorFlow.exe", VERSION_FILE_NAME, "SELF-CHECK/MentorFlowSelfCheck.exe", "SELF-CHECK/verify_windows_package.py", "SELF-CHECK/mentorflow-check.bat", "SELF-CHECK/mentorflow-update.bat"}
    if not required.issubset(inventory):
        raise PackageError("required package files missing")
    version = load_version(package_root / VERSION_FILE_NAME)
    if version["version"] != manifest.get("version"):
        raise PackageError("package version mismatch")
    root_stat = package_root.stat()
    binding = {"schema": _GATE_SCHEMA, "manifest_sha256": manifest_hash, "root": str(package_root), "root_identity": [root_stat.st_dev, root_stat.st_ino], "version": version["version"]}
    # DPAPI authenticates the Windows user, not the creator of a verification
    # receipt. The same user can re-seal forged inventory after changing a file.
    # Until an independent trusted authority exists, this record is diagnostic
    # only: never consume it to authorize skipping any payload hash.
    inventory_seconds = time.perf_counter() - started
    hash_started = time.perf_counter()
    critical_hashes = {}
    hashed_prefixes = {}
    to_hash = sorted(inventory)
    for key, digest, prefix in _hash_gate_entries(package_root, to_hash, hash_workers):
        if digest != expected[key]["sha256"]:
            raise PackageError(f"package tampered: {key}")
        hashed_prefixes[key] = prefix
        if package_file_category(key) == "CRITICAL_EXECUTABLE":
            critical_hashes[key] = digest
    hash_seconds = time.perf_counter() - hash_started
    private = _find_user_data_files(package_root, validated_files=[package_root / key for key in inventory], hashed_prefixes=hashed_prefixes)
    # The manifest is separately pinned and is not a payload hash entry.
    private += _find_user_data_files(package_root, validated_files=[manifest_path])
    if private:
        raise PackageError("package contains private data/configuration")
    # A watch follows the directory handle, not a replacement at its old name.
    # Check the root identity again without performing another enumeration.
    final_root = _validate_package_root(package_root).stat()
    if (final_root.st_dev, final_root.st_ino) != (root_stat.st_dev, root_stat.st_ino):
        raise PackageError("package root identity changed during verification")
    unchanged()
    state = {"binding": binding, "inventory": inventory, "critical_hashes": critical_hashes, "full_verified_at": time.time()}
    encrypted = _protect_gate_state(json.dumps(state, sort_keys=True, separators=(",", ":")).encode())
    # Replace atomically; do not leave a plaintext snapshot on disk.
    with tempfile.NamedTemporaryFile(dir=state_path.parent, prefix="gate-", delete=False) as temporary:
        temporary.write(encrypted)
        temp_path = Path(temporary.name)
    try:
        _validate_staging_entry(state_path, state_path.parent, directory=False, allow_missing=True)
        temp_path.replace(state_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return {"verification": "PASS", "mode": "FULL", "hash_workers": hash_workers, "inventory_seconds": inventory_seconds, "hash_seconds": hash_seconds, "total_seconds": time.perf_counter() - started, "categories": categories, "files": len(inventory)}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MentorFlow Windows package integrity and update helper")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-manifest", help="write SELF-CHECK/package-manifest.json")
    build.add_argument("--package-root", type=Path, required=True)
    build.add_argument("--version-file", type=Path)
    verify = subparsers.add_parser("verify", help="verify package files, data databases and optional startup")
    verify.add_argument("--package-root", type=Path, required=True)
    verify.add_argument("--data-root", type=Path)
    verify.add_argument("--no-data-check", action="store_true")
    verify.add_argument("--no-smoke", action="store_true")
    verify.add_argument("--smoke-seconds", type=float, default=5.0)
    gate = subparsers.add_parser("launch-gate", help="full Windows launch verification with a diagnostic receipt")
    gate.add_argument("--package-root", type=Path, required=True)
    gate.add_argument("--state-path", type=Path, required=True)
    gate.add_argument("--expected-manifest-sha256", required=True)
    gate.add_argument("--hash-workers", type=int, choices=(1, 2, 4), default=1)
    update = subparsers.add_parser("check-update", help="check and optionally stage a GitHub release")
    update.add_argument("--version-file", type=Path)
    update.add_argument("--owner")
    update.add_argument("--repo")
    update.add_argument("--release-json", type=Path, help="offline API response fixture for tests/review")
    update.add_argument("--staging-root", type=Path)
    update.add_argument("--download", action="store_true", help="download to E: staging; never installs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "launch-gate":
            report = verify_launch_gate(args.package_root, state_path=args.state_path, expected_manifest_sha256=args.expected_manifest_sha256, hash_workers=args.hash_workers)
            print(json.dumps(report, ensure_ascii=False))
            print("MENTORFLOW_PACKAGE_CHECK=PASS")
            return 0
        if args.command == "build-manifest":
            path = build_manifest(args.package_root, version_file=args.version_file)
            print(f"PACKAGE_MANIFEST={path}")
            return 0
        if args.command == "verify":
            report = verify_package(
                args.package_root,
                data_root=args.data_root,
                check_data=not args.no_data_check,
                smoke=not args.no_smoke,
                smoke_seconds=args.smoke_seconds,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print("MENTORFLOW_PACKAGE_CHECK=PASS")
            return 0
        version_file = args.version_file or ROOT / VERSION_FILE_NAME
        report = stage_update(
            version_file,
            owner=args.owner,
            repo=args.repo,
            release_json=args.release_json,
            staging_root=args.staging_root,
            download=args.download,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("MENTORFLOW_UPDATE_CHECK=PASS")
        return 0
    except PackageError as exc:
        print(f"MENTORFLOW_CHECK_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
