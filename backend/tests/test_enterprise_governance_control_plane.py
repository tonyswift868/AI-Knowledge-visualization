from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.enterprise.contracts_v1 import ActorContext, ActorKind, TenantContext, TenantMode
from app.governance.audit import SQLiteAuditLog
from app.governance.authorization import AuthorizationService
from app.governance.control_plane import (
    CohortMembership,
    InstructorAssignment,
    LearnerAssignmentQuery,
    MemberStatus,
    OrganizationGovernanceService,
    OrganizationMember,
    QuotaEnforcementAdapter,
    WorkspaceGovernanceService,
    WorkspaceMembership,
    WorkspaceRecord,
)
from app.governance.models import GovernanceActorAdapter, Role
from app.governance.provider_governance import (
    ProviderConfig,
    ProviderCredentialRef,
    ProviderGovernanceError,
    ProviderGovernanceService,
    ProviderScope,
)
from app.governance.quotas import (
    QuotaPolicy,
    QuotaScope,
    QuotaSubject,
    QuotaUsageSnapshot,
    TenantQuotaService,
)
from app.governance.runtime import GovernanceEnforcementService, ResourceVisibilityFilter


NOW = datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc)


def subject(
    role: Role,
    *,
    subject_id: str,
    organization_id: str = "org-a",
    workspace_id: str | None = "ws-a",
) -> GovernanceActorAdapter:
    actor = ActorContext(
        subject_id=subject_id,
        actor_kind=ActorKind.HUMAN,
        issuer="https://issuer.example.test",
        token_id=f"token-{subject_id}",
        session_id=f"session-{subject_id}",
        request_id=f"request-{subject_id}",
        authentication_time=NOW,
        authentication_strength="mfa",
        organization_id=organization_id,
        workspace_id=workspace_id,
        membership_id=f"membership-{subject_id}",
        roles=(role.value,),
    )
    return GovernanceActorAdapter(
        actor=actor,
        effective_role=role,
        workspace_memberships=(
            frozenset({workspace_id}) if workspace_id is not None else frozenset()
        ),
    )


def tenant(workspace_id: str | None = "ws-a", organization_id: str = "org-a") -> TenantContext:
    return TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )


def stack(tmp_path: Path):
    audit = SQLiteAuditLog(tmp_path / "control-plane-audit.sqlite3")
    authorization = AuthorizationService(audit)
    enforcement = GovernanceEnforcementService(authorization)
    return authorization, enforcement, audit


def test_organization_governance_denies_before_mutation_and_audits_lifecycle(
    tmp_path: Path,
) -> None:
    _authorization, enforcement, audit = stack(tmp_path)
    service = OrganizationGovernanceService(enforcement)
    member = OrganizationMember(
        member_id="member-1",
        organization_id="org-a",
        subject_id="learner-1",
        email="learner@example.test",
    )
    denied = service.invite_member(
        member,
        subject=subject(Role.LEARNER, subject_id="learner-admin-attempt"),
        tenant=tenant(workspace_id=None),
        trace_id="trace-member-denied",
    )
    assert denied.allowed is False
    assert denied.mutation_performed is False
    assert service.snapshot() == ()

    owner = subject(Role.OWNER, subject_id="owner-a")
    invited = service.invite_member(
        member,
        subject=owner,
        tenant=tenant(workspace_id=None),
        trace_id="trace-member-invite",
    )
    assigned = service.assign_role(
        member.member_id,
        Role.LEARNER,
        subject=owner,
        tenant=tenant(workspace_id=None),
        trace_id="trace-member-role",
    )
    suspended = service.suspend_member(
        member.member_id,
        subject=owner,
        tenant=tenant(workspace_id=None),
        trace_id="trace-member-suspend",
    )
    revoked = service.revoke_member(
        member.member_id,
        subject=owner,
        tenant=tenant(workspace_id=None),
        trace_id="trace-member-revoke",
    )
    assert invited.result is not None
    assert assigned.result is not None and assigned.result.roles == frozenset({Role.LEARNER})
    assert suspended.result is not None and suspended.result.status is MemberStatus.SUSPENDED
    assert revoked.result is not None and revoked.result.status is MemberStatus.REVOKED
    assert [record.envelope.outcome for record in audit.records()] == [
        "DENY",
        "ALLOW",
        "ALLOW",
        "ALLOW",
        "ALLOW",
    ]


def test_workspace_roles_are_independent_per_workspace_and_cross_tenant_is_denied(
    tmp_path: Path,
) -> None:
    _authorization, enforcement, audit = stack(tmp_path)
    service = WorkspaceGovernanceService(enforcement)
    owner = subject(Role.OWNER, subject_id="owner-a", workspace_id=None)
    org_tenant = tenant(workspace_id=None)
    for workspace_id in ("ws-a", "ws-b"):
        receipt = service.create_workspace(
            WorkspaceRecord(workspace_id, "org-a", workspace_id.upper()),
            subject=owner,
            tenant=org_tenant,
            trace_id=f"trace-create-{workspace_id}",
        )
        assert receipt.allowed
    for workspace_id, role in (("ws-a", Role.INSTRUCTOR), ("ws-b", Role.CONTENT_MANAGER)):
        membership = WorkspaceMembership(workspace_id, "org-a", "member-shared")
        service.add_membership(
            membership,
            subject=owner,
            tenant=org_tenant,
            trace_id=f"trace-membership-{workspace_id}",
        )
        service.bind_role(
            workspace_id,
            "member-shared",
            role,
            subject=owner,
            tenant=org_tenant,
            trace_id=f"trace-role-{workspace_id}",
        )
    roles = {
        membership.workspace_id: membership.roles
        for membership in service.workspace_memberships()
    }
    assert roles == {
        "ws-a": frozenset({Role.INSTRUCTOR}),
        "ws-b": frozenset({Role.CONTENT_MANAGER}),
    }

    denied = service.create_workspace(
        WorkspaceRecord("ws-foreign", "org-b", "Foreign"),
        subject=owner,
        tenant=org_tenant,
        trace_id="trace-cross-tenant-workspace",
    )
    assert denied.allowed is False
    assert denied.mutation_performed is False
    assert audit.records()[-1].envelope.reason_code == "RESOURCE_ORGANIZATION_MISMATCH"


