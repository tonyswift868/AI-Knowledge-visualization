"""Regression coverage for update-dialog sizing and silent lock retries."""
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from refactor.desktop.update_dialog import UpdateDialog
from refactor.desktop.updates import DesktopUpdates
from tests.refactor.installer.test_startup96_presentation import pump


@pytest.fixture
def app168():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.mark.parametrize('parent_size', [(460, 640), (360, 520)])
def test_update_dialog168_fits_parent_and_wraps_full_long_text(app168, parent_size):
    parent = QWidget(); parent.resize(*parent_size); parent.show(); app168.processEvents()
    dialog = UpdateDialog(parent)
    notes = '版本说明：' + ('这是一段需要自动换行并完整保留的更新说明。' * 35)
    message = '正在准备更新：' + ('请稍候，更新文件仍会按签名清单逐项核验。' * 30)
    dialog.update_status(dict(state='OPTIONAL_UPDATE', current='2.0.25', latest='2.0.26',
                              release_notes=notes, message=message))
    dialog.show(); app168.processEvents()
    try:
        assert dialog.width() <= parent.width()
        assert parent.frameGeometry().contains(dialog.frameGeometry())
        assert dialog.notes.wordWrap() and dialog.message.wordWrap() and dialog.versions.wordWrap()
        assert dialog.notes.text() == notes and dialog.message.text() == message
        assert dialog.details_scroll.widget().sizeHint().height() > dialog.details_scroll.viewport().height()
        assert dialog.install.isVisible() and dialog.later.isVisible()
        if parent_size[0] < 380:
            assert dialog.install.geometry().top() < dialog.later.geometry().top()
    finally:
        dialog.close(); parent.close(); app168.processEvents()


def test_update168_download_core_identifies_only_lock_busy(monkeypatch, tmp_path):
    from refactor.desktop.delivery import per_user_install, per_user_update_core as core
    from refactor.desktop.delivery.update_protocol import RecoverableError
    logs = []
    monkeypatch.setattr(per_user_install, 'maintenance_log', lambda *a, **k: logs.append((a, k)))
    class Service:
        def close(self): pass
    def busy(*args, **kwargs): raise RecoverableError('another update operation is active')
    monkeypatch.setattr(core, 'prepare_update', busy)
    result = core.download_latest(tmp_path, service=Service(), reuse_prepared=True)
    assert result['code'] == 'UPDATE_LOCK_BUSY'
    assert result['state'] == 'FAILED_RECOVERABLE'
    def other(*args, **kwargs): raise RecoverableError('signed repair release is not available')
    monkeypatch.setattr(core, 'prepare_update', other)
    result = core.download_latest(tmp_path, service=Service(), reuse_prepared=True)
    assert result.get('code') != 'UPDATE_LOCK_BUSY'


