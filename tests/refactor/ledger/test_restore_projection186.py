from copy import deepcopy
from datetime import date, timedelta

import pytest

from refactor.learning.course.ledger import PlanWindow, Ledger
from refactor.learning.course.store import CourseStore, StoreError


ACCOUNT = 'mfa1_' + 'd' * 64
PLAN = 'e' * 32
START = date(2026, 10, 14)
TODAY = date(2026, 10, 20)
SOURCES = [(date(2026, 10, day), minutes) for day, minutes in (
    (15, 387), (16, 88), (17, 186), (18, 8), (19, 306), (20, 271))]


def make_store(root):
    store = CourseStore(ACCOUNT, root)
    window = PlanWindow(START, date(2026, 10, 22), 600)
    store.create_plan(PLAN, goal='账本归属回归', final_goal='恢复保留原学习日', level='beginner',
                      window=window, total_minutes=2659)
    store.add_sources(PLAN, [dict(id='src', kind='url', url='https://example.invalid/source', adopted=True,
                                  chunks=[dict(chunk_id='chunk', text='原文。')])])
    store.save_outline(PLAN, [dict(id='stage', title='阶段')],
                       [dict(id='module', stage_id='stage', title='模块')])
    store.save_generated_module(PLAN, 'module', [dict(id='point', title='知识点', summary='目标',
        minutes=2659, chunk_ids=['chunk'])], check_minutes=0, today=START)
    return store


def seed_legacy_184_case(store):
    # Confirmed earlier work remains live; six revoked source rows represent
    # the 1,246-minute recovery that the old classroom code lumped onto TODAY.
    store.add_credit(PLAN, 'learn:before-a', 'point', 97, START, 'confirmed')
    store.add_credit(PLAN, 'learn:before-b', 'point', 137, date(2026, 10, 15), 'confirmed')
    for index, (day, minutes) in enumerate(SOURCES):
        store.add_credit(PLAN, f'subpoint:source-{index}', 'point', minutes, day, 'tentative')
    store.revoke_credits(PLAN, 'point', event_id='legacy-failed-test', today=TODAY)

    legacy_id = 'relearn:concept:old-attempt:old-token'
    ledger = store.load_ledger(PLAN)
    credit = dict(credit_id=legacy_id, unit_id='point', minutes=1246, day=TODAY.isoformat(),
                  state='confirmed', seq=ledger.to_dict()['next_seq'])
    with store.transaction():
        store._put('credits', PLAN, dict(id=legacy_id, credit=credit,
                                         operation_seq=store._op_seq(PLAN)))
    store.add_credit(PLAN, 'test:today-questions', 'test:module', 130, TODAY, 'confirmed')


def test_legacy_recovery_projects_184_today_repaid_and_debt_and_roundtrips(tmp_path):
    store = make_store(tmp_path)
    try:
        seed_legacy_184_case(store)
        ledger = store.load_ledger(PLAN)
        status = ledger.status(TODAY)
        assert status.today_repaid_confirmed == 401
        assert status.debt_minutes == 164
        assert status.today_target == 295
        assert status.remaining_minutes == 1049

        expected = [(day, minutes) for day, minutes in SOURCES]
        actual = [(day, minutes) for credit, day, minutes in ledger.attributed_credits()
                  if credit.credit_id == 'relearn:concept:old-attempt:old-token']
        assert actual == expected
        restored = Ledger.from_dict(ledger.to_dict())
        assert restored.to_dict() == ledger.to_dict()
        assert restored.status(TODAY).today_repaid_confirmed == 401
        assert restored.status(TODAY).debt_minutes == 164
    finally:
        store.close()


def test_restore_pool_skips_live_source_and_same_attempt_retry_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    try:
        live_day, revoked_day, today = START, START + timedelta(days=1), START + timedelta(days=2)
        store.add_credit(PLAN, 'subpoint:still-live', 'point', 5, live_day, 'confirmed')
        store.add_credit(PLAN, 'subpoint:revoked', 'point', 6, revoked_day, 'tentative')
        store.revoke_credits(PLAN, 'point', event_id='one-failed-test', today=today)

        prefix = 'relearn:concept:attempt-one:point-hash'
        first = store.add_restored_learning(PLAN, prefix, 'point', 15, today)
        second = store.add_restored_learning(PLAN, prefix, 'point', 9, today)
        assert first == second == [dict(credit_id=f'{prefix}:source-day:{revoked_day.isoformat()}',
                                        day=revoked_day.isoformat(), minutes=6)]
        assert not any(day == live_day and minutes > 0 for day, minutes in [
            (row['day'], row['minutes']) for row in first
        ])
        with pytest.raises(StoreError) as exc:
            store.add_credit(PLAN, 'relearn:concept:bypass:token', 'point', 1, today, 'confirmed')
        assert exc.value.code == 'LEARNING_SOURCE_REQUIRED'
        assert len([row for row in store.records('credits', PLAN) if row['id'].startswith(prefix)]) == 1
    finally:
        store.close()


