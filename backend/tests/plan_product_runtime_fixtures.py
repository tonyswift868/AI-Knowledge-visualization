from __future__ import annotations

from datetime import date

from app.planning.effort import EffortEstimate, EffortFeatures
from app.planning.product_runtime import (
    PlanBufferRequest,
    PlanCreationMode,
    PlanCreationRequest,
    WeeklyAvailability,
)
from app.planning.review_effort import (
    ReviewItem,
    ReviewQuestionType,
    ReviewRoundConfig,
)
from app.planning.runtime_v2 import Weekday
from app.planning.scheduling import (
    KnowledgeTask,
    PrerequisiteEdge,
    TaskSplitPolicy,
)


def exact_effort(minutes: int, *, estimate_id: str) -> EffortEstimate:
    return EffortEstimate(
        estimate_id=estimate_id,
        estimator_version="plan-product-fixture.v1",
        understand_minutes=minutes,
        example_minutes=0,
        practice_minutes=0,
        assessment_minutes=0,
        review_minutes=0,
        low_minutes=minutes,
        expected_minutes=minutes,
        high_minutes=minutes,
        confidence=1,
        basis=("exact_test_fixture=true",),
        features=EffortFeatures(),
    )


def knowledge_task(
    task_id: str,
    minutes: int,
    *,
    split_policy: TaskSplitPolicy = TaskSplitPolicy.SPLITTABLE,
) -> KnowledgeTask:
    return KnowledgeTask(
        task_id=task_id,
        title=task_id.replace("-", " ").title(),
        effort=exact_effort(minutes, estimate_id=f"effort-{task_id}-{minutes}"),
        split_policy=split_policy,
        minimum_chunk_minutes=1,
        maximum_chunks=(1 if split_policy is TaskSplitPolicy.UNSPLITTABLE else None),
        allow_final_short_chunk=split_policy is TaskSplitPolicy.SPLITTABLE,
    )


def review_items_for(
    tasks: tuple[KnowledgeTask, ...],
    *,
    tiny: bool = False,
) -> tuple[ReviewItem, ...]:
    return tuple(
        ReviewItem(
            item_id=f"review-item-{index}",
            concept_id=item.task_id,
            question_type=(
                ReviewQuestionType.SHORT_RECALL
                if index % 2 == 0
                else ReviewQuestionType.EXPLANATION
            ),
            expected_answer_seconds=(0.1 if tiny else None),
            expected_evaluation_seconds=(0.1 if tiny else None),
            expected_feedback_seconds=(0.1 if tiny else None),
        )
        for index, item in enumerate(tasks)
    )


def standard_creation_request(
    *,
    request_id: str = "product-create-1",
    plan_id: str = "product-plan-1",
    rounds: int = 3,
    creation_mode: PlanCreationMode = PlanCreationMode.NEW_PLAN,
) -> PlanCreationRequest:
    start = date(2026, 9, 1)
    tasks = (
        knowledge_task("foundation", 20),
        knowledge_task("application", 25),
        knowledge_task("mastery", 20),
    )
    return PlanCreationRequest(
        request_id=request_id,
        plan_id=plan_id,
        creation_mode=creation_mode,
        knowledge_tasks=tasks,
        prerequisite_edges=(
            PrerequisiteEdge(
                prerequisite_task_id="foundation",
                dependent_task_id="application",
            ),
            PrerequisiteEdge(
                prerequisite_task_id="application",
                dependent_task_id="mastery",
            ),
        ),
        review_items=review_items_for(tasks),
        review_rounds=ReviewRoundConfig(selected_rounds=rounds),
        weekly_availability=(
            WeeklyAvailability(weekday=Weekday.TUESDAY, available_minutes=75),
            WeeklyAvailability(weekday=Weekday.WEDNESDAY, available_minutes=75),
            WeeklyAvailability(weekday=Weekday.THURSDAY, available_minutes=75),
            WeeklyAvailability(weekday=Weekday.FRIDAY, available_minutes=75),
        ),
        start_date=start,
        deadline=date(2026, 9, 4),
        buffers=(
            PlanBufferRequest(
                buffer_id="final-confidence-buffer",
                label="Final confidence buffer",
                minutes=15,
                earliest_date=date(2026, 9, 4),
                latest_date=date(2026, 9, 4),
            ),
        ),
    )


__all__ = [
    "exact_effort",
    "knowledge_task",
    "review_items_for",
    "standard_creation_request",
]
