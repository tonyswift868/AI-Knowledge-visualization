"""Lightweight native LoginGate shell; no account, network, database or WebEngine imports."""
from __future__ import annotations

from enum import Enum
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QCheckBox, QLineEdit

LOGIN_GATE_OBJECT_NAME = "mentorflowLoginGateWindow"

class LoginGateState(str, Enum):
    STARTING = "STARTING"
    SECURE_CONNECTION = "SECURE_CONNECTION"
    LOADING_AUTH = "LOADING_AUTH"
    AUTH_READY = "AUTH_READY"
    ERROR = "ERROR"
    LOGIN_READY = "LOGIN_READY"
    AUTHENTICATING = "AUTHENTICATING"
    AUTH_SUCCESS_BOOTSTRAPPING = "AUTH_SUCCESS_BOOTSTRAPPING"
    AUTH_FAILED = "AUTH_FAILED"


class LoginGateWindow(QWidget):
    first_paint_completed = Signal()
    login_requested = Signal()
    retry_requested = Signal()
    background_succeeded = Signal(str, object)
    background_failed = Signal(str, str)
    cancel_requested = Signal()
    closed = Signal()
    about_requested = Signal()
    update_requested = Signal()
    repair_requested = Signal()
    register_requested = Signal()
    forgot_password_requested = Signal()
    update_blocked = Signal(bool)

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
        # Reserve the native form viewport from first paint, including the
        # native consent controls. Loading/form/refresh never resize the shell.
        area = self.screen().availableGeometry()
        available_width = max(240, area.width() - 48)
        available_height = max(320, area.height() - 80)
        self.resize(min(480, available_width), min(740, available_height))
        self.setMinimumSize(min(440, available_width), min(680, available_height))
        self.state = LoginGateState.LOGIN_READY
        self._retry_mode = False
        self._centered = False
        self._first_paint_scheduled = False
        self.startup_update_state = None
        self.update_controller = None
        self._login_enabled = True
        self.account_capabilities = {}
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
        outer.setSpacing(8)
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
        outer.addSpacing(4)
        card = QFrame()
        self.login_card = card
        card.setObjectName("loginCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(26, 24, 26, 24)
        card_layout.setSpacing(11)
        card.setMinimumHeight(230)
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
        self.username_field = QLineEdit()
        self.username_field.setObjectName('nativeUsername')
        self.username_field.setPlaceholderText('账号')
        self.username_field.setAccessibleName('账号')
        self.username_field.setMaxLength(320)
        self.password_field = QLineEdit()
        self.password_field.setObjectName('nativePassword')
        self.password_field.setPlaceholderText('密码')
        self.password_field.setAccessibleName('密码')
        self.password_field.setMaxLength(1024)
        self.password_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_field.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.show_password = QCheckBox('显示密码')
        self.show_password.toggled.connect(lambda visible: self.password_field.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password))
        self.password_field.returnPressed.connect(self._activate)
        for widget in (self.username_field, self.password_field, self.show_password):
            card_layout.addWidget(widget)
        for field in (self.username_field, self.password_field):
            field.setMinimumHeight(40)
            field.setStyleSheet('QLineEdit {padding: 4px 10px; border: 1px solid #cbb4c1; border-radius: 6px;}')
        self.primary_button = QPushButton("登录 MentorFlow")
        self.primary_button.setObjectName("loginPrimaryButton")
        self.primary_button.setAccessibleName("登录 MentorFlow")
        self.primary_button.setDefault(True)
        self.primary_button.clicked.connect(self._activate)
        card_layout.addWidget(self.primary_button)
        self.update_button = QPushButton('更新到最新版本')
        self.update_button.setObjectName('startupUpdateLatest')
        self.update_button.clicked.connect(self.update_requested.emit)
        self.repair_button = QPushButton('修复安装')
        self.repair_button.setObjectName('startupRepairInstallation')
        self.repair_button.clicked.connect(self.repair_requested.emit)
        card_layout.addWidget(self.update_button)
        card_layout.addWidget(self.repair_button)
        self.update_button.hide(); self.repair_button.hide()
        outer.addWidget(card, 1)
        self.remember_box = QCheckBox("记住登录状态")
        self.remember_box.setObjectName("rememberLoginCheckBox")
        self.auto_box = QCheckBox("自动登录")
        self.auto_box.setObjectName("autoLoginCheckBox")
        self.auto_box.setEnabled(False)
        self.remember_box.toggled.connect(self._remember_toggled)
        outer.addWidget(self.remember_box)
        outer.addWidget(self.auto_box)
        self.cancel_button = QPushButton("取消并选择其他账号")
        self.cancel_button.setObjectName("loginCancelButton")
        self.cancel_button.setStyleSheet('min-height: 24px; padding: 3px;')
        self.cancel_button.clicked.connect(self.cancel_requested)
        outer.addWidget(self.cancel_button)
        self.notice_label = QLabel()
        self.notice_label.setTextFormat(Qt.TextFormat.PlainText)
        self.notice_label.setWordWrap(True)
        self.notice_label.setObjectName("loginSessionNotice")
        outer.addWidget(self.notice_label)
        privacy = QLabel("密码仅用于本次安全验证，不会保存。记住登录只保存安全会话。")
        privacy.setObjectName("loginSubtitle")
        privacy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        privacy.setWordWrap(True)
        outer.addWidget(privacy)
        self.about_button = QPushButton('关于与检查更新')
        self.about_button.setObjectName('loginAboutUpdates')
        self.about_button.setStyleSheet('min-height: 22px; padding: 2px; border: 0; background: transparent;')
        self.about_button.setAccessibleName('关于与检查更新')
        self.about_button.clicked.connect(self.about_requested.emit)
        self.about_button.setEnabled(False)
        outer.addWidget(self.about_button)
        self.update_entry_layout = QVBoxLayout()
        outer.addLayout(self.update_entry_layout)
        account_actions = QHBoxLayout()
        self.register_button = QPushButton('注册账号')
        self.forgot_password_button = QPushButton('忘记密码')
        for button in (self.register_button, self.forgot_password_button):
            button.setAccessibleName(button.text())
            button.setStyleSheet('QPushButton {min-height: 24px; padding: 2px; border: 0; background: transparent;}'
                                'QPushButton:disabled {color: #a69ba2;}')
            button.setEnabled(False)
            account_actions.addWidget(button)
        self.register_button.clicked.connect(lambda: self._account_action('register'))
        self.forgot_password_button.clicked.connect(lambda: self._account_action('forgot_password'))
        outer.addLayout(account_actions)
        self.cancel_button.hide()

    @property
    def login_is_blocked(self):
        if self.update_controller is not None:
            return self.update_controller.login_blocked
        return bool(self.startup_update_state and not self.startup_update_state['login_allowed'])

    def bind_update_controller(self, controller):
        if self.update_controller is controller:
            return
        if self.update_controller is not None:
            self.update_controller.login_policy_changed.disconnect(self._update_login_policy)
        self.update_controller = controller
        # Leave enough room for the native credential form and direct footer.
        self.brand_widgets[0].hide()
        controller.login_policy_changed.connect(self._update_login_policy)
        if self.startup_update_state:
            self.set_startup_policy(self.startup_update_state)
        self._update_login_policy(controller.login_blocked)

    def _update_login_policy(self, blocked):
        self.login_card.setVisible(not blocked)
        self.primary_button.setEnabled(self._login_enabled and not blocked)
        for widget in (self.username_field, self.password_field, self.show_password):
            widget.setEnabled(self._login_enabled and not self._retry_mode and not blocked)
        self.primary_button.setVisible(not blocked and self.auth_content is None)
        self.remember_box.setEnabled(not blocked)
        self.auto_box.setEnabled(not blocked and self.remember_box.isChecked())
        self.cancel_button.setEnabled(not blocked)
        self.remember_box.setVisible(not blocked)
        self.auto_box.setVisible(not blocked)
        self.cancel_button.setVisible(not blocked and self.state in {
            LoginGateState.AUTHENTICATING, LoginGateState.LOGIN_READY, LoginGateState.AUTH_FAILED})
        if self.auth_content is not None:
            self.auth_content.setEnabled(not blocked)
            self.auth_content.setVisible(not blocked)
        self.set_account_capabilities(self.account_capabilities)
        self.update_blocked.emit(blocked)

    def set_account_capabilities(self, capabilities):
        # Explicit 05 capability only. Missing/string/unknown values do not
        # advertise registration or reset support for the current realm.
        self.account_capabilities = {key: isinstance(capabilities, dict) and capabilities.get(key) is True
                                     for key in ('register', 'forgot_password')}
        blocked = self.update_controller is not None and self.update_controller._last_login_blocked
        self.register_button.setEnabled(self.account_capabilities['register'] and not blocked)
        self.forgot_password_button.setEnabled(self.account_capabilities['forgot_password'] and not blocked)
        self.register_button.setVisible(not blocked)
        self.forgot_password_button.setVisible(not blocked)
        self.register_button.setToolTip('' if self.account_capabilities['register'] else '当前账号服务尚未开放注册')
        self.forgot_password_button.setToolTip('' if self.account_capabilities['forgot_password'] else '当前账号服务暂未开放密码找回')

    def _account_action(self, action):
        if self.login_is_blocked or self.account_capabilities.get(action) is not True:
            return
        (self.register_requested if action == 'register' else self.forgot_password_requested).emit()

    def _remember_toggled(self, enabled: bool) -> None:
        self.auto_box.setEnabled(enabled)
        if not enabled:
            self.auto_box.setChecked(False)

    def set_auth_content(self, content: QWidget | None) -> None:
        if content is not None and self.login_is_blocked:
            content.hide()
            return
        if self.auth_content is not None:
            self.content_layout.removeWidget(self.auth_content)
            self.auth_content.hide()
        self.auth_content = content
        # Keep native identity framing in place while only the form area changes.
        for index, widget in enumerate(self.brand_widgets):
            widget.setVisible(index != 0 or self.update_controller is None)
        self.state_title.setVisible(content is None)
        self.state_detail.setVisible(content is None)
        self.primary_button.setVisible(content is None)
        if content is not None:
            self.content_layout.addWidget(content, 1)
            content.setEnabled(not self.login_is_blocked)
            content.show()

    def set_startup_policy(self, snapshot):
        self.startup_update_state = dict(snapshot)
        if self.update_controller is not None:
            # Project an already verified 07 result through the single adapter.
            self.update_controller.port.set_startup_state(snapshot)
            self.update_controller.refresh_login_policy()
            self.update_button.hide(); self.repair_button.hide()
            return
        state = snapshot['state']
        blocked = not snapshot['login_allowed']
        self.update_button.setVisible(state in ('OPTIONAL_UPDATE','MANDATORY_UPDATE','REPAIR_REQUIRED'))
        self.repair_button.setVisible(state == 'REPAIR_REQUIRED')
        if blocked:
            if self.auth_content is not None: self.auth_content.hide()
            self.state_title.show(); self.state_detail.show()
            self.state_title.setText('修复与更新' if state == 'REPAIR_REQUIRED' else '需要更新')
            self.state_detail.setText(snapshot['message'])
            self.primary_button.hide()
            self.remember_box.setEnabled(False); self.auto_box.setEnabled(False)
            self.setWindowTitle('MentorFlow · 修复与更新' if state == 'REPAIR_REQUIRED' else 'MentorFlow · 登录与更新')
        elif state == 'OPTIONAL_UPDATE':
            self.notice_label.setText(snapshot['message'])

    def closeEvent(self, event) -> None:
        self.clear_password()
        self.closed.emit()
        super().closeEvent(event)
        if self.login_is_blocked:
            # Other windows or keep-alive policy must never turn X into a skip.
            if self.update_controller is not None:
                self.update_controller.exit_application()
            else:
                QApplication.instance().quit()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._centered:
            screen = self.screen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.center() - self.rect().center())
            self._centered = True
            # One normal activation request on initial user launch, not a
            # topmost flag or repeated foreground-stealing loop.
            self.raise_()
            self.activateWindow()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._first_paint_scheduled:
            self._first_paint_scheduled = True
            # This is a Qt paint/event-loop milestone, not proof of compositor
            # presentation or that a real credential form is interactive.
            QTimer.singleShot(0, self.first_paint_completed.emit)

    def _activate(self) -> None:
        if self.login_is_blocked or not self._login_enabled:
            return
        if self._retry_mode:
            self.retry_requested.emit()
        else:
            self.login_requested.emit()

    def clear_password(self):
        self.password_field.clear()
        self.show_password.setChecked(False)

    def take_credentials(self):
        username, password = self.username_field.text().strip(), self.password_field.text()
        self.clear_password()
        return username, password

    def set_state(
        self,
        state: LoginGateState,
        *,
        detail: str = "",
        retry_bootstrap: bool = False,
    ) -> None:
        self.state = state
        self._retry_mode = bool(retry_bootstrap)
        if state in {LoginGateState.STARTING, LoginGateState.SECURE_CONNECTION, LoginGateState.LOADING_AUTH}:
            title = {
                LoginGateState.STARTING: "正在启动 MentorFlow…",
                LoginGateState.SECURE_CONNECTION: "正在连接账号服务…",
                LoginGateState.LOADING_AUTH: "正在准备安全登录…",
            }[state]
            message = detail or "安全准备完成后，登录表单会在这里显示。"
            button = "正在准备…"
            enabled = False
        elif state in {LoginGateState.LOGIN_READY, LoginGateState.AUTH_READY}:
            title = "登录你的账号"
            message = detail or "学习计划、进度和本机资料会在登录后安全恢复。"
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
        self._login_enabled = enabled
        self.primary_button.setEnabled(enabled and not self.login_is_blocked)
        for widget in (self.username_field, self.password_field, self.show_password):
            widget.setEnabled(enabled and not retry_bootstrap and not self.login_is_blocked)
        preparing = state in {LoginGateState.STARTING, LoginGateState.SECURE_CONNECTION, LoginGateState.LOADING_AUTH}
        self.remember_box.setEnabled(not preparing)
        self.auto_box.setEnabled(not preparing and self.remember_box.isChecked())
        self.cancel_button.setVisible(state in {LoginGateState.AUTHENTICATING, LoginGateState.LOGIN_READY, LoginGateState.AUTH_FAILED})
        if self.login_is_blocked:
            self.primary_button.hide()
            self.remember_box.setEnabled(False)
            self.auto_box.setEnabled(False)
            self.cancel_button.setEnabled(False)
            self.cancel_button.hide()
        if enabled:
            self.primary_button.setFocus(Qt.FocusReason.OtherFocusReason)
