from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config

from app import db, expedition_service, learning_run, orm, provider_service
from app.database_recovery import (
    RecoveryError,
    backup_sqlite,
    inspect_sqlite,
    restore_sqlite,
)
from app.orm import LearningRunORM, WorkspaceORM
from app.security_redaction import REDACTED, redact, redact_text


def _alembic_config(database: Path) -> Config:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    return config


def test_empty_orm_database_migrates_to_head_twice(tmp_path: Path):
    database = tmp_path / "empty.db"
    config = _alembic_config(database)
    command.upgrade(config, "head")
    first = inspect_sqlite(database)
    command.upgrade(config, "head")
    second = inspect_sqlite(database)

    with sqlite3.connect(database) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
    assert revision == "0012_knowledge_map_foundation"
    assert first.table_counts == second.table_counts
    assert second.foreign_key_violations == 0


def test_legacy_init_is_additive_and_preserves_existing_rows(tmp_path: Path, monkeypatch):
    database = tmp_path / "legacy.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()
    with db.connection() as connection:
        connection.execute(
            "INSERT INTO workspaces "
            "(id,title,description,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            ("ws-preserved", "Preserve me", "", "active", "now", "now"),
        )

    db.init_db()
    assert db.row("workspaces", "id", "ws-preserved")["title"] == "Preserve me"
    assert inspect_sqlite(database).table_counts["workspaces"] == 1


def test_orm_upgrade_preserves_existing_data_and_relationships(tmp_path: Path):
    database = tmp_path / "with-data.db"
    config = _alembic_config(database)
    command.upgrade(config, "head")
    engine = orm._create_engine(database)
    sessions = orm.sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        session.add(
            WorkspaceORM(
                id="orm-ws-preserved",
                title="Preserve ORM",
                description="migration evidence",
            )
        )
        session.commit()
    engine.dispose()

    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT title FROM orm_workspaces WHERE id='orm-ws-preserved'"
        ).fetchone() == ("Preserve ORM",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_backup_restore_round_trip_and_refuses_overwrite(tmp_path: Path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE parent (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO parent(payload) VALUES ('kept')")
    backup = tmp_path / "backups" / "source.backup.db"
    restored = tmp_path / "restored.db"

    backup_snapshot = backup_sqlite(source, backup)
    restore_snapshot = restore_sqlite(backup, restored)
    assert backup_snapshot.table_counts == {"parent": 1}
    assert restore_snapshot.table_counts == backup_snapshot.table_counts
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT payload FROM parent").fetchone() == ("kept",)
    with pytest.raises(RecoveryError, match="overwrite"):
        restore_sqlite(backup, restored)


def test_corrupt_backup_is_rejected_without_partial_destination(tmp_path: Path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not sqlite")
    destination = tmp_path / "must-not-exist.db"
    with pytest.raises(RecoveryError, match="Invalid SQLite"):
        restore_sqlite(corrupt, destination)
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".partial").exists()


class _InvalidJsonProvider(provider_service.Provider):
    async def text(self, messages, temperature=0.2):
        return "not-json"


def test_invalid_provider_json_is_explicit_and_never_silently_faked():
    with pytest.raises(provider_service.ProviderError) as error:
        asyncio.run(_InvalidJsonProvider().json([]))
    assert error.value.code == "INVALID_PROVIDER_JSON"
    assert error.value.status == 502


def test_provider_timeout_is_explicit_and_does_not_fallback(monkeypatch):
    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, *args, **kwargs):
            request = httpx.Request("POST", "https://provider.invalid")
            raise httpx.ReadTimeout("bounded timeout", request=request)

    monkeypatch.setattr(provider_service.httpx, "AsyncClient", lambda **kwargs: TimeoutClient())
    provider = provider_service.OpenAICompatibleProvider(
        "https://provider.invalid/v1", "model", "never-log-this"
    )
    with pytest.raises(provider_service.ProviderError) as error:
        asyncio.run(provider.text([{"role": "user", "content": "test"}]))
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert error.value.status == 504
    assert "never-log-this" not in str(error.value)


def test_redaction_handles_nested_keys_headers_urls_and_known_values():
    secret = "sk-local-secret-123"
    payload = {
        "authorization": f"Bearer {secret}",
        "nested": {
            "api-key": secret,
            "url": f"https://example.test/v1?api_key={secret}&safe=yes",
        },
        "message": f"password={secret} Bearer {secret}",
    }
    result = redact(payload, known_secrets=[secret])
    serialized = json.dumps(result)
    assert secret not in serialized
    assert result["authorization"] == REDACTED
    assert result["nested"]["api-key"] == REDACTED
    assert "safe=yes" in result["nested"]["url"]
    assert REDACTED in redact_text(f"token={secret}", known_secrets=[secret])


def test_failed_reward_sidecar_keeps_terminal_learning_state_retryable(monkeypatch):
    orm.init_orm()
    run_id = f"run-sidecar-{uuid.uuid4().hex}"
    with orm.SessionLocal() as session:
        session.add(
            LearningRunORM(
                id=run_id,
                workspace_id="ws-sidecar",
                task_id="task-sidecar",
                lesson_id="lesson-sidecar",
                phase="passed",
                status="passed",
                state={
                    "standard_task_minutes": 25,
                    "expedition_reward_delivery": {
                        "status": "pending",
                        "attempts": 1,
                        "retryable": True,
                    },
                },
                revision=7,
                last_response={
                    "run_id": run_id,
                    "phase": "passed",
                    "status": "passed",
                    "revision": 7,
                },
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    def fail_reward(*args, **kwargs):
        raise RuntimeError("reward store unavailable")

    monkeypatch.setattr(expedition_service, "publish_verified_learning_reward", fail_reward)
    retried = learning_run.retry_pending_expedition_reward(run_id)
    current = learning_run.get_learning_run(run_id)
    assert retried["status"] == "passed"
    assert retried["state"]["expedition_reward_delivery"]["status"] == "pending"
    assert retried["state"]["expedition_reward_delivery"]["attempts"] == 2
    assert retried["state"]["expedition_reward_delivery"]["retryable"] is True
    assert current["phase"] == "passed"
    assert current["status"] == "passed"
    assert current["revision"] == 7
