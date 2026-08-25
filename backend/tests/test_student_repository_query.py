from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.teaching.contracts import (
    AssessmentState,
    CurrentTaskKind,
    ExampleDepth,
    ExampleStrategy,
    HintDependency,
    MasteryState,
    StudentMove,
    TeacherMove,
)
from app.teaching.learning_state import LearningStage
from app.teaching.pedagogy import plan_teaching
from app.teaching.student_model import StudentLearningEvent, StudentLearningEventType
from app.teaching.student_repository import (
    StudentEventRepository,
    StudentProgressQuery,
    StudentRepositoryLearnerContextPort,
)


BASE_TIME = datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc)


def _event(
    event_id: str,
    event_type: StudentLearningEventType,
    *,
    seconds: int = 0,
    concept_id: str = "hash-map",
    **changes: object,
) -> StudentLearningEvent:
    return StudentLearningEvent(
        event_id=event_id,
        student_id="student-a",
        session_id="session-a",
        concept_id=concept_id,
        event_type=event_type,
        occurred_at=BASE_TIME + timedelta(seconds=seconds),
        **changes,
    )


def _query_with_explainable_history(tmp_path: Path) -> StudentProgressQuery:
    repository = StudentEventRepository(tmp_path / "repository")
    repository.append_events(
        [
            _event("self", StudentLearningEventType.SELF_ATTESTED_MASTERY),
            _event(
                "misconception",
                StudentLearningEventType.MISCONCEPTION_SIGNAL,
                seconds=1,
                detail="capacity is the same as size",
            ),
            _event(
                "attempt",
                StudentLearningEventType.ATTEMPT,
                seconds=2,
                attempt_id="attempt-1",
            ),
            _event(
                "failed",
                StudentLearningEventType.ASSESSMENT_RESULT,
                seconds=3,
                attempt_id="attempt-1",
                assessment_score=0.25,
                assessment_passed=False,
                detail="used size as the resize threshold",
                evidence_ids=("assessment-proof-failed",),
            ),
            _event(
                "verified",
                StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
                seconds=4,
                evidence_ids=("assessment-proof-passed",),
            ),
            _event("hint-1", StudentLearningEventType.HINT_REQUESTED, seconds=5),
            _event("hint-2", StudentLearningEventType.HINT_REQUESTED, seconds=6),
            _event("hint-3", StudentLearningEventType.HINT_REQUESTED, seconds=7),
            _event(
                "teacher-move",
                StudentLearningEventType.TEACHING_MOVE,
                seconds=8,
                teacher_move=TeacherMove.EXPLAIN,
            ),
            _event(
                "example",
                StudentLearningEventType.EXAMPLE_USED,
                seconds=9,
                example_id="hash-code-example",
                example_strategy=ExampleStrategy.CODE,
                example_depth=ExampleDepth.LEVEL_3_CODE,
                example_effective=True,
            ),
        ]
    )
    repository.create_checkpoint(
        student_id="student-a", session_id="session-a", force=True
    )
    return StudentProgressQuery(repository)


def test_mastery_ledger_explains_attestation_failure_and_verified_evidence(
    tmp_path: Path,
) -> None:
    query = _query_with_explainable_history(tmp_path)

    ledger = query.mastery_ledger(
        student_id="student-a", session_id="session-a", concept_id="hash-map"
    )

    assert ledger.current_stage is LearningStage.INDEPENDENTLY_VERIFIED
    assert ledger.self_attested_event_ids == ("self",)
    assert ledger.failed_assessment_event_ids == ("failed",)
    assert ledger.verified_event_ids == ("verified",)
    assert ledger.assessment_evidence_ids == (
        "assessment-proof-passed",
        "assessment-proof-failed",
    )
    assert "misconception" in ledger.source_event_ids
    source_record_ids = tuple(record.event_id for record in ledger.source_events)
    assert source_record_ids == ledger.source_event_ids
    assert ledger.misconception_event_ids == ("misconception",)
    assert ledger.recent_attempt_event_ids == ("attempt", "failed")


def test_progress_history_and_attempt_query_are_bounded(tmp_path: Path) -> None:
    query = _query_with_explainable_history(tmp_path)

    history = query.concept_history(
        student_id="student-a",
        session_id="session-a",
        concept_id="hash-map",
        transition_limit=2,
        attempt_limit=1,
    )

    assert history.state.stage is LearningStage.INDEPENDENTLY_VERIFIED
    assert len(history.transitions) == 2
    assert tuple(record.event_id for record in history.recent_attempts) == ("failed",)
    assert history.misconception_history == ("capacity is the same as size",)
    summary = query.attempt_summary(
        student_id="student-a", session_id="session-a", concept_id="hash-map"
    )
    assert summary.attempt_count == 1
    assert summary.assessment_count == 1
    assert summary.consecutive_failures == 1
    with pytest.raises(ValueError, match="between 1 and 200"):
        query.concept_history(
            student_id="student-a",
            session_id="session-a",
            concept_id="hash-map",
            transition_limit=201,
        )


def test_teacher_port_exposes_only_frozen_learner_context_and_attempt_summary(
    tmp_path: Path,
) -> None:
    query = _query_with_explainable_history(tmp_path)
    port = StudentRepositoryLearnerContextPort(query)

    projection = port.build_projection(
        student_id="student-a",
        session_id="session-a",
        current_concept="hash-map",
        student_move=StudentMove.REQUEST_ASSESSMENT,
        learning_goal="understand hashing",
        learner_level="beginner",
        current_task=CurrentTaskKind.ASSESSMENT,
        next_concept="collision resolution",
        next_concept_relationship="uses the same bucket model",
    )
    context = projection.learner_context

    assert context.mastery_state is MasteryState.INDEPENDENTLY_VERIFIED
    assert context.assessment_state is AssessmentState.REQUESTED
    assert context.evidence_available is True
    assert context.relevant_evidence_ids == (
        "assessment-proof-failed",
        "assessment-proof-passed",
    )
    assert projection.attempt_summary.attempt_count == 1
    assert context.hint_dependency is HintDependency.PERSISTENT
    assert context.recent_teaching_moves == (TeacherMove.EXPLAIN,)
    assert context.recent_examples[0].example_id == "hash-code-example"
    assert plan_teaching(context).focus_concept == "hash-map"
    exposed_fields = set(context.model_dump())
    assert not exposed_fields.intersection(
        {
            "psychological_profile",
            "personality",
            "emotion",
            "diagnosis",
            "database_path",
            "raw_events",
        }
    )


def test_empty_repository_projects_unknown_state_without_guessing(tmp_path: Path) -> None:
    query = StudentProgressQuery(StudentEventRepository(tmp_path / "repository"))
    port = StudentRepositoryLearnerContextPort(query)

    context = port.build_context(
        student_id="student-a",
        session_id="session-a",
        current_concept="graph",
        student_move="continue",
    )

    assert context.mastery_state is MasteryState.UNKNOWN
    assert context.evidence_available is False
    assert context.recent_mistakes == ()
    assert context.known_misconceptions == ()
