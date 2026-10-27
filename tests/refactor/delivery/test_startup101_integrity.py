"""Startup optimization and installer race, on signed inert files only."""
import os
import threading
import time
from types import SimpleNamespace

import pytest
from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_update_core as core
from refactor.desktop.delivery import startup_integrity as integrity
from refactor.desktop.delivery.update_protocol import SecurityError, strict_json
from tests.refactor.delivery.test_updater_refresh94 import runtime94


@pytest.fixture(autouse=True)
def inert_gui101(monkeypatch):
    # Feed contains signed inert bytes, not Qt DLLs. Existing bootstrap tests
    # cover the real closure; these tests isolate file-cache/race behavior.
    monkeypatch.setattr(install, 'gui_configuration', lambda *_: ())


def test_startup101_normal_launch_classification_never_hashes(runtime94, monkeypatch):
    f = runtime94
    def forbidden(*args, **kwargs): pytest.fail('foreground must not hash payload/runtime/closure')
    for name in ('verify_tree', 'verify_payload', 'verify_gui', 'verify_recovery_runtime'):
        monkeypatch.setattr(install, name, forbidden)
    state = core.get_startup_update_state(f.root, full=False, check_feed=False)
    assert state['state'] == 'HEALTHY' and state['login_allowed']
    monkeypatch.setattr(install, 'launch_health', lambda *args: {'ready': True})
    from refactor.desktop.delivery.per_user_launcher import _launch_checked
    monkeypatch.setattr(install, 'require_app_stopped', lambda _: None)
    assert _launch_checked(f.root)['health']['ready']


def test_startup101_missing_record_hashes_all_then_unchanged_files_skip(runtime94, monkeypatch):
    f = runtime94
    integrity.record_path(f.root).unlink()
    release = install.installed(f.root)
    assert integrity.verify_background(f.root)['rehashed'] == len(release.rows)
    monkeypatch.setattr(integrity, 'sha256_file', lambda _: pytest.fail('unchanged App must not rehash'))
    assert integrity.verify_background(f.root)['rehashed'] == 0
    assert not integrity.record_path(f.root).is_relative_to(f.root / 'App')


@pytest.mark.parametrize('same_size', [False, True])
def test_startup101_modified_size_or_time_is_detected(runtime94, same_size):
    f = runtime94
    item = f.root / 'App/MentorFlow.exe'
    data = item.read_bytes()
    item.write_bytes(b'X' * (len(data) if same_size else len(data)+1))
    os.utime(item, ns=(item.stat().st_atime_ns, item.stat().st_mtime_ns + 1000000))
    with pytest.raises(SecurityError, match='content mismatch'):
        integrity.verify_background(f.root)


def test_startup101_update_refreshes_record_before_visible_health(runtime94):
    f = runtime94
    def health(root, release):
        # No integrity work is awaited by the actual window/pipe proof.
        assert (root / install.UPDATER_RELEASE).read_bytes() == f.raw
        assert not (root / install.UPDATER_SWAP).exists()
        assert core.get_startup_update_state(root, full=False, check_feed=False)['login_allowed']
        return {'ready': True}
    assert install.transact(f.root, f.source, f.raw, health=health,
                            refresh_runtime=True)['status'] == 'HEALTHY'
    assert integrity.verify_background(f.root)['rehashed'] == 0


def test_startup101_background_waits_for_install_writer_without_false_damage(runtime94, monkeypatch):
    from refactor.desktop.delivery.update_download import InstallUpdateLock
    from refactor.desktop.delivery.update_protocol import RecoverableError
    f = runtime94; result = {}
    contention_seen = threading.Event()
    original_enter = InstallUpdateLock.__enter__

    def observe_contention(lock):
        try:
            return original_enter(lock)
        except RecoverableError:
            # Signal only after the background worker actually attempted the
            # OS lock and observed the install writer holding it.
            contention_seen.set()
            raise

    monkeypatch.setattr(InstallUpdateLock, '__enter__', observe_contention)
    with InstallUpdateLock(f.root):
        worker = threading.Thread(target=lambda: result.update(integrity.verify_background(f.root)))
        worker.start()
        saw_contention = contention_seen.wait(1)
        waiting_before_release = worker.is_alive() and not result
    worker.join(3)
    # Release the writer before assertions so even a failed handshake leaves
    # no test-owned background thread waiting on the fixture.
    assert saw_contention
    assert waiting_before_release
    assert not worker.is_alive() and result['rehashed'] == 0


