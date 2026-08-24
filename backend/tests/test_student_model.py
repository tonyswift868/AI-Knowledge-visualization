from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

import app.teaching.student_model as student_model_module
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
from app.teaching.pedagogy import plan_teaching
from app.teaching.student_model import (
    OUT_OF_ORDER_POLICY,
    DuplicateEventConflictError,
    LearnerContextAssembler,
    ReplayPolicy,
    StudentLearningEvent,
    StudentLearningEventType,
    StudentLearningSnapshot,
    StudentModelStore,
    replay_student_learning,
)


BASE_TIME = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)


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


def _replay(
    events: list[StudentLearningEvent],
    *,
    policy: ReplayPolicy | None = None,
) -> StudentLearningSnapshot:
    return replay_student_learning(
        events,
        student_id="student-a",
        session_id="session-a",
        policy=policy,
    )


def test_required_event_vocabulary_is_complete() -> None:
    assert {event_type.value for event_type in StudentLearningEventType} == {
        "self_attested_mastery",
        "independently_verified_mastery",
        "answer_correct",
        "answer_partial",
        "answer_incorrect",
        "hint_requested",
        "misconception_signal",
        "teaching_move",
        "example_used",
        "attempt",
        "active_learning_time_observation",
        "assessment_result",
    }


def test_self_attested_mastery_is_not_independent_verification() -> None:
    self_attested = _event("e-1", StudentLearningEventType.SELF_ATTESTED_MASTERY)

    state = _replay([self_attested]).concept("hash-map")

    assert state is not None
    assert state.self_attested_mastery is True
    assert state.independently_verified_mastery is False
    assert state.mastery_state is MasteryState.SELF_ATTESTED


def test_independent_verification_remains_a_distinct_event() -> None:
    events = [
        _event("e-1", StudentLearningEventType.SELF_ATTESTED_MASTERY),
        _event(
            "e-2",
            StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
            seconds=1,
            evidence_ids=("assessment-42",),
        ),
    ]

    state = _replay(events).concept("hash-map")

    assert state is not None
    assert state.self_attested_mastery is True
    assert state.independently_verified_mastery is True
    assert state.mastery_state is MasteryState.INDEPENDENTLY_VERIFIED
    assert state.recent_evidence_ids == ("assessment-42",)


def test_chat_and_teaching_activity_do_not_create_mastery() -> None:
    events = [
        _event(
            f"move-{index}",
            StudentLearningEventType.TEACHING_MOVE,
            seconds=index,
            teacher_move=TeacherMove.EXPLAIN,
        )
        for index in range(5)
    ]
    events.append(_event("attempt", StudentLearningEventType.ATTEMPT, seconds=10))

    state = _replay(events).concept("hash-map")

    assert state is not None
    assert state.mastery_state is MasteryState.IN_PROGRESS
    assert state.self_attested_mastery is False
    assert state.independently_verified_mastery is False


def test_duplicate_event_is_idempotent() -> None:
    attempt = _event(
        "attempt-1",
        StudentLearningEventType.ATTEMPT,
        attempt_id="answer-1",
    )

    snapshot = _replay([attempt, attempt, attempt])
    state = snapshot.concept("hash-map")

    assert snapshot.applied_event_count == 1
    assert state is not None
    assert state.attempt_count == 1


def test_conflicting_duplicate_event_fails_closed() -> None:
    first = _event("same-id", StudentLearningEventType.ANSWER_CORRECT)
    conflict = _event("same-id", StudentLearningEventType.ANSWER_INCORRECT)

    with pytest.raises(DuplicateEventConflictError, match="conflicting content"):
        _replay([first, conflict])


def test_attempt_and_result_share_attempt_id_without_double_counting() -> None:
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
    ]

    state = _replay(events).concept("hash-map")

    assert state is not None
    assert state.attempt_count == 1
    assert state.partial_answer_count == 1


