from __future__ import annotations

from .test_device_profile import create_settings

from .test_device_profile import create_web_profile

import json
import hashlib
import os
import math
import re
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import QDate, QSettings, Qt, QTimer, QUrl, QPoint, QPointF, QRect, QSize, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QPainter, QPen, QPolygonF, QTextCharFormat
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QApplication,
    QDialog,
    QDialogButtonBox,
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
    QScrollArea,
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
from .account_identity import (
    AccountCredentialVault,
    AccountIdentity,
    AccountPreferenceStore,
    AccountProfile,
    ProfileMode,
)
from .account_settings_page import AccountSettingsPage
from .analogy_history import analogy_settings, analogy_ticket, analogy_ticket_current
from .account_vault import AccountVaultManager, VaultLayout
from .local_asset_lifecycle import LocalAssetLifecycle
from .local_asset_page import LocalAssetPage
from .knowledge_map_view import KnowledgeMapView
from .materials_page import MaterialsPage
from .plans_page import PlanCalendar, PlansPage, flattened_tasks
from .classroom_page import ClassroomPage
from .system_page import SystemPage
from .admin_overview_page import AdminOverviewPage, admin_role_visible
from .official_feed import OfficialFeedService, OfficialUpdate
from .version import version_display
from .web_links import install_external_link_handler
from .runtime import (
    AccountSwitchCoordinator,
    active_local_service,
    relaunch_current_desktop,
)
from backend.app.credential_store import CredentialStoreError, store_secret


TASK_THEMES = {
    "sage": {"label": "鼠尾草绿", "background": "#edf6f0", "accent": "#4b896f", "symbol": "✦"},
    "pink": {"label": "樱花粉", "background": "#fff0f5", "accent": "#d2678d", "symbol": "✿"},
    "blue": {"label": "晴空蓝", "background": "#eef5ff", "accent": "#5b82c4", "symbol": "◌"},
    "amber": {"label": "暖阳橙", "background": "#fff6e8", "accent": "#c88842", "symbol": "☼"},
    "lavender": {"label": "薰衣草紫", "background": "#f4efff", "accent": "#8068b7", "symbol": "✧"},
    "mint": {"label": "薄荷青", "background": "#eaf8f6", "accent": "#3b9a91", "symbol": "❋"},
}


class ClickableCard(QFrame):
    """A keyboard and mouse accessible card used for user-owned actions."""

    activated = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit()
            event.accept()
            return
        super().keyPressEvent(event)


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
        title_label.setObjectName("taskCardTitle")
        title_label.setMinimumWidth(0)
        title_label.setWordWrap(True)
        title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
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


