from __future__ import annotations

import asyncio

from app import generic_knowledge_api as planner


def test_planning_searches_three_queries_concurrently_and_merges_stably(monkeypatch):
    active = 0
    max_active = 0

    async def fake_search(query: str, *, max_results: int):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep({"q1": 0.03, "q2": 0.01, "q3": 0.02}[query])
        active -= 1
        return {
            "results": [
                {"url": "https://example.test/shared", "title": query},
                {"url": f"https://example.test/{query}"},
                {"url": "http://example.test/insecure"},
            ]
        }

    monkeypatch.setattr(planner, "search_web", fake_search)
    resources, warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": "q1", "reason": "one"}, {"query": "q2", "reason": "two"}, {"query": "q3", "reason": "three"}]
        )
    )

    assert max_active == 3
    assert [item["query"] for item in resources] == ["q1", "q1", "q2"]
    assert [item["url"] for item in resources] == [
        "https://example.test/shared",
        "https://example.test/q1",
        "https://example.test/q2",
    ]
    assert warnings == []


def test_planning_search_cache_reuses_results(monkeypatch):
    calls = 0

    async def fake_search(query: str, *, max_results: int):
        nonlocal calls
        calls += 1
        return {"results": [{"url": f"https://example.test/{query}", "title": query}]}

    monkeypatch.setattr(planner, "search_web", fake_search)
    query = [{"query": "portable topic", "reason": "test"}]
    first_stats: dict[str, int] = {}
    second_stats: dict[str, int] = {}
    first = asyncio.run(planner._planning_search_resources(query, cache_stats=first_stats))
    second = asyncio.run(planner._planning_search_resources(query, cache_stats=second_stats))

    assert calls == 1
    assert first[0] == second[0]
    assert first_stats == {"queries": 1}
    assert second_stats == {"hits": 1, "queries": 1}
