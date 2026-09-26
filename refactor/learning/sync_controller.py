"""Background account sync owned by the learning window, not individual pages."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import time

from PySide6.QtCore import QObject, QTimer, Signal

from .plan_models import PlanError
from .sync_transport import SyncTransport


class _HistoryTransport(SyncTransport):
    def __init__(self, service, **kwargs):
        super().__init__(**kwargs)
        self.service = service
        self._history_signature = None
        self._history_changed = False

    def read_snapshot(self):
        snapshot = super().read_snapshot()
        self.require_current()
        self.history_error = ""
        self._history_changed = False
        try:
            signature = tuple(sorted((e["kind"], e["entity_id"], e["revision"], e["deleted"])
                for e in snapshot["entities"]
                if isinstance(e, dict) and isinstance(e.get("payload"), dict)
                and "product_record" in e["payload"]))
            if signature != self._history_signature:
                # The sync controller emits one coalesced invalidation after
                # the whole sync round.  Suppress the service-local signal
                # here so historical restore cannot rebuild the visible page
                # twice and cause a flash.
                self.service.accept_snapshot(snapshot, emit_changed=False)
                self._history_signature = signature
                self._history_changed = True
        except Exception:
            # A historical format/file error belongs to history, not to new
            # plans or the current account's cloud write protocol.
            self.history_error = "部分旧记录暂未恢复，本机和云端原记录仍保留。"
        return snapshot


class LearningSyncController(QObject):
    status_changed = Signal(str)
    restored = Signal()

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self._closed = False
        self._engine = self._adapter = self._transport = None
        self._future = None
        self._force = True
        self._last_scan = ""
        # This is deliberately separate from ``_last_scan``.  The latter is
        # the worker's polling baseline; it can differ from the last visible
        # state while a retry is pending.  Using it for UI invalidation made a
        # failed retry repeatedly rebuild PlansPage/PlanCompletionPage.
        self._last_content_fingerprint = None
        self._last_status = None
        self._last_attempt = self._last_remote = 0
        self._retry = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mf-account-sync")
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        QTimer.singleShot(0, self.sync_now)

    def _ensure(self):
        if self._engine is not None:
            return
        from .sync_local import LocalSyncAdapter
        from .sync_records import SyncEngine
        c = self.context
        transport_factory = c.get("sync_transport_factory")
        self._transport = transport_factory() if transport_factory else _HistoryTransport(c["plan_service"],
            account_id=c["account_id"], session_provider=c["session_provider"],
            api_base_url=c["api_base_url"], ca_bundle=c["ca_bundle"])
        scope = self._transport.require_current()
        self._adapter = LocalSyncAdapter(c["plan_service"], scope_check=lambda: self._transport.require_current() == scope)
        self._engine = SyncEngine(c["data_root"], scope, self._transport, self._adapter)

    def sync_now(self):
        if self._closed:
            return
        self._force = True
        if self._future is None:
            self._start()

    def _fingerprint(self):
        records = self._adapter.scan()
        serializable = [[list(key), value] for key, value in sorted(records.items())]
        return hashlib.sha256(json.dumps(serializable, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _run(self, force):
        if self._closed:
            raise PlanError("CANCELLED", "同步已关闭。")
        self._ensure()
        fingerprint = self._fingerprint()
        if not force and not self._retry and fingerprint == self._last_scan:
            return None
        result = self._engine.sync()
        if self._closed:
            return None
        self._engine._fence()
        finalized = self._adapter.finalize()
        self._engine._fence()
        if finalized.get("pending"):
            result["pending"] += finalized["pending"]
            result["ok"] = False
        result["history_error"] = getattr(self._transport, "history_error", "")
        result["has_saved_conflicts"] = bool(self._adapter.conflicts())
        result["fingerprint"] = self._fingerprint()
        # A successful polling round is not itself a content change.  Pages
        # must only be invalidated when the account-scoped records actually
        # changed; emitting changed/restored on every forced poll rebuilt the
        # classroom and made the visible page flash every few minutes.
        # Include edits made before this worker started.  A caller can update
        # the local store between timer ticks without emitting a Qt signal;
        # comparing only start/end would otherwise hide that change when the
        # sync round itself made no writes.
        # A page invalidation is an account-data event, not a sync-attempt
        # event.  Compare with the last fingerprint that was actually exposed
        # to the UI so retries and periodic no-op polls stay invisible.
        result["content_changed"] = (
            self._last_content_fingerprint is None
            or result["fingerprint"] != self._last_content_fingerprint
            or bool(getattr(self._transport, "_history_changed", False))
        )
        return result

    def _emit_status(self, message):
        if message == self._last_status:
            return
        self._last_status = message
        self.status_changed.emit(message)

    def _start(self):
        force, self._force = self._force, False
        self._last_attempt = time.monotonic()
        self._future = self._executor.submit(self._run, force)
        if force:
            self._emit_status("正在同步计划与学习记录…")

    def _poll(self):
        if self._closed:
            return
        if self._future is not None and self._future.done():
            future, self._future = self._future, None
            try:
                result = future.result()
                if result is not None:
                    self._last_remote = time.monotonic()
                    self._last_scan = result["fingerprint"]
                    if result.get("content_changed"):
                        self._last_content_fingerprint = result["fingerprint"]
                    self._retry = not result["ok"]
                    if result["conflicts"] or result["has_saved_conflicts"]:
                        message = "同步发现不同设备的修改，已保留双方记录，可查看同步冲突。"
                    elif result["pending"] or result["errors"]:
                        message = "部分记录暂未同步，本机内容已保留；可稍后重试。"
                    else:
                        message = "计划与学习记录已同步"
                    if result.get("history_error"):
                        message += "；" + result["history_error"]
                    self._emit_status(message)
                    if result.get("content_changed"):
                        self.context["plan_service"].changed.emit()
                        self.restored.emit()
            except Exception as exc:
                self._retry = True
                self._emit_status(str(exc) if isinstance(exc, PlanError) else "暂时无法同步，已保留本机记录，可重试。")
        if self._future is None:
            elapsed = time.monotonic() - self._last_attempt
            if self._force or elapsed >= (30 if self._retry else 5):
                # Periodic pulls use the same account-scoped endpoint. Local
                # edits are detected separately and coalesced into one upload.
                self._force |= time.monotonic() - self._last_remote >= 300
                self._start()

    def conflicts(self):
        return self._adapter.conflicts() if self._adapter else []

    def close(self):
        self._closed = True
        self._timer.stop()
        if self._engine:
            self._engine.close()
        if self._transport:
            self._transport.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
