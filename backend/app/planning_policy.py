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

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not 0 < self.lower_minutes <= self.estimated_minutes <= self.upper_minutes:
            raise ValueError("minute interval must contain the estimate")


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
) -> MinuteEstimate:
    """Estimate cognitive task time from stable, inspectable inputs."""

    base = TASK_BASE_MINUTES.get(task_type, 25)
    difficulty_factor = DIFFICULTY_FACTOR.get(difficulty, 1.0)
    level_factor = LEVEL_FACTOR.get(learner_level, 1.0)
    practice_factor = 1.0 + max(0, practice_items - 1) * 0.16
    hint_factor = 1.0 + max(0, expected_hint_level) * 0.08
    evidence_factor = 1.0 + max(0, evidence_count - 1) * 0.04
    estimate = _bounded_minutes(
        base * difficulty_factor * level_factor * practice_factor * hint_factor * evidence_factor
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
            f"difficulty={difficulty}",
            f"learner_level={learner_level}",
            f"practice_items={max(0, practice_items)}",
            f"expected_hint_level={max(0, expected_hint_level)}",
            f"evidence_count={max(0, evidence_count)}",
        ),
    )


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
