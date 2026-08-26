from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.governance.audit import SQLiteAuditLog
from app.governance.audit_coverage import AuditCoverageError, AuditCoverageVerifier
from app.governance.authorization import AuthorizationService
from app.governance.models import GovernanceActorAdapter, Role
from app.governance.runtime import AuthorizedResourceCandidate
from app.governance.side_effects import (
    GovernedSideEffectExecutor,
    SideEffectKind,
    SideEffectPlan,
)
from app.governance.surface_enforcement import (
    AuthorizedServiceCall,
    ContextTrustSource,
    ProtectedResourceReference,
    TrustedEnterpriseContext,
    authorize_request,
)
from app.governance.surface_manifest import (
    ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    RequiredPolicy,
    TenantScopeRequirement,
)
from app.governance.tenant_export import (
    TenantAwareExportService,
    TenantExportRequest,
)


NOW = datetime(2026, 8, 26, 20, 0, tzinfo=timezone.utc)


def context(
    role: Role,
    *,
    subject_id: str,
    workspace_id: str | None = "ws-a",
) -> TrustedEnterpriseContext:
    actor = ActorContext(
        subject_id=subject_id,
        actor_kind=ActorKind.HUMAN,
        issuer="https://issuer.example.test",
        token_id=f"token-{subject_id}",
        session_id=f"session-{subject_id}",
        request_id=f"request-{subject_id}",
        authentication_time=NOW,
        authentication_strength="mfa",
        organization_id="org-a",
        workspace_id=workspace_id,
        membership_id=f"membership-{subject_id}",
        roles=(role.value,),
    )
    return TrustedEnterpriseContext(
        subject=GovernanceActorAdapter(
            actor=actor,
            effective_role=role,
            workspace_memberships=(
                frozenset({workspace_id}) if workspace_id is not None else frozenset()
            ),
        ),
        tenant=TenantContext(
            mode=TenantMode.ENTERPRISE_SERVER,
            organization_id="org-a",
            workspace_id=workspace_id,
        ),
        source=ContextTrustSource.IDENTITY_RESOLVER,
        validated_at=NOW,
    )


def scope(
    *,
    organization_id: str = "org-a",
    workspace_id: str | None = "ws-a",
) -> ResourceScope:
    return ResourceScope(
        scope_type=(
            ResourceScopeType.WORKSPACE
            if workspace_id is not None
            else ResourceScopeType.ORGANIZATION
        ),
        organization_id=organization_id,
        workspace_id=workspace_id,
        classification=ResourceClassification.RESTRICTED,
    )


def services(tmp_path: Path) -> tuple[AuthorizationService, SQLiteAuditLog]:
    audit = SQLiteAuditLog(tmp_path / "policy-integration.sqlite3")
    return AuthorizationService(audit), audit


def test_deny_executes_zero_db_job_provider_and_object_effects_but_audits(
    tmp_path: Path,
) -> None:
    authorization, audit = services(tmp_path)
    effects: list[str] = []
    call = AuthorizedServiceCall(
        context=context(Role.LEARNER, subject_id="learner-a"),
        operation_id="provider_config.register",
        resource=ProtectedResourceReference("provider-a", scope()),
        trace_id="trace-denied-all-effects",
        occurred_at=NOW,
    )
    receipt = GovernedSideEffectExecutor(authorization).execute(
        call,
        SideEffectPlan(
            db_mutation=lambda: effects.append("db"),
            job_enqueue=lambda: effects.append("job"),
            provider_call=lambda: effects.append("provider"),
            object_write=lambda: effects.append("object"),
        ),
    )
    assert receipt.allowed is False
    assert receipt.denied_with_zero_side_effects is True
    assert effects == []
    assert all(receipt.count(kind) == 0 for kind in SideEffectKind)
    assert len(audit.records()) == 1
    assert audit.records()[0].envelope.outcome == "DENY"


def test_allow_executes_declared_effects_only_after_authorization_audit(tmp_path: Path) -> None:
    authorization, audit = services(tmp_path)
    observed: list[tuple[str, int]] = []

    def effect(name: str):
        def execute() -> str:
            observed.append((name, len(audit.records())))
            return name + "-done"

        return execute

    receipt = GovernedSideEffectExecutor(authorization).execute(
        AuthorizedServiceCall(
            context=context(Role.OWNER, subject_id="owner-a"),
            operation_id="job.enqueue",
            resource=ProtectedResourceReference("job-a", scope()),
            trace_id="trace-allowed-effects",
            occurred_at=NOW,
        ),
        SideEffectPlan(
            db_mutation=effect("db"),
            job_enqueue=effect("job"),
            object_write=effect("object"),
        ),
    )
    assert receipt.allowed is True
    assert receipt.performed == (
        SideEffectKind.DB_MUTATION,
        SideEffectKind.JOB_ENQUEUE,
        SideEffectKind.OBJECT_WRITE,
    )
    assert observed == [("db", 1), ("job", 1), ("object", 1)]


