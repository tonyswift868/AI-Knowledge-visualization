"""An explicit click waits for local maintenance off the GUI thread."""
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.updates import DesktopUpdates


@pytest.mark.parametrize('cancel', [False, True])
def test_install239_waits_for_maintenance_without_losing_click(tmp_path, monkeypatch, cancel):
    app = QApplication.instance() or QApplication([])
    gate = QWidget()
    lock = threading.Lock()
    lock.acquire()
    monkeypatch.setattr(install, '_maintenance_lock', lock)
    monkeypatch.setattr(install, 'maintenance_log', lambda *_a, **_k: None)
    invoked = threading.Event()
    clicks = []

    class Core:
        def attach_shutdown_guard(self, _app): pass
        def record_update_authorization(self, root, target, fingerprint):
            clicks.append((target, fingerprint))
        def update_to_latest(self, root, **_options):
            assert lock.locked()  # excludes cleanup for prepare through ACK
            invoked.set()
            return {'state': 'CURRENT_HEALTHY'}

    adapter = DesktopUpdates(app, tmp_path, gate, core=Core())
    adapter.state.update(state='UPDATE_AVAILABLE', latest='2.0.38', release_sha256='a' * 64)
    try:
        adapter.run('install', gate)  # returns even though maintenance still holds the lock
        assert clicks == [('2.0.38', 'a' * 64)]
        assert adapter.snapshot()['busy'] and not invoked.is_set()
        app.processEvents()
        if cancel:
            adapter._cancel.set()
        lock.release()
        deadline = time.monotonic() + 5
        while adapter.snapshot()['busy'] and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        assert not adapter.snapshot()['busy']
        assert invoked.is_set() is not cancel
        assert adapter.state['state'] == ('CANCELLED' if cancel else 'CURRENT_HEALTHY')
        assert not lock.locked()
    finally:
        if lock.locked(): lock.release()
        adapter.close()
        adapter._pool.shutdown(wait=True)
        gate.close()


def test_install239_explicit_repair_keeps_existing_repair_admission(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    gate = QWidget()
    invoked = []
    monkeypatch.setattr(install, 'maintenance_log', lambda *_a, **_k: None)

    class Core:
        def attach_shutdown_guard(self, _app): pass
        def record_update_authorization(self, *_args):
            pytest.fail('repair does not have an offered new-version identity')
        def repair_installation(self, root, **_options):
            invoked.append(root)
            return {'state': 'CURRENT_HEALTHY'}

    adapter = DesktopUpdates(app, tmp_path, gate, core=Core())
    adapter.state.update(state='REPAIR_REQUIRED', shell='repair')
    try:
        adapter.run('install', gate)
        deadline = time.monotonic() + 5
        while adapter.snapshot()['busy'] and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        assert invoked == [tmp_path]
        assert adapter.state['state'] == 'CURRENT_HEALTHY'
    finally:
        adapter.close()
        adapter._pool.shutdown(wait=True)
        gate.close()
