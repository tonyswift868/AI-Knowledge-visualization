"""Lightweight Qt developer projection. No product/UI/auth imports."""
import threading

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from .authority import RepoSource, load_view, public_text
from .targets import Refused, TrustedPaths, launch_exact, navigation_path

COLORS = {'PASS': '#167047', 'PASS_STAGE': '#167047', 'CURRENT': '#1d4ed8', 'RUNNING': '#1d4ed8',
          'SILENT': '#64748b', 'BLOCKED': '#b42318', 'FAIL': '#b42318', 'LOCKED': '#64748b',
          'NOT_RUN': '#64748b', 'UNKNOWN': '#64748b', 'USER_ACTION_REQUIRED': '#b45309',
          'WAITING_FOR_ARTIFACT': '#b45309', 'SUPERSEDED': '#64748b'}
ERRORS = {
    'ARTIFACT_HASH_MISMATCH': '这个文件不是当前指定测试版本。',
    'BUILD_IDENTITY_MISMATCH': '构建身份与指定候选不一致。',
    'SOURCE_IDENTITY_MISMATCH': 'Source SHA 与 tree 无法匹配。',
    'UNTRUSTED_PATH': '文件不在受信任的本地产物目录，或路径包含别名。',
    'ARTIFACT_MISSING': '指定的文件尚不存在。',
    'OLD_OR_UNREADY_TARGET': '这是旧测试版本，或候选尚未准备完成。',
    'TARGET_CHANGED': '当前测试目标已更新，请刷新后再启动。',
    'CANCELLED': '已取消启动。',
}


