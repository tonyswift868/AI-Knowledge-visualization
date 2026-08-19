from __future__ import annotations

import asyncio
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
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import desc, select

from .conversion_adapters import (
    ConversionBudget,
    ConversionError,
    ConversionRegistry,
    ConversionResult,
    ConversionSource,
)
from .orm import (
    ConversionManifestORM,
    KnowledgeEdgeORM,
    KnowledgeMapRevisionORM,
    KnowledgeNodeORM,
    SessionLocal,
    SourceRevisionORM,
)
from .knowledge_policy import (
    CONVERSION_POLICY,
    POLICY_VERSION,
    PROMPT_POLICY,
    SCHEDULING_POLICY,
    TEACHING_POLICY,
    TEXT_LIMITS_POLICY,
)
from .teacher_turn_v1 import (
    MAX_REMEDIATION_ATTEMPTS,
    TeacherEvaluationV1,
    TeacherExpressionV1,
    TeacherTurnV1,
    build_learner_preference_profile,
    build_teacher_turn,
    explicit_preferences_from_orm,
    explicit_preferences_from_receipt,
    merge_provider_expression,
    persist_teacher_turn,
    record_actual_elapsed,
    self_attested_completion_transition,
    transition_after_answer,
    turn_from_dict,
)
from .provider_runtime import (
    generation_meta,
    provider_for_session,
    require_provider_session,
    same_provider_configuration,
)
from .provider_service import ProviderError, parse_conversational_payload
from .classroom_media import (
    IMAGE_SUFFIXES as CLASSROOM_IMAGE_SUFFIXES,
    MAX_CLASSROOM_ATTACHMENT_BYTES,
    MAX_CLASSROOM_ATTACHMENT_COUNT,
    attachment_kind,
    classroom_asr_status,
    extract_attachment_text,
    extract_image_text,
    safe_attachment_name,
    transcribe_classroom_audio,
    validate_attachment_bytes,
    warm_classroom_asr,
)
from .url_fetcher_secure import fetch_safe
from .video_transcript import (
    VideoTranscriptError,
    classify_video_url,
    extract_video_transcript,
    remember_candidates,
    resolve_candidate,
    search_supplementary_materials,
    source_family_id,
    source_lineage,
)


router = APIRouter(prefix="/api/active-teaching", tags=["active-teaching"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_ROOT = Path(
    os.getenv(
        "MENTORFLOW_ACTIVE_UPLOAD_ROOT",
        str(PROJECT_ROOT / "data" / "active_teaching" / "uploads"),
    )
).expanduser().resolve()
MATERIAL_CONVERSION_REGISTRY = ConversionRegistry()
CONFUSION_MARKERS = ("不懂", "不理解", "为什么", "再解释", "没明白", "听不懂", "看不懂")
UNDERSTOOD_MARKERS = ("懂了", "明白了", "理解了", "会了", "可以继续")
QUESTION_FUNCTION_TERMS = {
    "为什",
    "什么",
    "怎么",
    "如何",
    "是否",
    "请问",
    "哪一",
    "哪里",
    "能不",
    "不能",
    "么会",
}
MAX_MATERIAL_BYTES = CONVERSION_POLICY.maximum_input_bytes
MAX_ARCHIVE_ENTRIES = CONVERSION_POLICY.maximum_archive_entries
MAX_ARCHIVE_UNCOMPRESSED_BYTES = CONVERSION_POLICY.maximum_expanded_bytes
MAX_TEACHING_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TEACHING_IMAGE_PIXELS = 40_000_000
TEACHING_IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}
TEACHER_VISUAL_PROFILES: dict[str, dict[str, Any]] = {
    "teacher-live2d-tsubaki-local": {
        "id": "teacher-live2d-tsubaki-local",
        "name": "椿 · Live2D 本地模拟",
        "description": "用户提供的非商用 Cubism 模型；原生网格、物理、表情与语义动作。",
        "manifest_url": "/teacher2d/tsubaki-live2d/manifest.json",
        "interactive": True,
        "local_only": True,
    },
    "teacher-02-blonde-fang-v9": {
        "id": "teacher-02-blonde-fang-v9",
        "name": "教师 02 · 金发虎牙小熊",
        "description": "小熊软糖教学态与泳装聊天态共用同一套轻量互动角色。",
        "manifest_url": "/teacher2d/original-ai-teacher-blonde-fang/manifest.json",
        "interactive": True,
    },
}
DEFAULT_TEACHER_VISUAL_PROFILE_ID = "teacher-live2d-tsubaki-local"
LEGACY_DEFAULT_TEACHER_VISUAL_PROFILE_ID = "teacher-02-blonde-fang-v9"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_date_from_utc(value: Any) -> str | None:
    """Project a stored UTC timestamp to the host's local calendar day.

    Legacy timestamps may be SQLite's ``YYYY-MM-DD HH:MM:SS`` form or an ISO
    value with a trailing ``Z``.  Naive values are treated as UTC because all
    new writes use :func:`_now`; no fixed offset is assumed.
    """

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().date().isoformat()


def task_completion_fields(value: Any) -> dict[str, str | None]:
    """Return the stable public completion projection for a task row/value."""

    completed_at = value
    if isinstance(value, Mapping):
        completed_at = value.get("completed_at")
    elif hasattr(value, "keys") and "completed_at" in value.keys():
        completed_at = value["completed_at"]
    elif hasattr(value, "completed_at"):
        completed_at = value.completed_at
    if isinstance(completed_at, datetime):
        completed_at = completed_at.isoformat()
    completed_at = str(completed_at) if completed_at not in (None, "") else None
    local_date = local_date_from_utc(completed_at)
    return {
        "completed_at": completed_at,
        "completed_on": local_date,
        "completed_at_local_date": local_date,
    }


def activity_by_day(connection: sqlite3.Connection, plan_id: str) -> dict[str, dict[str, float]]:
    """Aggregate explicit elapsed-time events by their recorded local day.

    Completion effort is intentionally not included here; callers can display
    this map beside scheduled/planned minutes without conflating the two.
    """

    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_task_time_events'"
    ).fetchone():
        return {}
    plan_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('active_plan_tasks','orm_plan_execution_tasks')"
        ).fetchall()
    }
    if not plan_tables:
        return {}
    task_queries: list[str] = []
    params: list[Any] = []
    if "active_plan_tasks" in plan_tables:
        task_queries.append("SELECT id FROM active_plan_tasks WHERE plan_id=?")
        params.append(plan_id)
    if "orm_plan_execution_tasks" in plan_tables:
        task_queries.append("SELECT id FROM orm_plan_execution_tasks WHERE plan_id=?")
        params.append(plan_id)
    task_ids = [str(row[0]) for row in connection.execute(" UNION ".join(task_queries), params).fetchall()]
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    rows = connection.execute(
        f"SELECT recorded_at,elapsed_minutes FROM active_task_time_events WHERE task_id IN ({placeholders})",
        task_ids,
    ).fetchall()
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        day = local_date_from_utc(row["recorded_at"])
        if not day:
            continue
        bucket = result.setdefault(
            day,
            {"actual_elapsed_minutes": 0.0, "activity_event_count": 0},
        )
        bucket["actual_elapsed_minutes"] = round(
            float(bucket["actual_elapsed_minutes"]) + float(row["elapsed_minutes"] or 0),
            3,
        )
        bucket["activity_event_count"] += 1
    return result


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
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=rw",
            uri=True,
        )
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            "DATABASE_MIGRATION_REQUIRED: active teaching database is unavailable"
        ) from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    _require_active_teaching_schema(connection)
    return connection


_ACTIVE_TEACHING_RUNTIME_TABLES = frozenset(
    {
        "active_materials",
        "active_chunks",
        "active_receipts",
        "active_workspace_settings",
        "active_learner_profiles",
        "active_knowledge_memories",
        "active_knowledge_progress_snapshots",
        "active_teacher_personas",
        "active_knowledge_points",
        "active_dependencies",
        "active_knowledge_conflicts",
        "active_plans",
        "active_plan_tasks",
        "active_teaching_sessions",
        "active_teaching_messages",
        "active_teaching_attachments",
        "active_mastery_evidence",
        "active_task_time_events",
        "active_review_items",
        "active_review_attempts",
        "active_replan_history",
        "orm_learner_preferences",
    }
)


def _require_active_teaching_schema(connection: sqlite3.Connection) -> None:
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(_ACTIVE_TEACHING_RUNTIME_TABLES - present)
    if missing:
        connection.close()
        raise RuntimeError(
            "DATABASE_MIGRATION_REQUIRED: missing active teaching tables: "
            + ", ".join(missing)
        )


def initialize_active_teaching_storage() -> None:
    """Initialize legacy compatibility storage once during application startup.

    Canonical v1 knowledge-map tables remain exclusively Alembic-owned.  This
    startup-only bridge preserves the existing legacy classroom tables without
    allowing an HTTP request to create or alter database schema.
    """

    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        _init_schema(connection)
        _require_active_teaching_schema(connection)
    finally:
        connection.close()


def _progress_snapshot_key(
    material_id: Any,
    normalized_name: Any = "",
    name: Any = "",
) -> tuple[str, str]:
    """Return a stable key independent from generated graph and plan ids."""
    material = str(material_id or "").strip()
    value = str(normalized_name or name or "").strip()
    return material, _normalise(value)


