"""Account-scoped Windows local vaults and checksum-addressed shared models."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Protocol

from .account_identity import AccountProfile, ProfileMode


FILE_ATTRIBUTE_REPARSE_POINT = 0x400
VAULT_SUBDIRECTORIES = (
    "managed-assets/objects",
    "managed-assets/staging",
    "metadata",
    "cache",
    "derived",
    "trash",
    "snapshots",
    "sync-journal",
    "data",
)
SHARED_MODEL_SUBDIRECTORIES = (
    "binaries",
    "download-cache",
    "manifests",
)


class ImportMode(str, Enum):
    MANAGED = "MANAGED"
    LINKED = "LINKED"


class AssetAvailability(str, Enum):
    AVAILABLE_LOCAL = "AVAILABLE_LOCAL"
    MISSING_ON_DEVICE = "MISSING_ON_DEVICE"
    LINKED_FILE_MISSING = "LINKED_FILE_MISSING"
    REDOWNLOAD_AVAILABLE = "REDOWNLOAD_AVAILABLE"
    RESTORABLE_FROM_PACKAGE = "RESTORABLE_FROM_PACKAGE"


def _lexical_path(value: Path | str) -> Path:
    return Path(os.path.abspath(os.path.normpath(str(value))))


def _normalized_path(value: Path | str) -> str:
    return os.path.normcase(str(_lexical_path(value)))


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        attributes & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _reject_reparse_chain(path: Path, *, allow_missing_leaf: bool = True) -> None:
    candidate = _lexical_path(path)
    anchor = Path(candidate.anchor)
    current = anchor
    for part in candidate.parts[1:]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            if allow_missing_leaf:
                continue
            raise FileNotFoundError(current)
        if _is_reparse_point(current):
            raise ValueError(f"Vault path contains a symlink or reparse point: {current}")


def _safe_child(root: Path, *parts: str) -> Path:
    base = _lexical_path(root)
    candidate = _lexical_path(base.joinpath(*parts))
    try:
        common = os.path.commonpath([_normalized_path(base), _normalized_path(candidate)])
    except ValueError as exc:
        raise ValueError("Vault path crosses a volume boundary") from exc
    if common != _normalized_path(base):
        raise ValueError("Vault path traversal is forbidden")
    _reject_reparse_chain(candidate)
    return candidate


def _ensure_directory(path: Path) -> Path:
    candidate = _lexical_path(path)
    _reject_reparse_chain(candidate.parent)
    candidate.mkdir(parents=True, exist_ok=True)
    _reject_reparse_chain(candidate, allow_missing_leaf=False)
    if not candidate.is_dir():
        raise ValueError(f"Vault path is not a directory: {candidate}")
    return candidate


def _regular_source(path: Path) -> Path:
    candidate = _lexical_path(path)
    _reject_reparse_chain(candidate, allow_missing_leaf=False)
    info = candidate.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Import source must be a regular file")
    return candidate


def _validate_sha256(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("checksum must be lowercase SHA-256")
    return normalized


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class VaultLayout:
    root: Path = Path("E:/MentorFlowData")

    def __post_init__(self) -> None:
        if not Path(self.root).is_absolute():
            raise ValueError("Vault root must be absolute")
        normalized = _lexical_path(self.root)
        object.__setattr__(self, "root", normalized)

    @property
    def personal_local_root(self) -> Path:
        return _safe_child(self.root, "personal-local")

    @property
    def accounts_root(self) -> Path:
        return _safe_child(self.root, "accounts")

    @property
    def shared_models_root(self) -> Path:
        return _safe_child(self.root, "shared-models")

    def root_for_profile(self, profile: AccountProfile) -> Path:
        if profile.mode is ProfileMode.PERSONAL_LOCAL:
            return self.personal_local_root
        assert profile.issuer_hash is not None and profile.subject_hash is not None
        return _safe_child(
            self.accounts_root,
            profile.issuer_hash,
            profile.subject_hash,
        )


@dataclass(frozen=True, slots=True)
class AssetRecord:
    local_asset_id: str
    import_mode: ImportMode
    display_name: str
    mime_type: str
    size_bytes: int
    sha256: str | None
    object_relative_path: str | None
    linked_path: str | None
    portable: bool
    backed_up: bool
    verified_source_url: str | None = None
    restorable_from_package: bool = False

    @property
    def portability_labels(self) -> tuple[str, ...]:
        if self.import_mode is ImportMode.LINKED:
            return ("NON_PORTABLE", "NOT_BACKED_UP")
        return ()


class ClosableHandle(Protocol):
    def close(self) -> None: ...


class AccountVault:
    def __init__(self, root: Path, profile: AccountProfile) -> None:
        self.root = _lexical_path(root)
        self.profile = profile
        self._closed = False
        self._handles: list[ClosableHandle] = []
        self._database = sqlite3.connect(
            str(_safe_child(self.root, "metadata", "vault.sqlite")),
            check_same_thread=False,
        )
        self._database.row_factory = sqlite3.Row
        self._database.execute("PRAGMA foreign_keys = ON")
        self._database.execute("PRAGMA journal_mode = DELETE")
        self._database.executescript(
            """
            CREATE TABLE IF NOT EXISTS local_assets (
              local_asset_id TEXT PRIMARY KEY,
              import_mode TEXT NOT NULL,
              display_name TEXT NOT NULL,
              mime_type TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              sha256 TEXT,
              object_relative_path TEXT,
              linked_path TEXT,
              portable INTEGER NOT NULL,
              backed_up INTEGER NOT NULL,
              verified_source_url TEXT,
              restorable_from_package INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              CHECK (import_mode IN ('MANAGED', 'LINKED')),
              CHECK (
                (import_mode = 'MANAGED' AND object_relative_path IS NOT NULL
                  AND linked_path IS NULL AND sha256 IS NOT NULL)
                OR
                (import_mode = 'LINKED' AND linked_path IS NOT NULL
                  AND object_relative_path IS NULL)
              )
            );
            """
        )
        self._database.commit()

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Account Vault is closed")

    def register_handle(self, handle: ClosableHandle) -> None:
        self._require_open()
        self._handles.append(handle)

    def _insert_asset(self, record: AssetRecord) -> None:
        self._require_open()
        self._database.execute(
            """
            INSERT INTO local_assets(
              local_asset_id, import_mode, display_name, mime_type,
              size_bytes, sha256, object_relative_path, linked_path,
              portable, backed_up, verified_source_url,
              restorable_from_package
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.local_asset_id,
                record.import_mode.value,
                record.display_name,
                record.mime_type,
                record.size_bytes,
                record.sha256,
                record.object_relative_path,
                record.linked_path,
                int(record.portable),
                int(record.backed_up),
                record.verified_source_url,
                int(record.restorable_from_package),
            ),
        )
        self._database.commit()

    def import_managed(self, source: Path | str) -> AssetRecord:
        self._require_open()
        source_path = _regular_source(Path(source))
        staging = _safe_child(
            self.root,
            "managed-assets",
            "staging",
            f"{uuid.uuid4().hex}.part",
        )
        digest = hashlib.sha256()
        size = 0
        try:
            with source_path.open("rb") as reader, staging.open("xb") as writer:
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            checksum = digest.hexdigest()
            object_directory = _ensure_directory(
                _safe_child(
                    self.root,
                    "managed-assets",
                    "objects",
                    checksum[:2],
                )
            )
            target = _safe_child(object_directory, checksum)
            if target.exists():
                existing = _sha256_file(target)
                if existing != checksum:
                    raise RuntimeError("Managed object checksum collision")
                staging.unlink(missing_ok=True)
            else:
                os.replace(staging, target)
            relative = target.relative_to(self.root).as_posix()
            record = AssetRecord(
                local_asset_id="la_" + uuid.uuid4().hex,
                import_mode=ImportMode.MANAGED,
                display_name=source_path.name,
                mime_type=mimetypes.guess_type(source_path.name)[0]
                or "application/octet-stream",
                size_bytes=size,
                sha256=checksum,
                object_relative_path=relative,
                linked_path=None,
                portable=True,
                backed_up=False,
            )
            self._insert_asset(record)
            return record
        finally:
            staging.unlink(missing_ok=True)

    def import_linked(self, source: Path | str) -> AssetRecord:
        self._require_open()
        source_path = _regular_source(Path(source))
        info = source_path.stat()
        record = AssetRecord(
            local_asset_id="la_" + uuid.uuid4().hex,
            import_mode=ImportMode.LINKED,
            display_name=source_path.name,
            mime_type=mimetypes.guess_type(source_path.name)[0]
            or "application/octet-stream",
            size_bytes=int(info.st_size),
            sha256=None,
            object_relative_path=None,
            linked_path=str(source_path),
            portable=False,
            backed_up=False,
        )
        self._insert_asset(record)
        return record

    @staticmethod
    def _asset(row: sqlite3.Row) -> AssetRecord:
        return AssetRecord(
            local_asset_id=str(row["local_asset_id"]),
            import_mode=ImportMode(str(row["import_mode"])),
            display_name=str(row["display_name"]),
            mime_type=str(row["mime_type"]),
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]) if row["sha256"] is not None else None,
            object_relative_path=(
                str(row["object_relative_path"])
                if row["object_relative_path"] is not None
                else None
            ),
            linked_path=(
                str(row["linked_path"])
                if row["linked_path"] is not None
                else None
            ),
            portable=bool(row["portable"]),
            backed_up=bool(row["backed_up"]),
            verified_source_url=(
                str(row["verified_source_url"])
                if row["verified_source_url"] is not None
                else None
            ),
            restorable_from_package=bool(row["restorable_from_package"]),
        )

    def list_assets(self) -> list[AssetRecord]:
        self._require_open()
        rows = self._database.execute(
            "SELECT * FROM local_assets ORDER BY created_at, local_asset_id"
        ).fetchall()
        return [self._asset(row) for row in rows]

    def availability(self, asset: AssetRecord) -> AssetAvailability:
        self._require_open()
        if asset.import_mode is ImportMode.LINKED:
            linked = Path(asset.linked_path or "")
            return (
                AssetAvailability.AVAILABLE_LOCAL
                if linked.is_file() and not _is_reparse_point(linked)
                else AssetAvailability.LINKED_FILE_MISSING
            )
        if asset.object_relative_path:
            target = _safe_child(self.root, *Path(asset.object_relative_path).parts)
            if target.is_file() and not _is_reparse_point(target):
                return AssetAvailability.AVAILABLE_LOCAL
        if asset.verified_source_url:
            return AssetAvailability.REDOWNLOAD_AVAILABLE
        if asset.restorable_from_package:
            return AssetAvailability.RESTORABLE_FROM_PACKAGE
        return AssetAvailability.MISSING_ON_DEVICE

    def managed_object_path(self, asset: AssetRecord) -> Path:
        self._require_open()
        if asset.import_mode is not ImportMode.MANAGED or not asset.object_relative_path:
            raise ValueError("asset is not managed by this Vault")
        return _safe_child(self.root, *Path(asset.object_relative_path).parts)

    def close(self) -> None:
        if self._closed:
            return
        for handle in reversed(self._handles):
            try:
                handle.close()
            except Exception:
                pass
        self._handles.clear()
        self._database.close()
        self._closed = True


