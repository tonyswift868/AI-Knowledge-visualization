from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FreeChatChunk:
    session_id: str
    content: str
    sequence: int
    final: bool = False

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("Free Chat chunk session_id cannot be empty")
        if self.sequence < 0:
            raise ValueError("Free Chat chunk sequence cannot be negative")
        if not self.content and not self.final:
            raise ValueError("Non-final Free Chat chunks require content")


class StreamResult(Protocol):
    """Transport-neutral shape for a future streaming Provider integration."""

    def __aiter__(self) -> AsyncIterator[FreeChatChunk]:
        ...
