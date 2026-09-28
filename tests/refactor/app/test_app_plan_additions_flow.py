"""PLAN-10 host flow: pick -> analyze -> generate -> apply, with a scripted model."""
import json
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import materials
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.store import CourseStore

ACCOUNT = "mfa1_" + "a" * 64
PLAN = "c" * 32
START = date(2026, 9, 28)
TODAY = START + timedelta(days=2)
ROOT = Path(r"D:\MentorFlow\.local\test-tmp\plan-additions\host")


class Model:
    def __init__(self):
        self.calls = []

    def complete(self, purpose, messages, *, system=None, cancel=None, plan_id="", max_tokens=4096, **_):
        self.calls.append((purpose, max_tokens))
        if purpose == "plan_additions":
            text = json.dumps({"supplements": [{"point_id": "p0", "chunk_ids": ["nc"], "reason": "新的例子"}],
                               "new_modules": [{"key": "n1", "title": "新模块", "summary": "能使用新方法", "stage_id": "s",
                                                "after_module_id": "m1", "prerequisites": ["m1"], "required_by": ["m2"],
                                                "minutes": [20, 30], "chunk_ids": ["nc"], "reason": "尚未覆盖的新方法"}],
                               "overlaps": [], "ignored": []}, ensure_ascii=False)
        else:
            alias = re.findall(r"\[(c\d+)\]", messages[-1]["content"])[0]
            text = json.dumps({"points": [{"title": "新方法概念", "summary": "能解释含义", "chunks": [alias], "minutes": 10},
                                          {"title": "新方法应用", "summary": "能独立完成应用", "chunks": [alias], "minutes": 12}]},
                              ensure_ascii=False)
        return SimpleNamespace(text=text, search_hits=(), citations=())


@pytest.fixture
def flow(monkeypatch):
    ROOT.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.TemporaryDirectory(dir=ROOT) as folder:
        with CourseStore(ACCOUNT, Path(folder)) as s:
            s.create_plan(PLAN, goal="Python 基础", final_goal="能解释和应用", level="beginner",
                          window=PlanWindow(START, START + timedelta(days=4), 30))
            s.add_sources(PLAN, [dict(id="old", url="https://example.invalid/old", title="旧资料", adopted=True,
                                      chunks=[dict(id="oc", text="旧资料全文")])])
            s.save_outline(PLAN, [dict(id="s", title="Python")],
                           [dict(id=f"m{i}", title=f"旧模块{i}", summary=f"旧模块摘要{i}", stage_id="s",
                                 prerequisites=[f"m{i-1}"] if i else []) for i in range(3)])
            for i in range(3):
                s.save_generated_module(PLAN, f"m{i}", [dict(id=f"p{i}", title=f"旧点{i}", summary="旧摘要",
                                                            chunk_ids=["oc"], minutes=20)], check_minutes=5, today=START)
            s.start_plan(PLAN, today=START)
            s.append_judgement(PLAN, Judgement("j0", 1, datetime(2026, 9, 28, tzinfo=timezone.utc), "module_test", "m0",
                                               "t0", (("p0", True),)))
            s.append_attempt_closed(PLAN, AttemptClosed("a0", 2, "m0", "t0", "module_test"))

            def fake_import(store, plan_id, path, **_):   # the picked file becomes the new source
                store.add_sources(plan_id, [dict(id="new", url="https://example.invalid/new", title="新增教程", adopted=True,
                                                 chunks=[dict(id="nc", text="新主题和已有知识点的补充解释。", section=["新章节"])])])
                return {"id": "new"}
            monkeypatch.setattr(materials, "import_file", fake_import)
            model = Model()
            services = SimpleNamespace(store=s, gateway=model, account_id=ACCOUNT, data_root=Path(folder))
            yield PlanFlow(services, lambda *a: None, lambda: TODAY), model, s


def ctx():
    return SimpleNamespace(cancel=threading.Event())


def test_adding_material_to_a_started_plan(flow):
    plan_flow, model, store = flow
    plan_flow._picked["t"] = [Path("新增教程.md")]
    first = plan_flow.additions_analyze(PLAN, "t", ctx())
    assert [m["key"] for m in first["preview"]["modules"]] == ["n1"] and len(first["preview"]["supplements"]) == 1
    assert model.calls[0] == ("plan_additions", 16384)
    exact = plan_flow.additions_generate(first["addition_id"], None, None, ctx())
    assert exact["exact"] and exact["confirmation_id"]
    before = store.load_ledger(PLAN).total_minutes
    result = plan_flow.additions_apply(first["addition_id"], exact["confirmation_id"])
    assert result["supplement_count"] == 1 and store.load_ledger(PLAN).total_minutes > before
    titles = [m["title"] for m in sorted(store.records("modules", PLAN), key=lambda m: m["position"])]
    assert titles == ["旧模块0", "旧模块1", "新模块", "旧模块2"]
    assert "nc" in next(p for p in store.records("points", PLAN) if p["id"] == "p0")["chunk_ids"]
    with pytest.raises(BridgeError) as gone:   # a session is used once
        plan_flow.additions_apply(first["addition_id"], exact["confirmation_id"])
    assert gone.value.code == "ADDITION_EXPIRED"


def test_only_files_chosen_in_the_dialog_are_read(flow):
    plan_flow, model, store = flow
    with pytest.raises(BridgeError):
        plan_flow.additions_analyze(PLAN, "not-a-token", ctx())
    assert model.calls == []
