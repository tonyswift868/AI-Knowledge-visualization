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


def test_planner_keeps_exact_identifier_ahead_of_approximate_search(monkeypatch):
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

    assert result["resources"][0]["url"] == "https://www.bilibili.com/video/BV1ib411t7YR/"
    assert result["resources"][0]["direct_user_reference"] is True
    assert result["resources"][0]["authority_status"] == "unknown"
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
    assert result["resources"][0].get("direct_user_reference") is not True


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
    assert result["resources"][0]["url"] == "https://www.bilibili.com/video/BV1ib411t7YR/"


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
                    "url": "https://materials.example/aurora",
                    "title": "Aurora Manual",
                    "snippet": "A public reference",
                    "engine": "fake",
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
