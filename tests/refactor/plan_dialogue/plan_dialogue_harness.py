"""PlanFlow test wiring. Only model/web I/O is substituted in offline checks.

No AppServices is constructed: it would also construct unrelated account/sync
services. The production register() and PlanFlow/CourseStore methods run intact.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import threading
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

from refactor.learning.app import plan_flow
from refactor.learning.app.bridge import CallContext
from refactor.learning.course import github_repo, planner_agents, source_filter
from refactor.learning.course.store import CourseStore
from refactor.learning.course.research import FetchedContent

HERE = Path(__file__).parent
ACCOUNT = "plan_dialogue_synthetic"
OUTPUT_ROOT = Path(r"D:\MentorFlow\.local\test-tmp\plan-dialogue")
ADMIN = "mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932"
ADMIN_ROOT = Path(r"D:\MentorFlowData\account-shell")


def utcnow():
    return datetime.now(timezone.utc)


def json_value(value):
    from dataclasses import asdict, is_dataclass
    from decimal import Decimal
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (date, Path, Decimal)):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(type(value).__name__)


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_value) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_cases():
    return json.loads((HERE / "plan_dialogue_cases.json").read_text(encoding="utf-8"))["cases"]


def form(today=None):
    today = today or date.today()
    return dict(goal="用 Python 处理 CSV 销售表并制作可靠的命令行报表工具",
                final_goal="独立完成读取、去重、分组汇总、导出，并用测试核对结果",
                level="beginner", level_note="会变量和循环，不会读写文件",
                notes="覆盖合成教材的全部八章；异常处理与数据清洗分别成模块，图表作为独立的可选模块。",
                start=today.isoformat(), end=(today + timedelta(days=35)).isoformat(),
                daily_cap=60, weekday_weights=[1, 1, 1, 1, 1, 0.5, 0], special_days=[], only_mine=False)


def clean_url(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


class OfflineWeb:
    """Explicit synthetic HTML, *not* evidence of real source content/quality."""
    def __init__(self):
        self.pages = {}
        self.calls = []

    def register_result(self, result):
        for hit in (*result.search_hits, *result.citations):
            if hit.url:
                self.pages[clean_url(hit.url)] = (hit.title, hit.snippet)
        for item in planner_agents.parse_research(result.text):
            self.pages.setdefault(clean_url(item["url"]), (item.get("title", ""), item.get("why", "")))

    def __call__(self, url, *, cancel, timeout):
        title, snippet = self.pages.get(clean_url(url), ("Python CSV 教程", ""))
        title = title or "Python CSV 教程"
        self.calls.append({"url": url, "mode": "offline_synthetic_html", "title": title})
        # Match the real hit title so title consistency checks remain in use.
        # Keep snippets separate from invented body in the trace.
        text = ("这是隔离测试的合成正文，不代表该网址实际内容。学习 Python CSV 文件读写，"
                "去重与分组汇总。示例：读取带表头的销售记录；练习：核对数据清洗前后的总额。") * 12
        body = ("<html><head><title>" + html.escape(title) + "</title></head><body><h1>" +
                html.escape(title) + "</h1><h2>基础与练习</h2><p>" + html.escape(text) +
                "</p><p>" + html.escape(snippet + " 来源标识：" + clean_url(url)) + "</p></body></html>")
        return FetchedContent(url, clean_url(url), "text/html; charset=utf-8", body.encode(), utcnow().isoformat())


class RepoWeb:
    def __init__(self, *, live=False, fetch=None):
        self.live = live
        self.fetch = fetch
        self.calls = []

    def __call__(self, url, cancel=None):
        if urlsplit(url).hostname not in {"api.github.com", "raw.githubusercontent.com"}:
            raise AssertionError("Unexpected repository fetch host")
        if self.fetch is None and not self.live:
            raise AssertionError("Real repository network is disabled")
        entry = {"url": url, "mode": "real_github" if self.live else "offline_repository", "at": str(utcnow())}
        self.calls.append(entry)
        try:
            result = (self.fetch or github_repo.default_fetch)(url, cancel=cancel)
            entry.update(bytes=len(result), sha256=hashlib.sha256(result).hexdigest())
            return result
        except Exception as exc:
            entry["error_type"] = type(exc).__name__
            raise


class DirectBridge:
    def __init__(self):
        self.handlers = {}
        self.events = []

    def register(self, name, fn, **_):
        self.handlers[name] = fn

    def push(self, name, data):
        self.events.append({"name": name, "data": copy.deepcopy(data)})


class Harness:
    def __init__(self, root, gateway, *, live_repo=False, repo_fetch=None):
        self.root = Path(root)
        self.web = OfflineWeb()
        self.repo = RepoWeb(live=live_repo, fetch=repo_fetch)
        self.gateway = gateway
        self.services = SimpleNamespace(
            account_id=ACCOUNT, data_root=self.root, store=CourseStore(ACCOUNT, self.root),
            gateway=gateway, catalog=SimpleNamespace(load_lists=source_filter.load_lists),
            settings=SimpleNamespace(load=lambda: {}))
        self.bridge = DirectBridge()
        self.flow = plan_flow.PlanFlow(self.services, self.bridge.push, date.today, fetcher=self.web)
        self.flow.repo_fetch = self.repo
        # Inject this real instance only into the lazy factory. All methods and
        # stage dispatch are production implementations, including register.say.
        with patch.object(plan_flow, "PlanFlow", return_value=self.flow):
            plan_flow.register(self.bridge, lambda: self.services, None, lambda _: date.today(), fetcher=self.web)
            self.call("plan.drafts")  # populate register's cache without creating a draft
        if hasattr(gateway, "web"):
            gateway.web = self.web

    def call(self, method, **params):
        context = CallContext("dialogue-" + str(len(self.bridge.events)), threading.Event(), lambda _: None)
        return self.bridge.handlers[method](params, context)

    def restore_pool_decisions(self, draft):
        for c in draft["sources"]:
            if c.get("url") and c["status"] in {"adopted", "rejected"}:
                fn = self.flow._pool(draft["plan_id"]).mark_accepted if c["status"] == "adopted" else self.flow._pool(draft["plan_id"]).mark_rejected
                fn(c["url"])

    def snapshot(self, pid):
        draft = copy.deepcopy(self.flow.load(pid))
        return {"draft": draft, "plan": self.services.store.get_plan(pid),
                "records": {k: self.services.store.records(k, pid) for k in
                            ("sources", "source_chunks", "stages", "modules", "points")}}

    def close(self):
        self.services.store.close()


def seed_sources(harness):
    """Synthetic saved precondition, imported through real material/store code."""
    draft = harness.flow.current()
    pid = draft["plan_id"]
    value, window = plan_flow.validate_form(form(), date.today())
    harness.services.store.create_plan(pid, goal=value["goal"], final_goal=value["final_goal"],
                                       level=value["level"], notes=value["notes"], window=window)
    draft.update(form=value, goal=value["goal"], focus=["Python", "CSV", "命令行", "异常", "测试"], phase="sources")
    harness.flow.save(draft)
    harness.flow.add_file(pid, HERE / "plan_dialogue_course.md")
    # A second URL source supplies an explicit previously rejected card.
    harness.call("plan.say", plan_id=pid, text="https://docs.python.org/3/tutorial/old-dialogue-fixture.html")
    rejected = harness.flow.load(pid)["sources"][-1]
    harness.call("plan.decide", plan_id=pid, card_id=rejected["card_id"], adopt=False)
    return pid


def question_prestate(harness, pid, case):
    draft = harness.flow.load(pid)
    draft["form"].update(goal="学 Python", final_goal="能写小工具", notes="", only_mine=True)
    draft.update(phase="questions", goal="学 Python", focus=[])
    question = case.get("question", "你想用 Python 写什么工具？需要处理什么输入，最后得到什么结果？")
    draft["messages"] = [dict(role="ai", kind="questions", text=question)]
    harness.services.store.save_intake(pid, goal="学 Python", final_goal="能写小工具",
                                      today=date.today(), reason="合成的追问前置状态")
    harness.flow.save(draft)


def clone_tree(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists() or source == target or source in target.parents:
        raise ValueError("A clone requires a fresh sibling test directory")
    shutil.copytree(source, target)


def modules(snapshot):
    return [m for s in (snapshot["draft"].get("outline") or {}).get("stages", []) for m in s["modules"]]


def render_case(case, before):
    case = copy.deepcopy(case)
    mods = modules(before)
    substitutions = {
        "start_plus_7": (date.fromisoformat(before["draft"]["form"]["start"]) + timedelta(days=7)).isoformat(),
        "end_plus_7": (date.fromisoformat(before["draft"]["form"]["end"]) + timedelta(days=7)).isoformat(),
        "split_title": mods[0]["title"] if mods else "",
        "merge_title_1": mods[0]["title"] if mods else "",
        "merge_title_2": mods[1]["title"] if len(mods) > 1 else "",
    }
    case["user"] = case["user"].format_map(substitutions)
    if isinstance(case["expected"].get("field_value"), str):
        case["expected"]["field_value"] = case["expected"]["field_value"].format_map(substitutions)
    case["rendered_variables"] = substitutions
    return case


class ScriptedGateway:
    """Only offline runner checks / explicitly labelled synthetic preconditions."""
    def __init__(self):
        self.calls = []
        self.web = None
        self.price_catalog = SimpleNamespace(estimate_cost=lambda *a, **k: None)

    def get_configuration(self):
        return {"provider_id": "deepseek", "model": "deepseek-v4-flash"}

    def complete(self, purpose, messages, **kwargs):
        self.calls.append({"purpose": purpose, "enable_search": kwargs.get("enable_search", False)})
        prompt = messages[-1]["content"]
        if purpose == "plan_point_review":
            from tests.refactor.planner.semantic_fakes34 import wire
            return SimpleNamespace(text=wire(messages), search_hits=(), citations=())
        if purpose == "sufficiency":
            value = {"enough": True, "questions": [], "goal": "Python CSV 报表工具", "focus": ["CSV", "中文", "练习"]}
        elif purpose == "outline":
            refs = list(dict.fromkeys(re.findall(r"\[(s\d+\.\d+)\]", prompt)))
            titles = ["Python 基础", "CSV 读写", "数据清洗", "异常处理", "汇总报表", "命令行工具", "测试与综合实战", "可选图表"]
            value = {"stages": [{"title": "合成前置轮廓", "modules": [
                {"key": "m" + str(i), "title": titles[i % len(titles)], "summary": "合成模块",
                 "refs": [ref], "minutes": [30, 60], "prerequisites": []} for i, ref in enumerate(refs)]}],
                "message": "此为离线合成前置状态。"}
        elif purpose == "generate":
            aliases = re.findall(r"\[(c\d+)\]", prompt)
            value = {"points": [{"title": "合成知识点" + str(i), "summary": "仅测试前置数据",
                                  "chunks": [aliases[0]], "minutes": 20} for i in range(2)]}
        elif purpose == "research":
            value = {"sources": [{"url": "https://docs.python.org/3/library/csv.html",
                                  "title": "Python CSV 教程", "why": "入门练习"}]}
        elif purpose == "source_scoring":
            value = {"scores": [{"id": item, "relevance": 5, "credibility": 5, "note": "合成评分"}
                                for item in re.findall(r"\[(r\d+)\]", prompt)]}
        elif purpose == "plan_intent":  # the obvious intent for the phase (the real one is judged live)
            payload = json.loads(prompt.split("\n", 1)[1])
            said = payload["text"]
            value = {"intents": [{"questions": {"kind": "answer"},
                                  "sources": {"kind": "find_sources", "instruction": said},
                                  "outline": {"kind": "revise_outline", "instruction": said},
                                  "ready": {"kind": "question", "answer": "合成回答"}}[payload["ctx"]["phase"]]], "reply": ""}
        else:
            raise AssertionError(purpose)
        result = SimpleNamespace(text=json.dumps(value, ensure_ascii=False), search_hits=(), citations=())
        if self.web and purpose == "research":
            self.web.register_result(result)
        return result
