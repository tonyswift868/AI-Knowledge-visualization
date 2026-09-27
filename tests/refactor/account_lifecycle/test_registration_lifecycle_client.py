import asyncio
import json
import httpx
import pytest
from refactor.desktop.account_protocol import AccountConfig, AccountError
from refactor.desktop.lifecycle_client import LifecycleClient


CONFIG = AccountConfig('https://identity.invalid/realms/test','mentorflow-desktop','mentorflow-api','https://api.invalid')


def test_registration_transport_opaque_contract_and_no_ambient_auth():
    calls=[]
    def handler(request):
        assert not any(h in request.headers for h in ('Authorization','Cookie','Origin'))
        calls.append((request.url.path,json.loads(request.content)))
        verify=request.url.path.endswith('/verify')
        return httpx.Response(200 if verify or request.url.path.endswith('/resend') else 201,
            json={'state':'VERIFIED'} if verify else {'state':'CODE_SENT','credential':'t'*43,'expires_in':600,'resend_after':60})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),headers={
                'Authorization':'Bearer not-for-registration','Cookie':'secret=x','Origin':'https://wrong.invalid'}) as http:
            client=LifecycleClient(CONFIG,http_client=http)
            payload={'username':'new','password':'test-only password','email':'new@fixture.invalid'}
            response=await client.action('register',payload)
            assert not payload and response['credential']=='t'*43
            await client.registration_code('resend',{'credential':response['credential']})
            assert await client.registration_code('verify',{'credential':response['credential'],'code':'123456'})=={'state':'VERIFIED'}
    asyncio.run(run())
    assert [v[0].rsplit('/',1)[1] for v in calls]==['register','resend','verify']
    assert set(calls[2][1])=={'credential','code'}


@pytest.mark.parametrize('code', ['REGISTRATION_WRONG_CODE','REGISTRATION_EXPIRED','REGISTRATION_EXHAUSTED',
    'REGISTRATION_COOLDOWN','REGISTRATION_SEND_LIMIT','REGISTRATION_INVALID'])
def test_registration_errors_use_local_messages(code):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(400,json={
                'error':{'code':code,'message':'DO NOT SHOW PRIVATE PROVIDER DETAILS'}}))) as http:
            client=LifecycleClient(CONFIG,http_client=http)
            with pytest.raises(AccountError) as error:
                await client.registration_code('verify',{'credential':'t'*43,'code':'123456'})
            assert error.value.code==code and 'PRIVATE' not in str(error.value)
    asyncio.run(run())


@pytest.mark.parametrize('response', [
    {'state':'VERIFY_EMAIL_PENDING'}, {'state':'CODE_SENT','credential':'subject','expires_in':600,'resend_after':60},
    {'state':'CODE_SENT','credential':'t'*43,'expires_in':601,'resend_after':60},
    {'state':'CODE_SENT','credential':'t'*43,'expires_in':600,'resend_after':False}])
def test_registration_rejects_old_or_malformed_response(response):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(201,json=response))) as http:
            with pytest.raises(AccountError, match='注册'):
                await LifecycleClient(CONFIG,http_client=http).registration_code('start',{})
    asyncio.run(run())


def test_recovery_remains_in_app_code_flow():
    paths=[]
    def handler(r):
        paths.append(r.url.path)
        if paths[-1].endswith('/request'): response={'state':'CODE_SENT','transaction_id':'t'*43,'expires_in':600,'resend_after':60}
        elif paths[-1].endswith('/verify'): response={'state':'VERIFIED','reset_proof':'p'*43,'expires_in':300}
        else: response={'state':'COMPLETED','account_id':'mfa1_'+'a'*64}
        return httpx.Response(202 if paths[-1].endswith('/request') else 200,json=response)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client=LifecycleClient(CONFIG,http_client=http)
            r=await client.reset_code('request',{'email':'test@fixture.invalid'})
            r=await client.reset_code('verify',{'transaction_id':r['transaction_id'],'code':'123456'})
            await client.reset_code('complete',{'reset_proof':r['reset_proof'],'new_password':'test pw new','confirm_password':'test pw new'})
    asyncio.run(run())
    assert all('/reset-code/' in p for p in paths)
