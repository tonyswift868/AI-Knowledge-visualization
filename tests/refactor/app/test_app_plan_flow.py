"""New-plan chain end to end with a scripted model and a fake web (ARCH-06)."""
import json
import re
import threading
from datetime import date, timedelta
from types import SimpleNamespace

from refactor.learning.app import handlers
from refactor.learning.app.services import AppServices
from refactor.learning.connections.providers.base import WebHit
from refactor.learning.planning.research import FetchedContent

from .test_app_window_layer import make_bridge, pump, qapp, tmp_root  # noqa: F401

ACCOUNT = "mfa1_" + "e" * 64
PAGES = {
    "https://docs.python.org/3/tutorial/classes.html": "Python 教程：类",
    "https://docs.python.org/3/tutorial/errors.html": "Python 教程：错误和异常",
    "https://docs.python.org/3/tutorial/modules.html": "Python 教程：模块",
}


def page(title):
    body = "".join(f"<h2>{title} 第{i}节</h2><p>" + f"{title} 的第 {i} 部分讲解，包含定义、例子和练习。" * 12 + "</p>"
                   for i in range(1, 4))
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>".encode("utf-8")


def fetcher(url, *, cancel, timeout):
    return FetchedContent(url, url, "text/html; charset=utf-8", page(PAGES[url]), "2026-09-27T00:00:00Z")


class ScriptedModel:
    """Answers by purpose; asserts the host never lets a model change state directly."""

    def __init__(self):
        self.calls = []
        self.sufficiency = [{"enough": False, "questions": ["你想做到能写什么样的程序？"], "goal": "学 Python 面向对象", "focus": []},
                            {"enough": True, "questions": [], "goal": "三周学会 Python 类、异常和模块", "focus": ["类", "异常"]}]
        self.generate_failures = 2   # both the first answer and the host retry are malformed
        self.intents = []            # queued intent answers; otherwise the obvious one for the phase
        self.lock = threading.Lock()

    def get_configuration(self):
        return {"provider_id": "deepseek", "model": "deepseek-v4-flash"}

    price_catalog = SimpleNamespace(estimate_cost=lambda *a, **k: None)

    def complete(self, purpose, messages, *, system=None, enable_search=False, on_text=None, cancel=None, plan_id="", max_tokens=4096):
        with self.lock:
            self.calls.append((purpose, enable_search, max_tokens))
        user = messages[-1]["content"]
        hits = ()
        if purpose == "sufficiency":
            text = json.dumps(self.sufficiency.pop(0), ensure_ascii=False)
        elif purpose == "research":
            assert enable_search
            hits = tuple(WebHit(url, title, "") for url, title in PAGES.items())
            text = "我搜到了这些。" + json.dumps({"sources": [{"url": "https://docs.python.org/3/tutorial/classes.html", "title": "类", "why": "官方"}]})
        elif purpose == "source_scoring":
            ids = re.findall(r"\[(r\d+)\]", user)
            text = json.dumps({"scores": [{"id": i, "relevance": 5, "credibility": 5, "note": "官方教程"} for i in ids]})
        elif purpose == "outline":
            refs = re.findall(r"\[(s\d+\.\d+)\]", user)
            modules = [{"key": f"m{i}", "title": f"模块{i}", "summary": f"学第{i}块", "refs": [ref],
                        "prerequisites": [f"m{i - 1}"] if i > 1 else [], "minutes": [30, 60]} for i, ref in enumerate(refs[:3], 1)]
            if "修改意见" in user:
                modules[0]["title"] = "改过的模块1"
            text = json.dumps({"stages": [{"title": "基础", "modules": modules}], "message": "先打基础再进阶。"}, ensure_ascii=False)
        elif purpose == "generate":
            aliases = re.findall(r"\[(c\d+)\]", user)
            with self.lock:
                fail = self.generate_failures > 0 and "模块2" in user.split("【本模块】")[1][:10]
                if fail:
                    self.generate_failures -= 1
            text = "不是 JSON" if fail else json.dumps({"points": [
                {"title": f"点{i}", "summary": f"掌握第{i}件事", "chunks": [aliases[0]], "minutes": 10} for i in range(1, 4)]})
        elif purpose == "plan_intent":
            payload = json.loads(user.split("\n", 1)[1])
            phase, said = payload["ctx"]["phase"], payload["text"]
            with self.lock:
                queued = self.intents.pop(0) if self.intents else None
            default = {"questions": {"kind": "answer"}, "sources": {"kind": "find_sources", "instruction": said},
                       "outline": {"kind": "revise_outline", "instruction": said}}.get(phase)
            text = json.dumps({"intents": queued or [default], "reply": ""}, ensure_ascii=False)
        else:
            raise AssertionError(purpose)
        return SimpleNamespace(text=text, search_hits=hits, citations=())

    def close(self):
        pass


