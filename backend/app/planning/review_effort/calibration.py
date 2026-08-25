"""Pure historical calibration for future or explicitly regenerated plans."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json

from .contracts import (
    QuestionTypeCalibration,
    ReviewCalibrationObservation,
    ReviewCalibrationProfile,
    ReviewQuestionType,
)


CALIBRATION_VERSION = "mentorflow.review-calibration.rules.v1"

# Conservative answer/evaluation priors are repeated here deliberately: the
# calibration ratio must use the same explainable baseline as estimation.
_ANSWER_PRIOR_SECONDS = {
    ReviewQuestionType.SHORT_RECALL: 24.0,
    ReviewQuestionType.EXPLANATION: 105.0,
    ReviewQuestionType.CODE_PROBLEM: 300.0,
    ReviewQuestionType.ORAL_STYLE_QA: 150.0,
}
_EVALUATION_PRIOR_SECONDS = {
    ReviewQuestionType.SHORT_RECALL: 8.0,
    ReviewQuestionType.EXPLANATION: 20.0,
    ReviewQuestionType.CODE_PROBLEM: 45.0,
    ReviewQuestionType.ORAL_STYLE_QA: 25.0,
}


def _bounded_ratio(actual: float, prior: float) -> float:
    return round(max(0.50, min(2.50, actual / prior)), 4)


def calibrate_review_history(
    observations: tuple[ReviewCalibrationObservation, ...],
) -> ReviewCalibrationProfile:
    """Aggregate immutable observations without touching a published plan."""

    if not observations:
        raise ValueError("at least one review calibration observation is required")
    grouped: dict[ReviewQuestionType, list[ReviewCalibrationObservation]] = (
        defaultdict(list)
    )
    for observation in observations:
        grouped[observation.question_type].append(observation)

    values: list[QuestionTypeCalibration] = []
    for question_type in sorted(grouped, key=lambda item: item.value):
        items = grouped[question_type]
        sample_count = sum(item.sample_count for item in items)

        def weighted(attribute: str) -> float:
            return sum(
                float(getattr(item, attribute)) * item.sample_count
                for item in items
            ) / sample_count

        values.append(
            QuestionTypeCalibration(
                question_type=question_type,
                sample_count=sample_count,
                answer_speed_factor=_bounded_ratio(
                    weighted("actual_answer_time_seconds"),
                    _ANSWER_PRIOR_SECONDS[question_type],
                ),
                evaluation_speed_factor=_bounded_ratio(
                    weighted("evaluation_time_seconds"),
                    _EVALUATION_PRIOR_SECONDS[question_type],
                ),
                wrong_rate=round(weighted("wrong_rate"), 4),
                partial_rate=round(weighted("partial_rate"), 4),
                hint_rate=round(weighted("hint_rate"), 4),
                weak_item_retry_rate=round(
                    weighted("weak_item_retry_rate"),
                    4,
                ),
            )
        )

    payload = {
        "calibration_version": CALIBRATION_VERSION,
        "values": [item.model_dump(mode="json") for item in values],
    }
    digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return ReviewCalibrationProfile(
        profile_id=f"review-calibration-{digest}",
        by_question_type=tuple(values),
    )


__all__ = ["CALIBRATION_VERSION", "calibrate_review_history"]
