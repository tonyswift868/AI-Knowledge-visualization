"""BUG-16: allocation boundaries must not move or fabricate frozen history."""
from datetime import date, timedelta

import pytest

from refactor.learning.course.ledger import Ledger, PlanWindow


@pytest.mark.parametrize('total, target', [(0, 0), (120, 60)])
def test_repeated_replans_before_start_only_allocate_inside_window(total, target):
    start = date(2026, 10, 1)
    window = PlanWindow(start, start + timedelta(days=1), 120)
    ledger = Ledger(window, 0)
    for today in (date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)):
        ledger.replan(today, window, total)
        assert ledger.targets == {start: target, window.end: target}
        status = ledger.status(today)
        assert (status.today_target, status.debt_minutes, status.history_debt_minutes) == (0, 0, 0)
        snapshot = ledger.to_dict()
        assert snapshot['history'][-1]['closed_on'] == today.isoformat()
        assert all(not h['targets'] for h in snapshot['history'])
        assert Ledger.from_dict(snapshot).to_dict() == snapshot


def test_moving_start_forward_freezes_only_elapsed_days_and_keeps_old_progress():
    start = date(2026, 9, 28)
    ledger = Ledger(PlanWindow(start, start + timedelta(days=5), 120), 360)
    ledger.add_confirmed('first', 'point', 90, start)
    today = start + timedelta(days=2)
    future = PlanWindow(start + timedelta(days=4), start + timedelta(days=6), 120)
    ledger.replan(today, future, 360)
    assert ledger.targets == {future.start + timedelta(days=i): 90 for i in range(3)}
    snapshot = ledger.to_dict()
    history = snapshot['history'][0]
    assert history['targets'] == {'2026-09-28': 60, '2026-09-29': 60}
    assert history['confirmed'] == {'2026-09-28': 60, '2026-09-29': 30}
    assert history['debt_minutes'] == 30
    assert history['closed_on'] == '2026-09-30'
    assert ledger.status(today).today_target == ledger.status(today).debt_minutes == 0
    assert ledger.status(future.start).remaining_confirmed_minutes == 270
    assert Ledger.from_dict(snapshot).to_dict() == snapshot


def test_initial_allocation_and_mid_plan_replan_have_distinct_lower_bounds():
    start = date(2026, 10, 1)
    window = PlanWindow(start, start + timedelta(days=2), 120)
    ledger = Ledger(window, 180)
    assert ledger.targets == {start + timedelta(days=i): 60 for i in range(3)}
    ledger.add_confirmed('first', 'point', 60, start)
    today = start + timedelta(days=1)
    ledger.replan(today, window, 180)
    assert ledger.targets == {today: 60, window.end: 60}
    assert ledger.to_dict()['history'][0]['confirmed'] == {'2026-10-01': 60}
    assert ledger.status(today).history_debt_minutes == 0
