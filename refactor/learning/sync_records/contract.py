"""Strict wire contract for new learning records.

The existing structured-sync endpoint also carries legacy ``product_record``
payloads.  These records intentionally use a separate envelope and namespaced
entity IDs so that the refactor never overwrites or masquerades as legacy
product data.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCHEMA = "mentorflow.refactor-sync.v1"
VERSION = 1
MAX_RECORD_BYTES = 256 * 1024
_ENTITY_PREFIX = "refactor.v1:"
_ID_HASH = re.compile(r"[0-9a-f]{64}\Z")

RECORD_TYPES = frozenset({
    "plan", "task", "progress", "turn", "note", "receipt", "resource",
    # Durable learning facts are deliberately separate from task progress.
    # They are account/plan scoped and can be added without changing the
    # legacy product-record envelope.
    "plan_revision", "learning_contract", "evidence", "error", "verification",
    "tutor_checkpoint", "daily_mission", "draft_plan",
    "setting",
    # Account-owned tool/chat/research state follows the same authenticated
    # structured-sync scope and CAS ledger as plans and classroom records.
    "tool_activity", "chat_session", "research_source",
})
_KIND_BY_RECORD = {
    "plan": "learning_run",
    "task": "task",
    "progress": "task",
    "turn": "history",
    "note": "history",
    "receipt": "history",
    # Resource records contain metadata plus a bounded body excerpt.  The
    # server's existing resource kind is intentionally reused; source bytes
    # and local paths never enter this envelope.
    "resource": "resource",
    "plan_revision": "learning_run",
    "learning_contract": "learning_run",
    "evidence": "history",
    "error": "history",
    "verification": "history",
    "tutor_checkpoint": "learning_run",
    "daily_mission": "learning_run",
    "draft_plan": "learning_run",
    "setting": "setting",
    "tool_activity": "history",
    "chat_session": "history",
    "research_source": "history",
}
_SECRET_KEYS = frozenset(
    {"password", "access_token", "refresh_token", "token", "credential",
     "api_key", "provider_secret", "private_key", "signing_key", "secret"}
)
_SECRET_PARTS = ("password", "token", "secret", "credential", "private_key")


class SyncRecordError(ValueError):
    """A public, non-sensitive local contract error."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RefactorRecord:
    record_type: str
    record_id: str
    values: dict[str, Any]
    entity_id: str
    kind: str
    payload: dict[str, Any]


def _record_type(value: Any) -> str:
    if not isinstance(value, str) or value not in RECORD_TYPES:
        raise SyncRecordError("SYNC_RECORD_TYPE_INVALID", "学习记录类型无效。")
    return value


def _record_id(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 512
            or any(ord(char) < 32 for char in value)):
        raise SyncRecordError("SYNC_RECORD_ID_INVALID", "学习记录标识无效。")
    return value


def _check_values(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SyncRecordError("SYNC_RECORD_VALUES_INVALID", "学习记录内容必须是对象。")

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise SyncRecordError("SYNC_RECORD_VALUES_INVALID", "学习记录字段名无效。")
                lowered = key.lower()
                if (lowered == "product_record" or lowered in _SECRET_KEYS
                        or any(part in lowered for part in _SECRET_PARTS)):
                    raise SyncRecordError("SYNC_SECRET_FIELD_REJECTED", "学习记录含有不可同步字段。")
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    try:
        # The server's untyped product-contract path serializes with escaped
        # non-ASCII, so this conservative form matches its byte accounting.
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False,
                             sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError):
        raise SyncRecordError("SYNC_RECORD_VALUES_INVALID", "学习记录内容不能安全序列化。") from None
    if len(encoded.encode("utf-8")) >= MAX_RECORD_BYTES:
        raise SyncRecordError("SYNC_RECORD_TOO_LARGE", "单条学习记录超过同步容量。")
    return value


def _encoded_payload(payload: dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False,
                             sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError):
        raise SyncRecordError("SYNC_RECORD_VALUES_INVALID", "学习记录不能安全序列化。") from None
    raw = encoded.encode("utf-8")
    if len(raw) >= MAX_RECORD_BYTES:
        raise SyncRecordError("SYNC_RECORD_TOO_LARGE", "单条学习记录超过同步容量。")
    return raw


