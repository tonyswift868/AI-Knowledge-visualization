"""Conservative SQLite backup and restore helpers.

These helpers are intentionally not wired into application startup.  Recovery
must be an explicit operator action until MentorFlow has selected one database
as the production source of truth.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
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