class AccountVaultManager:
    def __init__(self, layout: VaultLayout) -> None:
        self.layout = layout
        self.current: AccountVault | None = None

    def prepare_profile(self, profile: AccountProfile) -> Path:
        root = _ensure_directory(self.layout.root_for_profile(profile))
        for relative in VAULT_SUBDIRECTORIES:
            _ensure_directory(_safe_child(root, *Path(relative).parts))
        for database_name in ("app.db", "mentorflow_v2.db"):
            database = _safe_child(root, "data", database_name)
            connection = sqlite3.connect(str(database))
            connection.close()
        return root

    def mount(self, profile: AccountProfile) -> AccountVault:
        target = self.prepare_profile(profile)
        if self.current is not None:
            if (
                not self.current.closed
                and _normalized_path(self.current.root) == _normalized_path(target)
            ):
                return self.current
            self.unmount()
        self.current = AccountVault(target, profile)
        return self.current

    def unmount(self) -> None:
        if self.current is not None:
            self.current.close()
            self.current = None

    def current_assets(self) -> list[AssetRecord]:
        return self.current.list_assets() if self.current is not None else []

    def prepare_shared_models(self) -> Path:
        root = _ensure_directory(self.layout.shared_models_root)
        for relative in SHARED_MODEL_SUBDIRECTORIES:
            _ensure_directory(_safe_child(root, relative))
        return root

    def install_shared_model(
        self,
        source: Path | str,
        *,
        expected_sha256: str,
    ) -> Path:
        source_path = _regular_source(Path(source))
        checksum = _validate_sha256(expected_sha256)
        digest = _sha256_file(source_path)
        if digest != checksum:
            raise ValueError("shared model checksum mismatch")
        root = self.prepare_shared_models()
        directory = _ensure_directory(_safe_child(root, "binaries", checksum[:2]))
        target = _safe_child(directory, checksum)
        if not target.exists():
            temporary = _safe_child(directory, f"{checksum}.{uuid.uuid4().hex}.part")
            try:
                with source_path.open("rb") as reader, temporary.open("xb") as writer:
                    while True:
                        chunk = reader.read(1024 * 1024)
                        if not chunk:
                            break
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        if _sha256_file(target) != checksum:
            raise RuntimeError("shared model binary failed checksum verification")
        return target


__all__ = [
    "AccountVault",
    "AccountVaultManager",
    "AssetAvailability",
    "AssetRecord",
    "ImportMode",
    "SHARED_MODEL_SUBDIRECTORIES",
    "VAULT_SUBDIRECTORIES",
    "VaultLayout",
]
