"""BUG-72: reserve the account WebEngine surface before showing LoginGate."""
from types import SimpleNamespace as NS

import pytest
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget

from refactor.desktop.account_identity import AccountPreferenceStore
from refactor.desktop.account_shell_main import AccountShell
from refactor.desktop.login_session import LoginSession
from refactor.desktop.login_shell import LoginGateWindow


@pytest.fixture
def app():
    result = QApplication.instance() or QApplication([])
    result.setQuitOnLastWindowClosed(False)
    return result


class Pane(QWidget):
    finished = Signal(bool)
    prepared = Signal()

    def __init__(self, intent, parent=None, *, shell_runtime=None, load=True):
        super().__init__(parent)
        self.intent = intent
        self._document_started = bool(load)
        self.disposed = False
        self.view = QWidget(self)
        self.page = Page(self)
        self.page.runJavaScript = lambda *args: None
        self.bridge = NS(completed_username=None)

    def prewarm(self):
        self._document_started = True

    def activate(self, intent, theme):
        self.intent = intent
        self.prewarm()

    def dispose(self):
        self.disposed = True
        self.hide()
        self.deleteLater()

    def cancel(self):
        pass


class Page(QObject):
    loadFinished = Signal(bool)


class TracedGate(LoginGateWindow):
    def __init__(self):
        super().__init__()
        self.lifecycle = []

    def showEvent(self, event):
        self.lifecycle.append('shown')
        super().showEvent(event)

    def hideEvent(self, event):
        self.lifecycle.append('hidden')
        super().hideEvent(event)


@pytest.fixture
def shell(app, tmp_path, monkeypatch):
    from refactor.desktop import lifecycle_pane
    monkeypatch.setattr(lifecycle_pane, 'AccountLifecyclePane', Pane)
    session = LoginSession(
        AccountPreferenceStore(QSettings(str(tmp_path / 'account.ini'), QSettings.IniFormat)),
        credentials=NS(), passwords=NS())
    session.remembered = lambda: (None, False)
    session.take_update_login = lambda: (False, None, None)
    gate = TracedGate()
    result = AccountShell(
        app, NS(data_root=tmp_path), login_session=session, gate_window=gate,
        capability_fetcher=lambda: {}, prepare_account_surface=True)
    yield result
    result.close()
    gate.deleteLater()
    app.processEvents()


def test_flicker102_surface_is_created_before_login_window_and_reused(shell, app):
    gate = shell.gate
    warm = shell._warm_pane
    assert warm is not None
    assert warm.parentWidget() is gate
    assert gate._composition_anchor.parentWidget() is gate
    assert not gate._composition_anchor.isHidden()
    assert not warm._document_started
    assert not gate.isVisible()

    gate.show()
    app.processEvents()
    assert gate.lifecycle == ['shown']
    shell._capability_fetcher = lambda: {'register': True, 'forgot_password': True}
    capabilities = QSignalSpy(gate.capabilities_received)
    shell.show()
    if not capabilities.count():
        assert capabilities.wait(5000)
    app.processEvents()
    assert shell._capabilities_loaded and gate.account_capabilities['register'] is True
    assert gate.lifecycle == ['shown']

    shell._prewarm_account_page()
    app.processEvents()
    assert gate.isVisible()
    assert gate.lifecycle == ['shown']
    assert shell._warm_pane is warm and warm._document_started

    shell._begin_account_action('register')
    assert shell._pane is warm
    assert shell._warm_pane is None
    assert warm.parentWidget() is gate.login_card
    assert gate.isVisible()
    assert gate.lifecycle == ['shown']
    shell._return_from_account_action(shell._generation)
    app.processEvents()
    shell._prewarm_account_page()
    shell._begin_account_action('forgot_password')
    assert shell._pane.intent == 'forgot_password'
    shell._return_from_account_action(shell._generation)
    app.processEvents()
    assert gate.isVisible()
    assert gate.lifecycle == ['shown']


def test_flicker102_prewarm_waits_for_capabilities_without_hiding_gate(shell, app):
    gate = shell.gate
    gate.show()
    app.processEvents()
    gate.set_account_capabilities({'register': False, 'forgot_password': False})
    shell._prewarm_account_page()
    assert gate.isVisible()
    assert gate.lifecycle == ['shown']
    assert not shell._warm_pane._document_started
