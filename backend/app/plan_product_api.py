"""Standalone product API for plan preview, publication, and daily runtime UX.

The module intentionally owns its own FastAPI factory and is not imported by
``app.main``.  Integrators can mount ``router`` later, while this delivery can
be exercised directly through ``create_plan_product_api_app``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from math import ceil
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .planning.effort import EffortEstimate, EffortFeatures
from .planning.product_runtime import (
    PlanBufferRequest,
    PlanCreationMode,
    PlanCreationRequest,
    PlanPreviewStatus,
    PlanProductError,
    PlanProductRuntime,
    PlanPublishConfirmationRequest,
    PlanRegenerationRequest,
    WeeklyAvailability,
)
from .planning.review_effort import (
    ReviewItem,
    ReviewQuestionType,
    ReviewRoundConfig,
)
from .planning.runtime_v2 import (
    ImmutablePlanSchedule,
    PlanShiftError,
    PlanShiftRequest,
    Weekday,
    WeeklyLearningPreferences,
)
from .planning.scheduling import (
    AllocationKind,
    KnowledgeTask,
    ScheduleSolveConfig,
    TaskSplitPolicy,
)


DELIVERY_ID = "mentorflow-live-demo-07-plan-product-experience-v1"
EXPIRED_PLAN_MESSAGE = "计划已逾期，但任务不会丢失，你可以继续完成。"
WEEKDAY_ORDER = tuple(Weekday)


class _ApiInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlanTaskIn(_ApiInput):
    task_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=500)
    minutes: int = Field(gt=0, le=1_440)
    unsplittable: bool = False


class PlanPreviewCreateIn(_ApiInput):
    request_id: str = Field(
        default_factory=lambda: f"plan-create-{uuid4().hex}",
        min_length=1,
        max_length=160,
    )
    plan_id: str = Field(default="learning-plan", min_length=1, max_length=160)
    start_date: date = Field(default_factory=date.today)
    deadline: date
    daily_minutes: int = Field(default=60, gt=0, le=1_440)
    weekly_learning_days: tuple[Weekday, ...] = (
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
        Weekday.FRIDAY,
    )
    unavailable_dates: frozenset[date] = frozenset()
    review_rounds: int = Field(default=3, ge=0, le=12)
    buffer_minutes: int = Field(default=30, ge=0, le=1_440)
    tasks: tuple[PlanTaskIn, ...] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_window_and_availability(self) -> "PlanPreviewCreateIn":
        if self.deadline < self.start_date:
            raise ValueError("截止日期不能早于计划开始日期")
        if (self.deadline - self.start_date).days > 730:
            raise ValueError("计划日期范围不能超过 730 天")
        if len(self.weekly_learning_days) != len(set(self.weekly_learning_days)):
            raise ValueError("每周学习日不能重复")
        if not self.weekly_learning_days:
            raise ValueError("至少选择一个每周学习日")
        if any(
            item < self.start_date or item > self.deadline
            for item in self.unavailable_dates
        ):
            raise ValueError("不可学习日期必须位于计划日期范围内")
        task_ids = [item.task_id for item in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("学习任务 ID 不能重复")
        return self


class PublishPlanIn(_ApiInput):
    confirmation_id: str = Field(
        default_factory=lambda: f"publish-confirm-{uuid4().hex}",
        min_length=1,
        max_length=200,
    )
    explicitly_confirmed_by_user: Literal[True]
    published_on: date = Field(default_factory=date.today)


class CompleteTaskIn(_ApiInput):
    explicitly_completed_by_user: Literal[True]
    completed_on: date = Field(default_factory=date.today)


class ShiftPreviewIn(_ApiInput):
    request_id: str = Field(
        default_factory=lambda: f"shift-{uuid4().hex}",
        min_length=1,
        max_length=160,
    )
    calendar_days_delta: int = Field(default=1, gt=0, le=36_500)
    shift_from_date: date | None = None
    weekly_learning_days: tuple[Weekday, ...]
    unavailable_dates: frozenset[date] = frozenset()

    @model_validator(mode="after")
    def validate_weekdays(self) -> "ShiftPreviewIn":
        if not self.weekly_learning_days:
            raise ValueError("整体后移至少需要一个学习日")
        if len(self.weekly_learning_days) != len(set(self.weekly_learning_days)):
            raise ValueError("整体后移学习日不能重复")
        return self


class ShiftConfirmIn(_ApiInput):
    confirmation_id: str = Field(
        default_factory=lambda: f"shift-confirm-{uuid4().hex}",
        min_length=1,
        max_length=200,
    )
    explicitly_confirmed_by_user: Literal[True]


class RegeneratePlanIn(_ApiInput):
    explicitly_requested_by_user: Literal[True]
    creation: PlanPreviewCreateIn


def _effort(task: PlanTaskIn) -> EffortEstimate:
    understand = ceil(task.minutes * 0.50)
    practice = ceil(task.minutes * 0.32)
    assessment = task.minutes - understand - practice
    if assessment < 0:
        practice += assessment
        assessment = 0
    return EffortEstimate(
        estimate_id=f"api-effort-{task.task_id}",
        estimator_version="plan-product-api-visible-estimate.v1",
        understand_minutes=understand,
        example_minutes=0,
        practice_minutes=practice,
        assessment_minutes=assessment,
        review_minutes=0,
        low_minutes=max(0, int(task.minutes * 0.80)),
        expected_minutes=task.minutes,
        high_minutes=max(task.minutes, ceil(task.minutes * 1.25)),
        confidence=0.65,
        basis=(
            "user_visible_task_estimate=true",
            "review_effort_is_estimated_separately=true",
        ),
        features=EffortFeatures(),
    )


def _last_learning_day(value: PlanPreviewCreateIn) -> date:
    allowed = {item.number for item in value.weekly_learning_days}
    candidate = value.deadline
    while candidate >= value.start_date:
        if candidate.weekday() in allowed and candidate not in value.unavailable_dates:
            return candidate
        candidate -= timedelta(days=1)
    return value.deadline


def _to_creation_request(
    value: PlanPreviewCreateIn,
    *,
    mode: PlanCreationMode = PlanCreationMode.NEW_PLAN,
    plan_id: str | None = None,
) -> PlanCreationRequest:
    task_values = tuple(
        KnowledgeTask(
            task_id=item.task_id,
            title=item.title,
            effort=_effort(item),
            split_policy=(
                TaskSplitPolicy.UNSPLITTABLE
                if item.unsplittable
                else TaskSplitPolicy.SPLITTABLE
            ),
            minimum_chunk_minutes=min(10, item.minutes),
            maximum_chunks=1 if item.unsplittable else None,
            allow_final_short_chunk=not item.unsplittable,
        )
        for item in value.tasks
    )
    review_items = (
        tuple(
            ReviewItem(
                item_id=f"review-{index:04d}-{item.task_id}",
                concept_id=item.task_id,
                question_type=(
                    ReviewQuestionType.SHORT_RECALL
                    if index % 3 == 0
                    else (
                        ReviewQuestionType.EXPLANATION
                        if index % 3 == 1
                        else ReviewQuestionType.CODE_PROBLEM
                    )
                ),
            )
            for index, item in enumerate(task_values)
        )
        if value.review_rounds
        else ()
    )
    buffer_day = _last_learning_day(value)
    buffers = (
        (
            PlanBufferRequest(
                buffer_id="product-finish-buffer",
                label="收尾与意外缓冲",
                minutes=value.buffer_minutes,
                earliest_date=buffer_day,
                latest_date=buffer_day,
            ),
        )
        if value.buffer_minutes
        else ()
    )
    order = {item: index for index, item in enumerate(WEEKDAY_ORDER)}
    return PlanCreationRequest(
        request_id=value.request_id,
        plan_id=plan_id or value.plan_id,
        creation_mode=mode,
        knowledge_tasks=task_values,
        review_items=review_items,
        review_rounds=ReviewRoundConfig(selected_rounds=value.review_rounds),
        weekly_availability=tuple(
            WeeklyAvailability(weekday=item, available_minutes=value.daily_minutes)
            for item in sorted(value.weekly_learning_days, key=order.__getitem__)
        ),
        start_date=value.start_date,
        deadline=value.deadline,
        unavailable_dates=value.unavailable_dates,
        buffers=buffers,
        solve_config=ScheduleSolveConfig(max_time_seconds=3),
    )


def _schedule_payload(schedule: ImmutablePlanSchedule) -> dict[str, Any]:
    definitions = schedule.task_by_id
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for allocation in sorted(
        schedule.allocations,
        key=lambda item: (item.day, item.order_in_day, item.allocation_id),
    ):
        target_id = allocation.task_id or allocation.reservation_id or allocation.allocation_id
        if allocation.kind is AllocationKind.TASK:
            title = definitions[target_id].title
            label = "Initial Learning"
        elif allocation.kind is AllocationKind.REVIEW:
            definition = definitions[target_id]
            title = definition.title
            label = title if title.startswith("Review Round") else target_id.replace("_", " ").title()
        else:
            title = "收尾与意外缓冲"
            label = "Buffer"
        by_day[allocation.day].append(
            {
                "allocation_id": allocation.allocation_id,
                "task_id": target_id,
                "title": title,
                "kind": allocation.kind.value,
                "label": label,
                "minutes": allocation.minutes,
                "chunk_index": allocation.chunk_index,
                "chunk_count": allocation.chunk_count,
            }
        )
    days: list[dict[str, Any]] = []
    for day, tasks in sorted(by_day.items()):
        days.append(
            {
                "date": day.isoformat(),
                "total_minutes": sum(item["minutes"] for item in tasks),
                "initial_minutes": sum(
                    item["minutes"] for item in tasks if item["kind"] == "task"
                ),
                "review_minutes": sum(
                    item["minutes"] for item in tasks if item["kind"] == "review"
                ),
                "buffer_minutes": sum(
                    item["minutes"] for item in tasks if item["kind"] == "final_buffer"
                ),
                "tasks": tasks,
            }
        )
    return {
        "schedule_id": schedule.schedule_id,
        "version": schedule.version,
        "start_date": schedule.start_date.isoformat(),
        "finish_date": schedule.end_date.isoformat(),
        "deadline": schedule.deadline.isoformat(),
        "immutable": True,
        "days": days,
    }


def _preview_payload(
    preview: Any,
    schedule: ImmutablePlanSchedule | None,
) -> dict[str, Any]:
    return {
        "delivery_id": DELIVERY_ID,
        "preview": preview.model_dump(mode="json"),
        "schedule": _schedule_payload(schedule) if schedule is not None else None,
    }


class PlanProductApiService:
    """Thread-safe in-memory demo adapter over the immutable W6 runtime."""

    def __init__(self, runtime: PlanProductRuntime | None = None) -> None:
        self.runtime = runtime or PlanProductRuntime()
        self._lock = RLock()
        self._inputs_by_preview: dict[str, PlanPreviewCreateIn] = {}
        self._published_schedule_json: dict[str, str] = {}

    def create_preview(self, value: PlanPreviewCreateIn) -> dict[str, Any]:
        with self._lock:
            preview = self.runtime.create_preview(_to_creation_request(value))
            self._inputs_by_preview[preview.preview_id] = value
            schedule = (
                self.runtime.preview_schedule(preview.preview_id)
                if preview.status is PlanPreviewStatus.READY_FOR_CONFIRMATION
                else None
            )
            return _preview_payload(preview, schedule)

    def publish(self, preview_id: str, value: PublishPlanIn) -> dict[str, Any]:
        with self._lock:
            source = self._inputs_by_preview.get(preview_id)
            if source is None:
                raise PlanProductError("找不到要发布的计划预览")
            self.runtime.confirm_publication(
                PlanPublishConfirmationRequest(
                    preview_id=preview_id,
                    confirmation_id=value.confirmation_id,
                    explicitly_confirmed_by_user=True,
                )
            )
            published = self.runtime.publish(preview_id, published_on=value.published_on)
            self._published_schedule_json[published.publication_id] = (
                published.published_schedule.model_dump_json()
            )
            return self.get_published(published.publication_id)

    def get_published(self, publication_id: str) -> dict[str, Any]:
        with self._lock:
            published = self.runtime.get_published(publication_id)
            current = self.runtime.current_runtime_schedule(publication_id)
            return {
                "delivery_id": DELIVERY_ID,
                "published": published.model_dump(mode="json"),
                "current_schedule": _schedule_payload(current),
                "published_schedule_unchanged": (
                    published.published_schedule.model_dump_json()
                    == self._published_schedule_json.get(publication_id)
                ),
            }

    def daily(self, publication_id: str, *, today: date) -> dict[str, Any]:
        with self._lock:
            snapshot = self.runtime.snapshot(publication_id, today=today)
            labels = {
                "overdue": "OVERDUE",
                "due_today": "DUE TODAY",
                "upcoming": "UPCOMING",
                "completed": "COMPLETED",
                "skipped_by_user": "SKIPPED",
            }
            groups: dict[str, list[dict[str, Any]]] = {
                "OVERDUE": [],
                "DUE TODAY": [],
                "UPCOMING": [],
                "COMPLETED": [],
            }
            for task in snapshot.tasks:
                groups.setdefault(labels[task.status.value], []).append(
                    task.model_dump(mode="json")
                )
            return {
                "delivery_id": DELIVERY_ID,
                "snapshot": snapshot.model_dump(mode="json"),
                "groups": groups,
                "expired_message": (
                    EXPIRED_PLAN_MESSAGE
                    if snapshot.status.value == "expired_with_overdue"
                    else None
                ),
                "automatic_shift_applied": False,
            }

    def complete(
        self,
        publication_id: str,
        task_id: str,
        value: CompleteTaskIn,
    ) -> dict[str, Any]:
        with self._lock:
            completion = self.runtime.record_completion(
                publication_id,
                task_id,
                completed_on=value.completed_on,
            )
            return completion.model_dump(mode="json")

    def shift_preview(
        self,
        publication_id: str,
        value: ShiftPreviewIn,
    ) -> dict[str, Any]:
        with self._lock:
            published = self.runtime.get_published(publication_id)
            current = self.runtime.current_runtime_schedule(publication_id)
            preview, receipt = self.runtime.create_shift_preview(
                publication_id,
                PlanShiftRequest(
                    request_id=value.request_id,
                    plan_id=published.plan_id,
                    expected_schedule_version=current.version,
                    shift_from_date=value.shift_from_date,
                    calendar_days_delta=value.calendar_days_delta,
                    weekly_preferences=WeeklyLearningPreferences(
                        allowed_weekdays=frozenset(value.weekly_learning_days),
                        unavailable_dates=value.unavailable_dates,
                    ),
                ),
            )
            return {
                "delivery_id": DELIVERY_ID,
                "shift_preview": preview.model_dump(mode="json"),
                "receipt": receipt.model_dump(mode="json"),
            }

    def confirm_shift(
        self,
        publication_id: str,
        preview_id: str,
        value: ShiftConfirmIn,
    ) -> dict[str, Any]:
        with self._lock:
            confirmation = self.runtime.confirm_shift(
                publication_id,
                preview_id,
                confirmation_id=value.confirmation_id,
            )
            application = self.runtime.apply_shift(publication_id, preview_id)
            return {
                "delivery_id": DELIVERY_ID,
                "confirmation": confirmation.model_dump(mode="json"),
                "application": application.model_dump(mode="json"),
                "current_schedule": _schedule_payload(application.applied_schedule),
            }

    def regenerate(
        self,
        publication_id: str,
        value: RegeneratePlanIn,
    ) -> dict[str, Any]:
        with self._lock:
            source = self.runtime.get_published(publication_id)
            creation = _to_creation_request(
                value.creation,
                mode=PlanCreationMode.USER_REQUESTED_REGENERATION,
                plan_id=source.plan_id,
            )
            preview, receipt = self.runtime.regenerate(
                PlanRegenerationRequest(
                    source_publication_id=publication_id,
                    explicitly_requested_by_user=True,
                    creation_request=creation,
                )
            )
            self._inputs_by_preview[preview.preview_id] = value.creation
            schedule = (
                self.runtime.preview_schedule(preview.preview_id)
                if preview.status is PlanPreviewStatus.READY_FOR_CONFIRMATION
                else None
            )
            return {
                **_preview_payload(preview, schedule),
                "receipt": receipt.model_dump(mode="json"),
                "source_publication_id": publication_id,
            }


def demo_plan_request(*, today: date | None = None) -> PlanPreviewCreateIn:
    current = today or date.today()
    tasks = tuple(
        PlanTaskIn(
            task_id=f"large-material-concept-{index:02d}",
            title=f"大型资料知识点 {index:02d}：理解、练习与验收",
            minutes=18 + (index % 4) * 3,
        )
        for index in range(1, 37)
    )
    return PlanPreviewCreateIn(
        request_id=f"large-material-demo-{current.isoformat()}",
        plan_id="large-material-demo-plan",
        start_date=current - timedelta(days=1),
        deadline=current + timedelta(days=35),
        daily_minutes=60,
        weekly_learning_days=tuple(Weekday),
        unavailable_dates=frozenset(
            (current + timedelta(days=10), current + timedelta(days=24))
        ),
        review_rounds=3,
        buffer_minutes=60,
        tasks=tasks,
    )


_default_service = PlanProductApiService()


def get_plan_product_api_service(request: Request) -> PlanProductApiService:
    return getattr(request.app.state, 'plan_product_service', _default_service)


router = APIRouter(prefix="/api/plan-product", tags=["plan-product"])


def _raise_api_error(exc: Exception) -> None:
    status = 404 if "unknown" in str(exc).lower() or "找不到" in str(exc) else 422
    if isinstance(exc, PlanShiftError):
        status = 409
    raise HTTPException(
        status_code=status,
        detail={
            "error": {
                "code": type(exc).__name__,
                "message": str(exc),
            }
        },
    ) from exc


@router.get("/demo")
def get_demo_request() -> dict[str, Any]:
    return {
        "delivery_id": DELIVERY_ID,
        "fixture_name": "large-material-initial-plus-3-reviews",
        "creation": demo_plan_request().model_dump(mode="json"),
    }


@router.post("/previews")
def create_preview(
    value: PlanPreviewCreateIn,
    service: PlanProductApiService = Depends(get_plan_product_api_service),
) -> dict[str, Any]:
    try:
        return service.create_preview(value)
    except (PlanProductError, PlanShiftError, ValueError) as exc:
        _raise_api_error(exc)


@router.post("/previews/{preview_id}/publish")
def publish_preview(
    preview_id: str,
    value: PublishPlanIn,
    service: PlanProductApiService = Depends(get_plan_product_api_service),
) -> dict[str, Any]:
    try:
        return service.publish(preview_id, value)
    except (PlanProductError, PlanShiftError, ValueError) as exc:
        _raise_api_error(exc)


@router.get("/published/{publication_id}")
def get_published(
    publication_id: str,
    service: PlanProductApiService = Depends(get_plan_product_api_service),
) -> dict[str, Any]:
    try:
        return service.get_published(publication_id)
    except (PlanProductError, PlanShiftError, ValueError) as exc:
        _raise_api_error(exc)


@router.get("/published/{publication_id}/daily")
def get_daily_status(
    publication_id: str,
    today: date = Query(default_factory=date.today),
    service: PlanProductApiService = Depends(get_plan_product_api_service),
) -> dict[str, Any]:
    try:
        return service.daily(publication_id, today=today)
    except (PlanProductError, PlanShiftError, ValueError) as exc:
        _raise_api_error(exc)


@router.post("/published/{publication_id}/tasks/{task_id}/complete")
def complete_task(
    publication_id: str,
    task_id: str,
    value: CompleteTaskIn,
    service: PlanProductApiService = Depends(get_plan_product_api_service),
) -> dict[str, Any]:
    try:
        return service.complete(publication_id, task_id, value)
    except (PlanProductError, PlanShiftError, ValueError) as exc:
        _raise_api_error(exc)


@router.post("/published/{publication_id}/shifts/preview")
def preview_shift(
    publication_id: str,
    value: ShiftPreviewIn,
    service: PlanProductApiService = Depends(get_plan_product_api_service),
) -> dict[str, Any]:
    try:
        return service.shift_preview(publication_id, value)
    except (PlanProductError, PlanShiftError, ValueError) as exc:
        _raise_api_error(exc)


@router.post("/published/{publication_id}/shifts/{preview_id}/confirm")
def confirm_shift(
    publication_id: str,
    preview_id: str,
    value: ShiftConfirmIn,
    service: PlanProductApiService = Depends(get_plan_product_api_service),
) -> dict[str, Any]:
    try:
        return service.confirm_shift(publication_id, preview_id, value)
    except (PlanProductError, PlanShiftError, ValueError) as exc:
        _raise_api_error(exc)


@router.post("/published/{publication_id}/regenerate")
def regenerate_plan(
    publication_id: str,
    value: RegeneratePlanIn,
    service: PlanProductApiService = Depends(get_plan_product_api_service),
) -> dict[str, Any]:
    try:
        return service.regenerate(publication_id, value)
    except (PlanProductError, PlanShiftError, ValueError) as exc:
        _raise_api_error(exc)


def create_plan_product_api_app(
    service: PlanProductApiService | None = None,
) -> FastAPI:
    standalone = FastAPI(title="MentorFlow Plan Product API", version="1.0")
    if service is not None:
        standalone.dependency_overrides[get_plan_product_api_service] = lambda: service
    standalone.include_router(router)
    return standalone


app = create_plan_product_api_app()


__all__ = [
    "DELIVERY_ID",
    "EXPIRED_PLAN_MESSAGE",
    "PublishPlanIn",
    "PlanPreviewCreateIn",
    "PlanProductApiService",
    "PlanTaskIn",
    "ShiftPreviewIn",
    "app",
    "create_plan_product_api_app",
    "demo_plan_request",
    "get_plan_product_api_service",
    "router",
]
