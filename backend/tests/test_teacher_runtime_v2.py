from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
from pathlib import Path
from typing import Any

import pytest

import app.teaching.runtime_v2.orchestrator as runtime_module
from app.teaching.composer import (
    FollowUpKind,
    ProviderMetadata,
    TeacherEvidence,
    TeacherProviderDraft,
    TeacherResponseDraft,
    compose_teacher_response,
)
from app.teaching.contracts import (
    AssessmentState,
    CurrentTaskKind,
    LearnerContext,
    MasteryState,
    StudentMove,
    TeacherMove,
)
from app.teaching.examples import plan_example
from app.teaching.pedagogy import plan_teaching
from app.teaching.runtime_v2 import (
    TeacherRuntimeError,
    TeacherTurnResult,
    run_teacher_turn,
)
from app.teaching.student_move import StudentMoveResolver


BACKEND_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = TeacherEvidence(
    evidence_id="chunk-1",
    content="HashMap resizes when size crosses capacity multiplied by loadFactor.",
    citation="JDK HashMap implementation note",
    source_reference="source://java/hashmap#resize",
)


def _context(**changes: Any) -> LearnerContext:
    values: dict[str, Any] = {
        "current_concept": "HashMap resize",
        "learning_goal": "Distinguish threshold from capacity",
        "learner_level": "intermediate",
        "known_prerequisites": ("hashing",),
        "mastery_state": MasteryState.IN_PROGRESS,
        "student_move": StudentMove.CONTINUE,
        "evidence_available": True,
        "relevant_evidence_ids": ("chunk-1",),
    }
    values.update(changes)
    return LearnerContext(**values)


