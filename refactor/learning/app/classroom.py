"""Classroom orchestration (ARCH-07; TEACH-01/02/03/04/05/06/07).

The host decides every step. The teacher agent only speaks, the judge agent
only classifies and judges, the question agent only writes questions and the
profile agent only keeps notes (ARCH-04). Everything durable lives in
CourseStore: classroom messages, mastery judgements, study-time credits and the
teacher profile. The lesson position is a small per-plan file on this computer
(classroom/<plan_id>.json); without it the position is rebuilt from the store.

Steps: teach -> answer (casual question) -> ... -> test_offer -> testing ->
test_done [-> retesting -> test_done] -> next module ... -> check_offer ->
checking (final check, CHK-01) -> completed.
Points are study-time units: a taught point is credited as tentative time; a
module test confirms the mastered points and revokes the red ones (PLAN-05).
"""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict
import copy
import logging
import json
import random
import re
import threading
import time
import uuid

from ..connections.errors import MODEL_OUTPUT_TRUNCATED, ModelCancelled, ModelError
from ..course import check_agent, judge_agent, lesson_prep, profile_agent, question_agent, question_bank, teacher_agent
from ..course.request_layout import dialogue_tail
from ..course.mastery import ANGLES, AttemptClosed, Judgement
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
VERDICT_EVENT = "\x01VERDICT:"  # early, display-only result before teacher feedback
PREF_KEYS = ("teacher_style", "detail", "examples", "analogy_domains")
CASUAL_ASKS = 2             # a point is asked casually at most twice (TEACH-02)
PLAN_PAGE = "「我的计划」页面"
CHECK_BATCH = 4             # final-check questions asked per batch (points of one module)


def _now():
    return datetime.now(timezone.utc)


