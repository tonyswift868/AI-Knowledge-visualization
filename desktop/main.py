from __future__ import annotations

import os
import json
import re
import shutil
import sqlite3
import stat
import sys
import threading
import uuid
import zipfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QSplashScreen
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtGui import QIcon

from .local_migration import (
    MigrationError,
    _assert_within,
    _copy_file_no_follow,
    _ensure_directory,
    _file_identity,
    _hold_directory_chain,
    _is_reparse_info,
    _lexical_path,
    _open_verified_file,
    _same_file_identity,
    _snapshot_file,
    _validate_safe_settings,
    _write_new_text_no_follow,
    _validate_existing_entry,
    _validate_parent_chain,
    MAX_CONFIG_JSON_BYTES,
    MAX_NEXT_LAUNCH_POINTER_BYTES,
    SAFE_SETTINGS_MEMBER,
    import_migration_archive,
)


PROJECT_ROOT = _lexical_path(Path(__file__)).parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


_FRONTEND_BUNDLE_ENV = "MENTORFLOW_DESKTOP_FRONTEND_BUNDLE"
_FRONTEND_BUNDLE_MAX_FILES = 20_000
_FRONTEND_BUNDLE_MAX_BYTES = 1024 * 1024 * 1024
_FRONTEND_INDEX_MAX_BYTES = 1024 * 1024


def _frontend_dist_error(detail: str) -> RuntimeError:
    return RuntimeError(
        "DESKTOP_FRONTEND_ASSETS_MISSING: "
        "Desktop 前端资源不可用，创建计划、教师舞台和教师资源页无法启动。"
        f"{detail}请使用完整安装包或带前端资源包的 Personal Local 启动器。"
    )


def _validate_desktop_frontend_dist(dist_root: Path) -> Path:
    """Require a complete SPA shell before the loopback backend is imported."""

    index = dist_root / "index.html"
    if not index.is_file():
        raise _frontend_dist_error("缺少 frontend/dist/index.html；")
    if index.stat().st_size > _FRONTEND_INDEX_MAX_BYTES:
        raise _frontend_dist_error("frontend/dist/index.html 超出安全大小限制；")
    try:
        html = index.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _frontend_dist_error("frontend/dist/index.html 无法读取；") from exc
    references = {
        match.group(1).split("?", 1)[0].split("#", 1)[0]
        for match in re.finditer(r"(?:src|href)=[\"']/([^\"']+)[\"']", html)
        if match.group(1).startswith("assets/")
    }
    if not references:
        raise _frontend_dist_error("frontend/dist/index.html 未引用构建后的 assets；")
    missing = [value for value in sorted(references) if not (dist_root / value).is_file()]
    if missing:
        raise _frontend_dist_error(f"缺少构建资源 {missing[0]}；")
    for required in (
        dist_root / "teacher2d" / "catalog.json",
        dist_root / "vendor" / "live2d" / "live2dcubismcore.min.js",
    ):
        if not required.is_file():
            raise _frontend_dist_error(f"缺少教师舞台资源 {required.relative_to(dist_root)}；")
    return dist_root


def _prepare_desktop_frontend(project_root: Path = PROJECT_ROOT) -> Path:
    """Materialize an exact Desktop SPA bundle before FastAPI registers routes.

    Normal installers already contain ``frontend/dist``. Source-only Personal
    Local launchers may provide one immutable zip through the environment; the
    bundle is expanded into their disposable runtime source tree. No endpoint
    fallback is registered when assets are absent or incomplete.
    """

    project_root = _lexical_path(project_root)
    frontend_root = project_root / "frontend"
    dist_root = frontend_root / "dist"
    if dist_root.exists():
        return _validate_desktop_frontend_dist(dist_root)

    bundle_value = os.getenv(_FRONTEND_BUNDLE_ENV, "").strip()
    if not bundle_value:
        raise _frontend_dist_error(f"未设置 {_FRONTEND_BUNDLE_ENV}；")
    bundle = _lexical_path(Path(bundle_value))
    if not bundle.is_file():
        raise _frontend_dist_error(f"前端资源包不存在：{bundle}；")

    frontend_root.mkdir(parents=True, exist_ok=True)
    staging = frontend_root / f".dist-staging-{uuid.uuid4().hex}"
    staging.mkdir(exist_ok=False)
    file_count = 0
    total_bytes = 0
    try:
        with zipfile.ZipFile(bundle, "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative = PurePosixPath(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if (
                    relative.is_absolute()
                    or not relative.parts
                    or any(part in {"", ".", ".."} or ":" in part for part in relative.parts)
                    or mode == stat.S_IFLNK
                ):
                    raise _frontend_dist_error("前端资源包包含不安全路径；")
                file_count += 1
                total_bytes += max(0, int(info.file_size))
                if (
                    file_count > _FRONTEND_BUNDLE_MAX_FILES
                    or total_bytes > _FRONTEND_BUNDLE_MAX_BYTES
                ):
                    raise _frontend_dist_error("前端资源包超出安全大小限制；")
                destination = staging.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
        _validate_desktop_frontend_dist(staging)
        if dist_root.exists():
            raise _frontend_dist_error("frontend/dist 在准备期间被其他进程创建；")
        os.replace(staging, dist_root)
        return _validate_desktop_frontend_dist(dist_root)
    except (OSError, zipfile.BadZipFile) as exc:
        raise _frontend_dist_error("前端资源包无法安全展开；") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _bind_local_teacher_asset_vault(app_root: Path) -> dict[str, Path]:
    """Bind private Teacher assets to the selected local account root."""

    vault = _lexical_path(app_root) / "teacher-assets"
    bindings = {
        "MENTORFLOW_VOICE_PACK_ROOT": vault / "voice-packs",
        "MENTORFLOW_PRIVATE_TEACHER_ASSET_ROOT": vault / "tsubaki-live2d",
    }
    for name, path in bindings.items():
        # The selected Personal Local root is authoritative. In particular, a
        # stale environment value from another local account must never expose
        # that account's private Teacher assets in this process.
        os.environ[name] = str(path)
    return bindings


_NEXT_LAUNCH_TARGET: tuple[Path, tuple[int, int]] | None = None
_FIRST_START_IMPORTED_TARGET: Path | None = None
_FIRST_START_CHOICE_MADE = False


