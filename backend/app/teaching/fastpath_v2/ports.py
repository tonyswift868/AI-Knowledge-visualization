"""Streaming-neutral ports for Teacher Fast Path v2."""

from __future__ import annotations

from time import perf_counter
from typing import AsyncIterator, Protocol

from ..runtime_v2 import TeacherResponseChunk
from .models import TeacherPromptEnvelope, TeacherResponseMode


class StreamingTeacherPort(Protocol):
    provider_name: str

    def stream(
        self,
        envelope: TeacherPromptEnvelope,
        *,
        response_mode: TeacherResponseMode,
        temperature: float = 0.2,
    ) -> AsyncIterator[TeacherResponseChunk]:
        """Start exactly one streamed Provider response."""


class TeacherChunkObserverPort(Protocol):
    async def on_chunk(self, chunk: TeacherResponseChunk) -> None:
        """Receive each validated chunk for endpoint-specific forwarding."""


class PerformanceClockPort(Protocol):
    def now_ms(self) -> float:
        """Return a monotonic millisecond reading."""


class SystemPerformanceClock:
    def now_ms(self) -> float:
        return perf_counter() * 1_000


__all__ = [
    "PerformanceClockPort",
    "StreamingTeacherPort",
    "SystemPerformanceClock",
    "TeacherChunkObserverPort",
]
