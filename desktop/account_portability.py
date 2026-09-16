"""Versioned, account-scoped ``.mentorflowpack`` export and recovery.

The package is a plain ZIP with a strict manifest and SHA-256 member ledger.
It is intentionally independent from account switching: callers provide the
already-selected account vault and imports target only a new or empty vault.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .local_migration import (
    MAX_COMPRESSION_RATIO,
    MAX_MANIFEST_BYTES,
    MAX_MEMBERS,
    MAX_TOTAL_BYTES,
    MigrationError,
    _assert_within,
    _cleanup_private_file,
    _cleanup_private_tree,
    _ensure_directory,
    _file_identity,
    _hold_directory_chain,
    _is_hardlink_info,
    _is_reparse_info,
    _lexical_path,
    _open_verified_file,
    _same_file_identity,
    _scan_portable_secret,
    _sha256,
    _snapshot_file,
    _validate_existing_entry,
    _validate_parent_chain,
    _verify_sqlite_integrity,
    sanitize_account_portable_sqlite,
    scan_account_portable_sqlite,
)


PACKAGE_SCHEMA = "mentorflow-account-portability-v1"
PACKAGE_FORMAT_VERSION = 1
PACKAGE_EXTENSION = ".mentorflowpack"
MANIFEST_MEMBER = "manifest.json"
CHECKSUMS_MEMBER = "SHA256SUMS"
VERSION_MEMBER = "schema/version.json"
STRUCTURED_MEMBER = "structured/account.json"
PREFERENCES_MEMBER = "preferences/nonsecret.json"
ASSET_MANIFESTS_MEMBER = "manifests/assets.json"
MODEL_MANIFESTS_MEMBER = "manifests/models.json"
RECOVERY_INDEX_MEMBER = "recovery/index.json"
IMPORT_RECEIPT_RELATIVE = Path("data/portability/import-receipt.json")
PACKAGE_EXCLUSIONS = (
    "model_binaries",
    "access_tokens",
    "refresh_tokens",
    "provider_secrets",
    "credential_references",
    "provider_sessions",
    "absolute_local_paths",
    "temporary_caches",
    "other_account_vaults",
)
MAX_JSON_MEMBER_BYTES = 16 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)^[a-z]:[\\/]")
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:access|refresh|id|auth|session)[_-]?token|api[_-]?key|"
    r"provider[_-]?secret|provider[_-]?session|credential[_-]?ref|"
    r"client[_-]?secret|authorization|private[_-]?key|password"
)
_PATH_KEY_RE = re.compile(r"(?i)(?:^|[_-])(?:path|root)$")
_MODEL_BINARY_KEY_RE = re.compile(
    r"(?i)(?:weights?|binary|checkpoint|gguf|safetensors|model[_-]?file)"
)
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class AccountPortabilityError(MigrationError):
    """Stable, human-readable package error with a machine-safe code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PackageMode(str, Enum):
    QUICK = "QUICK_ACCOUNT_EXPORT"
    FULL = "FULL_PORTABLE_EXPORT"


class MaterialAvailability(str, Enum):
    AVAILABLE_LOCAL = "AVAILABLE_LOCAL"
    MISSING_ON_DEVICE = "MISSING_ON_DEVICE"
    RESTORABLE_FROM_PACKAGE = "RESTORABLE_FROM_PACKAGE"
    REDOWNLOAD_AVAILABLE = "REDOWNLOAD_AVAILABLE"
    LINKED_FILE_MISSING = "LINKED_FILE_MISSING"


@dataclass(frozen=True, slots=True)
class _Member:
    path: str
    payload: bytes | None = None
    source: Path | None = None

    @property
    def size(self) -> int:
        if self.payload is not None:
            return len(self.payload)
        if self.source is None:  # pragma: no cover - construction invariant
            return 0
        return self.source.stat().st_size


