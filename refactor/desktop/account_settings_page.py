"""Email/password UI extracted from the old account page, without vault recovery."""
import re
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .account_security_client import SECURITY_MESSAGES


class AccountSettingsPage(QDialog):
    read_requested = Signal()
    code_requested = Signal(str)
    verify_requested = Signal(str)
    password_requested = Signal(str, str)
    revocation_requested = Signal()
    closure_requested = Signal()
    cancel_email_requested = Signal()
    closed = Signal()

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self._closed = False
        self._operation = None
        self._can_email_action = False
        self._email_read_failed = False
        self._has_code = False
        self._resend_at = self._expires = 0
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName('accountSettingsWindow')
        self.setWindowTitle('MentorFlow · 账号设置')
        self.resize(520, 710)
        self.setMinimumSize(420, 480)
        self.setStyleSheet('''
            QDialog#accountSettingsWindow { background: #fff9fc; color: #3b2b3f; }
            QFrame#settingsCard { background: white; border: 1px solid #ead8e1; border-radius: 8px; }
            QPushButton { min-height: 30px; padding: 3px 10px; }
            QLineEdit { min-height: 28px; padding: 3px 8px; }
            QLabel#pageHeading { color: #3b2b3f; font-size: 24px; font-weight: 700; }
        ''')
        outer = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); scroll.setWidget(content); outer.addWidget(scroll)
        layout = QVBoxLayout(content); layout.setContentsMargins(20, 20, 20, 20); layout.setSpacing(12)
        heading = QLabel('账号与安全'); heading.setObjectName('pageHeading'); layout.addWidget(heading)
        self.account_label = self._label(profile.safe_account_label, layout)
        self._label('查看或验证绑定邮箱，修改登录密码。', layout)
        card = QFrame(); card.setObjectName('settingsCard'); layout.addWidget(card)
        fields = QVBoxLayout(card); fields.setContentsMargins(16, 14, 16, 14); fields.setSpacing(10)
        self.email_status = self._label('正在读取邮箱状态…', fields)
        self.email_address = QLineEdit(); self.email_address.setPlaceholderText('要绑定的邮箱')
        self.email_address.setAccessibleName('绑定邮箱'); self.email_address.setMaxLength(254); fields.addWidget(self.email_address)
        self.email_address.hide()
        self.refresh_security_button = QPushButton('重试读取邮箱状态')
        self.refresh_security_button.clicked.connect(self.read_requested.emit); fields.addWidget(self.refresh_security_button)
        self.send_email_code_button = QPushButton('发送邮箱验证码')
        self.send_email_code_button.clicked.connect(self._request_code); fields.addWidget(self.send_email_code_button)
        self.email_code = QLineEdit(); self.email_code.setPlaceholderText('邮件中的六位验证码')
        self.email_code.setAccessibleName('邮箱验证码'); self.email_code.setMaxLength(6); fields.addWidget(self.email_code)
        self.verify_email_button = QPushButton('验证邮箱')
        self.verify_email_button.clicked.connect(self._verify_code); fields.addWidget(self.verify_email_button)
        self.cancel_email_button = QPushButton('取消邮箱验证')
        self.cancel_email_button.clicked.connect(self._cancel_email); fields.addWidget(self.cancel_email_button)
        self.security_status = self._label('', fields)

        password_card = QFrame(); password_card.setObjectName('settingsCard'); layout.addWidget(password_card)
        password_layout = QVBoxLayout(password_card); password_layout.setContentsMargins(16, 14, 16, 14)
        self.password_toggle = QPushButton('修改密码…'); password_layout.addWidget(self.password_toggle)
        self.password_form = QWidget(); form = QVBoxLayout(self.password_form)
        self.current_password = self._password_field('当前密码', 1024, form)
        self.new_password = self._password_field('新密码（8–128 位）', 128, form)
        self.confirm_password = self._password_field('确认新密码', 128, form)
        self.change_password_button = QPushButton('确认修改密码')
        self.change_password_button.clicked.connect(self._change_password); form.addWidget(self.change_password_button)
        self.password_status = self._label('', form)
        self._label('修改后需在本机重新登录；学习资料会保留。', form)
        password_layout.addWidget(self.password_form); self.password_form.hide()
        self.password_toggle.clicked.connect(lambda: self.password_form.setVisible(self.password_form.isHidden()))
        self.revocation_notice = self._label('', password_layout)
        self.revocation_button = QPushButton('重试撤销会话（也会退出本次登录）')
        self.revocation_button.clicked.connect(self.revocation_requested.emit)
        password_layout.addWidget(self.revocation_button)
        self.set_revocation_pending('')
        self.closure_button = QPushButton('注销当前账号…')
        self.closure_button.clicked.connect(self.closure_requested.emit)
        layout.addWidget(self.closure_button)
        layout.addStretch()
        close_button = QPushButton('返回学习'); close_button.clicked.connect(self.close); outer.addWidget(close_button)
        self._timer = QTimer(self); self._timer.setInterval(250); self._timer.timeout.connect(self._buttons)
        self._timer.start(); self._buttons()
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.email_code.returnPressed.connect(self._verify_code)

    @staticmethod
    def _label(text, layout):
        label = QLabel(text); label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(label); return label

    @staticmethod
    def _password_field(label, maximum, layout):
        field = QLineEdit(); field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText(label); field.setAccessibleName(label); field.setMaxLength(maximum)
        layout.addWidget(field); return field

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.read_requested.emit)

    def set_busy(self, operation):
        self._operation = operation
        if operation:
            (self.password_status if operation in ('password', 'revoke') else self.security_status).setText('正在处理…')
        self._buttons()

    def _buttons(self):
        idle = self._operation is None and not self._closed
        remaining = max(0, int(self._resend_at - time.monotonic() + .999))
        if self._has_code and time.monotonic() >= self._expires:
            self._has_code = False; self.email_code.clear(); self.cancel_email_requested.emit()
            self.security_status.setText('验证码已过期，请重新获取。')
        self.refresh_security_button.setVisible(self._email_read_failed)
        self.refresh_security_button.setEnabled(idle and self._email_read_failed)
        self.send_email_code_button.setVisible(self._can_email_action)
        self.send_email_code_button.setEnabled(idle and self._can_email_action and remaining == 0)
        self.send_email_code_button.setText(f'{remaining} 秒后可重新发送' if remaining else '发送邮箱验证码')
        self.email_address.setEnabled(idle)
        self.email_code.setVisible(self._has_code); self.email_code.setEnabled(idle)
        self.verify_email_button.setVisible(self._has_code); self.verify_email_button.setEnabled(idle and self._has_code)
        self.cancel_email_button.setVisible(self._has_code or self._operation in ('request', 'verify'))
        self.change_password_button.setEnabled(idle)
        self.password_toggle.setEnabled(idle)
        self.revocation_button.setEnabled(idle)
        self.closure_button.setEnabled(idle)
        for field in (self.current_password, self.new_password, self.confirm_password): field.setEnabled(idle)

    def _request_code(self):
        if self._operation is not None or not self._can_email_action or time.monotonic() < self._resend_at:
            return
        address = self.email_address.text().strip()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', address):
            self.security_status.setText('请输入有效的邮箱地址。'); return
        self._has_code = False; self.email_code.clear(); self._resend_at = time.monotonic() + 60
        self.code_requested.emit(address)

    def _verify_code(self):
        if self._operation is not None or not self._has_code:
            return
        code = self.email_code.text().strip()
        if not re.fullmatch(r'[0-9]{6}', code):
            self.security_status.setText('请输入邮件中的六位数字验证码。'); return
        self.email_code.clear(); self.verify_requested.emit(code)

    def _cancel_email(self):
        self._has_code = False; self.email_code.clear(); self.cancel_email_requested.emit()
        self.security_status.setText('邮箱验证已取消；可稍后重新获取验证码。'); self._buttons()

    def _change_password(self):
        if self._operation is not None:
            return
        current, new, confirm = self.current_password.text(), self.new_password.text(), self.confirm_password.text()
        if not current or not 8 <= len(new) <= 128 or new != confirm:
            self.password_status.setText('请输入当前密码，检查新密码长度，并确认两次输入一致。'); return
        if current == new:
            self.password_status.setText('新密码不能与当前密码相同。'); return
        for field in (self.current_password, self.new_password, self.confirm_password): field.clear()
        self.password_requested.emit(current, new)

    def complete(self, operation, value, error):
        self._operation = None
        if error:
            if operation == 'read':
                self._email_read_failed = True
                self._can_email_action = False; self._has_code = False
                self.email_address.clear(); self.email_address.hide(); self.email_code.clear()
                self.cancel_email_requested.emit()
                self.email_status.setText('邮箱状态待确认')
            if error in {'EMAIL_CODE_INVALID_OR_EXPIRED', 'ACCOUNT_RESULT_UNCERTAIN'} and operation == 'verify':
                self._has_code = False
            message = SECURITY_MESSAGES.get(error, '操作未能确认完成，请稍后重试；不会自动重试。')
            if operation in ('read', 'request', 'verify'):
                message = {
                    'EMAIL_ALREADY_BOUND': '账号已有绑定邮箱，正在重新读取邮箱状态。',
                    'EMAIL_ALREADY_VERIFIED': '邮箱已经验证，正在重新读取邮箱状态。',
                    'ACCOUNT_ACTION_UNAVAILABLE': '邮箱服务暂不可用，请稍后重试；不会自动重发验证码。',
                    'ACCOUNT_RESULT_UNCERTAIN': ('邮箱状态暂时无法读取，请重试读取。' if operation == 'read'
                        else '暂时无法确认操作结果。请稍后重新打开账号设置核对状态；不会自动重发验证码。'),
                }.get(error, message)
            (self.password_status if operation in ('password', 'revoke') else self.security_status).setText(message)
        elif operation in ('read', 'verify'):
            self._email_read_failed = False
            self._can_email_action = value['can_bind'] or value['can_verify']
            if not self._can_email_action:
                self._has_code = False; self.email_code.clear(); self.cancel_email_requested.emit()
            email = value['email']; self.email_address.setText(email)
            self.email_address.setReadOnly(bool(email)); self.email_address.setVisible(not email)
            self.email_status.setText(('绑定邮箱：' + email + (' · 已验证' if value['email_verified'] else ' · 未验证')) if email else '尚未绑定邮箱')
            if operation == 'verify':
                self._has_code = False; self.security_status.setText('邮箱已验证。')
            else:
                self.security_status.clear()
        elif operation == 'request':
            self._has_code = True; self._expires = time.monotonic() + value['expires_in']
            self.security_status.setText('验证码已发送，请在 10 分钟内输入；不会自动重发。')
        self._buttons()

    def set_revocation_pending(self, state):
        self.revocation_button.setVisible(bool(state))
        self.revocation_notice.setVisible(bool(state))
        self.revocation_notice.setText('此前的会话撤销尚未全部确认。你已重新登录，可主动重试撤销；此操作也会结束当前新会话，不会重新提交改密。' if state else '')

    def _dispose(self):
        if not self._closed:
            self._closed = True; self._timer.stop(); self.email_code.clear()
            for field in (self.current_password, self.new_password, self.confirm_password): field.clear()
            self.closed.emit()

    def reject(self):
        # Escape takes QDialog.reject(), bypassing closeEvent.
        self._dispose()
        super().reject()

    def closeEvent(self, event):
        self._dispose()
        super().closeEvent(event)
