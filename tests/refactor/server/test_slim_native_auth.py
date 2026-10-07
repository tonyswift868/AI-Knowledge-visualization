"""Synthetic identity bridge; no live credentials, provider, SMTP or deployment."""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from refactor.server.enterprise.identity.native import (NativeSessions, MtlsIdentityVerifier, PREFIX,
    router, denied, from_env)
from refactor.server.enterprise.identity.models import AuthenticatedPrincipal
from refactor.server.enterprise.identity.oidc import IdentityError
from slim_sqlite_support import system

ISSUER = 'https://identity.example.test/realms/mentorflow'


def principal(subject='user-a', **changes):
    # Match MtlsIdentityVerifier issuance. On Windows/Python 3.11, microsecond
    # datetime timestamps can round one float ULP ahead of time.time(), which
    # correctly trips NativeSessions' strict future-issued principal rejection.
    now = datetime.fromtimestamp(int(time.time()), timezone.utc)
    value = AuthenticatedPrincipal(issuer=ISSUER, subject=subject, audiences=('mentorflow-api',),
        token_id='synthetic-native', issued_at=now, expires_at=now + timedelta(minutes=5),
        authentication_time=now, authentication_strength='password_verified', client_id='mentorflow-desktop')
    return value.model_copy(update=changes)


def test_principal_fixture_uses_native_verifier_integer_second_issuance(monkeypatch):
    import sys
    # Exact clock sampled when the old fixture was rejected on Python 3.11.15.
    sampled = 1789458520.3959029
    monkeypatch.setattr(sys.modules[__name__], 'time', SimpleNamespace(time=lambda: sampled))
    issued = principal()
    assert type(issued) is AuthenticatedPrincipal
    assert issued.issued_at.timestamp() == int(sampled)
    assert issued.expires_at.timestamp() == int(sampled) + 300
    sessions = NativeSessions(None, ISSUER, clock=lambda: sampled)
    sessions._principal(issued)
    # Preserve the strict security boundary even for the original tiny skew.
    rounded_future = datetime(2026, 9, 15, 7, 48, 40, 395903, tzinfo=timezone.utc)
    assert rounded_future.timestamp() > sampled
    with pytest.raises(IdentityError, match='账号或密码不正确'):
        sessions._principal(issued.model_copy(update={'issued_at': rounded_future}))


def test_committed_reset_revokes_subject_and_fences_inflight_login():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        class Verifier:
            slow = False
            async def authenticate(self, username, password):
                if self.slow:
                    started.set(); await release.wait()
                return principal(username)
        verifier = Verifier(); sessions = NativeSessions(verifier, ISSUER)
        a = await sessions.login('user-a','synthetic','peer')
        b = await sessions.login('user-b','synthetic','peer')
        verifier.slow = True
        pending = asyncio.create_task(sessions.login('user-a','synthetic','peer'))
        await started.wait()
        sessions.revoke_subject('user-a'); release.set()
        with pytest.raises(IdentityError): await pending
        with pytest.raises(IdentityError): sessions.authenticate(a['access_token'])
        with pytest.raises(IdentityError): await sessions.refresh(a['refresh_token'])
        assert sessions.authenticate(b['access_token']).subject == 'user-b'
        verifier.slow = False
        fresh = await sessions.login('user-a','synthetic-new','peer')
        assert sessions.authenticate(fresh['access_token']).subject == 'user-a'
    asyncio.run(run())


@pytest.mark.parametrize('malformed', [None, {}, {'issuer': ISSUER, 'subject': 'user-a'}, SimpleNamespace()])
def test_unvalidated_principal_objects_remain_rejected(malformed):
    with pytest.raises(IdentityError):
        NativeSessions(None, ISSUER)._principal(malformed)


@pytest.mark.parametrize('change', [
    {'issuer': 'https://other.invalid/realm'},
    {'audiences': ('other-api',)},
    {'client_id': 'other-client'},
    {'authentication_strength': 'unverified'},
])
def test_principal_authority_fields_remain_strict(change):
    with pytest.raises(IdentityError):
        NativeSessions(None, ISSUER)._principal(principal(**change))


