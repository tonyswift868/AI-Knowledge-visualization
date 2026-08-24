from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app import generic_knowledge_api as planner


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        id="candidate-expansion-session",
        provider="openai_compatible",
        model="deepseek-chat",
        base_url="https://provider.synthetic.example/v1",
        temperature=0.2,
    )


def test_structured_target_sizes_fresh_candidates_without_topic_branching():
    assert planner._planning_discovery_batch_size(3, page_size=3, target_count=6) == 3
    assert planner._planning_discovery_batch_size(4, page_size=3, target_count=9) == 5
    assert planner._planning_discovery_batch_size(2, page_size=3, target_count=3) == 1


def test_add_page_rejects_short_selection_before_provider_or_search(monkeypatch):
    provider_calls: list[str] = []

    monkeypatch.setattr(
        planner,
        "_v1_provider_session",
        lambda *_args: provider_calls.append("provider") or (_ for _ in ()).throw(AssertionError("provider called")),
    )
    monkeypatch.setattr(
        planner,
        "search_web",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("search called")),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            planner.planning_assistant(
                "structured-add-invalid",
                object(),
                {
                    "message": "expand candidate pool",
                    "candidate_discovery_mode": "add_page",
                    "selected_material_count": 2,
                    "candidate_target_count": 6,
                    "page_size": 3,
                },
            )
        )
    assert raised.value.status_code == 422
    assert raised.value.detail["error"]["code"] == "PLANNING_CANDIDATE_ADD_PAGE_SELECTION_REQUIRED"
    assert provider_calls == []


def test_candidate_round_changes_provider_identity_and_review_context(monkeypatch):
    session = _session()
    common: dict[str, Any] = {
        "message": "find complementary notes",
        "goal": "Learn a subject",
        "history": [],
        "search_authorized": True,
        "candidate_discovery_mode": "add_page",
        "candidate_target_count": 6,
        "selected_material_count": 3,
        "existing_candidate_summaries": [
            {
                "candidate_id": "old-1",
                "title": "Old notes",
                "url": "https://notes.example/old.pdf",
            }
        ],
    }
    first = planner._planning_provider_cache_identity(
        session,
        **common,
        candidate_search_round="1",
    )
    second = planner._planning_provider_cache_identity(
        session,
        **common,
        candidate_search_round="2",
    )
    assert first[0] != second[0]
    assert first[2] != second[2]

    class Provider:
        def __init__(self) -> None:
            self.messages: list[list[dict[str, str]]] = []

        async def json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
            self.messages.append(messages)
            return {
                "reviews": [{
                    "candidate_id": "new-1",
                    "decision": "qualified",
                    "relevance_score": 0.9,
                    "completeness_score": 0.9,
                    "reason": "semantic subject coverage",
                }]
            }

    provider = Provider()
    monkeypatch.setattr(planner, "_planning_provider_review_cache_read", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner, "_planning_provider_review_cache_write", lambda **_kwargs: None)
    candidate = {
        "candidate_id": "new-1",
        "url": "https://notes.example/new.pdf",
        "title": "Complementary structured notes",
        "snippet": "Definitions, chapters, derivations, examples and exercises.",
        "media_kind": "document",
        "source_class": "official_or_educational",
    }
    mapping, _meta = asyncio.run(
        planner._planning_provider_review_candidates(
            provider=provider,
            session=session,
            message="find complementary notes",
            objective="Learn a subject",
            anchors=[{"title": "Selected video", "url": "https://video.example/watch"}],
            candidates=[candidate],
            discovery_context={
                "mode": "add_page",
                "target_count": 6,
                "search_round": "2",
                "existing_candidate_summaries": common["existing_candidate_summaries"],
            },
        )
    )
    assert "new-1" in mapping
    serialized = json.dumps(provider.messages, ensure_ascii=False)
    assert "add_page" in serialized
    assert "search_round" in serialized and "2" in serialized
    assert "平台标识符" in serialized
    assert "语义主题" in serialized
    assert "未知权威" in serialized


