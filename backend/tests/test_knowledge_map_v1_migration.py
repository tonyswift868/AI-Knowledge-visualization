from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app import active_teaching
from app.orm import WorkspaceORM, _create_engine, sessionmaker


def _alembic_config(database: Path) -> Config:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    # Programmatic migrations must not replace pytest/caplog handlers.  The
    # standalone Alembic CLI retains its normal logging configuration.
    config.attributes["configure_logger"] = False
    return config


def test_in_process_migration_supports_backend_package_from_repository_root(
    tmp_path: Path,
):
    """Exercise the same import shape used by the isolated acceptance server."""

    project_root = Path(__file__).resolve().parents[2]
    database = tmp_path / "root-package-startup.db"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from backend.app import orm; "
                f"orm.configure_orm({str(database)!r}); "
                "orm.upgrade_orm_schema(); "
                "print(orm.ORM_DB)"
            ),
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0021_local_login_throttle",)


def test_knowledge_map_teacher_v1_migration_is_additive_and_explicit(tmp_path: Path):
    database = tmp_path / "knowledge-map-v1.db"
    config = _alembic_config(database)

    command.upgrade(config, "0012_knowledge_map_foundation")
    engine = _create_engine(database)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        session.add(WorkspaceORM(id="ws-preserved", title="Preserve this row", description=""))
        session.commit()
    engine.dispose()

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    expected_tables = {
        "orm_knowledge_map_jobs",
        "orm_material_dispositions",
        "orm_knowledge_review_candidates",
        "orm_plan_activation_records",
        "orm_learner_preferences",
        "orm_teacher_turns",
        "orm_teacher_events",
        "orm_plan_execution_tasks",
        "orm_plan_mastery_events",
        "orm_learning_resources",
        "orm_local_users",
        "orm_local_sessions",
        "orm_workspace_ownership",
        "active_extraction_runs",
        "active_point_details",
        "active_plan_calendars",
        "active_external_resources",
        "active_resource_discovery_runs",
    }
    inspector = inspect(_create_engine(database))
    assert expected_tables <= set(inspector.get_table_names())
    assert {
        "status",
        "staging_token",
        "staged_at",
        "activation_id",
        "activated_at",
        "failure_json",
    } <= {column["name"] for column in inspector.get_columns("orm_knowledge_map_revisions")}

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT title FROM orm_workspaces WHERE id = 'ws-preserved'"
        ).fetchone() == ("Preserve this row",)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0021_local_login_throttle",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    unique_constraints = {
        item.get("name")
        for item in inspector.get_unique_constraints("orm_material_dispositions")
    }
    assert "uq_orm_material_disposition_revision" in unique_constraints
    assert "uq_orm_material_disposition_material" not in unique_constraints


def test_knowledge_map_statuses_and_idempotency_are_string_columns(tmp_path: Path):
    database = tmp_path / "knowledge-map-v1-statuses.db"
    command.upgrade(_alembic_config(database), "head")
    with sqlite3.connect(database) as connection:
        statuses = {
            "queued",
            "converting",
            "extracting",
            "validating",
            "ready_with_review",
            "ready",
            "failed",
            "cancelled",
        }
        connection.execute(
            "INSERT INTO orm_knowledge_map_jobs "
            "(id, workspace_id, map_id, job_type, status, idempotency_key) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("job-1", "ws-1", "map-1", "generate", "queued", "idem-1"),
        )
        for index, status in enumerate(sorted(statuses), start=2):
            connection.execute(
                "INSERT INTO orm_knowledge_map_jobs "
                "(id, workspace_id, map_id, job_type, status, idempotency_key) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"job-{index}", "ws-1", "map-1", "generate", status, f"idem-{index}"),
            )
        assert {
            row[0]
            for row in connection.execute(
                "SELECT status FROM orm_knowledge_map_jobs WHERE workspace_id = 'ws-1'"
            )
        } == statuses
        column = next(
            item for item in connection.execute("PRAGMA table_info('orm_knowledge_map_jobs')")
            if item[1] == "status"
        )
        assert column[2].upper().startswith("VARCHAR")
        connection.commit()


def test_0013_upgrades_a_nonempty_pre_v1_revision_table(tmp_path: Path):
    """Mirror the real 0011-era DB where request bootstrap precreated 0012 tables."""

    database = tmp_path / "knowledge-map-v1-live-shape.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL PRIMARY KEY);
            INSERT INTO alembic_version(version_num) VALUES ('0012_knowledge_map_foundation');
            CREATE TABLE orm_knowledge_map_revisions (
                id VARCHAR(96) PRIMARY KEY,
                map_id VARCHAR(64) NOT NULL,
                workspace_id VARCHAR(64) NOT NULL,
                revision INTEGER NOT NULL,
                is_current BOOLEAN NOT NULL,
                generation_mode VARCHAR(40) NOT NULL,
                provider_used BOOLEAN NOT NULL,
                source_revisions JSON NOT NULL,
                quality JSON NOT NULL,
                fingerprint VARCHAR(128) NOT NULL,
                warnings JSON NOT NULL,
                created_at DATETIME NOT NULL
            );
            INSERT INTO orm_knowledge_map_revisions
                (id,map_id,workspace_id,revision,is_current,generation_mode,
                 provider_used,source_revisions,quality,fingerprint,warnings,created_at)
            VALUES
                ('legacy-rev','legacy-map','legacy-workspace',1,1,'legacy_unverified',
                 0,'[]','{}','legacy-fingerprint','[]','2026-01-02 03:04:05');
            """
        )

    command.upgrade(_alembic_config(database), "head")

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status, is_current, staged_at, fingerprint FROM orm_knowledge_map_revisions "
            "WHERE id='legacy-rev'"
        ).fetchone()
        assert row == ("legacy_unverified", 0, "2026-01-02 03:04:05", "legacy-fingerprint")
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_request_connection_never_bootstraps_active_teaching_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "unmigrated-request.db"
    database.touch()
    monkeypatch.setattr(active_teaching, "_db_path", lambda: database)

    with pytest.raises(RuntimeError, match="DATABASE_MIGRATION_REQUIRED"):
        active_teaching._connect()

    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == []
