"""New-plan orchestration for the new window (ARCH-06, PLAN-01/02/06/07).

The host owns every state change: agents only return text/JSON that is checked
against what this module sent them (planner_agents). The creation conversation
is a per-plan draft file on this computer (plan-drafts/<plan_id>.json); the plan
itself, adopted materials, outline and generated points live in CourseStore and
sync like all course data. Pages learn about progress from two pushed events:
plan_draft (full public draft) and plan_status (one short line while busy).

Phases: form -> questions -> sources -> outline -> generating -> ready.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import json
import logging
import math
import sqlite3
from statistics import median
import re
import threading
import uuid

from ..connections.errors import MODEL_OUTPUT_TRUNCATED, ModelCancelled, ModelError
from ..course import plan_intent
from ..course import planner_agents as agents
from ..course.request_layout import dialogue_tail
from ..course import source_quality
from ..course.ledger import LedgerError, PlanWindow, check_cap, distribute, estimate_check_minutes, full_day_minutes
from ..course.store import StoreError, POINT_MINUTES_VERSION
from ..preferences.storage import AccountJSON, StorageError
from .bridge import BridgeError, CallContext

log = logging.getLogger(__name__)

MAX_CARDS_PER_ROUND = 6
MAX_GENERATE_BATCHES = 15
CHECK_MINUTES_PER_QUESTION = 1.5
GENERATE_WORKERS = 4
TRANSIENT = frozenset({"MODEL_RESPONSE_INVALID", "MODEL_CONNECTION_FAILED", "MODEL_TIMEOUT"})
_PLAN_ID = re.compile(r"[a-f0-9]{32}")
_URL = re.compile(r"https?://[^\s，。；、）)>\]】\"'<]+")


def find_links(text):
    return list(dict.fromkeys(u.rstrip(".,;:!?") for u in _URL.findall(text or "")))[:10]
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
LEVELS = tuple(agents.LEVELS)
LEVEL_NAMES = dict(agents.LEVELS)


def _new_id():
    return uuid.uuid4().hex


def _msg(role, kind, text="", **data):
    return {"id": _new_id()[:12], "role": role, "kind": kind, "text": text, **data}


def _day(value, name):
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        raise BridgeError("FORM_INVALID", f"{name}格式不对。")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BridgeError("FORM_INVALID", f"{name}不是有效日期。") from None


def validate_form(raw, today: date) -> tuple[dict, PlanWindow]:
    """Required fields are checked here, never by a model (ARCH-06 step 2)."""
    if not isinstance(raw, dict):
        raise BridgeError("FORM_INVALID", "表单内容无效。")
    def text(key, label, limit, required=True):
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value) > limit:
            raise BridgeError("FORM_INVALID", f"{label}太长或格式不对。")
        value = value.strip()
        if required and not value:
            raise BridgeError("FORM_MISSING", f"请填写{label}。")
        return value
    form = {"goal": text("goal", "学习目标", 300), "final_goal": text("final_goal", "最终要达到的程度", 300),
            "level_note": text("level_note", "水平补充说明", 300, False), "notes": text("notes", "补充说明", 1000, False)}
    if raw.get("level") not in LEVELS:
        raise BridgeError("FORM_MISSING", "请选择当前水平。")
    form["level"] = raw["level"]
    start, end = _day(raw.get("start"), "开始日期"), _day(raw.get("end"), "结束日期")
    if start < today:
        raise BridgeError("FORM_INVALID", "开始日期不能早于今天。")
    if end < start:
        raise BridgeError("FORM_INVALID", "结束日期不能早于开始日期。")
    if (end - start).days > 3 * 366:
        raise BridgeError("FORM_INVALID", "计划最长三年，请缩短日期范围。")
    cap = raw.get("daily_cap")
    if type(cap) is not int or cap < 10:
        raise BridgeError("FORM_INVALID", "每天最多学多久请填至少 10 分钟的整数。")
    weights = raw.get("weekday_weights")
    if (not isinstance(weights, list) or len(weights) != 7
            or any(w not in (0, 0.5, 1) or type(w) is bool for w in weights)):
        raise BridgeError("FORM_INVALID", "每周可学的日子格式不对。")
    special = raw.get("special_days", [])
    if not isinstance(special, list) or len(special) > 120:
        raise BridgeError("FORM_INVALID", "特殊日期太多或格式不对。")
    dates = {}
    for item in special:
        if not isinstance(item, dict) or item.get("weight") not in (0, 0.5):
            raise BridgeError("FORM_INVALID", "特殊日期格式不对。")
        day = _day(item.get("date"), "特殊日期")
        if not start <= day <= end:
            raise BridgeError("FORM_INVALID", f"特殊日期 {day.isoformat()} 不在计划日期内。")
        dates[day] = float(item["weight"])
    form.update(start=start.isoformat(), end=end.isoformat(), daily_cap=cap,
                weekday_weights=[float(w) for w in weights],
                special_days=[{"date": d.isoformat(), "weight": w} for d, w in sorted(dates.items())],
                only_mine=bool(raw.get("only_mine", False)))
    try:
        window = PlanWindow(start, end, cap, tuple(form["weekday_weights"]), dates)
        if not distribute(window, 1):
            raise LedgerError("NO_DAYS", "")
    except LedgerError:
        raise BridgeError("FORM_INVALID", "在这段日期里没有可以学习的日子，请调整每周可学的日子或特殊日期。") from None
    return form, window


def window_note(window: PlanWindow) -> str:
    days = [d for d, _ in distribute(window, 10000).items()]
    return (f"{window.start.isoformat()} 至 {window.end.isoformat()}，共 {len(days)} 个可学的日子，"
            f"每天最多 {window.daily_cap_minutes} 分钟")


MINUTES_PER_POINT_GUESS = 8  # Duration estimate only; point granularity stays unchanged.
# 2026-10-06: small57 4481 chars/10 points; find59b 123846/163
# (its ML module has 162 points); fix54 computer 2434/8, math 43743/26
# selected-section characters. Rounded outward; rough estimates, not a point quota.
CHARS_PER_POINT_RANGE = (300, 1700)
# 2026-10-06: same four samples above plus exp64 JavaGuide AI core
# 28813 chars/62 points. Use the median of all five for the usual case;
# retain the outward bounds only as uncertainty, not adjustment targets.
CHARS_PER_POINT_TYPICAL = median((4481/10, 123846/163, 2434/8, 43743/26, 28813/62))


def with_checks(learning_minutes: int, question_count=0) -> int:
    """Question reserves use material question counts, never inferred point counts."""
    return learning_minutes + question_count * 2 + estimate_check_minutes(question_count, minutes_per_question=2)


def minute_ranges(window: PlanWindow, low: int, high: int) -> dict:
    """Totals include module tests and the final check; daily figures are for a full study day."""
    lo_total, hi_total = with_checks(low), with_checks(high)
    lo_day = full_day_minutes(distribute(window, lo_total), window)
    hi_day = full_day_minutes(distribute(window, hi_total), window)
    cap = check_cap(window, hi_total)
    return {"total": [lo_total, hi_total], "daily": [lo_day, hi_day], "cap": window.daily_cap_minutes,
            "over_cap": hi_day > window.daily_cap_minutes,
            "suggested_end": cap.suggested_end.isoformat() if cap.suggested_end else None}


def short_plan_name(goal):
    """Use the form's subject clause, leaving the complete goal in details."""
    text = re.sub(r"[（(].*?[）)]", "", goal or "")
    text = re.sub(r"^(?:我想要?|希望|想要|计划|学习|学会)\s*", "", text.strip())
    text = re.split(r"[。；;，,]|并且|并能|以便|目标是|达到", text, maxsplit=1)[0].strip()
    return (text or "学习计划")[:20].rstrip(" ：:、与和")


def map_ranges(window, point_count, *, module_counts=None, question_counts=(), learning_minutes=None):
    learning = point_count * MINUTES_PER_POINT_GUESS if learning_minutes is None else learning_minutes
    ranges = minute_ranges(window, learning, learning)
    if module_counts is not None or question_counts:
        # Generation persists each module's integer reserve separately.
        # Round at that same boundary, rather than once for the whole map.
        total = learning + sum(question_counts)*2 + sum(estimate_check_minutes(n,minutes_per_question=2) for n in question_counts)
        daily = full_day_minutes(distribute(window, total), window)
        cap = check_cap(window, total)
        ranges.update(total=[total,total],daily=[daily,daily],over_cap=daily>window.daily_cap_minutes,
                      suggested_end=cap.suggested_end.isoformat() if cap.suggested_end else None)
    suggested = check_cap(window, ranges["total"][1])
    end = suggested.suggested_end
    ranges.update(points=point_count, learning=learning,
                  checks_pending=not bool(question_counts),
                  suggested_cap=math.ceil(ranges["daily"][1] / 10) * 10,
                  suggested_end=end.isoformat() if end else ranges["suggested_end"],
                  suggested_days=(end - window.start).days + 1 if end else None)
    return ranges


