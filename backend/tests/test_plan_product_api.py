from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient

from app.plan_product_api import (
    DELIVERY_ID,
    EXPIRED_PLAN_MESSAGE,
    PlanPreviewCreateIn,
    PlanProductApiService,
    PlanTaskIn,
    PublishPlanIn,
    ShiftPreviewIn,
    create_plan_product_api_app,
    demo_plan_request,
)
from app.planning.runtime_v2 import Weekday


ROOT = Path(__file__).resolve().parents[2]
MAIN_SHA256 = "432ddf17ca1adac7dafa18421f1fc17ddb9857f619c38997af67001bdf256a8e"


def creation_payload(
    *,
    request_id: str = "api-functional-preview",
    rounds: int = 3,
) -> dict:
    start = date(2026, 9, 1)
    return PlanPreviewCreateIn(
        request_id=request_id,
        plan_id="api-functional-plan",
        start_date=start,
        deadline=start + timedelta(days=30),
        daily_minutes=30,
        weekly_learning_days=tuple(Weekday),
        review_rounds=rounds,
        buffer_minutes=15,
        tasks=(
            PlanTaskIn(task_id="foundation", title="Foundation", minutes=20),
            PlanTaskIn(task_id="application", title="Application", minutes=20),
            PlanTaskIn(task_id="assessment", title="Assessment", minutes=20),
        ),
    ).model_dump(mode="json")


def test_standalone_api_preview_publish_daily_shift_and_regenerate() -> None:
    service = PlanProductApiService()
    app = create_plan_product_api_app(service)
    with TestClient(app) as client:
        preview_response = client.post(
            "/api/plan-product/previews",
            json=creation_payload(),
        )
        assert preview_response.status_code == 200
        preview = preview_response.json()
        assert preview["delivery_id"] == DELIVERY_ID
        assert preview["preview"]["status"] == "ready_for_confirmation"
        assert [item["round_index"] for item in preview["preview"]["review_rounds"]] == [1, 2, 3]
        assert preview["preview"]["review_total"]["expected_minutes"] > 0
        assert preview["preview"]["effort"]["total"]["expected_minutes"] == (
            preview["preview"]["initial_learning"]["expected_minutes"]
            + preview["preview"]["review_total"]["expected_minutes"]
        )
        labels = {
            task["label"]
            for day in preview["schedule"]["days"]
            for task in day["tasks"]
        }
        assert "Initial Learning" in labels
        assert {"Review Round 1", "Review Round 2", "Review Round 3"}.issubset(labels)
        assert "Buffer" in labels

        preview_id = preview["preview"]["preview_id"]
        publish_response = client.post(
            f"/api/plan-product/previews/{preview_id}/publish",
            json={
                "confirmation_id": "explicit-publish",
                "explicitly_confirmed_by_user": True,
                "published_on": "2026-09-01",
            },
        )
        assert publish_response.status_code == 200
        published = publish_response.json()
        assert published["published"]["immutable"] is True
        publication_id = published["published"]["publication_id"]
        original_published_schedule = service.runtime.get_published(
            publication_id
        ).published_schedule.model_dump_json()

        schedule_days = published["current_schedule"]["days"]
        second_day = schedule_days[1]["date"]
        daily = client.get(
            f"/api/plan-product/published/{publication_id}/daily",
            params={"today": second_day},
        ).json()
        assert daily["groups"]["OVERDUE"]
        assert daily["groups"]["DUE TODAY"]
        assert daily["automatic_shift_applied"] is False
        due_id = daily["groups"]["DUE TODAY"][0]["task_id"]
        completed = client.post(
            f"/api/plan-product/published/{publication_id}/tasks/{due_id}/complete",
            json={
                "explicitly_completed_by_user": True,
                "completed_on": second_day,
            },
        )
        assert completed.status_code == 200
        refreshed = client.get(
            f"/api/plan-product/published/{publication_id}/daily",
            params={"today": second_day},
        ).json()
        assert due_id in {item["task_id"] for item in refreshed["groups"]["COMPLETED"]}

        expired = client.get(
            f"/api/plan-product/published/{publication_id}/daily",
            params={"today": "2026-11-01"},
        ).json()
        assert expired["snapshot"]["status"] == "expired_with_overdue"
        assert expired["expired_message"] == EXPIRED_PLAN_MESSAGE

        shift_response = client.post(
            f"/api/plan-product/published/{publication_id}/shifts/preview",
            json={
                "request_id": "api-shift-preview",
                "calendar_days_delta": 2,
                "weekly_learning_days": [item.value for item in Weekday],
                "unavailable_dates": [],
            },
        )
        assert shift_response.status_code == 200
        shift = shift_response.json()
        assert shift["receipt"]["cp_sat_calls"] == 0
        assert shift["receipt"]["cp_sat_allowed"] is False
        assert shift["shift_preview"]["tasks_moved"] > 0
        shift_preview_id = shift["shift_preview"]["preview_id"]
        confirm_response = client.post(
            f"/api/plan-product/published/{publication_id}/shifts/{shift_preview_id}/confirm",
            json={
                "confirmation_id": "explicit-shift-confirm",
                "explicitly_confirmed_by_user": True,
            },
        )
        assert confirm_response.status_code == 200
        assert confirm_response.json()["application"]["status"] == "applied"
        assert service.runtime.get_published(publication_id).published_schedule.model_dump_json() == original_published_schedule

        regeneration = creation_payload(
            request_id="api-explicit-regeneration",
            rounds=1,
        )
        regenerated_response = client.post(
            f"/api/plan-product/published/{publication_id}/regenerate",
            json={
                "explicitly_requested_by_user": True,
                "creation": regeneration,
            },
        )
        assert regenerated_response.status_code == 200
        regenerated = regenerated_response.json()
        assert regenerated["receipt"]["operation"] == "regenerate"
        assert regenerated["receipt"]["cp_sat_allowed"] is True
        assert regenerated["receipt"]["cp_sat_calls"] == 1
        assert len(regenerated["preview"]["review_rounds"]) == 1
        assert service.runtime.get_published(publication_id).published_schedule.model_dump_json() == original_published_schedule


