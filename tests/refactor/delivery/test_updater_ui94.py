"""Repair close/cancel boundaries and Inno opt-in shortcut contracts."""
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from types import MethodType

import pytest
from PySide6.QtWidgets import QApplication

from refactor.desktop.delivery.repair_shell import RepairWindow
from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_update_core as core
from tests.refactor.delivery.test_updater_refresh94 import runtime94


@pytest.mark.parametrize('state', ['HEALTHY', 'OPTIONAL_UPDATE', 'MANDATORY_UPDATE', 'FATAL_UNRECOVERABLE'])
def test_updater94_app_schedules_only_after_startup_classification(monkeypatch, tmp_path, state):
    from refactor.desktop.updates import DesktopUpdates
    calls = []
    monkeypatch.setattr(install, 'schedule_updater_refresh', lambda root: calls.append(('refresh', root)))
    monkeypatch.setattr(install, 'maintenance_log', lambda *_a, **_k: None)
    def publish(value): calls.append(('classified', value['state']))
    ui = SimpleNamespace(_closed=False, _busy=True, state={}, root=tmp_path,
                         _startup_pointer_policy=None,
                         _background_preparing=False,
                         _background_retry=SimpleNamespace(isActive=lambda: False),
                         _publish=publish, gate=SimpleNamespace(setEnabled=lambda _: None),
                         _login_wait_timer=SimpleNamespace(stop=lambda: None),
                         _startup_started=time.monotonic(), _next_automatic=0,
                         _show_prompt=lambda: None, _update_prompt=lambda: None,
                         _prepare_in_background=lambda: calls.append(('prepare', tmp_path)))
    ui._apply_startup_result = MethodType(DesktopUpdates._apply_startup_result, ui)
    DesktopUpdates._receive(ui, dict(action='startup', result=dict(state=state, login_allowed=state=='HEALTHY')))
    assert calls[0] == ('classified', state)
    expected = [] if state=='FATAL_UNRECOVERABLE' else [('refresh', tmp_path)]
    if state in ('OPTIONAL_UPDATE','MANDATORY_UPDATE'):
        expected.append(('prepare', tmp_path))
    assert calls[1:] == expected


@pytest.fixture
def qt94():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('state', ['REPAIR_REQUIRED', 'CHECKING', 'DOWNLOADING', 'FAILED_SECURITY', 'MANDATORY_UPDATE'])
def test_updater94_close_enabled_in_every_noncritical_state(qt94, tmp_path, state):
    window = RepairWindow(tmp_path, None, None)
    try:
        window.show(); window.busy = True
        window.update_status(dict(state=state, mandatory=True, busy=True))
        assert window.later.text() == '关闭' and window.later.isEnabled()
        window.later.click()
        assert not window.isVisible() and window.closing and window.cancel.is_set()
    finally:
        window.pool.shutdown(wait=False, cancel_futures=True); window.hide()


def test_updater94_critical_close_explains_wait_and_reenables(qt94, tmp_path):
    window = RepairWindow(tmp_path, None, None)
    try:
        window.show(); window.busy = True
        window.report(dict(critical=True, state='APPLYING'))
        qt94.processEvents()
        assert not window.later.isEnabled()
        assert '正在处理，请稍候' in window.message.text()
        window.reject()
        assert window.isVisible() and not window.closing
        window.report(dict(critical=False)); qt94.processEvents()
        assert window.later.isEnabled()
        window.later.click()
        assert not window.isVisible()
    finally:
        window.pool.shutdown(wait=False, cancel_futures=True); window.hide()


