"""Conservative SQLite backup and restore helpers.

These helpers are intentionally not wired into application startup.  Recovery
must be an explicit operator action until MentorFlow has selected one database
as the production source of truth.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RecoveryError(ValueError):
    """Raised before a recovery operation can overwrite or publish bad data."""


@dataclass(frozen=True)
class DatabaseSnapshot:
    path: str
    size_bytes: int
    table_counts: dict[str, int]
    integrity_check: str
    foreign_key_violations: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PAIR_MANIFEST_VERSION = 1
PAIR_FILENAMES = {
    "legacy": "app.db",
    "orm": "mentorflow_v2.db",
}


def _resolved_file(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise RecoveryError(f"SQLite source does not exist: {resolved}")
    return resolved


def inspect_sqlite(path: str | Path) -> DatabaseSnapshot:
    """Read a SQLite database without creating or modifying it."""

    source = _resolved_file(path)
    try:
        connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
        try:
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            if integrity != "ok":
                raise RecoveryError(f"SQLite integrity check failed: {integrity}")
            violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            counts = {
                table: int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                    ).fetchone()[0]
                )
                for table in tables
            }
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise RecoveryError(f"Invalid SQLite database: {source}") from exc
    return DatabaseSnapshot(
        path=str(source),
        size_bytes=source.stat().st_size,
        table_counts=counts,
        integrity_check=integrity,
        foreign_key_violations=violations,
    )


def backup_sqlite(source: str | Path, destination: str | Path) -> DatabaseSnapshot:
    """Create a consistent backup at a new path; existing files are never replaced."""

    source_path = _resolved_file(source)
    destination_path = Path(destination).expanduser().resolve()
    if destination_path == source_path:
        raise RecoveryError("Backup destination must differ from source")
    if destination_path.exists():
        raise RecoveryError(f"Refusing to overwrite backup destination: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(destination_path.name + ".partial")
    if temporary.exists():
        raise RecoveryError(f"Refusing to overwrite partial backup: {temporary}")

    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            f"{source_path.as_uri()}?mode=ro", uri=True
        )
        destination_connection = sqlite3.connect(temporary)
        source_connection.backup(destination_connection)
        destination_connection.commit()
        destination_connection.close()
        destination_connection = None
        snapshot = inspect_sqlite(temporary)
        if snapshot.foreign_key_violations:
            raise RecoveryError(
                f"Backup contains {snapshot.foreign_key_violations} foreign-key violations"
            )
        temporary.replace(destination_path)
        return inspect_sqlite(destination_path)
    except (sqlite3.DatabaseError, OSError) as exc:
        raise RecoveryError(f"SQLite backup failed: {source_path}") from exc
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        if temporary.exists():
            temporary.unlink()


def restore_sqlite(backup: str | Path, destination: str | Path) -> DatabaseSnapshot:
    """Restore a verified backup to a new path.

    An existing destination is rejected.  Replacing a live production database
    remains an operator-controlled procedure with a separate rollback copy.
    """

    backup_snapshot = inspect_sqlite(backup)
    if backup_snapshot.foreign_key_violations:
        raise RecoveryError(
            "Backup cannot be restored because foreign-key checks failed"
        )
    return backup_sqlite(backup, destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _manifest_path(bundle: Path) -> Path:
    return bundle / "manifest.json"


def _read_pair_manifest(bundle: Path) -> dict[str, Any]:
    manifest_path = _manifest_path(bundle)
    if not manifest_path.is_file():
        raise RecoveryError(f"Database bundle manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"Database bundle manifest is invalid: {manifest_path}") from exc
    if manifest.get("manifest_version") != PAIR_MANIFEST_VERSION:
        raise RecoveryError("Unsupported database bundle manifest version")
    if set(manifest.get("databases", {})) != set(PAIR_FILENAMES):
        raise RecoveryError("Database bundle must contain legacy and ORM entries")
    return manifest


def verify_database_pair_bundle(bundle: str | Path) -> dict[str, Any]:
    """Verify both backups, checksums, table counts, and foreign keys."""

    bundle_path = Path(bundle).expanduser().resolve()
    if not bundle_path.is_dir():
        raise RecoveryError(f"Database bundle does not exist: {bundle_path}")
    manifest = _read_pair_manifest(bundle_path)
    for role, filename in PAIR_FILENAMES.items():
        database_path = bundle_path / filename
        entry = manifest["databases"][role]
        if _sha256(_resolved_file(database_path)) != entry.get("sha256"):
            raise RecoveryError(f"{role} database backup checksum mismatch")
        snapshot = inspect_sqlite(database_path)
        if snapshot.table_counts != entry.get("table_counts"):
            raise RecoveryError(f"{role} database table counts do not match manifest")
        if snapshot.foreign_key_violations:
            raise RecoveryError(f"{role} database backup has foreign-key violations")
    return manifest


def _safe_remove_staging(path: Path) -> None:
    if path.name.endswith(".partial") and path.parent != path:
        shutil.rmtree(path, ignore_errors=True)


def backup_database_pair(
    legacy_source: str | Path,
    orm_source: str | Path,
    bundle: str | Path,
) -> dict[str, Any]:
    """Back up both runtime databases as one atomically published unit."""

    sources = {
        "legacy": _resolved_file(legacy_source),
        "orm": _resolved_file(orm_source),
    }
    source_hashes = {role: _sha256(path) for role, path in sources.items()}
    bundle_path = Path(bundle).expanduser().resolve()
    staging = bundle_path.with_name(bundle_path.name + ".partial")
    if bundle_path.exists():
        manifest = verify_database_pair_bundle(bundle_path)
        recorded = {
            role: entry.get("source_sha256")
            for role, entry in manifest["databases"].items()
        }
        if recorded != source_hashes:
            raise RecoveryError(
                "Existing bundle belongs to a different source state; refusing overwrite"
            )
        return {**manifest, "idempotent": True}
    if staging.exists():
        raise RecoveryError(f"Interrupted staging directory requires review: {staging}")

    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        entries: dict[str, dict[str, Any]] = {}
        for role, filename in PAIR_FILENAMES.items():
            destination = staging / filename
            snapshot = backup_sqlite(sources[role], destination)
            entries[role] = {
                "filename": filename,
                "source_filename": sources[role].name,
                "source_sha256": source_hashes[role],
                "sha256": _sha256(destination),
                "table_counts": snapshot.table_counts,
                "foreign_key_violations": snapshot.foreign_key_violations,
            }
        manifest = {
            "manifest_version": PAIR_MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "databases": entries,
        }
        _manifest_path(staging).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        verify_database_pair_bundle(staging)
        staging.replace(bundle_path)
        return {**manifest, "idempotent": False}
    except Exception:
        _safe_remove_staging(staging)
        raise


def restore_database_pair(
    bundle: str | Path,
    destination_directory: str | Path,
) -> dict[str, Any]:
    """Restore a verified pair into a new directory without replacing live files."""

    bundle_path = Path(bundle).expanduser().resolve()
    manifest = verify_database_pair_bundle(bundle_path)
    destination = Path(destination_directory).expanduser().resolve()
    staging = destination.with_name(destination.name + ".partial")
    if destination.exists():
        if not destination.is_dir():
            raise RecoveryError(f"Restore destination is not a directory: {destination}")
        for role, filename in PAIR_FILENAMES.items():
            restored = inspect_sqlite(destination / filename)
            expected = manifest["databases"][role]["table_counts"]
            if restored.table_counts != expected or restored.foreign_key_violations:
                raise RecoveryError(
                    "Existing restore destination differs from bundle; refusing overwrite"
                )
        return {**manifest, "idempotent": True, "destination": str(destination)}
    if staging.exists():
        raise RecoveryError(f"Interrupted restore staging requires review: {staging}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        for filename in PAIR_FILENAMES.values():
            restore_sqlite(bundle_path / filename, staging / filename)
        for role, filename in PAIR_FILENAMES.items():
            restored = inspect_sqlite(staging / filename)
            if restored.table_counts != manifest["databases"][role]["table_counts"]:
                raise RecoveryError(f"{role} restored table counts do not match bundle")
        staging.replace(destination)
        return {**manifest, "idempotent": False, "destination": str(destination)}
    except Exception:
        _safe_remove_staging(staging)
        raise
