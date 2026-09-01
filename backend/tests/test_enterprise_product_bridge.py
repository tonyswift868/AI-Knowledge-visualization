from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.enterprise.contracts_v1 import ActorKind, TenantMode
from app.enterprise.identity.config import EnterpriseIdentityConfig, KeycloakOidcConfig
from app.enterprise.identity.context import IdentityContextError
from app.enterprise.identity.models import (
    AuthenticatedPrincipal,
    BusinessRole,
    MembershipGrant,
    MembershipStatus,
)
from app.enterprise.identity.oidc import IdentityError
from app.enterprise.identity.runtime import (
    EnterpriseIdentityRuntime,
    enterprise_route_is_tenant_ready,
    identity_error_response,
    install_enterprise_identity,
)
from app.enterprise_tenancy.document_import import EnterpriseDocumentImportService
from app.enterprise_tenancy.models import EnterprisePersistenceRequestError
from app.enterprise_tenancy.teaching import EnterpriseTeachingService
from app.enterprise_tenancy.workspaces import EnterpriseWorkspaceService


ISSUER = "https://identity.example.test/realms/mentorflow"
ORIGIN = "https://mentorflow.example"


def _principal(subject: str, token: str) -> AuthenticatedPrincipal:
    now = datetime.now(timezone.utc)
    return AuthenticatedPrincipal(
        issuer=ISSUER,
        audiences=("mentorflow-api",),
        token_id=f"jti-{token}",
        subject=subject,
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(minutes=5),
        authentication_time=now - timedelta(minutes=1),
        authentication_strength="mfa",
        client_id="mentorflow-web",
        actor_kind=ActorKind.HUMAN,
    )


class _BearerClient:
    async def authenticate_access_token(self, token: str) -> AuthenticatedPrincipal:
        subjects = {"token-a": "user-a", "token-b": "user-b"}
        try:
            subject = subjects[token]
        except KeyError as exc:
            raise IdentityError("OIDC_TOKEN_INVALID", "Bearer token is invalid") from exc
        return _principal(subject, token)

    async def aclose(self) -> None:
        return None


class _Directory:
    def __init__(
        self,
        *,
        grants: set[tuple[str, str, str | None]] | None = None,
        status_sequence: list[MembershipStatus] | None = None,
    ) -> None:
        self.grants = grants if grants is not None else {
            ("user-a", "org-a", "workspace-a"),
            ("user-b", "org-b", "workspace-b"),
        }
        self.status_sequence = list(status_sequence or [])
        self.resolve_calls = 0
        self._lock = threading.Lock()

    def resolve_membership(
        self,
        *,
        issuer: str,
        subject: str,
        organization_id: str,
        workspace_id: str | None,
    ) -> MembershipGrant | None:
        with self._lock:
            self.resolve_calls += 1
            status = (
                self.status_sequence.pop(0)
                if self.status_sequence
                else MembershipStatus.ACTIVE
            )
        if issuer != ISSUER or (subject, organization_id, workspace_id) not in self.grants:
            return None
        return MembershipGrant(
            membership_id=f"membership-{subject}-{organization_id}",
            issuer=issuer,
            subject=subject,
            organization_id=organization_id,
            workspace_id=workspace_id,
            roles=(BusinessRole.LEARNER,),
            status=status,
        )

    def resolve_service_identity(self, **_kwargs):
        return None


def _runtime(directory: _Directory | None = None) -> EnterpriseIdentityRuntime:
    config = EnterpriseIdentityConfig(
        mode=TenantMode.ENTERPRISE_SERVER,
        keycloak=KeycloakOidcConfig(
            issuer=ISSUER,
            client_id="mentorflow-web",
            audience="mentorflow-api",
            redirect_uri=f"{ORIGIN}/api/enterprise/auth/callback",
        ),
        browser_allowed_origins=(ORIGIN,),
    )
    return EnterpriseIdentityRuntime(
        config,
        client=_BearerClient(),  # type: ignore[arg-type]
        directory=directory or _Directory(),
    )


def _headers(identity: str = "a") -> dict[str, str]:
    return {
        "Authorization": f"Bearer token-{identity}",
        "X-MentorFlow-Organization-ID": f"org-{identity}",
        "X-MentorFlow-Workspace-ID": f"workspace-{identity}",
    }


def _goal() -> dict[str, Any]:
    return {
        "goal_statement": "Build a verified learning project",
        "deadline": (date.today() + timedelta(days=7)).isoformat(),
        "success_criteria": ["A focused test passes"],
    }


