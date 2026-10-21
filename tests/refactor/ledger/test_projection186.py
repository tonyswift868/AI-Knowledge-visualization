from datetime import date

import pytest

from refactor.learning.course.ledger import Ledger, LedgerError, PlanWindow, learning_day_slices
from refactor.learning.course.store import _legacy_learning_day_projection


def _day(day):
    return date(2026, 10, day)


def _source_rows():
    totals = [(15, 387, 23), (16, 88, 6), (17, 186, 35),
              (18, 8, 1), (19, 306, 50), (20, 271, 41)]
    rows = []
    for day, minutes, count in totals:
        base, extra = divmod(minutes, count)
        rows.extend((_day(day), base + (index < extra)) for index in range(count))
    return rows


def test_revoking_projected_tentative_recovery_keeps_snapshot_valid():
    ledger = Ledger(PlanWindow(_day(14), _day(22), 600), 90)
    ledger.add_tentative('subpoint:source', 'point', 4, _day(15))
    ledger.revoke('point')
    restored = 'relearn:concept:legacy:point'
    ledger._add_journal_credit(restored, 'point', 4, _day(20), 'tentative')
    ledger.set_learning_day_projection({restored: [(_day(15), 4)]})
    assert ledger.revoke('point') == 4
    assert 'learning_day_projection' not in ledger.to_dict()
    clone = Ledger.from_dict(ledger.to_dict())
    assert clone.status(_day(20)) == ledger.status(_day(20))
    assert clone.restored_learning_slices('point', 4, _day(21)) == [(_day(15), 4)]


def test_legacy_restore_projection_reproduces_184_without_touching_accounting():
    window = PlanWindow(_day(14), _day(22), 600)
    ledger = Ledger(window, 2659)
    # The observed post-replan calendar: four 296-minute days, then five 295.
    ledger._targets = {_day(day): (296 if day <= 17 else 295) for day in range(14, 23)}
    ledger.add_confirmed('old-a', 'old-a', 97, _day(14))
    ledger.add_confirmed('old-b', 'old-b', 137, _day(15))
    for index, (study_day, minutes) in enumerate(_source_rows()):
        ledger.add_tentative(f'subpoint:{index}', 'point-6', minutes, study_day)
    assert ledger.revoke('point-6') == 1246
    ledger._add_journal_credit('relearn:concept:attempt:token', 'point-6', 1246,
                               _day(20), 'confirmed')
    ledger.add_confirmed('test:attempt', 'test-6', 130, _day(20))

    before_targets = ledger.targets
    before_submissions = ledger.to_dict()['submissions']
    before = ledger.status(_day(20))
    assert before.today_repaid_confirmed == 1376
    assert before.debt_minutes == 164

    slices = learning_day_slices(_source_rows(), 1246)
    ledger.set_learning_day_projection({'relearn:concept:attempt:token': slices})
    after = ledger.status(_day(20))
    assert after.today_repaid_confirmed == 401
    assert after.debt_minutes == before.debt_minutes == 164
    assert after.today_target == before.today_target == 295
    assert ledger.targets == before_targets
    assert ledger.status(_day(20)).history_debt_minutes == before.history_debt_minutes
    assert ledger.to_dict()['submissions'] == before_submissions

    restored = Ledger.from_dict(ledger.to_dict())
    assert restored.status(_day(20)).today_repaid_confirmed == 401
    assert restored.status(_day(20)).debt_minutes == 164
    assert restored.targets == before_targets


def test_learning_day_slices_caps_to_sources_and_keeps_ordered_partial_source():
    assert learning_day_slices([(_day(15), 5), (_day(18), 9)], 8) == [(_day(15), 5), (_day(18), 3)]
    with pytest.raises(LedgerError) as error:
        learning_day_slices([(_day(15), 5)], 6)
    assert error.value.code == 'INSUFFICIENT_LEARNING_SOURCE'


def test_general_credit_api_cannot_create_recovery_on_a_chosen_day():
    ledger = Ledger(PlanWindow(_day(14), _day(20), 600), 1000)
    with pytest.raises(LedgerError) as error:
        ledger.add_confirmed('relearn:concept:attempt:token:source-day:2026-10-20',
                             'point', 10, _day(20))
    assert error.value.code == 'LEARNING_SOURCE_REQUIRED'

    # A live source is not eligible for restoration; only a revoked source is.
    ledger.add_tentative('learn:point', 'point', 15, _day(15))
    assert ledger.add_restored_learning('relearn:concept:attempt:token', 'point', 15, _day(20)) == []
    ledger.revoke('point')
    result = ledger.add_restored_learning('relearn:concept:attempt:token', 'point', 20, _day(20))
    assert [(credit.day, credit.minutes) for credit in result] == [(_day(15), 15)]


