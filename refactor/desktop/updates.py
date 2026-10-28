"""Qt adapter for the existing signed per-user updater; no second updater."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication

# User 2026-10-17: re-check every 30 minutes so a mandatory release reaches apps left open.
AUTOMATIC_CHECK_SECONDS = 30 * 60
BACKGROUND_PREPARE_RETRY_DELAYS = (2, 3, 5, 10, 20, 40)


class DesktopUpdates(QObject):
    changed = Signal(object)
    _finished = Signal(object)
    def __init__(self, app, root, gate, *, core=None, login_handoff=None):
        super().__init__(app)
        if core is None:
            from refactor.desktop.delivery import per_user_update_core as core
        self.core, self.root, self.gate = core, Path(root), gate
        self._login_handoff = login_handoff
        self._handoff_armed = False
        self._cleanup_started = False
        self.state = {"state": "IDLE", "message": "尚未检查更新。"}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mf-app-update")
        self._busy = self._closed = False
        self._cancel = threading.Event()
        from refactor.desktop.delivery.update_session import UpdateSession
        from refactor.desktop.delivery.update_session import UnavailableSession
        try:
            self._session = UpdateSession()
        except OSError:
            self._session = UnavailableSession()
        self._finished.connect(self._receive, Qt.ConnectionType.QueuedConnection)
        self._prompt = None
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(60000)
        self._retry.timeout.connect(lambda: self.run("install", self._window))
        self._window = None
        self._deferred = False
        self._next_automatic = 0
        self._continuing_download = False
        self._background_preparing = False
        self._background_prepare_attempt = 0
        self._background_retry_suppressed = False
        self._background_retry = QTimer(self)
        self._background_retry.setSingleShot(True)
        self._background_retry.timeout.connect(self._prepare_in_background)
        self._login_blocked = False
        self._startup_cached = None
        self._startup_pointer_policy = None
        self._startup_started = None
        app.aboutToQuit.connect(self.close)
        app.commitDataRequest.connect(self._defer_session)
        core.attach_shutdown_guard(app)

    def start(self):
        # User 2026-10-20: login never waits for network update discovery.
        # Locally verified mandatory policy and later signed results still apply.
        getattr(self.gate, 'set_login_available', self.gate.setEnabled)(True)
        self._startup_started = time.monotonic()
        self._publish({**self.state, 'state': 'CHECKING',
                       'login_allowed': True, 'login_check_pending': False,
                       'login_check_status': 'background',
                       'login_check_message': '',
                       'message': '正在后台检查更新…'})
        QTimer.singleShot(0, lambda: self.run("startup"))

    def _receive_pointer_policy(self, policy):
        """Hints are diagnostics; only verified policy can restrict login."""
        self._startup_pointer_policy = dict(policy)
        from .delivery.per_user_install import maintenance_log
        maintenance_log(self.root, 'startup_login_gate', outcome='background_pointer',
                        hinted_mandatory=policy['mandatory'],
                        pointer_version=policy['version'])

    def _apply_startup_result(self, result, *, status='ready'):
        result = {**result,
                  'login_check_pending': False,
                  'login_check_status': status,
                  'login_check_message': ''}
        result['mandatory'] = result.get('state') == 'MANDATORY_UPDATE'
        self._publish({**self.state, **result})
        self._login_blocked = not result.get('login_allowed', False)
        getattr(self.gate, 'set_login_available', self.gate.setEnabled)(not self._login_blocked)
        if self._login_blocked or result.get('state') == 'OPTIONAL_UPDATE' or result.get('deferred_update'):
            self._show_prompt()
        if result.get('state') in ('HEALTHY', 'OPTIONAL_UPDATE', 'MANDATORY_UPDATE'):
            from .delivery.per_user_install import schedule_updater_refresh
            schedule_updater_refresh(self.root)
        if status == 'ready' and result.get('feed') == 'VERIFIED':
            self._next_automatic = time.monotonic() + AUTOMATIC_CHECK_SECONDS
        from .delivery.per_user_install import maintenance_log
        maintenance_log(self.root, 'startup_login_gate', outcome=status,
                        elapsed_ms=round((time.monotonic() - (self._startup_started or time.monotonic())) * 1000),
                        state=result.get('state'), feed=result.get('feed'))

    def snapshot(self):
        return {**self.state, "busy": self._busy, "available": True, "deferred": self._deferred}

    def _publish(self, value):
        if not self._closed:
            if value.get('state') in ('UP_TO_DATE', 'CURRENT_HEALTHY'):
                value = {**value, 'deferred_update': False}
            if (value.get('state') == 'PENDING_INSTALL' or value.get('deferred_update') and
                    value.get('state') in ('HEALTHY', 'OPTIONAL_UPDATE', 'UPDATE_AVAILABLE', 'MANDATORY_UPDATE')):
                from .delivery.per_user_update_core import PENDING_MESSAGE
                value = {**value, 'message': value.get('deferred_message') or PENDING_MESSAGE,
                         'latest': value.get('deferred_target') or value.get('latest') or value.get('target', '')}
            self.state = dict(value)
            self.changed.emit(self.snapshot())

    def run(self, action, window=None):
        if self._closed:
            return self.snapshot()
        if action == "defer":
            if self.state.get('mandatory') or self.state.get('state') == 'MANDATORY_UPDATE':
                return self.snapshot()
            self._deferred = True
            self.changed.emit(self.snapshot())
            return self.snapshot()
        if self._busy: return self.snapshot()
        if self._background_preparing and action in ('startup', 'check', 'automatic'):
            return self.snapshot()
        if action == "automatic":
            if time.monotonic() < self._next_automatic: return self.snapshot()
            self._next_automatic = time.monotonic() + AUTOMATIC_CHECK_SECONDS
            action = "check"
        if action not in {"startup", "check", "install"}:
            raise ValueError("unknown update operation")
        if action == 'install':
            # The user action takes priority over a delayed, optional prefetch.
            self._background_retry.stop()
            self._background_retry_suppressed = True
        # HWND must be captured on the GUI thread, even though apply runs on
        # the worker. The existing native coordinator closes and relaunches us.
        if window is not None:
            from shiboken6 import isValid
            if not isValid(window): window = None
        target = window or QApplication.activeWindow() or self.gate
        self._window = target
        self._retry.stop()
        hwnd = f"{int(target.winId()):016x}"
        self._busy = True
        self._cancel.clear()
        self._publish({**self.state, "state": "DOWNLOADING" if action == "install" else "CHECKING",
                       'download_state': 'PREPARING' if action == 'install' else '',
                       'prepared': False,
                       "message": "正在下载并准备更新…" if action == "install" else "正在检查更新…"})
        self._update_prompt()

        def work():
            # Keep the verified result in this worker; the GUI may not have
            # consumed its queued cache signal before a later core failure.
            cached = None
            try:
                if action == "startup":
                    cached = self.core.get_startup_update_state(self.root, full=False, check_feed=False,
                                                               cancel=self._cancel.is_set)
                    self._send({'cached': cached})
                    result = self.core.get_startup_update_state(self.root, full=False, check_feed=True,
                        cancel=self._cancel.is_set,
                        on_pointer_policy=lambda policy: self._send({'pointer_policy': policy}))
                elif action == "check":
                    result = self.core.check_latest(self.root, cancel=self._cancel.is_set)
                else:
                    self._session.require_running()
                    operation = self.core.repair_installation if self.state.get('shell') == 'repair' else self.core.update_to_latest
                    result = operation(
                        self.root, progress=self._install_progress,
                        cancel=self._cancel.is_set, pid=os.getpid(), hwnd_hex=hwnd)
                    if result.get("coordinator_started") and result.get('app_exit') == 'self':
                        # The coordinator has the signed staged update. New
                        # Apps exit themselves after this acknowledgement; the
                        # updater waits for the PID instead of sending WM_CLOSE.
                        self._send({"handoff": {**result,
                                                "message": "正在安装更新，完成后自动重启…"}})
                        return
                    if result.get('coordinator_started'):
                        # Legacy repair coordinators have no self-exit ACK.
                        # Never interpret process creation alone as permission
                        # to close the App and abandon an unacknowledged task.
                        result = self._coordinator_result(result)
                    if not result.get('coordinator_started') or result.get('state') in ('FAILED_RECOVERABLE', 'PENDING_INSTALL') or result.get('cancelled'):
                        self._clear_login_handoff()
            except Exception as exc:
                from .delivery.per_user_install import maintenance_log
                event = 'startup_update_check' if action == 'startup' else 'in_app_update_deferred'
                maintenance_log(self.root, event, outcome='error',
                                error_type=type(exc).__name__, detail=str(exc))
                if action == 'startup':
                    if cached is not None:
                        result = {**cached, 'feed': 'UNAVAILABLE_RECOVERABLE',
                                  'offline_fallback': True}
                        self._send({'action': action, 'result': result,
                                    'network_error': type(exc).__name__})
                        return
                    result = {'state': 'REPAIR_REQUIRED', 'login_allowed': False,
                              'feed': 'UNAVAILABLE_RECOVERABLE', 'offline_fallback': True,
                              'message': '无法读取本机已验证的更新状态，请修复或重新安装。',
                              'reason': 'verified_cache_unavailable'}
                    self._send({'action': action, 'result': result,
                                'network_error': type(exc).__name__})
                    return
                self._clear_login_handoff()
                from .delivery.per_user_update_core import PENDING
                pending = self.root / PENDING
                if action == 'install' and pending.exists():
                    result = {"state": "PENDING_INSTALL", "destructive_apply": False,
                              "message": "新版已下载好，下次打开软件时完成更新。"}
                else:
                    result = {"state": "FAILED_RECOVERABLE", "message": "更新暂时无法完成，请稍后重试。"}
            self._send({"action": action, "result": result})

        self._pool.submit(work)
        return self.snapshot()

    def _prepare_in_background(self):
        """Queue only after the GUI has received the verified offer.

        Install uses this same single worker, so a click during preparation
        continues its download and then reuses the existing signed ready tree.
        """
        if self._background_preparing or self._closed:
            return
        self._background_preparing = True
        self._background_prepare_attempt += 1
        attempt = self._background_prepare_attempt
        def work():
            from .delivery.startup_integrity import background_io
            from .delivery.per_user_install import _maintenance_lock, maintenance_log
            try:
                # Serialize with cleanup and Updater refresh off the GUI thread.
                while not self._cancel.is_set() and not self._closed:
                    if _maintenance_lock.acquire(timeout=.2):
                        try:
                            with background_io():
                                prepared = self.core.download_latest(self.root,
                                    progress=lambda value: self._send({'background_progress': value}),
                                    cancel=self._cancel.is_set, reuse_prepared=True)
                        finally:
                            _maintenance_lock.release()
                        break
                else:
                    prepared = {'state': 'CANCELLED', 'cancelled': True}
                maintenance_log(self.root, 'background_update_prepare_attempt', attempt=attempt,
                                outcome=prepared.get('code') or prepared.get('state', 'unknown'))
            except Exception as error:
                maintenance_log(self.root, 'background_update_prepare_attempt', attempt=attempt,
                                outcome='error', error_type=type(error).__name__, detail=str(error))
                prepared = {'state': 'FAILED_RECOVERABLE', 'code': 'BACKGROUND_PREPARE_ERROR'}
            self._send({'background_ready': prepared, 'attempt': attempt})
        self._pool.submit(work)

    def _install_progress(self, value):
        # The existing core calls APPLYING synchronously immediately before
        # launching the installed coordinator. Never write during downloading.
        if value.get('state') == 'APPLYING' and self._login_handoff is not None:
            if self._cancel.is_set():
                raise RuntimeError('更新已取消。')
            self._handoff_armed = self._login_handoff.prepare_update_login()
            if self._cancel.is_set():
                self._clear_login_handoff()
                raise RuntimeError('更新已取消。')
        if value.get('state') == 'APPLYING':
            value = {**value, 'message': '正在安装更新，完成后自动重启…'}
        self._send({'progress': value})

    def _clear_login_handoff(self):
        if self._handoff_armed:
            self._login_handoff.clear_update_login()
            self._handoff_armed = False

    def _coordinator_result(self, result):
        """Observe a failed handoff instead of leaving the old UI in APPLYING.

        This result is only presentation; the existing coordinator still owns
        every signature check, process handshake, swap, rollback and relaunch.
        """
        path = Path(result["result_path"]).resolve()
        if path.parent != self.root.resolve():
            raise RuntimeError("更新结果路径无效。")
        deadline = time.monotonic() + 240
        while not self._cancel.wait(.2):
            if path.is_file() and path.stat().st_size < 65536:
                outcome = json.loads(path.read_text(encoding="utf-8"))
                if outcome.get("status") == "FAILED" or outcome.get('state') == 'PENDING_INSTALL':
                    from .delivery.per_user_update_core import PENDING
                    if (self.root / PENDING).is_file():
                        return {"state": "PENDING_INSTALL", "message": "新版已下载好，下次打开软件时完成更新。"}
                    return {"state": "FAILED_RECOVERABLE", "message": "更新暂时无法完成，请稍后重试。"}
                return {**result, **outcome}
            if time.monotonic() >= deadline:
                return {"state": "PENDING_INSTALL", "message": "新版已下载好，下次打开软件时完成更新。"}
        return result

    def _defer_session(self, _manager=None):
        self._session.defer()
        self._cancel.set()
        self._clear_login_handoff()

    def _send(self, value):
        if not self._closed:
            try:
                self._finished.emit(value)
            except RuntimeError:
                pass

    @Slot(object)
    def _receive(self, value):
        if self._closed:
            return
        if 'pointer_policy' in value:
            self._receive_pointer_policy(value['pointer_policy'])
            return
        if 'background_progress' in value or 'background_ready' in value:
            finished = 'background_ready' in value
            progress = value.get('background_ready', value.get('background_progress', {}))
            if finished:
                self._background_preparing = False
                if progress.get('prepared'):
                    self._background_prepare_attempt = 0
                    self._background_retry_suppressed = False
                elif (progress.get('code') == 'UPDATE_LOCK_BUSY'
                      and not self._closed and not self._cancel.is_set()
                      and not self._background_retry_suppressed):
                    retry_index = self._background_prepare_attempt - 1
                    from .delivery.per_user_install import maintenance_log
                    if retry_index < len(BACKGROUND_PREPARE_RETRY_DELAYS):
                        delay = BACKGROUND_PREPARE_RETRY_DELAYS[retry_index]
                        maintenance_log(self.root, 'background_update_prepare_retry_scheduled',
                                        attempt=self._background_prepare_attempt + 1,
                                        delay_seconds=delay)
                        self._background_retry.start(delay * 1000)
                    else:
                        maintenance_log(self.root, 'background_update_prepare_deferred',
                                        attempts=self._background_prepare_attempt,
                                        reason='update_lock_busy')
            phase = progress.get('ui_state') or progress.get('state', 'PREPARING')
            ready = bool(progress.get('prepared') or phase == 'READY_TO_APPLY')
            lock_busy = finished and progress.get('code') == 'UPDATE_LOCK_BUSY'
            message = ('已下载好，点一下就能更新。' if ready else
                       self.state.get('message', '') if lock_busy or (
                           finished and progress.get('state') in {
                               'FAILED_RECOVERABLE', 'FAILED_SECURITY', 'CANCELLED',
                               'DOWNLOAD_PAUSED_OR_INTERRUPTED'
                           }) else
                       progress.get('message') or '后台准备暂时中断，点更新可继续。')
            # Background work does not consume user consent or hide the offer.
            fields = {key: progress[key] for key in ('downloaded_bytes', 'actual_downloaded_bytes',
                'expected_download_bytes', 'downloaded_files', 'total_download_files') if key in progress}
            self._publish({**self.state, **fields, 'download_state': phase,
                           'prepared': ready,
                           'background_downloading': not finished or self._background_retry.isActive(),
                           'message': message})
            self._update_prompt()
            return
        if 'cached' in value:
            result = value['cached']
            self._startup_cached = dict(result)
            from .delivery.per_user_install import maintenance_log
            maintenance_log(self.root, 'startup_login_gate', outcome='verified_cache_ready',
                            elapsed_ms=round((time.monotonic() - (self._startup_started or time.monotonic())) * 1000),
                            state=result.get('state'))
            # Discovery stays in the background. Known signed local restrictions
            # still apply immediately without waiting for a network response.
            self._publish({**self.state, 'cached_login_allowed': bool(result.get('login_allowed'))})
            if not result.get('login_allowed', False):
                # An already verified mandatory floor can be shown immediately;
                # it never needs a fresh network request to remain binding.
                self._publish({**self.state, **result,
                               'mandatory': result.get('state') == 'MANDATORY_UPDATE',
                               'login_check_pending': False, 'login_allowed': False})
                self._login_blocked = True
                getattr(self.gate, 'set_login_available', self.gate.setEnabled)(False)
                self._show_prompt()
            if result.get('state') in ('HEALTHY', 'OPTIONAL_UPDATE', 'MANDATORY_UPDATE') and not self._cleanup_started:
                self._cleanup_started = True
                from .delivery.per_user_install import schedule_program_cleanup
                # Stagger housekeeping from refresh's two-second first try;
                # their workers also serialize actual attempts inside App.
                schedule_program_cleanup(self.root, initial_delay=4)
            return
        if 'handoff' in value:
            self._busy = False
            self._publish({**self.state, **value['handoff'],
                           'state': 'APPLYING', 'message': '正在安装更新，完成后自动重启…'})
            QTimer.singleShot(120, self._finish_handoff)
            # If saving/closing the UI cannot finish, observe the coordinator's
            # bounded wait and display the deferred path instead of retrying.
            self._pool.submit(lambda: self._send({'action': 'install',
                'result': self._coordinator_result(value['handoff'])}))
            return
        if "progress" in value:
            progress = value['progress']
            self._publish({**self.state, **progress,
                           'download_state': progress.get('state', self.state.get('download_state')),
                           'prepared': progress.get('state') == 'READY_TO_APPLY'})
            self._update_prompt()
            return
        self._busy = False
        result = value["result"]
        if value['action'] == 'startup':
            result = {**result, 'mandatory': result.get('state') == 'MANDATORY_UPDATE'}
            status = ('offline_fallback' if result.get('offline_fallback') or
                      result.get('feed') != 'VERIFIED' else 'ready')
            self._apply_startup_result(result, status=status)
        else:
            self._publish({**self.state, **result})
        if value["action"] == "startup":
            if value.get('network_error'):
                from .delivery.per_user_install import maintenance_log
                maintenance_log(self.root, 'startup_update_check', outcome='network_error',
                                error_type=value['network_error'])
        elif value["action"] == "install":
            if result.get('state') in ('PENDING_INSTALL', 'FAILED_RECOVERABLE') or result.get('cancelled'):
                self._clear_login_handoff()
            # User already consented. Retain the .part files and try again later;
            # security failures and explicit cancellation never auto-retry.
            paused = result.get("state") == "DOWNLOAD_PAUSED_OR_INTERRUPTED" and not result.get("cancelled")
            retry_check = self._continuing_download and result.get("state") == "FAILED_RECOVERABLE" and result.get("code") == "UPDATE_CHECK_NETWORK_UNAVAILABLE"
            self._continuing_download = bool(paused or retry_check)
            if retry_check:
                done, total = self.state.get("downloaded_bytes", 0), self.state.get("expected_download_bytes", 0)
                self._publish({**self.state, "state": "DOWNLOAD_PAUSED_OR_INTERRUPTED",
                               "message": f"网络不稳，已下载 {done}/{total} 字节，稍后会自动继续。"})
            if self._continuing_download: self._retry.start()
        self._update_prompt()
        if value['action'] in ('startup', 'check') and result.get('state') in (
                'OPTIONAL_UPDATE', 'MANDATORY_UPDATE', 'UPDATE_AVAILABLE'):
            if not self._background_preparing and not self._background_retry.isActive():
                self._background_prepare_attempt = 0
                self._background_retry_suppressed = False
            self._prepare_in_background()

    def _finish_handoff(self):
        """Close our UI only after the updater process has been launched."""
        if self._closed:
            return
        if self._prompt is not None:
            self._prompt.hide()
        shell = self._login_handoff
        try:
            if shell is not None and hasattr(shell, 'prepare_update_shutdown'):
                shell.prepare_update_shutdown()
            else:
                QApplication.closeAllWindows()
            self._exit_for_update()
        except Exception as error:
            from .delivery.per_user_install import maintenance_log
            maintenance_log(self.root, 'app_self_exit_deferred', detail=str(error))
            self._busy = False
            self._clear_login_handoff()
            self._publish({**self.state, 'state': 'PENDING_INSTALL',
                           'message': '新版已下载好，下次打开软件时完成更新。'})
            self._show_prompt()

    def _show_prompt(self):
        from .update_dialog import UpdateDialog
        target = self._prompt_parent()
        if target is not self.gate:
            # The main React window already presents update.status. Do not
            # place a second native modal over its existing UpdateNotice.
            if self._prompt is not None: self._prompt.hide()
            return
        if self._prompt is None:
            self._prompt = UpdateDialog(target)
            self._prompt.install_requested.connect(lambda: self.run('install', self._prompt_parent()))
            self._prompt.defer_requested.connect(lambda: self.run('defer'))
            self._prompt.close_requested.connect(self._close_for_pending)
        elif self._prompt.parentWidget() is not target:
            self._prompt.setParent(target, self._prompt.windowFlags())
        self._update_prompt()
        self._prompt.show(); self._prompt.raise_(); self._prompt.activateWindow()

    def _close_for_pending(self):
        """Let the user close the App so the signed pending update can apply."""
        if self._prompt is not None:
            self._prompt.hide()
        shell = self._login_handoff
        if shell is not None and hasattr(shell, 'prepare_update_shutdown'):
            shell.prepare_update_shutdown()
        self._exit_for_update()

    @staticmethod
    def _exit_for_update():
        # The desktop lifetime guard deliberately rejects unrequested Qt Quit
        # events. Register the agreed update handoff before asking Qt to exit.
        app = QApplication.instance()
        diagnostics = getattr(app, '_mentorflow_diagnostics', None)
        if diagnostics is not None:
            diagnostics.request_exit('UPDATE_HANDOFF')
        else:
            app.quit()

    def _update_prompt(self):
        if self._prompt is not None:
            status = self.snapshot()
            status['hide_later'] = bool(status.get('mandatory'))
            if status.get('state') in ('OPTIONAL_UPDATE', 'UPDATE_AVAILABLE'):
                guidance = ('有新版本可用，可以现在更新，或点“稍后”继续登录。'
                    if self._prompt_parent() is self.gate else
                    '有新版本可用，可以现在更新，或稍后在 设置 → 关于 更新。')
                # Preserve live download/preparation messages, but classify a
                # fresh offer for the window visible NOW (login may have moved).
                if not status.get('download_state') and not status.get('deferred_update'):
                    status['message'] = guidance
            self._prompt.update_status(status)

    def _prompt_parent(self):
        from shiboken6 import isValid
        home = getattr(self._login_handoff, 'home', None)
        if home is not None and isValid(home) and home.isVisible(): return home
        if self.gate.isVisible(): return self.gate
        window = self._window
        if window is not None and isValid(window) and window.isVisible(): return window
        return self.gate

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._prompt is not None: self._prompt.hide()
        self._retry.stop()
        self._background_retry.stop()
        self._cancel.set()
        self._session.close()
        self._pool.shutdown(wait=False, cancel_futures=True)
