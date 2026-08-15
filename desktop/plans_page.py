"""Native learning-condition editor and canonical plan preview/activation UI."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .api import ApiError, DesktopApi


def flattened_tasks(plan: dict[str, Any]) -> list[dict[str, Any]]:
    execution = plan.get("execution_tasks") or []
    if execution:
        return [dict(item) for item in execution if isinstance(item, dict)]
    result: list[dict[str, Any]] = []
    days = plan.get("days") or []
    for day in days:
        if not isinstance(day, dict):
            continue
        for task in day.get("tasks") or []:
            if isinstance(task, dict):
                result.append({**task, "day_date": task.get("day_date") or day.get("date")})
    return result or [dict(item) for item in plan.get("tasks") or [] if isinstance(item, dict)]


class PlansPage(QWidget):
    def __init__(
        self,
        api: DesktopApi,
        open_map: Callable[[str], None] | None = None,
        open_tasks: Callable[[], None] | None = None,
        task_color_menu: Callable[[str], Any] | None = None,
        task_color_accent: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__()
        self.api = api
        self.open_map_callback = open_map
        self.open_tasks_callback = open_tasks
        self.task_color_menu_factory = task_color_menu
        self.task_color_accent = task_color_accent
        self.current_preview: dict[str, Any] = {}
        self.current_receipt: dict[str, Any] = {}
        self._color_workspace_id = ""
        self._active_color_menu = None
        self._build()
        self._apply_policy()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(38, 32, 38, 32)
        heading = QLabel("计划详情")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        subtitle = QLabel(
            "开始日期、截止日期和学习日保存在本地回执中；每日容量由“总预计时长 ÷ 可学习日”自动计算，不能单独手改。"
            "先预览，明确确认后才激活计划。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        workspace_row = QHBoxLayout()
        back = QPushButton("返回我的计划")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self.open_tasks)
        workspace_row.addWidget(back)
        workspace_row.addWidget(QLabel("学习任务"))
        self.workspace = QComboBox()
        self.workspace.currentIndexChanged.connect(self.load_workspace)
        workspace_row.addWidget(self.workspace, 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_workspaces)
        workspace_row.addWidget(refresh)
        self.map_button = QPushButton("查看本任务知识地图")
        self.map_button.setEnabled(False)
        self.map_button.clicked.connect(self.open_map)
        workspace_row.addWidget(self.map_button)
        self.color_button = QToolButton()
        self.color_button.setText("●")
        self.color_button.setFixedSize(36, 36)
        self.color_button.setToolTip("调整任务卡片颜色")
        self.color_button.setEnabled(False)
        self.color_button.clicked.connect(self._open_color_menu)
        self.color_button.setStyleSheet(
            "QToolButton { color: #4b896f; font-size: 25px; border: 0; background: transparent; padding: 0; }"
            "QToolButton:hover { background: #edf5f1; border-radius: 18px; }"
        )
        workspace_row.addWidget(self.color_button)
        layout.addLayout(workspace_row)

        form = QFrame()
        form.setObjectName("explanationCard")
        grid = QGridLayout(form)
        self.goal = QLineEdit()
        self.goal.setPlaceholderText("你希望学会什么")
        self.level = QComboBox()
        self.level.addItem("入门", "beginner")
        self.level.addItem("有一些基础", "intermediate")
        self.level.addItem("进阶", "advanced")
        self.style = QComboBox()
        self.style.addItem("结构化讲解与练习", "structured")
        self.style.addItem("示例优先", "example_first")
        self.style.addItem("练习优先", "practice_first")
        self.start_date = QDateEdit(QDate.currentDate())
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("yyyy-MM-dd")
        self.deadline = QDateEdit(QDate.currentDate().addDays(30))
        self.deadline.setCalendarPopup(True)
        self.deadline.setDisplayFormat("yyyy-MM-dd")
        self.allocation = QComboBox()
        self.allocation.addItem("系统按总任务量与学习日自动计算", "system")
        self.allocation.setEnabled(False)
        self.allocation.setToolTip("每日容量由总预计时长除以可学习日自动计算。请调整开始/截止日期或学习日。")
        self.daily_minutes = QSpinBox()
        self.daily_minutes.setSuffix(" 分钟/天")
        self.daily_minutes.setReadOnly(True)
        self.daily_minutes.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.daily_minutes.setToolTip("只读：由计划总预计时长和可学习日自动计算。")
        self.review_rounds = QSpinBox()
        self.review_rounds.setSuffix(" 轮")
        self.interests = QLineEdit()
        self.interests.setPlaceholderText("可选；只使用你明确填写的兴趣生成类比")
        self.discover_resources = QCheckBox("允许单独发现网络学习资源（不会改写知识地图）")

        grid.addWidget(QLabel("学习目标"), 0, 0)
        grid.addWidget(self.goal, 0, 1, 1, 3)
        grid.addWidget(QLabel("当前基础"), 1, 0)
        grid.addWidget(self.level, 1, 1)
        grid.addWidget(QLabel("偏好方式"), 1, 2)
        grid.addWidget(self.style, 1, 3)
        grid.addWidget(QLabel("开始日期"), 2, 0)
        grid.addWidget(self.start_date, 2, 1)
        grid.addWidget(QLabel("截止日期"), 2, 2)
        grid.addWidget(self.deadline, 2, 3)
        grid.addWidget(QLabel("容量方式"), 3, 0)
        grid.addWidget(self.allocation, 3, 1)
        grid.addWidget(QLabel("每日容量（自动）"), 3, 2)
        grid.addWidget(self.daily_minutes, 3, 3)
        grid.addWidget(QLabel("复习轮次"), 4, 0)
        grid.addWidget(self.review_rounds, 4, 1)
        grid.addWidget(QLabel("兴趣档案"), 4, 2)
        grid.addWidget(self.interests, 4, 3)

        weekday_box = QHBoxLayout()
        self.weekdays: list[QCheckBox] = []
        for index, label in enumerate(("周一", "周二", "周三", "周四", "周五", "周六", "周日")):
            checkbox = QCheckBox(label)
            checkbox.setChecked(index < 5)
            self.weekdays.append(checkbox)
            weekday_box.addWidget(checkbox)
        grid.addWidget(QLabel("学习星期"), 5, 0)
        grid.addLayout(weekday_box, 5, 1, 1, 3)
        grid.addWidget(self.discover_resources, 6, 1, 1, 3)

        self.materials = QListWidget()
        self.materials.setMaximumHeight(105)
        grid.addWidget(QLabel("纳入资料"), 7, 0)
        grid.addWidget(self.materials, 7, 1, 1, 3)

        actions = QHBoxLayout()
        save = QPushButton("保存学习条件")
        save.clicked.connect(self.save_conditions)
        actions.addWidget(save)
        preview = QPushButton("生成计划预览")
        preview.setObjectName("primaryButton")
        preview.clicked.connect(self.preview_plan)
        actions.addWidget(preview)
        self.activate_button = QPushButton("确认并激活")
        self.activate_button.setEnabled(False)
        self.activate_button.clicked.connect(self.activate_plan)
        actions.addWidget(self.activate_button)
        grid.addLayout(actions, 8, 1, 1, 3)
        layout.addWidget(form)

        self.status = QLabel("请选择学习任务。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        insight_row = QHBoxLayout()
        calendar_card = QFrame()
        calendar_card.setObjectName("explanationCard")
        calendar_layout = QVBoxLayout(calendar_card)
        calendar_layout.addWidget(QLabel("计划日历"))
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setMaximumHeight(190)
        calendar_layout.addWidget(self.calendar)
        insight_row.addWidget(calendar_card, 1)

        chart_card = QFrame()
        chart_card.setObjectName("explanationCard")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.addWidget(QLabel("计划与实际"))
        self.planned_actual_label = QLabel("还没有计划数据。")
        chart_layout.addWidget(self.planned_actual_label)
        self.planned_bar = QProgressBar()
        self.planned_bar.setFormat("计划 %v 分钟")
        chart_layout.addWidget(self.planned_bar)
        self.actual_bar = QProgressBar()
        self.actual_bar.setFormat("实际 %v 分钟")
        chart_layout.addWidget(self.actual_bar)
        chart_layout.addWidget(QLabel("任务状态"))
        self.state_chart = QLabel("计划中 0 · 进行中 0 · 已完成 0")
        chart_layout.addWidget(self.state_chart)
        insight_row.addWidget(chart_card, 1)
        layout.addLayout(insight_row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["日期", "任务", "阶段", "类型", "预计", "实际", "状态"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

    def open_map(self) -> None:
        workspace_id = str(self.workspace.currentData() or "")
        if workspace_id and self.open_map_callback:
            self.open_map_callback(workspace_id)

    def open_tasks(self) -> None:
        if self.open_tasks_callback:
            self.open_tasks_callback()

    def select_workspace(self, workspace_id: str) -> None:
        index = self.workspace.findData(workspace_id)
        if index >= 0:
            self.workspace.setCurrentIndex(index)

    def _apply_policy(self) -> None:
        try:
            scheduling = self.api.runtime_policy().get("scheduling", {})
        except ApiError:
            scheduling = {}
        minimum = int(scheduling.get("minimum_daily_minutes") or 1)
        maximum = int(scheduling.get("maximum_daily_minutes") or minimum)
        default = int(scheduling.get("default_daily_minutes") or minimum)
        self.daily_minutes.setRange(minimum, maximum)
        self.daily_minutes.setValue(default)
        review_min = int(scheduling.get("minimum_review_rounds") or 1)
        review_max = int(scheduling.get("maximum_review_rounds") or review_min)
        self.review_rounds.setRange(review_min, review_max)
        self.review_rounds.setValue(int(scheduling.get("default_review_rounds") or review_min))

    def refresh_workspaces(self) -> None:
        selected = str(self.workspace.currentData() or "")
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
        if selected:
            index = self.workspace.findData(selected)
            if index >= 0:
                self.workspace.setCurrentIndex(index)
        self.workspace.blockSignals(False)
        self.load_workspace()

    def load_workspace(self, _index: int = -1) -> None:
        workspace_id = str(self.workspace.currentData() or "")
        self.map_button.setEnabled(bool(workspace_id and self.open_map_callback))
        self._refresh_color_button(workspace_id)
        if not workspace_id:
            self.materials.clear()
            self.table.setRowCount(0)
            return
        try:
            receipt = self.api.receipt(workspace_id)
        except ApiError as exc:
            receipt = {} if exc.status == 404 else {}
            if exc.status != 404:
                self.status.setText(f"读取学习条件失败：{exc.code}：{exc.message}")
        self.current_receipt = dict(receipt)
        self._fill_receipt(receipt)
        self._fill_materials(workspace_id, set(str(value) for value in receipt.get("material_ids") or []))
        self.load_current_plan()

    def _refresh_color_button(self, workspace_id: str) -> None:
        enabled = bool(workspace_id and self.task_color_menu_factory)
        self._color_workspace_id = workspace_id if enabled else ""
        self.color_button.setEnabled(enabled)
        if not enabled:
            self._active_color_menu = None
            return
        accent = self.task_color_accent(workspace_id) if self.task_color_accent else "#4b896f"
        self.color_button.setStyleSheet(
            f"QToolButton {{ color: {accent}; font-size: 25px; border: 0; background: transparent; padding: 0; }}"
            "QToolButton:hover { background: #edf5f1; border-radius: 18px; }"
        )

    def _open_color_menu(self) -> None:
        if not self._color_workspace_id or not self.task_color_menu_factory:
            return
        self._active_color_menu = self.task_color_menu_factory(self._color_workspace_id)
        anchor = self.color_button.mapToGlobal(QPoint(0, self.color_button.height()))
        self._active_color_menu.popup(anchor)

    def _fill_receipt(self, receipt: dict[str, Any]) -> None:
        if not receipt:
            return
        self.goal.setText(str(receipt.get("learning_goal") or ""))
        for combo, value in ((self.level, receipt.get("current_level")), (self.style, receipt.get("preferred_learning_style"))):
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)
        for widget, value in ((self.start_date, receipt.get("start_date")), (self.deadline, receipt.get("deadline"))):
            parsed = QDate.fromString(str(value or ""), "yyyy-MM-dd")
            if parsed.isValid():
                widget.setDate(parsed)
        selected_weekdays = {int(value) for value in receipt.get("study_weekdays") or []}
        for index, checkbox in enumerate(self.weekdays):
            checkbox.setChecked(index in selected_weekdays)
        if receipt.get("daily_task_minutes") is not None:
            self.daily_minutes.setValue(int(receipt["daily_task_minutes"]))
        allocation = receipt.get("allocation") or {}
        recommended = allocation.get("recommended_minutes_per_day")
        if recommended is not None:
            self.daily_minutes.setValue(int(recommended))
        if receipt.get("review_rounds") is not None:
            self.review_rounds.setValue(int(receipt["review_rounds"]))
        self.interests.setText(str(receipt.get("learner_interests") or ""))
        self.discover_resources.setChecked(bool(receipt.get("discover_external_resources", True)))

    def _fill_materials(self, workspace_id: str, selected: set[str]) -> None:
        self.materials.clear()
        try:
            materials = self.api.materials(workspace_id)
        except ApiError:
            materials = []
        for material in materials:
            if str(material.get("status")) != "ready":
                continue
            material_id = str(material.get("id") or "")
            item = QListWidgetItem(str(material.get("original_filename") or material_id))
            item.setData(Qt.ItemDataRole.UserRole, material_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if not selected or material_id in selected else Qt.CheckState.Unchecked
            )
            self.materials.addItem(item)

    def _selected_material_ids(self) -> list[str]:
        return [
            str(self.materials.item(index).data(Qt.ItemDataRole.UserRole) or "")
            for index in range(self.materials.count())
            if self.materials.item(index).checkState() == Qt.CheckState.Checked
        ]

    def save_conditions(self) -> bool:
        workspace_id = str(self.workspace.currentData() or "")
        weekdays = [index for index, item in enumerate(self.weekdays) if item.isChecked()]
        material_ids = self._selected_material_ids()
        if not workspace_id or not self.goal.text().strip() or not weekdays or not material_ids:
            self.status.setText("学习任务、学习目标、至少一个学习日和至少一份资料都是必填项。")
            return False
        payload = {
            **self.current_receipt,
            "learning_goal": self.goal.text().strip(),
            "current_level": str(self.level.currentData()),
            "preferred_learning_style": str(self.style.currentData()),
            "start_date": self.start_date.date().toString("yyyy-MM-dd"),
            "deadline": self.deadline.date().toString("yyyy-MM-dd"),
            "allocation_mode": "system",
            "daily_task_minutes": self.daily_minutes.value(),
            "review_rounds": self.review_rounds.value(),
            "study_weekdays": weekdays,
            "fixed_rest_weekdays": [value for value in range(7) if value not in weekdays],
            "material_ids": material_ids,
            "learner_interests": self.interests.text().strip(),
            "discover_external_resources": self.discover_resources.isChecked(),
        }
        try:
            self.current_receipt = self.api.save_receipt(workspace_id, payload)
        except ApiError as exc:
            self.status.setText(f"保存失败：{exc.code}：{exc.message}")
            return False
        allocation = self.current_receipt.get("allocation") or {}
        recommended = allocation.get("recommended_minutes_per_day")
        if recommended is not None:
            self.daily_minutes.setValue(int(recommended))
        self.status.setText(
            "学习条件已保存。"
            + (f" 系统按总预计时长和可学习日计算每日 {recommended} 分钟。" if recommended else " 生成预览后会显示自动计算的每日容量。")
        )
        return True

    def preview_plan(self) -> None:
        if not self.save_conditions():
            return
        workspace_id = str(self.workspace.currentData() or "")
        try:
            self.current_preview = self.api.preview_mastery_plan(workspace_id)
        except ApiError as exc:
            self.current_preview = {}
            self.activate_button.setEnabled(False)
            self.status.setText(f"计划预览失败：{exc.code}：{exc.message}")
            return
        self._render_plan(self.current_preview)
        allocation = self.current_preview.get("allocation") or {}
        recommended = allocation.get("recommended_minutes_per_day")
        if recommended is None:
            recommended = self.current_preview.get("recommended_minutes_per_day")
        if recommended is not None:
            self.daily_minutes.setValue(int(recommended))
        blockers = self.current_preview.get("activation_blockers") or []
        extension = self.current_preview.get("extension_draft") or {}
        self.activate_button.setEnabled(not blockers)
        self.status.setText(
            f"预览已生成：预计 {self.current_preview.get('total_estimated_task_minutes') or 0} 分钟；"
            f"任务 {len(flattened_tasks(self.current_preview))} 个。"
            + (f" 需要顺延至 {extension.get('expected_completion_date')}。" if extension.get("required") else " 容量充足。")
            + (f" 仍有 {len(blockers)} 份材料需要确认，暂不能激活。" if blockers else "")
            + (f" 每日容量自动计算为 {recommended} 分钟。" if recommended is not None else "")
        )

    def activate_plan(self) -> None:
        workspace_id = str(self.workspace.currentData() or "")
        plan_id = str(self.current_preview.get("preview_id") or self.current_preview.get("plan_id") or "")
        if not workspace_id or not plan_id:
            self.status.setText("请先生成计划预览。")
            return
        extension = self.current_preview.get("extension_draft") or {}
        confirm_extension = False
        if extension.get("requires_confirmation"):
            answer = QMessageBox.question(
                self,
                "确认顺延计划",
                f"原截止日：{extension.get('original_deadline')}\n"
                f"预计完成日：{extension.get('expected_completion_date')}\n\n是否确认顺延并激活？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            confirm_extension = True
        try:
            activated = self.api.activate_mastery_plan(
                workspace_id,
                plan_id,
                confirm_extension=confirm_extension,
            )
        except ApiError as exc:
            self.status.setText(f"激活失败：{exc.code}：{exc.message}")
            return
        self.current_preview = {}
        self.activate_button.setEnabled(False)
        self._render_plan(activated)
        self.status.setText(f"计划已激活：{activated.get('execution_task_count') or len(flattened_tasks(activated))} 个执行任务。")

    def load_current_plan(self) -> None:
        workspace_id = str(self.workspace.currentData() or "")
        if not workspace_id:
            return
        try:
            plan = self.api.current_mastery_plan(workspace_id)
        except ApiError as canonical_error:
            try:
                plan = self.api.current_legacy_plan(workspace_id)
            except ApiError:
                self.table.setRowCount(0)
                self.status.setText("学习条件可以编辑；当前没有已激活计划。" if canonical_error.status == 404 else canonical_error.message)
                return
        self._render_plan(plan)
        self.status.setText(f"当前计划：{plan.get('status') or 'active'}；任务 {len(flattened_tasks(plan))} 个。")

    def _render_plan(self, plan: dict[str, Any]) -> None:
        tasks = flattened_tasks(plan)
        dates = []
        for task in tasks:
            value = str(task.get("day_date") or task.get("date") or "")
            date = QDate.fromString(value, "yyyy-MM-dd")
            if date.isValid():
                dates.append(date)
                day_format = QTextCharFormat()
                day_format.setBackground(QColor("#f4bfd0"))
                day_format.setForeground(QColor("#412b39"))
                self.calendar.setDateTextFormat(date, day_format)
        if dates:
            first = min(dates, key=lambda value: value.toJulianDay())
            last = max(dates, key=lambda value: value.toJulianDay())
            self.calendar.setDateRange(first, last)
            self.calendar.setSelectedDate(first)
        planned = 0
        actual = 0
        state_counts = {"planned": 0, "in_progress": 0, "completed": 0}
        for task in tasks:
            planned += int(task.get("estimated_task_minutes") or task.get("planned_task_minutes") or 0)
            actual += int(task.get("actual_elapsed_minutes") or 0)
            status = str(task.get("status") or "planned").casefold()
            if status in {"complete", "completed", "done"}:
                state_counts["completed"] += 1
            elif status in {"active", "learning", "in_progress"}:
                state_counts["in_progress"] += 1
            else:
                state_counts["planned"] += 1
        maximum = max(planned, actual, 1)
        self.planned_bar.setRange(0, maximum)
        self.planned_bar.setValue(planned)
        self.actual_bar.setRange(0, maximum)
        self.actual_bar.setValue(min(actual, maximum))
        self.planned_actual_label.setText(f"计划 {planned} 分钟 · 实际 {actual} 分钟")
        self.state_chart.setText(
            f"计划中 {state_counts['planned']} · 进行中 {state_counts['in_progress']} · 已完成 {state_counts['completed']}"
        )
        self.table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            estimated = task.get("estimated_task_minutes")
            if estimated is None:
                estimated = task.get("planned_task_minutes")
            values = (
                task.get("day_date") or task.get("date") or "—",
                task.get("title") or task.get("name") or "未命名任务",
                task.get("learning_phase") or "—",
                task.get("task_type") or "—",
                f"{estimated or 0} 分钟",
                f"{task.get('actual_elapsed_minutes') or 0} 分钟",
                task.get("status") or ("预览" if plan.get("status") == "preview" else "planned"),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
