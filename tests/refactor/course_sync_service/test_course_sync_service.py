from concurrent.futures import Future
from datetime import datetime
from threading import Event
import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

from refactor.learning.sync_identity import PlanError
from refactor.learning.services.course_sync_service import CourseSyncService


class Clock:
    value = 0
    def __call__(self): return self.value


class ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        try: future.set_result(fn(*args, **kwargs))
        except Exception as exc: future.set_exception(exc)
        return future
    def shutdown(self, **kwargs): pass


class Store:
    pending = False
    def pending_records(self, limit): return [object()] if self.pending else []


class Engine:
    def __init__(self, store, transport):
        self.store = store
        self.calls = 0
        self.failure = None
        self.closed = False
    def sync(self, *, cancel):
        self.calls += 1
        if self.failure: raise self.failure
        self.store.pending = False
        return dict(ok=True, pending=0)
    def cancel(self): pass
    def close(self): self.closed = True


class Guard(QObject):
    superseded = Signal()
    reauth_required = Signal()
    def __init__(self):
        super().__init__()
        self.errors = []
    def note_error(self, code): self.errors.append(code)


@pytest.fixture(scope='module')
def app(): return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def setup(app):
    clock, store, guard = Clock(), Store(), Guard()
    service = CourseSyncService(store, object(), guard, clock=clock, wall_clock=lambda: datetime(2026, 9, 28, 12),
                                auto_start=False, executor=ImmediateExecutor(), sync_factory=Engine)
    statuses = []
    service.status_changed.connect(statuses.append)
    yield service, clock, store, guard, statuses
    service.dispose()


def tick(service, clock, at):
    clock.value = at
    service._tick()
    service._tick()


def test_login_periodic_and_manual_sync(setup):
    s, c, store, guard, statuses = setup
    s.start(); tick(s, c, 0)
    assert s._sync.calls == 1 and statuses == ['同步中', '已同步 12:00:00']
    tick(s, c, 299); assert s._sync.calls == 1
    tick(s, c, 300); assert s._sync.calls == 2
    s.sync_now(); tick(s, c, 301); assert s._sync.calls == 3


def test_automatic_store_probe_and_fixed_debounce(setup, app):
    s, c, store, guard, statuses = setup
    s.start(); tick(s, c, 0)
    store.pending = True
    tick(s, c, 1)
    for at in (2, 3):
        c.value = at
        s.notify_local_change(); app.processEvents(); tick(s, c, at)
    assert s._sync.calls == 1
    tick(s, c, 4)
    assert s._sync.calls == 2 and not store.pending


def test_change_just_after_clean_probe_runs_within_five_seconds(setup):
    s, c, store, guard, statuses = setup
    s.start(); tick(s, c, 0)
    c.value = 1
    s._tick()  # A clean probe has read the store; its completion is still queued.
    changed_at = 1.01
    store.pending = True
    step = s._timer.interval() / 1000
    # One completion/launch cycle per real timer interval, rather than settling
    # every future immediately at the same fake time as most unit tests do.
    while s._sync.calls == 1 and c.value < changed_at + 6:
        c.value += step
        s._tick()
    assert s._sync.calls == 2
    assert 3 <= c.value - changed_at <= 5


def test_failure_backoff_not_bypassed_by_pending_changes(setup):
    s, c, store, guard, statuses = setup
    s._sync.failure = OSError('offline')
    s.start(); tick(s, c, 0)
    assert s.status()['state'] == 'retrying'
    assert statuses[-1] == '正在重试'
    s._on_change()
    tick(s, c, 29); assert s._sync.calls == 1
    tick(s, c, 30); assert s._sync.calls == 2
    tick(s, c, 89); assert s._sync.calls == 2
    s._sync.failure = None
    tick(s, c, 90); assert s._sync.calls == 3
    assert statuses[-1] == '已同步 12:00:00'
    tick(s, c, 389); assert s._sync.calls == 3
    tick(s, c, 390); assert s._sync.calls == 4


@pytest.mark.parametrize('code,status', [('NATIVE_SESSION_SUPERSEDED', '账号已在另一台设备登录'),
                                       ('REAUTH_REQUIRED', '需要重新登录')])
def test_auth_stops_until_login_and_hands_superseded_to_guard(setup, code, status):
    s, c, store, guard, statuses = setup
    s._sync.failure = PlanError(code, 'synthetic')
    store.pending = True
    s.start(); tick(s, c, 0)
    assert not s._active and statuses[-1] == status and store.pending
    s.sync_now(); tick(s, c, 1000)
    assert s._sync.calls == 1
    assert guard.errors == (['NATIVE_SESSION_SUPERSEDED'] if code == 'NATIVE_SESSION_SUPERSEDED' else [])
    s._sync.failure = None
    s.start(); tick(s, c, 1001)
    assert s._sync.calls == 2


def test_guard_signal_stops_scheduled_rounds(setup):
    s, c, store, guard, statuses = setup
    s.start(); tick(s, c, 0)
    guard.superseded.emit()
    tick(s, c, 400)
    assert s._sync.calls == 1 and not s._active


def test_close_cancels_inflight_on_worker_without_losing_data(app):
    entered, exited = Event(), Event()
    class Blocking(Engine):
        def sync(self, *, cancel):
            entered.set()
            assert cancel.wait(5)
            exited.set()
            raise PlanError('CANCELLED', 'cancelled')
    store, transport, guard = Store(), object(), Guard()
    store.pending = True
    s = CourseSyncService(store, transport, guard, sync_factory=Blocking)
    try:
        assert entered.wait(5)
        s.sync_now(); s.notify_local_change()
        started = time.monotonic()
        s.dispose()
        assert time.monotonic() - started < 1
        assert exited.wait(5) and store.pending and s._sync.closed
        assert not guard.errors
    finally:
        s.dispose()
