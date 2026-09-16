"""Native reset HTTP contract; real identity/SMTP semantics live in the focused fixture."""
import asyncio
import json
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from app.enterprise.identity import lifecycle_api as api
from app.enterprise.identity.oidc import IdentityError
from fastapi.responses import JSONResponse


def test_three_steps_retry_and_after_commit_contract(monkeypatch):
    monkeypatch.setattr(api,'_throttle',lambda *a:None)
    calls=[];status={'complete':'SAME_CURRENT_PASSWORD','committed':True}
    def handler(request):
        body=json.loads(request.content);calls.append(body['operation'])
        operation=body['operation']
        result={'status':'ACCEPTED'}
        if operation=='email-code-verify':result={'status':'VERIFIED','reset_proof':'p'*43,'expires_in':'300'}
        if operation=='email-code-complete':result={'status':status['complete']}
        if operation=='email-code-status':result={'status':'COMPLETED','subject':'fixture-user'} if status['committed'] else {'status':'INVALID'}
        return httpx.Response(200,json=result)
    async def run():
        app=FastAPI();app.include_router(api.router)
        @app.exception_handler(IdentityError)
        async def error(request,exc):return JSONResponse({'code':exc.code},status_code=exc.status)
        authority=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        verifier=SimpleNamespace(http=authority,endpoint='https://id.invalid/private')
        lifecycle=SimpleNamespace(active=True,jobs=set())
        app.state.enterprise_identity_runtime=SimpleNamespace(lifecycle=lifecycle,
            native_sessions=SimpleNamespace(verifier=verifier,issuer='https://id.invalid/realms/fixture',
                revoke_subject=lambda subject:calls.append('revoke:'+subject)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='https://api.invalid') as c:
            root=api.PREFIX+'/reset-code'
            requests=[await c.post(root+'/request',json={'email':email}) for email in ('present@fixture.invalid','absent@fixture.invalid')]
            assert all(r.status_code==202 for r in requests)
            a,b=[r.json() for r in requests];assert a.keys()==b.keys() and a['message']==b['message']
            assert a['expires_in']==600 and a['resend_after']==60 and a['transaction_id']!=b['transaction_id']
            await asyncio.gather(*lifecycle.jobs)
            verified=await c.post(root+'/verify',json={'transaction_id':a['transaction_id'],'code':'123456'})
            assert verified.json()=={'state':'VERIFIED','reset_proof':'p'*43,'expires_in':300}
            payload={'reset_proof':'p'*43,'new_password':'synthetic-password','confirm_password':'synthetic-password'}
            same=await c.post(root+'/complete',json=payload)
            assert same.status_code==400 and same.json()['code']=='NEW_PASSWORD_SAME_AS_CURRENT'
            assert (await c.post(root+'/complete',json={**payload,'confirm_password':'different'})).json()['code']=='PASSWORD_CONFIRMATION_MISMATCH'
            status['complete']='PENDING_COMMIT';status['committed']=False
            assert (await c.post(root+'/complete',json=payload)).status_code==503
            status['committed']=True
            done=await c.post(root+'/complete',json=payload)
            assert done.status_code==200 and set(done.json())=={'state','account_id','message'}
            assert calls[-3:]==['email-code-complete','email-code-status','revoke:fixture-user']
            # No authority is accepted from the Desktop, even after valid email proof.
            assert (await c.post(root+'/complete',json={**payload,'subject':'another'})).status_code==400
            assert (await c.post(root+'/verify',json={'transaction_id':'x'*43,'code':'123456'},headers={'Authorization':'Bearer client'})).status_code==403
        await authority.aclose()
    asyncio.run(run())


def test_reset_public_paths_are_explicit():
    assert all(api.PREFIX+'/reset-code/'+name in api.PUBLIC_PATHS for name in ('request','verify','complete'))
    assert api.PREFIX+'/change-password' not in api.PUBLIC_PATHS