@pytest.mark.parametrize('missing', ['issuer', 'subject'])
def test_missing_identity_fields_cannot_construct_validated_principal(missing):
    from pydantic import ValidationError
    fields = principal().model_dump()
    fields.pop(missing)
    with pytest.raises(ValidationError):
        AuthenticatedPrincipal.model_validate(fields)


class Verifier:
    calls = 0
    async def authenticate(self, username, password):
        self.calls += 1
        if (username, password) != ('known', 'synthetic-test-only'):
            raise denied()
        return principal()
    async def revalidate(self, previous):
        return principal(previous.subject)
    async def aclose(self): pass


def make_client(service=True, base_url='https://api.example.test'):
    app = FastAPI()
    app.include_router(router)
    app.state.enterprise_identity_runtime = SimpleNamespace(
        native_sessions=NativeSessions(Verifier(), ISSUER) if service else None)
    return TestClient(app, base_url=base_url), app.state.enterprise_identity_runtime.native_sessions


def test_wrong_password_and_unknown_account_are_identical_and_not_echoed(caplog):
    client, service = make_client()
    responses = [client.post(PREFIX + '/login', json={'username': username, 'password': 'synthetic-wrong'})
                 for username in ('known', 'unknown')]
    assert [r.status_code for r in responses] == [401, 401]
    assert responses[0].content == responses[1].content
    assert responses[0].json() == {'code': 'NATIVE_CREDENTIALS_INVALID', 'message': '账号或密码不正确'}
    assert responses[0].headers['cache-control'] == 'no-store'
    assert not service.families and 'synthetic-wrong' not in caplog.text


@pytest.mark.parametrize('variant', ['http', 'origin', 'cookie', 'authorization', 'unconfigured'])
def test_native_boundary_fails_closed(variant):
    client, _ = make_client(service=variant != 'unconfigured',
        base_url='http://api.example.test' if variant == 'http' else 'https://api.example.test')
    headers = {'Origin': 'https://evil.test'} if variant == 'origin' else (
        {'Cookie': 'session=synthetic'} if variant == 'cookie' else (
            {'Authorization': 'Bearer synthetic'} if variant == 'authorization' else {}))
    result = client.post(PREFIX + '/login', json={'username': 'known', 'password': 'synthetic-test-only'}, headers=headers)
    assert result.status_code == (503 if variant == 'unconfigured' else 403)


@pytest.mark.parametrize('payload', [[], {'username': 'known'}, {'username': 'known', 'password': 1},
    {'username': 'known', 'password': 'x', 'client_secret': 'x'}, {'username': ' ', 'password': 'x'},
    {'username': 'known', 'password': 'x' * 4100}])
def test_invalid_input_never_reaches_identity_verifier_or_echoes_body(payload):
    client, service = make_client()
    result = client.post(PREFIX + '/login', json=payload)
    assert result.status_code == 400
    assert service.verifier.calls == 0
    assert set(result.json()) == {'code', 'message'}


def test_rotation_replay_expiry_and_logout_revoke_access():
    async def run():
        service = NativeSessions(Verifier(), ISSUER)
        first = await service.login('known', 'synthetic-test-only', 'peer')
        assert service.authenticate(first['access_token']).subject == 'user-a'
        assert first['access_token'] not in repr(service.__dict__)
        assert first['refresh_token'] not in repr(service.__dict__)
        second = await service.refresh(first['refresh_token'])
        assert second['refresh_token'] != first['refresh_token']
        with pytest.raises(IdentityError): service.authenticate(first['access_token'])
        with pytest.raises(IdentityError): await service.refresh(first['refresh_token'])
        with pytest.raises(IdentityError): service.authenticate(second['access_token'])
        third = await service.login('known', 'synthetic-test-only', 'peer')
        service.logout(third['refresh_token'])
        with pytest.raises(IdentityError): service.authenticate(third['access_token'])
        fourth = await service.login('known', 'synthetic-test-only', 'peer')
        service.clock = lambda: datetime.now(timezone.utc).timestamp() + 301
        with pytest.raises(IdentityError): service.authenticate(fourth['access_token'])
        service.clock = lambda: datetime.now(timezone.utc).timestamp() + 8 * 3600 + 1
        assert (await service.refresh(fourth['refresh_token']))['expires_in'] > 0
    asyncio.run(run())


