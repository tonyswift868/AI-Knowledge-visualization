from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.providers.telemetry import ProviderUsageReceipt
from app.teaching.contracts import TeachingDepth
from app.teaching.evaluator_v2 import EvaluationPath, EvaluationResult

from .contracts import CacheObservatoryError, CurrencyAmount


def _count(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CacheObservatoryError(f"{name} must be a non-negative integer")


def _money(name: str, value: Decimal | None) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite() or value < 0:
        raise CacheObservatoryError(f"{name} must be finite and non-negative")


def _currency(value: str | None, *, required: bool) -> str | None:
    normalized = str(value or "").strip().upper() or None
    if required and normalized is None:
        raise CacheObservatoryError("estimated cost requires a currency")
    return normalized


class TeacherCostMode(str, Enum):
    FAST = "FAST"
    DEEP = "DEEP"

    @classmethod
    def from_teaching_depth(cls, depth: TeachingDepth | str) -> TeacherCostMode:
        normalized = depth.value if isinstance(depth, TeachingDepth) else str(depth)
        return cls(normalized.strip().upper())


class TeacherCostComponent(str, Enum):
    STABLE_PREFIX_INPUT = "STABLE_PREFIX_INPUT"
    DYNAMIC_INPUT = "DYNAMIC_INPUT"
    OUTPUT = "OUTPUT"


@dataclass(frozen=True, slots=True)
class TeacherTurnCostTrace:
    trace_id: str
    mode: TeacherCostMode
    prompt_tokens: int
    stable_prefix_tokens: int
    dynamic_tokens: int
    cached_input_tokens: int | None
    output_tokens: int
    provider_call_count: int
    estimated_actual_cost: Decimal | None
    currency: str | None
    stable_prefix_estimated_cost: Decimal | None = None
    dynamic_input_estimated_cost: Decimal | None = None
    output_estimated_cost: Decimal | None = None
    ttft_ms: Decimal | None = None
    estimation_basis: str = "runtime telemetry estimate"
    cost_classification: str = "estimated_actual_cost"
    synthetic: bool = False

    def __post_init__(self) -> None:
        trace_id = str(self.trace_id or "").strip()
        if not trace_id:
            raise CacheObservatoryError("trace_id must not be empty")
        object.__setattr__(self, "trace_id", trace_id)
        mode = (
            self.mode
            if isinstance(self.mode, TeacherCostMode)
            else TeacherCostMode(self.mode)
        )
        object.__setattr__(self, "mode", mode)
        for name in (
            "prompt_tokens",
            "stable_prefix_tokens",
            "dynamic_tokens",
            "output_tokens",
            "provider_call_count",
        ):
            _count(name, getattr(self, name))
        if self.cached_input_tokens is not None:
            _count("cached_input_tokens", self.cached_input_tokens)
        if self.stable_prefix_tokens + self.dynamic_tokens != self.prompt_tokens:
            raise CacheObservatoryError(
                "stable-prefix and dynamic tokens must reconcile to prompt tokens"
            )
        if (
            self.cached_input_tokens is not None
            and self.cached_input_tokens > self.prompt_tokens
        ):
            raise CacheObservatoryError("cached input cannot exceed prompt tokens")
        if self.provider_call_count > 1:
            raise CacheObservatoryError(
                "Teacher Runtime v2 permits at most one Provider call"
            )
        if self.provider_call_count == 0 and self.ttft_ms is not None:
            raise CacheObservatoryError("TTFT requires an observed Provider call")
        if self.ttft_ms is not None:
            _money("ttft_ms", self.ttft_ms)
        components = (
            self.stable_prefix_estimated_cost,
            self.dynamic_input_estimated_cost,
            self.output_estimated_cost,
        )
        _money("estimated_actual_cost", self.estimated_actual_cost)
        for name, value in zip(
            (
                "stable_prefix_estimated_cost",
                "dynamic_input_estimated_cost",
                "output_estimated_cost",
            ),
            components,
            strict=True,
        ):
            _money(name, value)
        if self.estimated_actual_cost is None:
            if any(value is not None for value in components):
                raise CacheObservatoryError(
                    "component costs require an estimated actual cost"
                )
        elif any(value is None for value in components):
            raise CacheObservatoryError(
                "estimated actual cost requires all Teacher component estimates"
            )
        elif (
            sum((value for value in components if value is not None), Decimal(0))
            != self.estimated_actual_cost
        ):
            raise CacheObservatoryError(
                "Teacher component estimates must reconcile to estimated actual cost"
            )
        object.__setattr__(
            self,
            "currency",
            _currency(self.currency, required=self.estimated_actual_cost is not None),
        )
        if not str(self.estimation_basis or "").strip():
            raise CacheObservatoryError("estimation_basis must not be empty")
        if self.cost_classification != "estimated_actual_cost":
            raise CacheObservatoryError(
                "Teacher cost must remain explicitly classified as estimated"
            )


@dataclass(frozen=True, slots=True)
class TTFTMetadata:
    observed_count: int
    missing_count: int
    minimum_ms: Decimal | None
    average_ms: Decimal | None
    p50_ms: Decimal | None
    p95_ms: Decimal | None
    maximum_ms: Decimal | None
    measurement_seam: str = "provider_stream_first_token"


@dataclass(frozen=True, slots=True)
class TeacherComponentCost:
    component: TeacherCostComponent
    currency: str
    estimated_amount: Decimal
    classification: str = "estimated"


@dataclass(frozen=True, slots=True)
class TeacherTurnCostProfile:
    mode: TeacherCostMode
    turn_count: int
    prompt_tokens: int
    stable_prefix_tokens: int
    dynamic_tokens: int
    cached_input_tokens: int
    cache_usage_unknown_turns: int
    output_tokens: int
    provider_call_count: int
    estimated_actual_cost: Decimal | None
    estimated_costs_by_currency: tuple[CurrencyAmount, ...]
    component_costs: tuple[TeacherComponentCost, ...]
    most_expensive_component: TeacherCostComponent | None
    cost_unknown_turns: int
    ttft: TTFTMetadata
    synthetic_turn_count: int
    cost_classification: str = "estimated_actual_cost"

    def to_public_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "turn_count": self.turn_count,
            "prompt_tokens": self.prompt_tokens,
            "stable_prefix_tokens": self.stable_prefix_tokens,
            "dynamic_tokens": self.dynamic_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_usage_unknown_turns": self.cache_usage_unknown_turns,
            "output_tokens": self.output_tokens,
            "provider_call_count": self.provider_call_count,
            "estimated_actual_cost": (
                str(self.estimated_actual_cost)
                if self.estimated_actual_cost is not None
                else None
            ),
            "estimated_costs_by_currency": [
                {"currency": item.currency, "amount": str(item.amount)}
                for item in self.estimated_costs_by_currency
            ],
            "component_costs": [
                {
                    "component": item.component.value,
                    "currency": item.currency,
                    "estimated_amount": str(item.estimated_amount),
                    "classification": item.classification,
                }
                for item in self.component_costs
            ],
            "most_expensive_component": (
                self.most_expensive_component.value
                if self.most_expensive_component is not None
                else None
            ),
            "cost_unknown_turns": self.cost_unknown_turns,
            "ttft": {
                "observed_count": self.ttft.observed_count,
                "missing_count": self.ttft.missing_count,
                "minimum_ms": str(self.ttft.minimum_ms)
                if self.ttft.minimum_ms is not None
                else None,
                "average_ms": str(self.ttft.average_ms)
                if self.ttft.average_ms is not None
                else None,
                "p50_ms": str(self.ttft.p50_ms)
                if self.ttft.p50_ms is not None
                else None,
                "p95_ms": str(self.ttft.p95_ms)
                if self.ttft.p95_ms is not None
                else None,
                "maximum_ms": str(self.ttft.maximum_ms)
                if self.ttft.maximum_ms is not None
                else None,
                "measurement_seam": self.ttft.measurement_seam,
            },
            "synthetic_turn_count": self.synthetic_turn_count,
            "cost_classification": self.cost_classification,
        }


