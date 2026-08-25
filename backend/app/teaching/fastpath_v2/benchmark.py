"""Reproducible 10k-turn offline benchmark for Teacher Fast Path v2."""

from __future__ import annotations

import argparse
import asyncio
import math
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..application_v2 import LearnerContextSnapshot, TeacherTurnCommand
from ..composer import ConversationTurn, FollowUpKind, TeacherEvidence
from ..contracts import AssessmentState, LearnerContext, MasteryState, StudentMove
from ..runtime_v2 import (
    TeacherExecutionTrace,
    TeacherResponseChunk,
    TeacherRuntimeCancelledError,
)
from ..session_v2 import InMemoryClassroomSessionStore, SessionTurnCommand
from .context_budget import TeacherContextBudget
from .execution import TeacherExecutionService
from .models import TeacherLatencyBudget, TeacherPromptEnvelope, TeacherResponseMode
from .response_length import TeacherResponseLengthPolicy
from .runtime import run_streaming_teacher_turn


OFFLINE_BENCHMARK_DISCLAIMER = (
    "Fake offline benchmark measures local orchestration only; it does not "
    "represent real Provider latency, production TTFT, or billable token usage."
)


class _BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeacherMetricSummary(_BenchmarkModel):
    sample_count: int = Field(ge=1)
    p50: float = Field(ge=0.0)
    p95: float = Field(ge=0.0)
    minimum: float = Field(ge=0.0)
    maximum: float = Field(ge=0.0)


class TeacherOfflineBenchmarkReport(_BenchmarkModel):
    benchmark_kind: Literal["offline_fake_teacher_runtime_10k"] = (
        "offline_fake_teacher_runtime_10k"
    )
    turn_count: int = Field(ge=1)
    stream_attempt_count: int = Field(ge=1)
    completed_stream_count: int = Field(ge=1)
    duplicate_replay_count: int = Field(ge=0)
    cancelled_turn_count: int = Field(ge=0)
    concurrent_session_count: int = Field(ge=0)
    resumed_session_count: int = Field(ge=0)
    fast_turn_count: int = Field(ge=0)
    deep_turn_count: int = Field(ge=0)
    fast_ratio: float = Field(ge=0.0, le=1.0)
    deep_ratio: float = Field(ge=0.0, le=1.0)
    primary_provider_calls: int = Field(ge=0)
    expected_primary_provider_calls: int = Field(ge=0)
    provider_call_invariant_passed: bool
    routing_provider_calls: Literal[0] = 0
    real_provider_calls: Literal[0] = 0
    stable_prefix_unique_count: int = Field(ge=1)
    dynamic_tail_unique_count: int = Field(ge=1)
    prompt_token_budget: int = Field(ge=1)
    context_item_variations: tuple[int, ...]
    history_turn_variations: tuple[int, ...]
    evidence_size_variations: tuple[int, ...]
    student_move_variations: tuple[StudentMove, ...]
    orchestration_latency_ms: TeacherMetricSummary
    time_to_first_chunk_ms: TeacherMetricSummary
    runtime_overhead_ms: TeacherMetricSummary
    context_item_distribution: TeacherMetricSummary
    estimated_prompt_token_distribution: TeacherMetricSummary
    disclaimer: Literal[
        "Fake offline benchmark measures local orchestration only; it does not represent real Provider latency, production TTFT, or billable token usage."
    ] = OFFLINE_BENCHMARK_DISCLAIMER

    @model_validator(mode="after")
    def validate_accounting(self) -> "TeacherOfflineBenchmarkReport":
        if self.fast_turn_count + self.deep_turn_count != self.turn_count:
            raise ValueError("FAST and DEEP counts must account for every fake turn")
        if (
            self.completed_stream_count
            + self.duplicate_replay_count
            + self.cancelled_turn_count
            != self.turn_count
        ):
            raise ValueError("scenario counts must account for every fake turn")
        if self.stream_attempt_count + self.duplicate_replay_count != self.turn_count:
            raise ValueError("only duplicate replays may skip a stream attempt")
        if self.provider_call_invariant_passed != (
            self.primary_provider_calls == self.expected_primary_provider_calls
        ):
            raise ValueError("Provider call invariant flag disagrees with call counts")
        return self


