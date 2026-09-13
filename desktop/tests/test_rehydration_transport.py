"""Product adapter against the exact Batch04 producer; synthetic sessions only."""
import asyncio
import os
import subprocess
import sys
import threading
import types
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from desktop.account_identity import AccountPreferenceStore
from desktop.login_session import LoginSession
from desktop.rehydration_transport import (
    AuthenticatedRehydrationTransport, RehydrationTransportError, PREFIX, invalidate_active,
)
from desktop.tests.test_login_smooth import login, Settings, Credentials, ISSUER, API, workspaces, app_context
from desktop.tests.test_native_login import pair, client_for
from desktop.enterprise_oidc import DesktopOidcConfig
from desktop.tests.test_login_gate_bootstrap import short_root

INTEGRATION = '04e2d61e3e39729886b3a2e6c7aff022b65a382a'


@pytest.fixture(autouse=True)
def producer(monkeypatch):
    # Read-only dependency fixture. Never checkout or rewrite integration/04 files.
    source = subprocess.check_output(['git', 'show', INTEGRATION + ':desktop/structured_sync.py'],
                                     cwd=Path(__file__).resolve().parents[2])
    module = types.ModuleType('desktop.structured_sync')
    exec(compile(source, 'batch04/desktop/structured_sync.py', 'exec'), module.__dict__)
    monkeypatch.setitem(sys.modules, 'desktop.structured_sync', module)
    invalidate_active()
    yield module.StructuredSyncClient
    invalidate_active()


def state(subject='a'):
    identity = login(subject).identity
    return {'authenticated': True,
            'account': {'account_id': identity.account_id, 'subject_id': identity.subject},
            'context': {'organization_id': identity.organization_id, 'workspace_id': identity.workspace_id}}


def snapshot(subject='a'):
    return {'schema': 'mentorflow.structured-sync.v1', 'complete': True, 'entities': [
        {'kind': 'plan', 'entity_id': 'plan-' + subject, 'deleted': False, 'revision': 1,
         'payload': {'title': subject, 'resource_ids': []}}]}


def transport(handler=None, *, subject='a', clock=None, activate=True, server_state=None):
    result = login(subject)
    adapter = AuthenticatedRehydrationTransport(
        DesktopOidcConfig(ISSUER, 'mentorflow-desktop', 'mentorflow-api', API), result.identity,
        server_state or state(subject), pair(subject),
        client=httpx.Client(transport=httpx.MockTransport(handler or (lambda _: httpx.Response(200, json=snapshot(subject))))),
        **({'clock': clock} if clock else {}))
    if activate:
        adapter.activate()
    return adapter


def test_native_login_accept_bootstrap_uses_authenticated_transport():
    paths = []
    def handler(request):
        assert request.url.host == 'api.example.test'
        paths.append(request.url.path)
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json=pair())
        assert request.headers['authorization'] == 'Bearer ' + pair()['access_token']
        if request.url.path.endswith('/workspaces'):
            return httpx.Response(200, json=workspaces())
        assert request.headers['x-mentorflow-organization-id'] == 'org-a'
        assert request.headers['x-mentorflow-workspace-id'] == 'ws-a'
        return httpx.Response(200, json=snapshot() if request.url.path.endswith('/snapshot') else state())
    async def authenticate():
        client, http = client_for(handler)
        async with http:
            return await client.login('arbitrary-username', 'synthetic-test-only')
    result = asyncio.run(authenticate())
    adapter = result.rehydration_session
    adapter._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert adapter.bootstrap().provenance == 'UNAUTHENTICATED'
    LoginSession(AccountPreferenceStore(Settings()), Credentials()).accept(result, remember=False, automatic=False)
    envelope = adapter.bootstrap()
    commits = []
    envelope.publish(lambda payload, context: commits.append((payload, context)))
    assert commits[0][0]['plans'][0]['entity_id'] == 'plan-a'
    assert commits[0][1].account_id == result.identity.account_id
    assert paths == ['/api/enterprise/auth/native/login', '/api/enterprise/account/workspaces',
                     '/api/enterprise/auth/me', PREFIX + '/snapshot']


@pytest.mark.parametrize('field,value', [('account_id','other'), ('subject_id','b'),
                                       ('organization_id','org-b'), ('workspace_id','ws-b')])
def test_wrong_account_or_workspace_confirmation_rejected(field, value):
    server = state()
    server['account' if field in {'account_id','subject_id'} else 'context'][field] = value
    with pytest.raises(RehydrationTransportError, match='UNAUTHENTICATED'):
        transport(server_state=server)


def test_callers_cannot_override_authority(producer):
    requests = []
    adapter = transport(lambda req: requests.append(req) or httpx.Response(200, json={}))
    with pytest.raises(TypeError):
        adapter.get(PREFIX + '/snapshot', headers={'X-MentorFlow-Workspace-ID': 'ws-b'})
    with pytest.raises(RehydrationTransportError, match='CONTEXT_OVERRIDE_REJECTED'):
        adapter.get('https://evil.test/snapshot')
    with pytest.raises(RehydrationTransportError, match='CONTEXT_OVERRIDE_REJECTED'):
        adapter.put(PREFIX + '/entities/plan/a', json={'workspace_id':'ws-b'})
    assert not requests
    client = producer(adapter)
    client.write('plan', 'a', request_id='r1', expected_revision=0, payload={'title':'a'})
    client.delete('plan', 'a', request_id='r2', expected_revision=1)
    assert [r.method for r in requests] == ['PUT', 'POST']
    assert all(r.headers['x-mentorflow-workspace-id'] == 'ws-a' for r in requests)


