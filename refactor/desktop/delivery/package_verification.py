"""Runtime contracts extracted from scripts/verify_windows_package.py."""
from __future__ import annotations

import hashlib

import json

import os

import re

import stat

import threading

from pathlib import Path

from typing import Any, Iterable

VERSION_FILE_NAME = "mentorflow-version.json"

MANIFEST_RELATIVE = Path("SELF-CHECK") / "package-manifest.json"

MANIFEST_SCHEMA = "mentorflow.windows-package-manifest.v1"

VERSION_SCHEMA = "mentorflow.version.v1"

VERSION_RE = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")

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
    ``_internal`` root (also under the bundled UserRuntime) is dependency-owned
    unless its immediate child is a known application data/content/config/state
    or fixture root.
    Content and config scans below those roots therefore remain strict while
    arbitrary third-party names such as ``torch/_vendor/quack/cache`` do not
    trigger a private-path finding by themselves.
    """
    parts = tuple(part.casefold() for part in Path(relative).parts)
    return _application_managed_parts(parts)

def _application_managed_parts(parts: tuple[str, ...]) -> bool:
    """Pure rule over already normalized/casefolded path components."""
    if parts[:2] == ("userruntime", "_internal"):
        parts = parts[1:]
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
        # Windows rooted-relative names ('/outside') have a root but no drive;
        # is_absolute() alone misses them and joining discards package_root.
        if relative_path.drive or relative_path.root or ".." in relative_path.parts:
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
