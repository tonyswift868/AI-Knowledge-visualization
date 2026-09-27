"""Chat persistence: transactional turns, durable order, and legacy import."""
from datetime import datetime, timezone
import sqlite3
from threading import Event

import pytest

from refactor.learning.app.chat_store import CourseChatStore
from refactor.learning.connections.errors import ModelError, ModelCancelled
from refactor.learning.course.store import CourseStore
from refactor.learning.tools.free_chat.session import FreeChatSessionConflictError
from refactor.learning.tools.service import ScopedChatStore
from refactor.learning.tools.storage import ToolStore

from .test_app_free_chat import ACCOUNT, setup, ask
from .test_app_window_layer import qapp, tmp_root  # noqa: F401

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def legacy(root, account=ACCOUNT, sid='old-session'):
    reader = ScopedChatStore(ToolStore(root, account).root / 'chat')
    session = reader.create(session_id=sid, created_at=NOW)
    reader.commit_turn(sid, expected_version=session.version, user_content='以前聊过', assistant_content='旧回复', committed_at=NOW)
    return reader.root / 'sessions' / (sid + '.json')


def test_same_instant_turn_order_title_and_reopen(qapp, tmp_root, monkeypatch):
    from refactor.learning.app import chat
    monkeypatch.setattr(chat, 'utc_now', lambda: NOW)
    services, bridge, sent = setup(qapp, tmp_root)
    try:
        sid = ask(qapp, bridge, sent, 'create', 'chat.create')['result']['session']['id']
        for i in range(3):
            reply = ask(qapp, bridge, sent, f'send{i}', 'chat.send', dict(session_id=sid, text=f'问题{i}'))
            assert reply['ok']
        saved = reply['result']['session']
        assert [m['role'] for m in saved['messages']] == ['user', 'assistant'] * 3
        assert [m['seq'] for m in saved['messages']] == list(range(1, 7))
        assert services.store.get_chat_session(sid)['title'] == saved['title'] == '问题0'
        assert len(services.store.pending_records(100)) == 7
        assert not list(services.store.root.glob('tools/chat/sessions/*.json'))
        with CourseStore(ACCOUNT, tmp_root) as reopened:
            again = CourseChatStore(reopened).get(sid)
            assert [m.content for m in again.messages] == [m['content'] for m in saved['messages']]
            assert again.title == saved['title']
    finally:
        bridge.close(); services.close()


@pytest.mark.parametrize('failure', ['storage', 'cancel'])
def test_turn_rollback_and_observer_isolation(tmp_root, monkeypatch, failure):
    with CourseStore(ACCOUNT, tmp_root) as store, CourseStore(ACCOUNT, tmp_root) as reader:
        chat = CourseChatStore(store)
        snapshot = chat.create(session_id='s', created_at=NOW)
        pending = store.pending_records(100)
        notified = []
        store.subscribe(lambda origin, entities: notified.append((origin, entities, reader.chat_messages('s'))))
        original = store.append_chat_message
        cancel = Event()
        def append(*args, **kwargs):
            result = original(*args, **kwargs)
            assert reader.chat_messages('s') == []  # no half-turn visible outside the transaction
            if args[1] == 'assistant':
                if failure == 'storage': raise sqlite3.OperationalError('synthetic disk failure')
                cancel.set()
            return result
        monkeypatch.setattr(store, 'append_chat_message', append)
        with pytest.raises(sqlite3.OperationalError if failure == 'storage' else ModelCancelled):
            chat.commit_turn('s', expected_version=snapshot.version, user_content='问题', assistant_content='回答', committed_at=NOW, cancel=cancel)
        assert store.chat_messages('s') == [] and store.pending_records(100) == pending and notified == []
        assert store.get_chat_session('s')['title'] == '新对话'
        monkeypatch.setattr(store, 'append_chat_message', original)
        chat.commit_turn('s', expected_version=snapshot.version, user_content='问题', assistant_content='回答', committed_at=NOW)
        assert len(notified) == 1 and len(notified[0][2]) == 2
        with pytest.raises(FreeChatSessionConflictError):
            chat.commit_turn('s', expected_version=snapshot.version, user_content='过期', assistant_content='回答', committed_at=NOW)


def test_failed_model_stream_keeps_outbox_unchanged(qapp, tmp_root):
    services, bridge, sent = setup(qapp, tmp_root)
    try:
        sid = ask(qapp, bridge, sent, 'create', 'chat.create')['result']['session']['id']
        pending = services.store.pending_records(100)
        def fail(*args, **kwargs):
            kwargs['on_text']('不完整回复')
            raise ModelError('MODEL_UNAVAILABLE', '模型暂不可用。')
        services.gateway.complete = fail
        response = ask(qapp, bridge, sent, 'send', 'chat.send', dict(session_id=sid, text='问题'))
        assert response['error']['code'] == 'MODEL_UNAVAILABLE'
        assert services.store.chat_messages(sid) == [] and services.store.pending_records(100) == pending
    finally:
        bridge.close(); services.close()


