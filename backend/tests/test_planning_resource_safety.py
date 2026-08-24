from __future__ import annotations

import asyncio

from app import generic_knowledge_api as planner


def _comprehensive_note(url: str, topic: str = "线性代数") -> dict[str, object]:
    return {
        "url": url,
        "title": f"{topic} Comprehensive Lecture Notes Chapters 1-8",
        "snippet": (
            f"{topic}: chapter outline and section summaries; definitions, derivations, "
            "worked examples, formulas, exercises, and review checklist."
        ),
        "media_kind": "document",
        "comprehensive": True,
        "sections": ["Foundations", "Applications", "Exercises"],
    }


def test_unsafe_and_off_topic_rows_are_not_qualified(monkeypatch):
    async def fake_search(_query, *, max_results):
        return {
            "results": [
                {
                    "url": "https://spam.example/51",
                    "title": "ENHYPEN 西村力站姐 Mina | 51视频网站",
                    "snippet": "娱乐内容",
                    "media_kind": "document",
                },
                {
                    "url": "https://unsafe.example/live",
                    "title": "成人视频直播",
                    "snippet": "与学习无关的露骨内容",
                    "media_kind": "video",
                },
                {
                    "url": "https://unrelated.example/course",
                    "title": "烘焙课程完整视频合集",
                    "snippet": "面包和甜点制作",
                    "media_kind": "video_collection",
                },
                _comprehensive_note("https://math.example/linear-algebra"),
            ]
        }

    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)
    resources, warnings = asyncio.run(
        planner._planning_search_resources(
            [{"query": "线性代数 课程 笔记", "reason": "test"}], max_resources=3
        )
    )
    urls = [item["url"] for item in resources]
    assert urls == ["https://math.example/linear-algebra"]
    assert any("不安全内容" in warning or "主题不相关" in warning for warning in warnings)


def test_generic_media_words_do_not_create_topic_fit():
    candidate = {"title": "娱乐视频资料合集", "snippet": "明星资讯", "media_kind": "video_collection"}
    assert planner._planning_query_terms("公开视频课程资料") == []
    assert planner._planning_query_fit(candidate, "公开视频课程资料") is False


def test_note_request_rejects_video_and_single_video_is_explicitly_partial():
    video = {
        "url": "https://www.bilibili.com/video/BV1ib411t7YR/",
        "title": "线性代数单段讲解",
        "snippet": "矩阵与向量",
        "media_kind": "video",
    }
    normalized = planner._planning_normalize_resource(video)
    assert normalized["coverage_status"] == "partial"
    assert normalized["coverage_type"] == "segment"
    assert normalized["segment_metadata"]["is_segment"] is True


def test_collection_ranks_above_same_course_segment_and_direct_reference_survives():
    collection = planner._planning_normalize_resource(
        {
            "url": "https://www.bilibili.com/medialist/play/ml-linear",
            "title": "线性代数完整播放列表",
            "snippet": "从基础到应用",
        }
    )
    segment = planner._planning_normalize_resource(
        {
            "url": "https://www.bilibili.com/video/BV1ib411t7YR/",
            "title": "线性代数第 1 讲",
            "snippet": "向量基础",
        }
    )
    assert collection["rank_score"] > segment["rank_score"]
    assert collection["coverage_status"] == "complete"
    assert planner._planning_direct_resource_candidates("使用 BV1ib411t7YR")


def test_opaque_or_generic_queries_cannot_authorize_arbitrary_candidates():
    unrelated = {
        "url": "https://example.test/entertainment",
        "title": "公开娱乐学习资料",
        "snippet": "明星资讯和综艺视频",
    }
    assert planner._planning_query_fit(unrelated, "公开视频课程资料") is False
    assert planner._planning_query_fit(unrelated, "BV1AbCdEfGhI 视频资料") is False
    assert planner._planning_query_fit(unrelated, "已选资料 指定主题") is False


def test_topic_fit_requires_more_than_one_shared_generic_fragment():
    classical = {
        "url": "https://physics.example/classical",
        "title": "牛顿力学入门",
        "snippet": "经典力学与运动定律",
    }
    quantum = {
        "url": "https://physics.example/quantum",
        "title": "量子力学入门",
        "snippet": "波函数与量子态",
    }
    assert planner._planning_query_fit(classical, "量子力学 课程") is False
    assert planner._planning_query_fit(quantum, "量子力学 课程") is True


def test_scam_and_percent_encoded_adult_signals_are_blocked_generically():
    assert planner._planning_candidate_safety({"title": "刷单返利高收益", "url": "https://example.test/job"})[0] is False
    assert planner._planning_candidate_safety({"title": "crypto scam giveaway", "url": "https://example.test/invest"})[0] is False
    assert planner._planning_candidate_safety({"title": "resource", "url": "https://example.test/%E6%88%90%E4%BA%BA%E8%A7%86%E9%A2%91"})[0] is False


def test_unverified_collection_metadata_cannot_promote_an_ordinary_page():
    watch = planner._planning_normalize_resource({
        "url": "https://video.example/watch/episode-1",
        "title": "线性代数第一讲",
        "media_kind": "video_collection",
    })
    opaque = planner._planning_normalize_resource({
        "url": "https://example.org/recommended-resource",
        "title": "线性代数课程",
        "media_kind": "video_collection",
    })
    assert watch["media_kind"] == "video"
    assert watch["coverage_status"] == "partial"
    assert opaque["media_kind"] == "unknown"
    assert opaque["coverage_status"] == "not_applicable"


def test_explicit_search_denial_wins_over_stale_true_alias():
    body = {"resource_search_authorized": False, "allow_resource_search": True}
    assert planner._planning_search_authorized(body, "帮我找资料", {}) is False


def test_pool_projection_excludes_blocked_rows_without_dropping_safe_user_selection():
    page = planner._planning_recheck_pool_page({
        "page_size": 3,
        "total_count": 3,
        "qualified_candidate_count": 3,
        "new_candidate_count": 3,
        "items": [
            {
                "candidate_id": "selected-safe",
                "title": "用户已选的线性代数课程",
                "snippet": "线性代数章节大纲、定义、推导、公式、例题、练习与复习清单。",
                "media_kind": "document",
                "comprehensive": True,
                "sections": ["基础", "应用", "练习"],
                "selection_state": "anchor",
                "selected": True,
            },
            {
                "candidate_id": "fresh-safe",
                "title": "线性代数课程讲义",
                "snippet": "线性代数章节大纲、定义、推导、公式、例题、练习与复习清单。",
                "media_kind": "document",
                "comprehensive": True,
                "sections": ["基础", "应用", "练习"],
                "query": "线性代数课程讲义",
            },
            {
                "candidate_id": "off-topic",
                "title": "娱乐站点明星视频",
                "snippet": "综艺和娱乐新闻",
                "query": "线性代数课程讲义",
            },
        ],
    })
    assert [item["candidate_id"] for item in page["items"]] == ["selected-safe", "fresh-safe"]
    assert page["total_count"] == 2
    assert page["qualified_candidate_count"] == 2
    assert page["held_incomplete_count"] == 2
    assert page["blocked_count"] == 1
    assert page["blocked_items"][0]["candidate_id"] == "off-topic"
