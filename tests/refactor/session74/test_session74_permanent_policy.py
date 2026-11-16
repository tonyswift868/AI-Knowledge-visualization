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
from refactor.server.keycloak import session_policy as policy

ISSUER = 'https://identity.fixture.invalid/realms/mentorflow'


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
