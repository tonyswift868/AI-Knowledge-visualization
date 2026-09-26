"""Old overview layout, driven only by the current authenticated plan projection."""
from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget)

from .plan_widgets import PlanCalendar, TaskCardWidget
from .presentation import completed_task, human_status


def label(text, name=""):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    result.setMinimumWidth(0)
    if name:
        result.setObjectName(name)
    return result


class OverviewPage(QWidget):
    open_plan = Signal(str)
    open_materials = Signal()
    refresh_requested = Signal()

    def __init__(self, display_name, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(38, 24, 38, 18)
        layout.setSpacing(12)
        self.greeting = label("你好，" + (display_name or "MentorFlow 账号"), "pageHeading")
        layout.addWidget(self.greeting)
        layout.addWidget(label("这里是你的学习总览：查看当前计划、学习安排和关联资料。", "pageSubtitle"))
        metrics = QHBoxLayout()
        self.metric_values = {}
        for key, title in (("plans", "已同步计划"), ("progress", "已完成 / 已同步任务"), ("materials", "计划关联资料")):
            card = QFrame()
            card.setObjectName("metricCard")
            box = QVBoxLayout(card)
            box.addWidget(label(title))
            value = label("尚未确认", "metricValue")
            self.metric_values[key] = value
            box.addWidget(value)
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)
        current = QFrame()
        current.setObjectName("overviewTaskSection")
        current_layout = QVBoxLayout(current)
        current_layout.addWidget(label("当前计划与下一步", "sectionHeading"))
        self.current_label = label("计划会话尚未接入；正在等待账号同步。")
        current_layout.addWidget(self.current_label)
        self.status = label("")
        self.status.setAccessibleName("概览计划同步状态")
        current_layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("刷新概览")
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.setToolTip("重新读取已经同步到本机的计划列表；不会单独执行账号同步。")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.refresh_button.setEnabled(False)
        actions.addWidget(self.refresh_button)
        self.materials_button = QPushButton("查看计划完成情况")
        self.materials_button.setObjectName("secondaryButton")
        self.materials_button.setToolTip("资料会在对应计划详情中查看；这里打开计划完成情况和知识地图。")
        self.materials_button.clicked.connect(self.open_materials.emit)
        actions.addWidget(self.materials_button)
        actions.addStretch()
        current_layout.addLayout(actions)
        layout.addWidget(current)

        plans_section = QFrame()
        plans_section.setObjectName("overviewTaskSection")
        plans_layout = QVBoxLayout(plans_section)
        plans_header = QHBoxLayout()
        plans_header.addWidget(label("最近计划", "sectionHeading"))
        plans_header.addStretch()
        self.all_plans_button = QPushButton("查看全部计划")
        self.all_plans_button.setObjectName("secondaryButton")
        self.all_plans_button.setToolTip("进入“我的计划”查看、编辑和管理全部计划。")
        self.all_plans_button.clicked.connect(lambda: self.open_plan.emit(""))
        plans_header.addWidget(self.all_plans_button)
        plans_layout.addLayout(plans_header)
        self.cards = QVBoxLayout()
        plans_layout.addLayout(self.cards)
        self.cards_notice = label("同步后显示当前账号的最近计划。", "pageSubtitle")
        plans_layout.addWidget(self.cards_notice)
        layout.addWidget(plans_section)

        calendar_section = QFrame()
        self.calendar_section = calendar_section
        calendar_section.setObjectName("overviewTaskSection")
        calendar_layout = QVBoxLayout(calendar_section)
        calendar_layout.addWidget(label("近期日历 · 当前计划安排", "sectionHeading"))
        self.calendar_summary = label("计划尚未同步，无法确认日程。")
        calendar_layout.addWidget(self.calendar_summary)
        self.calendar = PlanCalendar()
        self.calendar.setObjectName("effortCalendar")
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(PlanCalendar.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setMinimumHeight(340)
        self.calendar.setMaximumHeight(360)
        self.calendar.selectionChanged.connect(self._calendar_selected)
        calendar_layout.addWidget(self.calendar)
        layout.addWidget(calendar_section)
        layout.addStretch()
        self.scroll.setWidget(content)
        root.addWidget(self.scroll)
        self._catalog = None
        self._selected_key = None
        self._rendered_content = None

    def _calendar_selected(self):
        self.calendar_summary.setText(self.calendar.summary_for(self.calendar.selectedDate()))

    def apply_display_preferences(self, preferences):
        self.calendar_section.setVisible(preferences.get("show_calendar", True))

    def set_plan_state(self, catalog, current_plan, message, can_refresh):
        self.status.setText(message)
        self.refresh_button.setEnabled(can_refresh)
        if catalog is None:
            self.current_label.setText("尚未确认当前计划，请等待账号同步或重试。")
        selected_key = current_plan.key if current_plan else None
        # A service may update task progress inside the same catalog object.
        # Cache a value snapshot, not just object identity, so refresh is visible.
        content = repr(catalog) if catalog is not None else None
        if content == self._rendered_content and selected_key == self._selected_key:
            return
        self._rendered_content = content
        self._catalog, self._selected_key = catalog, selected_key
        while self.cards.count():
            widget = self.cards.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.calendar.set_day_metrics({})
        self.calendar.set_day_summaries({})
        self.calendar.empty_summary = "计划尚未同步，无法确认这一天的安排。"
        if catalog is None:
            for metric in self.metric_values.values():
                metric.setText("尚未确认")
            self.current_label.setText("尚未确认当前计划，请等待账号同步或重试。")
            self.cards_notice.setText("尚未显示计划；账号同步完成后会自动更新。")
            self._calendar_selected()
            return
        all_tasks = [task for plan in catalog.plans for task in plan.tasks]
        partial = bool(catalog.notices or any(plan.issues for plan in catalog.plans))
        suffix = ""
        self.metric_values["plans"].setText(f"{len(catalog.plans)} 个{suffix}")
        self.metric_values["progress"].setText(f"{sum(completed_task(t) for t in all_tasks)} / {len(all_tasks)}{suffix}")
        self.metric_values["materials"].setText(f"{len({r.resource_id for p in catalog.plans for r in p.resources})} 份{suffix}")
        if current_plan:
            pending = next((task for task in current_plan.tasks if not completed_task(task)), None)
            text = current_plan.workspace_title + " · " + current_plan.title
            text += "\n下一项：" + str(pending.get("title") or pending.get("name") or "未命名任务") if pending else "\n请查看任务记录与关联资料。"
        elif catalog.plans:
            text = "已同步当前账号的计划，请选择一个继续查看。"
        else:
            text = "还没有已同步的计划，可以创建计划或等待账号同步。"
        self.current_label.setText(text)
        theme = {"accent": "#c95f8d", "background": "#fff2f7", "symbol": "◈"}
        # Keep the dashboard concise.  PlansPage owns the complete directory;
        # the overview only surfaces the two most recent entries.
        for plan in catalog.plans[:2]:
            card = TaskCardWidget(title=plan.workspace_title + " · " + plan.title,
                progress=f"已完成 {sum(completed_task(t) for t in plan.tasks)} / {len(plan.tasks)} 项",
                material_text=f"关联资料 {len(plan.resources)} 份" + (" · 内容待补全" if plan.issues else ""),
                status=human_status(plan.status), completed=sum(completed_task(t) for t in plan.tasks),
                total=len(plan.tasks), theme=theme,
                on_detail=lambda _checked=False, key=plan.key: self.open_plan.emit(key))
            self.cards.addWidget(card)
        source = "本机同步于" if getattr(catalog, "authority", "") == "LOCAL_ACCOUNT" else "服务器快照确认于"
        try:
            observed = datetime.fromisoformat(catalog.observed_at.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
        except (ValueError, AttributeError):
            observed = "时间未记录"
        recent_notice = "这里显示最近 2 份计划；点击“查看全部计划”管理完整目录。" if len(catalog.plans) > 2 else "这里显示已同步的计划。"
        self.cards_notice.setText(f"{source} {observed}；{recent_notice}")
        self._render_calendar([current_plan] if current_plan else catalog.plans, partial)

    def _render_calendar(self, plans, partial):
        days = {}
        for plan in plans:
            for task in plan.tasks:
                day = str(task.get("day_date") or task.get("date") or "")[:10]
                if QDate.fromString(day, "yyyy-MM-dd").isValid():
                    days.setdefault(day, []).append(task)
        def elapsed(task):
            try:
                value = float(task.get("actual_elapsed_minutes") or 0)
                return value if math.isfinite(value) and value > 0 else 0
            except (ValueError, TypeError):
                return 0
        efforts = {day: sum(elapsed(t) for t in tasks) for day, tasks in days.items()}
        max_effort = max(efforts.values(), default=1) or 1
        self.calendar.empty_summary = "已同步的计划在这一天暂无安排。"
        self.calendar.set_day_summaries({day: day + "\n" + "\n".join(str(t.get("title") or t.get("name") or "未命名任务") + " · " + human_status(t.get("status")) for t in tasks) for day, tasks in days.items()})
        self.calendar.set_day_metrics({day: {"effort": efforts[day], "ratio": efforts[day] / max_effort,
            "scheduled": len(tasks), "has_activity": any(completed_task(t) for t in tasks)} for day, tasks in days.items()})
        self._calendar_selected()

    def dispose(self):
        self.set_plan_state(None, None, "", False)
        self.greeting.clear()
        self.current_label.clear()
        self.cards_notice.clear()
        self.calendar_summary.clear()
        self.setEnabled(False)
