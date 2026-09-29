"""Qt adapter for the existing signed per-user updater; no second updater."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox


class DesktopUpdates(QObject):
    changed = Signal(object)
    _finished = Signal(object)

    def __init__(self, app, root, gate, *, core=None):
        super().__init__(app)
        if core is None:
            from refactor.desktop.delivery import per_user_update_core as core
        self.core, self.root, self.gate = core, Path(root), gate
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
        app.aboutToQuit.connect(self.close)
        app.commitDataRequest.connect(self._defer_session)
        core.attach_shutdown_guard(app)

    def start(self):
        self.gate.setEnabled(False)
        QTimer.singleShot(0, lambda: self.run("startup"))

    def snapshot(self):
        return {**self.state, "busy": self._busy, "available": True}

    def _publish(self, value):
        if not self._closed:
            self.state = dict(value)
            self.changed.emit(self.snapshot())

    def run(self, action, window=None):
        if self._closed or self._busy:
            return self.snapshot()
        if action not in {"startup", "check", "install"}:
            raise ValueError("unknown update operation")
        # HWND must be captured on the GUI thread, even though apply runs on
        # the worker. The existing native coordinator closes and relaunches us.
        target = window or QApplication.activeWindow() or self.gate
        hwnd = f"{int(target.winId()):016x}"
        self._busy = True
        self._cancel.clear()
        self._publish({"state": "DOWNLOADING" if action == "install" else "CHECKING",
                       "message": "正在下载并准备更新…" if action == "install" else "正在检查更新…"})

        def work():
            try:
                if action == "startup":
                    result = self.core.get_startup_update_state(self.root, full=False)
                elif action == "check":
                    result = self.core.check_latest(self.root)
                else:
                    self._session.require_running()
                    result = self.core.update_to_latest(
                        self.root, progress=lambda value: self._send({"progress": value}),
                        cancel=self._cancel.is_set, pid=os.getpid(), hwnd_hex=hwnd)
                    if result.get("coordinator_started"):
                        self._send({"progress": {**result, "message": "正在安装更新，完成后将自动重启…"}})
                        result = self._coordinator_result(result)
            except Exception as exc:
                result = {"state": "FAILED_RECOVERABLE", "message": "更新失败：" + str(exc)}
            self._send({"action": action, "result": result})

        self._pool.submit(work)
        return self.snapshot()

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
                if outcome.get("status") == "FAILED":
                    return {"state": "FAILED_RECOVERABLE", "message": "更新没有完成：" + str(outcome.get("detail", "请重试。"))}
                return {**result, **outcome}
            if time.monotonic() >= deadline:
                return {"state": "FAILED_RECOVERABLE", "message": "更新确认超时，请重新检查更新。"}
        return result

    def _defer_session(self, _manager=None):
        self._session.defer()
        self._cancel.set()

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
        if "progress" in value:
            self._publish({**self.state, **value["progress"]})
            return
        self._busy = False
        result = value["result"]
        self._publish(result)
        if value["action"] == "startup":
            self.gate.setEnabled(result.get("login_allowed", False))
            if result["state"] in {"OPTIONAL_UPDATE", "MANDATORY_UPDATE"}:
                self._show_offer(result)
            elif not result.get("login_allowed", False):
                QMessageBox.warning(self.gate, "MentorFlow 安装检查", result.get("message", "请使用可信安装包修复。"))

    def _show_offer(self, result):
        dialog = QMessageBox(QApplication.activeWindow() or self.gate)
        dialog.setWindowTitle("MentorFlow 更新")
        dialog.setText("发现新版本 " + str(result.get("latest", "")) + "。更新完成后会自动重启。")
        dialog.setInformativeText(str(result.get("message", "")))
        install = dialog.addButton("立即更新", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        dialog.buttonClicked.connect(lambda button: self.run("install") if button is install else None)
        dialog.finished.connect(lambda _: setattr(self, "_prompt", None))
        self._prompt = dialog
        dialog.open()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._cancel.set()
        self._session.close()
        self._pool.shutdown(wait=False, cancel_futures=True)
