"""106: stalled proxy bodies and refresh locks cannot silently hold sync."""
import json
from types import SimpleNamespace
from threading import Event, Thread
import ast
import subprocess
import time
from dataclasses import make_dataclass

import httpx
import pytest
from test_sync_course import connection
from refactor.learning.sync_identity import PlanError
from refactor.learning import sync_transport as module


def test_sync106_trickling_batch_response_obeys_deadline_and_logs(connection, monkeypatch, caplog):
    _, transport, _ = connection
    ticks=SimpleNamespace(value=0)
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{'
            ticks.value=21
            yield b' '
    transport._client.close()
    transport._client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,stream=Stream())))
    monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:ticks.value))
    with caplog.at_level('INFO',logger='refactor.runtime'),pytest.raises(PlanError) as caught:
        transport.write_many([dict(kind='learning_run',entity_id='test')])
    assert caught.value.code=='SYNC_UNAVAILABLE'
    fields=[r.mf_fields for r in caplog.records if hasattr(r,'mf_fields')]
    assert fields[-1]['state']=='BATCH_REQUEST_END' and fields[-1]['code']=='SYNC_UNAVAILABLE'
    assert fields[-1]['elapsed_seconds']==21
    transport.close()


def test_sync106_account_refresh_lock_old_blocks_new_yields_then_recovers():
    from refactor.desktop import account_shell_main as shell
    from refactor.desktop.login_session import account_session_lock
    from refactor.desktop.account_identity import AccountIdentity
    identity=AccountIdentity('https://local.invalid/realm','sync106-lock','Offline fixture')
    profile=identity.profile()
    Login=make_dataclass('Login106',['identity','access_token','refresh_token'])
    login=Login(identity,'old-test-only','old-refresh-test-only')
    home=object()
    fake=SimpleNamespace(_closing=False,home=home,_login_result=login,_feature_session_confirmed=True,
                         _account_settings=None,_closure=None,_session_refresh_degraded=True,
                         _session_credentials=SimpleNamespace(load_oauth_tokens=lambda p:('new-test-only','new-refresh-test-only')))
    ready,release,done=Event(),Event(),Event()
    def holder():
        with account_session_lock(profile): ready.set();release.wait(5)
    holder_thread=Thread(target=holder);holder_thread.start();assert ready.wait(2)
    code=subprocess.run(['git','show','c2cf62bd:refactor/desktop/account_shell_main.py'],
                        check=True,capture_output=True,text=True,encoding='utf-8').stdout
    tree=ast.parse(code)
    method=next(n for c in tree.body if isinstance(c,ast.ClassDef) and c.name=='AccountShell'
                for n in c.body if isinstance(n,ast.FunctionDef) and n.name=='_feature_session')
    namespace=vars(shell).copy()
    exec(compile(ast.Module(body=[method],type_ignores=[]),'old_feature_session106.py','exec'),namespace)
    old_result=[]
    def old():
        try: old_result.append(namespace['_feature_session'](fake,home,identity.account_id))
        finally:done.set()
    old_thread=Thread(target=old);old_thread.start()
    try:
        assert not done.wait(.05)  # Old path really waits behind refresh.
        started=time.monotonic()
        assert shell.AccountShell._feature_session(fake,home,identity.account_id) is None
        assert time.monotonic()-started<.5
    finally:
        release.set();holder_thread.join(2);old_thread.join(2)
    assert done.is_set() and old_result[0].access_token=='new-test-only'
    assert shell.AccountShell._feature_session(fake,home,identity.account_id).access_token=='new-test-only'


@pytest.mark.parametrize('reason,rotated,expected', [
    ('UNAUTHORIZED',True,None), ('UNAUTHORIZED',False,'REAUTH_REQUIRED'),
    ('NATIVE_SESSION_SUPERSEDED',True,'NATIVE_SESSION_SUPERSEDED')])
def test_sync106_rotated_token_race_retries_only_new_token(connection,reason,rotated,expected):
    server,transport,login=connection
    server.incremental=True
    seen=[]
    def reject_once(request):
        seen.append(request.headers['Authorization'])
        if len(seen)==1:
            if rotated: login.access_token='new-test-access'
            return httpx.Response(401,json={'error':{'code':reason}})
    server.before=reject_once
    if expected:
        with pytest.raises(PlanError) as caught: transport.read_changes()
        assert caught.value.code==expected and len(seen)==1
    else:
        assert transport.read_changes()['entities']==[]
        assert len(seen)==2 and seen[0]!=seen[1] and seen[1]=='Bearer new-test-access'
    transport.close()
