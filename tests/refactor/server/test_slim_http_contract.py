"""Identical requests against MF-U11's entry and the standalone entry.

Run SLIM_CONTRACT_SIDE=old before migration to establish the reference. Never
replace these expectations with the new implementation's output.
"""
import asyncio
import json
import os
from pathlib import Path

import pytest

from contract_support import client, headers, module, principal

AUTH = '/api/enterprise/auth'
LIFE = AUTH + '/lifecycle'
SYNC = '/api/enterprise/sync/v1'
CASES = [
    ('GET', '/health/live', None, False),
    ('GET', '/health/startup', None, False),
    ('GET', '/health/ready', None, False),
    ('GET', AUTH + '/me', None, True),
    ('GET', AUTH + '/admin/check', None, True),
    ('GET', AUTH + '/admin/overview', None, True),
    ('GET', '/api/enterprise/account/workspaces', None, False),
    ('POST', '/api/enterprise/account/workspaces/initialize', {}, False),
    ('GET', LIFE + '/capabilities', None, False),
    *[('POST', LIFE + '/' + part, {}, False) for part in (
        'register', 'register/verify', 'register/resend', 'register/check',
        'reset-code/request', 'reset-code/verify', 'reset-code/complete', 'forgot')],
    ('GET', LIFE + '/email', None, True),
    *[('POST', LIFE + '/' + part, {}, True) for part in (
        'email-code/request', 'email-code/verify', 'change-password', 'revoke-sessions')],
    *[('POST', AUTH + '/native/' + part, {}, False) for part in ('login', 'refresh', 'logout')],
    ('GET', AUTH + '/native/session', None, False),
    ('POST', '/api/enterprise/account/closure/prepare', {}, False),
    ('POST', '/api/enterprise/account/closure/' + 'a' * 32 + '/commit', {}, False),
    ('GET', '/api/enterprise/account/closure/' + 'a' * 32, None, False),
    ('GET', SYNC + '/snapshot', None, True),
    ('GET', SYNC + '/changes', None, True),
    ('GET', SYNC + '/changes?limit=501', None, True),
    ('PUT', SYNC + '/entities/learning_run/sample', {}, True),
    ('POST', SYNC + '/entities/learning_run/sample/delete', {}, True),
    ('GET', '/api/enterprise/catalog/source-whitelist', None, True),
    ('GET', '/api/enterprise/catalog/model-prices', None, True),
]


def observe(response):
    """Preserve error codes/messages, JSON values and cookie/cache contracts."""
    value = response.json() if response.content else None
    return {'status': response.status_code, 'json': value,
            'headers': {k: response.headers[k] for k in (
                'cache-control', 'pragma', 'set-cookie', 'x-request-id',
                'x-mentorflow-runtime-profile') if k in response.headers}}


def collect(root, monkeypatch, tmp_path):
    monkeypatch.setenv('MENTORFLOW_CATALOG_DIR', str(tmp_path / 'absent'))
    with client(root, monkeypatch) as (http, runtime):
        results = []
        for method, path, body, authenticated in CASES:
            auth = headers() if authenticated else {'X-Request-ID': 'slim-contract'}
            results.append(observe(http.request(method, path, json=body, headers=auth)))
        return results


def test_slim_same_http_contract(monkeypatch, tmp_path):
    old = collect('app', monkeypatch, tmp_path)
    assert old[0]['status'] == old[1]['status'] == old[2]['status'] == 200
    assert old[3]['json']['authenticated'] is True
    assert old[4]['status'] == old[5]['status'] == 403
    assert len(old) == len(CASES)
    if os.getenv('SLIM_CONTRACT_SIDE') == 'old':
        output = Path(os.environ['TEMP']) / 'old-contract.json'
        output.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    new = collect('refactor.server', monkeypatch, tmp_path)
    for case, expected, actual in zip(CASES, old, new):
        assert actual == expected, case


def native_trace(root, monkeypatch):
    native = module(root, 'enterprise.identity.native')
    class Verifier:
        async def authenticate(self, username, password):
            if password != 'fixture-password':
                raise native.denied()
            return principal(root, username)

        async def revalidate(self, previous):
            return principal(root, previous.subject)

        async def aclose(self):
            pass

    with client(root, monkeypatch) as (http, runtime):
        runtime.native_sessions = native.NativeSessions(Verifier(), principal(root).issuer)
        def login():
            return http.post(AUTH + '/native/login', json={
                'username': 'user-a', 'password': 'fixture-password'})
        first = login()
        assert first.status_code == 200
        pair = first.json()
        assert set(pair) == {'schema', 'access_token', 'refresh_token', 'expires_in', 'identity'}
        auth = {'Authorization': 'Bearer ' + pair['access_token'], 'X-Request-ID': 'slim-contract'}
        results = [observe(http.get(AUTH + '/native/session', headers=auth))]
        newest = login().json()
        results.append(observe(http.get(AUTH + '/native/session', headers=auth)))
        results.append(observe(http.post(AUTH + '/native/refresh', json={'refresh_token': pair['refresh_token']},
                                         headers={'X-Request-ID': 'slim-contract'})))
        refreshed = http.post(AUTH + '/native/refresh', json={'refresh_token': newest['refresh_token']}).json()
        assert refreshed['refresh_token'] != newest['refresh_token']
        replay = http.post(AUTH + '/native/refresh', json={'refresh_token': newest['refresh_token']},
                           headers={'X-Request-ID': 'slim-contract'})
        results.append(observe(replay))
        results.append(observe(http.get(AUTH + '/native/session', headers={
            'Authorization': 'Bearer ' + refreshed['access_token'], 'X-Request-ID': 'slim-contract'})))
        assert [r['status'] for r in results] == [200, 401, 401, 401, 401]
        assert results[1]['json']['code'] == 'NATIVE_SESSION_SUPERSEDED'
        asyncio.run(runtime.native_sessions.aclose())
        return results


def test_slim_native_tokens_supersession_rotation_and_replay_contract(monkeypatch):
    old = native_trace('app', monkeypatch)
    if os.getenv('SLIM_CONTRACT_SIDE') != 'old':
        assert native_trace('refactor.server', monkeypatch) == old
