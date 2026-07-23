from __future__ import annotations

from app.goal_intent import build_goal_intent_receipt


def test_explicit_goal_receipt_preserves_original_and_asks_nothing() -> None:
    original = "我要在2026年9月30日前学会图搜索，每天45分钟、每周5天，成果是一份可运行的路径规划程序。"
    normalized = {
        "goal_statement": "掌握图搜索并独立实现路径规划",
        "deadline": "2026-09-30",
        "minutes_per_day": 45,
        "days_per_week": 5,
        "expected_output": "可运行的路径规划程序",
        "success_criteria": ["通过给定地图测试"],
    }

    receipt = build_goal_intent_receipt(original, normalized=normalized)

    assert receipt.original_text == original
    assert receipt.normalized == normalized
    assert receipt.missing_critical_fields == ()
    assert receipt.clarification_questions == ()
    assert receipt.intent_confidence >= 0.9


def test_vague_goal_asks_only_the_highest_value_missing_field() -> None:
    receipt = build_goal_intent_receipt("我想学 Python")

    assert receipt.missing_critical_fields == (
        "goal_statement",
        "expected_output",
        "deadline",
        "available_time",
    )
    assert receipt.clarification_questions == ("你最终想独立完成什么具体任务？",)
    assert receipt.intent_confidence < 0.5


def test_already_answered_field_is_not_asked_repeatedly() -> None:
    receipt = build_goal_intent_receipt(
        "想掌握数据分析",
        normalized={"goal_statement": "掌握数据分析"},
        already_answered_fields=("expected_output",),
    )

    assert "goal_statement" not in receipt.missing_critical_fields
    assert "expected_output" not in receipt.missing_critical_fields
    assert receipt.clarification_questions == ("你希望在什么日期前完成？",)


def test_no_constraints_are_invented_by_receipt_policy() -> None:
    receipt = build_goal_intent_receipt(
        "我要完成一份算法报告",
        normalized={"goal_statement": "完成算法报告"},
    )

    assert "deadline" not in receipt.normalized
    assert "minutes_per_day" not in receipt.normalized
    assert "constraints" not in receipt.normalized