class ResponsiveCardList(QListWidget):
    """Keep embedded cards fitted to the live viewport, including after resize.

    ``QListWidgetItem.setSizeHint(widget.sizeHint())`` freezes the width that a
    card happened to request when it was first rendered.  That is why a card
    generated in a wide window remained wide after the learner resized the
    application.  Recompute the row width from the current viewport instead.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Embedded cards must never force a second, horizontal scrolling lane.
        # The viewport already accounts for the vertical bar, so rows are
        # sized to that live width and the list keeps only the useful vertical
        # scroll affordance.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QTimer.singleShot(0, self.fit_cards_to_viewport)

    def fit_cards_to_viewport(self) -> None:
        viewport_width = max(0, self.viewport().width())
        # QListWidget's item margin and frame can otherwise make an embedded
        # card a handful of pixels wider than the viewport. Keep a small
        # breathing gap while preserving the minimum usable card width.
        width = max(220, viewport_width - 12)
        if viewport_width and width > viewport_width:
            width = viewport_width
        for row in range(self.count()):
            item = self.item(row)
            card = self.itemWidget(item)
            if card is None:
                continue
            card.setMinimumWidth(0)
            card.setMaximumWidth(width)
            card.resize(width, card.height())
            height = max(124, card.minimumSizeHint().height(), card.sizeHint().height())
            item.setSizeHint(QSize(width, height))
        self.updateGeometries()


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
        # Keep the guide inside the overlay at the supported minimum size and
        # on high-DPI displays where font metrics can make the natural card
        # wider than the available viewport.
        available_width = max(1, self.width() - 40)
        if not self.isVisible():
            # The constructor runs before the overlay receives its parent
            # geometry.  Keep a usable provisional size until showEvent.
            width = min(self.card.maximumWidth(), max(420, self.card.sizeHint().width()))
        else:
            width = min(self.card.maximumWidth(), max(280, available_width))
        natural_height = self.card.sizeHint().height()
        if not self.isVisible():
            # A hidden child reports the default 30px parent height; do not
            # collapse the card while waiting for the real overlay resize.
            height = max(160, natural_height)
        else:
            available_height = max(1, self.height() - 40)
            height = min(natural_height, available_height)
        self.card.resize(width, height)
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

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._position_card()

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

    @classmethod
    def _normalized_provider_model(cls, base_url: str, model: str) -> str:
        """Upgrade only DeepSeek's retired aliases; preserve every custom provider model."""

        host = (urlparse(str(base_url)).hostname or "").casefold()
        value = str(model or "").strip()
        if host == "api.deepseek.com" and value.casefold() in {
            "deepseek-chat",
            "deepseek-reasoner",
        }:
            return cls.DEEPSEEK_FLASH_MODEL
        return value

    def __init__(
        self,
        api: DesktopApi,
        user: dict,
        origin: str,
        *,
        account_profile: AccountProfile | None = None,
        account_vault_manager: AccountVaultManager | None = None,
        local_asset_lifecycle: LocalAssetLifecycle | None = None,
        bootstrap_snapshot: object | None = None,
        open_recovery_center=None,
        return_to_login_gate=None,
rehydration_session=None,
        admin_transport=None,
    ):
        super().__init__()
        self.api = api
        self.user = user
        self.origin = origin
        self.is_super_admin = str(user.get("role") or user.get("application_role") or "").upper() == "SUPER_ADMIN"
        self.admin_transport = admin_transport
        self.admin_enabled = admin_role_visible(user, self.admin_transport)
        self.settings = create_settings(QSettings, "MentorFlow", "MentorFlow")
        self.account_preferences = AccountPreferenceStore(self.settings)
        if account_profile is not None:
            self.account_profile = account_profile
        else:
            try:
                self.account_profile = self.account_preferences.load()
            except ValueError:
                self.account_preferences.clear()
                self.account_profile = AccountProfile.personal_local()
        self.account_vault_manager = account_vault_manager or AccountVaultManager(
            VaultLayout.from_env()
        )
        if rehydration_session is not None:
            from .product_settings import AccountProductSettings
            self.settings = AccountProductSettings(self.settings, self.api,
                lambda message: self.statusBar().showMessage(message))
        if (
            self.account_vault_manager.current is None
            or self.account_vault_manager.current.profile != self.account_profile
        ):
            self.account_vault_manager.mount(self.account_profile)
        self.local_asset_lifecycle = local_asset_lifecycle or LocalAssetLifecycle(
            self.account_vault_manager.current,
            account_identity=self.user,
        )
        self.bootstrap_snapshot = bootstrap_snapshot
        self._rehydration_session = rehydration_session
        self._open_recovery_center_callback = open_recovery_center
        self._return_to_login_gate_callback = return_to_login_gate
        self.account_credential_vault = AccountCredentialVault()
        self._account_switch_in_progress = False
        self._initialize_analogy_policy()
        self.provider_session_id = ""
        self._fact_index = 0
        self._tutorial_overlay: SpotlightTutorial | None = None
        self._official_feed_service = OfficialFeedService()
        self._official_updates: list[OfficialUpdate] = []
        self._official_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mentorflow-official-updates",
        )
        self._analogy_executor = ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="mentorflow-life-analogy",
        )
        self._official_refresh_future: Future[list[OfficialUpdate]] | None = None
        self._personalized_rank_future: Future[dict] | None = None
        self._analogy_prefetch_future: Future[dict] | None = None
        self._analogy_prefetch_futures: set[Future[dict]] = set()
        self._analogy_prefetch_context = ""
        self._analogy_prefetch_generation = 0
        self._analogy_prefetch_retry_count = 0
        self._analogy_click_pending = False
        self._analogy_button_feedback = ""
        self._analogy_button_feedback_token = 0
        self._provider_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mentorflow-provider")
        self._provider_connect_future: Future[dict] | None = None
        self._news_ai_ranked = False
        self._life_example: dict[str, str] = self._load_cached_life_example()
        self._analogy_recent_cards = self._load_recent_analogy_cards()
        self._analogy_card_library = self._load_analogy_card_library()
        self._analogy_library_cursor = 0
        loaded_prefetch = self._load_prefetched_life_examples()
        self._analogy_prefetch_queue: list[dict[str, object]] = loaded_prefetch
        self._analogy_prefetched = loaded_prefetch[0] if loaded_prefetch else {}
        self._workspace_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mentorflow-workspaces")
        self._workspace_refresh_future: Future[list[dict]] | None = None
        self._workspace_refresh_token = 0
        self._overview_workspace_future: Future[list[dict]] | None = None
        self._overview_workspace_token = 0
        self._workspace_cache: dict[bool, list[dict]] = {}
        self._overview_plan_future: Future[dict] | None = None
        self._overview_plan_token = 0
        self._workspace_action_future: Future[dict] | None = None
        self._workspace_action_context: dict[str, object] = {}
        self.setWindowTitle("MentorFlow")
        self.resize(1480, 940)
        self.setMinimumSize(1100, 720)
        shell = QWidget()
        self.setCentralWidget(shell)
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

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
        settings_label = "设置"
        navigation_items = (
            ("概览", 0),
            ("我的计划", 1),
            ("资料管理", 3),
            ("本地资料", 12),
            ("原生课堂", 5),
            ("创建计划", 6),
            ("模型连接", 8),
            (settings_label, 9),
            ("账号与数据", 10),
            ("更多工具", 11),
        )
        if self.admin_enabled:
            navigation_items += (("管理员概览", 13),)
        self._navigation_page_indices = [page_index for _label, page_index in navigation_items]
        for label, _page_index in navigation_items:
            QListWidgetItem(label, self.navigation)
        self.navigation.currentRowChanged.connect(self._navigate)
        side.addWidget(self.navigation, 1)
        self.device_recovery_button = QPushButton()
        self.device_recovery_button.setObjectName("deviceRecoveryBanner")
        self.device_recovery_button.setAccessibleName("打开本机资料恢复")
        self.device_recovery_button.setVisible(False)
        if self._open_recovery_center_callback is not None:
            self.device_recovery_button.clicked.connect(
                self._open_recovery_center_callback
            )
        side.addWidget(self.device_recovery_button)
        account = QLabel(
            "Personal Local\n本机独立数据"
            if self.account_profile.mode is ProfileMode.PERSONAL_LOCAL
            else f"Enterprise Account\n{self.account_profile.safe_account_label}"
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
            self.classroom_page.apply_theme,
        )
        self.system_page.analogy_history_settings = analogy_settings(self)
        self.pages.addWidget(self.system_page)
        self.account_settings_page = AccountSettingsPage(
            profile=self.account_profile,
            vault_manager=self.account_vault_manager,
            credential_vault=self.account_credential_vault,
            switch_account=self._switch_enterprise_account,
            logout_account=self._logout_enterprise_account,
            begin_account_switch=(
                self._begin_account_switch_at_login_gate
                if self._return_to_login_gate_callback is not None
                else None
            ),
            open_recovery_center=self._open_recovery_center_callback,
            change_password=self._change_enterprise_password,
        )
        self.pages.addWidget(self.account_settings_page)
        self.pages.addWidget(self._more_tools())
        if self._rehydration_session is not None:
            from .product_asset_metadata import bind_vault_metadata, resource_rows
            current_vault = self.account_vault_manager.current
            bind_vault_metadata(current_vault,self.api)
            def relink_synced_resource(key,path):
                resources = self.api.request('GET','/api/product-resources')
                resource = next(item for item in resources if item['id']==key)
                return self.local_asset_lifecycle.bind_resource(key,path,fingerprint_sha256=resource['sha256'])
            self.local_asset_page = LocalAssetPage(self.local_asset_lifecycle,
                list_resources=lambda: resource_rows(current_vault,self.local_asset_lifecycle,self.api),
                relink_resource=relink_synced_resource)
        else:
            self.local_asset_page = LocalAssetPage(self.local_asset_lifecycle)
        self.pages.addWidget(self.local_asset_page)
        self.admin_overview_page = None
        if self.admin_enabled:
            self.admin_overview_page = AdminOverviewPage(self.admin_transport)
            self.pages.addWidget(self.admin_overview_page)
        self.set_device_recovery_snapshot(self.bootstrap_snapshot)
        layout.addWidget(self.pages, 1)
        self.navigation.setCurrentRow(0)
        QTimer.singleShot(0, self._refresh_overview)
        if os.getenv("MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE", "") != "1":
            QTimer.singleShot(0, self._restore_provider)
        QTimer.singleShot(250, self._maybe_start_tutorial)
        # Recommendations change only after an explicit user action.  There is
        # intentionally no timer and no network request during normal launch.
        self._official_updates = self._official_feed_service.load_cached()
        self._render_fact()

    def set_device_recovery_snapshot(self, snapshot: object | None) -> None:
        self.bootstrap_snapshot = snapshot
        resources = tuple(getattr(snapshot, "resources", ()) or ())
        missing = sum(
            1
            for item in resources
            if str(getattr(getattr(item, "state", ""), "value", getattr(item, "state", "")))
            == "MISSING_ON_THIS_DEVICE"
        )
        self.account_settings_page.set_resource_device_states(resources if snapshot is not None else None)
        if missing and self._open_recovery_center_callback is not None:
            self.device_recovery_button.setText(f"需要恢复 {missing} 项资料")
            self.device_recovery_button.setVisible(True)
        else:
            self.device_recovery_button.setVisible(False)

    def _change_enterprise_password(self, current_password: str, new_password: str) -> None:
        transport = self._rehydration_session
        if transport is None:
            # AccountBootstrapResult owns the live session; snapshots do not.
            raise RuntimeError("AUTHENTICATED_SESSION_REQUIRED")
        response = transport.post(
            "/api/enterprise/auth/lifecycle/change-password",
            json={"current_password": current_password, "new_password": new_password},
        )
        response.raise_for_status()

    def _begin_account_switch_at_login_gate(self) -> None:
        callback = self._return_to_login_gate_callback
        if callback is not None:
            callback("switch")

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
            self.navigation.setCurrentRow(5)
        elif section == "resources":
            self.navigation.setCurrentRow(2)
        elif section == "data":
            # Preserve the existing System settings callback contract.  The
            # account Vault page has its own explicit sidebar destination.
            self.navigation.setCurrentRow(6)
        else:
            self.navigation.setCurrentRow(6)

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
        QTimer.singleShot(0, self._sync_overview_geometry)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._close_current_account_handles()
        self.account_vault_manager.unmount()
        super().closeEvent(event)

    def _stop_current_account_tasks(self) -> None:
        self._seal_analogy_history()
        service = active_local_service()
        if service is not None:
            service.stop()

    def _close_current_account_handles(self) -> None:
        self._seal_analogy_history()
        for name in (
            "_official_executor",
            "_analogy_executor",
            "_provider_executor",
            "_workspace_executor",
        ):
            executor = getattr(self, name, None)
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
        for view in self.findChildren(QWebEngineView):
            view.stop()
        for profile in self.findChildren(QWebEngineProfile):
            try:
                profile.cookieStore().deleteAllCookies()
                profile.clearHttpCache()
            except RuntimeError:
                pass
        try:
            self.api.close()
        except RuntimeError:
            pass

    def _clear_account_ui_and_runtime_cache(self) -> None:
        self.provider_session_id = ""
        for name in (
            "_workspace_cache",
            "_official_updates",
            "_analogy_prefetch_queue",
            "_analogy_recent_cards",
            "_analogy_card_library",
        ):
            value = getattr(self, name, None)
            if hasattr(value, "clear"):
                value.clear()
        self._analogy_prefetched = {}
        self._analogy_prefetch_context = ""
        self._workspace_refresh_token += 1
        self._overview_workspace_token += 1
        for key in (
            "provider/credential_ref",
            "provider/base_url",
            "provider/model",
            "news/blocked",
            "development/teacher_action_reviews_v1",
        ):
            self.settings.remove(key)
        self.settings.sync()
        for widget_type in (QListWidget, QTableWidget):
            for widget in self.findChildren(widget_type):
                widget.clearSelection()
        for dialog in self.findChildren(QDialog):
            dialog.close()

    def _current_account_handles_closed(self) -> bool:
        service = active_local_service()
        service_closed = service is None or service.wait_until_stopped()
        api_closed = bool(getattr(self.api.client, "is_closed", False))
        return service_closed and api_closed and self.account_vault_manager.current is None

    def _quit_for_account_switch(self) -> None:
        self.close()
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def _account_switch_coordinator(self) -> AccountSwitchCoordinator:
        return AccountSwitchCoordinator(
            vault_manager=self.account_vault_manager,
            preferences=self.account_preferences,
            stop_current_tasks=self._stop_current_account_tasks,
            close_current_handles=self._close_current_account_handles,
            clear_ui_and_runtime_cache=self._clear_account_ui_and_runtime_cache,
            quit_current_window=self._quit_for_account_switch,
            wait_for_handles_closed=self._current_account_handles_closed,
            relaunch_desktop=relaunch_current_desktop,
        )

    def _switch_enterprise_account(self, identity: AccountIdentity):
        if self._account_switch_in_progress:
            raise RuntimeError("account switch already in progress")
        self._account_switch_in_progress = True
        try:
            return self._account_switch_coordinator().switch(identity)
        except Exception:
            self._account_switch_in_progress = False
            raise

    def _logout_enterprise_account(self) -> None:
        if self.account_profile.mode is ProfileMode.PERSONAL_LOCAL:
            return
        try:
            if self._return_to_login_gate_callback is not None:
                self._return_to_login_gate_callback("logout")
                return
            self.account_credential_vault.logout(self.account_profile)
            self._account_switch_in_progress = True
            self._account_switch_coordinator().switch_to_profile(
                AccountProfile.personal_local(),
                initial_event="logout_current_enterprise_account",
            )
        except (CredentialStoreError, OSError, RuntimeError, ValueError):
            self._account_switch_in_progress = False
            QMessageBox.warning(
                self,
                "无法安全退出",
                "当前账号的安全凭据或本机句柄未能完整清除。",
            )

    def _next_fact(self) -> None:
        keywords = str(
            analogy_settings(self).value("analogy/keywords", "")
            or analogy_settings(self).value("news/interests", "")
            or ""
        ).strip()
        if not keywords:
            self._open_news_preferences()
            return
        if not getattr(self, "provider_session_id", ""):
            # Keep the action usable even before a provider is connected, but
            # explain the next step in learner-facing language.
            self._set_analogy_button_feedback("先连接模型", 900)
            return
        # The click path is deliberately local-only. A ready card swaps in
        # immediately; otherwise remember the intent and let the hidden
        # one-slot request serve it as soon as it completes.
        if self._consume_analogy_prefetch():
            self._set_analogy_button_feedback("已换一张", 650)
            return
        # A learner can click faster than any remote model can answer.  Cycle
        # through already generated, context-matched cards immediately while
        # the hidden refill continues; never make repeated clicks wait on the
        # network after the first small library has been built.
        if self._consume_analogy_library():
            self._set_analogy_button_feedback("已换一张", 650)
            if hasattr(self, "_analogy_executor"):
                self._schedule_analogy_prefetch()
            return
        pending_futures = set(getattr(self, "_analogy_prefetch_futures", set()))
        pending = any(not future.done() for future in pending_futures) or bool(
            getattr(self, "_analogy_prefetch_future", None)
            and not self._analogy_prefetch_future.done()
        )
        context = self._analogy_context_key()
        if pending and str(getattr(self, "_analogy_prefetch_context", "")) == context:
            self._analogy_click_pending = True
            self._set_analogy_button_feedback("正在加载…", 900)
            return
        # Lightweight callers can be created without the runtime executor;
        # request the hidden refill when the real window is available.
        self._analogy_click_pending = True
        self._set_analogy_button_feedback("正在加载…", 900)
        if hasattr(self, "_analogy_executor"):
            self._schedule_analogy_prefetch()

    def _set_analogy_button_feedback(self, text: str, duration_ms: int = 800) -> None:
        """Show brief, non-technical feedback without disabling the action."""
        ticket = analogy_ticket(self)
        if not analogy_ticket_current(self, ticket):
            return

        if not hasattr(self, "next_fact_button"):
            return
        self._analogy_button_feedback_token = int(
            getattr(self, "_analogy_button_feedback_token", 0)
        ) + 1
        token = self._analogy_button_feedback_token
        self._analogy_button_feedback = str(text or "")
        self.next_fact_button.setText(self._analogy_button_feedback or "换一张")

        def clear_feedback() -> None:
            if not analogy_ticket_current(self, ticket) or token != int(getattr(self, "_analogy_button_feedback_token", 0)):
                return
            self._analogy_button_feedback = ""
            self._update_analogy_button_state()

        QTimer.singleShot(max(0, int(duration_ms)), clear_feedback)

    def _load_recent_analogy_cards(self) -> list[dict[str, str]]:
        """Load structured long-term memory used to prevent concept/template repetition."""

        raw = str(analogy_settings(self).value("analogy/recent_cards", "") or "")
        try:
            values = json.loads(raw) if raw else []
        except (TypeError, ValueError, json.JSONDecodeError):
            values = []
        if not isinstance(values, list):
            return []
        records: list[dict[str, str]] = []
        for value in values[-48:]:
            if isinstance(value, dict):
                record = {
                    field: str(value.get(field) or "").strip()[:500]
                    for field in (
                        "summary",
                        "concept_key",
                        "concept_label",
                        "mechanism",
                        "format_key",
                        "scene_family",
                        "situation_key",
                        "mechanism_tag",
                        "style_mode",
                        "meme_key",
                        "scene_signature",
                    )
                }
            else:
                # Preserve old cards as avoidance evidence, but never display
                # the obsolete cached card after the v3 contract upgrade.
                record = {
                    "summary": str(value or "").strip()[:500],
                    "concept_key": "",
                    "concept_label": "",
                    "mechanism": "",
                    "format_key": "",
                    "scene_family": "",
                    "situation_key": "",
                    "mechanism_tag": "",
                    "style_mode": "",
                    "meme_key": "",
                    "scene_signature": "",
                }
            if record["summary"]:
                records.append(record)
        return records[-48:]

    def _initialize_analogy_policy(self) -> None:
        self._analogy_policy_version = 'v12'
        store = analogy_settings(self)
        self._analogy_legacy_policy_mismatch = False
        if not store.active or str(store.value('analogy/generation_policy', '')) == self._analogy_policy_version:
            return
        if store.owner == 'personal-local':
            # Retain unmarked bytes, but do not revive cards from an obsolete
            # quality policy. Advance the policy only after a new accepted card.
            self._analogy_legacy_policy_mismatch = True
        else:
            store.setValue('analogy/generation_policy', self._analogy_policy_version)
            for key in ('analogy/latest_card', 'analogy/prefetched_card', 'analogy/card_library'):
                store.remove(key)
        store.setValue('analogy/variation_counter', -1)
        self.settings.sync()

    def _load_cached_life_example(self) -> dict[str, str]:
        """Restore the last good card so refresh never leaves a blank panel."""

        if getattr(self, '_analogy_legacy_policy_mismatch', False):
            return {}

        raw = str(analogy_settings(self).value("analogy/latest_card", "") or "")
        try:
            value = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            return {}
        if value.get("schema") != "mentorflow.life-analogy.v7":
            return {}
        card = {
            field: str(value.get(field) or "").strip()
            for field in ("title", "analogy", "takeaway")
        }
        return card if card["title"] and card["analogy"] else {}

    def _remember_life_example(
        self,
        card: dict[str, object],
        memory: dict[str, object] | None = None,
    ) -> None:
        if not analogy_settings(self).active:
            return
        cached_card = {
            field: str(card.get(field) or "").strip()
            for field in ("title", "analogy", "takeaway")
        }
        if cached_card["title"] and cached_card["analogy"]:
            analogy_settings(self).setValue('analogy/generation_policy', getattr(self, '_analogy_policy_version', 'v12'))
            self._analogy_legacy_policy_mismatch = False
            analogy_settings(self).setValue(
                "analogy/latest_card",
                json.dumps(
                    {"schema": "mentorflow.life-analogy.v7", **cached_card},
                    ensure_ascii=False,
                ),
            )
        summary = "｜".join(
            str(card.get(field) or "").strip()
            for field in ("title", "analogy", "takeaway")
            if str(card.get(field) or "").strip()
        )[:260]
        if not summary:
            return
        memory = memory or {}
        record = {
            "summary": summary,
            "concept_key": str(memory.get("concept_key") or "").strip()[:80],
            "concept_label": str(memory.get("concept_label") or "").strip()[:80],
            "mechanism": str(memory.get("mechanism") or "").strip()[:240],
            "format_key": str(memory.get("format_key") or "").strip()[:80],
            "scene_family": str(memory.get("scene_family") or "").strip()[:80],
            "situation_key": str(memory.get("situation_key") or "").strip()[:80],
            "mechanism_tag": str(memory.get("mechanism_tag") or "").strip()[:80],
            "style_mode": str(memory.get("style_mode") or "").strip()[:40],
            "meme_key": str(memory.get("meme_key") or "").strip()[:80],
            "scene_signature": str(memory.get("scene_signature") or "").strip()[:160],
        }
        recent_cards = self._load_recent_analogy_cards()
        self._analogy_recent_cards = [
            value
            for value in recent_cards
            if value.get("summary") != summary
        ][-47:] + [record]
        analogy_settings(self).setValue(
            "analogy/recent_cards",
            json.dumps(self._analogy_recent_cards, ensure_ascii=False),
        )
        self.settings.sync()

    def _analogy_context_key(self) -> str:
        if not analogy_settings(self).active:
            return ''
        payload = {
            "account_scope": analogy_settings(self).owner,
            "keywords": str(
                analogy_settings(self).value("analogy/keywords", "")
                or analogy_settings(self).value("news/interests", "")
                or ""
            ).strip(),
            "base_url": str(self.settings.value("provider/base_url", "") or "").strip(),
            "model": str(self.settings.value("provider/model", "") or "").strip(),
            "contract": "mentorflow.life-analogy.v11",
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _load_prefetched_life_examples(self) -> list[dict[str, object]]:
        if getattr(self, '_analogy_legacy_policy_mismatch', False):
            return []
        raw = str(analogy_settings(self).value("analogy/prefetched_card", "") or "")
        try:
            value = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(value, dict):
            return []
        if value.get("schema") == "mentorflow.life-analogy.prefetch.v5":
            candidates = value.get("items") if isinstance(value.get("items"), list) else []
        elif value.get("schema") == "mentorflow.life-analogy.prefetch.v4":
            candidates = [value]
        else:
            return []
        context = self._analogy_context_key()
        accepted: list[dict[str, object]] = []
        for candidate in candidates[:3]:
            if not isinstance(candidate, dict) or str(candidate.get("context") or "") != context:
                continue
            result = candidate.get("result")
            card = result.get("card") if isinstance(result, dict) else None
            if (
                isinstance(card, dict)
                and str(card.get("title") or "").strip()
                and str(card.get("analogy") or "").strip()
                and str(card.get("takeaway") or "").strip()
            ):
                accepted.append(candidate)
        return accepted

    def _load_analogy_card_library(self) -> list[dict[str, object]]:
        """Load reusable provider cards for zero-wait repeated browsing."""

        if getattr(self, '_analogy_legacy_policy_mismatch', False):
            return []

        raw = str(analogy_settings(self).value("analogy/card_library", "") or "")
        try:
            value = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(value, dict) or value.get("schema") != "mentorflow.life-analogy.library.v1":
            return []
        context = self._analogy_context_key()
        accepted: list[dict[str, object]] = []
        for candidate in value.get("items") or []:
            if not isinstance(candidate, dict) or str(candidate.get("context") or "") != context:
                continue
            result = candidate.get("result")
            card = result.get("card") if isinstance(result, dict) else None
            if isinstance(card, dict) and all(
                str(card.get(field) or "").strip()
                for field in ("title", "analogy", "takeaway")
            ):
                accepted.append(candidate)
        return accepted[-12:]

    def _persist_analogy_card_library(self) -> None:
        if not analogy_settings(self).active:
            return
        library = list(getattr(self, "_analogy_card_library", []))[-12:]
        if not library:
            analogy_settings(self).remove("analogy/card_library")
            return
        analogy_settings(self).setValue(
            "analogy/card_library",
            json.dumps(
                {"schema": "mentorflow.life-analogy.library.v1", "items": library},
                ensure_ascii=False,
            ),
        )

    def _remember_analogy_library_item(self, item: dict[str, object]) -> None:
        if not analogy_settings(self).active or not isinstance(item, dict) or item.get('context') != self._analogy_context_key():
            return
        result = item.get("result") if isinstance(item, dict) else None
        card = result.get("card") if isinstance(result, dict) else None
        if not isinstance(card, dict):
            return
        memory = result.get("memory") if isinstance(result, dict) else None
        concept_key = str((memory or {}).get("concept_key") or "").strip().casefold()
        signature = "\u241f".join(
            str(card.get(field) or "").strip() for field in ("title", "analogy", "takeaway")
        )
        library = list(getattr(self, "_analogy_card_library", []))
        if concept_key and any(
            str(
                ((((candidate.get("result") or {}).get("memory") or {}).get("concept_key")) or "")
            ).strip().casefold()
            == concept_key
            for candidate in library
        ):
            return
        library = [
            candidate
            for candidate in library
            if "\u241f".join(
                str(((candidate.get("result") or {}).get("card") or {}).get(field) or "").strip()
                for field in ("title", "analogy", "takeaway")
            ) != signature
        ]
        library.append(item)
        self._analogy_card_library = library[-12:]
        self._persist_analogy_card_library()

    def _consume_analogy_library(self) -> bool:
        if not analogy_settings(self).active:
            return False
        library = list(getattr(self, "_analogy_card_library", []))
        if not library:
            return False
        current_signature = "\u241f".join(
            str(self._life_example.get(field) or "").strip()
            for field in ("title", "analogy", "takeaway")
        )
        start = int(getattr(self, "_analogy_library_cursor", 0)) % len(library)
        for offset in range(len(library)):
            index = (start + offset) % len(library)
            candidate = library[index]
            if str(candidate.get("context") or "") != self._analogy_context_key():
                continue
            result = candidate.get("result")
            card = result.get("card") if isinstance(result, dict) else None
            if not isinstance(card, dict):
                continue
            signature = "\u241f".join(
                str(card.get(field) or "").strip()
                for field in ("title", "analogy", "takeaway")
            )
            if not signature.strip("\u241f") or signature == current_signature:
                continue
            self._analogy_library_cursor = (index + 1) % len(library)
            self._life_example = {
                field: str(card.get(field) or "").strip()
                for field in ("title", "analogy", "takeaway")
            }
            self._render_fact()
            self._update_analogy_button_state()
            return True
        return False

    def _load_prefetched_life_example(self) -> dict[str, object]:
        """Compatibility accessor for focused callers that expect one buffered card."""

        values = self._load_prefetched_life_examples()
        return values[0] if values else {}

    def _persist_analogy_prefetch_queue(self) -> None:
        if not analogy_settings(self).active:
            return
        queue = list(getattr(self, "_analogy_prefetch_queue", []))[:3]
        if queue:
            analogy_settings(self).setValue(
                "analogy/prefetched_card",
                json.dumps(
                    {"schema": "mentorflow.life-analogy.prefetch.v5", "items": queue},
                    ensure_ascii=False,
                ),
            )
        else:
            analogy_settings(self).remove("analogy/prefetched_card")
        self.settings.sync()

    def _clear_analogy_prefetch(self) -> None:
        if not analogy_settings(self).active:
            return
        self._analogy_prefetch_generation = int(getattr(self, "_analogy_prefetch_generation", 0)) + 1
        futures = set(getattr(self, "_analogy_prefetch_futures", set()))
        legacy_future = getattr(self, "_analogy_prefetch_future", None)
        if legacy_future is not None:
            futures.add(legacy_future)
        for future in futures:
            if not future.done():
                future.cancel()
        personalized_future = getattr(self, "_personalized_rank_future", None)
        if personalized_future is not None and not personalized_future.done():
            personalized_future.cancel()
        self._analogy_prefetch_future = None
        self._analogy_prefetch_futures = set()
        self._personalized_rank_future = None
        self._analogy_prefetch_context = ""
        self._analogy_prefetch_retry_count = 0
        self._analogy_click_pending = False
        self._analogy_prefetched = {}
        self._analogy_prefetch_queue = []
        self._analogy_card_library = []
        self._analogy_library_cursor = 0
        analogy_settings(self).remove("analogy/prefetched_card")
        analogy_settings(self).remove("analogy/card_library")
        self.settings.sync()
        self._update_analogy_button_state()

    def _seal_analogy_history(self) -> None:
        """Revoke old producers before waiting for API shutdown; preserve disk history."""
        self._analogy_history_closed = True
        self._analogy_prefetch_generation = int(getattr(self, '_analogy_prefetch_generation', 0)) + 1
        self._analogy_button_feedback_token = int(getattr(self, '_analogy_button_feedback_token', 0)) + 1
        for future in set(getattr(self, '_analogy_prefetch_futures', set())):
            future.cancel()

    def _next_analogy_seed(self) -> int:
        variation_seed = int(analogy_settings(self).value("analogy/variation_counter", 0) or 0) + 1
        analogy_settings(self).setValue("analogy/variation_counter", variation_seed)
        self.settings.sync()
        return variation_seed

    def _generate_life_analogy_resilient(
        self,
        provider_session_id: str,
        keywords: str,
        recent_cards: list[dict[str, str]],
        variation_seed: int,
        request_api=None,
        ticket: tuple[str | None, int] | None = None,
    ) -> dict:
        """Fill one buffer slot without turning one slow draft into a 90-second wait."""

        last_error: Exception | None = None
        ticket = ticket if ticket is not None else analogy_ticket(self)
        if not analogy_ticket_current(self, ticket):
            raise RuntimeError('Analogy account context is no longer active')
        request_api = request_api if request_api is not None else self.api
        for _attempt in range(1):
            try:
                result = request_api.generate_life_analogy(
                    provider_session_id,
                    keywords,
                    recent_cards=recent_cards,
                    # Retry the same requested card contract. Provider sampling
                    # may vary the draft, but the requested meme and style must
                    # not silently change after a rejected attempt.
                    variation_seed=variation_seed,
                )
                card = result.get("card") if isinstance(result, dict) else None
                if (
                    not isinstance(card, dict)
                    or not str(card.get("title") or "").strip()
                    or not str(card.get("analogy") or "").strip()
                    or not str(card.get("takeaway") or "").strip()
                ):
                    raise RuntimeError("life analogy response missing card")
                return result
            except Exception as exc:  # worker boundary; UI handles only final failure
                last_error = exc
        raise last_error or RuntimeError("life analogy generation failed")

    def _schedule_analogy_prefetch(self) -> None:
        if not analogy_settings(self).active:
            return
        keywords = str(
            analogy_settings(self).value("analogy/keywords", "")
            or analogy_settings(self).value("news/interests", "")
            or ""
        ).strip()
        context = self._analogy_context_key()
        if not keywords or not getattr(self, "provider_session_id", ""):
            self._update_analogy_button_state()
            return
        if not hasattr(self, "_analogy_executor"):
            # Lightweight callers/tests may construct the window without the
            # runtime executor; leave the existing card untouched.
            self._update_analogy_button_state()
            return
        queue = list(getattr(self, "_analogy_prefetch_queue", []))
        queue = [item for item in queue if str(item.get("context") or "") == context][:3]
        self._analogy_prefetch_queue = queue
        self._analogy_prefetched = queue[0] if queue else {}
        futures = {
            future
            for future in set(getattr(self, "_analogy_prefetch_futures", set()))
            if not future.done()
        }
        self._analogy_prefetch_futures = futures
        if str(getattr(self, "_analogy_prefetch_context", "") or context) != context:
            for future in futures:
                future.cancel()
            futures = set()
            self._analogy_prefetch_futures = futures
            self._analogy_prefetch_generation = int(
                getattr(self, "_analogy_prefetch_generation", 0)
            ) + 1
        generation = int(getattr(self, "_analogy_prefetch_generation", 0))
        self._analogy_prefetch_context = context
        # Keep three ready cards, with at most three Provider requests in
        # flight.  Filling the whole buffer in one background wave prevents a
        # learner from consuming the only ready card before the second request
        # has even started.
        # This is a buffer, not authored content: every card still comes from
        # the verified Provider and the same generic quality contract.
        while len(queue) + len(futures) < 3 and len(futures) < 3:
            future = self._analogy_executor.submit(
                self._generate_life_analogy_resilient,
                self.provider_session_id,
                keywords,
                self._load_recent_analogy_cards(),
                self._next_analogy_seed(),
                self.api,
                analogy_ticket(self),
            )
            futures.add(future)
            self._analogy_prefetch_futures = futures
            self._analogy_prefetch_future = next(iter(futures), None)
            QTimer.singleShot(
                80,
                lambda current=future: self._poll_analogy_prefetch(
                    generation, context, current
                ),
            )
        self._update_analogy_button_state()

    def _poll_analogy_prefetch(
        self,
        generation: int,
        context: str,
        polled_future: Future[dict] | None = None,
    ) -> None:
        # A context switch can leave an already queued Qt callback behind.
        # Reject that callback before it can inspect or clear the replacement
        # future belonging to the new context.
        if (
            not analogy_settings(self).active
            or generation != int(getattr(self, "_analogy_prefetch_generation", 0))
            or context != self._analogy_context_key()
        ):
            return
        future = polled_future or getattr(self, "_analogy_prefetch_future", None)
        if future is None:
            return
        tracked_futures = set(getattr(self, "_analogy_prefetch_futures", set()))
        # A queued Qt callback may arrive after another callback already
        # consumed this Future.  Treat it as stale so one Provider result can
        # never be inserted twice or trigger an extra refill request.
        if polled_future is not None and future not in tracked_futures:
            return
        if not future.done():
            QTimer.singleShot(
                80,
                lambda: self._poll_analogy_prefetch(generation, context, future),
            )
            return
        futures = tracked_futures
        futures.discard(future)
        self._analogy_prefetch_futures = futures
        self._analogy_prefetch_future = next(iter(futures), None)
        try:
            result = future.result()
            card = result.get("card") if isinstance(result, dict) else None
            if (
                not isinstance(card, dict)
                or not str(card.get("title") or "").strip()
                or not str(card.get("analogy") or "").strip()
                or not str(card.get("takeaway") or "").strip()
            ):
                raise RuntimeError("life analogy response missing card")
        except Exception:
            # Keep the last good card (or the normal empty card) visible while
            # retrying the hidden slot.  Never paint a technical error here.
            self._render_fact()
            self._update_analogy_button_state()
            retries = int(getattr(self, "_analogy_prefetch_retry_count", 0)) + 1
            self._analogy_prefetch_retry_count = retries
            if hasattr(self, "_analogy_executor"):
                # Provider output can occasionally miss the quality gate.
                # Refill stays invisible and uses bounded backoff; after six
                # consecutive failures it pauses for one minute instead of
                # creating a tight paid-request loop.
                delay_ms = 60_000 if retries >= 6 else min(15_000, 900 * (2 ** min(retries - 1, 4)))
                if retries >= 6:
                    self._analogy_prefetch_retry_count = 0
                QTimer.singleShot(delay_ms, self._schedule_analogy_prefetch)
            return
        self._analogy_prefetch_retry_count = 0
        prefetched: dict[str, object] = {
            "schema": "mentorflow.life-analogy.prefetch.v4",
            "context": context,
            "result": result,
        }
        queue = list(getattr(self, "_analogy_prefetch_queue", []))
        memory = result.get("memory") if isinstance(result, dict) else None
        concept_key = str((memory or {}).get("concept_key") or "").strip().casefold()
        reserved_keys = {
            str(
                (((candidate.get("result") or {}).get("memory") or {}).get("concept_key") or "")
            ).strip().casefold()
            for candidate in queue + list(getattr(self, "_analogy_card_library", []))
            if isinstance(candidate, dict)
        }
        if concept_key and concept_key in reserved_keys:
            # Parallel prefetch requests can independently choose the same
            # concept.  Do not expose duplicate cards; refill that slot with a
            # new seed in the background.
            QTimer.singleShot(0, self._schedule_analogy_prefetch)
            return
        if len(queue) < 3:
            queue.append(prefetched)
        self._analogy_prefetch_queue = queue
        self._analogy_prefetched = queue[0] if queue else {}
        self._persist_analogy_prefetch_queue()
        self._update_analogy_button_state()
        if getattr(self, "_analogy_click_pending", False):
            self._analogy_click_pending = False
            # Consume on the next event turn so the worker callback remains
            # short and the visible card swaps without a foreground wait.
            QTimer.singleShot(0, self._consume_pending_analogy_prefetch)
        else:
            QTimer.singleShot(0, self._schedule_analogy_prefetch)

    def _consume_pending_analogy_prefetch(self) -> None:
        if self._consume_analogy_prefetch():
            self._set_analogy_button_feedback("已换一张", 650)
        else:
            self._set_analogy_button_feedback("暂时保留这一张", 900)

    def _consume_analogy_prefetch(self) -> bool:
        if not analogy_settings(self).active:
            return False
        queue = list(getattr(self, "_analogy_prefetch_queue", []))
        prefetched = queue[0] if queue else getattr(self, "_analogy_prefetched", {})
        if not prefetched or str(prefetched.get("context") or "") != self._analogy_context_key():
            return False
        result = prefetched.get("result")
        card = result.get("card") if isinstance(result, dict) else None
        memory = result.get("memory") if isinstance(result, dict) else None
        if (
            not isinstance(card, dict)
            or not str(card.get("title") or "").strip()
            or not str(card.get("analogy") or "").strip()
            or not str(card.get("takeaway") or "").strip()
        ):
            return False
        self._analogy_click_pending = False
        if queue:
            queue.pop(0)
        self._analogy_prefetch_queue = queue
        self._analogy_prefetched = queue[0] if queue else {}
        self._analogy_prefetch_retry_count = 0
        self._persist_analogy_prefetch_queue()
        self._remember_analogy_library_item(prefetched)
        self._remember_life_example(card, memory if isinstance(memory, dict) else {})
        self._life_example = {
            "title": str(card.get("title") or ""),
            "analogy": str(card.get("analogy") or ""),
            "takeaway": str(card.get("takeaway") or ""),
        }
        self._render_fact()
        self._update_analogy_button_state()
        QTimer.singleShot(0, self._schedule_analogy_prefetch)
        return True

    def _has_valid_analogy_prefetch(self) -> bool:
        if not analogy_settings(self).active:
            return False
        queue = list(getattr(self, "_analogy_prefetch_queue", []))
        prefetched = queue[0] if queue else getattr(self, "_analogy_prefetched", {})
        if not isinstance(prefetched, dict) or str(prefetched.get("context") or "") != self._analogy_context_key():
            return False
        result = prefetched.get("result")
        card = result.get("card") if isinstance(result, dict) else None
        return bool(
            isinstance(card, dict)
            and str(card.get("title") or "").strip()
            and str(card.get("analogy") or "").strip()
            and str(card.get("takeaway") or "").strip()
        )

    def _update_analogy_button_state(self) -> None:
        if hasattr(self, "next_fact_button"):
            keywords = str(
                analogy_settings(self).value("analogy/keywords", "")
                or analogy_settings(self).value("news/interests", "")
                or ""
            ).strip()
            # The action is always available: with no keywords it opens the
            # preferences, with no provider it explains the next step, and
            # with a pending hidden card it records the learner's intent.
            self.next_fact_button.setEnabled(True)
            if not str(getattr(self, "_analogy_button_feedback", "") or ""):
                self.next_fact_button.setText("换一张")
        if hasattr(self, "analogy_loading_status"):
            # Prefetching is an implementation detail.  Keep the inline status
            # permanently quiet so loading/retry/provider errors never leak
            # into the visible life card.
            self.analogy_loading_status.clear()
            self.analogy_loading_status.setVisible(False)

    def _set_analogy_busy(self, busy: bool, message: str = "") -> None:
        """Compatibility shim; provider work never disables the next action."""
        self._update_analogy_button_state()
        if hasattr(self, "next_fact_button"):
            self.next_fact_button.setEnabled(True)
            if busy:
                self._set_analogy_button_feedback("正在加载…", 900)
        if hasattr(self, "analogy_loading_status"):
            # Keep provider details out of the life card; button feedback is
            # sufficient for this local interaction.
            self.analogy_loading_status.clear()
            self.analogy_loading_status.setVisible(False)

    def _render_fact(self) -> None:
        if not hasattr(self, "offline_fact_title"):
            return
        keywords = str(
            analogy_settings(self).value("analogy/keywords", "")
            or analogy_settings(self).value("news/interests", "")
            or ""
        ).strip()
        if not keywords:
            self.offline_fact_title.setText("把绕口的概念，换成好懂的生活画面")
            self.offline_fact_body.setText("点“设置”写下你最近觉得有点绕的词；这里只展示轻松的小例子，不展示你填了什么。")
            self.offline_fact_source.setText("")
            self._current_fact_url = ""
            self.fact_source_button.setVisible(False)
            self._update_analogy_button_state()
            return
        if analogy_settings(self).active and self._life_example:
            self.offline_fact_title.setText(str(self._life_example.get("title") or "生活小剧场"))
            self.offline_fact_body.setText(str(self._life_example.get("analogy") or ""))
            takeaway = str(self._life_example.get("takeaway") or "").strip()
            self.offline_fact_source.setText(takeaway)
            self._current_fact_url = ""
            self.fact_source_button.setVisible(False)
            self._update_analogy_button_state()
            return
        self.offline_fact_title.setText("准备一张生活小卡片")
        self.offline_fact_body.setText(
            "连接模型后点“换一张”，我会把难懂的说法换成生活里的小场景。"
            if not self.provider_session_id
            else "准备好下一张后，点“换一张”即可更换。"
        )
        self.offline_fact_source.setText("")
        self._current_fact_url = ""
        self.fact_source_button.setVisible(False)
        self._update_analogy_button_state()

    def _preferred_updates(self) -> list[OfficialUpdate]:
        interests = str(analogy_settings(self).value("news/interests", "") or "").strip().casefold()
        blocked = str(self.settings.value("news/blocked", "") or "").strip().casefold()
        blocked_terms = [value for value in re.split(r"[，,、\s]+", blocked) if value]
        interest_terms = [value for value in re.split(r"[，,、\s]+", interests) if value]
        allowed = [
            item for item in self._official_updates
            if not any(term in f"{item.title} {item.publisher}".casefold() for term in blocked_terms)
        ]
        matched = [
            item for item in allowed
            if any(term in f"{item.title} {item.publisher}".casefold() for term in interest_terms)
        ]
        return allowed if self._news_ai_ranked else matched

    def _open_news_preferences(self) -> None:
        ticket = analogy_ticket(self)
        if not analogy_ticket_current(self, ticket):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("生活类比设置")
        dialog.setMinimumWidth(520)
        box = QVBoxLayout(dialog)
        title = QLabel("最近哪些词让你觉得有点绕？")
        title.setObjectName("sectionHeading")
        box.addWidget(title)
        hint = QLabel(
            "写几个词或一句话就行。卡片会围绕这些主题换成一个生活化比喻，"
            "但不会在概览里原样列出你的设置。"
        )
        hint.setWordWrap(True)
        box.addWidget(hint)
        interests = QLineEdit(str(
            analogy_settings(self).value("analogy/keywords", "")
            or analogy_settings(self).value("news/interests", "")
            or ""
        ))
        interests.setPlaceholderText("例如：写下几个你想换种方式理解的词")
        box.addWidget(interests)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并生成")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not analogy_ticket_current(self, ticket):
            return
        value = interests.text().strip()
        if not value:
            QMessageBox.information(self, "还差一步", "请至少填写一个你关注的方向。")
            return
        analogy_settings(self).setValue("analogy/keywords", value)
        self.settings.sync()
        self._clear_analogy_prefetch()
        self._render_fact()
        if self.provider_session_id:
            self._rank_updates_with_provider()
        else:
            QMessageBox.information(self, "还没有连接模型", "设置已经保存。连接模型后，点“换一张”即可生成生活小卡片。")

    def _open_current_fact_source(self) -> None:
        url = str(getattr(self, "_current_fact_url", "") or "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

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

    def _rank_updates_with_provider(self) -> None:
        keywords = str(
            analogy_settings(self).value("analogy/keywords", "")
            or analogy_settings(self).value("news/interests", "")
            or ""
        ).strip()
        if not keywords or not getattr(self, "provider_session_id", ""):
            self._render_fact()
            return
        # Settings changes may request a new hidden buffer, but unlike the
        # click path this is not a card transition and never touches the
        # foreground labels.
        self._schedule_analogy_prefetch()

    def _poll_personalized_rank(
        self,
        generation: int | None = None,
        context: str | None = None,
    ) -> None:
        """Poll the retired foreground request slot as a hidden prefetch.

        Older callers/tests may still populate ``_personalized_rank_future``;
        keep that slot safe by routing it through the same buffer validator
        instead of ever replacing the visible card directly.
        """
        if not analogy_settings(self).active:
            return
        if analogy_settings(self).owner != 'personal-local' and (generation is None or context is None):
            return  # No anonymous legacy callback may adopt the current account.
        future = getattr(self, "_personalized_rank_future", None)
        if future is None:
            return
        if generation is None:
            generation = int(getattr(self, "_analogy_prefetch_generation", 0))
        if context is None:
            context = self._analogy_context_key()
        if (
            generation != int(getattr(self, "_analogy_prefetch_generation", 0))
            or context != self._analogy_context_key()
        ):
            return
        if not future.done():
            QTimer.singleShot(80, lambda: self._poll_personalized_rank(generation, context))
            return
        self._personalized_rank_future = None
        self._analogy_prefetch_future = future
        self._analogy_prefetch_futures = set(
            getattr(self, "_analogy_prefetch_futures", set())
        ) | {future}
        self._analogy_prefetch_context = context
        self._poll_analogy_prefetch(generation, context, future)

    def _render_official_updates(self) -> None:
        self._fact_index = 0
        self._render_fact()

    def _overview(self) -> QWidget:
        page = QWidget()
        page_root = QVBoxLayout(page)
        page_root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        self.overview_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.overview_content = content
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(content)
        self.overview_content_layout = layout
        layout.setContentsMargins(38, 24, 38, 18)
        layout.setSpacing(10)
        heading = QLabel("你好，" + self.user["display_name"])
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        subtitle = QLabel("这里是你的学习总览：正在进行什么、已经完成多少，以及今天最适合先做哪一步。")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        scroll.setWidget(content)
        page_root.addWidget(scroll)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        cards_container = QFrame()
        cards_container.setObjectName("overviewMetrics")
        cards_container.setLayout(cards)
        local_mode = os.getenv("MENTORFLOW_LOCAL_DATA_MODE", "").strip().lower() in {"1", "true", "yes"}
        account_card = ("本地学习空间", "已自动识别") if local_mode else ("学习账户", "已连接")
        account_snapshot = self.bootstrap_snapshot
        if account_snapshot is not None:
            account_card = ("学习空间", str(account_snapshot.workspace.get("title") or "已连接"))
        self.overview_metric_values: dict[str, QLabel] = {}
        for key, (title, value) in (
            ("space", account_card),
            ("active", ("学习计划" if account_snapshot is not None else "进行中任务", "读取中")),
            ("progress", ("进度记录" if account_snapshot is not None else "已完成学习项", "读取中")),
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

        calendar_card = QFrame()
        calendar_card.setObjectName("overviewTaskSection")
        calendar_card.setMinimumWidth(0)
        calendar_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        calendar_layout = QVBoxLayout(calendar_card)
        calendar_title = QLabel("近期日历")
        calendar_title.setObjectName("sectionHeading")
        calendar_layout.addWidget(calendar_title)
        self.overview_calendar = PlanCalendar()
        self.overview_calendar.setObjectName("effortCalendar")
        self.overview_calendar.setGridVisible(False)
        self.overview_calendar.setVerticalHeaderFormat(PlanCalendar.VerticalHeaderFormat.NoVerticalHeader)
        self.overview_calendar.setMinimumHeight(340)
        self.overview_calendar.setMaximumHeight(360)
        self.overview_calendar_summary = QLabel("正在读取日程…")
        self.overview_calendar_summary.setObjectName("inlineStatus")
        self.overview_calendar_summary.setWordWrap(True)
        calendar_layout.addWidget(self.overview_calendar_summary)
        calendar_layout.addWidget(self.overview_calendar)
        self.overview_calendar.selectionChanged.connect(
            lambda: self.overview_calendar_summary.setText(
                self.overview_calendar.summary_for(self.overview_calendar.selectedDate())
            )
        )
        layout.addWidget(calendar_card)

        fact_card = QFrame()
        fact_card.setObjectName("analogyCard")
        fact_card.setMinimumWidth(0)
        fact_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        fact_layout = QVBoxLayout(fact_card)
        fact_heading = QHBoxLayout()
        fact_label = QLabel("☕ 生活小剧场")
        fact_label.setObjectName("analogyEyebrow")
        fact_heading.addWidget(fact_label)
        fact_heading.addStretch()
        self.analogy_loading_status = QLabel("")
        self.analogy_loading_status.setObjectName("inlineStatus")
        self.analogy_loading_status.setVisible(False)
        fact_heading.addWidget(self.analogy_loading_status)
        fact_settings = QPushButton("设置")
        fact_settings.setObjectName("linkButton")
        fact_settings.clicked.connect(lambda _checked=False: self._open_news_preferences())
        fact_heading.addWidget(fact_settings)
        self.next_fact_button = QPushButton("换一张")
        self.next_fact_button.setObjectName("linkButton")
        self.next_fact_button.clicked.connect(lambda _checked=False: self._next_fact())
        fact_heading.addWidget(self.next_fact_button)
        fact_layout.addLayout(fact_heading)
        self.offline_fact_title = QLabel("")
        self.offline_fact_title.setObjectName("sectionHeading")
        self.offline_fact_title.setMinimumWidth(0)
        self.offline_fact_title.setWordWrap(True)
        fact_layout.addWidget(self.offline_fact_title)
        self.offline_fact_body = QLabel("")
        self.offline_fact_body.setMinimumWidth(0)
        self.offline_fact_body.setWordWrap(True)
        fact_layout.addWidget(self.offline_fact_body)
        self.offline_fact_source = QLabel("")
        self.offline_fact_source.setObjectName("pageSubtitle")
        self.offline_fact_source.setMinimumWidth(0)
        self.offline_fact_source.setWordWrap(True)
        fact_layout.addWidget(self.offline_fact_source)
        self.fact_source_button = QPushButton("查看来源")
        self.fact_source_button.setObjectName("linkButton")
        self.fact_source_button.clicked.connect(lambda _checked=False: self._open_current_fact_source())
        fact_layout.addWidget(self.fact_source_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(fact_card)
        self._render_fact()

        task_card = QFrame()
        task_card.setObjectName("overviewTaskSection")
        task_layout = QVBoxLayout(task_card)
        task_header = QHBoxLayout()
        section_title = QLabel("最近计划")
        section_title.setObjectName("sectionHeading")
        task_header.addWidget(section_title)
        task_header.addStretch()
        open_tasks = QPushButton("查看全部计划")
        open_tasks.setObjectName("secondaryButton")
        open_tasks.clicked.connect(lambda: self.navigation.setCurrentRow(1))
        task_header.addWidget(open_tasks)
        task_layout.addLayout(task_header)
        overview_status_row = QHBoxLayout()
        self.overview_status_icon = QLabel("!")
        self.overview_status_icon.setObjectName("overviewStatusIcon")
        self.overview_status_icon.setFixedSize(20, 20)
        self.overview_status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overview_status_icon.setVisible(False)
        overview_status_row.addWidget(self.overview_status_icon)
        self.overview_status = QLabel("正在读取任务……")
        self.overview_status.setObjectName("inlineStatus")
        overview_status_row.addWidget(self.overview_status)
        overview_status_row.addStretch()
        task_layout.addLayout(overview_status_row)
        self.overview_task_list = ResponsiveCardList()
        self.overview_task_list.setObjectName("overviewTaskList")
        self.overview_task_list.setFixedHeight(130)
        self.overview_task_list.itemDoubleClicked.connect(self._open_task_from_list)
        task_layout.addWidget(self.overview_task_list)
        # The most recent plan belongs above secondary calendar/discovery
        # content.  The overview is scrollable, so cards never overlap when a
        # smaller screen needs more vertical room.
        layout.insertWidget(5, task_card)
        layout.addStretch()
        QTimer.singleShot(0, self._sync_overview_geometry)
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
        self.task_loading_status = QLabel("")
        self.task_loading_status.setObjectName("inlineStatus")
        plan_actions.addWidget(self.task_loading_status)
        plan_actions.addStretch()
        layout.addLayout(plan_actions)
        self.task_list = ResponsiveCardList()
        self.task_list.setObjectName("taskList")
        self.task_list.setWordWrap(True)
        self.task_list.setSpacing(8)
        self.task_list.itemDoubleClicked.connect(self._open_task_from_list)
        layout.addWidget(self.task_list, 1)
        return page

    def _sync_overview_geometry(self) -> None:
        """Adapt overview padding and embedded cards to the live viewport."""

        if not hasattr(self, "overview_scroll") or not hasattr(self, "overview_content_layout"):
            return
        viewport_width = max(0, self.overview_scroll.viewport().width())
        margin = 18 if viewport_width < 940 else 28 if viewport_width < 1160 else 38
        self.overview_content_layout.setContentsMargins(margin, 24, margin, 18)
        if hasattr(self, "overview_task_list"):
            self.overview_task_list.fit_cards_to_viewport()
        if hasattr(self, "task_list"):
            self.task_list.fit_cards_to_viewport()

    def _open_task_from_list(self, item: QListWidgetItem) -> None:
        workspace_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not workspace_id:
            return
        self._open_workspace_detail(workspace_id)

    def _open_workspace_detail(self, workspace_id: str) -> None:
        """Open plan as task detail; it intentionally has no separate sidebar slot."""
        # Keep the sidebar selection truthful while opening the detail view.
        # When the user opened a card from “我的计划”, the row is already
        # selected, so relying on currentRowChanged alone would leave the
        # detail page mounted and make the back button appear to do nothing.
        self.navigation.blockSignals(True)
        self.navigation.setCurrentRow(1)
        self.navigation.blockSignals(False)
        self.pages.setCurrentIndex(4)
        # Carry the clicked id into the asynchronous list refresh. A deferred
        # select against the old combo contents is otherwise overwritten when
        # the refresh completes and restores its captured previous selection.
        self.plans_page.refresh_workspaces(workspace_id)
        QTimer.singleShot(0, lambda: self.plans_page.select_workspace(workspace_id))

    def _open_tasks_page(self) -> None:
        # The detail page is not a sidebar page.  Always set the stacked page
        # explicitly, even when row 1 is already selected (Qt then emits no
        # currentRowChanged signal).
        self.pages.setCurrentIndex(1)
        if self.navigation.currentRow() != 1:
            self.navigation.setCurrentRow(1)
        else:
            self._refresh_tasks()

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
        self.map_generate_button = QPushButton("从资料生成")
        self.map_generate_button.setObjectName("primaryButton")
        self.map_generate_button.clicked.connect(self._start_map_job)
        controls.addWidget(self.map_generate_button)
        fit = QPushButton("适应窗口")
        fit.clicked.connect(self._fit_map)
        controls.addWidget(fit)
        layout.addLayout(controls)

        self.map_progress = QProgressBar()
        self.map_progress.setObjectName("knowledgeMapProgress")
        self.map_progress.setRange(0, 100)
        self.map_progress.setValue(0)
        self.map_progress.setFormat("准备生成知识地图")
        self.map_progress.setTextVisible(True)
        self.map_progress.setVisible(False)
        layout.addWidget(self.map_progress)

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
        self.map_candidates.setAccessibleName("待审核知识地图候选")
        self.map_candidates.currentItemChanged.connect(self._show_candidate)
        self.map_candidates.itemChanged.connect(self._candidate_check_changed)
        evidence_layout.addWidget(self.map_candidates)
        self._map_checked_candidate_ids: set[str] = set()
        self._map_current_candidate_id = ""
        self.map_select_all_button = QPushButton("全选待审核")
        self.map_select_all_button.setAccessibleName("全选待审核候选")
        self.map_select_all_button.setToolTip("勾选或取消勾选全部待审核候选")
        self.map_select_all_button.clicked.connect(self._toggle_all_candidates)
        evidence_layout.addWidget(self.map_select_all_button)
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
        self._update_candidate_select_all_button()
        return page

    def _teacher_chat(self) -> QWidget:
        page, layout = self._page(
            "创建计划",
            "先说清学习目标，再选择可核对的资料；确认后只需补充日期，系统会生成知识地图和每日任务。",
        )
        profile = create_web_profile(QWebEngineProfile, "mentorflow_teacher_chat", self)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        web = QWebEngineView()
        web.setPage(install_external_link_handler(QWebEnginePage(profile, web)))
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
            (
                "学习远征",
                "在独立空间探索、训练和记录成长，不会混入正式学习任务。",
                "expedition",
                "打开学习远征",
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
        profile = create_web_profile(QWebEngineProfile, f"mentorflow_auxiliary_{tool}_{id(dialog)}", dialog)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        web.setPage(install_external_link_handler(QWebEnginePage(profile, web)))
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
        profile = create_web_profile(QWebEngineProfile, "mentorflow_expedition", self)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        web = QWebEngineView()
        web.setPage(install_external_link_handler(QWebEnginePage(profile, web)))
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
        self.provider_save_button = save_button
        actions.addWidget(save_button)
        connect_button = QPushButton("保存并验证连接")
        connect_button.setObjectName("primaryButton")
        connect_button.clicked.connect(self._connect_provider)
        self.provider_connect_button = connect_button
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
        # The active-teaching index is the canonical product task list.  The
        # legacy /api/workspaces table contains historical blank/UI-test rows
        # and has no durable archive flag, so merging it after the canonical
        # call silently resurrects plans the user already archived.  Keep it
        # only as a fail-closed compatibility fallback when the canonical
        # endpoint itself is unavailable.
        try:
            try:
                payload = self.api.active_workspaces(include_archived=include_archived)
            except TypeError:
                payload = self.api.active_workspaces()
            items = [dict(item) for item in payload.get("items", [])]
        except ApiError:
            items = [dict(item) for item in self.api.workspaces()]
            if not include_archived:
                items = [
                    item
                    for item in items
                    if not bool(item.get("archived"))
                    and str(item.get("status") or "").casefold() != "archived"
                ]
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return items

    @staticmethod
    def _task_progress(item: dict) -> tuple[int, int]:
        # Overview cards describe progress through the learner's knowledge
        # route, not the implementation-level phase/review task rows.  One
        # concept may expand into several execution tasks, so showing those
        # counts (for example 167/648) conflicts with the classroom's 42/150
        # position even when both refer to the same authoritative session.
        total = max(
            0,
            int(item.get("knowledge_point_count") or item.get("task_count") or 0),
        )
        completed_value = (
            item.get("completed_knowledge_point_count")
            if item.get("completed_knowledge_point_count") is not None
            else item.get("completed_task_count") or 0
        )
        completed = min(total, max(0, int(completed_value)))
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
            "needs_adjustment": "需调整",
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
        refresh_color = getattr(getattr(self, "plans_page", None), "_refresh_color_button", None)
        if callable(refresh_color):
            refresh_color(workspace_id)
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
        if self._workspace_action_future is not None and not self._workspace_action_future.done():
            self.task_loading_status.setText("上一项计划操作仍在进行，请稍候。")
            return
        button = self.sender() if isinstance(self.sender(), QPushButton) else None
        if button is not None:
            button.setEnabled(False)
            button.setText(f"正在{action}…")
        self.task_loading_status.setText(f"正在{action}“{title}”…")
        self._workspace_action_context = {
            "action": action,
            "button": button,
            "archived": archived,
        }
        self._workspace_action_future = self._workspace_executor.submit(
            self.api.archive_workspace,
            workspace_id,
            archived=not archived,
        )
        QTimer.singleShot(40, self._poll_workspace_action)

    def _poll_workspace_action(self) -> None:
        future = self._workspace_action_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(40, self._poll_workspace_action)
            return
        self._workspace_action_future = None
        context = self._workspace_action_context
        self._workspace_action_context = {}
        action = str(context.get("action") or "更新")
        try:
            future.result()
        except ApiError as exc:
            self.task_loading_status.setText(f"{action}失败：{exc.code}：{exc.message}")
            button = context.get("button")
            if isinstance(button, QPushButton):
                button.setEnabled(True)
                button.setText("恢复计划" if context.get("archived") else "归档计划")
            return
        self._workspace_cache.clear()
        self.task_loading_status.setText(f"{action}完成，正在更新列表…")
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
        if self.bootstrap_snapshot is not None:
            snapshot = self.bootstrap_snapshot
            self.overview_metric_values["active"].setText(str(len(snapshot.plans)))
            self.overview_metric_values["progress"].setText(str(len(snapshot.progress)))
            self.overview_current_plan.setText(
                "学习计划与进度已同步。本机资料可从“账号与数据”中的“本机资料恢复”继续处理。"
                if snapshot.plans else "欢迎来到新的学习空间，目前没有历史学习计划。"
            )
            self.overview_calendar.set_day_summaries({})
            self.overview_calendar_summary.setText("当前账号同步内容未包含每日任务日程。")
            self.overview_status_icon.setVisible(False)
            self.overview_status.setVisible(False)
            self._show_account_plan_metadata(self.overview_task_list)
            return
        cached = self._workspace_cache.get(False)
        if cached is not None:
            self._render_overview_items(cached)
        else:
            self.overview_status_icon.setVisible(False)
            self.overview_status.setVisible(True)
            self.overview_status.setText("正在更新计划概览…")
        self._overview_workspace_token += 1
        token = self._overview_workspace_token
        self._overview_workspace_future = self._workspace_executor.submit(
            self._workspace_items,
            include_archived=False,
        )
        QTimer.singleShot(40, lambda: self._poll_overview_items(token))

    def _poll_overview_items(self, token: int) -> None:
        future = self._overview_workspace_future
        if token != self._overview_workspace_token or future is None:
            return
        if not future.done():
            QTimer.singleShot(40, lambda: self._poll_overview_items(token))
            return
        self._overview_workspace_future = None
        try:
            items = future.result()
        except Exception as exc:
            self.overview_status_icon.setVisible(False)
            self.overview_status.setVisible(True)
            self.overview_status.setText(f"计划概览更新失败：{exc}")
            return
        self._workspace_cache[False] = items
        self._render_overview_items(items)

    def _render_overview_items(self, items: list[dict]) -> None:
        active = [item for item in items if self._human_status(item.get("status")) != "已完成"]
        completed = sum(self._task_progress(item)[0] for item in items)
        total = sum(self._task_progress(item)[1] for item in items)
        self.overview_metric_values["active"].setText(str(len(active)))
        self.overview_metric_values["progress"].setText(f"{completed} / {total}" if total else "暂无任务")
        if active:
            current = active[0]
            done, count = self._task_progress(current)
            title = str(current.get("title") or "当前计划")
            self.overview_current_plan.setText(
                f"{title} · 已完成 {done}/{count} 项。下一步：打开计划详情，继续最早未完成的学习项。"
            )
            self._load_overview_calendar(
                [
                    str(item.get("workspace_id") or item.get("id") or "")
                    for item in active
                    if str(item.get("workspace_id") or item.get("id") or "")
                ]
            )
        else:
            self.overview_current_plan.setText("还没有进行中的计划。点击“新建计划”，设定目标和日期后开始。")
            self.overview_calendar.set_day_summaries({})
            self.overview_calendar_summary.setText("创建计划后，这里会显示每天的任务与完成情况。")
        self.overview_task_list.clear()
        if not items:
            self.overview_status_icon.setVisible(False)
            self.overview_status.setVisible(True)
            self.overview_status.setText("还没有学习任务。先到“创建计划”设定目标和日期。")
            empty_item = QListWidgetItem("暂无任务。打开“创建计划”开始。")
            self.overview_task_list.addItem(empty_item)
            create_plan = QPushButton("打开创建计划")
            create_plan.setObjectName("primaryButton")
            create_plan.clicked.connect(self._open_create_plan)
            self.overview_task_list.setItemWidget(empty_item, create_plan)
            QTimer.singleShot(0, self.overview_task_list.fit_cards_to_viewport)
            return
        has_other_plans = len(items) > 1
        self.overview_status_icon.setVisible(has_other_plans)
        self.overview_status.setVisible(has_other_plans)
        self.overview_status.setText("其他计划可从右上角统一查看。" if has_other_plans else "")
        latest = active[0] if active else items[0]
        overview_item, overview_widget = self._task_card(latest, overview=True)
        self.overview_task_list.addItem(overview_item)
        self.overview_task_list.setItemWidget(overview_item, overview_widget)
        QTimer.singleShot(0, self.overview_task_list.fit_cards_to_viewport)

    def _load_overview_calendar(self, workspace_ids: str | list[str]) -> None:
        selected_ids = (
            [workspace_ids]
            if isinstance(workspace_ids, str)
            else [value for value in workspace_ids if value]
        )
        if not selected_ids:
            return
        self._overview_plan_token += 1
        token = self._overview_plan_token
        self.overview_calendar_summary.setText("正在更新日历…")
        self._overview_plan_future = self._workspace_executor.submit(
            self._current_plans_for_overview,
            selected_ids,
        )
        QTimer.singleShot(40, lambda: self._poll_overview_calendar(token))

    def _current_plan_for_overview(self, workspace_id: str) -> dict:
        """Read the canonical plan, falling back to the preserved legacy plan."""

        try:
            return self.api.current_mastery_plan(workspace_id)
        except ApiError as canonical_error:
            try:
                return self.api.current_legacy_plan(workspace_id)
            except ApiError:
                raise canonical_error

    def _current_plans_for_overview(self, workspace_ids: list[str]) -> dict:
        """Load every visible plan so older learning history remains visible."""

        plans: list[dict] = []
        for workspace_id in dict.fromkeys(workspace_ids):
            try:
                plans.append(self._current_plan_for_overview(workspace_id))
            except ApiError:
                continue
        if not plans:
            raise ApiError(404, "ACTIVE_PLAN_NOT_FOUND", "没有可显示的活动计划")
        return {"plans": plans}

    def _poll_overview_calendar(self, token: int) -> None:
        future = self._overview_plan_future
        if token != self._overview_plan_token or future is None:
            return
        if not future.done():
            QTimer.singleShot(40, lambda: self._poll_overview_calendar(token))
            return
        self._overview_plan_future = None
        try:
            plan = future.result()
        except ApiError:
            self.overview_calendar.set_day_summaries({})
            self.overview_calendar_summary.setText("当前计划还没有可显示的日程。")
            return
        self._render_overview_calendar(plan)

    def _render_overview_calendar(self, plan: dict) -> None:
        self.overview_calendar.setDateTextFormat(QDate(), QTextCharFormat())
        daily: dict[str, dict[str, float]] = {}
        dates: list[QDate] = []
        plans = [item for item in plan.get("plans", []) if isinstance(item, dict)] or [plan]
        def bucket_for(value: str) -> dict[str, float]:
            return daily.setdefault(
                value,
                {
                    "tasks": 0,
                    "scheduled": 0,
                    "unscheduled": 0,
                    "planned": 0,
                    "actual": 0,
                    "completed": 0,
                    "completed_minutes": 0,
                    "has_activity": 0,
                },
            )

        for visible_plan in plans:
            for task in flattened_tasks(visible_plan):
                value = str(
                    task.get("day_date")
                    or task.get("scheduled_date")
                    or task.get("original_day_date")
                    or task.get("date")
                    or ""
                )
                date = QDate.fromString(value, "yyyy-MM-dd")
                if not date.isValid():
                    continue
                dates.append(date)
                bucket = bucket_for(value)
                bucket["tasks"] += 1
                estimated = float(task.get("estimated_task_minutes") or task.get("planned_task_minutes") or 0)
                status = str(task.get("status") or "").casefold()
                if status == "unscheduled":
                    bucket["unscheduled"] += 1
                else:
                    bucket["scheduled"] += 1
                    bucket["planned"] += estimated
                if status in {"complete", "completed", "done"}:
                    completed_on = str(
                        task.get("completed_on")
                        or task.get("completed_at_local_date")
                        or task.get("completed_at")
                        or value
                    )[:10]
                    completed_date = QDate.fromString(completed_on, "yyyy-MM-dd")
                    if not completed_date.isValid():
                        completed_on, completed_date = value, date
                    completed_bucket = bucket_for(completed_on)
                    dates.append(completed_date)
                    completed_bucket["completed"] += 1
                    completed_bucket["completed_minutes"] += float(
                        task.get("completed_task_minutes") or estimated
                    )
            for value, raw in (visible_plan.get("activity_by_day") or {}).items():
                date = QDate.fromString(str(value), "yyyy-MM-dd")
                if not date.isValid() or not isinstance(raw, dict):
                    continue
                dates.append(date)
                bucket = bucket_for(str(value))
                bucket["actual"] += float(raw.get("actual_elapsed_minutes") or 0)
                bucket["has_activity"] = 1
        peak_effort = max((bucket["completed_minutes"] for bucket in daily.values()), default=0.0)
        metrics = {
            value: {
                **bucket,
                "effort": bucket["completed_minutes"],
                "ratio": (bucket["completed_minutes"] / peak_effort) if peak_effort else 0.0,
            }
            for value, bucket in daily.items()
        }
        self.overview_calendar.set_day_metrics(metrics)
        summaries = {
            value: (
                f"{value}\n已安排 {int(bucket['scheduled'])} 项 · 已完成 {int(bucket['completed'])} 项"
                + (f" · 待排期 {int(bucket['unscheduled'])} 项" if bucket["unscheduled"] else "")
                + "\n"
                f"计划 {int(bucket['planned'])} 分钟 · 完成任务时长 {int(bucket['completed_minutes'])} 分钟"
                + (f" · 当天专注 {bucket['actual']:.1f} 分钟" if bucket["actual"] else "")
                + ("\n有课堂活动记录，但旧数据没有可核对的分钟数。" if bucket["has_activity"] and not bucket["actual"] else "")
            )
            for value, bucket in daily.items()
        }
        self.overview_calendar.set_day_summaries(summaries)
        if dates:
            today = QDate.currentDate()
            scheduled_on_or_after_today = [value for value in dates if value >= today]
            first = (
                min(scheduled_on_or_after_today, key=lambda value: value.toJulianDay())
                if scheduled_on_or_after_today
                else max(dates, key=lambda value: value.toJulianDay())
            )
            last = max(dates, key=lambda value: value.toJulianDay())
            earliest = min(dates, key=lambda value: value.toJulianDay())
            self.overview_calendar.setDateRange(earliest, last)
            self.overview_calendar.setSelectedDate(first)
            self.overview_calendar_summary.setText(self.overview_calendar.summary_for(first))
            if hasattr(self, "overview_scroll"):
                QTimer.singleShot(0, lambda: self.overview_scroll.verticalScrollBar().setValue(0))
        else:
            self.overview_calendar_summary.setText("当前计划还没有可显示的日程。")

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
                saved_base_url = str(form.get("base_url") or self.DEEPSEEK_BASE_URL)
                saved_model = self._normalized_provider_model(
                    saved_base_url,
                    str(form.get("model") or self.DEEPSEEK_FLASH_MODEL),
                )
                self.provider_base_url.setText(saved_base_url)
                self.provider_model.setText(saved_model)
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
        if self._provider_connect_future is not None and not self._provider_connect_future.done():
            self.provider_status.setText("模型连接正在验证，请稍候。")
            return
        try:
            form, credential_ref = self._provider_form_with_credential()
            if form is None:
                return
            preset_name = self._provider_preset_name()
            # A provider/model switch invalidates any card request started
            # under the previous context.  The worker may still finish, so
            # advance the generation before replacing the session handle.
            self._clear_analogy_prefetch()
            self._remember_provider_form(credential_ref)
        except CredentialStoreError as exc:
            self.provider_status.setText(f"凭据库失败：{exc.code}：{exc.message}")
            self.provider_secret.clear()
            return
        self.provider_secret.clear()
        self.provider_connect_button.setEnabled(False)
        self.provider_connect_button.setText("正在验证…")
        self.provider_status.setText("已保存设置，正在后台验证模型连接…")

        def work() -> dict:
            created = self.api.create_provider_session(
                provider=form["provider"],
                base_url=form["base_url"],
                model=form["model"],
                credential_ref=credential_ref,
            )
            result = self.api.test_provider_session(str(created["provider_session_id"]))
            self.api.save_provider_preset(preset_name, form)
            return {"result": result, "form": form}

        self._provider_connect_future = self._provider_executor.submit(work)
        QTimer.singleShot(60, self._poll_provider_connection)

    def _poll_provider_connection(self) -> None:
        future = self._provider_connect_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(60, self._poll_provider_connection)
            return
        self._provider_connect_future = None
        self.provider_connect_button.setEnabled(True)
        self.provider_connect_button.setText("保存并验证连接")
        try:
            payload = future.result()
            result = payload["result"]
            form = payload["form"]
            self.provider_session_id = str(result["provider_session_id"])
            self.provider_status.setText(
                "连接成功\n"
                f"主机：{result.get('base_url_host') or '未知'}\n"
                f"服务端模型：{result.get('model_reported') or result.get('model_requested') or form['model']}\n"
                f"延迟：{result.get('latency_ms')} ms"
            )
            # The overview analogy changes only after the learner explicitly
            # saves its settings or presses “换一张”.
            QTimer.singleShot(0, self._schedule_analogy_prefetch)
        except ApiError as exc:
            self.provider_status.setText(f"连接失败：{exc.code}：{exc.message}")

    def _refresh_tasks(self, _checked: bool = False) -> None:
        if self.bootstrap_snapshot is not None:
            self._show_account_plan_metadata(self.task_list)
            self.task_loading_status.setText("已恢复当前账号的计划与进度记录")
            return
        include_archived = bool(
            getattr(self, "include_archived_plans", None)
            and self.include_archived_plans.isChecked()
        )
        self._workspace_refresh_token += 1
        token = self._workspace_refresh_token
        cached = self._workspace_cache.get(include_archived)
        if cached is not None:
            self._show_task_items(cached)
        self.task_loading_status.setText("正在更新…")
        self._workspace_refresh_future = self._workspace_executor.submit(
            self._workspace_items,
            include_archived=include_archived,
        )
        QTimer.singleShot(40, lambda: self._poll_task_refresh(token, include_archived))

    def _show_account_plan_metadata(self, target: QListWidget) -> None:
        """Render the authenticated snapshot without inventing local task rows."""
        snapshot = self.bootstrap_snapshot
        target.clear()
        include_archived = bool(self.include_archived_plans.isChecked())
        plans = [
            plan for plan in snapshot.plans
            if include_archived or str(plan.get("status", "")).casefold() != "archived"
        ]
        for plan in plans:
            plan_id = str(plan.get("plan_id") or "")
            progress = [entry for entry in snapshot.progress if entry.get("plan_id") == plan_id]
            title = str(plan.get("title") or "学习计划")
            item = QListWidgetItem(f"{title}\n已同步 · 进度记录 {len(progress)} 项")
            item.setSizeHint(QSize(300, 76))
            target.addItem(item)
        if not plans:
            target.addItem(QListWidgetItem("当前账号没有可显示的学习计划。"))

    def _poll_task_refresh(self, token: int, include_archived: bool) -> None:
        future = self._workspace_refresh_future
        if token != self._workspace_refresh_token or future is None:
            return
        if not future.done():
            QTimer.singleShot(40, lambda: self._poll_task_refresh(token, include_archived))
            return
        self._workspace_refresh_future = None
        try:
            items = future.result()
        except Exception as exc:  # API failures are already normalized in _workspace_items
            self.task_loading_status.setText(f"更新失败：{exc}")
            return
        self._workspace_cache[include_archived] = items
        self._show_task_items(items)
        self.task_loading_status.setText("")

    def _show_task_items(self, items: list[dict]) -> None:
        self.task_list.clear()
        if not items:
            self.task_list.addItem("暂无任务。先到“创建计划”设定目标和日期。")
            return
        for item in items:
            item_widget, card = self._task_card(item)
            self.task_list.addItem(item_widget)
            self.task_list.setItemWidget(item_widget, card)
        QTimer.singleShot(0, self.task_list.fit_cards_to_viewport)

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
        self._map_node_names = {
            str(item.get("id") or ""): str(item.get("name") or "未命名概念")
            for item in nodes
            if isinstance(item, dict) and item.get("id")
        }
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
            self.map_generate_button.setEnabled(True)
            return
        self._map_job_id = str(job.get("id") or job.get("job_id") or "")
        if not self._map_job_id:
            self.map_state.setText("服务没有返回地图任务 ID。")
            self.map_generate_button.setEnabled(True)
            return
        self.map_generate_button.setEnabled(False)
        self.map_generate_button.setText("正在生成…")
        self.map_progress.setVisible(True)
        self._render_map_job_progress(job)
        self._map_job_timer.start()

    def _render_map_job_progress(self, job: dict) -> None:
        """Render only persisted work-unit progress reported by the backend."""

        progress = job.get("progress") if isinstance(job, dict) else {}
        if not isinstance(progress, dict):
            progress = {}
        completed = max(0, int(progress.get("completed_units") or 0))
        total = max(1, int(progress.get("total_units") or 1))
        percent = max(0, min(100, round(float(progress.get("percent") or 0))))
        message = str(progress.get("message") or "正在生成知识地图").strip()
        status = str(job.get("status") or progress.get("phase") or "queued")
        phase_name = {
            "queued": "等待开始",
            "converting": "整理资料",
            "extracting": "提取知识",
            "validating": "检查知识关系",
            "ready_with_review": "生成完成，存在待确认项",
            "ready": "生成完成",
            "failed": "生成失败",
            "cancelled": "已取消",
        }.get(status, "正在处理")
        self.map_progress.setValue(percent)
        self.map_progress.setFormat(f"{phase_name}  {percent}%  ·  {completed}/{total}")
        self.map_state.setText(message)

    def _poll_map_job(self) -> None:
        if not self._map_job_id:
            self._map_job_timer.stop()
            return
        try:
            job = self.api.knowledge_map_job(self._map_job_id)
        except ApiError as exc:
            self._map_job_timer.stop()
            self.map_state.setText(f"地图任务查询失败：{exc.code}：{exc.message}")
            self.map_generate_button.setEnabled(True)
            self.map_generate_button.setText("从资料生成")
            return
        status = str(job.get("status") or "未知")
        dispositions = job.get("dispositions") or []
        self._render_map_job_progress(job)
        if status in {"ready", "ready_with_review"}:
            self._map_job_timer.stop()
            self.map_progress.setValue(100)
            self.map_generate_button.setEnabled(True)
            self.map_generate_button.setText("重新生成")
            self._load_selected_map()
            review_note = "，有待确认内容" if status == "ready_with_review" else ""
            self.map_state.setText(
                f"知识地图已生成{review_note}。已处理 {len(dispositions)} 份资料；可点击节点查看来源。"
            )
        elif status in {"failed", "cancelled"}:
            self._map_job_timer.stop()
            error = job.get("error") or {}
            self.map_generate_button.setEnabled(True)
            self.map_generate_button.setText("重新生成")
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
            page_start = item.get("page_start", item.get("page_number"))
            page_end = item.get("page_end", page_start)
            if page_start is not None:
                location.append(
                    f"第 {page_start} 页"
                    if page_end in {None, page_start}
                    else f"第 {page_start}–{page_end} 页"
                )
            if item.get("char_start") is not None or item.get("char_end") is not None:
                location.append(f"字符 {item.get('char_start', '?')}–{item.get('char_end', '?')}")
            audio_start = item.get("audio_start_ms", item.get("time_start_ms"))
            audio_end = item.get("audio_end_ms", item.get("time_end_ms"))
            if audio_start is not None or audio_end is not None:
                location.append(f"音频 {audio_start if audio_start is not None else '?'}–{audio_end if audio_end is not None else '?'} ms")
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
        self._remember_candidate_selection()
        previous_current_id = self._map_current_candidate_id
        self.map_candidates.clear()
        try:
            candidates = self.api.knowledge_map_candidates(workspace_id).get("candidates", [])
        except ApiError as exc:
            self.map_state.setText(self.map_state.text() + f"\n候选读取失败：{exc.code}")
            return
        pending_candidates = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("status") or "pending") != "pending":
                continue
            pending_candidates.append(candidate)
        pending_ids = {
            str(candidate.get("id") or "")
            for candidate in pending_candidates
            if str(candidate.get("id") or "")
        }
        self._map_checked_candidate_ids.intersection_update(pending_ids)
        for candidate in pending_candidates:
            payload = candidate.get("payload") or {}
            candidate_type = str(candidate.get("candidate_type") or "candidate")
            type_label = {
                "node": "概念建议",
                "edge": "关系建议",
                "merge_suggestion": "合并建议",
                "model_common": "模型常识",
            }.get(candidate_type, "待确认建议")
            relation = str(payload.get("relation") or "")
            relation_label = {
                "contains": "包含",
                "prerequisite": "前置关系",
                "related": "相关",
                "contrast": "对比",
            }.get(relation, relation)
            node_names = getattr(self, "_map_node_names", {})
            source_id = str(payload.get("source_id") or payload.get("prerequisite_id") or "")
            target_id = str(payload.get("target_id") or payload.get("dependent_id") or "")
            source_name = str(payload.get("source_name") or node_names.get(source_id) or "")
            target_name = str(payload.get("target_name") or node_names.get(target_id) or "")
            if source_name and target_name:
                label = f"{source_name} → {target_name}" + (f"（{relation_label}）" if relation_label else "")
            else:
                label = str(payload.get("name") or payload.get("label") or relation_label or "请查看详情")
            item = QListWidgetItem(f"{type_label} · {label}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            candidate_id = str(candidate.get("id") or "")
            item.setCheckState(
                Qt.CheckState.Checked
                if candidate_id and candidate_id in self._map_checked_candidate_ids
                else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            self.map_candidates.addItem(item)
        if self.map_candidates.count() == 0:
            empty = QListWidgetItem("没有待审核候选")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.map_candidates.addItem(empty)
        else:
            target = next(
                (index for index in range(self.map_candidates.count()) if self._candidate_id_for_item(self.map_candidates.item(index)) == previous_current_id),
                -1,
            )
            if target < 0:
                target = 0
            self.map_candidates.setCurrentRow(target)
        self._update_candidate_select_all_button()

    @staticmethod
    def _candidate_id_for_item(item: QListWidgetItem | None) -> str:
        if item is None:
            return ""
        candidate = item.data(Qt.ItemDataRole.UserRole)
        return str(candidate.get("id") or "") if isinstance(candidate, dict) else ""

    def _remember_candidate_selection(self) -> None:
        """Keep checked/current candidate IDs across candidate list rebuilds."""

        if not hasattr(self, "map_candidates"):
            return
        checked: set[str] = set()
        for index in range(self.map_candidates.count()):
            item = self.map_candidates.item(index)
            candidate = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get("id") or "")
            if candidate_id and item.checkState() == Qt.CheckState.Checked:
                checked.add(candidate_id)
        self._map_checked_candidate_ids = checked
        current = self.map_candidates.currentItem()
        candidate = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._map_current_candidate_id = str(candidate.get("id") or "") if isinstance(candidate, dict) else ""

    def _candidate_check_changed(self, _item: QListWidgetItem) -> None:
        self._remember_candidate_selection()
        self._update_candidate_select_all_button()

    def _update_candidate_select_all_button(self) -> None:
        button = getattr(self, "map_select_all_button", None)
        candidates = []
        widget = getattr(self, "map_candidates", None)
        if widget is not None:
            for index in range(widget.count()):
                candidate = widget.item(index).data(Qt.ItemDataRole.UserRole)
                if isinstance(candidate, dict) and str(candidate.get("id") or ""):
                    candidates.append(candidate)
        all_checked = bool(candidates) and all(
            str(candidate.get("id") or "") in self._map_checked_candidate_ids for candidate in candidates
        )
        if button is not None:
            button.setEnabled(bool(candidates))
            button.setText("取消全选" if all_checked else "全选待审核")
            button.setAccessibleName("取消全选待审核候选" if all_checked else "全选待审核候选")

    def _toggle_all_candidates(self) -> None:
        self._remember_candidate_selection()
        candidates = []
        for index in range(self.map_candidates.count()):
            item = self.map_candidates.item(index)
            candidate = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(candidate, dict) and str(candidate.get("id") or ""):
                candidates.append(item)
        if not candidates:
            self._update_candidate_select_all_button()
            return
        select_all = not all(item.checkState() == Qt.CheckState.Checked for item in candidates)
        self.map_candidates.blockSignals(True)
        try:
            state = Qt.CheckState.Checked if select_all else Qt.CheckState.Unchecked
            for item in candidates:
                item.setCheckState(state)
        finally:
            self.map_candidates.blockSignals(False)
        self._remember_candidate_selection()
        self._update_candidate_select_all_button()

    def _show_candidate(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        candidate = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(candidate, dict):
            return
        self._map_current_candidate_id = str(candidate.get("id") or "")
        payload = candidate.get("payload") or {}
        candidate_type = str(candidate.get("candidate_type") or "candidate")
        lines = [
            {
                "node": "概念建议",
                "edge": "关系建议",
                "merge_suggestion": "合并建议",
                "model_common": "模型常识",
            }.get(candidate_type, "待确认建议"),
            "",
        ]
        if payload.get("name") or payload.get("label"):
            lines.append("内容：" + str(payload.get("name") or payload.get("label")))
        relation = str(payload.get("relation") or "")
        if relation:
            lines.append("关系：" + {
                "contains": "包含",
                "prerequisite": "前置关系",
                "related": "相关",
                "contrast": "对比",
            }.get(relation, relation))
        explanation = str(payload.get("explanation") or payload.get("reason") or "").strip()
        if explanation:
            lines.extend(["", explanation])
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
        self._remember_candidate_selection()
        checked: list[dict] = []
        for index in range(self.map_candidates.count()):
            item = self.map_candidates.item(index)
            candidate = item.data(Qt.ItemDataRole.UserRole)
            if (
                isinstance(candidate, dict)
                and item.checkState() == Qt.CheckState.Checked
                and str(candidate.get("id") or "")
            ):
                checked.append(candidate)
        selected = checked
        if not selected:
            current = self.map_candidates.currentItem()
            candidate = current.data(Qt.ItemDataRole.UserRole) if current else None
            if isinstance(candidate, dict) and str(candidate.get("id") or ""):
                selected = [candidate]
        deduped: list[dict] = []
        seen_ids: set[str] = set()
        for candidate in selected:
            candidate_id = str(candidate.get("id") or "")
            if candidate_id and candidate_id not in seen_ids:
                seen_ids.add(candidate_id)
                deduped.append(candidate)
        if not workspace_id or not deduped:
            self.map_state.setText("请选择一个待审核候选。")
            return
        reason = self.candidate_reason.text().strip()
        action = "确认" if decision == "confirmed" else "驳回"
        if checked:
            candidate_ids = [str(candidate.get("id") or "") for candidate in deduped]
            try:
                result = self.api.decide_knowledge_map_candidates(
                    workspace_id,
                    candidate_ids,
                    decision=decision,
                    reason=reason,
                )
            except ApiError as exc:
                # The batch endpoint is atomic: do not reload or report a
                # partial success when the server rejects a stale revision.
                self.map_state.setText(f"批量{action}失败：{exc.code}：{exc.message}")
                return

            decided_count = result.get("decided_count") if isinstance(result, dict) else None
            if decided_count is None:
                rows = (
                    (result.get("results") or result.get("candidates"))
                    if isinstance(result, dict)
                    else []
                )
                decided_count = len(rows) if isinstance(rows, list) else len(candidate_ids)
            try:
                decided_count = int(decided_count)
            except (TypeError, ValueError):
                decided_count = len(candidate_ids)
            remaining_pending = result.get("remaining_pending_count") if isinstance(result, dict) else None
            remaining_label = str(remaining_pending) if remaining_pending is not None else "未知"
            revision = (
                str(result.get("revision") or result.get("revision_id") or "")
                if isinstance(result, dict)
                else ""
            )
            final_status = (
                f"已{action} {decided_count} 项，剩余待审核 {remaining_label} 项，"
                f"新修订：{revision or '未知'}"
            )
        else:
            # Keep the legacy single-candidate path for callers that have no
            # checked rows.  A checked selection always uses the atomic batch
            # endpoint above; never loop over single decisions here.
            try:
                result = self.api.decide_knowledge_map_candidate(
                    workspace_id,
                    str(deduped[0].get("id") or ""),
                    decision=decision,
                    reason=reason,
                )
            except ApiError as exc:
                self.map_state.setText(f"{action}失败：{exc.code}：{exc.message}")
                return
            revision = str(result.get("revision") or result.get("revision_id") or "")
            final_status = f"已{action} 1 项，新修订：{revision or '未知'}"

        self.candidate_reason.clear()
        self._load_selected_map()
        # Loading the refreshed graph replaces ``map_state`` with its graph
        # summary.  Keep the decision receipt visible afterwards.
        graph_summary = self.map_state.text().strip()
        self.map_state.setText(
            f"{graph_summary}\n{final_status}" if graph_summary else final_status
        )

    def _logout(self) -> None:
        if self.account_profile.mode is ProfileMode.ENTERPRISE_ACCOUNT:
            self._logout_enterprise_account()
            return
        try:
            self.api.logout()
        finally:
            self.close()