class _WorkspaceService(EnterpriseWorkspaceService):
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.goal = _goal()

    def create(self, *, actor, tenant, body):
        self.calls.append("workspace.create")
        return {"id": tenant.workspace_id, "title": body.title, "status": "active"}

    def get(self, *, actor, tenant, workspace_id):
        self.calls.append("workspace.read")
        if workspace_id != tenant.workspace_id:
            raise EnterprisePersistenceRequestError(
                "ENTERPRISE_WORKSPACE_NOT_FOUND",
                "Workspace was not found",
                status=404,
            )
        return {
            "id": workspace_id,
            "title": "Course A",
            "status": "active",
            "sources": [],
            "goal": None,
            "plans": [],
        }

    def set_goal(self, *, actor, tenant, workspace_id, body):
        self.calls.append("goal.create")
        self.goal = body.model_dump(mode="json")
        return {"id": "goal-a", "workspace_id": workspace_id, "payload": self.goal}

    def get_goal(self, *, actor, tenant, workspace_id):
        self.calls.append("goal.read")
        return {"id": "goal-a", "workspace_id": workspace_id, "payload": self.goal}

    def patch_goal(self, *, actor, tenant, workspace_id, body):
        self.calls.append("goal.update")
        self.goal.update(body.model_dump(mode="json", exclude_none=True))
        return {"id": "goal-a", "workspace_id": workspace_id, "payload": self.goal}


class _DocumentService(EnterpriseDocumentImportService):
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.owners: dict[str, tuple[str, str]] = {}

    def _require(self, tenant, resource_id: str) -> None:
        if self.owners.get(resource_id) != (
            tenant.organization_id,
            tenant.workspace_id,
        ):
            raise EnterprisePersistenceRequestError(
                "DOCUMENT_SELECTION_NOT_FOUND",
                "Document selection was not found",
                status=404,
            )

    async def select(self, *, actor, tenant, body):
        self.calls.append("document.select")
        assert body.assets[0].local_asset_id == "local-asset-a"
        self.owners["selection-a"] = (tenant.organization_id, tenant.workspace_id)
        return {
            "selection_id": "selection-a",
            "document_ids": ["assetrev-a"],
            "status": "selected",
        }

    def preflight(self, *, actor, tenant, selection_id):
        self.calls.append("document.preflight")
        self._require(tenant, selection_id)
        return {"selection_id": selection_id, "status": "ready", "ready": True}

    def start_batch(self, *, actor, tenant, selection_id, body):
        self.calls.append("document.batch.start")
        self._require(tenant, selection_id)
        self.owners["job-a"] = (tenant.organization_id, tenant.workspace_id)
        return {
            "selection_id": selection_id,
            "batch_id": "job-a",
            "job_id": "job-a",
            "status": "PENDING",
        }

    def progress(self, *, actor, tenant, batch_id):
        self.calls.append("document.batch.progress")
        self._require(tenant, batch_id)
        return {"batch_id": batch_id, "status": "PENDING"}

    def result(self, *, actor, tenant, batch_id):
        self.calls.append("document.batch.result")
        self._require(tenant, batch_id)
        return None


class _TeachingService(EnterpriseTeachingService):
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.owners: dict[str, tuple[str, str]] = {}

    def start(self, *, actor, tenant, workspace_id, body):
        self.calls.append("teaching.session.start")
        if workspace_id != tenant.workspace_id:
            raise EnterprisePersistenceRequestError(
                "ENTERPRISE_WORKSPACE_NOT_FOUND", "Workspace was not found", status=404
            )
        self.owners["session-a"] = (tenant.organization_id, tenant.workspace_id)
        return {
            "session_id": "session-a",
            "workspace_id": workspace_id,
            "plan_id": body.get("plan_id"),
            "status": "READY",
        }

    async def teach_next(self, *, actor, tenant, session_id, body):
        self.calls.append("teaching.teach-next")
        if self.owners.get(session_id) != (
            tenant.organization_id,
            tenant.workspace_id,
        ):
            raise EnterprisePersistenceRequestError(
                "ENTERPRISE_TEACHING_SESSION_NOT_FOUND",
                "Teaching session was not found",
                status=404,
            )
        return {
            "session_id": session_id,
            "workspace_id": tenant.workspace_id,
            "status": "AWAITING_LEARNER",
        }


