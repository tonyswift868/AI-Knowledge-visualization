"""Account chat archive metadata migration and sync compatibility."""
import json

import pytest

from refactor.learning.course.store import CourseStore, SCHEMA_VERSION
from refactor.learning.course.sync_contract import SyncRecord
from refactor.learning.course.sync_course import CourseSync
from tests.refactor.sync_course.test_sync_course import connection


ACCOUNT = 'mfa1_' + 'a' * 64


def test_schema11_migration_adds_default_without_rewriting_messages(tmp_path):
    with CourseStore(ACCOUNT, tmp_path) as store:
        session_id = store.create_chat_session('旧会话', session_id='old-chat')
        store.append_chat_message(session_id, 'user', '保留的消息', message_id='old-message', at='2026-10-20T00:00:00+00:00')
        row = store._db.execute("SELECT local_revision,pending,payload FROM _records WHERE entity='chat_sessions'").fetchone()
        message_row = store._db.execute("SELECT payload,revision FROM chat_messages WHERE id='old-message'").fetchone()
        before_message = tuple(message_row)
        session = json.loads(row['payload'])
        session.pop('archived')
        store._db.execute("UPDATE _records SET payload=? WHERE entity='chat_sessions'", (json.dumps(session),))
        store._db.execute("UPDATE chat_sessions SET payload=? WHERE id='old-chat'", (json.dumps(session),))
        store._db.execute('UPDATE schema_version SET version=10')
        before_revision, before_pending = row['local_revision'], row['pending']

    with CourseStore(ACCOUNT, tmp_path) as migrated:
        assert migrated._db.execute('SELECT version FROM schema_version').fetchone()[0] == SCHEMA_VERSION
        assert migrated.get_chat_session('old-chat')['archived'] is False
        assert tuple(migrated._db.execute("SELECT payload,revision FROM chat_messages WHERE id='old-message'").fetchone()) == before_message
        row = migrated._db.execute("SELECT local_revision,pending FROM _records WHERE entity='chat_sessions'").fetchone()
        assert (row['local_revision'], row['pending']) == (before_revision, before_pending)
        assert migrated.chat_messages('old-chat')[0]['content'] == '保留的消息'


def test_archived_session_syncs_between_course_store_devices(connection, tmp_path):
    server, transport, login = connection
    with CourseStore(login.identity.account_id, tmp_path / 'device-a') as a, \
            CourseStore(login.identity.account_id, tmp_path / 'device-b') as b:
        sid = a.create_chat_session('跨设备', session_id='shared-chat')
        a.append_chat_message(sid, 'user', '消息内容', message_id='shared-message', at='2026-10-20T00:00:00+00:00')
        assert CourseSync(a, transport).sync()['ok']
        assert CourseSync(b, transport).sync()['ok']
        a.set_chat_session_archived(sid, True)
        assert CourseSync(a, transport).sync()['ok']
        assert CourseSync(b, transport).sync()['ok']
        assert b.get_chat_session(sid)['archived'] is True
        assert b.chat_messages(sid)[0]['content'] == '消息内容'

        def make_server_row_legacy():
            for wire_id, row in server.rows.items():
                values = row.get('payload', {}).get('values', {})
                if values.get('entity') == 'chat_sessions' and values.get('payload', {}).get('id') == sid:
                    values['payload'].pop('archived', None)
                    values['payload'].get('_field_versions', {}).pop('archived', None)
                    row['revision'] += 1
                    server.stamp(wire_id)
                    return
            raise AssertionError('chat session is missing from the fake sync server')

        # A non-pending local row must be repaired after reading an old-server
        # payload; the ensuing upload teaches that server the additive field.
        make_server_row_legacy()
        assert CourseSync(b, transport).sync()['ok']
        session_row = b._db.execute("SELECT pending,payload FROM _records WHERE entity='chat_sessions'").fetchone()
        assert session_row['pending'] == 0 and json.loads(session_row['payload'])['archived'] is True
        sync = CourseSync(b, transport)
        assert sync.sync()['ok'] and sync.sync()['uploaded'] == 0

        # Repeat while the local row is pending as well. The local archive and
        # its field stamp win the merge, then exactly one repair upload settles.
        b.set_chat_session_archived(sid, False)
        b.set_chat_session_archived(sid, True)
        make_server_row_legacy()
        assert CourseSync(b, transport).sync()['ok']
        session_row = b._db.execute("SELECT pending,payload FROM _records WHERE entity='chat_sessions'").fetchone()
        assert session_row['pending'] == 0 and json.loads(session_row['payload'])['archived'] is True
        assert CourseSync(b, transport).sync()['uploaded'] == 0


