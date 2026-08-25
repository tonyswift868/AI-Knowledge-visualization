"""Public, privacy-bounded progress read contracts.

Mastery evidence and FSRS memory state are deliberately different objects.
Nothing in this surface converts retrievability into a mastery percentage.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..contracts import HintDependency
from ..review_memory import ReviewObservationOutcome, ReviewRating


PROGRESS_READ_SCHEMA_VERSION = "mentorflow.progress-read.v1"


class _ReadModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )


class MasteryStatus(StrEnum):
    LEARNING = "learning"
    SELF_ATTESTED = "self_attested"
    VERIFIED = "verified"


class TimelineCategory(StrEnum):
    TEACHING = "teaching"
    ASSESSMENT = "assessment"
    REVIEW = "review"
    VERIFIED = "verified"
    WEAK = "weak"


class AttemptResult(StrEnum):
    ATTEMPTED = "attempted"
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    ASSESSMENT_PASSED = "assessment_passed"
    ASSESSMENT_FAILED = "assessment_failed"


class MasteryEvidenceRead(_ReadModel):
    status: MasteryStatus
    explanation: str = Field(min_length=1, max_length=360)
    self_attested_event_count: int = Field(ge=0)
    verified_event_count: int = Field(ge=0)
    assessment_evidence_count: int = Field(ge=0)
    failed_assessment_count: int = Field(ge=0)
    misconception_count: int = Field(ge=0)
    source_event_ids: tuple[str, ...] = Field(default=(), max_length=128)
    verification_evidence_ids: tuple[str, ...] = Field(default=(), max_length=128)


class RecentAttemptRead(_ReadModel):
    event_id: str = Field(min_length=1, max_length=160)
    occurred_at: datetime
    result: AttemptResult
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=64)


class MemoryDiagnosticsRead(_ReadModel):
    difficulty: float | None = Field(default=None, ge=0.0, le=10.0)
    stability: float | None = Field(default=None, ge=0.0)
    retrievability: float | None = Field(default=None, ge=0.0, le=1.0)
    retrievability_calculated_at: datetime | None = None
    scheduler_version: str | None = Field(default=None, max_length=80)
    state_version: int | None = Field(default=None, ge=1)
    memory_is_mastery: Literal[False] = False


class ReviewPanelRead(_ReadModel):
    item_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    next_due_at: datetime | None = None
    recent_result: ReviewObservationOutcome | None = None
    recent_rating: ReviewRating | None = None
    recent_reviewed_at: datetime | None = None
    failure_streak: int = Field(ge=0)
    hint_dependency: HintDependency
    is_due: bool
    is_weak: bool
    explanation: str = Field(min_length=1, max_length=360)
    advanced_diagnostics: MemoryDiagnosticsRead


class ConceptProgressRead(_ReadModel):
    concept_id: str = Field(min_length=1, max_length=240)
    label: str = Field(min_length=1, max_length=240)
    mastery: MasteryEvidenceRead
    review: ReviewPanelRead
    recent_attempts: tuple[RecentAttemptRead, ...] = Field(default=(), max_length=20)
    active_misconceptions: tuple[str, ...] = Field(default=(), max_length=12)
    last_activity_at: datetime | None = None


class QueueItemRead(_ReadModel):
    item_id: str = Field(min_length=1, max_length=200)
    concept_id: str = Field(min_length=1, max_length=240)
    concept_label: str = Field(min_length=1, max_length=240)
    due_at: datetime | None = None
    failure_streak: int = Field(ge=0)
    hint_dependency: HintDependency
    reason: str = Field(min_length=1, max_length=240)


class TimelineEntryRead(_ReadModel):
    entry_id: str = Field(min_length=1, max_length=240)
    concept_id: str = Field(min_length=1, max_length=240)
    concept_label: str = Field(min_length=1, max_length=240)
    category: TimelineCategory
    occurred_at: datetime
    title: str = Field(min_length=1, max_length=160)
    explanation: str = Field(min_length=1, max_length=360)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=64)


class ProgressSummaryRead(_ReadModel):
    concept_count: int = Field(ge=0)
    learning_count: int = Field(ge=0)
    self_attested_count: int = Field(ge=0)
    verified_count: int = Field(ge=0)
    due_count: int = Field(ge=0)
    weak_count: int = Field(ge=0)
    recent_attempt_count: int = Field(ge=0)


class ExplainProgressReadModel(_ReadModel):
    schema_version: Literal["mentorflow.progress-read.v1"] = (
        PROGRESS_READ_SCHEMA_VERSION
    )
    student_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    generated_at: datetime
    demo: bool = False
    demo_label: str | None = Field(default=None, max_length=160)
    mastery_semantics: Literal[
        "learning_self_attested_verified_from_student_evidence"
    ] = "learning_self_attested_verified_from_student_evidence"
    memory_semantics: Literal[
        "fsrs_memory_state_not_mastery"
    ] = "fsrs_memory_state_not_mastery"
    privacy_scope: Literal["learning_evidence_only"] = "learning_evidence_only"
    timeline_categories: tuple[TimelineCategory, ...] = tuple(TimelineCategory)
    summary: ProgressSummaryRead
    concepts: tuple[ConceptProgressRead, ...] = Field(default=(), max_length=1_000)
    due_queue: tuple[QueueItemRead, ...] = Field(default=(), max_length=1_000)
    weak_queue: tuple[QueueItemRead, ...] = Field(default=(), max_length=1_000)
    recent_activity: tuple[TimelineEntryRead, ...] = Field(default=(), max_length=200)

    @field_validator("generated_at")
    @classmethod
    def generated_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value


class ConceptProgressDetailReadModel(_ReadModel):
    schema_version: Literal["mentorflow.progress-read.v1"] = (
        PROGRESS_READ_SCHEMA_VERSION
    )
    student_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    generated_at: datetime
    demo: bool = False
    concept: ConceptProgressRead
    evidence_timeline: tuple[TimelineEntryRead, ...] = Field(
        default=(), max_length=500
    )


__all__ = [
    "AttemptResult",
    "ConceptProgressDetailReadModel",
    "ConceptProgressRead",
    "ExplainProgressReadModel",
    "MasteryEvidenceRead",
    "MasteryStatus",
    "MemoryDiagnosticsRead",
    "PROGRESS_READ_SCHEMA_VERSION",
    "ProgressSummaryRead",
    "QueueItemRead",
    "RecentAttemptRead",
    "ReviewPanelRead",
    "TimelineCategory",
    "TimelineEntryRead",
]
