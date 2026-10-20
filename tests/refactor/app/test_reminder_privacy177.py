"""Account-scoped privacy defaults for learning reminder notifications."""
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtWidgets import QApplication

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.reminder import ReminderService
from refactor.learning.app.settings import AppSettings, DEFAULTS, validate


TZ = timezone(timedelta(hours=8))


class FakeStore:
    def __init__(self, label):
        self.plan = {"id": "plan-1", "goal": label, "state": "active", "window": {"start": "2026-09-01"}}

    def list_plans(self):
        return [self.plan]

    def today_status(self, *_args, **_kwargs):
        return {"today_target": 45, "today_confirmed": 0, "today_tentative": 0}

    def has_study_activity(self, *_args, **_kwargs):
        return False


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def __call__(self, title, message, _clicked):
        self.calls.append((title, message))
        return True

    def close(self):
        pass


class FakeTime:
    def __init__(self):
        self.value = datetime.fromisoformat("2026-10-20T19:59:55+08:00")

    def __call__(self):
        return self.value


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def notify(account_id, label, root, source):
    source.value = datetime.fromisoformat("2026-10-20T19:59:55+08:00")
    settings = AppSettings(account_id, root)
    settings.save({"reminder_enabled": True})
    notifier = FakeNotifier()
    service = ReminderService(account_id, root, lambda: FakeStore(label), notifier=notifier,
                              now=source, timezone=TZ)
    try:
        source.value = datetime.fromisoformat("2026-10-20T20:00:00+08:00")
        service.clock.check_now()
        return settings, notifier.calls
    finally:
        service.dispose()
        service.deleteLater()


def test_notification_defaults_to_generic_copy_and_opt_in_restores_plan_name(tmp_path, qt_app):
    source = FakeTime()
    account_id = "reminder-privacy-177"
    settings, generic_calls = notify(account_id, "物理基础计划", tmp_path, source)
    assert settings.load()["show_plan_name_in_notification"] is False
    assert generic_calls == [("MentorFlow", "该学习了")]

    settings.save({"show_plan_name_in_notification": True})
    _, detailed_calls = notify(account_id, "物理基础计划", tmp_path, source)
    assert detailed_calls == [("该开始今天的学习了", "今天有学习任务尚未开始：物理基础计划。打开 MentorFlow 开始学习。")]


def test_notification_name_preference_is_account_scoped(tmp_path, qt_app):
    source = FakeTime()
    account_a = AppSettings("reminder-a-177", tmp_path)
    account_b = AppSettings("reminder-b-177", tmp_path)
    account_a.save({"show_plan_name_in_notification": True})

    _, detailed_calls = notify("reminder-a-177", "共同显示名", tmp_path, source)
    _, generic_calls = notify("reminder-b-177", "共同显示名", tmp_path, source)
    assert "共同显示名" in detailed_calls[0][1]
    assert generic_calls == [("MentorFlow", "该学习了")]
    assert account_a.load()["show_plan_name_in_notification"] is True
    assert account_b.load()["show_plan_name_in_notification"] is False


def test_notification_name_preference_requires_boolean():
    with pytest.raises(BridgeError) as exc:
        validate({**DEFAULTS, "show_plan_name_in_notification": "true"})
    assert exc.value.code == "SETTINGS_INVALID"
