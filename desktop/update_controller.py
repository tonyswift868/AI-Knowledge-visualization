"""One-click UI orchestration. Core owns validation, pending work and restart."""
from __future__ import annotations

import threading
from enum import StrEnum

from PySide6.QtCore import QObject, QEvent, QSettings, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .update_lifecycle import CoreUpdatePort


class UpdateState(StrEnum):
    CHECKING = 'CHECKING'
    OPTIONAL_UPDATE = 'OPTIONAL_UPDATE'
    UPDATE_AVAILABLE = 'OPTIONAL_UPDATE'  # Compatibility name, one state.
    MANDATORY_UPDATE = 'MANDATORY_UPDATE'
    REPAIR_REQUIRED = 'REPAIR_REQUIRED'
    DOWNLOADING = 'DOWNLOADING'
    PREPARING = 'PREPARING'
    UP_TO_DATE = 'UP_TO_DATE'
    DEFERRED_TO_NEXT_LAUNCH = 'DEFERRED_TO_NEXT_LAUNCH'
    RECOVERABLE_ERROR = 'RECOVERABLE_ERROR'


STATE_MAP = {
    'IDLE': UpdateState.CHECKING, 'CHECKING': UpdateState.CHECKING,
    'UPDATE_AVAILABLE': UpdateState.UPDATE_AVAILABLE,
    'OPTIONAL_UPDATE': UpdateState.OPTIONAL_UPDATE,
    'MANDATORY_UPDATE': UpdateState.MANDATORY_UPDATE,
    'REPAIR_REQUIRED': UpdateState.REPAIR_REQUIRED,
    'PREPARING': UpdateState.PREPARING, 'DOWNLOADING': UpdateState.DOWNLOADING,
    'UP_TO_DATE': UpdateState.UP_TO_DATE, 'CURRENT_HEALTHY': UpdateState.UP_TO_DATE,
    'DEFERRED_TO_NEXT_LAUNCH': UpdateState.DEFERRED_TO_NEXT_LAUNCH,
    'PENDING_INSTALL': UpdateState.DEFERRED_TO_NEXT_LAUNCH,
}
DEFERRED = frozenset(('DEFERRED_TO_NEXT_LAUNCH', 'PENDING_INSTALL'))


