"""Immutable contracts for Teacher Fast Path v2."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


__all__ = [
    "TEACHER_FASTPATH_SCHEMA_VERSION",
    "TeacherResponseMode",
    "TeacherResponseModeDecision",
    "TeacherResponseModeReason",
    "TeacherResponseModeRequest",
]
