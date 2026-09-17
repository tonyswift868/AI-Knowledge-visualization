"""Sender-side observation wiring; no real relay or credentials."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import ssl
import time
from types import SimpleNamespace as NS
import httpx
import pytest
from app.enterprise.identity import native, admin_overview
from app.observability.health import HealthRegistry
from test_admin_overview import Engine


@pytest.mark.parametrize('payload,expected', [
    ({'status':'READY','code':'EMAIL_TLS_AUTH_READY'}, (True,'EMAIL_TLS_AUTH_READY')),
    ({'status':'UNAVAILABLE','code':'EMAIL_TRANSPORT_UNAVAILABLE'}, (False,'EMAIL_TRANSPORT_UNAVAILABLE')),
    ({'status':'UNAVAILABLE','code':'EMAIL_CONFIGURATION_UNAVAILABLE'}, (False,'EMAIL_CONFIGURATION_UNAVAILABLE')),
    ({'status':'UNAVAILABLE','code':'EMAIL_AUTHORITY_NOT_CONFIGURED'}, (False,'EMAIL_AUTHORITY_NOT_CONFIGURED')),
    ({'status':'READY','code':'EMAIL_TRANSPORT_UNAVAILABLE'}, (False,'EMAIL_TRANSPORT_UNAVAILABLE')),
    ({'status':'READY','code':'EMAIL_TLS_AUTH_READY','password':'must-not-return'}, (False,'EMAIL_TRANSPORT_UNAVAILABLE')),
])
def test_fixed_mtls_probe_and_cache(monkeypatch,payload,expected):
    context=ssl.create_default_context();calls=[];client_class=httpx.Client
    def handler(request):
        calls.append(request)
        assert str(request.url)=='https://identity.invalid/realms/test/mentorflow-native-identity/v1'
        assert json.loads(request.content)=={'operation':'email-transport-health'}
        return httpx.Response(200,json=payload)
    def factory(**kwargs):
        assert kwargs==dict(verify=context,timeout=5,follow_redirects=False,trust_env=False)
        return client_class(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(native.httpx,'Client',factory)
    verifier=native.MtlsIdentityVerifier('https://identity.invalid/realms/test/mentorflow-native-identity/v1',
                                       'https://identity.invalid/realms/test',context=context)
    try:
        with ThreadPoolExecutor(4) as pool:
            assert list(pool.map(lambda _:verifier.email_transport_health(),range(4)))==[expected]*4
        assert len(calls)==1 and verifier._email_deadline>time.monotonic()+55
        verifier._email_deadline=0
        assert verifier.email_transport_health()==expected and len(calls)==2
    finally:asyncio.run(verifier.aclose())


@pytest.mark.parametrize('response', [httpx.Response(404,json={'status':'READY','code':'EMAIL_TLS_AUTH_READY'}),
    httpx.Response(200,content=b'{"status":"READY","status":"UNAVAILABLE","code":"EMAIL_TLS_AUTH_READY"}'),
    httpx.Response(200,content=b'x'*513)])
def test_untrusted_response_fails_closed(monkeypatch,response):
    client_class=httpx.Client
    monkeypatch.setattr(native.httpx,'Client',lambda **_:client_class(transport=httpx.MockTransport(lambda _:response)))
    verifier=native.MtlsIdentityVerifier('https://identity.invalid/realms/test/mentorflow-native-identity/v1',
        'https://identity.invalid/realms/test',context=ssl.create_default_context())
    try:assert verifier.email_transport_health()==(False,'EMAIL_TRANSPORT_UNAVAILABLE')
    finally:asyncio.run(verifier.aclose())


@pytest.mark.parametrize('configured',[True,False])
def test_registry_and_admin_observe_sender_only(configured):
    registry=HealthRegistry();registry.started=True
    def forbidden():raise AssertionError('Backend SMTP must never be probed')
    registry.register('email',forbidden,required=False)
    sessions=NS(verifier=NS(email_transport_health=lambda:(True,'EMAIL_TLS_AUTH_READY'))) if configured else None
    identity=NS(keycloak=NS(canonical_identity_issuer='issuer'))
    service=admin_overview.from_env(Engine(),identity,registry,{},native_sessions=sessions)
    result=asyncio.run(service.collect())
    assert result['services']['email']==dict(status='healthy' if configured else 'unavailable',
        code='EMAIL_TLS_AUTH_READY' if configured else 'EMAIL_AUTHORITY_NOT_CONFIGURED')
    assert registry.readiness()['status']=='ready'  # Email remains optional for login.
