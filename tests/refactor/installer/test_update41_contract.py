"""Unsigned publisher and branded startup regressions; no real account/install."""
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import time
import pytest
from PySide6.QtWidgets import QApplication, QWidget
from tests.refactor.delivery.test_delivery_mature_update import feed
from refactor.desktop.delivery.update_protocol import (verify_release, mandatory_floor,
    require_monotonic_floor, startup_version_policy, SecurityError)
from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.updates import DesktopUpdates
from refactor.desktop.update_dialog import UpdateDialog
from scripts.build_learning_installer import release_policy


def verified(feed, version, epoch, **policy):
    signed = feed.publish(version, feed.old_files)
    signed.update(metadata_version=epoch, **policy)
    return verify_release(feed.sign(signed), feed.keys, current='0.0.0')


def test_update41_intermediate_mandatory_propagates_and_never_lowers(feed):
    base = verified(feed, '2.0.2', 1, min_supported_version='2.0.0')
    p3 = release_policy('2.0.3', base, mandatory=True, reason='protocol')
    r3 = verified(feed, '2.0.3', 2, **p3)
    p4 = release_policy('2.0.4', r3)
    r4 = verified(feed, '2.0.4', 3, **p4)
    assert p4['mandatory'] is False and mandatory_floor(r4) == '2.0.3'
    assert startup_version_policy(r4, '2.0.2') == 'MANDATORY_UPDATE'
    assert startup_version_policy(r4, '2.0.3') == 'OPTIONAL_UPDATE'
    require_monotonic_floor(r4, r3)
    with pytest.raises(ValueError, match='下调'): release_policy('2.0.4', r3, minimum='2.0.0')
    lowered = verified(feed, '2.0.4', 3, min_supported_version='2.0.0')
    with pytest.raises(SecurityError, match='downgrade'): require_monotonic_floor(lowered, r3)
    with pytest.raises(ValueError, match='原因'): release_policy('2.0.5', r4, mandatory=True)
    # The ordinary checking service must publish the inherited floor, not just
    # the latest release's False tag. Its local rows are an inert substitute.
    feed.sign(r4.signed)
    service = feed.service('floor-service')
    service.current = '2.0.2'
    service._local_rows = lambda: {}
    try:
        s = service.check(manual=True)
        assert s['mandatory'] and s['mandatory_floor'] == '2.0.3'
        service.current = '2.0.3'
        s = service.check(manual=True)
        assert not s['mandatory'] and s['mandatory_floor'] == '2.0.3'
    finally: service.close()


def test_update41_prelogin_gate_progress_and_no_defer(tmp_path):
    app = QApplication.instance() or QApplication([])
    gate = QWidget(); gate.show()
    class Core:
        def attach_shutdown_guard(self, app): pass
        def get_startup_update_state(self, root, full, check_feed, cancel=None):
            # Cached signed policy is checked before refreshing the feed.
            return dict(state='MANDATORY_UPDATE', login_allowed=False, current='2.0.2', latest='2.0.4',
                mandatory_floor='2.0.3', mandatory_reason='protocol', release_notes='升级说明')
    adapter = DesktopUpdates(app, tmp_path, gate, core=Core())
    try:
        adapter.start()
        until = time.monotonic()+2
        while adapter._prompt is None and time.monotonic()<until:
            app.processEvents(); time.sleep(.005)
        assert not gate.isEnabled() and adapter._prompt.isVisible()
        assert adapter._prompt.install.isEnabled() and not adapter._prompt.later.isEnabled()
        assert '版本 2.0.3 必须安装' in adapter._prompt.reason.text()
        adapter.run('defer'); adapter._prompt.reject()
        assert not adapter.snapshot()['deferred'] and adapter._prompt.isVisible()
        adapter._busy=True
        adapter._receive({'progress':dict(downloaded_bytes=10,expected_download_bytes=100)})
        assert adapter._prompt.progress.value()==100 and not adapter._prompt.later.isEnabled()
        adapter._receive({'action':'install','result':dict(state='FAILED_RECOVERABLE')})
        assert not gate.isEnabled() and not adapter._prompt.later.isEnabled()
    finally: adapter.close(); gate.close()


def test_update41_optional_and_repair_use_theme():
    app = QApplication.instance() or QApplication([])
    dialog = UpdateDialog()
    try:
        dialog.update_status(dict(state='OPTIONAL_UPDATE',current='2.0.3',latest='2.0.4'))
        assert dialog.later.isEnabled()
        assert 'border-radius: 18px' in dialog.styleSheet()
        dialog.update_status(dict(state='REPAIR_REQUIRED',message='安装需要修复'))
        assert '安装检查' in dialog.heading.text() and dialog.install.text()=='修复安装'
    finally: dialog.hide(); dialog.deleteLater(); app.processEvents()


