"""R1 native login regressions using only isolated synthetic accounts."""
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtTest import QTest
from desktop.account_registry import KEY
from desktop.enterprise_oidc import EnterpriseOidcError
from desktop.login_session import LoginSession
from desktop.tests.test_multi_account import session, Passwords, add
from desktop.tests.test_login_smooth import app_context, login, ISSUER, API


def wait_until(predicate):
    for _ in range(150):
        QTest.qWait(10)
        time.sleep(0.01)
        if predicate(): return
    assert predicate()


def test_selected_username_password_and_failed_attempt_history(app_context,monkeypatch):
    app,qt,settings=app_context
    monkeypatch.setattr(app,'_load_account_capabilities',lambda:None)
    monkeypatch.setattr(app,'_start_account_bootstrap',lambda:None)
    for who in ('a','b'):
        app._login_session.accept(login(who),remember=False,automatic=False,username='name-'+who)
    app._show_login_gate();gate=app.login_gate
    app._select_local_account(login('b').identity.account_id)
    gate.password_field.setText('old-b-secret')
    app._select_local_account(login('a').identity.account_id)
    assert gate.username_field.text()=='name-a' and not gate.password_field.text()
    calls=[]
    def authenticate(username,password,**kwargs):
        calls.append((username,password));return login('a')
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env',authenticate)
    gate.password_field.setText('a-secret');gate.primary_button.click()
    wait_until(lambda:app._last_login_result is not None)
    assert calls==[('name-a','a-secret')]
    app._cancel_login();before=app._login_session.registry.snapshot()
    def fail(*args,**kwargs):raise EnterpriseOidcError('NATIVE_CREDENTIALS_INVALID','invalid')
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env',fail)
    gate.username_field.setText('never-authenticated');gate.password_field.setText('wrong');gate.primary_button.click()
    wait_until(lambda:gate.primary_button.isEnabled())
    assert app._login_session.registry.snapshot()==before
    assert 'wrong' not in json.dumps(settings.values)


def test_remembered_reopen_click_and_explicit_uncheck(app_context,monkeypatch):
    app,qt,_=app_context
    monkeypatch.setattr(app,'_load_account_capabilities',lambda:None)
    app._login_session.passwords=Passwords()
    a=add(app._login_session,'a',True)
    # New session object represents reopening settings and existing credential store.
    old=app._login_session
    app._login_session=LoginSession(old.preferences,old.credentials,old.passwords)
    calls=[]
    monkeypatch.setattr(app,'_begin_remembered_login',lambda:calls.append('restore'))
    app._show_login_gate();qt.processEvents()
    assert not calls and not app.login_gate.auto_box.isChecked()
    assert not app.login_gate.password_field.text()
    app.login_gate.primary_button.click();assert calls==['restore']
    app.login_gate.remember_password_box.click()
    assert a.account_id not in old.passwords.values
    assert not old.registry.get(a.account_id)['remember_password']
    assert old.registry.get(a.account_id) is not None


def test_idle_expiry_renewal_and_bad_saved_password(session,monkeypatch):
    now=[1_800_000_000]
    monkeypatch.setattr('desktop.account_registry.time.time',lambda:now[0])
    a=add(session,'a',True)
    now[0]+=29*86400
    session.accept(login('a'),remember=True,automatic=False)  # success renews idle window
    now[0]+=29*86400
    monkeypatch.setattr(session,'_restore_session',lambda *args:login('a'))
    assert session.restore(a,threading.Event()).identity.profile()==a
    now[0]+=86400
    remote=Mock();monkeypatch.setattr(session,'_restore_session',remote)
    with pytest.raises(EnterpriseOidcError) as expired:session.restore(a,threading.Event())
    assert expired.value.code=='SAVED_PASSWORD_EXPIRED';remote.assert_not_called()
    assert not session.registry.get(a.account_id)['remember_password']
    add(session,'a',True)
    monkeypatch.setattr(session,'_restore_session',Mock(side_effect=KeyError()))
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_ISSUER',ISSUER)
    monkeypatch.setenv('MENTORFLOW_ENTERPRISE_API_BASE_URL',API)
    authenticate=Mock(side_effect=EnterpriseOidcError('NATIVE_CREDENTIALS_INVALID','invalid'))
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env',authenticate)
    with pytest.raises(EnterpriseOidcError) as rejected:session.restore(a,threading.Event())
    assert rejected.value.code=='SAVED_PASSWORD_REJECTED'
    with pytest.raises(KeyError):session.restore(a,threading.Event())
    assert authenticate.call_count==1 and not session.passwords.values


def test_old_registry_without_username_preserved(session):
    a=add(session,'a')
    value=session.registry.snapshot()
    for row in value['accounts']:
        row.pop('username');row.pop('last_authenticated')
    session.preferences.settings.setValue(KEY,json.dumps(value))
    assert session.select(a.account_id)['username']==''
    assert session.registry.get(a.account_id) is not None


def test_backend_capabilities_gate(app_context,monkeypatch):
    from desktop.lifecycle_client import LifecycleClient
    from desktop.enterprise_oidc import DesktopOidcConfig
    import httpx
    app,qt,_=app_context
    monkeypatch.setattr(DesktopOidcConfig,'from_env',staticmethod(lambda:DesktopOidcConfig(ISSUER,'mentorflow-desktop','mentorflow-api',API)))
    original_init=LifecycleClient.__init__
    monkeypatch.setattr(LifecycleClient,'__init__',lambda self,config:original_init(self,config,http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'register':False,'forgot_password':True})))) )
    app._show_login_gate()
    wait_until(lambda:app.login_gate.forgot_password_button.isEnabled())
    assert not app.login_gate.register_button.isEnabled()


@pytest.mark.parametrize('known_account', [True, False])
def test_reset_completion_clears_only_canonical_account_credentials(app_context,monkeypatch,known_account):
    from dataclasses import replace
    from desktop.account_identity import AccountIdentity
    app,qt,_=app_context
    monkeypatch.setattr(app,'_load_account_capabilities',lambda:None)
    app._login_session.passwords=Passwords()
    a=add(app._login_session,'a',True)
    other=replace(login('b'),identity=AccountIdentity('https://other.example.test/realms/mentorflow','b','Other','org-b','ws-b'))
    app._login_session.accept(other,remember=True,automatic=False,remember_password=True,
                             username='same-display-username',password='other-only')
    app._show_login_gate()
    app._auth_pane=SimpleNamespace(bridge=SimpleNamespace(completed_account_id=a.account_id if known_account else login('unknown').identity.account_id),dispose=lambda:None)
    app._return_from_password_reset(app._login_generation,completed=True)
    assert (a.account_id not in app._login_session.passwords.values) == known_account
    assert other.identity.account_id in app._login_session.passwords.values
    assert app._last_login_result is None and app._remembered_profile is None
    assert '密码已重置' in app.login_gate.state_detail.text()
