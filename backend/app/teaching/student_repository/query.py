"""Bounded, explainable progress queries over durable student events."""

from __future__ import annotations

from collections.abc import Iterable

from ..learning_state import ConceptLearningState, ReducerResult
from ..student_model import StudentLearningEventType
from .models import (
    AttemptSummary,
    ConceptProgressHistory,
    MasteryEvidenceLedger,
    StudentEventRecord,
)
from .repository import StudentEventRepository


_ATTEMPT_EVENT_TYPES = (
    StudentLearningEventType.ATTEMPT,
    StudentLearningEventType.ANSWER_CORRECT,
    StudentLearningEventType.ANSWER_PARTIAL,
    StudentLearningEventType.ANSWER_INCORRECT,
    StudentLearningEventType.ASSESSMENT_RESULT,
)


def _unique_tail(values: Iterable[str], limit: int) -> tuple[str, ...]:
    unique: list[str] = []
    for value in values:
        if value in unique:
            unique.remove(value)
        unique.append(value)
    return tuple(unique[-limit:])


class StudentProgressQuery:
    """Read model for teacher-safe progress, history, and evidence explanations."""

    def __init__(self, repository: StudentEventRepository) -> None:
        self._repository = repository

    def snapshot(self, *, student_id: str, session_id: str) -> ReducerResult:
        return self._repository.rebuild_snapshot(
            student_id=student_id,
            session_id=session_id,
        )

    def state(
        self,
        *,
        student_id: str,
        session_id: str,
        concept_id: str,
    ) -> ConceptLearningState:
        normalized_concept_id = concept_id.strip()
        if not normalized_concept_id:
            raise ValueError("concept_id must be non-empty")
        result = self.snapshot(student_id=student_id, session_id=session_id)
        return result.concept(normalized_concept_id) or ConceptLearningState(
            concept_id=normalized_concept_id
        )

    def _recent_by_type(
        self,
        *,
        student_id: str,
        session_id: str,
        concept_id: str,
        event_type: StudentLearningEventType,
        limit: int,
    ) -> tuple[StudentEventRecord, ...]:
        return self._repository.list_recent_events(
            student_id=student_id,
            session_id=session_id,
            concept_id=concept_id,
            event_type=event_type,
            limit=limit,
        )

    def recent_attempts(
        self,
        *,
        student_id: str,
        session_id: str,
        concept_id: str,
        limit: int = 20,
    ) -> tuple[StudentEventRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        records = [
            record
            for event_type in _ATTEMPT_EVENT_TYPES
            for record in self._recent_by_type(
                student_id=student_id,
                session_id=session_id,
                concept_id=concept_id,
                event_type=event_type,
                limit=limit,
            )
        ]
        records.sort(key=lambda record: record.sequence)
        return tuple(records[-limit:])

    def mastery_ledger(
        self,
        *,
        student_id: str,
        session_id: str,
        concept_id: str,
        event_limit: int = 64,
        source_limit: int = 128,
        result: ReducerResult | None = None,
    ) -> MasteryEvidenceLedger:
        if not 1 <= event_limit <= 200:
            raise ValueError("event_limit must be between 1 and 200")
        if not 1 <= source_limit <= 256:
            raise ValueError("source_limit must be between 1 and 256")
        normalized_concept_id = concept_id.strip()
        if not normalized_concept_id:
            raise ValueError("concept_id must be non-empty")
        projection = result or self.snapshot(
            student_id=student_id,
            session_id=session_id,
        )
        state = projection.concept(normalized_concept_id) or ConceptLearningState(
            concept_id=normalized_concept_id
        )
        self_attested = self._recent_by_type(
            student_id=student_id,
            session_id=session_id,
            concept_id=normalized_concept_id,
            event_type=StudentLearningEventType.SELF_ATTESTED_MASTERY,
            limit=event_limit,
        )
        verified_candidates = self._recent_by_type(
            student_id=student_id,
            session_id=session_id,
            concept_id=normalized_concept_id,
            event_type=StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
            limit=event_limit,
        )
        verified = tuple(
            record for record in verified_candidates if record.event.evidence_ids
        )
        assessments = self._recent_by_type(
            student_id=student_id,
            session_id=session_id,
            concept_id=normalized_concept_id,
            event_type=StudentLearningEventType.ASSESSMENT_RESULT,
            limit=event_limit,
        )
        misconceptions = self._recent_by_type(
            student_id=student_id,
            session_id=session_id,
            concept_id=normalized_concept_id,
            event_type=StudentLearningEventType.MISCONCEPTION_SIGNAL,
            limit=event_limit,
        )
        attempts = self.recent_attempts(
            student_id=student_id,
            session_id=session_id,
            concept_id=normalized_concept_id,
            limit=min(event_limit, 100),
        )
        transitions = tuple(
            transition
            for transition in projection.transitions
            if transition.concept_id == normalized_concept_id
        )
        source_event_ids = _unique_tail(
            (
                event_id
                for transition in transitions
                for event_id in transition.source_event_ids
            ),
            source_limit,
        )
        source_event_ids = _unique_tail(
            (
                *source_event_ids,
                *(record.event_id for record in self_attested),
                *(record.event_id for record in verified_candidates),
                *(record.event_id for record in assessments),
                *(record.event_id for record in misconceptions),
                *(record.event_id for record in attempts),
            ),
            source_limit,
        )
        assessment_evidence_ids = _unique_tail(
            (
                evidence_id
                for record in (*verified, *assessments)
                for evidence_id in record.event.evidence_ids
            ),
            source_limit,
        )
        source_records = self._repository.get_events(
            student_id=student_id,
            session_id=session_id,
            event_ids=source_event_ids,
        )
        return MasteryEvidenceLedger(
            student_id=projection.student_id,
            session_id=projection.session_id,
            concept_id=normalized_concept_id,
            current_stage=state.stage,
            confidence=state.confidence,
            source_event_ids=source_event_ids,
            self_attested_event_ids=tuple(record.event_id for record in self_attested),
            verified_event_ids=tuple(record.event_id for record in verified),
            assessment_evidence_ids=assessment_evidence_ids,
            failed_assessment_event_ids=tuple(
                record.event_id
                for record in assessments
                if record.event.assessment_passed is False
            ),
            misconception_event_ids=tuple(record.event_id for record in misconceptions),
            recent_attempt_event_ids=tuple(record.event_id for record in attempts),
            source_events=source_records,
        )

    def concept_history(
        self,
        *,
        student_id: str,
        session_id: str,
        concept_id: str,
        transition_limit: int = 50,
        attempt_limit: int = 20,
    ) -> ConceptProgressHistory:
        if not 1 <= transition_limit <= 200:
            raise ValueError("transition_limit must be between 1 and 200")
        normalized_concept_id = concept_id.strip()
        if not normalized_concept_id:
            raise ValueError("concept_id must be non-empty")
        result = self.snapshot(student_id=student_id, session_id=session_id)
        state = result.concept(normalized_concept_id) or ConceptLearningState(
            concept_id=normalized_concept_id
        )
        transitions = tuple(
            transition
            for transition in result.transitions
            if transition.concept_id == state.concept_id
        )[-transition_limit:]
        attempts = self.recent_attempts(
            student_id=student_id,
            session_id=session_id,
            concept_id=state.concept_id,
            limit=attempt_limit,
        )
        return ConceptProgressHistory(
            student_id=result.student_id,
            session_id=result.session_id,
            concept_id=state.concept_id,
            state=state,
            transitions=transitions,
            misconception_history=state.misconception_history,
            recent_attempts=attempts,
            ledger=self.mastery_ledger(
                student_id=student_id,
                session_id=session_id,
                concept_id=state.concept_id,
                result=result,
            ),
        )

    def attempt_summary(
        self,
        *,
        student_id: str,
        session_id: str,
        concept_id: str,
        result: ReducerResult | None = None,
    ) -> AttemptSummary:
        projection = result or self.snapshot(
            student_id=student_id,
            session_id=session_id,
        )
        state = projection.concept(concept_id)
        if state is None:
            return AttemptSummary()
        return AttemptSummary(
            attempt_count=state.attempt_count,
            correct_count=state.correct_answer_count,
            partial_count=state.partial_answer_count,
            incorrect_count=state.incorrect_answer_count,
            assessment_count=state.assessment_result_count,
            consecutive_failures=state.consecutive_failures,
        )

    def list_sessions(self, *, student_id: str) -> tuple[str, ...]:
        return self._repository.list_sessions(student_id=student_id)


__all__ = ["StudentProgressQuery"]
