"""Immutable review-memory contracts, deliberately separate from mastery."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..contracts import HintDependency


REVIEW_MEMORY_SCHEMA_VERSION = "review-memory.v1"


class _ReviewModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )


class ReviewObservationOutcome(StrEnum):
    FAILED = "failed"
    PARTIAL = "partial"
    HINT_DEPENDENT = "hint_dependent"
    RECALLED_WITH_DIFFICULTY = "recalled_with_difficulty"
    RECALLED = "recalled"


class ReviewRating(StrEnum):
    AGAIN = "again"
    HARD = "hard"
    GOOD = "good"
    EASY = "easy"


class ReviewPriorityBand(StrEnum):
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


def _validate_aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class ReviewMemoryItem(_ReviewModel):
    schema_version: Literal["review-memory.v1"] = REVIEW_MEMORY_SCHEMA_VERSION
    item_id: str = Field(min_length=1, max_length=200)
    student_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    question_id: str | None = Field(default=None, min_length=1, max_length=160)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=64)
    source_event_ids: tuple[str, ...] = Field(default=(), max_length=128)
    last_review_at: datetime | None = None
    review_count: int = Field(default=0, ge=0, le=1_000_000)
    failure_count: int = Field(default=0, ge=0, le=1_000_000)
    consecutive_recalls: int = Field(default=0, ge=0, le=1_000_000)
    hint_dependency: HintDependency = HintDependency.NONE
    difficulty: float | None = Field(default=None, ge=0.0, le=10.0)
    stability: float | None = Field(default=None, ge=0.0)
    retrievability: float | None = Field(default=None, ge=0.0, le=1.0)
    due_at: datetime | None = None
    last_observation: ReviewObservationOutcome | None = None
    last_review_log_id: str | None = Field(default=None, min_length=1, max_length=240)
    scheduler_card_json: str = Field(default="{}", max_length=200_000)
    critical_weight: float = Field(default=1.0, ge=1.0, le=3.0)

    @field_validator("last_review_at", "due_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return _validate_aware(value, "review timestamp")

    @field_validator("evidence_ids", "source_event_ids")
    @classmethod
    def validate_unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("evidence/source ids cannot contain blanks")
        if len(values) != len(set(values)):
            raise ValueError("evidence/source ids must be unique")
        return values

    @field_validator("scheduler_card_json")
    @classmethod
    def validate_card_json(cls, value: str) -> str:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("scheduler_card_json must be valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("scheduler_card_json must encode an object")
        return value

    @model_validator(mode="after")
    def validate_review_shape(self) -> "ReviewMemoryItem":
        if self.failure_count > self.review_count:
            raise ValueError("failure_count cannot exceed review_count")
        if self.consecutive_recalls > self.review_count:
            raise ValueError("consecutive_recalls cannot exceed review_count")
        reviewed_fields = (
            self.last_observation,
            self.last_review_log_id,
        )
        if self.review_count == 0 and any(value is not None for value in reviewed_fields):
            raise ValueError("unreviewed item cannot carry review outcome/log identity")
        if self.review_count > 0:
            if self.last_review_at is None:
                raise ValueError("reviewed item requires last_review_at")
            if any(value is None for value in reviewed_fields):
                raise ValueError("reviewed item requires outcome and review-log identity")
            if self.due_at is None:
                raise ValueError("reviewed item requires scheduler due_at")
            if self.scheduler_card_json == "{}":
                raise ValueError("reviewed item requires serialized scheduler card state")
        return self


class ReviewMemoryState(_ReviewModel):
    schema_version: Literal["review-memory.v1"] = REVIEW_MEMORY_SCHEMA_VERSION
    student_id: str = Field(min_length=1, max_length=160)
    items: tuple[ReviewMemoryItem, ...] = Field(default=(), max_length=100_000)
    processed_observation_ids: tuple[str, ...] = Field(
        default=(),
        max_length=100_000,
    )
    revision: int = Field(default=0, ge=0)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def validate_updated_at(cls, value: datetime) -> datetime:
        return _validate_aware(value, "updated_at")

    @model_validator(mode="after")
    def validate_state(self) -> "ReviewMemoryState":
        item_ids = tuple(item.item_id for item in self.items)
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("review memory item ids must be unique")
        if any(item.student_id != self.student_id for item in self.items):
            raise ValueError("review memory items must belong to state student")
        if len(self.processed_observation_ids) != len(
            set(self.processed_observation_ids)
        ):
            raise ValueError("processed observation ids must be unique")
        return self

    @property
    def item_by_id(self) -> dict[str, ReviewMemoryItem]:
        return {item.item_id: item for item in self.items}


class ReviewObservation(_ReviewModel):
    observation_id: str = Field(min_length=1, max_length=240)
    student_id: str = Field(min_length=1, max_length=160)
    item_id: str = Field(min_length=1, max_length=200)
    concept_id: str = Field(min_length=1, max_length=240)
    question_id: str | None = Field(default=None, min_length=1, max_length=160)
    outcome: ReviewObservationOutcome
    observed_at: datetime
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=64)
    source_event_ids: tuple[str, ...] = Field(default=(), max_length=128)
    assessment_id: str | None = Field(default=None, min_length=1, max_length=160)
    hint_used: bool = False
    hint_dependency: HintDependency = HintDependency.NONE
    hesitation_observed: bool = False
    response_latency_seconds: float | None = Field(default=None, ge=0.0, le=86_400.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    macro_round_index: int = Field(ge=1, le=10_000)
    same_round_attempt: int = Field(default=1, ge=1, le=100)

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _validate_aware(value, "observed_at")

    @field_validator("evidence_ids", "source_event_ids")
    @classmethod
    def validate_observation_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("observation ids cannot contain blanks")
        if len(values) != len(set(values)):
            raise ValueError("observation ids must be unique")
        return values

    @model_validator(mode="after")
    def validate_hint_observation(self) -> "ReviewObservation":
        if self.hint_used and self.hint_dependency is HintDependency.NONE:
            raise ValueError("hint_used requires an explicit hint dependency")
        if self.outcome is ReviewObservationOutcome.HINT_DEPENDENT and not self.hint_used:
            raise ValueError("hint-dependent outcome requires hint_used=true")
        return self


class ReviewRatingDecision(_ReviewModel):
    policy_id: Literal["review-rating-policy.v1"] = "review-rating-policy.v1"
    observation_id: str = Field(min_length=1, max_length=240)
    rating: ReviewRating
    reasons: tuple[str, ...] = Field(min_length=1, max_length=16)
    easy_gate_satisfied: bool = False


class RoundAttemptCount(_ReviewModel):
    item_id: str = Field(min_length=1, max_length=200)
    attempts: int = Field(ge=0, le=100)


class MacroRoundBoundary(_ReviewModel):
    configured_macro_rounds: int = Field(ge=1, le=10_000)
    current_macro_round: int = Field(ge=1, le=10_000)
    same_round_attempts: tuple[RoundAttemptCount, ...] = Field(
        default=(),
        max_length=100_000,
    )

    @model_validator(mode="after")
    def validate_boundary(self) -> "MacroRoundBoundary":
        if self.current_macro_round > self.configured_macro_rounds:
            raise ValueError("current macro round exceeds product-configured rounds")
        item_ids = tuple(item.item_id for item in self.same_round_attempts)
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("same-round attempt items must be unique")
        return self

    def attempts_for(self, item_id: str) -> int:
        return next(
            (
                item.attempts
                for item in self.same_round_attempts
                if item.item_id == item_id
            ),
            0,
        )


class ReviewPriority(_ReviewModel):
    item_id: str = Field(min_length=1, max_length=200)
    score: float = Field(ge=0.0, le=1_000.0)
    band: ReviewPriorityBand
    reasons: tuple[str, ...] = Field(min_length=1, max_length=24)
    due_at: datetime | None = None
    critical_weight: float = Field(ge=1.0, le=3.0)
    same_round_retry_allowed: bool
    remaining_same_round_retries: int = Field(ge=0, le=100)

    @field_validator("due_at")
    @classmethod
    def validate_due_at(cls, value: datetime | None) -> datetime | None:
        return _validate_aware(value, "due_at")


class SchedulerReviewResult(_ReviewModel):
    adapter_id: str = Field(min_length=1, max_length=160)
    card_state_json: str = Field(min_length=2, max_length=200_000)
    review_log_id: str = Field(min_length=1, max_length=240)
    due_at: datetime
    difficulty: float | None = Field(default=None, ge=0.0, le=10.0)
    stability: float | None = Field(default=None, ge=0.0)
    retrievability: float | None = Field(default=None, ge=0.0, le=1.0)
    scheduled_at: datetime
    macro_round_index: int = Field(ge=1, le=10_000)
    configured_macro_rounds: int = Field(ge=1, le=10_000)

    @field_validator("due_at", "scheduled_at")
    @classmethod
    def validate_scheduler_timestamps(cls, value: datetime) -> datetime:
        return _validate_aware(value, "scheduler timestamp")

    @field_validator("card_state_json")
    @classmethod
    def validate_scheduler_card(cls, value: str) -> str:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("card_state_json must be valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("card_state_json must encode an object")
        return value

    @model_validator(mode="after")
    def validate_scheduler_boundary(self) -> "SchedulerReviewResult":
        if self.macro_round_index > self.configured_macro_rounds:
            raise ValueError("scheduler result exceeds configured macro rounds")
        return self


class ReviewMemoryEventProposal(_ReviewModel):
    proposal_id: str = Field(min_length=1, max_length=64)
    observation: ReviewObservation
    source: Literal["student_repository"] = "student_repository"
    changes_mastery: Literal[False] = False
    proposed_mastery_event_ids: tuple[()] = ()


class ReviewMemoryReceipt(_ReviewModel):
    schema_version: Literal["review-memory.v1"] = REVIEW_MEMORY_SCHEMA_VERSION
    receipt_id: str = Field(min_length=1, max_length=64)
    observation: ReviewObservation
    item_before: ReviewMemoryItem
    item_after: ReviewMemoryItem
    rating_decision: ReviewRatingDecision
    scheduler_result: SchedulerReviewResult
    priority: ReviewPriority
    state_revision: int = Field(ge=1)
    configured_macro_rounds_before: int = Field(ge=1, le=10_000)
    configured_macro_rounds_after: int = Field(ge=1, le=10_000)
    applied: Literal[True] = True
    mastery_changed: Literal[False] = False

    @model_validator(mode="after")
    def validate_receipt(self) -> "ReviewMemoryReceipt":
        if self.item_before.item_id != self.item_after.item_id:
            raise ValueError("receipt item identity changed")
        if self.observation.item_id != self.item_after.item_id:
            raise ValueError("receipt observation item mismatch")
        if self.rating_decision.observation_id != self.observation.observation_id:
            raise ValueError("receipt rating decision observation mismatch")
        if self.priority.item_id != self.item_after.item_id:
            raise ValueError("receipt priority item mismatch")
        if self.item_after.review_count != self.item_before.review_count + 1:
            raise ValueError("receipt must advance review count exactly once")
        if self.item_after.last_review_log_id != self.scheduler_result.review_log_id:
            raise ValueError("receipt review-log identity mismatch")
        if self.item_after.due_at != self.scheduler_result.due_at:
            raise ValueError("receipt item due_at differs from scheduler result")
        if self.scheduler_result.scheduled_at != self.observation.observed_at:
            raise ValueError("receipt scheduler timestamp differs from observation")
        if self.scheduler_result.macro_round_index != self.observation.macro_round_index:
            raise ValueError("receipt scheduler macro round differs from observation")
        if self.configured_macro_rounds_before != self.configured_macro_rounds_after:
            raise ValueError("review scheduler cannot change macro round count")
        if (
            self.scheduler_result.configured_macro_rounds
            != self.configured_macro_rounds_after
        ):
            raise ValueError("scheduler result changed macro round boundary")
        return self


__all__ = [
    "REVIEW_MEMORY_SCHEMA_VERSION",
    "MacroRoundBoundary",
    "ReviewMemoryEventProposal",
    "ReviewMemoryItem",
    "ReviewMemoryReceipt",
    "ReviewMemoryState",
    "ReviewObservation",
    "ReviewObservationOutcome",
    "ReviewPriority",
    "ReviewPriorityBand",
    "ReviewRating",
    "ReviewRatingDecision",
    "RoundAttemptCount",
    "SchedulerReviewResult",
]
