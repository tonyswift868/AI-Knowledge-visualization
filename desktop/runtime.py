"""Own the local runtime or connect to an authenticated cloud API."""

from __future__ import annotations

import socket
import threading
import time
import os
from urllib.parse import urlparse

import httpx
import uvicorn
from PySide6.QtCore import QObject, Signal


class LocalService(QObject):
    ready = Signal(str)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.port = self._free_port()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def start(self) -> None:
        from backend.app.main import app

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
        self.thread = threading.Thread(target=self.server.run, name="mentorflow-local-api", daemon=True)
        self.thread.start()
        threading.Thread(target=self._wait_until_ready, name="mentorflow-health", daemon=True).start()

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + 120
        last_error = "服务尚未响应"
        while time.monotonic() < deadline:
            if self.thread and not self.thread.is_alive():
                self.failed.emit("本地服务提前退出。")
                return
            try:
                response = httpx.get(f"{self.origin}/api/health", timeout=2)
                if response.status_code == 200:
                    self.ready.emit(self.origin)
                    return
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = str(exc)
            time.sleep(0.25)
        self.failed.emit(f"本地服务启动超时：{last_error}")

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=15)
        self.stopped.emit()


class RemoteService(QObject):
    """Connect the desktop client to a user-configured cloud API.

    The cloud endpoint is deliberately a public URL only; credentials remain
    in the server environment.  HTTPS is required for a non-loopback endpoint
    so a packaged client cannot silently downgrade account traffic to HTTP.
    """

    ready = Signal(str)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, origin: str | None = None) -> None:
        super().__init__()
        self.origin = (origin or os.getenv("MENTORFLOW_REMOTE_API_URL", "")).strip().rstrip("/")
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        parsed = urlparse(self.origin)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        ):
            self.failed.emit("云端 API 必须使用 HTTPS 地址。")
            return
        self.thread = threading.Thread(target=self._wait_until_ready, name="mentorflow-cloud-health", daemon=True)
        self.thread.start()

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + 120
        last_error = "云端服务尚未响应"
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{self.origin}/api/health", timeout=5)
                if response.status_code == 200:
                    self.ready.emit(self.origin)
                    return
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = str(exc)
            time.sleep(1)
        self.failed.emit(f"云端服务连接超时：{last_error}")

    def stop(self) -> None:
        self.stopped.emit()
