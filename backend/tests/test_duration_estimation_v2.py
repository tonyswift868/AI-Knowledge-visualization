from __future__ import annotations

from app.generic_knowledge import mastery_time_estimate
from app.knowledge_policy import MASTERY_ESTIMATION_POLICY
from app.planning_policy import estimate_provider_task_minutes, estimate_task_minutes


def test_v2_snapshot_is_domain_independent_and_monotonic() -> None:
    small = estimate_task_minutes(
        task_type="concept_understanding",
        evidence_characters=600,
        evidence_chunks=1,
        material_volume=1,
        structural_volume=1,
        dependency_depth=0,
        complexity="low",
        novelty="familiar",
        output_requirement="explanation",
    )
    large = estimate_task_minutes(
        task_type="concept_understanding",
        evidence_characters=1800,
        evidence_chunks=3,
        material_volume=3,
        structural_volume=3,
        dependency_depth=2,
        complexity="high",
        novelty="new",
        output_requirement="transfer",
    )
    assert large.estimated_minutes >= small.estimated_minutes
    assert large.evidence["schema"] == "mentorflow.duration-estimator-input.v2"
    assert {"task_kind", "mode", "evidence_characters", "evidence_chunks", "material_volume", "structural_volume", "dependency_depth", "complexity", "novelty", "output_requirement", "learner_level", "review_rounds"} <= set(large.evidence)
    assert large.lower_minutes <= large.estimated_minutes <= large.upper_minutes
    assert large.uncertainty_ratio > 0


def test_provider_undershoot_and_overshoot_are_bounded() -> None:
    deterministic = estimate_task_minutes(
        task_type="coding_practice",
        mode="coding_practice",
        evidence_characters=2000,
        evidence_chunks=4,
        material_volume=4,
        structural_volume=4,
        dependency_depth=2,
        complexity="high",
        output_requirement="artifact",
        learner_level="beginner",
    )
    low = estimate_provider_task_minutes(
        goal="goal",
        learner_level="beginner",
        learning_style="examples",
        concept_or_task_kind="coding_practice",
        evidence_size=2000,
        evidence_count=4,
        expected_output="artifact",
        mode="coding_practice",
        material_volume=4,
        structural_volume=4,
        dependency_depth=2,
        complexity="high",
        output_requirement="artifact",
        provider_estimate_minutes=1,
    )
    high = estimate_provider_task_minutes(
        goal="goal",
        learner_level="beginner",
        learning_style="examples",
        concept_or_task_kind="coding_practice",
        evidence_size=2000,
        evidence_count=4,
        expected_output="artifact",
        mode="coding_practice",
        material_volume=4,
        structural_volume=4,
        dependency_depth=2,
        complexity="high",
        output_requirement="artifact",
        provider_estimate_minutes=10000,
    )
    assert low.estimated_minutes >= deterministic.estimated_minutes * MASTERY_ESTIMATION_POLICY.provider_lower_bound_ratio * (1 - MASTERY_ESTIMATION_POLICY.provider_blend_weight)
    assert high.estimated_minutes <= deterministic.estimated_minutes * MASTERY_ESTIMATION_POLICY.provider_upper_bound_ratio
    assert low.provider_metadata["bounds"]["lower"] <= low.provider_metadata["bounded_minutes"] <= low.provider_metadata["bounds"]["upper"]


def test_review_aggregate_has_two_round_invariant() -> None:
    estimate = mastery_time_estimate(
        text="A grounded explanation with a checkable outcome.",
        difficulty="medium",
        current_level="intermediate",
        review_rounds=2,
    )
    assert estimate["projected_review_minutes"] == estimate["one_review_round_minutes"] * 2
    assert estimate["total_mastery_minutes"] == estimate["initial_learning_minutes"] + estimate["projected_review_minutes"]
