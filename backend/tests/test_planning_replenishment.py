from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import generic_knowledge_api as planner
from app.planning_resource_pool import PlanningResourcePool


class _FakeProvider:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    async def json(self, _messages):
        return self.payload


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        id="planning-replenishment-test-session",
        provider="openai_compatible",
        model="test-model",
        base_url="https://provider.example",
        credential_ref="ref",
    )


def _row(
    slug: str,
    *,
    title: str,
    query: str | None = None,
    direct: bool = False,
    selected_intent: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "url": f"https://pool-fixtures.example/{slug}",
        "title": title,
        "snippet": (
            f"Comprehensive structured lecture notes for {title}: chapter outline, "
            "section summaries, definitions, derivations, worked examples, formulas, "
            "exercises, and review checklist."
        ),
        "media_kind": "document",
        "comprehensive": True,
        "sections": ["Foundations", "Applications", "Exercises"],
        "planning_batch_contract": "complete-page-v1",
    }
    if query is not None:
        payload["query"] = query
    if direct:
        payload["direct_user_reference"] = True
    if selected_intent:
        payload["planning_selection_intent"] = "selected"
    return payload


def _provider_for_topic(topic: str) -> _FakeProvider:
    return _FakeProvider(
        {
            "known_facts": {
                "objective": f"Learn {topic}",
                "purpose": "",
                "timeframe": "",
                "named_materials": [],
                "mastery_criteria": "Explain the core ideas",
                "practice_requirements": [],
            },
            "search_queries": [
                {"query": f"{topic} reference", "reason": "Find public references"}
            ],
        }
    )


def _safe_search_for_topic(topic: str):
    async def fake_search(_query: str, *, max_results: int):
        return {
            "results": [
                {
                    "url": f"https://fresh-fixtures.example/{topic.casefold().replace(' ', '-')}-{index}",
                    "title": f"{topic} Comprehensive Lecture Notes Chapters 1-8 {index}",
                    "snippet": (
                        f"{topic}: chapter outline and section summaries; definitions, derivations, "
                        "worked examples, formulas, exercises, and review checklist."
                    ),
                    "media_kind": "document",
                    "comprehensive": True,
                    "sections": ["Foundations", "Applications", "Exercises"],
                }
                for index in range(1, max(3, max_results) + 1)
            ]
        }

    return fake_search


def _patch_discovery(monkeypatch, topic: str, *, search=None):
    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: _provider_for_topic(topic))
    monkeypatch.setattr(planner, "search_web", search or _safe_search_for_topic(topic))
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)


def test_supersede_blocked_protects_selected_anchor_and_direct_rows():
    pool = PlanningResourcePool()
    workspace_id = "replenishment-service-invariants"
    created = pool.upsert(
        workspace_id,
        [
            _row("blocked", title="Old unrelated row", query="different topic reference"),
            _row("selected", title="Selected row", selected_intent=True),
            _row("anchor", title="Anchor row"),
            _row("direct", title="Exact learner link", direct=True),
        ],
        page_size=3,
    )
    session_id = created["planning_session_id"]
    ids = {
        item["title"]: item["candidate_id"]
        for item in pool.candidates(workspace_id, created["planning_session_id"])
    }

    selected = pool.select(
        workspace_id,
        planning_session_id=session_id,
        candidate_ids=ids["Selected row"],
        expected_revision=created["revision"],
    )
    anchored = pool.decide(
        workspace_id,
        planning_session_id=session_id,
        action="set_anchor",
        anchor_candidate_id=ids["Anchor row"],
        expected_revision=selected["revision"],
    )
    result = pool.supersede_blocked(
        workspace_id,
        planning_session_id=session_id,
        candidate_ids=list(ids.values()),
        expected_revision=anchored["revision"],
    )

    rows = {
        row["title"]: row
        for row in pool.candidates(workspace_id, session_id, include_inactive=True)
    }
    assert rows["Old unrelated row"]["selection_state"] == "superseded"
    assert rows["Selected row"]["selection_state"] in {"selected", "support"}
    assert rows["Anchor row"]["selection_state"] == "anchor"
    assert rows["Exact learner link"]["direct_user_reference"] is True
    active_titles = {
        row["title"]
        for row in pool.candidates(workspace_id, session_id, include_inactive=False)
    }
    assert "Old unrelated row" not in active_titles
    assert {"Selected row", "Anchor row", "Exact learner link"} <= active_titles
    assert result["selection"]["anchor"] == ids["Anchor row"]


