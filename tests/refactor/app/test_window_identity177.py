"""The classroom bridge exposes account identity separately from its label."""
from types import SimpleNamespace

from refactor.learning.app.window import LearningWindow
from refactor.learning.app.settings import AppSettings, DEFAULTS
from refactor.learning.preferences.storage import AccountJSON


def test_window_account_info177_keeps_stable_ids_for_identical_display_names():
    def info(identifier):
        window = SimpleNamespace(_account_id=identifier, _display_name="同名学习者",
                                 _role=None, _api_label="offline", _settings_bound=False,
                                 _session_offline=False)
        return LearningWindow.account_info(window)

    first, second = info("account-a-177"), info("account-b-177")
    assert first["name"] == second["name"] == "同名学习者"
    assert first["account_id"] == "account-a-177"
    assert second["account_id"] == "account-b-177"


def test_old_account_settings177_keep_preferences_and_default_to_private_notification(tmp_path):
    old = {key: value for key, value in DEFAULTS.items() if key != "show_plan_name_in_notification"}
    old.update(theme="aurora", reminder_enabled=True, reminder_time="18:15")
    AccountJSON("legacy-177", tmp_path, "app-settings.json").write(old)
    loaded = AppSettings("legacy-177", tmp_path).load()
    assert loaded["theme"] == "aurora"
    assert loaded["reminder_enabled"] is True
    assert loaded["reminder_time"] == "18:15"
    assert loaded["show_plan_name_in_notification"] is False
