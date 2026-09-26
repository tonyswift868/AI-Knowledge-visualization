"""Current plan and associated originals, extracted from PlansPage presentation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QHeaderView, QInputDialog, QListWidget, QListWidgetItem, QTabWidget, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QTableWidget,
    QTreeWidget, QTreeWidgetItem,
    QTableWidgetItem, QVBoxLayout, QWidget)

from .plan_models import PlanError, valid_knowledge_ref
from .plan_resources import PlanResourceResolver
from .file_io import open_resource_source
from .store import MaterialError, MaterialStore
from .text_import import MAX_FILE_BYTES, parse_text
from .plan_export import ExportCancellation, ExportResult, ExportStatus, export_current_plan
from .presentation import human_status


PLANS_PAGE_STYLE = """
QListWidget#planCatalog { border: 0; background: transparent; outline: 0; }
QListWidget#planCatalog::item { padding: 16px; border-bottom: 1px solid #ead7df; }
QListWidget#planCatalog::item:hover { background: #fff0f6; }
QListWidget#planCatalog::item:selected { background: #f8dce8; color: #3b2b3f; }
"""


class PlansPage(QWidget):
    state_changed = Signal()
    # Shell may connect this to PlanCompletionPage.open_plan without making
    # the two pages navigate each other directly.
    open_completion = Signal(str)

    @staticmethod
    def _display_notices(notices):
        """Translate compatibility notices into the account-sync vocabulary.

        The service still keeps its legacy notice codes for old callers and
        migration tests.  The product page should explain the current flow:
        account sync restores the cloud projection automatically, while this
        page only refreshes the already-synced local list.
        """
        replacements = (
            ("账号历史快照尚未读取；等待账号同步。当前列表只显示本账号已同步或本机新建的计划。",
             "账号同步尚未完成；本机计划列表会在同步完成后自动更新。"),
            ("账号历史快照暂不可读（尚未自动读取）；等待账号同步。当前列表只显示本账号已同步或本机新建的计划。",
             "账号历史暂未同步；本机计划仍可使用，账号同步会自动重试。"),
            ("账号历史快照读取失败，未确认云端计划数量；已保存的本机计划仍保留。",
             "账号历史同步失败，暂未确认云端计划数量；本机计划仍保留。"),
            ("旧计划尚未读取；这里的本机计划列表不代表旧账号没有计划。",
             "账号同步尚未完成；本机计划列表会在同步完成后自动更新。"),
            ("历史记录暂不可读；本机新计划仍可使用，可重试云端同步。",
             "账号历史暂未同步；本机计划仍可使用，账号同步会自动重试。"),
            ("旧计划读取失败，未确认旧计划数量；已保存的本机计划仍保留。",
             "账号历史同步失败，暂未确认云端计划数量；本机计划仍保留。"),
        )
        result = []
        for notice in notices or ():
            text = str(notice)
            for old, new in replacements:
                text = text.replace(old, new)
            # Future compatibility messages should not reintroduce a
            # user-facing migration action that no longer exists.
            text = text.replace("读取旧计划", "等待账号同步")
            result.append(text)
        return tuple(result)

    def __init__(self, account_id, data_root, parent=None):
        super().__init__(parent)
        self.setObjectName("plansPage")
        self.setStyleSheet(PLANS_PAGE_STYLE)
        self.account_id, self.data_root = account_id, Path(data_root)
        self.source = self.resolver = self.catalog = None
        self.plan_service = None
        self.feature_context = None
        self._selected_key = ""
        self._refresh_pending = False
        self.legacy_data_root = None
        self.originals = ()
        self._server_map_item = None
        self._knowledge_map_job_id = ""
        self._knowledge_map_revision = ""
        self._knowledge_map_job = None
        self._disposed, self._future, self._completed = False, None, None
        self._export_callback = None
        self._export_cancelled = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mf-plans")
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        header = QHBoxLayout()
        heading = QLabel("我的计划")
        heading.setObjectName("pageHeading")
        header.addWidget(heading, 1)
        self.refresh_button = QPushButton("刷新账号快照")
        self.refresh_button.setToolTip("读取当前账号已确认的计划快照；不会读取旧本机数据库，也不会替代顶栏的立即同步。")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        self.rename_button = QPushButton("重命名计划")
        self.rename_button.clicked.connect(self.rename_current_plan)
        header.addWidget(self.rename_button)
        self.delete_button = QPushButton("归档计划（同步到账号）")
        self.delete_button.clicked.connect(self.delete_current_plan)
        header.addWidget(self.delete_button)
        layout.addLayout(header)
        self.status = QLabel("正在准备计划列表…")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("计划操作状态")
        layout.addWidget(self.status)
        # Compatibility adapter for account-shell callbacks and older tests;
        # the visible plan list below is the product selector.
        self.selector = QComboBox(self)
        self.selector.setObjectName("planSelector")
        self.selector.currentIndexChanged.connect(self._select)
        self.selector.hide()

        self.views = QStackedWidget()
        layout.addWidget(self.views, 1)
        self.plan_list = QListWidget()
        self.plan_list.setObjectName("planCatalog")
        self.plan_list.setAccessibleName("全部学习计划，点击查看详情")
        self.plan_list.setWordWrap(True)
        self.plan_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.plan_list.itemClicked.connect(self._open_plan_item)
        self.plan_list.itemActivated.connect(self._open_plan_item)
        self.views.addWidget(self.plan_list)

        self.detail_page = QWidget()
        detail_layout = QVBoxLayout(self.detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.back_button = QPushButton("返回计划列表")
        self.back_button.clicked.connect(self.show_catalog)
        bar.addWidget(self.back_button)
        self.plan_title = QLabel()
        self.plan_title.setWordWrap(True)
        self.plan_title.setTextFormat(Qt.TextFormat.PlainText)
        self.plan_title.setObjectName("sectionHeading")
        bar.addWidget(self.plan_title, 1)
        self.history_button = QPushButton("学习记录")
        self.history_button.clicked.connect(self.show_history)
        bar.addWidget(self.history_button)
        self.review_button = QPushButton("进入复习")
        self.review_button.setAccessibleName("进入当前计划复习")
        self.review_button.clicked.connect(self.open_review)
        bar.addWidget(self.review_button)
        self.mission_button = QPushButton("编译今日任务")
        self.mission_button.setAccessibleName("编译当前计划今日任务")
        self.mission_button.clicked.connect(self.compile_daily_mission)
        bar.addWidget(self.mission_button)
        self.start_mission_button = QPushButton("开始并固定今日任务")
        self.start_mission_button.setAccessibleName("开始并固定当前计划今日任务")
        self.start_mission_button.clicked.connect(self.start_daily_mission)
        bar.addWidget(self.start_mission_button)
        self.map_button = QPushButton("知识地图")
        self.map_button.setAccessibleName("查看当前计划知识地图")
        self.map_button.clicked.connect(self.show_knowledge_map)
        bar.addWidget(self.map_button)
        self.completion_button = QPushButton("计划完成情况")
        self.completion_button.setAccessibleName("在计划完成情况中查看当前计划")
        self.completion_button.clicked.connect(self._open_completion)
        bar.addWidget(self.completion_button)
        detail_layout.addLayout(bar)
        self.detail_tabs = QTabWidget()
        detail_layout.addWidget(self.detail_tabs, 1)

        task_split = QSplitter(Qt.Orientation.Vertical)
        self.tasks = QTableWidget(0, 5)
        self.tasks.setAccessibleName("每日学习任务")
        self.tasks.setHorizontalHeaderLabels(["日期", "任务", "阶段", "预计分钟", "状态"])
        self.tasks.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tasks.verticalHeader().hide()
        self.tasks.verticalHeader().setDefaultSectionSize(38)
        self.tasks.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tasks.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tasks.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.tasks.itemSelectionChanged.connect(self._task_detail)
        task_split.addWidget(self.tasks)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName("任务详情")
        self.detail.setPlaceholderText("选择任务查看目标和完成条件。")
        task_split.addWidget(self.detail)
        task_split.setSizes([420, 140])
        task_page = QWidget()
        task_layout = QVBoxLayout(task_page)
        task_layout.setContentsMargins(0, 0, 0, 0)
        self.mission_status = QLabel("今日任务按当前计划进度动态编译；点击“编译今日任务”查看。")
        self.mission_status.setObjectName("missionStatus")
        self.mission_status.setWordWrap(True)
        task_layout.addWidget(self.mission_status)
        task_layout.addWidget(task_split, 1)
        self.detail_tabs.addTab(task_page, "任务安排")

        resource_page = QWidget()
        resource_layout = QVBoxLayout(resource_page)
        resource_layout.setContentsMargins(0, 0, 0, 0)
        self.resources = QTableWidget(0, 2)
        self.resources.setAccessibleName("计划关联资料")
        self.resources.setHorizontalHeaderLabels(["资料", "原件状态"])
        for column in (0, 1):
            self.resources.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.resources.verticalHeader().hide()
        self.resources.verticalHeader().setDefaultSectionSize(38)
        self.resources.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.resources.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.resources.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.resources.itemDoubleClicked.connect(lambda _item: self.preview_original())
        resource_layout.addWidget(self.resources, 1)
        actions = QHBoxLayout()
        self.relink_button = QPushButton("重新关联原件")
        self.relink_button.clicked.connect(self.choose_original)
        actions.addWidget(self.relink_button)
        self.preview_button = QPushButton("查看正文")
        self.preview_button.clicked.connect(self.preview_original)
        actions.addWidget(self.preview_button)
        actions.addStretch()
        resource_layout.addLayout(actions)
        self.resource_preview = QPlainTextEdit()
        self.resource_preview.setReadOnly(True)
        self.resource_preview.setPlaceholderText("选择资料后查看正文。")
        resource_layout.addWidget(self.resource_preview, 1)
        self.detail_tabs.addTab(resource_page, "关联资料")

        map_page = QWidget()
        map_layout = QVBoxLayout(map_page)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_hint = QLabel("按计划快照中已有的知识引用展示任务关系；不会根据相同标题猜测知识点。")
        map_hint.setWordWrap(True)
        map_hint.setObjectName("planMapHint")
        map_layout.addWidget(map_hint)
        map_actions = QHBoxLayout()
        self.generate_map_button = QPushButton("根据计划资料生成")
        self.generate_map_button.setAccessibleName("生成当前计划知识地图")
        self.generate_map_button.clicked.connect(self.generate_knowledge_map)
        map_actions.addWidget(self.generate_map_button)
        self.poll_map_button = QPushButton("刷新生成进度")
        self.poll_map_button.setAccessibleName("刷新知识地图生成进度")
        self.poll_map_button.clicked.connect(self.poll_knowledge_map)
        map_actions.addWidget(self.poll_map_button)
        self.activate_map_button = QPushButton("激活当前修订")
        self.activate_map_button.setAccessibleName("激活当前知识地图修订")
        self.activate_map_button.clicked.connect(self.activate_knowledge_map)
        map_actions.addWidget(self.activate_map_button)
        map_actions.addStretch()
        map_layout.addLayout(map_actions)
        self.map_status = QLabel("选择计划后读取知识地图引用。")
        self.map_status.setWordWrap(True)
        self.map_status.setObjectName("planMapStatus")
        map_layout.addWidget(self.map_status)
        self.knowledge_tree = QTreeWidget()
        self.knowledge_tree.setObjectName("knowledgeMapTree")
        self.knowledge_tree.setAccessibleName("当前计划知识地图")
        self.knowledge_tree.setHeaderLabels(["知识点", "任务阶段", "任务数", "来源与前置"])
        self.knowledge_tree.setRootIsDecorated(True)
        self.knowledge_tree.setAlternatingRowColors(True)
        self.knowledge_tree.header().setStretchLastSection(True)
        self.knowledge_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        map_layout.addWidget(self.knowledge_tree, 1)
        self.detail_tabs.addTab(map_page, "知识地图")
        self.views.addWidget(self.detail_page)

        export_actions = QHBoxLayout()
        self.export_button = QPushButton("导出当前计划及关联资料")
        self.export_button.setObjectName("primaryButton")
        self.export_button.clicked.connect(lambda: self.request_export(lambda _result: None))
        export_actions.addWidget(self.export_button)
        self.cancel_export_button = QPushButton("取消导出")
        self.cancel_export_button.clicked.connect(self.cancel_export)
        self.cancel_export_button.hide()
        export_actions.addWidget(self.cancel_export_button)
        export_actions.addStretch()
        detail_layout.addLayout(export_actions)
        self._controls()

    @property
    def busy(self):
        return self._future is not None

    @property
    def current_plan(self):
        if self.catalog is None:
            return None
        key = self._selected_key or self.selector.currentData()
        return next((p for p in self.catalog.plans if p.key == key), None)

    def bind_source(self, source, *, legacy_data_root=None):
        if self._disposed or self.busy or self.source is not None:
            raise ValueError("Bind one authenticated plan source per window")
        if source.account_id != self.account_id:
            raise ValueError("Plan source account mismatch")
        self.source, self.legacy_data_root = source, legacy_data_root
        self.status.setText("刷新账号快照只读取当前账号已确认的计划；立即同步请在账号同步入口执行。")
        self._controls()

    def bind_context(self, context):
        """Give plan-bound actions the same account/model context as the shell."""
        if self._disposed:
            return
        if context is not None and not isinstance(context, dict):
            raise ValueError("plan context must be a dictionary")
        self.feature_context = context

    def bind_service(self, plan_service):
        if self._disposed or self.busy or self.source is not None:
            raise ValueError("Bind one plan service per window")
        if plan_service.account_id != self.account_id:
            raise ValueError("Plan service account mismatch")
        self.plan_service = self.source = plan_service
        self.plan_service.changed.connect(self._service_changed)
        self.refresh_button.setText("刷新账号快照")
        self.status.setText("新建计划的修改先保存在本机并由账号同步到其他设备；服务器历史计划当前只读。")
        self.refresh()

    def _service_changed(self):
        if self._disposed:
            return
        if self.busy:
            self._refresh_pending = True
        else:
            self.refresh()

    def _controls(self):
        usable = not self._disposed and not self.busy
        self.refresh_button.setEnabled(usable and self.source is not None)
        local = self.current_plan is not None and self.plan_service is not None and self.current_plan.source == "本机计划"
        self.rename_button.setEnabled(usable and local)
        self.delete_button.setEnabled(usable and local)
        if self.current_plan is not None and not local:
            self.rename_button.setToolTip("当前计划来自服务器历史快照，当前版本只读；需要服务端 revision mutation 后才能编辑。")
            self.delete_button.setToolTip("当前计划来自服务器历史快照，当前版本只读；不会误删服务器历史记录。")
        else:
            self.rename_button.setToolTip("重命名本机新建计划，名称会随账号同步。")
            self.delete_button.setToolTip("归档本机新建计划；学习进度、资料关联和历史记录会保留。")
        self.relink_button.setEnabled(usable and self.current_plan is not None and bool(self.originals))
        self.preview_button.setEnabled(usable and self.current_plan is not None and bool(self.originals))
        self.export_button.setEnabled(usable and self.source is not None and self.current_plan is not None)
        self.history_button.setEnabled(usable and self.current_plan is not None and self.plan_service is not None)
        self.review_button.setEnabled(usable and self.current_plan is not None)
        self.mission_button.setEnabled(usable and self.current_plan is not None and callable(getattr(self.plan_service, "daily_mission", None)))
        self.start_mission_button.setEnabled(usable and self.current_plan is not None and callable(getattr(self.plan_service, "start_daily_mission", None)))
        self.map_button.setEnabled(usable and self.current_plan is not None)
        self.completion_button.setEnabled(usable and self.current_plan is not None)
        map_ready = usable and self.current_plan is not None and self.plan_service is not None
        self.generate_map_button.setEnabled(map_ready)
        self.poll_map_button.setEnabled(map_ready and bool(self._knowledge_map_job_id))
        self.activate_map_button.setEnabled(map_ready and bool(self._knowledge_map_revision))
        self.plan_list.setEnabled(usable and bool(self.catalog and self.catalog.plans))
        self.state_changed.emit()

    def show_history(self):
        if self.current_plan is None or self.plan_service is None:
            return
        self._start(lambda: self.plan_service.historical_records(self.current_plan.key),
                    self._show_history, "正在读取这份计划的历史学习记录…")

    def compile_daily_mission(self):
        """Compile today's bounded mission from the saved plan and progress."""
        plan, service = self.current_plan, self.plan_service
        compiler = getattr(service, "daily_mission", None) if service is not None else None
        if plan is None or not callable(compiler):
            self.mission_status.setText("当前计划尚未绑定动态排程服务。")
            return False
        plan_key = plan.key

        def compile_mission():
            return compiler(plan_key)

        def show_mission(mission):
            if self.current_plan is None or self.current_plan.key != plan_key:
                return
            if not isinstance(mission, dict):
                self.mission_status.setText("今日任务编译没有返回可读取结果。")
                return
            status = str(mission.get("status") or "unknown")
            used = mission.get("used_minutes", 0)
            capacity = mission.get("available_minutes", 0)
            remaining = mission.get("remaining_minutes", 0)
            objectives = mission.get("objectives") if isinstance(mission.get("objectives"), list) else []
            titles = [str(item.get("title") or "未命名任务") for item in objectives[:3] if isinstance(item, dict)]
            reflowed = mission.get("reflowed_task_ids") if isinstance(mission.get("reflowed_task_ids"), list) else []
            parts = [f"今日状态：{status} · 已安排 {used}/{capacity} 分钟 · 尚余 {remaining} 分钟"]
            if titles:
                parts.append("今日目标：" + "、".join(titles))
            if reflowed:
                parts.append(f"已将 {len(reflowed)} 项逾期未完成任务前移到今天。")
            if mission.get("notice"):
                parts.append(str(mission["notice"]))
            self.mission_status.setText("\n".join(parts))

        return self._start(compile_mission, show_mission, "正在按当前进度编译今日任务…")

    def start_daily_mission(self):
        """Freeze the current derived mission as an account-sync snapshot."""
        plan, service = self.current_plan, self.plan_service
        starter = getattr(service, "start_daily_mission", None) if service is not None else None
        if plan is None or not callable(starter):
            self.mission_status.setText("当前计划尚未绑定每日任务快照服务。")
            return False
        plan_key = plan.key

        def start():
            return starter(plan_key)

        def show_started(snapshot):
            if self.current_plan is None or self.current_plan.key != plan_key:
                return
            self.mission_status.setText(
                f"今日任务已固定（计划版本 {snapshot.get('plan_revision', 1)}），"
                f"已安排 {snapshot.get('used_minutes', 0)} 分钟；后台排程不会偷偷改写这份快照。"
            )

        return self._start(start, show_started, "正在保存今日任务快照…")

    def _show_history(self, records):
        dialog = QDialog(self)
        dialog.setWindowTitle("历史学习记录")
        dialog.resize(800, 620)
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        titles = {"messages": "课堂对话", "knowledge": "知识点", "progress": "掌握进度", "evidence": "评估记录", "review": "复习记录"}
        names = {str(r.get("id")): r.get("name", "知识点") for r in records.get("knowledge", [])}
        labels = {"explanation": "讲解", "acceptance_method": "检验方法", "mastery": "掌握程度",
            "completed_task_count": "完成任务", "completed_task_minutes": "完成分钟",
            "actual_elapsed_minutes": "实际用时", "evidence": "评估内容", "score": "分数",
            "due_date": "到期日期", "scheduled_date": "安排日期", "attempt_count": "尝试次数",
            "last_score": "最近分数", "created_at": "记录时间", "updated_at": "更新时间"}
        def entry(row, number):
            heading = row.get("name") or names.get(str(row.get("knowledge_point_id"))) or f"记录 {number}"
            lines = [str(heading)]
            for field, caption in labels.items():
                if row.get(field) not in (None, ""):
                    lines.append(f"{caption}：{row[field]}")
            if type(row.get("passed")) is bool:
                lines.append("评估结果：" + ("通过" if row["passed"] else "尚未通过"))
            if row.get("status"):
                lines.append("状态：" + human_status(row["status"]))
            return "\n".join(lines)
        for key, title in titles.items():
            rows = records.get(key, [])
            view = QPlainTextEdit()
            view.setReadOnly(True)
            if key == "messages":
                content = "\n\n".join(f"{r.get('created_at', '')}  {'老师' if r.get('role') == 'assistant' else '你' if r.get('role') == 'user' else '记录'}\n{r.get('content', '')}" for r in rows)
            else:
                content = "\n\n".join(entry(row, number) for number, row in enumerate(rows, 1))
            view.setPlainText(content or "这份计划没有此类历史记录。")
            tabs.addTab(view, f"{title}（{len(rows)}）")
        layout.addWidget(tabs)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def open_review(self):
        """Start plan-bound review without placing review in More Tools."""
        plan = self.current_plan
        context = getattr(self, "feature_context", None)
        if plan is None:
            return
        if not isinstance(context, dict) or context.get("model_client") is None:
            self.status.setText("当前模型连接不可用；请先在“模型连接”中配置模型。")
            return
        from .tools.jobs import PageJobs
        from .tools.page import ActivityPane
        from .tools.service import ToolsService

        dialog = QDialog(self)
        dialog.setWindowTitle("复习 · " + plan.title)
        dialog.resize(980, 680)
        outer = QVBoxLayout(dialog)
        status = QLabel("正在读取当前计划的复习记录…")
        status.setWordWrap(True)
        outer.addWidget(status)
        host = QWidget(dialog)
        host.service = ToolsService(context)
        host.jobs = PageJobs(host)
        host.run = lambda operation, callback: host.jobs.run(operation, callback)
        pane = ActivityPane(host, "review")
        pane.page = host
        host.pane = pane
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.addWidget(pane)
        outer.addWidget(host, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)

        def loaded(library):
            pane.set_catalog([plan])
            if pane.binding.count() > 1:
                pane.binding.setCurrentIndex(1)
            pane.set_rows(library.get("review", ()))
            status.setText("这份复习活动会保存到当前计划和当前账号。")

        host.jobs.failed.connect(status.setText)
        host.jobs.run(host.service.library, loaded)
        dialog.exec()
        host.jobs.dispose()

    def show_knowledge_map(self):
        plan = self.current_plan
        if plan is None:
            return
        self.detail_tabs.setCurrentIndex(self.detail_tabs.indexOf(self.knowledge_tree.parentWidget()))
        self._remove_server_map_item()
        service = self.plan_service
        reader = getattr(service, "server_knowledge_map", None) if service is not None else None
        if not callable(reader):
            self.map_status.setText("云端知识地图服务不可用；当前继续显示计划快照中已有的知识引用。")
            return
        plan_key = plan.key
        self.map_status.setText("正在读取当前账号工作区的服务端知识地图；计划快照中的任务引用会单独保留。")

        def read_map():
            try:
                return True, reader(), ""
            except PlanError as exc:
                return False, None, str(exc)
            except Exception:
                return False, None, "服务端知识地图读取失败，计划快照仍可查看。"

        def display(result):
            if self.current_plan is None or self.current_plan.key != plan_key:
                return
            ok, graph, message = result
            if not ok:
                self.map_status.setText("服务端知识地图暂不可用；以下仍为计划快照中的已有知识引用。" + ("\n" + message if message else ""))
                self.status.setText("服务端知识地图读取失败；计划快照仍可查看。" + (" " + message if message else ""))
                return
            self._show_server_knowledge_map(graph)

        self._start(read_map, display, "正在读取当前账号工作区的服务端知识地图…")

    def _open_completion(self):
        plan = self.current_plan
        if plan is not None:
            self.open_completion.emit(plan.key)

    def _map_material_ids(self):
        plan = self.current_plan
        if plan is None:
            return ()
        return tuple(str(resource.resource_id) for resource in plan.resources if resource.resource_id)

    def generate_knowledge_map(self):
        """Queue provider grounded map generation for the selected plan.

        The job is deliberately separate from activation: generation may be
        expensive and its result remains a reviewable revision until the user
        explicitly activates it.
        """
        plan, service = self.current_plan, self.plan_service
        starter = getattr(service, "start_server_knowledge_map", None) if service else None
        if plan is None or not callable(starter):
            self.map_status.setText("当前计划或知识地图服务不可用。")
            return False
        material_ids = self._map_material_ids()
        if not material_ids:
            self.map_status.setText("当前计划没有关联资料，无法生成知识地图。")
            return False
        plan_key = plan.key
        def queue():
            return starter(material_ids, anchor_material_id=material_ids[0])
        def queued(job):
            if self.current_plan is None or self.current_plan.key != plan_key:
                return
            self._knowledge_map_job = dict(job or {})
            self._knowledge_map_job_id = str(job.get("id") or "")
            self._knowledge_map_revision = str(job.get("revision") or job.get("result_revision") or "")
            self._update_map_job_status()
        self.map_status.setText("正在提交知识地图生成任务…")
        return self._start(queue, queued, "正在提交知识地图生成任务…")

    def poll_knowledge_map(self):
        """Poll the durable generation job and expose a revision only when ready."""
        service, job_id, plan_key = self.plan_service, self._knowledge_map_job_id, self.current_plan.key if self.current_plan else ""
        reader = getattr(service, "server_knowledge_map_job", None) if service else None
        if not job_id or not callable(reader):
            self.map_status.setText("当前没有可刷新的知识地图生成任务。")
            return False
        def read():
            return reader(job_id)
        def loaded(job):
            if self.current_plan is None or self.current_plan.key != plan_key:
                return
            self._knowledge_map_job = dict(job or {})
            self._knowledge_map_revision = str(job.get("revision") or job.get("result_revision") or "")
            self._update_map_job_status()
        return self._start(read, loaded, "正在读取知识地图生成进度…")

    def _update_map_job_status(self):
        job = self._knowledge_map_job or {}
        status = str(job.get("status") or "unknown")
        progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
        percent = progress.get("percent")
        suffix = f"（{percent:g}%）" if isinstance(percent, (int, float)) else ""
        revision = self._knowledge_map_revision
        if status in {"completed", "succeeded", "ready"} and revision:
            self.map_status.setText(f"知识地图已生成{suffix}，修订 {revision} 待激活。请确认后点击“激活当前修订”。")
        elif status in {"failed", "cancelled", "canceled"}:
            reason = str(job.get("error") or job.get("message") or "生成失败")
            self.map_status.setText(f"知识地图生成{status}：{reason}")
        else:
            self.map_status.setText(f"知识地图生成状态：{status}{suffix}。可继续刷新进度。")
        self._controls()

    def activate_knowledge_map(self):
        """Activate the reviewed revision and bind the returned graph to this plan view."""
        plan, service, revision = self.current_plan, self.plan_service, self._knowledge_map_revision
        activator = getattr(service, "activate_server_knowledge_map_for_plan", None) if service else None
        if plan is None or not revision or not callable(activator):
            self.map_status.setText("没有可激活的知识地图修订。")
            return False
        plan_key = plan.key
        def activate():
            return activator(plan_key, revision)
        def activated(projection):
            if self.current_plan is None or self.current_plan.key != plan_key:
                return
            map_state = (projection or {}).get("knowledge_map", {}) if isinstance(projection, dict) else {}
            graph = map_state.get("graph")
            if isinstance(graph, dict) and isinstance(graph.get("nodes"), list) and isinstance(graph.get("edges"), list):
                self._show_server_knowledge_map(graph)
            self.map_status.setText(f"知识地图修订 {revision} 已激活并绑定当前计划。")
            self._knowledge_map_job = None
            self._knowledge_map_job_id = ""
            self._knowledge_map_revision = ""
        return self._start(activate, activated, "正在激活知识地图修订并绑定当前计划…")

    def _remove_server_map_item(self):
        if self._server_map_item is None:
            return
        index = self.knowledge_tree.indexOfTopLevelItem(self._server_map_item)
        if index >= 0:
            self.knowledge_tree.takeTopLevelItem(index)
        self._server_map_item = None

    def _show_server_knowledge_map(self, graph):
        """Render only validated server nodes/edges, separate from plan refs."""
        self._remove_server_map_item()
        revision = graph.get("revision")
        root = QTreeWidgetItem(["服务器知识地图（当前工作区）", "服务端", str(len(graph["nodes"])),
                                f"修订 {revision} · 与计划任务引用分开展示"])
        root.setToolTip(0, "服务端知识地图；没有按标题将节点关联到计划任务。")
        self.knowledge_tree.addTopLevelItem(root)
        nodes = {node["id"]: node for node in graph["nodes"]}
        incoming = {node_id: [] for node_id in nodes}
        for edge in graph["edges"]:
            incoming[edge["target_id"]].append(edge)
        for node in graph["nodes"]:
            node_id = node["id"]
            relations = incoming[node_id]
            metadata = " · ".join(value for value in (str(node.get("node_type") or ""),
                                                        str(node.get("status") or "")) if value)
            item = QTreeWidgetItem([node["name"], metadata or "服务端节点", str(len(relations)), node_id])
            explanation = str(node.get("explanation") or "")
            if explanation:
                item.setToolTip(0, explanation)
            root.addChild(item)
            for edge in relations:
                source = nodes[edge["source_id"]]
                relation = str(edge.get("relation") or "关联")
                child = QTreeWidgetItem([f"前置：{source['name']}", relation, "", edge["id"]])
                reason = str(edge.get("reason") or "")
                if reason:
                    child.setToolTip(0, reason)
                item.addChild(child)
        root.setExpanded(True)
        self._server_map_item = root
        self.map_status.setText(f"已读取服务器确认的知识地图（修订 {revision}）：{len(graph['nodes'])} 个节点、{len(graph['edges'])} 条关系。计划快照中的任务引用仍单独展示，未按标题推测对应关系。")
        self.status.setText("服务端知识地图已读取；计划任务和知识地图节点保持各自来源。")

    def _populate_knowledge_map(self, plan):
        self.knowledge_tree.clear()
        self._server_map_item = None
        details = plan.details if isinstance(plan.details, dict) else {}
        revision = (details.get("knowledge_map_revision") or details.get("map_revision_id")
                    or details.get("knowledge_map_revision_id") or "")
        groups = {}
        tasks_by_id = {str(task.get("id")): task for task in plan.tasks if task.get("id")}
        source_titles = {}
        for source in details.get("sources", ()):
            if isinstance(source, dict) and source.get("id"):
                source_titles[str(source["id"])] = str(source.get("title") or source.get("final_url") or source["id"])
        for resource in plan.resources:
            source_titles[str(resource.resource_id)] = resource.filename
        for row, task in enumerate(plan.tasks):
            reference = valid_knowledge_ref(task)
            if reference is not None:
                point_id = str(reference["knowledge_point_id"])
                source = str(reference.get("source") or reference.get("kind") or "已关联")
                group_key = (source, str(reference.get("map_revision_id") or ""), point_id)
                label = point_id
                origin = source
            else:
                group_key = ("unmapped", "", str(task.get("id") or row))
                label = "未绑定知识点"
                origin = "任务快照未提供知识引用"
            groups.setdefault(group_key, {"label": label, "origin": origin, "rows": []})["rows"].append((row, task))
        for group in groups.values():
            root = QTreeWidgetItem([group["label"], "", str(len(group["rows"])), group["origin"]])
            self.knowledge_tree.addTopLevelItem(root)
            for row, task in group["rows"]:
                phase = str(task.get("learning_phase") or "—")
                title = str(task.get("title") or task.get("name") or "未命名任务")
                relations = []
                prerequisites = task.get("prerequisite_task_ids", ())
                if isinstance(prerequisites, (list, tuple)) and prerequisites:
                    names = [str(tasks_by_id.get(str(task_id), {}).get("title") or task_id)
                             for task_id in prerequisites]
                    relations.append("前置：" + "、".join(names))
                source_ids = task.get("source_ids", ())
                chunk_ids = task.get("source_chunk_ids", ())
                if isinstance(source_ids, (list, tuple)) and source_ids:
                    names = [source_titles.get(str(source_id), str(source_id)) for source_id in source_ids]
                    relations.append("资料：" + "、".join(names))
                elif isinstance(chunk_ids, (list, tuple)) and chunk_ids:
                    relations.append("资料段落：" + "、".join(str(value) for value in chunk_ids))
                origin = " · ".join(relations) or "任务第 %d 行" % (row + 1)
                child = QTreeWidgetItem([title, phase, "", origin])
                child.setData(0, Qt.ItemDataRole.UserRole, row)
                root.addChild(child)
            root.setExpanded(True)
        if not groups:
            self.map_status.setText("当前计划没有任务，暂时没有可展示的知识地图节点。")
        elif revision:
            self.map_status.setText("知识地图修订：%s · 已按原始知识引用分组。" % revision)
        elif any(key[0] != "unmapped" for key in groups):
            self.map_status.setText("当前计划已带有知识点引用；快照未提供修订号，仍按原始引用展示。")
        else:
            self.map_status.setText("当前计划任务尚未带知识地图引用；没有猜测或合并同名任务。")

    def _start(self, operation, completed, message):
        if self._disposed or self.busy:
            return False
        self.status.setText(message)
        self._completed = completed
        self._future = self._executor.submit(operation)
        self._controls()

        self._timer.start()
        return True

    def _poll(self):
        if self._disposed or self._future is None or not self._future.done():
            return
        future, completed = self._future, self._completed
        self._future, self._completed = None, None
        self._timer.stop()
        try:
            value = future.result()
            if not isinstance(value, ExportResult):
                self.source.require_current()
            completed(value)
        except PlanError as exc:
            self.status.setText(str(exc))
            if self._export_callback:
                self._finish_export(ExportResult(ExportStatus.CANCELLED if exc.code == "CANCELLED" else ExportStatus.FAILED, exc.code, str(exc), account_id=self.account_id))
        except Exception:
            self.status.setText("计划操作失败；账号同步状态未确认，请重试。")
            if self._export_callback:
                self._finish_export(ExportResult(ExportStatus.FAILED, "EXPORT_FAILED", "计划读取或导出失败；未报告保存成功。", account_id=self.account_id))
        self._controls()
        if self._refresh_pending and not self.busy:
            self._refresh_pending = False
            self.refresh()

    def _clear(self, *, emit=True):
        self.catalog, self.originals = None, ()
        self.plan_list.blockSignals(True)
        self.plan_list.clear()
        self.plan_list.blockSignals(False)
        self._selected_key = ""
        self._knowledge_map_job_id = ""
        self._knowledge_map_revision = ""
        self._knowledge_map_job = None
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.blockSignals(False)
        self.tasks.setRowCount(0)
        self.resources.setRowCount(0)
        self.detail.clear()
        self.resource_preview.clear()
        self.plan_title.clear()
        self.knowledge_tree.clear()
        self.map_status.setText("选择计划后读取知识地图引用。")
        if emit:
            self.state_changed.emit()

    def rename_current_plan(self):
        plan = self.current_plan
        if plan is None or self.plan_service is None or plan.source != "本机计划":
            return
        title, ok = QInputDialog.getText(self, "重命名计划", "计划名称：", text=plan.title)
        if not ok or not title.strip() or title.strip() == plan.title:
            return
        self._start(lambda: self.plan_service.rename_local_plan(plan.key, title), self._renamed, "正在保存并同步计划名称…")

    def _renamed(self, plan):
        self.status.setText("计划名称已保存到本机；账号同步会更新其他设备。")
        self.refresh()

    def delete_current_plan(self):
        plan = self.current_plan
        if plan is None or self.plan_service is None or plan.source != "本机计划":
            return
        answer = QMessageBox.question(self, "归档计划", f"确定归档本账号的计划“{plan.title}”吗？\n归档标记会同步到其他设备；已保存的学习进度、资料关联和历史记录仍会保留。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(lambda: self.plan_service.delete_local_plan(plan.key), self._deleted, "正在保存账号删除标记…")

    def _deleted(self, _value):
        self.status.setText("计划已归档；账号同步会将归档标记传到其他设备，学习进度和历史记录仍保留。")
        self.refresh()

    def refresh(self):
        if self.source is None or self.busy or self._disposed:
            return
        # Keep the last confirmed snapshot visible while the next account
        # read is in flight.  Clearing the catalog here made every sync or
        # retry look like the page had reopened and briefly showed an empty
        # panel.  The loaded callback replaces the list atomically.
        self._start(self.source.load, self._loaded, "正在读取当前账号的计划快照…")

    def _loaded(self, catalog):
        # Commit the replacement only after the new snapshot is available.
        # The previous catalog remains visible during network I/O and on
        # failure, so a retry cannot flash an empty page.
        self._clear(emit=False)
        if self.resolver:
            self.resolver.close()
        self.catalog = catalog
        self.resolver = PlanResourceResolver(data_root=self.data_root, scope=catalog.scope, legacy_data_root=self.legacy_data_root)
        self.plan_list.clear()
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItem("请选择当前计划", "")
        for plan in catalog.plans:
            self.selector.addItem(plan.title, plan.key)
            completed = sum(1 for task in plan.tasks if task.get("status") == "completed")
            origin = str(plan.source or "账号同步")
            item = QListWidgetItem(
                f"{plan.title}\n{human_status(plan.status)} · {completed} / {len(plan.tasks)} 项任务 · {len(plan.resources)} 份资料\n来源：{origin}"
            )
            item.setData(Qt.ItemDataRole.UserRole, plan.key)
            item.setToolTip("来源：%s\n%s" % (origin, "\n".join(plan.issues) if plan.issues else "服务器/账号计划快照已读取。"))
            self.plan_list.addItem(item)
        self.selector.blockSignals(False)
        notices = self._display_notices(catalog.notices)
        self.status.setText("\n".join(notices) or (f"共 {len(catalog.plans)} 份计划，点击查看详情。" if catalog.plans else "还没有计划，可从“创建计划”开始。"))
        key = catalog.selected_key
        if not key and len(catalog.plans) == 1:
            key = catalog.plans[0].key
        self.select_plan(key)
        if self.current_plan is None:
            self.show_catalog()

    def show_catalog(self):
        self.views.setCurrentWidget(self.plan_list)

    def _open_plan_item(self, item):
        self.open_plan(item.data(Qt.ItemDataRole.UserRole))

    def open_plan(self, key):
        if not self.select_plan(key):
            return False
        self.views.setCurrentWidget(self.detail_page)
        return True

    def select_plan(self, key):
        if self.catalog is None or not any(plan.key == key for plan in self.catalog.plans):
            return False
        if key != self._selected_key:
            if self.busy:
                return False
            self._knowledge_map_job_id = ""
            self._knowledge_map_revision = ""
            self._knowledge_map_job = None
            self._selected_key = key
            service = self.plan_service or getattr(self, "_wiring_test_service", None)
            if service is not None:
                try:
                    service.select_plan(key)
                except Exception:
                    # The async resolver below will surface a truthful error;
                    # a failed selection must never replace the catalog with
                    # an empty state.
                    pass
            index = self.selector.findData(key)
            self.selector.blockSignals(True)
            if index >= 0:
                self.selector.setCurrentIndex(index)
            self.selector.blockSignals(False)
            self._select()
        for row in range(self.plan_list.count()):
            if self.plan_list.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.plan_list.setCurrentRow(row)
                break
        return True

    def _select(self):
        current = self.selector.currentData()
        if current:
            self._selected_key = current
        plan = self.current_plan
        self.detail.clear()
        self.tasks.setRowCount(0)
        self.resources.setRowCount(0)
        self.originals = ()
        if plan is None:
            self._controls()
            return
        self.plan_title.setText(f"{plan.title} · {plan.source or '账号同步计划'}")
        self.resource_preview.clear()
        self.mission_status.setText("今日任务按当前计划进度动态编译；点击“编译今日任务”查看。")
        self._populate_knowledge_map(plan)
        self.tasks.setRowCount(len(plan.tasks))
        for row, task in enumerate(plan.tasks):
            values = (task.get("day_date") or task.get("date") or "—", task.get("title") or task.get("name") or "未命名任务", task.get("learning_phase") or "—", task.get("estimated_task_minutes", task.get("planned_task_minutes", "—")), human_status(task.get("status")))
            for column, value in enumerate(values):
                self.tasks.setItem(row, column, QTableWidgetItem(str(value)))
        self.apply_preferences(getattr(self, "_product_preferences", {}))
        def read():
            if self.plan_service:
                self.plan_service.select_plan(plan.key)
                if self.resolver:
                    self.resolver.close()
                self.resolver = self.plan_service.resolver_for(plan.key)
            return tuple(self.resolver.resolve(resource) for resource in plan.resources)
        self._start(read, self._show_originals, "正在校验关联资料原件…")

    def _show_originals(self, originals):
        self.originals = originals
        self.resources.setRowCount(len(originals))
        for row, original in enumerate(originals):
            self.resources.setItem(row, 0, QTableWidgetItem(original.resource.filename))
            self.resources.setItem(row, 1, QTableWidgetItem(original.message))
        plan = self.current_plan
        self.status.setText("\n".join([f"{len(self.catalog.plans)} 份计划 · 当前计划 {len(plan.tasks)} 项任务、{len(plan.resources)} 份资料", *self._display_notices(self.catalog.notices), *plan.issues]))

    def _task_detail(self):
        plan, row = self.current_plan, self.tasks.currentRow()
        if plan and 0 <= row < len(plan.tasks):
            task = plan.tasks[row]
            self.detail.setPlainText(f"目标：{task.get('objective') or '未记录'}\n完成条件：{task.get('acceptance_condition') or '未记录'}")

    def choose_original(self):
        row = self.resources.currentRow()
        if self.busy or not 0 <= row < len(self.originals):
            return
        resource = self.originals[row].resource
        path, _ = QFileDialog.getOpenFileName(self, "重新关联 " + resource.filename, str(self.data_root), "所有文件 (*)")
        if path:
            def completed(original):
                values = list(self.originals)
                values[row] = original
                self._show_originals(tuple(values))
            self._start(lambda: self.resolver.relink(resource, Path(path)), completed, "正在校验所选原件…")

    def apply_preferences(self, values):
        from PySide6.QtGui import QColor, QBrush
        self._product_preferences = dict(values)
        colors = values.get("task_colors", {})
        plan = self.current_plan
        if plan is None:
            return
        for row, task in enumerate(plan.tasks):
            names = [task.get("task_type"), task.get("learning_phase"), task.get("status"),
                     human_status(task.get("status")), task.get("title")]
            color = next((colors[name] for name in names if name in colors), "")
            if not color:
                title = str(task.get("title") or "")
                color = next((value for name, value in colors.items() if name and name in title), "")
            brush = QBrush(QColor(color)) if color and QColor(color).isValid() else QBrush()
            for column in range(self.tasks.columnCount()):
                item = self.tasks.item(row, column)
                if item is not None:
                    item.setForeground(brush)

    def preview_original(self):
        row = self.resources.currentRow()
        if self.busy or not 0 <= row < len(self.originals):
            return
        resource = self.originals[row].resource
        self.resource_preview.clear()
        self.mission_status.setText("今日任务按当前计划进度动态编译；点击“编译今日任务”查看。")
        def read():
            original = self.resolver.resolve(resource)
            if original.status != "available":
                # The structured sync contract restores a bounded excerpt
                # with resource metadata. Show it in plan details when this
                # device lacks the full original, while keeping the missing
                # original status explicit.
                material_store = None
                try:
                    material_store = MaterialStore(self.data_root, self.account_id)
                    preview = material_store.preview(resource.resource_id)
                except MaterialError:
                    preview = None
                finally:
                    if material_store is not None:
                        material_store.close()
                if preview is not None and preview.text:
                    return (preview.text[:200_000]
                            + "\n（原件缺失；这里显示云端保留的正文片段，不能替代完整原件）")
                raise PlanError("ORIGINAL_UNAVAILABLE", original.message)
            if resource.size_bytes > MAX_FILE_BYTES or Path(resource.filename).suffix.lower() not in {".txt", ".md", ".csv", ".json"}:
                return "原件已校验。此格式/大小暂不提供内置正文预览，可导出后用对应软件离线查看。"
            with open_resource_source(original.path) as (_, stream):
                raw = stream.read(MAX_FILE_BYTES + 1)
            import hashlib
            if len(raw) != resource.size_bytes or hashlib.sha256(raw).hexdigest() != resource.sha256:
                raise PlanError("ORIGINAL_CHANGED", "原件在读取期间发生变化，请重试。")
            text, _ = parse_text(raw, Path(resource.filename).suffix.lower())
            return text[:200_000] + ("\n（这里只显示前 200,000 字符）" if len(text) > 200_000 else "")
        def shown(text):
            self.resource_preview.setPlainText(text)
            self.status.setText("关联资料：" + resource.filename)
        self._start(read, shown, "正在读取关联资料正文…")

    def request_export(self, on_finished, *, cleanup_roots=()):
        """GUI-thread API for MF-01; exactly one callback with ExportResult.

        Reloads the real source, asks for a plan only when needed, then asks for
        a destination. No account deletion or confirmation is performed here.
        cleanup_roots adds any further account-layer deletion destinations.
        """
        if self._disposed or self.source is None or self.busy or self._export_callback is not None:
            on_finished(ExportResult(ExportStatus.FAILED, "PLAN_NOT_READY", "计划会话尚未就绪或正在处理，请稍后重试。", account_id=self.account_id))
            return
        selected_key = self.current_plan.key if self.current_plan else None
        self._export_callback = on_finished
        self._export_cancelled = ExportCancellation()
        self.cancel_export_button.show()
        self.cancel_export_button.setEnabled(True)

        def loaded(catalog):
            if self._export_cancelled.is_set():
                self._finish_export(ExportResult(ExportStatus.CANCELLED, "CANCELLED", "导出已取消。", account_id=self.account_id))
                return
            if not catalog.plans:
                self._clear()
                self.catalog = catalog
                self._finish_export(ExportResult(ExportStatus.FAILED if catalog.notices else ExportStatus.NO_CURRENT_PLAN, "PLAN_SOURCE_INCOMPLETE" if catalog.notices else "NO_CURRENT_PLAN", "\n".join(catalog.notices) or "服务器已确认：暂无当前计划；未生成空导出包。", account_id=self.account_id))
                return
            plan = next((item for item in catalog.plans if item.key == selected_key), None)
            if selected_key and plan is None:
                self._clear()
                self._finish_export(ExportResult(ExportStatus.FAILED, "CURRENT_PLAN_CHANGED", "服务器当前计划已变化，请重新读取并选择计划。", account_id=self.account_id))
                return
            if plan is None:
                if len(catalog.plans) == 1:
                    plan = catalog.plans[0]
                else:
                    labels = [f"{index + 1}. {p.workspace_title} · {p.title}" for index, p in enumerate(catalog.plans)]
                    selected, ok = QInputDialog.getItem(self, "选择要保存的当前计划", "仅导出所选计划及其关联资料", labels, 0, False)
                    if not ok or self._disposed:
                        self._finish_export(ExportResult(ExportStatus.CANCELLED, "CANCELLED", "已取消选择计划。", account_id=self.account_id))
                        return
                    plan = catalog.plans[labels.index(selected)]
            if self.resolver is None or self.resolver.scope != catalog.scope:
                if self.resolver:
                    self.resolver.close()
                self.resolver = PlanResourceResolver(data_root=self.data_root, scope=catalog.scope, legacy_data_root=self.legacy_data_root)
            path, _ = QFileDialog.getSaveFileName(self, "保存当前计划及关联资料", str(self.data_root.parent / "MentorFlow-current-plan.zip"), "离线计划包 (*.zip)")
            if not path or self._disposed or self._export_cancelled.is_set():
                self._finish_export(ExportResult(ExportStatus.CANCELLED, "CANCELLED", "已取消保存。", account_id=self.account_id))
                return
            cancellation = self._export_cancelled
            def export():
                if self.plan_service:
                    self.plan_service.select_plan(plan.key)
                    actual = self.plan_service.catalog_for(plan.key)
                    resolver = self.plan_service.resolver_for(plan.key)
                    try:
                        return export_current_plan(catalog=actual, plan_key=plan.key, resolver=resolver, destination=Path(path), cleanup_roots=(self.data_root, *cleanup_roots), cancelled=cancellation, require_current=self.source.require_current)
                    finally:
                        resolver.close()
                return export_current_plan(catalog=catalog, plan_key=plan.key, resolver=self.resolver, destination=Path(path), cleanup_roots=(self.data_root, *cleanup_roots), cancelled=cancellation, require_current=self.source.require_current)
            self._start(export, self._finish_export, "正在导出当前计划和关联原件；可取消…")
        self._start(self.source.load, loaded, "正在确认服务器当前计划及导出范围…")

    def cancel_export(self):
        if self._export_cancelled:
            self._export_cancelled.set()
            self.cancel_export_button.setEnabled(False)
            self.status.setText("正在取消导出…")

    def _finish_export(self, result):
        callback, self._export_callback = self._export_callback, None
        self._export_cancelled = None
        self.cancel_export_button.hide()
        self.status.setText(result.message + ("\n缺失：" + "、".join(result.missing) if result.missing else "") + ("\n" + str(result.output_path) if result.output_path else ""))
        if callback:
            callback(result)

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.cancel_export()
        if self._export_callback:
            outcome = self._export_cancelled.published_result
            self._finish_export(outcome or ExportResult(ExportStatus.CANCELLED, "CANCELLED", "账号窗口已关闭，导出操作已取消。", account_id=self.account_id))
        if self.plan_service:
            self.plan_service.changed.disconnect(self._service_changed)
        elif self.source:
            self.source.close()
        if self.resolver:
            self.resolver.close()
        self._timer.stop()
        if self._future:
            self._future.cancel()
        self._future, self._completed = None, None
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._clear()
        self.status.clear()
        self.setEnabled(False)

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)
