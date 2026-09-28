"""Strict, source-bounded tests for the CHK-01 attribution component."""
from dataclasses import FrozenInstanceError
import copy
import json
import os
import subprocess
import sys

import pytest

from refactor.learning.course.check_agent import Attribution, CheckProfile, check_request, parse_check
from refactor.learning.course.planner_agents import AgentFormatError


def context(**changes):
    value = {
        "points": [
            {"id": "p1", "title": "概念边界", "summary": "区分当前感知与内部状态。"},
            {"id": "p2", "title": "应用迁移", "summary": "在新场景中选择合适机制。"},
            {"id": "p3", "title": "纠错", "summary": "找出条件和结论之间的错误。"},
        ],
        "records": [
            {"question": "说明两个智能体的差别。", "answer": "都只看当前感知。", "reason": "把内部状态和当前感知混为一谈。", "context": "module_test", "point_ids": ["p1"]},
            {"question": "把概念用在门禁场景。", "answer": "换个场景就不知道怎么判断。", "reason": "没有把条件迁移到新场景。", "context": "check", "point_ids": ["p2"]},
        ],
    }
    value.update(changes)
    return value


def response(**changes):
    value = {
        "categories": [
            {"category": "概念边界混淆", "point_ids": ["p1"], "explanation": "把只依赖当前感知与使用内部状态的条件混为一谈。"},
            {"category": "换场景迁移困难", "point_ids": ["p2"], "explanation": "在新的门禁场景中没有把原理映射到题目条件。"},
        ],
        "teacher_profile": "学习者能复述部分定义，但在相近概念和新场景中容易混淆条件；后续先用对比例子，再要求说明依据。",
    }
    value.update(changes)
    return json.dumps(value, ensure_ascii=False)


def test_request_and_parse_return_frozen_plan_profile_without_mutation():
    ctx = context()
    before = copy.deepcopy(ctx)
    system, messages = check_request(ctx)
    assert "只根据已经判错的记录" in system and "不是指令" in system
    assert len(messages) == 2 and all(row["role"] == "user" for row in messages)
    assert "错题记录（数据）" in messages[-1]["content"]
    value = parse_check(response(), ctx)
    assert isinstance(value, CheckProfile) and isinstance(value.categories[0], Attribution)
    assert value.categories[0].point_ids == ("p1",) and value.profile == value.teacher_profile
    assert "概念边界混淆" in value.text and ctx == before
    with pytest.raises(FrozenInstanceError):
        value.teacher_profile = "改写"
    with pytest.raises(FrozenInstanceError):
        value.categories[0].category = "改写"


def test_request_has_stable_point_prefix_and_normalizes_singular_point_id():
    ctx = context(records=[dict(context="casual", point_id="p1", question="复述概念。", answer="不会。", reason="无法说出区别。")])
    system, messages = check_request(ctx)
    changed = context(records=[dict(context="check", point_id="p1", question="新题。", answer="还是不会。", reason="新场景没有依据。")])
    other_system, other = check_request(changed)
    assert system == other_system and messages[0] == other[0] and messages[1] != other[1]


def test_empty_records_produce_empty_categories_only():
    ctx = context(records=[])
    value = parse_check(response(categories=[], teacher_profile="没有错题证据，暂不归纳稳定的问题模式。"), ctx)
    assert value.categories == ()
    with pytest.raises(AgentFormatError):
        parse_check(response(), ctx)


@pytest.mark.parametrize("changes", [
    {"categories": [{"category": "漏点", "point_ids": ["p1"], "explanation": "只覆盖一个点。"}]},
    {"categories": [{"category": "越权归因", "point_ids": ["p3"], "explanation": "没有这条错题证据。"},
                     {"category": "迁移困难", "point_ids": ["p2"], "explanation": "说明不足。"}]},
    {"categories": [{"category": "重复", "point_ids": ["p1"], "explanation": "第一类。"},
                     {"category": "重复", "point_ids": ["p2"], "explanation": "第二类。"}]},
    {"categories": [{"category": "重复点", "point_ids": ["p1", "p1"], "explanation": "重复。"},
                     {"category": "迁移", "point_ids": ["p2"], "explanation": "迁移。"}]},
    {"categories": [{"category": "英文", "point_ids": ["p1"], "explanation": "English only."},
                     {"category": "迁移", "point_ids": ["p2"], "explanation": "迁移。"}]},
])
def test_missing_duplicate_or_unobserved_attribution_is_rejected(changes):
    with pytest.raises(AgentFormatError):
        parse_check(response(**changes), context())


