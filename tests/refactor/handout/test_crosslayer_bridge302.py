from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE = Path(__file__).resolve().parents[3] / "scripts/handout/crosslayer_bridge.py"
SPEC = importlib.util.spec_from_file_location("crosslayer_bridge302", MODULE)
bridge_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(bridge_module)


def test_literal_bridge_registration_and_runtime_events(tmp_path):
    app = tmp_path / "refactor/learning/app"
    web = tmp_path / "refactor/learning/web/src"
    app.mkdir(parents=True)
    (web / "pages").mkdir(parents=True)
    (app / "handlers.py").write_text(
        'def register(bridge):\n'
        '    bridge.register("direct", direct)\n'
        '    methods = {"mapped": mapped}\n'
        '    for name, fn in methods.items():\n'
        '        bridge.register(name, wrap(fn))\n'
        '    names = ("check", "install")\n'
        '    for action in names:\n'
        '        bridge.register("update." + action, lambda p: update_action(action, p))\n'
        'def direct(p): pass\n'
        'def mapped(p): pass\n'
        'def update_action(action, p): pass\n', encoding="utf-8")
    (app / "bridge.py").write_text(
        'class Bridge:\n'
        '    def register(self, name, fn): pass\n'
        '    def push(self, name, data=None): pass\n', encoding="utf-8")
    (app / "sync.py").write_text(
        'class Sync:\n'
        '    def changed(self):\n'
        '        self.bridge.push("plans_changed", {})\n'
        '        self.bridge.push("unheard", {})\n', encoding="utf-8")
    (web / "bridge.ts").write_text(
        'export function call(method: string) {}\n'
        'export function on(name: string, fn: unknown) {}\n', encoding="utf-8")
    (web / "pages" / "Page.tsx").write_text(
        'import { call, on } from "../bridge"\n'
        'const act = React.useCallback(async (method) => call(method), [])\n'
        '// call("comment.only")\n'
        'export function Page() {\n'
        '  call("direct")\n'
        '  call("mapped")\n'
        '  call("update.install")\n'
        '  call("literal.but.unregistered")\n'
        '  call(enabled ? "conditional.one" : "conditional.two")\n'
        '  call(method)\n'
        '  request(method)\n'
        '  act(action)\n'
        '  on("plans_changed", () => reload())\n'
        '}\n', encoding="utf-8")

    result = bridge_module.extract(tmp_path)
    by_name = {row["name"]: row for row in result["connections"]}
    assert by_name["direct"]["target"]["function"] == "direct"
    assert by_name["mapped"]["target"]["function"] == "mapped"
    assert by_name["update.install"]["target"]["function"] == "update_action"
    assert by_name["plans_changed"]["source"]["function"] == "Sync.changed"
    assert by_name["plans_changed"]["target"]["function"].endswith("reload")
    assert any(ep["file"].endswith("bridge.ts") for ep in by_name["direct"]["via"])
    assert any(ep["file"].endswith("bridge.py") for ep in by_name["plans_changed"]["via"])
    dynamic_names = {row["name"] for row in result["dynamic"]}
    assert dynamic_names == {"method", "action"}
    assert "comment.only" not in dynamic_names
    assert all(row["source"]["file"].endswith("Page.tsx") for row in result["dynamic"])
    unresolved_names = {row["name"] for row in result["unresolved"]}
    assert {"literal.but.unregistered", "conditional.one", "conditional.two", "unheard"} <= unresolved_names


