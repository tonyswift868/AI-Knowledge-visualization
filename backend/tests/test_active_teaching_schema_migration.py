from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app import active_teaching


TABLES = (
    "active_materials",
    "active_knowledge_points",
    "active_plans",
    "active_plan_tasks",
)

COLUMNS = {
    "active_materials": (
        ("id", "TEXT", False, None),
        ("workspace_id", "TEXT", False, None),
        ("original_filename", "TEXT", False, None),
        ("media_type", "TEXT", False, None),
        ("size_bytes", "INTEGER", False, None),
        ("sha256", "TEXT", False, None),
        ("uploaded_at", "TEXT", False, None),
        ("stored_path", "TEXT", False, None),
        ("status", "TEXT", False, None),
        ("parse_error", "TEXT", False, ""),
        ("page_count", "INTEGER", False, 0),
        ("parser_name", "TEXT", False, None),
        ("archived", "INTEGER", False, 0),
        ("archived_at", "TEXT", True, None),
        ("archive_reason", "TEXT", False, ""),
        ("material_revision", "INTEGER", False, 1),
        ("source_url", "TEXT", False, ""),
        ("source_kind", "TEXT", False, "uploaded_file"),
        ("provenance_json", "TEXT", False, "{}"),
    ),
    "active_knowledge_points": (
        ("id", "TEXT", False, None),
        ("workspace_id", "TEXT", False, None),
        ("material_id", "TEXT", False, None),
        ("normalized_name", "TEXT", False, None),
        ("name", "TEXT", False, None),
        ("explanation", "TEXT", False, None),
        ("difficulty", "TEXT", False, None),
        ("estimated_task_minutes", "INTEGER", False, None),
        ("mastery", "REAL", False, 0),
        ("acceptance_method", "TEXT", False, None),
        ("source_evidence", "TEXT", False, None),
        ("isolated", "INTEGER", False, 0),
        ("created_at", "TEXT", False, None),
    ),
    "active_plans": (
        ("id", "TEXT", False, None),
        ("workspace_id", "TEXT", False, None),
        ("receipt", "TEXT", False, None),
        ("created_at", "TEXT", False, None),
        ("feasible", "INTEGER", False, None),
        ("total_estimated_task_minutes", "INTEGER", False, None),
        ("unscheduled_task_minutes", "INTEGER", False, None),
        ("warning", "TEXT", False, ""),
    ),
    "active_plan_tasks": (
        ("id", "TEXT", False, None),
        ("plan_id", "TEXT", False, None),
        ("workspace_id", "TEXT", False, None),
        ("day_date", "TEXT", False, None),
        ("original_day_date", "TEXT", False, ""),
        ("day_index", "INTEGER", False, None),
        ("task_order", "INTEGER", False, None),
        ("knowledge_point_id", "TEXT", False, None),
        ("title", "TEXT", False, None),
        ("objective", "TEXT", False, None),
        ("source_evidence", "TEXT", False, None),
        ("acceptance_condition", "TEXT", False, None),
        ("estimated_task_minutes", "INTEGER", False, None),
        ("completed_task_minutes", "INTEGER", False, 0),
        ("completed_at", "TEXT", True, None),
        ("actual_elapsed_minutes", "REAL", False, 0),
        ("status", "TEXT", False, "planned"),
        ("task_type", "TEXT", False, "initial_learning"),
        ("learning_phase", "TEXT", False, "integrated_initial"),
        ("point_sequence", "INTEGER", False, 0),
        ("phase_part_index", "INTEGER", False, 1),
        ("phase_part_count", "INTEGER", False, 1),
        ("linked_review_item_id", "TEXT", False, ""),
        ("review_cycle", "INTEGER", False, 0),
        ("origin_task_id", "TEXT", False, ""),
        ("not_before_date", "TEXT", True, None),
        ("trigger_reason", "TEXT", False, ""),
    ),
}

PRIMARY_KEYS = {table: ("id",) for table in TABLES}
UNIQUE_CONSTRAINTS = {
    "active_materials": (("workspace_id", "sha256"),),
    "active_knowledge_points": (("workspace_id", "normalized_name"),),
    "active_plans": (),
    "active_plan_tasks": (),
}
FOREIGN_KEYS = {
    "active_materials": (),
    "active_knowledge_points": (
        ("material_id", "active_materials", "id", "CASCADE", "NO ACTION"),
    ),
    "active_plans": (),
    "active_plan_tasks": (
        ("knowledge_point_id", "active_knowledge_points", "id", "NO ACTION", "NO ACTION"),
        ("plan_id", "active_plans", "id", "CASCADE", "NO ACTION"),
    ),
}
EXPLICIT_INDEXES = {
    "active_materials": (),
    "active_knowledge_points": (),
    "active_plans": (),
    "active_plan_tasks": (
        ("idx_active_plan_tasks_review", ("linked_review_item_id", "status")),
        ("idx_active_plan_tasks_schedule", ("plan_id", "status", "day_date")),
    ),
}


