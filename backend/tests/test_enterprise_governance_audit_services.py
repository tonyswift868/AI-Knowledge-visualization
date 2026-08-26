from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
from app.governance.audit_services import (
    AuditAccessDenied,
    AuditExportRequest,
    AuditExportService,
    AuditQuery,
    AuditQueryService,
    BreakGlassAccessRequest,
    BreakGlassStatus,
    BreakGlassWorkflowDenied,
    BreakGlassWorkflowService,
)
from app.governance.authorization import AuthorizationService, BreakGlassPolicy
from app.governance.models import (
    Action,
    GovernanceActorAdapter,
    PolicyRequest,
    RelationshipContext,
    ResourceType,
    Role,
)


NOW = datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc)


def subject(
    role: Role,
    *,
    subject_id: str,
    organization_id: str = "org-a",
    workspace_id: str | None = None,
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


def tenant(
    organization_id: str = "org-a",
    workspace_id: str | None = None,
) -> TenantContext:
    return TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )


def scope(
    organization_id: str = "org-a",
    workspace_id: str | None = None,
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


def authorize_sample(
    authorization: AuthorizationService,
    *,
    actor: GovernanceActorAdapter,
    current_tenant: TenantContext,
    trace_id: str,
) -> None:
    authorization.authorize(
        PolicyRequest(
            subject=actor,
            tenant=current_tenant,
            action=Action.READ,
            resource=ResourceType.DOCUMENT,
            resource_id=f"document-{trace_id}",
            resource_scope=scope(
                str(current_tenant.organization_id),
                current_tenant.workspace_id,
            ),
            trace_id=trace_id,
            occurred_at=NOW,
        )
    )


def test_audit_query_is_tenant_fenced_filterable_and_minimally_redacted(
    tmp_path: Path,
) -> None:
    audit = SQLiteAuditLog(tmp_path / "audit-query.sqlite3")
    authorization = AuthorizationService(audit)
    authorize_sample(
        authorization,
        actor=subject(Role.OWNER, subject_id="owner-a"),
        current_tenant=tenant("org-a"),
        trace_id="trace-org-a",
    )
    authorize_sample(
        authorization,
        actor=subject(Role.OWNER, subject_id="owner-b", organization_id="org-b"),
        current_tenant=tenant("org-b"),
        trace_id="trace-org-b",
    )
    queries = AuditQueryService(authorization, audit)
    auditor = subject(Role.AUDITOR, subject_id="auditor-a")
    result = queries.query(
        AuditQuery(
            organization_id="org-a",
            actor_id="owner-a",
            action="document.read",
            resource_type="document",
            outcome="ALLOW",
            occurred_after=NOW - timedelta(seconds=1),
            occurred_before=NOW + timedelta(seconds=1),
        ),
        subject=auditor,
        tenant=tenant("org-a"),
        trace_id="trace-audit-query",
    )
    assert len(result.entries) == 1
    assert result.entries[0].actor_id == "owner-a"
    serialized = json.dumps(result.entries[0].to_public_dict(), sort_keys=True)
    assert "token-owner-a" not in serialized
    assert "session-owner-a" not in serialized
    assert "request-owner-a" not in serialized

    with pytest.raises(AuditAccessDenied, match="RESOURCE_ORGANIZATION_MISMATCH"):
        queries.query(
            AuditQuery(organization_id="org-b"),
            subject=auditor,
            tenant=tenant("org-a"),
            trace_id="trace-cross-tenant-audit-query",
        )
    assert audit.records()[-1].envelope.outcome == "DENY"


def test_auditor_can_export_redacted_audit_but_remains_write_denied(tmp_path: Path) -> None:
    audit = SQLiteAuditLog(tmp_path / "audit-export.sqlite3")
    authorization = AuthorizationService(audit)
    authorize_sample(
        authorization,
        actor=subject(Role.OWNER, subject_id="owner-a"),
        current_tenant=tenant(),
        trace_id="trace-source-event",
    )
    auditor = subject(Role.AUDITOR, subject_id="auditor-a")
    export = AuditExportService(AuditQueryService(authorization, audit)).export(
        AuditExportRequest(AuditQuery(organization_id="org-a")),
        subject=auditor,
        tenant=tenant(),
        trace_id="trace-audit-export",
        now=NOW,
    )
    assert export.record_count == 1
    assert len(export.sha256) == 64
    assert "token-owner-a" not in "\n".join(export.json_lines)
    assert audit.records()[export.authorization_audit_sequence - 1].envelope.action == "audit.export"

    decision = authorization.authorize(
        PolicyRequest(
            subject=auditor,
            tenant=tenant(),
            action=Action.MANAGE,
            resource=ResourceType.MEMBERSHIP,
            resource_id="member-target",
            resource_scope=scope(),
            trace_id="trace-auditor-write-denied",
            occurred_at=NOW,
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "ROLE_MATRIX_DENY"


def test_break_glass_requires_two_people_and_revocation_invalidates_grant(
    tmp_path: Path,
) -> None:
    audit = SQLiteAuditLog(tmp_path / "break-glass.sqlite3")
    authorization = AuthorizationService(
        audit,
        break_glass_policy=BreakGlassPolicy(
            enabled=True,
            maximum_duration=timedelta(minutes=15),
        ),
    )
    workflow = BreakGlassWorkflowService(authorization, audit)
    requester = subject(
        Role.ADMIN,
        subject_id="admin-requester",
        workspace_id="ws-a",
    )
    approver = subject(
        Role.OWNER,
        subject_id="owner-approver",
        workspace_id="ws-a",
    )
    current_tenant = tenant(workspace_id="ws-a")
    target_scope = scope(workspace_id="ws-a")
    requested = workflow.request_access(
        BreakGlassAccessRequest(
            reason="Emergency learner safety repair",
            actions=frozenset({Action.DELETE}),
            resources=frozenset({ResourceType.STUDENT_PROGRESS}),
            resource_ids=frozenset({"student-private-model"}),
            resource_scope=target_scope,
            duration=timedelta(minutes=5),
        ),
        subject=requester,
        tenant=current_tenant,
        trace_id="trace-bg-request",
        now=NOW,
    )
    with pytest.raises(BreakGlassWorkflowDenied, match="SELF_APPROVAL"):
        workflow.approve(
            requested.request_id,
            subject=requester,
            tenant=current_tenant,
            trace_id="trace-bg-self-approve",
            now=NOW + timedelta(seconds=5),
        )
    approved = workflow.approve(
        requested.request_id,
        subject=approver,
        tenant=current_tenant,
        trace_id="trace-bg-approve",
        now=NOW + timedelta(seconds=10),
    )
    assert approved.status is BreakGlassStatus.APPROVED
    active = workflow.activate(
        requested.request_id,
        subject=requester,
        tenant=current_tenant,
        trace_id="trace-bg-activate",
        now=NOW + timedelta(seconds=20),
    )
    assert active.status is BreakGlassStatus.ACTIVE
    assert active.grant is not None

    elevated_request = PolicyRequest(
        subject=requester,
        tenant=current_tenant,
        action=Action.DELETE,
        resource=ResourceType.STUDENT_PROGRESS,
        resource_id="student-private-model",
        resource_scope=target_scope,
        relationships=RelationshipContext(private_student_model=True),
        trace_id="trace-bg-use",
        occurred_at=NOW + timedelta(seconds=30),
        break_glass=active.grant,
    )
    assert authorization.authorize(elevated_request).reason_code == "BREAK_GLASS_ACTIVE"
    revoked = workflow.revoke(
        requested.request_id,
        subject=approver,
        tenant=current_tenant,
        trace_id="trace-bg-revoke",
        now=NOW + timedelta(seconds=40),
    )
    assert revoked.status is BreakGlassStatus.REVOKED
    denied_after_revoke = authorization.authorize(elevated_request)
    assert denied_after_revoke.allowed is False
    assert denied_after_revoke.reason_code == "ROLE_MATRIX_DENY"
    actions = [record.envelope.action for record in audit.records()]
    for lifecycle_action in (
        "break_glass.request",
        "break_glass.approve",
        "break_glass.activate",
        "break_glass.revoke",
    ):
        assert lifecycle_action in actions


def test_break_glass_expire_transitions_and_fences_grant(tmp_path: Path) -> None:
    audit = SQLiteAuditLog(tmp_path / "break-glass-expire.sqlite3")
    authorization = AuthorizationService(
        audit,
        break_glass_policy=BreakGlassPolicy(
            enabled=True,
            maximum_duration=timedelta(minutes=15),
        ),
    )
    workflow = BreakGlassWorkflowService(authorization, audit)
    requester = subject(Role.ADMIN, subject_id="admin-requester", workspace_id="ws-a")
    approver = subject(Role.OWNER, subject_id="owner-approver", workspace_id="ws-a")
    current_tenant = tenant(workspace_id="ws-a")
    request = workflow.request_access(
        BreakGlassAccessRequest(
            reason="Emergency short repair window",
            actions=frozenset({Action.DELETE}),
            resources=frozenset({ResourceType.STUDENT_PROGRESS}),
            resource_ids=frozenset({"student-private-model"}),
            resource_scope=scope(workspace_id="ws-a"),
            duration=timedelta(minutes=1),
        ),
        subject=requester,
        tenant=current_tenant,
        trace_id="trace-expire-request",
        now=NOW,
    )
    workflow.approve(
        request.request_id,
        subject=approver,
        tenant=current_tenant,
        trace_id="trace-expire-approve",
        now=NOW + timedelta(seconds=5),
    )
    active = workflow.activate(
        request.request_id,
        subject=requester,
        tenant=current_tenant,
        trace_id="trace-expire-activate",
        now=NOW + timedelta(seconds=10),
    )
    expired = workflow.expire(
        request.request_id,
        subject=approver,
        tenant=current_tenant,
        trace_id="trace-expire",
        now=NOW + timedelta(minutes=2),
    )
    assert expired.status is BreakGlassStatus.EXPIRED
    assert active.grant is not None
    assert "break_glass.expire" in [record.envelope.action for record in audit.records()]
