"""Account-fenced recovery presentation with an optional local file executor."""
from dataclasses import replace
from PySide6.QtCore import Qt, Signal, QTimer
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QProgressBar, QScrollArea, QToolButton, QPlainTextEdit, QFileDialog)

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

    def __init__(self, parent=None, *, management_only=False):
        super().__init__(parent)
        self.management_only = management_only
        self.model = RehydrationState()
        self._resource_lifecycle = None
        self._download_job = None
        self._model_cache_root = None
        self._download_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-recovery")
        self.destroyed.connect(lambda: self._stop_downloads())
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
        self.portable_pack.setVisible(not management_only)
        self.model_cache_button = QPushButton('手动指定已有模型')
        self.model_cache_button.clicked.connect(self._select_model_cache)
        layout.addWidget(self.model_cache_button)
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

    def _stop_downloads(self):
        if self._download_job: self._download_job[1].set()
        self._download_job = None
        self._resource_lifecycle = None
        self._model_cache_root = None
        self._download_pool.shutdown(wait=False, cancel_futures=True)

    def closeEvent(self, event):
        self._stop_downloads()
        super().closeEvent(event)

    def _select_model_cache(self):
        generation = self.model.generation
        selected = QFileDialog.getExistingDirectory(self, '指定已有模型目录（逐文件校验，不修改来源）')
        if selected and generation == self.model.generation and self._resource_lifecycle is not None:
            try:
                path = self._resource_lifecycle.validate_model_cache(selected)
            except (OSError, ValueError):
                self.model_cache_button.setText('模型目录不可用，请重新选择')
                return
            self._model_cache_root = path
            self.model_cache_button.setText('已指定模型目录（安装时校验）')

    def authenticated(self, account_key):
        if self._download_job: self._download_job[1].set()
        self._model_cache_root = None
        if self._resource_lifecycle is not None and self._resource_lifecycle.account_key != account_key:
            self._resource_lifecycle = None
        generation = self.model.authenticated(account_key)
        self.details_toggle.setChecked(False)
        self.render()
        return generation

    def bind_resource_recovery(self, lifecycle, *, model_cache_root=None):
        """Composition root supplies the current mounted account lifecycle."""
        if lifecycle.account_key != self.model.account_key or not lifecycle.account_key:
            raise ValueError('RECOVERY_ACCOUNT_MISMATCH')
        self._resource_lifecycle = lifecycle
        self._model_cache_root = model_cache_root
        self.render()

    def _package_enabled(self, resource):
        return bool(resource.kind == 'file' and resource.package_supported and resource.fingerprint_sha256
                    and self._resource_lifecycle is not None
                    and self._resource_lifecycle.account_key == self.model.account_key)

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
        if self.management_only:
            self.close()
            return
        if self.model.workspace_allowed:
            self.workspace_requested.emit(self.model.snapshot)

    def _toggle_details(self, expanded):
        self.details.setVisible(expanded)
        self.details_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _poll_model_download(self):
        job = self._download_job
        if job is None: return
        future, cancel, generation, key, operation = job
        if not future.done():
            QTimer.singleShot(100, self._poll_model_download)
            return
        self._download_job = None
        if generation != self.model.generation or self.model.snapshot is None: return
        try:
            future.result()
        except Exception:
            if key in self.resource_status:
                self.resource_status[key].setText('操作已取消，可重试。' if cancel.is_set() else '恢复或校验失败，请重试。')
            return
        self.model.snapshot = replace(self.model.snapshot, resources=tuple(
            replace(item, missing=False) if item.key == key else item for item in self.model.snapshot.resources))
        self.render()

    def _resource_action(self, resource, action):
        if self.model.snapshot is None or resource not in self.model.snapshot.resources:
            return
        if action == 'redownload' and resource.kind == 'model' and resource.verified_redownload:
            if self._download_job:
                self._download_job[1].set()
                self.resource_status[resource.key].setText('正在取消下载…')
                return
            lifecycle = self._resource_lifecycle
            if lifecycle is None or lifecycle.account_key != self.model.account_key:
                return
            cancel = Event()
            future = self._download_pool.submit(lifecycle.install_public_model, resource.key,
                fingerprint_sha256=resource.fingerprint_sha256, cancel=cancel, cache_root=self._model_cache_root)
            self._download_job = (future, cancel, self.model.generation, resource.key, 'model')
            self.resource_status[resource.key].setText('正在下载并校验模型；再次点击可取消。')
            QTimer.singleShot(100, self._poll_model_download)
            return
        if action == 'package' and self._package_enabled(resource):
            if self._download_job:
                self._download_job[1].set()
                self.resource_status[resource.key].setText('正在取消资源操作…')
                return
            generation, account = self.model.generation, self.model.account_key
            lifecycle = self._resource_lifecycle
            selected, _ = QFileDialog.getOpenFileName(self, '选择当前账号的原资料包', '', 'MentorFlow 资料包 (*.mentorflowpack)')
            # The modal picker runs a nested event loop: logout/account switch
            # can occur while it is open. Never apply its result to a new mount.
            if (not selected or generation != self.model.generation or account != self.model.account_key
                    or lifecycle is not self._resource_lifecycle or not self._package_enabled(resource)):
                return
            if self._download_job:
                self.resource_status[resource.key].setText('请先等待或取消正在进行的资源操作。')
                return
            cancel = Event()
            future = self._download_pool.submit(lifecycle.restore_resource_from_package,
                resource.key, selected, fingerprint_sha256=resource.fingerprint_sha256, cancel=cancel)
            self._download_job = (future, cancel, generation, resource.key, 'package')
            self.resource_status[resource.key].setText('正在校验并恢复原件；再次点击可取消。')
            QTimer.singleShot(100, self._poll_model_download)
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
        self.model_cache_button.setEnabled(self._resource_lifecycle is not None)
        self.title.setText(model.headline)
        self.progress.setVisible(model.state in (SyncState.AUTH_SUCCESS, SyncState.SYNCING))
        self.retry.setVisible(model.state in (SyncState.SYNC_OFFLINE, SyncState.SYNC_FAILED) or
                              snapshot is not None and snapshot.empty_state == 'NOT_INITIALIZED')
        self.enter.setEnabled(self.management_only or model.workspace_allowed)
        self.cache_notice.setText('')
        self.summary.setText('')
        if snapshot is not None:
            if model.state != SyncState.SYNC_COMPLETE:
                self.cache_notice.setText('正在显示可信本地缓存；上次同步时间：' + snapshot.last_sync)
                self.enter.setText('查看缓存的学习空间')
            else:
                self.enter.setText('进入学习空间')
            self.summary.setText(f'计划 {len(snapshot.plans)} · 进度 {snapshot.progress_count} · '
                                 f'复习 {snapshot.review_count if snapshot.review_count is not None else "暂不支持"} · '
                                 f'历史 {snapshot.history_count if snapshot.history_count is not None else "暂不支持"}')
        if self.management_only:
            self.enter.setText('关闭')
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
        if len(resource.plan_keys) > 1:
            rows.addWidget(caption('用于多个学习计划：' + '、'.join(
                p.name for p in self.model.snapshot.plans if p.key in resource.plan_keys)))
        elif resource.plan_keys:
            rows.addWidget(caption('用于学习计划：' + next(
                p.name for p in self.model.snapshot.plans if p.key == resource.plan_keys[0])))
        message = ('模型尚未安装在这台设备。' if resource.kind == 'model' else
                   '这份资料还没有下载到这台设备。') if resource.missing else '本机可用'
        if resource.kind in ('other', 'unknown') and resource.missing:
            message = '这项资源尚未在本机验证，资源类型以云端记录为准。'
        rows.addWidget(caption(message))
        if not resource.missing:
            return
        actions = QHBoxLayout()
        buttons = {}
        if resource.verified_redownload:
            download = QPushButton('安装 / 下载模型' if resource.kind == 'model' else '重新下载 / 取消')
            download.setEnabled(self._resource_lifecycle is not None and self._resource_lifecycle.account_key == self.model.account_key)
            download.clicked.connect(lambda: self._resource_action(resource, 'redownload'))
            actions.addWidget(download)
            buttons['redownload'] = download
        # Files always expose the future pack entry. Models expose it only when
        # their metadata explicitly supports package restoration.
        if resource.kind == 'file' or resource.package_supported:
            package = QPushButton('从迁移包恢复')
            package.setEnabled(self._package_enabled(resource))
            package.setToolTip('只恢复与当前账号及云端 SHA-256 一致的原文件，不覆盖学习状态。')
            package.clicked.connect(lambda: self._resource_action(resource, 'package'))
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
