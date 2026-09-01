"""User-visible Account and Data settings for the active local Vault."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .account_identity import (
    AccountCredentialVault,
    AccountIdentity,
    AccountProfile,
    ProfileMode,
    open_system_browser_oauth,
)
from .account_vault import (
    AccountVaultManager,
    AssetAvailability,
    AssetRecord,
)


AVAILABILITY_TEXT = {
    AssetAvailability.AVAILABLE_LOCAL: "AVAILABLE_LOCAL · 本机可用",
    AssetAvailability.MISSING_ON_DEVICE: "MISSING_ON_DEVICE · 本机缺少文件",
    AssetAvailability.LINKED_FILE_MISSING: "LINKED_FILE_MISSING · 请重新关联",
    AssetAvailability.REDOWNLOAD_AVAILABLE: "REDOWNLOAD_AVAILABLE · 可重新下载",
    AssetAvailability.RESTORABLE_FROM_PACKAGE: (
        "RESTORABLE_FROM_PACKAGE · 可从恢复包恢复"
    ),
}


class AccountSettingsPage(QWidget):
    def __init__(
        self,
        *,
        profile: AccountProfile,
        vault_manager: AccountVaultManager,
        credential_vault: AccountCredentialVault,
        switch_account: Callable[[AccountIdentity], object],
        logout_account: Callable[[], None],
    ) -> None:
        super().__init__()
        self.profile = profile
        self.vault_manager = vault_manager
        self.credential_vault = credential_vault
        self.switch_account = switch_account
        self.logout_account = logout_account

        layout = QVBoxLayout(self)
        layout.setContentsMargins(38, 32, 38, 32)
        layout.setSpacing(12)
        heading = QLabel("账号与本机数据")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        subtitle = QLabel(
            "Enterprise Account 的原始资料只保存在当前账号的本机 Vault；"
            "切换账号会关闭当前服务并重新启动 MentorFlow。"
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("settingsCard")
        card_layout = QVBoxLayout(card)
        self.profile_label = QLabel()
        self.profile_label.setObjectName("currentProfileValue")
        self.account_label = QLabel()
        self.account_label.setObjectName("currentAccountValue")
        self.data_root_label = QLabel()
        self.data_root_label.setObjectName("currentDataRootValue")
        self.data_root_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        card_layout.addWidget(self.profile_label)
        card_layout.addWidget(self.account_label)
        card_layout.addWidget(self.data_root_label)
        layout.addWidget(card)

        actions = QHBoxLayout()
        managed = QPushButton("Managed Import（复制到当前 Vault）")
        managed.setObjectName("managedImportButton")
        managed.clicked.connect(self._select_managed)
        actions.addWidget(managed)
        linked = QPushButton("Linked Import（仅本机引用）")
        linked.setObjectName("linkedImportButton")
        linked.clicked.connect(self._select_linked)
        actions.addWidget(linked)
        actions.addStretch()
        layout.addLayout(actions)

        self.assets = QTableWidget(0, 4)
        self.assets.setObjectName("accountVaultAssets")
        self.assets.setHorizontalHeaderLabels(
            ["资料", "导入方式", "本机状态", "可移植性"]
        )
        self.assets.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.assets, 1)

        account_actions = QHBoxLayout()
        oauth = QPushButton("使用系统浏览器登录或切换企业账号")
        oauth.setObjectName("externalBrowserOAuthButton")
        oauth.clicked.connect(self._begin_external_oauth)
        account_actions.addWidget(oauth)
        logout = QPushButton("退出当前企业账号")
        logout.setObjectName("accountLogoutButton")
        logout.clicked.connect(self.logout_account)
        account_actions.addWidget(logout)
        account_actions.addStretch()
        layout.addLayout(account_actions)

        self.status = QLabel()
        self.status.setObjectName("accountVaultStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    @property
    def current_data_root(self) -> Path:
        return self.vault_manager.layout.root_for_profile(self.profile)

    def set_profile(self, profile: AccountProfile) -> None:
        self.profile = profile
        self.refresh()

    def refresh(self) -> None:
        profile_name = (
            "Personal Local"
            if self.profile.mode is ProfileMode.PERSONAL_LOCAL
            else "Enterprise Account"
        )
        self.profile_label.setText(f"CURRENT_PROFILE = {profile_name}")
        self.account_label.setText(
            f"CURRENT_ACCOUNT = {self.profile.safe_account_label}"
        )
        self.data_root_label.setText(
            f"CURRENT_DATA_ROOT = {self.current_data_root}"
        )
        current = self.vault_manager.current
        if current is None or current.profile != self.profile:
            self.assets.setRowCount(0)
            self.status.setText("Vault 尚未挂载；导入资料时会安全挂载当前账号目录。")
            return
        self._render_assets(current.list_assets())

    def _render_assets(self, assets: list[AssetRecord]) -> None:
        current = self.vault_manager.current
        self.assets.setRowCount(len(assets))
        for row, asset in enumerate(assets):
            availability = (
                current.availability(asset)
                if current is not None
                else AssetAvailability.MISSING_ON_DEVICE
            )
            self.assets.setItem(row, 0, QTableWidgetItem(asset.display_name))
            self.assets.setItem(row, 1, QTableWidgetItem(asset.import_mode.value))
            self.assets.setItem(
                row,
                2,
                QTableWidgetItem(AVAILABILITY_TEXT[availability]),
            )
            self.assets.setItem(
                row,
                3,
                QTableWidgetItem(" / ".join(asset.portability_labels) or "VAULT_MANAGED"),
            )
        self.status.setText(
            "当前列表仅来自已挂载账号的本机 Vault；不会枚举其他账号目录。"
        )

    def _vault(self):
        current = self.vault_manager.current
        if current is None or current.profile != self.profile:
            current = self.vault_manager.mount(self.profile)
        return current

    def _select_managed(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(self, "选择要复制到 Vault 的资料")
        if not selected:
            return
        try:
            self._vault().import_managed(Path(selected))
        except (OSError, RuntimeError, ValueError):
            QMessageBox.warning(self, "无法导入", "所选文件无法安全复制到当前账号 Vault。")
            return
        self.refresh()

    def _select_linked(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(self, "选择仅在本机使用的关联资料")
        if not selected:
            return
        try:
            self._vault().import_linked(Path(selected))
        except (OSError, RuntimeError, ValueError):
            QMessageBox.warning(self, "无法关联", "所选文件不是可安全关联的本机文件。")
            return
        self.refresh()

    def _begin_external_oauth(self) -> None:
        authorization_url = os.getenv(
            "MENTORFLOW_OAUTH_AUTHORIZATION_URL",
            "",
        ).strip()
        if not authorization_url:
            self.status.setText(
                "尚未配置企业 OAuth 授权地址；MentorFlow 不会在应用内收集账号密码。"
            )
            return
        try:
            opened = open_system_browser_oauth(authorization_url)
        except ValueError:
            opened = False
        self.status.setText(
            "已在系统浏览器中打开登录；完成授权后请返回 MentorFlow。"
            if opened
            else "无法安全打开企业登录页面。"
        )

    def complete_external_oauth(
        self,
        identity: AccountIdentity,
        *,
        access_token: str,
        refresh_token: str,
    ) -> object:
        self.credential_vault.store_oauth_tokens(
            identity,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        return self.switch_account(identity)


__all__ = ["AVAILABILITY_TEXT", "AccountSettingsPage"]
