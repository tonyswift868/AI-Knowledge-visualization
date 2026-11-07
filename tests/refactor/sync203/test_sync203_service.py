"""Offline BUG-130 state-machine coverage (no network or account data)."""
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from refactor.learning.app import sync as window_sync_module
from refactor.learning.app.sync import WindowSync
from refactor.learning.course.store import CourseStore
from refactor.learning.services.course_sync_service import CourseSyncService
from refactor.learning.sync_identity import PlanError


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        return self.value


class ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:  # Keep worker exceptions observable via Future.
            future.set_exception(exc)
        return future

    def shutdown(self, **kwargs):
        pass


class Store:
    def __init__(self, pending=0):
        self.pending = pending

    def pending_records(self, limit):
        return [object() for _ in range(self.pending)][:limit]


class Engine:
    def __init__(self, store, transport):
        self.store = store
        self.outcomes = []
        self.calls = 0
        self.closed = False

    def sync(self, *, cancel):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else {'ok': True, 'pending': 0}
        if isinstance(outcome, Exception):
            raise outcome
        self.store.pending = outcome.get('pending', 0)
        return outcome

    def cancel(self):
        pass

    def close(self):
        self.closed = True


class Guard(QObject):
    superseded = Signal()
    reauth_required = Signal()

    def __init__(self):
        super().__init__()
        self.errors = []

    def note_error(self, code):
        self.errors.append(code)


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def rig(app):
    clock = Clock()
    base = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    store, guard, ready = Store(), Guard(), {'value': True}
    service = CourseSyncService(
        store,
        object(),
        guard,
        clock=clock,
        wall_clock=lambda: base + timedelta(seconds=clock.value),
        auto_start=False,
        executor=ImmediateExecutor(),
        sync_factory=Engine,
        session_ready=lambda: ready['value'],
    )
    yield service, clock, base, store, guard, ready
    service.dispose()


def settle(service):
    """Advance the immediate executor's future through the normal service path."""
    service._tick()
    service._tick()


def test_sync203_network_error_enters_retrying_with_truthful_details(rig):
    service, clock, base, store, _, _ = rig
    store.pending = 4
    service._sync.outcomes = [OSError('offline')]
    service.start()
    settle(service)

    status = service.status()
    assert status['state'] == 'retrying'
    assert status['pending'] == 4
    assert datetime.fromisoformat(status['retry_at']) == base + timedelta(seconds=30)
    assert status['last_error']['code']
    assert status['last_error']['message']
    assert datetime.fromisoformat(status['last_error']['at']) == base
    assert service._active


def test_sync203_persistent_failure_keeps_retrying_with_capped_backoff_then_recovers(rig):
    service, clock, base, store, _, _ = rig
    store.pending = 2
    service._sync.outcomes = [OSError('offline')] * 6 + [{'ok': True, 'pending': 0}]
    service.start()
    settle(service)

    for delay in (60, 120, 240, 300, 300):
        assert service.status()['state'] == 'retrying'
        # Set the monotonic clock to the next scheduled retry; backoff must not
        # collapse when the worker keeps failing.
        clock.value = service._retry_at
        settle(service)
        status = service.status()
        assert status['state'] == 'retrying'
        assert status['pending'] == 2
        assert datetime.fromisoformat(status['retry_at']) == base + timedelta(seconds=clock.value + delay)

    assert service._active
    clock.value = service._retry_at
    settle(service)
    status = service.status()
    assert status['state'] == 'synced'
    assert status['pending'] == 0
    assert status['retry_at'] is None
    assert status['last_error']['code']
    assert status['last_error']['message']
    assert datetime.fromisoformat(status['at']) == base + timedelta(seconds=clock.value)

    # A later local edit makes the last round's "synced" receipt stale. The
    # status queried by the UI must reflect the current outbox, not that receipt.
    store.pending = 1
    stale_receipt = service.status()
    assert stale_receipt['state'] == 'syncing'
    assert stale_receipt['pending'] == 1
    assert '等待同步' in stale_receipt['message']


