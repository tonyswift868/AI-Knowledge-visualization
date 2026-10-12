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


class _WindowCourseSyncService(CourseSyncService):
    """Window UI receipts on top of the existing worker and retry timer."""
    _progress_ready = Signal(object, object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._completed = 0
        self._receipt = {}
        self._progress_round = self._finished_progress_round = None
        self._progress_base = 0
        self._progress_ready.connect(self._show_progress, Qt.ConnectionType.QueuedConnection)
        self._sync.on_progress = self._worker_progress

    def _worker_progress(self, progress, cancel):
        if progress['phase'] == 'start':
            self._progress_round = object()
            self._progress_base = self._completed
        progress = dict(progress, round=self._progress_round, base=self._progress_base)
        if not self._closed:
            try:
                self._progress_ready.emit(progress, cancel)
            except RuntimeError:
                pass  # A closing window may have deleted the QObject.

    @Slot(object, object)
    def _show_progress(self, progress, cancel):
        # Ignore receipts from a stopped/previous session, including late signals.
        if (self._closed or not self._active or cancel is not self._cancel or cancel.is_set()
                or progress.get('round') is not self._progress_round
                or progress.get('round') is self._finished_progress_round):
            return
        if progress['phase'] in {'start', 'pull'}:
            message = (f"正在读取云端学习记录，已读取 {progress['completed']} 条…"
                       if progress['completed'] else '正在检查学习记录更新…')
            values = dict(phase=progress['phase'], completed=progress['completed'],
                          show_progress=progress['completed'] > 0)
        else:
            completed = progress['base'] + progress['completed']
            total = progress['base'] + progress['total']
            message = f'正在同步 已完成 {completed} / 共 {total} 条'
            if progress['failed']:
                message += f"，{progress['failed']} 条待重试"
            values = dict(phase='upload', completed=completed, total=total, failed=progress['failed'],
                          show_progress=progress['total'] > 0)
        self._status = dict(state='syncing', at=self._status['at'], message=message, **values)
        self.status_changed.emit(message)
        self.state_changed.emit(self.status())

    def status(self):
        status = super().status()
        if status.get('merged_conflicts'):
            status['merge_messages'] = self._receipt.get('merge_messages', [])
        if status['state'] == 'syncing' and self._future is None and self._receipt.get('failed'):
            status.update(completed=self._completed,
                          total=self._progress_base + self._receipt['total'], failed=self._receipt['failed'])
        return status

    def start(self):
        if not self._active:
            self._completed = 0
        super().start()

    def _finish(self, kind, result, error):
        if kind == 'sync':
            self._finished_progress_round = self._progress_round
            self._receipt = result or {}
            if result:
                self._completed += result.get('uploaded', 0)
            if error is None and result.get('failed'):
                # A few bad rows must not make successful transfers look failed,
                # or enter the ordinary three-second local-edit debounce loop.
                self._failures = min(self._failures + 1, 5)
                self._retry_at = self._clock() + min(30 * 2**(self._failures-1), 300)
                self._due, self._local_due = self._retry_at, None
                self._probe_at = self._retry_at
                total = self._progress_base + result['total']
                self._publish('syncing', f"正在同步 已完成 {self._completed} / 共 {total} 条，{result['failed']} 条待重试；本机记录已保留。",
                              merged_conflicts=result.get('merged_conflicts', 0))
                return
        super()._finish(kind, result, error)
        if kind == 'sync' and result and result.get('ok'):
            self._completed = 0


class WindowSync(QObject):
    _remote_chat = Signal()
    _remote_settings = Signal()
    _remote_plans = Signal()

    def __init__(self, window, services, *, http_transport=None, sync_options=None):
        super().__init__(window)
        self.window, self.bridge = window, window.bridge
        self.settings = services.settings
        self._closed = self._paused = False
        store = services.store
        context = dict(account_id=services.account_id, session_provider=services.session_provider,
                       api_base_url=services.api_base_url, ca_bundle=services.ca_bundle)
        self.transport = SyncTransport(**context, http_transport=http_transport)
        self.guard = SessionGuard(context, self, http_transport=http_transport)
        self.service = _WindowCourseSyncService(store, self.transport, self.guard, self,
                                         auto_start=False, **(sync_options or {}))
        self._unsubscribe = store.subscribe(self._store_changed)
        self.service.state_changed.connect(self._status_changed)
        self.bridge.request_failed.connect(self.note_error)
        self._remote_chat.connect(self._chat_changed, Qt.ConnectionType.QueuedConnection)
        self._remote_settings.connect(self._settings_changed, Qt.ConnectionType.QueuedConnection)
        self._remote_plans.connect(self._plans_changed, Qt.ConnectionType.QueuedConnection)

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
        else:
            if entities & {'chat_sessions', 'chat_messages'}: self._remote_chat.emit()
            if 'account_settings' in entities: self._remote_settings.emit()
            if entities - {'chat_sessions', 'chat_messages', 'account_settings'}:
                self._remote_plans.emit()

    @Slot()
    def _plans_changed(self):
        if not self._closed and not self._paused:
            # Store notifications run after COMMIT, before uploading the outbox.
            self.bridge.push('plans_changed', {})

    @Slot()
    def _settings_changed(self):
        if not self._closed and not self._paused:
            self.window.settings_changed(self.settings.load())

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
            for message in status.get('merge_messages') or ['同步已合并两端修改；本机未发送的修改仍保留。']:
                self.bridge.push('sync_conflict', {'message': message})
