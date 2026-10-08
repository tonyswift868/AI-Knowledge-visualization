"""Native tier timing, actions and first rendered frame, all offline."""
from types import SimpleNamespace as NS

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from refactor.desktop.login_shell import LoginGateWindow, LoginGateState
from refactor.desktop.native_loading import NativeLoading, WebFirstFrame


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('age,tier,slow', [(.299, 'quiet', False), (.3, 'medium', False),
    (2.999, 'medium', False), (3., 'long', False), (14.999, 'long', False), (15., 'long', True)])
def test_loading88_thresholds(app, age, tier, slow):
    now = [0.]
    widget = NativeLoading(clock=lambda: now[0], reduced=True)
    widget.begin('正在连接账号服务…', retry=True, cancel=True)
    now[0] += age
    widget.advance()
    assert widget.tier == tier
    assert widget.slow is slow
    assert widget.retry_button.isHidden() is not slow
    assert widget.cancel_button.isHidden() is not slow
    widget.finish()
    assert not widget.timer.isActive()
    widget.deleteLater()


def test_loading88_progress_copy_does_not_restart_clock(app):
    now = [10.]
    w = NativeLoading(clock=lambda: now[0])
    w.begin('正在恢复上次的登录…')
    now[0] = 26.
    w.begin('正在恢复连接…', restart=False)
    assert w.slow and w.started == 10.
    assert w.label.text() == '正在恢复连接…'
    w.finish()


@pytest.mark.parametrize('theme,accent', [('sakura', '#d65a86'), ('aurora', '#9b8cff')])
def test_loading88_theme_reduced_and_real_buttons(app, theme, accent):
    now = [0.]
    w = NativeLoading(clock=lambda: now[0], theme=theme, reduced=True)
    actions = []
    w.retry_requested.connect(lambda: actions.append('retry'))
    w.cancel_requested.connect(lambda: actions.append('cancel'))
    w.begin('正在登录…', retry=True, cancel=True)
    now[0] = 16.; w.advance()
    assert w.art.isHidden() and not w.label.isHidden()
    assert w.colors['accent'] == accent
    w.retry_button.click(); w.cancel_button.click()
    assert actions == ['retry', 'cancel']
    w.finish()


def test_loading88_non_cancelable_cleanup_does_not_offer_fake_actions(app):
    w = NativeLoading(clock=lambda: 0.)
    w.begin('正在安全退出账号…')
    w.clock = lambda: 16.; w.advance()
    assert w.slow and not w.slow_label.isHidden()
    assert w.retry_button.isHidden() and w.cancel_button.isHidden()
    w.finish()


def test_loading88_gate_preserves_password_handling_and_finishes(app):
    gate = LoginGateWindow()
    gate.password_field.setText('fixture-password')
    gate.set_state(LoginGateState.AUTHENTICATING)
    assert gate.loading.active and not gate.password_field.isEnabled()
    assert gate.password_field.text() == 'fixture-password'
    gate.set_state(LoginGateState.AUTH_FAILED, detail='离线测试失败')
    assert not gate.loading.active and gate.password_field.isEnabled()
    gate.set_state(LoginGateState.LOGIN_READY)
    gate.deleteLater()


class Page(QObject):
    loadFinished = Signal(bool)
    def __init__(self):
        super().__init__(); self.callbacks = []
    def runJavaScript(self, script, callback):
        self.callbacks.append(callback)


def test_loading88_load_finished_is_not_the_first_frame(app):
    home = QWidget(); home.page = Page(); home.view = NS(reload=lambda: None)
    cover = WebFirstFrame(home)
    home.show(); app.processEvents()
    home.page.loadFinished.emit(True)
    assert not cover.cover.isHidden()
    home.page.callbacks.pop()(False)
    cover.probe(); home.page.callbacks.pop()(True)
    assert cover.cover.isHidden() and not cover.poll.isActive()
    home.close()


def test_loading88_reload_ignores_old_dom_callback(app):
    reloads = []
    home = QWidget(); home.page = Page(); home.view = NS(reload=lambda: reloads.append(1))
    cover = WebFirstFrame(home)
    cover.probe(); old = home.page.callbacks.pop()
    cover.retry(); old(True)
    assert not cover._done and reloads == [1]
    cover.probe(); home.page.callbacks.pop()(True)
    assert cover._done
    home.close()


def test_loading88_close_ignores_pending_renderer_callback(app):
    home = QWidget(); home.page = Page(); home.view = NS(reload=lambda: None)
    cover = WebFirstFrame(home)
    cover.probe(); callback = home.page.callbacks.pop()
    home.show(); app.processEvents(); home.close()
    callback(True)
    assert cover._done and not cover.poll.isActive()


