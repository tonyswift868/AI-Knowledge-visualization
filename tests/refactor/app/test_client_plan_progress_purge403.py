import json

from refactor.learning.course.store import CourseStore, PROGRESS
from refactor.learning.course.sync_contract import SyncRecord
from refactor.learning.course.sync_course import CourseSync, _wire_id


ACCOUNT = 'purge403_' + 'a' * 64


def seed_progress(store, plan_id, *, marker):
    for entity in PROGRESS:
        identifier = f'{marker}-{entity}'
        wire_id = f'{len(plan_id)}:{plan_id}{identifier}'
        payload = json.dumps({'id': identifier, 'content': marker}, separators=(',', ':'))
        revision = store._revision()
        store._db.execute(
            'INSERT INTO _records VALUES(?,?,?,?,?,?,?,?)',
            (entity, wire_id, plan_id, payload, 0, revision, 0, 1),
        )


def progress_rows(store, plan_id):
    return store._db.execute(
        'SELECT entity,payload FROM _records WHERE plan_key=? AND entity IN ('
        + ','.join('?' for _ in PROGRESS) + ')', (plan_id, *PROGRESS),
    ).fetchall()


def test_local_plan_delete_purges_its_progress_only(tmp_path):
    with CourseStore(ACCOUNT, tmp_path) as store:
        store.create_plan('deleted')
        store.create_plan('archived')
        store.create_plan('other')
        store.archive_plan('archived')
        seed_progress(store, 'deleted', marker='secret-deleted')
        seed_progress(store, 'archived', marker='secret-archived')
        seed_progress(store, 'other', marker='secret-other')

        store.delete_plan('deleted')

        assert progress_rows(store, 'deleted') == []
        assert {row['entity'] for row in progress_rows(store, 'archived')} == set(PROGRESS)
        assert {row['entity'] for row in progress_rows(store, 'other')} == set(PROGRESS)
        assert store._db.execute("SELECT payload FROM _records WHERE entity='plans' AND entity_id='archived'").fetchone()


def test_remote_deleted_plan_purges_progress_and_ignores_late_legacy_event(tmp_path):
    with CourseStore(ACCOUNT, tmp_path) as store:
        store.create_plan('gone')
        seed_progress(store, 'gone', marker='secret-old-client')
        tombstone = SyncRecord('plans', 'gone', 'gone', 'mentorflow.course.plans.v1',
                               {'id': 'gone'}, True, 1)

        store.apply_remote([(tombstone, 4)])
        late_event = SyncRecord('question_answers', '4:goneanswer', 'gone',
                                'mentorflow.course.question_answers.v1',
                                {'id': 'answer', 'content': 'secret-old-client'}, False, 1)
        store.apply_remote([(late_event, 5)])

        assert progress_rows(store, 'gone') == []
        assert not any(record.plan_key == 'gone' and record.entity in PROGRESS
                       for record in store.pending_records(1000))


def test_hash_only_plan_tombstone_is_recovered_from_pending_progress(tmp_path):
    scope = object()

    class Transport:
        def require_current(self):
            return scope

    with CourseStore(ACCOUNT, tmp_path) as store:
        store.create_plan('gone')
        plan = next(record for record in store.pending_records(100) if record.entity == 'plans')
        store.mark_synced(plan.entity, plan.entity_id, plan.local_revision, 1)
        seed_progress(store, 'gone', marker='secret-old-client')
        pending = store.pending_records(100)
        assert pending and all(record.entity in PROGRESS for record in pending)
        plan_values = dict(schema='mentorflow.course.plans.v1', plan_key='gone', entity='plans',
                           entity_id='gone', payload={'id': 'gone'})
        tombstone_id = _wire_id(plan_values)
        cursor = {
            'schema': 'mentorflow.course-sync-cursor.v1', 'scope': {}, 'since': '0',
            'records': {tombstone_id: {'revision': 1, 'deleted': True}},
        }
        sync = CourseSync(store, Transport())
        sync._scope = scope

        sync._apply_page(cursor, pending, [
            {'kind': 'learning_run', 'entity_id': tombstone_id, 'revision': 2,
             'deleted': True, 'payload': {}},
        ], '2', None)

        assert progress_rows(store, 'gone') == []
        assert not any(record.entity in PROGRESS for record in store.pending_records(1000))


def test_open_migration_purges_only_tombstoned_plan_and_is_repeatable(tmp_path):
    with CourseStore(ACCOUNT, tmp_path) as store:
        store.create_plan('gone')
        store.create_plan('still-here')
        seed_progress(store, 'gone', marker='secret-deleted')
        seed_progress(store, 'still-here', marker='secret-live')
        store._db.execute("UPDATE _records SET deleted=1 WHERE entity='plans' AND entity_id='gone'")
        store._db.execute("DELETE FROM plans WHERE id='gone'")
        store._db.execute('UPDATE schema_version SET version=12')

    with CourseStore(ACCOUNT, tmp_path) as migrated:
        assert progress_rows(migrated, 'gone') == []
        assert {row['entity'] for row in progress_rows(migrated, 'still-here')} == set(PROGRESS)

    with CourseStore(ACCOUNT, tmp_path) as reopened:
        assert progress_rows(reopened, 'gone') == []
        assert {row['entity'] for row in progress_rows(reopened, 'still-here')} == set(PROGRESS)
        assert reopened._db.execute('SELECT version FROM schema_version').fetchone()[0] == 13
