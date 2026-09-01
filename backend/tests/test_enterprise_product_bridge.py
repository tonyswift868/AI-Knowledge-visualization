from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, FastAPI, File, Request, UploadFile
from fastapi.testclient import TestClient

from app.document_import_integration import DocumentBatchStartIn
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
from app.schemas import GoalPatch, GoalProfile, WorkspaceIn


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


def _app(
    runtime: EnterpriseIdentityRuntime,
    *,
    create_workspace_handler: Any | None = None,
) -> tuple[FastAPI, list[str]]:
    app = FastAPI()
    calls: list[str] = []
    app.state.enterprise_identity_runtime = runtime
    install_enterprise_identity(app)
    legacy = APIRouter()

    def create_workspace(body: WorkspaceIn, request: Request):
        calls.append("workspace.create")
        assert request.state.enterprise_identity.context is not None
        return {
            "id": "workspace-a",
            "title": body.title,
            "description": body.description,
            "status": "active",
            "execution_mode": body.execution_mode,
            "provider_session_id_received": body.provider_session_id,
        }

    legacy.add_api_route(
        "/api/workspaces",
        create_workspace_handler or create_workspace,
        methods=["POST"],
        name="create_workspace",
    )

    @legacy.get("/api/workspaces/{workspace_id}", name="get_workspace")
    def get_workspace(workspace_id: str):
        calls.append("workspace.read")
        return {"id": workspace_id, "title": "Course A", "status": "active"}

    @legacy.post("/api/workspaces/{workspace_id}/goal", name="set_goal")
    def set_goal(workspace_id: str, body: GoalProfile):
        calls.append("goal.create")
        return {
            "id": "goal-a",
            "workspace_id": workspace_id,
            "payload": body.model_dump(mode="json"),
        }

    @legacy.get("/api/workspaces/{workspace_id}/goal", name="get_goal")
    def get_goal(workspace_id: str):
        calls.append("goal.read")
        return {"id": "goal-a", "workspace_id": workspace_id, "payload": _goal()}

    @legacy.patch("/api/workspaces/{workspace_id}/goal", name="patch_goal")
    def patch_goal(workspace_id: str, body: GoalPatch):
        calls.append("goal.update")
        return {
            "id": "goal-a",
            "workspace_id": workspace_id,
            "payload": {**_goal(), **body.model_dump(mode="json", exclude_none=True)},
        }

    @legacy.post("/api/document-import/selections", name="select_documents")
    async def select_documents(
        request: Request,
        files: list[UploadFile] = File(...),
    ):
        calls.append("document.select")
        assert request.state.enterprise_identity.context is not None
        names = [file.filename for file in files]
        for file in files:
            await file.close()
        return {"selection_id": "selection-a", "files": names}

    @legacy.post(
        "/api/document-import/selections/{selection_id}/preflight",
        name="preflight_documents",
    )
    def preflight_documents(request: Request, selection_id: str):
        calls.append("document.preflight")
        assert request.state.enterprise_identity.context is not None
        return {"selection_id": selection_id, "status": "READY"}

    @legacy.post(
        "/api/document-import/selections/{selection_id}/batches",
        name="start_document_batch",
    )
    def start_document_batch(
        request: Request,
        selection_id: str,
        body: DocumentBatchStartIn = Body(),
    ):
        calls.append("document.batch.start")
        assert request.state.enterprise_identity.context is not None
        return {
            "batch_id": "batch-a",
            "selection_id": selection_id,
            "conversion_batch_size": body.conversion_batch_size,
        }

    @legacy.get(
        "/api/document-import/batches/{batch_id}/progress",
        name="document_batch_progress",
    )
    def document_batch_progress(request: Request, batch_id: str):
        calls.append("document.batch.progress")
        assert request.state.enterprise_identity.context is not None
        return {"batch_id": batch_id, "status": "RUNNING"}

    @legacy.get(
        "/api/document-import/batches/{batch_id}/result",
        name="document_batch_result",
    )
    def document_batch_result(request: Request, batch_id: str):
        calls.append("document.batch.result")
        assert request.state.enterprise_identity.context is not None
        return {"batch_id": batch_id, "status": "COMPLETED", "documents": []}

    @legacy.post(
        "/api/active-teaching/workspaces/{workspace_id}/sessions/start",
        name="start_session",
    )
    async def start_session(
        workspace_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
    ):
        calls.append("teaching.session.start")
        return {
            "session_id": "session-a",
            "workspace_id": workspace_id,
            "plan_id": body.get("plan_id", "plan-a"),
        }

    @legacy.post(
        "/api/active-teaching/sessions/{session_id}/teach-next",
        name="teach_next",
    )
    async def teach_next(
        session_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
    ):
        calls.append("teaching.teach-next")
        return {"session_id": session_id, "workspace_id": "workspace-a", "state": body}

    @legacy.get("/api/unapproved", name="unapproved_product")
    def unapproved_product():
        calls.append("unapproved")
        return {"unsafe": True}

    app.include_router(legacy)

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


