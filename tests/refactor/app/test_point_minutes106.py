"""BUG-83: immutable estimate replay, shared completion and no duplicate work."""
from datetime import date, timedelta
import json
import threading

import pytest

from refactor.learning.course.ledger import Ledger, PlanWindow
from refactor.learning.course.store import CourseStore
from .test_app_point_minutes84 import store, labels, flow, Model, finish  # noqa: F401
from .test_app_point_links82 import PLAN, DAY


def clean(store):
    for i, r in enumerate(store.pending_records(10000), 1):
        store.mark_synced(r.entity, r.entity_id, r.local_revision, i)


def exchange(source, target):
    # Existing wire API, including immutable progress and per-field merges.
    records = source.pending_records(10000)
    target.apply_remote([(r, i + 100) for i, r in enumerate(records)])
    return records


def test_106_content_replan_preserves_elapsed_targets_debt_and_fifo():
    start = date(2026, 10, 7)
    window = PlanWindow(start, date(2026, 10, 20), 600)
    ledger = Ledger(window, 4967)
    ledger.add_confirmed('past', 'p0', 24, start)
    today = date(2026, 10, 11)
    ledger.add_confirmed('today', 'p1', 248, today)
    ledger.add_tentative('tentative', 'p2', 16, today)
    before = ledger.status(today)
    prefix = {d: n for d, n in ledger.targets.items() if d < today}
    ledger.replan(today, window, 3800, preserve_past=True)
    assert {d: n for d, n in ledger.targets.items() if d < today} == prefix
    after = ledger.status(today)
    assert after.debt_minutes == before.debt_minutes == 1132
    assert (after.today_repaid_confirmed, after.today_repaid_tentative) == (248, 16)
    assert ledger.targets[today] == 238
    assert ledger.status(today + timedelta(days=1)).debt_minutes == 1370
    assert ledger.status(today).history_debt_minutes == 0
    assert Ledger.from_dict(ledger.to_dict()).to_dict() == ledger.to_dict()
    # Replaying the same estimate does not freeze or count the past again.
    snapshot = ledger.to_dict()
    ledger.replan(today, window, 3800, preserve_past=True)
    assert ledger.to_dict() == snapshot


def test_106_saved_legacy_event_keeps_history_without_event_migration(store):
    state = store.point_minutes_snapshot(PLAN)
    store.add_credit(PLAN, 'learn:m00', 'm00', 15, DAY)
    today = DAY + timedelta(days=2)
    before = store.load_ledger(PLAN)
    assert store.save_point_minutes(PLAN, state['fingerprint'], labels(state), today=today)
    # Remove new optional metadata to reproduce the already uploaded v2 command.
    with store.transaction():
        row = next(e for e in store.records('ledger_events', PLAN) if e.get('cause') == 'point_minutes')
        row = {k: v for k, v in row.items() if k not in ('point_minutes_key',)}
        store._db.execute('UPDATE ledger_events SET payload=? WHERE id=?', (json.dumps(row), row['id']))
    events = store.records('ledger_events', PLAN)
    credits = store.records('credits', PLAN)
    after = store.load_ledger(PLAN)
    assert after.status(today).debt_minutes == before.status(today).debt_minutes > 0
    assert {d: n for d, n in after.targets.items() if d < today} == {d: n for d, n in before.targets.items() if d < today}
    assert store.records('ledger_events', PLAN) == events
    assert store.records('credits', PLAN) == credits


def test_106_second_device_ready_opens_without_model_writes_or_replan(store, tmp_path):
    state = store.point_minutes_snapshot(PLAN)
    store.save_point_minutes(PLAN, state['fingerprint'], labels(state), today=DAY)
    with CourseStore('account', tmp_path / 'other') as other:
        exchange(store, other)
        before = other.records('ledger_events', PLAN)
        assert not other.pending_records(10000)
        model = Model()
        f = flow(other, model, [])
        f.ensure_point_minutes(PLAN, background_check=True)
        finish(f)
        assert model.calls == []
        assert other.point_minutes_snapshot(PLAN)['ready']
        assert other.records('ledger_events', PLAN) == before
        assert not other.pending_records(10000)


def test_106_independent_devices_identical_estimate_has_one_event(store, tmp_path):
    with CourseStore('account', tmp_path / 'other') as other:
        exchange(store, other)
        clean(store)
        for s in (store, other):
            state = s.point_minutes_snapshot(PLAN)
            s.save_point_minutes(PLAN, state['fingerprint'], labels(state), today=DAY)
        a, b = store.pending_records(10000), other.pending_records(10000)
        store.apply_remote([(r, i + 200) for i, r in enumerate(b)])
        other.apply_remote([(r, i + 200) for i, r in enumerate(a)])
        for s in (store, other):
            assert len([e for e in s.records('ledger_events', PLAN) if e.get('cause') == 'point_minutes']) == 1
        assert store.load_ledger(PLAN).to_dict() == other.load_ledger(PLAN).to_dict()


