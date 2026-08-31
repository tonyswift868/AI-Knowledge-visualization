from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app import generic_knowledge_api as planner


class _FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.messages = []

    async def json(self, messages):
        self.messages.extend(messages)
        return self.payload


def _session():
    return SimpleNamespace(
        id="planning-test-session",
        provider="openai_compatible",
        model="test-model",
        base_url="https://provider.example",
        credential_ref="ref",
    )


def _comprehensive_note(url: str, topic: str = "线性代数", *, index: object | None = None) -> dict[str, object]:
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


def _direct_document(index: object) -> dict[str, object]:
    return {
        "url": f"https://materials.example/linear-algebra-{index}.pdf",
        "title": f"线性代数用户指定讲义 {index}",
        "media_kind": "document",
        "direct_user_reference": True,
    }


def test_search_position_is_not_misreported_as_popularity():
    candidate = planner._planning_normalize_resource(
        {
            "url": "https://materials.example/series",
            "title": "Complete public lecture series",
            "rank": 1,
        }
    )
    assert candidate["media_kind"] == "video_collection"
    assert candidate["popularity_status"] == "unknown"
    assert candidate["popularity_evidence"]["value"] is None


def test_discovery_batch_size_fills_current_page_then_adds_one_page_on_continuation():
    assert planner._planning_discovery_batch_size(2, page_size=3) == 1
    assert planner._planning_discovery_batch_size(4, page_size=3) == 2
    assert planner._planning_discovery_batch_size(3, page_size=3) == 3
    assert planner._planning_discovery_batch_size(2, page_size=3, continuing=True) == 1
    assert planner._planning_discovery_batch_size(5, page_size=3, continuing=True) == 1
    assert planner._planning_discovery_batch_size(1, page_size=3, minimum_new_count=2) == 2
    assert planner._planning_discovery_batch_size(1, page_size=3, minimum_new_count=3) == 5
    assert planner._planning_requested_resource_count("我有一个视频，再给我找两个视频") == 2
    assert planner._planning_requested_resource_count("再推荐3份资料") == 3
    assert planner._planning_requested_resource_count("再找点高质量资料") == 0
    # Replacement sizes from the retained set and fills only to the next
    # three-card boundary; it does not force a media combination.
    assert planner._planning_discovery_batch_size(
        4, page_size=3, continuing=True, protected_count=4, replace_unselected=True
    ) == 2
    assert planner._planning_discovery_batch_size(
        5, page_size=3, continuing=True, protected_count=5, replace_unselected=True
    ) == 1
    assert planner._planning_discovery_batch_size(
        2, page_size=3, continuing=True, protected_count=2, replace_unselected=True
    ) == 1


def test_server_search_anchor_consumes_only_top_level_bounded_fields_in_order():
    requested = {
        "topic": "  线性代数  ",
        "title": "should not win",
        "content_label": "课程讲义",
        "objective": "掌握矩阵",
        "query": "search terms",
        "metadata": {"topic": "nested secret"},
        "header": "Authorization: secret",
        "token": "credential",
        "path": "C:\\private\\notes.pdf",
    }
    assert planner._planning_requested_search_anchor(requested) == "线性代数"
    assert planner._planning_server_search_anchor(
        requested_anchor=requested,
        facts={"objective": "其他目标"},
    ) == "线性代数"
    assert planner._planning_requested_search_anchor(
        {"topic": "", "title": "  课程讲义  ", "metadata": {"title": "nested"}}
    ) == "课程讲义"
    assert planner._planning_requested_search_anchor(
        {"metadata": {"topic": "nested"}, "header": "hidden", "path": "secret"}
    ) == ""


def test_server_search_anchor_prefers_explicit_topic_but_keeps_source_fallbacks():
    requested = {"topic": "请求主题"}
    assert planner._planning_server_search_anchor(
        direct_candidates=[{"title": "Aurora Protocol Manual"}],
        requested_anchor=requested,
        facts={"objective": "学习目标"},
    ) == "请求主题"
    assert planner._planning_server_search_anchor(
        direct_candidates=[{"title": "Aurora Protocol Manual"}],
        facts={"objective": "学习目标"},
    ) == "Aurora Protocol Manual 学习目标"
    assert planner._planning_server_search_anchor(
        existing_materials=[{"title": "已上传讲义"}],
    ) == "已上传讲义"
    assert planner._planning_server_search_anchor(
        selected_resources=[{"title": "已选候选"}],
    ) == "已选候选"


