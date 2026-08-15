from __future__ import annotations

import os
import math
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl, QPoint, QPointF, QRect
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QWidgetAction,
    QVBoxLayout,
    QWidget,
)

from .api import ApiError, DesktopApi
from .knowledge_map_view import KnowledgeMapView
from .materials_page import MaterialsPage
from .plans_page import PlansPage
from .classroom_page import ClassroomPage
from .system_page import SystemPage
from .facts import PROGRAMMER_ANALOGIES
from .official_feed import OfficialFeedService, OfficialUpdate
from .version import version_display
from backend.app.credential_store import CredentialStoreError, store_secret


TASK_THEMES = {
    "sage": {"label": "鼠尾草绿", "background": "#edf6f0", "accent": "#4b896f", "symbol": "✦"},
    "pink": {"label": "樱花粉", "background": "#fff0f5", "accent": "#d2678d", "symbol": "✿"},
    "blue": {"label": "晴空蓝", "background": "#eef5ff", "accent": "#5b82c4", "symbol": "◌"},
    "amber": {"label": "暖阳橙", "background": "#fff6e8", "accent": "#c88842", "symbol": "☼"},
    "lavender": {"label": "薰衣草紫", "background": "#f4efff", "accent": "#8068b7", "symbol": "✧"},
    "mint": {"label": "薄荷青", "background": "#eaf8f6", "accent": "#3b9a91", "symbol": "❋"},
}


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
        self.setMinimumHeight(112)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        title_label.setObjectName("taskCardTitle")
        title_label.setWordWrap(False)
        title_label.setToolTip(title)
        heading.addWidget(title_label, 1)
        status_label = QLabel(status)
        status_label.setObjectName("taskCardStatus")
        heading.addWidget(status_label)
        body.addLayout(heading)
        meta = QLabel(f"{progress}　{material_text}")
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