@pytest.mark.parametrize('trigger', ['expiry', '401'])
def test_renewal_reconfirms_context_rotates_locker_and_invalidates_old_envelope(trigger):
    now, calls, rotations = [0], [], []
    first = [True]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/refresh'):
            assert 'authorization' not in request.headers
            return httpx.Response(200, json=pair(suffix='b'))
        if request.url.path.endswith('/me'):
            assert request.headers['authorization'] == 'Bearer ' + pair(suffix='b')['access_token']
            return httpx.Response(200, json=state())
        if trigger == '401' and not first[0] and request.headers['authorization'].endswith('a'*43):
            return httpx.Response(401)
        return httpx.Response(200, json=snapshot())
    adapter = transport(handler, clock=lambda: now[0], activate=False)
    adapter.activate(rotation_writer=lambda *args: rotations.append(args))
    old = adapter.bootstrap()
    assert old.provenance == 'SERVER_CONFIRMED'
    first[0] = False
    if trigger == 'expiry': now[0] = 300
    current = adapter.bootstrap()
    assert current.provenance == 'SERVER_CONFIRMED'
    assert old.provenance == 'SESSION_STALE'
    assert len(rotations) == 1 and rotations[0][2] == pair(suffix='b')['refresh_token']
    assert calls[-3:] == ['/api/enterprise/auth/native/refresh', '/api/enterprise/auth/me', PREFIX+'/snapshot']


@pytest.mark.parametrize('failure', ['refresh-revoked', 'wrong-subject', 'wrong-workspace', 'network', '403'])
def test_revocation_or_failed_renewal_fails_closed(failure):
    now = [0]
    def handler(request):
        if failure == 'network': raise httpx.ConnectError('private detail')
        if failure == '403': return httpx.Response(403)
        if request.url.path.endswith('/refresh'):
            return httpx.Response(401 if failure == 'refresh-revoked' else 200,
                                  json=pair('b' if failure == 'wrong-subject' else 'a', 'b'))
        server = state()
        if failure == 'wrong-workspace': server['context']['workspace_id'] = 'ws-b'
        return httpx.Response(200, json=server)
    adapter = transport(handler, clock=lambda: now[0])
    if failure != '403': now[0] = 300
    result = adapter.bootstrap()
    assert result.provenance in {'UNAUTHENTICATED', 'TRANSPORT_UNAVAILABLE'}
    assert adapter.bootstrap().provenance == 'SESSION_STALE'
    with pytest.raises(RehydrationTransportError): result.publish(lambda *_: pytest.fail('must not commit'))


@pytest.mark.parametrize('switch', [False, True])
def test_logout_or_switch_rejects_late_a_response(switch):
    entered, release = threading.Event(), threading.Event()
    def handler(request):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json=snapshot())
    a = transport(handler)
    results, committed = [], []
    thread = threading.Thread(target=lambda: results.append(a.bootstrap()))
    thread.start()
    assert entered.wait(3)
    try:
        if switch:
            b = transport(subject='b')
            b.bootstrap().publish(lambda payload, ctx: committed.append(ctx.account_id))
        else:
            session = LoginSession(AccountPreferenceStore(Settings()), Credentials())
            session.logout(login().identity.profile())
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert results[0].provenance == 'SESSION_STALE'
        with pytest.raises(RehydrationTransportError, match='SESSION_STALE'):
            results[0].publish(lambda payload, ctx: committed.append(ctx.account_id))
        assert committed == ([login('b').identity.account_id] if switch else [])
    finally:
        release.set()
        thread.join(5)


def test_completed_result_is_fenced_at_deferred_commit_and_old_close_does_not_invalidate_b():
    a = transport()
    late = a.bootstrap()
    b = transport(subject='b')
    a.invalidate()
    assert late.provenance == 'SESSION_STALE'
    with pytest.raises(RehydrationTransportError): late.publish(lambda *_: pytest.fail('old cache write'))
    assert b.bootstrap().provenance == 'SERVER_CONFIRMED'


def test_bootstrap_unavailable_never_promotes_local_cache(monkeypatch):
    adapter = transport(lambda _: httpx.Response(503, json={'cached': 'old'}))
    assert adapter.bootstrap().provenance == 'TRANSPORT_UNAVAILABLE'
    monkeypatch.setitem(sys.modules, 'desktop.structured_sync', None)
    assert adapter.bootstrap().provenance == 'TRANSPORT_UNAVAILABLE'