@pytest.mark.parametrize("topic", ["Aurora Protocol", "Quartz Weaving"])
def test_endpoint_replenishes_blocked_rows_to_exact_page_for_arbitrary_topic(client, monkeypatch, topic):
    workspace_id = f"replenish-{topic.casefold().replace(' ', '-')}"
    pool = PlanningResourcePool()
    created = pool.upsert(
        workspace_id,
        [
            _row("old-a", title="Unrelated archive A", query=f"{topic} reference"),
            _row("old-b", title="Unrelated archive B", query=f"{topic} reference"),
        ],
        page_size=3,
    )
    session_id = created["planning_session_id"]
    _patch_discovery(monkeypatch, topic)

    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "message": f"Please find public references for {topic}.",
            "resource_search_authorized": True,
            "planning_session_id": session_id,
            "revision": created["revision"],
            "page_size": 3,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    visible = payload["candidate_page"]["items"]
    assert len(visible) == 3
    assert len(payload["resources"]) == 3
    assert all(topic in item["title"] for item in visible)

    durable = {
        item["title"]: item
        for item in pool.candidates(workspace_id, session_id, include_inactive=True)
    }
    assert durable["Unrelated archive A"]["selection_state"] == "superseded"
    assert durable["Unrelated archive B"]["selection_state"] == "superseded"
    active_titles = {
        item["title"]
        for item in pool.candidates(workspace_id, session_id, include_inactive=False)
    }
    assert {"Unrelated archive A", "Unrelated archive B"}.isdisjoint(active_titles)
    assert payload["candidate_page"]["blocked_count"] == 0


def test_endpoint_exhaustion_keeps_protected_card_and_reports_two_card_shortfall(client, monkeypatch):
    topic = "Aurora Protocol"
    workspace_id = "replenish-exhaustion"
    pool = PlanningResourcePool()
    created = pool.upsert(
        workspace_id,
        [
            _row("direct", title="Protected learner source", direct=True),
            _row("blocked-a", title="Old unrelated A", query=f"{topic} reference"),
            _row("blocked-b", title="Old unrelated B", query=f"{topic} reference"),
        ],
        page_size=3,
    )
    session_id = created["planning_session_id"]
    direct_url = "https://pool-fixtures.example/direct"
    calls = 0

    async def duplicate_and_blocked_search(_query: str, *, max_results: int):
        nonlocal calls
        calls += 1
        return {
            "results": [
                {
                    "url": direct_url,
                    "title": "Duplicate protected learner source",
                    "snippet": "The existing exact source.",
                    "media_kind": "document",
                },
                {
                    "url": f"https://off-topic-fixtures.example/{calls}",
                    "title": "Unrelated replacement result",
                    "snippet": "Evidence about an unrelated subject.",
                    "media_kind": "document",
                },
                {
                    "url": f"https://unsafe-fixtures.example/{calls}",
                    "title": "Adult content replacement result",
                    "snippet": "Unsafe result that must not be shown.",
                    "media_kind": "document",
                },
            ]
        }

    _patch_discovery(monkeypatch, topic, search=duplicate_and_blocked_search)
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "message": f"Please find public references for {topic}.",
            "resource_search_authorized": True,
            "planning_session_id": session_id,
            "revision": created["revision"],
            "page_size": 3,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert calls >= 1
    assert len(payload["resources"]) == 1
    assert payload["resources"][0]["title"] == "Protected learner source"
    assert payload["candidate_page"]["page_shortfall_count"] == 2
    assert payload["candidate_page"]["shortfall_count"] == 2
    assert payload["candidate_page"]["total_count"] == 1
    assert all("replacement" not in item["title"] for item in payload["resources"])

    durable = {
        item["title"]: item
        for item in pool.candidates(workspace_id, session_id, include_inactive=True)
    }
    assert durable["Protected learner source"]["direct_user_reference"] is True
    assert all(
        durable[name]["selection_state"] == "superseded"
        for name in ("Old unrelated A", "Old unrelated B")
    )
    active_titles = {
        item["title"]
        for item in pool.candidates(workspace_id, session_id, include_inactive=False)
    }
    assert active_titles == {"Protected learner source"}