def _metric(values: Sequence[int | float]) -> TeacherMetricSummary:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("benchmark metric requires at least one sample")

    def percentile(percent: float) -> float:
        index = max(0, math.ceil(percent * len(ordered)) - 1)
        return ordered[index]

    return TeacherMetricSummary(
        sample_count=len(ordered),
        p50=percentile(0.50),
        p95=percentile(0.95),
        minimum=ordered[0],
        maximum=ordered[-1],
    )


class _LeanBenchmarkStreamingProvider:
    provider_name = "offline-fastpath-benchmark"
    model = "no-network-fixture"

    def __init__(self) -> None:
        self.call_count = 0

    async def stream(
        self,
        envelope: TeacherPromptEnvelope,
        *,
        response_mode: TeacherResponseMode,
        temperature: float = 0.2,
    ) -> AsyncIterator[TeacherResponseChunk]:
        self.call_count += 1
        yield TeacherResponseChunk(sequence_number=0, text_delta="先看触发条件：")
        yield TeacherResponseChunk(
            sequence_number=1,
            text_delta="size 越过 threshold 时触发扩容。",
            is_final=True,
            cited_evidence_ids=envelope.allowed_evidence_ids[:1],
            follow_up_kind=FollowUpKind.NONE,
        )


class _CancellingBenchmarkProvider:
    provider_name = "offline-cancellation-benchmark"
    model = "no-network-fixture"

    def __init__(self) -> None:
        self.call_count = 0

    async def stream(
        self,
        envelope: TeacherPromptEnvelope,
        *,
        response_mode: TeacherResponseMode,
        temperature: float = 0.2,
    ) -> AsyncIterator[TeacherResponseChunk]:
        self.call_count += 1
        raise asyncio.CancelledError
        yield TeacherResponseChunk(sequence_number=0)  # pragma: no cover


class _BenchmarkClassroomState:
    def __init__(self, session_ids: Sequence[str], evidence_id: str) -> None:
        self.positions = {session_id: "position-1" for session_id in session_ids}
        self.context = LearnerContext(
            current_concept="HashMap threshold",
            learning_goal="Distinguish threshold from capacity",
            learner_level="offline-concurrent-benchmark",
            mastery_state=MasteryState.IN_PROGRESS,
            student_move=StudentMove.ASK_WHY,
            evidence_available=True,
            relevant_evidence_ids=(evidence_id,),
            assessment_state=AssessmentState.READY,
        )
        self.commit_calls: list[dict[str, Any]] = []

    async def load(self, session_id: str) -> LearnerContextSnapshot:
        return LearnerContextSnapshot(
            position_token=self.positions[session_id],
            context=self.context,
        )

    async def current_position(self, session_id: str) -> str:
        return self.positions[session_id]

    async def load_state(self, session_id: str) -> AssessmentState:
        return AssessmentState.READY

    async def commit(self, **kwargs: Any) -> tuple[str, ...]:
        self.commit_calls.append(kwargs)
        return tuple(event.event_id for event in kwargs["events"])


