import json
import threading
import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from desktop.account_identity import AccountPreferenceStore
from desktop.account_registry import AccountRegistry,KEY
from desktop.login_session import LoginSession
from desktop.saved_password import SavedPasswordVault
from desktop.enterprise_oidc import EnterpriseOidcError
from desktop.tests.test_login_smooth import Settings,Credentials,login,app_context,ISSUER,API


class Passwords:
    def __init__(self): self.values={};self.reads=[]
    def store(self,profile,username,password): self.values[profile.account_id]=(username,password)
    def load(self,profile):
        self.reads.append(profile.account_id)
        return self.values[profile.account_id]
    def delete(self,profile): self.values.pop(profile.account_id,None)


@pytest.fixture
def session(tmp_path,monkeypatch):
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_CLIENT_ID','mentorflow-desktop')
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_AUDIENCE','mentorflow-api')
    settings=QSettings(str(tmp_path/'preferences.ini'),QSettings.Format.IniFormat)
    return LoginSession(AccountPreferenceStore(settings),Credentials(),Passwords())


def add(session,subject,remember_password=False,automatic=False):
    result=login(subject)
    session.accept(result,remember=True,automatic=automatic,remember_password=remember_password,
                   username='same-display-username',password='test-password-'+subject if remember_password else None)
    return result.identity.profile()


def test_registry_reopen_select_remove_preserves_profiles_and_data(session,tmp_path):
    from desktop.account_vault import AccountVaultManager,VaultLayout
    a,b=add(session,'a'),add(session,'b')
    manager=AccountVaultManager(VaultLayout(tmp_path/'vaults',test_only=True))
    ra,rb=manager.prepare_profile(a),manager.prepare_profile(b)
    assert ra!=rb and a.account_id!=b.account_id
    (ra/'data/sentinel').write_text('A');(rb/'data/sentinel').write_text('B')
    for root,profile in ((ra,a),(rb,b)):
        with sqlite3.connect(root/'data/app.db') as database:
            database.execute('CREATE TABLE account_probe (id TEXT)')
            database.execute('INSERT INTO account_probe VALUES (?)',(profile.account_id,))
    reopened=LoginSession(AccountPreferenceStore(QSettings(session.preferences.settings.fileName(),QSettings.Format.IniFormat)),session.credentials,session.passwords)
    assert len(reopened.registry.snapshot()['accounts'])==2
    reopened.select(a.account_id)
    assert reopened.preferences.load_unvalidated()==a
    assert reopened.registry.snapshot()['current_account_id']==a.account_id
    reopened.remove_local(a.account_id)
    assert reopened.registry.get(a.account_id) is None
    assert reopened.registry.get(b.account_id) is not None
    assert (ra/'data/sentinel').read_text()=='A' and (rb/'data/sentinel').read_text()=='B'
    for root,profile in ((ra,a),(rb,b)):
        with sqlite3.connect(root/'data/app.db') as database:
            assert database.execute('SELECT id FROM account_probe').fetchall()==[(profile.account_id,)]
    assert session.credentials.load_oauth_tokens(b)
    assert reopened.registry.snapshot()['current_account_id'] is None


def test_remember_password_opt_in_and_no_plaintext_files(session,tmp_path):
    a=add(session,'a',True)
    assert session.passwords.values[a.account_id]==('same-display-username','test-password-a')
    add(session,'b',False)
    assert len(session.passwords.values)==1
    assert all(b'test-password-' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())
    add(session,'a',False)
    assert not session.passwords.values


def test_auto_prefers_session_without_loading_password(session,monkeypatch):
    a=add(session,'a',True,True)
    monkeypatch.setattr(session,'_restore_session',lambda *args:login('a'))
    assert session.remembered()==(a,True)
    assert session.restore(a,threading.Event()).identity.profile()==a
    assert not session.passwords.reads


def test_auto_expired_session_uses_only_saved_account_password(session,monkeypatch):
    a=add(session,'a',True,True);b=add(session,'b',True,True)
    monkeypatch.setattr(session,'_restore_session',Mock(side_effect=EnterpriseOidcError('ENTERPRISE_SESSION_REAUTH_REQUIRED','expired')))
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_ISSUER',ISSUER)
    monkeypatch.setenv('MENTORFLOW_ENTERPRISE_API_BASE_URL',API)
    calls=[]
    def authenticate(username,password):
        calls.append((username,password));return login('a')
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env',authenticate)
    assert session.restore(a,threading.Event()).identity.profile()==a
    assert calls==[('same-display-username','test-password-a')]
    assert session.passwords.reads==[a.account_id]
    assert b.account_id in session.passwords.values