@pytest.mark.parametrize('was_pending', [False, True])
def test_old_remote_without_archive_preserves_true_and_repairs_once(tmp_path, was_pending):
    with CourseStore(ACCOUNT, tmp_path) as store:
        sid = store.create_chat_session('兼容', session_id=f'compat-{was_pending}')
        store.set_chat_session_archived(sid, True)
        row = store._db.execute("SELECT * FROM _records WHERE entity='chat_sessions'").fetchone()
        wire_id = row['entity_id']
        if not was_pending:
            store.mark_synced('chat_sessions', wire_id, row['local_revision'], 1)
        local = store.get_chat_session(sid)
        local_record = store._db.execute("SELECT payload FROM _records WHERE entity='chat_sessions'").fetchone()
        versions = json.loads(local_record['payload']).get('_field_versions', {})
        old_payload = {'id': sid, 'title': local['title']}
        incoming = SyncRecord('chat_sessions', wire_id, '', 'mentorflow.course.chat_sessions.v1',
                              old_payload, False, 2)

        store.apply_remote([(incoming, 2)])
        assert store.get_chat_session(sid)['archived'] is True
        after = store._db.execute("SELECT payload,pending FROM _records WHERE entity='chat_sessions'").fetchone()
        assert after['pending'] == 1
        assert json.loads(after['payload']).get('_field_versions', {}).get('archived') == versions['archived']

        # A modern peer stores the repaired field and its LWW stamp. Re-reading
        # that explicit value must settle without an endless repair upload.
        repaired = json.loads(after['payload'])
        store.mark_synced('chat_sessions', wire_id,
                          store._db.execute("SELECT local_revision FROM _records WHERE entity='chat_sessions'").fetchone()[0], 3)
        remote = SyncRecord('chat_sessions', wire_id, '', 'mentorflow.course.chat_sessions.v1', repaired, False, 3)
        store.apply_remote([(remote, 4)])
        assert store.get_chat_session(sid)['archived'] is True
        assert not [r for r in store.pending_records(100) if r.entity == 'chat_sessions']
        # A later same-value echo is idempotent.
        store.apply_remote([(remote, 5)])
        assert not [r for r in store.pending_records(100) if r.entity == 'chat_sessions']


def test_set_chat_session_archived_validates_boolean_and_restores_explicit_false(tmp_path):
    with CourseStore(ACCOUNT, tmp_path) as store:
        sid = store.create_chat_session(session_id='toggle')
        assert store.set_chat_session_archived(sid, True)['archived'] is True
        assert store.set_chat_session_archived(sid, False)['archived'] is False
        with pytest.raises(ValueError):
            store.set_chat_session_archived(sid, 1)


def test_newer_explicit_remote_false_restores_archived_session(tmp_path):
    with CourseStore(ACCOUNT, tmp_path) as store:
        sid = store.create_chat_session('可恢复', session_id='restore-chat')
        store.set_chat_session_archived(sid, True)
        local = store._db.execute("SELECT * FROM _records WHERE entity='chat_sessions'").fetchone()
        local_payload = json.loads(local['payload'])
        local_stamp = local_payload['_field_versions']['archived']
        store.mark_synced('chat_sessions', local['entity_id'], local['local_revision'], 1)

        remote_payload = dict(id=sid, title='可恢复', archived=False,
                              _field_versions={'archived': [local_stamp[0] + 1, 'remote-device']})
        remote = SyncRecord('chat_sessions', local['entity_id'], '', 'mentorflow.course.chat_sessions.v1',
                            remote_payload, False, 2)
        store.apply_remote([(remote, 2)])
        assert store.get_chat_session(sid)['archived'] is False
        assert not [r for r in store.pending_records(100) if r.entity == 'chat_sessions']
