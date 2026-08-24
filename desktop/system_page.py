"""Local data diagnostics and portable migration controls."""

from __future__ import annotations

import os
import hashlib
import json
import shutil
import sqlite3
import stat
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSettings, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QGridLayout,
    QVBoxLayout,
    QWidget,
)

from .classroom_theme import (
    DEFAULT_CLASSROOM_THEME,
    classroom_theme,
    classroom_theme_options,
    featured_classroom_theme_options,
    normalize_classroom_theme,
)
from .local_migration import (
    MigrationError,
    export_migration_archive,
    import_migration_archive,
    write_next_launch_data_root_pointer,
)
from .version import VERSION_MANIFEST
from .update_service import (
    UpdateError,
    check_for_update,
    download_update,
    launch_verified_installer,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_ROOT = Path(os.getenv("MENTORFLOW_BACKUP_ROOT", r"E:\Codex\Data\backups\MentorFlow"))
FREE_CHAT_PROMPT_SETTINGS_KEY = "classroom/free_chat_prompt"


def _safe_qsettings_snapshot() -> dict[str, Any]:
    """Return only nonsecret Qt settings that are useful on a new device."""

    settings = QSettings("MentorFlow", "MentorFlow")
    allowed = {
        "official_updates/enabled",
        "tutorial/completed",
        "tutorial/step",
        "analogy/generation_policy",
        "analogy/keywords",
        "news/interests",
        "news/blocked",
        "provider/base_url",
        "provider/model",
        "provider/preset_name",
    }
    snapshot: dict[str, Any] = {}
    for key in settings.allKeys():
        if key not in allowed and not key.startswith("task-colors/"):
            continue
        # Credential references and provider sessions are deliberately not in
        # the allowlist.  QSettings can return QVariant wrappers, so retain
        # only JSON-native scalar values here.
        value = settings.value(key)
        if isinstance(value, (bool, int, float, str)):
            snapshot[key] = value
    return snapshot


def _database_report(path: Path, *, local_mode: bool = False) -> list[str]:
    if not path.is_file():
        return [f"{path.name}：不存在"]
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        table_names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        lines = [
            f"{path.name}：{path.stat().st_size / (1024 * 1024):.1f} MiB",
            f"  完整性：{connection.execute('PRAGMA integrity_check').fetchone()[0]}",
            f"  外键错误：{len(connection.execute('PRAGMA foreign_key_check').fetchall())}",
            f"  数据表：{len(table_names)}",
        ]
        if "alembic_version" in table_names:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            lines.append(f"  迁移版本：{row[0] if row else '未知'}")
        for table, label in (
            ("orm_workspaces", "ORM 工作区"),
            ("active_receipts", "主动教学任务"),
            ("active_materials", "主动教学资料"),
            ("active_plans", "主动教学计划"),
            ("orm_progress", "学习进度"),
            ("orm_knowledge_map_revisions", "知识地图修订"),
        ):
            if table in table_names:
                count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                lines.append(f"  {label}：{count}")
        return lines
    finally:
        connection.close()


def diagnostics_text(origin: str, *, local_mode: bool = False) -> str:
    from backend.app import db, orm

    lines = [
        "MentorFlow 本地诊断",
        "",
        f"项目：{PROJECT_ROOT}",
        f"本地服务：{origin}",
        f"Python：{sys.version.split()[0]}",
        "界面：PySide6 / Qt 6",
        "",
        "数据库（只读检查）",
    ]
    for database in (Path(db.DB_PATH), Path(orm.ORM_DB)):
        lines.extend(_database_report(database, local_mode=local_mode))
    lines.extend(
        [
            "",
            "本地运行说明",
            "- 当前为纯本地文件模式，数据来自当前数据目录；没有数据时会初始化新的学习空间。",
            "- API Key 只保存在 Windows Credential Locker，迁移包不包含 Key；换设备后需重新填写。",
            "- 导入迁移包必须使用新目录或空目录，软件不会覆盖正在使用的数据。",
        ]
    )
    return "\n".join(lines)


def diagnostics_summary(report: str) -> str:
    integrity = [line.strip().casefold() for line in report.splitlines() if "完整性：" in line]
    foreign_keys = [line.strip() for line in report.splitlines() if "外键错误：" in line]
    missing = [line.strip() for line in report.splitlines() if line.strip().endswith("：不存在")]
    if missing:
        return "有本地数据文件尚未就绪，点击详情查看。"
    if integrity and all(line.endswith("：ok") for line in integrity) and all(line.endswith("：0") for line in foreign_keys):
        return "本地数据完整，没有发现缺失或关联错误。"
    return "本地数据需要检查，点击详情查看。"


def create_manual_backup() -> Path:
    if getattr(sys, "frozen", False):
        return _create_frozen_backup()
    DEFAULT_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "backup_knowledge_map_state.py"),
        "--project-root",
        str(PROJECT_ROOT),
        "--destination-root",
        str(DEFAULT_BACKUP_ROOT),
        "--label",
        "native-manual",
    ]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "备份脚本失败")
    destination = Path(completed.stdout.strip().splitlines()[-1]).resolve()
    if not destination.is_dir() or not (destination / "manifest-sha256.json").is_file():
        raise RuntimeError("备份脚本没有产生完整清单")
    return destination