class Window:
    research_fetcher = staticmethod(fetcher)

    def __init__(self, services):
        self.services = services
        self.session_notice = None

    def account_info(self):
        return {"name": "测试", "role_label": "学习者"}


def form(today):
    return {"goal": "学 Python", "final_goal": "能写小工具", "level": "beginner", "level_note": "", "notes": "",
            "start": today.isoformat(), "end": (today + timedelta(days=20)).isoformat(), "daily_cap": 90,
            "weekday_weights": [1, 1, 1, 1, 1, 0.5, 0], "special_days": [{"date": (today + timedelta(days=3)).isoformat(), "weight": 0}]}


def test_new_plan_chain_from_form_to_start(qapp, tmp_root):
    services = AppServices(account_id=ACCOUNT, data_root=tmp_root, session_provider=lambda: None,
                           api_base_url="https://api.invalid", ca_bundle=tmp_root / "ca.pem")
    model = services._gateway = ScriptedModel()
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, Window(services))
    n = iter(range(1000))

    def ask(method, **params):
        rid = f"q{next(n)}"
        bridge.call(json.dumps({"id": rid, "method": method, "params": params}))
        reply = pump(qapp, bridge, sent, lambda m: m.get("id") == rid and m["type"] == "response", timeout=20)[0]
        assert reply["ok"], reply
        return reply["result"]

    draft = ask("plan.current")["draft"]
    pid = draft["plan_id"]
    assert draft["phase"] == "form" and ask("plan.current")["draft"]["plan_id"] == pid   # reopened, not duplicated
    today = date.today()
    bad = dict(form(today), goal="  ")
    bridge.call(json.dumps({"id": "bad", "method": "plan.submit", "params": {"plan_id": pid, "form": bad}}))
    assert pump(qapp, bridge, sent, lambda m: m.get("id") == "bad")[0]["error"]["code"] == "FORM_MISSING"
    assert not model.calls                                   # required fields are the host's job

    draft = ask("plan.submit", plan_id=pid, form=form(today))["draft"]
    assert draft["phase"] == "questions" and "写什么样的程序" in draft["messages"][-1]["text"]
    draft = ask("plan.say", plan_id=pid, text="能写命令行小工具")["draft"]
    assert draft["phase"] == "sources" and draft["goal"].startswith("三周")
    cards = [c for c in draft["sources"] if c["status"] == "new"]
    assert len(cards) == 3 and all(c["tier"] == "whitelist" for c in cards)
    ask("plan.decide", plan_id=pid, card_id=cards[0]["card_id"], adopt=True)
    ask("plan.decide", plan_id=pid, card_id=cards[1]["card_id"], adopt=True)
    draft = ask("plan.decide", plan_id=pid, card_id=cards[2]["card_id"], adopt=False)["draft"]
    assert [c["status"] for c in draft["sources"]] == ["adopted", "adopted", "rejected"]

    draft = ask("plan.outline", plan_id=pid)["draft"]
    modules = [m for s in draft["outline"]["stages"] for m in s["modules"]]
    assert draft["phase"] == "outline" and len(modules) == 3 and "spans" not in modules[0]
    assert draft["outline"]["ranges"]["total"][0] > 0
    draft = ask("plan.say", plan_id=pid, text="第一个模块换个名字")["draft"]
    assert draft["outline"]["stages"][0]["modules"][0]["title"] == "改过的模块1" and draft["outline"]["version"] == 2
    # The chat box changes settings through the host, never by the model's say-so (BUG-13).
    model.intents = [[{"kind": "update_form", "daily_cap": 120, "notes_append": "多做练习"}]]
    draft = ask("plan.say", plan_id=pid, text="每天改成两小时，多给点练习")["draft"]
    assert draft["form"]["daily_cap"] == 120 and draft["form"]["notes"] == "多做练习"
    assert services.store.get_plan(pid)["window"]["daily_cap_minutes"] == 120
    assert draft["messages"][-1]["text"].startswith("已修改：每天最多学习时长改为 120 分钟")
    model.intents = [[{"kind": "refuse", "reason": "路线要从资料里来，不能跳过资料。"}]]
    draft = ask("plan.say", plan_id=pid, text="别管资料了直接生成")["draft"]
    assert draft["messages"][-1]["text"] == "路线要从资料里来，不能跳过资料。" and draft["phase"] == "outline"
    model.intents = [[{"kind": "proceed", "step": "generate"}]]
    draft = ask("plan.say", plan_id=pid, text="开始生成吧")["draft"]     # a paid step: show the estimate, never start it
    assert draft["estimate"]["modules"] == 3 and "amount" not in draft["estimate"]
    assert draft["estimate"]["calls"] >= 3
    assert draft["phase"] == "outline" and not any(c[0] == "generate" for c in model.calls)

    draft = ask("plan.generate", plan_id=pid)["draft"]
    assert draft["phase"] == "generating" and sorted(draft["generation"].values()) == ["done", "done", "failed"]
    draft = ask("plan.generate", plan_id=pid)["draft"]                # retries only the failed module
    assert draft["phase"] == "ready" and set(draft["generation"].values()) == {"done"}
    assert sum(1 for c in model.calls if c[0] == "generate") == 5    # 3 + retry + second round
    assert all(tokens == 8192 for purpose, _, tokens in model.calls if purpose in ("generate", "outline"))

    detail = ask("plan.detail", plan_id=pid)
    mods = [m for s in detail["stages"] for m in s["modules"]]
    assert len(mods) == 3 and all(len(m["points"]) == 3 for m in mods) and detail["plan"]["state"] == "ready"
    assert len(detail["sources"]) == 2 and detail["daily"]["cap"] == 120
    model.intents = [[{"kind": "edit_modules", "delete": [], "move": [{"n": 2, "to": 1}]}]]
    draft = ask("plan.say", plan_id=pid, text="把第二个模块挪到最前面")["draft"]
    assert "依赖" in draft["messages"][-1]["text"]                   # module 2 needs module 1
    model.intents = [[{"kind": "edit_modules", "delete": [3], "move": []}]]
    draft = ask("plan.say", plan_id=pid, text="第三个模块不要了")["draft"]
    assert draft["messages"][-1]["text"] == f"已删掉《{mods[2]['title']}》。"
    assert len(services.store.records("modules", pid)) == 2
    bridge.call(json.dumps({"id": "mv", "method": "plan.module", "params": {"plan_id": pid, "action": "move", "module_id": mods[1]["id"], "direction": -1}}))
    assert pump(qapp, bridge, sent, lambda m: m.get("id") == "mv")[0]["error"]["code"] == "ORDER_INVALID"   # module 2 needs module 1
    ask("plan.start", plan_id=pid)
    plans = ask("plans.list")["plans"]
    assert plans[0]["state"] == "active" and plans[0]["module_count"] == 2
    assert ask("plan.drafts")["drafts"] == []                          # the creation conversation is gone
    assert any(m.get("name") == "plan_started" for m in sent if m["type"] == "event")
    bridge.close()
    services.close()


