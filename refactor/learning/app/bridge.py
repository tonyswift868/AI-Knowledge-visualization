"""Request/response + event channel between the React page and Python.

Wire format (JSON strings over one QWebChannel object named ``mf``):

    page -> python   call('{"id": "...", "method": "ns.name", "params": {...}}')
                     call('{"id": "...", "method": "cancel", "params": {"target": "<id>"}}')
    python -> page   event('{"type": "response", "id", "ok": true, "result": ...}')
                     event('{"type": "response", "id", "ok": false, "error": {"code", "message"}}')
                     event('{"type": "stream", "id", "delta": "..."}')
                     event('{"type": "event", "name": "...", "data": ...}')

Everything the page sends is untrusted input: size, shape and method names are
checked here, parameters by each handler. Handlers run on a worker pool unless
registered with ``ui=True``; results always reach the page from the GUI thread.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import logging
import re
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

log = logging.getLogger(__name__)

MAX_REQUEST_BYTES = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_METHOD = re.compile(r"[a-z][a-z_]*\.[a-z][a-z_]*")


class BridgeError(Exception):
    """A user-facing failure: ``message`` is shown to the learner as is."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class CallContext:
    request_id: str
    cancel: threading.Event
    stream: Callable[[str], None]


@dataclass(frozen=True)
class _Handler:
    function: Callable[[dict, CallContext], Any]
    ui: bool


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default, separators=(",", ":"))


class AppBridge(QObject):
    """Registered on the page's QWebChannel as ``mf``."""

    event = Signal(str)
    request_failed = Signal(str)
    _deliver = Signal(str)

    def __init__(self, parent=None, *, workers: int = 4):
        super().__init__(parent)
        self._handlers: dict[str, _Handler] = {}
        self._pending: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mf-bridge")
        # Worker threads hand results to the GUI thread through this queued
        # signal; QWebChannel signals must only be emitted from the GUI thread.
        self._deliver.connect(self._emit, Qt.ConnectionType.QueuedConnection)

    def register(self, method: str, function, *, ui: bool = False):
        if not _METHOD.fullmatch(method) or method in self._handlers:
            raise ValueError(f"invalid or duplicate bridge method: {method}")
        self._handlers[method] = _Handler(function, ui)

    def push(self, name: str, data=None):
        """Python-initiated notification (thread-safe)."""
        self._send({"type": "event", "name": name, "data": data})

    def _send(self, payload):
        if not self._closed:
            self._deliver.emit(encode(payload))

    @Slot(str)
    def _emit(self, text):
        if not self._closed:
            self.event.emit(text)

    @Slot(str)
    def call(self, text: str):
        if self._closed:
            return
        try:
            if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_REQUEST_BYTES:
                raise ValueError("request too large")
            request = json.loads(text)
            request_id, method, params = request.get("id"), request.get("method"), request.get("params", {})
            if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
                raise ValueError("bad id")
            if not isinstance(params, dict):
                raise ValueError("bad params")
        except (ValueError, TypeError, AttributeError):
            log.warning("rejected malformed bridge request")
            return
        if method == "cancel":
            with self._lock:
                target = self._pending.get(params.get("target"))
            if target is not None:
                target.set()
            return
        handler = self._handlers.get(method) if isinstance(method, str) else None
        if handler is None:
            self._send({"type": "response", "id": request_id, "ok": False,
                        "error": {"code": "UNKNOWN_METHOD", "message": "界面请求了不存在的功能。"}})
            return
        cancel = threading.Event()
        with self._lock:
            self._pending[request_id] = cancel
        context = CallContext(request_id, cancel,
                              lambda delta: self._send({"type": "stream", "id": request_id, "delta": str(delta)}))
        if handler.ui:
            self._run(request_id, handler, params, context)
        else:
            try:
                self._pool.submit(self._run, request_id, handler, params, context)
            except RuntimeError:  # pool already shut down during close
                pass

    def _run(self, request_id, handler, params, context):
        try:
            if context.cancel.is_set():
                raise BridgeError('CANCELLED', '已取消。')
            result = handler.function(params, context)
            response = {"type": "response", "id": request_id, "ok": True, "result": result}
        except BridgeError as exc:
            response = {"type": "response", "id": request_id, "ok": False,
                        "error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:  # noqa: BLE001 - map every service failure to a visible error
            code = getattr(exc, "code", None)
            message = getattr(exc, "message", None)
            # PlanError deliberately keeps its text in Exception.args. Preserve
            # known auth codes even when that exception has no .message field.
            if code == 'NATIVE_SESSION_SUPERSEDED':
                message = '账号已在另一台设备登录'
            elif isinstance(code, str) and code in {'REAUTH_REQUIRED', 'ENTERPRISE_SESSION_REAUTH_REQUIRED', 'SESSION_UNAVAILABLE'}:
                message = '登录已失效，请重新验证账号。'
            if isinstance(code, str) and isinstance(message, str) and message:
                response = {"type": "response", "id": request_id, "ok": False,
                            "error": {"code": code, "message": message}}
            else:
                log.exception("bridge handler failed: %s", request_id)
                response = {"type": "response", "id": request_id, "ok": False,
                            "error": {"code": "INTERNAL", "message": "操作没有完成，请重试。"}}
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        # A late error from a cancelled pre-login request cannot evict the new
        # guard now attached to this retained bridge after re-verification.
        if not self._closed and not context.cancel.is_set() and not response['ok']:
            self.request_failed.emit(response['error']['code'])
        if context.cancel.is_set() and not response["ok"]:
            response["error"] = {"code": "CANCELLED", "message": "已取消。"}
        self._send(response)

    def cancel_pending(self):
        """Cancel account work while retaining the bridge for re-verification."""
        with self._lock:
            for event in self._pending.values():
                event.set()

    def close(self):
        self._closed = True
        self.cancel_pending()
        with self._lock:
            self._pending.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)
