from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.enterprise.contracts_v1 import ActorContext, ActorKind, TenantContext, TenantMode
from app.governance.admin_api import (
    AdminApiContext,
    StaticAdminContextResolver,
    create_enterprise_admin_app,
    create_enterprise_admin_services,
)
from app.governance.models import GovernanceActorAdapter, Role
from app.governance.provider_governance import (
    ProviderConfig,
    ProviderCredentialRef,
    ProviderScope,
)
from app.governance.ui_seams import (
    AdminNavigationCategory,
    NavigationState,
    PermissionAwareNavigation,
)


NOW = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)


def context(role: Role, *, subject_id: str) -> AdminApiContext:
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
        roles=(role.value,),
    )
    return AdminApiContext(
        subject=GovernanceActorAdapter(actor=actor, effective_role=role),
        tenant=TenantContext(
            mode=TenantMode.ENTERPRISE_SERVER,
            organization_id="org-a",
        ),
    )


def test_permission_navigation_has_all_ui_categories_without_becoming_authority() -> None:
    auditor = context(Role.AUDITOR, subject_id="auditor-a").subject
    entries = PermissionAwareNavigation.for_subject(auditor).admin_entries()
    assert tuple(entry.category for entry in entries) == tuple(AdminNavigationCategory)
    states = {entry.category: entry.state for entry in entries}
    assert states[AdminNavigationCategory.AUDIT] is NavigationState.VISIBLE
    assert states[AdminNavigationCategory.COST] is NavigationState.DISABLED
    assert states[AdminNavigationCategory.PROVIDER_SETTINGS] is NavigationState.HIDDEN
    assert all(entry.relationship_check_required for entry in entries)


def test_standalone_admin_api_mutations_and_reads_use_context_resolver(tmp_path: Path) -> None:
    services = create_enterprise_admin_services(str(tmp_path / "admin-api.sqlite3"))
    owner = context(Role.OWNER, subject_id="owner-a")
    app = create_enterprise_admin_app(
        services,
        StaticAdminContextResolver(owner),
    )
    with TestClient(app) as client:
        health = client.get("/api/enterprise-admin/health")
        assert health.status_code == 200
        assert health.json()["provider_calls"] == 0
        navigation = client.get("/api/enterprise-admin/navigation").json()
        assert len(navigation["entries"]) == 8
        assert navigation["authorization_still_required"] is True
        bootstrap = client.get("/api/enterprise-admin/bootstrap").json()
        assert bootstrap["actor"]["subject_id"] == "owner-a"
        assert bootstrap["tenant"]["organization_id"] == "org-a"
        invited = client.post(
            "/api/enterprise-admin/members/invitations",
            json={
                "member_id": "member-a",
                "subject_id": "learner-a",
                "email": "learner@example.test",
                "roles": ["LEARNER"],
            },
        )
        assert invited.status_code == 201
        assert invited.json()["mutation_performed"] is True
        members = client.get("/api/enterprise-admin/members")
        assert members.status_code == 200
        assert members.json()["members"][0]["member_id"] == "member-a"

        workspace = client.post(
            "/api/enterprise-admin/workspaces",
            json={"workspace_id": "ws-a", "name": "Workspace A"},
        )
        assert workspace.status_code == 201
        assert client.get("/api/enterprise-admin/workspaces").json()["workspaces"][0]["workspace_id"] == "ws-a"

        audit_query = client.post(
            "/api/enterprise-admin/audit/query",
            json={"organization_id": "org-a"},
        )
        assert audit_query.status_code == 200
        assert len(audit_query.json()["entries"]) >= 4

    learner_app = create_enterprise_admin_app(
        services,
        StaticAdminContextResolver(context(Role.LEARNER, subject_id="learner-operator")),
    )
    before = services.organizations.snapshot()
    with TestClient(learner_app) as learner_client:
        denied = learner_client.post(
            "/api/enterprise-admin/members/invitations",
            json={
                "member_id": "member-denied",
                "subject_id": "learner-denied",
                "email": "denied@example.test",
            },
        )
    assert denied.status_code == 403
    assert services.organizations.snapshot() == before
    assert services.audit_log.records()[-1].envelope.outcome == "DENY"


def test_provider_api_projection_never_contains_credential_reference(tmp_path: Path) -> None:
    services = create_enterprise_admin_services(str(tmp_path / "provider-api.sqlite3"))
    owner = context(Role.OWNER, subject_id="owner-a")
    reference = ProviderCredentialRef("mfcred_admin_api_reference_123")
    services.providers.register(
        ProviderConfig(
            config_id="provider-org-a",
            organization_id="org-a",
            provider="openai-compatible",
            secret_reference=reference,
            approved_models=frozenset({"model-a"}),
            scope=ProviderScope.ORGANIZATION,
        ),
        subject=owner.subject,
        tenant=owner.tenant,
        trace_id="trace-provider-fixture",
    )
    app = create_enterprise_admin_app(
        services,
        StaticAdminContextResolver(owner),
    )
    with TestClient(app) as client:
        response = client.get("/api/enterprise-admin/providers/provider-org-a")
        collection = client.get("/api/enterprise-admin/providers")
    assert response.status_code == 200
    assert collection.status_code == 200
    assert collection.json()["providers"][0]["config_id"] == "provider-org-a"
    serialized = json.dumps(response.json(), sort_keys=True)
    assert reference.value not in serialized
    assert "secret_reference" not in serialized


def test_integration_manifest_declares_standalone_mount_and_frozen_contract() -> None:
    manifest_path = (
        Path(__file__).parents[2]
        / "artifacts"
        / "enterprise_governance_control_plane"
        / "integration_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["backend"]["central_router_modified"] is False
    assert manifest["frontend"]["central_routes_modified"] is False
    assert manifest["frozen_contract"]["name"] == "enterprise-context.v1"
    assert manifest["frozen_contract"]["duplicated"] is False
