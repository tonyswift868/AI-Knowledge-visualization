"""Public contract tests. Real identity/storage/mail checks: registration_code_fixture.py."""
import asyncio
import json
from types import SimpleNamespace as NS
import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.enterprise.identity import lifecycle_api as api
from app.enterprise.identity.oidc import IdentityError


async def exercise(handler, action):
    app = FastAPI(); app.include_router(api.router)
    @app.exception_handler(IdentityError)
    async def error(request, exc):
        return JSONResponse({'code': exc.code}, status_code=exc.status)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as authority:
        app.state.enterprise_identity_runtime = NS(lifecycle=NS(active=True, jobs=set()),
            native_sessions=NS(verifier=NS(http=authority, endpoint='https://identity.invalid/private')))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://backend.invalid') as client:
            await action(client)


@pytest.mark.parametrize('status,http,code', [
    ('WRONG_CODE',400,'REGISTRATION_WRONG_CODE'), ('EXPIRED',400,'REGISTRATION_EXPIRED'),
    ('EXHAUSTED',400,'REGISTRATION_EXHAUSTED'), ('INVALID',400,'REGISTRATION_INVALID'),
    ('COOLDOWN',429,'REGISTRATION_COOLDOWN'), ('SEND_LIMIT',429,'REGISTRATION_SEND_LIMIT'),
    ('USERNAME_ALREADY_IN_USE',409,'USERNAME_ALREADY_IN_USE'),
    ('EMAIL_ALREADY_IN_USE',409,'EMAIL_ALREADY_IN_USE'), ('UNAVAILABLE',503,'REGISTRATION_UNAVAILABLE')])
def test_registration_authority_failures_are_safe(monkeypatch, status, http, code):
    monkeypatch.setattr(api, '_throttle', lambda *a: None)
    async def action(c):
        r = await c.post(api.PREFIX+'/register/verify', json={'credential':'t'*43,'code':'123456'})
        assert r.status_code == http and r.json()['code'] == code
        assert r.headers['cache-control']=='no-store' and 'subject' not in r.text
    asyncio.run(exercise(lambda r:httpx.Response(200,json={'status':status,'subject':'private-only'}),action))


def test_registration_commit_confirmation_resend_and_strict_anonymous_inputs(monkeypatch):
    calls=[]; throttle=[]
    monkeypatch.setattr(api, '_throttle', lambda *a: throttle.append(a))
    def handler(r):
        data=json.loads(r.content); calls.append(data)
        result={'status':'CODE_SENT','credential':'r'*43,'expires_in':'600','resend_after':'60'}
        if data['operation']=='registration-verify': result={'status':'PENDING_COMMIT'}
        if data['operation']=='registration-status': result={'status':'VERIFIED'}
        return httpx.Response(200,json=result)
    async def action(c):
        root=api.PREFIX+'/register'
        r=await c.post(root,json={'username':' sample ','email':'sample@fixture.invalid','password':'a valid password'})
        assert r.status_code==201 and set(r.json())=={'state','credential','expires_in','resend_after'}
        credential=r.json()['credential']
        assert calls[-1]['username']=='sample' and len(credential)==43
        r=await c.post(root+'/resend',json={'credential':credential})
        assert r.status_code==200 and r.json()['expires_in']==600
        r=await c.post(root+'/verify',json={'credential':credential,'code':'123456'})
        assert r.json()=={'state':'VERIFIED','message':'注册成功，请登录'}
        assert [d['operation'] for d in calls[-2:]]==['registration-verify','registration-status']
        count=len(calls)
        for extra in ({'subject':'victim'},{'account_id':'victim'},{'username':'victim'}):
            assert (await c.post(root+'/verify',json={'credential':credential,'code':'123456',**extra})).status_code==400
        for header in ({'Authorization':'Bearer old'}, {'Cookie':'old=value'}, {'Origin':'https://browser.invalid'}):
            assert (await c.post(root+'/resend',json={'credential':credential},headers=header)).status_code==403
        assert len(calls)==count
        assert any(t[0].startswith('registration:') for t in throttle)
        assert not any(credential in t[0] for t in throttle)
    asyncio.run(exercise(handler,action))


def test_registration_never_claims_success_before_commit(monkeypatch):
    monkeypatch.setattr(api,'_throttle',lambda *a:None)
    responses=iter(({'status':'PENDING_COMMIT'},{'status':'INVALID'}))
    async def action(c):
        r=await c.post(api.PREFIX+'/register/verify',json={'credential':'t'*43,'code':'123456'})
        assert r.status_code==503
    asyncio.run(exercise(lambda r:httpx.Response(200,json=next(responses)),action))


def test_registration_ip_limit_precedes_authority(monkeypatch):
    def throttle(*args): raise IdentityError('RESET_RATE_LIMITED','请稍后重试',status=429)
    monkeypatch.setattr(api,'_throttle',throttle)
    async def action(c):
        assert (await c.post(api.PREFIX+'/register/resend',json={'credential':'t'*43})).status_code==429
    def handler(r): pytest.fail('throttled request reached identity')
    asyncio.run(exercise(handler,action))


def test_registration_public_paths_are_exact():
    assert {api.PREFIX+'/register/'+p for p in ('verify','resend','check')} <= api.PUBLIC_PATHS
    assert api.PREFIX+'/register/status' not in api.PUBLIC_PATHS