def test_concurrent_refresh_fails_closed_and_preserves_authentication_time():
    async def run():
        service = NativeSessions(Verifier(), ISSUER)
        pair = await service.login('known', 'synthetic-test-only', 'peer')
        old_time = service.authenticate(pair['access_token']).authentication_time
        rotated = await service.refresh(pair['refresh_token'])
        assert service.authenticate(rotated['access_token']).authentication_time == old_time
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(previous):
            entered.set()
            await release.wait()
            return ''
        service.account_check = delayed
        first = asyncio.create_task(service.refresh(rotated['refresh_token']))
        await entered.wait()
        with pytest.raises(IdentityError): await service.refresh(rotated['refresh_token'])
        release.set()
        with pytest.raises(IdentityError): await first
        assert not service.families
    asyncio.run(run())


def test_disabled_identity_on_refresh_revokes_family():
    async def run():
        service = NativeSessions(Verifier(), ISSUER)
        pair = await service.login('known', 'synthetic-test-only', 'peer')
        async def disabled(previous): raise denied()
        service.account_check = disabled
        with pytest.raises(IdentityError): await service.refresh(pair['refresh_token'])
        with pytest.raises(IdentityError): service.authenticate(pair['access_token'])
    asyncio.run(run())


def test_rate_limit_counts_invalid_and_unknown_accounts_before_verification():
    client, service = make_client()
    for _ in range(5):
        assert client.post(PREFIX + '/login', json={'username': 'unknown', 'password': 'x'}).status_code == 401
    assert client.post(PREFIX + '/login', json={'username': 'UNKNOWN', 'password': 'x'}).status_code == 429
    assert service.verifier.calls == 5


@pytest.mark.parametrize('reply', ['valid', 'wrong-issuer', 'challenge', 'redirect', 'unavailable'])
def test_identity_bridge_exact_contract_never_uses_oauth_grant(reply):
    async def run():
        def handler(request):
            assert str(request.url) == 'https://identity.internal/mentorflow-native-identity/v1'
            body = json.loads(request.content)
            assert body == {'operation': 'authenticate', 'username': 'known', 'password': 'synthetic-test-only'}
            value = {'schema': 'mentorflow.native-identity.v1', 'outcome': 'authenticated',
                     'issuer': ISSUER, 'subject': 'user-a', 'display_name': 'A',
                     'authentication_strength': 'password_verified', 'verification_handle': 'v' * 43}
            if reply == 'wrong-issuer': value['issuer'] = 'https://evil.test'
            if reply == 'challenge': value['outcome'] = 'challenge_required'
            return httpx.Response(302 if reply == 'redirect' else 503 if reply == 'unavailable' else 200, json=value)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            verifier = MtlsIdentityVerifier('https://identity.internal/mentorflow-native-identity/v1', ISSUER, http_client=http)
            if reply == 'valid':
                assert (await verifier.authenticate('known', 'synthetic-test-only')).issuer == ISSUER
            else:
                with pytest.raises(IdentityError): await verifier.authenticate('known', 'synthetic-test-only')
    asyncio.run(run())


def test_unconfigured_is_off_and_bridge_requires_verified_mtls_and_single_process():
    assert from_env({}, ISSUER) is None
    with pytest.raises(ValueError): from_env({'MENTORFLOW_NATIVE_IDENTITY_ENDPOINT': 'https://identity.internal/mentorflow-native-identity/v1'}, ISSUER)
    with pytest.raises(ValueError): MtlsIdentityVerifier('https://identity.internal/mentorflow-native-identity/v1', ISSUER)
    with pytest.raises(ValueError): MtlsIdentityVerifier('http://identity.internal/mentorflow-native-identity/v1', ISSUER)


