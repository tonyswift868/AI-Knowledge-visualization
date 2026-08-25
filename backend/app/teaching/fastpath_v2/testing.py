"""Deterministic offline streaming fixtures; never a real Provider adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..composer import FollowUpKind
from ..runtime_v2 import TeacherResponseChunk
from .models import TeacherPromptEnvelope, TeacherResponseMode


class FakeStreamingTeacherProvider:
    provider_name = "offline-fake-streaming-teacher"
    model = "fixture-stream-model"

    def __init__(
        self,
        *,
        chunks: tuple[TeacherResponseChunk | object, ...] | None = None,
        error: Exception | None = None,
        first_chunk_delay_seconds: float = 0.0,
    ) -> None:
        self._chunks = chunks
        self._error = error
        self._first_chunk_delay_seconds = first_chunk_delay_seconds
        self.call_count = 0
        self.envelopes: list[TeacherPromptEnvelope] = []
        self.response_modes: list[TeacherResponseMode] = []

    async def stream(
        self,
        envelope: TeacherPromptEnvelope,
        *,
        response_mode: TeacherResponseMode,
        temperature: float = 0.2,
    ) -> AsyncIterator[TeacherResponseChunk]:
        self.call_count += 1
        self.envelopes.append(envelope)
        self.response_modes.append(response_mode)
        if self._first_chunk_delay_seconds:
            await asyncio.sleep(self._first_chunk_delay_seconds)
        if self._error is not None:
            raise self._error
        chunks = self._chunks or (
            TeacherResponseChunk(
                sequence_number=0,
                text_delta="threshold 是扩容触发线，",
            ),
            TeacherResponseChunk(
                sequence_number=1,
                text_delta="它由 capacity 与 load factor 共同决定。",
                is_final=True,
                cited_evidence_ids=envelope.allowed_evidence_ids[:1],
                follow_up_kind=FollowUpKind.NONE,
            ),
        )
        for chunk in chunks:
            yield chunk  # type: ignore[misc]


class RecordingTeacherChunkObserver:
    def __init__(self) -> None:
        self.chunks: list[TeacherResponseChunk] = []

    async def on_chunk(self, chunk: TeacherResponseChunk) -> None:
        self.chunks.append(chunk)


__all__ = ["FakeStreamingTeacherProvider", "RecordingTeacherChunkObserver"]
