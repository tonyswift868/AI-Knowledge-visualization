"""Batch35 offline contract checks: coverage gate, abandoned scope, and unconstrained point minutes."""
from datetime import date
import json
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import planner_agents as agents


FORM = {
    "goal": "初中一元一次方程（含乘除）", "final_goal": "能解决方程应用",
    "level": "beginner", "level_note": "", "notes": "", "only_mine": False,
    "start": "2026-10-05", "end": "2026-10-20", "daily_cap": 60,
    "weekday_weights": [1] * 7, "special_days": [],
}


def ctx():
    return SimpleNamespace(cancel=threading.Event())


def sections():
    return [agents.Section("s1.1", "src", "OpenStax", "加减法解方程", ("c1",), 100, 0, 0)]


def test_coverage_request_uses_only_titles_headings_and_leads():
    system, messages = agents.coverage_request("方程", FORM,
        [{"title": "教材", "url": "https://example.test"}],
        [{"source": "教材", "title": "加减法", "lead": "两边同时加减同一个数。"}])
    payload = json.loads(messages[0]["content"])
    assert "资料覆盖" in system and payload["sections"][0]["lead"]
    assert "全文" not in messages[0]["content"]
    assert payload["only_mine"] is False


def test_coverage_parser_requires_specific_missing_message_and_points():
    good = agents.parse_coverage(json.dumps({"covered": False, "missing": [
        {"content": "乘除性质", "why": "目标要求用乘除变形", "points": 2}],
        "message": "资料里没有乘除性质，要不要继续找资料？"}, ensure_ascii=False))
    assert good["missing"][0]["points"] == 2
    with pytest.raises(agents.AgentFormatError):
        agents.parse_coverage(json.dumps({"covered": False, "missing": [], "message": "缺资料"}, ensure_ascii=False))
    with pytest.raises(agents.AgentFormatError):
        agents.parse_coverage(json.dumps({"covered": True, "missing": [{"content": "x", "why": "y", "points": 1}], "message": ""}, ensure_ascii=False))


def test_make_outline_blocks_when_coverage_is_missing_and_hides_generation_path():
    flow = PlanFlow(None, lambda *_: None, date.today)
    draft = {"plan_id": "p", "phase": "sources", "form": FORM.copy(), "goal": FORM["goal"],
             "outline": None, "messages": [], "sources": [], "research": {}}
    source = {"id": "src", "title": "OpenStax", "url": "https://openstax.org"}
    chunks = {"src": [{"id": "c1", "text": "两边同时加减同一个数。", "position": 0, "section": ["加减法"]}]}
    flow._sections = lambda _draft: ( [source], sections() )
    flow._adopted = lambda _draft: ([source], chunks)
    flow.save = lambda _draft: None
    def ask(purpose, request, parse, *_args, **_kwargs):
        assert purpose == "coverage_check"
        return parse(json.dumps({"covered": False, "missing": [
            {"content": "乘除性质", "why": "目标包含乘除", "points": 2}],
            "message": "资料没有乘除性质，要不要继续找资料？"}, ensure_ascii=False)), None
    flow._ask = ask
    assert flow.make_outline(draft, ctx()) is False
    assert draft["phase"] == "sources" and draft.get("outline") is None
    assert draft["coverage"]["blocked"] is True
    assert "乘除" in draft["messages"][-1]["text"]


def test_only_my_materials_prefixes_specific_shortfall():
    flow = PlanFlow(None, lambda *_: None, date.today)
    draft = {"plan_id": "p", "phase": "sources", "form": dict(FORM, only_mine=True), "goal": FORM["goal"],
             "outline": None, "messages": [], "sources": [], "research": {}}
    source = {"id": "src", "title": "我的资料"}
    flow._sections = lambda _draft: ([source], sections())
    flow._adopted = lambda _draft: ([source], {"src": [{"id": "c1", "text": "加减法", "position": 0}]})
    flow.save = lambda _draft: None
    flow._ask = lambda purpose, request, parse, *_args, **_kwargs: (parse(json.dumps({
        "covered": False, "missing": [{"content": "乘除", "why": "目标需要", "points": 3}],
        "message": "资料里缺少乘除，要不要继续找资料？"}, ensure_ascii=False)), None)
    flow.make_outline(draft, ctx())
    assert draft["messages"][-1]["text"].startswith("你选了只用自己的资料。")
    assert "3 个知识点" in draft["messages"][-1]["text"]