class SpotlightTutorial(QWidget):
    """Four-step keyboard-accessible guide with a resize-safe spotlight."""

    def __init__(self, parent: QWidget, steps, on_finished, on_step_changed=None, start_step: int = 0) -> None:
        super().__init__(parent)
        self.steps = tuple(steps)
        self.on_finished = on_finished
        self.on_step_changed = on_step_changed
        self.step_index = max(0, min(int(start_step), len(self.steps) - 1))
        self.setObjectName("spotlightTutorial")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(
            "QFrame#spotlightCard { background: #fffaf7; border: 2px solid #edc2d3; border-radius: 18px; }"
        )
        self.card = QFrame(self)
        self.card.setObjectName("spotlightCard")
        self.card.setMaximumWidth(680)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(30, 26, 30, 26)
        self.step_label = QLabel()
        self.step_label.setStyleSheet("color: #b86287; font-size: 16px; font-weight: 800;")
        card_layout.addWidget(self.step_label)
        self.title = QLabel()
        self.title.setStyleSheet("color: #3b2b3f; font-size: 24px; font-weight: 800;")
        card_layout.addWidget(self.title)
        self.body = QLabel()
        self.body.setWordWrap(True)
        card_layout.addWidget(self.body)
        actions = QHBoxLayout()
        self.skip_button = QPushButton("跳过")
        self.skip_button.clicked.connect(self.skip)
        actions.addWidget(self.skip_button)
        actions.addStretch()
        self.back_button = QPushButton("上一步")
        self.back_button.clicked.connect(self.back)
        actions.addWidget(self.back_button)
        self.next_button = QPushButton()
        self.next_button.setObjectName("primaryButton")
        self.next_button.clicked.connect(self.next)
        actions.addWidget(self.next_button)
        card_layout.addLayout(actions)
        self._update_step()

    def _target_rect(self) -> QRect:
        if not self.steps:
            return QRect()
        getter = self.steps[self.step_index][1]
        value = getter(self) if callable(getter) else getter
        if isinstance(value, QRect):
            return value.adjusted(-8, -8, 8, 8).intersected(self.rect())
        if isinstance(value, QWidget):
            top_left = value.mapTo(self, QPoint(0, 0))
            return QRect(top_left, value.size()).adjusted(-8, -8, 8, 8).intersected(self.rect())
        return QRect()

    def _update_step(self) -> None:
        target, _getter, title, body = self.steps[self.step_index]
        self.step_label.setText(f"步骤 {self.step_index + 1}/{len(self.steps)} · {target}")
        self.title.setText(title)
        self.body.setText(body)
        self.back_button.setEnabled(self.step_index > 0)
        self.next_button.setText("完成" if self.step_index == len(self.steps) - 1 else "下一步")
        if self.on_step_changed:
            self.on_step_changed(self.step_index)
        self.update()
        self._position_card()

    def _position_card(self) -> None:
        target = self._target_rect()
        width = min(self.card.maximumWidth(), max(420, self.width() - 80))
        self.card.resize(width, self.card.sizeHint().height())
        candidates = (
            QPoint(max(30, target.left()), target.bottom() + 24),
            QPoint(max(30, target.left()), target.top() - self.card.height() - 24),
            QPoint((self.width() - self.card.width()) // 2, (self.height() - self.card.height()) // 2),
        )
        for point in candidates:
            bounded = QPoint(
                max(20, min(point.x(), self.width() - self.card.width() - 20)),
                max(20, min(point.y(), self.height() - self.card.height() - 20)),
            )
            if not QRect(bounded, self.card.size()).intersects(target) or point == candidates[-1]:
                self.card.move(bounded)
                break

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(36, 24, 42, 196))
        target = self._target_rect()
        if not target.isEmpty():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(target, 12, 12)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor("#edc2d3"), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(target, 12, 12)
            card = self.card.geometry()
            target_center = target.center()
            card_center = card.center()
            if target_center.x() < card.left():
                start = QPoint(card.left(), card_center.y())
            elif target_center.x() > card.right():
                start = QPoint(card.right(), card_center.y())
            elif target_center.y() < card.top():
                start = QPoint(card_center.x(), card.top())
            else:
                start = QPoint(card_center.x(), card.bottom())
            end = QPoint(target_center.x(), target_center.y())
            painter.setPen(QPen(QColor("#f19abd"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(start, end)
            angle = math.atan2(end.y() - start.y(), end.x() - start.x())
            arrow_size = 15.0
            left = QPoint(
                int(end.x() - arrow_size * math.cos(angle - math.pi / 6)),
                int(end.y() - arrow_size * math.sin(angle - math.pi / 6)),
            )
            right = QPoint(
                int(end.x() - arrow_size * math.cos(angle + math.pi / 6)),
                int(end.y() - arrow_size * math.sin(angle + math.pi / 6)),
            )
            painter.setBrush(QColor("#f19abd"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(QPolygonF([QPointF(end), QPointF(left), QPointF(right)]))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._position_card()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.skip()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.next()
            return
        super().keyPressEvent(event)

    def next(self) -> None:
        if self.step_index >= len(self.steps) - 1:
            self.on_finished()
        else:
            self.step_index += 1
            self._update_step()

    def back(self) -> None:
        if self.step_index:
            self.step_index -= 1
            self._update_step()

    def skip(self) -> None:
        self.on_finished()


class MainWindow(QMainWindow):
    PROVIDER_PRESET_NAME = "DeepSeek Flash"
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    DEEPSEEK_FLASH_MODEL = "deepseek-v4-flash"

    def __init__(self, api: DesktopApi, user: dict, origin: str):
        super().__init__()
        self.api = api
        self.user = user
        self.origin = origin
        self.settings = QSettings("MentorFlow", "MentorFlow")
        self.provider_session_id = ""
        self._fact_index = 0
        self._tutorial_overlay: SpotlightTutorial | None = None
        self._official_feed_service = OfficialFeedService()
        self._official_updates: list[OfficialUpdate] = []
        self._official_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mentorflow-official-updates",
        )
        self._official_refresh_future: Future[list[OfficialUpdate]] | None = None
        self.setWindowTitle("MentorFlow")
        self.resize(1480, 940)
        self.setMinimumSize(1100, 720)
        shell = QWidget()
        self.setCentralWidget(shell)
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        self._sidebar = sidebar
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 24, 22, 24)
        logo = QLabel("🌿  MentorFlow")
        logo.setObjectName("sideLogo")
        side.addWidget(logo)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        local_mode = os.getenv("MENTORFLOW_LOCAL_DATA_MODE", "").strip().lower() in {"1", "true", "yes"}
        settings_label = "设置"
        navigation_items = (
            ("概览", 0),
            ("我的计划", 1),
            ("资料管理", 3),
            ("原生课堂", 5),
            ("创建计划", 6),
            ("学习远征", 7),
            ("模型连接", 8),
            (settings_label, 9),
            ("更多工具", 10),
        )
        self._navigation_page_indices = [page_index for _label, page_index in navigation_items]
        for label, _page_index in navigation_items:
            QListWidgetItem(label, self.navigation)
        self.navigation.currentRowChanged.connect(self._navigate)
        side.addWidget(self.navigation, 1)
        account = QLabel(
            "本地学习空间\n数据自动识别"
            if local_mode
            else f"{user['display_name']}\n@{user['username']}"
        )
        account.setObjectName("accountBadge")
        side.addWidget(account)
        layout.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._overview())
        self.pages.addWidget(self._tasks())
        self.pages.addWidget(self._knowledge_map())
        self.materials_page = MaterialsPage(self.api, self._refresh_all_views, self._open_create_plan)
        self.pages.addWidget(self.materials_page)
        self.plans_page = PlansPage(
            self.api,
            self._open_map_from_plan,
            self._open_tasks_page,
            self._task_color_menu,
            self._task_color_accent,
        )
        self.pages.addWidget(self.plans_page)
        self.classroom_page = ClassroomPage(self.api, lambda: self.provider_session_id, self.origin)
        self.pages.addWidget(self.classroom_page)
        self.pages.addWidget(self._teacher_chat())
        self.pages.addWidget(self._expedition_page())
        self.pages.addWidget(self._provider_settings())
        self.system_page = SystemPage(
            self.user,
            self.origin,
            self._logout,
            self.api,
            self._open_settings_section,
            self.restart_tutorial,
            self._set_online_updates,
            self._reload_official_updates,
            str(self.settings.value("official_updates/enabled", "0") or "0") == "1",
        )
        self.pages.addWidget(self.system_page)
        self.pages.addWidget(self._more_tools())
        layout.addWidget(self.pages, 1)
        self.navigation.setCurrentRow(0)
        QTimer.singleShot(0, self._refresh_overview)
        if os.getenv("MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE", "") != "1":
            QTimer.singleShot(0, self._restore_provider)
        QTimer.singleShot(250, self._maybe_start_tutorial)
        QTimer.singleShot(0, self._load_cached_official_updates)
        self._fact_timer = QTimer(self)
        self._fact_timer.setInterval(60_000)
        self._fact_timer.timeout.connect(self._next_fact)
        self._fact_timer.start()

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(38, 32, 38, 32)
        heading = QLabel(title)
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        detail = QLabel(subtitle)
        detail.setObjectName("pageSubtitle")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        layout.addSpacing(18)
        return page, layout

    def _open_create_plan(self) -> None:
        """Route all new-plan calls to the dedicated planning page."""
        self.navigation.setCurrentRow(4)

    def _open_settings_section(self, section: str) -> None:
        if section == "provider":
            self.navigation.setCurrentRow(6)
        elif section == "resources":
            self.navigation.setCurrentRow(8)
        elif section == "data":
            # Data and backup controls live on the settings page itself; keep
            # this explicit so the action never falls through to an unrelated
            # destination as navigation entries evolve.
            self.navigation.setCurrentRow(7)
        else:
            self.navigation.setCurrentRow(7)

    def _maybe_start_tutorial(self) -> None:
        if str(self.settings.value("tutorial/completed", "") or "") == "1":
            return
        steps = (
            ("侧边栏", lambda overlay: self._tutorial_target_widget(self._sidebar, overlay), "从侧边栏开始", "这里可以快速切换概览、计划、资料、课堂和设置。"),
            ("我的计划", lambda overlay: self._tutorial_nav_row(1, overlay), "查看学习进度", "“我的计划”会显示进度、资料数量和可恢复的归档计划。"),
            ("创建计划", lambda overlay: self._tutorial_nav_row(4, overlay), "先说清想学什么", "先在这里设定学习目标，再选择可核对的资料并确认日期和学习节奏。"),
            ("原生课堂", lambda overlay: self._tutorial_nav_row(3, overlay), "在课堂中练习", "课堂会保留依据和掌握记录；完成后可随时回到计划。"),
        )
        start_step = int(self.settings.value("tutorial/step", 0) or 0)
        self._tutorial_overlay = SpotlightTutorial(
            self,
            steps,
            self._finish_tutorial,
            self._save_tutorial_step,
            start_step,
        )
        self._tutorial_overlay.setGeometry(self.rect())
        self._tutorial_overlay.show()
        self._tutorial_overlay.raise_()
        self._tutorial_overlay.setFocus()

    @staticmethod
    def _tutorial_target_widget(widget: QWidget, overlay: QWidget) -> QRect:
        top_left = overlay.mapFromGlobal(widget.mapToGlobal(QPoint(0, 0)))
        return QRect(top_left, widget.size())

    def _tutorial_nav_row(self, row: int, overlay: QWidget) -> QRect:
        item = self.navigation.item(row)
        if item is None:
            return self._tutorial_target_widget(self.navigation, overlay)
        visible = self.navigation.visualItemRect(item)
        global_top_left = self.navigation.viewport().mapToGlobal(visible.topLeft())
        top_left = overlay.mapFromGlobal(global_top_left)
        return QRect(top_left, visible.size())

    def _save_tutorial_step(self, step: int) -> None:
        self.settings.setValue("tutorial/step", int(step))
        self.settings.sync()

    def _finish_tutorial(self) -> None:
        self.settings.setValue("tutorial/completed", "1")
        self.settings.setValue("tutorial/step", 0)
        self.settings.sync()
        if self._tutorial_overlay is not None:
            self._tutorial_overlay.deleteLater()
            self._tutorial_overlay = None

    def restart_tutorial(self) -> None:
        self.settings.setValue("tutorial/completed", "0")
        self.settings.setValue("tutorial/step", 0)
        self.settings.sync()
        if self._tutorial_overlay is not None:
            self._tutorial_overlay.close()
            self._tutorial_overlay.deleteLater()
            self._tutorial_overlay = None
        self._maybe_start_tutorial()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._tutorial_overlay is not None:
            self._tutorial_overlay.setGeometry(self.rect())

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._official_executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    def _next_fact(self) -> None:
        self._fact_index = (self._fact_index + 1) % len(PROGRAMMER_ANALOGIES)
        self._render_fact()

    def _render_fact(self) -> None:
        if not hasattr(self, "offline_fact_title"):
            return
        title, body, attribution = PROGRAMMER_ANALOGIES[self._fact_index]
        self.offline_fact_title.setText(title)
        self.offline_fact_body.setText(body)
        self.offline_fact_source.setText(attribution)

    def _load_cached_official_updates(self) -> None:
        self._official_updates = self._official_feed_service.load_cached()
        self._render_official_updates()

    def _set_online_updates(self, enabled: bool) -> None:
        self.settings.setValue("official_updates/enabled", "1" if enabled else "0")
        self.settings.sync()
        if enabled:
            self.system_page.set_official_update_busy(True, "已开启，正在加载官方动态……")
            self._reload_official_updates()
        else:
            self._official_feed_service.last_status = "在线更新已关闭；仍可查看最近一次缓存。"
            self._render_official_updates()
            self.system_page.set_official_update_busy(False, "已关闭在线动态，不会发起网络请求。")

    def _reload_official_updates(self) -> None:
        if str(self.settings.value("official_updates/enabled", "0") or "0") != "1":
            self._official_feed_service.last_status = "在线更新已关闭；打开设置中的开关后才会联网。"
            self._render_official_updates()
            self.system_page.set_official_update_busy(False, self._official_feed_service.last_status)
            return
        if self._official_refresh_future is not None and not self._official_refresh_future.done():
            self.system_page.set_official_update_busy(True, "官方动态正在加载，请稍候……")
            return
        self.system_page.set_official_update_busy(True, "正在从允许的官方来源加载动态……")
        self._official_refresh_future = self._official_executor.submit(
            self._official_feed_service.refresh
        )
        QTimer.singleShot(120, self._poll_official_update_refresh)

    def _poll_official_update_refresh(self) -> None:
        future = self._official_refresh_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(120, self._poll_official_update_refresh)
            return
        self._official_refresh_future = None
        try:
            self._official_updates = future.result()
            status = self._official_feed_service.last_status
        except Exception as exc:  # defensive boundary around optional networking
            status = f"官方动态加载失败：{exc}"
            self._official_feed_service.last_status = status
        self._render_official_updates()
        self.system_page.set_official_update_busy(False, status)

    def _render_official_updates(self) -> None:
        if not hasattr(self, "official_updates_list"):
            return
        self.official_updates_list.clear()
        for item in self._official_updates[:12]:
            self.official_updates_list.addItem(
                f"{item.publisher} · {item.published}\n{item.title}\n{item.link}"
            )
        self.official_updates_status.setText(self._official_feed_service.last_status)

    def _overview(self) -> QWidget:
        page, layout = self._page(
            "你好，" + self.user["display_name"],
            "这里是你的学习总览：正在进行什么、已经完成多少，以及今天最适合先做哪一步。",
        )
        cards = QHBoxLayout()
        cards.setSpacing(12)
        cards_container = QFrame()
        cards_container.setObjectName("overviewMetrics")
        cards_container.setLayout(cards)
        local_mode = os.getenv("MENTORFLOW_LOCAL_DATA_MODE", "").strip().lower() in {"1", "true", "yes"}
        account_card = ("本地学习空间", "已自动识别") if local_mode else ("学习账户", "已连接")
        self.overview_metric_values: dict[str, QLabel] = {}
        for key, (title, value) in (
            ("space", account_card),
            ("active", ("进行中任务", "读取中")),
            ("progress", ("已完成学习项", "读取中")),
            ("next", ("下一步", "打开我的计划")),
        ):
            card = QFrame()
            card.setObjectName("metricCard")
            box = QVBoxLayout(card)
            box.addWidget(QLabel(title))
            number = QLabel(value)
            number.setObjectName("metricValue")
            box.addWidget(number)
            self.overview_metric_values[key] = number
            cards.addWidget(card)
        layout.addWidget(cards_container)
        current_card = QFrame()
        current_card.setObjectName("overviewTaskSection")
        current_layout = QVBoxLayout(current_card)
        current_layout.addWidget(QLabel("当前计划与下一步"), alignment=Qt.AlignmentFlag.AlignLeft)
        self.overview_current_plan = QLabel("正在读取当前计划……")
        self.overview_current_plan.setWordWrap(True)
        current_layout.addWidget(self.overview_current_plan)
        layout.addWidget(current_card)

        fact_card = QFrame()
        fact_card.setObjectName("overviewTaskSection")
        fact_layout = QVBoxLayout(fact_card)
        fact_heading = QHBoxLayout()
        fact_heading.addWidget(QLabel("程序员术语的生活类比"))
        fact_heading.addStretch()
        next_fact = QPushButton("换一张")
        next_fact.setObjectName("linkButton")
        next_fact.clicked.connect(self._next_fact)
        fact_heading.addWidget(next_fact)
        fact_layout.addLayout(fact_heading)
        self.offline_fact_title = QLabel("")
        self.offline_fact_title.setObjectName("sectionHeading")
        fact_layout.addWidget(self.offline_fact_title)
        self.offline_fact_body = QLabel("")
        self.offline_fact_body.setWordWrap(True)
        fact_layout.addWidget(self.offline_fact_body)
        self.offline_fact_source = QLabel("")
        self.offline_fact_source.setObjectName("pageSubtitle")
        fact_layout.addWidget(self.offline_fact_source)
        layout.addWidget(fact_card)
        self._render_fact()

        official_card = QFrame()
        official_card.setObjectName("overviewTaskSection")
        official_layout = QVBoxLayout(official_card)
        official_heading = QHBoxLayout()
        official_heading.addWidget(QLabel("官方动态（可选在线更新）"))
        official_heading.addStretch()
        reload_updates = QPushButton("重新加载")
        reload_updates.setObjectName("linkButton")
        reload_updates.clicked.connect(self._reload_official_updates)
        official_heading.addWidget(reload_updates)
        official_layout.addLayout(official_heading)
        self.official_updates_status = QLabel("在线更新默认关闭。")
        self.official_updates_status.setObjectName("inlineStatus")
        self.official_updates_status.setWordWrap(True)
        official_layout.addWidget(self.official_updates_status)
        self.official_updates_list = QListWidget()
        self.official_updates_list.setObjectName("officialUpdatesList")
        self.official_updates_list.setMaximumHeight(190)
        official_layout.addWidget(self.official_updates_list)
        layout.addWidget(official_card)
        self._render_official_updates()

        task_card = QFrame()
        task_card.setObjectName("overviewTaskSection")
        task_layout = QVBoxLayout(task_card)
        section_title = QLabel("当前学习任务")
        section_title.setObjectName("sectionHeading")
        task_layout.addWidget(section_title)
        self.overview_status = QLabel("正在读取任务……")
        self.overview_status.setObjectName("inlineStatus")
        task_layout.addWidget(self.overview_status)
        self.overview_task_list = QListWidget()
        self.overview_task_list.setObjectName("overviewTaskList")
        self.overview_task_list.setMaximumHeight(290)
        self.overview_task_list.itemDoubleClicked.connect(self._open_task_from_list)
        task_layout.addWidget(self.overview_task_list)
        open_tasks = QPushButton("查看全部计划")
        open_tasks.clicked.connect(lambda: self.navigation.setCurrentRow(1))
        task_layout.addWidget(open_tasks, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(task_card)
        layout.addStretch()
        return page

    def _tasks(self) -> QWidget:
        page, layout = self._page(
            "我的计划",
            "每张卡片只显示你需要做决定的信息。归档可恢复，不会删除资料、计划或进度。",
        )
        new_plan = QPushButton("＋ 新建计划")
        new_plan.setObjectName("primaryButton")
        new_plan.clicked.connect(self._open_create_plan)
        plan_actions = QHBoxLayout()
        plan_actions.addWidget(new_plan)
        self.include_archived_plans = QCheckBox("显示已归档计划")
        self.include_archived_plans.toggled.connect(self._refresh_tasks)
        plan_actions.addWidget(self.include_archived_plans)
        plan_actions.addStretch()
        layout.addLayout(plan_actions)
        self.task_list = QListWidget()
        self.task_list.setObjectName("taskList")
        self.task_list.setWordWrap(True)
        self.task_list.setSpacing(8)
        self.task_list.itemDoubleClicked.connect(self._open_task_from_list)
        layout.addWidget(self.task_list, 1)
        return page

    def _open_task_from_list(self, item: QListWidgetItem) -> None:
        workspace_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not workspace_id:
            return
        self._open_workspace_detail(workspace_id)

    def _open_workspace_detail(self, workspace_id: str) -> None:
        """Open plan as task detail; it intentionally has no separate sidebar slot."""
        self.pages.setCurrentIndex(4)
        self.plans_page.refresh_workspaces()
        QTimer.singleShot(0, lambda: self.plans_page.select_workspace(workspace_id))

    def _open_tasks_page(self) -> None:
        self.navigation.setCurrentRow(1)

    def _knowledge_map(self) -> QWidget:
        page, layout = self._page("学习计划里的知识地图", "地图属于学习计划的一部分：从计划选择任务后打开；单击节点查看材料证据。")
        controls = QHBoxLayout()
        back = QPushButton("返回任务详情")
        back.clicked.connect(lambda: self.pages.setCurrentIndex(4))
        controls.addWidget(back)
        controls.addWidget(QLabel("学习任务"))
        self.map_workspace = QComboBox()
        self.map_workspace.setMinimumWidth(360)
        self.map_workspace.currentIndexChanged.connect(self._load_selected_map)
        controls.addWidget(self.map_workspace, 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self._refresh_map_workspaces)
        controls.addWidget(refresh)
        generate = QPushButton("从资料生成")
        generate.setObjectName("primaryButton")
        generate.clicked.connect(self._start_map_job)
        controls.addWidget(generate)
        fit = QPushButton("适应窗口")
        fit.clicked.connect(self._fit_map)
        controls.addWidget(fit)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.map_view = KnowledgeMapView()
        self.map_view.node_selected.connect(self._show_node_evidence)
        splitter.addWidget(self.map_view)
        evidence_panel = QFrame()
        evidence_panel.setObjectName("evidencePanel")
        evidence_layout = QVBoxLayout(evidence_panel)
        self.map_state = QLabel("请选择一个学习任务。")
        self.map_state.setWordWrap(True)
        evidence_layout.addWidget(self.map_state)
        self.evidence_text = QPlainTextEdit()
        self.evidence_text.setReadOnly(True)
        self.evidence_text.setPlaceholderText("单击概念节点后，这里显示来源、页码、字符范围、音频时间和摘录。")
        evidence_layout.addWidget(self.evidence_text, 1)
        evidence_layout.addWidget(QLabel("待审核候选"))
        self.map_candidates = QListWidget()
        self.map_candidates.setMaximumHeight(180)
        self.map_candidates.currentItemChanged.connect(self._show_candidate)
        evidence_layout.addWidget(self.map_candidates)
        self.candidate_reason = QLineEdit()
        self.candidate_reason.setPlaceholderText("可选：记录确认或驳回原因")
        evidence_layout.addWidget(self.candidate_reason)
        candidate_actions = QHBoxLayout()
        confirm_candidate = QPushButton("确认候选")
        confirm_candidate.clicked.connect(lambda: self._decide_candidate("confirmed"))
        candidate_actions.addWidget(confirm_candidate)
        reject_candidate = QPushButton("驳回候选")
        reject_candidate.clicked.connect(lambda: self._decide_candidate("rejected"))
        candidate_actions.addWidget(reject_candidate)
        evidence_layout.addLayout(candidate_actions)
        splitter.addWidget(evidence_panel)
        splitter.setSizes([850, 360])
        layout.addWidget(splitter, 1)
        self._map_job_timer = QTimer(self)
        self._map_job_timer.setInterval(1200)
        self._map_job_timer.timeout.connect(self._poll_map_job)
        self._map_job_id = ""
        return page

    def _teacher_chat(self) -> QWidget:
        page, layout = self._page(
            "创建计划",
            "先说清学习目标，再选择可核对的资料；确认后只需补充日期，系统会生成知识地图和每日任务。",
        )
        profile = QWebEngineProfile("mentorflow_teacher_chat", self)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        web = QWebEngineView()
        web.setPage(QWebEnginePage(profile, web))
        web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        loaded = False

        def load_teacher_chat() -> None:
            nonlocal loaded
            if loaded:
                return
            loaded = True
            web.load(QUrl(f"{self.origin}/?tool=planner"))

        token = self.api.session_token
        if token:
            cookie = QNetworkCookie(b"mentorflow_session", token.encode("utf-8"))
            cookie.setHttpOnly(True)
            cookie.setPath("/")
            store = profile.cookieStore()
            store.cookieAdded.connect(
                lambda added: load_teacher_chat()
                if bytes(added.name()) == b"mentorflow_session"
                else None
            )
            store.setCookie(cookie, QUrl(self.origin))
            QTimer.singleShot(1500, load_teacher_chat)
        if not token:
            load_teacher_chat()
        layout.addWidget(web, 1)
        return page

    def _more_tools(self) -> QWidget:
        page, layout = self._page(
            "更多工具",
            "辅助工具集中放在这里，不会占用学习任务、资料管理、学习计划或原生课堂的位置。",
        )
        cards = QGridLayout()
        cards.setHorizontalSpacing(16)
        cards.setVerticalSpacing(16)
        tools = (
            (
                "教师闲聊",
                "保留自由交流，不会写入学习计划；需要正式学习时回到创建计划。",
                "casual",
                "打开教师闲聊",
            ),
        )
        for index, (title, detail, tool, action_text) in enumerate(tools):
            card = QFrame()
            card.setObjectName("explanationCard")
            box = QVBoxLayout(card)
            heading = QLabel(title)
            heading.setObjectName("cardHeading")
            box.addWidget(heading)
            body = QLabel(detail)
            body.setWordWrap(True)
            body.setObjectName("pageSubtitle")
            box.addWidget(body)
            button = QPushButton(action_text)
            button.setObjectName("primaryButton" if index == 0 else "secondaryButton")
            button.clicked.connect(lambda _checked=False, selected=tool, label=title: self._open_web_tool(selected, label))
            box.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
            cards.addWidget(card, index // 2, index % 2)
        layout.addLayout(cards)
        layout.addStretch()
        return page

    def _open_web_tool(self, tool: str, title: str) -> None:
        """Open an auxiliary web tool in a normal, non-topmost window."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"MentorFlow · {title}")
        dialog.resize(1180, 780)
        dialog.setModal(False)
        content = QVBoxLayout(dialog)
        web = QWebEngineView(dialog)
        profile = QWebEngineProfile(f"mentorflow_auxiliary_{tool}_{id(dialog)}", dialog)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        web.setPage(QWebEnginePage(profile, web))
        web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        token = self.api.session_token
        if token:
            cookie = QNetworkCookie(b"mentorflow_session", token.encode("utf-8"))
            cookie.setHttpOnly(True)
            cookie.setPath("/")
            profile.cookieStore().setCookie(cookie, QUrl(self.origin))
        web.load(QUrl(f"{self.origin}/?tool={tool}"))
        content.addWidget(web, 1)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.close)
        content.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.show()

    def _expedition_page(self) -> QWidget:
        """Keep the expedition as one focused top-level destination."""
        page, layout = self._page(
            "学习远征",
            "探索、训练和成长记录单独放在这里；不会把额外玩法混进任务、资料或原生课堂。",
        )
        profile = QWebEngineProfile("mentorflow_expedition", self)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        web = QWebEngineView()
        web.setPage(QWebEnginePage(profile, web))
        web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        loaded = False

        def load_expedition() -> None:
            nonlocal loaded
            if loaded:
                return
            loaded = True
            web.load(QUrl(f"{self.origin}/?tool=expedition"))

        token = self.api.session_token
        if token:
            cookie = QNetworkCookie(b"mentorflow_session", token.encode("utf-8"))
            cookie.setHttpOnly(True)
            cookie.setPath("/")
            store = profile.cookieStore()
            store.cookieAdded.connect(
                lambda added: load_expedition()
                if bytes(added.name()) == b"mentorflow_session"
                else None
            )
            store.setCookie(cookie, QUrl(self.origin))
            QTimer.singleShot(1500, load_expedition)
        else:
            load_expedition()
        layout.addWidget(web, 1)
        return page

    def _provider_settings(self) -> QWidget:
        page, layout = self._page(
            "模型连接",
            "可配置任意 OpenAI-compatible 服务。默认值是 DeepSeek Flash 示例，但保存后会一直显示你填写的地址和模型名称；API Key 只写入 Windows 凭据库。",
        )
        form = QFrame()
        form.setObjectName("explanationCard")
        box = QVBoxLayout(form)
        box.addWidget(QLabel("OpenAI-compatible Base URL"))
        self.provider_base_url = QLineEdit(
            str(self.settings.value("provider/base_url", self.DEEPSEEK_BASE_URL))
        )
        self.provider_base_url.setPlaceholderText("请输入服务商提供的 HTTPS API 地址")
        box.addWidget(self.provider_base_url)
        box.addWidget(QLabel("预设名称（方便区分不同服务）"))
        self.provider_preset_name = QLineEdit(
            str(self.settings.value("provider/preset_name", "默认模型预设"))
        )
        self.provider_preset_name.setPlaceholderText("例如：DeepSeek Flash、我的本地模型")
        box.addWidget(self.provider_preset_name)
        box.addWidget(QLabel("模型名称（服务商提供）"))
        self.provider_model = QLineEdit(
            str(self.settings.value("provider/model", self.DEEPSEEK_FLASH_MODEL))
        )
        self.provider_model.setPlaceholderText(self.DEEPSEEK_FLASH_MODEL)
        box.addWidget(self.provider_model)
        box.addWidget(QLabel("API Key（仅在新增或更换时输入）"))
        self.provider_secret = QLineEdit()
        self.provider_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.provider_secret.setPlaceholderText("已保存时留空即可重新连接")
        box.addWidget(self.provider_secret)
        actions = QHBoxLayout()
        save_button = QPushButton("保存预设")
        save_button.setObjectName("secondaryButton")
        save_button.clicked.connect(self._save_provider_preset)
        actions.addWidget(save_button)
        connect_button = QPushButton("保存并验证连接")
        connect_button.setObjectName("primaryButton")
        connect_button.clicked.connect(self._connect_provider)
        actions.addWidget(connect_button)
        box.addLayout(actions)
        self.provider_status = QLabel("尚未连接。")
        self.provider_status.setWordWrap(True)
        box.addWidget(self.provider_status)
        layout.addWidget(form)
        layout.addStretch()
        return page

    def _navigate(self, index: int) -> None:
        if index < 0 or index >= len(self._navigation_page_indices):
            return
        page_index = self._navigation_page_indices[index]
        self.pages.setCurrentIndex(page_index)
        if page_index == 0:
            self._refresh_overview()
        elif page_index == 1:
            self._refresh_tasks()
        elif page_index == 2:
            self._refresh_map_workspaces()
        elif page_index == 3:
            self.materials_page.refresh_workspaces()
        elif page_index == 4:
            self.plans_page.refresh_workspaces()
        elif page_index == 5:
            self.classroom_page.refresh_workspaces()

    def _workspace_items(self, *, include_archived: bool = False) -> list[dict]:
        merged: dict[str, dict] = {}
        try:
            for item in self.api.workspaces():
                workspace_id = str(item.get("id") or item.get("workspace_id") or "")
                if workspace_id:
                    merged[workspace_id] = dict(item)
        except ApiError:
            pass
        try:
            try:
                payload = self.api.active_workspaces(include_archived=include_archived)
            except TypeError:
                payload = self.api.active_workspaces()
            for item in payload.get("items", []):
                workspace_id = str(item.get("workspace_id") or item.get("id") or "")
                if workspace_id:
                    merged[workspace_id] = {**merged.get(workspace_id, {}), **item}
        except ApiError:
            pass
        items = list(merged.values())
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return items

    @staticmethod
    def _task_progress(item: dict) -> tuple[int, int]:
        total = max(0, int(item.get("task_count") or 0))
        completed = min(total, max(0, int(item.get("completed_task_count") or 0)))
        return completed, total

    @staticmethod
    def _human_status(value: object) -> str:
        return {
            "active": "学习中",
            "learning": "学习中",
            "completed": "已完成",
            "complete": "已完成",
            "paused": "已暂停",
            "archived": "已归档",
        }.get(str(value or "active").casefold(), "学习中")

    def _task_theme_key(self, workspace_id: str) -> str:
        value = str(self.settings.value(f"task-colors/{workspace_id}", "sage") or "sage")
        return value if value in TASK_THEMES else "sage"

    def _task_color_accent(self, workspace_id: str) -> str:
        return TASK_THEMES[self._task_theme_key(workspace_id)]["accent"]

    def _task_color_menu(self, workspace_id: str) -> QMenu:
        menu = QMenu(self)
        title = menu.addAction("选择卡片颜色")
        title.setEnabled(False)
        palette_widget = QWidget(menu)
        palette_layout = QGridLayout(palette_widget)
        palette_layout.setContentsMargins(10, 8, 10, 8)
        palette_layout.setHorizontalSpacing(10)
        palette_layout.setVerticalSpacing(10)
        for index, (key, theme) in enumerate(TASK_THEMES.items()):
            swatch = QToolButton(palette_widget)
            swatch.setFixedSize(34, 34)
            swatch.setToolTip(theme["label"])
            swatch.setText("●")
            swatch.setStyleSheet(
                f"QToolButton {{ color: {theme['accent']}; font-size: 28px; border: 0; "
                "background: transparent; padding: 0; } QToolButton:hover { background: #f2eee6; "
                "border-radius: 17px; }"
            )
            swatch.clicked.connect(lambda _checked=False, selected=key: (menu.close(), self._set_task_color(workspace_id, selected)))
            palette_layout.addWidget(swatch, index // 3, index % 3)
        palette_action = QWidgetAction(menu)
        palette_action.setDefaultWidget(palette_widget)
        menu.addAction(palette_action)
        menu.addSeparator()
        reset = menu.addAction("恢复默认颜色")
        reset.triggered.connect(lambda: self._set_task_color(workspace_id, "sage"))
        return menu

    def _set_task_color(self, workspace_id: str, theme_key: str) -> None:
        if theme_key not in TASK_THEMES:
            theme_key = "sage"
        self.settings.setValue(f"task-colors/{workspace_id}", theme_key)
        self.settings.sync()
        self._refresh_tasks()
        self._refresh_overview()

    def _task_card(self, item: dict, *, overview: bool = False) -> tuple[QListWidgetItem, TaskCardWidget]:
        workspace_id = str(item.get("workspace_id") or item.get("id") or "")
        done, count = self._task_progress(item)
        title = str(item.get("title") or "未命名任务")
        material_count = item.get("material_count")
        material_text = f"资料 {material_count} 份" if material_count is not None else "资料数待更新"
        progress = f"进度 {done} / {count} 项" if count else "尚未生成学习计划"
        theme = TASK_THEMES[self._task_theme_key(workspace_id)]
        list_item = QListWidgetItem()
        list_item.setData(Qt.ItemDataRole.UserRole, workspace_id)
        widget = TaskCardWidget(
            title=title,
            progress=progress,
            material_text=material_text,
            status=self._human_status("archived" if item.get("archived") else item.get("status")),
            completed=done,
            total=count,
            theme=theme,
            on_detail=lambda _checked=False, selected=workspace_id: self._open_workspace_detail(selected),
            on_archive=(
                lambda _checked=False, selected=dict(item): self._toggle_workspace_archive(selected)
            ) if not overview else None,
            archived=bool(item.get("archived")) or str(item.get("status") or "").casefold() == "archived",
        )
        list_item.setSizeHint(widget.sizeHint())
        return list_item, widget

    def _toggle_workspace_archive(self, item: dict) -> None:
        workspace_id = str(item.get("workspace_id") or item.get("id") or "")
        if not workspace_id:
            return
        title = str(item.get("title") or "未命名任务")
        archived = bool(item.get("archived")) or str(item.get("status") or "").casefold() == "archived"
        action = "恢复" if archived else "归档"
        if not archived and QMessageBox.question(
            self,
            "归档计划",
            f"确定归档“{title}”吗？\n\n资料、计划和进度都会保留，之后可以从设置或计划入口恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.api.archive_workspace(workspace_id, archived=not archived)
        except ApiError as exc:
            QMessageBox.warning(self, f"{action}失败", f"{exc.code}：{exc.message}")
            return
        self._refresh_all_views()

    def _refresh_all_views(self) -> None:
        self._refresh_overview()
        self._refresh_tasks()
        if hasattr(self, "plans_page"):
            self.plans_page.refresh_workspaces()
        if hasattr(self, "classroom_page"):
            self.classroom_page.refresh_workspaces()

    def _refresh_overview(self) -> None:
        if not hasattr(self, "overview_task_list"):
            return
        items = self._workspace_items()
        active = [item for item in items if self._human_status(item.get("status")) != "已完成"]
        completed = sum(self._task_progress(item)[0] for item in items)
        total = sum(self._task_progress(item)[1] for item in items)
        self.overview_metric_values["active"].setText(str(len(active)))
        self.overview_metric_values["progress"].setText(f"{completed} / {total}" if total else "暂无任务")
        self.overview_metric_values["next"].setText("开始第一个任务" if not active else "继续学习")
        if active:
            current = active[0]
            done, count = self._task_progress(current)
            title = str(current.get("title") or "当前计划")
            self.overview_current_plan.setText(
                f"{title} · 已完成 {done}/{count} 项。下一步：打开计划详情，继续最早未完成的学习项。"
            )
        else:
            self.overview_current_plan.setText("还没有进行中的计划。点击“新建计划”，设定目标和日期后开始。")
        self.overview_task_list.clear()
        if not items:
            self.overview_status.setText("还没有学习任务。先到“创建计划”设定目标和日期。")
            empty_item = QListWidgetItem("暂无任务。打开“创建计划”开始。")
            self.overview_task_list.addItem(empty_item)
            create_plan = QPushButton("打开创建计划")
            create_plan.setObjectName("primaryButton")
            create_plan.clicked.connect(self._open_create_plan)
            self.overview_task_list.setItemWidget(empty_item, create_plan)
            return
        self.overview_status.setText(f"共有 {len(items)} 个任务，其中 {len(active)} 个正在学习。")
        for item in items[:5]:
            overview_item, overview_widget = self._task_card(item, overview=True)
            self.overview_task_list.addItem(overview_item)
            self.overview_task_list.setItemWidget(overview_item, overview_widget)

    def _restore_provider(self) -> None:
        # The preset file is the durable source of truth in the local build.
        # QSettings remains a compatibility fallback for older installations.
        try:
            payload = self.api.list_provider_presets()
            presets = payload.get("presets", []) if isinstance(payload, dict) else []
            preferred_names = {
                str(self.settings.value("provider/preset_name", "默认模型预设") or "默认模型预设"),
                self.PROVIDER_PRESET_NAME,
            }
            saved = next(
                (
                    item
                    for item in presets
                    if str(item.get("name") or "") in preferred_names
                    and isinstance(item.get("form"), dict)
                ),
                next((item for item in presets if isinstance(item.get("form"), dict)), None),
            )
            if saved:
                form = saved["form"]
                self.provider_preset_name.setText(str(saved.get("name") or "默认模型预设"))
                self.provider_base_url.setText(str(form.get("base_url") or self.DEEPSEEK_BASE_URL))
                self.provider_model.setText(str(form.get("model") or self.DEEPSEEK_FLASH_MODEL))
                credential_ref = str(form.get("credential_ref") or "")
                self.settings.setValue("provider/base_url", self.provider_base_url.text())
                self.settings.setValue("provider/model", self.provider_model.text())
                self.settings.setValue("provider/preset_name", self.provider_preset_name.text().strip() or "默认模型预设")
                self.settings.setValue("provider/credential_ref", credential_ref)
                self.settings.sync()
                self.provider_status.setText("已加载保存的模型预设；地址、模型名称和状态会保留在这里。")
            else:
                credential_ref = str(self.settings.value("provider/credential_ref", "") or "")
        except ApiError:
            credential_ref = str(self.settings.value("provider/credential_ref", "") or "")
        if credential_ref and self.provider_base_url.text().strip() and self.provider_model.text().strip():
            self._connect_provider()

    def _provider_form_with_credential(self) -> tuple[dict[str, str] | None, str]:
        base_url = self.provider_base_url.text().strip()
        model = self.provider_model.text().strip()
        credential_ref = str(self.settings.value("provider/credential_ref", "") or "")
        secret = self.provider_secret.text()
        if not base_url or not model:
            self.provider_status.setText("请填写 Base URL 和模型名称。")
            return None, ""
        if secret:
            credential_ref = store_secret(secret, credential_ref or None)
        if not credential_ref:
            self.provider_status.setText("请先输入 API Key，或使用已有的已保存凭据。")
            return None, ""
        return {
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": model,
            "credential_ref": credential_ref,
        }, credential_ref

    def _remember_provider_form(self, credential_ref: str) -> None:
        self.settings.setValue("provider/base_url", self.provider_base_url.text().strip())
        self.settings.setValue("provider/model", self.provider_model.text().strip())
        self.settings.setValue("provider/preset_name", self.provider_preset_name.text().strip() or "默认模型预设")
        self.settings.setValue("provider/credential_ref", credential_ref)
        self.settings.sync()

    def _provider_preset_name(self) -> str:
        value = self.provider_preset_name.text().strip()
        return value or "默认模型预设"

    def _save_provider_preset(self) -> None:
        try:
            form, credential_ref = self._provider_form_with_credential()
            if form is None:
                return
            saved = self.api.save_provider_preset(self._provider_preset_name(), form)
            self._remember_provider_form(credential_ref)
            directory = str(saved.get("directory") or "E:\\MentorFlowData\\presets")
            self.provider_status.setText(
                "预设已保存，下次打开会自动加载。\n"
                f"模型：{form['model']}\n"
                f"本地目录：{directory}"
            )
        except CredentialStoreError as exc:
            self.provider_status.setText(f"凭据库失败：{exc.code}：{exc.message}")
        except ApiError as exc:
            self.provider_status.setText(f"预设保存失败：{exc.code}：{exc.message}")
        finally:
            self.provider_secret.clear()

    def _connect_provider(self) -> None:
        try:
            form, credential_ref = self._provider_form_with_credential()
            if form is None:
                return
            created = self.api.create_provider_session(
                provider=form["provider"],
                base_url=form["base_url"],
                model=form["model"],
                credential_ref=credential_ref,
            )
            result = self.api.test_provider_session(str(created["provider_session_id"]))
            self.provider_session_id = str(result["provider_session_id"])
            self._remember_provider_form(credential_ref)
            # A successful connection also updates the durable preset, so a
            # user who chooses the one-click action never has to re-enter it.
            self.api.save_provider_preset(self._provider_preset_name(), form)
            self.provider_status.setText(
                "连接成功\n"
                f"主机：{result.get('base_url_host') or '未知'}\n"
                f"服务端模型：{result.get('model_reported') or result.get('model_requested') or form['model']}\n"
                f"延迟：{result.get('latency_ms')} ms"
            )
        except CredentialStoreError as exc:
            self.provider_status.setText(f"凭据库失败：{exc.code}：{exc.message}")
        except ApiError as exc:
            self.provider_status.setText(f"连接失败：{exc.code}：{exc.message}")
        finally:
            self.provider_secret.clear()

    def _refresh_tasks(self, _checked: bool = False) -> None:
        items = self._workspace_items(include_archived=bool(getattr(self, "include_archived_plans", None) and self.include_archived_plans.isChecked()))
        self.task_list.clear()
        if not items:
            self.task_list.addItem("暂无任务。先到“创建计划”设定目标和日期。")
            return
        for item in items:
            item_widget, card = self._task_card(item)
            self.task_list.addItem(item_widget)
            self.task_list.setItemWidget(item_widget, card)

    def _open_map_from_plan(self, workspace_id: str) -> None:
        self.pages.setCurrentIndex(2)
        self._refresh_map_workspaces(workspace_id)

    def _refresh_map_workspaces(self, preferred_id: str = "") -> None:
        selected = preferred_id or str(self.map_workspace.currentData() or "")
        try:
            items = self.api.active_workspaces().get("items", [])
        except ApiError as exc:
            self.map_state.setText(f"无法读取学习任务：{exc.message}")
            return
        self.map_workspace.blockSignals(True)
        self.map_workspace.clear()
        for item in items:
            workspace_id = str(item.get("workspace_id") or "")
            if workspace_id:
                self.map_workspace.addItem(str(item.get("title") or workspace_id), workspace_id)
        if selected:
            index = self.map_workspace.findData(selected)
            if index >= 0:
                self.map_workspace.setCurrentIndex(index)
        self.map_workspace.blockSignals(False)
        self._load_selected_map()

    def _load_selected_map(self, _index: int = -1) -> None:
        workspace_id = str(self.map_workspace.currentData() or "")
        if not workspace_id:
            self.map_view.set_graph([], [])
            self.map_candidates.clear()
            self.map_state.setText("当前没有可显示的主动教学任务。")
            return
        try:
            graph = self.api.knowledge_map(workspace_id)
        except ApiError as exc:
            self.map_view.set_graph([], [])
            self.map_candidates.clear()
            self.evidence_text.clear()
            self.map_state.setText(f"此任务还没有活动知识地图。\n{exc.code}: {exc.message}")
            return
        nodes = graph.get("nodes") or graph.get("knowledge_points") or []
        edges = graph.get("edges") or []
        self.map_view.set_graph(nodes, edges)
        revision = graph.get("revision_id") or graph.get("revision") or "未知"
        state = graph.get("state") or graph.get("status") or "未知"
        self.map_state.setText(f"修订：{revision}\n状态：{state}\n节点：{len(nodes)}　关系：{len(edges)}")
        self.evidence_text.setPlainText("单击节点查看证据。")
        self._refresh_candidates(workspace_id)

    def _fit_map(self) -> None:
        scene = self.map_view.scene()
        if scene and not scene.sceneRect().isEmpty():
            self.map_view.fitInView(scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _start_map_job(self) -> None:
        workspace_id = str(self.map_workspace.currentData() or "")
        if not workspace_id:
            self.map_state.setText("请先选择学习任务。")
            return
        if not self.provider_session_id:
            self.map_state.setText("真实模型尚未验证。请先进入“模型连接”完成连接。")
            return
        try:
            job = self.api.create_knowledge_map_job(workspace_id, self.provider_session_id)
        except ApiError as exc:
            self.map_state.setText(f"无法创建地图任务：{exc.code}：{exc.message}")
            return
        self._map_job_id = str(job.get("id") or job.get("job_id") or "")
        if not self._map_job_id:
            self.map_state.setText("服务没有返回地图任务 ID。")
            return
        self.map_state.setText(f"地图任务已创建：{self._map_job_id}\n状态：{job.get('status') or 'queued'}")
        self._map_job_timer.start()

    def _poll_map_job(self) -> None:
        if not self._map_job_id:
            self._map_job_timer.stop()
            return
        try:
            job = self.api.knowledge_map_job(self._map_job_id)
        except ApiError as exc:
            self._map_job_timer.stop()
            self.map_state.setText(f"地图任务查询失败：{exc.code}：{exc.message}")
            return
        status = str(job.get("status") or "未知")
        dispositions = job.get("dispositions") or []
        self.map_state.setText(
            f"地图任务：{self._map_job_id}\n状态：{status}\n材料处置：{len(dispositions)}"
        )
        if status in {"ready", "ready_with_review"}:
            self._map_job_timer.stop()
            self._load_selected_map()
        elif status in {"failed", "cancelled"}:
            self._map_job_timer.stop()
            error = job.get("error") or {}
            self.map_state.setText(
                f"地图生成{status}：{error.get('code') or ''} {error.get('message') or error}"
            )

    def _show_node_evidence(self, node: dict) -> None:
        if node.get("_kind") == "edge":
            evidence = node.get("evidence") or node.get("source_evidence") or []
            lines = [
                f"关系：{node.get('relation') or 'related'}",
                f"来源节点：{node.get('source_id') or node.get('prerequisite_id') or ''}",
                f"目标节点：{node.get('target_id') or node.get('dependent_id') or ''}",
                f"层：{node.get('layer') or 'material'}",
                f"状态：{node.get('status') or '未知'}",
                "",
                f"关系证据（{len(evidence)}）",
            ]
            for index, item in enumerate(evidence, 1):
                source = item.get("source_file") or item.get("source_id") or item.get("material_id") or "未知来源"
                quote = str(item.get("quote") or item.get("excerpt") or "").strip()
                lines.append(f"{index}. {source}" + (f"：{quote}" if quote else ""))
            self.evidence_text.setPlainText("\n".join(lines))
            return
        lines = [
            str(node.get("name") or "未命名概念"),
            "",
            str(node.get("explanation") or "暂无解释"),
            "",
            f"层：{node.get('layer') or 'material'}",
            f"状态：{node.get('status') or '未知'}",
            f"置信度：{node.get('confidence') if node.get('confidence') is not None else '未知'}",
        ]
        evidence = node.get("evidence") or node.get("source_evidence") or []
        lines.extend(["", f"材料证据（{len(evidence)}）"])
        for index, item in enumerate(evidence, 1):
            source_id = item.get("source_id") or item.get("material_id") or "未知来源"
            quote = str(item.get("quote") or item.get("excerpt") or "").strip()
            location = []
            if item.get("page_number") is not None:
                location.append(f"第 {item['page_number']} 页")
            if item.get("char_start") is not None or item.get("char_end") is not None:
                location.append(f"字符 {item.get('char_start', '?')}–{item.get('char_end', '?')}")
            if item.get("time_start_ms") is not None or item.get("time_end_ms") is not None:
                location.append(f"音频 {item.get('time_start_ms', '?')}–{item.get('time_end_ms', '?')} ms")
            lines.extend(["", f"{index}. 来源：{source_id}"])
            if location:
                lines.append("   位置：" + "；".join(location))
            if quote:
                lines.append("   摘录：" + quote)
            quote_hash = item.get("quote_hash")
            if quote_hash:
                lines.append("   摘录哈希：" + str(quote_hash))
        if not evidence:
            lines.append("该节点没有可展示的材料证据；若属于模型常识层，它默认不参与计划。")
        self.evidence_text.setPlainText("\n".join(lines))

    def _refresh_candidates(self, workspace_id: str) -> None:
        self.map_candidates.clear()
        try:
            candidates = self.api.knowledge_map_candidates(workspace_id).get("candidates", [])
        except ApiError as exc:
            self.map_state.setText(self.map_state.text() + f"\n候选读取失败：{exc.code}")
            return
        for candidate in candidates:
            if str(candidate.get("status") or "pending") != "pending":
                continue
            payload = candidate.get("payload") or {}
            candidate_type = str(candidate.get("candidate_type") or "candidate")
            label = (
                payload.get("name")
                or payload.get("label")
                or payload.get("relation")
                or candidate.get("candidate_key")
                or candidate.get("id")
            )
            item = QListWidgetItem(f"{candidate_type} · {label}")
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            self.map_candidates.addItem(item)
        if self.map_candidates.count() == 0:
            self.map_candidates.addItem("没有待审核候选")

    def _show_candidate(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        candidate = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(candidate, dict):
            return
        payload = candidate.get("payload") or {}
        lines = [
            f"候选类型：{candidate.get('candidate_type') or '未知'}",
            f"候选键：{candidate.get('candidate_key') or ''}",
            f"状态：{candidate.get('status') or 'pending'}",
            "",
            "候选内容：",
        ]
        for key, value in payload.items():
            lines.append(f"{key}：{value}")
        evidence = candidate.get("evidence") or []
        lines.extend(["", f"证据条数：{len(evidence)}"])
        for index, item in enumerate(evidence, 1):
            if isinstance(item, dict):
                lines.append(
                    f"{index}. {item.get('source_file') or item.get('source_id') or item.get('material_id') or '未知来源'}"
                    + (f"：{item.get('quote')}" if item.get("quote") else "")
                )
        self.evidence_text.setPlainText("\n".join(lines))

    def _decide_candidate(self, decision: str) -> None:
        workspace_id = str(self.map_workspace.currentData() or "")
        current = self.map_candidates.currentItem()
        candidate = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not workspace_id or not isinstance(candidate, dict):
            self.map_state.setText("请选择一个待审核候选。")
            return
        try:
            result = self.api.decide_knowledge_map_candidate(
                workspace_id,
                str(candidate.get("id") or ""),
                decision=decision,
                reason=self.candidate_reason.text().strip(),
            )
        except ApiError as exc:
            self.map_state.setText(f"候选处理失败：{exc.code}：{exc.message}")
            return
        self.candidate_reason.clear()
        self.map_state.setText(
            f"候选已{'确认' if decision == 'confirmed' else '驳回'}，新修订：{result.get('revision') or result.get('revision_id')}"
        )
        self._load_selected_map()

    def _logout(self) -> None:
        try:
            self.api.logout()
        finally:
            self.close()
