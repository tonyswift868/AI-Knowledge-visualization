from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.review_product_v1 import ReviewProductService, create_review_product_app
from app.review_product_v1.demo_fixture import QUESTION_BY_ID


def _client(service: ReviewProductService | None = None) -> TestClient:
    return TestClient(create_review_product_app(service))


def test_standalone_api_exposes_every_review_application_operation() -> None:
    paths = set(create_review_product_app().openapi()["paths"])
    assert {
        "/api/review/campaigns",
        "/api/review/campaigns/{campaign_id}",
        "/api/review/campaigns/{campaign_id}/rounds/start",
        "/api/review/campaigns/{campaign_id}/questions/next",
        "/api/review/campaigns/{campaign_id}/answers",
        "/api/review/campaigns/{campaign_id}/hints",
        "/api/review/campaigns/{campaign_id}/explanations",
        "/api/review/campaigns/{campaign_id}/pause",
        "/api/review/campaigns/{campaign_id}/resume",
        "/api/review/campaigns/{campaign_id}/finish",
    } <= paths


def test_create_and_start_return_privacy_safe_demo_projection() -> None:
    with _client() as client:
        created = client.post("/api/review/campaigns", json={"mode": "demo"})
        assert created.status_code == 201
        campaign = created.json()
        assert campaign["fixture"] == {
            "concept_count": 10,
            "question_count": 20,
            "round_count": 3,
        }
        assert campaign["cost"]["provider_calls"] == 0
        started = client.post(
            f"/api/review/campaigns/{campaign['campaign_id']}/rounds/start"
        )
        assert started.status_code == 200
        payload = started.json()
        assert payload["round"]["display"] == "第 1 / 3 轮"
        assert payload["round"]["fsrs_controls_round_count"] is False
        assert payload["question"] is not None
        assert {
            "rubric",
            "accepted_answers",
            "evidence",
            "expected_answer",
            "explanation",
        }.isdisjoint(payload["question"])
        assert "accepted_answers" not in json.dumps(payload, ensure_ascii=False)


def test_hint_is_local_and_submission_reports_hint_dependent_ux() -> None:
    with _client() as client:
        campaign = client.post("/api/review/campaigns", json={}).json()
        path = f"/api/review/campaigns/{campaign['campaign_id']}"
        started = client.post(f"{path}/rounds/start").json()
        item_id = started["question"]["item_id"]
        hint = client.post(f"{path}/hints").json()
        assert hint["hint"]["provider_calls"] == 0
        assert hint["question"]["active_hint"] == hint["hint"]["text"]
        submitted = client.post(
            f"{path}/answers",
            json={"answer": QUESTION_BY_ID[item_id].canonical_answer},
        ).json()
        assert submitted["last_result"]["outcome"] == "hint_dependent"
        assert submitted["last_result"]["label"] == "依赖提示"
        assert submitted["last_result"]["expected_answer"]
        assert submitted["cost"]["provider_calls"] == 0


def test_outcome_ux_labels_cover_all_retrieval_results() -> None:
    from app.review_product_v1.service import OUTCOME_UX

    assert {item.value: value[0] for item, value in OUTCOME_UX.items()} == {
        "recalled": "答对",
        "recalled_with_difficulty": "比较困难但答出",
        "partial": "部分正确",
        "failed": "错误",
        "hint_dependent": "依赖提示",
    }


def test_weak_item_rolls_back_with_explicit_product_message() -> None:
    async def scenario():
        service = ReviewProductService()
        campaign = service.create_demo_campaign()
        campaign_id = campaign["campaign_id"]
        view = await service.start_round(campaign_id)
        first_item = view["question"]["item_id"]
        for ordinal in range(10):
            item_id = view["question"]["item_id"]
            answer = "故意错误" if ordinal == 0 else QUESTION_BY_ID[item_id].canonical_answer
            view = await service.submit_answer(campaign_id, answer=answer)
            if ordinal < 9:
                view = await service.next_question(campaign_id)
        assert view["question"]["item_id"] == first_item
        assert view["question"]["weak_repeat"] is True
        assert view["question"]["repeat_message"] == "这个知识点刚才掌握不稳，再确认一次。"
        assert view["metrics"]["weak_count"] == 1
        return service, campaign_id, view

    asyncio.run(scenario())


