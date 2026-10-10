"""Offline timing/race contracts; no installed App or external feed is run."""
from contextvars import ContextVar
from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
from refactor.desktop.updates import DesktopUpdates
from refactor.desktop.delivery import per_user_launcher as launcher
from refactor.desktop.delivery.update_protocol import SecurityError


@pytest.fixture
def app96():
    from PySide6.QtGui import QFontDatabase
    app = QApplication.instance() or QApplication([])
    # The headless Qt platform otherwise has no Chinese fallback font.
    QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\msyh.ttc')
    return app


def pump(app, until):
    deadline = time.monotonic() + 3
    while not until() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.002)
    assert until()


class Feed96:
    def __init__(self, state='OPTIONAL_UPDATE', cached='HEALTHY'):
        self.state, self.cached = state, cached
        self.entered, self.release = threading.Event(), threading.Event()
    def attach_shutdown_guard(self, app): pass
    def get_startup_update_state(self, root, *, full, check_feed, cancel):
        if check_feed:
            self.entered.set()
            while not self.release.wait(.005):
                if cancel(): break
        state = self.state if check_feed else self.cached
        return dict(state=state, login_allowed=state != 'MANDATORY_UPDATE',
                    current='2.0.5', latest='2.0.6', feed='VERIFIED' if check_feed else 'NOT_CHECKED',
                    mandatory_floor='2.0.6', message='有新版本可用，可以现在更新，或稍后在 设置 → 关于 更新。')


@pytest.mark.parametrize('logged_in', [False, True])
def test_startup96_pending_network_does_not_block_login_and_offer_uses_current_window(app96, tmp_path, monkeypatch, logged_in):
    from refactor.desktop.delivery import per_user_install
    monkeypatch.setattr(per_user_install, 'schedule_updater_refresh', lambda _: None)
    gate, home = QWidget(), QWidget()
    gate.show(); feed = Feed96()
    owner = SimpleNamespace(home=None)
    ui = DesktopUpdates(app96, tmp_path, gate, core=feed, login_handoff=owner)
    try:
        ui.start()
        pump(app96, lambda: feed.entered.is_set() and gate.isEnabled())
        assert ui.snapshot()['busy'] and ui.state['login_allowed']
        if logged_in:
            owner.home = home; gate.hide(); home.show()
        feed.release.set()
        pump(app96, lambda: not ui.snapshot()['busy'])
        assert ui._prompt_parent() is (home if logged_in else gate)
        if logged_in:
            # Existing React UpdateNotice owns the main-window presentation.
            assert ui._prompt is None
            assert '继续登录' not in ui.snapshot()['message']
            assert '关于' in ui.snapshot()['message']
        else:
            assert ui._prompt.isVisible() and ui._prompt.parentWidget() is gate
            assert '继续登录' in ui._prompt.message.text()
            assert ui._prompt.install.text() == '立即更新并重启'
            ui._prompt.grab().save(str(tmp_path/'login-update96.png'))
            ui._prompt.later.click()
            assert ui.snapshot()['deferred'] and gate.isEnabled() and not ui._prompt.isVisible()
    finally:
        feed.release.set(); ui.close(); gate.close(); home.close()


def test_startup96_cached_mandatory_cannot_be_bypassed_while_refreshing(app96, tmp_path, monkeypatch):
    from refactor.desktop.delivery import per_user_install
    monkeypatch.setattr(per_user_install, 'schedule_updater_refresh', lambda _: None)
    gate = QWidget(); gate.show()
    feed = Feed96('MANDATORY_UPDATE', 'MANDATORY_UPDATE')
    ui = DesktopUpdates(app96, tmp_path, gate, core=feed)
    try:
        ui.start(); pump(app96, lambda: ui._prompt is not None)
        assert not gate.isEnabled() and ui.state['mandatory']
        assert not ui._prompt.later.isVisible()
        feed.release.set(); pump(app96, lambda: not ui.snapshot()['busy'])
        assert ui._prompt.install.isEnabled() and ui._prompt.install.text() == '立即更新'
        ui.run('defer'); ui._prompt.reject()
        assert not ui.snapshot()['deferred'] and ui._prompt.isVisible()
    finally:
        feed.release.set(); ui.close(); gate.close()


def test_startup96_worker_keeps_session_context_and_gui_responsive(app96, tmp_path, monkeypatch):
    root = tmp_path/'MentorFlow'; root.mkdir()
    guard = ContextVar('startup96_guard', default=False)
    token = guard.set(True); ticks = []; windows = []
    timer = QTimer(); timer.setInterval(5); timer.timeout.connect(lambda: ticks.append(1))
    def checked(root, mark):
        assert guard.get() is True
        assert threading.current_thread() is not threading.main_thread()
        time.sleep(.08)
        mark('app_visible_healthy')
        return dict(status='HEALTHY', health=dict(ready=True))
    def capture():
        windows.extend(w.windowTitle() for w in app96.topLevelWidgets() if w.isVisible())
    QTimer.singleShot(15, capture)
    monkeypatch.setattr(launcher, '_launch_checked', checked)
    try:
        timer.start()
        assert launcher.launch(root)['health']['ready']
        assert len(ticks) >= 3 and 'MentorFlow' in windows
        assert not any(w.windowTitle() == 'MentorFlow' and w.isVisible() for w in app96.topLevelWidgets())
        assert (root/'Data/logs/launcher-startup.jsonl').is_file()
    finally:
        timer.stop(); guard.reset(token)


def test_startup96_failure_is_never_reported_as_healthy_and_repair_stays_on_gui_thread(app96, tmp_path, monkeypatch):
    def damaged(root, mark): raise SecurityError('invalid signed payload')
    monkeypatch.setattr(launcher, '_launch_checked', damaged)
    with pytest.raises(SecurityError, match='invalid signed payload'): launcher.launch(tmp_path)
    def repair_needed(root, mark): raise launcher._NeedsRepair()
    monkeypatch.setattr(launcher, '_launch_checked', repair_needed)
    def repair(root):
        assert threading.current_thread() is threading.main_thread()
        return dict(status='REPAIR')
    monkeypatch.setattr(launcher, 'repair_update_shell', repair)
    assert launcher.launch(tmp_path)['status'] == 'REPAIR'
