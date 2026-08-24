from __future__ import annotations

import ast
import inspect
import sqlite3

import pytest

from app.planning.effort import (
    ESTIMATOR_PROVIDER_CALLS,
    MAX_EXPECTED_MINUTES,
    MAX_HIGH_MINUTES,
    Difficulty,
    EffortFeatures,
    EffortObservation,
    KnowledgeType,
    LearnerLevel,
    RuleBasedEffortEstimator,
    TeachingDepth,
    estimate_effort,
)
from app.planning.effort import estimator as estimator_module


def _features(**overrides: object) -> EffortFeatures:
    values: dict[str, object] = {
        "content_amount": 4.0,
        "knowledge_type": KnowledgeType.CONCEPT,
        "difficulty": Difficulty.MEDIUM,
        "prerequisite_depth": 1,
        "code_density": 0.2,
        "formula_table_complexity": 0.2,
        "practice_requirement": 0.5,
        "teaching_depth": TeachingDepth.STANDARD,
        "learner_level": LearnerLevel.INTERMEDIATE,
    }
    values.update(overrides)
    return EffortFeatures(**values)


def _component_values(estimate: object) -> tuple[int, ...]:
    return tuple(
        getattr(estimate, name)
        for name in (
            "understand_minutes",
            "example_minutes",
            "practice_minutes",
            "assessment_minutes",
            "review_minutes",
        )
    )