def test_import_once_preserves_files_skips_bad_and_isolates_accounts(tmp_root, caplog):
    path = legacy(tmp_root)
    raw = path.read_bytes()
    bad = path.with_name('bad.json'); bad.write_text('{broken', encoding='utf-8')
    second_account = 'mfa1_' + 'e' * 64
    legacy(tmp_root, second_account, 'other')
    with CourseStore(ACCOUNT, tmp_root) as store:
        chat = CourseChatStore(store)
        sessions = chat.list_sessions()
        assert len(sessions) == 1 and sessions[0].title == '以前聊过'
        assert store.legacy_chat_imported() and path.read_bytes() == raw and bad.read_text() == '{broken'
        assert 'bad.json' in caplog.text and '旧回复' not in caplog.text
        # Deleting an imported chat must not be undone by re-opening the app.
        chat.delete(sessions[0].session_id)
        before = store.pending_records(100)
    with CourseStore(ACCOUNT, tmp_root) as reopened:
        assert CourseChatStore(reopened).list_sessions() == ()
        assert reopened.pending_records(100) == before
    with CourseStore(second_account, tmp_root) as other:
        assert len(CourseChatStore(other).list_sessions()) == 1


def test_chat_lookup_uses_index_and_nested_rollback_does_not_notify(tmp_root):
    with CourseStore(ACCOUNT, tmp_root) as store:
        store.create_chat_session(session_id='s', created_at=NOW)
        events = []
        store.subscribe(lambda origin, entities: events.append((origin, entities)))
        with store.transaction():
            store.rename_chat_session('s', '保留')
            with pytest.raises(RuntimeError):
                with store.transaction():
                    store.append_chat_message('s', 'user', '回滚')
                    raise RuntimeError()
        assert events == [('local', frozenset({'chat_sessions'}))]
        assert store.chat_messages('s') == []
        plan = store._db.execute("EXPLAIN QUERY PLAN SELECT payload FROM chat_messages WHERE plan_key='' AND session_id=? ORDER BY json_extract(payload,'$.at'),id", ('s',)).fetchall()
        assert any('chat_messages_order' in row[3] for row in plan)
        assert all('TEMP B-TREE' not in row[3] for row in plan)
        # A v2 account upgrades without touching its data or outbox.
        pending = store.pending_records(100)
        store._db.execute('DROP INDEX chat_messages_order')
        store._db.execute('UPDATE schema_version SET version=2')
    with CourseStore(ACCOUNT, tmp_root) as upgraded:
        assert upgraded.pending_records(100) == pending
        assert upgraded.get_chat_session('s')['title'] == '保留'
        assert upgraded._db.execute("SELECT 1 FROM sqlite_master WHERE name='chat_messages_order'").fetchone()


def test_interrupted_import_retries_unfinished_file_without_duplicating(tmp_root, monkeypatch):
    legacy(tmp_root, sid='first')
    legacy(tmp_root, sid='second')
    with CourseStore(ACCOUNT, tmp_root) as store:
        original = store.import_chat_session
        attempts = []
        def fail_second(*args):
            attempts.append(args[0])
            if len(attempts) == 2: raise sqlite3.OperationalError('synthetic interrupted import')
            return original(*args)
        monkeypatch.setattr(store, 'import_chat_session', fail_second)
        chat = CourseChatStore(store)
        with pytest.raises(sqlite3.OperationalError): chat.list_sessions()
        assert not store.legacy_chat_imported()
        assert len(store.records('chat_sessions')) == 1 and len(store.pending_records(100)) == 3
        monkeypatch.setattr(store, 'import_chat_session', original)
        assert len(chat.list_sessions()) == 2
        assert store.legacy_chat_imported() and len(store.pending_records(100)) == 6


def test_closing_services_does_not_wait_for_legacy_file_read(qapp, tmp_root, monkeypatch):
    import time
    legacy(tmp_root)
    started, release, finished = Event(), Event(), Event()
    original = ScopedChatStore.get
    def blocked(self, sid):
        started.set()
        assert release.wait(5)
        try:
            return original(self, sid)
        finally:
            finished.set()
    monkeypatch.setattr(ScopedChatStore, 'get', blocked)
    services, bridge, sent = setup(qapp, tmp_root)
    try:
        bridge.call('{"id":"import","method":"chat.list"}')
        assert started.wait(3)
        now = time.monotonic()
        bridge.close(); services.close()
        assert time.monotonic() - now < 1
    finally:
        release.set()
        # Let the abandoned worker release the old file before the temp dir is removed.
        finished.wait(5)
        bridge.close(); services.close()
