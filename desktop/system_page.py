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
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from backend.app.credential_store import CredentialStoreError, store_secret
from backend.app.sms_gateway import SmsGatewayError, public_config, save_config
from .local_migration import MigrationError, export_migration_archive, import_migration_archive
from .version import VERSION_MANIFEST, version_display


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_ROOT = Path(os.getenv("MENTORFLOW_BACKUP_ROOT", r"E:\Codex\Data\backups\MentorFlow"))


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
    lines.extend(["", "本地运行说明"] if local_mode else ["", "安全说明"])
    if local_mode:
        lines.extend(
            [
                "- 当前为纯本地文件模式，数据来自当前数据目录；没有数据时会初始化新的学习空间。",
                "- API Key 只保存在 Windows Credential Locker，迁移包不包含 Key；换设备后需重新填写。",
                "- 导入迁移包必须使用新目录或空目录，软件不会覆盖正在使用的数据。",
            ]
        )
    else:
        lines.extend(
            [
                "- 密码保存为 Argon2id 哈希，无法从数据库反推出原密码。",
                "- 登录会话只保存 SHA-256 令牌哈希。",
                "- Provider Key 由 Windows Credential Locker 保存，不在上述数据库中。",
                "- 恢复备份前必须先退出 MentorFlow，避免覆盖正在使用的数据库。",
            ]
        )
    return "\n".join(lines)


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
        user: dict,
        origin: str,
        logout: Callable[[], None],
        api: Any,
        open_section: Callable[[str], None] | None = None,
        restart_tutorial: Callable[[], None] | None = None,
        set_online_updates: Callable[[bool], None] | None = None,
        reload_official_updates: Callable[[], None] | None = None,
        online_updates_enabled: bool = False,
    ) -> None:
        super().__init__()
        self.user = user
        self.origin = origin
        self.logout_callback = logout
        self.api = api
        self.open_section = open_section
        self.restart_tutorial_callback = restart_tutorial
        self.set_online_updates_callback = set_online_updates
        self.reload_official_updates_callback = reload_official_updates
        self.online_updates_enabled = bool(online_updates_enabled)
        self.last_backup: Path | None = None
        self.data_root = self._resolve_data_root()
        self._build()

    @staticmethod
    def _resolve_data_root() -> Path:
        configured = os.getenv("MENTORFLOW_USER_DATA_ROOT", "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        database_path = os.getenv("MENTORFLOW_ORM_DB", "").strip() or os.getenv("MENTORFLOW_DB", "").strip()
        if database_path:
            return Path(database_path).expanduser().resolve().parent.parent
        return Path(r"E:\MentorFlowData").resolve()

    def _build(self) -> None:
        local_mode = os.getenv("MENTORFLOW_LOCAL_DATA_MODE", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        layout = QVBoxLayout(self)
        layout.setContentsMargins(38, 32, 38, 32)
        heading = QLabel("设置")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        subtitle = QLabel(
            "本地模式不需要登录：软件会自动读取数据目录；没有数据时会初始化新的学习空间。"
            if local_mode
            else "你不需要先学会 SQLite 命令：软件会只读检查数据库，并用 SQLite 在线备份 API 创建可验证副本。"
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
                f"版本：{version_display(VERSION_MANIFEST)} · 构建：本地原生版 · "
                "数据架构：主动教学 v1"
            )
        )
        common_hint = QLabel("这里只保留日常会用到的入口；数据迁移、在线动态和技术诊断分别放在下方。")
        common_hint.setObjectName("pageSubtitle")
        common_hint.setWordWrap(True)
        settings_layout.addWidget(common_hint)
        settings_actions = QHBoxLayout()
        provider = QPushButton("打开模型连接")
        provider.clicked.connect(lambda: self._open_section("provider"))
        settings_actions.addWidget(provider)
        tutorial = QPushButton("重新播放新手引导")
        tutorial.clicked.connect(self._restart_tutorial)
        settings_actions.addWidget(tutorial)
        settings_actions.addStretch()
        settings_layout.addLayout(settings_actions)
        self.settings_status = QLabel("可以在这里查看版本或进入模型连接。")
        self.settings_status.setObjectName("inlineStatus")
        self.settings_status.setWordWrap(True)
        settings_layout.addWidget(self.settings_status)
        layout.addWidget(settings_card)

        online_card = QFrame()
        online_card.setObjectName("explanationCard")
        online_layout = QVBoxLayout(online_card)
        online_layout.setSpacing(10)
        online_layout.addWidget(QLabel("官方动态（可选）"))
        online_hint = QLabel(
            "默认完全离线。开启后只访问应用内列出的官方技术来源，并把最新结果缓存在本地。"
        )
        online_hint.setWordWrap(True)
        online_layout.addWidget(online_hint)
        online_actions = QHBoxLayout()
        online_toggle = QCheckBox("允许加载官方在线更新（默认关闭）")
        self.online_toggle = online_toggle
        online_toggle.blockSignals(True)
        online_toggle.setChecked(self.online_updates_enabled)
        online_toggle.blockSignals(False)
        online_toggle.toggled.connect(self._set_online_updates)
        online_actions.addWidget(online_toggle)
        reload_updates = QPushButton("立即刷新官方动态")
        self.reload_updates_button = reload_updates
        reload_updates.setEnabled(self.online_updates_enabled)
        reload_updates.clicked.connect(self._reload_official_updates)
        online_actions.addWidget(reload_updates)
        online_actions.addStretch()
        online_layout.addLayout(online_actions)
        self.online_progress = QProgressBar()
        self.online_progress.setRange(0, 0)
        self.online_progress.setTextVisible(False)
        self.online_progress.setMaximumHeight(5)
        self.online_progress.setVisible(False)
        online_layout.addWidget(self.online_progress)
        self.online_status = QLabel(
            "在线动态已开启，点击“立即刷新”获取内容。"
            if self.online_updates_enabled
            else "在线动态已关闭，不会发起网络请求。"
        )
        self.online_status.setObjectName("inlineStatus")
        self.online_status.setWordWrap(True)
        online_layout.addWidget(self.online_status)
        layout.addWidget(online_card)

        if local_mode:
            local_card = QFrame()
            local_card.setObjectName("explanationCard")
            local_layout = QVBoxLayout(local_card)
            local_layout.setSpacing(10)
            local_layout.addWidget(QLabel("换设备：导出或导入本地数据"))
            configured_root = os.getenv("MENTORFLOW_USER_DATA_ROOT", "").strip()
            database_path = os.getenv("MENTORFLOW_ORM_DB", "").strip() or os.getenv("MENTORFLOW_DB", "").strip()
            if configured_root:
                root_text = configured_root
            elif database_path:
                root_text = str(Path(database_path).resolve().parent.parent)
            else:
                root_text = "由当前本地运行配置决定"
            local_layout.addWidget(QLabel("当前数据目录：" + root_text))
            package_note = QLabel(
                "软件安装包和用户数据包是两件东西：安装包可以发给任何人，迁移压缩包只属于当前本地学习空间。"
            )
            package_note.setObjectName("inlineStatus")
            package_note.setWordWrap(True)
            local_layout.addWidget(package_note)
            migration_help = QLabel(
                "换设备：①点击导出并选择保存位置；②在新设备单独安装软件；③首次打开后选择压缩包和一个空目录导入。"
                "导入不会覆盖当前正在使用的数据，也不会把别人的学习内容混进来；API Key 不会写入压缩包。"
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

        account = password = phone = sms = None
        if not local_mode:
            account = QFrame()
            account.setObjectName("explanationCard")
            account_layout = QVBoxLayout(account)
            account_layout.addWidget(QLabel(f"当前账户：{self.user['display_name']}（@{self.user['username']}）"))
            account_layout.addWidget(QLabel(f"账户 ID：{self.user['id']}"))
            account_layout.addWidget(QLabel("账户表保存密码哈希；会话表保存令牌哈希；归属表连接账户与学习任务。"))
            account_layout.addWidget(QLabel("找回密码：只有完成手机号绑定和短信验证的账户才具备找回资格；未绑定时无法找回。"))
            layout.addWidget(account)

            password = QFrame()
            password.setObjectName("explanationCard")
            password_layout = QVBoxLayout(password)
            password_layout.addWidget(QLabel("修改密码（需要当前密码；修改后其他设备会话立即失效）"))
            self.password_current = QLineEdit()
            self.password_current.setPlaceholderText("当前密码")
            self.password_current.setEchoMode(QLineEdit.EchoMode.Password)
            password_layout.addWidget(self.password_current)
            self.password_new = QLineEdit()
            self.password_new.setPlaceholderText("新密码：至少 10 位并包含三类字符")
            self.password_new.setEchoMode(QLineEdit.EchoMode.Password)
            password_layout.addWidget(self.password_new)
            self.password_confirm = QLineEdit()
            self.password_confirm.setPlaceholderText("再次输入密码")
            self.password_confirm.setEchoMode(QLineEdit.EchoMode.Password)
            password_layout.addWidget(self.password_confirm)
            change_password = QPushButton("更新密码")
            change_password.clicked.connect(self.change_password)
            password_layout.addWidget(change_password)
            layout.addWidget(password)

            phone = QFrame()
            phone.setObjectName("explanationCard")
            phone_layout = QVBoxLayout(phone)
            phone_layout.addWidget(QLabel("绑定手机号（必须使用带国家码的 E.164 格式）"))
            self.phone_state = QLabel("正在读取手机号状态……")
            self.phone_state.setWordWrap(True)
            phone_layout.addWidget(self.phone_state)
            self.phone_input = QLineEdit()
            self.phone_input.setPlaceholderText("手机号，例如 +8613812345678")
            phone_layout.addWidget(self.phone_input)
            self.phone_current_password = QLineEdit()
            self.phone_current_password.setPlaceholderText("当前密码（绑定前重新验证）")
            self.phone_current_password.setEchoMode(QLineEdit.EchoMode.Password)
            phone_layout.addWidget(self.phone_current_password)
            phone_actions = QHBoxLayout()
            request_phone = QPushButton("发送绑定验证码")
            request_phone.clicked.connect(self.request_phone_binding)
            phone_actions.addWidget(request_phone)
            self.phone_code = QLineEdit()
            self.phone_code.setPlaceholderText("6 位验证码")
            phone_actions.addWidget(self.phone_code)
            confirm_phone = QPushButton("确认绑定")
            confirm_phone.clicked.connect(self.confirm_phone_binding)
            phone_actions.addWidget(confirm_phone)
            phone_layout.addLayout(phone_actions)
            layout.addWidget(phone)

            sms = QFrame()
            sms.setObjectName("explanationCard")
            sms_layout = QVBoxLayout(sms)
            sms_layout.addWidget(QLabel("短信网关设置（只保存 HTTPS 地址、路径和 Credential Locker 引用）"))
            self.sms_gateway_url = QLineEdit()
            self.sms_gateway_url.setPlaceholderText("HTTPS 网关地址，例如 https://sms.example.com")
            sms_layout.addWidget(self.sms_gateway_url)
            self.sms_gateway_path = QLineEdit()
            self.sms_gateway_path.setPlaceholderText("发送路径，例如 /v1/messages")
            sms_layout.addWidget(self.sms_gateway_path)
            self.sms_gateway_secret = QLineEdit()
            self.sms_gateway_secret.setPlaceholderText("短信网关密钥（只写入 Windows Credential Locker）")
            self.sms_gateway_secret.setEchoMode(QLineEdit.EchoMode.Password)
            sms_layout.addWidget(self.sms_gateway_secret)
            sms_actions = QHBoxLayout()
            save_sms = QPushButton("保存短信网关")
            save_sms.clicked.connect(self.save_sms_gateway)
            sms_actions.addWidget(save_sms)
            self.sms_gateway_state = QLabel()
            self.sms_gateway_state.setWordWrap(True)
            sms_actions.addWidget(self.sms_gateway_state, 1)
            sms_layout.addLayout(sms_actions)
            layout.addWidget(sms)

        diagnostics_card = QFrame()
        diagnostics_card.setObjectName("explanationCard")
        diagnostics_layout = QVBoxLayout(diagnostics_card)
        diagnostics_layout.setSpacing(10)
        diagnostics_layout.addWidget(QLabel("本地安全与技术诊断"))
        diagnostics_hint = QLabel(
            "平时无需打开。遇到数据或启动问题时，可检查数据库、复制报告，或创建带校验清单的内部备份。"
        )
        diagnostics_hint.setWordWrap(True)
        diagnostics_layout.addWidget(diagnostics_hint)
        self.diagnostics_toggle = QPushButton("查看技术诊断")
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
        copy = QPushButton("复制诊断信息")
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
        logout = QPushButton("退出登录")
        logout.clicked.connect(self.logout_callback)
        logout.setVisible(not local_mode)
        actions.addWidget(logout)
        diagnostics_layout.addWidget(self.diagnostics_actions)

        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMinimumHeight(230)
        diagnostics_layout.addWidget(self.report)
        self.diagnostics_actions.setVisible(False)
        self.report.setVisible(False)
        self.diagnostics_status = QLabel("技术诊断已收起。")
        self.diagnostics_status.setObjectName("inlineStatus")
        self.diagnostics_status.setWordWrap(True)
        diagnostics_layout.addWidget(self.diagnostics_status)
        layout.addWidget(diagnostics_card)
        layout.addStretch()
        self.refresh()
        if not local_mode:
            self.refresh_phone()
            self.refresh_sms_gateway()

    def refresh(self) -> None:
        if hasattr(self, "refresh_button"):
            self.refresh_button.setEnabled(False)
            self.refresh_button.setText("检查中……")
        if hasattr(self, "diagnostics_status"):
            self.diagnostics_status.setText("正在只读检查本地数据库……")
        QApplication.processEvents()
        try:
            local_mode = os.getenv("MENTORFLOW_LOCAL_DATA_MODE", "").strip().lower() in {"1", "true", "yes"}
            self.report.setPlainText(diagnostics_text(self.origin, local_mode=local_mode))
            if hasattr(self, "diagnostics_status"):
                self.diagnostics_status.setText("检查完成；数据库未被修改。")
        except (OSError, sqlite3.Error) as exc:
            self.report.setPlainText(f"诊断失败：{exc}")
            if hasattr(self, "diagnostics_status"):
                self.diagnostics_status.setText(f"检查失败：{exc}")
        finally:
            if hasattr(self, "refresh_button"):
                self.refresh_button.setEnabled(True)
                self.refresh_button.setText("重新检查")

    def _open_section(self, section: str) -> None:
        if self.open_section:
            if hasattr(self, "settings_status"):
                self.settings_status.setText("正在打开模型连接……")
            self.open_section(section)

    def _restart_tutorial(self) -> None:
        self.settings_status.setText("新手引导已重新开始；可按“跳过”随时退出。")
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
        self.diagnostics_toggle.setText("收起技术诊断" if visible else "查看技术诊断")
        self.diagnostics_status.setText(
            "技术诊断已展开；所有数据库检查均为只读。"
            if visible
            else "技术诊断已收起。"
        )

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
        QApplication.processEvents()
        try:
            result = export_migration_archive(self.data_root, output)
        except (MigrationError, OSError, sqlite3.Error) as exc:
            self.migration_status.setText(f"导出失败：{exc}")
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        finally:
            self.export_button.setEnabled(True)
            self.export_button.setText("导出我的数据包")
        self.report.appendPlainText(
            f"导出完成：{result['archive']}\n"
            f"已打包 {result['files']} 个文件；压缩包不包含 Windows Credential Locker 中的 API Key。"
        )
        self.migration_status.setText(f"导出完成：{result['archive']}")
        QMessageBox.information(
            self,
            "迁移包已生成",
            f"迁移压缩包已保存到：\n{result['archive']}\n\n换设备时在此页面选择“导入迁移压缩包”。",
        )

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
        QApplication.processEvents()
        try:
            result = import_migration_archive(Path(archive_name), target_path)
        except (MigrationError, OSError, ValueError) as exc:
            self.migration_status.setText(f"导入失败：{exc}")
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        finally:
            self.import_button.setEnabled(True)
            self.import_button.setText("在新设备导入数据包")
        self.report.appendPlainText(f"导入完成：{result['target']}（{result['files']} 个文件）")
        self.migration_status.setText(f"导入完成：{result['target']}")
        QMessageBox.information(
            self,
            "迁移包已导入",
            f"数据已导入到：\n{result['target']}\n\n请退出软件，并在新环境启动时将本地数据目录指向这个目录。",
        )

    def change_password(self) -> None:
        current = self.password_current.text()
        new = self.password_new.text()
        if new != self.password_confirm.text():
            QMessageBox.warning(self, "无法更新密码", "两次输入的新密码不一致。")
            return
        try:
            self.api.change_password(current, new)
        except Exception as exc:
            message = getattr(exc, "message", str(exc))
            QMessageBox.warning(self, "无法更新密码", message)
            return
        self.password_current.clear()
        self.password_new.clear()
        self.password_confirm.clear()
        QMessageBox.information(self, "密码已更新", "密码已更新，其他设备会话已失效。")

    def backup(self) -> None:
        self.backup_button.setEnabled(False)
        self.backup_button.setText("正在创建……")
        self.diagnostics_status.setText("正在创建带 SHA-256 清单的安全备份……")
        self.report.appendPlainText("\n正在创建备份，请稍候……")
        QApplication.processEvents()
        try:
            self.last_backup = create_manual_backup()
        except (OSError, RuntimeError) as exc:
            self.diagnostics_status.setText(f"备份失败：{exc}")
            QMessageBox.critical(self, "备份失败", str(exc))
            return
        finally:
            self.backup_button.setEnabled(True)
            self.backup_button.setText("创建安全备份")
        self.open_backup_button.setEnabled(True)
        self.diagnostics_status.setText(f"安全备份已创建：{self.last_backup}")
        self.report.appendPlainText(
            f"\n备份完成：{self.last_backup}\n"
            "其中包含 SHA-256 清单、SQLite 完整性与行数报告、以及 RESTORE.txt。"
        )

    def open_backup(self) -> None:
        if self.last_backup:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_backup)))
            self.diagnostics_status.setText(
                "已打开最近备份目录。" if opened else "无法打开备份目录，请从诊断报告复制路径。"
            )

    def refresh_phone(self) -> None:
        try:
            payload = self.api.phone_status()
        except Exception as exc:
            message = getattr(exc, "message", str(exc))
            self.phone_state.setText(f"手机号状态读取失败：{message}")
            return
        if payload.get("bound"):
            self.phone_state.setText(f"已绑定并验证：{payload.get('phone_masked') or '已隐藏'}。忘记密码时可接收短信验证码。")
        else:
            self.phone_state.setText("未绑定手机号。当前账户不能通过短信找回密码。")

    def request_phone_binding(self) -> None:
        try:
            payload = self.api.request_phone_binding(
                self.phone_input.text(), self.phone_current_password.text()
            )
        except Exception as exc:
            message = getattr(exc, "message", str(exc))
            QMessageBox.warning(self, "无法发送绑定验证码", message)
            return
        self._phone_challenge_id = str(payload.get("challenge_id") or "")
        QMessageBox.information(self, "验证码已发送", "如果短信服务配置正确，验证码已发送到该手机号；请在 10 分钟内完成确认。")

    def confirm_phone_binding(self) -> None:
        challenge_id = str(getattr(self, "_phone_challenge_id", ""))
        if not challenge_id:
            QMessageBox.warning(self, "无法确认绑定", "请先发送绑定验证码。")
            return
        try:
            self.api.confirm_phone_binding(challenge_id, self.phone_code.text())
        except Exception as exc:
            message = getattr(exc, "message", str(exc))
            QMessageBox.warning(self, "无法确认绑定", message)
            return
        self.phone_code.clear()
        self.phone_current_password.clear()
        QMessageBox.information(
            self,
            "手机号已绑定",
            "手机号已验证并绑定。以后忘记密码时，验证码会发送到这个手机号。",
        )
        self.refresh_phone()

    def refresh_sms_gateway(self) -> None:
        config = public_config()
        self.sms_gateway_url.setText(str(config.get("base_url") or ""))
        self.sms_gateway_path.setText(str(config.get("send_path") or "/v1/messages"))
        self.sms_gateway_state.setText(
            "已配置（密钥仅保留在 Credential Locker）" if config.get("configured") else "未配置；绑定手机号时不会伪造发送。"
        )

    def save_sms_gateway(self) -> None:
        current = public_config()
        try:
            credential_ref = str(current.get("credential_ref") or "")
            secret = self.sms_gateway_secret.text().strip()
            if secret:
                credential_ref = store_secret(secret, credential_ref or None)
            if not credential_ref:
                raise SmsGatewayError("SMS_CREDENTIAL_REQUIRED", "请输入短信网关密钥。")
            save_config(
                base_url=self.sms_gateway_url.text(),
                send_path=self.sms_gateway_path.text(),
                credential_ref=credential_ref,
            )
        except (CredentialStoreError, SmsGatewayError) as exc:
            QMessageBox.warning(self, "无法保存短信网关", getattr(exc, "message", str(exc)))
            return
        self.sms_gateway_secret.clear()
        self.refresh_sms_gateway()
        QMessageBox.information(self, "短信网关已保存", "网关地址已保存，密钥只在 Windows Credential Locker 中保存。")
