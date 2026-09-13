"""Backend/Desktop wire contract; synthetic authority and zero email/login traffic."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.enterprise.identity import reset_completion as reset
from app.enterprise.identity.native import MtlsIdentityVerifier

TICKET = 't' * 43
STATE = 's' * 64
ISSUER = 'https://identity.example.test/realms/mentorflow'


@pytest.fixture(autouse=True)
def fresh_limits():
    reset._rates.clear()


def client_for(response=None, status=200, content=None):
    calls = []
    def identity(request):
        calls.append(json.loads(request.content))
        return httpx.Response(status, content=content) if content is not None else httpx.Response(status, json=response)
    verifier = MtlsIdentityVerifier(ISSUER + '/mentorflow-native-identity/v1', ISSUER,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(identity)))
    app = FastAPI()
    app.include_router(reset.router)
    app.state.enterprise_identity_runtime = SimpleNamespace(native_sessions=SimpleNamespace(verifier=verifier))
    return TestClient(app, base_url='https://api.example.test'), calls, app


def payload(state='PENDING', ticket=TICKET):
    return {'schema': reset.SCHEMA, 'transaction_id': ticket, 'status': state}


@pytest.mark.parametrize('state', sorted(reset.STATUSES))
def test_backend_desktop_same_status_contract(state):
    # This test also executes the shipped Desktop consumer through the real ASGI route.
    from desktop.reset_completion import ResetCompletionClient
    from desktop.enterprise_oidc import DesktopOidcConfig
    client, calls, app = client_for(payload(state))
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://api.example.test') as transport:
            desktop = ResetCompletionClient(DesktopOidcConfig(issuer=ISSUER, client_id='mentorflow-desktop',
                audience='mentorflow-api', api_base_url='https://api.example.test'), http_client=transport)
            assert await desktop.status(TICKET) == state
    asyncio.run(exercise())
    assert calls == [{'operation': 'reset-status', 'transaction_id': TICKET}]


def test_watch_does_not_take_account_or_password_or_enumerate():
    client, calls, _ = client_for(payload())
    response = client.post(reset.PREFIX + '/watch', json={'oidc_state': STATE})
    assert response.status_code == 200 and response.json() == payload()
    assert response.headers['cache-control'] == 'no-store'
    assert calls == [{'operation': 'reset-watch', 'state': STATE}]
    for name in ('known', 'unknown'):
        response = client.post(reset.PREFIX + '/watch', json={'oidc_state': STATE, 'username': name})
        assert response.status_code == 400
        assert name not in response.text


@pytest.mark.parametrize('value,status,content', [
    (payload('COMPLETED'), 200, None),  # A start response cannot be completed.
    ({'success': True}, 200, None),
    ({**payload(), 'password_changed': True}, 200, None),
    (None, 200, b'<html>Password updated</html>'),
    (None, 302, b''),
    (None, 200, b'{"status":"PENDING","status":"COMPLETED"}'),
    (None, 200, b'x' * 2049),
])
def test_untrusted_success_shapes_fail_closed(value, status, content):
    client, _, _ = client_for(value, status, content)
    response = client.post(reset.PREFIX + '/watch', json={'oidc_state': STATE})
    assert response.status_code == 503 and 'COMPLETED' not in response.text


def test_old_or_other_transaction_response_cannot_satisfy_poll():
    client, _, _ = client_for(payload('COMPLETED', 'o' * 43))
    response = client.post(reset.PREFIX + '/status', json={'transaction_id': TICKET})
    assert response.status_code == 503


@pytest.mark.parametrize('headers', [{'Origin': 'https://evil.invalid'}, {'Cookie': 'x=y'}, {'Authorization': 'Bearer x'}])
def test_browser_credential_channels_rejected(headers):
    client, calls, _ = client_for(payload())
    assert client.post(reset.PREFIX + '/watch', json={'oidc_state': STATE}, headers=headers).status_code == 403
    assert calls == []


@pytest.mark.parametrize('body', ['{"transaction_id":"x","transaction_id":"y"}', '{"success":true}', '[]', 'x' * 1025])
def test_client_cannot_declare_completion_or_send_oversized_input(body):
    client, calls, _ = client_for(payload('COMPLETED'))
    response = client.post(reset.PREFIX + '/status', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 400 and not calls


def test_observer_requests_are_rate_limited():
    client, calls, _ = client_for(payload())
    for _ in range(20):
        assert client.post(reset.PREFIX + '/watch', json={'oidc_state': STATE}).status_code == 200
    assert client.post(reset.PREFIX + '/watch', json={'oidc_state': STATE}).status_code == 429
    assert len(calls) == 20
