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

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import logging
import math
import re
import threading
import uuid

from ..connections.errors import MODEL_OUTPUT_TRUNCATED, ModelCancelled, ModelError
from ..course import plan_intent
from ..course import planner_agents as agents
from ..course.request_layout import dialogue_tail
from ..course import source_quality
from ..course.ledger import LedgerError, PlanWindow, check_cap, distribute, estimate_check_minutes, full_day_minutes
from ..course.store import StoreError
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
    if type(cap) is not int or not 10 <= cap <= 720:
        raise BridgeError("FORM_INVALID", "每天最多学多久请填 10–720 分钟。")
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


MINUTES_PER_POINT_GUESS = 15  # PLAN-04: each generated point is about 10–20 learning minutes


def with_checks(learning_minutes: int) -> int:
    """Learning plus the same module-test and final-check estimate generation uses (PLAN-05).

    Checks take close to half of a plan (end-to-end 2026-09-29: 149 learning + 54 module
    test + 71 final check); a small flat reserve made users plan far too little time.
    """
    points = max(1, round(learning_minutes / MINUTES_PER_POINT_GUESS))
    question = max(1, round(CHECK_MINUTES_PER_QUESTION))
    return learning_minutes + points * question + estimate_check_minutes(points, minutes_per_question=question)


def minute_ranges(window: PlanWindow, low: int, high: int) -> dict:
    """Totals include module tests and the final check; daily figures are for a full study day."""
    lo_total, hi_total = with_checks(low), with_checks(high)
    lo_day = full_day_minutes(distribute(window, lo_total), window)
    hi_day = full_day_minutes(distribute(window, hi_total), window)
    cap = check_cap(window, hi_total)
    return {"total": [lo_total, hi_total], "daily": [lo_day, hi_day], "cap": window.daily_cap_minutes,
            "over_cap": hi_day > window.daily_cap_minutes,
            "suggested_end": cap.suggested_end.isoformat() if cap.suggested_end else None}


