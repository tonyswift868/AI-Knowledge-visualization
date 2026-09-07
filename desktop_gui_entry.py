"""Windowed protected entry: one LoginGate from first paint through login.

The installer fully verifies the package. Daily UAT launch checks the protected
location before Qt import, then verifies the bounded GUI closure after paint.
Full inventory verification remains an install/manual operation, not daily UX.
"""
import json
import os
from pathlib import Path
import sys
import threading
import time


def main(argv=None):
    started = time.perf_counter()
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--install-root', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--tree-sha', required=True)
    parser.add_argument('--update-attempt')
    parser.add_argument('--update-nonce')
    args = parser.parse_args(argv)
    if bool(args.update_attempt) != bool(args.update_nonce):
        raise ValueError('incomplete update health correlation')
    if args.update_attempt:
        import re
        if re.fullmatch('[0-9a-f]{32}',args.update_attempt) is None or re.fullmatch('[0-9a-f]{64}',args.update_nonce) is None:
            raise ValueError('invalid update health correlation')
    from scripts.gui_bootstrap_trust import verify_gui_bootstrap, protected_gui_location
    identities = dict(manifest_sha256=args.manifest_sha256, source_sha=args.source_sha, tree_sha=args.tree_sha)
    package = protected_gui_location(args.install_root, executable=sys.executable)
    args.install_root = package.parent
    from desktop.startup_connection import prepare_environment, ConnectionPreparation
    env, certificate = prepare_environment(args.install_root)
    os.environ.clear()
    os.environ.update(env)
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = str(package / '_internal/PySide6/plugins/platforms')
    os.chdir(package)
    events, event_lock = [], threading.Lock()
    log_path = Path(env['TEMP']) / ('gui-launch-' + str(os.getpid()) + '.json')

    def record(name):
        # Fixed event labels only. No account fields, URLs, credentials or
        # provider documents are recorded. Logging cannot unlock the barrier.
        with event_lock:
            if len(events) >= 100:
                return
            events.append({'event':name, 'ms':round((time.perf_counter()-started)*1000, 3)})
            try:
                log_path.write_text(json.dumps({'pid':os.getpid(), 'events':events}), encoding='utf-8')
            except OSError:
                pass

    record('GUI_BOOTSTRAP_PREFLIGHT_END')
    from PySide6.QtCore import Qt, QCoreApplication
    from PySide6.QtWidgets import QApplication
    from desktop.login_shell import LoginGateWindow, LoginGateState
    from desktop.startup_controller import StartupController
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QCoreApplication.setLibraryPaths([str(package / '_internal/PySide6/plugins')])
    app = QApplication([sys.executable])
    app.setApplicationName('MentorFlow')
    app.setOrganizationName('MentorFlow')
    from scripts.install_product import configured
    if configured().uat:
        app.setApplicationName(configured().app_id)
        app.setOrganizationName(configured().app_id)
    gate = LoginGateWindow()
    gate.set_state(LoginGateState.STARTING)
    record('LOGIN_GATE_CREATED')
    gate.first_paint_completed.connect(lambda: record('LOGIN_GATE_PAINT_COMPLETED'))
    product = []

    def verify():
        verify_gui_bootstrap(args.install_root, executable=sys.executable, **identities)
        return {'verification': 'PASS', 'scope': 'gui-bootstrap-closure'}

    def attach(window):
        from desktop.enterprise_oidc import DesktopOidcConfig
        if not DesktopOidcConfig.is_configured():
            raise RuntimeError('Secure login configuration required')
        from desktop.main import MentorFlowApplication
        application = MentorFlowApplication(login_gate=window)
        if application._startup_failed or application._startup_cancelled:
            raise RuntimeError('Account startup unavailable')
        application._startup_record = record
        product.append(application)
        application._show_login_gate()

    controller = StartupController(gate, verify=verify,
        connection_factory=lambda: ConnectionPreparation(env, certificate), attach=attach, record=record)
    from desktop.gui_update_runtime import attach_updates
    attach_updates(app,gate,controller,args.install_root,attempt_id=args.update_attempt,nonce=args.update_nonce)
    app.aboutToQuit.connect(controller.close)
    gate.closed.connect(lambda:app.quit() if not product else None)
    gate.show()
    record('LOGIN_GATE_SHOW_REQUESTED')
    code = app.exec()
    controller.close()
    record('GUI_PROCESS_EXIT')
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        # Qt must not load after a failed preflight. Only the trusted OS repair
        # message is safe here; normal startup errors stay in the LoginGate.
        from scripts.windows_installed_launch import show_repair
        show_repair(type(error).__name__)
        raise SystemExit(3)
