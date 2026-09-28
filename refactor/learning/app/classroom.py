"""Classroom orchestration (ARCH-07; TEACH-01/02/03/04/05/06/07).

The host decides every step. The teacher agent only speaks, the judge agent
only classifies and judges, the question agent only writes questions and the
profile agent only keeps notes (ARCH-04). Everything durable lives in
CourseStore: classroom messages, mastery judgements, study-time credits and the
teacher profile. The lesson position is a small per-plan file on this computer
(classroom/<plan_id>.json); without it the position is rebuilt from the store.

Steps: teach -> answer (casual question) -> ... -> test_offer -> testing ->
test_done [-> retesting -> test_done] -> next module ... -> finished.
Points are study-time units: a taught point is credited as tentative time; a
module test confirms the mastered points and revokes the red ones (PLAN-05).
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
import threading
import time
import uuid

from ..connections.errors import MODEL_OUTPUT_TRUNCATED, ModelCancelled, ModelError
from ..course import judge_agent, profile_agent, question_agent, teacher_agent
from ..course.mastery import AttemptClosed, Judgement
from ..course.planner_agents import LEVELS, AgentFormatError
from ..course.store import StoreError
from ..preferences.storage import AccountJSON, StorageError
from .bridge import BridgeError, CallContext

log = logging.getLogger(__name__)

_PLAN_ID = re.compile(r"[a-f0-9]{32}")
TRANSIENT = frozenset({"MODEL_RESPONSE_INVALID", "MODEL_CONNECTION_FAILED", "MODEL_TIMEOUT"})
DIALOGUE_ROWS = 40          # layer 3 of TEACH-01: only the current module's recent dialogue
DIALOGUE_CHARS = 60000
RESET = "\x00"              # stream marker: the page drops what it showed (a retried answer follows)
PREF_KEYS = ("teacher_style", "detail", "examples", "analogy_domains")
CASUAL_ASKS = 2             # a point is asked casually at most twice (TEACH-02)
PLAN_PAGE = "「我的计划」页面"


def _now():
    return datetime.now(timezone.utc)


class Classroom:
    def __init__(self, services, study_day, *, now=_now):
        self.services, self.study_day, self.now = services, study_day, now
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._profiling: set[str] = set()
        self._entered_plans: set[str] = set()  # rooms entered in this process; a repeat enter keeps the day

    # ------------------------------------------------------------------ state
    def _file(self, plan_id):
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        return AccountJSON(self.services.account_id, self.services.data_root, f"classroom/{plan_id}.json")

    def _lock(self, plan_id):
        with self._guard:
            return self._locks.setdefault(plan_id, threading.Lock())

    def run(self, plan_id, work, ctx: CallContext | None = None):
        """One classroom operation per plan at a time; returns the page snapshot."""
        lock = self._lock(plan_id)
        if not lock.acquire(blocking=False):
            raise BridgeError("CLASS_BUSY", "老师还在说上一段，请稍等。")
        try:
            course = self._course(plan_id)
            state = self._load(course)
            try:
                work(course, state, ctx)
            except ModelCancelled:
                raise BridgeError("CANCELLED", "已停止。") from None
            except ModelError as exc:
                self._say(course, state, f"{exc.message} 可以稍后重试。", kind="error", role="system")
            except StoreError as exc:
                raise BridgeError("CLASS_STORE", exc.message) from None
            finally:
                self._save(course, state)
            return self.snapshot(course, state)
        finally:
            lock.release()

    def _load(self, course):
        try:
            state = self._file(course.plan_id).read({})
        except StorageError:
            state = {}
        modules = [m["id"] for m in course.modules]
        if state.get("version") != 1 or state.get("module_id") not in modules:
            state = self._rebuild(course)
        return state

    def _save(self, course, state):
        try:
            self._file(course.plan_id).write(state)
        except StorageError:
            log.warning("classroom position for %s could not be saved", course.plan_id)

    def _rebuild(self, course):
        """Position from the store: the first module not yet passed, its first untaught point."""
        book, taught = course.book(), course.taught()
        for module in course.modules:
            if book.module(module["id"]).state in ("passed", "skipped"):
                continue
            points = course.points_of(module["id"])
            index = next((i for i, p in enumerate(points) if p["id"] not in taught), len(points))
            step = "teach" if index < len(points) else ("test_offer" if book.module(module["id"]).state == "untested" else "test_done")
            return {"version": 1, "module_id": module["id"], "index": index, "step": step, "pending": None,
                    "test": None, "stems": [], "day": None, "resume": bool(taught)}
        last = course.modules[-1]["id"]
        return {"version": 1, "module_id": last, "index": len(course.points_of(last)), "step": "finished",
                "pending": None, "test": None, "stems": [], "day": None, "resume": False}

    def _course(self, plan_id):
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        store = self.services.store
        try:
            plan = store.get_plan(plan_id)
        except StoreError:
            raise BridgeError("PLAN_NOT_FOUND", "找不到这份计划。") from None
        if plan.get("state") not in ("active", "checking"):
            raise BridgeError("PLAN_NOT_STARTED", "这份计划还没开始，请先在新建计划里点“开始计划”。")
        return _Course(store, plan)

    # ------------------------------------------------------------------ page view
    def snapshot(self, course, state):
        book, taught = course.book(), course.taught()
        module = course.module(state["module_id"])
        points = course.points_of(module["id"])
        order = [m["id"] for m in course.modules]
        pending = state.get("pending")
        question = None
        if pending and pending.get("question"):
            q = pending["question"]
            question = {"type": q["type"], "stem": q["stem"], "options": q.get("options", []), "kind": pending["kind"],
                        "number": pending.get("number"), "total": pending.get("total")}
        messages = [{"id": m["id"], "role": m["role"], "kind": m.get("kind", "text"), "content": m["content"]}
                    for m in course.store.messages(course.plan_id, module["id"])]
        today = None
        try:
            today = self._today(course, state)
        except Exception:  # noqa: BLE001 - a broken ledger must not close the classroom
            log.warning("today status failed for %s", course.plan_id, exc_info=True)
        return {"plan": {"id": course.plan_id, "goal": course.plan.get("goal", ""), "state": course.plan.get("state")},
                "module": {"id": module["id"], "title": module["title"], "number": order.index(module["id"]) + 1,
                           "total": len(order), "state": book.module(module["id"]).state},
                "points": [{"id": p["id"], "title": p["title"], "minutes": p["minutes"], "taught": p["id"] in taught,
                            "state": book.point(p["id"]).state, "current": i == state["index"]}
                           for i, p in enumerate(points)],
                "step": state["step"], "question": question, "messages": messages,
                "can_skip": state["step"] == "teach" and state["index"] == 0 and not any(p["id"] in taught for p in points),
                "red": [p["title"] for p in points if book.point(p["id"]).state == "red"],
                "today": today}

    def _today(self, course, state):
        from datetime import time as clock
        settings = self.services.settings.load()
        hour, minute = map(int, settings["refresh_time"].split(":"))
        now = self.now().astimezone()
        day = state.get("day")
        from datetime import date
        return course.store.today_status(course.plan_id, now=now, refresh=clock(hour, minute), tz=now.tzinfo,
                                         classroom_day=date.fromisoformat(day) if day else None)

    # ------------------------------------------------------------------ entering / leaving
    def enter(self, course, state, ctx):
        """Pin the study day: whatever is learnt until leaving counts for this day (TEACH-05)."""
        # Duplicate enter requests in this live room are not an exit. A new
        # room/process must, however, discard a stale persisted in_session flag.
        with self._guard:
            if course.plan_id not in self._entered_plans or not state.get("in_session") or not state.get("day"):
                state["day"] = self.study_day().isoformat()
            self._entered_plans.add(course.plan_id)
        state["in_session"] = True

    def leave(self, course, state, ctx):
        with self._guard:
            self._entered_plans.discard(course.plan_id)
        if state.get("in_session"):
            state["in_session"] = False
            self._profile_later(course, state, "session_end")
        state["day"] = None

    def _day(self, state):
        return state.get("day") or self.study_day().isoformat()

    # ------------------------------------------------------------------ teaching
    def next(self, course, state, ctx):
        if state["step"] != "teach":
            raise BridgeError("STEP_INVALID", "现在不是讲新内容的时候。")
        self._teach_point(course, state, ctx, "")

    def _teach_point(self, course, state, ctx, lead):
        """Explain the current point and end with a casual question."""
        points = course.points_of(state["module_id"])
        point = points[state["index"]]
        resume = ""
        if state.get("resume"):
            resume = "这是这次上课的开头：先用一两句话接上学习者档案里“上次停在”的内容，再开始。"
        director = (f"{lead}{resume}讲解知识点《{point['title']}》：{point['summary']}（本模块第 {state['index'] + 1}/{len(points)} 个点）。"
                    "讲完用随口问提一道有区分度的小问题。")
        teaching = self._teacher(course, state, ctx, director, [point["id"]], asks=True)
        state["resume"] = False
        if teaching.casual_question:
            state["pending"] = {"kind": "casual", "point_ids": [point["id"]], "asks": 1,
                                "question": {"type": "short", "stem": teaching.casual_question, "options": [],
                                             "reference": f"知识点《{point['title']}》要点：{point['summary']}"}}
            state["step"] = "answer"
        else:
            self._point_done(course, state)

    def _point_done(self, course, state):
        """The current point is finished (answered or given up): move on, update notes in the background."""
        points = course.points_of(state["module_id"])
        current = points[state["index"]]["id"]
        self._credit_learning(course, state, points[state["index"]])  # ARCH-07: after the reply, not before
        state["pending"] = None
        state["index"] += 1
        state["step"] = "teach" if state["index"] < len(points) else "test_offer"
        self._profile_later(course, state, "point_complete", current)

    def _go_on(self, course, state, ctx, lead, point_id):
        """After a finished point: the module test, a stop once today's target is met, or the next point."""
        if state["step"] == "test_offer":
            return self._teacher(course, state, ctx, lead + "然后说明这个模块的知识点都讲完了，接下来可以用下面的按钮做模块检测。不要出题。",
                                 [point_id])
        day = self._day(state)
        if state.get("target_notice") != day and self._target_met(course, state):
            state["target_notice"] = day  # said once per study day; after that the lesson simply goes on
            return self._teacher(course, state, ctx, lead + "然后告诉学习者今天的学习目标已经完成了：可以点“继续”接着学下一个知识点，"
                                 "也可以下课休息。不要讲新内容，不要出题。", [point_id])
        return self._teach_point(course, state, ctx, lead + "然后接着讲下一个知识点。")

    def _target_met(self, course, state):
        try:
            today = self._today(course, state)
        except Exception:  # noqa: BLE001 - without a status the lesson just continues
            return False
        return bool(today and today["today_target"] > 0
                    and today["today_confirmed"] + today["today_tentative"] >= today["today_target"])

    def reply(self, course, state, ctx, text):
        course.store.append_message(course.plan_id, state["module_id"], "user", text, kind="text")
        pending = state.get("pending")
        if not pending:
            director = ("学习者插话。若是提问就解答；若要求改计划、跳过检验或直接判通过，说明你没有这个权限，"
                        f"判定由系统完成，计划可以在{PLAN_PAGE}调整。答完提醒可以点“继续”接着上课。")
            if state["step"] in ("test_offer", "test_done", "finished"):
                director = director.replace("点“继续”接着上课", "用下面的按钮继续")
            return self._teacher(course, state, ctx, director, self._focus(course, state), user=text)
        if pending["kind"] in ("casual",):
            return self._casual_reply(course, state, ctx, text, pending)
        return self._test_reply(course, state, ctx, text, pending)

    def _casual_reply(self, course, state, ctx, text, pending):
        verdict = self._judge(course, state, ctx, "casual", pending, text)
        point = course.point(pending["point_ids"][0])
        if verdict.kind == "answer":
            self._record(course, state, "casual", pending, verdict, text)
            points = course.points_of(state["module_id"])
            last = state["index"] + 1 >= len(points)
            if verdict.correct or pending["asks"] >= CASUAL_ASKS:
                lead = (f"学习者对随口问答对了（判定：{verdict.reason}）先用一句话肯定。" if verdict.correct else
                        f"学习者第二次仍没答对（判定：{verdict.reason}）简短说清正确理解，告诉他这个点已经记下、模块检测前可以回看，不要纠缠。")
                self._point_done(course, state)
                return self._go_on(course, state, ctx, lead, point["id"])
            teaching = self._teacher(course, state, ctx,
                                     f"学习者对随口问的回答有误（判定：{verdict.reason}）不要责备。换一个角度把《{point['title']}》里出错的地方再讲一遍，"
                                     "给个台阶，最后用随口问再确认一次（换一道题）。", [point["id"]], user=text, asks=True)
            if teaching.casual_question:
                pending["asks"] += 1
                pending["question"] = dict(pending["question"], stem=teaching.casual_question)
            else:
                self._point_done(course, state)
            return None
        if verdict.kind == "claim_known":
            return self._quick_check(course, state, ctx, pending)
        director = {
            "question": "学习者在回答随口问之前先提了个问题。先解答，然后提醒他上面的随口问还没回答（不要重复题目全文，不要出新题）。",
            "chat": "学习者在闲聊。用一两句自然回应，然后把话题带回上面还没回答的随口问（不要出新题）。",
            "change_rules": (f"学习者要求改变规则或进度。说明你没有这个权限，对错由系统判定，计划可以在{PLAN_PAGE}调整；"
                             "然后把话题带回上面的随口问（不要出新题）。"),
        }.get(verdict.kind, "没看出学习者是在回答随口问。请他直接回答上面的随口问，说“不会”也可以（不要出新题）。")
        teaching = self._teacher(course, state, ctx, director, pending["point_ids"], user=text, asks=True)
        if teaching.casual_question:  # restated (or reworded): answers are judged against what is on screen
            pending["question"] = dict(pending["question"], stem=teaching.casual_question)

    def _quick_check(self, course, state, ctx, pending):
        """'I know this': one quick confirmation question; wrong is recorded, never argued (TEACH-02)."""
        questions = self._questions(course, ctx, "quick_check", pending["point_ids"])
        q = questions[0]
        state["pending"] = {"kind": "quick", "point_ids": list(q.point_ids), "question": _question_dict(q)}
        self._say(course, state, "好，那来一道快速确认题，答对就直接往下讲。")
        self._say(course, state, _question_text(q), kind="question")

    # ------------------------------------------------------------------ tests
    def start_test(self, course, state, ctx, kind):
        book = course.book()
        module_id = state["module_id"]
        points = course.points_of(module_id)
        if kind == "skip_test":
            if state["step"] != "teach" or state["index"] != 0 or any(p["id"] in course.taught() for p in points):
                raise BridgeError("STEP_INVALID", "只有在模块开始前才能直接考。")
            targets = [p["id"] for p in points]
        elif kind == "module_test":
            if state["step"] != "test_offer":
                raise BridgeError("STEP_INVALID", "这个模块还没讲完，暂时不能做模块检测。")
            targets = [p["id"] for p in points]
        else:
            targets = [p["id"] for p in points if book.point(p["id"]).state == "red"]
            if state["step"] != "test_done" or not targets:
                raise BridgeError("STEP_INVALID", "现在没有需要重测的点。")
        self._say(course, state, {"skip_test": "好，直接考这个模块。正确率达到 90% 就跳过，不到就正常学。",
                                  "module_test": "开始模块检测。一题一题来，选择题可以直接点选。",
                                  "retest": "开始重测没过的点，每个点换个角度问，答对一次就算过。"}[kind], role="system")
        questions = self._questions(course, ctx, kind if kind == "retest" else "module_test", targets,
                                    avoid=state.get("stems", []))
        attempt = uuid.uuid4().hex[:16]
        state["test"] = {"kind": kind, "attempt": attempt, "queue": [_question_dict(q) for q in questions], "index": 0,
                         "targets": targets}
        state["stems"] = (state.get("stems", []) + [q.stem for q in questions])[-200:]
        state["step"] = "retesting" if kind == "retest" else "testing"
        self._ask_next(course, state)

    def _ask_next(self, course, state):
        test = state["test"]
        q = test["queue"][test["index"]]
        state["pending"] = {"kind": test["kind"], "point_ids": q["point_ids"], "question": q,
                            "number": test["index"] + 1, "total": len(test["queue"])}
        self._say(course, state, f"第 {test['index'] + 1}/{len(test['queue'])} 题：" + _question_text(q), kind="question")

    def _test_reply(self, course, state, ctx, text, pending):
        q = pending["question"]
        clicked = q["type"] == "choice" and text.strip() in q["options"]
        if clicked and (text.strip() == q["answer"] or len(q["point_ids"]) == 1):
            correct = text.strip() == q["answer"]
            verdict = judge_agent.Verdict("answer", correct, () if correct else tuple(q["point_ids"]),
                                          "选对了。" if correct else f"正确答案是：{q['answer']}。")
        else:
            verdict = self._judge(course, state, ctx, "test", pending, text)
        if verdict.kind != "answer":
            self._say(course, state, "没看出这是对这道题的作答。请直接回答；不会的话说“不会”也可以。", role="system")
            return
        context = {"quick": "quick_confirm", "skip_test": "skip_test", "module_test": "module_test",
                   "retest": "retest"}[pending["kind"]]
        self._record(course, state, context, pending, verdict, text)
        mark = "✓ 答对了。" if verdict.correct else "✗ 这题没答对。"
        explanation = verdict.reason if not verdict.correct else ""
        if q["type"] != "choice" and not verdict.correct:
            explanation += f" 参考答案：{q['answer']}"
        self._say(course, state, (mark + explanation).strip(), kind="verdict", role="system")
        if pending["kind"] == "quick":
            point = course.point(pending["point_ids"][0])
            self._point_done(course, state)
            lead = ("快速确认答对了，不用再讲这个点。" if verdict.correct else
                    "快速确认没答对，这个点已经记下，交给模块检测再看，不要纠缠。")
            return self._go_on(course, state, ctx, lead, point["id"])
        test = state["test"]
        test["index"] += 1
        if test["index"] < len(test["queue"]):
            return self._ask_next(course, state)
        self._finish_test(course, state, ctx)

    def _finish_test(self, course, state, ctx):
        test, module_id = state["test"], state["module_id"]
        points = course.points_of(module_id)
        day = self._day(state)
        state["pending"], state["test"] = None, None
        store = course.store
        if test["kind"] != "retest":
            store.append_attempt_closed(course.plan_id, AttemptClosed(uuid.uuid4().hex, course.next_seq(), module_id,
                                                                      test["attempt"], test["kind"]))
        book = course.book()
        states = {p["id"]: book.point(p["id"]).state for p in points}
        module_state = book.module(module_id).state
        if test["kind"] == "skip_test" and module_state == "untested":   # below 90%: learn it normally
            state["step"], state["index"] = "teach", 0
            wrong = [p["title"] for p in points if book.point(p["id"]).module_errors]
            return self._teacher(course, state, ctx, "直接考没有达到 90%，这个模块要正常学。用一两句话说明，"
                                 + (f"提到答错的点：{'、'.join(wrong)}；" if wrong else "") + "然后说可以点“继续”开始第一个点。不要讲课，不要出题。",
                                 [points[0]["id"]])
        if test["kind"] == "skip_test":
            for p in points:  # the whole module's time counts on the day of the direct test (TEACH-07)
                store.add_credit(course.plan_id, f"learn:{p['id']}", p["id"], p["minutes"], day, "confirmed")
        if test["kind"] in ("module_test", "skip_test"):
            store.add_credit(course.plan_id, f"test:{test['attempt']}", f"test:{module_id}",
                             max(1, course.module(module_id).get("check_minutes") or 1), day, "confirmed")
            for p in points:
                if states[p["id"]] == "red":
                    store.revoke_credits(course.plan_id, p["id"], today=day)
                else:
                    store.confirm_credits(course.plan_id, p["id"], today=day)
        else:
            for p in points:  # a red point answered right in a retest is learnt again: its time counts now
                if states[p["id"]] == "green" and p["id"] in test["targets"]:
                    store.add_credit(course.plan_id, f"relearn:{p['id']}", p["id"], p["minutes"], day, "confirmed")
        red = [p["title"] for p in points if states[p["id"]] == "red"]
        weak = [p["title"] for p in points if states[p["id"]] == "weak"]
        more = any(book.module(m["id"]).state == "untested" for m in course.modules if m["id"] != module_id)
        onward = "学下一个模块" if more else "结束模块学习、进入最终检验"
        mastered = sum(1 for p in points if states[p["id"]] == "green")
        state["step"] = "test_done"
        if module_state in ("passed", "skipped") and not red:
            outcome = (f"模块检测通过：{mastered}/{len(points)} 个点完全掌握。" if test["kind"] != "skip_test"
                       else f"直接考通过（{mastered}/{len(points)}），这个模块跳过。")
            director = outcome + (f"有小瑕疵的点：{'、'.join(weak)}，之后检验时会多问。" if weak else "") + \
                f"用两三句话告诉学习者结果，并说明可以用下面的按钮{onward}。不要出题。"
        else:
            director = (f"{'重测' if test['kind'] == 'retest' else '模块检测'}结束：{mastered}/{len(points)} 个点掌握，"
                        f"还没过的点：{'、'.join(red)}（需要掌握 90% 以上才算通过，没过的点换角度答对一次即可）。"
                        "用两三句话告诉学习者结果，针对没过的点各用一句话点出要回看的关键，"
                        f"说明可以重测这些点，也可以先{onward}（没过的点要在最终检验前重测通过）。不要出题。")
        self._teacher(course, state, ctx, director, [p["id"] for p in points if states[p["id"]] != "green"] or [points[0]["id"]])

    def next_module(self, course, state, ctx):
        if state["step"] not in ("test_done",):
            raise BridgeError("STEP_INVALID", "请先完成这个模块的检测。")
        book = course.book()
        after = [m for m in course.modules if book.module(m["id"]).state == "untested"]
        if after:
            state.update(module_id=after[0]["id"], index=0, step="teach", pending=None, test=None, stems=[])
            return
        failed = [m for m in course.modules if book.module(m["id"]).state == "failed"]
        if failed:
            state.update(module_id=failed[0]["id"], index=len(course.points_of(failed[0]["id"])), step="test_done",
                         pending=None, test=None)
            self._say(course, state, "所有模块都学过了。这个模块还有没过的点，重测通过后才能进入最终检验。", role="system")
            return
        state.update(step="finished", pending=None, test=None)
        self._say(course, state, "全部模块都通过了！接下来是最终检验（检验阶段即将接入）。", role="system")

    # ------------------------------------------------------------------ agents
    def _prefs(self):
        settings = self.services.settings.load()
        return {key: settings[key] for key in PREF_KEYS}

    def _skeleton(self, course, state, point_ids):
        module = course.module(state["module_id"])
        return ({"id": course.plan_id, "goal": course.plan.get("goal") or "学习", "final_goal": course.plan.get("final_goal") or "掌握",
                 "level": LEVELS.get(course.plan.get("level"), "未说明")},
                {"id": module["id"], "title": module["title"],
                 "points": [{"id": p["id"], "title": p["title"], "summary": p.get("summary", "")} for p in course.points_of(module["id"])]})

    def _dialogue(self, course, state):
        rows = []
        for m in course.store.messages(course.plan_id, state["module_id"]):
            role = "user" if m["role"] == "user" else "assistant"
            text = m["content"] if m["role"] != "system" else f"（系统）{m['content']}"
            rows.append({"role": role, "text": text})
        rows = rows[-DIALOGUE_ROWS:]
        while rows and sum(len(r["text"]) for r in rows) > DIALOGUE_CHARS:
            rows.pop(0)
        return rows

    def _focus(self, course, state):
        points = course.points_of(state["module_id"])
        return [points[min(state["index"], len(points) - 1)]["id"]]

    def _teacher(self, course, state, ctx, director, point_ids, *, user="", asks=False):
        """asks=False: a question the step did not ask for stays part of the explanation, not a card."""
        plan, module = self._skeleton(course, state, point_ids)
        profile = (course.store.records("teacher_profile", course.plan_id) or [{}])[0].get("document", {}) or {}
        tctx = {"prefs": self._prefs(), "plan": plan, "module": module, "excerpts": course.excerpts(point_ids),
                "dialogue": self._dialogue(course, state)[:-1] if user else self._dialogue(course, state),
                "profile": (profile.get("text") or "")[:6000], "director": director, "user": user}
        request = teacher_agent.teacher_request(tctx)
        mapping = teacher_agent.citation_map(tctx)
        stream = ctx.stream if ctx else (lambda _d: None)
        teaching, raw = None, ""
        max_tokens = 4096
        for attempt in range(3):
            if attempt:
                stream(RESET)
            try:
                result = self.services.gateway.complete("teacher", request[1], system=request[0], on_text=stream,
                                                        cancel=ctx.cancel if ctx else None, plan_id=course.plan_id,
                                                        max_tokens=max_tokens)
            except ModelError as exc:
                if exc.code == MODEL_OUTPUT_TRUNCATED and max_tokens < 8192:
                    max_tokens = 8192
                    continue
                if attempt or exc.code not in TRANSIENT or (ctx and ctx.cancel.is_set()):
                    raise
                continue
            raw = result.text
            try:
                teaching = teacher_agent.parse_teaching(raw, tctx)
                break
            except AgentFormatError as exc:
                log.info("teacher answer rejected (%s), attempt %s", exc, attempt + 1)
        if teaching is None:
            raise ModelError("AGENT_FORMAT", "老师这次的回答格式不对，已重试仍不行。")
        removed = teacher_agent.citation_diagnostics(raw, tctx)
        if removed:
            log.info("teacher cited unknown excerpts %s", removed)
        speech = _link_citations(teaching.speech, mapping)
        if teaching.casual_question and not asks:
            speech = (speech + "\n\n" + teaching.casual_question).strip()
            teaching = teacher_agent.Teaching(teaching.speech, None, teaching.cited_chunks)
        if speech:
            course.store.append_message(course.plan_id, state["module_id"], "assistant", speech, kind="teach")
        if teaching.casual_question:
            course.store.append_message(course.plan_id, state["module_id"], "assistant", teaching.casual_question, kind="casual")
        return teaching

    def _judge(self, course, state, ctx, mode, pending, reply):
        q = pending["question"]
        points = [course.point(pid) for pid in pending["point_ids"]]
        jctx = {"mode": mode, "points": [{"id": p["id"], "title": p["title"], "summary": p.get("summary", "")} for p in points],
                "excerpts": course.excerpts(pending["point_ids"]),
                "question": {"stem": q["stem"], "type": q["type"], "options": q.get("options", [])},
                "reference": q.get("reference") or q.get("answer") or "按知识点要点判断",
                "recent": self._dialogue(course, state)[-7:-1]}
        request = judge_agent.judge_request(jctx, reply)
        return self._ask(course, ctx, "judge", request, lambda text: judge_agent.parse_judgement(text, jctx), 2048)

    def _questions(self, course, ctx, purpose, point_ids, *, avoid=()):
        points = [course.point(pid) for pid in point_ids]
        excerpts = course.excerpts(point_ids)
        qctx = {"purpose": purpose, "per_point": 1, "excerpts": excerpts, "avoid": list(avoid)[-100:],
                "points": [{"id": p["id"], "title": p["title"], "summary": p.get("summary", ""),
                            "chunk_ids": [c for c in p["chunk_ids"] if c in excerpts] or p["chunk_ids"][:1]} for p in points]}
        request = question_agent.question_request(qctx)
        return self._ask(course, ctx, "questions", request, lambda text: question_agent.parse_questions(text, qctx), 8192)

    def _ask(self, course, ctx, purpose, request, parse, max_tokens):
        last = None
        for attempt in range(2):  # one retry on a malformed answer or a transient provider hiccup (ARCH-04)
            try:
                result = self.services.gateway.complete(purpose, request[1], system=request[0],
                                                        cancel=ctx.cancel if ctx else None, plan_id=course.plan_id,
                                                        max_tokens=max_tokens)
            except ModelError as exc:
                if exc.code == MODEL_OUTPUT_TRUNCATED and not attempt and max_tokens < 16384:
                    max_tokens, last = max_tokens * 2, exc
                    continue
                if attempt or exc.code not in TRANSIENT or (ctx and ctx.cancel.is_set()):
                    raise
                last = exc
                continue
            try:
                return parse(result.text)
            except AgentFormatError as exc:
                last = exc
                log.info("%s answer rejected (%s), attempt %s", purpose, exc, attempt + 1)
        raise ModelError("AGENT_FORMAT", "AI 的回答格式不对，已重试一次仍不行。") from last

    # ------------------------------------------------------------------ records
    def _record(self, course, state, context, pending, verdict, answer):
        wrong = set(verdict.wrong_points)
        results = tuple((pid, pid not in wrong) for pid in pending["point_ids"])
        attempt = (state.get("test") or {}).get("attempt") if context in ("module_test", "skip_test") else None
        event = Judgement(uuid.uuid4().hex, course.next_seq(), self.now(), context, state["module_id"], attempt, results)
        q = pending["question"]
        course.store.append_judgement(course.plan_id, event, details={
            "question": q["stem"], "answer": answer[:4000], "reason": verdict.reason, "chunk_ids": q.get("chunk_ids", [])})

    def _credit_learning(self, course, state, point):
        """A taught point counts as tentative study time until its module test (PLAN-05)."""
        try:
            course.store.add_credit(course.plan_id, f"learn:{point['id']}", point["id"], point["minutes"],
                                    self._day(state), "tentative")
        except Exception:  # noqa: BLE001 - an existing credit (taught again after a restart) is fine
            log.info("learning credit for %s not added", point["id"], exc_info=True)

    def _say(self, course, state, text, *, kind="text", role="system"):
        course.store.append_message(course.plan_id, state["module_id"], role, text, kind=kind)

    # ------------------------------------------------------------------ profile (background)
    def _profile_later(self, course, state, mode, point_id=None):
        plan_id = course.plan_id
        with self._guard:
            if plan_id in self._profiling:
                return
            self._profiling.add(plan_id)
        module_id = state["module_id"]

        def work():
            try:
                fresh = _Course(self.services.store, self.services.store.get_plan(plan_id))
                book = fresh.book()
                all_points = [p["id"] for p in fresh.points]
                weak = [p for p in all_points if book.point(p).casual_errors or book.point(p).module_errors
                        or book.point(p).state == "red"][-12:]
                plan, module = self._skeleton(fresh, {"module_id": module_id}, [])
                profile = (fresh.store.records("teacher_profile", plan_id) or [{}])[0].get("document", {}) or {}
                taught = len(fresh.taught())
                pctx = {"prefs": self._prefs(), "plan": plan, "module": module, "mode": mode,
                        "dialogue": self._dialogue(fresh, {"module_id": module_id}),
                        "profile": (profile.get("text") or "")[:6000], "known_point_ids": all_points,
                        "weak_point_ids": weak, "progress": f"已讲 {taught}/{len(all_points)} 个知识点，当前模块《{module['title']}》。"}
                if point_id:
                    pctx["current_point_id"] = point_id
                request = profile_agent.profile_request(pctx)
                result = self._ask(fresh, None, "profile", request, lambda text: profile_agent.parse_profile(text, pctx), 2048)
                fresh.store.save_teacher_profile(plan_id, {"text": result.text, "at": self.now().isoformat()})
            except Exception:  # noqa: BLE001 - notes are best effort and never block the lesson
                log.info("teacher profile update failed", exc_info=True)
            finally:
                with self._guard:
                    self._profiling.discard(plan_id)

        threading.Thread(target=work, name="classroom-profile", daemon=True).start()


