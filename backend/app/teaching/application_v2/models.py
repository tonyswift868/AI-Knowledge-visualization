"""Application-local contracts for transactional Teacher turns."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..composer import ConversationTurn, TeacherEvidence
from ..contracts import DecisionReason, LearnerContext, StudentMove, TeacherMove
from ..runtime_v2 import TeacherTurnResult


TEACHER_APPLICATION_SCHEMA_VERSION = "teacher-application.v2"


class _ApplicationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TurnTransactionStatus(str, Enum):
    RUNNING = "running"
    PENDING_COMMIT = "pending_commit"
    COMMITTING = "committing"
    COMMITTED = "committed"
    ABORTED = "aborted"
    FAILED = "failed"


class TurnCommitStatus(str, Enum):
    EMPTY = "empty"
    PENDING = "pending"
    COMMITTED = "committed"
    ABORTED = "aborted"


class ProposedEventType(str, Enum):
    TEACHER_TURN_COMPLETED = "teacher_turn_completed"
    LEARNER_SELF_ATTESTED = "learner_self_attested"
    TRANSITION_RECOMMENDED = "transition_recommended"
    ASSESSMENT_RECOMMENDED = "assessment_recommended"


class EventAttribute(_ApplicationModel):
    name: str = Field(min_length=1, max_length=120)
    value: str | bool | int | float | None


class ProposedLearningEvent(_ApplicationModel):
    event_id: str = Field(min_length=1, max_length=160)
    event_type: ProposedEventType
    turn_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=240)
    attributes: tuple[EventAttribute, ...] = Field(max_length=24)


class TeacherTurnCommand(_ApplicationModel):
    schema_version: Literal["teacher-application.v2"] = (
        TEACHER_APPLICATION_SCHEMA_VERSION
    )
    turn_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=240)
    session_id: str = Field(min_length=1, max_length=160)
    expected_learning_position: str = Field(min_length=1, max_length=240)
    latest_user_message: str = Field(min_length=1, max_length=20_000)
    explicit_action: StudentMove | str | None = None
    channel: str = Field(default="learning", min_length=1, max_length=80)
    evidence: tuple[TeacherEvidence, ...] = Field(default=(), max_length=32)
    recent_conversation: tuple[ConversationTurn, ...] = Field(default=(), max_length=24)


class LearnerContextSnapshot(_ApplicationModel):
    position_token: str = Field(min_length=1, max_length=240)
    context: LearnerContext


class TurnCommitPlan(_ApplicationModel):
    turn_id: str = Field(min_length=1, max_length=160)
    expected_learning_position: str = Field(min_length=1, max_length=240)
    status: TurnCommitStatus
    events: tuple[ProposedLearningEvent, ...] = Field(max_length=8)
    committed_event_ids: tuple[str, ...] = Field(default=(), max_length=8)


class TeacherTurnTransaction(_ApplicationModel):
    turn_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=240)
    command_fingerprint: str = Field(min_length=1, max_length=160)
    status: TurnTransactionStatus
    commit_plan: TurnCommitPlan


class TeacherApplicationReceipt(_ApplicationModel):
    schema_version: Literal["teacher-application.v2"] = (
        TEACHER_APPLICATION_SCHEMA_VERSION
    )
    turn_id: str = Field(min_length=1, max_length=160)
    student_move: StudentMove | None
    teaching_move: TeacherMove | None
    pedagogy_decision: DecisionReason | None
    runtime_result_identity: str | None = Field(default=None, max_length=160)
    provider_call_count: int = Field(ge=0, le=1)
    proposed_event_count: int = Field(ge=0, le=8)
    commit_status: TurnCommitStatus
    elapsed_ms: float = Field(ge=0.0)


class TeacherTurnApplicationResult(_ApplicationModel):
    transaction: TeacherTurnTransaction
    runtime_result: TeacherTurnResult | None
    receipt: TeacherApplicationReceipt
    idempotent_replay: bool = False


class TeacherApplicationError(Exception):
    """Fail-closed application error with an auditable empty/pending plan."""

    def __init__(
        self,
        code: str,
        *,
        transaction: TeacherTurnTransaction,
        receipt: TeacherApplicationReceipt,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.transaction = transaction
        self.receipt = receipt
        self.details = details or {}


__all__ = [
    "EventAttribute",
    "LearnerContextSnapshot",
    "ProposedEventType",
    "ProposedLearningEvent",
    "TEACHER_APPLICATION_SCHEMA_VERSION",
    "TeacherApplicationError",
    "TeacherApplicationReceipt",
    "TeacherTurnApplicationResult",
    "TeacherTurnCommand",
    "TeacherTurnTransaction",
    "TurnCommitPlan",
    "TurnCommitStatus",
    "TurnTransactionStatus",
]
