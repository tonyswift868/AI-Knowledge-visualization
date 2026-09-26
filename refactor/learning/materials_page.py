"""Native materials UI split from the old workspace/teaching-bound page.

Retains the table, file chooser and future/timer pattern; all operations use the
extracted account-local store. There is no workspace/plan prerequisite.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QBrush, QPen, QPolygonF
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QPushButton, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QScrollArea, QProgressBar, QStackedWidget, QGraphicsView, QGraphicsScene,
)

from .store import Material, MaterialError, MaterialPreview, MaterialStore
from .text_import import human_size
from .plan_models import valid_knowledge_ref
from .presentation import completed_task, human_status


MATERIALS_PAGE_STYLE = """
QWidget#materialsPage { background: #fff8fb; }
QFrame#materialsHeroCard, QFrame#materialsToolbarCard, QFrame#materialsStatusCard,
QFrame#materialsTablePanel, QFrame#materialsPreviewPanel {
    background: #fffdfd;
    border: 1px solid #ead7df;
    border-radius: 16px;
}
QFrame#materialsHeroCard { background: #fff4f8; border-color: #efc7d7; }
QLabel#materialsEyebrow { color: #a94f79; font-size: 12px; font-weight: 800; letter-spacing: 1px; }
QLabel#pageHeading, QLabel#pageSubtitle, QLabel#scopeNotice,
QLabel#materialsSectionHeading, QLabel#materialsSectionHint { background: transparent; }
QLabel#pageSubtitle { color: #786572; }
QLabel#scopeNotice {
    color: #725568;
    background: #fff8fb;
    border: 1px solid #f0d7e2;
    border-radius: 10px;
    padding: 10px 12px;
}
QLabel#materialsSectionHeading { color: #8d3f62; font-size: 17px; font-weight: 750; }
QLabel#materialsSectionHint { color: #8d7a84; font-size: 12px; }
QFrame#materialsStatusCard { background: #fff8fb; border-color: #f0d7e2; }
QLabel#materialsStatus { background: transparent; color: #725568; padding: 2px 0; }
QPushButton {
    min-height: 42px;
    border: 1px solid #e7b8cc;
    border-radius: 10px;
    padding: 0 14px;
    background: #fffdfd;
    color: #3b2b3f;
    font-weight: 650;
}
QPushButton:hover { background: #ffeaf3; border-color: #db86aa; }
QPushButton:pressed { background: #f7d8e5; }
QPushButton#primaryButton {
    min-height: 46px;
    color: white;
    background: #c95f8d;
    border: 0;
}
QPushButton#primaryButton:hover { background: #ae4d78; }
QPushButton#primaryButton:pressed { background: #963b66; }
QPushButton:disabled, QPushButton#primaryButton:disabled {
    color: #aa99a1;
    background: #f5eef1;
    border-color: #ebdfe4;
}
QPushButton#materialsRefreshButton, QPushButton#materialsViewButton {
    color: #8d3f62;
    background: #fff0f6;
    border: 1px solid #e6abc5;
}
QPushButton#materialsRefreshButton:hover, QPushButton#materialsViewButton:hover {
    background: #fbdfe9;
    border-color: #d99ab6;
}
QPushButton#materialsOpenButton, QPushButton#materialsRelinkButton {
    color: #76636d;
    background: #fffdfd;
    border: 1px solid #e4d5dc;
}
QPushButton#materialsOpenButton:hover, QPushButton#materialsRelinkButton:hover {
    color: #5d4a55;
    background: #f9f1f4;
    border-color: #cfaebe;
}
QTableWidget#materialsTable {
    border: 1px solid #ead7df;
    border-radius: 12px;
    background: #ffffff;
    alternate-background-color: #fff9fb;
    gridline-color: #f2e4e9;
    selection-background-color: #f3d7e3;
    selection-color: #342532;
}
QTableWidget#materialsTable::item { padding: 7px 8px; }
QTableWidget#materialsTable::item:selected { background: #f3d7e3; color: #342532; }
QTableWidget#materialsTable QHeaderView::section {
    background: #fff0f5;
    color: #8d3f62;
    border: 0;
    border-bottom: 1px solid #ead7df;
    padding: 10px 8px;
    font-weight: 750;
}
QLabel#materialDetail {
    color: #5f4b58;
    background: #fff8fb;
    border: 1px solid #f0d7e2;
    border-radius: 10px;
    padding: 9px 11px;
}
QPlainTextEdit#materialPreview {
    border: 1px solid #ead7df;
    border-radius: 12px;
    padding: 12px;
    background: #fffdfd;
    selection-background-color: #f1d1df;
}
QSplitter#materialsSplitter::handle { background: transparent; height: 10px; }
QPushButton#materialsRefreshButton:disabled, QPushButton#materialsViewButton:disabled,
QPushButton#materialsOpenButton:disabled, QPushButton#materialsRelinkButton:disabled {
    color: #aa99a1;
    background: #f5eef1;
    border-color: #ebdfe4;
}
"""


class MaterialsPage(QWidget):
    def __init__(self, store: MaterialStore, parent=None, *, title: str = "资料管理"):
        super().__init__(parent)
        self.setObjectName("materialsPage")
        self.setStyleSheet(MATERIALS_PAGE_STYLE)
        self.store = store
        self._disposed = False
        self._future: Future | None = None
        self._completed = None
        self._rows: dict[str, Material] = {}
        self._plan_service = None
        self._plan_signal = None
        self._plan_names: dict[str, tuple[str, ...]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mf-learning")
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        hero = QFrame()
        hero.setObjectName("materialsHeroCard")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(20, 16, 20, 16)
        eyebrow = QLabel("本机资料库")
        eyebrow.setObjectName("materialsEyebrow")
        hero_layout.addWidget(eyebrow)
        heading = QLabel(title)
        heading.setObjectName("pageHeading")
        hero_layout.addWidget(heading)
        subtitle = QLabel("导入资料，供计划、课堂和复习使用。支持 TXT、Markdown、CSV、JSON，单份不超过 8 MiB。")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        hero_layout.addWidget(subtitle)
        layout.addWidget(hero)
        scope = QLabel("资料属于当前账号，并按计划显示关联关系；原件缺失时仍保留可读正文，可在本机重新关联。")
        scope.setObjectName("scopeNotice")
        scope.setWordWrap(True)
        layout.addWidget(scope)
        toolbar = QFrame()
        toolbar.setObjectName("materialsToolbarCard")
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(14, 12, 14, 12)
        toolbar_layout.setSpacing(8)
        import_row = QHBoxLayout()
        import_row.setSpacing(8)
        self.upload_button = QPushButton("添加资料")
        self.upload_button.setObjectName("primaryButton")
        self.upload_button.setAccessibleName("添加资料")
        self.upload_button.clicked.connect(self.choose_file)
        import_row.addWidget(self.upload_button)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("materialsRefreshButton")
        self.refresh_button.setAccessibleName("刷新资料列表")
        self.refresh_button.clicked.connect(self.refresh_materials)
        import_row.addWidget(self.refresh_button)
        import_row.addStretch()
        toolbar_layout.addLayout(import_row)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.view_button = QPushButton("查看正文")
        self.view_button.setObjectName("materialsViewButton")
        self.view_button.setAccessibleName("查看选中资料正文")
        self.view_button.clicked.connect(self.open_selected)
        action_row.addWidget(self.view_button)
        self.open_original_button = QPushButton("打开原件")
        self.open_original_button.setObjectName("materialsOpenButton")
        self.open_original_button.setAccessibleName("打开选中资料原件")
        self.open_original_button.setToolTip("用系统默认应用打开完整原件的副本；编辑不会更改已保存的原件。")
        self.open_original_button.clicked.connect(self.open_original_selected)
        action_row.addWidget(self.open_original_button)
        self.relink_button = QPushButton("重新关联…")
        self.relink_button.setObjectName("materialsRelinkButton")
        self.relink_button.setAccessibleName("重新关联选中资料原件")
        self.relink_button.setToolTip("选择与原资料内容一致的文件，恢复当前账号的原件副本，保留同一资料记录。")
        self.relink_button.clicked.connect(self.choose_relink)
        action_row.addWidget(self.relink_button)
        action_row.addStretch()
        toolbar_layout.addLayout(action_row)
        layout.addWidget(toolbar)
        status_card = QFrame()
        status_card.setObjectName("materialsStatusCard")
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(16, 10, 16, 10)
        self.status = QLabel("正在读取本机资料…")
        self.status.setObjectName("materialsStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("资料操作状态")
        status_layout.addWidget(self.status)
        layout.addWidget(status_card)
        self.group_summary = QLabel("正在读取计划资料分组…")
        self.group_summary.setObjectName("materialsSectionHint")
        self.group_summary.setWordWrap(True)
        layout.addWidget(self.group_summary)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("materialsSplitter")
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        table_panel = QFrame()
        table_panel.setObjectName("materialsTablePanel")
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(12, 10, 12, 12)
        table_layout.setSpacing(8)
        table_heading = QLabel("资料列表")
        table_heading.setObjectName("materialsSectionHeading")
        table_layout.addWidget(table_heading)
        table_hint = QLabel("选择一行查看正文或操作原件。")
        table_hint.setObjectName("materialsSectionHint")
        table_hint.setWordWrap(True)
        table_layout.addWidget(table_hint)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("materialsTable")
        self.table.setAccessibleName("当前账号本机资料")
        # Keep the original status column position for existing callers; the
        # shared plan association is appended as an additional column.
        self.table.setHorizontalHeaderLabels(["文件", "状态", "大小", "导入时间（UTC）", "所属计划"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.open_selected)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setMinimumSectionSize(80)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.setAlternatingRowColors(True)
        table_layout.addWidget(self.table, 1)
        splitter.addWidget(table_panel)
        detail = QFrame()
        detail.setObjectName("materialsPreviewPanel")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 10, 12, 12)
        detail_layout.setSpacing(8)
        preview_heading = QLabel("正文预览")
        preview_heading.setObjectName("materialsSectionHeading")
        detail_layout.addWidget(preview_heading)
        self.detail_label = QLabel("选择资料查看正文")
        self.detail_label.setObjectName("materialDetail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        detail_layout.addWidget(self.detail_label)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("materialPreview")
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("资料正文预览")
        self.preview.setPlaceholderText("导入后，正文和处理结果会显示在这里。")
        detail_layout.addWidget(self.preview, 1)
        splitter.addWidget(detail)
        splitter.setSizes([240, 300])
        layout.addWidget(splitter, 1)
        self.refresh_materials()

    @property
    def busy(self) -> bool:
        return self._future is not None

    def _submit(self, operation, completed, message: str) -> None:
        if self._disposed or self.busy:
            return
        self.upload_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.table.setEnabled(False)
        self.status.setText(message)
        self._completed = completed
        self._future = self._executor.submit(operation)
        self._update_actions()
        self._timer.start()

    def _poll(self) -> None:
        if self._disposed or self._future is None or not self._future.done():
            return
        future, completed = self._future, self._completed
        self._future, self._completed = None, None
        self._timer.stop()
        self.upload_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.table.setEnabled(True)
        try:
            result = future.result()
        except MaterialError as exc:
            if exc.material is not None:
                self._show_material_state(exc.material)
                self.preview.clear()
                self.detail_label.setText(f"{exc.material.filename} · {exc.material.status_label}")
            self.status.setText(str(exc))
        except Exception:
            self.status.setText("资料操作未完成，请重试。现有记录未被清空。")
        else:
            completed(result)
        self._update_actions()

    def _selected_id(self) -> str:
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else ""

    def _update_actions(self) -> None:
        material = self._rows.get(self._selected_id())
        enabled = not self._disposed and not self.busy and material is not None
        self.view_button.setEnabled(enabled)
        self.open_original_button.setEnabled(enabled and material.availability == "available")
        self.relink_button.setEnabled(enabled and material.availability != "available")

    def _clear_rows(self) -> None:
        self._rows.clear()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)

    def bind_plan_service(self, service) -> None:
        """Use the shell's shared, account-scoped plan projection for grouping."""
        if self._disposed:
            return
        if service is self._plan_service:
            return
        if self._plan_signal is not None and self._plan_service is not None:
            try:
                self._plan_service.changed.disconnect(self._plan_signal)
            except (RuntimeError, TypeError):
                pass
        self._plan_service, self._plan_signal = service, None
        changed = getattr(service, "changed", None)
        if callable(getattr(changed, "connect", None)):
            def on_changed(*_args):
                if not self._disposed and not self.busy:
                    self.refresh_materials()
            changed.connect(on_changed)
            self._plan_signal = on_changed
        if not self.busy:
            self.refresh_materials()

    def bind_context(self, context) -> None:
        """Compatibility hook used by feature wiring and page factories."""
        self.bind_plan_service(context.get("plan_service") if isinstance(context, dict) else None)

    @staticmethod
    def _projection(service):
        if service is None:
            return {}, ""
        try:
            # Read the service-owned material projection first.  This confirms
            # that grouping only uses records restored for the current account.
            def value(item, key, default=None):
                return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)
            projected = {str(value(item, "material_id", "")) for item in service.materials()
                         if value(item, "material_id", None)}
            plans = service.list_plans()
        except Exception:
            return {}, "计划关联暂不可用；已显示本机资料。"
        names = {}
        for plan in plans:
            title = str(value(plan, "title", "") or value(plan, "name", "") or "未命名计划").strip()
            for resource in value(plan, "resources", ()) or ():
                material_id = str(value(resource, "resource_id", "") or value(resource, "id", ""))
                if material_id and (not projected or material_id in projected):
                    names.setdefault(material_id, []).append(title)
        return {key: tuple(dict.fromkeys(value)) for key, value in names.items()}, ""

    def _load_rows(self):
        rows = self.store.list_materials()
        names, notice = self._projection(self._plan_service)
        return rows, names, notice

    def refresh_materials(self) -> None:
        if self._disposed or self.busy:
            return
        preferred_id = self._selected_id()
        self.preview.clear()
        self.detail_label.setText("选择资料查看正文")
        self._clear_rows()
        self._submit(self._load_rows, lambda result: self._show_rows(*result, preferred_id), "正在读取本机资料…")

    def _show_rows(self, rows: list[Material], plan_names=None, projection_notice: str = "", preferred_id: str = "") -> None:
        # Keep the local store as the source of original bytes and metadata;
        # only the plan association column comes from the shared service.
        if isinstance(plan_names, str) and not projection_notice:
            preferred_id, plan_names, projection_notice = plan_names, {}, ""
        self._plan_names = plan_names or {}
        self._rows = {material.material_id: material for material in rows}
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setCurrentCell(-1, -1)
        self.table.setRowCount(len(rows))
        selected_row = -1
        for row_number, material in enumerate(rows):
            associated = "、".join(self._plan_names.get(material.material_id, ())) or "未关联计划"
            values = (material.filename, material.status_label, human_size(material.size_bytes), material.created_at[:19].replace("T", " "), associated)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, material.material_id)
                self.table.setItem(row_number, column, item)
            if material.material_id == preferred_id:
                selected_row = row_number
        self.table.blockSignals(False)
        groups = {}
        for material in rows:
            for name in self._plan_names.get(material.material_id, ()):
                groups[name] = groups.get(name, 0) + 1
        if groups:
            self.group_summary.setText("按计划分组：" + " · ".join(f"{name}（{count}）" for name, count in groups.items()))
        elif rows:
            self.group_summary.setText("按计划分组：未关联计划（{}）".format(len(rows)))
        else:
            self.group_summary.setText("当前暂无资料可按计划分组。")
        self.status.setText((f"本机共有 {len(rows)} 份资料。选择一行可查看正文；原件缺失或变化时可重新关联。" if rows else "当前账号在本机暂无导入记录。点击“添加资料”开始.") + (f" {projection_notice}" if projection_notice else ""))
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        self._update_actions()

    def choose_file(self) -> None:
        if self._disposed or self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择资料", str(self.store.root),
            "支持的资料 (*.txt *.md *.csv *.json);;所有文件 (*)",
        )
        if path:
            self.import_path(path)

    def import_path(self, path: str | Path) -> None:
        """The same real import operation used by the chooser and local tests."""
        if self._disposed or self.busy:
            return
        self.preview.clear()
        self.detail_label.setText("正在保存原件并解析正文…")

        def operation():
            material = self.store.import_file(path)
            return material, *self._load_rows()

        def completed(result):
            material, rows, names, notice = result
            self._show_rows(rows, names, notice, material.material_id)

        self._submit(operation, completed, "正在导入资料…")

    def open_selected(self) -> None:
        if self._disposed or self.busy:
            return
        material_id = self._selected_id()
        self._update_actions()
        if not material_id:
            self.preview.clear()
            self.detail_label.setText("选择资料查看正文")
            return
        self.preview.clear()
        self.detail_label.setText("正在读取资料…")
        self._submit(lambda: self.store.preview(material_id), self._show_preview, "正在检查原件并读取正文…")

    def _show_material_state(self, material: Material) -> None:
        self._rows[material.material_id] = material
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == material.material_id:
                self.table.item(row, 1).setText(material.status_label)
                break

    def _show_preview(self, result: MaterialPreview) -> None:
        material = result.material
        self._show_material_state(material)
        self.detail_label.setText(f"{material.filename} · {material.status_label}" + (f"\n{result.notice}" if result.notice else ""))
        self.preview.setPlainText(result.text)
        self.status.setText(result.notice or f"已读取 {material.filename}（{material.encoding}）；完整原件已保存在此账号下。")

    def open_original_selected(self) -> None:
        if self._disposed or self.busy:
            return
        material_id = self._selected_id()
        if material_id:
            self._submit(lambda: self.store.prepare_original_open(material_id), self._open_prepared, "正在校验完整原件…")

    def _open_prepared(self, path: Path) -> None:
        if self._disposed:
            return
        try:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception:
            opened = False
        self.status.setText(
            "已请求系统默认应用打开完整原件副本；在外部应用中的编辑不会更改已保存原件。"
            if opened else "系统未能打开原件，请检查此文件格式的默认应用后重试。"
        )

    def choose_relink(self) -> None:
        if self._disposed or self.busy:
            return
        material = self._rows.get(self._selected_id())
        if material is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"重新关联 {material.filename}（须与原内容一致）", "", "所有文件 (*)",
        )
        if path:
            self.relink_path(material.material_id, path)

    def relink_path(self, material_id: str, path: str | Path) -> None:
        if self._disposed or self.busy:
            return
        self.preview.clear()
        self.detail_label.setText("正在核对所选文件并恢复原件…")

        def operation():
            material = self.store.relink(material_id, path)
            return material, *self._load_rows()

        def completed(result):
            material, rows, names, notice = result
            self._show_rows(rows, names, notice, material.material_id)

        self._submit(operation, completed, "正在重新关联；资料记录将保持不变…")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The other entry can change the shared records while this page is hidden.
        self.refresh_materials()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._plan_signal is not None and self._plan_service is not None:
            try:
                self._plan_service.changed.disconnect(self._plan_signal)
            except (RuntimeError, TypeError):
                pass
        self._plan_signal = None
        self._plan_service = None
        self.store.close()
        self._timer.stop()
        if self._future is not None:
            self._future.cancel()
        self._future, self._completed = None, None
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._clear_rows()
        self.preview.clear()
        self.detail_label.clear()
        self.status.clear()
        self._update_actions()
        self.setEnabled(False)

    def closeEvent(self, event) -> None:
        self.dispose()
        super().closeEvent(event)


