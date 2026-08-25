"""Read-only StudentEventRepository to ReviewMemory proposal seam."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..contracts import HintDependency
from ..student_model import StudentLearningEventType
from ..student_repository import StudentEventCursor, StudentEventRepository
from .models import (
    ReviewMemoryEventProposal,
    ReviewObservation,
    ReviewObservationOutcome,
)


@dataclass(frozen=True, slots=True)
class ReviewMemoryProposalPage:
    proposals: tuple[ReviewMemoryEventProposal, ...]
    next_cursor: StudentEventCursor | None


def _proposal_id(student_id: str, item_id: str, event_id: str) -> str:
    return hashlib.sha256(
        f"review-memory:{student_id}:{item_id}:{event_id}".encode("utf-8")
    ).hexdigest()


class StudentRepositoryReviewMemoryPort:
    """Proposes review observations; it never writes state or mastery."""

    def __init__(self, repository: StudentEventRepository) -> None:
        self._repository = repository

    @staticmethod
    def _outcome(
        event_type: StudentLearningEventType,
        score: float | None,
        passed: bool | None,
    ) -> ReviewObservationOutcome | None:
        if event_type is StudentLearningEventType.ANSWER_INCORRECT:
            return ReviewObservationOutcome.FAILED
        if event_type is StudentLearningEventType.ANSWER_PARTIAL:
            return ReviewObservationOutcome.PARTIAL
        if event_type is StudentLearningEventType.HINT_REQUESTED:
            return ReviewObservationOutcome.HINT_DEPENDENT
        if event_type is StudentLearningEventType.ANSWER_CORRECT:
            return ReviewObservationOutcome.RECALLED
        if event_type is not StudentLearningEventType.ASSESSMENT_RESULT:
            return None
        if passed is False:
            return ReviewObservationOutcome.FAILED
        if passed is True:
            return (
                ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY
                if score is not None and score < 0.8
                else ReviewObservationOutcome.RECALLED
            )
        if score is None:
            return None
        if score < 0.4:
            return ReviewObservationOutcome.FAILED
        if score < 0.8:
            return ReviewObservationOutcome.PARTIAL
        return ReviewObservationOutcome.RECALLED

    def propose_observations(
        self,
        *,
        student_id: str,
        session_id: str,
        item_id: str,
        concept_id: str,
        macro_round_index: int,
        question_id: str | None = None,
        cursor: StudentEventCursor | None = None,
        limit: int = 100,
        same_round_attempt_start: int = 1,
    ) -> ReviewMemoryProposalPage:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if same_round_attempt_start < 1:
            raise ValueError("same_round_attempt_start must be positive")
        page = self._repository.list_events(
            student_id=student_id,
            session_id=session_id,
            concept_id=concept_id,
            cursor=cursor,
            limit=limit,
        )
        proposals: list[ReviewMemoryEventProposal] = []
        for record in page.records:
            event = record.event
            outcome = self._outcome(
                event.event_type,
                event.assessment_score,
                event.assessment_passed,
            )
            if outcome is None:
                continue
            hint_dependent = outcome is ReviewObservationOutcome.HINT_DEPENDENT
            observation = ReviewObservation(
                observation_id=f"repo:{record.sequence}:{event.event_id}",
                student_id=event.student_id,
                item_id=item_id,
                concept_id=event.concept_id,
                question_id=question_id,
                outcome=outcome,
                observed_at=event.occurred_at,
                evidence_ids=event.evidence_ids,
                source_event_ids=(event.event_id,),
                assessment_id=event.attempt_id,
                hint_used=hint_dependent,
                hint_dependency=(
                    HintDependency.RECENT if hint_dependent else HintDependency.NONE
                ),
                confidence=event.confidence,
                macro_round_index=macro_round_index,
                same_round_attempt=same_round_attempt_start + len(proposals),
            )
            proposals.append(
                ReviewMemoryEventProposal(
                    proposal_id=_proposal_id(student_id, item_id, event.event_id),
                    observation=observation,
                )
            )
        return ReviewMemoryProposalPage(
            proposals=tuple(proposals),
            next_cursor=page.next_cursor,
        )


__all__ = ["ReviewMemoryProposalPage", "StudentRepositoryReviewMemoryPort"]
