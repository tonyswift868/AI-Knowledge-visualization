"""Old learning shell layout, assembled without the old MainWindow/runtime.

Sidebar/stacked navigation and overview widgets are extracted locally. Account
actions keep the integrated account shell's signal/callback contract.
"""
from __future__ import annotations

from pathlib import Path
from importlib import import_module, util as import_util
import json
import sys
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QPlainTextEdit, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .materials_page import PlanCompletionPage
from .overview_page import OverviewPage, label
from .plan_export import ExportResult, ExportStatus
from .plans_page import PlansPage
from .style import STYLE, display_style


FEATURE_PAGES = {
    "classroom": ("原生课堂", "refactor.learning.classroom.page"),
    "planning": ("创建计划", "refactor.learning.planning.page"),
    "connections": ("模型连接", "refactor.learning.connections.page"),
    "preferences": ("设置", "refactor.learning.preferences.page"),
    "tools": ("更多工具", "refactor.learning.tools.page"),
}


def _module_available(name):
    """Discover page code without constructing its services or widgets."""
    if name in sys.modules:
        return True
    try:
        return import_util.find_spec(name) is not None
    except ModuleNotFoundError as exc:
        # A missing page is different from a delivered package with a broken
        # dependency: the latter gets a local error page and retry action.
        return not (exc.name and (name == exc.name or name.startswith(exc.name + ".")))
    except Exception:
        return True


