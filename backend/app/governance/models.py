from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import FrozenSet

from app.enterprise.contracts_v1 import (
    AccessDecision,
    ActorContext,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)


def _required(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class Role(str, Enum):
    ORGANIZATION_OWNER = "ORGANIZATION_OWNER"
    OWNER = "ORGANIZATION_OWNER"
    ADMIN = "ADMIN"
    CONTENT_MANAGER = "CONTENT_MANAGER"
    INSTRUCTOR = "INSTRUCTOR"
    LEARNER = "LEARNER"
    AUDITOR = "AUDITOR"

    @classmethod
    def from_contract_code(cls, value: str) -> "Role":
        normalized = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
        if normalized == "OWNER":
            normalized = "ORGANIZATION_OWNER"
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"unsupported enterprise role: {value}") from exc


class Action(str, Enum):
    READ = "READ"
    READ_METADATA = "READ_METADATA"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    MANAGE = "MANAGE"
    EXPORT = "EXPORT"
    EXECUTE = "EXECUTE"


class ResourceType(str, Enum):
    ORGANIZATION = "organization"
    MEMBERSHIP = "membership"
    MEMBERS = "membership"
    WORKSPACE = "workspace"
    DOCUMENT = "document"
    DOCUMENTS = "document"
    KNOWLEDGE_MAP = "knowledge_map"
    QUESTION_BANK = "question_bank"
    PLAN = "plan"
    PLANS = "plan"
    TEACHER_SESSION = "teacher_session"
    REVIEW_CAMPAIGN = "review_campaign"
    STUDENT_PROGRESS = "student_progress"
    PROVIDER_CONFIG = "provider_config"
    COST = "cost"
    AUDIT = "audit"
    EXPORT = "export"


# Compatibility name for the already-published foundation.  ResourceType is the
# frozen enterprise authorization vocabulary used by new callers.
Resource = ResourceType


@dataclass(frozen=True, slots=True)
class GovernanceActorAdapter:
    """Authorization-only fields layered around the frozen ActorContext."""

    actor: ActorContext
    effective_role: Role
    workspace_memberships: FrozenSet[str] = field(default_factory=frozenset)
    active: bool = True

    def __post_init__(self) -> None:
        contract_roles = frozenset(Role.from_contract_code(value) for value in self.actor.roles)
        if self.effective_role not in contract_roles:
            raise ValueError("effective_role must be present in ActorContext.roles")
        memberships = frozenset(
            _required("workspace_membership", item) for item in self.workspace_memberships
        )
        if self.actor.workspace_id is not None:
            memberships = memberships | {self.actor.workspace_id}
        object.__setattr__(self, "workspace_memberships", memberships)

    @property
    def actor_id(self) -> str:
        return self.actor.subject_id

    @property
    def organization_id(self) -> str | None:
        return self.actor.organization_id

    @classmethod
    def personal_local(
        cls,
        actor: ActorContext,
        *,
        effective_role: Role = Role.OWNER,
    ) -> "GovernanceActorAdapter":
        if actor.organization_id is not None or actor.workspace_id is not None:
            raise ValueError("personal-local actor cannot carry enterprise tenant ids")
        return cls(actor=actor, effective_role=effective_role)


