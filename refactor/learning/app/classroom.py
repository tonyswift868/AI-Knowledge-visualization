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
from contextlib import contextmanager

from ..connections.errors import MODEL_OUTPUT_TRUNCATED, ModelCancelled, ModelError
from ..course import answer_compare, check_agent, judge_agent, lesson_prep, profile_agent, question_agent, question_bank, teacher_agent
from ..course.request_layout import AppendOnlyPage, dialogue_tail
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


class _BankPending(Exception):
    def __init__(self, module_id, points, used_angles=None, needs=None, asked=()):
        self.module_id, self.points = module_id, list(points)
        self.used_angles = {p: sorted(a) for p, a in (used_angles or {}).items()}
        self.needs = needs
        self.asked = list(asked)


def _now():
    return datetime.now(timezone.utc)


class Classroom:
    _lesson_mutex = threading.Lock()
    _lesson_locks = {}
    _lesson_jobs = {}
    _lesson_errors = {}
    _room_locks = {}
    def __init__(self, services, study_day, *, now=_now, rng=None, review_wrong=None):
        self.services, self.study_day, self.now = services, study_day, now
        self.review_wrong = judge_agent.REVIEW_WRONG_DEFAULT if review_wrong is None else bool(review_wrong)
        self._rng = rng or random.Random()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._profiling: set[str] = set()
        self._profile_jobs: dict[str, list[tuple[str, str]]] = {}
        self._preparing: set[str] = set()
        self._prep_timer = None
        self._prep_context_local = threading.local()
        self._prep_rooms = {}
        self._prep_latest = {}
        self._entered_plans: set[str] = set()  # rooms entered in this process; a repeat enter keeps the day
        self._bank_jobs = {}
        self._bank_seen = {}
        self._bank_plans = set()
        self._lesson_wait_jobs = set()
        self.lesson_event = lambda data: None
        self._unsubscribe_bank = self.services.store.subscribe(self._bank_material_changed)

    # ------------------------------------------------------------------ state
    def _file(self, plan_id):
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        return AccountJSON(self.services.account_id, self.services.data_root, f"classroom/{plan_id}.json")

    def _lock(self, plan_id):
        key = (str(self.services.data_root), self.services.account_id, plan_id)
        with self._lesson_mutex:
            return self._room_locks.setdefault(key, threading.Lock())

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
                    self._resume_lesson_wait(course, state)
                    self._resume_bank_wait(course, state, ctx)
                    if state['step'] != 'preparing_questions':
                        state.pop('bank_wait_attempts', None)
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
        # A free-check round keeps the pre-practice room in practice.resume; compare that one,
        # or reopening a completed plan mid-practice would discard the pending question (BUG-30).
        room = (state.get('practice') or {}).get('resume', state)
        if (room.get('step') == 'completed') != (course.plan.get('state') == 'completed'):
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
                "lesson": self._lesson_view(course, state),
                "lesson_wait": self._lesson_wait_view(state),
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
        reenter = course.plan_id not in self._entered_plans or not state.get('in_session')
        with self._guard:
            if course.plan_id not in self._entered_plans or not state.get("in_session") or not state.get("day"):
                state["day"] = self.study_day().isoformat()
            self._entered_plans.add(course.plan_id)
        state["in_session"] = True
        if reenter:
            self._retry_bank(course, state)
        self._prefetch_banks(course, state)
        self._prepare_later(course, state)
        self._schedule_idle_prep(course, state)

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
    def _lesson_view(self, course, state):
        lesson = state.get("lesson")
        if not lesson:
            return None
        draft = lesson_prep.parse_prep(json.dumps(lesson["draft"], ensure_ascii=False), live=lesson.get("live", False))
        pos = lesson["position"]
        step = draft.steps[pos]
        return dict(point_id=lesson["point_id"], point_title=course.point(lesson["point_id"])["title"],
                    step=pos + 1, total=len(draft.steps), awaiting=lesson["awaiting"],
                    options=[o.text for o in step.options] if lesson["awaiting"] == "choice" else [])

    def next(self, course, state, ctx):
        if state["step"] == "preparing_lesson":
            if (state.get("lesson_wait") or {}).get("error"):
                self._teach_point(course, state, ctx, "")
            return
        lesson = state.get("lesson")
        if lesson and state["step"] == "lesson":
            if lesson["awaiting"] != "continue":
                raise BridgeError("STEP_INVALID", "请先选择选项或回到刚才。")
            lesson["position"] += 1
            draft = lesson_prep.parse_prep(json.dumps(lesson["draft"], ensure_ascii=False), live=lesson.get("live", False))
            if lesson["position"] == len(draft.steps):
                lesson["position"] -= 1
                lesson["awaiting"] = None
                return self._casual_card(course, state, draft.questions[0], asks=1)
            return self._show_step(course, state)
        if state["step"] != "teach":
            raise BridgeError("STEP_INVALID", "现在不是讲新内容的时候。")
        self._teach_point(course, state, ctx, "")

    def choose(self, course, state, ctx, option):
        lesson = state.get("lesson")
        if not lesson or state["step"] != "lesson" or lesson["awaiting"] != "choice":
            raise BridgeError("STEP_INVALID", "现在没有待选的引导问题。")
        draft = lesson_prep.parse_prep(json.dumps(lesson["draft"], ensure_ascii=False), live=lesson.get("live", False))
        options = draft.steps[lesson["position"]].options
        if type(option) is not int or not 0 <= option < len(options):
            raise BridgeError("PARAM_INVALID", "选项序号无效。")
        selected = options[option]
        self._append_lesson(course, state, selected.text, kind="choice", role="user")
        self._append_lesson(course, state, selected.response, kind="step_reply", role="assistant")
        lesson["awaiting"] = "continue"

    def back(self, course, state, ctx):
        lesson = state.get("lesson")
        if not lesson or lesson["awaiting"] != "return":
            raise BridgeError("STEP_INVALID", "现在不用回到刚才。")
        lesson["awaiting"] = lesson.pop("return_to", None)

    def _show_step(self, course, state):
        lesson = state["lesson"]
        draft = lesson_prep.parse_prep(json.dumps(lesson["draft"], ensure_ascii=False), live=lesson.get("live", False))
        step = draft.steps[lesson["position"]]
        lesson["awaiting"] = "choice" if step.question else "continue"
        self._append_lesson(course, state, lesson_prep.render_step(step), kind="step", role="assistant")

    def _append_lesson(self, course, state, text, *, kind, role):
        # Instant prewritten messages can share a Windows clock tick. Store
        # sorts by (at, random id), so preserve their causal display order.
        from datetime import timedelta
        previous = course.store.messages(course.plan_id, self._channel(state))
        at = datetime.fromisoformat(previous[-1]["at"]) + timedelta(microseconds=1) if previous else None
        course.store.append_message(course.plan_id, self._channel(state), role, text, kind=kind, at=at)

    def _casual_card(self, course, state, question, *, asks):
        point_id = state["lesson"]["point_id"]
        state["pending"] = dict(kind="casual", answer_id=uuid.uuid4().hex, point_ids=[point_id],
                                asks=asks, question=question.pending(point_id))
        state["step"] = "answer"
        self._append_lesson(course, state, question.stem, kind="casual", role="assistant")

    def _teach_point(self, course, state, ctx, lead):
        point = course.points_of(state["module_id"])[state["index"]]
        prepared = self._prepared_point(course, point)
        if prepared is None:
            row = self._prep_row(course, point)
            if row.pop("delivery_error", None):
                self._persist_prep(course, point, row, {p["id"] for p in course.points})
            with self._lesson_mutex:
                self._lesson_errors.pop((*self._prep_key(course, point), row["fingerprint"]), None)
            state.pop("lesson", None)
            state["pending"] = None
            state["step"] = "preparing_lesson"
            state["lesson_wait"] = dict(point_id=point["id"], fingerprint=self._prep_fingerprint(course, point),
                                        started_at=self.now().isoformat())
            self._request_lesson(course, state)
            return
        self._install_lesson(course, state, point, prepared, live=False)

    def _install_lesson(self, course, state, point, prepared, *, live):
        resume_asks = (state.get("lesson_wait") or {}).get("resume_casual_asks")
        if live:
            row = self._prep_row(course, point)
            row["live_used"] = True
            self._persist_prep(course, point, row, {p["id"] for p in course.points})
        state.pop("lesson_wait", None)
        state["lesson"] = dict(point_id=point["id"], draft=asdict(prepared), position=0,
                               awaiting=None, live=live)
        state["pending"] = None
        state["step"] = "lesson"
        state["resume"] = False
        if resume_asks:
            self._casual_card(course, state, prepared.questions[resume_asks - 1], asks=resume_asks)
        else:
            self._show_step(course, state)
        self._prepare_later(course, state)

    def _lesson_wait_view(self, state):
        wait = state.get("lesson_wait")
        if not wait or state["step"] != "preparing_lesson":
            return None
        elapsed = max(0, (self.now() - datetime.fromisoformat(wait["started_at"])).total_seconds())
        return dict(point_id=wait["point_id"], elapsed_seconds=int(elapsed), error=wait.get("error"))

    def _resume_lesson_wait(self, course, state, *, launch=True):
        wait = state.get("lesson_wait")
        if state["step"] != "preparing_lesson" or not wait:
            return
        point = course.points_of(state["module_id"])[state["index"]]
        if point["id"] != wait["point_id"] or self._prep_fingerprint(course, point) != wait["fingerprint"]:
            state.pop("lesson_wait", None)
            state["step"] = "teach"
            return
        prepared = self._prepared_point(course, point)
        row = self._prep_row(course, point)
        live = False
        if prepared is None and row.get("live_lesson"):
            prepared = lesson_prep.parse_prep(json.dumps(row["live_lesson"], ensure_ascii=False),
                                             chunk_ids=point["chunk_ids"], live=True)
            if lesson_prep.check_lesson(prepared, chunk_ids=point["chunk_ids"]):
                prepared = None
            else:
                live = True
        if prepared is not None:
            self._install_lesson(course, state, point, prepared, live=live)
        else:
            with self._lesson_mutex:
                error = self._lesson_errors.get((*self._prep_key(course, point), wait["fingerprint"]))
            if row.get("delivery_error") or error:
                wait["error"] = row.get("delivery_error") or error
            elif launch and not wait.get("error") and state.get("in_session"):
                self._request_lesson(course, state)

    def _request_lesson(self, course, state):
        """Only enqueue work here. No classroom bridge thread waits for a model or a job."""
        point = course.point(state["lesson_wait"]["point_id"])
        fingerprint = state["lesson_wait"]["fingerprint"]
        token = (course.plan_id, point["id"], fingerprint)
        with self._guard:
            if token in self._lesson_wait_jobs:
                return
            self._lesson_wait_jobs.add(token)

        def work():
            error = None
            try:
                # Join this exact point if a batch owns it. Unrelated batches
                # never stand in front of an urgently needed point.
                while True:
                    with self._prep_claim(course, point) as owned:
                        if owned:
                            fresh = self._course(course.plan_id)
                            point_now = fresh.point(point["id"])
                            if self._prep_fingerprint(fresh, point_now) != fingerprint:
                                break
                            keep = {p["id"] for p in fresh.points}
                            for _ in range(4):
                                self._prepare_point_locked(fresh, point_now, keep)
                                if self._prep_row(fresh, point_now)["status"] in ("reviewed", "needs_live_teaching"):
                                    break
                            if self._prepared_point(fresh, point_now) is None:
                                self._prepare_live(fresh, point_now, keep)
                            break
                        event = self._prep_job_event(course, point)
                    if event is not None:
                        event.wait()  # worker only; no DB/model lock held
            except Exception as exc:
                error = getattr(exc, "message", "这一点暂时没有备好，可以重新准备。")
                log.info("requested lesson preparation failed", exc_info=True)
            finally:
                try:
                    # Completion only invalidates the view, never locks or
                    # writes a frozen room. Failure belongs to this exact
                    # fingerprint; ordinary reads/reopen don't retry it.
                    if error:
                        with self._lesson_mutex:
                            self._lesson_errors[(*self._prep_key(course, point), fingerprint)] = error
                        row = self._prep_row(course, point)
                        if row["fingerprint"] == fingerprint:
                            row["delivery_error"] = error
                            self._persist_prep(course, point, row, {p["id"] for p in course.points})
                except Exception:
                    log.debug("lesson room no longer available", exc_info=True)
                finally:
                    with self._guard:
                        self._lesson_wait_jobs.discard(token)
                self.lesson_event(dict(plan_id=course.plan_id, point_id=point["id"]))
        threading.Thread(target=work, name="classroom-needed-lesson", daemon=True).start()

    def _prepare_live(self, course, point, keep):
        row = self._prep_row(course, point)
        if row.get("live_lesson"):
            return
        request = teacher_agent.live_lesson_request(self._prep_context(course, point))
        for attempt in range(2):
            result = self.services.gateway.complete("teacher", request[1], system=request[0],
                                                    plan_id=course.plan_id, max_tokens=16384)
            try:
                draft = lesson_prep.parse_prep(result.text, chunk_ids=point["chunk_ids"], live=True)
                issues = lesson_prep.check_lesson(draft, chunk_ids=point["chunk_ids"])
                if issues:
                    raise AgentFormatError("；".join(issues))
                row.update(live_lesson=asdict(draft))
                self._persist_prep(course, point, row, keep)
                return
            except AgentFormatError:
                if attempt:
                    raise ModelError("AGENT_FORMAT", "现场讲稿这次未能核实，当前知识点保留，请稍后继续。") from None

    def _point_done(self, course, state):
        points = course.points_of(state["module_id"])
        self._credit_learning(course, state, points[state["index"]])
        state["pending"] = None
        state.pop("lesson", None)
        state["index"] += 1
        state["step"] = "teach" if state["index"] < len(points) else "test_offer"
        self._prepare_later(course, state)

    def _go_on(self, course, state, ctx, lead, point_id, *, commented=False):
        if state["step"] == "test_offer":
            return self._say(course, state, "这个模块的知识点已讲完，可以开始模块检测。")
        day = self._day(state)
        if state.get("target_notice") != day and self._target_met(course, state):
            state["target_notice"] = day
            return self._say(course, state, "今天的学习目标已完成，可以继续下一点，也可以下课休息。")
        return self._teach_point(course, state, ctx, lead)

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
            lesson = state.get("lesson")
            if lesson:
                lesson.setdefault("return_to", lesson["awaiting"])
                lesson["awaiting"] = "return"
            director = ("学习者插话。若是提问就解答；若要求改计划、跳过检验或直接判通过，说明你没有这个权限，"
                        f"判定由系统完成，计划可以在{PLAN_PAGE}调整。答完提醒可以点“继续”接着上课。")
            if state["step"] in ("test_offer", "test_done", "check_offer", "completed"):
                director = director.replace("点“继续”接着上课", "用下面的按钮继续")
            if lesson:
                director = director.replace("点“继续”接着上课", "点“回到刚才”恢复当前步")
                director += " 只回答这次插话，不讲下一步；答完等用户点回到刚才。"
            return self._teacher(course, state, ctx, director, self._focus(course, state), user=text)
        if pending["kind"] in ("casual",):
            return self._casual_reply(course, state, ctx, text, pending)
        if pending['kind'] == 'practice':
            return self._practice_reply(course, state, ctx, text, pending)
        if pending["kind"] == "check":
            return self._check_reply(course, state, ctx, text, pending)
        return self._test_reply(course, state, ctx, text, pending)

    def _casual_reply(self, course, state, ctx, text, pending):
        if (verdict := self._adjudicate(course, state, ctx, "casual", pending, text)) is None: return
        if (verdict := self._information_result(course, state, pending, text, verdict)) is None: return
        if verdict.uncertain or verdict.kind == 'clarify':
            verdict = self._deepen_question(course, state, ctx, pending, verdict)
            if verdict is None: return
        point = course.point(pending["point_ids"][0])
        if verdict.kind == "answer":
            self._record(course, state, "casual", pending, verdict, text)
            result_text = "✓ 答对了。" if verdict.correct else f"✗ 这题没答对。{verdict.reason}"
            self._append_lesson(course, state, result_text, kind="verdict", role="system")
            if ctx is not None:
                ctx.stream(VERDICT_EVENT + json.dumps({"text": result_text, "correct": verdict.correct}, ensure_ascii=False))
            if verdict.correct:
                self._point_done(course, state)
                return self._go_on(course, state, ctx, "", point["id"], commented=True)
            self._teacher(course, state, ctx,
                f"学习者随口问答错（判定：{verdict.reason}）。只用一两句纠正这次错误，不出题，题目由系统提供。",
                [point["id"]], user=text, short=True)
            if pending["asks"] >= CASUAL_ASKS:
                self._point_done(course, state)
                return self._go_on(course, state, ctx, "", point["id"], commented=True)
            lesson = state.get("lesson")
            if not lesson:
                # A pending v3 question can survive migration; do not retain
                # its old reference when supplying the second new question.
                self._teach_point(course, state, ctx, "")
                if state["step"] == "preparing_lesson":
                    state["lesson_wait"]["resume_casual_asks"] = 2
                    return
                lesson = state["lesson"]
            draft = lesson_prep.parse_prep(json.dumps(lesson["draft"], ensure_ascii=False), live=lesson.get("live", False))
            lesson["awaiting"] = None
            return self._casual_card(course, state, draft.questions[1], asks=2)
        if verdict.kind == "claim_known":
            return self._quick_check(course, state, ctx, pending)
        director = {
            "question": "学习者在回答随口问之前先提了个问题。只能解释题意或回应不涉及答案的内容；如果解答会透露答案或解题提示，说明判定之后再讲。然后重述原题，先请他作答，不出新题。",
            "chat": "学习者在闲聊。用一两句自然回应，再请他回答原题；不能透露答案或直接推导答案的提示，不重讲知识结论，不出新题。",
            "change_rules": (f"学习者要求改变规则或进度。说明你没有这个权限，对错由系统判定，计划可以在{PLAN_PAGE}调整；"
                             "然后把话题带回上面的随口问（不要出新题）。"),
        }.get(verdict.kind, "没看出学习者是在回答随口问。请他直接回答上面的随口问，说“不会”也可以（不要出新题）。")
        # An unjudged interruption never replaces the question/reference or
        # answer identity. The shared teacher boundary also covers check Q&A.
        lesson = state.get("lesson")
        if lesson:
            lesson.setdefault("return_to", lesson["awaiting"])
            lesson["awaiting"] = "return"
        return self._teacher(course, state, ctx, director, pending["point_ids"], user=text)

    def _quick_check(self, course, state, ctx, pending):
        """'I know this': one quick confirmation question; wrong is recorded, never argued (TEACH-02)."""
        if (state.get('pending') or {}).get('kind') == 'quick':
            return self._show_probe(course, state)
        q, reason = self._probe_question(course, ctx, pending['point_ids'][0])
        if q is None:
            self._probe_unconfirmed(course, state, pending, '快速确认', reason)
            point = course.point(pending['point_ids'][0])
            self._point_done(course, state)
            return self._go_on(course, state, ctx, '快速确认没有合格题，未确认；继续往下讲，不算答错。', point['id'])
        state["pending"] = {"kind": "quick", "answer_id": uuid.uuid4().hex,
                            "point_ids": list(q['point_ids']), "question": q}
        self._say(course, state, "好，那来一道快速确认题，答对就直接往下讲。")
        self._show_probe(course, state)

    # ------------------------------------------------------------------ tests
    def start_test(self, course, state, ctx, kind):
        if self._retry_bank(course, state):
            return
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
        self._prefetch_banks(course, state, testing=True)
        unstable = [pid for pid in targets if kind == 'retest' or book.point(pid).module_errors or
                    book.point(pid).casual_errors or book.point(pid).state in ('weak', 'red')]
        try:
            asked, angles = self._bank_history(course, module_id) if kind == 'retest' else ((), None)
            questions = self._bank_select(course, ctx, module_id, targets, unstable,
                                          asked=asked, used_angles=angles, defer=True)
        except _BankPending as pending:
            return self._defer_bank(course, state, pending, 'start_test', [kind])
        self._say(course, state, {"skip_test": "好，直接考这个模块。正确率达到 90% 就跳过，不到就正常学。",
                                  "module_test": "开始模块检测。一题一题来，选择题可以直接点选。",
                                  "retest": "开始重测没过的点，每个点换个角度问，答对一次就算过。"}[kind], role="system")
        attempt = uuid.uuid4().hex[:16]
        state["test"] = {"kind": kind, "attempt": attempt, "queue": questions, "index": 0,
                         "targets": targets, "split_single": [], "max_questions": max(len(questions), 4 * len(targets))}
        state["stems"] = (state.get("stems", []) + [q["stem"] for q in questions])[-200:]
        state["step"] = "retesting" if kind == "retest" else "testing"
        self._ask_next(course, state)

    def _ask_next(self, course, state):
        test = state["test"]
        q = test["queue"][test["index"]]
        course.store.mark_bank_asked(course.plan_id, q.get('id'), test['kind'])
        if q['type'] == 'choice' and not q.get('shuffled'):
            self._rng.shuffle(q['options']); q['shuffled'] = True
        state["pending"] = {"kind": test["kind"], "answer_id": uuid.uuid4().hex,
                            "point_ids": q["point_ids"], "question": q,
                            "number": test["index"] + 1, "total": len(test["queue"]),
                            'depth_probe': q.get('depth_probe', False)}
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
            verdict = self._adjudicate(course, state, ctx, "test", pending, text)
        if verdict is None: return
        if (verdict := self._information_result(course, state, pending, text, verdict)) is None: return
        if verdict.uncertain or verdict.kind == 'clarify':
            verdict = self._deepen_question(course, state, ctx, pending, verdict)
            if verdict is None: return
        if verdict.kind != "answer":
            self._say(course, state, "没看出这是对这道题的作答。请直接回答；不会的话说“不会”也可以。", role="system")
            return
        context = {"quick": "quick_confirm", "skip_test": "skip_test", "module_test": "module_test",
                   "retest": "retest"}[pending["kind"]]
        if not verdict.correct and len(q['point_ids']) > 1 and not judge_agent.structured_rubric({'question': q}):
            self._answer_fact(course, state, context, pending, verdict)
            self._split_test_question(course, state, ctx, q)
        else:
            self._record(course, state, context, pending, verdict, text)
            self._answer_fact(course, state, context, pending, verdict)
            for pid in verdict.wrong_points:
                self._refill_wrong(course, state, dict(pending, point_ids=[pid]),
                    judge_agent.Verdict('answer', False, (pid,), verdict.reason))
        if verdict.correct and q.get('id'):
            course.store.mark_bank_correct(course.plan_id, q['id'])
        mark = "✓ 答对了。" if verdict.correct else "✗ 这题没答对。"
        explanation = ("综合题未通过，暂不归因到任何知识点；接下来拆开问。" if len(q['point_ids']) > 1 and not judge_agent.structured_rubric({'question': q})
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
                     smaller_than=None, single_once=(), asked=(), used_angles=None, background=False, defer=False):
        def select():
            rows = self._bank_rows(course, module_id)
            # Standalone confirmations/probes use the existing practice exposure
            # bucket. Even an unanswered/wrong probe must not reappear in a test.
            exposed = set(asked) | {q['id'] for q in rows if 'practice' in q.get('asked_in', ())}
            return question_bank.choose_questions(rows, targets,
                unstable, limit=limit, rng=self._rng, smaller_than=smaller_than, single_once=single_once,
                asked=exposed, used_angles=used_angles)
        chosen, missing = select()
        if missing:
            if defer:
                raise _BankPending(module_id, missing, used_angles, {pid: 1 for pid in missing}, asked)
            self._bank_fill(course, ctx, module_id, point_ids=missing, needs={pid: 1 for pid in missing},
                            used_angles=used_angles, background=background)
            chosen, missing = select()
        if missing:
            raise BridgeError('QUESTION_BANK_INCOMPLETE', '题库暂时不足，稍后再试。')
        return [dict(q) for q in chosen]

    def _bank_rows(self, course, module_id):
        fingerprint = self._test_fingerprint(course, module_id)
        return [q for q in course.store.records('question_bank', course.plan_id)
                if q['module_id'] == module_id and q.get('fingerprint', fingerprint) == fingerprint]

    def _bank_history(self, course, module_id):
        return question_bank.answer_history(self._bank_rows(course, module_id),
            [r for r in course.store.records('question_answers', course.plan_id) if r['module_id'] == module_id],
            [r for r in course.store.records('judgements', course.plan_id) if r['event']['module_id'] == module_id])

    def _bank_fill(self, course, ctx, module_id, *, background=False, point_ids=None, force=False,
                   needs=None, used_angles=None, refill_for=None, reserve=None, asked=(), retry_round=None, avoid=()):
        """Review before banking; retain drafts and durable per-point reissue limits."""
        from ..course import question_review, question_solver
        ids = list(point_ids or [p['id'] for p in course.points_of(module_id)])
        fingerprint = self._test_fingerprint(course, module_id)
        mode = 'reserve' if reserve is not None else 'refill' if refill_for else 'explicit' if needs is not None else 'initial'
        scopes = {pid: dict(version=question_review.VERSION, fingerprint=fingerprint, point_id=pid,
            mode=mode, force=force, refill_for=refill_for, reserve=(reserve or {}).get(pid),
            quota=needs.get(pid) if mode == 'explicit' else None,
            used_angles=sorted((used_angles or {}).get(pid, ()))) for pid in ids}
        journal = question_review.ReviewJournal(self.services.account_id, self.services.data_root,
                                                course.plan_id, module_id, scopes)
        with journal.lock:
            if retry_round:
                journal.begin_round(ids, retry_round)
            # Foreground and background jobs recount after acquiring the same
            # module lock, including successful inserts from an earlier job.
            rows = self._bank_rows(course, module_id)
            if reserve is not None:
                needs = {pid: max(0, reserve.get(pid, 0) - sum(q['point_ids'] == [pid] and not q['correct_seen']
                    and q['id'] not in asked and q['angle'] not in (used_angles or {}).get(pid, ()) for q in rows)) for pid in ids}
            elif refill_for:
                needs = {pid: max(0, min(2, len(ANGLES) - len((used_angles or {}).get(pid, ()))) - sum(
                    q['point_ids'] == [pid] and q.get('refill_for') == refill_for for q in rows)) for pid in ids}
            elif needs is None:
                needs = ({pid: 1 for pid in ids} if force else question_bank.initial_deficits(rows, ids))
            remaining = {pid: min(2, int(needs.get(pid, 0))) for pid in ids}
            written, unfillable = question_review.written_allocation(rows, remaining)
            if unfillable:
                raise ModelError('QUESTION_BANK_TYPE_RATIO', '模块简答/纠错不足30%，现有题已占满配额；未降低标准或无限补题。')
            pending_ids = {pid for _, row in journal.candidates(ids) if row['status'] in ('pending', 'approved')
                           for pid in row['question']['point_ids']}
            wanted = [pid for pid in ids if remaining[pid] > 0 or pid in pending_ids]
            angles = {pid: set((used_angles or {}).get(pid, ())) for pid in ids}
            if any(len(angles[pid]) == len(ANGLES) for pid in wanted):
                raise ModelError('QUESTION_ANGLES_EXHAUSTED', '该点的五个角度已经问过，无法提供未用角度的重测题。')
            errors, deferred, processed = {}, set(), set()

            def current():
                if ctx and ctx.cancel.is_set():
                    raise ModelCancelled()
                fresh = _Course(course.store, course.store.get_plan(course.plan_id))
                if self._test_fingerprint(fresh, module_id) != fingerprint:
                    raise ModelError('QUESTION_MATERIAL_CHANGED', '资料已更新，正在重新准备题目。')
                return fresh

            def review_candidate(token, row, group, composites):
                if token in processed or row['status'] not in ('pending', 'approved'):
                    return composites
                processed.add(token)
                q = row['question']
                if not set(q['point_ids']) <= set(group):
                    return composites
                if row['status'] == 'pending' and len(q['point_ids']) == 1:
                    pid = q['point_ids'][0]
                    if not remaining[pid] or q.get('angle') in angles[pid]:
                        # A candidate with an already-filled quota/angle cannot
                        # enter the bank; do not pay for its independent review.
                        row['status'] = 'surplus'; journal.save(token, row)
                        return composites
                    if q['type'] not in question_review.WRITTEN_TYPES and remaining[pid] <= written[pid]:
                        row['status'] = 'wrong_type'; journal.save(token, row)
                        return composites
                while row['status'] == 'pending' and row['verdicts'] < question_review.MAX_VERDICTS:
                    fresh = current()
                    q = row['question']
                    points = [fresh.point(pid) for pid in q['point_ids']]
                    excerpts = {ref: text for ref, text in fresh.excerpts(q['point_ids']).items() if ref in q['chunk_ids']}
                    issues = question_review.check_question(q, points=points, excerpts=excerpts,
                        essay_count=sum(item['type'] == 'essay' for item in self._bank_rows(fresh, module_id)))
                    try:
                        verification = row.setdefault('verification', {})
                        if not issues and q['type'] != 'essay':
                            question_solver.verify(self.services.gateway, q, points=points, excerpts=excerpts,
                                state=verification, save=lambda: journal.save(token, row),
                                cancel=ctx.cancel if ctx else None, plan_id=course.plan_id)
                            current()
                        request = question_review.review_request(q, points=points, excerpts=excerpts,
                            structure_issues=issues, verification=verification)
                        row['review_calls'] += 1
                        journal.save(token, row)  # durable before sending, never store credentials
                        result = self.services.gateway.complete('question_review', request[1], system=request[0],
                            cancel=ctx.cancel if ctx else None, plan_id=course.plan_id, max_tokens=24576)
                        verdict = question_review.parse_review(result.text, q)
                    except ModelCancelled:
                        raise
                    except (ModelError, AgentFormatError) as exc:
                        row['error'] = getattr(exc, 'message', str(exc))
                        journal.save(token, row)
                        deferred.update(q['point_ids'])
                        errors.update({pid: '核对中断，题目已保留；下次只重新核对。' for pid in q['point_ids']})
                        return composites
                    row['verdicts'] += 1
                    row['reviews'].append(dict(verdict=verdict.verdict, issues=list(verdict.issues),
                                                structure_issues=list(issues), verification=copy.deepcopy(verification)))
                    if verdict.verdict == 'approve' and not issues and (q['type'] == 'essay' or verification.get('matched') is True):
                        row.update(status='approved', error='')
                    elif verdict.verdict == 'revise':
                        row['question'] = verdict.revised
                        row['verification'] = {}  # revised question must be solved afresh
                    else:
                        row['status'] = 'rejected'
                    journal.save(token, row)
                if row['status'] == 'pending':
                    row['status'] = 'rejected'  # two completed checks without approval
                    journal.save(token, row)
                if row['status'] != 'approved':
                    reason = '; '.join(row['reviews'][-1]['issues']) if row['reviews'] else '两次核对未批准。'
                    errors.update({pid: reason or '结构或独立解题核验未通过。' for pid in q['point_ids']})
                    return composites
                q = row['question']
                fresh = current()  # a late result cannot enter changed material or a cancelled job
                duplicate = any(item['stem'] == q['stem'] and item['point_ids'] == q['point_ids']
                    and item['angle'] == q['angle'] for item in self._bank_rows(fresh, module_id))
                if duplicate:
                    # The bank insert may have succeeded before a journal write
                    # failed. Its coverage was already counted under the lock.
                    row['status'] = 'stored'; journal.save(token, row)
                    return composites
                if len(q['point_ids']) == 1:
                    pid = q['point_ids'][0]
                    if not remaining[pid] or (q['angle'] in angles[pid] and not duplicate):
                        row['status'] = 'surplus'; journal.save(token, row)
                        return composites
                    if q['type'] not in question_review.WRITTEN_TYPES and remaining[pid] <= written[pid]:
                        row['status'] = 'wrong_type'; journal.save(token, row)
                        return composites
                elif len(group) != 2 or composites:
                    row['status'] = 'surplus'; journal.save(token, row)
                    return composites
                # Admission evidence lets quick/deepen reuse only reviewed rows.
                stored_ids = fresh.store.add_bank_questions(course.plan_id, module_id, [q], fingerprint=fingerprint, refill_for=refill_for)
                if stored_ids:
                    self._remember_probe_review(fresh,module_id,stored_ids)
                if len(q['point_ids']) == 1:
                    remaining[pid] -= 1; angles[pid].add(q['angle'])
                    if q['type'] in question_review.WRITTEN_TYPES:
                        written[pid] = max(0, written[pid] - 1)
                else:
                    composites += 1
                row['status'] = 'stored'; journal.save(token, row)
                return composites

            def generate(group, composites, *, written_only=False):
                journal.start_generation(group)  # first generation + one reissue, survives reopening
                current()
                old = list(avoid) + [q['stem'] for q in self._bank_rows(course, module_id) if set(q['point_ids']) & set(group)]
                old += [row['question']['stem'] for _, row in journal.candidates(group)]
                if written_only:
                    fresh = current()
                    excerpts = fresh.excerpts(group)
                    qctx = dict(purpose='bank', per_point=2, written_only=True, avoid=old[-100:],
                        points=[fresh.point(pid) for pid in group], excerpts=excerpts,
                        counts={pid: remaining[pid] for pid in group},
                        used_angles={pid: sorted(angles[pid]) for pid in group})
                    request = question_agent.question_request(qctx)
                    result = self.services.gateway.complete('questions', request[1], system=request[0],
                        cancel=ctx.cancel if ctx else None, plan_id=course.plan_id, max_tokens=16384)
                    batch = question_agent.parse_questions(result.text, qctx, partial_bank=True)
                else:
                    batch = self._questions(course, ctx, 'bank', group, avoid=old, background=background,
                        counts={pid: remaining[pid] for pid in group}, used_angles={pid: sorted(angles[pid]) for pid in group})
                current()
                pending = [journal.add(q) for q in batch]
                for token, row in pending:
                    composites = review_candidate(token, row, group, composites)
                return composites

            for offset in range(0, len(wanted), 2):
                group, composites = wanted[offset:offset + 2], 0
                try:
                    for token, row in journal.candidates(group):
                        composites = review_candidate(token, row, group, composites)
                    first = [pid for pid in group if remaining[pid] and pid not in deferred and not journal.generations(pid)]
                    if first:
                        composites = generate(first, composites)
                except ModelCancelled:
                    raise
                except (ModelError, AgentFormatError) as exc:
                    if getattr(exc, 'code', '') == 'QUESTION_MATERIAL_CHANGED': raise
                    errors.update({pid: getattr(exc, 'message', str(exc)) for pid in group})
                for pid in group:
                    if remaining[pid] and pid not in deferred and journal.generations(pid) < question_review.MAX_GENERATIONS:
                        try:
                            generate([pid], 0, written_only=bool(written[pid]))
                        except ModelCancelled:
                            raise
                        except (ModelError, AgentFormatError) as exc:
                            if getattr(exc, 'code', '') == 'QUESTION_MATERIAL_CHANGED': raise
                            errors[pid] = getattr(exc, 'message', str(exc))
                    if remaining[pid]:
                        errors.setdefault(pid, '核对后仍缺题；每点初次出题与一次重出已经用完。')
                    else:
                        errors.pop(pid, None)
                    if remaining[pid] and pid not in deferred:
                        errors[pid] += ' 本轮初次出题与一次重出已经用完。'
            if errors:
                details = '；'.join(f"{course.point(pid)['title']}：{reason}" for pid, reason in errors.items())
                raise ModelError('QUESTION_BANK_INCOMPLETE', '题目准备失败：' + details)
            _, shortfall = question_review.written_allocation(self._bank_rows(current(), module_id), {})
            if shortfall:
                raise ModelError('QUESTION_BANK_TYPE_RATIO', '模块单点题中的简答/纠错不足30%，尚未准备完成。')

    def _prepare_bank_later(self, course, module_id, *, point_ids=None, force=False,
                            needs=None, used_angles=None, refill_for=None, reserve=None, asked=(), retry_round=None):
        key = (course.plan_id, module_id)
        fingerprint = self._test_fingerprint(course, module_id)
        request = dict(point_ids=point_ids, force=force, needs=needs, used_angles=used_angles,
                       refill_for=refill_for, reserve=reserve, asked=list(asked), retry_round=retry_round)
        with self._guard:
            old = self._bank_jobs.get(key)
            if old and old['fingerprint'] == fingerprint and old['status'] == 'running':
                if (needs is not None or refill_for is not None or force or reserve is not None) and request not in old['queue']:
                    old['queue'].append(request)
                return
            job = {'fingerprint': fingerprint, 'status': 'running', 'error': '', 'queue': [request]}
            self._bank_jobs[key] = job
            self._bank_seen[key] = fingerprint
        def work():
            while True:
                with self._guard:
                    if not job['queue']:
                        job['status'] = 'failed' if job['error'] else 'ready'
                        break
                    item = job['queue'].pop(0)
                try:
                    fresh = _Course(self.services.store, self.services.store.get_plan(course.plan_id))
                    if item.get('retry_round'):
                        job['error'] = ''
                    self._bank_fill(fresh, None, module_id, background=True, **item)
                except Exception as exc:  # still drain independent queued wrong-point refills
                    job['error'] = getattr(exc, 'message', '题目准备失败，请检查模型连接后重试。')
                    log.info('question bank preparation failed for %s', module_id, exc_info=True)
        threading.Thread(target=work, name='classroom-question-bank', daemon=True).start()

    def _prefetch_banks(self, course, state, *, testing=False):
        self._bank_plans.add(course.plan_id)
        for module in course.modules:
            self._bank_seen.setdefault((course.plan_id, module['id']), self._test_fingerprint(course, module['id']))
        ids = [m['id'] for m in course.modules]
        current = state['module_id']
        index = ids.index(current)
        wanted = [current]
        if testing and not self._peak_prep() and index + 1 < len(ids):
            wanted.append(ids[index + 1])
        if index == len(ids) - 1 and not self._peak_prep():  # off-peak final preparation
            wanted = ids
        for module_id in wanted:
            self._prepare_bank_later(course, module_id)

    def _bank_material_changed(self, origin, entities):
        if not entities & {'points', 'modules', 'source_chunks'}:
            return
        for plan_id in tuple(self._bank_plans):
            try:
                course = _Course(self.services.store, self.services.store.get_plan(plan_id))
                for module in course.modules:
                    key = (plan_id, module['id'])
                    fingerprint = self._test_fingerprint(course, module['id'])
                    if self._bank_seen.get(key) != fingerprint:
                        self._prepare_bank_later(course, module['id'])
            except StoreError:
                continue

    def _defer_bank(self, course, state, pending, method, args=()):
        tries = state.get('bank_wait_attempts', 0) + 1
        if tries > 2:
            wait = dict(module_id=pending.module_id, points=pending.points, method=method,
                        args=list(args), step=state['step'], used_angles=pending.used_angles,
                        needs=pending.needs, asked=pending.asked)
            return self._bank_failed(course, state, wait, '本轮出题与核对次数已用完。')
        state['bank_wait_attempts'] = tries
        state['bank_wait'] = {'module_id': pending.module_id, 'points': pending.points,
                              'method': method, 'args': list(args), 'step': state['step'],
                              'used_angles': pending.used_angles, 'needs': pending.needs}
        state['bank_wait']['asked'] = pending.asked
        state['step'] = 'preparing_questions'
        self._say(course, state, '正在准备题目，备好后会自动开始。', role='system')
        self._prepare_bank_later(course, pending.module_id, point_ids=pending.points,
                                 reserve=pending.needs or {pid: 1 for pid in pending.points},
                                 used_angles=pending.used_angles, asked=pending.asked)

    def _bank_failed(self, course, state, wait, reason):
        state.pop('bank_wait', None)
        state['step'] = wait['step']
        state['bank_retry'] = wait
        titles = '、'.join(course.point(pid)['title'] for pid in wait['points'])
        self._say(course, state, f'{titles}暂时出不了合格题：{reason} '
                  '本轮已停止。可点击当前的检测/继续检验按钮重新准备一轮题目，'
                  '或离开后重新进入；已有学习与检验记录保留。若仍失败，请补充该点资料或检查模型连接。',
                  kind='error', role='system')

    def _retry_bank(self, course, state):
        """Existing test/check button or a fresh enter, never a snapshot poll."""
        wait = state.pop('bank_retry', None)
        if not wait:
            return False
        state['bank_wait_attempts'] = 0
        state['bank_wait'] = wait
        state['step'] = 'preparing_questions'
        self._say(course, state, '按你的操作重新准备一轮题目；仍会先独立解题并核对，不降低标准。', role='system')
        self._prepare_bank_later(course, wait['module_id'], point_ids=wait['points'],
            reserve=wait.get('needs') or {pid: 1 for pid in wait['points']},
            used_angles=wait.get('used_angles'), asked=wait.get('asked', ()), retry_round=uuid.uuid4().hex)
        return True

    def _resume_bank_wait(self, course, state, ctx):
        wait = state.get('bank_wait')
        if not wait:
            return
        job = self._bank_jobs.get((course.plan_id, wait['module_id']))
        if not job or job['fingerprint'] != self._test_fingerprint(course, wait['module_id']):
            self._prepare_bank_later(course, wait['module_id'], point_ids=wait['points'],
                                     reserve=wait.get('needs') or {pid: 1 for pid in wait['points']},
                                     used_angles=wait.get('used_angles'), asked=wait.get('asked', ()))
            return
        if job['status'] == 'running':
            return
        state['step'] = wait['step']
        state.pop('bank_wait', None)
        if job['status'] == 'failed':
            return self._bank_failed(course, state, wait, job['error'])
        getattr(self, wait['method'])(course, state, ctx, *wait['args'])

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
            self._prefetch_banks(course, state)
            self._prepare_later(course, state)
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
        if self._retry_bank(course, state):
            return
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
        ever_asked, _ = self._bank_history(course, module_id)
        asked = sorted(ever_asked | set(state.get('check_asked', ())))
        used = {pid: set(book.point(pid).used_angles) for pid in group}
        # MasteryBook rotates after all five final angles have been used.
        # Reset only that point; preserve the other points' angle exclusions.
        used = {pid: set() if len(angles) >= len(ANGLES) else angles for pid, angles in used.items()}
        fresh_rows = [q for q in self._bank_rows(course, module_id)
                      if used is None or all(q['angle'] not in used.get(pid, ()) for pid in q['point_ids'])]
        needs = question_bank.final_deficits(fresh_rows, {pid: book.point(pid).level for pid in group},
                                             asked=asked)
        unstable = [pid for pid in group if book.point(pid).module_errors or book.point(pid).state in ('red', 'weak')]
        try:
            if any(needs.values()):
                raise _BankPending(module_id, [pid for pid in group if needs[pid]], used,
                    {pid: 1 if book.point(pid).level == 2 else 2 for pid in group}, asked)
            questions = self._bank_select(course, ctx, module_id, group, unstable,
                limit=min(CHECK_BATCH - state.get('check_used', 0), len(group)),
                smaller_than=split[0]['size'] if split else None,
                single_once=state.get('check_single', []), asked=asked, used_angles=used, defer=True)
        except _BankPending as pending:
            return self._defer_bank(course, state, pending, '_next_check')
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
        course.store.mark_bank_asked(course.plan_id, q.get('id'), 'check')
        if q['type'] == 'choice' and not q.get('shuffled'):
            self._rng.shuffle(q['options']); q['shuffled'] = True
        state["module_id"] = check["module_id"]  # the teacher's context for questions asked meanwhile
        state["pending"] = {"kind": "check", "answer_id": uuid.uuid4().hex,
                            "point_ids": q["point_ids"], "question": q, 'depth_probe': q.get('depth_probe', False)}
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
            verdict = self._adjudicate(course, state, ctx, "casual", pending, text)  # a question is allowed here
        if verdict is None: return
        if (verdict := self._information_result(course, state, pending, text, verdict)) is None: return
        if verdict.uncertain or verdict.kind == 'clarify':
            verdict = self._deepen_question(course, state, ctx, pending, verdict)
            if verdict is None: return
        if verdict.kind == "question":
            return self._teacher(course, state, ctx, "最终检验中，学习者先问了一个问题。简短解答，但不要说出当前这道检验题的答案，"
                                 "也不要重讲整个知识点；答完提醒他回答上面的题（不要出新题）。", q["point_ids"], user=text)
        if verdict.kind != "answer":
            message = {"change_rules": "检验规则和进度由系统决定，对话里改不了。请直接回答上面的题；不会可以说“不会”。",
                       "claim_known": "检验要靠作答来确认。请直接回答上面的题。"}.get(
                verdict.kind, "没看出这是对这道题的作答。请直接回答；不会的话说“不会”也可以。")
            return self._say(course, state, message)
        if not verdict.correct and len(q['point_ids']) > 1 and not judge_agent.structured_rubric({'question': q}):
            state.setdefault('check_split', []).append({'point_ids': q['point_ids'], 'size': len(q['point_ids'])})
        else:
            self._record(course, state, "check", pending, verdict, text, angle=q.get("angle"))
            for pid in verdict.wrong_points:
                self._refill_wrong(course, state, dict(pending, point_ids=[pid]),
                    judge_agent.Verdict('answer', False, (pid,), verdict.reason))
        self._answer_fact(course, state, 'check', pending, verdict)
        if verdict.correct and q.get('id'):
            course.store.mark_bank_correct(course.plan_id, q['id'])
        explanation = "" if verdict.correct else ("综合题未通过，暂不归因到任何知识点；接下来拆开问。"
                                                 if len(q['point_ids']) > 1 and not judge_agent.structured_rubric({'question': q}) else verdict.reason)
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
                return self._defer_bank(course, state, _BankPending(point['module_id'], [point['id']], asked=used), '_ask_practice')
            ranked = [q for q in pool if q['point_ids'][0] in review] or pool
            practice['queue'].append(dict(self._rng.choice(ranked)))
        q = practice['queue'][practice['index']]
        course.store.mark_bank_asked(course.plan_id, q.get('id'), 'practice')
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
            verdict = self._adjudicate(course, state, ctx, 'test', pending, text)
        if verdict is None: return
        if (verdict := self._information_result(course, state, pending, text, verdict)) is None: return
        if verdict.uncertain or verdict.kind == 'clarify':
            verdict = self._deepen_question(course, state, ctx, pending, verdict)
            if verdict is None: return
        if verdict.kind != 'answer':
            return self._say(course, state, '请回答当前这道题。')
        if prior is None:
            course.store.append_practice_result(course.plan_id, q['id'], q['module_id'], q['point_ids'],
                verdict.correct, reason=verdict.reason, at=self.now(), result_id=self._answer_id(course, state, pending),
                wrong_points=verdict.wrong_points)
        self._answer_fact(course, state, 'practice', pending, verdict)
        for pid in verdict.wrong_points:
            self._refill_wrong(course, state, dict(pending, point_ids=[pid]),
                judge_agent.Verdict('answer', False, (pid,), verdict.reason))
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
        wrong_ids = sorted(row['id'] for row in course.store.records('judgements', plan_id)
                           if any(not correct for _, correct in row['event']['results']))
        if not wrong_ids or self._profile_document(course).get('check_error_ids') == wrong_ids:
            return
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
                document = dict(self._profile_document(fresh), check=result.text, check_at=self.now().isoformat(),
                                check_error_ids=wrong_ids)
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

    def _prep_context(self, course, point):
        module = course.module(point["module_id"])
        excerpts = course.excerpts([p["id"] for p in course.points_of(module["id"])])
        return {"module": {"id": module["id"], "title": module["title"]},
                "point": {"id": point["id"], "title": point["title"], "summary": point["summary"],
                          "chunk_ids": point["chunk_ids"]}, "excerpts": excerpts, "prefs": self._prefs()}

    def _prep_fingerprint(self, course, point):
        return lesson_prep.fingerprint(course.plan, course.module(point["module_id"]), point,
                                       course.excerpts([point["id"]]), prefs=self._prefs())

    def _prepared_point(self, course, point):
        row = course.store.load_lesson_cache(course.plan_id)["lesson_preps"].get(point["id"])
        if not isinstance(row, dict) or row.get("fingerprint") != self._prep_fingerprint(course, point):
            return None
        if row.get("status") != "reviewed" or row.get("version") != lesson_prep.VERSION:
            return None
        try:
            prepared = lesson_prep.parse_prep(json.dumps(row["lesson"], ensure_ascii=False), chunk_ids=point["chunk_ids"])
            return None if lesson_prep.check_lesson(prepared, chunk_ids=point["chunk_ids"]) else prepared
        except (AgentFormatError, KeyError, TypeError):
            return None

    @staticmethod
    def _test_fingerprint(course, module_id):
        points = course.points_of(module_id)
        return question_bank.material_fingerprint(points, course.excerpts([p["id"] for p in points]))

    def _peak_prep(self):
        try:
            gateway = self.services.gateway
            if gateway.get_configuration().get("provider_id") != "deepseek":
                return False
            return gateway.price_catalog._period(self.now()) != "off_peak"
        except (AttributeError, KeyError, TypeError, ValueError):
            return True

    def _prep_targets(self, course, state):
        from datetime import date, timedelta
        day = date.fromisoformat(state.get("day") or self._day(state))
        targets = course.store.load_ledger(course.plan_id).targets
        today = self._today(course, state)
        remaining = max(0, targets.get(day, 0) - today["today_confirmed"] - today["today_tentative"])
        peak = self._peak_prep()
        budget = min(60, remaining) if peak else remaining + (0 if state.get("start_only") else targets.get(day + timedelta(days=1), 0))
        points = course.points_of(state["module_id"])
        if state["index"] >= len(points):
            start = next((i for i, p in enumerate(course.points) if p["module_id"] == state["module_id"]), 0) + len(points)
        else:
            current = points[state["index"]]
            start = next(i for i, p in enumerate(course.points) if p["id"] == current["id"])
        taught = course.taught()
        candidates = [p for p in course.points[start:] if p["id"] not in taught]
        chosen, minutes = [], 0
        for point in candidates:
            # During class the current point plus three successors are
            # immediately-needed work, even at peak price or past today's
            # target. Only the more distant daily window may be deferred.
            minimum = 1 if state.get("start_only") else 4
            if chosen and minutes >= budget and len(chosen) >= minimum:
                break
            chosen.append(point)
            minutes += point["minutes"]
        return chosen

    def _schedule_idle_prep(self, course, state):
        # Only rooms started/entered in this process; no advance on an absent
        # day. A daemon stops naturally on app exit and never wakes the app.
        self._prep_rooms[course.plan_id] = copy.deepcopy(state)
        if self._prep_timer is not None and self._prep_timer.is_alive():
            return
        def tick():
            self._prep_timer = None
            alive = False
            for plan_id, saved in list(self._prep_rooms.items()):
                try:
                    course = self._course(plan_id)
                    if saved.get("day") != self.study_day().isoformat() or course.plan["state"] != "active":
                        continue
                    alive = True
                    state = self._load(course) if not saved.get("start_only") else saved
                    state["day"] = saved["day"]
                    if not self._peak_prep():
                        self._prefetch_banks(course, state, testing=True)
                        self._prepare_later(course, state)
                except Exception:
                    # The optional idle poll must also stop quietly after
                    # account/store disposal (sqlite ProgrammingError).
                    log.debug("idle preparation room unavailable", exc_info=True)
                    continue
            if alive:
                self._prep_timer = threading.Timer(60, tick)
                self._prep_timer.name = "lesson-idle-tick"
                self._prep_timer.daemon = True
                self._prep_timer.start()
        self._prep_timer = threading.Timer(60, tick)
        self._prep_timer.name = "lesson-idle-tick"
        self._prep_timer.daemon = True
        self._prep_timer.start()

    def prepare_started_plan(self, plan_id):
        course = self._course(plan_id)
        state = self._rebuild(course)
        state.update(day=self.study_day().isoformat(), start_only=True)
        self._prefetch_banks(course, state)
        self._prepare_later(course, state)
        self._schedule_idle_prep(course, state)

    def _prepare_later(self, course, state):
        if state.get("step") in ("completed", "checking"):
            return
        with self._guard:
            self._prep_latest[course.plan_id] = copy.deepcopy(state)
            if course.plan_id in self._preparing:
                return
            self._preparing.add(course.plan_id)
        frozen = copy.deepcopy(state)
        def work():
            try:
                attempted = set()
                while True:
                    fresh = self._course(course.plan_id)
                    with self._guard:
                        latest = copy.deepcopy(self._prep_latest.get(course.plan_id, frozen))
                    if latest.get("step") in ("completed", "checking"):
                        break
                    # Replenish from the latest position between batches;
                    # a skipped module/day change must not sit behind an
                    # obsolete frozen queue. Retry limits remain in the rows.
                    targets = [p for p in self._prep_targets(fresh, latest) if p["id"] not in attempted]
                    if not targets:
                        break
                    keep = {p["id"] for p in fresh.points}
                    group = targets[:1]
                    if len(targets) > 1 and targets[1]["module_id"] == group[0]["module_id"]:
                        group.append(targets[1])
                    attempted.update(p["id"] for p in group)
                    if len(group) == 2:
                        self._prepare_batch(fresh, group, keep)
                    else:
                        self._prepare_point(fresh, group[0], keep)
            except Exception:
                log.info("background lesson preparation failed", exc_info=True)
            finally:
                with self._guard:
                    self._preparing.discard(course.plan_id)
                    latest = self._prep_latest.get(course.plan_id, frozen)
                # A foreground advance during this job must not lose its
                # request to replenish the three-point lead. No retry loop
                # for an unchanged position or a failed review.
                position = lambda s: (s.get("day"), s.get("module_id"), s.get("index"))
                if position(latest) != position(frozen):
                    self._prepare_later(self._course(course.plan_id), latest)
        threading.Thread(target=work, name="classroom-lesson-prep", daemon=True).start()

    def _prep_lock(self, course, point):
        key = (str(self.services.data_root), self.services.account_id, course.plan_id, point["id"])
        with self._lesson_mutex:
            return self._lesson_locks.setdefault(key, threading.RLock())

    def _prep_key(self, course, point):
        return (str(self.services.data_root), self.services.account_id, course.plan_id, point["id"])

    def _prep_job_event(self, course, point):
        with self._lesson_mutex:
            job = self._lesson_jobs.get(self._prep_key(course, point))
            return job[1] if job else None

    @contextmanager
    def _prep_claim(self, course, point):
        key, owner = self._prep_key(course, point), threading.get_ident()
        with self._lesson_mutex:
            previous = self._lesson_jobs.get(key)
            owned = previous is None or previous[0] == owner
            if previous is None:
                self._lesson_jobs[key] = (owner, threading.Event())
        try:
            yield owned
        finally:
            if owned and previous is None:
                with self._lesson_mutex:
                    _, event = self._lesson_jobs.pop(key)
                    event.set()

    def _prep_ask(self, course, purpose, request, parse, max_tokens):
        if purpose == "questions":
            while self._lock(course.plan_id).locked(): time.sleep(0.1)
            return self._ask(course, None, purpose, request, parse, max_tokens)
        foreground = getattr(self._prep_context_local, "foreground", None)
        result = self.services.gateway.complete(purpose, request[1], system=request[0],
            plan_id=course.plan_id, max_tokens=max_tokens,
            cancel=getattr(foreground, "cancel", None))
        return parse(result.text)

    def _prep_row(self, course, point):
        row = dict(course.store.load_lesson_cache(course.plan_id)["lesson_preps"].get(point["id"]) or {})
        fingerprint = self._prep_fingerprint(course, point)
        if row.get("version") != lesson_prep.VERSION or row.get("fingerprint") != fingerprint:
            row = dict(version=lesson_prep.VERSION, fingerprint=fingerprint, status="draft",
                       generation_attempts=0, review_attempts=0, total_reviews=0, live_used=row.get("live_used", False))
        return row

    def _persist_prep(self, course, point, row, keep):
        with self._prep_lock(course, point):
            fresh = self._course(course.plan_id)
            if self._prep_fingerprint(fresh, fresh.point(point["id"])) != row["fingerprint"]:
                return False
            course.store.save_lesson_cache_item(course.plan_id, "lesson_preps", point["id"], row, keep_ids=keep)
            return True

    def _save_prepared(self, course, point, approved, keep):
        row = self._prep_row(course, point)
        row["status"] = "reviewed" if approved and not lesson_prep.check_lesson(approved, chunk_ids=point["chunk_ids"]) else "needs_live_teaching"
        if approved: row["lesson"] = asdict(approved)
        self._persist_prep(course, point, row, keep)

    def _prepare_batch(self, course, points, keep):
        from contextlib import ExitStack
        with ExitStack() as stack:
            points = [p for p in points if stack.enter_context(self._prep_claim(course, p))]
            pending = [p for p in points if self._prep_row(course, p)["status"] not in ("reviewed", "needs_live_teaching")]
            new = [p for p in pending if self._prep_row(course, p)["generation_attempts"] == 0]
            if len(new) == 2:
                rows = [self._prep_row(course, p) for p in new]
                for p, row in zip(new, rows):
                    row.update(generation_attempts=1, review_attempts=0, status="draft")
                    if not self._persist_prep(course, p, row, keep): return
                try:
                    request = lesson_prep.batch_prep_request([self._prep_context(course, p) for p in new])
                    drafts = self._prep_ask(course, "lesson_prep", request,
                        lambda raw: lesson_prep.parse_batch_preps(raw, tuple(p["id"] for p in new)), 16384)
                    for p, row, draft in zip(new, rows, drafts):
                        row["lesson"] = asdict(draft)
                        row["status"] = "review_pending"
                        self._persist_prep(course, p, row, keep)
                except ModelCancelled:
                    raise
                except (ModelError, AgentFormatError):
                    log.info("batch draft failed; bounded per-point rewrite remains")
            # One source read for two reviews; durable point counters and
            # independently validated results still survive partial failure.
            ready = []
            for point in pending:
                row = self._prep_row(course, point)
                if row.get("lesson") and row["review_attempts"] < 2:
                    draft = lesson_prep.parse_prep(json.dumps(row["lesson"], ensure_ascii=False))
                    if not lesson_prep.check_lesson(draft, chunk_ids=point["chunk_ids"]):
                        ready.append((point, row, draft))
            if len(ready) == 2:
                for point, row, _ in ready:
                    row["review_attempts"] += 1
                    row["total_reviews"] += 1
                    if not self._persist_prep(course, point, row, keep): return
                try:
                    request = lesson_prep.batch_review_request(
                        [self._prep_context(course, p) for p, _, _ in ready],
                        tuple(d for _, _, d in ready))
                    reviews = self._prep_ask(course, "lesson_review", request,
                        lambda raw: lesson_prep.parse_batch_review_results(raw, tuple(p["id"] for p, _, _ in ready)), 16384)
                except ModelCancelled:
                    raise
                except (ModelError, AgentFormatError) as exc:
                    for point, row, _ in ready:
                        row.update(status="review_pending", error=str(exc))
                        self._persist_prep(course, point, row, keep)
                    return  # Resume original drafts on re-entry, no immediate transport retry.
                for (point, row, draft), review in zip(ready, reviews):
                    self._prep_apply_review(course, point, row, draft, review, keep)
            for point in pending:
                # A malformed sibling gets its remaining retry on re-entry,
                # just like a single-point review format failure.
                if len(ready) == 2 and any(p["id"] == point["id"] and isinstance(r, AgentFormatError)
                                          for (p, _, _), r in zip(ready, reviews)):
                    continue
                self._prepare_point_locked(course, point, keep)

    def _prep_apply_review(self, course, point, row, draft, review, keep):
        if isinstance(review, AgentFormatError):
            row.update(status="review_pending", error=str(review))
        elif review.verdict == "approve" and not lesson_prep.check_lesson(draft, chunk_ids=point["chunk_ids"]):
            row.update(status="reviewed", error="")
        else:
            row["issues"] = list(review.issues)
            if review.revised:
                row["lesson"] = asdict(review.revised)
            if review.verdict == "live":
                row["review_attempts"] = 2
        self._persist_prep(course, point, row, keep)

    def _prepare_point(self, course, point, keep):
        with self._prep_claim(course, point) as owned:
            if owned:
                self._prepare_point_locked(course, point, keep)

    def _prepare_point_locked(self, course, point, keep):
        row = self._prep_row(course, point)
        if row["status"] in ("reviewed", "needs_live_teaching"):
            return
        context = self._prep_context(course, point)
        while row["generation_attempts"] <= 2:
            if not row.get("lesson"):
                if row["generation_attempts"] >= 2: break
                row.update(generation_attempts=row["generation_attempts"] + 1, review_attempts=0, status="draft")
                if not self._persist_prep(course, point, row, keep): return
                try:
                    draft = self._prep_ask(course, "lesson_prep", lesson_prep.prep_request(context),
                        lambda raw: lesson_prep.parse_prep(raw, chunk_ids=point["chunk_ids"]), 16384)
                    row.update(lesson=asdict(draft), status="review_pending")
                    if not self._persist_prep(course, point, row, keep): return
                except ModelCancelled:
                    raise
                except (ModelError, AgentFormatError) as exc:
                    row["error"] = str(exc)
                    self._persist_prep(course, point, row, keep)
                    continue
            draft = lesson_prep.parse_prep(json.dumps(row["lesson"], ensure_ascii=False))
            local_issues = lesson_prep.check_lesson(draft, chunk_ids=point["chunk_ids"])
            if local_issues:
                row["issues"] = list(local_issues)
                self._persist_prep(course, point, row, keep)
            while not local_issues and row["review_attempts"] < 2:
                row["review_attempts"] += 1
                row["total_reviews"] += 1
                if not self._persist_prep(course, point, row, keep): return
                draft = lesson_prep.parse_prep(json.dumps(row["lesson"], ensure_ascii=False))
                try:
                    review = self._prep_ask(course, "lesson_review", lesson_prep.review_request(context, draft), lesson_prep.parse_review, 16384)
                except ModelCancelled:
                    raise
                except (ModelError, AgentFormatError) as exc:
                    row.update(status="review_pending", error=str(exc))
                    self._persist_prep(course, point, row, keep)
                    # Preserve the manuscript across a re-entry; never restart
                    # generation merely because a review transport failed.
                    if row["review_attempts"] < 2: return
                    break
                if review.verdict == "approve" and not lesson_prep.check_lesson(draft, chunk_ids=point["chunk_ids"]):
                    row.update(status="reviewed", error="")
                    self._persist_prep(course, point, row, keep)
                    return
                row["issues"] = list(review.issues)
                if review.revised:
                    row["lesson"] = asdict(review.revised)
                    self._persist_prep(course, point, row, keep)
                    local_issues = lesson_prep.check_lesson(review.revised, chunk_ids=point["chunk_ids"])
                    if local_issues:
                        row["issues"] = list(local_issues)
                        self._persist_prep(course, point, row, keep)
                if review.verdict == "live": break
            if row["generation_attempts"] >= 2: break
            # Exactly one full rewrite after the original could not be approved.
            row.pop("lesson", None)
            row["review_attempts"] = 0
            self._persist_prep(course, point, row, keep)
        row["status"] = "needs_live_teaching"
        self._persist_prep(course, point, row, keep)

    def _teacher(self, course, state, ctx, director, point_ids, *, user="", asks=False, prepared=None, short=False):
        """asks=False: a question the step did not ask for stays part of the explanation, not a card."""
        pending = state.get("pending")
        protected = bool(pending and self._prior_verdict(course, state, pending) is None)
        question = pending["question"] if protected else None
        active = state.get("lesson")
        if active and prepared is None:
            prepared = lesson_prep.parse_prep(json.dumps(active["draft"], ensure_ascii=False), live=active.get("live", False))
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
        page = getattr(self, "_teacher_request_page", None)
        if page is None:
            self._teacher_request_page = page = AppendOnlyPage()
        tctx["request_page"] = page
        if protected:
            tctx["pending_question"] = {"stem": question["stem"], "options": question.get("options", [])}
        elif prepared is not None:
            tctx["prepared_lesson"] = asdict(prepared)
        request = teacher_agent.teacher_request(tctx)
        mapping = teacher_agent.citation_map(tctx)
        stream = ctx.stream if ctx else (lambda _d: None)
        teaching, raw = None, ""
        # The provider counts hidden reasoning against this cap. A 1024-token
        # cap truncated a two-sentence correction and charged for a retry.
        max_tokens = 4096
        for attempt in range(2 if protected else 3):
            if attempt and not protected:
                stream(RESET)
            try:
                result = self.services.gateway.complete("teacher", request[1], system=request[0],
                                                        on_text=None if protected else stream,
                                                        cancel=ctx.cancel if ctx else None, plan_id=course.plan_id,
                                                        max_tokens=max_tokens)
            except ModelCancelled:
                raise
            except ModelError as exc:
                if exc.code == MODEL_OUTPUT_TRUNCATED and max_tokens < 8192:
                    max_tokens = 8192
                    continue
                if protected and not (ctx and ctx.cancel.is_set()):
                    if attempt:
                        break
                    continue
                if attempt or exc.code not in TRANSIENT or (ctx and ctx.cancel.is_set()):
                    raise
                continue
            raw = result.text
            try:
                teaching = teacher_agent.parse_teaching(raw, tctx)
                if protected:
                    if teaching.casual_question and teaching.casual_question != question["stem"]:
                        raise AgentFormatError("待答题尚未判定，不能替换原题")
                    if teacher_agent.answer_leak(teaching.speech, question):
                        raise AgentFormatError("待答题回复包含参考答案要点")
                    # A restated original question remains prose, never a new
                    # card or a replacement answer id.
                    if teaching.casual_question:
                        teaching = teacher_agent.Teaching(teaching.speech + "\n\n" + question["stem"], None,
                                                         teaching.cited_chunks)
                if not asks and teaching.speech.rstrip().endswith(("？", "?")):
                    if not protected:
                        raise AgentFormatError("老师在未要求提问的步骤结尾新增了问题")
                break
            except AgentFormatError as exc:
                teaching = None
                log.info("teacher answer rejected (%s), attempt %s", exc, attempt + 1)
                if protected:
                    tctx["director"] += " 上次回复未通过待答题边界检查。请重写：只回应题外内容或重述原题，不讲任何答案要点、提示或知识结论。"
                    request = teacher_agent.teacher_request(tctx)
        if teaching is None:
            if not protected:
                raise ModelError("AGENT_FORMAT", "老师这次的回答格式不对，已重试仍不行。")
            options = "\n".join(f"{chr(65 + i)}. {option}" for i, option in enumerate(question.get("options", [])))
            teaching = teacher_agent.Teaching("先回答这道题，判定之后再讲解答案；不会可以说“不会”。\n\n" +
                                             question["stem"] + ("\n" + options if options else ""), None, ())
            raw = ""
        if protected:
            stream(teaching.speech)
        removed = teacher_agent.citation_diagnostics(raw, tctx) if raw else ()
        if removed:
            log.info("teacher cited unknown excerpts %s", removed)
        page.accept(request[0], request[1], teacher_agent._dialogue(tctx["dialogue"]), raw)
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
        followed = pending.get('information_answer_id') == pending.get('answer_id') and pending.get('information_requested') is True
        if followed:
            if not self._information_delivered(course, pending):
                return judge_agent.Verdict('insufficient', None, (), pending['information_reason'],
                                           checks=tuple(pending['information_checks']))
            reply = self._information_combined_reply(pending, reply)
        if q['type'] == 'choice' and reply.strip() in q['options']:
            correct = reply.strip() == q['answer']
            return judge_agent.Verdict('answer', correct, () if correct else tuple(q['point_ids']),
                                       '选对了。' if correct else '所选选项不符合题目条件。')
        compared = answer_compare.compare_exact(q, reply)
        if compared is False and len(q['point_ids']) > 1:
            compared = None  # a scalar mismatch alone cannot attribute several points
        if compared is not None:
            return judge_agent.Verdict('answer', compared, () if compared else tuple(q['point_ids']),
                '确定答案符合允许的等价写法。' if compared else '确定答案不符合题目给定的结果。',
                checks=({'method': 'program', 'answer_kind': q['answer_kind'], 'correct': compared},))
        points = [course.point(pid) for pid in pending["point_ids"]]
        jctx = {"mode": mode, "points": [{"id": p["id"], "title": p["title"], "summary": p.get("summary", "")} for p in points],
                "excerpts": course.excerpts(pending["point_ids"]),
                "question": q,
                "reference": q.get("reference") or q.get("answer") or "按知识点要点判断",
                "required_points": q.get('required_points'),
                "information_followed_up": followed,
                "reply": reply, "require_evidence": q["type"] in ("short", "fix_error", "essay")}
        request = judge_agent.judge_request(jctx, reply)
        parse = lambda text: judge_agent.parse_judgement(text, jctx)
        first = self._ask(course, ctx, "judge", request, parse, 4096)
        first = judge_agent.defer_incomplete(first, jctx)
        if q['type'] == 'exact' and first.kind == 'answer' and first.correct is False:
            # The answer is decidable, but a free-form reply required the judge
            # to identify it. Its text alone does not prove the student's method
            # (e.g. replying 30 to len(...) is not evidence of summing elements).
            from dataclasses import replace
            return replace(first, reason=f"作答未满足本题要求的确定答案，参考答案为：{q['answer'][:160]}。")
        if self.review_wrong and q['type'] in ('short', 'fix_error', 'essay') and first.kind == 'answer' and not first.correct and not first.uncertain:
            # Exactly the same isolated question/rubric/reply, no first verdict
            # or rationale in the second request. No majority/pass guessing.
            second = self._ask(course, ctx, "judge", request, parse, 4096)
            return judge_agent.review_verdict(first, second)
        return first

    @staticmethod
    def _information_combined_reply(pending, reply):
        if pending.get('information_requested') and pending.get('information_answer_id') == pending.get('answer_id'):
            return f"原回答：{pending['information_reply']}\n补充回答：{reply}"
        return reply

    def _information_delivered(self, course, pending):
        return any(r['id'] == pending.get('information_message_id')
                   for r in course.store.records('messages', course.plan_id))

    def _information_result(self, course, state, pending, text, verdict):
        if verdict.kind != 'insufficient':
            return verdict
        existing = (pending.get('information_requested') and
                    pending.get('information_answer_id') == pending.get('answer_id'))
        if existing and self._information_delivered(course, pending):
            points = [course.point(pid) for pid in pending['point_ids']]
            return judge_agent.settle_insufficient(verdict, dict(
                question=pending['question'], points=points, mode='test',
                reference=pending['question'].get('reference') or pending['question'].get('answer') or '',
                required_points=pending['question'].get('required_points'), require_evidence=True,
                excerpts=course.excerpts(pending['point_ids']), information_followed_up=True))
        if not existing:
            pending.setdefault('answer_id', self._answer_id(course, state, pending))
            pending.update(
                information_requested=True,
                information_answer_id=pending['answer_id'],
                information_reply=self._combined_reply(pending, text),
                information_checks=list(verdict.checks), information_reason=verdict.reason,
                information_message_id=uuid.uuid5(uuid.NAMESPACE_URL,
                                                   'information:' + pending['answer_id']).hex,
                information_prompt=judge_agent.information_prompt(pending['question']))
        self._save(course, state)
        course.store.append_message(course.plan_id, self._channel(state), 'system',
            pending['information_prompt'], message_id=pending['information_message_id'], kind='question', at=self.now())
        return None

    @staticmethod
    def _combined_reply(pending, reply):
        original = pending.get('process_reply')
        if pending.get('process_answer_id') != pending.get('answer_id'): original = None
        return f'原回答：{original}\n补充过程：{reply}' if original is not None else reply

    def _adjudicate(self, course, state, ctx, mode, pending, reply):
        pending.setdefault('answer_id', self._answer_id(course, state, pending))
        verdict = self._prior_verdict(course, state, pending) or self._judge(
            course, state, ctx, mode, pending, self._combined_reply(pending, reply))
        if verdict.kind == 'process_missing':
            pending.setdefault('process_reply', reply)
            pending['process_answer_id'] = pending['answer_id']
            self._say(course, state, '写一下你是怎么得到的', kind='question')
            return None  # keep question/answer ID; neither point judgement nor time fact yet
        return verdict

    def _answer_fact(self, course, state, context, pending, verdict):
        q = pending['question']
        course.store.append_question_answer(course.plan_id, self._answer_id(course, state, pending),
            module_id=course.point(pending['point_ids'][0])['module_id'], context=context,
            attempt_id=(state.get('test') or {}).get('attempt') if context in ('module_test', 'skip_test', 'retest') else None,
            question_id=q.get('id'), point_ids=pending['point_ids'], angle=q.get('angle'),
            correct=verdict.correct, wrong_points=verdict.wrong_points, reason=verdict.reason,
            checks=verdict.checks, at=self.now())

    def _refill_wrong(self, course, state, pending, verdict):
        if pending.get('kind') not in ('module_test', 'skip_test', 'retest', 'check', 'practice'): return
        if verdict.correct or len(pending['point_ids']) > 1: return
        module_id = course.point(pending['point_ids'][0])['module_id']
        _, used = self._bank_history(course, module_id)
        for pid in verdict.wrong_points:
            if pending['question'].get('angle'): used.setdefault(pid, set()).add(pending['question']['angle'])
        self._prepare_bank_later(course, module_id, point_ids=list(verdict.wrong_points),
            refill_for=self._answer_id(course, state, pending), used_angles={pid: sorted(a) for pid, a in used.items()})

    def _probe_pool(self, course, pid, original=None):
        """Reuse reviewed bank rows, including pre-marker initial-bank journals."""
        from ..course import question_review
        module_id = course.point(pid)['module_id']
        asked, _ = self._bank_history(course, module_id)
        old_stem = re.sub(r'\s+', '', (original or {}).get('stem', ''))
        evidence=self._probe_review_file(course,module_id).read({})
        admitted=set(evidence.get('question_ids',())) if evidence.get('version')==question_review.VERSION and evidence.get('fingerprint')==self._test_fingerprint(course,module_id) else set()
        candidates = [q for q in self._bank_rows(course, module_id) if q['point_ids'] == [pid]
            and q['type'] in ('choice', 'exact', 'short', 'fix_error') and not q['correct_seen']
            and not q.get('asked_in') and q['id'] not in asked
            and (not original or (q['angle'] in ('apply','contrast','fix_error','transfer')
                and q['angle'] != original.get('angle') and re.sub(r'\s+', '', q['stem']) != old_stem))]
        # Earlier bank inserts have no admission marker. Only the existing
        # journal's successful review/independent solution can vouch for them;
        # an old on-the-spot deepen insert has no such record.
        legacy = []
        if any(q['id'] not in admitted for q in candidates):
            scope = dict(version=question_review.VERSION, fingerprint=self._test_fingerprint(course,module_id),
                point_id=pid, mode='initial', force=False, refill_for=None, reserve=None, quota=None, used_angles=[])
            journal = question_review.ReviewJournal(self.services.account_id,self.services.data_root,
                course.plan_id,module_id,{pid:scope})
            # Atomic journal reads do not acquire the long background-generation
            # mutex: already-reviewed questions must be available immediately.
            legacy = [row for _,row in journal.candidates([pid]) if row['status'] in ('stored','approved')
                and row.get('reviews') and row['reviews'][-1]['verdict']=='approve'
                and not row['reviews'][-1]['structure_issues'] and row.get('verification',{}).get('matched') is True]
        def approved(q):
            if q['id'] in admitted: return True
            keys=('type','stem','options','answer','reference','point_ids','chunk_ids','angle')
            return any(all(row['question'].get(k)==q.get(k) for k in keys) for row in legacy)
        return [dict(q) for q in candidates if approved(q)]

    def _probe_review_file(self, course, module_id):
        import hashlib
        digest=hashlib.sha256(json.dumps([course.plan_id,module_id]).encode()).hexdigest()[:24]
        return AccountJSON(self.services.account_id,self.services.data_root,f'probe-{digest}.json')

    def _remember_probe_review(self, course, module_id, identifiers):
        from ..course import question_review
        file=self._probe_review_file(course,module_id)
        with file.lock:
            row=file.read({})
            fingerprint=self._test_fingerprint(course,module_id)
            previous=row.get('question_ids',[]) if row.get('version')==question_review.VERSION and row.get('fingerprint')==fingerprint else []
            file.write(dict(version=question_review.VERSION,fingerprint=fingerprint,question_ids=sorted(set(previous)|set(identifiers))))

    def _probe_question(self, course, ctx, pid, original=None):
        pool=self._probe_pool(course,pid,original)
        error='没有未用且已核对的单点题。'
        if not pool:
            module_id=course.point(pid)['module_id']
            _, used=self._bank_history(course,module_id)
            excluded=set(used.get(pid,()))
            if original:
                excluded.add('recall')
                if original.get('angle'): excluded.add(original['angle'])
            try:
                # Exactly the bank's bounded generation -> structure -> blind
                # solve -> review pipeline, including durable interruption resume.
                self._bank_fill(course,ctx,module_id,point_ids=[pid],needs={pid:1},used_angles={pid:sorted(excluded)},
                                avoid=[original['stem']] if original else ())
            except ModelCancelled:
                raise
            except (ModelError,AgentFormatError) as exc:
                if getattr(exc,'code','')=='QUESTION_MATERIAL_CHANGED': raise
                error=getattr(exc,'message',str(exc))
            pool=self._probe_pool(course,pid,original)
        if not pool: return None,error
        preferred=[q for q in pool if q['type']=='choice'] or pool
        q=dict(self._rng.choice(preferred))
        if q['type']=='choice':
            self._rng.shuffle(q['options']); q['shuffled']=True
        return q,''

    def _probe_unconfirmed(self, course, state, pending, purpose, reason):
        pending.setdefault('answer_id',self._answer_id(course,state,pending))
        pending['probe_failure']=dict(purpose=purpose,reason=reason)
        self._save(course,state)
        course.store.append_message(course.plan_id,self._channel(state),'system',
            f'{purpose}未确认：暂时没有可用的新题，不新增答错记录。',kind='unconfirmed',
            message_id=uuid.uuid5(uuid.NAMESPACE_URL,purpose+':'+pending['answer_id']).hex)

    def _show_probe(self, course, state):
        pending=state['pending']; q=pending['question']
        self._save(course,state)  # preserve chosen question/answer identity before exposure
        # The existing exposure contract has a practice bucket for standalone
        # probes. Actual answer events still use quick_confirm/casual/etc.
        course.store.mark_bank_asked(course.plan_id,q['id'],'practice')
        course.store.append_message(course.plan_id,self._channel(state),'system',_question_text(q),kind='question',
            message_id=uuid.uuid5(uuid.NAMESPACE_URL,'probe:'+pending['answer_id']).hex)

    def _deepen_question(self, course, state, ctx, pending, verdict=None):
        """Do not record the uncertain answer; grade one deeper probe per point."""
        if pending.get('depth_probe'):
            return self._say(course, state, '这道题还无法可靠判定，请补充说明你的作答。', role='system')
        probes = []
        for pid in pending['point_ids']:
            q,reason=self._probe_question(course,ctx,pid,pending['question'])
            if q is None:
                self._probe_unconfirmed(course,state,pending,'深问',reason)
                # Keep the original explicit binary result when one exists.
                # clarify has none: retain its original question, never invent
                # a wrong/credit event. A subsequent answer can clarify it.
                if verdict and type(verdict.correct) is bool:
                    from dataclasses import replace
                    return replace(verdict,uncertain=False)
                return None
            probes.append(dict(q, depth_probe=True))
        self._say(course, state, '这次答案还无法确定，换一道同知识点的深入应用题，以这道题的结果为准。', role='system')
        kind = pending['kind']
        if kind in ('module_test', 'skip_test', 'retest'):
            test = state['test']
            test['queue'][test['index']:test['index'] + 1] = probes
            self._ask_next(course, state)
        elif kind == 'check':
            check = state['check']
            check['queue'][check['index']:check['index'] + 1] = probes
            self._ask_check(course, state)
        elif kind == 'practice':
            practice = state['practice']
            practice['queue'][practice['index']] = probes[0]
            self._ask_practice(course, state, ctx)
        else:
            q = probes[0]
            state['pending'] = dict(pending, question=q, point_ids=q['point_ids'], answer_id=uuid.uuid4().hex,depth_probe=True)
            self._show_probe(course,state)
        state['pending']['depth_probe'] = True
        exposure=state['pending']['kind']
        course.store.mark_bank_asked(course.plan_id,state['pending']['question']['id'],
                                     exposure if exposure in ('module_test','skip_test','retest','check','practice') else 'practice')
        self._save(course,state)

    def _questions(self, course, ctx, purpose, point_ids, *, avoid=(), used_angles=None, background=False, deepen=False, counts=None):
        points = [course.point(pid) for pid in point_ids]
        excerpts = course.excerpts(point_ids)
        qctx = {"purpose": purpose, "per_point": 2 if purpose == 'bank' else 1,
                "excerpts": excerpts, "avoid": list(avoid)[-100:],
                "points": [{"id": p["id"], "title": p["title"], "summary": p.get("summary", ""),
                            "chunk_ids": [c for c in p["chunk_ids"] if c in excerpts] or p["chunk_ids"][:1]} for p in points]}
        if purpose == "check":
            qctx["used_angles"] = used_angles or {pid: [] for pid in point_ids}
        if purpose == 'bank':
            qctx['counts'] = counts or {pid: 2 for pid in point_ids}
            qctx['used_angles'] = used_angles or {pid: [] for pid in point_ids}
        if deepen:
            qctx['deepen'] = True
        if purpose in ('bank', 'check', 'module_test'):
            # One essay per module (TEACH-04): an extra one would reject the whole batch.
            modules = {p['module_id'] for p in points}
            if any(q['type'] == 'essay' for m in modules for q in self._bank_rows(course, m)):
                qctx['existing_essay'] = True
        request = question_agent.question_request(qctx)
        def parse(text):
            questions = question_agent.parse_questions(text, qctx, partial_bank=purpose == 'bank')
            if deepen and (len(questions) != 1 or questions[0].type != 'choice'):
                raise AgentFormatError('深化题须为一道情境应用单选题。')
            if purpose == 'bank' and len(questions) > 5:
                raise AgentFormatError('题库小批最多五题。')
            return questions
        if purpose == 'bank':
            # One response per small batch, then targeted one-point repair.
            # Reasoning shares the output cap; the fixed acceptance sample
            # truncated at 8192. One response per batch, no cap-doubling retry.
            result = self.services.gateway.complete('questions', request[1], system=request[0],
                cancel=ctx.cancel if ctx else None, plan_id=course.plan_id, max_tokens=16384)
            return parse(result.text)
        return (self._prep_ask(course, "questions", request, parse, 4096) if background else
                self._ask(course, ctx, "questions", request, parse, 4096))

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
        fact = next((r for r in course.store.records('question_answers', course.plan_id) if r['id'] == answer_id), None)
        if fact:
            return judge_agent.Verdict('answer', fact['correct'], tuple(fact['wrong_points']), fact['reason'],
                                       checks=tuple(fact['checks']))
        entity = 'practice_results' if pending.get('kind') == 'practice' else 'judgements'
        row = next((item for item in course.store.records(entity, course.plan_id)
                    if item['id'] == answer_id), None)
        if row is None: return None
        if entity == 'practice_results':
            return judge_agent.Verdict('answer', row['correct'],
                                       tuple(row.get('wrong_points', [] if row['correct'] else row['point_ids'])), row['reason'])
        event = row['event']
        return judge_agent.Verdict('answer', all(result[1] for result in event['results']),
                                   tuple(result[0] for result in event['results'] if not result[1]),
                                   row['details'].get('reason', ''), checks=tuple(row['details'].get('point_checks', ())),
                                   review=row['details'].get('judgement_review'))

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
            "question": q["stem"], "question_id": q.get('id'), "question_angle": q.get('angle'),
            "question_type": q.get('type'),
            "answer": self._information_combined_reply(pending, self._combined_reply(pending, answer))[:4000],
            "reason": verdict.reason,
            "information_followup": {
                "original": pending['information_reply'], "prompt": pending['information_prompt']
            } if pending.get('information_requested') and
                 pending.get('information_answer_id') == pending.get('answer_id') else None,
            "point_checks": list(verdict.checks), "judgement_review": verdict.review,
            "chunk_ids": q.get("chunk_ids", [])},
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
                    facts = profile_agent.build_verified_facts(
                        fresh.points,
                        fresh.store.records("judgements", plan_id),
                        fresh.store.records("question_answers", plan_id),
                        fresh.store.records("question_bank", plan_id),
                    )
                    plan, module = self._skeleton(fresh, {"module_id": module_id}, [])
                    profile = (fresh.store.records("teacher_profile", plan_id) or [{}])[0].get("document", {}) or {}
                    # Points of a module passed by a direct test were never taught (BUG-44).
                    skipped = {p["id"] for m in fresh.modules if book.module(m["id"]).state == "skipped"
                               for p in fresh.points_of(m["id"])}
                    taught = len(set(fresh.taught()) - skipped)
                    progress = f"已讲 {taught} 个知识点" + (f"，另有 {len(skipped)} 个点所在模块直接考通过、没有讲" if skipped else "")
                    pctx = {"prefs": self._prefs(), "plan": plan, "module": module, "mode": current_mode,
                            "dialogue": self._dialogue(fresh, {"module_id": module_id}),
                            "profile": (profile.get("text") or "")[:6000], "known_point_ids": all_points,
                            "weak_point_ids": weak,
                            "verified_facts": facts,
                            "progress": f"{progress}（全计划共 {len(all_points)} 个），当前模块《{module['title']}》。"}
                    page = getattr(self, "_profile_request_page", None)
                    if page is None:
                        self._profile_request_page = page = AppendOnlyPage()
                    pctx["request_page"] = page
                    request = profile_agent.profile_request(pctx)
                    def parse_and_keep(text):
                        result = profile_agent.parse_profile(text, pctx)
                        page.accept(request[0], request[1], teacher_agent._dialogue(pctx["dialogue"]), text)
                        return result
                    result = self._ask(fresh, None, "profile", request, parse_and_keep, 2048)
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
    result = {"id": q.id, "type": q.type, "stem": q.stem, "options": list(q.options), "answer": q.answer,
            "reference": q.reference, "point_ids": list(q.point_ids), "chunk_ids": list(q.chunk_ids), "angle": q.angle,
            "required_points": list(q.required_points) if q.required_points is not None else None}
    if q.type == 'exact':
        result.update(answer_kind=q.answer_kind, accepted=list(q.accepted))
        if q.answer_kind == 'number': result['tolerance'] = q.tolerance
    if q.type == 'essay': result['rubric'] = list(q.rubric)
    return result


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
            rooms[key].lesson_event = lambda data: bridge.push("classroom.lesson_ready", data)
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
        "classroom.choose": lambda p, c: room().run(p.get("plan_id"), lambda co, st, cx: room().choose(co, st, cx, p.get("option")), c),
        "classroom.back": lambda p, c: room().run(p.get("plan_id"), room().back, c),
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
