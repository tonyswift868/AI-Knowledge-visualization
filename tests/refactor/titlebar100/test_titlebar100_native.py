"""Caption interaction and update gates: offline, no real account/updater."""
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from PySide6.QtCore import Qt, QSettings, QPoint, QEvent, QPointF
from PySide6.QtGui import QEnterEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from refactor.desktop.login_shell import LoginGateWindow, LoginGateState
from refactor.desktop.account_shell_main import AccountShell
from refactor.desktop.account_identity import AccountPreferenceStore
from refactor.desktop.login_session import LoginSession
from refactor.desktop.updates import DesktopUpdates

@pytest.fixture
def app():
    result=QApplication.instance() or QApplication([])
    result.setQuitOnLastWindowClosed(False)
    return result

@pytest.fixture
def gate(app):
    result=LoginGateWindow();result.show();result.activateWindow();QTest.qWait(30)
    app.setActiveWindow(result)
    yield result
    result.close();result.deleteLater();app.processEvents()

@pytest.mark.parametrize('theme',['sakura','aurora'])
@pytest.mark.parametrize('action',['minimize','close'])
def test_titlebar100_hover_pressed_focus_and_full_hit_area(gate,theme,action):
    gate.set_loading_appearance(theme,reduced=True)
    button=getattr(gate,action+'_button')
    QApplication.sendEvent(button,QEvent(QEvent.Leave))
    normal=button.grab().toImage().pixelColor(10,10)
    QApplication.sendEvent(button,QEnterEvent(QPointF(20,16),QPointF(20,16),QPointF(button.mapToGlobal(QPoint(20,16)))))
    hover=button.grab().toImage().pixelColor(10,10)
    assert normal!=hover
    assert button.size().width()>=46 and button.size().height()>=32
    assert button.hitButton(QPoint(1,1)) and button.hitButton(button.rect().bottomRight())
    # A real button press, not a synthetic setDown implementation check.
    QTest.mousePress(button,Qt.LeftButton)
    pressed=button.grab().toImage().pixelColor(10,10)
    assert pressed!=hover and button.isDown()
    if action=='close':assert hover.red()>hover.green()*2 and pressed.red()<hover.red()
    QTest.mouseRelease(button,Qt.LeftButton,pos=QPoint(-5,-5))
    button.setFocus(Qt.TabFocusReason)
    assert button.hasFocus() and button.focusPolicy()==Qt.StrongFocus
    focused=button.grab().toImage()
    button.clearFocus()
    assert focused!=button.grab().toImage()

@pytest.mark.parametrize('reduced',[False,True])
def test_titlebar100_transition_and_reduced_motion(gate,reduced,monkeypatch):
    from refactor.desktop import native_loading
    monkeypatch.setattr(native_loading,'system_reduce_motion',lambda:False)
    gate.set_loading_appearance('sakura',reduced)
    button=gate.close_button
    QApplication.sendEvent(button,QEvent(QEvent.Leave))
    button.animation.setCurrentTime(100)
    QApplication.sendEvent(button,QEnterEvent(QPointF(20,16),QPointF(20,16),QPointF(button.mapToGlobal(QPoint(20,16)))))
    if reduced:assert button._level==1 and not button.animation.state().value
    else:
        assert button.animation.duration()==100
        button.animation.setCurrentTime(50);assert 0<button._level<1
        button.animation.setCurrentTime(100);assert button._level==1

def test_titlebar100_system_reduced_motion_is_respected(gate,monkeypatch):
    from refactor.desktop import native_loading
    monkeypatch.setattr(native_loading,'system_reduce_motion',lambda:True)
    gate.set_loading_appearance('aurora',False)
    assert gate.close_button.reduced and gate.minimize_button.reduced

class HeldCore:
    def attach_shutdown_guard(self,app):pass
    def get_startup_update_state(self,root,*,cancel,**kwargs):
        while not cancel():time.sleep(.005)
        return dict(state='FAILED_RECOVERABLE',login_allowed=False)

