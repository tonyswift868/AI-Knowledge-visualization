from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.teaching.learning_state import (
    LearningStage,
    LearningStatePolicy,
    LearningStateReducer,
)
from app.teaching.student_model import StudentLearningEvent, StudentLearningEventType
from app.teaching.student_repository import (
    CheckpointPolicy,
    CheckpointValidationError,
    StudentEventRepository,
    StudentRepositoryCorruptionError,
)


BASE_TIME = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)


def _event(
    event_id: str,
    event_type: StudentLearningEventType,
    *,
    seconds: int = 0,
    **changes: object,
) -> StudentLearningEvent:
    return StudentLearningEvent(
        event_id=event_id,
        student_id="student-a",
        session_id="session-a",
        concept_id="hash-map",
        event_type=event_type,
        occurred_at=BASE_TIME + timedelta(seconds=seconds),
        **changes,
    )


def test_checkpoint_policy_every_n_events_and_session_boundary(tmp_path: Path) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    repository.append_events(
        [
            _event("one", StudentLearningEventType.ANSWER_CORRECT),
            _event("two", StudentLearningEventType.ANSWER_CORRECT, seconds=1),
        ]
    )
    policy = CheckpointPolicy(every_n_events=3)

    assert repository.create_checkpoint(
        student_id="student-a", session_id="session-a", policy=policy
    ) is None
    boundary = repository.create_checkpoint(
        student_id="student-a",
        session_id="session-a",
        policy=policy,
        session_boundary=True,
    )
    assert boundary is not None
    assert boundary.event_count == 2

    repository.append_events(
        [
            _event("three", StudentLearningEventType.ANSWER_CORRECT, seconds=2),
            _event("four", StudentLearningEventType.ANSWER_CORRECT, seconds=3),
            _event("five", StudentLearningEventType.ANSWER_CORRECT, seconds=4),
        ]
    )
    periodic = repository.create_checkpoint(
        student_id="student-a", session_id="session-a", policy=policy
    )
    assert periodic is not None
    assert periodic.event_count == 5
    assert periodic.through_sequence > boundary.through_sequence


