"""Cross-path invariants for recovery attribution and direct examination."""
import json
from datetime import date, timedelta

import pytest

from refactor.learning.course.ledger import Ledger, LedgerError, PlanWindow
from refactor.learning.course.report import project_time_series_learning_days, reproject_cached_time_series

from tests.refactor.concept114.test_concept114_classroom import PLAN, concept_environment


def test_direct_credit_only_recovers_revoked_source_not_live_source(tmp_path):
    env = concept_environment(tmp_path)
    try:
        store, room = env.store, env.room
        old_day = room.study_day() - timedelta(days=2)
        live_day = room.study_day() - timedelta(days=1)
        today = room.study_day()

        # The failed module test removed four minutes learned two days ago.
        store.add_credit(PLAN, 'subpoint:revoked-old', 'p0', 4, old_day, 'tentative')
        store.revoke_credits(PLAN, 'p0', event_id='failed-test-before-direct', today=today)

        # A later three-minute lesson is still live and must not be treated as
        # a recovery source when direct examination fills the point's balance.
        store.add_credit(PLAN, 'subpoint:still-live', 'p0', 3, live_day, 'tentative')
        room._direct_credits(room._course(PLAN), {'module_id': 'm0', 'day': today.isoformat()}, 'direct-mixed')

        restored = [row for row in store.records('credits', PLAN)
                    if row['credit']['unit_id'] == 'p0' and row['id'].startswith('relearn:direct:')]
        by_day = {}
        for row in restored:
            day = row['credit']['day']
            by_day[day] = by_day.get(day, 0) + row['credit']['minutes']

        # Point length is 15. Four minutes have a revoked source day; the
        # still-live three minutes remain on their own day, leaving eight
        # genuinely new direct-test minutes for today.
        assert by_day == {old_day.isoformat(): 4, today.isoformat(): 8}
        live = [row for row in store.load_ledger(PLAN).to_dict()['credits']
                if row['unit_id'] == 'p0']
        assert sum(row['minutes'] for row in live) == 15
        assert next(row for row in live if row['credit_id'] == 'subpoint:still-live')['day'] == live_day.isoformat()
    finally:
        env.room.cancel_materials()
        env.store.close()


def test_ledger_roundtrip_keeps_recovery_attribution_and_checks_source_day():
    first, today = date(2026, 10, 19), date(2026, 10, 21)
    window = PlanWindow(first, today, 600, (1, 1, 1, 1, 1, 1, 1), {})
    ledger = Ledger(window, 30)
    # Colon is valid in a point identifier. Parsing the final source-day suffix
    # must not rely on a fixed number of colon-delimited segments.
    ledger.add_tentative('subpoint:source', 'point:with:colon', 10, first)
    ledger.revoke('point:with:colon')
    recovered = ledger.add_restored_learning(
        'relearn:direct:attempt:point:with:colon', 'point:with:colon', 10, today)
    assert len(recovered) == 1

    reopened = Ledger.from_dict(json.loads(json.dumps(ledger.to_dict())))
    assert reopened.status(today).today_repaid_confirmed == 0
    assert reopened.attributed_credits() == ledger.attributed_credits()

    ordinary = Ledger(window, 30)
    ordinary.add_confirmed('ordinary-credit', 'u', 10, today)
    reopened_ordinary = Ledger.from_dict(json.loads(json.dumps(ordinary.to_dict())))
    assert reopened_ordinary.status(today) == ordinary.status(today)

    with pytest.raises(LedgerError) as error:
        ordinary.add_confirmed('relearn:concept:attempt:token', 'u', 1, today)
    assert error.value.code == 'LEARNING_SOURCE_REQUIRED'


def test_cached_report_projection_is_idempotent_with_other_recovery_day_credit():
    source_day, recovery_day = date(2026, 10, 15), date(2026, 10, 20)
    window = PlanWindow(source_day, recovery_day, 600, (1, 1, 1, 1, 1, 1, 1), {})
    ledger = Ledger(window, 20)
    ledger.add_tentative('subpoint:old', 'u', 4, source_day)
    ledger.revoke('u')
    ledger._add_journal_credit('relearn:concept:legacy', 'u', 4, recovery_day, 'confirmed')
    ledger.add_confirmed('ordinary-credit-on-recovery-day', 'u', 6, recovery_day)
    ledger.set_learning_day_projection({'relearn:concept:legacy': [(source_day, 4)]})

    raw = dict(days=[dict(day=day.isoformat(), credited_confirmed_minutes=0,
                          credited_tentative_minutes=0, submitted_minutes=0,
                          judgement_count=0) for day in (source_day, recovery_day)],
               activity_dates=[source_day.isoformat(), recovery_day.isoformat()], learning_days=2)
    projected = project_time_series_learning_days(raw, ledger)
    cached = reproject_cached_time_series(projected, ledger)
    credited = {row['day']: row['credited_confirmed_minutes'] for row in cached['days']}
    assert credited == {source_day.isoformat(): 4, recovery_day.isoformat(): 6}
