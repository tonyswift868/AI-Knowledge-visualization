"""Deterministic review-effort estimation from item work, not plan ratios."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from math import ceil, floor
from typing import Mapping

from .contracts import (
    EffortRange,
    ReviewEffortBreakdown,
    ReviewEffortEstimate,
    ReviewEffortInput,
    ReviewItem,
    ReviewItemPriority,
    ReviewQuestionType,
    ReviewRoundEffort,
)


REVIEW_ESTIMATOR_VERSION = "mentorflow.review-effort.rules.v1"
REVIEW_ESTIMATOR_PROVIDER_CALLS = 0


ANSWER_PRIOR_SECONDS: Mapping[ReviewQuestionType, float] = {
    ReviewQuestionType.SHORT_RECALL: 24.0,
    ReviewQuestionType.EXPLANATION: 105.0,
    ReviewQuestionType.CODE_PROBLEM: 300.0,
    ReviewQuestionType.ORAL_STYLE_QA: 150.0,
}
EVALUATION_PRIOR_SECONDS: Mapping[ReviewQuestionType, float] = {
    ReviewQuestionType.SHORT_RECALL: 8.0,
    ReviewQuestionType.EXPLANATION: 20.0,
    ReviewQuestionType.CODE_PROBLEM: 45.0,
    ReviewQuestionType.ORAL_STYLE_QA: 25.0,
}
FEEDBACK_PRIOR_SECONDS: Mapping[ReviewQuestionType, float] = {
    ReviewQuestionType.SHORT_RECALL: 10.0,
    ReviewQuestionType.EXPLANATION: 32.0,
    ReviewQuestionType.CODE_PROBLEM: 60.0,
    ReviewQuestionType.ORAL_STYLE_QA: 38.0,
}
PRIORITY_FACTORS: Mapping[ReviewItemPriority, float] = {
    ReviewItemPriority.LOW: 0.72,
    ReviewItemPriority.NORMAL: 1.0,
    ReviewItemPriority.HIGH: 1.22,
    ReviewItemPriority.CRITICAL: 1.42,
}


def _minutes(seconds: float) -> int:
    return max(0, int(ceil(seconds / 60.0)))


def _estimate_id(value: ReviewEffortInput) -> str:
    request_payload = value.model_dump(mode="json")
    if request_payload["historical_calibration"] is not None:
        request_payload["historical_calibration"]["by_question_type"] = sorted(
            request_payload["historical_calibration"]["by_question_type"],
            key=lambda item: item["question_type"],
        )
    payload = {
        "estimator_version": REVIEW_ESTIMATOR_VERSION,
        "request": request_payload,
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"review-effort-{sha256(encoded).hexdigest()[:24]}"


def _item_seconds(
    item: ReviewItem,
    *,
    round_factor: float,
    value: ReviewEffortInput,
) -> tuple[float, float, float, float, float]:
    calibration = (
        value.historical_calibration.for_type(item.question_type)
        if value.historical_calibration is not None
        else None
    )
    answer_factor = calibration.answer_speed_factor if calibration else 1.0
    evaluation_factor = (
        calibration.evaluation_speed_factor if calibration else 1.0
    )
    answer = (
        item.expected_answer_seconds
        if item.expected_answer_seconds is not None
        else ANSWER_PRIOR_SECONDS[item.question_type]
    )
    evaluation = (
        item.expected_evaluation_seconds
        if item.expected_evaluation_seconds is not None
        else EVALUATION_PRIOR_SECONDS[item.question_type]
    )
    feedback = (
        item.expected_feedback_seconds
        if item.expected_feedback_seconds is not None
        else FEEDBACK_PRIOR_SECONDS[item.question_type]
    )
    priority_factor = PRIORITY_FACTORS[item.priority]
    answer_seconds = answer * answer_factor * priority_factor * round_factor
    evaluation_seconds = (
        evaluation * evaluation_factor * priority_factor * round_factor
    )

    observed_difficulty = 0.0
    retry_rate = 0.0
    if calibration is not None:
        observed_difficulty = min(
            1.5,
            calibration.wrong_rate
            + 0.55 * calibration.partial_rate
            + 0.30 * calibration.hint_rate,
        )
        retry_rate = calibration.weak_item_retry_rate
    feedback_seconds = feedback * priority_factor * round_factor * (
        1.0 + 0.35 * observed_difficulty
    )
    weak_risk = max(
        retry_rate,
        0.34 if item.priority in {ReviewItemPriority.HIGH, ReviewItemPriority.CRITICAL} else 0.14,
    )
    weak_retry_seconds = (
        (answer_seconds + evaluation_seconds + feedback_seconds)
        * value.round_config.weak_retry_allowance_rate
        * weak_risk
    )
    critical_load_seconds = 0.0
    if item.critical_concept or item.priority is ReviewItemPriority.CRITICAL:
        critical_load_seconds = (
            answer_seconds + evaluation_seconds + feedback_seconds
        ) * value.round_config.critical_concept_extra_rate
    return (
        answer_seconds,
        evaluation_seconds,
        feedback_seconds,
        weak_retry_seconds,
        critical_load_seconds,
    )


def estimate_review_effort(value: ReviewEffortInput) -> ReviewEffortEstimate:
    """Build exactly the user-selected rounds for a new plan or regeneration."""

    rounds: list[ReviewRoundEffort] = []
    type_counts = defaultdict(int)
    for item in value.items:
        type_counts[item.question_type] += 1
    relevant_concept_ids = tuple(
        sorted({item.concept_id for item in value.items})
    )

    for round_index in range(1, value.round_config.selected_rounds + 1):
        round_factor = value.round_config.load_factor_for(round_index)
        component_seconds = [0.0] * 5
        for item in value.items:
            for index, seconds in enumerate(
                _item_seconds(
                    item,
                    round_factor=round_factor,
                    value=value,
                )
            ):
                component_seconds[index] += seconds
        component_minutes = [_minutes(item) for item in component_seconds]
        expected = sum(component_minutes)
        historical = value.historical_calibration is not None
        uncertainty = 0.18 if historical else 0.30
        low = max(1, int(floor(expected * (1.0 - uncertainty))))
        high = max(expected, int(ceil(expected * (1.0 + uncertainty))))
        basis = (
            "model=item_timing_components",
            "initial_learning_ratio=not_used",
            f"review_item_count={len(value.items)}",
            "question_type_counts="
            + ",".join(
                f"{item.value}:{type_counts[item]}"
                for item in sorted(type_counts, key=lambda current: current.value)
            ),
            f"round_load_factor={round_factor:.4f}",
            f"weak_retry_allowance_rate={value.round_config.weak_retry_allowance_rate:.4f}",
            f"critical_concept_extra_rate={value.round_config.critical_concept_extra_rate:.4f}",
            "historical_speed="
            + (value.historical_calibration.profile_id if historical else "conservative_priors"),
            "provider_calls=0",
        )
        breakdown = ReviewEffortBreakdown(
            answer_minutes=component_minutes[0],
            evaluation_minutes=component_minutes[1],
            feedback_minutes=component_minutes[2],
            weak_retry_allowance_minutes=component_minutes[3],
            critical_concept_load_minutes=component_minutes[4],
            expected_minutes=expected,
        )
        rounds.append(
            ReviewRoundEffort(
                round_id=f"REVIEW_ROUND_{round_index}",
                round_index=round_index,
                review_item_count=len(value.items),
                relevant_concept_ids=relevant_concept_ids,
                low_minutes=low,
                expected_minutes=expected,
                high_minutes=high,
                breakdown=breakdown,
                confidence=0.82 if historical else 0.64,
                basis=basis,
            )
        )

    review_total = EffortRange(
        low_minutes=sum(item.low_minutes for item in rounds),
        expected_minutes=sum(item.expected_minutes for item in rounds),
        high_minutes=sum(item.high_minutes for item in rounds),
    )
    return ReviewEffortEstimate(
        estimate_id=_estimate_id(value),
        estimator_version=REVIEW_ESTIMATOR_VERSION,
        use_case=value.use_case,
        selected_review_rounds=value.round_config.selected_rounds,
        initial_learning_effort=value.initial_learning_effort,
        review_rounds=tuple(rounds),
        review_total=review_total,
        grand_total=value.initial_learning_effort + review_total,
        historical_calibration_profile_id=(
            value.historical_calibration.profile_id
            if value.historical_calibration is not None
            else None
        ),
        basis=(
            "total=initial_learning+all_user_selected_review_rounds",
            "macro_round_count=user_selected_and_fixed",
            "published_plan_mutation=unsupported",
            "supported_use_cases=new_plan,user_requested_regeneration",
        ),
    )


__all__ = [
    "ANSWER_PRIOR_SECONDS",
    "EVALUATION_PRIOR_SECONDS",
    "FEEDBACK_PRIOR_SECONDS",
    "PRIORITY_FACTORS",
    "REVIEW_ESTIMATOR_PROVIDER_CALLS",
    "REVIEW_ESTIMATOR_VERSION",
    "estimate_review_effort",
]
