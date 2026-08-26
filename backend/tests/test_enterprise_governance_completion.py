from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.cost.budget import BudgetDecision
from app.enterprise.contracts_v1 import (
    AccessDecisionKind,
    ActorContext,
    ActorKind,
    AuditMetadataEntry,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.governance.audit import (
    AuditEventAction,
    GovernanceAuditEvent,
    SQLiteAuditLog,
)
from app.governance.authorization import (
    DEFAULT_POLICY,
    ROLE_MATRIX,
    AuthorizationService,
    BreakGlassPolicy,
)
from app.governance.cost_views import CostViewScope, TenantCostView
from app.governance.models import (
    Action,
    AuthorizationResult,
    GovernanceActorAdapter,
    PolicyDecision,
    PolicyRequest,
    RelationshipContext,
    Resource,
    ResourceType,
    Role,
)
from app.governance.provider_governance import (
    ProviderConfig,
    ProviderConfigStatus,
    ProviderCredentialRef,
    ProviderGovernanceError,
    ProviderGovernanceService,
    ProviderScope,
    ProviderSecretReference,
)
from app.governance.quotas import (
    BudgetLimit,
    OrganizationBudget,
    QuotaScope,
    QuotaSubject,
    QuotaUsageSnapshot,
    TenantQuotaService,
    UserBudget,
    WorkspaceBudget,
)
from app.governance.ui_seams import (
    AccessDeniedReadModel,
    OrganizationCostReadModel,
    PermissionAwareNavigation,
    RoleBadge,
)


NOW = datetime(2026, 8, 26, 11, 30, tzinfo=timezone.utc)


def _actor(
    role: Role,
    *,
    subject_id: str = "actor-a",
    organization_id: str | None = "org-a",
    workspace_id: str | None = "ws-a",
    roles: tuple[str, ...] | None = None,
) -> ActorContext:
    return ActorContext(
        subject_id=subject_id,
        actor_kind=ActorKind.HUMAN,
        issuer=(
            "mentorflow://personal-local"
            if organization_id is None
            else "https://issuer.example.test"
        ),
        token_id="token-" + subject_id,
        session_id="session-" + subject_id,
        request_id="request-" + subject_id,
        authentication_time=NOW,
        authentication_strength="mfa" if organization_id is not None else "local-process",
        organization_id=organization_id,
        workspace_id=workspace_id,
        membership_id=None if organization_id is None else "membership-" + subject_id,
        roles=roles or (role.value,),
    )


def _subject(
    role: Role,
    *,
    subject_id: str = "actor-a",
    organization_id: str | None = "org-a",
    workspace_id: str | None = "ws-a",
) -> GovernanceActorAdapter:
    return GovernanceActorAdapter(
        actor=_actor(
            role,
            subject_id=subject_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
        ),
        effective_role=role,
        workspace_memberships=(
            frozenset() if workspace_id is None else frozenset({workspace_id})
        ),
    )


def _tenant(
    organization_id: str | None = "org-a",
    workspace_id: str | None = "ws-a",
) -> TenantContext:
    if organization_id is None:
        return TenantContext(mode=TenantMode.PERSONAL_LOCAL)
    return TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )


def _scope(
    organization_id: str = "org-a",
    workspace_id: str | None = "ws-a",
    *,
    owner_subject_id: str | None = None,
) -> ResourceScope:
    if workspace_id is None:
        return ResourceScope(
            scope_type=ResourceScopeType.ORGANIZATION,
            organization_id=organization_id,
            owner_subject_id=owner_subject_id,
            classification=ResourceClassification.CONFIDENTIAL,
        )
    return ResourceScope(
        scope_type=ResourceScopeType.WORKSPACE,
        organization_id=organization_id,
        workspace_id=workspace_id,
        owner_subject_id=owner_subject_id,
        classification=ResourceClassification.CONFIDENTIAL,
    )


def _request(
    subject: GovernanceActorAdapter,
    *,
    tenant: TenantContext | None = None,
    resource: ResourceType = ResourceType.DOCUMENT,
    resource_id: str = "resource-1",
    action: Action = Action.READ,
    scope: ResourceScope | None = None,
    relationships: RelationshipContext | None = None,
    break_glass=None,
) -> PolicyRequest:
    selected_tenant = tenant or _tenant()
    return PolicyRequest(
        subject=subject,
        tenant=selected_tenant,
        action=action,
        resource=resource,
        resource_id=resource_id,
        resource_scope=scope or _scope(),
        relationships=relationships or RelationshipContext(),
        trace_id="trace-" + subject.actor_id,
        occurred_at=NOW,
        break_glass=break_glass,
    )


