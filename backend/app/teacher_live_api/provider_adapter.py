"""ProviderRuntime adapter seam with an explicit offline DEMO_FAKE route."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from ..provider_service import ProviderError
from ..teaching.composer import FollowUpKind
from ..teaching.fastpath_v2 import TeacherPromptEnvelope, TeacherResponseMode
from ..teaching.runtime_v2 import (
    TeacherCachedInputMetadata,
    TeacherCachedInputStatus,
    TeacherResponseChunk,
)
from .demo_fixture import demo_reply
from .models import TeacherLiveError, TeacherLiveProviderMode


def _chunks(text: str, size: int = 28) -> tuple[str, ...]:
    value = str(text or "").strip()
    if not value:
        return ()
    return tuple(value[index : index + size] for index in range(0, len(value), size))


def _latest_message(envelope: TeacherPromptEnvelope) -> str:
    marker = "<LATEST_USER_MESSAGE>"
    end_marker = "</LATEST_USER_MESSAGE>"
    content = envelope.dynamic_tail.content
    start = content.find(marker)
    end = content.find(end_marker)
    if start < 0 or end <= start:
        return ""
    value = content[start + len(marker) : end].strip()
    if value.startswith('"') and value.endswith('"'):
        try:
            import json

            return str(json.loads(value))
        except Exception:
            return value.strip('"')
    return value


def _safe_usage(value: object) -> dict[str, int | None]:
    if not isinstance(value, Mapping):
        return {"input_tokens": None, "output_tokens": None, "cached_input_tokens": None}

    def token(name: str) -> int | None:
        raw = value.get(name)
        return raw if isinstance(raw, int) and raw >= 0 else None

    def first(*names: str) -> int | None:
        for name in names:
            value = token(name)
            if value is not None:
                return value
        return None

    return {
        "input_tokens": first("input_tokens", "prompt_tokens"),
        "output_tokens": first("output_tokens", "completion_tokens"),
        "cached_input_tokens": token("cached_input_tokens"),
    }


class TeacherProviderSelectionPort(Protocol):
    def select(self, provider_session_id: str | None) -> "SelectedTeacherProvider":
        """Resolve a verified real Provider or an explicit offline demo."""


@dataclass(frozen=True, slots=True)
class SelectedTeacherProvider:
    provider: Any
    mode: TeacherLiveProviderMode
    native_provider_streaming: bool


class DemoFakeTeacherProvider:
    provider_name = "mentorflow-teacher-live-demo-fake"
    model = "offline-five-turn-fixture"
    native_provider_streaming = True

    def __init__(self) -> None:
        self.call_count = 0
        self.usage_metadata = {
            "input_tokens": None,
            "output_tokens": None,
            "cached_input_tokens": None,
        }

    @property
    def cached_input_metadata(self) -> TeacherCachedInputMetadata:
        return TeacherCachedInputMetadata(
            metadata_available=True,
            status=TeacherCachedInputStatus.NOT_SUPPORTED,
        )

    async def stream(
        self,
        envelope: TeacherPromptEnvelope,
        *,
        response_mode: TeacherResponseMode,
        temperature: float = 0.2,
    ) -> AsyncIterator[TeacherResponseChunk]:
        self.call_count += 1
        reply = demo_reply(_latest_message(envelope))
        sequence = 0
        for part in _chunks(reply, size=12):
            yield TeacherResponseChunk(sequence_number=sequence, text_delta=part)
            sequence += 1
        yield TeacherResponseChunk(
            sequence_number=sequence,
            is_final=True,
            cited_evidence_ids=envelope.allowed_evidence_ids[:1],
            follow_up_kind=FollowUpKind.NONE,
        )


class ProviderRuntimeTeacherAdapter:
    """Bridge one verified ProviderRuntime handle into the W6 streaming port.

    Adapters may add ``stream_text`` without changing this contract. Existing
    ProviderRuntime implementations expose ``text`` only, so their one real
    call completes before safe local transport chunks are emitted. The receipt
    keeps ``native_provider_streaming`` false in that case.
    """

    provider_name = "provider-runtime-real"

    def __init__(self, provider: Any, *, model: str) -> None:
        self._provider = provider
        self.model = model
        self.call_count = 0
        self.native_provider_streaming = callable(
            getattr(provider, "stream_text", None)
        )
        self.usage_metadata = _safe_usage(None)
        self._cached_input_metadata = TeacherCachedInputMetadata()

    @property
    def cached_input_metadata(self) -> TeacherCachedInputMetadata:
        return self._cached_input_metadata

    def _refresh_safe_metadata(self) -> None:
        self.usage_metadata = _safe_usage(
            getattr(self._provider, "last_usage", None)
        )
        raw_cache = getattr(self._provider, "cached_input_metadata", None)
        try:
            self._cached_input_metadata = (
                TeacherCachedInputMetadata.model_validate(raw_cache)
                if raw_cache is not None
                else TeacherCachedInputMetadata()
            )
        except ValidationError:
            self._cached_input_metadata = TeacherCachedInputMetadata()

    async def _native_parts(
        self,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> AsyncIterator[str]:
        native = getattr(self._provider, "stream_text")
        result = native(messages, temperature=temperature)
        if inspect.isawaitable(result):
            result = await result
        async for raw in result:
            if isinstance(raw, Mapping):
                value = raw.get("text_delta") or raw.get("delta") or raw.get("text")
            else:
                value = raw
            text = str(value or "")
            if text:
                yield text

    async def stream(
        self,
        envelope: TeacherPromptEnvelope,
        *,
        response_mode: TeacherResponseMode,
        temperature: float = 0.2,
    ) -> AsyncIterator[TeacherResponseChunk]:
        self.call_count += 1
        messages = [
            {"role": "system", "content": envelope.stable_prefix.content},
            {"role": "user", "content": envelope.dynamic_tail.content},
        ]
        sequence = 0
        if self.native_provider_streaming:
            async for part in self._native_parts(messages, temperature):
                yield TeacherResponseChunk(sequence_number=sequence, text_delta=part)
                sequence += 1
        else:
            reply = await self._provider.text(messages, temperature=temperature)
            for part in _chunks(reply):
                yield TeacherResponseChunk(sequence_number=sequence, text_delta=part)
                sequence += 1
        self._refresh_safe_metadata()
        yield TeacherResponseChunk(
            sequence_number=sequence,
            is_final=True,
            cited_evidence_ids=envelope.allowed_evidence_ids[:1],
            follow_up_kind=FollowUpKind.NONE,
        )


class ProviderRuntimeTeacherSelector:
    """Read existing process-local ProviderRuntime state without creating quota."""

    def select(self, provider_session_id: str | None) -> SelectedTeacherProvider:
        from ..provider_runtime import (
            latest_verified_provider_session,
            provider_for_session,
            require_provider_session,
        )

        try:
            session = (
                require_provider_session(provider_session_id)
                if provider_session_id
                else latest_verified_provider_session()
            )
        except ProviderError as error:
            raise TeacherLiveError(
                "PROVIDER_UNAVAILABLE",
                "已选择的 Provider 暂时不可用，请先重新验证连接。",
                status=428,
            ) from error
        if session is None or session.provider == "fake":
            provider = DemoFakeTeacherProvider()
            return SelectedTeacherProvider(
                provider=provider,
                mode=TeacherLiveProviderMode.DEMO_FAKE,
                native_provider_streaming=True,
            )
        try:
            adapter = ProviderRuntimeTeacherAdapter(
                provider_for_session(session),
                model=session.model,
            )
        except ProviderError as error:
            raise TeacherLiveError(
                "PROVIDER_UNAVAILABLE",
                "Provider 配置已存在，但当前无法建立连接。",
                status=503,
            ) from error
        return SelectedTeacherProvider(
            provider=adapter,
            mode=TeacherLiveProviderMode.REAL,
            native_provider_streaming=adapter.native_provider_streaming,
        )


__all__ = [
    "DemoFakeTeacherProvider",
    "ProviderRuntimeTeacherAdapter",
    "ProviderRuntimeTeacherSelector",
    "SelectedTeacherProvider",
    "TeacherProviderSelectionPort",
]
