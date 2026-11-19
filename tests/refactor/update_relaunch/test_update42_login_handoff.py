"""Offline one-shot update login, native shell and existing updater seams."""
import json
import logging
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget

from refactor.desktop import credential_store as locker
from refactor.desktop.account_identity import AccountIdentity, AccountPreferenceStore, AccountCredentialVault
from refactor.desktop.account_protocol import AccountLoginResult, AccountError
from refactor.desktop.account_shell_main import AccountShell
from refactor.desktop.login_session import LoginSession, UpdateLoginHandoff
from refactor.desktop.updates import DesktopUpdates
from refactor import runtime_logging


@pytest.fixture
def vault(monkeypatch):
    class Backend:
        def __init__(self): self.values = {}
        def get_password(self, service, user): return self.values.get((service, user))
        def set_password(self, service, user, secret): self.values[service, user] = secret
        def delete_password(self, service, user): self.values.pop((service, user), None)
    backend = Backend()
    monkeypatch.setattr(locker, '_get_keyring', lambda: backend)
    return backend


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def result(subject='learner', *, restored=False):
    identity = AccountIdentity('https://fixture.invalid/realm', subject, '测试账号')
    return AccountLoginResult(identity, 'synthetic-access', 'synthetic-refresh',
                              {'role': 'LEARNER'}, restored=restored)


