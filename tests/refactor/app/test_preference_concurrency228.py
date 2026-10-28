"""Regression tests for concurrent account preference saves (BUG-187)."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from refactor.learning.app import handlers
from refactor.learning.app.services import AppServices
from refactor.learning.preferences.storage import StorageError

from .test_app_window_layer import _Window, make_bridge, pump, qapp, tmp_root  # noqa: F401


def _services(account_id, root):
    return AppServices(account_id=account_id, data_root=root, session_provider=lambda: None,
                       api_base_url="https://api.invalid", ca_bundle=root / "ca.pem")


def _setup(qapp, root, account_id):
    services = _services(account_id, root)
    bridge, sent = make_bridge(qapp)
    window = _Window(services)
    handlers.register(bridge, window)
    return services, window, bridge, sent


def _send(bridge, request_id, changes):
    bridge.call(json.dumps({"id": request_id, "method": "settings.save", "params": {"changes": changes}}))


def test_real_handlers_concurrent_partial_saves_preserve_both_fields(qapp, tmp_root):
    """Force both bridge workers to capture the same old snapshot before either writes."""
    services, _, bridge, sent = _setup(qapp, tmp_root, "mfa1_" + "a" * 64)
    assert services.settings.load()["font_scale"] == 1.0
    both_read = threading.Barrier(2)
    read = services.settings._file.read

    def synchronized_read(default):
        snapshot = read(default)
        try:
            both_read.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            # A serialized implementation prevents the second read from
            # reaching this test rendezvous; time out and let it continue.
            pass
        return snapshot

    services.settings._file.read = synchronized_read
    try:
        _send(bridge, "pref-a", {"theme": "aurora"})
        _send(bridge, "pref-b", {"font_scale": 1.2})
        responses = [pump(qapp, bridge, sent, lambda m: m.get("id") == rid and m["type"] == "response")[0]
                     for rid in ("pref-a", "pref-b")]

        assert [response["ok"] for response in responses] == [True, True]
        services.settings._file.read = read
        saved = services.settings._file.read({})
        assert saved["theme"] == "aurora"
        assert saved["font_scale"] == 1.2
    finally:
        bridge.close()
        services.close()


def test_separate_settings_objects_share_the_same_path_lock(tmp_path):
    from refactor.learning.app.settings import AppSettings

    first = AppSettings("mfa1_" + "e" * 64, tmp_path)
    second = AppSettings("mfa1_" + "e" * 64, tmp_path)
    assert first._file.lock is second._file.lock
    both_read = threading.Barrier(2)

    def gate(settings):
        read = settings._file.read

        def synchronized_read(default):
            snapshot = read(default)
            try:
                both_read.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            return snapshot
        settings._file.read = synchronized_read

    gate(first)
    gate(second)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(first.save, {"theme": "aurora"}),
                   workers.submit(second.save, {"font_scale": 1.2})]
        values = [future.result(timeout=4) for future in futures]
    # Either worker may acquire the lock first; each result confirms its own
    # patch, while the fresh disk read below must contain both patches.
    assert values[0]["theme"] == "aurora"
    assert values[1]["font_scale"] == 1.2
    saved = first._file.read({})
    assert saved["theme"] == "aurora"
    assert saved["font_scale"] == 1.2


def test_stale_account_save_remains_bound_to_original_account(qapp, tmp_root):
    old, window, bridge, sent = _setup(qapp, tmp_root, "mfa1_" + "b" * 64)
    new = _services("mfa1_" + "c" * 64, tmp_root)
    changed = []
    window.settings_changed = lambda value: changed.append(value)
    read_started, release_read = threading.Event(), threading.Event()
    read = old.settings._file.read

    def paused_read(default):
        snapshot = read(default)
        read_started.set()
        assert release_read.wait(3)
        return snapshot

    old.settings._file.read = paused_read
    try:
        _send(bridge, "old-account-save", {"theme": "aurora"})
        assert read_started.wait(3)
        window.services = new
        release_read.set()
        response = pump(qapp, bridge, sent,
                       lambda m: m.get("id") == "old-account-save" and m["type"] == "response")[0]

        assert response["ok"] is True
        assert old.settings._file.read({})["theme"] == "aurora"
        assert new.settings.load()["theme"] == "sakura"
        assert changed == []  # A late A response cannot update B's window state.
    finally:
        release_read.set()
        bridge.close()
        old.close()
        new.close()


def test_failed_preference_write_returns_error_and_keeps_disk_value(qapp, tmp_root):
    services, _, bridge, sent = _setup(qapp, tmp_root, "mfa1_" + "d" * 64)
    before = services.settings.load()

    def fail_write(_value):
        raise StorageError("模拟写入失败")

    services.settings._file.write = fail_write
    try:
        _send(bridge, "pref-failed", {"font_scale": 1.25})
        response = pump(qapp, bridge, sent,
                        lambda m: m.get("id") == "pref-failed" and m["type"] == "response")[0]

        assert response["ok"] is False
        assert response["error"]["code"] == "SETTINGS_SAVE_FAILED"
        assert services.settings.load() == before
    finally:
        bridge.close()
        services.close()
