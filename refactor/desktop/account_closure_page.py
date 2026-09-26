"""Explicit save choice, preparation, final confirmation and recovery UI."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QVBoxLayout, QWidget)

from .account_closure_client import MESSAGES
from .account_closure_store import REPLACEMENT_REFUSED, can_reprepare


class ClosurePage(QDialog):
    prepare_requested = Signal(str)
    reprepare_requested = Signal(str)
    commit_requested = Signal()
    query_requested = Signal()
    cleanup_requested = Signal()
    save_requested = Signal()
    export_requested = Signal()
    closed = Signal()

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self._closed = False
        self._busy = False
        self._record = None
        self._known = False
        self._prepare_authenticated = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('MentorFlow · 注销当前账号')
        self.resize(580, 610)
        box = QVBoxLayout(self)
        self.account_label = self.label('当前注销账号：' + profile.safe_account_label + '\n账号 ID：' + profile.account_id, box)
        self.label('提交后，服务器将按注销协议删除此账号，操作不可撤回。只有服务器明确确认完成后，才会清除此账号的本机登录凭据、记住密码、偏好及受管学习资料。不会清理其他账号、导出文件、外部原件或旧资料库。', box)
        self.choice = QWidget(); choice = QVBoxLayout(self.choice)
        self.save_choice = QRadioButton('保存当前计划及关联资料后再注销'); self.save_choice.setChecked(True)
        self.discard_choice = QRadioButton('不保存资料')
        choice.addWidget(self.save_choice); choice.addWidget(self.discard_choice)
        self.continue_button = QPushButton('继续')
        self.continue_button.clicked.connect(self._choose); choice.addWidget(self.continue_button)
        box.addWidget(self.choice)
        self.confirmation = QCheckBox('我已确认资料保存情况，并永久注销上述账号')
        box.addWidget(self.confirmation); self.confirmation.hide()
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('当前密码（仅用于准备，不会保存）'); self.password.setMaxLength(1024)
        box.addWidget(self.password); self.password.hide()
        self.prepare_button = QPushButton('验证密码并准备注销')
        self.prepare_button.clicked.connect(self._prepare); box.addWidget(self.prepare_button); self.prepare_button.hide()
        self.reprepare_button = QPushButton('重新准备（需重新输入密码）')
        self.reprepare_button.clicked.connect(self._reprepare); box.addWidget(self.reprepare_button); self.reprepare_button.hide()
        self.commit_button = QPushButton('最终确认：提交永久注销')
        self.commit_button.clicked.connect(self._commit); box.addWidget(self.commit_button); self.commit_button.hide()
        self.query_button = QPushButton('查询原操作')
        self.query_button.clicked.connect(self.query_requested.emit); box.addWidget(self.query_button); self.query_button.hide()
        self.save_button = QPushButton('重试保存恢复凭据')
        self.save_button.clicked.connect(self.save_requested.emit); box.addWidget(self.save_button); self.save_button.hide()
        self.cleanup_button = QPushButton('继续本机清理')
        self.cleanup_button.clicked.connect(self.cleanup_requested.emit); box.addWidget(self.cleanup_button); self.cleanup_button.hide()
        self.status = self.label('', box)
        box.addStretch()
        self.cancel_button = QPushButton('取消并返回')
        self.cancel_button.clicked.connect(self.close); box.addWidget(self.cancel_button)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.confirmation.toggled.connect(self._buttons)

    @staticmethod
    def label(text, layout):
        label = QLabel(text); label.setTextFormat(Qt.TextFormat.PlainText); label.setWordWrap(True)
        layout.addWidget(label)
        return label

    def _choose(self):
        if self._busy or self._closed or self._record is not None:
            return
        if self.save_choice.isChecked():
            self.export_requested.emit()
            return
        self._show_prepare(saved=False)

    def _show_prepare(self, *, saved):
        self.choice.hide(); self.confirmation.show(); self.password.show(); self.prepare_button.show()
        self.confirmation.setChecked(False)
        self.confirmation.setText('我已保存当前计划及关联资料，确认永久注销上述账号' if saved else '我确认不保存资料，并永久注销上述账号')
        self.status.setText(('已保存当前计划及关联资料（不含账号内其他资料）。' if saved else '请再次确认不保存资料。') + '请输入当前密码。准备本身不会删除或禁用账号；准备后还需最终确认提交。')
        self._buttons()

    def begin_export(self):
        self.set_busy(True)
        self.status.setText('请在学习主界面选择要保存的当前计划及导出位置。取消或导出未成功时会停留在此处，不会提交注销。')

    def finish_export(self, *, saved=False, message=''):
        self._busy = False
        if saved:
            self._show_prepare(saved=True)
        else:
            self.status.setText(message + '\n尚未保存，不会准备或提交注销。可重试保存或取消；不会自动改为不保存。')
            self._buttons()

    def _prepare(self):
        if self._busy or not self._prepare_authenticated or not self.confirmation.isChecked() or self._record is not None:
            return
        password = self.password.text(); self.password.clear()
        if not password:
            self.status.setText('请输入当前密码。')
            return
        self.prepare_requested.emit(password)

    def _reprepare(self):
        if self._busy or self._closed or not self._prepare_authenticated or not self.confirmation.isChecked() or not can_reprepare(self._record):
            return
        password = self.password.text(); self.password.clear()
        if not password:
            self.status.setText('请重新输入当前账号密码，再主动点击重新准备。')
            return
        self.reprepare_requested.emit(password)

    def set_prepare_authenticated(self, authenticated):
        self._prepare_authenticated = bool(authenticated)
        self._buttons()

    def _commit(self):
        if not self._busy and self._known and self.confirmation.isChecked() and self._record and self._record['state'] in {'PREPARED', 'RETRYABLE_FAILURE'}:
            self.commit_requested.emit()

    def set_busy(self, busy):
        self._busy = busy
        if busy:
            self.status.setText('正在处理；关闭窗口会停止等待。已提交的服务器注销不能由关闭窗口撤回。')
        self._buttons()

    def _buttons(self):
        idle = not self._busy and not self._closed
        for button in (self.continue_button, self.query_button, self.cleanup_button, self.save_button):
            button.setEnabled(idle)
        self.save_choice.setEnabled(idle); self.discard_choice.setEnabled(idle)
        self.password.setEnabled(idle and self._prepare_authenticated); self.confirmation.setEnabled(idle)
        self.prepare_button.setEnabled(idle and self._prepare_authenticated and self.confirmation.isChecked())
        self.reprepare_button.setEnabled(idle and self._prepare_authenticated and self.confirmation.isChecked())
        self.commit_button.setEnabled(idle and self._known and self.confirmation.isChecked())

    def render(self, record, *, error='', unsaved=False, finished=False):
        self._record = record
        self._busy = False
        self._known = record is not None and not error and not unsaved
        self.password.clear()
        self.save_button.setVisible(unsaved)
        reprepare = can_reprepare(record) and not unsaved
        self.reprepare_button.setVisible(reprepare)
        if record:
            self.choice.hide(); self.password.setVisible(reprepare); self.prepare_button.hide()
            state = record['state']
            commit = self._known and state in {'PREPARED', 'RETRYABLE_FAILURE'}
            self.confirmation.setVisible(commit or reprepare); self.confirmation.setChecked(False)
            self.commit_button.setVisible(commit)
            self.commit_button.setText('重试提交原注销操作' if record['submitted'] else '最终确认：提交永久注销')
            self.query_button.setVisible(bool(record['operation_id']) and state != 'COMPLETED')
            self.cleanup_button.setVisible(state == 'COMPLETED' and not finished)
            self.cancel_button.setText('关闭（仅停止等待）' if record['submitted'] else '取消本次提交并返回')
            message = {
                'PREPARE_UNKNOWN': MESSAGES['CLOSURE_PREPARE_UNKNOWN'],
                REPLACEMENT_REFUSED: MESSAGES['CLOSURE_ALREADY_COMMITTED'],
                'PREPARED': '账号已准备注销，尚未确认删除。你可以最终确认提交，或取消本次提交；恢复记录将保留。',
                'IN_PROGRESS': '服务器正在处理注销。关闭仅停止等待，可稍后查询原操作。',
                'RETRYABLE_FAILURE': '服务器尚未完成注销，可主动重试同一操作；不会重新准备。',
                'COMPLETED': '服务器已确认注销完成。本机清理尚未完成，可继续清理。',
            }[state]
            if reprepare:
                detail = MESSAGES.get(error, '') if error and error != 'CLOSURE_UNCONFIRMED' else ''
                if not self._prepare_authenticated:
                    detail += ' 请先关闭此窗口，登录上述同一账号后再打开注销恢复。'
                message = (detail + '\n' if detail else '') + message
            elif error:
                detail = MESSAGES.get(error, MESSAGES['CLOSURE_UNCONFIRMED'])
                if record['state'] != REPLACEMENT_REFUSED:
                    message = detail
                elif error != 'CLOSURE_ALREADY_COMMITTED':
                    message += '\n' + detail
            self.status.setText(message)
        else:
            self.status.setText(MESSAGES.get(error, '') if error else '')
        if finished:
            self.status.setText('服务器已确认注销完成；本机此账号的登录信息、偏好和受管资料已清理。')
            self.cancel_button.setText('关闭')
        self._buttons()

    def _dispose(self):
        if not self._closed:
            self._closed = True; self.password.clear(); self.closed.emit()

    def reject(self):
        self._dispose(); super().reject()

    def closeEvent(self, event):
        self._dispose(); super().closeEvent(event)
