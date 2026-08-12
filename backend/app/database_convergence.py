"""Read-only convergence audit and an explicit legacy/ORM write boundary."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from .database_recovery import RecoveryError, inspect_sqlite

LOGGER = logging.getLogger("mentorflow.database_convergence")
T = TypeVar("T")


# This map documents current production ownership. It does not perform migration.
DATA_OWNERSHIP = {
    "workspace": {
        "legacy_table": "workspaces",
        "orm_table": "orm_workspaces",
        "current_primary": "legacy",
        "mirror_fields": ("id", "title", "description"),
    },
    "source": {
        "legacy_table": "sources",
        "orm_table": "orm_sources",
        "current_primary": "legacy",
        "mirror_fields": ("id", "workspace_id", "title", "content_hash", "status"),
    },
    "goal": {
        "legacy_table": "goals",
        "orm_table": "orm_goals",
        "current_primary": "legacy",
        "mirror_fields": ("id", "workspace_id"),
    },
    "plan": {
        "legacy_table": "plans",
        "orm_table": "orm_plans",
        "current_primary": "legacy",
        "mirror_fields": ("id", "workspace_id", "version", "status"),
    },
    "session": {
        "legacy_table": "sessions",
        "orm_table": "orm_sessions",
        "current_primary": "legacy",
        "mirror_fields": ("id", "workspace_id", "lesson_id"),
    },
    "learning_run": {
        "legacy_table": None,
        "orm_table": "orm_learning_runs",
        "current_primary": "orm",
        "mirror_fields": (),
    },
    "knowledge_map": {
        "legacy_table": None,
        "orm_table": "orm_knowledge_map_revisions",
        "current_primary": "orm",
        "mirror_fields": (),
    },
    "narrative_and_expedition": {
        "legacy_table": None,
        "orm_table": None,
        "current_primary": "orm",
        "mirror_fields": (),
    },
}


@dataclass(frozen=True)
class ReconciliationIssue:
    object_type: str
    object_id: str
    issue: str
    field: str = ""
    legacy_value: Any = None
    orm_value: Any = None


@dataclass(frozen=True)
class ConvergenceReport:
    legacy_path: str
    orm_path: str
    legacy_counts: dict[str, int]
    orm_counts: dict[str, int]
    issues: tuple[ReconciliationIssue, ...]

    @property
    def consistent(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "legacy_path": self.legacy_path,
            "orm_path": self.orm_path,
            "legacy_counts": self.legacy_counts,
            "orm_counts": self.orm_counts,
            "consistent": self.consistent,
            "issues": [asdict(issue) for issue in self.issues],
        }


def _rows_by_id(connection: sqlite3.Connection, table: str) -> dict[str, dict[str, Any]]:
    columns = {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    if "id" not in columns:
        return {}
    return {
        str(row["id"]): dict(row)
        for row in connection.execute(f'SELECT * FROM "{table}"').fetchall()
    }


def _payload_value(row: dict[str, Any], field: str) -> Any:
    if field in row:
        return row[field]
    raw = row.get("payload")
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload.get(field)
    return None


def audit_database_convergence(
    legacy_path: str | Path,
    orm_path: str | Path,
) -> ConvergenceReport:
    """Compare IDs and fields that current legacy/ORM writes intend to mirror."""

    legacy_snapshot = inspect_sqlite(legacy_path)
    orm_snapshot = inspect_sqlite(orm_path)
    issues: list[ReconciliationIssue] = []
    legacy_connection = sqlite3.connect(
        f"{Path(legacy_snapshot.path).as_uri()}?mode=ro", uri=True
    )
    orm_connection = sqlite3.connect(
        f"{Path(orm_snapshot.path).as_uri()}?mode=ro", uri=True
    )
    legacy_connection.row_factory = sqlite3.Row
    orm_connection.row_factory = sqlite3.Row
    try:
        for object_type, ownership in DATA_OWNERSHIP.items():
            legacy_table = ownership["legacy_table"]
            orm_table = ownership["orm_table"]
            if not legacy_table or not orm_table:
                continue
            if legacy_table not in legacy_snapshot.table_counts:
                issues.append(ReconciliationIssue(object_type, "*", "legacy_table_missing"))
                continue
            if orm_table not in orm_snapshot.table_counts:
                issues.append(ReconciliationIssue(object_type, "*", "orm_table_missing"))
                continue
            legacy_rows = _rows_by_id(legacy_connection, legacy_table)
            orm_rows = _rows_by_id(orm_connection, orm_table)
            for object_id in sorted(legacy_rows.keys() - orm_rows.keys()):
                issues.append(ReconciliationIssue(object_type, object_id, "missing_in_orm"))
            for object_id in sorted(orm_rows.keys() - legacy_rows.keys()):
                issues.append(ReconciliationIssue(object_type, object_id, "missing_in_legacy"))
            for object_id in sorted(legacy_rows.keys() & orm_rows.keys()):
                for field in ownership["mirror_fields"]:
                    legacy_value = _payload_value(legacy_rows[object_id], field)
                    orm_value = _payload_value(orm_rows[object_id], field)
                    if legacy_value != orm_value:
                        issues.append(
                            ReconciliationIssue(
                                object_type,
                                object_id,
                                "field_mismatch",
                                field,
                                legacy_value,
                                orm_value,
                            )
                        )
    finally:
        legacy_connection.close()
        orm_connection.close()
    return ConvergenceReport(
        legacy_path=legacy_snapshot.path,
        orm_path=orm_snapshot.path,
        legacy_counts=legacy_snapshot.table_counts,
        orm_counts=orm_snapshot.table_counts,
        issues=tuple(issues),
    )


@dataclass(frozen=True)
class CompatibilityWriteOutcome(Generic[T]):
    primary_value: T
    mirror_succeeded: bool
    mirror_error_type: str = ""


def compatibility_write(
    *,
    object_type: str,
    object_id: str,
    primary_write: Callable[[], T],
    mirror_write: Callable[[], Any],
    issue_reporter: Callable[[ReconciliationIssue], None] | None = None,
) -> CompatibilityWriteOutcome[T]:
    """Run the primary write first and make any mirror failure observable."""

    if object_type not in DATA_OWNERSHIP:
        raise RecoveryError(f"Unknown convergence object type: {object_type}")
    primary_value = primary_write()
    try:
        mirror_write()
    except Exception as exc:
        issue = ReconciliationIssue(
            object_type=object_type,
            object_id=object_id,
            issue="mirror_write_failed",
            field=type(exc).__name__,
        )
        LOGGER.warning(
            "database_mirror_write_failed object_type=%s object_id=%s error_type=%s",
            object_type,
            object_id,
            type(exc).__name__,
        )
        if issue_reporter is not None:
            issue_reporter(issue)
        return CompatibilityWriteOutcome(
            primary_value=primary_value,
            mirror_succeeded=False,
            mirror_error_type=type(exc).__name__,
        )
    return CompatibilityWriteOutcome(primary_value=primary_value, mirror_succeeded=True)
