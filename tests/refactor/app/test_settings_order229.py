"""Regression coverage for stale, out-of-order settings.save snapshots (BUG-190)."""
import json
import threading
from types import SimpleNamespace

from refactor.learning.app import handlers
from refactor.learning.app.services import AppServices
from refactor.learning.app.settings import AppSettings

from .test_app_window_layer import _Window, make_bridge, pump, qapp, tmp_root  # noqa: F401


def _settings_result(message):
    """Accept the current envelope while keeping failure meaningful on baseline."""
    result = message["result"]
    return result["settings"] if "settings" in result else result


def test_snapshot_revisions_are_account_scoped_metadata_not_persisted(tmp_root):
    account_a = "mfa1_" + "h" * 64
    account_b = "mfa1_" + "i" * 64
    first_a = AppSettings(account_a, tmp_root)
    other_account = AppSettings(account_b, tmp_root)

    a1 = first_a.snapshot()
    b1 = other_account.snapshot()
    a2 = AppSettings(account_a, tmp_root).snapshot()

    assert a1["account_id"] == a2["account_id"] == account_a
    assert b1["account_id"] == account_b
    assert a2["settings_revision"] > a1["settings_revision"]
    assert b1["settings_revision"] > a1["settings_revision"]
    for account_id in (account_a, account_b):
        persisted = AppSettings(account_id, tmp_root)._file.read({})
        assert "settings_revision" not in persisted
        assert "account_id" not in persisted


def test_late_settings_save_response_cannot_replace_newer_visible_snapshot(qapp, tmp_root):
    """A save that finishes last must not make the UI forget a later persisted field."""
    account_id = "mfa1_" + "f" * 64
    services = AppServices(account_id=account_id, data_root=tmp_root, session_provider=lambda: None,
                           api_base_url="https://api.invalid", ca_bundle=tmp_root / "ca.pem")
    bridge, sent = make_bridge(qapp)
    window = _Window(services)
    published = []

    def publish(settings):
        # Match AppWindow.settings_changed's externally visible effects while
        # retaining this focused test's lightweight fake window.
        published.append(dict(settings))
        bridge.push("settings", dict(settings))

    window.settings_changed = publish
    handlers.register(bridge, window)

    persisted_old_snapshot = threading.Event()
    release_old_response = threading.Event()
    save = services.settings.save

    def save_then_pause(changes):
        snapshot = save(changes)
        if changes.get("theme") == "aurora":
            persisted_old_snapshot.set()
            assert release_old_response.wait(4)
        return snapshot

    services.settings.save = save_then_pause
    try:
        bridge.call(json.dumps({"id": "old-theme", "method": "settings.save",
                                "params": {"changes": {"theme": "aurora"}}}))
        assert persisted_old_snapshot.wait(3)

        bridge.call(json.dumps({"id": "new-font", "method": "settings.save",
                                "params": {"changes": {"font_scale": 1.2}}}))
        newer = pump(qapp, bridge, sent,
                     lambda m: m.get("id") == "new-font" and m.get("type") == "response")[0]
        assert newer["ok"] is True
        assert _settings_result(newer)["font_scale"] == 1.2

        release_old_response.set()
        older = pump(qapp, bridge, sent,
                     lambda m: m.get("id") == "old-theme" and m.get("type") == "response")[0]
        assert older["ok"] is True

        # Model the existing Settings.tsx callback: each successful response
        # replaces the whole visible settings object. The late old response
        # currently rolls font_scale back although storage has both changes.
        visible = {}
        for request_id in ("new-font", "old-theme"):
            response = next(m for m in sent if m.get("id") == request_id and m.get("type") == "response")
            visible = _settings_result(response)

        persisted = services.settings.load()
        assert persisted["theme"] == "aurora"
        assert persisted["font_scale"] == 1.2
        assert visible["theme"] == persisted["theme"]
        assert visible["font_scale"] == persisted["font_scale"]

        # New responses carry the per-account monotonic revision so clients can
        # discard out-of-order snapshots without persisting protocol metadata.
        old_result = older["result"]
        new_result = newer["result"]
        assert old_result["account_id"] == account_id == new_result["account_id"]
        assert old_result["settings_revision"] >= new_result["settings_revision"]
    finally:
        release_old_response.set()
        bridge.close()
        services.close()


