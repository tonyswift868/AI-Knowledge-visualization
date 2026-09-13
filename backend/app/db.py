from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .runtime_resources import project_resource_root
from typing import Any, Iterator

ROOT = project_resource_root()
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("MENTORFLOW_DB", DATA_DIR / "app.db"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '', status TEXT DEFAULT 'active',
              character_id TEXT DEFAULT 'warm', allow_external_knowledge INTEGER DEFAULT 0, persona_prompt TEXT DEFAULT '',
              execution_mode TEXT DEFAULT 'demo', provider_name TEXT DEFAULT 'fake', provider_model TEXT DEFAULT '',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL, source_type TEXT NOT NULL,
              original_filename TEXT DEFAULT '', original_url TEXT DEFAULT '', local_path TEXT DEFAULT '',
              trust_level TEXT DEFAULT 'personal_notes', content_hash TEXT NOT NULL, status TEXT DEFAULT 'ready',
              parse_error TEXT DEFAULT '', page_count INTEGER DEFAULT 1, text TEXT DEFAULT '',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS archive_previews (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, archive_name TEXT NOT NULL,
              archive_hash TEXT NOT NULL, archive_size_bytes INTEGER NOT NULL DEFAULT 0,
              expanded_size_bytes INTEGER NOT NULL DEFAULT 0, file_count INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'ready', expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
              storage_path TEXT NOT NULL DEFAULT '', archive_mtime_ns INTEGER NOT NULL DEFAULT 0,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS archive_preview_items (
              id TEXT PRIMARY KEY, preview_id TEXT NOT NULL, relative_path TEXT NOT NULL,
              filename TEXT NOT NULL, extension TEXT NOT NULL, size_bytes INTEGER NOT NULL DEFAULT 0,
              compressed_size_bytes INTEGER NOT NULL DEFAULT 0, content_hash TEXT NOT NULL DEFAULT '',
              kind TEXT NOT NULL, language TEXT DEFAULT '', status TEXT NOT NULL, reason TEXT DEFAULT '',
              selected_by_default INTEGER NOT NULL DEFAULT 0,
              FOREIGN KEY(preview_id) REFERENCES archive_previews(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS chunks (
              id TEXT PRIMARY KEY, source_id TEXT NOT NULL, ordinal INTEGER NOT NULL, page INTEGER DEFAULT 1,
              heading TEXT DEFAULT '', text TEXT NOT NULL, normalized_text TEXT NOT NULL, content_hash TEXT NOT NULL,
              FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS goals (
              id TEXT PRIMARY KEY, workspace_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
              confirmed INTEGER DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS assessments (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, payload TEXT NOT NULL,
              user_confirmed INTEGER DEFAULT 0, created_at TEXT NOT NULL,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS plans (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
              status TEXT DEFAULT 'draft', created_at TEXT NOT NULL, activated_at TEXT DEFAULT '',
              FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS study_days (
              id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, date TEXT NOT NULL, payload TEXT NOT NULL,
              FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, study_day_id TEXT NOT NULL, lesson_id TEXT DEFAULT '', payload TEXT NOT NULL,
              FOREIGN KEY(study_day_id) REFERENCES study_days(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, lesson_id TEXT DEFAULT '', payload TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, payload TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS catalog_curation_requests (
              id TEXT PRIMARY KEY, catalog_id TEXT NOT NULL, workspace_id TEXT DEFAULT '', requested_task_profile TEXT NOT NULL,
              status TEXT DEFAULT 'queued', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS local_previews (
              id TEXT PRIMARY KEY, root TEXT NOT NULL, entries_json TEXT NOT NULL,
              expires_at TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(workspaces)").fetchall()}
        for name, definition in {
            "execution_mode": "TEXT DEFAULT 'demo'",
            "provider_name": "TEXT DEFAULT 'fake'",
            "provider_model": "TEXT DEFAULT ''",
            "persona_prompt": "TEXT DEFAULT ''",
        }.items():
            if name not in columns:
                db.execute(f"ALTER TABLE workspaces ADD COLUMN {name} {definition}")
        source_columns = {row[1] for row in db.execute("PRAGMA table_info(sources)").fetchall()}
        for name, definition in {
            "archive_preview_id": "TEXT DEFAULT ''", "archive_relative_path": "TEXT DEFAULT ''",
            "parent_archive_name": "TEXT DEFAULT ''", "language": "TEXT DEFAULT ''",
        }.items():
            if name not in source_columns:
                db.execute(f"ALTER TABLE sources ADD COLUMN {name} {definition}")


def row(table: str, key: str, value: str) -> dict[str, Any] | None:
    with connection() as db:
        item = db.execute(f"SELECT * FROM {table} WHERE {key} = ?", (value,)).fetchone()
        return dict(item) if item else None


def rows(table: str, key: str, value: str) -> list[dict[str, Any]]:
    with connection() as db:
        return [dict(x) for x in db.execute(f"SELECT * FROM {table} WHERE {key} = ? ORDER BY rowid", (value,)).fetchall()]


def json_payload(item: dict[str, Any]) -> dict[str, Any]:
    if "payload" in item and isinstance(item["payload"], str):
        try:
            item["payload"] = json.loads(item["payload"])
        except json.JSONDecodeError:
            pass
    return item
