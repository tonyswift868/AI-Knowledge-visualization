"""Regression for startup exception fallback before the GUI consumes its cache."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from refactor.desktop.updates import DesktopUpdates


class CacheThenCleanupFailure219:
    def __init__(self, state='HEALTHY', allowed=True, *, cache_fails=False):
        self.state, self.allowed, self.cache_fails = state, allowed, cache_fails

    def attach_shutdown_guard(self, _app):
        pass

    def get_startup_update_state(self, _root, *, full, check_feed, cancel,
                                 on_pointer_policy=None):
        if not check_feed:
            if self.cache_fails:
                raise RuntimeError('simulated verified-cache read failure')
            return {'state': self.state, 'login_allowed': self.allowed, 'feed': 'NOT_CHECKED'}
        raise RuntimeError('simulated core cleanup failure')


def run_startup219(tmp_path, monkeypatch, core):
    from refactor.desktop.delivery import per_user_install

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(per_user_install, 'maintenance_log', lambda *_a, **_k: None)
    monkeypatch.setattr(DesktopUpdates, '_prepare_in_background', lambda _self: None)
    worker_done = threading.Event()
    original_submit = ThreadPoolExecutor.submit

    def submit_with_completion(executor, fn, *args, **kwargs):
        def wrapped():
            try:
                return fn(*args, **kwargs)
            finally:
                worker_done.set()
        return original_submit(executor, wrapped)

    monkeypatch.setattr(ThreadPoolExecutor, 'submit', submit_with_completion)
    gate = QWidget()
    ui = DesktopUpdates(app, tmp_path, gate, core=core)
    ui.run('startup')
    assert worker_done.wait(3), 'worker did not finish injected failure path'
    assert ui._startup_cached is None
    deadline = time.monotonic() + 3
    while ui.snapshot().get('state') == 'CHECKING' and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    app.processEvents()
    return app, gate, ui


def test_startup_exception_uses_verified_local_cache_before_queued_gui_delivery219(
        tmp_path, monkeypatch):
    from refactor.desktop.delivery import per_user_install

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(per_user_install, 'maintenance_log', lambda *_a, **_k: None)
    monkeypatch.setattr(DesktopUpdates, '_prepare_in_background', lambda _self: None)
    worker_done = threading.Event()
    original_submit = ThreadPoolExecutor.submit

    def submit_with_completion(executor, fn, *args, **kwargs):
        def wrapped():
            try:
                return fn(*args, **kwargs)
            finally:
                worker_done.set()
        return original_submit(executor, wrapped)

    monkeypatch.setattr(ThreadPoolExecutor, 'submit', submit_with_completion)
    gate = QWidget()
    ui = DesktopUpdates(app, tmp_path, gate, core=CacheThenCleanupFailure219())
    try:
        ui.run('startup')
        assert worker_done.wait(3), 'worker did not finish injected failure path'
        # Both signals are queued; GUI has consumed neither when the worker exits.
        assert ui._startup_cached is None
        deadline = time.monotonic() + 3
        while ui.snapshot().get('state') == 'CHECKING' and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        app.processEvents()
        assert ui.snapshot()['state'] == 'HEALTHY'
        assert ui.snapshot()['login_allowed'] is True
    finally:
        ui.close()
        gate.close()


@pytest.mark.parametrize('state', ['MANDATORY_UPDATE', 'REPAIR_REQUIRED'])
def test_startup_exception_preserves_restrictive_cached_policy219(
        tmp_path, monkeypatch, state):
    app, gate, ui = run_startup219(
        tmp_path, monkeypatch,
        CacheThenCleanupFailure219(state, False))
    try:
        assert ui.snapshot()['state'] == state
        assert ui.snapshot()['login_allowed'] is False
        assert not gate.isEnabled()
    finally:
        ui.close()
        gate.close()


def test_startup_cache_read_failure_still_requires_repair219(tmp_path, monkeypatch):
    app, gate, ui = run_startup219(
        tmp_path, monkeypatch,
        CacheThenCleanupFailure219(cache_fails=True))
    try:
        assert ui.snapshot()['state'] == 'REPAIR_REQUIRED'
        assert ui.snapshot()['login_allowed'] is False
        assert not gate.isEnabled()
    finally:
        ui.close()
        gate.close()
