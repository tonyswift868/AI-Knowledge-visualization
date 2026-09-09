"""Thin port for the frozen core; no manifest parser, downloader or transaction.

07's final high-level implementation binds here without changing panel/controller.
The old core's prepared result maps to PREPARING, never another user confirmation.
"""
from typing import Protocol

from .update_session import SessionEnding

CORE_STATES = {
    'IDLE': 'CHECKING', 'CHECKING': 'CHECKING',
    'UP_TO_DATE': 'UP_TO_DATE', 'CURRENT_HEALTHY': 'UP_TO_DATE',
    'HEALTHY': 'UP_TO_DATE',
    'UPDATE_AVAILABLE': 'OPTIONAL_UPDATE', 'OPTIONAL_UPDATE': 'OPTIONAL_UPDATE',
    'MANDATORY_UPDATE': 'MANDATORY_UPDATE', 'REPAIR_REQUIRED': 'REPAIR_REQUIRED',
    'DOWNLOADING': 'DOWNLOADING', 'PREPARING': 'PREPARING',
    'READY_TO_APPLY': 'PREPARING', 'UPDATE_DOWNLOADED': 'PREPARING',
    'APPLYING': 'PREPARING', 'RESTART_REQUIRED': 'PREPARING',
    'DEFERRED_TO_NEXT_LAUNCH': 'DEFERRED_TO_NEXT_LAUNCH',
    'PENDING_INSTALL': 'DEFERRED_TO_NEXT_LAUNCH',
}
DEFERRED = ('DEFERRED_TO_NEXT_LAUNCH', 'PENDING_INSTALL')


class UpdatePort(Protocol):
    @property
    def snapshot(self) -> dict: ...
    def check_latest(self) -> dict: ...
    def download_latest(self, progress, cancel) -> dict: ...
    def update_to_latest(self, progress=None, cancel=None, before_apply=None) -> dict: ...
    def apply_and_relaunch(self) -> dict: ...
    def repair_and_check(self, progress, cancel) -> dict: ...
    def close(self) -> None: ...


