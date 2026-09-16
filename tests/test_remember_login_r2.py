"""No QApplication/windows/network/real vault: real login methods with UI seams."""
import json
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from desktop.account_identity import AccountIdentity, AccountPreferenceStore
from desktop.enterprise_oidc import EnterpriseOidcError, EnterpriseOidcLoginResult
from desktop.login_session import LoginSession
from desktop.saved_password import SavedPasswordVault
from desktop.main import MentorFlowApplication

ISSUER='https://identity.example.test/realms/mentorflow'

class Signal:
    def __init__(self): self.callbacks=[]
    def connect(self, callback): self.callbacks.append(callback)
    def emit(self,*args):
        for callback in self.callbacks: callback(*args)

class Field:
    def __init__(self, text='', checked=False):
        self.value=text; self.checked=checked; self.placeholder='密码'
        self.clicked=Signal(); self.toggled=Signal(); self.textChanged=Signal()
    def text(self): return self.value
    def setText(self,value): self.value=value; self.textChanged.emit(value)
    def isChecked(self): return self.checked
    def setChecked(self,value):
        changed=self.checked!=value; self.checked=value
        if changed: self.toggled.emit(value)
    def setPlaceholderText(self,value): self.placeholder=value
    def setFocus(self): pass

class Gate:
    login_is_blocked=False
    def __init__(self):
        for name in ('login_requested','register_requested','forgot_password_requested','update_blocked',
                     'retry_requested','background_succeeded','background_failed','cancel_requested','closed',
                     'account_selected','account_remove_requested'):
            setattr(self,name,Signal())
        for name in ('username_field','password_field','remember_password_box','remember_box','auto_box',
                     'notice_label','state_title','state_detail','primary_button'):
            setattr(self,name,Field())
        self.remember_password_box.toggled.connect(lambda *_:self.remember_box.setChecked(
            self.remember_password_box.isChecked() or self.auto_box.isChecked()))
    def show(self): pass  # No Qt window is instantiated.
    def set_state(self,state,**kwargs): self.state=state
    def clear_password(self): self.password_field.setText('')
    def take_credentials(self):
        value=self.username_field.text().strip(),self.password_field.text()
        self.clear_password();return value

class Tokens:
    def __init__(self):self.values={}
    def store_oauth_tokens(self,identity,**values):self.values[identity.account_id]=values
    def load_oauth_tokens(self,profile):
        value=self.values[profile.account_id];return value['access_token'],value['refresh_token']
    def logout(self,profile):self.values.pop(profile.account_id,None)

class MemoryWinVault:
    def __init__(self):self.values={};self.reads=[]
    def set_password(self,service,key,value):self.values[service,key]=value
    def get_password(self,service,key):self.reads.append(key);return self.values.get((service,key))
    def delete_password(self,service,key):self.values.pop((service,key),None)


def login(subject='a'):
    return EnterpriseOidcLoginResult(AccountIdentity(ISSUER,subject,'Synthetic '+subject,'org','workspace'),
        'synthetic-access','mfn1_r_synthetic',{'authenticated':True,'context':{'organization_id':'org','workspace_id':'workspace'}})

@pytest.fixture
def storage(tmp_path,monkeypatch):
    backend=MemoryWinVault();tokens=Tokens()
    monkeypatch.setattr(SavedPasswordVault,'_backend',staticmethod(lambda:backend))
    monkeypatch.setattr('desktop.saved_password._service_name',lambda:'MentorFlow.R2.SYNTHETIC')
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_ISSUER',ISSUER)
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_CLIENT_ID','mentorflow-desktop')
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_AUDIENCE','mentorflow-api')
    monkeypatch.delenv('MENTORFLOW_DESKTOP_ISSUER_MIGRATION_JSON',raising=False)
    monkeypatch.delenv('MENTORFLOW_DESKTOP_IDENTITY_ISSUER',raising=False)
    monkeypatch.setenv('MENTORFLOW_ENTERPRISE_API_BASE_URL','https://api.example.test')
    monkeypatch.delenv('MENTORFLOW_DESKTOP_REQUIRE_INTERACTIVE_ONCE',raising=False)
    path=tmp_path/'nonsecret.ini'
    def reopen():
        return LoginSession(AccountPreferenceStore(QSettings(str(path),QSettings.Format.IniFormat)),tokens,SavedPasswordVault())
    return reopen,backend,tokens,path


def application(session,monkeypatch):
    import desktop.main as main
    target=MentorFlowApplication.__new__(MentorFlowApplication)
    target.app=None; target.login_gate=Gate(); target._login_session=session
    target._closing=False;target._remembered_profile=None;target._last_login_result=None
    target._refresh_local_accounts=lambda:None
    target._load_account_capabilities=lambda:None
    target._invalidate_login_jobs=lambda **kwargs:None
    target._login_generation=0;target._login_cancelled=threading.Event()
    target._start_account_bootstrap=lambda:None
    scheduled=[]
    monkeypatch.setattr(main,'QTimer',SimpleNamespace(singleShot=lambda delay,fn:scheduled.append(fn)))
    monkeypatch.setattr('desktop.gui_update_runtime.attach_update_entry',lambda *args:None)
    class InlineThread:
        def __init__(self,*,target,**kwargs):self.target=target
        def start(self):self.target()
    monkeypatch.setattr(main.threading,'Thread',InlineThread)
    return target,scheduled


