"""Own the local loopback runtime used by the desktop application."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import uvicorn
from PySide6.QtCore import QObject, QProcess, Signal

from .account_identity import AccountIdentity, AccountPreferenceStore, AccountProfile
from .account_vault import AccountVaultManager
from .local_migration import write_next_launch_data_root_pointer


_active_local_service: "LocalService | None" = None


class LocalService(QObject):
    ready = Signal(str)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self) -> None:
        super().__init__()
        global _active_local_service
        _active_local_service = self
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
        # The backend's canonical import root is ``app`` (its own modules use
        # that name for shared enterprise contracts).  Loading it once as
        # ``backend.app`` creates duplicate Enum/contract classes and makes a
        # Personal Local lifespan look like Enterprise mode.  Source launches
        # add the backend directory here; packaged launches already expose it.
        backend_root = Path(__file__).resolve().parents[1] / "backend"
        if backend_root.is_dir() and str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from app.main import app

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

    @property
    def handles_closed(self) -> bool:
        return (
            self._listener is None
            and (self.thread is None or not self.thread.is_alive())
            and (self.health_thread is None or not self.health_thread.is_alive())
        )

    def wait_until_stopped(self, timeout_seconds: float = 15.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while not self.handles_closed and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.handles_closed


def active_local_service() -> LocalService | None:
    return _active_local_service


def relaunch_current_desktop() -> bool:
    arguments = list(sys.argv)
    if not arguments:
        return False
    result = QProcess.startDetached(sys.executable, arguments)
    return bool(result[0] if isinstance(result, tuple) else result)


@dataclass(frozen=True, slots=True)
class AccountSwitchReceipt:
    target_root: Path
    pointer_path: Path
    events: tuple[str, ...]
    handles_closed: bool
    target_mounted_in_old_process: bool = False


class AccountSwitchCoordinator:
    """Close A completely and publish B only for the next Desktop process."""

    def __init__(
        self,
        *,
        vault_manager: AccountVaultManager,
        preferences: AccountPreferenceStore,
        stop_current_tasks: Callable[[], None],
        close_current_handles: Callable[[], None],
        clear_ui_and_runtime_cache: Callable[[], None],
        quit_current_window: Callable[[], None],
        wait_for_handles_closed: Callable[[], bool],
        relaunch_desktop: Callable[[], bool],
        pointer_writer: Callable[[Path, Path], Path] = (
            write_next_launch_data_root_pointer
        ),
    ) -> None:
        self.vault_manager = vault_manager
        self.preferences = preferences
        self.stop_current_tasks = stop_current_tasks
        self.close_current_handles = close_current_handles
        self.clear_ui_and_runtime_cache = clear_ui_and_runtime_cache
        self.quit_current_window = quit_current_window
        self.wait_for_handles_closed = wait_for_handles_closed
        self.relaunch_desktop = relaunch_desktop
        self.pointer_writer = pointer_writer

    def switch(
        self,
        identity: AccountIdentity,
        *,
        sync_cursor: str = "",
        model_selection: str = "",
    ) -> AccountSwitchReceipt:
        return self.switch_to_profile(
            identity.profile(),
            initial_event="authenticate_target_account",
            sync_cursor=sync_cursor,
            model_selection=model_selection,
        )

    def switch_to_profile(
        self,
        target_profile: AccountProfile,
        *,
        initial_event: str = "select_target_profile",
        sync_cursor: str = "",
        model_selection: str = "",
    ) -> AccountSwitchReceipt:
        events: list[str] = [initial_event]
        target_root = self.vault_manager.prepare_profile(target_profile)
        events.append("resolve_and_validate_target_vault")
        current = self.vault_manager.current
        if current is not None and os.path.normcase(str(current.root)) == os.path.normcase(
            str(target_root)
        ):
            raise ValueError("target account is already active")

        self.preferences.save_target(
            target_profile,
            sync_cursor=sync_cursor,
            model_selection=model_selection,
        )
        events.append("persist_nonsecret_account_preference_sync_cursor")
        pointer = self.pointer_writer(self.vault_manager.layout.root, target_root)
        events.append("publish_existing_mentorflow_next_data_root_v1_pointer")

        self.stop_current_tasks()
        events.append("stop_current_account_local_tasks")
        self.close_current_handles()
        events.append("close_current_sqlite_file_watcher_and_http_handles")
        self.clear_ui_and_runtime_cache()
        events.append("clear_current_account_ui_models_web_profiles_and_runtime_cache_handles")
        self.vault_manager.unmount()
        events.append("unmount_current_account_vault")
        self.quit_current_window()
        events.append("quit_current_desktop_window")
        handles_closed = bool(self.wait_for_handles_closed())
        events.append("wait_for_loopback_service_and_database_handles_to_close")
        if not handles_closed:
            raise RuntimeError("current account handles did not close")
        events.append("activate_target_identity_on_next_launch")
        if not self.relaunch_desktop():
            raise RuntimeError("Desktop relaunch could not be started")
        events.append("relaunch_desktop")
        events.append("open_target_account_vault_only_on_next_process")
        return AccountSwitchReceipt(
            target_root=target_root,
            pointer_path=pointer,
            events=tuple(events),
            handles_closed=True,
        )
