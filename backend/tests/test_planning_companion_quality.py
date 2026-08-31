from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import generic_knowledge_api as planner


def _anchor() -> dict[str, object]:
    return {
        "url": "https://anchor.example/resource/linear-algebra-notes.pdf",
        "title": "Linear Algebra Selected Notes",
        "direct_identifier": {"kind": "https_url", "value": "linear-algebra-notes.pdf"},
        "direct_user_reference": True,
        "media_kind": "document",
    }


def _video_anchor() -> dict[str, object]:
    return {
        "url": "https://video.example/watch/BV1ib411t7YR",
        "title": "Linear Algebra Selected Video",
        "direct_identifier": {"kind": "opaque", "value": "BV1ib411t7YR"},
        "direct_user_reference": True,
        "media_kind": "video",
    }


def _search_patch(monkeypatch, rows):
    async def fake_search(_query: str, *, max_results: int):
        return {"results": list(rows)}

    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)


def test_mirror_index_echo_is_not_an_independent_companion(monkeypatch):
    anchor = _anchor()
    _search_patch(monkeypatch, [{
        "url": "https://mirror.example/index/linear-algebra",
        "title": "Mirror index: Linear Algebra Complete Course",
        "snippet": f"Original source: {anchor['url']} ({anchor['direct_identifier']['value']})",
        "media_kind": "document",
    }])

    resources, warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": "linear algebra notes", "reason": "test"}],
            max_resources=3,
            anchor_candidates=[anchor],
        )
    )

    assert resources == []
    assert any("独立配套" in warning for warning in warnings)


def test_broad_page_with_incidental_topic_mention_is_not_a_companion(monkeypatch):
    _search_patch(monkeypatch, [{
        "url": "https://university.example/summer-catalog",
        "title": "University Summer Programs 2026",
        "snippet": "A broad catalog of many courses; linear algebra appears as one optional line.",
        "media_kind": "document",
    }])

    resources, _warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": "linear algebra notes", "reason": "test"}],
            max_resources=3,
            anchor_candidates=[_anchor()],
        )
    )
    assert resources == []


def test_topic_named_catalog_with_only_one_line_is_still_not_a_companion(monkeypatch):
    _search_patch(monkeypatch, [{
        "url": "https://catalog.example/linear-algebra-summer",
        "title": "Linear Algebra Summer Catalog",
        "snippet": "A directory listing; linear algebra is one optional course.",
        "media_kind": "document",
    }])
    resources, _warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": "linear algebra notes", "reason": "test"}],
            max_resources=3,
            anchor_candidates=[_anchor()],
        )
    )
    assert resources == []


def test_substantive_notes_that_cite_anchor_are_accepted(monkeypatch):
    anchor = _anchor()
    _search_patch(monkeypatch, [{
        "url": "https://notes.example/linear-algebra/lecture-1",
        "title": "Linear Algebra Lecture Notes",
        "snippet": (
            f"Definitions, vector spaces, basis, and worked examples. "
            f"The notes cite the selected source {anchor['url']} for the opening lecture."
        ),
        "media_kind": "document",
    }])

    resources, _warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": "linear algebra notes", "reason": "test"}],
            max_resources=3,
            anchor_candidates=[anchor],
        )
    )
    assert [item["url"] for item in resources] == ["https://notes.example/linear-algebra/lecture-1"]
    assert resources[0]["independent_companion"] is True


def test_legacy_pool_rows_without_quality_marker_are_rechecked_against_direct_anchor():
    anchor = _anchor()
    page = planner._planning_recheck_pool_page({
        "page_size": 3,
        "total_count": 3,
        "qualified_candidate_count": 3,
        "new_candidate_count": 2,
        "items": [
            {
                **anchor,
                "candidate_id": "direct-anchor",
                "stable_id": "direct-anchor",
                "selection_state": "unselected",
            },
            {
                "candidate_id": "legacy-mirror",
                "stable_id": "legacy-mirror",
                "url": "https://mirror.example/index/linear-algebra",
                "title": "Mirror index: Linear Algebra Complete Course",
                "snippet": f"Original source: {anchor['url']} ({anchor['direct_identifier']['value']})",
                "query": "linear algebra notes",
                "media_kind": "document",
                "qualification_status": "qualified",
            },
            {
                "candidate_id": "legacy-notes",
                "stable_id": "legacy-notes",
                "url": "https://notes.example/linear-algebra/lecture-2",
                "title": "Linear Algebra Lecture Notes",
                "snippet": "Definitions, vector spaces, basis, formulas, and worked examples.",
                "query": "linear algebra notes",
                "media_kind": "document",
                "qualification_status": "qualified",
            },
        ],
    })

    assert [item["candidate_id"] for item in page["items"]] == ["direct-anchor", "legacy-notes"]
    assert page["blocked_count"] == 1
    assert page["blocked_items"][0]["candidate_id"] == "legacy-mirror"
    assert page["blocked_items"][0]["block_reason"] == "independent_companion_quality_gate"


