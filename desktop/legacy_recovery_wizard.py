"""Chinese, summary-first recovery preview. This dialog cannot execute migration."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QWidget, QLabel, QPushButton, QToolButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget, QScrollArea, QCheckBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit)

from .legacy_recovery_preview import (RecoveryPreview, RecoverySelection, SUMMARY_LABELS,
    SOURCE_LABELS, CONFLICT_LABELS, READINESS_MESSAGES)
from .style import STYLE

STEPS = ('发现旧数据', '数据来源', '学习空间选择', '冲突预览', '安全保证', '迁移准备情况')
GUARANTEES = ('原始数据不会删除', '迁移采用复制/导入', '导入前先创建一致性快照',
              '有冲突先预览', '不覆盖当前账号已有数据')


def label(text, name=''):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(True)
    if name:
        item.setObjectName(name)
    return item


def number(value):
    return '待确认' if value is None else str(value)


class LegacyRecoveryWizard(QDialog):
    selection_changed = Signal(object)
    preview_requested = Signal(object)

    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self.setWindowTitle('旧数据恢复 / 迁移预览')
        self.setObjectName('legacyRecoveryPreviewWizard')
        self.setStyleSheet(STYLE + '\nQPushButton:disabled, #primaryButton:disabled '
                          '{color: #786572; background: #eee7eb; border: 1px solid #d9cbd2;}')
        self.selection = RecoverySelection(preview if isinstance(preview, RecoveryPreview)
                                           else RecoveryPreview.from_mapping(preview))
        self.step = 0
        area = self.screen().availableGeometry()
        self.resize(min(760, area.width() - 40), min(760, area.height() - 60))
        self.setMinimumSize(min(440, area.width() - 40), min(520, area.height() - 60))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        self.heading = label('旧数据恢复预览', 'pageHeading')
        outer.addWidget(self.heading)
        self.step_label = label('')
        outer.addWidget(self.step_label)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.details_toggle = QToolButton()
        self.details_toggle.setText('技术详情')
        self.details_toggle.setCheckable(True)
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.toggled.connect(self._toggle_details)
        outer.addWidget(self.details_toggle)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(180)
        self.details.hide()
        outer.addWidget(self.details)
        navigation = QHBoxLayout()
        self.back = QPushButton('上一步')
        self.back.clicked.connect(lambda: self.show_step(self.step - 1))
        self.next = QPushButton('下一步')
        self.next.setObjectName('primaryButton')
        self.next.clicked.connect(lambda: self.show_step(self.step + 1))
        close = QPushButton('关闭预览')
        close.clicked.connect(self.reject)
        navigation.addWidget(self.back)
        navigation.addStretch()
        navigation.addWidget(close)
        navigation.addWidget(self.next)
        outer.addLayout(navigation)
        self._build_pages()

    @property
    def preview(self):
        return self.selection.preview

    def _page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 8, 12, 8)
        layout.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.stack.addWidget(scroll)
        return layout

    def _build_pages(self):
        while self.stack.count():
            old = self.stack.widget(0)
            self.stack.removeWidget(old)
            old.deleteLater()
        self._summary_page()
        self._source_page()
        self._workspace_page()
        self._conflict_page()
        self._safety_page()
        self._readiness_page()
        self._refresh_workspaces()
        self._refresh_gate()
        self._set_details()
        self.show_step(self.step)

    def _summary_page(self):
        layout = self._page()
        if self.preview.valid and self.preview.data_present:
            headline = '我们找到了以前的 MentorFlow 学习数据。'
            status = '这些数据仍然存在，目前尚未导入当前账号。'
        else:
            headline = '等待旧数据预览'
            status = '暂时无法确认旧数据情况，请获取新的预览。'
        self.discovery_message = label(headline, 'sectionHeading')
        self.presence_message = label(status)
        layout.addWidget(self.discovery_message)
        grid = QGridLayout()
        self.summary_values = {}
        counts = dict(self.preview.summary)
        for i, (key, title) in enumerate(SUMMARY_LABELS.items()):
            card = QWidget()
            card_layout = QVBoxLayout(card)
            card_layout.addWidget(label(title))
            value = label(number(counts.get(key)), 'metricValue')
            self.summary_values[key] = value
            card_layout.addWidget(value)
            grid.addWidget(card, i // 2, i % 2)
        layout.addLayout(grid)
        layout.addWidget(self.presence_message)
        self.loss_message = label('尚未发现数据丢失。' if self.preview.data_loss_confirmed is False
            else '数据完整性还需要进一步核实。')
        layout.addWidget(self.loss_message)
        layout.addWidget(label('当前账号：' + (self.preview.target_label or '待确认')))
        layout.addStretch()

    def _source_page(self):
        layout = self._page()
        layout.addWidget(label('选择要纳入预览的历史来源。原始目录不会被修改。'))
        self.source_boxes = {}
        for source in self.preview.sources:
            box = QCheckBox(SOURCE_LABELS.get(source.kind, '其他历史副本'))
            box.setChecked(source.key in self.selection.sources)
            box.toggled.connect(lambda checked, key=source.key: self._source_changed(key, checked))
            self.source_boxes[source.key] = box
            layout.addWidget(box)
        self.difference_message = label('发现其他历史副本包含不同内容，需要先确认合并方式。')
        self.difference_message.setVisible(any(s.has_differences for s in self.preview.sources))
        layout.addWidget(self.difference_message)
        if not self.preview.sources:
            layout.addWidget(label('数据来源信息仍待确认。'))
        layout.addStretch()

    def _workspace_page(self):
        layout = self._page()
        layout.addWidget(label('请主动选择要预览的学习空间；归属未确认时不会替你默认全选。'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索学习空间名称')
        self.search.setAccessibleName('搜索学习空间名称')
        self.search.setMinimumHeight(40)
        self.search.textChanged.connect(self._refresh_workspaces)
        layout.addWidget(self.search)
        actions = QHBoxLayout()
        self.select_all = QPushButton('全部选择')
        self.select_all.clicked.connect(self._select_visible)
        clear = QPushButton('取消选择')
        clear.clicked.connect(self._clear_workspaces)
        actions.addWidget(self.select_all)
        actions.addWidget(clear)
        layout.addLayout(actions)
        self.workspace_table = QTableWidget(0, 4)
        self.workspace_table.setHorizontalHeaderLabels(['学习空间', '计划数量', '最近活动时间', '资料数量'])
        self.workspace_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.workspace_table.verticalHeader().hide()
        self.workspace_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.workspace_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.workspace_table.setMinimumHeight(210)
        self.workspace_table.itemChanged.connect(self._workspace_changed)
        layout.addWidget(self.workspace_table)
        self.selection_label = label('')
        layout.addWidget(self.selection_label)
        self.refresh_preview = QPushButton('更新所选范围的预览')
        self.refresh_preview.clicked.connect(self._request_preview)
        layout.addWidget(self.refresh_preview)
        self.preview_request_status = label('')
        layout.addWidget(self.preview_request_status)

    def _conflict_page(self):
        layout = self._page()
        layout.addWidget(label('本次预览的对比结果', 'sectionHeading'))
        self.conflict_scope_message = label('')
        layout.addWidget(self.conflict_scope_message)
        counts = dict(self.preview.conflicts)
        self.conflict_values = {}
        for key, title in CONFLICT_LABELS.items():
            value = label(f'{title}：{number(counts.get(key))}')
            self.conflict_values[key] = value
            layout.addWidget(value)
        repairs = counts.get('path_repair')
        self.path_message = label('')
        if repairs:
            suffix = '但系统仍找到了对应内容候选。' if self.preview.all_content_candidates_found else '内容候选仍待确认。'
            self.path_message.setText(f'有 {repairs} 条资料原路径已经失效，{suffix}')
        layout.addWidget(self.path_message)
        if self.preview.line_ending_only:
            layout.addWidget(label('有部分文本只存在换行格式差异。'))
        layout.addStretch()

    def _safety_page(self):
        layout = self._page()
        layout.addWidget(label('迁移必须遵守的安全保证', 'sectionHeading'))
        self.guarantee_labels = [label('✓ ' + text) for text in GUARANTEES]
        for item in self.guarantee_labels:
            layout.addWidget(item)
        layout.addWidget(label('当前仅提供预览；这些保证不是可以取消的选项。'))
        layout.addStretch()

    def _readiness_page(self):
        layout = self._page()
        layout.addWidget(label('开始前，需要确认所有准备条件。'))
        self.readiness_labels = {}
        titles = ('当前账号存储位置稳定', '账号身份地址稳定', '来源与学习空间已确认', '冲突处理方式已确认')
        for key, title in zip(READINESS_MESSAGES, titles):
            item = label(title)
            self.readiness_labels[key] = item, title
            layout.addWidget(item)
        self.blocker_message = label('')
        layout.addWidget(self.blocker_message)
        self.start_button = QPushButton('开始迁移')
        self.start_button.setObjectName('primaryButton')
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self._confirm_preview)
        layout.addWidget(self.start_button)
        self.execution_message = label('当前仅提供迁移预览，尚未执行导入。')
        layout.addWidget(self.execution_message)
        layout.addStretch()

    def show_step(self, step):
        self.step = max(0, min(len(STEPS) - 1, step))
        self.stack.setCurrentIndex(self.step)
        self.step_label.setText(f'{self.step + 1} / {len(STEPS)} · {STEPS[self.step]}')
        self.back.setEnabled(self.step > 0)
        self.next.setVisible(self.step < len(STEPS) - 1)

    def _source_changed(self, key, checked):
        self.selection.select_source(key, checked)
        self._refresh_workspaces()
        self._selection_changed()

    def _refresh_workspaces(self):
        query = self.search.text().strip().casefold()
        visible = [w for w in self.preview.workspaces if w.source_key in self.selection.sources
                   and query in w.name.casefold()]
        self.workspace_table.blockSignals(True)
        self.workspace_table.setRowCount(len(visible))
        for row, workspace in enumerate(visible):
            values = [workspace.name or '未命名学习空间', number(workspace.plans), workspace.last_active or '待确认', number(workspace.materials)]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setData(Qt.ItemDataRole.UserRole, workspace.selection_key)
                    item.setCheckState(Qt.CheckState.Checked if workspace.selection_key in self.selection.workspaces else Qt.CheckState.Unchecked)
                self.workspace_table.setItem(row, column, item)
        self.workspace_table.blockSignals(False)
        self.select_all.setText('选择搜索结果' if query else '全部选择')
        self.select_all.setEnabled(bool(visible))
        self.selection_label.setText(f'已选择 {len(self.selection.workspaces)} 个学习空间')

    def _workspace_changed(self, item):
        if item.column() != 0:
            return
        self.selection.select_workspace(tuple(item.data(Qt.ItemDataRole.UserRole)), item.checkState() == Qt.CheckState.Checked)
        self._selection_changed()

    def _select_visible(self):
        for row in range(self.workspace_table.rowCount()):
            self.selection.select_workspace(tuple(self.workspace_table.item(row, 0).data(Qt.ItemDataRole.UserRole)), True)
        self._refresh_workspaces()
        self._selection_changed()

    def _clear_workspaces(self):
        self.selection.workspaces.clear()
        self.selection.preview_confirmed = False
        self._refresh_workspaces()
        self._selection_changed()

    def _selection_changed(self):
        self.selection_label.setText(f'已选择 {len(self.selection.workspaces)} 个学习空间')
        self.execution_message.setText('当前仅提供迁移预览，尚未执行导入。')
        self._refresh_gate()
        self.selection_changed.emit(self.selection.selection_contract())

    def _request_preview(self):
        self.preview_request_status.setText('已记录预览范围，等待新的预览。')
        self.preview_requested.emit(self.selection.selection_contract())

    def _refresh_gate(self):
        blockers = self.selection.blockers
        self.start_button.setEnabled(not blockers)
        self.blocker_message.setText('\n'.join(blockers) if blockers else '准备条件已完成。当前仍仅提供预览，不会执行导入。')
        flags = dict(self.preview.readiness)
        for key, (item, title) in self.readiness_labels.items():
            ready = flags.get(key) is True and READINESS_MESSAGES[key] not in blockers
            item.setText(('✓ ' if ready else '待完成 · ') + title)
        matches = self.selection.sources == self.preview.confirmed_sources and self.selection.workspaces == self.preview.confirmed_workspaces
        self.conflict_scope_message.setText('' if matches else '选择已变化，请获取对应的新预览后再确认。')
        self.refresh_preview.setEnabled(bool(self.selection.sources) and bool(self.selection.workspaces))

    def _confirm_preview(self):
        if self.selection.confirm_preview():
            self.execution_message.setText('迁移预览已确认；当前未开放执行，尚未导入任何数据。')

    def apply_preview(self, preview):
        if not self.selection.replace_preview(preview if isinstance(preview, RecoveryPreview) else RecoveryPreview.from_mapping(preview)):
            return False
        self.details_toggle.setChecked(False)
        self._build_pages()
        return True

    def _set_details(self):
        p = self.preview
        lines = ['预览版本：' + p.revision, '目标账号标识：' + p.target_key]
        lines += [SOURCE_LABELS.get(s.kind, '其他历史副本') + '：' + s.path for s in p.sources]
        lines += ['学习空间标识：' + w.source_key + ' / ' + w.key for w in p.workspaces]
        titles = {'issuer_hash': '身份地址指纹', 'subject_hash': '账号指纹', 'manifest_hashes': '清单指纹', 'ledger_status': '导入登记状态'}
        lines += [titles[key] + '：' + value for key, value in p.technical]
        self.details.setPlainText('\n'.join(lines))

    def _toggle_details(self, expanded):
        self.details.setVisible(expanded)
        self.details_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
