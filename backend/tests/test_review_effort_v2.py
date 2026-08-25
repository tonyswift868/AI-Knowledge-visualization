from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.planning.review_effort import (
    ANSWER_PRIOR_SECONDS,
    EVALUATION_PRIOR_SECONDS,
    FEEDBACK_PRIOR_SECONDS,
    FIXED_REVIEW_ROUNDS,
    FSRS_CAN_CHANGE_MACRO_ROUND_COUNT,
    EffortRange,
    ReviewCalibrationObservation,
    ReviewEffortInput,
    ReviewEstimateUseCase,
    ReviewItem,
    ReviewItemPriority,
    ReviewQuestionType,
    ReviewRoundConfig,
    calibrate_review_history,
    estimate_review_effort,
)


INITIAL = EffortRange(low_minutes=80, expected_minutes=100, high_minutes=130)


def item(
    item_id: str,
    question_type: ReviewQuestionType,
    *,
    priority: ReviewItemPriority = ReviewItemPriority.NORMAL,
    critical: bool = False,
) -> ReviewItem:
    return ReviewItem(
        item_id=item_id,
        concept_id=f"concept-{item_id}",
        question_type=question_type,
        priority=priority,
        critical_concept=critical,
    )


def effort_input(
    rounds: int,
    items: tuple[ReviewItem, ...],
    *,
    initial: EffortRange = INITIAL,
    historical_calibration: object | None = None,
) -> ReviewEffortInput:
    return ReviewEffortInput(
        request_id=f"estimate-{rounds}-{len(items)}",
        use_case=ReviewEstimateUseCase.NEW_PLAN,
        initial_learning_effort=initial,
        round_config=ReviewRoundConfig(selected_rounds=rounds),
        items=items,
        historical_calibration=historical_calibration,
    )


def test_zero_rounds_has_zero_review_and_grand_total_equals_initial() -> None:
    estimate = estimate_review_effort(effort_input(0, ()))

    assert estimate.selected_review_rounds == 0
    assert estimate.review_rounds == ()
    assert estimate.review_total == EffortRange(
        low_minutes=0,
        expected_minutes=0,
        high_minutes=0,
    )
    assert estimate.grand_total == INITIAL
    assert FIXED_REVIEW_ROUNDS is True
    assert FSRS_CAN_CHANGE_MACRO_ROUND_COUNT is False


def test_three_rounds_are_exactly_the_user_selected_macro_rounds() -> None:
    items = (
        item("recall", ReviewQuestionType.SHORT_RECALL),
        item("explain", ReviewQuestionType.EXPLANATION),
    )

    estimate = estimate_review_effort(effort_input(3, items))

    assert [value.round_id for value in estimate.review_rounds] == [
        "REVIEW_ROUND_1",
        "REVIEW_ROUND_2",
        "REVIEW_ROUND_3",
    ]
    assert estimate.review_total.expected_minutes == sum(
        value.expected_minutes for value in estimate.review_rounds
    )
    assert estimate.grand_total == INITIAL + estimate.review_total


@pytest.mark.parametrize(
    "question_type",
    tuple(ReviewQuestionType),
)
def test_new_learner_priors_are_question_type_specific(
    question_type: ReviewQuestionType,
) -> None:
    assert ANSWER_PRIOR_SECONDS[question_type] > 0
    assert EVALUATION_PRIOR_SECONDS[question_type] > 0
    assert FEEDBACK_PRIOR_SECONDS[question_type] > 0

    estimates = {
        value: estimate_review_effort(effort_input(1, (item(value.value, value),)))
        .review_rounds[0]
        .expected_minutes
        for value in ReviewQuestionType
    }
    assert len(set(ANSWER_PRIOR_SECONDS.values())) == len(ReviewQuestionType)
    assert estimates[ReviewQuestionType.SHORT_RECALL] < estimates[
        ReviewQuestionType.EXPLANATION
    ]
    assert estimates[ReviewQuestionType.EXPLANATION] < estimates[
        ReviewQuestionType.CODE_PROBLEM
    ]


def test_review_effort_is_not_a_percentage_of_initial_learning() -> None:
    items = (item("code", ReviewQuestionType.CODE_PROBLEM),)
    small = estimate_review_effort(
        effort_input(
            1,
            items,
            initial=EffortRange(low_minutes=10, expected_minutes=20, high_minutes=30),
        )
    )
    large = estimate_review_effort(
        effort_input(
            1,
            items,
            initial=EffortRange(
                low_minutes=1_000,
                expected_minutes=2_000,
                high_minutes=3_000,
            ),
        )
    )

    assert small.review_total == large.review_total
    assert "initial_learning_ratio=not_used" in small.review_rounds[0].basis