def _alembic_config(database: Path) -> Config:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    return config


def _canonical_head(config: Config) -> str:
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


def _logical_type(declared_type: str) -> str:
    value = declared_type.upper()
    if "INT" in value:
        return "INTEGER"
    if value in {"FLOAT", "REAL", "DOUBLE", "DOUBLE PRECISION"}:
        return "REAL"
    if "TEXT" in value or "CHAR" in value or "CLOB" in value:
        return "TEXT"
    return value


def _logical_default(value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    while len(text) >= 2 and text[0] == "(" and text[-1] == ")":
        text = text[1:-1].strip()
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        text = text[1:-1].replace("''", "'")
    try:
        return int(text)
    except ValueError:
        return text


def _closure_snapshot(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    snapshot: dict[str, dict[str, Any]] = {}
    for table in TABLES:
        columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        indexes = connection.execute(f'PRAGMA index_list("{table}")').fetchall()

        primary_key = tuple(
            row["name"] for row in sorted(columns, key=lambda item: item["pk"]) if row["pk"]
        )
        unique_constraints = []
        explicit_indexes = []
        for index in indexes:
            index_columns = tuple(
                row["name"]
                for row in connection.execute(
                    f'PRAGMA index_info("{index["name"]}")'
                ).fetchall()
            )
            if index["origin"] == "u":
                unique_constraints.append(index_columns)
            elif index["origin"] == "c":
                explicit_indexes.append((index["name"], index_columns))

        foreign_keys = tuple(
            sorted(
                (
                    row["from"],
                    row["table"],
                    row["to"],
                    row["on_delete"].upper(),
                    row["on_update"].upper(),
                )
                for row in connection.execute(
                    f'PRAGMA foreign_key_list("{table}")'
                ).fetchall()
            )
        )
        snapshot[table] = {
            "columns": tuple(
                (
                    row["name"],
                    _logical_type(row["type"]),
                    not bool(row["notnull"] or row["pk"]),
                    _logical_default(row["dflt_value"]),
                )
                for row in columns
            ),
            "primary_key": primary_key,
            "unique_constraints": tuple(sorted(unique_constraints)),
            "foreign_keys": foreign_keys,
            "explicit_indexes": tuple(sorted(explicit_indexes)),
        }
    return snapshot


def _expected_snapshot() -> dict[str, dict[str, Any]]:
    return {
        table: {
            "columns": COLUMNS[table],
            "primary_key": PRIMARY_KEYS[table],
            "unique_constraints": UNIQUE_CONSTRAINTS[table],
            "foreign_keys": FOREIGN_KEYS[table],
            "explicit_indexes": EXPLICIT_INDEXES[table],
        }
        for table in TABLES
    }


def _assert_database_health(connection: sqlite3.Connection) -> None:
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_schema_is_exact_and_runtime_bootstrap_is_idempotent(tmp_path: Path):
    database = tmp_path / "migration-first.db"
    config = _alembic_config(database)
    command.upgrade(config, "head")

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        migrated = _closure_snapshot(connection)
        assert migrated == _expected_snapshot()

        active_teaching._init_schema(connection)
        first_bootstrap = _closure_snapshot(connection)
        active_teaching._init_schema(connection)
        second_bootstrap = _closure_snapshot(connection)

        assert first_bootstrap == migrated
        assert second_bootstrap == migrated
        _assert_database_health(connection)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == _canonical_head(config)


def test_runtime_bootstrap_schema_is_unchanged_by_migration_to_head(tmp_path: Path):
    database = tmp_path / "bootstrap-first.db"
    config = _alembic_config(database)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        active_teaching._init_schema(connection)
        bootstrapped = _closure_snapshot(connection)
        assert bootstrapped == _expected_snapshot()
        _assert_database_health(connection)

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        migrated = _closure_snapshot(connection)
        assert migrated == bootstrapped
        _assert_database_health(connection)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == _canonical_head(config)
