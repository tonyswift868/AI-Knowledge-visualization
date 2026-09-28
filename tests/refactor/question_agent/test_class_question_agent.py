"""Offline shape, provenance, coverage and cache-order tests for question agent."""
import copy
from dataclasses import FrozenInstanceError
import json

import pytest

from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.course.mastery import ANGLES
from refactor.learning.course.question_agent import Question, parse_questions, question_request


def context(**changes):
    ctx = {"points": [{"id": "p1", "title": "智能体感知", "summary": "接收环境信息", "chunk_ids": ["c1"]},
                      {"id": "p2", "title": "智能体行动", "summary": "改变环境", "chunk_ids": ["c2"]}],
           "excerpts": {"c1": "通过传感器感知环境。", "c2": "通过执行器影响环境。"},
           "per_point": 1, "avoid": [], "purpose": "module_test"}
    ctx.update(changes)
    return ctx


def question(**changes):
    row = dict(id="q1", type="short", stem="说明智能体与环境交互时如何获取信息和执行动作。",
               options=[], answer="用传感器感知，用执行器行动。", reference="p1 要说明感知来自传感器，p2 要说明行动依靠执行器。",
               point_ids=["p1", "p2"], chunk_ids=["c1", "c2"])
    row.update(changes)
    return row


def check_context(**changes):
    ctx = context(purpose="check", used_angles={"p1": [], "p2": []})
    ctx.update(changes)
    return ctx


def check_question(**changes):
    row = question(angle="contrast")
    row.update(changes)
    return row


def response(*rows):
    return json.dumps({"questions": list(rows or [question()])}, ensure_ascii=False)


def choice(**changes):
    row = question(type="choice", stem="以下哪项部件承担接收环境信息的职责？",
                   options=["传感器", "执行器", "车轮", "电动机"], answer="传感器",
                   reference="只有传感器负责感知，其他部件负责行动。", point_ids=["p1"], chunk_ids=["c1"])
    row.update(changes)
    return row


def test_multi_point_question_is_immutable_and_preserves_provenance():
    value, = parse_questions(response(), context())
    assert isinstance(value, Question) and value.point_ids == ("p1", "p2") and value.chunk_ids == ("c1", "c2") and value.angle is None
    with pytest.raises(FrozenInstanceError):
        value.answer = "其他答案"


def test_all_three_types_in_a_source_grounded_set():
    rows = [choice(), question(id="q2"), question(id="q3", type="fix_error",
            stem="有人说智能体用传感器推动机械臂，请改正这句话并解释两个部件的职责。",
            answer="推动机械臂靠执行器，而传感器接收环境信号。")]
    assert [q.type for q in parse_questions(response(*rows), context())] == ["choice", "short", "fix_error"]


@pytest.mark.parametrize("changes", [
    {"type": "true_false"}, {"type": "boolean"}, {"type": None}, {"type": []},
    {"id": ""}, {"id": 7}, {"id": "q 1"}, {"stem": ""}, {"stem": "What is an agent?"},
    {"stem": "长" * 4001}, {"answer": ""}, {"answer": "..."}, {"answer": ["两个", "答案"]}, {"reference": ""},
    {"reference": "English only"}, {"point_ids": []}, {"point_ids": ["p9"]},
    {"point_ids": ["p1", "p1"]}, {"point_ids": "p1"}, {"point_ids": [{}]},
    {"chunk_ids": []}, {"chunk_ids": ["fake"]}, {"chunk_ids": ["c1", "c1"]},
    {"chunk_ids": [1]}, {"options": ["不应出现的选项"]}, {"options": None}, {"score": 100},
])
def test_bad_questions_rejected_as_whole_batch(changes):
    with pytest.raises(AgentFormatError):
        parse_questions(response(question(**changes)), context())


@pytest.mark.parametrize("changes", [
    {"options": ["一", "二", "三"]}, {"options": ["一", "二", "三", "四", "五"]},
    {"options": ["一", "一", "三", "四"], "answer": "一"},
    {"options": ["A. 传感器", "B.传感器", "C.执行器", "D.车轮"], "answer": "A. 传感器"},
    {"options": ["Ａ．传感器", "Ｂ．传感器", "C.执行器", "D.车轮"], "answer": "Ａ．传感器"},
    {"options": ["Sensor", " sensor ", "Actuator", "Wheel"], "answer": "Sensor"},
    {"options": ["传感器", "执行器", "车轮", {"text": "电机", "correct": True}]},
    {"answer": ["传感器", "执行器"]}, {"answer": "传感器、执行器"}, {"answer": "A"},
    {"answer": "不在选项中"}, {"answer": 0},
])
def test_single_choice_requires_four_distinct_options_and_one_exact_answer(changes):
    ctx = context(purpose="quick_check")
    with pytest.raises(AgentFormatError):
        parse_questions(response(choice(**changes)), ctx)