def test_loading88_account_page_retry_keeps_its_copy_and_cancel(app):
    home = QWidget(); home.page = Page(); home.view = NS(reload=lambda: None)
    cover = WebFirstFrame(home, selector='body', label='正在打开账号服务…', cancel=lambda: None)
    cover.retry()
    cover.loading.clock = lambda: cover.loading.started + 16.
    cover.loading.advance()
    assert cover.loading.label.text() == '正在打开账号服务…'
    assert not cover.loading.cancel_button.isHidden()
    home.close()


def test_loading88_system_reduced_motion_overrides_animation(app, monkeypatch):
    from refactor.desktop import native_loading
    monkeypatch.setattr(native_loading, 'system_reduce_motion', lambda: True)
    w = NativeLoading(reduced=False)
    w.begin('正在启动…')
    w.clock = lambda: w.started + 4.; w.advance()
    assert w.reduced and w.art.isHidden() and not w.label.isHidden()
    w.finish()


def test_loading88_appearance_is_read_only_and_handles_bad_metadata(tmp_path):
    import json
    from refactor.desktop.native_loading import account_appearance
    assert account_appearance(tmp_path, 'student') == ('sakura', False)
    assert list(tmp_path.iterdir()) == []
    target = tmp_path/'learning/accounts/student/app-settings.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(dict(theme='aurora', reduce_motion=True)),encoding='utf-8')
    assert account_appearance(tmp_path, 'student') == ('aurora', True)
    target.write_text('[]',encoding='utf-8')
    assert account_appearance(tmp_path, 'student') == ('sakura', False)


def test_loading88_login_retry_returns_form_without_retaining_password(app, tmp_path):
    from PySide6.QtCore import QSettings
    from refactor.desktop.account_shell_main import AccountShell
    from refactor.desktop.account_identity import AccountPreferenceStore
    from refactor.desktop.login_session import LoginSession
    session = LoginSession(AccountPreferenceStore(QSettings(str(tmp_path/'account.ini'),QSettings.IniFormat)),
                           credentials=NS(), passwords=NS())
    shell = AccountShell(app, NS(data_root=tmp_path), login_session=session)
    shell.gate.password_field.setText('fixture')
    shell.gate.take_credentials()
    shell.gate.set_state(LoginGateState.AUTHENTICATING)
    shell.gate.loading.retry_button.click()
    assert shell.gate.state is LoginGateState.LOGIN_READY
    assert not shell.gate.password_field.text()
    assert shell._generation == 1
    shell.close(); shell.gate.deleteLater()


def test_loading88_actual_web_fallback_frame_is_revealed(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    script = tmp_path/'first_frame88.py'
    repo = Path(__file__).resolve().parents[3]
    script.write_text('import sys;sys.path.insert(0,'+repr(str(repo))+')\n'+'''
import time, faulthandler
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile,QWebEnginePage
from refactor.desktop.native_loading import WebFirstFrame
trace=open(Path(__file__).with_suffix('.crash'),'w',encoding='utf-8')
faulthandler.enable(trace)
events=open(Path(__file__).with_suffix('.events'),'w',encoding='utf-8')
def mark(value):events.write(value+'\\n');events.flush()
mark('create_app')
app=QApplication([]);app.setQuitOnLastWindowClosed(False)
home=QWidget();layout=QVBoxLayout(home)
profile=QWebEngineProfile(app)
home.view=QWebEngineView(home);layout.addWidget(home.view)
home.page=QWebEnginePage(profile,home.view);home.view.setPage(home.page)
cover=WebFirstFrame(home);home.show()
home.view.setHtml('<body>新界面尚未构建（缺少 web/dist）。</body>')
deadline=time.monotonic()+8
result=[]
timer=QTimer();timer.setInterval(20)
def finish():
    # Delete the page/view before its off-record profile; keep Qt's event loop
    # running while DeferredDelete events and renderer callbacks are drained.
    mark('delete_home')
    home.close();home.deleteLater()
    QTimer.singleShot(0,delete_profile)
def delete_profile():
    mark('delete_profile');profile.deleteLater()
    QTimer.singleShot(0,app.quit)
def check():
    if cover._done or time.monotonic()>=deadline:
        timer.stop()
        result.append(cover._done and cover.cover.isHidden())
        mark('frame_revealed' if result[0] else 'frame_timeout')
        finish()
timer.timeout.connect(check);timer.start()
mark('event_loop');app.exec();mark('event_loop_returned')
assert result==[True], 'native cover hid the visible fallback error'
''',encoding='utf-8')
    env=dict(os.environ, QSG_RHI_BACKEND='opengl', PYTHONDONTWRITEBYTECODE='1', TEMP=str(tmp_path),TMP=str(tmp_path))
    completed=subprocess.run([sys.executable,'-B',str(script)],env=env,cwd=repo,
                             capture_output=True,timeout=30)
    diagnostics='\n'.join(p.read_text(encoding='utf-8') for p in
                         (script.with_suffix('.events'),script.with_suffix('.crash')) if p.exists())
    assert completed.returncode == 0, completed.stderr.decode('utf-8',errors='replace')+diagnostics
