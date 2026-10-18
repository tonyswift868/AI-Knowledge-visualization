import json
from datetime import date, timedelta
from types import SimpleNamespace

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import material_questions
from refactor.learning.course import planner_agents as agents
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.connections.errors import ModelError
import pytest


def test_166_plan_flow_coverage_uses_removed_module_scope(monkeypatch):
    source = {"id": "repo", "title": "合并指南", "category": "repository", "reasons": ["github_docs"]}
    chunks = [
        {"id": "a0", "source_id": "repo", "position": 0, "text": "第一章导言正文。", "section": ["第一章"]},
        {"id": "a1", "source_id": "repo", "position": 1, "text": "第一章细节正文。", "section": ["第一章", "细节"]},
        {"id": "b0", "source_id": "repo", "position": 2, "text": "第二章导言正文。", "section": ["第二章"]},
        {"id": "b1", "source_id": "repo", "position": 3, "text": "第二章细节正文。", "section": ["第二章", "细节"]},
    ]
    all_sections = agents.build_sections([source], {"repo": chunks}, max_depth=None)
    chapter_a = [row for row in all_sections if row.chapter == "第一章"]
    chapter_b = [row for row in all_sections if row.chapter == "第二章"]
    draft = {"plan_id": "p", "form": {
        "start": date.today().isoformat(),
        "end": (date.today() + timedelta(days=7)).isoformat(),
        "daily_cap": 90, "weekday_weights": [1] * 7, "special_days": [],
    }, "removed_content": []}
    previous = {"modules": [
        {"key": "a", "title": "第一章", "summary": "旧章", "refs": [row.id for row in chapter_a],
         "chapters": [row.title for row in chapter_a]},
        {"key": "b", "title": "第二章", "summary": "保留章", "refs": [row.id for row in chapter_b],
         "chapters": [row.title for row in chapter_b]},
    ]}
    current = {"modules": [previous["modules"][1]], "excluded": []}
    PlanFlow._remember_removed_content(draft, previous, current, all_sections)
    assert any(row == {"kind": "module", "title": "第一章"} for row in draft["removed_content"])
    assert any(row["kind"] == "chapter" and row["title"] == "第一章" for row in draft["removed_content"])

    scoped = agents.build_sections((), {}, directory_sections=all_sections,
                                   directory_limit=len(all_sections),
                                   removed_content=draft["removed_content"])
    store = SimpleNamespace(records=lambda kind, plan_id: chunks if kind == "source_chunks" else [])
    flow = object.__new__(PlanFlow)
    flow.services = SimpleNamespace(store=store)
    flow._sections = lambda _: ([source], scoped)
    flow._adopted = lambda _: ([source], {"repo": chunks})
    flow._window = lambda _: PlanWindow(date.today(), date.today() + timedelta(days=7),
                                         90, (1,) * 7, {})
    monkeypatch.setattr(material_questions, "candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(material_questions, "for_module", lambda *args, **kwargs: [])
    draft["material_structure"] = {
        "modules": [{"key": "b", "title": "第二章", "summary": "保留章",
                     "refs": [chapter_b[0].id], "chapters": [chapter_b[0].title]}],
        "excluded": [], "missing": [],
    }

    modules, _ = flow.structure_ranges(draft)

    assert len(modules) == 1
    assert modules[0]["title"] == "第二章"
    assert set(modules[0]["refs"]) == {row.id for row in scoped}
    assert all(row.id not in modules[0]["refs"] for row in chapter_a)
    assert draft["material_structure"]["coverage"] == {
        "total": len(scoped), "assigned": len(scoped), "excluded": 0, "unassigned": 0, "duplicates": 0,
    }
import copy
from datetime import date, timedelta
from types import SimpleNamespace

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import material_questions
from refactor.learning.course import planner_agents as agents
from refactor.learning.course.ledger import PlanWindow


def test_166_public_ranges_do_not_normalize_saved_outline_snapshot(monkeypatch):
    source = {"id": "html", "title": "上传资料"}
    chunk = {"id": "c1", "source_id": "html", "position": 0,
             "text": "旧资料正文。", "section": ["旧章节"]}
    section = agents.Section("s1.1", "html", "上传资料", "旧章节", ("c1",), 20, 0, 0)
    store = SimpleNamespace(records=lambda kind, plan_id: [chunk] if kind == "source_chunks" else [])
    flow = object.__new__(PlanFlow)
    flow.services = SimpleNamespace(store=store)
    flow._sections = lambda _: ([source], [section])
    flow._adopted = lambda _: ([source], {"html": [chunk]})
    flow._window = lambda _: PlanWindow(date.today(), date.today() + timedelta(days=7),
                                         90, (1,) * 7, {})
    monkeypatch.setattr(material_questions, "candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(material_questions, "for_module", lambda *args, **kwargs: [])
    original = {"modules": [
        {"key": "m1", "title": "旧模块一", "summary": "共享章节", "refs": ["s1.1"]},
        {"key": "m2", "title": "旧模块二", "summary": "共享章节", "refs": ["s1.1"]},
    ], "excluded": [], "missing": []}
    draft = {"plan_id": "p", "form": {
        "start": date.today().isoformat(), "end": (date.today() + timedelta(days=7)).isoformat(),
        "daily_cap": 90, "weekday_weights": [1] * 7, "special_days": [],
    }, "material_structure": copy.deepcopy(original), "outline": {"stages": []}}

    flow.structure_ranges(draft)

    assert draft["material_structure"] == original
from datetime import date, timedelta
from types import SimpleNamespace

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import material_questions
from refactor.learning.course import planner_agents as agents
from refactor.learning.course.ledger import PlanWindow


def test_166_public_ranges_keeps_an_intentionally_empty_material_selection(monkeypatch):
    source = {"id": "html", "title": "上传资料"}
    chunk = {"id": "c1", "source_id": "html", "position": 0,
             "text": "章节正文。", "section": ["章节"]}
    section = agents.Section("s1.1", "html", "上传资料", "章节", ("c1",), 20, 0, 0)
    store = SimpleNamespace(records=lambda kind, plan_id: [chunk] if kind == "source_chunks" else [])
    flow = object.__new__(PlanFlow)
    flow.services = SimpleNamespace(store=store)
    flow._sections = lambda _: ([source], [section])
    flow._adopted = lambda _: ([source], {"html": [chunk]})
    flow._window = lambda _: PlanWindow(date.today(), date.today() + timedelta(days=7),
                                         90, (1,) * 7, {})
    monkeypatch.setattr(material_questions, "candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(material_questions, "for_module", lambda *args, **kwargs: [])
    draft = {"plan_id": "p", "form": {
        "start": date.today().isoformat(), "end": (date.today() + timedelta(days=7)).isoformat(),
        "daily_cap": 90, "weekday_weights": [1] * 7, "special_days": [],
    }, "material_structure": {"modules": [], "excluded": [], "missing": []}, "outline": None}

    modules, ranges = flow.structure_ranges(draft)

    assert modules == []
    assert ranges["estimated_question_count"] == 0


def test_166_outline_retries_once_when_canonical_coverage_is_missing():
    sections = [
        agents.Section("s1", "html", "上传资料", "第一章", ("c1",), 20, 0, 0),
        agents.Section("s2", "html", "上传资料", "第二章", ("c2",), 20, 1, 1),
    ]
    missing = json.dumps({"summary": "按资料安排", "modules": [
        {"key": "m1", "title": "第一章", "summary": "学习第一章", "refs": ["s1"]}],
        "missing": [], "excluded": []}, ensure_ascii=False)
    complete = json.dumps({"summary": "按资料安排", "modules": [
        {"key": "m1", "title": "资料", "summary": "学习资料", "refs": ["s1", "s2"]}],
        "missing": [], "excluded": []}, ensure_ascii=False)

    class Gateway:
        def __init__(self, replies):
            self.replies = list(replies)
            self.calls = []

        def complete(self, purpose, messages, **kwargs):
            self.calls.append((purpose, list(messages)))
            return SimpleNamespace(text=self.replies.pop(0))

    gateway = Gateway([missing, complete])
    flow = object.__new__(PlanFlow)
    flow.services = SimpleNamespace(gateway=gateway)
    ctx = SimpleNamespace(cancel=__import__("threading").Event())
    parse = lambda text: agents.parse_material_structure(text, sections)

    value, _ = flow._ask("outline", ("system", [{"role": "user", "content": "目录"}]),
                         parse, ctx, plan_id="p")

    assert len(gateway.calls) == 2
    assert value["coverage"]["assigned"] == 2
    assert value["coverage"]["unassigned"] == 0
    assert "上一条格式校验失败" in gateway.calls[1][1][-1]["content"]


def test_166_outline_fails_after_one_unresolved_coverage_retry():
    sections = [
        agents.Section("s1", "html", "上传资料", "第一章", ("c1",), 20, 0, 0),
        agents.Section("s2", "html", "上传资料", "第二章", ("c2",), 20, 1, 1),
    ]
    response = json.dumps({"summary": "按资料安排", "modules": [
        {"key": "m1", "title": "第一章", "summary": "学习第一章", "refs": ["s1"]}],
        "missing": [], "excluded": []}, ensure_ascii=False)

    class Gateway:
        def __init__(self):
            self.calls = []

        def complete(self, purpose, messages, **kwargs):
            self.calls.append(list(messages))
            return SimpleNamespace(text=response)

    gateway = Gateway()
    flow = object.__new__(PlanFlow)
    flow.services = SimpleNamespace(gateway=gateway)
    ctx = SimpleNamespace(cancel=__import__("threading").Event())
    parse = lambda text: agents.parse_material_structure(text, sections)

    with pytest.raises(ModelError) as exc:
        flow._ask("outline", ("system", [{"role": "user", "content": "目录"}]),
                  parse, ctx, plan_id="p")

    assert exc.value.code == "AGENT_FORMAT"
    assert len(gateway.calls) == 2
