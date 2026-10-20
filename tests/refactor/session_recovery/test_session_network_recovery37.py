"""Batch 37: transient confirmation, recovery, gate lifecycle and local logs."""
from concurrent.futures import Future
import json
import hashlib
import logging
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS, MethodType
from unittest.mock import Mock

import certifi
import httpx
import pytest
from PySide6.QtWidgets import QApplication, QWidget

from refactor.desktop.account_shell_main import AccountShell
from refactor.desktop.login_shell import LoginGateWindow
from refactor.learning.app.bridge import AppBridge, CallContext, _Handler, BridgeError
from refactor.learning.app.sync import WindowSync
from refactor.learning.services.course_sync_service import CourseSyncService
from refactor.learning.services.session_guard import SessionGuard
from refactor.learning.sync_identity import PlanError
from refactor.learning.sync_source import AuthenticatedPlanSource, CONNECTION_UNSTABLE
from refactor import runtime_logging as diagnostics

ISSUER, SUBJECT = 'https://fixture.invalid/realm', 'student'
ACCOUNT = 'mfa1_' + hashlib.sha256(('mentorflow.account.v1:' + ':'.join(
    hashlib.sha256(v.encode()).hexdigest() for v in (ISSUER, SUBJECT))).encode()).hexdigest()


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def login():
    profile = NS(account_id=ACCOUNT, issuer_hash=hashlib.sha256(ISSUER.encode()).hexdigest(),
                 subject_hash=hashlib.sha256(SUBJECT.encode()).hexdigest())
    identity = NS(account_id=ACCOUNT, issuer=ISSUER, subject=SUBJECT,
                  organization_id='org', workspace_id='workspace', profile=lambda: profile)
    return NS(access_token='synthetic-access', refresh_token='synthetic-refresh', identity=identity,
              server_state={'authenticated': True, 'account': {'account_id': ACCOUNT, 'subject_id': SUBJECT},
                            'context': {'organization_id': 'org', 'workspace_id': 'workspace'}})


def shell_fixture(qapp):
    current = login()
    shell = NS(app=qapp, home=Mock(), gate=LoginGateWindow(), _closing=False, _login_result=current,
               _feature_session_confirmed=True, _session_refresh_degraded=False, _session_refresh_busy=False,
               _account_settings=None, _closure=None, _session_timer=Mock(), _session_epoch=4,
               _logout_pending=False, _switch_pending=False, _confirmed_role_account_id=ACCOUNT,
               _confirmed_server_role='LEARNER', _session_credentials=Mock())
    shell.home.set_session_offline = Mock()
    shell._EphemeralCredentials = AccountShell._EphemeralCredentials
    shell._session_credentials.load_oauth_tokens.side_effect = OSError('synthetic vault timeout')
    shell.session = NS(closure_blocks_login=lambda profile: False,
                       registry=NS(get=lambda account: {'remember_session': False}))
    shell._confirmed_role_from_result = lambda result: 'LEARNER'
    shell._hide_login_windows = MethodType(AccountShell._hide_login_windows, shell)
    return shell


class Immediate:
    def submit(self, fn, *args, **kwargs):
        result = Future()
        try:
            result.set_result(fn(*args, **kwargs))
        except Exception as exc:
            result.set_exception(exc)
        return result
    def shutdown(self, **kwargs): pass


