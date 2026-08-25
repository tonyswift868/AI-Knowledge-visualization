from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone

import pytest

from app.teaching.contracts import ExampleDepth, ExampleStrategy, HintDependency, TeacherMove
from app.teaching.learning_state import (
    ConceptLearningState,
    LearningStage,
    LearningStatePolicy,
    LearningStateReducer,
)
from app.teaching.student_model import (
    DuplicateEventConflictError,
    OUT_OF_ORDER_POLICY,
    StudentLearningEvent,
    StudentLearningEventType,
)


BASE_TIME = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def _event(
    event_id: str,
    event_type: StudentLearningEventType,
    *,
    seconds: int = 0,
    student_id: str = "student-a",
    session_id: str = "session-a",
    concept_id: str = "hash-map",
    **changes: object,
) -> StudentLearningEvent:
    return StudentLearningEvent(
        event_id=event_id,
        student_id=student_id,
        session_id=session_id,
        concept_id=concept_id,
        event_type=event_type,
        occurred_at=BASE_TIME + timedelta(seconds=seconds),
        **changes,
    )


def _reduce(
    events: list[StudentLearningEvent],
    *,
    policy: LearningStatePolicy | None = None,
):
    return LearningStateReducer(policy).reduce(
        events,
        student_id="student-a",
        session_id="session-a",
    )


def test_state_vocabulary_has_four_strict_stages() -> None:
    assert tuple(stage.value for stage in LearningStage) == (
        "unknown",
        "learning",
        "self_attested",
        "independently_verified",
    )
    assert ConceptLearningState("hash-map").stage is LearningStage.UNKNOWN


def test_teacher_activity_and_repeated_correct_never_auto_verify() -> None:
    events = [
        _event(
            "teacher",
            StudentLearningEventType.TEACHING_MOVE,
            teacher_move=TeacherMove.EXPLAIN,
        ),
        *[
            _event(
                f"correct-{index}",
                StudentLearningEventType.ANSWER_CORRECT,
                seconds=index + 1,
            )
            for index in range(5)
        ],
    ]

    state = _reduce(events).concept("hash-map")

    assert state is not None
    assert state.stage is LearningStage.LEARNING
    assert state.correct_answer_count == 5
    assert state.independently_verified is False
    assert state.verification_evidence_ids == ()


def test_self_attested_is_distinct_and_evidenceless_verification_is_rejected() -> None:
    events = [
        _event("self", StudentLearningEventType.SELF_ATTESTED_MASTERY),
        _event(
            "unproven",
            StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
            seconds=1,
        ),
    ]

    result = _reduce(events)
    state = result.concept("hash-map")

    assert state is not None
    assert state.stage is LearningStage.SELF_ATTESTED
    assert state.self_attested_ever is True
    assert state.independently_verified is False
    assert result.transitions[-1].reason == "verification_rejected_missing_evidence"


def test_independent_verification_requires_and_records_evidence() -> None:
    event = _event(
        "verified",
        StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
        evidence_ids=("assessment-proof",),
    )

    state = _reduce([event]).concept("hash-map")

    assert state is not None
    assert state.stage is LearningStage.INDEPENDENTLY_VERIFIED
    assert state.independently_verified is True
    assert state.verification_evidence_ids == ("assessment-proof",)
    assert state.confidence == 1.0


def test_self_attested_then_failed_verification_returns_to_learning() -> None:
    events = [
        _event("self", StudentLearningEventType.SELF_ATTESTED_MASTERY),
        _event(
            "failed",
            StudentLearningEventType.ASSESSMENT_RESULT,
            seconds=1,
            assessment_score=0.2,
            assessment_passed=False,
            detail="confused threshold with capacity",
            evidence_ids=("failed-assessment",),
        ),
    ]

    result = _reduce(events)
    state = result.concept("hash-map")

    assert state is not None
    assert state.stage is LearningStage.LEARNING
    assert state.self_attested_ever is True
    assert state.independently_verified is False
    assert state.assessment_fail_count == 1
    assert state.recent_mistakes == ("confused threshold with capacity",)
    assert result.transitions[-1].reason == (
        "failed_assessment_invalidated_current_self_attestation"
    )


