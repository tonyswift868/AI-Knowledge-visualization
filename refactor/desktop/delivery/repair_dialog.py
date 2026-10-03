"""Runtime contracts extracted from scripts/windows_installed_launch.py."""
def show_repair(message):
    # Internal exceptions are diagnostics, not product error copy. This fallback
    # is reserved for when even the independent repair runtime is unsafe.
    from PySide6.QtWidgets import QApplication
    from refactor.desktop.update_dialog import UpdateDialog
    app = QApplication.instance() or QApplication([])
    dialog = UpdateDialog()
    dialog.update_status(dict(state='FATAL_UNRECOVERABLE',
        message='MentorFlow 暂时无法安全启动。请使用可信安装包修复；学习资料不会被删除。'))
    dialog.later.setText('关闭')
    dialog.later.clicked.disconnect(); dialog.later.clicked.connect(dialog.accept)
    dialog.exec()