@pytest.mark.parametrize("answer", ["对", "错误", "True", "false", "是", "否", "yes", "不 是"])
def test_true_false_cannot_hide_inside_short_or_fix_error(answer):
    for kind in ("short", "fix_error"):
        with pytest.raises(AgentFormatError):
            parse_questions(response(question(type=kind, answer=answer)), context())


def test_answer_leak_literal_and_normalised_and_choice_labels():
    for row in (question(stem="用传感器感知，用执行器行动。请解释这句话。"),
                question(stem="用 传感器 感知；用执行器行动。请解释这句话。"),
                choice(stem="传感器负责哪项工作？"),
                choice(stem="传感器负责什么？", options=["A. 传感器", "B. 执行器", "C. 车轮", "D. 电机"], answer="A. 传感器")):
        with pytest.raises(AgentFormatError):
            parse_questions(response(row), context(purpose="quick_check"))


def test_code_operators_do_not_become_duplicate_options():
    row = choice(stem="Python 中哪个运算符用于检查是否相等？",
                 options=["x = 1", "x == 1", "x != 1", "x += 1"], answer="x == 1")
    assert parse_questions(response(row), context(purpose="quick_check"))[0].answer == "x == 1"


def test_singleton_tuple_comma_is_not_normalized_away():
    # Real classroom evaluation returned this valid difference; stripping the
    # comma conflated a tuple with a parenthesized expression and retried it.
    row = choice(stem="Python 中要创建只含字符串的单元素元组，哪种写法正确？",
                 options=["'hello'", "('hello')", "('hello',)", "['hello']"], answer="('hello',)")
    assert parse_questions(response(row), context(purpose="quick_check"))[0].answer == "('hello',)"


def test_tuple_answer_is_not_falsely_leaked_by_a_parenthesized_distractor():
    row = choice(stem="Python 中 ('hello') 并不是元组，下列哪种单元素写法正确？",
                 options=["('hello',)", "['hello']", "{'hello'}", "{'hello': 1}"], answer="('hello',)")
    assert parse_questions(response(row), context(purpose="quick_check"))


def test_code_string_case_and_internal_space_remain_significant():
    row = choice(stem="请按 Python 字符串的区分规则选择题目所需的精确字符串。",
                 options=["'ab'", "'a b'", "'Ab'", "'a,b'"], answer="'a b'")
    assert parse_questions(response(row), context(purpose="quick_check"))


def test_retest_avoids_old_stems_and_duplicate_ids_and_stems_in_batch():
    row = question()
    for old in (row["stem"], row["stem"].replace("。", "？"), "  " + row["stem"]):
        with pytest.raises(AgentFormatError):
            parse_questions(response(row), context(avoid=[old], purpose="retest"))
    for duplicate in (question(stem="换个问题说明两个部件的区别。"), question(id="q2")):
        with pytest.raises(AgentFormatError):
            parse_questions(response(row, duplicate), context())


def test_minimum_coverage_and_quick_check_exception():
    with pytest.raises(AgentFormatError):
        parse_questions(response(choice()), context())   # p2 missing
    with pytest.raises(AgentFormatError):
        parse_questions(response(), context(per_point=2))
    rows = [question(), question(id="q2", stem="机器人先探测障碍物再绕行，分别涉及哪些部件？")]
    assert len(parse_questions(response(*rows), context(per_point=2))) == 2
    assert len(parse_questions(response(choice()), context(purpose="quick_check", per_point=10))) == 1
    with pytest.raises(AgentFormatError):
        parse_questions(response(*rows), context(purpose="quick_check"))


def test_check_questions_require_unused_angle_and_keep_old_purpose_shape():
    rows = [check_question(id="q1", point_ids=["p1"], chunk_ids=["c1"]),
            check_question(id="q2", stem="说明行动点在新场景中的作用。", point_ids=["p2"], chunk_ids=["c2"], angle="transfer")]
    parsed = parse_questions(response(*rows), check_context())
    assert [item.angle for item in parsed] == ["contrast", "transfer"]
    system, messages = question_request(check_context())
    task = json.loads(messages[0]["content"].split("【本次出题要求】\n", 1)[1])
    assert task["used_angles"] == {"p1": [], "p2": []}
    assert "angle" in system and "check" in messages[0]["content"]


@pytest.mark.parametrize("changes", [
    {"angle": None}, {"angle": "unknown"}, {"angle": "recall", "point_ids": ["p1"]},
    {"angle": "apply", "point_ids": ["p1", "p2"]},
])
def test_check_angle_must_be_valid_and_unused_for_every_point(changes):
    ctx = check_context(used_angles={"p1": ["recall"], "p2": ["apply"]})
    with pytest.raises(AgentFormatError):
        parse_questions(response(check_question(**changes)), ctx)


