"""No model/network: generation budgets, lossless batches and cited merging."""
from copy import deepcopy
import json

import pytest

from refactor.learning.course import planner_agents as agents
from tests.refactor.planner.test_planner_agents import FORM


def point(title, summary, minutes=10, refs=("a",)):
    return dict(title=title, summary=summary, minutes=minutes, chunk_ids=list(refs))


def wire(points):
    return json.dumps({"points": [dict(title=p["title"], summary=p["summary"], minutes=p["minutes"], chunks=p["chunk_ids"])
                                  for p in points]}, ensure_ascii=False)


@pytest.mark.parametrize("minutes,ok", [([37, 37], False), ([37, 38], True), ([50] * 5, True), ([50] * 4 + [51], False)])
def test_planner_time_tolerance_is_inclusive_25_percent_without_rescaling(minutes, ok):
    points = [point(f"概念{i}", "可检验标准", n) for i, n in enumerate(minutes)]
    original = deepcopy(points)
    if ok:
        parsed = agents.parse_points(wire(points), {"a": "source:1"}, minutes_range=(100, 200))
        assert sum(p["minutes"] for p in parsed) == sum(minutes)
    else:
        with pytest.raises(agents.AgentFormatError, match=r"minutes deviation:.*25%"):
            agents.parse_points(wire(points), {"a": "source:1"}, minutes_range=(100, 200))
    assert points == original


@pytest.mark.parametrize("invalid", [(0, 10), (20, 10), (True, 10), [1.5, 10], [1], [1, 1201]])
def test_planner_range_rejects_invalid_host_input(invalid):
    with pytest.raises(agents.AgentFormatError, match="minutes range"):
        agents.validate_point_minutes([point("内容", "说明")], invalid)


def test_planner_request_and_parser_share_explicit_batch_limits_and_original_ids():
    module = dict(title="结构分析", summary="分析结构", minutes=[100, 200])
    plan = dict(stages=[dict(title="核心", modules=[module])])
    form = dict(FORM, level="advanced", notes="多做练习，解释每次选择的理由")
    chunks = [dict(id="original", text="材料正文", section=["基础"])]
    system, messages, aliases = agents.generate_request(form, "理解结构", plan, module, chunks,
        minutes_range=(20, 30), batch_index=2, batch_count=4, max_points=3)
    prompt = messages[0]["content"]
    assert "20–30" in prompt and "100–200" not in prompt and "第2/4批" in prompt and "最多3个" in prompt
    assert aliases == {"c1": "original"} and "多做练习，解释每次选择的理由" in prompt
    assert "零基础" in system and "比较熟练" in system and "有结果可检查" in system
    parsed = agents.parse_points(wire([point("分析", "给出理由", 25, ("c1",))]), aliases,
                                minutes_range=(20, 30), min_points=1, max_points=3)
    assert parsed[0]["chunk_ids"] == ["original"]
    default = agents.generate_request(form, "理解结构", plan, module, chunks)[1][0]["content"]
    assert "100–200" in default and "第2/4批" not in default
    with pytest.raises(agents.AgentFormatError, match="chunks"):
        agents.parse_points(wire([point("分析", "给出理由", 25, ("elsewhere",))]), aliases, min_points=1)
    with pytest.raises(agents.AgentFormatError, match="batch"):
        agents.generate_request(form, "理解", plan, module, chunks, batch_index=3, batch_count=2)


def test_planner_adaptation_reaches_outline_and_points_without_domain_templates():
    form = dict(FORM, goal="学习和声", final_goal="分析作品", level="zero", notes="只想理解原理")
    system, messages = agents.outline_request("学习和声", form, "按表单", [], [])
    assert "学习和声" in messages[0]["content"] and "只想理解原理" in messages[0]["content"]
    for prompt in (system, agents.GENERATE_SYSTEM):
        assert "更长的解释" in prompt and "跳过已会的基础定义" in prompt and "不强加实现项目" in prompt
        assert not any(word in prompt for word in ("Python", "智能体", "ReAct"))


