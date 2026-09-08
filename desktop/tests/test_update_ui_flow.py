"""Updater presentation and lifecycle regressions; only synthetic signed releases."""
import hashlib
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel

from desktop.update_controller import UpdateController, UpdateState
from desktop.update_panel import UpdatePanel, release_summary
from desktop.update_session import UpdateSession, SessionEnding
from desktop.tests.test_mature_update import Feed


def until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.instance().processEvents()
        if predicate():
            QApplication.instance().processEvents()
            return
        time.sleep(.005)
    raise AssertionError('UI condition did not become true')


@pytest.fixture
def feed(tmp_path):
    value = Feed(tmp_path)
    yield value
    value.close()


@pytest.fixture
def host(tmp_path):
    window = QMainWindow()
    window.show()
    settings = QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat)
    yield window, settings
    window.close()


@pytest.mark.parametrize('raw,expected', [
    ('CHECKING', 'CHECKING'), ('UPDATE_AVAILABLE', 'UPDATE_AVAILABLE'),
    ('PREPARING', 'DOWNLOADING'), ('DOWNLOADING', 'DOWNLOADING'),
    ('READY_TO_APPLY', 'READY_TO_RESTART'), ('APPLYING', 'APPLYING'),
    ('RESTART_REQUIRED', 'APPLYING'), ('UP_TO_DATE', 'UP_TO_DATE'),
    ('FAILED_SECURITY', 'RECOVERABLE_ERROR'), ('FAILED_RECOVERABLE', 'RECOVERABLE_ERROR'),
    ('DOWNLOAD_PAUSED_OR_INTERRUPTED', 'RECOVERABLE_ERROR'),
])
def test_user_states_and_plain_labels(feed, raw, expected):
    service = feed.service()
    service.check()
    service.snapshot.update(state=raw, message='helper manifest receipt ' + 'f'*64,
                            actual_downloaded_bytes=5*1024**2, expected_download_bytes=10*1024**2,
                            release_notes='界面改进\nsource acceptance debug\nhardlink '+ 'f'*64)
    panel = UpdatePanel(service=service, auto_delay_ms=60000)
    panel.refresh()
    assert panel.property('updateState') == expected
    text = ' '.join(label.text() for label in panel.findChildren(QLabel))
    for forbidden in ('helper', 'manifest', 'receipt', 'source acceptance', 'hardlink', 'f'*64):
        assert forbidden not in text
    if raw == 'DOWNLOADING':
        assert panel.transfer_label.text() == '50% · 5.0 MB / 10.0 MB'
    if raw == 'UP_TO_DATE':
        assert panel.status.text() == '已是最新版本'
    assert panel.apply_button.text() == '重启并更新'
    panel.shutdown()
    panel.close()


def test_release_notes_are_short_and_plain():
    assert len(release_summary('x'*900)) == 400
    assert release_summary('manifest\nhelper') == '此更新包含功能改进与问题修复。'
    assert UpdatePanel._size(2*1024**3) == '2.0 GB'


def test_real_prelogin_attaches_one_delayed_updater_after_security_gate(feed, tmp_path, monkeypatch):
    from desktop.login_shell import LoginGateWindow
    from desktop import gui_update_runtime
    app = QApplication.instance()
    service = feed.service()
    controller = UpdateController(service, settings=QSettings(str(tmp_path/'login.ini'), QSettings.Format.IniFormat))
    monkeypatch.setattr(app, '_mentorflow_updater', controller, raising=False)
    monkeypatch.setattr(gui_update_runtime, 'notify_health', Mock())
    monkeypatch.setattr('scripts.install_product.configured', lambda: SimpleNamespace(uat=False))
    gate = LoginGateWindow(); startup = SimpleNamespace(results={'SECURITY_GATE': False})
    try:
        gui_update_runtime.attach_updates(app, gate, startup, tmp_path/'install')
        gate.show(); app.processEvents()
        assert controller.window is None and not controller._started
        startup.results['SECURITY_GATE'] = True
        until(lambda: controller.window is gate)
        assert gate.about_button.isEnabled() and gate.isVisible()
        assert not controller._started  # The startup callback is delayed, never synchronous I/O.
        gate.about_button.click(); app.processEvents()
        panel = gate.findChild(UpdatePanel)
        assert panel is not None and panel.controller is controller
    finally:
        gate.close(); controller.shutdown()


