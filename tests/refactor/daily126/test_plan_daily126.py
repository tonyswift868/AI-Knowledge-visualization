from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course.ledger import Ledger, PlanWindow, check_cap, full_day_minutes
from refactor.learning.course.store import CourseStore
from refactor.learning.course import plan_additions as additions
from tests.refactor.restructure114.test_restructure114_contract import seed, DAY
from tests.refactor.plan_additions.test_plan_additions import store as addition_store, proposal, parse, generated, TODAY


def flow_for(store, today=DAY):
    flow = PlanFlow(SimpleNamespace(store=store), lambda *a: None, lambda: today)
    # These independent background jobs can invoke models; the real detail and
    # date-change projections, storage and accounting remain in use.
    flow.ensure_point_links = lambda *a, **k: 'none'
    flow.ensure_point_minutes = lambda *a, **k: 'none'
    return flow


@pytest.fixture
def store126(tmp_path):
    with CourseStore('daily126', tmp_path) as store:
        seed(store)
        yield store


def expected(ledger, today):
    return full_day_minutes({d: n for d, n in ledger.targets.items() if d >= today}, ledger.window)


def test_daily126_started_date_change_excludes_past_and_separate_debt(store126):
    flow = flow_for(store126)
    before = store126.load_ledger('p').status(DAY)
    detail = flow.change_window('p', dict(end='2026-10-22'))
    ledger = store126.load_ledger('p')
    assert ledger.status(DAY).debt_minutes == before.debt_minutes > 0
    assert detail['daily']['full_day'] == expected(ledger, DAY)
    assert detail['daily']['full_day'] != check_cap(ledger.window, ledger.total_minutes).full_day_minutes
    assert detail['daily']['full_day'] < full_day_minutes(ledger.targets, ledger.window)


def test_daily126_debt_spread_changes_daily_and_cap_uses_actual_targets(store126):
    flow = flow_for(store126)
    flow.change_window('p', dict(end='2026-10-22', daily_cap=10))
    before = flow.detail('p')['daily']
    store126.replan_plan('p', DAY, cause='debt_spread')
    ledger = store126.load_ledger('p')
    after = flow.detail('p')['daily']
    assert after['full_day'] == expected(ledger, DAY) > before['full_day']
    assert after['full_day'] > 10
    assert check_cap(ledger.window, ledger.total_minutes).ok
    assert not after['ok'] and after['cap'] == 10
    assert after['suggested_end']


def test_daily126_unstarted_still_estimates(store126):
    with store126.transaction():
        plan = store126.get_plan('p')
        plan['state'] = 'ready'
        store126._put('plans', 'p', plan)
    ledger = store126.load_ledger('p')
    assert flow_for(store126).detail('p')['daily']['full_day'] == check_cap(ledger.window, ledger.total_minutes).full_day_minutes


def test_daily126_cap_full_weight_fallback_and_expired():
    start = date(2026, 10, 7)
    ledger = Ledger(PlanWindow(start, start + timedelta(days=3), 100,
                              date_weights={start: 1, start+timedelta(days=1): .5,
                                            start+timedelta(days=2): 0, start+timedelta(days=3): .5}), 600)
    cap = ledger.scheduled_cap(start+timedelta(days=1))
    assert cap.full_day_minutes == 150 and not cap.ok
    assert ledger.scheduled_cap(start+timedelta(days=4)).full_day_minutes == 0


def test_daily126_full_weight_max_ignores_higher_frozen_target():
    start = date(2026, 10, 7)
    today = start+timedelta(days=2)
    ledger = Ledger(PlanWindow(start, start+timedelta(days=3), 100), 400)
    ledger.replan(today, replace(ledger.window, end=start+timedelta(days=8),
                                date_weights={today: .5}), 400, preserve_past=True)
    future = {d:n for d,n in ledger.targets.items() if d >= today}
    assert ledger.scheduled_cap(today).full_day_minutes == full_day_minutes(future, ledger.window) < 100
    assert ledger.scheduled_cap(today).full_day_minutes > future[today]


def test_daily126_supplement_preview_excludes_frozen_large_targets(addition_store):
    ledger = addition_store.load_ledger('p')
    addition_store.replan_plan('p', TODAY, new_window=replace(ledger.window, end=TODAY+timedelta(days=20)))
    state, proposed = proposal(addition_store, modules=False, supplements=True)
    proposed = parse(state, proposed)
    preview = additions.preview(state, proposed, today=TODAY)
    assert preview['timing_high']['daily_minutes'] == flow_for(addition_store, TODAY).detail('p')['daily']['full_day']
    assert preview['timing_high']['daily_minutes'] < full_day_minutes(addition_store.load_ledger('p').targets, ledger.window)


def test_daily126_addition_deadline_preview_matches_saved_detail(addition_store):
    state, proposed = proposal(addition_store)
    proposed = parse(state, proposed)
    points = generated()
    view = additions.preview(state, proposed, today=TODAY, new_end=TODAY+timedelta(days=9), generated=points)
    additions.apply_additions(addition_store, state, proposed, points, view, today=TODAY, confirmed=True)
    actual = flow_for(addition_store, TODAY).detail('p')['daily']
    assert view['timing_high']['daily_minutes'] == actual['full_day']
    assert view['timing_high']['exceeds_cap'] == (not actual['ok'])