def _import_names(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_harder_concept_is_not_shorter_than_equivalent_easy_concept() -> None:
    easy = estimate_effort(_features(difficulty=Difficulty.EASY))
    hard = estimate_effort(_features(difficulty=Difficulty.HARD))

    assert hard.expected_minutes >= easy.expected_minutes


def test_prerequisite_depth_increase_never_decreases_effort() -> None:
    estimates = [
        estimate_effort(_features(prerequisite_depth=depth)).expected_minutes
        for depth in (0, 2, 6, 12, 50)
    ]

    assert estimates == sorted(estimates)


def test_practice_requirement_increase_never_decreases_effort() -> None:
    estimates = [
        estimate_effort(_features(practice_requirement=value)).expected_minutes
        for value in (0.0, 0.25, 0.5, 0.75, 1.0, 5.0)
    ]

    assert estimates == sorted(estimates)


def test_higher_mastery_can_reduce_expected_effort() -> None:
    low_mastery = estimate_effort(_features(mastery=0.1))
    high_mastery = estimate_effort(_features(mastery=0.9))

    assert high_mastery.expected_minutes < low_mastery.expected_minutes


def test_review_minutes_are_separate_and_included_in_expected_total() -> None:
    estimate = estimate_effort(_features())

    assert estimate.review_minutes > 0
    assert estimate.expected_minutes == sum(_component_values(estimate))


def test_interval_is_ordered_for_representative_inputs() -> None:
    for features in (
        _features(),
        _features(difficulty=Difficulty.HARD, knowledge_type=KnowledgeType.PROBLEM_SOLVING),
        _features(content_amount=0, mastery=1.0),
    ):
        estimate = estimate_effort(features)
        assert estimate.low_minutes <= estimate.expected_minutes <= estimate.high_minutes


def test_all_minute_values_are_non_negative_and_invalid_negatives_are_rejected() -> None:
    estimate = estimate_effort(_features(content_amount=0, practice_requirement=0))

    assert min(
        *(_component_values(estimate)),
        estimate.low_minutes,
        estimate.expected_minutes,
        estimate.high_minutes,
    ) >= 0
    with pytest.raises(ValueError):
        EffortFeatures(content_amount=-1)


def test_extreme_inputs_are_capped_to_a_bounded_interval() -> None:
    estimate = estimate_effort(
        _features(
            content_amount=1_000_000,
            prerequisite_depth=1_000_000,
            code_density=1_000_000,
            formula_table_complexity=1_000_000,
            practice_requirement=1_000_000,
            difficulty=Difficulty.HARD,
            knowledge_type=KnowledgeType.PROBLEM_SOLVING,
            teaching_depth=TeachingDepth.DEEP,
            learner_level=LearnerLevel.BEGINNER,
        )
    )

    assert estimate.expected_minutes <= MAX_EXPECTED_MINUTES
    assert estimate.high_minutes <= MAX_HIGH_MINUTES
    assert any("capped@" in item for item in estimate.basis)


def test_estimator_is_deterministic() -> None:
    features = _features(mastery=0.4)

    first = estimate_effort(features)
    second = estimate_effort(features)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_estimator_version_changes_estimate_identity() -> None:
    features = _features()
    first = RuleBasedEffortEstimator("mentorflow.effort-rules.v1").estimate(features)
    second = RuleBasedEffortEstimator("mentorflow.effort-rules.v2").estimate(features)

    assert first.estimate_id != second.estimate_id
    assert first.estimator_version != second.estimator_version


def test_prediction_and_observation_contracts_remain_distinct() -> None:
    estimate = estimate_effort(_features(mastery=0.25))
    observation = EffortObservation.from_estimate(
        estimate,
        actual_active_minutes=47,
        attempts=3,
        mistakes=2,
        hints=1,
        mastery_before=0.25,
        mastery_after=0.6,
    )

    assert observation.predicted_minutes == estimate.expected_minutes
    assert observation.actual_active_minutes == 47
    assert observation.estimate_id == estimate.estimate_id
    assert observation.attempts == 3
    assert observation.mistakes == 2
    assert observation.hints == 1
    assert observation.mastery_before == 0.25
    assert observation.mastery_after == 0.6


def test_estimation_makes_no_provider_call() -> None:
    imported = _import_names(estimator_module)

    assert ESTIMATOR_PROVIDER_CALLS == 0
    assert not any("provider" in name.casefold() for name in imported)
    estimate_effort(_features())
    assert ESTIMATOR_PROVIDER_CALLS == 0


def test_estimation_makes_no_database_write(monkeypatch: pytest.MonkeyPatch) -> None:
    imported = _import_names(estimator_module)

    assert not {"sqlite3", "sqlalchemy", "app.db", "app.orm"} & imported

    def fail_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("estimator attempted a database connection")

    with monkeypatch.context() as guard:
        guard.setattr(sqlite3, "connect", fail_connect)
        estimate_effort(_features())


def test_interval_is_not_presented_as_fake_precision() -> None:
    estimate = estimate_effort(_features())

    assert estimate.low_minutes < estimate.expected_minutes < estimate.high_minutes
    assert estimate.confidence < 1.0
    assert "calibration=untrained:no_accuracy_claim" in estimate.basis


def test_source_video_duration_is_not_copied_into_learning_effort() -> None:
    short_source = estimate_effort(_features(source_duration_minutes=20))
    hundred_hour_source = estimate_effort(_features(source_duration_minutes=6_000))

    assert _component_values(hundred_hour_source) == _component_values(short_source)
    assert hundred_hour_source.expected_minutes != 6_000
    assert hundred_hour_source.expected_minutes <= MAX_EXPECTED_MINUTES
    assert "source_duration_minutes=excluded:not_a_learning_time_proxy" in (
        hundred_hour_source.basis
    )


def test_code_formula_teaching_depth_and_learner_level_are_effective_features() -> None:
    lighter = estimate_effort(
        _features(
            code_density=0,
            formula_table_complexity=0,
            teaching_depth=TeachingDepth.BRIEF,
            learner_level=LearnerLevel.ADVANCED,
        )
    )
    heavier = estimate_effort(
        _features(
            code_density=1,
            formula_table_complexity=1,
            teaching_depth=TeachingDepth.DEEP,
            learner_level=LearnerLevel.BEGINNER,
        )
    )

    assert heavier.expected_minutes > lighter.expected_minutes