def test_stale_discovery_cannot_supersede_rows_before_revision_check(client, monkeypatch):
    topic = "Quartz Weaving"
    workspace_id = "replenish-stale-preflight"
    pool = PlanningResourcePool()
    created = pool.upsert(
        workspace_id,
        [
            _row("direct", title="Protected learner source", direct=True),
            _row("blocked", title="Old unrelated directory", query=f"{topic} reference"),
        ],
        page_size=3,
    )
    session_id = created["planning_session_id"]
    direct_id = next(
        item["candidate_id"]
        for item in pool.candidates(workspace_id, session_id)
        if item["direct_user_reference"]
    )
    selected = pool.select(
        workspace_id,
        planning_session_id=session_id,
        candidate_ids=direct_id,
        expected_revision=created["revision"],
    )
    _patch_discovery(monkeypatch, topic)

    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "message": f"Please find public references for {topic}.",
            "resource_search_authorized": True,
            "planning_session_id": session_id,
            "revision": created["revision"],
            "page_size": 3,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "STALE_REVISION"
    after = pool.page(workspace_id, session_id)
    assert after["revision"] == selected["revision"]
    blocked = next(
        item
        for item in pool.candidates(workspace_id, session_id, include_inactive=True)
        if item["title"] == "Old unrelated directory"
    )
    assert blocked["selection_state"] == "unselected"
    assert "Old unrelated directory" in {
        item["title"]
        for item in pool.candidates(workspace_id, session_id, include_inactive=False)
    }


def test_pool_action_refresh_is_read_only_and_skips_provider_and_search(client, monkeypatch):
    workspace_id = "replenish-refresh-read-only"
    pool = PlanningResourcePool()
    created = pool.upsert(workspace_id, [_row("one", title="Refreshable source")], page_size=3)
    session_id = created["planning_session_id"]
    before = pool.page(workspace_id, session_id)
    before_rows = pool.candidates(workspace_id, session_id, include_inactive=True)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("provider/search invoked by page refresh")

    monkeypatch.setattr(planner, "provider_for_session", must_not_run)
    monkeypatch.setattr(planner, "search_web", must_not_run)
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={"pool_action": "refresh", "planning_session_id": session_id},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    after = pool.page(workspace_id, session_id)
    after_rows = pool.candidates(workspace_id, session_id, include_inactive=True)
    assert payload["resources"] == payload["candidate_page"]["items"]
    assert after["revision"] == before["revision"]
    assert after_rows == before_rows


def test_refresh_rechecks_held_rows_and_repaginates_without_mutation(client, monkeypatch):
    workspace_id = "replenishment-refresh-held-policy-row"
    pool = PlanningResourcePool()
    created = pool.upsert(
        workspace_id,
        [
            _row(
                f"safe-{index}",
                title=f"Quartz Weaving field guide {index}",
                query="Quartz Weaving reference",
            )
            for index in range(3)
        ] + [
            _row(
                "held-blocked",
                title="Unrelated entertainment directory",
                query="Quartz Weaving reference",
            )
        ],
        page_size=3,
    )
    session_id = created["planning_session_id"]
    before_rows = pool.candidates(workspace_id, session_id, include_inactive=True)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("provider/search invoked by policy-only page refresh")

    monkeypatch.setattr(planner, "provider_for_session", must_not_run)
    monkeypatch.setattr(planner, "search_web", must_not_run)
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={"pool_action": "refresh", "planning_session_id": session_id, "page_size": 3},
    )
    assert response.status_code == 200, response.text
    payload = response.json()["candidate_page"]
    assert len(payload["items"]) == 3
    assert payload["total_count"] == 3
    assert payload["qualified_candidate_count"] == 3
    assert payload["blocked_count"] == 1
    assert payload["blocked_items"][0]["title"] == "Unrelated entertainment directory"
    assert payload["held_incomplete_count"] == 0
    assert payload["page_complete"] is True
    assert payload["has_more"] is False
    assert pool.candidates(workspace_id, session_id, include_inactive=True) == before_rows
