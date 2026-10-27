"""203 session-refresh retry and privacy-safe sync diagnostics."""
from hashlib import sha256
import json
import logging
import re
from threading import Event, Thread
from types import SimpleNamespace as NS

import certifi
import httpx
import pytest

from refactor.desktop.account_shell_main import AccountShell
from refactor.learning.sync_identity import PlanError
from refactor.learning.sync_transport import SyncTransport
from refactor.runtime_logging import DiagnosticFormatter


def login(token, organization='sync203-org'):
    issuer, subject = 'https://identity.example.test', 'sync203-user'
    parts = [sha256(value.encode()).hexdigest() for value in (issuer, subject)]
    account = 'mfa1_' + sha256(('mentorflow.account.v1:' + ':'.join(parts)).encode()).hexdigest()
    identity = NS(account_id=account, issuer=issuer, subject=subject,
                  organization_id=organization, workspace_id='sync203-workspace')
    return NS(identity=identity, access_token=token, server_state={
        'authenticated': True, 'account': {'account_id': account, 'subject_id': subject},
        'context': {'organization_id': identity.organization_id,
                    'workspace_id': identity.workspace_id}})


def make_transport(state, handler, waiter=None):
    return SyncTransport(account_id=state[0].identity.account_id,
        session_provider=lambda: state[0], session_refresh_waiter=waiter,
        api_base_url='https://sync203.example.test', ca_bundle=certifi.where(),
        http_transport=httpx.MockTransport(handler))


def test_sync203_waits_for_active_refresh_then_retries_with_new_token(caplog):
    state = [login('old-token')]
    authorization = []
    calls = []

    def handler(request):
        calls.append(request)
        authorization.append(request.headers['authorization'])
        assert re.fullmatch(r'[0-9a-f]{32}', request.headers['X-Request-ID'])
        if len(calls) == 1:
            return httpx.Response(401, headers={'X-Request-ID': 'server-trace-a'},
                                  json={'error': {'code': 'REAUTH_REQUIRED'}})
        return httpx.Response(200, headers={'X-Request-ID': 'server-trace-b'},
                              json={'schema': 'ok'})

    waiter_calls = []
    transport = None

    def waiter(cancel_event):
        waiter_calls.append(cancel_event)
        state[0] = login('fresh-token')

    transport = make_transport(state, handler, waiter)
    caplog.set_level(logging.INFO, logger='refactor.runtime')
    try:
        with transport._sync_response('GET', '/api/enterprise/sync/v1/changes') as response:
            assert response.json() == {'schema': 'ok'}
        assert authorization == ['Bearer old-token', 'Bearer fresh-token']
        assert waiter_calls == [transport._closed]
        rows = [json.loads(DiagnosticFormatter().format(row)) for row in caplog.records
                if getattr(row, 'mf_event', None) == 'sync']
        rejected = next(row for row in rows if row.get('state') == 'HTTP_AUTH_REJECTED')
        assert rejected['client_request_id'] == calls[0].headers['X-Request-ID']
        assert rejected['response_request_id'] == 'server-trace-a'
        assert calls[0].headers['X-Request-ID'] != calls[1].headers['X-Request-ID']
    finally:
        transport.close()


def test_sync203_fresh_token_rejection_and_superseded_session_are_not_retried():
    state = [login('same-token')]
    wait_count = []
    def waiter(_cancel):
        wait_count.append(True)
        return False  # shell reports that no refresh was in flight
    def rejected(request):
        return httpx.Response(401, json={'error': {'code': 'REAUTH_REQUIRED'}})
    transport = make_transport(state, rejected, waiter)
    try:
        with pytest.raises(PlanError) as caught:
            with transport._sync_response('GET', '/api/enterprise/sync/v1/changes'):
                pass
        assert caught.value.code == 'REAUTH_REQUIRED'
        assert len(wait_count) == 1
    finally:
        transport.close()
    wait_count.clear()
    def superseded(_request):
        return httpx.Response(401, json={'error': {'code': 'NATIVE_SESSION_SUPERSEDED'}})
    transport = make_transport(state, superseded, waiter)
    try:
        with pytest.raises(PlanError) as caught:
            with transport._sync_response('GET', '/api/enterprise/sync/v1/changes'):
                pass
        assert caught.value.code == 'NATIVE_SESSION_SUPERSEDED'
        assert not wait_count
    finally:
        transport.close()