def entity_id_for(record_type: str, record_id: str) -> str:
    record_type = _record_type(record_type)
    record_id = _record_id(record_id)
    digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    return f"{_ENTITY_PREFIX}{record_type}:{digest}"


def kind_for(record_type: str) -> str:
    record_type = _record_type(record_type)
    return _KIND_BY_RECORD[record_type]


def _parse_entity_id(entity_id: Any) -> tuple[str, str]:
    if not isinstance(entity_id, str):
        raise SyncRecordError("SYNC_ENTITY_INVALID", "同步实体标识无效。")
    parts = entity_id.split(":", 2)
    if len(parts) != 3 or f"{parts[0]}:" != _ENTITY_PREFIX:
        raise SyncRecordError("SYNC_ENTITY_INVALID", "同步实体标识无效。")
    record_type = _record_type(parts[1])
    if not _ID_HASH.fullmatch(parts[2]):
        raise SyncRecordError("SYNC_ENTITY_INVALID", "同步实体标识无效。")
    return record_type, parts[2]


def envelope_for(record_type: str, record_id: str, values: dict[str, Any]) -> dict[str, Any]:
    record_type = _record_type(record_type)
    record_id = _record_id(record_id)
    values = _check_values(values)
    payload = {
        "schema": SCHEMA,
        "version": VERSION,
        "record_type": record_type,
        "record_id": record_id,
        "values": values,
    }
    _encoded_payload(payload)
    return payload


def parse_envelope(payload: Any, *, expected_type: str | None = None,
                   expected_id: str | None = None) -> RefactorRecord:
    if not isinstance(payload, dict) or set(payload) != {
            "schema", "version", "record_type", "record_id", "values"}:
        raise SyncRecordError("SYNC_ENVELOPE_INVALID", "学习记录同步封装无效。")
    if payload.get("schema") != SCHEMA or type(payload.get("version")) is not int or payload["version"] != VERSION:
        raise SyncRecordError("SYNC_ENVELOPE_INVALID", "学习记录同步版本不受支持。")
    record_type = _record_type(payload.get("record_type"))
    record_id = _record_id(payload.get("record_id"))
    if expected_type is not None and record_type != expected_type:
        raise SyncRecordError("SYNC_ENVELOPE_INVALID", "学习记录类型与实体不一致。")
    if expected_id is not None and record_id != expected_id:
        raise SyncRecordError("SYNC_ENVELOPE_INVALID", "学习记录标识与实体不一致。")
    values = _check_values(payload.get("values"))
    entity_id = entity_id_for(record_type, record_id)
    # Rebuild the object so a caller cannot mutate a server-owned envelope
    # while the engine is comparing it with its ledger.
    normalized = envelope_for(record_type, record_id, values)
    _encoded_payload(normalized)
    return RefactorRecord(record_type, record_id, values, entity_id,
                          kind_for(record_type), normalized)


def parse_entity_identity(entity_id: str) -> tuple[str, str]:
    """Return ``(record_type, hash)`` for a namespaced entity ID."""
    return _parse_entity_id(entity_id)


def is_refactor_entity(entity_id: Any) -> bool:
    return isinstance(entity_id, str) and entity_id.startswith(_ENTITY_PREFIX)


