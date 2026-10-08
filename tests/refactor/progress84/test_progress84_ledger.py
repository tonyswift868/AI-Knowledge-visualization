"""Read-only repayment attribution; allocation, events and frozen history stay intact."""
from dataclasses import asdict
import json
from datetime import date, datetime, time, timedelta, timezone

from refactor.learning.course.ledger import Ledger, PlanWindow
from refactor.learning.course.today import build_today_status
from refactor.learning.app.classroom import TODAY_EVENT
from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, act, PLAN, DAY

START = date(2026, 10, 7)
TODAY = START + timedelta(days=1)


def book():
    return Ledger(PlanWindow(START, START + timedelta(days=4), 60), 300)


def numbers(ledger, day=TODAY):
    state = ledger.status(day)
    return state.debt_minutes, state.today_repaid_confirmed, state.today_repaid_tentative


def test_progress84_three_points_repay_immediately_confirm_and_revoke():
    ledger = book()
    assert numbers(ledger) == (60, 0, 0)
    for n in range(3):
        ledger.add_tentative(str(n), str(n), 8, TODAY)
        assert numbers(ledger) == (60 - 8 * (n + 1), 0, 8 * (n + 1))
    original = ledger.to_dict()
    assert asdict(ledger.status(TODAY))['today_tentative'] == 0
    assert ledger.to_dict() == original  # Projection is not a write/replan.
    ledger.confirm('0')
    assert numbers(ledger) == (36, 8, 16)
    ledger.revoke('1')
    assert numbers(ledger) == (44, 8, 8)


def test_progress84_debt_to_today_to_ahead_without_reassigning_targets():
    ledger = book()
    targets = ledger.targets
    ledger.add_tentative('straddle', 'p', 80, TODAY)
    state = ledger.status(TODAY)
    assert numbers(ledger) == (0, 0, 60)
    assert (state.today_tentative, state.ahead_minutes) == (20, 0)
    ledger.add_confirmed('extra', 'q', 50, TODAY)
    state = ledger.status(TODAY)
    assert (state.today_confirmed, state.today_tentative, state.ahead_minutes, state.ahead_tentative) == (0, 60, 10, 10)
    assert numbers(ledger) == (0, 50, 10)  # Confirmed-first is the existing rule.
    assert ledger.targets == targets


def test_progress84_prior_day_credits_not_counted_as_today_repayment():
    ledger = book()
    ledger.add_confirmed('yesterday', 'p', 20, START)
    ledger.add_tentative('today', 'q', 30, TODAY)
    assert numbers(ledger) == (10, 0, 30)
    tomorrow = TODAY + timedelta(days=1)
    assert numbers(ledger, tomorrow) == (70, 0, 0)
    ledger.add_tentative('tomorrow', 'r', 15, tomorrow)
    assert numbers(ledger, tomorrow) == (55, 0, 15)


def test_progress84_confirmation_reorders_attribution_like_existing_fifo():
    ledger = book()
    ledger.add_tentative('old', 'p', 40, START)
    ledger.add_tentative('new', 'q', 40, TODAY)
    assert numbers(ledger) == (0, 0, 20)
    ledger.confirm('q')
    assert numbers(ledger) == (0, 40, 0)
    assert (ledger.status(TODAY).today_confirmed, ledger.status(TODAY).today_tentative) == (0, 20)


def test_progress84_severity_and_relearning_frozen_snapshot():
    ledger = book()
    assert not ledger.status(TODAY).needs_negotiation
    assert ledger.status(TODAY + timedelta(days=1)).needs_negotiation
    ledger.add_tentative('old', 'p', 8, START)
    ledger.replan(TODAY, ledger.window, ledger.total_minutes)
    history = ledger.to_dict()['history']
    ledger.revoke('p')
    ledger.add_tentative('new', 'q', 8, TODAY)
    assert ledger.status(TODAY).today_repaid_tentative == 8
    assert ledger.to_dict()['history'] == history
    assert Ledger.from_dict(ledger.to_dict()).status(TODAY) == ledger.status(TODAY)


def test_progress84_classroom_pinned_day_refresh_and_completed_release():
    ledger = book()
    ledger.add_tentative('lesson', 'p', 24, TODAY)
    args = dict(now=datetime(2026, 10, 9, 5, tzinfo=timezone.utc), refresh=time(4), tz=timezone.utc,
                plan_state='active', check_complete=False)
    pinned = build_today_status(ledger, **args, classroom_day=TODAY)
    home = build_today_status(ledger, **args)
    assert (pinned['debt_minutes'], pinned['today_repaid_tentative']) == (36, 24)
    assert (home['debt_minutes'], home['today_repaid_tentative']) == (96, 0)
    complete = build_today_status(ledger, **dict(args, check_complete=True))
    assert (complete['debt_minutes'], complete['ahead_minutes'], complete['oldest_debt_day']) == (0, 0, None)


def test_progress84_real_classroom_next_answer_leave_refresh(tmp_path, monkeypatch):
    e = make_environment(tmp_path)
    monkeypatch.setattr(type(e.room), '_materials', staticmethod(lambda: e.materials))
    try:
        # Same real store and classroom as the offline conversation contract.
        current_day = DAY + timedelta(days=1)
        e.room.study_day = lambda: current_day
        e.room.now = lambda: datetime.combine(current_day, time(12), timezone.utc)
        act(e, 'next'); teaching = act(e, 'choose', 0)
        before = teaching['today']
        after = act(e, 'next')['today']
        assert after['today_repaid_tentative'] > before['today_repaid_tentative']
        assert after['debt_minutes'] < before['debt_minutes']
        testing = act(e, 'start_test', 'module_test')
        assert not testing['failure']
        confirmed = act(e, 'answer', [0])['today']
        assert confirmed['today_repaid_confirmed'] > 0
        assert confirmed['today_repaid_tentative'] == 0
        act(e, 'leave')
        home = e.store.today_status(PLAN, now=datetime.combine(current_day, time(12), timezone.utc), refresh=time(4), tz=timezone.utc)
        assert home['debt_minutes'] == confirmed['debt_minutes']
        assert home['today_repaid_confirmed'] == confirmed['today_repaid_confirmed']
    finally:
        e.store.close()


def test_progress84_credit_projection_streams_before_next_teacher_call(tmp_path, monkeypatch):
    e = make_environment(tmp_path, first_points=2)
    monkeypatch.setattr(type(e.room), '_materials', staticmethod(lambda: e.materials))
    try:
        day = DAY + timedelta(days=1)
        e.room.study_day = lambda: day
        e.room.now = lambda: datetime.combine(day, time(12), timezone.utc)
        act(e, 'next'); act(e, 'choose', 0)
        e.stream.clear()
        original = e.gateway.complete
        def teacher(*args, **kwargs):
            updates = [json.loads(text[len(TODAY_EVENT):]) for text in e.stream if text.startswith(TODAY_EVENT)]
            assert updates and updates[-1]['today_repaid_tentative'] == 15
            assert updates[-1]['debt_minutes'] == 4
            return original(*args, **kwargs)
        e.gateway.complete = teacher
        result = act(e, 'next')
        assert not result['failure']
        assert result['today']['today_repaid_tentative'] == 15
    finally:e.store.close()
