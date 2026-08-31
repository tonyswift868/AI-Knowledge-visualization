from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any

import pytest

from app import generic_knowledge_api as planner


class _FakeProvider:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    async def json(self, _messages: list[dict[str, str]]) -> dict[str, Any]:
        return self.payload


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        id="candidate-recall-test-session",
        provider="openai_compatible",
        model="offline-test-model",
        base_url="https://provider.synthetic.example",
        credential_ref="offline-ref",
    )


def _topic_slug(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", topic.casefold()).strip("-")


def _direct_video(topic: str) -> dict[str, Any]:
    return {
        "url": f"https://media.synthetic.example/{_topic_slug(topic)}-course.mp4",
        "title": f"{topic} exact learner video",
        "media_kind": "video",
        "direct_user_reference": True,
    }


def _direct_document(topic: str) -> dict[str, Any]:
    return {
        "url": f"https://materials.synthetic.edu/{_topic_slug(topic)}/learner-anchor.pdf",
        "title": f"{topic} exact learner notes",
        "media_kind": "document",
        "direct_user_reference": True,
    }


def _complete_note(topic: str, suffix: str, *, short: bool = False) -> dict[str, Any]:
    body = (
        "Definitions, examples, exercises."
        if short
        else (
            f"{topic}: chapter outline and section summaries; definitions, derivations, "
            "worked examples, formulas, exercises, and review checklist."
        )
    )
    return {
        "url": f"https://notes.synthetic.example/{_topic_slug(topic)}/{suffix}",
        "title": f"{topic} Course Lecture Notes",
        "snippet": body,
        "media_kind": "document",
    }


def _patch_offline_planning(monkeypatch, payload: dict[str, Any], search):
    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_args: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: _FakeProvider(payload))
    monkeypatch.setattr(planner, "search_web", search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)


def _provider_payload(topic: str, *, queries: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "known_facts": {
            "objective": f"Learn {topic}",
            "purpose": "",
            "timeframe": "",
            "named_materials": [],
            "mastery_criteria": "Explain the core ideas",
            "practice_requirements": [],
        },
        "search_queries": queries or [
            {"query": f"{topic} lecture notes", "reason": "topic-native lane"},
            {"query": f"{topic} official course documents", "reason": "evidence-form lane"},
        ],
    }


@pytest.mark.parametrize(
    ("topic", "translated_alias"),
    [
        ("Nebula Cartography", "星云制图"),
        ("Quartz Weaving", "石英织造"),
    ],
)
def test_verbose_goal_and_direct_label_yield_three_clean_query_lanes(topic, translated_alias):
    verbose_request = f"https://media.synthetic.example/course.mp4 {topic} 课程讲义笔记"
    subject = planner._planning_search_subject(verbose_request)

    assert subject == topic
    provider_queries = [
        {"query": f"{topic} lecture notes", "reason": "topic-native"},
        {"query": f"{translated_alias} 课程讲义", "reason": "translated alias"},
        {"query": f"{topic} official PDF chapter exercises", "reason": "evidence form"},
    ]
    lanes = planner._planning_anchor_queries(provider_queries, subject)
    lane_text = [item["query"] for item in lanes]

    assert len(lane_text) == 3
    assert len(set(lane.casefold() for lane in lane_text)) == 3
    assert any(topic.casefold() in lane.casefold() for lane in lane_text)
    assert any(translated_alias in lane for lane in lane_text)
    assert any(
        re.search(r"(?:pdf|lecture|course|chapter|讲义|文档|教材)", lane, re.IGNORECASE)
        for lane in lane_text
    )
    assert all("https://" not in lane and "media.synthetic.example" not in lane for lane in lane_text)
    assert all("course.mp4" not in lane and "作为学习入口" not in lane for lane in lane_text)


@pytest.mark.parametrize("topic", ["Nebula Cartography", "Quartz Weaving"])
def test_strong_educational_pdf_with_short_snippet_qualifies(topic, monkeypatch):
    candidate = {
        **_complete_note(topic, "lecture-notes.pdf", short=True),
        "url": f"https://courses.synthetic.edu/{_topic_slug(topic)}/lecture-notes.pdf",
    }

    async def fake_search(_query: str, *, max_results: int):
        return {"results": [candidate]}

    _patch_offline_planning(monkeypatch, _provider_payload(topic), fake_search)
    resources, warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": f"{topic} lecture notes", "reason": "educational notes"}],
            max_resources=3,
            anchor_candidates=[_direct_video(topic)],
        )
    )

    assert [item["url"] for item in resources] == [candidate["url"]]
    assert resources[0]["note_quality_status"] == "qualified"
    assert resources[0]["source_class"] == "official_or_educational"
    assert not any("完整/结构化笔记证据" in warning for warning in warnings)