def test_legacy_direct_recovery_consumes_revoked_source_before_new_direct_minutes():
    ledger = Ledger(PlanWindow(_day(14), _day(22), 600), 1000)
    ledger.add_tentative('subpoint:source', 'point', 4, _day(15))
    ledger.revoke('point')
    # Immutable pre-BUG-120 direct recovery rows used a learn:<point> ID.
    ledger._add_journal_credit('learn:point', 'point', 4, _day(20), 'confirmed')
    ledger.set_learning_day_projection({'learn:point': [(_day(15), 4)]})

    added = ledger.add_restored_learning(
        'relearn:direct:attempt:token', 'point', 3, _day(20),
        allow_current_remainder=True)
    assert [(credit.day, credit.minutes) for credit in added] == [(_day(20), 3)]


def _journal_credit(credit_id, minutes, state, day):
    return {'credit_id': credit_id, 'unit_id': 'point', 'minutes': minutes,
            'state': state, 'day': day.isoformat()}


def test_confirm_direct_confirms_sources_before_a_later_revoke():
    source_day = _day(15)
    ops = [
        (1, 'learn:source', 'credit', {'credit': _journal_credit('learn:source', 4, 'tentative', source_day)}),
        (2, 'confirm-direct:attempt:point', 'confirm', {'unit_id': 'point'}),
        (3, 'learn:point', 'credit', {'credit': _journal_credit('learn:point', 4, 'confirmed', _day(20))}),
        (4, 'revoke:point', 'revoke', {'unit_id': 'point'}),
        (5, 'relearn:concept:later', 'credit', {'credit': _journal_credit('relearn:concept:later', 4, 'confirmed', _day(21))}),
    ]
    # The confirmed source must not enter the revoked pool during the later revoke.
    assert _legacy_learning_day_projection(ops) == {}


def test_marked_source_day_restore_consumes_pool_before_legacy_aggregate():
    source_day = _day(15)
    ops = [
        (1, 'learn:source', 'credit', {'credit': _journal_credit('learn:source', 4, 'tentative', source_day)}),
        (2, 'revoke:point', 'revoke', {'unit_id': 'point'}),
        (3, 'relearn:concept:first:source-day:2026-10-15', 'credit',
         {'credit': _journal_credit('relearn:concept:first:source-day:2026-10-15', 4, 'confirmed', source_day)}),
        (4, 'relearn:concept:legacy', 'credit', {'credit': _journal_credit('relearn:concept:legacy', 4, 'confirmed', _day(20))}),
    ]
    # The new row's own day is already correct; the later legacy row has no
    # remaining revoked source to project and therefore stays on its event day.
    assert _legacy_learning_day_projection(ops) == {}


def test_revoked_tentative_legacy_recovery_returns_only_its_source_budget():
    source_day = _day(15)
    ops = [
        (1, 'learn:source', 'credit', {'credit': _journal_credit('learn:source', 4, 'tentative', source_day)}),
        (2, 'revoke:point:first', 'revoke', {'unit_id': 'point'}),
        (3, 'relearn:concept:first', 'credit', {'credit': _journal_credit('relearn:concept:first', 4, 'tentative', _day(20))}),
        (4, 'revoke:point:second', 'revoke', {'unit_id': 'point'}),
        (5, 'relearn:concept:second', 'credit', {'credit': _journal_credit('relearn:concept:second', 4, 'confirmed', _day(21))}),
    ]
    projection = _legacy_learning_day_projection(ops)
    assert projection == {'relearn:concept:second': [(source_day, 4)]}

    # Applying the read-only projection must succeed with only the currently
    # active recovery row represented.
    ledger = Ledger(PlanWindow(_day(14), _day(22), 600), 100)
    ledger._add_journal_credit('relearn:concept:second', 'point', 4, _day(21), 'confirmed')
    ledger.set_learning_day_projection(projection)
    assert ledger.attributed_credits()[0][1:] == (source_day, 4)
