from __future__ import annotations

import ast
from datetime import date, timedelta
import inspect
from time import perf_counter

import pytest

from app.planning.effort import EffortEstimate, EffortFeatures
from app.planning.review_effort import (
    InitialReviewScheduleRequest,
    ReviewEffortInput,
    ReviewEstimateUseCase,
    ReviewItem,
    ReviewQuestionType,
    ReviewRoundConfig,
    ReviewScheduleBridge,
    initial_learning_effort_for_problem,
)
from app.planning.review_effort import (
    calibration,
    contracts,
    estimator,
    schedule_bridge,
)
from app.planning.scheduling import (
    DailyCapacity,
    KnowledgeTask,
    PlanScheduleProblem,
    ScheduleSolveConfig,
    ScheduleStatus,
)


def large_material() -> tuple[PlanScheduleProblem, tuple[ReviewItem, ...]]:
    start = date(2026, 11, 2)
    one_minute = EffortEstimate(
        estimate_id="benchmark-one-minute",
        estimator_version="benchmark-fixture.v1",
        understand_minutes=1,
        example_minutes=0,
        practice_minutes=0,
        assessment_minutes=0,
        review_minutes=0,
        low_minutes=1,
        expected_minutes=1,
        high_minutes=1,
        confidence=1,
        basis=("scheduler_compute_fixture=true",),
        features=EffortFeatures(),
    )
    tasks = tuple(
        KnowledgeTask(
            task_id=f"concept-{index:04d}",
            title=f"Concept {index}",
            effort=one_minute,
            minimum_chunk_minutes=1,
        )
        for index in range(100)
    )
    items = tuple(
        ReviewItem(
            item_id=f"item-{index:04d}",
            concept_id=tasks[index % len(tasks)].task_id,
            question_type=ReviewQuestionType.SHORT_RECALL,
        )
        for index in range(1_000)
    )
    problem = PlanScheduleProblem(
        problem_id="review-benchmark-1000-concepts",
        tasks=tasks,
        start_date=start,
        deadline=start + timedelta(days=7),
        daily_capacities=tuple(
            DailyCapacity(day=start + timedelta(days=index), available_minutes=1_440)
            for index in range(8)
        ),
    )
    return problem, items


@pytest.mark.parametrize("selected_rounds", (0, 1, 3, 5))
def test_scheduler_compute_benchmark_1000_items_and_fixed_rounds(
    selected_rounds: int,
) -> None:
    """Compute benchmark only; it makes no learning-effectiveness claim."""

    problem, items = large_material()
    request = InitialReviewScheduleRequest(
        request_id=f"benchmark-{selected_rounds}",
        base_problem=problem,
        effort_input=ReviewEffortInput(
            request_id=f"benchmark-effort-{selected_rounds}",
            use_case=ReviewEstimateUseCase.NEW_PLAN,
            initial_learning_effort=initial_learning_effort_for_problem(problem),
            round_config=ReviewRoundConfig(selected_rounds=selected_rounds),
            items=items,
        ),
        solve_config=ScheduleSolveConfig(max_time_seconds=30),
    )
    started = perf_counter()

    result = ReviewScheduleBridge().solve(request)

    elapsed = perf_counter() - started
    assert result.schedule_solution.status is ScheduleStatus.FEASIBLE
    assert len(result.schedule_problem.tasks) == 100
    assert len(request.effort_input.items) == 1_000
    assert result.schedule_problem.selected_review_round_count == selected_rounds
    assert len(result.schedule_problem.review_workloads) == selected_rounds
    assert sum(
        item.minutes for item in result.schedule_problem.review_workloads
    ) == result.plan_preview.review_total.expected_minutes
    assert elapsed < 35
    print(
        "SCHEDULER_COMPUTE_BENCHMARK "
        f"concepts=100 items=1000 review_rounds={selected_rounds} "
        f"elapsed_ms={elapsed * 1000:.3f} "
        "learning_effectiveness_claim=none"
    )


def test_review_planning_has_no_provider_or_fsrs_execution_imports() -> None:
    imported: set[str] = set()
    for module in (calibration, contracts, estimator, schedule_bridge):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not any("provider" in item.lower() for item in imported)
    assert not any("fsrs" in item.lower() for item in imported)