def test_audit_coverage_matrix_fails_when_required_operation_has_no_envelope(
    tmp_path: Path,
) -> None:
    authorization, audit = services(tmp_path)
    manifest = ENTERPRISE_PROTECTED_SURFACE_MANIFEST
    verifier = AuditCoverageVerifier(manifest)
    matrix = verifier.matrix()
    assert len(matrix.audit_required) == 78
    assert matrix.audit_optional == ("identity.context.validate",)
    assert matrix.no_audit == ("admin.health",)

    for operation in manifest.operations:
        if operation.required_policy is not RequiredPolicy.AUTHORIZATION:
            continue
        workspace_id = (
            None
            if operation.tenant_scope is TenantScopeRequirement.ORGANIZATION
            else "ws-a"
        )
        authorize_request(
            authorization,
            context=context(
                Role.OWNER,
                subject_id=f"owner-{operation.operation_id}",
                workspace_id=workspace_id,
            ),
            operation_id=operation.operation_id,
            resource=ProtectedResourceReference(
                f"resource-{operation.operation_id}",
                scope(workspace_id=workspace_id),
            ),
            trace_id=f"coverage-{operation.operation_id}",
            occurred_at=NOW,
        )
    report = verifier.assert_complete(audit.records())
    assert report.complete is True
    assert report.covered_count == 78
    with pytest.raises(AuditCoverageError, match=matrix.audit_required[0]):
        verifier.assert_complete(audit.records()[1:])


def test_list_permission_does_not_grant_document_export(tmp_path: Path) -> None:
    authorization, audit = services(tmp_path)
    instructor = context(Role.INSTRUCTOR, subject_id="instructor-a")
    list_receipt = authorize_request(
        authorization,
        context=instructor,
        operation_id="document.list",
        resource=ProtectedResourceReference("document-list", scope()),
        trace_id="trace-document-list",
        occurred_at=NOW,
    )
    export = TenantAwareExportService(authorization).export(
        TenantExportRequest(
            context=instructor,
            operation_id="document.export",
            items=(
                AuthorizedResourceCandidate("document-own", scope(), {"id": "document-own"}),
            ),
            trace_id="trace-document-export-denied",
            occurred_at=NOW,
        )
    )
    assert list_receipt.allowed is True
    assert export.exported == ()
    assert export.denied == (("document-own", "ROLE_MATRIX_DENY"),)
    assert audit.records()[-1].envelope.outcome == "DENY"


def test_tenant_export_returns_only_authorized_workspace_resources(tmp_path: Path) -> None:
    authorization, audit = services(tmp_path)
    owner = context(Role.OWNER, subject_id="owner-a")
    receipt = TenantAwareExportService(authorization).export(
        TenantExportRequest(
            context=owner,
            operation_id="plan.export",
            items=(
                AuthorizedResourceCandidate("plan-own", scope(), {"id": "plan-own"}),
                AuthorizedResourceCandidate(
                    "plan-foreign-workspace",
                    scope(workspace_id="ws-b"),
                    {"id": "plan-foreign-workspace"},
                ),
            ),
            trace_id="trace-plan-export",
            occurred_at=NOW,
        )
    )
    assert receipt.exported == ({"id": "plan-own"},)
    assert receipt.exported_resource_ids == ("plan-own",)
    assert receipt.denied == (
        ("plan-foreign-workspace", "RESOURCE_WORKSPACE_MISMATCH"),
    )
    assert receipt.audit_sequence == audit.records()[-1].sequence
    assert audit.records()[-1].envelope.outcome == "PARTIAL"


def test_empty_export_still_emits_authorization_audit(tmp_path: Path) -> None:
    authorization, audit = services(tmp_path)
    receipt = TenantAwareExportService(authorization).export(
        TenantExportRequest(
            context=context(Role.OWNER, subject_id="owner-a"),
            operation_id="student_progress.export",
            items=(),
            trace_id="trace-empty-export",
            occurred_at=NOW,
        )
    )
    assert receipt.candidate_count == 0
    assert receipt.request_allowed is True
    assert receipt.reason_code == "ROLE_AND_RELATIONSHIP_ALLOW"
    assert receipt.audit_sequence == 1
    assert audit.records()[0].envelope.action == "student_progress.export"
