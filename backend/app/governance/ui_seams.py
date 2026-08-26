from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.governance.authorization import ROLE_MATRIX
from app.governance.cost_views import CostViewScope, TenantCostView
from app.governance.models import (
    Action,
    GovernanceActorAdapter,
    PolicyDecision,
    PolicyRequest,
    ResourceType,
    Role,
)


@dataclass(frozen=True, slots=True)
class NavigationPermission:
    resource: ResourceType
    actions: tuple[Action, ...]
    relationship_check_required: bool = True


class NavigationState(str, Enum):
    VISIBLE = "VISIBLE"
    DISABLED = "DISABLED"
    HIDDEN = "HIDDEN"


class AdminNavigationCategory(str, Enum):
    ORGANIZATION_ADMIN = "Organization Admin"
    WORKSPACE_ADMIN = "Workspace Admin"
    CONTENT = "Content"
    INSTRUCTOR = "Instructor"
    LEARNER = "Learner"
    AUDIT = "Audit"
    COST = "Cost"
    PROVIDER_SETTINGS = "Provider Settings"


@dataclass(frozen=True, slots=True)
class AdminNavigationEntry:
    category: AdminNavigationCategory
    state: NavigationState
    resource: ResourceType
    relationship_check_required: bool = True


@dataclass(frozen=True, slots=True)
class PermissionAwareNavigation:
    """UI capability candidates; backend authorization remains authoritative."""

    role: Role
    permissions: tuple[NavigationPermission, ...]

    @classmethod
    def for_subject(cls, subject: GovernanceActorAdapter) -> "PermissionAwareNavigation":
        permissions = tuple(
            NavigationPermission(
                resource=resource,
                actions=tuple(sorted(actions, key=lambda action: action.value)),
            )
            for resource, actions in sorted(
                ROLE_MATRIX.get(subject.effective_role, {}).items(),
                key=lambda item: item[0].value,
            )
            if actions
        )
        return cls(role=subject.effective_role, permissions=permissions)

    def permits_candidate(self, resource: ResourceType, action: Action) -> bool:
        return any(
            item.resource is resource and action in item.actions
            for item in self.permissions
        )

    def admin_entries(self) -> tuple[AdminNavigationEntry, ...]:
        """UI projection only; every displayed action still requires backend authorization."""

        definitions = (
            (AdminNavigationCategory.ORGANIZATION_ADMIN, ResourceType.ORGANIZATION, Action.READ_METADATA, Action.MANAGE),
            (AdminNavigationCategory.WORKSPACE_ADMIN, ResourceType.WORKSPACE, Action.READ_METADATA, Action.MANAGE),
            (AdminNavigationCategory.CONTENT, ResourceType.DOCUMENT, Action.READ, Action.UPDATE),
            (AdminNavigationCategory.INSTRUCTOR, ResourceType.TEACHER_SESSION, Action.READ, Action.EXECUTE),
            (AdminNavigationCategory.LEARNER, ResourceType.STUDENT_PROGRESS, Action.READ, Action.UPDATE),
            (AdminNavigationCategory.AUDIT, ResourceType.AUDIT, Action.READ_METADATA, Action.READ),
            (AdminNavigationCategory.COST, ResourceType.COST, Action.READ, Action.MANAGE),
            (AdminNavigationCategory.PROVIDER_SETTINGS, ResourceType.PROVIDER_CONFIG, Action.READ, Action.UPDATE),
        )
        entries: list[AdminNavigationEntry] = []
        for category, resource, visible_action, enabled_action in definitions:
            if self.permits_candidate(resource, enabled_action):
                state = NavigationState.VISIBLE
            elif self.permits_candidate(resource, visible_action):
                state = NavigationState.DISABLED
            else:
                state = NavigationState.HIDDEN
            entries.append(
                AdminNavigationEntry(
                    category=category,
                    state=state,
                    resource=resource,
                )
            )
        return tuple(entries)


@dataclass(frozen=True, slots=True)
class AccessDeniedReadModel:
    policy_id: str
    reason_code: str
    action: str
    resource_type: str
    request_id: str
    trace_id: str
    retryable: bool = False

    @classmethod
    def from_denial(
        cls,
        request: PolicyRequest,
        decision: PolicyDecision,
    ) -> "AccessDeniedReadModel":
        if decision.allowed:
            raise ValueError("AccessDeniedReadModel requires a DENY decision")
        return cls(
            policy_id=decision.decision.policy_id,
            reason_code=decision.reason_code,
            action=decision.decision.action,
            resource_type=request.resource.value,
            request_id=request.request_id,
            trace_id=request.trace_id,
        )

    def to_public_dict(self) -> dict[str, str | bool]:
        return {
            "policy_id": self.policy_id,
            "reason_code": self.reason_code,
            "action": self.action,
            "resource_type": self.resource_type,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class RoleBadge:
    code: str
    label: str

    @classmethod
    def for_subject(cls, subject: GovernanceActorAdapter) -> "RoleBadge":
        labels = {
            Role.ORGANIZATION_OWNER: "Organization Owner",
            Role.ADMIN: "Admin",
            Role.CONTENT_MANAGER: "Content Manager",
            Role.INSTRUCTOR: "Instructor",
            Role.LEARNER: "Learner",
            Role.AUDITOR: "Auditor",
        }
        return cls(
            code=subject.effective_role.value,
            label=labels[subject.effective_role],
        )


@dataclass(frozen=True, slots=True)
class OrganizationCostReadModel:
    organization_id: str
    currency: str | None
    known_cost_subtotal: Decimal | None
    total_cost: Decimal | None
    record_count: int
    unknown_cost_count: int
    read_only: bool = True

    @classmethod
    def from_view(cls, view: TenantCostView) -> "OrganizationCostReadModel":
        if view.scope is not CostViewScope.ORGANIZATION or view.organization_id is None:
            raise ValueError("organization cost read model requires an organization view")
        if not view.read_only:
            raise ValueError("organization cost view must be read-only")
        return cls(
            organization_id=view.organization_id,
            currency=view.currency,
            known_cost_subtotal=view.known_cost_subtotal,
            total_cost=view.total_cost,
            record_count=view.record_count,
            unknown_cost_count=view.unknown_cost_count,
        )


__all__ = [
    "AccessDeniedReadModel",
    "AdminNavigationCategory",
    "AdminNavigationEntry",
    "NavigationPermission",
    "NavigationState",
    "OrganizationCostReadModel",
    "PermissionAwareNavigation",
    "RoleBadge",
]
