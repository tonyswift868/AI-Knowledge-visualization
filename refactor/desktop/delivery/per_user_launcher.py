"""Stable shortcut adapter for the existing signed consumer lifecycle."""
from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_update_core as core
from refactor.desktop.delivery.update_protocol import strict_json
from refactor.desktop.delivery.update_protocol import RecoverableError
from refactor.desktop.delivery.update_protocol import SecurityError


def _recovery_health(root):
    """A writable recovery hint is usable only after native peer validation."""
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
    release = install.installed(root)
    doc = strict_json(install.read(root / 'last-recovery.json'))
    journal = strict_json(install.read(root / install.JOURNAL))
    proof = doc.get('health', {})
    if (doc.get('attempt') != journal.get('attempt') or
            journal.get('phase') != 'ROLLED_BACK' or
            doc.get('version') != release.version or
            proof.get('version') != release.version or proof.get('ready') is not True):
        raise SecurityError('recovery health does not match the current transaction')
    with ProcessPeer(proof['pid']) as peer:
        peer.require(executable=root / 'App/MentorFlow.exe')
        peer.visible_responsive_window(proof['hwnd_hex'])
    return proof


class _NeedsRepair(Exception):
    pass


class _AppBusy(RecoverableError):
    """The previous App is still active and needs a visible user-facing result."""
    pass


def _ensure_app_stopped(root, mark):
    try:
        install.require_app_stopped(root)
        return
    except RecoverableError as error:
        from refactor.desktop.delivery.windows_process_peer import ProcessPeer
        image=install.app_path(root) / 'MentorFlow.exe'
        pid=None; visible=False
        try:
            for candidate in install._matching_app_pids(image):
                pid=candidate
                with ProcessPeer(candidate) as peer:
                    visible=bool(peer.visible_windows())
                    if visible: peer.foreground()
                if visible: break
        except (OSError, RuntimeError, ValueError):
            pass
        if pid is not None and install.app_exit_confirmed(root, pid) and not visible:
            install.terminate_confirmed_app(root, pid)
            mark('stale_app_terminated')
            return
        from refactor.desktop.delivery.startup_window import set_startup_text
        message=('MentorFlow 还在退出，请稍候再试。' if visible else
                 '上一次的 MentorFlow 还没完全退出，请关闭已有窗口后重试。')
        set_startup_text(message, root=root)
        install.maintenance_log(root, 'launch_blocked_app_running', pid=pid or 0,
                                visible=int(visible), error_type=type(error).__name__)
        raise _AppBusy(message) from error


def _log_pending_install_failure(root, pending, error):
    """Record a deferred-install failure without hiding the recovery path."""
    from refactor.desktop.delivery.update_protocol import SecurityError
    if isinstance(error, SecurityError) and 'outside the approved cache' in str(error):
        code = 'PENDING_STAGE_OUTSIDE_CACHE'
    elif isinstance(error, RecoverableError):
        code = 'PENDING_INSTALL_DEFERRED'
    else:
        code = 'PENDING_INSTALL_FAILED'
    install.maintenance_log(root, 'pending_install_failed', target=pending.get('target', ''),
                            error_type=type(error).__name__, error_code=code, detail=str(error))


def _launch_checked(root, mark=lambda phase: None):
    from contextlib import ExitStack
    with ExitStack() as locks:
        try:
            locks.enter_context(install.launch_coordination(root))
        except RecoverableError:
            install.maintenance_log(root, 'launch_deferred_update_in_progress')
            return dict(status='UPDATE_IN_PROGRESS', message='正在更新 MentorFlow，请稍候…')
        return _launch_serialized(root, mark)


