"""User-facing update card; package/transaction diagnostics stay outside the UI."""
from __future__ import annotations

from datetime import datetime
import re

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar, QCheckBox, QScrollArea

from .version import VERSION_MANIFEST
from .update_service import configured_mature_service
from .update_controller import UpdateController, UpdateState, application_updater

_AUTO = object()


def release_summary(text):
    """Plain, bounded release prose; technical diagnostics are not release notes."""
    lines = [line.strip() for line in str(text or '').splitlines() if line.strip()]
    lines = [line for line in lines if not re.search(
        r'manifest|hardlink|receipt|source.acceptance|helper|\b[0-9a-f]{40,}\b', line, re.I)]
    return '\n'.join(lines[:3])[:400] or '此更新包含功能改进与问题修复。'


class UpdatePanel(QFrame):
    def __init__(self, parent=None, *, service=_AUTO, auto_delay_ms=5000):
        super().__init__(parent)
        self.setObjectName('updateCenter')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._shared = service is _AUTO
        self.controller = (application_updater(configured_mature_service) if self._shared
                           else UpdateController(service, self))
        self.service = self.controller.service
        self._closed = False
        self._restored = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(QLabel('软件版本与更新'))
        version = self.service.current if self.service else VERSION_MANIFEST.get('version', '—')
        self.version_label = QLabel(f'当前版本：{version}')
        layout.addWidget(self.version_label)
        self.checked_label = QLabel('最近检查：尚未检查')
        layout.addWidget(self.checked_label)
        self.notes = QLabel('学习资料和账号数据不参与程序更新。')
        self.notes.setTextFormat(Qt.TextFormat.PlainText)
        self.notes.setWordWrap(True)
        layout.addWidget(self.notes)
        self.sizes = QLabel('')
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
        self.download_button = QPushButton('更新')
        self.cancel_button = QPushButton('取消下载')
        self.apply_button = QPushButton('重启并更新')
        self.later_button = QPushButton('稍后')
        for button in (self.check_button, self.download_button, self.cancel_button, self.apply_button, self.later_button):
            button.setAccessibleName(button.text())
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        # Hidden compatibility attribute; never start an installer from quit.
        self.on_exit = QCheckBox('退出时更新', self)
        self.on_exit.hide()
        self.check_button.clicked.connect(lambda: self._run('check'))
        self.download_button.clicked.connect(lambda: self._run('download'))
        self.cancel_button.clicked.connect(self._cancel)
        self.apply_button.clicked.connect(lambda: self._run('apply_and_restart'))
        self.later_button.clicked.connect(self._later)
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

    def _run(self, action, **kwargs):
        if not self._closed:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            self._bind()
            self.controller.run(action, **kwargs)
            self.refresh()
            QTimer.singleShot(0, self._keep_card_visible)

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

    def _later(self):
        self.controller.remember_pending()
        self.controller._resume = False

    @staticmethod
    def _size(value):
        value = max(0, value)
        return f'{value / 1024**3:.1f} GB' if value >= 1024**3 else f'{value / 1024**2:.1f} MB'

    def refresh(self):
        focus = QApplication.focusWidget()
        owned_focus = focus is self or focus is not None and self.isAncestorOf(focus)
        previous_state = self.property('updateState')
        service = self.service
        snap = dict(service.snapshot) if service else {}
        state = self.controller.state
        self.setProperty('updateState', state.value)
        busy = self.controller.busy or state == UpdateState.APPLYING
        raw_state = snap.get('state')
        ready = state == UpdateState.READY_TO_RESTART
        failed = state == UpdateState.RECOVERABLE_ERROR
        downloading = state == UpdateState.DOWNLOADING
        security = raw_state == 'FAILED_SECURITY'
        can_download = bool(service and service.plan and service.plan.get('update')) and not security
        self.check_button.setVisible(not downloading and not ready and state != UpdateState.APPLYING)
        self.check_button.setEnabled(bool(service) and not busy)
        self.check_button.setText('重试' if failed and not can_download else '检查更新')
        self.download_button.setVisible(can_download and not ready and not downloading and state != UpdateState.APPLYING)
        self.download_button.setEnabled(can_download and not busy)
        self.download_button.setText('重试下载' if failed else '更新')
        self.check_button.setAccessibleName(self.check_button.text())
        self.download_button.setAccessibleName(self.download_button.text())
        self.cancel_button.setVisible(downloading)
        self.cancel_button.setEnabled(downloading)
        self.apply_button.setVisible(ready or state == UpdateState.APPLYING)
        self.apply_button.setEnabled(ready and not busy and service is not None and service.apply_bridge is not None)
        self.later_button.setVisible(ready)
        self.later_button.setEnabled(not busy)
        checked = snap.get('last_checked')
        self.checked_label.setText('最近检查：' + (datetime.fromtimestamp(checked).strftime('%Y-%m-%d %H:%M') if checked else '尚未检查'))
        actual = max(0, snap.get('actual_downloaded_bytes', 0))
        expected = max(0, snap.get('expected_download_bytes', 0))
        self.sizes.setText(f"新版本：{snap.get('latest', '—')} · 下载大小：{self._size(expected)}" if can_download else '')
        self.notes.setText(release_summary(snap.get('release_notes')) if can_download else '学习资料和账号数据不参与程序更新。')
        preparing = raw_state == 'PREPARING' or downloading and actual >= expected
        self.progress.setVisible(downloading or ready)
        self.progress.setRange(0, 0 if preparing else 100)
        percent = 100 if ready else min(99, int(actual * 100 / max(1, expected)))
        self.progress.setValue(percent)
        self.transfer_label.setVisible(downloading or ready)
        self.transfer_label.setText(f'{percent}% · {self._size(actual)} / {self._size(max(actual, expected))}')
        messages = {
            UpdateState.CHECKING: '正在检查更新…',
            UpdateState.UPDATE_AVAILABLE: '发现新版本，准备好后点击“更新”。',
            UpdateState.DOWNLOADING: '正在准备更新…' if preparing else '正在下载更新…',
            UpdateState.READY_TO_RESTART: '更新已下载。重启后即可使用新版本；也可以稍后继续。',
            UpdateState.APPLYING: '正在重启并更新，完成后会自动打开 MentorFlow…',
            UpdateState.UP_TO_DATE: '已是最新版本',
            UpdateState.RECOVERABLE_ERROR: ('更新校验未通过，请重新检查。当前版本可继续使用。' if security
                else '下载已暂停，可重试。当前版本可继续使用。' if raw_state == 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
                else '暂时无法完成更新，请重试。当前版本可继续使用。'),
        }
        self.status.setText(self.controller._error or messages[state])
        # Hiding a focused action must not transfer focus to controls farther
        # down Settings and scroll the progress out of the user's viewport.
        if owned_focus and self.isVisible():
            if focus is self or not focus.isVisible() or not focus.isEnabled():
                target = next((button for button in (self.cancel_button, self.apply_button,
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