def _auth(tmp_path: Path, *, break_glass: bool = False):
    audit = SQLiteAuditLog(tmp_path / "audit.sqlite3")
    return (
        AuthorizationService(
            audit,
            break_glass_policy=BreakGlassPolicy(enabled=break_glass),
        ),
        audit,
    )


def test_m1_m2_vocabulary_default_deny_and_role_matrix_are_frozen() -> None:
    assert Resource is ResourceType
    assert AuthorizationResult is PolicyDecision
    assert DEFAULT_POLICY is AccessDecisionKind.DENY
    assert {resource.name for resource in ResourceType} == {
        "ORGANIZATION",
        "MEMBERSHIP",
        "WORKSPACE",
        "DOCUMENT",
        "KNOWLEDGE_MAP",
        "QUESTION_BANK",
        "PLAN",
        "TEACHER_SESSION",
        "REVIEW_CAMPAIGN",
        "STUDENT_PROGRESS",
        "PROVIDER_CONFIG",
        "COST",
        "AUDIT",
        "EXPORT",
    }
    assert {role.value for role in Role} == {
        "ORGANIZATION_OWNER",
        "ADMIN",
        "CONTENT_MANAGER",
        "INSTRUCTOR",
        "LEARNER",
        "AUDITOR",
    }
    assert set(ROLE_MATRIX[Role.ORGANIZATION_OWNER]) == set(ResourceType)
    with pytest.raises(TypeError):
        ROLE_MATRIX[Role.LEARNER][ResourceType.AUDIT] = frozenset({Action.READ})  # type: ignore[index]


def test_m3_relationship_policy_requires_learner_membership_in_assigned_cohort(
    tmp_path: Path,
) -> None:
    service, audit = _auth(tmp_path)
    instructor = _subject(Role.INSTRUCTOR, subject_id="instructor-a")
    allowed = service.authorize(
        _request(
            instructor,
            resource=ResourceType.STUDENT_PROGRESS,
            resource_id="student-model-learner-a",
            relationships=RelationshipContext(
                learner_id="learner-a",
                cohort_id="cohort-a",
                assigned_cohort_ids=frozenset({"cohort-a"}),
                learner_cohort_ids=frozenset({"cohort-a"}),
                private_student_model=True,
            ),
        )
    )
    denied = service.authorize(
        _request(
            instructor,
            resource=ResourceType.STUDENT_PROGRESS,
            resource_id="student-model-learner-b",
            relationships=RelationshipContext(
                learner_id="learner-b",
                cohort_id="cohort-a",
                assigned_cohort_ids=frozenset({"cohort-a"}),
                learner_cohort_ids=frozenset({"cohort-b"}),
                private_student_model=True,
            ),
        )
    )
    assert allowed.allowed
    assert denied.reason_code == "INSTRUCTOR_ASSIGNMENT_REQUIRED"
    assert audit.records()[-1].envelope.outcome == "DENY"


def test_m3_m11_private_history_role_escalation_and_forged_role_fail_closed(
    tmp_path: Path,
) -> None:
    service, _audit = _auth(tmp_path)
    content_manager = _subject(Role.CONTENT_MANAGER, subject_id="content-a")
    denied = service.authorize(
        _request(
            content_manager,
            resource=ResourceType.STUDENT_PROGRESS,
            resource_id="private-student-model",
            relationships=RelationshipContext(private_student_model=True),
        )
    )
    assert denied.reason_code == "ROLE_MATRIX_DENY"

    learner_actor = _actor(Role.LEARNER, subject_id="learner-a")
    with pytest.raises(ValueError, match="effective_role"):
        GovernanceActorAdapter(actor=learner_actor, effective_role=Role.ADMIN)

    forged = _actor(
        Role.LEARNER,
        subject_id="forged-a",
        roles=("SUPER_ADMIN",),
    )
    with pytest.raises(ValueError, match="unsupported enterprise role"):
        GovernanceActorAdapter(actor=forged, effective_role=Role.ADMIN)