@dataclass(frozen=True, slots=True)
class RelationshipContext:
    resource_owner_id: str | None = None
    learner_id: str | None = None
    cohort_id: str | None = None
    assigned_learner_ids: FrozenSet[str] = field(default_factory=frozenset)
    assigned_cohort_ids: FrozenSet[str] = field(default_factory=frozenset)
    learner_cohort_ids: FrozenSet[str] = field(default_factory=frozenset)
    metadata_only: bool = False
    private_student_model: bool = False
    personal_resource: bool = False
    explicit_permissions: FrozenSet[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for name in ("resource_owner_id", "learner_id", "cohort_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required(name, value))
        for name in (
            "assigned_learner_ids",
            "assigned_cohort_ids",
            "learner_cohort_ids",
            "explicit_permissions",
        ):
            object.__setattr__(
                self,
                name,
                frozenset(_required(name, item) for item in getattr(self, name)),
            )


@dataclass(frozen=True, slots=True)
class BreakGlassGrant:
    grant_id: str
    actor_id: str
    organization_id: str
    workspace_id: str | None
    reason: str
    approved_by: str
    issued_at: datetime
    expires_at: datetime
    actions: FrozenSet[Action]
    resources: FrozenSet[ResourceType]
    resource_ids: FrozenSet[str]
    resource_scope: ResourceScope

    def __post_init__(self) -> None:
        for name in ("grant_id", "actor_id", "organization_id", "approved_by"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        reason = _required("reason", self.reason)
        if len(reason) < 8:
            raise ValueError("break-glass reason must contain at least 8 characters")
        object.__setattr__(self, "reason", reason)
        if self.workspace_id is not None:
            object.__setattr__(
                self,
                "workspace_id",
                _required("workspace_id", self.workspace_id),
            )
        object.__setattr__(self, "issued_at", _utc(self.issued_at))
        object.__setattr__(self, "expires_at", _utc(self.expires_at))
        if self.expires_at <= self.issued_at:
            raise ValueError("break-glass grant must expire after it is issued")
        if not self.actions or not self.resources or not self.resource_ids:
            raise ValueError("break-glass grant must be narrowly scoped")
        object.__setattr__(self, "actions", frozenset(self.actions))
        object.__setattr__(self, "resources", frozenset(self.resources))
        object.__setattr__(
            self,
            "resource_ids",
            frozenset(_required("resource_id", value) for value in self.resource_ids),
        )
        if self.resource_scope.organization_id != self.organization_id:
            raise ValueError("break-glass scope organization must match the grant")
        if self.resource_scope.workspace_id != self.workspace_id:
            raise ValueError("break-glass scope workspace must match the grant")


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    subject: GovernanceActorAdapter
    tenant: TenantContext
    action: Action
    resource: ResourceType
    resource_id: str
    resource_scope: ResourceScope
    relationships: RelationshipContext = field(default_factory=RelationshipContext)
    trace_id: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    break_glass: BreakGlassGrant | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_id", _required("resource_id", self.resource_id))
        object.__setattr__(self, "trace_id", _required("trace_id", self.trace_id))
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at))

    @property
    def actor(self) -> ActorContext:
        return self.subject.actor

    @property
    def request_id(self) -> str:
        return self.subject.actor.request_id


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: AccessDecision
    audit_sequence: int
    break_glass_used: bool = False

    def __post_init__(self) -> None:
        if self.audit_sequence < 1:
            raise ValueError("audit_sequence must be positive")

    @property
    def allowed(self) -> bool:
        return self.decision.decision.value == "ALLOW"

    @property
    def reason_code(self) -> str:
        return self.decision.reason_code


AuthorizationResult = PolicyDecision


def resource_scope_for_tenant(
    tenant: TenantContext,
    *,
    classification: ResourceClassification,
    owner_subject_id: str | None = None,
) -> ResourceScope:
    """Create canonical scope without inventing a second tenancy contract."""

    if tenant.mode is TenantMode.PERSONAL_LOCAL:
        return ResourceScope(
            scope_type=ResourceScopeType.PERSONAL,
            owner_subject_id=owner_subject_id,
            classification=classification,
        )
    if tenant.workspace_id is not None:
        return ResourceScope(
            scope_type=ResourceScopeType.WORKSPACE,
            owner_subject_id=owner_subject_id,
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            classification=classification,
        )
    return ResourceScope(
        scope_type=ResourceScopeType.ORGANIZATION,
        owner_subject_id=owner_subject_id,
        organization_id=tenant.organization_id,
        classification=classification,
    )


__all__ = [
    "Action",
    "AuthorizationResult",
    "BreakGlassGrant",
    "GovernanceActorAdapter",
    "PolicyDecision",
    "PolicyRequest",
    "RelationshipContext",
    "Resource",
    "ResourceType",
    "Role",
    "resource_scope_for_tenant",
]
