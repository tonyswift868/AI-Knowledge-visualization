"""MentorFlow update/repair presentation; the existing core owns all work."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFrame,
                              QLabel, QPushButton, QProgressBar, QScrollArea,
                              QWidget, QGridLayout, QApplication, QSizePolicy)
from .dialog_theme import apply, theme_of, PALETTES


REASONS = {'security': '安全修复', 'protocol': '更新协议兼容要求',
           'data_compatibility': '数据兼容要求', 'support_floor': '版本兼容要求'}


def mandatory_text(status):
    floor = status.get('mandatory_floor') or status.get('min_supported_version') or status.get('latest', '')
    reason = status.get('mandatory_reason') or 'support_floor'
    return f"版本 {floor} 必须安装：{REASONS.get(reason, reason)}"


class UpdateDialog(QDialog):
    install_requested = Signal()
    defer_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle('MentorFlow 更新')
        self.setModal(True)
        width, height = self._initial_size(parent)
        self.resize(width, height)
        layout = QVBoxLayout(self); layout.setContentsMargins(16, 16, 16, 16)
        card = QFrame(); card.setObjectName('settingsCard'); layout.addWidget(card)
        body = QVBoxLayout(card); body.setContentsMargins(26, 24, 26, 24); body.setSpacing(16)
        self.heading = QLabel('MentorFlow 更新'); self.heading.setObjectName('pageHeading'); body.addWidget(self.heading)
        self.versions = QLabel(); self.versions.setWordWrap(True)
        self.versions.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body.addWidget(self.versions)
        # Keep potentially long release notes and server messages in a scrollable
        # area. The buttons stay visible and the dialog never grows beyond its
        # parent/screen, which also prevents a narrow login window clipping it.
        self.details_scroll = QScrollArea(); self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.details_scroll.setMinimumWidth(0)
        self.details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        details = QWidget(); details.setMinimumWidth(0); self.details_layout = QVBoxLayout(details)
        self.details_layout.setContentsMargins(0, 0, 0, 0); self.details_layout.setSpacing(12)
        self.notes = QLabel(); self.notes.setWordWrap(True); self.notes.setTextFormat(Qt.TextFormat.PlainText); self.details_layout.addWidget(self.notes)
        self.reason = QLabel(); self.reason.setWordWrap(True); self.details_layout.addWidget(self.reason)
        self.message = QLabel(); self.message.setWordWrap(True); self.message.setTextFormat(Qt.TextFormat.PlainText); self.details_layout.addWidget(self.message)
        self.progress = QProgressBar(); self.progress.setRange(0, 1000); self.details_layout.addWidget(self.progress)
        self.bytes = QLabel(); self.bytes.setWordWrap(True); self.details_layout.addWidget(self.bytes)
        for label in (self.notes, self.reason, self.message, self.bytes):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.details_scroll.setWidget(details); body.addWidget(self.details_scroll, 1)
        row = QGridLayout(); row.setContentsMargins(0, 0, 0, 0); body.addLayout(row)
        self.install = QPushButton('立即更新并重启'); self.later = QPushButton('稍后')
        if width < 380:
            row.addWidget(self.install, 0, 0); row.addWidget(self.later, 1, 0)
        else:
            row.addWidget(self.install, 0, 0); row.addWidget(self.later, 0, 1)
        self.install.clicked.connect(self._install_or_close)
        self.later.clicked.connect(self._later)
        self._mandatory = False; self._busy = False; self._pending_install = False
        apply(self, theme_of(parent), {self.install: 'primary', self.later: 'ghost'})
        colors = PALETTES.get(theme_of(parent), PALETTES['sakura'])
        self.setStyleSheet(self.styleSheet() + f"QPushButton[variant=\"ghost\"]:disabled {{color:{colors['text3']}; background:transparent}} QProgressBar {{border:0; border-radius:6px; background:{colors['soft']}; text-align:center; color:{colors['text2']}; min-height:12px}} QProgressBar::chunk {{border-radius:6px; background:{colors['accent']}}}")
        self.setFixedSize(width, height)
        self._place_over_parent(parent)

    @staticmethod
    def _initial_size(parent):
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        max_width = min(560, max(280, available.width() - 48)) if available else 560
        max_height = min(560, max(320, available.height() - 48)) if available else 560
        if parent is not None:
            parent_width, parent_height = parent.width(), parent.height()
            if parent_width > 0: max_width = min(max_width, max(280, parent_width - 24))
            if parent_height > 0: max_height = min(max_height, max(320, parent_height - 24))
        return max_width, max_height

    def _place_over_parent(self, parent):
        if parent is not None and parent.width() > 0 and parent.height() > 0:
            point = parent.mapToGlobal(parent.rect().center())
        else:
            screen = QApplication.primaryScreen()
            point = screen.availableGeometry().center() if screen is not None else None
        if point is not None:
            self.move(point.x() - self.width() // 2, point.y() - self.height() // 2)

    def _later(self):
        if not self._mandatory and not self._busy:
            self.defer_requested.emit(); self.hide()

    def _install_or_close(self):
        if self._pending_install:
            self.close_requested.emit()
        else:
            self.install_requested.emit()

    def reject(self):
        self._later()  # Esc must never unlock a mandatory login gate.

    def update_status(self, status):
        self._mandatory = bool(status.get('mandatory') or status.get('state') == 'MANDATORY_UPDATE')
        self._busy = bool(status.get('busy'))
        self._pending_install = status.get('state') == 'PENDING_INSTALL'
        repair = status.get('state') in ('REPAIR_REQUIRED', 'FATAL_UNRECOVERABLE')
        self.heading.setText('MentorFlow 安装检查' if repair else 'MentorFlow 更新')
        self.versions.setText(f"当前版本 {status.get('current', '')} → 新版本 {status.get('latest') or status.get('target', '')}")
        self.notes.setText(status.get('release_notes') or ('请使用可信安装包修复。' if repair else '此版本未提供更新说明。'))
        self.reason.setText(mandatory_text(status) if self._mandatory else '')
        self.message.setText(status.get('message', ''))
        self.later.setEnabled(not self._mandatory and not self._busy)
        self.later.setVisible(not (self._mandatory and status.get('hide_later')))
        self.install.setText('关闭软件' if self._pending_install else
                             '修复安装' if repair else
                             '继续' if status.get('state') == 'DOWNLOAD_PAUSED_OR_INTERRUPTED' else
                             '立即更新' if self._mandatory else '立即更新并重启')
        self.install.setEnabled(not self._busy and status.get('state') not in ('FAILED_SECURITY', 'FATAL_UNRECOVERABLE'))
        total = status.get('expected_download_bytes', 0) or 0
        done = status.get('downloaded_bytes', status.get('actual_downloaded_bytes', 0)) or 0
        self.progress.setVisible(self._busy or bool(total)); self.bytes.setVisible(self._busy or bool(total))
        self.progress.setRange(0, 1000 if total else 0)
        if total: self.progress.setValue(min(1000, int(done * 1000 / total)))
        self.bytes.setText(f'{done / 1048576:.2f} MiB / {total / 1048576:.2f} MiB')