def test_instructor_query_returns_only_learners_in_assigned_cohorts(tmp_path: Path) -> None:
    authorization, enforcement, _audit = stack(tmp_path)
    service = WorkspaceGovernanceService(enforcement)
    owner = subject(Role.OWNER, subject_id="owner-a")
    context = {"subject": owner, "tenant": tenant(), "trace_id": "trace-assignment"}
    service.assign_instructor(
        InstructorAssignment("assignment-1", "org-a", "ws-a", "cohort-a", "instructor-a"),
        **context,
    )
    service.assign_cohort_member(
        CohortMembership("cohort-member-1", "org-a", "ws-a", "cohort-a", "learner-a"),
        **context,
    )
    service.assign_cohort_member(
        CohortMembership("cohort-member-2", "org-a", "ws-a", "cohort-b", "learner-b"),
        **context,
    )
    service.assign_cohort_member(
        CohortMembership("cohort-member-3", "org-a", "ws-a", "cohort-b", "learner-a"),
        **context,
    )
    query = LearnerAssignmentQuery(service, ResourceVisibilityFilter(authorization))
    visible = query.assigned_learner_ids(
        subject=subject(Role.INSTRUCTOR, subject_id="instructor-a"),
        tenant=tenant(),
        trace_id="trace-instructor-learners",
    )
    assert visible == ("learner-a",)


def test_workspace_provider_policy_is_metadata_only_and_revoke_fences_sessions(
    tmp_path: Path,
) -> None:
    authorization, _enforcement, _audit = stack(tmp_path)
    providers = ProviderGovernanceService(authorization)
    owner = subject(Role.OWNER, subject_id="owner-a")
    config = ProviderConfig(
        config_id="provider-ws-a",
        organization_id="org-a",
        workspace_id="ws-a",
        provider="openai-compatible",
        secret_reference=ProviderCredentialRef("mfcred_workspace_reference_123"),
        approved_models=frozenset({"model-a"}),
        scope=ProviderScope.WORKSPACE,
    )
    public = providers.register(
        config,
        subject=owner,
        tenant=tenant(),
        trace_id="trace-provider-register",
    )
    assert providers.get_internal(config.config_id).scope is ProviderScope.WORKSPACE
    assert "secret_reference" not in public
    grant = providers.authorize_new_session(config.config_id, "model-a", now=NOW)
    providers.update_model_policy(
        config.config_id,
        frozenset({"model-a", "model-b"}),
        subject=owner,
        tenant=tenant(),
        trace_id="trace-provider-model-policy",
    )
    with pytest.raises(ProviderGovernanceError, match="PROVIDER_SESSION_STALE"):
        providers.validate_session(grant)
    providers.revoke(
        config.config_id,
        subject=owner,
        tenant=tenant(),
        trace_id="trace-provider-revoke",
        now=NOW,
    )
    with pytest.raises(ProviderGovernanceError, match="PROVIDER_CONFIG_NOT_ACTIVE"):
        providers.authorize_new_session(config.config_id, "model-b", now=NOW)


def test_strictest_quota_stops_before_provider_call(tmp_path: Path) -> None:
    authorization, _enforcement, _audit = stack(tmp_path)
    quotas = TenantQuotaService(authorization)
    owner = subject(Role.OWNER, subject_id="owner-a")
    current_tenant = tenant()
    policies = (
        QuotaPolicy(
            "org-policy",
            "org-a",
            QuotaScope.ORGANIZATION,
            "USD",
            feature_quotas={"chat": 100},
        ),
        QuotaPolicy(
            "workspace-policy",
            "org-a",
            QuotaScope.WORKSPACE,
            "USD",
            workspace_id="ws-a",
            feature_quotas={"chat": 10},
        ),
        QuotaPolicy(
            "user-policy",
            "org-a",
            QuotaScope.USER,
            "USD",
            workspace_id="ws-a",
            user_id="learner-a",
            feature_quotas={"chat": 1},
        ),
    )
    for policy in policies:
        quotas.set_policy(
            policy,
            subject=owner,
            tenant=current_tenant,
            trace_id=f"trace-{policy.policy_id}",
        )
    calls: list[str] = []
    receipt = QuotaEnforcementAdapter(quotas).enforce_provider_call(
        subject=QuotaSubject(tenant=current_tenant, user_id="learner-a"),
        usage=QuotaUsageSnapshot(
            feature_usage={
                QuotaScope.ORGANIZATION: {"chat": 0},
                QuotaScope.WORKSPACE: {"chat": 0},
                QuotaScope.USER: {"chat": 1},
            }
        ),
        run_id="run-quota",
        operation_id="operation-quota",
        selected_model=None,
        feature="chat",
        provider_call=lambda: calls.append("provider-called"),
    )
    assert receipt.allowed is False
    assert receipt.provider_call_invoked is False
    assert calls == []
    assert receipt.evaluated_scopes == (
        QuotaScope.ORGANIZATION,
        QuotaScope.WORKSPACE,
        QuotaScope.USER,
    )
    assert any("user-policy:FEATURE_QUOTA_EXCEEDED:chat" in reason for reason in receipt.evaluation.reasons)