def test_out_of_order_input_has_deterministic_replay() -> None:
    events = [
        _event(
            "z-last",
            StudentLearningEventType.ANSWER_CORRECT,
            seconds=30,
            confidence=0.9,
        ),
        _event(
            "a-first",
            StudentLearningEventType.ANSWER_INCORRECT,
            seconds=10,
            detail="confused capacity with threshold",
            confidence=0.1,
        ),
        _event("m-middle", StudentLearningEventType.HINT_REQUESTED, seconds=20),
    ]

    forward = _replay(events)
    reversed_snapshot = _replay(list(reversed(events)))

    assert OUT_OF_ORDER_POLICY == "sort_by_occurred_at_utc_then_event_id"
    assert forward == reversed_snapshot
    assert forward.concept("hash-map").consecutive_failures == 0


def test_student_and_session_scopes_are_isolated() -> None:
    events = [
        _event("a", StudentLearningEventType.ANSWER_CORRECT),
        _event(
            "other-student",
            StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
            student_id="student-b",
        ),
        _event(
            "other-session",
            StudentLearningEventType.SELF_ATTESTED_MASTERY,
            session_id="session-b",
        ),
    ]

    snapshot = _replay(events)
    state = snapshot.concept("hash-map")

    assert snapshot.applied_event_count == 1
    assert state is not None
    assert state.correct_answer_count == 1
    assert state.self_attested_mastery is False
    assert state.independently_verified_mastery is False


def test_multiple_concepts_remain_separate_and_canonically_ordered() -> None:
    events = [
        _event(
            "trees-attempt",
            StudentLearningEventType.ATTEMPT,
            concept_id="trees",
        ),
        _event(
            "arrays-mastered",
            StudentLearningEventType.SELF_ATTESTED_MASTERY,
            concept_id="arrays",
        ),
    ]

    snapshot = _replay(events)

    assert tuple(state.concept_id for state in snapshot.concept_states) == (
        "arrays",
        "trees",
    )
    assert snapshot.concept("arrays").self_attested_mastery is True
    assert snapshot.concept("trees").attempt_count == 1


def test_recent_histories_are_bounded() -> None:
    policy = ReplayPolicy(
        recent_mistakes=2,
        recent_misconceptions=2,
        recent_teaching_moves=2,
        recent_examples=2,
        recent_assessments=2,
        recent_evidence_ids=2,
        confidence_window=2,
        persistent_hint_threshold=2,
    )
    events: list[StudentLearningEvent] = []
    for index in range(3):
        events.extend(
            [
                _event(
                    f"wrong-{index}",
                    StudentLearningEventType.ANSWER_INCORRECT,
                    seconds=index * 10,
                    detail=f"mistake-{index}",
                    confidence=index / 10,
                    evidence_ids=(f"evidence-{index}",),
                ),
                _event(
                    f"misconception-{index}",
                    StudentLearningEventType.MISCONCEPTION_SIGNAL,
                    seconds=index * 10 + 1,
                    detail=f"misconception-{index}",
                ),
                _event(
                    f"move-{index}",
                    StudentLearningEventType.TEACHING_MOVE,
                    seconds=index * 10 + 2,
                    teacher_move=TeacherMove.EXPLAIN,
                ),
                _event(
                    f"example-{index}",
                    StudentLearningEventType.EXAMPLE_USED,
                    seconds=index * 10 + 3,
                    example_id=f"example-{index}",
                    example_strategy=ExampleStrategy.REAL_WORLD,
                    example_depth=ExampleDepth.LEVEL_1_REAL_WORLD,
                ),
                _event(
                    f"assessment-{index}",
                    StudentLearningEventType.ASSESSMENT_RESULT,
                    seconds=index * 10 + 4,
                    assessment_score=index / 10,
                    assessment_passed=False,
                ),
            ]
        )

    state = _replay(events, policy=policy).concept("hash-map")

    assert state is not None
    assert state.recent_mistakes == ("mistake-1", "mistake-2")
    assert state.misconception_signals == ("misconception-1", "misconception-2")
    assert len(state.recent_teaching_moves) == 2
    assert [example.example_id for example in state.recent_examples] == [
        "example-1",
        "example-2",
    ]
    assert len(state.recent_assessments) == 2
    assert state.recent_evidence_ids == ("evidence-1", "evidence-2")


