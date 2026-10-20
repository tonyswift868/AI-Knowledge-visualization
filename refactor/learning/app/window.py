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

from PySide6.QtCore import QEvent, QObject, QRect, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
                                     QWebEngineUrlRequestInterceptor)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QFileDialog, QVBoxLayout, QWidget

from .bridge import AppBridge, BridgeError
from ..preferences.storage import AccountJSON, StorageError
from . import handlers
from .services import AppServices
from .sync import WindowSync
from .teacher_assets import TeacherAssets
from .reminder import register as register_reminder
from .voice_input import register as register_voice_input
from .speech import TeacherSpeech
from refactor.runtime_logging import event as diagnostic_event
from refactor.desktop.process_diagnostics import watch_renderer

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parents[1] / "web" / "dist"
# Same values as --bg in web/src/theme.css.
THEME_BACKGROUNDS = {"sakura": "#fbf4f7", "aurora": "#0c0e1c"}
_ROLE_LABELS = {"SUPER_ADMIN": "管理员", "LEARNER": "学习者"}


class _TeacherDrawer(QObject):
    """Reserve native window space for the classroom rail; all calls are on Qt's UI thread."""
    EDGE = 32

    def __init__(self, top, preferences, notify):
        super().__init__(top)
        self.top, self.preferences, self.notify = top, preferences, notify
        self.open, self.width, self.mode = False, 320, 'internal'
        self.view = dict(scale=1, panX=0, panY=0)
        self.extension, self.base, self.last, self.pending = 0, None, None, None
        self.suspended = None
        try:
            self.saved = preferences.read({})
        except StorageError:
            self.saved = {}
        self.remembered_open = bool(self.saved.get('open', False))
        geometry = self.saved.get('geometry')
        if isinstance(geometry, list) and len(geometry) == 4 and all(type(n) is int for n in geometry):
            rect = QRect(*geometry)
            if rect.width() >= top.minimumWidth() and rect.height() >= top.minimumHeight():
                screen = self.available()
                rect.moveLeft(max(screen.left(), min(rect.x(), screen.right() - rect.width() + 1)))
                rect.moveTop(max(screen.top(), min(rect.y(), screen.bottom() - rect.height() + 1)))
                top.setGeometry(rect)
        top.installEventFilter(self)

    def available(self):
        return self.top.screen().availableGeometry()

    def special(self):
        return self.top.isMaximized() or self.top.isFullScreen()

    def collapsed_geometry(self, current):
        rect = QRect(current)
        rect.setWidth(max(self.top.minimumWidth(), rect.width() - self.extension))
        if self.last is not None and self.base is not None:
            rect.moveTopLeft(self.base.topLeft())
        return rect

    def extend(self):
        current = self.top.geometry()
        self.base = self.collapsed_geometry(current)
        if self.special():
            self.mode = 'internal'
            return
        extra = self.width - self.EDGE
        frame = self.top.frameGeometry()
        left, right = current.x() - frame.x(), frame.right() - current.right()
        screen = self.available()
        target = QRect(self.base)
        target.setWidth(self.base.width() + extra)
        if target.width() + left + right > screen.width():
            self.top.setGeometry(self.base)
            self.extension, self.mode = 0, 'internal'
        else:
            target.moveLeft(max(screen.left() + left, min(target.x(), screen.right() - right - target.width() + 1)))
            self.top.setGeometry(target)
            self.extension, self.mode = extra, 'external'
        self.last = QRect(self.top.geometry())

    def snapshot(self):
        return dict(open=self.open, width=self.width, view=dict(self.view), mode=self.mode,
                    reason='window_state' if self.special() else ('screen_too_small' if self.open and self.mode == 'internal' else None))

    def save(self, *, keep_open=None):
        current = self.top.normalGeometry() if self.special() else self.top.geometry()
        rect = self.pending if self.pending is not None else self.collapsed_geometry(current)
        self.saved = dict(open=self.remembered_open if keep_open is None else keep_open, width=self.width,
                          view=dict(self.view), geometry=[rect.x(), rect.y(), rect.width(), rect.height()])
        try:
            self.preferences.write(self.saved)
        except StorageError:
            log.warning('teacher drawer preferences could not be saved')

    def action(self, action, params):
        restored = bool(params.get('restore'))
        values = self.saved if restored and self.saved else params
        width = values.get('width', self.width)
        if type(width) not in (int, float) or not 32 <= width <= 520:
            raise BridgeError('PARAM_INVALID', '老师栏宽度无效。')
        view = values.get('view', self.view)
        if (not isinstance(view, dict) or any(type(view.get(k)) not in (int, float) for k in ('scale', 'panX', 'panY'))
                or not .5 <= view['scale'] <= 2.5 or not -1 <= view['panX'] <= 1 or not -1 <= view['panY'] <= 1):
            raise BridgeError('PARAM_INVALID', '老师显示位置无效。')
        self.width, self.view = round(width), dict(view)
        opening = values.get('open', False) if restored else action == 'teacher_open'
        keep_open = self.open if params.get('preserve') else None
        if not params.get('preserve'):
            self.remembered_open = bool(opening)
        if opening:
            self.open = True
            if self.special():
                self.mode = 'internal'
            else:
                self.extend()
        else:
            if self.open:
                current = self.top.normalGeometry() if self.special() else self.top.geometry()
                target = self.suspended if self.special() and self.suspended is not None else self.collapsed_geometry(current)
                if self.special():
                    self.pending = target
                else:
                    self.top.setGeometry(target)
                self.base, self.last = target, None
            self.open, self.extension, self.mode = False, 0, 'internal'
        self.save(keep_open=keep_open)
        return self.snapshot()

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(0, self.top, self.state_changed)
        return False

    def state_changed(self):
        if self.special():
            if self.suspended is None:
                self.suspended = self.collapsed_geometry(self.top.normalGeometry())
            self.mode = 'internal'
        else:
            if self.pending is not None:
                self.top.setGeometry(self.pending)
                self.pending = None
            elif self.suspended is not None:
                self.top.setGeometry(self.suspended)
            elif self.extension:
                self.top.setGeometry(self.collapsed_geometry(self.top.geometry()))
            self.suspended = None
            self.extension, self.last = 0, None
            if self.open:
                self.extend()
        self.save()
        self.notify(self.snapshot())


