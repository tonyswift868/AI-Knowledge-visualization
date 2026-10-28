"""Account lifecycle and model configuration bridge wiring; no model/network use."""
import json
from types import SimpleNamespace

import pytest

from refactor.learning.app import handlers, chat, rag, classroom, plan_flow
from refactor.learning.app.bridge import CallContext
from refactor.learning.connections import balance
from refactor.learning.course import self_test


class Signal:
    def __init__(self):
        self.listeners = []

    def connect(self, listener):
        self.listeners.append(listener)

    def emit(self, value):
        for listener in tuple(self.listeners):
            listener(value)


class FakeGateway:
    def __init__(self):
        self.listener = None
        self.calls = []
        self.active_id = "preset-a"

    def get_configuration(self):
        return {"id": self.active_id, "provider_id": "deepseek"}

    def list_presets(self):
        return [{"id": self.active_id, "provider_id": "deepseek"}]

    def search_supported(self):
        return True

    def save_preset(self, provider_id, **kwargs):
        self.calls.append(("save", provider_id, kwargs))
        self.active_id = kwargs.get("preset_id", self.active_id)
        return {"id": self.active_id, "provider_id": provider_id}

    def select_preset(self, preset_id):
        self.calls.append(("select", preset_id))
        self.active_id = preset_id

    def delete_preset(self, preset_id):
        self.calls.append(("delete", preset_id))
        self.active_id = "preset-b"


class FakeServices:
    def __init__(self, account_id):
        self.account_id = account_id
        self._closed = False
        self.settings = SimpleNamespace(snapshot=lambda: {
            "settings": {"theme": "sakura"}, "settings_revision": 1, "account_id": account_id,
        })
        self.gateway = FakeGateway()
        self.status = {"state": "unknown", "short": "模型待验证"}
        self.status_listener = None
        self.probe_pending = False
        self.probes = []
        self.config_changes = 0

    def bind_model_status(self, listener):
        self.status_listener = listener

    def model_status(self):
        return dict(self.status)

    def start_model_auto_check(self, *, retry_network=False):
        if self._closed or self.probe_pending:
            return False
        self.probe_pending = True
        self.probes.append(("start", retry_network))
        return True

    def on_network_restored(self):
        if self.status.get("state") != "error" or self.status.get("fault", {}).get("category") != "network":
            return False
        return self.start_model_auto_check(retry_network=True)

    def model_configuration_changed(self):
        self.config_changes += 1
        self.probe_pending = False
        self.status = {"state": "unknown", "short": "模型待验证"}
        self.start_model_auto_check()


class FakeBridge:
    def __init__(self):
        self.event = Signal()
        self.handlers = {}
        self.ui_methods = set()
        self.events = []

    def register(self, name, function, ui=False):
        self.handlers[name] = function
        if ui:
            self.ui_methods.add(name)

    def push(self, name, data=None):
        self.events.append((name, data))


def _wire(monkeypatch, services):
    for module in (chat, rag, self_test, plan_flow, classroom, balance):
        monkeypatch.setattr(module, "register", lambda *args, **kwargs: None)
    bridge = FakeBridge()
    window = SimpleNamespace(
        services=services,
        account_info=lambda: {"account_id": services.account_id, "offline": False},
        session_notice=None,
    )
    handlers.register(bridge, window)
    context = CallContext("model199-test", __import__("threading").Event(), lambda _text: None)
    return bridge, window, context


def _event(name):
    return json.dumps({"type": "event", "name": name, "data": None})


def test_model_handlers199_ready_bootstrap_recovery_and_switch(monkeypatch):
    first = FakeServices("account-first")
    bridge, window, ctx = _wire(monkeypatch, first)

    # register() binds and starts at the already-ready account service;
    # bootstrap and a ready event cannot create duplicate in-flight checks.
    assert first.probes == [("start", False)]
    bootstrap = bridge.handlers["app.bootstrap"]({}, ctx)
    assert bootstrap["ready"] is True
    assert bootstrap["account_id"] == first.account_id
    assert bootstrap["settings_revision"] == 1
    assert bootstrap["settings"] == {"theme": "sakura"}
    bridge.event.emit(_event("ready"))
    assert first.probes == [("start", False)]

    # The existing session recovery event retries only a remembered network fault.
    first.probe_pending = False  # model the completed initial offline probe
    first.status = {"state": "error", "fault": {"category": "network"}}
    bridge.event.emit(_event("session_recovered"))
    assert first.probes[-1] == ("start", True)
    first.status = {"state": "error", "fault": {"category": "balance"}}
    first.probe_pending = False
    bridge.event.emit(_event("session_recovered"))
    assert first.probes[-1] == ("start", True)

    # Switching services makes the old status closure inert; a new ready event
    # binds and starts a check for the selected account.
    old_listener = first.status_listener
    second = FakeServices("account-second")
    window.services = second
    bridge.event.emit(_event("scope_changed"))
    bridge.event.emit(_event("ready"))
    assert second.probes == [("start", False)]
    old_listener({"state": "ok", "short": "模型正常"})
    assert not [event for event in bridge.events if event[0] == "model_status"]
    second.status_listener({"state": "ok", "short": "模型正常"})
    assert bridge.events[-1] == ("model_status", {"state": "ok", "short": "模型正常", "account_id": "account-second"})


@pytest.mark.parametrize("method,params", [
    ("models.save", {"provider_id": "deepseek", "preset_id": "preset-a", "api_key": "fake-key"}),
    ("models.select", {"preset_id": "preset-b"}),
    ("models.delete", {"preset_id": "preset-a"}),
])
def test_model_handlers199_configuration_changes_recheck(monkeypatch, method, params):
    services = FakeServices("account-config")
    bridge, _window, ctx = _wire(monkeypatch, services)
    bridge.handlers[method](params, ctx)
    assert services.config_changes == 1
    # The changed active config starts a new, non-network-recovery check.
    assert services.probes[-1] == ("start", False)


def test_model_handlers199_status_is_bound_to_account_scope(monkeypatch):
    services = FakeServices("account-status")
    bridge, _window, ctx = _wire(monkeypatch, services)
    result = bridge.handlers["models.status"]({}, ctx)
    assert result == {"state": "unknown", "short": "模型待验证", "account_id": "account-status"}

