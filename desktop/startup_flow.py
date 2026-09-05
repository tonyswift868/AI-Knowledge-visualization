"""Login-first Account Core bootstrap and device recovery UI.

This module composes the existing OIDC, account metadata, Account Vault, and
local resource lifecycle contracts.  It intentionally owns no server cache and
never transfers local paths or resource bytes to Account Core.
"""

from __future__ import annotations

import copy
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import httpx
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .account_identity import AccountIdentity, AccountProfile
from .account_metadata_sync import (
    AccountMetadataSnapshot,
    AccountMetadataSyncError,
    AccountMetadataSyncService,
    ResourceDeviceState,
    SyncedResource,
)
from .account_vault import AccountVaultManager, VaultLayout
from .enterprise_oidc import DesktopOidcConfig, EnterpriseOidcLoginResult
from .local_asset_lifecycle import LocalAssetLifecycle
from .local_asset_lifecycle import LocalAssetLifecycleError
from .runtime import bind_desktop_profile_data_root


LOGIN_GATE_OBJECT_NAME = "mentorflowLoginGateWindow"
MIGRATION_CENTER_OBJECT_NAME = "mentorflowDeviceMigrationCenter"


class LoginGateState(str, Enum):
    LOGIN_READY = "LOGIN_READY"
    AUTHENTICATING = "AUTHENTICATING"
    AUTH_SUCCESS_BOOTSTRAPPING = "AUTH_SUCCESS_BOOTSTRAPPING"
    AUTH_FAILED = "AUTH_FAILED"


class DeviceBootstrapState(str, Enum):
    RETURNING_DEVICE_READY = "RETURNING_DEVICE_READY"
    RETURNING_DEVICE_PARTIAL = "RETURNING_DEVICE_PARTIAL"
    NEW_DEVICE_OR_EMPTY_LOCAL_STATE = "NEW_DEVICE_OR_EMPTY_LOCAL_STATE"


class RecoveryItemState(str, Enum):
    READY_ON_THIS_DEVICE = "READY_ON_THIS_DEVICE"
    MISSING_ON_THIS_DEVICE = "MISSING_ON_THIS_DEVICE"
    RELINK_REQUIRED = "RELINK_REQUIRED"
    REACQUIRE_AVAILABLE = "REACQUIRE_AVAILABLE"
    RESTORING = "RESTORING"
    RESTORED = "RESTORED"
    SKIPPED = "SKIPPED"


_RECOVERY_LABELS = {
    RecoveryItemState.READY_ON_THIS_DEVICE: "本机已就绪",
    RecoveryItemState.MISSING_ON_THIS_DEVICE: "此设备缺少",
    RecoveryItemState.RELINK_REQUIRED: "需要重新关联",
    RecoveryItemState.REACQUIRE_AVAILABLE: "可以重新获取",
    RecoveryItemState.RESTORING: "正在恢复",
    RecoveryItemState.RESTORED: "已恢复",
    RecoveryItemState.SKIPPED: "稍后处理",
}


class AccountBootstrapError(RuntimeError):
    def __init__(self, code: str, user_message: str) -> None:
        self.code = str(code or "ACCOUNT_BOOTSTRAP_FAILED")
        self.user_message = str(user_message or "账号数据暂时无法恢复，请稍后重试。")
        super().__init__(self.code)