# These validators are intentionally small and transport-facing.  They keep
# the first cross-device version useful without making the sync layer own the
# planner or classroom state machines.
_DEPTHS = frozenset({"Recognize", "Understand", "Use", "Transfer", "DeepMastery"})
_EVIDENCE_TYPES = frozenset({"explanation", "comparison", "diagram", "answer", "implementation", "repository_trace", "test_result", "debugging", "design", "code_review", "teaching", "transfer_task", "self_report"})
_EVIDENCE_SOURCES = frozenset({"student_attempt", "tool_result", "self_report"})
_INDEPENDENCE = frozenset({"independent", "light_hint", "strong_hint", "demonstrated_by_tutor", "self_report"})
_EVALUATOR = frozenset({"supported", "supported_with_help", "contradicted", "insufficient"})
_CONFIDENCE = frozenset({"low", "medium", "high"})
_ERROR_CLASSES = frozenset({"slip", "knowledge_gap", "misconception_candidate", "missing_prerequisite", "transfer_failure", "uncertain"})
_ERROR_CONTEXTS = frozenset({"diagnosis", "guided_practice", "independent_application", "verification", "transfer"})
_HINT_LEVELS = frozenset({"none", "light_hint", "strong_hint"})
_VERDICTS = frozenset({"PASS", "UNCERTAIN", "FAIL"})
_ACTIONS = frozenset({"continue", "complete", "complete_with_verification_debt", "recheck", "remediate"})
_VERIFICATION_REASONS = frozenset({"repeated_error", "strong_hint_dependency", "active_misconception", "core_prerequisite", "insufficient_evidence", "transfer_failure", "stale_evidence"})
_VERIFICATION_PRIORITIES = frozenset({"low", "normal", "medium", "high"})
_VERIFICATION_STATUSES = frozenset({"pending", "in_progress", "passed", "failed", "resolved", "queued", "scheduled", "verifying", "uncertain", "remediation_required"})


def _state_key(plan_key: Any, child: Any, record_id: Any) -> None:
    if not isinstance(plan_key, str) or not plan_key.strip() or not isinstance(child, str) or not child.strip():
        raise SyncRecordError("SYNC_STATE_INVALID", "学习状态缺少账号计划标识。")
    expected = json.dumps([plan_key, child], ensure_ascii=False, separators=(",", ":"))
    if record_id != expected:
        raise SyncRecordError("SYNC_STATE_INVALID", "学习状态标识与计划内容不一致。")