def test_allowed_routes_revalidate_and_delegate_to_existing_product_handlers() -> None:
    directory = _Directory()
    app, calls = _app(_runtime(directory))
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _headers()
        created = client.post(
            "/api/enterprise/workspaces",
            headers=headers,
            json={
                "title": "Course A",
                "provider_session_id": "ps_opaque-session-001",
            },
        )
        assert created.status_code == 200
        assert created.json()["id"] == "workspace-a"
        assert created.json()["provider_session_id_received"] == (
            "ps_opaque-session-001"
        )

        assert client.get(
            "/api/enterprise/workspaces/workspace-a",
            headers=headers,
        ).status_code == 200
        assert client.post(
            "/api/enterprise/workspaces/workspace-a/goal",
            headers=headers,
            json=_goal(),
        ).status_code == 200
        assert client.get(
            "/api/enterprise/workspaces/workspace-a/goal",
            headers=headers,
        ).status_code == 200
        assert client.patch(
            "/api/enterprise/workspaces/workspace-a/goal",
            headers=headers,
            json={"minutes_per_day": 45},
        ).status_code == 200

        selected = client.post(
            "/api/enterprise/document-import/selections",
            headers=headers,
            files={"files": ("lesson.txt", b"tenant-safe material", "text/plain")},
        )
        assert selected.status_code == 200
        assert selected.json()["selection_id"] == "selection-a"
        assert client.post(
            "/api/enterprise/document-import/selections/selection-a/preflight",
            headers=headers,
        ).status_code == 200
        started_batch = client.post(
            "/api/enterprise/document-import/selections/selection-a/batches",
            headers=headers,
            json={"conversion_batch_size": 1},
        )
        assert started_batch.status_code == 200
        assert started_batch.json()["batch_id"] == "batch-a"
        assert client.get(
            "/api/enterprise/document-import/batches/batch-a/progress",
            headers=headers,
        ).status_code == 200
        assert client.get(
            "/api/enterprise/document-import/batches/batch-a/result",
            headers=headers,
        ).status_code == 200

        started_session = client.post(
            "/api/enterprise/active-teaching/workspaces/workspace-a/sessions/start",
            headers=headers,
            json={"plan_id": "plan-a"},
        )
        assert started_session.status_code == 200
        assert started_session.json()["session_id"] == "session-a"
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


