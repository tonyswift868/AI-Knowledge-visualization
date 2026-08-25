"""Immutable contracts for Teacher Fast Path v2."""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import StudentMove


TEACHER_FASTPATH_SCHEMA_VERSION = "teacher-fastpath.v2"


class _FastPathModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeacherResponseMode(str, Enum):
    FAST = "fast"
    DEEP = "deep"


class TeacherResponseModeReason(str, Enum):
    EVIDENCE_CONFLICT = "evidence_conflict"
    MULTI_SOURCE_REASONING = "multi_source_reasoning"
    COMPLEX_PROOF_OR_DERIVATION = "complex_proof_or_derivation"
    EXPLICIT_DEEP_REASONING = "explicit_deep_reasoning"
    FAST_STUDENT_MOVE = "fast_student_move"
    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    SIMPLE_CLARIFICATION = "simple_clarification"
    CURRENT_CONCEPT_FOLLOW_UP = "current_concept_follow_up"
    DEFAULT_FAST = "default_fast"


class TeacherResponseModeRequest(_FastPathModel):
    latest_user_message: str = Field(min_length=1, max_length=20_000)
    student_move: StudentMove
    evidence_count: int = Field(default=0, ge=0, le=32)
    evidence_source_count: int = Field(default=0, ge=0, le=32)
    evidence_sufficient: bool = False
    evidence_conflict: bool = False
    requires_multi_source_reasoning: bool = False
    requires_proof_or_derivation: bool = False
    explicit_deep_reasoning: bool = False
    simple_clarification: bool = False
    current_concept_follow_up: bool = False
    context_item_count: int = Field(default=0, ge=0, le=10_000)


class TeacherResponseModeDecision(_FastPathModel):
    schema_version: Literal["teacher-fastpath.v2"] = TEACHER_FASTPATH_SCHEMA_VERSION
    mode: TeacherResponseMode
    reason: TeacherResponseModeReason
    routing_provider_call_count: Literal[0] = 0


class StableTeacherPrefix(_FastPathModel):
    content: str = Field(min_length=1, max_length=100_000)
    stable_prefix_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hash(self) -> "StableTeacherPrefix":
        expected = "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.stable_prefix_hash != expected:
            raise ValueError("stable prefix hash mismatch")
        return self


class DynamicTeacherTail(_FastPathModel):
    content: str = Field(min_length=1, max_length=500_000)
    dynamic_tail_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hash(self) -> "DynamicTeacherTail":
        expected = "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.dynamic_tail_hash != expected:
            raise ValueError("dynamic tail hash mismatch")
        return self


class PromptIdentity(_FastPathModel):
    stable_prefix_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dynamic_tail_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prompt_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> "PromptIdentity":
        payload = f"{self.stable_prefix_hash}\n{self.dynamic_tail_hash}"
        expected = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if self.prompt_identity != expected:
            raise ValueError("prompt identity mismatch")
        return self


class TeacherPromptEnvelope(_FastPathModel):
    schema_version: Literal["teacher-fastpath.v2"] = TEACHER_FASTPATH_SCHEMA_VERSION
    stable_prefix: StableTeacherPrefix
    dynamic_tail: DynamicTeacherTail
    identity: PromptIdentity
    allowed_evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_nested_identity(self) -> "TeacherPromptEnvelope":
        if (
            self.identity.stable_prefix_hash
            != self.stable_prefix.stable_prefix_hash
            or self.identity.dynamic_tail_hash != self.dynamic_tail.dynamic_tail_hash
        ):
            raise ValueError("prompt envelope identity mismatch")
        if len(self.allowed_evidence_ids) != len(set(self.allowed_evidence_ids)):
            raise ValueError("allowed evidence IDs must be unique")
        return self

    def provider_messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.stable_prefix.content},
            {"role": "user", "content": self.dynamic_tail.content},
        ]


__all__ = [
    "TEACHER_FASTPATH_SCHEMA_VERSION",
    "DynamicTeacherTail",
    "PromptIdentity",
    "StableTeacherPrefix",
    "TeacherPromptEnvelope",
    "TeacherResponseMode",
    "TeacherResponseModeDecision",
    "TeacherResponseModeReason",
    "TeacherResponseModeRequest",
]
