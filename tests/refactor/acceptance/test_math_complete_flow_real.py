"""Batch 29/31/33 independent acceptance; MF_MATH_REAL=1 opts in to paid calls.

Only the registered bridge handlers, real agents and isolated CourseStore are
used. Fault injection interrupts local persistence boundaries, not verdicts.
Run phase 1, manually audit its artifacts, then set MF_MATH_PHASE=2.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack
from dataclasses import asdict
from datetime import timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from tests.refactor.acceptance import test_full_flow_real as base
from tests.refactor.class_eval import class_eval as ce
from refactor.learning.app import classroom as cm, handlers
from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.app.settings import AppSettings
from refactor.learning.connections import gateway as gm
from refactor.learning.connections.errors import ModelError
from refactor.learning.course.judge_agent import _json_object
from refactor.learning.course.ledger import study_day
from refactor.learning.course.store import CourseStore
from refactor.learning.preferences.storage import AccountJSON, StorageError

OUT = Path(r"D:\MentorFlow\.local\test-tmp\acceptance3c")
GOAL = "初中数学：一元一次方程（含解法与简单应用）"
FALLBACK = "https://openstax.org/books/prealgebra-2e/pages/8-1-solve-equations-using-the-subtraction-and-addition-properties-of-equality"
SCOPE = ("限定3个模块、总共8–10个知识点（推荐每模块3点），每点约15分钟。"
         "模块1等式与加减消元，模块2乘除及综合解法，模块3简单应用与检验。"
         "不含二元方程组、二次方程、不等式；不需要把整本教材每小节分别设点。")


def read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else copy.deepcopy(default)


class MathGateway(ce.RecordedGateway):
    phase = 1
    study_date = "setup"
    operation = "setup"

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def spent(self):
        with self.lock:
            return sum(Decimal(r.get("peak_cost") or r["reserved"]) for r in self.rows)

    def complete(self, purpose, messages, **kw):
        cap = Decimal("3") if self.phase == 1 else Decimal("8")
        packet = json.dumps(dict(system=kw.get("system"), messages=messages), ensure_ascii=False)
        bound = len(packet.encode("utf-8")) + 2048
        tokens = max(kw.get("max_tokens", 4096), gm.PURPOSE_MIN_OUTPUT.get(purpose, 0))
        search = kw.get("enable_search", False)
        if search:
            bound += 250000
            tokens *= 10
        context = dict(self.inner.estimate_context(), cache_mode="implicit", price_period="peak")
        estimate = self.inner.price_catalog.estimate_cost(self.config["provider_id"], self.config["model"],
            input_tokens=(bound, bound), output_tokens=(tokens, tokens), cache_hit_rate=(0, 0), context=context)
        with self.lock:
            if estimate is None or estimate.currency != "CNY" or self.spent() + estimate.high > cap:
                ce.dump(OUT / "budget-stop.json", dict(phase=self.phase, spent=str(self.spent()),
                    reserve=str(estimate.high) if estimate else None, purpose=purpose, cap=str(cap)))
                raise ModelError("EVAL_BUDGET_STOP", f"第{self.phase}段预算上限¥{cap}，未发起下一次调用。")
            row = dict(id=f"call-{len(self.rows)+1:04d}", at=ce.now().isoformat(), purpose=purpose,
                day=self.study_date, operation=self.operation, phase=self.phase,
                reserved=str(estimate.high), status="pending")
            self.rows.append(row)
            ce.dump(self.budget_path, self.rows)
            with (OUT / "expectations.md").open("a", encoding="utf-8") as f:
                f.write(f"\n- {row['id']} 调用前预期：{purpose}按当前{self.operation}合同输出，"
                    f"不修改宿主状态；事后本人核对。最高预留¥{estimate.high}，此前¥{self.spent()-estimate.high}。\n")
        entry = dict(row, system=kw.get("system"), messages=copy.deepcopy(messages),
            max_tokens=kw.get("max_tokens", 4096), effective_max_tokens=tokens, enable_search=search)
        ce.dump(self.output / "calls" / (row["id"] + ".json"), entry)
        started, first, usage = time.monotonic(), [], None
        on_text = kw.get("on_text")
        if on_text:
            def stream(text):
                if text and not first: first.append(time.monotonic() - started)
                on_text(text)
            kw["on_text"] = stream
        try:
            result = self.inner.complete(purpose, messages, **kw)
            usage = result.usage
            row["status"] = "received"
            entry.update(text=result.text, search_hits=[asdict(h) for h in result.search_hits],
                         citations=[asdict(h) for h in result.citations])
            return result
        except ModelError as exc:
            usage = exc.usage
            row.update(status="failed", error_code=exc.code)
            entry.update(error_code=exc.code, message=exc.message, diagnostics=getattr(exc, "diagnostics", {}))
            raise
        finally:
            entry.update(seconds=time.monotonic()-started, first_text_seconds=first[0] if first else None)
            if usage is not None:
                normalized = gm.ModelGateway._billable_usage(usage, self.config)
                entry["usage"] = asdict(normalized)
                amount = self.inner.price_catalog.cost(self.config["provider_id"], self.config["model"],
                    normalized, at=row["at"], context=context)
                row.update(peak_cost=str(amount.amount) if amount else None,
                    input_tokens=normalized.input_tokens, cached_input_tokens=normalized.cached_input_tokens,
                    output_tokens=normalized.output_tokens)
            with self.lock:
                entry.update(status=row["status"], billing=copy.deepcopy(row))
                self.calls.append(entry)
                ce.dump(self.output / "calls" / (row["id"] + ".json"), entry)
                ce.dump(self.budget_path, self.rows)


class MathRun:
    def __init__(self, stack, phase):
        OUT.mkdir(parents=True, exist_ok=True)
        ce.ROOT = OUT
        self.phase, self.stack = phase, stack
        self.clock = base.InjectedClock()
        self.clock.set(read(OUT / "clock.json", {"value": f"{base.START}T10:00:00+08:00"})["value"])
        stack.enter_context(patch.object(handlers, "datetime", self.clock.datetime))
        stack.enter_context(patch.object(ce, "RecordedGateway", MathGateway))
        self.gw = ce.live_gateway(stack, OUT)
        self.gw.phase = phase
        guarded_write = AccountJSON.write
        def record_failed_write(storage, value):
            try:
                return guarded_write(storage, value)
            except StorageError:
                rendered = json.dumps(value, ensure_ascii=False, indent=2)
                try: payload_bytes, encoding_error = len(rendered.encode("utf-8")), None
                except UnicodeEncodeError as exc: payload_bytes, encoding_error = None, str(exc)
                evidence = dict(path=str(storage.path), payload_bytes=payload_bytes,
                    encoding_error=encoding_error, value=value)
                (OUT / "storage-failure.json").write_text(
                    json.dumps(evidence, ensure_ascii=True, indent=2), encoding="utf-8")
                raise
        stack.enter_context(patch.object(AccountJSON, "write", record_failed_write))
        self.store = CourseStore("acceptance_math_33", OUT / "data")
        self.settings = AppSettings("acceptance_math_33", OUT / "data")
        if phase == 1 and not (OUT / "plan-id.txt").exists():
            self.settings.save(dict(base.SETTINGS, detail="brief", examples="few"))
        self.services = SimpleNamespace(store=self.store, settings=self.settings, gateway=self.gw,
            account_id="acceptance_math_33", data_root=OUT / "data")
        self.day = lambda: study_day(self.clock.now(), base.time(4), self.clock.zone)
        self.rooms = []
        real_room = cm.Classroom
        def room_factory(*args, **kwargs):
            kwargs.setdefault("now", lambda: self.clock.datetime.now(timezone.utc))
            r = real_room(*args, **kwargs)
            self.rooms.append(r)
            self.room = r
            return r
        stack.enter_context(patch.object(cm, "Classroom", room_factory))
        self.flow = PlanFlow(self.services, lambda *a: None, self.day)
        self.ops = read(OUT / "ops.json", [])
        self.days = read(OUT / "days.json", [])
        self.coverage = read(OUT / "coverage.json", {})
        self.faults = read(OUT / "reopens.json", [])
        self.plan_id = (OUT / "plan-id.txt").read_text().strip() if (OUT / "plan-id.txt").exists() else None
        self.view = None
        self.bind()

    def bind(self):
        self.registry = base.HandlerRegistry()
        self.ready_event = threading.Event()
        self.events = read(OUT / "bridge-events.json", [])
        event_lock = threading.Lock()
        def push(name, payload):
            with event_lock:
                self.events.append(dict(name=name, payload=payload, monotonic=time.monotonic()))
                ce.dump(OUT / "bridge-events.json", self.events)
            if name == "classroom.lesson_ready": self.ready_event.set()
        self.registry.push = push
        handlers.register(self.registry, SimpleNamespace(services=self.services, settings_changed=lambda _: None))
        # The application's registered classroom factory gets our injected OS clock.

    def quiet(self):
        deadline = time.monotonic() + 500
        while True:
            workers = [t for t in threading.enumerate() if t.name.startswith("classroom-")]
            if not workers: break
            for t in workers: t.join(min(1, max(0, deadline-time.monotonic())))
            if time.monotonic() >= deadline:
                raise RuntimeError("后台工作500秒未结束，记录阻断，不重复付费")

    def note(self, text):
        print(text, flush=True)

    def state(self):
        return self.room._load(self.room._course(self.plan_id))

    def call(self, method, **params):
        self.gw.study_date, self.gw.operation = self.day().isoformat(), method
        started, streams = time.monotonic(), []
        ctx = SimpleNamespace(cancel=threading.Event(), stream=lambda text: streams.append(
            dict(seconds=time.monotonic()-started, text=text)))
        before = self.state() if method.startswith("classroom.") and hasattr(self, "room") else None
        n = len(self.ops)+1
        error = None
        try:
            value = self.registry.methods[method](dict(plan_id=self.plan_id, **params), ctx)
            if method.startswith("classroom."): self.view = value
            return value
        except BaseException as exc:
            error = dict(type=type(exc).__name__, code=getattr(exc, "code", None), message=str(exc))
            raise
        finally:
            row = dict(id=f"op-{n:04d}", day=self.day().isoformat(), method=method, params=params,
                seconds=time.monotonic()-started, streams=streams, error=error,
                before=before, after=self.state() if method.startswith("classroom.") and hasattr(self, "room") else None,
                view=self.view if method.startswith("classroom.") else None)
            self.ops.append(row)
            ce.dump(OUT / "operations" / (row["id"]+".json"), row)
            ce.dump(OUT / "ops.json", self.ops)
            self.note(f"{row['id']} {method} {row['seconds']:.2f}s {self.view.get('step') if self.view else ''} {error or ''}")

    def plan_step(self, label, work):
        self.gw.operation = label
        value = self.flow.run(self.plan_id, work, SimpleNamespace(cancel=threading.Event(), stream=lambda _: None))
        ce.dump(OUT / "plan-steps" / (label+".json"), value)
        self.note(label + " " + value["draft"]["phase"])
        return value["draft"]

    def create(self):
        if self.plan_id and self.store.get_plan(self.plan_id)["state"] != "creating": return
        draft = self.flow.load(self.plan_id) if self.plan_id else self.flow.current()
        self.plan_id = draft["plan_id"]
        (OUT / "plan-id.txt").write_text(self.plan_id, encoding="utf-8")
        if draft["phase"] == "form":
            form = dict(goal=GOAL, final_goal="能用等式性质解一元一次方程、写出必要步骤并解决简单实际问题",
                level="beginner", start=base.START.isoformat(), end=(base.START+timedelta(days=3)).isoformat(),
                daily_cap=90, weekday_weights=[1]*7, special_days=[], only_mine=False, notes=SCOPE)
            draft = self.plan_step("01提交与真实搜索", lambda d,c: self.flow.submit_form(d,c,form))
        if draft["phase"] == "questions":
            draft = self.plan_step("02需求补充", lambda d,c: self.flow.chat(d,c,SCOPE+"零基础，4天，每天最多90分钟。"))
        if draft["phase"] == "sources":
            for card in list(draft["sources"]):
                if card["status"] == "new":
                    # Search and scoring still run, but this controlled rerun
                    # uses the same OpenStax page as the preceding acceptance.
                    adopt = card.get("url") == FALLBACK
                    draft = self.plan_step("03资料选择"+card["card_id"], lambda d,c,k=card["card_id"],a=adopt:self.flow.decide(d,k,a))
            if not any(c["status"] == "adopted" for c in draft["sources"]):
                self.coverage["pasted_url"] = FALLBACK
                draft = self.plan_step("04权威不足贴网址", lambda d,c:self.flow.more_sources(d,c,FALLBACK))
            ce.dump(OUT / "research.json", draft)
            draft = self.plan_step("05路线轮廓", lambda d,c:self.flow.make_outline(d,c,SCOPE))
        if draft["phase"] == "outline":
            ce.dump(OUT / "outline.json", draft["outline"])
            mods = [m for s in draft["outline"]["stages"] for m in s["modules"]]
            if draft["outline"].get("coverage_notice"):
                self.coverage["coverage_notice"] = draft["outline"]["coverage_notice"]
                draft = self.plan_step("06按资料覆盖范围生成", lambda d,c:self.flow.make_outline(d,c,"按资料范围生成"))
            elif len(mods) != 3:
                draft = self.plan_step("06调整为三个模块", lambda d,c:self.flow.make_outline(d,c,SCOPE))
            self.plan_step("07正式生成前预估", lambda d,c:self.flow.estimate(d))
        if draft["phase"] in ("outline", "generating"):
            draft = self.plan_step("08正式生成", lambda d,c:self.flow.generate(d,c))
            # A duplicate rejection can return a reduced outline. Consent is
            # the real product action, never a draft/DB repair or forced count.
            if draft["phase"] == "outline" and draft["outline"].get("coverage_notice"):
                self.coverage["generation_coverage_notice"] = draft["outline"]["coverage_notice"]
                self.plan_step("09确认调整后资料范围", lambda d,c:self.flow.make_outline(d,c,"按资料范围生成"))
                draft = self.plan_step("10重试正式生成", lambda d,c:self.flow.generate(d,c))
        if draft["phase"] != "ready": raise RuntimeError("建计划未完成："+draft["phase"])
        self.call("plan.start")
        self.quiet()
        ce.dump(OUT / "generated.json", dict(plan=self.store.get_plan(self.plan_id),
            modules=self.store.records("modules", self.plan_id), points=self.store.records("points", self.plan_id)))

    def goto(self, offset):
        value = f"{base.START+timedelta(days=offset)}T10:00:00+08:00"
        self.clock.set(value)
        ce.dump(OUT / "clock.json", dict(value=value))

    def record_day(self, label):
        plans = self.registry.methods["plans.list"]({}, None)["plans"]
        self.days.append(dict(label=label, day=self.day().isoformat(),
            value=next(p for p in plans if p["id"]==self.plan_id)))
        ce.dump(OUT / "days.json", self.days)

    def student(self, mode):
        pending = self.state().get("pending") or {}
        points = {p["id"]:p for p in self.store.records("points",self.plan_id)}
        chunks = {cid for pid in pending.get("point_ids",[]) for cid in points[pid]["chunk_ids"]}
        textbook = {c["id"]:c["text"][:14000] for c in self.store.records("source_chunks",self.plan_id) if c["id"] in chunks}
        instruction = {"brief":"只写最简短的正确答案，允许代词和省略，不要另加无关解释。",
            "result_only":"只给最终结果，故意不写题目要求的过程或中间等式。",
            "process":"补充刚才题目要求的那一步等式与得到它的运算。"}.get(mode, "")
        packet = dict(persona="零基础初中生，复习后认真回答", mode="correct" if mode in ("brief","process") else mode,
            instruction=instruction, question=self.view.get("question"), textbook=textbook,
            dialogue=[dict(role=m["role"],text=m["content"]) for m in self.view["messages"][-5:]])
        self.gw.operation = "学生作答:"+mode
        messages = [dict(role="user",content=json.dumps(packet,ensure_ascii=False))]
        system = ce.STUDENT_SYSTEM+"\n必须遵守instruction的表达方式；不能看到标准答案。" + (
            "\n本次只给最终结果，故意省略过程；这条优先于mode=correct的完整作答要求。" if mode=="result_only" else "")
        try:
            result = self.gw.complete("simulated_student", messages, system=system, max_tokens=4096)
        except ModelError as exc:
            if exc.code != "MODEL_OUTPUT_TRUNCATED": raise
            # A test student exhausting its thinking budget is not a product failure.
            # Record the failed call and pay for at most one larger retry.
            result = self.gw.complete("simulated_student", messages, system=system, max_tokens=8192)
        return _json_object(result.text)["reply"]


    def reopen(self, label):
        before = self.state()
        counts = {k:len(self.store.records(k,self.plan_id)) for k in ("judgements","practice_results","question_answers")}
        self.quiet()
        for r in self.rooms:
            if r._prep_timer: r._prep_timer.cancel()
            r._unsubscribe_bank()
        self.bind()
        self.call("classroom.state")
        self.faults.append(dict(label=label, before=before, after=self.state(), counts_before=counts,
            counts_after={k:len(self.store.records(k,self.plan_id)) for k in counts}))
        ce.dump(OUT / "reopens.json", self.faults)

    def reply_with_fault(self, text):
        state = self.state()
        pending = state.get("pending") or {}
        tag = "judgement_before_teacher" if pending.get("kind") == "casual" and not self.coverage.get("judgement_before_teacher") else None
        test = state.get("test") or {}
        if test and test.get("index") == len(test.get("queue",[]))-1 and not self.coverage.get("module_close_crash"):
            tag = "module_close_crash"
        if not tag: return self.call("classroom.reply", text=text)
        count = len(self.store.records("judgements",self.plan_id))
        original, closed, save = self.gw.complete, self.store.append_attempt_closed, self.room._save
        triggered = []
        def network(purpose, *a, **kw):
            if tag == "judgement_before_teacher" and purpose == "teacher" and len(self.store.records("judgements",self.plan_id))>count:
                triggered.append(True)
                raise InterruptedError("验收：判定入库后、老师请求前断电")
            return original(purpose,*a,**kw)
        def close(*a,**kw):
            value = closed(*a,**kw)
            if tag == "module_close_crash":
                triggered.append(True)
                raise InterruptedError("验收：模块关闭事件入库后断电")
            return value
        def persist(*a,**kw):
            if not triggered: return save(*a,**kw)
        try:
            with patch.object(self.gw,"complete",network), patch.object(self.store,"append_attempt_closed",close), patch.object(self.room,"_save",persist):
                return self.call("classroom.reply",text=text)
        except InterruptedError:
            self.coverage[tag] = True
            self.reopen(tag)
            if self.view.get("question"):
                self.call("classroom.reply",text=text)
            self.faults[-1]["after_resume"] = dict(state=self.state(),
                answer_id=pending.get("answer_id"), occurrences=sum(j["id"]==pending.get("answer_id") for j in self.store.records("judgements",self.plan_id)))
            ce.dump(OUT / "reopens.json",self.faults)

    def session(self, module_limit=None):
        self.call("classroom.enter")
        repeated = 0
        last = None
        for _ in range(240):
            state, step = self.state(), self.view["step"]
            if state.get("bank_retry"):
                retry = state["bank_retry"]
                key = retry["module_id"] + ":" + retry["method"]
                count = self.coverage.setdefault("bank_retry_rounds", {}).get(key, 0)
                if count >= 2:
                    raise RuntimeError("真实按钮重备两轮仍无合格题，保留完整证据待人工处理")
                self.coverage["bank_retry_rounds"][key] = count + 1
                ce.dump(OUT / "coverage.json", self.coverage)
                if retry["method"] in ("start_check", "_next_check"): self.call("classroom.check")
                elif retry["method"] == "start_test": self.call("classroom.test", kind=retry["args"][0])
                else: raise RuntimeError("待重备操作未覆盖：" + retry["method"])
                continue
            key_step = "waiting_without_question" if step in ("checking","preparing_questions") and not state.get("pending") else step
            key = json.dumps([key_step,state.get("pending"),state.get("lesson",{}).get("position")],sort_keys=True)
            repeated = repeated+1 if key==last else 0
            last=key
            if repeated >= 4: raise RuntimeError("同一状态连续四次未推进；停止自动重复，留证待人工绕过")
            if step == "completed": break
            if step == "preparing_questions":
                self.quiet()
                self.call("classroom.state")
            elif step == "preparing_lesson":
                self.wait_lesson()
            elif step == "teach":
                if self.view.get("can_skip") and self.view["module"]["number"]==2 and not self.coverage.get("direct"):
                    self.coverage["direct"] = True
                    self.call("classroom.test",kind="skip_test")
                else: self.call("classroom.next")
            elif step == "lesson":
                lesson = self.view["lesson"]
                if not self.coverage.get("lesson_interrupt"):
                    self.call("classroom.reply",text="为什么等式左右做同样的运算还能相等？")
                    self.coverage["lesson_interrupt"] = True
                elif lesson["awaiting"] == "return": self.call("classroom.back")
                elif lesson["awaiting"] == "choice":
                    index=0 if not self.coverage.get("guide_first") else len(lesson["options"])-1
                    self.call("classroom.choose",option=index)
                    self.coverage["guide_first"]=True
                else: self.call("classroom.next")
            elif step in ("answer","testing","retesting","checking","practice") and self.view.get("question"):
                pending = state["pending"]
                mode="correct"
                if pending.get("process_reply"):
                    self.coverage["process_prompt_received"] = pending["answer_id"]
                    mode="process"
                elif pending["kind"] == "casual" and not self.coverage.get("casual_wrong"):
                    mode="wrong";self.coverage["casual_wrong"]=True
                elif pending["kind"] == "casual" and not self.coverage.get("claim_known"):
                    mode="claim_known";self.coverage["claim_known"]=True
                elif pending["kind"] == "casual" and not self.coverage.get("change_rules"):
                    mode="change_rules";self.coverage["change_rules"]=True
                elif step == "testing" and len(pending.get("point_ids",[]))>1 and not self.coverage.get("composite_wrong"):
                    mode="wrong";self.coverage["composite_wrong"]=True
                elif step == "testing" and len(pending.get("point_ids",[]))==1 and (state.get("test") or {}).get("kind")=="module_test" and not self.coverage.get("single_test_wrong"):
                    mode="wrong";self.coverage["single_test_wrong"]=True
                elif step == "checking" and not self.coverage.get("check_wrong"):
                    mode="wrong";self.coverage["check_wrong"]=True
                elif step == "checking" and len(pending.get("point_ids",[]))==1 and self.store.load_mastery(self.plan_id).point(pending["point_ids"][0]).level==2 and not self.coverage.get("check_level2_wrong"):
                    mode="wrong";self.coverage["check_level2_wrong"]=True
                elif self.phase == 2 and not self.coverage.get("strict_result_requested") and self.view["question"]["type"] in ("short","fix_error","exact") and re.search("步骤|过程|化简后的|第一步|代入.*化简|列.*方程",self.view["question"]["stem"]):
                    mode="result_only";self.coverage["strict_result_requested"]=pending["answer_id"]
                elif self.view["question"]["type"] in ("short","fix_error","exact") and re.search("步骤|过程|化简后的|第一步|代入.*化简",self.view["question"]["stem"]) and not self.coverage.get("process_probe"):
                    mode="result_only";self.coverage["process_probe"]=True
                elif self.view["question"]["type"] in ("short","fix_error") and not self.coverage.get("brief"):
                    mode="brief";self.coverage["brief"]=True
                elif self.view["question"]["type"] == "fix_error" and not self.coverage.get("result_only"):
                    mode="result_only";self.coverage["result_only"]=True
                text=self.student(mode)
                self.reply_with_fault(text)
                if step=="checking" and not self.coverage.get("final_reopen"):
                    self.reopen("最终检验中途");self.coverage["final_reopen"]=True
            elif step == "test_offer": self.call("classroom.test",kind="module_test")
            elif step == "test_done":
                if self.view.get("red"): self.call("classroom.test",kind="retest")
                elif module_limit and self.view["module"]["number"]>=module_limit: break
                else:
                    self.call("classroom.next_module")
                    if self.view["step"] != "check_offer": break
            elif step in ("check_offer","checking"): self.call("classroom.check")
            else: raise RuntimeError("未识别状态："+step)
            ce.dump(OUT / "coverage.json",self.coverage)
            self.dump()
        self.call("classroom.leave")

    def wait_lesson(self):
        """Mirror the page's ready event + 500ms state polling, no next click."""
        started = time.monotonic()
        point_id = (self.view.get("lesson_wait") or {}).get("point_id")
        samples = []
        while self.view["step"] == "preparing_lesson":
            if time.monotonic() - started > 500:
                raise RuntimeError("正在准备500秒仍未自动继续")
            self.ready_event.wait(.5)
            self.ready_event.clear()
            self.view = self.registry.methods["classroom.state"](
                dict(plan_id=self.plan_id), SimpleNamespace(cancel=threading.Event(), stream=lambda _:None))
            wait = self.view.get("lesson_wait") or {}
            samples.append(dict(seconds=time.monotonic()-started, step=self.view["step"], error=wait.get("error")))
            if wait.get("error"):
                ce.dump(OUT / "lesson-wait-failure.json", samples)
                raise RuntimeError("准备失败，需真实重试按钮：" + wait["error"])
        rows = read(OUT / "lesson-waits.json", [])
        rows.append(dict(point_id=point_id, seconds=time.monotonic()-started, samples=samples))
        ce.dump(OUT / "lesson-waits.json", rows)
        self.call("classroom.state")

    def finish_report_practice(self):
        report=self.call("plan.report")
        ce.dump(OUT / "learning-report.json",report)
        from PySide6.QtWidgets import QApplication, QFileDialog
        app=QApplication.instance() or QApplication([])
        for method,name in (("plan.report_export","learning-report.md"),("plan.report_pdf","learning-report.pdf")):
            with patch.object(QFileDialog,"getSaveFileName",return_value=(str(OUT/name),"")):
                ce.dump(OUT/(name+"-export.json"),self.call(method))
        before=copy.deepcopy(self.store.get_plan(self.plan_id))
        practice = self.state().get("practice")
        if not practice: self.call("classroom.practice",count=5)
        # Resume the same pending practice after a test-student request fails.
        # Do not reset its queue or start a second five-question attempt.
        start = (self.state().get("practice") or {}).get("index", 0)
        for i in range(start,5):
            while self.view["step"] == "preparing_questions":
                self.quiet();self.call("classroom.state")
            self.reply_with_fault(self.student("wrong" if i==0 else "correct"))
            if i==1:
                self.reopen("自由检验中途");self.coverage["practice_reopen"]=True
        ce.dump(OUT / "practice-invariants.json",dict(before=before,after=self.store.get_plan(self.plan_id),
            detail=self.call("plan.detail"), report_after=self.call("plan.report")))

    def script(self):
        if self.phase==1:
            if (OUT/"phase1-complete.json").exists(): raise RuntimeError("首段已完成，禁止重复付费")
            self.create();self.goto(0);self.record_day("D1上课前")
            self.session(module_limit=1);self.record_day("D1第一个模块完成")
            ce.dump(OUT/"phase1-complete.json",dict(spent=str(self.gw.spent())))
        else:
            if not (OUT/"phase1-audited.json").exists(): raise RuntimeError("需本人核对首段后再继续")
            if (OUT/"phase2-complete.json").exists(): raise RuntimeError("后半程已完成，禁止重复付费；组件补测需MF_MATH_PROBE")
            self.call("classroom.state")
            if self.view["step"]=="test_done" and not self.view.get("red"):
                self.call("classroom.next_module")
            for offset in range(max(1,(self.day()-base.START).days),4):
                if self.store.get_plan(self.plan_id)["state"]=="completed": break
                self.goto(offset);self.record_day(f"D{offset+1}前")
                self.session();self.record_day(f"D{offset+1}后")
                if self.store.get_plan(self.plan_id)["state"]=="completed": break
            if self.store.get_plan(self.plan_id)["state"]!="completed":
                raise RuntimeError("模拟窗口已用完，计划仍未完成；不得把未测后半程算通过")
            self.finish_report_practice()
            ce.dump(OUT/"phase2-complete.json",dict(spent=str(self.gw.spent()),plan_state=self.store.get_plan(self.plan_id)["state"]))

    def dump(self):
        ce.dump(OUT/"coverage.json",self.coverage)
        if not self.plan_id: return
        ce.dump(OUT/"plan.json",self.store.get_plan(self.plan_id))
        for kind in ("points","modules","sources","source_chunks","question_bank","judgements","credits","ledger_events","attempt_closed","question_answers","practice_results"):
            ce.dump(OUT/(kind+".json"),self.store.records(kind,self.plan_id))
        ce.dump(OUT/"lesson-cache.json",self.store.load_lesson_cache(self.plan_id))
        messages=self.store.records("messages",self.plan_id)
        lines=["# 一元一次方程：完整对话",""]
        for m in sorted(messages,key=lambda m:(m.get("seq",0),m["at"])):
            lines += [f"### {m['id']} · {m['role']} · {m.get('kind')} · {m.get('module_id')}","",m["content"],""]
        (OUT/"dialogue.md").write_text("\n".join(lines),encoding="utf-8")

    def close(self):
        for r in self.rooms:
            if r._prep_timer: r._prep_timer.cancel()
        self.quiet()
        self.dump()
        self.store.close()


