from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .contracts import CacheLayer, CacheMissReason, CacheObservatoryError
from .opportunity import (
    CacheOpportunityAnalyzer,
    CacheOpportunityTrace,
    WorkloadKind,
)
from .profiles import LOCAL_REVIEW_ACTIONS, ReviewAction, ReviewActionCostTrace


class OptimizationRecommendationCode(str, Enum):
    STABILIZE_PREFIX_BOUNDARY = "STABILIZE_PREFIX_BOUNDARY"
    REDUCE_DYNAMIC_PREFIX_INPUT = "REDUCE_DYNAMIC_PREFIX_INPUT"
    VERSION_EVIDENCE_AT_SOURCE = "VERSION_EVIDENCE_AT_SOURCE"
    DEDUPE_IDENTICAL_REQUESTS = "DEDUPE_IDENTICAL_REQUESTS"
    REVIEW_NON_CACHEABLE_ROUTES = "REVIEW_NON_CACHEABLE_ROUTES"
    PRESERVE_LOCAL_OBJECTIVE_PATH = "PRESERVE_LOCAL_OBJECTIVE_PATH"
    CACHE_SEMANTIC_ARTIFACTS = "CACHE_SEMANTIC_ARTIFACTS"
    CACHE_TEACHER_EXPLANATIONS = "CACHE_TEACHER_EXPLANATIONS"


@dataclass(frozen=True, slots=True)
class OptimizationRecommendation:
    code: OptimizationRecommendationCode
    evidence_count: int
    message: str
    automatic_action: bool = False


@dataclass(frozen=True, slots=True)
class TeacherOptimizationReport:
    trace_count: int
    current_provider_prefix_hit_count: int
    current_provider_prefix_tokens_reused: int
    potential_stable_prefix_reuse: int
    potential_stable_prefix_tokens: int
    dynamic_context_miss_count: int
    evidence_churn_count: int
    avoidable_duplicate_calls: int
    non_cacheable_calls: int
    recommendations: tuple[OptimizationRecommendation, ...]
    source_classification: str
    behavior_changed: bool = False

    def to_public_dict(self) -> dict[str, object]:
        return {
            "trace_count": self.trace_count,
            "stable_prefix_reuse": {
                "current_hit_count": self.current_provider_prefix_hit_count,
                "current_tokens_reused": self.current_provider_prefix_tokens_reused,
                "potential_reuse": self.potential_stable_prefix_reuse,
                "potential_tokens": self.potential_stable_prefix_tokens,
            },
            "dynamic_context_miss_count": self.dynamic_context_miss_count,
            "evidence_churn_count": self.evidence_churn_count,
            "avoidable_duplicate_calls": self.avoidable_duplicate_calls,
            "non_cacheable_calls": self.non_cacheable_calls,
            "recommendations": [
                {
                    "code": item.code.value,
                    "evidence_count": item.evidence_count,
                    "message": item.message,
                    "automatic_action": item.automatic_action,
                }
                for item in self.recommendations
            ],
            "source_classification": self.source_classification,
            "behavior_changed": self.behavior_changed,
        }