def test_update41_automatic_and_late_login_cannot_bypass_mandatory_gate():
    from refactor.desktop.account_shell_main import AccountShell
    state={};adapter=SimpleNamespace(state=state)
    calls=[]
    shell=SimpleNamespace(app=SimpleNamespace(_mentorflow_updates=adapter),_closing=False,
        _discard_login_result=lambda result:calls.append('discard'),
        _begin_remembered_login=lambda:calls.append('resume'))
    AccountShell._begin_remembered_login(shell)
    assert shell._update_auto_pending and not calls
    state.update(login_allowed=False,mandatory=True)
    AccountShell._begin_interactive_login(shell)
    AccountShell._accept_login(shell,object())
    AccountShell._resume_update_login(shell,state)
    assert calls==['discard'] and shell._update_auto_pending
    state.update(login_allowed=True,mandatory=False)
    AccountShell._resume_update_login(shell,state)
    assert calls==['discard','resume'] and not shell._update_auto_pending


def test_update41_launch_grant_precedes_popen_and_normal_startup(monkeypatch):
    import ctypes
    calls=[]
    monkeypatch.setattr(ctypes.windll.user32, 'AllowSetForegroundWindow', lambda n: calls.append(n.value) or 1)
    options = install.app_launch_options()
    assert calls == [0xffffffff]
    assert options['startupinfo'].dwFlags & install.subprocess.STARTF_USESHOWWINDOW
    assert options['startupinfo'].wShowWindow==1
    # Guard the actual launch seam too: do not merely test an unused helper.
    source=Path(install.__file__).read_text(encoding='utf-8')
    assert 'cwd=app, close_fds=True, **app_launch_options()' in source


def test_update41_launch_health_passes_permission_and_startup_to_child(tmp_path, monkeypatch):
    import ctypes
    from refactor.desktop.delivery import windows_update_pipe, windows_process_peer, per_user_update_core
    events=[]; command=[]; options={}
    def popen(args, **kwargs):
        assert events==['grant']
        events.append('spawn');command.extend(args);options.update(kwargs)
        return SimpleNamespace(pid=77)
    class Listener:
        def __init__(self,*args): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def accept(self,**kwargs): return 77
        def receive(self,**kwargs):
            return dict(attempt_id=command[command.index('--update-attempt')+1],
                nonce=command[command.index('--update-nonce')+1],version='2.0.4',
                manifest_sha256=install.digest(b'manifest'),hwnd_hex='0000000000000001')
        def send(self,value): pass
    class Peer:
        def __init__(self,*args): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def require(self,**kwargs): pass
        def visible_responsive_window(self,hwnd): pass
    monkeypatch.setattr(ctypes.windll.user32,'AllowSetForegroundWindow',lambda flags:events.append('grant'))
    monkeypatch.setattr(windows_update_pipe,'Listener',Listener)
    monkeypatch.setattr(windows_process_peer,'ProcessPeer',Peer)
    monkeypatch.setattr(per_user_update_core,'unsafe_teardown',lambda:False)
    monkeypatch.setattr(install,'app_path',lambda root:tmp_path/'App')
    monkeypatch.setattr(install.subprocess,'Popen',popen)
    result=install.launch_health(tmp_path,SimpleNamespace(version='2.0.4',manifest_bytes=b'manifest'))
    assert result['ready'] and events==['grant','spawn']
    assert options['close_fds'] and options['startupinfo'].wShowWindow==1


def test_update41_repair_window_cancel_and_same_theme(tmp_path):
    from refactor.desktop.delivery.repair_shell import RepairWindow
    app=QApplication.instance() or QApplication([])
    window=RepairWindow(tmp_path,None,None)
    try:
        assert 'border-radius: 18px' in window.styleSheet()
        window.busy=True;window.reject()
        assert window.cancel.is_set() and window.closing
        window.receive({'result':dict(state='FAILED_RECOVERABLE')})
        assert not window.busy
    finally: window.pool.shutdown(wait=False);window.hide();app.processEvents()


def test_update41_installer_shortcuts_preserve_existing_and_honor_finish_choice():
    root=Path(__file__).resolve().parents[3]/'refactor/desktop/packaging/inno'
    script=(root/'MentorFlow.iss').read_text(encoding='utf-8')
    finish=(root/'finish-options.iss').read_text(encoding='utf-8')
    pages=(root/'branded-pages.iss').read_text(encoding='utf-8')
    desktop=next(l for l in script.splitlines() if l.startswith('Name: "desktopicon"'))
    assert 'unchecked' not in desktop
    root_link=next(l for l in script.splitlines() if l.startswith('Name: "{app}\\{#ShortcutName}"'))
    assert 'Updater\\UserRuntime.exe' in root_link and 'launch --root' in root_link
    assert 'Check: NewInstallationShortcuts' in root_link
    icons=script.split('[Icons]')[1].split('[Tasks]')[0]
    assert all('Check: NewInstallationShortcuts' in l or 'Check: CanonicalShortcutNeeded' in l
               for l in icons.splitlines() if l.startswith('Name:'))
    assert 'if not NewInstallationShortcuts then Exit;' not in finish
    assert "WizardSelectTasks('desktopicon')" in pages


def test_update41_local_release_driver_flags_and_shared_policy(monkeypatch):
    path=Path('D:/MentorFlow/.local/release-tools/build_release.py')
    if not path.is_file(): pytest.skip('local build driver not installed')
    source=path.read_text(encoding='utf-8');compile(source,str(path),'exec')
    assert "parser.add_argument('--mandatory',action='store_true')" in source
    assert 'policy=build.release_policy(' in source
    assert 'previous_release=previous_snapshot' in source