class UnavailablePage(QWidget):
    """A failed feature cannot prevent opening the shell or other features."""
    def __init__(self, title, parent=None, *, message=None, retry=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(38, 24, 38, 24)
        box.addWidget(label(title, "pageHeading"))
        self.message = label(message or "此功能暂时无法打开，请稍后重新进入。其他页面与账号操作仍可使用。")
        box.addWidget(self.message)
        self.retry_button = QPushButton("重试此页面")
        self.retry_button.setObjectName("secondaryButton")
        self.retry_button.setVisible(retry is not None)
        if retry:
            self.retry_button.clicked.connect(retry)
        box.addWidget(self.retry_button, alignment=Qt.AlignmentFlag.AlignLeft)
        box.addStretch()

    def dispose(self):
        self.setEnabled(False)


class LegacyLearningWindow(QWidget):
    switch_requested = Signal()
    logout_requested = Signal()
    verify_requested = Signal()
    settings_requested = Signal()

    def __init__(self, display_name: str, server_role: object, api_base_url: str, *, account_id: str, data_root: Path, parent=None):
        super().__init__(parent)
        self._disposed = False
        self._account_id = account_id
        self._data_root = Path(data_root)
        self._session_provider = None
        self._context_bound = False
        self._feature_pages = {}
        # Navigation signals are synchronous in the normal Qt path, but a
        # page factory may also cause a queued navigation while it is being
        # constructed.  Keep an explicit construction guard so one click can
        # never create two copies of a feature page (notably the planning
        # browser, which owns a WebEngine instance).
        self._opening_features = set()
        self.feature_errors = {}
        self._feature_connections = []
        self._service_bound = False
        self._plan_refresh_pending = False
        self._pending_plan_key = ""
        self._suspend_navigation_refresh = False
        self._display_preferences = {}
        self._preference_store = None
        self._sync_controller = None
        self.feature_context = {
            "account_id": account_id, "data_root": self._data_root,
            "session_provider": self._current_feature_session,
            "api_base_url": "", "ca_bundle": None,
            "model_client": None, "plan_service": None,
        }
        self._plan_refresh_timer = QTimer(self)
        self._plan_refresh_timer.setSingleShot(True)
        self._plan_refresh_timer.timeout.connect(self._flush_plan_refresh)
        self.setObjectName("mentorflowLearningHome")
        self.setWindowTitle("MentorFlow")
        self.resize(1480, 940)
        self.setMinimumSize(1100, 720)
        self.setStyleSheet(STYLE)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._sidebar = QFrame()
        self._sidebar.setObjectName("sidebar")
        side = QVBoxLayout(self._sidebar)
        side.setContentsMargins(22, 24, 22, 24)
        logo = QLabel("🌿  MentorFlow")
        logo.setObjectName("sideLogo")
        side.addWidget(logo)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setAccessibleName("学习平台导航")
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        items = (("概览", "overview"), ("我的计划", "plans"), ("计划完成情况", "completion"),
                 *((title, key) for key, (title, _module) in FEATURE_PAGES.items()))
        self._nav_rows = {}
        for title, key in items:
            item = QListWidgetItem(title, self.navigation)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._nav_rows[key] = self.navigation.count() - 1
        self.navigation.currentRowChanged.connect(self._navigate)
        side.addWidget(self.navigation, 1)
        self.settings_button = QPushButton("账号与安全")
        self.settings_button.setObjectName("accountSettings")
        self.settings_button.setEnabled(False)
        self.settings_button.setToolTip("等待账号设置入口接入。")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        side.addWidget(self.settings_button)
        badge = QFrame()
        badge.setObjectName("accountBadge")
        badge_layout = QVBoxLayout(badge)
        self.name_label = label(display_name or "MentorFlow 账号")
        self.name_label.setAccessibleName("当前账号")
        self.role_label = label("")
        self.role_label.setAccessibleName("服务端账号身份")
        self.set_server_role(server_role)
        badge_layout.addWidget(self.name_label)
        badge_layout.addWidget(self.role_label)
        side.addWidget(badge)
        actions = QHBoxLayout()
        self.switch_button = QPushButton("切换账号")
        self.switch_button.clicked.connect(self.switch_requested.emit)
        self.logout_button = QPushButton("退出登录")
        self.logout_button.clicked.connect(self.logout_requested.emit)
        actions.addWidget(self.switch_button)
        actions.addWidget(self.logout_button)
        side.addLayout(actions)
        outer.addWidget(self._sidebar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.session_banner = QFrame()
        self.session_banner.setObjectName("sessionNotice")
        banner_layout = QHBoxLayout(self.session_banner)
        self.status_label = label("")
        banner_layout.addWidget(self.status_label, 1)
        self.verify_button = QPushButton("重新验证账号")
        self.verify_button.clicked.connect(self.verify_requested.emit)
        banner_layout.addWidget(self.verify_button)
        self.session_banner.hide()
        body_layout.addWidget(self.session_banner)
        self.feature_banner = QFrame()
        self.feature_banner.setObjectName("featureNotice")
        feature_notice_layout = QHBoxLayout(self.feature_banner)
        self.feature_status_label = label("")
        feature_notice_layout.addWidget(self.feature_status_label, 1)
        self.feature_retry_button = QPushButton("重试此功能连接")
        self.feature_retry_button.clicked.connect(self._retry_feature_dependencies)
        feature_notice_layout.addWidget(self.feature_retry_button)
        self.feature_banner.hide()
        body_layout.addWidget(self.feature_banner)
        sync_bar = QWidget()
        sync_layout = QHBoxLayout(sync_bar)
        sync_layout.setContentsMargins(24, 5, 24, 5)
        self.sync_status = label("本机记录已保留")
        sync_layout.addWidget(self.sync_status, 1)
        self.sync_conflicts_button = QPushButton("查看同步冲突")
        self.sync_conflicts_button.clicked.connect(self._show_sync_conflicts)
        self.sync_conflicts_button.hide()
        sync_layout.addWidget(self.sync_conflicts_button)
        self.sync_button = QPushButton("立即同步")
        self.sync_button.clicked.connect(lambda: self._sync_controller.sync_now() if self._sync_controller else None)
        self.sync_button.setEnabled(False)
        sync_layout.addWidget(self.sync_button)
        body_layout.addWidget(sync_bar)
        self.api_label = label(f"账号服务：{api_base_url}")
        self.api_label.hide()
        self.pages = QStackedWidget()
        self.overview = OverviewPage(display_name, self)
        # The old standalone materials table is deliberately not a primary
        # destination.  Materials are owned by plans and are shown from the
        # plan detail; this entry is the account-wide completion directory.
        self.completion = self._build_feature("计划完成情况", lambda: PlanCompletionPage(parent=self))
        # Keep the historical attribute for integrations that only call
        # bind_plan_service/refresh; it points to the new page and never
        # constructs the retired materials table.
        self.materials = self.completion
        self.plans = self._build_feature("我的计划", lambda: PlansPage(account_id, data_root, self))
        self._destinations = {"overview": self.overview, "plans": self.plans, "completion": self.completion}
        for page in self._destinations.values():
            self.pages.addWidget(page)
        self.pages.currentChanged.connect(self._sync_navigation)
        self.pages.currentChanged.connect(self._page_changed)
        self.overview.open_plan.connect(self.open_plan)
        self.overview.open_materials.connect(lambda: self.navigate_to("completion"))
        if isinstance(self.plans, PlansPage):
            self.plans.open_completion.connect(self._open_completion_for_plan)
        if isinstance(self.plans, PlansPage):
            self.plans.state_changed.connect(self._plans_changed)
            self.overview.refresh_requested.connect(self._request_plan_refresh)
        else:
            self.overview.all_plans_button.setEnabled(False)
        body_layout.addWidget(self.pages, 1)
        outer.addWidget(body, 1)
        self.refresh_feature_availability()
        self.navigation.setCurrentRow(0)
        self._sync_overview()

    def _build_feature(self, title, factory):
        try:
            return factory()
        except Exception:
            return UnavailablePage(title, self)

    def _navigate(self, index):
        if self._disposed or index < 0:
            return
        key = self.navigation.item(index).data(Qt.ItemDataRole.UserRole)
        if key in FEATURE_PAGES:
            self._open_feature(key)
            return
        if key in self._destinations:
            self.pages.setCurrentWidget(self._destinations[key])
        else:
            self._sync_navigation()

    def navigate_to(self, key):
        # Historical links share the same material library and selection.
        if key in {"library", "materials"}:
            key = "completion"
        if not self._disposed and key in self._nav_rows:
            item = self.navigation.item(self._nav_rows[key])
            if not item.flags() & Qt.ItemFlag.ItemIsEnabled:
                return
            row = self._nav_rows[key]
            # Let the current-row signal be the single owner of lazy feature
            # construction.  Calling _open_feature again immediately after
            # setCurrentRow made WebEngine-backed pages visibly flash when a
            # navigation request arrived from another page or a queued signal.
            if self.navigation.currentRow() != row:
                self.navigation.setCurrentRow(row)
                return
            if key in self._destinations:
                page = self._destinations[key]
                if self.pages.currentWidget() is not page:
                    self.pages.setCurrentWidget(page)
            elif key in FEATURE_PAGES:
                self._open_feature(key)

    def _sync_navigation(self, _index=None):
        key = next((key for key, page in self._destinations.items() if page is self.pages.currentWidget()), None)
        if key:
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(self._nav_rows[key])
            self.navigation.blockSignals(False)

    def open_plan(self, key=""):
        if self._service_bound:
            self._pending_plan_key = key
        self.navigate_to("plans")
        if not self._disposed and isinstance(self.plans, PlansPage) and key and not self.plans.busy:
            opener = getattr(self.plans, "open_plan", None)
            if callable(opener):
                opener(key)
            else:
                index = self.plans.selector.findData(key)
                if index >= 0:
                    self.plans.selector.setCurrentIndex(index)

    def _open_completion_for_plan(self, key=""):
        """Open the account completion page and, when supplied, its map."""
        self.navigate_to("completion")
        page = self.completion
        opener = getattr(page, "open_plan", None)
        if callable(opener):
            opener(key)

    def _sync_overview(self):
        if self._disposed:
            return
        if isinstance(self.plans, PlansPage):
            try:
                self.overview.set_plan_state(self.plans.catalog, self.plans.current_plan,
                    self.plans.status.text(), (self._service_bound or self.plans.source is not None) and not self.plans.busy)
            except Exception:
                self.feature_errors["overview"] = "计划概览暂时无法显示，请重试。其他页面仍可使用。"
                self.overview.set_plan_state(None, None, self.feature_errors["overview"], True)
        else:
            self.overview.set_plan_state(None, None, "计划页面暂不可用，资料管理和账号操作仍可使用。", False)

    def _current_feature_session(self):
        """Pages/services keep this guarded provider, never a captured token."""
        if self._disposed or self._session_provider is None:
            return None
        try:
            result = self._session_provider()
            if result is not None and result.identity.account_id == self._account_id:
                return result
        except Exception:
            pass
        return None

    def bind_feature_context(self, *, session_provider, api_base_url, ca_bundle):
        """MF-01 GUI-thread entry. One endpoint/provider binding per window.

        The dictionary is shared with page factories and PlanService. Session
        refreshes are read through the provider; pages never receive tokens in
        the dictionary. Missing modules/configuration do not block the shell.
        """
        if self._disposed:
            return False
        try:
            url = urlsplit(api_base_url)
            if (url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.query or url.fragment or not callable(session_provider)):
                raise ValueError("invalid feature context")
            endpoint = api_base_url.rstrip("/")
            if self._context_bound and (endpoint != self.feature_context["api_base_url"]
                    or Path(ca_bundle) != self.feature_context["ca_bundle"]):
                raise ValueError("new endpoint requires new account window")
            bundle = Path(ca_bundle)
        except (ValueError, TypeError):
            self.feature_errors["context"] = "账号服务地址或证书配置无效，请从账号入口重新接入。"
            self._update_feature_notice()
            return False
        self._session_provider = session_provider
        self.feature_context.update(api_base_url=endpoint, ca_bundle=bundle)
        self._context_bound = True
        self.feature_errors.pop("context", None)
        self._load_product_preferences()
        self._ensure_shared_services()
        # Initialize Chromium while the account shell is still hidden.  On
        # Windows the first QWebEngine native surface can briefly toggle its
        # ancestor's visibility; doing that work before the home window is
        # shown keeps the first planning visit from flashing the whole app.
        self._preload_planning_page()
        self._ensure_sync()
        self.refresh_feature_availability()
        self._request_plan_refresh()
        return True

    def rebind_feature_context(self, *, session_provider, api_base_url, ca_bundle):
        """Replace account-scope-bound children while keeping this HWND alive.

        A refreshed session can select a different organization/workspace for
        the same account.  The old implementation disposed this top-level
        window and created another one, which made Windows briefly show the
        shell again and left the account badge out of sync with the page.  The
        shell is a presentation boundary, so reset its child pages/services in
        place and let the normal binding path construct the new scope.
        """
        if self._disposed:
            return False
        self._suspend_navigation_refresh = True
        self.pages.setUpdatesEnabled(False)
        try:
            self._plan_refresh_timer.stop()
            self._plan_refresh_pending = False
            self._pending_plan_key = ""
            for _page, signal, slot in self._feature_connections:
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            self._feature_connections.clear()
            self._opening_features.clear()
            if self._sync_controller is not None:
                try:
                    self._sync_controller.close()
                except Exception:
                    pass
                self._sync_controller = None
            self.sync_button.setEnabled(False)

            # Feature and account-base pages own executors, WebEngine views,
            # and detached teacher windows.  Dispose every old child before
            # replacing it so late completions cannot update the new scope.
            pages = list(self._feature_pages.values()) + [self.completion, self.plans, self.overview]
            seen = set()
            for page in pages:
                if page is None or id(page) in seen:
                    continue
                seen.add(id(page))
                self.pages.removeWidget(page)
                try:
                    page.dispose()
                except Exception:
                    pass
                page.hide()
                page.deleteLater()
            for key in ("plan_service", "model_client"):
                service = self.feature_context.get(key)
                self.feature_context[key] = None
                if service is not None:
                    try:
                        service.close()
                    except Exception:
                        pass

            self._feature_pages = {}
            self._destinations = {}
            self._context_bound = False
            self._service_bound = False
            self._session_provider = None
            self.feature_errors = {}
            self.feature_context.update(
                session_provider=self._current_feature_session,
                api_base_url="", ca_bundle=None, model_client=None,
                plan_service=None,
            )

            self.overview = OverviewPage(self.name_label.text() or "MentorFlow", self)
            self.completion = self._build_feature(
                "计划完成情况", lambda: PlanCompletionPage(parent=self)
            )
            self.materials = self.completion
            self.plans = self._build_feature(
                "我的计划", lambda: PlansPage(self._account_id, self._data_root, self)
            )
            self._destinations = {"overview": self.overview, "plans": self.plans, "completion": self.completion}
            for page in self._destinations.values():
                self.pages.addWidget(page)
            self.overview.open_plan.connect(self.open_plan)
            self.overview.open_materials.connect(lambda: self.navigate_to("completion"))
            if isinstance(self.plans, PlansPage):
                self.plans.open_completion.connect(self._open_completion_for_plan)
            if isinstance(self.plans, PlansPage):
                self.plans.state_changed.connect(self._plans_changed)
                self.overview.refresh_requested.connect(self._request_plan_refresh)
            else:
                self.overview.all_plans_button.setEnabled(False)
            self.pages.setCurrentWidget(self.overview)
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(self._nav_rows["overview"])
            self.navigation.blockSignals(False)
            self.refresh_feature_availability()
            self._sync_overview()
        finally:
            self.pages.setUpdatesEnabled(True)
            self._suspend_navigation_refresh = False
        return self.bind_feature_context(
            session_provider=session_provider, api_base_url=api_base_url, ca_bundle=ca_bundle,
        )

    def _preload_planning_page(self):
        """Construct and cache the planning page without navigating to it."""
        if self._disposed or not self._context_bound or "planning" in self._feature_pages:
            return
        current = self.pages.currentWidget()
        self._open_feature("planning", activate=False)
        # Adding a stack page should not alter the visible destination. Keep
        # this explicit because platform widgets may select a newly added
        # child when the stack is initialized differently.
        if current is not None and self.pages.currentWidget() is not current:
            self.pages.setCurrentWidget(current)
        self._sync_navigation()

    def _ensure_sync(self):
        service = self.feature_context.get("plan_service")
        if self._sync_controller is not None or not callable(getattr(service, "accept_snapshot", None)):
            return
        from .sync_controller import LearningSyncController
        self._sync_controller = LearningSyncController(self.feature_context, self)
        def status(message):
            if self._disposed:
                return
            self.sync_status.setText(message)
            self.sync_conflicts_button.setVisible("冲突" in message)
        self._sync_controller.status_changed.connect(status)
        self._sync_controller.restored.connect(self._sync_restored)
        self.sync_button.setEnabled(True)

    def _sync_restored(self):
        if self._disposed:
            return
        self._request_plan_refresh()
        if not getattr(self.completion, "busy", False):
            refresh = getattr(self.completion, "refresh", None)
            if callable(refresh):
                refresh()
        # A background pull must never replace an unsaved classroom note or
        # the task the learner is currently reading. Its explicit refresh
        # reloads the updated shared service when the learner is ready.

    def _show_sync_conflicts(self):
        if self._sync_controller is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("保留的同步冲突")
        dialog.resize(680, 520)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("以下保留了两台设备的不同修改，双方版本均可查看和复制。"))
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(json.dumps(self._sync_controller.conflicts(), ensure_ascii=False, indent=2))
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _load_product_preferences(self):
        try:
            if self._preference_store is None:
                from .preferences.store import PreferenceStore
                self._preference_store = PreferenceStore(self._account_id, self._data_root)
                self.feature_context.update(preferences_store=self._preference_store,
                                            preferences_provider=self._preference_store.load)
            self.apply_display_preferences(self._preference_store.load())
        except Exception:
            # A broken preference file is a settings-page error, never an
            # account, plan, or model readiness prerequisite.
            self.feature_errors["preferences"] = "偏好暂时无法读取，可在设置中重试。"

    def _ensure_shared_services(self):
        if self._disposed:
            return
        context = self.feature_context
        if context["model_client"] is None:
            self._create_service("model_client", "refactor.learning.connections.client", "ModelClient",
                (self._account_id, self._data_root), ("close",))
        if context["plan_service"] is None and self._context_bound:
            self._create_service("plan_service", "refactor.learning.planning.service", "PlanService",
                (context,), ("list_plans", "current_plan", "select_plan", "record_task_progress", "close"))
        service = context["plan_service"]
        bind_materials = getattr(self.completion, "bind_plan_service", None)
        if callable(bind_materials):
            try:
                bind_materials(service)
            except Exception:
                self.feature_errors["completion"] = "计划完成情况暂不可用，请重试账号同步。"
            else:
                self.feature_errors.pop("completion", None)
        if service is not None and not self._service_bound:
            bind_context = getattr(self.plans, "bind_context", None)
            if callable(bind_context):
                try:
                    bind_context(context)
                except Exception:
                    self.feature_errors["plans"] = "计划页面上下文暂不可用，请重试连接。"
            binder = getattr(self.plans, "bind_service", None)
            if not callable(binder):
                self.feature_errors["plans"] = "计划列表接口尚未接入（PlansPage.bind_service）。"
                if hasattr(self.plans, "status"):
                    self.plans.status.setText(self.feature_errors["plans"])
            else:
                try:
                    binder(service)
                except Exception:
                    self.feature_errors["plans"] = "计划列表暂时无法接入，请稍后重试。"
                    if hasattr(self.plans, "status"):
                        self.plans.status.setText(self.feature_errors["plans"])
                else:
                    self._service_bound = True
                    self.feature_errors.pop("plans", None)
        self._sync_overview()
        self._update_feature_notice()

    def _create_service(self, key, module, name, arguments, methods):
        candidate = None
        try:
            factory = getattr(import_module(module), name)
            candidate = factory(*arguments)
            if any(not callable(getattr(candidate, method, None)) for method in methods):
                raise TypeError("incomplete shared service interface")
        except Exception:
            if candidate is not None and callable(getattr(candidate, "close", None)):
                try:
                    candidate.close()
                except Exception:
                    pass
            self.feature_errors[key] = f"{module}.{name} 尚未接入或暂不可用。"
        else:
            self.feature_context[key] = candidate
            self.feature_errors.pop(key, None)

    def refresh_feature_availability(self):
        """Recheck delivered page code, without constructing it or reading data."""
        if self._disposed:
            return
        for key, (title, module) in FEATURE_PAGES.items():
            available = key in self._feature_pages or _module_available(module)
            item = self.navigation.item(self._nav_rows[key])
            item.setText(title if available else title + " · 未接入")
            item.setData(Qt.ItemDataRole.UserRole, key if available else None)
            flags = item.flags()
            item.setFlags(flags | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable if available
                else flags & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
            item.setToolTip("" if available else "此页面尚未交付，其他学习功能可继续使用。")
            if not available:
                self.feature_errors[key] = f"缺少 {module}.create_page(context, parent=None)。"

    def _open_feature(self, key, *, retry=False, activate=True):
        if self._disposed:
            return
        existing = self._feature_pages.get(key)
        if existing is not None and not retry:
            if activate:
                self.pages.setCurrentWidget(existing)
            return
        if key in self._opening_features:
            # A queued/currentRowChanged callback reached us while the
            # factory below is still running.  The in-flight call owns the
            # page and will select it when construction finishes.
            return
        self._opening_features.add(key)
        try:
            self._ensure_shared_services()
            title, module = FEATURE_PAGES[key]
            page = None
            # Construct feature pages directly under the stack.  A temporary
            # top-level QWidget here forces Qt to reparent the WebEngine page
            # after Chromium has created its native surface; on the first
            # planning visit after login that can recreate the native window
            # hierarchy and make the whole app visibly flash.
            owner = self.pages
            existing_children = set(owner.children())
            try:
                page = import_module(module).create_page(self.feature_context, parent=owner)
                if not isinstance(page, QWidget) or not callable(getattr(page, "dispose", None)):
                    raise TypeError("create_page must return QWidget with dispose")
                signal_name = {"planning": "plan_saved", "preferences": "preferences_changed",
                               "classroom": "preferences_changed", "tools": "preferences_changed"}.get(key)
                if signal_name and not callable(getattr(getattr(page, signal_name, None), "connect", None)):
                    raise TypeError("page signal missing")
                if signal_name:
                    signal = getattr(page, signal_name)
                    def changed(*args, _page=page):
                        if self._disposed or self._feature_pages.get(key) is not _page:
                            return
                        if key == "planning":
                            self._request_plan_refresh()
                        elif args:
                            self.apply_display_preferences(args[0])
                    signal.connect(changed)
                    self._feature_connections.append((page, signal, changed))
                apply_preferences = getattr(page, "apply_preferences", None)
                if callable(apply_preferences) and self._preference_store is not None:
                    apply_preferences(self._preference_store.load())
            except Exception:
                if page is not None and callable(getattr(page, "dispose", None)):
                    try:
                        page.dispose()
                    except Exception:
                        pass
                if isinstance(page, QWidget):
                    page.hide()
                    page.deleteLater()
                # Factories may fail after attaching helper widgets to the
                # supplied parent. Dispose only children created by this
                # attempt so no half-built page is left in the stack.
                for child in owner.children():
                    if child not in existing_children and isinstance(child, QWidget):
                        child.hide()
                        child.deleteLater()
                self.feature_errors[key] = "此页面暂时无法打开。请检查该功能的配置后重试；其他页面仍可使用。"
                page = UnavailablePage(title, message=self.feature_errors[key],
                    retry=lambda: self._open_feature(key, retry=True))
            else:
                self.feature_errors.pop(key, None)
            if existing is not None:
                self.pages.removeWidget(existing)
                try:
                    existing.dispose()
                except Exception:
                    pass
                existing.deleteLater()
            self._feature_pages[key] = page
            self._destinations[key] = page
            self.pages.addWidget(page)
            if activate:
                self.pages.setCurrentWidget(page)
            self._sync_navigation()
        finally:
            self._opening_features.discard(key)

    def _page_changed(self, _index):
        if not self._disposed and self._preference_store is not None:
            self._load_product_preferences()
        self._update_feature_notice()
        # Navigation must not refresh the plan service.  A page switch is a
        # presentation change, not a data change; refreshing here caused the
        # planning page/WebEngine to flash and made a single click appear to
        # reopen the creation flow.

    def _update_feature_notice(self):
        if self._disposed:
            return
        key = next((key for key, page in self._destinations.items() if page is self.pages.currentWidget()), "")
        needs = {
            "plans": ("context", "plan_service", "plans"),
            "planning": ("context", "plan_service", "model_client"),
            "classroom": ("context", "plan_service", "model_client"),
            "connections": ("model_client",), "tools": ("model_client",),
        }.get(key, ())
        messages = {"context": "账号服务配置无效，请从账号入口重新接入。",
            "plan_service": "计划服务暂不可用，可重试连接。",
            "plans": "计划列表连接暂不可用，可重试。",
            "model_client": "模型连接服务暂不可用，可重试连接。"}
        errors = [name for name in needs if name in self.feature_errors]
        self.feature_status_label.setText("\n".join(messages[name] for name in errors))
        self.feature_retry_button.setVisible(any(name != "context" for name in errors))
        self.feature_banner.setVisible(bool(errors))

    def _retry_feature_dependencies(self):
        if not self._disposed:
            self._ensure_shared_services()
            self._request_plan_refresh()

    def session_recovered(self):
        """Let pages clear a transient request error after auth recovery.

        The account shell owns refresh and only calls this after a new
        server-confirmed result is accepted.  A page may keep unsaved lesson
        input, so recovery is a notification rather than an unconditional
        data reload.
        """
        if self._disposed:
            return
        # A successful access-token rotation happens on a timer while the
        # learner may be reading, typing, or watching the teacher.  It does
        # not change the account snapshot, so rebuilding PlansPage or the
        # classroom here makes the visible page flash for no reason.  Retry
        # the data dependency only when the last request actually left a
        # feature in an error state; ordinary timer refreshes only notify
        # pages that their session is usable again.
        self._ensure_shared_services()
        retryable = {"context", "plan_service", "plans", "model_client"}
        if retryable.intersection(self.feature_errors):
            self._request_plan_refresh()
        for page in tuple(self._feature_pages.values()):
            recover = getattr(page, "session_recovered", None)
            if callable(recover):
                try:
                    recover()
                except Exception:
                    # Recovery of one feature must not block the account shell
                    # or another feature from using the new session.
                    continue

    def _request_plan_refresh(self, *_args):
        if self._disposed:
            return
        self._plan_refresh_pending = True
        if not getattr(self.plans, "busy", False):
            self._flush_plan_refresh()

    def _flush_plan_refresh(self):
        if self._disposed or not self._plan_refresh_pending or getattr(self.plans, "busy", False):
            return
        if not self._service_bound and getattr(self.plans, "source", None) is None:
            self._plan_refresh_pending = False
            return
        self._plan_refresh_pending = False
        try:
            self.plans.refresh()
        except Exception:
            self.feature_errors["plans"] = "计划刷新失败，可重试；其他页面仍可使用。"
            self.overview.set_plan_state(None, None, self.feature_errors["plans"], True)
        else:
            self.feature_errors.pop("plans", None)
            self._sync_overview()
        self._update_feature_notice()

    def _plans_changed(self):
        if self._disposed:
            return
        self._sync_overview()
        if self._pending_plan_key and not getattr(self.plans, "busy", False):
            key = self._pending_plan_key
            opener = getattr(self.plans, "open_plan", None)
            if callable(opener) and opener(key):
                self._pending_plan_key = ""
            else:
                index = self.plans.selector.findData(key)
                if index >= 0:
                    self._pending_plan_key = ""
                    self.plans.selector.setCurrentIndex(index)
        if self._plan_refresh_pending and not getattr(self.plans, "busy", False):
            self._plan_refresh_timer.start(0)

    def apply_display_preferences(self, preferences):
        """Apply saved product preferences to the actual page consumers."""
        if self._disposed or not isinstance(preferences, dict):
            return
        scale = preferences.get("font_scale")
        if type(scale) in (float, int) and 0.85 <= scale <= 1.5:
            self._display_preferences["font_scale"] = float(scale)
        for key in ("compact_mode", "show_calendar"):
            if type(preferences.get(key)) is bool:
                self._display_preferences[key] = preferences[key]
        self.setStyleSheet(display_style(self._display_preferences))
        self.overview.apply_display_preferences(self._display_preferences)
        product_keys = {"classroom_theme", "free_chat_prompt", "analogy_keywords", "task_colors",
                        "font_scale", "compact_mode", "show_calendar"}
        product_values = {key: value for key, value in preferences.items() if key in product_keys}
        if product_values:
            current = dict(self.feature_context.get("preferences", {}))
            current.update(product_values)
            self.feature_context["preferences"] = current
        for page in (self.plans, *self._feature_pages.values()):
            apply = getattr(page, "apply_preferences", None)
            if callable(apply):
                apply(self.feature_context.get("preferences", {}))

    def bind_plan_session(self, *, session_provider, api_base_url, ca_bundle, legacy_data_root=None):
        from .plan_source import AuthenticatedPlanSource
        if self._disposed or not isinstance(self.plans, PlansPage):
            return
        source = None
        try:
            source = AuthenticatedPlanSource(account_id=self._account_id,
                session_provider=session_provider, api_base_url=api_base_url, ca_bundle=ca_bundle)
            self.plans.bind_source(source, legacy_data_root=legacy_data_root)
        except Exception:
            if source:
                source.close()
            self.plans.status.setText("计划服务配置暂不可用；资料入口仍可使用，请检查账号服务配置。")
        self._sync_overview()

    def request_current_plan_export(self, on_finished, *, cleanup_roots=()):
        """GUI-thread entry; callback receives exactly one ExportResult."""
        # Export already refreshes its source. Do not start a competing refresh
        # merely by navigating here from an account-settings dialog.
        self._suspend_navigation_refresh = True
        try:
            self.navigate_to("plans")
        finally:
            self._suspend_navigation_refresh = False
        if self._disposed or not isinstance(self.plans, PlansPage):
            on_finished(ExportResult(ExportStatus.FAILED, "PLAN_NOT_READY", "计划页面暂不可用。", account_id=self._account_id))
            return
        self._plan_refresh_pending = False
        self._plan_refresh_timer.stop()
        completed = False
        def finish(result):
            nonlocal completed
            if not completed:
                completed = True
                on_finished(result)
        try:
            self.plans.request_export(finish, cleanup_roots=cleanup_roots)
        except Exception:
            finish(ExportResult(ExportStatus.FAILED, "EXPORT_FAILED", "导出入口暂不可用，请重试。", account_id=self._account_id))

    def set_server_role(self, role: object) -> None:
        value = "服务端身份：管理员" if role == "SUPER_ADMIN" else "服务端身份：学习者" if role == "LEARNER" else "服务端身份：待确认"
        self.role_label.setText(value)

    def set_session_notice(self, message: str, *, show_verify: bool = True) -> None:
        self.status_label.setText(message)
        self.session_banner.setVisible(bool(message))
        self.verify_button.setVisible(bool(message) and show_verify)

    def clear_session_notice(self) -> None:
        self.set_session_notice("")

    def bind_account_settings(self, callback) -> None:
        self.settings_requested.connect(callback)
        self.settings_button.setEnabled(True)
        self.settings_button.setToolTip("打开账号设置")

    def dispose(self) -> None:
        """Shell calls this on logout, switch, expiration and application exit."""
        if self._disposed:
            return
        self._disposed = True
        self._session_provider = None
        if self._sync_controller is not None:
            self._sync_controller.close()
        self._plan_refresh_timer.stop()
        self._plan_refresh_pending = False
        for _page, signal, slot in self._feature_connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._feature_connections.clear()
        for page in (*self._feature_pages.values(), self.completion, self.plans, self.overview):
            try:
                page.dispose()
            except Exception:
                # Cleanup of one feature must not retain another feature's UI.
                pass
            finally:
                page.hide()
        # Page switches keep these services alive. Only the account owner calls
        # dispose after accepting logout/switch; button signals remain requests.
        for key in ("plan_service", "model_client"):
            service = self.feature_context[key]
            self.feature_context[key] = None
            if service is not None:
                try:
                    service.close()
                except Exception:
                    self.feature_errors[key] = "服务关闭未完成。"
        self.name_label.clear()
        self.role_label.clear()
        self.api_label.clear()
        self.feature_status_label.clear()
        self.feature_banner.hide()
        self.clear_session_notice()
        self.setEnabled(False)

    def closeEvent(self, event) -> None:
        self.dispose()
        super().closeEvent(event)


# The account shell imports ``LearningWindow`` from here. The new single-page
# window (ARCH-01) is the default; MENTORFLOW_LEGACY_UI=1 keeps the old pages
# available until the new ones replace them.
import os as _os

if _os.environ.get("MENTORFLOW_LEGACY_UI") == "1":
    LearningWindow = LegacyLearningWindow
else:
    from .app.window import LearningWindow  # noqa: E402,F401
