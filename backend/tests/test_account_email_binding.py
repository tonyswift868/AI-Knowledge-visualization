"""Authenticated account settings contract; only the authority supplies email facts."""
import asyncio
import json
from types import SimpleNamespace as NS
import httpx
import pytest
from fastapi import FastAPI
from app.enterprise.identity import lifecycle_api as api
from app.enterprise.identity.oidc import IdentityError
from app.enterprise.identity.runtime import identity_error_response


@pytest.mark.parametrize('case', ['empty', 'unverified', 'verified', 'subject_spoof',
    'anonymous', 'cookie', 'origin', 'invalid_session', 'not_committed', 'wrong_code', 'overwrite'])
def test_email_binding_contract(case, monkeypatch):
    monkeypatch.setattr(api, '_throttle', lambda *a: None)
    async def run():
        calls=[]
        def handler(request):
            payload=json.loads(request.content); calls.append(payload)
            assert payload['subject']=='server-subject'
            op=payload['operation']
            result={'status':'PROFILE','email':'' if case=='empty' and op.endswith('profile') else 'bound@fixture.invalid',
                    'email_verified':'true' if case=='verified' or op.endswith('status') else 'false'}
            if op.endswith('request'):result={'status':'EMAIL_ALREADY_BOUND' if case=='overwrite' else 'SENT'}
            if op.endswith('verify'):result={'status':'INVALID' if case=='wrong_code' else 'PENDING_COMMIT'}
            if op.endswith('status') and case=='not_committed':result={'status':'INVALID'}
            return httpx.Response(200,json=result)
        async def revalidate(request):
            if case=='invalid_session':raise IdentityError('ENTERPRISE_SESSION_REQUIRED','expired',status=401)
            return NS(context=NS(actor=NS(subject_id='server-subject',issuer='https://identity.invalid')))
        authority=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app=FastAPI();app.include_router(api.router)
        app.state.enterprise_identity_runtime=NS(lifecycle=NS(active=True),
            native_sessions=NS(verifier=NS(http=authority,endpoint='https://identity.invalid/private')),
            revalidate_product_identity=revalidate)
        @app.exception_handler(IdentityError)
        async def error(request,exc):return identity_error_response(request,exc)
        headers={'Authorization':'Bearer mfn1_a_synthetic'}
        if case=='anonymous':headers={}
        if case=='cookie':headers['Cookie']='session=synthetic'
        if case=='origin':headers['Origin']='https://untrusted.invalid'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='https://api.invalid',headers=headers) as c:
            profile=await c.get(api.PREFIX+'/email')
            if case in ('anonymous','cookie','origin','invalid_session'):
                assert profile.status_code==(401 if case=='invalid_session' else 403)
                assert not calls;return
            assert profile.status_code==200 and profile.headers['cache-control']=='no-store'
            assert profile.json()['can_bind']==(case=='empty')
            assert profile.json()['email_verified']==(case=='verified')
            payload={'email':'bound@fixture.invalid'}
            if case=='subject_spoof':payload['subject']='other'
            sent=await c.post(api.PREFIX+'/email-code/request',json=payload)
            if case in ('subject_spoof','overwrite'):
                assert sent.status_code==(400 if case=='subject_spoof' else 409);return
            assert sent.status_code==202
            done=await c.post(api.PREFIX+'/email-code/verify',json={'transaction_id':sent.json()['transaction_id'],'code':'123456'})
            if case=='not_committed':assert done.status_code==503
            elif case=='wrong_code':assert done.status_code==400
            else:
                assert done.status_code==200 and done.json()['state']=='EMAIL_VERIFIED'
                assert calls[-1]['operation']=='email-binding-status'
                assert 'access_token' not in done.json()
        await authority.aclose()
    asyncio.run(run())


def test_email_settings_are_not_public():
    for path in ('email','email-code/request','email-code/verify'):
        assert api.PREFIX+'/'+path not in api.PUBLIC_PATHS
