"""Real Qt event loop stays responsive while the existing worker prepares."""
import threading
import time
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget
from refactor.desktop.updates import DesktopUpdates
from refactor.desktop.delivery import per_user_install
from tests.refactor.installer.test_startup96_presentation import app96, Feed96, pump


@pytest.mark.parametrize('failure', [False, True])
def test_offer_before_prepare_keeps_login_and_event_loop_responsive105(app96, tmp_path, monkeypatch, failure):
    monkeypatch.setattr(per_user_install, 'schedule_updater_refresh', lambda *_: None)
    monkeypatch.setattr(per_user_install, 'schedule_program_cleanup', lambda *a, **k: None)
    feed = Feed96(); feed.release.set()
    entered, done = threading.Event(), threading.Event()
    def download(root, cancel, progress=None, reuse_prepared=False):
        assert threading.current_thread() is not threading.main_thread()
        entered.set()
        while not done.wait(.01):
            if cancel(): return {}
        if failure: raise OSError('fixture interrupted preparation')
        return {'prepared': True}
    feed.download_latest = download
    gate = QWidget(); gate.show()
    ui = DesktopUpdates(app96, tmp_path, gate, core=feed)
    ticks = []
    timer = QTimer(); timer.setInterval(5); timer.timeout.connect(lambda: ticks.append(True)); timer.start()
    try:
        ui.start(); pump(app96, lambda: entered.is_set() and gate.isEnabled())
        assert not ui.snapshot()['busy'] and ui._background_preparing
        # Optional releases are surfaced by the main-window banner; startup
        # preparation must not create a native offer or hold login.
        assert ui._prompt is None
        pump(app96, lambda: len(ticks)>=5)
        done.set(); pump(app96, lambda: not ui._background_preparing)
        assert ui.state['state']=='OPTIONAL_UPDATE'
        assert ui.state['prepared'] is not failure
        assert ui._prompt is None
        assert gate.isEnabled()
        assert ui.state['latest'] == '2.0.6'
    finally:
        done.set(); timer.stop(); ui.close(); gate.close()
