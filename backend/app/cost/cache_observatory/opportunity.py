from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .contracts import (
    CacheEvent,
    CacheLayer,
    CacheObservatoryError,
    CacheObservatoryReport,
    CurrencyAmount,
)
from .metrics import CacheMetricsAggregator


class WorkloadKind(str, Enum):
    TEACHER_TURN = "TEACHER_TURN"
    REVIEW_EVALUATION = "REVIEW_EVALUATION"


def _non_negative(name: str, value: int | Decimal) -> None:
    if isinstance(value, int) and not isinstance(value, bool):
        valid = value >= 0
    elif isinstance(value, Decimal):
        valid = value.is_finite() and value >= 0
    else:
        valid = False
    if not valid:
        raise CacheObservatoryError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class CacheOpportunityTrace:
    trace_id: str
    workload: WorkloadKind
    cache_events: tuple[CacheEvent, ...]
    stable_prefix_key: str | None
    full_request_key: str | None
    stable_prefix_tokens: int
    provider_input_tokens: int
    provider_call_count: int
    cacheable: bool
    stable_prefix_reuse_estimated_savings: Decimal
    duplicate_call_estimated_cost: Decimal
    currency: str | None
    synthetic: bool = False

    def __post_init__(self) -> None:
        trace_id = str(self.trace_id or "").strip()
        if not trace_id:
            raise CacheObservatoryError("trace_id must not be empty")
        object.__setattr__(self, "trace_id", trace_id)
        workload = (
            self.workload
            if isinstance(self.workload, WorkloadKind)
            else WorkloadKind(self.workload)
        )
        object.__setattr__(self, "workload", workload)
        object.__setattr__(
            self,
            "stable_prefix_key",
            str(self.stable_prefix_key or "").strip() or None,
        )
        object.__setattr__(
            self,
            "full_request_key",
            str(self.full_request_key or "").strip() or None,
        )
        for name in (
            "stable_prefix_tokens",
            "provider_input_tokens",
            "provider_call_count",
        ):
            _non_negative(name, getattr(self, name))
        for name in (
            "stable_prefix_reuse_estimated_savings",
            "duplicate_call_estimated_cost",
        ):
            _non_negative(name, getattr(self, name))
        if self.provider_call_count > 2:
            raise CacheObservatoryError(
                "one Teacher/Review trace supports at most two Provider actions"
            )
        if workload == WorkloadKind.TEACHER_TURN and self.provider_call_count > 1:
            raise CacheObservatoryError(
                "one Teacher turn supports at most one Provider action"
            )
        if self.stable_prefix_tokens > self.provider_input_tokens:
            raise CacheObservatoryError(
                "stable-prefix tokens cannot exceed Provider input tokens"
            )
        if any(event.trace_id != trace_id for event in self.cache_events):
            raise CacheObservatoryError(
                "cache events must belong to their opportunity trace"
            )
        currency = str(self.currency or "").strip().upper() or None
        if (
            self.stable_prefix_reuse_estimated_savings > 0
            or self.duplicate_call_estimated_cost > 0
        ) and currency is None:
            raise CacheObservatoryError("opportunity estimates require a currency")
        object.__setattr__(self, "currency", currency)


@dataclass(frozen=True, slots=True)
class CacheOpportunityReport:
    trace_count: int
    teacher_turn_traces: int
    review_evaluation_traces: int
    current_cache_metrics: CacheObservatoryReport
    potential_stable_prefix_reuse: int
    potential_stable_prefix_tokens: int
    potential_stable_prefix_estimated_savings: Decimal | None
    potential_stable_prefix_savings_by_currency: tuple[CurrencyAmount, ...]
    avoidable_duplicate_calls: int
    avoidable_duplicate_input_tokens: int
    avoidable_duplicate_estimated_cost: Decimal | None
    avoidable_duplicate_costs_by_currency: tuple[CurrencyAmount, ...]
    non_cacheable_dynamic_calls: int
    source_classification: str
    estimates_classification: str = "estimated"
    behavior_changed: bool = False

    def to_public_dict(self) -> dict[str, object]:
        return {
            "trace_count": self.trace_count,
            "teacher_turn_traces": self.teacher_turn_traces,
            "review_evaluation_traces": self.review_evaluation_traces,
            "current_hit": self.current_cache_metrics.to_public_dict(),
            "potential_stable_prefix_reuse": self.potential_stable_prefix_reuse,
            "potential_stable_prefix_tokens": self.potential_stable_prefix_tokens,
            "potential_stable_prefix_estimated_savings": (
                str(self.potential_stable_prefix_estimated_savings)
                if self.potential_stable_prefix_estimated_savings is not None
                else None
            ),
            "potential_stable_prefix_savings_by_currency": [
                {"currency": item.currency, "amount": str(item.amount)}
                for item in self.potential_stable_prefix_savings_by_currency
            ],
            "avoidable_duplicate_calls": self.avoidable_duplicate_calls,
            "avoidable_duplicate_input_tokens": self.avoidable_duplicate_input_tokens,
            "avoidable_duplicate_estimated_cost": (
                str(self.avoidable_duplicate_estimated_cost)
                if self.avoidable_duplicate_estimated_cost is not None
                else None
            ),
            "avoidable_duplicate_costs_by_currency": [
                {"currency": item.currency, "amount": str(item.amount)}
                for item in self.avoidable_duplicate_costs_by_currency
            ],
            "non_cacheable_dynamic_calls": self.non_cacheable_dynamic_calls,
            "source_classification": self.source_classification,
            "estimates_classification": self.estimates_classification,
            "behavior_changed": self.behavior_changed,
        }


