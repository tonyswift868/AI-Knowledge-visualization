from __future__ import annotations

import ast
import inspect
from time import perf_counter

from app.planning.runtime_v2 import (
    PlanRuntime,
    PlanShiftPreview,
    PlanShiftRequest,
    Weekday,
    WeeklyLearningPreferences,
)
from app.planning.runtime_v2 import contracts, progress, runtime, shift
from constraint_scheduler_fixtures import high_load_synthetic


def test_1000_task_plan_shift_benchmark_preserves_every_allocation() -> None:
    scenario = high_load_synthetic(1_000)
    plan_runtime = PlanRuntime.from_solver_result(
        scenario.problem,
        scenario.solution,
        plan_id="runtime-1000",
    )
    source = plan_runtime.current_schedule
    request = PlanShiftRequest(
        request_id="benchmark-shift-1000",
        plan_id=source.plan_id,
        expected_schedule_version=1,
        calendar_days_delta=1,
        weekly_preferences=WeeklyLearningPreferences(
            allowed_weekdays=frozenset(
                (
                    Weekday.MONDAY,
                    Weekday.TUESDAY,
                    Weekday.WEDNESDAY,
                    Weekday.THURSDAY,
                    Weekday.FRIDAY,
                )
            )
        ),
    )
    started = perf_counter()

    preview = plan_runtime.create_shift_preview(request)

    elapsed_seconds = perf_counter() - started
    assert preview.tasks_moved == 1_000
    assert preview.allocations_moved == 1_000
    assert len(preview.proposed_schedule.allocations) == 1_000
    assert sum(item.minutes for item in source.allocations) == sum(
        item.minutes for item in preview.proposed_schedule.allocations
    )
    assert elapsed_seconds < 5.0


def test_shift_preview_serialization_roundtrip() -> None:
    scenario = high_load_synthetic(100)
    plan_runtime = PlanRuntime.from_solver_result(
        scenario.problem,
        scenario.solution,
        plan_id="runtime-roundtrip",
    )
    preview = plan_runtime.create_shift_preview(
        PlanShiftRequest(
            request_id="roundtrip-shift",
            plan_id=plan_runtime.current_schedule.plan_id,
            expected_schedule_version=1,
            calendar_days_delta=2,
            weekly_preferences=WeeklyLearningPreferences(),
        )
    )

    restored = PlanShiftPreview.model_validate_json(preview.model_dump_json())

    assert restored == preview


def test_runtime_modules_have_no_provider_or_solver_execution_imports() -> None:
    imported: set[str] = set()
    for module in (contracts, progress, runtime, shift):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not any("provider" in item for item in imported)
    assert not any("ortools" in item for item in imported)
    assert not any(item.endswith("scheduling.solver") for item in imported)
