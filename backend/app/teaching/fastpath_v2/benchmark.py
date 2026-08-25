"""Reproducible offline benchmark for the deterministic Teacher FAST path."""

from __future__ import annotations

import argparse
import asyncio
import math
from collections.abc import AsyncIterator, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..composer import FollowUpKind, TeacherEvidence
from ..contracts import (
    AssessmentState,
    LearnerContext,
    MasteryState,
    StudentMove,
)
from ..runtime_v2 import TeacherResponseChunk
from .models import (
    TeacherLatencyBudget,
    TeacherPromptEnvelope,
    TeacherResponseMode,
)
from .runtime import run_streaming_teacher_turn


OFFLINE_BENCHMARK_DISCLAIMER = (
    "Fake offline benchmark measures local orchestration only; it does not "
    "represent real Provider latency or production TTFT."
)


class _BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeacherLatencyMetricSummary(_BenchmarkModel):
    p50_ms: float = Field(ge=0.0)
    p95_ms: float = Field(ge=0.0)
    minimum_ms: float = Field(ge=0.0)
    maximum_ms: float = Field(ge=0.0)


class TeacherOfflineBenchmarkReport(_BenchmarkModel):
    benchmark_kind: Literal["offline_fake_fast_path"] = "offline_fake_fast_path"
    turn_count: int = Field(ge=1)
    fast_turn_count: int = Field(ge=0)
    deep_turn_count: int = Field(ge=0)
    primary_provider_calls: int = Field(ge=0)
    routing_provider_calls: Literal[0] = 0
    stable_prefix_unique_count: int = Field(ge=1)
    dynamic_tail_unique_count: int = Field(ge=1)
    context_item_variations: tuple[int, ...]
    evidence_size_variations: tuple[int, ...]
    student_move_variations: tuple[StudentMove, ...]
    time_to_first_chunk: TeacherLatencyMetricSummary
    total_response: TeacherLatencyMetricSummary
    runtime_overhead: TeacherLatencyMetricSummary
    disclaimer: Literal[
        "Fake offline benchmark measures local orchestration only; it does not represent real Provider latency or production TTFT."
    ] = OFFLINE_BENCHMARK_DISCLAIMER


