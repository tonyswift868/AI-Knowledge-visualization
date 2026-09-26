"""Opaque-session concurrency and HTTP probes; only synthetic local identities."""
import asyncio
from datetime import datetime, timedelta, timezone
import time

import httpx
import pytest

from app import account_core_mvp
from app.enterprise.identity.models import AuthenticatedPrincipal
from app.enterprise.identity.native import NativeSessions, PREFIX, denied
from app.enterprise.identity.oidc import IdentityError
from test_account_workspace_bootstrap import system, headers
from test_enterprise_account_core_api import ISSUER


class Clock:
    def __init__(self):
        self.value = time.time()

    def __call__(self):
        return self.value


class Verifier:
    def __init__(self, clock):
        self.clock = clock
        self.calls = 0

    def principal(self, subject):
        now = datetime.fromtimestamp(int(self.clock()), timezone.utc)
        return AuthenticatedPrincipal(issuer=ISSUER, subject=subject, audiences=('mentorflow-api',),
                                      token_id='synthetic', issued_at=now, expires_at=now + timedelta(minutes=5),
                                      authentication_time=now, authentication_strength='password_verified',
                                      client_id='mentorflow-desktop')

    async def authenticate(self, username, password):
        self.calls += 1
        if password != 'synthetic-ok':
            raise denied()
        return self.principal(username)

    async def revalidate(self, previous):
        return self.principal(previous.subject)

    async def aclose(self):
        pass


def service(**kwargs):
    clock = Clock()
    return NativeSessions(Verifier(clock), ISSUER, clock=clock, **kwargs)


async def login(store, username='user-a'):
    return await store.login(username, 'synthetic-ok', 'synthetic-peer')


def assert_code(exc, code='NATIVE_SESSION_SUPERSEDED'):
    assert exc.value.status == 401
    assert exc.value.code == code
    if code == 'NATIVE_SESSION_SUPERSEDED':
        assert exc.value.message == '账号已在另一台设备登录'


def test_new_login_supersedes_access_refresh_and_preserves_other_subject():
    async def run():
        store = service()
        first = await login(store)
        other = await login(store, 'user-b')
        newest = await login(store)
        with pytest.raises(IdentityError) as exc:
            store.authenticate(first['access_token'])
        assert_code(exc)
        with pytest.raises(IdentityError) as exc:
            await store.refresh(first['refresh_token'])
        assert_code(exc)
        assert store.authenticate(newest['access_token']).subject == 'user-a'
        assert store.authenticate(other['access_token']).subject == 'user-b'
        assert len(store.families) == 2
        refreshed = await store.refresh(newest['refresh_token'])
        assert store.authenticate(refreshed['access_token']).subject == 'user-a'
        for token in (first['access_token'], first['refresh_token'], newest['access_token'], newest['refresh_token']):
            assert token not in repr(store.__dict__)
    asyncio.run(run())


@pytest.mark.parametrize('revalidation_fails', [False, True])
def test_inflight_refresh_cannot_resurrect_superseded_family(revalidation_fails):
    async def run():
        store = service()
        first = await login(store)
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(principal):
            entered.set()
            await release.wait()
            if revalidation_fails:
                raise denied()
            return store.verifier.principal(principal.subject)
        store.verifier.revalidate = slow
        pending = asyncio.create_task(store.refresh(first['refresh_token']))
        await entered.wait()
        newest = await login(store)
        release.set()
        with pytest.raises(IdentityError) as exc:
            await pending
        assert_code(exc)
        assert store.authenticate(newest['access_token']).subject == 'user-a'
        assert len(store.families) == 1
    asyncio.run(run())


def test_concurrent_logins_leave_only_last_completed_login_active():
    async def run():
        store = service()
        entered, release = asyncio.Event(), asyncio.Event()
        original = store.verifier.authenticate
        async def slow(username, password):
            if password == 'slow':
                entered.set()
                await release.wait()
            return await original(username, 'synthetic-ok')
        store.verifier.authenticate = slow
        pending = asyncio.create_task(store.login('user-a', 'slow', 'peer'))
        await entered.wait()
        fast = await login(store)
        release.set()
        latest = await pending
        with pytest.raises(IdentityError) as exc:
            store.authenticate(fast['access_token'])
        assert_code(exc)
        assert store.authenticate(latest['access_token']).subject == 'user-a'
        assert len(store.families) == 1 and not store.pending_logins
    asyncio.run(run())


def test_rotation_replay_revokes_family_without_supersession_reason():
    async def run():
        store = service()
        first = await login(store)
        second = await store.refresh(first['refresh_token'])
        assert first['refresh_token'] != second['refresh_token']
        with pytest.raises(IdentityError) as exc:
            await store.refresh(first['refresh_token'])
        assert_code(exc, 'ENTERPRISE_SESSION_REAUTH_REQUIRED')
        with pytest.raises(IdentityError) as exc:
            store.authenticate(second['access_token'])
        assert_code(exc, 'ENTERPRISE_SESSION_REAUTH_REQUIRED')
        assert not store.families and not store.superseded
    asyncio.run(run())