@pytest.mark.parametrize('code',['NATIVE_NETWORK_UNAVAILABLE','ENTERPRISE_ACCOUNT_ISSUER_CHANGED'])
def test_network_or_issuer_error_does_not_replay_password(session,monkeypatch,code):
    a=add(session,'a',True,True)
    monkeypatch.setattr(session,'_restore_session',Mock(side_effect=EnterpriseOidcError(code,'safe error')))
    with pytest.raises(EnterpriseOidcError): session.restore(a,threading.Event())
    assert not session.passwords.reads


def test_unsaved_password_never_used_for_auto_fallback(session,monkeypatch):
    a=add(session,'a',False,True)
    monkeypatch.setattr(session,'_restore_session',Mock(side_effect=EnterpriseOidcError('ENTERPRISE_SESSION_REAUTH_REQUIRED','expired')))
    authenticate=Mock();monkeypatch.setattr('desktop.native_auth.login_native_account_from_env',authenticate)
    with pytest.raises(EnterpriseOidcError):session.restore(a,threading.Event())
    authenticate.assert_not_called();assert not session.passwords.values


def test_saved_password_identity_mismatch_rejected_before_mount_or_tokens(session,monkeypatch):
    a=add(session,'a',True,True)
    before=dict(session.credentials.values)
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_ISSUER',ISSUER)
    monkeypatch.setenv('MENTORFLOW_ENTERPRISE_API_BASE_URL',API)
    monkeypatch.setattr(session,'_restore_session',Mock(side_effect=KeyError()))
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env',lambda *args:login('b'))
    with pytest.raises(EnterpriseOidcError,match='所选账号'): session.restore(a,threading.Event())
    assert session.credentials.values==before


def test_logout_preserves_or_explicitly_forgets_password(session):
    a,b=add(session,'a',True,True),add(session,'b',True,True)
    session.logout(a)
    assert session.preferences.login_options()==(True,True)
    assert a.account_id in session.passwords.values
    assert not session.registry.get(a.account_id)['auto_login']
    assert session.credentials.key(a) not in session.credentials.values
    assert session.credentials.key(b) in session.credentials.values
    session.logout(a,forget_password=True)
    assert a.account_id not in session.passwords.values and b.account_id in session.passwords.values


def test_registry_rejects_corrupt_identity_unknown_fields(session):
    a=add(session,'a')
    value=session.registry.snapshot();value['accounts'][0]['account_id']=login('b').identity.account_id
    session.preferences.settings.setValue(KEY,json.dumps(value))
    with pytest.raises(ValueError,match='REGISTRY_INVALID'):session.select(a.account_id)
    value['accounts'][0]['password']='forbidden'
    session.preferences.settings.setValue(KEY,json.dumps(value))
    with pytest.raises(ValueError):session.registry.snapshot()


def test_password_vault_uses_stable_id_never_display_name(monkeypatch):
    values={}
    backend=Mock()
    backend.set_password.side_effect=lambda service,key,value:values.__setitem__((service,key),value)
    backend.get_password.side_effect=lambda service,key:values.get((service,key))
    backend.delete_password.side_effect=lambda service,key:values.pop((service,key))
    monkeypatch.setattr(SavedPasswordVault,'_backend',staticmethod(lambda:backend))
    vault=SavedPasswordVault();a,b=login('a').identity.profile(),login('b').identity.profile()
    vault.store(a,'same','A-only');vault.store(b,'same','B-only')
    assert vault.load(a)==('same','A-only') and vault.load(b)==('same','B-only')
    assert all('native-password:mfa1_' in key for _,key in values)
    vault.delete(a);assert vault.load(b)==('same','B-only')