def test_cjk_topic_core_allows_a_course_note_but_not_a_shared_short_tail():
    assert planner._planning_query_fit(
        {"title": "星云制图课程讲义", "snippet": "章节、定义与例题。"},
        "星云制图的本质 课程讲义",
    ) is True
    assert planner._planning_query_fit(
        {"title": "经典织造课程讲义", "snippet": "章节、定义与例题。"},
        "量子织造原理 课程讲义",
    ) is False


@pytest.mark.parametrize("topic", ["Nebula Cartography", "Quartz Weaving"])
def test_direct_document_plus_two_independent_notes_publishes_one_complete_page(topic, monkeypatch):
    direct = _direct_document(topic)
    notes = [_complete_note(topic, "course-a"), _complete_note(topic, "course-b")]
    calls: list[str] = []

    async def fake_search(query: str, *, max_results: int):
        calls.append(query)
        return {"results": [*notes, {**direct, "direct_user_reference": False}]}

    _patch_offline_planning(monkeypatch, _provider_payload(topic), fake_search)
    result = asyncio.run(
        planner.planning_assistant(
            f"direct-page-{_topic_slug(topic)}",
            object(),
            {
                "message": (
                    f"保留这个精确文档：\n{direct['url']}\n请为 {topic} 找两份配套文字笔记。"
                ),
                "goal": f"Learn {topic}",
                "search_anchor": topic,
                "resource_search_authorized": True,
                "page_size": 3,
            },
        )
    )
    page = result["candidate_page"]
    items = page["items"]

    assert calls
    assert len(items) == 3
    assert page["total_count"] == 3
    assert page["page_shortfall_count"] == 0
    assert {item["url"] for item in items} == {direct["url"], notes[0]["url"], notes[1]["url"]}
    assert sum(item.get("direct_user_reference") is True for item in items) == 1
    assert all(item.get("media_kind") in {"notes", "document"} for item in items)


def test_reject_then_refill_uses_two_novel_candidates_and_never_reactivates_rejected_identity(monkeypatch):
    topic = "Nebula Cartography"
    direct = _direct_document(topic)
    first_notes = [_complete_note(topic, "first-a"), _complete_note(topic, "first-b")]
    replacement_notes = [_complete_note(topic, "replacement-a"), _complete_note(topic, "replacement-b")]
    calls = 0

    async def fake_search(_query: str, *, max_results: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"results": [*first_notes, {**direct, "direct_user_reference": False}]}
        # The rejected identity is deliberately returned again.  The pool's
        # durable exclusion history must prevent it from becoming visible.
        return {
            "results": [
                first_notes[0],
                *replacement_notes,
                {**direct, "direct_user_reference": False},
            ]
        }

    _patch_offline_planning(monkeypatch, _provider_payload(topic), fake_search)
    workspace_id = "reject-refill-generic"
    initial = asyncio.run(
        planner.planning_assistant(
            workspace_id,
            object(),
            {
                "message": f"保留文档：\n{direct['url']}\n为 {topic} 找配套文字笔记。",
                "goal": f"Learn {topic}",
                "search_anchor": topic,
                "resource_search_authorized": True,
                "page_size": 3,
            },
        )
    )
    rejected_item = next(item for item in initial["candidate_page"]["items"] if item["url"] == first_notes[0]["url"])
    rejected = planner._PLANNING_RESOURCE_POOL.reject(
        workspace_id,
        planning_session_id=initial["planning_session_id"],
        candidate_ids=rejected_item["candidate_id"],
        expected_revision=initial["revision"],
    )

    replaced = asyncio.run(
        planner.planning_assistant(
            workspace_id,
            object(),
            {
                "message": f"请为 {topic} 换两份新的配套文字笔记。",
                "goal": f"Learn {topic}",
                "search_anchor": topic,
                "selected_resources": [direct],
                "resource_search_authorized": True,
                "planning_session_id": initial["planning_session_id"],
                "revision": rejected["revision"],
                "pool_mode": "replace_unselected",
                "page_size": 3,
            },
        )
    )
    page = replaced["candidate_page"]
    urls = {item["url"] for item in page["items"]}

    assert calls >= 2
    assert len(page["items"]) == 3
    assert page["total_count"] == 3
    assert direct["url"] in urls
    assert {item["url"] for item in replacement_notes} <= urls
    assert first_notes[0]["url"] not in urls
    durable = planner._PLANNING_RESOURCE_POOL.candidates(
        workspace_id,
        initial["planning_session_id"],
        include_inactive=True,
    )
    rejected_row = next(item for item in durable if item["url"] == first_notes[0]["url"])
    assert rejected_row["selection_state"] == "rejected"
    assert {item["url"] for item in durable if item["selection_state"] != "rejected"} >= {
        direct["url"],
        replacement_notes[0]["url"],
        replacement_notes[1]["url"],
    }


