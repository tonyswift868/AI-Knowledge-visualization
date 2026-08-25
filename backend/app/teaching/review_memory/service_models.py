"""Durable-service contracts layered over the W5 review-memory model."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..contracts import HintDependency
from .models import (
    MacroRoundBoundary,
    ReviewMemoryItem,
    ReviewMemoryReceipt,
    ReviewObservation,
    ReviewPriority,
    ReviewRating,
    ReviewRatingDecision,
)


DURABLE_REVIEW_SCHEMA_VERSION = "durable-fsrs-review.v1"


class _DurableModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


class SchedulerStateIdentity(_DurableModel):
    package_name: Literal["fsrs"] = "fsrs"
    package_version: str = Field(min_length=1, max_length=80)
    adapter_id: str = Field(min_length=1, max_length=160)
    scheduler_config_json: str = Field(min_length=2, max_length=200_000)
    scheduler_config_sha256: str = Field(min_length=64, max_length=64)
    card_schema_version: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_identity(self) -> "SchedulerStateIdentity":
        try:
            decoded = json.loads(self.scheduler_config_json)
        except json.JSONDecodeError as exc:
            raise ValueError("scheduler_config_json must be valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("scheduler_config_json must encode an object")
        actual = hashlib.sha256(self.scheduler_config_json.encode("utf-8")).hexdigest()
        if actual != self.scheduler_config_sha256:
            raise ValueError("scheduler configuration checksum mismatch")
        return self


class DurableObservationStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"


class PersistedReviewMemoryItem(_DurableModel):
    schema_version: Literal["durable-fsrs-review.v1"] = (
        DURABLE_REVIEW_SCHEMA_VERSION
    )
    item: ReviewMemoryItem
    state_version: int = Field(ge=1)
    failure_streak: int = Field(default=0, ge=0, le=1_000_000)
    scheduler_identity: SchedulerStateIdentity
    stored_at: datetime

    @field_validator("stored_at")
    @classmethod
    def validate_stored_at(cls, value: datetime) -> datetime:
        return _aware(value, "stored_at")


class DurableReviewObservationRecord(_DurableModel):
    observation: ReviewObservation
    boundary: MacroRoundBoundary
    payload_sha256: str = Field(min_length=64, max_length=64)
    status: DurableObservationStatus
    rating_decision: ReviewRatingDecision | None = None
    receipt_id: str | None = Field(default=None, min_length=1, max_length=64)
    created_at: datetime
    applied_at: datetime | None = None

    @field_validator("created_at", "applied_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value, "observation timestamp")

    @model_validator(mode="after")
    def validate_status(self) -> "DurableReviewObservationRecord":
        if self.observation.macro_round_index != self.boundary.current_macro_round:
            raise ValueError("observation does not match persisted macro round")
        if (
            self.observation.same_round_attempt
            != self.boundary.attempts_for(self.observation.item_id) + 1
        ):
            raise ValueError("observation does not match persisted attempt boundary")
        applied = self.status is DurableObservationStatus.APPLIED
        if applied != (self.rating_decision is not None):
            raise ValueError("applied observation requires a rating decision")
        if applied != (self.receipt_id is not None and self.applied_at is not None):
            raise ValueError("applied observation requires receipt and applied_at")
        return self


class DurableReviewLog(_DurableModel):
    sequence: int | None = Field(default=None, ge=1)
    review_log_id: str = Field(min_length=1, max_length=240)
    receipt_id: str = Field(min_length=1, max_length=64)
    observation_id: str = Field(min_length=1, max_length=240)
    student_id: str = Field(min_length=1, max_length=160)
    item_id: str = Field(min_length=1, max_length=200)
    rating: ReviewRating
    rating_reasons: tuple[str, ...] = Field(min_length=1, max_length=16)
    source_event_ids: tuple[str, ...] = Field(default=(), max_length=128)
    card_before_json: str = Field(min_length=2, max_length=200_000)
    card_after_json: str = Field(min_length=2, max_length=200_000)
    due_at: datetime
    difficulty: float | None = Field(default=None, ge=0.0, le=10.0)
    stability: float | None = Field(default=None, ge=0.0)
    retrievability: float | None = Field(default=None, ge=0.0, le=1.0)
    reviewed_at: datetime
    state_version: int = Field(ge=2)
    scheduler_identity: SchedulerStateIdentity

    @field_validator("due_at", "reviewed_at")
    @classmethod
    def validate_log_timestamps(cls, value: datetime) -> datetime:
        return _aware(value, "review log timestamp")


class DurableReviewReceipt(_DurableModel):
    receipt: ReviewMemoryReceipt
    persisted_state_version: int = Field(ge=2)
    review_log_id: str = Field(min_length=1, max_length=240)
    observation_status: Literal[DurableObservationStatus.APPLIED] = (
        DurableObservationStatus.APPLIED
    )
    idempotent_replay: bool = False

    @model_validator(mode="after")
    def validate_receipt(self) -> "DurableReviewReceipt":
        if self.receipt.state_revision != self.persisted_state_version:
            raise ValueError("receipt state version differs from durable item version")
        if self.receipt.scheduler_result.review_log_id != self.review_log_id:
            raise ValueError("durable receipt review-log identity mismatch")
        return self


class ReviewPrioritySnapshot(_DurableModel):
    student_id: str = Field(min_length=1, max_length=160)
    generated_at: datetime
    boundary: MacroRoundBoundary
    priorities: tuple[ReviewPriority, ...] = Field(default=(), max_length=10_000)
    candidate_count: int = Field(ge=0)
    due_count: int = Field(ge=0)
    weak_count: int = Field(ge=0)
    macro_rounds_created: Literal[0] = 0

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        return _aware(value, "generated_at")


class ReviewMemoryAnalytics(_DurableModel):
    student_id: str = Field(min_length=1, max_length=160)
    item_id: str = Field(min_length=1, max_length=200)
    concept_id: str = Field(min_length=1, max_length=240)
    difficulty: float | None = Field(default=None, ge=0.0, le=10.0)
    stability: float | None = Field(default=None, ge=0.0)
    retrievability: float | None = Field(default=None, ge=0.0, le=1.0)
    retrievability_calculated_at: datetime | None = None
    due_at: datetime | None = None
    failure_streak: int = Field(ge=0)
    hint_dependency: HintDependency
    review_count: int = Field(ge=0)
    state_version: int = Field(ge=1)
    scheduler_version: str = Field(min_length=1, max_length=80)
    memory_is_mastery: Literal[False] = False

    @field_validator("due_at", "retrievability_calculated_at")
    @classmethod
    def validate_analytics_due(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value, "analytics timestamp")


class ReviewHistoryPage(_DurableModel):
    records: tuple[DurableReviewLog, ...]
    next_after_sequence: int | None = Field(default=None, ge=1)


class ReviewCheckpointReceipt(_DurableModel):
    database_path: str = Field(min_length=1)
    mode: Literal["PASSIVE", "FULL", "RESTART", "TRUNCATE"]
    busy: int = Field(ge=0)
    wal_pages: int = Field(ge=0)
    checkpointed_pages: int = Field(ge=0)
    item_count: int = Field(ge=0)
    review_event_count: int = Field(ge=0)
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: datetime) -> datetime:
        return _aware(value, "completed_at")


__all__ = [
    "DURABLE_REVIEW_SCHEMA_VERSION",
    "DurableObservationStatus",
    "DurableReviewLog",
    "DurableReviewObservationRecord",
    "DurableReviewReceipt",
    "PersistedReviewMemoryItem",
    "ReviewCheckpointReceipt",
    "ReviewHistoryPage",
    "ReviewMemoryAnalytics",
    "ReviewPrioritySnapshot",
    "SchedulerStateIdentity",
]
