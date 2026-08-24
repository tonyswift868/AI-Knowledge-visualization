from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import generic_knowledge_api as planner


class _FakeProvider:
    def __init__(self, payload: dict):
        self.payload = payload

    async def json(self, _messages):
        return self.payload


def _session():
    return SimpleNamespace(
        id="planning-resource-authorization-session",
        provider="openai_compatible",
        model="planning-resource-model",
        base_url="https://provider.example/v1",
        credential_ref="credential-not-in-cache",
        temperature=0.2,
    )


def _patch_provider(monkeypatch, payload: dict):
    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: _FakeProvider(payload))


def _comprehensive_note(url: str, topic: str = "线性代数", *, index: int | None = None) -> dict[str, object]:
    suffix = f" {index}" if index is not None else ""
    return {
        "url": url,
        "title": f"{topic} Comprehensive Lecture Notes Chapters 1-8{suffix}",
        "snippet": (
            f"{topic}: chapter outline and section summaries; definitions, derivations, "
            "worked examples, formulas, exercises, and review checklist."
        ),
        "media_kind": "document",
        "comprehensive": True,
        "sections": ["Foundations", "Applications", "Exercises"],
    }


def test_user_authorization_survives_provider_cache_when_auth_field_is_omitted(monkeypatch):
    """A cached Provider interpretation must not turn omitted auth into false."""

    provider_payload = {
        "goal_summary": "掌握可验证的学习目标",
        "search_queries": [{"query": "线性代数官方学习资料", "reason": "查找原始来源"}],
        # Deliberately omit resource_search_authorized.
    }
    _patch_provider(
        monkeypatch,
        provider_payload,
    )
    searched: list[str] = []

    async def fake_search(query, *, max_results):
        # A fresh durable pool fills one visible three-card page.  The search
        # Search over-fetches before qualification but publishes only a
        # complete three-card page.
        assert max_results == 9
        searched.append(query)
        return {
            "results": [
                _comprehensive_note(f"https://example.com/official-{index}", index=index)
                for index in range(1, 4)
            ]
        }

    monkeypatch.setattr(planner, "search_web", fake_search)
    # Exercise the Provider-cache hit explicitly while isolating the unrelated
    # search-result cache.  Otherwise either cache can inherit rows from a
    # separately configured ORM session and make this authorization contract
    # depend on local test history.
    monkeypatch.setattr(
        planner,
        "_planning_provider_cache_read",
        lambda *_args, **_kwargs: dict(provider_payload),
    )
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda _query, _payload: None)
    result = asyncio.run(
        planner.planning_assistant(
            "planning-auth-workspace",
            object(),
            {"message": "请帮我找线性代数权威资料。"},
        )
    )

    # Authorization survives the cache hit, while the current W43 contract
    # prefixes every public query with the server-owned learning anchor.
    assert searched
    assert "线性代数" in searched[-1]
    assert any(term in searched[-1].casefold() for term in ("notes", "lecture", "document", "讲义", "笔记", "文档"))
    assert result["resource_search_authorized"] is True
    assert result["resources"][0]["url"] == "https://example.com/official-1"
    cached = planner._planning_provider_cache_value(
        {"goal_summary": "目标", "search_queries": [{"query": "资料"}]}
    )
    assert cached is not None
    assert "resource_search_authorized" not in cached
    cached_false = planner._planning_provider_cache_value(
        {
            "goal_summary": "目标",
            "resource_search_authorized": False,
            "search_queries": [],
        }
    )
    assert cached_false is not None
    assert cached_false["resource_search_authorized"] is False


def test_explicit_search_prohibition_blocks_provider_queries(monkeypatch):
    _patch_provider(
        monkeypatch,
        {
            "goal_summary": "掌握一个主题",
            "resource_search_authorized": True,
            "search_queries": [{"query": "不应执行的查询", "reason": "模型建议"}],
        },
    )
    searched = False

    async def fake_search(*_args, **_kwargs):
        nonlocal searched
        searched = True
        return {"results": []}

    monkeypatch.setattr(planner, "search_web", fake_search)
    result = asyncio.run(
        planner.planning_assistant(
            "planning-deny-workspace",
            object(),
            {
                "message": "我想掌握一个主题。",
                "resource_search_authorized": False,
            },
        )
    )

    assert searched is False
    assert result["resource_search_authorized"] is False
    assert result["queries"] == []
    assert result["resources"] == []
    assert result["cache"]["search_queries"] == 0


