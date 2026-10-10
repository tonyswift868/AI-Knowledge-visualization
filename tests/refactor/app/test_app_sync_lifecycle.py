"""New window + real workers/stores/transports; HTTP stays entirely in memory."""
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from threading import Event, get_ident
import time

import certifi
import httpx
import pytest

from refactor.learning.app import window as window_module
from refactor.learning.app.services import AppServices
from refactor.learning.app.sync import WindowSync
from refactor.learning.app.chat_store import CourseChatStore
from refactor.learning.course.store import CourseStore
from refactor.learning.course.sync_course import CourseSync
from refactor.learning.sync_identity import PlanError

from .test_app_free_chat import ask
from .test_app_window_layer import qapp, tmp_root  # noqa: F401

_spec = importlib.util.spec_from_file_location('window_sync_harness', Path(__file__).parents[1] / 'sync_course/test_sync_course.py')
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)
connection = harness.connection
NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


class Clock:
    value = 0
    def __call__(self): return self.value


def spin(qapp, win, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if win._sync is not None:
            win._sync.service._tick()
        if predicate(): return
        time.sleep(.005)
    raise AssertionError(f'window did not settle: {win.sync_status()}')


@pytest.fixture
def window(qapp, tmp_root, connection, monkeypatch):
    import json
    server, transport, login = connection
    server.incremental = True
    clock, mode, requests, sent = Clock(), {'session': 'active'}, [], []
    main_thread = get_ident()
    def api(request):
        requests.append((request.url.path, request.headers['Authorization'], get_ident()))
        assert get_ident() != main_thread  # real window HTTP never blocks Qt
        if request.url.path.endswith('/native/session'):
            if mode['session'] == 'offline': raise httpx.ConnectError('offline')
            if mode['session'] != 'active':
                return httpx.Response(401, json={'error': {'code': mode['session']}})
            return httpx.Response(200, json={'state': 'active'})
        return server.handle(request)
    monkeypatch.setattr(AppServices, 'refresh_catalog', lambda self: None)
    monkeypatch.setattr(window_module, 'WindowSync', lambda w, s: WindowSync(w, s,
        http_transport=httpx.MockTransport(api), sync_options={'clock': clock}))
    win = window_module.LearningWindow('同步测试', 'LEARNER', 'api.invalid',
                                       account_id=login.identity.account_id, data_root=tmp_root)
    win.bridge.event.connect(lambda text: sent.append(json.loads(text)))
    assert win.bind_feature_context(session_provider=lambda: login, api_base_url='https://api.invalid', ca_bundle=certifi.where())
    spin(qapp, win, lambda: win.sync_status()['state'] == 'synced' and win._sync.service._future is None
         and win._sync.guard._future is None)
    yield win, server, transport, login, clock, mode, requests, sent
    win.dispose()
    qapp.processEvents()


def states(sent):
    return [m['data'] for m in sent if m.get('type') == 'event' and m.get('name') == 'sync_status']


def test_merged_field_conflict_pushes_one_user_notice(window, qapp):
    win, _, _, _, _, _, _, sent = window
    message = '计划「同步测试」的计划信息有两端修改：按字段更新时间保留本机 1 项、云端 0 项。'
    win._sync._status_changed(dict(state='synced', at=None, message='已同步', merged_conflicts=1,
                                  merge_messages=[message]))
    spin(qapp, win, lambda: any(item.get('name') == 'sync_conflict' for item in sent))
    notices = [item for item in sent if item.get('name') == 'sync_conflict']
    assert len(notices) == 1
    assert notices[0]['data']['message'] == message


def test_sync95_pulled_plan_notifies_before_upload_finishes(window, qapp, tmp_root):
    win, server, transport, login, clock, mode, requests, sent = window
    assert not any(p['id'] == 'cloud-plan' for p in win.services.store.list_plans())
    with CourseStore(login.identity.account_id, tmp_root / 'second-device') as remote:
        remote.create_plan('cloud-plan', goal='云端新计划')
        assert CourseSync(remote, transport).sync()['ok']
    win.services.store.create_chat_session('待上传', session_id='blocked', created_at=NOW)
    entered, release = Event(), Event()
    def block(request):
        if request.method == 'PUT':
            entered.set()
            assert release.wait(10)
    server.before = block
    sent.clear()
    try:
        win.sync_now()
        spin(qapp, win, lambda: entered.is_set() and any(m.get('name') == 'plans_changed' for m in sent))
        assert win._sync.service._future is not None and not win._sync.service._future[0].done()
        response = ask(qapp, win.bridge, sent, 'pulled-plan', 'plans.list')
        assert any(p['id'] == 'cloud-plan' for p in response['result']['plans'])
    finally:
        release.set()
    spin(qapp, win, lambda: win._sync.service._future is None)


def test_sync95_partial_failure_backoff_and_progress_are_not_round_failure(window, qapp):
    import json
    win, server, _, _, clock, _, _, sent = window
    win.services.store.create_chat_session('成功记录', session_id='good', created_at=NOW)
    win.services.store.create_chat_session('重试记录', session_id='bad', created_at=NOW)
    def reject(request):
        if request.method == 'PUT' and json.loads(request.content)['payload']['values']['payload']['id'] == 'bad':
            return httpx.Response(503)
    server.before = reject
    sent.clear(); win.sync_now()
    spin(qapp, win, lambda: win._sync.service._future is None and '待重试' in win.sync_status()['message'])
    spin(qapp, win, lambda: any(s.get('completed') == 1 and s.get('total') == 2 for s in states(sent)))
    assert win.sync_status()['state'] == 'syncing'
    assert not any(s['state'] == 'failed' for s in states(sent))
    assert any(s.get('completed') == 1 and s.get('total') == 2 for s in states(sent)), states(sent)
    assert len(win.services.store.pending_records(100)) == 1
    assert win._sync.service._retry_at == 30
    calls = len(server.calls); clock.value = 29; win._sync.service._tick()
    assert len(server.calls) == calls
    server.before = None; clock.value = 30
    spin(qapp, win, lambda: win.sync_status()['state'] == 'synced')
    assert not win.services.store.pending_records(100)


def test_sync95_stale_progress_cannot_revive_stopped_session(window, qapp):
    win, _, _, _, _, _, _, sent = window
    service = win._sync.service
    cancel = service._cancel
    service.stop(); service.start()
    sent.clear()
    service._show_progress(dict(phase='upload', completed=7, total=10, failed=0), cancel)
    assert not any(s.get('completed') == 7 for s in states(sent))


def test_bind_immediate_sync_local_debounce_manual_and_fresh_token(window, qapp):
    win, server, transport, login, clock, mode, requests, sent = window
    assert states(sent)[0] == dict(state='syncing', at=None, message='同步中')
    first = win.sync_status()
    assert first['state'] == 'synced' and datetime.fromisoformat(first['at']).tzinfo
    assert win._sync.guard._timer.interval() == 60_000
    assert ask(qapp, win.bridge, sent, 'status', 'sync.status')['result'] == first
    chat = win.services.chat
    session = chat.create(session_id='debounced', created_at=NOW)
    chat.commit_turn('debounced', expected_version=session.version, user_content='你好', assistant_content='回复', committed_at=NOW)
    qapp.processEvents()
    assert win._sync.service._local_due == 3
    calls = len(requests)
    clock.value = 2.99
    win._sync.service._tick()
    assert len(requests) == calls and len(win.services.store.pending_records(100)) == 3
    clock.value = 3
    # Account teacher presets now share the real outbox, alongside these 3 chat records.
    spin(qapp, win, lambda: len(server.rows) == 4 and win._sync.service._future is None)
    assert not win.services.store.pending_records(100)
    assert not [m for m in sent if m.get('name') == 'chat_changed']  # no remote-change echo for local writes
    login.access_token = 'fresh-token-after-refresh'
    clock.value = 4
    before = len(requests)
    assert ask(qapp, win.bridge, sent, 'now', 'sync.now')['ok']
    spin(qapp, win, lambda: win._sync.service._future is None and len(requests) > before)
    win._sync.guard._launch()
    spin(qapp, win, lambda: win._sync.guard._future is None)
    assert all(token == 'Bearer fresh-token-after-refresh' for _, token, _ in requests[before:])
    assert {s['state'] for s in states(sent)} == {'syncing', 'synced'}


def test_remote_chat_changes_push_refresh_after_projection(window, qapp, tmp_root):
    win, server, transport, login, clock, mode, requests, sent = window
    with CourseStore(login.identity.account_id, tmp_root / 'remote') as remote:
        chat = CourseChatStore(remote)
        current = chat.create(session_id='remote-chat', created_at=NOW)
        chat.commit_turn(current.session_id, expected_version=current.version, user_content='另一台设备', assistant_content='回复', committed_at=NOW)
        CourseSync(remote, transport).sync()
        win.sync_now()
        spin(qapp, win, lambda: any(m.get('name') == 'chat_changed' for m in sent)
             and win._sync.service._future is None)
        notice = next(m for m in sent if m.get('name') == 'chat_changed')
        assert notice['data'] == {}
        result = ask(qapp, win.bridge, sent, 'remote', 'chat.get', {'session_id': 'remote-chat'})
        assert result['result']['session']['title'] == '另一台设备'
        sent.clear()
        chat.delete('remote-chat'); CourseSync(remote, transport).sync()
        win.sync_now()
        spin(qapp, win, lambda: any(m.get('name') == 'chat_changed' for m in sent))
        assert ask(qapp, win.bridge, sent, 'deleted', 'chat.list')['result'] == {'sessions': []}


@pytest.mark.parametrize('source', ['sync', 'guard', 'bridge'])
def test_superseded_stops_notice_preserves_pending_and_relogin_resumes(window, qapp, source):
    win, server, transport, login, clock, mode, requests, sent = window
    win.services.store.create_chat_session('待上传', session_id='pending', created_at=NOW)
    pending = win.services.store.pending_records(100)
    code = 'NATIVE_SESSION_SUPERSEDED'
    if source == 'sync':
        server.before = lambda req: httpx.Response(401, json={'error': {'code': code}})
        win.sync_now()
    elif source == 'guard':
        mode['session'] = code
        win._sync.guard._launch()
    else:
        def fail(params, ctx): raise PlanError(code, '账号已在另一台设备登录')
        win.bridge.register('test.auth', fail)
        ask(qapp, win.bridge, sent, 'failed', 'test.auth')
    spin(qapp, win, lambda: win.sync_status()['state'] == 'superseded')
    assert win.session_notice == {'message': '账号已在另一台设备登录', 'show_verify': True}
    assert not win._sync.service._active and not win._sync.guard._active
    assert win.services.store.pending_records(100) == pending
    qapp.processEvents()
    calls = len(requests)
    clock.value = 600
    win.sync_now(); win._sync.service._tick()
    assert len(requests) == calls and win.sync_status()['state'] == 'superseded'
    server.before = None; mode['session'] = 'active'; login.access_token = 'new-login'
    win.clear_session_notice(); win.session_recovered()
    spin(qapp, win, lambda: win.sync_status()['state'] == 'synced' and not win.services.store.pending_records(100))
    assert win.session_notice is None and server.rows
    assert all(token == 'Bearer new-login' for _, token, _ in requests[calls:])


def test_reauth_and_network_failure_status_keep_last_success(window, qapp):
    win, server, transport, login, clock, mode, requests, sent = window
    last = win.sync_status()['at']
    server.before = lambda req: httpx.Response(503)
    win.sync_now()
    spin(qapp, win, lambda: win.sync_status()['state'] == 'failed')
    assert win.sync_status()['at'] == last
    calls = len(requests)
    clock.value = 29; win._sync.service._tick()
    assert len(requests) == calls
    server.before = lambda req: httpx.Response(401, json={'error': {'code': 'NATIVE_SESSION_INVALID'}})
    clock.value = 30
    spin(qapp, win, lambda: win.sync_status()['state'] == 'reauth')
    assert win.session_notice['show_verify'] and not win._sync.service._active
    assert win.sync_status()['at'] == last
    qapp.processEvents()  # bridge notifications are queued onto Qt
    assert 'failed' in {s['state'] for s in states(sent)} and 'reauth' in {s['state'] for s in states(sent)}


@pytest.mark.parametrize('action', ['logout', 'switch'])
def test_account_actions_stop_and_rebind_reopens_pending(window, qapp, action):
    win, server, transport, login, clock, mode, requests, sent = window
    win.services.store.create_chat_session('未发完', session_id='pending', created_at=NOW)
    old_store, old_sync = win.services.store, win._sync
    pending = old_store.pending_records(100)
    win.request_account_action(action)
    assert not old_sync.service._active and not old_sync.guard._active
    calls = len(requests)
    clock.value = 1000; win.sync_now(); old_sync.service._tick()
    assert len(requests) == calls and old_store.pending_records(100) == pending
    assert win.rebind_feature_context(session_provider=lambda: login, api_base_url='https://api.invalid', ca_bundle=certifi.where())
    assert old_sync._closed and old_store._closed
    spin(qapp, win, lambda: win.sync_status()['state'] == 'synced' and not win.services.store.pending_records(100))
    assert server.rows


def test_window_close_cancels_blocked_upload_without_clearing_outbox(window, qapp, tmp_root):
    win, server, transport, login, clock, mode, requests, sent = window
    entered, release = Event(), Event()
    def block(request):
        if request.method == 'PUT':
            entered.set()
            assert release.wait(5)
    server.before = block
    win.services.store.create_chat_session('退出前未上传', session_id='pending', created_at=NOW)
    pending = win.services.store.pending_records(100)
    old_sync = win._sync
    win.sync_now()
    try:
        assert entered.wait(3)
        start = time.monotonic()
        win.dispose()
        assert time.monotonic() - start < 1
        release.set()
        with pytest.raises(PlanError) as exc:
            old_sync.service._future[0].result(timeout=3)
        assert exc.value.code == 'CANCELLED'
        with CourseStore(login.identity.account_id, tmp_root) as reopened:
            assert reopened.pending_records(100) == pending
            assert CourseSync(reopened, transport).sync()['ok']  # recover idempotently after the late server commit
    finally:
        release.set()


def test_guarded_provider_rejects_another_account_before_any_http(window, qapp):
    from copy import deepcopy
    win, server, transport, login, clock, mode, requests, sent = window
    other = deepcopy(login)
    other.identity.account_id = 'mfa1_' + 'e' * 64
    win.services.store.create_chat_session('当前账号数据', session_id='pending', created_at=NOW)
    calls = len(requests)
    assert win.rebind_feature_context(session_provider=lambda: other, api_base_url='https://api.invalid', ca_bundle=certifi.where())
    spin(qapp, win, lambda: win.sync_status()['state'] == 'failed')
    assert win._sync.service._active and not win.session_notice['show_verify']
    assert len(requests) == calls and win.services.store.pending_records(100)


def test_cancelled_old_request_error_cannot_supersede_rebound_session(window, qapp):
    from .test_app_window_layer import pump
    win, server, transport, login, clock, mode, requests, sent = window
    entered, release = Event(), Event()
    def old_request(params, ctx):
        entered.set()
        assert release.wait(5)
        raise PlanError('NATIVE_SESSION_SUPERSEDED', '账号已在另一台设备登录')
    win.bridge.register('test.old', old_request)
    win.bridge.call('{"id":"old-session-request","method":"test.old"}')
    try:
        assert entered.wait(3)
        assert win.rebind_feature_context(session_provider=lambda: login, api_base_url='https://api.invalid', ca_bundle=certifi.where())
        release.set()
        response = pump(qapp, win.bridge, sent, lambda m: m.get('id') == 'old-session-request')[0]
        assert response['error']['code'] == 'CANCELLED'
        spin(qapp, win, lambda: win.sync_status()['state'] == 'synced')
        qapp.processEvents()
        assert win._sync.service._active and win._sync.guard._active and win.session_notice is None
        assert 'superseded' not in {s['state'] for s in states(sent)}
    finally:
        release.set()
