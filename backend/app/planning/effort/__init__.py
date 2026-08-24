"""Adaptive learning-effort estimator foundation."""

from .contracts import (
    FEATURE_SCHEMA_VERSION,
    OBSERVATION_SCHEMA_VERSION,
    Difficulty,
    EffortEstimate,
    EffortFeatures,
    EffortObservation,
    KnowledgeType,
    LearnerLevel,
    TeachingDepth,
)
from .estimator import (
    ESTIMATOR_PROVIDER_CALLS,
    ESTIMATOR_VERSION,
    MAX_EXPECTED_MINUTES,
    MAX_HIGH_MINUTES,
    RuleBasedEffortEstimator,
    estimate_effort,
)

__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "OBSERVATION_SCHEMA_VERSION",
    "Difficulty",
    "ESTIMATOR_PROVIDER_CALLS",
    "ESTIMATOR_VERSION",
    "EffortEstimate",
    "EffortFeatures",
    "EffortObservation",
    "KnowledgeType",
    "LearnerLevel",
    "MAX_EXPECTED_MINUTES",
    "MAX_HIGH_MINUTES",
    "RuleBasedEffortEstimator",
    "TeachingDepth",
    "estimate_effort",
]
