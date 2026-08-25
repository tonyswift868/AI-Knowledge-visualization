from __future__ import annotations

import ast
from datetime import date
from functools import lru_cache
import inspect
from time import perf_counter

import pytest

from app.planning.product_runtime import (
    FeasibilityCategory,
    PlanCreationRequest,
    PlanCreationService,
    PlanPreviewStatus,
    WeeklyAvailability,
)
from app.planning.product_runtime import contracts, creation, feasibility, publication
from app.planning.review_effort import (
    ReviewItem,
    ReviewQuestionType,
    ReviewRoundConfig,
)
from app.planning.runtime_v2 import Weekday
from app.planning.scheduling import (
    ScheduleSolveConfig,
    ScheduleStatus,
    ScheduleValidator,
    TaskSplitPolicy,
)
from plan_product_runtime_fixtures import knowledge_task


@lru_cache(maxsize=1)
def large_corpus() -> tuple[tuple, tuple[ReviewItem, ...], tuple[ReviewItem, ...]]:
    tasks = tuple(knowledge_task(f"concept-{index:04d}", 1) for index in range(1_000))
    tiny_items = tuple(
        ReviewItem(
            item_id=f"tiny-review-{index:04d}",
            concept_id=item.task_id,
            question_type=ReviewQuestionType.SHORT_RECALL,
            expected_answer_seconds=0.1,
            expected_evaluation_seconds=0.1,
            expected_feedback_seconds=0.1,
        )
        for index, item in enumerate(tasks)
    )
    normal_items = tuple(
        ReviewItem(
            item_id=f"normal-review-{index:04d}",
            concept_id=item.task_id,
            question_type=(
                ReviewQuestionType.SHORT_RECALL
                if index % 2 == 0
                else ReviewQuestionType.EXPLANATION
            ),
        )
        for index, item in enumerate(tasks)
    )
    return tasks, tiny_items, normal_items


def availability(*values: tuple[Weekday, int]) -> tuple[WeeklyAvailability, ...]:
    return tuple(
        WeeklyAvailability(weekday=weekday, available_minutes=minutes)
        for weekday, minutes in values
    )


@pytest.mark.parametrize(
    ("rounds", "weekly_name", "weekly"),
    (
        (0, "monday_only", availability((Weekday.MONDAY, 1_440))),
        (
            1,
            "monday_wednesday",
            availability((Weekday.MONDAY, 600), (Weekday.WEDNESDAY, 600)),
        ),
        (
            3,
            "tuesday_thursday",
            availability(
                (Weekday.TUESDAY, 600),
                (Weekday.THURSDAY, 600),
            ),
        ),
        (
            5,
            "weekend",
            availability((Weekday.SATURDAY, 600), (Weekday.SUNDAY, 600)),
        ),
    ),
)
def test_scheduler_compute_benchmark_1000_concepts_feasible_corpus(
    rounds: int,
    weekly_name: str,
    weekly: tuple[WeeklyAvailability, ...],
) -> None:
    """Scheduler-compute benchmark; no learning-effectiveness claim is made."""

    tasks, tiny_items, _ = large_corpus()
    request = PlanCreationRequest(
        request_id=f"scale-feasible-{rounds}-{weekly_name}",
        plan_id=f"scale-feasible-plan-{rounds}",
        knowledge_tasks=tasks,
        review_items=tiny_items,
        review_rounds=ReviewRoundConfig(selected_rounds=rounds),
        weekly_availability=weekly,
        start_date=date(2026, 11, 2),
        deadline=date(2026, 11, 8),
        solve_config=ScheduleSolveConfig(max_time_seconds=15),
    )
    started = perf_counter()

    outcome = PlanCreationService().create(request)

    elapsed = perf_counter() - started
    assert outcome.schedule_solution.status is ScheduleStatus.FEASIBLE
    assert outcome.preview.status is PlanPreviewStatus.READY_FOR_CONFIRMATION
    assert len(outcome.schedule_problem.tasks) == 1_000
    assert outcome.schedule_problem.selected_review_round_count == rounds
    assert len(outcome.preview.review_rounds) == rounds
    assert ScheduleValidator().validate(
        outcome.schedule_problem,
        outcome.schedule_solution,
    ).valid
    assert elapsed < 20
    print(
        "PLAN_PRODUCT_SCHEDULER_BENCHMARK "
        f"concepts=1000 review_rounds={rounds} weekly={weekly_name} "
        f"elapsed_ms={elapsed * 1000:.3f} "
        "learning_effectiveness_claim=none"
    )


