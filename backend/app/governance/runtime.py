from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Generic, Iterable, TypeVar
from uuid import uuid4

from app.enterprise.contracts_v1 import ResourceScope, TenantContext, TenantMode
from app.governance.authorization import AuthorizationService
from app.governance.models import (
    Action,
    BreakGlassGrant,
    GovernanceActorAdapter,
    PolicyDecision,
    PolicyRequest,
    RelationshipContext,
    ResourceType,
)


T = TypeVar("T")


def _required(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("governance runtime timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class AuthorizedOperation:
    subject: GovernanceActorAdapter
    tenant: TenantContext
    action: Action
    resource: ResourceType
    resource_id: str
    resource_scope: ResourceScope
    trace_id: str
    relationships: RelationshipContext = field(default_factory=RelationshipContext)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    break_glass: BreakGlassGrant | None = None
    operation_id: str = field(default_factory=lambda: "governed_op_" + uuid4().hex)

    def __post_init__(self) -> None:
        for name in ("resource_id", "trace_id", "operation_id"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at))

    def to_policy_request(self) -> PolicyRequest:
        return PolicyRequest(
            subject=self.subject,
            tenant=self.tenant,
            action=self.action,
            resource=self.resource,
            resource_id=self.resource_id,
            resource_scope=self.resource_scope,
            relationships=self.relationships,
            trace_id=self.trace_id,
            occurred_at=self.occurred_at,
            break_glass=self.break_glass,
        )


@dataclass(frozen=True, slots=True)
class EnforcementReceipt(Generic[T]):
    operation_id: str
    decision: PolicyDecision
    mutation_performed: bool
    result: T | None = None

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    @property
    def reason_code(self) -> str:
        return self.decision.reason_code


class GovernanceEnforcementService:
    """Application mutation gate. DENY returns without invoking the mutation."""

    _WRITE_ACTIONS = frozenset(
        {Action.CREATE, Action.UPDATE, Action.DELETE, Action.MANAGE}
    )

    def __init__(self, authorization: AuthorizationService) -> None:
        self._authorization = authorization

    def authorize(self, operation: AuthorizedOperation) -> PolicyDecision:
        return self._authorization.authorize(operation.to_policy_request())

    def enforce_write(
        self,
        operation: AuthorizedOperation,
        mutation: Callable[[], T],
    ) -> EnforcementReceipt[T]:
        if operation.tenant.mode is not TenantMode.ENTERPRISE_SERVER:
            raise ValueError("enterprise enforcement requires ENTERPRISE_SERVER mode")
        if operation.action not in self._WRITE_ACTIONS:
            raise ValueError("enforce_write requires a governed write action")
        decision = self.authorize(operation)
        if not decision.allowed:
            return EnforcementReceipt(
                operation_id=operation.operation_id,
                decision=decision,
                mutation_performed=False,
            )
        result = mutation()
        return EnforcementReceipt(
            operation_id=operation.operation_id,
            decision=decision,
            mutation_performed=True,
            result=result,
        )


@dataclass(frozen=True, slots=True)
class AuthorizedResourceCandidate(Generic[T]):
    resource_id: str
    resource_scope: ResourceScope
    payload: T
    relationships: RelationshipContext = field(default_factory=RelationshipContext)

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_id", _required("resource_id", self.resource_id))


@dataclass(frozen=True, slots=True)
class AuthorizedQueryPolicy:
    subject: GovernanceActorAdapter
    tenant: TenantContext
    action: Action
    resource: ResourceType
    trace_id: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.tenant.mode is not TenantMode.ENTERPRISE_SERVER:
            raise ValueError("enterprise query policy requires ENTERPRISE_SERVER mode")
        if self.action not in {Action.READ, Action.READ_METADATA, Action.EXPORT}:
            raise ValueError("query policy supports read, metadata, or export only")
        object.__setattr__(self, "trace_id", _required("trace_id", self.trace_id))
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at))


