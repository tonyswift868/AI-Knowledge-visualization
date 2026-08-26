from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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
from app.governance.authorization import AuthorizationService
from app.governance.models import GovernanceActorAdapter, RelationshipContext, Role
from app.governance.surface_enforcement import (
    AuthorizedServiceCall,
    ContextTrustSource,
    FastAPIRequestGuard,
    ProtectedResourceReference,
    TrustedEnterpriseContext,
    authorize_request,
    authorize_service_call,
)
from app.governance.surface_manifest import (
    AuditRequirement,
    ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    RequiredPolicy,
    SurfaceDomain,
    TransportCapability,
)


NOW = datetime(2026, 8, 26, 19, 0, tzinfo=timezone.utc)


def subject(
    role: Role,
    *,
    subject_id: str,
    kind: ActorKind = ActorKind.HUMAN,
    service_permissions: tuple[str, ...] = (),
    workspace_id: str | None = "ws-a",
) -> GovernanceActorAdapter:
    actor = ActorContext(
        subject_id=subject_id,
        actor_kind=kind,
        issuer="https://issuer.example.test",
        token_id=f"token-{subject_id}",
        session_id=f"session-{subject_id}",
        request_id=f"request-{subject_id}",
        authentication_time=NOW,
        authentication_strength="mfa" if kind is ActorKind.HUMAN else "workload-identity",
        organization_id="org-a",
        workspace_id=workspace_id,
        membership_id=f"membership-{subject_id}",
        roles=(role.value,),
        service_permissions=service_permissions,
    )
    return GovernanceActorAdapter(
        actor=actor,
        effective_role=role,
        workspace_memberships=(
            frozenset({workspace_id}) if workspace_id is not None else frozenset()
        ),
    )


def trusted(
    role: Role,
    *,
    subject_id: str,
    kind: ActorKind = ActorKind.HUMAN,
    service_permissions: tuple[str, ...] = (),
    workspace_id: str | None = "ws-a",
) -> TrustedEnterpriseContext:
    return TrustedEnterpriseContext(
        subject=subject(
            role,
            subject_id=subject_id,
            kind=kind,
            service_permissions=service_permissions,
            workspace_id=workspace_id,
        ),
        tenant=TenantContext(
            mode=TenantMode.ENTERPRISE_SERVER,
            organization_id="org-a",
            workspace_id=workspace_id,
        ),
        source=(
            ContextTrustSource.SERVICE_ACCOUNT
            if kind is ActorKind.SERVICE
            else ContextTrustSource.IDENTITY_RESOLVER
        ),
        validated_at=NOW,
    )


def scope(workspace_id: str | None = "ws-a") -> ResourceScope:
    return ResourceScope(
        scope_type=(
            ResourceScopeType.WORKSPACE
            if workspace_id is not None
            else ResourceScopeType.ORGANIZATION
        ),
        organization_id="org-a",
        workspace_id=workspace_id,
        classification=ResourceClassification.RESTRICTED,
    )


def services(tmp_path: Path) -> tuple[AuthorizationService, SQLiteAuditLog]:
    audit = SQLiteAuditLog(tmp_path / "surface-enforcement.sqlite3")
    return AuthorizationService(audit), audit


def metadata(record) -> dict[str, object]:
    return {
        item.key: item.value
        for item in record.envelope.metadata_redacted
    }


def test_protected_surface_manifest_covers_every_enterprise_domain() -> None:
    manifest = ENTERPRISE_PROTECTED_SURFACE_MANIFEST
    assert len(manifest.operations) == 79
    assert {operation.domain for operation in manifest.operations} == set(SurfaceDomain)
    assert all(operation.resource_type for operation in manifest.operations)
    assert all(operation.tenant_scope for operation in manifest.operations)
    assert all(operation.required_policy for operation in manifest.operations)
    assert all(operation.audit_requirement for operation in manifest.operations)
    assert all(operation.source_seams for operation in manifest.operations)
    assert all(
        operation.audit_requirement is AuditRequirement.AUDIT_REQUIRED
        for operation in manifest.operations
        if operation.sensitive
    )
    assert {
        TransportCapability.GET,
        TransportCapability.POST,
        TransportCapability.PATCH,
        TransportCapability.DELETE,
        TransportCapability.EXPORT,
        TransportCapability.INTERNAL,
        TransportCapability.JOB,
    } <= {
        capability
        for operation in manifest.operations
        for capability in operation.transports
    }


