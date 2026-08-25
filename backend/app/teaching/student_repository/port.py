"""Teacher-facing adapter backed only by repository query contracts."""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts import (
    AssessmentState,
    CurrentTaskKind,
    HintDependency,
    LearnerContext,
    MasteryState,
    StudentMove,
)
from ..learning_state import ReducerResult
from .models import AttemptSummary, RepositoryLearnerContextProjection
from .query import StudentProgressQuery


class StudentRepositoryLearnerContextPort:
    """Build the frozen Teacher v2 input without exposing persistence details."""

    def __init__(self, query: StudentProgressQuery) -> None:
        self._query = query

    @staticmethod
    def _context_from_result(
        result: ReducerResult,
        *,
        current_concept: str,
        student_move: StudentMove,
        learning_goal: str,
        learner_level: str,
        known_prerequisites: Sequence[str],
        missing_prerequisites: Sequence[str],
        current_task: CurrentTaskKind,
        assessment_state: AssessmentState | None,
        additional_evidence_ids: Sequence[str],
        next_concept: str,
        next_concept_relationship: str,
    ) -> LearnerContext:
        concept_id = current_concept.strip()
        if not concept_id:
            raise ValueError("current_concept must be non-empty")
        state = result.concept(concept_id)
        evidence_ids = tuple(
            dict.fromkeys(
                (
                    *(() if state is None else state.recent_evidence_ids),
                    *additional_evidence_ids,
                )
            )
        )[-32:]
        resolved_assessment_state = assessment_state or (
            AssessmentState.REQUESTED
            if student_move is StudentMove.REQUEST_ASSESSMENT
            else AssessmentState.NOT_REQUESTED
        )
        return LearnerContext(
            current_concept=concept_id,
            learning_goal=learning_goal,
            learner_level=learner_level,
            known_prerequisites=tuple(known_prerequisites),
            missing_prerequisites=tuple(missing_prerequisites),
            mastery_state=(
                MasteryState.UNKNOWN if state is None else state.mastery_state
            ),
            recent_mistakes=(() if state is None else state.recent_mistakes),
            known_misconceptions=(
                () if state is None else state.active_misconceptions
            ),
            hint_dependency=(
                HintDependency.NONE if state is None else state.hint_dependency
            ),
            recent_teaching_moves=(
                () if state is None else state.recent_teaching_moves
            ),
            recent_examples=(() if state is None else state.recent_examples),
            current_task=current_task,
            student_move=student_move,
            evidence_available=bool(evidence_ids),
            relevant_evidence_ids=evidence_ids,
            assessment_state=resolved_assessment_state,
            consecutive_failures=(
                0 if state is None else min(state.consecutive_failures, 20)
            ),
            next_concept=next_concept,
            next_concept_relationship=next_concept_relationship,
        )

    def build_context(
        self,
        *,
        student_id: str,
        session_id: str,
        current_concept: str,
        student_move: StudentMove | str,
        learning_goal: str = "",
        learner_level: str = "",
        known_prerequisites: Sequence[str] = (),
        missing_prerequisites: Sequence[str] = (),
        current_task: CurrentTaskKind = CurrentTaskKind.NONE,
        assessment_state: AssessmentState | None = None,
        additional_evidence_ids: Sequence[str] = (),
        next_concept: str = "",
        next_concept_relationship: str = "",
    ) -> LearnerContext:
        resolved_student_move = StudentMove(student_move)
        result = self._query.snapshot(student_id=student_id, session_id=session_id)
        return self._context_from_result(
            result,
            current_concept=current_concept,
            student_move=resolved_student_move,
            learning_goal=learning_goal,
            learner_level=learner_level,
            known_prerequisites=known_prerequisites,
            missing_prerequisites=missing_prerequisites,
            current_task=current_task,
            assessment_state=assessment_state,
            additional_evidence_ids=additional_evidence_ids,
            next_concept=next_concept,
            next_concept_relationship=next_concept_relationship,
        )

    def attempt_summary(
        self,
        *,
        student_id: str,
        session_id: str,
        concept_id: str,
    ) -> AttemptSummary:
        return self._query.attempt_summary(
            student_id=student_id,
            session_id=session_id,
            concept_id=concept_id,
        )

    def build_projection(
        self,
        *,
        student_id: str,
        session_id: str,
        current_concept: str,
        student_move: StudentMove | str,
        learning_goal: str = "",
        learner_level: str = "",
        known_prerequisites: Sequence[str] = (),
        missing_prerequisites: Sequence[str] = (),
        current_task: CurrentTaskKind = CurrentTaskKind.NONE,
        assessment_state: AssessmentState | None = None,
        additional_evidence_ids: Sequence[str] = (),
        next_concept: str = "",
        next_concept_relationship: str = "",
    ) -> RepositoryLearnerContextProjection:
        result = self._query.snapshot(student_id=student_id, session_id=session_id)
        learner_context = self._context_from_result(
            result,
            current_concept=current_concept,
            student_move=StudentMove(student_move),
            learning_goal=learning_goal,
            learner_level=learner_level,
            known_prerequisites=known_prerequisites,
            missing_prerequisites=missing_prerequisites,
            current_task=current_task,
            assessment_state=assessment_state,
            additional_evidence_ids=additional_evidence_ids,
            next_concept=next_concept,
            next_concept_relationship=next_concept_relationship,
        )
        state = result.concept(current_concept)
        attempt_summary = (
            AttemptSummary()
            if state is None
            else AttemptSummary(
                attempt_count=state.attempt_count,
                correct_count=state.correct_answer_count,
                partial_count=state.partial_answer_count,
                incorrect_count=state.incorrect_answer_count,
                assessment_count=state.assessment_result_count,
                consecutive_failures=state.consecutive_failures,
            )
        )
        return RepositoryLearnerContextProjection(
            learner_context=learner_context,
            attempt_summary=attempt_summary,
        )


__all__ = ["StudentRepositoryLearnerContextPort"]
