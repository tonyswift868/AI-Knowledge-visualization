from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.governance.admin_api import (
    AdminApiContext,
    StaticAdminContextResolver,
    create_enterprise_admin_app,
    create_enterprise_admin_services,
)
from app.governance.audit import SQLiteAuditLog
from app.governance.authorization import AuthorizationService, BreakGlassPolicy
from app.governance.control_plane import QuotaEnforcementAdapter
from app.governance.models import (
    Action,
    GovernanceActorAdapter,
    PolicyRequest,
    RelationshipContext,
    ResourceType,
    Role,
)
from app.governance.quotas import (
    QuotaPolicy,
    QuotaScope,
    QuotaSubject,
    QuotaUsageSnapshot,
    TenantQuotaService,
)


NOW = datetime(2026, 8, 26, 17, 0, tzinfo=timezone.utc)


def subject(
    role: Role,
    *,
    subject_id: str,
    organization_id: str = "org-a",
    workspace_id: str | None = "ws-a",
    active: bool = True,
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
        active=active,
    )


def tenant(workspace_id: str | None = "ws-a") -> TenantContext:
    return TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id="org-a",
        workspace_id=workspace_id,
    )


def scope(workspace_id: str = "ws-a") -> ResourceScope:
    return ResourceScope(
        scope_type=ResourceScopeType.WORKSPACE,
        organization_id="org-a",
        workspace_id=workspace_id,
        classification=ResourceClassification.RESTRICTED,
    )


def authorization(tmp_path: Path) -> tuple[AuthorizationService, SQLiteAuditLog]:
    audit = SQLiteAuditLog(tmp_path / "negative-e2e.sqlite3")
    return AuthorizationService(audit), audit


def test_learner_admin_api_denied_with_zero_mutation(tmp_path: Path) -> None:
    services = create_enterprise_admin_services(str(tmp_path / "learner-admin.sqlite3"))
    learner = subject(Role.LEARNER, subject_id="learner-a", workspace_id=None)
    app = create_enterprise_admin_app(
        services,
        StaticAdminContextResolver(AdminApiContext(learner, tenant(workspace_id=None))),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/enterprise-admin/members/invitations",
            json={
                "member_id": "member-denied",
                "subject_id": "subject-denied",
                "email": "denied@example.test",
            },
        )
    assert response.status_code == 403
    assert services.organizations.snapshot() == ()
    assert services.audit_log.records()[-1].envelope.outcome == "DENY"


