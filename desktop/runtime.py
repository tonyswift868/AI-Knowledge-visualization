"""Own the local loopback runtime used by the desktop application."""

from __future__ import annotations

import socket
import sys
import threading
import time

import httpx
import uvicorn
from PySide6.QtCore import QObject, Signal


class LocalService(QObject):
    ready = Signal(str)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self) -> None:
        super().__init__()
        # Keep the listening socket open from port selection through uvicorn
        # startup.  Binding an ephemeral port and then closing it creates a
        # race in which another local process can claim the port before the
        # embedded API starts.
        self._listener: socket.socket | None = self._bind_listener()
        self.port = int(self._listener.getsockname()[1])
        self.origin = f"http://127.0.0.1:{self.port}"
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.health_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._server_error: Exception | None = None

    @staticmethod
    def _bind_listener(port: int = 0) -> socket.socket:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Windows can otherwise allow a second bind under some socket
            # reuse configurations.  The option is unavailable on POSIX.
            if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(socket.SOMAXCONN)
            return listener
        except BaseException:
            listener.close()
            raise

    def start(self) -> None:
        from backend.app.main import app

        if self.thread and self.thread.is_alive():
            return
        if self._listener is None or self._listener.fileno() < 0:
            self._listener = self._bind_listener()
            self.port = int(self._listener.getsockname()[1])
            self.origin = f"http://127.0.0.1:{self.port}"

        listener = self._listener
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self.port,
                access_log=False,
                log_level="warning",
                # A windowed PyInstaller executable has no stderr stream.
                # Uvicorn's default dictConfig tries to bind one and crashes
                # before the loopback service starts.
                log_config=None,
            )
        )
        self._server_error = None
        self._stop_event.clear()
        self.thread = threading.Thread(
            target=self._run_server,
            args=(listener,),
            name="mentorflow-local-api",
            daemon=True,
        )
        self.thread.start()
        self.health_thread = threading.Thread(
            target=self._wait_until_ready,
            name="mentorflow-health",
            daemon=True,
        )
        self.health_thread.start()

    def _run_server(self, listener: socket.socket) -> None:
        try:
            # Passing the already-bound socket closes the selection-to-bind
            # window while remaining compatible with source and packaged
            # uvicorn launches (both expose Server.run(sockets=...)).
            assert self.server is not None
            self.server.run(sockets=[listener])
        except Exception as exc:  # surfaced by _wait_until_ready
            self._server_error = exc
        finally:
            self._close_listener(listener)

    def _close_listener(self, listener: socket.socket | None = None) -> None:
        target = listener if listener is not None else self._listener
        if target is None:
            return
        try:
            target.close()
        except OSError:
            pass
        if target is self._listener:
            self._listener = None

    @staticmethod
    def _is_expected_health(response: httpx.Response) -> bool:
        """Require the local API's documented identity, not any HTTP 200."""
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("status") == "ok"
            and payload.get("service") == "MentorFlow"
        )

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + 120
        last_error = "服务尚未响应"
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            if self.thread and not self.thread.is_alive():
                detail = f"：{self._server_error}" if self._server_error else "。"
                self._close_listener()
                self.failed.emit(f"本地服务提前退出{detail}")
                return
            try:
                response = httpx.get(f"{self.origin}/api/health", timeout=2)
                if response.status_code == 200 and self._is_expected_health(response):
                    self.ready.emit(self.origin)
                    return
                if response.status_code != 200:
                    last_error = f"HTTP {response.status_code}"
                else:
                    last_error = "健康响应身份不匹配"
            except httpx.HTTPError as exc:
                last_error = str(exc)
            except (TypeError, ValueError) as exc:
                last_error = str(exc)
            self._stop_event.wait(0.25)
        if self._stop_event.is_set():
            return
        self._close_listener()
        self.failed.emit(f"本地服务启动超时：{last_error}")

    def stop(self) -> None:
        self._stop_event.set()
        if self.server:
            self.server.should_exit = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=15)
        if self.health_thread and self.health_thread.is_alive():
            self.health_thread.join(timeout=3)
        self._close_listener()
        self.stopped.emit()