def test_unsafe_off_topic_mirror_low_confidence_and_video_rows_do_not_fill(monkeypatch):
    topic = "Quartz Weaving"
    direct = _direct_video(topic)
    rows = [
        _complete_note(topic, "good"),
        {
            "url": "https://unsafe.synthetic.example/adult",
            "title": f"{topic} adult content",
            "snippet": "Pornographic adult video and nude content.",
            "media_kind": "document",
        },
        {
            "url": "https://notes.synthetic.example/unrelated",
            "title": "Unrelated Ocean Biology Course Lecture Notes",
            "snippet": "Definitions, derivations, worked examples, formulas, exercises, and review checklist.",
            "media_kind": "document",
        },
        {
            "url": "https://mirror.synthetic.example/index",
            "title": f"Mirror index: {topic} Course Lecture Notes",
            "snippet": f"Original source: {direct['url']}",
            "media_kind": "document",
        },
        {
            "url": "https://stackoverflow.com/questions/123",
            "title": f"{topic} Course Lecture Notes answer",
            "snippet": "Chapter outline, definitions, derivations, worked examples, formulas, exercises, and review checklist.",
            "media_kind": "document",
        },
        {
            "url": f"https://csdn.net/article/{_topic_slug(topic)}",
            "title": f"{topic} Course Lecture Notes blog",
            "snippet": "Chapter outline, definitions, derivations, worked examples, formulas, exercises, and review checklist.",
            "media_kind": "document",
        },
        {
            "url": f"https://video.synthetic.example/watch/{_topic_slug(topic)}",
            "title": f"{topic} full lecture video",
            "snippet": "Chapter outline, definitions, derivations, worked examples, formulas, exercises, and review checklist.",
            "media_kind": "video",
        },
    ]

    async def fake_search(_query: str, *, max_results: int):
        return {"results": rows}

    _patch_offline_planning(monkeypatch, _provider_payload(topic), fake_search)
    resources, warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": f"{topic} lecture notes", "reason": "quality gate"}],
            max_resources=3,
            required_media_kinds={"notes", "document"},
            anchor_candidates=[direct],
        )
    )
    urls = {item["url"] for item in resources}

    assert urls == {rows[0]["url"]}
    assert all("adult" not in url for url in urls)
    assert not any("unrelated" in item["title"].casefold() for item in resources)
    assert not any("mirror" in item["title"].casefold() for item in resources)
    assert not any("stackoverflow" in url or "csdn.net" in url for url in urls)
    assert not any(item.get("media_kind") == "video" for item in resources)
    assert any("已过滤" in warning for warning in warnings)


def test_duplicate_only_exhausted_search_reports_honest_shortfall(client, monkeypatch):
    topic = "Quartz Weaving"
    direct = _direct_document(topic)
    duplicate = {**direct, "direct_user_reference": False, "title": f"Duplicate {topic} notes"}
    calls = 0

    async def duplicate_search(_query: str, *, max_results: int):
        nonlocal calls
        calls += 1
        return {"results": [duplicate]}

    _patch_offline_planning(monkeypatch, _provider_payload(topic), duplicate_search)
    response = client.post(
        "/api/active-teaching/workspaces/duplicate-only-recall/planning-assistant",
        json={
            "message": f"保留文档：\n{direct['url']}\n为 {topic} 找配套文字笔记。",
            "goal": f"Learn {topic}",
            "search_anchor": topic,
            "resource_search_authorized": True,
            "page_size": 3,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    page = payload["candidate_page"]
    assert calls >= 1
    assert len(payload["resources"]) == 1
    assert payload["resources"][0]["url"] == direct["url"]
    assert page["total_count"] == 1
    assert page["page_shortfall_count"] == 2
    assert page["shortfall_count"] == 2
    assert page["page_complete"] is False
    assert "没有找到可核对" in payload["reply"] or "短缺" in payload["reply"] or page["page_shortfall_count"] == 2
