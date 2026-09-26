"""Standard-library tests, also collected by the task's pytest command."""
import copy
from dataclasses import FrozenInstanceError, asdict
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
import json
import random
import unittest
from zoneinfo import ZoneInfo

from refactor.learning.course.ledger import (
    CapReport, Credit, Ledger, LedgerError, PlanWindow, check_cap,
    distribute, estimate_check_minutes, full_day_minutes, study_day, weight_of,
)


MONDAY = date(2026, 9, 14)


def day(offset):
    return MONDAY + timedelta(days=offset)


def window(days=3, *, cap=100, weights=(1.0,) * 7, overrides=None):
    return PlanWindow(MONDAY, day(days - 1), cap, weights, overrides or {})


class LedgerTestCase(unittest.TestCase):
    def error_code(self, code, action):
        with self.assertRaises(LedgerError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.message, str(caught.exception))
        self.assertTrue(any('\u4e00' <= c <= '\u9fff' for c in str(caught.exception)))


class DistributionTests(LedgerTestCase):
    def test_original_two_week_example(self):
        plan = window(14, weights=(1, 1, 1, 1, 1, 0, 1), overrides={day(7): 0.5})
        targets = distribute(plan, 1150)
        self.assertEqual(len(targets), 14)
        self.assertEqual(targets[day(5)], 0)
        self.assertEqual(targets[day(12)], 0)
        self.assertEqual(targets[day(7)], 50)
        self.assertEqual([targets[day(i)] for i in range(14) if i not in (5, 7, 12)], [100] * 11)
        self.assertEqual(sum(targets.values()), 1150)
        self.assertEqual(full_day_minutes(targets, plan), 100)

    def test_remainders_then_earliest_date(self):
        plan = window(4, weights=(1, 0.5, 1, 0.3, 0, 0, 0))
        self.assertEqual(distribute(plan, 7), {day(0): 3, day(1): 1, day(2): 2, day(3): 1})
        self.assertEqual(list(distribute(window(3), 5).values()), [2, 2, 1])
        self.assertEqual(list(distribute(window(3, weights=(0.1, 0.2, 0.3, 0, 0, 0, 0)), 6).values()), [1, 2, 3])

    def test_random_conservation_and_largest_remainders(self):
        rng = random.Random(303)
        choices = (0, 0.1, 0.25, 0.5, 0.75, 1)
        for case in range(250):
            with self.subTest(case=case):
                count = rng.randint(1, 55)
                overrides = {day(i): rng.choice(choices) for i in range(count) if rng.random() < 0.3}
                overrides[MONDAY] = 1
                plan = window(count, weights=tuple(rng.choice(choices) for _ in range(7)), overrides=overrides)
                total = rng.randint(0, 20000) if case % 11 else 2 ** 60 + rng.randint(0, 100)
                targets = distribute(plan, total)
                self.assertEqual(sum(targets.values()), total)
                weights = {d: Fraction(str(weight_of(plan, d))) for d in targets}
                quotas = {d: total * w / sum(weights.values()) for d, w in weights.items()}
                floors = {d: q.numerator // q.denominator for d, q in quotas.items()}
                awarded = [d for d, n in targets.items() if n == floors[d] + 1]
                self.assertEqual(len(awarded), total - sum(floors.values()))
                for d, value in targets.items():
                    self.assertEqual(type(value), int)
                    self.assertIn(value, (floors[d], floors[d] + 1))
                    if weights[d] == 0:
                        self.assertEqual(value, 0)
                if awarded:
                    worst = max(awarded, key=lambda d: (-(quotas[d] - floors[d]), d))
                    for d in targets.keys() - set(awarded):
                        if weights[d]:
                            self.assertLessEqual((-(quotas[worst] - floors[worst]), worst),
                                                 (-(quotas[d] - floors[d]), d))

    def test_from_date_is_the_explicit_lower_bound(self):
        self.assertEqual(distribute(window(3), 11, from_date=day(1)), {day(1): 6, day(2): 5})
        plan = PlanWindow(day(1), day(2), 10)
        self.assertEqual(distribute(plan, 6, from_date=MONDAY), {day(0): 2, day(1): 2, day(2): 2})
        self.error_code('NO_STUDY_DAYS', lambda: distribute(plan, 1, from_date=day(3)))

    def test_no_study_days_even_for_zero_total(self):
        for total in (0, 1, 500):
            self.error_code('NO_STUDY_DAYS', lambda: distribute(window(weights=(0,) * 7), total))
        self.assertEqual(distribute(window(3), 0), {day(0): 0, day(1): 0, day(2): 0})

    def test_date_override_and_no_full_day_fallback(self):
        plan = window(3, weights=(0.5,) * 7, overrides={day(1): 0.25})
        targets = distribute(plan, 100)
        self.assertEqual(list(targets.values()), [40, 20, 40])
        self.assertEqual(full_day_minutes(targets, plan), 40)
        self.assertEqual(full_day_minutes({}, plan), 0)

    def test_window_is_defensively_frozen(self):
        overrides = {day(1): 0.5}
        weekdays = [1] * 7
        plan = PlanWindow(MONDAY, day(2), 10, weekdays, overrides)
        overrides[day(1)] = 0
        weekdays[0] = 0
        self.assertEqual(weight_of(plan, day(1)), 0.5)
        self.assertEqual(weight_of(plan, day(0)), 1)
        with self.assertRaises(TypeError):
            plan.date_weights[day(0)] = 0
        with self.assertRaises(FrozenInstanceError):
            plan.end = day(4)

    def test_invalid_inputs(self):
        for bad in (-1, 1.2, True, '5'):
            self.error_code('INVALID_MINUTES', lambda: distribute(window(), bad))
        self.error_code('INVALID_WINDOW', lambda: PlanWindow(day(2), MONDAY, 1))
        self.error_code('INVALID_MINUTES', lambda: window(cap=0))
        for value in (-0.1, 1.1, float('nan'), float('inf'), True):
            self.error_code('INVALID_WEIGHT', lambda: window(overrides={MONDAY: value}))
        self.error_code('INVALID_WEIGHT', lambda: window(weights=(1,) * 6))


class CapTests(LedgerTestCase):
    def test_within_cap_and_first_extension(self):
        self.assertEqual(check_cap(window(), 300), CapReport(True, 100, 100, None))
        self.assertEqual(check_cap(window(cap=60), 200), CapReport(False, 67, 60, day(3)))

    def test_extension_skips_unavailable_weekdays(self):
        plan = PlanWindow(day(4), day(4), 1, (0, 0, 0, 0, 1, 0, 0))
        self.assertEqual(check_cap(plan, 2).suggested_end, day(11))

    def test_outside_date_overrides_are_not_extended(self):
        plan = window(1, cap=1, overrides={day(1): 0, day(2): 0})
        self.assertEqual(check_cap(plan, 3).suggested_end, day(2))

    def test_no_full_weight_days(self):
        plan = window(1, cap=1, weights=(0.5,) * 7)
        self.assertEqual(check_cap(plan, 2).suggested_end, day(1))

    def test_five_year_limit_inclusive_and_leap_day(self):
        start = date(2024, 2, 29)
        limit = date(2029, 2, 28)
        plan = PlanWindow(start, start, 1)
        count = (limit - start).days + 1
        self.assertEqual(check_cap(plan, count).suggested_end, limit)
        self.assertIsNone(check_cap(plan, count + 1).suggested_end)

    def test_no_weekly_availability_cannot_extend(self):
        plan = window(1, cap=10, weights=(0,) * 7, overrides={MONDAY: 1})
        self.assertEqual(check_cap(plan, 11), CapReport(False, 11, 10, None))
        self.error_code('NO_STUDY_DAYS', lambda: check_cap(window(weights=(0,) * 7), 10))

    def test_suggested_end_matches_exhaustive_small_calendars(self):
        rng = random.Random(331)
        for case in range(30):
            with self.subTest(case=case):
                weights = tuple(rng.choice((0, 0.25, 0.5, 1)) for _ in range(6)) + (1,)
                plan = window(rng.randint(1, 8), cap=rng.randint(3, 15), weights=weights, overrides={MONDAY: 1})
                total = rng.randint(10, 80)
                original = full_day_minutes(distribute(plan, total), plan)
                report = check_cap(plan, total)
                if original <= plan.daily_cap_minutes:
                    self.assertTrue(report.ok)
                    continue
                expected = None
                overrides = {d: w for d, w in plan.date_weights.items() if plan.start <= d <= plan.end}
                for delta in range(1, 201):
                    candidate = PlanWindow(plan.start, plan.end + timedelta(days=delta), plan.daily_cap_minutes, weights, overrides)
                    if full_day_minutes(distribute(candidate, total), candidate) <= plan.daily_cap_minutes:
                        expected = candidate.end
                        break
                self.assertIsNotNone(expected)
                self.assertEqual(report.suggested_end, expected)


class StudyDayTests(LedgerTestCase):
    def test_shanghai_refresh_boundary(self):
        tz = ZoneInfo('Asia/Shanghai')
        self.assertEqual(study_day(datetime(2026, 9, 15, 3, 59, tzinfo=tz), time(4), tz), day(0))
        self.assertEqual(study_day(datetime(2026, 9, 15, 4, tzinfo=tz), time(4), tz), day(1))
        self.assertEqual(study_day(datetime(2026, 9, 14, 19, 59, tzinfo=timezone.utc), time(4), tz), day(0))
        self.assertEqual(study_day(datetime(2026, 9, 14, 20, tzinfo=timezone.utc), time(4), tz), day(1))

    def test_naive_timestamp_rejected(self):
        self.error_code('NAIVE_DATETIME', lambda: study_day(datetime(2026, 9, 15, 4), time(4), timezone.utc))

    def test_entire_session_uses_opening_day(self):
        tz = ZoneInfo('Asia/Shanghai')
        opening_day = study_day(datetime(2026, 9, 15, 3, 59, tzinfo=tz), time(4), tz)
        ledger = Ledger(window(), 300)
        ledger.add_confirmed('before', 'u1', 40, opening_day)
        # Caller still passes the same opening day after crossing 04:00.
        ledger.add_confirmed('after', 'u2', 60, opening_day)
        self.assertEqual([c['day'] for c in ledger.to_dict()['credits']], [MONDAY.isoformat()] * 2)
        self.assertEqual(ledger.status(MONDAY).today_confirmed, 100)

    def test_dst_uses_local_clock_not_a_fixed_utc_offset(self):
        tz = ZoneInfo('America/New_York')
        for utc_hour in (5, 6):  # Both instances of 01:30 during the fall-back.
            ts = datetime(2026, 11, 1, utc_hour, 30, tzinfo=timezone.utc)
            self.assertEqual(study_day(ts, time(4), tz), date(2026, 10, 31))


class AccountingTests(LedgerTestCase):
    def test_debt_is_paid_before_today(self):
        ledger = Ledger(window(), 300)
        self.assertEqual(ledger.status(day(1)).debt_minutes, 100)
        ledger.add_confirmed('one', 'u', 60, day(1))
        status = ledger.status(day(1))
        self.assertEqual((status.debt_minutes, status.today_confirmed), (40, 0))
        ledger.add_confirmed('two', 'u', 40, day(1))
        self.assertEqual(ledger.status(day(1)).today_confirmed, 0)
        ledger.add_tentative('three', 'v', 20, day(1))
        status = ledger.status(day(1))
        self.assertEqual((status.debt_minutes, status.today_confirmed, status.today_tentative), (0, 0, 20))

    def test_ahead_and_future_targets_do_not_change(self):
        ledger = Ledger(window(5), 500)
        before = ledger.targets
        ledger.add_confirmed('skip', 'module', 350, MONDAY)
        status = ledger.status(MONDAY)
        self.assertEqual((status.today_confirmed, status.ahead_minutes, status.ahead_days), (100, 250, 2))
        self.assertEqual((ledger.status(day(1)).ahead_minutes, ledger.status(day(1)).ahead_days), (150, 1))
        self.assertEqual(ledger.targets, before)
        before[MONDAY] = 999
        self.assertEqual(ledger.targets[MONDAY], 100)

    def test_ahead_days_skip_unavailable_days(self):
        plan = PlanWindow(day(4), day(7), 100, (1, 1, 1, 1, 1, 0, 1))
        ledger = Ledger(plan, 300)
        ledger.add_confirmed('all', 'u', 300, day(4))
        self.assertEqual(ledger.status(day(4)).ahead_days, 2)
        self.assertEqual(ledger.status(day(5)).today_target, 0)
        self.assertEqual(ledger.status(day(5)).ahead_days, 2)

    def test_confirmed_fills_before_earlier_tentative(self):
        ledger = Ledger(window(2), 200)
        ledger.add_tentative('tent', 'u', 80, MONDAY)
        ledger.add_confirmed('formal', 'v', 60, MONDAY)
        status = ledger.status(MONDAY)
        self.assertEqual((status.today_confirmed, status.today_tentative, status.ahead_minutes), (60, 40, 40))
        self.assertEqual(ledger.confirm('u'), 80)
        self.assertEqual(ledger.confirm('u'), 0)
        self.assertEqual(ledger.status(MONDAY).today_confirmed, 100)
        credits = ledger.to_dict()['credits']
        self.assertEqual([(c['seq'], c['day']) for c in credits], [(1, MONDAY.isoformat()), (2, MONDAY.isoformat())])

    def test_revoke_only_tentative_creates_debt(self):
        ledger = Ledger(window(), 300)
        ledger.add_tentative('tent1', 'u', 30, MONDAY)
        ledger.add_tentative('tent2', 'u', 50, MONDAY)
        ledger.add_confirmed('formal', 'u', 20, MONDAY)
        self.assertEqual(ledger.status(day(1)).debt_minutes, 0)
        self.assertEqual(ledger.revoke('u'), 80)
        self.assertEqual(ledger.revoke('u'), 0)
        self.assertEqual(ledger.status(day(1)).debt_minutes, 80)
        self.assertEqual(ledger.status(MONDAY).today_confirmed, 20)

    def test_negotiation_at_exactly_two_days_debt(self):
        ledger = Ledger(window(), 300)
        self.assertFalse(ledger.status(day(1)).needs_negotiation)
        self.assertTrue(ledger.status(day(2)).needs_negotiation)
        ledger.add_confirmed('one', 'u', 1, day(2))
        self.assertEqual(ledger.status(day(2)).debt_minutes, 199)
        self.assertFalse(ledger.status(day(2)).needs_negotiation)

    def test_zero_debt_never_needs_negotiation(self):
        ledger = Ledger(window(), 0)
        self.assertFalse(ledger.status(day(2)).needs_negotiation)
        ledger = Ledger(window(), 300)
        ledger.add_confirmed('done', 'u', 300, MONDAY)
        ledger.replan(day(1), window(3, weights=(0,) * 7), 300)
        self.assertFalse(ledger.status(day(1)).needs_negotiation)

    def test_zero_targets_neither_count_nor_break_ahead_days(self):
        ledger = Ledger(window(3, weights=(1, 0.01, 1, 1, 1, 1, 1)), 2)
        self.assertEqual(list(ledger.targets.values()), [1, 0, 1])
        ledger.add_confirmed('all', 'u', 2, MONDAY)
        self.assertEqual(ledger.status(day(-1)).ahead_days, 2)
        self.assertEqual(ledger.status(MONDAY).ahead_days, 1)
        self.assertEqual(Ledger(window(), 0).status(day(-1)).ahead_days, 0)

    def test_completion_requires_confirmed_and_overflow_is_allowed(self):
        ledger = Ledger(window(2), 200)
        ledger.add_tentative('all', 'u', 250, MONDAY)
        status = ledger.status(day(3))
        self.assertEqual((status.remaining_minutes, status.remaining_confirmed_minutes, status.overflow_minutes), (-50, 200, 50))
        self.assertFalse(status.completed)
        self.assertEqual(status.overdue_days, 2)
        self.assertEqual(ledger.confirm('u'), 250)
        status = ledger.status(day(3))
        self.assertTrue(status.completed)
        self.assertEqual((status.remaining_confirmed_minutes, status.overdue_days, status.overflow_minutes), (-50, 0, 50))

    def test_overdue_begins_after_end_not_on_end(self):
        ledger = Ledger(window(2), 200)
        self.assertEqual(ledger.status(day(1)).overdue_days, 0)
        self.assertEqual(ledger.status(day(2)).overdue_days, 1)
        self.assertEqual(ledger.status(day(10)).overdue_days, 9)

    def test_idempotency_conflicts_and_retries_after_state_changes(self):
        ledger = Ledger(window(), 300)
        ledger.add_tentative('one', 'u', 50, MONDAY)
        ledger.add_tentative('one', 'u', 50, MONDAY)
        self.assertEqual(ledger.status(MONDAY).today_tentative, 50)
        for unit, minutes, when in [('other', 50, MONDAY), ('u', 51, MONDAY), ('u', 50, day(1))]:
            self.error_code('CREDIT_CONFLICT', lambda: ledger.add_tentative('one', unit, minutes, when))
        self.error_code('CREDIT_CONFLICT', lambda: ledger.add_confirmed('one', 'u', 50, MONDAY))
        ledger.confirm('u')
        ledger.add_tentative('one', 'u', 50, MONDAY)
        self.assertEqual(ledger.status(MONDAY).today_confirmed, 50)
        ledger.add_tentative('two', 'v', 30, MONDAY)
        ledger.revoke('v')
        ledger.add_tentative('two', 'v', 30, MONDAY)
        self.assertEqual(ledger.status(MONDAY).today_tentative, 0)
        ledger.add_confirmed('three', 'v', 10, MONDAY)
        self.assertEqual([c['seq'] for c in ledger.to_dict()['credits']], [1, 3])

    def test_invalid_credits_do_not_change_state(self):
        ledger = Ledger(window(), 300)
        before = ledger.to_dict()
        for amount in (0, -1, 0.5, True):
            self.error_code('INVALID_MINUTES', lambda: ledger.add_confirmed('bad', 'u', amount, MONDAY))
        self.error_code('INVALID_CREDIT', lambda: ledger.add_tentative('', 'u', 1, MONDAY))
        self.error_code('INVALID_DATE', lambda: ledger.add_tentative('x', 'u', 1, datetime(2026, 9, 14)))
        self.assertEqual(ledger.to_dict(), before)
        credit = Credit('one', 'u', 1, MONDAY, 'tentative', 1)
        with self.assertRaises(FrozenInstanceError):
            credit.minutes = 2


class ReplanTests(LedgerTestCase):
    def test_freeze_and_backdated_new_credit_belongs_to_new_segment(self):
        ledger = Ledger(window(), 300)
        ledger.add_confirmed('old', 'u', 40, MONDAY)
        ledger.replan(day(1), window(4), 340)
        status = ledger.status(day(1))
        self.assertEqual((status.today_target, status.debt_minutes, status.history_debt_minutes, status.remaining_minutes), (100, 0, 60, 300))
        history = ledger.to_dict()['history']
        self.assertEqual(history[0]['confirmed'], {MONDAY.isoformat(): 40})
        ledger.add_confirmed('new', 'v', 100, MONDAY)
        self.assertEqual(ledger.status(day(1)).today_confirmed, 100)
        self.assertEqual(ledger.to_dict()['history'], history)

    def test_today_and_future_records_move_to_new_segment(self):
        ledger = Ledger(window(), 300)
        ledger.add_confirmed('old', 'u', 100, MONDAY)
        ledger.add_confirmed('today', 'v', 40, day(1))
        ledger.add_tentative('future', 'w', 30, day(2))
        ledger.replan(day(1), window(4), 400)
        self.assertEqual(ledger.targets, {day(1): 100, day(2): 100, day(3): 100})
        status = ledger.status(day(1))
        self.assertEqual((status.today_confirmed, status.today_tentative, status.remaining_minutes), (40, 30, 230))
        self.assertEqual([c['segment'] for c in ledger.to_dict()['credits']], [0, 1, 1])

    def test_replan_uses_record_day_not_its_allocated_target_day(self):
        ledger = Ledger(window(), 300)
        ledger.add_confirmed('made-up', 'u', 50, day(1))
        ledger.replan(day(1), window(4), 300)
        # The pre-replan snapshot retains Monday's fill; this Tuesday credit
        # nevertheless belongs to the new segment under the explicit day rule.
        history = ledger.to_dict()['history'][0]
        self.assertEqual(history['confirmed'][MONDAY.isoformat()], 50)
        self.assertEqual(history['debt_minutes'], 50)
        self.assertEqual(ledger.status(day(1)).today_confirmed, 50)
        self.assertEqual(sum(ledger.targets.values()), 300)
        self.assertEqual(ledger.status(day(1)).remaining_minutes, 250)

    def test_multiple_replans_accumulate_frozen_debt(self):
        ledger = Ledger(window(), 300)
        ledger.add_confirmed('old', 'u', 40, MONDAY)
        ledger.replan(day(1), window(4), 340)
        ledger.replan(day(2), window(5), 340)
        self.assertEqual(ledger.status(day(2)).history_debt_minutes, 160)
        ledger.add_confirmed('backdated', 'u', 60, MONDAY)
        ledger.replan(day(3), window(6), 340)
        self.assertEqual(ledger.targets, {day(3): 80, day(4): 80, day(5): 80})
        self.assertEqual(ledger.status(day(3)).history_debt_minutes, 200)
        self.assertEqual(ledger.status(day(3)).debt_minutes, 0)
        self.assertEqual(len(ledger.to_dict()['history']), 3)

    def test_historical_confirmation_does_not_rewrite_snapshot(self):
        ledger = Ledger(window(), 300)
        ledger.add_tentative('old', 'u', 100, MONDAY)
        ledger.replan(day(1), window(3), 300)
        history = ledger.to_dict()['history']
        self.assertEqual(ledger.confirm('u'), 100)
        self.assertEqual(ledger.to_dict()['history'], history)
        self.assertEqual(ledger.status(day(1)).remaining_confirmed_minutes, 200)
        self.assertEqual(ledger.revoke('u'), 0)

    def test_historical_revoke_keeps_snapshot_and_adds_current_debt(self):
        ledger = Ledger(window(), 300)
        ledger.add_tentative('old', 'u', 100, MONDAY)
        ledger.replan(day(1), window(3), 300)
        history = ledger.to_dict()['history']
        before = ledger.status(day(1))
        self.assertEqual(ledger.revoke('u'), 100)
        after = ledger.status(day(1))
        self.assertEqual(ledger.to_dict()['history'], history)
        self.assertEqual(after.remaining_minutes, before.remaining_minutes + 100)
        self.assertEqual(after.debt_minutes, before.debt_minutes + 100)
        self.assertEqual(after.history_debt_minutes, before.history_debt_minutes)
        self.assertEqual(ledger.revoke('u'), 0)
        self.assertEqual(ledger.status(day(1)).debt_minutes, 100)
        ledger.add_confirmed('relearn-1', 'u', 40, day(1))
        self.assertEqual((ledger.status(day(1)).debt_minutes, ledger.status(day(1)).today_confirmed), (60, 0))
        ledger.add_confirmed('relearn-2', 'u', 60, day(1))
        self.assertEqual((ledger.status(day(1)).debt_minutes, ledger.status(day(1)).today_confirmed), (0, 0))
        ledger.add_confirmed('next', 'v', 100, day(1))
        self.assertEqual(ledger.status(day(1)).today_confirmed, 100)

    def test_historical_revoke_is_absorbed_by_existing_ahead_progress(self):
        ledger = Ledger(window(5), 500)
        ledger.add_tentative('old', 'u', 100, MONDAY)
        ledger.replan(day(1), window(5), 500)
        ledger.add_confirmed('skip', 'v', 350, day(1))
        history = ledger.to_dict()['history']
        before = ledger.status(day(1))
        self.assertEqual((before.today_confirmed, before.ahead_minutes, before.debt_minutes), (100, 250, 0))
        ledger.revoke('u')
        after = ledger.status(day(1))
        self.assertEqual(after.ahead_minutes, before.ahead_minutes - 100)
        self.assertEqual(after.debt_minutes, before.debt_minutes)
        self.assertEqual(after.today_confirmed, before.today_confirmed)
        self.assertEqual(after.remaining_minutes, before.remaining_minutes + 100)
        self.assertEqual(after.history_debt_minutes, before.history_debt_minutes)
        self.assertEqual(ledger.to_dict()['history'], history)

    def test_historical_revoke_reduces_today_before_creating_debt(self):
        for current, expected_today, expected_debt in [(120, 20, 0), (80, 0, 20)]:
            with self.subTest(current=current):
                ledger = Ledger(window(), 300)
                ledger.add_tentative('old', 'u', 100, MONDAY)
                ledger.replan(day(1), window(3), 300)
                ledger.add_confirmed('current', 'v', current, day(1))
                ledger.revoke('u')
                status = ledger.status(day(1))
                self.assertEqual((status.today_confirmed, status.debt_minutes), (expected_today, expected_debt))
                self.assertEqual(status.ahead_minutes, 0)

    def test_same_day_replan_freezes_the_preceding_synthetic_target(self):
        ledger = Ledger(window(), 300)
        ledger.add_tentative('old', 'u', 100, MONDAY)
        ledger.replan(day(1), window(3), 300)
        ledger.revoke('u')
        ledger.add_confirmed('partial', 'v', 20, day(1))
        ledger.replan(day(1), window(3), 300)
        self.assertEqual(ledger.status(day(1)).history_debt_minutes, 80)
        self.assertEqual(ledger.status(day(1)).debt_minutes, 0)
        restored = Ledger.from_dict(json.loads(json.dumps(ledger.to_dict())))
        self.assertEqual(restored.to_dict(), ledger.to_dict())
        self.assertEqual(restored.status(day(1)), ledger.status(day(1)))

    def test_historical_relearning_is_folded_into_next_replan_once(self):
        ledger = Ledger(window(), 300)
        ledger.add_tentative('old', 'u', 100, MONDAY)
        ledger.replan(day(1), window(3), 300)
        first_snapshot = ledger.to_dict()['history'][0]
        ledger.revoke('u')
        ledger.add_confirmed('relearn', 'u', 40, day(1))
        ledger.replan(day(2), window(4), 300)
        self.assertEqual(ledger.to_dict()['history'][0], first_snapshot)
        self.assertEqual(ledger.status(day(2)).history_debt_minutes, 160)
        self.assertEqual(ledger.status(day(2)).debt_minutes, 0)
        self.assertEqual(ledger.targets, {day(2): 130, day(3): 130})

    def test_zero_new_budget_even_if_no_future_study_days(self):
        ledger = Ledger(window(), 300)
        ledger.add_confirmed('done', 'u', 300, MONDAY)
        ledger.replan(day(1), window(3, weights=(0,) * 7), 300)
        status = ledger.status(day(1))
        self.assertEqual(ledger.targets, {day(1): 0, day(2): 0})
        self.assertTrue(status.completed)
        self.assertEqual((status.today_target, status.ahead_days, status.remaining_minutes), (0, 0, 0))

    def test_revoke_history_can_reopen_a_zero_target_segment(self):
        ledger = Ledger(window(), 300)
        ledger.add_tentative('old', 'u', 300, MONDAY)
        ledger.replan(day(1), window(), 300)
        ledger.revoke('u')
        status = ledger.status(day(1))
        self.assertEqual((status.today_target, status.debt_minutes, status.remaining_minutes), (0, 300, 300))
        ledger.add_confirmed('again', 'u', 300, day(1))
        self.assertEqual(ledger.status(day(1)).debt_minutes, 0)
        self.assertEqual(ledger.status(day(1)).overflow_minutes, 0)
        self.assertTrue(ledger.status(day(1)).completed)

    def test_invalid_replans_are_atomic(self):
        ledger = Ledger(window(), 300)
        ledger.add_tentative('old', 'u', 100, MONDAY)
        before = ledger.to_dict()
        for action, code in [
            (lambda: ledger.replan(day(2), window(2), 300), 'INVALID_REPLAN'),
            (lambda: ledger.replan(day(1), window(3), 99), 'INVALID_REPLAN'),
            (lambda: ledger.replan(day(1), window(3), -1), 'INVALID_MINUTES'),
            (lambda: ledger.replan(day(1), window(3, weights=(0,) * 7), 300), 'NO_STUDY_DAYS'),
        ]:
            self.error_code(code, action)
            self.assertEqual(ledger.to_dict(), before)
        ledger.replan(day(2), window(4), 300)
        before = ledger.to_dict()
        self.error_code('INVALID_REPLAN', lambda: ledger.replan(day(1), window(5), 300))
        self.assertEqual(ledger.to_dict(), before)


class SerializationTests(LedgerTestCase):
    def complex_ledger(self):
        ledger = Ledger(window(4), 400)
        ledger.add_tentative('past', 'u', 80, MONDAY)
        ledger.add_confirmed('passed', 'v', 20, MONDAY)
        ledger.add_tentative('today', 'w', 45, day(1))
        ledger.replan(day(1), window(5), 500)
        ledger.revoke('u')
        ledger.add_confirmed('paid', 'u', 30, day(1))
        return ledger

    def test_json_round_trip_status_and_subsequent_operations(self):
        ledger = self.complex_ledger()
        restored = Ledger.from_dict(json.loads(json.dumps(ledger.to_dict(), allow_nan=False)))
        self.assertEqual(restored.to_dict(), ledger.to_dict())
        for offset in range(-2, 12):
            self.assertEqual(asdict(restored.status(day(offset))), asdict(ledger.status(day(offset))))
        for instance in (ledger, restored):
            instance.add_tentative('past', 'u', 80, MONDAY)  # Revoked replay stays revoked.
            instance.confirm('w')
            instance.replan(day(2), window(6), 550)
            instance.add_confirmed('next', 'v', 700, day(1))
        self.assertEqual(restored.to_dict(), ledger.to_dict())
        for offset in range(7):
            self.assertEqual(restored.status(day(offset)), ledger.status(day(offset)))

    def test_empty_zero_budget_and_repeated_same_day_replans(self):
        for total in (0, 100):
            ledger = Ledger(window(), total)
            ledger.replan(MONDAY, window(4), total)
            ledger.replan(MONDAY, window(2), total)
            restored = Ledger.from_dict(json.loads(json.dumps(ledger.to_dict())))
            self.assertEqual(restored.status(MONDAY), ledger.status(MONDAY))

    def test_export_and_input_do_not_alias_internal_state(self):
        ledger = self.complex_ledger()
        exported = ledger.to_dict()
        restored = Ledger.from_dict(exported)
        exported['credits'].clear()
        exported['history'][0]['confirmed'].clear()
        exported['window']['weekday_weights'][0] = 0
        self.assertEqual(restored.to_dict(), ledger.to_dict())

    def test_corrupt_serialized_values_rejected(self):
        base = self.complex_ledger().to_dict()
        cases = []
        data = copy.deepcopy(base); data['version'] = 2; cases.append(data)
        data = copy.deepcopy(base); data['credits'][0]['segment'] = 99; cases.append(data)
        data = copy.deepcopy(base); data['credits'][0]['minutes'] += 1; cases.append(data)
        data = copy.deepcopy(base); data['submissions'][0]['seq'] = 99; cases.append(data)
        data = copy.deepcopy(base); data['history'][0]['debt_minutes'] += 1; cases.append(data)
        data = copy.deepcopy(base); data['history'][0]['tentative'][MONDAY.isoformat()] = 999; cases.append(data)
        data = copy.deepcopy(base); data['relearning_minutes'] = -1; cases.append(data)
        data = copy.deepcopy(base); data['targets'][day(1).isoformat()] += 2; data['targets'][day(2).isoformat()] -= 2; cases.append(data)
        for index, data in enumerate(cases + [None, {}, []]):
            with self.subTest(index=index):
                self.error_code('INVALID_DATA', lambda: Ledger.from_dict(data))


class EstimateTests(LedgerTestCase):
    def test_examples_and_rounding(self):
        self.assertEqual(estimate_check_minutes(100, minutes_per_question=2), 260)
        self.assertEqual(estimate_check_minutes(0, minutes_per_question=2), 0)
        self.assertEqual(estimate_check_minutes(1, minutes_per_question=2.5, problem_ratio=0), round(2.5))
        self.assertEqual(estimate_check_minutes(100, minutes_per_question=2, problem_ratio=0.2, problem_questions=5), 360)

    def test_invalid_estimates(self):
        for bad in (-1, 2.5, True):
            self.error_code('INVALID_MINUTES', lambda: estimate_check_minutes(bad, minutes_per_question=2))
        for bad in (0, -1, float('nan'), float('inf'), True):
            self.error_code('INVALID_MINUTES', lambda: estimate_check_minutes(10, minutes_per_question=bad))
        self.error_code('INVALID_WEIGHT', lambda: estimate_check_minutes(10, minutes_per_question=2, problem_ratio=1.1))


if __name__ == '__main__':
    unittest.main()
