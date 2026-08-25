"""Public, UI-safe contracts for the standalone Teacher Live API."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _LiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeacherLiveProviderMode(str, Enum):
    REAL = "REAL"
    DEMO_FAKE = "DEMO_FAKE"


class TeacherLiveSessionState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ABORTED = "aborted"


class TeacherLiveEventType(str, Enum):
    TURN_STARTED = "turn_started"
    RESPONSE_CHUNK = "response_chunk"
    CITATION = "citation"
    METRICS = "metrics"
    COMPLETED = "completed"
    ERROR = "error"


class TeacherLiveEvidenceInput(_LiveModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=30_000)
    source_reference: str | None = Field(default=None, max_length=2_000)


class TeacherLiveCreateSessionRequest(_LiveModel):
    current_concept: str = Field(default="HashMap threshold", min_length=1, max_length=240)
    learning_goal: str = Field(
        default="理解 threshold、capacity 与扩容之间的关系",
        max_length=500,
    )
    learner_level: str = Field(default="demo", max_length=160)
    provider_session_id: str | None = Field(default=None, max_length=240)
    evidence: tuple[TeacherLiveEvidenceInput, ...] | None = Field(
        default=None,
        max_length=16,
    )


class TeacherLiveSessionReceipt(_LiveModel):
    session_id: str = Field(min_length=1, max_length=160)
    state: TeacherLiveSessionState
    provider_mode: TeacherLiveProviderMode
    provider_label: Literal["REAL", "DEMO_FAKE"]
    native_provider_streaming: bool
    current_concept: str = Field(min_length=1, max_length=240)
    next_sequence_number: int = Field(ge=1)
    demo_fixture: bool


class TeacherLiveTurnSubmitRequest(_LiveModel):
    message: str = Field(min_length=1, max_length=20_000)
    turn_id: str | None = Field(default=None, max_length=160)
    idempotency_key: str | None = Field(default=None, max_length=240)


class TeacherLiveTurnReceipt(_LiveModel):
    session_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=240)
    stream_path: str = Field(min_length=1, max_length=500)
    provider_mode: TeacherLiveProviderMode
    duplicate: bool
    state: Literal["queued", "streaming", "completed", "error", "cancelled"]


class TeacherLiveCitation(_LiveModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    source_reference: str | None = Field(default=None, max_length=2_000)
    excerpt: str | None = Field(default=None, max_length=500)


class TeacherLiveMetrics(_LiveModel):
    response_mode: Literal["fast", "deep"]
    ttft_ms: float | None = Field(default=None, ge=0.0)
    total_response_ms: float = Field(ge=0.0)
    provider_call_count: int = Field(ge=0, le=1)
    native_provider_streaming: bool
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_status: Literal["unknown", "hit", "miss", "not_supported"] = "unknown"


class TeacherLiveEvent(_LiveModel):
    sequence: int = Field(ge=1)
    event: TeacherLiveEventType
    session_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    provider_mode: TeacherLiveProviderMode
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_internal_payloads(self) -> "TeacherLiveEvent":
        forbidden = {"prompt", "api_key", "credential", "secret", "raw_key"}
        keys = {str(key).casefold() for key in self.data}
        if keys & forbidden:
            raise ValueError("live events cannot expose internal prompt or credentials")
        return self


class TeacherLiveError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = status


__all__ = [
    "TeacherLiveCitation",
    "TeacherLiveCreateSessionRequest",
    "TeacherLiveError",
    "TeacherLiveEvent",
    "TeacherLiveEventType",
    "TeacherLiveEvidenceInput",
    "TeacherLiveMetrics",
    "TeacherLiveProviderMode",
    "TeacherLiveSessionReceipt",
    "TeacherLiveSessionState",
    "TeacherLiveTurnReceipt",
    "TeacherLiveTurnSubmitRequest",
]