class _Course:
    """One read of a plan's structure for the duration of an operation."""

    def __init__(self, store, plan):
        self.store, self.plan, self.plan_id = store, plan, plan["id"]
        self.modules = sorted(store.records("modules", self.plan_id), key=lambda m: (m["position"], m["id"]))
        position = {m["id"]: i for i, m in enumerate(self.modules)}
        self.points = sorted(store.records("points", self.plan_id),
                             key=lambda p: (position.get(p["module_id"], 0), p["position"], p["id"]))
        for point in self.points:
            point.setdefault("title", (point.get("summary") or "知识点")[:40])
        self._chunks = None
        if not self.modules or not self.points:
            raise BridgeError("PLAN_EMPTY", "这份计划还没有可学的内容。")

    def module(self, module_id):
        return next(m for m in self.modules if m["id"] == module_id)

    def points_of(self, module_id):
        return [p for p in self.points if p["module_id"] == module_id]

    def point(self, point_id):
        return next(p for p in self.points if p["id"] == point_id)

    def book(self):
        return self.store.load_mastery(self.plan_id)

    def next_seq(self):
        """Replay order for mastery events: after every recorded event, even if the clock went back."""
        seqs = [row["event"]["seq"] for entity in ("judgements", "attempt_closed")
                for row in self.store.records(entity, self.plan_id)]
        return max([time.time_ns() // 1000, *(seq + 1 for seq in seqs)])

    def taught(self):
        return {c["credit"]["unit_id"] for c in self.store.records("credits", self.plan_id)
                if c["id"].startswith(("learn:", "relearn:"))}

    def excerpts(self, point_ids):
        """Source text the given points were generated from (only these, to keep requests small)."""
        if self._chunks is None:
            self._chunks = {c["id"]: c["text"] for c in self.store.records("source_chunks", self.plan_id)}
        wanted = [c for pid in point_ids for c in self.point(pid)["chunk_ids"]]
        result = {c: self._chunks[c][:40000] for c in dict.fromkeys(wanted) if self._chunks.get(c, "").strip()}
        if not result:  # a point whose source text is gone still needs something to stand on
            first = next(iter(self._chunks.items()), None)
            if first is None:
                raise BridgeError("NO_MATERIAL", "这份计划的资料原文不见了，无法上课。")
            result = {first[0]: first[1][:40000]}
        return result


def _question_dict(q):
    return {"id": q.id, "type": q.type, "stem": q.stem, "options": list(q.options), "answer": q.answer,
            "reference": q.reference, "point_ids": list(q.point_ids), "chunk_ids": list(q.chunk_ids)}


def _question_text(q):
    q = q if isinstance(q, dict) else _question_dict(q)
    if q["type"] == "choice":
        return q["stem"] + "\n\n" + "\n".join(f"{'ABCD'[i]}. {o}" for i, o in enumerate(q["options"]))
    return q["stem"]


def _link_citations(text, mapping):
    """[c3] -> a link the page opens as the original source text."""
    numbers = {}

    def link(match):
        chunk = mapping.get(match.group(1).lower())
        if not chunk:
            return ""
        n = numbers.setdefault(chunk, len(numbers) + 1)
        return f"[〔{n}〕](mf-source:{chunk})"
    literal = re.compile(r"```.*?```|`[^`\n]+`", re.S)
    out, end = [], 0
    for m in literal.finditer(text):
        out.append(re.sub(r"\[([cC]\d+)\]", link, text[end:m.start()]))
        out.append(m.group())
        end = m.end()
    out.append(re.sub(r"\[([cC]\d+)\]", link, text[end:]))
    return "".join(out)


def register(bridge, services, study_day, *, presence=lambda active: None):
    rooms: dict[int, Classroom] = {}

    def room():
        current = services()
        key = id(current)
        if key not in rooms:
            rooms.clear()
            rooms[key] = Classroom(current, lambda: study_day(current.settings.load()))
        return rooms[key]

    def text(params, key="text", limit=4000):
        value = params.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise BridgeError("PARAM_INVALID", "内容不能为空，也不能太长。")
        return value.strip()

    def source(params, ctx):
        r = room()
        course = r._course(params.get("plan_id"))
        chunk_id = params.get("chunk_id")
        chunk = next((c for c in course.store.records("source_chunks", course.plan_id) if c["id"] == chunk_id), None)
        if chunk is None:
            raise BridgeError("NOT_FOUND", "找不到这段原文。")
        sources = {s["id"]: s for s in course.store.records("sources", course.plan_id)}
        origin = sources.get(chunk["source_id"], {})
        return {"text": chunk["text"], "section": chunk.get("section") or [],
                "title": origin.get("title") or origin.get("filename") or "资料", "url": origin.get("url")}

    def kind(params):
        value = params.get("kind")
        if value not in ("module_test", "skip_test", "retest"):
            raise BridgeError("PARAM_INVALID", "检测类型无效。")
        return value

    def entered(params, ctx):
        view = room().run(params.get("plan_id"), room().enter, ctx)
        presence(True)
        return view

    def left(params, ctx):
        presence(False)
        return room().run(params.get("plan_id"), room().leave, ctx)

    handlers = {
        "classroom.enter": entered,
        "classroom.state": lambda p, c: room().run(p.get("plan_id"), lambda *a: None, c),
        "classroom.next": lambda p, c: room().run(p.get("plan_id"), room().next, c),
        "classroom.reply": lambda p, c: room().run(p.get("plan_id"), lambda co, st, cx: room().reply(co, st, cx, text(p)), c),
        "classroom.test": lambda p, c: room().run(p.get("plan_id"), lambda co, st, cx: room().start_test(co, st, cx, kind(p)), c),
        "classroom.next_module": lambda p, c: room().run(p.get("plan_id"), room().next_module, c),
        "classroom.leave": left,
        "classroom.source": source,
    }
    for name, fn in handlers.items():
        bridge.register(name, fn)
