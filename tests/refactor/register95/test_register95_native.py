"""Offline native input ownership, idle prewarm, and form return regression."""
from types import SimpleNamespace as NS
import pytest
from PySide6.QtCore import QObject,QSettings,Qt,Signal,QPoint
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLineEdit,QSizePolicy
from PySide6.QtTest import QTest
from refactor.desktop.account_shell_main import AccountShell
from refactor.desktop.account_identity import AccountPreferenceStore,AccountIdentity
from refactor.desktop.login_session import LoginSession
from refactor.desktop.login_shell import LoginGateWindow,LoginGateState
from refactor.desktop.native_loading import WebFirstFrame

@pytest.fixture
def app():return QApplication.instance() or QApplication([])

class Page(QObject):
    loadFinished=Signal(bool)
    def runJavaScript(self,code,callback=None):
        if callback:callback(False)

class Pane(QWidget):
    finished=Signal(bool)
    prepared=Signal()
    def __init__(self,intent,parent=None,**kwargs):
        super().__init__(parent);self.intent=intent;self.disposed=False
        self.view=QLineEdit(self);QVBoxLayout(self).addWidget(self.view)
        self.page=Page(self);self.bridge=NS(completed_username=None)
    def activate(self,intent,theme):self.intent=intent;self.theme=theme
    def dispose(self):self.disposed=True;self.hide();self.deleteLater()
    def cancel(self):pass

@pytest.fixture
def shell(app,tmp_path,monkeypatch):
    from refactor.desktop import lifecycle_pane
    monkeypatch.setattr(lifecycle_pane,'AccountLifecyclePane',Pane)
    session=LoginSession(AccountPreferenceStore(QSettings(str(tmp_path/'account.ini'),QSettings.IniFormat)),
                         credentials=NS(),passwords=NS())
    session.remembered=lambda:(None,False)
    result=AccountShell(app,NS(data_root=tmp_path),login_session=session,capability_fetcher=lambda:{})
    result.gate.set_account_capabilities({'register':True,'forgot_password':True})
    result.gate.show();app.processEvents();result._prewarm_timer.stop()
    yield result
    result.close();result.gate.deleteLater()

def test_register95_retired_cover_cannot_hit_or_focus_the_input(app):
    home=QWidget();home.resize(400,240);home.view=QLineEdit(home)
    QVBoxLayout(home).addWidget(home.view);home.page=Page(home)
    cover=WebFirstFrame(home);home.show();app.processEvents()
    cover.observed(True,0);app.processEvents()
    assert cover.cover.isHidden() and not cover.cover.isEnabled()
    assert cover.cover.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    point=home.view.mapToGlobal(home.view.rect().center())
    assert app.widgetAt(point) is home.view
    QTest.mouseClick(home.view,Qt.MouseButton.LeftButton)
    QTest.keyClicks(home.view,'editable')
    assert home.view.text()=='editable'
    home.close();home.deleteLater()

def test_register95_closed_cover_also_retires_without_js(app):
    home=QWidget();home.page=Page(home);home.view=NS(reload=lambda:None)
    cover=WebFirstFrame(home);home.show();app.processEvents();home.close()
    assert cover.cover.isHidden() and not cover.poll.isActive()
    home.deleteLater()

