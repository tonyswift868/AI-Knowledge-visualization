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


def test_missing_statistics_authority_preserves_discovery_readiness():
    import asyncio
    from app.observability.health import HealthRegistry
    registry = HealthRegistry()
    registry.started = True
    registry.register('keycloak_discovery', lambda: (True, 'KEYCLOAK_DISCOVERY_OK'))
    registry.register('email', lambda: (False, 'EMAIL_AUTHORITY_NOT_CONFIGURED'), required=False)
    result = asyncio.run(AdminOverviewService(Engine(), 'issuer', registry=registry).collect())
    assert result['services']['keycloak'] == dict(status='healthy', code='KEYCLOAK_DISCOVERY_OK')
    assert result['services']['email']['code'] == 'EMAIL_AUTHORITY_NOT_CONFIGURED'
    assert result['identity_statistics'] == dict(status='unavailable', code='ADMIN_READER_NOT_CONFIGURED')
    assert result['registered_human_users'] is None
    assert result['data_status'] == 'unavailable'


def test_refresh_observes_registration_and_verification_without_cache():
    import asyncio
    values = []
    service = AdminOverviewService(Engine(), 'issuer', Provider(values))
    async def scenario():
        assert (await service.collect())['registered_human_users'] == 0
        values.append(dict(id='new-disposable', createdTimestamp=int(datetime.now(timezone.utc).timestamp()*1000),
                           enabled=True, emailVerified=False, requiredActions=[]))
        result = await service.collect()
        assert result['registered_human_users'] == 1 and result['email_unverified_users'] == 1
        values[0]['emailVerified'] = True
        result = await service.collect()
        assert result['email_verified_users'] == 1 and result['email_unverified_users'] == 0
    asyncio.run(scenario())


def test_health_and_identity_share_deadline_in_parallel():
    import asyncio
    import threading
    observed = threading.Event()
    class Reader:
        async def users(self):
            observed.set()
            return []
    service = AdminOverviewService(Engine(), 'issuer', Reader())
    def health():
        assert observed.wait(1), 'identity read must run while health is in progress'
        return {'email': dict(status='healthy', code='EMAIL_TLS_AUTH_READY')}
    service._health = health
    result = asyncio.run(service.collect())
    assert result['services']['email']['status'] == 'healthy'


def test_overview_config_supports_existing_mtls_references(tmp_path, monkeypatch):
    import json
    import pytest
    from app.enterprise.identity import admin_overview as module
    loaded = []
    context = SimpleNamespace(load_cert_chain=lambda cert, key: loaded.append((cert, key)))
    monkeypatch.setattr(module.ssl, 'create_default_context', lambda **_: context)
    monkeypatch.setattr(module, 'ServiceAccountTokenResolver', lambda *a, **kw: object())
    monkeypatch.setattr(module, 'KeycloakAccountAdmin', lambda *a, **kw: object())
    identity = SimpleNamespace(keycloak=SimpleNamespace(_transport_authority='https://identity.invalid/realms/test', canonical_identity_issuer='issuer'))
    config = dict(origin='https://identity.invalid', realm='test', client_id='reader', secret_file='/private/reference', ca_file='/ca',
                  client_cert_file='/client.crt', client_key_file='/client.key')
    path = tmp_path/'overview.json'
    path.write_text(json.dumps(config))
    service = module.from_env(Engine(), identity, None, {'MENTORFLOW_ADMIN_OVERVIEW_CONFIG':str(path)})
    assert service.reader is not None and loaded == [('/client.crt', '/client.key')]
    del config['client_key_file']
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError): module.from_env(Engine(), identity, None, {'MENTORFLOW_ADMIN_OVERVIEW_CONFIG':str(path)})


def test_reuses_approved_admin_connection_read_only_without_owning_it():
    import asyncio
    import pytest
    from app.enterprise.identity import admin_overview as module
    calls = []
    class Admin:
        base = 'https://internal.invalid/admin/realms/test'
        async def _call(self, method, path, **kwargs):
            calls.append((method, path))
            return [], None
        async def aclose(self): raise AssertionError('borrowed connection must not be closed')
    lifecycle = SimpleNamespace(active=True, admin=Admin())
    identity = SimpleNamespace(keycloak=SimpleNamespace(canonical_identity_issuer='canonical', _transport_authority='https://public.invalid/realms/test'))
    service = module.from_env(Engine(), identity, None, {}, lifecycle=lifecycle)
    async def scenario():
        assert (await service.collect())['registered_human_users'] == 0
        assert calls == [('GET', '/users')]
        await service.aclose()
        lifecycle.active = False
        with pytest.raises(ValueError): await service.reader.users()
    asyncio.run(scenario())
    lifecycle.active = True
    lifecycle.admin.base = 'https://internal.invalid/admin/realms/wrong'
    with pytest.raises(ValueError): module.from_env(Engine(), identity, None, {}, lifecycle=lifecycle)