def test_hint_dependency_uses_explicit_hint_events() -> None:
    one_hint = _replay([_event("hint-1", StudentLearningEventType.HINT_REQUESTED)])
    three_hints = _replay(
        [
            _event(f"hint-{index}", StudentLearningEventType.HINT_REQUESTED, seconds=index)
            for index in range(3)
        ]
    )

    assert one_hint.concept("hash-map").hint_dependency is HintDependency.RECENT
    assert three_hints.concept("hash-map").hint_dependency is HintDependency.PERSISTENT


def test_active_time_and_confidence_use_observations_not_chat_count() -> None:
    events = [
        _event(
            "time-1",
            StudentLearningEventType.ACTIVE_LEARNING_TIME_OBSERVED,
            active_learning_seconds=12.5,
        ),
        _event(
            "time-2",
            StudentLearningEventType.ACTIVE_LEARNING_TIME_OBSERVED,
            seconds=1,
            active_learning_seconds=7.5,
        ),
        _event(
            "answer",
            StudentLearningEventType.ANSWER_CORRECT,
            seconds=2,
            confidence=0.8,
        ),
    ]

    snapshot = _replay(events)
    state = snapshot.concept("hash-map")

    assert snapshot.active_learning_seconds == 20.0
    assert state is not None
    assert state.confidence == 0.8


def test_failed_assessment_does_not_delete_history_or_verified_flag() -> None:
    events = [
        _event(
            "verified",
            StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
            evidence_ids=("old-proof",),
        ),
        _event(
            "correct",
            StudentLearningEventType.ANSWER_CORRECT,
            seconds=1,
            detail="previous success",
        ),
        _event(
            "failed-assessment",
            StudentLearningEventType.ASSESSMENT_RESULT,
            seconds=2,
            detail="missed resize threshold",
            assessment_score=0.3,
            assessment_passed=False,
            evidence_ids=("failed-proof",),
        ),
    ]

    state = _replay(events).concept("hash-map")

    assert state is not None
    assert state.independently_verified_mastery is True
    assert state.mastery_state is MasteryState.INDEPENDENTLY_VERIFIED
    assert state.correct_answer_count == 1
    assert state.assessment_fail_count == 1
    assert state.recent_assessments[-1].score == 0.3
    assert state.recent_assessments[-1].passed is False
    assert state.recent_mistakes == ("missed resize threshold",)
    assert state.recent_evidence_ids == ("old-proof", "failed-proof")


def test_snapshot_and_nested_state_are_immutable() -> None:
    snapshot = _replay([_event("attempt", StudentLearningEventType.ATTEMPT)])
    state = snapshot.concept("hash-map")

    with pytest.raises(FrozenInstanceError):
        snapshot.session_id = "changed"
    with pytest.raises(FrozenInstanceError):
        state.attempt_count = 99
    assert isinstance(snapshot.concept_states, tuple)


