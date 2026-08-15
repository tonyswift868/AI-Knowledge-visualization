"""Native material import and conversion-status page."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .api import ApiError, DesktopApi


def human_size(size_bytes: object) -> str:
    try:
        value = max(0, int(size_bytes or 0))
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


class MaterialsPage(QWidget):
    def __init__(
        self,
        api: DesktopApi,
        on_workspace_changed: Callable[[], None] | None = None,
        on_open_plans: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.api = api
        self.on_workspace_changed = on_workspace_changed
        self.on_open_plans = on_open_plans
        self._include_archived_materials = False
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(38, 32, 38, 32)
        heading = QLabel("资料管理")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        subtitle = QLabel(
            "把资料加入当前计划后，系统会保留原文件、解析状态和证据位置。"
            "支持 PDF、Word、PowerPoint、纯文本、Markdown、CSV/JSON，以及常见音频；视频在当前版本暂不解析。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("学习任务"))
        self.workspace = QComboBox()
        self.workspace.setMinimumWidth(380)
        self.workspace.currentIndexChanged.connect(self._workspace_changed)
        controls.addWidget(self.workspace, 1)
        self.archived_toggle = QCheckBox("显示已归档资料")
        self.archived_toggle.toggled.connect(self._toggle_archived_materials)
        controls.addWidget(self.archived_toggle)
        upload = QPushButton("添加资料")
        upload.setObjectName("primaryButton")
        upload.clicked.connect(self.choose_files)
        controls.addWidget(upload)
        capabilities = QPushButton("支持的格式")
        capabilities.clicked.connect(self.show_capabilities)
        controls.addWidget(capabilities)
        layout.addLayout(controls)

        self.status = QLabel("请选择一个计划，再添加资料。")
        self.status.setWordWrap(True)
        self.status.setObjectName("inlineStatus")
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["文件", "状态", "类型", "大小", "分段", "解析器", "操作"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

    def refresh_workspaces(self, preferred_id: str = "") -> None:
        current = preferred_id or str(self.workspace.currentData() or "")
        try:
            items = self.api.active_workspaces().get("items", [])
        except ApiError as exc:
            self.status.setText(f"读取任务失败：{exc.code}：{exc.message}")
            return
        self.workspace.blockSignals(True)
        self.workspace.clear()
        for item in items:
            workspace_id = str(item.get("workspace_id") or "")
            if workspace_id:
                self.workspace.addItem(str(item.get("title") or workspace_id), workspace_id)
        if current:
            index = self.workspace.findData(current)
            if index >= 0:
                self.workspace.setCurrentIndex(index)
        self.workspace.blockSignals(False)
        self._workspace_changed()

    def _workspace_changed(self, _index: int = -1) -> None:
        self.refresh_materials()

    def _toggle_archived_materials(self, checked: bool) -> None:
        self._include_archived_materials = bool(checked)
        self.refresh_materials()

    def refresh_materials(self, _index: int = -1) -> None:
        workspace_id = str(self.workspace.currentData() or "")
        if not workspace_id:
            self.table.setRowCount(0)
            return
        try:
            try:
                items = self.api.materials(
                    workspace_id, include_archived=self._include_archived_materials
                )
            except TypeError:
                # Keep compatibility with small fake clients used by native UI tests.
                items = self.api.materials(workspace_id)
        except ApiError as exc:
            if exc.status == 404:
                items = []
            else:
                self.status.setText(f"读取资料失败：{exc.code}：{exc.message}")
                return
        self._set_materials(items)
        self.status.setText(f"当前计划共有 {len(items)} 份资料。可归档资料，之后仍可恢复；删除前会再次确认。")

    def _set_materials(self, items: list[dict]) -> None:
        self.table.setRowCount(len(items))
        status_labels = {
            "ready": "可使用",
            "failed": "解析失败",
            "processing": "处理中",
            "archived": "已归档",
        }
        for row, item in enumerate(items):
            material_id = str(item.get("id") or "")
            archived = bool(item.get("archived")) or str(item.get("status") or "").casefold() == "archived"
            values = (
                item.get("original_filename") or "未命名资料",
                status_labels.get(str(item.get("status") or "").casefold(), str(item.get("status") or "未知")),
                item.get("media_type") or "文件",
                human_size(item.get("size_bytes")),
                item.get("chunk_count") if item.get("chunk_count") is not None else "—",
                item.get("parser_name") or "自动识别",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(4, 2, 4, 2)
            archive = QPushButton("恢复" if archived else "归档")
            archive.setToolTip("归档不会删除资料；恢复后可以再次加入计划。")
            archive.clicked.connect(
                lambda _checked=False, selected=material_id, was_archived=archived: self._archive_material(
                    selected, not was_archived
                )
            )
            action_layout.addWidget(archive)
            delete = QPushButton("永久删除")
            delete.setToolTip("不可逆；仅在服务端确认资料未被计划或知识地图引用时执行。")
            delete.clicked.connect(lambda _checked=False, selected=material_id: self._delete_material(selected))
            action_layout.addWidget(delete)
            actions.setProperty("workspace_id", str(self.workspace.currentData() or ""))
            self.table.setCellWidget(row, 6, actions)

    def _archive_material(self, material_id: str, archived: bool) -> None:
        if not material_id:
            return
        workspace_id = str(self.workspace.currentData() or "")
        try:
            self.api.archive_material(workspace_id, material_id, archived=archived)
        except ApiError as exc:
            self.status.setText(f"{'归档' if archived else '恢复'}失败：{exc.code}：{exc.message}")
            return
        self.status.setText(f"资料已{'归档' if archived else '恢复'}；计划不会被静默激活。")
        self.refresh_materials()
        if self.on_workspace_changed:
            self.on_workspace_changed()

    def _delete_material(self, material_id: str) -> None:
        if not material_id:
            return
        if QMessageBox.question(
            self,
            "确认永久删除",
            "这会永久删除资料及其解析内容，且不能从界面撤销。\n"
            "服务端会先检查计划和知识地图引用；若仍被引用，请改用“归档”。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        workspace_id = str(self.workspace.currentData() or "")
        try:
            self.api.delete_material(workspace_id, material_id)
        except ApiError as exc:
            self.status.setText(f"永久删除失败：{exc.code}：{exc.message}。如资料仍被引用，请改用“归档”。")
            return
        self.status.setText("资料已永久删除。")
        self.refresh_materials()
        if self.on_workspace_changed:
            self.on_workspace_changed()

    def choose_files(self) -> None:
        workspace_id = str(self.workspace.currentData() or "")
        if not workspace_id:
            QMessageBox.information(self, "先选择计划", "请先在“我的计划”打开一个计划，再添加资料。")
            if self.on_open_plans:
                self.on_open_plans()
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择学习资料")
        if not paths:
            return
        failures: list[str] = []
        for index, raw_path in enumerate(paths, 1):
            path = Path(raw_path)
            self.status.setText(f"正在处理 {index}/{len(paths)}：{path.name}")
            QApplication.processEvents()
            try:
                self.api.upload_material(workspace_id, path)
            except (ApiError, OSError) as exc:
                message = exc.message if isinstance(exc, ApiError) else str(exc)
                failures.append(f"{path.name}：{message}")
        self.refresh_workspaces(workspace_id)
        if failures:
            self.status.setText(f"完成，但有 {len(failures)} 份资料未被接受。")
            QMessageBox.warning(self, "部分资料未纳入", "\n".join(failures))
        else:
            self.status.setText(f"{len(paths)} 份资料已处理完成。")
            try:
                self.api.current_mastery_plan(workspace_id)
            except ApiError as exc:
                if exc.status == 404:
                    return
            answer = QMessageBox.question(
                self,
                "计划需要重新确认",
                "资料已加入当前计划。为了避免改变学习节奏，系统不会自动激活或重排计划；是否打开“创建计划”重新预览并确认日期？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes and self.on_open_plans:
                self.on_open_plans()

    def show_capabilities(self) -> None:
        try:
            payload = self.api.material_capabilities()
        except ApiError as exc:
            QMessageBox.warning(self, "无法读取能力", f"{exc.code}：{exc.message}")
            return
        registry = payload.get("registry") if isinstance(payload.get("registry"), dict) else payload
        adapters = registry.get("capabilities") or registry.get("adapters") or registry.get("items") or []
        lines = [
            "当前运行环境登记的材料能力",
            "可直接加入计划的常见格式：PDF、DOCX、PPTX、TXT、Markdown、CSV、JSON；音频会保留转写状态。",
            "视频当前仅作为文件保留，暂不自动解析。",
            "",
        ]
        if isinstance(adapters, dict):
            adapters = list(adapters.values())
        for item in adapters:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("adapter") or item.get("id") or "未命名适配器"
            status = item.get("status") or ("可用" if item.get("available", True) else "不可用")
            formats = item.get("extensions") or item.get("formats") or item.get("mime_types") or []
            if isinstance(formats, str):
                formats = [formats]
            lines.append(f"{name} · {status}")
            if formats:
                lines.append("  " + "、".join(str(value) for value in formats))
            reason = item.get("reason") or item.get("limitations")
            if reason:
                lines.append("  限制：" + str(reason))
        if len(lines) == 2:
            lines.append("能力接口没有返回可展示的适配器。")
        dialog = QDialog(self)
        dialog.setWindowTitle("支持的材料格式")
        dialog_layout = QVBoxLayout(dialog)
        title = QLabel("能力注册表")
        title.setObjectName("pageHeading")
        dialog_layout.addWidget(title)
        detail = QPlainTextEdit("\n".join(lines))
        detail.setReadOnly(True)
        detail.setMinimumSize(720, 460)
        dialog_layout.addWidget(detail)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.accept)
        dialog_layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()