def test_logout_during_rotation_cannot_save_or_publish_late_credentials():
    entered, release = threading.Event(), threading.Event()
    now, writes, results = [0], [], []
    def handler(request):
        if request.url.path.endswith('/refresh'):
            entered.set()
            assert release.wait(5)
            return httpx.Response(200, json=pair(suffix='b'))
        return httpx.Response(200, json=state())
    adapter = transport(handler, clock=lambda: now[0], activate=False)
    adapter.activate(rotation_writer=lambda *args: writes.append(args))
    now[0] = 300
    thread = threading.Thread(target=lambda: results.append(adapter.bootstrap()))
    thread.start()
    assert entered.wait(3)
    try:
        invalidate_active()
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert not writes
        assert results[0].provenance == 'SESSION_STALE'
    finally:
        release.set()
        thread.join(5)


def test_product_account_bootstrap_triggers_structured_sync_and_shared_token_owner(short_root, monkeypatch):
    from desktop.startup_flow import bootstrap_enterprise_account
    from desktop.tests.test_login_gate_bootstrap import _metadata
    monkeypatch.setattr(os, 'environ', dict(os.environ))
    now, calls = [0], []
    identity = login().identity
    metadata = _metadata('a', assets=0)
    metadata['tenant'] = {'organization_id': identity.organization_id, 'workspace_id': identity.workspace_id}
    metadata['workspace']['workspace_id'] = identity.workspace_id
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/refresh'):
            return httpx.Response(200, json=pair(suffix='b'))
        if request.url.path.endswith('/me'):
            return httpx.Response(200, json=state())
        if now[0] >= 300:
            assert request.headers['authorization'] == 'Bearer ' + pair(suffix='b')['access_token']
        return httpx.Response(200, json=snapshot() if request.url.path.endswith('/snapshot') else metadata)
    adapter = transport(handler, clock=lambda: now[0], activate=False)
    result = replace(login(), access_token=pair()['access_token'], refresh_token=pair()['refresh_token'],
                     server_state=state(), rehydration_session=adapter)
    LoginSession(AccountPreferenceStore(Settings()), Credentials()).accept(result, remember=False, automatic=False)
    boot = bootstrap_enterprise_account(result, config=adapter.config, vault_root=short_root/'v')
    try:
        assert boot.rehydration_result.provenance == 'SERVER_CONFIRMED'
        assert calls == ['/api/enterprise/account/metadata', PREFIX+'/snapshot']
        assert boot.metadata_api._session_transport is adapter
        now[0] = 300
        assert adapter.bootstrap().provenance == 'SERVER_CONFIRMED'
        # Existing progress calls share the rotated token; no parallel token owner.
        boot.metadata_api.put_account_progress({'progress': []})
        assert calls[-1] == '/api/enterprise/account/metadata/progress'
    finally:
        boot.close()
    assert adapter.bootstrap().provenance == 'SESSION_STALE'


def test_login_cancel_invalidates_accepted_transport(app_context):
    target, qt, _ = app_context
    target._show_login_gate()
    adapter = transport()
    envelope = adapter.bootstrap()
    target._cancel_login()
    assert envelope.provenance == 'SESSION_STALE'


def test_concurrent_expired_requests_rotate_once():
    from concurrent.futures import ThreadPoolExecutor
    now, refreshes = [0], []
    def handler(request):
        if request.url.path.endswith('/refresh'):
            refreshes.append(1)
            return httpx.Response(200, json=pair(suffix='b'))
        return httpx.Response(200, json=state() if request.url.path.endswith('/me') else snapshot())
    adapter = transport(handler, clock=lambda: now[0])
    now[0] = 300
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: adapter.bootstrap(), range(2)))
    assert refreshes == [1]
    assert all(result.provenance == 'SERVER_CONFIRMED' for result in results)


@pytest.mark.parametrize('remember', [True, False])
def test_logout_uses_latest_rotation_and_only_consent_persists_it(remember, monkeypatch):
    from desktop.native_auth import NativeCredentialClient
    now, ended = [0], []
    def handler(request):
        if request.url.path.endswith('/refresh'):
            return httpx.Response(200, json=pair(suffix='b'))
        return httpx.Response(200, json=state() if request.url.path.endswith('/me') else snapshot())
    adapter = transport(handler, clock=lambda: now[0], activate=False)
    credentials = Credentials()
    session = LoginSession(AccountPreferenceStore(Settings()), credentials)
    result = replace(login(), access_token=pair()['access_token'], refresh_token=pair()['refresh_token'],
                     rehydration_session=adapter)
    session.accept(result, remember=remember, automatic=False)
    now[0] = 300
    envelope = adapter.bootstrap()
    assert envelope.provenance == 'SERVER_CONFIRMED'
    if remember:
        assert credentials.load_oauth_tokens(result.identity.profile())[1] == pair(suffix='b')['refresh_token']
    else:
        assert credentials.values == {}
    async def end(self, token):
        ended.append(token)
        return True
    monkeypatch.setattr(DesktopOidcConfig, 'from_env', staticmethod(lambda: adapter.config))
    monkeypatch.setattr(NativeCredentialClient, 'end_session', end)
    assert session.logout(result.identity.profile(), remote=True, refresh_token=result.refresh_token)
    assert ended == [pair(suffix='b')['refresh_token']]
    assert envelope.provenance == 'SESSION_STALE'
    assert credentials.values == {}
