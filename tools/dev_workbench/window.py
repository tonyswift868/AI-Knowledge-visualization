"""Summary-first Qt projection. Launch admission remains owned by targets.py."""
import json
import threading

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QTextOption
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QListWidget, QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget)

from .authority import RepoSource, load_view, public_text
from .presentation import details_payload, summarize
from .resources import Resources, COLORS
from .targets import Refused, TrustedPaths, launch_exact, navigation_path


def label(text='', size=14, bold=False):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(True)
    item.setMinimumWidth(0)
    item.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    item.setStyleSheet(f'font-size:{size}px; font-weight:{600 if bold else 400};')
    return item


class Job(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try: self.done.emit(self.operation())
        except Refused as exc: self.failed.emit(str(exc))
        except Exception: self.failed.emit('OPERATION_FAILED')


class Workbench(QMainWindow):
    def __init__(self, source_factory=RepoSource, paths=None, *, auto_load=True, resources=None):
        super().__init__()
        self.source_factory, self.paths = source_factory, paths or TrustedPaths()
        self.resources = resources or Resources()
        self.view, self.jobs, self.summary = {}, [], {}
        self.cancel = threading.Event()
        self.last_error = None
        self._wide = None
        self.setWindowTitle(self.tr_text('labels', 'title'))
        self.resize(1100, 900)
        self.setMinimumSize(280, 320)
        self.setStyleSheet('''
            QMainWindow, QScrollArea, QWidget#content {background:#f3f5f9;}
            QWidget {font-family:"Microsoft YaHei UI","Segoe UI"; font-size:14px; color:#18243b;}
            QScrollArea {border:0;} QFrame#card {background:white; border:1px solid #dfe5ef; border-radius:10px;}
            QFrame#actionCard {background:#edf4ff; border:2px solid #adc8f3; border-radius:10px;}
            QLabel {background:transparent; border:0;}
            QPushButton {background:white; border:1px solid #cbd5e1; border-radius:6px; padding:7px 10px;}
            QPushButton:hover {background:#e5edff;} QPushButton:disabled {color:#94a3b8; background:#f1f5f9;}
            QPushButton#primary {background:#2458d3; color:white; border:0; font-weight:600; padding:11px;}
            QPushButton#primary:disabled {background:#dce5f7; color:#64748b;}
            QPushButton#worker {text-align:left; border:0; background:#f7f9fc;}
            QTextEdit {background:white; border:1px solid #dfe5ef;}
        ''')
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.pages = QTabWidget(); self.pages.addTab(self.scroll, self.tr_text('labels', 'overview'))
        self.setCentralWidget(self.pages)
        self.content = QWidget(); self.content.setObjectName('content'); self.scroll.setWidget(self.content)
        layout = QVBoxLayout(self.content); layout.setContentsMargins(12, 12, 12, 12); layout.setSpacing(10)
        self.sections = []
        header, top = self.card()
        self.title = label(self.tr_text('labels', 'title'), 23, True); top.addWidget(self.title)
        self.phase = label(self.tr_text('messages', 'loading'), 17, True); top.addWidget(self.phase)
        self.overall = label(); top.addWidget(self.overall)
        self.blockers = label(); top.addWidget(self.blockers); self.blockers.hide()
        self.header_controls = QGridLayout()
        self.integration = label(); self.integration.setMaximumWidth(260)
        self.integration.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.copy_button = self.button('copy', self.copy_integration)
        self.engineering_button = self.button('engineering', self.engineering_details)
        self.refresh_button = self.button('refresh', self.refresh)
        top.addLayout(self.header_controls)
        self.provenance = label(self.tr_text('labels', 'snapshot'), 12); top.addWidget(self.provenance)
        layout.addWidget(header)
        action_card, action = self.card('action', action=True)
        self.action_text = label(self.tr_text('messages', 'loading'), 25, True)
        self.criteria = label('', 15)
        action.addWidget(self.action_text); action.addWidget(self.criteria)
        self.action_button = self.button('instructions', self.primary_action, primary=True)
        action.addWidget(self.action_button); self.action_button.hide()
        self.notice = label(); action.addWidget(self.notice); self.notice.hide()
        layout.addWidget(action_card)
        target_card, target = self.card('target')
        self.target_title = label('', 19, True); target.addWidget(self.target_title)
        self.target_version = label('', 16, True); target.addWidget(self.target_version)
        self.target_purpose = label(); target.addWidget(self.target_purpose)
        self.launch_button = self.button('launch', self.launch, primary=True)
        self.nav_buttons = {'directory': self.button('directory', lambda: self.navigate('directory'))}
        self.version_button = self.button('version_details', self.version_details)
        self.target_controls = QGridLayout(); target.addLayout(self.target_controls)
        layout.addWidget(target_card)
        self.bottom = QGridLayout(); self.bottom.setSpacing(10)
        self.workflow_card, flow = self.card('workflow')
        self.flow_rows = []
        for _ in range(5):
            row = QHBoxLayout(); title, status = label(), label()
            row.addWidget(title, 3); row.addWidget(status, 2); flow.addLayout(row)
            self.flow_rows.append((title, status))
        self.workflow_note = label('', 12); flow.addWidget(self.workflow_note); self.workflow_note.hide()
        flow.addStretch()
        self.workers_card, workers = self.card('workers')
        self.worker_rows, self.worker_layouts = [], []
        for index in range(5):
            button = QPushButton(); button.setObjectName('worker')
            button.clicked.connect(lambda checked=False, i=index: self.worker_details(i))
            summary = label('', 13)
            row = QGridLayout(); row.addWidget(button, 0, 0); row.addWidget(summary, 1, 0)
            workers.addLayout(row); self.worker_layouts.append(row)
            self.worker_rows.append((button, summary))
        workers.addStretch()
        layout.addLayout(self.bottom)
        layout.addStretch()
        self.build_details_dialog()
        from .device_panel import DevicePanel
        self.device_panel = DevicePanel(self, label)
        self.pages.addTab(self.device_panel, self.tr_text('labels', 'devices'))
        self.launch_button.setEnabled(False)
        self.reflow()
        if auto_load: self.refresh()

    def tr_text(self, group, key, **values):
        return self.resources.text(group, key, **values)

    def button(self, key, callback, *, primary=False):
        button = QPushButton(self.tr_text('actions', key))
        if primary: button.setObjectName('primary')
        button.clicked.connect(callback)
        return button

    def card(self, title=None, *, action=False):
        frame = QFrame(); frame.setObjectName('actionCard' if action else 'card')
        frame.setMinimumWidth(0)
        body = QVBoxLayout(frame); body.setContentsMargins(14, 12, 14, 12); body.setSpacing(8)
        if title: body.addWidget(label(self.tr_text('labels', title), 16, True))
        self.sections.append(frame)
        return frame, body

    def reflow(self):
        wide = self.width() >= 760
        self.title.setText(self.tr_text('labels', 'title_compact' if self.width() < 400 else 'title'))
        if wide == self._wide: return
        self._wide = wide
        self.integration.setWordWrap(not wide)
        for index, widget in enumerate((self.integration, self.copy_button, self.engineering_button, self.refresh_button)):
            self.header_controls.removeWidget(widget)
            self.header_controls.addWidget(widget, 0 if wide else index // 2, index if wide else index % 2)
        self.header_controls.setColumnStretch(0, 1 if not wide else 0)
        self.header_controls.setColumnStretch(4, 1 if wide else 0)
        for index, button in enumerate((self.launch_button, self.nav_buttons['directory'], self.version_button)):
            self.target_controls.removeWidget(button)
            self.target_controls.addWidget(button, 0 if wide else index, index if wide else 0)
        self.target_controls.setColumnStretch(3, 1 if wide else 0)
        self.bottom.removeWidget(self.workflow_card); self.bottom.removeWidget(self.workers_card)
        self.bottom.addWidget(self.workflow_card, 0, 0)
        self.bottom.addWidget(self.workers_card, 0 if wide else 1, 1 if wide else 0)
        self.bottom.setColumnStretch(0, 1); self.bottom.setColumnStretch(1, 1 if wide else 0)
        for row, (button, message) in zip(self.worker_layouts, self.worker_rows):
            row.removeWidget(message)
            row.addWidget(message, 0 if wide else 1, 1 if wide else 0)
            row.setColumnStretch(0, 0 if wide else 1); row.setColumnStretch(1, 1 if wide else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'bottom'): self.reflow()

    def build_details_dialog(self):
        self.details_dialog = QDialog(self)
        self.details_dialog.resize(760, 620); self.details_dialog.setMinimumSize(260, 280)
        body = QVBoxLayout(self.details_dialog)
        self.details = QTextEdit(); self.details.setReadOnly(True)
        self.details.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.detail_tabs = QTabWidget()
        self.detail_tabs.addTab(self.details, self.tr_text('labels', 'engineering'))
        self.history = QListWidget(); self.history.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.history.itemDoubleClicked.connect(lambda item: self.history_notice(self.history.row(item), 0))
        self.detail_tabs.addTab(self.history, self.tr_text('labels', 'history'))
        body.addWidget(self.detail_tabs, 1)
        self.account = label(); body.addWidget(self.account); self.account.hide()
        buttons = QGridLayout()
        for index, kind in enumerate(('receipt', 'log')):
            button = self.button(kind, lambda checked=False, k=kind: self.navigate(k))
            self.nav_buttons[kind] = button; buttons.addWidget(button, 0, index)
        buttons.addWidget(self.button('copy_source', self.copy_source), 1, 0)
        self.web_button = self.button('handoff', self.open_handoff); buttons.addWidget(self.web_button, 1, 1)
        body.addLayout(buttons)
        body.addWidget(self.button('close', self.details_dialog.hide))

    def open_details(self, title, value):
        self.details_dialog.setWindowTitle(title)
        self.details.setPlainText(json.dumps(details_payload(value), ensure_ascii=False, indent=2))
        self.detail_tabs.setCurrentIndex(0)
        self.details_dialog.resize(min(760, self.width()), min(620, self.height()))
        self.details_dialog.show(); self.details_dialog.raise_()

    def engineering_details(self):
        self.open_details(self.tr_text('labels', 'engineering'),
                          {**self.view, 'authority_ref': 'refs/heads/integration/main3', 'last_error': self.last_error})

    def version_details(self):
        self.open_details(self.tr_text('labels', 'version_details'), self.view.get('active_target'))

    def worker_details(self, index):
        rows = self.view.get('workers', [])
        if not 0 <= index < len(rows): return
        self.open_details(self.tr_text('labels', 'worker_details', slot=rows[index]['slot']), rows[index])

    def primary_action(self):
        if self.summary.get('target_state') == 'READY': self.launch()
        else: self.open_details(self.tr_text('labels', 'actions_details'), self.view.get('actions'))

    def start_job(self, operation, done):
        self.refresh_button.setEnabled(False); self.launch_button.setEnabled(False)
        self.action_button.setEnabled(False)
        job = Job(operation, self); self.jobs.append(job)
        job.done.connect(done); job.failed.connect(self.failure)
        def finish():
            self.jobs.remove(job); job.deleteLater()
            self.refresh_button.setEnabled(True); self.action_button.setEnabled(True)
            self.launch_button.setEnabled((self.view.get('active_target') or {}).get('status') == 'CURRENT')
            self.device_panel.show_view(self.view)
        job.finished.connect(finish); job.start()

    def set_notice(self, text):
        self.notice.setText(text); self.notice.setVisible(bool(text))

    def refresh(self):
        if self.jobs: return
        self.set_notice(self.tr_text('messages', 'loading'))
        self.start_job(lambda: load_view(self.source_factory(), self.paths), self.show_view)

    def show_view(self, view):
        self.view = view
        self.device_panel.show_view(view)
        self.summary = summary = summarize(view, self.resources)
        self.phase.setText(self.tr_text('labels', 'phase', value=summary['phase']))
        self.overall.setText(self.tr_text('labels', 'overall', value=summary['overall']))
        self.blockers.setText(self.tr_text('labels', 'blocked', stages='、'.join(summary['blocked'])))
        self.blockers.setVisible(bool(summary['blocked']))
        self.integration.setText(self.tr_text('labels', 'integration', sha=summary['integration_short']))
        self.action_text.setText(summary['action_title']); self.criteria.setText(summary['action_sentence'])
        ready = summary['target_state'] == 'READY'
        self.action_button.setVisible(summary['action_required'])
        self.action_button.setText(self.tr_text('actions', 'action_launch' if ready else 'instructions'))
        self.target_title.setText(summary['target_text'])
        self.target_version.setText(self.tr_text('labels', 'version', version=summary['version']))
        self.target_version.setVisible(ready)
        self.target_purpose.setText(self.tr_text('labels', 'purpose', purpose=summary['purpose']) if ready else
            self.tr_text('empty', 'waiting_target' if summary['target_state'] == 'WAITING' else 'none_target'))
        self.launch_button.setVisible(ready); self.launch_button.setEnabled(ready and not self.jobs)
        self.launch_button.setObjectName('secondary' if summary['action_required'] else 'primary')
        self.launch_button.style().unpolish(self.launch_button); self.launch_button.style().polish(self.launch_button)
        active = view.get('active_target') or {}
        self.version_button.setVisible(bool(active))
        self.nav_buttons['directory'].setVisible(ready)
        for kind, button in self.nav_buttons.items():
            button.setEnabled(bool(active.get('artifact_path' if kind == 'directory' else kind + '_path')))
        self.web_button.setEnabled(bool(view.get('handoff_available') and view.get('web_handoff_url')))
        self.account.setText(self.tr_text('labels', 'account', username=public_text(active.get('test_username')),
            reference=public_text(active.get('secret_ref'))) if active.get('test_username') or active.get('secret_ref') else '')
        self.account.setVisible(bool(self.account.text()))
        self.history.clear()
        for target in view.get('history', []):
            self.history.addItem(self.tr_text('statuses', target['status']) + '  ·  ' +
                                 public_text(target.get('product_version')) + '  ·  ' + target['test_id'])
        for (title, status), row in zip(self.flow_rows, summary['workflow']):
            title.setText(row['title']); status.setText(row['text'])
            status.setStyleSheet(f"color:{COLORS.get(row['status'], '#64748b')}; font-weight:600;")
        completed = {row['task_id'] for row in view.get('workflow', []) if row['status'] == 'PASS'}
        mail_note = 'qq_pass' if 'REAL_QQ_DELIVERY' in completed else 'smtp_pass' if 'REAL_SMTP_TRANSPORT' in completed else None
        self.workflow_note.setText(self.tr_text('workflow', mail_note) if mail_note else '')
        self.workflow_note.setVisible(mail_note is not None)
        for index, (button, message) in enumerate(self.worker_rows):
            row = summary['workers'][index] if index < len(summary['workers']) else None
            button.setVisible(row is not None); message.setVisible(row is not None)
            if row:
                button.setText(row['slot'] + '    ' + row['text'] + '   ›')
                button.setAccessibleName(self.tr_text('actions', 'worker', slot=row['slot']))
                message.setText(row['summary'])
        self.set_notice(self.tr_text('messages', 'issues') if view.get('issues') else '')

    def failure(self, code):
        self.last_error = code
        self.set_notice(self.tr_text('errors', code))
        self.device_panel.notice.setText(self.tr_text('errors', code))

    def launch(self):
        if self.jobs: return
        selected = self.view.get('active_target')
        self.set_notice(self.tr_text('messages', 'verifying'))
        self.cancel.clear()
        def operation():
            source = self.source_factory()
            fresh = load_view(source, self.paths).get('active_target')
            process = launch_exact(selected, fresh, source.tree_for, self.paths, cancelled=self.cancel.is_set,
                                   authority_check=source.assert_current)
            return process.pid
        self.start_job(operation, lambda pid: self.set_notice(self.tr_text('messages', 'launched')))

    def navigate(self, kind):
        try:
            path = navigation_path(self.view.get('active_target') or {}, kind, self.paths)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Refused as exc: self.failure(str(exc))

    def copy_integration(self):
        if self.view.get('integration_sha'): QApplication.clipboard().setText(self.view['integration_sha'])

    def copy_source(self):
        value = (self.view.get('active_target') or {}).get('source_sha')
        if value: QApplication.clipboard().setText(value)

    def open_handoff(self):
        url = self.view.get('web_handoff_url')
        if url: QDesktopServices.openUrl(QUrl(url))

    def history_notice(self, row, _column):
        target = self.view['history'][row]
        message = self.tr_text('messages', 'old'
            if target['status'] != 'CURRENT' or target != self.view.get('active_target') else 'current_history')
        self.set_notice(message)
        if self.details_dialog.isVisible():
            self.open_details(self.tr_text('labels', 'history'), {self.tr_text('labels', 'actions_details'): message, **target})

    def closeEvent(self, event):
        if self.jobs:
            self.cancel.set(); self.set_notice(self.tr_text('messages', 'closing')); event.ignore()
        else:
            self.details_dialog.hide(); event.accept()
