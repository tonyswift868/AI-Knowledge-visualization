"""Native Settings update card using the existing update_service facade."""
from __future__ import annotations
import logging
import threading
from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QProgressBar, QCheckBox)
from .version import VERSION_MANIFEST
from .update_service import configured_mature_service

_AUTO = object()


class UpdatePanel(QFrame):
    def __init__(self, parent=None, *, service=_AUTO, auto_delay_ms=5000):
        super().__init__(parent)
        self.setObjectName('updateCenter')
        self.service = None
        self._thread = None
        self._closed = False
        self._error = ''
        try:
            self.service = configured_mature_service() if service is _AUTO else service
        except Exception:
            logging.getLogger(__name__).exception('update configuration rejected')
            self._error = '更新配置校验失败；当前版本可继续使用。'
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12,10,12,10)
        layout.addWidget(QLabel('软件版本与更新'))
        version = self.service.current if self.service else VERSION_MANIFEST.get('version','unknown')
        channel = self.service.channel if self.service else 'stable'
        self.version_label = QLabel(f'当前版本：{version} · 更新频道：{channel}')
        layout.addWidget(self.version_label)
        self.checked_label = QLabel('最近检查：尚未检查')
        layout.addWidget(self.checked_label)
        self.notes = QLabel('默认只下载变化文件；学习资料和账号数据不参与程序更新。')
        self.notes.setTextFormat(Qt.TextFormat.PlainText)
        self.notes.setWordWrap(True); layout.addWidget(self.notes)
        self.sizes = QLabel('最新版本：— · 完整版本大小：— · 预计下载：— · 实际下载：—')
        self.sizes.setWordWrap(True); layout.addWidget(self.sizes)
        actions = QHBoxLayout()
        self.check_button = QPushButton('检查更新')
        self.download_button = QPushButton('更新')
        self.cancel_button = QPushButton('取消下载')
        self.apply_button = QPushButton('更新并重启')
        self.later_button = QPushButton('稍后')
        for button in (self.check_button,self.download_button,self.cancel_button,self.apply_button,self.later_button):
            button.setAccessibleName(button.text()); actions.addWidget(button)
        layout.addLayout(actions)
        self.progress = QProgressBar(); self.progress.setRange(0,100)
        layout.addWidget(self.progress)
        self.on_exit = QCheckBox('退出时更新')
        self.on_exit.setEnabled(False)
        self.on_exit.hide()
        layout.addWidget(self.on_exit)
        self.status = QLabel(''); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.check_button.clicked.connect(lambda:self._run('check'))
        self.download_button.clicked.connect(lambda:self._run('download'))
        self.cancel_button.clicked.connect(self._cancel)
        self.apply_button.clicked.connect(lambda:self._run('apply_and_restart'))
        self.later_button.clicked.connect(self._later)
        self.timer=QTimer(self); self.timer.setInterval(100); self.timer.timeout.connect(self.refresh); self.timer.start()
        self.refresh()
        if self.service:
            QTimer.singleShot(auto_delay_ms,lambda:self._run('check',manual=False) if not self._closed else None)

    def _run(self, action, **kwargs):
        if self._closed or self.service is None or self._thread and self._thread.is_alive():
            return
        def work():
            try:
                getattr(self.service,action)(**kwargs)
            except Exception:
                logging.getLogger(__name__).exception('update UI action failed')
                self._error='暂时无法执行更新，当前版本可继续使用。'
        self._error=''
        self._thread=threading.Thread(target=work,daemon=True,name='mentorflow-update-ui')
        self._thread.start()
        self.refresh()

    def _cancel(self):
        if self.service: self.service.cancel_download()

    def _later(self):
        self.status.setText('已保留下载进度；你可以继续使用当前版本。')

    @staticmethod
    def _size(value):
        return f'{value/1024**3:.1f} GB' if value>=1024**3 else f'{value/1024**2:.1f} MB'

    def refresh(self):
        if self.service is None:
            self.status.setText(self._error or '暂时无法检查更新，当前版本可继续使用。')
            for button in (self.check_button,self.download_button,self.cancel_button,self.apply_button,self.later_button): button.setEnabled(False)
            return
        snap=dict(self.service.snapshot); state=snap['state']
        running=bool(self._thread and self._thread.is_alive())
        running=running or state in ('APPLYING','RESTART_REQUIRED')
        self.check_button.setEnabled(not running)
        self.download_button.setEnabled(not running and self.service.plan is not None and bool(self.service.plan.get('update')) and state != 'FAILED_SECURITY')
        self.cancel_button.setEnabled(state in ('PREPARING','DOWNLOADING'))
        self.apply_button.setEnabled(not running and state=='READY_TO_APPLY' and self.service.apply_bridge is not None)
        self.apply_button.setToolTip('当前暂不能重启更新。' if self.service.apply_bridge is None else '')
        self.later_button.setEnabled(not running)
        checked=snap.get('last_checked')
        self.checked_label.setText('最近检查：'+(datetime.fromtimestamp(checked).strftime('%Y-%m-%d %H:%M:%S') if checked else '尚未检查'))
        actual=snap.get('actual_downloaded_bytes',0); expected=snap.get('expected_download_bytes',0)
        self.sizes.setText(f"最新版本：{snap.get('latest','—')} · 下载大小：{self._size(expected)}")
        if snap.get('release_notes'): self.notes.setText(snap['release_notes'])
        preparing=state=='PREPARING' or state=='DOWNLOADING' and actual>=expected
        self.progress.setRange(0,0 if preparing else 100)
        self.progress.setValue(100 if state=='READY_TO_APPLY' else min(99,int(actual*100/max(1,expected))))
        self.status.setText(self._error or snap['message'])
        if preparing and not self._error:self.status.setText('正在准备更新…')

    def shutdown(self):
        self._closed=True; self.timer.stop(); self._cancel()
        if self.service and hasattr(self.service,'close'): self.service.close()

    def closeEvent(self,event):
        self.shutdown(); super().closeEvent(event)