def test_planner_batching_is_lossless_bounded_and_does_not_mutate_source():
    chunks = [dict(id="a", text=("一段文字🙂。\n" * 4200), section=["章1"], position=0),
              dict(id="b", text="下一章正文" * 301, section=["章2"], position=1),
              dict(id="c", text="结尾", section=["章3"], position=2)]
    original = deepcopy(chunks)
    batches = agents.split_module_chunks(chunks, max_chars=1000)
    assert len(batches) > 20
    assert all(0 < sum(len(c["text"]) for c in b) <= 1000 for b in batches)
    flat = [c for b in batches for c in b]
    for source in chunks:
        fragments = [c for c in flat if c["id"] == source["id"]]
        assert "".join(c["text"] for c in fragments) == source["text"]
        assert fragments[0]["fragment_start"] == 0 and fragments[-1]["fragment_end"] == len(source["text"])
        assert all(a["fragment_end"] == b["fragment_start"] for a, b in zip(fragments, fragments[1:]))
        assert all(c["section"] == source["section"] and c["position"] == source["position"] for c in fragments)
    assert chunks == original
    assert agents.split_module_chunks(chunks, max_chars=1000) == batches


def test_planner_batching_keeps_small_chunks_whole_and_rejects_ambiguous_ids():
    chunks = [dict(id="a", text="a" * 7), dict(id="b", text="b" * 6)]
    assert [[c["id"] for c in b] for b in agents.split_module_chunks(chunks, max_chars=10)] == [["a"], ["b"]]
    for bad in ([], [dict(id="a", text="")], chunks + chunks[:1]):
        with pytest.raises(ValueError):
            agents.split_module_chunks(bad)
    for cap in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            agents.split_module_chunks(chunks, max_chars=cap)


def test_planner_batch_ranges_conserve_both_endpoints_and_fail_on_impossible_budget():
    batches = [[dict(text="a" * 200)], [dict(text="b" * 100)], [dict(text="c")]]
    ranges = agents.batch_minute_ranges(batches, (31, 61))
    assert sum(r[0] for r in ranges) == 31 and sum(r[1] for r in ranges) == 61
    assert all(1 <= low <= high for low, high in ranges)
    assert ranges[0][0] > ranges[1][0] > ranges[2][0]
    with pytest.raises(agents.AgentFormatError, match="batch minutes"):
        agents.batch_minute_ranges(batches, (2, 10))


def test_planner_merge_unions_citations_counts_time_once_and_orders_by_material():
    chunks = [dict(id="s:2"), dict(id="s:10"), dict(id="s:1")]
    batches = [[point("后置练习", "完成分析任务并说明理由", 20, ("s:1",)),
                point("结构分析", "理解组成之间的关系，能够解释信息如何流动。", 12, ("s:10",))],
               [point("结构分析！", "理解组成之间的关系，能够解释信息如何流动。", 15, ("s:2",)),
                point("综合检验", "用新案例检验判断", 10, ("s:1",))]]
    original = deepcopy(batches)
    merged = agents.merge_point_batches(batches, chunks, minutes_range=(40, 50))
    assert len(merged) == 3 and sum(p["minutes"] for p in merged) == 45
    assert merged[0]["chunk_ids"] == ["s:2", "s:10"] and merged[0]["minutes"] == 15
    assert [p["title"] for p in merged] == ["结构分析", "后置练习", "综合检验"]
    assert batches == original
    with pytest.raises(agents.AgentFormatError, match="minutes deviation"):
        agents.merge_point_batches(batches, chunks, minutes_range=(100, 150))


def test_planner_merge_preserves_exercises_distinct_summaries_numbers_and_negation():
    items = [point("分析结构", "说明结构的组成和行为"), point("分析结构", "独立完成结构分析练习并检查结果"),
             point("重试次数", "最多进行3次重试"), point("重试次数", "最多进行5次重试"),
             point("等待时间", "等待2.5秒后检查结果"), point("等待时间", "等待25秒后检查结果"),
             point("执行条件", "满足条件时执行操作"), point("执行条件", "不满足条件时执行操作")]
    assert len(agents.merge_point_batches([items[:3], items[3:]], [dict(id="a")])) == len(items)
    with pytest.raises(agents.AgentFormatError, match="chunks"):
        agents.merge_point_batches([[point("未知", "无引用", refs=("foreign",))], items], [dict(id="a")])
    with pytest.raises(agents.AgentFormatError, match="empty point batch"):
        agents.merge_point_batches([items, []], [dict(id="a")])


def test_planner_malformed_citation_is_a_format_error_not_unhashable_exception():
    payload = {"points": [dict(title="坏引用", summary="说明", minutes=10, chunks=[{}])]}
    with pytest.raises(agents.AgentFormatError, match="chunks"):
        agents.parse_points(json.dumps(payload), {"a": "original"}, min_points=1)