def _app(runtime: EnterpriseIdentityRuntime) -> tuple[FastAPI, list[str]]:
    app = FastAPI()
    calls: list[str] = []
    app.state.enterprise_identity_runtime = runtime
    app.state.enterprise_workspace_service = _WorkspaceService(calls)
    app.state.enterprise_document_import_service = _DocumentService(calls)
    app.state.enterprise_teaching_service = _TeachingService(calls)
    install_enterprise_identity(app)

    @app.get("/api/unapproved")
    def unapproved_product():
        calls.append("unapproved")
        return {"unsafe": True}

    @app.middleware("http")
    async def enterprise_boundary(request: Request, call_next):
        request.state.request_id = "product-bridge-request"
        try:
            path = request.url.path
            if path.startswith("/api") and not runtime.is_public_path(path):
                binding = await runtime.authenticate_request(request)
                request.state.enterprise_identity = binding
                request.state.actor_context = (
                    binding.context.actor if binding.context is not None else None
                )
                request.state.tenant_context = (
                    binding.context.tenant if binding.context is not None else None
                )
                if not enterprise_route_is_tenant_ready(path):
                    raise IdentityError(
                        "ENTERPRISE_ROUTE_NOT_TENANT_READY",
                        "This API route is not yet bound to enterprise tenant persistence",
                        status=503,
                    )
            return await call_next(request)
        except (IdentityError, IdentityContextError) as exc:
            return identity_error_response(request, exc)

    return app, calls


def _asset_selection() -> dict[str, object]:
    return {
        "assets": [
            {
                "local_asset_id": "local-asset-a",
                "filename": "lesson.txt",
                "mime_type": "text/plain",
                "size_bytes": 11,
                "sha256": "a" * 64,
                "import_mode": "MANAGED",
                "portability": "PORTABLE",
            }
        ]
    }


def test_all_enterprise_product_routes_revalidate_and_use_canonical_services() -> None:
    directory = _Directory()
    app, calls = _app(_runtime(directory))
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _headers()
        assert client.post(
            "/api/enterprise/workspaces",
            headers=headers,
            json={"title": "Course A"},
        ).status_code == 200
        assert client.get(
            "/api/enterprise/workspaces/workspace-a", headers=headers
        ).status_code == 200
        assert client.post(
            "/api/enterprise/workspaces/workspace-a/goal",
            headers=headers,
            json=_goal(),
        ).status_code == 200
        assert client.get(
            "/api/enterprise/workspaces/workspace-a/goal", headers=headers
        ).status_code == 200
        assert client.patch(
            "/api/enterprise/workspaces/workspace-a/goal",
            headers=headers,
            json={"minutes_per_day": 45},
        ).status_code == 200
        selected = client.post(
            "/api/enterprise/document-import/selections",
            headers=headers,
            json=_asset_selection(),
        )
        assert selected.status_code == 200
        assert client.post(
            "/api/enterprise/document-import/selections/selection-a/preflight",
            headers=headers,
        ).status_code == 200
        assert client.post(
            "/api/enterprise/document-import/selections/selection-a/batches",
            headers=headers,
            json={"conversion_batch_size": 1},
        ).status_code == 200
        assert client.get(
            "/api/enterprise/document-import/batches/job-a/progress",
            headers=headers,
        ).status_code == 200
        assert client.get(
            "/api/enterprise/document-import/batches/job-a/result",
            headers=headers,
        ).status_code == 200
        assert client.post(
            "/api/enterprise/active-teaching/workspaces/workspace-a/sessions/start",
            headers=headers,
            json={"plan_id": "plan-a"},
        ).status_code == 200
        assert client.post(
            "/api/enterprise/active-teaching/sessions/session-a/teach-next",
            headers=headers,
            json={},
        ).status_code == 200

    assert calls == [
        "workspace.create",
        "workspace.read",
        "goal.create",
        "goal.read",
        "goal.update",
        "document.select",
        "document.preflight",
        "document.batch.start",
        "document.batch.progress",
        "document.batch.result",
        "teaching.session.start",
        "teaching.teach-next",
    ]
    assert directory.resolve_calls == len(calls) * 2