def _state_text(value: Any, name: str, limit: int = 8000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SyncRecordError("SYNC_STATE_INVALID", f"学习状态字段无效：{name}。")


def _state_list(value: Any, name: str, *, max_items: int = 100, strings: bool = True) -> None:
    if not isinstance(value, list) or len(value) > max_items:
        raise SyncRecordError("SYNC_STATE_INVALID", f"学习状态字段无效：{name}。")
    if strings and any(not isinstance(item, str) or not item.strip() for item in value):
        raise SyncRecordError("SYNC_STATE_INVALID", f"学习状态字段无效：{name}。")


def validate_learning_record(record_type: str, record_id: str, values: Any) -> dict[str, Any]:
    """Validate account/plan learning facts before local staging or upload."""
    if record_type not in {"plan_revision", "learning_contract", "evidence", "error", "verification", "tutor_checkpoint", "daily_mission", "draft_plan"}:
        raise SyncRecordError("SYNC_STATE_INVALID", "学习状态记录类型无效。")
    if not isinstance(values, dict):
        raise SyncRecordError("SYNC_STATE_INVALID", "学习状态记录必须是对象。")
    if record_type == "plan_revision":
        if set(values) != {"plan_key", "plan_id", "revision", "status", "snapshot", "created_at"}:
            raise SyncRecordError("SYNC_STATE_INVALID", "计划 revision 字段不完整。")
        _state_key(values["plan_key"], values["plan_id"], record_id)
        _state_text(values["plan_id"], "plan_id", 512)
        if type(values["revision"]) is not int or values["revision"] < 1:
            raise SyncRecordError("SYNC_STATE_INVALID", "计划 revision 无效。")
        if values["status"] not in {"draft", "active", "archived"} or not isinstance(values["snapshot"], dict):
            raise SyncRecordError("SYNC_STATE_INVALID", "计划 revision 内容无效。")
        _state_text(values["created_at"], "created_at", 128)
    elif record_type == "learning_contract":
        fields = {"plan_key", "contract_id", "plan_revision", "milestone_id", "competency_ids", "title", "purpose", "target_depth", "visible_objectives", "required_evidence", "resource_refs", "estimated_minutes", "created_at"}
        if set(values) != fields:
            raise SyncRecordError("SYNC_STATE_INVALID", "学习契约字段不完整。")
        _state_key(values["plan_key"], values["contract_id"], record_id)
        _state_text(values["milestone_id"], "milestone_id", 512); _state_text(values["title"], "title"); _state_text(values["purpose"], "purpose")
        if type(values["plan_revision"]) is not int or values["plan_revision"] < 1 or values["target_depth"] not in _DEPTHS:
            raise SyncRecordError("SYNC_STATE_INVALID", "学习契约版本或深度无效。")
        _state_list(values["competency_ids"], "competency_ids"); _state_list(values["resource_refs"], "resource_refs")
        _state_list(values["visible_objectives"], "visible_objectives", max_items=50, strings=False)
        if any(not isinstance(item, dict) or set(item) - {"id", "text", "blocking"} or not isinstance(item.get("id"), str) or not isinstance(item.get("text"), str) or type(item.get("blocking")) is not bool for item in values["visible_objectives"]):
            raise SyncRecordError("SYNC_STATE_INVALID", "学习契约目标无效。")
        _state_list(values["required_evidence"], "required_evidence", max_items=100, strings=False)
        if any(not isinstance(item, dict) or not isinstance(item.get("objective_id"), str) or not isinstance(item.get("acceptable_types"), list) or any(t not in _EVIDENCE_TYPES for t in item["acceptable_types"]) or item.get("independence_required") not in {"none", "light", "strict"} for item in values["required_evidence"]):
            raise SyncRecordError("SYNC_STATE_INVALID", "学习契约证据要求无效。")
        if type(values["estimated_minutes"]) is not int or not 1 <= values["estimated_minutes"] <= 100000:
            raise SyncRecordError("SYNC_STATE_INVALID", "学习契约预计时长无效。")
        _state_text(values["created_at"], "created_at", 128)
    elif record_type == "evidence":
        fields = {"plan_key", "evidence_id", "contract_id", "objective_ids", "competency_ids", "type", "source", "summary", "independence", "evaluator_result", "confidence", "created_at"}
        extended_fields = fields | {"session_id", "artifact_refs"}
        if set(values) not in (fields, extended_fields):
            raise SyncRecordError("SYNC_STATE_INVALID", "证据记录字段不完整。")
        _state_key(values["plan_key"], values["evidence_id"], record_id); _state_text(values["contract_id"], "contract_id", 512); _state_text(values["summary"], "summary")
        _state_list(values["objective_ids"], "objective_ids"); _state_list(values["competency_ids"], "competency_ids")
        if values["type"] not in _EVIDENCE_TYPES or values["source"] not in _EVIDENCE_SOURCES or values["independence"] not in _INDEPENDENCE or values["evaluator_result"] not in _EVALUATOR or values["confidence"] not in _CONFIDENCE:
            raise SyncRecordError("SYNC_STATE_INVALID", "证据枚举无效。")
        if "session_id" in values:
            _state_text(values["session_id"], "session_id", 512)
            _state_list(values["artifact_refs"], "artifact_refs")
        _state_text(values["created_at"], "created_at", 128)
    elif record_type == "error":
        fields = {"plan_key", "error_id", "contract_id", "evidence_id", "competency_id", "error_class", "description", "context", "hint_level", "resolved_in_session", "misconception_candidate", "created_at"}
        if set(values) != fields:
            raise SyncRecordError("SYNC_STATE_INVALID", "错误记录字段不完整。")
        _state_key(values["plan_key"], values["error_id"], record_id); _state_text(values["contract_id"], "contract_id", 512); _state_text(values["competency_id"], "competency_id", 512); _state_text(values["description"], "description")
        for key in ("evidence_id", "misconception_candidate"):
            if not isinstance(values[key], str): raise SyncRecordError("SYNC_STATE_INVALID", f"学习错误字段无效：{key}。")
        if values["error_class"] not in _ERROR_CLASSES or values["context"] not in _ERROR_CONTEXTS or values["hint_level"] not in _HINT_LEVELS or type(values["resolved_in_session"]) is not bool:
            raise SyncRecordError("SYNC_STATE_INVALID", "错误枚举无效。")
        _state_text(values["created_at"], "created_at", 128)
    elif record_type == "verification":
        # ``verification`` carries either a pending VerificationItem or the
        # older EvaluationResult projection.  Keeping both shapes under the
        # same namespaced record type lets the additive classroom store sync
        # without invalidating already shipped evaluator snapshots.
        item_fields = {"plan_key", "verification_id", "contract_id", "objective_id",
                       "priority", "reason", "status", "source_error_ids", "created_at"}
        item_extended_fields = item_fields | {"reason_codes", "source_evidence_ids"}
        if set(values) in (item_fields, item_extended_fields):
            _state_key(values["plan_key"], values["verification_id"], record_id)
            _state_text(values["contract_id"], "contract_id", 512)
            _state_text(values["objective_id"], "objective_id", 512)
            if values["priority"] not in _VERIFICATION_PRIORITIES or values["status"] not in _VERIFICATION_STATUSES:
                raise SyncRecordError("SYNC_STATE_INVALID", "待检验项状态无效。")
            _state_text(values["reason"], "reason", 2000)
            _state_list(values["source_error_ids"], "source_error_ids")
            if "reason_codes" in values:
                _state_list(values["reason_codes"], "reason_codes", max_items=20)
                if any(code not in _VERIFICATION_REASONS for code in values["reason_codes"]):
                    raise SyncRecordError("SYNC_STATE_INVALID", "掌握检验原因无效。")
            if "source_evidence_ids" in values:
                _state_list(values["source_evidence_ids"], "source_evidence_ids")
            _state_text(values["created_at"], "created_at", 128)
        else:
            fields = {"plan_key", "verification_id", "contract_id", "verdict", "objective_results", "observed_errors", "recommended_action", "created_at"}
            if set(values) != fields:
                raise SyncRecordError("SYNC_STATE_INVALID", "掌握检验字段不完整。")
            _state_key(values["plan_key"], values["verification_id"], record_id); _state_text(values["contract_id"], "contract_id", 512)
            if values["verdict"] not in _VERDICTS or values["recommended_action"] not in _ACTIONS:
                raise SyncRecordError("SYNC_STATE_INVALID", "掌握检验结果无效。")
            _state_list(values["objective_results"], "objective_results", max_items=100, strings=False); _state_list(values["observed_errors"], "observed_errors", max_items=100, strings=False)
            if any(not isinstance(item, dict) or not isinstance(item.get("objective_id"), str) or item.get("status") not in _EVALUATOR for item in values["objective_results"]):
                raise SyncRecordError("SYNC_STATE_INVALID", "掌握检验目标结果无效。")
            if any(not isinstance(item, dict) or not isinstance(item.get("description"), str) for item in values["observed_errors"]):
                raise SyncRecordError("SYNC_STATE_INVALID", "掌握检验错误结果无效。")
            _state_text(values["created_at"], "created_at", 128)
    elif record_type == "tutor_checkpoint":
        fields = {"plan_key", "checkpoint_id", "plan_id", "contract_id",
                  "achieved_objectives", "pending_objectives",
                  "stable_competencies", "attention_competencies",
                  "active_misconception_ids", "recent_evidence_ids",
                  "suggested_next_step", "summary", "created_at"}
        if set(values) != fields:
            raise SyncRecordError("SYNC_STATE_INVALID", "Tutor checkpoint 字段不完整。")
        _state_key(values["plan_key"], values["checkpoint_id"], record_id)
        _state_text(values["plan_id"], "plan_id", 512)
        _state_text(values["contract_id"], "contract_id", 512)
        for name in ("achieved_objectives", "pending_objectives", "stable_competencies",
                     "attention_competencies", "active_misconception_ids", "recent_evidence_ids"):
            _state_list(values[name], name, max_items=100)
        _state_text(values["suggested_next_step"], "suggested_next_step", 2000)
        _state_text(values["summary"], "summary", 4000)
        _state_text(values["created_at"], "created_at", 128)
    elif record_type == "daily_mission":
        fields = {"schema", "plan_key", "mission_id", "plan_revision", "day_date", "timezone",
                  "status", "task_ids", "objectives", "available_minutes", "used_minutes",
                  "remaining_minutes", "reflowed_task_ids", "notice", "started_at", "updated_at", "idempotency_key"}
        if set(values) != fields:
            raise SyncRecordError("SYNC_STATE_INVALID", "每日任务快照字段不完整。")
        _state_key(values["plan_key"], values["mission_id"], record_id)
        if values["schema"] != "mentorflow.daily-mission.v1":
            raise SyncRecordError("SYNC_STATE_INVALID", "每日任务快照版本无效。")
        _state_text(values["day_date"], "day_date", 32)
        _state_text(values["timezone"], "timezone", 128)
        try:
            ZoneInfo(values["timezone"])
        except ZoneInfoNotFoundError:
            raise SyncRecordError("SYNC_STATE_INVALID", "每日任务时区必须是有效的 IANA 标识。") from None
        _state_text(values["status"], "status", 32)
        if values["status"] not in {"started", "paused", "completed", "cancelled"}:
            raise SyncRecordError("SYNC_STATE_INVALID", "每日任务快照状态无效。")
        if type(values["plan_revision"]) is not int or values["plan_revision"] < 1:
            raise SyncRecordError("SYNC_STATE_INVALID", "每日任务快照的计划版本无效。")
        _state_list(values["task_ids"], "task_ids", max_items=100)
        _state_list(values["reflowed_task_ids"], "reflowed_task_ids", max_items=100)
        _state_list(values["objectives"], "objectives", max_items=100, strings=False)
        if any(not isinstance(item, dict) or not isinstance(item.get("task_id"), str)
               for item in values["objectives"]):
            raise SyncRecordError("SYNC_STATE_INVALID", "每日任务目标无效。")
        for name in ("available_minutes", "used_minutes", "remaining_minutes"):
            if type(values[name]) is not int or not 0 <= values[name] <= 1440:
                raise SyncRecordError("SYNC_STATE_INVALID", "每日任务分钟数无效。")
        _state_text(values["started_at"], "started_at", 128)
        _state_text(values["updated_at"], "updated_at", 128)
        _state_text(values["idempotency_key"], "idempotency_key", 256)
        if not isinstance(values["notice"], str) or len(values["notice"]) > 2000:
            raise SyncRecordError("SYNC_STATE_INVALID", "每日任务提示无效。")
    elif record_type == "draft_plan":
        fields = {"plan_key", "draft_id", "revision", "status", "snapshot", "confirmed",
                  "updated_at", "idempotency_key"}
        if set(values) != fields:
            raise SyncRecordError("SYNC_STATE_INVALID", "计划草稿快照字段不完整。")
        # ``draft_id`` identifies the current DraftPlan revision; the
        # namespaced sync entity is the stable account draft aggregate.
        # Keeping the entity id stable is what lets revision/CAS detect an
        # edit instead of treating every revision as a new record.
        _state_key(values["plan_key"], "current", record_id)
        _state_text(values["status"], "status", 32)
        if values["status"] not in {"NEEDS_INPUT", "PLAN_READY"} or not isinstance(values["snapshot"], dict):
            raise SyncRecordError("SYNC_STATE_INVALID", "计划草稿状态无效。")
        if type(values["revision"]) is not int or values["revision"] < 1 or type(values["confirmed"]) is not bool:
            raise SyncRecordError("SYNC_STATE_INVALID", "计划草稿版本无效。")
        confirmation = values["snapshot"].get("draft_confirmation")
        if values["confirmed"]:
            if (not isinstance(confirmation, dict)
                    or set(confirmation) != {"schema", "draft_id", "revision", "confirmed_at", "confirmed_by", "idempotency_key"}
                    or confirmation.get("schema") != "mentorflow.draft-confirmation.v1"
                    or confirmation.get("draft_id") != values["draft_id"]
                    or confirmation.get("revision") != values["revision"]):
                raise SyncRecordError("SYNC_STATE_INVALID", "草稿确认事件无效。")
            _state_text(confirmation["confirmed_at"], "confirmed_at", 128)
            _state_text(confirmation["confirmed_by"], "confirmed_by", 512)
            _state_text(confirmation["idempotency_key"], "idempotency_key", 256)
        _state_text(values["updated_at"], "updated_at", 128)
        _state_text(values["idempotency_key"], "idempotency_key", 256)
    _check_values(values)
    return json.loads(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
