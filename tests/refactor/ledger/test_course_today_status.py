"""Pure daily API contract; all instants and expected figures are explicit."""
from datetime import date, datetime, time, timedelta, timezone
import json

import pytest

from refactor.learning.course.ledger import Ledger, LedgerError, PlanWindow
from refactor.learning.course.today import build_today_status


EAST = timezone(timedelta(hours=8))
START = date(2024, 2, 28)


def project(ledger, *, now='2024-03-01T12:00:00+08:00', **kwargs):
    return build_today_status(ledger, now=datetime.fromisoformat(now), refresh=time(4), tz=EAST,
                              plan_state='checking', **kwargs)


def test_today_projection_preserves_fifo_numbers_is_json_ready_and_does_not_mutate():
    ledger = Ledger(PlanWindow(START, date(2024, 3, 2), 60), 240)
    ledger.add_confirmed('formal', 'p1', 30, START)
    ledger.add_tentative('draft', 'p2', 60, START)
    before = ledger.to_dict()
    result = project(ledger, check_complete=False)
    assert result == dict(today_target=60, today_confirmed=0, today_tentative=0,
        debt_minutes=30, needs_negotiation=False, ahead_minutes=0, ahead_days=0,
        remaining_minutes=150, remaining_confirmed_minutes=210, overdue_days=0,
        completed=False, history_debt_minutes=0, overflow_minutes=0, time_completed=False,
        study_day='2024-03-01', progress_day='2024-03-01', is_late_night=False,
        needs_rest=False, check_complete=False)
    assert project(ledger, check_complete=False) == json.loads(json.dumps(result))
    assert ledger.to_dict() == before


@pytest.mark.parametrize('plan_state, checked, completed, overdue', [
    ('active', False, False, 1), ('checking', False, False, 1),
    ('checking', True, True, 0), ('completed', True, True, 0),
    ('completed', False, False, 1), ('ready', False, False, 0),
])
def test_overdue_uses_course_completion_not_only_budget(plan_state, checked, completed, overdue):
    ledger = Ledger(PlanWindow(START, date(2024, 2, 29), 60), 120)
    ledger.add_confirmed('all', 'points', 120, START)
    result = build_today_status(ledger, now=datetime(2024, 3, 1, 12, tzinfo=EAST),
        refresh=time(4), tz=EAST, plan_state=plan_state, check_complete=checked)
    assert result['time_completed'] is True
    assert result['completed'] is completed
    assert result['overdue_days'] == overdue
    # The original time-only ledger contract is unchanged.
    assert ledger.status(date(2024, 3, 1)).completed is True
    assert ledger.status(date(2024, 3, 1)).overdue_days == 0


def test_classroom_keeps_entry_day_but_overdue_uses_current_day():
    ledger = Ledger(PlanWindow(START, date(2024, 2, 29), 60), 120)
    ledger.add_confirmed('yesterday', 'p', 60, START)
    result = project(ledger, now='2024-03-01T04:00:00+08:00', check_complete=False,
                     classroom_day=date(2024, 2, 29))
    assert (result['study_day'], result['progress_day']) == ('2024-03-01', '2024-02-29')
    assert (result['today_target'], result['debt_minutes'], result['overdue_days']) == (60, 0, 1)
    assert not result['is_late_night'] and result['needs_rest']
    current = project(ledger, now='2024-03-01T04:00:00+08:00', check_complete=False)
    assert (current['today_target'], current['debt_minutes'], current['needs_rest']) == (0, 60, False)


@pytest.mark.parametrize('instant, refresh, expected_day, late', [
    ('2024-02-29T19:59:59+00:00', time(4), '2024-02-29', True),
    ('2024-02-29T20:00:00+00:00', time(4), '2024-03-01', False),
    ('2024-02-29T16:00:00+00:00', time(0), '2024-03-01', False),
])
def test_rest_condition_follows_local_refresh_including_exact_boundary(instant, refresh, expected_day, late):
    result = build_today_status(Ledger(PlanWindow(START, date(2024, 3, 2), 60), 240),
        now=datetime.fromisoformat(instant), refresh=refresh, tz=EAST,
        plan_state='active', check_complete=False)
    assert result['study_day'] == result['progress_day'] == expected_day
    assert result['is_late_night'] is late and result['needs_rest'] is late


def test_today_api_rejects_ambiguous_clock_and_invalid_classroom_day():
    ledger = Ledger(PlanWindow(START, date(2024, 3, 2), 60), 240)
    with pytest.raises(LedgerError, match='时区'):
        project(ledger, now='2024-03-01T12:00:00', check_complete=False)
    with pytest.raises(LedgerError, match='开课学习日'):
        project(ledger, check_complete=False, classroom_day='2024-03-02')