class CacheOpportunityAnalyzer:
    """Pure offline analysis; it never writes cache or execution policy."""

    def analyze(
        self,
        traces: tuple[CacheOpportunityTrace, ...] | list[CacheOpportunityTrace],
        *,
        source_classification: str = "offline_execution_traces",
    ) -> CacheOpportunityReport:
        immutable_traces = tuple(traces)
        aggregator = CacheMetricsAggregator()
        seen_stable_prefixes: set[str] = set()
        seen_full_requests: set[str] = set()
        stable_count = 0
        stable_tokens = 0
        duplicate_calls = 0
        duplicate_tokens = 0
        non_cacheable_calls = 0
        stable_costs: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        duplicate_costs: dict[str, Decimal] = defaultdict(lambda: Decimal(0))

        for trace in immutable_traces:
            aggregator.extend(trace.cache_events)
            provider_prompt_miss_count = sum(
                event.layer == CacheLayer.PROVIDER_PROMPT_CACHE and not event.hit
                for event in trace.cache_events
            )
            local_result_hit = any(
                event.layer == CacheLayer.LOCAL_RESULT_CACHE and event.hit
                for event in trace.cache_events
            )
            if (
                trace.provider_call_count
                and trace.cacheable
                and trace.stable_prefix_key is not None
            ):
                if (
                    trace.stable_prefix_key in seen_stable_prefixes
                    and provider_prompt_miss_count
                ):
                    stable_count += provider_prompt_miss_count
                    stable_tokens += (
                        trace.stable_prefix_tokens * provider_prompt_miss_count
                    )
                    if trace.currency is not None:
                        stable_costs[trace.currency] += (
                            trace.stable_prefix_reuse_estimated_savings
                            * provider_prompt_miss_count
                        )
                seen_stable_prefixes.add(trace.stable_prefix_key)
            if trace.full_request_key is not None:
                if (
                    trace.provider_call_count
                    and trace.cacheable
                    and trace.full_request_key in seen_full_requests
                    and not local_result_hit
                ):
                    duplicate_calls += trace.provider_call_count
                    duplicate_tokens += (
                        trace.provider_input_tokens * trace.provider_call_count
                    )
                    if trace.currency is not None:
                        duplicate_costs[trace.currency] += (
                            trace.duplicate_call_estimated_cost
                            * trace.provider_call_count
                        )
                seen_full_requests.add(trace.full_request_key)
            if trace.provider_call_count and not trace.cacheable:
                non_cacheable_calls += trace.provider_call_count

        stable_totals = tuple(
            CurrencyAmount(currency=currency, amount=amount)
            for currency, amount in sorted(stable_costs.items())
        )
        duplicate_totals = tuple(
            CurrencyAmount(currency=currency, amount=amount)
            for currency, amount in sorted(duplicate_costs.items())
        )
        return CacheOpportunityReport(
            trace_count=len(immutable_traces),
            teacher_turn_traces=sum(
                trace.workload == WorkloadKind.TEACHER_TURN
                for trace in immutable_traces
            ),
            review_evaluation_traces=sum(
                trace.workload == WorkloadKind.REVIEW_EVALUATION
                for trace in immutable_traces
            ),
            current_cache_metrics=aggregator.report(),
            potential_stable_prefix_reuse=stable_count,
            potential_stable_prefix_tokens=stable_tokens,
            potential_stable_prefix_estimated_savings=(
                stable_totals[0].amount if len(stable_totals) == 1 else None
            ),
            potential_stable_prefix_savings_by_currency=stable_totals,
            avoidable_duplicate_calls=duplicate_calls,
            avoidable_duplicate_input_tokens=duplicate_tokens,
            avoidable_duplicate_estimated_cost=(
                duplicate_totals[0].amount if len(duplicate_totals) == 1 else None
            ),
            avoidable_duplicate_costs_by_currency=duplicate_totals,
            non_cacheable_dynamic_calls=non_cacheable_calls,
            source_classification=str(
                source_classification or "offline_execution_traces"
            ),
        )