@pytest.mark.parametrize(
    ("reference", "expected_url"),
    [
        ("BV1ib411t7YR", "https://www.bilibili.com/video/BV1ib411t7YR/"),
        ("https://example.com/guide", "https://example.com/guide"),
        ("https://localhost:8443/guide", "https://localhost:8443/guide"),
        ("https://192.168.1.20/manual", "https://192.168.1.20/manual"),
        ("https://printer.local/status", "https://printer.local/status"),
        ("https://[::1]:8443/guide", "https://[::1]:8443/guide"),
        ("https://[fd00::20]/manual", "https://[fd00::20]/manual"),
        ("https://doi.org/10.1000/xyz123", "https://doi.org/10.1000/xyz123"),
        ("arXiv:2401.12345", "https://arxiv.org/abs/2401.12345"),
    ],
)
def test_direct_reference_is_retained_without_similar_search(
    monkeypatch, reference: str, expected_url: str
):
    """Exact user references survive opt-out and never become search queries."""

    _patch_provider(
        monkeypatch,
        {
            "goal_summary": "使用用户指定资源学习",
            "search_queries": [{"query": "相似资源", "reason": "模型建议"}],
        },
    )
    searched = False

    async def fake_search(*_args, **_kwargs):
        nonlocal searched
        searched = True
        return {"results": [{"url": "https://example.com/similar", "title": "相似结果"}]}

    monkeypatch.setattr(planner, "search_web", fake_search)
    result = asyncio.run(
        planner.planning_assistant(
            f"direct-reference-{reference}",
            object(),
            {
                # Keep a whitespace boundary around URL references so the
                # exact URL token is not conflated with adjacent prose.
                "message": f"请使用 {reference} ，不要找相似资源。",
                "resource_search_authorized": False,
            },
        )
    )

    assert searched is False
    assert result["queries"] == []
    assert result["cache"]["search_queries"] == 0
    assert result["resources"]
    candidate = result["resources"][0]
    assert candidate["url"] == expected_url
    assert candidate["direct_user_reference"] is True
    assert candidate["reference_resolution"]["network_verified"] is False


def test_direct_local_reference_normalization_keeps_url_identity_and_coverage_role():
    exact_url = "https://localhost:8443/guide"
    candidates = planner._planning_direct_resource_candidates(
        f"请使用 {exact_url} ，不要找相似资源。"
    )
    assert len(candidates) == 1

    normalized = planner._planning_normalize_resource(candidates[0])

    assert normalized["url"] == exact_url
    assert normalized["coverage_role"] == "user_named_resource"
    assert normalized["direct_identifier"] == {
        "kind": "https_url",
        "value": exact_url,
        "match": "exact",
    }
    assert normalized["reference_resolution"]["network_verified"] is False
    assert normalized["reference_resolution"]["requires_user_confirmation"] is True
    assert normalized["merge_metadata"]["merge_key"] == exact_url


def test_public_search_results_keep_private_https_urls_filtered(monkeypatch):
    _patch_provider(
        monkeypatch,
        {
            "goal_summary": "查找线性代数公开资料",
            "resource_search_authorized": True,
            "search_queries": [{"query": "线性代数公开资料", "reason": "补充资料"}],
        },
    )

    async def fake_search(*_args, **_kwargs):
        return {
            "results": [
                {"url": "https://localhost:8443/private", "title": "不应暴露的内网结果"},
                {"url": "https://192.168.1.20/manual", "title": "不应暴露的局域网结果"},
                _comprehensive_note("https://public.example/guide"),
            ]
        }

    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args: None)
    result = asyncio.run(
        planner.planning_assistant(
            "planning-public-url-policy",
            object(),
            {"message": "请帮我找线性代数公开资料", "resource_search_authorized": True},
        )
    )

    assert result["resources"] == []
    assert result["candidate_page"]["qualified_candidate_count"] == 1
    assert result["candidate_page"]["held_incomplete_count"] == 1
    assert result["candidate_page"]["items"] == []
    assert result["candidate_page"]["all_active_count"] == 1
    # One qualified row cannot form a complete three-card page.  Discovery may
    # make a bounded supplemental attempt, but it must still hide the partial
    # page rather than fill it with private or unrelated URLs.
    assert 1 < result["cache"]["search_queries"] <= 3
