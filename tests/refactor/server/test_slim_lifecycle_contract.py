"""Success paths compared on both complete entries; identity/mail are explicit doubles."""
import asyncio
import json
import re
from types import SimpleNamespace as NS

import httpx

from contract_support import client, headers, module, principal, ISSUER
from test_slim_http_contract import observe

ROOT_PATH = '/api/enterprise/auth/lifecycle'


def lifecycle_trace(root, monkeypatch):
    api=module(root,'enterprise.identity.lifecycle_api')
    native=module(root,'enterprise.identity.native')
    monkeypatch.setattr(api,'secrets',NS(token_urlsafe=lambda _: 't'*43))
    monkeypatch.setattr(api,'_throttle',lambda *args:None)
    operations=[]
    def authority(request):
        body=json.loads(request.content); operation=body['operation'];operations.append(operation)
        replies={
            'registration-start':{'status':'CODE_SENT','credential':'r'*43,'expires_in':'600','resend_after':'60'},
            'registration-resend':{'status':'CODE_SENT','credential':'r'*43,'expires_in':'600','resend_after':'60'},
            'registration-verify':{'status':'PENDING_COMMIT'},'registration-status':{'status':'VERIFIED'},
            'email-code-request':{'status':'ACCEPTED'},
            'email-code-verify':{'status':'VERIFIED','reset_proof':'p'*43,'expires_in':'300'},
            'email-code-complete':{'status':'PENDING_COMMIT'},
            'email-code-status':{'status':'COMPLETED','subject':'user-a'},
            'email-binding-profile':{'status':'PROFILE','email':'user@fixture.invalid','email_verified':'false'},
            'email-binding-request':{'status':'SENT'}, 'email-binding-verify':{'status':'PENDING_COMMIT'},
            'email-binding-status':{'status':'PROFILE','email':'user@fixture.invalid','email_verified':'true'},
        }
        return httpx.Response(200,json=replies[operation])
    authority_client=httpx.AsyncClient(transport=httpx.MockTransport(authority))
    class Verifier:
        http=authority_client
        endpoint='https://identity.invalid/private'
        async def authenticate(self, username, password):
            return principal(root)
        async def aclose(self): pass
    class Admin:
        async def find_username(self, value): return None
        async def username_for_subject(self, subject): return 'user-a'
        async def change_password(self, subject, password): operations.append('change-password')
        async def _call(self, method, path, *, expected): operations.append('provider-logout')
    results=[]
    with client(root,monkeypatch) as (http,runtime):
        runtime.native_sessions=native.NativeSessions(Verifier(),ISSUER)
        runtime.client.store=module(root,'enterprise.identity.keycloak').InMemoryEnterpriseSessionStore()
        runtime.lifecycle=NS(active=True,admin=Admin(),jobs=set())
        anonymous={'X-Request-ID':'slim-contract'}
        def call(method,path,body=None,auth=None,expected=200):
            response=http.request(method,ROOT_PATH+path,json=body,headers=auth or anonymous)
            assert response.status_code==expected,response.text
            results.append(observe(response));return response
        call('GET','/capabilities')
        call('POST','/register',{'username':'new-user','email':'user@fixture.invalid','password':'fixture-password'},expected=201)
        call('POST','/register/check',{'field':'username','value':'new-user'})
        call('POST','/register/resend',{'credential':'r'*43})
        call('POST','/register/verify',{'credential':'r'*43,'code':'123456'})
        call('POST','/forgot',{},expected=410)
        call('POST','/reset-code/request',{'email':'user@fixture.invalid'},expected=202)
        call('POST','/reset-code/verify',{'transaction_id':'t'*43,'code':'123456'})
        call('POST','/reset-code/complete',{'reset_proof':'p'*43,'new_password':'fixture-password','confirm_password':'fixture-password'})
        def authenticated():
            pair=asyncio.run(runtime.native_sessions.login('user-a','fixture-password','fixture'))
            return {**headers(),'Authorization':'Bearer '+pair['access_token']}
        auth=authenticated()
        call('GET','/email',auth=auth)
        call('POST','/email-code/request',{'email':'user@fixture.invalid'},auth,202)
        call('POST','/email-code/verify',{'transaction_id':'t'*43,'code':'123456'},auth)
        call('POST','/change-password',{'current_password':'fixture-password','new_password':'fixture-new-password'},auth)
        call('POST','/revoke-sessions',{},authenticated())
        asyncio.run(runtime.native_sessions.aclose())
    asyncio.run(authority_client.aclose())
    return results