def label(text='', size=None, bold=False):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(True)
    if size: item.setStyleSheet(f'font-size:{size}px; font-weight:{600 if bold else 400};')
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
    def __init__(self, source_factory=RepoSource, paths=None, *, auto_load=True):
        super().__init__()
        self.source_factory, self.paths = source_factory, paths or TrustedPaths()
        self.view, self.jobs = {}, []
        self.cancel = threading.Event()
        self.setWindowTitle('MentorFlow Dev Workbench · 开发工作台')
        self.resize(1240, 910)
        self.setMinimumSize(980, 760)
        self.setStyleSheet('''
            QMainWindow {background:#f3f5f9;} QWidget {font-family:"Segoe UI","Microsoft YaHei UI"; font-size:13px; color:#18243b;}
            QFrame#card {background:white; border:1px solid #dfe5ef; border-radius:10px;}
            QPushButton {background:white; border:1px solid #cbd5e1; border-radius:6px; padding:8px 14px;}
            QPushButton:hover {background:#eef4ff;} QPushButton:disabled {color:#94a3b8; background:#f1f5f9;}
            QPushButton#primary {background:#2458d3; color:white; border:0; font-weight:600;}
            QPushButton#primary:disabled {background:#dce5f7; color:#64748b;}
            QTableWidget {border:0; background:white; gridline-color:#edf0f5; selection-background-color:#eaf0ff; selection-color:#18243b;}
            QHeaderView::section {background:#f7f9fc; border:0; padding:8px; color:#58667f;}
            QTabWidget::pane {border:1px solid #dfe5ef; background:white;} QTextEdit {border:0; background:white;}
        ''')
        root = QWidget(); self.setCentralWidget(root)
        layout = QVBoxLayout(root); layout.setContentsMargins(24, 16, 24, 16); layout.setSpacing(12)
        header = QHBoxLayout()
        title = label('MentorFlow  /  开发工作台', 24, True); title.setWordWrap(False)
        header.addWidget(title); header.addStretch()
        self.refresh_button = QPushButton('刷新 repo 快照'); self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button); layout.addLayout(header)
        self.phase = label('正在读取 repo authority…', 17, True); layout.addWidget(self.phase)
        self.blockers = label('', 13); layout.addWidget(self.blockers)
        self.provenance = label('只读投影 · 不代表员工或服务器的实时状态', 12); layout.addWidget(self.provenance)
        action_card = QFrame(); action_card.setObjectName('card')
        action = QVBoxLayout(action_card); action.setContentsMargins(18, 14, 18, 14)
        action.addWidget(label('现在你需要做什么？', 18, True))
        self.action_text = label('正在读取…', 15); action.addWidget(self.action_text)
        self.target_title = label(); action.addWidget(self.target_title)
        self.criteria = label(); action.addWidget(self.criteria)
        self.account = label(); action.addWidget(self.account)
        buttons = QHBoxLayout()
        self.launch_button = QPushButton('启动当前测试版'); self.launch_button.setObjectName('primary')
        self.launch_button.clicked.connect(self.launch); buttons.addWidget(self.launch_button)
        self.nav_buttons = {}
        for key, title in (('directory', '打开所在目录'), ('receipt', '打开回执'), ('log', '打开日志')):
            button = QPushButton(title); button.clicked.connect(lambda checked=False, k=key: self.navigate(k))
            self.nav_buttons[key] = button; buttons.addWidget(button)
        copy = QPushButton('复制 Source SHA'); copy.clicked.connect(self.copy_source); buttons.addWidget(copy)
        self.web_button = QPushButton('打开网页版接手页'); self.web_button.clicked.connect(self.open_handoff)
        buttons.addWidget(self.web_button); buttons.addStretch(); action.addLayout(buttons)
        self.notice = label(); action.addWidget(self.notice); layout.addWidget(action_card)
        layout.addWidget(label('员工交付看板  ·  A / 01 / 02 / 03 / 04', 16, True))
        self.workers = self.table(['员工', '状态', '当前目标', '阻塞', '下一关'], 5)
        self.workers.setFixedHeight(192); self.workers.itemSelectionChanged.connect(self.worker_details)
        self.workers.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.workers)
        tabs = QTabWidget()
        self.flow = self.table(['阶段 / Gate', '状态', '证据范围'], 0)
        self.flow.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tabs.addTab(self.flow, 'MAIN3 工作流')
        self.history = self.table(['TEST_ID', '版本', 'Source SHA', '状态'], 0)
        self.history.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.history.cellDoubleClicked.connect(self.history_notice)
        tabs.addTab(self.history, '测试目标历史 · 只读')
        self.details = QTextEdit(); self.details.setReadOnly(True); tabs.addTab(self.details, '工程详情 / 来源')
        layout.addWidget(tabs, 1)
        self.footer = label('Workbench 不执行 merge / push / deploy，也不修改治理状态。', 12)
        layout.addWidget(self.footer)
        self.launch_button.setEnabled(False)
        if auto_load: self.refresh()

    @staticmethod
    def table(headers, rows):
        table = QTableWidget(rows, len(headers)); table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.verticalHeader().hide(); table.verticalHeader().setDefaultSectionSize(31)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        return table

    def fill(self, table, rows):
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                value = public_text(value)
                item = QTableWidgetItem(value); item.setToolTip(value)
                if value in COLORS: item.setForeground(QColor(COLORS[value]))
                table.setItem(row, col, item)

    def start_job(self, operation, done):
        self.refresh_button.setEnabled(False); self.launch_button.setEnabled(False)
        job = Job(operation, self); self.jobs.append(job)
        job.done.connect(done); job.failed.connect(self.failure)
        def finish():
            self.jobs.remove(job); job.deleteLater()
            self.refresh_button.setEnabled(True)
            self.launch_button.setEnabled(self.view.get('active_target', {}).get('status') == 'CURRENT'
                                          if self.view.get('active_target') else False)
        job.finished.connect(finish); job.start()

    def refresh(self):
        if self.jobs: return
        self.notice.setText('正在读取同一 Git commit 的权威文件…')
        self.start_job(lambda: load_view(self.source_factory(), self.paths), self.show_view)

    def show_view(self, view):
        self.view = view
        self.phase.setText(f"{view['generation']}  ·  {view['current_phase']}")
        blocked = [item['title'] for item in view['workflow'] if item['status'] in ('BLOCKED', 'LOCKED')]
        self.blockers.setText('阻塞：' + (' · '.join(blocked) if blocked else 'UNKNOWN / 尚无已登记阻塞'))
        self.provenance.setText(f"交付快照 {view['snapshot_at']}  ·  integration {view['integration_sha'][:12]}  ·  实时状态未查询")
        self.action_text.setText(view['action_summary'])
        active = view.get('active_target') or {}
        self.target_title.setText('当前测试目标：' + public_text(active.get('title')) + '  ·  ' + public_text(active.get('status')))
        actions = view['actions']
        if actions:
            self.criteria.setText('\n'.join(public_text(a['title']) + '：' + public_text(a['instruction']) +
                '\n成功标准：' + public_text(a['success_criteria']) for a in actions))
        else:
            self.criteria.setText('等待 01/A 登记完整的 exact 候选回执后，此处将开放启动。' if active.get('status') == 'WAITING_FOR_ARTIFACT' else '')
        self.account.setText(('测试账号：' + public_text(active.get('test_username')) +
            '  ·  密码：SECURE_REFERENCE_ONLY\n引用：' + public_text(active.get('secret_ref')))
            if active.get('test_username') or active.get('secret_ref') else '')
        self.account.setVisible(bool(self.account.text()))
        self.launch_button.setEnabled(active.get('status') == 'CURRENT')
        for kind, button in self.nav_buttons.items():
            button.setEnabled(bool(active.get('artifact_path' if kind == 'directory' else kind + '_path')))
        self.web_button.setEnabled(bool(view.get('handoff_available') and view.get('web_handoff_url')))
        self.fill(self.workers, [(w['slot'], w['status'],
            '此快照未登记新任务；详细交付说明见工程详情' if w['status'] == 'SILENT' and w['task_id'] == 'UNKNOWN'
            else w['objective'], w['blocker'], w['next_gate']) for w in view['workers']])
        self.fill(self.flow, [(s['title'], s['status'], s['reason']) for s in view['workflow']])
        self.fill(self.history, [(t['test_id'], t['product_version'], (t['source_sha'] or 'UNKNOWN')[:12], t['status']) for t in view['history']])
        self.notice.setText('；'.join(view['issues']) or '仅启动当前指定候选；路径、SHA256、source/build 身份全部匹配后才启动。')
        details = '\n'.join(f'{key} = {public_text(view.get(key))}' for key in (
            'project', 'generation', 'current_phase', 'current_gate', 'integration_sha', 'integration_tree',
            'remote_head', 'snapshot_at', 'snapshot_kind', 'staging_status', 'database_revision', 'source_schema'))
        self.base_details = details + '\n\n远端 SHA 仅为本地已有快照，未 fetch。数据库运行态与源码 schema 分别显示。'
        self.details.setPlainText(self.base_details)

    def worker_details(self):
        index = self.workers.currentRow()
        rows = self.view.get('workers', [])
        if not 0 <= index < len(rows): return
        self.details.setPlainText(getattr(self, 'base_details', '') + '\n\n员工明细\n' +
            '\n'.join(f'{k} = {public_text(v)}' for k, v in rows[index].items()))

    def failure(self, code):
        self.notice.setText('REFUSE_TO_LAUNCH / ' + ERRORS.get(code, '无法完成操作；请检查已登记的权威文件。') + ' [' + code + ']')

    def launch(self):
        if self.jobs: return
        selected = self.view.get('active_target')
        self.notice.setText('正在核验 exact 候选…')
        self.cancel.clear()
        def operation():
            source = self.source_factory()
            fresh = load_view(source, self.paths).get('active_target')
            process = launch_exact(selected, fresh, source.tree_for, self.paths, cancelled=self.cancel.is_set)
            return process.pid
        self.start_job(operation, lambda pid: self.notice.setText(f'已启动已验证的指定候选。进程 {pid}；验收结果仍需回执确认。'))

    def navigate(self, kind):
        try:
            path = navigation_path(self.view.get('active_target') or {}, kind, self.paths)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Refused as exc: self.failure(str(exc))

    def copy_source(self):
        value = (self.view.get('active_target') or {}).get('source_sha')
        if value: QApplication.clipboard().setText(value)

    def open_handoff(self):
        url = self.view.get('web_handoff_url')
        if url: QDesktopServices.openUrl(QUrl(url))

    def history_notice(self, row, _column):
        target = self.view['history'][row]
        self.notice.setText('这是旧测试版本。历史记录只读，请由 A 将其明确登记为 CURRENT 后再启动。'
                           if target['status'] != 'CURRENT' or target != self.view.get('active_target')
                           else '请使用上方“启动当前测试版”按钮，启动前将重新验证。')

    def closeEvent(self, event):
        if self.jobs:
            self.cancel.set()
            self.notice.setText('正在取消校验，请稍后关闭。')
            event.ignore()
        else:
            event.accept()
