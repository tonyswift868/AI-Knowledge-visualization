"""Account-scoped native classroom; page construction is deliberately lazy."""

from .domain import (
    BlockResolver,
    ErrorEpisode,
    EvaluationResult,
    EvidenceEvent,
    LearningContract,
    MisconceptionRecord,
    contract_for_task,
    RequiredEvidence,
    TutorCheckpoint,
    VisibleObjective,
    VerificationItem,
    validate_evaluation,
)
from .evaluator import EvaluationCommit, EvaluationError, EvaluationService, parse_evaluation
from .verification import VerificationScheduler
from .recap import build_session_recap


def create_page(context, parent=None):
    from .page import create_page as build
    return build(context, parent)


__all__ = [
    "BlockResolver", "ErrorEpisode", "EvaluationResult", "EvidenceEvent",
    "LearningContract", "MisconceptionRecord", "RequiredEvidence", "TutorCheckpoint", "VerificationItem", "VisibleObjective", "contract_for_task",
    "validate_evaluation", "EvaluationCommit", "EvaluationError", "EvaluationService",
    "parse_evaluation", "VerificationScheduler", "build_session_recap", "create_page",
]
