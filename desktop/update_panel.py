"""User-facing update card; package/transaction diagnostics stay outside the UI."""
from __future__ import annotations

import re

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar, QPlainTextEdit, QScrollArea

from .version import VERSION_MANIFEST
from .update_lifecycle import configured_update_service as configured_mature_service
from .update_controller import UpdateController, UpdateState, application_updater

_AUTO = object()


def release_details(text):
    """Plain, bounded release prose; technical diagnostics are not release notes."""
    lines = [line.strip() for line in str(text or '').splitlines() if line.strip()]
    lines = [line for line in lines if not re.search(
        r'manifest|hardlink|receipt|source.acceptance|helper|\bsha(?:256)?\b|traceback|http\s+stack|https?://|\b[0-9a-f]{40,}\b', line, re.I)]
    return '\n'.join(lines)[:4000] or '此更新包含功能改进与问题修复。'


def release_summary(text):
    return '\n'.join(release_details(text).splitlines()[:3])[:240]


class UpdatePanel(QFrame):
    def __init__(self, parent=None, *, service=_AUTO, port=None, controller=None,
                 compact=False, auto_delay_ms=5000):
        super().__init__(parent)
        self.setObjectName('updateCenter')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._compact = compact
        self._shared = controller is not None or service is _AUTO and port is None
        self.controller = controller or (application_updater(configured_mature_service) if self._shared
                           else UpdateController(None if service is _AUTO else service, self, port=port))
        self.service = self.controller.service
        self._closed = False
        self._restored = False
        self._notes_text = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        self.heading = QLabel('关于 MentorFlow 与更新')
        layout.addWidget(self.heading)
        self.heading.setVisible(not compact)
        version = self.controller.port.snapshot.get('current') or VERSION_MANIFEST.get('version', '—')
        self.version_label = QLabel(f'当前版本：{version}')
        self.version_label.setTextFormat(Qt.TextFormat.PlainText)
        version_row = QHBoxLayout()
        version_row.addWidget(self.version_label)
        version_row.addStretch()
        layout.addLayout(version_row)
        self.notes = QLabel('学习资料和账号数据不参与程序更新。')
        self.notes.setTextFormat(Qt.TextFormat.PlainText)
        self.notes.setWordWrap(True)
        layout.addWidget(self.notes)
        self.notes_more = QPushButton('查看更新内容')
        self.notes_more.setProperty('secondary', True)
        self.notes_more.setAccessibleName('查看更新内容')
        layout.addWidget(self.notes_more, alignment=Qt.AlignmentFlag.AlignLeft)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName('更新内容')
        self.details.setMaximumHeight(160)
        self.details.hide()
        layout.addWidget(self.details)
        self.notes_more.clicked.connect(self._toggle_notes)
        self.sizes = QLabel('')
        self.sizes.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.sizes)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.transfer_label = QLabel('')
        layout.addWidget(self.transfer_label)
        self.status = QLabel('')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.check_button = QPushButton('检查更新')
        self.download_button = QPushButton('更新到最新版本')
        self.download_button.setObjectName('primaryButton')
        self.download_button.setProperty('primary', True)
        self.cancel_button = QPushButton('取消')
        for button in (self.check_button, self.download_button, self.cancel_button):
            button.setAccessibleName(button.text())
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        if compact:
            actions.removeWidget(self.check_button)
            version_row.addWidget(self.check_button)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(5)
            self.download_button.setStyleSheet('min-height: 32px; padding: 3px 12px;')
            self.check_button.setStyleSheet('min-height: 24px; padding: 2px 6px; border: 0; background: transparent;')
            self.cancel_button.setStyleSheet('min-height: 24px; padding: 2px 6px;')
        self.check_button.clicked.connect(lambda: self._run('check'))
        self.download_button.clicked.connect(self._primary_action)
        self.cancel_button.clicked.connect(self._cancel)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QTimer.singleShot(0, self._bind)
        QTimer.singleShot(auto_delay_ms, self._start)
        self.refresh()

    @property
    def _thread(self):
        return self.controller._thread

    def _bind(self):
        if not self._closed:
            window = self.window()
            while window.parentWidget() is not None:
                window = window.parentWidget().window()
            self.controller.bind_window(window)
            if not self._restored:
                self._restored = True
                self.controller.restore_navigation()

    def _start(self):
        if not self._closed:
            self.controller.start()

    def _run(self, action):
        if not self._closed:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            self._bind()
            self.controller.run(action)
            self.refresh()
            QTimer.singleShot(0, self._keep_card_visible)

    def _primary_action(self):
        repair = self.controller.state == UpdateState.REPAIR_REQUIRED or (
            self.controller.state == UpdateState.RECOVERABLE_ERROR and
            (self.controller._requirement == 'REPAIR_REQUIRED' or self.controller._installation_verified is False))
        self._run('repair' if repair else 'download')

    def _cancel(self):
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.controller.cancel()

    def _keep_card_visible(self):
        if self._closed or not self.isVisible():
            return
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(self, 0, 12)
                return
            parent = parent.parentWidget()

    def _toggle_notes(self):
        expanded = not self.details.isVisible()
        self.details.setVisible(expanded)
        self.notes_more.setText('收起更新内容' if expanded else '查看更新内容')
        self.notes_more.setAccessibleName(self.notes_more.text())

    @staticmethod
    def _size(value):
        value = max(0, value)
        return f'{value / 1024**3:.1f} GB' if value >= 1024**3 else f'{value / 1024**2:.1f} MB'

    def refresh(self):
        focus = QApplication.focusWidget()
        owned_focus = focus is self or focus is not None and self.isAncestorOf(focus)
        previous_state = self.property('updateState')
        snap = dict(self.controller.port.snapshot)
        prefix = '版本' if self._compact else '当前版本'
        self.version_label.setText(f"{prefix} {snap.get('current') or VERSION_MANIFEST.get('version', '—')}" + (' ·' if self._compact else ''))
        self.controller.refresh_login_policy()
        state = self.controller.state
        self.setProperty('updateState', state.value)
        preparing = state == UpdateState.PREPARING
        busy = self.controller.busy or preparing
        failed = state == UpdateState.RECOVERABLE_ERROR
        downloading = state == UpdateState.DOWNLOADING
        security = snap.get('error_kind') == 'validation'
        repair = state == UpdateState.REPAIR_REQUIRED or failed and (
            self.controller._requirement == 'REPAIR_REQUIRED' or self.controller._installation_verified is False)
        can_download = bool(snap.get('has_update')) and not (failed and security)
        available = state in (UpdateState.OPTIONAL_UPDATE, UpdateState.MANDATORY_UPDATE) or repair or failed and can_download
        self.check_button.setVisible(state in (UpdateState.UP_TO_DATE, UpdateState.CHECKING,
            UpdateState.OPTIONAL_UPDATE, UpdateState.MANDATORY_UPDATE) or failed and not repair)
        self.check_button.setEnabled(snap.get('available', True) and not busy)
        self.check_button.setText('重试' if failed and not can_download else '检查更新')
        self.download_button.setVisible(available)
        self.download_button.setEnabled(available and not busy)
        self.download_button.setText('修复并检查更新' if repair else '更新到最新版本')
        self.check_button.setAccessibleName(self.check_button.text())
        self.download_button.setAccessibleName(self.download_button.text())
        self.cancel_button.setVisible(downloading)
        self.cancel_button.setEnabled(downloading)
        actual = max(0, snap.get('downloaded', 0))
        expected = max(0, snap.get('total', 0))
        self.sizes.setText(f"新版本 {snap.get('latest', '—')}\n预计下载 {self._size(expected)}" if can_download else '')
        full_notes = release_details(snap.get('notes'))
        summary = release_summary(snap.get('notes'))
        self.notes.setText(summary if can_download else '学习资料和账号数据不参与程序更新。')
        self.notes.setVisible(not self._compact)
        self.sizes.setVisible(not self._compact)
        if full_notes != self._notes_text:
            self._notes_text = full_notes
            self.details.setPlainText(full_notes)
            self.details.hide()
            self.notes_more.setText('查看更新内容')
            self.notes_more.setAccessibleName('查看更新内容')
        more = not self._compact and can_download and full_notes != summary
        self.notes_more.setVisible(more)
        if not more:
            self.details.hide()
        self.progress.setVisible(downloading)
        self.progress.setRange(0, 100)
        percent = min(100, round(actual * 100 / max(1, expected)))
        self.progress.setValue(percent)
        self.transfer_label.setVisible(downloading)
        self.transfer_label.setText(f'{self._size(actual)} / {self._size(max(actual, expected))}')
        messages = {
            UpdateState.CHECKING: '正在检查更新…',
            UpdateState.UPDATE_AVAILABLE: '下载完成后将自动更新并重新启动。',
            UpdateState.MANDATORY_UPDATE: '当前版本需要更新后才能继续',
            UpdateState.REPAIR_REQUIRED: 'MentorFlow 需要修复',
            UpdateState.DOWNLOADING: '正在下载更新',
            UpdateState.PREPARING: '正在准备更新…\nMentorFlow 将自动重新启动',
            UpdateState.UP_TO_DATE: '✓ 已是最新版本',
            UpdateState.DEFERRED_TO_NEXT_LAUNCH: '更新已下载，将在下次启动时完成。',
            UpdateState.RECOVERABLE_ERROR: ('更新文件验证失败，当前版本不会受到影响。' if security
                else '下载已取消，可重新更新。' if self.controller._cancelled or snap.get('error_kind') == 'cancelled'
                else '暂时无法检查更新，请稍后重试。'),
        }
        text = messages[state]
        if self._compact and state == UpdateState.OPTIONAL_UPDATE:
            text = f"发现 {snap.get('latest') or '新版本'}"
        if repair and failed:
            text = '暂时无法修复，请稍后重试。'
        self.status.setText(text)
        self.status.setVisible(not self._compact or state != UpdateState.CHECKING)
        # Hiding a focused action must not transfer focus to controls farther
        # down Settings and scroll the progress out of the user's viewport.
        if owned_focus and self.isVisible():
            if focus is self or not focus.isVisible() or not focus.isEnabled():
                target = next((button for button in (self.cancel_button,
                    self.download_button, self.check_button) if button.isVisible() and button.isEnabled()), self)
                target.setFocus(Qt.FocusReason.OtherFocusReason)
            if previous_state != state.value:
                QTimer.singleShot(0, self._keep_card_visible)

    def shutdown(self):
        self._closed = True
        self.timer.stop()
        if not self._shared:
            self.controller.shutdown()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
