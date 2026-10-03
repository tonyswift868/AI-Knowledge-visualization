"""Independent branded Qt adapter over the existing recovery core."""
from concurrent.futures import ThreadPoolExecutor
import threading
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QApplication, QPushButton
from refactor.desktop.update_dialog import UpdateDialog
from refactor.desktop.dialog_theme import apply


class RepairWindow(UpdateDialog):
    finished = Signal(object)

    def __init__(self, root, core, install):
        super().__init__()
        self.root, self.core, self.engine = root, core, install
        self.cancel = threading.Event(); self.busy = False; self.closing = False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mf-repair')
        self.result = dict(state='REPAIR_REQUIRED', message='检查安装、修复或更新，无需登录。')
        self.check_install = QPushButton('检查安装'); self.check_update = QPushButton('检查更新')
        self.layout().addWidget(self.check_install); self.layout().addWidget(self.check_update)
        self.install_requested.connect(lambda: self.run('repair'))
        self.check_install.clicked.connect(lambda: self.run('startup'))
        self.check_update.clicked.connect(lambda: self.run('check'))
        self.finished.connect(self.receive, Qt.ConnectionType.QueuedConnection)
        self.later.setText('关闭')
        # Closing exits this repair process, never permits learning on a broken App.
        self.later.clicked.disconnect(); self.later.clicked.connect(self.reject)
        # Base dialog already applies the shared theme, including progress.
        self.update_status(self.result)

    def run(self, action):
        if self.busy: return
        self.busy = True; self.cancel.clear()
        self.update_status({**self.result, 'busy': True, 'message': '正在检查并准备修复…'})
        self.check_install.setEnabled(False); self.check_update.setEnabled(False)
        def work():
            try:
                if not self.engine.is_install(self.root): self.core.bootstrap_legacy_identity(self.root)
                if action == 'startup': value = self.core.get_startup_update_state(self.root, check_feed=False)
                elif action == 'check': value = self.core.check_latest(self.root, cancel=self.cancel.is_set)
                else: value = self.core.repair_installation(self.root, cancel=self.cancel.is_set,
                    progress=lambda p: self.finished.emit({'progress': p}))
            except Exception:
                value = dict(state='FAILED_RECOVERABLE', message='修复未完成，请检查网络后重试。学习资料未更改。')
            self.finished.emit({'result': value})
        self.pool.submit(work)

    def receive(self, value):
        if 'progress' in value:
            self.update_status({**self.result, **value['progress'], 'busy': True}); return
        self.busy = False; self.result = {**self.result, **value['result']}
        self.update_status({**self.result, 'busy': False})
        self.install.setText('修复安装 / 更新到最新版本')
        self.check_install.setEnabled(True); self.check_update.setEnabled(True)
        self.later.setEnabled(True)
        if self.closing or self.result.get('state') == 'CURRENT_HEALTHY': self.reject()

    def reject(self):
        if self.busy:
            self.closing = True; self.cancel.set(); self.message.setText('正在安全取消，请稍候…'); return
        self.pool.shutdown(wait=False, cancel_futures=True)
        super(UpdateDialog, self).reject()


def run(root, core, install):
    app = QApplication.instance() or QApplication([])
    dialog = RepairWindow(root, core, install)
    dialog.showNormal(); dialog.raise_(); dialog.activateWindow()
    dialog.exec()
    return dict(state='REPAIR_SHELL_CLOSED')
