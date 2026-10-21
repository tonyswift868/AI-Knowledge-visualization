from datetime import timedelta

from tests.refactor.concept114.test_concept114_classroom import PLAN, concept_environment


def _restore_rows(store, point_id='p0'):
    return [row for row in store.records('credits', PLAN)
            if row['credit']['unit_id'] == point_id and row['id'].startswith('relearn:')]


def test_failed_concept_test_retest_restores_original_learning_days(tmp_path):
    e = concept_environment(tmp_path)
    try:
        store, room = e.store, e.room
        days = [room.study_day() + timedelta(days=offset) for offset in range(3)]
        for index, day in enumerate(days):
            store.add_credit(PLAN, f'subpoint:source-{index}', 'p0', 5, day, 'tentative')

        today = days[-1]
        store.replan_plan(PLAN, today, cause='explicit_replan')
        store.revoke_credits(PLAN, 'p0', event_id='failed-module-test', today=today)
        before = store.load_ledger(PLAN).to_dict()

        room._restore_concept_learning(room._course(PLAN), {'module_id': 'm0', 'day': today.isoformat()}, 'passed-retest')
        after_ledger = store.load_ledger(PLAN)
        after = after_ledger.to_dict()

        recovered = _restore_rows(store)
        assert [(row['credit']['day'], row['credit']['minutes']) for row in recovered] == [
            (day.isoformat(), 5) for day in days
        ]
        status = after_ledger.status(today)
        assert status.today_repaid_confirmed == 5
        assert before['history'] == after['history']
        assert before['targets'] == after['targets']
        attributed = {(day.isoformat(), minutes) for _, day, minutes in after_ledger.attributed_credits()
                      if _.unit_id == 'p0'}
        assert attributed == {(day.isoformat(), 5) for day in days}
    finally:
        e.room.cancel_materials()
        e.store.close()


def test_direct_skip_splits_old_learning_from_new_minutes(tmp_path):
    e = concept_environment(tmp_path)
    try:
        store, room = e.store, e.room
        first, second, today = (room.study_day() + timedelta(days=offset) for offset in range(3))
        store.add_credit(PLAN, 'subpoint:old-a', 'p0', 2, first, 'tentative')
        store.add_credit(PLAN, 'subpoint:old-b', 'p0', 4, second, 'tentative')
        store.revoke_credits(PLAN, 'p0', event_id='failed-before-direct', today=today)

        room._direct_credits(room._course(PLAN), {'module_id': 'm0', 'day': today.isoformat()}, 'direct-skip')

        recovered = _restore_rows(store)
        assert sorted((row['credit']['day'], row['credit']['minutes']) for row in recovered) == sorted([
            (first.isoformat(), 2), (second.isoformat(), 4), (today.isoformat(), 9)
        ])
        assert sum(row['credit']['minutes'] for row in recovered) == 15
    finally:
        e.room.cancel_materials()
        e.store.close()


def test_concept_retest_restores_only_minutes_with_learning_sources(tmp_path):
    e = concept_environment(tmp_path)
    try:
        store, room = e.store, e.room
        first, second, today = (room.study_day() + timedelta(days=offset) for offset in range(3))
        store.add_credit(PLAN, 'subpoint:partial-a', 'p0', 2, first, 'tentative')
        store.add_credit(PLAN, 'subpoint:partial-b', 'p0', 4, second, 'tentative')
        store.revoke_credits(PLAN, 'p0', event_id='failed-partial-test', today=today)

        room._restore_concept_learning(room._course(PLAN), {'module_id': 'm0', 'day': today.isoformat()}, 'passed-partial-retest')

        recovered = _restore_rows(store)
        assert sorted((row['credit']['day'], row['credit']['minutes']) for row in recovered) == sorted([
            (first.isoformat(), 2), (second.isoformat(), 4)
        ])
        assert sum(row['credit']['minutes'] for row in recovered) == 6
    finally:
        e.room.cancel_materials()
        e.store.close()


def test_direct_skip_fresh_point_keeps_established_learn_credit_id(tmp_path):
    e = concept_environment(tmp_path)
    try:
        store, room = e.store, e.room
        today = room.study_day()
        room._direct_credits(room._course(PLAN), {'module_id': 'm0', 'day': today.isoformat()},
                             'fresh-direct-skip')

        rows = [row for row in store.records('credits', PLAN)
                if row['credit']['unit_id'] == 'p0']
        assert [(row['id'], row['credit']['day'], row['credit']['minutes'], row['credit']['state'])
                for row in rows] == [(f'learn:p0', today.isoformat(), 15, 'confirmed')]
        assert not any(row['id'].startswith('relearn:') for row in rows)
    finally:
        e.room.cancel_materials()
        e.store.close()