def test_multiday_restore_rolls_back_partial_store_write_and_retries_atomically(tmp_path):
    store = make_store(tmp_path)
    try:
        first_day, second_day = START + timedelta(days=1), START + timedelta(days=2)
        recovery_day = START + timedelta(days=3)
        store.add_credit(PLAN, 'subpoint:revoked-a', 'point', 6, first_day, 'tentative')
        store.add_credit(PLAN, 'subpoint:revoked-b', 'point', 8, second_day, 'tentative')
        store.revoke_credits(PLAN, 'point', event_id='two-day-failed-test', today=recovery_day)
        before_credits = deepcopy(store.records('credits', PLAN))
        before_outbox = [tuple(row) for row in store._db.execute(
            'SELECT entity,entity_id,plan_key,payload,local_revision,server_revision,pending,deleted '
            'FROM _records ORDER BY entity,entity_id')]

        original_put = store._put
        failed_once = False

        def fail_second_restore(entity, plan_id, payload):
            nonlocal failed_once
            if entity == 'credits' and payload.get('id', '').startswith('relearn:concept:two-day:'):
                if failed_once:
                    raise RuntimeError('injected second-slice write failure')
                failed_once = True
            return original_put(entity, plan_id, payload)

        store._put = fail_second_restore
        with pytest.raises(RuntimeError, match='injected second-slice'):
            store.add_restored_learning(PLAN, 'relearn:concept:two-day:attempt', 'point', 14,
                                        recovery_day)
        assert store.records('credits', PLAN) == before_credits
        after_failed_outbox = [tuple(row) for row in store._db.execute(
            'SELECT entity,entity_id,plan_key,payload,local_revision,server_revision,pending,deleted '
            'FROM _records ORDER BY entity,entity_id')]
        assert after_failed_outbox == before_outbox

        store._put = original_put
        expected = [
            dict(credit_id='relearn:concept:two-day:attempt:source-day:' + first_day.isoformat(),
                 day=first_day.isoformat(), minutes=6),
            dict(credit_id='relearn:concept:two-day:attempt:source-day:' + second_day.isoformat(),
                 day=second_day.isoformat(), minutes=8),
        ]
        assert store.add_restored_learning(PLAN, 'relearn:concept:two-day:attempt', 'point', 14,
                                           recovery_day) == expected
        assert store.add_restored_learning(PLAN, 'relearn:concept:two-day:attempt', 'point', 14,
                                           recovery_day) == expected
    finally:
        store.close()


@pytest.mark.parametrize('chunked', [False, True], ids=['plain', 'chunks'])
def test_cached_report_projection_is_read_only_and_stable_after_comment_save(tmp_path, chunked):
    store = make_store(tmp_path)
    try:
        seed_legacy_184_case(store)
        with store.transaction():
            plan = store.get_plan(PLAN)
            plan['state'] = 'completed'
            store._put('plans', PLAN, plan)
        report_id = 'f' * 32 if chunked else PLAN
        original_days = [
            dict(day=(START).isoformat(), credited_confirmed_minutes=97,
                 credited_tentative_minutes=0, submitted_minutes=97),
            dict(day=(START + timedelta(days=1)).isoformat(), credited_confirmed_minutes=137,
                 credited_tentative_minutes=0, submitted_minutes=137),
            # Legacy cache versions can persist multiple aggregate rows for a
            # day; their sum is the old lumped amount, larger than the corrected
            # source-day value for that day.
            dict(day=TODAY.isoformat(), credited_confirmed_minutes=700,
                 credited_tentative_minutes=0, submitted_minutes=700),
            dict(day=TODAY.isoformat(), credited_confirmed_minutes=676,
                 credited_tentative_minutes=0, submitted_minutes=676),
        ]
        original = dict(time=dict(days=original_days,
            activity_dates=[row['day'] for row in original_days], learning_days=3), comments=[])
        if chunked:
            original['large_note'] = 'x' * (270 * 1024)
        store.save_report(PLAN, original, report_id=report_id)
        before_read = deepcopy(store.records('reports', PLAN))

        corrected = store.load_report(PLAN, report_id=report_id)
        corrected_days = {row['day']: row['credited_confirmed_minutes'] for row in corrected['time']['days']}
        assert corrected_days[TODAY.isoformat()] == 401
        assert corrected_days['2026-10-15'] == 524
        assert corrected_days['2026-10-16'] == 88
        assert corrected['time']['learning_days'] == 7
        assert store.records('reports', PLAN) == before_read

        corrected['comments'].append('保留用户批注')
        store.save_report(PLAN, corrected, report_id=report_id)
        saved_rows = deepcopy(store.records('reports', PLAN))
        after_save = store.load_report(PLAN, report_id=report_id)
        after_second_read = store.load_report(PLAN, report_id=report_id)
        assert after_save == after_second_read
        assert after_save['comments'] == ['保留用户批注']
        assert {row['day']: row['credited_confirmed_minutes'] for row in after_save['time']['days']} == corrected_days
        assert store.records('reports', PLAN) == saved_rows
    finally:
        store.close()