def test_infeasibility_exposes_capacity_review_weekday_deadline_and_unsplittable_reasons() -> None:
    start = date(2026, 9, 7)
    value = PlanPreviewCreateIn(
        request_id="api-infeasible",
        plan_id="api-infeasible-plan",
        start_date=start,
        deadline=start,
        daily_minutes=30,
        weekly_learning_days=(Weekday.MONDAY,),
        review_rounds=3,
        buffer_minutes=0,
        tasks=(
            PlanTaskIn(
                task_id="atomic",
                title="Cannot split this task",
                minutes=90,
                unsplittable=True,
            ),
        ),
    )

    result = PlanProductApiService().create_preview(value)

    assert result["preview"]["status"] == "infeasible"
    assert result["schedule"] is None
    categories = {
        item["category"]
        for item in result["preview"]["feasibility"]["explanations"]
    }
    assert "not_enough_total_capacity" in categories
    assert "deadline_too_short" in categories
    assert "unsplittable_task" in categories


def test_large_material_demo_fixture_has_initial_learning_and_three_real_reviews() -> None:
    fixture = demo_plan_request(today=date(2026, 9, 1))

    assert len(fixture.tasks) == 36
    assert fixture.review_rounds == 3
    assert sum(item.minutes for item in fixture.tasks) >= 700
    result = PlanProductApiService().create_preview(fixture)
    assert result["preview"]["status"] == "ready_for_confirmation"
    assert result["preview"]["review_total"]["expected_minutes"] > 0
    assert result["preview"]["effort"]["total"]["expected_minutes"] > sum(
        item.minutes for item in fixture.tasks
    )


def test_1000_concept_api_preview_schedule_and_shift_performance() -> None:
    start = date(2026, 11, 2)
    value = PlanPreviewCreateIn(
        request_id="api-scale-1000",
        plan_id="api-scale-1000-plan",
        start_date=start,
        deadline=start + timedelta(days=6),
        daily_minutes=1_440,
        weekly_learning_days=(Weekday.MONDAY,),
        review_rounds=0,
        buffer_minutes=0,
        tasks=tuple(
            PlanTaskIn(
                task_id=f"concept-{index:04d}",
                title=f"Concept {index:04d}",
                minutes=1,
            )
            for index in range(1_000)
        ),
    )
    service = PlanProductApiService()
    started = perf_counter()
    preview = service.create_preview(value)
    preview_elapsed = perf_counter() - started
    assert preview["preview"]["status"] == "ready_for_confirmation"
    assert sum(len(day["tasks"]) for day in preview["schedule"]["days"]) == 1_000
    assert preview_elapsed < 20

    published = service.publish(
        preview["preview"]["preview_id"],
        PublishPlanIn(
            confirmation_id="scale-publish",
            explicitly_confirmed_by_user=True,
            published_on=start,
        ),
    )
    publication_id = published["published"]["publication_id"]
    started = perf_counter()
    shifted = service.shift_preview(
        publication_id,
        ShiftPreviewIn(
            request_id="scale-shift",
            calendar_days_delta=1,
            weekly_learning_days=tuple(Weekday),
        ),
    )
    shift_elapsed = perf_counter() - started
    assert shifted["shift_preview"]["allocations_moved"] == 1_000
    assert shifted["receipt"]["cp_sat_calls"] == 0
    assert shift_elapsed < 10


def test_integration_fence_keeps_central_router_untouched() -> None:
    main_path = ROOT / "backend" / "app" / "main.py"
    manifest = json.loads(
        (ROOT / "artifacts" / "live_demo_07_plan_product_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert sha256(main_path.read_bytes()).hexdigest() == MAIN_SHA256
    source = main_path.read_text(encoding="utf-8")
    assert "plan_product_api" not in source
    assert manifest["delivery_id"] == DELIVERY_ID
    assert manifest["backend"]["central_router_modified"] is False
    assert manifest["backend"]["central_router_sha256"] == MAIN_SHA256
    assert manifest["operation_fence"]["shift_cp_sat_calls"] == 0
    assert manifest["operation_fence"]["regenerate_cp_sat_allowed"] is True
    assert manifest["operation_fence"]["provider_calls"] == 0
