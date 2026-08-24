from __future__ import annotations

import asyncio

import pytest

from app import generic_knowledge_api as planner


def _direct_video() -> dict[str, object]:
    return planner._planning_direct_resource_candidates(
        "请保留 https://www.bilibili.com/video/BV1ib411t7YR/ 作为学习入口"
    )[0]


def _complete_notes(topic: str, suffix: str = "") -> dict[str, object]:
    return {
        "url": f"https://notes.example/{topic.casefold().replace(' ', '-')}/{suffix or 'course'}",
        "title": f"{topic} Lecture Notes: Chapters 1-8",
        "snippet": (
            f"{topic}: chapter outline and section summaries; definitions, derivations, "
            "worked examples, formulas, exercises, and review checklist."
        ),
        "media_kind": "document",
        "query": f"{topic} notes",
    }


def _search_patch(monkeypatch, rows):
    async def fake_search(_query: str, *, max_results: int):
        return {"results": list(rows)}

    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)


def test_direct_video_is_exact_but_mixed_provider_videos_are_rejected(monkeypatch):
    direct = _direct_video()
    complete = _complete_notes("Aurora Protocol")
    _search_patch(
        monkeypatch,
        [
            {
                "url": "https://video.example/aurora-full",
                "title": "Aurora Protocol Complete Course Playlist",
                "snippet": "All chapters and worked examples in a full video collection.",
                "media_kind": "video_collection",
                "rank": 1,
            },
            {
                "url": "https://video.example/aurora-short",
                "title": "Aurora Protocol Lecture Video",
                "snippet": "Definitions and examples in one video.",
                "media_kind": "video",
                "rank": 2,
            },
            complete,
            {
                "url": "https://notes.example/aurora/partial",
                "title": "Aurora Protocol Note Fragment",
                "snippet": "A brief definition and one example.",
                "media_kind": "document",
            },
        ],
    )

    resources, warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": "Aurora Protocol notes", "reason": "notes"}],
            max_resources=3,
            anchor_candidates=[direct],
        )
    )

    assert [item["url"] for item in resources] == [complete["url"]]
    assert all(item["media_kind"] in {"notes", "document"} for item in resources)
    assert any("资料类型不符" in warning for warning in warnings)
    normalized_direct = planner._planning_normalize_resource(direct)
    assert normalized_direct["url"] == "https://www.bilibili.com/video/BV1ib411t7YR/"
    assert normalized_direct["direct_user_reference"] is True


def test_recheck_blocks_all_video_rows_including_legacy_direct_anchor():
    direct = planner._planning_normalize_resource(_direct_video())
    page = planner._planning_recheck_pool_page(
        {
            "page_size": 3,
            "total_count": 3,
            "qualified_candidate_count": 3,
            "new_candidate_count": 2,
            "items": [
                direct,
                {
                    "candidate_id": "selected-video",
                    "stable_id": "selected-video",
                    "url": "https://video.example/selected",
                    "title": "Aurora Protocol Selected Video",
                    "snippet": "Definitions, derivations, and worked examples.",
                    "media_kind": "video",
                    "selection_state": "selected",
                    "selected": True,
                    "query": "Aurora Protocol notes",
                    "qualification_status": "qualified",
                },
                _complete_notes("Aurora Protocol", "accepted"),
            ],
        }
    )
    assert [item["url"] for item in page["items"]] == ["https://notes.example/aurora-protocol/accepted"]
    assert page["blocked_count"] == 2
    assert all(item["block_reason"] == "notes_only_media_contract" for item in page["blocked_items"])


@pytest.mark.parametrize("topic", ["Aurora Protocol", "Quartz Weaving"])
def test_comprehensive_note_gate_is_topic_neutral(monkeypatch, topic):
    _search_patch(monkeypatch, [_complete_notes(topic)])
    resources, _warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": f"{topic} notes", "reason": "notes"}],
            max_resources=3,
            anchor_candidates=[
                {
                    "url": f"https://anchor.example/{topic.casefold().replace(' ', '-')}",
                    "title": f"{topic} Selected Resource",
                    "direct_user_reference": True,
                }
            ],
        )
    )
    assert len(resources) == 1
    assert resources[0]["query_fit"] is True
    assert resources[0]["note_quality_status"] == "qualified"


