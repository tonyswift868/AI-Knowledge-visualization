from __future__ import annotations

import os
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtGui import QIcon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ui_review_mode_enabled() -> bool:
    """Return whether this process is the explicitly isolated UI review build."""
    return os.getenv("MENTORFLOW_UI_REVIEW_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _local_data_mode_enabled() -> bool:
    """Return whether the desktop should use the local data directory directly.

    A packaged build is local-first unless a remote API is explicitly
    configured.  The explicit environment switch is useful for the isolated
    UI-review copy and for smoke tests; ``0`` remains an opt-out for a
    deliberately authenticated/remote deployment.
    """
    value = os.getenv("MENTORFLOW_LOCAL_DATA_MODE", "").strip().lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    return bool(getattr(sys, "frozen", False)) and not os.getenv(
        "MENTORFLOW_REMOTE_API_URL", ""
    ).strip()


def _configure_frozen_user_storage() -> None:
    """Keep distributable builds writable and isolate every Windows profile."""
    if not getattr(sys, "frozen", False):
        return
    if _ui_review_mode_enabled():
        # UI review is intentionally a local, auth-free copy.  Fail closed if
        # the caller did not point it at a marked copy instead of risking the
        # canonical E:\\MentorFlowData database.
        configured_root = os.getenv("MENTORFLOW_USER_DATA_ROOT", "").strip()
        if not configured_root:
            raise RuntimeError("UI 评审模式必须指定 MENTORFLOW_USER_DATA_ROOT。")
        review_root = Path(configured_root).expanduser().resolve()
        canonical_root = Path("E:/MentorFlowData").resolve()
        marker = review_root / "ui-review-manifest.json"
        if review_root == canonical_root or not marker.is_file():
            raise RuntimeError(
                "UI 评审模式只能使用带 ui-review-manifest.json 的隔离数据目录。"
            )
    # A cloud-configured client must not initialize or write a local primary
    # database.  The cloud API is the source of truth in this mode.
    if os.getenv("MENTORFLOW_REMOTE_API_URL", "").strip():
        return
    storage_was_overridden = bool(
        os.getenv("MENTORFLOW_USER_DATA_ROOT")
        or os.getenv("MENTORFLOW_DB")
        or os.getenv("MENTORFLOW_ORM_DB")
    )
    local_app_data = Path(
        os.getenv("LOCALAPPDATA")
        or (Path.home() / "AppData" / "Local")
    )
    legacy_root = local_app_data / "MentorFlow"
    configured_root = os.getenv("MENTORFLOW_USER_DATA_ROOT")
    app_root = (
        Path(configured_root).expanduser()
        if configured_root
        else Path("E:/MentorFlowData")
        if Path("E:/").is_dir()
        else legacy_root
    )
    # Only migrate the historical C: tree when the user did not explicitly
    # choose a data root.  An explicitly empty root is a deliberate request
    # for a fresh local space and must never be populated from another profile.
    if not configured_root and app_root.resolve() != legacy_root.resolve() and legacy_root.is_dir():
        _migrate_legacy_user_storage(legacy_root, app_root)
    data_root = app_root / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    defaults = {
        "MENTORFLOW_DB": data_root / "app.db",
        "MENTORFLOW_ORM_DB": data_root / "mentorflow_v2.db",
        "MENTORFLOW_ACTIVE_UPLOAD_ROOT": data_root / "active_teaching" / "uploads",
        "MENTORFLOW_CLASSROOM_ASR_UPLOAD_ROOT": data_root / "classroom" / "uploads",
        "MENTORFLOW_DOCUMENT_CACHE_ROOT": data_root / "runtime-cache" / "documents",
        "MENTORFLOW_VOICE_CACHE_ROOT": data_root / "runtime-cache" / "voice",
        "MENTORFLOW_BACKUP_ROOT": app_root / "backups",
        "MENTORFLOW_SMS_CONFIG_PATH": app_root / "sms_gateway.json",
        "MENTORFLOW_PRESET_ROOT": app_root / "presets",
        "MENTORFLOW_PRESET_SETTINGS_FILE": app_root / "preset_settings.json",
    }
    for name, path in defaults.items():
        os.environ.setdefault(name, str(path))
    if not storage_was_overridden:
        _import_development_data_once(app_root, data_root)


def _copy_sqlite_if_missing(source: Path, destination: Path) -> None:
    """Copy a SQLite database transactionally and verify it before publishing."""
    if not source.is_file() or destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".migrating")
    temporary.unlink(missing_ok=True)
    source_connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    target_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(target_connection)
        if target_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"迁移数据库完整性检查失败：{source.name}")
    finally:
        target_connection.close()
        source_connection.close()
    temporary.replace(destination)


