"""Immutable product contracts for plan creation, preview, and publication."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..review_effort import (
    EffortRange,
    ReviewCalibrationProfile,
    ReviewItem,
    ReviewRoundConfig,
    ReviewRoundEffort,
)
from ..runtime_v2 import ImmutablePlanSchedule, Weekday
from ..scheduling import (
    EffortIntervalPolicy,
    KnowledgeTask,
    PrerequisiteEdge,
    ScheduleSolveConfig,
)


PLAN_PRODUCT_SCHEMA_VERSION = "mentorflow.plan-product.v1"
DELIVERY_ID = "mentorflow-w6-07-plan-product-runtime-v1"


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )


class PlanCreationMode(StrEnum):
    NEW_PLAN = "new_plan"
    USER_REQUESTED_REGENERATION = "user_requested_regeneration"


class PlanOperationKind(StrEnum):
    SHIFT = "shift"
    REGENERATE = "regenerate"


class PlanPreviewStatus(StrEnum):
    READY_FOR_CONFIRMATION = "ready_for_confirmation"
    INFEASIBLE = "infeasible"
    NOT_SOLVED = "not_solved"


class FeasibilityCategory(StrEnum):
    NOT_ENOUGH_TOTAL_CAPACITY = "not_enough_total_capacity"
    SELECTED_REVIEW_ROUNDS_TOO_MANY = "selected_review_rounds_too_many"
    DEADLINE_TOO_SHORT = "deadline_too_short"
    WEEKLY_PREFERENCE_TOO_RESTRICTIVE = "weekly_preference_too_restrictive"
    UNSPLITTABLE_TASK = "unsplittable_task"
    PREREQUISITE_CONFLICT = "prerequisite_conflict"
    CONSTRAINT_INTERACTION = "constraint_interaction"
    SOLVER_NOT_SOLVED = "solver_not_solved"


class WeeklyAvailability(_FrozenContract):
    weekday: Weekday
    available_minutes: int = Field(gt=0, le=24 * 60)


class PlanBufferRequest(_FrozenContract):
    buffer_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=300)
    minutes: int = Field(gt=0, le=24 * 60)
    earliest_date: date
    latest_date: date

    @model_validator(mode="after")
    def validate_window(self) -> "PlanBufferRequest":
        if self.latest_date < self.earliest_date:
            raise ValueError("buffer latest_date precedes earliest_date")
        return self


class PlanCreationRequest(_FrozenContract):
    schema_version: Literal["mentorflow.plan-product.v1"] = (
        PLAN_PRODUCT_SCHEMA_VERSION
    )
    request_id: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(min_length=1, max_length=160)
    creation_mode: PlanCreationMode = PlanCreationMode.NEW_PLAN
    knowledge_tasks: tuple[KnowledgeTask, ...] = Field(min_length=1)
    prerequisite_edges: tuple[PrerequisiteEdge, ...] = ()
    review_items: tuple[ReviewItem, ...] = ()
    review_rounds: ReviewRoundConfig
    weekly_availability: tuple[WeeklyAvailability, ...] = Field(min_length=1)
    start_date: date
    deadline: date
    unavailable_dates: frozenset[date] = frozenset()
    buffers: tuple[PlanBufferRequest, ...] = ()
    effort_interval_policy: EffortIntervalPolicy = EffortIntervalPolicy.EXPECTED
    historical_calibration: ReviewCalibrationProfile | None = None
    solve_config: ScheduleSolveConfig = ScheduleSolveConfig()

    @model_validator(mode="after")
    def validate_creation_request(self) -> "PlanCreationRequest":
        if self.deadline < self.start_date:
            raise ValueError("deadline precedes start_date")
        task_ids = [item.task_id for item in self.knowledge_tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("knowledge task ids must be unique")
        task_id_set = set(task_ids)
        edge_pairs = [
            (item.prerequisite_task_id, item.dependent_task_id)
            for item in self.prerequisite_edges
        ]
        if len(edge_pairs) != len(set(edge_pairs)):
            raise ValueError("prerequisite edges must be unique")
        if any(
            source not in task_id_set or target not in task_id_set
            for source, target in edge_pairs
        ):
            raise ValueError("prerequisite edge references an unknown task")
        review_item_ids = [item.item_id for item in self.review_items]
        if len(review_item_ids) != len(set(review_item_ids)):
            raise ValueError("review item ids must be unique")
        if any(item.concept_id not in task_id_set for item in self.review_items):
            raise ValueError("review item references an unknown knowledge task")
        if self.review_rounds.selected_rounds > 0 and not self.review_items:
            raise ValueError("selected review rounds require review items")

        weekdays = [item.weekday for item in self.weekly_availability]
        if len(weekdays) != len(set(weekdays)):
            raise ValueError("weekly availability weekdays must be unique")
        if weekdays != sorted(weekdays, key=lambda item: item.number):
            raise ValueError("weekly availability must be ordered Monday to Sunday")
        if any(
            not self.start_date <= day <= self.deadline
            for day in self.unavailable_dates
        ):
            raise ValueError("unavailable date lies outside the plan window")

        buffer_ids = [item.buffer_id for item in self.buffers]
        if len(buffer_ids) != len(set(buffer_ids)):
            raise ValueError("buffer ids must be unique")
        if any(
            item.earliest_date < self.start_date
            or item.latest_date > self.deadline
            for item in self.buffers
        ):
            raise ValueError("buffer lies outside the plan window")
        buffer_windows = {
            (item.earliest_date, item.latest_date) for item in self.buffers
        }
        if len(buffer_windows) > 1:
            raise ValueError(
                "buffers must share one scheduling window for atomic reservation"
            )
        if sum(item.minutes for item in self.buffers) > 24 * 60:
            raise ValueError("combined atomic buffer exceeds one day")
        return self

    @property
    def allowed_weekdays(self) -> frozenset[Weekday]:
        return frozenset(item.weekday for item in self.weekly_availability)

    @property
    def weekly_capacity_minutes(self) -> int:
        return sum(item.available_minutes for item in self.weekly_availability)

    @property
    def buffer_total_minutes(self) -> int:
        return sum(item.minutes for item in self.buffers)


class InitialLearningEffort(EffortRange):
    """Named initial-learning component of the full effort envelope."""


class PlanEffortEnvelope(_FrozenContract):
    initial_learning_effort: InitialLearningEffort
    review_round_efforts: tuple[ReviewRoundEffort, ...] = ()
    review_total: EffortRange
    total: EffortRange

    @model_validator(mode="after")
    def validate_effort_totals(self) -> "PlanEffortEnvelope":
        review = EffortRange(
            low_minutes=sum(item.low_minutes for item in self.review_round_efforts),
            expected_minutes=sum(
                item.expected_minutes for item in self.review_round_efforts
            ),
            high_minutes=sum(item.high_minutes for item in self.review_round_efforts),
        )
        if self.review_total != review:
            raise ValueError("review_total must equal every review round")
        if self.total != self.initial_learning_effort + self.review_total:
            raise ValueError("plan effort total must equal initial plus review")
        return self

    @property
    def low_hours(self) -> float:
        return round(self.total.low_minutes / 60.0, 2)

    @property
    def expected_hours(self) -> float:
        return round(self.total.expected_minutes / 60.0, 2)

    @property
    def high_hours(self) -> float:
        return round(self.total.high_minutes / 60.0, 2)


class FeasibilityExplanation(_FrozenContract):
    category: FeasibilityCategory
    summary: str = Field(min_length=1, max_length=1_000)
    required_minutes: int | None = Field(default=None, ge=0)
    available_minutes: int | None = Field(default=None, ge=0)
    shortfall_minutes: int | None = Field(default=None, ge=0)
    evidence: tuple[str, ...] = Field(min_length=1)
    suggested_action: str = Field(min_length=1, max_length=1_000)


class PlanFeasibilityReport(_FrozenContract):
    solved: bool
    feasible: bool
    required_minutes: int = Field(ge=0)
    available_minutes: int = Field(ge=0)
    initial_learning_minutes: int = Field(ge=0)
    selected_review_minutes: int = Field(ge=0)
    buffer_minutes: int = Field(ge=0)
    explanations: tuple[FeasibilityExplanation, ...] = ()
    raw_infeasibility_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_report(self) -> "PlanFeasibilityReport":
        if self.required_minutes != (
            self.initial_learning_minutes
            + self.selected_review_minutes
            + self.buffer_minutes
        ):
            raise ValueError("feasibility required minutes do not add up")
        if self.feasible and not self.solved:
            raise ValueError("a feasible report must be solved")
        if self.feasible and (self.explanations or self.raw_infeasibility_codes):
            raise ValueError("feasible report cannot contain infeasibility reasons")
        if not self.feasible and not self.explanations:
            raise ValueError("infeasible report requires product explanations")
        return self


class PlanPreviewDailyLoad(_FrozenContract):
    day: date
    task_minutes: int = Field(ge=0)
    review_minutes: int = Field(ge=0)
    buffer_minutes: int = Field(ge=0)
    total_minutes: int = Field(ge=0)
    capacity_minutes: int = Field(ge=0)
    utilization: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_load(self) -> "PlanPreviewDailyLoad":
        if self.total_minutes != (
            self.task_minutes + self.review_minutes + self.buffer_minutes
        ):
            raise ValueError("daily preview load components do not add up")
        if self.total_minutes > self.capacity_minutes:
            raise ValueError("daily preview exceeds capacity")
        return self


class PlanBufferPreview(_FrozenContract):
    buffer_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=300)
    minutes: int = Field(gt=0)
    scheduled_dates: tuple[date, ...] = ()


class PlanCostShapeSeam(_FrozenContract):
    shape_version: Literal["mentorflow.plan-cost-shape.v1"] = (
        "mentorflow.plan-cost-shape.v1"
    )
    cp_sat_solve_calls: int = Field(default=1, ge=0)
    provider_calls: int = Field(default=0, ge=0)
    provider_cost_priced: bool = False
    notes: tuple[str, ...] = (
        "scheduler_compute_only",
        "provider_pricing_not_attached",
    )


class PlanPreviewDTO(_FrozenContract):
    schema_version: Literal["mentorflow.plan-product.v1"] = (
        PLAN_PRODUCT_SCHEMA_VERSION
    )
    preview_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(min_length=1, max_length=160)
    status: PlanPreviewStatus
    effort: PlanEffortEnvelope
    total_hours: float = Field(ge=0)
    initial_learning: InitialLearningEffort
    review_rounds: tuple[ReviewRoundEffort, ...] = ()
    review_total: EffortRange
    daily_load: tuple[PlanPreviewDailyLoad, ...] = ()
    finish_date: date | None = None
    buffers: tuple[PlanBufferPreview, ...] = ()
    buffer_total_minutes: int = Field(ge=0)
    warnings: tuple[str, ...] = ()
    cost_shape: PlanCostShapeSeam
    feasibility: PlanFeasibilityReport
    schedule_solution_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_preview(self) -> "PlanPreviewDTO":
        if self.total_hours != self.effort.expected_hours:
            raise ValueError("total_hours must expose expected full effort")
        if self.initial_learning != self.effort.initial_learning_effort:
            raise ValueError("initial learning preview does not match envelope")
        if self.review_rounds != self.effort.review_round_efforts:
            raise ValueError("review rounds preview does not match envelope")
        if self.review_total != self.effort.review_total:
            raise ValueError("review total preview does not match envelope")
        if self.buffer_total_minutes != sum(item.minutes for item in self.buffers):
            raise ValueError("buffer total does not match preview buffers")
        ready = self.status is PlanPreviewStatus.READY_FOR_CONFIRMATION
        if ready != self.feasibility.feasible:
            raise ValueError("preview status contradicts feasibility")
        if (
            self.status is PlanPreviewStatus.INFEASIBLE
            and not self.feasibility.solved
        ):
            raise ValueError("infeasible preview requires a solved report")
        if (
            self.status is PlanPreviewStatus.NOT_SOLVED
            and self.feasibility.solved
        ):
            raise ValueError("not_solved preview requires an unsolved report")
        if ready and self.finish_date is None:
            raise ValueError("feasible preview requires finish_date")
        if not ready and (self.daily_load or self.finish_date is not None):
            raise ValueError("infeasible preview cannot claim a schedule")
        return self


class PlanPublishConfirmationRequest(_FrozenContract):
    preview_id: str = Field(min_length=1, max_length=200)
    confirmation_id: str = Field(min_length=1, max_length=200)
    explicitly_confirmed_by_user: Literal[True]


class PlanPublishConfirmation(_FrozenContract):
    preview_id: str = Field(min_length=1, max_length=200)
    confirmation_id: str = Field(min_length=1, max_length=200)
    explicitly_confirmed_by_user: Literal[True] = True
    status: Literal["confirmed"] = "confirmed"


class PublishedPlan(_FrozenContract):
    schema_version: Literal["mentorflow.plan-product.v1"] = (
        PLAN_PRODUCT_SCHEMA_VERSION
    )
    publication_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(min_length=1, max_length=160)
    publication_version: int = Field(gt=0)
    preview_id: str = Field(min_length=1, max_length=200)
    confirmation_id: str = Field(min_length=1, max_length=200)
    published_on: date
    published_schedule: ImmutablePlanSchedule
    effort: PlanEffortEnvelope
    immutable: Literal[True] = True


class PlanRegenerationRequest(_FrozenContract):
    operation: Literal[PlanOperationKind.REGENERATE] = PlanOperationKind.REGENERATE
    source_publication_id: str = Field(min_length=1, max_length=200)
    explicitly_requested_by_user: Literal[True]
    creation_request: PlanCreationRequest

    @model_validator(mode="after")
    def validate_regeneration(self) -> "PlanRegenerationRequest":
        if (
            self.creation_request.creation_mode
            is not PlanCreationMode.USER_REQUESTED_REGENERATION
        ):
            raise ValueError("regeneration requires user_requested_regeneration mode")
        return self


class PlanOperationReceipt(_FrozenContract):
    operation: PlanOperationKind
    source_publication_id: str = Field(min_length=1, max_length=200)
    result_id: str = Field(min_length=1, max_length=200)
    cp_sat_allowed: bool
    cp_sat_calls: int = Field(ge=0)
    provider_calls: int = Field(ge=0)
    knowledge_order_reoptimization_allowed: bool
    knowledge_order_reoptimized: bool


__all__ = [
    "DELIVERY_ID",
    "PLAN_PRODUCT_SCHEMA_VERSION",
    "FeasibilityCategory",
    "FeasibilityExplanation",
    "InitialLearningEffort",
    "PlanBufferPreview",
    "PlanBufferRequest",
    "PlanCostShapeSeam",
    "PlanCreationMode",
    "PlanCreationRequest",
    "PlanEffortEnvelope",
    "PlanFeasibilityReport",
    "PlanOperationKind",
    "PlanOperationReceipt",
    "PlanPreviewDTO",
    "PlanPreviewDailyLoad",
    "PlanPreviewStatus",
    "PlanPublishConfirmation",
    "PlanPublishConfirmationRequest",
    "PlanRegenerationRequest",
    "PublishedPlan",
    "WeeklyAvailability",
]
