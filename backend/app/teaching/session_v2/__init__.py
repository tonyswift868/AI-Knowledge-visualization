"""Public API for Classroom Session Runtime v2."""

from .adapters import StudentStateLearningEventSink
from .harness import FakeClassroomHarness
from .models import (
    CLASSROOM_SESSION_SCHEMA_VERSION,
    AssessmentSubmission,
    ClassroomSessionAction,
    ClassroomSessionError,
    ClassroomSessionId,
    ClassroomSessionReceipt,
    ClassroomSessionState,
    CompletedSessionTurn,
    EvaluationProposal,
    EvaluationRequest,
    PendingTurnRecovery,
    RecoveryDecision,
    RecoveryOutcome,
    SessionRecoverySnapshot,
    SessionTurnCommand,
)
from .ports import (
    ClassroomSessionStorePort,
    EvaluationPort,
    LearningPositionPort,
    StudentStatePort,
)
from .service import ClassroomSessionRuntime
from .stores import InMemoryClassroomSessionStore


__all__ = [
    "CLASSROOM_SESSION_SCHEMA_VERSION",
    "AssessmentSubmission",
    "ClassroomSessionAction",
    "ClassroomSessionError",
    "ClassroomSessionId",
    "ClassroomSessionReceipt",
    "ClassroomSessionRuntime",
    "ClassroomSessionState",
    "ClassroomSessionStorePort",
    "CompletedSessionTurn",
    "EvaluationPort",
    "EvaluationProposal",
    "EvaluationRequest",
    "FakeClassroomHarness",
    "InMemoryClassroomSessionStore",
    "LearningPositionPort",
    "PendingTurnRecovery",
    "RecoveryDecision",
    "RecoveryOutcome",
    "SessionRecoverySnapshot",
    "SessionTurnCommand",
    "StudentStateLearningEventSink",
    "StudentStatePort",
]