class UpdateController(QObject):
    finished = Signal(str)
    login_policy_changed = Signal(bool)

    def __init__(self, service=None, parent=None, *, settings=None, port=None):
        super().__init__(parent)
        self.service = service
        self.settings = settings if settings is not None else QSettings('MentorFlow', 'MentorFlow')
        self.key = f"updater/{getattr(service, 'install_identity', 'unconfigured')}/"
        self.window = None
        self._thread = None
        self._closed = self._started = self._chain = self._ending = False
        self._cancelled = False
        self._requirement = ''
        self._installation_verified = None
        self._last_login_blocked = False
        self._operation_pending = False
        self._outcome = self._action = ''
        self._window_id = None
        self._window_available = False
        self._cancel = threading.Event()
        self.port = port if port is not None else CoreUpdatePort(service,
            window_id=lambda: self._window_id,
            can_apply=lambda: self._window_available and not self._closed and not self._ending)
        self.finished.connect(self._finished)
        app = QApplication.instance()
        app.commitDataRequest.connect(self._session_ending)
        app.aboutToQuit.connect(self.shutdown)

    @property
    def busy(self):
        return bool(self._thread and self._thread.is_alive()) or bool(self.port.snapshot.get('busy', False))

    @property
    def raw_state(self):
        raw = self.port.snapshot.get('state', 'CHECKING')
        return raw if raw in DEFERRED else self._outcome or raw

    @property
    def state(self):
        if self.raw_state in DEFERRED:
            return UpdateState.DEFERRED_TO_NEXT_LAUNCH
        if self.busy and self._action == 'apply_and_restart':
            return UpdateState.PREPARING
        if self._installation_verified is False and not self.busy and not self._outcome:
            return UpdateState.REPAIR_REQUIRED
        return STATE_MAP.get(self.raw_state, UpdateState.RECOVERABLE_ERROR)

    @property
    def login_blocked(self):
        self.refresh_login_policy()
        return self._last_login_blocked

    def refresh_login_policy(self):
        state = STATE_MAP.get(self.raw_state)
        if state in (UpdateState.MANDATORY_UPDATE, UpdateState.REPAIR_REQUIRED):
            self._requirement = state.value
        elif state in (UpdateState.UP_TO_DATE, UpdateState.OPTIONAL_UPDATE) and not self._outcome and not self._operation_pending:
            self._requirement = ''
        blocked = bool(self._requirement or self._installation_verified is False)
        if blocked != self._last_login_blocked:
            self._last_login_blocked = blocked
            self.login_policy_changed.emit(blocked)

    def set_installation_verified(self, value):
        self._installation_verified = value
        self.refresh_login_policy()

    def configure_service(self, service):
        if self.busy or self._closed:
            return
        self.port.close()
        self.service = service
        self.key = f"updater/{getattr(service, 'install_identity', 'unconfigured')}/"
        self.port = CoreUpdatePort(service, window_id=lambda: self._window_id,
            can_apply=lambda: self._window_available and not self._closed and not self._ending)

    def bind_window(self, window):
        if self._closed or window is None:
            return
        if self.window is not window:
            if self.window is not None:
                try:
                    self.window.removeEventFilter(self)
                except RuntimeError:
                    pass
            self.window = window
            window.installEventFilter(self)
            token = id(window)
            window.destroyed.connect(lambda: self._forget_window(token))
            geometry = self.settings.value(self.key + type(window).__name__ + '/geometry')
            if geometry is not None and hasattr(geometry, 'data'):
                window.restoreGeometry(geometry)
        self._window_id = f'{int(window.winId()):016x}'
        self._window_available = window.isVisible()

    def _forget_window(self, token):
        if id(getattr(self, 'window', None)) == token:
            self.window = None
            self._window_id = None
            self._window_available = self._chain = False

    def start(self):
        if not self._started and not self._closed:
            self._started = True
            self.run('check')  # Core, not UI cache state, decides pending behavior.

    def run(self, action):
        if self._closed or self.busy:
            return
        if action not in ('check', 'download', 'repair', 'apply_and_restart'):
            raise ValueError('unknown update UI action')
        if self._installation_verified is False and action not in ('repair', 'apply_and_restart'):
            return
        if action != 'repair' and not self.port.snapshot.get('available', True):
            return
        if self._ending:
            return
        if action == 'apply_and_restart':
            if self.window is None or not self.window.isVisible() or QApplication.instance().isSavingSession():
                # Only an explicit core result may promise next-launch apply.
                self._outcome = 'RECOVERABLE_ERROR'
                self._chain = False
                return
            self.save_ui_state()
        else:
            self._chain = action in ('download', 'repair')
            self._cancelled = False
            self._cancel.clear()
        self._operation_pending = True
        self._outcome = ''
        self._action = action
        def work():
            try:
                if action == 'check':
                    result = self.port.check_latest()
                elif action == 'download':
                    result = self.port.download_latest(lambda _: None, self._cancel)
                elif action == 'repair':
                    result = self.port.repair_and_check(lambda _: None, self._cancel)
                else:
                    result = self.port.apply_and_relaunch()
                if isinstance(result, dict):
                    code = result.get('state')
                    if code in DEFERRED or code in ('RECOVERABLE_ERROR', 'MANDATORY_UPDATE', 'REPAIR_REQUIRED'):
                        self._outcome = code
            except Exception:
                self._outcome = 'RECOVERABLE_ERROR'
                # Never display or log arbitrary exception/HTTP/credential text.
            finally:
                try:
                    self.finished.emit(action)
                except RuntimeError:
                    pass
        self._thread = threading.Thread(target=work, daemon=True, name='mentorflow-update-ui')
        self._thread.start()

    def _finished(self, action):
        if self._closed or action != self._action:
            return
        if self.busy:
            QTimer.singleShot(10, lambda: self._finished(action))
            return
        self._operation_pending = False
        if self._cancelled:
            self._outcome = 'RECOVERABLE_ERROR'
        if self.state in (UpdateState.RECOVERABLE_ERROR, UpdateState.DEFERRED_TO_NEXT_LAUNCH):
            self._chain = False
            return
        self.refresh_login_policy()
        if action in ('download', 'repair') and self.raw_state == 'PREPARING' and self._chain:
            self._chain = False  # Consume once before scheduling apply.
            self.run('apply_and_restart')

    def save_ui_state(self):
        if self.window is not None:
            self.settings.setValue(self.key + type(self.window).__name__ + '/geometry', self.window.saveGeometry())
            navigation = getattr(self.window, 'navigation', None)
            if navigation is not None:
                self.settings.setValue(self.key + 'navigation', navigation.currentRow())
        self.settings.sync()

    def restore_navigation(self):
        navigation = getattr(self.window, 'navigation', None)
        if navigation is not None and self.settings.contains(self.key + 'navigation'):
            try:
                row = int(self.settings.value(self.key + 'navigation', 0))
                if 0 <= row < navigation.count():
                    navigation.setCurrentRow(row)
            except (ValueError, TypeError):
                pass

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'window', None) and event.type() == QEvent.Type.Close:
            self._window_available = self._chain = False
            self.save_ui_state()
        return False

    def _session_ending(self, _manager=None):
        self._ending = True
        defer = getattr(self.port, 'defer_session', None)
        if defer is not None:
            defer()
        self.save_ui_state()
        self.cancel()

    def cancel(self):
        self._chain = False
        self._cancelled = True
        self._cancel.set()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.cancel()
        self.port.close()
        if self.window is not None:
            try:
                self.window.removeEventFilter(self)
            except RuntimeError:
                pass
            self.window = None
        app = QApplication.instance()
        app.commitDataRequest.disconnect(self._session_ending)
        app.aboutToQuit.disconnect(self.shutdown)


def application_updater(factory=None):
    app = QApplication.instance()
    existing = getattr(app, '_mentorflow_updater', None)
    if existing is None or existing._closed:
        if factory is None:
            from .update_lifecycle import configured_update_service
            factory = configured_update_service
        try:
            service = factory()
        except Exception:
            service = None
        existing = UpdateController(service, app)
        app._mentorflow_updater = existing
    return existing
