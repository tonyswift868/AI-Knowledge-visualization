from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.enterprise.contracts_v1 import ActorKind, TenantMode
from app.enterprise.identity.config import EnterpriseIdentityConfig, KeycloakOidcConfig
from app.enterprise.identity.context import IdentityContextError
from app.enterprise.identity.keycloak import KeycloakOidcClient
from app.enterprise.identity.models import (
    AuthenticatedPrincipal,
    BusinessRole,
    MembershipGrant,
    MembershipStatus,
)
from app.enterprise.identity.oidc import IdentityError
from app.enterprise.identity.runtime import (
    CONTEXT_PATH,
    EnterpriseIdentityRuntime,
    identity_error_response,
    install_enterprise_identity,
)


ISSUER = "https://identity.example.test/realms/mentorflow"
ORIGIN = "https://mentorflow.example"


def _principal() -> AuthenticatedPrincipal:
    now = datetime.now(timezone.utc)
    return AuthenticatedPrincipal(
        issuer=ISSUER,
        audiences=("mentorflow-api",),
        token_id="runtime-token-001",
        subject="runtime-user-001",
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(minutes=5),
        authentication_time=now - timedelta(minutes=1),
        authentication_strength="mfa",
        identity_provider_session_id="keycloak-session-runtime-001",
        preferred_username="runtime-user",
        email="runtime-user@example.test",
        display_name="Runtime User",
        client_id="mentorflow-web",
        actor_kind=ActorKind.HUMAN,
    )


class _Oidc:
    async def validate_access_token(self, token: str) -> AuthenticatedPrincipal:
        if token not in {"access-token", "refreshed-access-token", "bearer-token"}:
            raise IdentityError("OIDC_SIGNATURE_INVALID", "OIDC token signature is invalid")
        return _principal().model_copy(
            update={"token_id": f"runtime-{token}", "client_id": "mentorflow-web"}
        )

    async def validate_id_token(
        self,
        token: str,
        *,
        expected_nonce: str,
    ) -> AuthenticatedPrincipal:
        assert token == "id-token"
        assert expected_nonce
        return _principal().model_copy(update={"token_id": "runtime-id-token"})

    async def validate_logout_token(self, _token: str):
        return {
            "jti": "logout-001",
            "sid": "keycloak-session-runtime-001",
        }


class _Directory:
    def __init__(self) -> None:
        self.status = MembershipStatus.ACTIVE
        self.roles = (BusinessRole.LEARNER,)

    def resolve_membership(
        self,
        *,
        issuer: str,
        subject: str,
        organization_id: str,
        workspace_id: str | None,
    ) -> MembershipGrant | None:
        if issuer != ISSUER or subject != "runtime-user-001":
            return None
        if (organization_id, workspace_id) not in {
            ("org-a", "workspace-a"),
            ("org-a", "workspace-b"),
        }:
            return None
        return MembershipGrant(
            membership_id="membership-runtime-001",
            issuer=issuer,
            subject=subject,
            organization_id=organization_id,
            workspace_id=workspace_id,
            roles=self.roles,
            status=self.status,
        )

    def resolve_service_identity(self, **_kwargs):
        return None


