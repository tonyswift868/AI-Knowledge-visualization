"""Standalone desktop entry for the minimal authenticated account shell."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

# NumPy's OpenBLAS otherwise starts one thread per core; 87-MF04 measured 22 fewer threads, ~0.7 GB less.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
from refactor.runtime_logging import event as diagnostic_event


class AccountShell:
    """Compose the existing account flow without starting learning services."""

    def __init__(
        self,
        app,
        shell_runtime,
        *,
        login_session=None,
        native_login: Callable | None = None,
        native_restorer: Callable | None = None,
        capability_fetcher: Callable | None = None,
        lifecycle_pane_factory: Callable | None = None,
        security_client_factory: Callable | None = None,
        closure_client_factory: Callable | None = None,
        closure_cleanup_factory: Callable | None = None,
        gate_window=None,
        prepare_account_surface=False,
    ) -> None:
        from PySide6.QtCore import QSettings
        from refactor.desktop.account_identity import AccountPreferenceStore
        from refactor.desktop.login_session import LoginSession
        from refactor.desktop.login_shell import LoginGateWindow

        self.app = app
        self.runtime = shell_runtime
        self._native_login = native_login
        self._native_restorer = native_restorer
        self._capability_fetcher = capability_fetcher
        self._lifecycle_pane_factory = lifecycle_pane_factory
        self._security_client_factory = security_client_factory
        self._closure_client_factory = closure_client_factory
        self._closure_cleanup_factory = closure_cleanup_factory
        self._closure = None
        self._closure_recovery = None
        self._account_settings = None
        self._cleanup_profile = None
        self._cleanup_button = None
        self.session = login_session or LoginSession(
            AccountPreferenceStore(self._settings(QSettings))
        )
        self.gate = gate_window if gate_window is not None else LoginGateWindow()
        self._loading_action = 'login'
        self.home = None
        self._pane = None
        self._warm_pane = None
        self._account_form_state = None
        self._remembered_profile = None
        self._login_result = None
        self._session_credentials = None
        self._update_login_token = None
        self._update_restore_pending = False
        self._update_auto_pending = False
        self._update_interactive_pending = False
        self._update_handoff_account = None
        self._generation = 0
        self._cancelled = threading.Event()
        self._closing = False
        self._update_exit_pending = False
        self._session_epoch = 0
        self._feature_session_confirmed = False
        # Keep the last server-confirmed product role separate from the
        # display name and from the latest refresh payload.  A transient
        # /auth/me response may omit role while the rotated session is still
        # usable; it must never downgrade a known administrator to the
        # ambiguous "待确认" label.
        self._confirmed_server_role = None
        self._confirmed_role_account_id = None
        # A transient refresh failure must not erase the last server-confirmed
        # identity. Keep this separate from the feature-session guard so a
        # short outage can be retried without making the UI look logged out.
        self._session_refresh_degraded = False
        self._session_refresh_busy = False
        self._logout_pending = False
        self._switch_pending = False
        self._capabilities_loading = False
        self._capabilities_loaded = False
        self._capabilities_result_succeeded = False

        from PySide6.QtCore import QTimer

        self._session_timer = QTimer(app)
        self._session_timer.setInterval(240_000)
        self._session_timer.timeout.connect(self._refresh_active_session)
        self._capabilities_retry_timer = QTimer(app)
        self._capabilities_retry_timer.setSingleShot(True)
        self._capabilities_retry_timer.setInterval(60_000)
        self._capabilities_retry_timer.timeout.connect(self._load_capabilities)
        self._prewarm_timer = QTimer(app)
        self._prewarm_timer.setSingleShot(True)
        self._prewarm_timer.setInterval(250)
        self._prewarm_timer.timeout.connect(self._prewarm_account_page)
        self.gate.first_paint_completed.connect(self._schedule_account_prewarm)

        self.gate.login_requested.connect(self._begin_interactive_login)
        self.gate.retry_requested.connect(self._begin_interactive_login)
        self.gate.loading_retry_requested.connect(self._retry_native_loading)
        self.gate.cancel_requested.connect(self._cancel_login)
        self.gate.closed.connect(self._gate_closed)
        # The frameless close button uses QWidget.close(), whose Qt event is
        # not spontaneous. Mark this explicit user action before closing.
        self.gate.close_button.clicked.disconnect(self.gate.close)
        self.gate.close_button.clicked.connect(self._close_login_window)
        self.gate.background_succeeded.connect(self._background_succeeded)
        self.gate.background_failed.connect(self._background_failed)
        self.gate.capabilities_received.connect(self._capabilities_received)
        self.gate.register_requested.connect(lambda: self._begin_account_action("register"))
        self.gate.forgot_password_requested.connect(lambda: self._begin_account_action("forgot_password"))
        self.gate.account_selected.connect(self._select_local_account)
        self.gate.account_remove_requested.connect(self._remove_local_account)
        self.gate.remember_password_box.clicked.connect(self._remember_password_clicked)
        self.gate.username_field.textChanged.connect(self._refresh_remembered_password_hint)
        self.gate.remember_password_box.toggled.connect(self._refresh_remembered_password_hint)
        self.app.aboutToQuit.connect(self.close)
        self.app.setQuitOnLastWindowClosed(False)
        self._set_primary(self.gate, 'LOGIN_MAIN')
        if prepare_account_surface:
            self._prepare_account_surface()

    def _set_primary(self, window, role):
        diagnostics = getattr(self.app, '_mentorflow_diagnostics', None)
        if diagnostics is not None:
            diagnostics.set_primary(window, role)

    def _retry_native_loading(self):
        if self._loading_action == 'restore':
            self._begin_remembered_login()
        elif self._loading_action == 'login':
            # take_credentials already cleared the password. Never retain it
            # for an animation; reuse the existing cancel/form retry path.
            self._cancel_login()
            self.gate.notice_label.setText('请重新输入账号和密码后登录。')
        elif self._loading_action == 'account_service' and self._pane is not None:
            view = getattr(self._pane, 'view', None)
            if view is not None:
                view.reload()

    def _loading_appearance(self, account_id):
        from .native_loading import account_appearance
        theme, reduced = account_appearance(self.runtime.data_root, account_id)
        self.gate.set_loading_appearance(theme, reduced)
        return theme, reduced

    def _schedule_account_prewarm(self):
        timer = getattr(self, '_prewarm_timer', None)
        if timer is not None and self.gate._first_paint_scheduled and not self._closing:
            timer.start()

    def _prepare_account_surface(self):
        """Create the dormant account page in the OpenGL-ready login gate."""
        if self._closing or self._warm_pane is not None or self._lifecycle_pane_factory is not None:
            return
        try:
            from .lifecycle_pane import AccountLifecyclePane
            self._warm_pane = AccountLifecyclePane(
                'register', parent=self.gate, shell_runtime=self.runtime, load=False)
            self._warm_pane.hide()
        except Exception:
            diagnostic_event('login', state='ACCOUNT_PAGE_SURFACE_PREPARE_FAILED', exc_info=True)

    def _prewarm_account_page(self):
        from .login_shell import LoginGateState
        if (self._closing or self._login_result is not None or self._pane is not None
                or (self._warm_pane is not None and getattr(self._warm_pane, '_document_started', True))
                or self._lifecycle_pane_factory is not None
                or not self.gate.isVisible() or self.gate.state is LoginGateState.AUTHENTICATING
                or not any(self.gate.account_capabilities.values())):
            return
        try:
            # QWidget/Profile/Page belong to the GUI thread. Loading Chromium
            # and the local document then proceeds asynchronously, hidden.
            from .lifecycle_pane import AccountLifecyclePane
            pane = self._warm_pane
            if pane is None:
                pane = AccountLifecyclePane('register', parent=self.gate, shell_runtime=self.runtime)
            self._warm_pane = pane
            start = getattr(pane, 'prewarm', None)
            if callable(start):
                start()
            pane.hide()
            pane.prepared.connect(lambda: diagnostic_event('login', state='ACCOUNT_PAGE_PREWARM_READY'))
            diagnostic_event('login', state='ACCOUNT_PAGE_PREWARM_STARTED')
        except Exception:
            diagnostic_event('login', state='ACCOUNT_PAGE_PREWARM_FAILED', exc_info=True)

    def _release_account_prewarm(self):
        timer = getattr(self, '_prewarm_timer', None)
        if timer is not None: timer.stop()
        pane = getattr(self, '_warm_pane', None)
        self._warm_pane = None
        if pane is not None: pane.dispose()

    def _show_gate_before_retiring_home(self):
        AccountShell._set_primary(self, self.gate, 'LOGIN_MAIN')
        loading = getattr(self.gate, 'loading', None)
        if loading is not None:
            loading.begin('正在返回登录入口…')
        self.gate.show()
        AccountShell._schedule_account_prewarm(self)

    def _close_login_window(self):
        if self.home is None or not self.home.isVisible():
            diagnostics = getattr(self.app, '_mentorflow_diagnostics', None)
            if diagnostics is not None:
                diagnostics.request_exit('USER_MAIN_CLOSE')
            else:
                diagnostic_event('exit', reason='USER_MAIN_CLOSE', window='LOGIN_MAIN')
                self.app.quit()
        self.gate.close()

    def _gate_closed(self):
        # A hidden login surface is still used to deliver refresh callbacks.
        # Closing it must never dispose the signed-in home/session.
        diagnostic_event('window', state='CLOSED', window='LOGIN_MAIN')

    def _settings(self, settings_type):
        from PySide6.QtCore import QSettings

        path = self.runtime.data_root / "config" / "account.ini"
        settings = settings_type(str(path), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        return settings

    @staticmethod
    def _confirmed_role_from_result(result):
        """Return only the server-authoritative product role from a login result.

        ``AccountLoginResult.server_state`` is typed as a Mapping.  The live
        native client currently returns a plain dict, while restore/adapters
        used by older deployments may provide another Mapping implementation.
        Treating only ``dict`` as valid silently changed a known administrator
        into ``待确认`` during an otherwise successful session refresh.
        """
        state = getattr(result, "server_state", None)
        if not isinstance(state, Mapping):
            return None
        # The canonical contract is the top-level ``role`` field.  A few
        # older account-service deployments returned the same server-owned
        # value under ``application_role`` or inside the account projection.
        # Accept those explicit server fields for display continuity, while
        # never deriving a role from a local username, cached badge, or tenant
        # membership label.
        account = state.get("account")
        candidates = [state.get("role"), state.get("application_role")]
        if isinstance(account, Mapping):
            candidates.extend((account.get("role"), account.get("application_role")))
        return next((role for role in candidates if role in {"SUPER_ADMIN", "LEARNER"}), None)

    def show(self) -> None:
        self._refresh_local_accounts()
        if not self.gate.isVisible():
            self.gate.showNormal()
        self.gate.raise_()
        self.gate.activateWindow()
        self._load_capabilities()
        try:
            profile, automatic = self.session.remembered()
        except (ValueError, RuntimeError):
            profile, automatic = None, False
            self.gate.notice_label.setText("本机账号状态无法安全读取，请重新选择账号。")
        self._remembered_profile = profile
        # A handoff takes precedence even when automatic login is disabled.
        # Invalid/expired handoffs lead to the normal gate, not saved auto-login.
        take_handoff = getattr(self.session, 'take_update_login', None)
        if take_handoff is not None:
            present, handoff_profile, token = take_handoff()
            if present:
                automatic = bool(token)
                if token:
                    profile = self._remembered_profile = handoff_profile
                    self._update_login_token = token
                    self._update_restore_pending = True
        if profile is not None:
            self._loading_appearance(profile.account_id)
            row = self.session.registry.get(profile.account_id)
            self.gate.username_field.setText(row.get("username", "") if row else "")
            self.gate.remember_password_box.setChecked(bool(row and row["remember_password"]))
            self.gate.remember_box.setChecked(bool(row and row["remember_session"]))
            self.gate.auto_box.setChecked(bool(row and row['auto_login']) if self._update_login_token else automatic)
            from refactor.desktop.login_shell import LoginGateState

            self.gate.set_state(LoginGateState.LOGIN_READY)
            self.gate.state_title.setText(profile.safe_account_label)
            self.gate.state_detail.setText("已记住此账号，点击登录可安全继续。")
            self.gate.primary_button.setText("登录")
            if automatic:
                from PySide6.QtCore import QTimer

                QTimer.singleShot(0, self._begin_remembered_login)
        else:
            from refactor.desktop.login_shell import LoginGateState

            self.gate.set_state(LoginGateState.LOGIN_READY)
            self.gate.username_field.setFocus()
        self._refresh_remembered_password_hint()
        self._refresh_closure_recovery()
        if self._capabilities_loading and self.gate.state is LoginGateState.LOGIN_READY:
            self._loading_action = 'capabilities'
            self.gate.loading.begin('正在检查账号服务可用功能，不影响登录…')
        self._schedule_account_prewarm()

    def _load_capabilities(self) -> None:
        if (
            self._closing
            or self._capabilities_loaded
            or self._capabilities_loading
            or self._login_result is not None
            or not self.gate.isVisible()
        ):
            return
        gate = self.gate
        self._capabilities_loading = True
        from .login_shell import LoginGateState
        if self.gate.state is LoginGateState.LOGIN_READY:
            self._loading_action = 'capabilities'
            self.gate.loading.begin('正在检查账号服务可用功能，不影响登录…')
        fetch = self._capability_fetcher or self._fetch_capabilities

        def work():
            succeeded = False
            try:
                capabilities = fetch()
                succeeded = isinstance(capabilities, dict)
                if not succeeded:
                    capabilities = {}
            except Exception:
                capabilities = {}
            if not self._closing and self.gate is gate:
                self._capabilities_result_succeeded = succeeded
                gate.capabilities_received.emit(capabilities)

        threading.Thread(target=work, name="mentorflow-shell-capabilities", daemon=True).start()

    def _capabilities_received(self, capabilities) -> None:
        self._capabilities_loading = False
        self._schedule_account_prewarm()
        if self._loading_action == 'capabilities':
            self.gate.loading.finish()
        if self._closing:
            return
        succeeded = self._capabilities_result_succeeded
        if succeeded:
            self._capabilities_loaded = True
            self._capabilities_retry_timer.stop()
        elif self.gate.isVisible() and self._login_result is None:
            self._capabilities_retry_timer.start()

    @staticmethod
    def _fetch_capabilities():
        from refactor.desktop.account_protocol import AccountConfig
        from refactor.desktop.lifecycle_client import LifecycleClient

        async def fetch():
            client = LifecycleClient(AccountConfig.from_env())
            try:
                return await client.action("capabilities")
            finally:
                await client.aclose()

        return asyncio.run(fetch())

    def _refresh_local_accounts(self) -> None:
        try:
            snapshot = self.session.registry.snapshot()
        except (ValueError, RuntimeError):
            self.gate.notice_label.setText("本机账号列表无法安全读取；不会覆盖已有账号。")
            self.gate.account_selector.clear()
            self.gate.remove_account_button.setEnabled(False)
            return
        selector = self.gate.account_selector
        selector.blockSignals(True)
        typed = self.gate.username_field.text()
        selector.clear()
        labels = [(row.get("username") or "").strip() or friendly_name(row) for row in snapshot["accounts"]]
        for index, row in enumerate(snapshot["accounts"]):
            label = labels[index]
            if labels.count(label) > 1:   # same name on two local accounts: number them, never show ids
                label += f"（{labels[:index + 1].count(label)}）"
            selector.addItem(label, row["account_id"])
        selector.setCurrentIndex(selector.findData(snapshot["current_account_id"]))
        if selector.currentIndex() < 0:
            self.gate.username_field.setText(typed)   # refreshing the list never wipes what was typed
        selector.blockSignals(False)
        self.gate.remove_account_button.setEnabled(bool(snapshot["accounts"]))

    def _refresh_remembered_password_hint(self, *_args) -> None:
        profile = self._remembered_profile
        if profile is None:
            self.gate.password_field.setPlaceholderText("密码")
            return
        row = self.session.registry.get(profile.account_id)
        available = bool(
            row
            and row["remember_password"]
            and not self.session.requires_reauthentication(profile)
            and self.gate.remember_password_box.isChecked()
            and (
                not self.gate.username_field.text().strip()
                or self.gate.username_field.text().strip() == row.get("username")
            )
        )
        self.gate.password_field.setPlaceholderText("已保存密码，直接点击登录" if available else "密码")

    def _select_local_account(self, account_id: str) -> None:
        if not account_id:
            return
        self._invalidate_login_jobs()
        try:
            row = self.session.select(account_id)
        except Exception:
            self.gate.notice_label.setText("无法选择此本机账号，请重试。")
            return
        self._remembered_profile = self.session.registry.profile(row)
        self._loading_appearance(self._remembered_profile.account_id)
        self.gate.clear_password()
        self.gate.username_field.setText(row.get("username", ""))
        self.gate.remember_box.setChecked(row["remember_session"])
        self.gate.auto_box.setChecked(row["auto_login"])
        self.gate.remember_password_box.setChecked(row["remember_password"])
        from refactor.desktop.login_shell import LoginGateState

        self.gate.set_state(LoginGateState.LOGIN_READY)
        self.gate.state_title.setText(friendly_name(row))
        self.gate.state_detail.setText(
            "输入密码后登录；已记住密码时可直接点击登录。"
            if row.get("username")
            else "此历史账号未保存登录名，请填写账号与密码。"
        )
        self._refresh_remembered_password_hint()

    def _remember_password_clicked(self, checked: bool) -> None:
        if checked or self._remembered_profile is None:
            return
        try:
            self.session.forget_password(self._remembered_profile)
            self.gate.auto_box.setChecked(False)
            self.gate.notice_label.setText("已删除此账号保存的密码，请输入密码登录。")
        except Exception:
            self.gate.remember_password_box.setChecked(True)
            self.gate.notice_label.setText("保存的密码未能删除，请稍后重试。")

    def _remove_local_account(self, account_id: str) -> None:
        if not account_id:
            return
        self._invalidate_login_jobs()
        try:
            self.session.remove_local(account_id)
        except Exception:
            self.gate.notice_label.setText("本机凭据未能清除，账号未移除。请稍后重试。")
            return
        self._cancel_login()
        self._refresh_local_accounts()
        self.gate.notice_label.setText("已移除本机账号；服务器账号和数据保留。")

    def _new_request(self):
        self._invalidate_login_jobs()
        return self._generation, self._cancelled

    def _invalidate_login_jobs(self) -> None:
        self._generation += 1
        self._cancelled.set()
        self._cancelled = threading.Event()
        self._dispose_pane()

    def _dispose_pane(self) -> None:
        if self._pane is not None:
            pane, self._pane = self._pane, None
            self.gate.set_auth_content(None)
            dispose = getattr(pane, "dispose", None)
            if dispose is not None:
                dispose()
            else:
                pane.deleteLater()

    def _cancel_login(self) -> None:
        if self._pane is not None and getattr(self, '_account_form_state', None) is not None:
            self._return_from_account_action(self._generation)
            return
        update_restore = getattr(self, '_update_restore_pending', False)
        self._update_restore_pending = False
        self._update_login_token = None
        if update_restore:
            self._update_auto_pending = False
        self._update_interactive_pending = False
        self._update_auto_pending = False
        self._invalidate_login_jobs()
        self._remembered_profile = None
        self._login_result = None
        self.gate.remember_box.setChecked(False)
        self.gate.auto_box.setChecked(False)
        self.gate.remember_password_box.setChecked(False)
        self.gate.account_selector.setCurrentIndex(-1)
        self.gate.username_field.clear()   # (an editable box also clears its text with the selection)
        self.gate.clear_password()
        self._refresh_remembered_password_hint()
        try:
            if not update_restore:
                self.session.suspend_automatic()
        except Exception:
            self.gate.notice_label.setText("本机自动登录设置暂未保存；请重试或手动选择账号。")
        from refactor.desktop.login_shell import LoginGateState

        self.gate.set_state(LoginGateState.LOGIN_READY)

        AccountShell._schedule_account_prewarm(self)

    def _updates_allow_login(self) -> bool:
        adapter = getattr(getattr(self, 'app', None), '_mentorflow_updates', None)
        if adapter is None:
            return True
        state = adapter.state
        return not state.get('login_check_pending') and bool(
            state.get('login_allowed') and not state.get('mandatory'))

    def _resume_update_login(self, value) -> None:
        if value.get('login_check_pending'):
            return
        allowed = AccountShell._updates_allow_login(self)
        if not allowed:
            if getattr(self, '_update_interactive_pending', False):
                self._update_interactive_pending = False
                from refactor.desktop.login_shell import LoginGateState
                self.gate.set_state(LoginGateState.LOGIN_READY,
                                    detail='当前版本需要先更新，更新完成后才能登录。')
            return
        waiting_for_optional_choice = (
            value.get('state') == 'OPTIONAL_UPDATE' and not value.get('deferred'))
        if getattr(self, '_update_auto_pending', False) and not waiting_for_optional_choice:
            self._update_auto_pending = False
            self._begin_remembered_login()
        elif (getattr(self, '_update_interactive_pending', False)
              and (value.get('state') != 'OPTIONAL_UPDATE' or value.get('deferred'))):
            self._update_interactive_pending = False
            from refactor.desktop.login_shell import LoginGateState
            self.gate.set_state(LoginGateState.LOGIN_READY)
            self._begin_interactive_login()

    def _begin_remembered_login(self) -> None:
        from refactor.desktop.login_shell import LoginGateState

        if not AccountShell._updates_allow_login(self):
            self._update_auto_pending = True
            adapter = getattr(self.app, '_mentorflow_updates', None)
            if adapter is not None and adapter.state.get('login_check_pending'):
                self.gate.set_state(LoginGateState.AUTHENTICATING, detail='正在检查更新…')
            return
        profile = self._remembered_profile
        if self._closing or profile is None:
            return
        generation, cancelled = self._new_request()
        update_token = getattr(self, '_update_login_token', None)
        self._update_login_token = None
        self._loading_action = 'restore'
        self._release_account_prewarm()
        self.gate.set_state(LoginGateState.AUTHENTICATING, detail="正在恢复上次的登录…")
        self.gate.loading.begin('正在恢复上次的登录…', retry=True, cancel=True)

        def work():
            try:
                if update_token:
                    result = self.session.restore_update_login(profile, update_token, cancelled,
                        restorer=self._native_restorer)
                    self.gate.background_succeeded.emit(f"{generation}:update_restore", result)
                    return
                result = self.session.restore(profile, cancelled)
                self.gate.background_succeeded.emit(f"{generation}:restore", result)
            except Exception as exc:
                if update_token:
                    self.gate.background_failed.emit(f"{generation}:update_restore", "更新后的登录未能恢复，请重新登录。")
                    return
                code = getattr(exc, "code", "")
                expired = code in {
                    "ENTERPRISE_SESSION_REAUTH_REQUIRED",
                    "NATIVE_SESSION_SUPERSEDED",
                    "SAVED_PASSWORD_REJECTED",
                }
                message = (
                    str(exc)
                    if code in {"SAVED_PASSWORD_EXPIRED", "SAVED_PASSWORD_REJECTED"}
                    else "登录已失效，请重新输入账号和密码。"
                    if expired
                    else "暂时无法恢复登录，请检查网络或账号权限后重试。"
                )
                job = "restore_expired" if expired else "restore_retry"
                diagnostic_event('login', state=job, code=code, exc_info=True)
                self.gate.background_failed.emit(f"{generation}:{job}", message)

        threading.Thread(target=work, name="mentorflow-shell-restore", daemon=True).start()

    def _begin_interactive_login(self) -> None:
        from refactor.desktop.login_shell import LoginGateState
        from refactor.desktop.native_auth import MESSAGES

        if self._closing:
            return
        if not AccountShell._updates_allow_login(self):
            adapter = getattr(self.app, '_mentorflow_updates', None)
            if adapter is not None and adapter.state.get('login_check_pending'):
                self._update_interactive_pending = True
                self.gate.set_state(LoginGateState.AUTHENTICATING, detail='正在检查更新…')
            return
        selected = (
            self.session.registry.get(self._remembered_profile.account_id)
            if self._remembered_profile
            else None
        )
        if (
            selected is not None
            and not self.gate.password_field.text()
            and (
                not self.gate.username_field.text().strip()
                or self.gate.username_field.text().strip() == selected.get("username")
            )
            and (selected["remember_password"] or selected["remember_session"])
        ):
            self._begin_remembered_login()
            return

        if not self.gate.username_field.text().strip() or not self.gate.password_field.text():
            missing_account = not self.gate.username_field.text().strip()
            self.gate.set_state(LoginGateState.LOGIN_READY,
                                detail="请输入账号" if missing_account else "请输入密码")
            (self.gate.username_field if missing_account else self.gate.password_field).setFocus()
            return
        username, password = self.gate.take_credentials()
        self._release_account_prewarm()
        self._remembered_profile = None
        generation, _cancelled = self._new_request()
        save_password = self.gate.remember_password_box.isChecked()
        remember = self.gate.remember_box.isChecked()
        automatic = self.gate.auto_box.isChecked()
        self._loading_action = 'login'
        self.gate.set_state(LoginGateState.AUTHENTICATING)

        def work():
            nonlocal password
            try:
                authenticate = self._native_login
                if authenticate is None:
                    from refactor.desktop.native_auth import login_native_account_from_env

                    authenticate = login_native_account_from_env
                result = authenticate(username, password)
                saved_password = password if save_password else None
                payload = (result, username, saved_password, remember, automatic)
                self.gate.background_succeeded.emit(f"{generation}:authenticate", payload)
                payload = None
            except Exception as exc:
                diagnostic_event('login', state='FAILED', code=getattr(exc, 'code', 'INTERNAL'), exc_info=True)
                message = MESSAGES.get(
                    getattr(exc, "code", ""),
                    "登录暂时无法完成，请检查网络或账号服务后重试。",
                )
                self.gate.background_failed.emit(f"{generation}:authenticate", message)
            finally:
                password = None

        threading.Thread(target=work, name="mentorflow-shell-login", daemon=True).start()

    def _accept_login(self, result, *, username=None, password=None, remember=None, automatic=None, update_handoff=False):
        from refactor.desktop.account_protocol import AccountLoginResult
        from refactor.desktop.login_shell import LoginGateState

        if not AccountShell._updates_allow_login(self):
            self._discard_login_result(result)
            return
        if not isinstance(result, AccountLoginResult):
            self.gate.set_state(LoginGateState.AUTH_FAILED, detail="登录结果无法验证，请重新登录。")
            AccountShell._schedule_account_prewarm(self)
            return
        try:
            if remember is None:
                remember = self.gate.remember_box.isChecked()
            if automatic is None:
                automatic = self.gate.auto_box.isChecked()
            if not update_handoff:
                self.session.accept(
                    result,
                    remember=remember,
                    automatic=automatic,
                    remember_password=self.gate.remember_password_box.isChecked(),
                    username=username,
                    password=password,
                )
        except Exception:
            self._discard_login_result(result)
            self.gate.set_state(
                LoginGateState.AUTH_FAILED,
                detail="登录凭据无法安全保存，请检查 Windows 凭据服务后重试。",
            )
            return
        finally:
            password = None

        self._login_result = result
        account_id = result.identity.profile().account_id
        if self._confirmed_role_account_id != account_id:
            self._confirmed_server_role = None
            self._confirmed_role_account_id = account_id
        role = self._confirmed_role_from_result(result)
        if role in {"SUPER_ADMIN", "LEARNER"}:
            self._confirmed_server_role = role
        if self._cleanup_profile is not None and self._cleanup_profile.account_id == result.identity.account_id:
            self._cleanup_profile = None
            if self._cleanup_button is not None:
                self._cleanup_button.hide()
        self._remembered_profile = result.identity.profile()
        self._capabilities_retry_timer.stop()
        row = self.session.registry.get(result.identity.profile().account_id)
        self._session_credentials = (
            self.session.credentials
            if row is not None and row["remember_session"]
            else self._EphemeralCredentials(result)
        )
        self._session_refresh_degraded = False
        self._refresh_local_accounts()
        appearance = self._loading_appearance(account_id)
        self._release_account_prewarm()
        self.gate.loading.begin('正在准备学习空间…', retry=False, cancel=False, restart=False)
        self.gate.repaint()
        from refactor.learning.app.window import LearningWindow

        api_base_url = os.environ.get("MENTORFLOW_ENTERPRISE_API_BASE_URL", "")
        api_authority = urlsplit(api_base_url)
        api_label = api_authority.hostname or api_base_url or "账号服务"
        home = LearningWindow(
            friendly_name({"display_name": result.identity.display_name, "username": (row or {}).get("username", "")}),
            self._confirmed_server_role,
            api_label,
            account_id=result.identity.profile().account_id,
            data_root=self.runtime.data_root,
        )
        from .native_loading import WebFirstFrame
        from PySide6.QtWidgets import QWidget
        if isinstance(home, QWidget) and hasattr(home, 'page') and hasattr(home, 'view'):
            home._native_first_frame = WebFirstFrame(home, theme=appearance[0], reduced=appearance[1])
        home.switch_requested.connect(self._switch_account)
        home.logout_requested.connect(self._logout_account)
        home.verify_requested.connect(self._refresh_active_session)
        home.bind_account_settings(self._open_account_settings)
        old_home = self.home
        self.home = home
        self._session_epoch += 1
        self._session_refresh_busy = False
        self._feature_session_confirmed = True
        # Bind the account services (and let LearningWindow prewarm its
        # WebEngine-backed planning page) before the signed-in window becomes
        # visible.  The first native Chromium surface can otherwise make the
        # Windows top-level window briefly hide and show on first navigation.
        self._bind_learning_features(home)
        self._set_primary(home, 'LEARNING_MAIN')
        if old_home is not None and old_home.isMinimized():
            home.showMinimized()
        else:
            home.showNormal()
        self._hide_login_windows()
        if old_home is not None:
            old_home.dispose()
            old_home.close()
            old_home.deleteLater()
        home.raise_()
        home.activateWindow()
        self._session_timer.setInterval(240_000)
        self._session_timer.start()

    def _hide_login_windows(self):
        """Dismiss the gate and its owned auth surfaces after confirmed login.

        Hide rather than close: closing the gate invokes the shell quit handler.
        Generation/epoch fences already reject callbacks from prior login jobs.
        """
        from refactor.desktop.login_shell import LoginGateWindow
        self.gate.clear_password()
        self.gate.loading.finish()
        self.gate.hide()
        for window in self.app.topLevelWidgets():
            if isinstance(window, LoginGateWindow):
                window.clear_password()
                window.hide()
            elif window is not self.home:
                # Qt's isAncestorOf stops at a separate Window surface.
                owner = window.parentWidget()
                while owner is not None and owner is not self.gate:
                    owner = owner.parentWidget()
                if owner is self.gate:
                    window.hide()
        diagnostic_event('login', state='CONNECTED')

    def _feature_session(self, home, account_id):
        # Called by learning clients on every request; never capture a token.
        current = self._login_result
        if (self._closing or home is None or self.home is not home or current is None
                or current.identity.account_id != account_id
                or not self._feature_session_confirmed
                or (self._account_settings is not None and self._account_settings.password_pending)):
            return None
        closure = self._closure
        if (closure is not None and closure.profile.account_id == account_id
                and (closure._busy or (closure.record and closure.record['submitted']))):
            return None
        # A native refresh rotates the server-side access token before the
        # follow-up workspace/role check completes.  During a transient
        # failure that check is reported as ``session_retry`` and the last
        # confirmed LoginResult still contains the now-revoked token.  Read
        # the account-scoped credential pair that the refresh operation has
        # already persisted and use its access token while retaining the last
        # confirmed identity/tenant/role.  This keeps learning requests alive
        # during a short outage without inventing a new account context.
        if self._session_refresh_degraded:
            try:
                profile = current.identity.profile()
                credentials = self._session_credentials
                # Refresh writes access and refresh secrets as one locked
                # account operation.  Reading under the same lock avoids a
                # feature request observing one half of a rotated pair.
                from .login_session import account_session_lock
                # Refresh can hold this lock across a slow network exchange.
                # Feature workers must yield and retry, never wait behind it.
                with account_session_lock(profile, blocking=False):
                    access_token, refresh_token = credentials.load_oauth_tokens(profile)
                if (isinstance(access_token, str) and access_token
                        and isinstance(refresh_token, str) and refresh_token):
                    if (access_token, refresh_token) != (current.access_token, current.refresh_token):
                        from dataclasses import replace
                        return replace(current, access_token=access_token, refresh_token=refresh_token)
            except Exception:
                # Never send a known-stale token after a rotation if the
                # local vault cannot be read.  The feature will report an
                # unavailable session while the shell keeps the account
                # visible and retries the refresh.
                return None
        return current

    def _bind_learning_features(self, home, *, rebind=False):
        from .account_protocol import AccountConfig
        account_id = self._login_result.identity.account_id
        try:
            binder = home.rebind_feature_context if rebind else home.bind_feature_context
            binder(
                session_provider=lambda: self._feature_session(home, account_id),
                api_base_url=AccountConfig.from_env().api_base_url,
                ca_bundle=self.runtime.ca_bundle,
            )
        except Exception:
            # Account entry remains available with an older learning baseline
            # or a missing feature configuration.
            home.set_session_notice('学习功能连接暂不可用，可继续使用账号设置。')

    def _replace_learning_home_for_scope(self, result):
        """Rebind the learning shell after the server selects a new scope.

        A token refresh can legitimately select a different identity scope for
        the same account (for example after membership/default-workspace
        changes). Existing plan/material/classroom pages retain the old
        authenticated scope and must never continue querying it. Rebinding
        the existing learning shell gives every feature a new scope-bound
        service without replacing its top-level window. This is deliberately
        separate from ``_expire_active_session``: a scope refresh is not a
        logout.
        """
        old_home = self.home
        if old_home is None:
            return None
        # Keep the existing top-level QWidget/HWND.  Only scope-bound child
        # pages and services are replaced, so a workspace rotation cannot
        # flash the whole application or leave the account badge stale.
        self._bind_learning_features(old_home, rebind=True)
        old_home.set_server_role(self._confirmed_server_role)
        return old_home

    def _open_account_settings(self):
        if self._closing or self._login_result is None or self.home is None:
            return
        if self._closure is not None and self._closure.profile.account_id == self._login_result.identity.account_id:
            if not self._closure._closed:
                self._closure.page.raise_()
                return
            if self._closure._busy:
                self.home.set_session_notice('原注销操作正在处理，请等待或稍后继续查询。')
                return
        if self.session.closure_blocks_login(self._login_result.identity.profile()):
            self._open_closure()
            return
        previous = self._account_settings
        if previous is not None and not previous._closed:
            previous.page.raise_()
            previous.page.activateWindow()
            return
        if previous is not None and previous.password_pending:
            self.home.set_session_notice('密码操作正在处理，请等待结果后继续。')
            return
        from .account_protocol import AccountConfig
        from .account_security_client import AccountSecurityClient
        from .account_security_transport import AccountSecurityTransport
        from .account_settings_page import AccountSettingsPage
        from .account_settings_controller import AccountSettingsController

        identity = self._login_result.identity
        try:
            if self._security_client_factory is None:
                client = AccountSecurityClient(AccountSecurityTransport(
                    AccountConfig.from_env(), identity, self._session_credentials))
            else:
                client = self._security_client_factory(identity, self._session_credentials)
            page = AccountSettingsPage(identity.profile(), self.home)
            page.set_revocation_pending(self.session.revocation_pending(identity.profile()))
            page.closure_requested.connect(self._open_closure)
            controller = AccountSettingsController(
                page, client, self.session, identity, self._session_credentials,
                password_result=self._settings_password_result,
                session_expired=self._settings_session_expired,
                session_epoch=lambda: self._session_epoch, parent=self.app,
            )
            self._account_settings = controller
            page.show()
        except Exception:
            self.home.set_session_notice('账号设置暂时无法打开，请重新验证账号后重试。')

    def _close_account_settings(self):
        controller = self._account_settings
        if controller is not None and not controller._closed:
            controller.page.close()

    def _settings_session_expired(self, controller):
        if self._login_result is not None and self._login_result.identity.account_id == controller.identity.account_id:
            self._expire_active_session(self._login_result)

    def _settings_password_result(self, controller, result):
        profile = controller.identity.profile()
        message = {
            'PASSWORD_CHANGED': '密码已修改，旧登录信息已停用，请使用新密码重新登录。',
            'PASSWORD_CHANGED_REVOCATION_PENDING': '密码已修改，但会话撤销尚未全部确认。请用新密码重新登录，再从账号设置主动重试撤销。',
            'PASSWORD_CHANGE_OUTCOME_UNKNOWN': '密码修改结果未确认，已停止本机自动登录。请用新密码或原密码重新登录；不会自动重发改密，可在账号设置主动重试撤销会话。',
            'SESSION_REVOCATION_CONFIRMED': '服务器已确认会话撤销，本次新会话也已结束，请重新登录。',
            'SESSION_REVOCATION_PENDING': '会话撤销尚未全部确认，本次会话已停止使用。请重新登录后按需主动重试；不会自动循环。',
        }[result['state']]
        if not result['cleaned']:
            message += ' 本机旧登录信息未能全部清理，请重试清理。'
            self._cleanup_profile = profile
            self._show_cleanup_retry()
        current = self._login_result
        if (current is not None and current.identity.account_id == profile.account_id
                and self._session_epoch == controller.password_epoch):
            login_name = self.gate.username_field.text().strip()
            self._close_account_settings()
            self._session_epoch += 1
            self._session_refresh_busy = False
            self._session_timer.stop()
            self._logout_pending = self._switch_pending = False
            self._session_credentials = None
            if self.home is not None:
                self._show_gate_before_retiring_home()
                self.home.dispose()
                self.home.hide()
            self._cancel_login()
            self._refresh_local_accounts()
            self.gate.username_field.setText(login_name)
            if not self._closing:
                self.gate.show()
                self.gate.notice_label.setText(message)
                self.gate.password_field.setFocus()
        elif current is None and not self._closing:
            from .login_shell import LoginGateState
            if self.gate.state is not LoginGateState.AUTHENTICATING:
                self.gate.notice_label.setText(message)
        elif current is not None and self.home is not None and not result['cleaned']:
            self.home.set_session_notice('此前账号的本机登录信息未能全部清理；该账号已禁止自动恢复。')

    def _refresh_closure_recovery(self):
        from .account_closure_store import ClosureStore
        from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
        if self._closure_recovery is not None:
            self._closure_recovery.hide()
            self._closure_recovery.deleteLater()
        panel = self._closure_recovery = QWidget(self.gate)
        box = QVBoxLayout(panel)
        try:
            records = ClosureStore(self.session.preferences.settings).records()
            for record in records:
                profile = ClosureStore.profile(record)
                button = QPushButton('继续注销 / 本机清理：' + profile.safe_account_label)
                button.clicked.connect(lambda _checked=False, p=profile: self._open_closure(p))
                box.addWidget(button)
        except Exception:
            records = []
            self.gate.notice_label.setText('注销恢复记录无法读取；请保留本机记录，不要重新发起删除。')
        self.gate.layout().addWidget(panel)
        panel.setVisible(bool(records))
        if self._closure is not None and self._closure.record and self._closure.record['state'] == 'COMPLETED':
            try:
                self._refresh_local_accounts()
            except Exception:
                pass

    def _open_closure(self, profile=None):
        if self._closing:
            return
        if self._closure is not None and not self._closure._closed:
            self._closure.page.raise_()
            self._closure.page.activateWindow()
            return
        if self._closure is not None and self._closure._busy:
            return
        if profile is None:
            if self._login_result is None:
                return
            profile = self._login_result.identity.profile()
        from .account_closure_client import ClosureClient
        from .account_closure_store import ClosureStore
        from .account_closure_cleanup import ClosureCleanup
        from .account_closure_page import ClosurePage
        from .account_closure_controller import ClosureController
        from .account_protocol import AccountConfig
        try:
            current = self._login_result
            credentials = self._session_credentials if current and current.identity.account_id == profile.account_id else None
            client = (self._closure_client_factory(profile, credentials) if self._closure_client_factory else
                      ClosureClient(AccountConfig.from_env(), profile, credentials))
            cleanup = self._closure_cleanup_factory(self.session) if self._closure_cleanup_factory else ClosureCleanup(self.session)
            from . import dialog_theme
            page = ClosurePage(profile, theme=dialog_theme.theme_of(self.home))
            home = self.home
            identity = current.identity if current and current.identity.account_id == profile.account_id else None

            def export_is_current():
                active = self._feature_session(home, profile.account_id)
                return (not self._session_refresh_degraded
                        and identity is not None and active is not None
                        and (active.identity.organization_id, active.identity.workspace_id)
                        == (identity.organization_id, identity.workspace_id))

            self._closure = ClosureController(page, client, ClosureStore(self.session.preferences.settings), cleanup,
                release_account=self._release_closed_account, changed=self._refresh_closure_recovery,
                export_plan=getattr(home, 'request_current_plan_export', None),
                export_is_current=export_is_current, parent=self.app)
            self._close_account_settings()
            page.show()
        except Exception:
            if self.home is not None and current is not None:
                self.home.set_session_notice('注销恢复信息暂不可用，请保留记录后重试。')
            else:
                self.gate.notice_label.setText('注销恢复信息暂不可用，请保留记录后重试。')

    def _close_account_closure(self):
        if self._closure is not None and not self._closure._closed:
            self._closure.page.close()

    def _release_closed_account(self, profile):
        # Called only after a validated COMPLETED response (or its persisted
        # recovery record). Never dispose the window of another signed-in user.
        current = self._login_result
        if current is None or current.identity.account_id != profile.account_id:
            return
        if self.home is not None:
            self._show_gate_before_retiring_home()
            self.home.dispose()
            self.home.hide()
        self._close_account_settings()
        if isinstance(self._session_credentials, self._EphemeralCredentials):
            self._session_credentials.logout(profile)
        self._session_credentials = None
        self._login_result = None
        self._remembered_profile = None
        self._session_epoch += 1
        self._session_refresh_busy = False
        self._logout_pending = self._switch_pending = False
        self._session_timer.stop()
        self._cancel_login()
        if not self._closing:
            self.gate.show()
            self.gate.notice_label.setText('服务器已确认注销完成，正在清理本机此账号资料。')

    def _show_cleanup_retry(self):
        if self._cleanup_button is None:
            from PySide6.QtWidgets import QPushButton
            self._cleanup_button = QPushButton('重试清理旧登录信息', self.gate)
            self._cleanup_button.clicked.connect(self._retry_settings_cleanup)
            self.gate.layout().addWidget(self._cleanup_button)
        self._cleanup_button.setEnabled(True)
        self._cleanup_button.show()

    def _retry_settings_cleanup(self):
        profile = self._cleanup_profile
        if profile is None:
            return
        self._cleanup_button.setEnabled(False)
        def work():
            from .login_session import account_session_lock
            try:
                with account_session_lock(profile):
                    if self._cleanup_profile is not profile or not self.session.requires_reauthentication(profile):
                        cleaned = True
                    else:
                        cleaned = self.session.invalidate_saved_login(profile)
            except Exception:
                cleaned = False
            self.gate.background_succeeded.emit(f'{self._generation}:security_cleanup', (profile, cleaned))
        threading.Thread(target=work, name='mentorflow-account-cleanup', daemon=True).start()

    def _should_revoke_discarded_result(self, result) -> bool:
        try:
            row = self.session.registry.get(result.identity.profile().account_id)
        except Exception:
            return True
        return row is None or not row["remember_session"]

    def _best_effort_native_logout(self, refresh_token: str, *, identity=None) -> None:
        token = str(refresh_token or "")
        if not token:
            return

        def work():
            async def end_session():
                from refactor.desktop.account_protocol import AccountConfig
                from refactor.desktop.native_auth import NativeCredentialClient

                from refactor.desktop.account_identity import issuer_hash

                config = AccountConfig.from_env()
                if (
                    identity is not None
                    and identity.issuer_hash != issuer_hash(config.canonical_identity_issuer)
                ):
                    return
                client = NativeCredentialClient(config)
                try:
                    await client.end_session(token)
                finally:
                    await client.aclose()

            try:
                asyncio.run(end_session())
            except Exception:
                pass

        threading.Thread(
            target=work,
            name="mentorflow-shell-best-effort-logout",
            daemon=True,
        ).start()

    def _discard_login_result(
        self, result, *, revoke_remote: bool = False, refresh_token: str | None = None
    ) -> None:
        if isinstance(result, tuple) and result:
            result = result[0]
        if revoke_remote:
            token = refresh_token or getattr(result, "refresh_token", "")
            self._best_effort_native_logout(token, identity=getattr(result, "identity", None))

    class _EphemeralCredentials:
        """Refresh one non-persistent session without writing Locker tokens."""

        def __init__(self, result):
            self.profile = result.identity.profile()
            self.pair = (result.access_token, result.refresh_token)

        def load_oauth_tokens(self, profile):
            if profile.account_id != self.profile.account_id:
                raise ValueError("ENTERPRISE_ACCOUNT_RESTORE_MISMATCH")
            return self.pair

        def store_oauth_tokens(self, identity, *, access_token, refresh_token):
            if identity.profile().account_id != self.profile.account_id:
                raise ValueError("ENTERPRISE_ACCOUNT_RESTORE_MISMATCH")
            self.pair = (access_token, refresh_token)
            return {}

        def logout(self, profile):
            if profile.account_id == self.profile.account_id:
                self.pair = ("", "")

    def _restore_active_session(self, profile, credentials):
        if self._native_restorer is not None:
            return self._native_restorer(profile, credentials)
        from refactor.desktop.account_protocol import AccountConfig
        from refactor.desktop.native_auth import NativeCredentialClient

        async def restore():
            client = NativeCredentialClient(AccountConfig.from_env())
            try:
                return await client.restore(profile, credentials)
            finally:
                await client.aclose()

        return asyncio.run(restore())

    def _refresh_active_session(self) -> None:
        source = self._login_result
        if (
            self._closing
            or getattr(self, '_update_handoff_account', None) is not None
            or source is None
            or self.home is None
            or self._session_refresh_busy
            or (self._account_settings is not None and self._account_settings.password_pending)
            or (self._closure is not None and not self._closure._closed)
        ):
            return
        profile = source.identity.profile()
        if self._closure is not None and self._closure.profile.account_id == profile.account_id and self._closure._busy:
            return
        if self.session.closure_blocks_login(profile):
            self.home.set_session_notice('此账号的注销操作尚待确认，请继续查询原操作。')
            return
        try:
            row = self.session.registry.get(profile.account_id)
            if row is None:
                raise ValueError("ACCOUNT_NOT_REGISTERED")
            remember = bool(row["remember_session"])
            credentials = self.session.credentials if remember else self._session_credentials
            if not remember and credentials is None:
                credentials = self._session_credentials = self._EphemeralCredentials(source)
        except Exception:
            self._retry_session_refresh()
            return

        self._session_refresh_busy = True
        session_epoch = self._session_epoch

        def work():
            try:
                from refactor.desktop.login_session import account_session_lock

                with account_session_lock(profile):
                    if self.session.closure_blocks_login(profile):
                        raise RuntimeError('Closure status must be reconciled')
                    if self.session.requires_reauthentication(profile):
                        from .account_protocol import AccountError
                        raise AccountError('ENTERPRISE_SESSION_REAUTH_REQUIRED', '请使用密码重新登录。')
                    refreshed = self._restore_active_session(profile, credentials)
                self.gate.background_succeeded.emit(
                    f"{session_epoch}:session_refresh", (source, refreshed)
                )
            except Exception as exc:
                diagnostic_event('refresh', state='FAILED', code=getattr(exc, 'code', 'NETWORK_UNAVAILABLE'), exc_info=True)
                code = getattr(exc, "code", "")
                expired = code in {
                    "ENTERPRISE_SESSION_REAUTH_REQUIRED",
                    "NATIVE_SESSION_SUPERSEDED",
                }
                job = "session_expired" if expired else "session_retry"
                self.gate.background_failed.emit(
                    f"{session_epoch}:{job}", str(exc) if not expired else ""
                )

        threading.Thread(target=work, name="mentorflow-shell-session-refresh", daemon=True).start()

    def _accept_session_refresh(self, source, refreshed) -> None:
        if self._account_settings is not None and self._account_settings.password_pending:
            # Rotation is already in the captured credential store. Do not
            # re-enable restore preferences while a password mutation is pending.
            self._session_refresh_busy = False
            return
        if ((self._closure is not None and self._closure._busy)
                or self.session.closure_blocks_login(source.identity.profile())):
            self._retry_session_refresh()
            return
        if self._login_result is not source or self.home is None:
            self._discard_login_result(
                refreshed,
                revoke_remote=self._should_revoke_discarded_result(refreshed),
            )
            return
        if refreshed.identity.profile().account_id != source.identity.profile().account_id:
            self._discard_login_result(refreshed, revoke_remote=False)
            self._retry_session_refresh()
            return
        try:
            row = self.session.registry.get(source.identity.profile().account_id)
            if row is None:
                raise ValueError("ACCOUNT_NOT_REGISTERED")
            if row["remember_session"]:
                self.session.accept(
                    refreshed,
                    remember=True,
                    automatic=bool(row["auto_login"]),
                    remember_password=bool(row["remember_password"]),
                    username=row.get("username"),
                )
        except Exception:
            # A settings/vault failure is not server revocation. Rotation was
            # already saved by the restorer; retry without ending that session.
            self._retry_session_refresh()
            return
        scope_changed = (
            (
                source.identity.issuer,
                source.identity.subject,
                source.identity.organization_id,
                source.identity.workspace_id,
            )
            != (
                refreshed.identity.issuer,
                refreshed.identity.subject,
                refreshed.identity.organization_id,
                refreshed.identity.workspace_id,
            )
        )
        self._login_result = refreshed
        role = self._confirmed_role_from_result(refreshed)
        if role in {"SUPER_ADMIN", "LEARNER"}:
            self._confirmed_server_role = role
            self._confirmed_role_account_id = refreshed.identity.profile().account_id
        self._feature_session_confirmed = True
        self._session_refresh_degraded = False
        if scope_changed:
            self._close_account_settings()
        if row["remember_session"]:
            self._session_credentials = self.session.credentials
        self._session_epoch += 1
        self._session_refresh_busy = False
        self._session_timer.setInterval(240_000)
        self._session_timer.start()
        if scope_changed:
            self._replace_learning_home_for_scope(refreshed)
        # Do not replace a confirmed role with an absent or malformed value.
        # The account identity is still the same, and a later refresh can
        # complete the role check without making the shell look logged out.
        if self._confirmed_role_account_id == refreshed.identity.profile().account_id:
            self.home.set_server_role(self._confirmed_server_role)
        self.home.clear_session_notice()
        self._hide_login_windows()
        diagnostic_event('refresh', state='RECOVERED')
        self.home.session_recovered()
        if self._logout_pending:
            from PySide6.QtCore import QTimer

            self._logout_pending = False
            QTimer.singleShot(0, self._logout_account)
        elif self._switch_pending:
            from PySide6.QtCore import QTimer

            self._switch_pending = False
            QTimer.singleShot(0, self._switch_account)

    def _expire_active_session(self, source) -> None:
        if self._login_result is not source:
            return
        diagnostic_event('login', state='REAUTH_REQUIRED')
        self._close_account_settings()
        from refactor.desktop.login_shell import LoginGateState

        profile = source.identity.profile()
        self._session_epoch += 1
        self._session_refresh_busy = False
        self._session_timer.stop()
        self._logout_pending = False
        switch_pending = self._switch_pending
        self._switch_pending = False
        row = self.session.registry.get(profile.account_id)
        try:
            if row is not None and row["remember_session"]:
                self.session.credentials.logout(profile)
            self.session.registry.set_options(
                profile.account_id, remember_session=False, auto_login=False
            )
            self.session.preferences.disable_restore()
        except Exception:
            pass
        self._discard_login_result(source)
        if isinstance(self._session_credentials, self._EphemeralCredentials):
            self._session_credentials.logout(profile)
        self._session_credentials = None
        self._login_result = None
        self._confirmed_server_role = None
        self._confirmed_role_account_id = None
        self._remembered_profile = profile
        if self.home is not None:
            self._show_gate_before_retiring_home()
            self.home.dispose()
            self.home.hide()
        self.gate.username_field.setText(row.get("username", "") if row else "")
        self.gate.remember_box.setChecked(False)
        self.gate.auto_box.setChecked(False)
        self.gate.remember_password_box.setChecked(bool(row and row["remember_password"]))
        self.gate.clear_password()
        self.gate.set_state(LoginGateState.LOGIN_READY, detail="登录已失效，请重新验证账号。")
        self.gate.notice_label.setText("登录已失效，请重新输入账号和密码。")
        self.gate.show()
        if switch_pending:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._switch_account)

    def _retry_session_refresh(self, detail: str = "") -> None:
        # Keep the last confirmed login and role during a transient outage.
        # Previously this cleared the feature session, so classroom and plan
        # pages reported a logout even though no account change had occurred.
        self._session_refresh_degraded = True
        diagnostic_event('refresh', state='RETRY')
        if self.home is not None:
            self.home.set_session_notice(
                "账号连接不稳，正在重试；当前学习不受影响",
                show_verify=False,
            )
        self._session_refresh_busy = False
        if self._logout_pending:
            from PySide6.QtCore import QTimer

            self._logout_pending = False
            QTimer.singleShot(0, self._logout_account)
            return
        if self._switch_pending:
            from PySide6.QtCore import QTimer

            self._switch_pending = False
            QTimer.singleShot(0, self._switch_account)
            return
        self._session_timer.setInterval(30_000)
        self._session_timer.start()

    def _refresh_token_for_logout(self, profile, result) -> str:
        credentials = self._session_credentials
        if (
            isinstance(credentials, self._EphemeralCredentials)
            and credentials.profile.account_id == profile.account_id
        ):
            return credentials.pair[1] or result.refresh_token
        try:
            _access, refresh = self.session.credentials.load_oauth_tokens(profile)
            return refresh or result.refresh_token
        except Exception:
            return result.refresh_token

    def _background_succeeded(self, job: str, result: object) -> None:
        try:
            generation, job = job.split(":", 1)
            generation_number = int(generation)
        except (ValueError, AttributeError):
            return
        if job == 'security_cleanup':
            profile, cleaned = result
            if self._cleanup_profile is profile:
                self._cleanup_button.setEnabled(True)
                if cleaned:
                    self._cleanup_profile = None
                    self._cleanup_button.hide()
                self.gate.notice_label.setText('本机旧登录信息已清理，请使用密码重新登录。' if cleaned else '本机旧登录信息仍未能全部清理；自动恢复保持关闭，可稍后重试。')
            return
        if job == "session_refresh":
            if generation_number != self._session_epoch or self._closing:
                if isinstance(result, tuple) and len(result) == 2:
                    refreshed = result[1]
                    self._discard_login_result(
                        refreshed,
                        revoke_remote=self._should_revoke_discarded_result(refreshed),
                    )
                return
            source, refreshed = result
            self._session_refresh_busy = False
            self._accept_session_refresh(source, refreshed)
            return
        is_current = generation_number == self._generation and not self._closing
        if not is_current:
            if job in {"restore", "authenticate", "update_restore"}:
                self._discard_login_result(
                    result,
                    revoke_remote=(
                        job == "authenticate"
                        or self._should_revoke_discarded_result(result)
                    ),
                )
            return
        if job == "authenticate":
            login_result, username, password, remember, automatic = result
            try:
                self._accept_login(
                    login_result,
                    username=username,
                    password=password,
                    remember=remember,
                    automatic=automatic,
                )
            finally:
                password = None
            return
        if job in {"restore", "update_restore"}:
            if job == 'update_restore':
                self._update_restore_pending = False
            if (
                self._remembered_profile is None
                or result.identity.profile().account_id != self._remembered_profile.account_id
            ):
                self._discard_login_result(result)
                self._background_failed(
                    f"{generation}:{job}",
                    "登录会话与所选账号不匹配，请重新登录。",
                )
                return
            self._accept_login(result, **({'update_handoff': True} if job == 'update_restore' else {}))
            return
        if job == "logout":
            self._finish_logout(bool(result))

    def _background_failed(self, job: str, message: str) -> None:
        from refactor.desktop.login_shell import LoginGateState

        try:
            generation, job = job.split(":", 1)
            generation_number = int(generation)
        except (ValueError, AttributeError):
            return
        if job in {"session_expired", "session_retry"}:
            if generation_number != self._session_epoch or self._closing:
                return
            if job == "session_expired":
                source = self._login_result
                if source is not None:
                    self._expire_active_session(source)
            else:
                self._retry_session_refresh(message)
            return
        if generation_number != self._generation or self._closing:
            return
        if job == 'update_restore':
            self._update_restore_pending = False
        if job == "logout_error":
            self._invalidate_login_jobs()
            self.gate.set_state(LoginGateState.AUTH_FAILED, detail=message)
            self.gate.primary_button.hide()
            self.gate.cancel_button.hide()
            return
        if job == "restore_expired":
            try:
                self.session.preferences.disable_restore()
                self.session.suspend_automatic()
            except Exception:
                pass
            self._remembered_profile = None
            self.gate.remember_box.setChecked(False)
            self.gate.clear_password()
            self.gate.set_state(LoginGateState.LOGIN_READY, detail=message)
            self.gate.remember_password_box.setChecked(False)
            self.gate.notice_label.setText(message)
            AccountShell._schedule_account_prewarm(self)
            return
        if job == "restore_retry":
            from PySide6.QtCore import QTimer

            # Keep the selected profile and tokens after an outage. The generation
            # fence prevents a delayed retry from overriding logout/manual login.
            self.gate.set_state(LoginGateState.AUTHENTICATING, detail="正在恢复连接…")
            def retry():
                if not self._closing and generation_number == self._generation:
                    self._begin_remembered_login()
            QTimer.singleShot(30_000, retry)
            return
        self._invalidate_login_jobs()
        self._remembered_profile = None
        self.gate.set_state(LoginGateState.AUTH_FAILED, detail=message)
        AccountShell._schedule_account_prewarm(self)

    def _begin_account_action(self, intent: str) -> None:
        if self._closing:
            return
        if intent not in ("register", "forgot_password"):
            return
        if self.gate.account_capabilities.get(intent) is not True:
            return
        from refactor.desktop.login_shell import LoginGateState

        self._invalidate_login_jobs()
        generation = self._generation
        self._account_form_state = dict(profile=self._remembered_profile, state=self.gate.state,
            title=self.gate.state_title.text(), button=self.gate.primary_button.text(),
            detail=self.gate.state_detail.text(), notice=self.gate.notice_label.text())
        self._prewarm_timer.stop()
        self.gate.set_state(LoginGateState.AUTHENTICATING, detail="正在打开账号服务…")
        self._loading_action = 'account_service'
        try:
            if self._lifecycle_pane_factory is None:
                from refactor.desktop.lifecycle_pane import AccountLifecyclePane

                factory = AccountLifecyclePane
            else:
                factory = self._lifecycle_pane_factory
            pane, self._warm_pane = self._warm_pane, None
            if pane is None:
                pane = factory(intent, parent=self.gate, shell_runtime=self.runtime)
            activate = getattr(pane, 'activate', None)
            if callable(activate): activate(intent, self.gate.current_theme)
            self._pane = pane
            pane.finished.connect(
                lambda completed: self._return_from_account_action(
                    generation,
                    completed=completed,
                    registration=intent == "register",
                )
            )
            self.gate.set_auth_content(pane)
            from PySide6.QtWidgets import QWidget
            if isinstance(pane, QWidget) and hasattr(pane, 'page') and hasattr(pane, 'view'):
                from .native_loading import WebFirstFrame
                pane._native_first_frame = WebFirstFrame(pane, theme=self.gate.current_theme,
                    reduced=self.gate.loading.reduced, selector='#mf-account-lifecycle:not([hidden])',
                    label='正在打开账号服务…', fallback_body=False,
                    cancel=self._cancel_login)
        except Exception:
            self._return_from_account_action(generation)
            self.gate.notice_label.setText("账号操作页面暂不可用，请稍后重试。")

    def _return_from_account_action(self, generation: int, *, completed=False, registration=False) -> None:
        if generation != self._generation or self._closing:
            return
        from refactor.desktop.lifecycle_client import COMPLETED_MESSAGE
        from refactor.desktop.login_shell import LoginGateState

        pane, self._pane = self._pane, None
        form_state = getattr(self, '_account_form_state', None)
        self._account_form_state = None
        completed_username = getattr(getattr(pane, "bridge", None), "completed_username", None)
        self.gate.set_auth_content(None)
        if pane is not None:
            dispose = getattr(pane, "dispose", None)
            if dispose is not None:
                dispose()
            else:
                pane.deleteLater()
        if not completed and form_state is not None:
            self._remembered_profile = form_state['profile']
            self.gate.set_state(form_state['state'], detail=form_state['detail'])
            self.gate.state_title.setText(form_state['title'])
            self.gate.primary_button.setText(form_state['button'])
            self.gate.notice_label.setText(form_state['notice'])
            self._refresh_remembered_password_hint()
            self._schedule_account_prewarm()
            return
        if completed and not registration:
            account_id = getattr(getattr(pane, "bridge", None), "completed_account_id", None)
            row = self.session.registry.get(account_id) if account_id else None
            if row is not None:
                try:
                    self.session.logout(self.session.registry.profile(row), remote=False, forget_password=True)
                except Exception:
                    self.gate.notice_label.setText(
                        "密码已重置，本机旧凭据未能删除，请移除本机账号后重新登录。"
                    )
        self._remembered_profile = None
        self._login_result = None
        self._confirmed_server_role = None
        self._confirmed_role_account_id = None
        self.gate.clear_password()
        self.gate.remember_password_box.setChecked(False)
        if registration and completed and completed_username:
            self.gate.username_field.setText(completed_username)
        detail = (
            "注册成功，请登录" if registration and completed else
            "注册页面已关闭；请确认页面提示提交成功后再使用新账号登录。"
            if registration
            else COMPLETED_MESSAGE
            if completed
            else "尚未确认密码重置完成。请重新发起找回，或使用当前密码登录。"
        )
        self.gate.set_state(LoginGateState.LOGIN_READY, detail=detail)
        AccountShell._schedule_account_prewarm(self)

    def _switch_account(self) -> None:
        AccountShell.clear_update_login(self)
        self._close_account_closure()
        self._close_account_settings()
        if self._session_refresh_busy:
            self._switch_pending = True
            if self.home is not None:
                self.home.set_session_notice("正在安全更新会话；完成后会切换账号。")
            return
        self._switch_pending = False
        self._logout_pending = False
        self._session_epoch += 1
        self._session_refresh_busy = False
        self._session_timer.stop()
        self._capabilities_retry_timer.stop()
        if self._login_result is not None:
            profile = self._login_result.identity.profile()
            result = self._login_result
            if isinstance(self._session_credentials, self._EphemeralCredentials):
                refresh_token = self._refresh_token_for_logout(profile, result)
                self._session_credentials.logout(profile)
                self._best_effort_native_logout(refresh_token, identity=result.identity)
            try:
                # Match the existing switch flow: keep opt-in credentials for A,
                # but require an explicit account choice on the next attempt.
                self.session.preferences.disable_restore()
                self.session.suspend_automatic()
            except Exception:
                self.gate.notice_label.setText("本机账号设置暂未更新；请检查账号后继续。")
        self._login_result = None
        self._confirmed_server_role = None
        self._confirmed_role_account_id = None
        self._session_credentials = None
        self._remembered_profile = None
        if self.home is not None:
            self._show_gate_before_retiring_home()
            self.home.dispose()
            self.home.hide()
        self._cancel_login()
        self._refresh_local_accounts()
        self.gate.show()
        self._load_capabilities()
        self.gate.username_field.setFocus()

    def _logout_account(self) -> None:
        AccountShell.clear_update_login(self)
        if self._login_result is None or self._closing:
            return
        self._close_account_closure()
        self._close_account_settings()
        if self._session_refresh_busy:
            self._switch_pending = False
            self._logout_pending = True
            if self.home is not None:
                self.home.set_session_notice("正在安全更新会话；完成后会退出登录。")
            return
        from refactor.desktop.login_shell import LoginGateState

        result = self._login_result
        profile = result.identity.profile()
        refresh_token = self._refresh_token_for_logout(profile, result)
        if isinstance(self._session_credentials, self._EphemeralCredentials):
            self._session_credentials.logout(profile)
        self._login_result = None
        self._confirmed_server_role = None
        self._confirmed_role_account_id = None
        self._session_credentials = None
        self._session_epoch += 1
        self._session_refresh_busy = False
        self._session_timer.stop()
        self._logout_pending = False
        self._switch_pending = False
        if self.home is not None:
            self._show_gate_before_retiring_home()
            self.home.dispose()
            self.home.hide()
        generation, _cancelled = self._new_request()
        self.gate.set_auth_content(None)
        self.gate.set_state(LoginGateState.AUTHENTICATING, detail="正在安全退出账号…")
        self._loading_action = 'logout'
        self.gate.loading.begin('正在安全退出账号…', retry=False, cancel=False)
        self.gate.cancel_button.hide()
        self.gate.show()

        def work():
            try:
                outcome = self.session.logout(
                    result.identity.profile(),
                    remote=True,
                    refresh_token=refresh_token,
                )
                self.gate.background_succeeded.emit(f"{generation}:logout", outcome)
            except Exception:
                self.gate.background_failed.emit(
                    f"{generation}:logout_error",
                    "此设备的会话未能完整清除，请关闭软件后重试。",
                )

        threading.Thread(target=work, name="mentorflow-shell-logout", daemon=True).start()

    def _finish_logout(self, remote_confirmed: bool) -> None:
        from refactor.desktop.login_shell import LoginGateState

        self._refresh_local_accounts()
        self._remembered_profile = None
        self._session_credentials = None
        self.gate.remember_box.setChecked(False)
        self.gate.auto_box.setChecked(False)
        self.gate.remember_password_box.setChecked(False)
        self.gate.clear_password()
        self.gate.set_state(LoginGateState.LOGIN_READY)
        self.gate.notice_label.setText(
            "已退出当前账号。" if remote_confirmed else "此设备已退出；远端注销未确认。"
        )
        self._load_capabilities()

    def _remembered_password_login(self):
        self._begin_remembered_login()

    def prepare_update_login(self):
        """Called synchronously on update worker, after download, before handoff."""
        from .login_session import account_session_lock
        result = self._login_result
        if result is None:
            return False  # Updating at the login gate needs no session handoff.
        if (self._closing or self._session_refresh_busy or self._logout_pending or self._switch_pending
                or (self._account_settings is not None and self._account_settings.password_pending)
                or (self._closure is not None and not self._closure._closed)):
            raise RuntimeError('账号正在处理其他操作，请稍后再更新。')
        profile = result.identity.profile()
        self._update_handoff_account = profile.account_id
        try:
            with account_session_lock(profile):
                if self.session.closure_blocks_login(profile) or self.session.requires_reauthentication(profile):
                    raise RuntimeError('请重新登录后再更新。')
                _, refresh = self._session_credentials.load_oauth_tokens(profile)
                self.session.update_handoff.write(profile, refresh)
            return True
        except Exception:
            self.clear_update_login()
            raise RuntimeError('无法准备更新后的登录，请稍后重试。') from None

    def prepare_update_shutdown(self):
        """Close visible account windows without logging out the handoff token."""
        self._release_account_prewarm()
        self._close_account_closure()
        self._close_account_settings()
        if self.home is not None:
            self.home.dispose()
            self.home.hide()
        self.gate.hide()
        self._update_exit_pending = True

    def clear_update_login(self):
        if getattr(self, '_update_handoff_account', None) is None:
            return
        try:
            self.session.update_handoff.clear()
        except Exception:
            self.session.update_handoff._log('交接失败')
        finally:
            self._update_handoff_account = None

    def notify_update_health(self, root, attempt, nonce):
        from refactor.desktop.delivery.per_user_install import notify_health
        # Fast handoff login may already have hidden the gate at 250 ms.
        # Report the visible home instead of a now-hidden login HWND.
        notify_health(root, self.home if self.home is not None else self.gate, attempt, nonce)

    def close(self) -> None:
        if self._closing:
            return
        if self._update_exit_pending:
            self._closing = True
            self._session_timer.stop()
            self._capabilities_retry_timer.stop()
            self._invalidate_login_jobs()
            if self.home is not None:
                self.home.hide()
            self.gate.hide()
            return
        self._release_account_prewarm()
        diagnostic_event('exit', reason='ACCOUNT_SHELL_CLEANUP')
        self._close_account_closure()
        self._close_account_settings()
        self._closing = True
        self._update_login_token = None
        if self.home is not None:
            self.home.dispose()
        self._session_epoch += 1
        self._session_timer.stop()
        self._capabilities_retry_timer.stop()
        self._logout_pending = False
        self._invalidate_login_jobs()
        result = self._login_result
        self._login_result = None
        self._confirmed_server_role = None
        self._confirmed_role_account_id = None
        if result is not None and isinstance(self._session_credentials, self._EphemeralCredentials):
            profile = result.identity.profile()
            refresh_token = self._refresh_token_for_logout(profile, result)
            if getattr(self, '_update_handoff_account', None) != profile.account_id:
                self._best_effort_native_logout(refresh_token, identity=result.identity)
            self._session_credentials.logout(profile)
        self._session_credentials = None


def _show_launch_error(message: str) -> None:
    title = "MentorFlow 无法启动"
    detail = (f"{message}\n\n请使用可信的 MentorFlow 安装程序修复后重试。"
              if getattr(sys, "frozen", False) else (
        f"{message}\n\n请确认 D:\\MentorFlow\\.venv 已由项目运行环境准备好，"
        "并使用 python -m refactor.desktop.account_shell_main 重新启动。"
    ))
    try:
        from PySide6.QtWidgets import QApplication
        from .update_dialog import UpdateDialog
        app = QApplication.instance() or QApplication([])
        dialog = UpdateDialog()
        dialog.update_status(dict(state='FATAL_UNRECOVERABLE', message=detail))
        dialog.later.setText('关闭')
        dialog.exec()
        return
    except Exception:
        pass
    sys.stderr.write(title + ": " + detail + "\n")


def _acquire_instance_lock(shell_runtime):
    """Keep the desktop shell single-instance.

    The account/session state is process-owned.  Running a second shell against
    the same credential vault makes one window show a stale session banner while
    another window still shows the cached role.  A small QLockFile prevents that
    split-brain UI and is released automatically when the process exits.
    """
    from PySide6.QtCore import QLockFile

    lock_path = shell_runtime.data_root / "runtime" / "MentorFlow.instance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_path))
    if not lock.tryLock(100):
        return None
    return lock


def friendly_name(row) -> str:
    """What the learner calls this account: the login name when the server only has the
    placeholder display name (self-registered accounts have none), never an id fragment."""
    display, username = (row.get("display_name") or "").strip(), (row.get("username") or "").strip()
    if username and (not display or display == "MentorFlow"):
        return username
    return display or username or "我的账号"


def main(*, install_root=None, update_attempt=None, update_nonce=None) -> int:
    # Qt's default D3D11 composition flickers on hybrid-GPU laptops (BUG-04);
    # OpenGL composition does not. Must be set before QApplication exists.
    os.environ.setdefault("QSG_RHI_BACKEND", "opengl")
    try:
        from .shell_paths import prepare_shell_runtime

        shell_runtime = prepare_shell_runtime()
        from refactor.runtime_logging import configure, install_exception_hooks, enable_crash_dump
        configure(shell_runtime.data_root)
        enable_crash_dump(shell_runtime.data_root)
        install_exception_hooks()
        from .process_diagnostics import ProcessDiagnostics
        diagnostics = ProcessDiagnostics(shell_runtime.data_root)
        from .web_schemes import register_teacher_stage_scheme

        register_teacher_stage_scheme()
        from PySide6.QtWidgets import QApplication

        app = QApplication(sys.argv)
        if install_root is not None:
            from .delivery.startup_window import log_phase, foreground, window_state
            log_phase(install_root, 'qt_ready')
        app.setOrganizationName("MentorFlow")
        app.setApplicationName("MentorFlow")
        instance_lock = _acquire_instance_lock(shell_runtime)
        if instance_lock is None:
            diagnostic_event('exit', reason='SECOND_INSTANCE')
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                None,
                "MentorFlow 已在运行",
                "已有 MentorFlow 窗口正在运行，请使用那个窗口；\n"
                "如果窗口被遮挡，请从任务栏切换到 MentorFlow。",
            )
            return 0
        # Keep the lock alive for the whole Qt application lifetime.  Storing
        # it on QApplication avoids accidental garbage collection while also
        # leaving the lock cleanup to QLockFile/process exit.
        app._mentorflow_instance_lock = instance_lock
        diagnostics.attach(app)
        from .login_shell import LoginGateWindow, LoginGateState
        gate = LoginGateWindow()
        diagnostics.set_primary(gate, 'LOGIN_MAIN')
        gate.set_state(LoginGateState.AUTHENTICATING, detail='正在启动 MentorFlow…')
        gate.loading.begin('正在启动 MentorFlow…')
        gate.cancel_button.hide()
        startup_close = lambda: diagnostics.request_exit('USER_MAIN_CLOSE')
        gate.close_button.clicked.connect(startup_close)
        # The gate's permanent OpenGL anchor exists before its first show.
        # Warm and switch account pages without changing that native surface.
        shell = AccountShell(app, shell_runtime, gate_window=gate, prepare_account_surface=True)
        if install_root is not None:
            foreground(gate)
        else:
            gate.showNormal()
        app.processEvents()
        if install_root is not None:
            log_phase(install_root, 'login_window_visible', **window_state(gate))
        gate.close_button.clicked.disconnect(startup_close)
        shell.show()
        if install_root is not None:
            from .updates import DesktopUpdates

            app._mentorflow_updates = DesktopUpdates(app, install_root, shell.gate, login_handoff=shell)
            app._mentorflow_updates.changed.connect(
                lambda value: diagnostic_event('update', state=value.get('state', 'UNKNOWN')))
            app._mentorflow_updates.changed.connect(shell._resume_update_login)
            readiness_logged = [False]
            def record_login_ready(value):
                if not readiness_logged[0] and value.get('login_allowed'):
                    readiness_logged[0] = True
                    log_phase(install_root, 'login_window_input_ready', **window_state(gate))
            app._mentorflow_updates.changed.connect(record_login_ready)
            app._mentorflow_updates.start()
            from .delivery.startup_integrity import schedule
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: schedule(install_root, app, shell))
            if update_attempt and update_nonce:
                from PySide6.QtCore import QTimer

                QTimer.singleShot(250, lambda: shell.notify_update_health(
                    install_root, update_attempt, update_nonce))
        result = app.exec()
        diagnostic_event('exit', reason='EVENT_LOOP_RETURNED', exit_code=result)
        if getattr(shell, '_update_exit_pending', False) and getattr(sys, 'frozen', False):
            # All synchronous saves and service disposal completed before quit.
            # Python's executor atexit joins must not keep a windowless frozen
            # App alive after the acknowledged updater handoff.
            import logging
            logging.shutdown()
            os._exit(result)
        return result
    except Exception as exc:
        diagnostic_event('startup', state='FAILED', exc_info=True)
        diagnostic_event('exit', reason='STARTUP_FAILED')
        _show_launch_error(str(exc) or "账号外壳运行环境暂不可用。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