def test_bilibili_bvid_becomes_exact_direct_candidate():
    candidates = planner._planning_direct_resource_candidates(
        "文字资料保留，再使用 BV1ib411t7YR 作为视频素材"
    )
    assert len(candidates) == 1
    candidate = planner._planning_normalize_resource(candidates[0])
    assert candidate["url"] == "https://www.bilibili.com/video/BV1ib411t7YR/"
    assert candidate["direct_identifier"] == {
        "kind": "bilibili_bvid",
        "value": "BV1ib411t7YR",
        "match": "exact",
    }
    assert candidate["rank_score"] >= 1000
    assert candidate["authority_status"] == "unknown"
    assert candidate["popularity_status"] == "unknown"


def test_direct_video_is_annotated_but_never_enters_planning_page(monkeypatch):
    provider = _FakeProvider(
        {
            "known_facts": {
                "objective": "学习一个公开课程",
                "purpose": "",
                "timeframe": "两天",
                "named_materials": ["BV1ib411t7YR"],
                "mastery_criteria": "能够讲清核心内容",
                "practice_requirements": [],
            },
            "search_queries": [{"query": "相关公开课程", "reason": "补充资料"}],
        }
    )

    async def fake_search(*_args, **_kwargs):
        return {
            "results": [
                {
                    "url": "https://video.example/unrelated-series",
                    "title": "相关但不是用户指定的视频合集",
                    "media_kind": "video_collection",
                }
            ]
        }

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)

    result = asyncio.run(
        planner.planning_assistant(
            "arbitrary-workspace",
            object(),
            {
                "message": "保留文字资料，并使用 BV1ib411t7YR 作为视频素材，请继续找补充资料。",
                "search_resources": True,
            },
        )
    )

    annotation = result["resources"][0]
    assert annotation["url"] == "https://www.bilibili.com/video/BV1ib411t7YR/"
    assert annotation["direct_user_reference"] is True
    assert annotation["reference_retained"] is True
    assert annotation["blocked"] is True
    assert annotation["selectable"] is False
    assert annotation["block_reason"] == "notes_only_media_contract"
    assert result["candidate_page"]["items"] == []
    assert result["candidate_page"]["total_count"] == 0
    assert result["candidate_page"]["qualified_candidate_count"] == 0
    assert result["candidate_page"]["selected_count"] == 0
    assert result["mutated"] is False


def test_malformed_exact_identifier_does_not_trigger_approximate_search(monkeypatch):
    provider = _FakeProvider(
        {
            "known_facts": {"objective": "学习指定视频", "named_materials": ["BV12345"]},
            "search_queries": [{"query": "BV12345", "reason": "查找视频"}],
        }
    )
    searched = False

    async def fake_search(*_args, **_kwargs):
        nonlocal searched
        searched = True
        return {"results": [{"url": "https://example.org/guess", "title": "相似视频"}]}

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    result = asyncio.run(
        planner.planning_assistant(
            "arbitrary-workspace",
            object(),
            {"message": "请使用 BV12345 这个视频", "search_resources": True},
        )
    )
    assert searched is False
    assert result["resources"] == []
    assert result["next_step"] == "choose_source"
    assert "无法解析资源标识" in result["reply"]


def test_provider_named_url_is_not_promoted_to_user_direct_reference(monkeypatch):
    provider = _FakeProvider(
        {
            "known_facts": {
                "objective": "学习一个主题",
                "named_materials": ["https://model.example/fabricated"],
            },
            "search_queries": [{"query": "主题公开资料", "reason": "搜索"}],
        }
    )

    async def fake_search(*_args, **_kwargs):
        return {"results": [{"url": "https://model.example/fabricated", "title": "搜索候选"}]}

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    result = asyncio.run(
        planner.planning_assistant(
            "arbitrary-workspace",
            object(),
            {"message": "请帮我找一个主题的资料", "search_resources": True},
        )
    )
    assert result["resources"] == []
    assert any("已过滤" in warning for warning in result["warnings"])