def _metric(values: Sequence[float]) -> TeacherLatencyMetricSummary:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("benchmark metric requires at least one sample")

    def percentile(percent: float) -> float:
        index = max(0, math.ceil(percent * len(ordered)) - 1)
        return ordered[index]

    return TeacherLatencyMetricSummary(
        p50_ms=percentile(0.50),
        p95_ms=percentile(0.95),
        minimum_ms=ordered[0],
        maximum_ms=ordered[-1],
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
        yield TeacherResponseChunk(
            sequence_number=0,
            text_delta="先看触发条件：",
        )
        yield TeacherResponseChunk(
            sequence_number=1,
            text_delta="size 越过 threshold 时触发扩容。",
            is_final=True,
            cited_evidence_ids=envelope.allowed_evidence_ids[:1],
            follow_up_kind=FollowUpKind.NONE,
        )


class TeacherFastPathBenchmarkHarness:
    """Exercise real local orchestration with a zero-network fake stream."""

    _MOVES = (
        StudentMove.ASK_WHY,
        StudentMove.CONFUSED,
        StudentMove.ASK_EXAMPLE,
        StudentMove.REQUEST_SIMPLIFY,
        StudentMove.RELATED_TANGENT,
        StudentMove.ASK_DEFINITION,
        StudentMove.REQUEST_PRACTICAL_USE,
        StudentMove.CONTINUE,
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
    )
    _CONTEXT_ITEMS = (0, 4, 12, 24)
    _EVIDENCE_SIZES = (64, 512, 2_048, 8_192)

    def __init__(self, *, latency_budget: TeacherLatencyBudget) -> None:
        self._latency_budget = latency_budget

    async def run(self, *, turn_count: int = 1_000) -> TeacherOfflineBenchmarkReport:
        if turn_count < 1:
            raise ValueError("turn_count must be positive")
        provider = _LeanBenchmarkStreamingProvider()
        ttft_samples: list[float] = []
        total_samples: list[float] = []
        overhead_samples: list[float] = []
        stable_hashes: set[str] = set()
        dynamic_hashes: set[str] = set()
        fast_count = 0
        deep_count = 0

        for index in range(turn_count):
            variant = index % len(self._MOVES)
            context_size = self._CONTEXT_ITEMS[index % len(self._CONTEXT_ITEMS)]
            evidence_size = self._EVIDENCE_SIZES[
                (index // len(self._CONTEXT_ITEMS)) % len(self._EVIDENCE_SIZES)
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
                mastery_state=MasteryState.IN_PROGRESS,
                student_move=self._MOVES[variant],
                evidence_available=True,
                relevant_evidence_ids=(evidence.evidence_id,),
                assessment_state=AssessmentState.READY,
            )
            result = await run_streaming_teacher_turn(
                latest_user_message=self._MESSAGES[variant],
                learner_context=context,
                provider=provider,
                latency_budget=self._latency_budget,
                evidence=(evidence,),
                explicit_action=self._MOVES[variant],
            )
            receipt = result.runtime_trace.stream_receipt
            if receipt is None or receipt.time_to_first_chunk_ms is None:
                raise RuntimeError("benchmark turn did not produce streaming telemetry")
            if receipt.response_mode == TeacherResponseMode.FAST.value:
                fast_count += 1
            else:
                deep_count += 1
            ttft_samples.append(receipt.time_to_first_chunk_ms)
            total_samples.append(receipt.total_response_ms)
            overhead_samples.append(receipt.composer_overhead_ms)
            stable_hashes.add(receipt.stable_prefix_hash)
            dynamic_hashes.add(receipt.dynamic_tail_hash)

        if fast_count != turn_count or deep_count != 0:
            raise RuntimeError("offline FAST benchmark unexpectedly selected DEEP")
        if provider.call_count != turn_count:
            raise RuntimeError("benchmark Provider call count drifted from turn count")
        return TeacherOfflineBenchmarkReport(
            turn_count=turn_count,
            fast_turn_count=fast_count,
            deep_turn_count=deep_count,
            primary_provider_calls=provider.call_count,
            stable_prefix_unique_count=len(stable_hashes),
            dynamic_tail_unique_count=len(dynamic_hashes),
            context_item_variations=self._CONTEXT_ITEMS,
            evidence_size_variations=self._EVIDENCE_SIZES,
            student_move_variations=self._MOVES,
            time_to_first_chunk=_metric(ttft_samples),
            total_response=_metric(total_samples),
            runtime_overhead=_metric(overhead_samples),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, default=1_000)
    parser.add_argument("--fast-ttft-ms", type=float, required=True)
    parser.add_argument("--fast-total-ms", type=float, required=True)
    parser.add_argument("--deep-ttft-ms", type=float, required=True)
    parser.add_argument("--deep-total-ms", type=float, required=True)
    parser.add_argument("--timeout-risk-ms", type=float, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    budget = TeacherLatencyBudget(
        budget_id="caller-supplied-offline-benchmark-budget",
        fast_ttft_expected_ms=args.fast_ttft_ms,
        fast_total_expected_ms=args.fast_total_ms,
        deep_ttft_expected_ms=args.deep_ttft_ms,
        deep_total_expected_ms=args.deep_total_ms,
        timeout_risk_ms=args.timeout_risk_ms,
    )
    report = asyncio.run(
        TeacherFastPathBenchmarkHarness(latency_budget=budget).run(
            turn_count=args.turns
        )
    )
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "OFFLINE_BENCHMARK_DISCLAIMER",
    "TeacherFastPathBenchmarkHarness",
    "TeacherLatencyMetricSummary",
    "TeacherOfflineBenchmarkReport",
]
