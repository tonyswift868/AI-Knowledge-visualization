from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import sqlite3
import uuid
import csv
import io
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from .provider_runtime import generation_meta, provider_for_session, require_provider_session
from .provider_service import ProviderError


router = APIRouter(prefix="/api/active-teaching", tags=["active-teaching"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_ROOT = Path(
    os.getenv(
        "MENTORFLOW_ACTIVE_UPLOAD_ROOT",
        str(PROJECT_ROOT / "data" / "active_teaching" / "uploads"),
    )
).expanduser().resolve()
SUPPORTED_SUFFIXES = {
    ".txt", ".md", ".markdown", ".html", ".htm", ".pdf", ".docx",
    ".csv", ".json", ".yaml", ".yml", ".xml", ".rtf",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".h",
    ".go", ".rs", ".sql", ".sh", ".ps1",
    ".pptx", ".xlsx", ".epub",
}
CONFUSION_MARKERS = ("不懂", "不理解", "为什么", "再解释", "没明白", "听不懂", "看不懂")
UNDERSTOOD_MARKERS = ("懂了", "明白了", "理解了", "会了", "可以继续")
MAX_MATERIAL_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
TEACHER_VISUAL_PROFILES: dict[str, dict[str, Any]] = {
    "teacher-02-blonde-fang-v9": {
        "id": "teacher-02-blonde-fang-v9",
        "name": "教师 02 · 金发虎牙小熊",
        "description": "小熊软糖教学态与泳装聊天态共用同一套轻量互动角色。",
        "manifest_url": "/teacher2d/original-ai-teacher-blonde-fang/manifest.json",
        "interactive": True,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_text(value: str) -> str:
    return _hash_bytes(value.encode("utf-8", errors="ignore"))


def _normalise(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _requires_executable_verification(acceptance_method: str) -> bool:
    """Detect an observable coding requirement without assuming a course domain."""
    value = str(acceptance_method or "")
    return bool(
        re.search(
            (
                r"独立(?:编码|编程)|编写(?:代码|程序|函数)|可运行(?:代码|程序|实现)|"
                r"通过(?:边界|隐藏|单元|自动化)?测试|实现.+(?:算法|函数|程序|代码)|"
                r"(?:算法|函数|程序|代码).+实现|"
                r"\b(?:code|coding|implement|program|unit tests?|hidden tests?)\b"
            ),
            value,
            re.IGNORECASE,
        )
    )


def _coding_foundation_from_reply(message: str) -> str:
    """Classify an explicit learner self-report without treating arbitrary text as evidence."""
    value = str(message or "").strip().casefold()
    if value in {"a", "a。", "a.", "a、"} or any(
        marker in value
        for marker in (
            "不会写代码",
            "没写过代码",
            "完全不会",
            "零基础",
            "看不懂代码",
            "不懂编程",
        )
    ):
        return "none"
    if value in {"b", "b。", "b.", "b、"} or any(
        marker in value for marker in ("能看懂一点", "会一点", "有一点基础")
    ):
        return "partial"
    if value in {"c", "c。", "c.", "c、"} or any(
        marker in value for marker in ("能写简单", "会写代码", "有编程基础")
    ):
        return "comfortable"
    return "unknown"


def _db_path() -> Path:
    from . import orm

    return Path(orm.ORM_DB)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    _init_schema(connection)
    return connection


def _init_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS active_materials (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            media_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            status TEXT NOT NULL,
            parse_error TEXT NOT NULL DEFAULT '',
            page_count INTEGER NOT NULL DEFAULT 0,
            parser_name TEXT NOT NULL,
            UNIQUE(workspace_id, sha256)
        );
        CREATE TABLE IF NOT EXISTS active_chunks (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            material_id TEXT NOT NULL REFERENCES active_materials(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            page_start INTEGER,
            page_end INTEGER,
            paragraph_start INTEGER NOT NULL,
            paragraph_end INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            heading TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            text TEXT NOT NULL,
            UNIQUE(material_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS active_receipts (
            workspace_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_workspace_settings (
            workspace_id TEXT PRIMARY KEY,
            archived INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_teacher_personas (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            prompt TEXT NOT NULL,
            teaching_style TEXT NOT NULL DEFAULT '',
            visual_profile_id TEXT NOT NULL DEFAULT '',
            visual_profile_name TEXT NOT NULL DEFAULT '',
            visual_manifest_url TEXT NOT NULL DEFAULT '',
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_knowledge_points (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            material_id TEXT NOT NULL REFERENCES active_materials(id) ON DELETE CASCADE,
            normalized_name TEXT NOT NULL,
            name TEXT NOT NULL,
            explanation TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            estimated_task_minutes INTEGER NOT NULL,
            mastery REAL NOT NULL DEFAULT 0,
            acceptance_method TEXT NOT NULL,
            source_evidence TEXT NOT NULL,
            isolated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(workspace_id, normalized_name)
        );
        CREATE TABLE IF NOT EXISTS active_dependencies (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            prerequisite_id TEXT NOT NULL REFERENCES active_knowledge_points(id) ON DELETE CASCADE,
            dependent_id TEXT NOT NULL REFERENCES active_knowledge_points(id) ON DELETE CASCADE,
            evidence TEXT NOT NULL,
            UNIQUE(workspace_id, prerequisite_id, dependent_id)
        );
        CREATE TABLE IF NOT EXISTS active_knowledge_conflicts (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL REFERENCES active_knowledge_points(id) ON DELETE CASCADE,
            topic TEXT NOT NULL,
            evidence_a TEXT NOT NULL,
            evidence_b TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unresolved'
        );
        CREATE TABLE IF NOT EXISTS active_plans (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            receipt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            feasible INTEGER NOT NULL,
            total_estimated_task_minutes INTEGER NOT NULL,
            unscheduled_task_minutes INTEGER NOT NULL,
            warning TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS active_plan_tasks (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES active_plans(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            day_date TEXT NOT NULL,
            day_index INTEGER NOT NULL,
            task_order INTEGER NOT NULL,
            knowledge_point_id TEXT NOT NULL REFERENCES active_knowledge_points(id),
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            source_evidence TEXT NOT NULL,
            acceptance_condition TEXT NOT NULL,
            estimated_task_minutes INTEGER NOT NULL,
            completed_task_minutes INTEGER NOT NULL DEFAULT 0,
            actual_elapsed_minutes REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'planned',
            task_type TEXT NOT NULL DEFAULT 'initial_learning',
            learning_phase TEXT NOT NULL DEFAULT 'integrated_initial',
            point_sequence INTEGER NOT NULL DEFAULT 0,
            phase_part_index INTEGER NOT NULL DEFAULT 1,
            phase_part_count INTEGER NOT NULL DEFAULT 1,
            linked_review_item_id TEXT NOT NULL DEFAULT '',
            review_cycle INTEGER NOT NULL DEFAULT 0,
            origin_task_id TEXT NOT NULL DEFAULT '',
            not_before_date TEXT,
            trigger_reason TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS active_teaching_sessions (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            plan_id TEXT NOT NULL REFERENCES active_plans(id),
            state TEXT NOT NULL,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            actual_elapsed_minutes REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS active_teaching_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            action TEXT NOT NULL,
            content TEXT NOT NULL,
            citations TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_mastery_evidence (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            knowledge_point_id TEXT NOT NULL REFERENCES active_knowledge_points(id),
            evidence_type TEXT NOT NULL,
            score REAL NOT NULL,
            passed INTEGER NOT NULL,
            evidence TEXT NOT NULL,
            citations TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_task_time_events (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE,
            task_id TEXT NOT NULL REFERENCES active_plan_tasks(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            elapsed_minutes REAL NOT NULL,
            activity_kind TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_review_items (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            plan_id TEXT NOT NULL DEFAULT '',
            knowledge_point_id TEXT NOT NULL REFERENCES active_knowledge_points(id) ON DELETE CASCADE,
            source_task_id TEXT NOT NULL REFERENCES active_plan_tasks(id) ON DELETE CASCADE,
            source_mastery_evidence_id TEXT NOT NULL,
            due_date TEXT NOT NULL,
            interval_step INTEGER NOT NULL DEFAULT 0,
            interval_days INTEGER NOT NULL DEFAULT 1,
            review_total_minutes INTEGER NOT NULL DEFAULT 0,
            estimated_task_minutes INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            success_streak INTEGER NOT NULL DEFAULT 0,
            last_score REAL,
            last_passed INTEGER,
            last_attempted_at TEXT,
            scheduled_task_id TEXT NOT NULL DEFAULT '',
            scheduled_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(session_id, knowledge_point_id)
        );
        CREATE TABLE IF NOT EXISTS active_review_attempts (
            id TEXT PRIMARY KEY,
            review_item_id TEXT NOT NULL REFERENCES active_review_items(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            plan_task_id TEXT NOT NULL DEFAULT '',
            mastery_evidence_id TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            score REAL NOT NULL,
            passed INTEGER NOT NULL,
            assistance_level TEXT NOT NULL DEFAULT 'none',
            evaluator_kind TEXT NOT NULL,
            estimated_task_minutes INTEGER NOT NULL,
            completed_task_minutes INTEGER NOT NULL DEFAULT 0,
            actual_elapsed_minutes REAL NOT NULL DEFAULT 0,
            outcome TEXT NOT NULL,
            reviewed_on TEXT NOT NULL,
            next_interval_days INTEGER,
            next_due_date TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(review_item_id, attempt_no)
        );
        CREATE TABLE IF NOT EXISTS active_replan_history (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES active_plans(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            trigger_name TEXT NOT NULL,
            reason TEXT NOT NULL,
            affected_from TEXT NOT NULL,
            before_snapshot TEXT NOT NULL,
            after_snapshot TEXT NOT NULL,
            moved_task_ids TEXT NOT NULL,
            unscheduled_minutes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    existing_task_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(active_plan_tasks)").fetchall()
    }
    for column, ddl in (
        ("task_type", "TEXT NOT NULL DEFAULT 'initial_learning'"),
        ("learning_phase", "TEXT NOT NULL DEFAULT 'integrated_initial'"),
        ("point_sequence", "INTEGER NOT NULL DEFAULT 0"),
        ("phase_part_index", "INTEGER NOT NULL DEFAULT 1"),
        ("phase_part_count", "INTEGER NOT NULL DEFAULT 1"),
        ("linked_review_item_id", "TEXT NOT NULL DEFAULT ''"),
        ("review_cycle", "INTEGER NOT NULL DEFAULT 0"),
        ("origin_task_id", "TEXT NOT NULL DEFAULT ''"),
        ("not_before_date", "TEXT"),
        ("trigger_reason", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in existing_task_columns:
            connection.execute(
                f"ALTER TABLE active_plan_tasks ADD COLUMN {column} {ddl}"
            )
    existing_persona_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(active_teacher_personas)"
        ).fetchall()
    }
    for column in (
        "visual_profile_id",
        "visual_profile_name",
        "visual_manifest_url",
    ):
        if column not in existing_persona_columns:
            connection.execute(
                f"ALTER TABLE active_teacher_personas ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
            )
    existing_review_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(active_review_items)").fetchall()
    }
    for column, ddl in (
        ("source_mastery_evidence_id", "TEXT NOT NULL DEFAULT ''"),
        ("interval_step", "INTEGER NOT NULL DEFAULT 0"),
        ("review_total_minutes", "INTEGER NOT NULL DEFAULT 0"),
        ("scheduled_task_id", "TEXT NOT NULL DEFAULT ''"),
        ("scheduled_date", "TEXT"),
        ("plan_id", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in existing_review_columns:
            connection.execute(
                f"ALTER TABLE active_review_items ADD COLUMN {column} {ddl}"
            )
    existing_review_attempt_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(active_review_attempts)").fetchall()
    }
    if "plan_task_id" not in existing_review_attempt_columns:
        connection.execute(
            "ALTER TABLE active_review_attempts ADD COLUMN plan_task_id TEXT NOT NULL DEFAULT ''"
        )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_active_plan_tasks_schedule
           ON active_plan_tasks(plan_id,status,day_date)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_active_plan_tasks_review
           ON active_plan_tasks(linked_review_item_id,status)"""
    )
    existing_replan_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(active_replan_history)").fetchall()
    }
    for column, ddl in (
        ("plan_id", "TEXT NOT NULL DEFAULT ''"),
        ("session_id", "TEXT NOT NULL DEFAULT ''"),
        ("trigger", "TEXT NOT NULL DEFAULT ''"),
        ("trigger_name", "TEXT NOT NULL DEFAULT ''"),
        ("reason", "TEXT NOT NULL DEFAULT ''"),
        ("affected_from", "TEXT NOT NULL DEFAULT ''"),
        ("before_state", "TEXT NOT NULL DEFAULT '[]'"),
        ("after_state", "TEXT NOT NULL DEFAULT '[]'"),
        ("before_snapshot", "TEXT NOT NULL DEFAULT '[]'"),
        ("after_snapshot", "TEXT NOT NULL DEFAULT '[]'"),
        ("moved_task_ids", "TEXT NOT NULL DEFAULT '[]'"),
        ("unscheduled_minutes", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column not in existing_replan_columns:
            connection.execute(
                f"ALTER TABLE active_replan_history ADD COLUMN {column} {ddl}"
            )
    connection.commit()


def _persona_usage_count(connection: sqlite3.Connection, persona_id: str) -> int:
    count = 0
    for row in connection.execute("SELECT payload FROM active_receipts").fetchall():
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if str(payload.get("teacher_persona_id") or "") == persona_id:
            count += 1
    return count


def _teacher_persona_item(
    row: sqlite3.Row,
    *,
    usage_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "prompt": str(row["prompt"]),
        "teaching_style": str(row["teaching_style"] or ""),
        "visual_profile_id": str(row["visual_profile_id"] or ""),
        "visual_profile_name": str(row["visual_profile_name"] or ""),
        "visual_manifest_url": str(row["visual_manifest_url"] or ""),
        "usage_count": usage_count,
        "is_default": bool(row["is_default"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _sync_teacher_persona_library(connection: sqlite3.Connection) -> None:
    """Seed the role library and migrate task-scoped persona text without losing it."""
    now = _now()
    if not connection.execute(
        "SELECT 1 FROM active_teacher_personas LIMIT 1"
    ).fetchone():
        connection.execute(
            """INSERT INTO active_teacher_personas
               (id,name,prompt,teaching_style,visual_profile_id,visual_profile_name,
                visual_manifest_url,is_default,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,1,?,?)""",
            (
                "teacher_persona_iris_default",
                "艾莉丝 · 主动陪练",
                (
                    "温柔、耐心但不敷衍。每次只推进一个最小目标，先摸清学习者基础，"
                    "允许随时打断；先用生活语言建立直觉，再逐步进入术语、练习和独立验收。"
                ),
                "主动提问、短讲解、逐步撤除脚手架",
                "teacher-02-blonde-fang-v9",
                TEACHER_VISUAL_PROFILES["teacher-02-blonde-fang-v9"]["name"],
                TEACHER_VISUAL_PROFILES["teacher-02-blonde-fang-v9"]["manifest_url"],
                now,
                now,
            ),
        )
    connection.execute(
        """UPDATE active_teacher_personas
           SET visual_profile_id=?,visual_profile_name=?,visual_manifest_url=?,updated_at=?
           WHERE id='teacher_persona_iris_default' AND visual_profile_id=''""",
        (
            "teacher-02-blonde-fang-v9",
            TEACHER_VISUAL_PROFILES["teacher-02-blonde-fang-v9"]["name"],
            TEACHER_VISUAL_PROFILES["teacher-02-blonde-fang-v9"]["manifest_url"],
            now,
        ),
    )
    migrated_index = 0
    for receipt_row in connection.execute(
        "SELECT workspace_id,payload FROM active_receipts ORDER BY updated_at"
    ).fetchall():
        try:
            payload = json.loads(receipt_row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        prompt = str(payload.get("teacher_persona") or "").strip()
        if not prompt:
            continue
        persona_row = connection.execute(
            "SELECT * FROM active_teacher_personas WHERE prompt=?", (prompt,)
        ).fetchone()
        if not persona_row:
            migrated_index += 1
            persona_id = _id("teacher_persona")
            persona_name = str(
                payload.get("teacher_persona_name")
                or f"已保存教师人设 {migrated_index}"
            )
            connection.execute(
                """INSERT INTO active_teacher_personas
                   (id,name,prompt,teaching_style,visual_profile_id,visual_profile_name,
                    visual_manifest_url,is_default,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,0,?,?)""",
                (
                    persona_id,
                    persona_name,
                    prompt[:2000],
                    str(payload.get("preferred_learning_style") or "")[:500],
                    "",
                    "",
                    "",
                    now,
                    now,
                ),
            )
        else:
            persona_id = str(persona_row["id"])
            persona_name = str(persona_row["name"])
        canonical_persona = connection.execute(
            "SELECT * FROM active_teacher_personas WHERE id=?", (persona_id,)
        ).fetchone()
        if (
            payload.get("teacher_persona_id") != persona_id
            or payload.get("teacher_persona_name") != persona_name
            or payload.get("teacher_visual_profile_id")
            != str(canonical_persona["visual_profile_id"] or "")
        ):
            _apply_persona_to_payload(payload, canonical_persona)
            connection.execute(
                "UPDATE active_receipts SET payload=? WHERE workspace_id=?",
                (
                    json.dumps(payload, ensure_ascii=False),
                    receipt_row["workspace_id"],
                ),
            )


@router.get("/teacher-personas")
def list_teacher_personas() -> dict[str, Any]:
    with _connect() as connection:
        _sync_teacher_persona_library(connection)
        connection.commit()
        rows = connection.execute(
            """SELECT * FROM active_teacher_personas
               ORDER BY is_default DESC,updated_at DESC,name"""
        ).fetchall()
        return {
            "items": [
                _teacher_persona_item(
                    row,
                    usage_count=_persona_usage_count(connection, str(row["id"])),
                )
                for row in rows
            ]
        }


@router.get("/teacher-visual-profiles")
def list_teacher_visual_profiles() -> dict[str, Any]:
    return {"items": list(TEACHER_VISUAL_PROFILES.values())}


def _resolved_visual_profile(profile_id: Any) -> dict[str, Any]:
    value = str(profile_id or "").strip()
    if not value:
        return {"id": "", "name": "", "manifest_url": ""}
    profile = TEACHER_VISUAL_PROFILES.get(value)
    if not profile:
        _error("TEACHER_VISUAL_PROFILE_NOT_FOUND", "所选教师外观不存在", 422)
    return profile


def _apply_persona_to_payload(payload: dict[str, Any], row: sqlite3.Row) -> None:
    payload.update(
        {
            "teacher_persona_id": str(row["id"]),
            "teacher_persona_name": str(row["name"]),
            "teacher_persona": str(row["prompt"]),
            "teacher_visual_profile_id": str(row["visual_profile_id"] or ""),
            "teacher_visual_profile_name": str(row["visual_profile_name"] or ""),
            "teacher_visual_manifest_url": str(row["visual_manifest_url"] or ""),
        }
    )


def _propagate_persona_to_references(
    connection: sqlite3.Connection,
    *,
    source_persona_id: str,
    target: sqlite3.Row,
) -> None:
    for table, key_column in (
        ("active_receipts", "workspace_id"),
        ("active_plans", "id"),
    ):
        payload_column = "payload" if table == "active_receipts" else "receipt"
        rows = connection.execute(
            f"SELECT {key_column},{payload_column} FROM {table}"
        ).fetchall()
        for item in rows:
            payload = json.loads(item[payload_column])
            if str(payload.get("teacher_persona_id") or "") != source_persona_id:
                continue
            _apply_persona_to_payload(payload, target)
            connection.execute(
                f"UPDATE {table} SET {payload_column}=? WHERE {key_column}=?",
                (
                    json.dumps(payload, ensure_ascii=False),
                    item[key_column],
                ),
            )
    for item in connection.execute(
        "SELECT id,state FROM active_teaching_sessions"
    ).fetchall():
        state = json.loads(item["state"])
        if str(state.get("teacher_persona_id") or "") != source_persona_id:
            continue
        _apply_persona_to_payload(state, target)
        connection.execute(
            "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), _now(), item["id"]),
        )


@router.post("/teacher-personas")
def create_teacher_persona(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    name = str(body.get("name") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    teaching_style = str(body.get("teaching_style") or "").strip()
    visual = _resolved_visual_profile(body.get("visual_profile_id"))
    if not name or not prompt:
        _error("TEACHER_PERSONA_REQUIRED", "请填写教师人设名称和人设说明", 422)
    if len(name) > 80 or len(prompt) > 2000 or len(teaching_style) > 500:
        _error("TEACHER_PERSONA_TOO_LONG", "教师人设内容过长", 422)
    persona_id = _id("teacher_persona")
    now = _now()
    with _connect() as connection:
        _sync_teacher_persona_library(connection)
        is_default = bool(body.get("is_default"))
        if is_default:
            connection.execute("UPDATE active_teacher_personas SET is_default=0")
        connection.execute(
            """INSERT INTO active_teacher_personas
               (id,name,prompt,teaching_style,visual_profile_id,visual_profile_name,
                visual_manifest_url,is_default,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                persona_id,
                name,
                prompt,
                teaching_style,
                visual["id"],
                visual["name"],
                visual["manifest_url"],
                int(is_default),
                now,
                now,
            ),
        )
        connection.commit()
        return _teacher_persona_item(
            connection.execute(
                "SELECT * FROM active_teacher_personas WHERE id=?", (persona_id,)
            ).fetchone()
        )


@router.patch("/teacher-personas/{persona_id}")
def update_teacher_persona(
    persona_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    with _connect() as connection:
        _sync_teacher_persona_library(connection)
        current = connection.execute(
            "SELECT * FROM active_teacher_personas WHERE id=?", (persona_id,)
        ).fetchone()
        if not current:
            _error("TEACHER_PERSONA_NOT_FOUND", "教师人设不存在", 404)
        name = str(body.get("name", current["name"]) or "").strip()
        prompt = str(body.get("prompt", current["prompt"]) or "").strip()
        teaching_style = str(
            body.get("teaching_style", current["teaching_style"]) or ""
        ).strip()
        visual = _resolved_visual_profile(
            body.get("visual_profile_id", current["visual_profile_id"])
        )
        if not name or not prompt:
            _error("TEACHER_PERSONA_REQUIRED", "请填写教师人设名称和人设说明", 422)
        if len(name) > 80 or len(prompt) > 2000 or len(teaching_style) > 500:
            _error("TEACHER_PERSONA_TOO_LONG", "教师人设内容过长", 422)
        is_default = bool(body.get("is_default", current["is_default"]))
        if is_default:
            connection.execute("UPDATE active_teacher_personas SET is_default=0")
        connection.execute(
            """UPDATE active_teacher_personas
               SET name=?,prompt=?,teaching_style=?,visual_profile_id=?,
                   visual_profile_name=?,visual_manifest_url=?,is_default=?,updated_at=?
               WHERE id=?""",
            (
                name,
                prompt,
                teaching_style,
                visual["id"],
                visual["name"],
                visual["manifest_url"],
                int(is_default),
                _now(),
                persona_id,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM active_teacher_personas WHERE id=?", (persona_id,)
        ).fetchone()
        _propagate_persona_to_references(
            connection,
            source_persona_id=persona_id,
            target=updated,
        )
        connection.commit()
        return _teacher_persona_item(
            updated,
            usage_count=_persona_usage_count(connection, persona_id),
        )


@router.delete("/teacher-personas/{persona_id}")
def delete_teacher_persona(persona_id: str) -> dict[str, Any]:
    with _connect() as connection:
        _sync_teacher_persona_library(connection)
        current = connection.execute(
            "SELECT * FROM active_teacher_personas WHERE id=?", (persona_id,)
        ).fetchone()
        if not current:
            _error("TEACHER_PERSONA_NOT_FOUND", "教师人设不存在", 404)
        if bool(current["is_default"]):
            _error("DEFAULT_TEACHER_PERSONA_REQUIRED", "默认教师不能删除", 409)
        replacement = connection.execute(
            """SELECT * FROM active_teacher_personas
               WHERE id<>? ORDER BY is_default DESC,updated_at DESC LIMIT 1""",
            (persona_id,),
        ).fetchone()
        if not replacement:
            _error("LAST_TEACHER_PERSONA_REQUIRED", "至少需要保留一个教师角色", 409)
        usage_count = _persona_usage_count(connection, persona_id)
        _propagate_persona_to_references(
            connection,
            source_persona_id=persona_id,
            target=replacement,
        )
        connection.execute(
            "DELETE FROM active_teacher_personas WHERE id=?", (persona_id,)
        )
        connection.commit()
        return {
            "deleted_id": persona_id,
            "reassigned_task_count": usage_count,
            "replacement": _teacher_persona_item(
                replacement,
                usage_count=_persona_usage_count(
                    connection, str(replacement["id"])
                ),
            ),
        }


def _error(code: str, message: str, status: int = 400, **details: Any) -> None:
    raise HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "details": details}},
    )


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._ -]+", "_", Path(value).name).strip(" .")
    return cleaned[:180] or "material"


def _split_markdown(text: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text))
    if not matches:
        matches = [re.match(r"", text)]
    parts: list[dict[str, Any]] = []
    paragraph_offsets = [match.start() for match in re.finditer(r"(?m)(?:^|\n)\s*\S", text)]
    for index, match in enumerate(matches):
        start = match.start() if match else 0
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if not content:
            continue
        heading = match.group(2).strip() if match and match.lastindex else ""
        level = len(match.group(1)) if match and match.lastindex else 1
        paragraph_start = 1 + sum(1 for offset in paragraph_offsets if offset < start)
        paragraph_end = max(paragraph_start, sum(1 for offset in paragraph_offsets if offset < end))
        parts.append(
            {
                "text": content,
                "heading": heading,
                "heading_level": level,
                "page_start": None,
                "page_end": None,
                "paragraph_start": paragraph_start,
                "paragraph_end": paragraph_end,
                "char_start": start,
                "char_end": end,
            }
        )
    return parts


def _split_plain(text: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"\S(?:.*?)(?=\n\s*\n|\Z)", text, flags=re.S))
    parts = []
    for index, match in enumerate(matches, 1):
        value = match.group(0).strip()
        if value:
            parts.append(
                {
                    "text": value,
                    "heading": value.splitlines()[0][:120],
                    "heading_level": 3,
                    "page_start": None,
                    "page_end": None,
                    "paragraph_start": index,
                    "paragraph_end": index,
                    "char_start": match.start(),
                    "char_end": match.end(),
                }
            )
    return parts


def _read_text_with_fallback(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    failures = []
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            failures.append(f"{encoding}:{exc.start}")
    raise ValueError("无法识别文本编码（已尝试 UTF-8、UTF-16、GB18030）")


def _validate_text_quality(text: str) -> None:
    if not text.strip():
        raise ValueError("文件中没有可解析的正文")
    if "\x00" in text:
        raise ValueError("文本包含 NUL 字节，文件内容与文本格式不匹配")
    sample = text[:200_000]
    control_count = sum(
        1
        for char in sample
        if ord(char) < 32 and char not in {"\n", "\r", "\t", "\f"}
    )
    if control_count / max(1, len(sample)) > 0.01:
        raise ValueError("文本控制字符比例异常，无法确认正文质量")
    if sample.count("\ufffd") / max(1, len(sample)) > 0.001:
        raise ValueError("文本包含过多解码替换字符，无法可靠解析")


def _validate_structured_text(text: str, suffix: str) -> None:
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 语法错误（第 {exc.lineno} 行第 {exc.colno} 列）") from exc
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml

            yaml.safe_load(text)
        except Exception as exc:
            raise ValueError(f"YAML 语法错误：{exc}") from exc
    elif suffix == ".xml":
        from xml.etree import ElementTree

        try:
            ElementTree.fromstring(text)
        except ElementTree.ParseError as exc:
            raise ValueError(f"XML 语法错误：{exc}") from exc
    elif suffix == ".csv":
        try:
            rows = list(csv.reader(io.StringIO(text)))
        except csv.Error as exc:
            raise ValueError(f"CSV 语法错误：{exc}") from exc
        if not rows or not any(any(cell.strip() for cell in row) for row in rows):
            raise ValueError("CSV 中没有可解析的数据")
    elif suffix == ".rtf" and not text.lstrip().startswith(r"{\rtf"):
        raise ValueError("RTF 文件缺少有效的 RTF 文档头")


def _parse_zip_xml_text(path: Path, kind: str) -> tuple[list[dict[str, Any]], int, str]:
    import zipfile
    from xml.etree import ElementTree

    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError(
                f"压缩文档条目过多（{len(infos)} > {MAX_ARCHIVE_ENTRIES}）"
            )
        uncompressed = sum(max(0, int(info.file_size)) for info in infos)
        if uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError(
                "压缩文档解压后超过安全预算 "
                f"（{uncompressed} > {MAX_ARCHIVE_UNCOMPRESSED_BYTES} 字节）"
            )
        names = archive.namelist()
        if kind == "pptx":
            selected = sorted(
                (name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                key=lambda value: int(re.search(r"\d+", Path(value).stem).group()),
            )
        elif kind == "xlsx":
            selected = sorted(
                name for name in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
        else:
            selected = sorted(
                name for name in names
                if name.casefold().endswith((".xhtml", ".html", ".htm"))
            )
        parts: list[dict[str, Any]] = []
        char_cursor = 0
        paragraph = 1
        for page_number, name in enumerate(selected, 1):
            raw = archive.read(name)
            if kind == "epub":
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(raw, "html.parser")
                values = [
                    item.get_text(" ", strip=True)
                    for item in soup.select("h1,h2,h3,h4,h5,h6,p,li")
                    if item.get_text(" ", strip=True)
                ]
            else:
                root = ElementTree.fromstring(raw)
                values = [
                    str(node.text).strip()
                    for node in root.iter()
                    if node.text and str(node.text).strip()
                ]
            text = "\n".join(values).strip()
            if not text:
                continue
            parts.append(
                {
                    "text": text,
                    "heading": values[0][:120] if values else Path(name).stem,
                    "heading_level": 2,
                    "page_start": page_number,
                    "page_end": page_number,
                    "paragraph_start": paragraph,
                    "paragraph_end": paragraph + max(0, len(values) - 1),
                    "char_start": char_cursor,
                    "char_end": char_cursor + len(text),
                }
            )
            char_cursor += len(text) + 2
            paragraph += max(1, len(values))
        return parts, len(selected), f"stdlib-{kind}-xml"


def _parse_file(path: Path) -> tuple[list[dict[str, Any]], int, str]:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的文件格式：{suffix or '无扩展名'}")
    text_suffixes = {
        ".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".xml", ".rtf",
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".h",
        ".go", ".rs", ".sql", ".sh", ".ps1",
    }
    if suffix in text_suffixes:
        text, encoding = _read_text_with_fallback(path)
        _validate_text_quality(text)
        _validate_structured_text(text, suffix)
        parts = _split_markdown(text) if suffix in {".md", ".markdown"} else _split_plain(text)
        return parts, 1, f"{encoding}-{suffix.lstrip('.')}"
    if suffix in {".html", ".htm"}:
        from bs4 import BeautifulSoup

        text, encoding = _read_text_with_fallback(path)
        soup = BeautifulSoup(text, "html.parser")
        text = "\n\n".join(item.get_text(" ", strip=True) for item in soup.select("h1,h2,h3,h4,h5,h6,p,li"))
        return _split_plain(text), 1, f"beautifulsoup-{encoding}"
    if suffix == ".docx":
        from docx import Document

        paragraphs = [paragraph.text.strip() for paragraph in Document(path).paragraphs if paragraph.text.strip()]
        text = "\n\n".join(paragraphs)
        return _split_plain(text), 1, "python-docx"
    if suffix == ".pdf":
        import fitz

        document = fitz.open(path)
        parts: list[dict[str, Any]] = []
        char_cursor = 0
        paragraph_cursor = 1
        for page_number, page in enumerate(document, 1):
            blocks = [item[4].strip() for item in page.get_text("blocks") if str(item[4]).strip()]
            for block in blocks:
                parts.append(
                    {
                        "text": block,
                        "heading": block.splitlines()[0][:120],
                        "heading_level": 3,
                        "page_start": page_number,
                        "page_end": page_number,
                        "paragraph_start": paragraph_cursor,
                        "paragraph_end": paragraph_cursor,
                        "char_start": char_cursor,
                        "char_end": char_cursor + len(block),
                    }
                )
                char_cursor += len(block) + 2
                paragraph_cursor += 1
        return parts, len(document), "pymupdf"
    if suffix in {".pptx", ".xlsx", ".epub"}:
        return _parse_zip_xml_text(path, suffix.lstrip("."))
    raise ValueError(f"不支持的文件格式：{suffix}")


def _evidence(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "material_id": row["material_id"],
        "chunk_id": row["id"],
        "source_file": row["original_filename"] if "original_filename" in keys else None,
        "heading": row["heading"] if "heading" in keys else "",
        "page_start": row["page_start"],
        "page_end": row["page_end"],
        "paragraph_start": row["paragraph_start"],
        "paragraph_end": row["paragraph_end"],
        "char_start": row["char_start"],
        "char_end": row["char_end"],
        "content_hash": row["content_hash"],
        "quote": row["text"][:360],
    }


def _public_material(connection: sqlite3.Connection, material_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM active_materials WHERE id=?", (material_id,)).fetchone()
    if not row:
        _error("MATERIAL_NOT_FOUND", "学习资料不存在", 404)
    output = dict(row)
    output["chunks"] = [
        dict(item)
        for item in connection.execute(
            "SELECT * FROM active_chunks WHERE material_id=? ORDER BY ordinal", (material_id,)
        ).fetchall()
    ]
    output["chunk_count"] = len(output["chunks"])
    return output


@router.post("/workspaces/{workspace_id}/materials")
async def upload_material(workspace_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    filename = _safe_filename(file.filename or "material")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        _error(
            "UNSUPPORTED_FILE_TYPE",
            f"不支持的文件格式：{suffix or '无扩展名'}；支持 {', '.join(sorted(SUPPORTED_SUFFIXES))}",
            415,
        )
    payload = await file.read()
    if len(payload) > MAX_MATERIAL_BYTES:
        _error(
            "MATERIAL_TOO_LARGE",
            f"资料超过单文件安全上限 {MAX_MATERIAL_BYTES // (1024 * 1024)} MiB",
            413,
            size_bytes=len(payload),
            max_size_bytes=MAX_MATERIAL_BYTES,
        )
    sha256 = _hash_bytes(payload)
    with _connect() as connection:
        duplicate = connection.execute(
            "SELECT id FROM active_materials WHERE workspace_id=? AND sha256=?",
            (workspace_id, sha256),
        ).fetchone()
        if duplicate:
            result = _public_material(connection, duplicate["id"])
            if result["status"] != "ready":
                _error(
                    "MATERIAL_PARSE_FAILED",
                    f"相同资料此前解析失败：{result['parse_error']}",
                    422,
                    material_id=result["id"],
                    deduplicated=True,
                )
            result["deduplicated"] = True
            return result
        material_id = _id("material")
        target_dir = UPLOAD_ROOT / re.sub(r"[^0-9A-Za-z._-]", "_", workspace_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{sha256[:16]}-{filename}"
        target.write_bytes(payload)
        try:
            parts, page_count, parser_name = _parse_file(target)
            if not parts:
                raise ValueError("文件中没有可解析的正文")
        except Exception as exc:
            connection.execute(
                """INSERT INTO active_materials
                   (id,workspace_id,original_filename,media_type,size_bytes,sha256,uploaded_at,stored_path,status,parse_error,page_count,parser_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    material_id,
                    workspace_id,
                    filename,
                    file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    len(payload),
                    sha256,
                    _now(),
                    str(target),
                    "failed",
                    str(exc),
                    0,
                    "failed",
                ),
            )
            connection.commit()
            _error("MATERIAL_PARSE_FAILED", f"资料解析失败：{exc}", 422, material_id=material_id)
        connection.execute(
            """INSERT INTO active_materials
               (id,workspace_id,original_filename,media_type,size_bytes,sha256,uploaded_at,stored_path,status,parse_error,page_count,parser_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                material_id,
                workspace_id,
                filename,
                file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                len(payload),
                sha256,
                _now(),
                str(target),
                "ready",
                "",
                page_count,
                parser_name,
            ),
        )
        for ordinal, part in enumerate(parts, 1):
            text = part["text"].strip()
            connection.execute(
                """INSERT INTO active_chunks
                   (id,workspace_id,material_id,ordinal,page_start,page_end,paragraph_start,paragraph_end,char_start,char_end,heading,content_hash,text)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _id("chunk"),
                    workspace_id,
                    material_id,
                    ordinal,
                    part.get("page_start"),
                    part.get("page_end"),
                    int(part.get("paragraph_start") or ordinal),
                    int(part.get("paragraph_end") or ordinal),
                    int(part.get("char_start") or 0),
                    int(part.get("char_end") or len(text)),
                    str(part.get("heading") or "")[:300],
                    _hash_text(text),
                    text,
                ),
            )
        connection.commit()
        result = _public_material(connection, material_id)
        result["deduplicated"] = False
        return result


@router.get("/workspaces/{workspace_id}/materials")
def list_materials(workspace_id: str) -> list[dict[str, Any]]:
    with _connect() as connection:
        return [
            _public_material(connection, row["id"])
            for row in connection.execute(
                "SELECT id FROM active_materials WHERE workspace_id=? ORDER BY uploaded_at", (workspace_id,)
            ).fetchall()
        ]


def _validate_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    required = (
        "learning_goal",
        "current_level",
        "deadline",
        "study_weekdays",
        "preferred_learning_style",
        "material_ids",
    )
    missing = [field for field in required if payload.get(field) in (None, "", [])]
    if missing:
        _error("RECEIPT_FIELDS_REQUIRED", "学习回执缺少必填字段", 422, missing=missing)
    try:
        deadline = date.fromisoformat(str(payload["deadline"]))
    except ValueError:
        _error("INVALID_DEADLINE", "截止日期必须是 YYYY-MM-DD", 422)
    if deadline < date.today():
        _error("DEADLINE_IN_PAST", "截止日期不能早于今天", 422)
    try:
        start_date = date.fromisoformat(str(payload.get("start_date") or date.today().isoformat()))
    except ValueError:
        _error("INVALID_START_DATE", "开始日期必须是 YYYY-MM-DD", 422)
    if start_date > deadline:
        _error("INVALID_DATE_RANGE", "开始日期不能晚于截止日期", 422)
    allocation_mode = (
        "system" if str(payload.get("allocation_mode") or "").casefold() == "system"
        else "manual"
    )
    minutes = int(payload.get("daily_task_minutes") or 30)
    if allocation_mode == "system" and not 10 <= minutes <= 720:
        minutes = 30
    elif not 10 <= minutes <= 720:
        _error("INVALID_DAILY_TASK_MINUTES", "每日任务分钟预算必须在 10 到 720 之间", 422)
    weekdays = sorted({int(item) for item in payload["study_weekdays"]})
    if not weekdays or any(item < 0 or item > 6 for item in weekdays):
        _error("INVALID_STUDY_WEEKDAYS", "每周可学习日期必须使用 0 到 6", 422)
    fixed_rest_weekdays = sorted({int(item) for item in payload.get("fixed_rest_weekdays", [])})
    if any(item < 0 or item > 6 for item in fixed_rest_weekdays):
        _error("INVALID_REST_WEEKDAYS", "固定休息星期必须使用 0 到 6", 422)
    if set(fixed_rest_weekdays) & set(weekdays):
        _error("WEEKDAY_AVAILABILITY_CONFLICT", "同一星期不能同时设为学习日和固定休息日", 422)
    unavailable_dates = sorted({str(item) for item in payload.get("unavailable_dates", [])})
    for item in unavailable_dates:
        try:
            unavailable_date = date.fromisoformat(item)
        except ValueError:
            _error("INVALID_UNAVAILABLE_DATE", "额外不可学习日期必须是 YYYY-MM-DD", 422, value=item)
        if unavailable_date < start_date or unavailable_date > deadline:
            _error("UNAVAILABLE_DATE_OUTSIDE_RANGE", "额外不可学习日期必须位于学习周期内", 422, value=item)
    special_date_minutes: dict[str, int] = {}
    for raw_day, raw_minutes in dict(payload.get("special_date_minutes") or {}).items():
        try:
            special_day = date.fromisoformat(str(raw_day))
        except ValueError:
            _error("INVALID_SPECIAL_DATE", "特殊日期必须是 YYYY-MM-DD", 422, value=raw_day)
        if special_day < start_date or special_day > deadline:
            _error("SPECIAL_DATE_OUTSIDE_RANGE", "特殊日期必须位于学习周期内", 422, value=raw_day)
        special_minutes = int(raw_minutes)
        if not 0 <= special_minutes <= 720:
            _error("INVALID_SPECIAL_DATE_MINUTES", "特殊日期分钟数必须在 0 到 720 之间", 422)
        special_date_minutes[special_day.isoformat()] = special_minutes
    raw_period_ratio = payload.get("initial_learning_period_ratio")
    initial_learning_period_ratio: float | None = None
    if raw_period_ratio is not None:
        try:
            initial_learning_period_ratio = float(raw_period_ratio)
        except (TypeError, ValueError):
            _error("INVALID_LEARNING_PERIOD_RATIO", "首学周期比例必须是 0 到 1 之间的数字", 422)
        if not 0.1 <= initial_learning_period_ratio <= 0.9:
            _error("INVALID_LEARNING_PERIOD_RATIO", "首学周期比例必须在 0.1 到 0.9 之间", 422)
    review_rounds = int(payload.get("review_rounds") or 2)
    if review_rounds not in {2, 3, 4}:
        _error("INVALID_REVIEW_ROUNDS", "完整复习轮数只能选择 2、3 或 4", 422)
    return {
        **payload,
        "start_date": start_date.isoformat(),
        "deadline": deadline.isoformat(),
        "daily_task_minutes": minutes,
        "allocation_mode": allocation_mode,
        "study_weekdays": weekdays,
        "fixed_rest_weekdays": fixed_rest_weekdays or sorted(set(range(7)) - set(weekdays)),
        "unavailable_dates": unavailable_dates,
        "special_date_minutes": special_date_minutes,
        "initial_learning_period_ratio": initial_learning_period_ratio,
        "review_rounds": review_rounds,
        "current_foundation": str(payload.get("current_foundation") or payload["current_level"]),
        "target_level": str(payload.get("target_level") or "complete_mastery"),
        "notes": str(payload.get("notes") or ""),
        "teacher_persona_id": str(payload.get("teacher_persona_id") or "")[:120],
        "teacher_persona_name": str(payload.get("teacher_persona_name") or "")[:80],
        "teacher_persona": str(payload.get("teacher_persona") or "")[:2000],
        "teacher_visual_profile_id": str(
            payload.get("teacher_visual_profile_id") or ""
        )[:120],
        "teacher_visual_profile_name": str(
            payload.get("teacher_visual_profile_name") or ""
        )[:120],
        "teacher_visual_manifest_url": str(
            payload.get("teacher_visual_manifest_url") or ""
        )[:500],
        "learner_interests": str(payload.get("learner_interests") or "")[:1000],
        "allow_schedule_suggestions": bool(payload.get("allow_schedule_suggestions", True)),
        "material_ids": list(dict.fromkeys(str(item) for item in payload["material_ids"])),
        "updated_at": _now(),
    }


@router.put("/workspaces/{workspace_id}/receipt")
def save_receipt(workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
    receipt = _validate_receipt(body)
    with _connect() as connection:
        _sync_teacher_persona_library(connection)
        persona_id = str(receipt.get("teacher_persona_id") or "")
        persona_row = (
            connection.execute(
                "SELECT * FROM active_teacher_personas WHERE id=?", (persona_id,)
            ).fetchone()
            if persona_id
            else None
        )
        if persona_id and not persona_row:
            _error("TEACHER_PERSONA_NOT_FOUND", "所选教师人设不存在，请重新选择", 422)
        if not persona_row and receipt.get("teacher_persona"):
            persona_row = connection.execute(
                "SELECT * FROM active_teacher_personas WHERE prompt=?",
                (receipt["teacher_persona"],),
            ).fetchone()
        if persona_row:
            _apply_persona_to_payload(receipt, persona_row)
        known = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM active_materials WHERE workspace_id=? AND status='ready'", (workspace_id,)
            ).fetchall()
        }
        missing = sorted(set(receipt["material_ids"]) - known)
        if missing:
            _error("RECEIPT_MATERIAL_NOT_FOUND", "回执引用了不存在或解析失败的资料", 422, material_ids=missing)
        if receipt["allocation_mode"] == "system":
            study_dates = []
            cursor = date.fromisoformat(receipt["start_date"])
            deadline = date.fromisoformat(receipt["deadline"])
            unavailable = set(receipt["unavailable_dates"])
            while cursor <= deadline:
                if cursor.weekday() in set(receipt["study_weekdays"]) and cursor.isoformat() not in unavailable:
                    study_dates.append(cursor)
                cursor += timedelta(days=1)
            if not study_dates:
                _error("NO_STUDY_DATES", "截止日期前没有可用学习日", 422)
            placeholders = ",".join("?" for _ in receipt["material_ids"])
            chunk_count = int(connection.execute(
                f"""SELECT COUNT(*) FROM active_chunks
                    WHERE workspace_id=? AND material_id IN ({placeholders})""",
                [workspace_id, *receipt["material_ids"]],
            ).fetchone()[0])
            exact_estimates: list[dict[str, Any]] = []
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_point_details'"
            ).fetchone():
                detail_rows = connection.execute(
                    f"""SELECT d.mastery_estimate
                        FROM active_point_details d
                        JOIN active_knowledge_points p ON p.id=d.knowledge_point_id
                        WHERE p.workspace_id=? AND p.material_id IN ({placeholders})""",
                    [workspace_id, *receipt["material_ids"]],
                ).fetchall()
                for row in detail_rows:
                    try:
                        estimate = json.loads(row["mastery_estimate"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if all(
                        key in estimate
                        for key in (
                            "understand_minutes",
                            "recall_minutes",
                            "guided_practice_minutes",
                            "independent_application_minutes",
                            "review_minutes",
                        )
                    ):
                        exact_estimates.append(estimate)
            if exact_estimates:
                estimated_initial = sum(
                    int(estimate[key])
                    for estimate in exact_estimates
                    for key in (
                        "understand_minutes",
                        "recall_minutes",
                        "guided_practice_minutes",
                        "independent_application_minutes",
                    )
                )
                estimated_review_round = sum(
                    int(estimate["review_minutes"]) for estimate in exact_estimates
                )
                estimation_source = "existing_knowledge_map"
            else:
                estimated_initial = max(90, chunk_count * 8 + 120)
                estimated_review_round = max(
                    30, math.ceil(estimated_initial * 0.12 / 5) * 5
                )
                estimation_source = "material_structure_estimate"
            estimated_total = (
                estimated_initial
                + estimated_review_round * int(receipt.get("review_rounds") or 2)
            )
            recommended = max(
                20,
                math.ceil(estimated_total / len(study_dates) / 5) * 5,
            )
            receipt["daily_task_minutes"] = recommended
            receipt["allocation"] = {
                "mode": "system",
                "estimated_total_minutes": estimated_total,
                "initial_learning_minutes": estimated_initial,
                "one_review_round_minutes": estimated_review_round,
                "review_rounds": int(receipt.get("review_rounds") or 2),
                "buffer_rate": 0,
                "available_study_days": len(study_dates),
                "recommended_minutes_per_day": recommended,
                "estimation_source": estimation_source,
                "reason": "首学与所选完整复习轮次合并后，平均分配到全部可学习日。",
            }
        connection.execute(
            """INSERT INTO active_receipts(workspace_id,payload,updated_at) VALUES (?,?,?)
               ON CONFLICT(workspace_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
            (workspace_id, json.dumps(receipt, ensure_ascii=False), receipt["updated_at"]),
        )
        connection.commit()
    return receipt


@router.get("/workspaces/{workspace_id}/receipt")
def get_receipt(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if not row:
            _error("RECEIPT_NOT_FOUND", "学习回执尚未填写", 404)
        return json.loads(row["payload"])


def _extract_field(text: str, labels: Iterable[str]) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?i)(?:^|[。\n])\s*(?:{label_pattern})\s*[：:]\s*([^。\n]+)",
        text,
    )
    return match.group(1).strip() if match else ""


def _difficulty_minutes(difficulty: str, text: str) -> int:
    explicit = _extract_field(text, ("预计任务分钟", "estimated_task_minutes"))
    if explicit:
        number = re.search(r"\d+", explicit)
        if number:
            return max(10, min(240, int(number.group())))
    value = difficulty.casefold()
    if "hard" in value or "困难" in value:
        return 85
    if "easy" in value or "简单" in value:
        return 40
    return 60


def _cycle_nodes(edges: list[tuple[str, str]]) -> set[str]:
    outgoing: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for source, target in edges:
        outgoing[source].add(target)
        nodes.update((source, target))
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: set[str] = set()
    stack: list[str] = []

    def walk(node: str) -> None:
        if node in visiting:
            cycles.add(node)
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for target in outgoing[node]:
            if target in visiting:
                cycles.update(stack[stack.index(target) :])
            else:
                walk(target)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in nodes:
        walk(node)
    return cycles


@router.post("/workspaces/{workspace_id}/knowledge-graph/generate")
def generate_knowledge_graph(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if not receipt_row:
            _error("RECEIPT_REQUIRED", "请先填写学习回执", 409)
        receipt = json.loads(receipt_row["payload"])
        placeholders = ",".join("?" for _ in receipt["material_ids"])
        chunks = connection.execute(
            f"""SELECT * FROM active_chunks
                WHERE workspace_id=? AND material_id IN ({placeholders})
                ORDER BY material_id, ordinal""",
            (workspace_id, *receipt["material_ids"]),
        ).fetchall()
        if not chunks:
            _error("KNOWLEDGE_SOURCE_REQUIRED", "所选资料没有可追溯正文", 409)
        old_plans = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM active_plans WHERE workspace_id=?", (workspace_id,)
            ).fetchall()
        ]
        for plan_id in old_plans:
            session_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM active_teaching_sessions WHERE plan_id=?", (plan_id,)
                ).fetchall()
            ]
            for session_id in session_ids:
                connection.execute(
                    "DELETE FROM active_replan_history WHERE session_id=?", (session_id,)
                )
                connection.execute(
                    "DELETE FROM active_task_time_events WHERE session_id=?", (session_id,)
                )
                connection.execute(
                    "DELETE FROM active_mastery_evidence WHERE session_id=?", (session_id,)
                )
                connection.execute(
                    "DELETE FROM active_teaching_messages WHERE session_id=?", (session_id,)
                )
            connection.execute("DELETE FROM active_teaching_sessions WHERE plan_id=?", (plan_id,))
            connection.execute("DELETE FROM active_plan_tasks WHERE plan_id=?", (plan_id,))
        connection.execute("DELETE FROM active_plans WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_knowledge_conflicts WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_dependencies WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_knowledge_points WHERE workspace_id=?", (workspace_id,))
        created: list[dict[str, Any]] = []
        aliases: dict[str, str] = {}
        duplicate_count = 0
        candidate_dependencies: list[tuple[str, str, dict[str, Any]]] = []
        for chunk in chunks:
            raw_name = re.sub(r"^\s*\d+[.)、]\s*", "", str(chunk["heading"] or "")).strip()
            if not raw_name:
                raw_name = chunk["text"].splitlines()[0].lstrip("# ").strip()
            if not raw_name:
                continue
            normalized = _normalise(raw_name)
            if not normalized:
                continue
            difficulty = _extract_field(chunk["text"], ("难度", "训练难度", "difficulty")) or "medium"
            explanation = _extract_field(chunk["text"], ("知识点", "解释", "explanation"))
            if not explanation:
                explanation = re.sub(r"(?m)^#{1,6}\s+.*$", "", chunk["text"]).strip()[:600]
            if not explanation:
                explanation = f"依据资料章节“{raw_name}”进行讲解与练习。"
            acceptance = _extract_field(chunk["text"], ("验收", "验收方式", "acceptance"))
            if not acceptance:
                acceptance = (
                    "能不用照抄资料解释该知识点，给出一个具体应用或例子，"
                    "并按资料中的事实或步骤完成一次可观察检验。"
                )
            evidence = _evidence(chunk)
            if normalized in aliases:
                duplicate_count += 1
                point_id = aliases[normalized]
                existing = connection.execute(
                    "SELECT explanation,source_evidence FROM active_knowledge_points WHERE id=?",
                    (point_id,),
                ).fetchone()
                existing_evidence = json.loads(existing["source_evidence"])
                connection.execute(
                    "UPDATE active_knowledge_points SET source_evidence=? WHERE id=?",
                    (json.dumps([*existing_evidence, evidence], ensure_ascii=False), point_id),
                )
                if _normalise(existing["explanation"]) != _normalise(explanation):
                    connection.execute(
                        """INSERT INTO active_knowledge_conflicts
                           (id,workspace_id,knowledge_point_id,topic,evidence_a,evidence_b,summary,status)
                           VALUES (?,?,?,?,?,?,?,'unresolved')""",
                        (
                            _id("conflict"),
                            workspace_id,
                            point_id,
                            raw_name[:240],
                            json.dumps(existing_evidence[0], ensure_ascii=False),
                            json.dumps(evidence, ensure_ascii=False),
                            "多个上传资料对同一知识点给出了不同表述，教学前需要指出并核对。",
                        ),
                    )
                continue
            point_id = f"kp_{hashlib.sha256((workspace_id + ':' + normalized).encode()).hexdigest()[:20]}"
            connection.execute(
                """INSERT INTO active_knowledge_points
                   (id,workspace_id,material_id,normalized_name,name,explanation,difficulty,estimated_task_minutes,mastery,acceptance_method,source_evidence,isolated,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    point_id,
                    workspace_id,
                    chunk["material_id"],
                    normalized,
                    raw_name[:240],
                    explanation,
                    difficulty,
                    _difficulty_minutes(difficulty, chunk["text"]),
                    0,
                    acceptance,
                    json.dumps([evidence], ensure_ascii=False),
                    0,
                    _now(),
                ),
            )
            aliases[normalized] = point_id
            synonyms = _extract_field(chunk["text"], ("同义词", "aliases", "synonyms"))
            for synonym in re.split(r"[,，、;；]", synonyms):
                key = _normalise(synonym)
                if key:
                    aliases[key] = point_id
            created.append(
                {
                    "id": point_id,
                    "name": raw_name[:240],
                    "explanation": explanation,
                    "difficulty": difficulty,
                    "estimated_task_minutes": _difficulty_minutes(difficulty, chunk["text"]),
                    "current_mastery": 0,
                    "acceptance_method": acceptance,
                    "material_id": chunk["material_id"],
                    "source_evidence": [evidence],
                    "prerequisites": [],
                    "successors": [],
                    "isolated": False,
                }
            )
            explicit = _extract_field(chunk["text"], ("前置", "前置知识", "prerequisites"))
            for item in re.split(r"[,，、;；]", explicit):
                key = _normalise(item)
                if key:
                    candidate_dependencies.append((key, point_id, evidence))
        if not created:
            _error("KNOWLEDGE_EXTRACTION_EMPTY", "资料正文无法生成有来源的知识点", 422)
        id_set = {item["id"] for item in created}
        name_by_id = {item["id"]: item["name"] for item in created}
        edges: list[tuple[str, str, dict[str, Any]]] = []
        for source_key, target, evidence in candidate_dependencies:
            source = source_key if source_key in id_set else aliases.get(source_key)
            if source and source != target:
                edges.append((source, target, evidence))
        cycles = _cycle_nodes([(source, target) for source, target, _ in edges])
        if cycles:
            connection.rollback()
            _error(
                "KNOWLEDGE_DEPENDENCY_CYCLE",
                "检测到循环依赖，知识图谱未保存",
                422,
                nodes=sorted(name_by_id.get(item, item) for item in cycles),
            )
        unique_edges: set[tuple[str, str]] = set()
        for source, target, evidence in edges:
            if (source, target) in unique_edges:
                continue
            unique_edges.add((source, target))
            connection.execute(
                """INSERT INTO active_dependencies(id,workspace_id,prerequisite_id,dependent_id,evidence)
                   VALUES (?,?,?,?,?)""",
                (_id("dep"), workspace_id, source, target, json.dumps(evidence, ensure_ascii=False)),
            )
        connected = {item for edge in unique_edges for item in edge}
        for point in created:
            point["isolated"] = point["id"] not in connected and len(created) > 1
            connection.execute(
                "UPDATE active_knowledge_points SET isolated=? WHERE id=?",
                (int(point["isolated"]), point["id"]),
            )
        connection.commit()
        return _graph(connection, workspace_id) | {
            "quality": {
                "duplicate_count": duplicate_count,
                "synonym_merge_count": duplicate_count,
                "cycle_count": 0,
                "unsourced_blocked_count": 0,
                "isolated_count": sum(1 for item in created if item["isolated"]),
                "duration_outlier_count": sum(
                    1 for item in created if not 10 <= item["estimated_task_minutes"] <= 240
                ),
            }
        }


def _graph(connection: sqlite3.Connection, workspace_id: str) -> dict[str, Any]:
    point_rows = connection.execute(
        "SELECT * FROM active_knowledge_points WHERE workspace_id=? ORDER BY created_at,id", (workspace_id,)
    ).fetchall()
    edge_rows = connection.execute(
        "SELECT * FROM active_dependencies WHERE workspace_id=? ORDER BY id", (workspace_id,)
    ).fetchall()
    prerequisites: dict[str, list[str]] = defaultdict(list)
    successors: dict[str, list[str]] = defaultdict(list)
    edges = []
    for row in edge_rows:
        prerequisites[row["dependent_id"]].append(row["prerequisite_id"])
        successors[row["prerequisite_id"]].append(row["dependent_id"])
        edges.append(
            {
                "id": row["id"],
                "prerequisite_id": row["prerequisite_id"],
                "dependent_id": row["dependent_id"],
                "relation": "prerequisite",
                "source_evidence": json.loads(row["evidence"]),
            }
        )
    points = []
    for row in point_rows:
        points.append(
            {
                "id": row["id"],
                "name": row["name"],
                "explanation": row["explanation"],
                "source_evidence": json.loads(row["source_evidence"]),
                "prerequisites": prerequisites[row["id"]],
                "successors": successors[row["id"]],
                "difficulty": row["difficulty"],
                "estimated_task_minutes": row["estimated_task_minutes"],
                "current_mastery": row["mastery"],
                "acceptance_method": row["acceptance_method"],
                "material_id": row["material_id"],
                "isolated": bool(row["isolated"]),
            }
        )
    conflicts = [
        {
            **dict(row),
            "evidence_a": json.loads(row["evidence_a"]),
            "evidence_b": json.loads(row["evidence_b"]),
        }
        for row in connection.execute(
            "SELECT * FROM active_knowledge_conflicts WHERE workspace_id=? ORDER BY id",
            (workspace_id,),
        ).fetchall()
    ]
    return {
        "workspace_id": workspace_id,
        "knowledge_points": points,
        "edges": edges,
        "conflicts": conflicts,
    }


@router.get("/workspaces/{workspace_id}/knowledge-graph")
def get_knowledge_graph(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        result = _graph(connection, workspace_id)
        if not result["knowledge_points"]:
            _error("KNOWLEDGE_GRAPH_NOT_FOUND", "知识图谱尚未生成", 404)
        return result


def _topological_points(
    points: list[sqlite3.Row], dependencies: list[sqlite3.Row]
) -> list[sqlite3.Row]:
    by_id = {row["id"]: row for row in points}
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {row["id"]: 0 for row in points}
    for edge in dependencies:
        if edge["prerequisite_id"] in by_id and edge["dependent_id"] in by_id:
            outgoing[edge["prerequisite_id"]].append(edge["dependent_id"])
            indegree[edge["dependent_id"]] += 1
    def learning_order(row: sqlite3.Row) -> tuple[int, int, int, str]:
        difficulty = str(row["difficulty"] or "").casefold()
        difficulty_rank = (
            0
            if any(item in difficulty for item in ("easy", "简单", "基础"))
            else 2
            if any(item in difficulty for item in ("hard", "困难", "高级"))
            else 1
        )
        try:
            evidence = json.loads(row["source_evidence"])
        except (KeyError, TypeError, json.JSONDecodeError):
            evidence = []
        positions = [
            (
                int(item.get("paragraph") or item.get("paragraph_start") or 0),
                int(item.get("char_start") or 0),
            )
            for item in evidence
            if isinstance(item, dict)
        ]
        paragraph, char_start = min(positions, default=(0, 0))
        return difficulty_rank, paragraph, char_start, str(row["name"])
    ready = [
        node_id
        for node_id, value in indegree.items()
        if value == 0
    ]
    # Kahn's algorithm guarantees prerequisites always come first.  When
    # several nodes are simultaneously available, teach the easier one before
    # medium/hard peers, then preserve source order for deterministic results.
    ready.sort(key=lambda item: learning_order(by_id[item]))
    ordered: list[sqlite3.Row] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(by_id[node_id])
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=lambda item: learning_order(by_id[item]))
    if len(ordered) != len(points):
        _error("KNOWLEDGE_DEPENDENCY_CYCLE", "知识依赖存在循环，无法生成计划", 422)
    return ordered


def _study_dates(deadline: date, weekdays: list[int]) -> list[date]:
    result = []
    cursor = date.today()
    while cursor <= deadline:
        if cursor.weekday() in weekdays:
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


@router.post("/workspaces/{workspace_id}/plans/generate")
def generate_plan(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if not receipt_row:
            _error("RECEIPT_REQUIRED", "请先填写学习回执", 409)
        receipt = json.loads(receipt_row["payload"])
        material_ids = list(receipt.get("material_ids") or [])
        placeholders = ",".join("?" for _ in material_ids)
        points = connection.execute(
            f"""SELECT * FROM active_knowledge_points
                WHERE workspace_id=? AND material_id IN ({placeholders})""",
            [workspace_id, *material_ids],
        ).fetchall()
        if not points:
            _error("KNOWLEDGE_GRAPH_REQUIRED", "请先生成知识图谱", 409)
        dependencies = connection.execute(
            "SELECT * FROM active_dependencies WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
        ordered = _topological_points(points, dependencies)
        dates = _study_dates(date.fromisoformat(receipt["deadline"]), receipt["study_weekdays"])
        if not dates:
            _error("NO_STUDY_DATES", "截止日期前没有符合回执设置的学习日", 422)
        budget = int(receipt["daily_task_minutes"])
        plan_id = _id("active_plan")
        task_specs = []
        for point in ordered:
            minutes = int(point["estimated_task_minutes"])
            split_count = max(1, (minutes + min(120, budget) - 1) // min(120, budget))
            remaining = minutes
            for split_index in range(split_count):
                part_minutes = min(remaining, min(120, budget))
                remaining -= part_minutes
                suffix = f"（{split_index + 1}/{split_count}）" if split_count > 1 else ""
                task_specs.append((point, suffix, part_minutes))
        day_index = 0
        used = 0
        scheduled: list[dict[str, Any]] = []
        unscheduled = 0
        task_order = 0
        for point, suffix, minutes in task_specs:
            if used and used + minutes > budget:
                day_index += 1
                used = 0
                task_order = 0
            if day_index >= len(dates):
                unscheduled += minutes
                continue
            task_order += 1
            used += minutes
            evidence = json.loads(point["source_evidence"])
            task = {
                "id": _id("active_task"),
                "plan_id": plan_id,
                "workspace_id": workspace_id,
                "day_date": dates[day_index].isoformat(),
                "day_index": day_index,
                "task_order": task_order,
                "knowledge_point_id": point["id"],
                "title": f"{point['name']}{suffix}",
                "objective": f"围绕“{point['name']}”完成讲解、示例、理解检测和独立验收。",
                "source_evidence": evidence,
                "acceptance_condition": point["acceptance_method"],
                "estimated_task_minutes": minutes,
                "completed_task_minutes": 0,
                "actual_elapsed_minutes": 0.0,
                "status": "planned",
            }
            scheduled.append(task)
        total = sum(int(point["estimated_task_minutes"]) for point in ordered)
        warning = (
            f"截止日期前容量不足，仍有 {unscheduled} 任务分钟未排入；请延长截止日期、增加学习日或缩小范围。"
            if unscheduled
            else ""
        )
        connection.execute(
            """INSERT INTO active_plans
               (id,workspace_id,receipt,created_at,feasible,total_estimated_task_minutes,unscheduled_task_minutes,warning)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                plan_id,
                workspace_id,
                json.dumps(receipt, ensure_ascii=False),
                _now(),
                int(unscheduled == 0),
                total,
                unscheduled,
                warning,
            ),
        )
        for task in scheduled:
            connection.execute(
                """INSERT INTO active_plan_tasks
                   (id,plan_id,workspace_id,day_date,day_index,task_order,knowledge_point_id,title,objective,source_evidence,acceptance_condition,estimated_task_minutes,completed_task_minutes,actual_elapsed_minutes,status)
                   VALUES (:id,:plan_id,:workspace_id,:day_date,:day_index,:task_order,:knowledge_point_id,:title,:objective,:source_evidence,:acceptance_condition,:estimated_task_minutes,:completed_task_minutes,:actual_elapsed_minutes,:status)""",
                {**task, "source_evidence": json.dumps(task["source_evidence"], ensure_ascii=False)},
            )
        connection.commit()
        return _plan(connection, plan_id)


def _plan(connection: sqlite3.Connection, plan_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM active_plans WHERE id=?", (plan_id,)).fetchone()
    if not row:
        _error("ACTIVE_PLAN_NOT_FOUND", "主动教学计划不存在", 404)
    tasks = [
        dict(item)
        for item in connection.execute(
            "SELECT * FROM active_plan_tasks WHERE plan_id=? ORDER BY day_index,task_order", (plan_id,)
        ).fetchall()
    ]
    for task in tasks:
        task["source_evidence"] = json.loads(task["source_evidence"])
    days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        days[task["day_date"]].append(task)
    receipt = json.loads(row["receipt"])
    level = str(
        receipt.get("current_foundation")
        or receipt.get("current_level")
        or "beginner"
    ).casefold()
    elapsed_multipliers = {
        "zero_foundation": (1.3, 1.8),
        "beginner": (1.15, 1.6),
        "intermediate": (0.9, 1.3),
        "advanced": (0.75, 1.1),
    }.get(level, (1.0, 1.5))
    initial_minutes = sum(
        int(item["estimated_task_minutes"])
        for item in tasks
        if item["task_type"] == "initial_learning"
    )
    projected_review_minutes = max(
        0, int(row["total_estimated_task_minutes"]) - initial_minutes
    )
    total_mastery_minutes = int(row["total_estimated_task_minutes"])
    result = {
        **dict(row),
        "receipt": receipt,
        "daily_task_minutes_budget": receipt["daily_task_minutes"],
        "total_mastery_task_minutes": total_mastery_minutes,
        "initial_learning_task_minutes": initial_minutes,
        "projected_review_task_minutes": projected_review_minutes,
        "initial_learning_ratio": round(
            initial_minutes / max(1, total_mastery_minutes), 4
        ),
        "projected_review_ratio": round(
            projected_review_minutes / max(1, total_mastery_minutes), 4
        ),
        "expected_actual_elapsed_minutes_range": {
            "min": round(total_mastery_minutes * elapsed_multipliers[0]),
            "max": round(total_mastery_minutes * elapsed_multipliers[1]),
            "basis": (
                f"current_foundation={level}; "
                f"task_minutes×{elapsed_multipliers[0]:.2f}–{elapsed_multipliers[1]:.2f}"
            ),
        },
        "materialized_review_task_minutes": sum(
            int(item["estimated_task_minutes"])
            for item in tasks
            if item["task_type"] == "review"
        ),
        "review_scheduling_policy": (
            "复习分钟计入完全掌握容量预测；只有最终独立应用阶段通过后才进入复习队列，"
            "初始计划不为未学知识创建复习任务。"
        ),
        "tasks": tasks,
        "days": [
            {
                "date": day,
                "estimated_task_minutes": sum(item["estimated_task_minutes"] for item in items),
                "completed_task_minutes": sum(item["completed_task_minutes"] for item in items),
                "actual_elapsed_minutes": round(sum(item["actual_elapsed_minutes"] for item in items), 3),
                "tasks": items,
            }
            for day, items in sorted(days.items())
        ],
    }
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_plan_calendars'"
    ).fetchone():
        calendar_row = connection.execute(
            "SELECT calendar,available_capacity_minutes FROM active_plan_calendars WHERE plan_id=?",
            (plan_id,),
        ).fetchone()
        if calendar_row:
            result["calendar"] = json.loads(calendar_row["calendar"])
            for day in result["calendar"]:
                planned = int(day.get("planned_task_minutes") or 0) + int(
                    day.get("projected_review_task_minutes") or 0
                )
                day["expected_actual_elapsed_minutes_range"] = {
                    "min": round(planned * elapsed_multipliers[0]),
                    "max": round(planned * elapsed_multipliers[1]),
                }
            result["available_capacity_minutes"] = calendar_row["available_capacity_minutes"]
    calendar = result.get("calendar") or []
    result["initial_learning_period_ratio"] = receipt.get(
        "initial_learning_period_ratio"
    )
    result["review_rounds"] = int(receipt.get("review_rounds") or 2)
    result["one_review_round_task_minutes"] = int(
        (receipt.get("allocation") or {}).get("one_review_round_minutes") or 0
    )
    result["phase_boundary"] = {
        "initial_learning_dates": [
            day["date"]
            for day in calendar
            if day.get("study_phase") in {"initial_learning", "transition"}
        ],
        "review_dates": [
            day["date"]
            for day in calendar
            if day.get("study_phase") in {"review", "transition"}
        ],
        "transition_dates": [
            day["date"] for day in calendar if day.get("study_phase") == "transition"
        ],
    }
    return result


@router.get("/workspaces/{workspace_id}/plans/current")
def current_plan(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT id FROM active_plans WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        if not row:
            _error("ACTIVE_PLAN_NOT_FOUND", "主动教学计划尚未生成", 404)
        return _plan(connection, row["id"])


def _citations_for_point(connection: sqlite3.Connection, point_id: str) -> list[dict[str, Any]]:
    row = connection.execute(
        "SELECT source_evidence FROM active_knowledge_points WHERE id=?", (point_id,)
    ).fetchone()
    return json.loads(row["source_evidence"]) if row else []


def _task_rows(connection: sqlite3.Connection, plan_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """SELECT * FROM active_plan_tasks
           WHERE plan_id=? AND task_type='initial_learning'
           ORDER BY point_sequence,day_index,task_order,id""",
        (plan_id,),
    ).fetchall()


def _review_total_minutes_for_point(
    connection: sqlite3.Connection, point: sqlite3.Row
) -> int:
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_point_details'"
    ).fetchone():
        detail = connection.execute(
            "SELECT mastery_estimate FROM active_point_details WHERE knowledge_point_id=?",
            (point["id"],),
        ).fetchone()
        if detail:
            try:
                minutes = int(json.loads(detail["mastery_estimate"]).get("review_minutes") or 0)
            except (TypeError, ValueError, json.JSONDecodeError):
                minutes = 0
            if minutes > 0:
                return max(5, min(120, minutes))
    return max(5, min(60, round(int(point["estimated_task_minutes"]) * 0.2)))


def _review_occurrence_minutes(total_minutes: int, cycle: int) -> int:
    """Each configured cycle is one complete review of the knowledge point."""
    return max(3, int(total_minutes))


def _planned_review_due_date(
    connection: sqlite3.Connection,
    *,
    plan_id: str,
    knowledge_point_id: str,
    cycle: int,
    fallback: str,
) -> str:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_plan_calendars'"
    ).fetchone():
        return fallback
    row = connection.execute(
        "SELECT calendar FROM active_plan_calendars WHERE plan_id=?",
        (plan_id,),
    ).fetchone()
    if not row:
        return fallback
    candidates: list[str] = []
    for day in json.loads(row["calendar"]):
        if any(
            str(item.get("knowledge_point_id") or "") == knowledge_point_id
            and int(item.get("review_round") or 0) == cycle
            for item in day.get("projected_review_items") or []
        ):
            candidates.append(str(day["date"]))
    return min(candidates) if candidates else fallback


def _task_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "day_date": row["day_date"],
        "status": row["status"],
        "task_type": row["task_type"],
        "learning_phase": row["learning_phase"],
        "linked_review_item_id": row["linked_review_item_id"],
        "origin_task_id": row["origin_task_id"],
        "not_before_date": row["not_before_date"],
        "estimated_task_minutes": row["estimated_task_minutes"],
        "completed_task_minutes": row["completed_task_minutes"],
        "actual_elapsed_minutes": row["actual_elapsed_minutes"],
    }


def _refresh_plan_calendar(
    connection: sqlite3.Connection,
    plan_id: str,
    receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rebuild the persisted calendar from authoritative plan-task rows."""
    from .generic_knowledge import calendar_capacities

    calendar = [
        {
            **item,
            "planned_task_minutes": 0,
            "completed_task_minutes": 0,
            "actual_elapsed_minutes": 0.0,
            "tasks": [],
        }
        for item in calendar_capacities(receipt)
    ]
    by_date = {item["date"]: item for item in calendar}
    rows = connection.execute(
        """SELECT * FROM active_plan_tasks
           WHERE plan_id=? ORDER BY day_index,task_order,id""",
        (plan_id,),
    ).fetchall()
    for row in rows:
        if row["status"] == "unscheduled" or row["day_date"] not in by_date:
            continue
        day = by_date[row["day_date"]]
        payload = dict(row)
        payload["source_evidence"] = json.loads(payload["source_evidence"])
        day["tasks"].append(payload)
        day["planned_task_minutes"] += int(row["estimated_task_minutes"])
        day["completed_task_minutes"] += int(row["completed_task_minutes"])
        day["actual_elapsed_minutes"] = round(
            float(day["actual_elapsed_minutes"]) + float(row["actual_elapsed_minutes"]),
            3,
        )
    materialized_review_keys = {
        (str(row["knowledge_point_id"]), int(row["review_cycle"] or 0))
        for row in rows
        if row["task_type"] == "review"
    }
    projected_schedule = (
        (receipt.get("allocation") or {}).get("projected_review_schedule") or {}
    )
    for day_date, items in projected_schedule.items():
        if day_date not in by_date:
            continue
        day = by_date[day_date]
        for raw_item in items:
            item = dict(raw_item)
            key = (
                str(item.get("knowledge_point_id") or ""),
                int(item.get("review_round") or 0),
            )
            if key in materialized_review_keys:
                continue
            day.setdefault("projected_review_items", []).append(item)
            day["projected_review_task_minutes"] = int(
                day.get("projected_review_task_minutes") or 0
            ) + int(item.get("projected_review_task_minutes") or 0)
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_plan_calendars'"
    ).fetchone():
        connection.execute(
            """INSERT INTO active_plan_calendars
               (plan_id,workspace_id,calendar,available_capacity_minutes,created_at)
               VALUES (
                 ?,
                 (SELECT workspace_id FROM active_plans WHERE id=?),
                 ?,?,
                 ?
               )
               ON CONFLICT(plan_id) DO UPDATE SET
                 calendar=excluded.calendar,
                 available_capacity_minutes=excluded.available_capacity_minutes""",
            (
                plan_id,
                plan_id,
                json.dumps(calendar, ensure_ascii=False),
                sum(int(item["capacity_minutes"]) for item in calendar),
                _now(),
            ),
        )
    return calendar


def _reschedule_plan_work(
    connection: sqlite3.Connection,
    *,
    plan_id: str,
    receipt: dict[str, Any],
    affected_from: date,
) -> tuple[list[str], int, list[dict[str, Any]]]:
    """Pack unfinished main work and materialized auxiliary work into one calendar.

    Completed/attempted work is fixed and consumes capacity. Review work may never
    be placed before its due date. Main-route tasks keep their dependency order.
    """
    from .generic_knowledge import calendar_capacities

    capacities = calendar_capacities(receipt)
    eligible = [
        item for item in capacities if item["date"] >= affected_from.isoformat()
    ]
    remaining = {item["date"]: int(item["capacity_minutes"]) for item in eligible}
    day_index = {item["date"]: index for index, item in enumerate(capacities)}
    all_rows = connection.execute(
        """SELECT * FROM active_plan_tasks
           WHERE plan_id=? ORDER BY day_index,task_order,id""",
        (plan_id,),
    ).fetchall()
    movable_statuses = {"planned", "unscheduled"}
    pending = [row for row in all_rows if row["status"] in movable_statuses]
    fixed = [row for row in all_rows if row["status"] not in movable_statuses]
    order_by_day: dict[str, int] = defaultdict(int)
    for row in fixed:
        day_value = str(row["day_date"])
        if day_value not in remaining or row["status"] == "cancelled":
            continue
        remaining[day_value] = max(
            0, remaining[day_value] - int(row["estimated_task_minutes"])
        )
        order_by_day[day_value] = max(order_by_day[day_value], int(row["task_order"]))

    phase_rank = {
        "understand": 0,
        "recall": 1,
        "guided_practice": 2,
        "independent_application": 3,
    }
    main_queue = sorted(
        (row for row in pending if row["task_type"] == "initial_learning"),
        key=lambda row: (
            int(row["point_sequence"]),
            phase_rank.get(str(row["learning_phase"]), 99),
            int(row["phase_part_index"]),
            int(row["day_index"]),
            int(row["task_order"]),
            row["id"],
        ),
    )
    review_queue = sorted(
        (row for row in pending if row["task_type"] == "review"),
        key=lambda row: (
            str(row["not_before_date"] or row["day_date"]),
            int(row["review_cycle"]),
            row["id"],
        ),
    )
    remediation_queue = sorted(
        (row for row in pending if row["task_type"] == "remediation"),
        key=lambda row: (
            str(row["not_before_date"] or affected_from.isoformat()),
            int(row["day_index"]),
            int(row["task_order"]),
            row["id"],
        ),
    )
    other_queue = sorted(
        (
            row
            for row in pending
            if row["task_type"] not in {"initial_learning", "review", "remediation"}
        ),
        key=lambda row: (int(row["day_index"]), int(row["task_order"]), row["id"]),
    )
    previous = {row["id"]: (row["day_date"], row["status"]) for row in pending}
    assigned: set[str] = set()
    moved: list[str] = []

    def place(row: sqlite3.Row, day_value: str) -> None:
        order_by_day[day_value] += 1
        connection.execute(
            """UPDATE active_plan_tasks
               SET day_date=?,day_index=?,task_order=?,status='planned'
               WHERE id=?""",
            (
                day_value,
                day_index[day_value],
                order_by_day[day_value],
                row["id"],
            ),
        )
        remaining[day_value] -= int(row["estimated_task_minutes"])
        assigned.add(row["id"])
        if previous[row["id"]] != (day_value, "planned"):
            moved.append(row["id"])

    for day in eligible:
        day_value = day["date"]
        while remaining[day_value] > 0:
            due_reviews = [
                row
                for row in review_queue
                if row["id"] not in assigned
                and str(row["not_before_date"] or row["day_date"]) <= day_value
                and int(row["estimated_task_minutes"]) <= remaining[day_value]
            ]
            due_remediation = [
                row
                for row in remediation_queue
                if row["id"] not in assigned
                and str(row["not_before_date"] or affected_from.isoformat()) <= day_value
                and int(row["estimated_task_minutes"]) <= remaining[day_value]
            ]
            if due_remediation:
                place(due_remediation[0], day_value)
                continue
            if due_reviews:
                place(due_reviews[0], day_value)
                continue
            main = next((row for row in main_queue if row["id"] not in assigned), None)
            if main is not None:
                not_before = str(main["not_before_date"] or affected_from.isoformat())
                if (
                    not_before <= day_value
                    and int(main["estimated_task_minutes"]) <= remaining[day_value]
                ):
                    place(main, day_value)
                    continue
            other = next(
                (
                    row
                    for row in other_queue
                    if row["id"] not in assigned
                    and str(row["not_before_date"] or affected_from.isoformat()) <= day_value
                    and int(row["estimated_task_minutes"]) <= remaining[day_value]
                ),
                None,
            )
            if other is not None:
                place(other, day_value)
                continue
            break

    unscheduled = 0
    for row in pending:
        if row["id"] in assigned:
            continue
        connection.execute(
            """UPDATE active_plan_tasks
               SET status='unscheduled',day_index=999999,task_order=999999
               WHERE id=?""",
            (row["id"],),
        )
        if previous[row["id"]][1] != "unscheduled":
            moved.append(row["id"])
        unscheduled += int(row["estimated_task_minutes"])
    connection.execute(
        """UPDATE active_review_items
           SET scheduled_date=(
             SELECT CASE WHEN task.status='unscheduled' THEN NULL ELSE task.day_date END
             FROM active_plan_tasks AS task
             WHERE task.id=active_review_items.scheduled_task_id
           ),
           updated_at=?
           WHERE plan_id=? AND scheduled_task_id<>''""",
        (_now(), plan_id),
    )
    calendar = _refresh_plan_calendar(connection, plan_id, receipt)
    return list(dict.fromkeys(moved)), unscheduled, calendar


def _materialize_review_occurrence(
    connection: sqlite3.Connection,
    *,
    session: sqlite3.Row,
    review: sqlite3.Row,
    point: sqlite3.Row,
    due_date: str,
    cycle: int,
    trigger_reason: str,
) -> str:
    """Create one auditable plan task for one review occurrence."""
    due_date = _planned_review_due_date(
        connection,
        plan_id=session["plan_id"],
        knowledge_point_id=point["id"],
        cycle=cycle,
        fallback=due_date,
    )
    current_task_id = str(review["scheduled_task_id"] or "")
    if current_task_id:
        current = connection.execute(
            "SELECT id,status FROM active_plan_tasks WHERE id=?",
            (current_task_id,),
        ).fetchone()
        if current and current["status"] in {"planned", "in_progress", "unscheduled"}:
            return current["id"]
    total_minutes = int(review["review_total_minutes"] or 0)
    if total_minutes <= 0:
        total_minutes = _review_total_minutes_for_point(connection, point)
    occurrence_minutes = _review_occurrence_minutes(total_minutes, cycle)
    task_id = _id("active_review_task")
    connection.execute(
        """INSERT INTO active_plan_tasks
           (id,plan_id,workspace_id,day_date,day_index,task_order,
            knowledge_point_id,title,objective,source_evidence,
            acceptance_condition,estimated_task_minutes,
            completed_task_minutes,actual_elapsed_minutes,status,
            task_type,learning_phase,point_sequence,phase_part_index,phase_part_count,
            linked_review_item_id,review_cycle,origin_task_id,not_before_date,
            trigger_reason)
           VALUES (?,?,?,?,999999,999999,?,?,?,?,?,?,0,0,'unscheduled',
                   'review','review',0,1,1,?,?,?,?,?)""",
        (
            task_id,
            session["plan_id"],
            session["workspace_id"],
            due_date,
            point["id"],
            f"{point['name']} · 第 {cycle} 次间隔复习",
            (
                "在不依赖答案或提示的情况下完成延迟复测；"
                "只有独立通过才增加复习掌握证据。"
            ),
            point["source_evidence"],
            point["acceptance_method"],
            occurrence_minutes,
            review["id"],
            cycle,
            review["source_task_id"],
            due_date,
            trigger_reason,
        ),
    )
    connection.execute(
        """UPDATE active_review_items
           SET plan_id=?,scheduled_task_id=?,scheduled_date=NULL,
               estimated_task_minutes=?,review_total_minutes=?,updated_at=?
           WHERE id=?""",
        (
            session["plan_id"],
            task_id,
            occurrence_minutes,
            total_minutes,
            _now(),
            review["id"],
        ),
    )
    return task_id


def _estimate_remediation_task_minutes(
    task: sqlite3.Row, point: sqlite3.Row, failure_count: int
) -> int:
    difficulty = str(point["difficulty"] or "medium").casefold()
    factor = 0.25 if difficulty in {"easy", "简单"} else 0.35
    if difficulty in {"hard", "困难"}:
        factor = 0.45
    estimate = round(int(task["estimated_task_minutes"]) * factor) + min(
        20, max(0, failure_count) * 5
    )
    return max(10, min(60, estimate))


def _create_remediation_task(
    connection: sqlite3.Connection,
    *,
    session: sqlite3.Row,
    state: dict[str, Any],
    task: sqlite3.Row,
    point: sqlite3.Row,
    trigger_reason: str,
) -> dict[str, Any]:
    stack = state.get("remediation_stack") or []
    if stack:
        active_id = str(stack[-1].get("remediation_task_id") or "")
        if active_id:
            existing = connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=?", (active_id,)
            ).fetchone()
            if existing and existing["status"] in {
                "planned",
                "in_progress",
                "unscheduled",
            }:
                return dict(existing)
    failure_count = sum(
        1
        for item in state.get("unresolved_confusions") or []
        if item.get("knowledge_point_id") == point["id"]
    )
    minutes = _estimate_remediation_task_minutes(task, point, failure_count)
    today = date.today().isoformat()
    remediation_id = _id("active_remediation_task")
    connection.execute(
        """INSERT INTO active_plan_tasks
           (id,plan_id,workspace_id,day_date,day_index,task_order,
            knowledge_point_id,title,objective,source_evidence,
            acceptance_condition,estimated_task_minutes,
            completed_task_minutes,actual_elapsed_minutes,status,
            task_type,learning_phase,point_sequence,phase_part_index,phase_part_count,
            linked_review_item_id,review_cycle,origin_task_id,not_before_date,
            trigger_reason)
           VALUES (?,?,?,?,999999,999999,?,?,?,?,?,?,0,0,'planned',
                   'remediation','remediation',0,1,1,'',0,?,?,?)""",
        (
            remediation_id,
            session["plan_id"],
            session["workspace_id"],
            today,
            point["id"],
            f"{point['name']} · 临时深化教学",
            "定位当前缺失的前置知识，降低解释层级，完成更简单示例并重新检测理解。",
            point["source_evidence"],
            point["acceptance_method"],
            minutes,
            task["id"],
            today,
            trigger_reason,
        ),
    )
    plan = connection.execute(
        "SELECT * FROM active_plans WHERE id=?", (session["plan_id"],)
    ).fetchone()
    _replan_pending_in_transaction(
        connection,
        session=session,
        plan=plan,
        receipt=json.loads(plan["receipt"]),
        affected_from=date.today(),
        trigger_name="remediation_inserted",
        reason=f"知识点“{point['name']}”触发临时深化教学。",
        state=state,
    )
    connection.execute(
        """UPDATE active_plan_tasks
           SET status='in_progress' WHERE id=?""",
        (remediation_id,),
    )
    return dict(
        connection.execute(
            "SELECT * FROM active_plan_tasks WHERE id=?", (remediation_id,)
        ).fetchone()
    )


def _review_items(
    connection: sqlite3.Connection, session_id: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT review.*,point.name AS knowledge_point_name,
                  point.acceptance_method,point.source_evidence
           FROM active_review_items AS review
           JOIN active_knowledge_points AS point ON point.id=review.knowledge_point_id
           WHERE review.session_id=?
           ORDER BY CASE review.status WHEN 'in_progress' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                    review.due_date,review.created_at,review.id""",
        (session_id,),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["source_evidence"] = json.loads(item["source_evidence"])
        if item["last_passed"] is not None:
            item["last_passed"] = bool(item["last_passed"])
        items.append(item)
    return items


def _sync_pending_reviews(
    connection: sqlite3.Connection, session_id: str, state: dict[str, Any]
) -> None:
    state["pending_reviews"] = [
        {
            "id": item["id"],
            "knowledge_point_id": item["knowledge_point_id"],
            "knowledge_point_name": item["knowledge_point_name"],
            "source_task_id": item["source_task_id"],
            "due_date": item["due_date"],
            "scheduled_date": item["scheduled_date"],
            "scheduled_task_id": item["scheduled_task_id"],
            "interval_days": item["interval_days"],
            "review_total_minutes": item["review_total_minutes"],
            "estimated_task_minutes": item["estimated_task_minutes"],
            "status": item["status"],
            "attempt_count": item["attempt_count"],
            "success_streak": item["success_streak"],
            "last_score": item["last_score"],
            "last_passed": item["last_passed"],
        }
        for item in _review_items(connection, session_id)
        if item["status"] in {"pending", "in_progress"}
    ]


def _public_session(connection: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row:
        _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
    messages = []
    for message in connection.execute(
        "SELECT * FROM active_teaching_messages WHERE session_id=? ORDER BY created_at,id",
        (session_id,),
    ).fetchall():
        item = dict(message)
        item["citations"] = json.loads(item["citations"])
        messages.append(item)
    state = json.loads(row["state"])
    _sync_pending_reviews(connection, session_id, state)
    incomplete_initial = int(
        connection.execute(
            """SELECT COUNT(*) FROM active_plan_tasks
               WHERE plan_id=? AND task_type='initial_learning'
                 AND status<>'completed'""",
            (row["plan_id"],),
        ).fetchone()[0]
    )
    incomplete_initial_points = int(
        connection.execute(
            """SELECT COUNT(DISTINCT knowledge_point_id)
               FROM active_plan_tasks
               WHERE plan_id=? AND task_type='initial_learning'
                 AND status<>'completed'""",
            (row["plan_id"],),
        ).fetchone()[0]
    )
    state["formal_review_gate"] = {
        "unlocked": incomplete_initial == 0,
        "incomplete_initial_task_count": incomplete_initial,
        "incomplete_initial_knowledge_point_count": incomplete_initial_points,
        "daily_consolidation_counts_as_formal_review": False,
        "reason": (
            "all_initial_learning_completed"
            if incomplete_initial == 0
            else "complete_first_pass_before_formal_review"
        ),
    }
    return {
        "session_id": row["id"],
        "workspace_id": row["workspace_id"],
        "plan_id": row["plan_id"],
        "state": state,
        "messages": messages,
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "actual_elapsed_minutes": round(float(row["actual_elapsed_minutes"]), 3),
    }


def _record_message(
    connection: sqlite3.Connection,
    session_id: str,
    role: str,
    action: str,
    content: str,
    citations: list[dict[str, Any]],
) -> None:
    connection.execute(
        """INSERT INTO active_teaching_messages(id,session_id,role,action,content,citations,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (_id("teaching_message"), session_id, role, action, content, json.dumps(citations, ensure_ascii=False), _now()),
    )


async def _render_teacher_content(
    *,
    draft: str,
    action: str,
    citations: list[dict[str, Any]],
    provider_session_id: str | None,
    state: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Let a verified real provider improve delivery without controlling state or facts."""
    if not provider_session_id:
        return draft, {
            "mode": "system_guided",
            "provider": None,
            "fallback_used": False,
            "scope": "state_and_source_grounded_template",
        }
    try:
        provider_session = require_provider_session(provider_session_id)
    except ProviderError as exc:
        _error(exc.code, exc.message, exc.status, **exc.details)
    if provider_session.provider == "fake":
        _error(
            "REAL_PROVIDER_REQUIRED",
            "主动教学表达需要已验证的真实 Provider；Fake Provider 不能冒充 AI 教师。",
            428,
        )
    evidence = [
        {
            "source_file": item.get("source_file"),
            "page": item.get("page"),
            "paragraph": item.get("paragraph"),
            "quote": str(item.get("quote") or "")[:1200],
        }
        for item in citations
    ]
    prompt = {
        "fixed_action": action,
        "fixed_route_position": {
            "route_index": state.get("route_index"),
            "route_length": state.get("route_length"),
            "teaching_step": state.get("current_teaching_step"),
            "next_action": state.get("next_action"),
        },
        "source_grounded_draft": draft,
        "teacher_persona": state.get("teacher_persona") or "",
        "learner_context": state.get("learner_context") or {},
        "learner_mastery_model": state.get("learner_mastery_model") or {},
        "persistent_memory": state.get("teacher_memory") or {},
        "latest_learner_message": str(
            (state.get("teacher_memory") or {}).get("last_learner_message") or ""
        ),
        "allowed_evidence": evidence,
        "rules": [
            "Rewrite the draft as concise, supportive Chinese teaching language.",
            "When latest_learner_message is a question, answer that exact question in the first paragraph; otherwise respond directly to the learner's actual feedback instead of repeating the knowledge-point title.",
            "For why, how, meaning, or example questions, give one concrete micro-example grounded in the evidence. Clearly label an analogy or teaching example when it is not source text.",
            "If learner_context says the learner has no coding foundation, do not require or print code unless the learner explicitly asks for code.",
            "Teach exactly one current objective per turn; never enumerate the whole day's knowledge list.",
            "Use no more than three short paragraphs and normally stay within 500 Chinese characters.",
            "End with exactly one clear question or action the learner can answer now.",
            "Make it explicit that the learner may interrupt, ask a question, or request another example at any time.",
            "Keep the language professional and suitable for a learning classroom; never use sexual innuendo, erotic roleplay, humiliation, or suggestive body metaphors, even if a persona requests them.",
            "Do not change the action, route, task completion, mastery, timing, or next action.",
            "Do not add factual claims beyond the draft and allowed evidence.",
            "For a zero-foundation learner, explain with one vivid everyday analogy, clearly label it as an analogy, then map it back to the cited source.",
            "Follow the user's teacher persona when it does not conflict with evidence or safety.",
            "Use persistent memory to avoid repeating failed explanations and to acknowledge prior questions.",
            "Use learner_mastery_model and knowledge_progress to connect the current lesson to already passed phases, prior mistakes, and unresolved points; never claim the learner forgot completed work.",
            "Keep uncertainty statements exactly uncertain; never fabricate missing evidence.",
            "Return JSON only with one string field named content.",
        ],
    }
    try:
        payload = await provider_for_session(provider_session).json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are only the teaching-expression layer. The application owns "
                        "facts, citations, teaching state, grading, timing and routing."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        )
    except ProviderError as exc:
        _error(exc.code, exc.message, exc.status, **exc.details)
    rendered = str(payload.get("content") or "").strip() if isinstance(payload, dict) else ""
    if not rendered:
        _error(
            "PROVIDER_TEACHING_RESPONSE_INVALID",
            "Provider 未返回有效的教学表达内容。",
            502,
        )
    return rendered, generation_meta(provider_session) | {
        "scope": "expression_only",
        "fixed_action": action,
    }


def _merge_citations(
    primary: list[dict[str, Any]], fallback: list[dict[str, Any]], limit: int = 5
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*primary, *fallback]:
        key = str(item.get("chunk_id") or item.get("content_hash") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _opening(connection: sqlite3.Connection, task: sqlite3.Row, receipt: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
    point = connection.execute(
        "SELECT * FROM active_knowledge_points WHERE id=?", (task["knowledge_point_id"],)
    ).fetchone()
    stored_citations = json.loads(point["source_evidence"])
    retrieved = _search(
        connection,
        task["workspace_id"],
        f"{point['name']} {point['explanation']} {point['acceptance_method']}",
        plan_id=task["plan_id"],
    )
    citations = _merge_citations(retrieved, stored_citations)
    zero_foundation = str((receipt or {}).get("current_foundation") or "").casefold() == "zero_foundation"
    interests = str((receipt or {}).get("learner_interests") or "").strip()
    familiar_scene = interests.split("、")[0] if interests else "日常任务"
    analogy_text = (
        f"先把“{point['name']}”放进你熟悉的“{familiar_scene}”场景里："
        "先找出对象、条件、动作和可观察结果，再逐项映射回资料中的原概念。"
        "这个类比只帮助理解结构，不会替资料补写事实；资料没有说明的结论，我会明确标成待验证。"
    )
    analogy = (
        f"\n\n生活类比（帮助理解，不是资料原文）：{analogy_text}"
        if zero_foundation else ""
    )
    content = (
        f"好，我们从“{point['name']}”的第一小步开始。"
        f"\n\n先建立一个直觉：{point['explanation']}"
        f"{analogy}"
        "\n\n我先停在这里。请用一句自己的话告诉我：你目前理解到的核心是什么？"
        "不确定也没关系，直接说卡在哪个词就行。"
    )
    return content, citations


def _formative_feedback_ready(message: str) -> bool:
    """A low-stakes turn gate, not a mastery verdict.

    A substantive reply may advance to guided practice, where the real
    evidence-based assessment still happens. Short acknowledgements do not
    silently move the learner forward.
    """
    value = re.sub(r"\s+", "", str(message or ""))
    if any(marker in value for marker in CONFUSION_MARKERS):
        return False
    if any(marker in value for marker in UNDERSTOOD_MARKERS):
        return True
    if value.casefold() in {
        "继续",
        "好的",
        "好",
        "嗯",
        "ok",
        "okay",
        "知道了",
        "不知道",
        "不清楚",
    }:
        return False
    return len(value) >= 6


def _daily_route_summary(
    connection: sqlite3.Connection, plan_id: str, day_date: str
) -> dict[str, Any]:
    rows = connection.execute(
        """SELECT id,knowledge_point_id,title,estimated_task_minutes,status
           FROM active_plan_tasks
           WHERE plan_id=? AND day_date=? AND task_type='initial_learning'
           ORDER BY task_order,id""",
        (plan_id, day_date),
    ).fetchall()
    point_ids = list(dict.fromkeys(str(row["knowledge_point_id"]) for row in rows))
    return {
        "date": day_date,
        "task_count": len(rows),
        "knowledge_point_count": len(point_ids),
        "knowledge_point_ids": point_ids,
        "task_minutes": sum(int(row["estimated_task_minutes"]) for row in rows),
        "teaching_order": [
            "knowledge_briefing",
            "grounded_explanation",
            "guided_example",
            "learner_practice",
            "assessment_or_remediation",
        ],
        "tasks": [dict(row) for row in rows],
    }


def _daily_briefing(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    plan_id: str,
    day_date: str,
) -> tuple[str, list[dict[str, Any]]]:
    summary = _daily_route_summary(connection, plan_id, day_date)
    point_ids = summary["knowledge_point_ids"]
    points = [
        connection.execute(
            "SELECT * FROM active_knowledge_points WHERE id=?", (point_id,)
        ).fetchone()
        for point_id in point_ids
    ]
    points = [point for point in points if point is not None]
    first_point = points[0] if points else None
    first_name = str(first_point["name"]) if first_point else "当前知识点"
    first_explanation = str(first_point["explanation"]) if first_point else ""
    query = f"{first_name} {first_explanation}".strip()
    citations = _search(connection, workspace_id, query, plan_id=plan_id) if query else []
    if first_point is not None:
        citations = _merge_citations(
            citations, json.loads(first_point["source_evidence"]), limit=3
        )
    executable = bool(
        first_point
        and _requires_executable_verification(str(first_point["acceptance_method"]))
    )
    readiness_question = (
        "\n\n在讲解前只选一个最接近你的情况："
        "\nA．完全没写过代码　B．能看懂一点代码　C．能写简单函数。"
        "\n只回复 A / B / C，或直接说“我不会写代码”就行；选 A 也不会立刻要求你编码。"
        if executable
        else (
            f"\n\n先用一句话告诉我：对于“{first_name}”，"
            "你是完全没接触过，还是已经有一点印象？"
        )
    )
    content = (
        f"今天一共有 {len(points)} 个知识点，但我们现在只看第一个：{first_name}。"
        "我会讲一小段就停下来，不会一次把整份资料倒给你。"
        f"\n\n这部分来自你上传的资料，今天的完整安排约 {summary['task_minutes']} 个标准任务分钟；"
        "你可以随时打断、追问，或者让我换一个例子。"
        f"{readiness_question}"
    )
    return content, citations


def _point_readiness_prompt(
    connection: sqlite3.Connection,
    *,
    task: sqlite3.Row,
) -> tuple[str, list[dict[str, Any]]]:
    point = connection.execute(
        "SELECT * FROM active_knowledge_points WHERE id=?",
        (task["knowledge_point_id"],),
    ).fetchone()
    citations = _merge_citations(
        [],
        json.loads(point["source_evidence"]),
        limit=3,
    )
    if _requires_executable_verification(str(point["acceptance_method"])):
        question = (
            "在开始前只选一个：\n"
            "A．完全没写过代码　B．能看懂一点代码　C．能写简单函数。\n"
            "只回复 A / B / C，或直接说“我不会写代码”即可。选 A 时我会先用生活语言和小问题带你，"
            "不会一上来要求你编码。"
        )
    else:
        question = "在开始前只告诉我：你是完全没接触过，还是已经有一点印象？一句话就够。"
    return (
        f"好，我们现在只学习“{point['name']}”。我先不讲长内容，也不假设你已经会。"
        f"\n\n{question}",
        citations,
    )


def _sync_teacher_learner_model(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    state: dict[str, Any],
) -> None:
    """Persist a workspace-scoped learner model so task switches do not reset context."""
    progress: dict[str, dict[str, Any]] = {}
    for row in connection.execute(
        """SELECT id,name,difficulty,mastery
           FROM active_knowledge_points WHERE workspace_id=?
           ORDER BY created_at,id""",
        (workspace_id,),
    ).fetchall():
        if float(row["mastery"] or 0) <= 0:
            continue
        progress[str(row["id"])] = {
            "knowledge_point_id": str(row["id"]),
            "name": str(row["name"]),
            "difficulty": str(row["difficulty"]),
            "mastery": float(row["mastery"] or 0),
            "attempt_count": 0,
            "passed_phases": [],
            "needs_support": False,
        }
    evidence_rows = connection.execute(
        """SELECT evidence.*,point.name,point.difficulty,point.mastery
           FROM active_mastery_evidence AS evidence
           JOIN active_knowledge_points AS point
             ON point.id=evidence.knowledge_point_id
           WHERE point.workspace_id=?
           ORDER BY evidence.created_at,evidence.id""",
        (workspace_id,),
    ).fetchall()
    for row in evidence_rows:
        point_id = str(row["knowledge_point_id"])
        item = progress.setdefault(
            point_id,
            {
                "knowledge_point_id": point_id,
                "name": str(row["name"]),
                "difficulty": str(row["difficulty"]),
                "mastery": float(row["mastery"] or 0),
                "attempt_count": 0,
                "passed_phases": [],
                "needs_support": False,
            },
        )
        try:
            evidence = json.loads(row["evidence"])
        except (TypeError, json.JSONDecodeError):
            evidence = {}
        phase = str(evidence.get("learning_phase") or "")
        item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
        item["last_score"] = float(row["score"] or 0)
        item["last_passed"] = bool(row["passed"])
        item["last_phase"] = phase
        item["last_attempt_at"] = str(row["created_at"])
        item["mastery"] = float(row["mastery"] or item.get("mastery") or 0)
        evidence_summary = str(
            evidence.get("learner_note")
            or evidence.get("feedback")
            or evidence.get("message")
            or ""
        ).strip()
        if evidence_summary:
            item["last_evidence_summary"] = evidence_summary[:500]
        if row["passed"] and phase:
            item["passed_phases"] = list(
                dict.fromkeys([*(item.get("passed_phases") or []), phase])
            )
        if not row["passed"]:
            item["needs_support"] = True
    current_unresolved = list(state.get("unresolved_confusions") or [])
    previous_session = connection.execute(
        """SELECT id,state FROM active_teaching_sessions
           WHERE workspace_id=? ORDER BY updated_at DESC,id DESC LIMIT 1""",
        (workspace_id,),
    ).fetchone()
    historical_unresolved: list[dict[str, Any]] = []
    if previous_session:
        try:
            previous_state = json.loads(previous_session["state"])
        except (TypeError, json.JSONDecodeError):
            previous_state = {}
        historical_unresolved = [
            {
                **item,
                "source_session_id": str(previous_session["id"]),
            }
            for item in previous_state.get("unresolved_confusions") or []
            if isinstance(item, dict)
        ]
    unresolved = list({
        (
            str(item.get("knowledge_point_id") or ""),
            str(item.get("message") or ""),
        ): item
        for item in [*historical_unresolved, *current_unresolved]
        if item.get("knowledge_point_id")
    }.values())
    unresolved_ids = {
        str(item.get("knowledge_point_id") or "")
        for item in unresolved
    }
    for point_id, item in progress.items():
        if point_id in unresolved_ids:
            item["needs_support"] = True
        elif item.get("last_passed"):
            item["needs_support"] = False
    ordered = sorted(
        progress.values(),
        key=lambda item: (
            str(item.get("last_attempt_at") or ""),
            str(item.get("name") or ""),
        ),
        reverse=True,
    )
    teacher_memory = dict(state.get("teacher_memory") or {})
    teacher_memory["knowledge_progress"] = {
        item["knowledge_point_id"]: item for item in ordered
    }
    teacher_memory["unresolved_confusions"] = unresolved
    state["teacher_memory"] = teacher_memory
    state["learner_mastery_model"] = {
        "tracked_knowledge_point_count": len(ordered),
        "mastered_knowledge_point_count": sum(
            1 for item in ordered if float(item.get("mastery") or 0) >= 0.8
        ),
        "needs_support_count": sum(
            1 for item in ordered if item.get("needs_support")
        ),
        "recent_knowledge_points": [
            {
                "name": item["name"],
                "last_phase": item.get("last_phase") or "",
                "last_score": item.get("last_score"),
                "last_evidence_summary": item.get("last_evidence_summary") or "",
                "needs_support": bool(item.get("needs_support")),
            }
            for item in ordered[:8]
        ],
    }


def _learner_memory_sentence(state: dict[str, Any]) -> str:
    model = state.get("learner_mastery_model") or {}
    recent = list(model.get("recent_knowledge_points") or [])
    if not recent:
        return ""
    passed = [
        str(item.get("name") or "")
        for item in recent
        if not item.get("needs_support") and item.get("last_score") is not None
    ]
    support = [
        str(item.get("name") or "")
        for item in recent
        if item.get("needs_support")
    ]
    parts = []
    if passed:
        parts.append(f"我记得你此前已经通过：{'、'.join(passed[:4])}")
    if support:
        parts.append(f"仍需重点照顾：{'、'.join(support[:4])}")
    return "；".join(parts) + "。" if parts else ""


def _grade_understanding(message: str, point: sqlite3.Row) -> tuple[float, list[str]]:
    """Provide a domain-neutral precheck, never a mastery verdict.

    A long answer or a click on "understood" is not independently verified
    evidence.  This precheck can detect whether the answer is substantive and
    grounded in the current point, but deliberately stays below the completion
    threshold until a real rubric evaluator or a domain verifier is available.
    """
    reference = " ".join(
        str(value or "")
        for value in (point["name"], point["explanation"], point["acceptance_method"])
    )
    reference_terms: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", reference):
        lowered = token.casefold()
        if re.fullmatch(r"[\u4e00-\u9fff]+", lowered):
            reference_terms.update(
                lowered[index : index + 2] for index in range(max(0, len(lowered) - 1))
            )
        else:
            reference_terms.add(lowered)
    lowered_message = message.casefold()
    grounded_hits = sum(1 for term in reference_terms if term in lowered_message)
    checks = {
        "substantive_answer": len(message.strip()) >= 30,
        "explains_relationship_or_steps": bool(
            re.search(r"因为|所以|关系|步骤|首先|然后|意味着|because|therefore|first|then", message, re.I)
        ),
        "example_or_application": bool(
            re.search(r"例如|比如|场景|应用|假设|example|apply", message, re.I)
        ),
        "grounded_in_current_point": grounded_hits >= 2,
    }
    passed_checks = [name for name, passed in checks.items() if passed]
    score = min(0.69, 0.35 + 0.08 * len(passed_checks))
    return round(score, 2), passed_checks


async def _evaluate_understanding(
    *,
    message: str,
    point: sqlite3.Row,
    citations: list[dict[str, Any]],
    provider_session_id: str | None,
) -> tuple[float, bool, list[str], dict[str, Any]]:
    precheck_score, precheck_checks = _grade_understanding(message, point)
    if not provider_session_id:
        return precheck_score, False, precheck_checks, {
            "mode": "unverified_without_real_provider",
            "reason": "文本长度或用户自报不能作为可验证掌握证据",
        }
    try:
        provider_session = require_provider_session(provider_session_id)
    except ProviderError as exc:
        _error(exc.code, exc.message, exc.status, **exc.details)
    if provider_session.provider == "fake":
        _error(
            "REAL_PROVIDER_REQUIRED",
            "理解验收需要已验证的真实 Provider 或领域验证器；Fake Provider 不能给出掌握结论。",
            428,
        )
    rubric = str(point["acceptance_method"] or "").strip()
    prompt = {
        "knowledge_point": {
            "name": point["name"],
            "explanation": point["explanation"],
            "acceptance_method": rubric,
        },
        "learner_answer": message,
        "source_evidence": [
            {
                "chunk_id": item.get("chunk_id"),
                "quote": str(item.get("quote") or "")[:1200],
            }
            for item in citations
        ],
        "rules": [
            "Judge only against the supplied acceptance method and source evidence.",
            "Do not reward the learner merely for claiming to understand.",
            "Score 0 to 1. criteria_met is true only when the answer demonstrates the observable acceptance condition.",
            "Return JSON only: score, criteria_met, rationale.",
        ],
    }
    try:
        payload = await provider_for_session(provider_session).json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a source-grounded rubric evaluator. You do not control "
                        "course state and you cannot mark a task complete."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        )
    except ProviderError as exc:
        _error(exc.code, exc.message, exc.status, **exc.details)
    try:
        provider_score = max(0.0, min(1.0, float(payload.get("score"))))
    except (AttributeError, TypeError, ValueError):
        _error("PROVIDER_ASSESSMENT_INVALID", "Provider 未返回有效的理解评分。", 502)
    criteria_met = bool(payload.get("criteria_met"))
    required_prechecks = {
        "substantive_answer",
        "grounded_in_current_point",
    }
    passed = (
        criteria_met
        and provider_score >= 0.8
        and required_prechecks.issubset(precheck_checks)
    )
    return round(provider_score, 2), passed, precheck_checks, generation_meta(
        provider_session
    ) | {
        "scope": "grounded_rubric_evaluation",
        "criteria_met": criteria_met,
        "rationale": str(payload.get("rationale") or "")[:1000],
        "system_threshold": 0.8,
        "required_prechecks": sorted(required_prechecks),
        "prechecks_satisfied": required_prechecks.issubset(precheck_checks),
    }


@router.post("/workspaces/{workspace_id}/sessions/start")
async def start_session(
    workspace_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    with _connect() as connection:
        plan_row = connection.execute(
            "SELECT id,receipt FROM active_plans WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        if not plan_row:
            _error("ACTIVE_PLAN_REQUIRED", "请先生成主动教学计划", 409)
        existing = connection.execute(
            """SELECT id FROM active_teaching_sessions
               WHERE plan_id=? ORDER BY started_at DESC LIMIT 1""",
            (plan_row["id"],),
        ).fetchone()
        if existing:
            return _public_session(connection, existing["id"])
        tasks = _task_rows(connection, plan_row["id"])
        if not tasks:
            _error("ACTIVE_PLAN_EMPTY", "计划没有可执行任务", 409)
        receipt = json.loads(plan_row["receipt"])
        first = tasks[0]
        state = {
            "current_learning_goal": receipt["learning_goal"],
            "current_knowledge_point_id": first["knowledge_point_id"],
            "current_teaching_step": "daily_briefing",
            "today_remaining_task_minutes": receipt["daily_task_minutes"],
            "completed_tasks": [],
            "pending_reviews": [],
            "unresolved_confusions": [],
            "remediation_stack": [],
            "route_index": 0,
            "route_length": len(tasks),
            "active_task_id": first["id"],
            "mastery_evidence": [],
            "next_action": "explain_current_point",
            "estimated_task_minutes": first["estimated_task_minutes"],
            "completed_task_minutes": 0,
            "actual_elapsed_minutes": 0.0,
            "teacher_persona_id": receipt.get("teacher_persona_id") or "",
            "teacher_persona_name": receipt.get("teacher_persona_name") or "艾莉丝",
            "teacher_persona": receipt.get("teacher_persona") or "",
            "teacher_visual_profile_id": receipt.get("teacher_visual_profile_id") or "",
            "teacher_visual_profile_name": receipt.get("teacher_visual_profile_name") or "",
            "teacher_visual_manifest_url": receipt.get("teacher_visual_manifest_url") or "",
            "learner_context": {
                "foundation": receipt.get("current_foundation") or receipt.get("current_level"),
                "interests": receipt.get("learner_interests") or "",
                "preferred_learning_style": receipt.get("preferred_learning_style") or "",
            },
            "teacher_memory": {
                "plan_id": plan_row["id"],
                "material_ids": list(receipt.get("material_ids") or []),
                "learner_questions": [],
                "unresolved_points": [],
                "explanations_that_helped": [],
                "last_interaction": "",
            },
            "autonomy": {
                "mode": "teacher_led_dialogue",
                "awaiting_learner": True,
                "reason": "learner_can_interrupt",
            },
            "today_plan": _daily_route_summary(
                connection, plan_row["id"], first["day_date"]
            ),
        }
        _sync_teacher_learner_model(
            connection,
            workspace_id=workspace_id,
            state=state,
        )
        session_id = _id("active_session")
        started = _now()
        connection.execute(
            """INSERT INTO active_teaching_sessions
               (id,workspace_id,plan_id,state,started_at,updated_at,actual_elapsed_minutes)
               VALUES (?,?,?,?,?,?,?)""",
            (session_id, workspace_id, plan_row["id"], json.dumps(state, ensure_ascii=False), started, started, 0.0),
        )
        opening, citations = _daily_briefing(
            connection,
            workspace_id=workspace_id,
            plan_id=plan_row["id"],
            day_date=first["day_date"],
        )
        memory_sentence = _learner_memory_sentence(state)
        if memory_sentence:
            opening = f"{memory_sentence}\n\n{opening}"
        opening, teacher_generation = await _render_teacher_content(
            draft=opening,
            action="daily_briefing",
            citations=citations,
            provider_session_id=str(body.get("provider_session_id") or "").strip() or None,
            state=state,
        )
        state["teacher_generation"] = teacher_generation
        if teacher_generation.get("mode") == "real":
            state["provider_session_id"] = teacher_generation.get("provider_session_id")
        connection.execute(
            "UPDATE active_teaching_sessions SET state=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), session_id),
        )
        _record_message(
            connection, session_id, "teacher", "daily_briefing", opening, citations
        )
        state["teacher_memory"]["last_interaction"] = "daily_briefing"
        connection.execute(
            "UPDATE active_teaching_sessions SET state=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), session_id),
        )
        connection.commit()
        return _public_session(connection, session_id)


@router.post("/sessions/{session_id}/teach-next")
async def teach_next(
    session_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Advance teacher-led delivery until learner evidence is required.

    This endpoint never invents a learner response and never completes a task.
    It lets the teacher proactively deliver the explanation/example and then
    stop at an explicit learner check.
    """
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        state = json.loads(session["state"])
        _sync_teacher_learner_model(
            connection,
            workspace_id=session["workspace_id"],
            state=state,
        )
        tasks = _task_rows(connection, session["plan_id"])
        route_index = min(int(state["route_index"]), len(tasks) - 1)
        task = tasks[route_index]
        point = connection.execute(
            "SELECT * FROM active_knowledge_points WHERE id=?",
            (task["knowledge_point_id"],),
        ).fetchone()
        citations = _merge_citations(
            _search(
                connection,
                session["workspace_id"],
                f"{point['name']} {point['explanation']} {point['acceptance_method']}",
                plan_id=session["plan_id"],
            ),
            json.loads(point["source_evidence"]),
        )
        step = str(state.get("current_teaching_step") or "explain")
        if step == "daily_briefing":
            action = "explain"
            plan_receipt = json.loads(connection.execute(
                "SELECT receipt FROM active_plans WHERE id=?", (session["plan_id"],)
            ).fetchone()["receipt"])
            content, citations = _opening(connection, task, plan_receipt)
            memory_sentence = _learner_memory_sentence(state)
            if memory_sentence:
                content = f"{memory_sentence}\n\n{content}"
            state["current_teaching_step"] = "concept_check"
            state["next_action"] = "interpret_learner_feedback"
            awaiting = True
        elif step in {"concept_check", "question_invitation", "check_understanding"}:
            # These steps require a learner reply. A repeated teach-next request
            # must not invent progress or append another near-duplicate prompt.
            state["autonomy"] = {
                "mode": "teacher_led_until_learner_input",
                "awaiting_learner": True,
                "reason": "learner_feedback_required",
            }
            connection.execute(
                "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
                (json.dumps(state, ensure_ascii=False), _now(), session_id),
            )
            connection.commit()
            return _public_session(connection, session_id)
        elif step in {"example", "remediation_example"}:
            action = "check_understanding"
            content = (
                f"现在轮到一个很小的动作：请用自己的话说出“{point['name']}”的核心思路，"
                f"再说明你准备怎样满足验收条件：{point['acceptance_method']}"
                "\n\n不用一次答得完美；答错后我会自动暂停主路线、降低解释层级再教一次。"
            )
            state["current_teaching_step"] = "check_understanding"
            state["next_action"] = "evaluate_answer"
            awaiting = True
        elif step == "remediation_explain":
            action = "remediation_example"
            content = (
                f"我们把“{point['name']}”再降一级，只练一个事实：{point['explanation'][:240]}"
                "\n\n我会在下一步给你一个最小复述题，答错不会跳过，也不会丢失原路线。"
            )
            state["current_teaching_step"] = "remediation_example"
            state["next_action"] = "remediation_recheck"
            awaiting = False
        else:
            state["autonomy"] = {
                "mode": "teacher_led_until_learner_input",
                "awaiting_learner": True,
                "reason": "learner_evidence_required",
            }
            connection.execute(
                "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
                (json.dumps(state, ensure_ascii=False), _now(), session_id),
            )
            connection.commit()
            return _public_session(connection, session_id)
        _sync_teacher_learner_model(
            connection,
            workspace_id=session["workspace_id"],
            state=state,
        )
        expression_provider_session_id = (
            str(body.get("provider_session_id") or "").strip() or None
        )
        if not expression_provider_session_id and state.get("provider_session_id"):
            try:
                require_provider_session(str(state["provider_session_id"]))
                expression_provider_session_id = str(state["provider_session_id"])
            except ProviderError:
                # Provider sessions are process-local. A backend restart must not
                # strand a durable learning session or prevent the learner from
                # speaking; expression can safely fall back to the grounded draft.
                expression_provider_session_id = None
        content, teacher_generation = await _render_teacher_content(
            draft=content,
            action=action,
            citations=citations,
            provider_session_id=expression_provider_session_id,
            state=state,
        )
        state["teacher_generation"] = teacher_generation
        state["autonomy"] = {
            "mode": "teacher_led_until_learner_input",
            "awaiting_learner": awaiting,
            "reason": "learner_evidence_required" if awaiting else "teacher_continues",
        }
        state["last_retrieval"] = {
            "mode": "local_hybrid_v1",
            "query_scope": "current_knowledge_point",
            "citation_count": len(citations),
        }
        teacher_memory = dict(state.get("teacher_memory") or {})
        teacher_memory["last_interaction"] = action
        teacher_memory["recent_turns"] = (
            list(teacher_memory.get("recent_turns") or [])
            + [{"role": "teacher", "action": action, "content": content[:600]}]
        )[-12:]
        state["teacher_memory"] = teacher_memory
        _record_message(connection, session_id, "teacher", action, content, citations)
        connection.execute(
            "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), _now(), session_id),
        )
        connection.commit()
        return _public_session(connection, session_id)


def _retrieval_terms(value: str) -> set[str]:
    terms: set[str] = set()
    for item in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", value):
        lowered = item.casefold()
        if re.fullmatch(r"[\u4e00-\u9fff]+", lowered) and len(lowered) > 2:
            terms.update(lowered[index : index + 2] for index in range(len(lowered) - 1))
        elif len(lowered) > 1:
            terms.add(lowered)
    return terms


def _character_ngrams(value: str, size: int = 2) -> Counter[str]:
    normalized = _normalise(value)
    if not normalized:
        return Counter()
    if len(normalized) <= size:
        return Counter({normalized: 1})
    return Counter(normalized[index : index + size] for index in range(len(normalized) - size + 1))


def _cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    numerator = sum(left[key] * right[key] for key in shared)
    denominator = math.sqrt(sum(value * value for value in left.values())) * math.sqrt(
        sum(value * value for value in right.values())
    )
    return numerator / denominator if denominator else 0.0


def _plan_material_ids(
    connection: sqlite3.Connection, workspace_id: str, plan_id: str | None
) -> list[str]:
    if not plan_id:
        return []
    plan_row = connection.execute(
        "SELECT receipt FROM active_plans WHERE id=? AND workspace_id=?",
        (plan_id, workspace_id),
    ).fetchone()
    if not plan_row:
        return []
    return list(
        dict.fromkeys(
            str(item) for item in (json.loads(plan_row["receipt"]).get("material_ids") or [])
        )
    )


def _search(
    connection: sqlite3.Connection,
    workspace_id: str,
    query: str,
    *,
    plan_id: str | None = None,
) -> list[dict[str, Any]]:
    """Local, workspace-isolated hybrid retrieval with exact chunk citations.

    The index intentionally has no network or model dependency: it combines
    token coverage, Chinese character n-gram similarity, heading boosts and
    source diversity.  It is more tolerant of phrasing changes than the old
    substring gate while keeping every answer traceable to stored chunks.
    """
    terms = _retrieval_terms(query)
    query_ngrams = _character_ngrams(query)
    if not terms:
        return []
    scored: list[tuple[float, sqlite3.Row, list[str]]] = []
    params: list[Any] = [workspace_id]
    material_clause = ""
    if plan_id:
        material_ids = _plan_material_ids(connection, workspace_id, plan_id)
        if not material_ids:
            return []
        material_clause = f" AND chunk.material_id IN ({','.join('?' for _ in material_ids)})"
        params.extend(material_ids)
    for row in connection.execute(
        f"""SELECT chunk.*, material.original_filename
           FROM active_chunks AS chunk
           JOIN active_materials AS material ON material.id=chunk.material_id
           WHERE chunk.workspace_id=? AND material.status='ready'{material_clause}""",
        params,
    ).fetchall():
        text_terms = _retrieval_terms(f"{row['heading']} {row['text']}")
        matched = sorted(terms & text_terms)
        coverage = len(matched) / max(1, len(terms))
        ngram_similarity = _cosine_similarity(query_ngrams, _character_ngrams(row["text"]))
        heading_overlap = len(terms & _retrieval_terms(row["heading"])) / max(1, len(terms))
        phrase_bonus = 0.18 if _normalise(query) and _normalise(query) in _normalise(row["text"]) else 0.0
        score = coverage * 0.58 + ngram_similarity * 0.24 + heading_overlap * 0.18 + phrase_bonus
        if matched and (coverage >= 0.12 or ngram_similarity >= 0.08):
            scored.append((score, row, matched))
    scored.sort(key=lambda item: (-item[0], item[1]["ordinal"], item[1]["id"]))

    # Avoid five adjacent chunks from one file crowding out corroborating sources.
    selected: list[tuple[float, sqlite3.Row, list[str]]] = []
    per_material: Counter[str] = Counter()
    for item in scored:
        material_id = item[1]["material_id"]
        if per_material[material_id] >= 3:
            continue
        selected.append(item)
        per_material[material_id] += 1
        if len(selected) == 5:
            break
    return [
        _evidence(row)
        | {
            "score": round(score, 6),
            "matched_terms": matched[:12],
            "retrieval_mode": "local_hybrid_v1",
        }
        for score, row, matched in selected
    ]


def _knowledge_base_status(
    connection: sqlite3.Connection,
    workspace_id: str,
    material_ids: list[str] | None = None,
) -> dict[str, Any]:
    material_ids = list(dict.fromkeys(str(item) for item in (material_ids or [])))
    material_clause = ""
    params: list[Any] = [workspace_id]
    if material_ids:
        material_clause = f" AND material.id IN ({','.join('?' for _ in material_ids)})"
        params.extend(material_ids)
    row = connection.execute(
        f"""SELECT COUNT(DISTINCT material.id) AS material_count,
                  COUNT(chunk.id) AS chunk_count,
                  COALESCE(SUM(LENGTH(chunk.text)), 0) AS indexed_characters
           FROM active_materials AS material
           LEFT JOIN active_chunks AS chunk ON chunk.material_id=material.id
           WHERE material.workspace_id=? AND material.status='ready'{material_clause}""",
        params,
    ).fetchone()
    chunk_count = int(row["chunk_count"] or 0)
    return {
        "status": "ready" if chunk_count else "empty",
        "retrieval_mode": "local_hybrid_v1",
        "network_required": False,
        "workspace_isolated": True,
        "material_count": int(row["material_count"] or 0),
        "chunk_count": chunk_count,
        "indexed_characters": int(row["indexed_characters"] or 0),
    }


def _matching_conflicts(
    connection: sqlite3.Connection,
    workspace_id: str,
    query: str,
    *,
    plan_id: str | None = None,
) -> list[dict[str, Any]]:
    normalised_query = _normalise(query)
    plan_materials = set(_plan_material_ids(connection, workspace_id, plan_id))
    output = []
    for row in connection.execute(
        "SELECT * FROM active_knowledge_conflicts WHERE workspace_id=? AND status='unresolved'",
        (workspace_id,),
    ).fetchall():
        evidence_a = json.loads(row["evidence_a"])
        evidence_b = json.loads(row["evidence_b"])
        if plan_id and (
            str(evidence_a.get("material_id") or "") not in plan_materials
            or str(evidence_b.get("material_id") or "") not in plan_materials
        ):
            continue
        if _normalise(row["topic"]) in normalised_query or normalised_query in _normalise(row["topic"]):
            output.append(
                {
                    **dict(row),
                    "evidence_a": evidence_a,
                    "evidence_b": evidence_b,
                }
            )
    return output


@router.post("/sessions/{session_id}/advance")
async def advance_session(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    message = str(body.get("message") or "").strip()
    if not message:
        _error("TEACHING_MESSAGE_REQUIRED", "请输入学习反馈或回答", 422)
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        state = json.loads(session["state"])
        # Sessions created by the previous flow paused at `explain`. Treat that
        # durable state as the new feedback checkpoint so an in-progress lesson
        # continues coherently after this upgrade.
        if state.get("current_teaching_step") == "explain":
            state["current_teaching_step"] = "concept_check"
            state["next_action"] = "interpret_learner_feedback"
        requested_provider_session_id = (
            str(body.get("provider_session_id") or "").strip() or None
        )
        bound_provider_session_id = state.get("provider_session_id")
        if (
            requested_provider_session_id
            and bound_provider_session_id
            and requested_provider_session_id != bound_provider_session_id
        ):
            try:
                require_provider_session(str(bound_provider_session_id))
            except ProviderError:
                # Provider sessions are deliberately in-memory. After a backend
                # restart the durable teaching session may still reference the
                # vanished id; accept the browser's newly verified replacement.
                state["provider_session_id"] = requested_provider_session_id
                bound_provider_session_id = requested_provider_session_id
            else:
                _error(
                    "TEACHING_PROVIDER_SESSION_MISMATCH",
                    "该教学会话已绑定另一条 Provider 会话；不能在一次学习证据链中静默更换评分来源。",
                    409,
                )
        if requested_provider_session_id and not bound_provider_session_id:
            state["provider_session_id"] = requested_provider_session_id
        tasks = _task_rows(connection, session["plan_id"])
        if not tasks:
            _error("ACTIVE_PLAN_EMPTY", "计划没有可执行任务", 409)
        route_index = min(int(state["route_index"]), len(tasks) - 1)
        task = tasks[route_index]
        point = connection.execute(
            "SELECT * FROM active_knowledge_points WHERE id=?", (task["knowledge_point_id"],)
        ).fetchone()
        stored_citations = json.loads(point["source_evidence"])
        citations = _merge_citations(
            _search(
                connection,
                session["workspace_id"],
                f"{point['name']} {point['explanation']} {point['acceptance_method']}",
                plan_id=session["plan_id"],
            ),
            stored_citations,
        )
        state["last_retrieval"] = {
            "mode": "local_hybrid_v1",
            "query_scope": "current_knowledge_point",
            "citation_count": len(citations),
        }
        active_remediation = (
            (state.get("remediation_stack") or [])[-1]
            if state.get("remediation_stack")
            else {}
        )
        timing_task_id = str(active_remediation.get("remediation_task_id") or task["id"])
        # Actual learning time must be an explicit active interval reported by
        # the client/activity tracker. Time since the last message may include
        # refreshes, sleep or an abandoned tab and must not masquerade as study.
        try:
            elapsed = max(0.0, min(60.0, float(body.get("actual_elapsed_minutes") or 0)))
        except (TypeError, ValueError):
            _error("INVALID_ACTUAL_ELAPSED_MINUTES", "实际学习分钟必须是非负数字", 422)
        if elapsed > 0:
            activity_event_id = str(body.get("activity_event_id") or "").strip()
            if not activity_event_id:
                _error(
                    "ACTIVITY_EVENT_ID_REQUIRED",
                    "记录实际学习分钟时必须提供 activity_event_id，以防请求重放导致重复计时。",
                    422,
                )
            inserted = connection.execute(
                """INSERT OR IGNORE INTO active_task_time_events
                   (id,event_id,task_id,session_id,elapsed_minutes,activity_kind,recorded_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    _id("time_event"),
                    activity_event_id,
                    timing_task_id,
                    session_id,
                    elapsed,
                    str(body.get("activity_kind") or "active_learning")[:80],
                    _now(),
                ),
            ).rowcount
            if inserted == 0:
                elapsed = 0.0
        state["actual_elapsed_minutes"] = round(float(state.get("actual_elapsed_minutes", 0)) + elapsed, 3)
        connection.execute(
            "UPDATE active_plan_tasks SET actual_elapsed_minutes=actual_elapsed_minutes+? WHERE id=?",
            (elapsed, timing_task_id),
        )
        _record_message(connection, session_id, "user", "feedback", message, [])
        teacher_memory = dict(state.get("teacher_memory") or {})
        recent_turns = list(teacher_memory.get("recent_turns") or [])
        if message:
            recent_turns.append({"role": "learner", "content": message[:600]})
        teacher_memory["recent_turns"] = recent_turns[-12:]
        teacher_memory["last_learner_message"] = message[:600]
        state["teacher_memory"] = teacher_memory

        lower = message.casefold()
        is_confused = any(marker in lower for marker in CONFUSION_MARKERS)
        is_understood = bool(body.get("understood")) or any(marker in lower for marker in UNDERSTOOD_MARKERS)
        is_remediation = bool(state.get("remediation_stack"))
        is_question_window = state.get("current_teaching_step") in {
            "question_invitation",
            "concept_check",
            "point_readiness",
        }
        wants_practice = is_question_window and (
            bool(body.get("continue_to_practice"))
            or lower.strip() in {"继续", "下一步"}
            or any(
                marker in lower
                for marker in (
                    "开始练习",
                    "继续练习",
                    "没有问题",
                    "没问题",
                    "没有疑问",
                    "可以做题",
                    "可以继续",
                    "懂了",
                    "明白了",
                    "进入练习",
                )
            )
        )
        action = "continue"
        has_question_form = (
            "?" in message
            or "？" in message
            or any(
                marker in message
                for marker in ("为什么", "怎么", "能不能", "是否", "请问", "哪一步", "哪里")
            )
        )
        is_explicit_question = (
            (has_question_form or (
                state.get("current_teaching_step") == "question_invitation"
                and not wants_practice
            ))
            and not is_understood
            and not is_remediation
            and (not is_confused or is_question_window)
        )
        question_hits = _search(
            connection, session["workspace_id"], message, plan_id=session["plan_id"]
        ) if is_explicit_question else []
        question_conflicts = (
            _matching_conflicts(
                connection,
                session["workspace_id"],
                message,
                plan_id=session["plan_id"],
            )
            if is_explicit_question
            else []
        )
        if question_conflicts:
            action = "source_conflict"
            citations = [
                question_conflicts[0]["evidence_a"],
                question_conflicts[0]["evidence_b"],
            ]
            content = (
                f"资料存在冲突：{question_conflicts[0]['summary']}"
                "我会保留两份原文证据，在冲突解决前不选择其中一项作为确定事实。"
                "\n\n你可以继续追问；确认清楚后说“开始练习”。"
            )
            state["current_teaching_step"] = "question_invitation"
            state["next_action"] = "answer_question_or_start_practice"
            state["autonomy"] = {
                "mode": "teacher_led_question_dialogue",
                "awaiting_learner": True,
                "reason": "follow_up_question_invited",
            }
        elif is_explicit_question and not question_hits:
            action = "insufficient_evidence"
            citations = []
            content = (
                "资料中无法确认。当前上传资料没有足够证据支持这个问题，我不会猜测或编造。"
                "你可以换一种问法、补充资料，或者继续追问我能从现有资料确认的部分；"
                "确认清楚后说“开始练习”。"
            )
            state["current_teaching_step"] = "question_invitation"
            state["next_action"] = "answer_question_or_start_practice"
            state["autonomy"] = {
                "mode": "teacher_led_question_dialogue",
                "awaiting_learner": True,
                "reason": "follow_up_question_invited",
            }
        elif is_explicit_question:
            action = "grounded_answer"
            citations = question_hits
            learner_context = state.get("learner_context") or {}
            interests = str(learner_context.get("interests") or "").strip()
            asks_for_example = any(
                marker in message
                for marker in ("例子", "举例", "怎么理解", "什么意思", "为什么", "怎么做")
            )
            analogy = (
                f"\n\n帮助理解的小例子（不是资料原文）：可以先把它放进“{interests[:40]}”这个你熟悉的场景，"
                "只看输入、处理过程和可验证结果三步；我会在下一句把这个类比逐项映射回资料。"
                if asks_for_example and interests
                else ""
            )
            content = (
                f"你问的是：“{message[:240]}”\n\n"
                f"直接回答：结合当前知识点，{point['explanation'][:280]}"
                f"\n\n资料依据：{question_hits[0]['quote'][:360]}"
                f"{analogy}"
                "\n\n这是否回答了你真正卡住的那一步？如果没有，请指出哪一句不清楚，我会换一种讲法。"
            )
            state["current_teaching_step"] = "question_invitation"
            state["next_action"] = "answer_question_or_start_practice"
            state["autonomy"] = {
                "mode": "teacher_led_question_dialogue",
                "awaiting_learner": True,
                "reason": "follow_up_question_invited",
            }
        elif state.get("current_teaching_step") == "daily_briefing":
            action = "explain"
            plan_receipt = json.loads(connection.execute(
                "SELECT receipt FROM active_plans WHERE id=?", (session["plan_id"],)
            ).fetchone()["receipt"])
            coding_foundation = (
                _coding_foundation_from_reply(message)
                if _requires_executable_verification(str(point["acceptance_method"]))
                else "not_applicable"
            )
            learner_context = dict(state.get("learner_context") or {})
            learner_context["coding_foundation"] = coding_foundation
            state["learner_context"] = learner_context
            if coding_foundation == "none":
                citations = _merge_citations(
                    citations,
                    json.loads(point["source_evidence"]),
                )
                content = (
                    f"收到：你现在不会写代码，这完全可以。学习“{point['name']}”时，"
                    "这一轮先不写代码，也不会把最终验收直接丢给你。"
                    f"\n\n先只建立直觉：{point['explanation'][:320]}"
                    " 现在只回答一个很小的问题：这里要处理的对象是什么、希望它发生什么变化？"
                    "各说一个词也可以；最终编码验收会等到理解和引导练习之后。"
                )
            else:
                content, citations = _opening(connection, task, plan_receipt)
                content = (
                    f"收到，你目前对“{point['name']}”的感觉是：{message[:120]}。"
                    f"\n\n{content}"
                    "\n\n我先停在这里。哪一句不清楚就直接问，或者回复“继续”。"
                )
            state["current_teaching_step"] = "concept_check"
            state["next_action"] = "interpret_learner_feedback"
            state["autonomy"] = {
                "mode": "teacher_led_dialogue",
                "awaiting_learner": True,
                "reason": "learner_can_interrupt",
            }
        elif state.get("current_teaching_step") == "point_readiness":
            action = "explain"
            plan_receipt = json.loads(connection.execute(
                "SELECT receipt FROM active_plans WHERE id=?", (session["plan_id"],)
            ).fetchone()["receipt"])
            content, citations = _opening(connection, task, plan_receipt)
            content = (
                f"收到。你对这一点的当前感觉是：“{message[:120]}”。"
                f"\n\n{content}"
            )
            state["current_teaching_step"] = "concept_check"
            state["next_action"] = "interpret_learner_feedback"
            state["autonomy"] = {
                "mode": "teacher_led_dialogue",
                "awaiting_learner": True,
                "reason": "learner_feedback_required",
            }
        elif (
            state.get("current_teaching_step") == "concept_check"
            and not is_confused
        ):
            if _formative_feedback_ready(message):
                action = "guided_practice"
                content = (
                    f"我听到你目前的理解是：“{message[:180]}”。这足够进入下一小步，"
                    "但还不等于已经掌握。"
                    f"\n\n现在只做一个最小应用：请针对“{point['name']}”给出一个具体场景或关键步骤，"
                    f"并说明它怎样符合资料中的这个核心：{point['explanation'][:220]}"
                    "\n\n写到你会的部分就停；我会根据这次回答判断是推进、纠正还是换一种讲法。"
                )
                state["current_teaching_step"] = "check_understanding"
                state["next_action"] = "evaluate_answer"
                state["autonomy"] = {
                    "mode": "teacher_led_until_learner_input",
                    "awaiting_learner": True,
                    "reason": "learner_practice_required",
                }
            else:
                action = "clarify_feedback"
                content = (
                    f"我收到的是：“{message[:160]}”，但这还不足以判断你具体会到哪一步，"
                    "所以我不会直接把你推到后面。"
                    f"\n\n我们把问题再缩小：关于“{point['name']}”，"
                    "你现在能说出“它要处理什么对象”或“它希望得到什么结果”中的任意一个吗？"
                    "只答一个短句就可以。"
                )
                state["current_teaching_step"] = "concept_check"
                state["next_action"] = "interpret_learner_feedback"
                state["autonomy"] = {
                    "mode": "teacher_led_dialogue",
                    "awaiting_learner": True,
                    "reason": "more_specific_feedback_required",
                }
        elif is_question_window and wants_practice:
            action = "guided_practice"
            state["current_teaching_step"] = "check_understanding"
            state["next_action"] = "evaluate_answer"
            state["autonomy"] = {
                "mode": "teacher_led_until_learner_input",
                "awaiting_learner": True,
                "reason": "learner_practice_required",
            }
            content = (
                f"好，现在进入“{point['name']}”的引导练习。"
                f"\n\n先按这个核心开始：{point['explanation'][:260]}"
                f"\n\n请你用自己的话说明思路，给出一个具体应用或关键步骤，"
                f"并完成验收条件：{point['acceptance_method']}。"
                "遇到任何一步不会，可以立刻提问，我会暂停验收并继续答疑。"
            )
        elif is_confused and not is_remediation:
            state["unresolved_confusions"].append(
                {"knowledge_point_id": point["id"], "message": message, "created_at": _now()}
            )
            remediation_task = _create_remediation_task(
                connection,
                session=session,
                state=state,
                task=task,
                point=point,
                trigger_reason="learner_confused",
            )
            state["remediation_stack"].append(
                {
                    "route_index": route_index,
                    "teaching_step": state["current_teaching_step"],
                    "remediation_task_id": remediation_task["id"],
                    "estimated_task_minutes": remediation_task[
                        "estimated_task_minutes"
                    ],
                    "origin_task_id": task["id"],
                    "trigger_reason": "learner_confused",
                }
            )
            state["current_teaching_step"] = "remediation_explain"
            state["next_action"] = "remediation_example"
            state["estimated_task_minutes"] = remediation_task[
                "estimated_task_minutes"
            ]
            action = "remediation_explain"
            content = (
                f"已暂停主路线并保存位置（第 {route_index + 1}/{len(tasks)} 项）。"
                f"\n\n分层补讲：先把“{point['name']}”缩小成三件事：它是什么、材料说明了哪些关键关系或步骤、"
                f"怎样用当前验收条件证明自己会了。当前验收条件是：{point['acceptance_method']}"
            )
        elif is_remediation and is_understood:
            saved = state["remediation_stack"].pop()
            remediation_task_id = str(saved.get("remediation_task_id") or "")
            if remediation_task_id:
                remediation_task = connection.execute(
                    "SELECT * FROM active_plan_tasks WHERE id=?",
                    (remediation_task_id,),
                ).fetchone()
                if remediation_task:
                    connection.execute(
                        """UPDATE active_plan_tasks
                           SET status='completed',
                               completed_task_minutes=estimated_task_minutes
                           WHERE id=?""",
                        (remediation_task_id,),
                    )
                    if remediation_task_id not in state["completed_tasks"]:
                        state["completed_tasks"].append(remediation_task_id)
                        state["completed_task_minutes"] = int(
                            state.get("completed_task_minutes", 0)
                        ) + int(remediation_task["estimated_task_minutes"])
                        state["today_remaining_task_minutes"] = max(
                            0,
                            int(state.get("today_remaining_task_minutes", 0))
                            - int(remediation_task["estimated_task_minutes"]),
                        )
            state["current_teaching_step"] = "check_understanding"
            state["next_action"] = "check_understanding"
            state["estimated_task_minutes"] = task["estimated_task_minutes"]
            state["unresolved_confusions"] = [
                item for item in state["unresolved_confusions"] if item["knowledge_point_id"] != point["id"]
            ]
            action = "resume_main_route"
            content = (
                f"补救教学完成，已恢复主路线原位置（第 {saved['route_index'] + 1}/{len(tasks)} 项）。"
                f"\n\n现在做一次最小理解检查：请不用照抄资料，用自己的话解释“{point['name']}”，"
                f"并说明你会如何满足这个验收条件：{point['acceptance_method']}"
            )
            plan_row = connection.execute(
                "SELECT * FROM active_plans WHERE id=?",
                (session["plan_id"],),
            ).fetchone()
            _refresh_plan_calendar(
                connection, session["plan_id"], json.loads(plan_row["receipt"])
            )
        elif is_remediation:
            state["current_teaching_step"] = "remediation_example"
            state["next_action"] = "remediation_recheck"
            action = "remediation_example"
            content = (
                f"更简单的例子：只保留资料中关于“{point['name']}”的最小事实——{point['explanation'][:240]}"
                f"\n\n请先举一个符合这个事实的具体场景，再说明它怎样满足验收条件：{point['acceptance_method']}"
                "。如果仍不理解，我会继续降低解释层级或标记需要额外资料。"
            )
        elif state["current_teaching_step"] == "example":
            state["current_teaching_step"] = "check_understanding"
            state["next_action"] = "evaluate_answer"
            action = "check_understanding"
            content = (
                f"理解检查：请不用照抄资料，用自己的话解释“{point['name']}”，"
                f"给出一个具体应用或例子，并完成这个验收条件：{point['acceptance_method']}"
            )
        elif state["current_teaching_step"] == "check_understanding" and (
            is_understood or len(message.strip()) >= 6
        ):
            learning_phase = str(task["learning_phase"] or "integrated_initial")
            phase_part_index = int(task["phase_part_index"] or 1)
            phase_part_count = int(task["phase_part_count"] or 1)
            completes_independent_phase = (
                learning_phase in {"integrated_initial", "independent_application"}
                and phase_part_index >= phase_part_count
            )
            score, passed, grading_checks, evaluation = await _evaluate_understanding(
                message=message,
                point=point,
                citations=citations,
                provider_session_id=requested_provider_session_id,
            )
            mastery_evidence_id = _id("mastery")
            connection.execute(
                """INSERT INTO active_mastery_evidence
                   (id,session_id,knowledge_point_id,evidence_type,score,passed,evidence,citations,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    mastery_evidence_id,
                    session_id,
                    point["id"],
                    "provider_grounded_rubric" if evaluation.get("mode") == "real" else "unverified_recall",
                    score,
                    1 if passed else 0,
                    json.dumps(
                        {
                            "learner_answer": message,
                            "task_id": task["id"],
                            "learning_phase": learning_phase,
                            "phase_part_index": phase_part_index,
                            "phase_part_count": phase_part_count,
                            "grading_checks": grading_checks,
                            "evaluation": evaluation,
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(citations, ensure_ascii=False),
                    _now(),
                ),
            )
            state["mastery_evidence"].append(
                {
                    "knowledge_point_id": point["id"],
                    "score": score,
                    "passed": passed,
                    "evidence": message,
                    "task_id": task["id"],
                    "learning_phase": learning_phase,
                    "grading_checks": grading_checks,
                    "evaluation": evaluation,
                }
            )
            if passed:
                if completes_independent_phase:
                    connection.execute(
                        "UPDATE active_knowledge_points SET mastery=MAX(mastery,?) WHERE id=?",
                        (score, point["id"]),
                    )
                connection.execute(
                    """UPDATE active_plan_tasks SET status='completed',
                       completed_task_minutes=estimated_task_minutes WHERE id=?""",
                    (task["id"],),
                )
                state["completed_tasks"].append(task["id"])
                state["completed_task_minutes"] = int(state.get("completed_task_minutes", 0)) + int(
                    task["estimated_task_minutes"]
                )
                state["today_remaining_task_minutes"] = max(
                    0,
                    int(state["today_remaining_task_minutes"]) - int(task["estimated_task_minutes"]),
                )
                if completes_independent_phase:
                    review_due = (date.today() + timedelta(days=1)).isoformat()
                    plan_receipt = connection.execute(
                        "SELECT receipt FROM active_plans WHERE id=?",
                        (session["plan_id"],),
                    ).fetchone()
                    if plan_receipt:
                        review_due = _planned_review_due_date(
                            connection,
                            plan_id=session["plan_id"],
                            knowledge_point_id=point["id"],
                            cycle=1,
                            fallback=review_due,
                        )
                    now = _now()
                    review_total_minutes = _review_total_minutes_for_point(
                        connection, point
                    )
                    first_review_minutes = _review_occurrence_minutes(
                        review_total_minutes, 1
                    )
                    connection.execute(
                        """INSERT INTO active_review_items
                           (id,session_id,workspace_id,plan_id,knowledge_point_id,source_task_id,
                            source_mastery_evidence_id,due_date,interval_step,interval_days,
                            review_total_minutes,estimated_task_minutes,status,
                            attempt_count,success_streak,last_score,last_passed,
                            last_attempted_at,created_at,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,NULL,NULL,NULL,?,?)
                           ON CONFLICT(session_id,knowledge_point_id) DO UPDATE SET
                             plan_id=excluded.plan_id,
                             source_task_id=excluded.source_task_id,
                             source_mastery_evidence_id=excluded.source_mastery_evidence_id,
                             due_date=excluded.due_date,
                             interval_step=0,
                             interval_days=1,
                             review_total_minutes=excluded.review_total_minutes,
                             estimated_task_minutes=excluded.estimated_task_minutes,
                             status='pending',
                             attempt_count=0,
                             success_streak=0,
                             last_score=NULL,
                             last_passed=NULL,
                             last_attempted_at=NULL,
                             updated_at=excluded.updated_at""",
                        (
                            _id("review"),
                            session_id,
                            session["workspace_id"],
                            session["plan_id"],
                            point["id"],
                            task["id"],
                            mastery_evidence_id,
                            review_due,
                            0,
                            1,
                            review_total_minutes,
                            first_review_minutes,
                            "pending",
                            now,
                            now,
                        ),
                    )
                    review_row = connection.execute(
                        """SELECT * FROM active_review_items
                           WHERE session_id=? AND knowledge_point_id=?""",
                        (session_id, point["id"]),
                    ).fetchone()
                    _materialize_review_occurrence(
                        connection,
                        session=session,
                        review=review_row,
                        point=point,
                        due_date=review_due,
                        cycle=1,
                        trigger_reason="independent_application_passed",
                    )
                    plan_row = connection.execute(
                        "SELECT * FROM active_plans WHERE id=?",
                        (session["plan_id"],),
                    ).fetchone()
                    _replan_pending_in_transaction(
                        connection,
                        session=session,
                        plan=plan_row,
                        receipt=json.loads(plan_row["receipt"]),
                        affected_from=date.today(),
                        trigger_name="review_enqueued",
                        reason=f"知识点“{point['name']}”独立通过，加入 +1 日复习。",
                        state=state,
                    )
                    _sync_pending_reviews(connection, session_id, state)
                if route_index + 1 < len(tasks):
                    route_index += 1
                    next_task = tasks[route_index]
                    next_point = connection.execute(
                        "SELECT * FROM active_knowledge_points WHERE id=?",
                        (next_task["knowledge_point_id"],),
                    ).fetchone()
                    state["route_index"] = route_index
                    state["active_task_id"] = next_task["id"]
                    state["current_knowledge_point_id"] = next_point["id"]
                    state["current_teaching_step"] = "point_readiness"
                    state["next_action"] = "ask_point_readiness"
                    state["estimated_task_minutes"] = next_task["estimated_task_minutes"]
                    action = "advance_route"
                    citations = json.loads(next_point["source_evidence"])
                    if completes_independent_phase:
                        progress_message = (
                            f"独立应用阶段通过（{score:.0%}），知识点掌握度已更新，"
                            f"并安排 {review_due} 的首次复习。"
                        )
                    else:
                        progress_message = (
                            f"“{learning_phase}”阶段通过（{score:.0%}）；"
                            "该阶段任务分钟已完成，但尚未把整个知识点标记为掌握。"
                        )
                    content = (
                        f"{progress_message}"
                        f"\n\n下一项是“{next_point['name']}”。在我开始讲之前，"
                        "请只告诉我：你是完全没接触过、只有一点印象，还是已经能用自己的话解释？"
                    )
                else:
                    state["route_index"] = len(tasks)
                    state["current_teaching_step"] = "completed"
                    state["next_action"] = "review"
                    action = "complete_route"
                    content = "当前计划路线已完成；下一步进入基于掌握证据的间隔复习。"
            else:
                state["unresolved_confusions"].append(
                    {
                        "knowledge_point_id": point["id"],
                        "message": message,
                        "created_at": _now(),
                        "reason": "failed_assessment",
                    }
                )
                remediation_task = _create_remediation_task(
                    connection,
                    session=session,
                    state=state,
                    task=task,
                    point=point,
                    trigger_reason="failed_assessment",
                )
                state["current_teaching_step"] = "remediation_explain"
                state["remediation_stack"].append(
                    {
                        "route_index": route_index,
                        "teaching_step": "check_understanding",
                        "remediation_task_id": remediation_task["id"],
                        "estimated_task_minutes": remediation_task[
                            "estimated_task_minutes"
                        ],
                        "origin_task_id": task["id"],
                        "trigger_reason": "failed_assessment",
                    }
                )
                state["estimated_task_minutes"] = remediation_task[
                    "estimated_task_minutes"
                ]
                state["next_action"] = "remediation_example"
                action = "remediation_explain"
                if evaluation.get("mode") == "unverified_without_real_provider":
                    content = (
                        "这次回答已保存，但当前没有真实 Provider 或领域验证器，不能把自报理解当作掌握通过。"
                        "主路线已暂停；连接真实 Provider 后可按资料与验收条件评分，或继续补讲。"
                    )
                else:
                    content = (
                        "本次理解检查未达到系统验收阈值，主路线已暂停；"
                        f"评估说明：{evaluation.get('rationale') or '需要补足验收条件中的可观察证据。'}"
                    )
        elif state.get("current_teaching_step") == "check_understanding":
            action = "clarify_feedback"
            citations = []
            content = (
                "这条反馈太短，我还不能据此判断你是否理解，因此不会擅自推进。"
                f"\n\n请用一个完整短句回答刚才关于“{point['name']}”的小练习；"
                "如果不会，直接说具体卡在哪一步，我会立即换一种讲法。"
            )
            state["next_action"] = "evaluate_answer"
            state["autonomy"] = {
                "mode": "teacher_led_until_learner_input",
                "awaiting_learner": True,
                "reason": "substantive_answer_required",
            }
        else:
            hits = _search(
                connection, session["workspace_id"], message, plan_id=session["plan_id"]
            )
            if not hits:
                action = "insufficient_evidence"
                citations = []
                content = "资料中无法确认。当前上传资料没有足够证据支持这个问题，我不会猜测或编造。"
            else:
                citations = hits
                action = "grounded_answer"
                content = f"根据上传资料，可确认的相关片段如下：{hits[0]['quote'][:240]}"

        _sync_teacher_learner_model(
            connection,
            workspace_id=session["workspace_id"],
            state=state,
        )
        content, teacher_generation = await _render_teacher_content(
            draft=content,
            action=action,
            citations=citations,
            provider_session_id=requested_provider_session_id,
            state=state,
        )
        state["teacher_generation"] = teacher_generation
        teacher_memory = dict(state.get("teacher_memory") or {})
        teacher_memory["last_interaction"] = action
        if is_explicit_question and message:
            questions = list(teacher_memory.get("learner_questions") or [])
            questions.append(message[:400])
            teacher_memory["learner_questions"] = questions[-20:]
        if is_confused:
            unresolved = list(teacher_memory.get("unresolved_points") or [])
            unresolved.append(str(point["name"]))
            teacher_memory["unresolved_points"] = list(dict.fromkeys(unresolved))[-20:]
        teacher_memory["recent_turns"] = (
            list(teacher_memory.get("recent_turns") or [])
            + [{"role": "teacher", "action": action, "content": content[:600]}]
        )[-12:]
        state["teacher_memory"] = teacher_memory
        _record_message(connection, session_id, "teacher", action, content, citations)
        connection.execute(
            """UPDATE active_teaching_sessions
               SET state=?,updated_at=?,actual_elapsed_minutes=actual_elapsed_minutes+? WHERE id=?""",
            (json.dumps(state, ensure_ascii=False), _now(), elapsed, session_id),
        )
        connection.commit()
        return _public_session(connection, session_id)


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    with _connect() as connection:
        return _public_session(connection, session_id)


@router.post("/sessions/{session_id}/focus")
def focus_session_task(
    session_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Move an existing plan-scoped teacher to a selected calendar task.

    The session, memory and retrieval scope stay bound to the same plan.  A
    task from another plan is therefore rejected instead of silently changing
    the teacher's knowledge base.
    """
    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        _error("ACTIVE_TASK_REQUIRED", "请选择要进入的日历任务", 422)
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        tasks = _task_rows(connection, session["plan_id"])
        route_index = next(
            (index for index, task in enumerate(tasks) if task["id"] == task_id),
            None,
        )
        if route_index is None:
            _error(
                "ACTIVE_TASK_OUTSIDE_SESSION_PLAN",
                "所选任务不属于当前教师会话的计划，不能混用另一任务的知识库或记忆。",
                409,
            )
        task = tasks[route_index]
        state = json.loads(session["state"])
        state["route_index"] = route_index
        state["route_length"] = len(tasks)
        state["current_knowledge_point_id"] = task["knowledge_point_id"]
        state["current_teaching_step"] = "daily_briefing"
        state["next_action"] = "explain_current_point"
        state["estimated_task_minutes"] = task["estimated_task_minutes"]
        state["active_task_id"] = task["id"]
        state["today_remaining_task_minutes"] = int(
            connection.execute(
                """SELECT COALESCE(SUM(estimated_task_minutes-completed_task_minutes),0)
                   FROM active_plan_tasks
                   WHERE plan_id=? AND day_date=? AND status<>'completed'""",
                (session["plan_id"], task["day_date"]),
            ).fetchone()[0]
        )
        state["today_plan"] = _daily_route_summary(
            connection, session["plan_id"], task["day_date"]
        )
        state["autonomy"] = {
            "mode": "teacher_led_until_learner_input",
            "awaiting_learner": True,
            "reason": "learner_foundation_required",
        }
        teacher_memory = dict(state.get("teacher_memory") or {})
        teacher_memory["last_interaction"] = "calendar_task_selected"
        state["teacher_memory"] = teacher_memory
        _sync_teacher_learner_model(
            connection,
            workspace_id=session["workspace_id"],
            state=state,
        )
        readiness, citations = _point_readiness_prompt(connection, task=task)
        _record_message(
            connection,
            session_id,
            "teacher",
            "daily_briefing",
            readiness,
            citations,
        )
        connection.execute(
            """UPDATE active_teaching_sessions
               SET state=?,updated_at=? WHERE id=?""",
            (json.dumps(state, ensure_ascii=False), _now(), session_id),
        )
        connection.commit()
        return _public_session(connection, session_id)


def _review_as_of(value: Any) -> date:
    try:
        return date.fromisoformat(str(value or date.today().isoformat()))
    except ValueError:
        _error("INVALID_REVIEW_DATE", "复习日期必须是有效 ISO 日期", 422)


@router.get("/sessions/{session_id}/reviews")
def list_session_reviews(
    session_id: str, status: str = "", as_of: str = ""
) -> dict[str, Any]:
    with _connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone():
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        items = _review_items(connection, session_id)
        if status:
            allowed = {"pending", "due", "in_progress", "completed"}
            if status not in allowed:
                _error("INVALID_REVIEW_STATUS", "复习状态无效", 422)
            if status == "due":
                day = _review_as_of(as_of)
                items = [
                    item
                    for item in items
                    if item["status"] in {"pending", "in_progress"}
                    and date.fromisoformat(item["due_date"]) <= day
                ]
            else:
                items = [item for item in items if item["status"] == status]
        return {"session_id": session_id, "items": items}


@router.post("/sessions/{session_id}/reviews/{review_id}/start")
async def start_review(
    session_id: str,
    review_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    as_of = _review_as_of(body.get("as_of"))
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        incomplete_initial = connection.execute(
            """SELECT COUNT(*) FROM active_plan_tasks
               WHERE plan_id=? AND task_type='initial_learning'
                 AND status<>'completed'""",
            (session["plan_id"],),
        ).fetchone()[0]
        if incomplete_initial:
            incomplete_initial_points = connection.execute(
                """SELECT COUNT(DISTINCT knowledge_point_id)
                   FROM active_plan_tasks
                   WHERE plan_id=? AND task_type='initial_learning'
                     AND status<>'completed'""",
                (session["plan_id"],),
            ).fetchone()[0]
            _error(
                "INITIAL_LEARNING_NOT_COMPLETE",
                "正式复习只能在第一遍全部完成后开始；当天首学后的巩固不计入正式复习阶段。",
                409,
                incomplete_initial_task_count=int(incomplete_initial),
                incomplete_initial_knowledge_point_count=int(
                    incomplete_initial_points
                ),
            )
        review = connection.execute(
            """SELECT review.*,point.name AS knowledge_point_name,
                      point.acceptance_method,point.source_evidence
               FROM active_review_items AS review
               JOIN active_knowledge_points AS point
                 ON point.id=review.knowledge_point_id
               WHERE review.id=? AND review.session_id=?""",
            (review_id, session_id),
        ).fetchone()
        if not review:
            _error("REVIEW_NOT_FOUND", "复习任务不存在", 404)
        if review["status"] == "completed":
            _error("REVIEW_ALREADY_COMPLETED", "该间隔复习链已经完成", 409)
        if date.fromisoformat(review["due_date"]) > as_of:
            _error(
                "REVIEW_NOT_DUE",
                "该复习尚未到期，未学过或未到期内容不会提前进入今日复习。",
                409,
                due_date=review["due_date"],
            )
        scheduled_task_id = str(review["scheduled_task_id"] or "")
        scheduled_task = (
            connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=? AND linked_review_item_id=?",
                (scheduled_task_id, review_id),
            ).fetchone()
            if scheduled_task_id
            else None
        )
        if not scheduled_task:
            _error(
                "REVIEW_NOT_SCHEDULED",
                "复习队列存在，但尚未生成可执行的日历任务。",
                409,
            )
        if scheduled_task["status"] == "unscheduled":
            _error(
                "REVIEW_OUTSIDE_CAPACITY",
                "该复习尚未能排入截止日期前的可用容量。",
                409,
                due_date=review["due_date"],
            )
        scheduled_for = date.fromisoformat(
            str(review["scheduled_date"] or scheduled_task["day_date"])
        )
        if scheduled_for > as_of:
            _error(
                "REVIEW_NOT_SCHEDULED_TODAY",
                "该复习已到期，但根据日历容量安排在稍后的学习日。",
                409,
                scheduled_date=scheduled_for.isoformat(),
            )
        state = json.loads(session["state"])
        active_review = state.get("active_review")
        if active_review and active_review.get("id") != review_id:
            _error("OTHER_REVIEW_IN_PROGRESS", "请先完成当前复习任务", 409)
        provider_session_id = str(body.get("provider_session_id") or "").strip() or None
        bound_provider_session_id = state.get("provider_session_id")
        if provider_session_id and bound_provider_session_id and provider_session_id != bound_provider_session_id:
            _error(
                "TEACHING_PROVIDER_SESSION_MISMATCH",
                "该教学会话已绑定另一条 Provider 会话。",
                409,
            )
        if provider_session_id and not bound_provider_session_id:
            state["provider_session_id"] = provider_session_id
        state.setdefault(
            "review_return",
            {
                "route_index": state.get("route_index"),
                "current_knowledge_point_id": state.get("current_knowledge_point_id"),
                "current_teaching_step": state.get("current_teaching_step"),
                "next_action": state.get("next_action"),
                "estimated_task_minutes": state.get("estimated_task_minutes"),
            },
        )
        state["active_review"] = {
            "id": review_id,
            "plan_task_id": scheduled_task_id,
            "knowledge_point_id": review["knowledge_point_id"],
            "due_date": review["due_date"],
            "interval_days": review["interval_days"],
            "interval_step": review["interval_step"],
        }
        state["current_knowledge_point_id"] = review["knowledge_point_id"]
        state["current_teaching_step"] = "review_recall"
        state["next_action"] = "review_check"
        state["estimated_task_minutes"] = review["estimated_task_minutes"]
        connection.execute(
            "UPDATE active_review_items SET status='in_progress',updated_at=? WHERE id=?",
            (_now(), review_id),
        )
        connection.execute(
            "UPDATE active_plan_tasks SET status='in_progress' WHERE id=?",
            (scheduled_task_id,),
        )
        _sync_pending_reviews(connection, session_id, state)
        citations = json.loads(review["source_evidence"])
        content = (
            f"到期复习：“{review['knowledge_point_name']}”。请先不看答案，用自己的话回忆核心关系或步骤，"
            f"再完成这项验收：{review['acceptance_method']}。"
            f"本轮预计 {review['estimated_task_minutes']} 个标准任务分钟。"
        )
        content, teacher_generation = await _render_teacher_content(
            draft=content,
            action="review_recall",
            citations=citations,
            provider_session_id=provider_session_id,
            state=state,
        )
        state["teacher_generation"] = teacher_generation
        _record_message(connection, session_id, "teacher", "review_recall", content, citations)
        connection.execute(
            "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), _now(), session_id),
        )
        connection.commit()
        return {
            "review": next(
                item for item in _review_items(connection, session_id) if item["id"] == review_id
            ),
            "session": _public_session(connection, session_id),
        }


@router.post("/sessions/{session_id}/reviews/{review_id}/submit")
async def submit_review(
    session_id: str, review_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    message = str(body.get("message") or "").strip()
    if not message:
        _error("REVIEW_ANSWER_REQUIRED", "请输入复习回答", 422)
    assistance_level = str(body.get("assistance_level") or "none").strip()
    if assistance_level not in {"none", "hinted", "guided"}:
        _error("INVALID_ASSISTANCE_LEVEL", "assistance_level 必须是 none、hinted 或 guided", 422)
    reviewed_on = _review_as_of(body.get("as_of"))
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        state = json.loads(session["state"])
        if (state.get("active_review") or {}).get("id") != review_id:
            _error("REVIEW_NOT_STARTED", "请先启动该复习任务", 409)
        review = connection.execute(
            "SELECT * FROM active_review_items WHERE id=? AND session_id=?",
            (review_id, session_id),
        ).fetchone()
        if not review:
            _error("REVIEW_NOT_FOUND", "复习任务不存在", 404)
        review_task_id = str(review["scheduled_task_id"] or "")
        review_task = (
            connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=? AND linked_review_item_id=?",
                (review_task_id, review_id),
            ).fetchone()
            if review_task_id
            else None
        )
        if not review_task:
            _error("REVIEW_NOT_SCHEDULED", "当前复习没有对应的计划任务。", 409)
        if str(review["scheduled_date"] or review_task["day_date"]) > reviewed_on.isoformat():
            _error(
                "REVIEW_NOT_SCHEDULED_TODAY",
                "当前复习轮次尚未到排定日期。",
                409,
                scheduled_date=review["scheduled_date"] or review_task["day_date"],
            )
        point = connection.execute(
            "SELECT * FROM active_knowledge_points WHERE id=?",
            (review["knowledge_point_id"],),
        ).fetchone()
        provider_session_id = str(body.get("provider_session_id") or "").strip() or None
        bound_provider_session_id = state.get("provider_session_id")
        if provider_session_id and bound_provider_session_id and provider_session_id != bound_provider_session_id:
            _error(
                "TEACHING_PROVIDER_SESSION_MISMATCH",
                "该教学会话已绑定另一条 Provider 会话。",
                409,
            )
        if bound_provider_session_id and not provider_session_id:
            provider_session_id = bound_provider_session_id
        try:
            elapsed = max(0.0, min(60.0, float(body.get("actual_elapsed_minutes") or 0)))
        except (TypeError, ValueError):
            _error("INVALID_ACTUAL_ELAPSED_MINUTES", "实际学习分钟必须是非负数字", 422)
        if elapsed > 0:
            activity_event_id = str(body.get("activity_event_id") or "").strip()
            if not activity_event_id:
                _error(
                    "ACTIVITY_EVENT_ID_REQUIRED",
                    "记录实际学习分钟时必须提供 activity_event_id。",
                    422,
                )
            inserted = connection.execute(
                """INSERT OR IGNORE INTO active_task_time_events
                   (id,event_id,task_id,session_id,elapsed_minutes,activity_kind,recorded_at)
                   VALUES (?,?,?,?,?,'review',?)""",
                (
                    _id("time_event"),
                    activity_event_id,
                    review_task_id,
                    session_id,
                    elapsed,
                    _now(),
                ),
            ).rowcount
            if inserted == 0:
                elapsed = 0.0
        citations = json.loads(point["source_evidence"])
        score, independently_passed, checks, evaluation = await _evaluate_understanding(
            message=message,
            point=point,
            citations=citations,
            provider_session_id=provider_session_id,
        )
        passed = independently_passed and assistance_level == "none"
        evidence_id = _id("mastery")
        attempt_no = int(review["attempt_count"]) + 1
        evidence_payload = {
            "learner_answer": message,
            "review_item_id": review_id,
            "attempt_no": attempt_no,
            "assistance_level": assistance_level,
            "grading_checks": checks,
            "evaluation": evaluation,
        }
        connection.execute(
            """INSERT INTO active_mastery_evidence
               (id,session_id,knowledge_point_id,evidence_type,score,passed,evidence,citations,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                evidence_id,
                session_id,
                point["id"],
                "delayed_independent_review"
                if evaluation.get("mode") == "real"
                else "unverified_delayed_review",
                score,
                int(passed),
                json.dumps(evidence_payload, ensure_ascii=False),
                json.dumps(citations, ensure_ascii=False),
                _now(),
            ),
        )
        previous_step = int(review["interval_step"])
        success_streak = int(review["success_streak"])
        plan_receipt_row = connection.execute(
            "SELECT receipt FROM active_plans WHERE id=?",
            (session["plan_id"],),
        ).fetchone()
        configured_review_rounds = max(
            2,
            min(
                4,
                int(
                    json.loads(plan_receipt_row["receipt"]).get("review_rounds") or 2
                )
                if plan_receipt_row
                else 2,
            ),
        )
        if passed:
            next_step = previous_step + 1
            success_streak += 1
            if next_step >= configured_review_rounds:
                next_interval = None
                next_due = None
                next_status = "completed"
            else:
                next_interval = (1, 3, 7)[min(2, next_step - 1)]
                fallback_due = (
                    reviewed_on + timedelta(days=next_interval)
                ).isoformat()
                next_due = _planned_review_due_date(
                    connection,
                    plan_id=session["plan_id"],
                    knowledge_point_id=point["id"],
                    cycle=next_step + 1,
                    fallback=fallback_due,
                )
                next_status = "pending"
            completed_minutes = int(review["estimated_task_minutes"])
            connection.execute(
                "UPDATE active_knowledge_points SET mastery=MAX(mastery,?) WHERE id=?",
                (score, point["id"]),
            )
        else:
            next_step = previous_step
            success_streak = 0
            next_interval = 1
            next_due = (reviewed_on + timedelta(days=1)).isoformat()
            next_status = "pending"
            completed_minutes = 0
        connection.execute(
            """INSERT INTO active_review_attempts
               (id,review_item_id,session_id,plan_task_id,mastery_evidence_id,attempt_no,score,passed,
                assistance_level,evaluator_kind,estimated_task_minutes,completed_task_minutes,
                actual_elapsed_minutes,outcome,reviewed_on,next_interval_days,next_due_date,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _id("review_attempt"),
                review_id,
                session_id,
                review_task_id,
                evidence_id,
                attempt_no,
                score,
                int(passed),
                assistance_level,
                str(evaluation.get("mode") or "unknown"),
                review["estimated_task_minutes"],
                completed_minutes,
                elapsed,
                "passed" if passed else "failed",
                reviewed_on.isoformat(),
                next_interval,
                next_due,
                _now(),
            ),
        )
        connection.execute(
            """UPDATE active_plan_tasks
               SET status=?,completed_task_minutes=?,
                   actual_elapsed_minutes=actual_elapsed_minutes+?
               WHERE id=?""",
            (
                "completed" if passed else "attempted_failed",
                completed_minutes,
                elapsed,
                review_task_id,
            ),
        )
        connection.execute(
            """UPDATE active_review_items
               SET due_date=?,interval_step=?,interval_days=?,status=?,attempt_count=?,
                   success_streak=?,last_score=?,last_passed=?,last_attempted_at=?,updated_at=?
               WHERE id=?""",
            (
                next_due or review["due_date"],
                next_step,
                next_interval or int(review["interval_days"]),
                next_status,
                attempt_no,
                success_streak,
                score,
                int(passed),
                _now(),
                _now(),
                review_id,
            ),
        )
        if next_status == "pending":
            updated_review = connection.execute(
                "SELECT * FROM active_review_items WHERE id=?",
                (review_id,),
            ).fetchone()
            next_cycle = next_step + 1
            next_review_task_id = _materialize_review_occurrence(
                connection,
                session=session,
                review=updated_review,
                point=point,
                due_date=str(next_due),
                cycle=next_cycle,
                trigger_reason="review_passed" if passed else "review_retry",
            )
            plan_row = connection.execute(
                "SELECT * FROM active_plans WHERE id=?",
                (session["plan_id"],),
            ).fetchone()
            _replan_pending_in_transaction(
                connection,
                session=session,
                plan=plan_row,
                receipt=json.loads(plan_row["receipt"]),
                affected_from=reviewed_on,
                trigger_name="review_rescheduled" if passed else "review_retry",
                reason=(
                    f"知识点“{point['name']}”进入下一次延迟复测。"
                    if passed
                    else f"知识点“{point['name']}”未独立通过，增加次日重试。"
                ),
                state=state,
            )
            if not passed:
                state.setdefault("active_review", {})["plan_task_id"] = next_review_task_id
        else:
            connection.execute(
                """UPDATE active_review_items
                   SET scheduled_task_id='',scheduled_date=NULL,updated_at=?
                   WHERE id=?""",
                (_now(), review_id),
            )
            plan_row = connection.execute(
                "SELECT * FROM active_plans WHERE id=?",
                (session["plan_id"],),
            ).fetchone()
            _refresh_plan_calendar(
                connection, session["plan_id"], json.loads(plan_row["receipt"])
            )
        state["actual_elapsed_minutes"] = round(
            float(state.get("actual_elapsed_minutes", 0)) + elapsed, 3
        )
        state["mastery_evidence"].append(
            {
                "knowledge_point_id": point["id"],
                "score": score,
                "passed": passed,
                "evidence": message,
                "review_item_id": review_id,
                "assistance_level": assistance_level,
                "evaluation": evaluation,
            }
        )
        if passed:
            restored = state.pop("review_return", {})
            state.pop("active_review", None)
            for key, value in restored.items():
                state[key] = value
            action = "review_completed" if next_status == "completed" else "review_rescheduled"
            if next_status == "completed":
                content = (
                    f"延迟独立复测通过（{score:.0%}），"
                    f"{configured_review_rounds} 轮完整复习已完成。"
                    "已恢复原学习路线。"
                )
            else:
                content = (
                    f"延迟独立复测通过（{score:.0%}），下一次复习安排在 {next_due}。"
                    "已恢复原学习路线。"
                )
        else:
            state["current_teaching_step"] = "review_remediation"
            state["next_action"] = "review_retry"
            action = "review_remediation"
            if assistance_level != "none":
                content = (
                    "本轮使用了提示或引导，因此不能记为独立掌握；"
                    f"已把下一次独立复测提前到 {next_due}。"
                )
            else:
                content = (
                    "本轮复习未达到独立验收门槛，掌握度没有提高；"
                    f"已把下一次复习提前到 {next_due}，并保留当前复习位置用于补救。"
                )
        _sync_pending_reviews(connection, session_id, state)
        _record_message(connection, session_id, "user", "review_answer", message, [])
        content, teacher_generation = await _render_teacher_content(
            draft=content,
            action=action,
            citations=citations,
            provider_session_id=provider_session_id,
            state=state,
        )
        state["teacher_generation"] = teacher_generation
        _record_message(connection, session_id, "teacher", action, content, citations)
        connection.execute(
            """UPDATE active_teaching_sessions
               SET state=?,updated_at=?,actual_elapsed_minutes=actual_elapsed_minutes+?
               WHERE id=?""",
            (json.dumps(state, ensure_ascii=False), _now(), elapsed, session_id),
        )
        connection.commit()
        return {
            "passed": passed,
            "review": next(
                item for item in _review_items(connection, session_id) if item["id"] == review_id
            ),
            "attempt": dict(
                connection.execute(
                    "SELECT * FROM active_review_attempts WHERE mastery_evidence_id=?",
                    (evidence_id,),
                ).fetchone()
            ),
            "session": _public_session(connection, session_id),
        }


def _remaining_projected_review_minutes(
    connection: sqlite3.Connection, plan: sqlite3.Row
) -> int:
    configured_review_rounds = max(
        2,
        min(4, int(json.loads(plan["receipt"]).get("review_rounds") or 2)),
    )
    initial_minutes = int(
        connection.execute(
            """SELECT COALESCE(SUM(estimated_task_minutes),0)
               FROM active_plan_tasks
               WHERE plan_id=? AND task_type='initial_learning'""",
            (plan["id"],),
        ).fetchone()[0]
    )
    baseline = max(0, int(plan["total_estimated_task_minutes"]) - initial_minutes)
    materialized_baseline = 0
    rows = connection.execute(
        """SELECT review.id,review.review_total_minutes,
                  COALESCE(MAX(task.review_cycle),0) AS max_cycle
           FROM active_review_items AS review
           LEFT JOIN active_plan_tasks AS task
             ON task.linked_review_item_id=review.id
           WHERE review.plan_id=?
           GROUP BY review.id,review.review_total_minutes""",
        (plan["id"],),
    ).fetchall()
    for row in rows:
        total = int(row["review_total_minutes"] or 0)
        for cycle in range(
            1,
            min(configured_review_rounds, int(row["max_cycle"] or 0)) + 1,
        ):
            materialized_baseline += _review_occurrence_minutes(total, cycle)
    return max(0, baseline - materialized_baseline)


def _replan_pending_in_transaction(
    connection: sqlite3.Connection,
    *,
    session: sqlite3.Row,
    plan: sqlite3.Row,
    receipt: dict[str, Any],
    affected_from: date,
    trigger_name: str,
    reason: str,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Authoritative shared replan used by manual, review and remediation flows."""
    from .generic_knowledge import calendar_capacities

    before_rows = connection.execute(
        """SELECT * FROM active_plan_tasks WHERE plan_id=?
           ORDER BY day_index,task_order,id""",
        (plan["id"],),
    ).fetchall()
    before_snapshot = [_task_snapshot(row) for row in before_rows]
    moved, directly_unscheduled, calendar = _reschedule_plan_work(
        connection,
        plan_id=plan["id"],
        receipt=receipt,
        affected_from=affected_from,
    )
    after_rows = connection.execute(
        """SELECT * FROM active_plan_tasks WHERE plan_id=?
           ORDER BY day_index,task_order,id""",
        (plan["id"],),
    ).fetchall()
    after_snapshot = [_task_snapshot(row) for row in after_rows]
    eligible_dates = {
        item["date"]: int(item["capacity_minutes"])
        for item in calendar_capacities(receipt)
        if item["date"] >= affected_from.isoformat()
    }
    fixed_minutes = sum(
        int(row["estimated_task_minutes"])
        for row in after_rows
        if row["day_date"] in eligible_dates
        and row["status"] not in {"planned", "unscheduled", "cancelled"}
    )
    pending_minutes = sum(
        int(row["estimated_task_minutes"])
        for row in after_rows
        if row["status"] in {"planned", "unscheduled"}
    )
    future_review_minutes = _remaining_projected_review_minutes(connection, plan)
    available_for_pending = max(0, sum(eligible_dates.values()) - fixed_minutes)
    quantity_shortfall = max(
        0, pending_minutes + future_review_minutes - available_for_pending
    )
    unscheduled = max(directly_unscheduled, quantity_shortfall)
    warning = (
        f"重排后仍有 {unscheduled} 个标准任务分钟无法在截止日期前容纳。"
        if unscheduled
        else ""
    )
    connection.execute(
        """UPDATE active_plans
           SET receipt=?,feasible=?,unscheduled_task_minutes=?,warning=?
           WHERE id=?""",
        (
            json.dumps(receipt, ensure_ascii=False),
            int(unscheduled == 0),
            unscheduled,
            warning,
            plan["id"],
        ),
    )
    history_id = _id("replan")
    before_json = json.dumps(before_snapshot, ensure_ascii=False)
    after_json = json.dumps(after_snapshot, ensure_ascii=False)
    connection.execute(
        """INSERT INTO active_replan_history
           (id,plan_id,session_id,workspace_id,trigger,trigger_name,reason,affected_from,
            before_state,after_state,before_snapshot,after_snapshot,moved_task_ids,
            unscheduled_minutes,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            history_id,
            plan["id"],
            session["id"],
            session["workspace_id"],
            trigger_name,
            trigger_name,
            reason[:1000],
            affected_from.isoformat(),
            before_json,
            after_json,
            before_json,
            after_json,
            json.dumps(moved, ensure_ascii=False),
            unscheduled,
            _now(),
        ),
    )
    history = {
        "id": history_id,
        "trigger": trigger_name,
        "reason": reason,
        "affected_from": affected_from.isoformat(),
        "moved_task_ids": moved,
        "unscheduled_task_minutes": unscheduled,
    }
    if state is not None:
        state.setdefault("replan_history", []).append(history)
        state["today_remaining_task_minutes"] = next(
            (
                max(
                    0,
                    int(item["capacity_minutes"])
                    - sum(
                        int(task["estimated_task_minutes"])
                        for task in item["tasks"]
                        if task["status"] == "completed"
                    ),
                )
                for item in calendar
                if item["date"] == affected_from.isoformat()
            ),
            0,
        )
    return {
        "history": history,
        "moved_task_ids": moved,
        "unscheduled_task_minutes": unscheduled,
        "calendar": calendar,
        "remaining_projected_review_minutes": future_review_minutes,
    }


@router.post("/sessions/{session_id}/replan")
def replan_session(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Roll unfinished standard-task work forward without changing elapsed time."""
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        plan = connection.execute(
            "SELECT * FROM active_plans WHERE id=?", (session["plan_id"],)
        ).fetchone()
        if not plan:
            _error("ACTIVE_PLAN_NOT_FOUND", "主动教学计划不存在", 404)
        receipt = json.loads(plan["receipt"])
        try:
            affected_from = date.fromisoformat(
                str(body.get("affected_from") or date.today().isoformat())
            )
            deadline = date.fromisoformat(receipt["deadline"])
        except (TypeError, ValueError):
            _error("INVALID_REPLAN_DATE", "重排日期必须是有效 ISO 日期", 422)
        if affected_from > deadline:
            _error("REPLAN_AFTER_DEADLINE", "重排起点晚于截止日期", 422)

        added_unavailable = {
            str(item).strip()
            for item in body.get("additional_unavailable_dates") or []
            if str(item).strip()
        }
        for item in added_unavailable:
            try:
                date.fromisoformat(item)
            except ValueError:
                _error("INVALID_UNAVAILABLE_DATE", f"无效不可学习日期：{item}", 422)
        receipt["unavailable_dates"] = sorted(
            set(receipt.get("unavailable_dates") or []) | added_unavailable
        )
        if connection.execute(
            "SELECT 1 FROM active_receipts WHERE workspace_id=?",
            (session["workspace_id"],),
        ).fetchone():
            connection.execute(
                "UPDATE active_receipts SET payload=?,updated_at=? WHERE workspace_id=?",
                (json.dumps(receipt, ensure_ascii=False), _now(), session["workspace_id"]),
            )
        trigger_name = str(body.get("trigger") or "manual")[:80]
        state = json.loads(session["state"])
        outcome = _replan_pending_in_transaction(
            connection,
            session=session,
            plan=plan,
            receipt=receipt,
            affected_from=affected_from,
            trigger_name=trigger_name,
            reason=str(body.get("reason") or "滚动未完成任务"),
            state=state,
        )
        connection.execute(
            "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), _now(), session_id),
        )
        connection.commit()
        return {
            "history_id": outcome["history"]["id"],
            "affected_from": affected_from.isoformat(),
            "moved_task_ids": outcome["moved_task_ids"],
            "unscheduled_task_minutes": outcome["unscheduled_task_minutes"],
            "estimated_task_minutes_changed": False,
            "actual_elapsed_minutes_changed": False,
            "plan": _plan(connection, plan["id"]),
            "session": _public_session(connection, session_id),
        }


@router.get("/sessions/{session_id}/replan-history")
def replan_history(session_id: str) -> dict[str, Any]:
    with _connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone():
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        rows = connection.execute(
            """SELECT * FROM active_replan_history
               WHERE session_id=? ORDER BY created_at,id""",
            (session_id,),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            for key in ("before_snapshot", "after_snapshot", "moved_task_ids"):
                item[key] = json.loads(item[key])
            items.append(item)
        return {"session_id": session_id, "items": items}


def _completion_report(
    connection: sqlite3.Connection, session_id: str
) -> dict[str, Any]:
    session = connection.execute(
        "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not session:
        _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
    plan = connection.execute(
        "SELECT * FROM active_plans WHERE id=?", (session["plan_id"],)
    ).fetchone()
    state = json.loads(session["state"])
    tasks = connection.execute(
        """SELECT * FROM active_plan_tasks WHERE plan_id=?
           ORDER BY day_index,task_order,id""",
        (session["plan_id"],),
    ).fetchall()
    by_type: dict[str, dict[str, Any]] = {}
    for task in tasks:
        bucket = by_type.setdefault(
            task["task_type"],
            {
                "task_count": 0,
                "completed_count": 0,
                "planned_task_minutes": 0,
                "completed_task_minutes": 0,
                "actual_elapsed_minutes": 0.0,
            },
        )
        bucket["task_count"] += 1
        bucket["completed_count"] += int(task["status"] == "completed")
        bucket["planned_task_minutes"] += int(task["estimated_task_minutes"])
        bucket["completed_task_minutes"] += int(task["completed_task_minutes"])
        bucket["actual_elapsed_minutes"] = round(
            float(bucket["actual_elapsed_minutes"])
            + float(task["actual_elapsed_minutes"]),
            3,
        )
    planned_minutes = sum(int(task["estimated_task_minutes"]) for task in tasks)
    completed_minutes = sum(int(task["completed_task_minutes"]) for task in tasks)
    actual_minutes = round(
        sum(float(task["actual_elapsed_minutes"]) for task in tasks), 3
    )
    initial = [task for task in tasks if task["task_type"] == "initial_learning"]
    review_rows = connection.execute(
        "SELECT * FROM active_review_items WHERE session_id=? ORDER BY due_date,id",
        (session_id,),
    ).fetchall()
    evidence_rows = connection.execute(
        """SELECT evidence_type,score,passed,knowledge_point_id,created_at
           FROM active_mastery_evidence WHERE session_id=? ORDER BY created_at,id""",
        (session_id,),
    ).fetchall()
    independent_evidence = [
        row
        for row in evidence_rows
        if row["passed"]
        and row["evidence_type"]
        in {"provider_grounded_rubric", "delayed_independent_review", "code_hidden_tests"}
    ]
    conflicts = connection.execute(
        """SELECT COUNT(*) FROM active_knowledge_conflicts
           WHERE workspace_id=? AND status='unresolved'""",
        (session["workspace_id"],),
    ).fetchone()[0]
    mistakes: list[dict[str, Any]] = []
    executable_required_point_ids = {
        str(row["id"])
        for row in connection.execute(
            """SELECT id,acceptance_method FROM active_knowledge_points
               WHERE workspace_id=?""",
            (session["workspace_id"],),
        ).fetchall()
        if _requires_executable_verification(str(row["acceptance_method"] or ""))
    }
    system_verified_problem_point_ids: set[str] = set()
    independent_oracle_problem_point_ids: set[str] = set()
    independently_verified_problem_point_ids: set[str] = set()
    code_summary = {
        "problem_count": 0,
        "submission_count": 0,
        "independently_verified_problem_count": 0,
        "executable_required_point_count": len(executable_required_point_ids),
        "system_verified_problem_point_count": 0,
        "independent_oracle_problem_point_count": 0,
        "independently_verified_problem_point_count": 0,
    }
    if connection.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='active_practice_code_submissions'"""
    ).fetchone():
        problem_spec_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(active_practice_problem_specs)"
            ).fetchall()
        }
        verification_scope_expression = (
            "COALESCE(verification_scope,'bundle_consistency')"
            if "verification_scope" in problem_spec_columns
            else "'bundle_consistency'"
        )
        problems = connection.execute(
            f"""SELECT id,title,knowledge_point_id,reference_status,
                      {verification_scope_expression} AS verification_scope
               FROM active_practice_problem_specs
               WHERE workspace_id=? ORDER BY created_at,id""",
            (session["workspace_id"],),
        ).fetchall()
        code_summary["problem_count"] = len(problems)
        for problem in problems:
            if (
                problem["reference_status"] == "system_verified"
                and str(problem["knowledge_point_id"])
                in executable_required_point_ids
            ):
                system_verified_problem_point_ids.add(
                    str(problem["knowledge_point_id"])
                )
                if problem["verification_scope"] == "independent_oracle":
                    independent_oracle_problem_point_ids.add(
                        str(problem["knowledge_point_id"])
                    )
            submissions = connection.execute(
                """SELECT * FROM active_practice_code_submissions
                   WHERE session_id=? AND problem_id=? ORDER BY created_at,id""",
                (session_id, problem["id"]),
            ).fetchall()
            code_summary["submission_count"] += len(submissions)
            independent_accept = next(
                (
                    row
                    for row in reversed(submissions)
                    if row["mode"] == "submit"
                    and row["status"] == "accepted"
                    and int(row["hint_level"]) == 0
                ),
                None,
            )
            if independent_accept:
                code_summary["independently_verified_problem_count"] += 1
                if str(problem["knowledge_point_id"]) in executable_required_point_ids:
                    independently_verified_problem_point_ids.add(
                        str(problem["knowledge_point_id"])
                    )
            for row in submissions:
                if row["status"] == "accepted":
                    continue
                mistakes.append(
                    {
                        "submission_id": row["id"],
                        "problem_id": problem["id"],
                        "problem_title": problem["title"],
                        "status": row["status"],
                        "passed_count": row["passed_count"],
                        "total_count": row["total_count"],
                        "hint_level": row["hint_level"],
                        "created_at": row["created_at"],
                        "resolved": bool(
                            independent_accept
                            and independent_accept["created_at"] >= row["created_at"]
                        ),
                    }
                )
    code_summary["system_verified_problem_point_count"] = len(
        system_verified_problem_point_ids
    )
    code_summary["independent_oracle_problem_point_count"] = len(
        independent_oracle_problem_point_ids
    )
    code_summary["independently_verified_problem_point_count"] = len(
        independently_verified_problem_point_ids
    )
    gates = {
        "all_initial_tasks_completed": bool(initial)
        and all(task["status"] == "completed" for task in initial),
        "all_materialized_reviews_completed": bool(review_rows)
        and all(row["status"] == "completed" for row in review_rows),
        "no_unresolved_confusions": not state.get("unresolved_confusions"),
        "no_unresolved_source_conflicts": int(conflicts) == 0,
        "no_unscheduled_minutes": int(plan["unscheduled_task_minutes"]) == 0,
        "all_code_problems_independently_verified": (
            not executable_required_point_ids
            or (
                executable_required_point_ids
                <= independently_verified_problem_point_ids
                and executable_required_point_ids
                <= independent_oracle_problem_point_ids
            )
        ),
        "has_independent_mastery_evidence": bool(independent_evidence),
    }
    verified_complete = all(gates.values())
    return {
        "session_id": session_id,
        "workspace_id": session["workspace_id"],
        "learning_goal": state.get("current_learning_goal"),
        "status": (
            "verified_complete"
            if verified_complete
            else "blocked_by_capacity"
            if int(plan["unscheduled_task_minutes"]) > 0
            else "in_progress"
        ),
        "verified_complete": verified_complete,
        "gates": gates,
        "time": {
            "planned_task_minutes": planned_minutes,
            "completed_task_minutes": completed_minutes,
            "actual_elapsed_minutes": actual_minutes,
            "variance_minutes": round(actual_minutes - completed_minutes, 3),
            "variance_reason": (
                "实际耗时与完成的标准任务分钟分开记录；差异可能来自学习速度、补救或中断。"
            ),
        },
        "tasks_by_type": by_type,
        "mastery": {
            "evidence_count": len(evidence_rows),
            "independent_pass_count": len(independent_evidence),
            "review_chain_count": len(review_rows),
            "completed_review_chain_count": sum(
                row["status"] == "completed" for row in review_rows
            ),
        },
        "code": code_summary,
        "mistake_queue": mistakes[-50:],
        "unresolved_confusions": state.get("unresolved_confusions") or [],
        "generated_at": _now(),
    }


@router.get("/sessions/{session_id}/completion-report")
def completion_report(session_id: str) -> dict[str, Any]:
    with _connect() as connection:
        return _completion_report(connection, session_id)


@router.post("/workspaces/{workspace_id}/retrieve")
def retrieve(workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
    query = str(body.get("query") or "").strip()
    plan_id = str(body.get("plan_id") or "").strip() or None
    if not query:
        _error("QUERY_REQUIRED", "检索问题不能为空", 422)
    with _connect() as connection:
        plan_material_ids = _plan_material_ids(connection, workspace_id, plan_id)
        if plan_id and not plan_material_ids:
            _error("ACTIVE_PLAN_NOT_FOUND", "当前工作区中不存在该主动教学计划", 404)
        conflicts = _matching_conflicts(
            connection, workspace_id, query, plan_id=plan_id
        )
        if conflicts:
            return {
                "status": "conflict",
                "answer": f"资料存在冲突：{conflicts[0]['summary']}",
                "citations": [conflicts[0]["evidence_a"], conflicts[0]["evidence_b"]],
                "conflicts": conflicts,
                "retrieval": _knowledge_base_status(
                    connection, workspace_id, plan_material_ids
                ),
            }
        hits = _search(connection, workspace_id, query, plan_id=plan_id)
        if not hits:
            return {
                "status": "insufficient",
                "answer": "资料中无法确认",
                "citations": [],
                "retrieval": _knowledge_base_status(
                    connection, workspace_id, plan_material_ids
                ),
            }
        return {
            "status": "grounded",
            "answer": f"资料中可确认：{hits[0]['quote'][:300]}",
            "citations": hits,
            "retrieval": _knowledge_base_status(
                connection, workspace_id, plan_material_ids
            ),
        }


@router.get("/workspaces/{workspace_id}/snapshot")
def workspace_snapshot(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        materials = [
            _public_material(connection, row["id"])
            for row in connection.execute(
                "SELECT id FROM active_materials WHERE workspace_id=? ORDER BY uploaded_at", (workspace_id,)
            ).fetchall()
        ]
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        plan_row = connection.execute(
            "SELECT id FROM active_plans WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        session_row = (
            connection.execute(
                "SELECT id FROM active_teaching_sessions WHERE plan_id=? ORDER BY started_at DESC LIMIT 1",
                (plan_row["id"],),
            ).fetchone()
            if plan_row
            else None
        )
        knowledge_graph = _graph(connection, workspace_id)
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_point_details'"
        ).fetchone():
            from .generic_knowledge_api import public_generic_graph

            knowledge_graph = public_generic_graph(connection, workspace_id)
        plan_material_ids = (
            _plan_material_ids(connection, workspace_id, plan_row["id"])
            if plan_row
            else []
        )
        return {
            "workspace_id": workspace_id,
            "local_knowledge_base": {
                **_knowledge_base_status(
                    connection, workspace_id, plan_material_ids
                ),
                "scope": "current_plan" if plan_row else "workspace",
                "plan_id": plan_row["id"] if plan_row else None,
                "material_ids": plan_material_ids,
            },
            "materials": materials,
            "receipt": json.loads(receipt_row["payload"]) if receipt_row else None,
            "knowledge_graph": knowledge_graph,
            "plan": _plan(connection, plan_row["id"]) if plan_row else None,
            "session": _public_session(connection, session_row["id"]) if session_row else None,
            "completion_report": (
                _completion_report(connection, session_row["id"]) if session_row else None
            ),
        }


def _workspace_day_overview(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    plan_id: str,
    day_date: str,
) -> dict[str, Any]:
    rows = connection.execute(
        """SELECT task.*,point.name AS knowledge_point_name,
                  point.difficulty,point.mastery
           FROM active_plan_tasks AS task
           JOIN active_knowledge_points AS point
             ON point.id=task.knowledge_point_id
           WHERE task.plan_id=? AND task.day_date=?
           ORDER BY task.point_sequence,task.task_order,task.id""",
        (plan_id, day_date),
    ).fetchall()
    overdue_rows = connection.execute(
        """SELECT task.*,point.name AS knowledge_point_name,
                  point.difficulty,point.mastery
           FROM active_plan_tasks AS task
           JOIN active_knowledge_points AS point
             ON point.id=task.knowledge_point_id
           WHERE task.plan_id=? AND task.day_date<?
             AND task.status IN ('planned','in_progress','unscheduled')
           ORDER BY task.day_date,task.point_sequence,task.task_order,task.id""",
        (plan_id, day_date),
    ).fetchall()
    today_point_ids = list(
        dict.fromkeys(str(row["knowledge_point_id"]) for row in rows)
    )
    relevant_rows = [*rows, *overdue_rows]
    point_ids = list(
        dict.fromkeys(str(row["knowledge_point_id"]) for row in relevant_rows)
    )
    prerequisite_names: dict[str, list[str]] = defaultdict(list)
    if point_ids:
        placeholders = ",".join("?" for _ in point_ids)
        for row in connection.execute(
            f"""SELECT dependency.dependent_id,point.name
                FROM active_dependencies AS dependency
                JOIN active_knowledge_points AS point
                  ON point.id=dependency.prerequisite_id
                WHERE dependency.workspace_id=?
                  AND dependency.dependent_id IN ({placeholders})
                ORDER BY dependency.dependent_id,point.name""",
            (workspace_id, *point_ids),
        ).fetchall():
            prerequisite_names[str(row["dependent_id"])].append(str(row["name"]))
    def enrich_task(row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        task["prerequisite_names"] = prerequisite_names.get(
            str(row["knowledge_point_id"]), []
        )
        task["ordering_reason"] = (
            "先修知识完成后，按简单到困难进入"
            if task["prerequisite_names"]
            else "当前无未满足先修，按简单到困难进入"
        )
        return task

    tasks = [enrich_task(row) for row in rows]
    overdue_tasks = [
        enrich_task(row)
        for row in overdue_rows
        if int(row["completed_task_minutes"] or 0)
        < int(row["estimated_task_minutes"] or 0)
    ]
    completed_minutes = sum(
        int(row["completed_task_minutes"] or 0) for row in rows
    )
    planned_minutes = sum(int(row["estimated_task_minutes"] or 0) for row in rows)
    overdue_minutes = sum(
        max(
            0,
            int(row["estimated_task_minutes"] or 0)
            - int(row["completed_task_minutes"] or 0),
        )
        for row in overdue_rows
    )
    has_plan_calendars = connection.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='active_plan_calendars'"""
    ).fetchone()
    calendar_row = (
        connection.execute(
            "SELECT calendar FROM active_plan_calendars WHERE plan_id=?",
            (plan_id,),
        ).fetchone()
        if has_plan_calendars
        else None
    )
    projected_reviews: list[dict[str, Any]] = []
    if calendar_row:
        try:
            calendar = json.loads(calendar_row["calendar"])
        except (TypeError, json.JSONDecodeError):
            calendar = []
        selected_day = next(
            (item for item in calendar if str(item.get("date")) == day_date),
            {},
        )
        projected_reviews = list(selected_day.get("projected_review_items") or [])
    return {
        "date": day_date,
        "knowledge_point_count": len(today_point_ids),
        "task_count": len(tasks),
        "planned_task_minutes": planned_minutes,
        "completed_task_minutes": completed_minutes,
        "remaining_task_minutes": max(0, planned_minutes - completed_minutes),
        "tasks": tasks,
        "overdue_task_count": len(overdue_tasks),
        "overdue_task_minutes": overdue_minutes,
        "oldest_overdue_date": (
            min(str(task["day_date"]) for task in overdue_tasks)
            if overdue_tasks
            else None
        ),
        "overdue_tasks": overdue_tasks,
        "projected_review_items": projected_reviews,
        "learning_order_policy": (
            "严格先满足前置依赖；同一时刻可学习的知识点按简单、"
            "中等、困难排序，难度相同再按资料原始顺序。"
        ),
    }


@router.get("/workspaces")
def list_learning_workspaces(
    include_archived: bool = False,
    day: str = "",
) -> dict[str, Any]:
    """Return the durable task index for the unified learning workspace."""
    selected_day = _review_as_of(day).isoformat() if day else date.today().isoformat()
    with _connect() as connection:
        workspace_ids = [
            row["workspace_id"]
            for row in connection.execute(
                """SELECT workspace_id FROM active_materials
                   UNION SELECT workspace_id FROM active_receipts
                   UNION SELECT workspace_id FROM active_plans
                   ORDER BY workspace_id"""
            ).fetchall()
        ]
        items: list[dict[str, Any]] = []
        for workspace_id in workspace_ids:
            settings_row = connection.execute(
                """SELECT archived FROM active_workspace_settings
                   WHERE workspace_id=?""",
                (workspace_id,),
            ).fetchone()
            archived = bool(settings_row["archived"]) if settings_row else False
            if archived and not include_archived:
                continue
            receipt_row = connection.execute(
                "SELECT payload,updated_at FROM active_receipts WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
            receipt = json.loads(receipt_row["payload"]) if receipt_row else {}
            plan = connection.execute(
                """SELECT * FROM active_plans
                   WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1""",
                (workspace_id,),
            ).fetchone()
            session = (
                connection.execute(
                    """SELECT id,updated_at FROM active_teaching_sessions
                       WHERE plan_id=? ORDER BY updated_at DESC LIMIT 1""",
                    (plan["id"],),
                ).fetchone()
                if plan
                else None
            )
            counts = connection.execute(
                """SELECT COUNT(*) AS material_count,
                          COALESCE(SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END),0)
                              AS ready_material_count
                   FROM active_materials WHERE workspace_id=?""",
                (workspace_id,),
            ).fetchone()
            first_material = connection.execute(
                """SELECT original_filename FROM active_materials
                   WHERE workspace_id=? ORDER BY uploaded_at,id LIMIT 1""",
                (workspace_id,),
            ).fetchone()
            point_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM active_knowledge_points WHERE workspace_id=?",
                    (workspace_id,),
                ).fetchone()[0]
            )
            task_counts = {"total": 0, "completed": 0}
            next_task = None
            if plan:
                task_row = connection.execute(
                    """SELECT id,title,day_date,estimated_task_minutes,status,
                              task_type,learning_phase
                       FROM active_plan_tasks
                       WHERE plan_id=? AND status<>'completed'
                       ORDER BY day_date,task_order,id LIMIT 1""",
                    (plan["id"],),
                ).fetchone()
                if task_row:
                    next_task = dict(task_row)
                count_row = connection.execute(
                    """SELECT COUNT(*) AS total,
                              COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0)
                                  AS completed
                       FROM active_plan_tasks WHERE plan_id=?""",
                    (plan["id"],),
                ).fetchone()
                task_counts = {
                    "total": int(count_row["total"]),
                    "completed": int(count_row["completed"]),
                }
            updated_candidates = [
                str(value)
                for value in (
                    receipt_row["updated_at"] if receipt_row else "",
                    plan["created_at"] if plan else "",
                    session["updated_at"] if session else "",
                )
                if value
            ]
            items.append(
                {
                    "workspace_id": workspace_id,
                    "archived": archived,
                    "title": str(
                        receipt.get("learning_goal")
                        or (next_task["title"] if next_task else "")
                        or (
                            Path(first_material["original_filename"]).stem
                            if first_material
                            else workspace_id
                        )
                    ),
                    "status": (
                        "learning"
                        if session
                        else "planned"
                        if plan
                        else "setting_up"
                    ),
                    "material_count": int(counts["material_count"]),
                    "ready_material_count": int(counts["ready_material_count"]),
                    "knowledge_point_count": point_count,
                    "plan_id": plan["id"] if plan else None,
                    "plan_feasible": bool(plan["feasible"]) if plan else None,
                    "session_id": session["id"] if session else None,
                    "task_count": task_counts["total"],
                    "completed_task_count": task_counts["completed"],
                    "next_task": next_task,
                    "today_plan": (
                        _workspace_day_overview(
                            connection,
                            workspace_id=workspace_id,
                            plan_id=plan["id"],
                            day_date=selected_day,
                        )
                        if plan
                        else None
                    ),
                    "learning_order_policy": (
                        "prerequisite_then_difficulty_then_source"
                        if plan
                        else None
                    ),
                    "updated_at": max(updated_candidates) if updated_candidates else "",
                }
            )
        items.sort(key=lambda item: (item["updated_at"], item["workspace_id"]), reverse=True)
        return {"items": items}


@router.patch("/workspaces/{workspace_id}")
def update_learning_workspace(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Archive or restore a unified learning task without deleting its learning data."""
    if "archived" not in payload or not isinstance(payload["archived"], bool):
        _error(
            "INVALID_WORKSPACE_UPDATE",
            "archived 必须是布尔值",
            422,
        )
    archived = bool(payload["archived"])
    with _connect() as connection:
        exists = connection.execute(
            """SELECT 1 FROM active_materials WHERE workspace_id=?
               UNION SELECT 1 FROM active_receipts WHERE workspace_id=?
               UNION SELECT 1 FROM active_plans WHERE workspace_id=?
               LIMIT 1""",
            (workspace_id, workspace_id, workspace_id),
        ).fetchone()
        if not exists:
            _error(
                "LEARNING_WORKSPACE_NOT_FOUND",
                "学习任务不存在",
                404,
            )
        connection.execute(
            """INSERT INTO active_workspace_settings(workspace_id,archived,updated_at)
               VALUES(?,?,?)
               ON CONFLICT(workspace_id) DO UPDATE SET
                 archived=excluded.archived,
                 updated_at=excluded.updated_at""",
            (workspace_id, int(archived), _now()),
        )
        connection.commit()
    return {"workspace_id": workspace_id, "archived": archived}