class _LocalOnly(QWebEngineUrlRequestInterceptor):
    """Only the bundled app files and Qt's channel script may load."""

    def __init__(self, roots, parent=None, *, teacher_assets=None):
        super().__init__(parent)
        self.roots = tuple(Path(r).resolve() for r in roots)
        self.teacher_assets = teacher_assets

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
        elif url.scheme() == "mentorflow":
            allowed = (bytes(info.requestMethod()) == b"GET" and self.teacher_assets is not None
                       and self.teacher_assets.allows(url))
        if not allowed:
            info.block(True)


class _AppPage(QWebEnginePage):
    def __init__(self, profile, parent, entry: Path):
        super().__init__(profile, parent)
        watch_renderer(self, 'LEARNING_PAGE')
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

    def createWindow(self, _type):  # noqa: N802 - target=_blank links land here, not in acceptNavigationRequest
        return _ExternalLinkPage(self.profile(), self)


class _ExternalLinkPage(QWebEnginePage):
    """Throwaway page for a new-window request: hands its https URL to the system browser."""

    def __init__(self, profile, parent):
        super().__init__(profile, parent)
        watch_renderer(self, 'EXTERNAL_LINK_PAGE')

    def acceptNavigationRequest(self, url, kind, is_main_frame):  # noqa: N802 - Qt API
        if url.scheme() == "https":
            QDesktopServices.openUrl(url)
        self.deleteLater()
        return False