class PlanCompletionPage(QWidget):
    """Account-scoped plan completion view.

    The former materials table mixed local file operations with plan progress.
    This page is deliberately a plan index: files remain attached to plans in
    the plan detail/resource view, while this entry shows progress and the
    canonical knowledge references already present in each plan snapshot.
    ``open_plan`` is the single navigation contract used by the shell.
    """

    open_plan = Signal(str)
    refresh_requested = Signal()

    def __init__(self, store=None, parent=None, *, title: str = "计划完成情况"):
        super().__init__(parent)
        self.setObjectName("planCompletionPage")
        self.setStyleSheet("""
        QWidget#planCompletionPage { background:#fff8fb; }
        QFrame#completionHero, QFrame#completionCard {
          background:#fffdfd; border:1px solid #ead7df; border-radius:16px;
        }
        QFrame#completionHero { background:#fff4f8; border-color:#efc7d7; }
        QLabel#completionEyebrow { color:#a94f79; font-size:12px; font-weight:800; letter-spacing:1px; }
        QLabel#completionHeading { color:#3b2340; font-size:27px; font-weight:800; }
        QLabel#completionSubtitle, QLabel#completionHint { color:#786572; }
        QLabel#completionCardTitle { color:#8d3f62; font-size:17px; font-weight:750; }
        QLabel#completionMeta { color:#725568; }
        QPushButton { min-height:40px; border:1px solid #e7b8cc; border-radius:10px; padding:0 14px; background:#fffdfd; color:#3b2b3f; font-weight:650; }
        QPushButton:hover { background:#ffeaf3; }
        QPushButton#completionPrimary { color:white; background:#c95f8d; border:0; }
        QProgressBar { min-height:10px; max-height:10px; border:0; border-radius:5px; background:#f4e8ed; text-visible:false; }
        QProgressBar::chunk { border-radius:5px; background:#c95f8d; }
        """)
        self._plan_service = None
        self._plan_signal = None
        self._disposed = False
        self._plan_index = {}
        self._rendered_signature = None
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        hero = QFrame(); hero.setObjectName("completionHero")
        hero_layout = QVBoxLayout(hero); hero_layout.setContentsMargins(20, 16, 20, 16)
        eyebrow = QLabel("账号计划空间"); eyebrow.setObjectName("completionEyebrow"); hero_layout.addWidget(eyebrow)
        heading = QLabel(title); heading.setObjectName("completionHeading"); hero_layout.addWidget(heading)
        subtitle = QLabel("按计划查看任务完成情况和知识地图。资料会跟随对应计划同步到账号，在计划详情中查看。")
        subtitle.setObjectName("completionSubtitle"); subtitle.setWordWrap(True); hero_layout.addWidget(subtitle)
        root.addWidget(hero)
        bar = QHBoxLayout()
        self.status = QLabel("等待账号同步…"); self.status.setObjectName("completionHint"); self.status.setWordWrap(True); bar.addWidget(self.status, 1)
        self.refresh_button = QPushButton("刷新计划"); self.refresh_button.clicked.connect(self.refresh); bar.addWidget(self.refresh_button)
        root.addLayout(bar)
        self.views = QStackedWidget(); root.addWidget(self.views, 1)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget(); self.cards = QVBoxLayout(self.content); self.cards.setContentsMargins(0, 0, 8, 0); self.cards.setSpacing(12); self.cards.addStretch()
        self.scroll.setWidget(self.content); self.views.addWidget(self.scroll)
        self.detail = QFrame(); self.detail.setObjectName("completionCard")
        detail_layout = QVBoxLayout(self.detail); detail_layout.setContentsMargins(16, 14, 16, 16); detail_layout.setSpacing(10)
        detail_header = QHBoxLayout()
        self.back_button = QPushButton("返回计划列表"); self.back_button.clicked.connect(lambda: self.views.setCurrentWidget(self.scroll)); detail_header.addWidget(self.back_button)
        self.selected_title = QLabel("计划完成情况"); self.selected_title.setObjectName("completionCardTitle"); detail_header.addWidget(self.selected_title, 1)
        detail_layout.addLayout(detail_header)
        self.selected_summary = QLabel("选择计划查看知识地图。"); self.selected_summary.setObjectName("completionMeta"); self.selected_summary.setWordWrap(True); detail_layout.addWidget(self.selected_summary)
        self.map_hint = QLabel("节点只依据计划中保存的稳定知识引用分组；连线只依据实际保存的前置任务 ID。已完成表示任务记录状态，不等同于通过掌握度评估。")
        self.map_hint.setObjectName("completionHint"); self.map_hint.setWordWrap(True); detail_layout.addWidget(self.map_hint)
        self.map_view = QGraphicsView(); self.map_view.setObjectName("completionKnowledgeMap"); self.map_view.setAccessibleName("所选计划知识地图"); self.map_view.setMinimumHeight(380)
        self.map_scene = QGraphicsScene(self.map_view); self.map_view.setScene(self.map_scene); detail_layout.addWidget(self.map_view, 1)
        self.views.addWidget(self.detail)

    def bind_plan_service(self, service) -> None:
        # ``LearningWindow._ensure_shared_services`` can be called again after
        # session recovery or a dependency retry.  Rebinding the same service
        # used to tear down and rebuild every completion card even though no
        # account data changed, which was visible as a page flash.
        if service is self._plan_service and self._plan_signal is not None:
            return
        if self._plan_signal is not None and self._plan_service is not None:
            try: self._plan_service.changed.disconnect(self._plan_signal)
            except (RuntimeError, TypeError): pass
        self._plan_service, self._plan_signal = service, None
        changed = getattr(service, "changed", None) if service is not None else None
        if changed is not None and callable(getattr(changed, "connect", None)):
            def on_changed(*_args):
                if not self._disposed: self.refresh()
            changed.connect(on_changed); self._plan_signal = on_changed
        self.refresh()

    def bind_context(self, context) -> None:
        self.bind_plan_service(context.get("plan_service") if isinstance(context, dict) else None)

    @staticmethod
    def _value(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    def _plans(self):
        service = self._plan_service
        if service is None: return ()
        try: return tuple(service.list_plans() or ())
        except Exception: return ()

    def _clear_cards(self):
        while self.cards.count() > 1:
            item = self.cards.takeAt(0); widget = item.widget()
            if widget is not None: widget.deleteLater()

    def refresh(self) -> None:
        if self._disposed: return
        plans = self._plans()
        signature = repr(tuple(plans))
        if signature == self._rendered_signature:
            if plans:
                self.status.setText(f"当前账号共有 {len(plans)} 个计划；点击计划可查看任务详情和知识地图。")
            else:
                self.status.setText("当前账号还没有已同步的计划。完成创建计划后，这里会自动出现计划完成情况。")
            return
        self._rendered_signature = signature
        self._clear_cards()
        if not plans:
            self.status.setText("当前账号还没有已同步的计划。完成创建计划后，这里会自动出现计划完成情况。")
            return
        self.status.setText(f"当前账号共有 {len(plans)} 个计划；点击计划可查看任务详情和知识地图。")
        self._plan_index = {str(self._value(plan, "key", "") or self._value(plan, "plan_id", "")): plan for plan in plans}
        for plan in plans:
            self.cards.insertWidget(self.cards.count() - 1, self._card(plan))

    def _card(self, plan):
        key = str(self._value(plan, "key", "") or self._value(plan, "plan_id", ""))
        title = str(self._value(plan, "title", "未命名计划") or "未命名计划")
        tasks = list(self._value(plan, "tasks", ()) or ())
        total = len(tasks); done = sum(1 for task in tasks if completed_task(task))
        active = sum(1 for task in tasks if str(task.get("status", "")).casefold() in {"active", "learning", "in_progress"})
        percent = int(done * 100 / total) if total else 0
        card = QFrame(); card.setObjectName("completionCard")
        box = QVBoxLayout(card); box.setContentsMargins(18, 16, 18, 16); box.setSpacing(8)
        top = QHBoxLayout(); label = QLabel(title); label.setObjectName("completionCardTitle"); label.setWordWrap(True); top.addWidget(label, 1)
        state = self._value(plan, "status", ""); state_label = QLabel(human_status(state)); state_label.setObjectName("completionMeta"); top.addWidget(state_label)
        box.addLayout(top)
        details = self._value(plan, "details", {}) or {}
        goal = details.get("learning_goal") or details.get("goal") or "尚未填写学习目标"
        meta = QLabel(f"{goal}\n已完成 {done} / {total} 项任务 · {active} 项进行中")
        meta.setObjectName("completionMeta"); meta.setWordWrap(True); box.addWidget(meta)
        progress = QProgressBar(); progress.setRange(0, 100); progress.setValue(percent); progress.setAccessibleName(f"{title}完成进度"); box.addWidget(progress)
        ref_groups = {}
        for task in tasks:
            ref = valid_knowledge_ref(task)
            if not ref:
                continue
            ref_id = str(ref.get("knowledge_point_id") or "")
            if ref_id:
                ref_groups.setdefault(ref_id, []).append(task)
        knowledge_done = knowledge_active = knowledge_pending = 0
        for grouped in ref_groups.values():
            if grouped and all(completed_task(item) for item in grouped):
                knowledge_done += 1
            elif any(str(item.get("status", "")).casefold() in {"active", "learning", "in_progress"} for item in grouped):
                knowledge_active += 1
            else:
                knowledge_pending += 1
        ref_label = QLabel(
            f"知识地图：{len(ref_groups)} 个知识点 · 已完成 {knowledge_done} · 进行中 {knowledge_active} · 待学习 {knowledge_pending}"
            if ref_groups else "知识地图：等待计划中的知识引用"
        )
        ref_label.setObjectName("completionMeta"); box.addWidget(ref_label)
        actions = QHBoxLayout(); actions.addStretch(); button = QPushButton("查看计划完成情况"); button.setObjectName("completionPrimary"); button.setAccessibleName(f"查看{title}计划完成情况"); button.clicked.connect(lambda _checked=False, value=key: self.open_plan(value)); actions.addWidget(button); box.addLayout(actions)
        return card

    @staticmethod
    def _knowledge_key(task, ordinal):
        ref = valid_knowledge_ref(task)
        if ref:
            return ("knowledge", ref["kind"], ref["source"], ref.get("map_revision_id", ""), ref["knowledge_point_id"]), str(ref["knowledge_point_id"])
        task_id = str(task.get("id") or f"unidentified-{ordinal}")
        return ("task", task_id), str(task.get("title") or task.get("name") or "未绑定知识点")

    def open_plan(self, plan_key: str) -> bool:
        """Select a plan and render its known graph inside this page."""
        if not plan_key:
            self.views.setCurrentWidget(self.scroll)
            return True
        plan = getattr(self, "_plan_index", {}).get(str(plan_key))
        if plan is None:
            return False
        self._render_plan_map(plan)
        self.views.setCurrentWidget(self.detail)
        return True

    def _render_plan_map(self, plan):
        title = str(self._value(plan, "title", "未命名计划") or "未命名计划")
        tasks = list(self._value(plan, "tasks", ()) or ())
        done = sum(completed_task(task) for task in tasks)
        running = sum(str(task.get("status", "")).casefold() in {"active", "learning", "in_progress"} for task in tasks)
        self.selected_title.setText(title)
        self.selected_summary.setText(f"任务完成 {done} / {len(tasks)} · 进行中 {running} · 待开始 {max(0, len(tasks)-done-running)}")
        self.map_scene.clear()
        if not tasks:
            self.map_scene.addText("当前计划还没有任务或知识地图节点。")
            self.map_scene.setSceneRect(0, 0, 640, 120)
            return
        groups = {}
        task_to_group = {}
        for ordinal, task in enumerate(tasks):
            key, label = self._knowledge_key(task, ordinal)
            group = groups.setdefault(key, {"label": label, "tasks": [], "ordinal": ordinal})
            group["tasks"].append(task)
            if task.get("id"):
                task_to_group[str(task["id"])] = key
        keys = list(groups)
        edges = set()
        for key, group in groups.items():
            for task in group["tasks"]:
                prerequisites = task.get("prerequisite_task_ids", ())
                if not isinstance(prerequisites, (list, tuple)):
                    continue
                for prerequisite_id in prerequisites:
                    parent_key = task_to_group.get(str(prerequisite_id))
                    if parent_key is not None and parent_key != key:
                        edges.add((parent_key, key))
        # Topological columns use only persisted prerequisite edges. Remaining
        # disconnected nodes keep their stable plan ordering without invented links.
        level = {key: 0 for key in keys}
        for _ in range(len(keys)):
            changed = False
            for parent, child in edges:
                proposed = level[parent] + 1
                if proposed > level[child]: level[child] = proposed; changed = True
            if not changed: break
        column_width, row_height, node_width, node_height = 300, 166, 270, 140
        positions = {}
        rows_by_level = {}
        for key in keys:
            xlevel = level[key]
            row = rows_by_level.get(xlevel, 0); rows_by_level[xlevel] = row + 1
            positions[key] = (30 + xlevel * column_width, 30 + row * row_height)
        for parent, child in edges:
            x1, y1 = positions[parent]; x2, y2 = positions[child]
            start = QPointF(x1 + node_width, y1 + node_height / 2)
            end = QPointF(x2, y2 + node_height / 2)
            self.map_scene.addLine(start.x(), start.y(), end.x(), end.y(), QPen(QColor("#b27a95"), 2))
            arrow = QPolygonF([end, QPointF(end.x()-9, end.y()-5), QPointF(end.x()-9, end.y()+5)])
            self.map_scene.addPolygon(arrow, QPen(QColor("#b27a95")), QBrush(QColor("#b27a95")))
        colors = {"done": ("#f0f9f1", "#65a374"), "active": ("#fff4df", "#d09a40"), "pending": ("#fffdfd", "#d8b7c6")}
        for key in keys:
            group = groups[key]; x, y = positions[key]
            states = [str(task.get("status", "")).casefold() for task in group["tasks"]]
            if all(completed_task(task) for task in group["tasks"]): state, caption = "done", "已完成 · 任务记录"
            elif any(value in {"active", "learning", "in_progress"} for value in states): state, caption = "active", "进行中"
            else: state, caption = "pending", "未完成 · 待学习"
            fill, border = colors[state]
            self.map_scene.addRect(x, y, node_width, node_height, QPen(QColor(border), 2), QBrush(QColor(fill)))
            ref_kind = key[0]
            node_title = f"知识点 {group['label']}" if ref_kind == "knowledge" else f"未绑定知识点 · {group['label']}"
            text = self.map_scene.addText(node_title); text.setDefaultTextColor(QColor("#3b2b3f")); text.setPos(x + 10, y + 8)
            state_text = self.map_scene.addText(f"{caption} · {len(group['tasks'])} 项任务")
            state_text.setDefaultTextColor(QColor("#725568")); state_text.setPos(x + 10, y + 37)
            names = [str(task.get("title") or task.get("name") or "未命名任务") for task in group["tasks"]]
            detail = self.map_scene.addText("\n".join(names[:4]) + (f"\n…另有 {len(names)-4} 项" if len(names) > 4 else ""))
            detail.setDefaultTextColor(QColor("#594756")); detail.setPos(x + 10, y + 62)
            ref = valid_knowledge_ref(group["tasks"][0])
            if ref:
                detail.setToolTip("稳定引用：" + " / ".join(str(value) for value in (ref.get("source"), ref.get("map_revision_id"), ref.get("knowledge_point_id")) if value))
            else:
                detail.setToolTip("此任务快照没有有效知识点引用，因此单独显示，不推测知识点归属。")
        width = max((x + node_width for x, _y in positions.values()), default=640) + 30
        height = max((y + node_height for _x, y in positions.values()), default=180) + 30
        self.map_scene.setSceneRect(0, 0, max(width, 640), max(height, 180))
        self.map_view.fitInView(self.map_scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def dispose(self):
        self._disposed = True
        if self._plan_signal is not None and self._plan_service is not None:
            try: self._plan_service.changed.disconnect(self._plan_signal)
            except (RuntimeError, TypeError): pass
        self._plan_signal = None; self._plan_service = None

    def closeEvent(self, event):
        self.dispose(); super().closeEvent(event)


# Short explicit export for feature wiring; keep the descriptive class name
# available for callers that want to distinguish this from legacy MaterialsPage.
CompletionPage = PlanCompletionPage
