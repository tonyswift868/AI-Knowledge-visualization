from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.cost.budget import BudgetDecision
from app.enterprise.contracts_v1 import (
    AccessDecision,
    AccessDecisionKind,
    ActorContext,
    ActorKind,
    AuditEnvelope,
    AuditMetadataEntry,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
    verify_frozen_contract_hashes,
)
from app.governance.audit import SQLiteAuditLog
from app.governance.authorization import (
    AuthorizationAuditError,
    AuthorizationService,
    BreakGlassDenied,
    BreakGlassPolicy,
    ROLE_MATRIX,
)
from app.governance.cost_views import (
    CostViewDenied,
    CostViewRequest,
    CostViewScope,
    TenantCostEntry,
    TenantCostStore,
    TenantCostViewService,
)
from app.governance.models import (
    Action,
    GovernanceActorAdapter,
    PolicyRequest,
    RelationshipContext,
    Resource,
    Role,
)
from app.governance.provider_governance import (
    ProviderConfig,
    ProviderConfigStatus,
    ProviderGovernanceError,
    ProviderGovernanceService,
    ProviderScope,
    ProviderSecretReference,
)
from app.governance.quotas import (
    BudgetLimit,
    QuotaPolicy,
    QuotaPolicyError,
    QuotaScope,
    QuotaSubject,
    QuotaUsageSnapshot,
    TenantQuotaService,
)


NOW = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)


def _actor(
    role: Role,
    *,
    subject_id: str | None = None,
    organization_id: str | None = "org-a",
    workspace_id: str | None = "ws-a",
    request_id: str | None = None,
) -> ActorContext:
    subject = subject_id or role.value.lower()
    return ActorContext(
        subject_id=subject,
        actor_kind=ActorKind.HUMAN,
        issuer=(
            "mentorflow://personal-local"
            if organization_id is None
            else "https://issuer.example.test"
        ),
        token_id=f"token-{subject}",
        session_id=f"session-{subject}",
        request_id=request_id or f"request-{subject}",
        authentication_time=NOW,
        authentication_strength="local-process" if organization_id is None else "mfa",
        organization_id=organization_id,
        workspace_id=workspace_id,
        membership_id=None if organization_id is None else f"membership-{subject}",
        roles=(role.value,),
    )


