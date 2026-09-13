"""Synthetic Desktop behavior tests, not live Keycloak/server qualification."""
from __future__ import annotations


def test_business_window_import_stays_behind_authenticated_show_main():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / 'main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    application = next(node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'MentorFlowApplication')
    import_owners = [method.name for method in application.body
        if isinstance(method, ast.FunctionDef)
        for node in ast.walk(method)
        if isinstance(node, ast.ImportFrom) and node.module == 'main_window']
    assert import_owners == ['_show_main']

import asyncio
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtCore import Signal, QObject
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from PySide6.QtTest import QTest

from desktop.account_identity import AccountIdentity, AccountPreferenceStore
from desktop.enterprise_oidc import DesktopOidcConfig, EnterpriseOidcClient, EnterpriseOidcError, EnterpriseOidcLoginResult, ValidatedTokenIdentity
from desktop.login_session import LoginSession
from desktop.in_app_oidc import InAppOidcLoginDialog as RealOidcPane

ISSUER = 'https://identity.example.test/realms/mentorflow'
API = 'https://api.example.test'


class Settings:
    def __init__(self): self.values = {}
    def value(self, key, default=None): return self.values.get(key, default)
    def setValue(self, key, value): self.values[key] = value
    def remove(self, key): self.values.pop(key, None)
    def sync(self): pass


class Credentials:
    def __init__(self): self.values = {}; self.deleted = []
    @staticmethod
    def key(profile): return profile.issuer_hash, profile.subject_hash
    def store_oauth_tokens(self, identity, *, access_token, refresh_token):
        self.values[self.key(identity.profile())] = (access_token, refresh_token)
        return {}
    def load_oauth_tokens(self, profile): return self.values[self.key(profile)]
    def logout(self, profile):
        self.deleted.append(self.key(profile)); self.values.pop(self.key(profile), None)


def login(subject='a'):
    return EnterpriseOidcLoginResult(
        identity=AccountIdentity(ISSUER, subject, 'Synthetic ' + subject, 'org-' + subject, 'ws-' + subject),
        access_token='synthetic-access-' + subject, refresh_token='synthetic-refresh-' + subject,
        server_state={'authenticated': True, 'context': {'organization_id': 'org-' + subject, 'workspace_id': 'ws-' + subject}},
    )


def test_no_remember_writes_no_credentials_and_preserves_existing_accounts():
    settings, credentials = Settings(), Credentials()
    for subject in ('a', 'b'):
        result = login(subject)
        credentials.store_oauth_tokens(result.identity, access_token='existing-' + subject, refresh_token='existing-refresh-' + subject)
    before = dict(credentials.values)
    session = LoginSession(AccountPreferenceStore(settings), credentials)
    assert session.remembered() == (None, False)
    session.accept(login(), remember=False, automatic=True)
    assert credentials.values == before
    assert credentials.deleted == []
    reopened = LoginSession(AccountPreferenceStore(settings), credentials)
    assert reopened.remembered() == (None, False)
    assert 'synthetic-access' not in json.dumps(settings.values)
    assert 'synthetic-refresh' not in json.dumps(settings.values)


def test_remember_then_restart_uses_real_refresh_path(monkeypatch):
    settings, credentials = Settings(), Credentials()
    session = LoginSession(AccountPreferenceStore(settings), credentials)
    result = login()
    session.accept(result, remember=True, automatic=False)
    reopened = LoginSession(AccountPreferenceStore(settings), credentials)
    profile, automatic = reopened.remembered()
    assert profile == result.identity.profile() and not automatic
    calls = []
    def restore(target, vault, *, refresh_session):
        calls.append((target, vault.load_oauth_tokens(target), refresh_session))
        return result
    monkeypatch.setattr('desktop.login_session.restore_enterprise_account_from_env', restore)
    assert reopened.restore(profile, threading.Event()) is result
    assert calls == [(profile, ('synthetic-access-a', 'synthetic-refresh-a'), True)]