def _error(code: str, message: str) -> AccountPortabilityError:
    return AccountPortabilityError(code, message)


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error("PORTABILITY_JSON_INVALID", "便携数据包含无法序列化的值。") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_id(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    # Server resource IDs contain colons; they are metadata, never ZIP paths.
    # Local asset/package/artifact identifiers retain the filesystem-safe rule.
    pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$") if field == "asset_id" else _SAFE_ID_RE
    if not pattern.fullmatch(text):
        raise _error("PORTABILITY_ID_INVALID", f"便携数据中的 {field} 不是有效标识。")
    return text


def _unsafe_portable_component(value: str) -> bool:
    stem = value.split(".", 1)[0].upper()
    return bool(
        not value
        or value.endswith((" ", "."))
        or any(ord(char) < 32 or char in '<>:"|?*' for char in value)
        or stem in _WINDOWS_RESERVED_NAMES
    )


def _safe_filename(value: object, *, field: str) -> str:
    text = str(value or "")
    if text in {"", ".", ".."} or Path(text).name != text or _unsafe_portable_component(text):
        raise _error("PORTABILITY_PATH_INVALID", f"{field} 不是可移植文件名。")
    return text


def account_identity_fingerprint(identity: Mapping[str, Any] | str) -> str:
    """Return a non-reversible account binding without exporting raw identity."""

    if isinstance(identity, str):
        supplied = identity.strip().casefold()
        if _SHA256_RE.fullmatch(supplied):
            return supplied
        raise _error("ACCOUNT_FINGERPRINT_INVALID", "账号身份指纹必须是 SHA-256。")
    supplied = str(identity.get("identity_fingerprint") or "").strip().casefold()
    if supplied:
        if not _SHA256_RE.fullmatch(supplied):
            raise _error("ACCOUNT_FINGERPRINT_INVALID", "账号身份指纹必须是 SHA-256。")
        return supplied
    issuer = str(identity.get("issuer") or "").strip().casefold()
    subject = str(identity.get("subject") or identity.get("subject_id") or "").strip()
    if not issuer or not subject:
        # The current desktop still passes its legacy local user record.  Bind
        # that record deterministically without putting username/display name
        # into the package.  Enterprise callers use issuer + subject above.
        issuer = "mentorflow-local-desktop"
        subject = str(identity.get("id") or identity.get("username") or "").strip()
    if not subject:
        raise _error("ACCOUNT_IDENTITY_REQUIRED", "无法确定当前账号身份，未创建便携包。")
    return hashlib.sha256(f"{issuer}\x1f{subject}".encode("utf-8")).hexdigest()


def _is_absolute_path_text(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    parsed = urlsplit(text)
    if parsed.scheme.casefold() in {"http", "https"} and parsed.hostname:
        return False
    return bool(
        _WINDOWS_ABSOLUTE_RE.match(text)
        or text.startswith("\\\\")
        or text.startswith("//")
        or text.startswith("/")
    )


def _sanitize_json(
    value: Any,
    *,
    path: str,
    model_manifest: bool = False,
) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error("PORTABILITY_JSON_INVALID", f"便携数据包含非有限数值：{path}")
        return value
    if isinstance(value, str):
        if _is_absolute_path_text(value):
            raise _error("ABSOLUTE_PATH_REJECTED", f"便携数据包含设备绝对路径：{path}")
        try:
            _scan_portable_secret_text(value, path=path)
        except MigrationError as exc:
            raise _error("SECRET_MATERIAL_REJECTED", "便携数据包含凭据或 Provider 会话。") from exc
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise _error("PORTABILITY_JSON_INVALID", f"便携对象键不是字符串：{path}")
            key = raw_key.strip()
            if not key:
                raise _error("PORTABILITY_JSON_INVALID", f"便携对象包含空键：{path}")
            if _SECRET_KEY_RE.search(key) or _PATH_KEY_RE.search(key):
                continue
            if (
                model_manifest
                and key.casefold() != "binary_exported"
                and _MODEL_BINARY_KEY_RE.search(key)
            ):
                continue
            output[key] = _sanitize_json(
                item,
                path=f"{path}.{key}",
                model_manifest=model_manifest,
            )
        return output
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [
            _sanitize_json(item, path=f"{path}[{index}]", model_manifest=model_manifest)
            for index, item in enumerate(value)
        ]
    raise _error("PORTABILITY_JSON_INVALID", f"便携数据包含不支持的类型：{path}")


def _scan_portable_secret_text(value: str, *, path: str) -> None:
    # Reuse the migration scanner without exposing its private error wording.
    from .local_migration import _secret_scan_text

    _secret_scan_text(value, path=path)


def _https_source_url(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(ord(char) < 32 or char.isspace() for char in text)
    ):
        return None
    try:
        _scan_portable_secret_text(text, path="verified_source_url")
    except MigrationError as exc:
        raise _error("SECRET_MATERIAL_REJECTED", "已验证下载地址包含凭据或 Provider 会话。") from exc
    return text


def _safe_relative(value: object, *, field: str) -> str:
    text = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(text)
    if (
        not text
        or text.startswith("/")
        or _WINDOWS_ABSOLUTE_RE.match(text)
        or ".." in path.parts
        or any(not part or part in {".", ".."} for part in path.parts)
        or any(_unsafe_portable_component(part) for part in path.parts)
    ):
        raise _error("PORTABILITY_PATH_INVALID", f"{field} 必须是 Vault 内相对路径。")
    return path.as_posix()


def _source_file(vault_root: Path, relative: str) -> tuple[Path, os.stat_result]:
    root = _lexical_path(vault_root)
    root_info = _validate_existing_entry(root, role="账号 Vault")
    if root_info is None or not stat.S_ISDIR(root_info.st_mode):
        raise _error("ACCOUNT_VAULT_MISSING", "当前账号 Vault 不存在。")
    source = root / Path(relative)
    _assert_within(source, root, role="账号 Vault")
    _validate_parent_chain(source.parent, role="账号 Vault 资料")
    try:
        observed = os.lstat(source)
    except FileNotFoundError:
        observed = None
    except OSError as exc:
        raise _error("MANAGED_ASSET_MISSING", f"无法检查 Managed 资料：{Path(relative).name}") from exc
    if observed is not None and (
        not stat.S_ISREG(observed.st_mode)
        or _is_reparse_info(observed)
        or _is_hardlink_info(observed)
    ):
        raise _error("UNSAFE_ASSET_REJECTED", f"Managed 资料不是可移植的普通文件：{Path(relative).name}")
    info = _validate_existing_entry(source, role="账号 Vault 资料", allow_missing=True)
    if info is None:
        raise _error("MANAGED_ASSET_MISSING", f"Managed 资料在当前设备缺失：{Path(relative).name}")
    if not stat.S_ISREG(info.st_mode) or _is_reparse_info(info) or _is_hardlink_info(info):
        raise _error("UNSAFE_ASSET_REJECTED", f"Managed 资料不是可移植的普通文件：{Path(relative).name}")
    return source, info


def _normalized_assets(
    assets: Sequence[Mapping[str, Any]],
    *,
    vault_root: Path,
    mode: PackageMode,
    staged_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[_Member], list[_Member]]:
    normalized: list[dict[str, Any]] = []
    recovery: list[dict[str, Any]] = []
    raw_members: list[_Member] = []
    derived_members: list[_Member] = []
    seen_assets: set[str] = set()
    seen_local: set[str] = set()
    for index, raw in enumerate(assets):
        if not isinstance(raw, Mapping):
            raise _error("ASSET_MANIFEST_INVALID", "Asset manifest 必须是对象。")
        asset_id = _safe_id(raw.get("asset_id") or raw.get("id"), field="asset_id")
        revision_id = _safe_id(
            raw.get("asset_revision_id") or raw.get("revision_id"),
            field="asset_revision_id",
        )
        local_asset_id = _safe_id(raw.get("local_asset_id"), field="local_asset_id")
        if asset_id in seen_assets or local_asset_id in seen_local:
            raise _error("ASSET_MANIFEST_DUPLICATE", "Asset manifest 包含重复标识。")
        seen_assets.add(asset_id)
        seen_local.add(local_asset_id)
        storage_kind = str(raw.get("storage_kind") or raw.get("ownership") or "").strip().upper()
        if storage_kind not in {"MANAGED", "LINKED"}:
            raise _error("ASSET_MANIFEST_INVALID", f"资料 {asset_id} 的存储类型无效。")
        filename = _safe_filename(
            raw.get("filename") or raw.get("original_filename") or asset_id,
            field=f"资料 {asset_id}",
        )
        verified_url = _https_source_url(
            raw.get("verified_source_url") or raw.get("verified_redownloadable_source_url")
        )
        portable = _sanitize_json(dict(raw), path=f"assets[{index}]")
        portable.update(
            {
                "asset_id": asset_id,
                "asset_revision_id": revision_id,
                "local_asset_id": local_asset_id,
                "storage_kind": storage_kind,
                "filename": filename,
                "verified_source_url": verified_url,
            }
        )
        for key in tuple(portable):
            if key.casefold() in {
                "vault_relative_path",
                "managed_relative_path",
                "relative_path",
                "local_relative_path",
                "package_member",
            }:
                portable.pop(key, None)
        for key in ("required_derived", "required_derived_artifacts", "derived_artifacts"):
            portable.pop(key, None)
        package_member: str | None = None
        content_sha = str(raw.get("sha256") or raw.get("content_sha256") or "").strip().casefold()
        try:
            size_bytes = int(raw.get("size_bytes") or 0)
        except (TypeError, ValueError) as exc:
            raise _error("ASSET_MANIFEST_INVALID", f"资料 {asset_id} 的大小无效。") from exc
        if storage_kind == "MANAGED" and mode is PackageMode.FULL:
            raw_relative = (
                raw.get("vault_relative_path")
                or raw.get("managed_relative_path")
                or raw.get("local_relative_path")
            )
            source: Path | None = None
            source_info: os.stat_result | None = None
            if raw_relative:
                relative = _safe_relative(raw_relative, field=f"asset {asset_id}")
                try:
                    source, source_info = _source_file(vault_root, relative)
                except AccountPortabilityError as exc:
                    if exc.code != "MANAGED_ASSET_MISSING":
                        raise
            if source is not None and source_info is not None:
                staged = staged_root / "raw" / local_asset_id / filename
                _snapshot_file(source, staged, expected_source_info=source_info)
                _scan_portable_secret(
                    staged, relative=f"raw-assets/{local_asset_id}/{filename}"
                )
                observed_sha = _sha256(staged)
                if content_sha and (
                    not _SHA256_RE.fullmatch(content_sha) or content_sha != observed_sha
                ):
                    raise _error("ASSET_CHECKSUM_MISMATCH", f"Managed 资料校验失败：{filename}")
                content_sha = observed_sha
                size_bytes = staged.stat().st_size
                package_member = f"raw-assets/{local_asset_id}/{filename}"
                raw_members.append(_Member(package_member, source=staged))
                portable["availability"] = MaterialAvailability.RESTORABLE_FROM_PACKAGE.value
            elif verified_url:
                portable["availability"] = MaterialAvailability.REDOWNLOAD_AVAILABLE.value
            else:
                portable["availability"] = MaterialAvailability.MISSING_ON_DEVICE.value
        elif storage_kind == "LINKED":
            portable["availability"] = MaterialAvailability.LINKED_FILE_MISSING.value
        elif verified_url:
            portable["availability"] = MaterialAvailability.REDOWNLOAD_AVAILABLE.value
        else:
            portable["availability"] = MaterialAvailability.MISSING_ON_DEVICE.value
        portable_derived: list[dict[str, Any]] = []
        recovery_derived: list[dict[str, Any]] = []
        declared_derived = (
            raw.get("required_derived_artifacts")
            or raw.get("required_derived")
            or raw.get("derived_artifacts")
            or []
        )
        if not isinstance(declared_derived, Sequence) or isinstance(
            declared_derived, (str, bytes, bytearray)
        ):
            raise _error("ASSET_MANIFEST_INVALID", f"资料 {asset_id} 的派生物清单无效。")
        if storage_kind == "LINKED" and declared_derived:
            raise _error("ASSET_MANIFEST_INVALID", "Linked 资料不能声明可移植派生物。")
        if mode is PackageMode.FULL:
            seen_derived: set[str] = set()
            for derived_index, raw_derived in enumerate(declared_derived):
                if not isinstance(raw_derived, Mapping):
                    raise _error("ASSET_MANIFEST_INVALID", f"资料 {asset_id} 的派生物无效。")
                artifact_id = _safe_id(
                    raw_derived.get("artifact_id")
                    or raw_derived.get("kind")
                    or f"derived-{derived_index + 1}",
                    field="artifact_id",
                )
                if artifact_id in seen_derived:
                    raise _error("ASSET_MANIFEST_DUPLICATE", f"资料 {asset_id} 包含重复派生物。")
                seen_derived.add(artifact_id)
                relative = _safe_relative(
                    raw_derived.get("vault_relative_path")
                    or raw_derived.get("relative_path")
                    or raw_derived.get("local_relative_path"),
                    field=f"asset {asset_id} derived {artifact_id}",
                )
                source, source_info = _source_file(vault_root, relative)
                derived_filename = _safe_filename(
                    raw_derived.get("filename") or Path(relative).name,
                    field=f"资料 {asset_id} 派生物 {artifact_id}",
                )
                staged = staged_root / "derived" / local_asset_id / artifact_id / derived_filename
                _snapshot_file(source, staged, expected_source_info=source_info)
                member_name = (
                    f"derived-assets/{local_asset_id}/{artifact_id}/{derived_filename}"
                )
                _scan_portable_secret(staged, relative=member_name)
                observed_sha = _sha256(staged)
                declared_sha = str(raw_derived.get("sha256") or "").strip().casefold()
                if declared_sha and (
                    not _SHA256_RE.fullmatch(declared_sha) or declared_sha != observed_sha
                ):
                    raise _error("ASSET_CHECKSUM_MISMATCH", f"派生物校验失败：{derived_filename}")
                derived_entry = {
                    "artifact_id": artifact_id,
                    "filename": derived_filename,
                    "mime_type": _sanitize_json(
                        str(raw_derived.get("mime_type") or "application/octet-stream"),
                        path=f"assets[{index}].derived[{derived_index}].mime_type",
                    ),
                    "size_bytes": staged.stat().st_size,
                    "sha256": observed_sha,
                    "availability": MaterialAvailability.RESTORABLE_FROM_PACKAGE.value,
                }
                portable_derived.append(derived_entry)
                recovery_derived.append({**derived_entry, "package_member": member_name})
                derived_members.append(_Member(member_name, source=staged))
        portable["derived_artifacts"] = portable_derived
        portable["sha256"] = content_sha if _SHA256_RE.fullmatch(content_sha) else None
        portable["size_bytes"] = max(0, size_bytes)
        normalized.append(portable)
        recovery.append(
            {
                "asset_id": asset_id,
                "asset_revision_id": revision_id,
                "evidence_revision": portable.get("evidence_revision"),
                "original_local_asset_id": local_asset_id,
                "storage_kind": storage_kind,
                "filename": filename,
                "mime_type": portable.get("mime_type") or "application/octet-stream",
                "size_bytes": portable["size_bytes"],
                "sha256": portable["sha256"],
                "package_member": package_member,
                "verified_source_url": verified_url,
                "derived_artifacts": recovery_derived,
                "state": portable["availability"],
                "actions": (
                    ["restore"]
                    if package_member
                    else ["redownload", "relink"]
                    if verified_url
                    else ["relink"]
                ),
            }
        )
    return normalized, recovery, raw_members, derived_members


def _normalized_models(models: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(models):
        if not isinstance(raw, Mapping):
            raise _error("MODEL_MANIFEST_INVALID", "Model manifest 必须是对象。")
        model_id = _safe_id(raw.get("model_id") or raw.get("id"), field="model_id")
        if model_id in seen:
            raise _error("MODEL_MANIFEST_DUPLICATE", "Model manifest 包含重复标识。")
        seen.add(model_id)
        item = _sanitize_json(dict(raw), path=f"models[{index}]", model_manifest=True)
        item["model_id"] = model_id
        item["device_state"] = "REDOWNLOAD_REQUIRED"
        item["binary_exported"] = False
        normalized.append(item)
    return normalized


def _database_members(vault_root: Path, staged_root: Path) -> tuple[list[_Member], dict[str, int]]:
    members: list[_Member] = []
    counts: dict[str, int] = {}
    for name in ("app.db", "mentorflow_v2.db"):
        source = _lexical_path(vault_root / "data" / name)
        info = _validate_existing_entry(source, role="结构化数据库", allow_missing=True)
        if info is None:
            continue
        if not stat.S_ISREG(info.st_mode) or _is_reparse_info(info) or _is_hardlink_info(info):
            raise _error("STRUCTURED_SNAPSHOT_INVALID", f"结构化数据库不可移植：{name}")
        staged = staged_root / "structured" / name
        _snapshot_file(source, staged, expected_source_info=info)
        sanitize_account_portable_sqlite(staged)
        _verify_sqlite_integrity(staged)
        with sqlite3.connect(staged) as connection:
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            row_total = 0
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                row_total += int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
            database_key = Path(name).stem
            counts[f"{database_key}_tables"] = len(tables)
            counts[f"{database_key}_rows"] = row_total
        members.append(_Member(f"structured/databases/{name}", source=staged))
    return members, counts


def discover_portability_catalogs(vault_root: Path) -> dict[str, Any]:
    """Read only prior/imported portable catalogs from the selected vault.

    Live structured databases are snapshotted separately.  These optional
    JSON catalogs provide the current Asset/Model manifests and make a package
    imported on one device safely re-exportable on another.
    """

    root = _lexical_path(vault_root.expanduser())
    root_info = _validate_existing_entry(root, role="账号 Vault")
    if root_info is None or not stat.S_ISDIR(root_info.st_mode):
        raise _error("ACCOUNT_VAULT_MISSING", "当前账号 Vault 不存在。")
    paths = {
        "structured_snapshot": root / "data/portability/structured-account.json",
        "asset_manifests": root / "data/portability/asset-manifests.json",
        "model_manifests": root / "data/portability/model-manifests.json",
    }
    defaults: dict[str, Any] = {
        "structured_snapshot": {},
        "asset_manifests": [],
        "model_manifests": [],
    }
    for key, path in paths.items():
        _assert_within(path, root, role="账号 Vault")
        info = _validate_existing_entry(path, role="便携目录", allow_missing=True)
        if info is None:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON_MEMBER_BYTES:
            raise _error("PORTABILITY_CATALOG_INVALID", "账号 Vault 的便携目录无效。")
        try:
            with _open_verified_file(path, role="便携目录", expected_info=info) as stream:
                value = json.loads(stream.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error("PORTABILITY_CATALOG_INVALID", "账号 Vault 的便携目录无法读取。") from exc
        if key == "structured_snapshot" and not isinstance(value, Mapping):
            raise _error("PORTABILITY_CATALOG_INVALID", "结构化账号快照必须是对象。")
        if key != "structured_snapshot" and not isinstance(value, list):
            raise _error("PORTABILITY_CATALOG_INVALID", "Asset/Model manifest 必须是数组。")
        defaults[key] = value
    return defaults


def _inventory(snapshot: Mapping[str, Any], assets: Sequence[object], models: Sequence[object]) -> dict[str, Any]:
    inventory: dict[str, Any] = {
        "assets": len(assets),
        "models": len(models),
    }
    for key in (
        "workspaces",
        "goals",
        "plans",
        "progress",
        "knowledge_inventory",
        "knowledge_map",
        "evidence",
        "preferences",
    ):
        value = snapshot.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            inventory[key] = len(value)
        elif isinstance(value, Mapping):
            inventory[key] = len(value)
    plans = snapshot.get("plans")
    if isinstance(plans, Sequence) and not isinstance(plans, (str, bytes, bytearray)):
        inventory["archived_plans"] = sum(
            1 for plan in plans if isinstance(plan, Mapping) and bool(plan.get("archived"))
        )
    return inventory


def _member_sha(member: _Member) -> str:
    if member.payload is not None:
        return _sha256_bytes(member.payload)
    if member.source is None:  # pragma: no cover - construction invariant
        raise _error("PORTABILITY_MEMBER_INVALID", "便携包成员缺少内容。")
    return _sha256(member.source)


def _export_account_package_impl(
    vault_root: Path,
    destination: Path,
    *,
    account_identity: Mapping[str, Any] | str,
    mode: PackageMode | str = PackageMode.QUICK,
    structured_snapshot: Mapping[str, Any] | None = None,
    preferences: Mapping[str, Any] | None = None,
    asset_manifests: Sequence[Mapping[str, Any]] = (),
    model_manifests: Sequence[Mapping[str, Any]] = (),
    personal_local: bool = False,
    allow_personal_local: bool = False,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Create one account-scoped Quick or Full package without overwriting."""

    try:
        selected_mode = mode if isinstance(mode, PackageMode) else PackageMode(str(mode))
    except ValueError as exc:
        raise _error("PACKAGE_MODE_INVALID", "便携包模式不受支持。") from exc
    if personal_local and not allow_personal_local:
        raise _error("PERSONAL_LOCAL_EXPORT_NOT_CONFIRMED", "Personal Local 必须明确确认后才能导出。")
    root = _lexical_path(vault_root.expanduser())
    root_info = _validate_existing_entry(root, role="账号 Vault")
    if root_info is None or not stat.S_ISDIR(root_info.st_mode):
        raise _error("ACCOUNT_VAULT_MISSING", "当前账号 Vault 不存在。")
    output = _lexical_path(destination.expanduser())
    if output.suffix.casefold() != PACKAGE_EXTENSION:
        raise _error("PACKAGE_EXTENSION_INVALID", "便携包文件名必须以 .mentorflowpack 结尾。")
    _validate_parent_chain(output.parent, role="便携包导出目标")
    if _validate_existing_entry(output, role="便携包导出目标", allow_missing=True) is not None:
        raise _error("PACKAGE_DESTINATION_EXISTS", "目标便携包已存在，请选择新文件名。")
    output_parent = _ensure_directory(output.parent, role="便携包导出目标")
    output_parent_info = _validate_existing_entry(output_parent, role="便携包导出目标")
    assert output_parent_info is not None
    temp_root = _ensure_directory(
        Path(os.getenv("MENTORFLOW_TEMP_ROOT", tempfile.gettempdir())),
        role="便携包暂存",
    )
    temp_root_info = _validate_existing_entry(temp_root, role="便携包暂存")
    assert temp_root_info is not None
    staging = _lexical_path(Path(tempfile.mkdtemp(prefix="mentorflow-pack-", dir=temp_root)))
    staging_info = _validate_existing_entry(staging, role="便携包暂存")
    assert staging_info is not None
    temporary = output.with_name(output.name + f".{uuid.uuid4().hex}.part")
    temporary_identity: tuple[int, int] | None = None
    try:
        fingerprint = account_identity_fingerprint(account_identity)
        safe_snapshot = _sanitize_json(
            dict(structured_snapshot or {}), path="structured_snapshot"
        )
        safe_preferences = _sanitize_json(dict(preferences or {}), path="preferences")
        assets, recovery_assets, raw_members, derived_members = _normalized_assets(
            asset_manifests,
            vault_root=root,
            mode=selected_mode,
            staged_root=staging,
        )
        models = _normalized_models(model_manifests)
        database_members, database_counts = _database_members(root, staging)
        timestamp = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        object_inventory = _inventory(safe_snapshot, assets, models)
        object_inventory["database_rows"] = database_counts
        object_inventory["derived_artifacts"] = len(derived_members)
        recovery_index = {
            "schema": PACKAGE_SCHEMA,
            "account_identity_fingerprint": fingerprint,
            "assets": recovery_assets,
            "model_recovery": [
                {
                    "model_id": item["model_id"],
                    "state": "REDOWNLOAD_REQUIRED",
                    "binary_exported": False,
                }
                for item in models
            ],
        }
        members: list[_Member] = [
            _Member(
                VERSION_MEMBER,
                payload=_json_bytes(
                    {"schema": PACKAGE_SCHEMA, "package_format_version": PACKAGE_FORMAT_VERSION}
                ),
            ),
            _Member(STRUCTURED_MEMBER, payload=_json_bytes(safe_snapshot)),
            _Member(PREFERENCES_MEMBER, payload=_json_bytes(safe_preferences)),
            _Member(ASSET_MANIFESTS_MEMBER, payload=_json_bytes(assets)),
            _Member(MODEL_MANIFESTS_MEMBER, payload=_json_bytes(models)),
            _Member(RECOVERY_INDEX_MEMBER, payload=_json_bytes(recovery_index)),
            *database_members,
            *raw_members,
            *derived_members,
        ]
        paths = [member.path for member in members]
        if len(paths) != len(set(paths)) or len(paths) + 2 > MAX_MEMBERS:
            raise _error("PACKAGE_MEMBER_INVALID", "便携包成员重复或超过安全上限。")
        total = sum(member.size for member in members)
        if total > MAX_TOTAL_BYTES:
            raise _error("PACKAGE_TOO_LARGE", "便携包超过安全容量上限。")
        member_entries = [
            {"path": member.path, "size_bytes": member.size, "sha256": _member_sha(member)}
            for member in members
        ]
        package_seed = {
            "schema": PACKAGE_SCHEMA,
            "mode": selected_mode.value,
            "created_at": timestamp,
            "account_identity_fingerprint": fingerprint,
            "members": member_entries,
        }
        package_id = "mfp_" + _sha256_bytes(_json_bytes(package_seed))[:40]
        manifest = {
            "schema": PACKAGE_SCHEMA,
            "package_format_version": PACKAGE_FORMAT_VERSION,
            "package_id": package_id,
            "package_mode": selected_mode.value,
            "created_at": timestamp,
            "account_identity_fingerprint": fingerprint,
            "personal_local": bool(personal_local),
            "object_inventory": object_inventory,
            "members": member_entries,
            "recovery_index": RECOVERY_INDEX_MEMBER,
            "checksums": CHECKSUMS_MEMBER,
            "exclusions": list(PACKAGE_EXCLUSIONS),
        }
        manifest_bytes = _json_bytes(manifest)
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise _error("PACKAGE_MANIFEST_TOO_LARGE", "便携包清单超过安全上限。")
        checksums = {
            entry["path"]: entry["sha256"] for entry in member_entries
        }
        checksums[MANIFEST_MEMBER] = _sha256_bytes(manifest_bytes)
        checksum_bytes = "".join(
            f"{digest}  {name}\n" for name, digest in sorted(checksums.items())
        ).encode("utf-8")
        with _hold_directory_chain(
            output.parent,
            role="便携包导出目标",
            expected_info=output_parent_info,
        ):
            with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(MANIFEST_MEMBER, manifest_bytes)
                archive.writestr(CHECKSUMS_MEMBER, checksum_bytes)
                for member in members:
                    if member.payload is not None:
                        archive.writestr(member.path, member.payload)
                    else:
                        assert member.source is not None
                        archive.write(member.source, member.path)
            temporary_info = _validate_existing_entry(temporary, role="便携包导出暂存")
            assert temporary_info is not None
            temporary_identity = _file_identity(temporary_info)
            if _validate_existing_entry(output, role="便携包导出目标", allow_missing=True) is not None:
                raise _error("PACKAGE_DESTINATION_CHANGED", "便携包导出目标在发布前发生变化。")
            os.replace(temporary, output)
        final_info = _validate_existing_entry(output, role="便携包导出目标")
        if final_info is None or not stat.S_ISREG(final_info.st_mode):
            raise _error("PACKAGE_PUBLISH_FAILED", "便携包未能安全发布。")
        return {
            "archive": output,
            "package_id": package_id,
            "package_mode": selected_mode.value,
            "package_format_version": PACKAGE_FORMAT_VERSION,
            "members": len(members),
            "raw_assets": len(raw_members),
            "derived_artifacts": len(derived_members),
            "object_inventory": object_inventory,
            "manifest_sha256": checksums[MANIFEST_MEMBER],
        }
    except AccountPortabilityError:
        if temporary_identity is None:
            temporary_info = _validate_existing_entry(
                temporary, role="便携包导出暂存", allow_missing=True
            )
            if temporary_info is not None and stat.S_ISREG(temporary_info.st_mode):
                temporary_identity = _file_identity(temporary_info)
        _cleanup_private_file(
            temporary,
            parent_info=output_parent_info,
            expected_identity=temporary_identity,
            role="便携包导出暂存清理",
        )
        raise
    except (MigrationError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        if temporary_identity is None:
            temporary_info = _validate_existing_entry(
                temporary, role="便携包导出暂存", allow_missing=True
            )
            if temporary_info is not None and stat.S_ISREG(temporary_info.st_mode):
                temporary_identity = _file_identity(temporary_info)
        _cleanup_private_file(
            temporary,
            parent_info=output_parent_info,
            expected_identity=temporary_identity,
            role="便携包导出暂存清理",
        )
        raise _error("PACKAGE_EXPORT_FAILED", "便携包导出失败，未发布部分文件。") from exc
    finally:
        _cleanup_private_tree(staging, parent_info=temp_root_info, role="便携包暂存清理")


def export_account_package(
    vault_root: Path,
    destination: Path,
    *,
    account_identity: Mapping[str, Any] | str,
    mode: PackageMode | str = PackageMode.QUICK,
    structured_snapshot: Mapping[str, Any] | None = None,
    preferences: Mapping[str, Any] | None = None,
    asset_manifests: Sequence[Mapping[str, Any]] = (),
    model_manifests: Sequence[Mapping[str, Any]] = (),
    personal_local: bool = False,
    allow_personal_local: bool = False,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Stable public export boundary for all filesystem validation errors."""

    try:
        return _export_account_package_impl(
            vault_root,
            destination,
            account_identity=account_identity,
            mode=mode,
            structured_snapshot=structured_snapshot,
            preferences=preferences,
            asset_manifests=asset_manifests,
            model_manifests=model_manifests,
            personal_local=personal_local,
            allow_personal_local=allow_personal_local,
            created_at=created_at,
        )
    except AccountPortabilityError:
        raise
    except (MigrationError, OSError, sqlite3.Error, ValueError, zipfile.BadZipFile) as exc:
        raise _error("PACKAGE_EXPORT_FAILED", "便携包导出失败，未发布部分文件。") from exc


def _safe_archive_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or _WINDOWS_ABSOLUTE_RE.match(normalized)
        or ".." in path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(_unsafe_portable_component(part) for part in path.parts)
    ):
        raise _error("PACKAGE_ZIP_SLIP_REJECTED", "便携包包含不安全路径。")
    return path.as_posix()


def _is_allowed_payload_member(name: str) -> bool:
    if name in {
        MANIFEST_MEMBER,
        VERSION_MEMBER,
        STRUCTURED_MEMBER,
        PREFERENCES_MEMBER,
        ASSET_MANIFESTS_MEMBER,
        MODEL_MANIFESTS_MEMBER,
        RECOVERY_INDEX_MEMBER,
    }:
        return True
    if name in {
        "structured/databases/app.db",
        "structured/databases/mentorflow_v2.db",
    }:
        return True
    parts = PurePosixPath(name).parts
    return bool(
        (len(parts) == 3 and parts[0] == "raw-assets")
        or (len(parts) == 4 and parts[0] == "derived-assets")
    )


def _load_json_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, name: str) -> Any:
    if info.file_size > MAX_JSON_MEMBER_BYTES:
        raise _error("PACKAGE_JSON_TOO_LARGE", f"便携包 JSON 成员超过安全上限：{name}")
    try:
        return json.loads(
            archive.read(info).decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise _error("PACKAGE_JSON_INVALID", f"便携包 JSON 无法读取：{name}") from exc


def _parse_checksums(raw: bytes) -> dict[str, str]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise _error("PACKAGE_CHECKSUMS_INVALID", "SHA256SUMS 不是 UTF-8。") from exc
    parsed: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise _error("PACKAGE_CHECKSUMS_INVALID", "SHA256SUMS 格式无效。")
        name = _safe_archive_name(match.group(2))
        if name in parsed or name == CHECKSUMS_MEMBER:
            raise _error("PACKAGE_CHECKSUMS_INVALID", "SHA256SUMS 包含重复成员。")
        parsed[name] = match.group(1)
    return parsed


def _read_verified_archive(
    source: Path,
) -> tuple[dict[str, Any], dict[str, bytes], str]:
    if not zipfile.is_zipfile(source):
        raise _error("PACKAGE_INVALID", "所选文件不是有效的 MentorFlow 便携包。")
    with zipfile.ZipFile(source, "r") as archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise _error("PACKAGE_MEMBER_LIMIT", "便携包文件数量超过安全上限。")
        names: dict[str, zipfile.ZipInfo] = {}
        total = 0
        for info in infos:
            name = _safe_archive_name(info.filename.rstrip("/"))
            if name in names:
                raise _error("PACKAGE_MEMBER_DUPLICATE", "便携包包含重复文件。")
            mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode) or (
                file_type not in {0, stat.S_IFREG} and not info.is_dir()
            ):
                raise _error("PACKAGE_LINK_REJECTED", "便携包包含链接或特殊文件。")
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise _error("PACKAGE_ENCRYPTION_UNSUPPORTED", "便携包不支持加密 ZIP 成员。")
            if info.file_size > 0 and info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
                raise _error("PACKAGE_COMPRESSION_RATIO", "便携包压缩比例超过安全上限。")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise _error("PACKAGE_TOO_LARGE", "便携包超过安全容量上限。")
            names[name] = info
        required = {
            MANIFEST_MEMBER,
            CHECKSUMS_MEMBER,
            VERSION_MEMBER,
            STRUCTURED_MEMBER,
            PREFERENCES_MEMBER,
            ASSET_MANIFESTS_MEMBER,
            MODEL_MANIFESTS_MEMBER,
            RECOVERY_INDEX_MEMBER,
        }
        if not required <= set(names):
            raise _error("PACKAGE_REQUIRED_MEMBER_MISSING", "便携包缺少必要清单或恢复索引。")
        if any(
            name != CHECKSUMS_MEMBER and not _is_allowed_payload_member(name)
            for name in names
        ):
            raise _error("PACKAGE_MEMBER_INVALID", "便携包包含未授权成员类型。")
        manifest = _load_json_member(archive, names[MANIFEST_MEMBER], name=MANIFEST_MEMBER)
        if not isinstance(manifest, dict):
            raise _error("PACKAGE_MANIFEST_INVALID", "便携包 manifest.json 必须是对象。")
        if (
            manifest.get("schema") != PACKAGE_SCHEMA
            or manifest.get("package_format_version") != PACKAGE_FORMAT_VERSION
        ):
            raise _error("PACKAGE_VERSION_UNSUPPORTED", "便携包版本不受当前软件支持。")
        try:
            PackageMode(str(manifest.get("package_mode") or ""))
        except ValueError as exc:
            raise _error("PACKAGE_MODE_INVALID", "便携包模式不受当前软件支持。") from exc
        allowed_manifest_keys = {
            "schema",
            "package_format_version",
            "package_id",
            "package_mode",
            "created_at",
            "account_identity_fingerprint",
            "personal_local",
            "object_inventory",
            "members",
            "recovery_index",
            "checksums",
            "exclusions",
        }
        if set(manifest) != allowed_manifest_keys:
            raise _error("PACKAGE_MANIFEST_INVALID", "便携包 manifest 字段集合无效。")
        _safe_id(manifest.get("package_id"), field="package_id")
        if not isinstance(manifest.get("personal_local"), bool):
            raise _error("PACKAGE_MANIFEST_INVALID", "便携包 Personal Local 标记无效。")
        if manifest.get("recovery_index") != RECOVERY_INDEX_MEMBER or manifest.get(
            "checksums"
        ) != CHECKSUMS_MEMBER:
            raise _error("PACKAGE_MANIFEST_INVALID", "便携包清单指针无效。")
        exclusions = manifest.get("exclusions")
        if (
            not isinstance(exclusions, list)
            or _sanitize_json(exclusions, path="manifest.exclusions") != exclusions
            or exclusions != list(PACKAGE_EXCLUSIONS)
        ):
            raise _error("PACKAGE_PROHIBITED_CONTENT", "便携包排除清单包含禁止内容。")
        try:
            created_at = datetime.fromisoformat(str(manifest.get("created_at") or ""))
        except ValueError as exc:
            raise _error("PACKAGE_MANIFEST_INVALID", "便携包创建时间无效。") from exc
        if created_at.tzinfo is None:
            raise _error("PACKAGE_MANIFEST_INVALID", "便携包创建时间缺少时区。")
        inventory = manifest.get("object_inventory")
        if not isinstance(inventory, Mapping) or _sanitize_json(
            inventory, path="manifest.object_inventory"
        ) != inventory:
            raise _error("PACKAGE_PROHIBITED_CONTENT", "便携包对象清单包含禁止内容。")
        fingerprint = str(manifest.get("account_identity_fingerprint") or "").casefold()
        if not _SHA256_RE.fullmatch(fingerprint):
            raise _error("ACCOUNT_FINGERPRINT_INVALID", "便携包缺少有效账号身份指纹。")
        checksums = _parse_checksums(archive.read(names[CHECKSUMS_MEMBER]))
        if set(checksums) != set(names) - {CHECKSUMS_MEMBER}:
            raise _error("PACKAGE_MEMBER_SET_MISMATCH", "便携包成员与 SHA256SUMS 不一致。")
        payloads: dict[str, bytes] = {}
        for name, info in names.items():
            if name == CHECKSUMS_MEMBER:
                continue
            raw = archive.read(info)
            if _sha256_bytes(raw) != checksums[name]:
                raise _error("PACKAGE_CHECKSUM_MISMATCH", f"便携包文件校验失败：{name}")
            payloads[name] = raw
        declared = manifest.get("members")
        if not isinstance(declared, list):
            raise _error("PACKAGE_MANIFEST_INVALID", "便携包 manifest 缺少成员清单。")
        declared_paths: set[str] = set()
        for entry in declared:
            if not isinstance(entry, Mapping):
                raise _error("PACKAGE_MANIFEST_INVALID", "便携包成员清单无效。")
            if set(entry) != {"path", "size_bytes", "sha256"}:
                raise _error("PACKAGE_MANIFEST_INVALID", "便携包成员清单字段无效。")
            path = _safe_archive_name(str(entry.get("path") or ""))
            if path in declared_paths:
                raise _error("PACKAGE_MANIFEST_INVALID", "便携包成员清单重复。")
            declared_paths.add(path)
            if path not in payloads or int(entry.get("size_bytes") or -1) != len(payloads[path]):
                raise _error("PACKAGE_MANIFEST_INVALID", f"便携包成员大小不一致：{path}")
            if str(entry.get("sha256") or "") != checksums.get(path):
                raise _error("PACKAGE_MANIFEST_INVALID", f"便携包成员摘要不一致：{path}")
        if declared_paths != set(payloads) - {MANIFEST_MEMBER}:
            raise _error("PACKAGE_MEMBER_SET_MISMATCH", "便携包 manifest 成员集合不一致。")
        for name in (
            VERSION_MEMBER,
            STRUCTURED_MEMBER,
            PREFERENCES_MEMBER,
            ASSET_MANIFESTS_MEMBER,
            MODEL_MANIFESTS_MEMBER,
            RECOVERY_INDEX_MEMBER,
        ):
            parsed = json.loads(payloads[name].decode("utf-8"))
            cleaned = _sanitize_json(
                parsed,
                path=name,
                model_manifest=name == MODEL_MANIFESTS_MEMBER,
            )
            if cleaned != parsed:
                raise _error(
                    "PACKAGE_PROHIBITED_CONTENT",
                    f"便携包包含禁止的凭据、模型二进制字段或设备路径：{name}",
                )
        recovery_payload = json.loads(payloads[RECOVERY_INDEX_MEMBER].decode("utf-8"))
        recovery_rows = (
            recovery_payload.get("assets") if isinstance(recovery_payload, Mapping) else None
        )
        if not isinstance(recovery_rows, list):
            raise _error("PACKAGE_RECOVERY_INVALID", "便携包恢复索引无效。")
        referenced_binary: set[str] = set()
        for row in recovery_rows:
            if not isinstance(row, Mapping):
                raise _error("PACKAGE_RECOVERY_INVALID", "便携包恢复索引无效。")
            package_member = row.get("package_member")
            if package_member:
                member_name = _safe_archive_name(str(package_member))
                if member_name in referenced_binary:
                    raise _error("PACKAGE_RECOVERY_INVALID", "便携包恢复索引重复引用成员。")
                referenced_binary.add(member_name)
            derived_rows = row.get("derived_artifacts") or []
            if not isinstance(derived_rows, list):
                raise _error("PACKAGE_RECOVERY_INVALID", "便携包派生物恢复索引无效。")
            for derived in derived_rows:
                if not isinstance(derived, Mapping) or not derived.get("package_member"):
                    raise _error("PACKAGE_RECOVERY_INVALID", "便携包派生物恢复索引无效。")
                member_name = _safe_archive_name(str(derived["package_member"]))
                if member_name in referenced_binary:
                    raise _error("PACKAGE_RECOVERY_INVALID", "便携包恢复索引重复引用成员。")
                referenced_binary.add(member_name)
        binary_members = {
            name
            for name in payloads
            if name.startswith(("raw-assets/", "derived-assets/"))
        }
        if referenced_binary != binary_members:
            raise _error("PACKAGE_MEMBER_SET_MISMATCH", "便携包恢复索引与资料成员不一致。")
        manifest_sha = checksums[MANIFEST_MEMBER]
        return manifest, payloads, manifest_sha


def _restored_local_id(package_id: str, original: str) -> str:
    digest = hashlib.sha256(f"{package_id}\x1f{original}".encode("utf-8")).hexdigest()
    return "restored_" + digest[:24]


def _existing_receipt(target: Path, manifest_sha: str) -> dict[str, Any] | None:
    receipt = target / IMPORT_RECEIPT_RELATIVE
    info = _validate_existing_entry(receipt, role="便携包导入回执", allow_missing=True)
    if info is None:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON_MEMBER_BYTES:
        raise _error("IMPORT_TARGET_NOT_EMPTY", "导入目标不是新的空账号 Vault。")
    try:
        with _open_verified_file(receipt, role="便携包导入回执", expected_info=info) as stream:
            payload = json.loads(stream.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("IMPORT_RECEIPT_INVALID", "目标中的便携包导入回执无效。") from exc
    if payload.get("package_manifest_sha256") == manifest_sha:
        return payload
    raise _error("IMPORT_TARGET_NOT_EMPTY", "导入目标已包含其他账号数据或不同便携包。")


def _write_stage_bytes(path: Path, payload: bytes) -> None:
    _ensure_directory(path.parent, role="便携包导入暂存")
    with _open_verified_file(
        path,
        role="便携包导入暂存",
        create_new=True,
        mode="wb",
    ) as stream:
        stream.write(payload)
        stream.flush()


def _import_account_package_impl(
    archive_path: Path,
    target_vault_root: Path,
    *,
    expected_account_identity: Mapping[str, Any] | str,
    allow_personal_local: bool = False,
) -> dict[str, Any]:
    """Verify completely, then publish one package into a new/empty vault."""

    requested_source = _lexical_path(archive_path.expanduser())
    requested_target = _lexical_path(target_vault_root.expanduser())
    _validate_parent_chain(requested_source.parent, role="便携包")
    source_info = _validate_existing_entry(requested_source, role="便携包")
    if source_info is None or not stat.S_ISREG(source_info.st_mode):
        raise _error("PACKAGE_INVALID", "所选文件不是有效的 MentorFlow 便携包。")
    expected_fingerprint = account_identity_fingerprint(expected_account_identity)
    target_parent = _ensure_directory(requested_target.parent, role="便携包导入目标")
    target_parent_info = _validate_existing_entry(target_parent, role="便携包导入目标")
    assert target_parent_info is not None
    target_info = _validate_existing_entry(requested_target, role="便携包导入目标", allow_missing=True)
    if target_info is not None and not stat.S_ISDIR(target_info.st_mode):
        raise _error("IMPORT_TARGET_NOT_EMPTY", "导入目标必须是新目录或空目录。")
    staging = _lexical_path(
        Path(tempfile.mkdtemp(prefix=".mentorflow-account-import-", dir=target_parent))
    )
    staging_info = _validate_existing_entry(staging, role="便携包导入暂存")
    assert staging_info is not None
    source_snapshot = staging / ".mentorflow-source.mentorflowpack"
    try:
        _snapshot_file(requested_source, source_snapshot, expected_source_info=source_info)
        manifest, payloads, manifest_sha = _read_verified_archive(source_snapshot)
        if str(manifest.get("account_identity_fingerprint")) != expected_fingerprint:
            raise _error("PACKAGE_ACCOUNT_MISMATCH", "便携包不属于当前登录账号。")
        if bool(manifest.get("personal_local")) and not allow_personal_local:
            raise _error("PERSONAL_LOCAL_IMPORT_NOT_CONFIRMED", "Personal Local 包需要明确确认后导入。")
        if target_info is not None and any(requested_target.iterdir()):
            receipt = _existing_receipt(requested_target, manifest_sha)
            if receipt is None:
                raise _error(
                    "IMPORT_TARGET_NOT_EMPTY",
                    "导入目标已包含其他账号数据或不同便携包。",
                )
            return {
                "target": requested_target,
                "package_id": receipt["package_id"],
                "package_mode": receipt["package_mode"],
                "package_format_version": PACKAGE_FORMAT_VERSION,
                "duplicate": True,
                "object_inventory": receipt.get("object_inventory") or {},
                "raw_assets_restored": int(receipt.get("raw_assets_restored") or 0),
                "derived_artifacts_restored": int(
                    receipt.get("derived_artifacts_restored") or 0
                ),
            }
        stage_data = staging / "publish" / "data"
        _ensure_directory(stage_data, role="便携包导入暂存")
        portability = stage_data / "portability"
        _ensure_directory(portability, role="便携包导入暂存")
        fixed_outputs = {
            VERSION_MEMBER: portability / "schema-version.json",
            STRUCTURED_MEMBER: portability / "structured-account.json",
            PREFERENCES_MEMBER: portability / "preferences.json",
            MODEL_MANIFESTS_MEMBER: portability / "model-manifests.json",
            RECOVERY_INDEX_MEMBER: portability / "source-recovery-index.json",
        }
        for member, destination in fixed_outputs.items():
            _write_stage_bytes(destination, payloads[member])
        for name, raw in payloads.items():
            if not name.startswith("structured/databases/"):
                continue
            filename = Path(name).name
            if filename not in {"app.db", "mentorflow_v2.db"}:
                raise _error("PACKAGE_MEMBER_INVALID", "便携包包含未知结构化数据库。")
            destination = stage_data / filename
            _write_stage_bytes(destination, raw)
            scan_account_portable_sqlite(destination)
            _verify_sqlite_integrity(destination)
        asset_manifests = json.loads(payloads[ASSET_MANIFESTS_MEMBER].decode("utf-8"))
        recovery_index = json.loads(payloads[RECOVERY_INDEX_MEMBER].decode("utf-8"))
        if not isinstance(asset_manifests, list) or not isinstance(recovery_index, Mapping):
            raise _error("PACKAGE_RECOVERY_INVALID", "便携包 Asset manifest 或恢复索引无效。")
        recovery_rows = recovery_index.get("assets")
        if not isinstance(recovery_rows, list) or len(recovery_rows) != len(asset_manifests):
            raise _error("PACKAGE_RECOVERY_INVALID", "便携包恢复索引与 Asset manifest 不一致。")
        rows_by_asset = {
            str(row.get("asset_id")): row
            for row in recovery_rows
            if isinstance(row, Mapping)
        }
        restored_manifests: list[dict[str, Any]] = []
        local_map: list[dict[str, Any]] = []
        raw_restored = 0
        derived_restored = 0
        package_id = _safe_id(manifest.get("package_id"), field="package_id")
        for item in asset_manifests:
            if not isinstance(item, Mapping):
                raise _error("PACKAGE_RECOVERY_INVALID", "便携包 Asset manifest 无效。")
            asset_id = _safe_id(item.get("asset_id"), field="asset_id")
            row = rows_by_asset.get(asset_id)
            if row is None:
                raise _error("PACKAGE_RECOVERY_INVALID", "便携包恢复索引缺少 Asset。")
            original_local = _safe_id(row.get("original_local_asset_id"), field="local_asset_id")
            restored_local = _restored_local_id(package_id, original_local)
            package_member = str(row.get("package_member") or "")
            restored = dict(item)
            restored["local_asset_id"] = restored_local
            restored.pop("availability", None)
            local_relative: str | None = None
            if package_member:
                member_name = _safe_archive_name(package_member)
                if member_name not in payloads or not member_name.startswith("raw-assets/"):
                    raise _error("PACKAGE_RECOVERY_INVALID", "便携包恢复资料成员无效。")
                filename = Path(member_name).name
                destination = stage_data / "managed-assets" / restored_local / filename
                _write_stage_bytes(destination, payloads[member_name])
                _scan_portable_secret(destination, relative=member_name)
                expected_sha = str(row.get("sha256") or "")
                if not _SHA256_RE.fullmatch(expected_sha) or _sha256(destination) != expected_sha:
                    raise _error("PACKAGE_CHECKSUM_MISMATCH", f"恢复资料校验失败：{filename}")
                state = MaterialAvailability.AVAILABLE_LOCAL.value
                local_relative = destination.relative_to(staging / "publish").as_posix()
                raw_restored += 1
            elif str(row.get("storage_kind")) == "LINKED":
                state = MaterialAvailability.LINKED_FILE_MISSING.value
            elif row.get("verified_source_url"):
                state = MaterialAvailability.REDOWNLOAD_AVAILABLE.value
            else:
                state = MaterialAvailability.MISSING_ON_DEVICE.value
            restored_derived: list[dict[str, Any]] = []
            derived_paths: list[str] = []
            declared_derived = row.get("derived_artifacts") or []
            if not isinstance(declared_derived, list):
                raise _error("PACKAGE_RECOVERY_INVALID", "便携包派生物恢复索引无效。")
            for derived in declared_derived:
                if not isinstance(derived, Mapping):
                    raise _error("PACKAGE_RECOVERY_INVALID", "便携包派生物恢复索引无效。")
                artifact_id = _safe_id(derived.get("artifact_id"), field="artifact_id")
                derived_member = _safe_archive_name(str(derived.get("package_member") or ""))
                expected_prefix = f"derived-assets/{original_local}/{artifact_id}/"
                if derived_member not in payloads or not derived_member.startswith(expected_prefix):
                    raise _error("PACKAGE_RECOVERY_INVALID", "便携包派生物成员无效。")
                derived_filename = Path(derived_member).name
                derived_destination = (
                    stage_data
                    / "managed-assets"
                    / restored_local
                    / "derived"
                    / artifact_id
                    / derived_filename
                )
                _write_stage_bytes(derived_destination, payloads[derived_member])
                _scan_portable_secret(derived_destination, relative=derived_member)
                derived_sha = str(derived.get("sha256") or "")
                if (
                    not _SHA256_RE.fullmatch(derived_sha)
                    or _sha256(derived_destination) != derived_sha
                ):
                    raise _error(
                        "PACKAGE_CHECKSUM_MISMATCH",
                        f"恢复派生物校验失败：{derived_filename}",
                    )
                derived_relative = derived_destination.relative_to(
                    staging / "publish"
                ).as_posix()
                derived_paths.append(derived_relative)
                restored_derived.append(
                    {
                        "artifact_id": artifact_id,
                        "filename": derived_filename,
                        "mime_type": derived.get("mime_type") or "application/octet-stream",
                        "size_bytes": len(payloads[derived_member]),
                        "sha256": derived_sha,
                        "availability": MaterialAvailability.AVAILABLE_LOCAL.value,
                        "local_relative_path": derived_relative,
                    }
                )
                derived_restored += 1
            restored["availability"] = state
            restored["local_relative_path"] = local_relative
            restored["derived_artifacts"] = restored_derived
            restored_manifests.append(restored)
            local_map.append(
                {
                    "asset_id": asset_id,
                    "asset_revision_id": row.get("asset_revision_id"),
                    "evidence_revision": row.get("evidence_revision"),
                    "original_local_asset_id": original_local,
                    "local_asset_id": restored_local,
                    "local_relative_path": local_relative,
                    "derived_local_relative_paths": derived_paths,
                    "state": state,
                    "actions": row.get("actions") or [],
                }
            )
        _write_stage_bytes(
            portability / "asset-manifests.json", _json_bytes(restored_manifests)
        )
        _write_stage_bytes(portability / "local-asset-map.json", _json_bytes(local_map))
        receipt = {
            "schema": PACKAGE_SCHEMA,
            "package_id": package_id,
            "package_manifest_sha256": manifest_sha,
            "package_mode": manifest.get("package_mode"),
            "account_identity_fingerprint": expected_fingerprint,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "object_inventory": manifest.get("object_inventory") or {},
            "raw_assets_restored": raw_restored,
            "derived_artifacts_restored": derived_restored,
        }
        _write_stage_bytes(portability / "import-receipt.json", _json_bytes(receipt))
        # Publish one already-verified top-level directory.  A failure before
        # this rename leaves the target empty; no per-object partial import is
        # visible to the application.
        with _hold_directory_chain(
            target_parent,
            role="便携包导入目标",
            expected_info=target_parent_info,
        ):
            current = _validate_existing_entry(requested_target, role="便携包导入目标", allow_missing=True)
            if target_info is None:
                if current is not None:
                    raise _error("IMPORT_TARGET_CHANGED", "导入目标在发布前发生变化。")
                requested_target.mkdir(exist_ok=False)
                target_info = _validate_existing_entry(requested_target, role="便携包导入目标")
            elif (
                current is None
                or not _same_file_identity(current, target_info)
                or any(requested_target.iterdir())
            ):
                raise _error("IMPORT_TARGET_CHANGED", "导入目标在发布前发生变化。")
            destination_data = requested_target / "data"
            if _validate_existing_entry(destination_data, role="便携包导入目标", allow_missing=True) is not None:
                raise _error("IMPORT_TARGET_CHANGED", "导入目标在发布前发生变化。")
            os.replace(stage_data, destination_data)
        return {
            "target": requested_target,
            "package_id": package_id,
            "package_mode": manifest.get("package_mode"),
            "package_format_version": PACKAGE_FORMAT_VERSION,
            "duplicate": False,
            "object_inventory": manifest.get("object_inventory") or {},
            "raw_assets_restored": raw_restored,
            "derived_artifacts_restored": derived_restored,
            "missing_assets": sum(
                1 for row in local_map if row["state"] != MaterialAvailability.AVAILABLE_LOCAL.value
            ),
        }
    except AccountPortabilityError:
        raise
    except (MigrationError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise _error("PACKAGE_IMPORT_FAILED", "便携包导入失败，未发布部分数据。") from exc
    finally:
        _cleanup_private_tree(staging, parent_info=target_parent_info, role="便携包导入暂存清理")


def import_account_package(
    archive_path: Path,
    target_vault_root: Path,
    *,
    expected_account_identity: Mapping[str, Any] | str,
    allow_personal_local: bool = False,
    test_only: bool = False,
) -> dict[str, Any]:
    """Stable public import boundary for all fail-closed validation errors."""

    try:
        from .account_storage import require_migration_target
        from .account_identity import AccountIdentity
        from .account_vault import VaultLayout
        profile = None
        if isinstance(expected_account_identity, Mapping) and expected_account_identity.get('issuer'):
            profile = AccountIdentity(expected_account_identity['issuer'],
                expected_account_identity.get('subject') or expected_account_identity.get('subject_id'),
                'Account').profile()
        if profile is None and not test_only:
            raise ValueError('Authenticated canonical account identity required for migration')
        if profile is not None and expected_account_identity.get('identity_fingerprint'):
            raise ValueError('Caller-supplied fingerprint cannot override canonical account identity')
        require_migration_target(target_vault_root, profile=profile,
                                 root=VaultLayout.from_env().root, test_only=test_only)
        return _import_account_package_impl(
            archive_path,
            target_vault_root,
            expected_account_identity=expected_account_identity,
            allow_personal_local=allow_personal_local,
        )
    except AccountPortabilityError:
        raise
    except (MigrationError, OSError, sqlite3.Error, ValueError, zipfile.BadZipFile) as exc:
        raise _error("PACKAGE_IMPORT_FAILED", "便携包导入失败，未发布部分数据。") from exc


__all__ = [
    "ASSET_MANIFESTS_MEMBER",
    "AccountPortabilityError",
    "CHECKSUMS_MEMBER",
    "MANIFEST_MEMBER",
    "MaterialAvailability",
    "PACKAGE_EXTENSION",
    "PACKAGE_FORMAT_VERSION",
    "PACKAGE_SCHEMA",
    "PackageMode",
    "RECOVERY_INDEX_MEMBER",
    "account_identity_fingerprint",
    "discover_portability_catalogs",
    "export_account_package",
    "import_account_package",
]
