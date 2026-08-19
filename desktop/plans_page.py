"""Native learning-condition editor and canonical plan preview/activation UI."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date as Date
from typing import Any, Callable, Iterable

from PySide6.QtCore import QDate, QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget,
    QScrollArea,
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
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTableView,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .api import ApiError, DesktopApi


def flattened_tasks(plan: dict[str, Any]) -> list[dict[str, Any]]:
    execution = plan.get("execution_tasks") or []
    if execution:
        return [dict(item) for item in execution if isinstance(item, dict)]
    result: list[dict[str, Any]] = []
    days = plan.get("days") or plan.get("study_days") or plan.get("calendar_days") or []
    for day in days:
        if not isinstance(day, dict):
            continue
        for task in day.get("tasks") or day.get("items") or []:
            if isinstance(task, dict):
                result.append(
                    {
                        **task,
                        "day_date": (
                            task.get("day_date")
                            or task.get("scheduled_date")
                            or task.get("date")
                            or day.get("date")
                            or day.get("day_date")
                        ),
                    }
                )
    return result or [dict(item) for item in plan.get("tasks") or [] if isinstance(item, dict)]


_COMPLETED_TASK_STATES = frozenset({"complete", "completed", "done"})
_ACTIVE_TASK_STATES = frozenset({"active", "learning", "in_progress"})


def _minutes(value: Any) -> float:
    """Parse a non-negative duration without letting malformed legacy data leak into the UI."""

    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _iso_date(value: Any) -> str:
    candidate = str(value or "").strip()[:10]
    try:
        Date.fromisoformat(candidate)
    except (TypeError, ValueError):
        return ""
    return candidate


def _format_minutes(value: float) -> str:
    rounded = round(float(value), 1)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.1f}"


def _empty_day_metrics() -> dict[str, float]:
    return {
        "tasks": 0,
        "planned_minutes": 0.0,
        "measured_actual_minutes": 0.0,
        "completed_tasks": 0,
        "active_tasks": 0,
        "completed_measured_minutes": 0.0,
        "completed_fallback_minutes": 0.0,
        "completed_effort_minutes": 0.0,
        "unscheduled_minutes": 0.0,
    }


def task_time_metrics(task: dict[str, Any]) -> dict[str, Any]:
    """Project one task's planned, measured, and honest completed effort.

    ``actual_elapsed_minutes`` is a session measurement and always wins when
    it is positive.  A completed task with no measurement is still useful in
    historical plans, so its estimate is exposed as a clearly labelled
    ``completed_fallback_minutes`` value rather than being presented as actual
    elapsed time.  Incomplete tasks never receive that fallback.
    """

    estimated = _minutes(task.get("estimated_task_minutes") or task.get("planned_task_minutes"))
    measured_actual = _minutes(task.get("actual_elapsed_minutes"))
    status = str(task.get("status") or "planned").strip().casefold()
    completed = status in _COMPLETED_TASK_STATES
    active = status in _ACTIVE_TASK_STATES
    completed_measured = measured_actual if completed and measured_actual else 0.0
    completed_fallback = estimated if completed and not measured_actual else 0.0
    return {
        "estimated_minutes": estimated,
        "measured_actual_minutes": measured_actual,
        "completed": completed,
        "active": active,
        "completed_measured_minutes": completed_measured,
        "completed_fallback_minutes": completed_fallback,
        "completed_effort_minutes": completed_measured or completed_fallback,
        "scheduled_date": _iso_date(
            task.get("day_date")
            or task.get("scheduled_date")
            or task.get("original_day_date")
            or task.get("date")
        ),
        "completed_date": _iso_date(
            task.get("completed_on")
            or task.get("completed_at_local_date")
            or task.get("completed_at")
        ),
    }


def _day_summary(value: str, bucket: dict[str, float]) -> str:
    actual = _format_minutes(bucket["measured_actual_minutes"])
    effort = _format_minutes(bucket["completed_effort_minutes"])
    summary = (
        f"{value}\n任务 {int(bucket['tasks'])} 项 · 已完成 {int(bucket['completed_tasks'])} 项 · "
        f"进行中 {int(bucket['active_tasks'])} 项\n"
        f"计划 {_format_minutes(bucket['planned_minutes'])} 分钟 · 实际记录 {actual} 分钟\n"
        f"完成任务时长 {effort} 分钟"
    )
    if bucket["completed_measured_minutes"]:
        summary += f"（实测 {_format_minutes(bucket['completed_measured_minutes'])} 分钟）"
    if bucket["completed_fallback_minutes"]:
        summary += (
            f"（完成任务折算时长 {_format_minutes(bucket['completed_fallback_minutes'])} 分钟；"
            "旧记录无实际用时）"
        )
    return summary


def plan_time_metrics(tasks: Iterable[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    """Aggregate plan detail time metrics for the chart and calendar.

    Planned minutes include every task independently from completion effort.
    The calendar's ``effort`` and the selected-day summaries are both derived
    from ``completed_effort_minutes``, ensuring their intensity and wording
    cannot drift apart.
    """

    if isinstance(tasks, dict):
        tasks = flattened_tasks(tasks)

    planned_minutes = 0.0
    measured_actual_minutes = 0.0
    completed_measured_minutes = 0.0
    completed_fallback_minutes = 0.0
    completed_effort_minutes = 0.0
    days: dict[str, dict[str, Any]] = {}

    def bucket_for(value: str) -> dict[str, float]:
        return days.setdefault(value, _empty_day_metrics())

    for raw_task in tasks:
        if not isinstance(raw_task, dict):
            continue
        task = task_time_metrics(raw_task)
        estimated = float(task["estimated_minutes"])
        measured_actual = float(task["measured_actual_minutes"])
        planned_minutes += estimated
        measured_actual_minutes += measured_actual
        completed_measured_minutes += float(task["completed_measured_minutes"])
        completed_fallback_minutes += float(task["completed_fallback_minutes"])
        completed_effort_minutes += float(task["completed_effort_minutes"])

        scheduled_date = str(task["scheduled_date"] or "")
        if scheduled_date:
            bucket = bucket_for(scheduled_date)
            bucket["tasks"] += 1
            bucket["planned_minutes"] += estimated
            bucket["measured_actual_minutes"] += measured_actual
            if task["active"]:
                bucket["active_tasks"] += 1
            if str(raw_task.get("status") or "planned").strip().casefold() == "unscheduled":
                bucket["unscheduled_minutes"] += estimated

        if task["completed"]:
            completion_date = str(task["completed_date"] or scheduled_date)
            if completion_date:
                bucket = bucket_for(completion_date)
                bucket["completed_tasks"] += 1
                bucket["completed_measured_minutes"] += float(task["completed_measured_minutes"])
                bucket["completed_fallback_minutes"] += float(task["completed_fallback_minutes"])
                bucket["completed_effort_minutes"] += float(task["completed_effort_minutes"])

    peak_effort = max(
        (float(bucket["completed_effort_minutes"]) for bucket in days.values()),
        default=0.0,
    )
    for value, bucket in days.items():
        bucket["effort"] = float(bucket["completed_effort_minutes"])
        bucket["ratio"] = (
            float(bucket["completed_effort_minutes"]) / peak_effort if peak_effort else 0.0
        )
        bucket["scheduled"] = float(bucket["planned_minutes"])
        bucket["unscheduled"] = float(bucket["unscheduled_minutes"])
        bucket["has_activity"] = bool(bucket["measured_actual_minutes"])
        bucket["summary"] = _day_summary(value, bucket)

    return {
        "planned_minutes": planned_minutes,
        "measured_actual_minutes": measured_actual_minutes,
        "completed_measured_minutes": completed_measured_minutes,
        "completed_fallback_minutes": completed_fallback_minutes,
        "completed_effort_minutes": completed_effort_minutes,
        "days": days,
    }


class PlanCalendar(QCalendarWidget):
    """Friendly effort calendar with compact circular day markers."""

    def __init__(self) -> None:
        super().__init__()
        self._day_summaries: dict[str, str] = {}
        self._day_metrics: dict[str, dict[str, float]] = {}
        self._calendar_view = self.findChild(QTableView)
        self._calendar_viewport = None
        self._wheel_targets: set[object] = set()
        if self._calendar_view is not None:
            self._calendar_viewport = self._calendar_view.viewport()
        # QCalendarWidget has several child controls.  Filtering all of them
        # keeps a wheel gesture over the header or table from becoming a month
        # navigation command.
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)
            self._wheel_targets.add(child)
        # Do not clamp the view to the first/last planned day.  Users can
        # inspect an empty day and still navigate with the explicit controls.
        self.setMinimumDate(QDate(1, 1, 1))
        self.setMaximumDate(QDate(9999, 12, 31))
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_day_summaries(self, values: dict[str, str]) -> None:
        self._day_summaries = dict(values)

    def set_day_metrics(self, values: dict[str, dict[str, float]]) -> None:
        self._day_metrics = {key: dict(value) for key, value in values.items()}
        self.updateCells()

    def summary_for(self, value: QDate) -> str:
        return self._day_summaries.get(value.toString("yyyy-MM-dd"), "这一天还没有学习记录。")

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        """Let the overview scroll normally without changing months by accident."""

        event.ignore()

    def _outer_scroll_area(self) -> QScrollArea | None:
        """Find the page scroll area without mistaking the calendar table for it."""

        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _forward_wheel(self, event) -> bool:
        """Consume calendar wheel input and move the surrounding page instead."""

        scroll = self._outer_scroll_area()
        if scroll is not None:
            pixel_delta = event.pixelDelta().y()
            angle_delta = event.angleDelta().y()
            delta = pixel_delta or angle_delta
            if delta:
                # Qt reports angle deltas in eighths of a degree (120 per
                # notch).  A modest page-step keeps the gesture predictable
                # while preserving pixel-precise trackpad movement.
                amount = int(delta if pixel_delta else delta / 8)
                if amount == 0:
                    amount = 1 if delta > 0 else -1
                bar = scroll.verticalScrollBar()
                bar.setValue(bar.value() - amount)
        event.accept()
        return True

    def paintCell(self, painter: QPainter, rect, date: QDate) -> None:  # type: ignore[override]
        """Draw one calm circular marker; color intensity means completed minutes."""

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(rect, QColor("#fffafc"))
        key = date.toString("yyyy-MM-dd")
        metric = self._day_metrics.get(key, {})
        effort = float(metric.get("effort") or 0)
        ratio = max(0.0, min(1.0, float(metric.get("ratio") or 0)))
        scheduled = float(metric.get("scheduled") or 0) > 0
        pending = float(metric.get("unscheduled") or 0) > 0
        has_activity = bool(metric.get("has_activity"))

        diameter = max(28, min(rect.width(), rect.height()) - 13)
        circle = rect.adjusted(
            (rect.width() - diameter) // 2,
            (rect.height() - diameter) // 2,
            -(rect.width() - diameter + 1) // 2,
            -(rect.height() - diameter + 1) // 2,
        )
        if effort > 0:
            light = QColor("#f7d8e5")
            strong = QColor("#bd4f7d")
            fill = QColor(
                round(light.red() + (strong.red() - light.red()) * ratio),
                round(light.green() + (strong.green() - light.green()) * ratio),
                round(light.blue() + (strong.blue() - light.blue()) * ratio),
            )
        elif has_activity:
            fill = QColor("#eadff0")
        elif scheduled:
            fill = QColor("#f4eef1")
        else:
            fill = QColor("#fffafc")
        painter.setBrush(fill)
        border = QColor("#d5c4cc")
        if date == QDate.currentDate():
            border = QColor("#43a58f")
        if date == self.selectedDate():
            border = QColor("#b83f74")
        emphasized = date == QDate.currentDate() or date == self.selectedDate()
        painter.setPen(QPen(border, 2 if emphasized else 1))
        painter.drawEllipse(circle)

        if pending:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#d79043"))
            painter.drawEllipse(circle.right() - 6, circle.top() + 2, 6, 6)
        painter.setPen(QColor("#ffffff") if ratio >= 0.58 else QColor("#422c38"))
        font = painter.font()
        font.setBold(bool(effort or date == QDate.currentDate()))
        painter.setFont(font)
        painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(date.day()))
        painter.restore()

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if (
            watched in self._wheel_targets
        ):
            if event.type() == QEvent.Type.Wheel:
                return self._forward_wheel(event)
            if event.type() != QEvent.Type.ToolTip:
                return super().eventFilter(watched, event)
            if self._calendar_view is None or watched is not self._calendar_viewport:
                return super().eventFilter(watched, event)
            index = self._calendar_view.indexAt(event.pos())
            try:
                day = int(index.data())
            except (TypeError, ValueError):
                return super().eventFilter(watched, event)
            year, month = self.yearShown(), self.monthShown()
            if index.row() <= 1 and day >= 20:
                month -= 1
                if month == 0:
                    month, year = 12, year - 1
            elif index.row() >= 4 and day <= 14:
                month += 1
                if month == 13:
                    month, year = 1, year + 1
            value = QDate(year, month, day)
            if value.isValid():
                QToolTip.showText(event.globalPos(), self.summary_for(value), self)
                return True
        return super().eventFilter(watched, event)


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
        self._plan_needs_adjustment = False
        self._color_workspace_id = ""
        self._active_color_menu = None
        self._action_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mentorflow-plan-actions")
        self._action_future: Future[Any] | None = None
        self._action_kind = ""
        self._load_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mentorflow-plan-load")
        self._workspace_list_future: Future[list[dict[str, Any]]] | None = None
        self._workspace_list_token = 0
        self._requested_workspace_id = ""
        self._workspace_load_future: Future[dict[str, Any]] | None = None
        self._workspace_load_token = 0
        self._build()
        self._apply_policy()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._action_executor.shutdown(wait=False, cancel_futures=True)
        self._load_executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    def _build(self) -> None:
        # Plan detail can contain hundreds of execution rows.  Keep the whole
        # page vertically scrollable so the task table is never compressed to
        # a header-only strip on ordinary laptop-height windows.
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("planDetailScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("planDetailContent")
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        scroll.setWidget(content)
        shell.addWidget(scroll)

        self.detail_scroll = scroll
        layout = QVBoxLayout(content)
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
        self.save_button = QPushButton("保存学习条件")
        self.save_button.clicked.connect(self.save_conditions)
        actions.addWidget(self.save_button)
        self.preview_button = QPushButton("生成计划预览")
        self.preview_button.setObjectName("primaryButton")
        self.preview_button.clicked.connect(self.preview_plan)
        actions.addWidget(self.preview_button)
        self.activate_button = QPushButton("确认并激活")
        self.activate_button.setEnabled(False)
        self.activate_button.clicked.connect(self.activate_plan)
        actions.addWidget(self.activate_button)
        grid.addLayout(actions, 8, 1, 1, 3)
        layout.addWidget(form)

        status_row = QHBoxLayout()
        self.status = QLabel("请选择学习任务。")
        self.status.setWordWrap(True)
        status_row.addWidget(self.status, 1)
        self.adjust_button = QPushButton("调整并重新生成预览")
        self.adjust_button.setObjectName("primaryButton")
        self.adjust_button.setToolTip("基于当前知识地图重新生成计划预览；不会直接改动已完成的学习记录。")
        self.adjust_button.setVisible(False)
        self.adjust_button.clicked.connect(self.regenerate_adjusted_plan)
        status_row.addWidget(self.adjust_button)
        layout.addLayout(status_row)

        insight_row = QHBoxLayout()
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
        self.actual_bar.setFormat("已完成任务 %v 分钟")
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
        self.table.setWordWrap(True)
        self.table.setMinimumHeight(240)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table_empty_state = QLabel(
            "当前计划还没有可显示的任务。先生成计划预览并确认激活，"
            "日程明细会在这里按日期显示。"
        )
        self.table_empty_state.setObjectName("inlineStatus")
        self.table_empty_state.setWordWrap(True)
        self.table_empty_state.setVisible(True)
        layout.addWidget(self.table_empty_state)
        layout.addWidget(self.table)
        self._set_table_state(False)

    def open_map(self) -> None:
        workspace_id = str(self.workspace.currentData() or "")
        if workspace_id and self.open_map_callback:
            self.open_map_callback(workspace_id)

    def open_tasks(self) -> None:
        if self.open_tasks_callback:
            self.open_tasks_callback()

    def _set_table_state(self, has_tasks: bool, message: str = "") -> None:
        """Show useful plan detail only when there are rows to reach."""

        visible = bool(has_tasks)
        self.table.setVisible(visible)
        # QTableWidget can keep its header strip painted after a zero-row
        # refresh on some Qt styles. Hide both headers with the table so the
        # detail page has no orphaned date/task labels when it is empty.
        self.table.horizontalHeader().setVisible(visible)
        self.table.verticalHeader().setVisible(visible)
        self.table_empty_state.setVisible(not visible)
        if message:
            self.table_empty_state.setText(message)

    def select_workspace(self, workspace_id: str) -> None:
        # Keep an explicit route target across an in-flight list refresh. The
        # combo may still contain the previous list when a card is clicked.
        requested = str(workspace_id or "")
        self._requested_workspace_id = requested
        index = self.workspace.findData(requested)
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

    def refresh_workspaces(self, preferred_workspace_id: str = "") -> None:
        if preferred_workspace_id:
            self._requested_workspace_id = str(preferred_workspace_id)
        selected = str(
            self._requested_workspace_id
            or self.workspace.currentData()
            or ""
        )
        self._workspace_list_token += 1
        token = self._workspace_list_token
        self.status.setText("正在更新计划列表…")
        self._workspace_list_future = self._load_executor.submit(
            lambda: list(self.api.active_workspaces().get("items", []))
        )
        QTimer.singleShot(40, lambda: self._poll_workspace_list(token, selected))

    def _poll_workspace_list(self, token: int, selected: str) -> None:
        future = self._workspace_list_future
        if token != self._workspace_list_token or future is None:
            return
        if not future.done():
            QTimer.singleShot(40, lambda: self._poll_workspace_list(token, selected))
            return
        self._workspace_list_future = None
        try:
            items = future.result()
        except ApiError as exc:
            self.status.setText(f"读取任务失败：{exc.code}：{exc.message}")
            return
        self.workspace.blockSignals(True)
        self.workspace.clear()
        for item in items:
            workspace_id = str(item.get("workspace_id") or "")
            if workspace_id:
                title = str(item.get("title") or workspace_id)
                self.workspace.addItem(title, workspace_id)
                self.workspace.setItemData(self.workspace.count() - 1, title, Qt.ItemDataRole.ToolTipRole)
        requested = str(self._requested_workspace_id or selected)
        if requested:
            index = self.workspace.findData(requested)
            if index >= 0:
                self.workspace.setCurrentIndex(index)
            elif self._requested_workspace_id:
                # Do not silently open the first plan when the route target is
                # no longer present in the refreshed list.
                self.workspace.setCurrentIndex(-1)
        self._requested_workspace_id = ""
        self.workspace.blockSignals(False)
        self.load_workspace()

    def load_workspace(self, _index: int = -1) -> None:
        workspace_id = str(self.workspace.currentData() or "")
        self.map_button.setEnabled(bool(workspace_id and self.open_map_callback))
        self._refresh_color_button(workspace_id)
        if not workspace_id:
            self.materials.clear()
            self.table.setRowCount(0)
            self._set_table_state(False, "请选择一个学习任务，计划日程明细会显示在这里。")
            self._plan_needs_adjustment = False
            self.adjust_button.setVisible(False)
            return
        self._workspace_load_token += 1
        token = self._workspace_load_token
        self.status.setText("正在加载计划详情…")
        self._workspace_load_future = self._load_executor.submit(
            self._load_workspace_payload,
            workspace_id,
        )
        QTimer.singleShot(40, lambda: self._poll_workspace_payload(token, workspace_id))

    def _load_workspace_payload(self, workspace_id: str) -> dict[str, Any]:
        receipt_error = ""
        try:
            receipt = self.api.receipt(workspace_id)
        except ApiError as exc:
            receipt = {}
            if exc.status != 404:
                receipt_error = f"读取学习条件失败：{exc.code}：{exc.message}"
        try:
            materials = self.api.materials(workspace_id)
        except ApiError:
            materials = []
        plan = None
        plan_error = ""
        try:
            plan = self.api.current_mastery_plan(workspace_id)
        except ApiError as canonical_error:
            try:
                plan = self.api.current_legacy_plan(workspace_id)
            except ApiError:
                plan_error = (
                    "学习条件可以编辑；当前没有已激活计划。"
                    if canonical_error.status == 404
                    else canonical_error.message
                )
        return {
            "receipt": receipt,
            "materials": materials,
            "plan": plan,
            "error": receipt_error or plan_error,
        }

    def _poll_workspace_payload(self, token: int, workspace_id: str) -> None:
        future = self._workspace_load_future
        if token != self._workspace_load_token or future is None:
            return
        if not future.done():
            QTimer.singleShot(40, lambda: self._poll_workspace_payload(token, workspace_id))
            return
        self._workspace_load_future = None
        try:
            payload = future.result()
        except ApiError as exc:
            self.status.setText(f"计划详情加载失败：{exc.code}：{exc.message}")
            return
        receipt = dict(payload.get("receipt") or {})
        self.current_receipt = receipt
        self._fill_receipt(receipt)
        selected = {str(value) for value in receipt.get("material_ids") or []}
        self._fill_material_items(list(payload.get("materials") or []), selected)
        plan = payload.get("plan")
        if isinstance(plan, dict):
            self._render_plan(plan)
            self.status.setText(self._plan_status_text(plan))
        else:
            self.table.setRowCount(0)
            self._set_table_state(
                False,
                str(payload.get("error") or "当前计划还没有可显示的任务。先生成计划预览并确认激活。"),
            )
            self._plan_needs_adjustment = False
            self.adjust_button.setVisible(False)
            self.status.setText(str(payload.get("error") or "学习条件可以编辑；当前没有已激活计划。"))

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
        self._fill_material_items(materials, selected)

    def _fill_material_items(self, materials: list[dict[str, Any]], selected: set[str]) -> None:
        self.materials.clear()
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

    def _conditions_payload(self) -> tuple[str, dict[str, Any]] | None:
        workspace_id = str(self.workspace.currentData() or "")
        weekdays = [index for index, item in enumerate(self.weekdays) if item.isChecked()]
        material_ids = self._selected_material_ids()
        if not workspace_id or not self.goal.text().strip() or not weekdays or not material_ids:
            self.status.setText("学习任务、学习目标、至少一个学习日和至少一份资料都是必填项。")
            return None
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
        return workspace_id, payload

    def _start_plan_action(self, kind: str, work: Callable[[], Any], status: str) -> bool:
        if self._action_future is not None and not self._action_future.done():
            self.status.setText("上一项操作仍在进行，请稍候。")
            return False
        self._action_kind = kind
        self.status.setText(status)
        self.save_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.activate_button.setEnabled(False)
        self.adjust_button.setEnabled(False)
        self.save_button.setText("正在保存…" if kind == "save" else "保存学习条件")
        self.preview_button.setText("正在生成…" if kind == "preview" else "生成计划预览")
        self.activate_button.setText("正在激活…" if kind == "activate" else "确认并激活")
        if kind == "preview" and self._plan_needs_adjustment:
            self.adjust_button.setText("正在重新生成…")
        self._action_future = self._action_executor.submit(work)
        QTimer.singleShot(40, self._poll_plan_action)
        return True

    def _restore_plan_buttons(self) -> None:
        self.save_button.setEnabled(True)
        self.preview_button.setEnabled(True)
        blockers = self.current_preview.get("activation_blockers") or []
        self.activate_button.setEnabled(bool(self.current_preview) and not blockers)
        self.save_button.setText("保存学习条件")
        self.preview_button.setText("生成计划预览")
        self.activate_button.setText("确认并激活")
        self.adjust_button.setEnabled(self._plan_needs_adjustment)
        self.adjust_button.setText("调整并重新生成预览")

    def _poll_plan_action(self) -> None:
        future = self._action_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(40, self._poll_plan_action)
            return
        self._action_future = None
        kind = self._action_kind
        self._action_kind = ""
        try:
            result = future.result()
        except ApiError as exc:
            self.status.setText(f"{'保存' if kind == 'save' else '计划预览' if kind == 'preview' else '激活'}失败：{exc.code}：{exc.message}")
            self._restore_plan_buttons()
            return
        except Exception as exc:  # keep the native page responsive on transport/runtime failures
            action = "保存" if kind == "save" else "计划预览" if kind == "preview" else "激活"
            self.status.setText(f"{action}失败：{str(exc).strip() or '出现未知错误，请重试。'}")
            self._restore_plan_buttons()
            return
        if kind == "save":
            self.current_receipt = dict(result)
            self._show_saved_receipt()
        elif kind == "preview":
            receipt, preview = result
            self.current_receipt = dict(receipt)
            self.current_preview = dict(preview)
            self._show_preview()
        elif kind == "activate":
            activated = dict(result)
            self.current_preview = {}
            self._render_plan(activated)
            activation = activated.get("activation") or {}
            if activation.get("no_op"):
                self.status.setText(
                    str(activation.get("reason") or "这份计划已经激活，无需重复操作。")
                    + " "
                    + str(activation.get("message") or "如需调整，请先重新生成计划预览。")
                )
            else:
                self.status.setText(
                    f"计划已激活：{activated.get('execution_task_count') or len(flattened_tasks(activated))} 个执行任务。"
                )
        self._restore_plan_buttons()

    def _show_saved_receipt(self) -> None:
        allocation = self.current_receipt.get("allocation") or {}
        recommended = allocation.get("recommended_minutes_per_day")
        if recommended is not None:
            self.daily_minutes.setValue(int(recommended))
        self.status.setText(
            "学习条件已保存。"
            + (f" 系统按总预计时长和可学习日计算每日 {recommended} 分钟。" if recommended else " 生成预览后会显示自动计算的每日容量。")
        )

    def save_conditions(self) -> bool:
        collected = self._conditions_payload()
        if collected is None:
            return False
        workspace_id, payload = collected
        return self._start_plan_action(
            "save",
            lambda: self.api.save_receipt(workspace_id, payload),
            "正在保存学习条件…",
        )

    def preview_plan(self) -> None:
        collected = self._conditions_payload()
        if collected is None:
            return
        workspace_id, payload = collected

        def work() -> tuple[dict[str, Any], dict[str, Any]]:
            receipt = self.api.save_receipt(workspace_id, payload)
            return receipt, self.api.preview_mastery_plan(workspace_id)

        self._start_plan_action("preview", work, "正在保存条件并生成计划预览…")

    def regenerate_adjusted_plan(self) -> None:
        """Rebuild a stale plan preview without changing learning progress."""

        if not self._plan_needs_adjustment:
            self.status.setText("当前计划不需要调整。")
            return
        self.status.setText("正在基于当前知识地图重新生成计划预览…")
        self.preview_plan()

    def _show_preview(self) -> None:
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
        self._start_plan_action(
            "activate",
            lambda: self.api.activate_mastery_plan(
                workspace_id,
                plan_id,
                confirm_extension=confirm_extension,
            ),
            "正在激活计划…",
        )

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
                self._set_table_state(
                    False,
                    "学习条件可以编辑；当前没有已激活计划。",
                )
                self._plan_needs_adjustment = False
                self.adjust_button.setVisible(False)
                self.status.setText("学习条件可以编辑；当前没有已激活计划。" if canonical_error.status == 404 else canonical_error.message)
                return
        self._render_plan(plan)
        self.status.setText(self._plan_status_text(plan))

    @staticmethod
    def _plan_status_text(plan: dict[str, Any]) -> str:
        status = str(plan.get("status") or "").strip().casefold()
        if bool(plan.get("stale")) or status in {"stale", "needs_adjustment"}:
            old = plan.get("old_plan_estimate") or {}
            pending = plan.get("pending_plan_estimate") or {}
            old_minutes = int(
                old.get("total_estimated_task_minutes")
                or plan.get("total_estimated_task_minutes")
                or 0
            )
            pending_minutes = int(pending.get("total_estimated_task_minutes") or 0)
            pending_initial = int(pending.get("initial_learning_minutes") or 0)
            pending_reviews = int(pending.get("projected_review_minutes") or 0)
            comparison = (
                f"当前知识地图重新评估为 {pending_minutes} 分钟"
                + (
                    f"（首次学习 {pending_initial} + 复习 {pending_reviews}）"
                    if pending_initial or pending_reviews
                    else ""
                )
                if pending_minutes > 0
                else "需要基于当前知识地图重新生成预览"
            )
            return (
                f"需调整：{comparison}。旧版 {old_minutes} 分钟已过期，"
                "下方旧日程只读保留；确认新预览前不会改动学习进度。"
            )
        total_minutes = int(plan.get("total_estimated_task_minutes") or 0)
        return (
            f"当前计划：{plan.get('status') or 'active'}；"
            f"任务 {len(flattened_tasks(plan))} 个；预计总学习 {total_minutes} 分钟。"
        )

    def _render_plan(self, plan: dict[str, Any]) -> None:
        tasks = flattened_tasks(plan)
        metrics = plan_time_metrics(tasks)
        planned = float(metrics["planned_minutes"])
        actual = float(metrics["measured_actual_minutes"])
        completed_effort = float(metrics["completed_effort_minutes"])
        state_counts = {"planned": 0, "in_progress": 0, "completed": 0}
        for task in tasks:
            status = str(task.get("status") or "planned").strip().casefold()
            if status in {"complete", "completed", "done"}:
                state_counts["completed"] += 1
            elif status in {"active", "learning", "in_progress"}:
                state_counts["in_progress"] += 1
            else:
                state_counts["planned"] += 1
        plan_status = str(plan.get("status") or "").strip().casefold()
        stale = bool(plan.get("stale")) or plan_status in {"stale", "needs_adjustment"}
        self._plan_needs_adjustment = stale
        self.adjust_button.setVisible(stale)
        self.adjust_button.setEnabled(stale and self._action_future is None)
        old_estimate = plan.get("old_plan_estimate") or {}
        pending_estimate = plan.get("pending_plan_estimate") or {}
        pending_total = float(pending_estimate.get("total_estimated_task_minutes") or 0)
        pending_initial = float(pending_estimate.get("initial_learning_minutes") or 0)
        pending_reviews = float(pending_estimate.get("projected_review_minutes") or 0)
        pending_daily = int(
            pending_estimate.get("recommended_minutes_per_day")
            or (pending_estimate.get("allocation") or {}).get("recommended_minutes_per_day")
            or 0
        )
        if stale and pending_daily > 0:
            self.daily_minutes.setValue(pending_daily)
        old_total = float(
            old_estimate.get("total_estimated_task_minutes")
            or plan.get("total_estimated_task_minutes")
            or planned
            or 0
        )
        displayed_plan_minutes = pending_total if stale and pending_total > 0 else planned
        maximum = max(round(displayed_plan_minutes), round(completed_effort), 1)
        self.planned_bar.setRange(0, maximum)
        self.planned_bar.setValue(round(displayed_plan_minutes))
        self.actual_bar.setRange(0, maximum)
        self.actual_bar.setValue(round(min(completed_effort, maximum)))
        planned_label = _format_minutes(planned)
        actual_label = _format_minutes(actual)
        completed_label = _format_minutes(completed_effort)
        detail_labels = []
        if metrics["completed_measured_minutes"]:
            detail_labels.append(
                f"实测 {_format_minutes(float(metrics['completed_measured_minutes']))} 分钟"
            )
        if metrics["completed_fallback_minutes"]:
            detail_labels.append(
                "完成任务折算时长 "
                f"{_format_minutes(float(metrics['completed_fallback_minutes']))} 分钟"
            )
        completion_note = f"完成任务 {completed_label} 分钟"
        if detail_labels:
            completion_note += f"（{' · '.join(detail_labels)}）"
        if stale and pending_total > 0:
            pending_breakdown = (
                f"（首次学习 {_format_minutes(pending_initial)} + "
                f"复习 {_format_minutes(pending_reviews)}）"
                if pending_initial or pending_reviews
                else ""
            )
            self.planned_actual_label.setText(
                f"当前重新评估 {_format_minutes(pending_total)} 分钟{pending_breakdown} · "
                f"旧版 {_format_minutes(old_total)} 分钟仅供回看 · "
                f"{completion_note} · 实际计时记录 {actual_label} 分钟"
            )
        else:
            self.planned_actual_label.setText(
                f"计划 {planned_label} 分钟 · {completion_note} · 实际计时记录 {actual_label} 分钟"
            )
        self.state_chart.setText(
            ("旧版任务（新预览待确认）：" if stale else "")
            + f"计划中 {state_counts['planned']} · 进行中 {state_counts['in_progress']} · 已完成 {state_counts['completed']}"
        )
        self.table.setRowCount(len(tasks))
        self._set_table_state(
            bool(tasks),
            "当前计划还没有可显示的任务。先生成计划预览并确认激活。",
        )
        for row, task in enumerate(tasks):
            estimated = task.get("estimated_task_minutes")
            if estimated is None:
                estimated = task.get("planned_task_minutes")
            task_metrics = task_time_metrics(task)
            actual = float(task_metrics["measured_actual_minutes"])
            if actual:
                actual_text = f"实际记录 {_format_minutes(actual)} 分钟"
            elif task_metrics["completed_fallback_minutes"]:
                actual_text = (
                    "完成任务折算时长 "
                    f"{_format_minutes(float(task_metrics['completed_fallback_minutes']))} 分钟"
                )
            else:
                actual_text = "无实际记录"
            values = (
                task.get("day_date") or task.get("date") or "—",
                task.get("title") or task.get("name") or "未命名任务",
                task.get("learning_phase") or "—",
                task.get("task_type") or "—",
                f"{estimated or 0} 分钟",
                actual_text,
                task.get("status") or ("预览" if plan.get("status") == "preview" else "planned"),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(str(value))
                self.table.setItem(row, column, cell)