def test_refresh_timeout_missing_feature_session_recovers_without_login(qapp):
    shell = shell_fixture(qapp)
    source = AuthenticatedPlanSource(account_id=ACCOUNT, api_base_url='https://fixture.invalid',
        ca_bundle=certifi.where(), session_provider=lambda: AccountShell._feature_session(shell, shell.home, ACCOUNT))
    clock = NS(now=0)
    class Engine:
        def __init__(self, *args): self.calls = 0
        def sync(self, **kwargs):
            self.calls += 1
            source.require_current()
            return {'ok': True}
        def cancel(self): pass
        def close(self): pass
    service = CourseSyncService(NS(pending_records=lambda limit: []), None, auto_start=False,
                                clock=lambda: clock.now, executor=Immediate(), sync_factory=Engine)
    try:
        AccountShell._retry_session_refresh(shell)
        service.start(); service._tick()
        assert service._active and service.status()['state'] == 'failed'
        assert service.status()['message'] == CONNECTION_UNSTABLE and service._retry_at == 30
        shell.home.set_session_notice.assert_called_with(CONNECTION_UNSTABLE, show_verify=False)
        assert not shell.gate.isVisible()
        shell._session_credentials.load_oauth_tokens.side_effect = None
        shell._session_credentials.load_oauth_tokens.return_value = ('synthetic-access', 'synthetic-refresh')
        AccountShell._accept_session_refresh(shell, shell._login_result, login())
        assert not shell._session_refresh_degraded
        clock.now = 29; service._tick(); assert service._sync.calls == 1
        clock.now = 30; service._tick(); service._tick()
        assert service.status()['state'] == 'synced' and service._active
        assert service._sync.calls == 2 and service._failures == 0
    finally:
        service.dispose(); source.close(); shell.gate.deleteLater()


def test_real_refresh_worker_timeout_callback_then_success(qapp):
    from refactor.desktop.account_protocol import AccountError
    shell = shell_fixture(qapp)
    shell.session.requires_reauthentication = lambda profile: False
    modes = [AccountError('NATIVE_NETWORK_UNAVAILABLE', 'synthetic network timeout'), login()]
    def restore(profile, credentials):
        value = modes.pop(0)
        if isinstance(value, Exception): raise value
        return value
    shell._restore_active_session = restore
    shell._retry_session_refresh = MethodType(AccountShell._retry_session_refresh, shell)
    shell._accept_session_refresh = MethodType(AccountShell._accept_session_refresh, shell)
    shell._expire_active_session = Mock()
    shell.gate.background_failed.connect(lambda job, text: AccountShell._background_failed(shell, job, text))
    shell.gate.background_succeeded.connect(lambda job, result: AccountShell._background_succeeded(shell, job, result))
    def settle():
        until = time.monotonic() + 5
        while shell._session_refresh_busy and time.monotonic() < until:
            qapp.processEvents()
            time.sleep(.001)
        assert not shell._session_refresh_busy
    try:
        original = shell._login_result
        AccountShell._refresh_active_session(shell); settle()
        assert shell._login_result is original and shell._session_refresh_degraded
        shell._expire_active_session.assert_not_called()
        AccountShell._refresh_active_session(shell); settle()
        assert not shell._session_refresh_degraded and shell._login_result is not original
        assert shell._session_epoch == 5 and not shell.gate.isVisible()
    finally:
        shell.gate.deleteLater()


@pytest.mark.parametrize('code', ['REAUTH_REQUIRED', 'ENTERPRISE_SESSION_REAUTH_REQUIRED', 'NATIVE_SESSION_SUPERSEDED'])
def test_only_explicit_denial_stops_sync(qapp, code):
    service = CourseSyncService(NS(), None, auto_start=False)
    try:
        service._active = True
        service._finish('sync', None, PlanError(code, 'synthetic refusal'))
        assert not service._active
        assert service.status()['state'] == ('superseded' if code == 'NATIVE_SESSION_SUPERSEDED' else 'reauth')
    finally:
        service.dispose()


def test_transient_backoff_caps_at_five_minutes(qapp):
    service = CourseSyncService(NS(), None, auto_start=False, clock=lambda: 0)
    try:
        service._active = True
        for delay in [30, 60, 120, 240, 300, 300]:
            service._finish('sync', None, PlanError('SESSION_UNAVAILABLE', 'synthetic'))
            assert service._retry_at == delay and service._active
    finally:
        service.dispose()


@pytest.mark.parametrize('status,expected', [(200, 'active'), (401, 'retry'), (403, 'retry'), (503, 'retry')])
def test_guard_missing_session_retry_then_real_server_status(qapp, status, expected):
    state = {'login': None}
    guard = SessionGuard(dict(account_id=ACCOUNT, session_provider=lambda: state['login'],
        api_base_url='https://fixture.invalid', ca_bundle=certifi.where()),
        http_transport=httpx.MockTransport(lambda req: httpx.Response(status, json={'state': 'active'})))
    try:
        assert guard._probe() == 'retry'
        state['login'] = login()
        assert guard._probe() == expected
    finally:
        guard.dispose()