def test_sync203_fresh_token_still_401_returns_reauth_without_second_wait_or_cas_change():
    state = [login('old-token')]
    requests = []
    def handler(request):
        requests.append((request.headers['authorization'], request.headers['X-Request-ID'], request.content))
        return httpx.Response(401, json={'error': {'code': 'REAUTH_REQUIRED'}})
    waiter_calls = []
    def waiter(_cancel):
        waiter_calls.append(True)
        state[0] = login('fresh-token')
        return True
    transport = make_transport(state, handler, waiter)
    body = {'request_id': 'cas-operation-203', 'expected_revision': 7,
            'payload': {'field': 'same-payload'}}
    try:
        with pytest.raises(PlanError) as caught:
            with transport._sync_response('PUT', '/api/enterprise/sync/v1/entities/task/item', json=body):
                pass
        assert caught.value.code == 'REAUTH_REQUIRED'
        assert [row[0] for row in requests] == ['Bearer old-token', 'Bearer fresh-token']
        assert requests[0][2] == requests[1][2]
        assert json.loads(requests[0][2])['request_id'] == body['request_id']
        assert requests[0][1] != requests[1][1]
        assert len(waiter_calls) == 1
    finally:
        transport.close()


def test_sync203_scope_change_during_refresh_does_not_retry_under_new_scope():
    state = [login('old-token')]
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={'error': {'code': 'REAUTH_REQUIRED'}})
    waiter_calls = []
    def waiter(_cancel):
        waiter_calls.append(True)
        state[0] = login('fresh-token', organization='new-org')
        return True
    transport = make_transport(state, handler, waiter)
    try:
        with pytest.raises(PlanError) as caught:
            with transport._sync_response('GET', '/api/enterprise/sync/v1/changes'):
                pass
        assert caught.value.code == 'SESSION_UNAVAILABLE'
        assert len(requests) == 1
        assert len(waiter_calls) == 1
    finally:
        transport.close()


def test_sync203_waited_refresh_without_rotation_is_transient_not_reauth():
    state = [login('still-current-token')]
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={'error': {'code': 'REAUTH_REQUIRED'}})
    waiter_calls = []
    def waiter(_cancel):
        waiter_calls.append(True)  # refresh completed with a network error; session stayed unchanged
        return True
    transport = make_transport(state, handler, waiter)
    try:
        with pytest.raises(PlanError) as caught:
            with transport._sync_response('GET', '/api/enterprise/sync/v1/changes'):
                pass
        assert caught.value.code == 'SESSION_UNAVAILABLE'
        assert len(requests) == 1 and len(waiter_calls) == 1
    finally:
        transport.close()


def test_sync203_refresh_waiter_error_is_transient_not_reauth():
    state = [login('old-token')]
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={'error': {'code': 'REAUTH_REQUIRED'}})
    def waiter(_cancel):
        raise OSError('private refresh failure detail')
    transport = make_transport(state, handler, waiter)
    try:
        with pytest.raises(PlanError) as caught:
            with transport._sync_response('GET', '/api/enterprise/sync/v1/changes'):
                pass
        assert caught.value.code == 'SESSION_UNAVAILABLE'
        assert len(requests) == 1
    finally:
        transport.close()

def test_sync203_forbidden_does_not_wait_for_refresh():
    state = [login('token')]
    wait_count = []
    transport = make_transport(state,
        lambda _request: httpx.Response(403, json={'error': {'code': 'REAUTH_REQUIRED'}}),
        lambda _cancel: wait_count.append(True))
    try:
        with pytest.raises(PlanError) as caught:
            with transport._sync_response('GET', '/api/enterprise/sync/v1/changes'):
                pass
        assert caught.value.code == 'REAUTH_REQUIRED'
        assert not wait_count
    finally:
        transport.close()


