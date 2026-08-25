from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.planning.product_runtime import (
    DELIVERY_ID,
    PLAN_CREATION_PROVIDER_CALLS,
    PUBLISH_PROVIDER_CALLS,
    REGENERATE_MAY_REOPTIMIZE_KNOWLEDGE_ORDER,
    REGENERATE_PROVIDER_CALLS,
    SHIFT_CP_SAT_CALLS,
    SHIFT_PROVIDER_CALLS,
    SHIFT_REOPTIMIZES_KNOWLEDGE_ORDER,
    PlanCreationMode,
    PlanCreationRequest,
    PlanCreationService,
    PlanOperationKind,
    PlanPreviewDTO,
    PlanPreviewStatus,
    PlanProductError,
    PlanProductRuntime,
    PlanPublishConfirmationRequest,
    PlanRegenerationRequest,
    PublishConfirmationRequiredError,
    PublishedPlan,
)
from app.planning.review_effort import ReviewRoundConfig, ReviewScheduleBridge
from app.planning.runtime_v2 import (
    AUTO_CP_SAT_ON_MISS,
    AUTO_PROVIDER_ON_MISS,
    AUTO_REPLAN,
    AUTO_SHIFT_ON_MISS,
    PlanRuntimeStatus,
    PlanShiftRequest,
    ScheduledTaskStatus,
    ShiftNotConfirmedError,
    Weekday,
    WeeklyLearningPreferences,
)
from app.planning.scheduling import AllocationKind, ORToolsScheduleSolver
from plan_product_runtime_fixtures import standard_creation_request


def publish_standard(
    runtime: PlanProductRuntime,
    *,
    request: PlanCreationRequest | None = None,
) -> tuple[PlanPreviewDTO, PublishedPlan]:
    effective = request or standard_creation_request()
    preview = runtime.create_preview(effective)
    runtime.confirm_publication(
        PlanPublishConfirmationRequest(
            preview_id=preview.preview_id,
            confirmation_id=f"confirm-{effective.request_id}",
            explicitly_confirmed_by_user=True,
        )
    )
    published = runtime.publish(
        preview.preview_id,
        published_on=effective.start_date,
    )
    return preview, published


def test_creation_preview_assembles_full_effort_and_real_daily_load() -> None:
    request = standard_creation_request(rounds=3)
    original_payload = request.model_dump_json()

    preview = PlanProductRuntime().create_preview(request)

    assert preview.status is PlanPreviewStatus.READY_FOR_CONFIRMATION
    assert [item.round_id for item in preview.review_rounds] == [
        "REVIEW_ROUND_1",
        "REVIEW_ROUND_2",
        "REVIEW_ROUND_3",
    ]
    assert preview.effort.total == (
        preview.effort.initial_learning_effort + preview.effort.review_total
    )
    assert (
        preview.effort.total.low_minutes
        <= preview.effort.total.expected_minutes
        <= preview.effort.total.high_minutes
    )
    assert preview.total_hours == round(
        preview.effort.total.expected_minutes / 60,
        2,
    )
    assert sum(item.task_minutes for item in preview.daily_load) == (
        preview.initial_learning.expected_minutes
    )
    assert sum(item.review_minutes for item in preview.daily_load) == (
        preview.review_total.expected_minutes
    )
    assert sum(item.buffer_minutes for item in preview.daily_load) == 15
    assert all(item.total_minutes <= item.capacity_minutes for item in preview.daily_load)
    assert preview.finish_date is not None
    assert preview.buffers[0].buffer_id == "final-confidence-buffer"
    assert preview.buffers[0].scheduled_dates == (date(2026, 9, 4),)
    assert preview.cost_shape.provider_calls == 0
    assert request.model_dump_json() == original_payload