def analyze_teacher_optimization(
    traces: tuple[CacheOpportunityTrace, ...] | list[CacheOpportunityTrace],
    *,
    source_classification: str = "offline_execution_traces",
) -> TeacherOptimizationReport:
    immutable_traces = tuple(traces)
    if any(trace.workload != WorkloadKind.TEACHER_TURN for trace in immutable_traces):
        raise CacheObservatoryError(
            "Teacher optimization accepts Teacher-turn traces only"
        )
    opportunity = CacheOpportunityAnalyzer().analyze(
        immutable_traces, source_classification=source_classification
    )
    provider = opportunity.current_cache_metrics.for_layer(
        CacheLayer.PROVIDER_PROMPT_CACHE
    )
    dynamic_misses = sum(
        event.layer == CacheLayer.PROVIDER_PROMPT_CACHE
        and event.miss_reason == CacheMissReason.DYNAMIC_CONTEXT_CHANGED
        for trace in immutable_traces
        for event in trace.cache_events
    )
    evidence_churn = sum(
        event.layer == CacheLayer.EVIDENCE_CACHE
        and event.miss_reason == CacheMissReason.EVIDENCE_CHANGED
        for trace in immutable_traces
        for event in trace.cache_events
    )
    evidence = (
        (
            OptimizationRecommendationCode.STABILIZE_PREFIX_BOUNDARY,
            opportunity.potential_stable_prefix_reuse,
            "Keep reusable instructions before dynamic Teacher context.",
        ),
        (
            OptimizationRecommendationCode.REDUCE_DYNAMIC_PREFIX_INPUT,
            dynamic_misses,
            "Inspect LearnerContext placement without removing required context.",
        ),
        (
            OptimizationRecommendationCode.VERSION_EVIDENCE_AT_SOURCE,
            evidence_churn,
            "Track source and evidence versions to isolate real Evidence churn.",
        ),
        (
            OptimizationRecommendationCode.DEDUPE_IDENTICAL_REQUESTS,
            opportunity.avoidable_duplicate_calls,
            "Deduplicate identical cacheable requests at an integration boundary.",
        ),
        (
            OptimizationRecommendationCode.REVIEW_NON_CACHEABLE_ROUTES,
            opportunity.non_cacheable_dynamic_calls,
            "Review non-cacheable routes individually before changing policy.",
        ),
    )
    return TeacherOptimizationReport(
        trace_count=len(immutable_traces),
        current_provider_prefix_hit_count=provider.hit_count,
        current_provider_prefix_tokens_reused=provider.tokens_reused,
        potential_stable_prefix_reuse=opportunity.potential_stable_prefix_reuse,
        potential_stable_prefix_tokens=opportunity.potential_stable_prefix_tokens,
        dynamic_context_miss_count=dynamic_misses,
        evidence_churn_count=evidence_churn,
        avoidable_duplicate_calls=opportunity.avoidable_duplicate_calls,
        non_cacheable_calls=opportunity.non_cacheable_dynamic_calls,
        recommendations=tuple(
            OptimizationRecommendation(code=code, evidence_count=count, message=message)
            for code, count, message in evidence
            if count > 0
        ),
        source_classification=source_classification,
    )


@dataclass(frozen=True, slots=True)
class ReviewOptimizationReport:
    evaluation_count: int
    question_lookup_local_count: int
    question_bank_selection_local_count: int
    objective_evaluation_local_count: int
    semantic_evaluation_count: int
    semantic_provider_call_count: int
    teacher_explanation_count: int
    teacher_explanation_provider_call_count: int
    current_zero_provider_evaluations: int
    current_zero_provider_share: Decimal | None
    achievable_local_path_evaluations: int
    achievable_zero_provider_review_share: Decimal | None
    recommendations: tuple[OptimizationRecommendation, ...]
    source_classification: str
    behavior_changed: bool = False

    def to_public_dict(self) -> dict[str, object]:
        ratio = lambda value: str(value) if value is not None else None
        return {
            "evaluation_count": self.evaluation_count,
            "actions": {
                "question_lookup_local": self.question_lookup_local_count,
                "question_bank_selection_local": (
                    self.question_bank_selection_local_count
                ),
                "objective_evaluation_local": self.objective_evaluation_local_count,
                "semantic_evaluation": {
                    "action_count": self.semantic_evaluation_count,
                    "provider_call_count": self.semantic_provider_call_count,
                },
                "teacher_explanation": {
                    "action_count": self.teacher_explanation_count,
                    "provider_call_count": (
                        self.teacher_explanation_provider_call_count
                    ),
                },
            },
            "current_zero_provider_evaluations": (
                self.current_zero_provider_evaluations
            ),
            "current_zero_provider_share": ratio(self.current_zero_provider_share),
            "achievable_local_path_evaluations": (
                self.achievable_local_path_evaluations
            ),
            "achievable_zero_provider_review_share": ratio(
                self.achievable_zero_provider_review_share
            ),
            "recommendations": [
                {
                    "code": item.code.value,
                    "evidence_count": item.evidence_count,
                    "message": item.message,
                    "automatic_action": item.automatic_action,
                }
                for item in self.recommendations
            ],
            "source_classification": self.source_classification,
            "behavior_changed": self.behavior_changed,
        }