class TeacherFastPathBenchmarkHarness:
    """Exercise production orchestration with zero-network fake streams."""

    _MOVES = (
        StudentMove.ASK_WHY,
        StudentMove.CONFUSED,
        StudentMove.ASK_EXAMPLE,
        StudentMove.REQUEST_SIMPLIFY,
        StudentMove.RELATED_TANGENT,
        StudentMove.ASK_DEFINITION,
        StudentMove.REQUEST_PRACTICAL_USE,
        StudentMove.CONTINUE,
        StudentMove.ASK_DEEPER_EXPLANATION,
    )
    _MESSAGES = (
        "为什么 threshold 不是 capacity？",
        "我没懂",
        "换个例子",
        "简单说一下",
        "顺便问一下 ConcurrentHashMap",
        "HashMap 是什么？",
        "实际代码里怎么用？",
        "继续",
        "请严格证明并逐步推导摊还复杂度",
    )
    _CONTEXT_ITEMS = (0, 4, 12, 24)
    _HISTORY_TURNS = (0, 2, 8, 24)
    _EVIDENCE_SIZES = (64, 512, 2_048, 8_192)

    def __init__(
        self,
        *,
        latency_budget: TeacherLatencyBudget,
        context_budget: TeacherContextBudget,
        response_length_policy: TeacherResponseLengthPolicy,
    ) -> None:
        self._latency_budget = latency_budget
        self._context_budget = context_budget
        self._response_length_policy = response_length_policy

    def _variant(
        self, index: int
    ) -> tuple[
        str,
        LearnerContext,
        TeacherEvidence,
        tuple[ConversationTurn, ...],
        int,
    ]:
        variant = index % len(self._MOVES)
        context_size = self._CONTEXT_ITEMS[index % len(self._CONTEXT_ITEMS)]
        history_size = self._HISTORY_TURNS[
            (index // len(self._CONTEXT_ITEMS)) % len(self._HISTORY_TURNS)
        ]
        evidence_size = self._EVIDENCE_SIZES[
            (index // (len(self._CONTEXT_ITEMS) * len(self._HISTORY_TURNS)))
            % len(self._EVIDENCE_SIZES)
        ]
        evidence = TeacherEvidence(
            evidence_id="benchmark-evidence",
            content="E" * evidence_size,
            source_reference="fixture://benchmark/evidence",
        )
        context = LearnerContext(
            current_concept="HashMap threshold",
            learning_goal="Distinguish threshold from capacity",
            learner_level="offline-benchmark",
            known_prerequisites=tuple(
                f"prerequisite-{item}" for item in range(context_size)
            ),
            recent_mistakes=tuple(
                f"mistake-{item}" for item in range(context_size // 4)
            ),
            mastery_state=MasteryState.IN_PROGRESS,
            student_move=self._MOVES[variant],
            evidence_available=True,
            relevant_evidence_ids=(evidence.evidence_id,),
            assessment_state=AssessmentState.READY,
        )
        history = tuple(
            ConversationTurn(
                role="user" if item % 2 == 0 else "assistant",
                content=f"history-{item}: threshold discussion",
            )
            for item in range(history_size)
        )
        context_items = context_size + context_size // 4 + history_size
        return self._MESSAGES[variant], context, evidence, history, context_items

    async def _stream_turn(self, index: int, provider: object):
        message, context, evidence, history, context_items = self._variant(index)
        result = await run_streaming_teacher_turn(
            latest_user_message=message,
            learner_context=context,
            provider=provider,
            latency_budget=self._latency_budget,
            context_budget=self._context_budget,
            response_length_policy=self._response_length_policy,
            evidence=(evidence,),
            explicit_action=context.student_move,
            recent_conversation=history,
        )
        return result, context_items

    async def run(self, *, turn_count: int = 10_000) -> TeacherOfflineBenchmarkReport:
        if turn_count < 1:
            raise ValueError("turn_count must be positive")
        edge_count = min(100, turn_count // 4)
        duplicate_count = edge_count
        cancel_count = edge_count
        concurrent_count = edge_count
        sequential_count = turn_count - duplicate_count - cancel_count - concurrent_count
        provider = _LeanBenchmarkStreamingProvider()
        cancellation_providers: list[_CancellingBenchmarkProvider] = []
        ttft_samples: list[float] = []
        total_samples: list[float] = []
        overhead_samples: list[float] = []
        prompt_token_samples: list[int] = []
        context_item_samples: list[int] = []
        stable_hashes: set[str] = set()
        dynamic_hashes: set[str] = set()
        fast_count = 0
        deep_count = 0

        def record_trace(trace: TeacherExecutionTrace, *, context_items: int) -> None:
            nonlocal fast_count, deep_count
            if trace.mode == TeacherResponseMode.FAST.value:
                fast_count += 1
            else:
                deep_count += 1
            if trace.time_to_first_chunk_ms is not None:
                ttft_samples.append(trace.time_to_first_chunk_ms)
            total_samples.append(trace.total_latency_ms)
            prompt_token_samples.append(trace.estimated_prompt_tokens)
            context_item_samples.append(context_items)
            stable_hashes.add(trace.stable_prefix_hash)

        for index in range(sequential_count):
            result, context_items = await self._stream_turn(index, provider)
            receipt = result.runtime_trace.stream_receipt
            if receipt is None or receipt.execution_trace is None:
                raise RuntimeError("benchmark turn did not produce execution telemetry")
            record_trace(receipt.execution_trace, context_items=context_items)
            overhead_samples.append(receipt.composer_overhead_ms)
            dynamic_hashes.add(receipt.dynamic_tail_hash)

        if concurrent_count:
            session_ids = tuple(
                f"benchmark-session-{item}" for item in range(concurrent_count)
            )
            evidence = self._variant(0)[2]
            state = _BenchmarkClassroomState(session_ids, evidence.evidence_id)
            service = TeacherExecutionService.from_ports(
                learner_context_port=state,
                learning_event_sink_port=state,
                assessment_port=state,
                session_store=InMemoryClassroomSessionStore(),
                learning_position_port=state,
                latency_budget=self._latency_budget,
                context_budget=self._context_budget,
                response_length_policy=self._response_length_policy,
            )
            await asyncio.gather(*(service.create_session(item) for item in session_ids))
            await asyncio.gather(*(service.activate_session(item) for item in session_ids))
            commands = tuple(
                SessionTurnCommand(
                    sequence_number=1,
                    turn=TeacherTurnCommand(
                        turn_id=f"turn-{item}",
                        idempotency_key=f"idem-{item}",
                        session_id=session_id,
                        expected_learning_position="position-1",
                        latest_user_message="为什么会扩容？",
                        explicit_action=StudentMove.ASK_WHY,
                        evidence=(evidence,),
                    ),
                )
                for item, session_id in enumerate(session_ids)
            )
            executions = await asyncio.gather(
                *(
                    service.execute_turn(command, provider=provider)
                    for command in commands
                )
            )
            for execution in executions:
                record_trace(execution.execution_trace, context_items=1)
                overhead_samples.append(execution.stream_receipt.composer_overhead_ms)
                dynamic_hashes.add(execution.stream_receipt.dynamic_tail_hash)
            calls_before_replay = provider.call_count
            replays = await asyncio.gather(
                *(
                    service.execute_turn(command, provider=provider)
                    for command in commands[:duplicate_count]
                )
            )
            if any(not item.application_result.idempotent_replay for item in replays):
                raise RuntimeError("benchmark duplicate did not use idempotent replay")
            if provider.call_count != calls_before_replay:
                raise RuntimeError("duplicate replay repeated a Provider call")
            fast_count += duplicate_count
            await service.pause_session(session_ids[0])
            await service.resume_session(session_ids[0])

        for index in range(cancel_count):
            cancelling_provider = _CancellingBenchmarkProvider()
            cancellation_providers.append(cancelling_provider)
            try:
                await self._stream_turn(sequential_count + index, cancelling_provider)
            except TeacherRuntimeCancelledError as error:
                receipt = error.trace.stream_receipt
                if receipt is None or receipt.execution_trace is None:
                    raise RuntimeError("cancelled benchmark turn lost telemetry") from error
                _, _, _, _, context_items = self._variant(sequential_count + index)
                record_trace(receipt.execution_trace, context_items=context_items)
                overhead_samples.append(receipt.composer_overhead_ms)
                dynamic_hashes.add(receipt.dynamic_tail_hash)
            else:  # pragma: no cover - fail closed if cancellation fixture drifts
                raise RuntimeError("cancellation benchmark unexpectedly completed")

        cancellation_calls = sum(item.call_count for item in cancellation_providers)
        actual_calls = provider.call_count + cancellation_calls
        expected_calls = sequential_count + concurrent_count + cancel_count
        if fast_count + deep_count != turn_count:
            raise RuntimeError("benchmark route accounting drifted from turn count")
        if actual_calls != expected_calls:
            raise RuntimeError("benchmark Provider call count drifted from invariant")
        completed_count = sequential_count + concurrent_count
        return TeacherOfflineBenchmarkReport(
            turn_count=turn_count,
            stream_attempt_count=expected_calls,
            completed_stream_count=completed_count,
            duplicate_replay_count=duplicate_count,
            cancelled_turn_count=cancel_count,
            concurrent_session_count=concurrent_count,
            resumed_session_count=1 if concurrent_count else 0,
            fast_turn_count=fast_count,
            deep_turn_count=deep_count,
            fast_ratio=fast_count / turn_count,
            deep_ratio=deep_count / turn_count,
            primary_provider_calls=actual_calls,
            expected_primary_provider_calls=expected_calls,
            provider_call_invariant_passed=actual_calls == expected_calls,
            stable_prefix_unique_count=len(stable_hashes),
            dynamic_tail_unique_count=len(dynamic_hashes),
            prompt_token_budget=self._context_budget.maximum_prompt_tokens,
            context_item_variations=self._CONTEXT_ITEMS,
            history_turn_variations=self._HISTORY_TURNS,
            evidence_size_variations=self._EVIDENCE_SIZES,
            student_move_variations=self._MOVES,
            orchestration_latency_ms=_metric(total_samples),
            time_to_first_chunk_ms=_metric(ttft_samples),
            runtime_overhead_ms=_metric(overhead_samples),
            context_item_distribution=_metric(context_item_samples),
            estimated_prompt_token_distribution=_metric(prompt_token_samples),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, default=10_000)
    parser.add_argument("--fast-ttft-ms", type=float, required=True)
    parser.add_argument("--fast-total-ms", type=float, required=True)
    parser.add_argument("--deep-ttft-ms", type=float, required=True)
    parser.add_argument("--deep-total-ms", type=float, required=True)
    parser.add_argument("--timeout-risk-ms", type=float, required=True)
    parser.add_argument("--prompt-token-budget", type=int, required=True)
    parser.add_argument("--evidence-token-budget", type=int, required=True)
    parser.add_argument("--history-token-budget", type=int, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    latency_budget = TeacherLatencyBudget(
        budget_id="caller-supplied-offline-benchmark-budget",
        fast_ttft_expected_ms=args.fast_ttft_ms,
        fast_total_expected_ms=args.fast_total_ms,
        deep_ttft_expected_ms=args.deep_ttft_ms,
        deep_total_expected_ms=args.deep_total_ms,
        timeout_risk_ms=args.timeout_risk_ms,
    )
    context_budget = TeacherContextBudget(
        budget_id="caller-supplied-offline-context-budget",
        maximum_prompt_tokens=args.prompt_token_budget,
        maximum_evidence_tokens=args.evidence_token_budget,
        maximum_history_tokens=args.history_token_budget,
    )
    length_policy = TeacherResponseLengthPolicy(
        fast_clarification_target_tokens=80,
        fast_why_target_tokens=160,
        fast_example_target_tokens=220,
        fast_default_target_tokens=140,
        fast_maximum_output_tokens=320,
        deep_target_tokens=600,
        deep_maximum_output_tokens=1_200,
    )
    report = asyncio.run(
        TeacherFastPathBenchmarkHarness(
            latency_budget=latency_budget,
            context_budget=context_budget,
            response_length_policy=length_policy,
        ).run(turn_count=args.turns)
    )
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "OFFLINE_BENCHMARK_DISCLAIMER",
    "TeacherFastPathBenchmarkHarness",
    "TeacherMetricSummary",
    "TeacherOfflineBenchmarkReport",
]
