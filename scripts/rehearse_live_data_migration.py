r"""Rehearse the v1 Alembic upgrade and restore against an immutable live backup.

The source backup is opened read-only.  All migration writes target a disposable
copy under E:\Codex\Temp, and the report proves that LeetCode workspace, plan,
task and progress rows remain byte-for-byte equivalent at the SQLite value level.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
DEFAULT_BACKUP = Path(
    r"E:\Codex\Data\backups\MentorFlow\20260813-knowledge-map-teacher-v1-prechange"
)
DEFAULT_TEMP_ROOT = Path(r"E:\Codex\Temp")
LEETCODE_TABLES = (
    "orm_workspaces",
    "orm_plans",
    "orm_progress",
    "active_plans",
    "active_plan_tasks",
    "active_knowledge_progress_snapshots",
    "active_review_items",
    "active_teaching_sessions",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _tables(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        if not str(row[0]).startswith("sqlite_")
    ]


def _row_counts(path: Path) -> dict[str, int]:
    with _connect_readonly(path) as connection:
        return {
            table: int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                ).fetchone()[0]
            )
            for table in _tables(connection)
        }


def _leetcode_workspace_ids(connection: sqlite3.Connection) -> list[str]:
    return [
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


def _stable_rows(rows: list[sqlite3.Row]) -> list[list[Any]]:
    return [
        [value.hex() if isinstance(value, bytes) else value for value in tuple(row)]
        for row in rows
    ]


def _leetcode_snapshot(path: Path) -> dict[str, Any]:
    with _connect_readonly(path) as connection:
        workspace_ids = _leetcode_workspace_ids(connection)
        placeholders = ",".join("?" for _ in workspace_ids)
        tables = set(_tables(connection))
        result: dict[str, Any] = {"workspace_ids": workspace_ids, "tables": {}}
        for table in LEETCODE_TABLES:
            if table not in tables:
                continue
            columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
            if table == "orm_workspaces":
                predicate = f"id IN ({placeholders})"
            elif "workspace_id" in columns:
                predicate = f"workspace_id IN ({placeholders})"
            else:
                continue
            order = "id" if "id" in columns else ",".join(columns)
            rows = _stable_rows(
                list(
                    connection.execute(
                        f'SELECT * FROM "{table}" WHERE {predicate} ORDER BY {order}',
                        workspace_ids,
                    )
                )
            )
            encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str)
            result["tables"][table] = {
                "row_count": len(rows),
                "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            }
        return result


def _database_checks(path: Path) -> dict[str, Any]:
    with _connect_readonly(path) as connection:
        version = None
        if "alembic_version" in _tables(connection):
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            version = str(row[0]) if row else None
        return {
            "integrity_check": str(connection.execute("PRAGMA integrity_check").fetchone()[0]),
            "foreign_key_violations": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
            "alembic_version": version,
        }


def _upgrade(database: Path) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--temp-root", type=Path, default=DEFAULT_TEMP_ROOT)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    backup = args.backup.expanduser().resolve(strict=True)
    source_app = backup / "data" / "app.db"
    source_orm = backup / "data" / "mentorflow_v2.db"
    if not source_app.is_file() or not source_orm.is_file():
        raise FileNotFoundError("backup must contain data/app.db and data/mentorflow_v2.db")

    run_root = args.temp_root.expanduser().resolve() / (
        f"mentorflow-live-migration-rehearsal-{time.strftime('%Y%m%d-%H%M%S')}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    migrated_root = run_root / "migrated"
    restored_root = run_root / "restored"
    migrated_root.mkdir(parents=True)
    restored_root.mkdir(parents=True)
    migrated_app = migrated_root / "app.db"
    migrated_orm = migrated_root / "mentorflow_v2.db"
    restored_app = restored_root / "app.db"
    restored_orm = restored_root / "mentorflow_v2.db"
    for source, target in (
        (source_app, migrated_app),
        (source_orm, migrated_orm),
        (source_app, restored_app),
        (source_orm, restored_orm),
    ):
        shutil.copy2(source, target)
        target.chmod(0o600)

    before_counts = _row_counts(source_orm)
    before_leetcode = _leetcode_snapshot(source_orm)
    _upgrade(migrated_orm)
    after_counts = _row_counts(migrated_orm)
    after_leetcode = _leetcode_snapshot(migrated_orm)
    preexisting_count_changes = {
        table: {"before": count, "after": after_counts.get(table)}
        for table, count in before_counts.items()
        if table != "alembic_version" and after_counts.get(table) != count
    }
    restored_hashes_match = {
        "app.db": _sha256(restored_app) == _sha256(source_app),
        "mentorflow_v2.db": _sha256(restored_orm) == _sha256(source_orm),
    }
    report: dict[str, Any] = {
        "schema": "mentorflow.live-data-migration-rehearsal.v1",
        "status": "passed",
        "source_backup": str(backup),
        "run_root": str(run_root),
        "source_was_read_only": True,
        "checks": {
            "before": _database_checks(source_orm),
            "after": _database_checks(migrated_orm),
            "restored": _database_checks(restored_orm),
            "preexisting_table_row_count_changes": preexisting_count_changes,
            "leetcode_before": before_leetcode,
            "leetcode_after": after_leetcode,
            "leetcode_rows_preserved": before_leetcode == after_leetcode,
            "restore_hashes_match_source": restored_hashes_match,
            "app_db_untouched_by_migration": _sha256(migrated_app) == _sha256(source_app),
        },
    }
    required = (
        report["checks"]["before"]["integrity_check"] == "ok"
        and report["checks"]["after"]["integrity_check"] == "ok"
        and report["checks"]["after"]["foreign_key_violations"] == 0
        and report["checks"]["after"]["alembic_version"] == "0021_local_login_throttle"
        and not preexisting_count_changes
        and before_leetcode == after_leetcode
        and all(restored_hashes_match.values())
        and report["checks"]["app_db_untouched_by_migration"]
    )
    if not required:
        report["status"] = "failed"
    report_path = (
        args.report.expanduser().resolve()
        if args.report
        else run_root / "migration-rehearsal-report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0 if required else 1


if __name__ == "__main__":
    raise SystemExit(main())
