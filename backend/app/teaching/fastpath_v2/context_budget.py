"""Priority-preserving prompt context selection for Teacher Fast Path v2."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..composer import ConversationTurn, TeacherEvidence
from ..contracts import AssessmentState, EvidenceRequirement, LearnerContext, TeachingBrief
from ..examples import ExamplePlan
from .models import TeacherPromptEnvelope, TeacherResponseMode
from .prompt import build_teacher_prompt_envelope
from .response_length import TeacherResponseLengthDecision


class _ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextSelectionReason(str, Enum):
    WITHIN_BUDGET = "within_budget"
    OPTIONAL_CONTEXT_DROPPED = "optional_context_dropped"


class TeacherContextBudget(_ContextModel):
    budget_id: str = Field(min_length=1, max_length=160)
    maximum_prompt_tokens: int = Field(ge=1)
    maximum_evidence_tokens: int = Field(ge=1)
    maximum_history_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_sub_budgets(self) -> "TeacherContextBudget":
        if self.maximum_evidence_tokens > self.maximum_prompt_tokens:
            raise ValueError("evidence budget cannot exceed prompt budget")
        if self.maximum_history_tokens > self.maximum_prompt_tokens:
            raise ValueError("history budget cannot exceed prompt budget")
        return self


class ContextSelectionReceipt(_ContextModel):
    budget_id: str = Field(min_length=1, max_length=160)
    input_estimated_tokens: int = Field(ge=1)
    stable_prefix_tokens: int = Field(ge=1)
    dynamic_tokens: int = Field(ge=1)
    evidence_tokens: int = Field(ge=0)
    history_tokens: int = Field(ge=0)
    selected_evidence_ids: tuple[str, ...] = Field(max_length=32)
    selected_history_count: int = Field(ge=0)
    dropped_optional_context: tuple[str, ...] = Field(max_length=1_000)
    reason: ContextSelectionReason


class ContextBudgetPlan(_ContextModel):
    envelope: TeacherPromptEnvelope
    selected_evidence: tuple[TeacherEvidence, ...] = Field(max_length=32)
    selected_history: tuple[ConversationTurn, ...] = Field(max_length=24)
    learner_context_payload: dict[str, Any]
    receipt: ContextSelectionReceipt


class TeacherContextBudgetError(Exception):
    def __init__(self, code: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


def estimate_text_tokens(value: str) -> int:
    """Conservative deterministic UTF-8 estimate; not Provider billing usage."""

    return max(1, math.ceil(len(value.encode("utf-8")) / 4))


def _json_tokens(value: object) -> int:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return estimate_text_tokens(serialized)


def _nonempty(value: object) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


class ContextBudgetPlanner:
    """Keep mandatory data first and trim only optional prompt context."""

    _KEY_CONTEXT_FIELDS = (
        "learning_goal",
        "learner_level",
        "known_prerequisites",
        "missing_prerequisites",
        "mastery_state",
        "hint_dependency",
        "current_task",
        "recent_teaching_moves",
        "recent_examples",
        "next_concept",
        "next_concept_relationship",
    )
    _RECENT_MISTAKE_FIELDS = (
        "recent_mistakes",
        "known_misconceptions",
        "consecutive_failures",
    )

    def plan(
        self,
        *,
        budget: TeacherContextBudget,
        teaching_brief: TeachingBrief,
        learner_context: LearnerContext,
        latest_user_message: str,
        evidence: Sequence[TeacherEvidence],
        assessment_state: AssessmentState,
        response_mode: TeacherResponseMode,
        response_length: TeacherResponseLengthDecision | None,
        example_plan: ExamplePlan | None,
        recent_conversation: Sequence[ConversationTurn],
    ) -> ContextBudgetPlan:
        evidence_by_id: dict[str, TeacherEvidence] = {}
        for item in evidence:
            if item.evidence_id in evidence_by_id:
                raise TeacherContextBudgetError(
                    "DUPLICATE_EVIDENCE_ID",
                    details={"evidence_id": item.evidence_id},
                )
            evidence_by_id[item.evidence_id] = item
        relevant_ids = tuple(dict.fromkeys(teaching_brief.relevant_evidence_ids))
        missing = tuple(item for item in relevant_ids if item not in evidence_by_id)
        required = teaching_brief.evidence_requirement is EvidenceRequirement.REQUIRED
        if required and (not relevant_ids or missing):
            raise TeacherContextBudgetError(
                "EVIDENCE_REQUIRED_BUT_MISSING",
                details={"missing_evidence_ids": missing or relevant_ids},
            )
        relevant = tuple(
            evidence_by_id[item] for item in relevant_ids if item in evidence_by_id
        )
        selected_evidence: list[TeacherEvidence] = list(relevant if required else ())
        evidence_tokens = _json_tokens(
            [item.model_dump(mode="json") for item in selected_evidence]
        ) if selected_evidence else 0
        if evidence_tokens > budget.maximum_evidence_tokens:
            raise TeacherContextBudgetError(
                "REQUIRED_EVIDENCE_EXCEEDS_CONTEXT_BUDGET",
                details={
                    "required_evidence_tokens": evidence_tokens,
                    "maximum_evidence_tokens": budget.maximum_evidence_tokens,
                },
            )

        full_context = learner_context.model_dump(mode="json")
        selected_context: dict[str, Any] = {
            "current_concept": learner_context.current_concept,
            "student_move": learner_context.student_move.value,
            "assessment_state": assessment_state.value,
            "evidence_available": bool(selected_evidence),
            "relevant_evidence_ids": [
                item.evidence_id for item in selected_evidence
            ],
        }
        selected_history: list[ConversationTurn] = []

        def build_envelope() -> TeacherPromptEnvelope:
            selected_context["evidence_available"] = bool(selected_evidence)
            selected_context["relevant_evidence_ids"] = [
                item.evidence_id for item in selected_evidence
            ]
            return build_teacher_prompt_envelope(
                teaching_brief=teaching_brief,
                learner_context=learner_context,
                learner_context_payload=selected_context,
                latest_user_message=latest_user_message,
                evidence=selected_evidence,
                assessment_state=assessment_state,
                response_mode=response_mode,
                response_length=response_length,
                example_plan=example_plan,
                recent_conversation=selected_history,
            )

        def within_limits(envelope: TeacherPromptEnvelope) -> bool:
            return (
                estimate_text_tokens(envelope.stable_prefix.content)
                + estimate_text_tokens(envelope.dynamic_tail.content)
                <= budget.maximum_prompt_tokens
            )

        envelope = build_envelope()
        if not within_limits(envelope):
            code = (
                "REQUIRED_EVIDENCE_EXCEEDS_CONTEXT_BUDGET"
                if required and selected_evidence
                else "CONTEXT_BUDGET_OVERFLOW"
            )
            raise TeacherContextBudgetError(
                code,
                details={
                    "minimum_prompt_tokens": (
                        estimate_text_tokens(envelope.stable_prefix.content)
                        + estimate_text_tokens(envelope.dynamic_tail.content)
                    ),
                    "maximum_prompt_tokens": budget.maximum_prompt_tokens,
                },
            )

        dropped: list[str] = []
        for item in relevant:
            if item in selected_evidence:
                continue
            candidate_tokens = _json_tokens(
                [
                    evidence_item.model_dump(mode="json")
                    for evidence_item in (*selected_evidence, item)
                ]
            )
            if candidate_tokens > budget.maximum_evidence_tokens:
                dropped.append(f"evidence:{item.evidence_id}")
                continue
            selected_evidence.append(item)
            candidate = build_envelope()
            if within_limits(candidate):
                envelope = candidate
                evidence_tokens = candidate_tokens
            else:
                selected_evidence.pop()
                dropped.append(f"evidence:{item.evidence_id}")

        for field_name in (*self._KEY_CONTEXT_FIELDS, *self._RECENT_MISTAKE_FIELDS):
            value = full_context[field_name]
            if not _nonempty(value):
                continue
            selected_context[field_name] = value
            candidate = build_envelope()
            if within_limits(candidate):
                envelope = candidate
            else:
                selected_context.pop(field_name)
                dropped.append(f"learner_context:{field_name}")

        history_tokens = 0
        selected_reverse: list[ConversationTurn] = []
        for reverse_index, item in enumerate(reversed(recent_conversation)):
            item_tokens = _json_tokens(item.model_dump(mode="json"))
            if history_tokens + item_tokens > budget.maximum_history_tokens:
                dropped.append(
                    f"history:{len(recent_conversation) - reverse_index - 1}"
                )
                continue
            selected_reverse.append(item)
            selected_history[:] = reversed(selected_reverse)
            candidate = build_envelope()
            if within_limits(candidate):
                envelope = candidate
                history_tokens += item_tokens
            else:
                selected_reverse.pop()
                selected_history[:] = reversed(selected_reverse)
                dropped.append(
                    f"history:{len(recent_conversation) - reverse_index - 1}"
                )

        stable_tokens = estimate_text_tokens(envelope.stable_prefix.content)
        dynamic_tokens = estimate_text_tokens(envelope.dynamic_tail.content)
        receipt = ContextSelectionReceipt(
            budget_id=budget.budget_id,
            input_estimated_tokens=stable_tokens + dynamic_tokens,
            stable_prefix_tokens=stable_tokens,
            dynamic_tokens=dynamic_tokens,
            evidence_tokens=evidence_tokens,
            history_tokens=history_tokens,
            selected_evidence_ids=tuple(
                item.evidence_id for item in selected_evidence
            ),
            selected_history_count=len(selected_history),
            dropped_optional_context=tuple(dropped),
            reason=(
                ContextSelectionReason.OPTIONAL_CONTEXT_DROPPED
                if dropped
                else ContextSelectionReason.WITHIN_BUDGET
            ),
        )
        return ContextBudgetPlan(
            envelope=envelope,
            selected_evidence=tuple(selected_evidence),
            selected_history=tuple(selected_history),
            learner_context_payload=selected_context,
            receipt=receipt,
        )


__all__ = [
    "ContextBudgetPlan",
    "ContextBudgetPlanner",
    "ContextSelectionReason",
    "ContextSelectionReceipt",
    "TeacherContextBudget",
    "TeacherContextBudgetError",
    "estimate_text_tokens",
]
