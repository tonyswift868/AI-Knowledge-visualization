"""Qt adapter for the existing signed per-user updater; no second updater."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication


class DesktopUpdates(QObject):
    changed = Signal(object)
    _finished = Signal(object)

    def __init__(self, app, root, gate, *, core=None, login_handoff=None):
        super().__init__(app)
        if core is None:
            from refactor.desktop.delivery import per_user_update_core as core
        self.core, self.root, self.gate = core, Path(root), gate
        self._login_handoff = login_handoff
        self._handoff_armed = False
        self._cleanup_started = False
        self.state = {"state": "IDLE", "message": "尚未检查更新。"}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mf-app-update")
        self._busy = self._closed = False
        self._cancel = threading.Event()
        from refactor.desktop.delivery.update_session import UpdateSession
        from refactor.desktop.delivery.update_session import UnavailableSession
        try:
            self._session = UpdateSession()
        except OSError:
            self._session = UnavailableSession()
        self._finished.connect(self._receive, Qt.ConnectionType.QueuedConnection)
        self._prompt = None
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(60000)
        self._retry.timeout.connect(lambda: self.run("install", self._window))
        self._window = None
        self._deferred = False
        self._next_automatic = 0
        self._continuing_download = False
        self._login_blocked = True
        app.aboutToQuit.connect(self.close)
        app.commitDataRequest.connect(self._defer_session)
        core.attach_shutdown_guard(app)

    def start(self):
        getattr(self.gate, 'set_login_available', self.gate.setEnabled)(False)
        QTimer.singleShot(0, lambda: self.run("startup"))

    def snapshot(self):
        return {**self.state, "busy": self._busy, "available": True, "deferred": self._deferred}

    def _publish(self, value):
        if not self._closed:
            self.state = dict(value)
            self.changed.emit(self.snapshot())

    def run(self, action, window=None):
        if self._closed:
            return self.snapshot()
        if action == "defer":
            if self.state.get('mandatory') or self.state.get('state') == 'MANDATORY_UPDATE':
                return self.snapshot()
            self._deferred = True
            self.changed.emit(self.snapshot())
            return self.snapshot()
        if self._busy: return self.snapshot()
        if action == "automatic":
            if time.monotonic() < self._next_automatic: return self.snapshot()
            self._next_automatic = time.monotonic() + 86400
            action = "check"
        if action not in {"startup", "check", "install"}:
            raise ValueError("unknown update operation")
        # HWND must be captured on the GUI thread, even though apply runs on
        # the worker. The existing native coordinator closes and relaunches us.
        if window is not None:
            from shiboken6 import isValid
            if not isValid(window): window = None
        target = window or QApplication.activeWindow() or self.gate
        self._window = target
        self._retry.stop()
        hwnd = f"{int(target.winId()):016x}"
        self._busy = True
        self._cancel.clear()
        self._publish({**self.state, "state": "DOWNLOADING" if action == "install" else "CHECKING",
                       "message": "正在下载并准备更新…" if action == "install" else "正在检查更新…"})
        self._update_prompt()

        def work():
            try:
                if action == "startup":
                    cached = self.core.get_startup_update_state(self.root, full=False, check_feed=False,
                                                               cancel=self._cancel.is_set)
                    self._send({'cached': cached})
                    result = self.core.get_startup_update_state(self.root, full=False, check_feed=True,
                                                               cancel=self._cancel.is_set)
                elif action == "check":
                    result = self.core.check_latest(self.root, cancel=self._cancel.is_set)
                else:
                    self._session.require_running()
                    operation = self.core.repair_installation if self.state.get('shell') == 'repair' else self.core.update_to_latest
                    result = operation(
                        self.root, progress=self._install_progress,
                        cancel=self._cancel.is_set, pid=os.getpid(), hwnd_hex=hwnd)
                    if result.get("coordinator_started") and result.get('app_exit') == 'self':
                        # The coordinator has the signed staged update. New
                        # Apps exit themselves after this acknowledgement; the
                        # updater waits for the PID instead of sending WM_CLOSE.
                        self._send({"handoff": {**result,
                                                "message": "正在安装更新，完成后自动重启…"}})
                        return
                    if result.get('coordinator_started'):
                        # Legacy repair coordinators have no self-exit ACK.
                        # Never interpret process creation alone as permission
                        # to close the App and abandon an unacknowledged task.
                        result = self._coordinator_result(result)
                    if not result.get('coordinator_started') or result.get('state') in ('FAILED_RECOVERABLE', 'PENDING_INSTALL') or result.get('cancelled'):
                        self._clear_login_handoff()
            except Exception as exc:
                from .delivery.per_user_install import maintenance_log
                maintenance_log(self.root, 'in_app_update_deferred', error_type=type(exc).__name__, detail=str(exc))
                self._clear_login_handoff()
                from .delivery.per_user_update_core import PENDING
                pending = self.root / PENDING
                if action == 'install' and pending.exists():
                    result = {"state": "PENDING_INSTALL", "destructive_apply": False,
                              "message": "更新已下载好，关闭软件后下次打开时自动安装。"}
                else:
                    result = {"state": "FAILED_RECOVERABLE", "message": "更新暂时无法完成，请稍后重试。"}
            self._send({"action": action, "result": result})

        self._pool.submit(work)
        return self.snapshot()

    def _install_progress(self, value):
        # The existing core calls APPLYING synchronously immediately before
        # launching the installed coordinator. Never write during downloading.
        if value.get('state') == 'APPLYING' and self._login_handoff is not None:
            if self._cancel.is_set():
                raise RuntimeError('更新已取消。')
            self._handoff_armed = self._login_handoff.prepare_update_login()
            if self._cancel.is_set():
                self._clear_login_handoff()
                raise RuntimeError('更新已取消。')
        if value.get('state') == 'APPLYING':
            value = {**value, 'message': '正在安装更新，完成后自动重启…'}
        self._send({'progress': value})

    def _clear_login_handoff(self):
        if self._handoff_armed:
            self._login_handoff.clear_update_login()
            self._handoff_armed = False

    def _coordinator_result(self, result):
        """Observe a failed handoff instead of leaving the old UI in APPLYING.

        This result is only presentation; the existing coordinator still owns
        every signature check, process handshake, swap, rollback and relaunch.
        """
        path = Path(result["result_path"]).resolve()
        if path.parent != self.root.resolve():
            raise RuntimeError("更新结果路径无效。")
        deadline = time.monotonic() + 240
        while not self._cancel.wait(.2):
            if path.is_file() and path.stat().st_size < 65536:
                outcome = json.loads(path.read_text(encoding="utf-8"))
                if outcome.get("status") == "FAILED" or outcome.get('state') == 'PENDING_INSTALL':
                    from .delivery.per_user_update_core import PENDING
                    if (self.root / PENDING).is_file():
                        return {"state": "PENDING_INSTALL", "message": "更新已下载好，关闭软件后下次打开时自动安装。"}
                    return {"state": "FAILED_RECOVERABLE", "message": "更新暂时无法完成，请稍后重试。"}
                return {**result, **outcome}
            if time.monotonic() >= deadline:
                return {"state": "PENDING_INSTALL", "message": "更新已下载好，关闭软件后下次打开时自动安装。"}
        return result

    def _defer_session(self, _manager=None):
        self._session.defer()
        self._cancel.set()
        self._clear_login_handoff()

    def _send(self, value):
        if not self._closed:
            try:
                self._finished.emit(value)
            except RuntimeError:
                pass

    @Slot(object)
    def _receive(self, value):
        if self._closed:
            return
        if 'cached' in value:
            result = value['cached']
            self._login_blocked = not result.get('login_allowed', False)
            getattr(self.gate, 'set_login_available', self.gate.setEnabled)(not self._login_blocked)
            self._publish({**self.state, **result,
                           'mandatory': result.get('state') == 'MANDATORY_UPDATE'})
            if result.get('state') in ('HEALTHY', 'OPTIONAL_UPDATE', 'MANDATORY_UPDATE') and not self._cleanup_started:
                self._cleanup_started = True
                from .delivery.per_user_install import schedule_program_cleanup
                schedule_program_cleanup(self.root)
            if self._login_blocked: self._show_prompt()
            return
        if 'handoff' in value:
            self._busy = False
            self._publish({**self.state, **value['handoff'],
                           'state': 'APPLYING', 'message': '正在安装更新，完成后自动重启…'})
            QTimer.singleShot(120, self._finish_handoff)
            # If saving/closing the UI cannot finish, observe the coordinator's
            # bounded wait and display the deferred path instead of retrying.
            self._pool.submit(lambda: self._send({'action': 'install',
                'result': self._coordinator_result(value['handoff'])}))
            return
        if "progress" in value:
            self._publish({**self.state, **value["progress"]})
            self._update_prompt()
            return
        self._busy = False
        result = value["result"]
        if value['action'] == 'startup':
            result = {**result, 'mandatory': result.get('state') == 'MANDATORY_UPDATE'}
            if 'login_allowed' not in result:
                # A transport exception cannot erase a verified cached gate.
                result['login_allowed'] = self.state.get('login_allowed', False)
                result['mandatory'] = self.state.get('mandatory', False)
        self._publish({**self.state, **result})
        if value["action"] == "startup":
            if result.get('feed') == 'VERIFIED': self._next_automatic = time.monotonic() + 86400
            self._login_blocked = not result.get("login_allowed", False)
            getattr(self.gate, 'set_login_available', self.gate.setEnabled)(not self._login_blocked)
            if self._login_blocked or result.get('state') == 'OPTIONAL_UPDATE':
                self._show_prompt()
            if result.get('state') in ('HEALTHY', 'OPTIONAL_UPDATE', 'MANDATORY_UPDATE'):
                # Wait for the startup classifier before touching its runtime:
                # replacing while it hashes Updater can manufacture a failure.
                from .delivery.per_user_install import schedule_updater_refresh
                schedule_updater_refresh(self.root)
        elif value["action"] == "install":
            if result.get('state') in ('PENDING_INSTALL', 'FAILED_RECOVERABLE') or result.get('cancelled'):
                self._clear_login_handoff()
            # User already consented. Retain the .part files and try again later;
            # security failures and explicit cancellation never auto-retry.
            paused = result.get("state") == "DOWNLOAD_PAUSED_OR_INTERRUPTED" and not result.get("cancelled")
            retry_check = self._continuing_download and result.get("state") == "FAILED_RECOVERABLE" and result.get("code") == "UPDATE_CHECK_NETWORK_UNAVAILABLE"
            self._continuing_download = bool(paused or retry_check)
            if retry_check:
                done, total = self.state.get("downloaded_bytes", 0), self.state.get("expected_download_bytes", 0)
                self._publish({**self.state, "state": "DOWNLOAD_PAUSED_OR_INTERRUPTED",
                               "message": f"网络不稳，已下载 {done}/{total} 字节，稍后会自动继续。"})
            if self._continuing_download: self._retry.start()
        self._update_prompt()

    def _finish_handoff(self):
        """Close our UI only after the updater process has been launched."""
        if self._closed:
            return
        if self._prompt is not None:
            self._prompt.hide()
        shell = self._login_handoff
        try:
            if shell is not None and hasattr(shell, 'prepare_update_shutdown'):
                shell.prepare_update_shutdown()
            else:
                QApplication.closeAllWindows()
            QApplication.instance().quit()
        except Exception as error:
            from .delivery.per_user_install import maintenance_log
            maintenance_log(self.root, 'app_self_exit_deferred', detail=str(error))
            self._busy = False
            self._clear_login_handoff()
            self._publish({**self.state, 'state': 'PENDING_INSTALL',
                           'message': '更新已下载好，关闭软件后下次打开时自动安装。'})
            self._show_prompt()

    def _show_prompt(self):
        from .update_dialog import UpdateDialog
        target = self._prompt_parent()
        if target is not self.gate:
            # The main React window already presents update.status. Do not
            # place a second native modal over its existing UpdateNotice.
            if self._prompt is not None: self._prompt.hide()
            return
        if self._prompt is None:
            self._prompt = UpdateDialog(target)
            self._prompt.install_requested.connect(lambda: self.run('install', self._prompt_parent()))
            self._prompt.defer_requested.connect(lambda: self.run('defer'))
            self._prompt.close_requested.connect(self._close_for_pending)
        elif self._prompt.parentWidget() is not target:
            self._prompt.setParent(target, self._prompt.windowFlags())
        self._update_prompt()
        self._prompt.show(); self._prompt.raise_(); self._prompt.activateWindow()

    def _close_for_pending(self):
        """Let the user close the App so the signed pending update can apply."""
        if self._prompt is not None:
            self._prompt.hide()
        shell = self._login_handoff
        if shell is not None and hasattr(shell, 'prepare_update_shutdown'):
            shell.prepare_update_shutdown()
        QApplication.instance().quit()

    def _update_prompt(self):
        if self._prompt is not None:
            status = self.snapshot()
            status['hide_later'] = bool(status.get('mandatory'))
            if status.get('state') in ('OPTIONAL_UPDATE', 'UPDATE_AVAILABLE'):
                status['message'] = ('有新版本可用，可以现在更新，或点“稍后”继续登录。'
                    if self._prompt_parent() is self.gate else
                    '有新版本可用，可以现在更新，或稍后在 设置 → 关于 更新。')
            self._prompt.update_status(status)

    def _prompt_parent(self):
        from shiboken6 import isValid
        home = getattr(self._login_handoff, 'home', None)
        if home is not None and isValid(home) and home.isVisible(): return home
        if self.gate.isVisible(): return self.gate
        window = self._window
        if window is not None and isValid(window) and window.isVisible(): return window
        return self.gate

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._prompt is not None: self._prompt.hide()
        self._retry.stop()
        self._cancel.set()
        self._session.close()
        self._pool.shutdown(wait=False, cancel_futures=True)