def test_exact_reference_without_companion_request_skips_generic_search(monkeypatch):
    provider = _FakeProvider(
        {
            "known_facts": {"objective": "使用指定视频", "named_materials": ["BV1ib411t7YR"]},
            "search_queries": [{"query": "相似视频", "reason": "模型建议"}],
        }
    )
    searched = False

    async def fake_search(*_args, **_kwargs):
        nonlocal searched
        searched = True
        return {"results": []}

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    result = asyncio.run(
        planner.planning_assistant(
            "arbitrary-workspace",
            object(),
            {
                "message": "使用 BV1ib411t7YR 作为视频，文字资料仍选择已有的第二份，不要找相似视频替换。",
                "search_resources": True,
            },
        )
    )
    assert searched is False
    assert result["queries"] == []
    annotation = result["resources"][0]
    assert annotation["url"] == "https://www.bilibili.com/video/BV1ib411t7YR/"
    assert annotation["reference_retained"] is True
    assert annotation["blocked"] is True
    assert annotation["selectable"] is False
    assert result["candidate_page"]["items"] == []
    assert result["candidate_page"]["selected_count"] == 0


def test_direct_document_duplicate_triggers_bounded_supplement_and_reports_published_new_count(monkeypatch):
    workspace_id = "direct-duplicate-supplement"
    direct = _direct_document("duplicate")
    provider = _FakeProvider(
        {
            "known_facts": {"objective": "学习线性代数", "named_materials": [direct["url"]]},
            "search_queries": [],
        }
    )
    calls: list[str] = []

    async def fake_search(query, *, max_results):
        calls.append(query)
        if len(calls) == 1:
            return {
                "results": [
                    {
                        "url": direct["url"],
                        "title": "搜索再次返回线性代数用户指定讲义",
                        "media_kind": "document",
                        "snippet": "duplicate direct reference",
                    },
                    {
                        "url": "https://www.zhihu.com/question/123456",
                        **_comprehensive_note("https://notes.example/duplicate", index=1),
                    },
                ]
            }
        return {
            "results": [
                _comprehensive_note(f"https://supplement.example/{len(calls)}", index=len(calls)),
            ]
        }

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        planner.planning_assistant(
            workspace_id,
            object(),
            {
                "message": f"使用文档 {direct['url']} 学习线性代数，请继续找配套文字讲义",
                "resource_search_authorized": True,
                "page_size": 3,
            },
        )
    )

    assert len(calls) >= 2
    page = result["candidate_page"]
    assert page["total_count"] == 3
    assert page["new_candidate_count"] == 2
    assert len({item["stable_id"] for item in page["items"]}) == 3
    assert "2 份新的" in result["reply"]


def test_provider_videos_cannot_fill_an_explicit_document_page(monkeypatch):
    workspace_id = "explicit-document-count-priority"
    direct = _direct_document("policy")
    provider = _FakeProvider(
        {
            "known_facts": {"objective": "学习线性代数", "named_materials": [direct["url"]]},
            "search_queries": [],
        }
    )
    calls: list[str] = []

    async def fake_search(query, *, max_results):
        calls.append(query)
        if len(calls) == 1:
            results = [
                {
                    "url": direct["url"],
                    "title": "搜索再次返回线性代数用户指定讲义",
                    "media_kind": "document",
                    "snippet": "duplicate direct reference",
                }
            ]
            results.extend(
                {
                    "url": f"https://videos.example/requested-{index}",
                    "title": f"用户请求的第 {index} 个线性代数视频课程",
                    "media_kind": "video",
                    "snippet": "包含完整讲解和例题",
                }
                for index in range(1, 4)
            )
            results.append(_comprehensive_note("https://notes.example/flexible-initial", index=1))
            return {"results": results}
        return {
            "results": [_comprehensive_note("https://notes.example/flexible-supplement", index=2)],
        }

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        planner.planning_assistant(
            workspace_id,
            object(),
            {
                "message": f"使用文档 {direct['url']} 学习线性代数，再给我找三份配套文字讲义",
                "resource_search_authorized": True,
                "page_size": 3,
            },
        )
    )
    session_id = result["planning_session_id"]
    page = result["candidate_page"]
    items = list(page["items"])
    cursor = page.get("next_cursor")
    while cursor:
        next_page = planner._PLANNING_RESOURCE_POOL.page(workspace_id, session_id, page_size=3, cursor=cursor)
        items.extend(next_page["items"])
        cursor = next_page.get("next_cursor")

    assert len(calls) >= 2
    assert len(items) == 3
    assert page["new_candidate_count"] == 2
    assert sum(item.get("direct_user_reference") is True for item in items) == 1
    assert all(item.get("media_kind") in {"notes", "document"} for item in items)
    assert page["total_count"] == 3
    assert "2 份新的资料候选" in result["reply"]
    assert "视频/课程" not in result["reply"]