def test_startup101_damage_hint_is_nonfatal_and_uses_existing_repair(runtime94, monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
    f = runtime94
    app = QApplication.instance() or QApplication([])
    gate = QWidget(); gate.show(); actions = []
    # QApplication is shared with later account tests; never leave a repair
    # adapter behind, which would correctly prevent those shells from logging in.
    monkeypatch.setattr(app, '_mentorflow_updates',
                        SimpleNamespace(state={}, run=lambda *args: actions.append(args)), raising=False)
    monkeypatch.setattr(app, '_mentorflow_integrity', None, raising=False)
    def damaged(_): raise SecurityError('changed file')
    monkeypatch.setattr(integrity, 'verify_background', damaged)
    integrity.schedule(f.root, app, SimpleNamespace(home=None, gate=gate)).join(1)
    deadline = time.monotonic()+2
    while not hasattr(app._mentorflow_integrity, 'dialog') and time.monotonic() < deadline:
        app.processEvents()
    dialog = app._mentorflow_integrity.dialog
    assert gate.isVisible() and dialog.text() == '安装文件有损坏，请修复'
    next(b for b in dialog.buttons() if b.text() == '修复').click()
    assert actions[0][0] == 'install' and app._mentorflow_updates.state['shell'] == 'repair'
    gate.close(); dialog.close()
    app._mentorflow_integrity.deleteLater()


def test_startup101_packaged_entry_does_not_wait_for_full_integrity(runtime94, monkeypatch):
    import sys
    from refactor.desktop import packaged_main, account_shell_main
    f = runtime94
    monkeypatch.setattr(sys, 'argv', ['MentorFlow.exe','--install-mode','per_user','--install-root',str(f.root)])
    monkeypatch.setattr(sys, 'executable', str(f.root/'App/MentorFlow.exe'))
    for name in ('verify_payload', 'verify_gui'):
        monkeypatch.setattr(install, name, lambda *_a, **_k: pytest.fail('pre-login whole-file check'))
    seen = []
    monkeypatch.setattr(account_shell_main, 'main', lambda **kw: seen.append(kw) or 0)
    assert packaged_main.main() == 0
    assert seen[0]['install_root'] == f.root


def test_startup101_native_splash_pumps_messages_until_real_health(tmp_path, monkeypatch):
    from refactor.desktop.delivery import per_user_launcher as launcher, startup_window
    monkeypatch.setattr(startup_window, 'has_native_splash', lambda: True)
    closed = []; pumps = []
    monkeypatch.setattr(startup_window, 'close_native_splash', lambda: closed.append(1))
    monkeypatch.setattr(startup_window, 'pump_native_messages', lambda: pumps.append(1))
    def checked(root, mark):
        time.sleep(.04); mark('app_visible_healthy'); return dict(health={'ready': True})
    monkeypatch.setattr(launcher, '_launch_checked', checked)
    assert launcher.launch(tmp_path)['health']['ready']
    assert closed == [1] and len(pumps) > 1


def test_startup101_metadata_cache_uses_bytes_and_keys_not_stat(runtime94):
    f = runtime94
    release = install.installed(f.root)
    assert install.historical(f.old, f.feed.keys) is release
    altered = bytearray(f.old); altered[-10] ^= 1
    with pytest.raises((SecurityError, ValueError)):
        install.historical(bytes(altered), f.feed.keys)
    with pytest.raises(SecurityError):
        install.historical(f.old, {'wrong': b'x'*32})


def test_startup101_malformed_stat_record_falls_back_to_full_hash(runtime94):
    from refactor.desktop.delivery.update_protocol import canonical, digest
    f=runtime94
    integrity.record_path(f.root).write_bytes(canonical(dict(schema=1,release=digest(f.old),files=[])))
    assert integrity.verify_background(f.root)['rehashed'] == len(install.installed(f.root).rows)


@pytest.mark.skipif(os.name != 'nt', reason='native Windows splash')
def test_startup101_native_first_frame_without_qt():
    import ctypes
    from refactor.desktop.delivery import startup_window
    startup_window.native_splash()
    try:
        assert startup_window.has_native_splash()
        hwnd=ctypes.c_void_p(startup_window._native)
        startup_window.pump_native_messages()
        assert ctypes.windll.user32.IsWindowVisible(hwnd)
        assert not ctypes.windll.user32.IsIconic(hwnd)
    finally:
        startup_window.close_native_splash()
    assert not startup_window.has_native_splash()