def test_multiple_product_buffers_are_atomically_reserved_and_visible() -> None:
    base = standard_creation_request()
    second_buffer = base.buffers[0].model_copy(
        update={
            "buffer_id": "final-retry-buffer",
            "label": "Final retry buffer",
            "minutes": 5,
        }
    )
    request = PlanCreationRequest.model_validate(
        {
            **base.model_dump(mode="python"),
            "request_id": "multiple-buffers",
            "buffers": (
                base.buffers[0].model_copy(update={"minutes": 10}),
                second_buffer,
            ),
        }
    )

    preview = PlanProductRuntime().create_preview(request)

    assert [item.buffer_id for item in preview.buffers] == [
        "final-confidence-buffer",
        "final-retry-buffer",
    ]
    assert preview.buffer_total_minutes == 15
    assert sum(item.buffer_minutes for item in preview.daily_load) == 15
    assert all(item.scheduled_dates == (date(2026, 9, 4),) for item in preview.buffers)


def test_preview_is_ui_serializable_and_zero_rounds_are_exact() -> None:
    request = standard_creation_request(rounds=0)
    request = request.model_copy(
        update={
            "request_id": "zero-round-preview",
            "review_items": (),
            "review_rounds": ReviewRoundConfig(selected_rounds=0),
        }
    )

    preview = PlanProductRuntime().create_preview(request)
    restored = PlanPreviewDTO.model_validate_json(preview.model_dump_json())

    assert restored == preview
    assert restored.review_rounds == ()
    assert restored.review_total.expected_minutes == 0
    assert "no_review_rounds_selected" in restored.warnings


def test_publish_requires_explicit_confirmation_and_is_idempotent() -> None:
    runtime = PlanProductRuntime()
    request = standard_creation_request()
    preview = runtime.create_preview(request)

    with pytest.raises(PublishConfirmationRequiredError):
        runtime.publish(preview.preview_id, published_on=request.start_date)

    confirmation_request = PlanPublishConfirmationRequest(
        preview_id=preview.preview_id,
        confirmation_id="explicit-confirmation",
        explicitly_confirmed_by_user=True,
    )
    confirmation = runtime.confirm_publication(confirmation_request)
    duplicate_confirmation = runtime.confirm_publication(
        confirmation_request.model_copy(update={"confirmation_id": "duplicate"})
    )
    published = runtime.publish(
        preview.preview_id,
        published_on=request.start_date,
    )
    duplicate_publish = runtime.publish(
        preview.preview_id,
        published_on=request.deadline,
    )

    assert duplicate_confirmation == confirmation
    assert duplicate_publish == published
    assert published.publication_version == 1
    assert published.immutable is True
    assert runtime.get_published(published.publication_id) == published
    with pytest.raises(ValidationError):
        published.plan_id = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PlanPublishConfirmationRequest(
            preview_id=preview.preview_id,
            confirmation_id="not-explicit",
            explicitly_confirmed_by_user=False,
        )


def test_published_schedule_keeps_learning_review_and_round_order() -> None:
    runtime = PlanProductRuntime()
    _, published = publish_standard(runtime)
    allocations = published.published_schedule.allocations
    positions: dict[str, list[tuple[int, int]]] = {}
    for allocation in allocations:
        target_id = allocation.task_id or allocation.reservation_id
        if target_id is not None:
            positions.setdefault(target_id, []).append(
                (allocation.day.toordinal(), allocation.order_in_day)
            )

    assert max(positions["foundation"]) < min(positions["REVIEW_ROUND_1"])
    assert max(positions["application"]) < min(positions["REVIEW_ROUND_1"])
    assert max(positions["mastery"]) < min(positions["REVIEW_ROUND_1"])
    assert max(positions["REVIEW_ROUND_1"]) < min(positions["REVIEW_ROUND_2"])
    assert max(positions["REVIEW_ROUND_2"]) < min(positions["REVIEW_ROUND_3"])


