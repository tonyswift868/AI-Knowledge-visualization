from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import FreeChatMessage, FreeChatSession
from .session import (
    FreeChatSessionConflictError,
    FreeChatSessionNotFoundError,
    _committed_turn,
    _normalized_persona_context,
    _validated_session_id,
)


FREE_CHAT_SESSION_SCHEMA = "free_chat_session.v1"
MAX_SESSION_FILE_BYTES = 8 * 1024 * 1024


class FreeChatStoreError(RuntimeError):
    pass


class FreeChatStoreCorruptionError(FreeChatStoreError):
    pass


class FreeChatStoreIOError(FreeChatStoreError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class AtomicJsonFreeChatStore:
    """Durable session adapter rooted only at an injected absolute directory."""

    def __init__(self, root: str | Path) -> None:
        supplied = Path(root)
        if not supplied.is_absolute():
            raise ValueError("AtomicJsonFreeChatStore root must be absolute")
        self._root = supplied.resolve(strict=False)
        self._sessions_root = self._root / "sessions"
        self._guard = threading.RLock()

    @property
    def root(self) -> Path:
        return self._root

    def create(
        self,
        *,
        session_id: str | None,
        created_at: datetime,
        persona_context: str | None = None,
    ) -> FreeChatSession:
        requested_id = str(session_id or "").strip()
        with self._guard:
            candidate = (
                _validated_session_id(requested_id)
                if requested_id
                else self._new_session_id()
            )
            while not requested_id and self._path(candidate).exists():
                candidate = self._new_session_id()
            if self._path(candidate).exists():
                raise FreeChatSessionConflictError(
                    f"Free Chat session already exists: {candidate}"
                )
            session = FreeChatSession(
                session_id=candidate,
                messages=(),
                created_at=created_at,
                updated_at=created_at,
                persona_context=_normalized_persona_context(persona_context),
            )
            self._atomic_write(session)
            return session

    def get(self, session_id: str) -> FreeChatSession:
        session_id = _validated_session_id(session_id)
        with self._guard:
            path = self._path(session_id)
            if not path.is_file():
                raise FreeChatSessionNotFoundError(
                    f"Free Chat session not found: {session_id}"
                )
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise FreeChatStoreIOError(
                    f"Unable to read Free Chat session: {session_id}"
                ) from exc
            try:
                if len(raw) > MAX_SESSION_FILE_BYTES:
                    raise ValueError("session file is too large")
                payload = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_reject_duplicate_keys,
                )
                return self._decode(payload, expected_session_id=session_id)
            except Exception as exc:
                raise FreeChatStoreCorruptionError(
                    f"Free Chat session is corrupt: {session_id}"
                ) from exc

    def list_sessions(self) -> tuple[FreeChatSession, ...]:
        with self._guard:
            if not self._sessions_root.is_dir():
                return ()
            sessions = [
                self.get(path.stem)
                for path in self._sessions_root.glob("*.json")
            ]
            return tuple(
                sorted(
                    sessions,
                    key=lambda item: (item.updated_at, item.session_id),
                    reverse=True,
                )
            )

    def delete(self, session_id: str) -> bool:
        session_id = _validated_session_id(session_id)
        with self._guard:
            path = self._path(session_id)
            if not path.exists():
                return False
            try:
                path.unlink()
            except OSError as exc:
                raise FreeChatStoreIOError(
                    f"Unable to delete Free Chat session: {session_id}"
                ) from exc
            return True

    def commit_turn(
        self,
        session_id: str,
        *,
        expected_version: int,
        user_content: str,
        assistant_content: str,
        committed_at: datetime,
    ) -> FreeChatSession:
        with self._guard:
            current = self.get(session_id)
            if current.version != expected_version:
                raise FreeChatSessionConflictError(
                    f"Free Chat session version changed: {session_id}"
                )
            updated = _committed_turn(
                current,
                user_content=user_content,
                assistant_content=assistant_content,
                committed_at=committed_at,
            )
            self._atomic_write(updated)
            return updated

    def _path(self, session_id: str) -> Path:
        return self._sessions_root / f"{_validated_session_id(session_id)}.json"

    def _atomic_write(self, session: FreeChatSession) -> None:
        target = self._path(session.session_id)
        temporary = self._sessions_root / (
            f".{session.session_id}.{secrets.token_hex(8)}.tmp"
        )
        body = json.dumps(
            self._encode(session),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        try:
            self._sessions_root.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            raise FreeChatStoreIOError(
                f"Unable to persist Free Chat session: {session.session_id}"
            ) from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _encode(session: FreeChatSession) -> dict[str, Any]:
        return {
            "schema": FREE_CHAT_SESSION_SCHEMA,
            "session_id": session.session_id,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "version": session.version,
            "persona_context": session.persona_context,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "timestamp": message.timestamp.isoformat(),
                    "sequence": message.sequence,
                }
                for message in session.messages
            ],
        }

    @staticmethod
    def _decode(payload: object, *, expected_session_id: str) -> FreeChatSession:
        if not isinstance(payload, dict) or payload.get("schema") != FREE_CHAT_SESSION_SCHEMA:
            raise ValueError("invalid Free Chat session schema")
        if payload.get("session_id") != expected_session_id:
            raise ValueError("Free Chat session id does not match its file")
        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list):
            raise ValueError("Free Chat messages must be a list")
        if type(payload.get("version")) is not int:
            raise ValueError("Free Chat version must be an integer")
        if not isinstance(payload.get("created_at"), str) or not isinstance(
            payload.get("updated_at"), str
        ):
            raise ValueError("Free Chat timestamps must be strings")
        persona = payload.get("persona_context")
        if persona is not None and not isinstance(persona, str):
            raise ValueError("Free Chat persona_context must be text or null")
        for item in raw_messages:
            if not isinstance(item, dict):
                raise ValueError("Free Chat message entry must be an object")
            if item.get("role") not in {"user", "assistant"}:
                raise ValueError("Free Chat message role is invalid")
            if not isinstance(item.get("content"), str) or not isinstance(
                item.get("timestamp"), str
            ):
                raise ValueError("Free Chat message text fields are invalid")
            if type(item.get("sequence")) is not int:
                raise ValueError("Free Chat message sequence must be an integer")
        messages = tuple(
            FreeChatMessage(
                role=item["role"],
                content=item["content"],
                timestamp=datetime.fromisoformat(item["timestamp"]),
                sequence=item["sequence"],
            )
            for item in raw_messages
        )
        return FreeChatSession(
            session_id=expected_session_id,
            messages=messages,
            created_at=datetime.fromisoformat(payload["created_at"]),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
            version=payload["version"],
            persona_context=persona,
        )

    @staticmethod
    def _new_session_id() -> str:
        return "fc_" + secrets.token_urlsafe(12)