def _runtime() -> tuple[EnterpriseIdentityRuntime, list[dict[str, str]]]:
    config = EnterpriseIdentityConfig(
        mode=TenantMode.ENTERPRISE_SERVER,
        keycloak=KeycloakOidcConfig(
            issuer=ISSUER,
            client_id="mentorflow-web",
            audience="mentorflow-api",
            redirect_uri=f"{ORIGIN}/api/enterprise/auth/callback",
            post_logout_redirect_uri=f"{ORIGIN}/",
        ),
        browser_allowed_origins=(ORIGIN,),
    )
    forms: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = {
            key: values[0]
            for key, values in parse_qs(request.content.decode("utf-8")).items()
        }
        forms.append(form)
        if request.url.path.endswith("/revoke"):
            return httpx.Response(204)
        if form.get("grant_type") == "refresh_token":
            return httpx.Response(
                200,
                json={
                    "access_token": "refreshed-access-token",
                    "refresh_token": "rotated-refresh-token",
                    "refresh_expires_in": 1200,
                },
            )
        return httpx.Response(
            200,
            json={
                "access_token": "access-token",
                "id_token": "id-token",
                "refresh_token": "server-only-refresh-token",
                "refresh_expires_in": 1200,
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = KeycloakOidcClient(
        config,
        oidc=_Oidc(),
        http_client=http,
    )
    return EnterpriseIdentityRuntime(
        config,
        client=client,
        directory=_Directory(),
    ), forms


def _app(runtime: EnterpriseIdentityRuntime) -> FastAPI:
    app = FastAPI()
    app.state.enterprise_identity_runtime = runtime
    install_enterprise_identity(app)

    @app.middleware("http")
    async def bind_identity(request: Request, call_next):
        request.state.request_id = "runtime-request-001"
        try:
            if request.url.path.startswith("/api") and not runtime.is_public_path(
                request.url.path
            ):
                request.state.enterprise_identity = await runtime.authenticate_request(request)
            return await call_next(request)
        except (IdentityError, IdentityContextError) as exc:
            return identity_error_response(request, exc)

    return app


def _login(client: TestClient) -> tuple[str, str]:
    started = client.get(
        "/api/enterprise/auth/login",
        params={
            "organization_id": "org-a",
            "workspace_id": "workspace-a",
            "return_to": "/dashboard",
        },
        follow_redirects=False,
    )
    assert started.status_code == 302
    query = parse_qs(urlparse(started.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert "code_challenge" in query
    assert "nonce" in query
    assert "state" in query
    return query["state"][0], started.headers["set-cookie"]


def test_bff_login_context_switch_and_logout_are_fenced() -> None:
    runtime, forms = _runtime()
    with TestClient(_app(runtime), base_url=ORIGIN) as client:
        state, transaction_cookie = _login(client)
        assert "HttpOnly" in transaction_cookie
        assert "Secure" in transaction_cookie
        assert "SameSite=lax" in transaction_cookie

        callback = client.get(
            "/api/enterprise/auth/callback",
            params={"code": "one-time-code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/dashboard"
        session_cookie = callback.headers["set-cookie"]
        assert "__Host-mentorflow_enterprise_session=" in session_cookie
        assert "HttpOnly" in session_cookie
        assert "Secure" in session_cookie
        assert "SameSite=lax" in session_cookie
        assert "server-only-refresh-token" not in callback.text
        assert forms[0]["grant_type"] == "authorization_code"
        assert forms[0]["code_verifier"]

        me = client.get("/api/enterprise/auth/me")
        assert me.status_code == 200
        assert me.json()["context"] == {
            "contract_version": "enterprise-context.v1",
            "organization_id": "org-a",
            "workspace_id": "workspace-a",
            "roles": ["LEARNER"],
            "service_permissions": [],
            "context_version": 0,
        }
        old_session_id = client.cookies.get("__Host-mentorflow_enterprise_session")

        switched = client.post(
            CONTEXT_PATH,
            headers={"Origin": ORIGIN},
            json={
                "organization_id": "org-a",
                "workspace_id": "workspace-b",
                "expected_context_version": 0,
            },
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["context"]["workspace_id"] == "workspace-b"
        assert switched.json()["context"]["context_version"] == 1
        assert client.cookies.get("__Host-mentorflow_enterprise_session") != old_session_id
        with pytest.raises(IdentityError) as old_session:
            assert runtime.client is not None
            runtime.client.store.get_public(str(old_session_id))
        assert old_session.value.code == "ENTERPRISE_SESSION_INVALID"

        stale = client.post(
            CONTEXT_PATH,
            headers={"Origin": ORIGIN},
            json={
                "organization_id": "org-a",
                "workspace_id": "workspace-a",
                "expected_context_version": 0,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "ENTERPRISE_CONTEXT_FENCED"

        logged_out = client.post(
            "/api/enterprise/auth/logout",
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        )
        assert logged_out.status_code == 303
        assert logged_out.headers["location"].startswith(
            f"{ISSUER}/protocol/openid-connect/logout?"
        )
        assert "server-only-refresh-token" not in logged_out.headers["location"]


def test_runtime_default_denies_missing_conflicting_and_untrusted_context() -> None:
    runtime, _forms = _runtime()
    app = _app(runtime)
    with TestClient(app, base_url=ORIGIN) as anonymous:
        missing = anonymous.get("/api/enterprise/auth/me")
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "ENTERPRISE_AUTH_REQUIRED"

        state, _cookie = _login(anonymous)
        callback = anonymous.get(
            "/api/enterprise/auth/callback",
            params={"code": "code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        conflict = anonymous.get(
            "/api/enterprise/auth/me",
            headers={"Authorization": "Bearer bearer-token"},
        )
        assert conflict.status_code == 401
        assert conflict.json()["error"]["code"] == "AUTH_BOUNDARY_CONFLICT"
        untrusted = anonymous.get(
            "/api/enterprise/auth/me",
            headers={"X-MentorFlow-Workspace-ID": "workspace-b"},
        )
        assert untrusted.status_code == 409
        assert untrusted.json()["error"]["code"] == "ENTERPRISE_CONTEXT_CONFLICT"
        csrf = anonymous.post(
            CONTEXT_PATH,
            json={
                "organization_id": "org-a",
                "workspace_id": "workspace-b",
                "expected_context_version": 0,
            },
        )
        assert csrf.status_code == 403
        assert csrf.json()["error"]["code"] == "ENTERPRISE_CSRF_REJECTED"


def test_login_state_is_bound_to_the_originating_browser_transaction() -> None:
    runtime, _forms = _runtime()
    app = _app(runtime)
    with TestClient(app, base_url=ORIGIN) as victim:
        state, _cookie = _login(victim)
        with TestClient(app, base_url=ORIGIN) as attacker:
            rejected = attacker.get(
                "/api/enterprise/auth/callback",
                params={"code": "attacker-code", "state": state},
                follow_redirects=False,
            )
            assert rejected.status_code == 401
            assert rejected.json()["error"]["code"] == "OIDC_BROWSER_TRANSACTION_INVALID"
        accepted = victim.get(
            "/api/enterprise/auth/callback",
            params={"code": "victim-code", "state": state},
            follow_redirects=False,
        )
        assert accepted.status_code == 303


def test_rejected_login_consumes_state_and_clears_browser_transaction() -> None:
    runtime, _forms = _runtime()
    with TestClient(_app(runtime), base_url=ORIGIN) as client:
        state, _cookie = _login(client)
        rejected = client.get(
            "/api/enterprise/auth/callback",
            params={"error": "access_denied", "state": state},
            follow_redirects=False,
        )
        assert rejected.status_code == 401
        assert rejected.json()["error"]["code"] == "OIDC_LOGIN_REJECTED"
        assert "Max-Age=0" in rejected.headers["set-cookie"]
        replay = client.get(
            "/api/enterprise/auth/callback",
            params={"code": "replayed-code", "state": state},
            follow_redirects=False,
        )
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "OIDC_STATE_INVALID"


def test_bearer_scope_is_server_resolved_and_revoked_membership_is_denied() -> None:
    runtime, _forms = _runtime()
    directory = runtime.directory
    assert isinstance(directory, _Directory)
    with TestClient(_app(runtime), base_url=ORIGIN) as client:
        headers = {
            "Authorization": "Bearer bearer-token",
            "X-MentorFlow-Organization-ID": "org-a",
            "X-MentorFlow-Workspace-ID": "workspace-a",
        }
        accepted = client.get("/api/enterprise/auth/me", headers=headers)
        assert accepted.status_code == 200
        assert accepted.json()["authentication_mode"] == "BEARER"
        directory.status = MembershipStatus.REVOKED
        revoked = client.get("/api/enterprise/auth/me", headers=headers)
        assert revoked.status_code == 403
        assert revoked.json()["error"]["code"] == "MEMBERSHIP_NOT_ACTIVE"


def test_membership_role_change_and_foreign_context_switch_are_revalidated() -> None:
    runtime, _forms = _runtime()
    directory = runtime.directory
    assert isinstance(directory, _Directory)
    with TestClient(_app(runtime), base_url=ORIGIN) as client:
        state, _cookie = _login(client)
        assert client.get(
            "/api/enterprise/auth/callback",
            params={"code": "code", "state": state},
            follow_redirects=False,
        ).status_code == 303
        directory.roles = (BusinessRole.INSTRUCTOR, BusinessRole.AUDITOR)
        current = client.get("/api/enterprise/auth/me")
        assert current.status_code == 200
        assert current.json()["context"]["roles"] == ["INSTRUCTOR", "AUDITOR"]

        foreign = client.post(
            CONTEXT_PATH,
            headers={"Origin": ORIGIN},
            json={
                "organization_id": "org-b",
                "workspace_id": "workspace-foreign",
                "expected_context_version": 0,
            },
        )
        assert foreign.status_code == 403
        assert foreign.json()["error"]["code"] == "MEMBERSHIP_NOT_ACTIVE"
        still_current = client.get("/api/enterprise/auth/me")
        assert still_current.json()["context"]["organization_id"] == "org-a"
        assert still_current.json()["context"]["workspace_id"] == "workspace-a"


def test_personal_local_config_preserves_no_login_mode() -> None:
    runtime = EnterpriseIdentityRuntime(EnterpriseIdentityConfig.from_env({}))
    with TestClient(_app(runtime), base_url=ORIGIN) as client:
        config = client.get("/api/enterprise/auth/config")
        assert config.status_code == 200
        assert config.json()["mode"] == "PERSONAL_LOCAL"
        assert config.json()["oidc_enabled"] is False
        assert config.json()["login_required"] is False
        login = client.get("/api/enterprise/auth/login", follow_redirects=False)
        assert login.status_code == 404
        assert login.json()["error"]["code"] == "ENTERPRISE_IDENTITY_DISABLED"


def test_main_enterprise_mode_fails_closed_without_lifespan_and_preserves_cors(
    monkeypatch,
) -> None:
    from app import main as main_module

    marker = object()
    previous_runtime = getattr(
        main_module.app.state,
        "enterprise_identity_runtime",
        marker,
    )
    if previous_runtime is not marker:
        delattr(main_module.app.state, "enterprise_identity_runtime")
    assert main_module._configured_origins
    origin = main_module._configured_origins[0]

    async def scenario():
        transport = httpx.ASGITransport(
            app=main_module.app,
            raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://mentorflow.test",
        ) as client:
            denied = await client.get(
                "/api/health",
                headers={"Origin": origin},
            )
            preflight = await client.options(
                CONTEXT_PATH,
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            return denied, preflight

    with monkeypatch.context() as test_state:
        test_state.setenv("MENTORFLOW_DEPLOYMENT_MODE", "ENTERPRISE_SERVER")
        try:
            denied, preflight = asyncio.run(scenario())
        finally:
            if previous_runtime is marker:
                if hasattr(main_module.app.state, "enterprise_identity_runtime"):
                    delattr(main_module.app.state, "enterprise_identity_runtime")
            else:
                main_module.app.state.enterprise_identity_runtime = previous_runtime

    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "ENTERPRISE_IDENTITY_NOT_READY"
    assert denied.headers["access-control-allow-origin"] == origin
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "POST" in preflight.headers["access-control-allow-methods"]