@dataclass(frozen=True, slots=True)
class TeacherCostProfileReport:
    fast: TeacherTurnCostProfile
    deep: TeacherTurnCostProfile
    most_expensive_mode: TeacherCostMode | None

    def for_mode(self, mode: TeacherCostMode) -> TeacherTurnCostProfile:
        normalized = (
            mode if isinstance(mode, TeacherCostMode) else TeacherCostMode(mode)
        )
        return self.fast if normalized == TeacherCostMode.FAST else self.deep

    def to_public_dict(self) -> dict[str, object]:
        return {
            "FAST": self.fast.to_public_dict(),
            "DEEP": self.deep.to_public_dict(),
            "most_expensive_mode": (
                self.most_expensive_mode.value
                if self.most_expensive_mode is not None
                else None
            ),
        }


def _percentile(values: tuple[Decimal, ...], percentile: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(Decimal(len(ordered)) * percentile))
    return ordered[rank - 1]


def build_teacher_turn_cost_profile(
    traces: tuple[TeacherTurnCostTrace, ...] | list[TeacherTurnCostTrace],
    *,
    mode: TeacherCostMode,
) -> TeacherTurnCostProfile:
    normalized = mode if isinstance(mode, TeacherCostMode) else TeacherCostMode(mode)
    selected = tuple(trace for trace in traces if trace.mode == normalized)
    costs: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    components: dict[tuple[TeacherCostComponent, str], Decimal] = defaultdict(
        lambda: Decimal(0)
    )
    for trace in selected:
        if trace.estimated_actual_cost is None or trace.currency is None:
            continue
        costs[trace.currency] += trace.estimated_actual_cost
        for component, amount in (
            (
                TeacherCostComponent.STABLE_PREFIX_INPUT,
                trace.stable_prefix_estimated_cost,
            ),
            (TeacherCostComponent.DYNAMIC_INPUT, trace.dynamic_input_estimated_cost),
            (TeacherCostComponent.OUTPUT, trace.output_estimated_cost),
        ):
            if amount is not None:
                components[(component, trace.currency)] += amount
    currency_totals = tuple(
        CurrencyAmount(currency=currency, amount=amount)
        for currency, amount in sorted(costs.items())
    )
    component_totals = tuple(
        TeacherComponentCost(
            component=component, currency=currency, estimated_amount=amount
        )
        for (component, currency), amount in sorted(
            components.items(), key=lambda item: (item[0][1], item[0][0].value)
        )
    )
    if len(currency_totals) == 1 and component_totals:
        most_expensive = max(
            component_totals,
            key=lambda item: (item.estimated_amount, item.component.value),
        ).component
    else:
        most_expensive = None
    ttft_values = tuple(
        trace.ttft_ms for trace in selected if trace.ttft_ms is not None
    )
    return TeacherTurnCostProfile(
        mode=normalized,
        turn_count=len(selected),
        prompt_tokens=sum(trace.prompt_tokens for trace in selected),
        stable_prefix_tokens=sum(trace.stable_prefix_tokens for trace in selected),
        dynamic_tokens=sum(trace.dynamic_tokens for trace in selected),
        cached_input_tokens=sum(trace.cached_input_tokens or 0 for trace in selected),
        cache_usage_unknown_turns=sum(
            trace.cached_input_tokens is None for trace in selected
        ),
        output_tokens=sum(trace.output_tokens for trace in selected),
        provider_call_count=sum(trace.provider_call_count for trace in selected),
        estimated_actual_cost=(
            currency_totals[0].amount if len(currency_totals) == 1 else None
        ),
        estimated_costs_by_currency=currency_totals,
        component_costs=component_totals,
        most_expensive_component=most_expensive,
        cost_unknown_turns=sum(
            trace.estimated_actual_cost is None for trace in selected
        ),
        ttft=TTFTMetadata(
            observed_count=len(ttft_values),
            missing_count=sum(
                trace.provider_call_count > 0 and trace.ttft_ms is None
                for trace in selected
            ),
            minimum_ms=min(ttft_values) if ttft_values else None,
            average_ms=(
                sum(ttft_values, Decimal(0)) / Decimal(len(ttft_values))
                if ttft_values
                else None
            ),
            p50_ms=_percentile(ttft_values, Decimal("0.50")),
            p95_ms=_percentile(ttft_values, Decimal("0.95")),
            maximum_ms=max(ttft_values) if ttft_values else None,
        ),
        synthetic_turn_count=sum(trace.synthetic for trace in selected),
    )


