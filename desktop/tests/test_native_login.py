"""Native form and controlled API contract tests; synthetic identity only."""
import asyncio
import inspect
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from desktop.account_identity import AccountPreferenceStore
from desktop.enterprise_oidc import DesktopOidcConfig, EnterpriseOidcError
from desktop.native_auth import NativeCredentialClient, PREFIX
from desktop.login_session import LoginSession
from desktop.login_shell import LoginGateState
from desktop.tests.test_login_smooth import app_context, Settings, Credentials, login, ISSUER, API, workspaces


def pair(subject='a', suffix='a'):
    return {'schema': 'mentorflow.native-session.v1', 'access_token': 'mfn1_a_' + suffix * 43,
        'refresh_token': 'mfn1_r_' + suffix * 43, 'expires_in': 299,
        'identity': {'issuer': ISSUER, 'subject': subject, 'display_name': 'Synthetic ' + subject}}


def client_for(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return NativeCredentialClient(DesktopOidcConfig(ISSUER, 'mentorflow-desktop', 'mentorflow-api', API), http_client=http), http


def test_native_form_masking_clear_no_persistence_or_browser(app_context, monkeypatch):
    target, qt, settings = app_context
    def forbidden(*args, **kwargs): raise AssertionError('Browser is not a native login dependency')
    monkeypatch.setattr('PySide6.QtGui.QDesktopServices.openUrl', forbidden)
    monkeypatch.setattr('desktop.in_app_oidc.InAppOidcLoginDialog', forbidden)
    entered, release = threading.Event(), threading.Event()
    def auth(username, password, **kwargs):
        assert username == 'known' and password == 'synthetic-test-only'
        entered.set(); assert release.wait(3)
        raise EnterpriseOidcError('NATIVE_CREDENTIALS_INVALID', 'untrusted backend detail')
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env', auth)
    target._show_login_gate(); qt.processEvents()
    gate = target.login_gate
    assert gate.password_field.echoMode() == QLineEdit.EchoMode.Password
    gate.show_password.setChecked(True)
    assert gate.password_field.echoMode() == QLineEdit.EchoMode.Normal
    gate.username_field.setText('known'); gate.password_field.setText('synthetic-test-only')
    gate.primary_button.click()
    assert entered.wait(2)
    assert gate.password_field.text() == ''
    assert gate.password_field.echoMode() == QLineEdit.EchoMode.Password
    assert 'synthetic-test-only' not in json.dumps(settings.values)
    assert not target._login_session.credentials.values
    assert target.main is None and target._auth_pane is None
    release.set()
    for _ in range(100):
        QTest.qWait(10)
        if gate.state == LoginGateState.AUTH_FAILED: break
    assert gate.state_detail.text() == '账号或密码不正确'
    assert gate.password_field.isEnabled() and target.main is None
    assert not gate.register_button.isEnabled() and not gate.forgot_password_button.isEnabled()


def test_success_enters_bootstrap_and_failure_cannot_open_main(app_context, monkeypatch):
    target, qt, _ = app_context
    target._show_login_gate(); qt.processEvents()
    entered, release = threading.Event(), threading.Event()
    def auth(*args, on_authenticated, **kwargs):
        on_authenticated()
        entered.set(); assert release.wait(3)
        return login()
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env', auth)
    def failed_bootstrap(result):
        raise RuntimeError('synthetic workspace failure')
    monkeypatch.setattr('desktop.startup_flow.bootstrap_enterprise_account', failed_bootstrap)
    gate = target.login_gate
    gate.username_field.setText('known'); gate.password_field.setText('synthetic-test-only')
    gate.primary_button.click(); assert entered.wait(2)
    QTest.qWait(30)
    assert gate.state == LoginGateState.AUTH_SUCCESS_BOOTSTRAPPING
    assert target.main is None and target.service is None
    release.set()
    for _ in range(100):
        QTest.qWait(10)
        if gate.state == LoginGateState.AUTH_FAILED: break
    assert gate.state == LoginGateState.AUTH_FAILED and gate._retry_mode
    assert target.main is None and target.bootstrap_result is None
    target.api = SimpleNamespace(close=lambda: None)
    target._show_main({'username': 'cannot-open'})
    assert target.main is None


@pytest.mark.parametrize('status', [401, 503, 'network'])
def test_client_wrong_unknown_and_network_errors_are_safe(status):
    async def run():
        def handler(request):
            if status == 'network': raise httpx.ConnectError('secret echo forbidden', request=request)
            return httpx.Response(status, json={'code': 'ACCOUNT_DOES_NOT_EXIST', 'message': 'private internal detail'})
        client, http = client_for(handler)
        async with http:
            errors = []
            for username in ('known', 'unknown'):
                with pytest.raises(EnterpriseOidcError) as error: await client.login(username, 'synthetic-test-only')
                errors.append(str(error.value))
            assert errors[0] == errors[1]
            if status == 401: assert errors[0] == '账号或密码不正确'
            assert all('private' not in text and 'secret' not in text for text in errors)
    asyncio.run(run())


def test_client_success_verifies_workspace_and_me_on_api_authority():
    paths, milestones = [], []
    def handler(request):
        assert request.url.host == 'api.example.test'
        paths.append(request.url.path)
        if request.url.path == PREFIX + '/login':
            assert json.loads(request.content) == {'username': 'known', 'password': 'synthetic-test-only'}
            return httpx.Response(200, json=pair())
        assert request.headers['authorization'] == 'Bearer ' + pair()['access_token']
        if request.url.path.endswith('/workspaces'):
            assert milestones == ['AUTH_SUCCESS_BOOTSTRAPPING']
            return httpx.Response(200, json=workspaces())
        assert request.headers['x-mentorflow-workspace-id'] == 'ws-a'
        return httpx.Response(200, json={'authenticated': True, 'account': {'subject_id': 'a'},
            'context': {'organization_id': 'org-a', 'workspace_id': 'ws-a'}})
    async def run():
        client, http = client_for(handler)
        async with http:
            return await client.login('known', 'synthetic-test-only',
                on_authenticated=lambda: milestones.append('AUTH_SUCCESS_BOOTSTRAPPING'))
    result = asyncio.run(run())
    assert result.identity.issuer == ISSUER and result.identity.workspace_id == 'ws-a'
    assert paths == [PREFIX + '/login', '/api/enterprise/account/workspaces', '/api/enterprise/auth/me']
    assert 'synthetic-test-only' not in repr(result)


@pytest.mark.parametrize('failure', ['none', 'workspace', 'expired', 'other-account'])
def test_native_restore_rotates_secure_material_before_workspace(failure):
    vault = Credentials()
    profile = login().identity.profile()
    vault.store_oauth_tokens(login().identity, access_token=pair()['access_token'], refresh_token=pair()['refresh_token'])
    def handler(request):
        if request.url.path == PREFIX + '/refresh':
            assert json.loads(request.content) == {'refresh_token': pair()['refresh_token']}
            return httpx.Response(401 if failure == 'expired' else 200,
                json=pair('b' if failure == 'other-account' else 'a', 'b'))
        assert vault.load_oauth_tokens(profile) == (pair(suffix='b')['access_token'], pair(suffix='b')['refresh_token'])
        if request.url.path.endswith('/workspaces'):
            return httpx.Response(503 if failure == 'workspace' else 200, json=workspaces())
        return httpx.Response(200, json={'authenticated': True, 'account': {'subject_id': 'a'},
            'context': {'organization_id': 'org-a', 'workspace_id': 'ws-a'}})
    async def run():
        client, http = client_for(handler)
        async with http:
            if failure == 'none':
                assert (await client.restore(profile, vault)).restored
            else:
                with pytest.raises(EnterpriseOidcError): await client.restore(profile, vault)
    asyncio.run(run())
    assert vault.load_oauth_tokens(profile)[1] == pair(suffix='b' if failure in {'none', 'workspace'} else 'a')['refresh_token']


def test_auto_login_needs_saved_secure_material():
    settings, vault = Settings(), Credentials()
    preferences = AccountPreferenceStore(settings)
    preferences.save_target(login().identity.profile(), remember_me=True, auto_login=True)
    assert LoginSession(preferences, vault).remembered() == (None, False)


def test_explicit_native_credentials_never_restore_a_different_remembered_account(app_context, monkeypatch):
    target, qt, _ = app_context
    target._login_session.accept(login('a'), remember=True, automatic=False)
    target._show_login_gate(); qt.processEvents()
    called = threading.Event()
    def native(username, password, **kwargs):
        assert username == 'different-account'
        called.set()
        raise EnterpriseOidcError('NATIVE_CREDENTIALS_INVALID', 'denied')
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env', native)
    monkeypatch.setattr(target, '_begin_remembered_login', lambda: pytest.fail('must not restore account A'))
    target.login_gate.username_field.setText('different-account')
    target.login_gate.password_field.setText('synthetic-test-only')
    target.login_gate.primary_button.click()
    assert called.wait(2)
    QTest.qWait(30)
    assert target._remembered_profile is None and target.main is None


def test_login_session_native_restore_expiry_clears_only_its_secure_namespace(monkeypatch):
    session = LoginSession(AccountPreferenceStore(Settings()), Credentials())
    for subject in ('a', 'b'):
        result = login(subject)
        session.accept(result, remember=True, automatic=True)
        session.credentials.store_oauth_tokens(result.identity,
            access_token=pair()['access_token'], refresh_token=pair()['refresh_token'])
    calls = []
    def expired(profile, credentials):
        calls.append(profile)
        raise EnterpriseOidcError('ENTERPRISE_SESSION_REAUTH_REQUIRED', 'expired')
    monkeypatch.setattr('desktop.native_auth.restore_native_account_from_env', expired)
    monkeypatch.setattr('desktop.login_session.restore_enterprise_account_from_env',
        lambda *a, **k: pytest.fail('Native session must not fall back to legacy refresh'))
    with pytest.raises(EnterpriseOidcError): session.restore(login().identity.profile(), threading.Event())
    assert calls == [login().identity.profile()]
    assert session.credentials.deleted == [session.credentials.key(login().identity.profile())]
    assert session.credentials.key(login('b').identity.profile()) in session.credentials.values


def test_native_logout_uses_controlled_api_and_clears_current_locker(app_context, monkeypatch):
    target, _, _ = app_context
    result = login()
    target._login_session.accept(result, remember=True, automatic=True)
    target._login_session.credentials.store_oauth_tokens(result.identity,
        access_token=pair()['access_token'], refresh_token=pair()['refresh_token'])
    calls = []
    async def logout(self, token):
        calls.append(token)
        return True
    monkeypatch.setattr(NativeCredentialClient, 'end_session', logout)
    assert target._login_session.logout(result.identity.profile(), remote=True)
    assert calls == [pair()['refresh_token']]
    assert not target._login_session.credentials.values


def test_verified_native_result_only_opens_main_after_workspace_and_local_service_ready(app_context, monkeypatch):
    from desktop.startup_flow import AccountBootstrapResult, DeviceBootstrapState
    target, qt, _ = app_context
    target._show_login_gate(); qt.processEvents()
    account = login()
    ready = AccountBootstrapResult(identity=account.identity, profile=account.identity.profile(),
        server_state=account.server_state, snapshot=SimpleNamespace(resources=()),
        vault_manager=SimpleNamespace(unmount=lambda: None), local_asset_lifecycle=None,
        metadata_sync=None, metadata_api=SimpleNamespace(close=lambda: None),
        device_state=DeviceBootstrapState.RETURNING_DEVICE_READY)
    def auth(*args, on_authenticated, **kwargs):
        on_authenticated()
        return account
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env', auth)
    monkeypatch.setattr('desktop.startup_flow.bootstrap_enterprise_account', lambda _: ready)
    monkeypatch.setattr('desktop.runtime.bind_desktop_profile_data_root', lambda _: None)
    started, shown = [], []
    monkeypatch.setattr(target, '_start_enterprise_local_service', lambda: started.append('start'))
    monkeypatch.setattr(target, '_show_main', lambda user: shown.append(user))
    monkeypatch.setattr('desktop.api.DesktopApi', lambda origin: SimpleNamespace(close=lambda: None))
    gate = target.login_gate
    gate.username_field.setText('known'); gate.password_field.setText('synthetic-test-only')
    gate.primary_button.click()
    for _ in range(100):
        QTest.qWait(10)
        if started: break
    assert started == ['start'] and shown == []
    assert gate.state == LoginGateState.AUTH_SUCCESS_BOOTSTRAPPING and target.bootstrap_result is ready
    target._service_ready('http://127.0.0.1:45678')
    assert shown == [ready.main_user]


def test_normal_entry_has_no_browser_dependency():
    from desktop.main import MentorFlowApplication
    from desktop.account_settings_page import AccountSettingsPage
    for method in (MentorFlowApplication._begin_interactive_login, MentorFlowApplication._show_login_gate,
                   AccountSettingsPage._begin_in_app_oauth):
        source = inspect.getsource(method)
        assert 'openUrl' not in source and 'InAppOidcLoginDialog' not in source
        assert 'login_enterprise_account_in_app_from_env' not in source


def test_native_rejects_plain_http_even_with_legacy_loopback_allowance():
    config = SimpleNamespace(api_base_url='http://127.0.0.1:18080')
    with pytest.raises(EnterpriseOidcError, match='安全连接'): NativeCredentialClient(config)


@pytest.mark.parametrize('changes', [{'client_id': 'other-client'}, {'audience': 'other-api'}])
def test_native_session_scope_is_not_widened(changes):
    config = SimpleNamespace(api_base_url=API, client_id='mentorflow-desktop', audience='mentorflow-api')
    for key, value in changes.items(): setattr(config, key, value)
    with pytest.raises(EnterpriseOidcError): NativeCredentialClient(config)