class LearningWindow(QWidget):
    switch_requested = Signal()
    # Bridge handlers run on worker threads; widget changes must hop to the GUI thread.
    _theme_changed = Signal(str)
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
        self._sync = None
        self._sync_fallback = dict(state='reauth', at=None, message='请先登录账号。')
        self.session_notice = None
        self._bound_endpoint = None
        self.setObjectName("mentorflowLearningHome")
        self.setWindowTitle("MentorFlow")
        self.resize(1480, 940)
        self.setMinimumSize(1100, 720)
        self._teacher_drawer = _TeacherDrawer(self.window(),
            AccountJSON(account_id, self._data_root, 'teacher-drawer.json'),
            lambda state: self.bridge.push('teacher_window', state) if hasattr(self, 'bridge') and not self._disposed else None)

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
        self.teacher_assets = TeacherAssets(self.profile)
        self.interceptor = _LocalOnly((WEB_ROOT,), self.profile, teacher_assets=self.teacher_assets)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        entry = WEB_ROOT / "index.html"
        self.page = _AppPage(self.profile, self.view, entry)
        self.view.setPage(self.page)
        settings = self.page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        # An opaque page background matching the theme: a transparent one lets the
        # native window show through between animated frames (visible flicker).
        self._apply_theme("sakura")
        self._theme_changed.connect(self._apply_theme, Qt.ConnectionType.QueuedConnection)
        self.bridge = AppBridge(self)
        handlers.register(self.bridge, self)
        self.teacher_assets.register(self.bridge, self)
        self.voice_input = register_voice_input(self.bridge, self)
        self.reminder = register_reminder(self)
        # A window left open past the refresh time moves the home page to the new study day (PLAN-05).
        self.reminder.study_day_changed.connect(lambda day: self.bridge.push("study_day", {"day": day.isoformat()}))
        self.speech = TeacherSpeech(self.bridge, self._data_root, self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("mf", self.bridge)
        self.page.setWebChannel(self.channel)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=lambda: self.window_action("toggle_fullscreen"))
        if entry.is_file():
            self.view.setUrl(QUrl.fromLocalFile(str(entry)))
        else:
            self.view.setHtml("<body style='font-family:sans-serif;padding:40px'>新界面尚未构建（缺少 web/dist）。</body>")

    # ------------------------------------------------------------ for handlers
    def account_info(self):
        return {"name": self._display_name, "account_id": self._account_id,
                "role": self._role if isinstance(self._role, str) else None,
                "role_label": _ROLE_LABELS.get(self._role, "待确认"), "api": self._api_label,
                "settings_available": self._settings_bound}

    def request_account_action(self, action):
        if action in {'switch', 'logout'}:
            self.speech.reset()
            self.bridge.cancel_pending()
            if self._sync is not None:
                self._sync.stop()
        signal = {"switch": self.switch_requested, "logout": self.logout_requested,
                  "verify": self.verify_requested, "account_settings": self.settings_requested}[action]
        # Leave the bridge call first; the shell may dispose this window.
        QTimer.singleShot(0, signal.emit)

    def window_action(self, action, **params):
        top = self.window()
        if action == 'quit':
            from PySide6.QtWidgets import QApplication
            diagnostics = getattr(QApplication.instance(), '_mentorflow_diagnostics', None)
            if diagnostics is not None:
                diagnostics.request_exit('USER_MENU_EXIT')
                top.close()
            return True
        if action in {'teacher_open', 'teacher_close'}:
            return self._teacher_drawer.action(action, params)
        if action == "toggle_fullscreen" and not top.isFullScreen():
            top.showFullScreen()
        elif top.isFullScreen():
            top.showNormal()
        full = top.isFullScreen()
        self.bridge.push("window", {"fullscreen": full})
        return full

    def settings_changed(self, settings):
        """Called from bridge worker threads: only signals, never touches widgets here."""
        self._theme_changed.emit(settings.get("theme", "sakura"))
        self.bridge.push("settings", settings)
        self.reminder.settings_changed(settings)  # hops to the GUI thread itself

    def _apply_theme(self, theme):
        self.current_theme = theme if theme in THEME_BACKGROUNDS else "sakura"  # read by account dialogs
        color = THEME_BACKGROUNDS[self.current_theme]
        self.page.setBackgroundColor(QColor(color))
        self.setStyleSheet(f"#mentorflowLearningHome {{ background: {color}; }}")

    def sync_status(self):
        return self._sync.status() if self._sync is not None else dict(self._sync_fallback)

    def sync_now(self):
        if self._sync is not None:
            return self._sync.sync_now()
        if self.services is not None and not self._disposed:
            self._start_sync()
        return self.sync_status()

    def _start_sync(self):
        try:
            self._sync = WindowSync(self, self.services)
            self._sync.start()
        except Exception:
            # Optional sync failure must not prevent local work or account settings.
            log.warning('window sync could not start', exc_info=True)
            if self._sync is not None:
                self._sync.dispose()
                self._sync = None
            self._sync_fallback = dict(state='failed', at=None, message='同步暂时无法启动，本机记录已保留，请重试。')
            self.bridge.push('sync_status', self._sync_fallback)

    def _close_services(self):
        self.speech.reset()
        self.bridge.cancel_pending()
        if self._sync is not None:
            self._sync.dispose()
            self._sync = None
        old, self.services = self.services, None
        if old is not None:
            old.close()

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
        self._close_services()
        guarded = self._guarded_session(session_provider)
        self.services = AppServices(account_id=self._account_id, data_root=self._data_root,
                                    session_provider=guarded, api_base_url=endpoint[0], ca_bundle=Path(ca_bundle))
        self.services.refresh_catalog()
        self._apply_theme(self.services.settings.load().get("theme", "sakura"))
        self._start_sync()
        self.bridge.push("ready", {"account": self.account_info()})
        return True

    def rebind_feature_context(self, *, session_provider, api_base_url, ca_bundle):
        if self._disposed:
            return False
        self._close_services()
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
            if self._sync is not None:
                self._sync.start()
            elif self.services is not None:
                self._start_sync()
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
        from refactor.learning.course.export_contract import ExportResult
        from refactor.learning.course.export_contract import ExportStatus
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
        self._teacher_drawer.save()
        self._disposed = True
        self.voice_input.dispose()
        self.reminder.dispose()
        self._close_services()
        self.bridge.close()
        self.speech.dispose()
        try:
            self.view.stop()
            self.page.setUrl(QUrl("about:blank"))
        except RuntimeError:
            pass
        self.setEnabled(False)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        diagnostic_event('exit', reason='MAIN_CLOSE_EVENT', window='LEARNING_MAIN',
                         code='USER' if event.spontaneous() else 'PROGRAMMATIC')
        self.dispose()
        super().closeEvent(event)
