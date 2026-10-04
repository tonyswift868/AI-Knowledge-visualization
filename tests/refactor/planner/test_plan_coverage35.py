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