def test_m4_m5_audit_vocabulary_redaction_and_tail_delete_detection(tmp_path: Path) -> None:
    assert {item.value for item in AuditEventAction} == {
        "login",
        "membership.invite",
        "membership.disable",
        "credential.revoke",
        "membership.role_change",
        "document.upload",
        "document.delete",
        "plan.publish",
        "provider_config.change",
        "export.create",
        "access.denied",
    }
    audit = SQLiteAuditLog(tmp_path / "audit.sqlite3")
    actor = _actor(Role.ADMIN)
    record = audit.record(
        GovernanceAuditEvent(
            actor=actor,
            action=AuditEventAction.LOGIN,
            resource_type="session",
            resource_id="session-a",
            outcome=AccessDecisionKind.ALLOW,
            reason_code="LOGIN_SUCCEEDED",
            trace_id="trace-login",
            occurred_at=NOW,
            metadata_redacted=(
                AuditMetadataEntry(key="diagnostic", value="api_key=do-not-store"),
            ),
        )
    )
    assert record.envelope.actor_context.organization_id == "org-a"
    assert record.envelope.actor_context.workspace_id == "ws-a"
    persisted = json.dumps(record.to_public_dict())
    assert "do-not-store" not in persisted
    assert "[REDACTED]" in persisted
    assert audit.verify_integrity().valid

    attacker = sqlite3.connect(str(audit.path))
    try:
        attacker.execute("DROP TRIGGER enterprise_audit_no_delete")
        attacker.execute("DELETE FROM enterprise_audit_log WHERE sequence = 1")
        attacker.commit()
    finally:
        attacker.close()
    report = audit.verify_integrity()
    assert report.valid is False
    assert report.entries_checked == 0
    assert report.expected_entries == 1

    tampered = SQLiteAuditLog(tmp_path / "tampered.sqlite3")
    tampered.record(
        GovernanceAuditEvent(
            actor=actor,
            action=AuditEventAction.INVITE,
            resource_type="membership",
            resource_id="membership-a",
            outcome=AccessDecisionKind.ALLOW,
            reason_code="INVITED",
            trace_id="trace-invite",
            occurred_at=NOW,
        )
    )
    attacker = sqlite3.connect(str(tampered.path))
    try:
        attacker.execute("DROP TRIGGER enterprise_audit_no_update")
        attacker.execute(
            "UPDATE enterprise_audit_log SET envelope_json = ? WHERE sequence = 1",
            ('{"tampered":true}',),
        )
        attacker.commit()
    finally:
        attacker.close()
    assert tampered.verify_integrity().valid is False