def test_instructor_foreign_learner_denied(tmp_path: Path) -> None:
    service, audit = authorization(tmp_path)
    decision = service.authorize(
        PolicyRequest(
            subject=subject(Role.INSTRUCTOR, subject_id="instructor-a"),
            tenant=tenant(),
            action=Action.READ,
            resource=ResourceType.STUDENT_PROGRESS,
            resource_id="progress-foreign",
            resource_scope=scope(),
            relationships=RelationshipContext(
                learner_id="learner-foreign",
                assigned_learner_ids=frozenset({"learner-assigned"}),
            ),
            trace_id="negative-instructor-foreign-learner",
            occurred_at=NOW,
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "INSTRUCTOR_ASSIGNMENT_REQUIRED"
    assert audit.records()[-1].envelope.outcome == "DENY"


def test_content_manager_private_student_state_denied(tmp_path: Path) -> None:
    service, _audit = authorization(tmp_path)
    decision = service.authorize(
        PolicyRequest(
            subject=subject(Role.CONTENT_MANAGER, subject_id="content-a"),
            tenant=tenant(),
            action=Action.READ,
            resource=ResourceType.STUDENT_PROGRESS,
            resource_id="student-private-state",
            resource_scope=scope(),
            relationships=RelationshipContext(private_student_model=True),
            trace_id="negative-content-private-student",
            occurred_at=NOW,
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "ROLE_MATRIX_DENY"


def test_auditor_mutation_denied(tmp_path: Path) -> None:
    service, _audit = authorization(tmp_path)
    decision = service.authorize(
        PolicyRequest(
            subject=subject(Role.AUDITOR, subject_id="auditor-a"),
            tenant=tenant(),
            action=Action.UPDATE,
            resource=ResourceType.WORKSPACE,
            resource_id="ws-a",
            resource_scope=scope(),
            trace_id="negative-auditor-mutation",
            occurred_at=NOW,
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "ROLE_MATRIX_DENY"


def test_admin_plaintext_provider_secret_denied_without_config_write(tmp_path: Path) -> None:
    services = create_enterprise_admin_services(str(tmp_path / "plaintext-secret.sqlite3"))
    owner = subject(Role.ADMIN, subject_id="admin-a", workspace_id=None)
    app = create_enterprise_admin_app(
        services,
        StaticAdminContextResolver(AdminApiContext(owner, tenant(workspace_id=None))),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/enterprise-admin/providers",
            json={
                "config_id": "provider-raw-denied",
                "provider": "openai-compatible",
                "credential_reference": "sk-plaintext-must-never-persist",
                "approved_models": ["model-a"],
                "scope": "ORGANIZATION",
            },
        )
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}
    assert services.providers.config_ids() == ()
    assert "sk-plaintext" not in str(response.json())


def test_foreign_workspace_idor_denied_and_audited(tmp_path: Path) -> None:
    service, audit = authorization(tmp_path)
    decision = service.authorize(
        PolicyRequest(
            subject=subject(Role.OWNER, subject_id="owner-a"),
            tenant=tenant("ws-a"),
            action=Action.READ,
            resource=ResourceType.DOCUMENT,
            resource_id="document-foreign-workspace",
            resource_scope=scope("ws-b"),
            trace_id="negative-foreign-workspace-idor",
            occurred_at=NOW,
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "RESOURCE_WORKSPACE_MISMATCH"
    assert audit.records()[-1].envelope.outcome == "DENY"


def test_revoked_membership_actor_denied(tmp_path: Path) -> None:
    service, _audit = authorization(tmp_path)
    decision = service.authorize(
        PolicyRequest(
            subject=subject(Role.ADMIN, subject_id="revoked-admin", active=False),
            tenant=tenant(),
            action=Action.MANAGE,
            resource=ResourceType.MEMBERSHIP,
            resource_id="member-target",
            resource_scope=scope(),
            trace_id="negative-revoked-membership",
            occurred_at=NOW,
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "ACTOR_INACTIVE"


def test_expired_break_glass_denied(tmp_path: Path) -> None:
    audit = SQLiteAuditLog(tmp_path / "expired-break-glass.sqlite3")
    service = AuthorizationService(
        audit,
        break_glass_policy=BreakGlassPolicy(
            enabled=True,
            maximum_duration=timedelta(minutes=15),
        ),
    )
    admin = subject(Role.ADMIN, subject_id="admin-a")
    grant = service.issue_break_glass(
        subject=admin,
        tenant=tenant(),
        reason="Emergency repair window",
        approved_by="owner-approver",
        actions=frozenset({Action.DELETE}),
        resources=frozenset({ResourceType.STUDENT_PROGRESS}),
        resource_ids=frozenset({"student-private-state"}),
        resource_scope=scope(),
        trace_id="negative-expired-break-glass-issue",
        duration=timedelta(minutes=1),
        now=NOW,
    )
    decision = service.authorize(
        PolicyRequest(
            subject=admin,
            tenant=tenant(),
            action=Action.DELETE,
            resource=ResourceType.STUDENT_PROGRESS,
            resource_id="student-private-state",
            resource_scope=scope(),
            relationships=RelationshipContext(private_student_model=True),
            trace_id="negative-expired-break-glass-use",
            occurred_at=NOW + timedelta(minutes=2),
            break_glass=grant,
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "ROLE_MATRIX_DENY"


def test_quota_bypass_denied_before_provider_call(tmp_path: Path) -> None:
    service, _audit = authorization(tmp_path)
    quotas = TenantQuotaService(service)
    owner = subject(Role.OWNER, subject_id="owner-a")
    current_tenant = tenant()
    quotas.set_policy(
        QuotaPolicy(
            policy_id="org-permissive",
            organization_id="org-a",
            scope=QuotaScope.ORGANIZATION,
            currency="USD",
            feature_quotas={"chat": 100},
        ),
        subject=owner,
        tenant=current_tenant,
        trace_id="negative-quota-org",
    )
    quotas.set_policy(
        QuotaPolicy(
            policy_id="user-strict",
            organization_id="org-a",
            workspace_id="ws-a",
            user_id="learner-a",
            scope=QuotaScope.USER,
            currency="USD",
            feature_quotas={"chat": 5},
        ),
        subject=owner,
        tenant=current_tenant,
        trace_id="negative-quota-user",
    )
    calls: list[str] = []
    receipt = QuotaEnforcementAdapter(quotas).enforce_provider_call(
        subject=QuotaSubject(tenant=current_tenant, user_id="learner-a"),
        usage=QuotaUsageSnapshot(
            feature_usage={QuotaScope.USER: {"chat": 5}}
        ),
        run_id="negative-quota-run",
        operation_id="negative-quota-operation",
        selected_model=None,
        feature="chat",
        provider_call=lambda: calls.append("bypass"),
    )
    assert receipt.allowed is False
    assert receipt.provider_call_invoked is False
    assert calls == []