def test_106_racing_other_day_or_sequence_projects_estimate_once(store, tmp_path):
    with CourseStore('account', tmp_path / 'other') as other:
        exchange(store, other)
        clean(store)
        # A device has one additional immutable record and a different study day.
        other.add_credit(PLAN, 'learn:m00', 'm00', 15, DAY)
        for s, day in ((store, DAY), (other, DAY + timedelta(days=1))):
            state = s.point_minutes_snapshot(PLAN)
            s.save_point_minutes(PLAN, state['fingerprint'], labels(state), today=day)
        a, b = store.pending_records(10000), other.pending_records(10000)
        store.apply_remote([(r, i + 200) for i, r in enumerate(b)])
        other.apply_remote([(r, i + 200) for i, r in enumerate(a)])
        assert store.load_ledger(PLAN).to_dict() == other.load_ledger(PLAN).to_dict()
        assert store.load_ledger(PLAN).total_minutes == store.get_plan(PLAN)['total_minutes']
        # Compare with replaying only the winner referenced by the merged plan.
        events = store.records('ledger_events', PLAN)
        assert len([e for e in events if e.get('cause') == 'point_minutes']) == 2
        assert store.get_plan(PLAN).get('point_minutes_event_id')
        winner = store.get_plan(PLAN)['point_minutes_event_id']
        with store.transaction():
            for e in events:
                if e.get('cause') == 'point_minutes' and e['id'] != winner:
                    store._db.execute('DELETE FROM ledger_events WHERE id=?', (e['id'],))
        assert store.load_ledger(PLAN).to_dict() == other.load_ledger(PLAN).to_dict()


def test_106_equal_estimate_does_not_dirty_points_or_modules(store, monkeypatch):
    state = store.point_minutes_snapshot(PLAN)
    difficulties = labels(state)
    store.save_point_minutes(PLAN, state['fingerprint'], difficulties, today=DAY)
    # A plan-only marker needs repair; its fully identical point values do not.
    with store.transaction():
        p = store.get_plan(PLAN)
        p.pop('point_minutes_signature')
        store._put('plans', PLAN, p)
    clean(store)
    puts = []
    put = store._put
    def spy(entity, *args, **kwargs):
        puts.append(entity)
        return put(entity, *args, **kwargs)
    monkeypatch.setattr(store, '_put', spy)
    events = store.records('ledger_events', PLAN)
    store.save_point_minutes(PLAN, state['fingerprint'], difficulties, today=DAY)
    assert 'points' not in puts and 'modules' not in puts
    assert {r.entity for r in store.pending_records(10000)} == {'plans'}
    assert store.records('ledger_events', PLAN) == events


def test_106_remote_completion_while_model_inflight_stops_remaining_modules(store, tmp_path):
    with CourseStore('account', tmp_path / 'other') as other:
        exchange(store, other)
        clean(store)
        state = other.point_minutes_snapshot(PLAN)
        gate, entered = threading.Event(), threading.Event()
        model = Model(gate=gate)
        complete = model.complete
        def call(*a, **kw):
            entered.set()
            return complete(*a, **kw)
        model.complete = call
        f = flow(store, model, [])
        f.ensure_point_minutes(PLAN)
        assert entered.wait(3)
        other.save_point_minutes(PLAN, state['fingerprint'], labels(state), today=DAY)
        exchange(other, store)
        clean(store)
        before = store.records('ledger_events', PLAN)
        gate.set(); finish(f)
        assert model.calls == ['point_difficulty']
        assert store.records('ledger_events', PLAN) == before
        assert not store.pending_records(10000)


@pytest.mark.parametrize('total', [0, 20, 100, 400])
def test_106_estimate_smaller_than_elapsed_obligations_and_snapshot(total):
    window = PlanWindow(DAY, DAY+timedelta(days=3), 180)
    ledger = Ledger(window, 400)
    ledger.replan(DAY+timedelta(days=2), window, total, preserve_past=True)
    assert ledger.targets[DAY] == ledger.targets[DAY+timedelta(days=1)] == 100
    assert ledger.status(DAY+timedelta(days=2)).debt_minutes == 200
    assert sum(v for d,v in ledger.targets.items() if d>=DAY+timedelta(days=2)) == max(0,total-200)
    assert Ledger.from_dict(ledger.to_dict()).to_dict() == ledger.to_dict()


def test_106_estimate_retains_relearning_release_and_explicit_replan_contract():
    window = PlanWindow(DAY, DAY+timedelta(days=5), 180)
    ledger = Ledger(window, 360)
    ledger.add_tentative('old', 'p', 60, DAY)
    ledger.replan(DAY+timedelta(days=1), window, 360)
    ledger.revoke('p')  # A real relearning debt in the current segment.
    ledger.release_unused(20)
    history = ledger.to_dict()['history']
    before = ledger.status(DAY+timedelta(days=2))
    ledger.replan(DAY+timedelta(days=2), window, 320, preserve_past=True)
    after = ledger.status(DAY+timedelta(days=2))
    assert after.debt_minutes == before.debt_minutes
    assert ledger.to_dict()['history'] == history
    assert ledger.to_dict()['settlement']['released'] == 20
    assert Ledger.from_dict(ledger.to_dict()).to_dict() == ledger.to_dict()
    ledger.replan(DAY+timedelta(days=3), window, 320)
    assert 'estimate_start' not in ledger.to_dict()
    assert Ledger.from_dict(ledger.to_dict()).to_dict() == ledger.to_dict()
