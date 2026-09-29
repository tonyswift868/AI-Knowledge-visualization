from __future__ import annotations

import re
import secrets
import threading
from datetime import datetime
from typing import Protocol

from refactor.learning.chat.contracts import FreeChatMessage
from refactor.learning.chat.contracts import FreeChatSession


class FreeChatSessionNotFoundError(LookupError):
    pass


class FreeChatSessionConflictError(RuntimeError):
    pass


_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _validated_session_id(value: object) -> str:
    session_id = str(value or "").strip()
    if not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("Free Chat session_id has an invalid format")
    return session_id


def _normalized_persona_context(value: object) -> str | None:
    persona = str(value or "").strip()
    return persona or None


def _committed_turn(
    current: FreeChatSession,
    *,
    user_content: str,
    assistant_content: str,
    committed_at: datetime,
) -> FreeChatSession:
    first_sequence = len(current.messages) + 1
    return FreeChatSession(
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
        persona_context=current.persona_context,
    )


class FreeChatSessionStore(Protocol):
    """Replaceable persistence boundary for independent Free Chat sessions."""

    def create(
        self,
        *,
        session_id: str | None,
        created_at: datetime,
        persona_context: str | None = None,
    ) -> FreeChatSession:
        ...

    def get(self, session_id: str) -> FreeChatSession:
        ...

    def list_sessions(self) -> tuple[FreeChatSession, ...]:
        ...

    def delete(self, session_id: str) -> bool:
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
                persona_context=_normalized_persona_context(persona_context),
            )
            self._sessions[candidate] = session
            return session

    def get(self, session_id: str) -> FreeChatSession:
        session_id = _validated_session_id(session_id)
        with self._guard:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise FreeChatSessionNotFoundError(
                    f"Free Chat session not found: {session_id}"
                ) from exc

    def list_sessions(self) -> tuple[FreeChatSession, ...]:
        with self._guard:
            return tuple(
                sorted(
                    self._sessions.values(),
                    key=lambda item: (item.updated_at, item.session_id),
                    reverse=True,
                )
            )

    def delete(self, session_id: str) -> bool:
        session_id = _validated_session_id(session_id)
        with self._guard:
            return self._sessions.pop(session_id, None) is not None

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
            self._sessions[session_id] = updated
            return updated

    @staticmethod
    def _new_session_id() -> str:
        return "fc_" + secrets.token_urlsafe(12)