def test_remember_save_reopen_and_manual_click_without_retyping(storage,monkeypatch):
    reopen,vault,tokens,path=storage
    now=[1_900_000_000]
    monkeypatch.setattr('desktop.account_registry.time.time',lambda:now[0])
    first,scheduled=application(reopen(),monkeypatch)
    first._show_login_gate();gate=first.login_gate
    gate.username_field.setText('synthetic-a');gate.password_field.setText('synthetic-only-secret')
    gate.remember_password_box.setChecked(True)
    calls=[]
    def authenticate(username,password,**kwargs):
        calls.append(username);assert password=='synthetic-only-secret';return login()
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env',authenticate)
    first._begin_interactive_login()
    assert first._last_login_result is not None
    assert len(vault.values)==1 and not scheduled
    # Fresh objects and a freshly opened persistent metadata handle. Secure values
    # live only in the in-memory substitute for the existing Windows backend.
    now[0]+=86400
    second,scheduled=application(reopen(),monkeypatch)
    second._show_login_gate();gate=second.login_gate
    assert not scheduled and not gate.password_field.text()
    assert '已保存密码' in gate.password_field.placeholder
    assert second._last_login_result is None
    # Exercise the real token-expired -> selected saved-password read path.
    def expired(*args):raise EnterpriseOidcError('ENTERPRISE_SESSION_REAUTH_REQUIRED','expired')
    monkeypatch.setattr('desktop.native_auth.restore_native_account_from_env',expired)
    second._begin_interactive_login()
    assert calls==['synthetic-a','synthetic-a']
    assert second._last_login_result.identity.account_id==login().identity.account_id
    assert reopen().registry.get(login().identity.account_id)['last_authenticated']==now[0]
    assert b'synthetic-only-secret' not in path.read_bytes()


def test_no_consent_and_changed_username_do_not_use_saved_password(storage,monkeypatch):
    reopen,vault,tokens,path=storage
    session=reopen();session.accept(login(),remember=False,automatic=False,remember_password=False,
        username='synthetic-a',password=None)
    target,scheduled=application(reopen(),monkeypatch);target._show_login_gate()
    assert not vault.values and not scheduled
    assert target.login_gate.password_field.placeholder=='密码'
    session.accept(login(),remember=True,automatic=False,remember_password=True,
        username='synthetic-a',password='synthetic-only-secret')
    target,scheduled=application(reopen(),monkeypatch);target._show_login_gate()
    target.login_gate.username_field.setText('another-account')
    assert target.login_gate.password_field.placeholder=='密码'
    read_count=len(vault.reads)
    target._begin_interactive_login()
    assert len(vault.reads)==read_count and target._last_login_result is None


@pytest.mark.parametrize('expired',[False,True])
def test_target_invalidated_preserves_other_account(storage,monkeypatch,expired):
    reopen,vault,tokens,path=storage
    session=reopen()
    for subject in ('b','a'):
        session.accept(login(subject),remember=True,automatic=False,remember_password=True,
            username='synthetic-'+subject,password='synthetic-only-secret-'+subject)
    if expired:
        row=session.registry.snapshot()
        for account in row['accounts']:
            if account['account_id']==login('a').identity.account_id:account['last_authenticated']=1
        from desktop.account_registry import KEY
        session.preferences.settings.setValue(KEY,json.dumps(row));session.preferences.settings.sync()
    def session_expired(*args):raise EnterpriseOidcError('ENTERPRISE_SESSION_REAUTH_REQUIRED','expired')
    monkeypatch.setattr('desktop.native_auth.restore_native_account_from_env',session_expired)
    attempted=[]
    def reject(username,password,**kwargs):
        attempted.append(username);raise EnterpriseOidcError('NATIVE_CREDENTIALS_INVALID','denied')
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env',reject)
    with pytest.raises(EnterpriseOidcError) as failure:
        reopen().restore(login('a').identity.profile(),threading.Event())
    assert failure.value.code==('SAVED_PASSWORD_EXPIRED' if expired else 'SAVED_PASSWORD_REJECTED')
    assert attempted==([] if expired else ['synthetic-a'])
    assert all(login('a').identity.account_id not in key for _,key in vault.values)
    assert any(login('b').identity.account_id in key for _,key in vault.values)
    assert reopen().registry.get(login('b').identity.account_id)['remember_password']


def test_only_auto_consent_schedules_restore(storage,monkeypatch):
    reopen,vault,tokens,path=storage
    session=reopen();session.accept(login(),remember=True,automatic=True,remember_password=True,
        username='synthetic-a',password='synthetic-only-secret')
    target,scheduled=application(reopen(),monkeypatch);target._show_login_gate()
    assert scheduled==[target._begin_remembered_login]
    assert target._last_login_result is None  # Scheduling only; no automatic auth is executed.


def test_select_and_uncheck_updates_hint_and_only_selected_vault(storage,monkeypatch):
    reopen,vault,tokens,path=storage
    session=reopen()
    for subject in ('a','b'):
        session.accept(login(subject),remember=True,automatic=False,remember_password=True,
            username='synthetic-'+subject,password='synthetic-only-secret-'+subject)
    target,scheduled=application(reopen(),monkeypatch);target._show_login_gate()
    target._select_local_account(login('a').identity.account_id)
    assert target.login_gate.username_field.text()=='synthetic-a'
    assert '已保存密码' in target.login_gate.password_field.placeholder
    target.login_gate.remember_password_box.setChecked(False)
    target._remember_password_clicked(False)
    assert target.login_gate.password_field.placeholder=='密码'
    assert not reopen().registry.get(login('a').identity.account_id)['remember_password']
    assert reopen().registry.get(login('b').identity.account_id)['remember_password']