def test_refresh_transactions_serialize_and_rotate_without_overwrite(monkeypatch):
    settings, credentials = Settings(), Credentials()
    session = LoginSession(AccountPreferenceStore(settings), credentials)
    result = login()
    session.accept(result, remember=True, automatic=True)
    observed, entered, release = [], threading.Event(), threading.Event()
    def restore(profile, vault, *, refresh_session):
        _, old = vault.load_oauth_tokens(profile)
        observed.append(old)
        entered.set()
        assert release.wait(3)
        vault.store_oauth_tokens(result.identity, access_token='rotated', refresh_token=old + '-next')
        return result
    monkeypatch.setattr('desktop.login_session.restore_enterprise_account_from_env', restore)
    errors = []
    def work():
        try: session.restore(result.identity.profile(), threading.Event())
        except Exception as exc: errors.append(exc)
    first, second = threading.Thread(target=work), threading.Thread(target=work)
    first.start(); assert entered.wait(3); second.start(); release.set()
    first.join(4); second.join(4)
    assert not errors and not first.is_alive() and not second.is_alive()
    assert observed == ['synthetic-refresh-a', 'synthetic-refresh-a-next']
    assert credentials.load_oauth_tokens(result.identity.profile())[1] == 'synthetic-refresh-a-next-next'


def test_expired_credentials_only_clear_current_namespace(monkeypatch):
    credentials, settings = Credentials(), Settings()
    session = LoginSession(AccountPreferenceStore(settings), credentials)
    for subject in ('a', 'b'):
        session.accept(login(subject), remember=True, automatic=True)
    def expired(*args, **kwargs): raise EnterpriseOidcError('ENTERPRISE_SESSION_REAUTH_REQUIRED', 'expired')
    monkeypatch.setattr('desktop.login_session.restore_enterprise_account_from_env', expired)
    with pytest.raises(EnterpriseOidcError): session.restore(login().identity.profile(), threading.Event())
    assert credentials.deleted == [credentials.key(login().identity.profile())]
    assert credentials.key(login('b').identity.profile()) in credentials.values


class Pane(QWidget):
    authenticated = Signal(object)
    authentication_failed = Signal(str, str)
    def __init__(self, config, *, parent, embedded):
        super().__init__(parent)
        assert embedded
        self.started = False
        self.disposed = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Synthetic provider credential form'))
    def start_embedded(self): self.started = True
    def dispose(self): self.disposed = True; self.hide()


@pytest.fixture
def app_context(monkeypatch, tmp_path):
    import desktop.main as main
    from desktop.runtime import clear_enterprise_account_session
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(application, '_mentorflow_updater', None, raising=False)
    settings = Settings()
    for name, value in {
        'MENTORFLOW_DESKTOP_OIDC_ISSUER': ISSUER, 'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID': 'mentorflow-desktop',
        'MENTORFLOW_DESKTOP_OIDC_AUDIENCE': 'mentorflow-api', 'MENTORFLOW_ENTERPRISE_API_BASE_URL': API,
        'MENTORFLOW_ACCOUNT_VAULT_ROOT': str(tmp_path / 'isolated'),
        'MENTORFLOW_USER_DATA_ROOT': str(tmp_path / 'isolated/prelaunch'),
        'MENTORFLOW_DB': str(tmp_path / 'isolated/prelaunch/data/app.db'),
        'MENTORFLOW_ORM_DB': str(tmp_path / 'isolated/prelaunch/data/mentorflow_v2.db'),
    }.items(): monkeypatch.setenv(name, value)
    monkeypatch.delenv('MENTORFLOW_DESKTOP_REQUIRE_INTERACTIVE_ONCE', raising=False)
    monkeypatch.delenv('MENTORFLOW_DESKTOP_SMOKE_SECONDS', raising=False)
    monkeypatch.setattr(main, 'QSettings', lambda *args: settings)
    monkeypatch.setattr('desktop.in_app_oidc.InAppOidcLoginDialog', Pane)
    target = main.MentorFlowApplication()
    target._login_session = LoginSession(AccountPreferenceStore(settings), Credentials())
    yield target, application, settings
    target._shutdown()
    if target.login_gate is not None: target.login_gate.close()
    updater = getattr(application, '_mentorflow_updater', None)
    if updater is not None:
        updater.shutdown()
    clear_enterprise_account_session()
    application.processEvents()


def test_first_screen_starts_native_credentials_without_second_auth_window(app_context):
    target, qt, _ = app_context
    target._show_login_gate(); qt.processEvents()
    assert target._auth_pane is None
    assert target.login_gate.username_field.isVisible()
    assert target.login_gate.password_field.isVisible()
    assert target.login_gate.primary_button.isVisible()
    assert target.main is None and target.service is None
    assert not target.login_gate.remember_box.isChecked()
    assert target.login_gate.auto_box.isEnabled()
    assert not target.login_gate.remember_box.isVisible()
    target.login_gate.remember_box.setChecked(True)
    assert target.login_gate.auto_box.isEnabled()
    target.login_gate.auto_box.setChecked(True)
    target.login_gate.remember_box.setChecked(False)
    assert not target.login_gate.auto_box.isChecked()