def test_m6_m7_credential_projection_rotation_invalid_and_session_fencing(
    tmp_path: Path,
) -> None:
    service, audit = _auth(tmp_path)
    governance = ProviderGovernanceService(service)
    owner = _subject(Role.ORGANIZATION_OWNER, subject_id="owner-a")
    with pytest.raises(ValueError, match="opaque secret reference") as invalid_ref:
        ProviderCredentialRef("sk-plaintext-never-echo")
    assert "sk-plaintext-never-echo" not in str(invalid_ref.value)
    assert ProviderSecretReference is ProviderCredentialRef

    old_ref = ProviderCredentialRef("mfcred_old_reference_123")
    config = ProviderConfig(
        config_id="provider-a",
        organization_id="org-a",
        provider="openai-compatible",
        secret_reference=old_ref,
        approved_models=frozenset({"model-a"}),
    )
    public = governance.register(
        config,
        subject=owner,
        tenant=_tenant(),
        trace_id="trace-register",
    )
    assert public["configured"] is True
    assert public["provider"] == "openai-compatible"
    assert public["model"] == ["model-a"]
    assert set(public) == {"configured", "provider", "model", "status", "rotation_time"}
    assert public["rotation_time"] is None
    assert old_ref.value not in json.dumps(dict(public))

    old_session = governance.authorize_new_session("provider-a", "model-a", now=NOW)
    governance.begin_rotation(
        "provider-a",
        subject=owner,
        tenant=_tenant(),
        trace_id="trace-rotation",
        now=NOW + timedelta(minutes=1),
    )
    with pytest.raises(ProviderGovernanceError, match="PROVIDER_CONFIG_NOT_ACTIVE"):
        governance.authorize_new_session("provider-a", "model-a", now=NOW)
    with pytest.raises(ProviderGovernanceError, match="PROVIDER_CONFIG_NOT_ACTIVE"):
        governance.validate_session(old_session)

    rotated = governance.complete_rotation(
        "provider-a",
        ProviderCredentialRef("mfcred_new_reference_456"),
        subject=owner,
        tenant=_tenant(),
        trace_id="trace-complete",
        now=NOW + timedelta(minutes=2),
    )
    assert rotated["status"] == ProviderConfigStatus.ACTIVE.value
    assert rotated["rotation_time"] == (NOW + timedelta(minutes=2)).isoformat()
    with pytest.raises(ProviderGovernanceError, match="PROVIDER_SESSION_STALE"):
        governance.validate_session(old_session)
    new_session = governance.authorize_new_session("provider-a", "model-a", now=NOW)
    assert governance.validate_session(new_session).revision == 2

    revoked = governance.revoke(
        "provider-a",
        subject=owner,
        tenant=_tenant(),
        trace_id="trace-revoke",
        now=NOW + timedelta(minutes=3),
    )
    assert revoked["configured"] is False
    with pytest.raises(ProviderGovernanceError, match="PROVIDER_CONFIG_NOT_ACTIVE"):
        governance.authorize_new_session("provider-a", "model-a")
    with pytest.raises(ProviderGovernanceError, match="PROVIDER_CONFIG_NOT_ACTIVE"):
        governance.validate_session(new_session)

    invalid_config = ProviderConfig(
        config_id="provider-invalid",
        organization_id="org-a",
        provider="openai-compatible",
        secret_reference=ProviderCredentialRef("mfcred_invalid_reference_123"),
        approved_models=frozenset({"model-a"}),
    )
    governance.register(
        invalid_config,
        subject=owner,
        tenant=_tenant(),
        trace_id="trace-invalid-register",
    )
    invalid = governance.mark_invalid(
        "provider-invalid",
        subject=owner,
        tenant=_tenant(),
        trace_id="trace-invalid",
        now=NOW + timedelta(minutes=4),
    )
    assert invalid["status"] == ProviderConfigStatus.INVALID.value
    with pytest.raises(ProviderGovernanceError, match="PROVIDER_CONFIG_NOT_ACTIVE"):
        governance.authorize_new_session("provider-invalid", "model-a")
    serialized = json.dumps(
        [record.to_public_dict() for record in audit.records()],
        ensure_ascii=False,
    )
    assert old_ref.value not in serialized
    assert "mfcred_new_reference_456" not in serialized


def _limit(maximum: str, warning: str, stop: str) -> BudgetLimit:
    return BudgetLimit(
        maximum=Decimal(maximum),
        warning_threshold=Decimal(warning),
        hard_stop_threshold=Decimal(stop),
    )


def test_m8_named_hierarchical_budgets_adapt_existing_budget_gate(tmp_path: Path) -> None:
    authorization, _audit = _auth(tmp_path)
    quotas = TenantQuotaService(authorization)
    owner = _subject(Role.ORGANIZATION_OWNER, subject_id="owner-a")
    tenant = _tenant()
    organization = OrganizationBudget(
        policy_id="org-budget",
        organization_id="org-a",
        currency="USD",
        daily=_limit("10", "8", "9"),
        model_allowlist=frozenset({"model-a"}),
    )
    workspace = WorkspaceBudget(
        policy_id="workspace-budget",
        organization_id="org-a",
        workspace_id="ws-a",
        currency="USD",
        monthly=_limit("100", "80", "90"),
    )
    user = UserBudget(
        policy_id="user-budget",
        organization_id="org-a",
        workspace_id="ws-a",
        user_id="learner-a",
        currency="USD",
        daily=_limit("100", "80", "90"),
        feature_quotas={"review": 2},
    )
    assert organization.to_policy().scope is QuotaScope.ORGANIZATION
    assert workspace.to_policy().scope is QuotaScope.WORKSPACE
    assert user.to_policy().scope is QuotaScope.USER
    for budget in (organization, workspace, user):
        quotas.set_budget(
            budget,
            subject=owner,
            tenant=tenant,
            trace_id="trace-" + budget.policy_id,
        )
    result = quotas.evaluate(
        subject=QuotaSubject(tenant=tenant, user_id="learner-a"),
        usage=QuotaUsageSnapshot(
            daily_cost={
                QuotaScope.ORGANIZATION: Decimal("9"),
                QuotaScope.USER: Decimal("0"),
            },
            monthly_cost={QuotaScope.WORKSPACE: Decimal("0")},
        ),
        run_id="run-budget",
        operation_id="operation-budget",
        selected_model="model-a",
        feature="review",
        estimate=None,
        provider_call_required=False,
    )
    assert result.decision is BudgetDecision.STOP
    assert "org-budget" in result.evaluated_policy_ids
    assert any("HARD_STOP_THRESHOLD_REACHED" in reason for reason in result.reasons)


