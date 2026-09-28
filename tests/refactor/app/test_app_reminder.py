"""Offline contract tests for the account-window learning reminder."""
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import threading

import pytest
from PySide6.QtWidgets import QApplication

from refactor.learning.app.reminder import ReminderService
from refactor.learning.app.settings import DEFAULTS


EAST = timezone(timedelta(hours=8))


class FakeTime:
    def __init__(self, value):
        self.value = datetime.fromisoformat(value)

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = datetime.fromisoformat(value)


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def list_plans(self):
        return [row["plan"] for row in self.rows.values()]

    def today_status(self, plan_id, **_kwargs):
        return dict(self.rows[plan_id]["status"])

    def has_study_activity(self, plan_id, **_kwargs):
        return False


class FakeNotifier:
    def __init__(self):
        self.calls = []
        self.closed = False

    def __call__(self, title, message, clicked):
        self.calls.append((title, message, clicked))
        return True

    def close(self):
        self.closed = True


class FakeBridge:
    def __init__(self):
        self.pushed = []

    def push(self, name, data):
        self.pushed.append((name, data))


class FakeWindow:
    def __init__(self):
        self.bridge = FakeBridge()
        self.actions = []

    def showNormal(self):
        self.actions.append("normal")

    def raise_(self):
        self.actions.append("raise")

    def activateWindow(self):
        self.actions.append("activate")


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def row(plan_id, label, *, target=60, confirmed=0, tentative=0, state="active"):
    return {"plan": {"id": plan_id, "goal": label, "state": state, "window": {"start": "2026-09-01"}},
            "status": {"today_target": target, "today_confirmed": confirmed, "today_tentative": tentative}}


@pytest.fixture
def setup(qt_app, tmp_path):
    made = []

    def create(rows, *, at="2026-09-27T19:59:55+08:00", reminder="20:00", enabled=True, window=None):
        source = FakeTime(at)
        store = FakeStore(rows)
        notifier = FakeNotifier()
        settings = dict(DEFAULTS, refresh_time="04:00", reminder_enabled=enabled, reminder_time=reminder)
        service = ReminderService("account-a", Path(tmp_path), lambda: store, settings=settings,
                                  now=source, timezone=EAST, notifier=notifier, window=window)
        made.append((service, source, store, notifier))
        return service, source, store, notifier

    yield create
    for service, *_ in made:
        service.dispose()
        service.deleteLater()
    qt_app.processEvents()


def tick(service, source, value):
    source.set(value)
    service.clock.check_now()


def test_due_notification_combines_plans_and_is_once_per_day(setup):
    service, source, _, notifier = setup({"p1": row("p1", "物理基础"), "p2": row("p2", "英语阅读")})
    tick(service, source, "2026-09-27T20:00:00+08:00")
    tick(service, source, "2026-09-27T20:10:00+08:00")
    assert len(notifier.calls) == 1
    assert "物理基础" in notifier.calls[0][1] and "英语阅读" in notifier.calls[0][1]
    assert "尚未开始" in notifier.calls[0][1]


def test_disabled_setting_keeps_clock_silent(setup):
    service, source, _, notifier = setup({"p1": row("p1", "计划")}, enabled=False)
    tick(service, source, "2026-09-27T20:00:00+08:00")
    assert service.clock.reminder is None and not notifier.calls


@pytest.mark.parametrize("status", [
    {"today_target": 60, "today_confirmed": 20, "today_tentative": 0},
    {"today_target": 60, "today_confirmed": 0, "today_tentative": 20},
    {"today_target": 0, "today_confirmed": 0, "today_tentative": 0},
])
def test_started_or_rest_day_does_not_notify(setup, status):
    value = row("p1", "今天的计划")
    value["status"] = status
    service, source, _, notifier = setup({"p1": value})
    tick(service, source, "2026-09-27T20:00:00+08:00")
    assert not notifier.calls


def test_no_active_plan_and_non_running_states_do_not_notify(setup):
    service, source, store, notifier = setup({
        "ready": row("ready", "未开始", state="ready"),
        "done": row("done", "已完成", state="completed"),
        "empty": row("empty", "无窗口", state="active"),
    })
    store.rows["empty"]["plan"]["window"] = None
    tick(service, source, "2026-09-27T20:00:00+08:00")
    assert not notifier.calls


def test_class_started_holds_due_and_class_ended_delivers(setup):
    service, source, _, notifier = setup({"p1": row("p1", "课堂计划")})
    service.class_started()
    tick(service, source, "2026-09-27T20:00:00+08:00")
    assert not notifier.calls
    service.class_ended()
    assert len(notifier.calls) == 1


def test_dispose_is_closed_app_semantics_and_closes_notifier(setup):
    service, source, _, notifier = setup({"p1": row("p1", "计划")})
    service.dispose()
    tick(service, source, "2026-09-27T20:00:00+08:00")
    assert not notifier.calls and notifier.closed
    service.dispose()


def test_settings_change_applies_reminder_and_refresh_immediately(setup):
    service, source, _, notifier = setup({"p1": row("p1", "计划")}, at="2026-09-27T19:00:00+08:00")
    service.settings_changed(dict(DEFAULTS, refresh_time="02:00", reminder_enabled=True, reminder_time="19:30"))
    assert service.clock.refresh == time(2) and service.clock.current_study_day.isoformat() == "2026-09-27"
    tick(service, source, "2026-09-27T19:30:00+08:00")
    assert len(notifier.calls) == 1
    service.settings_changed(dict(DEFAULTS, refresh_time="02:00", reminder_enabled=False, reminder_time="19:30"))
    tick(service, source, "2026-09-28T19:30:00+08:00")
    assert len(notifier.calls) == 1


def test_notification_click_restores_window_and_opens_home(setup, qt_app):
    window = FakeWindow()
    service, source, _, notifier = setup({"p1": row("p1", "计划")}, window=window)
    tick(service, source, "2026-09-27T20:00:00+08:00")
    notifier.calls[0][2]()
    qt_app.processEvents()
    assert window.actions == ["normal", "raise", "activate"]
    assert window.bridge.pushed == [("navigate", {"route": "home"})]


def test_service_reads_the_shared_store_provider_on_each_day(setup):
    service, source, store, notifier = setup({"p1": row("p1", "首日计划")})
    tick(service, source, "2026-09-27T20:00:00+08:00")
    store.rows["p1"]["status"]["today_confirmed"] = 60
    store.rows["p2"] = row("p2", "第二日计划")
    tick(service, source, "2026-09-28T19:59:55+08:00")
    tick(service, source, "2026-09-28T20:00:00+08:00")
    assert len(notifier.calls) == 2 and "第二日计划" in notifier.calls[-1][1]


def test_worker_thread_configuration_is_queued_to_qt_thread(setup, qt_app):
    service, source, _, _ = setup({"p1": row("p1", "计划")})
    changed = dict(DEFAULTS, reminder_enabled=True, reminder_time="21:00")
    worker = threading.Thread(target=lambda: (service.settings_changed(changed), service.class_started()))
    worker.start()
    worker.join()
    assert not service.in_class
    qt_app.processEvents()
    assert service.in_class and service.clock.reminder == time(21)
    worker = threading.Thread(target=service.class_ended)
    worker.start()
    worker.join()
    qt_app.processEvents()
    assert not service.in_class