@pytest.mark.parametrize("topic", ["Aurora Protocol", "Quartz Weaving"])
def test_independent_quality_gate_is_topic_and_domain_neutral(monkeypatch, topic):
    _search_patch(monkeypatch, [{
        "url": f"https://independent.example/{topic.casefold().replace(' ', '-')}/notes",
        "title": f"{topic} Lecture Notes",
        "snippet": "Definitions, core concepts, and worked examples for focused study.",
        "media_kind": "document",
    }])
    resources, _warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": f"{topic} notes", "reason": "test"}],
            max_resources=3,
            anchor_candidates=[{
                "url": f"https://anchor.example/{topic.casefold().replace(' ', '-')}",
                "title": f"{topic} Selected Course",
                "direct_user_reference": True,
            }],
        )
    )
    assert len(resources) == 1
    assert topic in resources[0]["title"]


class _FakeProvider:
    def __init__(self, payload):
        self.payload = payload

    async def json(self, _messages):
        return self.payload


def test_replenishment_uses_good_rows_after_echoes_and_reports_truthful_page(client, monkeypatch):
    provider = _FakeProvider({
        "known_facts": {"objective": "Learn linear algebra", "named_materials": []},
        "search_queries": [],
    })
    calls = 0
    anchor_url = "https://notes.example/linear-algebra/selected-anchor.pdf"

    async def fake_search(_query: str, *, max_results: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"results": [{
                "url": "https://mirror.example/index/linear-algebra",
                "title": "Mirror index: Linear Algebra Complete Course",
                "snippet": f"Original source: {anchor_url} BV1ib411t7YR",
                "media_kind": "document",
            }]}
        return {"results": [{
            "url": f"https://notes.example/linear-algebra/{calls}",
            "title": f"Linear Algebra Lecture Notes {calls}",
            "snippet": "Definitions, core concepts, and worked examples for focused study.",
            "media_kind": "document",
        }]}

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: SimpleNamespace(
        id="quality-session", provider="openai_compatible", model="test-model",
        base_url="https://provider.example", credential_ref="ref",
    ))
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)

    response = client.post(
        "/api/active-teaching/workspaces/quality-replenishment/planning-assistant",
        json={
            "message": f"请找线性代数笔记并保留这个文档 {anchor_url}，补充配套资料。",
            "goal": "学习线性代数",
            "resource_search_authorized": True,
            "page_size": 3,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert calls >= 2
    assert len(payload["resources"]) == 3
    assert payload["candidate_page"]["page_shortfall_count"] == 0
    assert all("Mirror" not in item["title"] for item in payload["resources"])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("B站视频 BV1ys411472E 线性代数总集 配套讲义笔记", "线性代数"),
        ("请帮我再找两份 Aurora Protocol 配套资料", "Aurora Protocol"),
        ("完全掌握 Quartz Weaving 全套视频", "Quartz Weaving"),
    ],
)
def test_search_subject_removes_transport_noise_without_topic_hardcoding(raw, expected):
    assert planner._planning_search_subject(raw) == expected


def test_policy_recheck_counts_eligible_direct_document_in_complete_page_geometry():
    page = planner._planning_recheck_pool_page({
        "page_size": 3,
        "total_count": 3,
        "qualified_candidate_count": 3,
        "new_candidate_count": 2,
        "held_incomplete_count": 0,
        "items": [
            {
                **_anchor(),
                "candidate_id": "direct-card",
                "stable_id": "direct-card",
                "planning_batch_contract": "complete-page-v1",
            },
            {
                "candidate_id": "note-a",
                "stable_id": "note-a",
                "url": "https://notes.example/linear-algebra/a",
                "title": "Linear Algebra Definitions and Examples A",
                "snippet": "Definitions, formulas, worked examples, and exercises.",
                "query": "linear algebra notes",
                "media_kind": "document",
                "qualification_status": "qualified",
                "planning_batch_contract": "complete-page-v1",
            },
            {
                "candidate_id": "note-b",
                "stable_id": "note-b",
                "url": "https://notes.example/linear-algebra/b",
                "title": "Linear Algebra Definitions and Examples B",
                "snippet": "Definitions, formulas, worked examples, and exercises.",
                "query": "linear algebra notes",
                "media_kind": "document",
                "qualification_status": "qualified",
                "planning_batch_contract": "complete-page-v1",
            },
        ],
    })

    assert [item["candidate_id"] for item in page["items"]] == ["direct-card", "note-a", "note-b"]
    assert page["held_incomplete_count"] == 0
    assert page["shortfall_count"] == 0
    assert page["page_complete"] is True