@pytest.mark.parametrize('failure', [PlanError('SESSION_UNAVAILABLE', 'synthetic'),
    PlanError('SOURCE_UNAVAILABLE', 'synthetic'), httpx.ReadTimeout('synthetic'),
    BridgeError('SESSION_UNAVAILABLE', 'synthetic')])
@pytest.mark.parametrize('recovers', [True, False])
def test_learning_call_retries_once_then_reports_network(qapp, failure, recovers):
    bridge, calls, sent, errors = AppBridge(), [], [], []
    bridge._send = sent.append
    bridge.request_failed.connect(errors.append)
    def work(params, ctx):
        calls.append(1)
        if len(calls) == 1 or not recovers: raise failure
        return {'preserved': True}
    try:
        bridge._run('test', _Handler(work, False), {}, CallContext('test', Event(), lambda text: None))
        assert len(calls) == 2
        if recovers:
            assert sent[-1]['ok'] and not errors
        else:
            assert sent[-1]['error']['message'] == '网络不稳，请稍后再试'
            assert 'REAUTH_REQUIRED' not in errors
    finally:
        bridge.close()


def test_server_refusal_not_replayed_and_cancel_does_not_replay(qapp):
    bridge, sent, calls = AppBridge(), [], []
    bridge._send = sent.append
    def work(params, ctx):
        calls.append(1)
        raise PlanError('REAUTH_REQUIRED', 'refused')
    context = CallContext('test', Event(), lambda text: None)
    try:
        bridge._run('test', _Handler(work, False), {}, context)
        assert len(calls) == 1 and sent[-1]['error']['message'].startswith('登录已失效')
        def cancelled(params, ctx):
            ctx.cancel.set()
            calls.append(1)
            raise PlanError('SESSION_UNAVAILABLE', 'temporary')
        bridge._run('test', _Handler(cancelled, False), {}, context)
        assert len(calls) == 2 and sent[-1]['error']['code'] == 'CANCELLED'
    finally:
        bridge.close()


def test_window_transient_notice_does_not_pause_and_clears_on_sync(qapp):
    home = NS(session_notice=None, set_session_notice=Mock(), clear_session_notice=Mock())
    owner = NS(_closed=False, _paused=False, guard=Mock(), window=home, bridge=Mock())
    WindowSync.note_error(owner, 'SESSION_UNAVAILABLE')
    owner.guard.note_error.assert_called_once_with('SESSION_UNAVAILABLE')
    home.set_session_notice.assert_called_with(CONNECTION_UNSTABLE, show_verify=False)
    owner.guard.stop.assert_not_called()
    home.session_notice = {'message': CONNECTION_UNSTABLE}
    WindowSync._status_changed(owner, {'state': 'synced', 'message': '已同步'})
    home.clear_session_notice.assert_called_once()
    owner.bridge.cancel_pending.assert_not_called()


def test_confirmed_relogin_hides_all_old_gates_and_epoch_rejects_old_failure(qapp):
    shell = shell_fixture(qapp)
    old_gate = LoginGateWindow()
    child = QWidget(shell.gate)
    child.setWindowFlag(__import__('PySide6.QtCore', fromlist=['Qt']).Qt.WindowType.Window)
    try:
        shell.gate.show(); old_gate.show(); child.show()
        shell.gate.password_field.setText('synthetic-secret')
        shell._hide_login_windows()
        assert not shell.gate.isVisible() and not old_gate.isVisible() and not child.isVisible()
        assert not shell.gate.password_field.text()
        shell._expire_active_session = Mock()
        AccountShell._background_failed(shell, '3:session_expired', 'old request')
        shell._expire_active_session.assert_not_called()
        assert not shell.gate.isVisible()
    finally:
        shell.gate.deleteLater(); old_gate.deleteLater()


@pytest.fixture
def diagnostic_log(tmp_path):
    logger = logging.getLogger('refactor')
    prior, level = diagnostics._handler, logger.level
    if prior is not None: logger.removeHandler(prior)
    diagnostics._handler = None
    target = diagnostics.configure(tmp_path)
    yield target
    logger.removeHandler(diagnostics._handler)
    diagnostics._handler.close()
    diagnostics._handler = prior
    if prior is not None: logger.addHandler(prior)
    logger.setLevel(level)


