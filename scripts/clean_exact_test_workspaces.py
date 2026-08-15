"""Remove explicitly named test workspaces while proving user progress is unchanged."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DB = PROJECT_ROOT / "data" / "app.db"
ORM_DB = PROJECT_ROOT / "data" / "mentorflow_v2.db"
UPLOAD_ROOT = (PROJECT_ROOT / "data" / "active_teaching" / "uploads").resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _leetcode_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    ids = [
        str(row[0])
        for row in connection.execute(
            """SELECT id FROM orm_workspaces
               WHERE lower(coalesce(title,'')) LIKE '%leetcode%'
                  OR title LIKE '%力扣%'
                  OR lower(coalesce(title,'')) LIKE '%hot 150%'
                  OR lower(coalesce(title,'')) LIKE '%top 150%'
               ORDER BY id"""
        )
    ]
    placeholders = ",".join("?" for _ in ids)
    result: dict[str, Any] = {"workspace_ids": ids, "tables": {}}
    for table in ("orm_workspaces", "orm_plans", "orm_progress"):
        key = "id" if table == "orm_workspaces" else "workspace_id"
        rows = connection.execute(
            f'SELECT * FROM "{table}" WHERE {key} IN ({placeholders}) ORDER BY 1', ids
        ).fetchall()
        encoded = json.dumps(rows, ensure_ascii=False, default=str, separators=(",", ":"))
        result["tables"][table] = {
            "row_count": len(rows),
            "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        }
    return result


def _tables_with_column(connection: sqlite3.Connection, column: str) -> list[str]:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        if not str(row[0]).startswith("sqlite_")
    ]
    return [
        table
        for table in tables
        if column in {
            str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
    ]


def _clean_database(
    path: Path,
    *,
    workspace_table: str,
    target_ids: list[str],
) -> dict[str, Any]:
    if not target_ids:
        return {"deleted": {}, "integrity_check": "ok", "foreign_key_violations": []}
    placeholders = ",".join("?" for _ in target_ids)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA defer_foreign_keys=ON")
        before = {
            table: int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE workspace_id IN ({placeholders})',
                    target_ids,
                ).fetchone()[0]
            )
            for table in _tables_with_column(connection, "workspace_id")
            if table != workspace_table
        }
        # Direct workspace-owned rows are removed first.  Declared cascades
        # remove source/plan/campaign children that do not carry workspace_id.
        for table in reversed(list(before)):
            connection.execute(
                f'DELETE FROM "{table}" WHERE workspace_id IN ({placeholders})',
                target_ids,
            )
        root_key = "id" if workspace_table in {"workspaces", "orm_workspaces"} else "workspace_id"
        root_count = int(
            connection.execute(
                f'SELECT COUNT(*) FROM "{workspace_table}" WHERE {root_key} IN ({placeholders})',
                target_ids,
            ).fetchone()[0]
        )
        connection.execute(
            f'DELETE FROM "{workspace_table}" WHERE {root_key} IN ({placeholders})',
            target_ids,
        )
        connection.commit()
        remaining = sum(
            int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE workspace_id IN ({placeholders})',
                    target_ids,
                ).fetchone()[0]
            )
            for table in _tables_with_column(connection, "workspace_id")
            if table != workspace_table
        )
        remaining += int(
            connection.execute(
                f'SELECT COUNT(*) FROM "{workspace_table}" WHERE {root_key} IN ({placeholders})',
                target_ids,
            ).fetchone()[0]
        )
        return {
            "deleted": {**{table: count for table, count in before.items() if count}, workspace_table: root_count},
            "remaining_target_rows": remaining,
            "integrity_check": str(connection.execute("PRAGMA integrity_check").fetchone()[0]),
            "foreign_key_violations": connection.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-workspace", action="append", default=[])
    parser.add_argument("--orm-workspace", action="append", default=[])
    parser.add_argument("--active-workspace", action="append", default=[])
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    app_ids = sorted(set(args.app_workspace))
    orm_ids = sorted(set(args.orm_workspace) | set(args.active_workspace))
    active_ids = sorted(set(args.active_workspace))
    if not app_ids and not orm_ids:
        raise ValueError("at least one exact workspace ID is required")
    backup = args.backup.expanduser().resolve(strict=True)
    if not (backup / "data" / "app.db").is_file():
        raise FileNotFoundError("backup is missing data/app.db")

    before_hashes = {"app.db": _sha256(APP_DB), "mentorflow_v2.db": _sha256(ORM_DB)}
    with sqlite3.connect(ORM_DB) as connection:
        leetcode_before = _leetcode_snapshot(connection)
    app_result = _clean_database(APP_DB, workspace_table="workspaces", target_ids=app_ids)
    orm_result = _clean_database(ORM_DB, workspace_table="orm_workspaces", target_ids=orm_ids)
    removed_uploads: list[str] = []
    for workspace_id in active_ids:
        candidate = (UPLOAD_ROOT / workspace_id).resolve()
        if candidate.parent != UPLOAD_ROOT or candidate.name != workspace_id:
            raise RuntimeError(f"unsafe upload cleanup target: {candidate}")
        if candidate.is_dir():
            shutil.rmtree(candidate)
            removed_uploads.append(str(candidate))
    with sqlite3.connect(ORM_DB) as connection:
        leetcode_after = _leetcode_snapshot(connection)
    passed = (
        app_result["integrity_check"] == "ok"
        and orm_result["integrity_check"] == "ok"
        and not app_result["foreign_key_violations"]
        and not orm_result["foreign_key_violations"]
        and app_result["remaining_target_rows"] == 0
        and orm_result["remaining_target_rows"] == 0
        and leetcode_before == leetcode_after
    )
    report = {
        "schema": "mentorflow.exact-test-workspace-cleanup.v1",
        "status": "passed" if passed else "failed",
        "backup": str(backup),
        "targets": {"app": app_ids, "orm": orm_ids, "active_uploads": active_ids},
        "before_sha256": before_hashes,
        "app": app_result,
        "orm": orm_result,
        "removed_upload_directories": removed_uploads,
        "leetcode_before": leetcode_before,
        "leetcode_after": leetcode_after,
        "leetcode_unchanged": leetcode_before == leetcode_after,
        "after_sha256": {"app.db": _sha256(APP_DB), "mentorflow_v2.db": _sha256(ORM_DB)},
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    args.report.expanduser().resolve().write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.report.expanduser().resolve())
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