class PlanFlow:
    _material_guard = threading.Lock()

    def __init__(self, services, push, study_day, *, fetcher=None, prepare_module=None, stars=None):
        self.services, self.push, self.study_day = services, push, study_day
        self.fetcher = fetcher  # injected by offline tests; None = real guarded web fetch
        self._stars = stars  # Optional metadata transport can be replaced in offline tests.
        from ..course import github_repo
        self.repo_fetch = github_repo.default_fetch  # replaced by offline tests
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._call_context = threading.local()
        self._status: dict[str, str] = {}
        self._additions: dict[str, dict] = {}   # PLAN-10 sessions: the host holds state, the page only picks
        self._picked: dict[str, list] = {}      # files chosen in the dialog, by token
        self._prepare_module = prepare_module  # MF-04 contract; injected by offline tests
        # Plan creation and classroom share account-local jobs. No model call
        # holds this lock; at most one worker per plan writes/prepares at once.
        with self._material_guard:
            self._material_jobs = getattr(services, '_material_jobs', None)
            if self._material_jobs is None:
                self._material_jobs = dict(guard=threading.Lock(), jobs={}, active=set(), queued={}, execution_locks={})
            if services is not None:  # Pure prompt helpers also construct a flow without services.
                services._material_jobs = self._material_jobs
                services._material_flow = self
            self._point_link_jobs = getattr(services, '_point_link_jobs', None)
            if self._point_link_jobs is None:
                self._point_link_jobs = dict(guard=threading.Lock(), jobs={})
            if services is not None:
                services._point_link_jobs = self._point_link_jobs
            self._point_minute_jobs = getattr(services, '_point_minute_jobs', None)
            if self._point_minute_jobs is None:
                self._point_minute_jobs = dict(guard=threading.Lock(), jobs={}, statuses={}, epoch=0)
                store = getattr(services, 'store', None)
                if store is not None and hasattr(store, 'subscribe'):
                    shared = self._point_minute_jobs
                    def invalidate_minutes(origin, entities):
                        if not entities.intersection({'plans', 'points', 'modules', 'source_chunks', 'question_bank', 'credits'}): return
                        with shared['guard']:
                            shared['epoch'] += 1
                            shared['statuses'].clear()
                            for job in shared['jobs'].values():
                                if job.get('writer') == threading.get_ident():
                                    job['write_epoch'] = shared['epoch']
                    store.subscribe(invalidate_minutes)
            if services is not None:
                services._point_minute_jobs = self._point_minute_jobs
        self._preparing = self._material_jobs['active']

    # ----------------------------------------------------------- drafts
    def _file(self, plan_id):
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        # Intake drafts include full verified material, unlike small settings.
        return AccountJSON(self.services.account_id, self.services.data_root,
                           f"plan-drafts/{plan_id}.json", max_bytes=64*1024*1024)

    def load(self, plan_id):
        try:
            draft = self._file(plan_id).read({})
        except StorageError:
            raise BridgeError("DRAFT_UNREADABLE", "这份计划的创建记录读不出来了，可以删除后重新新建。") from None
        if draft.get("plan_id") != plan_id:
            raise BridgeError("DRAFT_NOT_FOUND", "找不到这份计划的创建记录。")
        return draft

    def save(self, draft):
        draft.pop('_verified_sources', None)
        try:
            self._file(draft["plan_id"]).write(draft)
        except StorageError:
            raise BridgeError("DRAFT_SAVE_FAILED", "创建记录保存失败，请重试。") from None
        self.push("plan_draft", self.public(draft))

    def public(self, draft):
        view = {k:v for k,v in draft.items() if k != '_verified_sources'}
        view['busy'] = self._status.get(draft["plan_id"])
        if draft.get('material_structure') and draft.get('form'):
            try:
                modules, ranges = self.structure_ranges(draft)
            except sqlite3.ProgrammingError as exc:
                # A late preparation notification can arrive after account
                # shutdown. It must not reopen the account DB to estimate.
                if 'closed database' not in str(exc):
                    raise
            else:
                view['material_structure'] = dict(draft['material_structure'], modules=modules, ranges=ranges)
        if draft.get("outline"):
            view["outline"] = {k: v for k, v in draft["outline"].items() if k != "agent"}
            view["outline"]["stages"] = [dict(s, modules=[dict(
                {k: v for k, v in m.items() if k not in ("spans", "points")},
                points=[dict(p) for p in m['points']]) for m in s["modules"]])
                                         for s in draft["outline"]["stages"]]
            if draft['outline'].get('grounded') and draft.get('form'):
                # Display content totals, including preparation's actual reserves.
                modules = agents.outline_modules(view['outline'])
                try:
                    saved = {m['id']: m.get('question_count')
                             for m in self.services.store.records('modules', draft['plan_id'])}
                except sqlite3.ProgrammingError as exc:
                    if 'closed database' not in str(exc):
                        raise
                    # Late background notifications must not reopen an account.
                    return view  # Keep the last preview; don't query the closed account again.
                known=[saved.get(m.get('id')) for m in modules]
                view['outline']['ranges'] = self._content_ranges(draft, modules,
                    question_counts=[n or 0 for n in known] if saved else ())
                view['outline']['ranges']['checks_pending']=any(n is None for n in known)
        return view

    def status(self, plan_id, text):
        if text:
            self._status[plan_id] = text
        else:
            self._status.pop(plan_id, None)
        self.push("plan_status", {"plan_id": plan_id, "text": text})

    def _lock(self, plan_id):
        with self._guard:
            return self._locks.setdefault(plan_id, threading.Lock())

    def run(self, plan_id, work, ctx: CallContext):
        """One operation per plan at a time; model errors come back as a chat line."""
        lock = self._lock(plan_id)
        if not lock.acquire(blocking=False):
            raise BridgeError("PLAN_BUSY", "上一步还在进行，请稍等。")
        self._call_context.cancel = ctx.cancel
        try:
            draft = self.load(plan_id)
            try:
                work(draft, ctx)
            except ModelCancelled:
                raise BridgeError("CANCELLED", "已停止。") from None
            except ModelError as exc:
                draft["messages"].append(_msg("ai", "error", f"{exc.message} 可以稍后重试。"))
                self.save(draft)
        finally:
            del self._call_context.cancel
            self.status(plan_id, "")
            lock.release()
        return {"draft": self.public(draft)}

    def drafts(self):
        root = self._file(_new_id()).path.parent
        items = []
        for path in sorted(root.glob("*.json")) if root.is_dir() else []:
            if _PLAN_ID.fullmatch(path.stem):
                try:
                    draft = self.load(path.stem)
                except BridgeError:
                    continue
                items.append({"plan_id": draft["plan_id"], "phase": draft["phase"],
                              "goal": (draft.get("form") or {}).get("goal", ""), "updated": path.stat().st_mtime})
        return sorted(items, key=lambda d: d["updated"], reverse=True)

    def current(self):
        """Reopen the newest unsubmitted draft instead of piling up empty ones."""
        for item in self.drafts():
            if item["phase"] == "form":
                return self.load(item["plan_id"])
        draft = {"version": 1, "plan_id": _new_id(), "phase": "form", "form": None, "goal": "", "focus": [],
                 "messages": [_msg("ai", "form", "你好！先告诉我你想学什么。填好表后我们聊清需求，先看大致模块，确认后再找资料。")],
                 "sources": [], "research": {"round": 0, "rejected": []}, "outline": None, "generation": {}}
        self.save(draft)
        return draft

    def discard(self, plan_id):
        lock = self._lock(plan_id)
        if not lock.acquire(blocking=False):
            raise BridgeError("PLAN_BUSY", "这份计划正在处理，请稍后再删除。")
        try:
            with self.services.store.transaction():
                try:
                    plan = self.services.store.get_plan(plan_id)
                except StoreError as exc:
                    if exc.code != "NOT_FOUND": raise
                else:
                    if plan['state'] not in ('creating', 'ready'):
                        raise BridgeError('PLAN_STARTED', '计划已经开始，不能放弃草稿或删除计划。')
                    self.services.store.delete_plan(plan_id)
            self._cleanup_plan_files(plan_id)
            return {"deleted": True}
        finally:
            lock.release()

    def delete_started(self, plan_id):
        """Explicit two-click deletion from My Plans; never used to discard a draft."""
        lock = self._lock(plan_id)
        if not lock.acquire(blocking=False):
            raise BridgeError('PLAN_BUSY', '这份计划正在处理，请稍后再删除。')
        try:
            self.services.store.delete_plan(plan_id)
            self._cleanup_plan_files(plan_id)
            return {'deleted': True}
        finally:
            lock.release()

    def _cleanup_plan_files(self, plan_id):
        self._file(plan_id).path.unlink(missing_ok=True)
        AccountJSON(self.services.account_id, self.services.data_root, f"classroom/{plan_id}.json").path.unlink(missing_ok=True)

    # ----------------------------------------------------------- model calls
    def _ask(self, purpose, request, parse, ctx, *, plan_id, search=False, max_tokens=4096):
        system, messages = request[0], request[1]
        messages = list(messages)
        last = None
        transport_retried = format_retried = False
        for attempt in range(3):  # one provider retry and one format repair, both bounded
            try:
                result = self.services.gateway.complete(purpose, messages, system=system, enable_search=search,
                                                        cancel=ctx.cancel, plan_id=plan_id, max_tokens=max_tokens,
                                                        **({'on_text': lambda _text: None} if purpose == 'outline' else {}))
            except ModelError as exc:
                if (exc.code == MODEL_OUTPUT_TRUNCATED and not transport_retried and max_tokens < 32768
                        and not ctx.cancel.is_set()):
                    # A long outline can outgrow its cap; the same request with room to finish.
                    log.info("agent %s hit the output cap %s, retrying with double", purpose, max_tokens)
                    max_tokens, last = min(32768, max_tokens * 2), exc
                    transport_retried = True
                    continue
                if transport_retried or exc.code not in TRANSIENT or ctx.cancel.is_set():
                    raise
                log.info("agent %s transient error %s, retrying once", purpose, exc.code)
                last = exc
                transport_retried = True
                continue
            try:
                return parse(result.text), result
            except agents.AgentFormatError as exc:
                last = exc
                log.info("agent %s returned an invalid format (%s), attempt %s", purpose, exc, attempt + 1)
                if format_retried:
                    break
                format_retried = True
                if not ctx.cancel.is_set():
                    detail = (' plan_intent 的 reply 必须在根对象；chat 没有参数，不能把 reply 放进 params。'
                              if purpose == 'plan_intent' else '')
                    reason = str(exc)
                    if exc.__cause__:
                        reason += '：' + str(exc.__cause__)
                    messages = messages + [{'role': 'assistant', 'content': result.text},
                        {'role': 'user', 'content': '上一条格式校验失败：' + reason + '。' + detail +
                         '请依据原输出结构修正一次，返回完整合法JSON，检查括号、字段层级及引用；不执行任何动作。'}]
        raise ModelError("AGENT_FORMAT", "没听懂，换个说法。" if purpose == 'plan_intent' else
                         "AI 的回答格式不对，已重试一次仍不行。") from last

    # ----------------------------------------------------------- 1 form + sufficiency
    def submit_form(self, draft, ctx, raw):
        if draft["phase"] not in ("form", "questions"):
            raise BridgeError("PHASE_INVALID", "表单已经提交过了。")
        today = self.study_day()
        form, window = validate_form(raw, today)
        store, plan_id = self.services.store, draft["plan_id"]
        try:
            store.get_plan(plan_id)
            store.save_intake(plan_id, goal=form["goal"], final_goal=form["final_goal"], level=form["level"],
                              notes=form["notes"], window=window, today=today)
        except StoreError as exc:
            if exc.code != "NOT_FOUND":
                raise
            store.create_plan(plan_id, goal=form["goal"], final_goal=form["final_goal"], level=form["level"],
                              notes=form["notes"], window=window,
                              teacher_preset_id=(self.services.settings.load().get('default_teacher_preset_id', 'teacher-1')
                                                 if getattr(self.services, 'settings', None) is not None else 'teacher-1'))
        draft["form"] = form
        draft["name"] = short_plan_name(form["goal"])
        store.rename_plan(plan_id, draft["name"])
        draft["messages"].append(_msg("user", "form_summary", agents.form_brief(form)))
        draft["phase"] = "questions"
        self.save(draft)
        self._check_sufficiency(draft, ctx)

    def _conversation(self, draft):
        return [{"role": m["role"], "text": m["text"]} for m in draft["messages"]
                if m["kind"] in ("questions", "user_text", "preview")]

    def _effective_form(self, draft):
        """Carry every accepted chat requirement into research/coverage/map requests."""
        form = dict(draft["form"])
        requirements = draft.get("requirements", [])
        if requirements:
            form["notes"] = "\n".join([form.get("notes", ""), "【对话补充，冲突时以最新要求为准】", *requirements])
        if draft.get("accepted_preview"):
            form["notes"] += "\n【认可的需求摘要】\n" + draft['accepted_preview']['summary']
            form["notes"] += "\n【认可的大致模块，正式地图仍以资料为依据】\n" + "\n".join(
                f"{m['title']}：{m['summary']}" for m in draft["accepted_preview"]["modules"])
        return form

    def _show_preview(self, draft, ctx, *, confirmed=False):
        # Invalidate derived work before calling the model: cancellation/failure
        # must not leave an obsolete map available for generation.
        draft["phase"] = "questions"
        draft["outline"] = None
        draft.pop("preview", None)
        draft.pop("accepted_preview", None)
        draft.pop("estimate", None)
        draft.pop("coverage", None)
        draft.pop('material_structure', None)
        self.save(draft)
        self.status(draft["plan_id"], "正在整理需求摘要和大致模块…")
        preview, _ = self._ask("sufficiency", agents.preview_request(self._effective_form(draft), self._conversation(draft)),
                               agents.parse_preview, ctx, plan_id=draft["plan_id"])
        preview["version"] = draft.get("preview_version", 0) + 1
        draft["preview_version"] = preview["version"]
        draft["preview"] = preview
        draft["phase"] = "preview"
        text = preview["summary"] + "\n\n" + "\n".join(f"{m['title']}｜{m['summary']}" for m in preview["modules"])
        text += ("\n\n已按你这条消息的确认采用新版大致模块，接下来找资料。" if confirmed else
                 "\n\n这样可以吗？确认后我再找资料；想改哪里也可以继续说。")
        draft["messages"].append(_msg("ai", "preview", text, version=preview["version"]))
        self.save(draft)

    def _check_sufficiency(self, draft, ctx):
        self.status(draft["plan_id"], "正在核对信息是否足够…")
        verdict, _ = self._ask("sufficiency", agents.sufficiency_request(self._effective_form(draft), self._conversation(draft)),
                               agents.parse_sufficiency, ctx, plan_id=draft["plan_id"])
        draft["goal"], draft["focus"] = verdict["goal"], verdict["focus"]
        if not verdict["enough"]:
            text = "\n".join(filter(None, [verdict["message"], *verdict["questions"]]))
            draft["messages"].append(_msg("ai", "questions", text, options=verdict["options"]))
            draft["phase"] = "questions"
            self.save(draft)
            return
        self._show_preview(draft, ctx)

    def _confirm_preview(self, draft, ctx):
        if draft["phase"] != "preview" or not draft.get("preview"):
            raise BridgeError("PHASE_INVALID", "请先确认当前的大致模块。")
        draft["accepted_preview"] = draft["preview"]
        self.services.store.save_intake(draft['plan_id'], notes=self._effective_form(draft)['notes'],
                                        today=self.study_day(), reason='确认需求摘要和大致模块')
        draft["phase"] = "sources"
        self.save(draft)
        links = [] if draft.get('initial_links_imported') else find_links(" ".join(
            [draft["form"].get("notes", ""), draft["form"].get("goal", "")] +
            [m["text"] for m in draft["messages"] if m["kind"] == "user_text"]))
        if links:
            self._import_links(draft, ctx, links)
        draft['initial_links_imported'] = True
        if draft["form"].get("only_mine"):
            has_own = any(s["status"] == "adopted" for s in draft["sources"])
            draft["messages"].append(_msg("ai", "text", "你说只用自己给的资料，我就不另外搜索了。" +
                                          ("可以直接生成路线轮廓。" if has_own else "请点下面的“上传资料”，传好后生成路线轮廓。")))
            self.save(draft)
            return
        self._say(draft, '你有自己信任的教材、课程、仓库或学习清单吗？可以上传或贴网址，也可以让我推荐。看中推荐资料后勾选“认可并采用”，即可读取正文。')
        self._recommend(draft, ctx)

    # ----------------------------------------------------------- 2 research
    def _recommend(self, draft, ctx, instruction=""):
        self.status(draft['plan_id'], '正在推荐完整资料链接…')
        rows, _ = self._ask('research', agents.recommendation_request(
            draft['goal'], self._effective_form(draft), draft.get('accepted_preview') or draft.get('preview'),
            self._lists(), instruction=instruction, previous=draft.get('recommendations', [])),
            agents.parse_recommendations, ctx, plan_id=draft['plan_id'], max_tokens=12288)
        cards = []
        for row in rows:
            if ctx.cancel.is_set(): raise ModelCancelled()
            evidence = {}
            if self.fetcher is not None:
                # Offline adapters replace HTTP only, not extraction or scoring.
                try:
                    response = self.fetcher(row['url'], cancel=ctx.cancel, timeout=15)
                    ok, final, reason = True, response.final_url, ''
                    body = response.body[:16384].decode('utf-8', errors='ignore').lower()
                    evidence['prefix'] = body
                    if 'this book has been removed' in body:
                        ok, reason = False, '资料已下架'
                except Exception as exc:
                    ok, final, reason = False, row['url'], type(exc).__name__
            else:
                ok, final, reason = self._link_available(row['url'], ctx.cancel, evidence=evidence)
            if ok:
                from ..course.source_filter import recommendation_rejection
                reason = recommendation_rejection(final, lists=self._lists(), allow_video=True)
                ok = not reason
            if not ok:
                log.info('recommended link unavailable: %s (%s)', row['url'], reason)
                continue
            cards.append(dict(row, final_url=final, card_id=_new_id()[:12], _page_prefix=evidence.get('prefix', '')))
        from ..course.research_pipeline import GithubStars, prepare_recommendations
        from ..course.research import ResearchCancelled
        if self._stars is None and self.fetcher is None:
            self._stars = GithubStars(self.services.account_id, self.services.data_root)
        try:
            cards = prepare_recommendations(cards, lists=self._lists(), stars=self._stars,
                                            cancel=ctx.cancel, previous=draft.get('recommendations', []))
        except ResearchCancelled:
            raise ModelCancelled() from None
        if ctx.cancel.is_set(): raise ModelCancelled()
        from ..course.source_filter import classify_source
        for card in cards:
            identity = classify_source(card.get('final_url') or card['url'], lists=self._lists())
            card.update(kind='url', site=card['url'], tier='recommendation', summary=card.get('why',''),
                note=card.get('why',''), status='new', chars=0, user_provided=False, relevance=None,
                credibility={1:5,2:4}.get(identity.get('level'),3),
                quality=dict(source_type=identity.get('type'), covered_modules=card.get('modules',[])))
        draft.setdefault('recommendations', []).extend(cards)
        draft['messages'].append(_msg('ai', 'recommendations',
            '勾选“认可并采用”后会读取这份资料的正文；也可以直接贴网址或 GitHub 仓库。推荐时还没有读取正文。'
            if cards else '这次没有找到更多可用文字资料：候选链接暂时打不开，或只有视频。可以上传自己的资料，或让我换一批推荐。',
            cards=[c['card_id'] for c in cards]))
        self.save(draft)

    def _link_available(self, url, cancel, *, evidence=None):
        # Existing httpx transport only; no extraction, scoring or body import.
        from ..course.research import ResearchService
        try:
            if cancel.is_set(): raise ModelCancelled()
            with ResearchService()._client() as client:
                with client.stream('GET', url, follow_redirects=True, timeout=15,
                                   headers={'Range': 'bytes=0-16383'}) as response:
                    from ..course.source_filter import recommendation_rejection
                    rejected = recommendation_rejection(str(response.url), lists=self._lists(), allow_video=True)
                    if rejected:
                        return False, str(response.url), rejected
                    if response.status_code not in (200, 206):
                        return False, str(response.url), f'HTTP {response.status_code}'
                    prefix = b''
                    if 'html' in response.headers.get('content-type', ''):
                        for part in response.iter_bytes(chunk_size=4096):
                            if cancel.is_set(): raise ModelCancelled()
                            prefix += part
                            if len(prefix) >= 16384: break
                    if evidence is not None:
                        evidence['prefix'] = prefix.decode('utf-8', errors='ignore')
                    text = prefix.decode('utf-8', errors='ignore').lower()
                    if any(v in text for v in ('this book has been removed', 'this page has been removed',
                            '<title>404', '<title>page not found', '该资源已下架')):
                        return False, str(response.url), '资料已下架或不存在'
                    return True, str(response.url), ''
        except ModelCancelled: raise
        except Exception as exc:
            return False, url, type(exc).__name__

    def _import_links(self, draft, ctx, urls):
        self.status(draft['plan_id'], '正在读取你提供的资料…')
        outcome = self._read_links(draft, ctx, urls)
        if outcome['cancelled']: raise ModelCancelled()
        cards = []
        stored = {s['id']: s for s in self.services.store.records('sources', draft['plan_id'])}
        for result in outcome['results']:
            if result['status'] in ('imported', 'existing'):
                source = stored[result['source_id']]
                existing = next((c for c in draft['sources'] if c.get('source_id') == source['id']), None)
                if existing:
                    existing['status'] = 'adopted'
                else:
                    cards.append(dict(card_id=_new_id()[:12], kind='url', url=source['url'],
                        title=source['title'], site=source['url'], tier='user', summary='', relevance=None,
                        credibility=None, note='由你提供，正文已读取。', user_provided=True, status='adopted',
                        source_id=source['id'], chars=result['char_count'], sha=source.get('sha256')))
            else:
                draft['messages'].append(_msg('ai', 'error', result['original_url']+'：'+result['message']))
        draft['sources'].extend(cards)
        if any(r['status'] in ('imported', 'existing') for r in outcome['results']):
            self._invalidate_material_structure(draft)
        imported = any(r['status'] in ('imported', 'existing') for r in outcome['results'])
        draft['messages'].append(_msg('ai', 'sources' if imported else 'error',
            '已读取你提供的资料。请按这些资料调整结构。' if imported else '这次资料未能导入，请检查链接，或下载后上传文件。',
            cards=[c['card_id'] for c in cards]))
        self.save(draft)

    def _read_links(self, draft, ctx, urls, *, recommended=False):
        from ..course.addition_links import import_links
        return import_links(self.services.store, draft['plan_id'], urls, fetcher=self.fetcher,
            repo_fetch=self.repo_fetch, lists=self._lists(), cancel=ctx.cancel, creating=True,
            recommended=recommended)

    @staticmethod
    def _decision_cards(draft):
        # Recommendation order remains stable even after a selection is also
        # attached to sources. Imported/user cards follow without duplicates.
        cards, seen = [], set()
        for card in [*draft.get('recommendations',[]), *draft['sources']]:
            if not card.get('card_id'): continue
            if card['card_id'] not in seen:
                cards.append(card); seen.add(card['card_id'])
        return cards

    def _lists(self):
        try:
            return self.services.catalog.load_lists()
        except Exception:  # noqa: BLE001 - the built-in lists stay the fallback
            from ..course.source_filter import load_lists
            return load_lists()

    def more_sources(self, draft, ctx, instruction="", *, echo=True):
        if draft['phase'] not in ('sources', 'material_preview', 'outline'):
            raise BridgeError('PHASE_INVALID', self._source_phase_message(draft))
        if instruction and echo:
            draft['messages'].append(_msg('user', 'user_text', instruction))
        draft['phase'] = 'sources'
        draft['outline'] = None
        draft.pop('estimate', None)
        self._recommend(draft, ctx, instruction)

    def _source_phase_message(self, draft):
        phase = draft['phase']
        if phase == 'preview':
            return '现在还在确认大致模块，确认后再采用或调整资料。'
        if phase in ('form', 'questions'):
            return '现在还在填写或核对学习需求，请先完成需求和大致模块确认。'
        if phase == 'material_preview':
            return '现在还在确认按资料调整的结构，请先确认或修改。'
        if phase == 'ready':
            return '计划已经生成，现在不能再改创建时的资料。'
        return '正在生成计划，请稍等，当前不能再改资料。'

    def decide(self, draft, card_id, adopt, *, ctx=None):
        if draft['phase'] not in ('sources', 'material_preview', 'outline', 'questions'):
            raise BridgeError('PHASE_INVALID', self._source_phase_message(draft))
        card = next((c for c in self._decision_cards(draft) if c['card_id'] == card_id), None)
        if not card:
            raise BridgeError('PARAM_INVALID', '找不到这份资料，请刷新后重试。')
        if not card.get('source_id') and adopt:
            if ctx is None: raise BridgeError('PARAM_INVALID', '请重新选择这份资料。')
            if card.get('is_video'):
                card.update(read_status='failed',read_error='这是视频资料，未取得文字正文，请下载后上传文字资料。',
                            read_code='video_source',status='new')
                self.save(draft)
                return False
            self.status(draft['plan_id'], '正在读取《'+card['title']+'》…')
            card.update(read_status='reading', read_error='')
            self.save(draft)
            try:
                outcome = self._read_links(draft, ctx, [card['url']], recommended=True)
                result = outcome['results'][0]
                if result['status']=='cancelled': raise ModelCancelled()
                if result['status'] not in ('imported','existing'):
                    card.update(read_status='failed', read_error=result['message']+' 请下载后上传。',
                                read_code=result['code'], status='new')
                    self.save(draft)
                    return False
                source = next(s for s in self.services.store.records('sources',draft['plan_id']) if s['id']==result['source_id'])
                card.update(source_id=source['id'],chars=result['char_count'],sha=source.get('sha256'),
                            read_status='ready',read_error='',read_seconds=result['elapsed_seconds'])
                if not any(c.get('source_id')==source['id'] for c in draft['sources']):
                    draft['sources'].append(card)
            except ModelCancelled:
                card.update(read_status='cancelled',read_error='已取消读取。',status='new')
                self.save(draft)
                raise
            except Exception:
                card.update(read_status='failed',read_error='正文读取失败，请稍后重试，或下载后上传。',status='new')
                self.save(draft)
                raise
        if not card.get('source_id'):
            card.update(status='rejected',read_status='none',read_error='')
            self.save(draft)
            return True
        self.services.store.set_source_adopted(draft['plan_id'], card['source_id'], adopt)
        for other in [*draft['sources'],*draft.get('recommendations',[])]:
            if other.get('source_id')==card['source_id']:
                other['status']='adopted' if adopt else 'rejected'
        self._invalidate_material_structure(draft)
        self.save(draft)
        return True

    def _invalidate_material_structure(self, draft):
        if (draft.get('material_structure') or {}).get('removed_content'):
            draft['removed_content'] = draft['material_structure']['removed_content']
        draft.pop('material_structure', None)
        draft['outline'] = None
        draft.pop('estimate', None)
        if draft['phase'] in ('material_preview', 'outline'): draft['phase'] = 'sources'

    def add_file(self, plan_id, path):
        from ..course.materials import import_file
        from ..course.extract import ExtractError
        lock = self._lock(plan_id)
        with lock:
            draft = self.load(plan_id)
            if draft["phase"] not in ("form", "questions", "preview", "sources", "material_preview", "outline"):
                raise BridgeError("PHASE_INVALID", "路线已经开始生成，不能再加资料。")
            try:
                self.services.store.get_plan(plan_id)
            except StoreError:
                raise BridgeError("FORM_FIRST", "请先提交表单，再上传资料。") from None
            self.status(plan_id, f"正在读取 {path.name}…")
            try:
                try:
                    saved = import_file(self.services.store, plan_id, path, ocr=False)
                except ExtractError as error:
                    if error.code != 'SCANNED_NO_TEXT':
                        raise
                    saved = import_file(self.services.store, plan_id, path, ocr=True)
            finally:
                self.status(plan_id, "")
            if not any(c.get("source_id") == saved["id"] for c in draft["sources"]):
                quality = source_quality.annotate(self.services.store, plan_id, [saved["id"]], lists=self._lists(), today=self.study_day())[0]
                draft["sources"].append({"card_id": _new_id()[:12], "kind": "file", "url": None,
                                         "title": saved.get("title") or path.name, "site": path.name, "tier": "file",
                                         "summary": "", "relevance": quality["relevance"], "credibility": quality["credibility"],
                                         "note": quality["note"], "quality": quality, "user_provided": True,
                                         "status": "adopted", "source_id": saved["id"], "chars": saved.get("char_count", 0)})
                draft["messages"].append(_msg("ai", "sources", f"已读取你上传的《{saved.get('title') or path.name}》，默认采用。",
                                              cards=[draft["sources"][-1]["card_id"]]))
                self._invalidate_material_structure(draft)
            self.save(draft)

    # ----------------------------------------------------------- 3 outline
    def _adopted(self, draft):
        store, plan_id = self.services.store, draft["plan_id"]
        adopted_ids = [c["source_id"] for c in draft["sources"] if c["status"] == "adopted" and c.get("source_id")]
        sources = {s["id"]: s for s in store.records("sources", plan_id)}
        chosen = [sources[i] for i in adopted_ids if i in sources]
        chosen = [dict(s, summary=(s.get("quality", {}).get("note", "") + " " + s.get("summary", "")).strip()) for s in chosen]
        chunks: dict[str, list] = {}
        for chunk in store.records("source_chunks", plan_id):
            chunks.setdefault(chunk["source_id"], []).append(chunk)
        for items in chunks.values():
            items.sort(key=lambda c: c["position"])
        return chosen, chunks

    def _window(self, draft):
        return PlanWindow(date.fromisoformat(draft["form"]["start"]), date.fromisoformat(draft["form"]["end"]),
                          draft["form"]["daily_cap"], tuple(draft["form"]["weekday_weights"]),
                          {date.fromisoformat(s["date"]): s["weight"] for s in draft["form"]["special_days"]})

    def _sections(self, draft):
        """Canonical sections within learner scope; only the model view is folded.

        Keep the full hierarchy and stable IDs (PLAN-06); request construction
        folds these to <=150 entries and parsing expands aliases back here.
        """
        sources, chunks = self._adopted(draft)
        sections = agents.build_sections(sources, chunks,
            max_per_source=max(40, sum(map(len, chunks.values()))),
            max_chars=max(9000, sum(len(c['text']) for items in chunks.values() for c in items)),
            max_depth=None)
        scope = draft.get("scope") or {}
        if scope.get("include"):
            sections = [s for s in sections if s.id in scope["include"]]
        return sources, [s for s in sections if s.id not in scope.get("exclude", [])]

    def structure_ranges(self, draft):
        """No model: estimate only expanded selected sections, counting each block once per module."""
        from ..connections.pricing import _bounds
        _, sections = self._sections(draft)
        by_id = {s.id: s for s in sections}
        _, chunks = self._adopted(draft)
        texts = {c['id']: c['text'] for rows in chunks.values() for c in rows}
        from ..course.material_questions import candidates, for_module
        question_candidates = candidates([c for rows in chunks.values() for c in rows], self._adopted(draft)[0])
        question_ids = set()
        modules, low_counts, high_counts, usual_counts, token_low, token_high = [], [], [], [], 0, 0
        for module in draft['material_structure']['modules']:
            ids = dict.fromkeys(c for r in module['refs'] if r in by_id for c in by_id[r].chunk_ids)
            chars = sum(len(texts.get(c, '')) for c in ids)
            low = math.ceil(chars / CHARS_PER_POINT_RANGE[1])
            high = math.ceil(chars / CHARS_PER_POINT_RANGE[0])
            usual = math.ceil(chars / CHARS_PER_POINT_TYPICAL)
            module_questions = for_module(question_candidates, chunk_ids=ids)
            question_count = len(module_questions)
            question_ids.update(q['id'] for q in module_questions)
            modules.append(dict(module, estimated_points=[low, high], estimated_point_count=usual, chars=chars,
                                estimated_question_count=question_count, has_material_questions=question_count > 0))
            low_counts.append(low); high_counts.append(high)
            usual_counts.append(usual)
            a, b = _bounds('\n'.join(texts.get(c, '') for c in ids))
            token_low += a; token_high += b
        window = self._window(draft)
        low = map_ranges(window, sum(low_counts), module_counts=low_counts, question_counts=[m['estimated_question_count'] for m in modules])
        high = map_ranges(window, sum(high_counts), module_counts=high_counts, question_counts=[m['estimated_question_count'] for m in modules])
        usual = map_ranges(window, sum(usual_counts), module_counts=usual_counts, question_counts=[m['estimated_question_count'] for m in modules])
        warning = 'none' if not usual['over_cap'] else 'possible' if not low['over_cap'] else 'impossible'
        ranges = dict(usual, typical=usual, warning_level=warning, total=[low['total'][0], high['total'][1]],
            daily=[low['daily'][0], high['daily'][1]], points=[sum(low_counts), sum(high_counts)],
            learning=[low['learning'], high['learning']],
            module_check=[sum(m['estimated_question_count'] for m in modules)*2]*2,
            final_check=[sum(estimate_check_minutes(m['estimated_question_count'],minutes_per_question=2) for m in modules)]*2, input_tokens=[token_low, token_high],
            estimated_question_count=len(question_ids),
            modules_with_questions=sum(m['has_material_questions'] for m in modules))
        return modules, ranges

    def material_structure(self, draft, ctx, instruction="", *, confirmed=False):
        sources, sections = self._sections(draft)
        if not sources or not sections:
            raise BridgeError('NO_SOURCES', '请先上传资料，或贴资料网址、GitHub 仓库。')
        previous = draft.get('material_structure')
        self._invalidate_material_structure(draft)
        self.save(draft)
        self.status(draft['plan_id'], '正在按资料目录调整结构…')
        request = agents.material_structure_request(draft['goal'],
            self._effective_form(draft), draft.get('accepted_preview'), sections,
            previous=previous, instruction=instruction, removed_content=draft.get('removed_content', ()))
        value, _ = self._ask('outline', request,
            lambda text: agents.parse_material_structure(text, sections,
                removed_content=draft.get('removed_content', ())), ctx,
            plan_id=draft['plan_id'], max_tokens=32768)
        from ..course.question_bank import original_text
        deleted_names = {original_text(r['title']).casefold() for r in draft.get('removed_content', ())
                         if r['kind'] == 'module'}
        value['modules'] = [m for m in value['modules']
                            if original_text(m['title']).casefold() not in deleted_names]
        self._remember_removed_content(draft, previous, value, sections)
        # Classroom already reads the plan's original goal/final_goal. Preserve
        # note/chat-only preferences there too, without changing account presets
        # or introducing another persisted preference schema.
        preferences = json.loads(request[1][0]['content'])['teaching_preferences']
        original = draft['form']['goal'] + '\n' + draft['form']['final_goal']
        extra = [p for p in preferences if p not in original]
        final_goal = draft['form']['final_goal']
        if extra: final_goal += '\n讲法偏好：' + '；'.join(extra)
        self.services.store.save_intake(draft['plan_id'], final_goal=final_goal)
        value['version'] = draft.get('material_version', 0) + 1
        draft['material_version'] = value['version']
        draft['material_structure'] = value
        draft['phase'] = 'material_preview'
        text = '已按资料整理出 ' + str(len(value['modules'])) + ' 个模块：' + '；'.join(
            str(n) + '. ' + m['title'] for n, m in enumerate(value['modules'], 1)) + '。'
        if value['missing']:
            text += '\n\n所选资料没讲到：' + '、'.join(value['missing']) + '。可以再传一份资料、删掉这些部分，或就这样继续。'
        if value.get('excluded'):
            text += '\n\n这些内容不是知识，未放进计划：' + '、'.join(r['name'] for r in value['excluded']) + '。需要时可在对话里说“把××也加进来”。'
        draft['messages'].append(_msg('ai', 'material_preview', text, version=value['version']))
        self.save(draft)

    @staticmethod
    def _remember_removed_content(draft, previous, current, sections=()):
        """Keep deletions in the local draft, independently of rebuilt outlines.

        Directory IDs are request-local; persist chapter names and source IDs
        instead, so adding a source cannot change the meaning of a deletion.
        """
        removed = list(draft.get('removed_content', ()))
        if previous:
            for row in previous.get('removed_content', ()):
                if row not in removed: removed.append(row)
            kept = {r for m in current['modules'] for r in m['refs']}
            automatic = {r for row in current.get('excluded', []) for r in row['refs']}
            by_id = {s.id: s for s in sections}
            for module in previous['modules']:
                # A structure revision may renumber model keys; retained
                # chapters, not those temporary keys, identify surviving scope.
                if not kept.intersection(module['refs']) and not set(module['refs']) <= automatic:
                    row = dict(kind='module', title=module['title'])
                    if row not in removed: removed.append(row)
                for ref, title in zip(module['refs'], module['chapters']):
                    if ref in kept or ref in automatic: continue
                    row = dict(kind='chapter', title=title,
                               source_id=by_id[ref].source_id if ref in by_id else None)
                    if row not in removed: removed.append(row)
        draft['removed_content'] = removed
        current['removed_content'] = list(removed)

    def _select_material_modules(self, draft, params):
        structure = draft['material_structure']
        modules = structure['modules']
        included, excluded = set(params['include']), set(params['exclude'])
        # An entirely invalid keep request must not silently delete everything.
        structure['modules'] = [m for n, m in enumerate(modules, 1)
                                if (not included or n in included) and n not in excluded]
        self._remember_removed_content(draft, dict(structure, modules=modules), structure,
                                       self._sections(draft)[1])
        structure['version'] += 1
        draft['material_version'] = structure['version']
        draft.pop('estimate', None)
        text = '现在保留的模块：\n' + '\n'.join(f"{n}. {m['title']}｜{m['summary']}\n章节：" +
            '；'.join(m['chapters']) for n, m in enumerate(structure['modules'], 1))
        if params['ignored']:
            text += '\n以下编号不存在、重复或冲突，已忽略：' + '、'.join(params['ignored']) + '。'
        text += '\n请确认这份结构，确认后再填知识点。'
        draft['messages'].append(_msg('ai', 'material_preview', text, version=structure['version']))
        self.save(draft)

    def resolve_missing(self, draft, ctx, action):
        if draft['phase'] != 'material_preview' or action not in ('add', 'delete', 'continue'):
            raise BridgeError('PHASE_INVALID', '请先看按资料调整的结构。')
        if action == 'add':
            draft['phase'] = 'sources'
            return self._say(draft, '请再上传资料或贴网址，也可以让我推荐完整资料链接。')
        structure = draft['material_structure']
        structure['missing_choice'] = action
        if action == 'delete':
            structure['removed_goals'] = list(structure['missing'])
            structure['missing'] = []
        self.save(draft)
        return self.make_outline(draft, ctx)

    def make_outline(self, draft, ctx, request="", *, echo=True):
        if draft['phase'] == 'sources' or request:
            return self.material_structure(draft, ctx, request)
        if draft['phase'] != 'material_preview':
            raise BridgeError('PHASE_INVALID', '请先确认按资料调整的结构。')
        sources, sections = self._sections(draft)
        if not sources:
            raise BridgeError("NO_SOURCES", "请先采用至少一份资料，路线要从资料里来。")
        if not sections:
            raise BridgeError("NO_SECTIONS", "按现在的范围一章都不剩了，请重新说一下要学哪些章节。")
        structure = draft.get('material_structure')
        if not structure:
            raise BridgeError('STRUCTURE_REQUIRED', '请先按资料调整并确认结构。')
        matched = {r for m in structure['modules'] for r in m['refs']}
        sections = [s for s in sections if s.id in matched]
        window = self._window(draft)
        previous = draft["outline"]["agent"] if (request and draft.get("outline")) else None
        if request and echo:
            draft["messages"].append(_msg("user", "user_text", request))
        chunks = [c for items in self._adopted(draft)[1].values() for c in items]
        self.status(draft["plan_id"], "正在从已认可资料整理知识地图…" if not previous else "正在按你的意见修改知识地图…")
        # Structure is host-owned. Reuse module generation, bounded material
        # batches and truncation splitting; never ask a model for a whole map.
        modules = [dict(m, id=m['key'], prerequisites=[], minutes=[15, 15], points=[])
                   for m in structure['modules']]
        # One stage; its label is not the goal sentence (that carried stale dates).
        skeleton = {'stages': [{'title': '全部模块', 'modules': modules}]}
        by_section = {s.id: s for s in sections}
        for module in modules:
            allowed = {cid for ref in module['refs'] for cid in by_section[ref].chunk_ids}
            selected = [c for c in chunks if c['id'] in allowed]
            # Explicitly removed directory branches (and their broad parent
            # introduction) cannot reappear through a shared selected chunk.
            def retained(chunk):
                path = tuple(agents.original_text(v).casefold() for v in chunk.get('section') or [])
                for removed in draft.get('removed_content', ()):
                    if removed['kind'] != 'chapter' or removed.get('source_id') not in (None,chunk['source_id']): continue
                    deleted = tuple(agents.original_text(v).casefold() for v in removed['title'].split(' / '))
                    if path and (path[:len(deleted)] == deleted or deleted[:len(path)] == path): return False
                return True
            selected = [c for c in selected if retained(c)]
            picked = self._module_chunks(selected, {s['id']: s['title'] for s in sources})
            self.status(draft['plan_id'], '正在填知识点：' + module['title'])
            def complete(stage, system, messages):
                reply = self.services.gateway.complete('generate', messages, system=system,
                    cancel=ctx.cancel, plan_id=draft['plan_id'], max_tokens=8192)
                if ctx.cancel.is_set(): raise ModelCancelled()
                return reply if isinstance(reply, str) else reply.text
            module.update(agents.directory_plan_module(module, picked, complete))
        teaching = [m for m in modules if m['points']]
        if not teaching:
            raise BridgeError('NO_KNOWLEDGE', '采用的内容只有资料导航，没有可讲的知识正文，请补充资料。')
        # A navigation-only chapter is viewable at the next teaching module,
        # never an empty teaching module that would block starting the plan.
        for index, module in enumerate(modules):
            if module['points']: continue
            owner = next((m for m in modules[index+1:] if m['points']), teaching[-1])
            owner['navigation'] = module['navigation'] + owner['navigation']
            owner['refs'] = list(dict.fromkeys(module['refs'] + owner['refs']))
        skeleton['stages'][0]['modules'] = teaching
        # The host owns headings, grouping and evidence; do not run the legacy
        # point parser, which would strip subpoints and merge source overlaps.
        outline = dict(skeleton, dropped=[], message='已按原文目录整理大知识点、小要点和资料导航。')
        if outline["dropped"]:
            self._say(draft, "找不到可核验原文，已删除这些知识点：" + "、".join(outline["dropped"]) +
                      "。如还需要学习这些内容，请继续找资料、提供资料，或告诉我这部分暂时不学。")
        if not outline["stages"]:
            draft["outline"] = None
            draft["phase"] = "sources"
            self.save(draft)
            return False
        constraint_errors = agents.scope_requirement_errors(agents.outline_modules(outline), draft['form'])
        by_id = {s.id: s for s in sections}
        modules = agents.outline_modules(outline)
        for module in modules:
            for point in module['points']:
                point['source_general']=source_quality.general_only(point['chunk_ids'],sources,chunks,lists=self._lists())
            # Compact spans (source + chunk positions) keep the draft small for big books.
            module["spans"] = [[by_id[r].source_id, by_id[r].first, by_id[r].last] for r in module["refs"]]
            module["ref_titles"] = [f"{by_id[r].source_title} · {by_id[r].title}" for r in module["refs"]]
        draft["outline"] = {"agent": {"stages": [{"title": s["title"], "modules": [
            {k: m[k] for k in ("key", "title", "summary", "refs", "prerequisites", "points") if k in m} for m in s["modules"]]}
            for s in outline["stages"]]}, "stages": outline["stages"],
            "ranges": self._content_ranges(draft, modules),
            "version": (draft["outline"] or {}).get("version", 0) + 1, 'constraint_errors': constraint_errors,
            "grounded": True, "dropped": outline["dropped"]}
        draft.pop("estimate", None)
        draft["phase"] = "outline"
        draft["messages"].append(_msg("ai", "outline", outline["message"] or "这是依据资料整理的简略知识地图。", version=draft["outline"]["version"]))
        if constraint_errors:
            draft['messages'].append(_msg('ai', 'outline', '调整一次后仍不符合补充说明：' + '；'.join(constraint_errors)
                                          + ' 请修改学习范围后再生成。'))
        self.save(draft)

    # ----------------------------------------------------------- 4 estimate + generate
    def _content_ranges(self, draft, modules, *, annotate=False, question_counts=()):
        """Preview and persisted points use the same estimates and cached labels.

        Annotating runs only in the bridge's estimate worker, never in a view.
        Content edits invalidate labels; date/cap edits and deleting points don't.
        """
        chunks = self.services.store.records('source_chunks', draft['plan_id'])
        cancel = getattr(self._call_context, 'cancel', None) or threading.Event()
        rows = [dict(p, id=f'{i}:{j}', module_id=str(i))
                for i,m in enumerate(modules) for j,p in enumerate(m['points'])]
        metrics = agents.point_minute_metrics(rows, chunks, [])
        saved = {p['id']: p for p in self.services.store.records('points', draft['plan_id'])}
        for i, module in enumerate(modules):
            points = [p for p in rows if p['module_id'] == str(i)]
            keys = [hashlib.sha256(json.dumps(
                [p['title'], p.get('summary'), p.get('citations'), metrics[p['id']]['characters']],
                ensure_ascii=False, sort_keys=True).encode()).hexdigest() for p in points]
            originals = module['points']
            if annotate and not all(p.get('structure') == 'concept_v1' for p in originals) and any(p.get('_difficulty_key') != key or p.get('difficulty') not in agents.MINUTE_RULE['difficulty']
                                for p,key in zip(originals, keys)):
                if cancel.is_set() or getattr(self.services, '_closed', False): raise ModelCancelled()
                messages, aliases = agents.point_difficulty_request(dict(module, id=str(i)), points)
                reply = self.services.gateway.complete('point_difficulty', messages, system=agents.POINT_DIFFICULTY_SYSTEM,
                    cancel=cancel, plan_id=draft['plan_id'], max_tokens=4096)
                if cancel.is_set() or getattr(self.services, '_closed', False): raise ModelCancelled()
                labels = agents.parse_point_difficulty(reply if isinstance(reply, str) else reply.text, aliases)
                for p,row,key in zip(originals, points, keys):
                    p.update(difficulty=labels[row['id']], _difficulty_key=key)
            for j,(p,row) in enumerate(zip(originals, points), 1):
                persisted = saved.get(p.get('id') if p.get('structure') == 'concept_v1' else f"{module.get('id')}-{j:02d}") if draft['phase'] in ('generating','ready') else None
                p['minutes'] = (persisted['minutes'] if persisted else
                    sum(s['minutes'] for s in p['subpoints']) if p.get('structure') == 'concept_v1' else
                    agents.estimate_point_minutes(**metrics[row['id']], difficulty=p.get('difficulty', '中')))
            module['minutes'] = [sum(p['minutes'] for p in originals)] * 2
        return map_ranges(self._window(draft), len(rows), module_counts=[len(m['points']) for m in modules],
                          question_counts=question_counts, learning_minutes=sum(m['minutes'][0] for m in modules))

    def estimate(self, draft, *, annotate=True):
        if draft["phase"] != "outline" or not draft.get("outline"):
            raise BridgeError("PHASE_INVALID", "请先生成路线轮廓。")
        modules = agents.outline_modules(draft["outline"])
        ranges = self._content_ranges(draft, modules, annotate=annotate)
        draft["outline"]["ranges"] = ranges
        draft["estimate"] = {"modules": len(modules), "points": ranges["points"],
            "input_tokens": [0, 0], "output_tokens": [0, 0], "model": "",
            "note": "知识地图已核验；保存时不再生成知识点。只采用资料自带的题，检测时长按备好的实际题数计算。",
            "blocked": False}
        if ranges["over_cap"]:
            text = (f"按现在日期每天需要 {ranges['daily'][1]} 分钟，超过你填写的 {ranges['cap']} 分钟。"
                    f"建议延长到 {ranges['suggested_end']}（共 {ranges['suggested_days']} 天），或缩小学习范围。"
                    "可以提高每天时长、延长周期或调整内容，也可以按当前安排继续。")
        else:
            text = (f"共 {ranges['points']} 个知识点，按原文分量和难度预估学习 {ranges['learning']} 分钟；"
                    f"目前学习约 {ranges['total'][1]} 分钟，每天约 {ranges['daily'][1]} 分钟；检测时长待实际题数确定。确认后保存计划并准备第 1 模块。")
        draft["messages"].append(_msg("ai", "estimate", text))
        self.save(draft)

    def _save_outline(self, draft):
        """Write stages/modules to the store once; ids are kept in the draft for retries."""
        store, plan_id = self.services.store, draft["plan_id"]
        stages, modules, keys = [], [], {}
        for stage in draft["outline"]["stages"]:
            stage.setdefault("id", _new_id()[:16])
            stages.append({"id": stage["id"], "title": stage["title"]})
            for module in stage["modules"]:
                module.setdefault("id", _new_id()[:16])
                keys[module["key"]] = module["id"]
        for stage in draft["outline"]["stages"]:
            for module in stage["modules"]:
                modules.append({"id": module["id"], "stage_id": stage["id"], "title": module["title"],
                                "summary": module["summary"], "prerequisites": [keys[p] for p in module["prerequisites"]],
                                "learning_minutes": sum(p['minutes'] for p in module['points']),
                                **({'navigation': module['navigation']} if 'navigation' in module else {})})
        chunks = store.records("source_chunks", plan_id)
        for module, source in zip(modules, [m for s in draft["outline"]["stages"] for m in s["modules"]]):
            module["chunk_ids"] = [c["id"] for c in self._span_chunks(source, chunks)]
        store.save_outline(plan_id, stages, modules)
        # Material decisions are final now: unadopted sources and their text leave the plan.
        adopted = {c["source_id"] for c in draft["sources"] if c["status"] == "adopted" and c.get("source_id")}
        for source in store.records("sources", plan_id):
            if source["id"] not in adopted and source.get("adopted"):
                store.set_source_adopted(plan_id, source["id"], False)

    def generate(self, draft, ctx):
        if draft['phase'] == 'ready':
            modules = agents.outline_modules(draft['outline'])
            failed = [m for m in modules if draft.get('generation', {}).get(m['id']) == 'failed']
            self._prepare_remaining(draft['plan_id'], failed, self._prepare_callable(), retry=True)
            return
        if draft["phase"] not in ("outline", "generating"):
            raise BridgeError("PHASE_INVALID", "现在不能生成。")
        outline = draft.get("outline") or {}
        if not outline.get("grounded"):
            raise BridgeError("MAP_REQUIRED", "请根据已认可资料重新整理知识地图。旧格式计划不继续生成。")
        modules = agents.outline_modules(outline)
        if draft["phase"] == "outline":
            if not draft.get("estimate"):
                self.estimate(draft)
                return
        errors = agents.scope_requirement_errors(modules + [p for m in modules for p in m["points"]], draft["form"])
        if errors:
            self._say(draft, "地图涉及已排除内容，请先调整：" + "；".join(errors), "error")
            return
        prepare = self._prepare_callable()
        store, plan_id = self.services.store, draft["plan_id"]
        chunks = {c["id"]: c for c in store.records("source_chunks", plan_id)}
        adopted, _ = self._adopted(draft)
        allowed = {s["id"] for s in adopted}
        for module in modules:
            for point in module["points"]:
                for citation in point["citations"]:
                    c = chunks.get(citation["chunk_id"])
                    original = agents.resolve_paragraph(c, citation) if c and 'paragraph_id' in citation else None
                    if not c or c["source_id"] not in allowed or original is None:
                        raise BridgeError("MAP_SOURCE_CHANGED", "地图引用的资料已变化，请重新整理知识地图。")
                    citation['quote'] = original
        if draft["phase"] == "outline":
            shown_learning = (draft['outline'].get('ranges') or {}).get('learning')
            ranges = self._content_ranges(draft, modules, annotate=True)
            if shown_learning is not None and shown_learning != ranges['learning']:
                # A local edit of an older, unlabelled draft used a provisional
                # medium estimate. Show the refined preview before saving it.
                self.estimate(draft)
                return
            self._save_outline(draft)
            for module in modules:
                payload = [dict({k:v for k,v in p.items() if k != '_difficulty_key'},
                                id=p['id'] if p.get('structure') == 'concept_v1' else f"{module['id']}-{index:02d}")
                           for index, p in enumerate(module["points"], 1)]
                store.save_generated_module(plan_id, module["id"], payload, today=self.study_day(),
                    check_minutes=0, final_check_minutes=0, check_question_minutes=2)
            # Reuse preview labels: saving must not rescale or ask the model again.
            if not all(p.get('structure') == 'concept_v1' for m in modules for p in m['points']):
                initial = store.point_minutes_snapshot(plan_id)
                store.save_point_minutes(plan_id, initial['fingerprint'],
                    {p['id']: p['difficulty'] for p in initial['points']}, today=self.study_day())
            draft["generation"] = {m["id"]: "pending" for m in modules}
            draft["phase"] = "generating"
            draft["messages"].append(_msg("ai", "progress", "计划已保存，正在准备第 1 模块的资料与题目。"))
            self.save(draft)
            self.ensure_point_links(plan_id)
            self.ensure_point_minutes(plan_id)
        first = modules[0]
        def progress(done, total, text):
            self.status(plan_id, f"正在准备第 1 模块的资料与题目 {done}/{total}" + (f" · {text}" if text else ""))
        self.status(plan_id, f"正在准备第 1 模块的资料与题目 0/{len(first['points'])}")
        draft["generation"][first["id"]] = "running"
        self.save(draft)
        try:
            self._prepare_notes(plan_id, first['id'], ctx)
            preparation = prepare(store, plan_id, first["id"], self._material_context(ctx), progress)
            self._save_material_summary(plan_id, first["id"], preparation)
            if ctx.cancel.is_set():
                raise ModelCancelled()
            if not self._module_ready(plan_id, first['id']):
                raise BridgeError('MATERIALS_INCOMPLETE', '第 1 模块的原文或带答案题不足，请补充资料后重试；计划尚未就绪。')
        except Exception as exc:
            draft["generation"][first["id"]] = "failed"
            reason = getattr(exc, 'message', '这个模块的题未备好，请重试。')
            draft.setdefault('generation_errors', {})[first['id']] = reason
            store.save_module_preparation(plan_id, first['id'], dict(state='failed', error=reason))
            self.save(draft)
            raise
        draft["generation"][first["id"]] = "done"
        draft.setdefault('generation_errors', {}).pop(first['id'], None)
        draft["phase"] = "ready"
        draft["messages"].append(_msg("ai", "ready", "第 1 模块的资料与题目已备好，可以开始计划。其余模块会在后台依次全部准备，某个模块失败不影响其他模块。"))
        self.save(draft)
        row = next(m for m in store.records('modules', plan_id) if m['id'] == first['id'])
        store.save_module_preparation(plan_id, first['id'], dict(row.get('material_preparation') or {}, state='done'))
        self._prepare_remaining(plan_id, modules[1:], prepare)

    def _prepare_callable(self):
        if self._prepare_module is not None:
            return self._prepare_module
        try:
            from ..course.point_materials import prepare_module
            return prepare_module
        except ImportError:
            raise BridgeError('PREPARATION_UNAVAILABLE', '资料与题目准备组件尚未接入，暂时不能开始计划。') from None

    def get_draft(self, plan_id):
        draft = self.load(plan_id)
        if not self._lock(plan_id).locked():
            interrupted = [c for c in self._decision_cards(draft) if c.get('read_status')=='reading']
            if interrupted:
                for card in interrupted:
                    card.update(read_status='cancelled',read_error='上次读取已中断，请重新勾选。')
                self.save(draft)
        if draft['phase'] == 'ready':
            modules = agents.outline_modules(draft['outline'])
            self._prepare_remaining(plan_id, modules, self._prepare_callable())
            draft = self.load(plan_id)
            persisted = {m['id']:m.get('material_preparation') or {} for m in self.services.store.records('modules', plan_id)}
            # A process can exit after the module write but before the draft
            # write. Restore visible completion/errors before resuming the tail.
            for m in modules:
                summary = persisted.get(m['id'], {})
                state = 'done' if self._module_ready(plan_id,m['id']) else summary.get('state')
                if state in ('done', 'failed'):
                    draft['generation'][m['id']] = state
                    errors = draft.setdefault('generation_errors', {})
                    if state=='failed' and summary.get('error'): errors[m['id']] = summary['error']
                    else: errors.pop(m['id'], None)
            # This is a read-only view; never overwrite a worker's newer draft.
        return self.public(draft)

    def _material_context(self, ctx):
        # Preserve the bridge cancellation/stream contract and expose the
        # existing account gateway; MF-04 owns material/question preparation.
        return dict(cancel=ctx.cancel, gateway=self.services.gateway, fetcher=self.fetcher,
                    on_status=lambda text: ctx.stream(text))

    def _prepare_notes(self, plan_id, module_id, ctx):
        """Optional module preparation, never a prerequisite for teaching."""
        store = self.services.store
        points = [p for p in store.records('points', plan_id) if p['module_id'] == module_id]
        if not any(p.get('structure') == 'concept_v1' and
                   any(not s.get('notes') for s in p['subpoints']) for p in points): return
        chunks = store.records('source_chunks', plan_id)
        def complete(stage, system, messages):
            if ctx.cancel.is_set(): raise ModelCancelled()
            try:
                reply = self.services.gateway.complete('point_materials', messages, system=system,
                    cancel=ctx.cancel, plan_id=plan_id, max_tokens=8192)
            except ModelCancelled: raise
            except Exception as exc:
                raise agents.AgentFormatError('讲义准备失败') from exc
            return reply if isinstance(reply, str) else reply.text
        try:
            notes = agents.prepare_directory_notes(points, chunks, complete)
            if ctx.cancel.is_set(): raise ModelCancelled()
            with store.transaction():
                for current in store.records('points', plan_id):
                    if current['module_id'] != module_id or current.get('structure') != 'concept_v1': continue
                    changed = False
                    for sub in current['subpoints']:
                        if not sub.get('notes') and notes.get(sub['id']):
                            sub['notes'] = notes[sub['id']]; changed = True
                    if changed: store._put('points', plan_id, current)
        except ModelCancelled: raise
        except Exception:
            log.warning('optional notes failed plan=%s module=%s; original source remains available',
                        plan_id, module_id, exc_info=True)

    def _module_ready(self, plan_id, module_id):
        from ..course.point_materials import preparation_done
        return preparation_done(self.services.store, plan_id, module_id)

    def _save_material_summary(self, plan_id, module_id, result):
        from ..course.question_bank import assessment_questions
        count = len(assessment_questions(self.services.store.records('question_bank', plan_id), module_id))
        if any(m['id']==module_id for m in self.services.store.records('modules', plan_id)):
            self.services.store.set_question_estimate(plan_id, module_id, count, today=self.study_day())
        if not isinstance(result, dict) or not any(k in result for k in ('points', 'cloze_points', 'cloze_heavy')):
            # The integrated baseline still returns aggregate question counts.
            # MF-02's new summary will be exercised through the agreed test stub.
            return
        summary = {k: result.get(k) for k in ('points', 'cloze_points', 'cloze_heavy')}
        if (type(summary['points']) is not int or type(summary['cloze_points']) is not int
                or not 0 <= summary['cloze_points'] <= summary['points'] or type(summary['cloze_heavy']) is not bool):
            raise BridgeError('PREPARATION_FORMAT', '资料准备结果不完整，请重试。')
        self.services.store.save_module_preparation(plan_id, module_id, summary)

    def _prepare_remaining(self, plan_id, modules, prepare, *, retry=False, notify=None, fingerprint=None, priority=False):
        """One sequential queue per plan; classroom demand moves a job to its head."""
        if not modules or getattr(self.services, '_closed', False):
            return None
        state = self._material_jobs
        with state['guard']:
            queue = state['queued'].setdefault(plan_id, [])
            persisted = {m['id']: m.get('material_preparation') or {} for m in self.services.store.records('modules', plan_id)}
            first_job = None
            for module in modules:
                mid = module['id']; key = (plan_id, mid)
                job = state['jobs'].get(key)
                if job and job['status'] in ('running', 'queued') and not job['cancel'].is_set():
                    if job['fingerprint'] is None: job['fingerprint'] = fingerprint
                else:
                    ready = self._module_ready(plan_id, mid) and (not job or fingerprint is None or job['fingerprint'] in (None, fingerprint))
                    failed = (job and job['status']=='failed') or persisted.get(mid, {}).get('state')=='failed'
                    if not job or not (ready or failed and not retry):
                        if job: job['cancel'].set()
                        job = dict(module_id=mid, status='ready' if ready else 'failed' if failed and not retry else 'queued',
                            done=0, total=len(module.get('points', [])), text='这个模块的题还在准备',
                            error=persisted.get(mid, {}).get('error') if failed and not retry else None,
                            fingerprint=fingerprint, cancel=threading.Event(), listeners=[])
                        state['jobs'][key] = job
                    if job['status']=='queued':
                        queue[:] = [entry for entry in queue if entry[0]['id']!=mid]
                        queue.append((module, prepare, job))
                if job['fingerprint'] is None: job['fingerprint'] = fingerprint
                if notify and notify not in job['listeners']: job['listeners'].append(notify)
                if priority and job['status']=='queued':
                    entries = [entry for entry in queue if entry[2] is job]
                    queue[:] = entries + [entry for entry in queue if entry[2] is not job]
                if first_job is None: first_job = job
            if queue and plan_id not in state['active']:
                state['active'].add(plan_id)
                threading.Thread(target=self._prepare_worker, args=(plan_id,), name='plan-materials', daemon=True).start()
            return first_job

    def _prepare_worker(self, plan_id):
        state = self._material_jobs
        while True:
            with state['guard']:
                queue = state['queued'].get(plan_id, [])
                if not queue:
                    state['queued'].pop(plan_id, None)
                    state['active'].discard(plan_id)
                    # Questions prepared after finalization change host weights;
                    # persisted difficulty labels are reused without another AI call.
                    if not getattr(self.services, '_closed', False):
                        store = self.services.store
                        try:
                            plan = {}
                            if store is not None:
                                with store.transaction(): plan = store.get_plan(plan_id)
                            # Old courses start on detail, never as a side effect of grading.
                            if plan.get('point_minutes_version') or plan.get('point_minutes_provisional'):
                                self.ensure_point_minutes(plan_id)
                        except StoreError as exc:
                            if exc.code != 'STORE_CLOSED':
                                log.exception('point minutes preparation hook failed plan=%s', plan_id)
                                self._point_minutes_event(plan_id, 'failed')
                    return
                module, prepare, job = queue.pop(0)
                job['status'] = 'running'
            mid = module['id']
            def progress(done, total, text):
                job.update(done=done, total=total, text='这个模块的题还在准备' + (f' · {text}' if text else ''))
                self._notify_preparation(plan_id, job)
            ctx = CallContext('plan-materials', job['cancel'], lambda _: None)
            try:
                if job['cancel'].is_set() or getattr(self.services, '_closed', False): raise ModelCancelled()
                self._record_preparation(plan_id, mid, 'running')
                with self._background_material_lock(plan_id):
                    if job['cancel'].is_set(): raise ModelCancelled()
                    self._prepare_notes(plan_id, mid, ctx)
                    result = prepare(self.services.store, plan_id, mid, self._material_context(ctx), progress)
                if job['cancel'].is_set() or getattr(self.services, '_closed', False): raise ModelCancelled()
                self._save_material_summary(plan_id, mid, result)
                if not self._module_ready(plan_id, mid):
                    raise BridgeError('MATERIALS_INCOMPLETE', '这个模块的原文或题目不足，进入模块时可重试。')
                job['status'] = 'ready'
                self._record_preparation(plan_id, mid, 'done')
            except ModelCancelled:
                job['status'] = 'cancelled'
            except Exception as exc:
                log.warning('module preparation failed: %s', type(exc).__name__)
                job.update(status='failed', error=getattr(exc, 'message', '这个模块的题未备好，进入模块时可重试。'),
                           code=getattr(exc, 'code', None))
                if not getattr(self.services, '_closed', False):
                    try: self._record_preparation(plan_id, mid, 'failed', error=job['error'])
                    except Exception: log.warning('could not persist module preparation failure', exc_info=True)
            finally:
                self._notify_preparation(plan_id, job)

    def _background_material_lock(self, plan_id):
        # Existing final-check replenishment shares the same IO slot. Only
        # background workers acquire it; bridge operations never wait here.
        with self._material_jobs['guard']:
            return self._material_jobs['execution_locks'].setdefault(plan_id, threading.Lock())

    def _notify_preparation(self, plan_id, job):
        if job['cancel'].is_set() or getattr(self.services, '_closed', False): return
        data = {k: job[k] for k in ('module_id', 'done', 'total', 'text', 'error')}
        data.update(plan_id=plan_id, status=job['status'])
        try: self.push('plan_preparation', data)
        except Exception: log.info('preparation view closed', exc_info=True)
        for notify in list(job['listeners']):
            try: notify(data)
            except Exception: log.info('preparation listener closed', exc_info=True)

    def _record_preparation(self, plan_id, module_id, state, *, error=None):
        # The draft is removed when learning starts. Keep module state in the
        # existing persisted summary as well, so reopen never loses completion.
        for module in self.services.store.records('modules', plan_id):
            if module['id'] == module_id:
                if state=='done':
                    from ..course.question_bank import assessment_questions
                    count=len(assessment_questions(self.services.store.records('question_bank',plan_id),module_id))
                    self.services.store.set_question_estimate(plan_id,module_id,count,today=self.study_day())
                summary = dict(module.get('material_preparation') or {}, state=state, error=error)
                self.services.store.save_module_preparation(plan_id, module_id, summary)
                break
        # Read the latest draft under the foreground operation's lock. Starting
        # or deleting a plan removes the draft; background work must not recreate it.
        with self._lock(plan_id):
            if not self._file(plan_id).path.exists():
                return
            draft = self.load(plan_id)
            if module_id in draft.get("generation", {}):
                draft["generation"][module_id] = state
                errors = draft.setdefault('generation_errors', {})
                if error: errors[module_id] = error
                else: errors.pop(module_id, None)
                self.save(draft)

    def _generate_module(self, draft, module, chunks, ctx):
        points = self._generate_module_batches(draft, module, chunks, ctx)
        return self._check_module_points(draft, module, chunks, ctx, points, [])

    def _drop_empty_module(self, draft, module):
        """Remove a module whose generated points were all duplicate/forbidden."""
        module_id = module.get('id')
        try:
            self.services.store.delete_module(draft['plan_id'], module_id, today=self.study_day())
        except (AttributeError, StoreError):
            # Unit-level callers may not have a store; the draft still needs the
            # same visible result when the real host later persists the outline.
            pass
        for stage in draft.get('outline', {}).get('stages', []):
            stage['modules'] = [m for m in stage.get('modules', []) if m.get('id') != module_id and m.get('key') != module.get('key')]
            for remaining in stage['modules']:
                remaining['prerequisites'] = [p for p in remaining.get('prerequisites', []) if p != module.get('key')]
        agent = draft.get('outline', {}).get('agent', {})
        for stage in agent.get('stages', []):
            stage['modules'] = [m for m in stage.get('modules', []) if m.get('id') != module_id and m.get('key') != module.get('key')]
            for remaining in stage['modules']:
                remaining['prerequisites'] = [p for p in remaining.get('prerequisites', []) if p != module.get('key')]
        draft.get('generation', {}).pop(module_id, None)

    @staticmethod
    def _forbidden_point_indexes(points, form):
        topics = [str(x).strip() for x in (form.get('abandoned_topics') or []) if str(x).strip()]
        return {i for i, point in enumerate(points)
                if any(topic in (point.get('title', '') + ' ' + point.get('summary', '')) for topic in topics)}

    def _check_module_points(self, draft, module, chunks, ctx, points, previous):
        # Cross-module comparison is deliberately deterministic and free. Keep
        # the first occurrence and silently omit later lexical duplicates.
        duplicate_indexes = {hit['index'] for hit in agents.cross_module_duplicates(points, previous)}
        points = [point for index, point in enumerate(points) if index not in duplicate_indexes]
        forbidden = self._forbidden_point_indexes(points, draft['form'])
        errors = agents.point_requirement_errors(points, module, draft['form'])
        if not errors and not forbidden:
            return points
        if not points:
            return []
        # One explicit content adjustment, preserving all original material and
        # user scope. A forbidden topic is filtered after this one retry.
        request = agents.generate_request(draft['form'], draft['goal'], draft['outline'], module, chunks)
        aliases = request[2]
        reverse = {value: key for key, value in aliases.items()}
        last_version = [dict(p, chunks=[reverse[r] for r in p['chunk_ids']]) for p in points]
        request[1].append({'role': 'user', 'content': '【程序检查与一次调整】\n' + '；'.join(errors)
            + '\n只重生成本模块，不能重复前面模块的学习目标（改名、改写摘要、重复练习都不算新内容）。'
              '不得出现学习者已放弃的内容；资料不足时不凑数，输出实际独立点即可；不添原文没有的内容。\n【前面模块已收的点】\n'
            + json.dumps(previous, ensure_ascii=False) + '\n【上一版】\n'
            + json.dumps(last_version, ensure_ascii=False)})
        try:
            points, _ = self._ask('generate', request[:2],
                lambda text: agents.parse_points(text, aliases, min_points=1, max_points=30), ctx,
                plan_id=draft['plan_id'], max_tokens=8192)
        except ModelError as exc:
            if exc.code != 'AGENT_FORMAT':
                raise
            raise agents.AgentFormatError('；'.join(errors) + ' 调整回复仍无有效知识点结构。') from exc
        duplicate_indexes = {hit['index'] for hit in agents.cross_module_duplicates(points, previous)}
        forbidden = self._forbidden_point_indexes(points, draft['form'])
        points = [point for index, point in enumerate(points) if index not in duplicate_indexes and index not in forbidden]
        if not points:
            return []
        errors = agents.point_requirement_errors(points, module, draft['form'])
        if errors:
            raise agents.AgentFormatError('；'.join(errors))
        return points

    def _generate_module_batches(self, draft, module, chunks, ctx):
        """Read all of a module's material: big modules go in bounded batches, merged afterwards."""
        if draft.get('material_map'):
            batches = agents.split_module_chunks(agents.material_paragraphs(chunks), max_chars=agents.DEFAULT_BATCH_CHARS)
            parts = [part for index, batch in enumerate(batches, 1)
                     for part in self._generate_batch(draft, module, batch, None,
                         (index, len(batches)), ctx, max_points=30)]
            return parts[0] if len(parts) == 1 else agents.merge_point_batches(parts, chunks, min_points=1)
        limit = max(1, min(MAX_GENERATE_BATCHES, module["minutes"][0] // 10))
        size = agents.DEFAULT_BATCH_CHARS
        batches = agents.split_module_chunks(chunks, max_chars=size)
        needed = min(limit, max(1, math.ceil(module["minutes"][1] / 450)))
        if len(batches) < needed:
            total_chars = sum(len(chunk["text"]) for chunk in chunks)
            size = max(1, math.ceil(total_chars / needed))
            batches = agents.split_module_chunks(chunks, max_chars=size)
        while len(batches) > limit:  # very long material: fewer, larger batches rather than dropping text
            size = int(size * 1.4) + 1
            batches = agents.split_module_chunks(chunks, max_chars=size)
        if len(batches) == 1:
            parts = self._generate_batch(draft, module, chunks, tuple(module["minutes"]), None, ctx, max_points=30)
        else:
            per = max(1, min(30, math.ceil(module["minutes"][1] / (10 * len(batches)))))
            ranges = agents.batch_minute_ranges(batches, module["minutes"])
            parts = [part for index, (batch, minutes) in enumerate(zip(batches, ranges), 1)
                     for part in self._generate_batch(draft, module, batch, minutes, (index, len(batches)), ctx, max_points=per)]
        if len(parts) == 1:
            return parts[0]
        points = agents.merge_point_batches(parts, chunks)
        try:
            agents.validate_point_minutes(points, module["minutes"])
        except agents.AgentFormatError as exc:  # merging near-duplicates can lower the total; keep the points
            log.info("module %s minutes after merging: %s", module["id"], exc)
        return points

    def _generate_batch(self, draft, module, batch, minutes, position, ctx, *, max_points, split=True):
        """Returns a list of point lists; an answer cut off at the output limit is retried as two halves."""
        if draft.get('material_map'):
            batch = agents.material_paragraphs(batch)
        index, count = position or (None, None)
        request = agents.generate_request(draft["form"], draft["goal"], draft["outline"], module, batch,
                                          minutes_range=minutes, batch_index=index, batch_count=count, max_points=max_points,
                                          material_map=bool(draft.get('material_map')),
                                          removed_content=draft.get('removed_content', ()) if draft.get('material_map') else ())
        aliases = request[2]
        # Count constraints are checked after ALL batches are merged. Allow an
        # overlong first result through parsing so the dedicated adjustment can
        # see and revise it, rather than repeating the same request unchanged.
        def parse(text):
            points = agents.parse_points(text, aliases, minutes_range=minutes,
                max_points=max_points, min_points=1,
                paragraph_chunks=batch if draft.get('material_map') else None)
            if draft.get('material_map'):
                points = agents.filter_removed_content(points, batch, draft.get('removed_content', ()))
            return points
        try:
            return [self._ask("generate", request[:2], parse, ctx, plan_id=draft["plan_id"], max_tokens=8192)[0]]
        except ModelError as exc:
            if exc.code != MODEL_OUTPUT_TRUNCATED or not split or (minutes is not None and minutes[0] < 4):
                raise
            log.info("module %s batch %s hit the output limit; retrying in two halves", module["id"], index)
        halves = agents.split_module_chunks(batch, max_chars=sum(len(c["text"]) for c in batch) // 2 + 1)
        if len(halves) != 2:
            raise ModelError(MODEL_OUTPUT_TRUNCATED, "模型输出达到上限，这个模块的资料拆小后仍然不行。")
        ranges = agents.batch_minute_ranges(halves, minutes) if minutes is not None else [None, None]
        return [part for i, (half, r) in enumerate(zip(halves, ranges), 1)
                for part in self._generate_batch(draft, module, half, r, (i, 2), ctx,
                                                 max_points=max(1, max_points // 2), split=False)]

    @staticmethod
    def _span_chunks(module, chunks):
        picked, seen = [], set()
        for source_id, first, last in module["spans"]:
            for chunk in sorted((c for c in chunks if c["source_id"] == source_id and first <= c["position"] <= last),
                                key=lambda c: c["position"]):
                if chunk["id"] not in seen:
                    seen.add(chunk["id"])
                    picked.append(chunk)
        return picked

    @staticmethod
    def _module_chunks(chunks, titles):
        return [{"id": c["id"], "text": c["text"], "section": c.get("section") or [],
                 "block_fragments": c.get("block_fragments") or [],
                 "source_id": c['source_id'],
                 "source_title": titles.get(c["source_id"], "资料")} for c in chunks if c["text"].strip()]

    # ----------------------------------------------------------- chat box (ARCH-06 intents)
    def _intent_context(self, draft):
        phase = draft["phase"]
        form = {k: v for k, v in (draft["form"] or {}).items() if k != "only_mine"}
        recent = dialogue_tail([{"role": m["role"], "text": m["text"][:1500]} for m in draft["messages"]
                                if m["role"] in ("ai", "user") and m.get("text")], max_rows=6)
        cards = [{"n": n, "title": c["title"], "site": c.get("site") or "", "summary": (c.get("summary") or "")[:200],
                  "status": {"new": "待定", "adopted": "已采用", "rejected": "不要"}[c.get("status",'new')]}
                 for n, c in enumerate(self._decision_cards(draft), 1)] if phase in ("sources", "material_preview", "outline") else []
        sections, modules, facts = [], [], {}
        if phase in ("sources", "material_preview", "outline"):
            sections = [{"id": s.id, "source": s.source_title, "title": s.title, "chars": s.chars}
                        for s in self._sections(draft)[1]]
            if draft.get("scope"):
                facts["scope"] = draft["scope"]
        if phase in ("outline", "ready") and draft.get("outline"):
            facts["ranges"] = draft["outline"]["ranges"]
            facts["outline"] = [{"stage": s["title"], "modules": [m["title"] for m in s["modules"]]}
                                for s in draft["outline"]["stages"]]
            if phase == 'outline':
                modules = [dict(n=n, title=m['title'], key=m['key'],
                    points=[dict(n=i, title=p['title']) for i,p in enumerate(m.get('points', []), 1)])
                    for n,m in enumerate(agents.outline_modules(draft['outline']), 1)]
        if phase in ("outline", "ready") and draft.get("estimate"):
            facts["estimate"] = draft["estimate"]
        # An accepted preview can still carry real learner requirements after
        # structure confirmation; retain it rather than guessing it is stale.
        if draft.get("preview"):
            facts["preview"] = draft["preview"]
        if phase in ('material_preview', 'outline', 'ready') and draft.get('material_structure'):
            # The current module/point numbering is already in modules. Keep
            # scope/missing/deletion facts, without repeating an older full map.
            facts['material_structure'] = {k: v for k, v in draft['material_structure'].items() if k != 'modules'}
            if phase == 'material_preview':
                modules = [dict(n=n, title=m['title'], summary=m['summary'], chapters=m['chapters'])
                           for n, m in enumerate(draft['material_structure']['modules'], 1)]
        if phase == "ready":
            store, plan_id = self.services.store, draft["plan_id"]
            rows = store.records("modules", plan_id)
            number = {m["id"]: n for n, m in enumerate(rows, 1)}
            modules = [{"n": number[m["id"]], "title": m["title"], "summary": m.get("summary", ""),
                        "prerequisites": [number[p] for p in m["prerequisites"] if p in number]} for m in rows]
            facts["generated"] = {"modules": len(rows), "points": len(store.records("points", plan_id)),
                                  "total_minutes": store.get_plan(plan_id).get("total_minutes")}
        return {"phase": phase, "today": self.study_day().isoformat(), "form": form, "recent": recent,
                "cards": cards, "sections": sections, "modules": modules, "facts": facts}

    def chat(self, draft, ctx, message):
        """The chat box: the intent agent says what the message asks for; the host does it (BUG-13)."""
        phase = draft["phase"]
        if phase not in ("questions", "preview", "sources", "material_preview", "outline", "ready"):
            raise BridgeError("PHASE_INVALID", "请先填写上面的表单。" if phase == "form" else "正在生成，请稍等。")
        draft["messages"].append(_msg("user", "user_text", message))
        self.save(draft)
        compact = re.sub(r"[\s，,。！!？?]+", "", message)
        if phase == 'outline' and draft.get('pending_map_delete'):
            if compact in ('是', '是的', '确定', '确认', '确认删除', '确定删除', '好的'):
                return self.confirm_map_delete(draft, True)
            if compact in ('否', '不', '取消', '不删了', '不要删', '取消删除'):
                return self.confirm_map_delete(draft, False)
            # A new request supersedes the old confirmation; never apply stale selections.
            draft.pop('pending_map_delete', None)
        if phase == 'questions' and compact in ('就这样吧', '就这样', '直接开始', '直接开始吧', '开始吧'):
            draft['goal'] = draft.get('goal') or draft['form']['goal']
            return self._show_preview(draft, ctx)
        if phase == 'preview' and compact in ('可以', '可以了', '行', '好的', '同意', '就这样吧', '确认并找资料', '开始找资料', '直接开始'):
            return self._confirm_preview(draft, ctx)
        if phase == 'material_preview' and compact in ('确认', '同意', '可以', '好的', '就这样继续', '确认结构', '确认并填知识点'):
            return self.resolve_missing(draft, ctx, 'continue')
        text = message
        links = find_links(message) if phase in ("sources", "material_preview", "outline") else []
        if links:  # pasted addresses are opened directly; no model is needed to understand them
            self._carry_out(draft, ctx, plan_intent.Intent("import_sources", {"urls": links}), {})
            text = _URL.sub("", message).strip(" ，,。;；\n")
            if len(text) < 6:
                return
        self.status(draft["plan_id"], "正在理解你的意思…")
        context = self._intent_context(draft)
        if phase == 'outline' and re.search(r'删掉|删除|去掉', text):
            # Map deletions only need the already generated names/numbers.
            # Sending the book's full directory hides those targets and costs
            # more than the local edit itself. Other intent contexts stay intact.
            context = dict(context, sections=[], facts={k:v for k,v in context['facts'].items()
                                                       if k in ('ranges', 'outline')})
        intents, _ = self._ask("plan_intent", plan_intent.intent_request(context, text),
                               lambda answer: plan_intent.parse_intents(answer, context, user_text=text), ctx, plan_id=draft["plan_id"])
        deletions = [i for i in intents if i.kind == 'delete_map']
        if deletions:
            return self._carry_out(draft, ctx, deletions[0], context)
        supplements = [i for i in intents if i.kind == 'supplement']
        confirm_step = {'preview': 'sources', 'material_preview': 'outline'}.get(phase)
        confirms = [i for i in intents if i.kind == 'proceed' and i.params['step'] == confirm_step]
        if confirm_step and supplements and confirms:
            # The same message confirms the amended requirements. Never spend
            # a search on the obsolete preview or ask for consent a second time.
            draft.setdefault('requirements', []).extend(i.params['instruction'] for i in supplements)
            if phase == 'preview':
                self._show_preview(draft, ctx, confirmed=True)
                self._confirm_preview(draft, ctx)
            else:
                self.material_structure(draft, ctx, '\n'.join(i.params['instruction'] for i in supplements),
                                        confirmed=True)
                self.make_outline(draft, ctx)
            intents = [i for i in intents if i not in supplements and i not in confirms]
            context = self._intent_context(draft)
        for intent in intents:
            try:
                self._carry_out(draft, ctx, intent, context)
            except BridgeError as exc:  # stop at the first step that cannot be done; say why
                self._say(draft, exc.message, "error")
                return

    def _say(self, draft, text, kind="text"):
        draft["messages"].append(_msg("ai", kind, text))
        self.save(draft)

    def _abandon_scope(self, draft, ctx, items):
        if draft["phase"] not in ("sources", "material_preview", "outline"):
            return self._say(draft, "现在还不能改学习范围。")
        topics = list(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))
        if not topics:
            raise BridgeError("PARAM_INVALID", "请说清暂时不学的内容。")

        def without(text):
            value = text or ""
            for topic in topics:
                value = re.sub(r"[（(][^（）()]{0,80}" + re.escape(topic) + r"[^（）()]{0,80}[）)]", "", value)
                value = re.sub(re.escape(topic), "", value)
            value = re.sub(r"[（(]\s*(?:含|包括|以及|和)?\s*[）)]", "", value)
            value = re.sub(r"\s{2,}", " ", value).strip(" ，,、；;：:")
            return value

        old_form = draft["form"]
        abandoned = list(dict.fromkeys((draft.get("abandoned_topics") or []) + topics))
        draft["abandoned_topics"] = abandoned
        narrowed_goal = without(old_form.get("goal") or draft.get("goal")) or "资料覆盖的学习内容"
        narrowed_final = without(old_form.get("final_goal")) or narrowed_goal
        # A generic goal such as “solve linear equations” still implies the
        # abandoned family to a coverage checker. Make the learner's accepted
        # scope explicit while retaining the original wording for the chat.
        scope_suffix = "（只学习现有资料能覆盖的部分）"
        if scope_suffix not in narrowed_goal:
            narrowed_goal += scope_suffix
        if scope_suffix not in narrowed_final:
            narrowed_final += scope_suffix
        draft["goal"] = narrowed_goal
        draft["name"] = short_plan_name(narrowed_goal)
        draft["form"] = dict(old_form, goal=narrowed_goal,
                              final_goal=narrowed_final, abandoned_topics=abandoned)
        draft["outline"] = None
        draft.pop("estimate", None)
        draft.pop("coverage", None)
        draft["phase"] = "sources"
        try:
            self.services.store.save_intake(draft["plan_id"], goal=draft["form"]["goal"],
                final_goal=draft["form"]["final_goal"], level=draft["form"]["level"],
                notes=draft["form"].get("notes", ""), window=self._window(draft),
                today=self.study_day(), reason="学习者放弃部分目标")
            self.services.store.rename_plan(draft["plan_id"], draft["name"])
        except AttributeError:
            pass
        self._say(draft, "已按你的意思调整目标，只学习资料能覆盖的部分；暂时不学：" + "、".join(topics)
                  + "。调整后的目标是：" + draft["goal"] + "。")
        sources, chunks = self._adopted(draft)
        sections = self._sections(draft)[1]
        if sources and sections:
            return self.material_structure(draft, ctx)

    def _carry_out(self, draft, ctx, intent, context):
        kind, params, phase = intent.kind, intent.params, draft["phase"]
        if kind == 'delete_map':
            modules = agents.outline_modules(draft.get('outline') or {'stages': []})
            return self.request_map_delete(draft,
                [modules[n-1]['key'] for n in params['modules']],
                [dict(module=modules[p['module']-1]['key'], n=p['n']) for p in params['points']],
                params['unknown'])
        if kind == "import_sources":
            draft["phase"] = "sources"
            return self._import_links(draft, ctx, params["urls"])
        if kind == "answer":
            return self._check_sufficiency(draft, ctx)
        if kind == 'select_modules':
            return self._select_material_modules(draft, params)
        if kind == 'supplement' and phase in ('sources', 'material_preview', 'outline'):
            draft.setdefault('requirements', []).append(params['instruction'])
            if any(c.get('status') == 'adopted' for c in draft['sources']):
                return self.material_structure(draft, ctx, params['instruction'])
            return self._show_preview(draft, ctx)
        if kind == "supplement":
            draft.setdefault('requirements', []).append(params['instruction'])
            draft['outline'] = None
            draft.pop('estimate', None)
            draft.pop('coverage', None)
            draft.pop('preview', None)
            draft.pop('accepted_preview', None)
            draft['phase'] = 'questions'
            self.save(draft)
            return self._check_sufficiency(draft, ctx)
        if kind in ("question", "ask_user", "refuse"):
            return self._say(draft, params[{"question": "answer", "ask_user": "question", "refuse": "reason"}[kind]])
        if kind == "chat":
            return self._say(draft, "我们先把学习计划定下来吧，有需要随时告诉我。")
        if kind == "update_form":
            return self._update_form(draft, ctx, params)
        if kind == "find_sources":
            if phase not in ("sources", "material_preview", "outline"):
                return self._say(draft, "等需求确认好、开始找资料时再说资料的要求。" if phase == "questions"
                                 else "路线已经生成，不能再换资料了。")
            return self.more_sources(draft, ctx, params["instruction"], echo=False)
        if kind == "decide_sources":
            titles = {True: [], False: []}
            cards = self._decision_cards(draft)
            for adopt, key in ((True, "adopt"), (False, "reject")):
                for n in params[key]:
                    card = cards[n - 1]
                    if self.decide(draft, card["card_id"], adopt, ctx=ctx):
                        titles[adopt].append(f"《{card['title']}》")
            parts = ([f"已采用{'、'.join(titles[True])}"] if titles[True] else []) + \
                    ([f"已不要{'、'.join(titles[False])}"] if titles[False] else [])
            return self._say(draft, "；".join(parts) + "。" if parts else '没有采用新的资料；请查看卡片上的读取原因。')
        if kind == "limit_scope":
            if phase not in ("sources", "material_preview", "outline"):
                return self._say(draft, "现在已经不能改学习范围了。")
            names = {s["id"]: f"{s['source']} · {s['title']}" for s in context["sections"]}
            draft["scope"] = {"include": params["include"], "exclude": params["exclude"]}
            picked = params["include"] or params["exclude"]
            summary = ("只学" if params["include"] else "不学") + "：" + "、".join(names[i] for i in picked[:6]) + \
                      (f" 等 {len(picked)} 部分" if len(picked) > 6 else "")
            if phase in ('outline', 'material_preview'):
                self._say(draft, f"好的，范围改为{summary}。正在按新范围调整路线…")
                return self.make_outline(draft, ctx, "按学习者要求调整学习范围，" + summary, echo=False)
            return self._say(draft, f"记下了，生成路线轮廓时{summary}。")
        if kind == "abandon_scope":
            return self._abandon_scope(draft, ctx, params["items"])
        if kind == 'revise_outline' and phase == 'material_preview':
            return self.material_structure(draft, ctx, params['instruction'])
        if kind == "revise_outline":
            if phase in ('questions', 'preview'):
                return self._carry_out(draft, ctx, plan_intent.Intent('supplement', params), context)
            if phase == "sources":
                return self.make_outline(draft, ctx, params["instruction"], echo=False)
            if phase != "outline":
                return self._say(draft, "路线已经生成，可以直接说要删掉或移动哪个模块。")
            return self.make_outline(draft, ctx, params["instruction"], echo=False)
        if kind == "edit_modules":
            return self._edit_modules(draft, params)
        if kind == "proceed":
            step = params["step"]
            if step == 'preview':
                if phase != 'questions':
                    return self._say(draft, '请先确认大致模块，之后再找资料。')
                return self._show_preview(draft, ctx)
            if step == 'sources':
                previous = (context.get('facts') or {}).get('preview') or {}
                if context.get('phase') != 'preview' or previous.get('version') != (draft.get('preview') or {}).get('version'):
                    return self._say(draft, '大致模块已调整，请先看新版摘要和模块，确认后再找资料。')
                return self._confirm_preview(draft, ctx)
            if step == "outline":
                if phase not in ('sources', 'material_preview'):
                    return self._say(draft, "路线轮廓已经有了。" if phase == "outline" else "现在还不到生成路线的时候。")
                return self.make_outline(draft, ctx, echo=False)
            if step in ('estimate', 'generate') and phase == 'material_preview':
                return self.resolve_missing(draft, ctx, 'continue')
            if step in ("estimate", "generate"):
                if phase != "outline":
                    return self._say(draft, "要先有路线轮廓，才能预估费用和正式生成。")
                if step == "estimate" or not draft.get("estimate"):
                    return self.estimate(draft)
                return self._say(draft, "请确认上面的时间预估，再点“确认，保存并准备第 1 模块”。")
            if phase != "ready":
                return self._say(draft, "计划还没生成完，暂时不能开始。")
            return self._say(draft, "开始后就不能再删模块、调顺序了。确认没问题就点下面的“开始计划”。")
        raise BridgeError("AGENT_FORMAT", "没能理解这句话，请换个说法。")

    def request_map_delete(self, draft, module_keys, points, unknown=()):
        """Queue a local map edit; neither this nor confirmation calls a model."""
        if draft['phase'] != 'outline' or not draft.get('outline'):
            raise BridgeError('PHASE_INVALID', '请先到知识地图这一步，再删除模块或知识点。')
        modules = agents.outline_modules(draft['outline'])
        by_key = {m['key']:m for m in modules}
        if not isinstance(module_keys, list) or not isinstance(points, list):
            raise BridgeError('PARAM_INVALID', '请选择要删除的模块或知识点。')
        keys = list(dict.fromkeys(module_keys))
        if any(k not in by_key for k in keys):
            raise BridgeError('PARAM_INVALID', '模块已变化，请重新选择。')
        selected, names = [], [by_key[k]['title'] for k in keys]
        count = sum(len(by_key[k]['points']) for k in keys)
        for p in points:
            if (not isinstance(p, dict) or set(p) != {'module','n'} or p['module'] not in by_key
                    or type(p['n']) is not int or not 1 <= p['n'] <= len(by_key[p['module']]['points'])):
                raise BridgeError('PARAM_INVALID', '知识点已变化，请重新选择。')
            if p['module'] in keys or p in selected: continue
            selected.append(dict(p))
            names.append(by_key[p['module']]['points'][p['n']-1]['title'])
            count += 1
        draft.pop('pending_map_delete', None)
        if unknown:
            self._say(draft, '没有找到：' + '、'.join(unknown) + '。现有模块：' +
                '、'.join(f"{i}. {m['title']}" for i,m in enumerate(modules, 1)) + '。请选现有模块或知识点。')
        if not names: return
        prompt = '将删掉：' + '、'.join(names) + f'，共 {count} 个知识点，删除后不能恢复，确定吗？'
        draft['pending_map_delete'] = dict(modules=keys, points=selected, text=prompt,
                                           version=draft['outline'].get('version'))
        return self._say(draft, prompt, 'map_delete_confirmation')

    def confirm_map_delete(self, draft, confirm):
        if type(confirm) is not bool:
            raise BridgeError('PARAM_INVALID', '请选择确定或取消。')
        pending = draft.get('pending_map_delete')
        if draft['phase'] != 'outline' or not pending:
            raise BridgeError('PHASE_INVALID', '现在没有待确认的知识地图删除。')
        if pending.get('version') != draft['outline'].get('version'):
            draft.pop('pending_map_delete', None)
            return self._say(draft, '知识地图已变化，请重新选择要删除的内容。')
        draft.pop('pending_map_delete')
        if not confirm: return self._say(draft, '已取消删除，知识地图没有改动。')
        removed_keys = set(pending['modules'])
        removed = list(draft.get('removed_content', []))
        for stage in draft['outline']['stages']:
            for m in stage['modules']:
                if m['key'] in removed_keys:
                    row = dict(kind='module', title=m['title'])
                    if row not in removed: removed.append(row)
                else:
                    deleted = {p['n'] for p in pending['points'] if p['module']==m['key']}
                    for i,p in enumerate(m['points'], 1):
                        if i in deleted:
                            row = dict(kind='point', title=p['title'])
                            if row not in removed: removed.append(row)
                    m['points'] = [p for i,p in enumerate(m['points'], 1) if i not in deleted]
                    m['minutes'] = [sum(p['minutes'] for p in m['points'])]*2
                    m['prerequisites'] = [k for k in m.get('prerequisites', []) if k not in removed_keys]
            stage['modules'] = [m for m in stage['modules'] if m['key'] not in removed_keys]
        draft['outline']['stages'] = [s for s in draft['outline']['stages'] if s['modules']]
        draft['outline']['version'] = draft['outline'].get('version', 0) + 1
        draft['outline'].pop('agent', None)
        draft['messages'].append(_msg('ai', 'outline', '这是删减后的知识地图。', version=draft['outline']['version']))
        draft['removed_content'] = removed
        structure = draft.get('material_structure')
        if structure:
            previous = dict(structure, modules=list(structure['modules']))
            structure['modules'] = [m for m in structure['modules'] if m['key'] not in removed_keys]
            self._remember_removed_content(draft, previous, structure, self._sections(draft)[1])
        modules = agents.outline_modules(draft['outline'])
        draft['outline']['ranges'] = self._content_ranges(draft, modules)
        draft.pop('estimate', None)
        self._say(draft, '已删除所选内容，其余知识点和已采用资料均已保留；时间预估已更新。')
        if modules: self.estimate(draft, annotate=False)

    _FORM_LABELS = {"goal": "学习目标", "final_goal": "最终目标", "level": "当前水平", "level_note": "水平说明",
                    "start": "开始日期", "end": "结束日期", "daily_cap": "每天最多学习时长",
                    "weekday_weights": "每周学习安排", "special_days": "特殊日期", "notes_append": "补充说明"}

    def _update_form(self, draft, ctx, params):
        phase, old = draft["phase"], draft["form"]
        raw = dict(old)
        for key, value in params.items():
            if key == "notes_append":
                raw["notes"] = "\n".join(part for part in (old.get("notes", ""), value.strip()) if part)
            else:
                raw[key] = value
        form, window = validate_form(raw, self.study_day())
        about = ("goal", "final_goal", "level", "level_note", "notes")
        if phase == "ready" and any(form[k] != old[k] for k in about):
            return self._say(draft, "路线已经按原来的目标生成好了，现在只能改日期和每天的时间。想换目标需要重新新建计划。")
        intake = {k: form[k] for k in ("goal", "final_goal", "level", "notes")} if phase != "ready" else {}
        self.services.store.save_intake(draft["plan_id"], window=window, today=self.study_day(),
                                        reason="新建计划时在对话中修改", **intake)
        draft["form"] = dict(form, only_mine=old.get("only_mine", False))
        if "goal" in params:
            draft["name"] = short_plan_name(form["goal"])
            draft["goal"] = form["goal"]
            self.services.store.rename_plan(draft["plan_id"], draft["name"])
            draft["outline"] = None
            draft.pop("estimate", None)
        draft.pop("coverage", None)
        if draft.get("outline"):
            modules = agents.outline_modules(draft["outline"])
            draft["outline"]["ranges"] = self._content_ranges(draft, modules)
        shown = []
        for key in params:
            value = {"level": LEVEL_NAMES.get(form["level"]), "daily_cap": f"{form['daily_cap']} 分钟",
                     "start": form["start"], "end": form["end"]}.get(key)
            shown.append(f"{self._FORM_LABELS[key]}改为 {value}" if value else f"更新了{self._FORM_LABELS[key]}")
        self._say(draft, "已修改：" + "，".join(shown) + "。")
        if phase == 'outline' and not any(form[k] != old[k] for k in about):
            self.estimate(draft, annotate=False)
        if phase in ('questions', 'preview', 'sources', 'outline') and any(form[k] != old[k] for k in about):
            draft['outline'] = None
            draft.pop('preview', None)
            draft.pop('accepted_preview', None)
            draft.pop('estimate', None)
            draft['phase'] = 'questions'
            self.save(draft)
            self._check_sufficiency(draft, ctx)

    def _edit_modules(self, draft, params):
        if draft["phase"] != "ready":
            return self._say(draft, "模块要等正式生成完才能删改。")
        store, plan_id, today = self.services.store, draft["plan_id"], self.study_day()
        rows = store.records("modules", plan_id)
        by_n = dict(enumerate(rows, 1))
        deleted = [by_n[n]["id"] for n in params["delete"]]
        if len(deleted) >= len(rows):
            return self._say(draft, "至少要保留一个模块。")
        order = [m["id"] for m in rows if m["id"] not in deleted]
        for move in params["move"]:  # positions count in the list after deletion, applied in turn
            key = by_n[move["n"]]["id"]
            order.remove(key)
            order.insert(move["to"] - 1, key)
        position = {key: i for i, key in enumerate(order)}
        prerequisites = {m["id"]: m["prerequisites"] for m in rows}
        if any(position[p] > position[m] for m in order for p in prerequisites[m] if p in position):
            return self._say(draft, "这样调整会让模块排在它依赖的内容前面，所以没有改动。")
        for key in deleted:
            store.delete_module(plan_id, key, today=today)
        if params["move"]:
            store.reorder_modules(plan_id, order, today=today)
        titles = {m["id"]: f"《{m['title']}》" for m in rows}
        parts = ([f"删掉{'、'.join(titles[k] for k in deleted)}"] if deleted else []) + \
                ([f"调整了{'、'.join(titles[by_n[m['n']]['id']] for m in params['move'])}的位置"] if params["move"] else [])
        self._say(draft, "已" + "，".join(parts) + "。")

    # ----------------------------------------------------------- 5 review + start
    def _point_minutes_event(self, plan_id, status):
        if getattr(self.services, '_closed', False): return
        try: self.push('plan.point_minutes_changed', dict(plan_id=plan_id, status=status))
        except Exception: log.info('point minutes view closed', exc_info=True)

    def ensure_point_minutes(self, plan_id, *, background_check=False):
        """Start once per unchanged input/version; no model IO on the caller thread."""
        if getattr(self.services, '_closed', False): return 'none'
        shared = self._point_minute_jobs
        with shared['guard']:
            if plan_id in shared['jobs']: return 'running'
            if background_check and plan_id in shared['statuses']:
                return shared['statuses'][plan_id]
            state = None if background_check else self.services.store.point_minutes_snapshot(plan_id)
            if state is not None:
                if state['ready']:
                    shared['statuses'][plan_id] = 'ready'
                    return 'ready'
                if not state['points'] or sum(m.get('point_count', 0) for m in state['modules']) != len(state['points']):
                    return 'none'
            job = dict(cancel=threading.Event(), epoch=shared['epoch'])
            worker = threading.Thread(target=self._point_minutes_worker, args=(plan_id, job, state),
                                      name='plan-point-minutes', daemon=True)
            job['thread'] = worker
            shared['jobs'][plan_id] = job
        self._point_minutes_event(plan_id, 'running')
        worker.start()
        return 'running'

    def _point_minutes_worker(self, plan_id, job, state):
        status = 'failed'
        store = self.services.store
        try:
            if state is None:
                state = store.point_minutes_snapshot(plan_id)
            if state['points'] and all(p.get('structure') == 'concept_v1' for p in state['points']):
                # Directory points were estimated per subpoint. Legacy v2
                # recalculation would cap their combined minutes at 25.
                status = 'ready'
                return
            if state['ready']:
                status = 'ready'
                return
            if not state['points'] or sum(m.get('point_count', 0) for m in state['modules']) != len(state['points']):
                status = 'none'
                return
            difficulties = {}
            for module in state['modules']:
                # A synced result can arrive while a module's model request is
                # in flight. Reuse that completion before spending on the next
                # module; save_point_minutes checks again atomically at commit.
                plan = store.get_plan(plan_id)
                if (plan.get('point_minutes_version') == POINT_MINUTES_VERSION
                        and plan.get('point_minutes_signature') == state['signature']):
                    status = 'ready'
                    return
                points = [p for p in state['points'] if p['module_id'] == module['id']]
                if not points: continue
                if all(p.get('difficulty_fingerprint') == state['point_fingerprints'][p['id']]
                       and p.get('difficulty') in agents.MINUTE_RULE['difficulty'] for p in points):
                    difficulties.update({p['id']: p['difficulty'] for p in points})
                    continue
                if job['cancel'].is_set() or getattr(self.services, '_closed', False): return
                messages, aliases = agents.point_difficulty_request(module, state['points'])
                reply = self.services.gateway.complete('point_difficulty', messages, system=agents.POINT_DIFFICULTY_SYSTEM,
                    cancel=job['cancel'], plan_id=plan_id, max_tokens=4096)
                difficulties.update(agents.parse_point_difficulty(reply if isinstance(reply, str) else reply.text, aliases))
            if job['cancel'].is_set() or getattr(self.services, '_closed', False): return
            job['writer'] = threading.get_ident()
            if store.save_point_minutes(plan_id, state['fingerprint'], difficulties, today=self.study_day()): status = 'ready'
        except Exception:
            if not getattr(self.services, '_closed', False): log.exception('point minutes failed plan=%s; original minutes retained', plan_id)
        finally:
            with self._point_minute_jobs['guard']:
                self._point_minute_jobs['jobs'].pop(plan_id, None)
                if self._point_minute_jobs['epoch'] == job.get('write_epoch', job['epoch']):
                    self._point_minute_jobs['statuses'][plan_id] = status
            self._point_minutes_event(plan_id, status)
            if status == 'ready' and not getattr(self.services, '_closed', False):
                # Classroom's existing snapshot refresh also reloads its ledger projection.
                try: self.push('classroom.materials_ready', dict(plan_id=plan_id, reason='point_minutes'))
                except Exception: log.info('point minutes classroom closed', exc_info=True)

    def _point_links_event(self, plan_id, status):
        if getattr(self.services, '_closed', False): return
        try: self.push('plan.point_links_changed', dict(plan_id=plan_id, status=status))
        except Exception: log.info('point links view closed', exc_info=True)

    def ensure_point_links(self, plan_id, *, retry=False):
        """Background only; detail/retry never wait for model IO. Shared per account."""
        if getattr(self.services, '_closed', False): return 'none'
        shared = self._point_link_jobs
        with shared['guard']:
            if plan_id in shared['jobs']:
                if retry: shared['jobs'][plan_id]['retry'] = True
                return 'running'
            state = self.services.store.point_links_state(plan_id)
            targets = state['dirty'] + (state['failed'] if retry else [])
            if not targets: return state['status']
            cancel = threading.Event()
            job = dict(cancel=cancel, retry=retry)
            shared['jobs'][plan_id] = job
            worker = threading.Thread(target=self._point_links_worker, args=(plan_id, job),
                                      name='plan-point-links', daemon=True)
            job['thread'] = worker
        self._point_links_event(plan_id, 'running')
        worker.start()
        return 'running'

    def _point_links_worker(self, plan_id, job):
        store, tried = self.services.store, set()
        try:
            while not job['cancel'].is_set() and not getattr(self.services, '_closed', False):
                state = store.point_links_state(plan_id)
                targets = set(state['dirty'] + (state['failed'] if job['retry'] else []))
                module = next((m for m in state['modules'] if m['id'] in targets and
                    (m['id'], state['fingerprints'][m['id']]) not in tried), None)
                if module is None: break
                fingerprint = state['fingerprints'][module['id']]
                tried.add((module['id'], fingerprint))
                messages, aliases = agents.point_links_request(module, state['points'])
                links, dropped, error = [], {}, None
                for attempt in range(2):
                    try:
                        reply = self.services.gateway.complete('point_links', messages, system=agents.POINT_LINKS_SYSTEM,
                            cancel=job['cancel'], plan_id=plan_id, max_tokens=4096)
                        text = reply if isinstance(reply, str) else reply.text
                        links, dropped = agents.validate_point_links(json.loads(text), aliases, module, state['points'])
                        error = None
                        break
                    except ModelCancelled:
                        return
                    except Exception as exc:
                        if job['cancel'].is_set() or getattr(self.services, '_closed', False): return
                        error = getattr(exc, 'code', type(exc).__name__)
                        log.warning('point links module=%s attempt=%d error=%s', module['id'], attempt + 1, error)
                if job['cancel'].is_set() or getattr(self.services, '_closed', False): return
                store.save_point_links(plan_id, module['id'], fingerprint, links, dropped=dropped, error=error)
                # Notify after each committed module, including partial failures.
                self._point_links_event(plan_id, 'running')
        except Exception:
            if not getattr(self.services, '_closed', False): log.exception('point links worker failed plan=%s', plan_id)
        finally:
            with self._point_link_jobs['guard']:
                self._point_link_jobs['jobs'].pop(plan_id, None)
            if not getattr(self.services, '_closed', False):
                try: self._point_links_event(plan_id, store.point_links_state(plan_id)['status'])
                except Exception: log.info('point links store closed', exc_info=True)

    def retry_point_links(self, plan_id):
        return dict(status=self.ensure_point_links(plan_id, retry=True))

    def retry_point_minutes(self, plan_id):
        with self._point_minute_jobs['guard']:
            self._point_minute_jobs['statuses'].pop(plan_id, None)
        return dict(status=self.ensure_point_minutes(plan_id, background_check=True))

    def preparation_status(self, plan_id, modules):
        """Project persisted completion/errors and this account's live queue only."""
        with self._material_jobs['guard']:
            states = []
            for module in modules:
                job = self._material_jobs['jobs'].get((plan_id, module['id']))
                saved = (module.get('material_preparation') or {}).get('state')
                state = job['status'] if job and not job['cancel'].is_set() else (
                    'ready' if saved == 'done' else 'failed' if saved == 'failed' else 'none')
                states.append(state)
        done, failed = states.count('ready'), states.count('failed')
        status = ('running' if any(s in ('running', 'queued') for s in states) else
                  'ready' if states and done == len(states) else
                  'failed' if states and failed == len(states) else 'partial' if failed else 'none')
        return dict(status=status, done=done, failed=failed, total=len(states))

    def detail(self, plan_id):
        store = self.services.store
        plan = store.get_plan(plan_id)
        from .settings import resolve_teacher_preset
        preset = resolve_teacher_preset(getattr(self.services, 'settings', None), plan)
        stages = store.records("stages", plan_id)
        modules = store.records("modules", plan_id)
        points = store.records("points", plan_id)
        started = plan.get("state") in ("active", "checking", "completed")
        book = store.load_mastery(plan_id) if started else None
        taught = {row['credit']['unit_id'] for row in store.records('credits', plan_id)
                  if row['id'].startswith(('learn:', 'relearn:'))} if started else set()
        from ..course.question_bank import review_points
        bank = store.records('question_bank', plan_id)
        retired = {q['id'] for q in bank if q.get('role')=='retired'}
        review = review_points([r for r in store.records('practice_results', plan_id)
                                if r['question_id'] not in retired]) if started else set()
        by_module: dict[str, list] = {}
        sources=store.records('sources',plan_id)
        chunks=store.records('source_chunks',plan_id)
        # Detail is a read projection. Classify each source once, rather than
        # rebuilding and hashing an entire book for every knowledge point.
        source_chunks: dict[str, list] = {}
        chunk_source = {}
        for chunk in chunks:
            source_chunks.setdefault(chunk['source_id'], []).append(chunk)
            chunk_source[chunk['id']] = chunk['source_id']
        lists = self._lists()
        general = {s['id']: source_quality.assess_stored(s, source_chunks.get(s['id'], []), '', lists=lists)['credibility'] == 3
                   for s in sources if not (s.get('adopted') or s.get('user_provided'))}
        bank_counts: dict[str, list[int]] = {}
        from ..course.question_bank import assessment_questions
        for question in assessment_questions(bank):
            counts = bank_counts.setdefault(question['module_id'], [0, 0])
            counts[0] += 1
            counts[1] += int(bool(book and book.question(question['id']).passed))
        for point in points:
            cids = point['chunk_ids']
            by_module.setdefault(point["module_id"], []).append(
                {"id": point["id"], "title": point.get("title", ""), "summary": point["summary"], "minutes": point["minutes"],
                 "citations": point.get("citations", []),
                 **({'structure': point['structure'], 'subpoints': point['subpoints']} if point.get('structure') == 'concept_v1' else {}),
                 "source_general": bool(cids) and all(cid in chunk_source and general.get(chunk_source[cid], False) for cid in cids),
                 "needs_review": point['id'] in review, "taught": point['id'] in taught})
        result = {"plan": {k: plan.get(k) for k in ("id", "title", "goal", "final_goal", "state", "window", "total_minutes", "module_count")},
                  "teacher_preset": {k: preset[k] for k in ('id', 'name')},
                  "stages": [{"id": s["id"], "title": s.get("title", ""), "modules": [
                      {"id": m["id"], "title": m["title"], "summary": m.get("summary", ""), "learning_minutes": m["learning_minutes"],
                       "check_minutes": m["check_minutes"], "points": by_module.get(m["id"], []),
                       "navigation": [dict(n, citations=[dict(chunk_id=c['id'], title=c.get('section', ['资料'])[-1] if c.get('section') else '资料',
                           quote=c['text'], locator=' / '.join(c.get('section') or []), url=None)
                           for c in chunks if c['id'] in n['chunk_ids']]) for n in m.get('navigation', [])],
                       "prerequisites": m.get("prerequisites", []),
                       "state": book.module(m["id"]).state if book else None,
                       "question_count": bank_counts.get(m['id'], [0, 0])[0],
                       "passed_questions": bank_counts.get(m['id'], [0, 0])[1],
                       "cloze_heavy": bool((m.get('material_preparation') or {}).get('cloze_heavy'))}
                      for m in modules if m["stage_id"] == s["id"]]} for s in stages],
                  "sources": [{"id": s["id"], "title": s.get("title") or s.get("url") or s.get("filename"), "url": s.get("url"),
                               "kind": s["kind"]} for s in store.records("sources", plan_id) if s.get("adopted")]}
        result["stages"] = [s for s in result["stages"] if s["modules"]]
        if any((m.get('question_extraction') or {}).get('reason')=='material_format_upgrade'
               and (m.get('material_preparation') or {}).get('state')!='done' for m in modules):
            result['migration_message']='旧题已退出考核，学习进度已保留；相关模块的题会按资料重新准备。'
        if plan.get("window") and (started or plan.get("total_minutes")):
            from ..course.store import _window
            window = _window(plan["window"])
            cap = (store.load_ledger(plan_id).scheduled_cap(self.study_day()) if started
                   else check_cap(window, plan["total_minutes"]))
            result["daily"] = {"full_day": cap.full_day_minutes, "cap": cap.cap, "ok": cap.ok,
                               "suggested_end": cap.suggested_end.isoformat() if cap.suggested_end else None}
        if started and plan.get("window"):
            status = store.load_ledger(plan_id).status(self.study_day())
            result["progress"] = {"remaining": status.remaining_minutes, "overdue_days": getattr(status, "overdue_days", 0)}
        result["has_report"] = plan.get("state") == "completed"
        result['point_links'] = store.point_links_state(plan_id)['links']
        result['point_links_status'] = self.ensure_point_links(plan_id)
        result['point_minutes_status'] = self.ensure_point_minutes(plan_id, background_check=True)
        result['preparation'] = self.preparation_status(plan_id, modules)
        result['preparation_status'] = result['preparation']['status']
        return result

    def change_window(self, plan_id, raw):
        """PLAN-08: a started plan may change its end date, daily cap and study days; only unlearnt time moves."""
        store, today = self.services.store, self.study_day()
        plan = store.get_plan(plan_id)
        if plan.get("state") not in ("active", "checking") or not plan.get("window"):
            raise BridgeError("PHASE_INVALID", "只有进行中的计划可以在这里改日期和时长。")
        from ..course.store import _window
        old = _window(plan["window"])
        end = _day(raw.get("end", old.end.isoformat()), "结束日期")
        cap = raw.get("daily_cap", old.daily_cap_minutes)
        weights = raw.get("weekday_weights", list(old.weekday_weights))
        if end < max(today, old.start):
            raise BridgeError("FORM_INVALID", "结束日期不能早于今天。")
        if (end - old.start).days > 3 * 366:
            raise BridgeError("FORM_INVALID", "计划最长三年。")
        if type(cap) is not int or not 10 <= cap <= 720:
            raise BridgeError("FORM_INVALID", "每天最多学多久请填 10–720 分钟。")
        if (not isinstance(weights, list) or len(weights) != 7
                or any(w not in (0, 0.5, 1) or type(w) is bool for w in weights)):
            raise BridgeError("FORM_INVALID", "每周可学的日子格式不对。")
        special = {day: weight for day, weight in old.date_weights.items() if old.start <= day <= end}
        window = PlanWindow(old.start, end, cap, tuple(float(w) for w in weights), special)
        try:
            if not distribute(window, 1):
                raise LedgerError("NO_DAYS", "")
        except LedgerError:
            raise BridgeError("FORM_INVALID", "这样设置后没有可以学习的日子，请调整。") from None
        store.save_intake(plan_id, window=window, today=today, reason="在计划详情里修改日期与每天时长")
        return self.detail(plan_id)

    # ----------------------------------------------------------- PLAN-10: add materials to a started plan
    def _exclusive(self, plan_id, work):
        lock = self._lock(plan_id)
        if not lock.acquire(blocking=False):
            raise BridgeError("PLAN_BUSY", "上一步还在进行，请稍等。")
        try:
            return work()
        except ModelCancelled:
            raise BridgeError("CANCELLED", "已停止。") from None
        except ModelError as exc:
            raise BridgeError(exc.code, f"{exc.message} 可以稍后重试。") from None
        finally:
            self.status(plan_id, "")
            lock.release()

    def _addition(self, addition_id):
        session = self._additions.get(addition_id) if isinstance(addition_id, str) else None
        if session is None:
            raise BridgeError("ADDITION_EXPIRED", "这次加资料的分析已失效（软件重启过或已应用），请重新选择资料。")
        return session

    def additions_analyze(self, plan_id, token, ctx, urls=None):
        from ..course import plan_additions as pa
        from ..course.materials import import_file
        paths = self._picked.pop(token, None) if isinstance(token, str) else None
        urls = find_links(" ".join(urls)) if isinstance(urls, list) and all(isinstance(u, str) for u in urls) else []
        if not paths and not urls:
            raise BridgeError("PARAM_INVALID", "请先选择资料文件或贴上网址。")

        def work():
            store = self.services.store
            plan = store.get_plan(plan_id)
            if plan.get("state") != "active" or plan.get("archived"):
                raise BridgeError("PHASE_INVALID", "只有进行中的计划可以中途加资料。")
            from ..course.staged_materials import StagedMaterials
            staged = StagedMaterials(store, plan_id)
            source_ids, failed = [], []
            for path in paths or []:
                self.status(plan_id, f"正在读取 {path.name}…")
                source_ids.append(import_file(staged, plan_id, path, cancel=ctx.cancel)["id"])
            if urls:
                from ..course.addition_links import import_links
                self.status(plan_id, f"正在打开 {len(urls)} 个网址…")
                linked = import_links(staged, plan_id, urls, fetcher=self.fetcher, lists=self._lists(), cancel=ctx.cancel,
                                      **({"repo_fetch": self.repo_fetch} if self.repo_fetch else {}))
                source_ids += [i for i in linked["source_ids"] if i not in source_ids]
                failed = [f"{r['original_url']}：{r.get('message') or '打不开'}" for r in linked["results"]
                          if r["status"] in ("failed", "cancelled")]
            if not source_ids:
                raise BridgeError("NO_NEW_MATERIAL", "没有可以加入的新资料。" + ("；".join(failed) if failed else ""))
            quality_rows = source_quality.annotate(staged, plan_id, source_ids, lists=self._lists(), today=self.study_day())
            self.status(plan_id, "正在分析新资料和现有路线的关系（通常半分钟左右）…")
            state = pa.snapshot(staged, plan_id, source_ids)
            proposal, _ = self._ask("plan_additions", pa.analysis_request(state),
                                    lambda text: _as_format_error(pa.parse_analysis, text, state), ctx,
                                    plan_id=plan_id, max_tokens=16384)
            addition_id = _new_id()
            self._additions[addition_id] = {"plan_id": plan_id, "state": state,
                                            "proposal": proposal, "staged": staged}
            return {"addition_id": addition_id, "preview": pa.preview(state, proposal, today=self.study_day()),
                    "failed": failed, "source_quality": quality_rows}
        return self._exclusive(plan_id, work)

    def additions_preview(self, addition_id, selected, new_end):
        from ..course import plan_additions as pa
        session = self._addition(addition_id)
        return pa.preview(session["state"], session["proposal"], today=self.study_day(), selected=selected,
                          new_end=_day(new_end, "结束日期") if new_end else None)

    def additions_generate(self, addition_id, selected, new_end, ctx):
        """Points only for the chosen new modules, then the exact preview the learner confirms."""
        from ..course import plan_additions as pa
        session = self._addition(addition_id)
        state, proposal, plan_id = session["state"], session["proposal"], session["plan_id"]

        def one(job):
            for attempt in range(2):
                try:
                    return self.services.gateway.complete("plan_additions_generate", job["messages"], system=job["system"],
                                                          cancel=ctx.cancel, plan_id=plan_id, max_tokens=8192).text
                except ModelError as exc:
                    if attempt or exc.code not in TRANSIENT | {MODEL_OUTPUT_TRUNCATED} or ctx.cancel.is_set():
                        raise

        def work():
            last = None
            for attempt in range(2):   # one retry when a generated batch fails validation
                jobs = pa.generation_jobs(state, proposal, selected=selected, cancel=ctx.cancel)
                self.status(plan_id, f"正在为新增的内容生成知识点（{len(jobs)} 批）…")
                with ThreadPoolExecutor(max_workers=GENERATE_WORKERS, thread_name_prefix="plan-addition") as pool:
                    responses = dict(zip([j["id"] for j in jobs], pool.map(one, jobs)))
                try:
                    generated = pa.finish_generation(state, proposal, responses, selected=selected, cancel=ctx.cancel)
                    break
                except (pa.AdditionError, agents.AgentFormatError) as exc:
                    if getattr(exc, "code", "") == "CANCELLED":
                        raise ModelCancelled() from None
                    last = exc
                    log.info("addition generation rejected (%s), attempt %s", exc, attempt + 1)
            else:
                raise ModelError("AGENT_FORMAT", "新增内容的生成结果格式不对，已重试一次仍不行。") from last
            exact = pa.preview(state, proposal, today=self.study_day(), selected=selected,
                               new_end=_day(new_end, "结束日期") if new_end else None, generated=generated)
            session.update(generated=generated, exact=exact)
            return exact
        return self._exclusive(plan_id, work)

    def additions_apply(self, addition_id, confirmation_id):
        from ..course import plan_additions as pa
        session = self._addition(addition_id)
        exact = session.get("exact")
        if not exact or exact.get("confirmation_id") != confirmation_id:
            raise BridgeError("PREVIEW_CHANGED", "预览已经变化，请重新生成后再确认。")

        def work():
            result = pa.apply_additions(self.services.store, session["state"], session["proposal"], session["generated"],
                                        exact, confirmed=True, today=self.study_day(), staged=session['staged'])
            self._additions.pop(addition_id, None)
            return result
        return self._exclusive(session["plan_id"], work)

    def additions_cancel(self, addition_id):
        self._additions.pop(addition_id, None)

    def report(self, plan_id, *, comments=True, cancel=None):
        """RPT-01: the saved learning report, built once the plan and its final check are complete."""
        from ..course.report import build_report
        store = self.services.store
        try:
            report = store.load_report(plan_id)
        except StoreError:
            report = None
        if not report:
            report = build_report(store, plan_id, today=self.study_day())
        if comments and 'point_comments' not in report:
            from ..course.report_comments import generate_comments
            with self._lock(plan_id):
                report = store.load_report(plan_id)
                if 'point_comments' not in report:
                    report = dict(report, point_comments=generate_comments(None, store, plan_id, report))
                    store.save_report(plan_id, report)
        titles = {p["id"]: p.get("title") or p["summary"][:30] for p in store.records("points", plan_id)}
        from ..course.report_views import report_views
        return {"report": report, "titles": titles, "views": report_views(report)}

    def edit_module(self, draft, action, module_id, direction=0):
        if draft["phase"] != "ready":
            raise BridgeError("PHASE_INVALID", "只有生成完、还没开始时可以调整模块。")
        store, plan_id, today = self.services.store, draft["plan_id"], self.study_day()
        if action == "delete":
            if len(store.records("modules", plan_id)) <= 1:
                raise BridgeError("LAST_MODULE", "至少要保留一个模块。")
            store.delete_module(plan_id, module_id, today=today)
        else:
            order = [m["id"] for m in store.records("modules", plan_id)]
            if module_id not in order or direction not in (-1, 1):
                raise BridgeError("PARAM_INVALID", "模块或方向无效。")
            index = order.index(module_id)
            target = index + direction
            if not 0 <= target < len(order):
                return
            order[index], order[target] = order[target], order[index]
            prerequisites = {m["id"]: m["prerequisites"] for m in store.records("modules", plan_id)}
            position = {key: i for i, key in enumerate(order)}
            if any(position[p] > position[m] for m in order for p in prerequisites[m] if p in position):
                raise BridgeError("ORDER_INVALID", "这样调整会让模块排在它依赖的内容前面。")
            try:
                store.reorder_modules(plan_id, order, today=today)
            except StoreError as exc:
                if exc.code in ("CYCLIC_PREREQUISITES", "MISSING_PREREQUISITE"):
                    raise BridgeError("ORDER_INVALID", "这样调整会让模块排在它依赖的内容前面。") from None
                raise
        self.save(draft)

    def extend_end(self, draft, end):
        if draft["phase"] not in ("material_preview", "outline", "ready"):
            raise BridgeError("PHASE_INVALID", "现在不能改日期。")
        new_end = _day(end, "结束日期")
        window = self._window(draft)
        if new_end <= window.end or (new_end - window.start).days > 3 * 366:
            raise BridgeError("FORM_INVALID", "新的结束日期要晚于原来的，且计划最长三年。")
        self._update_form(draft, None, {'end': new_end.isoformat()})

    def adjust_window(self, draft, ctx, params):
        """Estimate buttons use the existing intent validation and form-change path, without a model."""
        if draft['phase'] not in ('material_preview', 'outline'):
            raise BridgeError('PHASE_INVALID', '请先确认资料结构或知识地图。')
        if not isinstance(params, dict) or not params or set(params) - {'daily_cap', 'end'}:
            raise BridgeError('PARAM_INVALID', '只能修改每日时长或结束日期。')
        context = self._intent_context(draft)
        try:
            intents = plan_intent.parse_intents(json.dumps({'intents': [
                dict(kind='update_form', **params)], 'reply': ''}), context)
        except agents.AgentFormatError:
            raise BridgeError('FORM_INVALID', '请填写有效的每日时长或结束日期。') from None
        self._carry_out(draft, ctx, intents[0], context)

    def start(self, draft):
        with self.services.store.transaction():
            plan = self.services.store.get_plan(draft['plan_id'])
            if plan['state'] not in ('active', 'checking', 'completed'):
                if draft["phase"] != "ready":
                    raise BridgeError("PHASE_INVALID", "计划还没生成完。")
                self.services.store.start_plan(draft["plan_id"], today=self.study_day())
        self._file(draft["plan_id"]).path.unlink(missing_ok=True)
        self.push("plan_started", {"plan_id": draft["plan_id"]})

    def start_request(self, plan_id):
        """Bridge entry remains idempotent even after the draft file is gone."""
        lock = self._lock(plan_id)
        if not lock.acquire(blocking=False):
            raise BridgeError('PLAN_BUSY', '上一步还在进行，请稍等。')
        try:
            plan = self.services.store.get_plan(plan_id)
            already_started = plan['state'] in ('active', 'checking', 'completed')
            if already_started:
                draft = {'plan_id': plan_id, 'phase': 'ready'}
            else:
                draft = self.load(plan_id)
            self.start(draft)
            return {'started': True} if already_started else {'draft': self.public(draft)}
        finally:
            self.status(plan_id, '')
            lock.release()


def register(bridge, services, window, study_day, *, fetcher=None):
    flows: dict[int, PlanFlow] = {}

    def flow():
        current = services()
        key = id(current)
        if key not in flows:
            flows.clear()
            flows[key] = PlanFlow(current, bridge.push, lambda: study_day(current.settings.load()), fetcher=fetcher)
        return flows[key]

    def plan_id(params):
        value = params.get("plan_id")
        if not isinstance(value, str) or not _PLAN_ID.fullmatch(value):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        return value

    def _keys(params):
        value = params.get("selected")
        if value is None:
            return None
        if not isinstance(value, list) or len(value) > 50 or any(not isinstance(v, str) or len(v) > 40 for v in value):
            raise BridgeError("PARAM_INVALID", "选择的模块无效。")
        return value

    def text(params, key="text", limit=2000):
        value = params.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise BridgeError("PARAM_INVALID", "内容不能为空，也不能太长。")
        return value.strip()

    def wrap(fn):
        def run(params, ctx):
            try:
                return fn(params, ctx)
            except StoreError as exc:
                raise BridgeError("PLAN_STORE", exc.message) from None
            except LedgerError as exc:
                raise BridgeError("PLAN_WINDOW", str(exc) or "日期设置无效。") from None
            except ValueError as exc:
                from ..course.plan_additions import AdditionError
                if isinstance(exc, AdditionError):
                    raise BridgeError(exc.code, str(exc)) from None
                raise
        return run

    handlers = {
        "plan.current": lambda p, c: {"draft": flow().public(flow().current())},
        "plan.get": lambda p, c: {"draft": flow().get_draft(plan_id(p))},
        "plan.drafts": lambda p, c: {"drafts": flow().drafts()},
        "plan.discard": lambda p, c: flow().discard(plan_id(p)),
        "plan.delete": lambda p, c: flow().delete_started(plan_id(p)),
        "plan.submit": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().submit_form(d, x, p.get("form")), c),
        "plan.say": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().chat(d, x, text(p)), c),
        "plan.map_delete": lambda p,c: flow().run(plan_id(p), lambda d,x: flow().request_map_delete(d, p.get('modules', []), p.get('points', [])), c),
        "plan.map_delete_confirm": lambda p,c: flow().run(plan_id(p), lambda d,x: flow().confirm_map_delete(d, p.get('confirm')), c),
        "plan.more": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().more_sources(d, x), c),
        "plan.decide": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().decide(d, text(p, "card_id", 40), p.get("adopt") is True, ctx=x), c),
        "plan.missing": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().resolve_missing(d, x, text(p, 'action', 20)), c),
        "plan.outline": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().make_outline(d, x), c),
        "plan.estimate": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().estimate(d), c),
        "plan.window": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().adjust_window(d, x, p.get('changes')), c),
        "plan.generate": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().generate(d, x), c),
        "plan.module": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().edit_module(
            d, "delete" if p.get("action") == "delete" else "move", text(p, "module_id", 40), p.get("direction", 0)), c),
        "plan.extend": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().extend_end(d, p.get("end")), c),
        "plan.start": lambda p, c: flow().start_request(plan_id(p)),
        "plan.detail": lambda p, c: flow().detail(plan_id(p)),
        "plan.retry_point_links": lambda p, c: flow().retry_point_links(plan_id(p)),
        "plan.retry_point_minutes": lambda p, c: flow().retry_point_minutes(plan_id(p)),
        "plan.window": lambda p, c: flow().change_window(plan_id(p), p),
        "plan.rename": lambda p, c: services().store.rename_plan(plan_id(p), text(p, "title", 80)) or {"ok": True},
        "plan.archive": lambda p, c: (services().store.archive_plan(plan_id(p)) if p.get("archived") is True
                                      else services().store.restore_plan(plan_id(p))) or {"ok": True},
        "plan.report": lambda p, c: flow().report(plan_id(p), cancel=c.cancel if c else None),
        "plan.add_analyze": lambda p, c: flow().additions_analyze(plan_id(p), p.get("token"), c, p.get("urls")),
        "plan.add_preview": lambda p, c: flow().additions_preview(p.get("addition_id"), _keys(p), p.get("new_end")),
        "plan.add_generate": lambda p, c: flow().additions_generate(p.get("addition_id"), _keys(p), p.get("new_end"), c),
        "plan.add_apply": lambda p, c: flow().additions_apply(p.get("addition_id"), p.get("confirmation_id")),
        "plan.add_cancel": lambda p, c: flow().additions_cancel(p.get("addition_id")),
    }
    for name, fn in handlers.items():
        bridge.register(name, wrap(fn))

    def upload(params, ctx):
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        pid = plan_id(params)
        f = flow()
        paths, _ = QFileDialog.getOpenFileNames(window, "选择学习资料", str(Path.home()),
                                                "学习资料 (*.pdf *.docx *.pptx *.epub *.xlsx *.md *.txt *.html *.htm);;所有文件 (*)")
        if not paths:
            return {"files": 0}

        def work():
            for path in paths[:10]:
                try:
                    f.add_file(pid, Path(path))
                except Exception as exc:  # noqa: BLE001 - report each file, keep going
                    message = getattr(exc, "message", None) or "读取失败，请确认文件没有损坏。"
                    try:
                        draft = f.load(pid)
                        draft["messages"].append(_msg("ai", "error", f"《{Path(path).name}》没能导入：{message}"))
                        f.save(draft)
                    except BridgeError:
                        pass
        threading.Thread(target=work, name="plan-upload", daemon=True).start()
        return {"files": min(len(paths), 10)}

    bridge.register("plan.upload", upload, ui=True)

    def pick_additions(params, ctx):
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        plan_id(params)
        paths, _ = QFileDialog.getOpenFileNames(window, "选择要加入的学习资料", str(Path.home()),
                                                "学习资料 (*.pdf *.docx *.pptx *.epub *.xlsx *.md *.txt *.html *.htm);;所有文件 (*)")
        if not paths:
            return {"token": None, "names": []}
        token = _new_id()
        flow()._picked[token] = [Path(p) for p in paths[:10]]
        return {"token": token, "names": [Path(p).name for p in paths[:10]]}

    bridge.register("plan.add_pick", pick_additions, ui=True)

    def export_report(params, ctx):
        """Save the report as a Markdown file the learner keeps (RPT-01); no sharing features."""
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        pid = plan_id(params)
        data = flow().report(pid, comments=False)
        name = (data["report"]["plan"]["goal"] or "学习报告").strip()[:40]
        path, _ = QFileDialog.getSaveFileName(window, "保存学习报告", str(Path.home() / f"{name}-学习报告.md"), "Markdown (*.md)")
        if not path:
            return {"saved": False}
        Path(path).write_text(report_markdown(data["report"], data["titles"]), encoding="utf-8")
        return {"saved": True, "path": path}

    bridge.register("plan.report_export", wrap(export_report), ui=True)

    def export_report_pdf(params, ctx):
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        from ..course.report_pdf import render_report_pdf
        pid = plan_id(params)
        data = flow().report(pid, comments=False)
        name = (data['report']['plan']['goal'] or '学习报告').strip()[:40]
        path, _ = QFileDialog.getSaveFileName(window, '保存学习报告 PDF',
            str(Path.home() / f'{name}-学习报告.pdf'), 'PDF (*.pdf)')
        if not path:
            return {'saved': False}
        render_report_pdf(data['report'], data['titles'], data['views'], Path(path))
        return {'saved': True, 'path': path}

    bridge.register('plan.report_pdf', wrap(export_report_pdf), ui=True)