def _launch_serialized(root, mark):
    from refactor.desktop.delivery.update_download import InstallUpdateLock
    root = install.target(root)
    if not install.is_install(root):
        try: core.bootstrap_legacy_identity(root)
        except (OSError, RuntimeError, ValueError, ImportError):
            raise _NeedsRepair from None
    with InstallUpdateLock(root):
        _ensure_app_stopped(root, mark)
        if core.unsafe_teardown('next_launch'):
            raise RecoverableError('session ending: launch and update deferred')
        # Recovery shares the core's writer mutex. Release it before calling
        # apply, which acquires the same non-reentrant mutex itself.
        install.recover(root)
    mark('recovered')
    # A crash may leave App temporarily missing between the owned swaps.
    # Restore the verified previous generation BEFORE classifying its GUI.
    state = core.get_startup_update_state(root, check_feed=False, full=False)
    mark('startup_verified')
    if state['state'] in ('REPAIR_REQUIRED', 'FATAL_UNRECOVERABLE'):
        raise _NeedsRepair
    pending = None
    try:
        core.discard_stale_pending(root)
        if (root / core.PENDING).exists():
            pending = core.read_pending(root)
    except (OSError, RuntimeError, ValueError) as error:
        _log_pending_install_failure(root, {}, error)
    if pending is not None:
        retry_deferred_busy = (pending['state'] == 'PREVIOUS_HEALTHY' and
                               pending.get('deferred_reason') == 'app_directory_busy')
        retry_pending = pending['state'] in ('UPDATE_DOWNLOADED', 'PENDING_INSTALL', 'APPLYING')
        if retry_pending or retry_deferred_busy:
            if not core.authorization_matches(pending):
                # Legacy pending receipts and background preparation are hints,
                # never user consent. Keep the signed current App launchable.
                install.maintenance_log(root, 'pending_update_not_authorized',
                                        target=pending.get('target', ''),
                                        reason='missing_or_mismatched_user_authorization')
            else:
                from refactor.desktop.delivery.startup_window import set_startup_text
                set_startup_text('正在更新 MentorFlow…', root=root)
                try:
                    # A successful apply already launches/health-checks TARGET.
                    result = core.resume_pending_on_normal_launch(root, reason='next_launch')
                    if result.get('state') == 'CURRENT_HEALTHY':
                        return result
                except (OSError, RuntimeError, ValueError) as error:
                    _log_pending_install_failure(root, pending, error)
                    # Offline/withdrawn/invalid pending content cannot block the
                    # signed current App. A rolled-back App is already running;
                    # validate its live identity instead of launching a duplicate.
                    try:
                        rolled_back = core.read_pending(root)['state'] == 'PREVIOUS_HEALTHY'
                    except (OSError, RuntimeError, ValueError) as pending_error:
                        _log_pending_install_failure(root, pending, pending_error)
                        rolled_back = False
                    if rolled_back:
                        latest = core.read_pending(root)
                        try:
                            health = _recovery_health(root)
                        except (OSError, RuntimeError, ValueError) as health_error:
                            # The old App may have exited after rollback or while
                            # the next-start retry was offline. A stale proof is
                            # not damage evidence; fall through to the normal
                            # signed old-App launch, which still prevents duplicates.
                            install.maintenance_log(root, 'recovery_app_not_running',
                                                    error_type=type(health_error).__name__)
                        else:
                            return dict(status='PREVIOUS_HEALTHY', health=health,
                                        deferred_update=latest.get('deferred_reason') == 'app_directory_busy',
                                        deferred_target=latest.get('target', ''),
                                        deferred_reason=latest.get('deferred_reason', ''),
                                        deferred_message=core.PENDING_MESSAGE if latest.get('deferred_reason') == 'app_directory_busy' else '')
    with InstallUpdateLock(root):
        if core.unsafe_teardown('next_launch'):
            raise RecoverableError('session ending: launch and update deferred')
        _ensure_app_stopped(root, mark)
        mark('app_launch_begin')
        result = dict(status='HEALTHY', health=install.launch_health(root, install.installed(root)))
        mark('app_visible_healthy')
        return result


