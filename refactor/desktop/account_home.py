"""Small authenticated landing window for the MentorFlow account shell."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class AccountHome(QWidget):
    switch_requested = Signal()
    logout_requested = Signal()
    verify_requested = Signal()

    def __init__(self, display_name: str, server_role: object, api_base_url: str, parent=None):
        super().__init__(parent)
        self.setObjectName("mentorflowAccountHome")
        self.setWindowTitle("MentorFlow · 账号")
        self.resize(520, 360)
        self.setMinimumSize(420, 300)
        self.setStyleSheet(
            "QWidget#mentorflowAccountHome { background: #fff9fc; }"
            "QLabel#accountTitle { color: #3b2b3f; font-size: 26px; font-weight: 800; }"
            "QLabel#accountName { color: #3b2b3f; font-size: 20px; font-weight: 650; }"
            "QLabel#accountDetail { color: #786572; font-size: 14px; }"
            "QPushButton#accountPrimary { min-height: 44px; color: white; background: #c95f8d;"
            " border: 0; border-radius: 10px; font-weight: 700; }"
            "QPushButton#accountPrimary:hover { background: #ae4d78; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 30, 34, 28)
        outer.setSpacing(16)
        title = QLabel("MentorFlow")
        title.setObjectName("accountTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        name = QLabel(display_name or "MentorFlow 账号")
        name.setObjectName("accountName")
        name.setAccessibleName("当前账号")
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(name)

        self.role_label = QLabel(self._role_label(server_role))
        self.role_label.setObjectName("accountRole")
        self.role_label.setAccessibleName("服务端账号身份")
        self.role_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.role_label.setStyleSheet("color: #786572; font-size: 14px;")
        outer.addWidget(self.role_label)

        api = QLabel(f"账号服务：{api_base_url}")
        api.setObjectName("accountDetail")
        api.setAccessibleName("账号服务地址")
        api.setTextFormat(Qt.TextFormat.PlainText)
        api.setAlignment(Qt.AlignmentFlag.AlignCenter)
        api.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(api)

        empty = QLabel("账号已连接。学习功能将在后续版本开放。")
        empty.setObjectName("accountDetail")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        outer.addStretch(1)
        self.status_label = QLabel()
        self.status_label.setObjectName("accountSessionStatus")
        self.status_label.setAccessibleName("账号会话状态")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        outer.addWidget(self.status_label)
        outer.addWidget(empty)

        self.verify_button = QPushButton("重新验证账号")
        self.verify_button.setAccessibleName("重新验证账号")
        self.verify_button.clicked.connect(self.verify_requested.emit)
        self.verify_button.hide()
        outer.addWidget(self.verify_button)

        switch = QPushButton("切换账号")
        switch.setAccessibleName("切换账号")
        switch.clicked.connect(self.switch_requested.emit)
        outer.addWidget(switch)

        logout = QPushButton("退出登录")
        logout.setObjectName("accountPrimary")
        logout.setAccessibleName("退出登录")
        logout.clicked.connect(self.logout_requested.emit)
        outer.addWidget(logout)

    @staticmethod
    def _role_label(role: object) -> str:
        # Role is displayed only when /auth/me returned a recognized server value.
        if role == "SUPER_ADMIN":
            return "服务端身份：管理员"
        if role == "LEARNER":
            return "服务端身份：学习者"
        return "服务端身份：待确认"

    def set_server_role(self, role: object) -> None:
        self.role_label.setText(self._role_label(role))

    def set_session_notice(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.setVisible(bool(message))
        self.verify_button.setVisible(bool(message))

    def clear_session_notice(self) -> None:
        self.set_session_notice("")