def test_discard_removes_plan_and_draft(qapp, tmp_root):
    services = AppServices(account_id=ACCOUNT, data_root=tmp_root, session_provider=lambda: None,
                           api_base_url="https://api.invalid", ca_bundle=tmp_root / "ca.pem")
    services._gateway = ScriptedModel()
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, Window(services))
    bridge.call(json.dumps({"id": "a", "method": "plan.current", "params": {}}))
    pid = pump(qapp, bridge, sent, lambda m: m.get("id") == "a")[0]["result"]["draft"]["plan_id"]
    bridge.call(json.dumps({"id": "b", "method": "plan.submit", "params": {"plan_id": pid, "form": form(date.today())}}))
    pump(qapp, bridge, sent, lambda m: m.get("id") == "b", timeout=20)
    assert [p["id"] for p in services.store.list_plans()] == [pid]
    bridge.call(json.dumps({"id": "c", "method": "plan.discard", "params": {"plan_id": pid}}))
    assert pump(qapp, bridge, sent, lambda m: m.get("id") == "c")[0]["result"] == {"deleted": True}
    assert services.store.list_plans() == []
    bridge.call(json.dumps({"id": "d", "method": "plan.get", "params": {"plan_id": pid}}))
    assert pump(qapp, bridge, sent, lambda m: m.get("id") == "d")[0]["error"]["code"] == "DRAFT_NOT_FOUND"
    bridge.close()
    services.close()