def test_stale_settings_publication_does_not_replace_fresh_window_snapshot(qapp, tmp_root):
    """Window-side consumers must not receive an older full snapshot last."""
    account_id = "mfa1_" + "g" * 64
    services = AppServices(account_id=account_id, data_root=tmp_root, session_provider=lambda: None,
                           api_base_url="https://api.invalid", ca_bundle=tmp_root / "ca.pem")
    bridge, sent = make_bridge(qapp)
    window = _Window(services)
    published = []

    def publish(settings):
        published.append(dict(settings))
        bridge.push("settings", dict(settings))

    window.settings_changed = publish
    handlers.register(bridge, window)
    old_saved = threading.Event()
    release_old = threading.Event()
    save = services.settings.save

    def save_then_pause(changes):
        snapshot = save(changes)
        if changes.get("theme") == "aurora":
            old_saved.set()
            assert release_old.wait(4)
        return snapshot

    services.settings.save = save_then_pause
    try:
        bridge.call(json.dumps({"id": "pub-old", "method": "settings.save",
                                "params": {"changes": {"theme": "aurora"}}}))
        assert old_saved.wait(3)
        bridge.call(json.dumps({"id": "pub-new", "method": "settings.save",
                                "params": {"changes": {"font_scale": 1.2}}}))
        pump(qapp, bridge, sent,
             lambda m: m.get("id") == "pub-new" and m.get("type") == "response")
        release_old.set()
        pump(qapp, bridge, sent,
             lambda m: m.get("id") == "pub-old" and m.get("type") == "response")

        current = services.settings.load()
        assert published[-1]["theme"] == current["theme"]
        assert published[-1]["font_scale"] == current["font_scale"]
    finally:
        release_old.set()
        bridge.close()
        services.close()


def test_remote_settings_notification_cannot_publish_snapshot_after_local_save(qapp, tmp_root):
    """A sync-triggered read and publish must be ordered with local commits."""
    account_id = "mfa1_" + "j" * 64
    services = AppServices(account_id=account_id, data_root=tmp_root, session_provider=lambda: None,
                           api_base_url="https://api.invalid", ca_bundle=tmp_root / "ca.pem")
    settings = services.settings
    published = []
    window = SimpleNamespace(settings_changed=lambda value: published.append(dict(value)))
    sync_handler = SimpleNamespace(_closed=False, _paused=False, settings=settings, window=window)

    old_read = threading.Event()
    release_old_read = threading.Event()
    local_lock_attempted = threading.Event()
    local_published = threading.Event()
    real_lock = settings._file.lock
    local_acquired_without_waiting = []
    load = settings.load

    def pause_sync_load():
        value = load()
        if threading.current_thread().name == "remote-settings-notification":
            old_read.set()
            assert release_old_read.wait(4)
        return value

    settings.load = pause_sync_load
    from refactor.learning.app.sync import WindowSync

    sync_thread = threading.Thread(target=lambda: WindowSync._settings_changed(sync_handler),
                                   name="remote-settings-notification")

    def local_save_and_publish():
        acquired = real_lock.acquire(blocking=False)
        local_acquired_without_waiting.append(acquired)
        local_lock_attempted.set()
        if not acquired:
            real_lock.acquire()
        try:
            settings.save({"font_scale": 1.2})
            # Keep the local save and its full-snapshot publication together,
            # as the real bridge handler does, without network or Qt events.
            window.settings_changed(settings.snapshot()["settings"])
            local_published.set()
        finally:
            real_lock.release()

    local_thread = threading.Thread(target=local_save_and_publish, name="local-settings-save")
    try:
        sync_thread.start()
        assert old_read.wait(3)
        local_thread.start()
        assert local_lock_attempted.wait(3)

        # In the buggy path, the local writer acquires the real lock and
        # publishes while the older sync snapshot is paused. In the fixed path,
        # the sync notification still owns the lock, so release it first.
        assert local_acquired_without_waiting
        if local_acquired_without_waiting[0]:
            assert local_published.wait(3)
        release_old_read.set()
        sync_thread.join(3)
        local_thread.join(3)
        assert not sync_thread.is_alive() and not local_thread.is_alive()

        current = settings.load()
        assert current["font_scale"] == 1.2
        assert published[-1]["font_scale"] == current["font_scale"]
    finally:
        release_old_read.set()
        sync_thread.join(3)
        local_thread.join(3)
        services.close()
