import pytest

from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.faults import model_fault


def test_intent169_agent_format_preserves_intent_user_message():
    error = ModelError("AGENT_FORMAT", "没听懂，换个说法。")
    fault = model_fault(error, "deepseek")
    assert fault["category"] == "format"
    assert fault["message"] == "没听懂，换个说法。"
    assert "软件出了点问题" not in fault["message"]
    assert "不是网络" not in fault["message"]
    assert [action["id"] for action in fault["actions"]] == ["retry"]


def test_intent169_other_agent_format_keeps_its_specific_message():
    error = ModelError("AGENT_FORMAT", "新增内容的生成结果格式不对，已重试一次仍不行。")
    fault = model_fault(error, "deepseek")
    assert fault["category"] == "format"
    assert fault["message"] == error.message
    assert "没听懂" not in fault["message"]


def test_intent169_unclassified_internal_error_is_not_mapped_to_misunderstood():
    fault = model_fault(ValueError("internal parser detail"), "deepseek")
    assert fault["category"] == "software"
    assert "没听懂" not in fault["message"]
    assert "internal parser detail" not in fault["message"]