def build_teacher_cost_profile_report(
    traces: tuple[TeacherTurnCostTrace, ...] | list[TeacherTurnCostTrace],
) -> TeacherCostProfileReport:
    fast = build_teacher_turn_cost_profile(traces, mode=TeacherCostMode.FAST)
    deep = build_teacher_turn_cost_profile(traces, mode=TeacherCostMode.DEEP)
    most_expensive = None
    if (
        fast.estimated_actual_cost is not None
        and deep.estimated_actual_cost is not None
        and len(fast.estimated_costs_by_currency) == 1
        and len(deep.estimated_costs_by_currency) == 1
        and fast.estimated_costs_by_currency[0].currency
        == deep.estimated_costs_by_currency[0].currency
    ):
        most_expensive = (
            TeacherCostMode.DEEP
            if deep.estimated_actual_cost > fast.estimated_actual_cost
            else TeacherCostMode.FAST
        )
    return TeacherCostProfileReport(
        fast=fast,
        deep=deep,
        most_expensive_mode=most_expensive,
    )


def teacher_trace_from_usage_receipt(
    *,
    trace_id: str,
    mode: TeacherCostMode | TeachingDepth | str,
    receipt: ProviderUsageReceipt,
    stable_prefix_tokens: int,
    dynamic_tokens: int,
    estimated_actual_cost: Decimal | None,
    currency: str | None,
    stable_prefix_estimated_cost: Decimal | None,
    dynamic_input_estimated_cost: Decimal | None,
    output_estimated_cost: Decimal | None,
    ttft_ms: Decimal | None = None,
    estimation_basis: str = "Provider usage plus versioned pricing estimate",
) -> TeacherTurnCostTrace:
    if receipt.prompt_tokens is None or receipt.completion_tokens is None:
        raise CacheObservatoryError(
            "Teacher cost profile requires available token usage"
        )
    normalized_mode = (
        mode
        if isinstance(mode, TeacherCostMode)
        else TeacherCostMode.from_teaching_depth(mode)
    )
    return TeacherTurnCostTrace(
        trace_id=trace_id,
        mode=normalized_mode,
        prompt_tokens=receipt.prompt_tokens,
        stable_prefix_tokens=stable_prefix_tokens,
        dynamic_tokens=dynamic_tokens,
        cached_input_tokens=receipt.prompt_cache_hit_tokens,
        output_tokens=receipt.completion_tokens,
        provider_call_count=int(receipt.provider_called),
        estimated_actual_cost=estimated_actual_cost,
        currency=currency,
        stable_prefix_estimated_cost=stable_prefix_estimated_cost,
        dynamic_input_estimated_cost=dynamic_input_estimated_cost,
        output_estimated_cost=output_estimated_cost,
        ttft_ms=ttft_ms,
        estimation_basis=estimation_basis,
    )


