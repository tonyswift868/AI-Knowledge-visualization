from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Mapping

from app.cost.budget import BudgetDecision
from app.cost.estimation import CostEstimate
from app.cost.ledger import CurrencyTotal, LedgerSnapshot
from app.cost.runtime_budget import (
    BudgetReceipt,
    ExecutionBudgetEnvelope,
    ExecutionBudgetGate,
    UnknownCostPolicy,
)
from app.enterprise.contracts_v1 import (
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
)
from app.governance.authorization import AuthorizationService
from app.governance.models import (
    Action,
    GovernanceActorAdapter,
    PolicyRequest,
    Resource,
)


def _required(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _money(name: str, value: Decimal | None) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal or None")
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


class QuotaScope(str, Enum):
    ORGANIZATION = "ORGANIZATION"
    WORKSPACE = "WORKSPACE"
    USER = "USER"


class BudgetPeriod(str, Enum):
    DAILY = "DAILY"
    MONTHLY = "MONTHLY"


@dataclass(frozen=True, slots=True)
class BudgetLimit:
    maximum: Decimal
    warning_threshold: Decimal
    hard_stop_threshold: Decimal

    def __post_init__(self) -> None:
        for name in ("maximum", "warning_threshold", "hard_stop_threshold"):
            _money(name, getattr(self, name))
        if not self.warning_threshold <= self.hard_stop_threshold <= self.maximum:
            raise ValueError("budget amounts must satisfy warning <= hard stop <= maximum")


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    policy_id: str
    organization_id: str | None
    scope: QuotaScope
    currency: str
    workspace_id: str | None = None
    user_id: str | None = None
    daily_budget: BudgetLimit | None = None
    monthly_budget: BudgetLimit | None = None
    model_allowlist: frozenset[str] = field(default_factory=frozenset)
    feature_quotas: Mapping[str, int] = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self) -> None:
        for name in ("policy_id", "currency"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        if self.organization_id is not None:
            object.__setattr__(
                self,
                "organization_id",
                _required("organization_id", self.organization_id),
            )
        if self.scope == QuotaScope.ORGANIZATION:
            if self.organization_id is None:
                raise ValueError("organization policy requires organization_id")
            if self.workspace_id is not None or self.user_id is not None:
                raise ValueError("organization policy cannot target workspace or user")
        elif self.scope == QuotaScope.WORKSPACE:
            if self.organization_id is None:
                raise ValueError("workspace policy requires organization_id")
            object.__setattr__(
                self,
                "workspace_id",
                _required("workspace_id", self.workspace_id or ""),
            )
            if self.user_id is not None:
                raise ValueError("workspace policy cannot target a user")
        else:
            object.__setattr__(self, "user_id", _required("user_id", self.user_id or ""))
            if self.workspace_id is not None:
                object.__setattr__(
                    self,
                    "workspace_id",
                    _required("workspace_id", self.workspace_id),
                )
        object.__setattr__(
            self,
            "model_allowlist",
            frozenset(_required("approved_model", item) for item in self.model_allowlist),
        )
        normalized_quotas: dict[str, int] = {}
        for feature, limit in self.feature_quotas.items():
            key = _required("feature", feature)
            if isinstance(limit, bool) or int(limit) < 0:
                raise ValueError("feature quota must be a non-negative integer")
            normalized_quotas[key] = int(limit)
        object.__setattr__(self, "feature_quotas", normalized_quotas)
        if self.daily_budget is None and self.monthly_budget is None and not (
            self.model_allowlist or self.feature_quotas
        ):
            raise ValueError("quota policy must contain at least one limit")

    def applies_to(self, subject: "QuotaSubject") -> bool:
        if not self.enabled or self.organization_id != subject.tenant.organization_id:
            return False
        if self.scope == QuotaScope.ORGANIZATION:
            return True
        if self.scope == QuotaScope.WORKSPACE:
            return self.workspace_id == subject.tenant.workspace_id
        return self.user_id == subject.user_id and (
            self.workspace_id is None or self.workspace_id == subject.tenant.workspace_id
        )


@dataclass(frozen=True, slots=True)
class OrganizationBudget:
    policy_id: str
    organization_id: str
    currency: str
    daily: BudgetLimit | None = None
    monthly: BudgetLimit | None = None
    model_allowlist: frozenset[str] = field(default_factory=frozenset)
    feature_quotas: Mapping[str, int] = field(default_factory=dict)

    def to_policy(self) -> QuotaPolicy:
        return QuotaPolicy(
            policy_id=self.policy_id,
            organization_id=self.organization_id,
            scope=QuotaScope.ORGANIZATION,
            currency=self.currency,
            daily_budget=self.daily,
            monthly_budget=self.monthly,
            model_allowlist=self.model_allowlist,
            feature_quotas=self.feature_quotas,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceBudget:
    policy_id: str
    organization_id: str
    workspace_id: str
    currency: str
    daily: BudgetLimit | None = None
    monthly: BudgetLimit | None = None
    model_allowlist: frozenset[str] = field(default_factory=frozenset)
    feature_quotas: Mapping[str, int] = field(default_factory=dict)

    def to_policy(self) -> QuotaPolicy:
        return QuotaPolicy(
            policy_id=self.policy_id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            scope=QuotaScope.WORKSPACE,
            currency=self.currency,
            daily_budget=self.daily,
            monthly_budget=self.monthly,
            model_allowlist=self.model_allowlist,
            feature_quotas=self.feature_quotas,
        )


@dataclass(frozen=True, slots=True)
class UserBudget:
    policy_id: str
    user_id: str
    currency: str
    organization_id: str | None = None
    workspace_id: str | None = None
    daily: BudgetLimit | None = None
    monthly: BudgetLimit | None = None
    model_allowlist: frozenset[str] = field(default_factory=frozenset)
    feature_quotas: Mapping[str, int] = field(default_factory=dict)

    def to_policy(self) -> QuotaPolicy:
        return QuotaPolicy(
            policy_id=self.policy_id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            scope=QuotaScope.USER,
            currency=self.currency,
            daily_budget=self.daily,
            monthly_budget=self.monthly,
            model_allowlist=self.model_allowlist,
            feature_quotas=self.feature_quotas,
        )


BudgetDefinition = OrganizationBudget | WorkspaceBudget | UserBudget


@dataclass(frozen=True, slots=True)
class QuotaSubject:
    tenant: TenantContext
    user_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", _required("user_id", self.user_id))


@dataclass(frozen=True, slots=True)
class QuotaUsageSnapshot:
    daily_cost: Mapping[QuotaScope, Decimal | None] = field(default_factory=dict)
    monthly_cost: Mapping[QuotaScope, Decimal | None] = field(default_factory=dict)
    feature_usage: Mapping[QuotaScope, Mapping[str, int]] = field(default_factory=dict)

    def cost(self, scope: QuotaScope, period: BudgetPeriod) -> Decimal | None:
        values = self.daily_cost if period == BudgetPeriod.DAILY else self.monthly_cost
        value = values.get(scope, Decimal("0"))
        _money("usage_cost", value)
        return value

    def feature_count(self, scope: QuotaScope, feature: str) -> int:
        value = self.feature_usage.get(scope, {}).get(feature, 0)
        if isinstance(value, bool) or int(value) < 0:
            raise ValueError("feature usage must be a non-negative integer")
        return int(value)


@dataclass(frozen=True, slots=True)
class ScopedBudgetReceipt:
    policy_id: str
    scope: QuotaScope
    period: BudgetPeriod
    receipt: BudgetReceipt


@dataclass(frozen=True, slots=True)
class QuotaEvaluation:
    decision: BudgetDecision
    reasons: tuple[str, ...]
    evaluated_policy_ids: tuple[str, ...]
    budget_receipts: tuple[ScopedBudgetReceipt, ...]

    @property
    def provider_execution_allowed(self) -> bool:
        return self.decision != BudgetDecision.STOP


class QuotaPolicyError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _ledger_snapshot(
    *,
    run_id: str,
    operation_id: str | None,
    currency: str,
    spend: Decimal | None,
) -> LedgerSnapshot:
    complete = spend is not None
    known_costs = () if spend is None else (CurrencyTotal(currency=currency, amount=spend),)
    return LedgerSnapshot(
        run_id=run_id,
        operation_id=operation_id,
        operation_count=1,
        receipt_count=1 if spend is not None and spend > 0 else 0,
        duplicate_ingestions=0,
        provider_calls=0,
        reported_input_cache_hit_tokens=0,
        reported_input_cache_miss_tokens=0,
        reported_output_tokens=0,
        local_cache_avoided_calls=0,
        unknown_receipts=0 if complete else 1,
        unpriced_receipts=0,
        currency=currency if complete else None,
        known_costs=known_costs,
        known_actual_cost_subtotal=spend,
        actual_calculated_provider_cost=spend,
        actual_cost_complete=complete,
        provider_cache_savings=Decimal("0"),
        local_cache_savings=Decimal("0"),
        estimated_savings=Decimal("0"),
        savings_classification="estimated_savings",
        pricing_versions=(),
    )


class _ScopedUsageLedger:
    def __init__(self, *, currency: str, spend: Decimal | None) -> None:
        self._currency = currency
        self._spend = spend

    def scoped_snapshots(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> tuple[LedgerSnapshot, LedgerSnapshot]:
        return (
            _ledger_snapshot(
                run_id=run_id,
                operation_id=None,
                currency=self._currency,
                spend=self._spend,
            ),
            _ledger_snapshot(
                run_id=run_id,
                operation_id=operation_id,
                currency=self._currency,
                spend=self._spend,
            ),
        )


class TenantQuotaService:
    """Composes broad-to-narrow policy using the existing ExecutionBudgetGate."""

    def __init__(
        self,
        authorization: AuthorizationService,
        *,
        budget_gate: ExecutionBudgetGate | None = None,
    ) -> None:
        self._authorization = authorization
        self._budget_gate = budget_gate or ExecutionBudgetGate()
        self._policies: dict[str, QuotaPolicy] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _policy_resource_scope(policy: QuotaPolicy, tenant: TenantContext) -> ResourceScope:
        if policy.organization_id is None:
            return ResourceScope(
                scope_type=ResourceScopeType.PERSONAL,
                owner_subject_id=policy.user_id,
                classification=ResourceClassification.RESTRICTED,
            )
        if policy.workspace_id is not None or tenant.workspace_id is not None:
            return ResourceScope(
                scope_type=ResourceScopeType.WORKSPACE,
                owner_subject_id=policy.user_id,
                organization_id=policy.organization_id,
                workspace_id=policy.workspace_id or tenant.workspace_id,
                classification=ResourceClassification.RESTRICTED,
            )
        return ResourceScope(
            scope_type=ResourceScopeType.ORGANIZATION,
            owner_subject_id=policy.user_id,
            organization_id=policy.organization_id,
            classification=ResourceClassification.RESTRICTED,
        )

    def set_policy(
        self,
        policy: QuotaPolicy,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> None:
        result = self._authorization.authorize(
            PolicyRequest(
                subject=subject,
                tenant=tenant,
                action=Action.MANAGE,
                resource=Resource.COST,
                resource_id=policy.policy_id,
                resource_scope=self._policy_resource_scope(policy, tenant),
                trace_id=trace_id,
            )
        )
        if not result.allowed:
            raise QuotaPolicyError(result.reason_code)
        with self._lock:
            self._policies[policy.policy_id] = policy

    def set_budget(
        self,
        budget: BudgetDefinition,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> None:
        self.set_policy(
            budget.to_policy(),
            subject=subject,
            tenant=tenant,
            trace_id=trace_id,
        )

    def policies_for(self, subject: QuotaSubject) -> tuple[QuotaPolicy, ...]:
        scope_rank = {
            QuotaScope.ORGANIZATION: 0,
            QuotaScope.WORKSPACE: 1,
            QuotaScope.USER: 2,
        }
        with self._lock:
            return tuple(
                sorted(
                    (policy for policy in self._policies.values() if policy.applies_to(subject)),
                    key=lambda policy: (scope_rank[policy.scope], policy.policy_id),
                )
            )

    def evaluate(
        self,
        *,
        subject: QuotaSubject,
        usage: QuotaUsageSnapshot,
        run_id: str,
        operation_id: str,
        selected_model: str | None,
        feature: str | None,
        requested_feature_units: int = 1,
        estimate: CostEstimate | None,
        provider_call_required: bool = True,
        routing_available: bool = True,
    ) -> QuotaEvaluation:
        if isinstance(requested_feature_units, bool) or requested_feature_units < 0:
            raise ValueError("requested_feature_units must be non-negative")
        policies = self.policies_for(subject)
        reasons: list[str] = []
        receipts: list[ScopedBudgetReceipt] = []
        must_stop = False
        must_warn = False

        for policy in policies:
            if estimate is not None and estimate.currency != policy.currency:
                must_stop = True
                reasons.append(f"{policy.policy_id}:BUDGET_CURRENCY_MISMATCH")
                continue
            if policy.model_allowlist and selected_model not in policy.model_allowlist:
                must_stop = True
                reasons.append(f"{policy.policy_id}:MODEL_NOT_ALLOWED")
            if feature is not None and feature in policy.feature_quotas:
                current = usage.feature_count(policy.scope, feature)
                if current + requested_feature_units > policy.feature_quotas[feature]:
                    must_stop = True
                    reasons.append(f"{policy.policy_id}:FEATURE_QUOTA_EXCEEDED:{feature}")

            for period, limit in (
                (BudgetPeriod.DAILY, policy.daily_budget),
                (BudgetPeriod.MONTHLY, policy.monthly_budget),
            ):
                if limit is None:
                    continue
                receipt = self._budget_gate.evaluate(
                    ExecutionBudgetEnvelope(
                        run_id=f"{run_id}:{policy.policy_id}:{period.value}",
                        operation_id=operation_id,
                        currency=policy.currency,
                        user_max_budget=limit.maximum,
                        warning_threshold=limit.warning_threshold,
                        hard_stop_threshold=limit.hard_stop_threshold,
                        estimate=estimate,
                        provider_call_required=provider_call_required,
                        routing_available=routing_available,
                        selected_model=selected_model,
                        unknown_cost_policy=UnknownCostPolicy.STOP,
                    ),
                    _ScopedUsageLedger(
                        currency=policy.currency,
                        spend=usage.cost(policy.scope, period),
                    ),  # type: ignore[arg-type]
                )
                receipts.append(
                    ScopedBudgetReceipt(
                        policy_id=policy.policy_id,
                        scope=policy.scope,
                        period=period,
                        receipt=receipt,
                    )
                )
                if receipt.decision == BudgetDecision.STOP:
                    must_stop = True
                elif receipt.decision == BudgetDecision.WARN:
                    must_warn = True
                for reason in receipt.reasons:
                    if reason != "WITHIN_BUDGET":
                        reasons.append(f"{policy.policy_id}:{period.value}:{reason}")

        if must_stop:
            decision = BudgetDecision.STOP
        elif must_warn:
            decision = BudgetDecision.WARN
        else:
            decision = BudgetDecision.ALLOW
            reasons.append("ALL_APPLICABLE_QUOTAS_ALLOW")
        return QuotaEvaluation(
            decision=decision,
            reasons=tuple(dict.fromkeys(reasons)),
            evaluated_policy_ids=tuple(policy.policy_id for policy in policies),
            budget_receipts=tuple(receipts),
        )


__all__ = [
    "BudgetLimit",
    "BudgetPeriod",
    "BudgetDefinition",
    "OrganizationBudget",
    "QuotaEvaluation",
    "QuotaPolicy",
    "QuotaPolicyError",
    "QuotaScope",
    "QuotaSubject",
    "QuotaUsageSnapshot",
    "ScopedBudgetReceipt",
    "TenantQuotaService",
    "UserBudget",
    "WorkspaceBudget",
]