def test_checkpoint_survives_restart_and_full_replay_matches_after_later_events(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    repository = StudentEventRepository(root)
    prefix = [
        _event("self", StudentLearningEventType.SELF_ATTESTED_MASTERY),
        _event(
            "failed",
            StudentLearningEventType.ASSESSMENT_RESULT,
            seconds=2,
            assessment_score=0.2,
            assessment_passed=False,
            detail="confused threshold with capacity",
            evidence_ids=("assessment-1",),
        ),
    ]
    repository.append_events(prefix)
    checkpoint = repository.create_checkpoint(
        student_id="student-a", session_id="session-a", force=True
    )
    assert checkpoint is not None

    restarted = StudentEventRepository(root)
    assert restarted.load_latest_checkpoint(
        student_id="student-a", session_id="session-a"
    ) == checkpoint
    before = restarted.rebuild_snapshot(
        student_id="student-a", session_id="session-a"
    ).concept("hash-map")
    assert before is not None
    assert before.stage is LearningStage.LEARNING
    assert before.self_attested_ever is True
    assert before.assessment_fail_count == 1

    verified = _event(
        "verified",
        StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
        seconds=3,
        evidence_ids=("assessment-2",),
    )
    restarted.append_events([verified])
    validation = restarted.validate_checkpoint(checkpoint)
    rebuilt = restarted.rebuild_snapshot(
        student_id="student-a", session_id="session-a"
    )
    direct = LearningStateReducer().reduce(
        [*prefix, verified], student_id="student-a", session_id="session-a"
    )

    assert validation.valid is True
    assert validation.subsequent_event_count == 1
    assert validation.full_replay_equivalent is True
    assert rebuilt == direct
    assert rebuilt.concept("hash-map").stage is LearningStage.INDEPENDENTLY_VERIFIED


def test_checkpoint_digest_and_identity_mismatches_are_explainable(tmp_path: Path) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    repository.append_events(
        [_event("one", StudentLearningEventType.ANSWER_CORRECT)]
    )
    checkpoint = repository.create_checkpoint(
        student_id="student-a", session_id="session-a", force=True
    )
    assert checkpoint is not None

    digest_mismatch = repository.validate_checkpoint(
        replace(checkpoint, event_digest="0" * 64)
    )
    assert digest_mismatch.valid is False
    assert digest_mismatch.reasons == ("event_digest_mismatch",)

    identity_mismatch = repository.validate_checkpoint(
        replace(checkpoint, session_id="other-session")
    )
    assert identity_mismatch.valid is False
    assert "event_count_mismatch" in identity_mismatch.reasons
    assert "checkpoint_replay_mismatch" in identity_mismatch.reasons

    altered_capsule = repository.validate_checkpoint(
        replace(
            checkpoint,
            replay_events=(
                _event(
                    "one",
                    StudentLearningEventType.ANSWER_CORRECT,
                    detail="payload changed without changing reducer state",
                ),
            ),
        )
    )
    assert altered_capsule.valid is False
    assert "replay_capsule_mismatch" in altered_capsule.reasons


def test_corrupted_checkpoint_is_rejected_before_rebuild(tmp_path: Path) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    repository.append_events(
        [_event("one", StudentLearningEventType.ANSWER_CORRECT)]
    )
    repository.create_checkpoint(
        student_id="student-a", session_id="session-a", force=True
    )

    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("DROP TRIGGER student_checkpoints_no_update")
        connection.execute(
            "UPDATE student_checkpoints SET snapshot_json = snapshot_json || 'x'"
        )

    with pytest.raises(StudentRepositoryCorruptionError, match="checksum mismatch"):
        repository.load_latest_checkpoint(
            student_id="student-a", session_id="session-a"
        )
    with pytest.raises(StudentRepositoryCorruptionError, match="checksum mismatch"):
        repository.rebuild_snapshot(student_id="student-a", session_id="session-a")


def test_rebuild_rejects_validly_encoded_but_mismatched_checkpoint(tmp_path: Path) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    repository.append_events(
        [_event("one", StudentLearningEventType.ANSWER_CORRECT)]
    )
    checkpoint = repository.create_checkpoint(
        student_id="student-a", session_id="session-a", force=True
    )
    assert checkpoint is not None

    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("DROP TRIGGER student_checkpoints_no_update")
        connection.execute(
            "UPDATE student_checkpoints SET event_digest = ?",
            ("f" * 64,),
        )

    with pytest.raises(CheckpointValidationError, match="event_digest_mismatch"):
        repository.rebuild_snapshot(student_id="student-a", session_id="session-a")


def test_same_boundary_cannot_silently_replace_a_different_reducer_checkpoint(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    repository = StudentEventRepository(root)
    repository.append_events(
        [_event("one", StudentLearningEventType.ANSWER_CORRECT)]
    )
    repository.create_checkpoint(
        student_id="student-a", session_id="session-a", force=True
    )
    changed_policy_repository = StudentEventRepository(
        root,
        reducer=LearningStateReducer(
            LearningStatePolicy(correct_confidence_delta=0.4)
        ),
    )

    with pytest.raises(CheckpointValidationError, match="different reducer result"):
        changed_policy_repository.create_checkpoint(
            student_id="student-a", session_id="session-a", force=True
        )


def test_failed_assessment_after_verified_checkpoint_preserves_verified_state(
    tmp_path: Path,
) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    verified = _event(
        "verified",
        StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
        evidence_ids=("verified-proof",),
    )
    repository.append_events([verified])
    checkpoint = repository.create_checkpoint(
        student_id="student-a", session_id="session-a", force=True
    )
    assert checkpoint is not None
    failed = _event(
        "later-failure",
        StudentLearningEventType.ASSESSMENT_RESULT,
        seconds=1,
        assessment_score=0.1,
        assessment_passed=False,
        evidence_ids=("failed-proof",),
    )
    repository.append_events([failed])

    validation = repository.validate_checkpoint(checkpoint)
    state = repository.rebuild_snapshot(
        student_id="student-a", session_id="session-a"
    ).concept("hash-map")

    assert validation.full_replay_equivalent is True
    assert state is not None
    assert state.stage is LearningStage.INDEPENDENTLY_VERIFIED
    assert state.assessment_fail_count == 1
    assert state.verification_evidence_ids == ("verified-proof",)