def test_sync203_start_waits_for_session_ready_without_running_sync(rig):
    service, _, _, _, _, ready = rig
    ready['value'] = False
    service.start()

    assert service.status()['state'] == 'waiting'
    assert not service._active
    assert service._sync.calls == 0

    ready['value'] = True
    service.start()
    settle(service)
    assert service._sync.calls == 1
    assert service.status()['state'] == 'synced'


@pytest.mark.parametrize('resume', ['session_event', 'manual_retry'])
def test_sync203_window_start_does_not_probe_until_provider_is_ready(app, monkeypatch, resume):
    calls = {'http': 0, 'guard_start': 0}
    ready = {'session': None}

    class Transport:
        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

    class Guard(QObject):
        superseded = Signal()
        reauth_required = Signal()

        def __init__(self, context, parent=None, **kwargs):
            super().__init__(parent)

        def start(self):
            calls['guard_start'] += 1

        def set_session_epoch(self, _epoch):
            pass

        def stop(self):
            pass

        def dispose(self):
            pass

    class Bridge(QObject):
        request_failed_for_session = Signal(str, int)

        def begin_session_epoch(self):
            return 1

        def __init__(self):
            super().__init__()
            self.events = []

        def push(self, name, data):
            self.events.append((name, data))

        def cancel_pending(self):
            pass

    class SubscribableStore(Store):
        def subscribe(self, callback):
            return lambda: None

    monkeypatch.setattr(window_sync_module, 'SyncTransport', Transport)
    monkeypatch.setattr(window_sync_module, 'SessionGuard', Guard)
    store, bridge = SubscribableStore(), Bridge()
    services = SimpleNamespace(
        settings=object(),
        account_id='fixture-account',
        session_provider=lambda: ready['session'],
        api_base_url='https://fixture.invalid',
        ca_bundle='fixture-ca',
        store=store,
        # No learning jobs are suspended in this readiness fixture.
        resume_learning=lambda: False,
    )
    window = QObject()
    window.bridge = bridge
    window.settings = services.settings
    window.session_notice = None
    sync = WindowSync(window, services, sync_options={
        'executor': ImmediateExecutor(),
        'sync_factory': Engine,
    })
    try:
        sync.start()
        assert sync.status()['state'] == 'waiting'
        assert calls == {'http': 0, 'guard_start': 0}
        assert sync.service._sync.calls == 0
        sync.sync_now()
        assert sync.status()['state'] == 'waiting' and sync.service._sync.calls == 0

        ready['session'] = object()
        if resume == 'session_event':
            sync.start()  # The account-shell session_recovered event reaches this method.
        else:
            sync.sync_now()
        settle(sync.service)
        assert sync.service._sync.calls == 1
        assert calls['guard_start'] == 1
        assert sync.status()['state'] == 'synced'
    finally:
        sync.dispose()


@pytest.mark.parametrize('code,state', [
    ('REAUTH_REQUIRED', 'reauth'),
    ('ENTERPRISE_SESSION_REAUTH_REQUIRED', 'reauth'),
    ('NATIVE_SESSION_SUPERSEDED', 'superseded'),
])
def test_sync203_auth_and_superseded_stop_without_retry_schedule(rig, code, state):
    service, _, _, store, guard, _ = rig
    store.pending = 3
    service._sync.outcomes = [PlanError(code, 'synthetic refusal')]
    service.start()
    settle(service)

    status = service.status()
    assert status['state'] == state
    assert status['pending'] == 3
    assert status['retry_at'] is None
    assert not service._active
    assert guard.errors == ([code] if code == 'NATIVE_SESSION_SUPERSEDED' else [])


def test_sync203_real_outbox_count_includes_new_records_and_success_time_survives_reopen(tmp_path):
    account, stamp = 'sync203-fixture-account', '2026-10-27T12:34:56+00:00'
    with CourseStore(account, tmp_path) as store:
        before = store.pending_count()
        store.create_plan('sync203-plan', goal='计划')
        store.create_chat_session('新对话', session_id='sync203-chat')
        expected_pending = store.pending_count()
        assert expected_pending >= before + 2
        store.set_sync_success_at(stamp)
        assert store.pending_count() == expected_pending
        assert store.get_sync_success_at() == stamp

    with CourseStore(account, tmp_path) as reopened:
        assert reopened.pending_count() == expected_pending
        assert reopened.get_sync_success_at() == stamp
