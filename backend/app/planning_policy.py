"""Pure planning policy primitives.

This module deliberately has no database or web-framework dependency.  It is
intended to be called by the existing ``services.build_plan`` flow instead of
creating a second persisted planning system.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from math import ceil
from typing import Iterable, Mapping

from .knowledge_policy import MASTERY_ESTIMATION_POLICY


TASK_BASE_MINUTES: Mapping[str, int] = {
    "explanation": 15,
    "lesson": 25,
    "guided_practice": 30,
    "independent_practice": 35,
    "review": 20,
    "transfer_test": 30,
    "final_task": 45,
    # Keep coding work distinct from conceptual explanation/review.  These
    # values are policy inputs, not completion-time measurements.
    "coding_solution": 40,
    "coding_practice": 45,
    "concept_understanding": 25,
    "concept_review": 20,
}
# Compatibility aliases; estimator coefficients are loaded from the active
# versioned policy rather than being duplicated in code.
DIFFICULTY_FACTOR: Mapping[str, float] = MASTERY_ESTIMATION_POLICY.difficulty_factors
LEVEL_FACTOR: Mapping[str, float] = MASTERY_ESTIMATION_POLICY.learner_level_factors


def _bounded_minutes(value: float, minimum: int = 5, maximum: int = 480) -> int:
    return max(minimum, min(maximum, int(ceil(value / 5.0) * 5)))


@dataclass(frozen=True)
class MinuteEstimate:
    """An explainable estimate, not an elapsed-time measurement."""

    estimated_minutes: int
    lower_minutes: int
    upper_minutes: int
    confidence: float
    reasons: tuple[str, ...]
    task_kind: str = "concept_understanding"
    difficulty: str = "medium"
    evidence: Mapping[str, object] = field(default_factory=dict)
    provider_metadata: Mapping[str, object] = field(default_factory=dict)
    uncertainty_ratio: float = 0.0

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not 0 < self.lower_minutes <= self.estimated_minutes <= self.upper_minutes:
            raise ValueError("minute interval must contain the estimate")
        if not str(self.task_kind or "").strip():
            raise ValueError("task_kind is required")
        if not isinstance(self.evidence, Mapping):
            raise ValueError("evidence must be a mapping")
        if not isinstance(self.provider_metadata, Mapping):
            raise ValueError("provider_metadata must be a mapping")
        if not 0 <= self.uncertainty_ratio < 1:
            raise ValueError("uncertainty_ratio must be between 0 and 1")

    def as_dict(self) -> dict[str, object]:
        """Serialize an explainable estimate without conflating elapsed time."""

        return {
            "estimated_minutes": self.estimated_minutes,
            "lower_minutes": self.lower_minutes,
            "upper_minutes": self.upper_minutes,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "task_kind": self.task_kind,
            "difficulty": self.difficulty,
            "evidence": dict(self.evidence),
            "provider_metadata": dict(self.provider_metadata),
            "uncertainty_ratio": self.uncertainty_ratio,
            "input_snapshot": dict(self.evidence),
        }

    @property
    def input_snapshot(self) -> Mapping[str, object]:
        """Versioned estimator inputs for inspection and audit output."""

        return self.evidence


@dataclass(frozen=True)
class TaskMinuteState:
    """Separate planned, observed, and reward-bearing task minutes."""

    estimated_minutes: int
    observed_minutes: int = 0
    credited_minutes: int = 0
    credit_settled: bool = False

    def __post_init__(self) -> None:
        if min(self.estimated_minutes, self.observed_minutes, self.credited_minutes) < 0:
            raise ValueError("minutes cannot be negative")
        if self.credit_settled and self.credited_minutes <= 0:
            raise ValueError("a settled credit must be positive")


def estimate_task_minutes(
    *,
    task_type: str,
    difficulty: str = "medium",
    learner_level: str = "intermediate",
    practice_items: int = 1,
    expected_hint_level: int = 0,
    evidence_count: int = 1,
    task_kind: str | None = None,
    evidence: Mapping[str, object] | None = None,
    provider_estimate_minutes: int | None = None,
    provider_metadata: Mapping[str, object] | None = None,
    mode: str | None = None,
    evidence_characters: int | None = None,
    evidence_chunks: int | None = None,
    material_volume: int | float = 0,
    structural_volume: int | float = 0,
    dependency_depth: int = 0,
    complexity: str = "medium",
    novelty: str = "unknown",
    output_requirement: str = "unknown",
    review_rounds: int = 0,
) -> MinuteEstimate:
    """Estimate initial learning work from evidence-grounded, stable inputs.

    This is intentionally domain independent.  A provider proposal is advisory
    and can only influence the deterministic estimate inside policy bounds.
    """

    policy = MASTERY_ESTIMATION_POLICY
    normalized_type = str(task_kind or mode or task_type or "concept_understanding").strip().casefold()
    if evidence is not None and not isinstance(evidence, Mapping):
        raise ValueError("evidence must be a mapping")
    if provider_metadata is not None and not isinstance(provider_metadata, Mapping):
        raise ValueError("provider_metadata must be a mapping")
    if provider_estimate_minutes is not None and int(provider_estimate_minutes) <= 0:
        raise ValueError("provider_estimate_minutes must be positive")
    def _factor(table: Mapping[str, float], value: object) -> tuple[str, float, bool]:
        key = str(value or "unknown").strip().casefold()
        if key in table:
            return key, float(table[key]), True
        return "unknown" if "unknown" in table else key, float(table.get("unknown", 1.0)), False

    normalized_difficulty = str(difficulty or "medium").strip().casefold()
    if normalized_difficulty not in DIFFICULTY_FACTOR:
        if any(token in normalized_difficulty for token in ("hard", "困难", "高级", "challenging")):
            normalized_difficulty = "hard"
        elif any(token in normalized_difficulty for token in ("easy", "简单", "基础", "introductory")):
            normalized_difficulty = "easy"
        elif any(token in normalized_difficulty for token in ("expert", "专家")):
            normalized_difficulty = "expert"
        else:
            normalized_difficulty = "medium"
    difficulty_factor = DIFFICULTY_FACTOR.get(normalized_difficulty, 1.0)
    normalized_level = str(learner_level or "intermediate").strip().casefold()
    if normalized_level not in LEVEL_FACTOR:
        if any(token in normalized_level for token in ("zero", "foundation", "零基础", "初级")):
            normalized_level = "beginner"
        elif any(token in normalized_level for token in ("advanced", "高级", "专家")):
            normalized_level = "advanced"
        else:
            normalized_level = "intermediate"
    level_factor = LEVEL_FACTOR.get(normalized_level, 1.0)
    mode_key, mode_factor, mode_known = _factor(policy.mode_factors, mode or normalized_type)
    complexity_key, complexity_factor, complexity_known = _factor(policy.complexity_factors, complexity)
    novelty_key, novelty_factor, novelty_known = _factor(policy.novelty_factors, novelty)
    output_key, output_factor, output_known = _factor(policy.output_requirement_factors, output_requirement)

    chars = max(0, int(evidence_characters if evidence_characters is not None else (evidence or {}).get("characters", 0)))
    chunks = max(0, int(evidence_chunks if evidence_chunks is not None else evidence_count))
    material_units = max(0.0, float(material_volume or 0))
    structural_units = max(0.0, float(structural_volume or 0))
    depth = max(0, int(dependency_depth or 0))
    information_units = max(
        policy.minimum_information_units,
        min(policy.maximum_information_units, chars / float(policy.information_unit_characters)),
    )
    volume_minutes = (
        float(policy.minutes_per_information_unit) * information_units
        + float(policy.minutes_per_evidence_chunk) * chunks
        + float(policy.minutes_per_material_unit) * material_units
        + float(policy.minutes_per_structural_unit) * structural_units
        + float(policy.minutes_per_dependency_depth) * depth
    )
    practice_factor = 1.0 + max(0, int(practice_items) - 1) * (policy.review_round_ratio * 0.5)
    hint_factor = 1.0 + max(0, int(expected_hint_level)) * (policy.uncertainty_missing_input_ratio * 0.5)
    base = float(policy.base_minutes) + volume_minutes
    structural_estimate = _bounded_minutes(
        base * difficulty_factor * level_factor * complexity_factor * novelty_factor * output_factor * mode_factor * practice_factor * hint_factor,
        minimum=policy.minimum_total_minutes,
        maximum=policy.maximum_total_minutes,
    )
    # A provider is a bounded estimator only: its proposal is blended with the
    # deterministic policy estimate and constrained to a conservative interval.
    provider_value = None
    provider_bounds: tuple[int, int] | None = None
    if provider_estimate_minutes is not None:
        lower_bound = _bounded_minutes(
            structural_estimate * policy.provider_lower_bound_ratio,
            minimum=policy.minimum_total_minutes,
            maximum=policy.maximum_total_minutes,
        )
        upper_bound = _bounded_minutes(
            structural_estimate * policy.provider_upper_bound_ratio,
            minimum=policy.minimum_total_minutes,
            maximum=policy.maximum_total_minutes,
        )
        provider_bounds = (min(lower_bound, upper_bound), max(lower_bound, upper_bound))
        provider_value = max(provider_bounds[0], min(provider_bounds[1], int(provider_estimate_minutes)))
    estimate = _bounded_minutes(
        structural_estimate if provider_value is None else structural_estimate * (1.0 - policy.provider_blend_weight) + provider_value * policy.provider_blend_weight,
        minimum=policy.minimum_total_minutes,
        maximum=policy.maximum_total_minutes,
    )
    missing_inputs = sum(not bool(value) for value in (chars, chunks, material_units, structural_units))
    uncertainty = min(
        0.8,
        policy.uncertainty_base_ratio
        + policy.uncertainty_volume_ratio * min(1.0, information_units / policy.maximum_information_units)
        + policy.uncertainty_missing_input_ratio * missing_inputs,
    )
    confidence = round(max(0.2, min(0.95, 1.0 - uncertainty + (0.04 if provider_value is not None else 0.0))), 2)
    spread = max(5, _bounded_minutes(estimate * uncertainty, minimum=5, maximum=policy.maximum_total_minutes))
    lower = _bounded_minutes(max(policy.minimum_total_minutes, estimate - spread), minimum=policy.minimum_total_minutes, maximum=policy.maximum_total_minutes)
    upper = _bounded_minutes(min(policy.maximum_total_minutes, estimate + spread), minimum=policy.minimum_total_minutes, maximum=policy.maximum_total_minutes)
    if upper <= estimate and estimate < policy.maximum_total_minutes:
        upper = min(policy.maximum_total_minutes, estimate + 5)
    if lower > estimate:
        lower = estimate
    input_snapshot = {
        "schema": "mentorflow.duration-estimator-input.v2",
        "task_kind": normalized_type,
        "mode": mode_key,
        "evidence_characters": chars,
        "evidence_chunks": chunks,
        "evidence_character_count": chars,
        "evidence_chunk_count": chunks,
        "material_volume": round(material_units, 3),
        "structural_volume": round(structural_units, 3),
        "dependency_depth": depth,
        "complexity": complexity_key,
        "novelty": novelty_key,
        "output_requirement": output_key,
        "learner_level": normalized_level,
        "review_rounds": max(0, int(review_rounds)),
    }
    return MinuteEstimate(
        estimated_minutes=estimate,
        lower_minutes=min(lower, estimate),
        upper_minutes=max(upper, estimate),
        confidence=confidence,
        reasons=(
            f"task_type={task_type}",
            f"task_kind={normalized_type}",
            f"difficulty={normalized_difficulty}",
            f"learner_level={normalized_level}",
            f"practice_items={max(0, practice_items)}",
            f"expected_hint_level={max(0, expected_hint_level)}",
            f"evidence_count={chunks}",
            f"evidence_characters={chars}",
            f"material_volume={material_units:g}",
            f"structural_volume={structural_units:g}",
            f"dependency_depth={depth}",
            f"complexity={complexity_key}",
            f"novelty={novelty_key}",
            f"output_requirement={output_key}",
            "provider_bounded=true" if provider_value is not None else "provider_bounded=false",
        ),
        task_kind=normalized_type,
        difficulty=normalized_difficulty,
        evidence={**dict(evidence or {}), **input_snapshot},
        provider_metadata={
            **dict(provider_metadata or {}),
            **({
                "proposed_minutes": int(provider_estimate_minutes),
                "bounded_minutes": provider_value,
                "bounds": {"lower": provider_bounds[0], "upper": provider_bounds[1]},
                "blend_weight": policy.provider_blend_weight,
            } if provider_value is not None else {}),
        },
        uncertainty_ratio=round(uncertainty, 3),
    )


def estimate_provider_task_minutes(
    *,
    goal: str,
    learner_level: str,
    learning_style: str,
    concept_or_task_kind: str,
    evidence_size: int | None = None,
    evidence_count: int | None = None,
    expected_output: str = "",
    mode: str = "",
    hints: int = 0,
    review_rounds: int = 0,
    schedule_constraints: Mapping[str, object] | None = None,
    difficulty: str = "medium",
    material_volume: int | float = 0,
    structural_volume: int | float = 0,
    dependency_depth: int = 0,
    complexity: str = "medium",
    novelty: str = "unknown",
    output_requirement: str = "unknown",
    evidence_characters: int | None = None,
    evidence_chunks: int | None = None,
    provider_estimate_minutes: int | None = None,
    provider_metadata: Mapping[str, object] | None = None,
) -> MinuteEstimate:
    """Validate comprehensive provider input and return a bounded estimate.

    ``mode`` is intentionally explicit so coding solution/practice cannot be
    silently treated as concept understanding/review.  Schedule constraints
    are evidence for the estimator, never permission to mutate a schedule.
    """

    evidence_size = max(0, int(evidence_characters if evidence_characters is not None else (evidence_size or 0)))
    evidence_count = max(0, int(evidence_chunks if evidence_chunks is not None else (evidence_count or 0)))
    text_fields = {
        "goal": goal,
        "learner_level": learner_level,
        "learning_style": learning_style,
        "concept_or_task_kind": concept_or_task_kind,
        "expected_output": expected_output,
        "mode": mode,
    }
    if any(not str(value or "").strip() for value in text_fields.values()):
        raise ValueError("provider estimator requires goal, learner, task kind, output, and mode")
    if hints < 0 or review_rounds < 0:
        raise ValueError("provider estimator counts cannot be negative")
    normalized_mode = str(mode).strip().casefold()
    mode_aliases = {
        "coding": "coding_practice",
        "coding_solution": "coding_solution",
        "coding_practice": "coding_practice",
        "understand": "concept_understanding",
        "concept_understanding": "concept_understanding",
        "review": "concept_review",
        "concept_review": "concept_review",
    }
    task_kind = mode_aliases.get(normalized_mode)
    if task_kind is None:
        raise ValueError("mode must distinguish coding_solution, coding_practice, concept_understanding, or concept_review")
    schedule = dict(schedule_constraints or {})
    estimate = estimate_task_minutes(
        task_type=task_kind,
        task_kind=task_kind,
        difficulty=difficulty,
        learner_level=learner_level,
        practice_items=max(1, review_rounds + (1 if task_kind == "coding_practice" else 0)),
        expected_hint_level=hints,
        evidence_count=max(1, evidence_count),
        mode=task_kind,
        evidence_characters=max(0, int(evidence_size)),
        evidence_chunks=max(0, int(evidence_count)),
        material_volume=max(0, float(material_volume)),
        structural_volume=max(0, float(structural_volume)),
        dependency_depth=max(0, int(dependency_depth)),
        complexity=complexity,
        novelty=novelty,
        output_requirement=output_requirement,
        review_rounds=max(0, int(review_rounds)),
        evidence={
            "size": int(evidence_size),
            "count": int(evidence_count),
            "characters": int(evidence_size),
            "chunks": int(evidence_count),
            "expected_output": str(expected_output).strip(),
            "goal": str(goal).strip(),
            "material_volume": float(material_volume),
            "structural_volume": float(structural_volume),
            "dependency_depth": int(dependency_depth),
            "complexity": str(complexity),
            "novelty": str(novelty),
            "output_requirement": str(output_requirement),
            "schedule_constraints": schedule,
        },
        provider_estimate_minutes=provider_estimate_minutes,
        provider_metadata={
            **dict(provider_metadata or {}),
            "provider_input_schema": "mentorflow.task-duration-estimator.v1",
            "learner_level": str(learner_level).strip(),
            "learning_style": str(learning_style).strip(),
            "mode": normalized_mode,
            "review_rounds": int(review_rounds),
        },
    )
    return estimate


def observe_minutes(state: TaskMinuteState, observed_minutes: int) -> TaskMinuteState:
    """Record bounded active observation without granting any credit."""

    return replace(state, observed_minutes=max(0, min(24 * 60, int(observed_minutes))))


def settle_credit(
    state: TaskMinuteState,
    *,
    passed: bool,
    credit_minutes: int | None = None,
) -> TaskMinuteState:
    """Settle reward minutes exactly once after server-verified completion.

    Observed time is intentionally not used as the reward amount, so leaving a
    browser tab open cannot increase rewards.
    """

    if state.credit_settled or not passed:
        return state
    credited = state.estimated_minutes if credit_minutes is None else int(credit_minutes)
    credited = max(1, min(state.estimated_minutes, credited))
    return replace(state, credited_minutes=credited, credit_settled=True)


@dataclass(frozen=True)
class CalibrationSample:
    task_type: str
    estimated_minutes: int
    observed_minutes: int
    passed: bool


def future_calibration_factors(samples: Iterable[CalibrationSample]) -> dict[str, float]:
    """Return conservative factors for future estimates only."""

    ratios: dict[str, list[float]] = {}
    for sample in samples:
        if not sample.passed or sample.estimated_minutes <= 0 or sample.observed_minutes <= 0:
            continue
        ratio = sample.observed_minutes / sample.estimated_minutes
        ratios.setdefault(sample.task_type, []).append(max(0.6, min(1.6, ratio)))
    return {
        task_type: round(max(0.75, min(1.35, sum(values) / len(values))), 3)
        for task_type, values in ratios.items()
    }


@dataclass(frozen=True)
class LeaveRange:
    start: date
    end: date
    reason: str = "temporary_leave"

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("leave end cannot be before start")

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end


@dataclass(frozen=True)
class CapacityPolicy:
    daily_minutes: int | Mapping[int, int] = 30
    weekly_minutes: int | None = None
    recurring_blackout_weekdays: frozenset[int] = field(default_factory=frozenset)
    blackout_dates: frozenset[date] = field(default_factory=frozenset)
    temporary_leave: tuple[LeaveRange, ...] = ()

    def __post_init__(self) -> None:
        values = (
            self.daily_minutes.values()
            if isinstance(self.daily_minutes, Mapping)
            else (self.daily_minutes,)
        )
        if any(value < 0 for value in values):
            raise ValueError("daily capacity cannot be negative")
        if self.weekly_minutes is not None and self.weekly_minutes < 0:
            raise ValueError("weekly capacity cannot be negative")
        if any(day < 0 or day > 6 for day in self.recurring_blackout_weekdays):
            raise ValueError("blackout weekdays must be between 0 and 6")

    def raw_daily_capacity(self, value: date) -> int:
        if value.weekday() in self.recurring_blackout_weekdays:
            return 0
        if value in self.blackout_dates:
            return 0
        if any(leave.contains(value) for leave in self.temporary_leave):
            return 0
        if isinstance(self.daily_minutes, Mapping):
            return max(0, int(self.daily_minutes.get(value.weekday(), 0)))
        return max(0, int(self.daily_minutes))


def capacity_calendar(start: date, end: date, policy: CapacityPolicy) -> dict[date, int]:
    """Build effective capacities with ISO-week limits applied."""

    if end < start:
        raise ValueError("end cannot be before start")
    result: dict[date, int] = {}
    week_used: dict[tuple[int, int], int] = {}
    cursor = start
    while cursor <= end:
        raw = policy.raw_daily_capacity(cursor)
        iso = cursor.isocalendar()
        week_key = (iso.year, iso.week)
        if policy.weekly_minutes is not None:
            remaining = max(0, policy.weekly_minutes - week_used.get(week_key, 0))
            raw = min(raw, remaining)
        result[cursor] = raw
        week_used[week_key] = week_used.get(week_key, 0) + raw
        cursor += timedelta(days=1)
    return result


def total_effective_capacity(start: date, end: date, policy: CapacityPolicy) -> int:
    return sum(capacity_calendar(start, end, policy).values())


def feasibility_gap(required_minutes: int, capacities: Mapping[date, int]) -> dict[str, int | bool]:
    available = sum(max(0, value) for value in capacities.values())
    required = max(0, int(required_minutes))
    return {
        "required_minutes": required,
        "available_minutes": available,
        "gap_minutes": max(0, required - available),
        "feasible": available >= required,
    }
