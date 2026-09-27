"""The learning window the account shell creates after login (ARCH-01).

Public surface is identical to the legacy ``LearningWindow`` so the account
shell (ACC-02) is untouched. Inside, one QWebEngineView hosts the React app from
``refactor/learning/web/dist``; everything else goes through ``AppBridge``.
"""
from __future__ import annotations

import logging
from pathlib import Path
import threading
from urllib.parse import urlsplit

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
                                     QWebEngineUrlRequestInterceptor)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QFileDialog, QVBoxLayout, QWidget

from .bridge import AppBridge
from . import handlers
from .services import AppServices

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parents[1] / "web" / "dist"
_ROLE_LABELS = {"SUPER_ADMIN": "管理员", "LEARNER": "学习者"}


class _LocalOnly(QWebEngineUrlRequestInterceptor):
    """Only the bundled app files and Qt's channel script may load."""

    def __init__(self, roots, parent=None):
        super().__init__(parent)
        self.roots = tuple(Path(r).resolve() for r in roots)

    def interceptRequest(self, info):  # noqa: N802 - Qt API
        url = info.requestUrl()
        allowed = url.scheme() == "qrc" and url.path() == "/qtwebchannel/qwebchannel.js"
        if url.isLocalFile():
            try:
                target = Path(url.toLocalFile()).resolve()
                allowed = any(target.is_relative_to(root) for root in self.roots)
            except (OSError, ValueError):
                allowed = False
        elif url.scheme() == "data":
            allowed = True
        if not allowed:
            info.block(True)


class _AppPage(QWebEnginePage):
    def __init__(self, profile, parent, entry: Path):
        super().__init__(profile, parent)
        self.entry = entry.resolve()

    def acceptNavigationRequest(self, url, kind, is_main_frame):  # noqa: N802 - Qt API
        if url.isLocalFile():
            try:
                return Path(url.toLocalFile()).resolve() == self.entry
            except (OSError, ValueError):
                return False
        # Links clicked inside the app open in the system browser (https only).
        if url.scheme() == "https" and kind == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
        return False

    def createWindow(self, _type):  # noqa: N802 - never open extra web windows
        return None


