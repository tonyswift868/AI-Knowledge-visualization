from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.enterprise.identity.admin_overview import AdminOverviewService, router
from app.enterprise.identity.application_roles import ApplicationRole
from app.enterprise.identity.oidc import IdentityError
from app.enterprise.identity.runtime import identity_error_response
from desktop.admin_overview_page import admin_role_visible


class Provider:
    def __init__(self, users):
        self._values = users

    async def users(self):
        return self._values

    async def health(self):
        return "healthy", "fixture"


class Connection:
    def begin(self): return self
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def execute(self, query, *args):
        sql = str(query)
        if "mentorflow_admin_account_counts" in sql:
            return SimpleNamespace(scalar_one=lambda: {"backend_account_count": 2,
                "workspace_member_count": 2, "identity_account_mismatch_count": 0})
        if "organization_memberships" in sql:
            return SimpleNamespace(one=lambda: (2, ["human-a", "human-b"]))
        return SimpleNamespace(scalar_one=lambda: 2)


class Engine:
    def connect(self): return Connection()
    def begin(self): return Connection()


def test_aggregation_excludes_service_accounts_and_reports_windows():
    now = datetime.now(timezone.utc).timestamp() * 1000
    service = AdminOverviewService(Engine(), "issuer", Provider([
        {"id": "human-a", "createdTimestamp": now, "enabled": True,
         "emailVerified": True, "requiredActions": []},
        {"id": "human-b", "createdTimestamp": now - 8 * 86400 * 1000,
         "enabled": False, "emailVerified": False, "requiredActions": ["VERIFY_EMAIL"]},
        {"id": "technical", "serviceAccountClientId": "backend", "enabled": True},
    ]))
    import asyncio
    result = asyncio.run(service.collect())
    assert result["registered_human_users"] == 2
    assert result["registrations_today"] == 1
    assert result["registrations_7d"] == 1
    assert result["registrations_30d"] == 2
    assert result["enabled_users"] == 1 and result["disabled_users"] == 1
    assert result["email_verified_users"] == 1 and result["email_unverified_users"] == 1
    assert result["required_action_users"] == 1
    assert result["identity_account_mismatch_count"] == 0


def _app(role):
    app = FastAPI()
    app.include_router(router)
    class Roles:
        def role_for(self, _actor): return role
    class Runtime:
        application_roles = Roles()
        async def revalidate_product_identity(self, _request):
            return SimpleNamespace(context=SimpleNamespace(actor=object()))
    app.state.enterprise_identity_runtime = Runtime()
    app.state.admin_overview_service = AdminOverviewService(
        Engine(), "issuer", Provider([]))
    app.add_exception_handler(IdentityError, lambda request, exc: identity_error_response(request, exc))
    return app


def test_super_admin_200_member_403(monkeypatch):
    monkeypatch.setattr("app.enterprise.identity.runtime._runtime",
                        lambda request: request.app.state.enterprise_identity_runtime)
    response = TestClient(_app(ApplicationRole.SUPER_ADMIN)).get("/api/enterprise/auth/admin/overview")
    assert response.status_code == 200
    response = TestClient(_app(ApplicationRole.LEARNER)).get("/api/enterprise/auth/admin/overview")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "APPLICATION_SUPER_ADMIN_REQUIRED"


def test_desktop_role_gate_requires_super_admin_and_session_transport():
    assert admin_role_visible({"role": "SUPER_ADMIN"}, object()) is True
    assert admin_role_visible({"role": "LEARNER"}, object()) is False
    assert admin_role_visible({"role": "SUPER_ADMIN"}, None) is False
