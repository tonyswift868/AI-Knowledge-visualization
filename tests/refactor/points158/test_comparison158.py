from copy import deepcopy

import pytest

from tests.refactor.points158.comparison158 import render_html, title_fragment_reasons, validate_regroup


def sample():
    question = {"id": "q1", "point_ids": ["old-2"], "subpoint_id": "s2", "prompt": "题目"}
    snapshot = {
        "plan_id": "plan-a",
        "protection": {"old-1": ["current_cursor"]},
        "current_position": {"state": {"index": 0}},
        "mastery_modules": {"mod-a": "learning"},
        "chunks": [
            {"id": "c1", "text": "介绍锁的基本概念。", "title": "原文 A"},
            {"id": "c2", "text": "Redis 锁的实现细节。", "title": "原文 B"},
        ],
        "modules": [{
            "module_id": "mod-a", "position": 5, "title": "分布式系统",
            "questions": [question],
            "old_points": [
                {"id": "old-1", "position": 10, "title": "旧点一", "minutes": 5,
                 "subpoints": [{"id": "s1", "title": "原概念", "minutes": 5, "chunk_ids": ["c1"],
                                "notes": [{"text": "介绍锁的基本概念。", "chunk_id": "c1"}]}]},
                {"id": "old-2", "position": 11, "title": "旧点二", "minutes": 7,
                 "subpoints": [{"id": "s2", "title": "Redis 锁", "minutes": 7, "chunk_ids": ["c2"],
                                "notes": [{"text": "Redis 锁的实现细节。", "chunk_id": "c2"}]}]},
            ],
        }],
        "credits": [{"id": "learn:s1", "credit": {"unit_id": "s1"}}],
        "question_bank": [question],
    }
    apply = {
        "plan_id": "plan-a", "complete": True, "regroup_subpoints": True,
        "modules": [{"module_id": "mod-a", "replace_point_ids": ["old-1", "old-2"],
                     "points": [{"id": "new-1", "module_id": "mod-a", "position": 10,
                                 "title": "分布式锁基础与 Redis 实现", "summary": "理解锁与实现",
                                 "structure": "concept_v1", "goal": "理解锁与实现", "minutes": 12,
                                 "chunk_ids": ["c1", "c2"], "subpoints": [
                                     {"id": "s1", "title": "锁的基本概念", "minutes": 5,
                                      "chunk_ids": ["c1"], "notes": [{"text": "介绍锁的基本概念。", "chunk_id": "c1"}]},
                                     {"id": "s2", "title": "Redis 锁实现", "minutes": 7,
                                      "chunk_ids": ["c2"], "notes": [{"text": "Redis 锁的实现细节。", "chunk_id": "c2"}]},
                                 ]}], "navigation": []}],
    }
    return snapshot, apply


def test_exactly_once_notes_chunks_minutes_and_question_assignment_are_checked():
    snapshot, apply = sample()
    report = validate_regroup(snapshot, apply)
    assert report["passed"] is True
    assert report["automatic_validation"]["passed"] is True
    assert report["manual_theme_review"]["status"] == "not_evaluated_by_automated_checker"
    assert report["modules"][0]["new_subpoints"] == 2
    assert report["modules"][0]["proposed_group_count"] == 1
    assert report["modules"][0]["generated_new_count"] == 1
    assert report["modules"][0]["retained_count"] == 1
    assert report["modules"][0]["effective_new_points"] == 2
    assert report["modules"][0]["new_minutes"] == 12
    assert report["modules"][0]["question_count"] == 1

    broken = deepcopy(apply)
    broken["modules"][0]["points"][0]["subpoints"].append(
        deepcopy(broken["modules"][0]["points"][0]["subpoints"][0]))
    report = validate_regroup(snapshot, broken)
    assert any("重复小要点" in message for message in report["issues"])

    broken = deepcopy(apply)
    broken["modules"][0]["points"][0]["subpoints"].pop()
    report = validate_regroup(snapshot, broken)
    assert any("丢失小要点" in message for message in report["issues"])
    assert any("subpoint_id 未唯一保留" in message for message in report["issues"])


def test_protected_point_is_rendered_as_kept_and_notes_have_clickable_source():
    snapshot, apply = sample()
    html = render_html(snapshot, apply)
    assert "保护中 · 换入时保留" in html
    assert "查看原文" in html
    # Effective displayed grouping omits the protected subpoint from proposal,
    # while still displaying it under the preserved old point.
    assert '"title":"分布式锁基础与 Redis 实现"' in html
    assert '"title":"旧点一"' in html


def test_store_interface_limitation_is_explicit_when_provided():
    snapshot, apply = sample()
    apply["store_interface_status"] = "Store子点ID前缀校验暂阻换入，需兼容后执行。"
    html = render_html(snapshot, apply)
    assert "Store子点ID前缀校验暂阻换入" in html


def test_manual_theme_review_is_reported_separately_from_automatic_checks():
    snapshot, apply = sample()
    apply["quality_review"] = {
        "passed": True,
        "manual": "人工逐组核对，见 review.md",
        "scope": "仅主题合理性，不代表换入可执行",
    }
    report = validate_regroup(snapshot, apply)
    assert report["automatic_validation"]["passed"] is True
    assert report["manual_theme_review"] == {
        "status": "recorded", "passed": True,
        "source": "人工逐组核对，见 review.md",
        "scope": "仅主题合理性，不代表换入可执行",
    }


def test_post_apply_questions_learning_and_protected_points_must_survive():
    before, apply = sample()
    after = deepcopy(before)
    after["modules"][0]["old_points"] = [before["modules"][0]["old_points"][0], {
        "id": "new-1", "position": 11, "title": "分布式锁基础与 Redis 实现", "minutes": 12,
        "subpoints": deepcopy(apply["modules"][0]["points"][0]["subpoints"]),
    }]
    after["modules"][0]["points"] = after["modules"][0]["old_points"]
    after["question_bank"] = [{"id": "q1", "point_ids": ["new-1"], "subpoint_id": "s2", "prompt": "题目"}]
    report = validate_regroup(before, apply, after)
    assert report["passed"] is True

    after["credits"] = []
    report = validate_regroup(before, apply, after)
    assert any("credits 改变" in message for message in report["issues"])


@pytest.mark.parametrize("title", ["1、旧标题", "**加粗残留**", "| 特性", "Zuul 是 Netfl", "标题…"])
def test_broken_fragment_titles_are_flagged(title):
    assert title_fragment_reasons(title)


def test_normal_short_technical_titles_are_not_flagged():
    assert title_fragment_reasons("分布式锁：Redis 实现") == []
    assert title_fragment_reasons("JVM 垃圾回收") == []
