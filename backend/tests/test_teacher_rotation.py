from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.orm import TeacherMemoryORM
from app.teacher_rotation import TEACHER_ROSTER, enrich_tutor_result, grade_quiz, memory_row, public_memory, teacher_for_day


def _session():
    engine = create_engine("sqlite:///:memory:")
    TeacherMemoryORM.__table__.create(engine)
    return sessionmaker(engine, expire_on_commit=False)()


def _citation():
    return {
        "source_id": "source_course_notes",
        "chunk_id": "chunk_verified_1",
        "source_title": "课程讲义",
        "page_start": 3,
        "page_end": 3,
        "heading": "检索与核验",
        "quote": "回答必须由当前工作区资料中的可核验片段支持。",
    }


def test_daily_roster_is_stable_rotating_adult_and_avatar_decoupled():
    assert len({item["id"] for item in TEACHER_ROSTER}) == len(TEACHER_ROSTER)
    assert all(item["adult_only"] and not item["story_mode"] for item in TEACHER_ROSTER)
    assert all(item["gender"] == "woman" and item["adult_age"] >= 18 for item in TEACHER_ROSTER)
    assert all("art" not in item and "visual_asset_url" not in item for item in TEACHER_ROSTER)

    today = date(2026, 7, 16)
    avatars = ["licensed-avatar-a", "licensed-avatar-b", "licensed-avatar-c"]
    current = teacher_for_day("workspace-a", today, avatars)
    assert current == teacher_for_day("workspace-a", today, avatars)
    tomorrow = teacher_for_day("workspace-a", today + timedelta(days=1), avatars)
    assert current["id"] != tomorrow["id"]
    assert current["avatar_id"] != tomorrow["avatar_id"]
    assert current["avatar_source"] == "avatar_registry"


def test_grounded_reply_keeps_citation_alignment_and_hides_quiz_answer():
    session = _session()
    citation = _citation()
    teacher = teacher_for_day("workspace-a", date(2026, 7, 16))
    result, snapshot = enrich_tutor_result(
        session,
        workspace_id="workspace-a",
        run_id="run-a",
        lesson_title="资料检索",
        teacher=teacher,
        user_message="资料检索为什么需要核验？",
        result={"message": "先定位证据，再检查来源。", "status": "grounded", "confidence": 0.86, "citations": [citation]},
    )
    session.commit()

    assert result["citations"] == [citation]
    assert result["quick_quiz"]["citation"] == citation
    assert "correct_option_id" not in result["quick_quiz"]
    assert "pending_quizzes" not in snapshot
    assert snapshot["known_source_count"] == 1

    graded = grade_quiz(session, workspace_id="workspace-a", run_id="run-a", quiz_id=result["quick_quiz"]["quiz_id"], option_id="supported")
    assert graded["correct"] is True
    assert graded["citation"] == citation
    assert graded["memory_snapshot"]["completed_quiz_count"] == 1


def test_off_topic_reply_redirects_gently_without_false_citations():
    session = _session()
    teacher = teacher_for_day("workspace-a", date(2026, 7, 16))
    result, snapshot = enrich_tutor_result(
        session,
        workspace_id="workspace-a",
        run_id="run-a",
        lesson_title="二分查找",
        teacher=teacher,
        user_message="晚饭吃什么？",
        result={"message": "证据不足", "status": "insufficient", "confidence": 0.15, "citations": []},
    )

    assert result["status"] == "off_topic_redirect"
    assert result["citations"] == []
    assert result["quick_quiz"] is None
    assert "很有意思" in result["message"]
    assert "二分查找" in result["message"]
    assert snapshot["weak_points"] == ["二分查找"]


def test_memory_is_shared_across_rotating_teachers_but_isolated_by_workspace():
    session = _session()
    first_day = date(2026, 7, 16)
    for offset, question in enumerate(("第一个问题", "第二个问题")):
        enrich_tutor_result(
            session,
            workspace_id="workspace-a",
            run_id=f"run-{offset}",
            lesson_title="共享课程",
            teacher=teacher_for_day("workspace-a", first_day + timedelta(days=offset)),
            user_message=question,
            result={"message": "无证据", "status": "insufficient", "confidence": 0.1, "citations": []},
        )
    session.commit()

    shared = public_memory(memory_row(session, "workspace-a").memory)
    isolated = public_memory(memory_row(session, "workspace-b").memory)
    assert shared["interaction_count"] == 2
    assert shared["recent_focus"] == ["共享课程"]
    assert isolated["interaction_count"] == 0
