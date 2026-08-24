from __future__ import annotations

from app import generic_knowledge_api as planner


def test_csdn_article_wins_over_course_title_and_stale_video_metadata():
    candidate = planner._planning_normalize_resource(
        {
            "url": "https://blog.csdn.net/example/article/details/123456",
            "title": "线性代数课程学习笔记",
            "media_kind": "video_collection",
            # Both media metadata and coverage role are stale provenance; URL
            # evidence must make the document role canonical for the UI.
            "source_type": "video_collection",
            "coverage_role": "full_collection",
        }
    )

    assert candidate["media_kind"] == "document"
    assert candidate["coverage_role"] == "companion_document"
    assert candidate["source_type"] == "video_collection"


def test_actual_video_urls_and_collections_keep_video_classification():
    video = planner._planning_normalize_resource(
        {
            "url": "https://www.bilibili.com/video/BV1ib411t7YR/",
            "title": "线性代数课程学习笔记",
        }
    )
    collection = planner._planning_normalize_resource(
        {
            "url": "https://www.youtube.com/playlist?list=PL123",
            "title": "线性代数课程",
        }
    )

    assert video["media_kind"] == "video"
    assert video["coverage_role"] == "single_video"
    assert collection["media_kind"] == "video_collection"
    assert collection["coverage_role"] == "full_collection"


def test_visible_note_title_overrides_unverified_stale_video_collection_metadata():
    candidate = planner._planning_normalize_resource(
        {
            "url": "https://example.org/linear-algebra-notes",
            "title": "《线性代数的本质》配套学习笔记与例题讲义",
            "media_kind": "video_collection",
            "coverage_role": "full_collection",
        }
    )

    assert candidate["media_kind"] == "notes"
    assert candidate["coverage_role"] == "companion_notes"


def test_visible_video_title_cannot_satisfy_note_request_via_stale_document_metadata():
    candidate = planner._planning_normalize_resource(
        {
            "url": "https://example.org/recommended-resource",
            "title": "推荐视频：3Blue1Brown 线性代数系列课程",
            "media_kind": "document",
            "coverage_role": "companion_document",
        }
    )

    assert candidate["media_kind"] == "video_collection"
    assert candidate["coverage_role"] == "full_collection"
