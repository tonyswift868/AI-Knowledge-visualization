"""Immutable contracts for explainable learning-effort estimates.

The contracts deliberately separate a prediction from a later observation.
They contain no persistence, provider, or model-training behavior.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


FEATURE_SCHEMA_VERSION = "mentorflow.effort-features.v1"
OBSERVATION_SCHEMA_VERSION = "mentorflow.effort-observation.v1"


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )


class KnowledgeType(StrEnum):
    FACTUAL = "factual"
    CONCEPT = "concept"
    PROCEDURE = "procedure"
    PROBLEM_SOLVING = "problem_solving"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class TeachingDepth(StrEnum):
    BRIEF = "brief"
    STANDARD = "standard"
    DEEP = "deep"


class LearnerLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class EffortFeatures(_FrozenContract):
    """Available, explainable inputs for one concept or learning task.

    Density and requirement values use a normalized 0..1 convention. Values
    above one remain valid inputs but are capped by the estimator, making
    untrusted or accidental extreme inputs safe. ``source_duration_minutes``
    is trace-only: media playback time is not a learning-effort proxy.
    """

    schema_version: Literal["mentorflow.effort-features.v1"] = FEATURE_SCHEMA_VERSION
    content_amount: float = Field(default=1.0, ge=0)
    knowledge_type: KnowledgeType = KnowledgeType.CONCEPT
    difficulty: Difficulty = Difficulty.MEDIUM
    prerequisite_depth: int = Field(default=0, ge=0)
    code_density: float = Field(default=0.0, ge=0)
    formula_table_complexity: float = Field(default=0.0, ge=0)
    practice_requirement: float = Field(default=0.5, ge=0)
    teaching_depth: TeachingDepth = TeachingDepth.STANDARD
    learner_level: LearnerLevel = LearnerLevel.INTERMEDIATE
    mastery: float | None = Field(default=None, ge=0, le=1)
    source_duration_minutes: float | None = Field(default=None, ge=0)


class EffortEstimate(_FrozenContract):
    """Versioned component estimate with a deliberately coarse interval."""

    estimate_id: str = Field(min_length=1, max_length=80)
    estimator_version: str = Field(min_length=1, max_length=120)
    understand_minutes: int = Field(ge=0)
    example_minutes: int = Field(ge=0)
    practice_minutes: int = Field(ge=0)
    assessment_minutes: int = Field(ge=0)
    review_minutes: int = Field(ge=0)
    low_minutes: int = Field(ge=0)
    expected_minutes: int = Field(ge=0)
    high_minutes: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    basis: tuple[str, ...] = Field(min_length=1)
    features: EffortFeatures

    @model_validator(mode="after")
    def validate_component_and_interval_totals(self) -> "EffortEstimate":
        component_total = (
            self.understand_minutes
            + self.example_minutes
            + self.practice_minutes
            + self.assessment_minutes
            + self.review_minutes
        )
        if component_total != self.expected_minutes:
            raise ValueError("expected_minutes must equal the component total")
        if not self.low_minutes <= self.expected_minutes <= self.high_minutes:
            raise ValueError("minute interval must contain expected_minutes")
        return self


class EffortObservation(_FrozenContract):
    """Future calibration input; creating it never writes to a database."""

    schema_version: Literal["mentorflow.effort-observation.v1"] = (
        OBSERVATION_SCHEMA_VERSION
    )
    estimate_id: str = Field(min_length=1, max_length=80)
    estimator_version: str = Field(min_length=1, max_length=120)
    predicted_minutes: int = Field(ge=0)
    predicted_low_minutes: int = Field(ge=0)
    predicted_high_minutes: int = Field(ge=0)
    actual_active_minutes: int = Field(ge=0)
    attempts: int = Field(default=0, ge=0)
    mistakes: int = Field(default=0, ge=0)
    hints: int = Field(default=0, ge=0)
    mastery_before: float | None = Field(default=None, ge=0, le=1)
    mastery_after: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_prediction_interval(self) -> "EffortObservation":
        if not (
            self.predicted_low_minutes
            <= self.predicted_minutes
            <= self.predicted_high_minutes
        ):
            raise ValueError("predicted interval must contain predicted_minutes")
        return self

    @classmethod
    def from_estimate(
        cls,
        estimate: EffortEstimate,
        *,
        actual_active_minutes: int,
        attempts: int = 0,
        mistakes: int = 0,
        hints: int = 0,
        mastery_before: float | None = None,
        mastery_after: float | None = None,
    ) -> "EffortObservation":
        return cls(
            estimate_id=estimate.estimate_id,
            estimator_version=estimate.estimator_version,
            predicted_minutes=estimate.expected_minutes,
            predicted_low_minutes=estimate.low_minutes,
            predicted_high_minutes=estimate.high_minutes,
            actual_active_minutes=actual_active_minutes,
            attempts=attempts,
            mistakes=mistakes,
            hints=hints,
            mastery_before=mastery_before,
            mastery_after=mastery_after,
        )


__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "OBSERVATION_SCHEMA_VERSION",
    "Difficulty",
    "EffortEstimate",
    "EffortFeatures",
    "EffortObservation",
    "KnowledgeType",
    "LearnerLevel",
    "TeachingDepth",
]
