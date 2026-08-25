"""Explicit policy for proposing independent-verification signals."""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts import HintDependency
from ..evaluator_v2 import EvaluationOutcome, EvaluationResult, EvaluationRubric
from .models import (
    AssessmentQuestion,
    AssessmentReceipt,
    AssessmentSubmission,
    VerificationPolicy,
    VerificationQualification,
    VerificationStatus,
)


def _rubric_coverage(
    rubric: EvaluationRubric,
    result: EvaluationResult,
) -> float:
    if result.outcome is EvaluationOutcome.CORRECT and not rubric.criteria:
        return 1.0
    if result.outcome is EvaluationOutcome.PARTIAL and not rubric.criteria:
        return rubric.partial_credit
    if not rubric.criteria or not result.criterion_results:
        return 0.0
    satisfied = {
        item.criterion_id
        for item in result.criterion_results
        if item.satisfied
    }
    return min(
        1.0,
        sum(item.weight for item in rubric.criteria if item.criterion_id in satisfied),
    )


def qualify_verification(
    *,
    policy: VerificationPolicy,
    question: AssessmentQuestion,
    rubric: EvaluationRubric,
    submission: AssessmentSubmission,
    result: EvaluationResult,
    attempt_number: int,
    prior_receipts: Sequence[AssessmentReceipt],
) -> VerificationQualification:
    coverage = _rubric_coverage(rubric, result)
    required_grounding = tuple(
        dict.fromkeys((*question.evidence_ids, *rubric.required_evidence_ids))
    )
    evidence_grounded = (
        bool(required_grounding)
        and set(required_grounding) <= set(result.evidence_refs)
    )
    hint_free = not submission.hint_used
    hint_dependency_clear = (
        submission.hint_dependency is not HintDependency.PERSISTENT
        or policy.allow_persistent_hint_dependency
    )
    first_attempt = attempt_number == 1
    type_allowed = question.assessment_type in policy.allowed_assessment_types
    attempt_conditions_met = (
        (hint_free or policy.allow_hint_used_attempt)
        and hint_dependency_clear
        and (first_attempt or policy.allow_retry_attempt)
        and not submission.self_attested_mastery
    )
    score_ready = (
        result.score is not None
        and result.score >= policy.minimum_score
        and result.confidence >= policy.minimum_confidence
    )
    grounding_ready = evidence_grounded or not policy.require_evidence_grounding
    question_qualified = (
        result.outcome is EvaluationOutcome.CORRECT
        and type_allowed
        and coverage >= policy.minimum_rubric_coverage
        and score_ready
        and grounding_ready
        and attempt_conditions_met
    )
    qualified_ids = list(
        dict.fromkeys(
            receipt.question_id
            for receipt in prior_receipts
            if receipt.verification.question_qualified
        )
    )
    if question_qualified and question.question_id not in qualified_ids:
        qualified_ids.append(question.question_id)

    insufficient_result = result.outcome in {
        EvaluationOutcome.INSUFFICIENT_EVIDENCE,
        EvaluationOutcome.AMBIGUOUS,
    } or (
        result.outcome is EvaluationOutcome.INVALID_ATTEMPT
        and result.reason.startswith("semantic_provider")
    )
    if insufficient_result or (
        policy.require_evidence_grounding
        and result.outcome is EvaluationOutcome.CORRECT
        and not evidence_grounded
    ):
        status = VerificationStatus.INSUFFICIENT_EVIDENCE
        reason = "evaluation lacks policy-required verification evidence"
    elif len(qualified_ids) >= policy.minimum_question_count:
        status = VerificationStatus.QUALIFIED
        reason = "session satisfies all explicit independent-verification gates"
    else:
        status = VerificationStatus.NOT_QUALIFIED
        reason = "assessment result does not yet satisfy every verification gate"

    return VerificationQualification(
        status=status,
        policy_id=policy.policy_id,
        assessment_type=question.assessment_type,
        rubric_coverage=coverage,
        evidence_grounded=evidence_grounded,
        hint_free=hint_free,
        hint_dependency=submission.hint_dependency,
        hint_dependency_clear=hint_dependency_clear,
        first_attempt=first_attempt,
        attempt_conditions_met=attempt_conditions_met,
        question_qualified=question_qualified,
        qualified_question_ids=tuple(qualified_ids),
        required_question_count=policy.minimum_question_count,
        reason=reason,
    )


def rejected_qualification(
    *,
    policy: VerificationPolicy,
    question: AssessmentQuestion,
    reason: str,
    insufficient: bool = False,
) -> VerificationQualification:
    return VerificationQualification(
        status=(
            VerificationStatus.INSUFFICIENT_EVIDENCE
            if insufficient
            else VerificationStatus.NOT_QUALIFIED
        ),
        policy_id=policy.policy_id,
        assessment_type=question.assessment_type,
        rubric_coverage=0.0,
        evidence_grounded=False,
        hint_free=True,
        hint_dependency=HintDependency.NONE,
        hint_dependency_clear=True,
        first_attempt=True,
        attempt_conditions_met=False,
        question_qualified=False,
        required_question_count=policy.minimum_question_count,
        reason=reason,
    )


__all__ = ["qualify_verification", "rejected_qualification"]