def launch(root):
    """Paint before checking; keep the Qt loop responsive during signed launch.

    The existing native health handshake confirms the App's visible window.
    No timer, cache receipt or splash window is accepted as App health.
    """
    from refactor.desktop.delivery.startup_window import has_native_splash
    if has_native_splash():
        return _launch_native(root)
    import json
    from contextvars import copy_context
    import threading
    import time
    from datetime import datetime, timezone
    from PySide6.QtCore import QEventLoop, QTimer, Qt
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout, QProgressBar
    from refactor.desktop.dialog_theme import apply

    started = time.perf_counter()
    timings = []
    def mark(phase):
        timings.append(dict(phase=phase, seconds=round(time.perf_counter() - started, 6)))

    app = QApplication.instance() or QApplication([])
    previous_quit = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    splash = QDialog(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
    splash.setWindowTitle('MentorFlow')
    splash.setFixedSize(420, 220)
    layout = QVBoxLayout(splash); layout.setContentsMargins(32, 28, 32, 28)
    heading = QLabel('MentorFlow'); heading.setObjectName('pageHeading')
    from refactor.desktop.delivery.startup_window import current_startup_text
    startup_text = QLabel(current_startup_text())
    layout.addWidget(heading); layout.addWidget(startup_text)
    progress = QProgressBar(); progress.setRange(0, 0); progress.setTextVisible(False)
    layout.addWidget(progress)
    apply(splash, 'sakura')
    splash.reject = lambda: None  # Esc cannot hide the only startup feedback.
    from refactor.desktop.delivery.startup_window import foreground, window_state, log_phase, close_native_splash
    foreground(splash)
    app.processEvents()
    close_native_splash()
    log_phase(root, 'startup_window_visible', **window_state(splash))
    mark('startup_window_visible')

    outcome = {}
    done = threading.Event()
    def work():
        try:
            from refactor.desktop.delivery.per_user_runtime_session import runtime_session
            # First visible window precedes the hidden WM_QUERYENDSESSION listener.
            with runtime_session():
                outcome['result'] = _launch_checked(root, mark)
        except BaseException as error: outcome['error'] = error
        finally: done.set()
    loop = QEventLoop()
    timer = QTimer(); timer.setInterval(20)
    def poll_startup():
        startup_text.setText(current_startup_text())
        if done.is_set(): loop.quit()
    timer.timeout.connect(poll_startup)
    # Keep the caller's runtime_session guard in the launch worker.
    context = copy_context()
    worker = threading.Thread(target=lambda: context.run(work), name='mf-startup', daemon=True)
    try:
        worker.start(); timer.start(); loop.exec()
    finally:
        timer.stop(); splash.hide(); splash.deleteLater()
        app.setQuitOnLastWindowClosed(previous_quit)
        try:
            path = install.target(root) / 'Data/logs/launcher-startup.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),
                    phases=timings, error=type(outcome['error']).__name__ if 'error' in outcome else None)) + '\n')
        except (OSError, RuntimeError, ValueError):
            pass  # Diagnostics never change the signed launch decision.
    if isinstance(outcome.get('error'), _NeedsRepair):
        return repair_update_shell(root)
    if isinstance(outcome.get('error'), _AppBusy):
        return dict(status='APP_BUSY', message=str(outcome['error']))
    if 'error' in outcome: raise outcome['error']
    return outcome['result']


def _launch_native(root):
    """Normal shortcut startup needs no Qt in its launcher process."""
    import threading
    import time
    from contextvars import copy_context
    from refactor.desktop.delivery.startup_window import pump_native_messages, close_native_splash, log_phase
    from refactor.desktop.delivery.per_user_runtime_session import runtime_session
    done = threading.Event(); outcome = {}; timings = []
    started = time.perf_counter()
    def mark(phase):
        timings.append(dict(phase=phase, seconds=round(time.perf_counter()-started, 6)))
    def work():
        try:
            with runtime_session(): outcome['result'] = _launch_checked(root, mark)
        except BaseException as error: outcome['error'] = error
        finally: done.set()
    context = copy_context()
    threading.Thread(target=lambda: context.run(work), daemon=True, name='mf-startup').start()
    try:
        while not done.is_set():
            pump_native_messages(); done.wait(.01)
    finally:
        close_native_splash()
        log_phase(root, 'launcher_complete', phases=timings,
                  error=type(outcome['error']).__name__ if 'error' in outcome else None)
        if isinstance(outcome.get('error'), _NeedsRepair): return repair_update_shell(root)
        if isinstance(outcome.get('error'), _AppBusy):
            from refactor.desktop.delivery.startup_window import set_startup_text
            set_startup_text(str(outcome['error']), root=root)
            time.sleep(2)
            return dict(status='APP_BUSY', message=str(outcome['error']))
    if 'error' in outcome: raise outcome['error']
    return outcome['result']


def repair_update_shell(root):
    """Independent branded repair UI; the signed core still owns recovery."""
    import sys
    if not getattr(sys, 'frozen', False):
        raise RecoverableError('repair shell requires the packaged runtime')
    from refactor.desktop.delivery.repair_shell import run
    return run(install.target(root), core, install)