@pytest.mark.parametrize(
    ("workspace_id", "references", "expected_fresh", "expected_total"),
    [
        ("auto-companion-one", ["https://materials.example/algebra-1.pdf"], 2, 3),
        (
            "auto-companion-three",
            [
                "https://materials.example/algebra-1.pdf",
                "https://materials.example/algebra-2.pdf",
                "https://materials.example/algebra-3.pdf",
            ],
            3,
            6,
        ),
        (
            "auto-companion-four",
            [
                "https://materials.example/algebra-1.pdf",
                "https://materials.example/algebra-2.pdf",
                "https://materials.example/algebra-3.pdf",
                "https://materials.example/algebra-4.pdf",
            ],
            2,
            6,
        ),
    ],
)
def test_explicit_document_discovery_fills_three_card_pages_without_media_recipe(
    monkeypatch,
    workspace_id,
    references,
    expected_fresh,
    expected_total,
):
    provider = _FakeProvider(
        {
            "known_facts": {"objective": "按指定文档系统学习线性代数", "named_materials": references},
            "search_queries": [],
        }
    )
    searched_max_results: list[int] = []

    async def fake_search(_query, *, max_results):
        searched_max_results.append(max_results)
        return {
            "results": [
                _comprehensive_note(f"https://notes.example/companion-{index}", index=index)
                for index in range(1, 7)
            ]
        }

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        planner.planning_assistant(
            workspace_id,
            object(),
            {
                "message": "请按这些文档开始整理线性代数资料，并继续找配套讲义：" + " ".join(references),
                "resource_search_authorized": True,
                "auto_companion_resources": True,
                "page_size": 3,
            },
        )
    )
    first_page = result["candidate_page"]
    page = first_page
    items = list(first_page["items"])
    cursor = first_page.get("next_cursor")
    while cursor:
        page = planner._PLANNING_RESOURCE_POOL.page(
            workspace_id,
            result["planning_session_id"],
            page_size=3,
            cursor=cursor,
        )
        items.extend(page["items"])
        cursor = page.get("next_cursor")

    assert searched_max_results
    assert len(items) == expected_total
    assert len({item["stable_id"] for item in items}) == expected_total
    assert sum(item.get("direct_user_reference") is True for item in items) == len(references)
    assert sum(item.get("direct_user_reference") is not True for item in items) == expected_fresh
    assert first_page["total_count"] == expected_total
    assert first_page["protected_count"] == 0
    assert len(first_page["provided_items"]) == len(references)
    assert first_page["direct_reference_count"] == len(references)
    assert first_page["new_candidate_count"] == expected_fresh
    assert len(result["resources"]) == 3
    assert sum(item.get("direct_user_reference") is True for item in items[:3]) == min(3, len(references))
    if len(references) == 4:
        assert sum(item.get("direct_user_reference") is True for item in items[3:6]) == 1


def test_rejected_initial_companion_keeps_exact_documents_and_adds_fresh_choices(monkeypatch):
    workspace_id = "auto-companion-reject-and-expand"
    references = [
        "https://materials.example/linear-algebra-a.pdf",
        "https://materials.example/linear-algebra-b.pdf",
    ]
    provider = _FakeProvider(
        {
            "known_facts": {"objective": "按指定文档系统学习线性代数", "named_materials": references},
            "search_queries": [],
        }
    )
    search_limits: list[int] = []

    async def fake_search(_query, *, max_results):
        search_limits.append(max_results)
        round_number = len(search_limits)
        return {
            "results": [
                _comprehensive_note(
                    f"https://notes.example/round-{round_number}-{index}",
                    index=f"{round_number}-{index}",
                )
                for index in range(1, max_results + 1)
            ]
        }

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)

    initial = asyncio.run(
        planner.planning_assistant(
            workspace_id,
            object(),
            {
                "message": "请使用这些文档学习线性代数，并继续找配套讲义：" + " ".join(references),
                "resource_search_authorized": True,
                "auto_companion_resources": True,
                "page_size": 3,
            },
        )
    )
    initial_items = list(initial["candidate_page"]["items"])
    note = next(item for item in initial_items if item.get("media_kind") == "document")
    rejected = planner._PLANNING_RESOURCE_POOL.reject(
        workspace_id,
        planning_session_id=initial["planning_session_id"],
        candidate_ids=note["candidate_id"],
        expected_revision=initial["revision"],
    )

    replaced = asyncio.run(
        planner.planning_assistant(
            workspace_id,
            object(),
            {
                "message": "这份笔记不满意，请换一批",
                "resource_search_authorized": True,
                "auto_companion_resources": True,
                "planning_session_id": initial["planning_session_id"],
                "revision": rejected["revision"],
                "pool_mode": "replace_unselected",
                "page_size": 3,
            },
        )
    )
    assert len(search_limits) >= 2
    assert all(limit >= 3 for limit in search_limits)
    assert replaced["candidate_page"]["total_count"] == 3
    assert replaced["candidate_page"]["protected_count"] == 0
    assert replaced["candidate_page"]["total_count"] % 3 == 0


