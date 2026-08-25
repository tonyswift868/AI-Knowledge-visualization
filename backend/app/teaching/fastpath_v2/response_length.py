"""Deterministic response-length policy applied before the Provider call."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import StudentMove
from .models import TeacherResponseMode


class _LengthModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeacherResponseLengthReason(str, Enum):
    FAST_CLARIFICATION = "fast_clarification"
    FAST_WHY = "fast_why"
    FAST_EXAMPLE = "fast_example"
    FAST_DEFAULT = "fast_default"
    DEEP_REASONING = "deep_reasoning"


class TeacherResponseLengthDecision(_LengthModel):
    response_mode: TeacherResponseMode
    target_output_tokens: int = Field(ge=1)
    maximum_output_tokens: int = Field(ge=1)
    reason: TeacherResponseLengthReason

    @model_validator(mode="after")
    def validate_target(self) -> "TeacherResponseLengthDecision":
        if self.target_output_tokens > self.maximum_output_tokens:
            raise ValueError("target output tokens cannot exceed maximum")
        return self


class TeacherResponseLengthPolicy(_LengthModel):
    fast_clarification_target_tokens: int = Field(ge=1)
    fast_why_target_tokens: int = Field(ge=1)
    fast_example_target_tokens: int = Field(ge=1)
    fast_default_target_tokens: int = Field(ge=1)
    fast_maximum_output_tokens: int = Field(ge=1)
    deep_target_tokens: int = Field(ge=1)
    deep_maximum_output_tokens: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_limits(self) -> "TeacherResponseLengthPolicy":
        fast_targets = (
            self.fast_clarification_target_tokens,
            self.fast_why_target_tokens,
            self.fast_example_target_tokens,
            self.fast_default_target_tokens,
        )
        if max(fast_targets) > self.fast_maximum_output_tokens:
            raise ValueError("FAST target cannot exceed FAST maximum")
        if self.deep_target_tokens > self.deep_maximum_output_tokens:
            raise ValueError("DEEP target cannot exceed DEEP maximum")
        if self.deep_maximum_output_tokens <= self.fast_maximum_output_tokens:
            raise ValueError("DEEP maximum must exceed FAST maximum")
        return self

    def resolve(
        self,
        *,
        response_mode: TeacherResponseMode,
        student_move: StudentMove,
    ) -> TeacherResponseLengthDecision:
        if response_mode is TeacherResponseMode.DEEP:
            return TeacherResponseLengthDecision(
                response_mode=response_mode,
                target_output_tokens=self.deep_target_tokens,
                maximum_output_tokens=self.deep_maximum_output_tokens,
                reason=TeacherResponseLengthReason.DEEP_REASONING,
            )
        if student_move in {StudentMove.ASK_DEFINITION, StudentMove.REQUEST_SIMPLIFY}:
            target = self.fast_clarification_target_tokens
            reason = TeacherResponseLengthReason.FAST_CLARIFICATION
        elif student_move is StudentMove.ASK_WHY:
            target = self.fast_why_target_tokens
            reason = TeacherResponseLengthReason.FAST_WHY
        elif student_move in {
            StudentMove.ASK_EXAMPLE,
            StudentMove.CONFUSED,
            StudentMove.REQUEST_PRACTICAL_USE,
        }:
            target = self.fast_example_target_tokens
            reason = TeacherResponseLengthReason.FAST_EXAMPLE
        else:
            target = self.fast_default_target_tokens
            reason = TeacherResponseLengthReason.FAST_DEFAULT
        return TeacherResponseLengthDecision(
            response_mode=response_mode,
            target_output_tokens=target,
            maximum_output_tokens=self.fast_maximum_output_tokens,
            reason=reason,
        )


__all__ = [
    "TeacherResponseLengthDecision",
    "TeacherResponseLengthPolicy",
    "TeacherResponseLengthReason",
]
