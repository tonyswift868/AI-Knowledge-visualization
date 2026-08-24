from __future__ import annotations

import secrets
import threading
from datetime import datetime
from typing import Protocol

from .contracts import FreeChatMessage, FreeChatSession


class FreeChatSessionNotFoundError(LookupError):
    pass


class FreeChatSessionConflictError(RuntimeError):
    pass


class FreeChatSessionStore(Protocol):
    """Replaceable persistence boundary for independent Free Chat sessions."""

    def create(self, *, session_id: str | None, created_at: datetime) -> FreeChatSession:
        ...

    def get(self, session_id: str) -> FreeChatSession:
        ...

    def commit_turn(
        self,
        session_id: str,
        *,
        expected_version: int,
        user_content: str,
        assistant_content: str,
        committed_at: datetime,
    ) -> FreeChatSession:
        ...


class InMemoryFreeChatSessionStore:
    """Thread-safe test/foundation adapter with optimistic turn commits."""

    def __init__(self) -> None:
        self._sessions: dict[str, FreeChatSession] = {}
        self._guard = threading.RLock()

    def create(self, *, session_id: str | None, created_at: datetime) -> FreeChatSession:
        requested_id = str(session_id or "").strip()
        with self._guard:
            candidate = requested_id or self._new_session_id()
            while not requested_id and candidate in self._sessions:
                candidate = self._new_session_id()
            if candidate in self._sessions:
                raise FreeChatSessionConflictError(
                    f"Free Chat session already exists: {candidate}"
                )
            session = FreeChatSession(
                session_id=candidate,
                messages=(),
                created_at=created_at,
                updated_at=created_at,
            )
            self._sessions[candidate] = session
            return session

    def get(self, session_id: str) -> FreeChatSession:
        with self._guard:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise FreeChatSessionNotFoundError(
                    f"Free Chat session not found: {session_id}"
                ) from exc

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

            first_sequence = len(current.messages) + 1
            updated = FreeChatSession(
                session_id=current.session_id,
                messages=current.messages
                + (
                    FreeChatMessage(
                        role="user",
                        content=user_content,
                        timestamp=committed_at,
                        sequence=first_sequence,
                    ),
                    FreeChatMessage(
                        role="assistant",
                        content=assistant_content,
                        timestamp=committed_at,
                        sequence=first_sequence + 1,
                    ),
                ),
                created_at=current.created_at,
                updated_at=committed_at,
                version=current.version + 1,
            )
            self._sessions[session_id] = updated
            return updated

    @staticmethod
    def _new_session_id() -> str:
        return "fc_" + secrets.token_urlsafe(12)