def test_policy_recheck_excludes_direct_video_before_selection_and_page_geometry():
    page = planner._planning_recheck_pool_page({
        "page_size": 3,
        "total_count": 3,
        "qualified_candidate_count": 3,
        "selected_count": 1,
        "new_candidate_count": 2,
        "items": [
            {
                **_video_anchor(),
                "candidate_id": "blocked-direct-video",
                "stable_id": "blocked-direct-video",
                "selection_state": "selected",
            },
            {
                "candidate_id": "note-a",
                "stable_id": "note-a",
                "url": "https://notes.example/linear-algebra/a.pdf",
                "title": "Linear Algebra Definitions and Examples A",
                "snippet": "Definitions, formulas, worked examples, and exercises.",
                "query": "linear algebra notes",
                "media_kind": "document",
                "qualification_status": "qualified",
            },
            {
                "candidate_id": "note-b",
                "stable_id": "note-b",
                "url": "https://notes.example/linear-algebra/b.pdf",
                "title": "Linear Algebra Definitions and Examples B",
                "snippet": "Definitions, formulas, worked examples, and exercises.",
                "query": "linear algebra notes",
                "media_kind": "document",
                "qualification_status": "qualified",
            },
        ],
    })

    assert [item["candidate_id"] for item in page["items"]] == ["note-a", "note-b"]
    assert page["qualified_candidate_count"] == 2
    assert page["total_count"] == 2
    assert page["page_shortfall_count"] == 1
    assert page["page_complete"] is False
    assert page["blocked_items"][0]["candidate_id"] == "blocked-direct-video"
    assert page["blocked_items"][0]["block_reason"] == "notes_only_media_contract"


def test_companion_discovery_keeps_provider_topic_queries_and_diversifies_until_full(client, monkeypatch):
    provider = _FakeProvider({
        "known_facts": {"objective": "Master Aurora Protocol", "named_materials": []},
        "search_queries": [
            {"query": "Aurora Protocol lecture notes", "reason": "topic notes"},
            {"query": "Aurora Protocol worked examples", "reason": "topic examples"},
        ],
    })
    calls: list[str] = []
    anchor_url = "https://notes.example/aurora/selected-anchor.pdf"

    async def fake_search(query: str, *, max_results: int):
        calls.append(query)
        if len(calls) <= 6:
            return {"results": [{
                "url": f"https://mirror.example/{len(calls)}",
                "title": "Mirror index: Aurora Protocol Selected Course",
                "snippet": f"Original source: {anchor_url} BV1ib411t7YR",
                "media_kind": "document",
            }]}
        index = len(calls)
        return {"results": [{
            "url": f"https://notes.example/aurora/{index}",
            "title": f"Aurora Protocol Lecture Notes {index}",
            "snippet": "Definitions, methods, worked examples, and exercises for Aurora Protocol.",
            "media_kind": "document",
        }]}

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: SimpleNamespace(
        id="topic-session", provider="openai_compatible", model="test-model",
        base_url="https://provider.example", credential_ref="ref",
    ))
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)

    response = client.post(
        "/api/active-teaching/workspaces/topic-diversification/planning-assistant",
        json={
            "message": f"保留这个文档 {anchor_url}，再找点配套笔记。",
            "goal": "Master Aurora Protocol",
            "resource_search_authorized": True,
            "page_size": 3,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["resources"]) == 3
    assert payload["candidate_page"]["page_complete"] is True
    assert any(
        "Aurora Protocol" in query
        and any(term in query.casefold() for term in ("lecture notes", "course documents", "讲义", "笔记"))
        for query in calls
    )
    assert any("大学课程讲义" in query or "开放教材" in query for query in calls)
    # Exact source transport metadata is retained in the candidate pool, never
    # copied into public retrieval queries. Diversified topic lanes remain.
    assert all("selected-anchor.pdf" not in query and anchor_url not in query for query in calls)
