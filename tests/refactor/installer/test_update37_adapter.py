"""Presentation and automatic continuation without launching an installed app."""
import time
import threading

from PySide6.QtWidgets import QApplication, QWidget

from refactor.desktop.updates import DesktopUpdates


def test_update37_adapter_retains_offer_progress_and_retries_pause(tmp_path):
    app = QApplication.instance() or QApplication([])
    gate = QWidget()
    class Core:
        def attach_shutdown_guard(self, app): pass
    adapter = DesktopUpdates(app, tmp_path, gate, core=Core())
    try:
        adapter.state = dict(state='DOWNLOADING', latest='2.0.2', current='2.0.1', release_notes='notes', mandatory=True)
        adapter._receive({'progress': dict(downloaded_bytes=10, expected_download_bytes=100)})
        assert adapter.snapshot()['latest'] == '2.0.2' and adapter.snapshot()['downloaded_bytes'] == 10
        adapter._receive({'action': 'install', 'result': dict(state='DOWNLOAD_PAUSED_OR_INTERRUPTED', cancelled=False)})
        assert adapter._retry.isActive() and adapter._retry.interval() == 60000
        assert adapter.snapshot()['release_notes'] == 'notes' and adapter.snapshot()['mandatory']
        adapter._receive({'action': 'install', 'result': dict(state='FAILED_RECOVERABLE', code='UPDATE_CHECK_NETWORK_UNAVAILABLE')})
        assert adapter._retry.isActive() and adapter.snapshot()['state'] == 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
        adapter._retry.stop()
        adapter._receive({'action': 'install', 'result': dict(state='DOWNLOAD_PAUSED_OR_INTERRUPTED', cancelled=True)})
        assert not adapter._retry.isActive()
        adapter._receive({'action': 'install', 'result': dict(state='FAILED_SECURITY')})
        assert not adapter._retry.isActive()
    finally:
        adapter.close(); gate.close()


def test_update37_adapter_retry_calls_original_install(tmp_path):
    app = QApplication.instance() or QApplication([])
    gate = QWidget()
    class Core:
        def attach_shutdown_guard(self, app): pass
    adapter = DesktopUpdates(app, tmp_path, gate, core=Core())
    try:
        calls = []
        adapter.run = lambda action, window: calls.append(action)
        adapter._receive({'action': 'install', 'result': dict(state='DOWNLOAD_PAUSED_OR_INTERRUPTED')})
        adapter._retry.setInterval(1)
        until = time.monotonic() + 1
        while not calls and time.monotonic() < until:
            app.processEvents(); time.sleep(.005)
        assert calls == ['install']
    finally:
        adapter.close(); gate.close()


def test_update37_defer_and_daily_check_survive_window_recreation(tmp_path):
    app = QApplication.instance() or QApplication([])
    gate = QWidget()
    class Core:
        checks = 0
        def attach_shutdown_guard(self, app): pass
        def check_latest(self, root, cancel=None):
            self.checks += 1
            return dict(state='UPDATE_AVAILABLE', latest='2.0.2')
    core = Core()
    adapter = DesktopUpdates(app, tmp_path, gate, core=core)
    def finish():
        until = time.monotonic() + 2
        while adapter.snapshot()['busy'] and time.monotonic() < until:
            app.processEvents(); time.sleep(.005)
        assert not adapter.snapshot()['busy']
    try:
        adapter.run('automatic', gate); finish()
        adapter.run('defer', gate)
        other = QWidget()
        try:
            adapter.run('automatic', other); finish()
            assert core.checks == 1 and adapter.snapshot()['deferred']
            adapter.run('check', other); finish()
            assert core.checks == 2
            adapter._next_automatic = time.monotonic() - 1
            adapter.run('automatic', other); finish()
            assert core.checks == 3 and adapter.snapshot()['deferred']
        finally: other.close()
    finally:
        adapter.close(); gate.close()


def test_update37_close_cancels_check_worker(tmp_path):
    app = QApplication.instance() or QApplication([])
    gate = QWidget(); started = threading.Event(); finished = threading.Event()
    class Core:
        def attach_shutdown_guard(self, app): pass
        def check_latest(self, root, cancel=None):
            started.set()
            while not cancel(): time.sleep(.005)
            finished.set()
            return dict(state='FAILED_RECOVERABLE', cancelled=True)
    adapter = DesktopUpdates(app, tmp_path, gate, core=Core())
    try:
        adapter.run('check', gate)
        assert started.wait(1)
        adapter.close()
        assert finished.wait(1)
    finally:
        adapter.close(); gate.close()