class ReviewAction(str, Enum):
    QUESTION_READ = "QUESTION_READ"
    QUESTION_BANK_SELECTION = "QUESTION_BANK_SELECTION"
    OBJECTIVE_EVALUATION = "OBJECTIVE_EVALUATION"
    SEMANTIC_EVALUATION = "SEMANTIC_EVALUATION"
    TEACHER_EXPLANATION = "TEACHER_EXPLANATION"
    WEAK_QUEUE_UPDATE = "WEAK_QUEUE_UPDATE"
    FSRS_UPDATE = "FSRS_UPDATE"


class ReviewExecutionPlane(str, Enum):
    LOCAL = "LOCAL"
    PROVIDER = "PROVIDER"


LOCAL_REVIEW_ACTIONS = (
    ReviewAction.QUESTION_READ,
    ReviewAction.QUESTION_BANK_SELECTION,
    ReviewAction.OBJECTIVE_EVALUATION,
    ReviewAction.WEAK_QUEUE_UPDATE,
    ReviewAction.FSRS_UPDATE,
)


def review_execution_plane(action: ReviewAction) -> ReviewExecutionPlane:
    normalized = action if isinstance(action, ReviewAction) else ReviewAction(action)
    return (
        ReviewExecutionPlane.LOCAL
        if normalized in LOCAL_REVIEW_ACTIONS
        else ReviewExecutionPlane.PROVIDER
    )


