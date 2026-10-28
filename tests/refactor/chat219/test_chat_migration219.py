"""Retry legacy reads without resurrecting locally or remotely deleted chats."""
from datetime import datetime, timezone
import hashlib
import os

import pytest

from refactor.learning.app.chat_store import CourseChatStore
from refactor.learning.chat.persistence import FreeChatStoreIOError
from refactor.learning.chat.storage import ScopedChatStore
from refactor.learning.course.store import CourseStore
from refactor.learning.course.sync_course import CourseSync
from tests.refactor.sync_course.test_sync_course import connection  # noqa: F401

ACCOUNT = 'mfa1_' + 'a' * 64
NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def legacy(root, sid):
    old = ScopedChatStore(root / 'learning/accounts' / ACCOUNT / 'tools/chat')
    first = old.create(session_id=sid, created_at=NOW)
    old.commit_turn(sid, expected_version=first.version, user_content='旧问题',
                    assistant_content='旧回答', committed_at=NOW)
    return old.root / 'sessions' / (sid + '.json')


def key(sid):
    return hashlib.sha256(sid.encode()).hexdigest()


@pytest.mark.parametrize('reopen', [False, True])
def test_temporary_read_failure_retries_in_place_or_after_reopen(tmp_path, monkeypatch, reopen):
    path = legacy(tmp_path, 'retry')
    original = path.read_bytes()
    get = ScopedChatStore.get
    attempts = []

    def fail_once(reader, sid):
        attempts.append(sid)
        if len(attempts) == 1:
            raise FreeChatStoreIOError('temporary read failure')
        return get(reader, sid)

    monkeypatch.setattr(ScopedChatStore, 'get', fail_once)
    store = CourseStore(ACCOUNT, tmp_path)
    try:
        chat = CourseChatStore(store)
        assert chat.list_sessions() == ()
        assert not store.legacy_chat_imported()
        if reopen:
            store.close()
            store = CourseStore(ACCOUNT, tmp_path)
            chat = CourseChatStore(store)
        restored = chat.list_sessions()
        assert len(restored) == 1 and len(restored[0].messages) == 2
        assert store.legacy_chat_imported(key('retry'))
        pending = store.pending_records(100)
        assert len(pending) == 3
        assert len(chat.list_sessions()) == 1
        assert store.pending_records(100) == pending
        assert attempts == ['retry', 'retry'] and path.read_bytes() == original
    finally:
        store.close()


def test_repair_old_complete_imports_only_unimported_and_respects_both_deletion_guards(tmp_path):
    for sid in ('missing', 'local-deleted', 'remote-deleted'):
        legacy(tmp_path, sid)
    with CourseStore(ACCOUNT, tmp_path) as store:
        # Historical v1 total flag can be wrong. Per-source receipts and
        # synchronized tombstones are authoritative and must survive repair.
        store.mark_legacy_chat_imported()
        store.mark_legacy_chat_imported(key('local-deleted'))
        for sid in ('local-deleted', 'remote-deleted'):
            identifier = store.create_chat_session(session_id='legacy-' + key(sid))
            store.delete_chat_session(identifier)
        repaired = CourseChatStore(store).list_sessions()
        assert [s.session_id for s in repaired] == ['legacy-' + key('missing')]
        assert store.legacy_chat_imported(key('remote-deleted'))
        assert store._db.execute("SELECT count(*) FROM _records WHERE entity='chat_sessions' AND deleted=1").fetchone()[0] == 2
        pending = store.pending_records(100)
    with CourseStore(ACCOUNT, tmp_path) as store:
        assert len(CourseChatStore(store).list_sessions()) == 1
        assert store.pending_records(100) == pending


def test_repair_can_retry_even_with_old_global_complete_marker(tmp_path, monkeypatch):
    legacy(tmp_path, 'retry-old-complete')
    with CourseStore(ACCOUNT, tmp_path) as store:
        store.mark_legacy_chat_imported()
        with monkeypatch.context() as m:
            m.setattr(ScopedChatStore, 'get', lambda *a: (_ for _ in ()).throw(FreeChatStoreIOError('temporary')))
            assert CourseChatStore(store).list_sessions() == ()
        assert len(CourseChatStore(store).list_sessions()) == 1
        assert store.legacy_chat_imported(key('retry-old-complete'))


def test_directory_io_failure_is_not_treated_as_empty_finished_import(tmp_path, monkeypatch):
    path = legacy(tmp_path, 'unlisted')
    scandir = os.scandir
    with CourseStore(ACCOUNT, tmp_path) as store:
        def fail_directory(directory):
            if os.fspath(directory) == os.fspath(path.parent):
                raise PermissionError('temporary unavailable directory')
            return scandir(directory)
        with monkeypatch.context() as m:
            m.setattr(os, 'scandir', fail_directory)
            assert CourseChatStore(store).list_sessions() == ()
            assert not store.legacy_chat_imported()
        assert len(CourseChatStore(store).list_sessions()) == 1


def test_deleted_active_chat_syncs_without_archive_and_does_not_resurrect(connection, tmp_path):
    _, transport, login = connection
    with CourseStore(login.identity.account_id, tmp_path/'a') as a, CourseStore(login.identity.account_id, tmp_path/'b') as b:
        chat_a, chat_b = CourseChatStore(a), CourseChatStore(b)
        session = chat_a.create(session_id='active-delete219', created_at=NOW)
        chat_a.commit_turn(session.session_id, expected_version=session.version,
                           user_content='问题', assistant_content='回答', committed_at=NOW)
        for store in (a, b):
            assert CourseSync(store, transport).sync()['ok']
        assert len(chat_b.list_sessions()) == 1
        assert not chat_b.get(session.session_id).archived
        assert chat_a.delete(session.session_id)
        for store in (a, b):
            assert CourseSync(store, transport).sync()['ok']
        assert chat_b.list_sessions(archived=None) == ()
        assert b.chat_messages(session.session_id) == []
        assert CourseSync(a, transport).sync()['ok']
        assert chat_a.list_sessions(archived=None) == ()