def test_sync203_close_cancels_refresh_wait_without_second_request():
    state = [login('old-token')]
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={'error': {'code': 'REAUTH_REQUIRED'}})
    started = Event()
    def waiter(cancel_event):
        started.set()
        while not cancel_event.wait(.01):
            pass
    transport = make_transport(state, handler, waiter)
    outcome = []
    def run():
        try:
            with transport._sync_response('GET', '/api/enterprise/sync/v1/changes'):
                pass
        except PlanError as exc:
            outcome.append(exc.code)
    thread = Thread(target=run)
    thread.start()
    assert started.wait(1)
    transport.close()
    thread.join(1)
    assert not thread.is_alive()
    assert outcome == ['CANCELLED']
    assert len(requests) == 1


def test_sync203_refresh_waiter_is_released_and_cancellable():
    shell = AccountShell.__new__(AccountShell)
    shell._session_refresh_busy = False
    shell._session_refresh_wait_lock = __import__('threading').Lock()
    shell._session_refresh_done = Event()
    shell._session_refresh_done.set()
    shell._start_session_refresh_waiters()
    cancel = Event()
    finished = Event()
    thread = Thread(target=lambda: (shell._wait_for_session_refresh(cancel), finished.set()))
    thread.start()
    assert not finished.wait(.03)
    shell._finish_session_refresh_waiters()
    assert finished.wait(1)
    thread.join(1)

    shell._start_session_refresh_waiters()
    cancel.set()
    thread = Thread(target=lambda: (shell._wait_for_session_refresh(cancel), finished.set()))
    finished.clear()
    thread.start()
    assert finished.wait(1)
    thread.join(1)


def test_sync203_diagnostics_keep_safe_correlation_fields_only(caplog):
    state = [login('secret-token')]
    incoming = []
    def handler(request):
        incoming.append(request.headers['X-Request-ID'])
        return httpx.Response(401, headers={'X-Request-ID': 'req-203-abc'},
                              json={'error': {'code': 'REAUTH_REQUIRED', 'detail': 'secret-body'}})
    transport = make_transport(state, handler)
    caplog.set_level(logging.INFO, logger='refactor.runtime')
    try:
        with pytest.raises(PlanError):
            with transport._sync_response('GET', '/api/enterprise/sync/v1/changes'):
                pass
    finally:
        transport.close()
    rows = []
    for record in caplog.records:
        if getattr(record, 'mf_event', None) == 'sync':
            rows.append(json.loads(DiagnosticFormatter().format(record)))
    assert any(row.get('phase') == 'pull' and row.get('response_request_id') == 'req-203-abc'
               and row.get('client_request_id') == incoming[0]
               and row.get('http_status') == 401 for row in rows)
    formatted = '\n'.join(json.dumps(row) for row in rows)
    for secret in ('secret-token', 'secret-body', 'sync203-user', 'Authorization'):
        assert secret not in formatted


def test_sync203_request_id_and_error_type_are_bounded():
    formatter = DiagnosticFormatter()
    record = logging.LogRecord('refactor.runtime', logging.INFO, __file__, 1,
                               'ignored', (), None)
    record.mf_event = 'sync'
    record.mf_fields = {'state': 'REQUEST_FAILED', 'phase': 'upload',
                        'error_type': 'ConnectError', 'client_request_id': 'a' * 32,
                        'response_request_id': 'bad id with pii', 'operation_id': 'op-203'}
    row = json.loads(formatter.format(record))
    assert row['phase'] == 'upload' and row['error_type'] == 'ConnectError'
    assert row['client_request_id'] == 'a' * 32 and row['operation_id'] == 'op-203'
    assert 'response_request_id' not in row and 'pii' not in json.dumps(row)


