"""Pure event projection for the minimal state needed by adaptive teaching.

This module owns no persistence and performs no grading.  Callers provide
learning observations, and deterministic replay produces an immutable snapshot
that can be adapted to the frozen pedagogy ``LearnerContext`` contract.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, runtime_checkable

from .contracts import (
    AssessmentState,
    CurrentTaskKind,
    ExampleDepth,
    ExampleHistory,
    ExampleStrategy,
    HintDependency,
    LearnerContext,
    MasteryState,
    StudentMove,
    TeacherMove,
)


OUT_OF_ORDER_POLICY = "sort_by_occurred_at_utc_then_event_id"


class StudentLearningEventType(str, Enum):
    SELF_ATTESTED_MASTERY = "self_attested_mastery"
    INDEPENDENTLY_VERIFIED_MASTERY = "independently_verified_mastery"
    ANSWER_CORRECT = "answer_correct"
    ANSWER_PARTIAL = "answer_partial"
    ANSWER_INCORRECT = "answer_incorrect"
    HINT_REQUESTED = "hint_requested"
    MISCONCEPTION_SIGNAL = "misconception_signal"
    TEACHING_MOVE = "teaching_move"
    EXAMPLE_USED = "example_used"
    ATTEMPT = "attempt"
    ACTIVE_LEARNING_TIME_OBSERVED = "active_learning_time_observation"
    ASSESSMENT_RESULT = "assessment_result"


_ATTEMPT_EVENT_TYPES = frozenset(
    {
        StudentLearningEventType.ATTEMPT,
        StudentLearningEventType.ANSWER_CORRECT,
        StudentLearningEventType.ANSWER_PARTIAL,
        StudentLearningEventType.ANSWER_INCORRECT,
        StudentLearningEventType.ASSESSMENT_RESULT,
    }
)


class DuplicateEventConflictError(ValueError):
    """Raised when one id describes two different events in the same scope."""


@dataclass(frozen=True, slots=True)
class StudentLearningEvent:
    """One immutable, caller-observed learning fact.

    ``attempt_id`` links an attempt observation and its later result so replay
    counts the learner attempt once.  Correctness and assessment outcomes are
    accepted only as supplied facts; this layer never derives or grades them.
    """

    event_id: str
    student_id: str
    session_id: str
    concept_id: str
    event_type: StudentLearningEventType | str
    occurred_at: datetime
    attempt_id: str | None = None
    detail: str | None = None
    confidence: float | None = None
    evidence_ids: tuple[str, ...] = ()
    teacher_move: TeacherMove | str | None = None
    example_id: str | None = None
    example_strategy: ExampleStrategy | str = ExampleStrategy.NONE
    example_depth: ExampleDepth | int | None = None
    example_effective: bool | None = None
    active_learning_seconds: float | None = None
    assessment_score: float | None = None
    assessment_passed: bool | None = None

    def __post_init__(self) -> None:
        for name, maximum in (
            ("event_id", 160),
            ("student_id", 160),
            ("session_id", 160),
            ("concept_id", 240),
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            normalized = value.strip()
            if len(normalized) > maximum:
                raise ValueError(f"{name} exceeds {maximum} characters")
            object.__setattr__(self, name, normalized)

        if not isinstance(self.event_type, StudentLearningEventType):
            object.__setattr__(self, "event_type", StudentLearningEventType(self.event_type))
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

        if self.attempt_id is not None:
            normalized_attempt = self.attempt_id.strip()
            if not normalized_attempt:
                raise ValueError("attempt_id cannot be blank")
            if len(normalized_attempt) > 160:
                raise ValueError("attempt_id exceeds 160 characters")
            if self.event_type not in _ATTEMPT_EVENT_TYPES:
                raise ValueError("attempt_id is only valid for attempt or result events")
            object.__setattr__(self, "attempt_id", normalized_attempt)

        if self.detail is not None:
            normalized_detail = self.detail.strip()
            if not normalized_detail:
                raise ValueError("detail cannot be blank")
            if len(normalized_detail) > 500:
                raise ValueError("detail exceeds 500 characters")
            object.__setattr__(self, "detail", normalized_detail)

        evidence_ids = tuple(dict.fromkeys(value.strip() for value in self.evidence_ids))
        if any(not value for value in evidence_ids):
            raise ValueError("evidence_ids cannot contain blank values")
        if any(len(value) > 160 for value in evidence_ids):
            raise ValueError("evidence id exceeds 160 characters")
        object.__setattr__(self, "evidence_ids", evidence_ids)

        if self.confidence is not None:
            if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
                raise ValueError("confidence must be finite and between 0 and 1")

        if self.teacher_move is not None and not isinstance(self.teacher_move, TeacherMove):
            object.__setattr__(self, "teacher_move", TeacherMove(self.teacher_move))
        if self.example_strategy is not None and not isinstance(
            self.example_strategy, ExampleStrategy
        ):
            object.__setattr__(self, "example_strategy", ExampleStrategy(self.example_strategy))
        if self.example_depth is not None and not isinstance(self.example_depth, ExampleDepth):
            object.__setattr__(self, "example_depth", ExampleDepth(self.example_depth))

        if self.event_type is StudentLearningEventType.TEACHING_MOVE:
            if self.teacher_move is None:
                raise ValueError("teaching_move event requires teacher_move")
        elif self.teacher_move is not None:
            raise ValueError("teacher_move is only valid for teaching_move events")

        example_fields_present = any(
            value is not None
            for value in (self.example_id, self.example_depth, self.example_effective)
        ) or self.example_strategy is not ExampleStrategy.NONE
        if self.event_type is StudentLearningEventType.EXAMPLE_USED:
            if not self.example_id or not self.example_id.strip():
                raise ValueError("example_used event requires example_id")
            if len(self.example_id.strip()) > 160:
                raise ValueError("example_id exceeds 160 characters")
            if self.example_strategy is ExampleStrategy.NONE or self.example_depth is None:
                raise ValueError("example_used event requires strategy and depth")
            object.__setattr__(self, "example_id", self.example_id.strip())
        elif example_fields_present:
            raise ValueError("example fields are only valid for example_used events")

        if self.event_type is StudentLearningEventType.MISCONCEPTION_SIGNAL:
            if self.detail is None:
                raise ValueError("misconception_signal event requires detail")

        if self.active_learning_seconds is not None:
            if (
                not math.isfinite(self.active_learning_seconds)
                or not 0.0 <= self.active_learning_seconds <= 86_400.0
            ):
                raise ValueError("active_learning_seconds must be between 0 and 86400")
        if self.event_type is StudentLearningEventType.ACTIVE_LEARNING_TIME_OBSERVED:
            if self.active_learning_seconds is None:
                raise ValueError("active time event requires active_learning_seconds")
        elif self.active_learning_seconds is not None:
            raise ValueError("active_learning_seconds is only valid for active time events")

        if self.assessment_score is not None:
            if not math.isfinite(self.assessment_score) or not 0.0 <= self.assessment_score <= 1.0:
                raise ValueError("assessment_score must be between 0 and 1")
        if self.assessment_passed is not None and not isinstance(self.assessment_passed, bool):
            raise TypeError("assessment_passed must be a bool or None")
        if self.example_effective is not None and not isinstance(self.example_effective, bool):
            raise TypeError("example_effective must be a bool or None")
        assessment_fields_present = (
            self.assessment_score is not None or self.assessment_passed is not None
        )
        if self.event_type is StudentLearningEventType.ASSESSMENT_RESULT:
            if not assessment_fields_present:
                raise ValueError("assessment_result requires a score or passed observation")
        elif assessment_fields_present:
            raise ValueError("assessment fields are only valid for assessment_result events")


@dataclass(frozen=True, slots=True)
class ReplayPolicy:
    recent_mistakes: int = 8
    recent_misconceptions: int = 8
    recent_teaching_moves: int = 8
    recent_examples: int = 8
    recent_assessments: int = 8
    recent_evidence_ids: int = 32
    confidence_window: int = 8
    persistent_hint_threshold: int = 3

    def __post_init__(self) -> None:
        limits = {
            "recent_mistakes": 12,
            "recent_misconceptions": 12,
            "recent_teaching_moves": 8,
            "recent_examples": 8,
            "recent_assessments": 8,
            "recent_evidence_ids": 32,
            "confidence_window": 32,
            "persistent_hint_threshold": 20,
        }
        for name, maximum in limits.items():
            value = getattr(self, name)
            if not 1 <= value <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}")


@dataclass(frozen=True, slots=True)
class AssessmentObservation:
    occurred_at: datetime
    score: float | None
    passed: bool | None
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConceptLearningState:
    concept_id: str
    recent_mistakes: tuple[str, ...] = ()
    misconception_signals: tuple[str, ...] = ()
    hint_dependency: HintDependency = HintDependency.NONE
    recent_teaching_moves: tuple[TeacherMove, ...] = ()
    recent_examples: tuple[ExampleHistory, ...] = ()
    recent_assessments: tuple[AssessmentObservation, ...] = ()
    recent_evidence_ids: tuple[str, ...] = ()
    self_attested_mastery: bool = False
    independently_verified_mastery: bool = False
    attempt_count: int = 0
    correct_answer_count: int = 0
    partial_answer_count: int = 0
    incorrect_answer_count: int = 0
    hint_request_count: int = 0
    misconception_signal_count: int = 0
    assessment_result_count: int = 0
    assessment_pass_count: int = 0
    assessment_fail_count: int = 0
    consecutive_failures: int = 0
    active_learning_seconds: float = 0.0
    confidence: float = 0.0
    last_event_at: datetime | None = None

    @property
    def mastery_state(self) -> MasteryState:
        if self.independently_verified_mastery:
            return MasteryState.INDEPENDENTLY_VERIFIED
        if self.self_attested_mastery:
            return MasteryState.SELF_ATTESTED
        if (
            self.attempt_count
            or self.hint_request_count
            or self.misconception_signal_count
            or self.recent_teaching_moves
        ):
            return MasteryState.IN_PROGRESS
        return MasteryState.UNKNOWN


@dataclass(frozen=True, slots=True)
class StudentLearningSnapshot:
    student_id: str
    session_id: str
    concept_states: tuple[ConceptLearningState, ...] = ()
    applied_event_count: int = 0
    last_event_at: datetime | None = None

    def concept(self, concept_id: str) -> ConceptLearningState | None:
        normalized = concept_id.strip()
        return next(
            (state for state in self.concept_states if state.concept_id == normalized),
            None,
        )

    @property
    def total_attempt_count(self) -> int:
        return sum(state.attempt_count for state in self.concept_states)

    @property
    def active_learning_seconds(self) -> float:
        return sum(state.active_learning_seconds for state in self.concept_states)

    @property
    def confidence(self) -> float:
        samples = [state.confidence for state in self.concept_states if state.confidence > 0]
        return round(sum(samples) / len(samples), 6) if samples else 0.0

    @property
    def self_attested_concepts(self) -> tuple[str, ...]:
        return tuple(
            state.concept_id for state in self.concept_states if state.self_attested_mastery
        )

    @property
    def verified_concepts(self) -> tuple[str, ...]:
        return tuple(
            state.concept_id
            for state in self.concept_states
            if state.independently_verified_mastery
        )

    @classmethod
    def replay(
        cls,
        events: Iterable[StudentLearningEvent],
        *,
        student_id: str,
        session_id: str,
        policy: ReplayPolicy | None = None,
    ) -> "StudentLearningSnapshot":
        return replay_student_learning(
            events,
            student_id=student_id,
            session_id=session_id,
            policy=policy,
        )


@dataclass(slots=True)
class _ConceptAccumulator:
    concept_id: str
    policy: ReplayPolicy
    recent_mistakes: list[str] = field(default_factory=list)
    misconception_signals: list[str] = field(default_factory=list)
    recent_teaching_moves: list[TeacherMove] = field(default_factory=list)
    recent_examples: list[ExampleHistory] = field(default_factory=list)
    recent_assessments: list[AssessmentObservation] = field(default_factory=list)
    recent_evidence_ids: list[str] = field(default_factory=list)
    confidence_samples: list[float] = field(default_factory=list)
    counted_attempt_ids: set[str] = field(default_factory=set)
    self_attested_mastery: bool = False
    independently_verified_mastery: bool = False
    attempt_count: int = 0
    correct_answer_count: int = 0
    partial_answer_count: int = 0
    incorrect_answer_count: int = 0
    hint_request_count: int = 0
    misconception_signal_count: int = 0
    assessment_result_count: int = 0
    assessment_pass_count: int = 0
    assessment_fail_count: int = 0
    consecutive_failures: int = 0
    active_learning_seconds: float = 0.0
    last_event_at: datetime | None = None

    def append_bounded(self, values: list, value: object, limit: int) -> None:
        values.append(value)
        if len(values) > limit:
            del values[: len(values) - limit]

    def append_unique_bounded(self, values: list[str], value: str, limit: int) -> None:
        if value in values:
            values.remove(value)
        self.append_bounded(values, value, limit)

    def apply(self, event: StudentLearningEvent) -> None:
        self.last_event_at = event.occurred_at.astimezone(timezone.utc)
        for evidence_id in event.evidence_ids:
            self.append_unique_bounded(
                self.recent_evidence_ids,
                evidence_id,
                self.policy.recent_evidence_ids,
            )

        if event.event_type in _ATTEMPT_EVENT_TYPES:
            attempt_key = event.attempt_id or f"event:{event.event_id}"
            if attempt_key not in self.counted_attempt_ids:
                self.counted_attempt_ids.add(attempt_key)
                self.attempt_count += 1

        if event.event_type is StudentLearningEventType.SELF_ATTESTED_MASTERY:
            self.self_attested_mastery = True
        elif event.event_type is StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY:
            self.independently_verified_mastery = True
        elif event.event_type is StudentLearningEventType.ANSWER_CORRECT:
            self.correct_answer_count += 1
            self.consecutive_failures = 0
        elif event.event_type is StudentLearningEventType.ANSWER_PARTIAL:
            self.partial_answer_count += 1
            self.consecutive_failures = 0
        elif event.event_type is StudentLearningEventType.ANSWER_INCORRECT:
            self.incorrect_answer_count += 1
            self.consecutive_failures += 1
            if event.detail:
                self.append_bounded(
                    self.recent_mistakes,
                    event.detail,
                    self.policy.recent_mistakes,
                )
        elif event.event_type is StudentLearningEventType.HINT_REQUESTED:
            self.hint_request_count += 1
        elif event.event_type is StudentLearningEventType.MISCONCEPTION_SIGNAL:
            self.misconception_signal_count += 1
            self.append_unique_bounded(
                self.misconception_signals,
                event.detail or "",
                self.policy.recent_misconceptions,
            )
        elif event.event_type is StudentLearningEventType.TEACHING_MOVE:
            self.append_bounded(
                self.recent_teaching_moves,
                event.teacher_move,
                self.policy.recent_teaching_moves,
            )
        elif event.event_type is StudentLearningEventType.EXAMPLE_USED:
            self.append_bounded(
                self.recent_examples,
                ExampleHistory(
                    example_id=event.example_id,
                    strategy=event.example_strategy,
                    depth=event.example_depth,
                    effective=event.example_effective,
                ),
                self.policy.recent_examples,
            )
        elif event.event_type is StudentLearningEventType.ACTIVE_LEARNING_TIME_OBSERVED:
            self.active_learning_seconds += event.active_learning_seconds or 0.0
        elif event.event_type is StudentLearningEventType.ASSESSMENT_RESULT:
            self.assessment_result_count += 1
            self.append_bounded(
                self.recent_assessments,
                AssessmentObservation(
                    occurred_at=event.occurred_at.astimezone(timezone.utc),
                    score=event.assessment_score,
                    passed=event.assessment_passed,
                    evidence_ids=event.evidence_ids,
                ),
                self.policy.recent_assessments,
            )
            if event.assessment_passed is True:
                self.assessment_pass_count += 1
                self.consecutive_failures = 0
            elif event.assessment_passed is False:
                self.assessment_fail_count += 1
                self.consecutive_failures += 1
                if event.detail:
                    self.append_bounded(
                        self.recent_mistakes,
                        event.detail,
                        self.policy.recent_mistakes,
                    )

        confidence = _confidence_sample(event)
        if confidence is not None:
            self.append_bounded(
                self.confidence_samples,
                confidence,
                self.policy.confidence_window,
            )

    def freeze(self) -> ConceptLearningState:
        if self.hint_request_count >= self.policy.persistent_hint_threshold:
            hint_dependency = HintDependency.PERSISTENT
        elif self.hint_request_count:
            hint_dependency = HintDependency.RECENT
        else:
            hint_dependency = HintDependency.NONE
        confidence = (
            round(sum(self.confidence_samples) / len(self.confidence_samples), 6)
            if self.confidence_samples
            else 0.0
        )
        return ConceptLearningState(
            concept_id=self.concept_id,
            recent_mistakes=tuple(self.recent_mistakes),
            misconception_signals=tuple(self.misconception_signals),
            hint_dependency=hint_dependency,
            recent_teaching_moves=tuple(self.recent_teaching_moves),
            recent_examples=tuple(self.recent_examples),
            recent_assessments=tuple(self.recent_assessments),
            recent_evidence_ids=tuple(self.recent_evidence_ids),
            self_attested_mastery=self.self_attested_mastery,
            independently_verified_mastery=self.independently_verified_mastery,
            attempt_count=self.attempt_count,
            correct_answer_count=self.correct_answer_count,
            partial_answer_count=self.partial_answer_count,
            incorrect_answer_count=self.incorrect_answer_count,
            hint_request_count=self.hint_request_count,
            misconception_signal_count=self.misconception_signal_count,
            assessment_result_count=self.assessment_result_count,
            assessment_pass_count=self.assessment_pass_count,
            assessment_fail_count=self.assessment_fail_count,
            consecutive_failures=self.consecutive_failures,
            active_learning_seconds=round(self.active_learning_seconds, 6),
            confidence=confidence,
            last_event_at=self.last_event_at,
        )


def _confidence_sample(event: StudentLearningEvent) -> float | None:
    if event.confidence is not None:
        return event.confidence
    defaults = {
        StudentLearningEventType.SELF_ATTESTED_MASTERY: 0.55,
        StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY: 1.0,
        StudentLearningEventType.ANSWER_CORRECT: 0.8,
        StudentLearningEventType.ANSWER_PARTIAL: 0.5,
        StudentLearningEventType.ANSWER_INCORRECT: 0.2,
        StudentLearningEventType.MISCONCEPTION_SIGNAL: 0.25,
    }
    if event.event_type is StudentLearningEventType.ASSESSMENT_RESULT:
        if event.assessment_score is not None:
            return event.assessment_score
        if event.assessment_passed is not None:
            return 0.8 if event.assessment_passed else 0.2
    return defaults.get(event.event_type)


def _event_order_key(event: StudentLearningEvent) -> tuple[datetime, str]:
    return event.occurred_at.astimezone(timezone.utc), event.event_id


def replay_student_learning(
    events: Iterable[StudentLearningEvent],
    *,
    student_id: str,
    session_id: str,
    policy: ReplayPolicy | None = None,
) -> StudentLearningSnapshot:
    """Project one student/session scope with idempotent deterministic replay.

    Events for other students or sessions are ignored.  Exact duplicate ids are
    applied once; conflicting content for an id raises rather than guessing.
    Out-of-order events are sorted by UTC occurrence time and then event id.
    """

    normalized_student_id = student_id.strip()
    normalized_session_id = session_id.strip()
    if not normalized_student_id or not normalized_session_id:
        raise ValueError("student_id and session_id must be non-empty")
    replay_policy = policy or ReplayPolicy()

    unique_events: dict[str, StudentLearningEvent] = {}
    for event in events:
        if event.student_id != normalized_student_id or event.session_id != normalized_session_id:
            continue
        previous = unique_events.get(event.event_id)
        if previous is None:
            unique_events[event.event_id] = event
        elif previous != event:
            raise DuplicateEventConflictError(
                f"event_id {event.event_id!r} has conflicting content"
            )

    ordered_events = sorted(unique_events.values(), key=_event_order_key)
    accumulators: dict[str, _ConceptAccumulator] = {}
    for event in ordered_events:
        accumulator = accumulators.get(event.concept_id)
        if accumulator is None:
            accumulator = _ConceptAccumulator(event.concept_id, replay_policy)
            accumulators[event.concept_id] = accumulator
        accumulator.apply(event)

    concept_states = tuple(
        accumulators[concept_id].freeze() for concept_id in sorted(accumulators)
    )
    last_event_at = _event_order_key(ordered_events[-1])[0] if ordered_events else None
    return StudentLearningSnapshot(
        student_id=normalized_student_id,
        session_id=normalized_session_id,
        concept_states=concept_states,
        applied_event_count=len(ordered_events),
        last_event_at=last_event_at,
    )


class LearnerContextAssembler:
    """Adapt one concept state to the frozen pedagogy planner contract."""

    def assemble(
        self,
        snapshot: StudentLearningSnapshot,
        *,
        current_concept: str,
        student_move: StudentMove,
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
        concept_id = current_concept.strip()
        state = snapshot.concept(concept_id)
        evidence_ids = tuple(
            dict.fromkeys(
                (*(() if state is None else state.recent_evidence_ids), *additional_evidence_ids)
            )
        )[-32:]
        resolved_assessment_state = assessment_state
        if resolved_assessment_state is None:
            resolved_assessment_state = (
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
            mastery_state=(MasteryState.UNKNOWN if state is None else state.mastery_state),
            recent_mistakes=(() if state is None else state.recent_mistakes),
            known_misconceptions=(() if state is None else state.misconception_signals),
            hint_dependency=(HintDependency.NONE if state is None else state.hint_dependency),
            recent_teaching_moves=(() if state is None else state.recent_teaching_moves),
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


def assemble_learner_context(
    snapshot: StudentLearningSnapshot,
    **kwargs: object,
) -> LearnerContext:
    """Functional shorthand for ``LearnerContextAssembler().assemble``."""

    return LearnerContextAssembler().assemble(snapshot, **kwargs)


@runtime_checkable
class StudentModelStore(Protocol):
    """Persistence seam; this module intentionally provides no implementation."""

    def append_events(self, events: Sequence[StudentLearningEvent]) -> None: ...

    def load_events(
        self,
        *,
        student_id: str,
        session_id: str,
    ) -> Sequence[StudentLearningEvent]: ...


__all__ = [
    "OUT_OF_ORDER_POLICY",
    "AssessmentObservation",
    "ConceptLearningState",
    "DuplicateEventConflictError",
    "LearnerContextAssembler",
    "ReplayPolicy",
    "StudentLearningEvent",
    "StudentLearningEventType",
    "StudentLearningSnapshot",
    "StudentModelStore",
    "assemble_learner_context",
    "replay_student_learning",
]
