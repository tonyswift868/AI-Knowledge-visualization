"""Deterministic transition reducer for durable student learning state."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ..contracts import ExampleHistory, HintDependency, MasteryState, TeacherMove
from ..student_model import (
    OUT_OF_ORDER_POLICY,
    DuplicateEventConflictError,
    StudentLearningEvent,
    StudentLearningEventType,
)


class LearningStage(str, Enum):
    UNKNOWN = "unknown"
    LEARNING = "learning"
    SELF_ATTESTED = "self_attested"
    INDEPENDENTLY_VERIFIED = "independently_verified"


@dataclass(frozen=True, slots=True)
class LearningStatePolicy:
    """Small, explicit policy surface for state and bounded-history semantics."""

    verified_requires_evidence: bool = True
    verified_is_monotonic: bool = True
    persistent_hint_threshold: int = 3
    misconception_recovery_correct_streak: int = 2
    recent_mistakes: int = 8
    recent_misconceptions: int = 8
    recent_teaching_moves: int = 8
    recent_examples: int = 8
    recent_evidence_ids: int = 32
    self_attested_confidence: float = 0.55
    correct_confidence_delta: float = 0.15
    incorrect_confidence_delta: float = 0.20
    misconception_confidence_delta: float = 0.10

    def __post_init__(self) -> None:
        integer_limits = {
            "persistent_hint_threshold": (1, 20),
            "misconception_recovery_correct_streak": (1, 20),
            "recent_mistakes": (1, 12),
            "recent_misconceptions": (1, 12),
            "recent_teaching_moves": (1, 8),
            "recent_examples": (1, 8),
            "recent_evidence_ids": (1, 32),
        }
        for name, (minimum, maximum) in integer_limits.items():
            value = getattr(self, name)
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
        for name in (
            "self_attested_confidence",
            "correct_confidence_delta",
            "incorrect_confidence_delta",
            "misconception_confidence_delta",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ConceptLearningState:
    concept_id: str
    stage: LearningStage = LearningStage.UNKNOWN
    confidence: float = 0.0
    self_attested_ever: bool = False
    independently_verified: bool = False
    verification_evidence_ids: tuple[str, ...] = ()
    recent_evidence_ids: tuple[str, ...] = ()
    attempt_count: int = 0
    correct_answer_count: int = 0
    partial_answer_count: int = 0
    incorrect_answer_count: int = 0
    assessment_result_count: int = 0
    assessment_pass_count: int = 0
    assessment_fail_count: int = 0
    hint_request_count: int = 0
    hint_dependency: HintDependency = HintDependency.NONE
    misconception_signal_count: int = 0
    misconception_history: tuple[str, ...] = ()
    active_misconceptions: tuple[str, ...] = ()
    recent_mistakes: tuple[str, ...] = ()
    recent_teaching_moves: tuple[TeacherMove, ...] = ()
    recent_examples: tuple[ExampleHistory, ...] = ()
    consecutive_correct_answers: int = 0
    consecutive_failures: int = 0
    active_learning_seconds: float = 0.0
    last_event_at: datetime | None = None
    last_event_id: str | None = None

    @property
    def mastery_state(self) -> MasteryState:
        return {
            LearningStage.UNKNOWN: MasteryState.UNKNOWN,
            LearningStage.LEARNING: MasteryState.IN_PROGRESS,
            LearningStage.SELF_ATTESTED: MasteryState.SELF_ATTESTED,
            LearningStage.INDEPENDENTLY_VERIFIED: MasteryState.INDEPENDENTLY_VERIFIED,
        }[self.stage]


@dataclass(frozen=True, slots=True)
class LearningStateTransition:
    concept_id: str
    from_stage: LearningStage
    to_stage: LearningStage
    reason: str
    source_event_ids: tuple[str, ...]
    confidence: float
    occurred_at: datetime
    order_index: int
    ordering_policy: str = OUT_OF_ORDER_POLICY

    @property
    def state_changed(self) -> bool:
        return self.from_stage is not self.to_stage

    @property
    def timestamp(self) -> datetime:
        return self.occurred_at


@dataclass(frozen=True, slots=True)
class ReducerResult:
    student_id: str
    session_id: str
    concept_states: tuple[ConceptLearningState, ...] = ()
    transitions: tuple[LearningStateTransition, ...] = ()
    applied_event_ids: tuple[str, ...] = ()
    duplicate_event_ids: tuple[str, ...] = ()
    ordering_policy: str = OUT_OF_ORDER_POLICY
    last_event_at: datetime | None = None

    def concept(self, concept_id: str) -> ConceptLearningState | None:
        normalized = concept_id.strip()
        return next(
            (state for state in self.concept_states if state.concept_id == normalized),
            None,
        )


_ATTEMPT_EVENT_TYPES = frozenset(
    {
        StudentLearningEventType.ATTEMPT,
        StudentLearningEventType.ANSWER_CORRECT,
        StudentLearningEventType.ANSWER_PARTIAL,
        StudentLearningEventType.ANSWER_INCORRECT,
        StudentLearningEventType.ASSESSMENT_RESULT,
    }
)


@dataclass(slots=True)
class _ConceptAccumulator:
    concept_id: str
    policy: LearningStatePolicy
    stage: LearningStage = LearningStage.UNKNOWN
    confidence: float = 0.0
    self_attested_ever: bool = False
    independently_verified: bool = False
    verification_evidence_ids: list[str] = field(default_factory=list)
    recent_evidence_ids: list[str] = field(default_factory=list)
    counted_attempt_ids: set[str] = field(default_factory=set)
    attempt_count: int = 0
    correct_answer_count: int = 0
    partial_answer_count: int = 0
    incorrect_answer_count: int = 0
    assessment_result_count: int = 0
    assessment_pass_count: int = 0
    assessment_fail_count: int = 0
    hint_request_count: int = 0
    misconception_signal_count: int = 0
    misconception_history: list[str] = field(default_factory=list)
    active_misconceptions: list[str] = field(default_factory=list)
    recent_mistakes: list[str] = field(default_factory=list)
    recent_teaching_moves: list[TeacherMove] = field(default_factory=list)
    recent_examples: list[ExampleHistory] = field(default_factory=list)
    consecutive_correct_answers: int = 0
    consecutive_failures: int = 0
    active_learning_seconds: float = 0.0
    last_event_at: datetime | None = None
    last_event_id: str | None = None

    @staticmethod
    def _append_bounded(values: list, value: object, limit: int) -> None:
        values.append(value)
        if len(values) > limit:
            del values[: len(values) - limit]

    @classmethod
    def _append_unique_bounded(cls, values: list[str], value: str, limit: int) -> None:
        if value in values:
            values.remove(value)
        cls._append_bounded(values, value, limit)

    def _record_attempt(self, event: StudentLearningEvent) -> None:
        if event.event_type not in _ATTEMPT_EVENT_TYPES:
            return
        attempt_key = event.attempt_id or f"event:{event.event_id}"
        if attempt_key not in self.counted_attempt_ids:
            self.counted_attempt_ids.add(attempt_key)
            self.attempt_count += 1

    def _record_evidence(self, event: StudentLearningEvent) -> None:
        for evidence_id in event.evidence_ids:
            self._append_unique_bounded(
                self.recent_evidence_ids,
                evidence_id,
                self.policy.recent_evidence_ids,
            )

    def _preserve_verified_or_learn(self) -> bool:
        if self.independently_verified and self.policy.verified_is_monotonic:
            self.stage = LearningStage.INDEPENDENTLY_VERIFIED
            return True
        self.stage = LearningStage.LEARNING
        return False

    def _recover_misconceptions_if_ready(self, reason: str) -> str:
        if (
            self.active_misconceptions
            and self.consecutive_correct_answers
            >= self.policy.misconception_recovery_correct_streak
        ):
            self.active_misconceptions.clear()
            return reason + "; misconception_recovered_after_repeated_correct"
        return reason

    def _update_confidence(
        self,
        event: StudentLearningEvent,
        *,
        accepted_verification: bool,
    ) -> None:
        if event.event_type is StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY:
            if accepted_verification:
                self.confidence = 1.0
            return
        if event.confidence is not None:
            self.confidence = event.confidence
        elif event.event_type is StudentLearningEventType.SELF_ATTESTED_MASTERY:
            self.confidence = max(self.confidence, self.policy.self_attested_confidence)
        elif event.event_type is StudentLearningEventType.ANSWER_CORRECT:
            self.confidence += self.policy.correct_confidence_delta
        elif event.event_type is StudentLearningEventType.ANSWER_PARTIAL:
            self.confidence = (self.confidence + 0.5) / 2
        elif event.event_type is StudentLearningEventType.ANSWER_INCORRECT:
            self.confidence -= self.policy.incorrect_confidence_delta
        elif event.event_type is StudentLearningEventType.MISCONCEPTION_SIGNAL:
            self.confidence -= self.policy.misconception_confidence_delta
        elif event.event_type is StudentLearningEventType.ASSESSMENT_RESULT:
            if event.assessment_score is not None:
                self.confidence = event.assessment_score
            elif event.assessment_passed is True:
                self.confidence += self.policy.correct_confidence_delta
            elif event.assessment_passed is False:
                self.confidence -= self.policy.incorrect_confidence_delta
        self.confidence = round(min(1.0, max(0.0, self.confidence)), 6)

    def apply(self, event: StudentLearningEvent, order_index: int) -> LearningStateTransition:
        previous_stage = self.stage
        accepted_verification = False
        reason = f"{event.event_type.value}_recorded"
        self.last_event_at = event.occurred_at.astimezone(timezone.utc)
        self.last_event_id = event.event_id
        self._record_attempt(event)
        self._record_evidence(event)

        if event.event_type is StudentLearningEventType.SELF_ATTESTED_MASTERY:
            self.self_attested_ever = True
            if self.independently_verified and self.policy.verified_is_monotonic:
                self.stage = LearningStage.INDEPENDENTLY_VERIFIED
                reason = "self_attestation_recorded_verified_state_preserved"
            else:
                self.stage = LearningStage.SELF_ATTESTED
                reason = "learner_self_attested_without_independent_verification"
        elif event.event_type is StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY:
            if self.policy.verified_requires_evidence and not event.evidence_ids:
                reason = "verification_rejected_missing_evidence"
            else:
                accepted_verification = True
                self.independently_verified = True
                self.stage = LearningStage.INDEPENDENTLY_VERIFIED
                for evidence_id in event.evidence_ids:
                    self._append_unique_bounded(
                        self.verification_evidence_ids,
                        evidence_id,
                        self.policy.recent_evidence_ids,
                    )
                reason = "independent_verification_accepted_with_evidence"
        elif event.event_type is StudentLearningEventType.ANSWER_CORRECT:
            self.correct_answer_count += 1
            self.consecutive_correct_answers += 1
            self.consecutive_failures = 0
            if self.stage is LearningStage.UNKNOWN:
                self.stage = LearningStage.LEARNING
            reason = "correct_answer_recorded_not_independent_verification"
            reason = self._recover_misconceptions_if_ready(reason)
        elif event.event_type is StudentLearningEventType.ANSWER_PARTIAL:
            self.partial_answer_count += 1
            self.consecutive_correct_answers = 0
            self.consecutive_failures = 0
            preserved = self._preserve_verified_or_learn()
            reason = (
                "partial_answer_recorded_verified_state_preserved"
                if preserved
                else "partial_answer_requires_continued_learning"
            )
        elif event.event_type is StudentLearningEventType.ANSWER_INCORRECT:
            self.incorrect_answer_count += 1
            self.consecutive_correct_answers = 0
            self.consecutive_failures += 1
            if event.detail:
                self._append_bounded(
                    self.recent_mistakes,
                    event.detail,
                    self.policy.recent_mistakes,
                )
            preserved = self._preserve_verified_or_learn()
            reason = (
                "incorrect_answer_recorded_verified_state_preserved"
                if preserved
                else "incorrect_answer_requires_continued_learning"
            )
        elif event.event_type is StudentLearningEventType.ASSESSMENT_RESULT:
            self.assessment_result_count += 1
            if event.assessment_passed is True:
                self.assessment_pass_count += 1
                self.consecutive_correct_answers += 1
                self.consecutive_failures = 0
                if self.stage is LearningStage.UNKNOWN:
                    self.stage = LearningStage.LEARNING
                reason = "assessment_passed_requires_explicit_verified_mastery_event"
                reason = self._recover_misconceptions_if_ready(reason)
            elif event.assessment_passed is False:
                self.assessment_fail_count += 1
                self.consecutive_correct_answers = 0
                self.consecutive_failures += 1
                if event.detail:
                    self._append_bounded(
                        self.recent_mistakes,
                        event.detail,
                        self.policy.recent_mistakes,
                    )
                was_self_attested = previous_stage is LearningStage.SELF_ATTESTED
                preserved = self._preserve_verified_or_learn()
                if preserved:
                    reason = "failed_assessment_recorded_verified_state_preserved"
                elif was_self_attested:
                    reason = "failed_assessment_invalidated_current_self_attestation"
                else:
                    reason = "failed_assessment_requires_continued_learning"
            else:
                if self.stage is LearningStage.UNKNOWN:
                    self.stage = LearningStage.LEARNING
                reason = "assessment_score_recorded_without_pass_fail_inference"
        elif event.event_type is StudentLearningEventType.HINT_REQUESTED:
            self.hint_request_count += 1
            if self.stage is LearningStage.UNKNOWN:
                self.stage = LearningStage.LEARNING
            reason = "explicit_hint_dependency_observation"
        elif event.event_type is StudentLearningEventType.MISCONCEPTION_SIGNAL:
            self.misconception_signal_count += 1
            self.consecutive_correct_answers = 0
            self._append_unique_bounded(
                self.misconception_history,
                event.detail or "",
                self.policy.recent_misconceptions,
            )
            self._append_unique_bounded(
                self.active_misconceptions,
                event.detail or "",
                self.policy.recent_misconceptions,
            )
            preserved = self._preserve_verified_or_learn()
            reason = (
                "misconception_recorded_verified_state_preserved"
                if preserved
                else "misconception_requires_continued_learning"
            )
        elif event.event_type is StudentLearningEventType.TEACHING_MOVE:
            self._append_bounded(
                self.recent_teaching_moves,
                event.teacher_move,
                self.policy.recent_teaching_moves,
            )
            if self.stage is LearningStage.UNKNOWN:
                self.stage = LearningStage.LEARNING
            reason = "teacher_move_recorded_without_mastery_inference"
        elif event.event_type is StudentLearningEventType.EXAMPLE_USED:
            self._append_bounded(
                self.recent_examples,
                ExampleHistory(
                    example_id=event.example_id,
                    strategy=event.example_strategy,
                    depth=event.example_depth,
                    effective=event.example_effective,
                ),
                self.policy.recent_examples,
            )
            if self.stage is LearningStage.UNKNOWN:
                self.stage = LearningStage.LEARNING
            reason = "example_observation_recorded_without_mastery_inference"
        elif event.event_type is StudentLearningEventType.ATTEMPT:
            if self.stage is LearningStage.UNKNOWN:
                self.stage = LearningStage.LEARNING
            reason = "attempt_recorded_without_correctness_inference"
        elif event.event_type is StudentLearningEventType.ACTIVE_LEARNING_TIME_OBSERVED:
            self.active_learning_seconds += event.active_learning_seconds or 0.0
            if self.stage is LearningStage.UNKNOWN:
                self.stage = LearningStage.LEARNING
            reason = "active_time_observation_recorded_without_mastery_inference"

        self._update_confidence(event, accepted_verification=accepted_verification)
        return LearningStateTransition(
            concept_id=self.concept_id,
            from_stage=previous_stage,
            to_stage=self.stage,
            reason=reason,
            source_event_ids=(event.event_id,),
            confidence=self.confidence,
            occurred_at=self.last_event_at,
            order_index=order_index,
        )

    def freeze(self) -> ConceptLearningState:
        if self.hint_request_count >= self.policy.persistent_hint_threshold:
            hint_dependency = HintDependency.PERSISTENT
        elif self.hint_request_count:
            hint_dependency = HintDependency.RECENT
        else:
            hint_dependency = HintDependency.NONE
        return ConceptLearningState(
            concept_id=self.concept_id,
            stage=self.stage,
            confidence=self.confidence,
            self_attested_ever=self.self_attested_ever,
            independently_verified=self.independently_verified,
            verification_evidence_ids=tuple(self.verification_evidence_ids),
            recent_evidence_ids=tuple(self.recent_evidence_ids),
            attempt_count=self.attempt_count,
            correct_answer_count=self.correct_answer_count,
            partial_answer_count=self.partial_answer_count,
            incorrect_answer_count=self.incorrect_answer_count,
            assessment_result_count=self.assessment_result_count,
            assessment_pass_count=self.assessment_pass_count,
            assessment_fail_count=self.assessment_fail_count,
            hint_request_count=self.hint_request_count,
            hint_dependency=hint_dependency,
            misconception_signal_count=self.misconception_signal_count,
            misconception_history=tuple(self.misconception_history),
            active_misconceptions=tuple(self.active_misconceptions),
            recent_mistakes=tuple(self.recent_mistakes),
            recent_teaching_moves=tuple(self.recent_teaching_moves),
            recent_examples=tuple(self.recent_examples),
            consecutive_correct_answers=self.consecutive_correct_answers,
            consecutive_failures=self.consecutive_failures,
            active_learning_seconds=round(self.active_learning_seconds, 6),
            last_event_at=self.last_event_at,
            last_event_id=self.last_event_id,
        )


def _event_order_key(event: StudentLearningEvent) -> tuple[datetime, str]:
    return event.occurred_at.astimezone(timezone.utc), event.event_id


class LearningStateReducer:
    def __init__(self, policy: LearningStatePolicy | None = None) -> None:
        self.policy = policy or LearningStatePolicy()

    def reduce(
        self,
        events: Iterable[StudentLearningEvent],
        *,
        student_id: str,
        session_id: str,
    ) -> ReducerResult:
        normalized_student_id = student_id.strip()
        normalized_session_id = session_id.strip()
        if not normalized_student_id or not normalized_session_id:
            raise ValueError("student_id and session_id must be non-empty")

        unique_events: dict[str, StudentLearningEvent] = {}
        duplicate_ids: set[str] = set()
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
            else:
                duplicate_ids.add(event.event_id)

        ordered_events = sorted(unique_events.values(), key=_event_order_key)
        accumulators: dict[str, _ConceptAccumulator] = {}
        transitions: list[LearningStateTransition] = []
        for order_index, event in enumerate(ordered_events):
            accumulator = accumulators.get(event.concept_id)
            if accumulator is None:
                accumulator = _ConceptAccumulator(event.concept_id, self.policy)
                accumulators[event.concept_id] = accumulator
            transitions.append(accumulator.apply(event, order_index))

        concept_states = tuple(
            accumulators[concept_id].freeze() for concept_id in sorted(accumulators)
        )
        return ReducerResult(
            student_id=normalized_student_id,
            session_id=normalized_session_id,
            concept_states=concept_states,
            transitions=tuple(transitions),
            applied_event_ids=tuple(event.event_id for event in ordered_events),
            duplicate_event_ids=tuple(sorted(duplicate_ids)),
            last_event_at=(
                ordered_events[-1].occurred_at.astimezone(timezone.utc)
                if ordered_events
                else None
            ),
        )


__all__ = [
    "ConceptLearningState",
    "LearningStage",
    "LearningStatePolicy",
    "LearningStateReducer",
    "LearningStateTransition",
    "ReducerResult",
]