def _as_format_error(parse, text, state):
    """The component's own validation errors count as a malformed answer, so the host retries once."""
    from ..course.plan_additions import AdditionError
    try:
        return parse(text, state)
    except AdditionError as exc:
        raise agents.AgentFormatError(str(exc)) from None


def report_markdown(report, titles):
    from ..course.report_views import report_views
    views = report_views(report)
    plan = report["plan"]
    lines = [f"# 学习报告：{plan['goal']}", "", f"最终目标：{plan['final_goal']}", "",
             f"- 计划日期：{plan['start_date']} → {plan['end_date']}",
             f"- 实际开始：{plan.get('actual_start_date') or '—'}；完成：{plan.get('actual_completion_date') or '—'}",
             f"- 学习时长：{plan['credited_minutes']} 分钟（计划 {plan['planned_minutes']} 分钟）",
             f"- 调整过计划 {plan['replan_count']} 次", ""]
    checks = sum(p['check_questions'] for p in report.get('questions',[]))
    correct = sum(p['check_correct'] for p in report.get('questions',[]))
    has_questions = bool(report.get('questions'))
    lines.insert(-1, f"- 最终检验：{correct}/{checks} 题答对" if checks else
                 "- 最终检验：尚未进行。" if has_questions else "- 资料没有题，本计划只学习，不考核。")
    if plan.get("early_days"):
        lines.append(f"比截止日提前 {plan['early_days']} 天完成。")
    if plan.get("overdue_days"):
        lines.append(f"比截止日晚了 {plan['overdue_days']} 天完成。")
    lines += ["", "## 模块", ""]
    lines += [f"- {m['title']}：{ {'passed': '通过', 'skipped': '直接考通过', 'failed': '未通过'}.get(m['state'], m['state']) }"
              for m in report["modules"]]
    if has_questions:
        lines += ["", "## 各模块掌握情况", ""]
        lines += [f"- {m['title']}：{m['mastered']}/{m['total']} 道题通过最终检验" if m['total'] else f"- {m['title']}：只学不考"
                  for m in views['modules']]
        lines += ["", "## 错误分布", "",
                  f"- 随口问：{views['errors']['casual']} 次；模块检测：{views['errors']['module']} 次；最终检验：{views['errors']['check']} 次"]
    lines += ["", "## 每日目标与实际 / 历史明细", "",
              "按原始学习日统计实际完成；每天单独比较当天目标与实际，不是累计欠账。", ""]
    lines += [f"- {day['day']}：目标 {day['target_minutes']} 分，实际 {day['actual_minutes']} 分，"
              f"比当天目标少 {day['debt_minutes']} 分，多 {day['ahead_minutes']} 分"
              for day in views['history']]
    if report.get("live_points"):
        lines += ["", "## 老师现场讲的知识点", "", "以下知识点由老师结合资料现场讲解：", ""]
        lines += [f"- {p['title']}" for p in report["live_points"]]
    lines += ["", "## 最需要注意的知识点", ""]
    lines += [f"- {titles.get(w['point_id'], w['point_id'])}（出错 {w['score']} 次）" for w in report["weakest_points"] if w["score"]] \
        or ["- 没有明显的薄弱点。"]
    lines += ["", "## 逐点老师评语", ""]
    comments = report.get('point_comments') or {}
    lines += [f"- {titles.get(p['point_id'], p['summary'])}：{comments.get(p['point_id']) or '暂无'}"
              for p in report['points']]
    profile = report.get("teacher_profile") or {}
    if profile.get("check"):
        lines += ["", "## 问题归纳", "", profile["check"]]
    if profile.get("text"):
        lines += ["", "## 老师的记录", "", profile["text"]]
    return "\n".join(lines) + "\n"
