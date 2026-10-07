"""Offline policy/renewal checks. Never contacts an identity server or real vault."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest
from PySide6.QtWidgets import QApplication

from refactor.desktop.account_protocol import AccountConfig, AccountError, AccountStateClient
from refactor.desktop.native_auth import NativeCredentialClient
from refactor.desktop.account_shell_main import AccountShell
from refactor.server.enterprise.contracts_v1 import TenantMode
from refactor.server.enterprise.identity.config import EnterpriseIdentityConfig, KeycloakOidcConfig, IdentityConfigurationError
from refactor.server.enterprise.identity.keycloak import KeycloakOidcClient, InMemoryEnterpriseSessionStore
from refactor.server.enterprise.identity.models import AuthenticatedPrincipal
from refactor.server.enterprise.identity.oidc import IdentityError
from refactor.server.keycloak import session_policy as policy

ISSUER = 'https://identity.fixture.invalid/realms/mentorflow'


def config(**extra):
    return EnterpriseIdentityConfig(mode=TenantMode.ENTERPRISE_SERVER,
        keycloak=KeycloakOidcConfig(issuer=ISSUER, client_id='mentorflow-desktop',
            audience='mentorflow-api', redirect_uri='https://app.fixture.invalid/callback'), **extra)


def principal():
    now = datetime.now(timezone.utc)
    return AuthenticatedPrincipal(issuer=ISSUER, subject='fixture', audiences=('mentorflow-api',),
        client_id='mentorflow-desktop', token_id='fixture-only', issued_at=now,
        authentication_time=now, authentication_strength='password_verified', expires_at=now + timedelta(minutes=5))


@pytest.mark.parametrize('ttl', [0, 60, 2_147_483_647])
def test_session74_ttl_accepts_zero_and_no_seven_day_ceiling(ttl):
    assert config(session_ttl_seconds=ttl).session_ttl_seconds == ttl


@pytest.mark.parametrize('ttl', [-1, 1, 59])
def test_session74_ttl_invalid_values_still_fail(ttl):
    with pytest.raises(IdentityConfigurationError): config(session_ttl_seconds=ttl)


def test_session74_env_default_and_explicit_zero_are_permanent():
    env = dict(MENTORFLOW_DEPLOYMENT_MODE='ENTERPRISE_SERVER',
        MENTORFLOW_KEYCLOAK_ISSUER=ISSUER, MENTORFLOW_KEYCLOAK_CLIENT_ID='mentorflow-desktop',
        MENTORFLOW_KEYCLOAK_AUDIENCE='mentorflow-api',
        MENTORFLOW_KEYCLOAK_REDIRECT_URI='https://app.fixture.invalid/callback')
    assert EnterpriseIdentityConfig.from_env(env).session_ttl_seconds == 0
    env['MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS'] = '0'
    assert EnterpriseIdentityConfig.from_env(env).session_ttl_seconds == 0
    env['MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS'] = '2147483647'
    assert EnterpriseIdentityConfig.from_env(env).session_ttl_seconds == 2147483647


def test_session74_unlimited_session_survives_idle_and_explicit_revoke_works():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(503))) as http:
            client = KeycloakOidcClient(config(), oidc=NS(), http_client=http)
            session = client._new_session(principal=principal(), refresh_token='fixture-only', refresh_expires_in=0)
            assert session.session_expires_at is None and session.refresh_token_expires_at is None
            future = session.created_at + timedelta(days=365 * 100)
            assert client.store.get_for_refresh(session.session_id, now=future).public == session
            client.store.revoke(session.session_id)
            with pytest.raises(IdentityError): client.store.get(session.session_id, now=future)
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['network', '503', '401'])
def test_session74_oidc_refresh_outage_retains_session_revocation_removes_it(failure):
    async def run():
        def reply(req):
            if failure == 'network': raise httpx.ReadTimeout('fixture timeout')
            return httpx.Response(int(failure), json={'error': 'invalid_grant'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
            client = KeycloakOidcClient(config(), oidc=NS(), http_client=http)
            session = client._new_session(principal=principal(), refresh_token='fixture-only', refresh_expires_in=0)
            with pytest.raises(IdentityError) as exc: await client.refresh_session(session.session_id)
            if failure == '401':
                assert exc.value.code == 'IDENTITY_DISABLED_OR_REVOKED'
                with pytest.raises(IdentityError): client.store.get(session.session_id)
            else:
                assert exc.value.status == 503
                assert client.store.get(session.session_id).refresh_token == 'fixture-only'
    asyncio.run(run())


class Admin:
    def __init__(self):
        self.realm = dict.fromkeys(policy.VALUES, 1800)
        self.realm.update(ssoSessionMaxLifespan=28800, revokeRefreshToken=True,
                          offlineSessionMaxLifespanEnabled=True)
        self.clients = [dict(id='desktop', clientId='mentorflow-desktop', protocol='openid-connect',
                             attributes={'other': 'keep', 'client.session.idle.timeout': '1800'}),
                        dict(id='unrelated', clientId='other', protocol='openid-connect', attributes={'other':'keep'})]
        self.writes = []
        self.fail_client = False

    def call(self, path, method='GET', value=None):
        if method == 'PUT':
            self.writes.append(path)
            if path == policy.REALM: self.realm.update(deepcopy(value))
            else:
                if self.fail_client: raise RuntimeError('interrupted')
                row = next(r for r in self.clients if r['id'] == path.split('/')[-1])
                row.update(deepcopy(value))
            return
        if path == policy.REALM: return deepcopy(self.realm)
        if path.endswith('/clients'): return deepcopy(self.clients)
        return deepcopy(next(r for r in self.clients if r['id'] == path.split('/')[-1]))


def test_session74_admin_dry_run_read_only_apply_repeat_and_rollback(tmp_path):
    admin, backup = Admin(), tmp_path / 'backup.json'
    original = deepcopy((admin.realm, admin.clients))
    result = policy.policy(admin, backup)
    assert result['mode'] == 'dry-run' and not backup.exists() and not admin.writes
    policy.policy(admin, backup, execute=True)
    assert admin.realm == policy.VALUES
    assert admin.clients[0]['attributes']['other'] == 'keep'
    assert admin.clients[1] == original[1][1]
    writes = list(admin.writes)
    policy.policy(admin, backup, execute=True)
    assert admin.writes == writes
    policy.policy(admin, backup, execute=True, rollback=True)
    assert (admin.realm, admin.clients) == original
    assert set(json.loads(backup.read_text())) == {'realm', 'clients'}


def test_session74_admin_partial_apply_can_rollback(tmp_path):
    admin, backup = Admin(), tmp_path / 'backup.json'
    original = deepcopy((admin.realm, admin.clients))
    admin.fail_client = True
    with pytest.raises(RuntimeError): policy.policy(admin, backup, execute=True)
    assert backup.exists()
    admin.fail_client = False
    policy.policy(admin, backup, execute=True, rollback=True)
    assert (admin.realm, admin.clients) == original


def test_session74_kcadm_uses_existing_admin_cli_without_logging_secrets(monkeypatch):
    calls = []
    def run(command, **kw):
        calls.append((command, kw))
        return NS(returncode=0, stdout='{}')
    monkeypatch.setattr(policy.subprocess, 'run', run)
    client = policy.KcadmClient('fixture-keycloak')
    client.call(policy.REALM)
    client.call(policy.REALM + '/clients/desktop', 'PUT', {'attributes': policy.ATTRIBUTES})
    assert calls[0][0][-2:] == ['get', policy.REALM]
    assert calls[1][0][-6:] == ['clients/desktop', '-r', 'mentorflow', '-n', '-f', '-']
    assert json.loads(calls[1][1]['input']) == {'attributes': policy.ATTRIBUTES}


@pytest.mark.parametrize('status', [403, 500, 503])
def test_session74_native_refresh_non401_cannot_clear_login(status):
    async def run():
        cfg = AccountConfig(ISSUER, 'mentorflow-desktop', 'mentorflow-api', 'https://api.fixture.invalid')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
            httpx.Response(status, json={'code': 'ENTERPRISE_SESSION_REAUTH_REQUIRED'}))) as http:
            client = NativeCredentialClient(cfg, http_client=http)
            with pytest.raises(AccountError) as exc:
                await client._session_request('refresh', {'refresh_token':'synthetic'})
            assert exc.value.code == 'NATIVE_AUTH_UNAVAILABLE'
    asyncio.run(run())


def test_session74_native_refresh_explicit401_requires_login():
    async def run():
        cfg = AccountConfig(ISSUER, 'mentorflow-desktop', 'mentorflow-api', 'https://api.fixture.invalid')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401))) as http:
            client = NativeCredentialClient(cfg, http_client=http)
            with pytest.raises(AccountError) as exc:
                await client._session_request('refresh', {'refresh_token':'synthetic'})
            assert exc.value.code == 'ENTERPRISE_SESSION_REAUTH_REQUIRED'
    asyncio.run(run())


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def test_session74_startup_retry_keeps_profile_and_is_generation_fenced(qapp, monkeypatch):
    from PySide6.QtCore import QTimer
    from refactor.desktop.login_shell import LoginGateWindow
    callbacks = []
    monkeypatch.setattr(QTimer, 'singleShot', lambda delay, callback: callbacks.append((delay, callback)))
    profile = object()
    shell = NS(_generation=4, _closing=False, _remembered_profile=profile,
               gate=LoginGateWindow(), _begin_remembered_login=Mock())
    try:
        AccountShell._background_failed(shell, '4:restore_retry', 'temporary')
        assert shell._remembered_profile is profile and callbacks[0][0] == 30_000
        callbacks[0][1]()
        shell._begin_remembered_login.assert_called_once()
        shell._generation = 5
        callbacks[0][1]()
        shell._begin_remembered_login.assert_called_once()
    finally:
        shell.gate.deleteLater()
