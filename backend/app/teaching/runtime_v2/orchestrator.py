"""Standalone Teacher Runtime v2 orchestration without legacy callsite wiring."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from time import perf_counter
from typing import Any

from pydantic import BaseModel

from ..composer import (
    ConversationTurn,
    RelevantLearnerContext,
    TeacherComposerError,
    TeacherEvidence,
    TeacherProvider,
    TeacherResponseDraft,
    compose_teacher_response,
)
from ..contracts import (
    AssessmentState,
    ExampleStrategy,
    LearnerContext,
    StudentMove,
    TeacherMove,
    TeachingBrief,
)
from ..examples import ExamplePlan, ExampleRequest, plan_example
from ..pedagogy import plan_teaching
from ..student_move import (
    StudentMoveContext,
    StudentMoveResolution,
    StudentMoveResolver,
)
from .models import (
    ExampleChoiceTrace,
    PedagogyDecisionTrace,
    TeacherRuntimeChannel,
    TeacherRuntimeError,
    TeacherRuntimeTrace,
    TeacherTurnResult,
)


PedagogyPlanner = Callable[[LearnerContext], TeachingBrief]
ExamplePlanner = Callable[[ExampleRequest], ExamplePlan]
TeacherComposer = Callable[..., Awaitable[TeacherResponseDraft]]


_EXAMPLE_TEACHER_MOVES = frozenset(
    {
        TeacherMove.ANALOGY,
        TeacherMove.WORKED_EXAMPLE,
        TeacherMove.CONCRETE_EXAMPLE,
        TeacherMove.CODE_EXAMPLE,
        TeacherMove.COUNTEREXAMPLE,
        TeacherMove.CONNECT_PREREQUISITE,
        TeacherMove.TRANSFER_CHALLENGE,
        TeacherMove.SIMPLIFY,
        TeacherMove.DEEPEN,
        TeacherMove.CORRECT,
        TeacherMove.DIAGNOSE,
    }
)
_EXAMPLE_STUDENT_MOVES = frozenset(
    {
        StudentMove.ASK_EXAMPLE,
        StudentMove.ASK_DEEPER_EXPLANATION,
        StudentMove.CONFUSED,
        StudentMove.INCORRECT_ANSWER_SIGNAL,
        StudentMove.REQUEST_PRACTICAL_USE,
        StudentMove.REQUEST_SIMPLIFY,
    }
)


class _PrimaryProviderCallLimitError(Exception):
    code = "PRIMARY_PROVIDER_CALL_LIMIT_EXCEEDED"


class _SingleCallProvider:
    """Count and cap Composer-level primary Provider calls for one turn."""

    def __init__(self, provider: TeacherProvider) -> None:
        self._provider = provider
        self.call_count = 0
        self.provider_name = str(
            getattr(provider, "provider_name", "unknown") or "unknown"
        )
        self.model = getattr(provider, "model", None)

    @property
    def last_model(self) -> object | None:
        return getattr(self._provider, "last_model", None)

    async def structured(
        self,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
        temperature: float = 0.2,
    ) -> BaseModel:
        if self.call_count >= 1:
            raise _PrimaryProviderCallLimitError
        self.call_count += 1
        return await self._provider.structured(
            messages,
            response_model,
            temperature=temperature,
        )


def _channel_value(channel: TeacherRuntimeChannel | str) -> str:
    if isinstance(channel, TeacherRuntimeChannel):
        return channel.value
    return str(channel).strip().casefold()


def _should_plan_example(
    context: LearnerContext,
    brief: TeachingBrief,
    *,
    use_example_engine: bool,
) -> bool:
    return use_example_engine and (
        brief.example_strategy is not ExampleStrategy.NONE
        or brief.teaching_move in _EXAMPLE_TEACHER_MOVES
        or context.student_move in _EXAMPLE_STUDENT_MOVES
    )


def _example_trace(example_plan: ExamplePlan | None) -> ExampleChoiceTrace | None:
    if example_plan is None:
        return None
    return ExampleChoiceTrace(
        example_type=example_plan.example_type,
        depth=example_plan.depth,
        reason=example_plan.reason,
        semantic_family=example_plan.semantic_family,
        target_misconception=example_plan.target_misconception,
    )


def _pedagogy_trace(brief: TeachingBrief) -> PedagogyDecisionTrace:
    return PedagogyDecisionTrace(
        teaching_move=brief.teaching_move,
        supporting_moves=brief.supporting_moves,
        reason=brief.reason,
        transition_recommended=brief.transition_recommended,
        assessment_recommended=brief.assessment_recommended,
        preserve_current_learning_position=brief.preserve_current_learning_position,
    )


def _evidence_ids(
    brief: TeachingBrief,
    evidence: Sequence[TeacherEvidence],
) -> tuple[str, ...]:
    available = {item.evidence_id for item in evidence}
    return tuple(
        evidence_id
        for evidence_id in dict.fromkeys(brief.relevant_evidence_ids)
        if evidence_id in available
    )


def _prompt_context(
    context: LearnerContext,
    example_plan: ExamplePlan | None,
) -> RelevantLearnerContext:
    notes: tuple[str, ...] = ()
    if example_plan is not None:
        serialized = json.dumps(
            example_plan.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        notes = (f"Application-owned ExamplePlan intent: {serialized}",)
    return RelevantLearnerContext(
        learner_level=context.learner_level,
        known_prerequisites=context.known_prerequisites,
        relevant_notes=notes,
    )


def _runtime_trace(
    *,
    resolution: StudentMoveResolution,
    brief: TeachingBrief,
    example_plan: ExamplePlan | None,
    provider_call_count: int,
    evidence_ids: tuple[str, ...],
    assessment_state: AssessmentState,
    started_at: float,
) -> TeacherRuntimeTrace:
    return TeacherRuntimeTrace(
        student_move=resolution.primary_move,
        student_move_source=resolution.source,
        student_move_confidence=resolution.confidence,
        student_move_ambiguous=resolution.ambiguous,
        student_move_reason=resolution.reason,
        pedagogy_decision=_pedagogy_trace(brief),
        example_choice=_example_trace(example_plan),
        provider_call_count=provider_call_count,
        evidence_ids=evidence_ids,
        assessment_state=assessment_state,
        elapsed_ms=max(0.0, (perf_counter() - started_at) * 1_000),
    )


def _safe_error_details(error: Exception) -> dict[str, Any]:
    details = getattr(error, "details", {})
    if not isinstance(details, dict):
        return {"cause": type(error).__name__}
    allowed = {
        key: value
        for key, value in details.items()
        if key in {"cause", "missing_evidence_ids", "reason"}
    }
    return allowed or {"cause": type(error).__name__}


async def run_teacher_turn(
    *,
    latest_user_message: str,
    learner_context: LearnerContext,
    provider: TeacherProvider,
    evidence: Sequence[TeacherEvidence] = (),
    assessment_state: AssessmentState | None = None,
    explicit_action: StudentMove | str | None = None,
    recent_conversation: Sequence[ConversationTurn] = (),
    channel: TeacherRuntimeChannel | str = TeacherRuntimeChannel.LEARNING,
    use_example_engine: bool = True,
    student_move_resolver: StudentMoveResolver | None = None,
    pedagogy_planner: PedagogyPlanner = plan_teaching,
    example_planner: ExamplePlanner = plan_example,
    composer: TeacherComposer = compose_teacher_response,
    temperature: float = 0.2,
) -> TeacherTurnResult:
    """Resolve, plan, optionally select an example, and compose one turn."""

    if _channel_value(channel) != TeacherRuntimeChannel.LEARNING.value:
        raise TeacherRuntimeError(
            "FREE_CHAT_OUT_OF_SCOPE",
            details={"reason": "Teacher Runtime only accepts learning turns"},
        )

    started_at = perf_counter()
    resolver = student_move_resolver or StudentMoveResolver()
    resolution = resolver.resolve(
        latest_user_message,
        explicit_action=explicit_action,
        context=StudentMoveContext(
            current_concept=learner_context.current_concept,
            previous_teacher_move=(
                learner_context.recent_teaching_moves[-1]
                if learner_context.recent_teaching_moves
                else None
            ),
            current_task=learner_context.current_task,
        ),
    )
    turn_assessment_state = assessment_state or learner_context.assessment_state
    turn_context = learner_context.model_copy(
        update={
            "student_move": resolution.primary_move,
            "assessment_state": turn_assessment_state,
        }
    )
    brief = pedagogy_planner(turn_context)
    example_plan = (
        example_planner(ExampleRequest.from_teaching(turn_context, brief))
        if _should_plan_example(
            turn_context,
            brief,
            use_example_engine=use_example_engine,
        )
        else None
    )
    selected_evidence_ids = _evidence_ids(brief, evidence)
    counted_provider = _SingleCallProvider(provider)

    try:
        response = await composer(
            provider=counted_provider,
            teaching_brief=brief,
            latest_user_message=latest_user_message,
            evidence=evidence,
            learner_context=_prompt_context(turn_context, example_plan),
            recent_conversation=recent_conversation,
            temperature=temperature,
        )
        if response.teaching_move is not brief.teaching_move:
            mismatch = ValueError("Composer teaching move differs from Pedagogy")
            setattr(mismatch, "code", "TEACHER_RUNTIME_CONTRACT_MISMATCH")
            raise mismatch
    except Exception as error:
        trace = _runtime_trace(
            resolution=resolution,
            brief=brief,
            example_plan=example_plan,
            provider_call_count=counted_provider.call_count,
            evidence_ids=selected_evidence_ids,
            assessment_state=turn_assessment_state,
            started_at=started_at,
        )
        code = str(
            getattr(
                error,
                "code",
                "TEACHER_RESPONSE_INVALID"
                if isinstance(error, TeacherComposerError)
                else "TEACHER_RUNTIME_FAILED",
            )
        )
        raise TeacherRuntimeError(
            code,
            trace=trace,
            details=_safe_error_details(error),
        ) from error

    trace = _runtime_trace(
        resolution=resolution,
        brief=brief,
        example_plan=example_plan,
        provider_call_count=counted_provider.call_count,
        evidence_ids=selected_evidence_ids,
        assessment_state=turn_assessment_state,
        started_at=started_at,
    )
    return TeacherTurnResult(
        response_draft=response,
        teaching_move=brief.teaching_move,
        citations_metadata=response.citations,
        transition_recommendation=brief.transition_recommended,
        assessment_recommendation=brief.assessment_recommended,
        self_attested_signal=(
            resolution.primary_move is StudentMove.LEARNER_SAYS_MASTERED
        ),
        runtime_trace=trace,
    )


__all__ = ["run_teacher_turn"]
