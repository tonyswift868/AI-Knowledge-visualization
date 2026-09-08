"""UI orchestration; verification/download/transaction authority stays in the service."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from enum import StrEnum

from PySide6.QtCore import QObject, QEvent, QSettings, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .update_session import UpdateSession, UnavailableSession, SessionEnding, was_session_deferred


class UpdateState(StrEnum):
    CHECKING = 'CHECKING'
    UPDATE_AVAILABLE = 'UPDATE_AVAILABLE'
    DOWNLOADING = 'DOWNLOADING'
    READY_TO_RESTART = 'READY_TO_RESTART'
    APPLYING = 'APPLYING'
    UP_TO_DATE = 'UP_TO_DATE'
    RECOVERABLE_ERROR = 'RECOVERABLE_ERROR'


STATE_MAP = {
    'IDLE': UpdateState.CHECKING, 'CHECKING': UpdateState.CHECKING,
    'UPDATE_AVAILABLE': UpdateState.UPDATE_AVAILABLE,
    'PREPARING': UpdateState.DOWNLOADING, 'DOWNLOADING': UpdateState.DOWNLOADING,
    'READY_TO_APPLY': UpdateState.READY_TO_RESTART,
    'APPLYING': UpdateState.APPLYING, 'RESTART_REQUIRED': UpdateState.APPLYING,
    'UP_TO_DATE': UpdateState.UP_TO_DATE,
}


class UpdateController(QObject):
    finished = Signal(str)

    def __init__(self, service, parent=None, *, settings=None, session=None):
        super().__init__(parent)
        self.service = service
        self.settings = settings if settings is not None else QSettings('MentorFlow', 'MentorFlow')
        try:
            self.session = session if session is not None else UpdateSession()
        except OSError:
            self.session = UnavailableSession()
        self.key = f"updater/{getattr(service, 'install_identity', 'unconfigured')}/"
        self.window = None
        self._thread = None
        self._closed = self._started = self._resume = False
        self._error = ''
        self._deferred = False
        self._action = ''
        self._bridge = self._window_id = None
        self._handoff_peer = None
        self.finished.connect(self._finished)
        app = QApplication.instance()
        app.commitDataRequest.connect(self._session_ending)
        app.aboutToQuit.connect(self.shutdown)

    @property
    def busy(self):
        transport = getattr(getattr(self.service, 'cache', None), 'transport', None)
        return bool(self._thread and self._thread.is_alive()) or bool(getattr(transport, 'busy', False))

    @property
    def state(self):
        if self._error or self.service is None:
            return UpdateState.RECOVERABLE_ERROR
        if self.busy and self._action == 'apply_and_restart':
            return UpdateState.APPLYING
        return STATE_MAP.get(self.service.snapshot['state'], UpdateState.RECOVERABLE_ERROR)

    def bind_window(self, window):
        """After parenting, including while the Settings page is hidden."""
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
        if self.service and (self._bridge is None or self.service.apply_bridge is not self._bridge):
            bridge = self.service.apply_bridge
            if bridge is None:
                from .update_lifecycle import handoff
                bridge = lambda root, stage, envelope: handoff(root, stage, envelope, hwnd_hex=self._window_id)
            def guarded_bridge(*args):
                self.session.require_running()
                if self._closed:
                    raise SessionEnding('Update deferred until next normal launch')
                return bridge(*args)
            self._bridge = guarded_bridge
            self.service.apply_bridge = guarded_bridge

    def _forget_window(self, token):
        if id(getattr(self, 'window', None)) == token:
            self.window = None

    def start(self):
        if self._started or self._closed or self.service is None:
            return
        self._started = True
        pending = self.settings.value(self.key + 'pending_release', '')
        # A denied/failed handoff can relaunch the old version. Never turn that
        # recovery into an automatic retry/UAC loop for the same release.
        attempted = pending == self.settings.value(self.key + 'attempted_release', '')
        deferred = pending and attempted and was_session_deferred(
            self.service.cache.runtime_root, pending,
            self.settings.value(self.key + 'attempted_pid', 0),
            self.settings.value(self.key + 'attempted_at_ns', 0))
        self._resume = bool(pending and (not attempted or deferred))
        self.run('check')  # Fresh async check; cached backoff cannot authorize apply.

    def run(self, action, **kwargs):
        if self._closed or self.service is None or self.busy:
            return
        if action == 'apply_and_restart':
            try:
                self.session.require_running()
                if self.window is None or not self.window.isVisible() or QApplication.instance().isSavingSession():
                    raise SessionEnding('Update deferred until next normal launch')
                self.save_ui_state()
                self.remember_pending()
                self.settings.setValue(self.key + 'attempted_release', self.settings.value(self.key + 'pending_release', ''))
                self.settings.setValue(self.key + 'attempted_pid', os.getpid())
                self.settings.setValue(self.key + 'attempted_at_ns', str(time.time_ns()))
                self.settings.sync()
            except Exception:
                self._error = '更新已保留，将在下次正常启动时重新检查。'
                return
        self._error = ''
        self._deferred = False
        self._action = action
        def work():
            try:
                result = getattr(self.service, action)(**kwargs)
                if action == 'download' and self.service.snapshot['state'] == 'READY_TO_APPLY':
                    from .update_lifecycle import remember_download
                    remember_download(self.service)
                if action == 'apply_and_restart' and isinstance(result, dict) and result.get('bridge_pid'):
                    from .windows_process_peer import ProcessPeer
                    self._handoff_peer = ProcessPeer(result['bridge_pid'])
                    if self._closed:
                        self._handoff_peer.close()
                        self._handoff_peer = None
            except SessionEnding:
                self._deferred = True
                self._error = '更新已保留，将在下次正常启动时重新检查。'
            except Exception:
                logging.getLogger(__name__).warning('Update action did not finish: %s', action)
                self._error = '暂时无法完成更新，请重试。当前版本可继续使用。'
            finally:
                try:
                    self.finished.emit(action)
                except RuntimeError:
                    pass  # Its owning window may already have been destroyed.
        self._thread = threading.Thread(target=work, daemon=True, name='mentorflow-update-ui')
        self._thread.start()

    def _finished(self, action):
        if self._closed or action != self._action:
            return
        if self.busy:
            QTimer.singleShot(10, lambda: self._finished(action))
            return
        snap = self.service.snapshot
        if self._deferred:
            self._clear_attempt()
        if self._error:
            return
        if snap['state'] == 'UP_TO_DATE':
            self.settings.remove(self.key + 'pending_release')
            self._clear_attempt()
        if action == 'apply_and_restart' and self._handoff_peer is not None:
            QTimer.singleShot(250, self._poll_handoff)
        if action == 'check' and self._resume:
            self._resume = False
            expected = self.settings.value(self.key + 'pending_release', '')
            release = getattr(self.service, 'release', None)
            if (snap['state'] == 'UPDATE_AVAILABLE' and release is not None and
                    hashlib.sha256(release.envelope).hexdigest() == expected):
                self._resume = True
                self.run('download')  # Existing cache revalidation/reconstruction.
                return
            if snap['state'] == 'UPDATE_AVAILABLE':
                self.settings.remove(self.key + 'pending_release')
                self.settings.sync()
        if snap['state'] == 'READY_TO_APPLY':
            self.remember_pending()
            if self._resume:
                self._resume = False
                self.run('apply_and_restart')  # Fresh metadata/signature/tree hashes again.

    def _poll_handoff(self):
        if self._closed or self._handoff_peer is None:
            return
        if self._handoff_peer.alive():
            QTimer.singleShot(250, self._poll_handoff)
            return
        self._handoff_peer.close()
        self._handoff_peer = None
        self._error = '重启更新未完成，可重新检查。当前版本可继续使用。'

    def remember_pending(self):
        if self.service and self.service.staged and self.service.release:
            self.settings.setValue(self.key + 'pending_release', hashlib.sha256(self.service.release.envelope).hexdigest())
            self.settings.sync()

    def _clear_attempt(self):
        for field in ('attempted_release', 'attempted_pid', 'attempted_at_ns'):
            self.settings.remove(self.key + field)
        self.settings.sync()

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
            if QApplication.instance().isSavingSession():
                self.session.defer()
            self.save_ui_state()
            self.remember_pending()
            # Normal close and OS close only persist intent. Never apply here.
        return False

    def _session_ending(self, _manager=None):
        self.session.defer()
        self._resume = False
        self._clear_attempt()
        self.save_ui_state()
        self.remember_pending()
        if self.service:
            self.service.cancel_download()

    def cancel(self):
        self._resume = False
        if self.service:
            self.service.cancel_download()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.remember_pending()
        self.cancel()
        if self.service and hasattr(self.service, 'close'):
            self.service.close()
        self.session.close()
        if self._handoff_peer is not None:
            self._handoff_peer.close()
            self._handoff_peer = None
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
            from .update_service import configured_mature_service
            factory = configured_mature_service
        try:
            service = factory()
        except Exception:
            service = None
        existing = UpdateController(service, app)
        app._mentorflow_updater = existing
    return existing
