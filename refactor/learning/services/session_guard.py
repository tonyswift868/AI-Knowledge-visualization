"""Nonblocking classroom session checks; the account shell still owns login.

Connect superseded() to the classroom's stop-and-save action and display
SUPERSEDED_MESSAGE. start()/stop() delimit a lesson; dispose() releases only
this guard's worker and timer. Other request owners may call note_error(code).
"""
from concurrent.futures import ThreadPoolExecutor
import json

import httpx
from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt

from refactor.learning.sync_identity import PlanError
from refactor.learning.sync_source import AuthenticatedPlanSource
from refactor.learning.sync_source import sync_auth_error

SESSION_PATH = "/api/enterprise/auth/native/session"
SUPERSEDED_MESSAGE = "账号已在另一台设备登录"


class SessionGuard(QObject):
    superseded = Signal()
    reauth_required = Signal()
    _finished = Signal(int, object)
    _noted = Signal(str)

    def __init__(self, context, parent=None, *, http_transport=None):
        super().__init__(parent)
        self._source = AuthenticatedPlanSource(
            account_id=context["account_id"], session_provider=context["session_provider"],
            api_base_url=context["api_base_url"], ca_bundle=context["ca_bundle"],
            http_transport=http_transport)
        self._active = self._closed = False
        self._notified = False
        self._epoch = self._failures = 0
        self._future = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mf-session-guard")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._launch)
        self._finished.connect(self._complete, Qt.ConnectionType.QueuedConnection)
        self._noted.connect(self._handle_error, Qt.ConnectionType.QueuedConnection)

    def start(self):
        if self._closed or self._active:
            return
        self._active = True
        self._epoch += 1
        self._failures = 0
        self._notified = False
        self._launch()

    def stop(self):
        self._active = False
        self._epoch += 1
        self._timer.stop()

    def dispose(self):
        if self._closed:
            return
        self.stop()
        self._closed = True
        self._source.close()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def note_error(self, code):
        """Thread-safe notification hook; never refresh or clear shared tokens."""
        if not self._closed and code in {"NATIVE_SESSION_SUPERSEDED", "REAUTH_REQUIRED",
                                        "ENTERPRISE_SESSION_REAUTH_REQUIRED"}:
            self._noted.emit(code)

    @Slot(str)
    def _handle_error(self, code):
        if self._closed or self._notified:
            return
        self._notified = True
        self.stop()
        if code == "NATIVE_SESSION_SUPERSEDED":
            self.superseded.emit()
        else:
            self.reauth_required.emit()

    def _probe(self):
        headers = {}
        try:
            scope, token = self._source._session()  # Fetch the shell's current token every time.
            self._source._scope = scope
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
            with httpx.Client(verify=self._source._ssl, timeout=10, follow_redirects=False,
                              transport=self._source._transport) as client:
                with client.stream("GET", self._source._base_url + SESSION_PATH, headers=headers) as response:
                    self._source.require_current()
                    if response.status_code == 401:
                        code = sync_auth_error(response, "登录已失效，请重新登录。").code
                        # This endpoint checks the five-minute access token,
                        # which may expire during sleep. Only explicit eviction
                        # stops the guard; shell refresh decides session revocation.
                        return code if code == 'NATIVE_SESSION_SUPERSEDED' else 'retry'
                    if response.status_code != 200:
                        return "retry"
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        raw.extend(chunk)
                        if len(raw) > 8192:
                            return "retry"
                    self._source.require_current()
                    return "active" if json.loads(raw) == {"state": "active"} else "retry"
        except PlanError as exc:
            # A temporarily missing shell session is not a server refusal.
            return exc.code if exc.code in {'CANCELLED', 'REAUTH_REQUIRED',
                'ENTERPRISE_SESSION_REAUTH_REQUIRED', 'NATIVE_SESSION_SUPERSEDED'} else 'retry'
        except (httpx.HTTPError, OSError, ValueError, TypeError):
            return "retry"
        finally:
            token = ""
            headers.clear()

    @Slot()
    def _launch(self):
        if self._closed or not self._active or self._future is not None:
            return
        self._timer.stop()
        epoch = self._epoch
        self._future = self._executor.submit(self._probe)
        def done(future):
            try:
                result = future.result()
            except Exception:
                result = "retry"
            try:
                self._finished.emit(epoch, result)
            except RuntimeError:
                pass  # Parent QObject was destroyed after dispose().
        self._future.add_done_callback(done)

    @Slot(int, object)
    def _complete(self, epoch, result):
        self._future = None
        if self._closed or not self._active:
            return
        if epoch != self._epoch:
            self._launch()  # A stopped request cannot affect a restarted lesson.
            return
        if result in {"NATIVE_SESSION_SUPERSEDED", "REAUTH_REQUIRED", "ENTERPRISE_SESSION_REAUTH_REQUIRED"}:
            self._handle_error(result)
            return
        if result == "active":
            self._failures = 0
            delay = 60
        else:
            self._failures = min(self._failures + 1, 4)
            delay = min(60 * 2 ** self._failures, 900)
        self._timer.start(delay * 1000)