@pytest.mark.parametrize('failure', ['timeout', 'connection', 'malformed'])
def test_identity_check_transient_failure_retains_refresh_family(failure):
    async def run():
        service = NativeSessions(Verifier(), ISSUER)
        pair = await service.login('known', 'synthetic-test-only', 'peer')
        async def check(previous):
            if failure == 'timeout': raise httpx.ReadTimeout('private detail')
            if failure == 'connection': raise httpx.ConnectError('private detail')
            raise ValueError('private detail')
        service.account_check = check
        with pytest.raises(IdentityError) as error: await service.refresh(pair['refresh_token'])
        assert error.value.status == 503 and 'private detail' not in str(error.value)
        assert service.authenticate(pair['access_token']).subject == 'user-a'
        service.account_check = None
        assert (await service.refresh(pair['refresh_token']))['refresh_token']
    asyncio.run(run())


def test_direct_spi_endpoint_is_bound_to_the_exact_identity_realm():
    with pytest.raises(ValueError):
        MtlsIdentityVerifier('https://identity.internal/realms/other/mentorflow-native-identity/v1', ISSUER)


def test_bridge_issuance_does_not_round_a_fractional_clock_into_the_future(monkeypatch):
    from refactor.server.enterprise.identity import native
    instant = 1789234567.9999998
    class RoundedDatetime(datetime):
        @classmethod
        def now(cls, tz=None): return datetime.fromtimestamp(instant, timezone.utc)
    assert RoundedDatetime.now().timestamp() > instant  # Reproduces the Windows boundary.
    monkeypatch.setattr(native, 'datetime', RoundedDatetime)
    monkeypatch.setattr(native.time, 'time', lambda: instant)
    async def run():
        value = {'schema': 'mentorflow.native-identity.v1', 'outcome': 'authenticated',
                 'issuer': ISSUER, 'subject': 'user-a', 'display_name': 'A',
                 'authentication_strength': 'password_verified', 'verification_handle': 'v' * 43}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=value))) as http:
            service = NativeSessions(MtlsIdentityVerifier(ISSUER+'/mentorflow-native-identity/v1', ISSUER, http_client=http),
                                     ISSUER, clock=lambda: instant)
            pair = await service.login('known', 'synthetic-test-only', 'peer')
            trusted = service.authenticate(pair['access_token'])
            assert trusted.issued_at.timestamp() <= instant < trusted.expires_at.timestamp() <= instant+300
    asyncio.run(run())




def test_native_desktop_to_account_core_bootstrap_preserves_old_identity_membership(system):
    """Real client + API + isolated persistence; only identity verifier is synthetic."""
    from refactor.server import app as account_core_mvp
    from refactor.desktop.native_auth import NativeCredentialClient
    from refactor.desktop.account_protocol import AccountConfig as DesktopOidcConfig
    from sqlalchemy import text
    client, _, engine = system
    client.base_url = httpx.URL('https://api.example.test')
    runtime = account_core_mvp.app.state.enterprise_identity_runtime
    runtime.native_sessions = NativeSessions(Verifier(), ISSUER)
    old_headers = {'Authorization': 'Bearer token-a'}
    original = client.post('/api/enterprise/account/workspaces/initialize', headers=old_headers, json={})
    assert original.status_code == 200
    original_space = original.json()['workspaces'][0]
    def transport(request):
        response = client.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
        return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            native = NativeCredentialClient(DesktopOidcConfig(ISSUER, 'mentorflow-desktop', 'mentorflow-api',
                'https://api.example.test'), http_client=http)
            return await native.login('known', 'synthetic-test-only')
    result = asyncio.run(run())
    assert result.identity.subject == 'user-a' and result.identity.issuer == ISSUER
    assert result.identity.workspace_id == original_space['id']
    assert client.get('/api/enterprise/account/workspaces', headers=old_headers).json() == original.json()
    headers = {'Authorization': 'Bearer ' + result.access_token,
               'X-MentorFlow-Organization-ID': original_space['organization_id'],
               'X-MentorFlow-Workspace-ID': original_space['id']}
    assert client.get('/api/enterprise/auth/me', headers=headers).status_code == 200
    with engine.begin() as connection:
        connection.execute(text("UPDATE workspace_memberships SET status = 'revoked' WHERE subject_id = 'user-a'"))
    assert client.get('/api/enterprise/auth/me', headers=headers).status_code == 403
    headers['Authorization'] = 'Bearer token-a'
    assert client.get('/api/enterprise/auth/me', headers=headers).status_code == 403
