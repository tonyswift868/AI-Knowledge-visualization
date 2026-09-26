import hashlib
from threading import Event
import time
from types import SimpleNamespace as NS

import certifi
import httpx
import pytest
from PySide6.QtCore import QCoreApplication, QTimer

from refactor.learning.services.session_guard import SessionGuard, SESSION_PATH, SUPERSEDED_MESSAGE


@pytest.fixture(scope='module')
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def pump(app, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


@pytest.fixture
def make_guard(app):
    guards = []
    def make(handler):
        issuer, subject = 'https://identity.invalid/realm', 'guard-user'
        hashes = ':'.join(hashlib.sha256(s.encode()).hexdigest() for s in (issuer, subject))
        account = 'mfa1_' + hashlib.sha256(('mentorflow.account.v1:' + hashes).encode()).hexdigest()
        login = NS(access_token='first-token',
                   identity=NS(account_id=account, issuer=issuer, subject=subject,
                               organization_id='org-one', workspace_id='workspace-one'),
                   server_state={'authenticated': True, 'account': {'account_id': account, 'subject_id': subject},
                                 'context': {'organization_id': 'org-one', 'workspace_id': 'workspace-one'}})
        calls = []
        def handle(request):
            assert request.method == 'GET' and request.url.path == SESSION_PATH
            calls.append(request.headers['Authorization'])
            return handler(request)
        context = dict(account_id=account, session_provider=lambda: login,
                       api_base_url='https://api.invalid', ca_bundle=certifi.where())
        guard = SessionGuard(context, http_transport=httpx.MockTransport(handle))
        events = []
        guard.superseded.connect(lambda: events.append('superseded'))
        guard.reauth_required.connect(lambda: events.append('reauth'))
        guards.append(guard)
        return guard, login, calls, events
    yield make
    for guard in guards:
        guard.dispose()
    pump(app, lambda: all(g._future is None for g in guards))


def test_active_60_second_schedule_and_fresh_token(app, make_guard):
    guard, login, calls, events = make_guard(lambda request: httpx.Response(200, json={'state': 'active'}))
    guard.start()
    guard.start()
    pump(app, lambda: guard._timer.isActive())
    assert guard._timer.interval() == 60_000
    assert calls == ['Bearer first-token'] and not events
    login.access_token = 'rotated-token'
    guard._launch()
    pump(app, lambda: guard._future is None)
    assert calls == ['Bearer first-token', 'Bearer rotated-token']
    guard.stop()
    assert not guard._timer.isActive()


@pytest.mark.parametrize('body,event', [
    ({'code': 'NATIVE_SESSION_SUPERSEDED'}, 'superseded'),
    ({'error': {'code': 'NATIVE_SESSION_SUPERSEDED'}}, 'superseded'),
    ({'code': 'ENTERPRISE_SESSION_REAUTH_REQUIRED'}, 'reauth'),
    ({'code': 'unknown'}, 'reauth'),
])
def test_401_results_stop_checks(app, make_guard, body, event):
    guard, _, calls, events = make_guard(lambda request: httpx.Response(401, json=body))
    guard.start()
    pump(app, lambda: bool(events))
    assert events == [event] and len(calls) == 1
    assert not guard._active and not guard._timer.isActive()
    assert SUPERSEDED_MESSAGE == '账号已在另一台设备登录'


def test_malformed_401_still_requests_reauthentication(app, make_guard):
    guard, _, _, events = make_guard(lambda request: httpx.Response(401, content=b'not json'))
    guard.start()
    pump(app, lambda: bool(events))
    assert events == ['reauth']


def test_network_failure_exponential_backoff_and_success_reset(app, make_guard):
    state = {'ok': False}
    def handler(request):
        if not state['ok']:
            raise httpx.ConnectError('synthetic offline')
        return httpx.Response(200, json={'state': 'active'})
    guard, _, _, events = make_guard(handler)
    guard.start()
    for expected in [120_000, 240_000, 480_000, 900_000, 900_000]:
        pump(app, lambda: guard._future is None)
        assert guard._timer.interval() == expected and not events
        guard._launch()
    pump(app, lambda: guard._future is None)
    state['ok'] = True
    guard._launch()
    pump(app, lambda: guard._future is None)
    assert guard._timer.interval() == 60_000 and guard._failures == 0


@pytest.mark.parametrize('status,body', [(503, {}), (200, {'state': 'unknown'}), (302, {})])
def test_unavailable_or_invalid_response_is_quiet_retry(app, make_guard, status, body):
    guard, _, _, events = make_guard(lambda request: httpx.Response(status, json=body))
    guard.start()
    pump(app, lambda: guard._future is None)
    assert not events and guard._timer.interval() == 120_000


def test_ui_stays_responsive_and_stop_fences_late_result(app, make_guard):
    entered, release = Event(), Event()
    def handler(request):
        entered.set()
        assert release.wait(5)
        return httpx.Response(401, json={'code': 'NATIVE_SESSION_SUPERSEDED'})
    guard, _, calls, events = make_guard(handler)
    ticks = []
    guard.start()
    QTimer.singleShot(0, lambda: ticks.append('responsive'))
    try:
        pump(app, lambda: entered.is_set() and bool(ticks))
        guard.start()
        assert len(calls) == 1
        guard.stop()
    finally:
        release.set()
    pump(app, lambda: guard._future is None)
    assert not events and not guard._timer.isActive()


def test_note_error_from_any_request_and_dispose_are_idempotent(app, make_guard):
    guard, login, calls, events = make_guard(lambda request: httpx.Response(200, json={'state': 'active'}))
    guard.note_error('NETWORK_ERROR')
    guard.note_error('NATIVE_SESSION_SUPERSEDED')
    guard.note_error('NATIVE_SESSION_SUPERSEDED')
    pump(app, lambda: bool(events))
    assert events == ['superseded'] and not calls
    guard.start()
    pump(app, lambda: guard._future is None)
    guard.note_error('REAUTH_REQUIRED')
    pump(app, lambda: len(events) == 2)
    assert events == ['superseded', 'reauth']
    guard.dispose(); guard.dispose()
    guard.note_error('NATIVE_SESSION_SUPERSEDED')
    app.processEvents()
    assert len(events) == 2 and login.access_token == 'first-token'


def test_restart_ignores_old_response_and_never_overlaps_requests(app, make_guard):
    entered, release = Event(), Event()
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            entered.set()
            assert release.wait(5)
            return httpx.Response(401, json={'code': 'NATIVE_SESSION_SUPERSEDED'})
        return httpx.Response(200, json={'state': 'active'})
    guard, login, calls, events = make_guard(handler)
    guard.start()
    try:
        pump(app, lambda: entered.is_set())
        guard.stop()
        login.access_token = 'next-lesson-token'
        guard.start()
        assert len(calls) == 1
    finally:
        release.set()
    pump(app, lambda: len(calls) == 2 and guard._future is None)
    assert not events and guard._timer.interval() == 60_000
    assert calls[-1] == 'Bearer next-lesson-token'
