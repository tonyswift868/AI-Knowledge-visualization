"""Deterministic tutor decision engine with safe provider fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .student_model import StudentState, remediation_for
from .teacher_action import (
    TeacherAction,
    TeacherActionValidationError,
    parse_provider_action,
    validate_teacher_action,
)


@dataclass(frozen=True)
class TutorDecision:
    action: TeacherAction
    source: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action.to_dict(), "source": self.source, "reason": self.reason}


def _action(action_type: str, phase: str, target: str, *, criteria: tuple[str, ...] = ("完成当前行动并提交证据",), fallback: str = "retry", reason: str = "deterministic fallback", payload: Mapping[str, Any] | None = None) -> TutorDecision:
    return TutorDecision(validate_teacher_action({
        "action_type": action_type, "target_node": target, "expected_response": "用自己的话说明判断、步骤和证据",
        "success_criteria": criteria, "fallback": fallback, "kks_state": {"phase": phase}, "payload": dict(payload or {}),
    }, phase=phase), "fallback", reason)


def deterministic_fallback(state: StudentState, phase: str, *, target: str = "current_node") -> TutorDecision:
    """Select a bounded action without a model/provider call."""
    if phase in {"passed", "review_passed", "final_passed", "close_day"}:
        return _action("close_day", phase, target, criteria=(), fallback="close_day", reason="run already complete")
    if phase == "daily_variant_test":
        if state.misconceptions:
            return _action("misconception_feedback", phase, target, criteria=(), fallback="retry", reason="variant failed with misconception")
        return _action("variant_test", phase, target, criteria=("独立完成变式并提供证据",), fallback="retry", reason="variant test has no hint path")
    if state.misconceptions:
        sequence = remediation_for(state.misconceptions[0])
        return _action("misconception_feedback", phase, target, criteria=(), fallback="retry", reason=f"remediate {state.misconceptions[0]}", payload={"misconception": state.misconceptions[0], "remedy_sequence": list(sequence)})
    if phase == "explanation":
        return _action("explain", phase, target, criteria=(), fallback="diagnose", reason="start with a grounded explanation")
    if phase == "worked_example":
        return _action("worked_example", phase, target, criteria=("指出示例中的关键步骤",), fallback="explain", reason="move from explanation to example")
    if phase in {"guided_practice", "faded_guidance"}:
        return _action("faded_practice", phase, target, criteria=("完成下一步并说明依据",), fallback="worked_example", reason="practice before independence")
    if phase == "independent_attempt":
        return _action("independent_task", phase, target, criteria=("独立完成任务并提供证据",), fallback="misconception_feedback", reason="independent checkpoint")
    if phase in {"spaced_recall", "mixed_review", "transfer_test"}:
        return _action("review" if phase != "transfer_test" else "variant_test", phase, target, criteria=("召回并迁移关键方法",), fallback="misconception_feedback", reason="review/transfer checkpoint")
    return _action("diagnose", phase, target, criteria=("确认当前卡点和下一步",), fallback="explain", reason="unknown phase safe fallback")


def decide_next_action(
    state: StudentState,
    phase: str,
    *,
    provider_output: Any = None,
    target_node: str = "current_node",
) -> TutorDecision:
    """Use provider output only after validation; any failure is deterministic."""
    if provider_output is not None:
        try:
            action = parse_provider_action(provider_output, phase=phase)
            if action.target_node != target_node and target_node:
                # Do not let a model silently switch the knowledge node being assessed.
                raise TeacherActionValidationError("TARGET_MISMATCH", "Provider 不得切换当前知识节点")
            return TutorDecision(action, "provider", "validated provider action")
        except TeacherActionValidationError:
            pass
    fallback = deterministic_fallback(state, phase, target=target_node)
    return fallback


def decide_after_submission(
    state: StudentState,
    phase: str,
    *,
    passed: bool,
    target_node: str = "current_node",
) -> TutorDecision:
    """Bounded failure -> remediation -> retry progression."""
    if passed and phase in {"daily_variant_test", "final_assessment", "transfer_test"}:
        return _action("close_day", phase, target_node, criteria=(), fallback="close_day", reason="evaluator owns pass; close naturally")
    if passed:
        return deterministic_fallback(state, phase, target=target_node)
    return deterministic_fallback(state, phase, target=target_node)


def decide_after_remediation(state: StudentState, phase: str, *, target_node: str = "current_node") -> TutorDecision:
    """Return the bounded retry step after a remediation action is completed."""
    return _action("retry", phase, target_node, criteria=("按照修复后的方法重评一次",), fallback="remediation", reason="retry after remediation")


def offer_self_study(state: StudentState, phase: str, *, target_node: str = "current_node", approved: bool = False) -> TutorDecision:
    """Offer early study only when explicitly approved; never auto-unlocks a future phase."""
    if approved and phase not in {"passed", "review_passed", "final_passed", "close_day"}:
        return _action("offer_self_study", phase, target_node, criteria=("用户确认提前学习范围和负荷",), fallback="close_day", reason="explicitly approved early-study offer")
    return deterministic_fallback(state, phase, target=target_node)