def test_item_count_priority_critical_load_and_all_components_affect_effort() -> None:
    normal = item("normal", ReviewQuestionType.EXPLANATION)
    critical = item(
        "critical",
        ReviewQuestionType.EXPLANATION,
        priority=ReviewItemPriority.CRITICAL,
        critical=True,
    )

    one = estimate_review_effort(effort_input(1, (normal,))).review_rounds[0]
    critical_only = estimate_review_effort(
        effort_input(1, (critical,))
    ).review_rounds[0]
    two = estimate_review_effort(effort_input(1, (normal, critical))).review_rounds[0]

    assert critical_only.expected_minutes > one.expected_minutes
    assert two.expected_minutes > one.expected_minutes
    assert two.breakdown.answer_minutes > 0
    assert two.breakdown.evaluation_minutes > 0
    assert two.breakdown.feedback_minutes > 0
    assert two.breakdown.weak_retry_allowance_minutes > 0
    assert two.breakdown.critical_concept_load_minutes > 0
    assert two.low_minutes <= two.expected_minutes <= two.high_minutes


def test_explicit_answer_evaluation_and_feedback_times_override_priors() -> None:
    normal = item("normal", ReviewQuestionType.SHORT_RECALL)
    slower = normal.model_copy(
        update={
            "item_id": "slow",
            "expected_answer_seconds": 600,
            "expected_evaluation_seconds": 180,
            "expected_feedback_seconds": 180,
        }
    )

    normal_effort = estimate_review_effort(
        effort_input(1, (normal,))
    ).review_rounds[0]
    slower_effort = estimate_review_effort(
        effort_input(1, (slower,))
    ).review_rounds[0]

    assert slower_effort.expected_minutes > normal_effort.expected_minutes


def test_historical_calibration_updates_only_a_future_estimate() -> None:
    source = effort_input(
        2,
        (item("explain", ReviewQuestionType.EXPLANATION),),
    )
    prior_estimate = estimate_review_effort(source)
    profile = calibrate_review_history(
        (
            ReviewCalibrationObservation(
                question_type=ReviewQuestionType.EXPLANATION,
                sample_count=20,
                actual_answer_time_seconds=210,
                evaluation_time_seconds=40,
                wrong_rate=0.45,
                partial_rate=0.25,
                hint_rate=0.30,
                weak_item_retry_rate=0.55,
            ),
        )
    )
    calibrated_input = source.model_copy(
        update={
            "request_id": "calibrated-future-plan",
            "use_case": ReviewEstimateUseCase.USER_REQUESTED_REGENERATION,
            "historical_calibration": profile,
        }
    )

    calibrated = estimate_review_effort(calibrated_input)

    assert calibrated.review_total.expected_minutes > (
        prior_estimate.review_total.expected_minutes
    )
    assert calibrated.historical_calibration_profile_id == profile.profile_id
    assert prior_estimate.historical_calibration_profile_id is None
    assert prior_estimate == estimate_review_effort(source)


def test_calibration_aggregates_all_required_observation_fields() -> None:
    profile = calibrate_review_history(
        (
            ReviewCalibrationObservation(
                question_type=ReviewQuestionType.ORAL_STYLE_QA,
                sample_count=3,
                actual_answer_time_seconds=180,
                evaluation_time_seconds=30,
                wrong_rate=0.2,
                partial_rate=0.3,
                hint_rate=0.4,
                weak_item_retry_rate=0.5,
            ),
            ReviewCalibrationObservation(
                question_type=ReviewQuestionType.ORAL_STYLE_QA,
                sample_count=1,
                actual_answer_time_seconds=120,
                evaluation_time_seconds=20,
                wrong_rate=0.0,
                partial_rate=0.1,
                hint_rate=0.0,
                weak_item_retry_rate=0.1,
            ),
        )
    )
    value = profile.by_question_type[0]

    assert value.sample_count == 4
    assert value.answer_speed_factor > 1
    assert value.evaluation_speed_factor > 1
    assert value.wrong_rate == pytest.approx(0.15)
    assert value.partial_rate == pytest.approx(0.25)
    assert value.hint_rate == pytest.approx(0.30)
    assert value.weak_item_retry_rate == pytest.approx(0.40)


def test_contracts_reject_missing_items_and_unknown_published_update_mode() -> None:
    with pytest.raises(ValidationError, match="require review items"):
        effort_input(1, ())
    with pytest.raises(ValidationError):
        ReviewEffortInput(
            request_id="published-update-is-not-supported",
            use_case="published_plan_update",
            initial_learning_effort=INITIAL,
            round_config=ReviewRoundConfig(selected_rounds=0),
        )