def test_failed_assessment_does_not_erase_independent_verification_history() -> None:
    events = [
        _event(
            "verified",
            StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
            evidence_ids=("original-proof",),
        ),
        _event(
            "failed",
            StudentLearningEventType.ASSESSMENT_RESULT,
            seconds=1,
            assessment_passed=False,
            detail="later regression",
        ),
    ]

    result = _reduce(events)
    state = result.concept("hash-map")

    assert state is not None
    assert state.stage is LearningStage.INDEPENDENTLY_VERIFIED
    assert state.independently_verified is True
    assert state.verification_evidence_ids == ("original-proof",)
    assert state.recent_mistakes == ("later regression",)
    assert result.transitions[-1].reason == (
        "failed_assessment_recorded_verified_state_preserved"
    )


def test_duplicate_event_is_idempotent_and_conflict_fails_closed() -> None:
    attempt = _event("attempt", StudentLearningEventType.ATTEMPT)
    result = _reduce([attempt, attempt, attempt])

    assert result.applied_event_ids == ("attempt",)
    assert result.duplicate_event_ids == ("attempt",)
    assert result.concept("hash-map").attempt_count == 1

    conflict = _event("attempt", StudentLearningEventType.ANSWER_CORRECT)
    with pytest.raises(DuplicateEventConflictError, match="conflicting content"):
        _reduce([attempt, conflict])


def test_out_of_order_replay_is_deterministic_and_transitions_are_ordered() -> None:
    events = [
        _event("last", StudentLearningEventType.ANSWER_CORRECT, seconds=30),
        _event(
            "first",
            StudentLearningEventType.ANSWER_INCORRECT,
            seconds=10,
            detail="first mistake",
        ),
        _event("middle", StudentLearningEventType.HINT_REQUESTED, seconds=20),
    ]

    forward = LearningStateReducer().reduce(
        iter(events),
        student_id="student-a",
        session_id="session-a",
    )
    reverse = _reduce(list(reversed(events)))

    assert forward == reverse
    assert forward.ordering_policy == OUT_OF_ORDER_POLICY
    assert forward.applied_event_ids == ("first", "middle", "last")
    assert tuple(item.order_index for item in forward.transitions) == (0, 1, 2)
    assert forward.concept("hash-map").consecutive_failures == 0


def test_every_transition_has_reason_source_confidence_and_utc_time() -> None:
    result = _reduce([_event("hint", StudentLearningEventType.HINT_REQUESTED)])
    transition = result.transitions[0]

    assert transition.reason == "explicit_hint_dependency_observation"
    assert transition.source_event_ids == ("hint",)
    assert 0.0 <= transition.confidence <= 1.0
    assert transition.occurred_at.tzinfo is timezone.utc
    assert transition.timestamp == transition.occurred_at
    assert transition.ordering_policy == OUT_OF_ORDER_POLICY
    assert transition.state_changed is True


def test_hint_dependency_comes_only_from_hint_events() -> None:
    one = _reduce([_event("hint-1", StudentLearningEventType.HINT_REQUESTED)])
    three = _reduce(
        [
            _event(f"hint-{index}", StudentLearningEventType.HINT_REQUESTED, seconds=index)
            for index in range(3)
        ]
    )

    assert one.concept("hash-map").hint_dependency is HintDependency.RECENT
    assert three.concept("hash-map").hint_dependency is HintDependency.PERSISTENT


def test_misconception_recovery_preserves_history_but_clears_active_signal() -> None:
    events = [
        _event(
            "misconception",
            StudentLearningEventType.MISCONCEPTION_SIGNAL,
            detail="capacity equals threshold",
        ),
        _event("correct-1", StudentLearningEventType.ANSWER_CORRECT, seconds=1),
        _event("correct-2", StudentLearningEventType.ANSWER_CORRECT, seconds=2),
    ]

    result = _reduce(events)
    state = result.concept("hash-map")

    assert state is not None
    assert state.misconception_history == ("capacity equals threshold",)
    assert state.active_misconceptions == ()
    assert state.misconception_signal_count == 1
    assert "misconception_recovered" in result.transitions[-1].reason