def test_remembered_restart_shows_account_and_waits_for_login_click(app_context, monkeypatch):
    target, qt, _ = app_context
    target._login_session.accept(login(), remember=True, automatic=False)
    target._show_login_gate(); qt.processEvents()
    assert target._auth_pane is None
    assert 'Synthetic a' == target.login_gate.state_title.text()
    assert target.login_gate.primary_button.text() == '登录'
    assert not target.login_gate.auto_box.isChecked()
    called = []
    monkeypatch.setattr(target, '_begin_remembered_login', lambda: called.append('refresh'))
    target.login_gate.primary_button.click()
    assert called == ['refresh']


def test_auto_login_can_cancel_and_late_a_result_cannot_enter_b(app_context, monkeypatch):
    target, qt, _ = app_context
    target._login_session.accept(login(), remember=True, automatic=True)
    release = threading.Event()
    monkeypatch.setattr(target._login_session, 'restore', lambda *args: (release.wait(3), login())[1])
    target._show_login_gate(); qt.processEvents()
    old_generation = target._login_generation
    target.login_gate.cancel_button.click()
    assert target._auth_pane is None and target._remembered_profile is None
    assert target.login_gate.password_field.isEnabled()
    assert not target.login_gate.auto_box.isChecked()
    target._background_succeeded(f'{old_generation}:restore', login())
    assert target._last_login_result is None
    assert target.main is None
    release.set()


def test_expired_session_returns_to_original_gate_form(app_context):
    target, qt, settings = app_context
    target._login_session.accept(login(), remember=True, automatic=False)
    target._show_login_gate(); qt.processEvents()
    gate = target.login_gate
    target._background_failed(f'{target._login_generation}:restore_expired', 'expired')
    assert target.login_gate is gate and target._auth_pane is None
    assert gate.username_field.isVisible() and gate.password_field.isEnabled()
    assert target._login_session.preferences.login_options() == (False, False)
    assert target._remembered_profile is None


def test_late_bootstrap_closes_its_own_resources_without_binding_paths(app_context, monkeypatch):
    target, qt, _ = app_context
    target._show_login_gate(); qt.processEvents()
    closed, bound = [], []
    monkeypatch.setattr('desktop.runtime.bind_desktop_profile_data_root', lambda p: bound.append(p))
    stale = SimpleNamespace(close=lambda: closed.append(True))
    target._background_succeeded(f'{target._login_generation - 1}:bootstrap', stale)
    assert closed == [True] and bound == [] and target.bootstrap_result is None


@pytest.mark.parametrize('reason', ['switch', 'logout'])
def test_account_switch_reuses_gate_only_after_a_is_quiescent(app_context, monkeypatch, reason):
    target, qt, _ = app_context
    target._show_login_gate(); qt.processEvents()
    gate, window_id = target.login_gate, int(target.login_gate.winId())
    events, released = [], threading.Event()
    class OldMain(QWidget):
        def __init__(self):
            super().__init__()
            self.account_profile = login().identity.profile()
            self._account_switch_in_progress = False
            self.account_vault_manager = SimpleNamespace(unmount=lambda: events.append('unmount'))
        def _close_current_account_handles(self): events.append('handles')
        def _clear_account_ui_and_runtime_cache(self): events.append('cache')
    class Service:
        handles_closed = False
        def stop(self):
            assert released.wait(3)
            self.handles_closed = True
            events.append('service-dead')
    old_main, old_service = OldMain(), Service()
    target.main, target.service = old_main, old_service
    target._last_login_result = login()
    target._login_session.accept(login(), remember=True, automatic=True)
    monkeypatch.setattr(target._login_session, 'logout', lambda *a, **k: events.append('logout') or False)
    target._return_to_login_gate(reason)
    qt.processEvents()
    assert target._auth_pane is None
    assert target.main is old_main
    assert not old_main.isEnabled()
    released.set()
    for _ in range(150):
        QTest.qWait(10)
        if target.main is None: break
    assert target.login_gate is gate and int(gate.winId()) == window_id
    assert target._auth_pane is None and gate.password_field.isEnabled()
    assert target.main is None and target.service is None
    assert events.index('service-dead') < events.index('unmount')
    assert ('logout' in events) is (reason == 'logout')
    assert target._login_session.preferences.login_options() == (False, False)
    if reason == 'logout': assert '远端会话注销未确认' in gate.notice_label.text()


