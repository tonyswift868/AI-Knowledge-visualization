"""LEGACY / NON_DEFAULT / NOT_USER_VISIBLE OIDC compatibility shell.

The native credential authority supersedes this module for normal login and
account switching. Only explicit legacy callers/tests may instantiate it.

The legacy embedded page is only an OIDC user-agent. Authorization results continue
to arrive through the existing IPv4 loopback receiver; this module never reads
page DOM, cookies, form fields, authorization codes, or tokens.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit, unquote

from PySide6.QtCore import QThread, QUrl, Signal, Qt
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEngineCertificateError,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .enterprise_oidc import (
    DesktopOidcConfig,
    EnterpriseOidcClient,
    EnterpriseOidcError,
    EnterpriseOidcLoginResult,
    PreparedOidcLogin,
)


AUTH_DIALOG_OBJECT_NAME = "mentorflowInAppOidcDialog"
AUTH_VIEW_OBJECT_NAME = "mentorflowInAppOidcView"
RESET_LOGIN_REQUIRED = "ENTERPRISE_RESET_LOGIN_REQUIRED"
RESET_LOGIN_MESSAGE = "请使用新密码，通过正常登录页面登录 MentorFlow。"
_retiring_dialogs: set[object] = set()


def authorization_request_for_intent(prepared, intent, capabilities):
    """Supported Keycloak OIDC entry points; preserve the existing PKCE flow.

    Registration: prompt=create. Reset: the documented OIDC forgot-credentials
    endpoint, never an internal login-actions URL or an UPDATE_PASSWORD action
    which requires the user to know their old password.
    """
    if intent == 'login':
        return prepared
    if intent not in ('register', 'forgot_password') or not isinstance(capabilities, dict) or capabilities.get(intent) is not True:
        raise EnterpriseOidcError('ENTERPRISE_ACCOUNT_ACTION_UNAVAILABLE', '当前账号服务暂未开放此功能。')
    endpoint = urlsplit(prepared.discovery.authorization_endpoint)
    request = urlsplit(prepared.authorization_url)
    if (request.scheme, request.netloc, request.path) != (endpoint.scheme, endpoint.netloc, endpoint.path) or endpoint.scheme != 'https' or not endpoint.path.endswith('/protocol/openid-connect/auth'):
        raise EnterpriseOidcError('ENTERPRISE_ACCOUNT_ACTION_UNAVAILABLE', '当前账号服务暂不支持此操作。')
    parameters = dict(parse_qsl(request.query, keep_blank_values=True))
    if intent == 'register':
        parameters['prompt'] = 'create'
        path = endpoint.path
    else:
        parameters['prompt'] = 'login'
        path = endpoint.path[:-len('/auth')] + '/forgot-credentials'
    return replace(prepared, authorization_url=urlunsplit((request.scheme, request.netloc, path, urlencode(parameters), '')))


def _origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(str(value or "").strip())
    scheme = parsed.scheme.casefold()
    hostname = str(parsed.hostname or "").casefold()
    try:
        port = parsed.port
    except ValueError as exc:
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_BROWSER_POLICY_INVALID",
            "OIDC browser origin is invalid",
        ) from exc
    if not hostname or parsed.username is not None or parsed.password is not None:
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_BROWSER_POLICY_INVALID",
            "OIDC browser origin is invalid",
        )
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else 0
    return scheme, hostname, port


def account_action_navigation_allowed(url: str, *, register: bool = False,
                                      forgot_password: bool = False) -> bool:
    """Guard provider page links without reading action tokens or page contents.

    Keep this in the existing module: the delivery packager reuses a frozen
    dependency closure. This client boundary cannot replace realm policy.
    """
    try:
        parsed = urlsplit(url)
        path = parsed.path
        # Reject ambiguous nested encodings rather than disagreeing with the
        # browser/proxy over which account action the path addresses.
        for _ in range(4):
            decoded = unquote(path, errors='strict')
            if decoded == path:
                break
            path = decoded
        else:
            return False
        segments = {part.split(';', 1)[0].casefold()
                    for part in path.replace('\\', '/').split('/')}
        prompts = [value.split() for key, value in parse_qsl(parsed.query)
                   if key == 'prompt']
    except (ValueError, UnicodeError):
        return False
    registration = bool(segments & {'registration', 'registrations'}) or any(
        'create' in values for values in prompts)
    recovery = bool(segments & {'forgot-credentials', 'reset-credentials'})
    return ((not registration or register is True)
            and (not recovery or forgot_password is True))


@dataclass(frozen=True, slots=True)
class InAppOidcNavigationPolicy:
    """Exact origins allowed inside the authentication-only browser surface."""

    provider_origin: tuple[str, str, int]
    callback_origin: tuple[str, str, int]
    callback_path: str
    register_allowed: bool = False
    forgot_password_allowed: bool = False

    @classmethod
    def from_prepared(cls, prepared: PreparedOidcLogin, capabilities=None) -> "InAppOidcNavigationPolicy":
        provider = _origin(prepared.discovery.authorization_endpoint)
        callback = urlsplit(prepared.transaction.redirect_uri)
        callback_origin = _origin(prepared.transaction.redirect_uri)
        if provider[0] != "https" or callback_origin[:2] != ("http", "127.0.0.1"):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_BROWSER_POLICY_INVALID",
                "OIDC browser policy requires HTTPS identity and IPv4 loopback",
            )
        return cls(
            provider_origin=provider,
            callback_origin=callback_origin,
            callback_path=callback.path,
            register_allowed=isinstance(capabilities, dict) and capabilities.get('register') is True,
            forgot_password_allowed=isinstance(capabilities, dict) and capabilities.get('forgot_password') is True,
        )

    def allows(self, value: str) -> bool:
        raw = str(value or "").strip()
        if raw == "about:blank":
            return True
        if not raw or any(ord(character) < 32 for character in raw):
            return False
        parsed = urlsplit(raw)
        if parsed.username is not None or parsed.password is not None:
            return False
        try:
            candidate_origin = _origin(raw)
        except EnterpriseOidcError:
            return False
        if candidate_origin == self.provider_origin:
            return parsed.scheme.casefold() == "https" and account_action_navigation_allowed(
                raw, register=self.register_allowed,
                forgot_password=self.forgot_password_allowed,
            )
        return (
            candidate_origin == self.callback_origin
            and parsed.scheme.casefold() == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.path == self.callback_path
            and not parsed.fragment
        )


class _AuthRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.policy: InAppOidcNavigationPolicy | None = None

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802
        policy = self.policy
        if policy is None or not policy.allows(info.requestUrl().toString()):
            info.block(True)


class _AuthWebPage(QWebEnginePage):
    def __init__(
        self,
        profile: QWebEngineProfile,
        interceptor: _AuthRequestInterceptor,
        parent: QWidget,
    ) -> None:
        super().__init__(profile, parent)
        self._interceptor = interceptor
        self.featurePermissionRequested.connect(self._deny_feature)

    def acceptNavigationRequest(  # noqa: N802
        self,
        url: QUrl,
        _navigation_type: QWebEnginePage.NavigationType,
        _is_main_frame: bool,
    ) -> bool:
        policy = self._interceptor.policy
        return policy is not None and policy.allows(url.toString())

    def createWindow(  # noqa: N802
        self,
        _window_type: QWebEnginePage.WebWindowType,
    ) -> QWebEnginePage | None:
        return None

    def certificateError(  # noqa: N802
        self,
        error: QWebEngineCertificateError,
    ) -> bool:
        error.rejectCertificate()
        return False

    def _deny_feature(self, origin: QUrl, feature: QWebEnginePage.Feature) -> None:
        self.setFeaturePermission(
            origin,
            feature,
            QWebEnginePage.PermissionPolicy.PermissionDeniedByUser,
        )


class _OidcClient(Protocol):
    async def prepare_login(
        self,
        *,
        force_reauthentication: bool = False,
    ) -> PreparedOidcLogin: ...

    async def complete_login(
        self,
        prepared: PreparedOidcLogin,
        callback_url: str,
    ) -> EnterpriseOidcLoginResult: ...

    async def aclose(self) -> None: ...


class InAppOidcLoginDialog(QDialog):
    """A single-purpose, off-the-record Keycloak authorization dialog."""

    _prepared_signal = Signal(object)
    _succeeded_signal = Signal(object)
    _failed_signal = Signal(str, str)
    authenticated = Signal(object)
    authentication_failed = Signal(str, str)
    authorization_navigation_started = Signal()
    authorization_document_loaded = Signal(bool)
    _worker_finished = Signal()

    def __init__(
        self,
        config: DesktopOidcConfig,
        *,
        parent: QWidget | None = None,
        client_factory: Callable[[DesktopOidcConfig], _OidcClient] = EnterpriseOidcClient,
        embedded: bool = False,
        intent: str = 'login',
        capabilities: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(AUTH_DIALOG_OBJECT_NAME)
        self.setWindowTitle("登录 MentorFlow Enterprise")
        self.setModal(True)
        self.resize(760, 720)
        self._embedded = embedded
        self._dispose_pending = False
        self._browser_releasing = False
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
            self.setModal(False)
        self._config = config
        self._intent, self._capabilities = intent, dict(capabilities or {})
        self._client_factory = client_factory
        self._cancelled = threading.Event()
        self._page_ready = threading.Event()
        self._worker: threading.Thread | None = None
        self._prepared: PreparedOidcLogin | None = None
        self._result: EnterpriseOidcLoginResult | None = None
        self._error: EnterpriseOidcError | None = None

        layout = QVBoxLayout(self)
        heading = QLabel("企业账号安全登录")
        heading.setObjectName("inAppOidcHeading")
        layout.addWidget(heading)
        heading.setVisible(not embedded)
        self.status = QLabel("正在建立加密的企业登录会话…")
        self.status.setObjectName("inAppOidcStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._browser_created = False
        if not embedded and not config.requires_system_browser:
            self._ensure_browser()
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消登录")
        cancel.setObjectName("inAppOidcCancelButton")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        cancel.setVisible(not embedded)
        layout.addLayout(actions)

        self._prepared_signal.connect(self._show_authorization_page)
        self._succeeded_signal.connect(self._finish_successfully)
        self._failed_signal.connect(self._finish_with_error)
        self._worker_finished.connect(self._release_if_disposed)


    def _ensure_browser(self) -> None:
        if self._browser_created:
            return
        self._browser_created = True
        embedded, layout = self._embedded, self.layout()
        # A nameless profile is off-the-record.  No auth cookies, cache,
        # permissions, or browsing history survive this dialog instance.
        # Attach the profile after the view below: QObject destroys children
        # in insertion order, and every page must die before its profile.
        self.profile = QWebEngineProfile()
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        self.profile.setHttpCacheMaximumSize(0)
        self.profile.setPersistentPermissionsPolicy(
            QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime
        )
        self.profile.setSpellCheckEnabled(False)
        self.profile.downloadRequested.connect(lambda request: request.cancel())

        self._interceptor = _AuthRequestInterceptor(self.profile)
        self.profile.setUrlRequestInterceptor(self._interceptor)
        self.view = QWebEngineView(self)
        self.view.setObjectName(AUTH_VIEW_OBJECT_NAME)
        self.page = _AuthWebPage(self.profile, self._interceptor, self.view)
        self.view.setPage(self.page)
        self.profile.setParent(self)
        self.page.setBackgroundColor(QColor("#fff9fc"))
        if embedded:
            self.view.hide()
            self.view.loadFinished.connect(self._page_loaded)
            layout.setContentsMargins(0, 0, 0, 0)
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
            False,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,
            False,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalStorageEnabled,
            False,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            False,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
            False,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.AllowRunningInsecureContent,
            False,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.ScreenCaptureEnabled,
            False,
        )
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.NavigateOnDropEnabled, False)
        layout.addWidget(self.view, 1)


    def _page_loaded(self, ok: bool) -> None:
        if not self._browser_created:
            return
        if self._cancelled.is_set():
            return
        self.authorization_document_loaded.emit(ok)
        if not ok:
            self._finish_with_error("ENTERPRISE_LOGIN_PAGE_UNAVAILABLE", "登录页面暂时无法加载，请重试。")
            return
        self.status.hide()
        self.view.show()
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def start_embedded(self) -> None:
        if not self._embedded:
            raise RuntimeError("Embedded login requires a child widget")
        self.show()
        self._start()

    def dispose(self) -> None:
        self._cancelled.set()
        self._page_ready.set()
        if self._prepared is not None:
            self._prepared.receiver.close()
        self._reset_browser()
        self.hide()
        # A pending protocol finish can outlive the gate/layout. Detach before
        # DeferredDelete (the close-during-discovery native regression), and
        # retain Python ownership until Qt has actually destroyed the object.
        if self not in _retiring_dialogs:
            _retiring_dialogs.add(self)
            self.destroyed.connect(lambda: _retiring_dialogs.discard(self))
        self.setParent(None)
        self._dispose_pending = True
        if self._worker is None or not self._worker.is_alive():
            self._release_if_disposed()

    def _release_if_disposed(self) -> None:
        if self._dispose_pending and not self._browser_releasing:
            self._browser_releasing = True
            if not self._browser_created:
                self.deleteLater()
                return
            # Let the event loop finish page destruction before the browser
            # context, then release the shell after both are gone.
            self.page.destroyed.connect(self.profile.deleteLater)
            self.profile.destroyed.connect(self.deleteLater)
            self.page.deleteLater()

    @property
    def is_off_the_record(self) -> bool:
        return self.profile.isOffTheRecord()

    def _start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(
            target=self._run_protocol,
            name="mentorflow-in-app-oidc",
            daemon=True,
        )
        self._worker.start()

    def _run_protocol(self) -> None:
        async def run() -> EnterpriseOidcLoginResult:
            client = self._client_factory(self._config)
            prepared: PreparedOidcLogin | None = None
            reset_client = None
            try:
                prepared = await client.prepare_login(force_reauthentication=True)
                prepared = authorization_request_for_intent(prepared, self._intent, self._capabilities)
                if self._intent == 'forgot_password':
                    from .reset_completion import ResetCompletionClient
                    reset_client = ResetCompletionClient(self._config)
                    reset_transaction = await reset_client.begin(prepared.transaction.state)
                self._prepared_signal.emit(prepared)
                page_ready = await asyncio.to_thread(self._page_ready.wait, 10.0)
                if not page_ready or self._cancelled.is_set():
                    raise EnterpriseOidcError(
                        "ENTERPRISE_OIDC_LOGIN_CANCELLED",
                        "Enterprise login was cancelled",
                    )
                if reset_client is not None:
                    from .reset_completion import COMPLETED_CODE, COMPLETED_MESSAGE
                    await reset_client.wait_completed(reset_transaction, self._cancelled,
                        timeout=self._config.callback_timeout_seconds)
                    # This terminal outcome returns to login; never exchange an OIDC
                    # callback, create a MentorFlow session or emit authenticated.
                    raise EnterpriseOidcError(COMPLETED_CODE, COMPLETED_MESSAGE)
                callback = await asyncio.to_thread(
                    prepared.receiver.wait,
                    self._config.callback_timeout_seconds,
                )
                if self._cancelled.is_set():
                    raise EnterpriseOidcError(
                        "ENTERPRISE_OIDC_LOGIN_CANCELLED",
                        "Enterprise login was cancelled",
                    )
                return await client.complete_login(prepared, callback)
            finally:
                if prepared is not None:
                    prepared.receiver.close()
                if reset_client is not None:
                    await reset_client.aclose()
                await client.aclose()

        try:
            self._succeeded_signal.emit(asyncio.run(run()))
        except EnterpriseOidcError as exc:
            self._failed_signal.emit(exc.code, exc.message)
        except Exception:
            self._failed_signal.emit(
                "ENTERPRISE_OIDC_IN_APP_FAILED",
                "Enterprise login could not be completed safely",
            )
        finally:
            self._worker_finished.emit()

    def _show_authorization_page(self, value: object) -> None:
        if self._cancelled.is_set() or not isinstance(value, PreparedOidcLogin):
            return
        try:
            policy = InAppOidcNavigationPolicy.from_prepared(value, self._capabilities)
        except EnterpriseOidcError as exc:
            value.receiver.close()
            self._finish_with_error(exc.code, exc.message)
            return
        self._prepared = value
        if self._config.requires_system_browser:
            self.status.setText("请在系统浏览器中完成登录，完成后返回 MentorFlow。")
            try:
                opened = QDesktopServices.openUrl(QUrl(value.authorization_url))
            except Exception:
                opened = False
            if not opened:
                self._finish_with_error(
                    'ENTERPRISE_OIDC_BROWSER_UNAVAILABLE',
                    '无法打开系统浏览器，请检查默认浏览器设置后重试。',
                )
            else:
                self.authorization_navigation_started.emit()
            self._page_ready.set()
            return
        self._ensure_browser()
        self._interceptor.policy = policy
        self.status.setText(
            "正在加载登录页面…"
        )
        self.authorization_navigation_started.emit()
        self.view.setUrl(QUrl(value.authorization_url))
        self._page_ready.set()

    def _finish_successfully(self, value: object) -> None:
        if self._cancelled.is_set() or not isinstance(value, EnterpriseOidcLoginResult):
            return
        if self._intent == 'forgot_password':
            self._finish_with_error('RESET_UNVERIFIED_RETURN', '请返回登录页面，使用新密码登录。')
            return
        self._result = value
        if self._embedded:
            self._reset_browser()
            self.authenticated.emit(value)
        else:
            self.accept()

    def _finish_with_error(self, code: str, message: str) -> None:
        if self._cancelled.is_set():
            return
        self._error = EnterpriseOidcError(code, message)
        if self._embedded:
            self._cancelled.set()
            if self._prepared is not None:
                self._prepared.receiver.close()
            self._reset_browser()
            self.authentication_failed.emit(code, message)
        else:
            self.reject()

    def _reset_browser(self) -> None:
        if not self._browser_created:
            return
        # The profile is single-use and never attached to another dialog.  It
        # remains parent-owned until the Account page/app is destroyed, while
        # all session state is cleared as soon as this login attempt finishes.
        self.view.stop()
        self.profile.cookieStore().deleteAllCookies()
        # NoCache + off-the-record already prevent a reusable cache. Starting
        # an asynchronous cache clear while tearing down its profile can leave
        # Chromium work targeting a destroyed browser context.
        self._interceptor.policy = None

    def reject(self) -> None:
        self._cancelled.set()
        prepared = self._prepared
        if prepared is not None:
            prepared.receiver.close()
        super().reject()

    def execute_login(self) -> EnterpriseOidcLoginResult:
        application = QApplication.instance()
        if application is None or QThread.currentThread() is not application.thread():
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_UI_THREAD_REQUIRED",
                "In-app Enterprise login requires the MentorFlow UI thread",
            )
        self._start()
        try:
            self.exec()
        finally:
            self._reset_browser()
            worker = self._worker
            if worker is not None:
                worker.join(timeout=2.0)
        if self._result is not None:
            return self._result
        if self._error is not None:
            raise self._error
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_LOGIN_CANCELLED",
            "Enterprise login was cancelled",
        )


def login_enterprise_account_in_app_from_env(
    *,
    parent: QWidget | None = None,
) -> EnterpriseOidcLoginResult:
    dialog = InAppOidcLoginDialog(
        DesktopOidcConfig.from_env(),
        parent=parent,
    )
    try:
        return dialog.execute_login()
    finally:
        dialog.deleteLater()


__all__ = [
    "AUTH_DIALOG_OBJECT_NAME",
    "AUTH_VIEW_OBJECT_NAME",
    "InAppOidcLoginDialog",
    "InAppOidcNavigationPolicy",
    "login_enterprise_account_in_app_from_env",
]