@pytest.mark.parametrize('phase',['startup','login','idle'])
def test_titlebar100_update_gate_and_caption_close_respond_immediately(app,tmp_path,phase,monkeypatch):
    calls=[]
    diag=NS(set_primary=lambda *a:None,request_exit=lambda reason:calls.append(reason))
    monkeypatch.setattr(app,'_mentorflow_diagnostics',diag,raising=False)
    preferences=AccountPreferenceStore(QSettings(str(tmp_path/'account.ini'),QSettings.IniFormat))
    session=LoginSession(preferences,credentials=Mock(),passwords=Mock())
    session.remembered=lambda:(None,False)
    session.take_update_login=lambda:(False,None,None)
    shell=AccountShell(app,NS(data_root=tmp_path),login_session=session,capability_fetcher=lambda:{})
    gate=shell.gate;gate.show();gate.activateWindow();shell._prewarm_timer.stop()
    gate.set_state(LoginGateState.LOGIN_READY if phase=='idle' else LoginGateState.AUTHENTICATING,
                   detail='正在启动 MentorFlow…' if phase=='startup' else '正在连接账号服务…')
    ui=DesktopUpdates(app,tmp_path/'install',gate,core=HeldCore())
    try:
        if phase=='startup':
            ui.start();app.processEvents()
            # The startup update check keeps the login surface available; its
            # AUTHENTICATING state still disables credential controls until
            # AccountShell has resolved the pending login action.
            assert gate.isEnabled() and gate.login_card.isEnabled()
            assert not gate.primary_button.isEnabled() and gate.close_button.isEnabled()
        start=time.perf_counter()
        QTest.mouseClick(gate.close_button,Qt.LeftButton)
        assert (time.perf_counter()-start)<.1
        assert calls==['USER_MAIN_CLOSE'] and not gate.isVisible()
    finally:
        ui.close();shell.close();app.aboutToQuit.disconnect(shell.close)
        gate.deleteLater();app.processEvents()

def test_titlebar100_update_policy_restores_form_without_enabling_authentication(gate):
    gate.set_state(LoginGateState.AUTHENTICATING)
    gate.set_login_available(False)
    assert gate.close_button.isEnabled() and gate.minimize_button.isEnabled()
    gate.set_login_available(True)
    assert not gate.primary_button.isEnabled() and gate.close_button.isEnabled()
    gate.set_state(LoginGateState.LOGIN_READY)
    assert gate.primary_button.isEnabled()
    gate.set_login_available(False);gate.set_state(LoginGateState.LOGIN_READY)
    assert not gate.primary_button.isEnabled()
    calls=[];gate.login_requested.connect(lambda:calls.append(True));gate._activate()
    assert not calls
    gate.set_login_available(True);assert gate.primary_button.isEnabled()

@pytest.mark.parametrize('intent',['register','forgot_password'])
def test_titlebar100_account_web_cover_does_not_cover_caption(gate,intent):
    from refactor.desktop.native_loading import WebFirstFrame
    from PySide6.QtCore import QObject,Signal
    class Page(QObject):
        loadFinished=Signal(bool)
        def runJavaScript(self,*args):pass
    content=QWidget(gate);content.view=QWidget(content);content.page=Page(content)
    gate.set_auth_content(content);cover=WebFirstFrame(content)
    gate.set_state(LoginGateState.AUTHENTICATING);QTest.qWait(20)
    for button in (gate.minimize_button,gate.close_button):
        position=button.mapToGlobal(button.rect().center())
        assert QApplication.widgetAt(position) is button
        assert not cover.cover.geometry().contains(content.mapFromGlobal(position))
    gate.close_button.click();assert not gate.isVisible()

def test_titlebar100_keyboard_space_minimizes(gate):
    gate.minimize_button.setFocus(Qt.TabFocusReason)
    QTest.keyClick(gate.minimize_button,Qt.Key_Space)
    assert gate.isMinimized()

def test_titlebar100_dragging_pressed_caption_out_cancels_action_and_color(gate):
    gate.set_loading_appearance('sakura',True)
    button=gate.close_button;called=[];button.clicked.connect(lambda:called.append(True))
    QApplication.sendEvent(button,QEnterEvent(QPointF(20,16),QPointF(20,16),QPointF(button.mapToGlobal(QPoint(20,16)))))
    QTest.mousePress(button,Qt.LeftButton)
    assert button.isDown() and button._level==2
    local=QPointF(-10,16);global_point=QPointF(button.mapToGlobal(QPoint(-10,16)))
    QApplication.sendEvent(button,QMouseEvent(QEvent.MouseMove,local,global_point,Qt.NoButton,Qt.LeftButton,Qt.NoModifier))
    assert not button.isDown() and button._level==0
    QTest.mouseRelease(button,Qt.LeftButton,pos=QPoint(-10,16))
    assert not called and gate.isVisible()
