from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .contracts import (
    FreeChatMessage,
    FreeChatRole,
    FreeChatSession,
    FreeChatTurnResult,
)


@dataclass(frozen=True, slots=True)
class CreateFreeChatSessionDTO:
    session_id: str | None = None
    persona_context: str | None = None


@dataclass(frozen=True, slots=True)
class SendFreeChatMessageDTO:
    message: str


@dataclass(frozen=True, slots=True)
class FreeChatMessageDTO:
    role: FreeChatRole
    content: str
    timestamp: str
    sequence: int

    @classmethod
    def from_domain(cls, message: FreeChatMessage) -> "FreeChatMessageDTO":
        return cls(
            role=message.role,
            content=message.content,
            timestamp=message.timestamp.isoformat(),
            sequence=message.sequence,
        )


@dataclass(frozen=True, slots=True)
class FreeChatSessionDTO:
    session_id: str
    messages: tuple[FreeChatMessageDTO, ...]
    created_at: str
    updated_at: str
    version: int
    persona_context: str | None

    @classmethod
    def from_domain(cls, session: FreeChatSession) -> "FreeChatSessionDTO":
        return cls(
            session_id=session.session_id,
            messages=tuple(
                FreeChatMessageDTO.from_domain(message)
                for message in session.messages
            ),
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            version=session.version,
            persona_context=session.persona_context,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FreeChatTurnDTO:
    session: FreeChatSessionDTO
    reply: str

    @classmethod
    def from_domain(cls, result: FreeChatTurnResult) -> "FreeChatTurnDTO":
        return cls(
            session=FreeChatSessionDTO.from_domain(result.session),
            reply=result.reply,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