def test_cross_tenant_resources_fail_closed_before_service_side_effect() -> None:
    app, calls = _app(_runtime())
    with TestClient(app, base_url=ORIGIN) as client:
        mismatch = client.get(
            "/api/enterprise/workspaces/workspace-b", headers=_headers("a")
        )
        assert mismatch.status_code == 404
        selected = client.post(
            "/api/enterprise/document-import/selections",
            headers=_headers("a"),
            json=_asset_selection(),
        )
        assert selected.status_code == 200
        cross_selection = client.post(
            "/api/enterprise/document-import/selections/selection-a/preflight",
            headers=_headers("b"),
        )
        assert cross_selection.status_code == 404
        started = client.post(
            "/api/enterprise/active-teaching/workspaces/workspace-a/sessions/start",
            headers=_headers("a"),
            json={"plan_id": "plan-a"},
        )
        assert started.status_code == 200
        cross_session = client.post(
            "/api/enterprise/active-teaching/sessions/session-a/teach-next",
            headers=_headers("b"),
            json={},
        )
        assert cross_session.status_code == 404
    assert "unapproved" not in calls


def test_enterprise_manifest_selection_rejects_raw_uploads_and_forged_ownership() -> None:
    app, calls = _app(_runtime())
    with TestClient(app, base_url=ORIGIN) as client:
        raw_upload = client.post(
            "/api/enterprise/document-import/selections",
            headers=_headers("a"),
            files={"files": ("lesson.txt", b"must-stay-local", "text/plain")},
        )
        forged = _asset_selection()
        forged["organization_id"] = "org-b"
        forged_owner = client.post(
            "/api/enterprise/document-import/selections",
            headers=_headers("a"),
            json=forged,
        )
    assert raw_upload.status_code == 422
    assert forged_owner.status_code == 422
    assert "document.select" not in calls


def test_missing_or_stale_membership_denies_without_product_side_effects() -> None:
    anonymous_app, anonymous_calls = _app(_runtime())
    with TestClient(anonymous_app, base_url=ORIGIN) as client:
        denied = client.get("/api/enterprise/workspaces/workspace-a")
    assert denied.status_code == 401
    assert anonymous_calls == []

    missing_app, missing_calls = _app(_runtime(_Directory(grants=set())))
    with TestClient(missing_app, base_url=ORIGIN) as client:
        denied = client.get(
            "/api/enterprise/workspaces/workspace-a", headers=_headers()
        )
    assert denied.status_code == 403
    assert missing_calls == []

    directory = _Directory(
        status_sequence=[MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED]
    )
    stale_app, stale_calls = _app(_runtime(directory))
    with TestClient(stale_app, base_url=ORIGIN) as client:
        denied = client.get(
            "/api/enterprise/workspaces/workspace-a", headers=_headers()
        )
    assert denied.status_code == 403
    assert stale_calls == []


def test_unapproved_legacy_routes_and_unwired_persistence_fail_closed() -> None:
    app, calls = _app(_runtime())
    with TestClient(app, base_url=ORIGIN) as client:
        denied = client.get("/api/unapproved", headers=_headers())
    assert denied.status_code == 503
    assert calls == []

    runtime = _runtime()
    unwired = FastAPI()
    unwired.state.enterprise_identity_runtime = runtime
    install_enterprise_identity(unwired)

    @unwired.middleware("http")
    async def context(request: Request, call_next):
        binding = await runtime.authenticate_request(request)
        request.state.enterprise_identity = binding
        return await call_next(request)

    with TestClient(unwired, base_url=ORIGIN) as client:
        response = client.get(
            "/api/enterprise/workspaces/workspace-a", headers=_headers()
        )
    assert response.status_code == 503


def test_enterprise_bridge_has_no_legacy_delegate_or_process_local_scope_truth() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "enterprise" / "product_bridge.py").read_text(
        encoding="utf-8"
    )
    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    assert "EnterpriseProductScopeRegistry" not in source
    assert "_delegate" not in source
    assert "orm_workspaces" not in source
    assert "EnterpriseWorkspaceService" in source
    assert "configure_enterprise_orm" in main_source
    assert "document_selection_resolver=(" in main_source
    assert "document_service.resolve_selection_references" in main_source


def test_personal_local_keeps_legacy_no_login_behavior() -> None:
    runtime = EnterpriseIdentityRuntime(EnterpriseIdentityConfig.from_env({}))
    app = FastAPI()
    app.state.enterprise_identity_runtime = runtime
    install_enterprise_identity(app)

    @app.get("/api/local-only")
    def local_only():
        return {"login_required": False}

    with TestClient(app) as client:
        assert client.get("/api/local-only").json() == {"login_required": False}
        enterprise = client.get("/api/enterprise/workspaces/local")
    assert enterprise.status_code == 404
    assert enterprise.json()["error"]["code"] == "ENTERPRISE_IDENTITY_DISABLED"
