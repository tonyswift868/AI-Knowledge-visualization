from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from app.enterprise.contracts_v1 import (
    AccessDecision,
    AccessDecisionKind,
    AuditEnvelope,
    AuditMetadataEntry,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.governance.audit import SQLiteAuditLog
from app.governance.models import (
    Action,
    BreakGlassGrant,
    GovernanceActorAdapter,
    PolicyDecision,
    PolicyRequest,
    Resource,
    ResourceType,
    Role,
)


POLICY_ID = "mentorflow.enterprise.authorization.v1"
DEFAULT_POLICY = AccessDecisionKind.DENY


def _actions(*values: Action) -> frozenset[Action]:
    return frozenset(values)


_CONTENT_MANAGE = _actions(
    Action.READ,
    Action.CREATE,
    Action.UPDATE,
    Action.DELETE,
    Action.MANAGE,
    Action.EXPORT,
)
_ORG_MANAGE = _actions(
    Action.READ,
    Action.READ_METADATA,
    Action.CREATE,
    Action.UPDATE,
    Action.DELETE,
    Action.MANAGE,
)


def _freeze_role_matrix(
    value: Mapping[Role, Mapping[ResourceType, frozenset[Action]]],
) -> Mapping[Role, Mapping[ResourceType, frozenset[Action]]]:
    return MappingProxyType(
        {
            role: MappingProxyType(dict(resources))
            for role, resources in value.items()
        }
    )


ROLE_MATRIX = _freeze_role_matrix(
    {
        Role.OWNER: {
            Resource.ORGANIZATION: _ORG_MANAGE,
            Resource.MEMBERSHIP: _ORG_MANAGE,
            Resource.WORKSPACE: _ORG_MANAGE,
            Resource.DOCUMENT: _CONTENT_MANAGE,
            Resource.KNOWLEDGE_MAP: _CONTENT_MANAGE,
            Resource.QUESTION_BANK: _CONTENT_MANAGE,
            Resource.PLAN: _CONTENT_MANAGE,
            Resource.TEACHER_SESSION: _CONTENT_MANAGE | _actions(Action.EXECUTE),
            Resource.REVIEW_CAMPAIGN: _CONTENT_MANAGE | _actions(Action.EXECUTE),
            Resource.STUDENT_PROGRESS: _actions(
                Action.READ, Action.READ_METADATA, Action.UPDATE, Action.EXPORT
            ),
            Resource.PROVIDER_CONFIG: _ORG_MANAGE,
            Resource.COST: _actions(
                Action.READ, Action.READ_METADATA, Action.MANAGE, Action.EXPORT
            ),
            Resource.AUDIT: _actions(Action.READ, Action.READ_METADATA, Action.EXPORT),
            Resource.EXPORT: _actions(Action.CREATE, Action.READ, Action.MANAGE),
        },
        Role.ADMIN: {
            Resource.ORGANIZATION: _actions(
                Action.READ, Action.READ_METADATA, Action.UPDATE, Action.MANAGE
            ),
            Resource.MEMBERSHIP: _ORG_MANAGE,
            Resource.WORKSPACE: _ORG_MANAGE,
            Resource.DOCUMENT: _CONTENT_MANAGE,
            Resource.KNOWLEDGE_MAP: _CONTENT_MANAGE,
            Resource.QUESTION_BANK: _CONTENT_MANAGE,
            Resource.PLAN: _CONTENT_MANAGE,
            Resource.TEACHER_SESSION: _CONTENT_MANAGE | _actions(Action.EXECUTE),
            Resource.REVIEW_CAMPAIGN: _CONTENT_MANAGE | _actions(Action.EXECUTE),
            Resource.STUDENT_PROGRESS: _actions(
                Action.READ, Action.READ_METADATA, Action.UPDATE, Action.EXPORT
            ),
            Resource.PROVIDER_CONFIG: _ORG_MANAGE,
            Resource.COST: _actions(
                Action.READ, Action.READ_METADATA, Action.MANAGE, Action.EXPORT
            ),
            Resource.AUDIT: _actions(Action.READ, Action.READ_METADATA, Action.EXPORT),
            Resource.EXPORT: _actions(Action.CREATE, Action.READ, Action.MANAGE),
        },
        Role.CONTENT_MANAGER: {
            Resource.ORGANIZATION: _actions(Action.READ_METADATA),
            Resource.MEMBERSHIP: _actions(Action.READ_METADATA),
            Resource.WORKSPACE: _actions(Action.READ_METADATA),
            Resource.DOCUMENT: _CONTENT_MANAGE,
            Resource.KNOWLEDGE_MAP: _CONTENT_MANAGE,
            Resource.QUESTION_BANK: _CONTENT_MANAGE,
            Resource.PLAN: _CONTENT_MANAGE,
            Resource.TEACHER_SESSION: _actions(Action.READ),
            Resource.REVIEW_CAMPAIGN: _CONTENT_MANAGE,
            Resource.EXPORT: _actions(Action.CREATE, Action.READ),
        },
        Role.INSTRUCTOR: {
            Resource.ORGANIZATION: _actions(Action.READ_METADATA),
            Resource.MEMBERSHIP: _actions(Action.READ_METADATA),
            Resource.WORKSPACE: _actions(Action.READ_METADATA),
            Resource.DOCUMENT: _actions(Action.READ),
            Resource.KNOWLEDGE_MAP: _actions(Action.READ),
            Resource.QUESTION_BANK: _actions(Action.READ, Action.CREATE, Action.UPDATE),
            Resource.PLAN: _actions(Action.READ, Action.CREATE, Action.UPDATE),
            Resource.TEACHER_SESSION: _actions(
                Action.READ, Action.CREATE, Action.UPDATE, Action.EXECUTE
            ),
            Resource.REVIEW_CAMPAIGN: _actions(
                Action.READ, Action.CREATE, Action.UPDATE, Action.EXECUTE
            ),
            Resource.STUDENT_PROGRESS: _actions(
                Action.READ, Action.READ_METADATA, Action.UPDATE
            ),
            Resource.COST: _actions(Action.READ),
            Resource.EXPORT: _actions(Action.CREATE),
        },
        Role.LEARNER: {
            Resource.WORKSPACE: _actions(Action.READ_METADATA),
            Resource.DOCUMENT: _actions(Action.READ),
            Resource.KNOWLEDGE_MAP: _actions(Action.READ),
            Resource.QUESTION_BANK: _actions(Action.READ),
            Resource.PLAN: _actions(Action.READ),
            Resource.TEACHER_SESSION: _actions(Action.READ, Action.EXECUTE),
            Resource.REVIEW_CAMPAIGN: _actions(Action.READ, Action.EXECUTE),
            Resource.STUDENT_PROGRESS: _actions(Action.READ, Action.UPDATE),
            Resource.COST: _actions(Action.READ),
            Resource.EXPORT: _actions(Action.CREATE),
        },
        Role.AUDITOR: {
            Resource.ORGANIZATION: _actions(Action.READ_METADATA),
            Resource.MEMBERSHIP: _actions(Action.READ_METADATA),
            Resource.WORKSPACE: _actions(Action.READ_METADATA),
            Resource.COST: _actions(Action.READ, Action.READ_METADATA),
            Resource.AUDIT: _actions(Action.READ, Action.READ_METADATA),
        },
    }
)


@dataclass(frozen=True, slots=True)
class BreakGlassPolicy:
    enabled: bool = False
    maximum_duration: timedelta = timedelta(minutes=15)

    def __post_init__(self) -> None:
        if self.maximum_duration <= timedelta(0):
            raise ValueError("maximum_duration must be positive")
        if self.maximum_duration > timedelta(minutes=30):
            raise ValueError("break-glass duration cannot exceed 30 minutes")


class AuthorizationAuditError(RuntimeError):
    """No decision is usable when its mandatory enterprise audit append fails."""


class BreakGlassDenied(ValueError):
    pass


def _audit_metadata(**values: str | int | float | bool | None) -> tuple[AuditMetadataEntry, ...]:
    return tuple(
        AuditMetadataEntry(key=key, value=value)
        for key, value in sorted(values.items())
        if value is not None
    )


class AuthorizationService:
    def __init__(
        self,
        audit_log: SQLiteAuditLog,
        *,
        break_glass_policy: BreakGlassPolicy = BreakGlassPolicy(),
    ) -> None:
        self._audit_log = audit_log
        self._break_glass_policy = break_glass_policy

    @staticmethod
    def role_matrix() -> Mapping[Role, Mapping[ResourceType, frozenset[Action]]]:
        return ROLE_MATRIX

    def issue_break_glass(
        self,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        reason: str,
        approved_by: str,
        actions: frozenset[Action],
        resources: frozenset[ResourceType],
        resource_ids: frozenset[str],
        resource_scope: ResourceScope,
        trace_id: str,
        duration: timedelta = timedelta(minutes=10),
        now: datetime | None = None,
    ) -> BreakGlassGrant:
        issued_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        actor = subject.actor
        if not self._break_glass_policy.enabled:
            raise BreakGlassDenied("BREAK_GLASS_DISABLED")
        if tenant.mode is not TenantMode.ENTERPRISE_SERVER:
            raise BreakGlassDenied("BREAK_GLASS_ENTERPRISE_ONLY")
        if not subject.active or subject.effective_role not in {Role.OWNER, Role.ADMIN}:
            raise BreakGlassDenied("BREAK_GLASS_ADMIN_REQUIRED")
        if actor.organization_id != tenant.organization_id:
            raise BreakGlassDenied("BREAK_GLASS_CROSS_TENANT_DENIED")
        if tenant.workspace_id is not None and (
            subject.effective_role != Role.OWNER
            and tenant.workspace_id not in subject.workspace_memberships
        ):
            raise BreakGlassDenied("BREAK_GLASS_WORKSPACE_DENIED")
        if approved_by.strip() == subject.actor_id:
            raise BreakGlassDenied("BREAK_GLASS_SEPARATE_APPROVER_REQUIRED")
        if duration <= timedelta(0) or duration > self._break_glass_policy.maximum_duration:
            raise BreakGlassDenied("BREAK_GLASS_DURATION_INVALID")
        grant = BreakGlassGrant(
            grant_id="bg_" + uuid4().hex,
            actor_id=subject.actor_id,
            organization_id=str(tenant.organization_id),
            workspace_id=tenant.workspace_id,
            reason=reason,
            approved_by=approved_by,
            issued_at=issued_at,
            expires_at=issued_at + duration,
            actions=actions,
            resources=resources,
            resource_ids=resource_ids,
            resource_scope=resource_scope,
        )
        self._append_audit(
            AuditEnvelope(
                event_id="audit_" + uuid4().hex,
                actor_context=actor,
                action="break_glass.issue",
                resource_type="break_glass",
                resource_id=grant.grant_id,
                outcome=AccessDecisionKind.ALLOW.value,
                reason_code="BREAK_GLASS_ISSUED",
                occurred_at=issued_at,
                request_id=actor.request_id,
                trace_id=trace_id,
                metadata_redacted=_audit_metadata(
                    approved_by=grant.approved_by,
                    expires_at=grant.expires_at.isoformat(),
                    grant_reason=grant.reason,
                    resource_id_count=len(grant.resource_ids),
                    resource_scope=grant.resource_scope.scope_type.value,
                ),
            )
        )
        return grant

    def authorize(self, request: PolicyRequest) -> PolicyDecision:
        outcome, reason_code = self._base_decision(request)
        break_glass_used = False
        if outcome is AccessDecisionKind.DENY and self._valid_break_glass(request):
            outcome = AccessDecisionKind.ALLOW
            reason_code = "BREAK_GLASS_ACTIVE"
            break_glass_used = True
        decision = AccessDecision(
            decision=outcome,
            policy_id=POLICY_ID,
            reason_code=reason_code,
            actor=request.actor,
            resource=request.resource_scope,
            action=f"{request.resource.value}.{request.action.value.lower()}",
            evaluated_at=request.occurred_at,
        )
        grant = request.break_glass if break_glass_used else None
        record = self._append_audit(
            AuditEnvelope(
                event_id="audit_" + uuid4().hex,
                actor_context=request.actor,
                action=decision.action,
                resource_type=request.resource.value,
                resource_id=request.resource_id,
                outcome=decision.decision.value,
                reason_code=decision.reason_code,
                occurred_at=request.occurred_at,
                request_id=request.request_id,
                trace_id=request.trace_id,
                metadata_redacted=_audit_metadata(
                    break_glass_used=break_glass_used,
                    grant_id=grant.grant_id if grant is not None else None,
                    grant_reason=grant.reason if grant is not None else None,
                    policy_id=POLICY_ID,
                    role=request.subject.effective_role.value,
                    tenant_mode=request.tenant.mode.value,
                ),
            )
        )
        return PolicyDecision(
            decision=decision,
            audit_sequence=record.sequence,
            break_glass_used=break_glass_used,
        )

    def _append_audit(self, envelope: AuditEnvelope):
        try:
            return self._audit_log.append(envelope)
        except Exception as exc:
            raise AuthorizationAuditError("AUTHORIZATION_AUDIT_APPEND_FAILED") from exc

    @staticmethod
    def _base_decision(request: PolicyRequest) -> tuple[AccessDecisionKind, str]:
        subject = request.subject
        actor = request.actor
        tenant = request.tenant
        scope = request.resource_scope
        relationships = request.relationships
        role = subject.effective_role
        if not subject.active:
            return AccessDecisionKind.DENY, "ACTOR_INACTIVE"

        if tenant.mode is TenantMode.PERSONAL_LOCAL:
            if scope.scope_type is not ResourceScopeType.PERSONAL:
                return AccessDecisionKind.DENY, "PERSONAL_LOCAL_SCOPE_REQUIRED"
            if actor.organization_id is not None or actor.workspace_id is not None:
                return AccessDecisionKind.DENY, "PERSONAL_LOCAL_ENTERPRISE_CONTEXT_DENIED"
            if scope.owner_subject_id not in {None, subject.actor_id}:
                return AccessDecisionKind.DENY, "PERSONAL_LOCAL_OWNER_DENIED"
        else:
            if actor.organization_id != tenant.organization_id:
                return AccessDecisionKind.DENY, "CROSS_TENANT_DENIED"
            if scope.scope_type is ResourceScopeType.PERSONAL:
                return AccessDecisionKind.DENY, "ENTERPRISE_RESOURCE_TENANT_SCOPE_REQUIRED"
            if scope.organization_id != tenant.organization_id:
                return AccessDecisionKind.DENY, "RESOURCE_ORGANIZATION_MISMATCH"
            if tenant.workspace_id is not None:
                if scope.workspace_id != tenant.workspace_id:
                    return AccessDecisionKind.DENY, "RESOURCE_WORKSPACE_MISMATCH"
                if (
                    role != Role.OWNER
                    and tenant.workspace_id not in subject.workspace_memberships
                ):
                    return AccessDecisionKind.DENY, "WORKSPACE_RELATIONSHIP_REQUIRED"

        allowed_actions = ROLE_MATRIX.get(role, {}).get(request.resource, frozenset())
        if request.action not in allowed_actions:
            return AccessDecisionKind.DENY, "ROLE_MATRIX_DENY"

        if role == Role.AUDITOR:
            if request.action not in {Action.READ, Action.READ_METADATA}:
                return AccessDecisionKind.DENY, "AUDITOR_READ_ONLY"
            if request.resource in {Resource.MEMBERSHIP, Resource.WORKSPACE} and not (
                relationships.metadata_only or request.action == Action.READ_METADATA
            ):
                return AccessDecisionKind.DENY, "AUDITOR_METADATA_ONLY"

        if role == Role.INSTRUCTOR and request.resource in {
            Resource.TEACHER_SESSION,
            Resource.REVIEW_CAMPAIGN,
            Resource.STUDENT_PROGRESS,
            Resource.COST,
            Resource.EXPORT,
        }:
            learner_allowed = (
                relationships.learner_id is not None
                and relationships.learner_id in relationships.assigned_learner_ids
            )
            cohort_allowed = (
                relationships.cohort_id is not None
                and relationships.cohort_id in relationships.assigned_cohort_ids
                and (
                    relationships.learner_id is None
                    or relationships.cohort_id in relationships.learner_cohort_ids
                )
            )
            if not (learner_allowed or cohort_allowed):
                return AccessDecisionKind.DENY, "INSTRUCTOR_ASSIGNMENT_REQUIRED"

        if role == Role.LEARNER and request.resource in {
            Resource.TEACHER_SESSION,
            Resource.REVIEW_CAMPAIGN,
            Resource.STUDENT_PROGRESS,
            Resource.COST,
            Resource.EXPORT,
        }:
            target = relationships.learner_id or relationships.resource_owner_id
            if target != subject.actor_id:
                return AccessDecisionKind.DENY, "LEARNER_OWN_RESOURCE_ONLY"

        if (
            role == Role.ADMIN
            and request.resource == Resource.STUDENT_PROGRESS
            and relationships.private_student_model
        ):
            required = (
                "student_model:manage_private"
                if request.action in {Action.UPDATE, Action.DELETE, Action.MANAGE}
                else "student_model:read_private"
            )
            if required not in relationships.explicit_permissions:
                return AccessDecisionKind.DENY, "ADMIN_PRIVATE_STUDENT_MODEL_NOT_GRANTED"

        if (
            request.resource == Resource.PROVIDER_CONFIG
            and relationships.personal_resource
            and relationships.resource_owner_id != subject.actor_id
            and "provider_config:manage_personal" not in relationships.explicit_permissions
        ):
            return AccessDecisionKind.DENY, "PERSONAL_PROVIDER_OWNER_REQUIRED"

        return AccessDecisionKind.ALLOW, "ROLE_AND_RELATIONSHIP_ALLOW"

    def _valid_break_glass(self, request: PolicyRequest) -> bool:
        grant = request.break_glass
        policy = self._break_glass_policy
        subject = request.subject
        if not policy.enabled or grant is None:
            return False
        if request.tenant.mode is not TenantMode.ENTERPRISE_SERVER:
            return False
        if not subject.active or subject.effective_role not in {Role.OWNER, Role.ADMIN}:
            return False
        if grant.actor_id != subject.actor_id:
            return False
        if subject.organization_id != request.tenant.organization_id:
            return False
        if grant.organization_id != request.tenant.organization_id:
            return False
        if grant.workspace_id != request.tenant.workspace_id:
            return False
        if request.action not in grant.actions or request.resource not in grant.resources:
            return False
        if request.resource_id not in grant.resource_ids:
            return False
        if request.resource_scope != grant.resource_scope:
            return False
        if grant.approved_by == subject.actor_id:
            return False
        if grant.expires_at - grant.issued_at > policy.maximum_duration:
            return False
        return grant.issued_at <= request.occurred_at < grant.expires_at


__all__ = [
    "AuthorizationAuditError",
    "AuthorizationService",
    "BreakGlassDenied",
    "BreakGlassPolicy",
    "DEFAULT_POLICY",
    "POLICY_ID",
    "ROLE_MATRIX",
]