def test_slim_registration_reset_email_password_success_contract(monkeypatch):
    assert lifecycle_trace('app',monkeypatch)==lifecycle_trace('refactor.server',monkeypatch)


def closure_trace(root,monkeypatch):
    prefix='refactor.server' if root=='app' else 'refactor.server.account'
    closure=module(prefix,'closure')
    api=module(prefix,'closure_api')
    native=module(root,'enterprise.identity.native')
    class Store:
        issuer=ISSUER
        record=None
        clean_count=0
        def ensure_open(self,*args): pass
        def prepare(self,issuer,subject,account,operation,digest):
            self.digest=digest
            self.record=dict(operation_id=operation,account_id=account,subject_id=subject,
                state='PREPARED',data_cleaned=False,identity_deleted=False)
            return self.record.copy()
        def status(self,operation,digest):
            if digest!=self.digest or operation!=self.record['operation_id']:
                raise closure.ClosureError('CLOSURE_CREDENTIAL_INVALID',403)
            return self.record.copy()
        def acquire(self,operation,digest,claim):
            value=self.status(operation,digest)
            return dict(value,acquired=value['state']!='COMPLETED')
        def clean(self,*args):
            self.clean_count+=1;self.record['data_cleaned']=True;return self.record.copy()
        def provider_done(self,*args):
            self.record['identity_deleted']=True;return self.record.copy()
        def complete(self,*args):
            self.record['state']='COMPLETED';return self.record.copy()
        def fail(self,*args):
            self.record['state']='RETRYABLE_FAILURE';return self.record.copy()
    class Verifier:
        async def authenticate(self,*args): return principal(root)
        async def aclose(self): pass
    class Provider:
        deleted=[]
        async def username_for_subject(self,subject): return subject
        async def ensure_deleted(self,subject): self.deleted.append(subject)
    store=Store();provider=Provider();provider.admin=provider
    with client(root,monkeypatch) as (http,runtime):
        runtime.native_sessions=native.NativeSessions(Verifier(),ISSUER)
        runtime.client.store=module(root,'enterprise.identity.keycloak').InMemoryEnterpriseSessionStore()
        runtime.closure_store=store
        service=closure.AccountClosure(store,provider,runtime)
        monkeypatch.setattr(api,'_service',lambda *args,**kwargs:service)
        pair=asyncio.run(runtime.native_sessions.login('user-a','fixture','peer'))
        base='/api/enterprise/account/closure'
        prepared=http.post(base+'/prepare',json={'current_password':'fixture'},headers={
            'Authorization':'Bearer '+pair['access_token'],'X-Request-ID':'slim-contract'})
        assert prepared.status_code==200,prepared.text
        value=prepared.json();operation=value['operation_id'];token=value['recovery_token']
        assert re.fullmatch(r'[0-9a-f]{32}',operation) and re.fullmatch(r'mfc1_[A-Za-z0-9_-]{43}',token)
        auth={'X-MentorFlow-Closure-Token':token,'X-Request-ID':'slim-contract'}
        results=[observe(prepared)]
        for method,suffix,body in [('GET','',None),('POST','/commit',{'confirmed':True}),
                                   ('GET','',None),('POST','/commit',{'confirmed':True})]:
            response=http.request(method,base+'/'+operation+suffix,json=body,headers=auth)
            assert response.status_code==200,response.text
            results.append(observe(response))
        assert results[-1]['json']['state']=='COMPLETED'
        assert store.clean_count==1 and provider.deleted==['user-a']
        for result in results:
            result['json']['operation_id']='<operation>'
            if 'recovery_token' in result['json']: result['json']['recovery_token']='<recovery-token>'
        asyncio.run(runtime.native_sessions.aclose())
        return results


def test_slim_closure_prepare_commit_recovery_contract(monkeypatch):
    assert closure_trace('app',monkeypatch)==closure_trace('refactor.server',monkeypatch)


def test_slim_cookie_and_bearer_boundary_is_unchanged(monkeypatch):
    results=[]
    for root in ('app','refactor.server'):
        with client(root,monkeypatch) as (http,runtime):
            auth=headers()|{'Cookie':runtime.config.session_cookie_name+'=cookie-session'}
            response=http.get('/api/enterprise/auth/me',headers=auth)
            assert response.status_code==401
            assert response.json()['error']['code']=='AUTH_BOUNDARY_CONFLICT'
            results.append(observe(response))
    assert results[0]==results[1]