class PlanFlow:
    def __init__(self, services, push, study_day, *, fetcher=None):
        self.services, self.push, self.study_day = services, push, study_day
        self.fetcher = fetcher  # injected by offline tests; None = real guarded web fetch
        from ..course import github_repo
        self.repo_fetch = github_repo.default_fetch  # replaced by offline tests
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._status: dict[str, str] = {}
        self._pools: dict[str, object] = {}
        self._verified: dict[str, dict] = {}
        self._additions: dict[str, dict] = {}   # PLAN-10 sessions: the host holds state, the page only picks
        self._picked: dict[str, list] = {}      # files chosen in the dialog, by token

    # ----------------------------------------------------------- drafts
    def _file(self, plan_id):
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        return AccountJSON(self.services.account_id, self.services.data_root, f"plan-drafts/{plan_id}.json")

    def load(self, plan_id):
        try:
            draft = self._file(plan_id).read({})
        except StorageError:
            raise BridgeError("DRAFT_UNREADABLE", "这份计划的创建记录读不出来了，可以删除后重新新建。") from None
        if draft.get("plan_id") != plan_id:
            raise BridgeError("DRAFT_NOT_FOUND", "找不到这份计划的创建记录。")
        return draft

    def save(self, draft):
        try:
            self._file(draft["plan_id"]).write(draft)
        except StorageError:
            raise BridgeError("DRAFT_SAVE_FAILED", "创建记录保存失败，请重试。") from None
        self.push("plan_draft", self.public(draft))

    def public(self, draft):
        view = dict(draft, busy=self._status.get(draft["plan_id"]))
        if draft.get("outline"):
            view["outline"] = {k: v for k, v in draft["outline"].items() if k != "agent"}
            view["outline"]["stages"] = [dict(s, modules=[{k: v for k, v in m.items() if k != "spans"} for m in s["modules"]])
                                         for s in draft["outline"]["stages"]]
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
        try:
            draft = self.load(plan_id)
            try:
                work(draft, ctx)
            except ModelCancelled:
                raise BridgeError("CANCELLED", "已停止。") from None
            except ModelError as exc:
                draft["messages"].append(_msg("ai", "error", f"{exc.message} 可以稍后重试。"))
                self.save(draft)
            return {"draft": self.public(draft)}
        finally:
            self.status(plan_id, "")
            lock.release()

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
                 "messages": [_msg("ai", "form", "你好！先告诉我你想学什么。填好下面这张表，我会据此找资料、搭路线。")],
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
        self._pools.pop(plan_id, None)
        self._verified.pop(plan_id, None)

    # ----------------------------------------------------------- model calls
    def _ask(self, purpose, request, parse, ctx, *, plan_id, search=False, max_tokens=4096):
        system, messages = request[0], request[1]
        last = None
        for attempt in range(2):  # one retry on a malformed answer (ARCH-04) or a transient provider hiccup
            try:
                result = self.services.gateway.complete(purpose, messages, system=system, enable_search=search,
                                                        cancel=ctx.cancel, plan_id=plan_id, max_tokens=max_tokens)
            except ModelError as exc:
                if (exc.code == MODEL_OUTPUT_TRUNCATED and not attempt and max_tokens < 32768
                        and not ctx.cancel.is_set()):
                    # A long outline can outgrow its cap; the same request with room to finish.
                    log.info("agent %s hit the output cap %s, retrying with double", purpose, max_tokens)
                    max_tokens, last = min(32768, max_tokens * 2), exc
                    continue
                if attempt or exc.code not in TRANSIENT or ctx.cancel.is_set():
                    raise
                log.info("agent %s transient error %s, retrying once", purpose, exc.code)
                last = exc
                continue
            try:
                return parse(result.text), result
            except agents.AgentFormatError as exc:
                last = exc
                log.info("agent %s returned an invalid format (%s), attempt %s", purpose, exc, attempt + 1)
        raise ModelError("AGENT_FORMAT", "AI 的回答格式不对，已重试一次仍不行。") from last

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
                              notes=form["notes"], window=window)
        draft["form"] = form
        draft["messages"].append(_msg("user", "form_summary", agents.form_brief(form)))
        draft["phase"] = "questions"
        self.save(draft)
        self._check_sufficiency(draft, ctx)

    def _conversation(self, draft):
        return [{"role": m["role"], "text": m["text"]} for m in draft["messages"]
                if m["kind"] in ("questions", "user_text")]

    def _check_sufficiency(self, draft, ctx):
        self.status(draft["plan_id"], "正在核对信息是否足够…")
        verdict, _ = self._ask("sufficiency", agents.sufficiency_request(draft["form"], self._conversation(draft)),
                               agents.parse_sufficiency, ctx, plan_id=draft["plan_id"])
        draft["goal"], draft["focus"] = verdict["goal"], verdict["focus"]
        if not verdict["enough"]:
            text = "还需要确认几件事，这样找的资料和路线才准：\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(verdict["questions"], 1))
            draft["messages"].append(_msg("ai", "questions", text))
            draft["phase"] = "questions"
            self.save(draft)
            return
        draft["messages"].append(_msg("ai", "text", f"信息够了。我理解的目标是：{verdict['goal']}"))
        draft["phase"] = "sources"
        self.save(draft)
        links = find_links(" ".join([draft["form"].get("notes", ""), draft["form"].get("goal", "")] +
                                    [m["text"] for m in draft["messages"] if m["kind"] == "user_text"]))
        if links:
            self._import_links(draft, ctx, links)
        if draft["form"].get("only_mine"):
            has_own = any(s["status"] == "adopted" for s in draft["sources"])
            draft["messages"].append(_msg("ai", "text", "你说只用自己给的资料，我就不另外搜索了。" +
                                          ("可以直接生成路线轮廓。" if has_own else "请点下面的“上传资料”，传好后生成路线轮廓。")))
            self.save(draft)
            return
        self._research(draft, ctx)

    # ----------------------------------------------------------- 2 research
    def _pool(self, plan_id):
        from ..course.research_pipeline import ResearchPool
        return self._pools.setdefault(plan_id, ResearchPool())

    def _research(self, draft, ctx, instruction=""):
        """Bounded replenishment; a shortage never lowers the admission bar."""
        previous_count = -1
        for attempt in range(source_quality.MAX_SEARCH_ROUNDS):
            if ctx.cancel.is_set():
                raise ModelCancelled()
            self._research_once(draft, ctx, instruction)
            count = sum(c.get("quality", {}).get("eligible", False) and c["status"] != "rejected"
                        for c in draft["sources"] if not c.get("user_provided"))
            if count >= source_quality.MIN_SOURCES:
                return
            if attempt > 0 and count <= previous_count:
                # A second round that added nothing should not spend another
                # model search on the same pool of URLs.
                break
            previous_count = count
            instruction = (instruction + " 权威资料不足，请换来源继续找官方文档、原始教材、大学课程或权威机构，避免转载和社区帖。")[-1000:]
        draft["messages"].append(_msg("ai", "text", f"权威资料不足：本次最多检索 {source_quality.MAX_SEARCH_ROUNDS} 轮，"
            f"目前只有 {count} 份达到门槛，期望至少 {source_quality.MIN_SOURCES} 份。不会降低标准凑数；可以缩小主题或提供你自己的资料。"))
        self.save(draft)

    def _research_once(self, draft, ctx, instruction=""):
        from ..course.research_pipeline import ResearchPipeline
        from ..course.source_filter import RawHit
        plan_id = draft["plan_id"]
        pool = self._pool(plan_id)
        draft["research"]["round"] += 1
        accepted = [(s["title"], s["url"]) for s in draft["sources"] if s["status"] == "adopted" and s.get("url")]
        self.status(plan_id, "正在联网搜索资料（通常要半分钟到一分钟）…")
        suggestions, result = self._ask(
            "research", agents.research_request(draft["goal"], draft["form"], draft["focus"], accepted=accepted,
                                                rejected=draft["research"]["rejected"], round_no=draft["research"]["round"],
                                                instruction=instruction),
            agents.parse_research, ctx, plan_id=plan_id, search=True)
        hits, seen = [], set()
        # Tool output may contain dozens of raw hits. Give the model's short
        # explicit shortlist a chance to be verified before the 30-fetch cap;
        # every URL still goes through the same screen, fetch and quality gate.
        for url, title, snippet in ([(s["url"], s["title"], s["why"]) for s in suggestions]
                                    + [(h.url, h.title, h.snippet) for h in result.search_hits + result.citations]):
            if url and url not in seen:
                seen.add(url)
                hits.append(RawHit(url=url, title=title or "", snippet=snippet or ""))
        self.status(plan_id, f"搜到 {len(hits)} 条结果，正在逐个打开核实…")
        pipeline = ResearchPipeline(fetcher=self.fetcher, lists_provider=self._lists, max_sources=30, timeout=15.0)
        try:
            outcome = pipeline.run(hits, accepted_urls=pool.accepted_urls, rejected_urls=pool.rejected_urls, cancel=ctx.cancel)
        finally:
            pipeline.close()
        if ctx.cancel.is_set():
            raise ModelCancelled()
        # Sphinx "view source" pages (/_sources/...) are markup, not the lesson itself.
        verified = [s for s in outcome.verified if "/_sources/" not in s.final_url and "/_sources/" not in s.url]
        pool.add(verified)
        for source in verified:
            self._verified.setdefault(plan_id, {})[source.url] = source
        self._show_batch(draft, ctx, searched=len(hits), verified=len(verified))

    def _import_links(self, draft, ctx, urls):
        """Links the learner gave: open and verify each, adopt what passes; no search, no scoring gate."""
        from ..course.materials import import_verified_source
        from ..course.research_pipeline import ResearchPipeline
        from ..course.source_filter import RawHit
        plan_id = draft["plan_id"]
        done = set(draft["research"].setdefault("linked", []))
        urls = [u for u in urls if u not in done]
        if not urls:
            return
        from ..course import github_repo
        repos = [u for u in urls if github_repo.parse(u)]
        pages = [u for u in urls if u not in repos]
        verified, failed = [], []
        for url in repos:   # a repository's real material is its docs, not the home page
            self.status(plan_id, "正在读取 GitHub 仓库里的文档（大仓库要一两分钟）…")
            try:
                verified.append(github_repo.import_repo(url, fetch=self.repo_fetch, cancel=ctx.cancel, lists=self._lists()))
            except github_repo.RepoImportError as exc:
                failed.append(f"{url}（{exc.message}）")
            except Exception:  # noqa: BLE001 - network trouble on one link must not hide the others
                if ctx.cancel.is_set():
                    raise ModelCancelled() from None
                log.warning("repository import failed", exc_info=True)
                failed.append(f"{url}（读取失败，请稍后重试）")
        if pages:
            self.status(plan_id, f"正在打开你给的 {len(pages)} 个链接…")
            pipeline = ResearchPipeline(fetcher=self.fetcher, lists_provider=self._lists, max_sources=10, timeout=20.0)
            try:
                outcome = pipeline.run([RawHit(url=u) for u in pages], include_low=True, cancel=ctx.cancel)
            finally:
                pipeline.close()
            verified.extend(outcome.verified)
            failed.extend(f"{r.url}（{r.message}）" for r in outcome.rejected)
        if ctx.cancel.is_set():
            raise ModelCancelled()
        cards = []
        for source in verified:
            saved = import_verified_source(self.services.store, plan_id, source)
            if any(c.get("source_id") == saved["id"] for c in draft["sources"]):
                continue
            self._pool(plan_id).mark_accepted(source.url)
            quality = source_quality.annotate(self.services.store, plan_id, [saved["id"]], lists=self._lists(), today=self.study_day())[0]
            cards.append({"card_id": _new_id()[:12], "kind": "url", "url": source.url, "title": source.title or source.url,
                          "site": source.site, "tier": source.tier, "summary": source.summary[:300], "relevance": quality["relevance"],
                          "credibility": quality["credibility"], "note": quality["note"], "quality": quality, "user_provided": True,
                          "status": "adopted", "source_id": saved["id"],
                          "chars": source.document.char_count, "sha": source.document.sha256})
        draft["sources"].extend(cards)
        draft["research"]["linked"] = sorted(done | set(urls))
        text = (f"已打开并采用你给的 {len(cards)} 个链接。" if cards else "") + \
               ("没能使用：" + "；".join(failed[:5]) + "。" if failed else "")
        draft["messages"].append(_msg("ai", "sources", text or "这些链接已经在资料里了。", cards=[c["card_id"] for c in cards]))
        self.save(draft)

    def _lists(self):
        try:
            return self.services.catalog.load_lists()
        except Exception:  # noqa: BLE001 - the built-in lists stay the fallback
            from ..course.source_filter import load_lists
            return load_lists()

    def _show_batch(self, draft, ctx, *, searched=None, verified=None, quiet=False):
        """Score and show the next unseen verified sources; returns how many cards were added."""
        plan_id = draft["plan_id"]
        pool, cards = self._pool(plan_id), []
        for _ in range(3):
            batch = pool.take(8)
            if not batch:
                break
            self.status(plan_id, "正在评估资料的相关性和可信度…")
            modules = [m for stage in (draft.get("outline") or {}).get("stages", []) for m in stage["modules"]]
            qualities = [source_quality.assess(s, draft["goal"], level=draft["form"]["level"], modules=modules,
                                               today=self.study_day(), lists=self._lists()) for s in batch]
            candidates = [{"id": f"r{i}", "title": s.title or s.url, "site": s.site,
                           "excerpt": qualities[i]["evidence"]["excerpt"]}
                          for i, s in enumerate(batch) if qualities[i]["needs_review"]]
            scores = {}
            if candidates:
                scores, _ = self._ask("source_scoring", agents.scoring_request(draft["goal"], draft["form"], candidates),
                                      lambda text: agents.parse_scoring(text, [c["id"] for c in candidates]), ctx, plan_id=plan_id,
                                      max_tokens=8192)
            shown = {c.get("sha") for c in draft["sources"]} | {c["sha"] for c in cards}
            shown_keys = {c.get("content_key") or source_quality.content_identity(c.get("url", ""))
                          for c in draft["sources"]} | {c["content_key"] for c in cards}
            for i, source in enumerate(batch):
                score = source_quality.apply_review(qualities[i], scores.get(f"r{i}", {}))
                draft["research"].setdefault("quality_audit", {})[source.url] = score
                identity = source_quality.content_identity(source.final_url)
                if not score["eligible"] or source.document.sha256 in shown or identity in shown_keys:
                    continue  # also skips a page whose text is identical to one already shown
                shown.add(source.document.sha256)
                shown_keys.add(identity)
                cards.append({"card_id": _new_id()[:12], "kind": "url", "url": source.url, "title": source.title or source.url,
                              "site": source.site, "tier": source.tier, "summary": source.summary[:300],
                              "relevance": score["relevance"], "credibility": score["credibility"], "note": score["note"],
                              "quality": score, "user_provided": False,
                              "status": "new", "chars": source.document.char_count, "sha": source.document.sha256,
                              "content_key": identity})
            if len(cards) >= 3:
                break
        cards.sort(key=lambda c: (c["relevance"] * c["credibility"], c["tier"] == "whitelist"), reverse=True)
        cards = cards[:MAX_CARDS_PER_ROUND]
        draft["sources"].extend(cards)
        if cards:
            text = f"找到 {len(cards)} 份合适的资料" + (f"（共搜到 {searched} 条，核实通过 {verified} 条）" if searched else "") + \
                   "。看看哪些要用：点“采用”或“不要”。不满意可以点“再找找”，或者直接告诉我想要什么样的资料。"
            draft["messages"].append(_msg("ai", "sources", text, cards=[c["card_id"] for c in cards]))
        elif quiet:
            return 0
        else:
            draft["messages"].append(_msg("ai", "text", "这一轮权威资料不足。可以点“再找找”换个思路，"
                                                        "或告诉我更具体的方向；也可以上传你手头的资料。"))
        self.save(draft)
        return len(cards)

    def more_sources(self, draft, ctx, instruction="", *, echo=True):
        if draft["phase"] not in ("sources", "outline"):
            raise BridgeError("PHASE_INVALID", "现在还不能找资料。")
        draft["phase"] = "sources"
        if instruction and not echo:  # chat() already showed the message and opened its links
            return self._research(draft, ctx, instruction)
        if instruction:
            draft["messages"].append(_msg("user", "user_text", instruction))
            self.save(draft)
            links = find_links(instruction)
            if links:
                self._import_links(draft, ctx, links)
                rest = _URL.sub("", instruction).strip(" ，,。;；\n")
                if len(rest) < 6:   # only links: no extra search
                    return
                instruction = rest
            return self._research(draft, ctx, instruction)
        if not self._show_batch(draft, ctx, quiet=True):
            self._research(draft, ctx)

    def decide(self, draft, card_id, adopt):
        card = next((c for c in draft["sources"] if c["card_id"] == card_id), None)
        if card is None:
            raise BridgeError("PARAM_INVALID", "找不到这份资料。")
        if draft["phase"] not in ("sources", "outline", "questions"):
            raise BridgeError("PHASE_INVALID", "路线已经开始生成，不能再改资料。")
        store, plan_id = self.services.store, draft["plan_id"]
        if adopt:
            if card.get("kind") == "url" and not card.get("user_provided"):
                source = self._verified.get(plan_id, {}).get(card["url"])
                stored = next((s for s in store.records("sources", plan_id) if s["id"] == card.get("source_id")), None)
                modules = [m for stage in (draft.get("outline") or {}).get("stages", []) for m in stage["modules"]]
                options = dict(level=draft["form"]["level"], modules=modules, today=self.study_day(), lists=self._lists())
                if source is not None:
                    quality = source_quality.assess(source, draft["goal"], **options)
                elif stored is not None:
                    chunks = [c for c in store.records("source_chunks", plan_id) if c["source_id"] == stored["id"]]
                    quality = source_quality.assess_stored(stored, chunks, draft["goal"], **options)
                else:
                    raise BridgeError("SOURCE_EXPIRED", "请重新检索并核实这份资料后再采用。")
                previous = card.get("quality", {})
                if previous.get("context") == quality["context"]:
                    quality = source_quality.apply_review(quality, previous)
                if not quality["eligible"]:
                    raise BridgeError("SOURCE_QUALITY_LOW", "这份检索资料尚未达到权威性与相关性门槛，不能采用。")
                card["quality"] = quality
                if stored is not None:
                    store.add_sources(plan_id, [dict(stored, quality=quality, user_provided=False)])
            if card.get("source_id"):
                store.set_source_adopted(plan_id, card["source_id"], True)
            else:
                verified = self._verified.get(plan_id, {}).get(card["url"])
                if verified is None:
                    raise BridgeError("SOURCE_EXPIRED", "这份资料的正文已不在内存里（软件重启过），请点“再找找”重新获取。")
                from ..course.materials import import_verified_source
                self.status(plan_id, "正在保存资料正文…")
                saved = import_verified_source(store, plan_id, verified)
                card["source_id"] = saved["id"]
                store.add_sources(plan_id, [dict(saved, quality=card["quality"], user_provided=False)])
            if card.get("url"):
                self._pool(plan_id).mark_accepted(card["url"])
            if card.get("url") in draft["research"]["rejected"]:
                draft["research"]["rejected"].remove(card["url"])
        else:
            if card.get("source_id"):
                store.set_source_adopted(plan_id, card["source_id"], False)
            if card.get("url"):
                self._pool(plan_id).mark_rejected(card["url"])
                if card["url"] not in draft["research"]["rejected"]:
                    draft["research"]["rejected"].append(card["url"])
        card["status"] = "adopted" if adopt else "rejected"
        draft.pop("scope", None)  # section ids are numbered over the adopted sources
        self.save(draft)

    def add_file(self, plan_id, path):
        from ..course.materials import import_file
        lock = self._lock(plan_id)
        with lock:
            draft = self.load(plan_id)
            if draft["phase"] not in ("form", "questions", "sources", "outline"):
                raise BridgeError("PHASE_INVALID", "路线已经开始生成，不能再加资料。")
            try:
                self.services.store.get_plan(plan_id)
            except StoreError:
                raise BridgeError("FORM_FIRST", "请先提交表单，再上传资料。") from None
            self.status(plan_id, f"正在读取 {path.name}…")
            try:
                saved = import_file(self.services.store, plan_id, path)
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
        """Sections of the adopted sources, limited to the chapters the learner chose."""
        sources, chunks = self._adopted(draft)
        sections = agents.build_sections(sources, chunks)
        scope = draft.get("scope") or {}
        if scope.get("include"):
            sections = [s for s in sections if s.id in scope["include"]]
        return sources, [s for s in sections if s.id not in scope.get("exclude", [])]

    def make_outline(self, draft, ctx, request="", *, echo=True):
        if draft["phase"] not in ("sources", "outline"):
            raise BridgeError("PHASE_INVALID", "现在还不能生成路线轮廓。")
        sources, sections = self._sections(draft)
        if not sources:
            raise BridgeError("NO_SOURCES", "请先采用至少一份资料，路线要从资料里来。")
        if not sections:
            raise BridgeError("NO_SECTIONS", "按现在的范围一章都不剩了，请重新说一下要学哪些章节。")
        window = self._window(draft)
        previous = draft["outline"]["agent"] if (request and draft.get("outline")) else None
        if request and echo:
            draft["messages"].append(_msg("user", "user_text", request))
        self.status(draft["plan_id"], "正在设计路线轮廓…" if not previous else "正在按你的意见修改路线…")
        outline, _ = self._ask("outline", agents.outline_request(draft["goal"], draft["form"], window_note(window), sources,
                                                                sections, previous=previous, request=request),
                               lambda text: agents.parse_outline(text, [s.id for s in sections]), ctx,
                               plan_id=draft["plan_id"], max_tokens=8192)
        by_id = {s.id: s for s in sections}
        modules = agents.outline_modules(outline)
        for module in modules:
            # Compact spans (source + chunk positions) keep the draft small for big books.
            module["spans"] = [[by_id[r].source_id, by_id[r].first, by_id[r].last] for r in module["refs"]]
            module["ref_titles"] = [f"{by_id[r].source_title} · {by_id[r].title}" for r in module["refs"]]
        low, high = sum(m["minutes"][0] for m in modules), sum(m["minutes"][1] for m in modules)
        draft["outline"] = {"agent": {"stages": [{"title": s["title"], "modules": [
            {k: m[k] for k in ("key", "title", "summary", "refs", "prerequisites", "minutes")} for m in s["modules"]]}
            for s in outline["stages"]]}, "stages": outline["stages"], "ranges": minute_ranges(window, low, high),
            "version": (draft["outline"] or {}).get("version", 0) + 1}
        draft["phase"] = "outline"
        draft["messages"].append(_msg("ai", "outline", outline["message"] or "这是路线的大致样子。", version=draft["outline"]["version"]))
        self.save(draft)

    # ----------------------------------------------------------- 4 estimate + generate
    def estimate(self, draft):
        if draft["phase"] != "outline" or not draft.get("outline"):
            raise BridgeError("PHASE_INVALID", "请先生成路线轮廓。")
        from ..connections.pricing import estimate_tokens
        store, plan_id = self.services.store, draft["plan_id"]
        chunks = store.records("source_chunks", plan_id)
        modules = agents.outline_modules(draft["outline"])
        base = estimate_tokens(agents.GENERATE_SYSTEM + agents.form_brief(draft["form"])) + 60 * len(modules)
        input_tokens, calls = 0, 0
        for module in modules:  # every character is read, in batches that each repeat the fixed part
            selected = self._span_chunks(module, chunks)
            material = "".join(c["text"] for c in selected)
            limit = max(1, min(MAX_GENERATE_BATCHES, module["minutes"][0] // 10))
            size = agents.DEFAULT_BATCH_CHARS
            batches = agents.split_module_chunks(selected, max_chars=size)
            while len(batches) > limit:
                size = int(size * 1.4) + 1
                batches = agents.split_module_chunks(selected, max_chars=size)
            count = max(1, len(batches))
            calls += count
            input_tokens += base * count + estimate_tokens(material)
        low_in, high_in = int(input_tokens * 0.85), int(input_tokens * 1.4)
        out_low, out_high = 900 * calls, 3500 * calls
        gateway = self.services.gateway
        config = gateway.get_configuration() or {}
        estimate = {"modules": len(modules), "input_tokens": [low_in, high_in], "output_tokens": [out_low, out_high],
                    "calls": calls, "model": config.get("model", ""),
                    "note": "按当前资料分批估算；失败重试或截断后拆分可能增加调用与 token。"}
        draft["estimate"] = estimate
        draft["messages"].append(_msg("ai", "estimate", "正式生成会逐个模块读资料、提炼知识点。确认后开始："))
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
                                "learning_minutes": (module["minutes"][0] + module["minutes"][1]) // 2})
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
        if draft["phase"] not in ("outline", "generating"):
            raise BridgeError("PHASE_INVALID", "现在不能生成。")
        plan_id, store = draft["plan_id"], self.services.store
        if draft["phase"] == "outline":
            self._save_outline(draft)
            draft["phase"] = "generating"
            draft["generation"] = {m["id"]: "pending" for m in agents.outline_modules(draft["outline"])}
            draft["messages"].append(_msg("ai", "progress", "开始正式生成。每个模块单独从资料里提炼知识点，可以离开这个页面，回来会看到进度。"))
            self.save(draft)
        modules = [m for m in agents.outline_modules(draft["outline"]) if draft["generation"].get(m["id"]) != "done"]
        all_chunks = store.records("source_chunks", plan_id)
        titles = {s["id"]: s.get("title") or s.get("url") or "资料" for s in store.records("sources", plan_id)}
        lock = threading.Lock()

        def one(module):
            with lock:
                draft["generation"][module["id"]] = "running"
                self.save(draft)
            chunks = self._module_chunks(self._span_chunks(module, all_chunks), titles)
            points = self._generate_module(draft, module, chunks, ctx)
            payload = [dict(p, id=f"{module['id']}-{index:02d}") for index, p in enumerate(points, 1)]
            # Ledger records whole minutes; use the same rounded estimate at
            # generation and on every correct final-check answer.
            question_minutes = max(1, round(CHECK_MINUTES_PER_QUESTION))
            store.save_generated_module(plan_id, module["id"], payload, today=self.study_day(),
                                        check_minutes=len(payload) * question_minutes,
                                        final_check_minutes=estimate_check_minutes(len(payload), minutes_per_question=question_minutes),
                                        check_question_minutes=question_minutes)
            return len(payload)

        done_count = sum(1 for v in draft["generation"].values() if v == "done")
        total = len(draft["generation"])
        self.status(plan_id, f"正在生成：{done_count}/{total} 个模块完成")
        with ThreadPoolExecutor(max_workers=GENERATE_WORKERS, thread_name_prefix="plan-generate") as pool:
            futures = {pool.submit(one, m): m for m in modules}
            for future in as_completed(futures):
                module = futures[future]
                try:
                    future.result()
                    state = "done"
                except ModelCancelled:
                    state = "pending"
                except Exception as exc:  # noqa: BLE001 - one module failing must not stop the others
                    log.warning("module generation failed: %s", type(exc).__name__)
                    state = "failed"
                with lock:
                    draft["generation"][module["id"]] = state
                    done_count += state == "done"
                    self.save(draft)
                self.status(plan_id, f"正在生成：{done_count}/{total} 个模块完成")
        if ctx.cancel.is_set():
            raise ModelCancelled()
        failed = [m["title"] for m in agents.outline_modules(draft["outline"]) if draft["generation"][m["id"]] != "done"]
        if failed:
            draft["messages"].append(_msg("ai", "error", f"有 {len(failed)} 个模块没有生成成功：{'、'.join(failed[:5])}。"
                                                          "点“重试失败的模块”再试一次，已完成的不会重做。"))
        else:
            draft["phase"] = "ready"
            draft["messages"].append(_msg("ai", "ready", "全部生成好了。开始之前还可以删掉不想学的模块、调整顺序；确认后点“开始计划”。"))
        self.save(draft)

    def _generate_module(self, draft, module, chunks, ctx):
        """Read all of a module's material: big modules go in bounded batches, merged afterwards."""
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
        index, count = position or (None, None)
        request = agents.generate_request(draft["form"], draft["goal"], draft["outline"], module, batch,
                                          minutes_range=minutes, batch_index=index, batch_count=count, max_points=max_points)
        aliases = request[2]
        parse = lambda text: agents.parse_points(text, aliases, minutes_range=minutes, max_points=max_points,  # noqa: E731
                                                 min_points=1 if position else min(2, max_points),
                                                 point_minutes_range=(10, 20) if minutes[1] >= 10 else (2, 20))
        try:
            return [self._ask("generate", request[:2], parse, ctx, plan_id=draft["plan_id"], max_tokens=8192)[0]]
        except ModelError as exc:
            if exc.code != MODEL_OUTPUT_TRUNCATED or not split or minutes[0] < 4:
                raise
            log.info("module %s batch %s hit the output limit; retrying in two halves", module["id"], index)
        halves = agents.split_module_chunks(batch, max_chars=sum(len(c["text"]) for c in batch) // 2 + 1)
        if len(halves) != 2:
            raise ModelError(MODEL_OUTPUT_TRUNCATED, "模型输出达到上限，这个模块的资料拆小后仍然不行。")
        ranges = agents.batch_minute_ranges(halves, minutes)
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
                 "source_title": titles.get(c["source_id"], "资料")} for c in chunks if c["text"].strip()]

    # ----------------------------------------------------------- chat box (ARCH-06 intents)
    def _intent_context(self, draft):
        phase = draft["phase"]
        form = {k: v for k, v in (draft["form"] or {}).items() if k != "only_mine"}
        recent = dialogue_tail([{"role": m["role"], "text": m["text"][:1500]} for m in draft["messages"]
                                if m["role"] in ("ai", "user") and m.get("text")], max_rows=6)
        cards = [{"n": n, "title": c["title"], "site": c.get("site") or "", "summary": (c.get("summary") or "")[:200],
                  "status": {"new": "待定", "adopted": "已采用", "rejected": "不要"}[c["status"]]}
                 for n, c in enumerate(draft["sources"], 1)]
        sections, modules, facts = [], [], {}
        if phase in ("sources", "outline"):
            sources, chunks = self._adopted(draft)
            sections = [{"id": s.id, "source": s.source_title, "title": s.title, "chars": s.chars}
                        for s in agents.build_sections(sources, chunks)]
            if draft.get("scope"):
                facts["scope"] = draft["scope"]
        if draft.get("outline"):
            facts["ranges"] = draft["outline"]["ranges"]
            facts["outline"] = [{"stage": s["title"], "modules": [m["title"] for m in s["modules"]]}
                                for s in draft["outline"]["stages"]]
        if draft.get("estimate"):
            facts["estimate"] = draft["estimate"]
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
        if phase not in ("questions", "sources", "outline", "ready"):
            raise BridgeError("PHASE_INVALID", "请先填写上面的表单。" if phase == "form" else "正在生成，请稍等。")
        draft["messages"].append(_msg("user", "user_text", message))
        self.save(draft)
        text = message
        links = find_links(message) if phase in ("sources", "outline") else []
        if links:  # pasted addresses are opened directly; no model is needed to understand them
            draft["phase"] = "sources"
            self._import_links(draft, ctx, links)
            text = _URL.sub("", message).strip(" ，,。;；\n")
            if len(text) < 6:
                return
        self.status(draft["plan_id"], "正在理解你的意思…")
        context = self._intent_context(draft)
        intents, _ = self._ask("plan_intent", plan_intent.intent_request(context, text),
                               lambda answer: plan_intent.parse_intents(answer, context), ctx, plan_id=draft["plan_id"])
        for intent in intents:
            try:
                self._carry_out(draft, ctx, intent, context)
            except BridgeError as exc:  # stop at the first step that cannot be done; say why
                self._say(draft, exc.message, "error")
                return

    def _say(self, draft, text, kind="text"):
        draft["messages"].append(_msg("ai", kind, text))
        self.save(draft)

    def _carry_out(self, draft, ctx, intent, context):
        kind, params, phase = intent.kind, intent.params, draft["phase"]
        if kind == "answer":
            return self._check_sufficiency(draft, ctx)
        if kind in ("question", "ask_user", "refuse"):
            return self._say(draft, params[{"question": "answer", "ask_user": "question", "refuse": "reason"}[kind]])
        if kind == "chat":
            return self._say(draft, "我们先把学习计划定下来吧，有需要随时告诉我。")
        if kind == "update_form":
            return self._update_form(draft, ctx, params)
        if kind == "find_sources":
            if phase not in ("sources", "outline"):
                return self._say(draft, "等需求确认好、开始找资料时再说资料的要求。" if phase == "questions"
                                 else "路线已经生成，不能再换资料了。")
            return self.more_sources(draft, ctx, params["instruction"], echo=False)
        if kind == "decide_sources":
            titles = {True: [], False: []}
            for adopt, key in ((True, "adopt"), (False, "reject")):
                for n in params[key]:
                    card = draft["sources"][n - 1]
                    self.decide(draft, card["card_id"], adopt)
                    titles[adopt].append(f"《{card['title']}》")
            parts = ([f"已采用{'、'.join(titles[True])}"] if titles[True] else []) + \
                    ([f"已不要{'、'.join(titles[False])}"] if titles[False] else [])
            return self._say(draft, "；".join(parts) + "。")
        if kind == "limit_scope":
            if phase not in ("sources", "outline"):
                return self._say(draft, "现在已经不能改学习范围了。")
            names = {s["id"]: f"{s['source']} · {s['title']}" for s in context["sections"]}
            draft["scope"] = {"include": params["include"], "exclude": params["exclude"]}
            picked = params["include"] or params["exclude"]
            summary = ("只学" if params["include"] else "不学") + "：" + "、".join(names[i] for i in picked[:6]) + \
                      (f" 等 {len(picked)} 部分" if len(picked) > 6 else "")
            if phase == "outline":
                self._say(draft, f"好的，范围改为{summary}。正在按新范围调整路线…")
                return self.make_outline(draft, ctx, "按学习者要求调整学习范围，" + summary, echo=False)
            return self._say(draft, f"记下了，生成路线轮廓时{summary}。")
        if kind == "revise_outline":
            if phase == "sources":
                return self.make_outline(draft, ctx, params["instruction"], echo=False)
            if phase != "outline":
                return self._say(draft, "路线已经生成，可以直接说要删掉或移动哪个模块。")
            return self.make_outline(draft, ctx, params["instruction"], echo=False)
        if kind == "edit_modules":
            return self._edit_modules(draft, params)
        if kind == "proceed":
            step = params["step"]
            if step == "outline":
                if phase != "sources":
                    return self._say(draft, "路线轮廓已经有了。" if phase == "outline" else "现在还不到生成路线的时候。")
                return self.make_outline(draft, ctx, echo=False)
            if step in ("estimate", "generate"):
                if phase != "outline":
                    return self._say(draft, "要先有路线轮廓，才能预估费用和正式生成。")
                if step == "estimate" or not draft.get("estimate"):
                    return self.estimate(draft)
                return self._say(draft, "正式生成会产生费用，请确认上面的预估后点“开始生成”。")
            if phase != "ready":
                return self._say(draft, "计划还没生成完，暂时不能开始。")
            return self._say(draft, "开始后就不能再删模块、调顺序了。确认没问题就点下面的“开始计划”。")
        raise BridgeError("AGENT_FORMAT", "没能理解这句话，请换个说法。")

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
        if draft.get("outline"):
            modules = agents.outline_modules(draft["outline"])
            draft["outline"]["ranges"] = minute_ranges(window, sum(m["minutes"][0] for m in modules),
                                                       sum(m["minutes"][1] for m in modules))
        shown = []
        for key in params:
            value = {"level": LEVEL_NAMES.get(form["level"]), "daily_cap": f"{form['daily_cap']} 分钟",
                     "start": form["start"], "end": form["end"]}.get(key)
            shown.append(f"{self._FORM_LABELS[key]}改为 {value}" if value else f"更新了{self._FORM_LABELS[key]}")
        self._say(draft, "已修改：" + "，".join(shown) + "。")
        if phase == "questions" and any(form[k] != old[k] for k in about):
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
    def detail(self, plan_id):
        store = self.services.store
        plan = store.get_plan(plan_id)
        stages = store.records("stages", plan_id)
        modules = store.records("modules", plan_id)
        points = store.records("points", plan_id)
        started = plan.get("state") in ("active", "checking", "completed")
        book = store.load_mastery(plan_id) if started else None
        mastery = {}
        taught = {row['credit']['unit_id'] for row in store.records('credits', plan_id)
                  if row['id'].startswith(('learn:', 'relearn:'))} if started else set()
        from ..course.question_bank import review_points
        review = review_points(store.records('practice_results', plan_id)) if started else set()
        if book is not None:  # MAP-01: what the learner has shown so far, point by point
            for point in points:
                state = book.point(point["id"])
                mastery[point["id"]] = {"state": state.state, "level": state.level,
                                        "errors": state.casual_errors + state.module_errors,
                                        "needs_review": point['id'] in review,
                                        "taught": point['id'] in taught}
        by_module: dict[str, list] = {}
        for point in points:
            by_module.setdefault(point["module_id"], []).append(
                {"id": point["id"], "title": point.get("title", ""), "summary": point["summary"], "minutes": point["minutes"],
                 **mastery.get(point["id"], {})})
        result = {"plan": {k: plan.get(k) for k in ("id", "title", "goal", "final_goal", "state", "window", "total_minutes", "module_count")},
                  "stages": [{"id": s["id"], "title": s.get("title", ""), "modules": [
                      {"id": m["id"], "title": m["title"], "summary": m.get("summary", ""), "learning_minutes": m["learning_minutes"],
                       "check_minutes": m["check_minutes"], "points": by_module.get(m["id"], []),
                       "state": book.module(m["id"]).state if book else None}
                      for m in modules if m["stage_id"] == s["id"]]} for s in stages],
                  "sources": [{"id": s["id"], "title": s.get("title") or s.get("url") or s.get("filename"), "url": s.get("url"),
                               "kind": s["kind"]} for s in store.records("sources", plan_id) if s.get("adopted")]}
        result["stages"] = [s for s in result["stages"] if s["modules"]]
        if plan.get("window") and plan.get("total_minutes"):
            from ..course.store import _window
            window = _window(plan["window"])
            cap = check_cap(window, plan["total_minutes"])
            result["daily"] = {"full_day": cap.full_day_minutes, "cap": cap.cap, "ok": cap.ok,
                               "suggested_end": cap.suggested_end.isoformat() if cap.suggested_end else None}
        if started and plan.get("window"):
            status = store.load_ledger(plan_id).status(self.study_day())
            result["progress"] = {"remaining": status.remaining_minutes, "overdue_days": getattr(status, "overdue_days", 0)}
        result["has_report"] = plan.get("state") == "completed"
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
        if draft["phase"] != "ready":
            raise BridgeError("PHASE_INVALID", "现在不能改日期。")
        new_end = _day(end, "结束日期")
        window = self._window(draft)
        if new_end <= window.end or (new_end - window.start).days > 3 * 366:
            raise BridgeError("FORM_INVALID", "新的结束日期要晚于原来的，且计划最长三年。")
        window = PlanWindow(window.start, new_end, window.daily_cap_minutes, window.weekday_weights, dict(window.date_weights))
        self.services.store.save_intake(draft["plan_id"], window=window, today=self.study_day(), reason="新建计划时延后结束日期")
        draft["form"]["end"] = new_end.isoformat()
        self.save(draft)

    def start(self, draft):
        with self.services.store.transaction():
            plan = self.services.store.get_plan(draft['plan_id'])
            if plan['state'] not in ('active', 'checking', 'completed'):
                if draft["phase"] != "ready":
                    raise BridgeError("PHASE_INVALID", "计划还没生成完。")
                self.services.store.start_plan(draft["plan_id"], today=self.study_day())
        self._file(draft["plan_id"]).path.unlink(missing_ok=True)
        self._pools.pop(draft["plan_id"], None)
        self._verified.pop(draft["plan_id"], None)
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
        "plan.get": lambda p, c: {"draft": flow().public(flow().load(plan_id(p)))},
        "plan.drafts": lambda p, c: {"drafts": flow().drafts()},
        "plan.discard": lambda p, c: flow().discard(plan_id(p)),
        "plan.delete": lambda p, c: flow().delete_started(plan_id(p)),
        "plan.submit": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().submit_form(d, x, p.get("form")), c),
        "plan.say": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().chat(d, x, text(p)), c),
        "plan.more": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().more_sources(d, x), c),
        "plan.decide": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().decide(d, text(p, "card_id", 40), p.get("adopt") is True), c),
        "plan.outline": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().make_outline(d, x), c),
        "plan.estimate": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().estimate(d), c),
        "plan.generate": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().generate(d, x), c),
        "plan.module": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().edit_module(
            d, "delete" if p.get("action") == "delete" else "move", text(p, "module_id", 40), p.get("direction", 0)), c),
        "plan.extend": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().extend_end(d, p.get("end")), c),
        "plan.start": lambda p, c: flow().start_request(plan_id(p)),
        "plan.detail": lambda p, c: flow().detail(plan_id(p)),
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
    checks = sum(p['check_questions'] for p in report['points'])
    correct = sum(p['check_correct'] for p in report['points'])
    lines.insert(-1, f"- 最终检验：{correct}/{checks} 题答对" if checks else "- 最终检验：—")
    if plan.get("early_days"):
        lines.append(f"比截止日提前 {plan['early_days']} 天完成。")
    if plan.get("overdue_days"):
        lines.append(f"比截止日晚了 {plan['overdue_days']} 天完成。")
    lines += ["", "## 模块", ""]
    lines += [f"- {m['title']}：{ {'passed': '通过', 'skipped': '直接考通过', 'failed': '未通过'}.get(m['state'], m['state']) }"
              for m in report["modules"]]
    lines += ["", "## 各模块掌握情况", ""]
    lines += [f"- {m['title']}：{m['mastered']}/{m['total']} 个知识点通过最终检验"
              for m in views['modules']]
    lines += ["", "## 错误分布", "",
              f"- 随口问：{views['errors']['casual']} 次；模块检测：{views['errors']['module']} 次；最终检验：{views['errors']['check']} 次"]
    lines += ["", "## 每日目标与实际 / 历史明细", "",
              "按原始学习日统计实际完成；欠账与超前为当天目标与当天实际的差值。", ""]
    lines += [f"- {day['day']}：目标 {day['target_minutes']} 分，实际 {day['actual_minutes']} 分，"
              f"欠账 {day['debt_minutes']} 分，超前 {day['ahead_minutes']} 分"
              for day in views['history']]
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
