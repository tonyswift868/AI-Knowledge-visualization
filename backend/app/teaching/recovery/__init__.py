"""Public API for misconception diagnosis and teaching recovery."""

from .engine import (
    TeachingRecoveryEngine,
    diagnose_misconception,
    plan_teaching_recovery,
)
from .models import (
    MAX_RECOVERY_ATTEMPTS,
    TEACHING_RECOVERY_SCHEMA_VERSION,
    AnswerOutcome,
    AnswerResult,
    DiagnosisCategory,
    ErrorKindHint,
    MisconceptionDiagnosis,
    RecoveryPlan,
    RecoveryStrategy,
    RecoveryTrace,
    TeachingRecoveryDecision,
    TeachingRecoveryRequest,
)


__all__ = [
    "MAX_RECOVERY_ATTEMPTS",
    "TEACHING_RECOVERY_SCHEMA_VERSION",
    "AnswerOutcome",
    "AnswerResult",
    "DiagnosisCategory",
    "ErrorKindHint",
    "MisconceptionDiagnosis",
    "RecoveryPlan",
    "RecoveryStrategy",
    "RecoveryTrace",
    "TeachingRecoveryDecision",
    "TeachingRecoveryEngine",
    "TeachingRecoveryRequest",
    "diagnose_misconception",
    "plan_teaching_recovery",
]