def test_update168_background_lock_conflict_retries_silently(app168, tmp_path, monkeypatch):
    from refactor.desktop import updates as updates_module
    from refactor.desktop.delivery import per_user_install
    monkeypatch.setattr(per_user_install, 'schedule_updater_refresh', lambda *_: None)
    monkeypatch.setattr(per_user_install, 'schedule_program_cleanup', lambda *a, **k: None)
    monkeypatch.setattr(updates_module, 'BACKGROUND_PREPARE_RETRY_DELAYS', (.01, .01))
    calls = 0
    second_started, release_second = threading.Event(), threading.Event()
    class Core:
        def attach_shutdown_guard(self, app): pass
        def get_startup_update_state(self, root, *, full, check_feed, cancel, on_pointer_policy=None):
            return dict(state='OPTIONAL_UPDATE', login_allowed=True,
                        current='2.0.25', latest='2.0.26', feed='VERIFIED',
                        message='有新版本可用，可以现在更新，或稍后在 设置 → 关于 更新。')
        def download_latest(self, root, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return dict(state='FAILED_RECOVERABLE', code='UPDATE_LOCK_BUSY',
                            message='不要显示的锁冲突内部状态')
            second_started.set()
            if not release_second.wait(2): raise AssertionError('second attempt was not released')
            return dict(state='UPDATE_DOWNLOADED', ui_state='READY_TO_APPLY', prepared=True)

    gate = QWidget(); gate.resize(460, 640); gate.show()
    gate.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    monkeypatch.setattr(QApplication, 'activeWindow', lambda: gate)
    ui = DesktopUpdates(app168, tmp_path, gate, core=Core())
    try:
        ui.start()
        try:
            pump(app168, second_started.is_set)
        except AssertionError as error:
            raise AssertionError(f'{error}; calls={calls}; state={ui.snapshot()}; preparing={ui._background_preparing}; attempts={ui._background_prepare_attempt}; retry_active={ui._background_retry.isActive()}') from error
        assert calls == 2
        assert '锁冲突' not in ui.snapshot().get('message', '')
        assert '失败' not in ui.snapshot().get('message', '')
        assert ui._prompt is not None and '锁冲突' not in ui._prompt.message.text()
        assert ui.snapshot().get('background_downloading')
        release_second.set()
        pump(app168, lambda: bool(ui.snapshot().get('prepared')))
        assert calls == 2 and ui.snapshot()['prepared']
    finally:
        release_second.set(); ui.close(); gate.close(); app168.processEvents()


def test_update168_background_lock_retry_budget_exhausts_without_ui_error(app168, tmp_path, monkeypatch):
    from refactor.desktop import updates as updates_module
    from refactor.desktop.delivery import per_user_install
    monkeypatch.setattr(per_user_install, 'schedule_updater_refresh', lambda *_: None)
    monkeypatch.setattr(per_user_install, 'schedule_program_cleanup', lambda *a, **k: None)
    monkeypatch.setattr(updates_module, 'BACKGROUND_PREPARE_RETRY_DELAYS', (.001,) * 6)
    logs, calls = [], []
    monkeypatch.setattr(per_user_install, 'maintenance_log', lambda *a, **k: logs.append((a, k)))
    class Core:
        def attach_shutdown_guard(self, app): pass
        def get_startup_update_state(self, root, *, full, check_feed, cancel, on_pointer_policy=None):
            return dict(state='OPTIONAL_UPDATE', login_allowed=True, current='2.0.25',
                        latest='2.0.26', feed='VERIFIED', message='有新版本可用。')
        def download_latest(self, root, **kwargs):
            calls.append(1)
            return dict(state='FAILED_RECOVERABLE', code='UPDATE_LOCK_BUSY')
    gate = QWidget(); gate.resize(460, 640); gate.show()
    gate.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    monkeypatch.setattr(QApplication, 'activeWindow', lambda: gate)
    ui = DesktopUpdates(app168, tmp_path, gate, core=Core())
    try:
        ui.start()
        try:
            pump(app168, lambda: len(calls) >= 7 and not ui._background_preparing
                 and not ui._background_retry.isActive())
        except AssertionError as error:
            raise AssertionError(f'{error}; calls={len(calls)}; attempts={ui._background_prepare_attempt}; preparing={ui._background_preparing}; timer={ui._background_retry.isActive()}; logs={logs}') from error
        assert len(calls) == 7
        assert any(args[1] == 'background_update_prepare_deferred'
                   and kwargs.get('reason') == 'update_lock_busy' for args, kwargs in logs)
        assert ui.state.get('state') == 'OPTIONAL_UPDATE'
        assert '失败' not in ui.state.get('message', '')
        assert ui._prompt is not None and '失败' not in ui._prompt.message.text()
    finally:
        ui.close(); gate.close(); app168.processEvents()

def test_update168_background_download_pause_keeps_offer_and_progress(app168, tmp_path):
    """Network failure is silent before consent; partial byte counters remain."""
    from refactor.desktop.delivery.update_service import MatureUpdateService
    # This is the exact non-cancel network-error template assembled in
    # UpdateService.download() after its cache download raises a recoverable error.
    paused = dict(state='DOWNLOAD_PAUSED_OR_INTERRUPTED',
                  message='网络不稳，已下载 120/900 字节，稍后会自动继续。',
                  downloaded_bytes=120, expected_download_bytes=900)
    assert paused['state'] in MatureUpdateService.STATES
    assert paused['state'] == 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
    assert '网络不稳' in paused['message']  # UpdateService's actual pause wording.
    class Core:
        def attach_shutdown_guard(self, _app): pass
    gate = QWidget(); ui = DesktopUpdates(app168, tmp_path, gate, core=Core())
    try:
        ui._publish({'state': 'OPTIONAL_UPDATE', 'message': '有新版本可用。'})
        ui._receive({'background_ready': paused})
        assert ui.state['message'] == '有新版本可用。'
        assert ui.state['downloaded_bytes'] == 120
        assert ui.state['expected_download_bytes'] == 900
        assert ui.state['download_state'] == 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
        assert not ui.state['background_downloading']
    finally:
        ui.close(); gate.close(); app168.processEvents()
