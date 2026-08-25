"""Retrieval classification and completion gates for active recall."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ..contracts import HintDependency
from ..evaluator_v2 import EvaluationOutcome, EvaluationResult
from .models import (
    ReviewAttempt,
    ReviewAttemptReceipt,
    ReviewCompletionEvaluation,
    ReviewCompletionPolicy,
    ReviewItem,
    ReviewItemResult,
    ReviewRetrievalOutcome,
    ReviewWeakReason,
)


def classify_retrieval(
    *,
    attempt: ReviewAttempt,
    result: EvaluationResult,
    retry_number: int,
    prior_item_attempts: Sequence[ReviewAttempt],
    policy: ReviewCompletionPolicy,
) -> tuple[ReviewRetrievalOutcome, float, ReviewWeakReason | None]:
    answer_was_revealed = attempt.answer_revealed_before_submission or any(
        item.answer_revealed_before_submission for item in prior_item_attempts
    )
    if answer_was_revealed:
        return (
            ReviewRetrievalOutcome.HINT_DEPENDENT,
            0.0,
            ReviewWeakReason.ANSWER_REVEALED,
        )
    if attempt.hint_used or attempt.hint_dependency is not HintDependency.NONE:
        return (
            ReviewRetrievalOutcome.HINT_DEPENDENT,
            0.0,
            ReviewWeakReason.HINT_DEPENDENCY,
        )
    if result.outcome is EvaluationOutcome.CORRECT:
        difficult = (
            retry_number > 0
            or result.score is None
            or result.score < policy.strong_recall_min_score
            or attempt.response_latency_ms > policy.strong_recall_max_latency_ms
        )
        if difficult:
            return (
                ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY,
                policy.recalled_with_difficulty_score,
                ReviewWeakReason.DIFFICULT_RECALL,
            )
        return ReviewRetrievalOutcome.RECALLED, 1.0, None
    if result.outcome is EvaluationOutcome.PARTIAL:
        return (
            ReviewRetrievalOutcome.PARTIAL,
            result.score or 0.0,
            ReviewWeakReason.PARTIAL_RECALL,
        )
    return (
        ReviewRetrievalOutcome.FAILED,
        0.0,
        ReviewWeakReason.RETRIEVAL_FAILURE,
    )


def evaluate_completion(
    *,
    items: Sequence[ReviewItem],
    attempts: Sequence[ReviewAttempt],
    receipts: Sequence[ReviewAttemptReceipt],
    policy: ReviewCompletionPolicy,
) -> tuple[ReviewCompletionEvaluation, tuple[ReviewItemResult, ...]]:
    attempt_by_id = {attempt.attempt_id: attempt for attempt in attempts}
    receipts_by_item: dict[str, list[ReviewAttemptReceipt]] = defaultdict(list)
    for receipt in receipts:
        if receipt.accepted and not receipt.duplicate:
            receipts_by_item[receipt.item_id].append(receipt)

    rank = {
        ReviewRetrievalOutcome.RECALLED: 5,
        ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY: 4,
        ReviewRetrievalOutcome.PARTIAL: 3,
        ReviewRetrievalOutcome.HINT_DEPENDENT: 2,
        ReviewRetrievalOutcome.FAILED: 1,
    }
    item_results: list[ReviewItemResult] = []
    for item in items:
        item_receipts = receipts_by_item[item.item_id]
        if not item_receipts:
            raise ValueError("completion requires an accepted attempt for every item")
        contaminated = any(
            attempt_by_id[receipt.attempt_id].answer_revealed_before_submission
            for receipt in item_receipts
        )
        if contaminated:
            best_outcome = ReviewRetrievalOutcome.HINT_DEPENDENT
            best_score = 0.0
        else:
            best = max(
                item_receipts,
                key=lambda receipt: (
                    receipt.retrieval_score or 0.0,
                    rank.get(receipt.retrieval_outcome, 0),
                ),
            )
            if best.retrieval_outcome is None or best.retrieval_score is None:
                raise ValueError("accepted review receipt is incomplete")
            best_outcome = best.retrieval_outcome
            best_score = best.retrieval_score
        passed = best_outcome in {
            ReviewRetrievalOutcome.RECALLED,
            ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY,
        } and not contaminated
        item_results.append(
            ReviewItemResult(
                item_id=item.item_id,
                concept_id=item.concept_id,
                best_outcome=best_outcome,
                retrieval_score=best_score,
                passed=passed,
                required=item.required,
                critical=item.critical,
                attempt_count=len(item_receipts),
                contaminated_by_answer_reveal=contaminated,
            )
        )

    retrieval_score = sum(item.retrieval_score for item in item_results) / len(
        item_results
    )
    required = tuple(item for item in item_results if item.required)
    required_ids = tuple(item.item_id for item in required)
    passed_required_ids = tuple(item.item_id for item in required if item.passed)

    critical_by_concept: dict[str, list[ReviewItemResult]] = defaultdict(list)
    for item in item_results:
        if item.critical:
            critical_by_concept[item.concept_id].append(item)
    critical_ids = tuple(critical_by_concept)
    passed_critical_ids = tuple(
        concept_id
        for concept_id, concept_items in critical_by_concept.items()
        if all(item.passed for item in concept_items)
    )

    failed_gates: list[str] = []
    if policy.require_all_required_items_pass and (
        set(required_ids) != set(passed_required_ids)
    ):
        failed_gates.append("required_items")
    if retrieval_score < policy.minimum_retrieval_score:
        failed_gates.append("minimum_retrieval_score")
    if policy.require_critical_concepts_pass and (
        set(critical_ids) != set(passed_critical_ids)
    ):
        failed_gates.append("critical_concepts")
    passed = not failed_gates
    completion = ReviewCompletionEvaluation(
        policy_id=policy.policy_id,
        passed=passed,
        retrieval_score=retrieval_score,
        required_item_ids=required_ids,
        passed_required_item_ids=passed_required_ids,
        critical_concept_ids=critical_ids,
        passed_critical_concept_ids=passed_critical_ids,
        failed_gates=tuple(failed_gates),
        reason=(
            "review satisfies every retrieval completion gate"
            if passed
            else "review did not satisfy: " + ",".join(failed_gates)
        ),
    )
    return completion, tuple(item_results)


__all__ = ["classify_retrieval", "evaluate_completion"]