class _ExpansionProvider:
    def __init__(self, topic: str) -> None:
        self.topic = topic

    async def json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload: Any = None
        try:
            payload = json.loads(str(messages[-1].get("content") or ""))
        except (IndexError, TypeError, json.JSONDecodeError):
            pass
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if isinstance(candidates, list) and candidates:
            return {
                "reviews": [
                    {
                        "candidate_id": str(row.get("candidate_id") or ""),
                        "decision": "qualified",
                        "relevance_score": 0.95,
                        "completeness_score": 0.95,
                        "reason": "semantic subject coverage and structured evidence",
                    }
                    for row in candidates
                    if isinstance(row, dict)
                ]
            }
        return {
            "known_facts": {
                "objective": f"Learn {self.topic}",
                "purpose": "",
                "timeframe": "",
                "named_materials": [],
                "mastery_criteria": "Explain the core ideas",
                "practice_requirements": [],
            },
            "resource_search_authorized": True,
            "search_queries": [
                {"query": f"{self.topic} comprehensive lecture notes", "reason": "structured evidence"}
            ],
        }

    async def compact_json(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        del max_tokens, temperature
        return await self.json(messages)


def _pool_note(topic: str, index: int) -> dict[str, Any]:
    return {
        "url": f"https://expansion-existing.example/{topic.casefold().replace(' ', '-')}-{index}",
        "title": f"{topic} existing structured notes {index}",
        "snippet": "Chapter outline, definitions, derivations, worked examples, formulas and exercises.",
        "media_kind": "document",
        "query": f"{topic} comprehensive lecture notes",
        "comprehensive": True,
        "sections": ["Foundations", "Applications", "Exercises"],
        "planning_batch_contract": "complete-page-v1",
    }


@pytest.mark.parametrize(
    ("mode", "existing_count", "target", "selected_count", "expected_fresh"),
    [
        ("add_page", 3, 6, 3, 3),
        ("add_page", 4, 9, 4, 5),
        ("fill_page", 2, 3, 0, 1),
    ],
)
def test_structured_round_appends_only_target_shortfall(
    client,
    monkeypatch,
    mode: str,
    existing_count: int,
    target: int,
    selected_count: int,
    expected_fresh: int,
):
    topic = f"Expansion Topic {existing_count}"
    workspace_id = f"structured-expansion-{mode}-{existing_count}"
    pool = planner._PLANNING_RESOURCE_POOL
    created = pool.upsert(
        workspace_id,
        [_pool_note(topic, index) for index in range(existing_count)],
        page_size=3,
    )
    provider = _ExpansionProvider(topic)
    search_calls: list[int] = []

    async def fake_search(_query: str, *, max_results: int) -> dict[str, Any]:
        search_calls.append(max_results)
        return {
            "results": [
                {
                    "url": f"https://expansion-fresh.example/{existing_count}-{index}",
                    "title": f"{topic} fresh structured lecture notes {index}",
                    "snippet": "Chapter outline, definitions, derivations, worked examples, formulas and exercises.",
                    "media_kind": "document",
                    "comprehensive": True,
                    "sections": ["Foundations", "Applications", "Exercises"],
                }
                for index in range(max(1, expected_fresh))
            ]
        }

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_args: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner, "_planning_provider_cache_read", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner, "_planning_provider_cache_write", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner, "_planning_provider_review_cache_read", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner, "_planning_provider_review_cache_write", lambda **_kwargs: None)

    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "message": f"Please find public lecture notes for {topic}.",
            "goal": f"Learn {topic}",
            "search_anchor": topic,
            "resource_search_authorized": True,
            "planning_session_id": created["planning_session_id"],
            "revision": created["revision"],
            "page_size": 3,
            "candidate_discovery_mode": mode,
            "candidate_target_count": target,
            "selected_material_count": selected_count,
            "candidate_search_round": 1,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    audit = payload["candidate_discovery"]
    assert audit["requested_target_count"] == target
    assert audit["fresh_needed"] == expected_fresh
    assert audit["discovery_batch_size"] == expected_fresh
    assert audit["pool_mode"] == "append"
    # The public adapter intentionally over-fetches per query before hard
    # qualification; the bounded discovery batch (and durable pool) remains
    # exactly the requested shortfall.
    assert search_calls and max(search_calls) >= expected_fresh
    durable = pool.candidates(workspace_id, created["planning_session_id"], include_inactive=False)
    assert len(durable) == target