def test_updater94_close_during_slow_check_does_not_wait_for_worker(qt94, tmp_path):
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        started.set()
        try:
            assert release.wait(5)
            assert kwargs['cancel']()
            return dict(state='FAILED_RECOVERABLE')
        finally: done.set()
    window = RepairWindow(tmp_path, SimpleNamespace(check_latest=slow),
                          SimpleNamespace(is_install=lambda _: True))
    try:
        window.show(); window.run('check')
        assert started.wait(2)
        assert window.later.isEnabled()
        window.later.click()
        assert not window.isVisible() and not done.is_set()
    finally:
        release.set(); assert done.wait(3)
        window.pool.shutdown(wait=True); window.hide()


def test_updater94_repair_failure_gives_download_recovery_path(qt94, tmp_path):
    window = RepairWindow(tmp_path, None, None)
    try:
        window.receive({'result': dict(state='FAILED_SECURITY')})
        assert 'https://aiteacher.clannad.win/download' in window.message.text()
        assert '覆盖安装' in window.message.text() and window.later.isEnabled()
        window.grab().save(str(tmp_path / 'repair-recovery94.png'))
    finally:
        window.pool.shutdown(wait=False); window.hide()


@pytest.mark.parametrize('cancel_at_boundary', [False, True])
def test_updater94_repair_core_reports_critical_only_at_swap(runtime94, monkeypatch, cancel_at_boundary):
    f = runtime94
    release = install.historical(f.raw, f.feed.keys)
    events = []
    cancelled = False
    class Service:
        install_identity = 'test'
        current = '2.0.3'
        def __init__(self): self.release = release
        def check(self, **kwargs): return dict(state='UPDATE_AVAILABLE')
        def download(self, **kwargs): return dict(state='READY_TO_APPLY')
        def apply_and_restart(self): return self.apply_bridge(f.root, f.source, f.raw)
    monkeypatch.setattr(install, 'launch_health', f.ready)
    monkeypatch.setattr(install, 'require_app_stopped', lambda _: events.append('swap-boundary'))
    def progress(value):
        nonlocal cancelled
        if 'critical' in value:
            events.append(value['critical'])
            if value['critical'] and cancel_at_boundary: cancelled = True
    result = core.repair_installation(f.root, service=Service(), progress=progress, cancel=lambda: cancelled)
    if cancel_at_boundary:
        assert result['state'] == 'FAILED_RECOVERABLE'
        assert install.installed(f.root).version == '2.0.3'
        assert events == [True, False]
    else:
        assert result['state'] == 'CURRENT_HEALTHY'
        assert events == [True, 'swap-boundary', False]


def test_updater94_inno_finish_options_honor_upgrades_and_preserve_foreign_desktop():
    root = Path(__file__).resolve().parents[3] / 'refactor/desktop/packaging/inno'
    finish = (root / 'finish-options.iss').read_text(encoding='utf-8')
    action = finish.split('function ApplyFinishOptions: String;', 1)[1].split('procedure RemoveOwnedFinishOptions', 1)[0]
    assert 'if not NewInstallationShortcuts then Exit;' not in action
    assert "if WizardIsTaskSelected('desktopicon') then" in action
    assert 'if FileExists(DesktopShortcutPath)' in action and 'if not OwnsDesktopShortcut' in action
    assert 'CopyFile(SourceLink, DesktopShortcutPath, True)' in action  # fail if exists, never overwrite.
    existing_case = action.split('if FileExists(DesktopShortcutPath) then begin', 1)[1].split(
        'end else begin', 1
    )[0]
    assert '未覆盖' in action and 'DeleteFile(DesktopShortcutPath)' not in existing_case
    script = (root / 'MentorFlow.iss').read_text(encoding='utf-8')
    canonical = next(line for line in script.splitlines() if line.startswith('Name: "{autoprograms}\\{#ShortcutName}";'))
    assert 'Check: CanonicalShortcutNeeded' in canonical
    assert 'not FileExists' in finish.split('function CanonicalShortcutNeeded', 1)[1].split('end;', 1)[0]
    adapter = (root / 'per-user-core.iss').read_text(encoding='utf-8')
    assert "Pos('请先关闭 MentorFlow', Problem)" in adapter