def test_register95_actual_web_waits_for_visible_form_before_retiring_cover(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    script=tmp_path/'actual_form95.py'
    repo=Path(__file__).resolve().parents[3]
    script.write_text('import sys;sys.path.insert(0,'+repr(str(repo))+')\n'+'''
import time
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile,QWebEnginePage
from PySide6.QtTest import QTest
from refactor.desktop.native_loading import WebFirstFrame
app=QApplication([]);app.setQuitOnLastWindowClosed(False)
home=QWidget();home.resize(400,300);QVBoxLayout(home)
profile=QWebEngineProfile(app)
home.view=QWebEngineView(home);home.layout().addWidget(home.view)
home.page=QWebEnginePage(profile,home.view);home.view.setPage(home.page)
cover=WebFirstFrame(home,selector='#form:not([hidden])',fallback_body=False)
home.show();home.view.setHtml('<body>Not the form<div id="form" hidden><label for="field">Account</label><input id="field"></div></body>')
def js(code):
    values=[];home.page.runJavaScript(code,values.append)
    deadline=time.monotonic()+5
    while not values and time.monotonic()<deadline:QTest.qWait(10)
    assert values;return values[0]
deadline=time.monotonic()+8
while not js("document.readyState==='complete'") and time.monotonic()<deadline:QTest.qWait(10)
QTest.qWait(250)
assert not cover._done, 'body text must not retire a hidden form cover'
js("document.querySelector('#form').hidden=false")
while not cover._done and time.monotonic()<deadline:QTest.qWait(20)
assert cover._done and cover.cover.isHidden()
assert not cover.cover.isEnabled()
assert cover.cover.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
point=home.view.mapToGlobal(home.view.rect().center())
target=app.widgetAt(point)
assert target is not None and target is not cover.cover
home.close();home.deleteLater()
QTimer.singleShot(0,profile.deleteLater);QTimer.singleShot(50,app.quit);app.exec()
''',encoding='utf-8')
    env=dict(os.environ,QSG_RHI_BACKEND='opengl',PYTHONDONTWRITEBYTECODE='1',TEMP=str(tmp_path),TMP=str(tmp_path))
    result=subprocess.run([sys.executable,'-B',str(script)],cwd=repo,env=env,
                          capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr.decode('utf-8',errors='replace')

@pytest.mark.parametrize('intent',['register','forgot_password'])
def test_register95_prewarm_is_consumed_by_either_action_and_cancel_keeps_form(shell,intent):
    gate=shell.gate
    gate.username_field.setText('kept-user');gate.password_field.setText('kept-password')
    gate.remember_password_box.setChecked(True);gate.auto_box.setChecked(True)
    shell._prewarm_account_page();warm=shell._warm_pane
    assert warm is not None and warm.isHidden()
    shell._begin_account_action(intent)
    assert shell._pane is warm and shell._warm_pane is None and warm.intent==intent
    assert not gate.card_shadow.isEnabled()
    warm.finished.emit(False)
    assert warm.disposed and shell._pane is None
    assert gate.password_field.text()=='kept-password'
    assert gate.remember_password_box.isChecked() and gate.auto_box.isChecked()
    assert gate.username_field.text()=='kept-user'
    assert gate.state is LoginGateState.LOGIN_READY and gate.card_shadow.isEnabled()
    assert '页面已关闭' not in gate.state_detail.text()

def test_register95_prewarm_is_released_and_idle_timer_stopped(shell):
    shell._prewarm_account_page();warm=shell._warm_pane
    shell._prewarm_timer.start();shell._release_account_prewarm()
    assert warm.disposed and shell._warm_pane is None and not shell._prewarm_timer.isActive()

@pytest.mark.parametrize('entry',['login_failed','cancelled','gate_returned'])
def test_register95_returning_to_login_rearms_idle_prewarm(shell,entry):
    shell._release_account_prewarm()
    if entry=='login_failed':
        shell._background_failed(f'{shell._generation}:login','fixture failure')
    elif entry=='cancelled':shell._cancel_login()
    else:shell._show_gate_before_retiring_home()
    assert shell._prewarm_timer.isActive()

@pytest.mark.parametrize('intent',['register','forgot_password'])
def test_register95_return_keeps_saved_password_profile_and_preferences(shell,intent):
    profile=AccountIdentity('https://fixture.invalid','saved-user','Saved').profile()
    shell.session.registry.add(profile,remember_session=True,remember_password=True,
                               automatic=False,username='saved-user')
    shell._remembered_profile=profile
    shell.gate.username_field.setText('saved-user')
    shell.gate.remember_password_box.setChecked(True)
    shell.gate.state_title.setText('Saved')
    shell.gate.primary_button.setText('登录')
    before=shell.session.registry.snapshot()
    shell._begin_account_action(intent);shell._pane.finished.emit(False)
    assert shell._remembered_profile is profile
    assert shell.session.registry.snapshot()==before
    assert shell.gate.password_field.placeholderText()=='已保存密码，直接点击登录'
    assert shell.gate.state_title.text()=='Saved' and shell.gate.primary_button.text()=='登录'

@pytest.mark.parametrize('blocked',['login','home','pane','closed'])
def test_register95_prewarm_never_starts_during_other_work(shell,blocked):
    if blocked=='login':shell.gate.set_state(LoginGateState.AUTHENTICATING)
    elif blocked=='home':shell._login_result=NS()
    elif blocked=='pane':shell._pane=Pane('register',shell.gate)
    else:shell._closing=True
    shell._prewarm_account_page()
    assert shell._warm_pane is None
    # Restore fixture ownership for normal AccountShell cleanup.
    shell._login_result=None;shell._closing=False

@pytest.mark.parametrize('username,password,message',[('someone','','请输入密码'),('','typed-password','请输入账号')])
def test_register95_missing_field_is_inline_without_consuming_password(shell,username,password,message):
    gate=shell.gate;gate.username_field.setText(username);gate.password_field.setText(password)
    generation=shell._generation;shell._begin_interactive_login()
    assert gate.state is LoginGateState.LOGIN_READY and gate.primary_button.text()=='登录 MentorFlow'
    assert gate.state_detail.text()==message and gate.password_field.text()==password
    assert shell._generation==generation

@pytest.mark.parametrize('theme',['sakura','aurora'])
@pytest.mark.parametrize('width',[440,480])
def test_register95_three_checkboxes_have_room_for_full_labels(app,theme,width):
    gate=LoginGateWindow();gate.set_loading_appearance(theme);gate.resize(width,740)
    gate.show();app.processEvents()
    for box in (gate.show_password,gate.remember_password_box,gate.auto_box):
        assert box.sizePolicy().horizontalPolicy()==QSizePolicy.Policy.Minimum
        assert box.width()>=box.sizeHint().width()
    gate.close();gate.deleteLater()