@dataclass(frozen=True, slots=True)
class ReviewActionCostTrace:
    trace_id: str
    evaluation_id: str
    action: ReviewAction
    provider_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: Decimal | None = Decimal(0)
    currency: str | None = "USD"
    synthetic: bool = False
    cost_classification: str = "estimated"

    def __post_init__(self) -> None:
        if (
            not str(self.trace_id or "").strip()
            or not str(self.evaluation_id or "").strip()
        ):
            raise CacheObservatoryError("review trace identities must not be empty")
        action = (
            self.action
            if isinstance(self.action, ReviewAction)
            else ReviewAction(self.action)
        )
        object.__setattr__(self, "action", action)
        for name in ("provider_call_count", "input_tokens", "output_tokens"):
            _count(name, getattr(self, name))
        if self.provider_call_count > 1:
            raise CacheObservatoryError(
                "one review action permits at most one Provider call"
            )
        _money("estimated_cost", self.estimated_cost)
        object.__setattr__(
            self,
            "currency",
            _currency(self.currency, required=self.estimated_cost is not None),
        )
        if review_execution_plane(action) == ReviewExecutionPlane.LOCAL and (
            self.provider_call_count
            or self.input_tokens
            or self.output_tokens
            or (self.estimated_cost not in {None, Decimal(0)})
        ):
            raise CacheObservatoryError(
                f"{action.value} is local and cannot claim Provider usage"
            )
        if self.cost_classification != "estimated":
            raise CacheObservatoryError("Review cost must be explicitly estimated")


@dataclass(frozen=True, slots=True)
class ReviewActionCostProfile:
    action: ReviewAction
    execution_plane: ReviewExecutionPlane
    action_count: int
    provider_call_count: int
    input_tokens: int
    output_tokens: int
    estimated_cost: Decimal | None
    estimated_costs_by_currency: tuple[CurrencyAmount, ...]
    cost_unknown_actions: int


@dataclass(frozen=True, slots=True)
class ReviewCostProfile:
    evaluation_count: int
    actions: tuple[ReviewActionCostProfile, ...]
    provider_call_count: int
    estimated_cost: Decimal | None
    estimated_costs_by_currency: tuple[CurrencyAmount, ...]
    most_expensive_action: ReviewAction | None
    zero_provider_capable_actions: tuple[ReviewAction, ...]
    zero_provider_path: tuple[ReviewAction, ...]
    zero_provider_evaluation_count: int
    synthetic_action_count: int
    cost_classification: str = "estimated"

    def for_action(self, action: ReviewAction) -> ReviewActionCostProfile:
        normalized = (
            action if isinstance(action, ReviewAction) else ReviewAction(action)
        )
        return next(item for item in self.actions if item.action == normalized)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "evaluation_count": self.evaluation_count,
            "actions": {
                item.action.value: {
                    "execution_plane": item.execution_plane.value,
                    "action_count": item.action_count,
                    "provider_call_count": item.provider_call_count,
                    "input_tokens": item.input_tokens,
                    "output_tokens": item.output_tokens,
                    "estimated_cost": (
                        str(item.estimated_cost)
                        if item.estimated_cost is not None
                        else None
                    ),
                    "estimated_costs_by_currency": [
                        {"currency": cost.currency, "amount": str(cost.amount)}
                        for cost in item.estimated_costs_by_currency
                    ],
                    "cost_unknown_actions": item.cost_unknown_actions,
                }
                for item in self.actions
            },
            "provider_call_count": self.provider_call_count,
            "estimated_cost": (
                str(self.estimated_cost) if self.estimated_cost is not None else None
            ),
            "estimated_costs_by_currency": [
                {"currency": item.currency, "amount": str(item.amount)}
                for item in self.estimated_costs_by_currency
            ],
            "most_expensive_action": (
                self.most_expensive_action.value
                if self.most_expensive_action is not None
                else None
            ),
            "zero_provider_capable_actions": [
                item.value for item in self.zero_provider_capable_actions
            ],
            "zero_provider_path": [item.value for item in self.zero_provider_path],
            "zero_provider_evaluation_count": self.zero_provider_evaluation_count,
            "synthetic_action_count": self.synthetic_action_count,
            "cost_classification": self.cost_classification,
        }


