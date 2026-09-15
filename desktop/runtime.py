"""Own the local loopback runtime used by the desktop application."""

from __future__ import annotations

from .test_device_profile import create_settings

import os
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uvicorn

import httpx
from PySide6.QtCore import QObject, QProcess, QSettings, Signal

from .account_identity import (
    AccountCredentialVault,
    AccountIdentity,
    AccountPreferenceStore,
    AccountProfile,
    ProfileMode,
)
from .account_vault import AccountVaultManager, VaultLayout
from .local_migration import write_next_launch_data_root_pointer


_active_local_service: "LocalService | None" = None
_restored_enterprise_profiles: set[tuple[str, str]] = set()
_enterprise_server_state: dict[tuple[str, str], dict[str, object]] = {}
_startup_restore_failure = ""


def _profile_key(profile: AccountProfile) -> tuple[str, str]:
    if (
        profile.mode is not ProfileMode.ENTERPRISE_ACCOUNT
        or profile.issuer_hash is None
        or profile.subject_hash is None
    ):
        raise ValueError("Enterprise Account profile is required")
    return profile.issuer_hash, profile.subject_hash


def _safe_server_state(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    context = value.get("context")
    account = value.get("account")
    safe_context: dict[str, object] = {}
    if isinstance(context, dict):
        for name in (
            "contract_version",
            "organization_id",
            "workspace_id",
            "roles",
            "service_permissions",
            "context_version",
        ):
            candidate = context.get(name)
            if isinstance(candidate, (str, int, list, tuple)):
                safe_context[name] = list(candidate) if isinstance(candidate, tuple) else candidate
    safe_account: dict[str, object] = {}
    if isinstance(account, dict):
        for name in ("display_name", "preferred_username", "email"):
            candidate = account.get(name)
            if candidate is None or isinstance(candidate, str):
                safe_account[name] = candidate
    return {
        "schema_version": str(value.get("schema_version") or ""),
        "authenticated": value.get("authenticated") is True,
        "account": safe_account,
        "context": safe_context,
    }


def record_enterprise_account_session(profile: AccountProfile, server_state: object) -> None:
    global _startup_restore_failure
    key = _profile_key(profile)
    _restored_enterprise_profiles.add(key)
    _enterprise_server_state.clear()
    _enterprise_server_state[key] = _safe_server_state(server_state)
    _startup_restore_failure = ""


def enterprise_account_server_state(profile: AccountProfile) -> dict[str, object]:
    if profile.mode is not ProfileMode.ENTERPRISE_ACCOUNT:
        return {}
    return dict(_enterprise_server_state.get(_profile_key(profile), {}))


def startup_restore_failure() -> str:
    return _startup_restore_failure


def clear_enterprise_account_session(profile: AccountProfile | None = None) -> None:
    global _startup_restore_failure
    from .rehydration_transport import invalidate_active
    invalidate_active(profile)
    if profile is None:
        _restored_enterprise_profiles.clear()
        _enterprise_server_state.clear()
    elif profile.mode is ProfileMode.ENTERPRISE_ACCOUNT:
        key = _profile_key(profile)
        _restored_enterprise_profiles.discard(key)
        _enterprise_server_state.pop(key, None)
    _startup_restore_failure = ""


def restore_enterprise_profile_for_startup(profile: AccountProfile) -> AccountProfile:
    """Validate Locker tokens and server binding before a saved Vault is mounted."""

    global _startup_restore_failure
    if profile.mode is not ProfileMode.ENTERPRISE_ACCOUNT:
        return profile
    from .enterprise_oidc import (
        DesktopOidcConfig,
        EnterpriseOidcError,
        restore_enterprise_account_from_env,
    )

    if not DesktopOidcConfig.is_configured():
        return profile
    key = _profile_key(profile)
    if key in _restored_enterprise_profiles:
        return profile
    credentials = AccountCredentialVault()
    try:
        result = restore_enterprise_account_from_env(profile, credentials)
        restored_profile = result.identity.profile()
        if (
            restored_profile.issuer_hash != profile.issuer_hash
            or restored_profile.subject_hash != profile.subject_hash
        ):
            raise EnterpriseOidcError(
                "ENTERPRISE_ACCOUNT_RESTORE_MISMATCH",
                "Restored session belongs to another Enterprise Account",
            )
    except Exception as exc:
        try:
            credentials.logout(profile)
        except Exception:
            pass
        _startup_restore_failure = "企业会话无法安全恢复，请重新登录。"
        raise ValueError(_startup_restore_failure) from exc
    record_enterprise_account_session(restored_profile, result.server_state)
    return restored_profile


def _bind_desktop_profile_data_root(
    profile: AccountProfile,
    *,
    source: dict[str, str] | os._Environ[str],
) -> Path:
    manager = AccountVaultManager(VaultLayout.from_env(source))
    account_root = manager.prepare_profile(profile)
    data_root = account_root / "data"
    bindings = {
        "MENTORFLOW_USER_DATA_ROOT": account_root,
        "MENTORFLOW_DB": data_root / "app.db",
        "MENTORFLOW_ORM_DB": data_root / "mentorflow_v2.db",
        "MENTORFLOW_ACTIVE_UPLOAD_ROOT": data_root / "active_teaching" / "uploads",
        "MENTORFLOW_CLASSROOM_ASR_UPLOAD_ROOT": data_root / "classroom" / "uploads",
        "MENTORFLOW_DOCUMENT_CACHE_ROOT": data_root / "runtime-cache" / "documents",
        "MENTORFLOW_VOICE_CACHE_ROOT": data_root / "runtime-cache" / "voice",
        "MENTORFLOW_BACKUP_ROOT": account_root / "backups",
        "MENTORFLOW_SMS_CONFIG_PATH": account_root / "sms_gateway.json",
        "MENTORFLOW_PRESET_ROOT": account_root / "presets",
        "MENTORFLOW_PRESET_SETTINGS_FILE": account_root / "preset_settings.json",
        "MENTORFLOW_VOICE_PACK_ROOT": account_root / "teacher-assets" / "voice-packs",
        "MENTORFLOW_PRIVATE_TEACHER_ASSET_ROOT": (
            account_root / "teacher-assets" / "tsubaki-live2d"
        ),
    }
    for name, path in bindings.items():
        source[name] = str(path)
    return account_root


def bind_desktop_profile_data_root(
    profile: AccountProfile,
    *,
    source: dict[str, str] | os._Environ[str] | None = None,
) -> Path:
    """Bind one already-authenticated profile without doing network restore."""

    return _bind_desktop_profile_data_root(
        profile,
        source=os.environ if source is None else source,
    )


def preflight_desktop_account_storage(
    *,
    settings: object | None = None,
    source: dict[str, str] | os._Environ[str] | None = None,
) -> AccountProfile:
    """Resolve and validate the profile before the local backend opens a DB."""

    global _startup_restore_failure
    env = os.environ if source is None else source
    from .enterprise_oidc import DesktopOidcConfig

    target_settings = settings or create_settings(QSettings, "MentorFlow", "MentorFlow")
    preferences = AccountPreferenceStore(target_settings)
    configured = DesktopOidcConfig.is_configured(env)
    layout = VaultLayout.from_env(env)
    selected_root_raw = str(env.get("MENTORFLOW_USER_DATA_ROOT") or "").strip()
    selected_account_root = False
    if selected_root_raw:
        try:
            selected_account_root = os.path.commonpath(
                (
                    os.path.normcase(str(layout.accounts_root)),
                    os.path.normcase(str(Path(selected_root_raw).absolute())),
                )
            ) == os.path.normcase(str(layout.accounts_root))
        except ValueError:
            selected_account_root = False
    stored_mode = str(
        target_settings.value(
            "account/profile_mode",
            ProfileMode.PERSONAL_LOCAL.value,
        )
        or ProfileMode.PERSONAL_LOCAL.value
    )
    if (
        not configured
        and stored_mode != ProfileMode.ENTERPRISE_ACCOUNT.value
        and not selected_account_root
    ):
        return AccountProfile.personal_local()
    try:
        profile = preferences.load()
        if profile.mode is ProfileMode.ENTERPRISE_ACCOUNT and not configured:
            try:
                AccountCredentialVault().logout(profile)
            except Exception:
                pass
            raise ValueError("Enterprise OIDC configuration is unavailable")
        if selected_account_root and not configured:
            raise ValueError("Enterprise account data root requires OIDC configuration")
        if profile.mode is ProfileMode.ENTERPRISE_ACCOUNT:
            preferences.save_target(
                profile,
                sync_cursor=str(
                    target_settings.value("account/sync_cursor", "") or ""
                ),
                model_selection=str(
                    target_settings.value("account/model_selection", "") or ""
                ),
            )
    except ValueError:
        keys = getattr(target_settings, "allKeys", lambda: [])()
        for key in tuple(keys):
            target_settings.remove(key)
        preferences.clear()
        profile = AccountProfile.personal_local()
        _startup_restore_failure = "企业会话无法安全恢复，请重新登录。"
    _bind_desktop_profile_data_root(profile, source=env)
    return profile


class LocalService(QObject):
    ready = Signal(str)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, *, resolved_profile: AccountProfile | None = None, product_transport=None) -> None:
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
        self._resolved_profile = resolved_profile
        self._process_stop = None
        self._product_transport = product_transport
        self._product_relay_thread = None

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
        # Validate a persisted Enterprise session before importing app.main;
        # importing it opens the SQLite/ORM files selected by the previous
        # next-launch pointer.  A failed restore is rebound to Personal Local
        # first, so the prior account database is never opened in this process.
        if self._resolved_profile is None:
            preflight_desktop_account_storage()
        else:
            bind_desktop_profile_data_root(self._resolved_profile)
        if self._resolved_profile is not None and self._resolved_profile.mode is ProfileMode.ENTERPRISE_ACCOUNT:
            self._start_account_process()
            return
        from .backend_imports import load_app
        app = load_app()
        import uvicorn

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

    def _start_account_process(self) -> None:
        import multiprocessing
        from .account_backend import run_account_backend

        if self.thread is not None and self.thread.is_alive():
            return
        if self._listener is None:
            raise RuntimeError("Account listener is closed")
        context = multiprocessing.get_context("spawn")
        self._process_stop = context.Event()
        self._stop_event.clear()
        self._server_error = None
        # spawn transfers the reserved socket handle; there is no free-port
        # selection/rebind race and no reuse of A's module-global engines.
        child_port = None
        product_scope = None
        if self._product_transport is not None:
            from .product_sync_bridge import ProductSyncRelay
            relay = ProductSyncRelay(self._product_transport)
            parent_port, child_port = context.Pipe()
            product_scope = relay.scope
            self._product_relay_thread = threading.Thread(
                target=relay.serve, args=(parent_port, self._stop_event),
                name='product-sync-relay', daemon=True)
            self._product_relay_thread.start()
        self.thread = context.Process(
            target=run_account_backend,
            args=(self._listener, os.environ["MENTORFLOW_USER_DATA_ROOT"], self._process_stop, child_port, product_scope),
            name="mentorflow-account-api", daemon=True,
        )
        try:
            self.thread.start()
        except BaseException:
            self._stop_event.set()
            if self._product_relay_thread is not None:
                self._product_relay_thread.join(timeout=1)
            self.thread = None
            raise
        finally:
            if child_port is not None:
                child_port.close()
        self.health_thread = threading.Thread(
            target=self._wait_until_ready, name="mentorflow-account-health", daemon=True,
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
        if self._process_stop is not None:
            self._process_stop.set()
        if self.server:
            self.server.should_exit = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=15)
        if self._process_stop is not None and self.thread and self.thread.is_alive():
            # Terminate only this owned account API process if graceful
            # shutdown timed out. B must not start until death is observed.
            self.thread.terminate()
            self.thread.join(timeout=3)
        if self.health_thread and self.health_thread.is_alive():
            self.health_thread.join(timeout=3)
        if self._product_relay_thread and self._product_relay_thread.is_alive():
            self._product_relay_thread.join(timeout=3)
        self._close_listener()
        self.stopped.emit()

    @property
    def handles_closed(self) -> bool:
        return (
            self._listener is None
            and (self.thread is None or not self.thread.is_alive())
            and (self.health_thread is None or not self.health_thread.is_alive())
            and (self._product_relay_thread is None or not self._product_relay_thread.is_alive())
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

        from .rehydration_transport import invalidate_active
        invalidate_active()
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
