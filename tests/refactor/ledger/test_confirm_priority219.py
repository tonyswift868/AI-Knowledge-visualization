from datetime import date, timedelta
from itertools import permutations

import pytest

from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore, _legacy_learning_day_projection


DAY = date(2026, 1, 1)


def _seed(store):
    store.create_plan('plan', window=PlanWindow(DAY, DAY + timedelta(days=3), 100),
                      total_minutes=400)
    store.add_credit('plan', 'learn:point', 'point', 50, DAY, 'tentative')


def _events(store):
    return [r for r in store.pending_records(100) if r.entity == 'ledger_events']


def _receive(store, records):
    store.apply_remote([(r, 100 + i) for i, r in enumerate(records)])


@pytest.mark.parametrize('confirm_id,revoke_id', [
    ('confirm:attempt:point', 'revoke-concept:attempt:point'),
    ('z-confirm', 'a-revoke'),
    ('f815c001', '0091f001'),
])
def test_two_device_fork_confirmation_wins_without_event_name_priority219(tmp_path, confirm_id, revoke_id):
    a, b = CourseStore('acct', tmp_path / 'a'), CourseStore('acct', tmp_path / 'b')
    try:
        _seed(a)
        _receive(b, a.pending_records(100))
        assert a.confirm_credits('plan', 'point', event_id=confirm_id, today=DAY) == 50
        assert b.revoke_credits('plan', 'point', event_id=revoke_id, today=DAY) == 50
        event_a, event_b = _events(a), _events(b)
        assert event_a[0].payload['operation_seq'] == event_b[0].payload['operation_seq'] == 2
        _receive(a, event_b)
        _receive(b, event_a)
        for store in (a, b):
            ledger = store.load_ledger('plan')
            assert [(c['credit_id'], c['state']) for c in ledger.to_dict()['credits']] == [('learn:point', 'confirmed')]
            assert ledger.status(DAY).remaining_minutes == 350
            assert len(store.records('ledger_events', 'plan')) == 2
        assert a.load_ledger('plan').to_dict() == b.load_ledger('plan').to_dict()
        assert _events(a)[0].payload in [event_a[0].payload, event_b[0].payload]
    finally:
        a.close()
        b.close()


def test_interleaved_remote_credit_confirm_revoke_replay_converges219(tmp_path):
    a, b = CourseStore('acct', tmp_path / 'a'), CourseStore('acct', tmp_path / 'b')
    try:
        _seed(a)
        baseline = a.pending_records(100)
        _receive(b, baseline)
        a.confirm_credits('plan', 'point', event_id='z-confirm', today=DAY)
        b.revoke_credits('plan', 'point', event_id='a-revoke', today=DAY)
        credit = next(r for r in baseline if r.entity == 'credits')
        plan = next(r for r in baseline if r.entity == 'plans')
        for index, delivery in enumerate(permutations([credit, *_events(a), *_events(b)])):
            receiver = CourseStore('acct', tmp_path / f'receiver-{index}')
            try:
                _receive(receiver, [plan])
                for record in delivery:
                    _receive(receiver, [record])
                ledger = receiver.load_ledger('plan')
                assert ledger.status(DAY).remaining_minutes == 350
                assert ledger.to_dict()['credits'][0]['state'] == 'confirmed'
                # Repeated delivery remains idempotent; immutable payloads survive.
                _receive(receiver, list(delivery))
                assert receiver.load_ledger('plan').to_dict() == ledger.to_dict()
            finally:
                receiver.close()
    finally:
        a.close()
        b.close()


def test_sequential_revoke_then_confirm_does_not_restore_failed_learning219(tmp_path):
    store = CourseStore('acct', tmp_path)
    try:
        _seed(store)
        assert store.revoke_credits('plan', 'point', event_id='a-revoke', today=DAY) == 50
        assert store.confirm_credits('plan', 'point', event_id='z-confirm', today=DAY) == 0
        assert store.load_ledger('plan').status(DAY).remaining_minutes == 400
        assert store.load_ledger('plan').to_dict()['credits'] == []
    finally:
        store.close()


def test_legacy_restore_projection_uses_same_confirmation_priority219():
    def credit(identifier, day, state):
        return {'credit': {'credit_id': identifier, 'unit_id': 'point', 'minutes': 50,
                           'day': day.isoformat(), 'state': state}}
    operations = [
        (1, 'learn:point', 'credit', credit('learn:point', DAY, 'tentative')),
        (2, 'a-revoke', 'revoke', {'unit_id': 'point'}),
        (2, 'z-confirm', 'confirm', {'unit_id': 'point'}),
        (3, 'relearn:concept:old:point', 'credit',
         credit('relearn:concept:old:point', DAY + timedelta(days=1), 'confirmed')),
    ]
    # Confirmed learning never becomes a revoked source for old restoration rows.
    assert _legacy_learning_day_projection(operations) == {}