def test_real_settings_card_stays_in_view_across_download_states(feed, tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QScrollArea
    from desktop.system_page import SystemPage
    from desktop.style import STYLE
    feed.publish('1.1.1', {**feed.old_files, '_internal/frontend/ui.txt': b'viewport\n'*100000})
    feed.delay = .025
    service = feed.service(); service.check()
    app = QApplication.instance()
    controller = UpdateController(service, settings=QSettings(str(tmp_path/'view.ini'), QSettings.Format.IniFormat))
    monkeypatch.setattr(app, '_mentorflow_updater', controller, raising=False)
    monkeypatch.setenv('MENTORFLOW_USER_DATA_ROOT', str(tmp_path/'data'))
    page = SystemPage({}, 'http://127.0.0.1:0', lambda: None, object())
    page.setStyleSheet(STYLE); page.resize(1100, 850); page.show()
    panel = page.update_panel
    scroller = page.findChild(QScrollArea)
    def visible_card():
        point = panel.mapTo(scroller.viewport(), QPoint())
        assert point.y() >= 0
        assert point.y() + panel.height() <= scroller.viewport().height()
    try:
        app.processEvents(); panel.refresh()
        # A user may scroll until the action is visible while the card heading
        # is above the viewport. Starting an update must reveal its progress.
        scroller.verticalScrollBar().setValue(panel.mapTo(scroller.widget(), QPoint()).y() + 70)
        QTest.mouseClick(panel.download_button, Qt.MouseButton.LeftButton)
        until(lambda: service.snapshot.get('actual_downloaded_bytes', 0) > 0)
        panel.refresh(); app.processEvents(); visible_card()
        QTest.mouseClick(panel.cancel_button, Qt.MouseButton.LeftButton)
        until(lambda: not controller.busy)
        panel.refresh(); app.processEvents(); visible_card()
        QTest.mouseClick(panel.download_button, Qt.MouseButton.LeftButton)
        until(lambda: not controller.busy and service.snapshot['state'] == 'READY_TO_APPLY')
        panel.refresh(); app.processEvents(); visible_card()
        assert panel.apply_button.hasFocus()
    finally:
        page.close(); controller.shutdown()


def test_start_check_download_and_apply_save_state_before_bridge(feed, host):
    window, settings = host
    service = feed.service()
    calls = []
    service.apply_bridge = lambda *_: calls.append(settings.contains(f'updater/{service.install_identity}/QMainWindow/geometry'))
    controller = UpdateController(service, settings=settings)
    controller.bind_window(window)
    controller.start()
    until(lambda: not controller.busy and controller.state == UpdateState.UPDATE_AVAILABLE)
    controller.run('download')
    until(lambda: not controller.busy and service.snapshot['state'] == 'READY_TO_APPLY')
    controller.run('apply_and_restart')
    until(lambda: not controller.busy and bool(calls))
    assert calls == [True]
    assert settings.value(controller.key + 'pending_release') == hashlib.sha256(feed.raw).hexdigest()
    controller.shutdown()


def test_next_launch_rechecks_and_verifies_cached_update(feed, host, monkeypatch):
    window, settings = host
    first = feed.service()
    first.check(); first.download()
    controller = UpdateController(first, settings=settings)
    controller.remember_pending(); controller.shutdown()
    second = feed.service()
    events = []
    second.apply_bridge = lambda *_: events.append('bridge')
    verify = second.cache.verify_tree
    monkeypatch.setattr(second.cache, 'verify_tree', lambda *a: (events.append('hash'), verify(*a))[1])
    checks_before = sum(path == '/stable.json' for path, _ in feed.requests)
    controller = UpdateController(second, settings=settings)
    controller.bind_window(window)
    controller.start()
    until(lambda: 'bridge' in events)
    assert events.index('hash') < events.index('bridge')
    assert sum(path == '/stable.json' for path, _ in feed.requests) >= checks_before + 2
    until(lambda: not controller.busy)
    controller.shutdown()


@pytest.mark.parametrize('change', ['withdrawn', 'tampered', 'offline', 'cached_corrupt'])
def test_pending_cannot_bypass_validation(feed, host, change):
    window, settings = host
    service = feed.service()
    service.check(); service.download()
    controller = UpdateController(service, settings=settings)
    controller.remember_pending()
    # Even a forged user-cache deferral hint cannot authorize invalid updates.
    from desktop.update_session import record_session_deferred
    release_hash = hashlib.sha256(feed.raw).hexdigest()
    settings.setValue(controller.key + 'attempted_release', release_hash)
    settings.setValue(controller.key + 'attempted_pid', os.getpid())
    settings.setValue(controller.key + 'attempted_at_ns', str(time.time_ns()))
    assert record_session_deferred(service.staged, os.getpid(), release_hash)
    controller.shutdown()
    if change == 'withdrawn':
        feed.signed['metadata_version'] += 1
        feed.signed['withdrawn'] = True
        feed.sign(feed.signed)
    elif change == 'tampered':
        feed.raw = feed.raw.replace(b'Small frontend fix', b'Tampered release!')
    elif change == 'cached_corrupt':
        row = service.release.assets['_internal/frontend/ui.txt']
        service.cache.complete(row).write_bytes(b'corrupted cache')
    second = feed.service()
    if change == 'offline':
        second.metadata_url = feed.origin + '/offline'
    calls = []
    second.apply_bridge = lambda *_: calls.append(True)
    controller = UpdateController(second, settings=settings)
    controller.bind_window(window)
    controller.start()
    until(lambda: not controller.busy and second.snapshot['state'] != 'IDLE')
    # Queued startup continuation, if any, must also complete.
    until(lambda: not controller.busy)
    assert calls == []
    controller.shutdown()


def test_session_ending_before_apply_and_during_recheck_never_handoffs(feed, host, monkeypatch):
    window, settings = host
    service = feed.service()
    service.check(); service.download()
    calls = []
    service.apply_bridge = lambda *_: calls.append(True)
    controller = UpdateController(service, settings=settings)
    controller.bind_window(window)
    entered, release = threading.Event(), threading.Event()
    fetch = service.cache.transport.fetch_metadata
    def delayed(*args):
        entered.set(); release.wait(2)
        return fetch(*args)
    monkeypatch.setattr(service.cache.transport, 'fetch_metadata', delayed)
    controller.run('apply_and_restart')
    until(entered.is_set)
    controller._session_ending()
    release.set()
    until(lambda: not controller.busy)
    assert calls == []
    controller.run('apply_and_restart')
    assert calls == [] and not controller.busy
    assert settings.contains(controller.key + 'pending_release')
    assert not settings.contains(controller.key + 'attempted_release')
    controller.shutdown()
    # Deferral is not a denied consent: next normal launch must retry freshly.
    second = feed.service()
    second.apply_bridge = lambda *_: calls.append(True)
    controller = UpdateController(second, settings=settings)
    controller.bind_window(window); controller.start()
    until(lambda: bool(calls) and not controller.busy)
    assert calls == [True]
    controller.shutdown()


def test_windows_session_signal_survives_original_handle_close():
    if os.name != 'nt':
        pytest.skip('Windows handle lifetime')
    owner = UpdateSession()
    observer = UpdateSession(pid=os.getpid())
    owner.defer(); owner.close()
    with pytest.raises(SessionEnding):
        observer.require_running()
    observer.close()


def test_coordinator_observes_logoff_after_gui_handle_has_closed():
    if os.name != 'nt':
        pytest.skip('Windows session messages')
    import ctypes
    from ctypes import wintypes
    owner = UpdateSession()
    observer = UpdateSession(pid=os.getpid())
    owner.close()
    try:
        # Send only to our own hidden TEST window, never broadcast or log off.
        user = ctypes.WinDLL('user32')
        user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.SendMessageW(observer._monitor_window, 0x11, 0, 0x80000000)
        with pytest.raises(SessionEnding):
            observer.require_running()
    finally:
        observer.close()
    assert not observer._monitor.is_alive()


def test_normal_close_only_records_pending(feed, host):
    window, settings = host
    service = feed.service(); service.check(); service.download()
    calls = []
    service.apply_bridge = lambda *_: calls.append(True)
    controller = UpdateController(service, settings=settings)
    controller.bind_window(window)
    window.close()
    QApplication.instance().processEvents()
    assert calls == []
    assert settings.contains(controller.key + 'pending_release')
    controller.shutdown()


def test_failed_session_setup_keeps_application_usable(feed, host, monkeypatch):
    window, settings = host
    def unavailable():
        raise OSError('synthetic unavailable guard')
    monkeypatch.setattr('desktop.update_controller.UpdateSession', unavailable)
    service = feed.service(); service.check(); service.download()
    calls = []
    service.apply_bridge = lambda *_: calls.append(True)
    controller = UpdateController(service, settings=settings)
    controller.bind_window(window)
    controller.run('apply_and_restart')
    assert window.isVisible() and calls == []
    assert controller.state == UpdateState.RECOVERABLE_ERROR
    controller.shutdown()


def test_cancel_retry_and_duplicate_clicks_are_serialized(feed):
    feed.publish('1.1.1', {**feed.old_files, '_internal/frontend/ui.txt': b'retry\n'*100000})
    feed.delay = .01
    service = feed.service(); service.check()
    panel = UpdatePanel(service=service, auto_delay_ms=60000)
    panel.show()
    panel.download_button.click()
    first = panel._thread
    panel.download_button.click()
    assert panel._thread is first
    until(lambda: service.snapshot.get('actual_downloaded_bytes', 0) > 0)
    panel.refresh(); panel.cancel_button.click()
    until(lambda: not panel.controller.busy and service.snapshot['state'] == 'DOWNLOAD_PAUSED_OR_INTERRUPTED')
    panel.refresh()
    assert panel.download_button.text() == '重试下载' and panel.isVisible()
    panel.download_button.click()
    until(lambda: not panel.controller.busy and service.snapshot['state'] == 'READY_TO_APPLY')
    panel.refresh()
    assert panel.apply_button.isEnabled()
    panel.shutdown(); panel.close()


def test_session_rechecked_inside_uac_thread_before_os_launch(monkeypatch, tmp_path):
    from scripts import update_runtime
    shell = SimpleNamespace(ShellExecuteExW=Mock())
    ole = SimpleNamespace(CoInitializeEx=Mock(), CoUninitialize=Mock())
    monkeypatch.setattr(update_runtime.c, 'WinDLL', lambda name, **_: shell if name == 'shell32' else ole)
    class Guard:
        def require_running(self):
            raise SessionEnding('synthetic logoff after coordinator precheck')
    with pytest.raises(SessionEnding):
        update_runtime.runas_once(tmp_path/'unused.exe', tmp_path/'request.json', 'a'*64, session=Guard())
    shell.ShellExecuteExW.assert_not_called()
    ole.CoUninitialize.assert_called_once()


def test_failed_handoff_relaunch_does_not_loop_auto_apply(feed, host):
    window, settings = host
    first = feed.service(); first.check(); first.download()
    def denied(*_):
        raise RuntimeError('synthetic cancelled consent')
    first.apply_bridge = denied
    controller = UpdateController(first, settings=settings)
    controller.bind_window(window); controller.run('apply_and_restart')
    until(lambda: not controller.busy)
    assert settings.value(controller.key + 'attempted_release')
    controller.shutdown()
    calls = []
    second = feed.service(); second.apply_bridge = lambda *_: calls.append(True)
    controller = UpdateController(second, settings=settings)
    controller.bind_window(window); controller.start()
    until(lambda: not controller.busy and second.snapshot['state'] == 'UPDATE_AVAILABLE')
    assert calls == [] and not controller._resume
    controller.shutdown()


def test_logoff_after_base_exit_does_not_elevate_or_relaunch(feed, tmp_path, monkeypatch):
    from scripts import update_runtime as runtime
    from desktop.update_protocol import canonical, digest
    service = feed.service(); service.check(); service.download()
    root, stage = feed.install, service.staged
    (root/runtime.trust.RECEIPT).write_bytes(canonical({'binding': {'version': '1.1.0'}}))
    intent = dict(schema='mentorflow.update-intent.v1', app_id=runtime.configured().app_id,
        install_root=str(root), staged_app=str(stage), release_sha256=digest(service.release.envelope),
        base_pid=123, base_hwnd_hex='0'*16)
    path = tmp_path/'intent.json'; path.write_bytes(canonical(intent))
    monkeypatch.setattr(runtime, 'require_self', lambda root, high: root)
    monkeypatch.setattr(runtime.update_preflight, 'trusted_keys', lambda: feed.keys)
    monkeypatch.setattr(runtime.update_request, 'produce', lambda *_, **__: (tmp_path/'request.json', 'pin'))
    monkeypatch.setattr(runtime.update_preflight, 'accept', lambda *_, **__: ({'attempt_id':'a'*32}, None))
    class Guard:
        ending = False
        def require_running(self):
            if self.ending: raise SessionEnding('synthetic logoff')
        def close(self): pass
    guard = Guard()
    monkeypatch.setattr('desktop.update_session.UpdateSession', lambda **_: guard)
    class Peer:
        def __init__(self, *_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def require(self, **_): pass
        def visible_responsive_window(self, *_): pass
        def alive(self): return False
    monkeypatch.setattr(runtime, 'ProcessPeer', Peer)
    def closing(*_):
        guard.ending = True
        return True
    user = SimpleNamespace(PostMessageW=Mock(side_effect=closing))
    monkeypatch.setattr(runtime.c, 'WinDLL', lambda *_: user)
    elevate, launch = Mock(), Mock()
    monkeypatch.setattr(runtime, 'runas_once', elevate)
    monkeypatch.setattr(runtime, 'launch_app', launch)
    with pytest.raises(SessionEnding):
        runtime.coordinate(root, path, digest(path.read_bytes()))
    elevate.assert_not_called(); launch.assert_not_called()
    from desktop.update_session import was_session_deferred
    assert was_session_deferred(service.cache.runtime_root, digest(service.release.envelope), 123, 1)


def test_coordinator_deferral_restores_next_launch_retry(feed, host):
    from desktop.update_session import record_session_deferred, was_session_deferred
    window, settings = host
    first = feed.service(); first.check(); first.download()
    calls = []
    first.apply_bridge = lambda *_: calls.append('handed-off')
    controller = UpdateController(first, settings=settings)
    controller.bind_window(window); controller.run('apply_and_restart')
    until(lambda: not controller.busy and bool(calls))
    release_hash = settings.value(controller.key + 'pending_release')
    attempted_at = settings.value(controller.key + 'attempted_at_ns')
    controller.shutdown()  # BASE exits normally, before the session broadcast.
    assert record_session_deferred(first.staged, os.getpid(), release_hash)
    assert was_session_deferred(first.cache.runtime_root, release_hash, os.getpid(), attempted_at)
    assert not was_session_deferred(first.cache.runtime_root, release_hash, os.getpid(), time.time_ns())
    second = feed.service(); second.apply_bridge = lambda *_: calls.append('fresh-retry')
    before = sum(path == '/stable.json' for path, _ in feed.requests)
    controller = UpdateController(second, settings=settings)
    controller.bind_window(window); controller.start()
    until(lambda: 'fresh-retry' in calls and not controller.busy)
    assert sum(path == '/stable.json' for path, _ in feed.requests) >= before + 2
    controller.shutdown()


def test_invalid_deferral_hint_is_not_a_startup_failure(tmp_path, monkeypatch):
    import json
    from desktop.update_session import was_session_deferred, record_session_deferred
    from desktop.update_protocol import SecurityError
    root = tmp_path/'cache'; stage = root/('a'*12)/'App'
    stage.mkdir(parents=True)
    marker = stage.parent/'ui-session-123.json'
    for data in ('{bad', '[]', json.dumps({'release':'a'*64, 'deferred_at_ns':1}), 'x'*1025):
        marker.write_text(data)
        assert not was_session_deferred(root, 'a'*64, 123, 2)
    assert not was_session_deferred(root, '../escape', 123, 2)
    monkeypatch.setattr('desktop.update_download.safe_entry', Mock(side_effect=SecurityError('test alias')))
    assert not was_session_deferred(root, 'a'*64, 123, 2)
    assert not record_session_deferred(stage, 123, 'a'*64)


@pytest.mark.parametrize('fail', [False, True])
def test_existing_transaction_commit_restart_and_rollback_order(tmp_path, fail):
    # Reuse the unchanged transaction assertions with an E:-isolated fixture.
    # The legacy case fixture hardcodes D: and cannot run under this task's
    # storage policy. No trust check in the product engine is disabled here.
    from contextlib import ExitStack
    import json
    from unittest.mock import patch
    from scripts.test_mature_update_transaction import adapters, prepare
    from scripts.test_install_process_recovery import Shortcut
    from scripts.test_update_handoff import test_commit_before_launch_before_exit_and_same_lock_recovery
    with ExitStack() as stack:
        adapters(stack)
        # Adapt the older file-backed shortcut fixture to the current read-only
        # ownership query, checking its stored target rather than returning True.
        stack.enter_context(patch.object(Shortcut, 'points_to',
            lambda link, root: link.path.exists() and json.loads(link.path.read_text()).get('root') == str(root),
            create=True))
        feed, identities = prepare(tmp_path)
        try:
            test_commit_before_launch_before_exit_and_same_lock_recovery((tmp_path, feed, identities), fail)
        finally:
            feed.close()


def test_verified_ui_handoff_only_starts_installed_coordinator(feed, host, monkeypatch):
    import json
    from desktop import update_bridge, windows_process_peer
    from desktop.update_protocol import canonical
    from scripts import windows_install_transaction as tx
    window, settings = host
    root = feed.install
    bridge = root/'UpdateBridge.exe'
    bridge.write_bytes(b'MZ synthetic installed coordinator')
    from scripts import windows_install_trust as trust
    (root/trust.RECEIPT).write_bytes(canonical({'control_hashes': {'UpdateBridge.exe': tx.sha(bridge)}}))
    def validate_root(candidate):
        assert candidate == root
    monkeypatch.setattr(update_bridge, 'configured', lambda: SimpleNamespace(app_id='MentorFlow', validate_root=validate_root))
    monkeypatch.setattr(trust, 'assert_safe_parents', lambda path: None)
    monkeypatch.setattr(trust, 'assert_protected', lambda path: None)
    expected_hwnd = f'{int(window.winId()):016x}'
    class Peer:
        def __init__(self, pid): self.pid = pid
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def close(self): pass
        def alive(self): return True
        def require(self, **kwargs):
            assert kwargs == {'executable': root/'App/MentorFlow.exe', 'integrity_rid': 8192}
        def visible_responsive_window(self, hwnd):
            assert hwnd == expected_hwnd
    monkeypatch.setattr(windows_process_peer, 'ProcessPeer', Peer)
    started = []
    def launch(args, **kwargs):
        started.append(args)
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(update_bridge.subprocess, 'Popen', launch)
    service = feed.service(); service.check(); service.download()
    controller = UpdateController(service, settings=settings)
    controller.bind_window(window); controller.run('apply_and_restart')
    until(lambda: not controller.busy and bool(started))
    assert started[0][:2] == [str(bridge), '--coordinate']
    intent = json.loads(Path(started[0][2]).read_text())
    assert intent['base_pid'] == os.getpid() and intent['staged_app'] == str(service.staged)
    assert 'Setup' not in started[0][0]
    controller.shutdown()


def test_relaunch_command_is_main_app_never_install_wizard(tmp_path, monkeypatch):
    from scripts import update_runtime
    called = []
    monkeypatch.setattr(update_runtime.subprocess, 'Popen', lambda args, **kwargs: called.append(args))
    binding = {'manifest_sha256':'a'*64, 'source_sha':'b'*40, 'tree_sha':'c'*40}
    update_runtime.launch_app(tmp_path, binding)
    assert called[0][0] == str(tmp_path/'App/MentorFlow.exe')
    assert called[0][1:3] == ['--install-root', str(tmp_path)]
    assert not any('setup' in arg.casefold() or 'wizard' in arg.casefold() for arg in called[0])


@pytest.mark.parametrize('deny', [False, True])
def test_per_user_pending_preparation_runs_in_worker_before_ready(feed, host, monkeypatch, deny):
    window, settings = host
    from scripts import per_user_install, per_user_update_core
    calls = []
    monkeypatch.setattr(per_user_install, 'is_install', lambda root: True)
    def remember(root, stage, envelope):
        calls.append(threading.current_thread().name)
        assert root == feed.install and envelope == feed.raw and stage.is_dir()
        if deny:
            raise RuntimeError('synthetic core pending validation failure')
    monkeypatch.setattr(per_user_update_core, 'remember_download', remember)
    service = feed.service(); service.check()
    controller = UpdateController(service, settings=settings)
    controller.bind_window(window); controller.run('download')
    until(lambda: bool(calls) and not controller.busy)
    assert calls == ['mentorflow-update-ui']
    assert controller.state == (UpdateState.RECOVERABLE_ERROR if deny else UpdateState.READY_TO_RESTART)
    controller.shutdown()


@pytest.mark.parametrize('ending', [False, True])
def test_per_user_adapter_uses_frozen_pending_cli_never_installer(tmp_path, monkeypatch, ending):
    from desktop import update_lifecycle, windows_process_peer
    from scripts import per_user_install, per_user_update_core
    root = tmp_path/'MentorFlow'; root.mkdir()
    monkeypatch.setattr(per_user_install, 'is_install', lambda root: True)
    monkeypatch.setattr(per_user_install, 'target', lambda path: path)
    remember = Mock()
    monkeypatch.setattr(per_user_update_core, 'remember_download', remember)
    monkeypatch.setattr(per_user_update_core, 'unsafe_teardown', lambda: ending)
    class Peer:
        def __init__(self, pid): assert pid == os.getpid()
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def require(self, **kwargs): assert kwargs == {'executable': root/'App/MentorFlow.exe', 'integrity_rid': 8192}
        def visible_responsive_window(self, hwnd): assert hwnd == 'a'*16
    monkeypatch.setattr(windows_process_peer, 'ProcessPeer', Peer)
    launch = Mock(return_value=SimpleNamespace(pid=123))
    monkeypatch.setattr(update_lifecycle.subprocess, 'Popen', launch)
    if ending:
        with pytest.raises(SessionEnding): update_lifecycle.handoff(root, tmp_path/'App', b'fixture', hwnd_hex='a'*16)
        launch.assert_not_called()
    else:
        result = update_lifecycle.handoff(root, tmp_path/'App', b'fixture', hwnd_hex='a'*16)
        assert result['bridge_pid'] == 123 and result['update_uac_count'] == 0
        assert launch.call_args.args[0] == [str(root/'Updater/UserRuntime.exe'), 'apply-pending',
            '--root', str(root), '--pid', str(os.getpid()), '--hwnd-hex', 'a'*16]
        assert launch.call_args.kwargs['creationflags'] == update_lifecycle.subprocess.CREATE_NO_WINDOW
    remember.assert_called_once_with(root, tmp_path/'App', b'fixture')