def test_verified_flow_factory_resolves_callback_and_direct_methods(tmp_path):
    app = tmp_path / "refactor/learning/app"
    web = tmp_path / "refactor/learning/web/src"
    app.mkdir(parents=True)
    (web / "pages").mkdir(parents=True)
    (web / "bridge.ts").write_text('export function call(method: string) {}\n', encoding="utf-8")
    (web / "pages" / "Page.tsx").write_text(
        'import { call } from "../bridge"\n'
        'export function Page() {\n'
        '  call("plan.decide")\n'
        '  call("plan.extend")\n'
        '  call("plan.detail")\n'
        '  call("unknown.receiver")\n'
        '}\n', encoding="utf-8")
    (app / "plan_flow.py").write_text(
        'class PlanFlow:\n'
        '    def run(self, draft, work, ctx):\n'
        '        return work(draft, ctx)\n'
        '    def decide(self, draft): pass\n'
        '    def extend_end(self, draft): pass\n'
        '    def detail(self, plan_id): pass\n'
        'def register(bridge):\n'
        '    flows = {}\n'
        '    def flow():\n'
        '        key = 1\n'
        '        if key not in flows:\n'
        '            flows[key] = PlanFlow()\n'
        '        return flows[key]\n'
        '    def plan_detail(params):\n'
        '        current_flow = flow()\n'
        '        return current_flow.detail(params)\n'
        '    handlers = {\n'
        '        "plan.decide": lambda p, c: flow().run(p, lambda d, x: flow().decide(d), c),\n'
        '        "plan.extend": lambda p: flow().extend_end(p),\n'
        '        "plan.detail": plan_detail,\n'
        '        "unknown.receiver": lambda p: unknown().decide(p),\n'
        '    }\n'
        '    for name, fn in handlers.items():\n'
        '        bridge.register(name, fn)\n', encoding="utf-8")

    result = bridge_module.extract(tmp_path)
    rows = {row["name"]: row for row in result["connections"]}
    decide = rows["plan.decide"]
    assert decide["target"]["function"] == "PlanFlow.decide"
    assert [endpoint["function"] for endpoint in decide["via"] if endpoint["function"].startswith("PlanFlow.")] == [
        "PlanFlow.run", "PlanFlow.decide"]
    assert "only when run executes that callback" in decide["rule"]
    assert rows["plan.extend"]["target"]["function"] == "PlanFlow.extend_end"
    assert rows["plan.detail"]["target"]["function"] == "plan_detail"
    assert "PlanFlow.detail" in [endpoint["function"] for endpoint in rows["plan.detail"]["via"]]
    assert "unknown.receiver" not in rows
    unresolved = {row["name"]: row for row in result["unresolved"]}
    assert "unknown.receiver" in unresolved
    assert "receiver" in unresolved["unknown.receiver"]["reason"]


def test_static_two_and_three_item_registration_tuples_bind_self_methods(tmp_path):
    app = tmp_path / "refactor/learning/app"
    web = tmp_path / "refactor/learning/web/src"
    app.mkdir(parents=True)
    (web / "pages").mkdir(parents=True)
    (web / "bridge.ts").write_text('export function call(method: string) {}\n', encoding="utf-8")
    (web / "pages" / "Page.tsx").write_text(
        'import { call } from "../bridge"\n'
        'export function Page() { call("speech.status"); call("voice.start") }\n', encoding="utf-8")
    (app / "speech.py").write_text(
        'class Speech:\n'
        '    def __init__(self, bridge):\n'
        '        for name, fn, ui in (("status", self.status, False), ("download", self.download, True)):\n'
        '            bridge.register("speech." + name, fn, ui=ui)\n'
        '    def status(self): pass\n'
        '    def download(self): pass\n', encoding="utf-8")
    (app / "voice_input.py").write_text(
        'class Voice:\n'
        '    def __init__(self, bridge):\n'
        '        for name, handler in (("status", self.status), ("start", self.start)):\n'
        '            bridge.register("voice." + name, handler, ui=True)\n'
        '    def status(self): pass\n'
        '    def start(self): pass\n', encoding="utf-8")

    result = bridge_module.extract(tmp_path)
    rows = {row["name"]: row for row in result["connections"]}
    assert rows["speech.status"]["target"]["function"] == "Speech.status"
    assert rows["voice.start"]["target"]["function"] == "Voice.start"
    assert all("运行时" not in row["reason"] for row in result["dynamic"])
    unresolved = {row["name"]: row for row in result["unresolved"]}
    assert unresolved["speech.download"]["reason"].startswith("只找到Python")
