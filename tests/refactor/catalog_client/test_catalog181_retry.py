"""Offline regression for catalog retry after transient failures."""
import httpx

from refactor.learning.course.catalog_client import REFRESH_SECONDS
from tests.refactor.catalog_client.test_catalog_client import Clock, catalogs, client


class ScheduledCall:
    def __init__(self, delay, callback):
        self.delay, self.callback, self.cancelled = delay, callback, False

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


class Scheduler:
    def __init__(self, fire_first=False):
        self.calls, self.fire_first = [], fire_first

    def __call__(self, delay, callback):
        call = ScheduledCall(delay, callback)
        self.calls.append(call)
        if self.fire_first:
            self.fire_first = False
            callback()
        return call


def test_catalog181_failure_backoff_persists_and_success_keeps_six_hour_cooldown(tmp_path):
    clock, calls = Clock(), []

    def handler(request):
        calls.append(request)
        if len(calls) <= 2:
            return httpx.Response(503)
        kind = request.url.path.rsplit('/', 1)[-1]
        return httpx.Response(200, json=catalogs()[kind], headers={'etag': '"retry-v1"'})

    with client(tmp_path, handler, clock=clock, jitter=lambda low, high: 1.0) as first:
        first.refresh_background().result(timeout=3)
        assert first.refresh_background() is None
        clock.now += 29
        assert first.refresh_background() is None

    # The 30-second retry deadline survives a process restart.
    with client(tmp_path, handler, clock=clock, jitter=lambda low, high: 1.0) as reopened:
        assert reopened.refresh_background() is None
        clock.now += 1
        statuses = reopened.refresh_background().result(timeout=3)
        assert all(status.code == 'UPDATED' for status in statuses.values())
        assert reopened.refresh_background() is None
        clock.now += REFRESH_SECONDS - 1
        assert reopened.refresh_background() is None
        clock.now += 1
        statuses = reopened.refresh_background().result(timeout=3)
        assert all(status.code == 'UPDATED' for status in statuses.values())

    assert len(calls) == 6


def test_catalog181_retry_fires_without_load_or_manual_refresh(tmp_path):
    clock, calls, scheduler = Clock(), [], Scheduler()

    def handler(request):
        calls.append(request)
        if len(calls) <= 2:
            return httpx.Response(503)
        kind = request.url.path.rsplit('/', 1)[-1]
        return httpx.Response(200, json=catalogs()[kind], headers={'etag': '"auto-v1"'})

    with client(tmp_path, handler, clock=clock, jitter=lambda low, high: 1.0,
                scheduler=scheduler) as catalog:
        initial = catalog.refresh_background()
        initial.result(timeout=3)
        assert len(calls) == 2
        assert len(scheduler.calls) == 1 and scheduler.calls[0].delay == 30
        clock.now += 30
        scheduler.calls[0].fire()
        automatic = catalog._future
        assert automatic is not initial
        statuses = automatic.result(timeout=3)
        assert len(calls) == 4
        assert all(status.code == 'UPDATED' for status in statuses.values())


def test_catalog181_reopened_client_schedules_persisted_retry(tmp_path):
    clock, calls = Clock(), []
    first_scheduler, resumed_scheduler = Scheduler(), Scheduler()

    def handler(request):
        calls.append(request)
        if len(calls) <= 2:
            return httpx.Response(503)
        kind = request.url.path.rsplit('/', 1)[-1]
        return httpx.Response(200, json=catalogs()[kind], headers={'etag': '"reopen-v1"'})

    with client(tmp_path, handler, clock=clock, jitter=lambda low, high: 1.0,
                scheduler=first_scheduler) as first:
        first.refresh_background().result(timeout=3)
    with client(tmp_path, handler, clock=clock, jitter=lambda low, high: 1.0,
                scheduler=resumed_scheduler) as resumed:
        assert len(resumed_scheduler.calls) == 1
        assert resumed_scheduler.calls[0].delay == 30
        clock.now += 30
        resumed_scheduler.calls[0].fire()
        statuses = resumed._future.result(timeout=3)
        assert len(calls) == 4
        assert all(status.code == 'UPDATED' for status in statuses.values())


def test_catalog181_completion_race_reschedules_and_close_cancels(tmp_path):
    clock, calls, scheduler = Clock(), [], Scheduler(fire_first=True)

    def handler(request):
        calls.append(request)
        if len(calls) <= 2:
            return httpx.Response(503)
        kind = request.url.path.rsplit('/', 1)[-1]
        return httpx.Response(200, json=catalogs()[kind], headers={'etag': '"race-v1"'})

    catalog = client(tmp_path, handler, clock=clock, jitter=lambda low, high: 1.0,
                     scheduler=scheduler)
    try:
        initial = catalog.refresh_background()
        initial.result(timeout=3)
        # The first callback ran inside the worker before its Future completed.
        # It must schedule another chance rather than losing the retry.
        assert len(scheduler.calls) == 2
        assert scheduler.calls[1].delay == 0.1
        clock.now += 30
        scheduler.calls[1].fire()
        statuses = catalog._future.result(timeout=3)
        assert len(calls) == 4
        assert all(status.code == 'UPDATED' for status in statuses.values())
        assert scheduler.calls[0].cancelled
    finally:
        catalog.close()

    cancelled_scheduler = Scheduler()
    another = client(tmp_path / 'close', lambda request: httpx.Response(503),
                     clock=Clock(), jitter=lambda low, high: 1.0,
                     scheduler=cancelled_scheduler)
    another.refresh_background().result(timeout=3)
    pending = cancelled_scheduler.calls[0]
    another.close()
    assert pending.cancelled
    pending.fire()
    assert len(cancelled_scheduler.calls) == 1


def test_catalog181_legacy_attempt_does_not_migrate_to_success_cooldown(tmp_path):
    clock = Clock()
    with client(tmp_path, lambda request: httpx.Response(503), clock=clock,
                jitter=lambda low, high: 1.0) as seeded:
        state_path = seeded.cache_dir / 'refresh-state.json'
        seeded._write('refresh-state.json', seeded._envelope(last_attempt=clock()))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with client(tmp_path, handler, clock=clock, jitter=lambda low, high: 1.0) as migrated:
        result = migrated.refresh_background()
        result.result(timeout=3)
    assert len(calls) == 2