def _subject(
    role: Role,
    *,
    subject_id: str | None = None,
    organization_id: str | None = "org-a",
    workspace_id: str | None = "ws-a",
    memberships: frozenset[str] | None = None,
    active: bool = True,
) -> GovernanceActorAdapter:
    actor = _actor(
        role,
        subject_id=subject_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    return GovernanceActorAdapter(
        actor=actor,
        effective_role=role,
        workspace_memberships=(
            memberships
            if memberships is not None
            else (frozenset() if workspace_id is None else frozenset({workspace_id}))
        ),
        active=active,
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
    organization_id: str | None = "org-a",
    workspace_id: str | None = "ws-a",
    *,
    owner_subject_id: str | None = None,
) -> ResourceScope:
    if organization_id is None:
        return ResourceScope(
            scope_type=ResourceScopeType.PERSONAL,
            owner_subject_id=owner_subject_id,
            classification=ResourceClassification.CONFIDENTIAL,
        )
    if workspace_id is None:
        return ResourceScope(
            scope_type=ResourceScopeType.ORGANIZATION,
            owner_subject_id=owner_subject_id,
            organization_id=organization_id,
            classification=ResourceClassification.CONFIDENTIAL,
        )
    return ResourceScope(
        scope_type=ResourceScopeType.WORKSPACE,
        owner_subject_id=owner_subject_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        classification=ResourceClassification.CONFIDENTIAL,
    )


def _request(
    subject: GovernanceActorAdapter,
    tenant: TenantContext,
    *,
    action: Action = Action.READ,
    resource: Resource = Resource.DOCUMENTS,
    scope: ResourceScope | None = None,
    relationships: RelationshipContext | None = None,
    occurred_at: datetime = NOW,
    break_glass=None,
) -> PolicyRequest:
    return PolicyRequest(
        subject=subject,
        tenant=tenant,
        action=action,
        resource=resource,
        resource_id=f"{resource.value}-1",
        resource_scope=scope or _scope(tenant.organization_id, tenant.workspace_id),
        relationships=relationships or RelationshipContext(),
        trace_id=f"trace-{subject.actor_id}",
        occurred_at=occurred_at,
        break_glass=break_glass,
    )


def _auth(tmp_path: Path, *, break_glass: bool = False):
    audit = SQLiteAuditLog(tmp_path / "audit.sqlite3")
    service = AuthorizationService(
        audit,
        break_glass_policy=BreakGlassPolicy(enabled=break_glass),
    )
    return service, audit


def test_governance_imports_the_frozen_contract_without_parallel_shared_models() -> None:
    import app.governance.models as governance_models

    source = inspect.getsource(governance_models)
    for symbol in (
        "ActorContext",
        "TenantContext",
        "AccessDecision",
        "ResourceScope",
        "AuditEnvelope",
    ):
        assert f"class {symbol}" not in source
    assert governance_models.AccessDecision is AccessDecision
    assert governance_models.ActorContext is ActorContext
    assert governance_models.TenantContext is TenantContext
    assert verify_frozen_contract_hashes() is True


def test_frozen_role_matrix_covers_every_governed_resource_and_cannot_mutate() -> None:
    assert set(ROLE_MATRIX[Role.OWNER]) == set(Resource)
    with pytest.raises(TypeError):
        ROLE_MATRIX[Role.OWNER][Resource.COST] = frozenset()  # type: ignore[index]
    with pytest.raises(AttributeError):
        ROLE_MATRIX[Role.OWNER][Resource.COST].add(Action.DELETE)  # type: ignore[attr-defined]


def test_cross_tenant_deny_is_a_canonical_decision_and_is_audited(tmp_path: Path) -> None:
    service, audit = _auth(tmp_path)
    subject = _subject(Role.ADMIN, organization_id="org-a", workspace_id="ws-a")
    tenant = _tenant("org-b", "ws-b")
    result = service.authorize(
        _request(subject, tenant, scope=_scope("org-b", "ws-b"))
    )

    assert isinstance(result.decision, AccessDecision)
    assert result.decision.decision is AccessDecisionKind.DENY
    assert result.reason_code == "CROSS_TENANT_DENIED"
    record = audit.records()[0]
    assert record.envelope.outcome == "DENY"
    assert record.envelope.reason_code == "CROSS_TENANT_DENIED"
    assert record.envelope.actor_context.organization_id == "org-a"
    assert audit.verify_integrity().valid is True


def test_cross_workspace_object_guess_is_denied_and_audited(tmp_path: Path) -> None:
    service, audit = _auth(tmp_path)
    subject = _subject(Role.ADMIN, memberships=frozenset({"ws-a"}))
    result = service.authorize(
        _request(subject, _tenant(), scope=_scope("org-a", "ws-other"))
    )
    assert not result.allowed
    assert result.reason_code == "RESOURCE_WORKSPACE_MISMATCH"
    assert audit.records()[-1].envelope.outcome == "DENY"


def test_instructor_requires_assigned_learner_or_cohort(tmp_path: Path) -> None:
    service, audit = _auth(tmp_path)
    subject = _subject(Role.INSTRUCTOR)
    tenant = _tenant()
    denied = service.authorize(
        _request(
            subject,
            tenant,
            resource=Resource.STUDENT_PROGRESS,
            relationships=RelationshipContext(learner_id="learner-x"),
        )
    )
    allowed = service.authorize(
        _request(
            subject,
            tenant,
            resource=Resource.STUDENT_PROGRESS,
            relationships=RelationshipContext(
                learner_id="learner-x",
                assigned_learner_ids=frozenset({"learner-x"}),
            ),
        )
    )
    assert denied.reason_code == "INSTRUCTOR_ASSIGNMENT_REQUIRED"
    assert allowed.allowed
    assert [record.envelope.outcome for record in audit.records()] == ["DENY", "ALLOW"]


def test_role_escalation_and_private_student_model_are_denied(tmp_path: Path) -> None:
    service, _audit = _auth(tmp_path)
    content = _subject(Role.CONTENT_MANAGER)
    content_result = service.authorize(
        _request(content, _tenant(), resource=Resource.STUDENT_PROGRESS)
    )
    admin = _subject(Role.ADMIN)
    denied = service.authorize(
        _request(
            admin,
            _tenant(),
            resource=Resource.STUDENT_PROGRESS,
            relationships=RelationshipContext(private_student_model=True),
        )
    )
    granted = service.authorize(
        _request(
            admin,
            _tenant(),
            resource=Resource.STUDENT_PROGRESS,
            relationships=RelationshipContext(
                private_student_model=True,
                explicit_permissions=frozenset({"student_model:read_private"}),
            ),
        )
    )
    assert content_result.reason_code == "ROLE_MATRIX_DENY"
    assert denied.reason_code == "ADMIN_PRIVATE_STUDENT_MODEL_NOT_GRANTED"
    assert granted.allowed


def test_auditor_is_read_only_and_metadata_only(tmp_path: Path) -> None:
    service, _audit = _auth(tmp_path)
    auditor = _subject(Role.AUDITOR)
    mutation = service.authorize(
        _request(auditor, _tenant(), action=Action.UPDATE, resource=Resource.AUDIT)
    )
    full_member = service.authorize(
        _request(auditor, _tenant(), resource=Resource.MEMBERS)
    )
    metadata = service.authorize(
        _request(
            auditor,
            _tenant(),
            action=Action.READ_METADATA,
            resource=Resource.MEMBERS,
        )
    )
    assert mutation.reason_code == "ROLE_MATRIX_DENY"
    assert full_member.reason_code == "ROLE_MATRIX_DENY"
    assert metadata.allowed


def test_personal_local_requires_no_login_or_organization_and_remains_usable(
    tmp_path: Path,
) -> None:
    service, audit = _auth(tmp_path)
    actor = _actor(
        Role.OWNER,
        subject_id="local-user",
        organization_id=None,
        workspace_id=None,
    )
    subject = GovernanceActorAdapter.personal_local(actor)
    tenant = _tenant(None, None)
    result = service.authorize(
        _request(
            subject,
            tenant,
            scope=_scope(None, None, owner_subject_id="local-user"),
        )
    )
    assert actor.issuer == "mentorflow://personal-local"
    assert actor.organization_id is None
    assert result.allowed
    assert audit.records()[0].envelope.metadata_redacted[-1].value == "PERSONAL_LOCAL"


def test_break_glass_is_disabled_by_default_and_short_lived_when_enabled(
    tmp_path: Path,
) -> None:
    subject = _subject(Role.ADMIN)
    tenant = _tenant()
    disabled, disabled_audit = _auth(tmp_path / "disabled")
    with pytest.raises(BreakGlassDenied, match="BREAK_GLASS_DISABLED"):
        disabled.issue_break_glass(
            subject=subject,
            tenant=tenant,
            reason="incident response access",
            approved_by="owner-approver",
            actions=frozenset({Action.READ}),
            resources=frozenset({Resource.STUDENT_PROGRESS}),
            trace_id="trace-disabled",
            now=NOW,
        )
    assert disabled_audit.records() == ()

    service, audit = _auth(tmp_path / "enabled", break_glass=True)
    grant = service.issue_break_glass(
        subject=subject,
        tenant=tenant,
        reason="incident api_key=must-not-persist",
        approved_by="owner-approver",
        actions=frozenset({Action.READ}),
        resources=frozenset({Resource.STUDENT_PROGRESS}),
        trace_id="trace-enabled",
        duration=timedelta(minutes=10),
        now=NOW,
    )
    active = service.authorize(
        _request(
            subject,
            tenant,
            resource=Resource.STUDENT_PROGRESS,
            relationships=RelationshipContext(private_student_model=True),
            break_glass=grant,
        )
    )
    expired = service.authorize(
        _request(
            subject,
            tenant,
            resource=Resource.STUDENT_PROGRESS,
            relationships=RelationshipContext(private_student_model=True),
            break_glass=grant,
            occurred_at=grant.expires_at,
        )
    )
    assert active.allowed and active.break_glass_used
    assert expired.reason_code == "ADMIN_PRIVATE_STUDENT_MODEL_NOT_GRANTED"
    persisted = json.dumps(
        [record.to_public_dict() for record in audit.records()],
        ensure_ascii=False,
    )
    assert "must-not-persist" not in persisted
    assert "[REDACTED]" in persisted


def test_audit_failure_prevents_an_access_decision_from_being_used() -> None:
    class BrokenAudit:
        def append(self, _envelope):
            raise OSError("disk unavailable")

    service = AuthorizationService(BrokenAudit())  # type: ignore[arg-type]
    with pytest.raises(AuthorizationAuditError, match="AUTHORIZATION_AUDIT_APPEND_FAILED"):
        service.authorize(_request(_subject(Role.OWNER), _tenant()))


def test_audit_log_is_append_only_and_hash_chain_verifiable(tmp_path: Path) -> None:
    service, audit = _auth(tmp_path)
    service.authorize(_request(_subject(Role.OWNER), _tenant()))
    assert audit.verify_integrity().valid is True
    other = sqlite3.connect(str(audit.path))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            other.execute(
                "UPDATE enterprise_audit_log SET previous_hash = ? WHERE sequence = 1",
                ("f" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            other.execute("DELETE FROM enterprise_audit_log WHERE sequence = 1")
    finally:
        other.close()


def test_canonical_audit_envelope_redacts_sensitive_values(tmp_path: Path) -> None:
    audit = SQLiteAuditLog(tmp_path / "redaction.sqlite3")
    actor = _actor(Role.AUDITOR)
    audit.append(
        AuditEnvelope(
            event_id="audit-redaction",
            actor_context=actor,
            action="audit.read",
            resource_type="audit",
            resource_id="audit-1",
            outcome="ALLOW",
            reason_code="AUTHORIZED",
            occurred_at=NOW,
            request_id=actor.request_id,
            trace_id="trace-redaction",
            metadata_redacted=(
                AuditMetadataEntry(key="diagnostic", value="api_key=super-secret"),
            ),
        )
    )
    persisted = json.dumps(audit.records()[0].to_public_dict(), ensure_ascii=False)
    assert "super-secret" not in persisted
    assert "[REDACTED]" in persisted


def test_provider_secret_is_reference_only_and_absent_from_api_cache_and_audit(
    tmp_path: Path,
) -> None:
    service, audit = _auth(tmp_path)
    owner = _subject(Role.OWNER, subject_id="owner-a")
    governance = ProviderGovernanceService(service)
    with pytest.raises(ValueError, match="opaque secret reference"):
        ProviderSecretReference("sk-raw-provider-secret")
    reference = ProviderSecretReference("mfcred_opaque_reference_123")
    config = ProviderConfig(
        config_id="provider-org-a",
        organization_id="org-a",
        provider="openai-compatible",
        secret_reference=reference,
        approved_models=frozenset({"model-approved"}),
    )
    public = governance.register(
        config,
        subject=owner,
        tenant=_tenant(),
        trace_id="trace-provider-register",
    )
    identity = config.cache_identity("model-approved")
    persisted = json.dumps(
        {
            "public": dict(public),
            "identity": identity,
            "audit": [record.to_public_dict() for record in audit.records()],
        },
        ensure_ascii=False,
    )
    assert reference.value not in persisted
    assert "secret_reference" not in public
    assert governance.assert_model_approved(config.config_id, "model-approved") is config
    with pytest.raises(ProviderGovernanceError, match="MODEL_NOT_APPROVED"):
        governance.assert_model_approved(config.config_id, "model-unapproved")


def test_personal_provider_is_owner_only_and_cross_tenant_denial_is_audited(
    tmp_path: Path,
) -> None:
    service, audit = _auth(tmp_path)
    governance = ProviderGovernanceService(service)
    owner = _subject(Role.OWNER, subject_id="owner-a")
    config = ProviderConfig(
        config_id="personal-a",
        organization_id="org-a",
        provider="openai-compatible",
        secret_reference=ProviderSecretReference("mfcred_personal_reference_123"),
        approved_models=frozenset({"model-a"}),
        scope=ProviderScope.PERSONAL,
        owner_actor_id="owner-a",
    )
    governance.register(
        config,
        subject=owner,
        tenant=_tenant(),
        trace_id="trace-personal-register",
    )
    other_admin = _subject(Role.ADMIN, subject_id="admin-a")
    with pytest.raises(ProviderGovernanceError, match="PERSONAL_PROVIDER_OWNER_REQUIRED"):
        governance.api_view(
            config.config_id,
            subject=other_admin,
            tenant=_tenant(),
            trace_id="trace-personal-guess",
        )
    cross_tenant = _subject(
        Role.OWNER,
        subject_id="owner-b",
        organization_id="org-b",
        workspace_id="ws-b",
    )
    with pytest.raises(ProviderGovernanceError, match="RESOURCE_ORGANIZATION_MISMATCH"):
        governance.api_view(
            config.config_id,
            subject=cross_tenant,
            tenant=_tenant("org-b", "ws-b"),
            trace_id="trace-cross-tenant-provider",
        )
    assert [record.envelope.outcome for record in audit.records()][-2:] == ["DENY", "DENY"]


def test_personal_local_provider_rotation_and_revoke_preserve_reference_secrecy(
    tmp_path: Path,
) -> None:
    service, _audit = _auth(tmp_path)
    actor = _actor(
        Role.OWNER,
        subject_id="local-user",
        organization_id=None,
        workspace_id=None,
    )
    subject = GovernanceActorAdapter.personal_local(actor)
    tenant = _tenant(None, None)
    governance = ProviderGovernanceService(service)
    config = ProviderConfig(
        config_id="personal-local-provider",
        organization_id=None,
        provider="openai-compatible",
        secret_reference=ProviderSecretReference("mfcred_local_reference_123"),
        approved_models=frozenset({"local-model"}),
        scope=ProviderScope.PERSONAL,
        owner_actor_id="local-user",
    )
    governance.register(
        config,
        subject=subject,
        tenant=tenant,
        trace_id="trace-local-register",
    )
    rotating = governance.begin_rotation(
        config.config_id,
        subject=subject,
        tenant=tenant,
        trace_id="trace-local-rotate",
    )
    assert rotating["status"] == ProviderConfigStatus.ROTATING.value
    active = governance.complete_rotation(
        config.config_id,
        ProviderSecretReference("mfcred_local_reference_456"),
        subject=subject,
        tenant=tenant,
        trace_id="trace-local-complete",
    )
    assert active["status"] == ProviderConfigStatus.ACTIVE.value
    revoked = governance.revoke(
        config.config_id,
        subject=subject,
        tenant=tenant,
        trace_id="trace-local-revoke",
    )
    assert revoked["status"] == ProviderConfigStatus.REVOKED.value


def _limit(maximum: str, warning: str, hard_stop: str) -> BudgetLimit:
    return BudgetLimit(
        maximum=Decimal(maximum),
        warning_threshold=Decimal(warning),
        hard_stop_threshold=Decimal(hard_stop),
    )


def test_org_hard_stop_cannot_be_bypassed_by_a_more_permissive_user_policy(
    tmp_path: Path,
) -> None:
    auth, _audit = _auth(tmp_path)
    quotas = TenantQuotaService(auth)
    admin = _subject(Role.ADMIN)
    tenant = _tenant()
    quotas.set_policy(
        QuotaPolicy(
            policy_id="org-policy",
            organization_id="org-a",
            scope=QuotaScope.ORGANIZATION,
            currency="USD",
            daily_budget=_limit("10", "8", "9"),
        ),
        subject=admin,
        tenant=tenant,
        trace_id="trace-org-policy",
    )
    quotas.set_policy(
        QuotaPolicy(
            policy_id="user-policy",
            organization_id="org-a",
            workspace_id="ws-a",
            user_id="learner-a",
            scope=QuotaScope.USER,
            currency="USD",
            daily_budget=_limit("100", "80", "90"),
        ),
        subject=admin,
        tenant=tenant,
        trace_id="trace-user-policy",
    )
    result = quotas.evaluate(
        subject=QuotaSubject(tenant=tenant, user_id="learner-a"),
        usage=QuotaUsageSnapshot(
            daily_cost={
                QuotaScope.ORGANIZATION: Decimal("9"),
                QuotaScope.USER: Decimal("0"),
            }
        ),
        run_id="run-1",
        operation_id="op-1",
        selected_model=None,
        feature=None,
        estimate=None,
        provider_call_required=False,
    )
    assert result.decision is BudgetDecision.STOP
    assert any("org-policy:DAILY:HARD_STOP_THRESHOLD_REACHED" in value for value in result.reasons)


def test_model_allowlist_feature_quota_and_unknown_spend_fail_closed(tmp_path: Path) -> None:
    auth, _audit = _auth(tmp_path)
    quotas = TenantQuotaService(auth)
    owner = _subject(Role.OWNER)
    tenant = _tenant()
    quotas.set_policy(
        QuotaPolicy(
            policy_id="governance-policy",
            organization_id="org-a",
            scope=QuotaScope.ORGANIZATION,
            currency="USD",
            daily_budget=_limit("10", "8", "9"),
            model_allowlist=frozenset({"approved-model"}),
            feature_quotas={"semantic-review": 2},
        ),
        subject=owner,
        tenant=tenant,
        trace_id="trace-governance-policy",
    )
    result = quotas.evaluate(
        subject=QuotaSubject(tenant=tenant, user_id="learner-a"),
        usage=QuotaUsageSnapshot(
            daily_cost={QuotaScope.ORGANIZATION: None},
            feature_usage={QuotaScope.ORGANIZATION: {"semantic-review": 2}},
        ),
        run_id="run-2",
        operation_id="op-2",
        selected_model="unapproved-model",
        feature="semantic-review",
        estimate=None,
        provider_call_required=True,
    )
    assert result.decision is BudgetDecision.STOP
    assert any("MODEL_NOT_ALLOWED" in value for value in result.reasons)
    assert any("FEATURE_QUOTA_EXCEEDED" in value for value in result.reasons)
    assert any("UNKNOWN_COST_STOP_POLICY" in value for value in result.reasons)


def test_cross_tenant_quota_mutation_is_denied_and_audited(tmp_path: Path) -> None:
    auth, audit = _auth(tmp_path)
    quotas = TenantQuotaService(auth)
    owner_b = _subject(
        Role.OWNER,
        organization_id="org-b",
        workspace_id="ws-b",
    )
    with pytest.raises(QuotaPolicyError, match="RESOURCE_ORGANIZATION_MISMATCH"):
        quotas.set_policy(
            QuotaPolicy(
                policy_id="org-a-policy",
                organization_id="org-a",
                scope=QuotaScope.ORGANIZATION,
                currency="USD",
                daily_budget=_limit("10", "8", "9"),
            ),
            subject=owner_b,
            tenant=_tenant("org-b", "ws-b"),
            trace_id="trace-cross-tenant-quota",
        )
    assert audit.records()[-1].envelope.outcome == "DENY"


def test_workspace_monthly_budget_surfaces_warning_without_overriding_execution(
    tmp_path: Path,
) -> None:
    auth, _audit = _auth(tmp_path)
    quotas = TenantQuotaService(auth)
    admin = _subject(Role.ADMIN)
    tenant = _tenant()
    quotas.set_policy(
        QuotaPolicy(
            policy_id="workspace-monthly",
            organization_id="org-a",
            workspace_id="ws-a",
            scope=QuotaScope.WORKSPACE,
            currency="USD",
            monthly_budget=_limit("100", "75", "95"),
        ),
        subject=admin,
        tenant=tenant,
        trace_id="trace-monthly-policy",
    )
    result = quotas.evaluate(
        subject=QuotaSubject(tenant=tenant, user_id="learner-a"),
        usage=QuotaUsageSnapshot(
            monthly_cost={QuotaScope.WORKSPACE: Decimal("80")}
        ),
        run_id="run-monthly",
        operation_id="op-monthly",
        selected_model=None,
        feature=None,
        estimate=None,
        provider_call_required=False,
    )
    assert result.decision is BudgetDecision.WARN
    assert result.provider_execution_allowed is True
    assert any("MONTHLY:WARNING_THRESHOLD_REACHED" in value for value in result.reasons)


def test_personal_local_without_enterprise_quota_policy_remains_allowed(tmp_path: Path) -> None:
    auth, _audit = _auth(tmp_path)
    quotas = TenantQuotaService(auth)
    tenant = _tenant(None, None)
    result = quotas.evaluate(
        subject=QuotaSubject(tenant=tenant, user_id="local-user"),
        usage=QuotaUsageSnapshot(),
        run_id="run-local",
        operation_id="op-local",
        selected_model="local-model",
        feature="local-teaching",
        estimate=None,
        provider_call_required=False,
    )
    assert result.decision is BudgetDecision.ALLOW
    assert result.evaluated_policy_ids == ()


def _cost_store() -> TenantCostStore:
    store = TenantCostStore()
    for entry in (
        TenantCostEntry(
            entry_id="cost-a1",
            organization_id="org-a",
            workspace_id="ws-a",
            user_id="learner-a",
            cohort_id="cohort-a",
            currency="USD",
            amount=Decimal("1.25"),
            occurred_at=NOW,
        ),
        TenantCostEntry(
            entry_id="cost-a2",
            organization_id="org-a",
            workspace_id="ws-a",
            user_id="learner-b",
            cohort_id="cohort-a",
            currency="USD",
            amount=Decimal("2.75"),
            occurred_at=NOW,
        ),
        TenantCostEntry(
            entry_id="cost-b1",
            organization_id="org-b",
            workspace_id="ws-b",
            user_id="learner-b",
            cohort_id="cohort-b",
            currency="USD",
            amount=Decimal("99"),
            occurred_at=NOW,
        ),
    ):
        store.append(entry)
    return store


def test_cost_views_enforce_learner_instructor_admin_owner_and_auditor_scopes(
    tmp_path: Path,
) -> None:
    auth, _audit = _auth(tmp_path)
    views = TenantCostViewService(auth, _cost_store())
    learner = _subject(Role.LEARNER, subject_id="learner-a")
    own = views.view(
        CostViewRequest(
            subject=learner,
            tenant=_tenant(),
            scope=CostViewScope.USER,
            user_id="learner-a",
            trace_id="trace-learner-cost",
        )
    )
    assert own.total_cost == Decimal("1.25")
    with pytest.raises(CostViewDenied, match="LEARNER_OWN_COST_ONLY"):
        views.view(
            CostViewRequest(
                subject=learner,
                tenant=_tenant(),
                scope=CostViewScope.USER,
                user_id="learner-b",
                trace_id="trace-learner-guess",
            )
        )

    instructor = _subject(Role.INSTRUCTOR)
    cohort = views.view(
        CostViewRequest(
            subject=instructor,
            tenant=_tenant(),
            scope=CostViewScope.COHORT,
            cohort_id="cohort-a",
            relationships=RelationshipContext(
                assigned_cohort_ids=frozenset({"cohort-a"})
            ),
            trace_id="trace-instructor-cost",
        )
    )
    assert cohort.total_cost == Decimal("4.00")
    assert cohort.user_id is None

    admin = _subject(Role.ADMIN)
    workspace = views.view(
        CostViewRequest(
            subject=admin,
            tenant=_tenant(),
            scope=CostViewScope.WORKSPACE,
            trace_id="trace-admin-cost",
        )
    )
    assert workspace.total_cost == Decimal("4.00")

    owner = _subject(Role.OWNER, workspace_id=None)
    organization = views.view(
        CostViewRequest(
            subject=owner,
            tenant=_tenant("org-a", None),
            scope=CostViewScope.ORGANIZATION,
            trace_id="trace-owner-cost",
        )
    )
    assert organization.total_cost == Decimal("4.00")

    auditor = _subject(Role.AUDITOR)
    audited = views.view(
        CostViewRequest(
            subject=auditor,
            tenant=_tenant(),
            scope=CostViewScope.WORKSPACE,
            trace_id="trace-auditor-cost",
        )
    )
    assert audited.read_only is True
    assert audited.total_cost == Decimal("4.00")


def test_cost_view_cross_tenant_guess_is_denied_and_audited(tmp_path: Path) -> None:
    auth, audit = _auth(tmp_path)
    views = TenantCostViewService(auth, _cost_store())
    owner_a = _subject(Role.OWNER, workspace_id=None)
    with pytest.raises(CostViewDenied, match="CROSS_TENANT_DENIED"):
        views.view(
            CostViewRequest(
                subject=owner_a,
                tenant=_tenant("org-b", None),
                scope=CostViewScope.ORGANIZATION,
                trace_id="trace-cost-cross-tenant",
            )
        )
    assert audit.records()[-1].envelope.outcome == "DENY"