def test_log_redacts_exception_chain_arguments_and_content_keeps_stack(diagnostic_log):
    secret = 'SYNTHETIC-TOKEN-MUST-NOT-APPEAR'
    content = 'synthetic student chat and private learning content'
    try:
        try: raise RuntimeError('Bearer ' + secret + content)
        except RuntimeError as cause: raise PlanError('SESSION_UNAVAILABLE', 'password=' + secret) from cause
    except PlanError:
        logging.getLogger('refactor.fixture').exception('token %s; chat %s', secret, content)
        diagnostics.event('refresh', state='FAILED', code='SESSION_UNAVAILABLE', exc_info=True)
    value = diagnostic_log.read_text(encoding='utf-8')
    assert secret not in value and content not in value and 'password=' not in value
    record = json.loads(value.splitlines()[-1])
    assert record['code'] == 'SESSION_UNAVAILABLE' and record['stack']
    assert record['exception_type'] == 'PlanError'


def test_log_rolls_five_files_and_model_call_records_each_result(diagnostic_log):
    handler = diagnostics._handler
    assert handler.maxBytes == 5 * 1024 * 1024 and handler.backupCount == 4
    handler.maxBytes = 700
    for _ in range(45): diagnostics.event('sync', state='synced')
    files = list(diagnostic_log.parent.glob('mentorflow.log*'))
    assert len(files) == 5 and all(p.stat().st_size <= 700 for p in files)
    handler.maxBytes = 5 * 1024 * 1024
    class Gateway:
        @diagnostics.trace_model_call
        def request(self, purpose, fail=False):
            if fail: raise PlanError('MODEL_TIMEOUT', 'synthetic secret body')
            return 'ok'
    gateway = Gateway()
    assert gateway.request('teacher') == 'ok'
    with pytest.raises(PlanError): gateway.request('profile', True)
    records = [json.loads(line) for line in diagnostic_log.read_text(encoding='utf-8').splitlines()]
    models = [r for r in records if r.get('event') == 'model']
    assert [(r['purpose'], r['code']) for r in models] == [('teacher', 'OK'), ('profile', 'MODEL_TIMEOUT')]
    assert all(r['elapsed_seconds'] >= 0 for r in models)


def test_open_log_folder_uses_bound_data_root_only(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    parent = QWidget()
    parent._data_root = tmp_path
    urls = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: urls.append(url) or True)
    bridge = AppBridge(parent)
    try:
        assert bridge._open_logs({}, None) == {'ok': True}
        assert Path(urls[0].toLocalFile()) == (tmp_path / 'logs').resolve()
        with pytest.raises(BridgeError): bridge._open_logs({'path': 'not allowed'}, None)
        assert len(urls) == 1
    finally:
        bridge.close(); parent.deleteLater()


def test_installed_and_source_log_paths_use_existing_data_root(tmp_path, monkeypatch):
    import os
    import sys
    import tempfile
    from refactor.desktop import shell_paths
    # Isolated runtime only: never touch the installed app or source data.
    monkeypatch.setattr(os, 'environ', dict(os.environ))
    monkeypatch.setattr(tempfile, 'tempdir', tempfile.tempdir)
    monkeypatch.setattr(shell_paths, '_public_runtime_config', lambda: {})
    monkeypatch.setattr(shell_paths, '_build_ca_bundle', lambda cache=None: tmp_path / 'fixture.pem')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'MentorFlow' / 'App' / 'MentorFlow.exe'))
    installed = shell_paths.prepare_shell_runtime()
    assert diagnostics.log_directory(installed.data_root) == tmp_path / 'MentorFlow' / 'Data' / 'logs'
    monkeypatch.setattr(sys, 'frozen', False)
    monkeypatch.setattr(shell_paths, 'DATA_ROOT', tmp_path / 'source-data')
    monkeypatch.setattr(shell_paths, 'CACHE_ROOT', tmp_path / 'source-cache')
    source = shell_paths.prepare_shell_runtime()
    assert diagnostics.log_directory(source.data_root) == tmp_path / 'source-data' / 'logs'