def test_enterprise_workspace_create_uses_postgres_before_local_persistence(
    monkeypatch,
) -> None:
    import app.main as main_module
    from app import orm as orm_runtime

    calls = {"postgres": 0, "commit": 0, "sqlite": 0, "local_clock": 0}
    added: list[object] = []

    class _Session:
        def __enter__(self):
            calls["postgres"] += 1
            return self

        def __exit__(self, *_args):
            return False

        def add(self, value: object) -> None:
            added.append(value)

        def commit(self) -> None:
            calls["commit"] += 1

    def _forbidden_sqlite():
        calls["sqlite"] += 1
        raise AssertionError("Enterprise workspace creation touched SQLite")

    def _forbidden_local_clock():
        calls["local_clock"] += 1
        raise AssertionError("Enterprise workspace creation touched the local store")

    monkeypatch.setattr(orm_runtime, "SessionLocal", _Session)
    monkeypatch.setattr(main_module.db, "connection", _forbidden_sqlite)
    monkeypatch.setattr(main_module.db, "now", _forbidden_local_clock)
    monkeypatch.setattr(main_module, "uid", lambda _prefix: "ws-postgres-a")

    app, bridge_calls = _app(
        _runtime(),
        create_workspace_handler=main_module.create_workspace,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        created = client.post(
            "/api/enterprise/workspaces",
            headers=_headers("a"),
            json={"title": "Course A"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["id"] == "ws-postgres-a"

        denied = client.get(
            "/api/enterprise/workspaces/ws-postgres-a",
            headers=_headers("b"),
        )

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ENTERPRISE_PRODUCT_RESOURCE_FORBIDDEN"
    assert bridge_calls == []
    assert calls == {"postgres": 1, "commit": 1, "sqlite": 0, "local_clock": 0}
    assert len(added) == 1
    assert added[0].id == "ws-postgres-a"


def test_anonymous_and_missing_membership_deny_without_product_side_effects() -> None:
    anonymous_app, anonymous_calls = _app(_runtime())
    with TestClient(anonymous_app, base_url=ORIGIN) as client:
        denied = client.get("/api/enterprise/workspaces/workspace-a")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "ENTERPRISE_AUTH_REQUIRED"
    assert anonymous_calls == []

    missing_app, missing_calls = _app(_runtime(_Directory(grants=set())))
    with TestClient(missing_app, base_url=ORIGIN) as client:
        denied = client.get(
            "/api/enterprise/workspaces/workspace-a",
            headers=_headers(),
        )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "MEMBERSHIP_NOT_ACTIVE"
    assert missing_calls == []

    no_workspace_directory = _Directory(
        grants={("user-a", "org-a", None)},
    )
    no_workspace_app, no_workspace_calls = _app(_runtime(no_workspace_directory))
    headers = _headers()
    headers.pop("X-MentorFlow-Workspace-ID")
    with TestClient(no_workspace_app, base_url=ORIGIN) as client:
        denied = client.get(
            "/api/enterprise/workspaces/workspace-a",
            headers=headers,
        )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ENTERPRISE_MEMBERSHIP_REQUIRED"
    assert no_workspace_calls == []


def test_stale_membership_is_revalidated_before_product_invocation() -> None:
    directory = _Directory(
        status_sequence=[MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED]
    )
    app, calls = _app(_runtime(directory))
    with TestClient(app, base_url=ORIGIN) as client:
        denied = client.get(
            "/api/enterprise/workspaces/workspace-a",
            headers=_headers(),
        )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "MEMBERSHIP_NOT_ACTIVE"
    assert directory.resolve_calls == 2
    assert calls == []


def test_route_and_opaque_resource_scopes_deny_cross_tenant_before_delegation() -> None:
    app, calls = _app(_runtime())
    with TestClient(app, base_url=ORIGIN) as client:
        route_mismatch = client.get(
            "/api/enterprise/workspaces/workspace-b",
            headers=_headers("a"),
        )
        assert route_mismatch.status_code == 403
        assert route_mismatch.json()["error"]["code"] == (
            "ENTERPRISE_PRODUCT_RESOURCE_FORBIDDEN"
        )
        assert calls == []

        selected = client.post(
            "/api/enterprise/document-import/selections",
            headers=_headers("a"),
            files={"files": ("lesson.txt", b"material", "text/plain")},
        )
        assert selected.status_code == 200
        cross_selection = client.post(
            "/api/enterprise/document-import/selections/selection-a/preflight",
            headers=_headers("b"),
        )
        assert cross_selection.status_code == 403

        started = client.post(
            "/api/enterprise/active-teaching/workspaces/workspace-a/sessions/start",
            headers=_headers("a"),
            json={},
        )
        assert started.status_code == 200
        cross_session = client.post(
            "/api/enterprise/active-teaching/sessions/session-a/teach-next",
            headers=_headers("b"),
            json={},
        )
        assert cross_session.status_code == 403

    assert calls == ["document.select", "teaching.session.start"]


def test_unapproved_legacy_routes_remain_fail_closed() -> None:
    app, calls = _app(_runtime())
    with TestClient(app, base_url=ORIGIN) as client:
        unapproved = client.get("/api/unapproved", headers=_headers())
        legacy_product = client.post(
            "/api/workspaces",
            headers=_headers(),
            json={"title": "Must not bypass"},
        )
    for response in (unapproved, legacy_product):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == (
            "ENTERPRISE_ROUTE_NOT_TENANT_READY"
        )
    assert calls == []


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
