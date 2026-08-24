from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app import generic_knowledge_api as planner


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        id="candidate-hardening-session",
        provider="openai_compatible",
        model="deepseek-chat",
        base_url="https://provider.synthetic.example/v1",
        temperature=0.2,
    )


def _provider_result() -> dict[str, Any]:
    return {
        "reply": "已整理目标。",
        "goal_summary": "掌握一个可验证学习目标",
        "known_facts": {
            "objective": "掌握一个可验证学习目标",
            "purpose": "",
            "timeframe": "",
            "named_materials": [],
            "mastery_criteria": "能够独立完成一次验证",
            "practice_requirements": [],
        },
        "missing_questions": [],
        "assessment_intent": "independent_performance",
        "resource_search_authorized": False,
        "search_queries": [],
        "next_step": "ask_for_material",
    }


def _pool_note(topic: str, index: int) -> dict[str, Any]:
    return {
        "url": f"https://hardening.example/{index}",
        "title": f"{topic} structured lecture notes {index}",
        "snippet": "Chapter outline, definitions, derivations, worked examples, formulas and exercises.",
        "media_kind": "document",
        "query": f"{topic} comprehensive lecture notes",
        "comprehensive": True,
        "sections": ["Foundations", "Applications", "Exercises"],
        "planning_batch_contract": "complete-page-v1",
    }


@pytest.mark.parametrize(
    ("selected_count", "target_count"),
    [(3, 3), (4, 6)],
)
def test_add_page_target_must_extend_selection_by_one_page_before_provider(
    monkeypatch,
    selected_count: int,
    target_count: int,
):
    provider_calls: list[str] = []

    monkeypatch.setattr(
        planner,
        "_v1_provider_session",
        lambda *_args: provider_calls.append("provider")
        or (_ for _ in ()).throw(AssertionError("provider called")),
    )
    monkeypatch.setattr(
        planner,
        "search_web",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("search called")),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            planner.planning_assistant(
                "structured-add-geometry-invalid",
                object(),
                {
                    "message": "expand candidate pool",
                    "candidate_discovery_mode": "add_page",
                    "selected_material_count": selected_count,
                    "candidate_target_count": target_count,
                    "page_size": 3,
                },
            )
        )

    assert raised.value.status_code == 422
    assert raised.value.detail["error"]["code"] == "PLANNING_CANDIDATE_TARGET_INVALID"
    assert provider_calls == []


@pytest.mark.parametrize(
    ("mode", "mode_fields", "round_value"),
    [
        ("fill_page", {"candidate_target_count": 3}, None),
        ("fill_page", {"candidate_target_count": 3}, ""),
        ("add_page", {"selected_material_count": 3, "candidate_target_count": 6}, None),
        ("add_page", {"selected_material_count": 3, "candidate_target_count": 6}, ""),
        ("add_page", {"selected_material_count": 3, "candidate_target_count": 6}, "2"),
        ("initial", {}, None),
    ],
)
def test_structured_rounds_never_honor_stale_provider_cache_ref(
    monkeypatch,
    mode: str,
    mode_fields: dict[str, Any],
    round_value: str | None,
):
    session = _session()
    stale_ref = "a" * 64
    captured: list[str] = []

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_args: session)
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: object())
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])

    async def fake_provider_result(**kwargs: Any):
        captured.append(str(kwargs["preferred_cache_key"]))
        return _provider_result(), False, False, "fresh-provider-ref"

    monkeypatch.setattr(planner, "_planning_provider_result", fake_provider_result)

    body: dict[str, Any] = {
        "message": "organize a learning goal",
        "candidate_discovery_mode": mode,
        "provider_cache_ref": stale_ref,
        **mode_fields,
    }
    if round_value is not None:
        body["candidate_search_round"] = round_value

    asyncio.run(planner.planning_assistant("structured-cache-hardening", object(), body))

    assert captured == ([stale_ref] if mode == "initial" else [""])


def test_fill_page_noop_projects_pool_selection_and_expected_revision(
    client,
    monkeypatch,
):
    workspace_id = "fill-noop-selection-projection"
    pool = planner._PLANNING_RESOURCE_POOL
    created = pool.upsert(
        workspace_id,
        [_pool_note("No-op selection", index) for index in range(3)],
        page_size=3,
    )
    rows = pool.candidates(workspace_id, created["planning_session_id"])
    first_id = rows[0]["candidate_id"]
    second_id = rows[1]["candidate_id"]
    anchored = pool.decide(
        workspace_id,
        planning_session_id=created["planning_session_id"],
        action="set_anchor",
        anchor_candidate_id=first_id,
        expected_revision=created["revision"],
    )
    current = pool.select(
        workspace_id,
        planning_session_id=created["planning_session_id"],
        candidate_ids=second_id,
        expected_revision=anchored["revision"],
    )
    provider_calls: list[str] = []
    monkeypatch.setattr(
        planner,
        "_v1_provider_session",
        lambda *_args: provider_calls.append("provider") or (_ for _ in ()).throw(AssertionError("provider called")),
    )
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "message": "show the current candidate page",
            "planning_session_id": created["planning_session_id"],
            "expected_revision": current["revision"],
            "candidate_discovery_mode": "fill_page",
            "candidate_target_count": 3,
            "page_size": 3,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    bundle = payload["resource_bundle"]
    assert provider_calls == []
    assert set(bundle["retained_candidate_ids"]) == {first_id, second_id}
    assert bundle["merge"]["retained_input_count"] == 2
    assert bundle["merge"]["fresh_input_count"] == 1
    assert payload["selection"]["anchor"] == first_id
    assert second_id in payload["selection"]["support"]
    assert payload["candidate_page"]["anchor_candidate_id"] == first_id
    assert second_id in payload["candidate_page"]["supporting_candidate_ids"]


@pytest.mark.parametrize("revision_field", ["revision", "expected_revision"])
def test_fill_page_noop_rejects_stale_revision_without_provider_or_mutation(
    client,
    monkeypatch,
    revision_field: str,
):
    workspace_id = f"fill-noop-stale-{revision_field}"
    pool = planner._PLANNING_RESOURCE_POOL
    created = pool.upsert(
        workspace_id,
        [_pool_note("No-op stale", index) for index in range(3)],
        page_size=3,
    )
    session_id = created["planning_session_id"]
    current = pool.select(
        workspace_id,
        planning_session_id=session_id,
        candidate_ids=pool.candidates(workspace_id, session_id)[0]["candidate_id"],
        expected_revision=created["revision"],
    )
    before_page = pool.page(workspace_id, session_id)
    before_rows = pool.candidates(workspace_id, session_id, include_inactive=True)
    provider_calls: list[str] = []
    monkeypatch.setattr(
        planner,
        "_v1_provider_session",
        lambda *_args: provider_calls.append("provider") or (_ for _ in ()).throw(AssertionError("provider called")),
    )
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "message": "show the current candidate page",
            "planning_session_id": session_id,
            revision_field: created["revision"],
            "candidate_discovery_mode": "fill_page",
            "candidate_target_count": 3,
            "page_size": 3,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "STALE_REVISION"
    assert provider_calls == []
    assert pool.page(workspace_id, session_id)["revision"] == before_page["revision"] == current["revision"]
    assert pool.candidates(workspace_id, session_id, include_inactive=True) == before_rows