def test_replayed_old_device_token_cannot_revoke_new_device():
    async def run():
        store = service()
        first = await login(store)
        second = await store.refresh(first['refresh_token'])
        newest = await login(store)
        for token in (first['refresh_token'], second['refresh_token']):
            with pytest.raises(IdentityError) as exc:
                await store.refresh(token)
            assert_code(exc)
            store.logout(token)
            assert store.authenticate(newest['access_token']).subject == 'user-a'
    asyncio.run(run())


def test_failed_login_throttling_revoke_subject_and_logout_unchanged():
    async def run():
        store = service()
        first = await login(store)
        for _ in range(4):
            with pytest.raises(IdentityError) as exc:
                await store.login('user-a', 'wrong', 'peer')
            assert exc.value.code == 'NATIVE_CREDENTIALS_INVALID'
        with pytest.raises(IdentityError) as exc:
            await login(store)
        assert exc.value.code == 'NATIVE_RATE_LIMITED'
        assert store.authenticate(first['access_token']).subject == 'user-a'
        assert not store.superseded
        store.revoke_subject('user-a')
        with pytest.raises(IdentityError) as exc:
            await store.refresh(first['refresh_token'])
        assert_code(exc, 'ENTERPRISE_SESSION_REAUTH_REQUIRED')
        store.clock.value += 61
        fresh = await login(store)
        store.logout(fresh['refresh_token'])
        with pytest.raises(IdentityError) as exc:
            store.authenticate(fresh['access_token'])
        assert_code(exc, 'ENTERPRISE_SESSION_REAUTH_REQUIRED')
    asyncio.run(run())


def test_full_capacity_allows_replacement_but_not_new_subject():
    async def run():
        store = service(capacity=8)
        pairs = []
        for index in range(8):
            store.clock.value += 61
            pairs.append(await login(store, f'user-{index}'))
        store.clock.value += 61
        latest = await login(store, 'user-7')
        assert len(store.families) == 8
        with pytest.raises(IdentityError) as exc:
            await store.refresh(pairs[-1]['refresh_token'])
        assert_code(exc)
        with pytest.raises(IdentityError) as exc:
            await login(store, 'user-over-capacity')
        assert exc.value.code == 'NATIVE_SESSION_CAPACITY'
        assert store.authenticate(latest['access_token']).subject == 'user-7'
    asyncio.run(run())


def test_superseded_cache_has_capacity_expiry_and_hashes_only():
    async def run():
        store = service(superseded_capacity=6, superseded_ttl=3600)
        for _ in range(12):
            store.clock.value += 61
            newest = await login(store)
            assert len(store.superseded) <= 6
        assert len(store.superseded) == 6
        assert all(len(digest) == 64 and reason == 'NATIVE_SESSION_SUPERSEDED'
                   for digest, (reason, _) in store.superseded.items())
        assert newest['access_token'] not in repr(store.superseded)
        store.clock.value += 3601
        with pytest.raises(IdentityError):
            store.authenticate(newest['access_token'])
        assert not store.superseded
        await store.aclose()
        assert not store.families
    asyncio.run(run())


def test_http_old_refresh_access_and_session_three_states(system):
    client, _, _ = system
    client.base_url = httpx.URL('https://api.example.test')
    runtime = account_core_mvp.app.state.enterprise_identity_runtime
    runtime.native_sessions = service()
    workspace = client.post('/api/enterprise/account/workspaces/initialize', headers=headers(), json={}).json()['workspaces'][0]
    login_body = {'username': 'user-a', 'password': 'synthetic-ok'}
    first = client.post(PREFIX + '/login', json=login_body)
    assert first.status_code == 200, first.text
    first = first.json()
    old_auth = {'Authorization': 'Bearer ' + first['access_token']}
    # Probe needs only the bearer; no selected organization/workspace headers.
    active = client.get(PREFIX + '/session', headers=old_auth)
    assert active.status_code == 200 and active.json() == {'state': 'active'}
    assert active.headers['cache-control'] == 'no-store'
    second = client.post(PREFIX + '/login', json=login_body).json()
    for response in (client.get(PREFIX + '/session', headers=old_auth),
                     client.get('/api/enterprise/auth/me', headers={**headers(workspace=workspace), **old_auth}),
                     client.post(PREFIX + '/refresh', json={'refresh_token': first['refresh_token']})):
        assert response.status_code == 401, response.text
        body = response.json()
        error = body.get('error', body)  # Existing middleware versus native endpoint format.
        assert error['code'] == 'NATIVE_SESSION_SUPERSEDED'
        assert error['message'] == '账号已在另一台设备登录'
    assert client.get(PREFIX + '/session', headers={'Authorization': 'Bearer ' + second['access_token']}).json() == {'state': 'active'}
    for authorization in ('', 'Bearer mfn1_a_unknown', 'Bearer mfn1_r_not_access', 'Basic nope', 'Bearer  malformed'):
        response = client.get(PREFIX + '/session', headers={'Authorization': authorization})
        assert response.status_code == 401
        assert response.json()['code'] == 'ENTERPRISE_SESSION_REAUTH_REQUIRED'
    assert client.post(PREFIX + '/logout', json={'refresh_token': second['refresh_token']}).status_code == 200
    assert client.get(PREFIX + '/session', headers={'Authorization': 'Bearer ' + second['access_token']}).status_code == 401
