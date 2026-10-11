"""Opt-in independent acceptance run; all data is isolated under D:.

Invoke with MF_ACCEPTANCE_REAL=1 and MF_ACCEPTANCE_PHASE=1 or 2. The default
pytest collection skips real calls. This file intentionally changes no product
state or production code. Phase two resumes the same synthetic plan.
"""
import json
import os
import sys
import threading
import time as wall
from threading import RLock
from contextlib import ExitStack
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO), str(REPO / "tests/refactor/timeline"), str(REPO / "tests/refactor/class_eval")]

from tests.refactor.class_eval import class_eval as ce  # noqa: E402
from refactor.learning.app import handlers  # noqa: E402
from refactor.learning.app.classroom import Classroom  # noqa: E402
from refactor.learning.app.plan_flow import PlanFlow  # noqa: E402
from refactor.learning.app.settings import AppSettings  # noqa: E402
from refactor.learning.connections.errors import ModelError  # noqa: E402
from refactor.learning.course.judge_agent import _json_object  # noqa: E402
from refactor.learning.course.ledger import study_day  # noqa: E402
from refactor.learning.course.store import CourseStore  # noqa: E402
from test_course_timeline_simulation import InjectedClock, HandlerRegistry  # noqa: E402

OUT = Path(r"D:\MentorFlow\.local\test-tmp\acceptance")
CAP = Decimal("5")
ACCOUNT = "acceptance_learner"
START = date(2026, 10, 5)
MATERIAL = Path(r"D:\MentorFlow\.local\test-tmp\class-eval\sources\python-datastructures.html")
SETTINGS = dict(refresh_time="04:00", teacher_style="warm", detail="normal", examples="normal", analogy_domains=["life"])
PERSONA = "普通上班族，零基础学 Python，认真但偶尔记混相近的方法；答题用自己的话。"


