"""Immutable contracts for the student event repository and query surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..contracts import LearnerContext
from ..learning_state import (
    ConceptLearningState,
    LearningStage,
    LearningStateTransition,
    ReducerResult,
)
from ..student_model import StudentLearningEvent, StudentLearningEventType


@dataclass(frozen=True, slots=True)
class StudentEventRecord:
    sequence: int
    event_id: str
    student_id: str
    session_id: str
    concept_id: str
    event_type: StudentLearningEventType
    occurred_at: datetime
    ingested_at: datetime
    event: StudentLearningEvent

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.student_id, self.session_id, self.event_id


@dataclass(frozen=True, slots=True)
class StudentEventCursor:
    after_sequence: int = 0

    def __post_init__(self) -> None:
        if self.after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")


@dataclass(frozen=True, slots=True)
class StudentEventPage:
    records: tuple[StudentEventRecord, ...]
    next_cursor: StudentEventCursor | None = None


@dataclass(frozen=True, slots=True)
class CheckpointPolicy:
    every_n_events: int = 100

    def __post_init__(self) -> None:
        if not 1 <= self.every_n_events <= 100_000:
            raise ValueError("every_n_events must be between 1 and 100000")

    def should_checkpoint(
        self,
        *,
        current_event_count: int,
        previous_event_count: int,
        session_boundary: bool = False,
    ) -> bool:
        return session_boundary or (
            current_event_count > 0
            and current_event_count - previous_event_count >= self.every_n_events
        )


@dataclass(frozen=True, slots=True)
class StudentSnapshotCheckpoint:
    checkpoint_id: str
    student_id: str
    session_id: str
    through_sequence: int
    event_count: int
    event_digest: str
    snapshot_digest: str
    snapshot: ReducerResult
    created_at: datetime
    replay_events: tuple[StudentLearningEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckpointValidation:
    checkpoint_id: str
    valid: bool
    reasons: tuple[str, ...]
    event_digest_match: bool
    snapshot_digest_match: bool
    checkpoint_replay_match: bool
    full_replay_equivalent: bool
    subsequent_event_count: int


@dataclass(frozen=True, slots=True)
class MasteryEvidenceLedger:
    student_id: str
    session_id: str
    concept_id: str
    current_stage: LearningStage
    confidence: float
    source_event_ids: tuple[str, ...]
    self_attested_event_ids: tuple[str, ...]
    verified_event_ids: tuple[str, ...]
    assessment_evidence_ids: tuple[str, ...]
    failed_assessment_event_ids: tuple[str, ...]
    misconception_event_ids: tuple[str, ...]
    recent_attempt_event_ids: tuple[str, ...]
    source_events: tuple[StudentEventRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class ConceptProgressHistory:
    student_id: str
    session_id: str
    concept_id: str
    state: ConceptLearningState
    transitions: tuple[LearningStateTransition, ...]
    misconception_history: tuple[str, ...]
    recent_attempts: tuple[StudentEventRecord, ...]
    ledger: MasteryEvidenceLedger


@dataclass(frozen=True, slots=True)
class AttemptSummary:
    attempt_count: int = 0
    correct_count: int = 0
    partial_count: int = 0
    incorrect_count: int = 0
    assessment_count: int = 0
    consecutive_failures: int = 0


@dataclass(frozen=True, slots=True)
class RepositoryLearnerContextProjection:
    learner_context: LearnerContext
    attempt_summary: AttemptSummary


__all__ = [
    "AttemptSummary",
    "CheckpointPolicy",
    "CheckpointValidation",
    "ConceptProgressHistory",
    "MasteryEvidenceLedger",
    "RepositoryLearnerContextProjection",
    "StudentEventCursor",
    "StudentEventPage",
    "StudentEventRecord",
    "StudentSnapshotCheckpoint",
]
