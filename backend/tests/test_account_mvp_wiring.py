import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs
import httpx
import pytest
from fastapi import FastAPI
from app.enterprise.identity import lifecycle_api as api
from app.enterprise.identity.service_account import ServiceAccountTokenResolver
from app.enterprise.identity.account_lifecycle import LifecycleError


def test_client_credentials_is_cached_and_secret_errors_are_bounded(tmp_path):
    secret=tmp_path/'secret';secret.write_text('synthetic-secret-only-32-characters')
    requests=[]
    def handler(request):
        requests.append(request)
        value=parse_qs(request.content.decode())
        assert set(value)=={'grant_type','client_id','client_secret'}
        assert value['grant_type']==['client_credentials']
        return httpx.Response(200,json={'access_token':'synthetic-authority','expires_in':60})
    async def run():
        resolver=ServiceAccountTokenResolver('https://id.invalid','realm','service',secret,transport=httpx.MockTransport(handler))
        try:
            assert await resolver()==await resolver()=='synthetic-authority'
            assert len(requests)==1
            resolver.deadline=0;secret.write_text('short')
            with pytest.raises(LifecycleError,match='ADMIN_AUTHORITY_UNAVAILABLE'):await resolver()
            assert len(requests)==1
        finally:await resolver.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('status,body',[(401,{}),(200,{'access_token':'x','expires_in':0}),(200,{'access_token':'x\r\ny','expires_in':60})])
def test_bad_service_authority_fails_closed(tmp_path,status,body):
    secret=tmp_path/'secret';secret.write_text('synthetic-private-reference')
    async def run():
        resolver=ServiceAccountTokenResolver('https://id.invalid','realm','service',secret,transport=httpx.MockTransport(lambda r:httpx.Response(status,json=body)))
        try:
            with pytest.raises(LifecycleError,match='ADMIN_AUTHORITY_UNAVAILABLE'):await resolver()
        finally:await resolver.aclose()
    asyncio.run(run())


def test_release_capabilities_stay_off():
    assert api.from_env({}) is None
    with pytest.raises(ValueError):ServiceAccountTokenResolver('https://id.invalid','realm','service','none',verify=False)


def test_resident_lifecycle_opt_in_does_not_require_disposable_lease(tmp_path, monkeypatch):
    config = {'origin':'https://id.invalid', 'realm':'realm', 'client_id':'service',
              'secret_file':'private-reference', 'ca_file':'trusted-ca'}
    path = tmp_path/'lifecycle.json'; path.write_text(json.dumps(config))
    monkeypatch.setattr(api, 'LifecycleRuntime', lambda value:value)
    env = {'MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG':str(path)}
    assert api.from_env(env) is None
    assert api.from_env({**env,'MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED':'1'}) == config
    with pytest.raises(ValueError, match='expiry required'):
        api.from_env({**env,'MENTORFLOW_ACCOUNT_MVP_TEST':'1'})
    config['expires_at'] = '2000-01-01T00:00:00+00:00'
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='expired'):
        api.from_env({**env,'MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED':'1'})


def test_lifecycle_mtls_uses_explicit_paired_backend_references(tmp_path, monkeypatch):
    calls = []
    context = SimpleNamespace(load_cert_chain=lambda *args: calls.append(args))
    monkeypatch.setattr(api.ssl, 'create_default_context', lambda **kw: context)
    monkeypatch.setattr(api, 'ServiceAccountTokenResolver', lambda *args, **kw: kw['verify'])
    monkeypatch.setattr(api, 'KeycloakAccountAdmin', lambda *args, **kw: kw['verify'])
    config = {'origin':'https://id.invalid', 'realm':'realm', 'client_id':'service',
              'secret_file':'private-reference', 'ca_file':'trusted-ca',
              'client_cert_file':'backend-cert', 'client_key_file':'backend-key'}
    path = tmp_path/'lifecycle.json'
    env = {'MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED':'1',
           'MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG':str(path)}
    path.write_text(json.dumps(config))
    runtime = api.from_env(env)
    assert runtime.active and runtime.resolver is context and runtime.admin is context
    assert calls == [('backend-cert', 'backend-key')]
    del config['client_key_file']
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='must be paired'):
        api.from_env(env)


def test_registration_and_uniform_forgot_use_existing_observer(monkeypatch):
    calls=[]
    class Admin:
        async def create_account(self,**value):calls.append(('create',set(value)));return 'subject'
        async def send_verification(self,subject):calls.append(('verify',subject))
        async def find_account(self,name):return 'subject' if name=='present' else None
        async def send_password_reset(self,subject):calls.append(('reset',subject))
    ticket={'schema':'mentorflow.reset-completion.v1','transaction_id':'t'*43,'status':'PENDING'}
    async def observe(*args):return ticket
    monkeypatch.setattr('app.enterprise.identity.reset_completion.identity_observation',observe)
    monkeypatch.setattr(api,'_throttle',lambda *a:None)
    async def run():
        verifier=SimpleNamespace(endpoint='https://id.invalid/private',http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=ticket))))
        runtime=SimpleNamespace(active=True,admin=Admin(),jobs=set())
        app=FastAPI();app.include_router(api.router)
        app.state.enterprise_identity_runtime=SimpleNamespace(lifecycle=runtime,native_sessions=SimpleNamespace(verifier=verifier))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://backend.invalid') as c:
            assert (await c.get(api.PREFIX+'/capabilities')).json()=={'register':True,'forgot_password':True}
            r=await c.post(api.PREFIX+'/register',json={'username':'new','password':'fixture-password','email':'test@example.invalid'})
            assert r.status_code==201
            assert (await c.post(api.PREFIX+'/register',json={'username':'new','password':'fixture-password','email':'test@example.invalid','role':'SUPER_ADMIN'})).status_code==400
            a=await c.post(api.PREFIX+'/forgot',json={'account':'present'})
            b=await c.post(api.PREFIX+'/forgot',json={'account':'absent'})
            assert a.status_code==b.status_code==202 and a.json()==b.json()
            await asyncio.gather(*runtime.jobs)
            assert calls==[('create',{'username','password','email'}),('verify','subject'),('reset','subject')]
        await verifier.http.aclose()
    asyncio.run(run())