@dataclass(frozen=True, slots=True)
class BulkAuthorizationDecision:
    candidate_count: int
    allowed_resource_ids: tuple[str, ...]
    denied: tuple[tuple[str, str], ...]
    audit_sequence: int | None

    def __post_init__(self) -> None:
        if self.candidate_count < 0:
            raise ValueError("candidate_count must be non-negative")
        if len(self.allowed_resource_ids) + len(self.denied) != self.candidate_count:
            raise ValueError("bulk decision cardinality mismatch")

    @property
    def default_deny_preserved(self) -> bool:
        return self.candidate_count == len(self.allowed_resource_ids) + len(self.denied)

    def allows(self, resource_id: str) -> bool:
        return resource_id in self.allowed_resource_ids


class ResourceVisibilityFilter:
    """Application-layer read filter; PostgreSQL RLS remains the database floor."""

    def __init__(self, authorization: AuthorizationService) -> None:
        self._authorization = authorization

    @staticmethod
    def _materialize(
        candidates: Iterable[AuthorizedResourceCandidate[T]],
    ) -> tuple[AuthorizedResourceCandidate[T], ...]:
        materialized = tuple(candidates)
        identifiers = tuple(candidate.resource_id for candidate in materialized)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("resource candidates must use unique resource IDs")
        return materialized

    def authorize(
        self,
        policy: AuthorizedQueryPolicy,
        candidates: Iterable[AuthorizedResourceCandidate[T]],
    ) -> BulkAuthorizationDecision:
        materialized = self._materialize(candidates)
        requests = tuple(
            PolicyRequest(
                subject=policy.subject,
                tenant=policy.tenant,
                action=policy.action,
                resource=policy.resource,
                resource_id=candidate.resource_id,
                resource_scope=candidate.resource_scope,
                relationships=candidate.relationships,
                trace_id=policy.trace_id,
                occurred_at=policy.occurred_at,
            )
            for candidate in materialized
        )
        decisions = self._authorization.authorize_bulk(requests)
        allowed: list[str] = []
        denied: list[tuple[str, str]] = []
        for candidate, decision in zip(materialized, decisions, strict=True):
            if decision.allowed:
                allowed.append(candidate.resource_id)
            else:
                denied.append((candidate.resource_id, decision.reason_code))
        return BulkAuthorizationDecision(
            candidate_count=len(materialized),
            allowed_resource_ids=tuple(allowed),
            denied=tuple(denied),
            audit_sequence=(decisions[0].audit_sequence if decisions else None),
        )

    def visible(
        self,
        policy: AuthorizedQueryPolicy,
        candidates: Iterable[AuthorizedResourceCandidate[T]],
    ) -> tuple[T, ...]:
        materialized = self._materialize(candidates)
        decision = self.authorize(policy, materialized)
        allowed = frozenset(decision.allowed_resource_ids)
        return tuple(
            candidate.payload
            for candidate in materialized
            if candidate.resource_id in allowed
        )

    def list(
        self,
        policy: AuthorizedQueryPolicy,
        candidates: Iterable[AuthorizedResourceCandidate[T]],
    ) -> tuple[T, ...]:
        return self.visible(policy, candidates)

    def search(
        self,
        policy: AuthorizedQueryPolicy,
        candidates: Iterable[AuthorizedResourceCandidate[T]],
    ) -> tuple[T, ...]:
        return self.visible(policy, candidates)

    def detail(
        self,
        policy: AuthorizedQueryPolicy,
        candidate: AuthorizedResourceCandidate[T],
    ) -> T | None:
        visible = self.visible(policy, (candidate,))
        return visible[0] if visible else None

    def export(
        self,
        policy: AuthorizedQueryPolicy,
        candidates: Iterable[AuthorizedResourceCandidate[T]],
    ) -> tuple[T, ...]:
        if policy.action is not Action.EXPORT:
            raise ValueError("export filtering requires Action.EXPORT")
        return self.visible(policy, candidates)


__all__ = [
    "AuthorizedOperation",
    "AuthorizedQueryPolicy",
    "AuthorizedResourceCandidate",
    "BulkAuthorizationDecision",
    "EnforcementReceipt",
    "GovernanceEnforcementService",
    "ResourceVisibilityFilter",
]