def test_added_material_clears_old_block_and_rechecks():
    flow = PlanFlow(None, lambda *_: None, date.today)
    draft = {"plan_id": "p", "phase": "sources", "form": FORM.copy(), "goal": FORM["goal"],
             "outline": None, "messages": [], "sources": [], "research": {}}
    source = {"id": "src", "title": "资料"}
    flow.save = lambda _draft: None
    calls = []
    flow._ask = lambda purpose, request, parse, *_args, **_kwargs: (
        calls.append(purpose) or parse(json.dumps({
            "covered": len(calls) > 1,
            "missing": [] if len(calls) > 1 else [{"content": "乘除", "why": "目标需要", "points": 1}],
            "message": "缺乘除，要不要继续找资料？" if len(calls) == 1 else ""
        }, ensure_ascii=False)), None)
    assert flow._check_coverage(draft, ctx(), [source], sections(), {"src": []}) is False
    assert draft["coverage"]["blocked"]
    # Import/adopted-source paths invalidate the old verdict before retrying.
    draft.pop("coverage", None)
    assert flow._check_coverage(draft, ctx(), [source], sections(), {"src": []}) is True
    assert draft["coverage"]["blocked"] is False and calls == ["coverage_check", "coverage_check"]


def test_abandon_scope_rewrites_goal_and_rechecks_coverage():
    flow = PlanFlow(None, lambda *_: None, date.today)
    draft = {"plan_id": "p", "phase": "sources", "form": FORM.copy(), "goal": FORM["goal"],
             "outline": None, "messages": [], "sources": [], "research": {}}
    source = {"id": "src", "title": "OpenStax"}
    flow._adopted = lambda _draft: ([source], {"src": [{"id": "c1", "text": "加减法", "position": 0}]})
    flow._sections = lambda _draft: ([source], sections())
    flow.save = lambda _draft: None
    purposes = []
    def ask(purpose, request, parse, *_args, **_kwargs):
        purposes.append(purpose)
        if purpose == "coverage_check":
            return parse(json.dumps({"covered": True, "missing": [], "message": ""})), None
        raise AssertionError(purpose)
    flow._ask = ask
    flow._abandon_scope(draft, ctx(), ["乘除"])
    assert "乘除" not in draft["goal"]
    assert draft["form"]["abandoned_topics"] == ["乘除"]
    assert draft["coverage"]["covered"] and purposes == ["coverage_check"]
    assert "调整后的目标" in draft["messages"][-1]["text"]


def test_intent_contract_accepts_abandon_scope_items():
    from refactor.learning.course.plan_intent import parse_intents
    context = {"phase": "sources", "today": "2026-10-02", "form": FORM,
               "recent": [], "cards": [], "sections": [], "modules": [], "facts": {}}
    answer = json.dumps({"intents": [{"kind": "abandon_scope", "items": ["乘除"]}], "reply": ""}, ensure_ascii=False)
    assert parse_intents(answer, context)[0].params == {"items": ["乘除"]}


def test_positive_long_point_minutes_and_lexical_duplicate_are_admitted_or_removed():
    aliases = {"c1": "src:c1"}
    payload = json.dumps({"points": [{"title": "长实践", "summary": "完成完整项目并复盘", "chunks": ["c1"], "minutes": 45}]}, ensure_ascii=False)
    assert agents.parse_points(payload, aliases, min_points=1, max_points=1)[0]["minutes"] == 45
    flow = PlanFlow(None, lambda *_: None, date.today)
    draft = {"plan_id": "p", "form": dict(FORM, abandoned_topics=[]), "goal": "方程", "outline": {"stages": []}}
    previous = [{"title": "解加减方程", "summary": "用等式性质求解", "chunk_ids": ["c1"], "minutes": 10, "module_title": "基础"}]
    duplicate = [{"title": "求解加减方程", "summary": "用等式性质求解", "chunk_ids": ["c1"], "minutes": 45}]
    flow._ask = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate must be silent"))
    assert flow._check_module_points(draft, {"title": "应用", "minutes": [1, 120]},
                                     [{"id": "c1", "text": "原文"}], ctx(), duplicate, previous) == []


def test_abandoned_points_are_regenerated_once_then_removed():
    flow = PlanFlow(None, lambda *_: None, date.today)
    form = dict(FORM, abandoned_topics=["乘除"])
    draft = {"plan_id": "p", "form": form, "goal": "方程", "outline": {"stages": []}}
    module = {"title": "方程", "summary": "", "minutes": [1, 120]}
    points = [{"title": "乘除法", "summary": "乘除变形", "chunk_ids": ["c1"], "minutes": 5},
              {"title": "加减法", "summary": "加减变形", "chunk_ids": ["c1"], "minutes": 45}]
    calls = []
    def ask(purpose, request, parse, *_args, **_kwargs):
        calls.append(purpose)
        return parse(json.dumps({"points": [
            {"title": "乘除法", "summary": "乘除变形", "chunks": ["c1"], "minutes": 5},
            {"title": "加减法", "summary": "加减变形", "chunks": ["c1"], "minutes": 45}]})), None
    flow._ask = ask
    out = flow._check_module_points(draft, module, [{"id": "c1", "text": "原文"}], ctx(), points, [])
    assert calls == ["generate"] and [p["title"] for p in out] == ["加减法"]