def test_attempt_and_answer_result_share_identity_without_double_counting() -> None:
    events = [
        _event(
            "attempt-start",
            StudentLearningEventType.ATTEMPT,
            attempt_id="attempt-42",
        ),
        _event(
            "attempt-result",
            StudentLearningEventType.ANSWER_PARTIAL,
            seconds=1,
            attempt_id="attempt-42",
        ),
        _event(
            "wrong-result",
            StudentLearningEventType.ANSWER_INCORRECT,
            seconds=2,
            attempt_id="attempt-43",
            detail="wrong bucket",
        ),
    ]

    state = _reduce(events).concept("hash-map")

    assert state is not None
    assert state.attempt_count == 2
    assert state.partial_answer_count == 1
    assert state.incorrect_answer_count == 1


def test_scope_isolation_and_canonical_concept_order() -> None:
    events = [
        _event("trees", StudentLearningEventType.ATTEMPT, concept_id="trees"),
        _event("arrays", StudentLearningEventType.ATTEMPT, concept_id="arrays"),
        _event(
            "other-student",
            StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
            student_id="student-b",
            evidence_ids=("other-proof",),
        ),
        _event(
            "other-session",
            StudentLearningEventType.SELF_ATTESTED_MASTERY,
            session_id="session-b",
        ),
    ]

    result = _reduce(events)

    assert tuple(state.concept_id for state in result.concept_states) == (
        "arrays",
        "trees",
    )
    assert result.applied_event_ids == ("arrays", "trees")
    assert all(not state.independently_verified for state in result.concept_states)


def test_histories_are_bounded_and_snapshot_is_immutable() -> None:
    policy = LearningStatePolicy(recent_mistakes=2, recent_misconceptions=2)
    events: list[StudentLearningEvent] = []
    for index in range(3):
        events.extend(
            [
                _event(
                    f"wrong-{index}",
                    StudentLearningEventType.ANSWER_INCORRECT,
                    seconds=index * 2,
                    detail=f"mistake-{index}",
                ),
                _event(
                    f"misconception-{index}",
                    StudentLearningEventType.MISCONCEPTION_SIGNAL,
                    seconds=index * 2 + 1,
                    detail=f"misconception-{index}",
                ),
            ]
        )

    state = _reduce(events, policy=policy).concept("hash-map")

    assert state.recent_mistakes == ("mistake-1", "mistake-2")
    assert state.misconception_history == ("misconception-1", "misconception-2")
    with pytest.raises(FrozenInstanceError):
        state.stage = LearningStage.INDEPENDENTLY_VERIFIED
    assert isinstance(state.recent_mistakes, tuple)


def test_example_and_active_time_are_learning_observations_only() -> None:
    events = [
        _event(
            "example",
            StudentLearningEventType.EXAMPLE_USED,
            example_id="bucket-demo",
            example_strategy=ExampleStrategy.REAL_WORLD,
            example_depth=ExampleDepth.LEVEL_1_REAL_WORLD,
        ),
        _event(
            "time",
            StudentLearningEventType.ACTIVE_LEARNING_TIME_OBSERVED,
            seconds=1,
            active_learning_seconds=42.5,
        ),
    ]

    state = _reduce(events).concept("hash-map")

    assert state.stage is LearningStage.LEARNING
    assert state.independently_verified is False
    assert state.recent_examples[0].example_id == "bucket-demo"
    assert state.active_learning_seconds == 42.5


def test_state_contract_contains_no_psychological_profile_fields() -> None:
    names = {item.name.casefold() for item in fields(ConceptLearningState)}

    assert not names.intersection({"personality", "emotion", "psychology", "profile"})
