"""User-visible Account and Data settings for the active local Vault."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from PySide6.QtCore import Qt, Signal
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
)
from .account_vault import (
    AccountVaultManager,
    AssetAvailability,
    AssetRecord,
)
from .enterprise_oidc import (
    EnterpriseOidcLoginResult,
    login_enterprise_account_from_env,
)
from .runtime import (
    enterprise_account_server_state,
    record_enterprise_account_session,
    startup_restore_failure,
)


AVAILABILITY_TEXT = {
    AssetAvailability.AVAILABLE_LOCAL: "AVAILABLE_LOCAL · 本机可用",
    AssetAvailability.MISSING_ON_DEVICE: (
        "MISSING_ON_THIS_DEVICE · 当前设备缺少原文件"
    ),
    AssetAvailability.LINKED_FILE_MISSING: "LINKED_FILE_MISSING · 请重新关联",
    AssetAvailability.REDOWNLOAD_AVAILABLE: "REDOWNLOAD_AVAILABLE · 可重新下载",
    AssetAvailability.RESTORABLE_FROM_PACKAGE: (
        "RESTORABLE_FROM_PACKAGE · 可从恢复包恢复"
    ),
}


class AccountSettingsPage(QWidget):
    _oidc_succeeded = Signal(object)
    _oidc_failed = Signal(str)

    def __init__(
        self,
        *,
        profile: AccountProfile,
        vault_manager: AccountVaultManager,
        credential_vault: AccountCredentialVault,
        switch_account: Callable[[AccountIdentity], object],
        logout_account: Callable[[], None],
        oidc_login: Callable[[], EnterpriseOidcLoginResult] | None = None,
    ) -> None:
        super().__init__()
        self.profile = profile
        self.vault_manager = vault_manager
        self.credential_vault = credential_vault
        self.switch_account = switch_account
        self.logout_account = logout_account
        self.oidc_login = oidc_login or login_enterprise_account_from_env
        self._synced_resource_count = 0
        self._oidc_thread: threading.Thread | None = None
        self._oidc_succeeded.connect(self._finish_external_oauth)
        self._oidc_failed.connect(self._external_oauth_failed)

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
        self.device_state_label = QLabel()
        self.device_state_label.setObjectName("currentDeviceAccountState")
        self.device_state_label.setWordWrap(True)
        card_layout.addWidget(self.profile_label)
        card_layout.addWidget(self.account_label)
        card_layout.addWidget(self.data_root_label)
        card_layout.addWidget(self.device_state_label)
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
        self.oauth_button = QPushButton("使用系统浏览器登录或切换企业账号")
        self.oauth_button.setObjectName("externalBrowserOAuthButton")
        self.oauth_button.clicked.connect(self._begin_external_oauth)
        account_actions.addWidget(self.oauth_button)
        self.logout_button = QPushButton("退出当前企业账号")
        self.logout_button.setObjectName("accountLogoutButton")
        self.logout_button.clicked.connect(self.logout_account)
        account_actions.addWidget(self.logout_button)
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
        self._synced_resource_count = 0
        self.refresh()

    def set_synced_resource_count(self, count: int) -> None:
        """Present E1-05 metadata without retaining a server Manifest in the UI."""

        self._synced_resource_count = max(0, int(count))
        current = self.vault_manager.current
        local_asset_count = (
            len(current.list_assets())
            if current is not None and current.profile == self.profile
            else None
        )
        self._render_device_state(local_asset_count)

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
            self._render_device_state(None)
            self.status.setText(
                startup_restore_failure()
                or "Vault 尚未挂载；导入资料时会安全挂载当前账号目录。"
            )
            return
        self._render_assets(current.list_assets())

    def _render_assets(self, assets: list[AssetRecord]) -> None:
        current = self.vault_manager.current
        self._render_device_state(len(assets))
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
        message = "当前列表仅来自已挂载账号的本机 Vault；不会枚举其他账号目录。"
        restore_failure = startup_restore_failure()
        if restore_failure:
            message = f"{restore_failure} {message}"
        server_state = enterprise_account_server_state(self.profile)
        context = server_state.get("context")
        if self.profile.mode is ProfileMode.ENTERPRISE_ACCOUNT and isinstance(
            context,
            Mapping,
        ):
            organization = str(context.get("organization_id") or "").strip()
            workspace = str(context.get("workspace_id") or "").strip()
            if organization and workspace:
                message += f" 服务器账号状态已刷新：{organization} / {workspace}。"
        self.status.setText(message)

    def _render_device_state(self, local_asset_count: int | None) -> None:
        if self.profile.mode is ProfileMode.PERSONAL_LOCAL:
            self.device_state_label.clear()
            self.device_state_label.hide()
            return
        self.device_state_label.show()
        if local_asset_count is None:
            message = "当前设备正在等待此企业账号的本机 Vault。"
        elif local_asset_count == 0 and self._synced_resource_count:
            message = (
                "NEW_DEVICE · 账号元数据已恢复；原始资料未保存在此设备，"
                "请在“本地资料”中重新关联。"
            )
        elif local_asset_count == 0:
            message = "当前设备尚无此企业账号的本地资料。"
        else:
            message = f"当前设备已载入此企业账号的 {local_asset_count} 份本地资料。"
        self.device_state_label.setText(message)

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
        if self._oidc_thread is not None and self._oidc_thread.is_alive():
            return
        self.oauth_button.setEnabled(False)
        self.logout_button.setEnabled(False)
        self.status.setText(
            "正在系统默认浏览器中打开企业登录；完成授权后会自动返回 MentorFlow。"
        )

        def run() -> None:
            try:
                self._oidc_succeeded.emit(self.oidc_login())
            except Exception:
                self._oidc_failed.emit("企业登录未能安全完成，请检查配置后重试。")

        self._oidc_thread = threading.Thread(
            target=run,
            name="mentorflow-enterprise-oidc",
            daemon=True,
        )
        self._oidc_thread.start()

    def _finish_external_oauth(self, result: object) -> None:
        self.oauth_button.setEnabled(True)
        self.logout_button.setEnabled(True)
        if not isinstance(result, EnterpriseOidcLoginResult):
            self._external_oauth_failed("企业登录返回了无效结果。")
            return
        try:
            self.complete_external_oauth(
                result.identity,
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                server_state=result.server_state,
            )
        except Exception:
            self._external_oauth_failed("企业账号切换未能安全完成。")

    def _external_oauth_failed(self, message: str) -> None:
        self.oauth_button.setEnabled(True)
        self.logout_button.setEnabled(True)
        self.status.setText(str(message or "企业登录未能安全完成。"))

    def complete_external_oauth(
        self,
        identity: AccountIdentity,
        *,
        access_token: str,
        refresh_token: str,
        server_state: Mapping[str, object] | None = None,
    ) -> object:
        self.credential_vault.store_oauth_tokens(
            identity,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        if server_state is not None:
            record_enterprise_account_session(identity.profile(), server_state)
        try:
            return self.switch_account(identity)
        except BaseException:
            self.credential_vault.logout(identity.profile())
            raise


__all__ = ["AVAILABILITY_TEXT", "AccountSettingsPage"]
