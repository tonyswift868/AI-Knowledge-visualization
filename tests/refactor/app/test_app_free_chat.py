"""Free chat in the new window (UI-02): no system prompt but the user's persona."""
import json
import threading
from types import SimpleNamespace

from refactor.learning.app import handlers
from refactor.learning.app.services import AppServices
from refactor.learning.connections.errors import ModelCancelled
from refactor.learning.tools.free_chat.contracts import utc_now

from .test_app_window_layer import _Window, make_bridge, pump, qapp, tmp_root  # noqa: F401

ACCOUNT = "mfa1_" + "d" * 64


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.block = None

    def complete(self, purpose, messages, *, system=None, enable_search=False, on_text=None, cancel=None, plan_id=""):
        self.calls.append({"purpose": purpose, "messages": messages, "system": system, "search": enable_search})
        if self.block:
            self.block.set()
            cancel.wait(3)
            raise ModelCancelled()
        on_text("你")
        on_text("好")
        return SimpleNamespace(text="你好")

    def close(self):
        pass


def setup(qapp, tmp_root):
    services = AppServices(account_id=ACCOUNT, data_root=tmp_root, session_provider=lambda: None,
                           api_base_url="https://api.invalid", ca_bundle=tmp_root / "ca.pem")
    services._gateway = FakeGateway()
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, _Window(services))
    return services, bridge, sent


def ask(qapp, bridge, sent, rid, method, params=None):
    bridge.call(json.dumps({"id": rid, "method": method, "params": params or {}}))
    return pump(qapp, bridge, sent, lambda m: m.get("id") == rid and m["type"] == "response")[0]


def test_chat_sends_only_user_persona_and_keeps_history(qapp, tmp_root):
    services, bridge, sent = setup(qapp, tmp_root)
    sid = ask(qapp, bridge, sent, "c1", "chat.create")["result"]["session"]["id"]
    first = ask(qapp, bridge, sent, "c2", "chat.send", {"session_id": sid, "text": "  在吗  "})
    assert first["ok"] and [m["delta"] for m in sent if m["type"] == "stream" and m["id"] == "c2"] == ["你", "好"]
    call = services.gateway.calls[0]
    assert call == {"purpose": "free_chat", "messages": [{"role": "user", "content": "在吗"}], "system": None, "search": False}

    assert ask(qapp, bridge, sent, "c3", "settings.save", {"changes": {"chat_persona": "你叫小樱。"}})["ok"]
    ask(qapp, bridge, sent, "c4", "chat.send", {"session_id": sid, "text": "讲个笑话"})
    call = services.gateway.calls[1]
    assert call["system"] == "你叫小樱。"
    assert [m["content"] for m in call["messages"]] == ["在吗", "你好", "讲个笑话"]

    listed = ask(qapp, bridge, sent, "c5", "chat.list")["result"]["sessions"]
    assert listed == [dict(listed[0], id=sid, title="在吗", count=4)]
    got = ask(qapp, bridge, sent, "c6", "chat.get", {"session_id": sid})["result"]["session"]
    assert [m["role"] for m in got["messages"]] == ["user", "assistant", "user", "assistant"]
    assert ask(qapp, bridge, sent, "c7", "chat.delete", {"session_id": sid})["result"]["deleted"] is True
    assert ask(qapp, bridge, sent, "c8", "chat.get", {"session_id": sid})["error"]["code"] == "CHAT_NOT_FOUND"
    bridge.close()
    services.close()


def test_stopped_turn_is_not_saved_and_bad_input_rejected(qapp, tmp_root):
    services, bridge, sent = setup(qapp, tmp_root)
    sid = ask(qapp, bridge, sent, "s1", "chat.create")["result"]["session"]["id"]
    services.gateway.block = threading.Event()
    bridge.call(json.dumps({"id": "s2", "method": "chat.send", "params": {"session_id": sid, "text": "长回答"}}))
    assert services.gateway.block.wait(3)
    bridge.call(json.dumps({"id": "s2c", "method": "cancel", "params": {"target": "s2"}}))
    stopped = pump(qapp, bridge, sent, lambda m: m.get("id") == "s2")[0]
    assert stopped["error"]["code"] == "CANCELLED"
    assert ask(qapp, bridge, sent, "s3", "chat.get", {"session_id": sid})["result"]["session"]["messages"] == []
    assert ask(qapp, bridge, sent, "s4", "chat.send", {"session_id": sid, "text": "   "})["error"]["code"] == "PARAM_INVALID"
    assert ask(qapp, bridge, sent, "s5", "chat.get", {"session_id": "../x"})["error"]["code"] in ("PARAM_INVALID", "CHAT_NOT_FOUND")
    bridge.close()
    services.close()


def test_old_tools_page_sessions_are_visible(qapp, tmp_root):
    from refactor.learning.tools.service import ScopedChatStore
    from refactor.learning.tools.storage import ToolStore
    old = ScopedChatStore(ToolStore(tmp_root, ACCOUNT).root / "chat")
    session = old.create(session_id=None, created_at=utc_now())
    old.commit_turn(session.session_id, expected_version=0, user_content="旧界面聊过", assistant_content="嗯",
                    committed_at=utc_now())
    path = old.root / 'sessions' / (session.session_id + '.json')
    original = path.read_bytes()
    services, bridge, sent = setup(qapp, tmp_root)
    listed = ask(qapp, bridge, sent, "o1", "chat.list")["result"]["sessions"]
    assert [s["title"] for s in listed] == ["旧界面聊过"]
    assert services.store.records('chat_sessions')[0]['title'] == '旧界面聊过'
    assert [m['role'] for m in services.store.chat_messages(listed[0]['id'])] == ['user', 'assistant']
    assert all(r.plan_key == '' for r in services.store.pending_records(100))
    assert services.store.legacy_chat_imported() and path.read_bytes() == original
    bridge.close()
    services.close()
