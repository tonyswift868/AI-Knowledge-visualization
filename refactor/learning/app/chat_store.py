"""Free-chat snapshots over the account's CourseStore and its existing outbox.

The legacy reader is used only for a one-time, read-only JSON import. Neither
persona settings nor provider request history are stored as extra chat messages.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from threading import Lock
import uuid

from ..connections.errors import ModelCancelled
from ..course.store import StoreError
from ..tools.free_chat.contracts import FreeChatMessage, FreeChatSession
from ..tools.free_chat.session import FreeChatSessionConflictError, FreeChatSessionNotFoundError
from ..tools.free_chat.persistence import MAX_SESSION_FILE_BYTES, FreeChatStoreError
from ..tools.service import ScopedChatStore
from ..tools.storage import ToolError

log = logging.getLogger(__name__)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def title_from(text):
    text = ' '.join(text.split())
    return text[:28] + '…' if len(text) > 28 else text or '新对话'


@dataclass(frozen=True, slots=True)
class ChatSession(FreeChatSession):
    title: str = '新对话'


class CourseChatStore:
    def __init__(self, store):
        self.store = store
        self._import_lock = Lock()
        self._import_ready = False

    def _ensure_import(self):
        # AppServices constructs this adapter under its short ownership lock.
        # Read old files only from a chat handler's worker, so close/rebind does
        # not have to wait for filesystem I/O while holding the GUI thread.
        with self._import_lock:
            if not self._import_ready:
                self._import_legacy()
                self._import_ready = True

    def _snapshot(self, session_id):
        try:
            row = self.store.get_chat_session(session_id)
        except StoreError as exc:
            if exc.code == 'NOT_FOUND':
                raise FreeChatSessionNotFoundError(session_id) from None
            raise
        rows = self.store.chat_messages(session_id)
        # Content identity detects remote edits/deletes too, without adding a
        # second revision authority or modifying the RecordStore wire contract.
        version = int.from_bytes(hashlib.sha256(json.dumps([row, rows], sort_keys=True,
                                ensure_ascii=False, separators=(',', ':')).encode()).digest(), 'big')
        messages = tuple(FreeChatMessage(m['role'], m['content'], datetime.fromisoformat(m['at']), i)
                         for i, m in enumerate(rows, 1))
        created = datetime.fromisoformat(row['created_at']) if row.get('created_at') else (
            messages[0].timestamp if messages else EPOCH)
        updated = max([created, *(m.timestamp for m in messages),
                       datetime.fromisoformat(row['updated_at']) if row.get('updated_at') else created])
        return ChatSession(session_id, messages, created, updated, version,
                           title=row.get('title') or '新对话')

    def get(self, session_id):
        self._ensure_import()
        with self.store.transaction():
            return self._snapshot(session_id)

    def list_sessions(self):
        self._ensure_import()
        with self.store.transaction():
            return tuple(sorted((self._snapshot(row['id']) for row in self.store.records('chat_sessions')),
                                key=lambda s: (s.updated_at, s.session_id), reverse=True))

    def create(self, *, session_id, created_at):
        self._ensure_import()
        with self.store.transaction():
            sid = self.store.create_chat_session('新对话', session_id=session_id, created_at=created_at)
            return self._snapshot(sid)

    def delete(self, session_id):
        self._ensure_import()
        with self.store.transaction():
            try:
                self.store.delete_chat_session(session_id)
            except StoreError as exc:
                if exc.code == 'NOT_FOUND': return False
                raise
            return True

    def commit_turn(self, session_id, *, expected_version, user_content, assistant_content, committed_at, cancel=None):
        self._ensure_import()
        FreeChatMessage('user', user_content, committed_at, 1)
        with self.store.transaction():
            current = self._snapshot(session_id)
            if current.version != expected_version:
                raise FreeChatSessionConflictError(session_id)
            if cancel is not None and cancel.is_set(): raise ModelCancelled()
            # Canonical UTC timestamps increase per turn even for a frozen or
            # backwards clock. Both messages share one sortable turn ID; its
            # role suffix guarantees user then assistant on every device.
            at = max(committed_at.astimezone(timezone.utc), current.updated_at.astimezone(timezone.utc),
                     max((m.timestamp.astimezone(timezone.utc) + timedelta(microseconds=1)
                          for m in current.messages), default=EPOCH))
            turn = 'turn-' + uuid.uuid4().hex
            for index, (role, content) in enumerate((('user', user_content), ('assistant', assistant_content))):
                FreeChatMessage(role, content, at, len(current.messages) + index + 1)
                self.store.append_chat_message(session_id, role, content, message_id=f'{turn}-{index}', at=at)
            title = current.title if current.messages else title_from(user_content)
            self.store.touch_chat_session(session_id, title=title, updated_at=at)
            if cancel is not None and cancel.is_set(): raise ModelCancelled()
            return self._snapshot(session_id)

    def _import_legacy(self):
        if self.store.legacy_chat_imported(): return
        directory = self.store.root / 'tools' / 'chat' / 'sessions'
        # Do not follow a junction/symlink into another account's old files.
        for path in (directory.parent.parent, directory.parent, directory):
            self.store._safe_path(path)
        reader = ScopedChatStore(directory.parent)
        for path in sorted(directory.glob('*.json')):
            try:
                self.store._safe_path(path)
                if path.stat().st_size > MAX_SESSION_FILE_BYTES:
                    raise ValueError('oversized legacy session')
                source_key = hashlib.sha256(path.stem.encode('utf-8')).hexdigest()
                if self.store.legacy_chat_imported(source_key): continue
                old = reader.get(path.stem)
                sid = 'legacy-' + source_key
                rows, previous = [], old.created_at.astimezone(timezone.utc)
                for message in old.messages:
                    at = max(message.timestamp.astimezone(timezone.utc), previous)
                    rows.append(dict(id=f'{sid}-{message.sequence:010d}', session_id=sid, role=message.role,
                                     content=message.content, kind='text', at=at.isoformat(),
                                     compressed=False, summary_of=[]))
                    previous = at + timedelta(microseconds=1)
                updated = max(old.updated_at.astimezone(timezone.utc),
                              datetime.fromisoformat(rows[-1]['at']) if rows else previous)
                title = title_from(next((m.content for m in old.messages if m.role == 'user'), ''))
                self.store.import_chat_session(source_key, dict(id=sid, title=title,
                    created_at=old.created_at.astimezone(timezone.utc).isoformat(), updated_at=updated.isoformat()), rows)
            except (OSError, ValueError, OverflowError, FreeChatStoreError, ToolError) as exc:
                if isinstance(exc, StoreError) and exc.code == 'STORE_CLOSED': raise
                # No chat text, persona, tokens, or filesystem contents in logs.
                log.warning('legacy chat import skipped %s (%s); original retained', path.name, type(exc).__name__)
        self.store.mark_legacy_chat_imported()