def _apply_next_launch_data_root_pointer(default_root: Path | None = None) -> None:
    """Honor an explicitly imported local-data directory on the next launch.

    The pointer is written only after a verified migration import.  Environment
    variables remain authoritative, so managed or test launches are never
    redirected behind the caller's back.
    """

    global _NEXT_LAUNCH_TARGET
    _NEXT_LAUNCH_TARGET = None
    if (
        os.getenv("MENTORFLOW_USER_DATA_ROOT", "").strip()
        or os.getenv("MENTORFLOW_DB", "").strip()
        or os.getenv("MENTORFLOW_ORM_DB", "").strip()
    ):
        return
    if default_root is None:
        default_root = (
            Path("E:/MentorFlowData")
            if Path("E:/").is_dir()
            else Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
            / "MentorFlow"
        )
    try:
        default_root = _lexical_path(default_root)
        _validate_parent_chain(default_root.parent, role="当前数据目录")
        root_info = _validate_existing_entry(default_root, role="当前数据目录", allow_missing=True)
        if root_info is None or not stat.S_ISDIR(root_info.st_mode):
            return
        pointer = default_root / ".mentorflow-next-data-root.json"
        with _hold_directory_chain(
            default_root,
            role="当前数据目录",
            expected_info=root_info,
        ):
            pointer_info = _validate_existing_entry(pointer, role="下一次启动指针", allow_missing=True)
            if pointer_info is None or not stat.S_ISREG(pointer_info.st_mode):
                return
            # Read the selected pointer from the identity-checked handle.  A
            # normal ``read_text`` would reopen the pathname after lstat and
            # allow a concurrent replacement to select a different root.
            with _open_verified_file(
                pointer,
                role="下一次启动指针",
                expected_info=pointer_info,
            ) as pointer_stream:
                raw_pointer = pointer_stream.read(MAX_NEXT_LAUNCH_POINTER_BYTES + 1)
                if len(raw_pointer) > MAX_NEXT_LAUNCH_POINTER_BYTES:
                    return
                payload = json.loads(raw_pointer.decode("utf-8"))
        if payload.get("format") != "mentorflow-next-data-root-v1":
            return
        requested_target = _lexical_path(Path(str(payload.get("data_root") or "")).expanduser())
        _validate_parent_chain(requested_target.parent, role="下一次启动数据目录")
        target_info = _validate_existing_entry(requested_target, role="下一次启动数据目录")
        if target_info is None or not stat.S_ISDIR(target_info.st_mode):
            return
        target = requested_target
        with _hold_directory_chain(
            target,
            role="下一次启动数据目录",
            expected_info=target_info,
        ):
            data_dir = target / "data"
            data_info = _validate_existing_entry(data_dir, role="下一次启动数据目录")
            if data_info is None or not stat.S_ISDIR(data_info.st_mode):
                return
            with _hold_directory_chain(
                data_dir,
                role="下一次启动数据目录",
                expected_info=data_info,
            ):
                for database in (data_dir / "app.db", data_dir / "mentorflow_v2.db"):
                    database_info = _validate_existing_entry(database, role="下一次启动数据库")
                    if database_info is None or not stat.S_ISREG(database_info.st_mode):
                        return
    except (MigrationError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return
    _NEXT_LAUNCH_TARGET = (target, _file_identity(target_info))
    os.environ["MENTORFLOW_USER_DATA_ROOT"] = str(target)


def _ui_review_mode_enabled() -> bool:
    """Return whether this process is the explicitly isolated UI review build."""
    return os.getenv("MENTORFLOW_UI_REVIEW_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _local_data_mode_enabled() -> bool:
    """Return whether the desktop uses the local data directory (always)."""
    return True


class StartupCancelled(RuntimeError):
    """Raised when a first-start choice is deliberately cancelled."""


def _is_fresh_local_root(path: Path) -> bool:
    """Return whether ``path`` is still safe to treat as a first start.

    A missing root and an empty root are fresh.  The one interrupted-start
    shape we can safely recognize is an empty ``data`` directory; any other
    existing entry makes the root an existing user space and suppresses the
    prompt so startup never guesses or mutates it.
    """

    lexical = _lexical_path(path)
    _validate_parent_chain(lexical.parent, role="首次启动数据目录")
    info = _validate_existing_entry(lexical, role="首次启动数据目录", allow_missing=True)
    if info is None:
        return True
    if not stat.S_ISDIR(info.st_mode):
        return False
    try:
        entries = list(lexical.iterdir())
    except OSError as exc:
        raise RuntimeError(f"无法检查首次启动数据目录：{lexical}（{exc}）") from exc
    if not entries:
        return True
    if len(entries) == 1 and entries[0].name.casefold() == "data":
        data_info = _validate_existing_entry(entries[0], role="首次启动数据目录")
        return bool(data_info and stat.S_ISDIR(data_info.st_mode) and not any(entries[0].iterdir()))
    return False


def _first_start_dialog() -> str:
    """Ask for a first-start policy and return ``defaults``, ``import`` or ``cancel``.

    The environment override is intentionally test-only plumbing for isolated
    smoke tests; a normal packaged launch always presents the two explicit
    choices and a safe cancel button.
    """

    override = os.getenv("MENTORFLOW_FIRST_START_ACTION", "").strip().casefold()
    if os.getenv("MENTORFLOW_DESKTOP_SMOKE_SECONDS", "").strip():
        # Package smoke is a noninteractive health check and must remain
        # deterministic on a fresh isolated root.
        return "defaults"
    if override in {"defaults", "default", "new"}:
        return "defaults"
    if override in {"import", "restore"}:
        return "import"
    if override in {"cancel", "quit"}:
        return "cancel"
    dialog = QMessageBox()
    dialog.setWindowTitle("开始使用 MentorFlow")
    dialog.setText("这是一个新的本地学习空间。请选择如何开始：")
    dialog.setInformativeText(
        "可以使用默认空白空间，也可以导入已有 MentorFlow 数据包。导入只会写入新目录，不会合并或覆盖已有数据。"
    )
    defaults = dialog.addButton("开始新的学习空间", QMessageBox.ButtonRole.AcceptRole)
    imported = dialog.addButton("导入已有 MentorFlow 数据包", QMessageBox.ButtonRole.ActionRole)
    dialog.addButton("取消并退出", QMessageBox.ButtonRole.RejectRole)
    dialog.exec()
    clicked = dialog.clickedButton()
    if clicked is defaults:
        return "defaults"
    if clicked is imported:
        return "import"
    return "cancel"


def _first_start_import_paths() -> tuple[Path, Path] | None:
    """Select a ZIP and a new/empty target, returning ``None`` on cancel."""

    configured_archive = os.getenv("MENTORFLOW_FIRST_START_ARCHIVE", "").strip()
    configured_target = os.getenv("MENTORFLOW_FIRST_START_TARGET", "").strip()
    if configured_archive or configured_target:
        if not configured_archive or not configured_target:
            raise MigrationError("首次启动导入需要同时指定迁移包和新的数据目录。")
        return Path(configured_archive).expanduser(), Path(configured_target).expanduser()
    archive_name, _ = QFileDialog.getOpenFileName(
        None,
        "选择 MentorFlow 迁移压缩包",
        str(Path.home()),
        "MentorFlow 迁移包 (*.zip)",
    )
    if not archive_name:
        return None
    target_name = QFileDialog.getExistingDirectory(
        None,
        "选择导入后的新本地数据目录（必须为空）",
        str(Path.home()),
    )
    if not target_name:
        return None
    return Path(archive_name), Path(target_name)


def _prepare_first_start_root(app_root: Path) -> tuple[Path, bool]:
    """Run the frozen-build first-start choice before any default writes."""

    global _FIRST_START_CHOICE_MADE, _FIRST_START_IMPORTED_TARGET
    if not getattr(sys, "frozen", False) or not _is_fresh_local_root(app_root):
        return app_root, False
    action = _first_start_dialog()
    if action == "cancel":
        raise StartupCancelled("首次启动已取消；没有创建或切换本地数据目录。")
    if action == "defaults":
        _FIRST_START_CHOICE_MADE = True
        return app_root, False
    selected = _first_start_import_paths()
    if selected is None:
        raise StartupCancelled("首次启动导入已取消；没有创建或切换本地数据目录。")
    archive_path, target_path = selected
    target_lexical = _lexical_path(target_path.expanduser())
    app_lexical = _lexical_path(app_root)
    if target_lexical == app_lexical:
        raise MigrationError("首次启动导入必须选择不同于当前默认目录的新目录。")
    result = import_migration_archive(archive_path, target_lexical)
    imported_root = _lexical_path(Path(result["target"]))
    os.environ["MENTORFLOW_USER_DATA_ROOT"] = str(imported_root)
    _FIRST_START_CHOICE_MADE = True
    _FIRST_START_IMPORTED_TARGET = imported_root
    return imported_root, True


def _restore_safe_qsettings(app_root: Path) -> None:
    """Apply the verified nonsecret settings snapshot after an import."""

    path = _lexical_path(app_root / SAFE_SETTINGS_MEMBER)
    info = _validate_existing_entry(path, role="安全设置", allow_missing=True)
    if info is None:
        return
    try:
        with _open_verified_file(path, role="安全设置", expected_info=info) as stream:
            raw_settings = stream.read(MAX_CONFIG_JSON_BYTES + 1)
            if len(raw_settings) > MAX_CONFIG_JSON_BYTES:
                raise MigrationError("导入的安全设置超过安全上限。")
            payload = json.loads(raw_settings.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, MigrationError) as exc:
        raise MigrationError("导入的安全设置无法读取。") from exc
    if not isinstance(payload, dict):
        raise MigrationError("导入的安全设置必须是对象。")
    settings = _validate_safe_settings(payload)
    target = QSettings("MentorFlow", "MentorFlow")
    # A machine may have a previous profile's Qt reference.  It is not part
    # of the imported package and must not silently attach to this new space.
    for sensitive_key in (
        "provider/credential_ref",
        "provider/session",
        "provider/provider_session_id",
    ):
        target.remove(sensitive_key)
    for key, value in settings.items():
        target.setValue(key, value)
    target.sync()


def _prepare_desktop_storage() -> None:
    """Resolve an imported handoff and configure local storage for this run."""

    _apply_next_launch_data_root_pointer()
    _configure_frozen_user_storage()
    imported = _FIRST_START_IMPORTED_TARGET
    if imported is not None:
        _restore_safe_qsettings(imported)
    elif _NEXT_LAUNCH_TARGET is not None:
        _restore_safe_qsettings(Path(os.environ["MENTORFLOW_USER_DATA_ROOT"]))


def _configure_frozen_user_storage() -> None:
    """Configure the explicitly selected local store before backend imports.

    Frozen builds default to the E: application data root.  Source-mode runs
    retain the repository database unless the caller explicitly supplies
    ``MENTORFLOW_USER_DATA_ROOT``; when it is supplied, silently continuing to
    use ``./data`` would show a different learning space from the one named by
    the user.
    """
    explicit_data_root = os.getenv("MENTORFLOW_USER_DATA_ROOT", "").strip()
    if not getattr(sys, "frozen", False) and not explicit_data_root:
        return
    if _ui_review_mode_enabled():
        # UI review is intentionally an isolated local copy.  Fail closed if
        # the caller did not point it at a marked copy instead of risking the
        # canonical E:\\MentorFlowData database.
        configured_root = os.getenv("MENTORFLOW_USER_DATA_ROOT", "").strip()
        if not configured_root:
            raise RuntimeError("UI 评审模式必须指定 MENTORFLOW_USER_DATA_ROOT。")
        review_requested = _lexical_path(Path(configured_root).expanduser())
        _validate_parent_chain(review_requested.parent, role="UI 评审目标")
        review_info = _validate_existing_entry(
            review_requested,
            role="UI 评审目标",
            allow_missing=True,
        )
        if review_info is None or not stat.S_ISDIR(review_info.st_mode):
            raise RuntimeError("UI 评审目标必须是目录。")
        review_root = review_requested
        canonical_root = _lexical_path(Path("E:/MentorFlowData"))
        marker = review_root / "ui-review-manifest.json"
        marker_info = _validate_existing_entry(marker, role="UI 评审标记", allow_missing=True)
        if (
            review_root == canonical_root
            or marker_info is None
            or not stat.S_ISREG(marker_info.st_mode)
        ):
            raise RuntimeError(
                "UI 评审模式只能使用带 ui-review-manifest.json 的隔离数据目录。"
            )
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
    configured_root = explicit_data_root
    app_root = (
        Path(configured_root).expanduser()
        if configured_root
        else Path("E:/MentorFlowData")
        if Path("E:/").is_dir()
        else legacy_root
    )
    app_root = _lexical_path(app_root)
    legacy_root = _lexical_path(legacy_root)
    _validate_parent_chain(app_root.parent, role="目标")
    app_info = _validate_existing_entry(app_root, role="目标", allow_missing=True)
    if app_info is not None and not stat.S_ISDIR(app_info.st_mode):
        raise RuntimeError(f"本地数据目录必须是目录：{app_root}")
    # A frozen first boot must make the defaults/import decision before this
    # function creates ``data`` or lets the backend initialize a database.
    if _NEXT_LAUNCH_TARGET is None:
        app_root, imported = _prepare_first_start_root(app_root)
        if _FIRST_START_CHOICE_MADE:
            # A deliberate first-start choice is authoritative.  In
            # particular, choosing a blank space must not trigger the legacy
            # C: profile migration as a hidden third option.
            storage_was_overridden = True
        if imported:
            configured_root = str(app_root)
            explicit_data_root = configured_root
            storage_was_overridden = True
            _validate_parent_chain(app_root.parent, role="目标")
            app_info = _validate_existing_entry(app_root, role="目标")
            if app_info is None or not stat.S_ISDIR(app_info.st_mode):
                raise RuntimeError(f"导入后的本地数据目录必须是目录：{app_root}")
    if _NEXT_LAUNCH_TARGET is not None:
        expected_root, expected_identity = _NEXT_LAUNCH_TARGET
        if app_root != expected_root or app_info is None or _file_identity(app_info) != expected_identity:
            raise RuntimeError("下一次启动数据目录在启动前发生变化。")
    _validate_parent_chain(legacy_root.parent, role="源")
    legacy_info = _validate_existing_entry(legacy_root, role="源", allow_missing=True)
    if legacy_info is not None and not stat.S_ISDIR(legacy_info.st_mode):
        raise RuntimeError(f"历史数据目录必须是目录：{legacy_root}")
    # Only migrate the historical C: tree when the user did not explicitly
    # choose a data root.  An explicitly empty root is a deliberate request
    # for a fresh local space and must never be populated from another profile.
    if (
        not configured_root
        and app_root != legacy_root
        and legacy_info is not None
    ):
        _migrate_legacy_user_storage(legacy_root, app_root)
    initial_app_info = app_info
    data_root = app_root / "data"
    initial_data_info = _validate_existing_entry(data_root, role="目标", allow_missing=True)
    data_root = _ensure_directory(data_root, role="目标")
    current_app_info = _validate_existing_entry(app_root, role="目标")
    assert current_app_info is not None
    if initial_app_info is not None and not _same_file_identity(current_app_info, initial_app_info):
        raise RuntimeError("本地数据目录在启动前发生变化。")
    current_data_info = _validate_existing_entry(data_root, role="目标")
    assert current_data_info is not None
    if initial_data_info is not None and not _same_file_identity(current_data_info, initial_data_info):
        raise RuntimeError("本地数据目录在启动前发生变化。")
    _bind_local_teacher_asset_vault(app_root)
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
    source = _lexical_path(source)
    destination = _lexical_path(destination)
    _validate_parent_chain(source.parent, role="源")
    _validate_parent_chain(destination.parent, role="目标")
    source_info = _validate_existing_entry(source, role="源", allow_missing=True)
    if source_info is None:
        return
    destination_info = _validate_existing_entry(destination, role="目标", allow_missing=True)
    if destination_info is not None:
        return
    if not stat.S_ISREG(source_info.st_mode):
        raise RuntimeError(f"迁移数据库不是普通文件：{source}")
    temporary = destination.with_name(destination.name + f".{uuid.uuid4().hex}.migrating")
    _ensure_directory(destination.parent, role="目标")
    _validate_parent_chain(destination.parent, role="目标")
    destination_parent_info = _validate_existing_entry(destination.parent, role="目标")
    assert destination_parent_info is not None
    try:
        with _hold_directory_chain(
            destination.parent,
            role="目标",
            expected_info=destination_parent_info,
        ):
            _snapshot_file(source, temporary, expected_source_info=source_info)
            _validate_existing_entry(temporary, role="目标暂存文件")
            if _validate_existing_entry(destination, role="目标", allow_missing=True) is not None:
                raise RuntimeError(f"迁移目标在过程中发生变化：{destination}")
            try:
                os.replace(temporary, destination)
            except OSError as exc:
                raise RuntimeError(f"迁移目标在发布过程中发生变化：{destination}（{exc}）") from exc
        _validate_existing_entry(destination, role="目标")
    except MigrationError as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        try:
            _validate_existing_entry(temporary, role="目标暂存文件", allow_missing=True)
            temporary.unlink(missing_ok=True)
        except (MigrationError, OSError):
            pass


def _is_reparse_point(info: os.stat_result) -> bool:
    """Return whether a stat result represents a link or Windows reparse point."""
    return _is_reparse_info(info)


def _preflight_copy_tree(source: Path, source_root: Path) -> None:
    """Validate an entire legacy source tree before creating any target entry."""

    source = _lexical_path(source)
    _assert_within(source, source_root, role="源")
    info = _validate_existing_entry(source, role="源", allow_missing=True)
    if info is None or not stat.S_ISDIR(info.st_mode):
        return
    try:
        children = sorted(source.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise RuntimeError(f"无法读取迁移源目录：{source}（{exc}）") from exc
    for child in children:
        _preflight_copy_tree(child, source_root)


def _copy_missing_tree(
    source: Path,
    destination: Path,
    *,
    _source_root: Path | None = None,
    _destination_root: Path | None = None,
    _created_paths: list[tuple[Path, tuple[int, int]]] | None = None,
    _expected_source_info: os.stat_result | None = None,
) -> None:
    """Copy plain app-owned files without following links or overwriting targets.

    Every source file is read from an identity-checked no-follow handle and
    every new target is opened with ``CREATE_NEW``.  Directory handles are held
    while enumerating/copying so a concurrent junction replacement fails closed
    instead of redirecting a read or write outside the selected roots.
    """
    source = _lexical_path(source)
    destination = _lexical_path(destination)
    top_level = _created_paths is None
    if _created_paths is None:
        _created_paths = []
    if _source_root is None:
        source_probe = _validate_existing_entry(source, role="源", allow_missing=True)
        _source_root = source if source_probe is None or stat.S_ISDIR(source_probe.st_mode) else source.parent
    if _destination_root is None:
        destination_probe = _validate_existing_entry(destination, role="目标", allow_missing=True)
        _destination_root = (
            destination
            if destination_probe is None or stat.S_ISDIR(destination_probe.st_mode)
            else destination.parent
        )
    try:
        _validate_parent_chain(source.parent, role="源")
        _validate_parent_chain(destination.parent, role="目标")
        _assert_within(source, _source_root, role="源")
        _assert_within(destination, _destination_root, role="目标")

        # A missing source is the normal case for optional legacy directories.
        source_info = _expected_source_info or _validate_existing_entry(
            source,
            role="源",
            allow_missing=True,
        )
        if source_info is None:
            return
        current_source_entry = _validate_existing_entry(source, role="源", allow_missing=True)
        if current_source_entry is None or not _same_file_identity(current_source_entry, source_info):
            raise RuntimeError(f"迁移源在打开前发生变化：{source}")
        destination_info = _validate_existing_entry(destination, role="目标", allow_missing=True)

        if stat.S_ISDIR(source_info.st_mode):
            with _hold_directory_chain(source, role="源"):
                current_source_info = _validate_existing_entry(source, role="源")
                if not _same_file_identity(current_source_info, source_info):
                    raise RuntimeError(f"迁移源在过程中发生变化：{source}")
                if destination_info is None:
                    _ensure_directory(destination.parent, role="目标")
                    destination_parent_info = _validate_existing_entry(
                        destination.parent,
                        role="目标",
                    )
                    assert destination_parent_info is not None
                    try:
                        with _hold_directory_chain(
                            destination.parent,
                            role="目标",
                            expected_info=destination_parent_info,
                        ):
                            destination.mkdir(exist_ok=False)
                    except FileExistsError:
                        destination_info = _validate_existing_entry(destination, role="目标", allow_missing=True)
                    except OSError as exc:
                        raise RuntimeError(f"无法创建迁移目标目录：{destination}（{exc}）") from exc
                    if destination_info is None:
                        created_info = _validate_existing_entry(destination, role="目标")
                        assert created_info is not None
                        _created_paths.append((destination, _file_identity(created_info)))
                        destination_info = created_info
                if destination_info is None or not stat.S_ISDIR(destination_info.st_mode):
                    raise RuntimeError(f"迁移目标类型冲突：{destination}")
                with _hold_directory_chain(destination, role="目标"):
                    current_destination_info = _validate_existing_entry(destination, role="目标")
                    if not _same_file_identity(current_destination_info, destination_info):
                        raise RuntimeError(f"迁移目标在过程中发生变化：{destination}")
                    try:
                        children = sorted(source.iterdir(), key=lambda path: path.name)
                    except OSError as exc:
                        raise RuntimeError(f"无法读取迁移源目录：{source}（{exc}）") from exc
                    for child in children:
                        child_info = _validate_existing_entry(child, role="源")
                        _copy_missing_tree(
                            child,
                            destination / child.name,
                            _source_root=_source_root,
                            _destination_root=_destination_root,
                            _created_paths=_created_paths,
                            _expected_source_info=child_info,
                        )
            return
        if destination_info is None:
            _ensure_directory(destination.parent, role="目标")
            _validate_parent_chain(destination.parent, role="目标")
            try:
                _copy_file_no_follow(
                    source,
                    destination,
                    expected_source_info=source_info,
                    role="源文件",
                )
            except MigrationError as exc:
                raise RuntimeError(str(exc)) from exc
            copied_info = _validate_existing_entry(destination, role="目标")
            assert copied_info is not None
            _created_paths.append((destination, _file_identity(copied_info)))
    except Exception:
        if top_level:
            for path, identity in reversed(_created_paths):
                try:
                    info = _validate_existing_entry(path, role="目标回滚", allow_missing=True)
                    if info is None or _file_identity(info) != identity:
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        path.rmdir()
                    else:
                        path.unlink(missing_ok=True)
                except (MigrationError, OSError):
                    pass
        raise


def _migrate_legacy_user_storage(legacy_root: Path, app_root: Path) -> None:
    """Move the previous C: user-data tree to the E: data root, safely."""
    legacy_root = _lexical_path(legacy_root)
    app_root = _lexical_path(app_root)
    _validate_parent_chain(legacy_root.parent, role="源")
    legacy_info = _validate_existing_entry(legacy_root, role="源", allow_missing=True)
    if legacy_info is None or not stat.S_ISDIR(legacy_info.st_mode):
        raise RuntimeError(f"迁移源目录不存在：{legacy_root}")
    legacy_data = legacy_root / "data"
    target_data = app_root / "data"
    source_tree_roots = [legacy_data / relative for relative in ("active_teaching", "classroom", "runtime-cache")]
    source_file_candidates = [
        source
        for relative in ("backups", "presets", "sms_gateway.json", "preset_settings.json")
        for source in (legacy_root / relative, legacy_data / relative)
    ]
    source_file_candidates.append(legacy_root / "development-data-import.json")
    for source in (*source_tree_roots, *source_file_candidates):
        source_info = _validate_existing_entry(source, role="源", allow_missing=True)
        if source_info is not None and stat.S_ISDIR(source_info.st_mode):
            _preflight_copy_tree(source, source)
    destination_candidates = [
        target_data / database_name for database_name in ("app.db", "mentorflow_v2.db")
    ] + [
        app_root / relative
        for relative in (
            "backups",
            "presets",
            "sms_gateway.json",
            "preset_settings.json",
            "development-data-import.json",
            "storage-migration-from-localappdata.json",
        )
    ] + [target_data / relative for relative in ("active_teaching", "classroom", "runtime-cache")]
    for destination in destination_candidates:
        _validate_existing_entry(destination, role="目标", allow_missing=True)
    app_root = _ensure_directory(app_root, role="目标")
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
            if _validate_existing_entry(source, role="源", allow_missing=True) is not None:
                _copy_missing_tree(source, app_root / relative)
    _copy_missing_tree(
        legacy_root / "development-data-import.json",
        app_root / "development-data-import.json",
    )
    marker = app_root / "storage-migration-from-localappdata.json"
    if _validate_existing_entry(marker, role="目标标记", allow_missing=True) is None:
        _validate_parent_chain(marker.parent, role="目标标记")
        try:
            _write_new_text_no_follow(
                marker,
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
                role="目标标记",
            )
        except MigrationError as exc:
            raise RuntimeError(str(exc)) from exc
        _validate_existing_entry(marker, role="目标标记")


def _is_source_project_candidate(parent: Path) -> bool:
    """Inspect a checkout candidate without following any link component."""

    git_info = _validate_existing_entry(parent / ".git", role="源", allow_missing=True)
    app_db = _validate_existing_entry(parent / "data" / "app.db", role="源", allow_missing=True)
    orm_db = _validate_existing_entry(
        parent / "data" / "mentorflow_v2.db",
        role="源",
        allow_missing=True,
    )
    return (
        git_info is not None
        and stat.S_ISDIR(git_info.st_mode)
        and app_db is not None
        and stat.S_ISREG(app_db.st_mode)
        and orm_db is not None
        and stat.S_ISREG(orm_db.st_mode)
    )


def _import_development_data_once(app_root: Path, data_root: Path) -> None:
    """Preserve this checkout's live data without putting it in the release."""
    # A packaged application must never discover the checkout that happens to
    # contain its release folder and silently copy development databases into
    # a new user's local space.  Source-mode development keeps the historical
    # convenience import; frozen builds always initialize empty storage.
    if getattr(sys, "frozen", False):
        return
    app_root = _lexical_path(app_root)
    data_root = _lexical_path(data_root)
    _validate_parent_chain(app_root.parent, role="目标")
    app_info = _validate_existing_entry(app_root, role="目标", allow_missing=True)
    if app_info is not None and not stat.S_ISDIR(app_info.st_mode):
        raise RuntimeError(f"迁移目标不是目录：{app_root}")
    _validate_parent_chain(data_root.parent, role="目标")
    data_info = _validate_existing_entry(data_root, role="目标", allow_missing=True)
    if data_info is not None and not stat.S_ISDIR(data_info.st_mode):
        raise RuntimeError(f"迁移目标不是目录：{data_root}")
    destinations = (data_root / "app.db", data_root / "mentorflow_v2.db")
    for path in destinations:
        if _validate_existing_entry(path, role="目标", allow_missing=True) is not None:
            return
    executable = _lexical_path(Path(sys.executable))
    _validate_parent_chain(executable.parent, role="源")
    _validate_existing_entry(executable, role="源可执行文件")
    project_root = next(
        (
            parent
            for parent in executable.parents
            if _is_source_project_candidate(parent)
        ),
        None,
    )
    if project_root is None:
        return
    source_uploads = project_root / "data" / "active_teaching"
    source_upload_info = _validate_existing_entry(source_uploads, role="源", allow_missing=True)
    if source_upload_info is not None:
        if not stat.S_ISDIR(source_upload_info.st_mode):
            raise RuntimeError(f"迁移源不是目录：{source_uploads}")
        # Validate all legacy uploads before publishing either database or a
        # marker, so a nested junction/hardlink cannot leave a partial target.
        _preflight_copy_tree(source_uploads, source_uploads)
    app_root = _ensure_directory(app_root, role="目标")
    data_root = _ensure_directory(data_root, role="目标")
    app_root_info = _validate_existing_entry(app_root, role="目标")
    data_root_info = _validate_existing_entry(data_root, role="目标")
    assert app_root_info is not None and data_root_info is not None
    sources = (
        project_root / "data" / "app.db",
        project_root / "data" / "mentorflow_v2.db",
    )
    temporary = tuple(
        path.with_name(path.name + f".{uuid.uuid4().hex}.importing") for path in destinations
    )
    published_databases: list[tuple[Path, tuple[int, int]]] = []
    try:
        for source, target in zip(sources, temporary):
            source = _lexical_path(source)
            target = _lexical_path(target)
            _validate_parent_chain(source.parent, role="源")
            source_info = _validate_existing_entry(source, role="源", allow_missing=False)
            if source_info is None or not stat.S_ISREG(source_info.st_mode):
                raise RuntimeError(f"迁移数据库不是普通文件：{source}")
            _validate_parent_chain(target.parent, role="目标")
            try:
                with _hold_directory_chain(
                    data_root,
                    role="目标",
                    expected_info=data_root_info,
                ):
                    _snapshot_file(source, target, expected_source_info=source_info)
            except MigrationError as exc:
                raise RuntimeError(str(exc)) from exc
            _validate_existing_entry(target, role="目标暂存文件")
        for staged, destination in zip(temporary, destinations):
            with _hold_directory_chain(
                destination.parent,
                role="目标",
                expected_info=data_root_info,
            ):
                _validate_parent_chain(destination.parent, role="目标")
                if _validate_existing_entry(destination, role="目标", allow_missing=True) is not None:
                    raise RuntimeError(f"迁移目标在过程中发生变化：{destination}")
                try:
                    os.replace(staged, destination)
                except OSError as exc:
                    raise RuntimeError(f"迁移目标在发布过程中发生变化：{destination}（{exc}）") from exc
            destination_info = _validate_existing_entry(destination, role="目标")
            assert destination_info is not None
            published_databases.append((destination, _file_identity(destination_info)))
    except Exception:
        for staged in temporary:
            try:
                _validate_existing_entry(staged, role="目标暂存文件", allow_missing=True)
                staged.unlink(missing_ok=True)
            except RuntimeError:
                pass
        for destination, identity in published_databases:
            try:
                _validate_existing_entry(destination, role="目标", allow_missing=True)
                current_info = _validate_existing_entry(destination, role="目标", allow_missing=True)
                if current_info is not None and _file_identity(current_info) == identity:
                    destination.unlink(missing_ok=True)
            except RuntimeError:
                pass
        raise
    try:
        target_uploads = data_root / "active_teaching"
        target_upload_info = _validate_existing_entry(target_uploads, role="目标", allow_missing=True)
        if source_upload_info is not None:
            if not stat.S_ISDIR(source_upload_info.st_mode):
                raise RuntimeError(f"迁移源不是目录：{source_uploads}")
            if target_upload_info is None:
                with _hold_directory_chain(
                    data_root,
                    role="目标",
                    expected_info=data_root_info,
                ):
                    _copy_missing_tree(source_uploads, target_uploads)
        marker = app_root / "development-data-import.json"
        if _validate_existing_entry(marker, role="目标标记", allow_missing=True) is not None:
            return
        _validate_parent_chain(marker.parent, role="目标标记")
        try:
            with _hold_directory_chain(
                app_root,
                role="目标标记",
                expected_info=app_root_info,
            ):
                _write_new_text_no_follow(
                    marker,
                    json.dumps(
                        {
                            "source": str(project_root),
                            "imported_at": datetime.now(timezone.utc).isoformat(),
                            "databases": [path.name for path in destinations],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    role="目标标记",
                )
        except MigrationError as exc:
            raise RuntimeError(str(exc)) from exc
        _validate_existing_entry(marker, role="目标标记")
    except Exception:
        for destination, identity in reversed(published_databases):
            try:
                with _hold_directory_chain(destination.parent, role="目标回滚"):
                    current_info = _validate_existing_entry(
                        destination,
                        role="目标回滚",
                        allow_missing=True,
                    )
                    if current_info is not None and _file_identity(current_info) == identity:
                        destination.unlink(missing_ok=True)
            except (MigrationError, OSError):
                pass
        raise


class MentorFlowApplication:
    def __init__(self, *, login_gate=None) -> None:
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setApplicationName("MentorFlow")
        self.app.setOrganizationName("MentorFlow")
        self._startup_cancelled = False
        self._startup_failed = False
        self._enterprise_login_required = False
        try:
            from .enterprise_oidc import DesktopOidcConfig

            self._enterprise_login_required = DesktopOidcConfig.is_configured()
            if not self._enterprise_login_required:
                _prepare_desktop_frontend()
                _prepare_desktop_storage()
        except StartupCancelled:
            self._startup_cancelled = True
            return
        except (MigrationError, OSError, RuntimeError, ValueError) as exc:
            self._startup_failed = True
            QMessageBox.critical(None, "MentorFlow 无法启动", str(exc))
            return
        # The desktop always starts the local single-user runtime.  Legacy
        # mode variables are intentionally ignored so they cannot resurrect
        # account auth.
        os.environ["MENTORFLOW_LOCAL_DATA_MODE"] = "1"
        os.environ.setdefault("PYTHONUTF8", "1")
        # Backend/UI modules are imported only after the data-root decision;
        # this is what keeps first-start import from racing default DB setup.
        global STYLE
        from .style import STYLE as _STYLE
        STYLE = _STYLE
        self.app.setWindowIcon(
            QIcon(str(PROJECT_ROOT / "frontend" / "public" / "teacher2d" / "tsubaki-live2d" / "icon.png"))
        )
        self.app.setStyleSheet(STYLE)
        self.service: LocalService | None = None
        self.api: DesktopApi | None = None
        self.main: MainWindow | None = None
        self.login_gate = login_gate
        self.migration_center = None
        self.bootstrap_result = None
        self._last_login_result = None
        self._login_generation = 0
        self._login_cancelled = threading.Event()
        self._auth_pane = None
        self._remembered_profile = None
        self._closing = False
        from .account_identity import AccountPreferenceStore
        from .login_session import LoginSession
        self._login_session = LoginSession(AccountPreferenceStore(QSettings("MentorFlow", "MentorFlow")))
        pixmap = QPixmap(520, 270)
        pixmap.fill(QColor("#244c40"))
        self.splash = QSplashScreen(pixmap)
        self.splash.showMessage(
            "MentorFlow\n正在准备本地数据库与学习服务…",
            Qt.AlignmentFlag.AlignCenter,
            QColor("white"),
        )
        self.app.aboutToQuit.connect(self._shutdown)
        smoke_seconds = float(os.getenv("MENTORFLOW_DESKTOP_SMOKE_SECONDS", "0") or 0)
        if smoke_seconds > 0:
            QTimer.singleShot(max(1, int(smoke_seconds * 1000)), self.app.quit)

    def run(self) -> int:
        if self._startup_cancelled:
            return 0
        if self._startup_failed:
            return 1
        if self._enterprise_login_required:
            self._show_login_gate()
        else:
            from .runtime import LocalService
            self.splash.show()
            self.service = LocalService()
            self.service.ready.connect(self._service_ready)
            self.service.failed.connect(self._service_failed)
            self.service.start()
        return self.app.exec()

    def _show_login_gate(self) -> None:
        from .startup_flow import LoginGateState, LoginGateWindow

        if self.login_gate is None:
            self.login_gate = LoginGateWindow()
        from .gui_update_runtime import attach_update_entry
        attach_update_entry(self.app, self.login_gate)
        self.login_gate.login_requested.connect(self._begin_interactive_login)
        self.login_gate.register_requested.connect(lambda: self._begin_account_action('register'))
        self.login_gate.forgot_password_requested.connect(lambda: self._begin_account_action('forgot_password'))
        self.login_gate.update_blocked.connect(self._update_login_blocked)
        self.login_gate.retry_requested.connect(self._retry_login_or_bootstrap)
        self.login_gate.background_succeeded.connect(self._background_succeeded)
        self.login_gate.background_failed.connect(self._background_failed)
        self.login_gate.cancel_requested.connect(self._cancel_login)
        self.login_gate.closed.connect(self._invalidate_login_jobs)
        self.login_gate.notice_label.setText(os.environ.pop("MENTORFLOW_LOGIN_NOTICE", ""))
        self.login_gate.show()
        try:
            profile, automatic = self._login_session.remembered()
        except ValueError:
            profile, automatic = None, False
        force_once = os.environ.pop("MENTORFLOW_DESKTOP_REQUIRE_INTERACTIVE_ONCE", "") == "1"
        self._remembered_profile = None if force_once else profile
        if self._remembered_profile is not None:
            self.login_gate.remember_box.setChecked(True)
            self.login_gate.auto_box.setChecked(automatic)
            self.login_gate.set_state(LoginGateState.LOGIN_READY)
            self.login_gate.state_title.setText(profile.safe_account_label)
            self.login_gate.state_detail.setText("已记住此账号，点击登录可免输密码继续。")
            self.login_gate.primary_button.setText("登录")
            if automatic:
                QTimer.singleShot(0, self._begin_remembered_login)
        else:
            self.login_gate.set_state(LoginGateState.LOGIN_READY)
            self.login_gate.username_field.setFocus()

    def _invalidate_login_jobs(self) -> None:
        self._login_generation += 1
        self._login_cancelled.set()
        self._login_cancelled = threading.Event()
        if self._auth_pane is not None:
            pane, self._auth_pane = self._auth_pane, None
            if self.login_gate is not None:
                self.login_gate.set_auth_content(None)
            pane.dispose()

    def _cancel_login(self) -> None:
        self._invalidate_login_jobs()
        self._remembered_profile = None
        self._last_login_result = None
        self.login_gate.remember_box.setChecked(False)
        self.login_gate.auto_box.setChecked(False)
        self.login_gate.clear_password()
        # Cancel automatic login without deleting any account's credentials.
        self._login_session.preferences.settings.setValue("account/auto_login", "0")
        self._login_session.preferences.settings.sync()
        from .startup_flow import LoginGateState
        self.login_gate.set_state(LoginGateState.LOGIN_READY)

    def _begin_remembered_login(self) -> None:
        from .startup_flow import LoginGateState
        if self._closing or self._remembered_profile is None or self.login_gate.login_is_blocked:
            return
        self._invalidate_login_jobs()
        generation, cancelled = self._login_generation, self._login_cancelled
        profile, gate = self._remembered_profile, self.login_gate
        gate.set_state(LoginGateState.AUTHENTICATING, detail="正在登录…")
        gate.state_detail.setText("正在安全恢复已记住的会话…")

        def restore() -> None:
            try:
                result = self._login_session.restore(profile, cancelled)
                gate.background_succeeded.emit(f"{generation}:restore", result)
            except Exception as exc:
                code = getattr(exc, "code", "")
                reauthenticate = code in {"ENTERPRISE_SESSION_REAUTH_REQUIRED", "ENTERPRISE_OIDC_TOKEN_INVALID",
                                          "ENTERPRISE_ACCOUNT_RESTORE_MISMATCH", "ENTERPRISE_ACCOUNT_ISSUER_CHANGED",
                                          "CREDENTIAL_NOT_FOUND"}
                job = "restore_expired" if reauthenticate else "restore"
                gate.background_failed.emit(f"{generation}:{job}", "登录已失效，请重新登录。" if reauthenticate else "暂时无法恢复登录，请检查网络或账号权限后重试。")
        threading.Thread(target=restore, name="mentorflow-session-restore", daemon=True).start()

    def _update_login_blocked(self, blocked):
        if blocked:
            self._invalidate_login_jobs()
            self._last_login_result = None
            self.login_gate.clear_password()
            if self.main is not None:
                self._main_suspended_for_update = True
                self.main.setEnabled(False)
                self.main.hide()
                self.login_gate.show()
                self.login_gate.update_controller.bind_window(self.login_gate)
            from .startup_flow import LoginGateState
            self.login_gate.set_state(LoginGateState.LOGIN_READY)
        elif getattr(self, '_main_suspended_for_update', False) and self.main is not None:
            self._main_suspended_for_update = False
            self.main.setEnabled(True)
            self.main.show()
            self.login_gate.hide()
            self.login_gate.update_controller.bind_window(self.main)

    def _begin_account_action(self, intent):
        if self.login_gate is None or self._closing or self.login_gate.login_is_blocked or self.login_gate.account_capabilities.get(intent) is not True:
            return
        if intent != 'forgot_password':
            self.login_gate.notice_label.setText('当前账号服务暂未开放此功能。')
            return
        from .enterprise_oidc import DesktopOidcConfig
        from .in_app_oidc import InAppOidcLoginDialog
        from .reset_completion import COMPLETED_CODE
        from .startup_flow import LoginGateState
        self._invalidate_login_jobs()
        generation = self._login_generation
        self._remembered_profile = None
        self.login_gate.clear_password()
        self.login_gate.set_state(LoginGateState.AUTHENTICATING)
        try:
            pane = InAppOidcLoginDialog(DesktopOidcConfig.from_env(), parent=self.login_gate, embedded=True,
                intent=intent, capabilities=self.login_gate.account_capabilities)
            self._auth_pane = pane
            # Login results and provider page messages cannot attest reset success.
            pane.authenticated.connect(lambda _: self._return_from_password_reset(generation))
            pane.authentication_failed.connect(lambda code, _message:
                self._return_from_password_reset(generation, completed=code == COMPLETED_CODE))
            self.login_gate.set_auth_content(pane)
            pane.start_embedded()
        except Exception:
            self._return_from_password_reset(generation)

    def _return_from_password_reset(self, generation, *, completed=False):
        from .reset_completion import COMPLETED_MESSAGE
        from .startup_flow import LoginGateState
        if generation != self._login_generation or self._closing or self.login_gate is None:
            return
        self._invalidate_login_jobs()
        self._remembered_profile = None
        self._last_login_result = None
        self.login_gate.clear_password()
        self.login_gate.set_state(LoginGateState.LOGIN_READY, detail=COMPLETED_MESSAGE if completed
            else '尚未确认密码重置完成。请重新发起找回，或使用当前密码登录。')

    def _begin_interactive_login(self, *, intent='login') -> None:
        from .native_auth import login_native_account_from_env, MESSAGES
        from .startup_flow import LoginGateState

        if self.login_gate is None or self._closing or self.login_gate.login_is_blocked:
            return
        if self._remembered_profile is not None and not (
                self.login_gate.username_field.text() or self.login_gate.password_field.text()):
            self._begin_remembered_login()
            return
        if intent != 'login':
            return
        self._remembered_profile = None
        username, password = self.login_gate.take_credentials()
        if not username or not password:
            self.login_gate.set_state(LoginGateState.AUTH_FAILED, detail='请输入账号和密码。')
            return
        self._invalidate_login_jobs()
        generation, gate = self._login_generation, self.login_gate
        self.login_gate.set_state(LoginGateState.AUTHENTICATING)

        def authenticate():
            nonlocal password
            try:
                result = login_native_account_from_env(username, password, on_authenticated=lambda:
                    gate.background_succeeded.emit(f'{generation}:credentials_validated', None))
                gate.background_succeeded.emit(f'{generation}:authenticate', result)
            except Exception as exc:
                # Never interpolate exception/request objects into UI or receipts.
                message = MESSAGES.get(getattr(exc, 'code', ''), '登录暂时无法完成，请检查网络或账号服务后重试。')
                gate.background_failed.emit(f'{generation}:authenticate', message)
            finally:
                password = None
        threading.Thread(target=authenticate, name='mentorflow-native-login', daemon=True).start()

    def _accept_login_result(self, result) -> None:
        from .enterprise_oidc import EnterpriseOidcLoginResult
        from .runtime import record_enterprise_account_session
        from .startup_flow import LoginGateState

        if self.login_gate is not None and self.login_gate.login_is_blocked:
            return
        if self.login_gate is None or not isinstance(result, EnterpriseOidcLoginResult):
            if self.login_gate is not None:
                self.login_gate.set_state(
                    LoginGateState.AUTH_FAILED,
                    detail="登录结果无法验证，请重新登录。",
                )
            return
        try:
            self._login_session.accept(result, remember=self.login_gate.remember_box.isChecked(),
                                       automatic=self.login_gate.auto_box.isChecked())
            record_enterprise_account_session(
                result.identity.profile(),
                result.server_state,
            )
        except Exception:
            self.login_gate.set_state(
                LoginGateState.AUTH_FAILED,
                detail="登录凭据无法安全保存，请检查 Windows 凭据服务后重试。",
            )
            return
        self._last_login_result = result
        self._invalidate_login_jobs()
        self._start_account_bootstrap()

    def _start_account_bootstrap(self) -> None:
        from .startup_flow import LoginGateState, bootstrap_enterprise_account

        if self.login_gate is None or self._last_login_result is None or self.login_gate.login_is_blocked:
            return
        self.login_gate.set_state(
            LoginGateState.AUTH_SUCCESS_BOOTSTRAPPING,
            detail="正在恢复你的学习空间…",
        )
        login_result = self._last_login_result
        generation, gate = self._login_generation, self.login_gate

        def bootstrap() -> None:
            try:
                result = bootstrap_enterprise_account(login_result)
                gate.background_succeeded.emit(f"{generation}:bootstrap", result)
            except Exception as exc:
                message = str(getattr(exc, "user_message", "") or "")
                gate.background_failed.emit(
                    f"{generation}:bootstrap",
                    message or "账号数据暂时无法恢复，请检查网络和磁盘后重试。",
                )

        threading.Thread(
            target=bootstrap,
            name="mentorflow-account-bootstrap",
            daemon=True,
        ).start()

    def _retry_login_or_bootstrap(self) -> None:
        if self._last_login_result is not None:
            self._start_account_bootstrap()
        else:
            self._begin_interactive_login()

    def _background_succeeded(self, job: str, result: object) -> None:
        if ":" in job:
            generation, job = job.split(":", 1)
            if int(generation) != self._login_generation or self._closing:
                if job == "bootstrap" and hasattr(result, "close"):
                    result.close()
                return
        if job == 'credentials_validated':
            if self.login_gate is not None and not self.login_gate.login_is_blocked:
                from .startup_flow import LoginGateState
                self.login_gate.set_state(LoginGateState.AUTH_SUCCESS_BOOTSTRAPPING)
            return
        if job in {"restore", "authenticate"}:
            self._accept_login_result(result)
            return
        if job == "logout_remote":
            self._finish_return_to_login_gate(result)
            return
        if job != "bootstrap":
            return
        if self.login_gate is not None and self.login_gate.login_is_blocked:
            if hasattr(result, 'close'):
                result.close()
            return
        from .startup_flow import (
            AccountBootstrapResult,
            DeviceBootstrapState,
        )

        if not isinstance(result, AccountBootstrapResult):
            self._background_failed("bootstrap", "账号数据结果无法验证，请重试。")
            return
        self.bootstrap_result = result
        from .runtime import bind_desktop_profile_data_root
        bind_desktop_profile_data_root(result.profile)
        if result.device_state is DeviceBootstrapState.NEW_DEVICE_OR_EMPTY_LOCAL_STATE:
            self._show_device_migration(before_main=True)
        else:
            self._start_enterprise_local_service()

    def _background_failed(self, job: str, message: str) -> None:
        from .startup_flow import LoginGateState

        if ":" in job:
            generation, job = job.split(":", 1)
            if int(generation) != self._login_generation or self._closing:
                return

        if self.login_gate is None:
            return
        if job == "logout":
            self._last_login_result = None
            self.login_gate.set_state(LoginGateState.AUTH_FAILED, detail=message)
            self.login_gate.primary_button.hide()
            self.login_gate.cancel_button.hide()
            return
        if job == "restore_expired":
            self._login_session.preferences.disable_restore()
            self._remembered_profile = None
            self._last_login_result = None
            self.login_gate.remember_box.setChecked(False)
            self.login_gate.clear_password()
            self.login_gate.set_state(LoginGateState.LOGIN_READY, detail=message)
            self.login_gate.notice_label.setText('登录已失效，请重新登录。')
            return
        if job in {"restore", "authenticate"}:
            self._invalidate_login_jobs()
            self._last_login_result = None
            self.login_gate.set_state(
                LoginGateState.AUTH_FAILED,
                detail=message,
                retry_bootstrap=False,
            )
            return
        self.login_gate.set_state(
            LoginGateState.AUTH_FAILED,
            detail=message,
            retry_bootstrap=True,
        )

    def _show_device_migration(self, *, before_main: bool) -> None:
        from .startup_flow import DeviceMigrationCenter

        if self.bootstrap_result is None:
            return
        parent = None if before_main else self.main
        self.migration_center = DeviceMigrationCenter(
            self.bootstrap_result,
            parent=parent,
            before_main=before_main,
        )
        if self.main is not None:
            self.migration_center.snapshot_changed.connect(
                self.main.set_device_recovery_snapshot
            )
        if before_main:
            if self.login_gate is not None:
                self.login_gate.hide()
            self.migration_center.accepted.connect(
                self._resume_after_device_migration
            )
            self.migration_center.rejected.connect(self.app.quit)
            self.migration_center.show()
        else:
            self.migration_center.show()

    def _resume_after_device_migration(self) -> None:
        from .startup_flow import LoginGateState

        if self.login_gate is not None:
            self.login_gate.set_state(
                LoginGateState.AUTH_SUCCESS_BOOTSTRAPPING,
                detail="正在打开你的学习空间…",
            )
            self.login_gate.show()
        self._start_enterprise_local_service()

    def _start_enterprise_local_service(self) -> None:
        from .startup_flow import LoginGateState

        if self.bootstrap_result is None or self.login_gate is not None and self.login_gate.login_is_blocked:
            return
        if self.service is not None:
            return
        if self.login_gate is not None:
            self.login_gate.set_state(
                LoginGateState.AUTH_SUCCESS_BOOTSTRAPPING,
                detail="正在打开你的学习空间…",
            )
        from .runtime import LocalService
        self.service = LocalService(resolved_profile=self.bootstrap_result.profile)
        service = self.service
        generation = self._login_generation
        service.ready.connect(lambda origin: self._service_ready(origin)
                              if service is self.service and generation == self._login_generation and not self._closing else None)
        service.failed.connect(lambda message: self._service_failed(message)
                               if service is self.service and generation == self._login_generation and not self._closing else None)

        def start_service() -> None:
            try:
                _prepare_desktop_frontend()
                service.start()
            except Exception:
                service.failed.emit("本机学习服务暂时无法打开，请重新启动后重试。")

        threading.Thread(
            target=start_service,
            name="mentorflow-local-service-startup",
            daemon=True,
        ).start()

    def _service_ready(self, origin: str) -> None:
        from .api import DesktopApi
        self.api = DesktopApi(origin)
        try:
            if self.bootstrap_result is not None:
                self._show_main(self.bootstrap_result.main_user)
            else:
                self._show_main(
                    {
                        "id": "local-data",
                        "username": "local-data",
                        "display_name": "本地学习空间",
                    }
                )
        except Exception as exc:
            self._service_failed(str(exc))

    def _show_main(self, user: dict) -> None:
        updater = getattr(self.app, '_mentorflow_updater', None)
        if (not self.api or self.login_gate is not None and self.login_gate.login_is_blocked
                or updater is not None and updater.login_blocked):
            return
        if self._enterprise_login_required and self.bootstrap_result is None:
            return
        # Business pages are not needed to authenticate. Keep their imports
        # behind the verified login/bootstrap gate instead of delaying its form.
        from .main_window import MainWindow

        if self.bootstrap_result is None:
            self.main = MainWindow(self.api, user, self.service.origin)
        else:
            self.main = MainWindow(
                self.api,
                user,
                self.service.origin,
                account_profile=self.bootstrap_result.profile,
                account_vault_manager=self.bootstrap_result.vault_manager,
                local_asset_lifecycle=self.bootstrap_result.local_asset_lifecycle,
                bootstrap_snapshot=self.bootstrap_result.snapshot,
                open_recovery_center=lambda: self._show_device_migration(
                    before_main=False
                ),
                return_to_login_gate=self._return_to_login_gate,
            )
        # Keep MentorFlow a normal desktop window.  In particular, it must
        # release focus and z-order when the user clicks another application.
        self.main.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        if updater is not None and updater.login_blocked:
            self._update_login_blocked(True)
            return
        self.main.show()
        if updater is not None:
            updater.bind_window(self.main)
        if self._enterprise_login_required:
            if self.login_gate is not None:
                self.login_gate.hide()
        else:
            self.splash.finish(self.main)

    def _return_to_login_gate(self, reason: str) -> None:
        from .startup_flow import LoginGateState

        if self.main is None:
            return
        if self.main._account_switch_in_progress:
            return
        self.main._account_switch_in_progress = True
        # Fence analogy producers before the existing API/executor drain starts.
        seal_history = getattr(self.main, '_seal_analogy_history', None)
        if seal_history is not None:
            seal_history()
        self._invalidate_login_jobs()
        self._login_session.preferences.disable_restore()
        generation, gate = self._login_generation, self.login_gate
        profile = self.main.account_profile
        refresh = self._last_login_result.refresh_token if self._last_login_result is not None else None
        old_main, old_service = self.main, self.service
        # Stop UI producers before waiting on their executors off the Qt thread.
        for timer in old_main.findChildren(QTimer):
            timer.stop()
        old_main.setEnabled(False)
        self.main.hide()
        gate.set_state(LoginGateState.AUTH_SUCCESS_BOOTSTRAPPING, detail="正在安全结束当前账号会话…")
        gate.show()

        def logout():
            try:
                for name in ("_official_executor", "_analogy_executor", "_provider_executor", "_workspace_executor"):
                    executor = getattr(old_main, name, None)
                    if executor is not None:
                        executor.shutdown(wait=True, cancel_futures=True)
                if old_service is not None:
                    old_service.stop()
                    if not old_service.handles_closed:
                        raise RuntimeError("Previous account API is still running")
                # Switching is not explicit logout: preserve opted-in A
                # credentials, but never auto-restore A into B's login attempt.
                remote = False
                if reason == "logout":
                    remote = self._login_session.logout(profile, remote=True, refresh_token=refresh)
                gate.background_succeeded.emit(f"{generation}:logout_remote", {"reason": reason, "remote": remote})
            except Exception:
                gate.background_failed.emit(f"{generation}:logout", "此设备的会话未能完整清除，请关闭软件后重试。")
        threading.Thread(target=logout, name="mentorflow-account-logout", daemon=True).start()

    def _finish_return_to_login_gate(self, outcome) -> None:
        from .runtime import clear_enterprise_account_session
        reason = outcome["reason"]
        try:
            if self.service is not None and not self.service.handles_closed:
                raise RuntimeError("Previous account API is still running")
            old_main = self.main
            old_main._close_current_account_handles()
            old_main._clear_account_ui_and_runtime_cache()
            old_main.account_vault_manager.unmount()
            if self.bootstrap_result is not None:
                self.bootstrap_result.close()
            clear_enterprise_account_session()
            old_main.close()
            old_main.deleteLater()
            self.main = self.api = self.service = self.bootstrap_result = None
            self.migration_center = None
            self._last_login_result = None
            self._remembered_profile = None
            self.login_gate.remember_box.setChecked(False)
            self.login_gate.auto_box.setChecked(False)
            if reason == "logout":
                self.login_gate.notice_label.setText(
                    "已退出当前账号。" if outcome["remote"] else "此设备已退出；远端会话注销未确认。"
                )
            else:
                self.login_gate.notice_label.setText("")
            self.login_gate.clear_password()
            from .startup_flow import LoginGateState
            self.login_gate.set_state(LoginGateState.LOGIN_READY)
        except (OSError, RuntimeError, ValueError):
            self._background_failed("logout", "当前账号的数据句柄未能完整关闭，请关闭软件后重试。")

    def _service_failed(self, message: str) -> None:
        self.splash.close()
        if self._enterprise_login_required:
            message = "本机学习服务暂时无法打开，请重新启动后重试。"
        QMessageBox.critical(self.login_gate, "MentorFlow 无法启动", message)
        if self.login_gate is not None:
            self.login_gate.close()
        self.app.quit()

    def _shutdown(self) -> None:
        self._closing = True
        self._invalidate_login_jobs()
        self._last_login_result = None
        if getattr(self, "api", None):
            self.api.close()
        if getattr(self, "service", None):
            self.service.stop()
        if getattr(self, "bootstrap_result", None):
            self.bootstrap_result.close()


def main() -> int:
    import multiprocessing
    multiprocessing.freeze_support()
    return MentorFlowApplication().run()


if __name__ == "__main__":
    raise SystemExit(main())