@pytest.mark.parametrize("selected_count", [4, 5])
def test_replacement_batch_uses_multi_page_free_capacity(
    monkeypatch,
    selected_count,
):
    workspace_id = f"capacity-replacement-{selected_count}"
    seed = [
        {
            "url": f"https://materials.example/linear-algebra-{index}.pdf",
            "title": f"线性代数文档资料 {index}",
            "media_kind": "document",
            "direct_user_reference": True,
        }
        for index in range(6)
    ]
    created = planner._PLANNING_RESOURCE_POOL.upsert(workspace_id, seed, page_size=3)
    first_page = planner._PLANNING_RESOURCE_POOL.page(workspace_id, created["planning_session_id"], page_size=3)
    second_page = planner._PLANNING_RESOURCE_POOL.page(
        workspace_id,
        created["planning_session_id"],
        page_size=3,
        cursor=first_page["next_cursor"],
    )
    all_items = [*first_page["items"], *second_page["items"]]
    current = created
    for item in all_items[:selected_count]:
        current = planner._PLANNING_RESOURCE_POOL.select(
            workspace_id,
            planning_session_id=created["planning_session_id"],
            candidate_ids=item["candidate_id"],
            expected_revision=current["revision"],
        )

    provider = _FakeProvider(
        {"known_facts": {"objective": "继续完善线性代数资料", "named_materials": []}, "search_queries": []}
    )
    search_limits: list[int] = []

    async def fake_search(_query, *, max_results):
        search_limits.append(max_results)
        return {
            "results": [
                _comprehensive_note(
                    f"https://notes.example/capacity-{selected_count}-{index}", index=index
                )
                for index in range(max_results)
            ]
        }

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        planner.planning_assistant(
            workspace_id,
            object(),
            {
                "message": "保留已选线性代数资料，继续找新的配套文字笔记",
                "resource_search_authorized": True,
                "retained_candidates": all_items,
                "selected_resources": all_items[:selected_count],
                "planning_session_id": created["planning_session_id"],
                "revision": current["revision"],
                "pool_mode": "replace_unselected",
                "page_size": 3,
            },
        )
    )
    assert search_limits
    assert all(limit >= 3 for limit in search_limits)
    assert result["candidate_page"]["selected_count"] == selected_count
    assert result["candidate_page"]["total_count"] % 3 == 0, result["candidate_page"]
    assert result["candidate_page"]["total_count"] >= selected_count
    assert result["candidate_page"]["page_capacity"] >= selected_count
    assert result["candidate_page"]["page_capacity"] % 3 == 0