def test_closing_gate_during_native_login_discards_late_result(app_context, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def authenticate(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return login()
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env', authenticate)
    target, qt, _ = app_context
    target._show_login_gate(); qt.processEvents()
    target.login_gate.username_field.setText('known')
    target.login_gate.password_field.setText('synthetic-test-only')
    target.login_gate.primary_button.click()
    assert entered.wait(3)
    target.login_gate.close()
    assert target._auth_pane is None
    release.set()
    QTest.qWait(100)
    assert target.main is None and target._last_login_result is None
    assert target.login_gate.password_field.text() == ''


def test_delayed_restored_accept_never_overwrites_a_newer_rotation():
    credentials = Credentials()
    session = LoginSession(AccountPreferenceStore(Settings()), credentials)
    stale = replace(login(), restored=True)
    credentials.store_oauth_tokens(stale.identity, access_token='newest-access', refresh_token='newest-refresh')
    session.accept(stale, remember=True, automatic=True)
    assert credentials.load_oauth_tokens(stale.identity.profile()) == ('newest-access', 'newest-refresh')


def test_changed_issuer_never_receives_saved_credentials():
    result = login()
    class Vault:
        def load_oauth_tokens(self, profile):
            raise AssertionError('Must reject issuer change before even reading Locker')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError('No request allowed')),
        )) as http:
            client = EnterpriseOidcClient(DesktopOidcConfig('https://other.example.test', 'desktop', 'api', API), http_client=http)
            with pytest.raises(EnterpriseOidcError) as failure:
                await client.restore(result.identity.profile(), Vault(), refresh_session=True)
            assert failure.value.code == 'ENTERPRISE_ACCOUNT_ISSUER_CHANGED'
    asyncio.run(run())


def test_stale_account_service_signals_cannot_open_or_fail_the_new_session(app_context, monkeypatch):
    import desktop.main as entry
    target, qt, _ = app_context
    target._show_login_gate(); qt.processEvents()
    target.bootstrap_result = SimpleNamespace(profile=login().identity.profile(), close=lambda: None)
    class Service(QObject):
        ready = Signal(str)
        failed = Signal(str)
        def __init__(self, **kwargs): super().__init__()
        def start(self):
            startup_order.append('service')
            started.set()
        def stop(self): pass
    observed = []
    startup_order = []
    started = threading.Event()
    monkeypatch.setattr(entry, '_prepare_desktop_frontend', lambda: startup_order.append('frontend'))
    monkeypatch.setattr('desktop.runtime.LocalService', Service)
    monkeypatch.setattr(target, '_service_ready', lambda origin: observed.append('ready'))
    monkeypatch.setattr(target, '_service_failed', lambda message: observed.append('failed'))
    target._start_enterprise_local_service()
    assert started.wait(2)
    assert startup_order == ['frontend', 'service']
    previous = target.service
    target._invalidate_login_jobs()
    target.service = Service()
    previous.ready.emit('http://127.0.0.1:1234')
    previous.failed.emit('late old-account failure')
    qt.processEvents()
    assert observed == [] and target.main is None


def workspaces(empty=False, repair=False):
    return {'workspaces': [] if empty else [
        {'id': 'ws-a', 'organization_id': 'org-a', 'title': 'My space', 'status': 'active',
         'initialization_status': 'PROFILE_REQUIRED' if repair else 'READY'}],
        'default_workspace_id': None if empty else 'ws-a', 'initialization_required': empty or repair}