def _capture_knowledge_progress(
    connection: sqlite3.Connection,
    workspace_id: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Persist compact learner progress before derived rows are rebuilt."""
    rows = connection.execute(
        "SELECT id,material_id,normalized_name,name,mastery "
        "FROM active_knowledge_points WHERE workspace_id=?",
        (workspace_id,),
    ).fetchall()
    snapshot: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = _progress_snapshot_key(
            row["material_id"], row["normalized_name"], row["name"]
        )
        if not key[0] or not key[1]:
            continue
        snapshot[key] = {
            "workspace_id": workspace_id,
            "material_id": key[0],
            "normalized_name": key[1],
            "name": str(row["name"] or ""),
            "mastery": float(row["mastery"] or 0),
            "completed_task_count": 0,
            "completed_task_minutes": 0,
            "actual_elapsed_minutes": 0.0,
            "completed_phases": set(),
        }
    task_rows = connection.execute(
        """SELECT point.material_id,point.normalized_name,point.name AS point_name,
                  task.status,task.completed_task_minutes,
                  task.actual_elapsed_minutes,task.learning_phase
             FROM active_plan_tasks AS task
             JOIN active_knowledge_points AS point
               ON point.id=task.knowledge_point_id
            WHERE task.workspace_id=?""",
        (workspace_id,),
    ).fetchall()
    for row in task_rows:
        key = _progress_snapshot_key(
            row["material_id"], row["normalized_name"], row["point_name"]
        )
        item = snapshot.get(key)
        if item is None:
            continue
        item["completed_task_minutes"] += int(row["completed_task_minutes"] or 0)
        item["actual_elapsed_minutes"] += float(row["actual_elapsed_minutes"] or 0)
        if str(row["status"] or "") == "completed":
            item["completed_task_count"] += 1
            phase = str(row["learning_phase"] or "").strip()
            if phase:
                item["completed_phases"].add(phase)
    existing_phase_rows = connection.execute(
        """SELECT material_id,normalized_name,completed_phases
           FROM active_knowledge_progress_snapshots WHERE workspace_id=?""",
        (workspace_id,),
    ).fetchall()
    existing_phases: dict[tuple[str, str], set[str]] = {}
    for row in existing_phase_rows:
        try:
            values = json.loads(row["completed_phases"] or "[]")
        except (TypeError, json.JSONDecodeError):
            values = []
        existing_phases[(str(row["material_id"]), str(row["normalized_name"]))] = {
            str(value) for value in values if str(value)
        }
    for item in snapshot.values():
        phases = sorted(
            {
                *existing_phases.get(
                    (item["material_id"], item["normalized_name"]), set()
                ),
                *(str(value) for value in item.pop("completed_phases") if value),
            }
        )
        item["completed_phases"] = phases
        connection.execute(
            """INSERT INTO active_knowledge_progress_snapshots
               (workspace_id,material_id,normalized_name,name,mastery,
                completed_task_count,completed_task_minutes,actual_elapsed_minutes,
                completed_phases,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(workspace_id,material_id,normalized_name) DO UPDATE SET
                 name=excluded.name,
                 mastery=MAX(active_knowledge_progress_snapshots.mastery,excluded.mastery),
                 completed_task_count=MAX(active_knowledge_progress_snapshots.completed_task_count,excluded.completed_task_count),
                 completed_task_minutes=MAX(active_knowledge_progress_snapshots.completed_task_minutes,excluded.completed_task_minutes),
                 actual_elapsed_minutes=MAX(active_knowledge_progress_snapshots.actual_elapsed_minutes,excluded.actual_elapsed_minutes),
                 completed_phases=excluded.completed_phases,
                 updated_at=excluded.updated_at""",
            (
                item["workspace_id"], item["material_id"], item["normalized_name"],
                item["name"], item["mastery"], item["completed_task_count"],
                item["completed_task_minutes"], item["actual_elapsed_minutes"],
                json.dumps(phases, ensure_ascii=False), _now(),
            ),
        )
    return snapshot


def _progress_snapshots(
    connection: sqlite3.Connection,
    workspace_id: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Load durable learner progress keyed by material and normalized name."""
    rows = connection.execute(
        "SELECT * FROM active_knowledge_progress_snapshots WHERE workspace_id=?",
        (workspace_id,),
    ).fetchall()
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        try:
            phases = json.loads(row["completed_phases"] or "[]")
        except (TypeError, json.JSONDecodeError):
            phases = []
        output[(str(row["material_id"]), str(row["normalized_name"]))] = {
            **dict(row),
            "mastery": float(row["mastery"] or 0),
            "completed_task_count": int(row["completed_task_count"] or 0),
            "completed_task_minutes": int(row["completed_task_minutes"] or 0),
            "actual_elapsed_minutes": float(row["actual_elapsed_minutes"] or 0),
            "completed_phases": [str(value) for value in phases if str(value)],
        }
    return output


def _knowledge_point_progress_summary(
    connection: sqlite3.Connection,
    workspace_id: str,
    plan_id: str | None,
) -> dict[str, int]:
    """Return point-level completion separately from phase-task completion.

    A knowledge point has several initial-learning phase rows, so exposing the
    raw task count as "completed problems" is misleading (and was the source
    of the old 1/150 report).  This projection is derived only from the
    canonical active plan task statuses and never marks a point complete from
    reviews, reminders, or a display-only mastery value.
    """
    point_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM active_knowledge_points WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()[0]
    )
    if not plan_id:
        return {
            "completed_knowledge_point_count": 0,
            "initial_learning_task_count": 0,
            "completed_initial_learning_task_count": 0,
            "knowledge_point_count": point_count,
        }
    rows = connection.execute(
        """SELECT knowledge_point_id,status
           FROM active_plan_tasks
           WHERE workspace_id=? AND plan_id=? AND task_type='initial_learning'
           ORDER BY knowledge_point_id,id""",
        (workspace_id, plan_id),
    ).fetchall()
    # v1 activation stores immutable execution tasks in the ORM table rather
    # than the legacy active_plan_tasks table.  Keep the workspace index and
    # desktop overview truthful without copying v1 concepts into legacy rows.
    if not rows:
        rows = connection.execute(
            """SELECT knowledge_point_id,status
               FROM orm_plan_execution_tasks
               WHERE workspace_id=? AND plan_id=? AND task_type='initial_learning'
               ORDER BY knowledge_point_id,id""",
            (workspace_id, plan_id),
        ).fetchall()
        if point_count == 0:
            point_count = int(
                connection.execute(
                    """SELECT COUNT(DISTINCT knowledge_point_id)
                       FROM orm_plan_execution_tasks
                       WHERE workspace_id=? AND plan_id=?""",
                    (workspace_id, plan_id),
                ).fetchone()[0]
            )
    by_point: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_point[str(row["knowledge_point_id"])].append(str(row["status"] or ""))
    completed_points = sum(
        bool(statuses) and all(status == "completed" for status in statuses)
        for statuses in by_point.values()
    )
    completed_tasks = sum(
        status == "completed" for statuses in by_point.values() for status in statuses
    )
    return {
        "completed_knowledge_point_count": int(completed_points),
        "initial_learning_task_count": len(rows),
        "completed_initial_learning_task_count": int(completed_tasks),
        "knowledge_point_count": point_count,
    }


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
            archived INTEGER NOT NULL DEFAULT 0,
            archived_at TEXT,
            archive_reason TEXT NOT NULL DEFAULT '',
            material_revision INTEGER NOT NULL DEFAULT 1,
            source_url TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT 'uploaded_file',
            provenance_json TEXT NOT NULL DEFAULT '{}',
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
        CREATE TABLE IF NOT EXISTS active_learner_profiles (
            learner_key TEXT PRIMARY KEY,
            compact_profile TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_knowledge_memories (
            workspace_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL REFERENCES active_knowledge_points(id) ON DELETE CASCADE,
            detail_memory TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(workspace_id, knowledge_point_id)
        );
        CREATE TABLE IF NOT EXISTS active_knowledge_progress_snapshots (
            workspace_id TEXT NOT NULL,
            material_id TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            mastery REAL NOT NULL DEFAULT 0,
            completed_task_count INTEGER NOT NULL DEFAULT 0,
            completed_task_minutes INTEGER NOT NULL DEFAULT 0,
            actual_elapsed_minutes REAL NOT NULL DEFAULT 0,
            completed_phases TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(workspace_id, material_id, normalized_name)
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
            original_day_date TEXT NOT NULL DEFAULT '',
            day_index INTEGER NOT NULL,
            task_order INTEGER NOT NULL,
            knowledge_point_id TEXT NOT NULL REFERENCES active_knowledge_points(id),
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            source_evidence TEXT NOT NULL,
            acceptance_condition TEXT NOT NULL,
            estimated_task_minutes INTEGER NOT NULL,
            completed_task_minutes INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT,
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
            attachment_ids TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_teaching_attachments (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            media_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready',
            analysis_mode TEXT NOT NULL DEFAULT 'pending',
            analysis_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(session_id, sha256)
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
    # Material disposition is additive and reversible. This bridge runs only
    # during application startup (never from an HTTP request); Alembic 0022
    # covers databases that already have the legacy table.
    existing_material_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(active_materials)").fetchall()
    }
    for column, ddl in (
        ("archived", "INTEGER NOT NULL DEFAULT 0"),
        ("archived_at", "TEXT"),
        ("archive_reason", "TEXT NOT NULL DEFAULT ''"),
        ("material_revision", "INTEGER NOT NULL DEFAULT 1"),
        ("source_url", "TEXT NOT NULL DEFAULT ''"),
        ("source_kind", "TEXT NOT NULL DEFAULT 'uploaded_file'"),
        ("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
    ):
        if column not in existing_material_columns:
            connection.execute(
                f"ALTER TABLE active_materials ADD COLUMN {column} {ddl}"
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
    existing_message_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(active_teaching_messages)"
        ).fetchall()
    }
    if "attachment_ids" not in existing_message_columns:
        connection.execute(
            "ALTER TABLE active_teaching_messages "
            "ADD COLUMN attachment_ids TEXT NOT NULL DEFAULT '[]'"
        )
    if "teacher_turn_json" not in existing_message_columns:
        connection.execute(
            "ALTER TABLE active_teaching_messages "
            "ADD COLUMN teacher_turn_json TEXT NOT NULL DEFAULT ''"
        )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_active_teaching_attachments_session
           ON active_teaching_attachments(session_id,created_at)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_active_plan_tasks_schedule
           ON active_plan_tasks(plan_id,status,day_date)"""
    )
    existing_task_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(active_plan_tasks)").fetchall()
    }
    if "original_day_date" not in existing_task_columns:
        connection.execute(
            "ALTER TABLE active_plan_tasks "
            "ADD COLUMN original_day_date TEXT NOT NULL DEFAULT ''"
        )
    connection.execute(
        """UPDATE active_plan_tasks
           SET original_day_date=day_date
           WHERE original_day_date='' OR original_day_date IS NULL"""
    )
    # Older replans rewrote day_date in place. Recover the earliest recorded
    # schedule so rolling debt forward can never erase the day it was first due.
    for history_row in connection.execute(
        """SELECT before_snapshot FROM active_replan_history
           WHERE before_snapshot<>'' ORDER BY created_at,id"""
    ).fetchall():
        try:
            historical_tasks = json.loads(history_row["before_snapshot"])
        except (TypeError, json.JSONDecodeError):
            continue
        for task in historical_tasks if isinstance(historical_tasks, list) else []:
            task_id = str(task.get("id") or "")
            original_date = str(task.get("original_day_date") or task.get("day_date") or "")
            if not task_id or not original_date:
                continue
            connection.execute(
                """UPDATE active_plan_tasks
                   SET original_day_date=?
                   WHERE id=? AND (original_day_date='' OR original_day_date>?)""",
                (original_date, task_id, original_date),
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
            """INSERT OR IGNORE INTO active_teacher_personas
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
                DEFAULT_TEACHER_VISUAL_PROFILE_ID,
                TEACHER_VISUAL_PROFILES[DEFAULT_TEACHER_VISUAL_PROFILE_ID]["name"],
                TEACHER_VISUAL_PROFILES[DEFAULT_TEACHER_VISUAL_PROFILE_ID]["manifest_url"],
                now,
                now,
            ),
        )
    connection.execute(
        """UPDATE active_teacher_personas
           SET visual_profile_id=?,visual_profile_name=?,visual_manifest_url=?,updated_at=?
           WHERE id='teacher_persona_iris_default'
             AND visual_profile_id IN ('',?)""",
        (
            DEFAULT_TEACHER_VISUAL_PROFILE_ID,
            TEACHER_VISUAL_PROFILES[DEFAULT_TEACHER_VISUAL_PROFILE_ID]["name"],
            TEACHER_VISUAL_PROFILES[DEFAULT_TEACHER_VISUAL_PROFILE_ID]["manifest_url"],
            now,
            LEGACY_DEFAULT_TEACHER_VISUAL_PROFILE_ID,
        ),
    )
    default_persona = connection.execute(
        "SELECT * FROM active_teacher_personas WHERE id='teacher_persona_iris_default'"
    ).fetchone()
    if (
        default_persona
        and str(default_persona["visual_profile_id"] or "")
        == DEFAULT_TEACHER_VISUAL_PROFILE_ID
    ):
        # Existing plans and open teaching sessions keep their identity and
        # progress; only references to the former built-in default artwork are
        # repointed to the new Live2D model.
        _propagate_persona_to_references(
            connection,
            source_persona_id="teacher_persona_iris_default",
            target=default_persona,
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


@router.patch("/workspaces/{workspace_id}/teacher-persona")
def apply_teacher_persona_to_workspace(
    workspace_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Switch one learning task to a saved persona without rebuilding its plan."""
    persona_id = str(body.get("teacher_persona_id") or "").strip()
    if not persona_id:
        _error("TEACHER_PERSONA_REQUIRED", "请选择要应用到当前任务的教师人设", 422)
    with _connect() as connection:
        _sync_teacher_persona_library(connection)
        persona = connection.execute(
            "SELECT * FROM active_teacher_personas WHERE id=?", (persona_id,)
        ).fetchone()
        if not persona:
            _error("TEACHER_PERSONA_NOT_FOUND", "所选教师人设不存在", 404)
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if not receipt_row:
            _error("RECEIPT_NOT_FOUND", "当前学习任务尚未保存，不能绑定教师人设", 404)

        now = _now()
        receipt = json.loads(receipt_row["payload"])
        _apply_persona_to_payload(receipt, persona)
        receipt["updated_at"] = now
        connection.execute(
            "UPDATE active_receipts SET payload=?,updated_at=? WHERE workspace_id=?",
            (json.dumps(receipt, ensure_ascii=False), now, workspace_id),
        )

        updated_plan_count = 0
        for plan in connection.execute(
            "SELECT id,receipt FROM active_plans WHERE workspace_id=?", (workspace_id,)
        ).fetchall():
            plan_receipt = json.loads(plan["receipt"])
            _apply_persona_to_payload(plan_receipt, persona)
            connection.execute(
                "UPDATE active_plans SET receipt=? WHERE id=?",
                (json.dumps(plan_receipt, ensure_ascii=False), plan["id"]),
            )
            updated_plan_count += 1

        updated_session_count = 0
        for session in connection.execute(
            "SELECT id,state FROM active_teaching_sessions WHERE workspace_id=?",
            (workspace_id,),
        ).fetchall():
            state = json.loads(session["state"])
            _apply_persona_to_payload(state, persona)
            connection.execute(
                "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
                (json.dumps(state, ensure_ascii=False), now, session["id"]),
            )
            updated_session_count += 1

        connection.commit()
        return {
            "workspace_id": workspace_id,
            "persona": _teacher_persona_item(
                persona,
                usage_count=_persona_usage_count(connection, persona_id),
            ),
            "updated_plan_count": updated_plan_count,
            "updated_session_count": updated_session_count,
        }


def _error(code: str, message: str, status: int = 400, **details: Any) -> None:
    raise HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "details": details}},
    )


def _bind_provider_session(
    state: dict[str, Any], requested_provider_session_id: str | None
) -> str | None:
    """Resolve a browser Provider handle without mistaking reconnection for a source switch."""

    bound_provider_session_id = str(state.get("provider_session_id") or "").strip() or None
    if not requested_provider_session_id:
        if not bound_provider_session_id:
            return None
        try:
            require_provider_session(bound_provider_session_id)
        except ProviderError:
            return None
        return bound_provider_session_id

    requested_session = require_provider_session(requested_provider_session_id)
    if not bound_provider_session_id or requested_provider_session_id == bound_provider_session_id:
        state["provider_session_id"] = requested_provider_session_id
        return requested_provider_session_id

    try:
        bound_session = require_provider_session(bound_provider_session_id)
    except ProviderError:
        # Provider handles live in backend memory. A browser can transparently
        # recreate the same verified configuration after a backend restart.
        state["provider_session_id"] = requested_provider_session_id
        state["provider_reconnect"] = {
            "previous_provider_session_id": bound_provider_session_id,
            "provider_session_id": requested_provider_session_id,
            "reconnected_at": _now(),
            "reason": "previous_handle_unavailable",
        }
        return requested_provider_session_id

    if same_provider_configuration(bound_session, requested_session):
        state["provider_session_id"] = requested_provider_session_id
        state["provider_reconnect"] = {
            "previous_provider_session_id": bound_provider_session_id,
            "provider_session_id": requested_provider_session_id,
            "reconnected_at": _now(),
            "reason": "same_configuration",
        }
        return requested_provider_session_id

    _error(
        "TEACHING_PROVIDER_SESSION_MISMATCH",
        "该教学会话已绑定另一套 Provider 配置；如需更换模型或评分来源，请新建任务。",
        409,
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


def _parse_file(
    path: Path,
    media_type: str = "",
) -> tuple[list[dict[str, Any]], int, str, ConversionResult]:
    result = MATERIAL_CONVERSION_REGISTRY.convert_safe(
        ConversionSource(name=path.name, path=path, mime_type=media_type),
        budget=ConversionBudget(
            max_input_bytes=MAX_MATERIAL_BYTES,
            max_output_chars=CONVERSION_POLICY.maximum_output_characters,
            timeout_seconds=CONVERSION_POLICY.timeout_seconds,
            max_expanded_bytes=MAX_ARCHIVE_UNCOMPRESSED_BYTES,
            max_archive_entries=MAX_ARCHIVE_ENTRIES,
        ),
    )
    if result.status != "ready" or result.metadata is None:
        failure = result.failure
        raise ConversionError(
            failure.code if failure else "MATERIAL_CONVERSION_FAILED",
            failure.message if failure else "Material conversion produced no result",
            retryable=bool(failure and failure.retryable),
            details=(failure.details if failure else {}),
        )
    parts: list[dict[str, Any]] = []
    char_cursor = 0
    for ordinal, item in enumerate(result.parts, start=1):
        text = item.text.strip()
        if not text:
            continue
        parts.append(
            {
                "text": text,
                "heading": item.heading[:300],
                "heading_level": 3,
                "page_start": item.page_start,
                "page_end": item.page_end,
                "paragraph_start": ordinal,
                "paragraph_end": ordinal,
                "char_start": char_cursor,
                "char_end": char_cursor + len(text),
                "bbox": list(item.bbox) if item.bbox else None,
                "audio_start_ms": item.audio_start_ms,
                "audio_end_ms": item.audio_end_ms,
                "kind": item.kind,
            }
        )
        char_cursor += len(text) + 2
    return parts, int(result.metadata.page_count or 0), result.metadata.adapter, result


def _persist_source_conversion(
    *,
    workspace_id: str,
    material_id: str,
    source_hash: str,
    target: Path,
    media_type: str,
    status: str,
    result: ConversionResult | None,
    failure: dict[str, Any] | None = None,
) -> str:
    """Persist the immutable source revision and its conversion receipt."""

    source_revision_id = _id("source-revision")
    metadata = result.metadata if result else None
    part_locators = [
        {
            "ordinal": item.ordinal,
            "kind": item.kind,
            "page_start": item.page_start,
            "page_end": item.page_end,
            "bbox": list(item.bbox) if item.bbox else None,
            "audio_start_ms": item.audio_start_ms,
            "audio_end_ms": item.audio_end_ms,
            "content_hash": _hash_text(item.text),
        }
        for item in (result.parts if result else ())
    ]
    with SessionLocal() as session:
        session.add(
            SourceRevisionORM(
                id=source_revision_id,
                workspace_id=workspace_id,
                source_id=material_id,
                revision=1,
                source_hash=source_hash,
                status=status,
            )
        )
        session.add(
            ConversionManifestORM(
                id=_id("conversion"),
                source_revision_id=source_revision_id,
                adapter_name=(metadata.adapter if metadata else str((failure or {}).get("adapter") or "registry")),
                adapter_version=(metadata.adapter_version if metadata else "conversion-registry.v1"),
                mime_type=media_type,
                source_path=str(target),
                content_hash=source_hash,
                metadata_json={
                    "source_bytes": metadata.source_bytes if metadata else target.stat().st_size,
                    "output_chars": metadata.output_chars if metadata else 0,
                    "part_count": metadata.part_count if metadata else 0,
                    "page_count": metadata.page_count if metadata else 0,
                    "warnings": list(metadata.warnings) if metadata else [],
                    "parts": part_locators,
                },
                status=status,
                failure_json=failure or {},
            )
        )
        session.commit()
    return source_revision_id


def _evidence(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    material_id = str(row["material_id"])
    material_url = ""
    material_path = ""
    material_kind = "uploaded_file"
    material_provenance: dict[str, Any] = {}
    try:
        with _connect() as source_connection:
            material_row = source_connection.execute(
                "SELECT stored_path,source_url,source_kind,provenance_json FROM active_materials WHERE id=?",
                (material_id,),
            ).fetchone()
            if material_row:
                material_path = str(material_row["stored_path"] or "") if "stored_path" in material_row.keys() else ""
                material_url = str(material_row["source_url"] or "") if "source_url" in material_row.keys() else ""
                material_kind = str(material_row["source_kind"] or "uploaded_file") if "source_kind" in material_row.keys() else "uploaded_file"
                try:
                    material_provenance = json.loads(material_row["provenance_json"] or "{}") if "provenance_json" in material_row.keys() else {}
                except (TypeError, json.JSONDecodeError):
                    material_provenance = {}
    except sqlite3.Error:
        pass
    source_revision_id = ""
    adapter_name = ""
    adapter_version = ""
    locator: dict[str, Any] = {}
    try:
        with SessionLocal() as session:
            revision = session.scalar(
                select(SourceRevisionORM)
                .where(SourceRevisionORM.source_id == material_id)
                .order_by(desc(SourceRevisionORM.revision))
            )
            if revision is not None:
                source_revision_id = revision.id
                manifest = session.scalar(
                    select(ConversionManifestORM)
                    .where(ConversionManifestORM.source_revision_id == revision.id)
                    .order_by(desc(ConversionManifestORM.created_at))
                )
                if manifest is not None:
                    adapter_name = manifest.adapter_name
                    adapter_version = manifest.adapter_version
                    ordinal = int(row["ordinal"]) if "ordinal" in keys else 0
                    locator = next(
                        (
                            dict(item)
                            for item in (manifest.metadata_json or {}).get("parts") or []
                            if int(item.get("ordinal") or 0) == ordinal
                        ),
                        {},
                    )
    except Exception:
        # Legacy rows predate conversion manifests and remain readable, but
        # they are classified as legacy_unverified by the v1 revision layer.
        pass
    quote = str(row["text"] or "")[:360]
    return {
        "material_id": material_id,
        "source_id": material_id,
        "source_revision_id": source_revision_id,
        "chunk_id": row["id"],
        "source_file": row["original_filename"] if "original_filename" in keys else None,
        "original_url": material_url,
        "source_kind": str(row["source_kind"] or material_kind) if "source_kind" in keys else material_kind,
        "provenance": material_provenance,
        "stored_path": material_path,
        "heading": row["heading"] if "heading" in keys else "",
        "page_start": row["page_start"],
        "page_end": row["page_end"],
        "paragraph_start": row["paragraph_start"],
        "paragraph_end": row["paragraph_end"],
        "char_start": row["char_start"],
        "char_end": row["char_end"],
        "content_hash": row["content_hash"],
        "bbox": locator.get("bbox"),
        "audio_start_ms": locator.get("audio_start_ms"),
        "audio_end_ms": locator.get("audio_end_ms"),
        "quote": quote,
        "quote_hash": _hash_text(quote),
        "adapter_name": adapter_name,
        "adapter_version": adapter_version,
    }
def _public_material(connection: sqlite3.Connection, material_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM active_materials WHERE id=?", (material_id,)).fetchone()
    if not row:
        _error("MATERIAL_NOT_FOUND", "学习资料不存在", 404)
    output = dict(row)
    # Keep the database representation numeric for compatibility while making
    # disposition explicit to desktop clients.
    output["archived"] = bool(output.get("archived", 0))
    output["chunks"] = [
        dict(item)
        for item in connection.execute(
            "SELECT * FROM active_chunks WHERE material_id=? ORDER BY ordinal", (material_id,)
        ).fetchall()
    ]
    output["chunk_count"] = len(output["chunks"])
    try:
        output["provenance"] = json.loads(output.get("provenance_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        output["provenance"] = {}
    return output


def _set_material_provenance(
    workspace_id: str,
    material_id: str,
    *,
    source_url: str,
    source_kind: str,
    provenance: Mapping[str, Any] | None = None,
    preserve_existing: bool = False,
) -> None:
    """Attach source identity without overwriting an existing source record.

    Content-hash deduplication may return a local upload or a URL imported
    earlier.  In that case the new reference is appended as an alias instead
    of replacing the original provenance, so evidence remains auditable.
    """

    with _connect() as connection:
        row = connection.execute(
            "SELECT source_url,source_kind,provenance_json FROM active_materials WHERE id=? AND workspace_id=?",
            (material_id, workspace_id),
        ).fetchone()
        existing_url = str(row["source_url"] or "") if row else ""
        existing_kind = str(row["source_kind"] or "uploaded_file") if row else "uploaded_file"
        try:
            existing_provenance = json.loads(row["provenance_json"] or "{}") if row else {}
        except (TypeError, json.JSONDecodeError):
            existing_provenance = {}
        existing_provenance = existing_provenance if isinstance(existing_provenance, dict) else {}
        incoming = dict(provenance or {})
        aliases = [
            dict(item)
            for item in existing_provenance.get("source_aliases") or []
            if isinstance(item, Mapping)
        ]
        if preserve_existing and (existing_url or existing_kind):
            aliases.append(
                {
                    "source_url": existing_url or None,
                    "source_kind": existing_kind,
                    "provenance": {
                        key: value
                        for key, value in existing_provenance.items()
                        if key not in {"source_aliases", "latest_reference"}
                    },
                }
            )
        aliases.append(
            {
                "source_url": str(source_url or "") or None,
                "source_kind": str(source_kind or "uploaded_file"),
                "provenance": incoming,
            }
        )
        deduped_aliases: list[dict[str, Any]] = []
        alias_keys: set[tuple[str, str]] = set()
        for alias in aliases:
            key = (str(alias.get("source_url") or ""), str(alias.get("source_kind") or ""))
            if key in alias_keys:
                continue
            alias_keys.add(key)
            deduped_aliases.append(alias)
        if preserve_existing:
            stored_url = existing_url
            stored_kind = existing_kind
            stored_provenance = {
                **existing_provenance,
                "source_aliases": deduped_aliases[-24:],
                "latest_reference": {
                    "source_url": str(source_url or "") or None,
                    "source_kind": str(source_kind or "uploaded_file"),
                },
            }
        else:
            stored_url = str(source_url or "")
            stored_kind = str(source_kind or "uploaded_file")
            stored_provenance = {
                **incoming,
                "source_aliases": deduped_aliases[-24:],
            }
        connection.execute(
            """UPDATE active_materials
               SET source_url=?,source_kind=?,provenance_json=?
               WHERE id=? AND workspace_id=?""",
            (
                stored_url[:2000],
                stored_kind[:80],
                json.dumps(stored_provenance, ensure_ascii=False)[:12000],
                material_id,
                workspace_id,
            ),
        )
        connection.commit()


@router.post("/workspaces/{workspace_id}/materials")
async def upload_material(workspace_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    filename = _safe_filename(file.filename or "material")
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
    media_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
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
        conversion_result: ConversionResult | None = None
        try:
            parts, page_count, parser_name, conversion_result = _parse_file(target, media_type)
            if not parts:
                raise ValueError("文件中没有可解析的正文")
        except Exception as exc:
            registry_code = exc.code if isinstance(exc, ConversionError) else "MATERIAL_CONVERSION_FAILED"
            code = (
                "UNSUPPORTED_FILE_TYPE"
                if registry_code == "UNSUPPORTED_FORMAT"
                else registry_code
                if registry_code == "VIDEO_UNSUPPORTED_IN_V1"
                else "MATERIAL_PARSE_FAILED"
            )
            details = exc.details if isinstance(exc, ConversionError) else {}
            failure = {
                "code": code,
                "message": str(exc),
                "retryable": bool(isinstance(exc, ConversionError) and exc.retryable),
                "details": {**details, "registry_code": registry_code},
            }
            connection.execute(
                """INSERT INTO active_materials
                   (id,workspace_id,original_filename,media_type,size_bytes,sha256,uploaded_at,stored_path,status,parse_error,page_count,parser_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    material_id,
                    workspace_id,
                    filename,
                    media_type,
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
            _persist_source_conversion(
                workspace_id=workspace_id,
                material_id=material_id,
                source_hash=sha256,
                target=target,
                media_type=media_type,
                status="failed",
                result=conversion_result,
                failure=failure,
            )
            status_code = 415 if code in {"UNSUPPORTED_FILE_TYPE", "VIDEO_UNSUPPORTED_IN_V1"} else 422
            _error(code, f"资料解析失败：{exc}", status_code, material_id=material_id, retryable=failure["retryable"])
        connection.execute(
            """INSERT INTO active_materials
               (id,workspace_id,original_filename,media_type,size_bytes,sha256,uploaded_at,stored_path,status,parse_error,page_count,parser_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                material_id,
                workspace_id,
                filename,
                media_type,
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
        _persist_source_conversion(
            workspace_id=workspace_id,
            material_id=material_id,
            source_hash=sha256,
            target=target,
            media_type=media_type,
            status="ready",
            result=conversion_result,
        )
        result = _public_material(connection, material_id)
        result["deduplicated"] = False
        return result


@router.get("/workspaces/{workspace_id}/materials")
def list_materials(workspace_id: str, include_archived: bool = False) -> list[dict[str, Any]]:
    with _connect() as connection:
        clause = "" if include_archived else " AND COALESCE(archived,0)=0"
        return [
            _public_material(connection, row["id"])
            for row in connection.execute(
                "SELECT id FROM active_materials WHERE workspace_id=?" + clause + " ORDER BY uploaded_at",
                (workspace_id,),
            ).fetchall()
        ]


def _set_material_disposition(
    workspace_id: str,
    material_id: str,
    *,
    archived: bool,
    reason: str = "",
) -> dict[str, Any]:
    """Apply a reversible material disposition without deleting evidence."""

    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM active_materials WHERE id=? AND workspace_id=?",
            (material_id, workspace_id),
        ).fetchone()
        if row is None:
            _error("MATERIAL_NOT_FOUND", "学习资料不存在", 404)
        # Failed parses remain inspectable but cannot be made active by restore.
        if not archived and str(row["status"] or "") != "ready":
            _error(
                "MATERIAL_NOT_READY",
                "解析失败的资料不能恢复为可用资料",
                409,
                material_id=material_id,
            )
        next_archived = int(bool(archived))
        next_reason = str(reason or "")[:500]
        connection.execute(
            """UPDATE active_materials
               SET archived=?,archived_at=?,archive_reason=?,material_revision=COALESCE(material_revision,1)+1
               WHERE id=? AND workspace_id=?""",
            (
                next_archived,
                _now() if archived else None,
                next_reason if archived else "",
                material_id,
                workspace_id,
            ),
        )
        connection.commit()
        result = _public_material(connection, material_id)
        result["disposition"] = "archived" if archived else "active"
        result["reversible"] = True
        result["reason"] = (
            "资料已归档；知识地图、学习计划与历史记录保持不变。"
            if archived
            else "资料已恢复；既有知识地图、学习计划与历史记录保持不变。"
        )
        return result


@router.post("/workspaces/{workspace_id}/materials/{material_id}/archive")
def archive_material(
    workspace_id: str,
    material_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _set_material_disposition(
        workspace_id,
        material_id,
        archived=True,
        reason=str((body or {}).get("reason") or "").strip(),
    )


@router.post("/workspaces/{workspace_id}/materials/{material_id}/restore")
def restore_material(workspace_id: str, material_id: str) -> dict[str, Any]:
    return _set_material_disposition(workspace_id, material_id, archived=False)


@router.patch("/workspaces/{workspace_id}/materials/{material_id}")
def update_material_disposition(
    workspace_id: str,
    material_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    payload = body or {}
    if not isinstance(payload.get("archived"), bool):
        _error("MATERIAL_DISPOSITION_REQUIRED", "archived 必须是布尔值", 422)
    return _set_material_disposition(
        workspace_id,
        material_id,
        archived=bool(payload["archived"]),
        reason=str(payload.get("reason") or "").strip(),
    )


@router.delete("/workspaces/{workspace_id}/materials/{material_id}")
def delete_material(
    workspace_id: str,
    material_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Delete means reversible archive unless an explicit safe purge is requested.

    Desktop clients historically call DELETE for a material.  Keep that
    contract safe by archiving by default; a physical purge requires a caller
    supplied confirmation and zero references from plans, receipts, maps, or
    knowledge points.
    """

    payload = body or {}
    physical = bool(payload.get("physical") or payload.get("purge"))
    confirm = bool(payload.get("confirm_unreferenced") or payload.get("confirm"))
    if not physical or not confirm:
        result = _set_material_disposition(
            workspace_id,
            material_id,
            archived=True,
            reason=str(payload.get("reason") or "deleted by client; retained as reversible archive").strip(),
        )
        result.update({"deleted": True, "physical_delete": False, "reversible": True})
        return result
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM active_materials WHERE id=? AND workspace_id=?",
            (material_id, workspace_id),
        ).fetchone()
        if row is None:
            _error("MATERIAL_NOT_FOUND", "学习资料不存在", 404)
        references: list[str] = []
        if connection.execute(
            "SELECT 1 FROM active_knowledge_points WHERE workspace_id=? AND material_id=? LIMIT 1",
            (workspace_id, material_id),
        ).fetchone():
            references.append("active_knowledge_points")
        receipt_rows = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
        for receipt in receipt_rows:
            try:
                if material_id in {str(item) for item in (json.loads(receipt["payload"]).get("material_ids") or [])}:
                    references.append("active_receipts")
                    break
            except (TypeError, json.JSONDecodeError):
                continue
        # Canonical map nodes keep evidence as JSON in ORM; inspect without
        # deleting any immutable map history.
        try:
            canonical_refs = connection.execute(
                "SELECT 1 FROM orm_knowledge_nodes WHERE map_revision_id IN (SELECT id FROM orm_knowledge_map_revisions WHERE workspace_id=?) AND source_refs LIKE ? LIMIT 1",
                (workspace_id, f"%{material_id}%"),
            ).fetchone()
            if canonical_refs:
                references.append("orm_knowledge_nodes")
            needle = f"%{material_id}%"
            canonical_checks = (
                (
                    "orm_knowledge_edges",
                    """SELECT 1 FROM orm_knowledge_edges
                       WHERE map_revision_id IN (SELECT id FROM orm_knowledge_map_revisions WHERE workspace_id=?)
                         AND source_refs LIKE ? LIMIT 1""",
                ),
                (
                    "orm_knowledge_map_revisions",
                    """SELECT 1 FROM orm_knowledge_map_revisions
                       WHERE workspace_id=? AND source_revisions LIKE ? LIMIT 1""",
                ),
                (
                    "orm_plan_execution_tasks",
                    """SELECT 1 FROM orm_plan_execution_tasks
                       WHERE workspace_id=? AND (source_evidence LIKE ? OR concept_snapshot LIKE ?) LIMIT 1""",
                ),
                (
                    "orm_plan_activation_records",
                    """SELECT 1 FROM orm_plan_activation_records
                       WHERE workspace_id=? AND plan_snapshot LIKE ? LIMIT 1""",
                ),
                (
                    "orm_knowledge_map_jobs",
                    """SELECT 1 FROM orm_knowledge_map_jobs
                       WHERE workspace_id=? AND (request_json LIKE ? OR result_json LIKE ?) LIMIT 1""",
                ),
            )
            for label, statement in canonical_checks:
                params = (workspace_id,) + (needle,) * (statement.count("?") - 1)
                if connection.execute(statement, params).fetchone():
                    references.append(label)
        except sqlite3.Error:
            pass
        if references:
            _error(
                "MATERIAL_REFERENCED",
                "资料仍被学习历史或知识地图引用，请使用可恢复归档",
                409,
                references=sorted(set(references)),
            )
        stored_path = str(row["stored_path"] or "")
        # Only conversion-only rows may be removed.  Any map, plan, or job
        # reference above turns this operation into a safe 409 instead.
        try:
            revision_ids = [
                str(item[0])
                for item in connection.execute(
                    "SELECT id FROM orm_source_revisions WHERE workspace_id=? AND source_id=?",
                    (workspace_id, material_id),
                ).fetchall()
            ]
            if revision_ids:
                placeholders = ",".join("?" for _ in revision_ids)
                connection.execute(
                    f"DELETE FROM orm_conversion_manifests WHERE source_revision_id IN ({placeholders})",
                    revision_ids,
                )
            connection.execute(
                "DELETE FROM orm_material_dispositions WHERE workspace_id=? AND source_id=?",
                (workspace_id, material_id),
            )
            connection.execute(
                "DELETE FROM orm_source_revisions WHERE workspace_id=? AND source_id=?",
                (workspace_id, material_id),
            )
        except sqlite3.Error:
            pass
        connection.execute("DELETE FROM active_materials WHERE id=? AND workspace_id=?", (material_id, workspace_id))
        connection.commit()
        if stored_path:
            try:
                path = Path(stored_path).resolve()
                if path.is_file() and UPLOAD_ROOT.resolve() in path.parents:
                    path.unlink()
            except (OSError, ValueError):
                # The database deletion is complete; a missing artifact is
                # retained in the response for operator reconciliation.
                pass
        return {
            "workspace_id": workspace_id,
            "material_id": material_id,
            "deleted": True,
            "physical_delete": True,
            "reversible": False,
            "reason": "资料未被任何计划、回执或知识地图引用，且调用方明确确认后已物理删除。",
        }


@router.post("/workspaces/{workspace_id}/materials/url")
async def upload_url_material(
    workspace_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Fetch one user-confirmed public article and ingest it through the same registry.

    URL candidates remain separate from the map until the learner confirms one;
    this endpoint only creates the ordinary active material and never mutates a
    receipt, knowledge map, plan, or mastery state.
    """
    url = str(body.get("url") or "").strip()
    if not url:
        _error("MATERIAL_URL_REQUIRED", "请先选择一份资料", 422)
    if classify_video_url(url).get("recognized"):
        return await _video_material_flow(workspace_id, body, url=url)
    try:
        fetched = await fetch_safe(url)
    except ValueError as exc:
        _error("MATERIAL_URL_FETCH_FAILED", f"资料网页无法安全读取：{exc}", 422)
    text = str(fetched.get("text") or "").strip()
    if not text:
        _error("MATERIAL_URL_EMPTY", "资料网页没有可索引的正文", 422)
    title = str(body.get("title") or fetched.get("title") or "网页资料").strip()
    safe_title = _safe_filename(title)[:120] or "网页资料"
    # UploadFile is used only as an adapter here so URL imports and local files
    # share exactly the same conversion, evidence, hashing, and deduplication path.
    upload = UploadFile(file=io.BytesIO(text.encode("utf-8")), filename=f"{safe_title}.txt")
    result = await upload_material(workspace_id, upload)
    result["source_url"] = str(fetched.get("final_url") or url)
    result["source_title"] = title
    _set_material_provenance(
        workspace_id,
        str(result["id"]),
        source_url=result["source_url"],
        source_kind="web_article",
        provenance={"source_url": result["source_url"], "title": title},
        preserve_existing=bool(result.get("deduplicated")),
    )
    with _connect() as connection:
        result = _public_material(connection, str(result["id"]))
    result["requested_source_url"] = str(fetched.get("final_url") or url)
    result["source_title"] = title
    return result


def _video_selection_values(body: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    urls: list[str] = []
    raw_values: list[Any] = []
    for key in (
        "selected_candidate_ids",
        "selected_material_ids",
        "supplementary_material_ids",
        "selected_supplementary_materials",
    ):
        value = body.get(key)
        if isinstance(value, list):
            raw_values.extend(value)
        elif value not in (None, ""):
            raw_values.append(value)
    for key in ("selected_candidate_urls", "selected_material_urls", "supplementary_material_urls"):
        value = body.get(key)
        if isinstance(value, list):
            raw_values.extend(value)
        elif value not in (None, ""):
            raw_values.append(value)
    for value in raw_values:
        if isinstance(value, Mapping):
            candidate = resolve_candidate(value.get("id") or value.get("candidate_id") or value.get("url"))
            if candidate:
                urls.append(candidate["url"])
            continue
        text = str(value or "").strip()
        if text.startswith(("http://", "https://")):
            urls.append(text)
        elif text:
            ids.append(text)
            candidate = resolve_candidate(text)
            if candidate:
                urls.append(candidate["url"])
    return list(dict.fromkeys(ids)), list(dict.fromkeys(urls))


def _video_transcript_confirmed(body: Mapping[str, Any]) -> bool:
    return bool(
        body.get("confirm_transcript")
        or body.get("transcript_confirmed")
        or body.get("confirm")
    )


async def _ingest_text_material(
    workspace_id: str,
    *,
    text: str,
    filename: str,
    source_url: str,
    source_kind: str,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    upload = UploadFile(file=io.BytesIO(text.encode("utf-8")), filename=_safe_filename(filename)[:180] or "material.txt")
    result = await upload_material(workspace_id, upload)
    material_id = str(result["id"])
    _set_material_provenance(
        workspace_id,
        material_id,
        source_url=source_url,
        source_kind=source_kind,
        provenance=provenance,
        preserve_existing=bool(result.get("deduplicated")),
    )
    with _connect() as connection:
        output = _public_material(connection, material_id)
    output["requested_source_url"] = source_url
    if provenance.get("source_family_id"):
        output["source_family_id"] = str(provenance["source_family_id"])
    if provenance.get("lineage"):
        output["lineage"] = dict(provenance["lineage"])
    if "independent_evidence" in provenance:
        output["independence"] = {
            "independent_evidence": bool(provenance.get("independent_evidence")),
            "classification": str(provenance.get("independence") or "unknown"),
        }
    return output


async def _ingest_selected_supplementary_materials(
    workspace_id: str,
    *,
    video_url: str,
    selected_urls: list[str],
    resolved_source_family_id: str = "",
) -> list[dict[str, Any]]:
    """Fetch and persist only the supplementary URLs the learner selected."""

    supplementary_materials: list[dict[str, Any]] = []
    family_id = str(resolved_source_family_id or source_family_id(video_url))
    for selected_url in selected_urls:
        try:
            fetched = await fetch_safe(selected_url)
        except ValueError as exc:
            _error("SUPPLEMENTARY_FETCH_FAILED", f"补充资料无法安全读取：{exc}", 422, source_url=selected_url)
        text = str(fetched.get("text") or "").strip()
        if not text:
            _error("SUPPLEMENTARY_EMPTY", "所选补充资料没有可索引的正文", 422, source_url=selected_url)
        title = str(fetched.get("title") or "补充资料").strip()
        final_url = str(fetched.get("final_url") or selected_url)
        derived = source_family_id(final_url) == family_id
        # A different URL is not proof that a note was independently authored.
        # Keep its lineage unknown unless an adapter provides explicit metadata;
        # unknown companions remain available as learning material but are not
        # advertised as independent evidence.
        relation = "derived_same_family" if derived else "unknown_companion"
        lineage = source_lineage(
            family_id,
            role="companion_material",
            relation=relation,
            independent=False,
            parent_url=video_url,
        )
        supplementary_materials.append(
            await _ingest_text_material(
                workspace_id,
                text=text,
                filename=f"{title}.txt",
                source_url=final_url,
                source_kind="supplementary_material",
                provenance={
                    "source_url": final_url,
                    "selected_url": selected_url,
                    "selected_explicitly": True,
                    "video_source_url": video_url,
                    "source_family_id": family_id,
                    "lineage": lineage,
                    "independent_evidence": False,
                    "independence": relation,
                },
            )
        )
    return supplementary_materials


async def _video_material_flow(
    workspace_id: str,
    body: Mapping[str, Any],
    *,
    url: str,
) -> dict[str, Any]:
    classification = classify_video_url(url)
    choice = body.get("find_additional_materials")
    if not isinstance(choice, bool):
        return {
            "schema": "mentorflow.video-material-flow.v1",
            "status": "awaiting_supplementary_choice",
            "source_url": url,
            "capability": classification,
            "supplementary_material_choice": {
                "required": True,
                "question": "在提取字幕前，是否搜索额外的讲义、笔记或文档？",
                "options": [
                    {"value": True, "label": "搜索补充资料"},
                    {"value": False, "label": "只使用确认后的视频字幕"},
                ],
            },
            "ingested": False,
        }
    ids, selected_urls = _video_selection_values(body)
    confirmed = _video_transcript_confirmed(body)
    if choice and not selected_urls and not confirmed:
        query = str(body.get("query") or body.get("search_query") or body.get("title") or url).strip()
        search = await search_supplementary_materials(query)
        search["candidates"] = remember_candidates(list(search.get("candidates") or []))
        family_id = source_family_id(url)
        for candidate in search["candidates"]:
            candidate["source_family_id"] = family_id
            candidate["lineage"] = source_lineage(
                family_id,
                role="companion_material",
                relation="unknown_companion",
                independent=False,
                parent_url=url,
            )
            candidate["independent_evidence"] = False
            candidate["independence"] = "unknown"
        return {
            "schema": "mentorflow.video-material-flow.v1",
            "status": "supplementary_candidates",
            "source_url": url,
            "capability": classification,
            "supplementary_material_choice": {"accepted": True, "required": False},
            "supplementary_search": search,
            "selection_required": True,
            "ingested": False,
        }
    try:
        preview = await extract_video_transcript(url)
    except VideoTranscriptError as exc:
        _error(exc.code, str(exc), exc.status, **exc.details)
    family_id = str(preview.get("source_family_id") or source_family_id(url))
    preview.setdefault("source_family_id", family_id)
    preview.setdefault(
        "lineage",
        source_lineage(
            family_id,
            role="video_transcript",
            relation="primary_transcript",
            independent=False,
            parent_url=url,
        ),
    )
    preview.setdefault(
        "independence",
        {"independent_evidence": False, "classification": "primary_source"},
    )
    disposition = preview.get("disposition") or {}
    subtitle = preview.get("subtitle") or {}
    base_response = {
        "schema": "mentorflow.video-material-flow.v1",
        "status": "transcript_preview",
        "source_url": url,
        "capability": classification,
        "transcript": preview,
        "subtitle_preview": {
            "quality": subtitle.get("quality") or {},
            "segments": subtitle.get("segments") or [],
            "timestamped": bool((subtitle.get("quality") or {}).get("timestamped")),
        },
        "ingested": False,
        "requires_transcript_confirmation": disposition.get("state") == "ready",
    }
    if disposition.get("state") != "ready":
        base_response["status"] = str(disposition.get("state") or "needs_review")
        base_response["limited_coverage_warning"] = "字幕缺失或质量不足，无法形成可验证的视频材料。"
        base_response["can_continue_with_supplementary_materials"] = bool(choice and selected_urls)
        if choice and selected_urls and body.get("confirm_supplement_only") is True:
            supplementary_materials = await _ingest_selected_supplementary_materials(
                workspace_id,
                video_url=str(preview.get("source_url") or url),
                selected_urls=selected_urls,
                resolved_source_family_id=family_id,
            )
            return {
                **base_response,
                "status": "ingested_supplement_only",
                "ingested": True,
                "video_material": None,
                "video_disposition": disposition,
                "supplementary_materials": supplementary_materials,
                "material_ids": [item["id"] for item in supplementary_materials],
                "separate_evidence": True,
                "limited_coverage_warning": "视频字幕不可用，已按你的确认排除视频，仅使用所选讲义或文档生成计划。",
            }
        return base_response
    if choice and not selected_urls:
        _error(
            "SUPPLEMENTARY_SELECTION_REQUIRED",
            "请明确选择要加入学习计划的补充资料；搜索结果不会自动纳入。",
            422,
            candidate_ids=ids,
        )
    if not confirmed:
        if not choice:
            base_response["limited_coverage_warning"] = "你选择不搜索补充资料；学习内容仅覆盖已确认的视频字幕。"
        return base_response
    transcript_text = str(subtitle.get("text") or "").strip()
    family_id = str(preview.get("source_family_id") or source_family_id(url))
    transcript_lineage = preview.get("lineage") if isinstance(preview.get("lineage"), Mapping) else source_lineage(
        family_id,
        role="video_transcript",
        relation="primary_transcript",
        independent=False,
        parent_url=url,
    )
    transcript_material = await _ingest_text_material(
        workspace_id,
        text=transcript_text,
        filename=f"{preview.get('title') or 'video'} transcript.txt",
        source_url=url,
        source_kind="video_transcript",
        provenance={
            "source_url": url,
            "provider": preview.get("provider"),
            "language": subtitle.get("language"),
            "automatic": bool(subtitle.get("automatic")),
            "quality": subtitle.get("quality") or {},
            "timestamps": subtitle.get("segments") or [],
            "source_family_id": family_id,
            "lineage": dict(transcript_lineage),
            "independent_evidence": False,
            "independence": "primary_source",
        },
    )
    supplementary_materials = await _ingest_selected_supplementary_materials(
        workspace_id,
        video_url=str(preview.get("source_url") or url),
        selected_urls=selected_urls,
        resolved_source_family_id=family_id,
    )
    return {
        **base_response,
        "status": "ingested",
        "ingested": True,
        "transcript_material": transcript_material,
        "supplementary_materials": supplementary_materials,
        "material_ids": [transcript_material["id"], *(item["id"] for item in supplementary_materials)],
        "separate_evidence": True,
        "limited_coverage_warning": (
            "你选择不搜索补充资料；学习内容仅覆盖已确认的视频字幕。" if not choice else None
        ),
    }


@router.post("/workspaces/{workspace_id}/materials/video/preview")
async def preview_video_material(
    workspace_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    url = str(body.get("url") or "").strip()
    if not url:
        _error("MATERIAL_URL_REQUIRED", "请先选择一份视频资料", 422)
    if not classify_video_url(url).get("recognized"):
        _error("VIDEO_URL_UNSUPPORTED", "该链接不是可识别的视频地址", 415)
    return await _video_material_flow(workspace_id, body, url=url)


@router.post("/workspaces/{workspace_id}/materials/video/search")
async def search_video_supplementary_materials(
    workspace_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    del workspace_id
    url = str(body.get("url") or "").strip()
    if not url or not classify_video_url(url).get("recognized"):
        _error("VIDEO_URL_UNSUPPORTED", "该链接不是可识别的视频地址", 415)
    if body.get("find_additional_materials") is not True:
        _error("SUPPLEMENTARY_SEARCH_NOT_AUTHORIZED", "请先明确同意搜索额外讲义、笔记或文档", 403)
    query = str(body.get("query") or body.get("search_query") or body.get("title") or url).strip()
    try:
        result = await search_supplementary_materials(query)
    except VideoTranscriptError as exc:
        _error(exc.code, str(exc), exc.status, **exc.details)
    result["candidates"] = remember_candidates(list(result.get("candidates") or []))
    family_id = source_family_id(url)
    for candidate in result["candidates"]:
        candidate["source_family_id"] = family_id
        candidate["lineage"] = source_lineage(
            family_id,
            role="companion_material",
            relation="unknown_companion",
            independent=False,
            parent_url=url,
        )
        candidate["independent_evidence"] = False
        candidate["independence"] = "unknown"
    return {"schema": "mentorflow.video-material-flow.v1", "status": "supplementary_candidates", "source_url": url, "supplementary_search": result, "selection_required": True, "ingested": False}


@router.post("/workspaces/{workspace_id}/materials/video/ingest")
async def ingest_video_material(
    workspace_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    url = str(body.get("url") or "").strip()
    if not url or not classify_video_url(url).get("recognized"):
        _error("VIDEO_URL_UNSUPPORTED", "该链接不是可识别的视频地址", 415)
    if "find_additional_materials" not in body:
        _error("SUPPLEMENTARY_CHOICE_REQUIRED", "字幕提取前必须明确是否搜索补充资料", 422)
    if not isinstance(body.get("find_additional_materials"), bool):
        _error("SUPPLEMENTARY_CHOICE_REQUIRED", "find_additional_materials 必须是布尔值", 422)
    return await _video_material_flow(workspace_id, body, url=url)


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
    minutes = int(
        payload.get("daily_task_minutes")
        or SCHEDULING_POLICY.default_daily_task_minutes
    )
    if allocation_mode == "system" and not SCHEDULING_POLICY.minimum_daily_minutes <= minutes <= SCHEDULING_POLICY.maximum_special_date_minutes:
        minutes = SCHEDULING_POLICY.default_daily_task_minutes
    elif not SCHEDULING_POLICY.minimum_daily_minutes <= minutes <= SCHEDULING_POLICY.maximum_special_date_minutes:
        _error("INVALID_DAILY_TASK_MINUTES", f"每日任务分钟预算必须在 {SCHEDULING_POLICY.minimum_daily_minutes} 到 {SCHEDULING_POLICY.maximum_special_date_minutes} 之间", 422)
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
        if not 0 <= special_minutes <= SCHEDULING_POLICY.maximum_special_date_minutes:
            _error("INVALID_SPECIAL_DATE_MINUTES", f"特殊日期分钟数必须在 0 到 {SCHEDULING_POLICY.maximum_special_date_minutes} 之间", 422)
        special_date_minutes[special_day.isoformat()] = special_minutes
    raw_period_ratio = payload.get("initial_learning_period_ratio")
    initial_learning_period_ratio: float | None = None
    if raw_period_ratio is not None:
        try:
            initial_learning_period_ratio = float(raw_period_ratio)
        except (TypeError, ValueError):
            _error("INVALID_LEARNING_PERIOD_RATIO", "首学周期比例必须是 0 到 1 之间的数字", 422)
        if not (
            SCHEDULING_POLICY.minimum_initial_learning_period_ratio
            <= initial_learning_period_ratio
            <= SCHEDULING_POLICY.maximum_initial_learning_period_ratio
        ):
            _error(
                "INVALID_LEARNING_PERIOD_RATIO",
                "首学周期比例必须在 "
                f"{SCHEDULING_POLICY.minimum_initial_learning_period_ratio} 到 "
                f"{SCHEDULING_POLICY.maximum_initial_learning_period_ratio} 之间",
                422,
            )
    review_rounds = int(payload.get("review_rounds") or SCHEDULING_POLICY.default_review_rounds)
    if not SCHEDULING_POLICY.minimum_review_rounds <= review_rounds <= SCHEDULING_POLICY.maximum_review_rounds:
        _error(
            "INVALID_REVIEW_ROUNDS",
            f"完整复习轮数必须在 {SCHEDULING_POLICY.minimum_review_rounds} 到 {SCHEDULING_POLICY.maximum_review_rounds} 之间",
            422,
        )
    raw_resource_discovery = payload.get("discover_external_resources", True)
    if isinstance(raw_resource_discovery, str):
        discover_external_resources = raw_resource_discovery.strip().casefold() not in {
            "",
            "0",
            "false",
            "no",
            "off",
        }
    else:
        discover_external_resources = bool(raw_resource_discovery)
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
        "teacher_persona_id": str(payload.get("teacher_persona_id") or "")[: TEXT_LIMITS_POLICY.preference_key_characters],
        "teacher_persona_name": str(payload.get("teacher_persona_name") or "")[: TEXT_LIMITS_POLICY.brief_characters],
        "teacher_persona": str(payload.get("teacher_persona") or "")[: TEXT_LIMITS_POLICY.long_text_characters],
        "teacher_visual_profile_id": str(
            payload.get("teacher_visual_profile_id") or ""
        )[: TEXT_LIMITS_POLICY.preference_key_characters],
        "teacher_visual_profile_name": str(
            payload.get("teacher_visual_profile_name") or ""
        )[: TEXT_LIMITS_POLICY.preference_key_characters],
        "teacher_visual_manifest_url": str(
            payload.get("teacher_visual_manifest_url") or ""
        )[: TEXT_LIMITS_POLICY.objective_characters],
        "learner_interests": str(payload.get("learner_interests") or "")[: TEXT_LIMITS_POLICY.explanation_characters],
        "allow_schedule_suggestions": bool(payload.get("allow_schedule_suggestions", True)),
        # Keep this opt-in explicit for newly-created tasks while preserving
        # the historical behavior for receipts that predate the field.  The
        # generic knowledge-map endpoint treats a missing value as enabled;
        # clients should always send a boolean for new tasks.
        "discover_external_resources": discover_external_resources,
        "material_ids": list(dict.fromkeys(str(item) for item in payload["material_ids"])),
        "updated_at": _now(),
    }


def _sync_receipt_learner_preference_profile(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    receipt: Mapping[str, Any],
) -> None:
    """Persist only the interests the learner explicitly entered in the receipt."""

    profile = build_learner_preference_profile(
        workspace_id=workspace_id,
        explicit_preferences=explicit_preferences_from_receipt(receipt),
    )
    interests = [
        item.value for item in profile.preferences if item.key == "interest"
    ]
    preference_id = "learner_pref_" + _hash_text(
        f"{workspace_id}:interest"
    )[:32]
    if interests:
        now = _now()
        connection.execute(
            """INSERT INTO orm_learner_preferences
               (id,workspace_id,preference_key,value_json,source,status,revision,
                created_at,updated_at)
               VALUES (?,?,?,?,'explicit','active',1,?,?)
               ON CONFLICT(workspace_id,preference_key) DO UPDATE SET
                 value_json=excluded.value_json,
                 source='explicit',
                 status='active',
                 revision=orm_learner_preferences.revision+1,
                 updated_at=excluded.updated_at""",
            (
                preference_id,
                workspace_id,
                "interest",
                json.dumps({"items": interests}, ensure_ascii=False),
                now,
                now,
            ),
        )
    else:
        connection.execute(
            """UPDATE orm_learner_preferences
               SET status='inactive',revision=revision+1,updated_at=?
               WHERE workspace_id=? AND preference_key='interest'
                 AND source='explicit' AND status!='inactive'""",
            (_now(), workspace_id),
        )


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
                estimated_initial = max(
                    SCHEDULING_POLICY.structural_estimate_minimum_initial_minutes,
                    chunk_count
                    * SCHEDULING_POLICY.structural_estimate_minutes_per_chunk
                    + SCHEDULING_POLICY.structural_estimate_base_minutes,
                )
                rounding = SCHEDULING_POLICY.allocation_rounding_minutes
                estimated_review_round = max(
                    SCHEDULING_POLICY.structural_estimate_minimum_review_minutes,
                    math.ceil(
                        estimated_initial
                        * SCHEDULING_POLICY.structural_estimate_review_ratio
                        / rounding
                    )
                    * rounding,
                )
                estimation_source = "material_structure_estimate"
            estimated_total = (
                estimated_initial
                + estimated_review_round
                * int(
                    receipt.get("review_rounds")
                    or SCHEDULING_POLICY.default_review_rounds
                )
            )
            rounding = SCHEDULING_POLICY.allocation_rounding_minutes
            recommended = max(
                SCHEDULING_POLICY.structural_estimate_minimum_daily_minutes,
                math.ceil(estimated_total / len(study_dates) / rounding)
                * rounding,
            )
            receipt["daily_task_minutes"] = recommended
            receipt["allocation"] = {
                "mode": "system",
                "estimated_total_minutes": estimated_total,
                "initial_learning_minutes": estimated_initial,
                "one_review_round_minutes": estimated_review_round,
                "review_rounds": int(
                    receipt.get("review_rounds")
                    or SCHEDULING_POLICY.default_review_rounds
                ),
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
        _sync_receipt_learner_preference_profile(
            connection,
            workspace_id=workspace_id,
            receipt=receipt,
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


@router.get("/workspaces/{workspace_id}/learner-preference-profile")
def get_learner_preference_profile(workspace_id: str) -> dict[str, Any]:
    """Return the explicit-only profile used for teacher analogies."""

    with _connect() as connection:
        rows = connection.execute(
            """SELECT preference_key,value_json,source,status,revision
               FROM orm_learner_preferences
               WHERE workspace_id=? AND source='explicit' AND status='active'
               ORDER BY preference_key""",
            (workspace_id,),
        ).fetchall()
    values = [
        {
            "preference_key": row["preference_key"],
            "value_json": json.loads(row["value_json"] or "{}"),
            "source": row["source"],
            "status": row["status"],
        }
        for row in rows
    ]
    profile = build_learner_preference_profile(
        workspace_id=workspace_id,
        explicit_preferences=values,
        revision=max((int(row["revision"] or 0) for row in rows), default=0),
    )
    return profile.model_dump(mode="json")


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


@router.post("/workspaces/{workspace_id}/knowledge-graph/generate", status_code=410)
def generate_knowledge_graph(workspace_id: str) -> dict[str, Any]:
    _error(
        "LEGACY_KNOWLEDGE_GRAPH_ENDPOINT",
        "旧知识图谱生成接口已停用，请使用 /api/active-teaching/workspaces/{workspace_id}/knowledge-map/jobs。",
        410,
        canonical_path=f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/jobs",
    )
    # Retained below only as source-history context; the explicit 410 above is
    # the executable boundary and prevents the legacy generator from running.
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
        _capture_knowledge_progress(connection, workspace_id)
        progress = _progress_snapshots(connection, workspace_id)
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
            progress_item = progress.get(
                (str(chunk["material_id"]), normalized),
                {},
            )
            preserved_mastery = max(
                0.0, float(progress_item.get("mastery") or 0)
            )
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
                    preserved_mastery,
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
                    "current_mastery": preserved_mastery,
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


def _active_knowledge_map_revision(connection: sqlite3.Connection, workspace_id: str) -> dict[str, Any]:
    """Read the canonical active ORM revision without routing back to itself."""
    try:
        with SessionLocal() as session:
            revision = session.scalar(
                select(KnowledgeMapRevisionORM)
                .where(
                    KnowledgeMapRevisionORM.workspace_id == workspace_id,
                    KnowledgeMapRevisionORM.is_current.is_(True),
                    KnowledgeMapRevisionORM.status.in_(["active", "ready_with_review"]),
                )
                .order_by(desc(KnowledgeMapRevisionORM.revision))
            )
            if revision is None:
                return {}
            node_rows = session.scalars(
                select(KnowledgeNodeORM)
                .where(KnowledgeNodeORM.map_revision_id == revision.id)
                .order_by(KnowledgeNodeORM.node_id)
            ).all()
            edge_rows = session.scalars(
                select(KnowledgeEdgeORM)
                .where(KnowledgeEdgeORM.map_revision_id == revision.id)
                .order_by(KnowledgeEdgeORM.edge_id)
            ).all()
            nodes = [
                {
                    "id": item.node_id,
                    "name": item.label,
                    "explanation": item.summary,
                    "node_type": (item.history or {}).get("node_type", item.kind),
                    "difficulty": (item.history or {}).get("difficulty", "medium"),
                    "acceptance_method": (item.history or {}).get("acceptance_method", ""),
                    "mastery_estimate": (item.history or {}).get("mastery_estimate", {}),
                    "confidence": float((item.history or {}).get("confidence", 0.0)),
                    "layer": (item.history or {}).get("layer", "material"),
                    "status": item.status,
                    "evidence": item.source_refs or [],
                    "source_evidence": item.source_refs or [],
                    "material_ids": sorted({
                        str(ref.get("material_id") or ref.get("source_id") or "")
                        for ref in (item.source_refs or [])
                        if isinstance(ref, Mapping)
                    }),
                }
                for item in node_rows
            ]
            edges = []
            for item in edge_rows:
                refs = list(item.source_refs or [])
                metadata = (
                    dict(refs[0])
                    if refs and isinstance(refs[0], Mapping) and "relation" in refs[0]
                    else {}
                )
                evidence = refs[1:] if metadata else refs
                edges.append(
                    {
                        "id": item.edge_id,
                        "source_id": item.source_node_id,
                        "target_id": item.target_node_id,
                        "prerequisite_id": item.source_node_id,
                        "dependent_id": item.target_node_id,
                        "relation": item.relation,
                        "status": metadata.get("status", "pending"),
                        "layer": metadata.get("layer", "material"),
                        "reason": metadata.get("reason", ""),
                        "evidence": evidence,
                        "source_evidence": evidence,
                    }
                )
            return {
                "schema": "mentorflow.knowledge-map.v1",
                "policy_version": POLICY_VERSION,
                "workspace_id": workspace_id,
                "revision": int(revision.revision),
                "revision_id": revision.id,
                "state": revision.status,
                "source_fingerprint": revision.fingerprint,
                "provider_meta": (revision.quality or {}).get("provider_meta", {}),
                "quality": revision.quality or {},
                "nodes": nodes,
                "edges": edges,
                "knowledge_points": nodes,
            }
    except Exception as exc:
        raise RuntimeError("canonical knowledge-map revision could not be read") from exc


@router.get("/workspaces/{workspace_id}/knowledge-graph")
def get_knowledge_graph(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        result = _active_knowledge_map_revision(connection, workspace_id)
        if not result:
            _error(
                "KNOWLEDGE_MAP_NOT_FOUND",
                "当前工作区没有可用的 active knowledge-map revision",
                404,
            )
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
        _capture_knowledge_progress(connection, workspace_id)
        progress = _progress_snapshots(connection, workspace_id)
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
            segment_limit = min(SCHEDULING_POLICY.maximum_task_segment_minutes, budget)
            split_count = max(1, (minutes + segment_limit - 1) // segment_limit)
            remaining = minutes
            for split_index in range(split_count):
                part_minutes = min(remaining, segment_limit)
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
            evidence = json.loads(point["source_evidence"])
            progress_item = progress.get(
                _progress_snapshot_key(
                    point["material_id"], point["normalized_name"], point["name"]
                ),
                {},
            )
            preserved_completed = bool(
                float(progress_item.get("mastery") or 0) >= 0.8
            )
            capacity_exceeded = day_index >= len(dates)
            if capacity_exceeded and not preserved_completed:
                unscheduled += minutes
            if capacity_exceeded:
                task_day = dates[-1].isoformat()
                persisted_day_index = 999999
                persisted_task_order = 999999
            else:
                task_order += 1
                used += minutes
                task_day = dates[day_index].isoformat()
                persisted_day_index = day_index
                persisted_task_order = task_order
            task = {
                "id": _id("active_task"),
                "plan_id": plan_id,
                "workspace_id": workspace_id,
                "day_date": task_day,
                "day_index": persisted_day_index,
                "task_order": persisted_task_order,
                "knowledge_point_id": point["id"],
                "title": f"{point['name']}{suffix}",
                "objective": f"围绕“{point['name']}”完成讲解、示例、理解检测和独立验收。",
                "source_evidence": evidence,
                "acceptance_condition": point["acceptance_method"],
                "estimated_task_minutes": minutes,
                "completed_task_minutes": minutes if preserved_completed else 0,
                "actual_elapsed_minutes": 0.0,
                "status": (
                    "completed"
                    if preserved_completed
                    else "unscheduled"
                    if capacity_exceeded
                    else "planned"
                ),
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
                   (id,plan_id,workspace_id,day_date,original_day_date,day_index,task_order,knowledge_point_id,title,objective,source_evidence,acceptance_condition,estimated_task_minutes,completed_task_minutes,actual_elapsed_minutes,status)
                   VALUES (:id,:plan_id,:workspace_id,:day_date,:original_day_date,:day_index,:task_order,:knowledge_point_id,:title,:objective,:source_evidence,:acceptance_condition,:estimated_task_minutes,:completed_task_minutes,:actual_elapsed_minutes,:status)""",
                {
                    **task,
                    "original_day_date": task["day_date"],
                    "source_evidence": json.dumps(task["source_evidence"], ensure_ascii=False),
                },
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
        task.update(task_completion_fields(task))
    days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        days[task["day_date"]].append(task)
    receipt = json.loads(row["receipt"])
    level = str(
        receipt.get("current_foundation")
        or receipt.get("current_level")
        or "beginner"
    ).casefold()
    elapsed_multipliers = SCHEDULING_POLICY.learner_level_elapsed_multipliers.get(
        level,
        SCHEDULING_POLICY.default_elapsed_multipliers,
    )
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
        "activity_by_day": activity_by_day(connection, plan_id),
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
    result["review_rounds"] = int(
        receipt.get("review_rounds")
        or SCHEDULING_POLICY.default_review_rounds
    )
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


def _execution_receipt(
    connection: sqlite3.Connection,
    plan_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT receipt FROM active_plans WHERE id=?",
        (plan_id,),
    ).fetchone()
    return _json_object(row["receipt"]) if row else {}


def _uses_canonical_execution(
    connection: sqlite3.Connection,
    plan_id: str,
) -> bool:
    return str(_execution_receipt(connection, plan_id).get("activation_source") or "") in {
        "mastery-plan-preview.v1",
        "material-change-preview.v1",
    }


def _task_rows(connection: sqlite3.Connection, plan_id: str) -> list[sqlite3.Row]:
    if _uses_canonical_execution(connection, plan_id):
        return connection.execute(
            """SELECT * FROM orm_plan_execution_tasks
               WHERE plan_id=?
                 AND (
                   task_type='initial_learning'
                   OR (
                     task_type='review'
                     AND COALESCE(NULLIF(not_before_date,''),day_date)<=?
                   )
                 )
               ORDER BY CASE WHEN task_type='initial_learning' THEN 0 ELSE 1 END,
                        point_sequence,day_index,task_order,id""",
            (plan_id, date.today().isoformat()),
        ).fetchall()
    return connection.execute(
        """SELECT * FROM active_plan_tasks
           WHERE plan_id=? AND task_type='initial_learning'
           ORDER BY point_sequence,day_index,task_order,id""",
        (plan_id,),
    ).fetchall()


def _is_canonical_task(task: Mapping[str, Any] | sqlite3.Row) -> bool:
    keys = task.keys() if hasattr(task, "keys") else ()
    return "map_revision_id" in keys and bool(task["map_revision_id"])


def _point_for_task(
    connection: sqlite3.Connection,
    task: Mapping[str, Any] | sqlite3.Row,
) -> Mapping[str, Any] | sqlite3.Row:
    """Resolve a task concept without projecting it into a legacy graph row."""

    if not _is_canonical_task(task):
        legacy = connection.execute(
            "SELECT * FROM active_knowledge_points WHERE id=?",
            (task["knowledge_point_id"],),
        ).fetchone()
        if legacy is not None:
            return legacy
        _error("ACTIVE_KNOWLEDGE_POINT_NOT_FOUND", "知识点不存在", 409)
    snapshot = _json_object(task["concept_snapshot"])
    if (
        str(snapshot.get("map_revision_id") or "") != str(task["map_revision_id"])
        or str(snapshot.get("knowledge_point_id") or "")
        != str(task["knowledge_point_id"])
    ):
        _error(
            "PLAN_CONCEPT_SNAPSHOT_INVALID",
            "计划中的概念快照与知识地图修订不一致",
            409,
        )
    evidence = snapshot.get("source_evidence") or []
    if not isinstance(evidence, list):
        evidence = []
    latest_mastery = connection.execute(
        """SELECT MAX(score) FROM orm_plan_mastery_events
           WHERE map_revision_id=? AND knowledge_point_id=?
             AND event_type='mastery_confirmed' AND passed=1""",
        (task["map_revision_id"], task["knowledge_point_id"]),
    ).fetchone()
    mastery = float(latest_mastery[0] or 0) if latest_mastery else 0.0
    material_id = next(
        (
            str(item.get("material_id") or item.get("source_id") or "")
            for item in evidence
            if isinstance(item, dict)
        ),
        "",
    )
    return {
        "id": str(task["knowledge_point_id"]),
        "workspace_id": str(task["workspace_id"]),
        "material_id": material_id,
        "normalized_name": str(task["knowledge_point_id"]),
        "name": str(snapshot.get("name") or task["title"] or ""),
        "explanation": str(snapshot.get("explanation") or task["objective"] or ""),
        "difficulty": str(snapshot.get("difficulty") or "medium"),
        "estimated_task_minutes": int(task["estimated_task_minutes"] or 0),
        "mastery": mastery,
        "acceptance_method": str(
            snapshot.get("acceptance_method")
            or task["acceptance_condition"]
            or ""
        ),
        "source_evidence": json.dumps(evidence, ensure_ascii=False),
        "isolated": 0,
        "map_revision_id": str(task["map_revision_id"]),
        "evidence_hash": str(snapshot.get("evidence_hash") or ""),
    }


def _add_task_elapsed(
    connection: sqlite3.Connection,
    task: Mapping[str, Any] | sqlite3.Row,
    elapsed: float,
) -> None:
    table = "orm_plan_execution_tasks" if _is_canonical_task(task) else "active_plan_tasks"
    connection.execute(
        f"UPDATE {table} SET actual_elapsed_minutes=actual_elapsed_minutes+? WHERE id=?",
        (elapsed, task["id"]),
    )


def _complete_execution_task(
    connection: sqlite3.Connection,
    task: Mapping[str, Any] | sqlite3.Row,
) -> None:
    table = "orm_plan_execution_tasks" if _is_canonical_task(task) else "active_plan_tasks"
    columns = {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if "completed_at" not in columns:
        # Minimal in-memory fixtures from older integrations may omit the
        # additive audit column; migration-backed application databases always
        # take the timestamp branch below.
        connection.execute(
            f"""UPDATE {table} SET status='completed',
                completed_task_minutes=estimated_task_minutes WHERE id=?""",
            (task["id"],),
        )
        return
    connection.execute(
        f"""UPDATE {table} SET status='completed',
            completed_task_minutes=estimated_task_minutes,
            completed_at=COALESCE(completed_at, ?) WHERE id=?""",
        (_now(), task["id"]),
    )


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
                return max(
                    SCHEDULING_POLICY.minimum_review_task_minutes,
                    min(SCHEDULING_POLICY.maximum_task_segment_minutes, minutes),
                )
    return max(
        SCHEDULING_POLICY.minimum_review_task_minutes,
        min(
            SCHEDULING_POLICY.fallback_maximum_review_task_minutes,
            round(
                int(point["estimated_task_minutes"])
                * SCHEDULING_POLICY.fallback_review_minutes_ratio
            ),
        ),
    )


def _review_occurrence_minutes(total_minutes: int, cycle: int) -> int:
    """Each configured cycle is one complete review of the knowledge point."""
    return max(SCHEDULING_POLICY.minimum_review_occurrence_minutes, int(total_minutes))


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
        **task_completion_fields(row),
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
    factor = (
        TEACHING_POLICY.remediation_easy_factor
        if difficulty in {"easy", "简单"}
        else TEACHING_POLICY.remediation_default_factor
    )
    if difficulty in {"hard", "困难"}:
        factor = TEACHING_POLICY.remediation_hard_factor
    estimate = round(int(task["estimated_task_minutes"]) * factor) + min(
        TEACHING_POLICY.remediation_maximum_failure_increment_minutes,
        max(0, failure_count)
        * TEACHING_POLICY.remediation_failure_increment_minutes,
    )
    return max(
        TEACHING_POLICY.remediation_minimum_task_minutes,
        min(TEACHING_POLICY.remediation_maximum_task_minutes, estimate),
    )


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


def _safe_external_practice_url(value: str) -> str:
    candidate = str(value or "").strip().rstrip("。，、；;）)]}>\"'")
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    host = parsed.hostname.casefold()
    if (
        host in {"localhost", "127.0.0.1", "::1"}
        or host.endswith(".local")
        or host.endswith(".internal")
    ):
        return ""
    return candidate


def _practice_resource(
    *,
    title: str,
    url: str,
    source: str,
    authority: str,
) -> dict[str, str] | None:
    safe_url = _safe_external_practice_url(url)
    if not safe_url:
        return None
    host = str(urlparse(safe_url).hostname or "")
    provider = host
    return {
        "id": _hash_text(f"{source}:{safe_url}")[:20],
        "kind": "external_practice",
        "title": str(title or "外部实践资源")[:180],
        "url": safe_url,
        "host": host,
        "provider": provider,
        "source": source,
        "authority": authority,
        "open_mode": "new_tab",
    }


def _practice_resources_for_session(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    state: dict[str, Any],
) -> list[dict[str, str]]:
    tasks = _task_rows(connection, session["plan_id"])
    if not tasks:
        return []
    route_index = min(
        max(0, int(state.get("route_index") or 0)),
        len(tasks) - 1,
    )
    task = tasks[route_index]
    point = _point_for_task(connection, task)
    try:
        evidence = json.loads(point["source_evidence"])
    except (TypeError, json.JSONDecodeError):
        evidence = []

    resources: list[dict[str, str]] = []
    seen: set[str] = set()
    # Resources discovered during knowledge-map generation are backed by a
    # real web-search citation and work for any uploaded subject.
    try:
        from .learning_resources import public_resources

        plan_receipt = _execution_receipt(connection, str(session["plan_id"]))
        map_revision_id = (
            str(plan_receipt.get("knowledge_map_revision_id") or "")
            if _is_canonical_task(task)
            else None
        )
        discovered_resources = []
        for candidate in public_resources(
            connection,
            str(session["workspace_id"]),
            map_revision_id=map_revision_id,
        ):
            metadata = candidate.get("discovery_meta") or {}
            # A task-scoped resource is a general tool for the course (for
            # example an official problem platform).  It is intentionally
            # available from every point; point-specific resources stay local
            # to the current lesson.
            if (
                str(candidate.get("knowledge_point_id") or "")
                == str(point["id"])
                or str(metadata.get("scope") or "") == "task"
            ):
                discovered_resources.append(candidate)

        for discovered in discovered_resources:
            url = _safe_external_practice_url(str(discovered.get("url") or ""))
            if not url or url in seen:
                continue
            resources.append(
                {
                    "id": str(discovered["id"]),
                    "kind": str(discovered.get("kind") or "external_practice"),
                    "title": str(discovered.get("title") or point["name"]),
                    "description": str(discovered.get("description") or ""),
                    "url": url,
                    "host": str(discovered.get("host") or ""),
                    "provider": str(
                        discovered.get("provider")
                        or discovered.get("host")
                        or ""
                    ),
                    "source": str(
                        discovered.get("source")
                        or "web_search_grounding"
                    ),
                    "authority": str(
                        discovered.get("authority")
                        or "search_grounded"
                    ),
                    "search_query": str(
                        discovered.get("search_query") or ""
                    ),
                    "open_mode": "new_tab",
                }
            )
            seen.add(url)
    except sqlite3.Error:
        # Resource failures never mutate or block the revision-bound lesson.
        pass
    for item in evidence:
        searchable = "\n".join(
            str(item.get(key) or "")
            for key in ("heading", "quote", "source_file")
        )
        chunk_id = str(item.get("chunk_id") or "")
        if chunk_id:
            chunk = connection.execute(
                "SELECT text FROM active_chunks WHERE id=?",
                (chunk_id,),
            ).fetchone()
            if chunk:
                searchable += f"\n{chunk['text']}"
        for raw_url in re.findall(r"https://[^\s<>'\"]+", searchable):
            resource = _practice_resource(
                title=str(point["name"]),
                url=raw_url,
                source="uploaded_material",
                authority="source_provided",
            )
            if resource and resource["url"] not in seen:
                resources.append(resource)
                seen.add(resource["url"])
    return resources[:4]


def _public_teaching_attachment(row: sqlite3.Row) -> dict[str, Any]:
    kind = attachment_kind(
        str(row["original_filename"]),
        str(row["media_type"]),
    )
    return {
        "id": str(row["id"]),
        "original_filename": str(row["original_filename"]),
        "media_type": str(row["media_type"]),
        "kind": kind,
        "previewable": kind == "image",
        "size_bytes": int(row["size_bytes"]),
        "width": int(row["width"]),
        "height": int(row["height"]),
        "status": str(row["status"]),
        "analysis_mode": str(row["analysis_mode"]),
        "analysis_text": str(row["analysis_text"] or ""),
        "file_url": (
            f"/api/active-teaching/attachments/{row['id']}/file"
        ),
        "created_at": str(row["created_at"]),
    }


def _current_point_projection(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    state: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the bounded public view for the point currently in the lesson.

    The durable session keeps its complete transcript for audit and resume.
    Native clients render this projection instead, where progress counts
    distinct knowledge points rather than execution tasks.
    """

    tasks = _task_rows(connection, str(session["plan_id"]))
    point_ids: list[str] = []
    for task in tasks:
        point_id = str(task["knowledge_point_id"] or "")
        if point_id and point_id not in point_ids:
            point_ids.append(point_id)

    current_point_id = str(state.get("current_knowledge_point_id") or "")
    active_task_id = str(state.get("active_task_id") or "")
    current_task = next(
        (task for task in tasks if str(task["id"] or "") == active_task_id),
        None,
    )
    if current_task is None and current_point_id:
        current_task = next(
            (task for task in tasks if str(task["knowledge_point_id"] or "") == current_point_id),
            None,
        )
    if current_task is not None:
        current_point_id = str(current_task["knowledge_point_id"] or current_point_id)

    point: Mapping[str, Any] | sqlite3.Row | None = None
    if current_task is not None:
        try:
            point = _point_for_task(connection, current_task)
        except Exception:
            # A stale session must remain readable after a plan is rebuilt.
            point = None
    if point is not None:
        point_name = str(point["name"] or "").strip()
    else:
        point_name = ""
    point_name = (
        point_name
        or str(current_task["title"] if current_task is not None else "").strip()
        or str(state.get("current_knowledge_point_name") or "").strip()
        or "当前知识点"
    )
    ordinal = point_ids.index(current_point_id) + 1 if current_point_id in point_ids else 0
    total = len(point_ids)
    progress_label = f"{ordinal}/{total}" if ordinal and total else ""

    # Structured teacher turns identify their point. Learner replies inherit
    # the most recent teacher point, preserving the point-scoped conversation
    # without changing durable message rows.
    current_messages: list[dict[str, Any]] = []
    message_point_id = ""
    for item in messages:
        turn = item.get("teacher_turn")
        concept = turn.get("concept") if isinstance(turn, dict) else None
        concept_id = str(concept.get("id") or "") if isinstance(concept, dict) else ""
        concept_name = str(concept.get("name") or "") if isinstance(concept, dict) else ""
        if concept_id:
            message_point_id = concept_id
        elif concept_name and concept_name == point_name:
            message_point_id = current_point_id
        if message_point_id == current_point_id and current_point_id:
            current_messages.append(item)

    evidence: list[dict[str, Any]] = []
    seen_evidence: set[str] = set()
    raw_point_evidence: Any = []
    if point is not None and "source_evidence" in point.keys():
        raw_point_evidence = point["source_evidence"]
    try:
        point_evidence = (
            json.loads(raw_point_evidence)
            if isinstance(raw_point_evidence, str)
            else raw_point_evidence
        )
    except (TypeError, json.JSONDecodeError):
        point_evidence = []
    for candidate in point_evidence if isinstance(point_evidence, list) else []:
        if isinstance(candidate, dict):
            marker = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            if marker not in seen_evidence:
                seen_evidence.add(marker)
                evidence.append(dict(candidate))
    for message in current_messages:
        for candidate in message.get("citations") or []:
            if isinstance(candidate, dict):
                marker = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
                if marker not in seen_evidence:
                    seen_evidence.add(marker)
                    evidence.append(dict(candidate))

    state["current_point_ordinal"] = ordinal
    state["current_point_total"] = total
    state["current_point_label"] = (
        f"{progress_label} · {point_name}" if progress_label else point_name
    )
    return {
        "id": current_point_id,
        "knowledge_point_id": current_point_id,
        "name": point_name,
        "knowledge_point_name": point_name,
        "ordinal": ordinal,
        "total": total,
        "progress_label": progress_label,
        "messages": current_messages,
        "message_projection": current_messages,
        "evidence": evidence,
        "evidence_projection": evidence,
    }


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
        try:
            attachment_ids = json.loads(item.get("attachment_ids") or "[]")
        except (TypeError, json.JSONDecodeError):
            attachment_ids = []
        raw_teacher_turn = str(item.get("teacher_turn_json") or "").strip()
        if raw_teacher_turn:
            try:
                item["teacher_turn"] = turn_from_dict(json.loads(raw_teacher_turn)).to_dict()
            except (TypeError, ValueError, json.JSONDecodeError):
                # A malformed historical turn must remain readable as its
                # legacy plain-text message and must not become UI markup.
                item.pop("teacher_turn", None)
        attachments = []
        for attachment_id in attachment_ids:
            attachment = connection.execute(
                """SELECT * FROM active_teaching_attachments
                   WHERE id=? AND session_id=?""",
                (str(attachment_id), session_id),
            ).fetchone()
            if attachment:
                attachments.append(_public_teaching_attachment(attachment))
        item["attachments"] = attachments
        messages.append(item)
    state = json.loads(row["state"])
    current_point = _current_point_projection(connection, row, state, messages)
    state["practice_resources"] = _practice_resources_for_session(
        connection,
        row,
        state,
    )
    _sync_pending_reviews(connection, session_id, state)
    incomplete_rows = [
        task
        for task in _task_rows(connection, row["plan_id"])
        if str(task["task_type"] or "initial_learning") == "initial_learning"
        and str(task["status"]) != "completed"
    ]
    incomplete_initial = len(incomplete_rows)
    incomplete_initial_points = len(
        {str(task["knowledge_point_id"]) for task in incomplete_rows}
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
        # Keep the full transcript for existing integrations and audit tools;
        # native classroom clients render this bounded current-point view.
        "current_point": current_point,
        "current_point_projection": current_point,
        "current_point_messages": current_point["messages"],
        "current_point_evidence": current_point["evidence"],
        "knowledge_point_progress": {
            "ordinal": current_point["ordinal"],
            "total": current_point["total"],
            "label": current_point["progress_label"],
            "current": current_point["ordinal"],
            "current_ordinal": current_point["ordinal"],
            "total_points": current_point["total"],
        },
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
    attachment_ids: list[str] | None = None,
    teacher_turn: Mapping[str, Any] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO active_teaching_messages
           (id,session_id,role,action,content,citations,attachment_ids,teacher_turn_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            _id("teaching_message"),
            session_id,
            role,
            action,
            content,
            json.dumps(citations, ensure_ascii=False),
            json.dumps(attachment_ids or [], ensure_ascii=False),
            json.dumps(dict(teacher_turn), ensure_ascii=False) if teacher_turn else "",
            _now(),
        ),
    )


def _ensure_teacher_focus_markup(
    content: str,
    state: dict[str, Any],
) -> str:
    """Guarantee one concise, safe focus marker without styling whole answers."""
    value = str(content or "").strip()
    if not value or re.search(r"\[\[重点:[^\]\n]{1,120}\]\]|\*\*[^*\n]{1,120}\*\*", value):
        return value
    memory = state.get("current_knowledge_memory") or {}
    navigation = state.get("navigation_context") or {}
    candidates = [
        str(memory.get("knowledge_point_name") or "").strip(),
        str(navigation.get("to_knowledge_point_name") or "").strip(),
    ]
    for candidate in candidates:
        if candidate and len(candidate) <= 80 and candidate in value:
            return value.replace(candidate, f"[[重点:{candidate}]]", 1)
    quoted = re.search(r"[“\"]([^”\"\n]{2,60})[”\"]", value)
    if quoted:
        phrase = quoted.group(1).strip()
        if phrase:
            start, end = quoted.span(1)
            return f"{value[:start]}[[重点:{phrase}]]{value[end:]}"
    return value


async def _render_teacher_content(
    *,
    draft: str,
    action: str,
    citations: list[dict[str, Any]],
    provider_session_id: str | None,
    state: dict[str, Any],
    knowledge_scope: str = "source_grounded",
) -> tuple[str, dict[str, Any]]:
    """Render one teacher turn while preserving an explicit knowledge boundary."""
    if not provider_session_id:
        return _ensure_teacher_focus_markup(draft, state), {
            "mode": "system_guided",
            "provider": None,
            "fallback_used": False,
            "scope": (
                "state_and_source_grounded_template"
                if knowledge_scope == "source_grounded"
                else knowledge_scope
            ),
            "knowledge_scope": knowledge_scope,
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
        "available_external_tools": state.get("available_external_tools") or [],
        "teacher_persona": state.get("teacher_persona") or "",
        "persona_expression": {
            "principle": (
                "Keep the teacher recognizably in character throughout class. Preserve her "
                "voice, reactions, humor, relationship tone, favorite metaphors, and playful "
                "style instead of flattening her into a generic professional assistant."
            ),
            "boundary": (
                "Do not apply a blanket keyword ban to the persona. Intervene only when a "
                "specific response would create coercion, non-consensual conduct, real harm, "
                "or would prevent the learner from completing the lesson; preserve the intended "
                "emotion and character flavor when safely rephrasing."
            ),
        },
        "teaching_contract": {
            "priority": (
                "The teacher must genuinely help the learner complete and understand the current "
                "task; persona expression and teaching should reinforce each other."
            ),
            "during_class": (
                "Keep continuity with the learner's latest message and the current objective. "
                "Explain, react, joke, ask, or briefly digress as naturally as this persona would, "
                "then keep the learning thread available rather than resetting it."
            ),
            "after_class": (
                "Once the lesson is completed or paused, allow broader normal conversation while "
                "retaining the course context for an easy return."
            ),
            "non_negotiable_outcomes": [
                "Never claim mastery, completion, elapsed time, or progress without learner evidence.",
                "Never fabricate uploaded-material evidence or citations.",
                "Do not silently change the backend-selected action, route, task, or next state.",
            ],
        },
        "adaptive_teaching_guidance": [
            "Respond to what the learner actually said before advancing the prepared draft.",
            "Choose explanation depth, number of paragraphs, examples, and use of code from the learner's need; there is no fixed character or paragraph limit.",
            "For a beginner, build intuition before terminology and code, but do not force the same analogy pattern on every turn.",
            "Usually advance one learnable step and pause for feedback; when a complete explanation is needed, finish it instead of truncating it mechanically.",
            "The learner may interrupt, ask for another example, or briefly change topic. Answer naturally and preserve the place to resume.",
            "End with a clear and natural next move when one is useful; do not force every response into the same question template.",
            "If the learner asks for a practice site or tool, use an available_external_tools item when one exists and include its exact URL; do not invent a URL or dump links into an unrelated explanation.",
        ],
        "learner_context": state.get("learner_context") or {},
        "learner_mastery_model": state.get("learner_mastery_model") or {},
        "memory_contract": state.get("memory_policy") or {
            "cross_task": "compact_profile_only",
            "current_point": "bounded_detailed_summary",
            "full_transcript": "session_scoped_not_cross_task",
        },
        "global_learner_profile": state.get("global_learner_profile") or {},
        "current_knowledge_point_memory": state.get("current_knowledge_memory") or {},
        "task_memory": {
            key: value
            for key, value in (state.get("teacher_memory") or {}).items()
            if key
            in {
                "unresolved_points",
                "explanations_that_helped",
                "last_interaction",
                "knowledge_progress",
                "unresolved_confusions",
            }
        },
        "navigation_context": state.get("navigation_context") or {},
        "latest_learner_message": str(
            (state.get("teacher_memory") or {}).get("last_learner_message") or ""
        ),
        "allowed_evidence": evidence,
        "knowledge_scope": knowledge_scope,
        "rules": [
            "Write a coherent Chinese teacher response that combines the grounded draft, the learner's latest message, the saved persona, and the adaptive teaching guidance.",
            "Mark only 1 to 3 short genuinely important phrases with the exact syntax [[重点:短语]]. Never mark an entire paragraph, URL, citation, code block, or ordinary praise.",
            (
                "Do not add factual claims beyond the draft and allowed evidence."
                if knowledge_scope == "source_grounded"
                else (
                    "You may use stable general model knowledge and pedagogical expertise. "
                    "Clearly distinguish it from uploaded-material evidence, never invent a "
                    "citation, and say when a current or time-sensitive claim needs real web verification."
                )
            ),
            "Use current_knowledge_point_memory for detailed continuity inside this point and avoid repeating failed explanations.",
            "Use global_learner_profile only for compact cross-task continuity: level, preferences, learned topics, current state, and blockers. Never imply that full conversations from other tasks were retained.",
            "If navigation_context shows a jump, explicitly acknowledge where the learner came from and whether the previous point was unfinished or the destination was scheduled for later; do not shame or block curiosity.",
            "Use learner_mastery_model and knowledge_progress to connect the current lesson to already passed phases, prior mistakes, and unresolved points; never claim the learner forgot completed work.",
            "Keep uncertainty statements exactly uncertain; never fabricate missing evidence.",
            "Return JSON only with one string field named content.",
        ],
    }
    try:
        messages = [
                {
                    "role": "system",
                    "content": (
                        (
                            "You are the teaching-response layer. For this turn the application "
                            "allows stable general model knowledge, but uploaded-source citations, "
                            "teaching state, grading, timing and routing remain application-owned."
                        )
                        if knowledge_scope != "source_grounded"
                        else (
                            "You are only the teaching-expression layer. The application owns "
                            "facts, citations, teaching state, grading, timing and routing."
                        )
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        expression_provider = provider_for_session(provider_session)
        if hasattr(expression_provider, "text"):
            raw = await expression_provider.text(messages)
            payload, fallback_used = parse_conversational_payload(
                raw,
                text_field="content",
            )
        else:
            # Compatibility for narrowly scoped provider doubles and adapters
            # that expose only the former structured interface.
            payload = await expression_provider.json(messages)
            fallback_used = False
    except ProviderError as exc:
        _error(exc.code, exc.message, exc.status, **exc.details)
    rendered = str(payload.get("content") or "").strip() if isinstance(payload, dict) else ""
    if not rendered:
        _error(
            "PROVIDER_TEACHING_RESPONSE_INVALID",
            "Provider 未返回有效的教学表达内容。",
            502,
        )
    if (
        knowledge_scope == "model_general_knowledge"
        and "模型通识补充" not in rendered[:80]
    ):
        rendered = f"模型通识补充（非资料引用）：\n\n{rendered}"
    rendered = _ensure_teacher_focus_markup(rendered, state)
    return rendered, generation_meta(provider_session) | {
        "scope": (
            "expression_only"
            if knowledge_scope == "source_grounded"
            else knowledge_scope
        ),
        "knowledge_scope": knowledge_scope,
        "fixed_action": action,
        "response_format_fallback": fallback_used,
    }


def _v1_preferences(workspace_id: str, receipt: Mapping[str, Any]) -> list[dict[str, str]]:
    """Merge only explicit receipt/ORM preferences, keeping receipt order."""
    values = [
        *explicit_preferences_from_receipt(receipt),
        *explicit_preferences_from_orm(workspace_id),
    ]
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        key = (str(item.get("key") or "interest"), str(item.get("value") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        output.append({"key": key[0], "value": key[1], "source": "explicit"})
    profile = build_learner_preference_profile(
        workspace_id=workspace_id,
        explicit_preferences=output,
    )
    return [item.model_dump(mode="json") for item in profile.preferences]


def _v1_concept(point: sqlite3.Row, task: sqlite3.Row | None = None) -> dict[str, Any]:
    acceptance = (
        task["acceptance_condition"] if task is not None else point["acceptance_method"]
    ) or point["acceptance_method"] or ""
    return {
        "id": str(point["id"]),
        "name": str(point["name"] or ""),
        "explanation": str(point["explanation"] or ""),
        "acceptance_method": str(acceptance),
    }


async def _v1_prepare_turn(
    *,
    workspace_id: str,
    point: sqlite3.Row,
    task: sqlite3.Row,
    receipt: Mapping[str, Any],
    citations: list[dict[str, Any]],
    state: Mapping[str, Any],
    phase: str = "waiting_for_answer",
    remediation_attempt: int = 0,
    correction: str = "",
    provider_session_id: str | None = None,
) -> tuple[TeacherTurnV1, dict[str, Any]]:
    turn = build_teacher_turn(
        concept=_v1_concept(point, task),
        evidence=citations,
        explicit_preferences=_v1_preferences(workspace_id, receipt),
        phase=phase,
        remediation_attempt=remediation_attempt,
        correction=correction,
    )
    generation: dict[str, Any] = {
        "mode": "system_guided",
        "provider": None,
        "fallback_used": False,
        "scope": "teacher_turn_v1_expression",
        "knowledge_scope": "source_grounded",
    }
    if provider_session_id:
        try:
            provider_session = require_provider_session(provider_session_id)
            expression_provider = provider_for_session(provider_session)
            prompt = {
                "prompt_version": PROMPT_POLICY.version,
                "phase": phase,
                "concept": _v1_concept(point, task),
                "evidence": [
                    {
                        "chunk_id": item.get("chunk_id"),
                        "quote": str(item.get("quote") or "")[:1_200],
                    }
                    for item in citations
                ],
                "analogy": next(
                    (
                        {
                            "domain": item.get("domain"),
                            "mapping": item.get("mapping"),
                            "limitation": item.get("limitation"),
                        }
                        for item in turn.blocks
                        if item.get("type") == "analogy"
                    ),
                    {},
                ),
                "check": turn.check,
                "rules": list(PROMPT_POLICY.teacher_expression_rules),
            }
            messages = [
                {
                    "role": "system",
                    "content": PROMPT_POLICY.teacher_expression_system_message,
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
            if hasattr(expression_provider, "structured"):
                expression = await expression_provider.structured(messages, TeacherExpressionV1)
            else:
                expression = TeacherExpressionV1.model_validate(
                    await expression_provider.json(messages)
                )
        except ProviderError as exc:
            _error(exc.code, exc.message, exc.status, **exc.details)
        except Exception:
            _error(
                "PROVIDER_TEACHER_EXPRESSION_INVALID",
                "Provider 未返回可验证的主动教学结构。",
                502,
            )
        turn = merge_provider_expression(turn, expression)
        generation = generation_meta(provider_session) | {
            "scope": "teacher_turn_v1_expression",
            "knowledge_scope": "source_grounded",
            "prompt_version": PROMPT_POLICY.version,
        }
    return turn, generation


def _v1_turn_with_state(
    turn: TeacherTurnV1,
    state: Mapping[str, Any],
    plain_text: str | None = None,
) -> TeacherTurnV1:
    return TeacherTurnV1(
        state=dict(state),
        concept=dict(turn.concept),
        evidence=[dict(item) for item in turn.evidence],
        blocks=[dict(item) for item in turn.blocks],
        check=dict(turn.check),
        expected_learner_action=turn.expected_learner_action,
        plain_text=plain_text or turn.plain_text,
        schema_version=turn.schema_version,
    )


def _v1_record_turn(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    workspace_id: str,
    state: dict[str, Any],
    turn: TeacherTurnV1,
    action: str,
    citations: list[dict[str, Any]],
    generation: dict[str, Any],
) -> None:
    state["teacher_turn_v1"] = turn.to_dict()
    state["teacher_turn_v1_phase"] = str(turn.state.get("phase") or "")
    state["teacher_turn_number"] = int(state.get("teacher_turn_number") or 0) + 1
    waiting = bool(turn.state.get("waiting_for_answer"))
    state["current_teaching_step"] = "v1_waiting_for_answer" if waiting else str(turn.state.get("phase") or action)
    state["next_action"] = "evaluate_v1_answer" if waiting else str(action)
    state["autonomy"] = {
        "mode": "teacher_led_until_learner_input" if waiting else "teacher_led_state_transition",
        "awaiting_learner": waiting,
        "reason": "teacher_turn_v1_check" if waiting else str(turn.state.get("status") or action),
    }
    state["teacher_generation"] = generation
    _record_message(
        connection,
        session_id,
        "teacher",
        action,
        turn.plain_text,
        citations,
        teacher_turn=turn.to_dict(),
    )
    try:
        persist_teacher_turn(
            workspace_id=workspace_id,
            turn=turn,
            turn_key=f"active:{session_id}:{int(state['teacher_turn_number'])}",
            turn_number=int(state["teacher_turn_number"]),
            event_type=action,
        )
    except Exception:
        state["teacher_turn_orm_persisted"] = False
    else:
        state["teacher_turn_orm_persisted"] = True


async def _v1_answer_evidence(
    message: str,
    body: Mapping[str, Any],
    point: sqlite3.Row,
    citations: list[dict[str, Any]],
    provider_session_id: str | None,
    *,
    external_completion_evidence: bool = False,
) -> tuple[bool, float, list[str], dict[str, Any]]:
    """Use observable answer content; a UI flag alone is not mastery evidence."""
    lower = str(message or "").casefold()
    if any(marker in lower for marker in CONFUSION_MARKERS):
        return False, 0.0, ["explicit_confusion"], {
            "mode": "application_precheck",
            "reason": "learner_explicitly_reported_confusion",
        }
    score, passed, checks, evaluation = await _evaluate_understanding(
        message=message,
        point=point,
        citations=citations,
        provider_session_id=provider_session_id,
        external_completion_evidence=external_completion_evidence,
    )
    if bool(body.get("understood")):
        evaluation = {**evaluation, "learner_claimed_understanding": True}
    return passed, score, checks, evaluation


def _v1_successful_task(
    connection: sqlite3.Connection,
    *,
    task: sqlite3.Row,
    point: sqlite3.Row,
    state: dict[str, Any],
    score: float,
    evaluation: Mapping[str, Any],
) -> None:
    task_id = str(task["id"])
    if str(task["status"]) != "completed":
        _complete_execution_task(connection, task)
        state["completed_task_minutes"] = int(state.get("completed_task_minutes") or 0) + int(task["estimated_task_minutes"] or 0)
        state["completed_tasks"] = [*list(state.get("completed_tasks") or []), task_id]
    independent = str(task["learning_phase"] or "integrated_initial") in {
        "integrated_initial",
        "independent_application",
    }
    if independent and _is_canonical_task(task):
        event_key = f"mastery:{task_id}"
        connection.execute(
            """INSERT OR IGNORE INTO orm_plan_mastery_events
               (id,workspace_id,plan_id,task_id,map_revision_id,knowledge_point_id,
                evidence_hash,event_type,score,passed,payload,idempotency_key,occurred_at)
               VALUES (?,?,?,?,?,?,?,'mastery_confirmed',?,1,?,?,?)""",
            (
                "plan_mastery_" + hashlib.sha256(event_key.encode("utf-8")).hexdigest()[:32],
                task["workspace_id"],
                task["plan_id"],
                task_id,
                task["map_revision_id"],
                point["id"],
                point["evidence_hash"],
                max(0.0, min(1.0, float(score))),
                json.dumps(
                    {
                        "schema": "mentorflow.plan-mastery-event.v1",
                        "evaluation": dict(evaluation),
                        "task_status": "completed",
                    },
                    ensure_ascii=False,
                ),
                event_key,
                _now(),
            ),
        )
    elif str(task["task_type"] or "") == "review" and _is_canonical_task(task):
        # Canonical projected reviews are durable execution rows.  Completing
        # one records a distinct event so review evidence cannot be mistaken
        # for initial-learning mastery or counted twice on retry.
        event_key = f"review:{task_id}:{int(task['review_cycle'] or 0)}"
        connection.execute(
            """INSERT OR IGNORE INTO orm_plan_mastery_events
               (id,workspace_id,plan_id,task_id,map_revision_id,knowledge_point_id,
                evidence_hash,event_type,score,passed,payload,idempotency_key,occurred_at)
               VALUES (?,?,?,?,?,?,?,'review_passed',?,1,?,?,?)""",
            (
                "plan_review_"
                + hashlib.sha256(event_key.encode("utf-8")).hexdigest()[:32],
                task["workspace_id"],
                task["plan_id"],
                task_id,
                task["map_revision_id"],
                point["id"],
                point["evidence_hash"],
                max(0.0, min(1.0, float(score))),
                json.dumps(
                    {
                        "schema": "mentorflow.plan-review-event.v1",
                        "review_cycle": int(task["review_cycle"] or 0),
                        "evaluation": dict(evaluation),
                        "task_status": "completed",
                    },
                    ensure_ascii=False,
                ),
                event_key,
                _now(),
            ),
        )
    elif independent:
        connection.execute(
            "UPDATE active_knowledge_points SET mastery=MAX(mastery,?) WHERE id=?",
            (max(0.0, min(1.0, float(score))), point["id"]),
        )
    state["mastery_evidence"] = [
        *list(state.get("mastery_evidence") or []),
        {
            "knowledge_point_id": str(point["id"]),
            "task_id": task_id,
            "score": round(float(score), 2),
            "passed": True,
            "evidence_type": "teacher_turn_v1_observable_answer",
            "mastery_changed": independent,
            "evaluation": dict(evaluation),
        },
    ][-20:]


def _v1_self_attested_completion(
    connection: sqlite3.Connection,
    *,
    session: sqlite3.Row,
    tasks: list[sqlite3.Row],
    task: sqlite3.Row,
    point: Mapping[str, Any] | sqlite3.Row,
    state: dict[str, Any],
    message: str,
    citations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Complete one point from an explicit learner mastery assertion.

    The learner owns this fast path: it advances immediately and counts as a
    completed/mastered point.  The event origin remains auditable internally,
    but it does not create a second user-facing completion class.
    """

    point_id = str(point["id"])
    def task_type_of(row: Mapping[str, Any] | sqlite3.Row) -> str:
        keys = row.keys() if hasattr(row, "keys") else ()
        return str(row["task_type"] or "initial_learning") if "task_type" in keys else "initial_learning"

    if task_type_of(task) == "review":
        completion_tasks = [
            task
        ] if str(task["status"]) != "completed" else []
    else:
        completion_tasks = [
            candidate
            for candidate in tasks
            if str(candidate["knowledge_point_id"]) == point_id
            and task_type_of(candidate) == "initial_learning"
            and str(candidate["status"]) != "completed"
        ]
    completed_ids: list[str] = []
    completed_minutes = 0
    for candidate in completion_tasks:
        _complete_execution_task(connection, candidate)
        candidate_id = str(candidate["id"])
        completed_ids.append(candidate_id)
        completed_minutes += int(candidate["estimated_task_minutes"] or 0)
        if candidate_id not in state.setdefault("completed_tasks", []):
            state["completed_tasks"].append(candidate_id)

    state["completed_task_minutes"] = int(
        state.get("completed_task_minutes") or 0
    ) + completed_minutes
    state["today_remaining_task_minutes"] = max(
        0,
        int(state.get("today_remaining_task_minutes") or 0) - completed_minutes,
    )
    state.update(
        {
            "self_attested_completion": True,
            "completion_origin": "self_attested",
            "provider_verified": False,
            "mastery_changed": True,
            "completed_as_mastered": True,
        }
    )
    evidence_id = _id("mastery")
    evidence_payload = {
        "schema": "mentorflow.self-attested-completion.v1",
        "completion_origin": "self_attested",
        "provider_verified": False,
        "completed": True,
        "task_ids": completed_ids,
        "learner_message": message,
    }
    # Canonical v1 plans intentionally do not materialize legacy
    # ``active_knowledge_points`` rows, so their foreign-key-safe audit record
    # is the ORM mastery event below.  Legacy plans retain the SQLite evidence
    # row for compatibility with existing classroom reports.
    if not _is_canonical_task(task):
        connection.execute(
            """INSERT INTO active_mastery_evidence
               (id,session_id,knowledge_point_id,evidence_type,score,passed,evidence,citations,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                evidence_id,
                session["id"],
                point_id,
                "self_attested_completion",
                1.0,
                1,
                json.dumps(evidence_payload, ensure_ascii=False),
                json.dumps(citations, ensure_ascii=False),
                _now(),
            ),
        )
    state["mastery_evidence"] = [
        *list(state.get("mastery_evidence") or []),
        {
            "knowledge_point_id": point_id,
            "task_id": str(task["id"]),
            "task_ids": completed_ids,
            "score": 1.0,
            "passed": True,
            "evidence_type": "self_attested_completion",
            "completion_origin": "self_attested",
            "provider_verified": False,
            "mastery_changed": True,
            "evidence": message,
        },
    ][-20:]
    if _is_canonical_task(task):
        event_key = f"self-attested:{session['id']}:{point_id}"
        connection.execute(
            """INSERT OR IGNORE INTO orm_plan_mastery_events
               (id,workspace_id,plan_id,task_id,map_revision_id,knowledge_point_id,
                evidence_hash,event_type,score,passed,payload,idempotency_key,occurred_at)
               VALUES (?,?,?,?,?,?,?,'self_attested_completion',1,1,?,?,?)""",
            (
                "plan_self_attested_"
                + hashlib.sha256(event_key.encode("utf-8")).hexdigest()[:32],
                task["workspace_id"],
                task["plan_id"],
                task["id"],
                task["map_revision_id"],
                point_id,
                point["evidence_hash"],
                json.dumps(
                    {
                        "schema": "mentorflow.self-attested-completion.v1",
                        "completion_origin": "self_attested",
                        "provider_verified": False,
                        "completed": True,
                        "task_ids": completed_ids,
                    },
                    ensure_ascii=False,
                ),
                event_key,
                _now(),
            ),
        )
    return {
        "evidence_id": evidence_id,
        "task_ids": completed_ids,
        "completed_minutes": completed_minutes,
    }


async def _v1_process_answer(
    *,
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    state: dict[str, Any],
    task: sqlite3.Row,
    point: sqlite3.Row,
    receipt: Mapping[str, Any],
    message: str,
    body: Mapping[str, Any],
    citations: list[dict[str, Any]],
    provider_session_id: str | None,
    elapsed: float = 0.0,
) -> dict[str, Any]:
    """Process a v1 answer and emit exactly one next teacher turn."""
    if bool(body.get("force_complete")):
        tasks = _task_rows(connection, session["plan_id"])
        completion = _v1_self_attested_completion(
            connection,
            session=session,
            tasks=tasks,
            task=task,
            point=point,
            state=state,
            message=message,
            citations=citations,
        )
        _capture_knowledge_progress(connection, session["workspace_id"])
        next_state = self_attested_completion_transition(
            (state.get("teacher_turn_v1") or {}).get("state") or state
        )
        state.update(next_state)
        state["self_attested_completion_evidence_id"] = completion["evidence_id"]
        current_index = next(
            (index for index, candidate in enumerate(tasks) if str(candidate["id"]) == str(task["id"])),
            int(state.get("route_index") or 0),
        )
        next_index = next(
            (
                index
                for index, candidate in enumerate(tasks[current_index + 1 :], current_index + 1)
                if str(candidate["knowledge_point_id"]) != str(point["id"])
                and str(candidate["status"]) != "completed"
            ),
            len(tasks),
        )
        state["route_index"] = next_index
        if next_index < len(tasks):
            next_task = tasks[next_index]
            next_point = _point_for_task(connection, next_task)
            state["active_task_id"] = str(next_task["id"])
            state["current_knowledge_point_id"] = str(next_point["id"])
            state["estimated_task_minutes"] = int(next_task["estimated_task_minutes"] or 0)
            next_citations = _merge_citations(
                _search(
                    connection,
                    session["workspace_id"],
                    f"{next_point['name']} {next_point['explanation']} {next_point['acceptance_method']}",
                    plan_id=session["plan_id"],
                ),
                json.loads(next_point["source_evidence"]),
            )
            next_turn, next_generation = await _v1_prepare_turn(
                workspace_id=session["workspace_id"],
                point=next_point,
                task=next_task,
                receipt=receipt,
                citations=next_citations,
                state=state,
                phase="waiting_for_answer",
                provider_session_id=provider_session_id,
            )
            next_turn = _v1_turn_with_state(
                next_turn,
                dict(next_turn.state),
                f"已完成“{point['name']}”的全部首学任务。\n\n"
                f"下面进入“{next_point['name']}”。\n\n{next_turn.plain_text}",
            )
            _v1_record_turn(
                connection,
                session_id=session["id"],
                workspace_id=session["workspace_id"],
                state=state,
                turn=next_turn,
                action="teacher_turn_v1_self_attested_next",
                citations=next_citations,
                generation=next_generation,
            )
        else:
            state["route_index"] = len(tasks)
            state["current_teaching_step"] = "completed"
            state["next_action"] = "review"
            turn, generation = await _v1_prepare_turn(
                workspace_id=session["workspace_id"], point=point, task=task,
                receipt=receipt, citations=citations, state=state, phase="blocked",
            )
            turn = _v1_turn_with_state(
                turn,
                self_attested_completion_transition(state),
                f"已完成“{point['name']}”的全部首学任务，接下来进入复习。",
            )
            _v1_record_turn(
                connection, session_id=session["id"], workspace_id=session["workspace_id"],
                state=state, turn=turn, action="teacher_turn_v1_self_attested_completed",
                citations=citations, generation=generation,
            )
        connection.execute(
            """UPDATE active_teaching_sessions
               SET state=?,updated_at=?,actual_elapsed_minutes=actual_elapsed_minutes+?
               WHERE id=?""",
            (json.dumps(state, ensure_ascii=False), _now(), elapsed, session["id"]),
        )
        connection.commit()
        return _public_session(connection, session["id"])

    correct, score, answer_checks, evaluation = await _v1_answer_evidence(
        message,
        body,
        point,
        citations,
        provider_session_id,
        external_completion_evidence=bool(body.get("verify_completion")),
    )
    current_turn_state = (state.get("teacher_turn_v1") or {}).get("state") or {}
    next_turn_state = transition_after_answer(current_turn_state, correct=correct, score=score)
    if not correct:
        attempt = int(next_turn_state.get("remediation_attempt") or 0)
        phase = str(next_turn_state.get("phase") or "blocked")
        correction = str(
            PROMPT_POLICY.teacher_remediation_messages.get(
                phase,
                PROMPT_POLICY.teacher_remediation_messages["blocked"],
            )
        )
        turn, generation = await _v1_prepare_turn(
            workspace_id=session["workspace_id"], point=point, task=task,
            receipt=receipt, citations=citations, state=state, phase=phase,
            remediation_attempt=attempt, correction=correction,
            provider_session_id=provider_session_id,
        )
        if phase == "blocked":
            next_turn_state["waiting_for_answer"] = False
            turn = _v1_turn_with_state(
                turn,
                next_turn_state,
                f"我已经完成 {TEACHING_POLICY.maximum_remediation_attempts} 轮针对性补救，"
                f"但仍没有足够证据确认“{point['name']}”。主路线已记录为 blocked，"
                "掌握度不变；你可以补充资料或稍后重新安排。",
            )
        else:
            turn = _v1_turn_with_state(turn, next_turn_state)
        state["unresolved_confusions"] = [
            *list(state.get("unresolved_confusions") or []),
            {
                "knowledge_point_id": str(point["id"]),
                "attempt": attempt,
                "message": message[:600],
                "phase": phase,
                "checks": answer_checks,
                "evaluation": evaluation,
            },
        ][-20:]
        state["remediation_attempt"] = attempt
        state["blocked"] = phase == "blocked"
        _v1_record_turn(
            connection, session_id=session["id"], workspace_id=session["workspace_id"],
            state=state, turn=turn, action=phase, citations=citations, generation=generation,
        )
        connection.execute(
            """UPDATE active_teaching_sessions
               SET state=?,updated_at=?,actual_elapsed_minutes=actual_elapsed_minutes+?
               WHERE id=?""",
            (json.dumps(state, ensure_ascii=False), _now(), elapsed, session["id"]),
        )
        connection.commit()
        return _public_session(connection, session["id"])

    _v1_successful_task(
        connection,
        task=task,
        point=point,
        state=state,
        score=score,
        evaluation=evaluation,
    )
    if bool(body.get("verify_completion")):
        # External completion is still teacher/provider-evaluated, but the
        # intent is to finish the whole current knowledge point rather than a
        # single phase row.  The selected independent task above remains the
        # mastery anchor; these additional rows only complete execution work.
        current_tasks = _task_rows(connection, session["plan_id"])
        additional_minutes = 0
        for candidate in current_tasks:
            if str(candidate["knowledge_point_id"]) != str(point["id"]):
                continue
            if str(candidate["task_type"] or "initial_learning") != "initial_learning":
                continue
            fresh = connection.execute(
                f"SELECT * FROM {'orm_plan_execution_tasks' if _is_canonical_task(candidate) else 'active_plan_tasks'} WHERE id=?",
                (candidate["id"],),
            ).fetchone()
            if fresh is None or str(fresh["status"]) == "completed":
                continue
            _complete_execution_task(connection, fresh)
            candidate_id = str(fresh["id"])
            if candidate_id not in state.setdefault("completed_tasks", []):
                state["completed_tasks"].append(candidate_id)
                state["completed_task_minutes"] = int(
                    state.get("completed_task_minutes") or 0
                ) + int(fresh["estimated_task_minutes"] or 0)
                additional_minutes += int(fresh["estimated_task_minutes"] or 0)
        if additional_minutes:
            state["today_remaining_task_minutes"] = max(
                0,
                int(state.get("today_remaining_task_minutes") or 0)
                - additional_minutes,
            )
        state.update(
            {
                "completion_origin": "provider_verified",
                "provider_verified": True,
                "self_attested_completion": False,
            }
        )
    # Keep the durable cross-plan progress projection in sync with the
    # canonical task statuses.  This is intentionally after the state
    # transition so a restarted session and a newly generated plan see the
    # same completed phases.
    _capture_knowledge_progress(connection, session["workspace_id"])
    tasks = _task_rows(connection, session["plan_id"])
    next_index = next(
        (index for index, candidate in enumerate(tasks) if str(candidate["status"]) != "completed"),
        len(tasks),
    )
    state["route_index"] = next_index
    state["remediation_attempt"] = 0
    state["blocked"] = False
    if bool(body.get("verify_completion")):
        next_state.update(
            {
                "completion_origin": "provider_verified",
                "provider_verified": True,
                "self_attested_completion": False,
            }
        )
    if next_index >= len(tasks):
        next_state.update({"phase": "completed", "status": "completed", "waiting_for_answer": False})
        turn, generation = await _v1_prepare_turn(
            workspace_id=session["workspace_id"], point=point, task=task,
            receipt=receipt, citations=citations, state=state, phase="blocked",
        )
        turn = _v1_turn_with_state(
            turn, next_state,
            f"“{point['name']}”已完成这一步的可观察检查；路线中没有待完成任务了。",
        )
        action = "teacher_turn_v1_completed"
    else:
        next_task = tasks[next_index]
        next_point = _point_for_task(connection, next_task)
        state["active_task_id"] = next_task["id"]
        state["current_knowledge_point_id"] = next_point["id"]
        state["estimated_task_minutes"] = next_task["estimated_task_minutes"]
        next_citations = _merge_citations(
            _search(
                connection,
                session["workspace_id"],
                f"{next_point['name']} {next_point['explanation']} {next_point['acceptance_method']}",
                plan_id=session["plan_id"],
            ),
            json.loads(next_point["source_evidence"]),
        )
        turn, generation = await _v1_prepare_turn(
            workspace_id=session["workspace_id"], point=next_point, task=next_task,
            receipt=receipt, citations=next_citations, state=state,
            phase="waiting_for_answer", provider_session_id=provider_session_id,
        )
        if bool(body.get("verify_completion")):
            turn = _v1_turn_with_state(
                turn,
                {
                    **dict(turn.state),
                    "completion_origin": "provider_verified",
                    "provider_verified": True,
                },
            )
        citations = next_citations
        action = "teacher_turn_v1_next_point"
    _v1_record_turn(
        connection, session_id=session["id"], workspace_id=session["workspace_id"],
        state=state, turn=turn, action=action, citations=citations, generation=generation,
    )
    connection.execute(
        """UPDATE active_teaching_sessions
           SET state=?,updated_at=?,actual_elapsed_minutes=actual_elapsed_minutes+?
           WHERE id=?""",
        (json.dumps(state, ensure_ascii=False), _now(), elapsed, session["id"]),
    )
    connection.commit()
    return _public_session(connection, session["id"])


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
    rows = [
        row
        for row in _task_rows(connection, plan_id)
        if str(row["day_date"]) == str(day_date)
    ]
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
        "tasks": [
            {
                "id": row["id"],
                "knowledge_point_id": row["knowledge_point_id"],
                "title": row["title"],
                "estimated_task_minutes": row["estimated_task_minutes"],
                "status": row["status"],
            }
            for row in rows
        ],
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


GLOBAL_LEARNER_KEY = "local_default"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _unique_text(values: list[Any], limit: int, width: int = 160) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()[:width]
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result[-limit:]


def _read_global_learner_profile(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT compact_profile FROM active_learner_profiles WHERE learner_key=?",
        (GLOBAL_LEARNER_KEY,),
    ).fetchone()
    return _json_object(row["compact_profile"]) if row else {}


def _read_knowledge_memory(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    knowledge_point_id: str,
) -> dict[str, Any]:
    if not knowledge_point_id:
        return {}
    row = connection.execute(
        """SELECT detail_memory FROM active_knowledge_memories
           WHERE workspace_id=? AND knowledge_point_id=?""",
        (workspace_id, knowledge_point_id),
    ).fetchone()
    return _json_object(row["detail_memory"]) if row else {}


def _write_knowledge_memory(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    point: sqlite3.Row,
    state: dict[str, Any],
    role: str = "",
    message: str = "",
    teacher_action: str = "",
    confused: bool = False,
) -> dict[str, Any]:
    """Persist detail only inside one knowledge point.

    Full transcripts remain in the session message table.  This memory stores
    bounded teaching summaries that are useful when the learner leaves and
    later returns to the same point.
    """
    point_id = str(point["id"])
    memory = _read_knowledge_memory(
        connection,
        workspace_id=workspace_id,
        knowledge_point_id=point_id,
    )
    interactions = [
        item
        for item in list(memory.get("recent_interactions") or [])
        if isinstance(item, dict)
    ]
    if role and message:
        interactions.append(
            {
                "role": role[:24],
                "summary": str(message).strip()[:700],
                "action": str(teacher_action or "")[:80],
                "at": _now(),
            }
        )
    blockers = list(memory.get("blockers") or [])
    if confused and message:
        blockers.append(str(message).strip()[:260])
    unresolved = [
        str(item.get("message") or "")[:260]
        for item in state.get("unresolved_confusions") or []
        if isinstance(item, dict)
        and str(item.get("knowledge_point_id") or "") == point_id
    ]
    memory.update(
        {
            "knowledge_point_id": point_id,
            "knowledge_point_name": str(point["name"]),
            "mastery": round(float(point["mastery"] or 0), 4),
            "last_teaching_step": str(state.get("current_teaching_step") or ""),
            "last_task_id": str(state.get("active_task_id") or ""),
            "last_teacher_action": str(teacher_action or memory.get("last_teacher_action") or "")[:80],
            "blockers": _unique_text([*blockers, *unresolved], 8, 260),
            "recent_interactions": interactions[-10:],
            "updated_at": _now(),
        }
    )
    connection.execute(
        """INSERT INTO active_knowledge_memories
           (workspace_id,knowledge_point_id,detail_memory,updated_at)
           VALUES(?,?,?,?)
           ON CONFLICT(workspace_id,knowledge_point_id) DO UPDATE SET
             detail_memory=excluded.detail_memory,
             updated_at=excluded.updated_at""",
        (
            workspace_id,
            point_id,
            json.dumps(memory, ensure_ascii=False),
            memory["updated_at"],
        ),
    )
    return memory


def _sync_two_tier_memory(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    state: dict[str, Any],
    navigation_event: str = "",
) -> None:
    """Inject a bounded cross-task profile plus one detailed point memory."""
    existing = _read_global_learner_profile(connection)
    receipt_row = connection.execute(
        "SELECT payload FROM active_receipts WHERE workspace_id=?",
        (workspace_id,),
    ).fetchone()
    receipt = _json_object(receipt_row["payload"]) if receipt_row else {}
    point_id = str(state.get("current_knowledge_point_id") or "")
    point = connection.execute(
        "SELECT id,name,mastery FROM active_knowledge_points WHERE id=?",
        (point_id,),
    ).fetchone() if point_id else None
    learned = connection.execute(
        """SELECT name,mastery,workspace_id FROM active_knowledge_points
           WHERE mastery>=0.8 ORDER BY created_at DESC,id DESC LIMIT 40"""
    ).fetchall()
    memory_rows = connection.execute(
        """SELECT detail_memory FROM active_knowledge_memories
           ORDER BY updated_at DESC LIMIT 40"""
    ).fetchall()
    blockers: list[str] = []
    for row in memory_rows:
        memory = _json_object(row["detail_memory"])
        if memory.get("blockers"):
            blockers.append(
                f"{memory.get('knowledge_point_name') or '知识点'}："
                f"{str(list(memory.get('blockers') or [])[-1])[:180]}"
            )
    prior_preferences = dict(existing.get("preferences") or {})
    interest_values = [
        *list(prior_preferences.get("interests") or []),
        *re.split(r"[,，、;\n]+", str(receipt.get("learner_interests") or "")),
    ]
    profile = {
        "schema": "compact-cross-task-v1",
        "preferences": {
            "learning_style": str(
                receipt.get("preferred_learning_style")
                or prior_preferences.get("learning_style")
                or ""
            )[:260],
            "interests": _unique_text(interest_values, 12, 80),
        },
        "level": {
            "foundation": str(
                receipt.get("current_foundation")
                or receipt.get("current_level")
                or (existing.get("level") or {}).get("foundation")
                or ""
            )[:80],
            "mastered_point_count": len(learned),
        },
        "learned_points": [
            {
                "name": str(row["name"])[:160],
                "mastery": round(float(row["mastery"] or 0), 3),
                "workspace_id": str(row["workspace_id"])[:160],
            }
            for row in learned
        ],
        "active_blockers": _unique_text(blockers, 12, 260),
        "current_state": {
            "workspace_id": workspace_id,
            "knowledge_point_id": point_id,
            "knowledge_point_name": str(point["name"]) if point else "",
            "teaching_step": str(state.get("current_teaching_step") or ""),
            "navigation_event": navigation_event
            or str((state.get("navigation_context") or {}).get("kind") or ""),
            "updated_at": _now(),
        },
    }
    connection.execute(
        """INSERT INTO active_learner_profiles(learner_key,compact_profile,updated_at)
           VALUES(?,?,?)
           ON CONFLICT(learner_key) DO UPDATE SET
             compact_profile=excluded.compact_profile,
             updated_at=excluded.updated_at""",
        (
            GLOBAL_LEARNER_KEY,
            json.dumps(profile, ensure_ascii=False),
            profile["current_state"]["updated_at"],
        ),
    )
    state["global_learner_profile"] = profile
    state["current_knowledge_memory"] = (
        _write_knowledge_memory(
            connection,
            workspace_id=workspace_id,
            point=point,
            state=state,
        )
        if point
        else {}
    )
    state["memory_policy"] = {
        "cross_task": "compact_profile_only",
        "current_point": "bounded_detailed_summary",
        "full_transcript": "session_scoped_not_cross_task",
    }


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
        "substantive_answer": (
            len(message.strip())
            >= TEACHING_POLICY.minimum_substantive_answer_characters
        ),
        "explains_relationship_or_steps": bool(
            re.search(r"因为|所以|关系|步骤|首先|然后|意味着|because|therefore|first|then", message, re.I)
        ),
        "example_or_application": bool(
            re.search(r"例如|比如|场景|应用|假设|example|apply", message, re.I)
        ),
        "grounded_in_current_point": (
            grounded_hits >= TEACHING_POLICY.minimum_grounded_term_hits
        ),
    }
    passed_checks = [name for name, passed in checks.items() if passed]
    score = min(
        TEACHING_POLICY.heuristic_max_score,
        TEACHING_POLICY.heuristic_base_score
        + TEACHING_POLICY.heuristic_check_increment * len(passed_checks),
    )
    return round(score, 2), passed_checks


async def _evaluate_understanding(
    *,
    message: str,
    point: sqlite3.Row,
    citations: list[dict[str, Any]],
    provider_session_id: str | None,
    external_completion_evidence: bool = False,
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
        "prompt_version": PROMPT_POLICY.version,
        "rules": list(
            PROMPT_POLICY.teacher_external_evaluation_rules
            if external_completion_evidence
            else PROMPT_POLICY.teacher_evaluation_rules
        ),
    }
    try:
        evaluator = provider_for_session(provider_session)
        messages = [
            {
                "role": "system",
                "content": PROMPT_POLICY.teacher_evaluation_system_message,
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ]

        if hasattr(evaluator, "structured"):
            evaluation = await evaluator.structured(messages, TeacherEvaluationV1)
        else:
            evaluation = TeacherEvaluationV1.model_validate(await evaluator.json(messages))
    except ProviderError as exc:
        _error(exc.code, exc.message, exc.status, **exc.details)
    except Exception as exc:
        _error("PROVIDER_ASSESSMENT_INVALID", "Provider 未返回有效的理解评分。", 502)
    provider_score = float(evaluation.score)
    criteria_met = bool(evaluation.criteria_met)
    required_prechecks = (
        set()
        if external_completion_evidence
        else {"substantive_answer", "grounded_in_current_point"}
    )
    passed = (
        criteria_met
        and provider_score >= TEACHING_POLICY.learner_claim_pass_floor
        and required_prechecks.issubset(precheck_checks)
    )
    return round(provider_score, 2), passed, precheck_checks, generation_meta(
        provider_session
    ) | {
        "scope": (
            "external_completion_evaluation"
            if external_completion_evidence
            else "grounded_rubric_evaluation"
        ),
        "criteria_met": criteria_met,
        "rationale": str(evaluation.rationale or "")[:1000],
        "misconception": str(evaluation.misconception or "")[:600],
        "remediation_focus": str(evaluation.remediation_focus or "")[:600],
        "system_threshold": TEACHING_POLICY.learner_claim_pass_floor,
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
            """SELECT * FROM active_teaching_sessions
               WHERE plan_id=? ORDER BY started_at DESC LIMIT 1""",
            (plan_row["id"],),
        ).fetchone()
        if existing:
            state = json.loads(existing["state"])
            current_point_id = str(state.get("current_knowledge_point_id") or "")
            turn = state.get("teacher_turn_v1") or {}
            turn_point_id = str((turn.get("concept") or {}).get("id") or "")
            # Sessions created by older desktop builds often contain one large
            # unscoped transcript and no turn for the current point.  Never
            # replay an unrelated historical answer (for example binary search
            # while the card says square root).  Re-entering the classroom
            # materialises exactly one current-point turn and leaves the audit
            # transcript intact in storage.
            if not turn_point_id or turn_point_id != current_point_id:
                tasks = _task_rows(connection, plan_row["id"])
                if tasks:
                    route_index = min(max(0, int(state.get("route_index") or 0)), len(tasks) - 1)
                    active_task_id = str(state.get("active_task_id") or "")
                    task = next(
                        (candidate for candidate in tasks if str(candidate["id"]) == active_task_id),
                        tasks[route_index],
                    )
                    point = _point_for_task(connection, task)
                    receipt = json.loads(plan_row["receipt"])
                    citations = _merge_citations(
                        _search(
                            connection,
                            workspace_id,
                            f"{point['name']} {point['explanation']} {point['acceptance_method']}",
                            plan_id=plan_row["id"],
                        ),
                        json.loads(point["source_evidence"]),
                    )
                    provider_session_id = str(body.get("provider_session_id") or "").strip() or None
                    generated_turn, generation = await _v1_prepare_turn(
                        workspace_id=workspace_id,
                        point=point,
                        task=task,
                        receipt=receipt,
                        citations=citations,
                        state=state,
                        provider_session_id=provider_session_id,
                    )
                    if generation.get("mode") == "real":
                        state["provider_session_id"] = generation.get("provider_session_id")
                    _v1_record_turn(
                        connection,
                        session_id=existing["id"],
                        workspace_id=workspace_id,
                        state=state,
                        turn=generated_turn,
                        action="teacher_turn_v1_resume_current_point",
                        citations=citations,
                        generation=generation,
                    )
                    connection.execute(
                        "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
                        (json.dumps(state, ensure_ascii=False), _now(), existing["id"]),
                    )
                    connection.commit()
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
        if str(receipt.get("activation_source") or "") != "mastery-plan-preview.v1":
            _sync_two_tier_memory(
                connection,
                workspace_id=workspace_id,
                state=state,
                navigation_event="session_started",
            )
        session_id = _id("active_session")
        started = _now()
        connection.execute(
            """INSERT INTO active_teaching_sessions
               (id,workspace_id,plan_id,state,started_at,updated_at,actual_elapsed_minutes)
               VALUES (?,?,?,?,?,?,?)""",
            (session_id, workspace_id, plan_row["id"], json.dumps(state, ensure_ascii=False), started, started, 0.0),
        )
        first_point = _point_for_task(connection, first)
        citations = _merge_citations(
            _search(
                connection,
                workspace_id,
                f"{first_point['name']} {first_point['explanation']} {first_point['acceptance_method']}",
                plan_id=plan_row["id"],
            ),
            json.loads(first_point["source_evidence"]),
        )
        provider_session_id = str(body.get("provider_session_id") or "").strip() or None
        turn, teacher_generation = await _v1_prepare_turn(
            workspace_id=workspace_id,
            point=first_point,
            task=first,
            receipt=receipt,
            citations=citations,
            state=state,
            provider_session_id=provider_session_id,
        )
        if teacher_generation.get("mode") == "real":
            state["provider_session_id"] = teacher_generation.get("provider_session_id")
        _v1_record_turn(
            connection,
            session_id=session_id,
            workspace_id=workspace_id,
            state=state,
            turn=turn,
            action="teacher_turn_v1_opening",
            citations=citations,
            generation=teacher_generation,
        )
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
        if state.get("teacher_turn_v1"):
            # The v1 opening is complete before the first learner message;
            # repeated teach-next calls must not manufacture another turn.
            return _public_session(connection, session_id)
        _sync_teacher_learner_model(
            connection,
            workspace_id=session["workspace_id"],
            state=state,
        )
        _sync_two_tier_memory(
            connection,
            workspace_id=session["workspace_id"],
            state=state,
            navigation_event="teaching_resumed",
        )
        tasks = _task_rows(connection, session["plan_id"])
        route_index = min(int(state["route_index"]), len(tasks) - 1)
        task = tasks[route_index]
        point = _point_for_task(connection, task)
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
        expression_provider_session_id = _bind_provider_session(
            state,
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
        render_state = {
            **state,
            "available_external_tools": _practice_resources_for_session(
                connection,
                session,
                state,
            ),
        }
        content, teacher_generation = await _render_teacher_content(
            draft=content,
            action=action,
            citations=citations,
            provider_session_id=expression_provider_session_id,
            state=render_state,
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
    terms = _retrieval_terms(query) - QUESTION_FUNCTION_TERMS
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
        f"""SELECT chunk.*, material.original_filename,
                  material.source_url, material.source_kind, material.provenance_json
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


def _session_attachment_rows(
    connection: sqlite3.Connection,
    session_id: str,
    attachment_ids: Iterable[Any],
) -> list[sqlite3.Row]:
    unique_ids = list(
        dict.fromkeys(
            str(value).strip()
            for value in attachment_ids
            if str(value).strip()
        )
    )
    if len(unique_ids) > MAX_CLASSROOM_ATTACHMENT_COUNT:
        _error(
            "TOO_MANY_TEACHING_ATTACHMENTS",
            f"每条课堂消息最多附带 {MAX_CLASSROOM_ATTACHMENT_COUNT} 个文件",
            422,
        )
    rows: list[sqlite3.Row] = []
    for attachment_id in unique_ids:
        row = connection.execute(
            """SELECT * FROM active_teaching_attachments
               WHERE id=? AND session_id=? AND status='ready'""",
            (attachment_id, session_id),
        ).fetchone()
        if not row:
            _error(
                "TEACHING_ATTACHMENT_NOT_FOUND",
                "附件不存在、尚未完成上传或不属于当前课堂",
                404,
                attachment_id=attachment_id,
            )
        rows.append(row)
    return rows


@router.post("/sessions/{session_id}/attachments")
async def upload_teaching_attachment(
    session_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    from PIL import Image, UnidentifiedImageError

    raw_name = safe_attachment_name(file.filename or "attachment.bin")
    suffix = Path(raw_name).suffix.lower()
    data = await file.read(MAX_CLASSROOM_ATTACHMENT_BYTES + 1)
    if not data:
        _error("TEACHING_ATTACHMENT_EMPTY", "请选择一个非空文件", 422)
    if len(data) > MAX_CLASSROOM_ATTACHMENT_BYTES:
        _error(
            "TEACHING_ATTACHMENT_TOO_LARGE",
            "课堂附件不能超过 32 MB；图片不能超过 8 MB",
            413,
        )
    try:
        upload_info = validate_attachment_bytes(
            data,
            filename=raw_name,
            declared_media_type=str(file.content_type or ""),
        )
    except ValueError as exc:
        status = 415 if "不支持" in str(exc) else 413 if "MB" in str(exc) else 422
        _error(
            "TEACHING_ATTACHMENT_INVALID",
            str(exc),
            status,
        )
    width = 0
    height = 0
    media_type = str(upload_info["media_type"])
    suffix = str(upload_info["suffix"])
    if suffix in CLASSROOM_IMAGE_SUFFIXES:
        if len(data) > MAX_TEACHING_IMAGE_BYTES:
            _error(
                "TEACHING_IMAGE_TOO_LARGE",
                "课堂图片不能超过 8 MB",
                413,
            )
        try:
            with Image.open(io.BytesIO(data)) as image:
                image_format = str(image.format or "").upper()
                width, height = image.size
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            _error(
                "TEACHING_IMAGE_INVALID",
                "文件不是有效的 PNG、JPEG 或 WebP 图片",
                422,
                reason=type(exc).__name__,
            )
        if image_format not in TEACHING_IMAGE_FORMATS:
            _error(
                "TEACHING_IMAGE_FORMAT_UNSUPPORTED",
                "课堂图片仅支持 PNG、JPEG 和 WebP",
                422,
                image_format=image_format,
            )
        if width <= 0 or height <= 0 or width * height > MAX_TEACHING_IMAGE_PIXELS:
            _error(
                "TEACHING_IMAGE_DIMENSIONS_UNSAFE",
                "课堂图片尺寸无效或像素总量超过 4000 万",
                422,
                width=width,
                height=height,
            )
        media_type, suffix = TEACHING_IMAGE_FORMATS[image_format]
    digest = _hash_bytes(data)
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        existing = connection.execute(
            """SELECT * FROM active_teaching_attachments
               WHERE session_id=? AND sha256=?""",
            (session_id, digest),
        ).fetchone()
        if existing:
            return _public_teaching_attachment(existing)
        attachment_id = _id("teaching_attachment")
        attachment_root = (
            UPLOAD_ROOT
            / "_teaching_attachments"
            / str(session["workspace_id"])
            / session_id
        )
        attachment_root.mkdir(parents=True, exist_ok=True)
        stored_path = (attachment_root / f"{attachment_id}{suffix}").resolve()
        if attachment_root.resolve() not in stored_path.parents:
            _error(
                "TEACHING_ATTACHMENT_PATH_INVALID",
                "课堂截图存储路径无效",
                500,
            )
        stored_path.write_bytes(data)
        connection.execute(
            """INSERT INTO active_teaching_attachments
               (id,session_id,workspace_id,original_filename,media_type,
                size_bytes,width,height,sha256,stored_path,status,
                analysis_mode,analysis_text,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                attachment_id,
                session_id,
                session["workspace_id"],
                str(upload_info["filename"])[:240],
                media_type,
                len(data),
                width,
                height,
                digest,
                str(stored_path),
                "ready",
                "pending",
                "",
                _now(),
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM active_teaching_attachments WHERE id=?",
            (attachment_id,),
        ).fetchone()
        return _public_teaching_attachment(row)


@router.get("/attachments/{attachment_id}/file")
def teaching_attachment_file(attachment_id: str):
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM active_teaching_attachments WHERE id=?",
            (attachment_id,),
        ).fetchone()
        if not row:
            _error("TEACHING_ATTACHMENT_NOT_FOUND", "课堂附件不存在", 404)
        stored_path = Path(row["stored_path"]).expanduser().resolve()
        if not stored_path.is_file():
            _error(
                "TEACHING_ATTACHMENT_FILE_MISSING",
                "课堂附件文件已经缺失",
                410,
            )
        inline = attachment_kind(
            str(row["original_filename"]),
            str(row["media_type"]),
        ) == "image" or str(row["media_type"]) == "application/pdf"
        return FileResponse(
            stored_path,
            media_type=str(row["media_type"]),
            filename=Path(str(row["original_filename"])).name,
            content_disposition_type="inline" if inline else "attachment",
        )


@router.get("/voice/status")
def teaching_voice_status() -> dict[str, Any]:
    return classroom_asr_status()


@router.post("/voice/warm")
async def teaching_voice_warm() -> dict[str, Any]:
    return await asyncio.to_thread(warm_classroom_asr)


@router.post("/sessions/{session_id}/voice/transcribe")
async def transcribe_teaching_voice(
    session_id: str,
    file: UploadFile = File(...),
    language: str = "auto",
) -> dict[str, Any]:
    with _connect() as connection:
        exists = connection.execute(
            "SELECT 1 FROM active_teaching_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
    if not exists:
        _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
    data = await file.read(20 * 1024 * 1024 + 1)
    try:
        return await asyncio.to_thread(
            transcribe_classroom_audio,
            data,
            filename=file.filename or "recording.webm",
            language=language,
        )
    except ValueError as exc:
        _error("TEACHING_VOICE_INVALID", str(exc), 422)
    except RuntimeError as exc:
        _error("TEACHING_VOICE_UNAVAILABLE", str(exc), 503)


async def _analyse_teaching_attachments(
    *,
    connection: sqlite3.Connection,
    attachment_rows: list[sqlite3.Row],
    provider_session_id: str | None,
    learner_message: str,
    point: sqlite3.Row,
) -> tuple[str, str]:
    if not attachment_rows:
        return "", "none"
    image_rows = [
        row for row in attachment_rows
        if attachment_kind(str(row["original_filename"]), str(row["media_type"])) == "image"
    ]
    document_rows = [row for row in attachment_rows if row not in image_rows]
    document_sections: list[str] = []
    document_modes: list[str] = []
    remaining_chars = 48_000
    for row in document_rows:
        text, document_mode = extract_attachment_text(
            Path(row["stored_path"]),
            filename=str(row["original_filename"]),
            media_type=str(row["media_type"]),
            budget=max(1, remaining_chars),
        )
        document_modes.append(document_mode)
        if text:
            section = (
                f"附件：{row['original_filename']}\n"
                f"读取方式：{document_mode}\n{text}"
            )[:remaining_chars]
            document_sections.append(section)
            remaining_chars -= len(section)
    document_analysis = "\n\n".join(document_sections)
    vision_analysis = ""
    vision_mode = "none"
    local_ocr_sections: list[str] = []
    for row in image_rows:
        text, ocr_mode = await asyncio.to_thread(
            extract_image_text,
            Path(row["stored_path"]),
            budget=max(1, remaining_chars),
        )
        if text:
            section = (
                f"图片：{row['original_filename']}\n"
                f"本地 OCR 引擎：{ocr_mode}\n{text}"
            )[:remaining_chars]
            local_ocr_sections.append(section)
            remaining_chars -= len(section)
    local_ocr_analysis = "\n\n".join(local_ocr_sections)
    if image_rows and local_ocr_analysis:
        vision_mode = "local_ocr"
        vision_analysis = (
            "已在本机读取截图中的文字、代码与运行结果；"
            "以下内容将交给教师模型继续判断，不会上传到额外视觉服务。\n\n"
            f"{local_ocr_analysis}"
        )
    elif image_rows and not provider_session_id:
        vision_mode = "vision_provider_required"
        vision_analysis = (
            "截图已保存，但当前没有已验证的视觉模型。"
            "请把运行结果或报错文字粘贴到消息中；"
            "教师不会假装看到了图片。"
        )
    elif image_rows:
        try:
            provider_session = require_provider_session(provider_session_id)
            if provider_session.provider == "fake":
                raise ProviderError(
                    "PROVIDER_VISION_UNSUPPORTED",
                    "Fake Provider 不读取图片",
                    422,
                )
            images = [
                {
                    "media_type": str(row["media_type"]),
                    "data": Path(row["stored_path"]).read_bytes(),
                }
                for row in image_rows
            ]
            prompt = (
                "你正在读取学习者从外部练习环境回传的截图。"
                "只描述截图中实际可见的题目、代码、运行结果、通过状态和报错；"
                "不要猜测被裁掉或模糊的内容。先逐字提取关键代码或错误，"
                "再用简洁中文总结哪些测试通过、哪里失败。"
                f"\n当前知识点：{point['name']}"
                f"\n学习者附言：{learner_message or '请分析截图'}"
            )
            vision_analysis = (
                await provider_for_session(provider_session).vision(
                    prompt,
                    images,
                    temperature=0.1,
                )
            ).strip()
            vision_mode = "provider_vision"
        except (ProviderError, OSError) as exc:
            vision_mode = "vision_unavailable"
            vision_analysis = (
                "截图已保存，但当前模型没有成功读取图片。"
                "请把运行结果或报错文字粘贴到消息中；"
                "教师不会假装看到了图片内容。"
            )
            if isinstance(exc, ProviderError):
                vision_analysis += f"（{exc.code}）"
    if image_rows and document_rows:
        mode = (
            "provider_vision_with_documents"
            if vision_mode == "provider_vision" and document_analysis
            else (
                "local_ocr_with_documents"
                if vision_mode == "local_ocr" and document_analysis
                else vision_mode
            )
        )
    elif image_rows:
        mode = vision_mode
    elif document_analysis:
        mode = "document_text"
    else:
        mode = "document_unreadable"
    analysis_parts = []
    if vision_analysis:
        analysis_parts.append(f"[图片读取]\n{vision_analysis}")
    if document_analysis:
        analysis_parts.append(f"[文件正文]\n{document_analysis}")
    if document_rows and not document_analysis:
        analysis_parts.append(
            "[文件读取]\n附件已安全保存，但没有抽取到可供教师阅读的正文。"
            "请换成 PDF、Office、文本、代码文件，或在消息里补充关键内容。"
        )
    analysis = "\n\n".join(analysis_parts)
    for row in attachment_rows:
        connection.execute(
            """UPDATE active_teaching_attachments
               SET analysis_mode=?,analysis_text=? WHERE id=?""",
            (mode, analysis[:48_000], row["id"]),
        )
    return analysis, mode


@router.post("/sessions/{session_id}/advance")
async def advance_session(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    visible_message = str(body.get("message") or "").strip()
    verify_completion = bool(body.get("verify_completion"))
    force_completion = bool(body.get("force_complete"))
    direct_completion = verify_completion or force_completion
    raw_attachment_ids = body.get("attachment_ids") or []
    if not isinstance(raw_attachment_ids, list):
        _error(
            "INVALID_TEACHING_ATTACHMENTS",
            "attachment_ids 必须是附件 ID 数组",
            422,
        )
    if not visible_message and not raw_attachment_ids and not force_completion:
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
            None
            if force_completion
            else _bind_provider_session(
                state,
                str(body.get("provider_session_id") or "").strip() or None,
            )
        )
        tasks = _task_rows(connection, session["plan_id"])
        if not tasks:
            _error("ACTIVE_PLAN_EMPTY", "计划没有可执行任务", 409)
        route_index = min(int(state["route_index"]), len(tasks) - 1)
        task = tasks[route_index]
        if direct_completion and not force_completion:
            current_point_id = str(task["knowledge_point_id"])
            completion_candidates = [
                (index, candidate)
                for index, candidate in enumerate(tasks)
                if str(candidate["knowledge_point_id"]) == current_point_id
                and str(candidate["task_type"] or "initial_learning") == "initial_learning"
                and str(candidate["status"]) != "completed"
            ]
            independent_candidates = [
                item
                for item in completion_candidates
                if str(item[1]["learning_phase"]) in {
                    "integrated_initial",
                    "independent_application",
                }
            ]
            if independent_candidates:
                route_index, task = independent_candidates[-1]
            elif completion_candidates:
                route_index, task = completion_candidates[-1]
            state["route_index"] = route_index
            state["active_task_id"] = task["id"]
            state["current_teaching_step"] = "check_understanding"
            state["next_action"] = "verify_external_completion"
        point = _point_for_task(connection, task)
        if force_completion:
            attachment_rows = []
            attachment_analysis = ""
            attachment_analysis_mode = "not_requested"
        else:
            attachment_rows = _session_attachment_rows(
                connection,
                session_id,
                raw_attachment_ids,
            )
            attachment_analysis, attachment_analysis_mode = (
                await _analyse_teaching_attachments(
                    connection=connection,
                    attachment_rows=attachment_rows,
                    provider_session_id=requested_provider_session_id,
                    learner_message=visible_message,
                    point=point,
                )
            )
        if not visible_message and not force_completion:
            visible_message = (
                "请分析我上传的练习结果截图。"
                if attachment_rows and all(
                    attachment_kind(
                        str(row["original_filename"]),
                        str(row["media_type"]),
                    ) == "image"
                    for row in attachment_rows
                )
                else "请阅读我上传的附件，并结合当前知识点继续指导。"
            )
        message = (
            "学习者主动确认已掌握，并选择直接完成当前知识点。"
            if force_completion
            else visible_message
        )
        if attachment_analysis:
            message += (
                "\n\n[附件读取结果；不是学习者手动输入]"
                f"\n识别模式：{attachment_analysis_mode}"
                f"\n{attachment_analysis}"
            )
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
            elapsed = max(
                0.0,
                min(
                    float(TEACHING_POLICY.maximum_elapsed_event_minutes),
                    float(body.get("actual_elapsed_minutes") or 0),
                ),
            )
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
        timing_task = next(
            (candidate for candidate in tasks if str(candidate["id"]) == timing_task_id),
            task,
        )
        _add_task_elapsed(connection, timing_task, elapsed)
        if not force_completion:
            _record_message(
                connection,
                session_id,
                "user",
                "feedback",
                visible_message,
                [],
                [str(row["id"]) for row in attachment_rows],
            )
            teacher_memory = dict(state.get("teacher_memory") or {})
            recent_turns = list(teacher_memory.get("recent_turns") or [])
            if message:
                recent_turns.append({"role": "learner", "content": message[:600]})
            teacher_memory["recent_turns"] = recent_turns[-12:]
            teacher_memory["last_learner_message"] = message[:600]
            state["teacher_memory"] = teacher_memory

        attachment_unreadable = attachment_analysis_mode in {
            "vision_provider_required",
            "vision_unavailable",
            "document_unreadable",
        }
        if attachment_rows and attachment_unreadable:
            action = (
                "vision_unavailable"
                if attachment_analysis_mode.startswith("vision_")
                else "attachment_unavailable"
            )
            content = attachment_analysis
            state["next_action"] = "await_visual_text_fallback"
            state["autonomy"] = {
                "mode": "teacher_led_until_learner_input",
                "awaiting_learner": True,
                "reason": (
                    "vision_text_fallback_required"
                    if attachment_analysis_mode.startswith("vision_")
                    else "attachment_text_fallback_required"
                ),
            }
            state["teacher_generation"] = {
                "mode": "system_guided",
                "provider": None,
                "fallback_used": True,
                "scope": "attachment_capability_status",
            }
            teacher_memory = dict(state.get("teacher_memory") or {})
            teacher_memory["last_interaction"] = action
            teacher_memory["recent_turns"] = (
                list(teacher_memory.get("recent_turns") or [])
                + [{"role": "teacher", "action": action, "content": content[:600]}]
            )[-12:]
            state["teacher_memory"] = teacher_memory
            if not _is_canonical_task(task):
                _write_knowledge_memory(
                    connection,
                    workspace_id=session["workspace_id"],
                    point=point,
                    state=state,
                    role="learner",
                    message=visible_message,
                    confused=False,
                )
                _write_knowledge_memory(
                    connection,
                    workspace_id=session["workspace_id"],
                    point=point,
                    state=state,
                    role="teacher",
                    message=content,
                    teacher_action=action,
                )
                _sync_two_tier_memory(
                    connection,
                    workspace_id=session["workspace_id"],
                    state=state,
                    navigation_event="awaiting_attachment_text_fallback",
                )
            _record_message(
                connection,
                session_id,
                "teacher",
                action,
                content,
                [],
            )
            connection.execute(
                """UPDATE active_teaching_sessions
                   SET state=?,updated_at=?,
                       actual_elapsed_minutes=actual_elapsed_minutes+?
                   WHERE id=?""",
                (
                    json.dumps(state, ensure_ascii=False),
                    _now(),
                    elapsed,
                    session_id,
                ),
            )
            connection.commit()
            return _public_session(connection, session_id)

        if state.get("teacher_turn_v1"):
            receipt_row = connection.execute(
                "SELECT receipt FROM active_plans WHERE id=?", (session["plan_id"],)
            ).fetchone()
            receipt = json.loads(receipt_row["receipt"]) if receipt_row else {}
            return await _v1_process_answer(
                connection=connection,
                session=session,
                state=state,
                task=task,
                point=point,
                receipt=receipt,
                message=message,
                body=body,
                citations=citations,
                provider_session_id=requested_provider_session_id,
                elapsed=elapsed,
            )

        lower = message.casefold()
        is_confused = any(marker in lower for marker in CONFUSION_MARKERS)
        is_understood = bool(body.get("understood")) or any(marker in lower for marker in UNDERSTOOD_MARKERS)
        if direct_completion:
            is_confused = False
            is_understood = True
        if not force_completion:
            _write_knowledge_memory(
                connection,
                workspace_id=session["workspace_id"],
                point=point,
                state=state,
                role="learner",
                message=message,
                confused=is_confused,
            )
            _sync_two_tier_memory(
                connection,
                workspace_id=session["workspace_id"],
                state=state,
                navigation_event="learner_feedback",
            )
        is_remediation = bool(state.get("remediation_stack"))
        is_pedagogical_request = any(
            marker in lower
            for marker in (
                "带着我",
                "教我",
                "一步一步",
                "慢慢学",
                "怎么开始",
                "从哪里开始",
                "不敢写",
                "害怕写",
                "完成任务",
                "忘光",
                "忘了",
                "没基础",
                "零基础",
            )
        )
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
        knowledge_scope = "source_grounded"
        has_question_form = (
            "?" in message
            or "？" in message
            or any(
                marker in message
                for marker in (
                    "为什么",
                    "怎么",
                    "能不能",
                    "是否",
                    "请问",
                    "哪一步",
                    "哪里",
                    "吗",
                    "呢",
                )
            )
        )
        is_explicit_question = (
            (has_question_form or (
                state.get("current_teaching_step") == "question_invitation"
                and not wants_practice
            ))
            and not is_understood
            and not is_remediation
        )
        if direct_completion:
            is_pedagogical_request = False
            is_explicit_question = False
            is_remediation = False
        question_hits = _search(
            connection, session["workspace_id"], message, plan_id=session["plan_id"]
        ) if is_explicit_question else []
        if question_hits:
            query_topic_terms = _retrieval_terms(message) - QUESTION_FUNCTION_TERMS
            current_topic_terms = _retrieval_terms(
                " ".join(
                    (
                        str(point["name"]),
                        str(point["explanation"]),
                        str(point["acceptance_method"]),
                    )
                )
            )
            refers_to_current_context = any(
                marker in lower
                for marker in (
                    "这里",
                    "这个",
                    "这样",
                    "这一步",
                    "刚才",
                    "当前",
                    "它",
                    "该题",
                    "本题",
                )
            )
            if not (query_topic_terms & current_topic_terms) and not refers_to_current_context:
                # A lexical retriever can find a generic shared word such as
                # “运行”. Do not mislabel an unrelated question as grounded in
                # the current lesson merely because of that weak collision.
                question_hits = []
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
        if is_pedagogical_request and not is_understood and not is_remediation:
            action = "learning_coach_response"
            citations = question_hits
            knowledge_scope = "pedagogical_coaching"
            first_check = (
                f"看着“{point['name']}”这个名称，你觉得它大概要把什么输入变成什么结果？"
                "完全不知道也可以直接回答“不知道”。"
            )
            content = (
                "可以，我会带着你完成，而且这不是一道需要从资料里寻找事实答案的问题。"
                "你是在告诉我当前基础、压力和希望的教学节奏；这类学习引导不需要先命中本地资料。"
                "\n\n我们先不要求你独立写完整代码。第一步只做一件事："
                f"围绕“{point['name']}”，我先用生活语言说明输入、目标和一个最小例子，"
                "然后请你只回答一个很短的问题；根据你的回答，我再决定补讲还是进入下一小步。"
                f"\n\n现在先告诉我：{first_check}"
            )
            state["current_teaching_step"] = "question_invitation"
            state["next_action"] = "answer_question_or_start_practice"
            state["autonomy"] = {
                "mode": "teacher_led_question_dialogue",
                "awaiting_learner": True,
                "reason": "pedagogical_coaching_requested",
            }
        elif question_conflicts:
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
            action = (
                "model_general_answer"
                if requested_provider_session_id
                else "insufficient_evidence"
            )
            citations = []
            if requested_provider_session_id:
                knowledge_scope = "model_general_knowledge"
                content = (
                    "当前上传资料没有直接覆盖这个问题。下面可以使用模型的稳定通识知识回答，"
                    "但会明确标成“模型通识补充”，不会伪造为资料引用。"
                    f"\n\n请直接回答学习者的问题：“{message[:400]}”。"
                    "先给简短结论，再给一个适合当前基础的小例子；"
                    "如果问题涉及最新信息、价格、政策或实时状态，明确说明需要另行联网核验。"
                    "\n\n最后只问一个能帮助学习者继续的小问题。"
                )
            else:
                content = (
                    "当前上传资料没有直接覆盖这个问题，而且目前没有连接真实模型来提供通识补充。"
                    "这不代表问题超标；连接 Provider 后我可以把“资料依据”和“模型通识”分开回答，"
                    "但不会把通识内容伪装成资料引用。"
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
                    _complete_execution_task(connection, remediation_task)
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
            if force_completion:
                score = 1.0
                passed = True
                grading_checks = ["learner_self_attested_completion"]
                evaluation = {
                    "mode": "learner_override",
                    "scope": "learner_override",
                    "criteria_met": True,
                    "rationale": (
                        "学习者拥有首学阶段的最终通过权；该记录不是系统独立验收，"
                        "后续完整复习仍会保留。"
                    ),
                    "system_threshold": None,
                    "required_prechecks": [],
                    "prechecks_satisfied": True,
                }
            else:
                score, passed, grading_checks, evaluation = await _evaluate_understanding(
                    message=message,
                    point=point,
                    citations=citations,
                    provider_session_id=requested_provider_session_id,
                    external_completion_evidence=verify_completion,
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
                    (
                        "self_attested_completion"
                        if force_completion
                        else (
                            "provider_grounded_rubric"
                            if evaluation.get("mode") == "real"
                            else "unverified_recall"
                        )
                    ),
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
                            "completion_origin": "self_attested" if force_completion else "provider_verified",
                            "provider_verified": bool(not force_completion and evaluation.get("mode") == "real"),
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
                    "completion_origin": "self_attested" if force_completion else "provider_verified",
                    "provider_verified": bool(not force_completion and evaluation.get("mode") == "real"),
                }
            )
            if force_completion and _is_canonical_task(task):
                event_key = f"self-attested:{session_id}:{point['id']}"
                connection.execute(
                    """INSERT OR IGNORE INTO orm_plan_mastery_events
                       (id,workspace_id,plan_id,task_id,map_revision_id,knowledge_point_id,
                        evidence_hash,event_type,score,passed,payload,idempotency_key,occurred_at)
                       VALUES (?,?,?,?,?,?,?,'self_attested_completion',0,0,?,?,?)""",
                    (
                        "plan_self_attested_"
                        + hashlib.sha256(event_key.encode("utf-8")).hexdigest()[:32],
                        task["workspace_id"],
                        task["plan_id"],
                        task["id"],
                        task["map_revision_id"],
                        point["id"],
                        point["evidence_hash"],
                        json.dumps(
                            {
                                "schema": "mentorflow.self-attested-completion.v1",
                                "completion_origin": "self_attested",
                                "provider_verified": False,
                                "completed": True,
                            },
                            ensure_ascii=False,
                        ),
                        event_key,
                        _now(),
                    ),
            )
            if passed:
                state.update(
                    {
                        "completion_origin": "self_attested" if force_completion else "provider_verified",
                        "provider_verified": bool(not force_completion and evaluation.get("mode") == "real"),
                        "self_attested_completion": bool(force_completion),
                        "mastery_changed": bool(completes_independent_phase and not force_completion),
                    }
                )
                completion_tasks = (
                    [
                        candidate
                        for candidate in tasks
                        if str(candidate["knowledge_point_id"]) == str(point["id"])
                        and str(candidate["task_type"] or "initial_learning") == "initial_learning"
                        and str(candidate["status"]) != "completed"
                    ]
                    if direct_completion
                    else [task]
                )
                newly_completed_task_ids = [
                    str(candidate["id"]) for candidate in completion_tasks
                ]
                newly_completed_minutes = sum(
                    int(candidate["estimated_task_minutes"])
                    for candidate in completion_tasks
                )
                if completes_independent_phase and not force_completion:
                    connection.execute(
                        "UPDATE active_knowledge_points SET mastery=MAX(mastery,?) WHERE id=?",
                        (score, point["id"]),
                    )
                for completed_task_id in newly_completed_task_ids:
                    completed_row = connection.execute(
                        "SELECT * FROM active_plan_tasks WHERE id=?",
                        (completed_task_id,),
                    ).fetchone()
                    if completed_row is not None:
                        _complete_execution_task(connection, completed_row)
                    if completed_task_id not in state["completed_tasks"]:
                        state["completed_tasks"].append(completed_task_id)
                state["completed_task_minutes"] = int(
                    state.get("completed_task_minutes", 0)
                ) + newly_completed_minutes
                state["today_remaining_task_minutes"] = max(
                    0,
                    int(state["today_remaining_task_minutes"]) - newly_completed_minutes,
                )
                if completes_independent_phase and not force_completion:
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
                    if direct_completion:
                        while (
                            route_index < len(tasks)
                            and str(tasks[route_index]["knowledge_point_id"]) == str(point["id"])
                        ):
                            route_index += 1
                    if route_index >= len(tasks):
                        state["route_index"] = len(tasks)
                        state["current_teaching_step"] = "completed"
                        state["next_action"] = "review"
                        action = "complete_route"
                        content = "当前计划路线已完成；下一步进入基于掌握证据的间隔复习。"
                    else:
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
                        if force_completion:
                            progress_message = (
                                f"已按你的确认直接完成“{point['name']}”的全部首学步骤，"
                                f"并安排 {review_due} 的首次复习。之后仍可随时回来提问。"
                            )
                        elif verify_completion:
                            progress_message = (
                                f"外部做题结果核验通过（{score:.0%}），已直接完成"
                                f"“{point['name']}”的全部首学步骤，并安排 {review_due} 的首次复习。"
                            )
                        elif completes_independent_phase:
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
        if force_completion:
            state["teacher_generation"] = {
                "mode": "direct_state_transition",
                "provider": None,
                "fallback_used": False,
                "scope": "learner_override",
            }
            teacher_memory = dict(state.get("teacher_memory") or {})
            teacher_memory["last_interaction"] = "learner_override_completed"
            state["teacher_memory"] = teacher_memory
            _sync_two_tier_memory(
                connection,
                workspace_id=session["workspace_id"],
                state=state,
                navigation_event=(
                    "advanced_to_next_point"
                    if action == "advance_route"
                    else "teaching_route_completed"
                ),
            )
            connection.execute(
                """UPDATE active_teaching_sessions
                   SET state=?,updated_at=?,
                       actual_elapsed_minutes=actual_elapsed_minutes+?
                   WHERE id=?""",
                (json.dumps(state, ensure_ascii=False), _now(), elapsed, session_id),
            )
            connection.commit()
            return _public_session(connection, session_id)
        content, teacher_generation = await _render_teacher_content(
            draft=content,
            action=action,
            citations=citations,
            provider_session_id=requested_provider_session_id,
            state=state,
            knowledge_scope=knowledge_scope,
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
        _write_knowledge_memory(
            connection,
            workspace_id=session["workspace_id"],
            point=point,
            state=state,
            role="teacher",
            message=content,
            teacher_action=action,
        )
        _sync_two_tier_memory(
            connection,
            workspace_id=session["workspace_id"],
            state=state,
            navigation_event=(
                "advanced_to_next_point"
                if action == "advance_route"
                else "teaching_turn_completed"
            ),
        )
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
async def focus_session_task(
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
        previous_route_index = min(
            max(0, int(state.get("route_index") or 0)),
            max(0, len(tasks) - 1),
        )
        previous_task = tasks[previous_route_index]
        previous_point = _point_for_task(connection, previous_task)
        target_point = _point_for_task(connection, task)
        changed_point = (
            str(previous_task["knowledge_point_id"])
            != str(task["knowledge_point_id"])
        )
        previous_incomplete = str(previous_task["status"]) != "completed"
        if _is_canonical_task(task):
            completed_point_ids = {
                str(candidate["knowledge_point_id"])
                for candidate in tasks
                if str(candidate["status"]) == "completed"
            }
            unmet_prerequisites = [
                str(row["label"])
                for row in connection.execute(
                    """SELECT node.label,node.node_id
                       FROM orm_knowledge_edges AS edge
                       JOIN orm_knowledge_nodes AS node
                         ON node.map_revision_id=edge.map_revision_id
                        AND node.node_id=edge.source_node_id
                       WHERE edge.map_revision_id=?
                         AND edge.target_node_id=?
                         AND edge.relation='prerequisite'
                       ORDER BY node.label,node.node_id""",
                    (task["map_revision_id"], task["knowledge_point_id"]),
                ).fetchall()
                if str(row["node_id"]) not in completed_point_ids
            ]
        else:
            unmet_prerequisites = [
                str(row["name"])
                for row in connection.execute(
                    """SELECT prerequisite.name
                       FROM active_dependencies dependency
                       JOIN active_knowledge_points prerequisite
                         ON prerequisite.id=dependency.prerequisite_id
                       WHERE dependency.workspace_id=?
                         AND dependency.dependent_id=?
                         AND prerequisite.mastery<0.8
                       ORDER BY prerequisite.name""",
                    (session["workspace_id"], task["knowledge_point_id"]),
                ).fetchall()
            ]
        scheduled_for_later = str(task["day_date"]) > date.today().isoformat()
        if changed_point and (scheduled_for_later or unmet_prerequisites):
            navigation_kind = "future_jump"
        elif changed_point:
            navigation_kind = "cross_point_jump"
        else:
            navigation_kind = "resume_point"
        if changed_point and previous_point and not _is_canonical_task(task):
            _write_knowledge_memory(
                connection,
                workspace_id=session["workspace_id"],
                point=previous_point,
                state=state,
                role="system",
                message=(
                    f"学习者在“{previous_point['name']}”"
                    f"{'尚未完成时' if previous_incomplete else '完成后'}"
                    f"跳转到“{target_point['name']}”。"
                ),
                teacher_action="learner_navigated_away",
            )
        state["route_index"] = route_index
        state["route_length"] = len(tasks)
        state["current_knowledge_point_id"] = task["knowledge_point_id"]
        state["current_teaching_step"] = "daily_briefing"
        state["next_action"] = "explain_current_point"
        state["estimated_task_minutes"] = task["estimated_task_minutes"]
        state["active_task_id"] = task["id"]
        state["today_remaining_task_minutes"] = sum(
            int(candidate["estimated_task_minutes"] or 0)
            - int(candidate["completed_task_minutes"] or 0)
            for candidate in tasks
            if str(candidate["day_date"]) == str(task["day_date"])
            and str(candidate["status"]) != "completed"
        )
        state["today_plan"] = _daily_route_summary(
            connection, session["plan_id"], task["day_date"]
        )
        state["autonomy"] = {
            "mode": "teacher_led_until_learner_input",
            "awaiting_learner": True,
            "reason": "learner_foundation_required",
        }
        state["navigation_context"] = {
            "kind": navigation_kind,
            "from_knowledge_point_id": str(previous_task["knowledge_point_id"]),
            "from_knowledge_point_name": str(previous_point["name"]) if previous_point else "",
            "from_incomplete": previous_incomplete,
            "to_knowledge_point_id": str(task["knowledge_point_id"]),
            "to_knowledge_point_name": str(target_point["name"]) if target_point else "",
            "scheduled_for_later": scheduled_for_later,
            "scheduled_date": str(task["day_date"]),
            "unmet_prerequisites": unmet_prerequisites,
        }
        teacher_memory = dict(state.get("teacher_memory") or {})
        teacher_memory["last_interaction"] = "calendar_task_selected"
        state["teacher_memory"] = teacher_memory
        _sync_teacher_learner_model(
            connection,
            workspace_id=session["workspace_id"],
            state=state,
        )
        if not _is_canonical_task(task):
            _sync_two_tier_memory(
                connection,
                workspace_id=session["workspace_id"],
                state=state,
                navigation_event=navigation_kind,
            )
        receipt_row = connection.execute(
            "SELECT receipt FROM active_plans WHERE id=?", (session["plan_id"],)
        ).fetchone()
        receipt = json.loads(receipt_row["receipt"]) if receipt_row else {}
        citations = _merge_citations(
            _search(
                connection,
                session["workspace_id"],
                f"{target_point['name']} {target_point['explanation']} {target_point['acceptance_method']}",
                plan_id=session["plan_id"],
            ),
            json.loads(target_point["source_evidence"]),
        )
        turn, generation = await _v1_prepare_turn(
            workspace_id=session["workspace_id"],
            point=target_point,
            task=task,
            receipt=receipt,
            citations=citations,
            state=state,
            provider_session_id=str(state.get("provider_session_id") or "").strip() or None,
        )
        _v1_record_turn(
            connection,
            session_id=session_id,
            workspace_id=session["workspace_id"],
            state=state,
            turn=turn,
            action="teacher_turn_v1_focus",
            citations=citations,
            generation=generation,
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
        provider_session_id = _bind_provider_session(
            state, str(body.get("provider_session_id") or "").strip() or None
        )
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
        provider_session_id = _bind_provider_session(
            state, str(body.get("provider_session_id") or "").strip() or None
        )
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
            SCHEDULING_POLICY.minimum_review_rounds,
            min(
                SCHEDULING_POLICY.maximum_review_rounds,
                int(
                    json.loads(plan_receipt_row["receipt"]).get("review_rounds")
                    or SCHEDULING_POLICY.default_review_rounds
                )
                if plan_receipt_row
                else SCHEDULING_POLICY.default_review_rounds,
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
                intervals = SCHEDULING_POLICY.review_intervals_days
                next_interval = intervals[min(len(intervals) - 1, next_step - 1)]
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
        if passed:
            connection.execute(
                "UPDATE active_plan_tasks SET completed_at=COALESCE(completed_at,?) WHERE id=?",
                (_now(), review_task_id),
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
        SCHEDULING_POLICY.minimum_review_rounds,
        min(
            SCHEDULING_POLICY.maximum_review_rounds,
            int(
                json.loads(plan["receipt"]).get("review_rounds")
                or SCHEDULING_POLICY.default_review_rounds
            ),
        ),
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
           WHERE task.plan_id=?
             AND COALESCE(NULLIF(task.original_day_date,''),task.day_date)<?
             AND task.status IN ('planned','in_progress','unscheduled')
           ORDER BY COALESCE(NULLIF(task.original_day_date,''),task.day_date),
                    task.point_sequence,task.task_order,task.id""",
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
        task["debt_date"] = str(
            task.get("original_day_date") or task.get("day_date") or ""
        )
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
    overdue_days_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in overdue_tasks:
        overdue_days_map[str(task["debt_date"])].append(task)
    overdue_days = [
        {
            "date": debt_date,
            "task_count": len(day_tasks),
            "remaining_task_minutes": sum(
                max(
                    0,
                    int(task["estimated_task_minutes"] or 0)
                    - int(task["completed_task_minutes"] or 0),
                )
                for task in day_tasks
            ),
            "tasks": day_tasks,
        }
        for debt_date, day_tasks in sorted(overdue_days_map.items())
    ]
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
            min(str(task["debt_date"]) for task in overdue_tasks)
            if overdue_tasks
            else None
        ),
        "overdue_day_count": len(overdue_days),
        "overdue_days": overdue_days,
        "overdue_tasks": overdue_tasks,
        "projected_review_items": projected_reviews,
        "learning_order_policy": (
            "严格先满足前置依赖；同一时刻可学习的知识点按简单、"
            "中等、困难排序，难度相同再按资料原始顺序。"
        ),
    }


@router.get("/workspaces")
def list_learning_workspaces(
    request: Request,
    include_archived: bool = False,
    day: str = "",
    compact: bool = False,
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
                   UNION SELECT workspace_id FROM orm_knowledge_map_revisions
                     WHERE is_current=1 AND status IN ('active','ready','ready_with_review')
                   ORDER BY workspace_id"""
            ).fetchall()
        ]
        user = getattr(request.state, "local_user", None)
        # Pure local mode is a single data directory, not an account-scoped
        # service.  Do not hide newly imported workspaces simply because a
        # legacy UI-review owner row predates them.  Account mode keeps the
        # ownership filter below through the authenticated request context.
        auth_required = os.getenv("MENTORFLOW_AUTH_REQUIRED", "").strip() == "1"
        local_data_mode = os.getenv("MENTORFLOW_LOCAL_DATA_MODE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if user and auth_required and not local_data_mode:
            from . import local_auth

            owned = set(local_auth.owned_workspace_ids(user["user_id"]))
            workspace_ids = [item for item in workspace_ids if item in owned]
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
            canonical_map = connection.execute(
                """SELECT id,revision,activated_at
                   FROM orm_knowledge_map_revisions
                   WHERE workspace_id=? AND is_current=1
                     AND status IN ('active','ready','ready_with_review')
                   ORDER BY revision DESC LIMIT 1""",
                (workspace_id,),
            ).fetchone()
            if canonical_map:
                canonical_concepts = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM orm_knowledge_nodes
                           WHERE map_revision_id=? AND kind='concept'""",
                        (canonical_map["id"],),
                    ).fetchone()[0]
                )
                point_count = max(point_count, canonical_concepts)
            canonical_plan_state = (
                connection.execute(
                    """SELECT id,status,stale,scheduled_minutes,unscheduled_minutes,
                              created_at
                       FROM orm_plans WHERE id=? LIMIT 1""",
                    (plan["id"],),
                ).fetchone()
                if plan
                else None
            )
            pending_plan = connection.execute(
                """SELECT id,status,scheduled_minutes,unscheduled_minutes,created_at
                   FROM orm_plans
                   WHERE workspace_id=? AND status IN ('draft','preview')
                   ORDER BY created_at DESC LIMIT 1""",
                (workspace_id,),
            ).fetchone()
            active_plan_stale = bool(
                canonical_plan_state
                and (
                    canonical_plan_state["stale"]
                    or str(canonical_plan_state["status"] or "").casefold() == "stale"
                )
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
                if task_counts["total"] == 0:
                    v1_task_row = connection.execute(
                        """SELECT id,title,day_date,estimated_task_minutes,status,
                                  task_type,learning_phase
                           FROM orm_plan_execution_tasks
                           WHERE workspace_id=? AND plan_id=? AND status<>'completed'
                           ORDER BY day_date,task_order,id LIMIT 1""",
                        (workspace_id, plan["id"]),
                    ).fetchone()
                    if v1_task_row:
                        next_task = dict(v1_task_row)
                    v1_count_row = connection.execute(
                        """SELECT COUNT(*) AS total,
                                  COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0)
                                      AS completed
                           FROM orm_plan_execution_tasks
                           WHERE workspace_id=? AND plan_id=?""",
                        (workspace_id, plan["id"]),
                    ).fetchone()
                    task_counts = {
                        "total": int(v1_count_row["total"]),
                        "completed": int(v1_count_row["completed"]),
                    }
            point_progress = _knowledge_point_progress_summary(
                connection,
                workspace_id,
                str(plan["id"]) if plan else None,
            )
            updated_candidates = [
                str(value)
                for value in (
                    receipt_row["updated_at"] if receipt_row else "",
                    plan["created_at"] if plan else "",
                    session["updated_at"] if session else "",
                    canonical_map["activated_at"] if canonical_map else "",
                    pending_plan["created_at"] if pending_plan else "",
                )
                if value
            ]
            today_plan = (
                _workspace_day_overview(
                    connection,
                    workspace_id=workspace_id,
                    plan_id=plan["id"],
                    day_date=selected_day,
                )
                if plan
                else None
            )
            if compact and today_plan:
                # The workspace index is rendered on every desktop navigation.
                # Shipping hundreds of overdue task bodies (including evidence)
                # made this small index tens of megabytes.  Keep the actionable
                # summary and today's bounded task list; full debt detail remains
                # available from the plan/detail APIs.
                today_plan = {
                    **today_plan,
                    "overdue_days": [
                        {
                            "date": item.get("date"),
                            "task_count": int(item.get("task_count") or 0),
                            "remaining_task_minutes": int(
                                item.get("remaining_task_minutes") or 0
                            ),
                        }
                        for item in (today_plan.get("overdue_days") or [])
                    ],
                    "overdue_tasks": [],
                }
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
                        "needs_adjustment"
                        if active_plan_stale
                        else
                        "learning"
                        if session
                        else "planned"
                        if plan
                        else "setting_up"
                    ),
                    **point_progress,
                    "material_count": int(counts["material_count"]),
                    "ready_material_count": int(counts["ready_material_count"]),
                    "knowledge_point_count": point_count,
                    "active_map_revision_id": canonical_map["id"] if canonical_map else None,
                    "active_plan_stale": active_plan_stale,
                    "pending_plan_id": pending_plan["id"] if pending_plan else None,
                    "pending_plan_estimated_minutes": (
                        int(pending_plan["scheduled_minutes"] or 0)
                        if pending_plan
                        else None
                    ),
                    "pending_plan_unscheduled_minutes": (
                        int(pending_plan["unscheduled_minutes"] or 0)
                        if pending_plan
                        else None
                    ),
                    "plan_id": plan["id"] if plan else None,
                    "plan_feasible": bool(plan["feasible"]) if plan else None,
                    "session_id": session["id"] if session else None,
                    "task_count": task_counts["total"],
                    "completed_task_count": task_counts["completed"],
                    "next_task": next_task,
                    "today_plan": today_plan,
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


@router.delete("/workspaces/{workspace_id}")
def delete_learning_workspace(workspace_id: str) -> dict[str, Any]:
    """Remove an unstarted local task while protecting any learning history.

    The desktop intentionally offers this only for tasks with no completed
    work.  The server repeats that guard so a client cannot accidentally erase
    a task that already has classroom or mastery records.
    """
    upload_roots = [
        Path(os.getenv("MENTORFLOW_ACTIVE_UPLOAD_ROOT", str(PROJECT_ROOT / "data" / "active_teaching" / "uploads"))).resolve(),
        Path(os.getenv("MENTORFLOW_CLASSROOM_ASR_UPLOAD_ROOT", str(PROJECT_ROOT / "data" / "classroom" / "uploads"))).resolve(),
    ]
    paths_to_remove: list[Path] = []
    with _connect() as connection:
        exists = connection.execute(
            """SELECT 1 FROM active_materials WHERE workspace_id=?
               UNION SELECT 1 FROM active_receipts WHERE workspace_id=?
               UNION SELECT 1 FROM active_plans WHERE workspace_id=?
               LIMIT 1""",
            (workspace_id, workspace_id, workspace_id),
        ).fetchone()
        if not exists:
            _error("LEARNING_WORKSPACE_NOT_FOUND", "学习任务不存在", 404)
        progress = connection.execute(
            """SELECT
                 COALESCE((SELECT SUM(completed_task_count) FROM active_knowledge_progress_snapshots WHERE workspace_id=?),0)
                 + COALESCE((SELECT SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) FROM active_plan_tasks WHERE workspace_id=?),0)
                 + COALESCE((SELECT COUNT(*) FROM active_teaching_sessions WHERE workspace_id=?),0)
                 + COALESCE((SELECT COUNT(*) FROM active_mastery_evidence e JOIN active_teaching_sessions s ON s.id=e.session_id WHERE s.workspace_id=?),0)
                 AS progress""",
            (workspace_id, workspace_id, workspace_id, workspace_id),
        ).fetchone()
        if int(progress["progress"] or 0) > 0:
            _error("LEARNING_WORKSPACE_PROTECTED", "这个任务已经有学习进度，不能删除", 409)

        # Canonical maps and plans are append-only audit records.  The legacy
        # delete route must not erase only half of a learning space or leave a
        # ghost canonical plan behind.  Such workspaces are archive-only until
        # a dedicated audited whole-space purge exists.
        try:
            canonical = connection.execute(
                """SELECT 1 FROM orm_source_revisions WHERE workspace_id=?
                   UNION SELECT 1 FROM orm_knowledge_map_revisions WHERE workspace_id=?
                   UNION SELECT 1 FROM orm_knowledge_map_jobs WHERE workspace_id=?
                   UNION SELECT 1 FROM orm_material_dispositions WHERE workspace_id=?
                   UNION SELECT 1 FROM orm_plan_activation_records WHERE workspace_id=?
                   UNION SELECT 1 FROM orm_plan_execution_tasks WHERE workspace_id=?
                   UNION SELECT 1 FROM orm_plan_mastery_events WHERE workspace_id=?
                   LIMIT 1""",
                (workspace_id,) * 7,
            ).fetchone()
        except sqlite3.Error:
            canonical = None
        if canonical:
            _error(
                "LEARNING_WORKSPACE_ARCHIVE_REQUIRED",
                "这个计划包含可审计的知识地图或学习历史，只能归档，不能永久删除",
                409,
            )

        for row in connection.execute(
            "SELECT stored_path FROM active_materials WHERE workspace_id=? AND stored_path<>''",
            (workspace_id,),
        ).fetchall():
            paths_to_remove.append(Path(str(row["stored_path"])).resolve())
        for row in connection.execute(
            "SELECT stored_path FROM active_teaching_attachments WHERE workspace_id=? AND stored_path<>''",
            (workspace_id,),
        ).fetchall():
            paths_to_remove.append(Path(str(row["stored_path"])).resolve())

        plan_ids = [row[0] for row in connection.execute("SELECT id FROM active_plans WHERE workspace_id=?", (workspace_id,)).fetchall()]
        session_ids = [row[0] for row in connection.execute("SELECT id FROM active_teaching_sessions WHERE workspace_id=?", (workspace_id,)).fetchall()]
        task_ids = [row[0] for row in connection.execute("SELECT id FROM active_plan_tasks WHERE workspace_id=?", (workspace_id,)).fetchall()]
        point_ids = [row[0] for row in connection.execute("SELECT id FROM active_knowledge_points WHERE workspace_id=?", (workspace_id,)).fetchall()]
        review_ids = [row[0] for row in connection.execute("SELECT id FROM active_review_items WHERE workspace_id=?", (workspace_id,)).fetchall()]

        def delete_in(table: str, column: str, values: list[str]) -> None:
            if not values:
                return
            placeholders = ",".join("?" for _ in values)
            connection.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", values)

        # Delete dependent rows first; all identifiers come from this workspace.
        delete_in("active_review_attempts", "review_item_id", review_ids)
        delete_in("active_review_attempts", "session_id", session_ids)
        delete_in("active_task_time_events", "task_id", task_ids)
        delete_in("active_task_time_events", "session_id", session_ids)
        delete_in("active_mastery_evidence", "session_id", session_ids)
        delete_in("active_mastery_evidence", "knowledge_point_id", point_ids)
        delete_in("active_teaching_messages", "session_id", session_ids)
        delete_in("active_teaching_attachments", "session_id", session_ids)
        delete_in("active_replan_history", "session_id", session_ids)
        delete_in("active_replan_history", "plan_id", plan_ids)
        connection.execute("DELETE FROM active_review_attempts WHERE plan_task_id IN (SELECT id FROM active_plan_tasks WHERE workspace_id=?)", (workspace_id,))
        connection.execute("DELETE FROM active_review_items WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_teaching_sessions WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_plan_calendars WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_plan_tasks WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_plans WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_knowledge_conflicts WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_dependencies WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_knowledge_memories WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_point_details WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_knowledge_points WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_chunks WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_materials WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_external_resources WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_resource_discovery_runs WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_knowledge_progress_snapshots WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_receipts WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_workspace_settings WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_plan_calendars WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_teaching_attachments WHERE workspace_id=?", (workspace_id,))
        connection.commit()

    for path in paths_to_remove:
        if not path.is_file():
            continue
        if any(path == root or root in path.parents for root in upload_roots):
            try:
                path.unlink()
            except OSError:
                pass
    return {"workspace_id": workspace_id, "deleted": True}