def test_assembler_builds_frozen_learner_context_and_planner_accepts_it() -> None:
    events = [
        _event(
            "wrong",
            StudentLearningEventType.ANSWER_INCORRECT,
            detail="threshold is not capacity",
        ),
        _event(
            "misconception",
            StudentLearningEventType.MISCONCEPTION_SIGNAL,
            seconds=1,
            detail="threshold equals capacity",
        ),
        _event("hint", StudentLearningEventType.HINT_REQUESTED, seconds=2),
        _event(
            "move",
            StudentLearningEventType.TEACHING_MOVE,
            seconds=3,
            teacher_move=TeacherMove.EXPLAIN,
        ),
        _event(
            "example",
            StudentLearningEventType.EXAMPLE_USED,
            seconds=4,
            example_id="bucket-demo",
            example_strategy=ExampleStrategy.REAL_WORLD,
            example_depth=ExampleDepth.LEVEL_1_REAL_WORLD,
            example_effective=False,
        ),
    ]
    snapshot = _replay(events)

    context = LearnerContextAssembler().assemble(
        snapshot,
        current_concept="hash-map",
        learning_goal="understand resize threshold",
        student_move=StudentMove.CONFUSED,
        current_task=CurrentTaskKind.PRACTICE,
        additional_evidence_ids=("chunk-1",),
    )
    brief = plan_teaching(context)

    assert context.mastery_state is MasteryState.IN_PROGRESS
    assert context.recent_mistakes == ("threshold is not capacity",)
    assert context.known_misconceptions == ("threshold equals capacity",)
    assert context.hint_dependency is HintDependency.RECENT
    assert context.recent_teaching_moves == (TeacherMove.EXPLAIN,)
    assert context.recent_examples[0].example_id == "bucket-demo"
    assert context.evidence_available is True
    assert context.relevant_evidence_ids == ("chunk-1",)
    assert brief.focus_concept == "hash-map"


def test_assembler_preserves_self_attested_not_verified() -> None:
    snapshot = _replay(
        [_event("self", StudentLearningEventType.SELF_ATTESTED_MASTERY)]
    )

    context = LearnerContextAssembler().assemble(
        snapshot,
        current_concept="hash-map",
        student_move=StudentMove.LEARNER_SAYS_MASTERED,
    )

    assert context.mastery_state is MasteryState.SELF_ATTESTED
    assert context.mastery_state is not MasteryState.INDEPENDENTLY_VERIFIED


def test_assessment_request_is_caller_semantics_not_inferred_grading() -> None:
    snapshot = _replay([])

    context = LearnerContextAssembler().assemble(
        snapshot,
        current_concept="hash-map",
        student_move=StudentMove.REQUEST_ASSESSMENT,
    )

    assert context.assessment_state is AssessmentState.REQUESTED


@pytest.mark.parametrize(
    ("event_type", "changes", "message"),
    [
        (StudentLearningEventType.TEACHING_MOVE, {}, "requires teacher_move"),
        (StudentLearningEventType.EXAMPLE_USED, {}, "requires example_id"),
        (StudentLearningEventType.MISCONCEPTION_SIGNAL, {}, "requires detail"),
        (
            StudentLearningEventType.ACTIVE_LEARNING_TIME_OBSERVED,
            {},
            "requires active_learning_seconds",
        ),
        (StudentLearningEventType.ASSESSMENT_RESULT, {}, "requires a score"),
    ],
)
def test_typed_events_reject_missing_required_observation(
    event_type: StudentLearningEventType,
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _event("invalid", event_type, **changes)


def test_event_rejects_naive_time_for_deterministic_ordering() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        StudentLearningEvent(
            event_id="naive",
            student_id="student-a",
            session_id="session-a",
            concept_id="hash-map",
            event_type=StudentLearningEventType.ATTEMPT,
            occurred_at=datetime(2026, 8, 25),
        )


def test_store_protocol_is_only_a_persistence_seam() -> None:
    class MemoryStore:
        def append_events(self, events: list[StudentLearningEvent]) -> None:
            self.events = list(events)

        def load_events(
            self,
            *,
            student_id: str,
            session_id: str,
        ) -> list[StudentLearningEvent]:
            return getattr(self, "events", [])

    assert isinstance(MemoryStore(), StudentModelStore)


def test_business_layer_has_no_database_or_provider_dependency() -> None:
    tree = ast.parse(inspect.getsource(student_model_module))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )

    assert not any(
        forbidden in imported.casefold()
        for imported in imports
        for forbidden in ("database", "sqlalchemy", "orm", "provider", "openai")
    )