def test_links_from_form_and_chat_are_opened_and_adopted_without_search(qapp, tmp_root):
    services = AppServices(account_id=ACCOUNT, data_root=tmp_root, session_provider=lambda: None,
                           api_base_url="https://api.invalid", ca_bundle=tmp_root / "ca.pem")
    model = services._gateway = ScriptedModel()
    model.sufficiency = [{"enough": True, "questions": [], "goal": "学 Python 类", "focus": []}]
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, Window(services))
    n = iter(range(100))

    def ask(method, **params):
        rid = f"l{next(n)}"
        bridge.call(json.dumps({"id": rid, "method": method, "params": params}))
        reply = pump(qapp, bridge, sent, lambda m: m.get("id") == rid and m["type"] == "response", timeout=20)[0]
        assert reply["ok"], reply
        return reply["result"]

    try:
        pid = ask("plan.current")["draft"]["plan_id"]
        linked = dict(form(date.today()), notes="主要用这个：https://docs.python.org/3/tutorial/classes.html", only_mine=True)
        draft = ask("plan.submit", plan_id=pid, form=linked)["draft"]
        mine = [c for c in draft["sources"] if c["note"] == "你提供的链接"]
        assert [c["url"] for c in mine] == ["https://docs.python.org/3/tutorial/classes.html"] and mine[0]["status"] == "adopted"
        assert not any(c[0] == "research" for c in model.calls)       # only_mine + a link: no web search
        draft = ask("plan.say", plan_id=pid, text="再加上 https://docs.python.org/3/tutorial/errors.html")["draft"]
        assert [c["url"] for c in draft["sources"] if c["status"] == "adopted"] == [
            "https://docs.python.org/3/tutorial/classes.html", "https://docs.python.org/3/tutorial/errors.html"]
        assert not any(c[0] == "research" for c in model.calls)       # a message that is only a link does not search
        assert len(services.store.records("sources", pid)) == 2
    finally:
        bridge.close()
        services.close()


def test_a_github_repo_link_imports_its_docs(qapp, tmp_root):
    from tests.refactor.planner.test_github_repo_import import fake_fetch
    services = AppServices(account_id=ACCOUNT, data_root=tmp_root, session_provider=lambda: None,
                           api_base_url="https://api.invalid", ca_bundle=tmp_root / "ca.pem")
    model = services._gateway = ScriptedModel()
    model.sufficiency = [{"enough": True, "questions": [], "goal": "学智能体", "focus": []}]
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, Window(services))
    try:
        bridge.call(json.dumps({"id": "g1", "method": "plan.current", "params": {}}))
        pid = pump(qapp, bridge, sent, lambda m: m.get("id") == "g1")[0]["result"]["draft"]["plan_id"]
        # PlanFlow is created lazily per services; swap the repository fetcher for the fake one.
        from refactor.learning.app import plan_flow
        flows = [o for o in __import__("gc").get_objects() if isinstance(o, plan_flow.PlanFlow)]
        for f in flows:
            f.repo_fetch = fake_fetch([])
        linked = dict(form(date.today()), notes="教材：https://github.com/acme/book", only_mine=True)
        bridge.call(json.dumps({"id": "g2", "method": "plan.submit", "params": {"plan_id": pid, "form": linked}}))
        draft = pump(qapp, bridge, sent, lambda m: m.get("id") == "g2", timeout=20)[0]["result"]["draft"]
        cards = [c for c in draft["sources"] if c["status"] == "adopted"]
        assert [c["url"] for c in cards] == ["https://github.com/acme/book"] and cards[0]["site"] == "github.com"
        chunks = services.store.records("source_chunks", pid)
        assert chunks and {tuple(c["section"])[0] for c in chunks} >= {"第二章 发展史"}
    finally:
        bridge.close()
        services.close()
