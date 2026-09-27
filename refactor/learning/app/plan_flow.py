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
import re
import threading
import uuid

from ..connections.errors import ModelCancelled, ModelError
from ..course import planner_agents as agents
from ..course.ledger import LedgerError, PlanWindow, check_cap, distribute, estimate_check_minutes, full_day_minutes
from ..course.store import StoreError
from ..preferences.storage import AccountJSON, StorageError
from .bridge import BridgeError, CallContext

log = logging.getLogger(__name__)

MAX_CARDS_PER_ROUND = 6
MODULE_CHAR_BUDGET = 60000
CHECK_MINUTES_PER_QUESTION = 1.5
GENERATE_WORKERS = 4
TRANSIENT = frozenset({"MODEL_RESPONSE_INVALID", "MODEL_CONNECTION_FAILED", "MODEL_TIMEOUT"})
_PLAN_ID = re.compile(r"[a-f0-9]{32}")
_URL = re.compile(r"https?://[^\s，。；、）)>\]】\"'<]+")


def find_links(text):
    return list(dict.fromkeys(u.rstrip(".,;:!?") for u in _URL.findall(text or "")))[:10]
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
LEVELS = tuple(agents.LEVELS)


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


def minute_ranges(window: PlanWindow, low: int, high: int) -> dict:
    """Totals include a check reserve; daily figures are for a full study day."""
    lo_total, hi_total = round(low * 1.12), round(high * 1.12)
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
            try:
                self.services.store.delete_plan(plan_id)
            except StoreError as exc:
                if exc.code != "NOT_FOUND":
                    raise
            self._file(plan_id).path.unlink(missing_ok=True)
            self._pools.pop(plan_id, None)
            self._verified.pop(plan_id, None)
            return {"deleted": True}
        finally:
            lock.release()

    # ----------------------------------------------------------- model calls
    def _ask(self, purpose, request, parse, ctx, *, plan_id, search=False, max_tokens=4096):
        system, messages = request[0], request[1]
        last = None
        for attempt in range(2):  # one retry on a malformed answer (ARCH-04) or a transient provider hiccup
            try:
                result = self.services.gateway.complete(purpose, messages, system=system, enable_search=search,
                                                        cancel=ctx.cancel, plan_id=plan_id, max_tokens=max_tokens)
            except ModelError as exc:
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
        for url, title, snippet in ([(h.url, h.title, h.snippet) for h in result.search_hits + result.citations]
                                    + [(s["url"], s["title"], s["why"]) for s in suggestions]):
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
            cards.append({"card_id": _new_id()[:12], "kind": "url", "url": source.url, "title": source.title or source.url,
                          "site": source.site, "tier": source.tier, "summary": source.summary[:300], "relevance": None,
                          "credibility": None, "note": "你提供的链接", "status": "adopted", "source_id": saved["id"],
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
            candidates = [{"id": f"r{i}", "title": s.title or s.url, "site": s.site,
                           "excerpt": (s.summary + "\n" + " ".join(c.text for c in s.document.chunks[:3]))[:1500]}
                          for i, s in enumerate(batch)]
            scores, _ = self._ask("source_scoring", agents.scoring_request(draft["goal"], draft["form"], candidates),
                                  lambda text: agents.parse_scoring(text, [c["id"] for c in candidates]), ctx, plan_id=plan_id)
            shown = {c.get("sha") for c in draft["sources"]} | {c["sha"] for c in cards}
            for candidate, source in zip(candidates, batch):
                score = scores.get(candidate["id"])
                if not score or score["relevance"] < 3 or score["credibility"] < 3 or source.document.sha256 in shown:
                    continue  # also skips a page whose text is identical to one already shown
                shown.add(source.document.sha256)
                cards.append({"card_id": _new_id()[:12], "kind": "url", "url": source.url, "title": source.title or source.url,
                              "site": source.site, "tier": source.tier, "summary": source.summary[:300],
                              "relevance": score["relevance"], "credibility": score["credibility"], "note": score["note"],
                              "status": "new", "chars": source.document.char_count, "sha": source.document.sha256})
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
            draft["messages"].append(_msg("ai", "text", "这一轮没有找到足够相关、可信的资料。可以点“再找找”换个思路，"
                                                        "或告诉我更具体的方向；也可以上传你手头的资料。"))
        self.save(draft)
        return len(cards)

    def more_sources(self, draft, ctx, instruction=""):
        if draft["phase"] not in ("sources", "outline"):
            raise BridgeError("PHASE_INVALID", "现在还不能找资料。")
        draft["phase"] = "sources"
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
                draft["sources"].append({"card_id": _new_id()[:12], "kind": "file", "url": None,
                                         "title": saved.get("title") or path.name, "site": path.name, "tier": "file",
                                         "summary": "", "relevance": None, "credibility": None, "note": "你上传的资料",
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

    def make_outline(self, draft, ctx, request=""):
        if draft["phase"] not in ("sources", "outline"):
            raise BridgeError("PHASE_INVALID", "现在还不能生成路线轮廓。")
        sources, chunks = self._adopted(draft)
        if not sources:
            raise BridgeError("NO_SOURCES", "请先采用至少一份资料，路线要从资料里来。")
        sections = agents.build_sections(sources, chunks)
        window = self._window(draft)
        previous = draft["outline"]["agent"] if (request and draft.get("outline")) else None
        if request:
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
        input_tokens = 0
        for module in modules:
            material = "".join(c["text"] for c in self._span_chunks(module, chunks))[:MODULE_CHAR_BUDGET]
            input_tokens += base + estimate_tokens(material)
        low_in, high_in = int(input_tokens * 0.85), int(input_tokens * 1.4)
        out_low, out_high = 900 * len(modules), 3500 * len(modules)
        config = self.services.gateway.get_configuration() or {}
        amount = None
        try:
            quote = self.services.gateway.price_catalog.estimate_cost(
                config.get("provider_id", ""), config.get("model", ""), input_tokens=(low_in, high_in),
                output_tokens=(out_low, out_high), cache_hit_rate=(0, 0.3))
            if quote is not None:
                amount = {"low": str(quote.low.quantize(quote.low.__class__("0.01"))),
                          "high": str(quote.high.quantize(quote.high.__class__("0.01"))), "currency": quote.currency}
        except Exception:  # noqa: BLE001 - an unknown model just has no price
            amount = None
        estimate = {"modules": len(modules), "input_tokens": [low_in, high_in], "output_tokens": [out_low, out_high],
                    "amount": amount, "model": config.get("model", "")}
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
            request = agents.generate_request(draft["form"], draft["goal"], draft["outline"], module, chunks)
            aliases = request[2]
            points, _ = self._ask("generate", request[:2], lambda text: agents.parse_points(text, aliases), ctx,
                                  plan_id=plan_id, max_tokens=8192)
            payload = [dict(p, id=f"{module['id']}-{index:02d}") for index, p in enumerate(points, 1)]
            store.save_generated_module(plan_id, module["id"], payload, today=self.study_day(),
                                        check_minutes=estimate_check_minutes(len(payload), minutes_per_question=CHECK_MINUTES_PER_QUESTION))
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
        total = sum(len(c["text"]) for c in chunks)
        if total > MODULE_CHAR_BUDGET:  # keep an even spread of the section, in order
            keep, budget, step = [], MODULE_CHAR_BUDGET, total / MODULE_CHAR_BUDGET
            index = 0.0
            while int(index) < len(chunks) and budget > 0:
                chunk = chunks[int(index)]
                keep.append(chunk); budget -= len(chunk["text"]); index += step
            chunks = keep
        return [{"id": c["id"], "text": c["text"], "section": c.get("section") or [],
                 "source_title": titles.get(c["source_id"], "资料")} for c in chunks]

    # ----------------------------------------------------------- 5 review + start
    def detail(self, plan_id):
        store = self.services.store
        plan = store.get_plan(plan_id)
        stages = store.records("stages", plan_id)
        modules = store.records("modules", plan_id)
        points = store.records("points", plan_id)
        by_module: dict[str, list] = {}
        for point in points:
            by_module.setdefault(point["module_id"], []).append(
                {"id": point["id"], "title": point.get("title", ""), "summary": point["summary"], "minutes": point["minutes"]})
        result = {"plan": {k: plan.get(k) for k in ("id", "goal", "final_goal", "state", "window", "total_minutes", "module_count")},
                  "stages": [{"id": s["id"], "title": s.get("title", ""), "modules": [
                      {"id": m["id"], "title": m["title"], "summary": m.get("summary", ""), "learning_minutes": m["learning_minutes"],
                       "check_minutes": m["check_minutes"], "points": by_module.get(m["id"], [])}
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
        return result

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
        if draft["phase"] != "ready":
            raise BridgeError("PHASE_INVALID", "计划还没生成完。")
        self.services.store.start_plan(draft["plan_id"], today=self.study_day())
        self._file(draft["plan_id"]).path.unlink(missing_ok=True)
        self._pools.pop(draft["plan_id"], None)
        self._verified.pop(draft["plan_id"], None)
        self.push("plan_started", {"plan_id": draft["plan_id"]})


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
        return run

    def say(draft, ctx, message):
        phase = draft["phase"]
        f = flow()
        if phase == "questions":
            draft["messages"].append(_msg("user", "user_text", message))
            f.save(draft)
            return f._check_sufficiency(draft, ctx)
        if phase == "sources":
            return f.more_sources(draft, ctx, message)
        if phase == "outline":
            return f.make_outline(draft, ctx, message)
        raise BridgeError("PHASE_INVALID", "现在不需要在这里输入，请用上面的按钮操作。" if phase != "form" else "请先填写上面的表单。")

    handlers = {
        "plan.current": lambda p, c: {"draft": flow().public(flow().current())},
        "plan.get": lambda p, c: {"draft": flow().public(flow().load(plan_id(p)))},
        "plan.drafts": lambda p, c: {"drafts": flow().drafts()},
        "plan.discard": lambda p, c: flow().discard(plan_id(p)),
        "plan.submit": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().submit_form(d, x, p.get("form")), c),
        "plan.say": lambda p, c: flow().run(plan_id(p), lambda d, x: say(d, x, text(p)), c),
        "plan.more": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().more_sources(d, x), c),
        "plan.decide": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().decide(d, text(p, "card_id", 40), p.get("adopt") is True), c),
        "plan.outline": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().make_outline(d, x), c),
        "plan.estimate": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().estimate(d), c),
        "plan.generate": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().generate(d, x), c),
        "plan.module": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().edit_module(
            d, "delete" if p.get("action") == "delete" else "move", text(p, "module_id", 40), p.get("direction", 0)), c),
        "plan.extend": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().extend_end(d, p.get("end")), c),
        "plan.start": lambda p, c: flow().run(plan_id(p), lambda d, x: flow().start(d), c),
        "plan.detail": lambda p, c: flow().detail(plan_id(p)),
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
