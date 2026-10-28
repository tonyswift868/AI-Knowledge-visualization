import json

from test_sync_course import CourseSync, Record, MemoryStore, connection
from refactor.learning.course.store import CourseStore


def test_bug163_unknown_record_is_replayed_after_client_upgrade(connection):
    server, transport, _ = connection
    unknown = Record('future_entity', 'future-one', 'plan-one',
                     'mentorflow.course.future_entity.v1', {'id': 'future-one', 'value': 'kept'},
                     False, 1)
    server.incremental = True
    server.put_remote(unknown)
    store = MemoryStore()
    real_apply = store.apply_remote
    understood = set()

    def needs_replay(entity, entity_id):
        return entity in understood and (entity, entity_id) not in store.rows
    store.needs_sync_replay = needs_replay

    # Simulate the old client: it recognizes the envelope but not this entity.
    def old_apply(records):
        real_apply([(row, rev) for row, rev in records if row.entity != 'future_entity'])
    store.apply_remote = old_apply

    sync = CourseSync(store, transport)
    first = sync.sync(limit=20)
    first_cursor = json.loads(store.cursor)
    wire_id = next(iter(first_cursor['records']))
    assert first['pulled'] == 1
    assert first_cursor['since'] == '1'
    assert first_cursor['records'][wire_id]['entity'] == 'future_entity'
    assert not store.applied

    # Simulate upgrade: the same storage now understands the previously unknown entity.
    store.apply_remote = real_apply
    understood.add('future_entity')
    second = sync.sync(limit=20)

    assert second['pulled'] == 1
    assert store.applied[0][0].entity == 'future_entity'
    upgraded_cursor = json.loads(store.cursor)
    assert upgraded_cursor['since'] == '1'
    assert server.pages[-1]['since'] == '0'

    third = sync.sync(limit=20)
    assert third['pulled'] == 0
    assert server.pages[-1]['since'] == '1'


def test_bug163_replay_detection_requires_a_known_but_absent_journal_row(tmp_path):
    store = CourseStore('bug163-account', tmp_path)
    try:
        assert store.needs_sync_replay('modules', 'not-yet-materialized')
        assert not store.needs_sync_replay('future_entity', 'not-yet-materialized')
        store.create_plan('local-plan', goal='本机较新标题')
        local_plan = next(row for row in store.pending_records(20) if row.entity == 'plans')
        assert not store.needs_sync_replay(local_plan.entity, local_plan.entity_id)
        assert store.get_plan('local-plan')['goal'] == '本机较新标题'
    finally:
        store.close()