class CoreUpdatePort:
    def __init__(self, service, *, window_id=lambda: None, can_apply=lambda: True):
        self.service = service
        self.window_id, self.can_apply = window_id, can_apply
        self._outcome = ''
        self._downloading = False
        self._original_bridge = service.apply_bridge if service else None
        self._closed = False
        self._core_snapshot = {}
        self._startup_state = {}
        self._repair_dispatched = False
        from scripts.per_user_install import is_install
        install_root = getattr(service, 'install_root', None)
        self._per_user = bool(install_root is not None and is_install(install_root))
        self._session = None
        if self._per_user:
            from .update_session import UpdateSession, UnavailableSession
            try:
                self._session = UpdateSession()
            except OSError:
                self._session = UnavailableSession()

    @property
    def snapshot(self):
        raw = self._core_snapshot or self._startup_state or (self.service.snapshot if self.service else {})
        code = raw.get('state', 'IDLE')
        if code not in DEFERRED:
            code = self._outcome or code
        state = CORE_STATES.get(code, 'RECOVERABLE_ERROR') if raw else 'RECOVERABLE_ERROR'
        if code == 'PREPARING' and self._downloading:
            state = 'DOWNLOADING'  # Frozen core's pre-download preparation.
        error_kind = ('validation' if code == 'FAILED_SECURITY' else
            'cancelled' if code == 'USER_CANCELLED' else 'network')
        # Frozen 7235a440 already exposes this read-only scheduling seam:
        # cancellation may return before the socket worker releases ownership.
        # Keep that legacy binding here, never in the panel/controller.
        transport = getattr(getattr(self.service, 'cache', None), 'transport', None)
        # Only presentation values cross the port; no release envelope/cache/paths.
        return dict(state=state, current=getattr(self.service, 'current', ''),
            latest=raw.get('latest', ''), notes=raw.get('release_notes', ''),
            downloaded=raw.get('actual_downloaded_bytes', 0), total=raw.get('expected_download_bytes', 0),
            has_update=bool(raw.get('update', code == 'UPDATE_AVAILABLE')),
            error_kind=error_kind, busy=bool(getattr(transport, 'busy', False)),
            available=self.service is not None)

    def set_startup_state(self, value):
        from scripts.per_user_update_core import STARTUP_STATES
        if not isinstance(value, dict) or value.get('state') not in STARTUP_STATES:
            raise ValueError('verified startup state required')
        self._startup_state = dict(value)
        self._core_snapshot = {}
        self._outcome = ''

    def _result(self, value):
        if isinstance(value, dict):
            if self._per_user:
                self._core_snapshot = dict(value)
            codes = (value.get('ui_state'), value.get('state'), value.get('status'))
            code = next((code for code in codes if code in DEFERRED), next((code for code in codes if code), None))
            if self._per_user or code in DEFERRED or code in ('FAILED_SECURITY', 'FAILED_RECOVERABLE',
                'MANDATORY_UPDATE', 'REPAIR_REQUIRED', 'OPTIONAL_UPDATE', 'UP_TO_DATE', 'CURRENT_HEALTHY', 'PREPARING'):
                self._outcome = code
        return self.snapshot

    def _call(self, method):
        self._outcome = ''
        self._core_snapshot = {}
        if not self.service or self._closed:
            return self.snapshot
        try:
            return self._result(method())
        except SessionEnding:
            # A local/session guard cannot promise persisted next-launch work.
            self._outcome = 'FAILED_RECOVERABLE'
        except Exception as error:
            from .update_protocol import SecurityError
            self._outcome = 'FAILED_SECURITY' if isinstance(error, SecurityError) else 'FAILED_RECOVERABLE'
        return self.snapshot

    def check_latest(self):
        if self._per_user:
            from scripts.per_user_update_core import get_startup_update_state
            def check():
                policy = get_startup_update_state(self.service.install_root, service=self.service)
                self.set_startup_state(policy)
                return {**self.service.snapshot, **policy}
            return self._call(check)
        return self._call(lambda: self.service.check(manual=True))

    def repair_and_check(self, progress, cancel):
        if self._repair_dispatched:
            return self.snapshot
        if self._per_user:
            from scripts.per_user_update_core import repair_installation
            import os
            def repair():
                self._session.require_running()
                if not self.can_apply() or self._closed:
                    raise SessionEnding('session or window unavailable')
                if cancel.is_set():
                    return {'state': 'USER_CANCELLED'}
                def report(value):
                    self._core_snapshot = dict(value)
                    progress(self.snapshot)
                value = repair_installation(self.service.install_root, progress=report,
                    cancel=lambda: cancel.is_set() or self._closed or not self.can_apply(),
                    service=self.service, pid=os.getpid(), hwnd_hex=self.window_id())
                self._repair_dispatched = (value.get('state') == 'APPLYING' and
                                           value.get('coordinator_started') is True)
                return value
            return self._call(repair)
        # The UI may request repair, never implement it or fall back to Setup.
        # A core without this capability fails safely and keeps login blocked.
        operation = getattr(self.service, 'repair_and_check', None)
        if not callable(operation):
            self._outcome = 'FAILED_RECOVERABLE'
            return self.snapshot
        result = self._call(lambda: operation(progress=progress, cancel=cancel.is_set))
        if cancel.is_set():
            self._outcome = 'USER_CANCELLED'
            return self.snapshot
        return result

    def download_latest(self, progress, cancel):
        def download():
            if self._per_user:
                from scripts.per_user_update_core import download_latest
                def report(value):
                    self._core_snapshot = dict(value)
                    progress(self.snapshot)
                return download_latest(self.service.install_root,
                    progress=report, cancel=cancel.is_set,
                    service=self.service)
            # Cancellation is observed by core. This monitor never reads bytes
            # or interprets assets; it only forwards progress/cancel callbacks.
            import threading
            stop = threading.Event()
            def monitor():
                while not stop.wait(.025):
                    if cancel.is_set():
                        self.service.cancel_download()
                    progress(self.snapshot)
            watcher = threading.Thread(target=monitor, daemon=True, name='mentorflow-update-progress')
            watcher.start()
            try:
                if cancel.is_set():
                    return {'state': 'DOWNLOAD_PAUSED_OR_INTERRUPTED'}
                result = self.service.download()
                return result
            finally:
                stop.set()
                watcher.join(timeout=1)
        self._downloading = True
        try:
            result = self._call(download)
        finally:
            self._downloading = False
        if cancel.is_set():
            self._outcome = 'USER_CANCELLED'
            result = self.snapshot
        progress(result)
        return result

    def update_to_latest(self, progress=None, cancel=None, before_apply=None):
        import threading
        progress = progress or (lambda _: None)
        cancel = cancel if cancel is not None else threading.Event()
        before_apply = before_apply or (lambda: None)
        result = self.download_latest(progress, cancel)
        if result['state'] == 'PREPARING' and not cancel.is_set():
            before_apply()
            if not cancel.is_set():
                return self.apply_and_relaunch()
        return result

    def apply_and_relaunch(self):
        if self._repair_dispatched:
            # repair_installation already handed off to the native coordinator.
            # 03's prepared-result chain must not start a second transaction.
            return self.snapshot
        def apply():
            if self._per_user:
                from scripts.per_user_update_core import apply_and_relaunch
                reason = 'normal'
                try:
                    self._session.require_running()
                    if not self.can_apply() or self._closed:
                        raise SessionEnding('session or window unavailable')
                except SessionEnding:
                    reason = 'unsafe'
                return apply_and_relaunch(self.service.install_root,
                    hwnd_hex=self.window_id(), reason=reason, service=self.service)
            if not self.can_apply() or self._closed:
                raise SessionEnding('Update deferred until next normal launch')
            def bridge(root, stage, envelope):
                if not self.can_apply() or self._closed:
                    raise SessionEnding('Update deferred until next normal launch')
                if self._original_bridge is not None:
                    return self._original_bridge(root, stage, envelope)
                from .update_bridge import handoff
                return handoff(root, stage, envelope, hwnd_hex=self.window_id())
            self.service.apply_bridge = bridge
            return self.service.apply_and_restart()
        return self._call(apply)

    def defer_session(self):
        if self._session is not None:
            self._session.defer()

    def close(self):
        self._closed = True
        if self.service:
            self.service.close()
        if self._session is not None:
            self._session.close()


def configured_update_service(*, install_root=None):
    """E108 uses 07's verified build config; no UI interpretation of a feed.

    The older protected-install service remains for non-E108 installations only.
    Never fall back to it if an existing per-user installation fails admission.
    """
    import sys
    from pathlib import Path
    from scripts.per_user_install import is_install, resolve_launch_root, target
    image = Path(sys.executable).absolute()
    root = target(install_root) if install_root is not None else (
        resolve_launch_root(image) if image.name.casefold() == 'mentorflow.exe' else image.parent.parent)
    if is_install(root):
        from scripts.per_user_update_core import one_click_service
        return one_click_service(root)
    from .update_service import configured_mature_service
    return configured_mature_service(install_root=root) if install_root is not None else configured_mature_service()
