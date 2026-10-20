"""Opt-in, read-only source backup verifies a real account's v10 upgrade.

CHAT175_SOURCE_DB selects the source; ordinary suite runs never access user data.
Only aggregate counts and integrity flags enter the optional result JSON.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from refactor.learning.app.chat_store import CourseChatStore
from refactor.learning.course.store import CourseStore


def fingerprint(db, table):
    rows = db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def test_chat175_user_copy_migration_preserves_every_message(tmp_path):
    source = os.environ.get('CHAT175_SOURCE_DB')
    if not source:
        pytest.skip('opt-in user database backup; source is never opened by the ordinary suite')
    path = Path(source).resolve()
    # The backup is the only database subsequently opened with write access.
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as original:
        account = original.execute("SELECT value FROM _meta WHERE key='account_id'").fetchone()[0]
        destination = tmp_path / 'learning' / 'accounts' / account
        destination.mkdir(parents=True)
        with sqlite3.connect(destination / 'course.sqlite3') as copy:
            original.backup(copy)
    with sqlite3.connect(destination / 'course.sqlite3') as before:
        version = before.execute('SELECT version FROM schema_version').fetchone()[0]
        assert version == 10
        counts = {table: before.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                  for table in ('chat_sessions', 'chat_messages')}
        messages_hash = fingerprint(before, 'chat_messages')
        message_records = before.execute("SELECT * FROM _records WHERE entity='chat_messages' ORDER BY entity_id").fetchall()
        sessions = {sid: json.loads(payload) for sid, payload in before.execute('SELECT id,payload FROM chat_sessions')}
        journal = before.execute("SELECT entity_id,local_revision,server_revision,pending FROM _records WHERE entity='chat_sessions' ORDER BY entity_id").fetchall()
    with CourseStore(account, tmp_path) as upgraded:
        assert upgraded._db.execute('SELECT version FROM schema_version').fetchone()[0] == 11
        assert {table: upgraded._db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                for table in counts} == counts
        # sqlite Row objects are converted back for a byte-for-byte table hash.
        upgraded._db.row_factory = None
        assert fingerprint(upgraded._db, 'chat_messages') == messages_hash
        assert upgraded._db.execute("SELECT * FROM _records WHERE entity='chat_messages' ORDER BY entity_id").fetchall() == message_records
        assert upgraded._db.execute("SELECT entity_id,local_revision,server_revision,pending FROM _records WHERE entity='chat_sessions' ORDER BY entity_id").fetchall() == journal
        upgraded._db.row_factory = sqlite3.Row
        adapter = CourseChatStore(upgraded)
        for sid, payload in sessions.items():
            current = upgraded.get_chat_session(sid)
            assert current.pop('archived') is False
            assert current == payload
            adapter.get(sid)  # Old conversations remain openable, without printing content.
    result = dict(source_schema=version, target_schema=11, before=counts, after=counts,
                  messages_identical=True, message_journal_identical=True,
                  session_revisions_and_pending_identical=True, all_sessions_openable=True)
    output = os.environ.get('CHAT175_MIGRATION_RESULT')
    if output:
        Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