def test_published_missed_day_projects_overdue_without_solver_or_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = PlanProductRuntime()
    request = standard_creation_request()
    _, published = publish_standard(runtime, request=request)
    immutable_schedule = published.published_schedule

    from ortools.sat.python import cp_model

    def forbidden_solve(*args: object, **kwargs: object) -> object:
        raise AssertionError("ordinary published progress must not call CP-SAT")

    monkeypatch.setattr(cp_model.CpSolver, "Solve", forbidden_solve)
    first_day = min(
        item.day
        for item in immutable_schedule.allocations
        if item.kind is not AllocationKind.FINAL_BUFFER
    )
    first_snapshot = runtime.snapshot(
        published.publication_id,
        today=first_day,
    )
    assert first_snapshot.status is PlanRuntimeStatus.ACTIVE
    assert first_snapshot.due_today
    today = request.deadline + timedelta(days=1)
    snapshot = runtime.snapshot(published.publication_id, today=today)
    ledger = runtime.overdue_ledger(published.publication_id, today=today)

    assert snapshot.status is PlanRuntimeStatus.EXPIRED_WITH_OVERDUE
    assert all(
        item.status is ScheduledTaskStatus.OVERDUE for item in snapshot.tasks
    )
    assert {item.task_id for item in snapshot.tasks if item.allocation_kind is AllocationKind.REVIEW} == {
        "REVIEW_ROUND_1",
        "REVIEW_ROUND_2",
        "REVIEW_ROUND_3",
    }
    assert ledger.active_minutes > 0
    assert runtime.get_published(published.publication_id).published_schedule == (
        immutable_schedule
    )
    assert AUTO_REPLAN is False
    assert AUTO_CP_SAT_ON_MISS is False
    assert AUTO_PROVIDER_ON_MISS is False
    assert AUTO_SHIFT_ON_MISS is False


def test_shift_only_moves_dates_after_explicit_confirm_and_never_runs_cp_sat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = PlanProductRuntime()
    _, published = publish_standard(runtime)
    immutable_published = published.published_schedule
    before = runtime.current_runtime_schedule(published.publication_id)

    from ortools.sat.python import cp_model

    def forbidden_solve(*args: object, **kwargs: object) -> object:
        raise AssertionError("SHIFT must not call CP-SAT")

    monkeypatch.setattr(cp_model.CpSolver, "Solve", forbidden_solve)
    preferences = WeeklyLearningPreferences(
        allowed_weekdays=frozenset(
            (
                Weekday.MONDAY,
                Weekday.TUESDAY,
                Weekday.WEDNESDAY,
                Weekday.THURSDAY,
                Weekday.FRIDAY,
            )
        )
    )
    shift_preview, receipt = runtime.create_shift_preview(
        published.publication_id,
        PlanShiftRequest(
            request_id="product-explicit-shift",
            plan_id=published.plan_id,
            expected_schedule_version=1,
            calendar_days_delta=1,
            weekly_preferences=preferences,
        ),
    )

    assert receipt.operation is PlanOperationKind.SHIFT
    assert receipt.cp_sat_allowed is False
    assert receipt.cp_sat_calls == 0
    assert receipt.provider_calls == 0
    assert receipt.knowledge_order_reoptimization_allowed is False
    assert receipt.knowledge_order_reoptimized is False
    assert all(preferences.allows(item.new_date) for item in shift_preview.moves)
    with pytest.raises(ShiftNotConfirmedError):
        runtime.apply_shift(published.publication_id, shift_preview.preview_id)
    runtime.confirm_shift(
        published.publication_id,
        shift_preview.preview_id,
        confirmation_id="confirm-product-shift",
    )
    runtime.apply_shift(published.publication_id, shift_preview.preview_id)
    after = runtime.current_runtime_schedule(published.publication_id)

    identity = lambda value: (
        value.kind,
        value.task_id,
        value.reservation_id,
        value.minutes,
        value.chunk_index,
        value.chunk_count,
    )
    assert {
        item.allocation_id: identity(item) for item in before.allocations
    } == {
        item.allocation_id: identity(item) for item in after.allocations
    }
    assert after.version == 2
    assert runtime.get_published(published.publication_id).published_schedule == (
        immutable_published
    )


