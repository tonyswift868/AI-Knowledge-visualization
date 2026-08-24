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

    @property
    def domain_counts(self) -> dict[str, int]:
        """Return the number of reported problems for each convergence domain."""

        counts: dict[str, int] = {object_type: 0 for object_type in DATA_OWNERSHIP}
        for issue in self.issues:
            counts[issue.object_type] = counts.get(issue.object_type, 0) + 1
        return counts

    @property
    def issue_counts(self) -> dict[str, int]:
        """Return the number of occurrences of each issue kind."""

        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.issue] = counts.get(issue.issue, 0) + 1
        return counts

    @property
    def missing_count(self) -> int:
        return sum(
            count
            for issue, count in self.issue_counts.items()
            if "missing" in issue
        )

    @property
    def mismatch_count(self) -> int:
        return sum(
            count
            for issue, count in self.issue_counts.items()
            if "mismatch" in issue
        )

    @staticmethod
    def _ownership_dict() -> dict[str, dict[str, Any]]:
        """Expose ownership metadata without any row or field values."""

        return {
            object_type: {
                "legacy_table": ownership.get("legacy_table"),
                "orm_table": ownership.get("orm_table"),
                "current_primary": ownership.get("current_primary"),
                "mirror_fields": list(ownership.get("mirror_fields", ())),
            }
            for object_type, ownership in DATA_OWNERSHIP.items()
        }

    @staticmethod
    def _public_path(role: str, path: str | Path) -> dict[str, str]:
        """Expose only a stable database role and basename in public output."""

        return {"role": role, "basename": Path(path).name}

    def _base_dict(self, *, details: bool = False) -> dict[str, Any]:
        issue_counts = self.issue_counts
        missing_count = self.missing_count
        mismatch_count = self.mismatch_count
        ownership = self._ownership_dict()
        return {
            "report_version": 1,
            "legacy_path": (
                self.legacy_path
                if details
                else self._public_path("legacy", self.legacy_path)
            ),
            "orm_path": (
                self.orm_path
                if details
                else self._public_path("orm", self.orm_path)
            ),
            "legacy_counts": self.legacy_counts,
            "orm_counts": self.orm_counts,
            # ``table_counts`` and ``source_of_truth`` are stable operator
            # aliases; retain the original count keys for compatibility.
            "table_counts": {"legacy": self.legacy_counts, "orm": self.orm_counts},
            "ownership": ownership,
            "source_of_truth": {
                object_type: metadata.get("current_primary")
                for object_type, metadata in ownership.items()
            },
            "consistent": self.consistent,
            "domain_counts": self.domain_counts,
            "issue_counts": issue_counts,
            # ``problem_counts`` is the operator-facing name for the issue
            # histogram and keeps machine consumers from counting issue rows.
            "problem_counts": issue_counts,
            "missing_count": missing_count,
            "mismatch_count": mismatch_count,
            "total_issue_count": len(self.issues),
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Return the default privacy-safe machine-readable report.

        Issue IDs and field names remain available for diagnosis, while raw
        legacy/ORM field values are intentionally omitted.
        """

        payload = self._base_dict()
        payload["issues"] = [
            {
                "object_type": issue.object_type,
                "object_id": issue.object_id,
                "issue": issue.issue,
                "field": issue.field,
            }
            for issue in self.issues
        ]
        return payload

    def to_detailed_dict(self) -> dict[str, Any]:
        """Return the internal diagnostic report, including compared values."""

        payload = self._base_dict(details=True)
        payload["issues"] = [asdict(issue) for issue in self.issues]
        return payload

    # Keep an explicit internal alias for callers that used the old detailed
    # representation before public reports became privacy-safe by default.
    to_internal_dict = to_detailed_dict

    def to_dict(
        self,
        *,
        details: bool = False,
        include_values: bool | None = None,
        public: bool | None = None,
    ) -> dict[str, Any]:
        """Serialize a report; values require an explicit details opt-in."""

        if include_values is not None:
            details = include_values
        if public is not None:
            details = not public
        return self.to_detailed_dict() if details else self.to_public_dict()

    def to_json(
        self,
        *,
        details: bool = False,
        include_values: bool | None = None,
        public: bool | None = None,
    ) -> str:
        """Serialize the public (or explicitly detailed) report as JSON."""

        return json.dumps(
            self.to_dict(
                details=details,
                include_values=include_values,
                public=public,
            ),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )


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
        if row["id"] is not None and str(row["id"]).strip()
    }


def _table_has_id(connection: sqlite3.Connection, table: str) -> bool:
    return any(
        str(row[1]) == "id"
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    )


def _rows_with_missing_ids(
    connection: sqlite3.Connection, table: str
) -> int:
    """Count rows whose mirror identity is null or blank without exposing data."""

    return int(
        connection.execute(
            f'SELECT COUNT(*) FROM "{table}" '
            "WHERE id IS NULL OR TRIM(CAST(id AS TEXT)) = ''"
        ).fetchone()[0]
    )


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
            current_primary = ownership.get("current_primary")
            if current_primary not in {"legacy", "orm"}:
                issues.append(
                    ReconciliationIssue(
                        object_type,
                        "*",
                        "unknown_ownership",
                        "current_primary",
                    )
                )
                continue
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
            if not _table_has_id(legacy_connection, legacy_table):
                issues.append(
                    ReconciliationIssue(
                        object_type, "*", "id_column_missing", "legacy.id"
                    )
                )
                continue
            if not _table_has_id(orm_connection, orm_table):
                issues.append(
                    ReconciliationIssue(object_type, "*", "id_column_missing", "orm.id")
                )
                continue
            for side, connection, table in (
                ("legacy", legacy_connection, legacy_table),
                ("orm", orm_connection, orm_table),
            ):
                missing_ids = _rows_with_missing_ids(connection, table)
                for _ in range(missing_ids):
                    issues.append(
                        ReconciliationIssue(
                            object_type,
                            "*",
                            "missing_id",
                            f"{side}.id",
                        )
                    )
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
    if DATA_OWNERSHIP[object_type].get("current_primary") not in {"legacy", "orm"}:
        raise RecoveryError(f"Unknown convergence ownership: {object_type}")
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