def test_selected_document_refinement_finds_notes_and_drops_unselected_old_cards(monkeypatch):
    provider = _FakeProvider(
        {
            "known_facts": {
                "objective": "学习线性代数",
                "purpose": "系统学习",
                "timeframe": "一个月",
                "named_materials": [],
                "mastery_criteria": "能够独立解题",
                "practice_requirements": [],
            },
            # The Provider misses the implicit follow-up on purpose. The
            # deterministic companion contract must still create the query.
            "search_queries": [],
        }
    )
    searched: list[str] = []

    async def fake_search(query, *, max_results):
        searched.append(query)
        return {
            "results": [
                _comprehensive_note(
                    f"https://notes.example/linear-algebra-companion-{index}", index=index
                )
                for index in range(1, 4)
            ]
        }

    selected = {
        "url": "https://materials.example/linear-algebra-selected.pdf",
        "title": "已选线性代数文档",
        "media_kind": "document",
        "direct_user_reference": True,
    }
    unselected = [
        {
            "url": "https://materials.example/linear-algebra-old-a.pdf",
            "title": "旧候选 A",
            "media_kind": "document",
        },
        {
            "url": "https://materials.example/linear-algebra-old-b.pdf",
            "title": "旧候选 B",
            "media_kind": "document",
        },
    ]
    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)

    message = "好了我已经选择了一份文档资料，再找配套文字笔记"
    assert planner._planning_requests_companion_search(message) is True
    assert planner._planning_requests_companion_search("我已经选择了一份文档资料") is False

    result = asyncio.run(
        planner.planning_assistant(
            "arbitrary-workspace",
            object(),
            {
                "message": message,
                "goal": "学习线性代数",
                "resource_search_authorized": True,
                "retained_candidates": [selected, *unselected],
                "selected_resources": [selected],
            },
        )
    )

    assert searched
    assert any(
        term in searched[0].casefold()
        for term in ("文字笔记", "讲义", "lecture notes", "course documents")
    )
    urls = [item["url"] for item in result["resources"]]
    assert selected["url"] in urls
    assert "https://notes.example/linear-algebra-companion-1" in urls
    assert all(item["url"] not in urls for item in unselected)
    note = next(item for item in result["resources"] if item["media_kind"] == "document")
    assert note["coverage_role"] == "companion_document"
    kept = next(item for item in result["resources"] if item["url"] == selected["url"])
    assert kept["selected"] is True
    assert result["resource_bundle"]["merge"]["retained_input_count"] == 1
    assert "已保留你选中的 1 份资料" in result["reply"]
    assert "文字笔记" in result["reply"]
    assert result["mutated"] is False


def test_companion_notes_request_does_not_fill_candidates_with_more_videos(monkeypatch):
    provider = _FakeProvider(
        {
            "known_facts": {
                "objective": "学习线性代数",
                "timeframe": "一个月",
                "named_materials": [],
            },
            "search_queries": [],
        }
    )

    async def fake_search(_query, *, max_results):
        return {
            "results": [
                {
                    "url": "https://video.example/unrelated-more-video",
                    "title": "另一份线性代数视频",
                    "media_kind": "video",
                }
            ]
        }

    selected = {
        "url": "https://materials.example/selected.pdf",
        "title": "已选线性代数文档",
        "media_kind": "document",
        "direct_user_reference": True,
    }
    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        planner.planning_assistant(
            "arbitrary-workspace",
            object(),
            {
                "message": "文档选好了，再找配套文字笔记",
                "goal": "学习线性代数",
                "resource_search_authorized": True,
                "retained_candidates": [selected],
                "selected_resources": [selected],
            },
        )
    )

    assert [item["url"] for item in result["resources"]] == [selected["url"]]
    assert "还没有找到可核对的文字笔记或讲义" in result["reply"]
    assert result["resource_bundle"]["merge"]["fresh_input_count"] == 0


def test_companion_refinement_replaces_unselected_pool_rows_and_bypasses_stale_cache(monkeypatch):
    workspace_id = "fresh-companion-replacement"
    selected = {
        "url": "https://materials.example/selected.pdf",
        "title": "已选线性代数文档",
        "media_kind": "document",
        "planning_selection_intent": "selected",
        "direct_user_reference": True,
    }
    old = {
        "url": "https://www.zhihu.com/question/old",
        "title": "旧问答笔记",
        "media_kind": "document",
        "snippet": "旧候选摘要",
    }
    created = planner._PLANNING_RESOURCE_POOL.upsert(workspace_id, [selected, old], page_size=3)
    provider = _FakeProvider({
        "known_facts": {"objective": "学习线性代数", "named_materials": []},
        "search_queries": [],
    })
    calls: list[str] = []

    async def fake_search(query, *, max_results):
        calls.append(query)
        # The first live response is the same stale candidate that was in the
        # pool.  Later diversified rounds return genuinely new URLs.
        if len(calls) == 1:
            return {
                "results": [{
                    "url": old["url"],
                    "title": old["title"],
                    "media_kind": "document",
                    "snippet": old["snippet"],
                }]
            }
        return {
            "results": [
                _comprehensive_note(f"https://notes.example/fresh-{len(calls)}", index=len(calls))
            ]
        }

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    # A stale cached response must not prevent the bounded live replacement.
    monkeypatch.setattr(
        planner,
        "_planning_search_cache_read",
        lambda _query: {"results": [{"url": old["url"], "title": old["title"], "media_kind": "document", "snippet": old["snippet"]}]},
    )
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        planner.planning_assistant(
            workspace_id,
            object(),
            {
                "message": "文档选好了，再找配套文字笔记",
                "goal": "学习线性代数",
                "resource_search_authorized": True,
                "selected_resources": [selected],
                "planning_session_id": created["planning_session_id"],
                "revision": created["revision"],
                "page_size": 3,
            },
        )
    )

    urls = [item["url"] for item in result["candidate_page"]["items"]]
    assert len(calls) >= 2
    assert old["url"] not in urls
    assert len(urls) == 3
    assert len(set(urls)) == 3
    assert result["candidate_page"]["new_candidate_count"] == 2
    assert "2 份新的" in result["reply"]
    assert len(set(calls)) >= 2
    assert calls[1] != calls[0]