def _copy_missing_tree(source: Path, destination: Path) -> None:
    """Copy app-owned files without overwriting an already migrated target."""
    if not source.exists():
        return
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            _copy_missing_tree(child, destination / child.name)
        return
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _migrate_legacy_user_storage(legacy_root: Path, app_root: Path) -> None:
    """Move the previous C: user-data tree to the E: data root, safely."""
    app_root.mkdir(parents=True, exist_ok=True)
    legacy_data = legacy_root / "data"
    target_data = app_root / "data"
    for database_name in ("app.db", "mentorflow_v2.db"):
        _copy_sqlite_if_missing(
            legacy_data / database_name,
            target_data / database_name,
        )
    for relative in ("active_teaching", "classroom", "runtime-cache"):
        _copy_missing_tree(legacy_data / relative, target_data / relative)
    # These paths are app-level in the frozen build.  Accept both the old
    # app-level location and the earlier data/ location during migration.
    for relative in ("backups", "presets", "sms_gateway.json", "preset_settings.json"):
        for source in (legacy_root / relative, legacy_data / relative):
            if source.exists():
                _copy_missing_tree(source, app_root / relative)
    _copy_missing_tree(
        legacy_root / "development-data-import.json",
        app_root / "development-data-import.json",
    )
    marker = app_root / "storage-migration-from-localappdata.json"
    if not marker.exists():
        marker.write_text(
            json.dumps(
                {
                    "source": str(legacy_root),
                    "destination": str(app_root),
                    "migrated_at": datetime.now(timezone.utc).isoformat(),
                    "databases": ["app.db", "mentorflow_v2.db"],
                    "source_retained": True,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _import_development_data_once(app_root: Path, data_root: Path) -> None:
    """Preserve this checkout's live data without putting it in the release."""
    # A packaged application must never discover the checkout that happens to
    # contain its release folder and silently copy development databases into
    # a new user's local space.  Source-mode development keeps the historical
    # convenience import; frozen builds always initialize empty storage.
    if getattr(sys, "frozen", False):
        return
    destinations = (data_root / "app.db", data_root / "mentorflow_v2.db")
    if any(path.exists() for path in destinations):
        return
    executable = Path(sys.executable).resolve()
    project_root = next(
        (
            parent
            for parent in executable.parents
            if (parent / ".git").is_dir()
            and (parent / "data" / "app.db").is_file()
            and (parent / "data" / "mentorflow_v2.db").is_file()
        ),
        None,
    )
    if project_root is None:
        return
    data_root.mkdir(parents=True, exist_ok=True)
    sources = (
        project_root / "data" / "app.db",
        project_root / "data" / "mentorflow_v2.db",
    )
    temporary = tuple(path.with_suffix(path.suffix + ".importing") for path in destinations)
    try:
        for source, target in zip(sources, temporary):
            target.unlink(missing_ok=True)
            source_connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
            target_connection = sqlite3.connect(target)
            try:
                source_connection.backup(target_connection)
                if target_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError(f"导入数据库完整性检查失败：{source.name}")
            finally:
                target_connection.close()
                source_connection.close()
        for staged, destination in zip(temporary, destinations):
            staged.replace(destination)
    except Exception:
        for staged in temporary:
            staged.unlink(missing_ok=True)
        for destination in destinations:
            destination.unlink(missing_ok=True)
        raise
    source_uploads = project_root / "data" / "active_teaching"
    target_uploads = data_root / "active_teaching"
    if source_uploads.is_dir() and not target_uploads.exists():
        shutil.copytree(source_uploads, target_uploads)
    (app_root / "development-data-import.json").write_text(
        json.dumps(
            {
                "source": str(project_root),
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "databases": [path.name for path in destinations],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _load_cloud_endpoint_config() -> None:
    """Load a non-secret cloud API URL from the E: user-data root."""
    if (
        _ui_review_mode_enabled()
        or os.getenv("MENTORFLOW_LOCAL_DATA_MODE", "").strip().lower()
        in {"1", "true", "yes"}
        or os.getenv("MENTORFLOW_REMOTE_API_URL", "").strip()
    ):
        return
    configured = Path("E:/MentorFlowData/cloud_endpoint.json")
    if not configured.is_file():
        return
    try:
        payload = json.loads(configured.read_text(encoding="utf-8"))
        origin = str(payload.get("api_base_url") or "").strip().rstrip("/")
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("云端配置文件无法读取：E:\\MentorFlowData\\cloud_endpoint.json") from exc
    if not origin:
        raise RuntimeError("云端配置文件缺少 api_base_url：E:\\MentorFlowData\\cloud_endpoint.json")
    os.environ["MENTORFLOW_REMOTE_API_URL"] = origin


_load_cloud_endpoint_config()
_configure_frozen_user_storage()

# This flag is set before FastAPI is imported so the local data mode never
# opens an authentication window or returns 401 for the local desktop UI.
if _ui_review_mode_enabled() or _local_data_mode_enabled():
    os.environ.setdefault("MENTORFLOW_LOCAL_DATA_MODE", "1")
    os.environ["MENTORFLOW_AUTH_REQUIRED"] = "0"
elif getattr(sys, "frozen", False):
    # A packaged build with a remote endpoint is intentionally authenticated.
    os.environ["MENTORFLOW_AUTH_REQUIRED"] = "1"
else:
    os.environ.setdefault("MENTORFLOW_AUTH_REQUIRED", "1")
os.environ.setdefault("PYTHONUTF8", "1")

from .api import DesktopApi
from .auth_window import AuthWindow
from .main_window import MainWindow
from .runtime import LocalService, RemoteService
from .style import STYLE


class MentorFlowApplication:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("MentorFlow")
        self.app.setOrganizationName("MentorFlow")
        self.app.setWindowIcon(
            QIcon(str(PROJECT_ROOT / "frontend" / "public" / "teacher2d" / "tsubaki-live2d" / "icon.png"))
        )
        self.app.setStyleSheet(STYLE)
        remote_origin = os.getenv("MENTORFLOW_REMOTE_API_URL", "").strip()
        self.service = RemoteService(remote_origin) if remote_origin else LocalService()
        self.api: DesktopApi | None = None
        self.auth: AuthWindow | None = None
        self.main: MainWindow | None = None
        pixmap = QPixmap(520, 270)
        pixmap.fill(QColor("#244c40"))
        self.splash = QSplashScreen(pixmap)
        self.splash.showMessage(
            "MentorFlow\n正在连接云端学习服务…" if remote_origin else "MentorFlow\n正在准备本地数据库与学习服务…",
            Qt.AlignmentFlag.AlignCenter,
            QColor("white"),
        )
        self.service.ready.connect(self._service_ready)
        self.service.failed.connect(self._service_failed)
        self.app.aboutToQuit.connect(self._shutdown)
        smoke_seconds = float(os.getenv("MENTORFLOW_DESKTOP_SMOKE_SECONDS", "0") or 0)
        if smoke_seconds > 0:
            QTimer.singleShot(max(1, int(smoke_seconds * 1000)), self.app.quit)

    def run(self) -> int:
        self.splash.show()
        self.service.start()
        return self.app.exec()

    def _service_ready(self, origin: str) -> None:
        self.api = DesktopApi(origin)
        try:
            if _ui_review_mode_enabled():
                self._show_main(
                    {
                        "id": "ui-review",
                        "username": "ui-review",
                        # The review launcher is an internal data-root choice,
                        # not a user-facing identity.  Keep the same calm
                        # local-space wording as normal standalone mode.
                        "display_name": "本地学习空间",
                    }
                )
                return
            if _local_data_mode_enabled():
                # The local data directory is the identity in standalone mode:
                # existing data is loaded as-is, while a missing directory is
                # initialized by the local service.  Account/password/SMS
                # flows remain available only in the explicit remote mode.
                self._show_main(
                    {
                        "id": "local-data",
                        "username": "local-data",
                        "display_name": "本地学习空间",
                    }
                )
                return
            status = self.api.auth_status()
            if not status["registration_required"]:
                try:
                    user = self.api.me()["user"]
                except Exception:
                    user = None
                if user:
                    self._show_main(user)
                    return
            self.auth = AuthWindow(self.api, bool(status["registration_required"]))
            self.auth.authenticated.connect(self._show_main)
            self.auth.show()
            self.splash.finish(self.auth)
        except Exception as exc:
            self._service_failed(str(exc))

    def _show_main(self, user: dict) -> None:
        if not self.api:
            return
        self.main = MainWindow(self.api, user, self.service.origin)
        # Keep MentorFlow a normal desktop window.  In particular, it must
        # release focus and z-order when the user clicks another application.
        self.main.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.main.show()
        if self.auth:
            self.auth.close()
            self.auth = None
        self.splash.finish(self.main)

    def _service_failed(self, message: str) -> None:
        self.splash.close()
        QMessageBox.critical(None, "MentorFlow 无法启动", message)
        self.app.quit()

    def _shutdown(self) -> None:
        if self.api:
            self.api.close()
        self.service.stop()


def main() -> int:
    return MentorFlowApplication().run()


if __name__ == "__main__":
    raise SystemExit(main())