def session_at(tmp_path, remember=False, automatic=False, password=False):
    settings = QSettings(str(tmp_path / 'account.ini'), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    session = LoginSession(AccountPreferenceStore(settings))
    current = result()
    session.accept(current, remember=remember, automatic=automatic,
                   remember_password=password, username='learner', password='synthetic-password' if password else None)
    return session, current


def settings_snapshot(session):
    settings = session.preferences.settings
    settings.sync()
    return {key: settings.value(key) for key in settings.allKeys()}


def restored(profile, credentials):
    assert credentials.load_oauth_tokens(profile)[1] == 'synthetic-refresh'
    value = result(restored=True)
    credentials.store_oauth_tokens(value.identity, access_token=value.access_token, refresh_token='rotated-refresh')
    return AccountLoginResult(value.identity, value.access_token, 'rotated-refresh', value.server_state, restored=True)


def test_update42_handoff_consumed_once_before_refresh(vault):
    profile = result().identity.profile()
    handoff = UpdateLoginHandoff(clock=lambda: 1000)
    handoff.write(profile, 'synthetic-refresh')
    record = json.loads(locker.get_secret(handoff.REFERENCE))
    assert record == dict(account_id=profile.account_id, refresh_token='synthetic-refresh', written_at=1000)
    assert handoff.take(profile) == (True, 'synthetic-refresh')
    assert not vault.values
    assert handoff.take(profile) == (False, None)


@pytest.mark.parametrize('age,expected', [(599.9, 'synthetic-refresh'), (600, None), (601, None), (-1, None)])
def test_update42_handoff_ten_minute_expiry(vault, age, expected):
    profile = result().identity.profile()
    clock = NS(now=1000)
    handoff = UpdateLoginHandoff(clock=lambda: clock.now)
    handoff.write(profile, 'synthetic-refresh')
    clock.now += age
    assert handoff.take(profile) == (True, expected)
    assert not vault.values


def test_update42_account_mismatch_and_malformed_removed(vault):
    handoff = UpdateLoginHandoff()
    handoff.write(result().identity.profile(), 'synthetic-refresh')
    assert handoff.take(result('other').identity.profile()) == (True, None)
    assert not vault.values
    locker.store_secret('unreadable-json', handoff.REFERENCE)
    assert handoff.take(result().identity.profile()) == (True, None)
    assert not vault.values


def test_update42_locker_read_failure_uses_normal_login(vault, monkeypatch):
    profile = result().identity.profile()
    handoff = UpdateLoginHandoff()
    handoff.write(profile, 'synthetic-refresh')
    monkeypatch.setattr(locker, 'get_secret', Mock(side_effect=RuntimeError('synthetic-refresh')))
    assert handoff.take(profile) == (True, None)
    assert not vault.values


@pytest.mark.parametrize('remember,automatic,password', [(False, False, False), (True, False, False), (True, True, True)])
def test_update42_restore_keeps_all_preferences_and_only_opted_in_tokens(tmp_path, vault, remember, automatic, password):
    session, current = session_at(tmp_path, remember, automatic, password)
    before = settings_snapshot(session)
    session.update_handoff.write(current.identity.profile(), current.refresh_token)
    present, profile, token = session.take_update_login()
    assert present and token
    value = session.restore_update_login(profile, token, threading.Event(), restorer=restored)
    assert value.refresh_token == 'rotated-refresh'
    assert settings_snapshot(session) == before
    assert session.take_update_login()[0] is False
    assert not any(user == UpdateLoginHandoff.REFERENCE for _, user in vault.values)
    if remember:
        assert session.credentials.load_oauth_tokens(profile)[1] == 'rotated-refresh'
    else:
        assert not vault.values


@pytest.mark.parametrize('failure', ['network', 'mismatch', 'cancelled', 'reauth'])
def test_update42_failed_login_never_reuses_or_changes_options(tmp_path, vault, failure):
    session, current = session_at(tmp_path, True, True)
    if failure == 'reauth': session.set_reauthentication_required(current.identity.profile())
    before = settings_snapshot(session)
    session.update_handoff.write(current.identity.profile(), current.refresh_token)
    _, profile, token = session.take_update_login()
    cancelled = threading.Event()
    if failure == 'cancelled': cancelled.set()
    def fail(profile, credentials):
        if failure == 'mismatch': return result('other', restored=True)
        raise RuntimeError('synthetic-refresh must never be logged')
    with pytest.raises(AccountError, match='更新后的登录未能恢复'):
        session.restore_update_login(profile, token, cancelled, restorer=fail)
    assert settings_snapshot(session) == before
    assert session.take_update_login()[0] is False


def test_update42_logging_contains_only_handoff_states(vault, caplog):
    caplog.set_level(logging.INFO, logger='refactor.runtime')
    handoff = UpdateLoginHandoff(clock=lambda: 1)
    handoff.write(result().identity.profile(), 'synthetic-refresh')
    handoff.take(result('other').identity.profile())
    handoff.clock = lambda: 1000
    handoff.write(result().identity.profile(), 'synthetic-refresh')
    handoff.clock = lambda: 1600
    handoff.take(result().identity.profile())
    handoff._log('交接成功')
    logs = [json.loads(runtime_logging.DiagnosticFormatter().format(r)) for r in caplog.records]
    assert [r['state'] for r in logs] == ['HANDOFF_FAILED', 'HANDOFF_EXPIRED', 'HANDOFF_SUCCESS']
    assert 'synthetic-refresh' not in json.dumps(logs)


def wait_for(qapp, predicate):
    until = time.monotonic() + 3
    while not predicate() and time.monotonic() < until:
        qapp.processEvents()
        time.sleep(.005)
    assert predicate()


@pytest.mark.parametrize('remember,automatic', [(False, False), (True, False), (True, True)])
def test_update42_new_app_enters_home_once_without_updater_arguments(tmp_path, vault, monkeypatch, qapp, remember, automatic):
    session, current = session_at(tmp_path, remember, automatic)
    before = settings_snapshot(session)
    session.update_handoff.write(current.identity.profile(), current.refresh_token)
    seen = []
    def refresh(profile, credentials):
        seen.append(threading.get_ident())
        assert session.take_update_login()[0] is False
        return restored(profile, credentials)
    # Home presentation is the only fake; startup, background refresh,
    # result acceptance, Locker and settings contracts remain real.
    home = Mock()
    monkeypatch.setattr('refactor.learning.app.window.LearningWindow', lambda *a, **kw: home)
    shell = AccountShell(qapp, NS(data_root=tmp_path), login_session=session, native_restorer=refresh)
    shell._load_capabilities = lambda: None
    shell._refresh_closure_recovery = lambda: None
    shell._bind_learning_features = lambda *a, **kw: None
    shell._best_effort_native_logout = Mock()
    try:
        shell.show()
        wait_for(qapp, lambda: shell.home is home)
        assert seen and seen[0] != threading.get_ident()
        assert not shell.gate.isVisible() and shell._feature_session_confirmed
        assert shell.gate.auto_box.isChecked() is automatic
        assert settings_snapshot(session) == before
        assert session.take_update_login()[0] is False
        if not remember: assert not vault.values
    finally:
        shell.close(); shell.gate.hide(); shell.gate.deleteLater(); qapp.processEvents()


@pytest.mark.parametrize('kind', ['expired', 'mismatch', 'failed'])
def test_update42_invalid_startup_handoff_does_not_fall_back_to_auto_login(tmp_path, vault, qapp, kind):
    session, current = session_at(tmp_path, True, True)
    before = settings_snapshot(session)
    session.update_handoff.write(current.identity.profile(), current.refresh_token)
    if kind == 'expired': session.update_handoff.clock = lambda: time.time() + 601
    if kind == 'mismatch': session.update_handoff.write(result('other').identity.profile(), current.refresh_token)
    def fail(*args): raise RuntimeError('synthetic-refresh')
    shell = AccountShell(qapp, NS(data_root=tmp_path), login_session=session, native_restorer=fail)
    shell._load_capabilities = lambda: None
    shell._refresh_closure_recovery = lambda: None
    ordinary = Mock(side_effect=RuntimeError('network still unavailable'))
    session.restore = ordinary
    session.load_offline_snapshot = lambda profile: None
    try:
        shell.show()
        if kind == 'failed':
            wait_for(qapp, lambda: ordinary.called)
            assert shell._remembered_profile is not None
            assert shell.gate.state.value == 'AUTHENTICATING'
        else: qapp.processEvents()
        assert shell.home is None and shell.gate.isVisible()
        if kind != 'failed': ordinary.assert_not_called()
        assert settings_snapshot(session) == before
        assert session.take_update_login()[0] is False
    finally:
        shell.close(); shell.gate.hide(); shell.gate.deleteLater(); qapp.processEvents()


def test_update42_failed_handoff_skips_offline_snapshot_and_retries_network(tmp_path, vault, monkeypatch, qapp):
    """A transient handoff outage must not stop at the tokenless offline shell."""
    session, current = session_at(tmp_path, True, True, password=True)
    session.update_handoff.write(current.identity.profile(), current.refresh_token)
    ordinary = Mock(side_effect=RuntimeError('network still unavailable'))
    session.restore = ordinary
    # This is exactly the snapshot that previously short-circuited the retry
    # path after update_restore failed.
    session.load_offline_snapshot = lambda profile: result(restored=True)
    monkeypatch.setattr('refactor.learning.app.window.LearningWindow', lambda *a, **kw: Mock())
    shell = AccountShell(qapp, NS(data_root=tmp_path), login_session=session,
                         native_restorer=Mock(side_effect=RuntimeError('handoff unavailable')))
    shell._load_capabilities = lambda: None
    shell._refresh_closure_recovery = lambda: None
    try:
        shell.show()
        wait_for(qapp, lambda: ordinary.called)
        assert shell.home is None
        assert shell._remembered_profile is not None
        assert shell.gate.state.value == 'AUTHENTICATING'
        assert '重新登录' not in shell.gate.state_detail.text()
    finally:
        shell.close(); shell.gate.hide(); shell.gate.deleteLater(); qapp.processEvents()


@pytest.mark.parametrize('outcome', ['success', 'failed', 'paused', 'cancelled', 'dispatch_error'])
def test_update42_adapter_writes_after_download_before_old_coordinator_and_cleans_failures(tmp_path, vault, qapp, outcome):
    events = []
    handoff = UpdateLoginHandoff()
    profile = result().identity.profile()
    class Owner:
        def prepare_update_login(self):
            events.append('write')
            handoff.write(profile, 'synthetic-refresh')
            return True
        def clear_update_login(self):
            events.append('clear'); handoff.clear()
    class Core:
        def attach_shutdown_guard(self, app): pass
        def record_update_authorization(self, root, target, release_sha256):
            assert target == '2.0.39' and release_sha256 == 'a' * 64
            events.append('authorized')
        def update_to_latest(self, root, progress, cancel, **kw):
            assert not vault.values
            events.append('download')
            progress({'state': 'DOWNLOADING'})
            assert not vault.values
            if outcome == 'paused': return dict(state='DOWNLOAD_PAUSED_OR_INTERRUPTED')
            progress({'state': 'APPLYING'})
            assert locker.get_secret(handoff.REFERENCE)
            events.append('dispatch')
            if outcome == 'dispatch_error': raise RuntimeError('dispatch failed')
            return dict(state='APPLYING', coordinator_started=True)
    gate = QWidget()
    adapter = DesktopUpdates(qapp, tmp_path, gate, core=Core(), login_handoff=Owner())
    # Release the real named Windows session event before substituting it.
    # Otherwise a later cancellation leaves that leaked process-wide event
    # signalled and unrelated update tests can never apply their fixture.
    adapter._session.close()
    adapter._session = NS(require_running=lambda: None, close=lambda: None, defer=lambda: None)
    adapter._coordinator_result = lambda value: (dict(state='FAILED_RECOVERABLE') if outcome == 'failed'
        else {**value, 'cancelled': True} if outcome == 'cancelled' else value)
    # Model the verified optional release shown in the main-window banner;
    # calling install is the user's explicit click on that banner.
    adapter.state.update(state='OPTIONAL_UPDATE', login_allowed=True, mandatory=False,
                         latest='2.0.39', release_sha256='a' * 64, feed='VERIFIED')
    try:
        adapter.run('install', gate)
        wait_for(qapp, lambda: not adapter._busy)
        assert events[:2] == ['authorized', 'download']
        if outcome == 'success':
            assert events == ['authorized', 'download', 'write', 'dispatch']
            adapter.close()  # Coordinator shutdown must retain the record.
            assert handoff.take(profile) == (True, 'synthetic-refresh')
        else:
            assert not vault.values
            assert 'clear' in events or outcome == 'paused'
    finally:
        adapter.close(); gate.close(); handoff.clear()


def test_update42_explicit_session_cancellation_removes_handoff(tmp_path, vault, qapp):
    handoff = UpdateLoginHandoff()
    profile = result().identity.profile()
    owner = NS(prepare_update_login=lambda: handoff.write(profile, 'synthetic-refresh') or True,
               clear_update_login=handoff.clear)
    gate = QWidget()
    adapter = DesktopUpdates(qapp, tmp_path, gate,
        core=NS(attach_shutdown_guard=lambda app: None), login_handoff=owner)
    try:
        adapter._install_progress({'state': 'APPLYING'})
        adapter._defer_session()
        assert adapter._cancel.is_set() and not vault.values
    finally: adapter.close(); gate.close()


def test_update42_nonremembered_shutdown_preserves_only_armed_handoff(tmp_path, vault, qapp):
    session, current = session_at(tmp_path)
    shell = AccountShell(qapp, NS(data_root=tmp_path), login_session=session)
    shell._login_result = current
    shell._session_credentials = shell._EphemeralCredentials(current)
    shell._best_effort_native_logout = Mock()
    assert shell.prepare_update_login()
    shell.close()
    shell._best_effort_native_logout.assert_not_called()
    assert session.take_update_login()[2] == 'synthetic-refresh'
    shell.gate.hide(); shell.gate.deleteLater(); qapp.processEvents()


def test_update42_normal_shutdown_still_ends_nonremembered_session(tmp_path, vault, qapp):
    session, current = session_at(tmp_path)
    shell = AccountShell(qapp, NS(data_root=tmp_path), login_session=session)
    shell._login_result = current
    shell._session_credentials = shell._EphemeralCredentials(current)
    shell._best_effort_native_logout = Mock()
    shell.close()
    shell._best_effort_native_logout.assert_called_once()
    assert not vault.values
    shell.gate.hide(); shell.gate.deleteLater(); qapp.processEvents()


def test_update42_cancel_startup_handoff_keeps_user_options(tmp_path, vault, qapp):
    session, current = session_at(tmp_path, True, True)
    before = settings_snapshot(session)
    session.update_handoff.write(current.identity.profile(), current.refresh_token)
    session.take_update_login()
    shell = AccountShell(qapp, NS(data_root=tmp_path), login_session=session)
    shell._update_restore_pending = True
    shell._update_login_token = current.refresh_token
    shell._update_auto_pending = True
    try:
        shell._cancel_login()
        assert shell._update_login_token is None and not shell._update_restore_pending
        assert settings_snapshot(session) == before
        assert session.take_update_login()[0] is False
    finally:
        shell.close(); shell.gate.hide(); shell.gate.deleteLater(); qapp.processEvents()


def test_update42_fast_login_health_reports_home_not_hidden_gate(monkeypatch):
    from refactor.desktop.delivery import per_user_install
    report = Mock()
    monkeypatch.setattr(per_user_install, 'notify_health', report)
    shell = NS(home=object(), gate=object())
    AccountShell.notify_update_health(shell, 'fixture-root', 'fixture-attempt', 'fixture-nonce')
    report.assert_called_with('fixture-root', shell.home, 'fixture-attempt', 'fixture-nonce')
    shell.home = None
    AccountShell.notify_update_health(shell, 'fixture-root', 'fixture-attempt', 'fixture-nonce')
    report.assert_called_with('fixture-root', shell.gate, 'fixture-attempt', 'fixture-nonce')
