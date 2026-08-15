from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .api import ApiError, DesktopApi


class AuthWindow(QWidget):
    authenticated = Signal(dict)

    def __init__(self, api: DesktopApi, registration_required: bool):
        super().__init__()
        self.api = api
        self.registration_required = registration_required
        self.remote_mode = not (
            api.origin.startswith("http://127.0.0.1:")
            or api.origin.startswith("http://localhost:")
        )
        self.setWindowTitle("MentorFlow · 登录")
        self.setMinimumSize(900, 620)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        brand = QFrame()
        brand.setObjectName("brandPanel")
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(58, 58, 58, 58)
        brand_layout.addStretch()
        leaf = QLabel("🌿")
        leaf.setObjectName("brandLeaf")
        brand_layout.addWidget(leaf)
        title = QLabel("MentorFlow")
        title.setObjectName("brandTitle")
        brand_layout.addWidget(title)
        subtitle = QLabel(
            "真正属于你的云端学习工作台\n登录后可在不同设备继续学习。"
            if self.remote_mode
            else "真正属于你的本地学习工作台\n资料、计划和进度都保存在这台电脑上。"
        )
        subtitle.setObjectName("brandSubtitle")
        subtitle.setWordWrap(True)
        brand_layout.addWidget(subtitle)
        brand_layout.addStretch(2)
        root.addWidget(brand, 5)

        form_host = QWidget()
        form_layout = QVBoxLayout(form_host)
        form_layout.setContentsMargins(70, 60, 70, 60)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._login_page())
        self.stack.addWidget(self._register_page())
        self.stack.addWidget(self._recovery_page())
        self.stack.setCurrentIndex(1 if registration_required else 0)
        form_layout.addStretch()
        form_layout.addWidget(self.stack)
        form_layout.addStretch()
        root.addWidget(form_host, 6)

    def _field(self, placeholder: str, password: bool = False) -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setMinimumHeight(46)
        if password:
            field.setEchoMode(QLineEdit.EchoMode.Password)
        return field

    def _login_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("欢迎回来")
        heading.setObjectName("formHeading")
        layout.addWidget(heading)
        layout.addWidget(QLabel("登录后继续你的学习计划。"))
        layout.addSpacing(24)
        self.login_username = self._field("账号")
        self.login_password = self._field("密码", True)
        self.login_password.returnPressed.connect(self._login)
        layout.addWidget(self.login_username)
        layout.addWidget(self.login_password)
        button = QPushButton("登录")
        button.setObjectName("primaryButton")
        button.clicked.connect(self._login)
        layout.addWidget(button)
        switch = QPushButton("没有账号？创建本地账户")
        switch.setObjectName("linkButton")
        switch.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        layout.addWidget(switch)
        recover = QPushButton("忘记密码？使用手机号短信验证码")
        recover.setObjectName("linkButton")
        recover.clicked.connect(lambda: self.stack.setCurrentIndex(2))
        layout.addWidget(recover)
        return page

    def _register_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("建立你的本地账户")
        heading.setObjectName("formHeading")
        layout.addWidget(heading)
        note = QLabel(
            "第一个账户会认领当前已有的任务、资料和学习进度。"
            if self.registration_required
            else "新账户会获得独立的空白学习空间，不会看到其他账户的资料和进度。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addSpacing(18)
        self.register_username = self._field("账号：3–32 位英文、数字或 _.-")
        self.register_display = self._field("显示名称")
        self.register_password = self._field("密码：至少 10 位并包含三类字符", True)
        self.register_confirm = self._field("再次输入密码", True)
        self.claim_check = QCheckBox("认领当前已有数据（推荐）")
        self.claim_check.setChecked(self.registration_required)
        self.claim_check.setEnabled(False)
        for field in (self.register_username, self.register_display, self.register_password, self.register_confirm):
            layout.addWidget(field)
        layout.addWidget(self.claim_check)
        button = QPushButton("创建账户并进入")
        button.setObjectName("primaryButton")
        button.clicked.connect(self._register)
        layout.addWidget(button)
        switch = QPushButton("已有账户？返回登录")
        switch.setObjectName("linkButton")
        switch.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        layout.addWidget(switch)
        return page

    def _recovery_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("找回云端账户" if self.remote_mode else "找回本地账户")
        heading.setObjectName("formHeading")
        layout.addWidget(heading)
        note = QLabel(
            "只有已完成手机号绑定和验证的账号可以找回密码。请输入账号后请求短信验证码；"
            "没有绑定手机号的账号无法通过此流程恢复。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addSpacing(18)
        self.recovery_username = self._field("账号")
        self.recovery_code = self._field("短信验证码：6 位")
        self.recovery_password = self._field("新密码：至少 10 位并包含三类字符", True)
        self.recovery_confirm = self._field("再次输入新密码", True)
        for field in (
            self.recovery_username,
            self.recovery_code,
            self.recovery_password,
            self.recovery_confirm,
        ):
            layout.addWidget(field)
        request_code = QPushButton("发送短信验证码")
        request_code.clicked.connect(self._request_password_reset)
        layout.addWidget(request_code)
        reset = QPushButton("更新密码")
        reset.setObjectName("primaryButton")
        reset.clicked.connect(self._reset_password)
        layout.addWidget(reset)
        switch = QPushButton("返回登录")
        switch.setObjectName("linkButton")
        switch.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        layout.addWidget(switch)
        layout.addStretch()
        return page

    def _login(self) -> None:
        try:
            payload = self.api.login(self.login_username.text(), self.login_password.text())
        except ApiError as exc:
            QMessageBox.warning(self, "登录失败", exc.message)
            return
        self.authenticated.emit(payload["user"])

    def _register(self) -> None:
        if self.register_password.text() != self.register_confirm.text():
            QMessageBox.warning(self, "无法创建账户", "两次输入的密码不一致。")
            return
        try:
            payload = self.api.register(
                self.register_username.text(),
                self.register_display.text(),
                self.register_password.text(),
                claim_existing_data=self.claim_check.isChecked(),
            )
        except ApiError as exc:
            QMessageBox.warning(self, "无法创建账户", exc.message)
            return
        QMessageBox.information(
            self,
            "账户已创建",
            "账户已创建。请进入“账户与调试”绑定并验证手机号；没有绑定手机号时不能找回密码。",
        )
        self.authenticated.emit(payload["user"])

    def _request_password_reset(self) -> None:
        username = self.recovery_username.text().strip()
        if not username:
            QMessageBox.warning(self, "无法发送验证码", "请先输入账号。")
            return
        try:
            payload = self.api.request_password_reset(username)
        except ApiError as exc:
            QMessageBox.warning(self, "无法发送验证码", exc.message)
            return
        QMessageBox.information(self, "请检查短信", str(payload.get("message") or "如果账号已绑定手机号，验证码会发送到该手机号。"))

    def _reset_password(self) -> None:
        if self.recovery_password.text() != self.recovery_confirm.text():
            QMessageBox.warning(self, "无法更新密码", "两次输入的新密码不一致。")
            return
        try:
            self.api.reset_password(
                self.recovery_username.text(),
                self.recovery_code.text(),
                self.recovery_password.text(),
            )
        except ApiError as exc:
            QMessageBox.warning(self, "无法更新密码", exc.message)
            return
        self.login_username.setText(self.recovery_username.text().strip())
        self.login_password.clear()
        self.recovery_code.clear()
        self.recovery_password.clear()
        self.recovery_confirm.clear()
        self.stack.setCurrentIndex(0)
        QMessageBox.information(self, "密码已更新", "请使用新密码重新登录。")
