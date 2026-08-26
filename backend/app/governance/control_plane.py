from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Generic, TypeVar

from app.cost.estimation import CostEstimate
from app.enterprise.contracts_v1 import (
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
)
from app.governance.models import (
    Action,
    GovernanceActorAdapter,
    RelationshipContext,
    ResourceType,
    Role,
)
from app.governance.quotas import (
    QuotaEvaluation,
    QuotaScope,
    QuotaSubject,
    QuotaUsageSnapshot,
    TenantQuotaService,
)
from app.governance.runtime import (
    AuthorizedOperation,
    AuthorizedQueryPolicy,
    AuthorizedResourceCandidate,
    EnforcementReceipt,
    GovernanceEnforcementService,
    ResourceVisibilityFilter,
)


T = TypeVar("T")


def _required(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _organization_scope(organization_id: str) -> ResourceScope:
    return ResourceScope(
        scope_type=ResourceScopeType.ORGANIZATION,
        organization_id=organization_id,
        classification=ResourceClassification.RESTRICTED,
    )


def _workspace_scope(organization_id: str, workspace_id: str) -> ResourceScope:
    return ResourceScope(
        scope_type=ResourceScopeType.WORKSPACE,
        organization_id=organization_id,
        workspace_id=workspace_id,
        classification=ResourceClassification.RESTRICTED,
    )


class GovernanceMutationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class MemberStatus(str, Enum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class OrganizationMember:
    member_id: str
    organization_id: str
    subject_id: str
    email: str
    status: MemberStatus = MemberStatus.INVITED
    roles: frozenset[Role] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for name in ("member_id", "organization_id", "subject_id", "email"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        object.__setattr__(self, "roles", frozenset(self.roles))


class WorkspaceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    workspace_id: str
    organization_id: str
    name: str
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE

    def __post_init__(self) -> None:
        for name in ("workspace_id", "organization_id", "name"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))


@dataclass(frozen=True, slots=True)
class WorkspaceMembership:
    workspace_id: str
    organization_id: str
    member_id: str
    roles: frozenset[Role] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for name in ("workspace_id", "organization_id", "member_id"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        object.__setattr__(self, "roles", frozenset(self.roles))


@dataclass(frozen=True, slots=True)
class CohortMembership:
    membership_id: str
    organization_id: str
    workspace_id: str
    cohort_id: str
    learner_id: str

    def __post_init__(self) -> None:
        for name in (
            "membership_id",
            "organization_id",
            "workspace_id",
            "cohort_id",
            "learner_id",
        ):
            object.__setattr__(self, name, _required(name, getattr(self, name)))


@dataclass(frozen=True, slots=True)
class InstructorAssignment:
    assignment_id: str
    organization_id: str
    workspace_id: str
    cohort_id: str
    instructor_id: str

    def __post_init__(self) -> None:
        for name in (
            "assignment_id",
            "organization_id",
            "workspace_id",
            "cohort_id",
            "instructor_id",
        ):
            object.__setattr__(self, name, _required(name, getattr(self, name)))


class OrganizationGovernanceService:
    """Organization membership mutations gated before touching the store."""

    def __init__(self, enforcement: GovernanceEnforcementService) -> None:
        self._enforcement = enforcement
        self._members: dict[str, OrganizationMember] = {}
        self._lock = threading.RLock()

    def _operation(
        self,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        action: Action,
        member_id: str,
        organization_id: str,
        trace_id: str,
    ) -> AuthorizedOperation:
        return AuthorizedOperation(
            subject=subject,
            tenant=tenant,
            action=action,
            resource=ResourceType.MEMBERSHIP,
            resource_id=member_id,
            resource_scope=_organization_scope(organization_id),
            trace_id=trace_id,
        )

    def _mutate(
        self,
        operation: AuthorizedOperation,
        mutation: Callable[[], OrganizationMember],
    ) -> EnforcementReceipt[OrganizationMember]:
        return self._enforcement.enforce_write(operation, mutation)

    def invite_member(
        self,
        member: OrganizationMember,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> EnforcementReceipt[OrganizationMember]:
        def mutation() -> OrganizationMember:
            with self._lock:
                if member.member_id in self._members:
                    raise GovernanceMutationError("MEMBER_ALREADY_EXISTS")
                self._members[member.member_id] = member
                return member

        return self._mutate(
            self._operation(
                subject=subject,
                tenant=tenant,
                action=Action.CREATE,
                member_id=member.member_id,
                organization_id=member.organization_id,
                trace_id=trace_id,
            ),
            mutation,
        )

    def _set_status(
        self,
        member_id: str,
        status: MemberStatus,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> EnforcementReceipt[OrganizationMember]:
        with self._lock:
            current = self._members.get(member_id)
        organization_id = (
            current.organization_id
            if current is not None
            else _required("organization_id", tenant.organization_id or "")
        )

        def mutation() -> OrganizationMember:
            with self._lock:
                try:
                    current = self._members[member_id]
                except KeyError as exc:
                    raise GovernanceMutationError("MEMBER_NOT_FOUND") from exc
                updated = replace(current, status=status)
                self._members[member_id] = updated
                return updated

        return self._mutate(
            self._operation(
                subject=subject,
                tenant=tenant,
                action=Action.UPDATE if status is MemberStatus.SUSPENDED else Action.DELETE,
                member_id=member_id,
                organization_id=organization_id,
                trace_id=trace_id,
            ),
            mutation,
        )

    def suspend_member(self, member_id: str, **context: object) -> EnforcementReceipt[OrganizationMember]:
        return self._set_status(member_id, MemberStatus.SUSPENDED, **context)  # type: ignore[arg-type]

    def revoke_member(self, member_id: str, **context: object) -> EnforcementReceipt[OrganizationMember]:
        return self._set_status(member_id, MemberStatus.REVOKED, **context)  # type: ignore[arg-type]

    def _change_role(
        self,
        member_id: str,
        role: Role,
        *,
        add: bool,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> EnforcementReceipt[OrganizationMember]:
        with self._lock:
            current = self._members.get(member_id)
        organization_id = (
            current.organization_id
            if current is not None
            else _required("organization_id", tenant.organization_id or "")
        )

        def mutation() -> OrganizationMember:
            with self._lock:
                try:
                    current = self._members[member_id]
                except KeyError as exc:
                    raise GovernanceMutationError("MEMBER_NOT_FOUND") from exc
                roles = current.roles | {role} if add else current.roles - {role}
                updated = replace(current, roles=frozenset(roles))
                self._members[member_id] = updated
                return updated

        return self._mutate(
            self._operation(
                subject=subject,
                tenant=tenant,
                action=Action.MANAGE,
                member_id=member_id,
                organization_id=organization_id,
                trace_id=trace_id,
            ),
            mutation,
        )

    def assign_role(self, member_id: str, role: Role, **context: object) -> EnforcementReceipt[OrganizationMember]:
        return self._change_role(member_id, role, add=True, **context)  # type: ignore[arg-type]

    def remove_role(self, member_id: str, role: Role, **context: object) -> EnforcementReceipt[OrganizationMember]:
        return self._change_role(member_id, role, add=False, **context)  # type: ignore[arg-type]

    def snapshot(self) -> tuple[OrganizationMember, ...]:
        with self._lock:
            return tuple(sorted(self._members.values(), key=lambda item: item.member_id))


class WorkspaceGovernanceService:
    """Workspace state, membership, roles, cohorts, and instructor assignments."""

    def __init__(self, enforcement: GovernanceEnforcementService) -> None:
        self._enforcement = enforcement
        self._workspaces: dict[str, WorkspaceRecord] = {}
        self._memberships: dict[tuple[str, str], WorkspaceMembership] = {}
        self._cohorts: dict[str, CohortMembership] = {}
        self._instructors: dict[str, InstructorAssignment] = {}
        self._lock = threading.RLock()

    def _operation(
        self,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        action: Action,
        resource_id: str,
        organization_id: str,
        workspace_id: str,
        trace_id: str,
    ) -> AuthorizedOperation:
        return AuthorizedOperation(
            subject=subject,
            tenant=tenant,
            action=action,
            resource=ResourceType.WORKSPACE,
            resource_id=resource_id,
            resource_scope=_workspace_scope(
                organization_id,
                workspace_id,
            ),
            trace_id=trace_id,
        )

    def create_workspace(
        self,
        workspace: WorkspaceRecord,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> EnforcementReceipt[WorkspaceRecord]:
        def mutation() -> WorkspaceRecord:
            with self._lock:
                if workspace.workspace_id in self._workspaces:
                    raise GovernanceMutationError("WORKSPACE_ALREADY_EXISTS")
                self._workspaces[workspace.workspace_id] = workspace
                return workspace

        return self._enforcement.enforce_write(
            self._operation(
                subject=subject,
                tenant=tenant,
                action=Action.CREATE,
                resource_id=workspace.workspace_id,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                trace_id=trace_id,
            ),
            mutation,
        )

    def archive_workspace(
        self,
        workspace_id: str,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> EnforcementReceipt[WorkspaceRecord]:
        with self._lock:
            current = self._workspaces.get(workspace_id)
        organization_id = (
            current.organization_id
            if current is not None
            else _required("organization_id", tenant.organization_id or "")
        )

        def mutation() -> WorkspaceRecord:
            with self._lock:
                try:
                    current = self._workspaces[workspace_id]
                except KeyError as exc:
                    raise GovernanceMutationError("WORKSPACE_NOT_FOUND") from exc
                updated = replace(current, status=WorkspaceStatus.ARCHIVED)
                self._workspaces[workspace_id] = updated
                return updated

        return self._enforcement.enforce_write(
            self._operation(
                subject=subject,
                tenant=tenant,
                action=Action.DELETE,
                resource_id=workspace_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                trace_id=trace_id,
            ),
            mutation,
        )

    def _set_membership(
        self,
        membership: WorkspaceMembership,
        *,
        remove: bool,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> EnforcementReceipt[WorkspaceMembership]:
        def mutation() -> WorkspaceMembership:
            key = (membership.workspace_id, membership.member_id)
            with self._lock:
                if remove:
                    try:
                        return self._memberships.pop(key)
                    except KeyError as exc:
                        raise GovernanceMutationError("WORKSPACE_MEMBERSHIP_NOT_FOUND") from exc
                self._memberships[key] = membership
                return membership

        return self._enforcement.enforce_write(
            self._operation(
                subject=subject,
                tenant=tenant,
                action=Action.DELETE if remove else Action.MANAGE,
                resource_id=f"{membership.workspace_id}:{membership.member_id}",
                organization_id=membership.organization_id,
                workspace_id=membership.workspace_id,
                trace_id=trace_id,
            ),
            mutation,
        )

    def add_membership(self, membership: WorkspaceMembership, **context: object) -> EnforcementReceipt[WorkspaceMembership]:
        return self._set_membership(membership, remove=False, **context)  # type: ignore[arg-type]

    def remove_membership(self, membership: WorkspaceMembership, **context: object) -> EnforcementReceipt[WorkspaceMembership]:
        return self._set_membership(membership, remove=True, **context)  # type: ignore[arg-type]

    def bind_role(
        self,
        workspace_id: str,
        member_id: str,
        role: Role,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> EnforcementReceipt[WorkspaceMembership]:
        key = (workspace_id, member_id)

        def mutation() -> WorkspaceMembership:
            with self._lock:
                try:
                    current = self._memberships[key]
                except KeyError as exc:
                    raise GovernanceMutationError("WORKSPACE_MEMBERSHIP_NOT_FOUND") from exc
                updated = replace(current, roles=frozenset(current.roles | {role}))
                self._memberships[key] = updated
                return updated

        return self._enforcement.enforce_write(
            self._operation(
                subject=subject,
                tenant=tenant,
                action=Action.MANAGE,
                resource_id=f"{workspace_id}:{member_id}:role:{role.value}",
                organization_id=_required("organization_id", tenant.organization_id or ""),
                workspace_id=workspace_id,
                trace_id=trace_id,
            ),
            mutation,
        )

    def remove_role(
        self,
        workspace_id: str,
        member_id: str,
        role: Role,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> EnforcementReceipt[WorkspaceMembership]:
        key = (workspace_id, member_id)

        def mutation() -> WorkspaceMembership:
            with self._lock:
                try:
                    current = self._memberships[key]
                except KeyError as exc:
                    raise GovernanceMutationError("WORKSPACE_MEMBERSHIP_NOT_FOUND") from exc
                updated = replace(current, roles=frozenset(current.roles - {role}))
                self._memberships[key] = updated
                return updated

        return self._enforcement.enforce_write(
            self._operation(
                subject=subject,
                tenant=tenant,
                action=Action.MANAGE,
                resource_id=f"{workspace_id}:{member_id}:role:{role.value}",
                organization_id=_required("organization_id", tenant.organization_id or ""),
                workspace_id=workspace_id,
                trace_id=trace_id,
            ),
            mutation,
        )

    def assign_cohort_member(
        self,
        membership: CohortMembership,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> EnforcementReceipt[CohortMembership]:
        def mutation() -> CohortMembership:
            with self._lock:
                self._cohorts[membership.membership_id] = membership
                return membership

        return self._enforcement.enforce_write(
            self._operation(
                subject=subject,
                tenant=tenant,
                action=Action.MANAGE,
                resource_id=membership.membership_id,
                organization_id=membership.organization_id,
                workspace_id=membership.workspace_id,
                trace_id=trace_id,
            ),
            mutation,
        )

    def assign_instructor(
        self,
        assignment: InstructorAssignment,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> EnforcementReceipt[InstructorAssignment]:
        def mutation() -> InstructorAssignment:
            with self._lock:
                self._instructors[assignment.assignment_id] = assignment
                return assignment

        return self._enforcement.enforce_write(
            self._operation(
                subject=subject,
                tenant=tenant,
                action=Action.MANAGE,
                resource_id=assignment.assignment_id,
                organization_id=assignment.organization_id,
                workspace_id=assignment.workspace_id,
                trace_id=trace_id,
            ),
            mutation,
        )

    def workspace_memberships(self) -> tuple[WorkspaceMembership, ...]:
        with self._lock:
            return tuple(sorted(self._memberships.values(), key=lambda item: (item.workspace_id, item.member_id)))

    def workspaces(self) -> tuple[WorkspaceRecord, ...]:
        with self._lock:
            return tuple(sorted(self._workspaces.values(), key=lambda item: item.workspace_id))

    def cohort_memberships(self) -> tuple[CohortMembership, ...]:
        with self._lock:
            return tuple(sorted(self._cohorts.values(), key=lambda item: item.membership_id))

    def instructor_assignments(self) -> tuple[InstructorAssignment, ...]:
        with self._lock:
            return tuple(sorted(self._instructors.values(), key=lambda item: item.assignment_id))


class LearnerAssignmentQuery:
    """Joins assignments to cohorts and re-applies normal resource filtering."""

    def __init__(
        self,
        workspaces: WorkspaceGovernanceService,
        visibility: ResourceVisibilityFilter,
    ) -> None:
        self._workspaces = workspaces
        self._visibility = visibility

    def assigned_learner_ids(
        self,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> tuple[str, ...]:
        if subject.effective_role is not Role.INSTRUCTOR:
            raise ValueError("learner assignment query requires INSTRUCTOR role")
        workspace_id = _required("workspace_id", tenant.workspace_id or "")
        assigned_cohorts = frozenset(
            assignment.cohort_id
            for assignment in self._workspaces.instructor_assignments()
            if assignment.organization_id == tenant.organization_id
            and assignment.workspace_id == workspace_id
            and assignment.instructor_id == subject.actor_id
        )
        learner_cohorts: dict[str, set[str]] = {}
        for membership in self._workspaces.cohort_memberships():
            if (
                membership.organization_id == tenant.organization_id
                and membership.workspace_id == workspace_id
            ):
                learner_cohorts.setdefault(membership.learner_id, set()).add(
                    membership.cohort_id
                )
        candidates = tuple(
            AuthorizedResourceCandidate(
                resource_id=learner_id,
                resource_scope=_workspace_scope(
                    _required("organization_id", tenant.organization_id or ""),
                    workspace_id,
                ),
                payload=learner_id,
                relationships=RelationshipContext(
                    learner_id=learner_id,
                    cohort_id=next(
                        iter(sorted(cohorts & assigned_cohorts)),
                        next(iter(sorted(cohorts))),
                    ),
                    assigned_cohort_ids=assigned_cohorts,
                    learner_cohort_ids=frozenset(cohorts),
                ),
            )
            for learner_id, cohorts in sorted(learner_cohorts.items())
        )
        return self._visibility.list(
            AuthorizedQueryPolicy(
                subject=subject,
                tenant=tenant,
                action=Action.READ,
                resource=ResourceType.STUDENT_PROGRESS,
                trace_id=trace_id,
            ),
            candidates,
        )


@dataclass(frozen=True, slots=True)
class QuotaEnforcementReceipt(Generic[T]):
    evaluation: QuotaEvaluation
    evaluated_scopes: tuple[QuotaScope, ...]
    provider_call_invoked: bool
    result: T | None = None

    @property
    def allowed(self) -> bool:
        return self.evaluation.provider_execution_allowed


class QuotaEnforcementAdapter:
    """Applies org, workspace, and user policies conjunctively before provider I/O."""

    def __init__(self, quotas: TenantQuotaService) -> None:
        self._quotas = quotas

    def enforce_provider_call(
        self,
        *,
        subject: QuotaSubject,
        usage: QuotaUsageSnapshot,
        run_id: str,
        operation_id: str,
        selected_model: str | None,
        feature: str | None,
        provider_call: Callable[[], T],
        requested_feature_units: int = 1,
        estimate: CostEstimate | None = None,
        routing_available: bool = True,
    ) -> QuotaEnforcementReceipt[T]:
        evaluation = self._quotas.evaluate(
            subject=subject,
            usage=usage,
            run_id=run_id,
            operation_id=operation_id,
            selected_model=selected_model,
            feature=feature,
            requested_feature_units=requested_feature_units,
            estimate=estimate,
            provider_call_required=True,
            routing_available=routing_available,
        )
        policies = self._quotas.policies_for(subject)
        scope_rank = {
            QuotaScope.ORGANIZATION: 0,
            QuotaScope.WORKSPACE: 1,
            QuotaScope.USER: 2,
        }
        scopes = tuple(
            sorted({policy.scope for policy in policies}, key=scope_rank.__getitem__)
        )
        if not evaluation.provider_execution_allowed:
            return QuotaEnforcementReceipt(
                evaluation=evaluation,
                evaluated_scopes=scopes,
                provider_call_invoked=False,
            )
        return QuotaEnforcementReceipt(
            evaluation=evaluation,
            evaluated_scopes=scopes,
            provider_call_invoked=True,
            result=provider_call(),
        )


__all__ = [
    "CohortMembership",
    "GovernanceMutationError",
    "InstructorAssignment",
    "LearnerAssignmentQuery",
    "MemberStatus",
    "OrganizationGovernanceService",
    "OrganizationMember",
    "QuotaEnforcementAdapter",
    "QuotaEnforcementReceipt",
    "WorkspaceGovernanceService",
    "WorkspaceMembership",
    "WorkspaceRecord",
    "WorkspaceStatus",
]
