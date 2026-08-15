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
DIFFICULTY_FACTOR: Mapping[str, float] = {
    "easy": 0.8,
    "medium": 1.0,
    "hard": 1.3,
    "expert": 1.6,
}
LEVEL_FACTOR: Mapping[str, float] = {
    "beginner": 1.25,
    "intermediate": 1.0,
    "advanced": 0.85,
    "expert": 0.75,
}


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
        }


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
) -> MinuteEstimate:
    """Estimate cognitive task time from stable, inspectable inputs."""

    normalized_type = str(task_kind or task_type or "concept_understanding").strip().casefold()
    if evidence is not None and not isinstance(evidence, Mapping):
        raise ValueError("evidence must be a mapping")
    if provider_metadata is not None and not isinstance(provider_metadata, Mapping):
        raise ValueError("provider_metadata must be a mapping")
    if provider_estimate_minutes is not None and int(provider_estimate_minutes) <= 0:
        raise ValueError("provider_estimate_minutes must be positive")
    base = TASK_BASE_MINUTES.get(normalized_type, TASK_BASE_MINUTES.get(task_type, 25))
    difficulty_factor = DIFFICULTY_FACTOR.get(difficulty, 1.0)
    level_factor = LEVEL_FACTOR.get(learner_level, 1.0)
    practice_factor = 1.0 + max(0, practice_items - 1) * 0.16
    hint_factor = 1.0 + max(0, expected_hint_level) * 0.08
    evidence_factor = 1.0 + max(0, evidence_count - 1) * 0.04
    structural_estimate = _bounded_minutes(
        base * difficulty_factor * level_factor * practice_factor * hint_factor * evidence_factor
    )
    # A provider is a bounded estimator only: its proposal is blended with the
    # deterministic policy estimate and constrained to a conservative interval.
    provider_value = None
    if provider_estimate_minutes is not None:
        provider_value = _bounded_minutes(int(provider_estimate_minutes))
        provider_value = max(
            _bounded_minutes(structural_estimate * 0.5),
            min(_bounded_minutes(structural_estimate * 1.5), provider_value),
        )
    estimate = _bounded_minutes(
        structural_estimate if provider_value is None else structural_estimate * 0.6 + provider_value * 0.4
    )
    known_type = task_type in TASK_BASE_MINUTES
    known_difficulty = difficulty in DIFFICULTY_FACTOR
    known_level = learner_level in LEVEL_FACTOR
    confidence = 0.55 + 0.12 * known_type + 0.08 * known_difficulty + 0.08 * known_level
    confidence -= min(0.18, max(0, practice_items - 5) * 0.03)
    confidence = round(max(0.25, min(0.9, confidence)), 2)
    spread = max(5, _bounded_minutes(estimate * (0.16 + (1 - confidence) * 0.24)))
    lower = _bounded_minutes(max(5, estimate - spread))
    upper = _bounded_minutes(estimate + spread)
    return MinuteEstimate(
        estimated_minutes=estimate,
        lower_minutes=min(lower, estimate),
        upper_minutes=max(upper, estimate),
        confidence=confidence,
        reasons=(
            f"task_type={task_type}",
            f"task_kind={normalized_type}",
            f"difficulty={difficulty}",
            f"learner_level={learner_level}",
            f"practice_items={max(0, practice_items)}",
            f"expected_hint_level={max(0, expected_hint_level)}",
            f"evidence_count={max(0, evidence_count)}",
            "provider_bounded=true" if provider_value is not None else "provider_bounded=false",
        ),
        task_kind=normalized_type,
        difficulty=str(difficulty or "medium").strip().casefold(),
        evidence=dict(evidence or {"count": max(0, evidence_count)}),
        provider_metadata={
            **dict(provider_metadata or {}),
            **({"proposed_minutes": int(provider_estimate_minutes), "bounded_minutes": provider_value} if provider_value is not None else {}),
        },
    )


def estimate_provider_task_minutes(
    *,
    goal: str,
    learner_level: str,
    learning_style: str,
    concept_or_task_kind: str,
    evidence_size: int,
    evidence_count: int,
    expected_output: str,
    mode: str,
    hints: int = 0,
    review_rounds: int = 0,
    schedule_constraints: Mapping[str, object] | None = None,
    difficulty: str = "medium",
    provider_estimate_minutes: int | None = None,
    provider_metadata: Mapping[str, object] | None = None,
) -> MinuteEstimate:
    """Validate comprehensive provider input and return a bounded estimate.

    ``mode`` is intentionally explicit so coding solution/practice cannot be
    silently treated as concept understanding/review.  Schedule constraints
    are evidence for the estimator, never permission to mutate a schedule.
    """

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
    if evidence_size < 0 or evidence_count < 0 or hints < 0 or review_rounds < 0:
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
        evidence={
            "size": int(evidence_size),
            "count": int(evidence_count),
            "expected_output": str(expected_output).strip(),
            "goal": str(goal).strip(),
            "schedule_constraints": schedule,
        },
        provider_estimate_minutes=provider_estimate_minutes,
        provider_metadata={
            **dict(provider_metadata or {}),
            "provider_input_schema": "mentorflow.task-duration-estimator.v1",
            "learner_level": str(learner_level).strip(),
            "learning_style": str(learning_style).strip(),
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
