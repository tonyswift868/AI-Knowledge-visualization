"""Streaming Teacher runtime that preserves existing turn and state contracts."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from ..composer import (
    ConversationTurn,
    ProviderMetadata,
    TeacherCitation,
    TeacherEvidence,
    TeacherResponseDraft,
)
from ..contracts import (
    AssessmentState,
    EvidenceRequirement,
    ExampleStrategy,
    LearnerContext,
    StudentMove,
    TeacherMove,
)
from ..examples import ExamplePlan, ExampleRequest, plan_example
from ..pedagogy import plan_teaching
from ..runtime_v2 import (
    ExampleChoiceTrace,
    PedagogyDecisionTrace,
    TeacherLatencyClassification,
    TeacherCachedInputMetadata,
    TeacherExecutionTrace,
    TeacherResponseChunk,
    TeacherRuntimeChannel,
    TeacherRuntimeCancelledError,
    TeacherRuntimeError,
    TeacherRuntimeTrace,
    TeacherStreamReceipt,
    TeacherStreamStatus,
    TeacherTurnResult,
)
from ..student_move import StudentMoveContext, StudentMoveResolution, StudentMoveResolver
from .latency import TeacherLatencyClassifier
from .context_budget import (
    ContextSelectionReceipt,
    ContextBudgetPlanner,
    TeacherContextBudget,
    TeacherContextBudgetError,
    estimate_text_tokens,
)
from .degradation import TeacherDegradationPolicy
from .models import (
    TeacherLatencyBudget,
    TeacherPromptEnvelope,
    TeacherResponseMode,
    TeacherResponseModeRequest,
)
from .policy import TeacherResponseModeResolver
from .ports import (
    PerformanceClockPort,
    StreamingTeacherPort,
    SystemPerformanceClock,
    TeacherChunkObserverPort,
)
from .prompt import TeacherPromptAssemblyError, build_teacher_prompt_envelope
from .response_length import (
    TeacherResponseLengthPolicy,
)


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
_EVIDENCE_CONFLICT_PATTERN = re.compile(
    r"(?:证据|资料|来源).{0,20}(?:冲突|矛盾)|evidence.{0,20}conflict",
    re.IGNORECASE,
)
_MULTI_SOURCE_PATTERN = re.compile(
    r"(?:综合|对比).{0,20}(?:来源|资料|证据)|multiple\s+sources|cross[- ]source",
    re.IGNORECASE,
)


def _should_plan_example(
    context: LearnerContext,
    brief,
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


def _pedagogy_trace(brief) -> PedagogyDecisionTrace:
    return PedagogyDecisionTrace(
        teaching_move=brief.teaching_move,
        supporting_moves=brief.supporting_moves,
        reason=brief.reason,
        transition_recommended=brief.transition_recommended,
        assessment_recommended=brief.assessment_recommended,
        preserve_current_learning_position=brief.preserve_current_learning_position,
    )


def _evidence_source_count(evidence: Sequence[TeacherEvidence]) -> int:
    sources = {
        item.source_reference or item.citation or f"evidence:{item.evidence_id}"
        for item in evidence
    }
    return len(sources)


def _evidence_sufficient(
    context: LearnerContext,
    evidence: Sequence[TeacherEvidence],
) -> bool:
    available = {item.evidence_id for item in evidence}
    required = set(context.relevant_evidence_ids)
    return context.evidence_available and bool(required) and required.issubset(available)


def _context_item_count(context: LearnerContext) -> int:
    payload = context.model_dump(mode="json")
    return sum(len(value) if isinstance(value, (list, tuple, dict)) else 1 for value in payload.values())


def _response_mode_request(
    *,
    latest_user_message: str,
    resolution: StudentMoveResolution,
    context: LearnerContext,
    evidence: Sequence[TeacherEvidence],
) -> TeacherResponseModeRequest:
    source_count = _evidence_source_count(evidence)
    return TeacherResponseModeRequest(
        latest_user_message=latest_user_message,
        student_move=resolution.primary_move,
        evidence_count=len(evidence),
        evidence_source_count=source_count,
        evidence_sufficient=_evidence_sufficient(context, evidence),
        evidence_conflict=(
            source_count >= 2
            and _EVIDENCE_CONFLICT_PATTERN.search(latest_user_message) is not None
        ),
        requires_multi_source_reasoning=(
            source_count >= 2
            and _MULTI_SOURCE_PATTERN.search(latest_user_message) is not None
        ),
        simple_clarification=resolution.primary_move
        in {StudentMove.ASK_DEFINITION, StudentMove.REQUEST_SIMPLIFY},
        current_concept_follow_up=resolution.primary_move
        in {StudentMove.ASK_WHY, StudentMove.CONTINUE},
        context_item_count=_context_item_count(context),
    )


def _latency_classification(
    *,
    classifier: TeacherLatencyClassifier,
    mode: TeacherResponseMode,
    first_chunk_ms: float | None,
    total_ms: float,
    budget: TeacherLatencyBudget,
) -> TeacherLatencyClassification:
    return classifier.classify(
        response_mode=mode,
        time_to_first_chunk_ms=first_chunk_ms,
        total_response_ms=total_ms,
        budget=budget,
    )


def _receipt(
    *,
    envelope: TeacherPromptEnvelope,
    mode: TeacherResponseMode,
    status: TeacherStreamStatus,
    started_at: float,
    finished_at: float,
    provider_started_at: float,
    first_chunk_at: float | None,
    last_chunk_at: float | None,
    chunk_count: int,
    provider_wait_ms: float,
    provider_call_count: int,
    classifier: TeacherLatencyClassifier,
    budget: TeacherLatencyBudget,
    provider: object,
    context_receipt: ContextSelectionReceipt | None,
    cancelled: bool = False,
) -> TeacherStreamReceipt:
    total_ms = max(0.0, finished_at - started_at)
    wait_ms = min(total_ms, max(0.0, provider_wait_ms))
    first_ms = (
        max(0.0, first_chunk_at - started_at)
        if first_chunk_at is not None
        else None
    )
    classification = (
        TeacherLatencyClassification.TIMEOUT_RISK
        if status is TeacherStreamStatus.TIMED_OUT
        else _latency_classification(
            classifier=classifier,
            mode=mode,
            first_chunk_ms=first_ms,
            total_ms=total_ms,
            budget=budget,
        )
    )
    degradation = TeacherDegradationPolicy().decide(
        response_mode=mode,
        status=status,
        latency_classification=classification,
    )
    try:
        raw_cache_metadata = getattr(provider, "cached_input_metadata", None)
    except Exception:
        raw_cache_metadata = None
    try:
        cached_input_metadata = (
            TeacherCachedInputMetadata.model_validate(raw_cache_metadata)
            if raw_cache_metadata is not None
            else TeacherCachedInputMetadata()
        )
    except ValidationError:
        cached_input_metadata = TeacherCachedInputMetadata()
    stable_tokens = (
        context_receipt.stable_prefix_tokens
        if context_receipt is not None
        else estimate_text_tokens(envelope.stable_prefix.content)
    )
    dynamic_tokens = (
        context_receipt.dynamic_tokens
        if context_receipt is not None
        else estimate_text_tokens(envelope.dynamic_tail.content)
    )
    total_ms = max(0.0, finished_at - started_at)

    def offset(value: float | None) -> float | None:
        if value is None:
            return None
        return min(total_ms, max(0.0, value - started_at))

    execution_trace = TeacherExecutionTrace(
        mode=mode.value,
        prompt_identity=envelope.identity.prompt_identity,
        stable_prefix_hash=envelope.stable_prefix.stable_prefix_hash,
        estimated_prompt_tokens=stable_tokens + dynamic_tokens,
        stable_prefix_tokens=stable_tokens,
        dynamic_tokens=dynamic_tokens,
        evidence_tokens=(
            context_receipt.evidence_tokens if context_receipt is not None else 0
        ),
        context_budget_id=(
            context_receipt.budget_id if context_receipt is not None else None
        ),
        dropped_optional_context=(
            context_receipt.dropped_optional_context
            if context_receipt is not None
            else ()
        ),
        cached_input_metadata=cached_input_metadata,
        provider_start_offset_ms=offset(provider_started_at) or 0.0,
        first_chunk_offset_ms=offset(first_chunk_at),
        last_chunk_offset_ms=offset(last_chunk_at),
        time_to_first_chunk_ms=first_ms,
        total_latency_ms=total_ms,
        provider_calls=provider_call_count,
        cancelled=cancelled,
        timed_out=status is TeacherStreamStatus.TIMED_OUT,
        degradation_decision=degradation,
    )
    return TeacherStreamReceipt(
        response_mode=mode.value,
        status=status,
        prompt_identity=envelope.identity.prompt_identity,
        stable_prefix_hash=envelope.stable_prefix.stable_prefix_hash,
        dynamic_tail_hash=envelope.dynamic_tail.dynamic_tail_hash,
        time_to_first_chunk_ms=first_ms,
        total_response_ms=total_ms,
        chunk_count=chunk_count,
        provider_wait_ms=wait_ms,
        composer_overhead_ms=max(0.0, total_ms - wait_ms),
        provider_call_count=provider_call_count,
        latency_classification=classification,
        degradation_recommendation=degradation.fallback_recommendation,
        execution_trace=execution_trace,
    )


def _trace(
    *,
    resolution: StudentMoveResolution,
    brief,
    example_plan: ExamplePlan | None,
    evidence_ids: tuple[str, ...],
    assessment_state: AssessmentState,
    provider_call_count: int,
    elapsed_ms: float,
    stream_receipt: TeacherStreamReceipt | None,
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
        elapsed_ms=max(0.0, elapsed_ms),
        stream_receipt=stream_receipt,
    )


async def run_streaming_teacher_turn(
    *,
    latest_user_message: str,
    learner_context: LearnerContext,
    provider: StreamingTeacherPort,
    latency_budget: TeacherLatencyBudget,
    context_budget: TeacherContextBudget | None = None,
    response_length_policy: TeacherResponseLengthPolicy | None = None,
    evidence: Sequence[TeacherEvidence] = (),
    assessment_state: AssessmentState | None = None,
    explicit_action: StudentMove | str | None = None,
    recent_conversation: Sequence[ConversationTurn] = (),
    channel: TeacherRuntimeChannel | str = TeacherRuntimeChannel.LEARNING,
    use_example_engine: bool = True,
    response_mode_resolver: TeacherResponseModeResolver | None = None,
    context_budget_planner: ContextBudgetPlanner | None = None,
    latency_classifier: TeacherLatencyClassifier | None = None,
    chunk_observer: TeacherChunkObserverPort | None = None,
    clock: PerformanceClockPort | None = None,
    temperature: float = 0.2,
) -> TeacherTurnResult:
    """Execute one deterministic route and exactly one streamed Provider call."""

    channel_value = channel.value if isinstance(channel, TeacherRuntimeChannel) else str(channel).strip().casefold()
    if channel_value != TeacherRuntimeChannel.LEARNING.value:
        raise TeacherRuntimeError("FREE_CHAT_OUT_OF_SCOPE")
    timer = clock or SystemPerformanceClock()
    started_at = timer.now_ms()
    resolver = StudentMoveResolver()
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
    brief = plan_teaching(turn_context)
    example_plan = (
        plan_example(ExampleRequest.from_teaching(turn_context, brief))
        if _should_plan_example(
            turn_context,
            brief,
            use_example_engine=use_example_engine,
        )
        else None
    )
    mode_resolver = response_mode_resolver or TeacherResponseModeResolver()
    mode = mode_resolver.resolve(
        _response_mode_request(
            latest_user_message=latest_user_message,
            resolution=resolution,
            context=turn_context,
            evidence=evidence,
        )
    ).mode
    response_length = (
        response_length_policy.resolve(
            response_mode=mode,
            student_move=resolution.primary_move,
        )
        if response_length_policy is not None
        else None
    )
    classifier = latency_classifier or TeacherLatencyClassifier()
    context_selection_receipt: ContextSelectionReceipt | None = None
    evidence_ids = tuple(
        item for item in brief.relevant_evidence_ids if item in {e.evidence_id for e in evidence}
    )
    try:
        if context_budget is not None:
            context_plan = (context_budget_planner or ContextBudgetPlanner()).plan(
                budget=context_budget,
                teaching_brief=brief,
                learner_context=turn_context,
                latest_user_message=latest_user_message,
                evidence=evidence,
                assessment_state=turn_assessment_state,
                response_mode=mode,
                response_length=response_length,
                example_plan=example_plan,
                recent_conversation=recent_conversation,
            )
            envelope = context_plan.envelope
            context_selection_receipt = context_plan.receipt
        else:
            envelope = build_teacher_prompt_envelope(
                teaching_brief=brief,
                learner_context=turn_context,
                latest_user_message=latest_user_message,
                evidence=evidence,
                assessment_state=turn_assessment_state,
                response_mode=mode,
                response_length=response_length,
                example_plan=example_plan,
                recent_conversation=recent_conversation,
            )
    except (TeacherPromptAssemblyError, TeacherContextBudgetError) as error:
        trace = _trace(
            resolution=resolution,
            brief=brief,
            example_plan=example_plan,
            evidence_ids=evidence_ids,
            assessment_state=turn_assessment_state,
            provider_call_count=0,
            elapsed_ms=timer.now_ms() - started_at,
            stream_receipt=None,
        )
        raise TeacherRuntimeError(error.code, trace=trace, details=error.details) from error

    provider_call_count = 1
    provider_wait_ms = 0.0
    provider_started_at = timer.now_ms()
    first_chunk_at: float | None = None
    last_chunk_at: float | None = None
    chunks: list[TeacherResponseChunk] = []
    final_seen = False
    try:
        stream = provider.stream(
            envelope,
            response_mode=mode,
            temperature=temperature,
        )
        iterator = stream.__aiter__()
        while True:
            wait_started = timer.now_ms()
            try:
                raw_chunk = await anext(iterator)
            except TimeoutError:
                provider_wait_ms += max(0.0, timer.now_ms() - wait_started)
                raise
            except StopAsyncIteration:
                provider_wait_ms += max(0.0, timer.now_ms() - wait_started)
                break
            except Exception:
                provider_wait_ms += max(0.0, timer.now_ms() - wait_started)
                raise
            wait_finished = timer.now_ms()
            provider_wait_ms += max(0.0, wait_finished - wait_started)
            if first_chunk_at is None:
                first_chunk_at = wait_finished
            last_chunk_at = wait_finished
            chunk = TeacherResponseChunk.model_validate(raw_chunk)
            if final_seen:
                raise ValueError("chunk received after final chunk")
            if chunk.sequence_number != len(chunks):
                raise ValueError("stream chunk sequence is not contiguous")
            if chunk.is_final:
                final_seen = True
            chunks.append(chunk)
            if chunk_observer is not None:
                await chunk_observer.on_chunk(chunk)
    except TimeoutError as error:
        finished_at = timer.now_ms()
        receipt = _receipt(
            envelope=envelope,
            mode=mode,
            status=TeacherStreamStatus.TIMED_OUT,
            started_at=started_at,
            finished_at=finished_at,
            provider_started_at=provider_started_at,
            first_chunk_at=first_chunk_at,
            last_chunk_at=last_chunk_at,
            chunk_count=len(chunks),
            provider_wait_ms=provider_wait_ms,
            provider_call_count=provider_call_count,
            classifier=classifier,
            budget=latency_budget,
            provider=provider,
            context_receipt=context_selection_receipt,
        )
        trace = _trace(
            resolution=resolution,
            brief=brief,
            example_plan=example_plan,
            evidence_ids=evidence_ids,
            assessment_state=turn_assessment_state,
            provider_call_count=provider_call_count,
            elapsed_ms=finished_at - started_at,
            stream_receipt=receipt,
        )
        raise TeacherRuntimeError(
            "TEACHER_STREAM_TIMEOUT",
            trace=trace,
            details={
                "cause": type(error).__name__,
                "degradation_recommendation": receipt.degradation_recommendation,
            },
        ) from error
    except asyncio.CancelledError as error:
        finished_at = timer.now_ms()
        receipt = _receipt(
            envelope=envelope,
            mode=mode,
            status=TeacherStreamStatus.CANCELLED,
            started_at=started_at,
            finished_at=finished_at,
            provider_started_at=provider_started_at,
            first_chunk_at=first_chunk_at,
            last_chunk_at=last_chunk_at,
            chunk_count=len(chunks),
            provider_wait_ms=provider_wait_ms,
            provider_call_count=provider_call_count,
            classifier=classifier,
            budget=latency_budget,
            provider=provider,
            context_receipt=context_selection_receipt,
            cancelled=True,
        )
        trace = _trace(
            resolution=resolution,
            brief=brief,
            example_plan=example_plan,
            evidence_ids=evidence_ids,
            assessment_state=turn_assessment_state,
            provider_call_count=provider_call_count,
            elapsed_ms=finished_at - started_at,
            stream_receipt=receipt,
        )
        raise TeacherRuntimeCancelledError(trace=trace) from error
    except (ValidationError, ValueError, TypeError, AttributeError) as error:
        finished_at = timer.now_ms()
        receipt = _receipt(
            envelope=envelope,
            mode=mode,
            status=TeacherStreamStatus.INVALID,
            started_at=started_at,
            finished_at=finished_at,
            provider_started_at=provider_started_at,
            first_chunk_at=first_chunk_at,
            last_chunk_at=last_chunk_at,
            chunk_count=len(chunks),
            provider_wait_ms=provider_wait_ms,
            provider_call_count=provider_call_count,
            classifier=classifier,
            budget=latency_budget,
            provider=provider,
            context_receipt=context_selection_receipt,
        )
        trace = _trace(
            resolution=resolution,
            brief=brief,
            example_plan=example_plan,
            evidence_ids=evidence_ids,
            assessment_state=turn_assessment_state,
            provider_call_count=provider_call_count,
            elapsed_ms=finished_at - started_at,
            stream_receipt=receipt,
        )
        raise TeacherRuntimeError(
            "TEACHER_STREAM_INVALID",
            trace=trace,
            details={"cause": type(error).__name__},
        ) from error
    except Exception as error:
        finished_at = timer.now_ms()
        receipt = _receipt(
            envelope=envelope,
            mode=mode,
            status=TeacherStreamStatus.INVALID,
            started_at=started_at,
            finished_at=finished_at,
            provider_started_at=provider_started_at,
            first_chunk_at=first_chunk_at,
            last_chunk_at=last_chunk_at,
            chunk_count=len(chunks),
            provider_wait_ms=provider_wait_ms,
            provider_call_count=provider_call_count,
            classifier=classifier,
            budget=latency_budget,
            provider=provider,
            context_receipt=context_selection_receipt,
        )
        trace = _trace(
            resolution=resolution,
            brief=brief,
            example_plan=example_plan,
            evidence_ids=evidence_ids,
            assessment_state=turn_assessment_state,
            provider_call_count=provider_call_count,
            elapsed_ms=finished_at - started_at,
            stream_receipt=receipt,
        )
        raise TeacherRuntimeError(
            "TEACHER_STREAM_FAILED",
            trace=trace,
            details={"cause": type(error).__name__},
        ) from error

    try:
        if not chunks or not final_seen:
            raise ValueError("stream ended without final chunk")
        text = "".join(chunk.text_delta for chunk in chunks).strip()
        if not text:
            raise ValueError("stream response text is empty")
        if (
            response_length is not None
            and estimate_text_tokens(text)
            > response_length.maximum_output_tokens
        ):
            raise ValueError("stream response exceeds response length policy")
        final_chunk = chunks[-1]
        if not final_chunk.is_final:
            raise ValueError("final metadata must be the last chunk")
        unknown_ids = tuple(
            item
            for item in final_chunk.cited_evidence_ids
            if item not in envelope.allowed_evidence_ids
        )
        if unknown_ids:
            raise ValueError("stream cited evidence outside the prompt envelope")
        if (
            brief.evidence_requirement is EvidenceRequirement.REQUIRED
            and not final_chunk.cited_evidence_ids
        ):
            raise ValueError("required evidence was not cited")
        evidence_by_id = {item.evidence_id: item for item in evidence}
        citations = tuple(
            TeacherCitation(
                evidence_id=item,
                citation=evidence_by_id[item].citation,
                source_reference=evidence_by_id[item].source_reference,
            )
            for item in final_chunk.cited_evidence_ids
        )
        response = TeacherResponseDraft(
            text=text,
            citations=citations,
            teaching_move=brief.teaching_move,
            follow_up_kind=final_chunk.follow_up_kind,
            provider_metadata=ProviderMetadata(
                provider=str(getattr(provider, "provider_name", "unknown") or "unknown"),
                model=(
                    str(getattr(provider, "model"))
                    if getattr(provider, "model", None)
                    else None
                ),
            ),
        )
    except (ValidationError, ValueError, KeyError) as error:
        finished_at = timer.now_ms()
        receipt = _receipt(
            envelope=envelope,
            mode=mode,
            status=TeacherStreamStatus.INVALID,
            started_at=started_at,
            finished_at=finished_at,
            provider_started_at=provider_started_at,
            first_chunk_at=first_chunk_at,
            last_chunk_at=last_chunk_at,
            chunk_count=len(chunks),
            provider_wait_ms=provider_wait_ms,
            provider_call_count=provider_call_count,
            classifier=classifier,
            budget=latency_budget,
            provider=provider,
            context_receipt=context_selection_receipt,
        )
        trace = _trace(
            resolution=resolution,
            brief=brief,
            example_plan=example_plan,
            evidence_ids=evidence_ids,
            assessment_state=turn_assessment_state,
            provider_call_count=provider_call_count,
            elapsed_ms=finished_at - started_at,
            stream_receipt=receipt,
        )
        raise TeacherRuntimeError(
            "TEACHER_STREAM_INVALID",
            trace=trace,
            details={"cause": type(error).__name__},
        ) from error

    finished_at = timer.now_ms()
    receipt = _receipt(
        envelope=envelope,
        mode=mode,
        status=TeacherStreamStatus.COMPLETED,
        started_at=started_at,
        finished_at=finished_at,
        provider_started_at=provider_started_at,
        first_chunk_at=first_chunk_at,
        last_chunk_at=last_chunk_at,
        chunk_count=len(chunks),
        provider_wait_ms=provider_wait_ms,
        provider_call_count=provider_call_count,
        classifier=classifier,
        budget=latency_budget,
        provider=provider,
        context_receipt=context_selection_receipt,
    )
    trace = _trace(
        resolution=resolution,
        brief=brief,
        example_plan=example_plan,
        evidence_ids=evidence_ids,
        assessment_state=turn_assessment_state,
        provider_call_count=provider_call_count,
        elapsed_ms=finished_at - started_at,
        stream_receipt=receipt,
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


class TeacherFastPathRuntime:
    """Injectable Application runtime runner with explicit latency policy."""

    def __init__(
        self,
        *,
        latency_budget: TeacherLatencyBudget,
        context_budget: TeacherContextBudget | None = None,
        response_length_policy: TeacherResponseLengthPolicy | None = None,
        response_mode_resolver: TeacherResponseModeResolver | None = None,
        latency_classifier: TeacherLatencyClassifier | None = None,
        context_budget_planner: ContextBudgetPlanner | None = None,
        chunk_observer: TeacherChunkObserverPort | None = None,
        clock: PerformanceClockPort | None = None,
    ) -> None:
        self._latency_budget = latency_budget
        self._context_budget = context_budget
        self._response_length_policy = response_length_policy
        self._response_mode_resolver = response_mode_resolver
        self._latency_classifier = latency_classifier
        self._context_budget_planner = context_budget_planner
        self._chunk_observer = chunk_observer
        self._clock = clock

    async def __call__(self, **kwargs: Any) -> TeacherTurnResult:
        return await run_streaming_teacher_turn(
            **kwargs,
            latency_budget=self._latency_budget,
            context_budget=self._context_budget,
            response_length_policy=self._response_length_policy,
            response_mode_resolver=self._response_mode_resolver,
            latency_classifier=self._latency_classifier,
            context_budget_planner=self._context_budget_planner,
            chunk_observer=self._chunk_observer,
            clock=self._clock,
        )


__all__ = ["TeacherFastPathRuntime", "run_streaming_teacher_turn"]