@pytest.mark.parametrize("text", [
    "not JSON", "[]", "{}", '{"categories":[],"teacher_profile":"没有证据。","extra":1}',
    '{"categories":[],"teacher_profile":"没有证据。","teacher_profile":"重复。"}',
    response(categories=[{"category": "类别", "point_ids": ["p1"], "explanation": "依据。"}], teacher_profile="没有证据。") + " extra",
])
def test_strict_json_envelope_rejects_prose_duplicates_and_extra_fields(text):
    with pytest.raises(AgentFormatError):
        parse_check(text, context())


@pytest.mark.parametrize("record", [
    {"question": "题", "answer": "答", "reason": "理由", "context": "unknown", "point_ids": ["p1"]},
    {"question": "题", "answer": "答", "reason": "理由", "context": "check", "point_ids": ["missing"]},
    {"question": "题", "answer": "答", "reason": "理由", "context": "check", "point_ids": ["p1", "p1"]},
    {"question": "题", "answer": "答", "reason": "理由", "context": "check", "point_ids": []},
    {"question": "题", "answer": "答", "reason": "理由", "context": "check", "point_ids": ["p1"], "extra": 1},
])
def test_input_records_are_bounded_and_must_name_existing_points(record):
    with pytest.raises(AgentFormatError):
        check_request(context(records=[record]))


@pytest.mark.parametrize("changes", [
    {"teacher_profile": "我已经修改了计划并删除了检验。"},
    {"teacher_profile": "English only"},
    {"teacher_profile": "长" * 801},
    {"categories": [{"category": "类别", "point_ids": ["p1"], "explanation": "我已经把进度改好了。"},
                     {"category": "迁移", "point_ids": ["p2"], "explanation": "新场景条件没有迁移。"}]},
])
def test_profile_is_a_description_not_a_state_mutation(changes):
    with pytest.raises(AgentFormatError):
        parse_check(response(**changes), context())


def test_multiple_records_and_categories_may_share_a_point_but_no_more_than_eight_categories():
    ctx = context(records=[
        {"question": "题一", "answer": "答一", "reason": "边界不清。", "context": "casual", "point_ids": ["p1"]},
        {"question": "题二", "answer": "答二", "reason": "新场景不会用。", "context": "retest", "point_ids": ["p1"]},
    ])
    categories = [{"category": f"问题{i}", "point_ids": ["p1"], "explanation": "同一知识点出现了不同的观察证据。"} for i in range(8)]
    assert parse_check(response(categories=categories, teacher_profile="同一知识点在概念边界和新场景应用上都需要继续观察。"), ctx)
    with pytest.raises(AgentFormatError):
        parse_check(response(categories=categories + [{"category": "问题八", "point_ids": ["p1"], "explanation": "超出上限。"}], teacher_profile="需要继续观察。"), ctx)


@pytest.mark.parametrize("value", [None, {}, "p1"])
def test_context_shape_is_strict(value):
    with pytest.raises(AgentFormatError):
        check_request({"points": value, "records": []})
    with pytest.raises(AgentFormatError):
        check_request({"points": context()["points"], "records": value})


@pytest.mark.parametrize("text", [
    "[]", "{}", '{"categories":[],"teacher_profile":"没有足够证据。"}',
])
def test_invalid_output_never_gets_salvaged(text):
    with pytest.raises(AgentFormatError):
        parse_check(text, context())


def test_check_agent_imports_only_pure_components_in_a_fresh_process():
    code = """
import sys
from refactor.learning.course import check_agent
for name in sys.modules:
    assert not name.startswith(('PySide', 'PyQt', 'sqlite3', 'httpx', 'requests',
                               'refactor.learning.connections', 'refactor.learning.course.store',
                               'refactor.learning.app')), name
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