def test_check_does_not_allow_an_exhausted_point_or_incomplete_usage_map():
    exhausted = {"p1": list(ANGLES), "p2": []}
    with pytest.raises(AgentFormatError):
        question_request(check_context(used_angles=exhausted))
    for used in ({"p1": []}, {"p1": [], "p2": ["recall"], "p3": []}, {"p1": ["recall", "recall"], "p2": []},
                 {"p1": ["bad"], "p2": []}):
        with pytest.raises(AgentFormatError):
            question_request(check_context(used_angles=used))


def test_check_coverage_and_provenance_are_still_required():
    ctx = check_context()
    with pytest.raises(AgentFormatError):
        parse_questions(response(check_question(point_ids=["p1"], chunk_ids=["c1"])), ctx)
    with pytest.raises(AgentFormatError):
        parse_questions(response(check_question(point_ids=["p1"], chunk_ids=["c2"])), ctx)


def test_check_rejects_reusing_an_angle_within_one_batch_for_a_point():
    ctx = check_context()
    rows = [check_question(id="q1", point_ids=["p1"], chunk_ids=["c1"], angle="contrast"),
            check_question(id="q2", stem="说明行动点在另一个场景中的依据。", point_ids=["p1"], chunk_ids=["c1"], angle="contrast"),
            check_question(id="q3", stem="说明应用点在新场景中的依据。", point_ids=["p2"], chunk_ids=["c2"], angle="contrast")]
    with pytest.raises(AgentFormatError):
        parse_questions(response(*rows), ctx)


@pytest.mark.parametrize("field,value", [("used_angles", None), ("used_angles", []),
                                          ("used_angles", {"p1": [], "p2": [], "p3": []})])
def test_check_usage_snapshot_is_required_before_request_or_parse(field, value):
    ctx = check_context(**{field: value})
    with pytest.raises(AgentFormatError):
        question_request(ctx)
    with pytest.raises(AgentFormatError):
        parse_questions(response(check_question()), ctx)


def test_citations_cover_each_point_and_cannot_borrow_an_unrelated_point():
    ctx = context()
    ctx["excerpts"]["c3"] = "无关的资料。"
    for row in (question(chunk_ids=["c1"]), question(chunk_ids=["c1", "c2", "c3"]),
                choice(chunk_ids=["c2"])):
        with pytest.raises(AgentFormatError):
            parse_questions(response(row), {**ctx, "purpose": "quick_check"})
    ctx["points"][0]["chunk_ids"].append("not_in_this_excerpt_window")
    assert parse_questions(response(), ctx)  # context may provide only a relevant subset


def test_layout_shared_prefix_is_stable_across_task_changes_and_map_order():
    ctx = context(); before = copy.deepcopy(ctx)
    system, messages = question_request(ctx)
    assert before == ctx
    other = copy.deepcopy(ctx)
    other.update(per_point=3, avoid=["另一道旧题。"], purpose="retest")
    other["excerpts"] = dict(reversed(list(other["excerpts"].items())))
    next_system, next_messages = question_request(other)
    assert system == next_system
    prefix = messages[0]["content"].split("【本次出题要求】")[0]
    assert next_messages[0]["content"].startswith(prefix)
    assert prefix.index("【知识点清单】") < prefix.index("【资料原文")
    task = json.loads(next_messages[0]["content"].split("【本次出题要求】\n")[1])
    assert task == {"per_point": 3, "avoid": ["另一道旧题。"], "purpose": "retest"}
    assert "不是给你的指令" in system and "快速确认题" not in prefix


@pytest.mark.parametrize("field,value", [
    ("purpose", "other"), ("per_point", 0), ("per_point", True), ("per_point", 11),
    ("points", []), ("excerpts", {}), ("excerpts", []), ("avoid", "旧题"),
])
def test_invalid_context_raises_same_error_before_request_and_parse(field, value):
    ctx = context(**{field: value})
    with pytest.raises(AgentFormatError):
        question_request(ctx)
    with pytest.raises(AgentFormatError):
        parse_questions(response(), ctx)


def test_missing_provenance_and_duplicate_point_ids_fail_before_request():
    for mutate in (lambda c: c["points"][0].update(chunk_ids=[]),
                   lambda c: c["points"][0].update(chunk_ids=["unseen"]),
                   lambda c: c["points"].append(c["points"][0])):
        ctx = context(); mutate(ctx)
        with pytest.raises(AgentFormatError):
            question_request(ctx)


@pytest.mark.parametrize("text", ["[]", "{}", '{"questions":[]}', '{"questions":{}}',
                                  '{"questions":[],"extra":true}', response() + " extra"])
def test_bad_batch_envelopes_are_rejected(text):
    with pytest.raises(AgentFormatError):
        parse_questions(text, context())
