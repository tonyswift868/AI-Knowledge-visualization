"""Deterministic misconception diagnosis and bounded teaching recovery."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence

from ..contracts import ExampleDepth, HintDependency, StudentMove, TeacherMove
from ..examples import ExampleType
from .models import (
    MAX_RECOVERY_ATTEMPTS,
    AnswerOutcome,
    AnswerResult,
    DiagnosisCategory,
    ErrorKindHint,
    MisconceptionDiagnosis,
    RecoveryPlan,
    RecoveryStrategy,
    RecoveryTrace,
    TeachingRecoveryDecision,
    TeachingRecoveryRequest,
)


_FAILURE_RESULTS = frozenset({AnswerResult.INCORRECT, AnswerResult.PARTIAL})
_TYPE_DEPTH = {
    ExampleType.ANALOGY: ExampleDepth.LEVEL_1_REAL_WORLD,
    ExampleType.CONCRETE: ExampleDepth.LEVEL_2_CONCRETE_NUMERIC,
    ExampleType.CODE: ExampleDepth.LEVEL_3_CODE,
    ExampleType.COUNTEREXAMPLE: ExampleDepth.LEVEL_4_COUNTEREXAMPLE,
    ExampleType.PREREQUISITE: ExampleDepth.LEVEL_1_REAL_WORLD,
    ExampleType.TRANSFER: ExampleDepth.LEVEL_5_EDGE_CASE_TRANSFER,
}
_MOVE_TYPE = {
    TeacherMove.ANALOGY: ExampleType.ANALOGY,
    TeacherMove.SIMPLIFY: ExampleType.ANALOGY,
    TeacherMove.CONCRETE_EXAMPLE: ExampleType.CONCRETE,
    TeacherMove.WORKED_EXAMPLE: ExampleType.CONCRETE,
    TeacherMove.CODE_EXAMPLE: ExampleType.CODE,
    TeacherMove.COUNTEREXAMPLE: ExampleType.COUNTEREXAMPLE,
    TeacherMove.CONNECT_PREREQUISITE: ExampleType.PREREQUISITE,
    TeacherMove.TRANSFER_CHALLENGE: ExampleType.TRANSFER,
}
_TYPE_MOVE = {
    ExampleType.ANALOGY: (RecoveryStrategy.SIMPLIFY, TeacherMove.SIMPLIFY),
    ExampleType.CONCRETE: (
        RecoveryStrategy.CONCRETE_EXAMPLE,
        TeacherMove.CONCRETE_EXAMPLE,
    ),
    ExampleType.CODE: (RecoveryStrategy.CODE_EXAMPLE, TeacherMove.CODE_EXAMPLE),
    ExampleType.COUNTEREXAMPLE: (
        RecoveryStrategy.COUNTEREXAMPLE,
        TeacherMove.COUNTEREXAMPLE,
    ),
    ExampleType.PREREQUISITE: (
        RecoveryStrategy.CONNECT_PREREQUISITE,
        TeacherMove.CONNECT_PREREQUISITE,
    ),
    ExampleType.TRANSFER: (
        RecoveryStrategy.GUIDED_PRACTICE,
        TeacherMove.GUIDED_PRACTICE,
    ),
}
_ESCALATION_TYPE = {
    ExampleType.ANALOGY: ExampleType.CONCRETE,
    ExampleType.CONCRETE: ExampleType.CODE,
    ExampleType.CODE: ExampleType.COUNTEREXAMPLE,
    ExampleType.COUNTEREXAMPLE: ExampleType.TRANSFER,
    ExampleType.PREREQUISITE: ExampleType.CONCRETE,
    ExampleType.TRANSFER: ExampleType.TRANSFER,
}


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _normalize_signature(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def _failure_outcomes(request: TeachingRecoveryRequest) -> tuple[AnswerOutcome, ...]:
    return tuple(
        outcome
        for outcome in request.recent_answer_outcomes
        if outcome.result in _FAILURE_RESULTS
    )


def _correct_streak(request: TeachingRecoveryRequest) -> int:
    streak = 0
    for outcome in reversed(request.recent_answer_outcomes):
        if outcome.result is not AnswerResult.CORRECT:
            break
        streak += 1
    return streak


def _repeated_error(request: TeachingRecoveryRequest) -> tuple[str, int] | None:
    signatures = [
        _normalize_signature(outcome.error_signature)
        for outcome in _failure_outcomes(request)
        if outcome.error_signature.strip()
    ]
    if not signatures:
        return None
    signature, count = Counter(signatures).most_common(1)[0]
    return (signature, count) if count >= 2 else None


def _repeated_error_kind(
    request: TeachingRecoveryRequest,
) -> tuple[ErrorKindHint, int] | None:
    hints = [
        outcome.error_kind_hint
        for outcome in _failure_outcomes(request)
        if outcome.error_kind_hint is not None
    ]
    if not hints:
        return None
    hint, count = Counter(hints).most_common(1)[0]
    return (hint, count) if count >= 2 else None


def _trailing_same(values: Sequence[object]) -> int:
    if not values:
        return 0
    latest = values[-1]
    count = 0
    for value in reversed(values):
        if value != latest:
            break
        count += 1
    return count


def _strategy_history(
    request: TeachingRecoveryRequest,
) -> tuple[ExampleType | None, int]:
    plan_types = tuple(plan.example_type for plan in request.example_plan_history)
    move_types = tuple(
        _MOVE_TYPE.get(move)
        for move in request.recent_teaching_moves
    )
    plan_count = _trailing_same(plan_types)
    move_count = (
        _trailing_same(move_types)
        if move_types and move_types[-1] is not None
        else 0
    )
    if plan_count >= move_count and plan_types:
        return plan_types[-1], plan_count
    if move_types:
        return move_types[-1], move_count
    return None, 0


def _evidence_refs(request: TeachingRecoveryRequest) -> tuple[str, ...]:
    return _unique(
        ref
        for outcome in request.recent_answer_outcomes
        for ref in outcome.evidence_refs
    )


def _observed_signals(request: TeachingRecoveryRequest) -> tuple[str, ...]:
    failures = _failure_outcomes(request)
    repeated = _repeated_error(request)
    strategy_type, strategy_count = _strategy_history(request)
    signals = [
        f"current_concept:{request.current_concept}",
        f"student_move:{request.student_move.value}",
    ]
    if failures:
        signals.append(f"failure_outcomes:{len(failures)}")
    correct_streak = _correct_streak(request)
    if correct_streak:
        signals.append(f"correct_streak:{correct_streak}")
    if repeated is not None:
        signals.append(f"repeated_error:{repeated[0]}:{repeated[1]}")
    if strategy_type is not None:
        signals.append(f"recent_strategy:{strategy_type.value}:{strategy_count}")
    if request.prerequisite_hints:
        signals.append(f"prerequisite_hint:{request.prerequisite_hints[0]}")
    if request.known_misconception_hints:
        signals.append(
            f"known_misconception_hint:{request.known_misconception_hints[0]}"
        )
    if request.hint_dependency is not HintDependency.NONE:
        signals.append(f"hint_dependency:{request.hint_dependency.value}")
    return tuple(signals)


def _diagnosis(
    request: TeachingRecoveryRequest,
) -> MisconceptionDiagnosis:
    failures = _failure_outcomes(request)
    repeated_error = _repeated_error(request)
    repeated_kind = _repeated_error_kind(request)
    _, strategy_count = _strategy_history(request)
    evidence = _observed_signals(request)
    refs = _evidence_refs(request)

    if request.student_move is StudentMove.CHALLENGE_TEACHER:
        return MisconceptionDiagnosis(
            focus_concept=request.current_concept,
            category=DiagnosisCategory.UNKNOWN,
            confidence=0.05,
            evidence=evidence,
            evidence_refs=refs,
            reason="challenging the teacher is not evidence that the learner is wrong",
        )
    if (
        request.student_move is StudentMove.CORRECT_ANSWER_SIGNAL
        or (
            request.recent_answer_outcomes
            and request.recent_answer_outcomes[-1].result is AnswerResult.CORRECT
        )
    ):
        return MisconceptionDiagnosis(
            focus_concept=request.current_concept,
            category=DiagnosisCategory.UNKNOWN,
            confidence=0.1,
            evidence=evidence,
            evidence_refs=refs,
            reason="the latest correct outcome weakens prior diagnostic hypotheses",
        )
    if request.prerequisite_hints and (
        failures or request.student_move is StudentMove.CONFUSED
    ):
        return MisconceptionDiagnosis(
            focus_concept=request.current_concept,
            category=DiagnosisCategory.PREREQUISITE_GAP,
            confidence=0.84,
            evidence=evidence,
            evidence_refs=refs,
            reason="failure or confusion coincides with a caller-supplied prerequisite gap",
        )
    if (
        request.student_move is StudentMove.CONFUSED
        and strategy_count >= 2
    ):
        return MisconceptionDiagnosis(
            focus_concept=request.current_concept,
            category=DiagnosisCategory.CONFUSION_UNRESOLVED,
            confidence=0.72,
            evidence=evidence,
            evidence_refs=refs,
            reason="confusion remains after the same teaching strategy was repeated",
        )
    if repeated_kind is not None and repeated_kind[0] is ErrorKindHint.PROCEDURAL:
        return MisconceptionDiagnosis(
            focus_concept=request.current_concept,
            category=DiagnosisCategory.PROCEDURAL_ERROR,
            confidence=0.78,
            evidence=evidence,
            evidence_refs=refs,
            reason="multiple failed outcomes carry the same procedural error hint",
        )
    if repeated_kind is not None and repeated_kind[0] is ErrorKindHint.CARELESS:
        return MisconceptionDiagnosis(
            focus_concept=request.current_concept,
            category=DiagnosisCategory.CARELESS_ERROR,
            confidence=0.58,
            evidence=evidence,
            evidence_refs=refs,
            reason="multiple failed outcomes carry a caller-supplied careless-error hint",
        )
    if repeated_error is not None:
        target = (
            request.known_misconception_hints[0]
            if request.known_misconception_hints
            else repeated_error[0]
        )
        return MisconceptionDiagnosis(
            focus_concept=request.current_concept,
            category=DiagnosisCategory.SPECIFIC_MISCONCEPTION,
            confidence=0.86 if request.known_misconception_hints else 0.8,
            evidence=evidence,
            evidence_refs=refs,
            reason="the same error signature recurred across independent outcomes",
            target_misconception=target,
        )
    if (
        request.hint_dependency is HintDependency.PERSISTENT
        and (failures or request.student_move is StudentMove.CONFUSED)
    ):
        return MisconceptionDiagnosis(
            focus_concept=request.current_concept,
            category=DiagnosisCategory.KNOWLEDGE_GAP,
            confidence=0.64,
            evidence=evidence,
            evidence_refs=refs,
            reason="persistent hint dependence plus failure suggests an unresolved gap",
        )
    if len(failures) >= 2:
        return MisconceptionDiagnosis(
            focus_concept=request.current_concept,
            category=DiagnosisCategory.KNOWLEDGE_GAP,
            confidence=0.62,
            evidence=evidence,
            evidence_refs=refs,
            reason="multiple failures exist without one stable misconception signature",
        )
    if failures or request.student_move in {
        StudentMove.CONFUSED,
        StudentMove.INCORRECT_ANSWER_SIGNAL,
        StudentMove.PARTIAL_ANSWER_SIGNAL,
    }:
        return MisconceptionDiagnosis(
            focus_concept=request.current_concept,
            category=DiagnosisCategory.LOW_EVIDENCE,
            confidence=0.3,
            evidence=evidence,
            evidence_refs=refs,
            reason="one failure or confusion signal is insufficient for a stable diagnosis",
        )
    return MisconceptionDiagnosis(
        focus_concept=request.current_concept,
        category=DiagnosisCategory.UNKNOWN,
        confidence=0.0,
        evidence=evidence,
        evidence_refs=refs,
        reason="no reliable diagnostic evidence is available",
    )


_REJECTED = {
    DiagnosisCategory.SPECIFIC_MISCONCEPTION: (
        DiagnosisCategory.CARELESS_ERROR,
        DiagnosisCategory.KNOWLEDGE_GAP,
    ),
    DiagnosisCategory.PREREQUISITE_GAP: (
        DiagnosisCategory.SPECIFIC_MISCONCEPTION,
        DiagnosisCategory.KNOWLEDGE_GAP,
    ),
    DiagnosisCategory.PROCEDURAL_ERROR: (
        DiagnosisCategory.SPECIFIC_MISCONCEPTION,
        DiagnosisCategory.CARELESS_ERROR,
    ),
    DiagnosisCategory.CARELESS_ERROR: (
        DiagnosisCategory.SPECIFIC_MISCONCEPTION,
        DiagnosisCategory.PROCEDURAL_ERROR,
    ),
    DiagnosisCategory.KNOWLEDGE_GAP: (
        DiagnosisCategory.SPECIFIC_MISCONCEPTION,
        DiagnosisCategory.PREREQUISITE_GAP,
    ),
    DiagnosisCategory.CONFUSION_UNRESOLVED: (
        DiagnosisCategory.SPECIFIC_MISCONCEPTION,
        DiagnosisCategory.PROCEDURAL_ERROR,
    ),
    DiagnosisCategory.LOW_EVIDENCE: (
        DiagnosisCategory.SPECIFIC_MISCONCEPTION,
        DiagnosisCategory.PROCEDURAL_ERROR,
    ),
    DiagnosisCategory.UNKNOWN: (
        DiagnosisCategory.SPECIFIC_MISCONCEPTION,
        DiagnosisCategory.KNOWLEDGE_GAP,
    ),
}


def _level_is_beginner(learner_level: str) -> bool:
    return learner_level.strip().casefold() in {
        "beginner",
        "foundation",
        "novice",
        "初学",
        "初学者",
        "入门",
    }


def _escalated_strategy(
    request: TeachingRecoveryRequest,
) -> tuple[RecoveryStrategy, TeacherMove, ExampleType, str]:
    previous_type, _ = _strategy_history(request)
    previous_type = previous_type or ExampleType.ANALOGY
    next_type = _ESCALATION_TYPE[previous_type]
    strategy, move = _TYPE_MOVE[next_type]
    return (
        strategy,
        move,
        next_type,
        f"repeated {previous_type.value} attempts require a different strategy",
    )


def _recovery_components(
    request: TeachingRecoveryRequest,
    diagnosis: MisconceptionDiagnosis,
) -> tuple[
    RecoveryStrategy,
    TeacherMove,
    ExampleType | None,
    bool,
    bool,
    str | None,
    str,
]:
    correct_streak = _correct_streak(request)
    _, strategy_count = _strategy_history(request)
    exhausted = request.recovery_attempts >= MAX_RECOVERY_ATTEMPTS

    if exhausted:
        return (
            RecoveryStrategy.REVIEW,
            TeacherMove.DIAGNOSE,
            None,
            True,
            False,
            None,
            "bounded recovery attempts are exhausted; stop escalation and review evidence",
        )
    if correct_streak >= 2:
        return (
            RecoveryStrategy.REVIEW,
            TeacherMove.RETRIEVAL_PROMPT,
            None,
            False,
            True,
            None,
            "two consecutive correct outcomes support a cautious assessment recommendation",
        )
    if request.student_move is StudentMove.CHALLENGE_TEACHER:
        return (
            RecoveryStrategy.REVIEW,
            TeacherMove.ANSWER_DIRECTLY,
            None,
            False,
            False,
            None,
            "address the challenge without treating it as an incorrect answer",
        )
    if diagnosis.category is DiagnosisCategory.PREREQUISITE_GAP:
        return (
            RecoveryStrategy.CONNECT_PREREQUISITE,
            TeacherMove.CONNECT_PREREQUISITE,
            ExampleType.PREREQUISITE,
            True,
            False,
            request.prerequisite_hints[0],
            "bridge the supplied prerequisite before resuming the current concept",
        )
    if (
        diagnosis.category is DiagnosisCategory.CONFUSION_UNRESOLVED
        and strategy_count >= 2
    ):
        strategy, move, example_type, reason = _escalated_strategy(request)
        return strategy, move, example_type, True, False, None, reason
    if diagnosis.category is DiagnosisCategory.SPECIFIC_MISCONCEPTION:
        return (
            RecoveryStrategy.TARGETED_CORRECTION,
            TeacherMove.CORRECT,
            ExampleType.COUNTEREXAMPLE,
            True,
            False,
            None,
            "correct only the repeated misconception and contrast its boundary",
        )
    if diagnosis.category is DiagnosisCategory.PROCEDURAL_ERROR:
        return (
            RecoveryStrategy.GUIDED_PRACTICE,
            TeacherMove.GUIDED_PRACTICE,
            ExampleType.CONCRETE,
            True,
            False,
            None,
            "rehearse the procedure with a bounded concrete walkthrough",
        )
    if diagnosis.category is DiagnosisCategory.CARELESS_ERROR:
        return (
            RecoveryStrategy.GUIDED_PRACTICE,
            TeacherMove.GUIDED_PRACTICE,
            ExampleType.CONCRETE,
            True,
            False,
            None,
            "use a short verification pass instead of reteaching the whole concept",
        )
    if request.hint_dependency is HintDependency.PERSISTENT:
        return (
            RecoveryStrategy.GUIDED_PRACTICE,
            TeacherMove.GUIDED_PRACTICE,
            ExampleType.CONCRETE,
            True,
            False,
            None,
            "replace repeated hints with guided independent steps",
        )
    if diagnosis.category is DiagnosisCategory.KNOWLEDGE_GAP:
        if _level_is_beginner(request.learner_level):
            return (
                RecoveryStrategy.SIMPLIFY,
                TeacherMove.SIMPLIFY,
                ExampleType.ANALOGY,
                True,
                False,
                None,
                "reduce complexity before asking for another attempt",
            )
        return (
            RecoveryStrategy.CONCRETE_EXAMPLE,
            TeacherMove.CONCRETE_EXAMPLE,
            ExampleType.CONCRETE,
            True,
            False,
            None,
            "ground the unresolved gap in a concrete worked situation",
        )
    if diagnosis.category is DiagnosisCategory.CONFUSION_UNRESOLVED:
        return (
            RecoveryStrategy.CONCRETE_EXAMPLE,
            TeacherMove.CONCRETE_EXAMPLE,
            ExampleType.CONCRETE,
            True,
            False,
            None,
            "change representation without claiming a specific misconception",
        )
    if diagnosis.category is DiagnosisCategory.LOW_EVIDENCE:
        return (
            RecoveryStrategy.DELAY_ASSESSMENT,
            TeacherMove.DIAGNOSE,
            None,
            True,
            False,
            None,
            "collect another diagnostic signal before selecting a targeted correction",
        )
    return (
        RecoveryStrategy.REVIEW,
        TeacherMove.DIAGNOSE,
        None,
        False,
        False,
        None,
        "review available signals without forcing a diagnosis",
    )


def _recovery(
    request: TeachingRecoveryRequest,
    diagnosis: MisconceptionDiagnosis,
) -> RecoveryPlan:
    (
        strategy,
        move,
        example_type,
        assessment_deferred,
        assessment_recommended,
        prerequisite_fallback,
        reason,
    ) = _recovery_components(request, diagnosis)
    _, strategy_count = _strategy_history(request)
    attempt_number = min(request.recovery_attempts + 1, MAX_RECOVERY_ATTEMPTS)
    exhausted = request.recovery_attempts >= MAX_RECOVERY_ATTEMPTS
    escalation_count = min(max(strategy_count - 1, 0), MAX_RECOVERY_ATTEMPTS)
    trace = RecoveryTrace(
        observed_signals=_observed_signals(request),
        selected_diagnosis=diagnosis.category,
        rejected_alternatives=_REJECTED[diagnosis.category],
        recovery_reason=reason,
        strategy_escalation_count=escalation_count,
    )
    return RecoveryPlan(
        strategy=strategy,
        recommended_teaching_move=move,
        recommended_example_type=example_type,
        recommended_example_depth=(
            _TYPE_DEPTH[example_type] if example_type is not None else None
        ),
        prerequisite_fallback=prerequisite_fallback,
        assessment_deferred=assessment_deferred,
        assessment_recommended=assessment_recommended,
        attempt_number=attempt_number,
        attempts_exhausted=exhausted,
        reason=reason,
        trace=trace,
    )


class TeachingRecoveryEngine:
    """Pure facade for diagnosis and recovery planning."""

    __slots__ = ()

    @property
    def provider_calls(self) -> int:
        return 0

    def diagnose(
        self,
        request: TeachingRecoveryRequest,
    ) -> MisconceptionDiagnosis:
        return _diagnosis(request)

    def plan_recovery(
        self,
        request: TeachingRecoveryRequest,
        diagnosis: MisconceptionDiagnosis | None = None,
    ) -> RecoveryPlan:
        selected = diagnosis or self.diagnose(request)
        return _recovery(request, selected)

    def evaluate(
        self,
        request: TeachingRecoveryRequest,
    ) -> TeachingRecoveryDecision:
        diagnosis = self.diagnose(request)
        return TeachingRecoveryDecision(
            diagnosis=diagnosis,
            recovery=self.plan_recovery(request, diagnosis),
        )


def diagnose_misconception(
    request: TeachingRecoveryRequest,
) -> MisconceptionDiagnosis:
    return TeachingRecoveryEngine().diagnose(request)


def plan_teaching_recovery(
    request: TeachingRecoveryRequest,
) -> TeachingRecoveryDecision:
    return TeachingRecoveryEngine().evaluate(request)


__all__ = [
    "TeachingRecoveryEngine",
    "diagnose_misconception",
    "plan_teaching_recovery",
]
