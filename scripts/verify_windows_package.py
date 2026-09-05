"""Build and verify a MentorFlow Windows package.

This module intentionally uses only the Python standard library.  The copy in
``SELF-CHECK`` is safe to run from a shipped package (or from the source tree)
and never opens a user database for writing.  Update downloads are opt-in and
are staged under E: so an archive can be reviewed and installed manually.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
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


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
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


def _validate_package_entry(candidate: Path, package_root: Path, *, directory: bool) -> None:
    """Fail closed for links, reparse points, special files, and escapes."""
    try:
        relative = _relative(candidate, package_root)
        resolved = candidate.resolve(strict=False)
        metadata = candidate.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PackageError(f"cannot inspect package path {candidate}: {exc}") from exc
    if not _is_within(resolved, package_root):
        raise PackageError(f"package path resolves outside package root: {relative} -> {resolved}")
    if candidate.is_symlink():
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
    parts = tuple(part.casefold() for part in Path(relative).parts)
    if any(part in STALE_PATH_COMPONENTS for part in parts):
        raise PackageError(f"package contains stale Python bytecode path: {relative}")
    if _is_application_managed_path(relative) and any(part in PRIVATE_PATH_COMPONENTS for part in parts):
        raise PackageError(f"package contains a private/development path: {relative}")
    if Path(relative).suffix.casefold() in STALE_BYTECODE_SUFFIXES:
        raise PackageError(f"package contains stale Python bytecode: {relative}")


def _iter_package_entries(package_root: Path) -> Iterable[tuple[Path, bool]]:
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
            _validate_package_entry(candidate, package_root, directory=is_directory)
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


def _find_user_data_files(package_root: Path) -> list[str]:
    found: list[str] = []
    for candidate, _ in _iter_package_entries(package_root):
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
    user_data = _find_user_data_files(package_root)
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
    actual_paths = {
        _relative(candidate, package_root)
        for candidate in _iter_package_files(package_root, manifest_path)
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
