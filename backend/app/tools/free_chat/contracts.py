from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


FreeChatRole = Literal["user", "assistant"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class FreeChatMessage:
    role: FreeChatRole
    content: str
    timestamp: datetime
    sequence: int

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"}:
            raise ValueError("Free Chat messages accept only user and assistant roles")
        if not self.content.strip():
            raise ValueError("Free Chat message content cannot be empty")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Free Chat timestamps must be timezone-aware")
        if self.sequence < 1:
            raise ValueError("Free Chat message sequence must be positive")


@dataclass(frozen=True, slots=True)
class FreeChatSession:
    session_id: str
    messages: tuple[FreeChatMessage, ...]
    created_at: datetime
    updated_at: datetime
    version: int = 0

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("Free Chat session_id cannot be empty")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("Free Chat created_at must be timezone-aware")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("Free Chat updated_at must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("Free Chat updated_at cannot precede created_at")
        if self.version < 0:
            raise ValueError("Free Chat version cannot be negative")
        expected_sequences = tuple(range(1, len(self.messages) + 1))
        if tuple(message.sequence for message in self.messages) != expected_sequences:
            raise ValueError("Free Chat message sequences must be contiguous")


@dataclass(frozen=True, slots=True)
class FreeChatTurnResult:
    session: FreeChatSession
    reply: str
