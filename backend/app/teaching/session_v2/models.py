"""Endpoint-neutral contracts for an ordered classroom session."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..application_v2 import TeacherTurnApplicationResult, TeacherTurnCommand


CLASSROOM_SESSION_SCHEMA_VERSION = "classroom-session.v2"


class _SessionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClassroomSessionId(_SessionModel):
    value: str = Field(min_length=1, max_length=160)


class ClassroomSessionState(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    ABORTED = "aborted"


class ClassroomSessionAction(str, Enum):
    CREATED = "created"
    ACTIVATED = "activated"
    TURN_STARTED = "turn_started"
    TURN_COMMITTED = "turn_committed"
    COMPLETED = "completed"


class SessionTurnCommand(_SessionModel):
    sequence_number: int = Field(ge=1)
    turn: TeacherTurnCommand


class PendingTurnRecovery(_SessionModel):
    sequence_number: int = Field(ge=1)
    command: TeacherTurnCommand
    application_result: TeacherTurnApplicationResult


class CompletedSessionTurn(_SessionModel):
    sequence_number: int = Field(ge=1)
    command: TeacherTurnCommand
    application_result: TeacherTurnApplicationResult


class SessionRecoverySnapshot(_SessionModel):
    schema_version: Literal["classroom-session.v2"] = CLASSROOM_SESSION_SCHEMA_VERSION
    session_id: str = Field(min_length=1, max_length=160)
    state: ClassroomSessionState
    learning_position: str = Field(min_length=1, max_length=240)
    next_sequence_number: int = Field(default=1, ge=1)
    pending_turn: PendingTurnRecovery | None = None
    completed_turns: tuple[CompletedSessionTurn, ...] = Field(default=(), max_length=500)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_turn_order(self) -> "SessionRecoverySnapshot":
        sequences = tuple(item.sequence_number for item in self.completed_turns)
        if sequences != tuple(range(1, len(sequences) + 1)):
            raise ValueError("completed turns must be contiguous and ordered")
        if self.next_sequence_number != len(sequences) + 1:
            raise ValueError("next sequence must follow completed turns")
        if (
            self.pending_turn is not None
            and self.pending_turn.sequence_number != self.next_sequence_number
        ):
            raise ValueError("pending turn must use the next sequence")
        return self


class ClassroomSessionReceipt(_SessionModel):
    schema_version: Literal["classroom-session.v2"] = CLASSROOM_SESSION_SCHEMA_VERSION
    session_id: str = Field(min_length=1, max_length=160)
    action: ClassroomSessionAction
    state: ClassroomSessionState
    learning_position: str = Field(min_length=1, max_length=240)
    next_sequence_number: int = Field(ge=1)
    turn_id: str | None = Field(default=None, max_length=160)
    revision: int = Field(ge=0)
    idempotent_replay: bool = False


class ClassroomSessionError(Exception):
    """Fail-closed session error with the latest durable snapshot."""

    def __init__(
        self,
        code: str,
        *,
        snapshot: SessionRecoverySnapshot | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.snapshot = snapshot
        self.details = details or {}


__all__ = [
    "CLASSROOM_SESSION_SCHEMA_VERSION",
    "ClassroomSessionAction",
    "ClassroomSessionError",
    "ClassroomSessionId",
    "ClassroomSessionReceipt",
    "ClassroomSessionState",
    "CompletedSessionTurn",
    "PendingTurnRecovery",
    "SessionRecoverySnapshot",
    "SessionTurnCommand",
]
