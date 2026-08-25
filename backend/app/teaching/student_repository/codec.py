"""Canonical JSON codecs used by SQLite event and checkpoint rows."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from ..contracts import ExampleDepth, ExampleHistory, ExampleStrategy, HintDependency, TeacherMove
from ..learning_state import (
    ConceptLearningState,
    LearningStage,
    LearningStateTransition,
    ReducerResult,
)
from ..student_model import StudentLearningEvent, StudentLearningEventType


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def parse_utc(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def event_to_data(event: StudentLearningEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "student_id": event.student_id,
        "session_id": event.session_id,
        "concept_id": event.concept_id,
        "event_type": event.event_type.value,
        "occurred_at": utc_text(event.occurred_at),
        "attempt_id": event.attempt_id,
        "detail": event.detail,
        "confidence": event.confidence,
        "evidence_ids": list(event.evidence_ids),
        "teacher_move": None if event.teacher_move is None else event.teacher_move.value,
        "example_id": event.example_id,
        "example_strategy": event.example_strategy.value,
        "example_depth": None if event.example_depth is None else event.example_depth.value,
        "example_effective": event.example_effective,
        "active_learning_seconds": event.active_learning_seconds,
        "assessment_score": event.assessment_score,
        "assessment_passed": event.assessment_passed,
    }


def event_to_json(event: StudentLearningEvent) -> str:
    return canonical_json(event_to_data(event))


def event_from_data(data: object) -> StudentLearningEvent:
    if not isinstance(data, dict):
        raise TypeError("event payload must be an object")
    occurred_at = parse_utc(data["occurred_at"])
    if occurred_at is None:
        raise ValueError("event occurred_at cannot be null")
    return StudentLearningEvent(
        event_id=data["event_id"],
        student_id=data["student_id"],
        session_id=data["session_id"],
        concept_id=data["concept_id"],
        event_type=StudentLearningEventType(data["event_type"]),
        occurred_at=occurred_at,
        attempt_id=data.get("attempt_id"),
        detail=data.get("detail"),
        confidence=data.get("confidence"),
        evidence_ids=tuple(data.get("evidence_ids", ())),
        teacher_move=data.get("teacher_move"),
        example_id=data.get("example_id"),
        example_strategy=data.get("example_strategy", ExampleStrategy.NONE.value),
        example_depth=data.get("example_depth"),
        example_effective=data.get("example_effective"),
        active_learning_seconds=data.get("active_learning_seconds"),
        assessment_score=data.get("assessment_score"),
        assessment_passed=data.get("assessment_passed"),
    )


def event_from_json(payload: str) -> StudentLearningEvent:
    return event_from_data(json.loads(payload))


def _example_to_data(example: ExampleHistory) -> dict[str, object]:
    return {
        "example_id": example.example_id,
        "strategy": example.strategy.value,
        "depth": example.depth.value,
        "effective": example.effective,
    }


def _example_from_data(data: dict[str, object]) -> ExampleHistory:
    return ExampleHistory(
        example_id=data["example_id"],
        strategy=ExampleStrategy(data["strategy"]),
        depth=ExampleDepth(data["depth"]),
        effective=data.get("effective"),
    )


def _state_to_data(state: ConceptLearningState) -> dict[str, object]:
    return {
        "concept_id": state.concept_id,
        "stage": state.stage.value,
        "confidence": state.confidence,
        "self_attested_ever": state.self_attested_ever,
        "independently_verified": state.independently_verified,
        "verification_evidence_ids": list(state.verification_evidence_ids),
        "recent_evidence_ids": list(state.recent_evidence_ids),
        "attempt_count": state.attempt_count,
        "correct_answer_count": state.correct_answer_count,
        "partial_answer_count": state.partial_answer_count,
        "incorrect_answer_count": state.incorrect_answer_count,
        "assessment_result_count": state.assessment_result_count,
        "assessment_pass_count": state.assessment_pass_count,
        "assessment_fail_count": state.assessment_fail_count,
        "hint_request_count": state.hint_request_count,
        "hint_dependency": state.hint_dependency.value,
        "misconception_signal_count": state.misconception_signal_count,
        "misconception_history": list(state.misconception_history),
        "active_misconceptions": list(state.active_misconceptions),
        "recent_mistakes": list(state.recent_mistakes),
        "recent_teaching_moves": [move.value for move in state.recent_teaching_moves],
        "recent_examples": [_example_to_data(item) for item in state.recent_examples],
        "consecutive_correct_answers": state.consecutive_correct_answers,
        "consecutive_failures": state.consecutive_failures,
        "active_learning_seconds": state.active_learning_seconds,
        "last_event_at": utc_text(state.last_event_at),
        "last_event_id": state.last_event_id,
    }


def _state_from_data(data: dict[str, object]) -> ConceptLearningState:
    return ConceptLearningState(
        concept_id=data["concept_id"],
        stage=LearningStage(data["stage"]),
        confidence=data["confidence"],
        self_attested_ever=data["self_attested_ever"],
        independently_verified=data["independently_verified"],
        verification_evidence_ids=tuple(data["verification_evidence_ids"]),
        recent_evidence_ids=tuple(data["recent_evidence_ids"]),
        attempt_count=data["attempt_count"],
        correct_answer_count=data["correct_answer_count"],
        partial_answer_count=data["partial_answer_count"],
        incorrect_answer_count=data["incorrect_answer_count"],
        assessment_result_count=data["assessment_result_count"],
        assessment_pass_count=data["assessment_pass_count"],
        assessment_fail_count=data["assessment_fail_count"],
        hint_request_count=data["hint_request_count"],
        hint_dependency=HintDependency(data["hint_dependency"]),
        misconception_signal_count=data["misconception_signal_count"],
        misconception_history=tuple(data["misconception_history"]),
        active_misconceptions=tuple(data["active_misconceptions"]),
        recent_mistakes=tuple(data["recent_mistakes"]),
        recent_teaching_moves=tuple(
            TeacherMove(item) for item in data["recent_teaching_moves"]
        ),
        recent_examples=tuple(
            _example_from_data(item) for item in data["recent_examples"]
        ),
        consecutive_correct_answers=data["consecutive_correct_answers"],
        consecutive_failures=data["consecutive_failures"],
        active_learning_seconds=data["active_learning_seconds"],
        last_event_at=parse_utc(data["last_event_at"]),
        last_event_id=data["last_event_id"],
    )


def _transition_to_data(transition: LearningStateTransition) -> dict[str, object]:
    return {
        "concept_id": transition.concept_id,
        "from_stage": transition.from_stage.value,
        "to_stage": transition.to_stage.value,
        "reason": transition.reason,
        "source_event_ids": list(transition.source_event_ids),
        "confidence": transition.confidence,
        "occurred_at": utc_text(transition.occurred_at),
        "order_index": transition.order_index,
        "ordering_policy": transition.ordering_policy,
    }


def _transition_from_data(data: dict[str, object]) -> LearningStateTransition:
    occurred_at = parse_utc(data["occurred_at"])
    if occurred_at is None:
        raise ValueError("transition occurred_at cannot be null")
    return LearningStateTransition(
        concept_id=data["concept_id"],
        from_stage=LearningStage(data["from_stage"]),
        to_stage=LearningStage(data["to_stage"]),
        reason=data["reason"],
        source_event_ids=tuple(data["source_event_ids"]),
        confidence=data["confidence"],
        occurred_at=occurred_at,
        order_index=data["order_index"],
        ordering_policy=data["ordering_policy"],
    )


def result_to_data(result: ReducerResult) -> dict[str, object]:
    return {
        "student_id": result.student_id,
        "session_id": result.session_id,
        "concept_states": [_state_to_data(item) for item in result.concept_states],
        "transitions": [_transition_to_data(item) for item in result.transitions],
        "applied_event_ids": list(result.applied_event_ids),
        "duplicate_event_ids": list(result.duplicate_event_ids),
        "ordering_policy": result.ordering_policy,
        "last_event_at": utc_text(result.last_event_at),
    }


def result_to_json(result: ReducerResult) -> str:
    return canonical_json(result_to_data(result))


def result_from_data(data: object) -> ReducerResult:
    if not isinstance(data, dict):
        raise TypeError("snapshot payload must be an object")
    return ReducerResult(
        student_id=data["student_id"],
        session_id=data["session_id"],
        concept_states=tuple(_state_from_data(item) for item in data["concept_states"]),
        transitions=tuple(
            _transition_from_data(item) for item in data["transitions"]
        ),
        applied_event_ids=tuple(data["applied_event_ids"]),
        duplicate_event_ids=tuple(data["duplicate_event_ids"]),
        ordering_policy=data["ordering_policy"],
        last_event_at=parse_utc(data["last_event_at"]),
    )


def result_from_json(payload: str) -> ReducerResult:
    return result_from_data(json.loads(payload))


__all__ = [
    "canonical_json",
    "event_from_data",
    "event_from_json",
    "event_to_data",
    "event_to_json",
    "result_from_json",
    "result_to_json",
    "sha256_text",
    "utc_text",
]