def test_fill_page_normalizes_stale_target_to_current_page_boundary(
    client,
    monkeypatch,
):
    """A stale fill target cannot strand an incomplete strict page."""

    topic = "Fill Boundary Topic"
    workspace_id = "structured-fill-stale-target"
    pool = planner._PLANNING_RESOURCE_POOL
    created = pool.upsert(
        workspace_id,
        [_pool_note(topic, index) for index in range(5)],
        page_size=3,
    )
    provider = _ExpansionProvider(topic)
    search_calls: list[int] = []

    async def fake_search(_query: str, *, max_results: int) -> dict[str, Any]:
        search_calls.append(max_results)
        return {
            "results": [{
                "url": "https://fill-fresh.example/0",
                "title": f"{topic} fresh structured lecture notes",
                "snippet": "Chapter outline, definitions, derivations, worked examples, formulas and exercises.",
                "media_kind": "document",
                "comprehensive": True,
                "sections": ["Foundations", "Applications", "Exercises"],
            }],
        }

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_args: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner, "_planning_provider_cache_read", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner, "_planning_provider_cache_write", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner, "_planning_provider_review_cache_read", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner, "_planning_provider_review_cache_write", lambda **_kwargs: None)

    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "message": f"Please find public lecture notes for {topic}.",
            "goal": f"Learn {topic}",
            "search_anchor": topic,
            "resource_search_authorized": True,
            "planning_session_id": created["planning_session_id"],
            "revision": created["revision"],
            "page_size": 3,
            "candidate_discovery_mode": "fill_page",
            # This target describes the stale first page, not the current
            # eligible count of five rows held by the strict pool.
            "candidate_target_count": 3,
            "candidate_search_round": 1,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    audit = payload["candidate_discovery"]
    assert audit["requested_target_count"] == 6
    assert audit["active_eligible_count"] == 5
    assert audit["fresh_needed"] == 1
    assert audit["discovery_batch_size"] == 1
    assert search_calls and max(search_calls) >= 1

    first_page = payload["candidate_page"]
    assert first_page["total_count"] == 6
    assert first_page["page_count"] == 2
    assert first_page["has_more"] is True
    assert first_page["next_cursor"]
    first_ids = {
        str(item["candidate_id"])
        for item in first_page["items"]
    }
    assert len(first_ids) == 3

    second_response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "pool_action": "page",
            "planning_session_id": created["planning_session_id"],
            "page_size": 3,
            "cursor": first_page["next_cursor"],
        },
    )
    assert second_response.status_code == 200, second_response.text
    second_page = second_response.json()["candidate_page"]
    second_ids = {
        str(item["candidate_id"])
        for item in second_page["items"]
    }
    durable_ids = {
        str(item["candidate_id"])
        for item in pool.candidates(
            workspace_id,
            created["planning_session_id"],
            include_inactive=False,
        )
    }
    assert len(second_ids) == 3
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == durable_ids
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None


def test_fill_page_skips_provider_when_current_pool_already_meets_target(
    client,
    monkeypatch,
):
    topic = "Fill Complete Topic"
    workspace_id = "structured-fill-complete"
    pool = planner._PLANNING_RESOURCE_POOL
    created = pool.upsert(
        workspace_id,
        [_pool_note(topic, index) for index in range(3)],
        page_size=3,
    )
    provider_calls: list[str] = []

    monkeypatch.setattr(
        planner,
        "_v1_provider_session",
        lambda *_args: provider_calls.append("provider") or (_ for _ in ()).throw(AssertionError("provider called")),
    )
    monkeypatch.setattr(
        planner,
        "provider_for_session",
        lambda *_args: provider_calls.append("provider") or (_ for _ in ()).throw(AssertionError("provider called")),
    )
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])

    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "message": f"Please find public lecture notes for {topic}.",
            "planning_session_id": created["planning_session_id"],
            "revision": created["revision"],
            "page_size": 3,
            "candidate_discovery_mode": "fill_page",
            "candidate_target_count": 3,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert provider_calls == []
    assert payload["candidate_discovery"]["requested_target_count"] == 3
    assert payload["candidate_discovery"]["fresh_needed"] == 0
    assert payload["candidate_discovery"]["discovery_batch_size"] == 0
    assert payload["candidate_page"]["total_count"] == 3
    assert payload["candidate_page"]["has_more"] is False