class EnterpriseAccountMetadataApi:
    """One-token adapter for the existing Account Core metadata endpoint."""

    def __init__(
        self,
        config: DesktopOidcConfig,
        identity: AccountIdentity,
        access_token: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._identity = identity
        self._access_token = str(access_token or "")
        if not self._access_token:
            raise AccountBootstrapError(
                "ACCOUNT_ACCESS_TOKEN_REQUIRED",
                "登录状态无效，请重新登录。",
            )
        self._client = client or httpx.Client(
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._cached_metadata: dict[str, Any] | None = None
        self.metadata_network_calls = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }
        if self._identity.organization_id:
            headers["X-MentorFlow-Organization-ID"] = self._identity.organization_id
        if self._identity.workspace_id:
            headers["X-MentorFlow-Workspace-ID"] = self._identity.workspace_id
        return headers

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AccountBootstrapError(
                "ACCOUNT_METADATA_INVALID",
                "账号数据响应无法验证，请稍后重试。",
            ) from exc
        if not isinstance(payload, dict):
            raise AccountBootstrapError(
                "ACCOUNT_METADATA_INVALID",
                "账号数据响应无法验证，请稍后重试。",
            )
        return payload

    def account_metadata(self) -> Mapping[str, Any]:
        if self._cached_metadata is not None:
            return copy.deepcopy(self._cached_metadata)
        try:
            response = self._client.get(
                f"{self._config.api_base_url}/api/enterprise/account/metadata",
                headers=self._headers(),
            )
            self.metadata_network_calls += 1
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise AccountBootstrapError(
                    "ACCOUNT_WORKSPACE_UNAVAILABLE",
                    "此账号的学习空间暂不可用，请联系空间管理员后重试。",
                ) from exc
            if exc.response.status_code in {401, 403}:
                raise AccountBootstrapError(
                    "ACCOUNT_METADATA_ACCESS_DENIED",
                    "当前账号无法访问这个学习空间，请确认账号与空间配置后重新登录。",
                ) from exc
            raise AccountBootstrapError(
                "ACCOUNT_METADATA_UNAVAILABLE",
                "账号服务暂时无法提供学习数据，请稍后重试。",
            ) from exc
        except httpx.HTTPError as exc:
            raise AccountBootstrapError(
                "ACCOUNT_METADATA_UNAVAILABLE",
                "暂时无法连接账号服务，请检查网络后重试。",
            ) from exc
        self._cached_metadata = self._json_object(response)
        return copy.deepcopy(self._cached_metadata)

    def put_account_progress(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        try:
            response = self._client.put(
                f"{self._config.api_base_url}/api/enterprise/account/metadata/progress",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AccountBootstrapError(
                "ACCOUNT_PROGRESS_UNAVAILABLE",
                "学习进度暂时无法同步，请稍后重试。",
            ) from exc
        return self._json_object(response)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class _PreloadedMetadataApi:
    """Feed bootstrap's already-fetched snapshot into the existing sync service."""

    def __init__(self, payload: Mapping[str, Any], upstream: Any) -> None:
        self._payload = copy.deepcopy(dict(payload))
        self._upstream = upstream

    def account_metadata(self) -> Mapping[str, Any]:
        return copy.deepcopy(self._payload)

    def put_account_progress(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        return self._upstream.put_account_progress(payload)


@dataclass(slots=True)
class AccountBootstrapResult:
    identity: AccountIdentity
    profile: AccountProfile
    server_state: Mapping[str, Any]
    snapshot: AccountMetadataSnapshot
    vault_manager: AccountVaultManager
    local_asset_lifecycle: LocalAssetLifecycle
    metadata_sync: AccountMetadataSyncService
    metadata_api: EnterpriseAccountMetadataApi
    device_state: DeviceBootstrapState

    @property
    def resource_count(self) -> int:
        return len(self.snapshot.resources)

    @property
    def available_count(self) -> int:
        return sum(
            item.state in {ResourceDeviceState.AVAILABLE, ResourceDeviceState.RELINKED}
            for item in self.snapshot.resources
        )

    @property
    def missing_count(self) -> int:
        return sum(
            item.state is ResourceDeviceState.MISSING_ON_THIS_DEVICE
            for item in self.snapshot.resources
        )

    @property
    def main_user(self) -> dict[str, Any]:
        account = self.server_state.get("account")
        safe = dict(account) if isinstance(account, Mapping) else {}
        return {
            "id": self.identity.subject,
            "subject": self.identity.subject,
            "issuer": self.identity.issuer,
            "username": str(safe.get("preferred_username") or self.identity.subject),
            "display_name": str(safe.get("display_name") or self.identity.display_name),
            "organization_id": self.identity.organization_id,
            "workspace_id": self.identity.workspace_id,
        }

    def apply_snapshot(self, snapshot: AccountMetadataSnapshot) -> None:
        self.snapshot = snapshot
        self.device_state = classify_device(snapshot)

    def close(self) -> None:
        self.metadata_api.close()
        self.vault_manager.unmount()


def classify_device(snapshot: AccountMetadataSnapshot) -> DeviceBootstrapState:
    resources = tuple(snapshot.resources)
    if not resources:
        return DeviceBootstrapState.RETURNING_DEVICE_READY
    available = sum(
        item.state in {ResourceDeviceState.AVAILABLE, ResourceDeviceState.RELINKED}
        for item in resources
    )
    if available == len(resources):
        return DeviceBootstrapState.RETURNING_DEVICE_READY
    if available == 0:
        return DeviceBootstrapState.NEW_DEVICE_OR_EMPTY_LOCAL_STATE
    return DeviceBootstrapState.RETURNING_DEVICE_PARTIAL


def bootstrap_enterprise_account(
    login: EnterpriseOidcLoginResult,
    *,
    config: DesktopOidcConfig | None = None,
    vault_root: Path | None = None,
    metadata_api: EnterpriseAccountMetadataApi | None = None,
) -> AccountBootstrapResult:
    """Hydrate server metadata while opening the account Vault concurrently."""

    identity = login.identity
    profile = identity.profile()
    oidc_config = config or DesktopOidcConfig.from_env()
    manager = AccountVaultManager(
        VaultLayout(
            vault_root
            or Path(os.getenv("MENTORFLOW_ACCOUNT_VAULT_ROOT", "E:/MentorFlowData"))
        )
    )
    api = metadata_api or EnterpriseAccountMetadataApi(
        oidc_config,
        identity,
        login.access_token,
    )

    def open_local_state() -> LocalAssetLifecycle:
        vault = manager.mount(profile)
        return LocalAssetLifecycle(
            vault,
            account_identity={
                "issuer": identity.issuer,
                "subject": identity.subject,
            },
        )

    try:
        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="mentorflow-account-bootstrap",
        ) as executor:
            local_future = executor.submit(open_local_state)
            metadata_future = executor.submit(api.account_metadata)
            lifecycle = local_future.result()
            metadata = metadata_future.result()
        sync = AccountMetadataSyncService(
            _PreloadedMetadataApi(metadata, api),
            lifecycle,
            account_identity=identity,
        )
        snapshot = sync.sync()
        # Environment binding belongs to the UI generation acceptance gate.
        # A late A bootstrap must never redirect B's process-global paths.
        return AccountBootstrapResult(
            identity=identity,
            profile=profile,
            server_state=copy.deepcopy(dict(login.server_state)),
            snapshot=snapshot,
            vault_manager=manager,
            local_asset_lifecycle=lifecycle,
            metadata_sync=sync,
            metadata_api=api,
            device_state=classify_device(snapshot),
        )
    except AccountBootstrapError:
        manager.unmount()
        api.close()
        raise
    except AccountMetadataSyncError as exc:
        manager.unmount()
        api.close()
        raise AccountBootstrapError(exc.code, exc.user_message) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        manager.unmount()
        api.close()
        raise AccountBootstrapError(
            "ACCOUNT_LOCAL_STATE_UNAVAILABLE",
            "此账号的本机数据暂时无法打开，请检查磁盘后重试。",
        ) from exc


class LoginGateWindow(QWidget):
    login_requested = Signal()
    retry_requested = Signal()
    background_succeeded = Signal(str, object)
    background_failed = Signal(str, str)
    cancel_requested = Signal()
    closed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(LOGIN_GATE_OBJECT_NAME)
        self.setWindowTitle("登录 MentorFlow")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # Reserve the provider form viewport from first paint, including the
        # native consent controls. Loading/form/refresh never resize the shell.
        area = self.screen().availableGeometry()
        available_width = max(240, area.width() - 48)
        available_height = max(320, area.height() - 80)
        self.resize(min(480, available_width), min(740, available_height))
        self.setMinimumSize(min(440, available_width), min(680, available_height))
        self.state = LoginGateState.LOGIN_READY
        self._retry_mode = False
        self._centered = False
        self._build()
        self.set_state(LoginGateState.LOGIN_READY)

    def _build(self) -> None:
        self.setStyleSheet(
            "QWidget#mentorflowLoginGateWindow { background: #fff9fc; }"
            "QFrame#loginBrandMark { background: #3b2b3f; border-radius: 32px; }"
            "QLabel#loginLeaf { color: white; font-size: 34px; background: transparent; }"
            "QLabel#loginTitle { color: #3b2b3f; font-size: 30px; font-weight: 800; }"
            "QLabel#loginSubtitle { color: #786572; font-size: 14px; }"
            "QLabel#loginStateTitle { color: #8d3f62; font-size: 18px; font-weight: 750; }"
            "QLabel#loginStateDetail { color: #786572; }"
            "QFrame#loginCard { background: #fffdfd; border: 1px solid #edc2d3; border-radius: 18px; }"
            "QPushButton#loginPrimaryButton { min-height: 52px; color: white; background: #c95f8d; border: 0; border-radius: 12px; font-weight: 750; }"
            "QPushButton#loginPrimaryButton:hover { background: #ae4d78; }"
            "QPushButton#loginPrimaryButton:disabled { background: #d9b7c6; color: #fff7fa; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(14)
        self.brand_widgets = []
        brand = QFrame()
        brand.setObjectName("loginBrandMark")
        brand.setFixedSize(64, 64)
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(0, 0, 0, 0)
        leaf = QLabel("🌿")
        leaf.setObjectName("loginLeaf")
        leaf.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_row.addWidget(leaf)
        brand_wrap = QHBoxLayout()
        brand_wrap.addStretch(1)
        brand_wrap.addWidget(brand)
        brand_wrap.addStretch(1)
        outer.addLayout(brand_wrap)
        self.brand_widgets.append(brand)
        title = QLabel("MentorFlow")
        title.setObjectName("loginTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)
        self.brand_widgets.append(title)
        subtitle = QLabel("回到属于你的学习空间")
        subtitle.setObjectName("loginSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(subtitle)
        self.brand_widgets.append(subtitle)
        outer.addSpacing(14)
        card = QFrame()
        card.setObjectName("loginCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(26, 24, 26, 24)
        card_layout.setSpacing(11)
        self.content_layout = card_layout
        self.auth_content = None
        self.state_title = QLabel()
        self.state_title.setTextFormat(Qt.TextFormat.PlainText)
        self.state_title.setObjectName("loginStateTitle")
        self.state_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.state_title)
        self.state_detail = QLabel()
        self.state_detail.setObjectName("loginStateDetail")
        self.state_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_detail.setWordWrap(True)
        self.state_detail.setMinimumHeight(54)
        card_layout.addWidget(self.state_detail)
        self.primary_button = QPushButton("登录 MentorFlow")
        self.primary_button.setObjectName("loginPrimaryButton")
        self.primary_button.setAccessibleName("登录 MentorFlow")
        self.primary_button.setDefault(True)
        self.primary_button.clicked.connect(self._activate)
        card_layout.addWidget(self.primary_button)
        outer.addWidget(card, 1)
        self.remember_box = QCheckBox("记住我（下次免输密码）")
        self.remember_box.setObjectName("rememberLoginCheckBox")
        self.auto_box = QCheckBox("自动登录")
        self.auto_box.setObjectName("autoLoginCheckBox")
        self.auto_box.setEnabled(False)
        self.remember_box.toggled.connect(self._remember_toggled)
        outer.addWidget(self.remember_box)
        outer.addWidget(self.auto_box)
        self.cancel_button = QPushButton("取消并选择其他账号")
        self.cancel_button.setObjectName("loginCancelButton")
        self.cancel_button.clicked.connect(self.cancel_requested)
        outer.addWidget(self.cancel_button)
        self.notice_label = QLabel()
        self.notice_label.setTextFormat(Qt.TextFormat.PlainText)
        self.notice_label.setWordWrap(True)
        self.notice_label.setObjectName("loginSessionNotice")
        outer.addWidget(self.notice_label)
        privacy = QLabel("密码仅由身份服务处理，MentorFlow 不读取或保存原始密码。")
        privacy.setObjectName("loginSubtitle")
        privacy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        privacy.setWordWrap(True)
        outer.addWidget(privacy)
        self.cancel_button.hide()

    def _remember_toggled(self, enabled: bool) -> None:
        self.auto_box.setEnabled(enabled)
        if not enabled:
            self.auto_box.setChecked(False)

    def set_auth_content(self, content: QWidget | None) -> None:
        if self.auth_content is not None:
            self.content_layout.removeWidget(self.auth_content)
            self.auth_content.hide()
        self.auth_content = content
        for widget in self.brand_widgets:
            widget.setVisible(content is None)
        self.state_title.setVisible(content is None)
        self.state_detail.setVisible(content is None)
        self.primary_button.setVisible(content is None)
        if content is not None:
            self.content_layout.addWidget(content, 1)
            content.show()

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._centered:
            screen = self.screen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.center() - self.rect().center())
            self._centered = True

    def _activate(self) -> None:
        if self._retry_mode:
            self.retry_requested.emit()
        else:
            self.login_requested.emit()

    def set_state(
        self,
        state: LoginGateState,
        *,
        detail: str = "",
        retry_bootstrap: bool = False,
    ) -> None:
        self.state = state
        self._retry_mode = bool(retry_bootstrap)
        if state is LoginGateState.LOGIN_READY:
            title = "登录你的账号"
            message = "学习计划、进度和本机资料会在登录后安全恢复。"
            button = "登录 MentorFlow"
            enabled = True
        elif state is LoginGateState.AUTHENTICATING:
            title = "正在验证身份"
            message = detail or "请在 MentorFlow 登录页面中完成验证。"
            button = "正在登录…"
            enabled = False
        elif state is LoginGateState.AUTH_SUCCESS_BOOTSTRAPPING:
            title = "正在加载你的 MentorFlow…"
            message = detail or "正在确认账号 · 正在恢复学习数据 · 正在检查本机资料"
            button = "正在准备…"
            enabled = False
        else:
            title = "暂时无法继续"
            message = detail or "登录未完成，请检查网络后重试。"
            button = "重试"
            enabled = True
        self.state_title.setText(title)
        self.state_detail.setText(message)
        self.primary_button.setText(button)
        self.primary_button.setEnabled(enabled)
        self.cancel_button.setVisible(state in {LoginGateState.AUTHENTICATING, LoginGateState.LOGIN_READY, LoginGateState.AUTH_FAILED})
        if enabled:
            self.primary_button.setFocus(Qt.FocusReason.OtherFocusReason)


class DeviceMigrationCenter(QDialog):
    snapshot_changed = Signal(object)
    _operation_succeeded = Signal(str, object)
    _operation_failed = Signal(str, str)

    def __init__(
        self,
        bootstrap: AccountBootstrapResult,
        *,
        parent: QWidget | None = None,
        before_main: bool = False,
    ) -> None:
        super().__init__(parent)
        self.bootstrap = bootstrap
        self.before_main = before_main
        self.setObjectName(MIGRATION_CENTER_OBJECT_NAME)
        self.setWindowTitle("MentorFlow · 本机资料恢复")
        self.resize(940, 700)
        self.setMinimumSize(760, 560)
        self.setModal(parent is not None)
        self._states: dict[str, RecoveryItemState] = {}
        self._busy_resource = ""
        self._reacquire_queue: list[str] = []
        self._build()
        self._operation_succeeded.connect(self._finish_operation)
        self._operation_failed.connect(self._fail_operation)
        self._refresh()

    def _build(self) -> None:
        self.setStyleSheet(
            "QDialog#mentorflowDeviceMigrationCenter { background: #fff9fc; }"
            "QFrame#recoverySummary { background: #fffdfd; border: 1px solid #edc2d3; border-radius: 16px; }"
            "QLabel#recoveryHeading { font-size: 27px; font-weight: 800; color: #3b2b3f; }"
            "QLabel#recoveryStatus { color: #8d3f62; font-weight: 700; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(14)
        heading = QLabel("恢复这台电脑需要的本机资料")
        heading.setObjectName("recoveryHeading")
        layout.addWidget(heading)
        intro = QLabel("你的学习记录已经恢复。缺少的原始资料仍只在你的设备上处理，不会上传到账号服务。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        summary = QFrame()
        summary.setObjectName("recoverySummary")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(18, 14, 18, 14)
        self.account_summary = QLabel()
        self.account_summary.setWordWrap(True)
        summary_layout.addWidget(self.account_summary)
        self.count_summary = QLabel()
        self.count_summary.setObjectName("recoveryStatus")
        summary_layout.addWidget(self.count_summary)
        layout.addWidget(summary)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("deviceRecoveryResources")
        self.table.setHorizontalHeaderLabels(("资料", "状态", "恢复方式", "操作"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        self.operation_status = QLabel()
        self.operation_status.setObjectName("recoveryStatus")
        self.operation_status.setWordWrap(True)
        layout.addWidget(self.operation_status)
        actions = QHBoxLayout()
        self.restore_selected_button = QPushButton("恢复所选")
        self.restore_selected_button.setObjectName("secondaryButton")
        self.restore_selected_button.clicked.connect(self._restore_selected)
        actions.addWidget(self.restore_selected_button)
        self.restore_all_button = QPushButton("全部可恢复项")
        self.restore_all_button.setObjectName("secondaryButton")
        self.restore_all_button.clicked.connect(self._restore_all)
        actions.addWidget(self.restore_all_button)
        self.defer_button = QPushButton("稍后处理")
        self.defer_button.clicked.connect(self._defer_selected)
        actions.addWidget(self.defer_button)
        actions.addStretch(1)
        self.continue_button = QPushButton(
            "进入 MentorFlow" if self.before_main else "完成"
        )
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.clicked.connect(self.accept)
        actions.addWidget(self.continue_button)
        layout.addLayout(actions)

    @staticmethod
    def _resource_name(resource: SyncedResource) -> str:
        metadata = resource.server_metadata
        return str(
            metadata.get("filename")
            or metadata.get("title")
            or metadata.get("display_name")
            or "学习资料"
        )

    def _initial_state(self, resource: SyncedResource) -> RecoveryItemState:
        existing = self._states.get(resource.resource_id)
        if existing is not None:
            return existing
        if resource.state in {ResourceDeviceState.AVAILABLE, ResourceDeviceState.RELINKED}:
            return RecoveryItemState.READY_ON_THIS_DEVICE
        if self._can_reacquire(resource):
            return RecoveryItemState.REACQUIRE_AVAILABLE
        return RecoveryItemState.MISSING_ON_THIS_DEVICE

    @staticmethod
    def _can_reacquire(resource: SyncedResource) -> bool:
        metadata = resource.server_metadata
        return bool(
            str(metadata.get("verified_source_url") or "").startswith("https://")
            and str(metadata.get("import_mode") or "").strip().upper() == "MANAGED"
        )

    def _refresh(self) -> None:
        snapshot = self.bootstrap.snapshot
        workspace_count = 1 if snapshot.workspace else 0
        self.account_summary.setText(
            f"账号：{self.bootstrap.identity.display_name}　·　"
            f"学习空间 {workspace_count} 个　·　计划 {len(snapshot.plans)} 个　·　"
            f"进度记录 {len(snapshot.progress)} 项"
        )
        resources = tuple(snapshot.resources)
        self.table.setRowCount(len(resources))
        resolved = 0
        for row, resource in enumerate(resources):
            state = self._initial_state(resource)
            self._states[resource.resource_id] = state
            if state in {
                RecoveryItemState.READY_ON_THIS_DEVICE,
                RecoveryItemState.RESTORED,
            }:
                resolved += 1
            name_item = QTableWidgetItem(self._resource_name(resource))
            name_item.setData(Qt.ItemDataRole.UserRole, resource.resource_id)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(_RECOVERY_LABELS[state]))
            if state is RecoveryItemState.READY_ON_THIS_DEVICE:
                method = "无需处理"
            elif self._can_reacquire(resource):
                method = "从已验证来源重新获取"
            else:
                method = "使用同一文件重新关联"
            self.table.setItem(row, 2, QTableWidgetItem(method))
            if state in {
                RecoveryItemState.READY_ON_THIS_DEVICE,
                RecoveryItemState.RESTORED,
            }:
                button_text = "已就绪"
            elif self._can_reacquire(resource):
                button_text = "重新获取"
            else:
                button_text = "选择本机文件"
            button = QPushButton(button_text)
            button.setEnabled(
                not self._busy_resource
                and state
                not in {
                    RecoveryItemState.READY_ON_THIS_DEVICE,
                    RecoveryItemState.RESTORED,
                    RecoveryItemState.RESTORING,
                }
            )
            button.clicked.connect(
                lambda _checked=False, resource_id=resource.resource_id: self._activate_resource(
                    resource_id
                )
            )
            self.table.setCellWidget(row, 3, button)
        remaining = len(resources) - resolved
        self.count_summary.setText(
            f"本机资料共 {len(resources)} 项 · 已就绪 {resolved} 项 · 待处理 {remaining} 项"
        )
        enabled = not self._busy_resource and bool(resources)
        self.restore_selected_button.setEnabled(enabled)
        self.restore_all_button.setEnabled(enabled and remaining > 0)
        self.defer_button.setEnabled(enabled and remaining > 0)
        self.continue_button.setEnabled(not self._busy_resource)

    def reject(self) -> None:
        if self._busy_resource:
            self.operation_status.setText("正在处理当前文件，请稍候再关闭。")
            return
        super().reject()

    def _selected_resource_id(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _resource(self, resource_id: str) -> SyncedResource:
        return self.bootstrap.snapshot.resource(resource_id)

    def _restore_selected(self) -> None:
        resource_id = self._selected_resource_id()
        if not resource_id:
            self.operation_status.setText("请先选择一项需要恢复的资料。")
            return
        self._activate_resource(resource_id)

    def _restore_all(self) -> None:
        missing = [
            item
            for item in self.bootstrap.snapshot.resources
            if self._states.get(item.resource_id)
            not in {
                RecoveryItemState.READY_ON_THIS_DEVICE,
                RecoveryItemState.RESTORED,
            }
        ]
        if not missing:
            self.operation_status.setText("这台电脑上的资料已经全部就绪。")
            return
        reacquirable = [item.resource_id for item in missing if self._can_reacquire(item)]
        if reacquirable:
            self._reacquire_queue = reacquirable[1:]
            self._reacquire(reacquirable[0])
            return
        self.table.selectRow(
            next(
                index
                for index, item in enumerate(self.bootstrap.snapshot.resources)
                if item.resource_id == missing[0].resource_id
            )
        )
        self.operation_status.setText("本地资料需要你逐项选择同一文件；已定位到下一项。")

    def _activate_resource(self, resource_id: str) -> None:
        resource = self._resource(resource_id)
        if self._can_reacquire(resource):
            self._reacquire(resource_id)
        else:
            self._select_file(resource_id)

    def _defer_selected(self) -> None:
        resource_id = self._selected_resource_id()
        if not resource_id:
            self.operation_status.setText("请先选择一项资料。")
            return
        resource = self._resource(resource_id)
        if resource.state is ResourceDeviceState.MISSING_ON_THIS_DEVICE:
            self._states[resource_id] = RecoveryItemState.SKIPPED
            self.operation_status.setText("已保留为待恢复，你可以稍后从“账号与数据”重新打开。")
            self._refresh()

    def _select_file(self, resource_id: str) -> None:
        if self._busy_resource:
            return
        resource = self._resource(resource_id)
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            f"选择与 {self._resource_name(resource)} 相同的文件",
        )
        if not selected:
            return
        self._busy_resource = resource_id
        self._states[resource_id] = RecoveryItemState.RESTORING
        self.operation_status.setText("正在核对文件内容…")
        self._refresh()

        def run() -> None:
            try:
                result = self.bootstrap.metadata_sync.relink(
                    resource_id=resource.resource_id,
                    expected_fingerprint_sha256=resource.expected_fingerprint_sha256,
                    selected_file=Path(selected),
                )
                self._operation_succeeded.emit(resource_id, result.snapshot)
            except AccountMetadataSyncError as exc:
                self._operation_failed.emit(resource_id, exc.user_message)
            except (OSError, RuntimeError, ValueError):
                self._operation_failed.emit(
                    resource_id,
                    "所选文件无法安全验证，请重新选择。",
                )

        threading.Thread(
            target=run,
            name="mentorflow-resource-relink",
            daemon=True,
        ).start()

    def _reacquire(self, resource_id: str) -> None:
        if self._busy_resource:
            return
        resource = self._resource(resource_id)
        if not self._can_reacquire(resource):
            self._states[resource_id] = RecoveryItemState.RELINK_REQUIRED
            self.operation_status.setText("这份资料没有可验证的远程来源，请选择本机文件。")
            self._refresh()
            return
        self._busy_resource = resource_id
        self._states[resource_id] = RecoveryItemState.RESTORING
        self.operation_status.setText("正在从已验证来源重新获取资料…")
        self._refresh()

        def run() -> None:
            try:
                self.bootstrap.local_asset_lifecycle.reacquire_resource(
                    resource.resource_id,
                    fingerprint_sha256=resource.expected_fingerprint_sha256,
                    verified_source_url=str(
                        resource.server_metadata.get("verified_source_url") or ""
                    ),
                )
                snapshot = self.bootstrap.metadata_sync.refresh_device_state()
                self._operation_succeeded.emit(resource_id, snapshot)
            except (AccountMetadataSyncError, LocalAssetLifecycleError) as exc:
                self._operation_failed.emit(
                    resource_id,
                    str(getattr(exc, "user_message", "") or "资料重新获取失败。"),
                )
            except (OSError, RuntimeError, ValueError):
                self._operation_failed.emit(
                    resource_id,
                    "资料未能重新获取，请稍后重试或选择本机文件。",
                )

        threading.Thread(
            target=run,
            name="mentorflow-resource-reacquire",
            daemon=True,
        ).start()

    def _finish_operation(self, resource_id: str, snapshot: object) -> None:
        self._busy_resource = ""
        if isinstance(snapshot, AccountMetadataSnapshot):
            self.bootstrap.apply_snapshot(snapshot)
            self._states[resource_id] = RecoveryItemState.RESTORED
            self.operation_status.setText("资料已恢复，学习计划和进度保持不变。")
            self.snapshot_changed.emit(snapshot)
        else:
            self._states[resource_id] = RecoveryItemState.RELINK_REQUIRED
            self.operation_status.setText("资料结果无法验证，请重试。")
        self._refresh()
        if self._reacquire_queue:
            next_resource = self._reacquire_queue.pop(0)
            self._reacquire(next_resource)

    def _fail_operation(self, resource_id: str, message: str) -> None:
        self._busy_resource = ""
        self._reacquire_queue.clear()
        self._states[resource_id] = RecoveryItemState.RELINK_REQUIRED
        self.operation_status.setText(
            str(message or "所选文件不是同一份资料，本机关联未更改。")
        )
        self._refresh()


__all__ = [
    "AccountBootstrapError",
    "AccountBootstrapResult",
    "DeviceBootstrapState",
    "DeviceMigrationCenter",
    "EnterpriseAccountMetadataApi",
    "LOGIN_GATE_OBJECT_NAME",
    "LoginGateState",
    "LoginGateWindow",
    "MIGRATION_CENTER_OBJECT_NAME",
    "RecoveryItemState",
    "bootstrap_enterprise_account",
    "classify_device",
]
