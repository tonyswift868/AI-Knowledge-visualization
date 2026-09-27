"""Switching the theme from the page must not touch widgets on a bridge worker thread (crash)."""
import json
import threading

from refactor.learning.app.window import LearningWindow, THEME_BACKGROUNDS

from .test_app_window_layer import pump, qapp, tmp_root  # noqa: F401


def test_theme_switch_applies_on_gui_thread(qapp, tmp_root, monkeypatch):
    threads = []
    original = LearningWindow._apply_theme

    def record(self, theme):
        threads.append(threading.current_thread() is threading.main_thread())
        original(self, theme)
    monkeypatch.setattr(LearningWindow, "_apply_theme", record)
    win = LearningWindow("测试", "LEARNER", "api", account_id="mfa1_" + "f" * 64, data_root=tmp_root)
    assert win.bind_feature_context(session_provider=lambda: None, api_base_url="https://api.invalid",
                                    ca_bundle=tmp_root / "ca.pem")
    sent = []
    win.bridge.event.connect(lambda text: sent.append(json.loads(text)))
    threads.clear()
    win.bridge.call(json.dumps({"id": "t1", "method": "settings.save", "params": {"changes": {"theme": "aurora"}}}))
    assert pump(qapp, win.bridge, sent, lambda m: m.get("id") == "t1")[0]["ok"]
    pump(qapp, win.bridge, sent, lambda m: bool(threads))
    assert threads == [True]                                   # applied once, on the GUI thread
    assert win.page.backgroundColor().name() == THEME_BACKGROUNDS["aurora"]
    win.dispose()
