from __future__ import annotations

from datetime import date

import pytest

from app.planning_policy import (
    CalibrationSample,
    CapacityPolicy,
    LeaveRange,
    TaskMinuteState,
    capacity_calendar,
    estimate_task_minutes,
    feasibility_gap,
    future_calibration_factors,
    observe_minutes,
    settle_credit,
)


def test_estimate_is_explainable_and_sensitive_to_task_inputs() -> None:
    easy = estimate_task_minutes(
        task_type="review",
        difficulty="easy",
        learner_level="advanced",
        practice_items=1,
    )
    hard = estimate_task_minutes(
        task_type="independent_practice",
        difficulty="hard",
        learner_level="beginner",
        practice_items=4,
        expected_hint_level=2,
        evidence_count=3,
    )
    assert hard.estimated_minutes > easy.estimated_minutes
    assert hard.lower_minutes <= hard.estimated_minutes <= hard.upper_minutes
    assert 0 < hard.confidence <= 1
    assert "difficulty=hard" in hard.reasons


def test_observed_time_never_grants_credit_and_settlement_is_immutable() -> None:
    state = observe_minutes(TaskMinuteState(estimated_minutes=30), 600)
    assert state.observed_minutes == 600
    assert state.credited_minutes == 0
    assert settle_credit(state, passed=False) == state

    settled = settle_credit(state, passed=True)
    assert settled.credited_minutes == 30
    assert settled.credit_settled is True
    assert settle_credit(settled, passed=True, credit_minutes=5) == settled


def test_calibration_only_returns_factors_for_future_estimates() -> None:
    state = TaskMinuteState(estimated_minutes=30, credited_minutes=30, credit_settled=True)
    factors = future_calibration_factors([
        CalibrationSample("lesson", 30, 45, True),
        CalibrationSample("lesson", 30, 0, True),
        CalibrationSample("review", 20, 100, False),
    ])
    assert factors == {"lesson": 1.35}
    assert state.credited_minutes == 30


def test_capacity_calendar_combines_daily_weekly_and_blackouts() -> None:
    policy = CapacityPolicy(
        daily_minutes={0: 40, 1: 40, 2: 40, 3: 40, 4: 40, 5: 20, 6: 20},
        weekly_minutes=100,
        recurring_blackout_weekdays=frozenset({6}),
        blackout_dates=frozenset({date(2026, 1, 6)}),
        temporary_leave=(LeaveRange(date(2026, 1, 8), date(2026, 1, 8), "medical"),),
    )
    values = capacity_calendar(date(2026, 1, 5), date(2026, 1, 11), policy)
    assert values[date(2026, 1, 5)] == 40
    assert values[date(2026, 1, 6)] == 0
    assert values[date(2026, 1, 7)] == 40
    assert values[date(2026, 1, 8)] == 0
    assert values[date(2026, 1, 9)] == 20
    assert values[date(2026, 1, 10)] == 0
    assert values[date(2026, 1, 11)] == 0
    assert sum(values.values()) == 100


@pytest.mark.parametrize("days", [7, 30, 90])
def test_feasibility_for_standard_horizons(days: int) -> None:
    start = date(2026, 2, 2)
    end = start.fromordinal(start.toordinal() + days - 1)
    values = capacity_calendar(
        start,
        end,
        CapacityPolicy(daily_minutes=30, recurring_blackout_weekdays=frozenset({5, 6})),
    )
    result = feasibility_gap(sum(values.values()) + 1, values)
    assert result["available_minutes"] == sum(values.values())
    assert result["gap_minutes"] == 1
    assert result["feasible"] is False