class LearningWindow(QWidget):
    switch_requested = Signal()
    logout_requested = Signal()
    verify_requested = Signal()
    settings_requested = Signal()

    def __init__(self, display_name: str, server_role: object, api_base_url: str, *,
                 account_id: str, data_root: Path, parent=None):
        super().__init__(parent)
        self._disposed = False
        self._account_id = account_id
        self._data_root = Path(data_root)
        self._display_name = display_name or "MentorFlow 账号"
        self._role = server_role
        self._api_label = api_base_url
        self._settings_bound = False
        self.services: AppServices | None = None
        self.session_notice = None
        self._bound_endpoint = None
        self.setObjectName("mentorflowLearningHome")
        self.setWindowTitle("MentorFlow")
        self.resize(1480, 940)
        self.setMinimumSize(1100, 720)
        self.setStyleSheet("#mentorflowLearningHome { background: #fbf4f7; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        layout.addWidget(self.view)
        self.profile = QWebEngineProfile(self)  # off-the-record: nothing persisted by Chromium
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        cache_root = self._data_root / ".web-cache" / "app"
        self.profile.setPersistentStoragePath(str(cache_root / "storage"))
        self.profile.setCachePath(str(cache_root / "cache"))
        self.interceptor = _LocalOnly((WEB_ROOT,), self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        entry = WEB_ROOT / "index.html"
        self.page = _AppPage(self.profile, self.view, entry)
        self.view.setPage(self.page)
        settings = self.page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        self.page.setBackgroundColor(Qt.GlobalColor.transparent)
        self.bridge = AppBridge(self)
        handlers.register(self.bridge, self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("mf", self.bridge)
        self.page.setWebChannel(self.channel)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=lambda: self.window_action("toggle_fullscreen"))
        if entry.is_file():
            self.view.setUrl(QUrl.fromLocalFile(str(entry)))
        else:
            self.view.setHtml("<body style='font-family:sans-serif;padding:40px'>新界面尚未构建（缺少 web/dist）。"
                              "可设置环境变量 MENTORFLOW_LEGACY_UI=1 使用旧界面。</body>")

    # ------------------------------------------------------------ for handlers
    def account_info(self):
        return {"name": self._display_name, "role": self._role if isinstance(self._role, str) else None,
                "role_label": _ROLE_LABELS.get(self._role, "待确认"), "api": self._api_label,
                "settings_available": self._settings_bound}

    def request_account_action(self, action):
        signal = {"switch": self.switch_requested, "logout": self.logout_requested,
                  "verify": self.verify_requested, "account_settings": self.settings_requested}[action]
        # Leave the bridge call first; the shell may dispose this window.
        QTimer.singleShot(0, signal.emit)

    def window_action(self, action):
        top = self.window()
        if action == "toggle_fullscreen" and not top.isFullScreen():
            top.showFullScreen()
        elif top.isFullScreen():
            top.showNormal()
        full = top.isFullScreen()
        self.bridge.push("window", {"fullscreen": full})
        return full

    def settings_changed(self, settings):
        self.bridge.push("settings", settings)

    # --------------------------------------------------- account shell API
    def bind_feature_context(self, *, session_provider, api_base_url, ca_bundle):
        if self._disposed:
            return False
        try:
            url = urlsplit(api_base_url)
            if (url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.query or url.fragment or not callable(session_provider)):
                raise ValueError("invalid feature context")
            endpoint = (api_base_url.rstrip("/"), str(Path(ca_bundle)))
            if self._bound_endpoint is not None and endpoint != self._bound_endpoint:
                raise ValueError("new endpoint requires new account window")
        except (ValueError, TypeError):
            self.set_session_notice("账号服务地址或证书配置无效，请从账号入口重新登录。", show_verify=False)
            return False
        self._bound_endpoint = endpoint
        guarded = self._guarded_session(session_provider)
        self.services = AppServices(account_id=self._account_id, data_root=self._data_root,
                                    session_provider=guarded, api_base_url=endpoint[0], ca_bundle=Path(ca_bundle))
        self.services.refresh_catalog()
        self.bridge.push("ready", {"account": self.account_info()})
        return True

    def rebind_feature_context(self, *, session_provider, api_base_url, ca_bundle):
        if self._disposed:
            return False
        old, self.services = self.services, None
        if old is not None:
            old.close()
        self._bound_endpoint = None
        self.bridge.push("scope_changed", None)
        return self.bind_feature_context(session_provider=session_provider, api_base_url=api_base_url,
                                         ca_bundle=ca_bundle)

    def _guarded_session(self, provider):
        account_id = self._account_id

        def current():
            if self._disposed:
                return None
            try:
                result = provider()
                if result is not None and result.identity.account_id == account_id:
                    return result
            except Exception:  # noqa: BLE001 - a transient auth failure reads as "no session"
                pass
            return None
        return current

    def set_server_role(self, role: object) -> None:
        self._role = role
        self.bridge.push("account", self.account_info())

    def set_session_notice(self, message: str, *, show_verify: bool = True) -> None:
        self.session_notice = {"message": message, "show_verify": show_verify} if message else None
        self.bridge.push("session_notice", self.session_notice)

    def clear_session_notice(self) -> None:
        self.set_session_notice("")

    def session_recovered(self):
        if not self._disposed:
            self.bridge.push("session_recovered", None)

    def bind_account_settings(self, callback) -> None:
        self.settings_requested.connect(callback)
        self._settings_bound = True
        self.bridge.push("account", self.account_info())

    def bind_plan_session(self, **_kwargs):
        """Legacy hook; plans now come from the account's course store."""

    def request_current_plan_export(self, on_finished, *, cleanup_roots=()):
        """Before account deletion: export the current plan (ACC-02). One callback, always."""
        from ..course.export import export_plan
        from ..plan_export import ExportResult, ExportStatus
        done = {"called": False}

        def finish(result):
            if not done["called"]:
                done["called"] = True
                on_finished(result)

        def fail(status, code, message, **kw):
            finish(ExportResult(status, code, message, account_id=self._account_id, **kw))

        if self._disposed or self.services is None:
            return fail(ExportStatus.FAILED, "PLAN_NOT_READY", "学习数据尚未准备好，请稍后重试。")
        try:
            plans = [p for p in self.services.store.list_plans() if p.get("state") != "creating"]
        except Exception:  # noqa: BLE001
            return fail(ExportStatus.FAILED, "PLAN_READ_FAILED", "读取学习计划失败，请重试。")
        if not plans:
            return fail(ExportStatus.NO_CURRENT_PLAN, "NO_CURRENT_PLAN", "当前账号没有可导出的学习计划。")
        order = {"active": 0, "checking": 1, "ready": 2, "completed": 3}
        plan = sorted(plans, key=lambda p: order.get(p.get("state"), 9))[0]
        suggested = str(Path.home() / "Documents" / "MentorFlow-学习计划导出.zip")
        target, _ = QFileDialog.getSaveFileName(self, "保存学习计划", suggested, "ZIP 文件 (*.zip)")
        if not target:
            return fail(ExportStatus.CANCELLED, "EXPORT_CANCELLED", "已取消保存。", plan_id=plan["id"])
        target_path = Path(target).resolve()
        for root in cleanup_roots:
            try:
                if target_path.is_relative_to(Path(root).resolve()):
                    return fail(ExportStatus.FAILED, "EXPORT_TARGET_INVALID",
                                "保存位置在将被清理的目录里，请换一个位置。", plan_id=plan["id"])
            except (OSError, ValueError):
                continue
        store = self.services.store

        def work():
            try:
                result = export_plan(store, plan["id"], target_path)
            except Exception:  # noqa: BLE001
                log.exception("plan export failed")
                result = ExportResult(ExportStatus.FAILED, "EXPORT_FAILED", "导出没有完成，请重试。",
                                      plan_id=plan["id"], account_id=self._account_id)
            QTimer.singleShot(0, self, lambda: finish(result))
        threading.Thread(target=work, name="mf-export", daemon=True).start()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.bridge.close()
        if self.services is not None:
            self.services.close()
            self.services = None
        try:
            self.view.stop()
            self.page.setUrl(QUrl("about:blank"))
        except RuntimeError:
            pass
        self.setEnabled(False)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.dispose()
        super().closeEvent(event)
