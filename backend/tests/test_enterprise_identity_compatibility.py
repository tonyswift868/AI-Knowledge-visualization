from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    TenantContext,
    TenantMode,
    verify_frozen_contract_hashes,
)
from app.enterprise.identity.context import RequestIdentityContext
from app.enterprise.identity.runtime import (
    IdentityBinding,
    enterprise_route_is_tenant_ready,
    identity_job_context,
)
from app.enterprise.jobs_v1.models import JobRequest, WorkflowKind
from app.observability.context import correlation_scope, new_correlation_context


ROOT = Path(__file__).resolve().parents[2]
REALM = ROOT / "deploy" / "enterprise" / "config" / "keycloak-realm.json"
COMPOSE = ROOT / "deploy" / "enterprise" / "staging" / "compose.orchestrator.yml"


def test_realm_provisions_exact_clients_roles_pkce_audience_and_secret_references() -> None:
    payload = json.loads(REALM.read_text(encoding="utf-8"))
    assert payload["realm"] == "mentorflow"
    assert payload["enabled"] is True
    assert payload["sslRequired"] == "external"
    assert payload["registrationAllowed"] is False
    assert payload["defaultSignatureAlgorithm"] == "RS256"
    assert payload["revokeRefreshToken"] is True
    assert payload["refreshTokenMaxReuse"] == 0
    assert {role["name"] for role in payload["roles"]["realm"]} == {
        "OWNER",
        "ADMIN",
        "CONTENT_MANAGER",
        "INSTRUCTOR",
        "LEARNER",
        "AUDITOR",
    }
    clients = {client["clientId"]: client for client in payload["clients"]}
    assert set(clients) == {
        "mentorflow-api",
        "mentorflow-web",
        "mentorflow-bff",
        "mentorflow-worker",
    }

    api = clients["mentorflow-api"]
    assert api["bearerOnly"] is True
    assert api["standardFlowEnabled"] is False
    web = clients["mentorflow-web"]
    assert web["publicClient"] is True
    assert web["standardFlowEnabled"] is True
    assert web["implicitFlowEnabled"] is False
    assert web["directAccessGrantsEnabled"] is False
    assert web["serviceAccountsEnabled"] is False
    assert web["attributes"]["pkce.code.challenge.method"] == "S256"
    assert web["redirectUris"] == [
        "${MENTORFLOW_PUBLIC_ORIGIN}/api/enterprise/auth/callback"
    ]
    assert web["webOrigins"] == ["${MENTORFLOW_PUBLIC_ORIGIN}"]
    assert "*" not in json.dumps(web, sort_keys=True)
    audience = web["protocolMappers"][0]
    assert audience["protocolMapper"] == "oidc-audience-mapper"
    assert audience["config"]["included.client.audience"] == "mentorflow-api"
    assert audience["config"]["access.token.claim"] == "true"
    assert audience["config"]["id.token.claim"] == "false"

    bff = clients["mentorflow-bff"]
    worker = clients["mentorflow-worker"]
    assert bff["secret"] == "${MENTORFLOW_BFF_CLIENT_SECRET}"
    assert worker["secret"] == "${MENTORFLOW_WORKER_CLIENT_SECRET}"
    assert worker["serviceAccountsEnabled"] is True
    assert worker["standardFlowEnabled"] is False
    assert worker["directAccessGrantsEnabled"] is False
    for client in (bff, worker):
        assert client["secret"].startswith("${") and client["secret"].endswith("}")


def test_existing_keycloak_container_contract_is_production_start_and_internal_health() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "/opt/keycloak/bin/kc.sh start" in compose
    assert "start-dev" not in compose
    assert "KC_DB: postgres" in compose
    assert "--health-enabled=true" in compose
    assert "/dev/tcp/127.0.0.1/9000" in compose
    assert "GET /health/ready" in compose
    assert "GET /health/live" not in compose
    keycloak_block = compose.split("  keycloak:", 1)[1].split("  otel-collector:", 1)[0]
    assert "ports:" not in keycloak_block


def test_identity_context_round_trips_into_durable_worker_without_redefinition() -> None:
    verify_frozen_contract_hashes()
    actor = ActorContext(
        subject_id="subject-001",
        actor_kind=ActorKind.HUMAN,
        issuer="https://identity.example.test/realms/mentorflow",
        token_id="token-001",
        session_id="enterprise-session-0000000000000001",
        request_id="request-001",
        authentication_time=datetime.now(timezone.utc),
        authentication_strength="mfa",
        organization_id="org-a",
        workspace_id="workspace-a",
        membership_id="membership-001",
        roles=("LEARNER",),
    )
    tenant = TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id="org-a",
        workspace_id="workspace-a",
    )
    payload = identity_job_context(
        IdentityBinding(
            context=RequestIdentityContext(actor=actor, tenant=tenant),
            authentication_mode="COOKIE",
        )
    )
    request = JobRequest(
        workflow_kind=WorkflowKind.DOCUMENT_INGESTION,
        payload={"document_id": "document-001"},
        actor_context=payload["actor_context"],
        tenant_context=payload["tenant_context"],
        request_id="request-001",
        trace_id="0" * 31 + "1",
        idempotency_key="identity-worker-001",
    )
    assert type(request.actor_context) is ActorContext
    assert type(request.tenant_context) is TenantContext
    assert request.tenant_partition == "ENTERPRISE_SERVER:org-a:workspace-a"


def test_identity_correlation_records_only_safe_scope_identifiers() -> None:
    base = new_correlation_context(
        request_id="request-001",
        feature="auth",
    )
    with correlation_scope(
        base,
        organization_id="org-a",
        workspace_id="workspace-a",
        authorization="Bearer must-not-be-recorded",
        refresh_token="must-not-be-recorded",
    ) as scoped:
        fields = scoped.log_fields()
    assert fields["organization_id"] == "org-a"
    assert fields["workspace_id"] == "workspace-a"
    assert "authorization" not in fields
    assert "refresh_token" not in fields
    assert "token" not in json.dumps(fields).casefold()


def test_legacy_local_api_is_not_tenant_ready_in_enterprise_mode() -> None:
    assert enterprise_route_is_tenant_ready("/api/enterprise/auth/me") is True
    assert enterprise_route_is_tenant_ready("/api/enterprise/jobs/summary") is True
    assert enterprise_route_is_tenant_ready("/api/workspaces") is False
    assert enterprise_route_is_tenant_ready("/api/sources/source-001") is False
    assert enterprise_route_is_tenant_ready("/api/enterprise-admin/bootstrap") is False
