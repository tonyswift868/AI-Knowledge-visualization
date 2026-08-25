from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter

from .contracts import CacheEvent, CacheIdentity, CacheLayer, CacheMissReason
from .opportunity import (
    CacheOpportunityAnalyzer,
    CacheOpportunityReport,
    CacheOpportunityTrace,
    WorkloadKind,
)
from .profiles import (
    ReviewAction,
    ReviewActionCostTrace,
    ReviewCostProfile,
    TeacherCostMode,
    TeacherCostProfileReport,
    TeacherTurnCostTrace,
    build_review_cost_profile,
    build_teacher_cost_profile_report,
)

_TOKENS_PER_MILLION = Decimal(1_000_000)
_INPUT_HIT_RATE = Decimal("0.014")
_INPUT_MISS_RATE = Decimal("0.44")
_OUTPUT_RATE = Decimal("1.32")


@dataclass(frozen=True, slots=True)
class SyntheticBenchmarkConfig:
    teacher_turns: int = 10_000
    review_evaluations: int = 10_000
    stable_prefix_variants: int = 32
    learner_context_variants: int = 500
    evidence_variants: int = 200
    question_variants: int = 750
    currency: str = "USD"

    def __post_init__(self) -> None:
        for name in (
            "teacher_turns",
            "review_evaluations",
            "stable_prefix_variants",
            "learner_context_variants",
            "evidence_variants",
            "question_variants",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not str(self.currency or "").strip():
            raise ValueError("currency must not be empty")


@dataclass(frozen=True, slots=True)
class SyntheticBenchmarkResult:
    config: SyntheticBenchmarkConfig
    teacher_profile: TeacherCostProfileReport
    review_profile: ReviewCostProfile
    opportunity_report: CacheOpportunityReport
    elapsed_ms: Decimal
    data_classification: str = "synthetic_offline_estimate"
    disclaimer: str = (
        "Synthetic workload only; these metrics are not a real Provider hit rate "
        "or an official Provider bill."
    )
    real_provider_calls: int = 0

    def to_public_dict(self) -> dict[str, object]:
        return {
            "data_classification": self.data_classification,
            "disclaimer": self.disclaimer,
            "real_provider_calls": self.real_provider_calls,
            "configuration": {
                "teacher_turns": self.config.teacher_turns,
                "review_evaluations": self.config.review_evaluations,
                "stable_prefix_variants": self.config.stable_prefix_variants,
                "learner_context_variants": self.config.learner_context_variants,
                "evidence_variants": self.config.evidence_variants,
                "question_variants": self.config.question_variants,
                "currency": self.config.currency,
            },
            "teacher_cost_profile": self.teacher_profile.to_public_dict(),
            "review_cost_profile": self.review_profile.to_public_dict(),
            "cache_opportunity": self.opportunity_report.to_public_dict(),
            "elapsed_ms": str(self.elapsed_ms),
        }


def _input_cost(tokens: int, rate: Decimal) -> Decimal:
    return Decimal(tokens) * rate / _TOKENS_PER_MILLION


def _output_cost(tokens: int) -> Decimal:
    return Decimal(tokens) * _OUTPUT_RATE / _TOKENS_PER_MILLION


def _identity(
    *,
    layer: CacheLayer,
    namespace: str,
    artifact_id: str,
    stable: str | None = None,
    dynamic: str | None = None,
    evidence: str | None = None,
    question: str | None = None,
) -> CacheIdentity:
    return CacheIdentity(
        layer=layer,
        namespace=namespace,
        artifact_id=artifact_id,
        provider="openai_compatible"
        if layer == CacheLayer.PROVIDER_PROMPT_CACHE
        else None,
        model="deepseek-v4-flash"
        if layer == CacheLayer.PROVIDER_PROMPT_CACHE
        else None,
        prompt_version="synthetic-cache-observatory-v1",
        route_identity="synthetic-deepseek-route",
        stable_prefix_hash=stable,
        dynamic_context_hash=dynamic,
        evidence_hash=evidence,
        question_hash=question,
        parser_version="synthetic-parser-v1",
        evaluation_version="synthetic-evaluator-v1",
    )


def _event(
    *,
    event_id: str,
    trace_id: str,
    identity: CacheIdentity,
    hit: bool,
    reason: CacheMissReason | None = None,
    bytes_reused: int = 0,
    tokens_reused: int = 0,
    avoided_calls: int = 0,
    estimated_savings: Decimal = Decimal(0),
    currency: str = "USD",
    occurred_at_ms: int = 0,
) -> CacheEvent:
    return CacheEvent(
        event_id=event_id,
        trace_id=trace_id,
        identity=identity,
        hit=hit,
        miss_reason=None if hit else reason or CacheMissReason.UNKNOWN,
        bytes_reused=bytes_reused if hit else 0,
        tokens_reused=tokens_reused if hit else 0,
        avoided_provider_calls=avoided_calls if hit else 0,
        estimated_cost_avoided=estimated_savings if hit else Decimal(0),
        currency=currency,
        lookup_ms=Decimal("0.10"),
        occurred_at_ms=occurred_at_ms,
        synthetic=True,
    )


def _teacher_fixture(
    config: SyntheticBenchmarkConfig,
) -> tuple[list[TeacherTurnCostTrace], list[CacheOpportunityTrace]]:
    profiles: list[TeacherTurnCostTrace] = []
    opportunities: list[CacheOpportunityTrace] = []
    miss_cycle = (
        CacheMissReason.DYNAMIC_CONTEXT_CHANGED,
        CacheMissReason.EVIDENCE_CHANGED,
        CacheMissReason.MODEL_CHANGED,
        CacheMissReason.PROMPT_VERSION_CHANGED,
        CacheMissReason.ROUTE_CHANGED,
        CacheMissReason.EXPIRED,
        CacheMissReason.UNKNOWN,
    )
    for index in range(config.teacher_turns):
        trace_id = f"teacher-{index}"
        mode = TeacherCostMode.DEEP if index % 4 == 0 else TeacherCostMode.FAST
        if mode == TeacherCostMode.FAST:
            stable_tokens, dynamic_tokens, output_tokens = 900, 220, 120
        else:
            stable_tokens, dynamic_tokens, output_tokens = 1_500, 520, 360
        prompt_tokens = stable_tokens + dynamic_tokens
        stable_key = f"teacher-prefix-{index % config.stable_prefix_variants}"
        learner_key = f"learner-{index % config.learner_context_variants}"
        evidence_key = f"evidence-{index % config.evidence_variants}"
        full_key = f"teacher-request-{index % 400}"
        local_hit = index >= 400 and index % 41 == 0
        cacheable = index % 97 != 0
        provider_hit = (
            not local_hit
            and cacheable
            and index >= config.stable_prefix_variants
            and index % 5 != 0
        )
        events: list[CacheEvent] = []
        local_identity = _identity(
            layer=CacheLayer.LOCAL_RESULT_CACHE,
            namespace="teacher-result",
            artifact_id=full_key,
            stable=stable_key,
            dynamic=learner_key,
            evidence=evidence_key,
        )
        theoretical_stable = _input_cost(stable_tokens, _INPUT_MISS_RATE)
        theoretical_dynamic = _input_cost(dynamic_tokens, _INPUT_MISS_RATE)
        theoretical_output = _output_cost(output_tokens)
        theoretical_total = (
            theoretical_stable + theoretical_dynamic + theoretical_output
        )
        events.append(
            _event(
                event_id=f"{trace_id}:local",
                trace_id=trace_id,
                identity=local_identity,
                hit=local_hit,
                reason=(
                    CacheMissReason.COLD
                    if index < 400
                    else CacheMissReason.DYNAMIC_CONTEXT_CHANGED
                ),
                bytes_reused=2_048,
                avoided_calls=1,
                estimated_savings=theoretical_total,
                currency=config.currency,
                occurred_at_ms=index,
            )
        )
        if not local_hit:
            provider_identity = _identity(
                layer=CacheLayer.PROVIDER_PROMPT_CACHE,
                namespace="teacher-prompt",
                artifact_id=f"provider-{stable_key}-{learner_key}-{evidence_key}",
                stable=stable_key,
                dynamic=learner_key,
                evidence=evidence_key,
            )
            miss_reason = (
                CacheMissReason.NOT_CACHEABLE
                if not cacheable
                else CacheMissReason.COLD
                if index < config.stable_prefix_variants
                else miss_cycle[index % len(miss_cycle)]
            )
            provider_savings = _input_cost(
                stable_tokens, _INPUT_MISS_RATE - _INPUT_HIT_RATE
            )
            events.append(
                _event(
                    event_id=f"{trace_id}:provider",
                    trace_id=trace_id,
                    identity=provider_identity,
                    hit=provider_hit,
                    reason=miss_reason,
                    tokens_reused=stable_tokens,
                    estimated_savings=provider_savings,
                    currency=config.currency,
                    occurred_at_ms=index,
                )
            )
        evidence_hit = index >= config.evidence_variants and index % 6 != 0
        events.append(
            _event(
                event_id=f"{trace_id}:evidence",
                trace_id=trace_id,
                identity=_identity(
                    layer=CacheLayer.EVIDENCE_CACHE,
                    namespace="teacher-evidence",
                    artifact_id=evidence_key,
                    evidence=evidence_key,
                ),
                hit=evidence_hit,
                reason=(
                    CacheMissReason.COLD
                    if index < config.evidence_variants
                    else CacheMissReason.EVIDENCE_CHANGED
                ),
                bytes_reused=8_192,
                tokens_reused=180,
                currency=config.currency,
                occurred_at_ms=index,
            )
        )
        parse_hit = index >= config.evidence_variants and index % 10 != 0
        events.append(
            _event(
                event_id=f"{trace_id}:parse",
                trace_id=trace_id,
                identity=_identity(
                    layer=CacheLayer.PARSE_CACHE,
                    namespace="teacher-parse",
                    artifact_id=evidence_key,
                    evidence=evidence_key,
                ),
                hit=parse_hit,
                reason=(
                    CacheMissReason.COLD
                    if index < config.evidence_variants
                    else CacheMissReason.EVIDENCE_CHANGED
                ),
                bytes_reused=12_288,
                tokens_reused=220,
                currency=config.currency,
                occurred_at_ms=index,
            )
        )
        if local_hit:
            stable_cost = dynamic_cost = output_cost = Decimal(0)
            provider_calls = 0
            cached_input = 0
            ttft = None
        else:
            stable_cost = _input_cost(
                stable_tokens,
                _INPUT_HIT_RATE if provider_hit else _INPUT_MISS_RATE,
            )
            dynamic_cost = theoretical_dynamic
            output_cost = theoretical_output
            provider_calls = 1
            cached_input = stable_tokens if provider_hit else 0
            ttft = Decimal(140 + index % 180)
        profiles.append(
            TeacherTurnCostTrace(
                trace_id=trace_id,
                mode=mode,
                prompt_tokens=prompt_tokens,
                stable_prefix_tokens=stable_tokens,
                dynamic_tokens=dynamic_tokens,
                cached_input_tokens=cached_input,
                output_tokens=output_tokens,
                provider_call_count=provider_calls,
                estimated_actual_cost=stable_cost + dynamic_cost + output_cost,
                currency=config.currency,
                stable_prefix_estimated_cost=stable_cost,
                dynamic_input_estimated_cost=dynamic_cost,
                output_estimated_cost=output_cost,
                ttft_ms=ttft,
                estimation_basis="synthetic versioned pricing fixture",
                synthetic=True,
            )
        )
        opportunities.append(
            CacheOpportunityTrace(
                trace_id=trace_id,
                workload=WorkloadKind.TEACHER_TURN,
                cache_events=tuple(events),
                stable_prefix_key=stable_key,
                full_request_key=full_key,
                stable_prefix_tokens=stable_tokens,
                provider_input_tokens=prompt_tokens,
                provider_call_count=provider_calls,
                cacheable=cacheable,
                stable_prefix_reuse_estimated_savings=_input_cost(
                    stable_tokens, _INPUT_MISS_RATE - _INPUT_HIT_RATE
                ),
                duplicate_call_estimated_cost=theoretical_total,
                currency=config.currency,
                synthetic=True,
            )
        )
    return profiles, opportunities


def _local_review_trace(
    trace_id: str,
    evaluation_id: str,
    action: ReviewAction,
) -> ReviewActionCostTrace:
    return ReviewActionCostTrace(
        trace_id=trace_id,
        evaluation_id=evaluation_id,
        action=action,
        synthetic=True,
    )


def _review_fixture(
    config: SyntheticBenchmarkConfig,
) -> tuple[list[ReviewActionCostTrace], list[CacheOpportunityTrace]]:
    profiles: list[ReviewActionCostTrace] = []
    opportunities: list[CacheOpportunityTrace] = []
    for index in range(config.review_evaluations):
        trace_id = f"review-{index}"
        evaluation_id = f"evaluation-{index}"
        question_key = f"question-{index % config.question_variants}"
        learner_key = f"learner-{index % config.learner_context_variants}"
        evidence_key = f"evidence-{index % config.evidence_variants}"
        full_key = f"review-request-{index % 500}"
        semantic = index % 4 == 0
        artifact_hit = (
            semantic and index >= config.question_variants and index % 13 == 0
        )
        explanation = index % 5 == 0
        cacheable = index % 113 != 0
        events: list[CacheEvent] = []
        question_hit = index >= config.question_variants
        events.append(
            _event(
                event_id=f"{trace_id}:question",
                trace_id=trace_id,
                identity=_identity(
                    layer=CacheLayer.QUESTION_BANK_CACHE,
                    namespace="review-question-bank",
                    artifact_id=question_key,
                    question=question_key,
                ),
                hit=question_hit,
                reason=CacheMissReason.COLD,
                bytes_reused=1_024,
                tokens_reused=80,
                currency=config.currency,
                occurred_at_ms=index,
            )
        )
        semantic_input, semantic_output = 640, 80
        semantic_cost = _input_cost(semantic_input, _INPUT_MISS_RATE) + _output_cost(
            semantic_output
        )
        events.append(
            _event(
                event_id=f"{trace_id}:evaluation-artifact",
                trace_id=trace_id,
                identity=_identity(
                    layer=CacheLayer.EVALUATION_ARTIFACT_CACHE,
                    namespace="review-evaluation",
                    artifact_id=full_key,
                    dynamic=learner_key,
                    evidence=evidence_key,
                    question=question_key,
                ),
                hit=artifact_hit,
                reason=(
                    CacheMissReason.COLD
                    if index < config.question_variants
                    else CacheMissReason.DYNAMIC_CONTEXT_CHANGED
                ),
                bytes_reused=1_536,
                avoided_calls=1,
                estimated_savings=semantic_cost,
                currency=config.currency,
                occurred_at_ms=index,
            )
        )
        for action in (
            ReviewAction.QUESTION_READ,
            ReviewAction.QUESTION_BANK_SELECTION,
        ):
            profiles.append(
                _local_review_trace(f"{trace_id}:{action.value}", evaluation_id, action)
            )
        provider_calls = 0
        provider_input_per_call = 0
        provider_cost_per_call = Decimal(0)
        if semantic:
            semantic_called = not artifact_hit
            profiles.append(
                ReviewActionCostTrace(
                    trace_id=f"{trace_id}:semantic",
                    evaluation_id=evaluation_id,
                    action=ReviewAction.SEMANTIC_EVALUATION,
                    provider_call_count=int(semantic_called),
                    input_tokens=semantic_input if semantic_called else 0,
                    output_tokens=semantic_output if semantic_called else 0,
                    estimated_cost=semantic_cost if semantic_called else Decimal(0),
                    currency=config.currency,
                    synthetic=True,
                )
            )
            if semantic_called:
                provider_calls += 1
                provider_input_per_call = semantic_input
                provider_cost_per_call = semantic_cost
        else:
            profiles.append(
                _local_review_trace(
                    f"{trace_id}:objective",
                    evaluation_id,
                    ReviewAction.OBJECTIVE_EVALUATION,
                )
            )
        explanation_cost = _input_cost(820, _INPUT_MISS_RATE) + _output_cost(160)
        if explanation:
            profiles.append(
                ReviewActionCostTrace(
                    trace_id=f"{trace_id}:explanation",
                    evaluation_id=evaluation_id,
                    action=ReviewAction.TEACHER_EXPLANATION,
                    provider_call_count=1,
                    input_tokens=820,
                    output_tokens=160,
                    estimated_cost=explanation_cost,
                    currency=config.currency,
                    synthetic=True,
                )
            )
            provider_calls += 1
            provider_input_per_call = max(provider_input_per_call, 820)
            provider_cost_per_call = max(provider_cost_per_call, explanation_cost)
        for action in (ReviewAction.WEAK_QUEUE_UPDATE, ReviewAction.FSRS_UPDATE):
            profiles.append(
                _local_review_trace(f"{trace_id}:{action.value}", evaluation_id, action)
            )
        if provider_calls:
            stable_key = f"review-prefix-{index % config.stable_prefix_variants}"
            provider_hit = (
                cacheable and index >= config.stable_prefix_variants and index % 4 != 0
            )
            for call_index in range(provider_calls):
                events.append(
                    _event(
                        event_id=f"{trace_id}:provider:{call_index}",
                        trace_id=trace_id,
                        identity=_identity(
                            layer=CacheLayer.PROVIDER_PROMPT_CACHE,
                            namespace="review-provider-prompt",
                            artifact_id=f"{stable_key}:{call_index}",
                            stable=stable_key,
                            dynamic=learner_key,
                            evidence=evidence_key,
                            question=question_key,
                        ),
                        hit=provider_hit,
                        reason=(
                            CacheMissReason.NOT_CACHEABLE
                            if not cacheable
                            else CacheMissReason.COLD
                            if index < config.stable_prefix_variants
                            else CacheMissReason.DYNAMIC_CONTEXT_CHANGED
                        ),
                        tokens_reused=500,
                        estimated_savings=_input_cost(
                            500, _INPUT_MISS_RATE - _INPUT_HIT_RATE
                        ),
                        currency=config.currency,
                        occurred_at_ms=index,
                    )
                )
            stable_prefix_key: str | None = stable_key
        else:
            stable_prefix_key = f"review-prefix-{index % config.stable_prefix_variants}"
        opportunities.append(
            CacheOpportunityTrace(
                trace_id=trace_id,
                workload=WorkloadKind.REVIEW_EVALUATION,
                cache_events=tuple(events),
                stable_prefix_key=stable_prefix_key,
                full_request_key=full_key,
                stable_prefix_tokens=min(500, provider_input_per_call),
                provider_input_tokens=provider_input_per_call,
                provider_call_count=provider_calls,
                cacheable=cacheable,
                stable_prefix_reuse_estimated_savings=_input_cost(
                    min(500, provider_input_per_call),
                    _INPUT_MISS_RATE - _INPUT_HIT_RATE,
                ),
                duplicate_call_estimated_cost=provider_cost_per_call,
                currency=config.currency,
                synthetic=True,
            )
        )
    return profiles, opportunities


def run_synthetic_cache_benchmark(
    config: SyntheticBenchmarkConfig | None = None,
) -> SyntheticBenchmarkResult:
    """Run deterministic offline fixtures; no Provider or business mutation."""

    config = config or SyntheticBenchmarkConfig()
    started = perf_counter()
    teacher_traces, teacher_opportunities = _teacher_fixture(config)
    review_traces, review_opportunities = _review_fixture(config)
    opportunity_report = CacheOpportunityAnalyzer().analyze(
        (*teacher_opportunities, *review_opportunities),
        source_classification="synthetic_offline_estimate",
    )
    elapsed_ms = Decimal(str((perf_counter() - started) * 1_000))
    return SyntheticBenchmarkResult(
        config=config,
        teacher_profile=build_teacher_cost_profile_report(teacher_traces),
        review_profile=build_review_cost_profile(review_traces),
        opportunity_report=opportunity_report,
        elapsed_ms=elapsed_ms,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run offline cache opportunity benchmark"
    )
    parser.add_argument("--teacher-turns", type=int, default=10_000)
    parser.add_argument("--review-evaluations", type=int, default=10_000)
    arguments = parser.parse_args(argv)
    result = run_synthetic_cache_benchmark(
        SyntheticBenchmarkConfig(
            teacher_turns=arguments.teacher_turns,
            review_evaluations=arguments.review_evaluations,
        )
    )
    print(json.dumps(result.to_public_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
