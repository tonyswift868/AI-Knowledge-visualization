from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

import app.teaching.fastpath_v2.prompt as prompt_module
from app.teaching.composer import ConversationTurn, TeacherEvidence
from app.teaching.contracts import (
    AssessmentState,
    LearnerContext,
    MasteryState,
    StudentMove,
)
from app.teaching.fastpath_v2 import (
    DynamicTeacherTail,
    PromptIdentity,
    TeacherPromptAssemblyError,
    TeacherPromptEnvelope,
    TeacherResponseMode,
    build_teacher_prompt_envelope,
    stable_teacher_prefix,
)
from app.teaching.pedagogy import plan_teaching


EVIDENCE = TeacherEvidence(
    evidence_id="chunk-1",
    content="HashMap threshold equals capacity multiplied by load factor.",
    source_reference="fixture://hashmap/threshold",
)
UNUSED_EVIDENCE = TeacherEvidence(
    evidence_id="chunk-unused",
    content="This evidence is not selected by the TeachingBrief.",
    source_reference="fixture://unused",
)


def _context(**changes) -> LearnerContext:
    values = {
        "current_concept": "HashMap threshold",
        "learning_goal": "Distinguish threshold from capacity",
        "learner_level": "beginner",
        "mastery_state": MasteryState.IN_PROGRESS,
        "student_move": StudentMove.ASK_WHY,
        "evidence_available": True,
        "relevant_evidence_ids": ("chunk-1",),
        "assessment_state": AssessmentState.READY,
    }
    values.update(changes)
    return LearnerContext(**values)


def _envelope(
    *,
    context: LearnerContext | None = None,
    message: str = "为什么 threshold 不是 capacity？",
    evidence: tuple[TeacherEvidence, ...] = (EVIDENCE,),
    assessment_state: AssessmentState = AssessmentState.READY,
    mode: TeacherResponseMode = TeacherResponseMode.FAST,
) -> TeacherPromptEnvelope:
    learner_context = context or _context()
    return build_teacher_prompt_envelope(
        teaching_brief=plan_teaching(learner_context),
        learner_context=learner_context,
        latest_user_message=message,
        evidence=evidence,
        assessment_state=assessment_state,
        response_mode=mode,
        recent_conversation=(
            ConversationTurn(role="assistant", content="我们正在讨论扩容阈值。"),
        ),
    )


def test_stable_prefix_is_reused_while_every_dynamic_identity_changes() -> None:
    first = _envelope()
    second = _envelope(
        context=_context(
            learner_level="advanced",
            learning_goal="Analyze threshold implementation details",
        ),
        message="现在请结合实现解释 threshold",
        assessment_state=AssessmentState.IN_PROGRESS,
        mode=TeacherResponseMode.DEEP,
    )

    assert first.stable_prefix == second.stable_prefix
    assert first.identity.stable_prefix_hash == second.identity.stable_prefix_hash
    assert first.dynamic_tail.dynamic_tail_hash != second.dynamic_tail.dynamic_tail_hash
    assert first.identity.prompt_identity != second.identity.prompt_identity
    assert "beginner" in first.dynamic_tail.content
    assert "advanced" in second.dynamic_tail.content
    assert "beginner" not in second.dynamic_tail.content
    assert stable_teacher_prefix() is stable_teacher_prefix()


def test_stable_prefix_contains_only_policy_contract_and_schema_material() -> None:
    envelope = _envelope()
    stable = envelope.stable_prefix.content

    assert "Authority and safety rules" in stable
    assert "Return exactly one JSON object" in stable
    assert "teacher-composer.v1" in stable
    assert "pedagogy-planner.v1" in stable
    assert "teacher-runtime.v2" in stable
    assert "HashMap threshold" not in stable
    assert EVIDENCE.content not in stable
    assert "为什么 threshold" not in stable


def test_dynamic_tail_carries_fresh_context_concept_message_evidence_and_assessment() -> None:
    envelope = _envelope()
    dynamic = envelope.dynamic_tail.content

    required_sections = (
        "<LATEST_LEARNER_CONTEXT>",
        "<CURRENT_CONCEPT>",
        "<LATEST_USER_MESSAGE>",
        "<DYNAMIC_EVIDENCE>",
        "<ASSESSMENT_STATE>",
    )
    assert all(section in dynamic for section in required_sections)
    assert _context().current_concept in dynamic
    assert EVIDENCE.content in dynamic
    assert AssessmentState.READY.value in dynamic
    assert envelope.allowed_evidence_ids == ("chunk-1",)


def test_unselected_evidence_is_not_sent_or_included_in_prompt_identity() -> None:
    selected_only = _envelope(evidence=(EVIDENCE,))
    with_unused = _envelope(evidence=(EVIDENCE, UNUSED_EVIDENCE))

    assert selected_only == with_unused
    assert UNUSED_EVIDENCE.content not in with_unused.dynamic_tail.content


def test_hashes_and_prompt_identity_are_self_validating() -> None:
    envelope = _envelope()

    assert envelope.stable_prefix.stable_prefix_hash.startswith("sha256:")
    assert envelope.dynamic_tail.dynamic_tail_hash.startswith("sha256:")
    assert envelope.identity.prompt_identity.startswith("sha256:")
    assert len(envelope.identity.prompt_identity) == 71
    with pytest.raises(ValidationError, match="dynamic tail hash mismatch"):
        DynamicTeacherTail(
            content=envelope.dynamic_tail.content + "tampered",
            dynamic_tail_hash=envelope.dynamic_tail.dynamic_tail_hash,
        )
    with pytest.raises(ValidationError, match="prompt identity mismatch"):
        PromptIdentity(
            stable_prefix_hash=envelope.identity.stable_prefix_hash,
            dynamic_tail_hash=envelope.identity.dynamic_tail_hash,
            prompt_identity="sha256:" + "0" * 64,
        )


def test_required_evidence_missing_fails_before_an_envelope_exists() -> None:
    with pytest.raises(
        TeacherPromptAssemblyError,
        match="EVIDENCE_REQUIRED_BUT_MISSING",
    ):
        _envelope(evidence=())


def test_provider_messages_keep_stable_prefix_first() -> None:
    envelope = _envelope()

    assert envelope.provider_messages() == [
        {"role": "system", "content": envelope.stable_prefix.content},
        {"role": "user", "content": envelope.dynamic_tail.content},
    ]


def test_prompt_module_has_no_final_response_result_cache() -> None:
    source = inspect.getsource(prompt_module).casefold()

    assert "response_cache" not in source
    assert "final_response_cache" not in source
    assert "cache.set" not in source
    assert "cache.get" not in source
