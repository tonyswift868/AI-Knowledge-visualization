"""BUG-215: a rejected native session must fence the real classroom path."""
from datetime import date, timedelta
from threading import Event
import json

import httpx
import pytest

from tests.refactor.integration38.support import Agents, seed_bank
from .test_app_free_chat import ask
from .test_app_sync_lifecycle import NOW, spin, window  # noqa: F401
from .test_app_window_layer import pump
from refactor.learning.course.ledger import PlanWindow

pytest_plugins = (
    "tests.refactor.app.test_app_window_layer",
    "tests.refactor.sync_course.test_sync_course",
)

PLAN = "320" + "a" * 29
TODAY = date(2026, 10, 1)


class ControlledAgents(Agents):
    def __init__(self):
        super().__init__()
        self.teacher_entered = Event()
        self.teacher_release = None

    def complete(self, purpose, messages, **kwargs):
        if purpose == "teacher":
            self.teacher_entered.set()
            if self.teacher_release is not None:
                # Deliberately ignore cancellation and return late. The session
                # epoch, not model cooperation, must prevent a stale save.
                assert self.teacher_release.wait(8)
        return super().complete(purpose, messages, **kwargs)

    def close(self):
        pass


@pytest.fixture
def classroom_window(window, qapp):
    win, _, _, _, _, _, _, sent = window
    store = win.services.store
    store.create_plan(PLAN, goal="学习队列", final_goal="理解队列", level="beginner",
                      window=PlanWindow(TODAY, TODAY + timedelta(days=1), 120))
    store.add_sources(PLAN, [dict(id="s", url="https://example.invalid/source", adopted=True,
        chunks=[dict(chunk_id="k1", text="队列是先进先出的结构。")])])
    store.save_outline(PLAN, [dict(id="st", title="基础")],
                       [dict(id="m0", stage_id="st", title="队列")])
    store.save_generated_module(PLAN, "m0", [dict(id="p0", title="队列定义", summary="先进先出",
        minutes=10, chunk_ids=["k1"])], check_minutes=5, today=TODAY)
    seed_bank(store, PLAN, count=1)
    store.start_plan(PLAN, today=TODAY)
    agents = ControlledAgents()
    win.services._gateway = agents
    entered = ask(qapp, win.bridge, sent, "gate-enter", "classroom.enter", {"plan_id": PLAN})
    assert entered["ok"], entered
    return win, agents, store, sent


def _snapshot(store):
    return (store.records("messages", PLAN), store.classroom_position(PLAN),
            store.pending_records(1000))


def _reject_session(window, qapp, code):
    win, _, _, _, _, mode, _, _ = window
    if code == "REAUTH_REQUIRED":
        win._sync._status_changed(dict(state="reauth", message="登录已失效"))
        return win
    else:
        mode["session"] = code
        win._sync.guard._launch()
    spin(qapp, win, lambda: win.sync_status()["state"] == "superseded"
         and win._sync.guard._future is None)
    return win


@pytest.mark.parametrize(("session_code", "error_code"), [
    ("NATIVE_SESSION_SUPERSEDED", "NATIVE_SESSION_SUPERSEDED"),
    ("REAUTH_REQUIRED", "REAUTH_REQUIRED"),
])
def test_rejected_session_fences_classroom_but_verify_and_recovery_remain_available(
        classroom_window, window, qapp, session_code, error_code):
    win, agents, store, sent = classroom_window
    before = _snapshot(store)
    calls = len(agents.calls)
    _reject_session(window, qapp, session_code)

    rejected = ask(qapp, win.bridge, sent, "gate-reject", "classroom.next", {"plan_id": PLAN})
    assert not rejected["ok"] and rejected["error"]["code"] == error_code
    assert len(agents.calls) == calls
    assert _snapshot(store) == before

    verify_requested = []
    win.verify_requested.connect(lambda: verify_requested.append(True))
    verify = ask(qapp, win.bridge, sent, "gate-verify", "app.account", {"action": "verify"})
    assert verify["ok"] and verify_requested == [True]

    mode = window[5]
    login = window[3]
    mode["session"] = "active"
    login.access_token = "recovered-isolated-token"
    win.clear_session_notice()
    win.session_recovered()
    win.bridge.require_session()
    resumed = ask(qapp, win.bridge, sent, "gate-resume", "classroom.next", {"plan_id": PLAN})
    assert resumed["ok"], resumed
    assert agents.teacher_entered.is_set()
    assert len(agents.calls) > calls


def test_late_teacher_return_after_superseded_cannot_write_classroom_or_outbox(classroom_window, window, qapp):
    win, agents, store, sent = classroom_window
    store.create_chat_session("已有待同步", session_id="gate-pending", created_at=NOW)
    pending = store.pending_records(1000)
    agents.teacher_release = Event()
    agents.teacher_entered.clear()
    rid = "gate-inflight"
    win.bridge.call(json.dumps({"id": rid, "method": "classroom.next",
                                "params": {"plan_id": PLAN}}))
    assert agents.teacher_entered.wait(5), "classroom request did not reach the fake teacher"
    at_cancellation = _snapshot(store)
    prior_chat = [row for row in pending if row.entity in {"chat_sessions", "chat_messages"}]

    _reject_session(window, qapp, "NATIVE_SESSION_SUPERSEDED")
    after_gate = store.pending_records(1000)
    assert all(row in after_gate for row in prior_chat)
    assert _snapshot(store) == at_cancellation

    mode = window[5]
    login = window[3]
    mode["session"] = "active"
    login.access_token = "recovered-before-late-teacher"
    win.clear_session_notice()
    win.session_recovered()
    win.bridge.require_session()
    after_recovery = _snapshot(store)[:2]

    agents.teacher_release.set()
    result = pump(qapp, win.bridge, sent,
                  lambda item: item.get("id") == rid and item["type"] == "response", timeout=8)[0]
    assert not result["ok"]
    assert _snapshot(store)[:2] == after_recovery


def test_ordinary_offline_status_keeps_real_classroom_available(classroom_window, window, qapp):
    win, agents, store, sent = classroom_window
    server = window[1]
    server.before = lambda request: httpx.Response(503)
    win.sync_now()
    spin(qapp, win, lambda: win.sync_status()["state"] == "retrying"
         and win._sync.service._future is None)
    before = len(agents.calls)
    response = ask(qapp, win.bridge, sent, "gate-offline", "classroom.next", {"plan_id": PLAN})
    assert response["ok"], response
    assert len(agents.calls) > before
    assert store.classroom_position(PLAN) is not None


def test_normal_token_refresh_keeps_inflight_classroom_response(classroom_window, window, qapp):
    win, agents, store, sent = classroom_window
    agents.teacher_release = Event()
    agents.teacher_entered.clear()
    before = store.records("messages", PLAN)
    rid = "gate-normal-refresh"
    win.bridge.call(json.dumps({"id": rid, "method": "classroom.next",
                               "params": {"plan_id": PLAN}}))
    try:
        assert agents.teacher_entered.wait(5)
        window[3].access_token = "ordinary-refreshed-isolated-token"
        win.session_recovered()
    finally:
        agents.teacher_release.set()
    result = pump(qapp, win.bridge, sent,
                  lambda item: item.get("id") == rid and item["type"] == "response", timeout=8)[0]
    assert result["ok"], result
    assert store.records("messages", PLAN) != before

