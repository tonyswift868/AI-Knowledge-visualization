"""New learning window layer: bridge protocol, settings, handlers, export hook."""
import json
from pathlib import Path
import tempfile
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from refactor.learning.app.bridge import AppBridge, BridgeError
from refactor.learning.app import handlers
from refactor.learning.app.settings import AppSettings, DEFAULTS, validate

SCRATCH = Path("D:/MentorFlow/.local/test-tmp/app-layer")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tmp_root():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SCRATCH) as d:
        yield Path(d)


def pump(qapp, bridge, sent, predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        found = [m for m in sent if predicate(m)]
        if found:
            return found
        time.sleep(0.01)
    raise AssertionError(f"no matching bridge message; got {sent}")


def make_bridge(qapp):
    bridge = AppBridge()
    sent = []
    bridge.event.connect(lambda text: sent.append(json.loads(text)))
    return bridge, sent


def test_bridge_round_trip_error_mapping_and_stream(qapp):
    bridge, sent = make_bridge(qapp)
    bridge.register("t.ok", lambda p, c: {"echo": p["x"]})
    bridge.register("t.fail", lambda p, c: (_ for _ in ()).throw(BridgeError("NOPE", "不行。")))
    bridge.register("t.crash", lambda p, c: 1 / 0)

    def streaming(params, ctx):
        ctx.stream("a")
        ctx.stream("b")
        return "done"
    bridge.register("t.stream", streaming)
    bridge.call(json.dumps({"id": "r1", "method": "t.ok", "params": {"x": 5}}))
    bridge.call(json.dumps({"id": "r2", "method": "t.fail", "params": {}}))
    bridge.call(json.dumps({"id": "r3", "method": "t.crash", "params": {}}))
    bridge.call(json.dumps({"id": "r4", "method": "t.stream", "params": {}}))
    bridge.call(json.dumps({"id": "r5", "method": "no.such", "params": {}}))
    pump(qapp, bridge, sent, lambda m: m.get("id") == "r4" and m["type"] == "response")
    pump(qapp, bridge, sent, lambda m: m.get("id") == "r3")
    by_id = {m["id"]: m for m in sent if m["type"] == "response"}
    assert by_id["r1"] == {"type": "response", "id": "r1", "ok": True, "result": {"echo": 5}}
    assert by_id["r2"]["error"] == {"code": "NOPE", "message": "不行。"}
    assert by_id["r3"]["error"]["code"] == "INTERNAL"          # details never leak to the page
    assert by_id["r5"]["error"]["code"] == "UNKNOWN_METHOD"
    assert [m["delta"] for m in sent if m["type"] == "stream"] == ["a", "b"]
    bridge.close()


def test_bridge_rejects_malformed_and_supports_cancel(qapp):
    bridge, sent = make_bridge(qapp)
    started = threading.Event()

    def slow(params, ctx):
        started.set()
        ctx.cancel.wait(3)
        raise BridgeError("STOPPED", "stopped")
    bridge.register("t.slow", slow)
    for bad in ("not json", json.dumps({"id": "../x", "method": "t.slow"}), json.dumps({"id": "ok", "method": "t.slow", "params": []})):
        bridge.call(bad)
    bridge.call(json.dumps({"id": "s1", "method": "t.slow", "params": {}}))
    assert started.wait(2)
    bridge.call(json.dumps({"id": "c1", "method": "cancel", "params": {"target": "s1"}}))
    msg = pump(qapp, bridge, sent, lambda m: m.get("id") == "s1")[0]
    assert msg["error"]["code"] == "CANCELLED"
    assert all(m.get("id") == "s1" for m in sent)   # malformed requests got no response
    bridge.close()


def test_settings_validate_and_persist(tmp_root):
    settings = AppSettings("mfa1_" + "a" * 64, tmp_root)
    assert settings.load() == DEFAULTS
    saved = settings.save({"theme": "aurora", "analogy_domains": ["games", "music"], "refresh_time": "03:30"})
    assert saved["theme"] == "aurora" and AppSettings("mfa1_" + "a" * 64, tmp_root).load()["refresh_time"] == "03:30"
    for bad in ({"theme": "neon"}, {"analogy_domains": []}, {"refresh_time": "25:00"}, {"font_scale": 3}, {"unknown": 1}):
        with pytest.raises(BridgeError):
            validate({**DEFAULTS, **bad})


class _Window:
    def __init__(self, services):
        self.services = services
        self.session_notice = None
        self.actions = []

    def account_info(self):
        return {"name": "测试", "role_label": "学习者"}

    def request_account_action(self, action):
        self.actions.append(action)

    def window_action(self, action):
        return False

    def settings_changed(self, settings):
        pass


def test_handlers_against_real_store(qapp, tmp_root):
    from datetime import date, timedelta
    from refactor.learning.app.services import AppServices
    from refactor.learning.course.ledger import PlanWindow
    account = "mfa1_" + "b" * 64
    services = AppServices(account_id=account, data_root=tmp_root, session_provider=lambda: None,
                           api_base_url="https://api.invalid", ca_bundle=tmp_root / "ca.pem")
    today = date.today()
    store = services.store
    store.create_plan("p1", goal="学 ReAct", final_goal="能搭智能体", level="入门",
                      window=PlanWindow(today - timedelta(days=1), today + timedelta(days=5), 120))
    bridge, sent = make_bridge(qapp)
    window = _Window(services)
    handlers.register(bridge, window)
    bridge.call(json.dumps({"id": "b1", "method": "app.bootstrap", "params": {}}))
    bridge.call(json.dumps({"id": "b2", "method": "plans.list", "params": {}}))
    bridge.call(json.dumps({"id": "b3", "method": "settings.save", "params": {"changes": {"theme": "aurora"}}}))
    bridge.call(json.dumps({"id": "b4", "method": "app.account", "params": {"action": "logout"}}))
    bridge.call(json.dumps({"id": "b5", "method": "app.account", "params": {"action": "rm -rf"}}))
    bridge.call(json.dumps({"id": "b6", "method": "usage.summary", "params": {"days": 7}}))
    pump(qapp, bridge, sent, lambda m: m.get("id") == "b6")
    pump(qapp, bridge, sent, lambda m: m.get("id") == "b2")
    pump(qapp, bridge, sent, lambda m: m.get("id") == "b3")
    by_id = {m["id"]: m for m in sent if m.get("type") == "response"}
    assert by_id["b1"]["result"]["ready"] is True
    plans = by_id["b2"]["result"]["plans"]
    assert [p["goal"] for p in plans] == ["学 ReAct"] and plans[0]["state"] == "creating" and plans[0]["today"] is None
    assert by_id["b3"]["result"]["settings"]["theme"] == "aurora"
    assert window.actions == ["logout"] and by_id["b5"]["ok"] is False
    assert by_id["b6"]["ok"] is True and by_id["b6"]["result"]["total"]["calls"] == 0
    bridge.close()
    services.close()


def test_export_hook_without_plans_reports_no_current_plan(qapp, tmp_root):
    from refactor.learning.app.window import LearningWindow
    from refactor.learning.plan_export import ExportStatus
    win = LearningWindow("测试", "LEARNER", "api", account_id="mfa1_" + "c" * 64, data_root=tmp_root)
    assert win.bind_feature_context(session_provider=lambda: None, api_base_url="https://api.invalid",
                                    ca_bundle=tmp_root / "ca.pem")
    results = []
    win.request_current_plan_export(results.append)
    assert len(results) == 1 and results[0].status is ExportStatus.NO_CURRENT_PLAN
    assert not win.bind_feature_context(session_provider=lambda: None, api_base_url="http://insecure.invalid",
                                        ca_bundle=tmp_root / "ca.pem")
    win.dispose()
