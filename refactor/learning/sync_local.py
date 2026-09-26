"""Account-local adapter for the refactor structured-sync engine.

The adapter deliberately keeps the sync wire contract separate from the
legacy learning databases.  Plans are staged in ``plans.sqlite3`` until the
header and every referenced task are present; classroom records use the
existing account-owned ``classroom.sqlite3``.  A staged value is exposed to
the engine only while the local value captured by the CAS still matches.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import sqlite3
from threading import RLock
from typing import Any

from .classroom.records import ClassroomRecords
from .file_io import _native_path
from .plan_models import CurrentPlan, PlanError
from .planning.storage import decode_plan, encode
from .sync_records.contract import SyncRecordError, validate_learning_record
from .store import MaterialError, MaterialStore, SYNC_EXCERPT_CHARACTERS
from .preferences.store import PreferenceStore
from .tools.storage import ToolStore, ToolError, atomic_json, validate_activity
from .tools.free_chat.persistence import AtomicJsonFreeChatStore


_PLAN_STATUSES = frozenset({"pending", "in_progress", "completed"})
_TURN_STATES = frozenset({"pending", "completed", "failed"})
_PROGRESS_FIELDS = frozenset({"status", "actual_elapsed_minutes", "result"})
_MAX_TEXT = 100_000
_MAX_ID = 512
_RESOURCE_CLASSIFICATIONS = frozenset({"USER_FILE", "MODEL", "OTHER", "UNKNOWN"})


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SyncRecordError("SYNC_LOCAL_INVALID", "本机学习记录不能安全序列化。") from exc


def _clone(value: Any) -> Any:
    return json.loads(_json(value))


def _required_text(value: Any, name: str, *, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise SyncRecordError("SYNC_LOCAL_INVALID", f"本机学习记录字段无效：{name}。")
    return value


def _optional_text(value: Any, name: str, *, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise SyncRecordError("SYNC_LOCAL_INVALID", f"本机学习记录字段无效：{name}。")
    return value


def _record_key(plan_key: str, task_id: str) -> str:
    _required_text(plan_key, "plan_key", limit=_MAX_ID)
    _required_text(task_id, "task_id", limit=_MAX_ID)
    value = json.dumps([plan_key, task_id], ensure_ascii=False, separators=(",", ":"))
    if len(value) > _MAX_ID:
        raise SyncRecordError("SYNC_LOCAL_INVALID", "本机学习记录标识过长。")
    return value


def _same(left: Any, right: Any) -> bool:
    return left == right


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def details_tombstoned_data(details: Any) -> bool:
    return isinstance(details, dict) and isinstance(details.get("_sync_tombstone"), dict)


def details_tombstoned(plan: CurrentPlan) -> bool:
    return details_tombstoned_data(plan.details)


def _decode_json(raw: Any, name: str) -> Any:
    if not isinstance(raw, str):
        raise SyncRecordError("SYNC_LOCAL_INVALID", f"本机学习记录字段无效：{name}。")
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SyncRecordError("SYNC_LOCAL_INVALID", f"本机学习记录字段不是 JSON：{name}。") from exc


def _exact(values: Any, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(values, dict) or set(values) != keys:
        raise SyncRecordError("SYNC_LOCAL_INVALID", f"本机学习记录字段集合无效：{name}。")
    return values


class LocalSyncAdapter:
    """Expose one authenticated PlanService account to ``SyncEngine``.

    ``plan_service._lock`` is the application lock used around plan and
    progress mutations.  The adapter uses it for every read/stage/finalize
    operation so a local edit cannot be silently replaced between its CAS
    check and publication.
    """

    def __init__(self, plan_service, scope_check=None):
        self._service = plan_service
        self._repository = plan_service._repository
        self._lock = plan_service._lock
        self._scope = self._repository.scope
        self._classroom = ClassroomRecords(self._repository.root.parents[2], self._scope.account_id)
        self._materials = MaterialStore(self._repository.root.parents[2], self._scope.account_id)
        self._preferences = PreferenceStore(self._scope.account_id, self._repository.root.parents[2])
        # Tools and free-chat are account-owned JSON stores.  Keep them behind
        # the same adapter so a second device receives the complete durable
        # activity/session payload rather than only a local cache marker.
        self._tools = ToolStore(self._repository.root.parents[2], self._scope.account_id)
        self._chat = AtomicJsonFreeChatStore(self._tools.root / "chat")
        self._fallback_lock = RLock()
        self._scope_check = scope_check

    def _check_scope(self) -> None:
        self._service.require_current()
        if self._scope_check is not None and self._scope_check() is False:
            raise PlanError("SYNC_SCOPE_CHANGED", "账号或学习空间已变化；本机记录未修改。")

    @contextmanager
    def _locked(self):
        lock = self._lock if self._lock is not None else self._fallback_lock
        with lock:
            yield

    @staticmethod
    def _ensure_tables(connection: sqlite3.Connection) -> None:
        for statement in (
            """CREATE TABLE IF NOT EXISTS cloud_records (
                record_type TEXT NOT NULL, record_id TEXT NOT NULL,
                values_json TEXT NOT NULL, expected_local_json TEXT,
                staged_at TEXT NOT NULL,
                PRIMARY KEY(record_type, record_id)
            )""",
            """CREATE TABLE IF NOT EXISTS sync_conflicts (
                conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_type TEXT NOT NULL, record_id TEXT NOT NULL,
                local_json TEXT, remote_json TEXT, created_at TEXT NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS sync_conflicts_record
                ON sync_conflicts(record_type, record_id, conflict_id)""",
            """CREATE TABLE IF NOT EXISTS learning_records (
                record_type TEXT NOT NULL, record_id TEXT NOT NULL,
                values_json TEXT NOT NULL,
                PRIMARY KEY(record_type, record_id)
            )""",
        ):
            connection.execute(statement)

    @staticmethod
    def _rows(connection: sqlite3.Connection, statement: str):
        try:
            return connection.execute(statement).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise

    @contextmanager
    def _read_connection(self, path):
        # Read-only scans must not create an account database or acquire a
        # writer lock.  Existing files use SQLite's query_only mode; an absent
        # file is represented by an in-memory empty database for this read.
        native_path = _native_path(path)
        connection = sqlite3.connect(str(native_path) if native_path.exists() else ":memory:", timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            yield connection
        finally:
            connection.rollback()
            connection.close()

    def _verify_read_owner(self, connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise PlanError("PLAN_STORAGE_VERSION", "计划文件版本不兼容，已停止读取。")
        tables = {row[0] for row in self._rows(
            connection, "SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            if version == 1:
                raise PlanError("PLAN_OWNER_MISMATCH", "计划文件归属未知，已停止读取。")
            return
        if version == 0:
            raise PlanError("PLAN_STORAGE_INVALID", "计划文件已有未知内容，已停止读取。")
        if "owner" not in tables:
            raise PlanError("PLAN_OWNER_MISMATCH", "计划文件归属未知，已停止读取。")
        owners = [tuple(row) for row in self._rows(
            connection, "SELECT account_id,issuer,subject_id FROM owner")]
        expected = [(self._scope.account_id, self._scope.issuer, self._scope.subject_id)]
        if owners != expected:
            raise PlanError("PLAN_OWNER_MISMATCH", "计划文件归属不匹配，已停止读取。")

    @staticmethod
    def _classroom_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _turn_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        """Normalize a classroom turn from SQLite to the sync representation.

        ``decision_json`` was added after the first local classroom schema was
        shipped.  A read-only adapter can still encounter one of those old
        databases, so an absent column is represented by the empty decision
        object.  The wire contract carries the decoded object rather than the
        SQLite text so CAS comparisons remain stable across devices.
        """
        values = dict(row)
        raw = values.pop("decision_json", "{}")
        if isinstance(raw, str):
            try:
                decision = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise SyncRecordError("SYNC_LOCAL_INVALID", "课堂决策记录不是有效 JSON。") from exc
        else:
            decision = raw
        if not isinstance(decision, dict):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "课堂决策记录无效。")
        values["decision_json"] = decision
        return values

    def _validate_plan_header(self, record_id: str, values: Any) -> dict[str, Any]:
        if not isinstance(record_id, str) or not record_id:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "计划标识无效。")
        required = {"key", "plan_id", "workspace_id", "title", "workspace_title",
                    "status", "source", "resources", "issues", "details", "task_ids"}
        values = _exact(values, required, "plan")
        if values["key"] != record_id:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "计划标识与内容不一致。")
        for field in ("key", "plan_id", "workspace_id", "title", "workspace_title", "source"):
            _required_text(values[field], field, limit=_MAX_TEXT if field in {"title", "workspace_title"} else _MAX_ID)
        if values["status"] not in _PLAN_STATUSES:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "计划状态无效。")
        if not isinstance(values["resources"], list) or not isinstance(values["issues"], list) or not isinstance(values["details"], dict):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "计划附属字段无效。")
        details = dict(values["details"])
        revision = details.get("plan_revision", 1)
        if type(revision) is not int or revision < 1:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "计划 revision 无效。")
        details["plan_revision"] = revision
        values["details"] = details
        tombstone = values["details"].get("_sync_tombstone")
        if tombstone is not None:
            if (not isinstance(tombstone, dict)
                    or set(tombstone) != {"schema", "deleted_at"}
                    or tombstone.get("schema") != "mentorflow.plan-tombstone.v1"):
                raise SyncRecordError("SYNC_LOCAL_INVALID", "计划删除标记无效。")
            _required_text(tombstone.get("deleted_at"), "plan.deleted_at", limit=64)
        task_ids = values["task_ids"]
        if not isinstance(task_ids, list) or not task_ids or any(not isinstance(v, str) or not v for v in task_ids):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "计划任务标识无效。")
        if len(task_ids) != len(set(task_ids)):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "计划任务标识重复。")
        for resource in values["resources"]:
            if not isinstance(resource, dict) or not resource.get("resource_id"):
                raise SyncRecordError("SYNC_LOCAL_INVALID", "计划资料关联无效。")
        _json(values)
        return _clone(values)

    def _validate_task(self, record_id: str, values: Any) -> dict[str, Any]:
        values = _exact(values, {"plan_key", "task"}, "task")
        task = values["task"]
        if not isinstance(task, dict):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "任务内容无效。")
        task_id = task.get("id")
        plan_key = values["plan_key"]
        _required_text(plan_key, "plan_key", limit=_MAX_ID)
        if not isinstance(task_id, str) or _record_key(plan_key, task_id) != record_id:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "任务标识与内容不一致。")
        _required_text(task.get("title"), "task.title", limit=_MAX_TEXT)
        if task.get("status") not in _PLAN_STATUSES:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "任务状态无效。")
        _json(task)
        return _clone(values)

    def _validate_progress(self, record_id: str, values: Any) -> dict[str, Any]:
        values = _exact(values, {"plan_key", "task_id", "payload"}, "progress")
        expected = _record_key(values["plan_key"], values["task_id"])
        if expected != record_id or not isinstance(values["payload"], dict):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "进度标识或内容无效。")
        payload = values["payload"]
        if (set(payload) - _PROGRESS_FIELDS
                or payload.get("status") not in _PLAN_STATUSES):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "进度字段或状态无效。")
        if "actual_elapsed_minutes" in payload:
            minutes = payload["actual_elapsed_minutes"]
            if (type(minutes) not in (int, float) or not math.isfinite(minutes)
                    or not 0 <= minutes <= 1_000_000):
                raise SyncRecordError("SYNC_LOCAL_INVALID", "进度用时无效。")
        if "result" in payload and not isinstance(payload["result"], dict):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "进度结果无效。")
        _json(payload)
        return _clone(values)

    def _validate_turn(self, record_id: str, values: Any) -> dict[str, Any]:
        _required_text(record_id, "record_id", limit=_MAX_ID)
        # Keep values written by the pre-decision schema readable and
        # publishable.  New records always carry the explicit empty object.
        if isinstance(values, dict) and "decision_json" not in values:
            values = dict(values, decision_json={})
        fields = {"id", "plan_key", "task_id", "action", "question", "answer", "state", "created_at", "decision_json"}
        values = _exact(values, fields, "turn")
        if values["id"] != record_id:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "对话标识与内容不一致。")
        for field in ("plan_key", "task_id", "action", "created_at"):
            _required_text(values[field], field, limit=_MAX_ID if field in {"plan_key", "task_id"} else 512)
        for field in ("question", "answer"):
            _optional_text(values[field], field)
        if values["state"] not in _TURN_STATES:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "对话状态无效。")
        if not isinstance(values["decision_json"], dict):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "课堂决策记录无效。")
        _json(values["decision_json"])
        _json(values)
        return _clone(values)

    def _validate_note(self, record_id: str, values: Any) -> dict[str, Any]:
        # ``revision`` is optional on the wire for old devices.  New writes
        # always carry it so note edits participate in the same CAS contract
        # as plans and classroom checkpoints.
        if isinstance(values, dict) and set(values) == {"plan_key", "task_id", "text"}:
            values = dict(values, revision=1)
        values = _exact(values, {"plan_key", "task_id", "text", "revision"}, "note")
        if _record_key(values["plan_key"], values["task_id"]) != record_id:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "笔记标识与内容不一致。")
        _optional_text(values["text"], "text")
        if type(values["revision"]) is not int or values["revision"] < 1:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "笔记 revision 无效。")
        return _clone(values)

    def _validate_receipt(self, record_id: str, values: Any) -> dict[str, Any]:
        _required_text(record_id, "record_id", limit=_MAX_ID)
        values = _exact(values, {"id", "plan_key", "task_id", "payload", "created_at"}, "receipt")
        if values["id"] != record_id:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "回执标识与内容不一致。")
        _required_text(values["plan_key"], "plan_key", limit=_MAX_ID)
        _required_text(values["task_id"], "task_id", limit=_MAX_ID)
        _required_text(values["created_at"], "created_at", limit=512)
        _json(values["payload"])
        _json(values)
        return _clone(values)

    def _validate_resource(self, record_id: str, values: Any) -> dict[str, Any]:
        fields = {"material_id", "filename", "media_type", "size", "sha256",
                  "product_classification", "text"}
        # Older pending ledgers used metadata-only resource records. Upgrade
        # them in memory so they remain publishable under the excerpt-aware
        # contract.
        if isinstance(values, dict) and set(values) == fields - {"text"}:
            values = dict(values, text="")
        values = _exact(values, fields, "resource")
        if values["material_id"] != record_id:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "资料标识与内容不一致。")
        _required_text(record_id, "material_id", limit=_MAX_ID)
        _required_text(values["filename"], "filename", limit=300)
        if any(char in values["filename"] for char in "/\\"):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "资料文件名不能包含路径。")
        _required_text(values["media_type"], "media_type", limit=120)
        _required_text(values["product_classification"], "product_classification", limit=128)
        if (not __import__("re").fullmatch(r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+", values["media_type"])
                or values["product_classification"] not in _RESOURCE_CLASSIFICATIONS):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "资料类型或分类无效。")
        _optional_text(values["text"], "resource.text", limit=SYNC_EXCERPT_CHARACTERS)
        if type(values["size"]) is not int or values["size"] < 0:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "资料大小无效。")
        if not isinstance(values["sha256"], str) or not __import__("re").fullmatch(r"[a-f0-9]{64}", values["sha256"]):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "资料指纹无效。")
        _json(values)
        return _clone(values)

    def _validate_tool_activity(self, record_id: str, values: Any) -> dict[str, Any]:
        values = _exact(values, {"kind", "activity_id", "payload"}, "tool_activity")
        if values["activity_id"] != record_id or values["kind"] not in {"review", "expedition"}:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "工具活动标识或类型无效。")
        try:
            validate_activity(values["payload"], self._scope.account_id,
                              values["kind"], record_id)
        except (ToolError, TypeError, ValueError) as exc:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "工具活动记录格式或账号归属不符。") from exc
        _json(values)
        return _clone(values)

    def _validate_chat_session(self, record_id: str, values: Any) -> dict[str, Any]:
        values = _exact(values, {"session_id", "payload"}, "chat_session")
        if values["session_id"] != record_id or not isinstance(values["payload"], dict):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "自由聊天会话标识或内容无效。")
        try:
            session = AtomicJsonFreeChatStore._decode(values["payload"],
                                                       expected_session_id=record_id)
            values = {"session_id": record_id,
                      "payload": AtomicJsonFreeChatStore._encode(session)}
        except Exception as exc:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "自由聊天会话格式无效。") from exc
        _json(values)
        return _clone(values)

    @staticmethod
    def _validate_research_source(record_id: str, values: Any) -> dict[str, Any]:
        values = _exact(values, {"source_id", "payload"}, "research_source")
        payload = values["payload"]
        if values["source_id"] != record_id or not isinstance(payload, dict):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "研究资料标识或内容无效。")
        required = {"id", "url", "final_url", "canonical_url", "title",
                    "content_sha256", "content_type", "retrieved_at", "source_type",
                    "summary", "excerpt", "truncated", "text", "chunks"}
        if set(payload) != required or payload.get("id") != record_id:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "研究资料字段不完整。")
        for name, limit in (("url", 2000), ("final_url", 2000), ("canonical_url", 2000),
                            ("title", 500), ("content_type", 200), ("retrieved_at", 128),
                            ("source_type", 64), ("summary", 4000), ("excerpt", 4000),
                            ("text", 8000)):
            _optional_text(payload[name], f"research.{name}", limit=limit)
        if (not __import__("re").fullmatch(r"[a-f0-9]{64}", payload["content_sha256"])
                or type(payload["truncated"]) is not bool):
            raise SyncRecordError("SYNC_LOCAL_INVALID", "研究资料指纹或截断标记无效。")
        chunks = payload["chunks"]
        if not isinstance(chunks, list) or not chunks or len(chunks) > 200:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "研究资料段落无效。")
        for chunk in chunks:
            if (not isinstance(chunk, dict) or set(chunk) != {"id", "text"}
                    or not isinstance(chunk["id"], str) or not chunk["id"]
                    or not isinstance(chunk["text"], str) or not chunk["text"]
                    or len(chunk["text"]) > 8000):
                raise SyncRecordError("SYNC_LOCAL_INVALID", "研究资料段落无效。")
        _json(values)
        return _clone(values)

    def _validate_record(self, record_type: str, record_id: str, values: Any) -> dict[str, Any]:
        if record_type == "plan":
            return self._validate_plan_header(record_id, values)
        if record_type == "task":
            return self._validate_task(record_id, values)
        if record_type == "progress":
            return self._validate_progress(record_id, values)
        if record_type == "turn":
            return self._validate_turn(record_id, values)
        if record_type == "note":
            return self._validate_note(record_id, values)
        if record_type == "receipt":
            return self._validate_receipt(record_id, values)
        if record_type == "resource":
            return self._validate_resource(record_id, values)
        if record_type == "tool_activity":
            return self._validate_tool_activity(record_id, values)
        if record_type == "chat_session":
            return self._validate_chat_session(record_id, values)
        if record_type == "research_source":
            return self._validate_research_source(record_id, values)
        if record_type in {"plan_revision", "learning_contract", "evidence", "error", "verification", "tutor_checkpoint", "daily_mission", "draft_plan"}:
            return validate_learning_record(record_type, record_id, values)
        if record_type == "setting":
            if record_id != "preferences" or set(values) != {"key", "values"} or values.get("key") != "preferences":
                raise SyncRecordError("SYNC_LOCAL_INVALID", "偏好设置记录无效。")
            self._preferences.validate(values["values"])
            return _clone(values)
        raise SyncRecordError("SYNC_LOCAL_INVALID", "学习记录类型无效。")

    def original_bytes(self, material_id: str) -> bytes:
        return self._materials.original_bytes(material_id)

    def restore_original_bytes(self, material_id: str, data: bytes) -> None:
        self._materials.restore_original_bytes(material_id, data)

    @staticmethod
    def _learning_wire(record_type: str, plan_key: str, payload: Any) -> dict[str, Any]:
        """Project classroom domain rows onto the strict sync wire shape.

        Classroom domain objects intentionally contain richer local fields
        (for example ``session_id`` and ``primary_competency_ids``).  The
        first sync version keeps its public envelope small; the projection is
        explicit so no SQLite implementation detail leaks into the server.
        """
        if not isinstance(payload, dict) or not isinstance(plan_key, str) or not plan_key:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "课堂学习记录内容无效。")
        if record_type == "learning_contract":
            contract_id = payload.get("contract_id")
            values = {
                "plan_key": plan_key,
                "contract_id": contract_id,
                "plan_revision": payload.get("plan_revision"),
                "milestone_id": payload.get("milestone_id"),
                "competency_ids": list(payload.get("primary_competency_ids", payload.get("competency_ids", []))),
                "title": payload.get("title"),
                "purpose": payload.get("purpose"),
                "target_depth": payload.get("target_depth"),
                "visible_objectives": list(payload.get("visible_objectives", [])),
                "required_evidence": list(payload.get("required_evidence", [])),
                "resource_refs": list(payload.get("resource_refs", [])),
                "estimated_minutes": payload.get("estimated_minutes"),
                "created_at": payload.get("created_at"),
            }
        elif record_type == "evidence":
            values = {
                "plan_key": plan_key,
                "evidence_id": payload.get("evidence_id"),
                "contract_id": payload.get("contract_id"),
                "objective_ids": list(payload.get("objective_ids", [])),
                "competency_ids": list(payload.get("competency_ids", [])),
                "type": payload.get("type"),
                "source": payload.get("source"),
                "summary": payload.get("summary"),
                "session_id": payload.get("session_id", "sync"),
                "artifact_refs": list(payload.get("artifact_refs", [])),
                "independence": payload.get("independence"),
                "evaluator_result": payload.get("evaluator_result"),
                "confidence": payload.get("confidence"),
                "created_at": payload.get("created_at"),
            }
        elif record_type == "error":
            values = {
                "plan_key": plan_key,
                "error_id": payload.get("error_episode_id", payload.get("error_id")),
                "contract_id": payload.get("contract_id"),
                "evidence_id": payload.get("evidence_id") or "",
                "competency_id": payload.get("competency_id"),
                "error_class": payload.get("error_class"),
                "description": payload.get("description"),
                "context": payload.get("context"),
                "hint_level": payload.get("hint_level"),
                "resolved_in_session": payload.get("resolved_in_session"),
                "misconception_candidate": payload.get("misconception_candidate") or "",
                "created_at": payload.get("created_at"),
            }
        elif record_type == "verification":
            # The classroom queue stores VerificationItem.  Completed
            # evaluator projections use the alternate result shape and are
            # already accepted by the same validator.
            if "verification_id" in payload and "objective_id" in payload:
                values = {
                    "plan_key": plan_key,
                    "verification_id": payload.get("verification_id"),
                    "contract_id": payload.get("contract_id"),
                    "objective_id": payload.get("objective_id"),
                    "priority": payload.get("priority"),
                    "reason": payload.get("reason"),
                    "status": payload.get("status"),
                    "source_error_ids": list(payload.get("source_error_ids", [])),
                    "reason_codes": list(payload.get("reason_codes", [])),
                    "source_evidence_ids": list(payload.get("source_evidence_ids", [])),
                    "created_at": payload.get("created_at"),
                }
            else:
                values = dict(payload)
                values.setdefault("plan_key", plan_key)
        elif record_type == "tutor_checkpoint":
            values = {
                "plan_key": plan_key,
                "checkpoint_id": payload.get("checkpoint_id"),
                "plan_id": payload.get("plan_id") or plan_key,
                "contract_id": payload.get("contract_id"),
                "achieved_objectives": list(payload.get("achieved_objectives", [])),
                "pending_objectives": list(payload.get("pending_objectives", [])),
                "stable_competencies": list(payload.get("stable_competencies", [])),
                "attention_competencies": list(payload.get("attention_competencies", [])),
                "active_misconception_ids": list(payload.get("active_misconception_ids", [])),
                "recent_evidence_ids": list(payload.get("recent_evidence_ids", [])),
                "suggested_next_step": payload.get("suggested_next_step"),
                "summary": payload.get("summary"),
                "created_at": payload.get("created_at"),
            }
        elif record_type == "daily_mission":
            values = {
                "schema": payload.get("schema", "mentorflow.daily-mission.v1"),
                "plan_key": plan_key,
                "mission_id": payload.get("mission_id"),
                "plan_revision": payload.get("plan_revision"),
                "day_date": payload.get("day_date"),
                "timezone": payload.get("timezone"),
                "status": payload.get("status"),
                "task_ids": list(payload.get("task_ids", [])),
                "objectives": list(payload.get("objectives", [])),
                "available_minutes": payload.get("available_minutes"),
                "used_minutes": payload.get("used_minutes"),
                "remaining_minutes": payload.get("remaining_minutes"),
                "reflowed_task_ids": list(payload.get("reflowed_task_ids", [])),
                "notice": payload.get("notice", ""),
                "started_at": payload.get("started_at"),
                "updated_at": payload.get("updated_at"),
                "idempotency_key": payload.get("idempotency_key"),
            }
        elif record_type == "draft_plan":
            values = {
                "plan_key": plan_key,
                "draft_id": payload.get("draft_id"),
                "revision": payload.get("revision"),
                "status": payload.get("status"),
                "snapshot": dict(payload.get("snapshot") or {}),
                "confirmed": payload.get("confirmed"),
                "updated_at": payload.get("updated_at"),
                "idempotency_key": payload.get("idempotency_key"),
            }
        else:
            values = dict(payload)
            values.setdefault("plan_key", plan_key)
        return values

    @staticmethod
    def _domain_learning_payload(record_type: str, values: dict[str, Any], *, plan_id: str | None = None) -> dict[str, Any]:
        """Rebuild a ClassroomRecords payload from a synced wire record."""
        if record_type == "learning_contract":
            result = dict(values)
            result["plan_id"] = result.get("plan_id") or plan_id or result["plan_key"]
            result["primary_competency_ids"] = list(result.pop("competency_ids", []))
            result.setdefault("internal_concept_ids", [])
            result.pop("plan_key", None)
            return result
        if record_type == "evidence":
            result = dict(values)
            result["plan_id"] = result.get("plan_id") or plan_id or result["plan_key"]
            result["session_id"] = result.get("session_id") or "sync"
            result.setdefault("artifact_refs", [])
            result.pop("plan_key", None)
            return result
        if record_type == "error":
            result = dict(values)
            result["plan_id"] = result.get("plan_id") or plan_id or result["plan_key"]
            result["error_episode_id"] = result.pop("error_id")
            result["evidence_id"] = result.get("evidence_id") or None
            result["misconception_candidate"] = result.get("misconception_candidate") or None
            result.pop("plan_key", None)
            return result
        if record_type == "tutor_checkpoint":
            result = dict(values)
            result["plan_id"] = result.get("plan_id") or plan_id or result["plan_key"]
            result.pop("plan_key", None)
            return result
        result = dict(values)
        if record_type == "verification":
            result.pop("plan_key", None)
            if "objective_id" in result:
                result["plan_id"] = result.get("plan_id") or plan_id or values["plan_key"]
            else:
                # EvaluationResult has no plan_id, verification_id, or
                # transport timestamp in its durable JSON payload.
                result.pop("contract_id", None)
                result.pop("verification_id", None)
                result.pop("created_at", None)
        return result

    def _plan_header(self, plan: CurrentPlan) -> dict[str, Any]:
        body = asdict(plan)
        tasks = body.pop("tasks")
        body["task_ids"] = [task.get("id") for task in tasks]
        # ``asdict`` preserves tuple fields; the wire/storage contract is JSON
        # and therefore carries resources/issues as arrays.
        body = json.loads(encode(body))
        return self._validate_plan_header(plan.key, body)

    def _plan_from_header(self, header: dict[str, Any], tasks: list[dict[str, Any]]) -> CurrentPlan:
        body = dict(header)
        body.pop("task_ids", None)
        body["tasks"] = tasks
        try:
            plan = decode_plan(encode(body))
        except PlanError as exc:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "云端计划未通过本机计划校验。") from exc
        return plan

    def _generic_records(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Read account-owned JSON state that is outside the plan databases.

        Tool activities, free-chat sessions and fetched research sources are
        all durable user data.  Projecting them here makes the structured
        sync fingerprint include edits made while their pages are hidden.
        Each record is independently validated before it reaches the wire.
        """
        result: dict[tuple[str, str], dict[str, Any]] = {}
        if self._tools.root.exists():
            for kind in ("review", "expedition"):
                for row in self._tools.list(kind):
                    record_id = str(row.get("id") or "")
                    values = self._validate_record(
                        "tool_activity", record_id,
                        {"kind": kind, "activity_id": record_id, "payload": row},
                    )
                    result[("tool_activity", record_id)] = values
        sessions_root = self._chat.root / "sessions"
        if sessions_root.exists():
            for session in self._chat.list_sessions():
                record_id = str(session.session_id)
                values = self._validate_record(
                    "chat_session", record_id,
                    {"session_id": record_id,
                     "payload": AtomicJsonFreeChatStore._encode(session)},
                )
                result[("chat_session", record_id)] = values
        service_sources = getattr(self._service, "_research_sources", {})
        source_payload = getattr(self._service, "_research_source_payload", None)
        if isinstance(service_sources, dict) and callable(source_payload):
            for source_id, raw in sorted(service_sources.items()):
                payload = source_payload(raw, include_chunks=True)
                payload["id"] = source_id
                values = self._validate_record(
                    "research_source", source_id,
                    {"source_id": source_id, "payload": payload},
                )
                result[("research_source", source_id)] = values
        return result

    def _base_records(self, plans: sqlite3.Connection, classroom: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self._rows(plans, "SELECT body FROM plans ORDER BY rowid"):
            try:
                plan = decode_plan(row[0])
            except PlanError as exc:
                raise SyncRecordError("SYNC_LOCAL_INVALID", "本机计划记录损坏。") from exc
            result[("plan", plan.key)] = self._plan_header(plan)
            for task in plan.tasks:
                task_id = task.get("id")
                values = self._validate_task(_record_key(plan.key, task_id), {"plan_key": plan.key, "task": dict(task)})
                result[("task", _record_key(plan.key, task_id))] = values
        for row in self._rows(plans, "SELECT plan_key,task_id,payload FROM progress"):
            payload = _decode_json(row[2], "progress.payload")
            values = self._validate_progress(_record_key(row[0], row[1]), {"plan_key": row[0], "task_id": row[1], "payload": payload})
            result[("progress", _record_key(row[0], row[1]))] = values

        classroom.row_factory = sqlite3.Row
        for row in self._rows(classroom, "SELECT * FROM turns ORDER BY rowid"):
            values = self._validate_turn(row["id"], self._turn_row(row))
            result[("turn", row["id"])] = values
        try:
            note_rows = self._rows(classroom, "SELECT plan_key,task_id,text,revision FROM notes ORDER BY rowid")
        except sqlite3.OperationalError as exc:
            if "no such column" not in str(exc).lower():
                raise
            note_rows = [dict(row, revision=1) for row in self._rows(
                classroom, "SELECT plan_key,task_id,text FROM notes ORDER BY rowid")]
        for row in note_rows:
            values = self._validate_note(_record_key(row["plan_key"], row["task_id"]), dict(row))
            result[("note", _record_key(row["plan_key"], row["task_id"]))] = values
        for row in self._rows(classroom, "SELECT * FROM receipts ORDER BY rowid"):
            values = self._classroom_row(row)
            values["payload"] = _decode_json(values["payload"], "receipt.payload")
            values = self._validate_receipt(row["id"], values)
            result[("receipt", row["id"])] = values
        for values in self._materials.sync_metadata():
            values = self._validate_resource(values["material_id"], values)
            result[("resource", values["material_id"])] = values
        result.update(self._generic_records())
        try:
            result[("setting", "preferences")] = self._validate_record(
                "setting", "preferences", {"key": "preferences", "values": self._preferences.load()})
        except Exception as exc:
            if isinstance(exc, SyncRecordError):
                raise
        for row in self._rows(plans, "SELECT record_type,record_id,values_json FROM learning_records ORDER BY record_type,record_id"):
            raw = _decode_json(row[2], "learning.values")
            record_id = row[1]
            if row[0] == "draft_plan":
                # Older builds keyed this row by the DraftPlan revision id.
                # Re-project it onto the stable aggregate identity while the
                # account database is upgraded in place.
                if not isinstance(raw, dict) or not isinstance(raw.get("plan_key"), str):
                    continue
                record_id = _record_key(raw["plan_key"], "current")
            values = self._validate_record(row[0], record_id, raw)
            if row[0] == "draft_plan":
                previous = result.get((row[0], record_id))
                if previous is None or int(values.get("revision", 0)) >= int(previous.get("revision", 0)):
                    result[(row[0], record_id)] = values
            else:
                result[(row[0], record_id)] = values
        try:
            # A never-opened account has no local draft aggregate.  Do not
            # synthesize the deterministic empty bootstrap as a user edit;
            # otherwise a new device would report a false conflict before it
            # can hydrate the confirmed draft from the server.
            ui_state_path = self._service._ui_state_path()
            draft_state = self._service._read_ui_state() if ui_state_path.exists() else None
            draft = draft_state.get("draft_plan") if isinstance(draft_state, dict) else None
            if isinstance(draft, dict) and draft.get("revision_id"):
                draft_key = "__account_draft__"
                draft_id = str(draft["revision_id"])
                revisions = draft_state.get("revisions") or []
                revision = draft_state.get("draft_revision", max(1, len(revisions)))
                if type(revision) is not int or revision < 1:
                    revision = max(1, len(revisions))
                snapshot = dict(draft)
                confirmation = draft_state.get("draft_confirmation")
                if isinstance(confirmation, dict):
                    snapshot["draft_confirmation"] = dict(confirmation)
                values = self._learning_wire("draft_plan", draft_key, {
                    "draft_id": draft_id, "revision": revision,
                    "status": draft.get("status"), "snapshot": snapshot,
                    "confirmed": bool(draft.get("confirmed")),
                    "updated_at": draft.get("updated_at") or _now(),
                    "idempotency_key": f"draft:{draft_id}:{revision}",
                })
                # ``draft_id`` is the revision inside the aggregate.  The
                # sync entity itself must stay stable across edits so CAS can
                # compare revision N with revision N+1 instead of creating a
                # new remote row for every draft revision.
                record_id = _record_key(draft_key, "current")
                result[("draft_plan", record_id)] = self._validate_record("draft_plan", record_id, values)
        except (AttributeError, PlanError, SyncRecordError):
            # A malformed optional draft must not prevent plan/task sync; the
            # planning page will surface its own local validation error.
            pass
        for row in self._rows(plans, "SELECT plan_key,mission_id,payload FROM daily_missions ORDER BY day_date,mission_id"):
            raw = _decode_json(row[2], "daily_mission.payload")
            values = self._learning_wire("daily_mission", row[0], raw)
            record_id = _record_key(row[0], values.get("mission_id"))
            result[("daily_mission", record_id)] = self._validate_record("daily_mission", record_id, values)
        # Domain rows are the local source of truth for facts written by the
        # classroom.  They are projected after the restore cache so a local
        # edit wins over an older cached copy and is uploaded on the next
        # sync.  Missing tables are tolerated for pre-feature databases.
        for row in self._rows(classroom, "SELECT plan_key, payload FROM learning_contracts ORDER BY rowid"):
            raw = _decode_json(row[1], "learning_contract.payload")
            values = self._learning_wire("learning_contract", row[0], raw)
            record_id = _record_key(row[0], values.get("contract_id"))
            result[("learning_contract", record_id)] = self._validate_record("learning_contract", record_id, values)
        for row in self._rows(classroom, "SELECT plan_key, payload FROM evidence_events ORDER BY rowid"):
            raw = _decode_json(row[1], "learning.evidence.payload")
            values = self._learning_wire("evidence", row[0], raw)
            record_id = _record_key(row[0], values.get("evidence_id"))
            result[("evidence", record_id)] = self._validate_record("evidence", record_id, values)
        for row in self._rows(classroom, "SELECT plan_key, payload FROM error_episodes ORDER BY rowid"):
            raw = _decode_json(row[1], "learning.error.payload")
            values = self._learning_wire("error", row[0], raw)
            record_id = _record_key(row[0], values.get("error_id"))
            result[("error", record_id)] = self._validate_record("error", record_id, values)
        for row in self._rows(classroom, "SELECT plan_key, payload FROM verification_items ORDER BY rowid"):
            raw = _decode_json(row[1], "learning.verification.payload")
            values = self._learning_wire("verification", row[0], raw)
            record_id = _record_key(row[0], values.get("verification_id"))
            result[("verification", record_id)] = self._validate_record("verification", record_id, values)
        for row in self._rows(classroom, "SELECT plan_key, contract_id, payload, updated_at FROM evaluation_results ORDER BY updated_at, contract_id"):
            raw = _decode_json(row[2], "learning.evaluation.payload")
            if not isinstance(raw, dict):
                raise SyncRecordError("SYNC_LOCAL_INVALID", "评估结果内容无效。")
            raw = dict(raw)
            raw["verification_id"] = f"evaluation:{row[1]}"
            raw["contract_id"] = row[1]
            raw["created_at"] = raw.get("created_at") or row[3]
            values = self._learning_wire("verification", row[0], raw)
            record_id = _record_key(row[0], values.get("verification_id"))
            result[("verification", record_id)] = self._validate_record("verification", record_id, values)
        for row in self._rows(classroom, "SELECT plan_key, payload FROM tutor_checkpoints ORDER BY rowid"):
            raw = _decode_json(row[1], "learning.tutor_checkpoint.payload")
            values = self._learning_wire("tutor_checkpoint", row[0], raw)
            record_id = _record_key(row[0], values.get("checkpoint_id"))
            result[("tutor_checkpoint", record_id)] = self._validate_record("tutor_checkpoint", record_id, values)
        return result

    def _staged(self, plans: sqlite3.Connection) -> dict[tuple[str, str], tuple[dict[str, Any], Any]]:
        staged = {}
        for row in self._rows(plans, "SELECT record_type,record_id,values_json,expected_local_json FROM cloud_records"):
            values = self._validate_record(row[0], row[1], _decode_json(row[2], "cloud.values"))
            expected = None if row[3] is None else _decode_json(row[3], "cloud.expected_local")
            if expected is not None:
                expected = self._validate_record(row[0], row[1], expected)
            staged[(row[0], row[1])] = (values, expected)
        return staged

    def _visible(self, base, staged):
        result = dict(base)
        for key, (values, expected) in staged.items():
            if _same(base.get(key), expected):
                result[key] = _clone(values)
        return result

    def _historical_dependencies(self):
        catalog = getattr(self._service, "_legacy", None)
        if catalog is None:
            return frozenset()
        result = set()
        for plan in getattr(catalog, "plans", ()):
            plan_key = getattr(plan, "key", None)
            for task in getattr(plan, "tasks", ()):
                task_id = task.get("id") if isinstance(task, dict) else None
                if isinstance(plan_key, str) and isinstance(task_id, str):
                    result.add((plan_key, task_id))
        return frozenset(result)

    def _has_dependency(self, records, plan_key: str, task_id: str, historical) -> bool:
        try:
            task_key = ("task", _record_key(plan_key, task_id))
        except SyncRecordError:
            return False
        plan = records.get(("plan", plan_key))
        task = records.get(task_key)
        if (plan is not None and task is not None
                and task.get("plan_key") == plan_key
                and task.get("task", {}).get("id") == task_id
                and task_id in plan.get("task_ids", ())):
            return True
        return (plan_key, task_id) in historical

    def _filter_dependent(self, records, staged=()):
        historical = self._historical_dependencies()
        staged_values = {
            key: pair[0] for key, pair in staged.items()
        } if isinstance(staged, dict) else {key: None for key in staged}
        result = {}
        for key, values in records.items():
            if key[0] in {"progress", "turn", "note", "receipt", "plan_revision", "learning_contract", "evidence", "error", "verification", "tutor_checkpoint", "daily_mission", "draft_plan"}:
                if key[0] == "draft_plan":
                    result[key] = values
                    continue
                # A cloud value may be visible to the engine while it is
                # staged, even before its complete plan is published.  It
                # remains in cloud_records until the dependency gate below
                # permits publication; base orphan records stay hidden and
                # are therefore never newly uploaded.
                if (key in staged_values
                        and (staged_values[key] is None
                             or _same(values, staged_values[key]))):
                    result[key] = values
                    continue
                plan_key, task_id = values.get("plan_key"), values.get("task_id")
                if key[0] in {"plan_revision", "learning_contract", "evidence", "error", "verification", "tutor_checkpoint", "daily_mission"}:
                    if not self._has_plan(records, plan_key, historical):
                        continue
                elif not self._has_dependency(records, plan_key, task_id, historical):
                    continue
            result[key] = values
        return result

    def _has_plan(self, records, plan_key: str, historical) -> bool:
        if not isinstance(plan_key, str):
            return False
        if ("plan", plan_key) in records:
            return True
        return any(item[0] == plan_key for item in historical)

    @contextmanager
    def _databases(self):
        with self._locked():
            self._check_scope()
            with ExitStack() as stack:
                plans = stack.enter_context(self._repository.connection())
                self._ensure_tables(plans)
                classroom = stack.enter_context(self._classroom.connection())
                # Notes/turn writers do not take the PlanService lock. Hold a
                # SQLite write transaction from CAS read through publication.
                classroom.execute("BEGIN IMMEDIATE")
                yield plans, classroom
                self._check_scope()

    @contextmanager
    def _read_databases(self):
        with self._locked():
            self._check_scope()
            with ExitStack() as stack:
                plans = stack.enter_context(
                    self._read_connection(self._repository.root / "plans.sqlite3"))
                self._verify_read_owner(plans)
                classroom = stack.enter_context(
                    self._read_connection(self._classroom.path))
                yield plans, classroom
                self._check_scope()

    def scan(self) -> dict[tuple[str, str], dict[str, Any]]:
        with self._read_databases() as (plans, classroom):
            base = self._base_records(plans, classroom)
            staged = self._staged(plans)
            return self._filter_dependent(self._visible(base, staged), staged)

    def apply(self, record_type: str, record_id: str, values: dict[str, Any], expected_local: dict[str, Any] | None) -> bool:
        normalized = self._validate_record(record_type, record_id, values)
        if expected_local is not None:
            expected_local = self._validate_record(record_type, record_id, expected_local)
        if record_type == "resource":
            with self._locked():
                self._check_scope()
                current = {item["material_id"]: item for item in self._materials.sync_metadata()}.get(record_id)
                if (current is not None and expected_local is None) or current != expected_local:
                    return False
                try:
                    self._materials.apply_sync_metadata(normalized)
                except MaterialError as exc:
                    raise SyncRecordError("SYNC_LOCAL_INVALID", str(exc)) from exc
                self._check_scope()
                return True
        if record_type in {"tool_activity", "chat_session", "research_source"}:
            # These records are already atomic JSON/domain files.  Apply them
            # directly after a local CAS check; staging them in plans.sqlite3
            # would make a successful sync invisible until a later page open.
            with self._locked():
                self._check_scope()
                current = self._generic_records().get((record_type, record_id))
                if current != expected_local:
                    return False
                try:
                    if record_type == "tool_activity":
                        kind = normalized["kind"]
                        self._tools.open()
                        atomic_json(self._tools.path(kind, record_id), normalized["payload"])
                    elif record_type == "chat_session":
                        session = AtomicJsonFreeChatStore._decode(
                            normalized["payload"], expected_session_id=record_id)
                        self._chat._atomic_write(session)
                    else:
                        sources = dict(getattr(self._service, "_research_sources", {}) or {})
                        sources[record_id] = dict(normalized["payload"])
                        self._service._write_research_sources(sources)
                        self._service._research_sources = sources
                except Exception as exc:
                    raise SyncRecordError("SYNC_LOCAL_INVALID", "云端账号数据无法恢复到本机。") from exc
                self._check_scope()
                return True
        if record_type == "setting":
            with self._locked():
                self._check_scope()
                current = {"key": "preferences", "values": self._preferences.load()}
                if current != expected_local:
                    return False
                try:
                    self._preferences.save(normalized["values"])
                except Exception as exc:
                    raise SyncRecordError("SYNC_LOCAL_INVALID", "云端偏好设置无法恢复。") from exc
                return True
        with self._databases() as (plans, classroom):
            base = self._base_records(plans, classroom)
            if not _same(base.get((record_type, record_id)), expected_local):
                return False
            plans.execute("""INSERT INTO cloud_records(record_type,record_id,values_json,expected_local_json,staged_at)
                VALUES(?,?,?,?,?) ON CONFLICT(record_type,record_id) DO UPDATE SET
                values_json=excluded.values_json,expected_local_json=excluded.expected_local_json,staged_at=excluded.staged_at""",
                (record_type, record_id, _json(normalized), None if expected_local is None else _json(expected_local), _now()))
            return True

    def preserve_conflict(self, record_type: str, record_id: str,
                          local: dict[str, Any] | None, remote: dict[str, Any] | None) -> None:
        if local is not None:
            local = self._validate_record(record_type, record_id, local)
        if remote is not None:
            remote = self._validate_record(record_type, record_id, remote)
        with self._databases() as (plans, _classroom):
            plans.execute("INSERT INTO sync_conflicts(record_type,record_id,local_json,remote_json,created_at) VALUES(?,?,?,?,?)",
                          (record_type, record_id, None if local is None else _json(local),
                           None if remote is None else _json(remote), _now()))

    def conflicts(self) -> list[dict[str, Any]]:
        with self._read_databases() as (plans, _classroom):
            result = []
            for row in self._rows(plans, "SELECT conflict_id,record_type,record_id,local_json,remote_json,created_at FROM sync_conflicts ORDER BY conflict_id"):
                result.append({"conflict_id": row[0], "record_type": row[1], "record_id": row[2],
                               "local": None if row[3] is None else _decode_json(row[3], "conflict.local"),
                               "remote": None if row[4] is None else _decode_json(row[4], "conflict.remote"),
                               "created_at": row[5]})
            return result

    def _publish_plan(self, plans, base, staged, plan_key: str) -> tuple[bool, int]:
        header_stage = staged.get(("plan", plan_key))
        current_header = base.get(("plan", plan_key))
        header = header_stage[0] if header_stage else current_header
        if header is None:
            return False, 0
        if header_stage and not _same(current_header, header_stage[1]):
            return False, 0
        task_values = []
        for task_id in header["task_ids"]:
            key = ("task", _record_key(plan_key, task_id))
            staged_task = staged.get(key)
            if staged_task and not _same(base.get(key), staged_task[1]):
                # A local task edit happened after staging.  Leave the cloud
                # copy pending so the engine can surface a three-way conflict.
                return False, 0
            value = staged_task[0] if staged_task else base.get(key)
            if value is None:
                return False, 0
            task_values.append(value["task"])
        plan = self._plan_from_header(header, task_values)
        body = encode(asdict(plan))
        revision = int((plan.details or {}).get("plan_revision") or 1)
        existing = plans.execute("SELECT 1 FROM plans WHERE plan_key=?", (plan.key,)).fetchone()
        if existing:
            plans.execute("UPDATE plans SET plan_id=?,body=?,revision=? WHERE plan_key=?", (plan.plan_id, body, revision, plan.key))
        else:
            plans.execute("INSERT INTO plans(plan_key,plan_id,body,revision) VALUES(?,?,?,?)", (plan.key, plan.plan_id, body, revision))
        plans.execute("CREATE TABLE IF NOT EXISTS learning_records(record_type TEXT NOT NULL, record_id TEXT NOT NULL, values_json TEXT NOT NULL, PRIMARY KEY(record_type,record_id))")
        from .planning.storage import PlanRepository
        PlanRepository._write_plan_revision(plans, plan)
        # Keep an existing user choice untouched; a device with no local
        # selection needs one complete hydrated plan to make it usable.
        selected = plans.execute("SELECT plan_key FROM selection WHERE singleton=1").fetchone()
        if (details_tombstoned(plan) and selected and selected[0] == plan.key):
            plans.execute("DELETE FROM selection WHERE singleton=1")
            for candidate in plans.execute("SELECT plan_key,body FROM plans WHERE plan_key<>? ORDER BY rowid DESC", (plan.key,)):
                candidate_data = json.loads(candidate[1])
                if not details_tombstoned_data(candidate_data.get("details")):
                    plans.execute("INSERT INTO selection(singleton,plan_key) VALUES(1,?)", (candidate[0],))
                    break
        elif selected is None:
            plans.execute("INSERT INTO selection(singleton,plan_key) VALUES(1,?)", (plan.key,))
        base[("plan", plan.key)] = self._plan_header(plan)
        for task in plan.tasks:
            base[("task", _record_key(plan.key, task["id"]))] = self._validate_task(
                _record_key(plan.key, task["id"]), {"plan_key": plan.key, "task": dict(task)})
        plans.execute("DELETE FROM cloud_records WHERE record_type='plan' AND record_id=?", (plan.key,))
        for task_id in header["task_ids"]:
            plans.execute("DELETE FROM cloud_records WHERE record_type='task' AND record_id=?", (_record_key(plan.key, task_id),))
        return True, len(plan.tasks)

    def _publish_classroom(self, classroom, values: dict[str, Any]) -> None:
        record_type = values.pop("_record_type")
        if record_type == "turn":
            classroom.execute("""INSERT OR REPLACE INTO turns(
                    id,plan_key,task_id,action,question,answer,state,created_at,decision_json)
                VALUES(?,?,?,?,?,?,?,?,?)""", tuple(
                    _json(values["decision_json"]) if key == "decision_json" else values[key]
                    for key in ("id", "plan_key", "task_id", "action", "question", "answer", "state", "created_at", "decision_json")))
        elif record_type == "note":
            classroom.execute("INSERT OR REPLACE INTO notes(plan_key,task_id,text,revision) VALUES(?,?,?,?)",
                              (values["plan_key"], values["task_id"], values["text"], values["revision"]))
        elif record_type == "receipt":
            classroom.execute("""INSERT OR REPLACE INTO receipts(id,plan_key,task_id,payload,created_at)
                VALUES(?,?,?,?,?)""", (values["id"], values["plan_key"], values["task_id"], _json(values["payload"]), values["created_at"]))

    def _publish_learning_record(self, plans, classroom, record_type: str, values: dict[str, Any]) -> None:
        """Persist a restored learning fact in both sync cache and domain DB."""
        plan_key = values["plan_key"]
        record_id = values.get({
            "learning_contract": "contract_id",
            "evidence": "evidence_id",
            "error": "error_id",
            "verification": "verification_id",
            "tutor_checkpoint": "checkpoint_id",
            "draft_plan": "draft_id",
        }.get(record_type, ""))
        if not isinstance(record_id, str) or not record_id:
            raise SyncRecordError("SYNC_LOCAL_INVALID", "学习记录标识无效。")
        storage_record_id = "current" if record_type == "draft_plan" else record_id
        if record_type == "draft_plan":
            # Remove revision-keyed rows from pre-stable-aggregate builds and
            # keep exactly one current DraftPlan projection.
            plans.execute("DELETE FROM learning_records WHERE record_type=?", (record_type,))
        plans.execute(
            "INSERT OR REPLACE INTO learning_records(record_type,record_id,values_json) VALUES(?,?,?)",
            (record_type, _record_key(plan_key, storage_record_id), _json(values)),
        )
        if record_type == "draft_plan":
            state = self._service._read_ui_state()
            snapshot = dict(values["snapshot"])
            confirmation = snapshot.pop("draft_confirmation", None)
            state["draft_plan"] = snapshot
            state["draft_revision"] = int(values["revision"])
            state["draft_confirmation"] = confirmation if isinstance(confirmation, dict) else None
            self._service._write_ui_state(state)
            return
        plan_row = plans.execute("SELECT body FROM plans WHERE plan_key=?", (plan_key,)).fetchone()
        plan_id = None
        if plan_row:
            try:
                plan_id = json.loads(plan_row[0]).get("plan_id")
            except (TypeError, ValueError, json.JSONDecodeError):
                plan_id = None
        payload = self._domain_learning_payload(record_type, values, plan_id=plan_id)
        if record_type == "learning_contract":
            classroom.execute(
                "INSERT OR REPLACE INTO learning_contracts(contract_id,plan_key,payload,created_at) VALUES(?,?,?,?)",
                (record_id, plan_key, _json(payload), values["created_at"]),
            )
        elif record_type == "evidence":
            classroom.execute(
                "INSERT OR REPLACE INTO evidence_events(evidence_id,plan_key,contract_id,payload,created_at) VALUES(?,?,?,?,?)",
                (record_id, plan_key, values["contract_id"], _json(payload), values["created_at"]),
            )
        elif record_type == "error":
            classroom.execute(
                "INSERT OR REPLACE INTO error_episodes(error_episode_id,plan_key,contract_id,payload,created_at) VALUES(?,?,?,?,?)",
                (record_id, plan_key, values["contract_id"], _json(payload), values["created_at"]),
            )
        elif record_type == "verification":
            if "objective_id" in values:
                classroom.execute(
                    "INSERT OR REPLACE INTO verification_items(verification_id,plan_key,contract_id,payload,status,created_at) VALUES(?,?,?,?,?,?)",
                    (record_id, plan_key, values["contract_id"], _json(payload), values["status"], values["created_at"]),
                )
            else:
                classroom.execute(
                    "INSERT OR REPLACE INTO evaluation_results(plan_key,contract_id,payload,updated_at) VALUES(?,?,?,?)",
                    (plan_key, values["contract_id"], _json(payload), values["created_at"]),
                )
        elif record_type == "tutor_checkpoint":
            classroom.execute(
                "INSERT OR REPLACE INTO tutor_checkpoints(checkpoint_id,plan_key,contract_id,payload,created_at) VALUES(?,?,?,?,?)",
                (record_id, plan_key, values["contract_id"], _json(payload), values["created_at"]),
            )
    def finalize(self) -> dict[str, Any]:
        stats = {"published_plans": 0, "published_tasks": 0, "applied": 0,
                 "pending": 0, "conflicts": len(self.conflicts()), "staged": 0}
        with self._databases() as (plans, classroom):
            base = self._base_records(plans, classroom)
            staged = self._staged(plans)
            # A crash can commit classroom.sqlite3 before plans.sqlite3's
            # staging cleanup. The replay is already durable, not a conflict.
            for key, (values, _expected) in tuple(staged.items()):
                if base.get(key) == values:
                    plans.execute("DELETE FROM cloud_records WHERE record_type=? AND record_id=?", key)
            staged = self._staged(plans)
            stats["staged"] = len(staged)
            candidates = {key[1] for key in staged if key[0] == "plan"}
            candidates.update(value[0]["plan_key"] for key, value in staged.items()
                             if key[0] == "task")
            for plan_key in sorted(candidates):
                published, task_count = self._publish_plan(plans, base, staged, plan_key)
                if published:
                    stats["published_plans"] += 1
                    stats["published_tasks"] += task_count
                    stats["applied"] += 1 + task_count
                    staged = self._staged(plans)
            for key, (values, expected) in list(staged.items()):
                if key[0] == "progress":
                    if (not _same(base.get(key), expected)
                            or not self._has_dependency(
                                base, values["plan_key"], values["task_id"],
                                self._historical_dependencies())):
                        continue
                    plans.execute("INSERT OR REPLACE INTO progress(plan_key,task_id,payload) VALUES(?,?,?)",
                                  (values["plan_key"], values["task_id"], _json(values["payload"])))
                    base[key] = values
                    plans.execute("DELETE FROM cloud_records WHERE record_type=? AND record_id=?", key)
                    stats["applied"] += 1
            staged = self._staged(plans)
            classroom_values = {"turn", "note", "receipt"}
            for key, (values, expected) in list(staged.items()):
                if (key[0] not in classroom_values
                        or not _same(base.get(key), expected)
                        or not self._has_dependency(
                            base, values["plan_key"], values["task_id"],
                            self._historical_dependencies())):
                    continue
                write_values = dict(values)
                write_values["_record_type"] = key[0]
                self._publish_classroom(classroom, write_values)
                base[key] = values
                plans.execute("DELETE FROM cloud_records WHERE record_type=? AND record_id=?", key)
                stats["applied"] += 1
            # Plan revision and learner evidence are small immutable facts.
            # Keep them in the account-owned plans database and hydrate the
            # classroom domain tables so a second device can use them without
            # knowing about the transport cache.
            for key, (values, expected) in list(staged.items()):
                if key[0] not in {"plan_revision", "learning_contract", "evidence", "error", "verification", "tutor_checkpoint", "daily_mission", "draft_plan"}:
                    continue
                if (not _same(base.get(key), expected)
                        or (key[0] != "draft_plan"
                            and not self._has_plan(base, values["plan_key"], self._historical_dependencies()))):
                    continue
                if key[0] == "draft_plan":
                    self._publish_learning_record(plans, classroom, key[0], values)
                elif key[0] == "daily_mission":
                    existing = plans.execute("SELECT payload,revision FROM daily_missions WHERE plan_key=? AND mission_id=?",
                                             (values["plan_key"], values["mission_id"])).fetchone()
                    current_revision = int(existing[1]) if existing else 0
                    if existing and existing[0] != _json(values) and expected is not None:
                        # The sync engine already fenced the server entity. A
                        # local row changed after staging still requires a
                        # fresh user-visible conflict instead of overwriting it.
                        continue
                    plans.execute("INSERT OR REPLACE INTO daily_missions(mission_id,plan_key,day_date,payload,revision,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                  (values["mission_id"], values["plan_key"], values["day_date"], _json(values),
                                   max(1, current_revision), values["idempotency_key"], values["started_at"], values["updated_at"]))
                elif key[0] == "plan_revision":
                    plans.execute("INSERT OR REPLACE INTO learning_records(record_type,record_id,values_json) VALUES(?,?,?)",
                                  (key[0], key[1], _json(values)))
                else:
                    self._publish_learning_record(plans, classroom, key[0], values)
                base[key] = values
                plans.execute("DELETE FROM cloud_records WHERE record_type=? AND record_id=?", key)
                stats["applied"] += 1
            staged_row = plans.execute("SELECT count(*) FROM cloud_records").fetchone()
            stats["pending"] = staged_row[0]
        return stats