def test_endpoint_neutral_guard_uses_only_trusted_context_and_audits_denial(
    tmp_path: Path,
) -> None:
    authorization, audit = services(tmp_path)

    @dataclass
    class ForgedRequest:
        query_params: dict[str, str]
        headers: dict[str, str]

    class Resolver:
        def resolve(self, _request: object) -> TrustedEnterpriseContext:
            return trusted(Role.LEARNER, subject_id="learner-a", workspace_id=None)

    request = ForgedRequest(
        query_params={"role": "ORGANIZATION_OWNER", "organization_id": "org-b"},
        headers={"x-role": "ORGANIZATION_OWNER", "x-organization-id": "org-b"},
    )
    receipt = FastAPIRequestGuard(authorization, Resolver()).authorize(
        request,
        operation_id="admin.member.invite",
        resource=ProtectedResourceReference("member-target", scope(None)),
        trace_id="trace-forged-transport-claims",
        occurred_at=NOW,
    )
    assert receipt.allowed is False
    assert receipt.reason_code == "ROLE_MATRIX_DENY"
    assert receipt.transport_claims_ignored is True
    assert audit.records()[-1].envelope.outcome == "DENY"
    assert metadata(audit.records()[-1])["surface_operation_id"] == "admin.member.invite"


def test_authorize_request_allows_own_resource_and_denies_forged_id(tmp_path: Path) -> None:
    authorization, audit = services(tmp_path)
    context = trusted(Role.OWNER, subject_id="owner-a")
    allowed = authorize_request(
        authorization,
        context=context,
        operation_id="document.detail",
        resource=ProtectedResourceReference("document-own", scope()),
        trace_id="trace-own-document",
        occurred_at=NOW,
    )
    denied = authorize_request(
        authorization,
        context=context,
        operation_id="document.detail",
        resource=ProtectedResourceReference(
            "forged-document-id",
            scope(),
            RelationshipContext(resource_exists=False),
        ),
        trace_id="trace-forged-document",
        occurred_at=NOW,
    )
    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason_code == "RESOURCE_NOT_FOUND"
    assert [record.envelope.outcome for record in audit.records()] == ["ALLOW", "DENY"]


def test_background_service_requires_service_permission_even_when_role_allows(
    tmp_path: Path,
) -> None:
    authorization, audit = services(tmp_path)
    missing_permission = AuthorizedServiceCall(
        context=trusted(
            Role.ADMIN,
            subject_id="job-worker",
            kind=ActorKind.SERVICE,
        ),
        operation_id="job.enqueue",
        resource=ProtectedResourceReference("job-a", scope()),
        trace_id="trace-job-no-permission",
        required_service_permission="jobs:enqueue",
        occurred_at=NOW,
    )
    denied = authorize_service_call(authorization, missing_permission)
    permitted_call = AuthorizedServiceCall(
        context=trusted(
            Role.ADMIN,
            subject_id="job-worker-permitted",
            kind=ActorKind.SERVICE,
            service_permissions=("jobs:enqueue",),
        ),
        operation_id="job.enqueue",
        resource=ProtectedResourceReference("job-b", scope()),
        trace_id="trace-job-permitted",
        required_service_permission="jobs:enqueue",
        occurred_at=NOW,
    )
    allowed = authorize_service_call(authorization, permitted_call)
    assert denied.allowed is False
    assert denied.reason_code == "SERVICE_PERMISSION_REQUIRED"
    assert allowed.allowed is True
    assert [record.envelope.outcome for record in audit.records()] == ["DENY", "ALLOW"]


def test_manifest_context_validation_is_not_misrepresented_as_authorization(
    tmp_path: Path,
) -> None:
    authorization, _audit = services(tmp_path)
    operation = ENTERPRISE_PROTECTED_SURFACE_MANIFEST.require("identity.context.validate")
    assert operation.required_policy is RequiredPolicy.CONTEXT_VALIDATION
    try:
        authorize_request(
            authorization,
            context=trusted(Role.OWNER, subject_id="owner-a", workspace_id=None),
            operation_id=operation.operation_id,
            resource=ProtectedResourceReference("org-a", scope(None)),
            trace_id="trace-context-operation",
            occurred_at=NOW,
        )
    except ValueError as exc:
        assert "does not use AuthorizationService" in str(exc)
    else:  # pragma: no cover - fail loudly if the guard widens scope
        raise AssertionError("context validation must not be treated as authorization")
