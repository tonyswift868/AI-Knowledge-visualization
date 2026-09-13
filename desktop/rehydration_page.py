"""Native UI shell for future canonical sync projections, without executors."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QProgressBar, QScrollArea, QToolButton, QPlainTextEdit)

from .rehydration_state import RehydrationState, SyncState
from .style import STYLE


def caption(value):
    widget = QLabel(value)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    return widget


class PortablePackEntry(QWidget):
    """V2 has no implementation in this delivery. Never delegates to V1."""
    V2_EXECUTOR_AVAILABLE = False
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.button = QPushButton('导入 MentorFlow 迁移包')
        self.button.setObjectName('portablePackV2Entry')
        self.button.setEnabled(False)
        self.button.setToolTip('迁移包导入功能尚未准备完成，暂时不能使用。')
        layout.addWidget(self.button)
        layout.addWidget(caption('迁移包导入功能准备中；当前不会执行导入。'))

    def set_readiness(self, *, capability=False, ready=False):
        self.button.setEnabled(capability is True and ready is True and self.V2_EXECUTOR_AVAILABLE)


class RehydrationPage(QWidget):
    sync_requested = Signal(object)
    workspace_requested = Signal(object)
    resource_action_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = RehydrationState()
        self.setStyleSheet(STYLE + '\nQPushButton:disabled {color:#786572; background:#eee7eb;}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        self.title = caption('')
        self.title.setObjectName('pageHeading')
        layout.addWidget(self.title)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setAccessibleName('正在恢复学习空间')
        layout.addWidget(self.progress)
        self.cache_notice = caption('')
        self.summary = caption('')
        layout.addWidget(self.cache_notice)
        layout.addWidget(self.summary)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)
        self.portable_pack = PortablePackEntry()
        layout.addWidget(self.portable_pack)
        self.details_toggle = QToolButton()
        self.details_toggle.setText('技术详情')
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.details_toggle)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(140)
        layout.addWidget(self.details)
        self.details_toggle.toggled.connect(self._toggle_details)
        actions = QHBoxLayout()
        self.retry = QPushButton('重试')
        self.retry.clicked.connect(self.start_sync)
        self.enter = QPushButton('进入学习空间')
        self.enter.clicked.connect(self._enter)
        actions.addWidget(self.retry)
        actions.addStretch()
        actions.addWidget(self.enter)
        layout.addLayout(actions)
        self.render()

    def authenticated(self, account_key):
        generation = self.model.authenticated(account_key)
        self.details_toggle.setChecked(False)
        self.render()
        return generation

    def start_sync(self):
        generation = self.model.syncing()
        self.render()
        if generation is not None:
            self.sync_requested.emit({'account_key': self.model.account_key, 'generation': generation})
        return generation

    def receive(self, generation, state, payload=None):
        accepted = self.model.receive(generation, state, payload)
        if accepted:
            self.render()
        return accepted

    def _enter(self):
        if self.model.workspace_allowed:
            self.workspace_requested.emit(self.model.snapshot)

    def _toggle_details(self, expanded):
        self.details.setVisible(expanded)
        self.details_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _resource_action(self, resource, action):
        if self.model.snapshot is None or resource not in self.model.snapshot.resources:
            return
        if action == 'later':
            self.model.deferred.add(resource.key)
            noun = '模型' if resource.kind == 'model' else '资料'
            self.resource_status[resource.key].setText(f'已选择稍后处理；{noun}仍保留在原位置。')
        elif action == 'redownload' and resource.verified_redownload:
            self.resource_status[resource.key].setText('已选择重新下载，等待下载功能处理；当前尚未下载。')
            self.resource_action_requested.emit({'account_key': self.model.account_key,
                'generation': self.model.generation, 'resource_id': resource.key, 'action': action})

    def render(self):
        model, snapshot = self.model, self.model.snapshot
        self.title.setText(model.headline)
        self.progress.setVisible(model.state in (SyncState.AUTH_SUCCESS, SyncState.SYNCING))
        self.retry.setVisible(model.state in (SyncState.SYNC_OFFLINE, SyncState.SYNC_FAILED))
        self.enter.setEnabled(model.workspace_allowed)
        self.cache_notice.setText('')
        self.summary.setText('')
        if snapshot is not None:
            if model.state != SyncState.SYNC_COMPLETE:
                self.cache_notice.setText('正在显示可信本地缓存；上次同步时间：' + snapshot.last_sync)
                self.enter.setText('查看缓存的学习空间')
            else:
                self.enter.setText('进入学习空间')
            self.summary.setText(f'计划 {len(snapshot.plans)} · 进度 {snapshot.progress_count} · '
                                 f'复习 {snapshot.review_count} · 历史 {snapshot.history_count}')
        old = self.scroll.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        body = QWidget()
        rows = QVBoxLayout(body)
        self.plan_labels, self.progress_labels, self.resource_status, self.resource_buttons = {}, {}, {}, {}
        if snapshot is not None:
            for plan in snapshot.plans:
                name, progress = caption(plan.name), caption('学习进度：' + plan.progress)
                name.setObjectName('sectionHeading')
                self.plan_labels[plan.key], self.progress_labels[plan.key] = name, progress
                rows.addWidget(name)
                rows.addWidget(progress)
                for resource in snapshot.resources:
                    if resource.plan_key == plan.key:
                        self._resource(rows, resource)
            unbound = [r for r in snapshot.resources if not r.plan_key]
            if unbound:
                rows.addWidget(caption('我的资料与模型'))
                for resource in unbound:
                    self._resource(rows, resource)
        rows.addStretch()
        self.scroll.setWidget(body)
        lines = [] if snapshot is None else ['同步修订：' + snapshot.revision] + [
            '资源标识：' + r.key + (' · MISSING_ON_THIS_DEVICE' if r.missing else ' · AVAILABLE')
            for r in snapshot.resources]
        self.details.setPlainText('\n'.join(lines))
        self.details.setVisible(self.details_toggle.isChecked())

    def _resource(self, rows, resource):
        rows.addWidget(caption(resource.name))
        message = ('模型尚未安装在这台设备。' if resource.kind == 'model' else
                   '这份资料还没有下载到这台设备。') if resource.missing else '本机可用'
        rows.addWidget(caption(message))
        if not resource.missing:
            return
        actions = QHBoxLayout()
        buttons = {}
        if resource.verified_redownload:
            download = QPushButton('重新下载')
            download.clicked.connect(lambda: self._resource_action(resource, 'redownload'))
            actions.addWidget(download)
            buttons['redownload'] = download
        # Files always expose the future pack entry. Models expose it only when
        # their metadata explicitly supports package restoration.
        if resource.kind == 'file' or resource.package_supported:
            package = QPushButton('从迁移包恢复')
            package.setEnabled(False)
            package.setToolTip('迁移包恢复尚未准备完成，当前不能执行。')
            actions.addWidget(package)
            buttons['package'] = package
        later = QPushButton('稍后处理')
        later.clicked.connect(lambda: self._resource_action(resource, 'later'))
        actions.addWidget(later)
        buttons['later'] = later
        rows.addLayout(actions)
        status = caption('')
        if not resource.verified_redownload:
            status.setText('需要从旧设备迁移包恢复。' if resource.kind == 'model' else '可稍后从旧设备迁移包恢复。')
        rows.addWidget(status)
        self.resource_status[resource.key] = status
        self.resource_buttons[resource.key] = buttons