class Capped:
    def __init__(self, inner, phase):
        self.inner = inner
        self.phase = phase
        self._lock = RLock()

    def spent(self):
        return sum(Decimal(r.get("peak_cost") or r["reserved"]) for r in self.inner.rows)

    def complete(self, purpose, messages, **kw):
        # Serialize spending across prep/profile background threads. Reserve
        # the full estimated high cost before the next network call.
        with self._lock:
            maximum = Decimal("2.5") if self.phase == 1 else CAP
            packet = json.dumps(dict(system=kw.get("system"), messages=messages), ensure_ascii=False)
            bound = len(packet.encode("utf-8")) + 2048
            config = self.inner.config
            context = dict(self.inner.inner.estimate_context(), cache_mode="implicit", price_period="peak")
            estimate = self.inner.inner.price_catalog.estimate_cost(
                config["provider_id"], config["model"], input_tokens=(bound, bound),
                output_tokens=(kw.get("max_tokens", 4096),) * 2,
                cache_hit_rate=(0, 0), context=context)
            if estimate is None or estimate.currency != "CNY" or self.spent() + estimate.high > maximum:
                raise ModelError("EVAL_BUDGET_STOP", f"第 {self.phase} 段预算 ¥{maximum} 将被超过；未发起调用")
            return self.inner.complete(purpose, messages, **kw)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class Run:
    def __init__(self, stack, phase):
        OUT.mkdir(parents=True, exist_ok=True)
        ce.ROOT = OUT  # budget journal + guarded JSON writes stay in OUT
        self.phase = phase
        self.clock = InjectedClock()
        self.clock.set(f"{START.isoformat()}T10:00:00+08:00")
        stack.enter_context(__import__("unittest.mock").mock.patch.object(handlers, "datetime", self.clock.datetime))
        self.gw = Capped(ce.live_gateway(stack, OUT), phase)
        data = OUT / "data"
        self.store = CourseStore(ACCOUNT, data)
        stack.callback(self.store.close)
        self.settings = AppSettings(ACCOUNT, data)
        if phase == 1:
            self.settings.save(SETTINGS)
        self.services = SimpleNamespace(store=self.store, settings=self.settings, gateway=self.gw,
                                        account_id=ACCOUNT, data_root=data)
        self.day = lambda: study_day(self.clock.now(), time(4), self.clock.zone)
        from datetime import timezone
        self.room = Classroom(self.services, self.day, now=lambda: self.clock.datetime.now(timezone.utc))
        self.registry = HandlerRegistry()
        self.registry.push = lambda *a: None
        handlers.register(self.registry, SimpleNamespace(services=self.services, settings_changed=lambda _: None))
        self.flow = PlanFlow(self.services, lambda *a: None, self.day)
        self.ops = json.loads((OUT / "ops.json").read_text(encoding="utf-8")) if (OUT / "ops.json").exists() else []
        self.days = json.loads((OUT / "days.json").read_text(encoding="utf-8")) if (OUT / "days.json").exists() else []
        self.log = json.loads((OUT / "log.json").read_text(encoding="utf-8")) if (OUT / "log.json").exists() else []
        self.plan_id = None
        self.view = None

    # ---------------------------------------------------------------- helpers
    def ctx(self, stream=None):
        return SimpleNamespace(cancel=threading.Event(), stream=(stream.append if stream is not None else (lambda _: None)))

    def note(self, text):
        print(text, flush=True)
        self.log.append(dict(at=self.clock.now().isoformat(), text=text))

    def plan_step(self, label, work):
        started = wall.monotonic()
        result = self.flow.run(self.plan_id, work, self.ctx())
        draft = result["draft"]
        secs = round(wall.monotonic() - started, 2)
        errors = [m["text"] for m in draft["messages"][-3:] if m.get("kind") == "error"]
        self.ops.append(dict(stage="plan", name=label, seconds=secs, phase=draft["phase"], errors=errors))
        self.note(f"[plan] {label}: {secs}s phase={draft['phase']} {errors or ''}")
        return draft

    def act(self, name, *args, **kwargs):
        stream = []
        started = wall.monotonic()
        fn = getattr(self.room, name)
        def on_stream(value):
            stream.append(dict(seconds=round(wall.monotonic() - started, 4), text=value))
        try:
            self.view = self.room.run(self.plan_id, lambda co, st, cx: fn(co, st, cx, *args, **kwargs),
                                      SimpleNamespace(cancel=threading.Event(), stream=on_stream))
        finally:
            secs = round(wall.monotonic() - started, 2)
            for thread in threading.enumerate():
                if thread.name.startswith("classroom-"):
                    thread.join(120)
            v = self.view or {}
            marker = "\x01VERDICT:"
            verdict = next((e["seconds"] for e in stream if e["text"].startswith(marker)), None)
            teacher = next((e["seconds"] for e in stream if e["text"] and not e["text"].startswith(marker)
                            and e["text"] != "\x00" and (verdict is None or e["seconds"] >= verdict)), None)
            self.ops.append(dict(stage="class", day=self.day().isoformat(), name=name,
                                 args=[str(a)[:200] for a in args], kwargs=kwargs,
                                 seconds=secs, verdict_seconds=verdict, teacher_first_seconds=teacher,
                                 step=v.get("step"), module=(v.get("module") or {}).get("id"),
                                 question=(v.get("question") or {}).get("stem"),
                                 errors=[m["content"] for m in v.get("messages", [])[-3:] if m.get("kind") == "error"]))
            self._checkpoint()
        return self.view

    def _checkpoint(self):
        (OUT / "ops.json").write_text(json.dumps(self.ops, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / "days.json").write_text(json.dumps(self.days, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / "log.json").write_text(json.dumps(self.log, ensure_ascii=False, indent=2), encoding="utf-8")

    def today(self):
        plans = self.registry.methods["plans.list"]({}, None)["plans"]
        plan = next(p for p in plans if p["id"] == self.plan_id)
        t = plan.get("today") or {}
        return dict(state=plan.get("state"), **{k: t.get(k) for k in (
            "today_target", "today_confirmed", "today_tentative", "debt_minutes", "needs_negotiation",
            "ahead_minutes", "overdue_days", "remaining_minutes", "completed")})

    def chunks_for(self, point_ids):
        points = {p["id"]: p for p in self.store.records("points", self.plan_id)}
        wanted = {c for pid in point_ids for c in points.get(pid, {}).get("chunk_ids", [])}
        return {c["id"]: c["text"][:4000] for c in self.store.records("source_chunks", self.plan_id) if c["id"] in wanted}

    def student(self, mode):
        state = self.room._load(self.room._course(self.plan_id))
        pending = state.get("pending") or {}
        packet = dict(persona=PERSONA, mode=mode, question=self.view.get("question"),
                      dialogue=[dict(role=m["role"], text=m["content"]) for m in self.view["messages"][-6:]],
                      textbook=self.chunks_for(pending.get("point_ids") or []))
        self.gw.inner.case, self.gw.inner.operation = "student", f"{self.day()}"
        for attempt in range(2):
            result = self.gw.complete("simulated_student", [{"role": "user", "content": json.dumps(packet, ensure_ascii=False)}],
                                      system=ce.STUDENT_SYSTEM, max_tokens=4096)
            try:
                value = _json_object(result.text)
                return value["reply"]
            except Exception:  # noqa: BLE001
                if attempt:
                    raise

    # ---------------------------------------------------------------- plan creation
    def create(self):
        identity = OUT / "plan-id.txt"
        if identity.exists():
            draft = self.flow.load(identity.read_text(encoding="utf-8").strip())
        else:
            draft = self.flow.current()
            identity.write_text(draft["plan_id"], encoding="utf-8")
        self.plan_id = draft["plan_id"]
        form = dict(goal="学会 Python 列表和元组的基本用法",
                    final_goal="能说清列表常用方法（append/extend/insert/remove/pop/sort）和元组、解包的规则，并写出正确的小例子",
                    level="beginner", level_note="", notes="只学官方教程第 5 章里的列表方法（5.1）和元组与序列（5.3），其他小节不学。",
                    start=START.isoformat(), end=(START + timedelta(days=9)).isoformat(), daily_cap=60,
                    weekday_weights=[1] * 7, special_days=[], only_mine=True)
        if draft["phase"] == "form":
            draft = self.plan_step("提交表单+信息核对", lambda d, x: self.flow.submit_form(d, x, form))
        if draft["phase"] == "questions":
            draft = self.plan_step("回答追问", lambda d, x: self.flow.chat(d, x, "零基础，每天最多学 60 分钟；只学列表方法和元组两节，想能自己写小例子。"))
        if draft["phase"] == "sources":
            if not self.store.records("sources", self.plan_id):
                started = wall.monotonic()
                self.flow.add_file(self.plan_id, MATERIAL)
                self.ops.append(dict(stage="plan", name="导入资料", seconds=round(wall.monotonic() - started, 2)))
            draft = self.flow.load(self.plan_id)
            draft = self.plan_step("生成路线轮廓", lambda d, x: self.flow.make_outline(d, x))
        if draft["phase"] == "outline":
            (OUT / "outline.json").write_text(json.dumps(self.flow.public(draft).get("outline"), ensure_ascii=False, indent=2), encoding="utf-8")
            self.note("outline saved; estimate captured")
            draft = self.plan_step("正式生成", lambda d, x: self.flow.generate(d, x))
        if draft["phase"] != "ready":
            raise SystemExit("generation did not finish: " + json.dumps(draft["messages"][-3:], ensure_ascii=False))
        self.plan_step("开始计划", lambda d, x: self.flow.start(d))
        mods = self.store.records("modules", self.plan_id)
        pts = self.store.records("points", self.plan_id)
        self.note(f"plan: {len(mods)} modules, {len(pts)} points, total {self.store.get_plan(self.plan_id)['total_minutes']} min")
        (OUT / "generated.json").write_text(json.dumps(dict(modules=mods, points=pts,
            plan=self.store.get_plan(self.plan_id)), ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---------------------------------------------------------------- one class session
    def session(self, *, stop, modes, test_wrong_once=False, use_direct_test=False, force_one_test=False):
        """stop(today)->bool ends the day; modes: iterator of casual-answer modes."""
        self.act("enter")
        self.view = self.room.run(self.plan_id, lambda *a: None)
        wrong_done = set()
        test_started = False
        for _ in range(120):
            if self.gw.spent() >= (Decimal("2.3") if self.phase == 1 else Decimal("4.8")):
                self.note("预算接近上限，停止本节课")
                break
            step = self.view["step"]
            if step == "completed":
                break
            if step in ("teach",) and stop(self.today()) and (not force_one_test or test_started):
                break
            if step == "teach":
                if use_direct_test and self.view.get("can_skip") and self.view["module"].get("id") not in wrong_done:
                    wrong_done.add(self.view["module"]["id"])
                    self.act("start_test", "skip_test")
                else:
                    self.act("next")
            elif step == "answer":
                self.act("reply", self.student(next(modes, "correct")))
            elif step == "test_offer":
                if stop(self.today()) and (not force_one_test or test_started):
                    break
                self.act("start_test", "module_test")
                test_started = True
            elif step in ("testing", "retesting"):
                key = (self.view["module"]["id"], step)
                mode = "wrong" if test_wrong_once and step == "testing" and key not in wrong_done else "correct"
                wrong_done.add(key)
                self.act("reply", self.student(mode))
            elif step == "test_done":
                if self.view.get("red"):
                    self.act("start_test", "retest")
                elif force_one_test and test_started:
                    break
                else:
                    self.act("next_module")
            elif step == "check_offer":
                self.act("start_check")
            elif step == "checking":
                if self.view.get("question"):
                    self.act("reply", self.student(next(modes, "correct")))
                else:
                    self.act("start_check")
            else:
                raise RuntimeError("unexpected step " + step)
        self.act("leave")

    def record_day(self, label):
        snap = self.today()
        self.days.append(dict(day=self.day().isoformat(), label=label, **snap))
        self.note(f"[{self.day()}] {label}: {snap}")
        self._checkpoint()

    def goto(self, offset, hour=12):
        self.clock.set(f"{(START + timedelta(days=offset)).isoformat()}T{hour:02d}:00:00+08:00")

    # ---------------------------------------------------------------- script
    def script(self):
        met = lambda t: t["today_target"] and (t["today_confirmed"] + t["today_tentative"]) >= t["today_target"]
        if self.phase == 1:
            if (OUT / "phase1-complete.flag").exists():
                raise RuntimeError("第一阶段已完成；禁止无意重跑付费样本")
            self.create()
            self.goto(0); self.record_day("D1 上课前")
            self.session(stop=met, modes=iter(["wrong", "correct", "question", "correct", "chat", "correct"]))
            self.record_day("D1 下课")
            self.goto(1); self.record_day("D2 上课前")
            self.session(stop=lambda t: t["today_target"] and
                         (t["today_confirmed"] + t["today_tentative"]) >= 2 * t["today_target"],
                         modes=iter(["correct", "chat", "correct"]), test_wrong_once=True, force_one_test=True)
            self.record_day("D2 下课（多学，至少一次模块检测）")
            self.goto(2); self.record_day("D3 没上课")
            self.goto(3); self.record_day("D4 上课前")
            before = self.today()["today_confirmed"] + self.today()["today_tentative"]
            self.session(stop=lambda t: (t["today_confirmed"] + t["today_tentative"]) > before,
                         modes=iter(["correct"]))
            self.record_day("D4 下课（只学一点）")
            (OUT / "phase1-complete.flag").write_text("completed", encoding="utf-8")
            return
        self.plan_id = (OUT / "plan-id.txt").read_text(encoding="utf-8").strip()
        # Phase 2 only starts after phase-1 evidence is manually checked for
        # a blocking P1. Resume the same plan, not a newly generated one.
        for offset in range(4, 22):
            if self.gw.spent() >= Decimal("4.8"):
                self.note("第二阶段预算接近上限；保留当前计划状态，停止模拟时钟推进")
                break
            self.goto(offset); self.record_day(f"D{offset + 1} 上课前")
            if self.store.get_plan(self.plan_id)["state"] == "completed":
                break
            self.session(stop=lambda t: False,
                         modes=iter(["correct", "wrong", "correct", "correct", "correct", "correct"]),
                         test_wrong_once=True, use_direct_test=True)
            self.record_day(f"D{offset + 1} 下课")
            if self.store.get_plan(self.plan_id)["state"] == "completed":
                break
        if self.store.get_plan(self.plan_id)["state"] == "completed":
            self.complete_and_practice()

    def complete_and_practice(self):
        from refactor.learning.app.plan_flow import report_markdown
        from refactor.learning.course.report_pdf import render_report_pdf
        report = self.flow.report(self.plan_id)
        (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (OUT / "learning-report.md").write_text(report_markdown(report["report"], report["titles"]), encoding="utf-8")
        render_report_pdf(report["report"], report["titles"], report["views"], OUT / "learning-report.pdf")
        before = self.store.get_plan(self.plan_id)
        modules = self.store.records("modules", self.plan_id)
        self.act("start_practice", module_id=modules[0]["id"], count=3)
        self.room = Classroom(self.services, self.day, now=self.room.now)  # reopen mid-practice (BUG-30)
        self.view = self.room.run(self.plan_id, lambda *a: None)
        for i in range(3):
            self.act("reply", self.student("wrong" if i == 0 else "correct"))
        self.act("start_practice", count=3)
        for _ in range(3):
            self.act("reply", self.student("correct"))
        assert self.store.get_plan(self.plan_id)["state"] == before["state"]

    def dump(self):
        msgs = sorted(self.store.records("messages", self.plan_id), key=lambda m: (m["at"], m["id"])) if self.plan_id else []
        lines = ["# e2e 课堂全部对话", ""]
        for m in msgs:
            lines += [f"### {m.get('module_id')} · {m['role']} · {m.get('kind', 'text')} · {m['at']}", "", m["content"], ""]
        (OUT / "dialogue.md").write_text("\n".join(lines), encoding="utf-8")
        (OUT / "days.json").write_text(json.dumps(self.days, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / "ops.json").write_text(json.dumps(self.ops, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / "log.json").write_text(json.dumps(self.log, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.plan_id:
            for kind in ("judgements", "points", "modules", "credits"):
                (OUT / f"{kind}.json").write_text(json.dumps(self.store.records(kind, self.plan_id), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        calls = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((OUT / "calls").glob("*.json"))]
        by = {}
        for c in calls:
            b = by.setdefault(c["purpose"], dict(n=0, seconds=0.0, first=[], cost=Decimal(0), inp=0, cached=0))
            b["n"] += 1; b["seconds"] += c.get("seconds") or 0
            if c.get("first_text_seconds"): b["first"].append(c["first_text_seconds"])
            bill = c.get("billing") or {}
            b["cost"] += Decimal(bill.get("peak_cost") or 0)
            b["inp"] += bill.get("input_tokens") or 0; b["cached"] += bill.get("cached_input_tokens") or 0
        summary = {k: dict(calls=v["n"], avg_seconds=round(v["seconds"] / v["n"], 2),
                           avg_first_text=round(sum(v["first"]) / len(v["first"]), 2) if v["first"] else None,
                           cost=str(v["cost"]), input_tokens=v["inp"], cached_tokens=v["cached"]) for k, v in by.items()}
        summary["_total_cost"] = str(self.gw.spent())
        (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=1))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.environ.get("MF_ACCEPTANCE_REAL") != "1":
        raise RuntimeError("真实验收需显式设置 MF_ACCEPTANCE_REAL=1")
    phase = int(os.environ.get("MF_ACCEPTANCE_PHASE", "1"))
    if phase not in (1, 2):
        raise RuntimeError("MF_ACCEPTANCE_PHASE 只能是 1 或 2")
    with ExitStack() as stack:
        run = Run(stack, phase)
        try:
            if os.environ.get("MF_ACCEPTANCE_DUMP_ONLY") == "1":
                run.plan_id = (OUT / "plan-id.txt").read_text(encoding="utf-8").strip()
                run.days = [d for d in run.days if d.get("state") != "creating"]
                run.goto(4)
                if not any(d["label"] == "D5 阻断后" for d in run.days):
                    run.record_day("D5 阻断后")
            else:
                run.script()
        finally:
            run.dump()


def test_full_flow_requires_explicit_real_model_opt_in():
    import pytest
    if os.environ.get("MF_ACCEPTANCE_REAL") != "1":
        pytest.skip("real model acceptance is opt-in")
    main()


if __name__ == "__main__":
    main()
