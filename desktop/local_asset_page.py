"""Readable Windows Desktop UI for current-account local asset recovery."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .local_asset_lifecycle import (
    LocalAssetLifecycle,
    LocalAssetLifecycleError,
    LocalAssetView,
)


STATE_LABELS = {
    "AVAILABLE_LOCAL": "本机可用",
    "MISSING_ON_DEVICE": "MISSING_ON_THIS_DEVICE · 当前设备缺少原文件",
    "MISSING_ON_THIS_DEVICE": "当前设备缺少原文件",
    "LINKED_FILE_MISSING": "链接文件缺失",
    "REDOWNLOAD_AVAILABLE": "可从已验证来源重新下载",
    "RESTORABLE_FROM_PACKAGE": "可从恢复包还原",
}

STATE_CODES = {
    "MISSING_ON_DEVICE": "MISSING_ON_THIS_DEVICE",
}

ACTION_LABELS = {
    "relink": "重新关联",
    "redownload": "重新下载",
    "restore": "从恢复包还原",
}


@dataclass(frozen=True, slots=True)
class AccountResourceRow:
    """Pathless presentation input from E1-05 or the existing local lifecycle."""

    resource_id: str
    filename: str
    size_bytes: int
    source_kind: str
    state: str
    actions: tuple[str, ...]

    @classmethod
    def from_local_asset(cls, asset: LocalAssetView) -> "AccountResourceRow":
        return cls(
            resource_id=asset.local_asset_id,
            filename=asset.filename,
            size_bytes=asset.size_bytes,
            source_kind=asset.source_kind,
            state=asset.availability.value,
            actions=asset.actions,
        )


def safe_lifecycle_message(error: BaseException) -> str:
    if isinstance(error, LocalAssetLifecycleError):
        return error.user_message
    service_message = getattr(error, "user_message", "")
    if isinstance(service_message, str) and service_message.strip():
        return service_message.strip()
    return "操作未完成。请检查文件是否仍在当前设备，或选择可用的恢复方式。"


class LocalAssetPage(QWidget):
    def __init__(
        self,
        lifecycle: LocalAssetLifecycle,
        *,
        list_resources: (
            Callable[[], Sequence[AccountResourceRow | LocalAssetView]] | None
        ) = None,
        relink_resource: Callable[[str, Path], object] | None = None,
    ) -> None:
        super().__init__()
        self.lifecycle = lifecycle
        self._list_resources = list_resources or lifecycle.list_assets
        self._relink_resource = relink_resource or lifecycle.relink
        layout = QVBoxLayout(self)
        layout.setContentsMargins(38, 32, 38, 32)
        layout.setSpacing(14)

        heading = QLabel("本地资料")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        detail = QLabel(
            "Managed Import 会复制到当前账号 Vault；Linked Import 只保留当前设备链接。"
        )
        detail.setObjectName("pageSubtitle")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        import_actions = QHBoxLayout()
        self.managed_import_button = QPushButton("Managed Import（推荐）")
        self.managed_import_button.setObjectName("primaryButton")
        self.managed_import_button.clicked.connect(self._import_managed)
        import_actions.addWidget(self.managed_import_button)
        self.linked_import_button = QPushButton("Linked Import（高级）")
        self.linked_import_button.clicked.connect(self._import_linked)
        import_actions.addWidget(self.linked_import_button)
        import_actions.addStretch(1)
        layout.addLayout(import_actions)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("文件", "存储方式", "本机状态", "大小", "可用操作"))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("pageSubtitle")
        layout.addWidget(self.status)
        self.refresh()

    def _pick_file(self, title: str, file_filter: str = "所有文件 (*.*)") -> Path | None:
        selected, _ = QFileDialog.getOpenFileName(self, title, "", file_filter)
        return Path(selected) if selected else None

    def _run(self, operation, success: str) -> None:
        try:
            operation()
        except BaseException as exc:
            self.status.setText(safe_lifecycle_message(exc))
            return
        self.status.setText(success)
        self.refresh(keep_status=True)

    def _import_managed(self) -> None:
        source = self._pick_file("选择要复制到当前账号 Vault 的文件")
        if source is not None:
            self._run(lambda: self.lifecycle.import_managed(source), "已复制到当前账号 Vault。")

    def _import_linked(self) -> None:
        source = self._pick_file("选择要保留本机链接的文件")
        if source is None:
            return
        confirmed = QMessageBox.question(
            self,
            "Linked Import",
            "Linked 资料不会复制，也不会进入便携包；源文件移动后需要重新关联。继续吗？",
        )
        if confirmed == QMessageBox.StandardButton.Yes:
            self._run(lambda: self.lifecycle.import_linked(source), "已保存当前设备链接（NOT_PORTABLE）。")

    def _recover(self, asset: AccountResourceRow, action: str) -> None:
        if action == "redownload":
            self._run(lambda: self.lifecycle.redownload(asset.resource_id), "已按原 SHA-256 恢复资料。")
            return
        if action == "restore":
            package = self._pick_file("选择当前账号恢复包", "MentorFlow 恢复包 (*.mentorflowpack)")
            if package is not None:
                self._run(
                    lambda: self.lifecycle.restore_from_package(asset.resource_id, package),
                    "已从恢复包还原，资料 revision 保持不变。",
                )
            return
        source = self._pick_file(f"重新关联 {asset.filename}")
        if source is not None:
            self._run(
                lambda: self._relink_resource(asset.resource_id, source),
                "已重新关联（RELINKED）；学习记录仍关联同一资源。",
            )

    @staticmethod
    def _text_item(value: str) -> QTableWidgetItem:
        item = QTableWidgetItem(value)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def refresh(self, *, keep_status: bool = False) -> None:
        try:
            assets = [
                resource
                if isinstance(resource, AccountResourceRow)
                else AccountResourceRow.from_local_asset(resource)
                for resource in self._list_resources()
            ]
        except BaseException as exc:
            self.table.setRowCount(0)
            self.status.setText(safe_lifecycle_message(exc))
            return
        if not keep_status:
            missing_count = sum(
                asset.state in {"MISSING_ON_DEVICE", "MISSING_ON_THIS_DEVICE"}
                for asset in assets
            )
            if missing_count:
                self.status.setText(
                    f"当前设备有 {missing_count} 份资料缺少本地文件；"
                    "服务器资源身份保持不变，请选择“重新关联”。"
                )
            else:
                self.status.setText("" if assets else "当前账号还没有本地资料。")
        self.table.setRowCount(len(assets))
        for row, asset in enumerate(assets):
            self.table.setItem(row, 0, self._text_item(asset.filename))
            source_label = "MANAGED" if asset.source_kind == "MANAGED" else "LINKED · NOT_PORTABLE"
            self.table.setItem(row, 1, self._text_item(source_label))
            state = asset.state
            display_state = STATE_CODES.get(state, state)
            self.table.setItem(
                row,
                2,
                self._text_item(
                    f"{display_state}\n{STATE_LABELS.get(state, '状态未知')}"
                ),
            )
            self.table.setItem(row, 3, self._text_item(f"{asset.size_bytes:,} B"))
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.setSpacing(6)
            for action in asset.actions:
                button = QPushButton(ACTION_LABELS[action])
                button.setObjectName(f"localAssetAction_{action}")
                button.setProperty("resource_id", asset.resource_id)
                button.setProperty("recovery_action", action)
                button.clicked.connect(
                    lambda _checked=False, current=asset, selected=action: self._recover(
                        current, selected
                    )
                )
                action_layout.addWidget(button)
            if not asset.actions:
                available = QLabel("无需恢复")
                action_layout.addWidget(available)
            action_layout.addStretch(1)
            self.table.setCellWidget(row, 4, actions)
        self.table.resizeRowsToContents()


__all__ = [
    "ACTION_LABELS",
    "AccountResourceRow",
    "LocalAssetPage",
    "STATE_CODES",
    "STATE_LABELS",
    "safe_lifecycle_message",
]