class Classroom:
    def __init__(self, services, study_day, *, now=_now, rng=None):
        self.services, self.study_day, self.now = services, study_day, now
        self._rng = rng or random.Random()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._profiling: set[str] = set()
        self._profile_jobs: dict[str, list[tuple[str, str]]] = {}
        self._preparing: set[str] = set()
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
            before_test = state.get("test")
            try:
                if ctx is not None and self._test_already_closed(course, state):
                    self._finish_test(course, state, ctx)  # finish the interrupted close instead of the action (BUG-23)
                else:
                    work(course, state, ctx)
            except ModelCancelled:
                raise BridgeError("CANCELLED", "已停止。") from None
            except ModelError as exc:
                self._say(course, state, f"{exc.message} 可以稍后重试。", kind="error", role="system")
            except StoreError as exc:
                raise BridgeError("CLASS_STORE", exc.message) from None
            finally:
                if (before_test and before_test.get("kind") in ("module_test", "skip_test", "retest")
                        and state.get("test") is None):
                    self._profile_later(course, state, "module_complete")
                self._save(course, state)
            return self.snapshot(course, state)
        finally:
            lock.release()

    @staticmethod
    def _test_already_closed(course, state):
        test = state.get("test")
        return bool(test and test.get("kind") in ("module_test", "skip_test") and test.get("attempt")
                    and course.book().attempt_closed(state["module_id"], test["attempt"]))

    def _load(self, course):
        try:
            state = self._file(course.plan_id).read({})
        except StorageError:
            state = {}
        modules = [m["id"] for m in course.modules]
        if state.get("version") != 1 or state.get("module_id") not in modules:
            state = self._rebuild(course)
        if (state.get('step') == 'completed') != (course.plan.get('state') == 'completed'):
            state = self._rebuild(course)
        return state

    def _save(self, course, state):
        try:
            self._file(course.plan_id).write(state)
        except StorageError as exc:
            raise BridgeError('CLASS_SAVE_FAILED', f'课堂位置保存失败：{exc}') from exc

    def _rebuild(self, course):
        """Position from the store: the first module not yet passed, its first untaught point."""
        book, taught = course.book(), course.taught()
        last = course.modules[-1]["id"]
        if course.plan.get("state") in ("checking", "completed"):
            return {"version": 1, "module_id": last, "index": len(course.points_of(last)), "phase": "check",
                    "step": "completed" if course.plan["state"] == "completed" else "checking",
                    "pending": None, "test": None, "check": None, "stems": [], "day": None, "resume": False}
        for module in course.modules:
            if book.module(module["id"]).state in ("passed", "skipped"):
                continue
            points = course.points_of(module["id"])
            index = next((i for i, p in enumerate(points) if p["id"] not in taught), len(points))
            step = "teach" if index < len(points) else ("test_offer" if book.module(module["id"]).state == "untested" else "test_done")
            return {"version": 1, "module_id": module["id"], "index": index, "step": step, "pending": None,
                    "test": None, "stems": [], "day": None, "resume": bool(taught)}
        return {"version": 1, "module_id": last, "index": len(course.points_of(last)), "step": "check_offer",
                "pending": None, "test": None, "stems": [], "day": None, "resume": False}

    def _course(self, plan_id):
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        store = self.services.store
        try:
            plan = store.get_plan(plan_id)
        except StoreError:
            raise BridgeError("PLAN_NOT_FOUND", "找不到这份计划。") from None
        if plan.get("state") not in ("active", "checking", "completed"):
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
                    for m in course.store.messages(course.plan_id, self._channel(state))]
        check = None
        if state.get("phase") == "check" or state["step"] in ("check_offer", "completed"):
            levels = [book.point(p["id"]).level for p in course.points]
            check = {"passed": sum(level == 3 for level in levels), "total": len(levels)}
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
                "more_modules": any(book.module(m["id"]).state == "untested" for m in course.modules if m["id"] != module["id"]),
                "check": check, "today": today}

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
        self._prepare_later(course, state)

    def leave(self, course, state, ctx):
        with self._guard:
            self._entered_plans.discard(course.plan_id)
        if state.get("in_session"):
            state["in_session"] = False
            self._profile_later(course, state, "session_end")
        state["day"] = None

    @staticmethod
    def _channel(state):
        return None if state.get("phase") == "check" else state["module_id"]

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
        prepared = self._prepared_point(course, point)
        if prepared is not None:
            speech = lesson_prep.render_lesson(prepared, title=point["title"], chunk_ids=tuple(point["chunk_ids"]))
            course.store.append_message(course.plan_id, self._channel(state), "assistant", speech, kind="teach")
            course.store.append_message(course.plan_id, self._channel(state), "assistant",
                                        prepared.casual_question, kind="casual")
            question = prepared.casual_question
        else:
            resume = ""
            if state.get("resume"):
                resume = "这是这次上课的开头：先用一两句话接上学习者档案里“上次停在”的内容，再开始。"
            director = (f"{lead}{resume}讲解知识点《{point['title']}》：{point['summary']}（本模块第 {state['index'] + 1}/{len(points)} 个点）。"
                        "讲完用随口问提一道有区分度的小问题。")
            teaching = self._teacher(course, state, ctx, director, [point["id"]], asks=True)
            question = teaching.casual_question
        state["resume"] = False
        if question:
            state["pending"] = {"kind": "casual", "answer_id": uuid.uuid4().hex,
                                "point_ids": [point["id"]], "asks": 1,
                                "question": {"type": "short", "stem": question, "options": [],
                                             "reference": prepared.reference_answer if prepared else
                                             f"知识点《{point['title']}》要点：{point['summary']}"}}
            state["step"] = "answer"
        else:
            self._point_done(course, state)

    def _point_done(self, course, state):
        """The current point is finished; module-end/leave updates the profile."""
        points = course.points_of(state["module_id"])
        self._credit_learning(course, state, points[state["index"]])  # ARCH-07: after the reply, not before
        state["pending"] = None
        state["index"] += 1
        state["step"] = "teach" if state["index"] < len(points) else "test_offer"

    def _go_on(self, course, state, ctx, lead, point_id, *, commented=False):
        """After a finished point: the module test, a stop once today's target is met, or the next point."""
        if state["step"] == "test_offer":
            if commented:
                return self._say(course, state, "这个模块的知识点已讲完，可以用下面的按钮开始模块检测。")
            return self._teacher(course, state, ctx, lead + "然后说明这个模块的知识点都讲完了，接下来可以用下面的按钮做模块检测。不要出题。",
                                 [point_id])
        day = self._day(state)
        if state.get("target_notice") != day and self._target_met(course, state):
            state["target_notice"] = day  # said once per study day; after that the lesson simply goes on
            if commented:
                return self._say(course, state, "今天的学习目标已完成，可以继续下一点，也可以下课休息。")
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
        course.store.append_message(course.plan_id, self._channel(state), "user", text, kind="text")
        pending = state.get("pending")
        if not pending:
            director = ("学习者插话。若是提问就解答；若要求改计划、跳过检验或直接判通过，说明你没有这个权限，"
                        f"判定由系统完成，计划可以在{PLAN_PAGE}调整。答完提醒可以点“继续”接着上课。")
            if state["step"] in ("test_offer", "test_done", "check_offer", "completed"):
                director = director.replace("点“继续”接着上课", "用下面的按钮继续")
            return self._teacher(course, state, ctx, director, self._focus(course, state), user=text)
        if pending["kind"] in ("casual",):
            return self._casual_reply(course, state, ctx, text, pending)
        if pending['kind'] == 'practice':
            return self._practice_reply(course, state, ctx, text, pending)
        if pending["kind"] == "check":
            return self._check_reply(course, state, ctx, text, pending)
        return self._test_reply(course, state, ctx, text, pending)

    def _casual_reply(self, course, state, ctx, text, pending):
        verdict = self._prior_verdict(course, state, pending) or self._judge(course, state, ctx, "casual", pending, text)
        point = course.point(pending["point_ids"][0])
        if verdict.kind == "answer":
            self._record(course, state, "casual", pending, verdict, text)
            result_text = "✓ 答对了。" if verdict.correct else f"✗ 这题没答对。{verdict.reason}"
            self._say(course, state, result_text, kind="verdict", role="system")
            if ctx is not None:
                ctx.stream(VERDICT_EVENT + json.dumps({"text": result_text, "correct": verdict.correct}, ensure_ascii=False))
            if verdict.correct or pending["asks"] >= CASUAL_ASKS:
                lead = (f"学习者对随口问答对了（判定：{verdict.reason}）。只用一两句针对这次回答点评，不讲下一点、不出题。" if verdict.correct else
                        f"学习者第二次仍没答对（判定：{verdict.reason}）。只用一两句说明关键错误，告诉他已记下、模块检测前可回看，不出题。")
                self._teacher(course, state, ctx, lead, [point["id"]], user=text, short=True)
                self._point_done(course, state)
                return self._go_on(course, state, ctx, "", point["id"], commented=True)
            teaching = self._teacher(course, state, ctx,
                                     f"学习者对随口问的回答有误（判定：{verdict.reason}）。用一两句换角度说清关键错误，"
                                     "给个台阶，最后用随口问换一道题确认，不讲下一点。", [point["id"]], user=text, asks=True, short=True)
            if teaching.casual_question:
                pending["asks"] += 1
                pending["answer_id"] = uuid.uuid4().hex
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
            pending["answer_id"] = uuid.uuid4().hex
            pending["question"] = dict(pending["question"], stem=teaching.casual_question)

    def _quick_check(self, course, state, ctx, pending):
        """'I know this': one quick confirmation question; wrong is recorded, never argued (TEACH-02)."""
        questions = self._questions(course, ctx, "quick_check", pending["point_ids"])
        q = questions[0]
        state["pending"] = {"kind": "quick", "answer_id": uuid.uuid4().hex,
                            "point_ids": list(q.point_ids), "question": _question_dict(q)}
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
        unstable = [pid for pid in targets if kind == 'retest' or book.point(pid).module_errors or
                    book.point(pid).casual_errors or book.point(pid).state in ('weak', 'red')]
        questions = self._bank_select(course, ctx, module_id, targets, unstable)
        attempt = uuid.uuid4().hex[:16]
        state["test"] = {"kind": kind, "attempt": attempt, "queue": questions, "index": 0,
                         "targets": targets, "split_single": [], "max_questions": max(len(questions), 4 * len(targets))}
        state["stems"] = (state.get("stems", []) + [q["stem"] for q in questions])[-200:]
        state["step"] = "retesting" if kind == "retest" else "testing"
        self._ask_next(course, state)

    def _ask_next(self, course, state):
        test = state["test"]
        q = test["queue"][test["index"]]
        if q['type'] == 'choice' and not q.get('shuffled'):
            self._rng.shuffle(q['options']); q['shuffled'] = True
        state["pending"] = {"kind": test["kind"], "answer_id": uuid.uuid4().hex,
                            "point_ids": q["point_ids"], "question": q,
                            "number": test["index"] + 1, "total": len(test["queue"])}
        self._say(course, state, f"第 {test['index'] + 1}/{len(test['queue'])} 题：" + _question_text(q), kind="question")

    def _test_reply(self, course, state, ctx, text, pending):
        q = pending["question"]
        prior = self._prior_verdict(course, state, pending)
        clicked = q["type"] == "choice" and text.strip() in q["options"]
        if prior is not None:
            verdict = prior
        elif clicked and (text.strip() == q["answer"] or len(q["point_ids"]) == 1):
            correct = text.strip() == q["answer"]
            verdict = judge_agent.Verdict("answer", correct, () if correct else tuple(q["point_ids"]),
                                          "选对了。" if correct else f"正确答案是：{q['answer']}。")
        else:
            verdict = self._judge(course, state, ctx, "test", pending, text)
        if verdict.kind == 'clarify' and not pending.get('clarified'):
            pending['clarified'] = True
            self._say(course, state, verdict.reason, role='system')
            return
        if verdict.kind != "answer":
            self._say(course, state, "没看出这是对这道题的作答。请直接回答；不会的话说“不会”也可以。", role="system")
            return
        context = {"quick": "quick_confirm", "skip_test": "skip_test", "module_test": "module_test",
                   "retest": "retest"}[pending["kind"]]
        if not verdict.correct and len(q['point_ids']) > 1:
            self._split_test_question(course, state, ctx, q)
        else:
            self._record(course, state, context, pending, verdict, text)
        if verdict.correct and q.get('id'):
            course.store.mark_bank_correct(course.plan_id, q['id'])
        mark = "✓ 答对了。" if verdict.correct else "✗ 这题没答对。"
        explanation = ("综合题未通过，暂不归因到任何知识点；接下来拆开问。" if len(q['point_ids']) > 1
                       else verdict.reason) if not verdict.correct else ""
        if q["type"] != "choice" and not verdict.correct and len(q['point_ids']) == 1:
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

    def _bank_select(self, course, ctx, module_id, targets, unstable=(), *, limit=None,
                     smaller_than=None, single_once=(), asked=(), used_angles=None, background=False):
        def select():
            return question_bank.choose_questions(self._bank_rows(course, module_id), targets,
                unstable, limit=limit, rng=self._rng, smaller_than=smaller_than, single_once=single_once,
                asked=asked, used_angles=used_angles)
        chosen, missing = select()
        if missing:
            self._bank_fill(course, ctx, module_id, background=background)
            chosen, missing = select()
        if missing and used_angles is not None and (limit is None or len(chosen) < limit):
            extra, missing = question_bank.choose_questions(self._bank_rows(course, module_id), missing,
                set(unstable) & set(missing), limit=None if limit is None else limit - len(chosen), rng=self._rng,
                smaller_than=smaller_than, single_once=single_once,
                asked=[*asked, *(q['id'] for q in chosen)])
            chosen.extend(extra)
        if missing:
            raise BridgeError('QUESTION_BANK_INCOMPLETE', '题库暂时不足，稍后再试。')
        return [dict(q) for q in chosen]

    def _bank_rows(self, course, module_id):
        fingerprint = self._test_fingerprint(course, module_id)
        return [q for q in course.store.records('question_bank', course.plan_id)
                if q['module_id'] == module_id and q.get('fingerprint', fingerprint) == fingerprint]

    def _bank_fill(self, course, ctx, module_id, *, background=False):
        ids = [p['id'] for p in course.points_of(module_id)]
        old = [q['stem'] for q in course.store.records('question_bank', course.plan_id)
               if q['module_id'] == module_id]
        batch = self._questions(course, ctx, 'bank', ids, avoid=old, background=background)
        course.store.add_bank_questions(course.plan_id, module_id, batch,
                                        fingerprint=self._test_fingerprint(course, module_id))

    def _split_test_question(self, course, state, ctx, question):
        """A composite error provides no point verdict; insert smaller probes once."""
        test = state['test']
        ids = question['point_ids']
        once = set(test['split_single'])
        slots = test['max_questions'] - len(test['queue'])
        if slots < len(ids):
            return
        unstable = [pid for pid in ids if course.book().point(pid).module_errors or
                    course.book().point(pid).casual_errors or course.book().point(pid).state in ('red', 'weak')]
        probes = self._bank_select(course, ctx, state['module_id'], ids, unstable,
            limit=min(len(ids), slots), smaller_than=len(ids), single_once=once,
            asked=[q['id'] for q in test['queue']])
        for probe in probes:
            if len(probe['point_ids']) == 1: once.add(probe['point_ids'][0])
        test['split_single'] = sorted(once)
        test['queue'][test['index'] + 1:test['index'] + 1] = probes

    def _finish_test(self, course, state, ctx):
        test, module_id = state["test"], state["module_id"]
        points = course.points_of(module_id)
        day = self._day(state)
        state["pending"], state["test"] = None, None
        store = course.store
        # A crash after the close but before the room state was saved leaves the attempt closed in the
        # store; finishing again must not close it twice (BUG-23). The steps below are idempotent.
        if test["kind"] != "retest":
            if not course.book().attempt_closed(module_id, test["attempt"]):
                store.append_attempt_closed(course.plan_id, AttemptClosed(uuid.uuid4().hex, course.next_seq(), module_id,
                                                                          test["attempt"], test["kind"]))
            if "final_check_minutes" in course.module(module_id):
                store.settle_module_test(course.plan_id, module_id, test["attempt"], today=day)
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
            if "final_check_minutes" not in course.module(module_id):
                store.settle_module_test(course.plan_id, module_id, test["attempt"], today=day)
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
        state.update(step="check_offer", pending=None, test=None)
        self._say(course, state, "全部模块都通过了！接下来是最终检验：每个知识点换不同角度问，答对升级，"
                                 "全部升到最高级就完成整个计划。", role="system")

    # ------------------------------------------------------------------ final check (CHK-01)
    def start_check(self, course, state, ctx):
        if state["step"] not in ("check_offer", "checking") or (state["step"] == "checking" and state.get("pending")):
            raise BridgeError("STEP_INVALID", "现在还不能开始最终检验。")
        if course.plan["state"] == "active":
            try:
                course.store.start_checking(course.plan_id)
            except StoreError:
                raise BridgeError("CHECK_NOT_READY", "还有模块没通过，全部模块通过后才能开始最终检验。") from None
            course.plan = course.store.get_plan(course.plan_id)
        state.update(phase="check", step="checking", pending=None, check=None, check_used=0,
                     check_asked=[], check_split=state.get('check_split', []))
        self._say(course, state, "开始最终检验。老师不再重讲，只针对每个点换角度提问；答错会优先再问，"
                                 "答对的点隔几题再确认。有不懂的可以随时问。")
        self._next_check(course, state, ctx)

    def _next_check(self, course, state, ctx):
        """One explicit round has at most CHECK_BATCH questions, including split probes."""
        check = state.get("check")
        if check and check["index"] < len(check["queue"]):
            return self._ask_check(course, state)
        book = course.book()
        if book.check_complete():
            return self._complete(course, state, ctx)
        if state.get('check_used', 0) >= CHECK_BATCH:
            state['check'] = None
            state['pending'] = None
            return self._say(course, state, '本轮检验已结束，可点“继续检验”开始下一轮。')
        items = book.next_check_items(n=24, min_gap=3)
        if not items:
            return self._complete(course, state, ctx)
        split = state.get('check_split') or []
        group = split[0]['point_ids'] if split else [pid for pid, _ in items
            if course.point(pid)['module_id'] == course.point(items[0][0])['module_id']][:CHECK_BATCH - state.get('check_used', 0)]
        if split and len(group) > CHECK_BATCH - state.get('check_used', 0):
            state['check_used'] = CHECK_BATCH
            state['check'] = None
            state['pending'] = None
            return self._say(course, state, '综合题会拆开问；本轮题数已满，点“继续检验”再问。')
        module_id = course.point(group[0])['module_id']
        used = {pid: set(book.point(pid).used_angles) for pid in group}
        if any(len(angles) >= len(ANGLES) for angles in used.values()):
            used = None
        unstable = [pid for pid in group if book.point(pid).module_errors or book.point(pid).state in ('red', 'weak')]
        questions = self._bank_select(course, ctx, module_id, group, unstable,
            limit=min(CHECK_BATCH - state.get('check_used', 0), len(group)),
            smaller_than=split[0]['size'] if split else None,
            single_once=state.get('check_single', []), asked=state.get('check_asked', []), used_angles=used)
        if split:
            state['check_split'] = split[1:]
            state['check_single'] = list(set(state.get('check_single', [])) |
                {q['point_ids'][0] for q in questions if len(q['point_ids']) == 1})
        state["check"] = {"queue": questions, "index": 0, "module_id": module_id}
        state["stems"] = (state.get("stems", []) + [q['stem'] for q in questions])[-200:]
        self._attribute_later(course)
        self._ask_check(course, state)

    def _ask_check(self, course, state):
        check = state["check"]
        q = check["queue"][check["index"]]
        if q['type'] == 'choice' and not q.get('shuffled'):
            self._rng.shuffle(q['options']); q['shuffled'] = True
        state["module_id"] = check["module_id"]  # the teacher's context for questions asked meanwhile
        state["pending"] = {"kind": "check", "answer_id": uuid.uuid4().hex,
                            "point_ids": q["point_ids"], "question": q}
        levels = course.book()
        passed = sum(levels.point(p["id"]).level == 3 for p in course.points)
        self._say(course, state, f"检验（已通过 {passed}/{len(course.points)} 个点）：" + _question_text(q), kind="question")

    def _check_reply(self, course, state, ctx, text, pending):
        q = pending["question"]
        prior = self._prior_verdict(course, state, pending)
        clicked = q["type"] == "choice" and text.strip() in q["options"]
        if prior is not None:
            verdict = prior
        elif clicked and (text.strip() == q["answer"] or len(q["point_ids"]) == 1):
            correct = text.strip() == q["answer"]
            verdict = judge_agent.Verdict("answer", correct, () if correct else tuple(q["point_ids"]),
                                          "选对了。" if correct else f"正确答案是：{q['answer']}。")
        else:
            verdict = self._judge(course, state, ctx, "casual", pending, text)  # a question is allowed here
        if verdict.kind == 'clarify' and not pending.get('clarified'):
            pending['clarified'] = True
            return self._say(course, state, verdict.reason)
        if verdict.kind == "question":
            return self._teacher(course, state, ctx, "最终检验中，学习者先问了一个问题。简短解答，但不要说出当前这道检验题的答案，"
                                 "也不要重讲整个知识点；答完提醒他回答上面的题（不要出新题）。", q["point_ids"], user=text)
        if verdict.kind != "answer":
            message = {"change_rules": "检验规则和进度由系统决定，对话里改不了。请直接回答上面的题；不会可以说“不会”。",
                       "claim_known": "检验要靠作答来确认。请直接回答上面的题。"}.get(
                verdict.kind, "没看出这是对这道题的作答。请直接回答；不会的话说“不会”也可以。")
            return self._say(course, state, message)
        if not verdict.correct and len(q['point_ids']) > 1:
            state.setdefault('check_split', []).append({'point_ids': q['point_ids'], 'size': len(q['point_ids'])})
        else:
            self._record(course, state, "check", pending, verdict, text, angle=q.get("angle"))
        if verdict.correct and q.get('id'):
            course.store.mark_bank_correct(course.plan_id, q['id'])
        explanation = "" if verdict.correct else ("综合题未通过，暂不归因到任何知识点；接下来拆开问。"
                                                 if len(q['point_ids']) > 1 else verdict.reason)
        if q["type"] != "choice" and not verdict.correct and len(q['point_ids']) == 1:
            explanation += f" 参考答案：{q['answer']}"
        self._say(course, state, (("✓ 答对了。" if verdict.correct else "✗ 这题没答对。") + explanation).strip(),
                  kind="verdict")
        state["pending"] = None
        state['check_used'] = state.get('check_used', 0) + 1
        state.setdefault('check_asked', []).append(q['id'])
        state["check"]["index"] += 1
        self._next_check(course, state, ctx)

    def _complete(self, course, state, ctx):
        try:
            course.store.complete_plan(course.plan_id, today=self._day(state))
        except StoreError as exc:
            if exc.code != 'PLAN_NOT_COMPLETE': raise
            self._say(course, state, '还需要通过全部知识点的最终检验，才能完成计划。')
            return
        course.plan = course.store.get_plan(course.plan_id)
        state.update(step="completed", pending=None, check=None)
        self._say(course, state, "最终检验全部通过，这份计划完成了！可以在「我的计划」里查看学习报告。")

    def start_practice(self, course, state, ctx, *, module_id=None, point_id=None, count=5):
        """Free check writes practice events only; restore the interrupted room afterwards."""
        if type(count) is not int or not 1 <= count <= 50:
            raise BridgeError('PARAM_INVALID', '自由检验题数须为 1–50。')
        if state.get('practice'):
            raise BridgeError('STEP_INVALID', '已有一轮自由检验正在进行。')
        point_lookup = {p['id']: p for p in course.points}
        module_ids = {m['id'] for m in course.modules}
        if point_id is not None and point_id not in point_lookup:
            raise BridgeError('PARAM_INVALID', '知识点不存在。')
        if module_id is not None and module_id not in module_ids:
            raise BridgeError('PARAM_INVALID', '模块不存在。')
        if point_id and module_id and point_lookup[point_id]['module_id'] != module_id:
            raise BridgeError('PARAM_INVALID', '知识点不属于所选模块。')
        book = course.book()
        learned = {m['id'] for m in course.modules if course.plan['state'] == 'completed' or
                   book.module(m['id']).state != 'untested' or
                   any(p['id'] in course.taught() for p in course.points_of(m['id']))}
        if point_id:
            selected = [point_lookup[point_id]]
        else:
            selected = [p for p in course.points if module_id is None or p['module_id'] == module_id]
        selected = [p for p in selected if p['module_id'] in learned]
        if not selected:
            raise BridgeError('STEP_INVALID', '所选范围还没有学过的模块。')
        from ..course.question_bank import review_points
        review = review_points(course.store.records('practice_results', course.plan_id))
        self._rng.shuffle(selected)
        selected.sort(key=lambda p: p['id'] not in review)
        state['practice'] = {'resume': copy.deepcopy({k: v for k, v in state.items() if k != 'practice'}),
                             'targets': [p['id'] for p in selected], 'queue': [], 'index': 0, 'count': count}
        state.update(step='practicing', phase='practice', pending=None)
        self._say(course, state, f'开始自由检验，共 {count} 题；结果只记练习，不改计划和报告。')
        self._ask_practice(course, state, ctx)

    def _ask_practice(self, course, state, ctx):
        practice = state['practice']
        if practice['index'] == len(practice['queue']):
            from ..course.question_bank import review_points
            review = review_points(course.store.records('practice_results', course.plan_id))
            used = {q['id'] for q in practice['queue']}
            def available():
                return [q for module in course.modules for q in self._bank_rows(course, module['id'])
                        if len(q['point_ids']) == 1 and q['point_ids'][0] in practice['targets']
                        and not q['correct_seen'] and q['id'] not in used]
            pool = available()
            if not pool:
                point = course.point(practice['targets'][practice['index'] % len(practice['targets'])])
                self._bank_fill(course, ctx, point['module_id'])
                pool = available()
            if not pool:
                raise BridgeError('QUESTION_BANK_INCOMPLETE', '可用练习题暂时不足。')
            ranked = [q for q in pool if q['point_ids'][0] in review] or pool
            practice['queue'].append(dict(self._rng.choice(ranked)))
        q = practice['queue'][practice['index']]
        if q['type'] == 'choice' and not q.get('shuffled'):
            self._rng.shuffle(q['options']); q['shuffled'] = True
        state['module_id'] = q['module_id']
        state['pending'] = {'kind': 'practice', 'answer_id': uuid.uuid4().hex,
                            'point_ids': q['point_ids'], 'question': q,
                            'number': practice['index'] + 1, 'total': len(practice['queue'])}
        self._say(course, state, f"自由检验第 {practice['index'] + 1}/{practice['count']} 题：" + _question_text(q), kind='question')

    def _practice_reply(self, course, state, ctx, text, pending):
        q = pending['question']
        prior = self._prior_verdict(course, state, pending)
        if prior is not None:
            verdict = prior
        elif q['type'] == 'choice' and text.strip() in q['options']:
            correct = text.strip() == q['answer']
            verdict = judge_agent.Verdict('answer', correct, () if correct else tuple(q['point_ids']),
                '选对了。' if correct else f"正确答案是：{q['answer']}。")
        else:
            verdict = self._judge(course, state, ctx, 'test', pending, text)
        if verdict.kind == 'clarify' and not pending.get('clarified'):
            pending['clarified'] = True
            return self._say(course, state, verdict.reason)
        if verdict.kind != 'answer':
            return self._say(course, state, '请回答当前这道题。')
        if prior is None:
            course.store.append_practice_result(course.plan_id, q['id'], q['module_id'], q['point_ids'],
                verdict.correct, reason=verdict.reason, at=self.now(), result_id=self._answer_id(course, state, pending))
        if verdict.correct:
            course.store.mark_bank_correct(course.plan_id, q['id'])
        self._say(course, state, ('✓ 答对了。' if verdict.correct else '✗ 需要复习。') +
                  ('' if verdict.correct else verdict.reason), kind='verdict')
        practice = state['practice']
        practice['index'] += 1
        if practice['index'] < practice['count']:
            return self._ask_practice(course, state, ctx)
        resume = practice['resume']
        state.clear(); state.update(resume)
        self._say(course, state, '自由检验完成；练习记录已保存，原计划进度未变。')

    def _attribute_later(self, course):
        """Group what the learner got wrong into a few problem kinds for the teacher (CHK-01), in the background."""
        plan_id = course.plan_id
        key = plan_id + ":check"
        with self._guard:
            if key in self._profiling:
                return
            self._profiling.add(key)

        def work():
            try:
                store = self.services.store
                fresh = _Course(store, store.get_plan(plan_id))
                contexts = {"casual": "casual", "quick_confirm": "casual", "module_test": "module_test",
                            "skip_test": "module_test", "retest": "retest", "check": "check"}
                records = []
                for row in sorted(store.records("judgements", plan_id), key=lambda r: r["event"]["seq"]):
                    wrong = [pid for pid, correct in row["event"]["results"] if not correct]
                    details = row.get("details") or {}
                    if wrong and details.get("question") and details.get("reason"):
                        records.append({"question": details["question"][:4000], "answer": (details.get("answer") or "")[:4000],
                                        "reason": details["reason"][:800], "context": contexts[row["event"]["context"]],
                                        "point_ids": wrong})
                records = records[-200:]
                involved = list(dict.fromkeys(pid for r in records for pid in r["point_ids"]))[:100]
                records = [dict(r, point_ids=[p for p in r["point_ids"] if p in involved]) for r in records]
                records = [r for r in records if r["point_ids"]]
                if not records:
                    return
                cctx = {"points": [{"id": pid, "title": fresh.point(pid)["title"], "summary": fresh.point(pid).get("summary", "")}
                                   for pid in involved], "records": records}
                request = check_agent.check_request(cctx)
                result = self._ask(fresh, None, "check_profile", request, lambda text: check_agent.parse_check(text, cctx), 4096)
                document = dict(self._profile_document(fresh), check=result.text, check_at=self.now().isoformat())
                store.save_teacher_profile(plan_id, document)
            except Exception:  # noqa: BLE001 - attribution is advisory and never blocks the check
                log.info("check attribution failed", exc_info=True)
            finally:
                with self._guard:
                    self._profiling.discard(key)

        threading.Thread(target=work, name="classroom-check-profile", daemon=True).start()

    @staticmethod
    def _profile_document(course):
        return dict((course.store.records("teacher_profile", course.plan_id) or [{}])[0].get("document", {}) or {})

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
        for m in course.store.messages(course.plan_id, self._channel(state)):
            role = "user" if m["role"] == "user" else "assistant"
            text = m["content"] if m["role"] != "system" else f"（系统）{m['content']}"
            rows.append({"role": role, "text": text})
        return dialogue_tail(rows, max_rows=DIALOGUE_ROWS, max_chars=DIALOGUE_CHARS)

    def _focus(self, course, state):
        points = course.points_of(state["module_id"])
        return [points[min(state["index"], len(points) - 1)]["id"]]

    @staticmethod
    def _prep_context(course, point):
        module = course.module(point["module_id"])
        # Same module source first for every point: shared provider cache prefix.
        excerpts = course.excerpts([p["id"] for p in course.points_of(module["id"])])
        return {"module": {"id": module["id"], "title": module["title"]},
                "point": {"id": point["id"], "title": point["title"], "summary": point["summary"]},
                "excerpts": excerpts}

    @staticmethod
    def _prep_fingerprint(course, point):
        context = Classroom._prep_context(course, point)
        return lesson_prep.fingerprint(course.plan, course.module(point["module_id"]), point, context["excerpts"])

    def _prepared_point(self, course, point):
        row = course.store.load_lesson_cache(course.plan_id)["lesson_preps"].get(point["id"])
        if not isinstance(row, dict) or row.get("fingerprint") != self._prep_fingerprint(course, point):
            return None
        if row.get("status") != "reviewed" or row.get("version") != lesson_prep.VERSION:
            return None
        try:
            prepared = lesson_prep.parse_prep(json.dumps(row["lesson"], ensure_ascii=False))
            return None if lesson_prep.check_lesson(prepared) else prepared
        except (AgentFormatError, KeyError, TypeError):
            return None

    @staticmethod
    def _test_fingerprint(course, module_id):
        points = course.points_of(module_id)
        return lesson_prep.fingerprint(course.plan, course.module(module_id),
                                       {"points": points}, course.excerpts([p["id"] for p in points]))

    def _prep_targets(self, course, state):
        """At most today's remaining minutes plus the next calendar day's target."""
        from datetime import date, timedelta
        day = date.fromisoformat(state["day"])
        targets = course.store.load_ledger(course.plan_id).targets
        today = self._today(course, state)
        remaining = max(0, targets.get(day, 0) - today["today_confirmed"] - today["today_tentative"])
        budget = remaining + targets.get(day + timedelta(days=1), 0)
        if budget <= 0:
            return []
        module_points = course.points_of(state["module_id"])
        current = module_points[min(state["index"], len(module_points) - 1)]
        start = next(i for i, point in enumerate(course.points) if point["id"] == current["id"])
        chosen, minutes = [], 0
        for point in course.points[start:]:
            chosen.append(point)
            minutes += point["minutes"]
            if minutes >= budget:
                break
        return chosen

    def _prepare_later(self, course, state):
        if state.get("step") in ("completed", "checking"):
            return
        plan_id = course.plan_id
        with self._guard:
            if plan_id in self._preparing:
                return
            self._preparing.add(plan_id)
        try:
            targets = self._prep_targets(course, state)
        except Exception:  # noqa: BLE001 - bad ledger never delays entry
            with self._guard:
                self._preparing.discard(plan_id)
            log.info("lesson prep targets unavailable", exc_info=True)
            return

        def work():
            try:
                fresh = _Course(self.services.store, self.services.store.get_plan(plan_id))
                keep = {p["id"] for p in targets}
                self.services.store.prune_lesson_cache(
                    plan_id, point_ids=keep, module_ids={p["module_id"] for p in targets})
                # Start the module test while the learner is still working
                # through its points; long-form lesson reviews may take longer.
                for module_id in {p["module_id"] for p in targets}:
                    try:
                        points = fresh.points_of(module_id)
                        if points[-1]["id"] not in keep or self._bank_rows(fresh, module_id):
                            continue
                        self._bank_fill(fresh, None, module_id, background=True)
                    except Exception:  # noqa: BLE001 - a failed pretest must not discard lessons
                        log.info("module question preparation failed", exc_info=True)
                index = 0
                while index < len(targets):
                    point = targets[index]
                    group = [point]
                    if (index + 1 < len(targets) and targets[index + 1]["module_id"] == point["module_id"]):
                        group.append(targets[index + 1])
                    index += len(group)
                    try:
                        if len(group) == 1:
                            self._prepare_point(fresh, point, keep)
                        else:
                            self._prepare_batch(fresh, group, keep)
                    except Exception:  # noqa: BLE001 - another point can still be prepared
                        log.info("lesson preparation failed for %s", [p["id"] for p in group], exc_info=True)
                        for item in group:
                            if self._prepared_point(fresh, fresh.point(item["id"])):
                                continue
                            self.services.store.save_lesson_cache_item(plan_id, "lesson_preps", item["id"],
                                {"version": lesson_prep.VERSION, "status": "needs_live_teaching",
                                 "fingerprint": self._prep_fingerprint(fresh, item)}, keep_ids=keep)
            except Exception:  # noqa: BLE001 - preparation is optional
                log.info("background lesson prep failed", exc_info=True)
            finally:
                with self._guard:
                    self._preparing.discard(plan_id)

        threading.Thread(target=work, name="classroom-lesson-prep", daemon=True).start()

    def _prep_ask(self, course, purpose, request, parse, max_tokens):
        # Foreground classroom operations take priority. A request already in
        # flight cannot be interrupted, but no next prep starts while one runs.
        while self._lock(course.plan_id).locked():
            time.sleep(0.1)
        if purpose == "questions":
            return self._ask(course, None, purpose, request, parse, max_tokens)
        # A truncated preparation is a live-teaching fallback, not another
        # whole generation charged to the learner.
        result = self.services.gateway.complete(purpose, request[1], system=request[0],
                                                plan_id=course.plan_id, max_tokens=max_tokens)
        return parse(result.text)

    def _save_prepared(self, course, point, approved, keep):
        fresh = _Course(self.services.store, self.services.store.get_plan(course.plan_id))
        fingerprint = self._prep_fingerprint(course, point)
        if self._prep_fingerprint(fresh, fresh.point(point["id"])) != fingerprint:
            return
        if approved and not lesson_prep.check_lesson(approved):
            value = {"version": lesson_prep.VERSION, "status": "reviewed",
                     "fingerprint": fingerprint, "lesson": asdict(approved)}
        else:
            value = {"version": lesson_prep.VERSION, "status": "needs_live_teaching",
                     "fingerprint": fingerprint}
        self.services.store.save_lesson_cache_item(course.plan_id, "lesson_preps", point["id"], value,
                                                   keep_ids=keep)

    def _prepare_batch(self, course, points, keep):
        pending = [course.point(p["id"]) for p in points if not self._prepared_point(course, course.point(p["id"]))]
        if not pending:
            return
        if len(pending) == 1:
            self._prepare_point(course, pending[0], keep)
            return
        contexts = [self._prep_context(course, p) for p in pending]
        point_ids = tuple(p["id"] for p in pending)
        request = lesson_prep.batch_prep_request(contexts)
        drafts = self._prep_ask(course, "lesson_prep", request,
                                lambda raw: lesson_prep.parse_batch_preps(raw, point_ids), 16384)
        review_request = lesson_prep.batch_review_request(contexts, drafts)
        reviews = self._prep_ask(course, "lesson_review", review_request,
                                 lambda raw: lesson_prep.parse_batch_reviews(raw, point_ids), 16384)
        for point, context, draft, review in zip(pending, contexts, drafts, reviews):
            approved = draft if review.verdict == "approve" else review.revised
            if review.verdict == "revise" and approved is not None:
                second_request = lesson_prep.review_request(context, approved)
                second = self._prep_ask(course, "lesson_review", second_request, lesson_prep.parse_review, 16384)
                if second.verdict != "approve":
                    approved = None
            self._save_prepared(course, point, approved, keep)

    def _prepare_point(self, course, point, keep):
        current = course.point(point["id"])
        if self._prepared_point(course, current):
            return
        context = self._prep_context(course, current)
        request = lesson_prep.prep_request(context)
        draft = self._prep_ask(course, "lesson_prep", request, lesson_prep.parse_prep, 16384)
        review_request = lesson_prep.review_request(context, draft)
        review = self._prep_ask(course, "lesson_review", review_request, lesson_prep.parse_review, 16384)
        approved = draft if review.verdict == "approve" else review.revised
        if review.verdict == "revise" and approved is not None:
            second_request = lesson_prep.review_request(context, approved)
            second = self._prep_ask(course, "lesson_review", second_request, lesson_prep.parse_review, 16384)
            if second.verdict != "approve":
                approved = None
        # Re-read source before saving; a concurrent plan/material edit must
        # never make an old draft appear current.
        self._save_prepared(course, current, approved, keep)

    def _teacher(self, course, state, ctx, director, point_ids, *, user="", asks=False, prepared=None, short=False):
        """asks=False: a question the step did not ask for stays part of the explanation, not a card."""
        explicit_prepared = prepared is not None
        if prepared is None and len(point_ids) == 1:
            prepared = self._prepared_point(course, course.point(point_ids[0]))
        if short:
            director += " 这一步只现场点评学习者刚才的回答，最多两句中文，不重讲知识点、不写完整例子。"
        plan, module = self._skeleton(course, state, point_ids)
        profile = self._profile_document(course)
        notes = (profile.get("text") or "") + (f"\n检验中的问题归纳：{profile['check']}" if profile.get("check") else "")
        tctx = {"prefs": self._prefs(), "plan": plan, "module": module, "excerpts": course.excerpts(point_ids),
                "dialogue": self._dialogue(course, state)[:-1] if user else self._dialogue(course, state),
                "profile": notes[:6000], "director": director, "user": user}
        if prepared is not None:
            tctx["prepared_lesson"] = asdict(prepared)
        request = teacher_agent.teacher_request(tctx)
        mapping = teacher_agent.citation_map(tctx)
        stream = ctx.stream if ctx else (lambda _d: None)
        teaching, raw = None, ""
        # The provider counts hidden reasoning against this cap. A 1024-token
        # cap truncated a two-sentence correction and charged for a retry.
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
                if not asks and teaching.speech.rstrip().endswith(("？", "?")):
                    raise AgentFormatError("老师在未要求提问的步骤结尾新增了问题")
                break
            except AgentFormatError as exc:
                log.info("teacher answer rejected (%s), attempt %s", exc, attempt + 1)
        if teaching is None:
            raise ModelError("AGENT_FORMAT", "老师这次的回答格式不对，已重试仍不行。")
        removed = teacher_agent.citation_diagnostics(raw, tctx)
        if removed:
            log.info("teacher cited unknown excerpts %s", removed)
        if explicit_prepared and asks:
            teaching = teacher_agent.Teaching(teaching.speech, prepared.casual_question, teaching.cited_chunks)
        speech = _link_citations(teaching.speech, mapping)
        if teaching.casual_question and not asks:
            speech = (speech + "\n\n" + teaching.casual_question).strip()
            teaching = teacher_agent.Teaching(teaching.speech, None, teaching.cited_chunks)
        if speech:
            course.store.append_message(course.plan_id, self._channel(state), "assistant", speech, kind="teach")
        if teaching.casual_question:
            course.store.append_message(course.plan_id, self._channel(state), "assistant", teaching.casual_question, kind="casual")
        return teaching

    def _judge(self, course, state, ctx, mode, pending, reply):
        q = pending["question"]
        points = [course.point(pid) for pid in pending["point_ids"]]
        jctx = {"mode": mode, "points": [{"id": p["id"], "title": p["title"], "summary": p.get("summary", "")} for p in points],
                "excerpts": course.excerpts(pending["point_ids"]),
                "question": {"stem": q["stem"], "type": q["type"], "options": q.get("options", [])},
                "reference": q.get("reference") or q.get("answer") or "按知识点要点判断",
                "reply": reply, "require_evidence": q["type"] in ("short", "fix_error")}
        request = judge_agent.judge_request(jctx, reply)
        parse = lambda text: judge_agent.parse_judgement(text, jctx)
        first = self._ask(course, ctx, "judge", request, parse, 2048)
        if q["type"] not in ("short", "fix_error") or (not first.uncertain and first.kind != "clarify"):
            return first
        second = self._ask(course, ctx, "judge", request, parse, 2048)
        if (second.uncertain or first.kind != second.kind
                or first.correct != second.correct or first.wrong_points != second.wrong_points):
            return judge_agent.Verdict("clarify", None, (),
                "请只针对题目要求补充说明你刚才的答案。")
        return second

    def _questions(self, course, ctx, purpose, point_ids, *, avoid=(), used_angles=None, background=False):
        points = [course.point(pid) for pid in point_ids]
        excerpts = course.excerpts(point_ids)
        qctx = {"purpose": purpose, "per_point": 2 if purpose == 'bank' else 1,
                "excerpts": excerpts, "avoid": list(avoid)[-100:],
                "points": [{"id": p["id"], "title": p["title"], "summary": p.get("summary", ""),
                            "chunk_ids": [c for c in p["chunk_ids"] if c in excerpts] or p["chunk_ids"][:1]} for p in points]}
        if purpose == "check":
            qctx["used_angles"] = used_angles or {pid: [] for pid in point_ids}
        request = question_agent.question_request(qctx)
        parse = lambda text: question_agent.parse_questions(text, qctx)
        return (self._prep_ask(course, "questions", request, parse, 8192) if background else
                self._ask(course, ctx, "questions", request, parse, 8192))

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
    def _answer_id(self, course, state, pending):
        if pending.get('answer_id'):
            return pending['answer_id']
        # Stable for classroom files created before answer IDs were introduced.
        legacy = json.dumps([course.plan_id, state.get('module_id'), state.get('index'),
                             (state.get('test') or {}).get('attempt'), pending], sort_keys=True, ensure_ascii=False)
        return uuid.uuid5(uuid.NAMESPACE_URL, legacy).hex

    def _prior_verdict(self, course, state, pending):
        answer_id = self._answer_id(course, state, pending)
        entity = 'practice_results' if pending.get('kind') == 'practice' else 'judgements'
        row = next((item for item in course.store.records(entity, course.plan_id)
                    if item['id'] == answer_id), None)
        if row is None: return None
        if entity == 'practice_results':
            return judge_agent.Verdict('answer', row['correct'],
                                       () if row['correct'] else tuple(row['point_ids']), row['reason'])
        event = row['event']
        return judge_agent.Verdict('answer', all(result[1] for result in event['results']),
                                   tuple(result[0] for result in event['results'] if not result[1]),
                                   row['details'].get('reason', ''), checks=tuple(row['details'].get('point_checks', ())))

    def _record(self, course, state, context, pending, verdict, answer, *, angle=None):
        if self._prior_verdict(course, state, pending) is not None:
            return
        wrong = set(verdict.wrong_points)
        results = tuple((pid, pid not in wrong) for pid in pending["point_ids"])
        attempt = (state.get("test") or {}).get("attempt") if context in ("module_test", "skip_test") else None
        module_id = course.point(pending["point_ids"][0])["module_id"]
        event = Judgement(self._answer_id(course, state, pending), course.next_seq(), self.now(),
                          context, module_id, attempt, results, angle)
        q = pending["question"]
        course.store.append_judgement(course.plan_id, event, details={
            "question": q["stem"], "answer": answer[:4000], "reason": verdict.reason,
            "point_checks": list(verdict.checks), "chunk_ids": q.get("chunk_ids", [])},
            credit_day=self._day(state) if context == "check" else None)

    def _credit_learning(self, course, state, point):
        """A taught point counts as tentative study time until its module test (PLAN-05)."""
        try:
            course.store.add_credit(course.plan_id, f"learn:{point['id']}", point["id"], point["minutes"],
                                    self._day(state), "tentative")
        except Exception:  # noqa: BLE001 - an existing credit (taught again after a restart) is fine
            log.info("learning credit for %s not added", point["id"], exc_info=True)

    def _say(self, course, state, text, *, kind="text", role="system"):
        course.store.append_message(course.plan_id, self._channel(state), role, text, kind=kind)

    # ------------------------------------------------------------------ profile (background)
    def _profile_later(self, course, state, mode):
        plan_id = course.plan_id
        with self._guard:
            self._profile_jobs.setdefault(plan_id, []).append((state["module_id"], mode))
            if plan_id in self._profiling:
                return
            self._profiling.add(plan_id)

        def work():
            while True:
                with self._guard:
                    queue = self._profile_jobs.get(plan_id, [])
                    if not queue:
                        self._profiling.discard(plan_id)
                        self._profile_jobs.pop(plan_id, None)
                        return
                    module_id, current_mode = queue.pop(0)
                try:
                    fresh = _Course(self.services.store, self.services.store.get_plan(plan_id))
                    book = fresh.book()
                    all_points = [p["id"] for p in fresh.points]
                    weak = [p for p in all_points if book.point(p).casual_errors or book.point(p).module_errors
                            or book.point(p).state == "red"][-12:]
                    plan, module = self._skeleton(fresh, {"module_id": module_id}, [])
                    profile = (fresh.store.records("teacher_profile", plan_id) or [{}])[0].get("document", {}) or {}
                    taught = len(fresh.taught())
                    pctx = {"prefs": self._prefs(), "plan": plan, "module": module, "mode": current_mode,
                            "dialogue": self._dialogue(fresh, {"module_id": module_id}),
                            "profile": (profile.get("text") or "")[:6000], "known_point_ids": all_points,
                            "weak_point_ids": weak, "progress": f"已讲 {taught}/{len(all_points)} 个知识点，当前模块《{module['title']}》。"}
                    request = profile_agent.profile_request(pctx)
                    result = self._ask(fresh, None, "profile", request, lambda text: profile_agent.parse_profile(text, pctx), 2048)
                    fresh.store.save_teacher_profile(plan_id, dict(self._profile_document(fresh), text=result.text,
                                                                   at=self.now().isoformat()))
                except Exception:  # noqa: BLE001 - notes are best effort and never block the lesson
                    log.info("teacher profile update failed", exc_info=True)

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
            "reference": q.reference, "point_ids": list(q.point_ids), "chunk_ids": list(q.chunk_ids), "angle": q.angle}


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


def register(bridge, services, study_day, *, presence=lambda active, plan_id=None: None):
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
        presence(True, params.get("plan_id"))
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
        "classroom.check": lambda p, c: room().run(p.get("plan_id"), room().start_check, c),
        "classroom.practice": lambda p, c: room().run(p.get("plan_id"),
            lambda co, st, cx: room().start_practice(co, st, cx, module_id=p.get('module_id'),
                point_id=p.get('point_id'), count=p.get('count', 5)), c),
        "classroom.leave": left,
        "classroom.source": source,
    }
    for name, fn in handlers.items():
        bridge.register(name, fn)
