"""Validated, provider-independent actions for the active tutor loop.

The model may suggest an action, but it never gets to mutate Learning Run
state directly.  This module is deliberately free of database/HTTP imports so
that the contract can be tested and replayed in isolation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


ACTION_TYPES = (
    "diagnose",
    "explain",
    "analogy",
    "worked_example",
    "guided_question",
    "hint",
    "misconception_feedback",
    "faded_practice",
    "independent_task",
    "variant_test",
    "review",
    "encourage",
    "retry",
    "offer_self_study",
    "close_day",
)

_ALIASES = {
    "example": "worked_example",
    "question": "guided_question",
    "feedback": "misconception_feedback",
    "practice": "faded_practice",
    "test": "independent_task",
    "variant": "variant_test",
    "remediation": "misconception_feedback",
    "close": "close_day",
    "self_study": "offer_self_study",
}
_FORBIDDEN_KEYS = {
    "passed", "pass", "reward", "rewards", "reward_id", "status", "run_status",
    "learning_run_passed", "expedition_reward", "core_state", "write_state",
}


class TeacherActionValidationError(ValueError):
    """Raised when provider output is malformed or attempts a privileged write."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


def _contains_forbidden(value: Any, path: str = "payload") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                return f"{path}.{key}"
            found = _contains_forbidden(child, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found = _contains_forbidden(child, f"{path}[{index}]")
            if found:
                return found
    return None


@dataclass(frozen=True)
class TeacherAction:
    action_type: str
    target_node: str
    evidence_refs: tuple[str, ...] = ()
    expected_response: str = ""
    success_criteria: tuple[str, ...] = ()
    fallback: str = "retry"
    kks_state: Mapping[str, Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_refs"] = list(self.evidence_refs)
        result["success_criteria"] = list(self.success_criteria)
        result["kks_state"] = dict(self.kks_state)
        result["payload"] = dict(self.payload)
        return result


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) and item.strip() for item in value):
        raise TeacherActionValidationError("INVALID_FIELD", f"{field_name} 必须是非空字符串列表")
    return tuple(item.strip() for item in value)


def validate_teacher_action(raw: Mapping[str, Any] | TeacherAction, *, phase: str | None = None) -> TeacherAction:
    """Validate a model action and return a normalized immutable action.

    ``passed``/reward/status writes are rejected recursively, including nested
    provider payloads.  The evaluator and Learning Run remain the sole owners
    of completion and reward state.
    """
    if isinstance(raw, TeacherAction):
        candidate = raw.to_dict()
    elif isinstance(raw, Mapping):
        candidate = dict(raw)
    else:
        raise TeacherActionValidationError("INVALID_ACTION", "Teacher Action 必须是对象")
    forbidden_path = _contains_forbidden(candidate)
    if forbidden_path:
        raise TeacherActionValidationError("PRIVILEGED_WRITE", "模型不得写入通过、奖励或核心状态", {"path": forbidden_path})
    action_type = str(candidate.get("action_type") or candidate.get("type") or "").strip().lower()
    action_type = _ALIASES.get(action_type, action_type)
    if action_type not in ACTION_TYPES:
        raise TeacherActionValidationError("UNKNOWN_ACTION", "未知 Teacher Action", {"action_type": action_type})
    if phase == "daily_variant_test" and action_type == "hint":
        raise TeacherActionValidationError("HINT_NOT_ALLOWED", "daily_variant_test 不允许提示")
    target = str(candidate.get("target_node") or candidate.get("target") or "").strip()
    if not target:
        raise TeacherActionValidationError("TARGET_REQUIRED", "Teacher Action 必须指定目标知识节点")
    criteria = _strings(candidate.get("success_criteria"), "success_criteria")
    if action_type not in {"analogy", "explain", "misconception_feedback", "encourage", "close_day", "offer_self_study"} and not criteria:
        raise TeacherActionValidationError("CRITERIA_REQUIRED", "该动作必须包含 success_criteria")
    fallback = str(candidate.get("fallback") or "retry").strip().lower()
    fallback = _ALIASES.get(fallback, fallback)
    if fallback not in ACTION_TYPES:
        raise TeacherActionValidationError("INVALID_FALLBACK", "fallback 必须是合法 Teacher Action")
    evidence = _strings(candidate.get("evidence_refs"), "evidence_refs")
    expected = str(candidate.get("expected_response") or "").strip()
    kks_state = candidate.get("kks_state") or {}
    payload = candidate.get("payload") or {}
    if not isinstance(kks_state, Mapping) or not isinstance(payload, Mapping):
        raise TeacherActionValidationError("INVALID_FIELD", "kks_state 和 payload 必须是对象")
    return TeacherAction(action_type, target, evidence, expected, criteria, fallback, dict(kks_state), dict(payload))


def parse_provider_action(raw: Any, *, phase: str | None = None) -> TeacherAction:
    """Parse dict/JSON provider output through the same validator."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TeacherActionValidationError("INVALID_JSON", "Provider 输出不是有效 JSON") from exc
    return validate_teacher_action(raw, phase=phase)


def action_types() -> tuple[str, ...]:
    return ACTION_TYPES
