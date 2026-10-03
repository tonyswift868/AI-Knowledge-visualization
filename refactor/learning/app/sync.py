"""Window-owned synchronization and native-session monitoring.

Only the account shell supplies/refreshes credentials. Both HTTP adapters ask
its guarded session provider again for every request. No network runs here on
the GUI thread; the existing services own their cancellable workers.
"""
from PySide6.QtCore import QObject, Signal, Slot, Qt

from ..sync_transport import SyncTransport
from ..services.course_sync_service import CourseSyncService
from ..services.session_guard import SessionGuard, SUPERSEDED_MESSAGE
from ..sync_source import TRANSIENT_SESSION_CODES, CONNECTION_UNSTABLE


class WindowSync(QObject):
    _remote_chat = Signal()

    def __init__(self, window, services, *, http_transport=None, sync_options=None):
        super().__init__(window)
        self.window, self.bridge = window, window.bridge
        self._closed = self._paused = False
        store = services.store
        context = dict(account_id=services.account_id, session_provider=services.session_provider,
                       api_base_url=services.api_base_url, ca_bundle=services.ca_bundle)
        self.transport = SyncTransport(**context, http_transport=http_transport)
        self.guard = SessionGuard(context, self, http_transport=http_transport)
        self.service = CourseSyncService(store, self.transport, self.guard, self,
                                         auto_start=False, **(sync_options or {}))
        self._unsubscribe = store.subscribe(self._store_changed)
        self.service.state_changed.connect(self._status_changed)
        self.bridge.request_failed.connect(self.note_error)
        self._remote_chat.connect(self._chat_changed, Qt.ConnectionType.QueuedConnection)

    def start(self):
        if self._closed: return
        self._paused = False
        self.service.start()
        self.guard.start()

    def stop(self):
        self._paused = True
        self.service.stop()
        self.guard.stop()
        if not self._closed:
            self.bridge.push('sync_status', self.status())

    def dispose(self):
        if self._closed: return
        self._closed = True
        self._unsubscribe()
        self.bridge.request_failed.disconnect(self.note_error)
        self.service.dispose()
        self.guard.dispose()
        self.transport.close()

    def status(self):
        status = self.service.status()
        if self._paused:
            status.update(state='reauth', message='同步已暂停，请重新登录账号。')
        return status

    def sync_now(self):
        if not self._closed and not self._paused:
            self.service.sync_now()
        return self.status()

    @Slot(str)
    def note_error(self, code):
        if not self._closed and not self._paused:
            self.guard.note_error(code)
            if code in TRANSIENT_SESSION_CODES:
                self.window.set_session_notice(CONNECTION_UNSTABLE, show_verify=False)

    def _store_changed(self, origin, entities):
        if self._closed: return
        if origin == 'local':
            self.service.notify_local_change()
        elif entities & {'chat_sessions', 'chat_messages'}:
            self._remote_chat.emit()

    @Slot()
    def _chat_changed(self):
        if not self._closed and not self._paused:
            self.bridge.push('chat_changed', {})

    @Slot(object)
    def _status_changed(self, status):
        if self._closed: return
        if status['state'] in {'reauth', 'superseded'}:
            self.guard.stop()
            self.bridge.cancel_pending()
            message = (SUPERSEDED_MESSAGE if status['state'] == 'superseded' else '登录已失效，请重新验证账号。')
            self.window.set_session_notice(message, show_verify=True)
        elif status['state'] == 'failed' and status['message'] == CONNECTION_UNSTABLE:
            self.window.set_session_notice(CONNECTION_UNSTABLE, show_verify=False)
        elif status['state'] == 'synced' and (self.window.session_notice or {}).get('message') == CONNECTION_UNSTABLE:
            self.window.clear_session_notice()
        self.bridge.push('sync_status', status)
        if status.get('merged_conflicts'):
            self.bridge.push('sync_conflict', {'message': '另一台设备也改过，已按最新的保留。'})
