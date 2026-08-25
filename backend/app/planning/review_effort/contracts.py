"""Immutable contracts for review effort, calibration, and plan preview."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


REVIEW_EFFORT_SCHEMA_VERSION = "mentorflow.review-effort.v1"
REVIEW_CALIBRATION_SCHEMA_VERSION = "mentorflow.review-calibration.v1"


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )


class ReviewQuestionType(StrEnum):
    SHORT_RECALL = "short_recall"
    EXPLANATION = "explanation"
    CODE_PROBLEM = "code_problem"
    ORAL_STYLE_QA = "oral_style_qa"


class ReviewItemPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewEstimateUseCase(StrEnum):
    NEW_PLAN = "new_plan"
    USER_REQUESTED_REGENERATION = "user_requested_regeneration"


class EffortRange(_FrozenContract):
    low_minutes: int = Field(ge=0)
    expected_minutes: int = Field(ge=0)
    high_minutes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "EffortRange":
        if not self.low_minutes <= self.expected_minutes <= self.high_minutes:
            raise ValueError("effort interval must contain expected_minutes")
        return self

    def __add__(self, other: "EffortRange") -> "EffortRange":
        return EffortRange(
            low_minutes=self.low_minutes + other.low_minutes,
            expected_minutes=self.expected_minutes + other.expected_minutes,
            high_minutes=self.high_minutes + other.high_minutes,
        )


class ReviewRoundConfig(_FrozenContract):
    """User-owned macro-round count plus explainable workload controls."""

    selected_rounds: int = Field(ge=0)
    weak_retry_allowance_rate: float = Field(default=0.20, ge=0, le=2)
    critical_concept_extra_rate: float = Field(default=0.18, ge=0, le=2)
    round_load_factors: tuple[float, ...] = ()

    @model_validator(mode="after")
    def validate_round_factors(self) -> "ReviewRoundConfig":
        if self.round_load_factors and len(self.round_load_factors) != self.selected_rounds:
            raise ValueError(
                "round_load_factors must be empty or match selected_rounds"
            )
        if any(value <= 0 or value > 4 for value in self.round_load_factors):
            raise ValueError("round load factors must be within (0, 4]")
        return self

    def load_factor_for(self, round_index: int) -> float:
        if not 1 <= round_index <= self.selected_rounds:
            raise ValueError("round_index is outside the selected rounds")
        if self.round_load_factors:
            return self.round_load_factors[round_index - 1]
        # Later rounds remain real work, while routine items need less time.
        return max(0.58, 1.0 - 0.09 * (round_index - 1))


class ReviewItem(_FrozenContract):
    item_id: str = Field(min_length=1, max_length=200)
    concept_id: str = Field(min_length=1, max_length=160)
    question_type: ReviewQuestionType
    priority: ReviewItemPriority = ReviewItemPriority.NORMAL
    critical_concept: bool = False
    expected_answer_seconds: float | None = Field(default=None, gt=0, le=7_200)
    expected_evaluation_seconds: float | None = Field(default=None, gt=0, le=1_800)
    expected_feedback_seconds: float | None = Field(default=None, gt=0, le=1_800)


class QuestionTypeCalibration(_FrozenContract):
    question_type: ReviewQuestionType
    sample_count: int = Field(gt=0)
    answer_speed_factor: float = Field(gt=0, le=4)
    evaluation_speed_factor: float = Field(gt=0, le=4)
    wrong_rate: float = Field(ge=0, le=1)
    partial_rate: float = Field(ge=0, le=1)
    hint_rate: float = Field(ge=0, le=1)
    weak_item_retry_rate: float = Field(ge=0, le=1)


class ReviewCalibrationProfile(_FrozenContract):
    schema_version: Literal["mentorflow.review-calibration.v1"] = (
        REVIEW_CALIBRATION_SCHEMA_VERSION
    )
    profile_id: str = Field(min_length=1, max_length=200)
    by_question_type: tuple[QuestionTypeCalibration, ...] = ()

    @model_validator(mode="after")
    def validate_unique_types(self) -> "ReviewCalibrationProfile":
        values = [item.question_type for item in self.by_question_type]
        if len(values) != len(set(values)):
            raise ValueError("calibration question types must be unique")
        return self

    def for_type(
        self,
        question_type: ReviewQuestionType,
    ) -> QuestionTypeCalibration | None:
        return next(
            (
                item
                for item in self.by_question_type
                if item.question_type is question_type
            ),
            None,
        )


class ReviewCalibrationObservation(_FrozenContract):
    question_type: ReviewQuestionType
    sample_count: int = Field(default=1, gt=0)
    actual_answer_time_seconds: float = Field(gt=0, le=7_200)
    evaluation_time_seconds: float = Field(gt=0, le=1_800)
    wrong_rate: float = Field(ge=0, le=1)
    partial_rate: float = Field(ge=0, le=1)
    hint_rate: float = Field(ge=0, le=1)
    weak_item_retry_rate: float = Field(ge=0, le=1)


class ReviewEffortInput(_FrozenContract):
    schema_version: Literal["mentorflow.review-effort.v1"] = (
        REVIEW_EFFORT_SCHEMA_VERSION
    )
    request_id: str = Field(min_length=1, max_length=160)
    use_case: ReviewEstimateUseCase
    initial_learning_effort: EffortRange
    round_config: ReviewRoundConfig
    items: tuple[ReviewItem, ...] = ()
    historical_calibration: ReviewCalibrationProfile | None = None

    @model_validator(mode="after")
    def validate_items(self) -> "ReviewEffortInput":
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("review item ids must be unique")
        if self.round_config.selected_rounds > 0 and not self.items:
            raise ValueError("selected review rounds require review items")
        return self


class ReviewEffortBreakdown(_FrozenContract):
    answer_minutes: int = Field(ge=0)
    evaluation_minutes: int = Field(ge=0)
    feedback_minutes: int = Field(ge=0)
    weak_retry_allowance_minutes: int = Field(ge=0)
    critical_concept_load_minutes: int = Field(ge=0)
    expected_minutes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> "ReviewEffortBreakdown":
        total = (
            self.answer_minutes
            + self.evaluation_minutes
            + self.feedback_minutes
            + self.weak_retry_allowance_minutes
            + self.critical_concept_load_minutes
        )
        if total != self.expected_minutes:
            raise ValueError("review breakdown must sum to expected_minutes")
        return self


class ReviewRoundEffort(_FrozenContract):
    round_id: str = Field(pattern=r"^REVIEW_ROUND_[1-9][0-9]*$")
    round_index: int = Field(gt=0)
    review_item_count: int = Field(gt=0)
    relevant_concept_ids: tuple[str, ...] = Field(min_length=1)
    low_minutes: int = Field(gt=0)
    expected_minutes: int = Field(gt=0)
    high_minutes: int = Field(gt=0)
    breakdown: ReviewEffortBreakdown
    confidence: float = Field(ge=0, le=1)
    basis: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_round(self) -> "ReviewRoundEffort":
        if self.round_id != f"REVIEW_ROUND_{self.round_index}":
            raise ValueError("round_id must match round_index")
        if not self.low_minutes <= self.expected_minutes <= self.high_minutes:
            raise ValueError("review round interval must contain expected_minutes")
        if self.breakdown.expected_minutes != self.expected_minutes:
            raise ValueError("review breakdown must match round expected effort")
        if tuple(sorted(set(self.relevant_concept_ids))) != self.relevant_concept_ids:
            raise ValueError("relevant concept ids must be sorted and unique")
        return self

    @property
    def effort(self) -> EffortRange:
        return EffortRange(
            low_minutes=self.low_minutes,
            expected_minutes=self.expected_minutes,
            high_minutes=self.high_minutes,
        )


# Milestone C names the calibrated per-round result ReviewRoundEstimate.
ReviewRoundEstimate = ReviewRoundEffort


class ReviewEffortEstimate(_FrozenContract):
    schema_version: Literal["mentorflow.review-effort.v1"] = (
        REVIEW_EFFORT_SCHEMA_VERSION
    )
    estimate_id: str = Field(min_length=1, max_length=200)
    estimator_version: str = Field(min_length=1, max_length=120)
    use_case: ReviewEstimateUseCase
    selected_review_rounds: int = Field(ge=0)
    initial_learning_effort: EffortRange
    review_rounds: tuple[ReviewRoundEffort, ...] = ()
    review_total: EffortRange
    grand_total: EffortRange
    historical_calibration_profile_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    basis: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_preview_totals(self) -> "ReviewEffortEstimate":
        if len(self.review_rounds) != self.selected_review_rounds:
            raise ValueError("estimate must contain exactly the selected rounds")
        if [item.round_index for item in self.review_rounds] != list(
            range(1, self.selected_review_rounds + 1)
        ):
            raise ValueError("review rounds must be contiguous and ordered")
        expected_review_total = EffortRange(
            low_minutes=sum(item.low_minutes for item in self.review_rounds),
            expected_minutes=sum(
                item.expected_minutes for item in self.review_rounds
            ),
            high_minutes=sum(item.high_minutes for item in self.review_rounds),
        )
        if self.review_total != expected_review_total:
            raise ValueError("review_total must equal all selected review rounds")
        if self.grand_total != self.initial_learning_effort + self.review_total:
            raise ValueError(
                "grand_total must equal initial learning plus all review rounds"
            )
        return self

    @property
    def review_round_efforts(self) -> dict[str, EffortRange]:
        return {item.round_id: item.effort for item in self.review_rounds}


__all__ = [
    "REVIEW_CALIBRATION_SCHEMA_VERSION",
    "REVIEW_EFFORT_SCHEMA_VERSION",
    "EffortRange",
    "QuestionTypeCalibration",
    "ReviewCalibrationObservation",
    "ReviewCalibrationProfile",
    "ReviewEffortBreakdown",
    "ReviewEffortEstimate",
    "ReviewEffortInput",
    "ReviewEstimateUseCase",
    "ReviewItem",
    "ReviewItemPriority",
    "ReviewQuestionType",
    "ReviewRoundConfig",
    "ReviewRoundEffort",
    "ReviewRoundEstimate",
]
