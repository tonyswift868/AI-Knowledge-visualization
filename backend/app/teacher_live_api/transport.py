"""Server-sent event transport for UI-safe Teacher Live events."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from .models import TeacherLiveEvent


def encode_sse(event: TeacherLiveEvent) -> str:
    payload = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"id: {event.sequence}\nevent: {event.event.value}\ndata: {payload}\n\n"


async def stream_sse(events: AsyncIterator[TeacherLiveEvent]) -> AsyncIterator[str]:
    async for event in events:
        yield encode_sse(event)


__all__ = ["encode_sse", "stream_sse"]
