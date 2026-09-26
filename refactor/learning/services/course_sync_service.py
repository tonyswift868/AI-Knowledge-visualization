"""Account-owned course synchronization; construct after login, dispose on exit.

All store/HTTP work runs on one owned worker. A one-second background pending
probe also observes changes from existing CourseStore callers; they need not
be rewritten to emit signals. notify_local_change() is an optional fast hook.
The injected store, transport and SessionGuard remain owned by the caller.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt

from ..course.sync_course import CourseSync
from .session_guard import SUPERSEDED_MESSAGE


class CourseSyncService(QObject):
    status_changed = Signal(str)
    _changed = Signal()

    def __init__(self, record_store, transport, session_guard=None, parent=None, *,
                 clock=time.monotonic, wall_clock=datetime.now, auto_start=True,
                 executor=None, sync_factory=CourseSync):
        super().__init__(parent)
        self._store = record_store
        self._sync = sync_factory(record_store, transport)
        self._guard = session_guard
        self._clock, self._wall_clock = clock, wall_clock
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="mf-course-sync")
        self._future = None
        self._cancel = Event()
        self._active = self._closed = False
        self._epoch = self._failures = 0
        self._due = self._probe_at = float("inf")
        self._local_due = None
        self._retry_at = 0
        self._timer = QTimer(self)
        # Future completion must not add two one-second polling waits to the
        # three-second debounce. Store probes remain spaced about one second.
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        self._changed.connect(self._on_change, Qt.ConnectionType.QueuedConnection)
        if session_guard is not None:
            session_guard.superseded.connect(self._superseded)
            session_guard.reauth_required.connect(self._reauth)
        if auto_start:
            self.start()

    def start(self):
        """Start immediately after login, or resume after an explicit re-login."""
        if self._closed or self._active:
            return
        self._active = True
        self._epoch += 1
        self._cancel = Event()
        self._failures = 0
        self._retry_at = 0
        self._local_due = None
        self._due = self._probe_at = self._clock()
        self._timer.start()
        self._tick()

    def stop(self):
        self._active = False
        self._epoch += 1
        self._timer.stop()
        self._cancel.set()
        self._sync.cancel()

    def dispose(self):
        if self._closed:
            return
        self.stop()
        self._closed = True
        if self._guard is not None:
            for signal, slot in ((self._guard.superseded, self._superseded),
                                 (self._guard.reauth_required, self._reauth)):
                try:
                    signal.disconnect(slot)
                except RuntimeError:
                    pass  # The shell may already have destroyed its guard.
        self._sync.close()
        self._executor.shutdown(wait=False, cancel_futures=True)

    close = dispose

    def sync_now(self):
        if self._active and not self._closed:
            self._retry_at = 0
            self._due = self._clock()
            self._tick()

    def notify_local_change(self):
        """Thread-safe, fixed debounce window (continuous edits cannot starve it)."""
        if not self._closed:
            self._changed.emit()

    @Slot()
    def _on_change(self):
        if self._active and self._local_due is None:
            self._local_due = self._clock() + 3

    @Slot()
    def _superseded(self):
        if self._active:
            self.stop()
            self.status_changed.emit(SUPERSEDED_MESSAGE)

    @Slot()
    def _reauth(self):
        if self._active:
            self.stop()
            self.status_changed.emit("需要重新登录")

    def _finish(self, kind, result, error):
        now = self._clock()
        if error is not None:
            code = getattr(error, "code", "")
            if code == "NATIVE_SESSION_SUPERSEDED":
                self._superseded()
                if self._guard is not None:
                    self._guard.note_error(code)
                return
            if code in {"REAUTH_REQUIRED", "ENTERPRISE_SESSION_REAUTH_REQUIRED", "SESSION_UNAVAILABLE"}:
                self._reauth()
                return
            if code == "CANCELLED":
                self._due = now + 3
                return
            self._failures = min(self._failures + 1, 5)
            self._retry_at = now + min(30 * 2 ** (self._failures - 1), 300)
            self._due = self._retry_at
            self.status_changed.emit("同步失败，可重试")
        elif kind == "probe":
            if result:
                self._on_change()
            self._probe_at = now + 1
        else:
            self._failures = 0
            self._retry_at = 0
            self._due = now + 300
            self._probe_at = now + 1
            if result.get("pending", 0):
                self._on_change()
            self.status_changed.emit("已同步 " + self._wall_clock().strftime("%H:%M:%S")
                                     if result.get("ok") else "同步中，本机新改动等待同步")

    @Slot()
    def _tick(self):
        if self._closed or not self._active:
            return
        # Poll futures on the QObject's thread. Worker callbacks never touch a
        # destroyed QObject and stop/start fences all late results by epoch.
        if self._future is not None:
            future, kind, epoch = self._future
            if not future.done():
                return
            self._future = None
            if epoch == self._epoch:
                try:
                    result, error = future.result(), None
                except Exception as exc:
                    result, error = None, exc
                self._finish(kind, result, error)
            if not self._active:
                return
        now = self._clock()
        if now < self._retry_at:
            return
        due = min(self._due, self._local_due if self._local_due is not None else float("inf"))
        if now >= due:
            self._local_due = None
            self._due = float("inf")
            self.status_changed.emit("同步中")
            future = self._executor.submit(self._sync.sync, cancel=self._cancel)
            self._future = (future, "sync", self._epoch)
        elif now >= self._probe_at and self._local_due is None:
            self._future = (self._executor.submit(self._store.pending_records, 1), "probe", self._epoch)
