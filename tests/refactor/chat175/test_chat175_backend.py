"""Bridge and persistence behavior for 175-MF02 chat archive preferences."""
import json
import threading
from types import SimpleNamespace

from refactor.learning.app import handlers
from refactor.learning.app.services import AppServices
from refactor.learning.chat.contracts import utc_now
from refactor.learning.connections.errors import ModelCancelled
from refactor.learning.preferences.storage import AccountJSON, StorageError

from tests.refactor.app.test_app_free_chat import ask, make_bridge, pump
from tests.refactor.app.test_app_window_layer import _Window, qapp, tmp_root  # noqa: F401


ACCOUNT = "mfa1_" + "d" * 64


class _Gateway:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False

    def complete(self, purpose, messages, *, system=None, enable_search=False, on_text=None, cancel=None, plan_id=""):
        if self.block:
            self.entered.set()
            while not self.release.wait(0.01):
                if cancel and cancel.is_set():
                    raise ModelCancelled()
        on_text("答复")
        return SimpleNamespace(text="答复")

    def close(self):
        pass


def _setup(qapp, tmp_root, account_id=ACCOUNT):
    services = AppServices(account_id=account_id, data_root=tmp_root, session_provider=lambda: None,
                           api_base_url="https://api.invalid", ca_bundle=tmp_root / "ca.pem")
    services._gateway = _Gateway()
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, _Window(services))
    return services, bridge, sent


def _call(bridge, sent, qapp, rid, method, params=None):
    bridge.call(json.dumps({"id": rid, "method": method, "params": params or {}}))
    return pump(qapp, bridge, sent, lambda msg: msg.get("id") == rid and msg["type"] == "response")[0]


def test_archive_filters_restore_send_and_delete(qapp, tmp_root):
    services, bridge, sent = _setup(qapp, tmp_root)
    try:
        first = _call(bridge, sent, qapp, "new", "chat.create")["result"]["session"]
        sid = first["id"]
        assert first["archived"] is False
        assert _call(bridge, sent, qapp, "archive", "chat.archive", {"session_id": sid, "archived": True})[
            "result"]["session"]["archived"] is True
        assert [s["id"] for s in _call(bridge, sent, qapp, "active", "chat.list")["result"]["sessions"]] == []
        archived = _call(bridge, sent, qapp, "archived-list", "chat.list", {"archived": True})["result"]["sessions"]
        assert len(archived) == 1 and archived[0]["id"] == sid and archived[0]["archived"] is True

        sent_turn = _call(bridge, sent, qapp, "send", "chat.send", {"session_id": sid, "text": "继续聊"})
        assert sent_turn["ok"] and sent_turn["result"]["session"]["archived"] is False
        assert len(sent_turn["result"]["session"]["messages"]) == 2
        assert [s["id"] for s in _call(bridge, sent, qapp, "active2", "chat.list")["result"]["sessions"]] == [sid]
        assert _call(bridge, sent, qapp, "archive-again", "chat.archive",
                     {"session_id": sid, "archived": True})["ok"]
        assert _call(bridge, sent, qapp, "delete", "chat.delete", {"session_id": sid})["result"]["deleted"]
        assert _call(bridge, sent, qapp, "archived-empty", "chat.list", {"archived": True})["result"]["sessions"] == []
    finally:
        bridge.close()
        services.close()


def test_remote_archive_during_generation_does_not_conflict_or_restore(tmp_root):
    from refactor.learning.app.chat_store import CourseChatStore
    from refactor.learning.course.store import CourseStore

    with CourseStore(ACCOUNT, tmp_root) as db:
        chat = CourseChatStore(db)
        session = chat.create(session_id="concurrent", created_at=utc_now())
        before = session.version
        db.set_chat_session_archived(session.session_id, True)
        after_archive = chat.get(session.session_id)
        assert after_archive.archived is True and after_archive.version == before
        turn = chat.commit_turn(session.session_id, expected_version=before, user_content="问题", assistant_content="回答",
                                committed_at=utc_now())
        assert turn.archived is True
        assert [m.content for m in turn.messages] == ["问题", "回答"]


def test_bridge_archive_during_generation_keeps_reply_and_archive(qapp, tmp_root):
    services, bridge, sent = _setup(qapp, tmp_root)
    try:
        sid = _call(bridge, sent, qapp, "new", "chat.create")["result"]["session"]["id"]
        services.gateway.block = True
        bridge.call(json.dumps({"id": "pending-send", "method": "chat.send",
                                "params": {"session_id": sid, "text": "并发问题"}}))
        assert services.gateway.entered.wait(3)
        archived = _call(bridge, sent, qapp, "archive-mid-turn", "chat.archive",
                         {"session_id": sid, "archived": True})
        assert archived["ok"] and archived["result"]["session"]["archived"] is True
        services.gateway.release.set()
        completed = pump(qapp, bridge, sent,
                         lambda msg: msg.get("id") == "pending-send" and msg["type"] == "response")[0]
        assert completed["ok"]
        stored = _call(bridge, sent, qapp, "get-after-turn", "chat.get", {"session_id": sid})[
            "result"]["session"]
        assert stored["archived"] is True
        assert [message["content"] for message in stored["messages"]] == ["并发问题", "答复"]
    finally:
        services.gateway.release.set()
        bridge.close()
        services.close()


def test_ui_list_preference_is_account_scoped_and_storage_failure_is_nonfatal(qapp, tmp_root, monkeypatch):
    other_account = "mfa1_" + "e" * 64
    one, bridge_one, sent_one = _setup(qapp, tmp_root, ACCOUNT)
    two, bridge_two, sent_two = _setup(qapp, tmp_root, other_account)
    try:
        assert _call(bridge_one, sent_one, qapp, "get-one", "chat.ui_get")["result"]["list_collapsed"] is False
        saved = _call(bridge_one, sent_one, qapp, "set-one", "chat.ui_set", {"list_collapsed": True})["result"]
        assert saved == {"list_collapsed": True, "saved": True}
        assert _call(bridge_one, sent_one, qapp, "get-one-again", "chat.ui_get")["result"]["list_collapsed"] is True
        assert _call(bridge_two, sent_two, qapp, "get-two", "chat.ui_get")["result"]["list_collapsed"] is False

        original_read = AccountJSON.read
        def read_failure(self, default):
            if self.path.name == "chat-ui.json":
                raise StorageError()
            return original_read(self, default)
        monkeypatch.setattr(AccountJSON, "read", read_failure)
        unavailable = _call(bridge_one, sent_one, qapp, "read-failed", "chat.ui_get")["result"]
        assert unavailable == {"list_collapsed": False, "available": False}

        original_write = AccountJSON.write
        def write_failure(self, value):
            if self.path.name == "chat-ui.json":
                raise StorageError()
            return original_write(self, value)
        monkeypatch.setattr(AccountJSON, "write", write_failure)
        failed_save = _call(bridge_one, sent_one, qapp, "write-failed", "chat.ui_set",
                            {"list_collapsed": True})["result"]
        assert failed_save == {"list_collapsed": True, "saved": False}
    finally:
        bridge_one.close(); one.close()
        bridge_two.close(); two.close()