class CountingSolver:
    def __init__(self) -> None:
        self.calls = 0
        self._delegate = ORToolsScheduleSolver()

    def solve(self, problem: object, *, config: object = None) -> object:
        self.calls += 1
        return self._delegate.solve(problem, config=config)


def test_regenerate_requires_explicit_user_mode_and_is_a_new_publication() -> None:
    solver = CountingSolver()
    creation = PlanCreationService(
        review_bridge=ReviewScheduleBridge(solver=solver),  # type: ignore[arg-type]
    )
    runtime = PlanProductRuntime(creation_service=creation)
    original_request = standard_creation_request()
    _, original = publish_standard(runtime, request=original_request)
    original_payload = original.model_dump_json()
    assert solver.calls == 1

    regeneration_creation = standard_creation_request(
        request_id="explicit-regeneration-preview",
        plan_id=original.plan_id,
        rounds=1,
        creation_mode=PlanCreationMode.USER_REQUESTED_REGENERATION,
    )
    preview, receipt = runtime.regenerate(
        PlanRegenerationRequest(
            source_publication_id=original.publication_id,
            explicitly_requested_by_user=True,
            creation_request=regeneration_creation,
        )
    )

    assert solver.calls == 2
    assert receipt.operation is PlanOperationKind.REGENERATE
    assert receipt.cp_sat_allowed is True
    assert receipt.cp_sat_calls == 1
    assert receipt.provider_calls == 0
    assert receipt.knowledge_order_reoptimization_allowed is True
    assert receipt.knowledge_order_reoptimized is False
    assert runtime.get_published(original.publication_id).model_dump_json() == (
        original_payload
    )
    runtime.confirm_publication(
        PlanPublishConfirmationRequest(
            preview_id=preview.preview_id,
            confirmation_id="confirm-regenerated-plan",
            explicitly_confirmed_by_user=True,
        )
    )
    regenerated = runtime.publish(
        preview.preview_id,
        published_on=regeneration_creation.start_date,
    )
    assert regenerated.publication_version == 2
    assert original.publication_version == 1

    with pytest.raises(PlanProductError, match="explicit regenerate"):
        runtime.create_preview(
            standard_creation_request(
                request_id="regeneration-bypass",
                plan_id=original.plan_id,
                creation_mode=PlanCreationMode.USER_REQUESTED_REGENERATION,
            )
        )

    with pytest.raises(ValidationError):
        PlanRegenerationRequest(
            source_publication_id=original.publication_id,
            explicitly_requested_by_user=False,
            creation_request=regeneration_creation,
        )
    with pytest.raises(ValidationError, match="regeneration requires"):
        PlanRegenerationRequest(
            source_publication_id=original.publication_id,
            explicitly_requested_by_user=True,
            creation_request=standard_creation_request(
                request_id="wrong-regeneration-mode",
                plan_id=original.plan_id,
            ),
        )


def test_creation_request_idempotency_rejects_reuse_with_different_input() -> None:
    runtime = PlanProductRuntime()
    request = standard_creation_request()
    first = runtime.create_preview(request)
    duplicate = runtime.create_preview(request)

    assert duplicate == first
    with pytest.raises(PlanProductError, match="request_id was reused"):
        runtime.create_preview(
            request.model_copy(
                update={"review_rounds": ReviewRoundConfig(selected_rounds=1)}
            )
        )


def test_policy_cost_constants_keep_provider_out_of_plan_runtime() -> None:
    assert DELIVERY_ID == "mentorflow-w6-07-plan-product-runtime-v1"
    assert PLAN_CREATION_PROVIDER_CALLS == 0
    assert PUBLISH_PROVIDER_CALLS == 0
    assert SHIFT_CP_SAT_CALLS == 0
    assert SHIFT_PROVIDER_CALLS == 0
    assert REGENERATE_PROVIDER_CALLS == 0
    assert SHIFT_REOPTIMIZES_KNOWLEDGE_ORDER is False
    assert REGENERATE_MAY_REOPTIMIZE_KNOWLEDGE_ORDER is True