def _create_frozen_backup() -> Path:
    """Create the same recoverable backup without launching a bundled EXE again."""
    from backend.app import db, orm

    DEFAULT_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    destination = DEFAULT_BACKUP_ROOT / f"{timestamp}-native-manual"
    destination.mkdir(parents=True, exist_ok=False)
    report: dict[str, object] = {}
    for source in (Path(db.DB_PATH), Path(orm.ORM_DB)):
        target = destination / "data" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        source_connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        target_connection = sqlite3.connect(target)
        try:
            source_connection.backup(target_connection)
            tables = [
                str(row[0])
                for row in target_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            report[source.name] = {
                "integrity_check": target_connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0],
                "tables": {
                    table: target_connection.execute(
                        f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                    ).fetchone()[0]
                    for table in tables
                },
            }
        finally:
            target_connection.close()
            source_connection.close()
    upload_root = Path(os.environ["MENTORFLOW_ACTIVE_UPLOAD_ROOT"]).parent
    if upload_root.is_dir():
        shutil.copytree(upload_root, destination / "data" / "active_teaching")
    (destination / "sqlite-integrity-and-row-counts.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = []
    for path in sorted(item for item in destination.rglob("*") if item.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.append(
            {
                "relative_path": path.relative_to(destination).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": digest,
            }
        )
    (destination / "manifest-sha256.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (destination / "RESTORE.txt").write_text(
        "退出 MentorFlow 后，将 data 下的文件恢复到本用户的 MentorFlow 数据目录。\n",
        encoding="utf-8",
    )
    for path in (item for item in destination.rglob("*") if item.is_file()):
        os.chmod(path, stat.S_IREAD)
    return destination.resolve()


class SystemPage(QWidget):
    def __init__(
        self,
        _legacy_user: dict,
        origin: str,
        _legacy_action: Callable[[], None],
        api: Any,
        open_section: Callable[[str], None] | None = None,
        restart_tutorial: Callable[[], None] | None = None,
        set_online_updates: Callable[[bool], None] | None = None,
        reload_official_updates: Callable[[], None] | None = None,
        online_updates_enabled: bool = False,
        classroom_theme_changed: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.origin = origin
        self.api = api
        self.open_section = open_section
        self.restart_tutorial_callback = restart_tutorial
        self.set_online_updates_callback = set_online_updates
        self.reload_official_updates_callback = reload_official_updates
        self.online_updates_enabled = bool(online_updates_enabled)
        self.classroom_theme_changed_callback = classroom_theme_changed
        self._classroom_theme_committed_key = DEFAULT_CLASSROOM_THEME
        self._classroom_theme_preview_key: str | None = None
        self.last_backup: Path | None = None
        self.data_root = self._resolve_data_root()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mentorflow-settings")
        self._diagnostics_future: Future[str] | None = None
        self._data_future: Future[Any] | None = None
        self._data_action = ""
        self._update_future: Future[dict[str, Any]] | None = None
        self._update_action = ""
        self._last_update_result: dict[str, Any] | None = None
        self._build()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    @staticmethod
    def _resolve_data_root() -> Path:
        configured = os.getenv("MENTORFLOW_USER_DATA_ROOT", "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        database_path = os.getenv("MENTORFLOW_ORM_DB", "").strip() or os.getenv("MENTORFLOW_DB", "").strip()
        if database_path:
            return Path(database_path).expanduser().resolve().parent.parent
        if getattr(sys, "frozen", False):
            return Path(r"E:\MentorFlowData").resolve()
        # Source-mode defaults live under the repository, not the frozen E:
        # user-data root.  Reporting E: here made diagnostics and export point
        # at a different learning space from the backend.
        return Path(__file__).resolve().parents[1]

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(38, 32, 38, 32)
        scroller.setWidget(content)
        outer.addWidget(scroller)
        heading = QLabel("设置")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        subtitle = QLabel(
            "软件会自动读取当前数据目录；没有数据时会初始化新的学习空间。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        settings_card = QFrame()
        settings_card.setObjectName("explanationCard")
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setSpacing(12)
        settings_layout.addWidget(QLabel("版本与常用设置"))
        settings_layout.addWidget(
            QLabel(
                f"MentorFlow {VERSION_MANIFEST.get('version') or 'unknown'} · 本地版"
            )
        )
        common_hint = QLabel("常用设置放在这里；换设备和本地数据检查在下方。")
        common_hint.setObjectName("pageSubtitle")
        common_hint.setWordWrap(True)
        settings_layout.addWidget(common_hint)

        update_card = QFrame(settings_card)
        update_card.setObjectName("updateCenter")
        update_layout = QVBoxLayout(update_card)
        update_layout.setContentsMargins(12, 10, 12, 10)
        update_layout.setSpacing(8)
        update_layout.addWidget(QLabel("软件安装包更新"))
        update_hint = QLabel(
            "只在你点击检查时访问官方 GitHub Release；下载并启动最新 Windows 安装包，"
            "更新程序本身，不替换或清空你的学习数据。"
        )
        update_hint.setObjectName("pageSubtitle")
        update_hint.setWordWrap(True)
        update_layout.addWidget(update_hint)
        update_actions = QHBoxLayout()
        self.update_check_button = QPushButton("检查更新")
        self.update_check_button.setAccessibleName("检查 MentorFlow 软件更新")
        self.update_check_button.clicked.connect(self.check_for_updates)
        update_actions.addWidget(self.update_check_button)
        self.update_download_button = QPushButton("下载并校验更新")
        self.update_download_button.setAccessibleName("下载并校验 MentorFlow 更新")
        self.update_download_button.setEnabled(False)
        self.update_download_button.clicked.connect(self.download_and_install_update)
        update_actions.addWidget(self.update_download_button)
        self.update_release_button = QPushButton("打开 Release 页面")
        self.update_release_button.setAccessibleName("打开 MentorFlow Release 页面")
        self.update_release_button.setEnabled(False)
        self.update_release_button.clicked.connect(self._open_update_release)
        update_actions.addWidget(self.update_release_button)
        update_actions.addStretch(1)
        update_layout.addLayout(update_actions)
        self.update_status = QLabel("尚未检查；点击“检查更新”后才会联网。")
        self.update_status.setObjectName("inlineStatus")
        self.update_status.setWordWrap(True)
        update_layout.addWidget(self.update_status)
        settings_layout.addWidget(update_card)

        theme_label = QLabel("课堂皮肤")
        theme_label.setObjectName("sectionHeading")
        settings_layout.addWidget(theme_label)
        theme_row = QHBoxLayout()
        self.classroom_theme_combo = QComboBox(settings_card)
        self.classroom_theme_combo.setAccessibleName("课堂皮肤选择")
        self.classroom_theme_combo.setMinimumWidth(220)
        current_theme = normalize_classroom_theme(
            QSettings("MentorFlow", "MentorFlow").value(
                "appearance/classroom_theme", DEFAULT_CLASSROOM_THEME
            )
        )
        self._classroom_theme_committed_key = current_theme
        current_index = 0
        featured_themes = list(featured_classroom_theme_options())
        if current_theme not in {key for key, _label, _description in featured_themes}:
            current_theme = DEFAULT_CLASSROOM_THEME
            self._classroom_theme_committed_key = current_theme
        for index, (key, label, description) in enumerate(featured_themes):
            self.classroom_theme_combo.addItem(label, key)
            self.classroom_theme_combo.setItemData(index, description, Qt.ItemDataRole.ToolTipRole)
            if key == current_theme:
                current_index = index
        self.classroom_theme_combo.setCurrentIndex(current_index)
        self.classroom_theme_combo.setStyleSheet(
            "QComboBox { padding: 5px 10px; min-height: 30px; "
            "border: 1px solid #edc8d8; border-radius: 10px; background: #fff8fb; color: #3b2b3f; }"
            "QComboBox:focus { border: 2px solid #c95f8d; }"
            "QComboBox QAbstractItemView { background: #fffdfd; color: #3b2b3f; "
            "selection-background-color: #ffe9f2; }"
        )
        theme_row.addWidget(self.classroom_theme_combo)
        theme_row.addStretch(1)
        settings_layout.addLayout(theme_row)
        self.classroom_theme_description = QLabel(settings_card)
        self.classroom_theme_description.setObjectName("pageSubtitle")
        self.classroom_theme_description.setWordWrap(True)
        settings_layout.addWidget(self.classroom_theme_description)

        # Keep the user-facing choice deliberate: four structurally distinct
        # classroom skins.  Legacy manifest entries remain internal so an old
        # local setting can be normalized without exposing an unfinished wall
        # of near-duplicate cards.
        gallery_heading = QLabel("主题画廊")
        gallery_heading.setObjectName("sectionHeading")
        settings_layout.addWidget(gallery_heading)
        gallery_hint = QLabel("先预览课堂皮肤，再点击应用保存；取消预览会恢复上次已应用的皮肤。只影响课堂视觉，不会重载教师、模型或学习进度。")
        gallery_hint.setObjectName("pageSubtitle")
        gallery_hint.setWordWrap(True)
        settings_layout.addWidget(gallery_hint)
        gallery = QWidget(settings_card)
        gallery.setObjectName("classroomThemeGallery")
        gallery_layout = QGridLayout(gallery)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        gallery_layout.setHorizontalSpacing(10)
        gallery_layout.setVerticalSpacing(10)
        self.classroom_theme_cards: dict[str, QFrame] = {}
        self.classroom_theme_apply_buttons: dict[str, QPushButton] = {}
        self.classroom_theme_preview_buttons: dict[str, QPushButton] = {}
        for card_index, (key, label, description) in enumerate(featured_themes):
            theme = classroom_theme(key)
            canonical_label = str(theme["label"])
            card = QFrame(gallery)
            card.setObjectName("classroomThemeCard")
            card.setProperty("themeKey", key)
            card.setToolTip(description)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 9, 10, 9)
            card_layout.setSpacing(5)
            preview = QFrame(card)
            preview.setObjectName("classroomThemePreview")
            preview.setMinimumHeight(30)
            preview.setStyleSheet(
                f"QFrame#classroomThemePreview {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                f"stop:0 {theme['background_top']}, stop:.62 {theme['surface']}, stop:1 {theme['accent_soft']}); "
                f"border: 2px {theme['border_style']} {theme['border']}; border-radius: {theme['control_radius']}px; }}"
            )
            preview_layout = QHBoxLayout(preview)
            preview_layout.setContentsMargins(8, 5, 8, 5)
            preview_layout.setSpacing(5)
            preview_bubble = QFrame(preview)
            preview_bubble.setObjectName("classroomThemePreviewBubble")
            preview_bubble.setFixedSize(54, 18)
            preview_bubble.setStyleSheet(
                f"QFrame#classroomThemePreviewBubble {{ background: {theme['teacher_bubble']}; "
                f"border: {theme['bubble_edge_width']}px {theme['border_style']} {theme['teacher_border']}; "
                f"border-bottom: {theme['bubble_shadow_width']}px solid {theme['shadow_color']}; "
                f"border-radius: {min(int(theme['bubble_radius']), 10)}px; }}"
            )
            preview_layout.addWidget(preview_bubble)
            preview_layout.addStretch(1)
            preview_stage = QLabel(str(theme.get("stage_header_mark", theme["symbol"])), preview)
            preview_stage.setObjectName("classroomThemePreviewStage")
            preview_stage.setStyleSheet(
                f"color: {theme['accent']}; font-size: 17px; font-weight: {theme['icon_weight']}; "
                f"font-family: {theme['font_family']}; background: transparent;"
            )
            preview_layout.addWidget(preview_stage)
            card_layout.addWidget(preview)
            title = QLabel(f"{theme['symbol']}  {label}", card)
            title.setObjectName("classroomThemeCardTitle")
            title.setWordWrap(True)
            title.setStyleSheet(f"color: {theme['accent_dark']}; font-weight: 800; font-family: {theme['font_family']};")
            card_layout.addWidget(title)
            card_note = QLabel(description, card)
            card_note.setObjectName("classroomThemeCardDescription")
            card_note.setWordWrap(True)
            card_note.setStyleSheet(f"color: {theme['muted']}; font-size: 11px; background: transparent;")
            card_layout.addWidget(card_note)
            preview_button = QPushButton("预览", card)
            preview_button.setAccessibleName(f"预览课堂皮肤：{label}")
            preview_button.setProperty("themeKey", key)
            preview_button.setStyleSheet(
                f"QPushButton {{ color: {theme['accent_dark']}; background: {theme['surface']}; "
                f"border: 1px {theme['border_style']} {theme['border']}; border-radius: {theme['control_radius']}px; padding: 4px 8px; }}"
            )
            preview_button.clicked.connect(lambda _checked=False, theme_key=key: self._preview_classroom_theme(theme_key))
            card_layout.addWidget(preview_button)
            self.classroom_theme_preview_buttons[key] = preview_button
            apply_button = QPushButton("应用", card)
            apply_button.setAccessibleName(f"应用课堂皮肤：{canonical_label}")
            apply_button.setProperty("themeKey", key)
            apply_button.setStyleSheet(
                f"QPushButton {{ color: {theme['accent_dark']}; background: {theme['accent_soft']}; "
                f"border: 1px {theme['border_style']} {theme['accent']}; border-radius: {theme['control_radius']}px; padding: 4px 8px; }}"
            )
            apply_button.clicked.connect(lambda _checked=False, theme_key=key: self._apply_classroom_theme(theme_key))
            card_layout.addWidget(apply_button)
            row, column = divmod(card_index, 4)
            gallery_layout.addWidget(card, row, column)
            self.classroom_theme_cards[key] = card
            self.classroom_theme_apply_buttons[key] = apply_button
        settings_layout.addWidget(gallery)
        cancel_preview = QPushButton("取消预览", settings_card)
        cancel_preview.setAccessibleName("取消课堂皮肤预览")
        cancel_preview.setEnabled(False)
        cancel_preview.clicked.connect(self._cancel_classroom_theme_preview)
        self.classroom_theme_cancel_button = cancel_preview
        settings_layout.addWidget(cancel_preview, alignment=Qt.AlignmentFlag.AlignLeft)
        reset_theme = QPushButton("重置为樱花教室", settings_card)
        reset_theme.setAccessibleName("重置课堂皮肤为樱花教室")
        reset_theme.clicked.connect(lambda _checked=False: self._apply_classroom_theme(DEFAULT_CLASSROOM_THEME))
        settings_layout.addWidget(reset_theme, alignment=Qt.AlignmentFlag.AlignLeft)
        self.classroom_theme_combo.currentIndexChanged.connect(self._set_classroom_theme)
        self._set_classroom_theme(current_index, persist=False)

        theme_privacy = QLabel(
            "皮肤选择只保存在这台电脑；软件安装包始终从通用的樱花教室开始，不会携带制作者或其他用户的个人预设。"
        )
        theme_privacy.setObjectName("inlineStatus")
        theme_privacy.setWordWrap(True)
        settings_layout.addWidget(theme_privacy)

        free_chat_label = QLabel("自由聊天提示词")
        free_chat_label.setObjectName("sectionHeading")
        settings_layout.addWidget(free_chat_label)
        free_chat_hint = QLabel(
            "这里的文字只会用于“自由聊聊”。留空时直接使用你的消息，不添加 MentorFlow 自定义提示词；"
            "该设置只保存在当前电脑，不会进入迁移包或新电脑预设。"
        )
        free_chat_hint.setObjectName("pageSubtitle")
        free_chat_hint.setWordWrap(True)
        settings_layout.addWidget(free_chat_hint)
        self.free_chat_prompt = QPlainTextEdit(settings_card)
        self.free_chat_prompt.setAccessibleName("自由聊天自定义提示词")
        self.free_chat_prompt.setPlaceholderText("留空：不添加 MentorFlow 自定义自由聊天提示词")
        self.free_chat_prompt.setMaximumHeight(120)
        self.free_chat_prompt.setPlainText(
            str(QSettings("MentorFlow", "MentorFlow").value(FREE_CHAT_PROMPT_SETTINGS_KEY, "") or "")
        )
        settings_layout.addWidget(self.free_chat_prompt)
        free_chat_actions = QHBoxLayout()
        save_free_chat = QPushButton("保存自由聊天提示词", settings_card)
        save_free_chat.setAccessibleName("保存自由聊天提示词")
        save_free_chat.clicked.connect(self._save_free_chat_prompt)
        free_chat_actions.addWidget(save_free_chat)
        clear_free_chat = QPushButton("清空提示词", settings_card)
        clear_free_chat.setAccessibleName("清空自由聊天提示词")
        clear_free_chat.clicked.connect(self._clear_free_chat_prompt)
        free_chat_actions.addWidget(clear_free_chat)
        free_chat_actions.addStretch(1)
        settings_layout.addLayout(free_chat_actions)
        self.free_chat_prompt_status = QLabel("当前未设置自定义提示词。" if not self.free_chat_prompt.toPlainText().strip() else "已读取当前电脑保存的自由聊天提示词。")
        self.free_chat_prompt_status.setObjectName("inlineStatus")
        self.free_chat_prompt_status.setWordWrap(True)
        settings_layout.addWidget(self.free_chat_prompt_status)
        settings_actions = QHBoxLayout()
        provider = QPushButton("打开模型连接")
        provider.clicked.connect(lambda: self._open_section("provider"))
        settings_actions.addWidget(provider)
        settings_actions.addStretch()
        settings_layout.addLayout(settings_actions)
        layout.addWidget(settings_card)

        local_card = QFrame()
        local_card.setObjectName("explanationCard")
        local_layout = QVBoxLayout(local_card)
        local_layout.setSpacing(10)
        local_layout.addWidget(QLabel("换设备：导出或导入本地数据"))
        # Use the same resolved root used by export/import.  Reporting the
        # raw environment value here used to make a review copy appear to
        # point at E:\\MentorFlowData even when its databases lived elsewhere.
        local_layout.addWidget(QLabel("当前数据目录：" + str(self.data_root)))
        package_note = QLabel(
            "软件安装包和用户数据包是两件东西：安装包可以发给任何人，迁移压缩包只属于当前本地学习空间。"
        )
        package_note.setObjectName("inlineStatus")
        package_note.setWordWrap(True)
        local_layout.addWidget(package_note)
        migration_help = QLabel(
            "换设备：①点击导出并选择保存位置；②在新设备单独安装软件；③首次打开后选择压缩包和一个空目录导入。"
            "导入不会覆盖当前正在使用的数据，也不会把别人的学习内容混进来；API Key、Credential Locker 引用和 Provider 会话不会写入普通 ZIP。"
        )
        migration_help.setWordWrap(True)
        local_layout.addWidget(migration_help)
        migration_actions = QHBoxLayout()
        export_button = QPushButton("导出我的数据包")
        export_button.setObjectName("primaryButton")
        self.export_button = export_button
        export_button.clicked.connect(self.export_migration)
        migration_actions.addWidget(export_button)
        import_button = QPushButton("在新设备导入数据包")
        self.import_button = import_button
        import_button.clicked.connect(self.import_migration)
        migration_actions.addWidget(import_button)
        migration_actions.addStretch()
        local_layout.addLayout(migration_actions)
        self.migration_status = QLabel("尚未进行导入或导出。")
        self.migration_status.setObjectName("inlineStatus")
        self.migration_status.setWordWrap(True)
        local_layout.addWidget(self.migration_status)
        layout.addWidget(local_card)

        diagnostics_card = QFrame()
        diagnostics_card.setObjectName("explanationCard")
        diagnostics_layout = QVBoxLayout(diagnostics_card)
        diagnostics_layout.setSpacing(10)
        diagnostics_layout.addWidget(QLabel("本地数据检查"))
        diagnostics_hint = QLabel(
            "这里会检查学习数据是否完整；发现问题时再展开详情。"
        )
        diagnostics_hint.setWordWrap(True)
        diagnostics_layout.addWidget(diagnostics_hint)
        self.diagnostics_toggle = QPushButton("查看详情")
        self.diagnostics_toggle.setCheckable(True)
        self.diagnostics_toggle.toggled.connect(self._toggle_diagnostics)
        diagnostics_layout.addWidget(self.diagnostics_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        self.diagnostics_actions = QFrame()
        actions = QHBoxLayout(self.diagnostics_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        refresh = QPushButton("重新检查")
        self.refresh_button = refresh
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        copy = QPushButton("复制详情")
        copy.clicked.connect(self.copy_diagnostics)
        actions.addWidget(copy)
        backup = QPushButton("创建安全备份")
        self.backup_button = backup
        backup.clicked.connect(self.backup)
        actions.addWidget(backup)
        self.open_backup_button = QPushButton("打开最近备份")
        self.open_backup_button.setEnabled(False)
        self.open_backup_button.clicked.connect(self.open_backup)
        actions.addWidget(self.open_backup_button)
        actions.addStretch()
        diagnostics_layout.addWidget(self.diagnostics_actions)

        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMinimumHeight(230)
        diagnostics_layout.addWidget(self.report)
        self.diagnostics_actions.setVisible(False)
        self.report.setVisible(False)
        self.diagnostics_status = QLabel("正在检查本地数据…")
        self.diagnostics_status.setObjectName("inlineStatus")
        self.diagnostics_status.setWordWrap(True)
        diagnostics_layout.addWidget(self.diagnostics_status)
        layout.addWidget(diagnostics_card)
        layout.addStretch()
        self.refresh()

    def _save_free_chat_prompt(self) -> None:
        value = self.free_chat_prompt.toPlainText()[:6000]
        settings = QSettings("MentorFlow", "MentorFlow")
        if value.strip():
            settings.setValue(FREE_CHAT_PROMPT_SETTINGS_KEY, value)
            status = "已保存到当前电脑；不会导出到迁移包。"
        else:
            settings.remove(FREE_CHAT_PROMPT_SETTINGS_KEY)
            status = "已清空；自由聊聊将直接使用你的消息。"
        settings.sync()
        self.free_chat_prompt_status.setText(status)

    def _clear_free_chat_prompt(self) -> None:
        self.free_chat_prompt.clear()
        self._save_free_chat_prompt()

    def _set_classroom_theme(self, index: int, *, persist: bool = True) -> None:
        if not hasattr(self, "classroom_theme_combo"):
            return
        key = normalize_classroom_theme(self.classroom_theme_combo.itemData(max(0, index)))
        # Changing the selector is a preview. Only the explicit Apply button
        # commits a skin, while Cancel can always restore the last committed
        # theme. The `persist=False` initialization path only paints the UI.
        self._apply_classroom_theme(
            key,
            persist=False,
            preview=persist,
            notify=persist,
        )

    def _notify_classroom_theme_changed(self, key: str, *, persist: bool) -> None:
        callback = self.classroom_theme_changed_callback
        if callback is None:
            return
        # MainWindow passes ClassroomPage.apply_theme, which understands the
        # non-persisting preview path.  Keep the older one-argument callback
        # contract used by lightweight tests and embedders.
        try:
            callback(key, persist=persist)
        except TypeError:
            callback(key)

    def _preview_classroom_theme(self, key: str) -> None:
        self._apply_classroom_theme(key, persist=False, preview=True, notify=True)

    def _cancel_classroom_theme_preview(self) -> None:
        if self._classroom_theme_preview_key is None:
            return
        self._apply_classroom_theme(self._classroom_theme_committed_key, persist=False, preview=False, notify=True)

    def _apply_classroom_theme(
        self,
        key: str,
        *,
        persist: bool = True,
        preview: bool = False,
        notify: bool = False,
    ) -> None:
        key = normalize_classroom_theme(key)
        theme = classroom_theme(key)
        if hasattr(self, "classroom_theme_combo") and self.classroom_theme_combo.currentData() != key:
            self.classroom_theme_combo.blockSignals(True)
            self.classroom_theme_combo.setCurrentIndex(max(0, self.classroom_theme_combo.findData(key)))
            self.classroom_theme_combo.blockSignals(False)
        description = ""
        for option_key, _label, option_description in classroom_theme_options():
            if option_key == key:
                description = option_description
                break
        if hasattr(self, "classroom_theme_description"):
            self.classroom_theme_description.setText(description)
        if hasattr(self, "classroom_theme_combo"):
            self.classroom_theme_combo.setStyleSheet(
                f"QComboBox {{ padding: 5px 10px; min-height: 30px; border: 1px {theme['border_style']} {theme['border']}; "
                f"border-radius: {theme['control_radius']}px; background: {theme['surface_elevated']}; color: {theme['text']}; }}"
                f"QComboBox:focus {{ border: {int(theme.get('focus_width', 2))}px solid {theme['accent']}; }}"
                f"QComboBox QAbstractItemView {{ background: {theme['surface']}; color: {theme['text']}; "
                f"selection-background-color: {theme['accent_soft']}; }}"
            )
        self._refresh_theme_gallery_selection(key)
        if preview:
            self._classroom_theme_preview_key = key
        elif persist:
            self._classroom_theme_committed_key = key
            self._classroom_theme_preview_key = None
        elif key == self._classroom_theme_committed_key:
            self._classroom_theme_preview_key = None
        if hasattr(self, "classroom_theme_cancel_button"):
            self.classroom_theme_cancel_button.setEnabled(self._classroom_theme_preview_key is not None)
        if not persist:
            if notify:
                self._notify_classroom_theme_changed(key, persist=False)
            return
        settings = QSettings("MentorFlow", "MentorFlow")
        settings.setValue("appearance/classroom_theme", key)
        settings.sync()
        self._notify_classroom_theme_changed(key, persist=True)

    def _refresh_theme_gallery_selection(self, key: str) -> None:
        for card_key, card in getattr(self, "classroom_theme_cards", {}).items():
            selected = card_key == key
            card.setProperty("selected", selected)
            card_theme = classroom_theme(card_key)
            card.setStyleSheet(
                "QFrame#classroomThemeCard { border: %spx %s %s; border-radius: %spx; background: %s; }"
                % (
                    2 if selected else 1,
                    card_theme.get("border_style", "solid"),
                    card_theme["accent"] if selected else card_theme["border"],
                    card_theme["panel_radius"],
                    card_theme["surface_elevated"],
                )
            )

    def check_for_updates(self) -> None:
        """Start an explicit, asynchronous official release check."""

        if self._update_future is not None and not self._update_future.done():
            self.update_status.setText("更新检查正在进行，请稍候。")
            return
        self._update_action = "check"
        self._last_update_result = None
        self.update_check_button.setEnabled(False)
        self.update_download_button.setEnabled(False)
        self.update_release_button.setEnabled(False)
        self.update_check_button.setText("检查中……")
        self.update_status.setText("正在从官方 GitHub Release 检查更新……")
        self._update_future = self._executor.submit(check_for_update)
        QTimer.singleShot(40, self._poll_update)

    def download_and_install_update(self) -> None:
        """Stage a verified update, then ask before launching an installer."""

        if self._update_future is not None and not self._update_future.done():
            self.update_status.setText("更新操作正在进行，请稍候。")
            return
        result = self._last_update_result or {}
        if not bool(result.get("update")):
            self.update_status.setText("当前没有可下载的更新，请先检查更新。")
            self.update_download_button.setEnabled(False)
            return
        self._update_action = "download"
        self.update_check_button.setEnabled(False)
        self.update_download_button.setEnabled(False)
        self.update_release_button.setEnabled(False)
        self.update_download_button.setText("下载并校验中……")
        self.update_status.setText("正在下载到 E:\\MentorFlowUpdates 并校验 SHA-256……")
        self._update_future = self._executor.submit(download_update)
        QTimer.singleShot(40, self._poll_update)

    def _poll_update(self) -> None:
        future = self._update_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(40, self._poll_update)
            return
        self._update_future = None
        action = self._update_action
        self._update_action = ""
        try:
            result = future.result()
            if action == "check":
                self._last_update_result = result
                if not bool(result.get("update")):
                    self.update_status.setText(
                        f"当前已是最新版本（{result.get('current') or VERSION_MANIFEST.get('version') or 'unknown'}）。"
                    )
                else:
                    latest = str(result.get("latest") or "新版本")
                    release_page = str(result.get("release_page") or "")
                    self.update_status.setText(
                        f"发现新版本 {latest}（当前 {result.get('current') or 'unknown'}）。"
                        "可打开 Release 页面查看说明，或下载并校验安装器。"
                    )
                    self.update_download_button.setEnabled(True)
                    self.update_release_button.setEnabled(bool(release_page))
            else:
                staged_path = str(result.get("staged_path") or "")
                if not staged_path:
                    raise UpdateError("更新已完成校验，但没有返回暂存路径")
                installer = Path(staged_path)
                if installer.suffix.casefold() != ".exe":
                    self.update_status.setText(
                        f"资源已校验并暂存到：{installer}\n当前资源不是 Windows 安装器，请退出软件后手动处理。"
                    )
                else:
                    answer = QMessageBox.question(
                        self,
                        "确认启动安装器",
                        f"安装器已通过 SHA-256 校验：\n{installer}\n\n"
                        "启动后请按 Windows 安装向导操作；MentorFlow 不会覆盖当前数据目录。是否启动？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if answer == QMessageBox.StandardButton.Yes:
                        launch_verified_installer(
                            installer,
                            expected_sha256=str(result.get("sha256") or ""),
                        )
                        self.update_status.setText(f"已启动已校验安装器：{installer}")
                    else:
                        self.update_status.setText(f"已校验并暂存安装器：{installer}（未启动）。")
        except Exception as exc:  # defensive boundary around optional networking/installer launch
            self.update_status.setText(f"更新失败：{exc}")
        finally:
            self.update_check_button.setEnabled(True)
            self.update_check_button.setText("检查更新")
            if action == "download":
                self.update_download_button.setText("下载并校验更新")
                self.update_download_button.setEnabled(bool(self._last_update_result and self._last_update_result.get("update")))
                self.update_release_button.setEnabled(bool(self._last_update_result and self._last_update_result.get("release_page")))

    def _open_update_release(self) -> None:
        result = self._last_update_result or {}
        url = str(result.get("release_page") or "")
        if not url.startswith("https://github.com/"):
            self.update_status.setText("Release 页面地址无效，未打开外部链接。")
            return
        QDesktopServices.openUrl(QUrl(url))

    def refresh(self) -> None:
        if self._diagnostics_future is not None and not self._diagnostics_future.done():
            self.diagnostics_status.setText("本地数据检查正在进行…")
            return
        if hasattr(self, "refresh_button"):
            self.refresh_button.setEnabled(False)
            self.refresh_button.setText("检查中……")
        if hasattr(self, "diagnostics_status"):
            self.diagnostics_status.setText("正在只读检查本地数据库……")
        local_mode = os.getenv("MENTORFLOW_LOCAL_DATA_MODE", "").strip().lower() in {"1", "true", "yes"}
        self._diagnostics_future = self._executor.submit(
            diagnostics_text,
            self.origin,
            local_mode=local_mode,
        )
        QTimer.singleShot(40, self._poll_diagnostics)

    def _poll_diagnostics(self) -> None:
        future = self._diagnostics_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(40, self._poll_diagnostics)
            return
        self._diagnostics_future = None
        try:
            report = future.result()
            self.report.setPlainText(report)
            self.diagnostics_status.setText(diagnostics_summary(report))
        except (OSError, sqlite3.Error) as exc:
            self.report.setPlainText(f"诊断失败：{exc}")
            self.diagnostics_status.setText(f"检查失败：{exc}")
        finally:
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText("重新检查")

    def _open_section(self, section: str) -> None:
        if self.open_section:
            self.open_section(section)

    def _restart_tutorial(self) -> None:
        if self.restart_tutorial_callback:
            self.restart_tutorial_callback()
        else:
            QMessageBox.information(self, "引导已重置", "下次打开主界面时会再次显示引导。")

    def _set_online_updates(self, enabled: bool) -> None:
        self.set_official_update_busy(
            bool(enabled),
            "已开启，正在准备加载官方动态……" if enabled else "已关闭在线动态，不会发起网络请求。",
        )
        if self.set_online_updates_callback:
            self.set_online_updates_callback(bool(enabled))

    def _reload_official_updates(self) -> None:
        if not self.online_toggle.isChecked():
            self.set_official_update_busy(False, "请先勾选允许加载官方在线更新。")
            return
        self.set_official_update_busy(True, "正在加载官方动态……")
        if self.reload_official_updates_callback:
            self.reload_official_updates_callback()

    def set_official_update_busy(self, busy: bool, status: str) -> None:
        if not hasattr(self, "online_status"):
            return
        self.online_progress.setVisible(bool(busy))
        self.reload_updates_button.setEnabled(not busy and self.online_toggle.isChecked())
        self.reload_updates_button.setText("正在加载……" if busy else "立即刷新官方动态")
        self.online_status.setText(status)

    def _toggle_diagnostics(self, visible: bool) -> None:
        self.diagnostics_actions.setVisible(bool(visible))
        self.report.setVisible(bool(visible))
        self.diagnostics_toggle.setText("收起详情" if visible else "查看详情")
        if visible:
            self.diagnostics_status.setText("详细信息已展开。")
        else:
            self.diagnostics_status.setText(diagnostics_summary(self.report.toPlainText()))

    def copy_diagnostics(self) -> None:
        QApplication.clipboard().setText(self.report.toPlainText())
        self.diagnostics_status.setText("诊断信息已复制到剪贴板。")

    def export_migration(self) -> None:
        default_name = f"MentorFlow-迁移-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}.zip"
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "保存 MentorFlow 迁移压缩包",
            str(self.data_root.parent / default_name),
            "MentorFlow 迁移包 (*.zip)",
        )
        if not destination:
            self.migration_status.setText("已取消导出，没有创建文件。")
            return
        output = Path(destination)
        if output.suffix.casefold() != ".zip":
            output = output.with_suffix(".zip")
        self.export_button.setEnabled(False)
        self.export_button.setText("正在导出……")
        self.migration_status.setText(f"正在导出到：{output}")
        self.report.appendPlainText(f"\n正在导出迁移压缩包：{output}")
        if not self._start_data_action(
            "export",
            lambda: export_migration_archive(
                self.data_root,
                output,
                safe_settings=_safe_qsettings_snapshot(),
            ),
        ):
            self.export_button.setEnabled(True)
            self.export_button.setText("导出我的数据包")
            return

    def import_migration(self) -> None:
        archive_name, _ = QFileDialog.getOpenFileName(
            self,
            "选择 MentorFlow 迁移压缩包",
            str(self.data_root.parent),
            "MentorFlow 迁移包 (*.zip)",
        )
        if not archive_name:
            self.migration_status.setText("已取消导入，没有读取文件。")
            return
        target = QFileDialog.getExistingDirectory(
            self,
            "选择导入后的本地数据目录（必须是新目录或空目录）",
            str(Path(r"E:\MentorFlowData")),
        )
        if not target:
            self.migration_status.setText("已取消导入，没有选择目标目录。")
            return
        target_path = Path(target)
        if target_path.resolve() == self.data_root.resolve():
            QMessageBox.warning(
                self,
                "不能导入到当前目录",
                "当前数据目录正在使用中。请选择一个新目录或空目录，导入完成后再用该目录启动 MentorFlow。",
            )
            return
        if QMessageBox.question(
            self,
            "确认导入",
            f"将把迁移包导入到：\n{target_path}\n\n目标目录必须为空，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.report.appendPlainText(f"\n正在验证并导入迁移压缩包：{archive_name}")
        self.import_button.setEnabled(False)
        self.import_button.setText("正在验证并导入……")
        self.migration_status.setText("正在验证压缩包完整性并导入到新目录……")
        if not self._start_data_action(
            "import",
            lambda: import_migration_archive(Path(archive_name), target_path),
        ):
            self.import_button.setEnabled(True)
            self.import_button.setText("在新设备导入数据包")
            return

    def _start_data_action(self, kind: str, work: Callable[[], Any]) -> bool:
        if self._data_future is not None and not self._data_future.done():
            if hasattr(self, "migration_status"):
                self.migration_status.setText("上一项数据操作仍在进行，请稍候。")
            return False
        self._data_action = kind
        self._data_future = self._executor.submit(work)
        QTimer.singleShot(40, self._poll_data_action)
        return True

    def _poll_data_action(self) -> None:
        future = self._data_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(40, self._poll_data_action)
            return
        self._data_future = None
        action = self._data_action
        self._data_action = ""
        try:
            result = future.result()
        except (MigrationError, OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
            if action == "backup":
                self.backup_button.setEnabled(True)
                self.backup_button.setText("创建安全备份")
                self.diagnostics_status.setText(f"备份失败：{exc}")
                QMessageBox.critical(self, "备份失败", str(exc))
            else:
                button = self.export_button if action == "export" else self.import_button
                button.setEnabled(True)
                button.setText("导出我的数据包" if action == "export" else "在新设备导入数据包")
                self.migration_status.setText(f"{'导出' if action == 'export' else '导入'}失败：{exc}")
                QMessageBox.critical(self, f"{'导出' if action == 'export' else '导入'}失败", str(exc))
            return
        if action == "export":
            self.export_button.setEnabled(True)
            self.export_button.setText("导出我的数据包")
            self.report.appendPlainText(
                f"导出完成：{result['archive']}\n"
                f"已打包 {result['files']} 个文件；这是普通 ZIP，不包含 API Key、Credential Locker 引用或 Provider 会话。"
            )
            self.migration_status.setText(f"导出完成：{result['archive']}")
            QMessageBox.information(
                self,
                "迁移包已生成",
                f"迁移压缩包已保存到：\n{result['archive']}\n\n换设备时在此页面选择“导入迁移压缩包”。",
            )
        elif action == "import":
            self.import_button.setEnabled(True)
            self.import_button.setText("在新设备导入数据包")
            target = Path(result["target"])
            try:
                pointer = write_next_launch_data_root_pointer(self.data_root, target)
            except MigrationError as exc:
                self.report.appendPlainText(
                    f"导入完成：{target}（{result['files']} 个文件），但下一次启动指针保存失败：{exc}"
                )
                self.migration_status.setText(f"导入完成：{target}；指针保存失败：{exc}")
                QMessageBox.warning(
                    self,
                    "迁移包已导入，但需要手动指定目录",
                    f"数据已导入到：\n{target}\n\n下一次启动时请手动将本地数据目录指向该目录。",
                )
                return
            self.report.appendPlainText(
                f"导入完成：{target}（{result['files']} 个文件）\n"
                f"下一次启动数据目录指针：{pointer}"
            )
            self.migration_status.setText(f"导入完成：{target}；下一次启动将使用该目录。")
            QMessageBox.information(
                self,
                "迁移包已导入",
                f"数据已导入到：\n{target}\n\n"
                f"已保存下一次启动数据目录指针：\n{pointer}\n\n"
                "请退出软件后重新启动 MentorFlow；当前运行中的数据目录不会被切换或覆盖。",
            )
        elif action == "backup":
            self.last_backup = Path(result)
            self.backup_button.setEnabled(True)
            self.backup_button.setText("创建安全备份")
            self.open_backup_button.setEnabled(True)
            self.diagnostics_status.setText(f"安全备份已创建：{self.last_backup}")
            self.report.appendPlainText(
                f"\n备份完成：{self.last_backup}\n"
                "其中包含 SHA-256 清单、SQLite 完整性与行数报告、以及 RESTORE.txt。"
            )

    def backup(self) -> None:
        self.backup_button.setEnabled(False)
        self.backup_button.setText("正在创建……")
        self.diagnostics_status.setText("正在创建带 SHA-256 清单的安全备份……")
        self.report.appendPlainText("\n正在创建备份，请稍候……")
        if not self._start_data_action("backup", create_manual_backup):
            self.backup_button.setEnabled(True)
            self.backup_button.setText("创建安全备份")
            return

    def open_backup(self) -> None:
        if self.last_backup:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_backup)))
            self.diagnostics_status.setText(
                "已打开最近备份目录。" if opened else "无法打开备份目录，请从诊断报告复制路径。"
            )
