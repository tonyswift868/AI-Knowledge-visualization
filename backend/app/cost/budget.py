from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.cost.contracts import (
    CostAggregate,
    CostBreakdown,
    CostStatus,
    require_non_negative_decimal,
)
from app.cost.estimation import CostEstimate


class BudgetDecision(str, Enum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    STOP = "STOP"


@dataclass(frozen=True)
class CostBudget:
    """Pure budget policy; warning and hard-stop thresholds are cap ratios."""

    currency: str
    maximum_estimated_cost: Decimal
    maximum_actual_cost: Decimal
    warning_threshold: Decimal
    hard_stop_threshold: Decimal

    def __post_init__(self) -> None:
        if not str(self.currency).strip():
            raise ValueError("currency must not be empty")
        for name in (
            "maximum_estimated_cost",
            "maximum_actual_cost",
            "warning_threshold",
            "hard_stop_threshold",
        ):
            require_non_negative_decimal(name, getattr(self, name))
        if not (
            Decimal("0")
            <= self.warning_threshold
            <= self.hard_stop_threshold
            <= Decimal("1")
        ):
            raise ValueError(
                "threshold ratios must satisfy 0 <= warning <= hard stop <= 1"
            )


@dataclass(frozen=True)
class BudgetEvaluation:
    decision: BudgetDecision
    reason: str
    currency: str
    estimated_utilization: Decimal | None
    actual_utilization: Decimal | None


def _utilization(amount: Decimal, maximum: Decimal) -> Decimal:
    if maximum == 0:
        return Decimal("0") if amount == 0 else Decimal("Infinity")
    return amount / maximum


def evaluate_budget(
    budget: CostBudget,
    *,
    estimate: CostEstimate | None = None,
    actual: CostBreakdown | CostAggregate | None = None,
) -> BudgetEvaluation:
    """Return ALLOW/WARN/STOP without mutating the Provider execution chain."""

    if estimate is None and actual is None:
        return BudgetEvaluation(
            decision=BudgetDecision.STOP,
            reason="BUDGET_INPUT_UNAVAILABLE",
            currency=budget.currency,
            estimated_utilization=None,
            actual_utilization=None,
        )

    estimated_utilization: Decimal | None = None
    actual_utilization: Decimal | None = None
    if estimate is not None:
        if estimate.currency != budget.currency:
            return BudgetEvaluation(
                decision=BudgetDecision.STOP,
                reason="BUDGET_CURRENCY_MISMATCH",
                currency=budget.currency,
                estimated_utilization=None,
                actual_utilization=None,
            )
        estimated_utilization = _utilization(
            estimate.maximum,
            budget.maximum_estimated_cost,
        )

    if actual is not None:
        if actual.currency not in {None, budget.currency}:
            return BudgetEvaluation(
                decision=BudgetDecision.STOP,
                reason="BUDGET_CURRENCY_MISMATCH",
                currency=budget.currency,
                estimated_utilization=estimated_utilization,
                actual_utilization=None,
            )
        if isinstance(actual, CostBreakdown):
            actual_total = (
                None if actual.status == CostStatus.COST_UNAVAILABLE else actual.total_cost
            )
        else:
            actual_total = actual.total_cost
        if actual_total is None:
            return BudgetEvaluation(
                decision=BudgetDecision.STOP,
                reason="BUDGET_COST_UNAVAILABLE",
                currency=budget.currency,
                estimated_utilization=estimated_utilization,
                actual_utilization=None,
            )
        actual_utilization = _utilization(
            actual_total,
            budget.maximum_actual_cost,
        )

    utilizations = tuple(
        value
        for value in (estimated_utilization, actual_utilization)
        if value is not None
    )
    highest = max(utilizations)
    if highest >= budget.hard_stop_threshold:
        decision = BudgetDecision.STOP
        reason = "HARD_STOP_THRESHOLD_REACHED"
    elif highest >= budget.warning_threshold:
        decision = BudgetDecision.WARN
        reason = "WARNING_THRESHOLD_REACHED"
    else:
        decision = BudgetDecision.ALLOW
        reason = "WITHIN_BUDGET"
    return BudgetEvaluation(
        decision=decision,
        reason=reason,
        currency=budget.currency,
        estimated_utilization=estimated_utilization,
        actual_utilization=actual_utilization,
    )
