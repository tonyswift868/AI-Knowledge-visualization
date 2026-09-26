"""Deterministic rules for an initial, calibratable effort estimate."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import ceil, floor, log1p
from typing import Mapping

from .contracts import (
    Difficulty,
    EffortEstimate,
    EffortFeatures,
    KnowledgeType,
    LearnerLevel,
    TeachingDepth,
)


ESTIMATOR_VERSION = "mentorflow.effort-rules.v1"
ESTIMATOR_PROVIDER_CALLS = 0

MAX_CONTENT_AMOUNT = 20.0
MAX_PREREQUISITE_DEPTH = 12
MAX_NORMALIZED_FEATURE = 1.0
MAX_EXPECTED_MINUTES = 720
MAX_HIGH_MINUTES = 960

DIFFICULTY_FACTORS: Mapping[Difficulty, float] = {
    Difficulty.EASY: 0.82,
    Difficulty.MEDIUM: 1.0,
    Difficulty.HARD: 1.32,
}
LEARNER_LEVEL_FACTORS: Mapping[LearnerLevel, float] = {
    LearnerLevel.BEGINNER: 1.18,
    LearnerLevel.INTERMEDIATE: 1.0,
    LearnerLevel.ADVANCED: 0.88,
}
TEACHING_DEPTH_FACTORS: Mapping[TeachingDepth, float] = {
    TeachingDepth.BRIEF: 0.82,
    TeachingDepth.STANDARD: 1.0,
    TeachingDepth.DEEP: 1.25,
}
KNOWLEDGE_TYPE_WEIGHTS: Mapping[KnowledgeType, Mapping[str, float]] = {
    KnowledgeType.FACTUAL: {
        "understand": 0.82,
        "example": 0.70,
        "practice": 0.68,
        "assessment": 0.82,
        "review": 1.00,
    },
    KnowledgeType.CONCEPT: {
        "understand": 1.00,
        "example": 1.00,
        "practice": 0.92,
        "assessment": 1.00,
        "review": 1.00,
    },
    KnowledgeType.PROCEDURE: {
        "understand": 0.95,
        "example": 1.15,
        "practice": 1.22,
        "assessment": 1.08,
        "review": 1.02,
    },
    KnowledgeType.PROBLEM_SOLVING: {
        "understand": 1.10,
        "example": 1.22,
        "practice": 1.38,
        "assessment": 1.22,
        "review": 1.10,
    },
}

_COMPONENT_NAMES = (
    "understand_minutes",
    "example_minutes",
    "practice_minutes",
    "assessment_minutes",
    "review_minutes",
)


def _cap(value: float, maximum: float) -> float:
    return max(0.0, min(maximum, float(value)))


def _bounded_components(raw: Mapping[str, float]) -> dict[str, int]:
    rounded = {name: max(0, int(ceil(raw[name]))) for name in _COMPONENT_NAMES}
    if sum(rounded.values()) <= MAX_EXPECTED_MINUTES:
        return rounded

    raw_total = sum(max(0.0, raw[name]) for name in _COMPONENT_NAMES)
    scaled = {
        name: max(0.0, raw[name]) * MAX_EXPECTED_MINUTES / raw_total
        for name in _COMPONENT_NAMES
    }
    result = {name: int(floor(scaled[name])) for name in _COMPONENT_NAMES}
    remainder = MAX_EXPECTED_MINUTES - sum(result.values())
    priority = sorted(
        _COMPONENT_NAMES,
        key=lambda name: (scaled[name] - result[name], -_COMPONENT_NAMES.index(name)),
        reverse=True,
    )
    for name in priority[:remainder]:
        result[name] += 1
    return result


def _estimate_id(features: EffortFeatures, estimator_version: str) -> str:
    payload = {
        "estimator_version": estimator_version,
        "features": features.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"effort_{sha256(encoded).hexdigest()[:24]}"


@dataclass(frozen=True)
class RuleBasedEffortEstimator:
    """Replaceable estimator interface backed only by explicit local rules."""

    estimator_version: str = ESTIMATOR_VERSION

    def __post_init__(self) -> None:
        if not str(self.estimator_version or "").strip():
            raise ValueError("estimator_version is required")

    def estimate(self, features: EffortFeatures) -> EffortEstimate:
        amount = _cap(features.content_amount, MAX_CONTENT_AMOUNT)
        prerequisite_depth = min(
            MAX_PREREQUISITE_DEPTH,
            max(0, int(features.prerequisite_depth)),
        )
        code_density = _cap(features.code_density, MAX_NORMALIZED_FEATURE)
        formula_complexity = _cap(
            features.formula_table_complexity,
            MAX_NORMALIZED_FEATURE,
        )
        practice_requirement = _cap(
            features.practice_requirement,
            MAX_NORMALIZED_FEATURE,
        )
        difficulty_factor = DIFFICULTY_FACTORS[features.difficulty]
        learner_factor = LEARNER_LEVEL_FACTORS[features.learner_level]
        teaching_factor = TEACHING_DEPTH_FACTORS[features.teaching_depth]
        type_weights = KNOWLEDGE_TYPE_WEIGHTS[features.knowledge_type]
        mastery = features.mastery or 0.0

        # Amount grows logarithmically so repetition or a long source cannot
        # inflate a concept estimate linearly.
        content_factor = 0.75 + 0.48 * log1p(amount)
        prerequisite_factor = 1.0 + 0.06 * prerequisite_depth
        density_factor = 1.0 + 0.25 * code_density + 0.20 * formula_complexity
        practice_factor = 0.75 + 0.90 * practice_requirement
        mastery_factor = 1.0 - 0.40 * mastery
        common_factor = difficulty_factor * learner_factor * mastery_factor

        understand = (
            8.0
            * content_factor
            * type_weights["understand"]
            * common_factor
            * prerequisite_factor
            * density_factor
            * teaching_factor
        )
        example = (
            4.0
            * (0.85 + 0.15 * content_factor)
            * type_weights["example"]
            * common_factor
            * prerequisite_factor
            * density_factor
            * teaching_factor
        )
        practice = (
            6.0
            * (0.80 + 0.20 * content_factor)
            * type_weights["practice"]
            * common_factor
            * density_factor
            * practice_factor
            * teaching_factor
        )
        assessment = (
            4.0
            * type_weights["assessment"]
            * common_factor
            * (0.90 + 0.35 * practice_requirement)
        )
        initial_total = understand + example + practice + assessment
        review = max(
            2.0,
            initial_total
            * 0.16
            * type_weights["review"]
            * (1.0 - 0.15 * mastery),
        )
        components = _bounded_components(
            {
                "understand_minutes": understand,
                "example_minutes": example,
                "practice_minutes": practice,
                "assessment_minutes": assessment,
                "review_minutes": review,
            }
        )
        expected = sum(components.values())

        clipped_inputs = sum(
            (
                features.content_amount > MAX_CONTENT_AMOUNT,
                features.prerequisite_depth > MAX_PREREQUISITE_DEPTH,
                features.code_density > MAX_NORMALIZED_FEATURE,
                features.formula_table_complexity > MAX_NORMALIZED_FEATURE,
                features.practice_requirement > MAX_NORMALIZED_FEATURE,
            )
        )
        uncertainty = min(
            0.38,
            0.18
            + (0.06 if features.mastery is None else 0.0)
            + min(0.10, clipped_inputs * 0.02),
        )
        confidence = round(1.0 - uncertainty, 2)
        low = max(0, int(floor(expected * (1.0 - uncertainty) / 5.0) * 5))
        high = min(
            MAX_HIGH_MINUTES,
            int(ceil(expected * (1.0 + uncertainty) / 5.0) * 5),
        )
        if low >= expected and expected > 0:
            low = max(0, expected - 5)
        if high <= expected:
            high = min(MAX_HIGH_MINUTES, expected + 5)

        weights = ",".join(
            f"{name}:{type_weights[name]:.2f}"
            for name in ("understand", "example", "practice", "assessment", "review")
        )
        basis = (
            "model=deterministic_rule_weights",
            "provider_calls=0",
            f"content_curve=log1p:capped@{MAX_CONTENT_AMOUNT:g};effective={amount:g}",
            f"knowledge_type={features.knowledge_type.value};weights={weights}",
            f"difficulty={features.difficulty.value};factor={difficulty_factor:.2f}",
            f"prerequisite_depth=capped@{MAX_PREREQUISITE_DEPTH};effective={prerequisite_depth}",
            f"code_density=capped@1;effective={code_density:.3f}",
            f"formula_table_complexity=capped@1;effective={formula_complexity:.3f}",
            f"practice_requirement=capped@1;effective={practice_requirement:.3f}",
            f"teaching_depth={features.teaching_depth.value};factor={teaching_factor:.2f}",
            f"learner_level={features.learner_level.value};factor={learner_factor:.2f}",
            f"mastery={features.mastery if features.mastery is not None else 'unknown'};factor={mastery_factor:.3f}",
            "source_duration_minutes=excluded:not_a_learning_time_proxy",
            "calibration=untrained:no_accuracy_claim",
        )
        return EffortEstimate(
            estimate_id=_estimate_id(features, self.estimator_version),
            estimator_version=self.estimator_version,
            **components,
            low_minutes=low,
            expected_minutes=expected,
            high_minutes=high,
            confidence=confidence,
            basis=basis,
            features=features,
        )


def estimate_effort(
    features: EffortFeatures,
    *,
    estimator_version: str = ESTIMATOR_VERSION,
) -> EffortEstimate:
    return RuleBasedEffortEstimator(estimator_version=estimator_version).estimate(features)


__all__ = [
    "DIFFICULTY_FACTORS",
    "ESTIMATOR_PROVIDER_CALLS",
    "ESTIMATOR_VERSION",
    "KNOWLEDGE_TYPE_WEIGHTS",
    "LEARNER_LEVEL_FACTORS",
    "MAX_EXPECTED_MINUTES",
    "MAX_HIGH_MINUTES",
    "RuleBasedEffortEstimator",
    "TEACHING_DEPTH_FACTORS",
    "estimate_effort",
]