def test_sync203_network_failure_logs_client_trace_and_exception_category(caplog):
    state = [login('secret-token')]
    def fail(request):
        raise httpx.ConnectError('text must not appear in diagnostics', request=request)
    transport = make_transport(state, fail)
    caplog.set_level(logging.INFO, logger='refactor.runtime')
    try:
        with pytest.raises(httpx.ConnectError):
            with transport._sync_response('GET', '/api/enterprise/sync/v1/changes'):
                pass
    finally:
        transport.close()
    rows = [json.loads(DiagnosticFormatter().format(row)) for row in caplog.records
            if getattr(row, 'mf_event', None) == 'sync']
    failure = next(row for row in rows if row.get('state') == 'REQUEST_FAILED')
    assert failure['phase'] == 'pull' and failure['error_type'] == 'ConnectError'
    assert re.fullmatch(r'[0-9a-f]{32}', failure['client_request_id'])
    encoded = json.dumps(rows)
    assert 'text must not appear' not in encoded and 'secret-token' not in encoded


def test_sync203_native_refresh_error_attaches_safe_client_and_server_ids():
    import asyncio
    from refactor.desktop.account_protocol import AccountConfig
    from refactor.desktop.native_auth import NativeCredentialClient

    async def run():
        config = AccountConfig('https://identity.example.test', 'mentorflow-desktop',
                               'mentorflow-api', 'https://sync203.example.test')
        requested = []
        async def response(request):
            requested.append(request.headers['X-Request-ID'])
            assert re.fullmatch(r'[0-9a-f]{32}', request.headers['X-Request-ID'])
            return httpx.Response(503, headers={'X-Request-ID': 'server-refresh-203'},
                                  json={'detail': 'private'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            native = NativeCredentialClient(config, http_client=client)
            with pytest.raises(Exception) as caught:
                await native._session_request('refresh', {'refresh_token': 'never-log-this'})
            error = caught.value
            assert error.code == 'NATIVE_AUTH_UNAVAILABLE'
            assert error.diagnostic_error_type == 'HTTPStatusError'
            assert re.fullmatch(r'[0-9a-f]{32}', error.client_request_id)
            assert error.client_request_id == requested[0]
            assert error.response_request_id == 'server-refresh-203'
            assert 'never-log-this' not in repr(error.__dict__)
    asyncio.run(run())


def test_sync203_native_refresh_network_failure_keeps_only_safe_metadata():
    import asyncio
    from refactor.desktop.account_protocol import AccountConfig
    from refactor.desktop.native_auth import NativeCredentialClient

    async def run():
        config = AccountConfig('https://identity.example.test', 'mentorflow-desktop',
                               'mentorflow-api', 'https://sync203.example.test')
        async def fail(request):
            raise httpx.ConnectError('sensitive exception message', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            native = NativeCredentialClient(config, http_client=client)
            with pytest.raises(Exception) as caught:
                await native._session_request('refresh', {'refresh_token': 'never-log-this'})
            error = caught.value
            assert error.code == 'NATIVE_NETWORK_UNAVAILABLE'
            assert error.diagnostic_error_type == 'ConnectError'
            assert re.fullmatch(r'[0-9a-f]{32}', error.client_request_id)
            assert error.response_request_id == ''
            assert 'sensitive exception message' not in repr(error.__dict__)
            assert 'never-log-this' not in repr(error.__dict__)
    asyncio.run(run())


def test_sync203_account_state_failure_during_refresh_has_its_own_request_id():
    import asyncio
    from refactor.desktop.account_protocol import AccountConfig
    from refactor.desktop.native_auth import NativeCredentialClient

    async def run():
        config = AccountConfig('https://identity.example.test', 'mentorflow-desktop',
                               'mentorflow-api', 'https://sync203.example.test')
        async def response(request):
            assert re.fullmatch(r'[0-9a-f]{32}', request.headers['X-Request-ID'])
            return httpx.Response(503, headers={'X-Request-ID': 'server-state-203'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            native = NativeCredentialClient(config, http_client=client)
            with pytest.raises(Exception) as caught:
                await native._state_request('/api/enterprise/account/workspaces', 'secret-token')
            error = caught.value
            assert error.code == 'ENTERPRISE_ACCOUNT_STATE_UNAVAILABLE'
            assert error.diagnostic_error_type == 'HTTPStatusError'
            assert re.fullmatch(r'[0-9a-f]{32}', error.client_request_id)
            assert error.response_request_id == 'server-state-203'
            assert 'secret-token' not in repr(error.__dict__)
    asyncio.run(run())
