from __future__ import annotations

from .dto import (
    CreateFreeChatSessionDTO,
    FreeChatSessionDTO,
    FreeChatTurnDTO,
    SendFreeChatMessageDTO,
)
from .prompt import FreeChatHistoryWindow
from .service import FreeChatService, TextProvider
from .session import FreeChatSessionStore


class FreeChatRuntime:
    """Endpoint-neutral application facade over Free Chat service and storage."""

    def __init__(
        self,
        store: FreeChatSessionStore,
        *,
        history_window: FreeChatHistoryWindow | None = None,
    ) -> None:
        self._service = FreeChatService(store, history_window=history_window)

    def create_session(
        self,
        request: CreateFreeChatSessionDTO | None = None,
    ) -> FreeChatSessionDTO:
        request = request or CreateFreeChatSessionDTO()
        return FreeChatSessionDTO.from_domain(
            self._service.create_session(
                session_id=request.session_id,
                persona_context=request.persona_context,
            )
        )

    def get_session(self, session_id: str) -> FreeChatSessionDTO:
        return FreeChatSessionDTO.from_domain(self._service.get_session(session_id))

    def list_sessions(self) -> tuple[FreeChatSessionDTO, ...]:
        return tuple(
            FreeChatSessionDTO.from_domain(session)
            for session in self._service.list_sessions()
        )

    def delete_session(self, session_id: str) -> bool:
        return self._service.delete_session(session_id)

    async def send_message(
        self,
        session_id: str,
        request: SendFreeChatMessageDTO,
        provider: TextProvider,
    ) -> FreeChatTurnDTO:
        return FreeChatTurnDTO.from_domain(
            await self._service.send_message(
                session_id,
                request.message,
                provider,
            )
        )