def test_pass_uses_retrieval_and_critical_gates_not_question_seen_count() -> None:
    async def scenario():
        service = ReviewProductService()
        campaign_id = service.create_demo_campaign()["campaign_id"]
        view = await service.start_round(campaign_id)
        first_item = view["question"]["item_id"]
        for ordinal in range(10):
            item_id = view["question"]["item_id"]
            answer = "故意错误" if ordinal == 0 else QUESTION_BY_ID[item_id].canonical_answer
            view = await service.submit_answer(campaign_id, answer=answer)
            if ordinal < 9:
                view = await service.next_question(campaign_id)
        view = await service.next_question(campaign_id)
        assert view["question"]["item_id"] == first_item
        view = await service.submit_answer(
            campaign_id,
            answer=QUESTION_BY_ID[first_item].canonical_answer,
        )
        assert view["state"] == "round_evaluated"
        view = await service.finish(campaign_id)
        assert view["round_result"]["passed"] is False
        assert "required_items" in view["round_result"]["failed_gates"]
        assert view["round_result"]["remaining_weak_items"] == 1

    asyncio.run(scenario())


def test_three_fixed_rounds_complete_offline_with_zero_provider_calls() -> None:
    async def scenario():
        service = ReviewProductService()
        campaign_id = service.create_demo_campaign()["campaign_id"]
        view = None
        for round_number in range(1, 4):
            view = await service.start_round(campaign_id)
            assert view["round"]["display"] == f"第 {round_number} / 3 轮"
            while view["state"] == "round_active":
                item_id = view["question"]["item_id"]
                view = await service.submit_answer(
                    campaign_id,
                    answer=QUESTION_BY_ID[item_id].canonical_answer,
                )
                if view["state"] == "round_active":
                    view = await service.next_question(campaign_id)
            view = await service.finish(campaign_id)
        assert view is not None
        assert view["state"] == "completed"
        assert view["campaign_result"]["completed_rounds"] == 3
        assert view["campaign_result"]["passed_rounds"] == 3
        assert view["campaign_result"]["all_rounds_passed"] is True
        assert view["campaign_result"]["remaining_weak_items"] == 0
        assert view["cost"]["provider_calls"] == 0
        assert view["round"]["fixed_total"] == 3
        record = service._record(campaign_id)
        assert len(record.adapters.question_bank.calls) == 3
        assert record.adapters.review_memory.read_count == 3
        assert record.adapters.review_memory.write_count == 3
        assert record.adapters.student_progress.load_count == 3
        assert record.adapters.budget.requests == []

    asyncio.run(scenario())


def test_pause_internal_recovery_fixture_restores_question_and_hint() -> None:
    async def scenario():
        service = ReviewProductService()
        campaign_id = service.create_demo_campaign()["campaign_id"]
        started = await service.start_round(campaign_id)
        item_id = started["question"]["item_id"]
        hinted = await service.request_hint(campaign_id)
        await service.pause(campaign_id)
        fixture = service.export_recovery_fixture(campaign_id)

        recovered_service = ReviewProductService()
        recovered = recovered_service.restore_recovery_fixture(fixture)
        assert recovered["state"] == "paused"
        resumed = await recovered_service.resume(campaign_id)
        assert resumed["question"]["item_id"] == item_id
        assert resumed["question"]["active_hint"] == hinted["hint"]["text"]

    asyncio.run(scenario())


def test_teacher_path_is_not_touched_by_ordinary_review_turns() -> None:
    async def scenario():
        service = ReviewProductService()
        campaign_id = service.create_demo_campaign()["campaign_id"]
        view = await service.start_round(campaign_id)
        item_id = view["question"]["item_id"]
        await service.submit_answer(
            campaign_id,
            answer=QUESTION_BY_ID[item_id].canonical_answer,
        )
        record = service._record(campaign_id)
        assert record.adapters.budget.requests == []
        await service.next_question(campaign_id)
        explicit = await service.request_explanation(campaign_id, "为什么？")
        assert explicit["explanation"]["explicit_request"] is True
        assert explicit["explanation"]["provider_calls"] == 0

    asyncio.run(scenario())


def test_scope_fence_keeps_central_router_untouched() -> None:
    root = Path(__file__).resolve().parents[2]
    central = (root / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert "review_product_v1" not in central
    fence = json.loads(
        (root / "artifacts" / "review_product_v1" / "scope_fence.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads(
        (root / "artifacts" / "review_product_v1" / "integration_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert "backend/app/main.py" in fence["forbidden_paths"]
    assert manifest["integration_policy"]["central_router_modified"] is False
    assert manifest["offline_demo"]["provider_calls_expected"] == 0