def main():
    if os.environ.get("MF_MATH_REAL")!="1": raise RuntimeError("真实调用需MF_MATH_REAL=1")
    os.environ.setdefault("QT_QPA_PLATFORM","windows")  # real PDF needs installed Windows fonts
    phase=int(os.environ.get("MF_MATH_PHASE","1"))
    if phase not in (1,2): raise RuntimeError("阶段只能为1或2")
    if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    with ExitStack() as stack:
        run=MathRun(stack,phase)
        try:
            if os.environ.get("MF_MATH_PROBE")=="process": run.probe_process_component()
            else: run.script()
        finally: run.close()


def test_math_acceptance_real_opt_in():
    import pytest
    if os.environ.get("MF_MATH_REAL")!="1": pytest.skip("独立真实数学验收默认跳过")
    main()


def test_math_budget_reserves_effective_output_and_peak_before_call(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(sys.modules[__name__],"OUT",tmp_path)
    seen=[]
    prices=SimpleNamespace(estimate_cost=lambda *a,**kw:seen.append(kw) or SimpleNamespace(high=Decimal("0.2"),currency="CNY"))
    client=SimpleNamespace(get_configuration=lambda:dict(provider_id="deepseek",model="deepseek-flash"),
        estimate_context=lambda:{},price_catalog=prices)
    recorder=MathGateway(client,tmp_path,budget_path=tmp_path/"budget.json")
    recorder.rows=[dict(reserved="2.9")]
    with pytest.raises(ModelError):recorder.complete("generate",[dict(role="user",content="offline")],max_tokens=4096)
    assert seen[0]["output_tokens"]==(4096,4096)
    assert not (tmp_path/"calls").exists()


def test_math_evidence_dump_reads_real_store_contract(tmp_path, monkeypatch):
    from refactor.learning.course.ledger import PlanWindow
    monkeypatch.setattr(sys.modules[__name__],"OUT",tmp_path)
    store=CourseStore("offline_math_dump",tmp_path/"data")
    try:
        pid="a"*32
        store.create_plan(pid,goal=GOAL,final_goal="解方程",level="beginner",
            window=PlanWindow(base.START,base.START+timedelta(days=3),90))
        run=MathRun.__new__(MathRun)
        run.store,run.plan_id,run.coverage=store,pid,{}
        run.dump()
        assert read(tmp_path/"plan.json",{})["id"]==pid
        assert read(tmp_path/"judgements.json",None)==[]
        assert (tmp_path/"dialogue.md").is_file()
    finally:store.close()


if __name__=="__main__": main()
