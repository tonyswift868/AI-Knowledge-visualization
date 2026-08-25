from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.teaching.review_memory import (
    ReviewEvaluationMappingPolicy,
    ReviewMemoryItem,
    ReviewObservationOutcome,
    ReviewRating,
    StudentRepositoryReviewMemoryPort,
)
from app.teaching.student_model import StudentLearningEvent, StudentLearningEventType
from app.teaching.student_repository import StudentEventCursor, StudentEventRepository


NOW = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)


def _event(
    event_id: str,
    event_type: StudentLearningEventType,
    *,
    seconds: int,
    **changes: object,
) -> StudentLearningEvent:
    return StudentLearningEvent(
        event_id=event_id,
        student_id="student-a",
        session_id="session-a",
        concept_id="hash-map",
        event_type=event_type,
        occurred_at=NOW + timedelta(seconds=seconds),
        **changes,
    )


def _repository(tmp_path: Path) -> StudentEventRepository:
    repository = StudentEventRepository(tmp_path / "student-repository")
    repository.append_events(
        [
            _event(
                "incorrect",
                StudentLearningEventType.ANSWER_INCORRECT,
                seconds=1,
                attempt_id="attempt-1",
                detail="wrong bucket",
            ),
            _event(
                "partial",
                StudentLearningEventType.ANSWER_PARTIAL,
                seconds=2,
                attempt_id="attempt-2",
            ),
            _event("hint", StudentLearningEventType.HINT_REQUESTED, seconds=3),
            _event(
                "correct",
                StudentLearningEventType.ANSWER_CORRECT,
                seconds=4,
                attempt_id="attempt-3",
                evidence_ids=("answer-proof",),
            ),
            _event(
                "assessment-hard",
                StudentLearningEventType.ASSESSMENT_RESULT,
                seconds=5,
                attempt_id="attempt-4",
                assessment_score=0.7,
                assessment_passed=True,
                evidence_ids=("assessment-proof",),
            ),
            _event(
                "verified-mastery",
                StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
                seconds=6,
                evidence_ids=("verified-proof",),
            ),
        ]
    )
    return repository


def test_port_maps_repository_events_to_review_proposals_only(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    port = StudentRepositoryReviewMemoryPort(repository)

    page = port.propose_observations(
        student_id="student-a",
        session_id="session-a",
        item_id="item-1",
        concept_id="hash-map",
        question_id="question-1",
        macro_round_index=1,
    )

    assert tuple(proposal.observation.outcome for proposal in page.proposals) == (
        ReviewObservationOutcome.FAILED,
        ReviewObservationOutcome.PARTIAL,
        ReviewObservationOutcome.HINT_DEPENDENT,
        ReviewObservationOutcome.RECALLED,
        ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY,
    )
    assert all(proposal.changes_mastery is False for proposal in page.proposals)
    assert all(not proposal.proposed_mastery_event_ids for proposal in page.proposals)
    assert tuple(
        proposal.observation.source_event_ids[0] for proposal in page.proposals
    ) == ("incorrect", "partial", "hint", "correct", "assessment-hard")
    assert "verified-mastery" not in {
        event_id
        for proposal in page.proposals
        for event_id in proposal.observation.source_event_ids
    }
    assert len(repository.list_events(student_id="student-a").records) == 6


def test_repository_correct_result_maps_to_good_not_easy(tmp_path: Path) -> None:
    proposal = StudentRepositoryReviewMemoryPort(_repository(tmp_path)).propose_observations(
        student_id="student-a",
        session_id="session-a",
        item_id="item-1",
        concept_id="hash-map",
        macro_round_index=1,
    ).proposals[3]
    item = ReviewMemoryItem(
        item_id="item-1",
        student_id="student-a",
        concept_id="hash-map",
    )

    decision = ReviewEvaluationMappingPolicy().map(item, proposal.observation)

    assert proposal.observation.outcome is ReviewObservationOutcome.RECALLED
    assert decision.rating is ReviewRating.GOOD


def test_port_paginates_bounded_repository_reads(tmp_path: Path) -> None:
    port = StudentRepositoryReviewMemoryPort(_repository(tmp_path))
    first = port.propose_observations(
        student_id="student-a",
        session_id="session-a",
        item_id="item-1",
        concept_id="hash-map",
        macro_round_index=1,
        limit=2,
    )
    second = port.propose_observations(
        student_id="student-a",
        session_id="session-a",
        item_id="item-1",
        concept_id="hash-map",
        macro_round_index=1,
        cursor=first.next_cursor,
        limit=2,
        same_round_attempt_start=3,
    )

    assert len(first.proposals) == 2
    assert first.next_cursor is not None
    assert isinstance(first.next_cursor, StudentEventCursor)
    assert len(second.proposals) == 2
    assert second.proposals[0].observation.same_round_attempt == 3


def test_integration_port_has_no_mastery_mutation_authority() -> None:
    source = inspect.getsource(
        __import__(
            "app.teaching.review_memory.port",
            fromlist=["StudentRepositoryReviewMemoryPort"],
        )
    )
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "MasteryState" not in imported_names
    assert "LearningStage" not in imported_names
    assert "LearningStateReducer" not in imported_names
    assert ".append_events(" not in source
    assert "INDEPENDENTLY_VERIFIED_MASTERY" not in source