def test_m10_break_glass_is_fenced_to_exact_resource_id_and_scope(tmp_path: Path) -> None:
    service, audit = _auth(tmp_path, break_glass=True)
    admin = _subject(Role.ADMIN, subject_id="admin-a")
    scope = _scope()
    grant = service.issue_break_glass(
        subject=admin,
        tenant=_tenant(),
        reason="incident response for student one",
        approved_by="owner-approver",
        actions=frozenset({Action.READ}),
        resources=frozenset({ResourceType.STUDENT_PROGRESS}),
        resource_ids=frozenset({"student-model-1"}),
        resource_scope=scope,
        trace_id="trace-break-glass",
        duration=timedelta(minutes=5),
        now=NOW,
    )
    allowed = service.authorize(
        _request(
            admin,
            resource=ResourceType.STUDENT_PROGRESS,
            resource_id="student-model-1",
            scope=scope,
            relationships=RelationshipContext(private_student_model=True),
            break_glass=grant,
        )
    )
    denied = service.authorize(
        _request(
            admin,
            resource=ResourceType.STUDENT_PROGRESS,
            resource_id="student-model-2",
            scope=scope,
            relationships=RelationshipContext(private_student_model=True),
            break_glass=grant,
        )
    )
    assert allowed.allowed and allowed.break_glass_used
    assert denied.reason_code == "ADMIN_PRIVATE_STUDENT_MODEL_NOT_GRANTED"
    assert audit.records()[-1].envelope.outcome == "DENY"


def test_m11_m12_idor_denial_and_application_read_models(tmp_path: Path) -> None:
    service, _audit = _auth(tmp_path)
    learner = _subject(Role.LEARNER, subject_id="learner-a")
    guessed = _request(
        learner,
        resource=ResourceType.STUDENT_PROGRESS,
        resource_id="student-model-other-user",
        relationships=RelationshipContext(
            learner_id="other-user",
            resource_owner_id="other-user",
            private_student_model=True,
        ),
    )
    decision = service.authorize(guessed)
    assert decision.reason_code == "LEARNER_OWN_RESOURCE_ONLY"
    denied = AccessDeniedReadModel.from_denial(guessed, decision)
    assert denied.reason_code == "LEARNER_OWN_RESOURCE_ONLY"
    assert "other-user" not in json.dumps(denied.to_public_dict())

    learner_nav = PermissionAwareNavigation.for_subject(learner)
    assert learner_nav.permits_candidate(ResourceType.TEACHER_SESSION, Action.EXECUTE)
    assert not learner_nav.permits_candidate(ResourceType.AUDIT, Action.READ)
    owner = _subject(Role.ORGANIZATION_OWNER, subject_id="owner-a")
    owner_nav = PermissionAwareNavigation.for_subject(owner)
    assert owner_nav.permits_candidate(ResourceType.AUDIT, Action.READ)
    assert RoleBadge.for_subject(owner).code == "ORGANIZATION_OWNER"

    organization_view = TenantCostView(
        scope=CostViewScope.ORGANIZATION,
        organization_id="org-a",
        workspace_id=None,
        user_id=None,
        cohort_id=None,
        currency="USD",
        known_cost_subtotal=Decimal("4.00"),
        total_cost=Decimal("4.00"),
        record_count=2,
        unknown_cost_count=0,
    )
    cost_model = OrganizationCostReadModel.from_view(organization_view)
    assert cost_model.organization_id == "org-a"
    assert cost_model.total_cost == Decimal("4.00")
    assert cost_model.read_only is True


def test_m13_enterprise_governance_docs_cover_required_policies() -> None:
    docs = Path(__file__).resolve().parents[2] / "docs" / "enterprise"
    expected = {
        "authorization-matrix.md": ("Default policy", "ORGANIZATION_OWNER", "Break glass"),
        "audit-policy.md": ("Required event vocabulary", "Redaction", "not** an immutable legal ledger"),
        "provider-secret-governance.md": ("ProviderCredentialRef", "INVALID", "session"),
        "quota-policy.md": ("OrganizationBudget", "ExecutionBudgetGate", "Cost views"),
    }
    for filename, markers in expected.items():
        text = (docs / filename).read_text(encoding="utf-8")
        assert all(marker in text for marker in markers)
