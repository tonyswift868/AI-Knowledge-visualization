from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from ...providers.telemetry import provider_request_context
from .contracts import FreeChatSession, FreeChatTurnResult, utc_now
from .prompt import FreeChatHistoryWindow, build_provider_messages
from .session import FreeChatSessionStore


FREE_CHAT_TASK_TYPE = "free_chat"
FREE_CHAT_PROMPT_VERSION = "free-chat-v1"


class TextProvider(Protocol):
    async def text(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
    ) -> str:
        ...


class FreeChatInputError(ValueError):
    pass


class FreeChatProviderResponseError(RuntimeError):
    pass


class FreeChatEmptyResponseError(FreeChatProviderResponseError):
    pass


class FreeChatService:
    """Independent, single-Provider-call conversational service.

    A turn is committed only after a non-empty Provider reply succeeds. Locks
    are per session, so two sessions can progress concurrently while turns for
    one session are serialized and cannot overwrite each other's transcript.
    """

    def __init__(
        self,
        store: FreeChatSessionStore,
        *,
        history_window: FreeChatHistoryWindow | None = None,
        temperature: float = 0.65,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._history_window = history_window or FreeChatHistoryWindow()
        self._temperature = temperature
        self._now = now
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_locks_guard = asyncio.Lock()

    def create_session(self, *, session_id: str | None = None) -> FreeChatSession:
        return self._store.create(session_id=session_id, created_at=self._now())

    def get_session(self, session_id: str) -> FreeChatSession:
        return self._store.get(session_id)

    async def send_message(
        self,
        session_id: str,
        user_message: str,
        provider: TextProvider,
        *,
        persona_context: str | None = None,
    ) -> FreeChatTurnResult:
        content = str(user_message or "").strip()
        if not content:
            raise FreeChatInputError("Free Chat user message cannot be empty")

        self._store.get(session_id)
        lock = await self._lock_for(session_id)
        async with lock:
            session = self._store.get(session_id)
            provider_messages = build_provider_messages(
                session,
                content,
                persona_context=persona_context,
                history_window=self._history_window,
            )
            with provider_request_context(
                task_type=FREE_CHAT_TASK_TYPE,
                request_id="req_free_chat_" + secrets.token_hex(10),
                prompt_version=FREE_CHAT_PROMPT_VERSION,
            ):
                raw_reply = await provider.text(
                    provider_messages,
                    temperature=self._temperature,
                )

            if not isinstance(raw_reply, str):
                raise FreeChatProviderResponseError(
                    "Free Chat Provider returned a non-text reply"
                )
            if not raw_reply.strip():
                raise FreeChatEmptyResponseError(
                    "Free Chat Provider returned an empty text reply"
                )
            reply = raw_reply.strip()
            updated = self._store.commit_turn(
                session_id,
                expected_version=session.version,
                user_content=content,
                assistant_content=reply,
                committed_at=self._now(),
            )
            return FreeChatTurnResult(session=updated, reply=reply)

    async def _lock_for(self, session_id: str) -> asyncio.Lock:
        async with self._session_locks_guard:
            return self._session_locks.setdefault(session_id, asyncio.Lock())