def test_unverified_qa_is_rejected_but_independent_authority_qualifies(monkeypatch):
    async def fake_search(_query, *, max_results):
        return {
            "results": [
                {
                    **_comprehensive_note("https://www.zhihu.com/question/without-authority"),
                },
                {
                    **_comprehensive_note("https://university.example/question/with-authority"),
                    "authority": "某大学数学系",
                    "authority_status": "observed",
                },
            ]
        }

    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)
    resources, warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": "线性代数 配套笔记", "reason": "测试"}],
            required_media_kinds={"notes", "document"},
            max_resources=3,
        )
    )

    urls = [item["url"] for item in resources]
    assert "https://www.zhihu.com/question/without-authority" not in urls
    assert "https://university.example/question/with-authority" in urls
    assert any("过滤" in warning for warning in warnings)


def test_planner_retains_arbitrary_facts_and_searches_named_material(monkeypatch):
    provider = _FakeProvider(
        {
            "reply": "请补充你的基础和可用时间。",
            "goal_summary": "掌握流形上的通量计算",
            "known_facts": {
                "objective": "掌握流形上的通量计算",
                "purpose": "实验演示",
                "timeframe": "三天",
                "named_materials": ["Aurora Manual", "并安排计划"],
                "mastery_criteria": "能独立推导并解决两道题",
                "practice_requirements": ["完成经典练习"],
            },
            "missing_questions": ["请说明基础", "请说明时间"],
            "assessment_intent": "recognition_only",
            "search_queries": [],
        }
    )
    searched: list[str] = []

    async def fake_search(query, *, max_results):
        searched.append(query)
        return {
            "results": [
                {
                    **_comprehensive_note(
                        f"https://materials.example/aurora-{index}",
                        topic="Aurora Manual",
                        index=index,
                    ),
                    "engine": "fake",
                }
                for index in range(1, 4)
            ]
        }

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)

    result = asyncio.run(
        planner.planning_assistant(
            "arbitrary-workspace",
            object(),
            {
                "message": (
                    "我想在三天内掌握流形上的通量计算，用于实验演示；"
                    "使用\"Aurora Manual\"，掌握标准是能独立推导并解决两道题，"
                    "并完成经典练习。请帮我找公开资料。"
                ),
                "search_resources": True,
            },
        )
    )

    facts = result["known_facts"]
    assert facts["objective"] == "掌握流形上的通量计算"
    assert facts["purpose"] == "实验演示"
    assert facts["timeframe"] == "三天"
    assert "Aurora Manual" in facts["named_materials"]
    assert "并安排计划" not in facts["named_materials"]
    assert facts["mastery_criteria"]
    assert facts["practice_requirements"] == ["完成经典练习"]
    assert result["assessment_intent"] == "independent_performance"
    assert result["missing_questions"] == []
    assert "掌握标准" in result["handoff_goal"]
    assert "练习要求" in result["handoff_goal"]
    assert result["next_step"] == "show_resources"
    assert searched and "Aurora Manual" in searched[0]
    assert result["resources"][0]["url"].startswith("https://")
    assert "核对" in result["reply"] and "选择" in result["reply"]

    prompt = json.loads(provider.messages[-1]["content"])
    assert set(prompt["response_schema"]["known_facts"]) == {
        "objective",
        "purpose",
        "timeframe",
        "named_materials",
        "mastery_criteria",
        "practice_requirements",
    }