@pytest.mark.parametrize(
    ("case_name", "rounds", "weekly", "expected_category"),
    (
        (
            "selected_reviews",
            5,
            availability((Weekday.MONDAY, 1_440)),
            FeasibilityCategory.SELECTED_REVIEW_ROUNDS_TOO_MANY,
        ),
        (
            "weekly_restrictive",
            0,
            availability((Weekday.MONDAY, 500)),
            FeasibilityCategory.WEEKLY_PREFERENCE_TOO_RESTRICTIVE,
        ),
        (
            "deadline_short",
            0,
            availability((Weekday.MONDAY, 500)),
            FeasibilityCategory.DEADLINE_TOO_SHORT,
        ),
    ),
)
def test_1000_concept_infeasible_corpus_is_explained_and_validated(
    case_name: str,
    rounds: int,
    weekly: tuple[WeeklyAvailability, ...],
    expected_category: FeasibilityCategory,
) -> None:
    tasks, _, normal_items = large_corpus()
    one_day = case_name in {"selected_reviews", "deadline_short"}
    request = PlanCreationRequest(
        request_id=f"scale-infeasible-{case_name}",
        plan_id=f"scale-infeasible-plan-{case_name}",
        knowledge_tasks=tasks,
        review_items=normal_items if rounds else (),
        review_rounds=ReviewRoundConfig(selected_rounds=rounds),
        weekly_availability=weekly,
        start_date=date(2026, 11, 2),
        deadline=date(2026, 11, 2) if one_day else date(2026, 11, 8),
    )

    outcome = PlanCreationService().create(request)

    assert outcome.schedule_solution.status is ScheduleStatus.INFEASIBLE
    assert expected_category in {
        item.category for item in outcome.preview.feasibility.explanations
    }
    assert ScheduleValidator().validate(
        outcome.schedule_problem,
        outcome.schedule_solution,
    ).valid


def test_1000_concept_unsplittable_corpus_has_specific_reason() -> None:
    normal_tasks, _, _ = large_corpus()
    tasks = (
        knowledge_task(
            "concept-atomic",
            600,
            split_policy=TaskSplitPolicy.UNSPLITTABLE,
        ),
        *normal_tasks[1:],
    )
    request = PlanCreationRequest(
        request_id="scale-infeasible-unsplittable",
        plan_id="scale-infeasible-plan-unsplittable",
        knowledge_tasks=tasks,
        review_rounds=ReviewRoundConfig(selected_rounds=0),
        weekly_availability=availability(
            (Weekday.MONDAY, 500),
            (Weekday.TUESDAY, 500),
            (Weekday.WEDNESDAY, 500),
            (Weekday.THURSDAY, 500),
        ),
        start_date=date(2026, 11, 2),
        deadline=date(2026, 11, 5),
    )

    outcome = PlanCreationService().create(request)

    assert outcome.schedule_solution.status is ScheduleStatus.INFEASIBLE
    assert FeasibilityCategory.UNSPLITTABLE_TASK in {
        item.category for item in outcome.preview.feasibility.explanations
    }
    assert ScheduleValidator().validate(
        outcome.schedule_problem,
        outcome.schedule_solution,
    ).valid


def test_1000_concept_timeout_is_not_mislabeled_as_infeasible() -> None:
    tasks, tiny_items, _ = large_corpus()
    request = PlanCreationRequest(
        request_id="scale-not-solved-timeout",
        plan_id="scale-not-solved-timeout-plan",
        knowledge_tasks=tasks,
        review_items=tiny_items,
        review_rounds=ReviewRoundConfig(selected_rounds=3),
        weekly_availability=availability(
            (Weekday.MONDAY, 250),
            (Weekday.TUESDAY, 250),
            (Weekday.WEDNESDAY, 250),
            (Weekday.THURSDAY, 250),
            (Weekday.FRIDAY, 250),
        ),
        start_date=date(2026, 11, 2),
        deadline=date(2026, 11, 8),
        solve_config=ScheduleSolveConfig(max_time_seconds=1e-9),
    )

    outcome = PlanCreationService().create(request)

    assert outcome.schedule_solution.status is ScheduleStatus.NOT_SOLVED
    assert outcome.preview.status is PlanPreviewStatus.NOT_SOLVED
    assert outcome.preview.feasibility.solved is False
    assert FeasibilityCategory.SOLVER_NOT_SOLVED in {
        item.category for item in outcome.preview.feasibility.explanations
    }
    assert ScheduleValidator().validate(
        outcome.schedule_problem,
        outcome.schedule_solution,
    ).valid


def test_product_runtime_has_no_provider_execution_imports() -> None:
    imported: set[str] = set()
    for module in (contracts, creation, feasibility, publication):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not any("provider" in item.lower() for item in imported)