def analyze_review_optimization(
    traces: tuple[ReviewActionCostTrace, ...] | list[ReviewActionCostTrace],
    *,
    source_classification: str = "offline_execution_traces",
) -> ReviewOptimizationReport:
    immutable_traces = tuple(traces)
    actions = Counter(trace.action for trace in immutable_traces)
    provider_calls = Counter()
    evaluation_calls: dict[str, int] = defaultdict(int)
    evaluation_actions: dict[str, set[ReviewAction]] = defaultdict(set)
    for trace in immutable_traces:
        provider_calls[trace.action] += trace.provider_call_count
        evaluation_calls[trace.evaluation_id] += trace.provider_call_count
        evaluation_actions[trace.evaluation_id].add(trace.action)
    evaluation_count = len(evaluation_actions)
    current_zero = sum(value == 0 for value in evaluation_calls.values())
    local_path_eligible = sum(
        ReviewAction.OBJECTIVE_EVALUATION in selected
        and selected.issubset(set(LOCAL_REVIEW_ACTIONS))
        for selected in evaluation_actions.values()
    )
    current_share = (
        Decimal(current_zero) / Decimal(evaluation_count) if evaluation_count else None
    )
    achievable_share = (
        Decimal(local_path_eligible) / Decimal(evaluation_count)
        if evaluation_count
        else None
    )
    recommendation_evidence = (
        (
            OptimizationRecommendationCode.PRESERVE_LOCAL_OBJECTIVE_PATH,
            local_path_eligible,
            "Preserve question lookup and objective evaluation as a local path.",
        ),
        (
            OptimizationRecommendationCode.CACHE_SEMANTIC_ARTIFACTS,
            provider_calls[ReviewAction.SEMANTIC_EVALUATION],
            "Evaluate semantic artifact reuse without changing evaluation semantics.",
        ),
        (
            OptimizationRecommendationCode.CACHE_TEACHER_EXPLANATIONS,
            provider_calls[ReviewAction.TEACHER_EXPLANATION],
            "Measure explanation reuse only for identical versioned inputs.",
        ),
    )
    return ReviewOptimizationReport(
        evaluation_count=evaluation_count,
        question_lookup_local_count=actions[ReviewAction.QUESTION_READ],
        question_bank_selection_local_count=actions[
            ReviewAction.QUESTION_BANK_SELECTION
        ],
        objective_evaluation_local_count=actions[ReviewAction.OBJECTIVE_EVALUATION],
        semantic_evaluation_count=actions[ReviewAction.SEMANTIC_EVALUATION],
        semantic_provider_call_count=provider_calls[ReviewAction.SEMANTIC_EVALUATION],
        teacher_explanation_count=actions[ReviewAction.TEACHER_EXPLANATION],
        teacher_explanation_provider_call_count=provider_calls[
            ReviewAction.TEACHER_EXPLANATION
        ],
        current_zero_provider_evaluations=current_zero,
        current_zero_provider_share=current_share,
        achievable_local_path_evaluations=local_path_eligible,
        achievable_zero_provider_review_share=achievable_share,
        recommendations=tuple(
            OptimizationRecommendation(code=code, evidence_count=count, message=message)
            for code, count, message in recommendation_evidence
            if count > 0
        ),
        source_classification=source_classification,
    )