def test_short_pool_reports_honest_page_shortfall():
    page = planner._planning_recheck_pool_page(
        {
            "page_size": 3,
            "total_count": 2,
            "qualified_candidate_count": 2,
            "new_candidate_count": 1,
            "items": [
                planner._planning_normalize_resource(_direct_video()),
                planner._planning_normalize_resource(_complete_notes("Quartz Weaving")),
            ],
        }
    )
    assert page["page_shortfall_count"] == 2
    assert page["page_complete"] is False
    assert len(page["items"]) == 1


def test_direct_video_is_not_a_planning_resource_or_handoff_candidate():
    direct = planner._planning_direct_resource_candidates(
        "请使用 https://www.youtube.com/watch?v=video-only"
    )[0]
    assert planner._planning_video_candidate(direct) is True
    checked = planner._planning_recheck_pool_page(
        {
            "page_size": 3,
            "total_count": 1,
            "qualified_candidate_count": 1,
            "items": [
                {
                    **planner._planning_normalize_resource(direct),
                    "selection_state": "anchor",
                    "selected": True,
                }
            ],
        }
    )
    assert checked["items"] == []
    assert checked["blocked_items"][0]["block_reason"] == "notes_only_media_contract"


def test_provider_queries_are_rewritten_to_notes_docs_only():
    queries = planner._planning_queries(
        {
            "search_queries": [
                {"query": "Quartz Weaving video playlist", "reason": "provider"},
            ]
        },
        {"objective": "Quartz Weaving"},
        True,
    )
    assert len(queries) == 1
    query = queries[0]["query"].casefold()
    assert "video" not in query
    assert "playlist" not in query
    assert any(term in query for term in ("notes", "lecture", "document", "讲义", "笔记", "文档"))


@pytest.mark.parametrize(
    ("topic", "requested_anchor"),
    [
        ("Aurora Protocol", "Aurora Protocol state transitions and invariants"),
        ("Quartz Weaving", "Quartz Weaving patterns, proofs, and worked examples"),
    ],
)
def test_explicit_topic_anchor_outranks_bare_direct_url_and_provider_echo(topic, requested_anchor):
    direct = planner._planning_direct_resource_candidates(
        "https://www.bilibili.com/video/BV1ys411472E"
    )[0]
    server_anchor = planner._planning_server_search_anchor(
        direct_candidates=[direct],
        requested_anchor=requested_anchor,
        facts={},
    )
    topic_seed = planner._planning_search_topic_seed(
        [{"query": "com selected resource BV1ys411472E official source"}],
        requested_anchor=requested_anchor,
        objective="",
        message=f"请为 {topic} 找完整讲义",
        selected_resources=[direct],
    )

    assert topic.casefold() in server_anchor.casefold()
    assert topic.casefold() in topic_seed.casefold()
    assert "bv1ys411472e" not in server_anchor.casefold()
    assert topic_seed.casefold() != "com"

    generic_direct = planner._planning_direct_resource_candidates(
        "https://example.org/video/123"
    )[0]
    generic_anchor = planner._planning_server_search_anchor(
        direct_candidates=[generic_direct],
        requested_anchor=requested_anchor,
        facts={},
    )
    assert topic.casefold() in generic_anchor.casefold()
    assert "example.org" not in generic_anchor.casefold()

    public_queries = planner._planning_anchor_queries(
        [
            {
                "query": (
                    "我已经选择 https://www.bilibili.com/video/BV1ys411472E 作为纲领，"
                    f"请为 {topic} 找视频合集"
                ),
                "reason": "provider echo",
            }
        ],
        topic_seed,
    )
    assert len(public_queries) == 1
    public_query = public_queries[0]["query"].casefold()
    assert topic.casefold() in public_query
    assert "https://" not in public_query
    assert "www." not in public_query
    assert "bv1ys411472e" not in public_query
    assert "作为纲领" not in public_query
    assert "我已经选择" not in public_query
    assert "视频" not in public_query
