from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from app.enterprise.contracts_v1 import ResourceClassification, TenantContext, TenantMode
from app.governance.authorization import AuthorizationService
from app.governance.models import (
    Action,
    GovernanceActorAdapter,
    PolicyRequest,
    RelationshipContext,
    Resource,
    Role,
    resource_scope_for_tenant,
)


def _required(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


class CostViewScope(str, Enum):
    USER = "USER"
    COHORT = "COHORT"
    WORKSPACE = "WORKSPACE"
    ORGANIZATION = "ORGANIZATION"


@dataclass(frozen=True, slots=True)
class TenantCostEntry:
    entry_id: str
    organization_id: str | None
    workspace_id: str | None
    user_id: str
    cohort_id: str | None
    currency: str
    amount: Decimal | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        for name in ("entry_id", "user_id", "currency"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        for name in ("organization_id", "workspace_id", "cohort_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required(name, value))
        if self.amount is not None and (
            not isinstance(self.amount, Decimal)
            or not self.amount.is_finite()
            or self.amount < 0
        ):
            raise ValueError("amount must be a finite non-negative Decimal or None")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(
            self,
            "occurred_at",
            self.occurred_at.astimezone(timezone.utc),
        )


class TenantCostStore:
    """Append-only cost-view input seam; Cost Engine remains the calculation owner."""

    def __init__(self) -> None:
        self._entries: list[TenantCostEntry] = []
        self._entry_ids: set[str] = set()
        self._lock = threading.RLock()

    def append(self, entry: TenantCostEntry) -> None:
        with self._lock:
            if entry.entry_id in self._entry_ids:
                raise ValueError("duplicate tenant cost entry")
            self._entry_ids.add(entry.entry_id)
            self._entries.append(entry)

    def entries(self) -> tuple[TenantCostEntry, ...]:
        with self._lock:
            return tuple(self._entries)


@dataclass(frozen=True, slots=True)
class CostViewRequest:
    subject: GovernanceActorAdapter
    tenant: TenantContext
    scope: CostViewScope
    trace_id: str
    user_id: str | None = None
    cohort_id: str | None = None
    relationships: RelationshipContext = field(default_factory=RelationshipContext)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_id", _required("trace_id", self.trace_id))
        if self.user_id is not None:
            object.__setattr__(self, "user_id", _required("user_id", self.user_id))
        if self.cohort_id is not None:
            object.__setattr__(self, "cohort_id", _required("cohort_id", self.cohort_id))


@dataclass(frozen=True, slots=True)
class TenantCostView:
    scope: CostViewScope
    organization_id: str | None
    workspace_id: str | None
    user_id: str | None
    cohort_id: str | None
    currency: str | None
    known_cost_subtotal: Decimal | None
    total_cost: Decimal | None
    record_count: int
    unknown_cost_count: int
    read_only: bool = True


class CostViewDenied(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class TenantCostViewService:
    def __init__(
        self,
        authorization: AuthorizationService,
        store: TenantCostStore,
    ) -> None:
        self._authorization = authorization
        self._store = store

    @staticmethod
    def _role_scope_reason(request: CostViewRequest) -> str | None:
        role = request.subject.effective_role
        if role == Role.LEARNER:
            if request.scope != CostViewScope.USER or request.user_id != request.subject.actor_id:
                return "LEARNER_OWN_COST_ONLY"
        elif role == Role.INSTRUCTOR:
            if request.scope != CostViewScope.COHORT or request.cohort_id is None:
                return "INSTRUCTOR_COHORT_AGGREGATE_ONLY"
            if request.cohort_id not in request.relationships.assigned_cohort_ids:
                return "INSTRUCTOR_ASSIGNMENT_REQUIRED"
        elif role == Role.ADMIN:
            if request.scope != CostViewScope.WORKSPACE or request.tenant.workspace_id is None:
                return "ADMIN_WORKSPACE_COST_ONLY"
        elif role == Role.OWNER:
            if request.tenant.mode is TenantMode.PERSONAL_LOCAL:
                if request.scope != CostViewScope.USER or request.user_id != request.subject.actor_id:
                    return "PERSONAL_LOCAL_OWN_COST_ONLY"
            elif request.scope != CostViewScope.ORGANIZATION:
                return "OWNER_ORGANIZATION_COST_ONLY"
        elif role == Role.AUDITOR:
            expected = (
                CostViewScope.WORKSPACE
                if request.tenant.workspace_id is not None
                else CostViewScope.ORGANIZATION
            )
            if request.scope != expected:
                return "AUDITOR_TENANT_AGGREGATE_ONLY"
        else:
            return "ROLE_MATRIX_DENY"
        return None

    def view(self, request: CostViewRequest) -> TenantCostView:
        role_reason = self._role_scope_reason(request)
        if role_reason is not None:
            raise CostViewDenied(role_reason)
        relationships = request.relationships
        if request.subject.effective_role == Role.LEARNER:
            relationships = RelationshipContext(
                resource_owner_id=request.user_id,
                learner_id=request.user_id,
            )
        elif request.subject.effective_role == Role.INSTRUCTOR:
            relationships = RelationshipContext(
                cohort_id=request.cohort_id,
                assigned_cohort_ids=request.relationships.assigned_cohort_ids,
            )
        result = self._authorization.authorize(
            PolicyRequest(
                subject=request.subject,
                tenant=request.tenant,
                action=Action.READ,
                resource=Resource.COST,
                resource_id=(
                    request.user_id
                    or request.cohort_id
                    or request.tenant.workspace_id
                    or request.tenant.organization_id
                    or request.subject.actor_id
                ),
                resource_scope=resource_scope_for_tenant(
                    request.tenant,
                    classification=ResourceClassification.CONFIDENTIAL,
                    owner_subject_id=request.user_id,
                ),
                relationships=relationships,
                trace_id=request.trace_id,
            )
        )
        if not result.allowed:
            raise CostViewDenied(result.reason_code)

        entries = tuple(
            entry
            for entry in self._store.entries()
            if entry.organization_id == request.tenant.organization_id
            and (
                request.tenant.workspace_id is None
                or entry.workspace_id == request.tenant.workspace_id
            )
            and (request.scope != CostViewScope.USER or entry.user_id == request.user_id)
            and (request.scope != CostViewScope.COHORT or entry.cohort_id == request.cohort_id)
        )
        currencies = {entry.currency for entry in entries}
        known = [entry.amount for entry in entries if entry.amount is not None]
        unknown_count = len(entries) - len(known)
        currency = next(iter(currencies)) if len(currencies) == 1 else None
        known_subtotal = sum(known, Decimal("0")) if currency is not None else None
        total = known_subtotal if unknown_count == 0 and currency is not None else None
        return TenantCostView(
            scope=request.scope,
            organization_id=request.tenant.organization_id,
            workspace_id=request.tenant.workspace_id,
            user_id=request.user_id if request.scope == CostViewScope.USER else None,
            cohort_id=request.cohort_id if request.scope == CostViewScope.COHORT else None,
            currency=currency,
            known_cost_subtotal=known_subtotal,
            total_cost=total,
            record_count=len(entries),
            unknown_cost_count=unknown_count,
        )


__all__ = [
    "CostViewDenied",
    "CostViewRequest",
    "CostViewScope",
    "TenantCostEntry",
    "TenantCostStore",
    "TenantCostView",
    "TenantCostViewService",
]
