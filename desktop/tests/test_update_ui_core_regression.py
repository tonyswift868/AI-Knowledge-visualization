"""Existing core/session regressions retained when the UI moved to one click.

Synthetic fixtures only; process launches are replaced by recording fakes.
"""
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMainWindow

from desktop.update_controller import UpdateController
from desktop.update_session import UpdateSession, SessionEnding
from desktop.tests.test_update_ui_flow import feed, until, no_external_launch


@pytest.fixture
def host(tmp_path):
    window = QMainWindow()
    window.show()
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    yield window, settings
    window.close()


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


def test_one_click_card_retains_existing_signed_transaction_fixture(tmp_path, monkeypatch):
    # Adapt only the test's storage/shortcut/process boundaries; the existing
    # signed apply/rollback implementation remains unchanged.
    from contextlib import ExitStack
    import json
    from unittest.mock import patch
    from scripts import test_mature_update_transaction as original
    from scripts.test_install_process_recovery import Shortcut
    with ExitStack() as stack:
        original.adapters(stack)
        stack.enter_context(patch.object(Shortcut, 'points_to',
            lambda link, root: link.path.exists() and json.loads(link.path.read_text()).get('root') == str(root),
            create=True))
        value, identities = original.prepare(tmp_path)
        try:
            monkeypatch.setattr(original, 'restart_fixture',
                lambda root: json.loads((root/'App/mentorflow-version.json').read_bytes())['version'])
            original.test_update_card_check_download_apply_fixture_handoff((tmp_path, value, identities))
        finally:
            value.close()
