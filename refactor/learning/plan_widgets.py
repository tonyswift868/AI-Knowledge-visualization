"""Pure widgets extracted from old main_window.py and plans_page.py.

Retains plan card visuals and the effort calendar's drawing, keyboard/tooltips
and wheel forwarding, without importing any old business modules.
"""
from PySide6.QtCore import QDate, QEvent, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QCalendarWidget, QFrame, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QTableView, QToolTip,
    QVBoxLayout, QWidget)


class TaskCardWidget(QFrame):
    """Compact, readable task card used by both overview and task list."""

    def __init__(
        self,
        *,
        title: str,
        progress: str,
        material_text: str,
        status: str,
        completed: int,
        total: int,
        theme: dict[str, str],
        on_detail,
        on_archive=None,
        archived: bool = False,
    ) -> None:
        super().__init__()
        self.setObjectName("taskCard")
        self.setMinimumHeight(124)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.apply_theme(theme)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(15, 12, 12, 12)
        outer.setSpacing(12)
        accent = QFrame()
        accent.setObjectName("taskCardAccent")
        accent.setFixedWidth(7)
        accent.setStyleSheet(f"background: {theme['accent']}; border-radius: 3px;")
        outer.addWidget(accent)

        body = QVBoxLayout()
        body.setSpacing(5)
        heading = QHBoxLayout()
        icon = QLabel(theme["symbol"])
        icon.setStyleSheet(f"color: {theme['accent']}; font-size: 20px; background: transparent;")
        heading.addWidget(icon)
        title_label = QLabel(title)
        title_label.setTextFormat(Qt.TextFormat.PlainText)
        title_label.setObjectName("taskCardTitle")
        title_label.setMinimumWidth(0)
        title_label.setWordWrap(True)
        title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title_label.setToolTip(title)
        heading.addWidget(title_label, 1)
        status_label = QLabel(status)
        status_label.setTextFormat(Qt.TextFormat.PlainText)
        status_label.setObjectName("taskCardStatus")
        heading.addWidget(status_label)
        body.addLayout(heading)
        meta = QLabel(f"{progress}　{material_text}")
        meta.setTextFormat(Qt.TextFormat.PlainText)
        meta.setObjectName("taskCardMeta")
        body.addWidget(meta)
        bar = QProgressBar()
        bar.setRange(0, max(total, 1))
        bar.setValue(min(max(completed, 0), max(total, 1)) if total else 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setStyleSheet(
            "QProgressBar { background: rgba(255,255,255,0.75); border: 0; border-radius: 3px; }"
            f"QProgressBar::chunk {{ background: {theme['accent']}; border-radius: 3px; }}"
        )
        body.addWidget(bar)
        outer.addLayout(body, 1)

        actions = QVBoxLayout()
        actions.setSpacing(5)
        detail = QPushButton("查看计划")
        detail.setObjectName("secondaryButton")
        detail.setMinimumHeight(34)
        detail.clicked.connect(on_detail)
        actions.addWidget(detail)
        if on_archive is not None:
            archive = QPushButton("恢复计划" if archived else "归档计划")
            archive.setObjectName("secondaryButton")
            archive.setMinimumHeight(34)
            archive.clicked.connect(on_archive)
            actions.addWidget(archive)
        actions.addStretch()
        outer.addLayout(actions)

    def apply_theme(self, theme: dict[str, str]) -> None:
        self.setStyleSheet(
            f"QFrame#taskCard {{ background: {theme['background']}; border: 1px solid #d9d3c8; "
            f"border-radius: 14px; }} QLabel#taskCardTitle {{ font-size: 16px; font-weight: 800; "
            f"color: #273a34; background: transparent; }} QLabel#taskCardMeta {{ color: #64736e; "
            f"background: transparent; }} QLabel#taskCardStatus {{ color: {theme['accent']}; "
            f"font-weight: 700; background: transparent; }}"
        )

class PlanCalendar(QCalendarWidget):
    """Friendly effort calendar with compact circular day markers."""

    def __init__(self) -> None:
        super().__init__()
        self.empty_summary = "计划尚未读取，无法确认这一天的安排。"
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
        return self._day_summaries.get(value.toString("yyyy-MM-dd"), self.empty_summary)

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