def build_review_cost_profile(
    traces: tuple[ReviewActionCostTrace, ...] | list[ReviewActionCostTrace],
) -> ReviewCostProfile:
    grouped: dict[ReviewAction, list[ReviewActionCostTrace]] = {
        action: [] for action in ReviewAction
    }
    for trace in traces:
        grouped[trace.action].append(trace)
    action_profiles: list[ReviewActionCostProfile] = []
    total_currencies: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for action in ReviewAction:
        action_traces = tuple(grouped[action])
        currencies: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for trace in action_traces:
            if trace.estimated_cost is not None and trace.currency is not None:
                currencies[trace.currency] += trace.estimated_cost
                total_currencies[trace.currency] += trace.estimated_cost
        totals = tuple(
            CurrencyAmount(currency=currency, amount=amount)
            for currency, amount in sorted(currencies.items())
        )
        action_profiles.append(
            ReviewActionCostProfile(
                action=action,
                execution_plane=review_execution_plane(action),
                action_count=len(action_traces),
                provider_call_count=sum(
                    trace.provider_call_count for trace in action_traces
                ),
                input_tokens=sum(trace.input_tokens for trace in action_traces),
                output_tokens=sum(trace.output_tokens for trace in action_traces),
                estimated_cost=totals[0].amount if len(totals) == 1 else None,
                estimated_costs_by_currency=totals,
                cost_unknown_actions=sum(
                    trace.estimated_cost is None for trace in action_traces
                ),
            )
        )
    overall_totals = tuple(
        CurrencyAmount(currency=currency, amount=amount)
        for currency, amount in sorted(total_currencies.items())
    )
    most_expensive = None
    if len(overall_totals) == 1:
        known_profiles = tuple(
            item for item in action_profiles if item.estimated_cost is not None
        )
        if known_profiles:
            most_expensive = max(
                known_profiles,
                key=lambda item: (item.estimated_cost or Decimal(0), item.action.value),
            ).action
    by_evaluation: dict[str, int] = defaultdict(int)
    for trace in traces:
        by_evaluation[trace.evaluation_id] += trace.provider_call_count
    zero_path = (
        ReviewAction.QUESTION_READ,
        ReviewAction.QUESTION_BANK_SELECTION,
        ReviewAction.OBJECTIVE_EVALUATION,
        ReviewAction.WEAK_QUEUE_UPDATE,
        ReviewAction.FSRS_UPDATE,
    )
    return ReviewCostProfile(
        evaluation_count=len(by_evaluation),
        actions=tuple(action_profiles),
        provider_call_count=sum(trace.provider_call_count for trace in traces),
        estimated_cost=(overall_totals[0].amount if len(overall_totals) == 1 else None),
        estimated_costs_by_currency=overall_totals,
        most_expensive_action=most_expensive,
        zero_provider_capable_actions=LOCAL_REVIEW_ACTIONS,
        zero_provider_path=zero_path,
        zero_provider_evaluation_count=sum(
            provider_calls == 0 for provider_calls in by_evaluation.values()
        ),
        synthetic_action_count=sum(trace.synthetic for trace in traces),
    )


def review_trace_from_evaluation_result(
    *,
    trace_id: str,
    result: EvaluationResult,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost: Decimal | None = Decimal(0),
    currency: str | None = "USD",
) -> ReviewActionCostTrace:
    action = (
        ReviewAction.OBJECTIVE_EVALUATION
        if result.path == EvaluationPath.OBJECTIVE
        else ReviewAction.SEMANTIC_EVALUATION
    )
    return ReviewActionCostTrace(
        trace_id=trace_id,
        evaluation_id=result.attempt_id,
        action=action,
        provider_call_count=result.provider_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=estimated_cost,
        currency=currency,
    )