class RecordingProvider:
    provider_name = "runtime-recording-fake"
    model = "offline-runtime-model"

    def __init__(
        self,
        *,
        cited_evidence_ids: tuple[str, ...] = ("chunk-1",),
        raw_output: object | None = None,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.cited_evidence_ids = cited_evidence_ids
        self.raw_output = raw_output
        self.error = error
        self.events = events
        self.calls: list[dict[str, Any]] = []
        self.api_key = "must-never-enter-runtime-trace"

    async def structured(self, messages, response_model, temperature=0.2):
        if self.events is not None:
            self.events.append("provider")
        self.calls.append(
            {
                "messages": messages,
                "response_model": response_model,
                "temperature": temperature,
            }
        )
        if self.error is not None:
            raise self.error
        if self.raw_output is not None:
            return self.raw_output
        return response_model(
            text="关键是 size 越过 threshold，而不是等到 capacity 用完。",
            cited_evidence_ids=self.cited_evidence_ids,
            follow_up_kind=FollowUpKind.NONE,
        )


def _run(
    message: str,
    *,
    provider: RecordingProvider | None = None,
    context: LearnerContext | None = None,
    evidence: tuple[TeacherEvidence, ...] = (EVIDENCE,),
    **kwargs: Any,
):
    selected_provider = provider or RecordingProvider()
    result = asyncio.run(
        run_teacher_turn(
            latest_user_message=message,
            learner_context=context or _context(),
            provider=selected_provider,
            evidence=evidence,
            **kwargs,
        )
    )
    return selected_provider, result


def test_core_orchestrates_student_move_pedagogy_composer_and_result() -> None:
    provider, result = _run("为什么 threshold 不是 capacity？")

    assert result.teaching_move is TeacherMove.ANSWER_DIRECTLY
    assert result.response_draft.text.startswith("关键是")
    assert result.citations_metadata == result.response_draft.citations
    assert result.transition_recommendation is False
    assert result.assessment_recommendation is False
    assert result.self_attested_signal is False
    assert result.runtime_trace.student_move is StudentMove.ASK_WHY
    assert result.runtime_trace.provider_call_count == 1
    assert result.runtime_trace.evidence_ids == ("chunk-1",)
    assert result.runtime_trace.elapsed_ms >= 0
    assert len(provider.calls) == 1


def test_runtime_executes_the_declared_stage_order() -> None:
    events: list[str] = []

    class OrderedResolver(StudentMoveResolver):
        def resolve(self, *args, **kwargs):
            events.append("student_move")
            return super().resolve(*args, **kwargs)

    def pedagogy(context):
        events.append("pedagogy")
        return plan_teaching(context)

    def examples(request):
        events.append("example")
        return plan_example(request)

    async def composer(**kwargs):
        events.append("composer")
        return await compose_teacher_response(**kwargs)

    provider = RecordingProvider(events=events)
    _run(
        "换个例子",
        provider=provider,
        student_move_resolver=OrderedResolver(),
        pedagogy_planner=pedagogy,
        example_planner=examples,
        composer=composer,
    )

    assert events == ["student_move", "pedagogy", "example", "composer", "provider"]


def test_required_evidence_missing_fails_before_provider_call() -> None:
    context = _context()
    before = context.model_dump(mode="json")
    provider = RecordingProvider()

    with pytest.raises(TeacherRuntimeError) as captured:
        _run("为什么？", provider=provider, context=context, evidence=())

    assert captured.value.code == "EVIDENCE_REQUIRED_BUT_MISSING"
    assert captured.value.trace is not None
    assert captured.value.trace.provider_call_count == 0
    assert captured.value.trace.evidence_ids == ()
    assert context.model_dump(mode="json") == before
    assert provider.calls == []


@pytest.mark.parametrize(
    "provider",
    [
        RecordingProvider(error=TimeoutError("offline timeout")),
        RecordingProvider(
            raw_output={
                "text": "",
                "cited_evidence_ids": ["chunk-1"],
                "follow_up_kind": "none",
            }
        ),
    ],
)
def test_provider_timeout_or_invalid_output_cannot_modify_learning_state(
    provider: RecordingProvider,
) -> None:
    context = _context()
    before = context.model_dump(mode="json")

    with pytest.raises(TeacherRuntimeError) as captured:
        _run("为什么？", provider=provider, context=context)

    assert captured.value.code == "TEACHER_RESPONSE_INVALID"
    assert captured.value.trace is not None
    assert captured.value.trace.provider_call_count == 1
    assert context.model_dump(mode="json") == before
    assert len(provider.calls) == 1


def test_learner_says_mastered_only_emits_self_attested_transition_signal() -> None:
    context = _context()
    before = context.model_dump(mode="json")
    provider, result = _run(
        "这个我会了",
        context=context,
        provider=RecordingProvider(cited_evidence_ids=()),
    )

    assert result.self_attested_signal is True
    assert result.transition_recommendation is True
    assert result.teaching_move is TeacherMove.TRANSITION
    assert context.model_dump(mode="json") == before
    payload = result.model_dump(mode="json")
    assert {
        "mastery",
        "mastery_update",
        "task_status",
        "task_complete",
        "plan",
        "score",
    }.isdisjoint(payload)
    assert len(provider.calls) == 1


def test_related_tangent_preserves_current_learning_position() -> None:
    _provider, result = _run(
        "顺便问一下，ConcurrentHashMap 呢？",
        context=_context(
            next_concept="ConcurrentHashMap",
            next_concept_relationship="supports coordinated concurrent access",
        ),
    )

    assert result.runtime_trace.student_move is StudentMove.RELATED_TANGENT
    assert (
        result.runtime_trace.pedagogy_decision.preserve_current_learning_position
        is True
    )
    assert result.transition_recommendation is False


def test_confused_uses_recovery_example_without_forcing_assessment() -> None:
    provider, result = _run(
        "我没听懂",
        provider=RecordingProvider(cited_evidence_ids=()),
    )

    assert result.runtime_trace.student_move is StudentMove.CONFUSED
    assert result.assessment_recommendation is False
    assert result.runtime_trace.example_choice is not None
    assert "Application-owned ExamplePlan intent" in provider.calls[0]["messages"][1][
        "content"
    ]


def test_example_plan_consumption_can_be_disabled_for_the_same_turn() -> None:
    provider, result = _run("换个例子", use_example_engine=False)

    assert result.runtime_trace.example_choice is None
    assert "Application-owned ExamplePlan intent" not in provider.calls[0]["messages"][1][
        "content"
    ]


def test_assessment_is_only_a_recommendation_and_never_a_score() -> None:
    _provider, result = _run(
        "考考我",
        provider=RecordingProvider(cited_evidence_ids=()),
        assessment_state=AssessmentState.READY,
    )

    assert result.assessment_recommendation is True
    assert result.runtime_trace.assessment_state is AssessmentState.READY
    assert "score" not in TeacherTurnResult.model_fields
    assert "grade" not in TeacherTurnResult.model_fields


def test_primary_provider_call_limit_is_enforced_even_for_a_bad_composer() -> None:
    async def bad_composer(**kwargs):
        provider = kwargs["provider"]
        await provider.structured([], TeacherProviderDraft)
        await provider.structured([], TeacherProviderDraft)
        raise AssertionError("second call must not complete")

    provider = RecordingProvider()
    with pytest.raises(TeacherRuntimeError) as captured:
        _run("为什么？", provider=provider, composer=bad_composer)

    assert captured.value.code == "PRIMARY_PROVIDER_CALL_LIMIT_EXCEEDED"
    assert captured.value.trace is not None
    assert captured.value.trace.provider_call_count == 1
    assert len(provider.calls) == 1


def test_runtime_rejects_a_composer_move_that_drifts_from_pedagogy() -> None:
    async def drifting_composer(**_kwargs):
        return TeacherResponseDraft(
            text="drifted response",
            citations=(),
            teaching_move=TeacherMove.TRANSITION,
            follow_up_kind=FollowUpKind.NONE,
            provider_metadata=ProviderMetadata(provider="drift-test"),
        )

    provider = RecordingProvider()
    with pytest.raises(TeacherRuntimeError) as captured:
        _run("为什么？", provider=provider, composer=drifting_composer)

    assert captured.value.code == "TEACHER_RUNTIME_CONTRACT_MISMATCH"
    assert captured.value.trace is not None
    assert captured.value.trace.provider_call_count == 0
    assert provider.calls == []


def test_free_chat_channel_never_enters_teacher_runtime_pipeline() -> None:
    provider = RecordingProvider()
    resolver = StudentMoveResolver()

    with pytest.raises(TeacherRuntimeError) as captured:
        _run(
            "随便聊聊",
            provider=provider,
            channel="free_chat",
            student_move_resolver=resolver,
        )

    assert captured.value.code == "FREE_CHAT_OUT_OF_SCOPE"
    assert captured.value.trace is None
    assert resolver.classifier_calls == 0
    assert provider.calls == []


def test_trace_is_explainable_and_never_records_provider_secret() -> None:
    provider, result = _run("为什么？")
    trace_payload = result.runtime_trace.model_dump_json()

    assert result.runtime_trace.student_move_reason
    assert result.runtime_trace.pedagogy_decision.reason.value
    assert result.runtime_trace.provider_call_count == 1
    assert provider.api_key not in trace_payload
    assert "api_key" not in trace_payload
    assert "secret" not in trace_payload


def test_input_contract_hashes_match_the_formal_w2_receipt() -> None:
    expected = {
        "app/teaching/composer.py": (
            "266aa76bbaa5a58ccc5a8f2c18e6f7bd04f73b4342b8cd27202862cb153a5abc"
        ),
        "app/teaching/prompt.py": (
            "9ac7afb25015b80ab3ae660a0f7b442be9b71a762fc02eeb4c1df6c6839f9115"
        ),
    }
    for relative_path, expected_hash in expected.items():
        digest = hashlib.sha256((BACKEND_ROOT / relative_path).read_bytes()).hexdigest()
        assert digest == expected_hash


def test_runtime_contract_and_import_boundary_are_state_write_free() -> None:
    assert set(TeacherTurnResult.model_fields) == {
        "response_draft",
        "teaching_move",
        "citations_metadata",
        "transition_recommendation",
        "assessment_recommendation",
        "self_attested_signal",
        "runtime_trace",
    }
    source = inspect.getsource(runtime_module)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert {
        "active_teaching",
        "generic_knowledge_api",
        "main",
        "db",
        "orm",
        "free_chat_service",
    }.isdisjoint(imported_modules)
    assert all(
        forbidden not in source
        for forbidden in (
            "SessionLocal",
            "mastery_update",
            "task_complete",
            "plan_update",
            "knowledge_map",
        )
    )
