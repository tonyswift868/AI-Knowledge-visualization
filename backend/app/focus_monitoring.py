from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/active-teaching", tags=["focus-monitoring"])

MAX_FOCUS_EVENTS = 120
FOCUS_PRIVACY = {
    "processing": "browser_local",
    "frames_stored": False,
    "video_uploaded": False,
    "audio_captured": False,
    "event_cap": MAX_FOCUS_EVENTS,
}


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


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
        CREATE TABLE IF NOT EXISTS active_focus_sessions (
            id TEXT PRIMARY KEY,
            teaching_session_id TEXT NOT NULL
                REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            task_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            detector_kind TEXT NOT NULL,
            camera_enabled INTEGER NOT NULL DEFAULT 1,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            total_seconds INTEGER NOT NULL DEFAULT 0,
            focused_seconds INTEGER NOT NULL DEFAULT 0,
            distracted_seconds INTEGER NOT NULL DEFAULT 0,
            focus_rate REAL NOT NULL DEFAULT 0,
            distraction_count INTEGER NOT NULL DEFAULT 0,
            event_count INTEGER NOT NULL DEFAULT 0,
            end_reason TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_active_focus_sessions_teaching
            ON active_focus_sessions(teaching_session_id, started_at DESC);
        CREATE TABLE IF NOT EXISTS active_focus_events (
            id TEXT PRIMARY KEY,
            focus_session_id TEXT NOT NULL
                REFERENCES active_focus_sessions(id) ON DELETE CASCADE,
            client_event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_active_focus_events_session
            ON active_focus_events(focus_session_id, recorded_at);
        """
    )


def _error(code: str, message: str, status: int = 400) -> None:
    raise HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "details": {}}},
    )


class FocusStartIn(BaseModel):
    detector_kind: str = Field(default="mediapipe_face_detector", max_length=80)
    camera_enabled: bool = True


class FocusEventIn(BaseModel):
    client_event_id: str = Field(min_length=8, max_length=120)
    event_type: Literal["attention_lost", "attention_restored"]
    reason: Literal["face_absent", "page_hidden", "window_blurred", "attention_restored"]


class FocusFinishIn(BaseModel):
    reason: Literal[
        "user_finished",
        "lesson_finished",
        "page_closed",
        "component_unmounted",
        "camera_ended",
    ] = "user_finished"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _public_focus_session(
    connection: sqlite3.Connection,
    focus_id: str,
    *,
    include_events: bool = False,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM active_focus_sessions WHERE id=?",
        (focus_id,),
    ).fetchone()
    if not row:
        _error("FOCUS_SESSION_NOT_FOUND", "专注记录不存在", 404)
    payload = dict(row)
    payload["camera_enabled"] = bool(payload["camera_enabled"])
    payload["privacy"] = dict(FOCUS_PRIVACY)
    if include_events:
        payload["events"] = [
            dict(item)
            for item in connection.execute(
                """SELECT event_type,reason,recorded_at
                   FROM active_focus_events
                   WHERE focus_session_id=?
                   ORDER BY recorded_at,id""",
                (focus_id,),
            ).fetchall()
        ]
    return payload


def _attention_summary(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    ended_at: datetime,
) -> dict[str, Any]:
    started_at = _parse_time(row["started_at"]) or ended_at
    total_seconds = max(0, round((ended_at - started_at).total_seconds()))
    events = connection.execute(
        """SELECT event_type,reason,recorded_at
           FROM active_focus_events
           WHERE focus_session_id=?
           ORDER BY recorded_at,id""",
        (row["id"],),
    ).fetchall()

    distracted_seconds = 0
    lost_at: datetime | None = None
    distraction_count = 0
    for event in events:
        at = _parse_time(event["recorded_at"]) or ended_at
        if event["event_type"] == "attention_lost" and lost_at is None:
            lost_at = max(started_at, at)
            distraction_count += 1
        elif event["event_type"] == "attention_restored" and lost_at is not None:
            distracted_seconds += max(0, round((min(at, ended_at) - lost_at).total_seconds()))
            lost_at = None
    if lost_at is not None:
        distracted_seconds += max(0, round((ended_at - lost_at).total_seconds()))

    distracted_seconds = min(total_seconds, distracted_seconds)
    focused_seconds = max(0, total_seconds - distracted_seconds)
    focus_rate = round((focused_seconds / total_seconds) * 100, 1) if total_seconds else 0.0
    if total_seconds < 15:
        summary = "本次专注时间很短，记录已保存；下次可以从一个更小的学习目标开始。"
    elif focus_rate >= 90:
        summary = "本次整体很专注，只有很少的离开；可以保持现在的学习节奏。"
    elif focus_rate >= 70:
        summary = "本次大部分时间保持专注，中途有短暂离开；下一轮可减少一次切换。"
    else:
        summary = "本次出现了较多离开，但记录只用于帮助调整节奏；建议下一轮先设一个更短的目标。"
    return {
        "total_seconds": total_seconds,
        "focused_seconds": focused_seconds,
        "distracted_seconds": distracted_seconds,
        "focus_rate": focus_rate,
        "distraction_count": distraction_count,
        "event_count": len(events),
        "summary": summary,
    }


def _sync_teacher_memory(
    connection: sqlite3.Connection,
    teaching_session_id: str,
    focus_summary: dict[str, Any],
) -> None:
    session = connection.execute(
        "SELECT state FROM active_teaching_sessions WHERE id=?",
        (teaching_session_id,),
    ).fetchone()
    if not session:
        return
    try:
        state = json.loads(session["state"])
    except (TypeError, ValueError):
        state = {}
    compact_summary = {
        key: focus_summary[key]
        for key in (
            "id",
            "started_at",
            "ended_at",
            "total_seconds",
            "focused_seconds",
            "distracted_seconds",
            "focus_rate",
            "distraction_count",
            "summary",
        )
    }
    state["last_focus_summary"] = compact_summary
    teacher_memory = dict(state.get("teacher_memory") or {})
    teacher_memory["last_focus_summary"] = compact_summary
    state["teacher_memory"] = teacher_memory
    connection.execute(
        "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
        (json.dumps(state, ensure_ascii=False), _now(), teaching_session_id),
    )


@router.post("/sessions/{teaching_session_id}/focus-monitoring/start")
def start_focus_monitoring(
    teaching_session_id: str,
    body: FocusStartIn,
) -> dict[str, Any]:
    if not body.camera_enabled:
        _error("FOCUS_CAMERA_REQUIRED", "专注模式需要用户明确开启摄像头", 409)
    with _connect() as connection:
        teaching_session = connection.execute(
            "SELECT workspace_id,plan_id,state FROM active_teaching_sessions WHERE id=?",
            (teaching_session_id,),
        ).fetchone()
        if not teaching_session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        existing = connection.execute(
            """SELECT id FROM active_focus_sessions
               WHERE teaching_session_id=? AND status='active'
               ORDER BY started_at DESC LIMIT 1""",
            (teaching_session_id,),
        ).fetchone()
        if existing:
            return _public_focus_session(connection, existing["id"])
        try:
            state = json.loads(teaching_session["state"])
        except (TypeError, ValueError):
            state = {}
        focus_id = _id("focus")
        now = _now()
        connection.execute(
            """INSERT INTO active_focus_sessions
               (id,teaching_session_id,workspace_id,plan_id,task_id,status,
                detector_kind,camera_enabled,started_at,created_at,updated_at)
               VALUES (?,?,?,?,?,'active',?,1,?,?,?)""",
            (
                focus_id,
                teaching_session_id,
                teaching_session["workspace_id"],
                teaching_session["plan_id"],
                str(state.get("active_task_id") or ""),
                body.detector_kind.strip() or "mediapipe_face_detector",
                now,
                now,
                now,
            ),
        )
        connection.commit()
        return _public_focus_session(connection, focus_id)


@router.post("/focus-monitoring/{focus_id}/events")
def record_focus_event(focus_id: str, body: FocusEventIn) -> dict[str, Any]:
    with _connect() as connection:
        focus = connection.execute(
            "SELECT id,status FROM active_focus_sessions WHERE id=?",
            (focus_id,),
        ).fetchone()
        if not focus:
            _error("FOCUS_SESSION_NOT_FOUND", "专注记录不存在", 404)
        if focus["status"] != "active":
            _error("FOCUS_SESSION_FINISHED", "本次专注已经结束", 409)

        duplicate = connection.execute(
            "SELECT id FROM active_focus_events WHERE client_event_id=?",
            (body.client_event_id,),
        ).fetchone()
        if duplicate:
            return {"accepted": False, "reason": "duplicate", "privacy": dict(FOCUS_PRIVACY)}
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM active_focus_events WHERE focus_session_id=?",
                (focus_id,),
            ).fetchone()[0]
        )
        if count >= MAX_FOCUS_EVENTS:
            return {"accepted": False, "reason": "event_cap_reached", "privacy": dict(FOCUS_PRIVACY)}
        previous = connection.execute(
            """SELECT event_type FROM active_focus_events
               WHERE focus_session_id=? ORDER BY recorded_at DESC,id DESC LIMIT 1""",
            (focus_id,),
        ).fetchone()
        if (not previous and body.event_type == "attention_restored") or (
            previous and previous["event_type"] == body.event_type
        ):
            return {"accepted": False, "reason": "state_unchanged", "privacy": dict(FOCUS_PRIVACY)}

        now = _now()
        connection.execute(
            """INSERT INTO active_focus_events
               (id,focus_session_id,client_event_id,event_type,reason,recorded_at)
               VALUES (?,?,?,?,?,?)""",
            (_id("focus_event"), focus_id, body.client_event_id, body.event_type, body.reason, now),
        )
        connection.execute(
            "UPDATE active_focus_sessions SET event_count=event_count+1,updated_at=? WHERE id=?",
            (now, focus_id),
        )
        connection.commit()
        return {"accepted": True, "recorded_at": now, "privacy": dict(FOCUS_PRIVACY)}


@router.post("/focus-monitoring/{focus_id}/finish")
def finish_focus_monitoring(focus_id: str, body: FocusFinishIn) -> dict[str, Any]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM active_focus_sessions WHERE id=?",
            (focus_id,),
        ).fetchone()
        if not row:
            _error("FOCUS_SESSION_NOT_FOUND", "专注记录不存在", 404)
        if row["status"] == "completed":
            return _public_focus_session(connection, focus_id, include_events=True)

        ended_at = _now_dt()
        summary = _attention_summary(connection, row, ended_at)
        connection.execute(
            """UPDATE active_focus_sessions
               SET status='completed',ended_at=?,total_seconds=?,focused_seconds=?,
                   distracted_seconds=?,focus_rate=?,distraction_count=?,event_count=?,
                   end_reason=?,summary=?,updated_at=?
               WHERE id=?""",
            (
                ended_at.isoformat(),
                summary["total_seconds"],
                summary["focused_seconds"],
                summary["distracted_seconds"],
                summary["focus_rate"],
                summary["distraction_count"],
                summary["event_count"],
                body.reason,
                summary["summary"],
                ended_at.isoformat(),
                focus_id,
            ),
        )
        connection.commit()
        public = _public_focus_session(connection, focus_id, include_events=True)
        _sync_teacher_memory(connection, row["teaching_session_id"], public)
        connection.commit()
        return _public_focus_session(connection, focus_id, include_events=True)


@router.get("/sessions/{teaching_session_id}/focus-monitoring")
def list_focus_monitoring(
    teaching_session_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    limit = max(1, min(30, int(limit)))
    with _connect() as connection:
        exists = connection.execute(
            "SELECT id FROM active_teaching_sessions WHERE id=?",
            (teaching_session_id,),
        ).fetchone()
        if not exists:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        rows = connection.execute(
            """SELECT id FROM active_focus_sessions
               WHERE teaching_session_id=?
               ORDER BY started_at DESC LIMIT ?""",
            (teaching_session_id, limit),
        ).fetchall()
        return {
            "items": [_public_focus_session(connection, row["id"]) for row in rows],
            "privacy": dict(FOCUS_PRIVACY),
        }
