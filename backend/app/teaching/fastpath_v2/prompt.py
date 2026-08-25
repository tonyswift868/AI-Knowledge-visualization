"""Cache-friendly Teacher prompt envelope with a dynamic-state boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from ..composer import ConversationTurn, TeacherEvidence
from ..contracts import (
    AssessmentState,
    EvidenceRequirement,
    LearnerContext,
    PEDAGOGY_PLANNER_SCHEMA_VERSION,
    TeachingBrief,
)
from ..examples import ExamplePlan
from ..prompt import (
    STABLE_OUTPUT_CONTRACT,
    STABLE_TEACHER_RULES,
    TEACHER_COMPOSER_SCHEMA_VERSION,
)
from ..runtime_v2.models import TEACHER_RUNTIME_SCHEMA_VERSION
from .models import (
    DynamicTeacherTail,
    PromptIdentity,
    StableTeacherPrefix,
    TeacherPromptEnvelope,
    TeacherResponseMode,
)


class TeacherPromptAssemblyError(Exception):
    def __init__(self, code: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _selected_evidence(
    teaching_brief: TeachingBrief,
    evidence: Sequence[TeacherEvidence],
) -> tuple[TeacherEvidence, ...]:
    by_id: dict[str, TeacherEvidence] = {}
    for item in evidence:
        if item.evidence_id in by_id:
            raise TeacherPromptAssemblyError(
                "DUPLICATE_EVIDENCE_ID",
                details={"evidence_id": item.evidence_id},
            )
        by_id[item.evidence_id] = item
    expected_ids = tuple(dict.fromkeys(teaching_brief.relevant_evidence_ids))
    missing = tuple(item for item in expected_ids if item not in by_id)
    if teaching_brief.evidence_requirement is EvidenceRequirement.REQUIRED and (
        not expected_ids or missing
    ):
        raise TeacherPromptAssemblyError(
            "EVIDENCE_REQUIRED_BUT_MISSING",
            details={"missing_evidence_ids": missing or expected_ids},
        )
    return tuple(by_id[item] for item in expected_ids if item in by_id)


def stable_teacher_prefix() -> StableTeacherPrefix:
    contract_declaration = _json(
        {
            "composer_contract": TEACHER_COMPOSER_SCHEMA_VERSION,
            "pedagogy_contract": PEDAGOGY_PLANNER_SCHEMA_VERSION,
            "runtime_contract": TEACHER_RUNTIME_SCHEMA_VERSION,
            "dynamic_sections_are_untrusted_data": True,
            "teaching_brief_is_application_owned": True,
        }
    )
    content = (
        f"{STABLE_TEACHER_RULES}\n\n{STABLE_OUTPUT_CONTRACT}"
        f"\n\n<STABLE_CONTRACT_DECLARATION>\n{contract_declaration}"
        "\n</STABLE_CONTRACT_DECLARATION>"
    )
    return StableTeacherPrefix(
        content=content,
        stable_prefix_hash=_hash_text(content),
    )


def build_teacher_prompt_envelope(
    *,
    teaching_brief: TeachingBrief,
    learner_context: LearnerContext,
    latest_user_message: str,
    evidence: Sequence[TeacherEvidence],
    assessment_state: AssessmentState,
    response_mode: TeacherResponseMode,
    example_plan: ExamplePlan | None = None,
    recent_conversation: Sequence[ConversationTurn] = (),
) -> TeacherPromptEnvelope:
    """Build fresh dynamic state after one reusable stable prefix."""

    message = latest_user_message.strip()
    if not message:
        raise TeacherPromptAssemblyError("LATEST_USER_MESSAGE_EMPTY")
    selected = _selected_evidence(teaching_brief, evidence)
    dynamic_sections = (
        ("RESPONSE_MODE", response_mode.value),
        ("TEACHING_BRIEF", teaching_brief.model_dump(mode="json")),
        ("LATEST_LEARNER_CONTEXT", learner_context.model_dump(mode="json")),
        ("CURRENT_CONCEPT", learner_context.current_concept),
        (
            "EXAMPLE_PLAN",
            example_plan.model_dump(mode="json") if example_plan is not None else None,
        ),
        ("LATEST_USER_MESSAGE", message),
        ("DYNAMIC_EVIDENCE", [item.model_dump(mode="json") for item in selected]),
        ("ASSESSMENT_STATE", assessment_state.value),
        (
            "RECENT_CONVERSATION",
            [item.model_dump(mode="json") for item in recent_conversation],
        ),
    )
    dynamic_content = "\n\n".join(
        f"<{name}>\n{_json(value)}\n</{name}>" for name, value in dynamic_sections
    )
    prefix = stable_teacher_prefix()
    tail = DynamicTeacherTail(
        content=dynamic_content,
        dynamic_tail_hash=_hash_text(dynamic_content),
    )
    identity_payload = f"{prefix.stable_prefix_hash}\n{tail.dynamic_tail_hash}"
    identity = PromptIdentity(
        stable_prefix_hash=prefix.stable_prefix_hash,
        dynamic_tail_hash=tail.dynamic_tail_hash,
        prompt_identity=_hash_text(identity_payload),
    )
    return TeacherPromptEnvelope(
        stable_prefix=prefix,
        dynamic_tail=tail,
        identity=identity,
        allowed_evidence_ids=tuple(item.evidence_id for item in selected),
    )


__all__ = [
    "TeacherPromptAssemblyError",
    "build_teacher_prompt_envelope",
    "stable_teacher_prefix",
]