@pytest.mark.parametrize('initial', ['existing', 'empty', 'repair'])
def test_workspace_discovery_initialization_and_selected_metadata_headers(initial):
    calls = []
    def handler(request):
        path = request.url.path
        calls.append((request.method, path))
        if path.startswith('/api/enterprise/account/workspaces'):
            assert 'x-mentorflow-organization-id' not in request.headers
            assert 'x-mentorflow-workspace-id' not in request.headers
            if request.method == 'POST':
                assert json.loads(request.content) == {}
                return httpx.Response(200, json=workspaces())
            return httpx.Response(200, json=workspaces(empty=initial == 'empty', repair=initial == 'repair'))
        assert path == '/api/enterprise/auth/me'
        assert request.headers['x-mentorflow-organization-id'] == 'org-a'
        assert request.headers['x-mentorflow-workspace-id'] == 'ws-a'
        return httpx.Response(200, json={'authenticated': True, 'account': {'subject_id': 'a'},
                                       'context': {'organization_id': 'org-a', 'workspace_id': 'ws-a'}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            config = DesktopOidcConfig(ISSUER, 'mentorflow-desktop', 'mentorflow-api', API,
                                       organization_id='stale-org-b', workspace_id='stale-ws-b')
            client = EnterpriseOidcClient(config, http_client=http)
            return await client._server_state(ValidatedTokenIdentity(ISSUER, 'a', 'A'), 'synthetic')
    identity, _ = asyncio.run(run())
    assert identity.organization_id == 'org-a' and identity.workspace_id == 'ws-a'
    assert calls[0] == ('GET', '/api/enterprise/account/workspaces')
    assert sum(method == 'POST' for method, path in calls) == (initial != 'existing')


@pytest.mark.parametrize('status', [401, 403, 404, 500])
def test_workspace_errors_never_initialize_or_become_empty_success(status):
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(status, json={'workspaces': [], 'initialization_required': True})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = EnterpriseOidcClient(DesktopOidcConfig(ISSUER, 'desktop', 'api', API), http_client=http)
            with pytest.raises(EnterpriseOidcError): await client._discover_workspace('synthetic')
    asyncio.run(run())
    assert calls == ['GET']


def test_explicit_logout_disables_restore_and_never_claims_remote_success_on_failure(monkeypatch):
    session = LoginSession(AccountPreferenceStore(Settings()), Credentials())
    result = login()
    session.accept(result, remember=True, automatic=True)
    async def fail(self, token): return False
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_ISSUER', ISSUER)
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_CLIENT_ID', 'desktop')
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_AUDIENCE', 'api')
    monkeypatch.setenv('MENTORFLOW_ENTERPRISE_API_BASE_URL', API)
    monkeypatch.setattr(EnterpriseOidcClient, 'end_session', fail)
    session.preferences.disable_restore()
    assert session.logout(result.identity.profile(), remote=True, refresh_token=result.refresh_token) is False
    assert session.remembered() == (None, False)
    assert not session.credentials.values


def test_unchecking_remember_on_a_restored_session_removes_only_that_rotated_pair():
    session = LoginSession(AccountPreferenceStore(Settings()), Credentials())
    for subject in ('a', 'b'):
        session.accept(login(subject), remember=True, automatic=True)
    session.accept(replace(login(), restored=True), remember=False, automatic=False)
    assert session.credentials.deleted == [session.credentials.key(login().identity.profile())]
    assert session.credentials.key(login('b').identity.profile()) in session.credentials.values
    assert session.remembered() == (None, False)


def test_legacy_restore_requires_server_mapping_before_vault_rotation():
    credentials = Credentials()
    result = login()
    credentials.store_oauth_tokens(result.identity, access_token='still-valid', refresh_token='old-refresh')
    calls = []
    class Validator:
        async def validate_access(self, token): return ValidatedTokenIdentity(ISSUER, 'a', 'A')
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/.well-known/openid-configuration'):
            return httpx.Response(200, json={'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/protocol/openid-connect/auth',
                'token_endpoint': ISSUER + '/protocol/openid-connect/token', 'jwks_uri': ISSUER + '/protocol/openid-connect/certs'})
        if request.url.path.endswith('/token'):
            assert b'grant_type=refresh_token' in request.content
            assert b'offline_access' not in request.content
            return httpx.Response(200, json={'token_type': 'Bearer', 'access_token': 'new-access', 'refresh_token': 'new-refresh'})
        return httpx.Response(403, json={'detail': 'No space access'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = EnterpriseOidcClient(DesktopOidcConfig(ISSUER, 'desktop', 'api', API), http_client=http, token_validator=Validator())
            with pytest.raises(EnterpriseOidcError, match='学习空间'):
                await client.restore(result.identity.profile(), credentials, refresh_session=True)
    asyncio.run(run())
    assert any(path.endswith('/token') for path in calls)
    # Batch04 stable-identity authority: failed Backend mapping cannot select
    # a canonical Vault namespace, even when the legacy token was rotated.
    assert credentials.load_oauth_tokens(result.identity.profile()) == ('still-valid', 'old-refresh')