def test_planner_does_not_search_without_explicit_authorization(monkeypatch):
    provider = _FakeProvider(
        {
            "known_facts": {
                "objective": "Understand a synthetic protocol",
                "purpose": "research",
                "timeframe": "one week",
                "named_materials": ["Nebula Notes"],
                "mastery_criteria": "explain and implement it",
                "practice_requirements": ["independent exercises"],
            },
            "search_queries": [{"query": "Nebula Notes official", "reason": "source"}],
            "resource_search_authorized": True,
        }
    )
    called = False

    async def fake_search(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"results": []}

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)

    result = asyncio.run(
        planner.planning_assistant(
            "arbitrary-workspace",
            object(),
            {"message": "I will use \"Nebula Notes\" to understand a synthetic protocol."},
        )
    )

    assert called is False
    assert result["queries"] == []
    assert result["resources"] == []
    assert result["resource_search_authorized"] is False
    assert result["missing_questions"] == []


def test_planner_caps_missing_questions_to_one_blocking_fact(monkeypatch):
    provider = _FakeProvider(
        {
            "reply": "Tell me your level, hours, and source.",
            "missing_questions": ["level?", "hours?", "source?"],
            "search_queries": [],
        }
    )
    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)

    result = asyncio.run(
        planner.planning_assistant(
            "arbitrary-workspace",
            object(),
            {"message": "Please help."},
        )
    )

    assert len(result["missing_questions"]) == 1
    assert "独立" in result["missing_questions"][0]


def test_planner_drops_plan_command_from_model_material_names():
    # The screenshot flow included the command clause in the Provider's
    # ``named_materials`` array.  It is an action, not a source to search.
    assert planner._planning_material_list(
        ["考研线性代数资料", "并且做出相应计划", "请找到资料并且做出相应计划"]
    ) == ["考研线性代数资料"]


def test_pool_decision_route_does_not_require_provider_and_persists_selection(client, monkeypatch):
    workspace_id = "pool-decision-no-provider"
    pool = planner.PlanningResourcePool()
    created = pool.upsert(
        workspace_id,
        [{
            **_comprehensive_note("https://materials.example/pool-decision", topic="线性代数"),
            "title": "线性代数课程讲义",
        }],
        page_size=3,
    )
    candidate_id = created["items"][0]["candidate_id"]

    def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("pool decisions must not invoke Provider")

    monkeypatch.setattr(planner, "provider_for_session", provider_must_not_run)
    selected = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "pool_action": "select",
            "planning_session_id": created["planning_session_id"],
            "candidate_ids": [candidate_id],
            "revision": created["revision"],
        },
    )
    assert selected.status_code == 200, selected.text
    payload = selected.json()
    assert payload["provider"] == {}
    assert payload["selection"]["selected"] == [candidate_id]

    page = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={
            "pool_action": "page",
            "planning_session_id": created["planning_session_id"],
            "revision": payload["revision"],
        },
    )
    assert page.status_code == 200, page.text
    persisted = next(item for item in page.json()["resources"] if item["candidate_id"] == candidate_id)
    assert persisted["selected"] is True


def test_pool_read_rechecks_legacy_unsafe_payload_without_mutating_database(client):
    workspace_id = "pool-legacy-safety-recheck"
    pool = planner.PlanningResourcePool()
    created = pool.upsert(
        workspace_id,
        [{
            "url": "https://cdn.example/legacy-page",
            "title": "ENHYPEN 西村力站姐 Mina | 51视频网站",
            "snippet": "娱乐内容",
            "media_kind": "document",
            "query": "线性代数 课程笔记",
            "qualification_status": "qualified",
            "query_fit": True,
        }],
        page_size=3,
    )
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/planning-assistant",
        json={"pool_action": "page", "planning_session_id": created["planning_session_id"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resources"] == []
    assert payload["candidate_page"]["blocked_count"] == 1
    item = payload["candidate_page"]["blocked_items"][0]
    assert item["blocked"] is True
    assert item["qualification_status"] == "blocked"
    # This legacy row is removed by the general topic gate: its visible
    # entertainment text has no substantive overlap with the learning query.
    # It must not require a domain/title-specific blacklist to be blocked.
    assert item["safety_status"] == "safe"
    assert item["off_topic"] is True
