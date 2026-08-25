"""Evaluator v2 with deterministic objective and single-call semantic paths."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Sequence

from pydantic import ValidationError

from ..contracts import StudentMove
from .models import (
    AssessmentAttempt,
    EvaluationEvidence,
    EvaluationOutcome,
    EvaluationPath,
    EvaluationResult,
    EvaluationRubric,
    EvaluatorDecision,
    EvaluatorRequest,
    ProposedLearningEvent,
    ProposedLearningEventType,
    RubricCriterionResult,
    SemanticEvaluationDraft,
    SemanticEvaluationPayload,
    SemanticVerdict,
)
from .ports import SemanticEvaluatorPort


_SCORED_OUTCOMES = frozenset(
    {
        EvaluationOutcome.CORRECT,
        EvaluationOutcome.PARTIAL,
        EvaluationOutcome.INCORRECT,
    }
)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def _idempotency_key(request: EvaluatorRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rubric_issue(rubric: EvaluationRubric) -> str | None:
    if rubric.passing_score <= rubric.partial_credit:
        return "score_thresholds_overlap"
    if len(rubric.required_evidence_ids) != len(set(rubric.required_evidence_ids)):
        return "duplicate_required_evidence_id"

    accepted = {_normalize(value) for value in rubric.accepted_answers}
    partial = {_normalize(value) for value in rubric.partial_answers}
    misconception = {
        _normalize(item.answer) for item in rubric.misconception_answers
    }
    if rubric.path is EvaluationPath.OBJECTIVE:
        if not accepted and not partial and not misconception:
            return "objective_rubric_has_no_answers"
        if rubric.criteria:
            return "objective_rubric_mixes_semantic_criteria"
        if accepted & partial or accepted & misconception or partial & misconception:
            return "objective_answer_categories_overlap"
        return None

    if accepted or partial or misconception:
        return "semantic_rubric_mixes_objective_answers"
    if not rubric.criteria:
        return "semantic_rubric_has_no_criteria"
    criterion_ids = tuple(item.criterion_id for item in rubric.criteria)
    if len(criterion_ids) != len(set(criterion_ids)):
        return "duplicate_criterion_id"
    if abs(sum(item.weight for item in rubric.criteria) - 1.0) > 1e-9:
        return "semantic_criterion_weights_must_sum_to_one"
    return None


def _evidence_by_id(
    evidence: Sequence[EvaluationEvidence],
) -> tuple[dict[str, EvaluationEvidence], str | None]:
    result: dict[str, EvaluationEvidence] = {}
    for item in evidence:
        if item.evidence_id in result:
            return {}, "duplicate_evidence_id"
        result[item.evidence_id] = item
    return result, None


def _grounding_issue(
    request: EvaluatorRequest,
    by_id: dict[str, EvaluationEvidence],
) -> tuple[EvaluationOutcome, str] | None:
    missing = tuple(
        evidence_id
        for evidence_id in request.rubric.required_evidence_ids
        if evidence_id not in by_id or not by_id[evidence_id].verified
    )
    if missing:
        return (
            EvaluationOutcome.INSUFFICIENT_EVIDENCE,
            "required_evidence_missing:" + ",".join(missing),
        )
    invalid_answer_refs = tuple(
        evidence_id
        for evidence_id in request.attempt.answer_evidence_refs
        if evidence_id not in by_id or not by_id[evidence_id].verified
    )
    if invalid_answer_refs:
        return (
            EvaluationOutcome.INSUFFICIENT_EVIDENCE,
            "answer_evidence_missing:" + ",".join(invalid_answer_refs),
        )
    return None


def _result(
    request: EvaluatorRequest,
    *,
    outcome: EvaluationOutcome,
    score: float | None,
    confidence: float,
    reason: str,
    evidence_refs: Sequence[str] = (),
    rubric_signals: Sequence[str] = (),
    criterion_results: Sequence[RubricCriterionResult] = (),
    provider_calls: int = 0,
) -> EvaluationResult:
    return EvaluationResult(
        assessment_id=request.attempt.assessment_id,
        attempt_id=request.attempt.attempt_id,
        rubric_id=request.rubric.rubric_id,
        path=request.rubric.path,
        outcome=outcome,
        score=score,
        confidence=confidence,
        reason=reason,
        evidence_refs=_unique(evidence_refs),
        rubric_signals=tuple(rubric_signals),
        criterion_results=tuple(criterion_results),
        provider_calls=provider_calls,
    )


def _preflight(
    request: EvaluatorRequest,
) -> tuple[EvaluationResult | None, dict[str, EvaluationEvidence], bool]:
    if request.attempt.assessment_id in set(request.processed_assessment_ids):
        return (
            _result(
                request,
                outcome=EvaluationOutcome.INVALID_ATTEMPT,
                score=None,
                confidence=1.0,
                reason="duplicate_assessment_id",
                rubric_signals=("duplicate_assessment_id",),
            ),
            {},
            True,
        )
    if request.attempt.student_move is StudentMove.CHALLENGE_TEACHER:
        return (
            _result(
                request,
                outcome=EvaluationOutcome.INVALID_ATTEMPT,
                score=None,
                confidence=1.0,
                reason="challenge_teacher_is_not_an_assessment_answer",
                rubric_signals=("student_move:challenge_teacher",),
            ),
            {},
            False,
        )
    if not request.attempt.answer.strip():
        return (
            _result(
                request,
                outcome=EvaluationOutcome.INVALID_ATTEMPT,
                score=None,
                confidence=1.0,
                reason="blank_answer",
                rubric_signals=("answer:blank",),
            ),
            {},
            False,
        )

    issue = _rubric_issue(request.rubric)
    if issue is not None:
        return (
            _result(
                request,
                outcome=EvaluationOutcome.AMBIGUOUS,
                score=None,
                confidence=1.0,
                reason=issue,
                rubric_signals=(f"rubric_issue:{issue}",),
            ),
            {},
            False,
        )
    by_id, evidence_issue = _evidence_by_id(request.evidence)
    if evidence_issue is not None:
        return (
            _result(
                request,
                outcome=EvaluationOutcome.INVALID_ATTEMPT,
                score=None,
                confidence=1.0,
                reason=evidence_issue,
                rubric_signals=(f"evidence_issue:{evidence_issue}",),
            ),
            {},
            False,
        )
    grounding_issue = _grounding_issue(request, by_id)
    if grounding_issue is not None:
        return (
            _result(
                request,
                outcome=grounding_issue[0],
                score=None,
                confidence=1.0,
                reason=grounding_issue[1],
                rubric_signals=(grounding_issue[1],),
            ),
            by_id,
            False,
        )
    return None, by_id, False


def _objective_result(
    request: EvaluatorRequest,
    by_id: dict[str, EvaluationEvidence],
) -> EvaluationResult:
    answer = _normalize(request.attempt.answer)
    accepted = {_normalize(value) for value in request.rubric.accepted_answers}
    partial = {_normalize(value) for value in request.rubric.partial_answers}
    misconception = {
        _normalize(item.answer): item.misconception
        for item in request.rubric.misconception_answers
    }
    grounding_refs = _unique(
        (
            *request.rubric.required_evidence_ids,
            *request.attempt.answer_evidence_refs,
        )
    )
    grounding_refs = tuple(ref for ref in grounding_refs if ref in by_id)

    if answer in accepted:
        return _result(
            request,
            outcome=EvaluationOutcome.CORRECT,
            score=1.0,
            confidence=1.0,
            reason="answer exactly matches an accepted objective-rubric value",
            evidence_refs=grounding_refs,
            rubric_signals=("objective_match:accepted",),
        )
    if answer in partial:
        return _result(
            request,
            outcome=EvaluationOutcome.PARTIAL,
            score=request.rubric.partial_credit,
            confidence=0.95,
            reason="answer exactly matches a partial-credit rubric value",
            evidence_refs=grounding_refs,
            rubric_signals=("objective_match:partial",),
        )
    if answer in misconception:
        return _result(
            request,
            outcome=EvaluationOutcome.INCORRECT,
            score=0.0,
            confidence=0.98,
            reason="answer matches misconception rubric: " + misconception[answer],
            evidence_refs=grounding_refs,
            rubric_signals=("objective_match:misconception",),
        )
    return _result(
        request,
        outcome=EvaluationOutcome.INCORRECT,
        score=0.0,
        confidence=0.75,
        reason="answer does not match any grounded objective-rubric value",
        evidence_refs=grounding_refs,
        rubric_signals=("objective_match:none",),
    )


def _semantic_score_issue(
    rubric: EvaluationRubric,
    draft: SemanticEvaluationDraft,
) -> str | None:
    if draft.outcome is SemanticVerdict.CORRECT and draft.score < rubric.passing_score:
        return "correct_verdict_below_passing_score"
    if draft.outcome is SemanticVerdict.PARTIAL and not (
        rubric.partial_credit <= draft.score < rubric.passing_score
    ):
        return "partial_verdict_outside_partial_band"
    if draft.outcome is SemanticVerdict.INCORRECT and draft.score >= rubric.partial_credit:
        return "incorrect_verdict_above_incorrect_band"
    return None


def _semantic_grounding_issue(
    request: EvaluatorRequest,
    selected_by_id: dict[str, EvaluationEvidence],
    draft: SemanticEvaluationDraft,
) -> tuple[EvaluationOutcome, str] | None:
    cited = tuple(draft.cited_evidence_ids)
    if len(cited) != len(set(cited)):
        return EvaluationOutcome.INVALID_ATTEMPT, "duplicate_provider_citation"
    unknown = tuple(
        evidence_id for evidence_id in cited if evidence_id not in selected_by_id
    )
    if unknown:
        return EvaluationOutcome.INVALID_ATTEMPT, "fabricated_citation"
    missing_required = tuple(
        evidence_id
        for evidence_id in request.rubric.required_evidence_ids
        if evidence_id not in cited
    )
    if missing_required:
        return EvaluationOutcome.INSUFFICIENT_EVIDENCE, "provider_omitted_required_evidence"

    expected_criteria = tuple(item.criterion_id for item in request.rubric.criteria)
    actual_criteria = tuple(item.criterion_id for item in draft.criterion_results)
    if len(actual_criteria) != len(set(actual_criteria)):
        return EvaluationOutcome.INVALID_ATTEMPT, "duplicate_provider_criterion"
    if set(actual_criteria) != set(expected_criteria):
        return EvaluationOutcome.INVALID_ATTEMPT, "provider_criterion_mismatch"
    return None


def _semantic_result(
    request: EvaluatorRequest,
    draft: SemanticEvaluationDraft,
) -> EvaluationResult:
    outcome = EvaluationOutcome(draft.outcome.value)
    return _result(
        request,
        outcome=outcome,
        score=draft.score,
        confidence=draft.confidence,
        reason=draft.reason,
        evidence_refs=draft.cited_evidence_ids,
        rubric_signals=(
            "semantic_provider:structured",
            f"criteria_grounded:{len(draft.criterion_results)}",
        ),
        criterion_results=draft.criterion_results,
        provider_calls=1,
    )


def _event_id(
    idempotency_key: str,
    event_type: ProposedLearningEventType,
    result: EvaluationResult,
) -> str:
    payload = (
        f"{idempotency_key}:{event_type.value}:{result.outcome.value}:"
        f"{result.score}:{result.confidence}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def _proposed_events(
    request: EvaluatorRequest,
    result: EvaluationResult,
    idempotency_key: str,
) -> tuple[ProposedLearningEvent, ...]:
    events: list[ProposedLearningEvent] = []
    answer_event = {
        EvaluationOutcome.CORRECT: ProposedLearningEventType.ANSWER_CORRECT,
        EvaluationOutcome.PARTIAL: ProposedLearningEventType.ANSWER_PARTIAL,
        EvaluationOutcome.INCORRECT: ProposedLearningEventType.ANSWER_INCORRECT,
    }.get(result.outcome)
    if answer_event is not None:
        events.append(
            ProposedLearningEvent(
                event_id=_event_id(idempotency_key, answer_event, result),
                event_type=answer_event,
                assessment_id=result.assessment_id,
                evaluation_outcome=result.outcome,
                score=result.score,
                confidence=result.confidence,
                evidence_refs=result.evidence_refs,
            )
        )

    events.append(
        ProposedLearningEvent(
            event_id=_event_id(
                idempotency_key,
                ProposedLearningEventType.ASSESSMENT_RESULT,
                result,
            ),
            event_type=ProposedLearningEventType.ASSESSMENT_RESULT,
            assessment_id=result.assessment_id,
            evaluation_outcome=result.outcome,
            score=result.score,
            confidence=result.confidence,
            evidence_refs=result.evidence_refs,
        )
    )
    independently_verified = (
        result.outcome is EvaluationOutcome.CORRECT
        and result.score is not None
        and result.score >= request.rubric.passing_score
        and result.confidence >= 0.8
        and not request.attempt.self_attested_mastery
        and request.attempt.student_move is not StudentMove.LEARNER_SAYS_MASTERED
    )
    if independently_verified:
        events.append(
            ProposedLearningEvent(
                event_id=_event_id(
                    idempotency_key,
                    ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL,
                    result,
                ),
                event_type=ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL,
                assessment_id=result.assessment_id,
                evaluation_outcome=result.outcome,
                score=result.score,
                confidence=result.confidence,
                evidence_refs=result.evidence_refs,
            )
        )
    return tuple(events)


def _decision(
    request: EvaluatorRequest,
    result: EvaluationResult,
    *,
    suppress_events: bool = False,
) -> EvaluatorDecision:
    key = _idempotency_key(request)
    return EvaluatorDecision(
        idempotency_key=key,
        result=result,
        proposed_learning_events=(
            () if suppress_events else _proposed_events(request, result, key)
        ),
    )


class EvaluatorV2:
    """Pure evaluator facade; it never writes learning or planning state."""

    __slots__ = ("_semantic_port",)

    def __init__(self, semantic_port: SemanticEvaluatorPort | None = None) -> None:
        self._semantic_port = semantic_port

    def evaluate_objective(self, request: EvaluatorRequest) -> EvaluatorDecision:
        if request.rubric.path is not EvaluationPath.OBJECTIVE:
            result = _result(
                request,
                outcome=EvaluationOutcome.INVALID_ATTEMPT,
                score=None,
                confidence=1.0,
                reason="objective_evaluator_requires_objective_rubric",
                rubric_signals=("path_mismatch",),
            )
            return _decision(request, result)
        preflight, by_id, suppress_events = _preflight(request)
        if preflight is not None:
            return _decision(request, preflight, suppress_events=suppress_events)
        return _decision(request, _objective_result(request, by_id))

    async def evaluate_semantic(self, request: EvaluatorRequest) -> EvaluatorDecision:
        if request.rubric.path is not EvaluationPath.SEMANTIC:
            result = _result(
                request,
                outcome=EvaluationOutcome.INVALID_ATTEMPT,
                score=None,
                confidence=1.0,
                reason="semantic_evaluator_requires_semantic_rubric",
                rubric_signals=("path_mismatch",),
            )
            return _decision(request, result)
        preflight, by_id, suppress_events = _preflight(request)
        if preflight is not None:
            return _decision(request, preflight, suppress_events=suppress_events)
        if self._semantic_port is None:
            result = _result(
                request,
                outcome=EvaluationOutcome.INSUFFICIENT_EVIDENCE,
                score=None,
                confidence=1.0,
                reason="semantic_provider_unavailable",
                rubric_signals=("semantic_provider:unavailable",),
            )
            return _decision(request, result)

        selected_ids = _unique(
            (
                *request.rubric.required_evidence_ids,
                *request.attempt.answer_evidence_refs,
            )
        )
        selected_evidence = (
            tuple(by_id[evidence_id] for evidence_id in selected_ids)
            if selected_ids
            else tuple(
                by_id[evidence_id]
                for evidence_id in sorted(by_id)
                if by_id[evidence_id].verified
            )
        )
        payload = SemanticEvaluationPayload(
            assessment_id=request.attempt.assessment_id,
            question=request.attempt.question,
            answer=request.attempt.answer.strip(),
            rubric_id=request.rubric.rubric_id,
            criteria=request.rubric.criteria,
            evidence=selected_evidence,
        )
        try:
            raw = await self._semantic_port.structured(
                payload.model_dump(mode="json"),
                SemanticEvaluationDraft,
            )
        except TimeoutError:
            result = _result(
                request,
                outcome=EvaluationOutcome.INSUFFICIENT_EVIDENCE,
                score=None,
                confidence=0.0,
                reason="semantic_provider_timeout",
                rubric_signals=("semantic_provider:timeout",),
                provider_calls=1,
            )
            return _decision(request, result)
        except Exception as exc:
            result = _result(
                request,
                outcome=EvaluationOutcome.INVALID_ATTEMPT,
                score=None,
                confidence=0.0,
                reason=f"semantic_provider_error:{type(exc).__name__}",
                rubric_signals=("semantic_provider:error",),
                provider_calls=1,
            )
            return _decision(request, result)

        try:
            draft = SemanticEvaluationDraft.model_validate(raw)
        except (ValidationError, TypeError, ValueError):
            result = _result(
                request,
                outcome=EvaluationOutcome.INVALID_ATTEMPT,
                score=None,
                confidence=0.0,
                reason="semantic_provider_output_invalid",
                rubric_signals=("semantic_provider:invalid_output",),
                provider_calls=1,
            )
            return _decision(request, result)

        selected_by_id = {item.evidence_id: item for item in selected_evidence}
        grounding_issue = _semantic_grounding_issue(request, selected_by_id, draft)
        score_issue = _semantic_score_issue(request.rubric, draft)
        if grounding_issue is not None or score_issue is not None:
            outcome, reason = grounding_issue or (
                EvaluationOutcome.INVALID_ATTEMPT,
                score_issue,
            )
            result = _result(
                request,
                outcome=outcome,
                score=None,
                confidence=0.0,
                reason=reason or "semantic_provider_output_invalid",
                rubric_signals=(f"semantic_provider:{reason}",),
                provider_calls=1,
            )
            return _decision(request, result)
        return _decision(request, _semantic_result(request, draft))

    async def evaluate(self, request: EvaluatorRequest) -> EvaluatorDecision:
        if request.rubric.path is EvaluationPath.OBJECTIVE:
            return self.evaluate_objective(request)
        return await self.evaluate_semantic(request)


async def evaluate_assessment(
    request: EvaluatorRequest,
    *,
    semantic_port: SemanticEvaluatorPort | None = None,
) -> EvaluatorDecision:
    return await EvaluatorV2(semantic_port).evaluate(request)


__all__ = ["EvaluatorV2", "evaluate_assessment"]