def test_native_ui_remember_password_and_account_list(app_context,monkeypatch):
    target,qt,settings=app_context
    passwords=Passwords();target._login_session.passwords=passwords
    add(target._login_session,'b',True)
    monkeypatch.setattr(target,'_start_account_bootstrap',lambda:None)
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env',lambda *args,**kwargs:login('a'))
    target._show_login_gate();qt.processEvents();gate=target.login_gate
    gate.username_field.setText('same');gate.password_field.setText('UI-only-secret')
    gate.remember_password_box.setChecked(True);gate.primary_button.click()
    for _ in range(100):
        QTest.qWait(10)
        if target._last_login_result is not None:break
    a=login('a').identity.profile();b=login('b').identity.profile()
    assert passwords.values[a.account_id]==('same','UI-only-secret')
    assert 'UI-only-secret' not in json.dumps(settings.values)
    target._refresh_local_accounts()
    assert gate.account_selector.count()==2
    target._select_local_account(b.account_id)
    assert target._remembered_profile==b and gate.password_field.text()==''
    target._remove_local_account(a.account_id)
    assert gate.account_selector.count()==1 and b.account_id in passwords.values


def test_legacy_selected_account_survives_first_add(session):
    a=login('a').identity.profile()
    session.preferences.save_target(a,remember_me=True,auto_login=True)
    b=add(session,'b')
    assert {r['account_id'] for r in session.registry.snapshot()['accounts']}=={a.account_id,b.account_id}
    assert not session.registry.get(a.account_id)['remember_password']


def test_failed_registry_write_does_not_leave_invisible_saved_password(session,monkeypatch):
    monkeypatch.setattr(session.registry,'add',Mock(side_effect=RuntimeError('disk failure')))
    with pytest.raises(RuntimeError):add(session,'a',True)
    assert not session.passwords.values


def test_removed_account_cannot_race_into_saved_password_auth(session,monkeypatch):
    a=add(session,'a',True,True)
    def expired(*args):
        session.remove_local(a.account_id)
        raise EnterpriseOidcError('ENTERPRISE_SESSION_REAUTH_REQUIRED','expired')
    monkeypatch.setattr(session,'_restore_session',expired)
    with pytest.raises(EnterpriseOidcError):session.restore(a,threading.Event())
    assert not session.passwords.reads


def test_inconsistent_current_selection_does_not_auto_launch_wrong_account(session):
    a=add(session,'a',True,True);add(session,'b',True,True)
    session.preferences.save_target(a,remember_me=True,auto_login=True)
    assert session.remembered()==(None,False)


def test_same_label_accounts_keep_distinct_options(session):
    a,b=add(session,'a',True,True),add(session,'b',False,False)
    session.select(a.account_id)
    assert session.preferences.login_options()==(True,True)
    assert session.registry.get(a.account_id)['remember_password']
    session.select(b.account_id)
    assert session.preferences.login_options()==(True,False)
    assert not session.registry.get(b.account_id)['remember_password']


def test_native_qsettings_key_is_never_treated_as_filesystem_path(tmp_path,monkeypatch):
    class NativeSettings(Settings):
        def fileName(self): return r'\HKEY_CURRENT_USER\Software\MentorFlow\DisposableRegistryTest'
    settings=NativeSettings()
    def no_mkdir(*args,**kwargs):pytest.fail('Native registry key treated as directory')
    monkeypatch.setattr(Path,'mkdir',no_mkdir)
    registry=AccountRegistry(AccountPreferenceStore(settings))
    registry.add(login('a').identity.profile(),remember_session=True,remember_password=False,automatic=True)
    assert len(registry.snapshot()['accounts'])==1


def test_password_backend_failure_is_redacted_and_has_no_file_fallback(tmp_path,monkeypatch):
    backend=Mock()
    backend.set_password.side_effect=RuntimeError('do-not-echo-secret')
    monkeypatch.setattr(SavedPasswordVault,'_backend',staticmethod(lambda:backend))
    with pytest.raises(Exception) as caught:SavedPasswordVault().store(login('a').identity.profile(),'same','do-not-echo-secret')
    assert 'do-not-echo-secret' not in str(caught.value)
    assert not list(tmp_path.iterdir())


def test_cancel_automatic_persists_for_saved_password_without_forgetting_it(session):
    a=add(session,'a',True,True)
    session.suspend_automatic()
    reopened=LoginSession(session.preferences,session.credentials,session.passwords)
    assert reopened.remembered()==(a,False)
    assert a.account_id in session.passwords.values
