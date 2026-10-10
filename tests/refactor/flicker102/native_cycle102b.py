"""Real Windows/WebEngine cycle; also frozen as the offline packaged UAT entry.

Only capabilities and saved-account lookup are faked. The window, local page,
prewarm timer, click connections, native first-frame cover and diagnostics are
the product implementations. No server, credentials or user data are used.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import subprocess
import time
from types import SimpleNamespace as NS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--theme', choices=('sakura', 'aurora'), default='sakura')
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--without-anchor', action='store_true')
    parser.add_argument('--hold', type=int, default=0)
    parser.add_argument('--arm', action='store_true')
    parser.add_argument('--record-region')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not getattr(sys, 'frozen', False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    # Match account_shell_main.main before QApplication construction.
    os.environ.setdefault('QSG_RHI_BACKEND', 'opengl')
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = '--disable-gpu-shader-disk-cache'
    from PySide6.QtCore import QEvent, QObject, QSettings, QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    if args.record_region:
        # Independent Qt event loop: recording must continue while the tested
        # UI is attaching Chromium. Keep pixmaps in RAM and encode afterwards.
        x, y, width, height = map(int, args.record_region.split(','))
        app = QApplication([])
        frames = []
        recording = time.perf_counter()
        (output/'recorder-ready.json').write_text(json.dumps(recording), encoding='utf-8')
        def sample():
            now = time.perf_counter()
            frames.append((now, app.primaryScreen().grabWindow(0, x, y, width, height)))
            if now - recording >= 2.1:
                timer.stop()
                metadata = []
                for index, (at, pixmap) in enumerate(frames):
                    name = f'frame-{index:03d}.png'
                    pixmap.save(str(output/name))
                    metadata.append(dict(at=at, file=name, null=pixmap.isNull()))
                (output/'frames.json').write_text(json.dumps(metadata), encoding='utf-8')
                app.quit()
        timer = QTimer(); timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.setInterval(40); timer.timeout.connect(sample); timer.start()
        app.exec()
        return 0
    from refactor.runtime_logging import configure
    from refactor.desktop.process_diagnostics import ProcessDiagnostics
    from refactor.desktop.account_shell_main import AccountShell
    from refactor.desktop.login_shell import LoginGateWindow
    from refactor.desktop.account_identity import AccountPreferenceStore
    from refactor.desktop.login_session import LoginSession
    from refactor.desktop.shell_paths import ShellRuntime
    from refactor.desktop.lifecycle_pane import assets

    configure(output)
    app = QApplication([])
    diagnostics = ProcessDiagnostics(output)
    diagnostics.attach(app)
    gate = LoginGateWindow()
    gate.set_loading_appearance(args.theme)
    if args.without_anchor:
        gate._composition_anchor.setParent(None)
        gate._composition_anchor.deleteLater()
    session = LoginSession(AccountPreferenceStore(QSettings(
        str(output / 'account.ini'), QSettings.IniFormat)), credentials=NS(), passwords=NS())
    session.remembered = lambda: (None, False)
    session.take_update_login = lambda: (False, None, None)
    runtime = ShellRuntime(output, output / 'cache', assets(), output / 'unused.pem')
    shell = AccountShell(app, runtime, login_session=session, gate_window=gate,
        capability_fetcher=lambda: {'register': True, 'forgot_password': True},
        prepare_account_surface=True)
    started = time.perf_counter()
    result = dict(frozen=bool(getattr(sys, 'frozen', False)), theme=args.theme,
                  events=[], actions=[], frames=[], timings={}, failure=None)
    stage = 'warm_register'
    click_at = None
    capture_at = None
    finished = False
    js_pending = False
    latest_ready = False
    recorder = None

    def elapsed():
        return round(time.perf_counter() - started, 4)

    class Watch(QObject):
        def eventFilter(self, obj, event):
            if obj is gate and event.type() in (QEvent.Type.Show, QEvent.Type.Hide,
                                               QEvent.Type.PlatformSurface, QEvent.Type.WinIdChange):
                handle = gate.windowHandle()
                result['events'].append(dict(t=elapsed(), event=event.type().name,
                    surface=handle.surfaceType().name if handle else None))
            return False

    watch = Watch()
    gate.installEventFilter(watch)
    gate.showNormal()
    gate.raise_()
    gate.activateWindow()
    shell.show()
    result['timings']['gate_shown'] = elapsed()

    def finish(failure=None):
        nonlocal finished
        if finished:
            return
        finished = True
        poll.stop()
        capture.stop()
        result['failure'] = failure
        if recorder is not None:
            recorder.wait(timeout=15)
            result['frames'] = json.loads((output/'frames.json').read_text(encoding='utf-8'))
            for frame in result['frames']:
                frame['relative'] = round(frame['at'] - result['register_click_clock'], 4)
        result['completed_at'] = elapsed()
        result['events_before_exit'] = list(result['events'])
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        QTimer.singleShot(args.hold * 1000, lambda: diagnostics.request_exit('TEST_COMPLETE'))

    def record_frame():
        if capture_at is not None and not finished:
            identifier = int(gate.winId())
            result.setdefault('visibility', []).append(dict(t=elapsed(), hwnd=identifier,
                visible=bool(ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(identifier)))))

    def observed(value):
        nonlocal js_pending, latest_ready
        js_pending = False
        latest_ready = bool(value)

    def tick():
        nonlocal stage, click_at, capture_at, js_pending, latest_ready, recorder
        now = time.perf_counter()
        if now - started > (120 if args.arm else 40):
            finish('timeout at ' + stage)
            return
        if stage.startswith('warm_'):
            if shell._warm_pane is None or not shell._warm_pane.ready:
                return
            if stage == 'warm_register':
                result['timings'].setdefault('prewarm_ready', elapsed())
                if args.arm:
                    (output/'armed.json').write_text(json.dumps(dict(hwnd=int(gate.winId()))), encoding='utf-8')
                    if not (output/'go').exists():
                        return
                gate.raise_(); gate.activateWindow()
                capture_at = now
                if args.capture:
                    gate.screen().grabWindow(0, gate.x(), gate.y(), gate.width(), gate.height()).save(str(output/'login.png'))
                    point = gate.mapToGlobal(gate.rect().topLeft())
                    prefix = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, '-B', __file__]
                    recorder = subprocess.Popen(prefix + ['--output', str(output), '--record-region',
                        f'{point.x()},{point.y()},{gate.width()},{gate.height()}'],
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    stage = 'arming_capture'
                else:
                    stage = 'before_register'
            else:
                stage = 'before_forgot'
                click_at = now
        elif stage == 'arming_capture':
            if (output/'recorder-ready.json').exists():
                capture_at = json.loads((output/'recorder-ready.json').read_text(encoding='utf-8'))
                stage = 'before_register'
        elif stage == 'before_register' and now - capture_at >= 1:
            click_at = now
            result['register_click_clock'] = now
            result['actions'].append(dict(action='register', t=elapsed()))
            QTest.mouseClick(gate.register_button, Qt.MouseButton.LeftButton)
            latest_ready = False
            stage = 'register'
        elif stage == 'before_forgot' and now - click_at >= .3:
            click_at = now
            result['actions'].append(dict(action='forgot_password', t=elapsed()))
            QTest.mouseClick(gate.forgot_password_button, Qt.MouseButton.LeftButton)
            latest_ready = False
            stage = 'forgot_password'
        elif stage in ('register', 'forgot_password'):
            pane = shell._pane
            if pane is None:
                finish('missing pane at ' + stage)
                return
            cover = getattr(pane, '_native_first_frame', None)
            if not js_pending:
                js_pending = True
                pane.page.runJavaScript("(()=>{const r=document.querySelector('#mf-account-lifecycle:not([hidden])');"
                    "return !!r && !!r.getClientRects().length && [...r.querySelectorAll('input')].some("
                    "e=>!e.disabled && e.getClientRects().length);})()", observed)
            if latest_ready and cover is not None and cover._done:
                result['timings'].setdefault(stage, round(now - click_at, 4))
                if now - click_at >= 1.3:
                    if args.capture:
                        point = gate.mapToGlobal(gate.rect().topLeft())
                        gate.screen().grabWindow(0, point.x(), point.y(), gate.width(), gate.height()).save(str(output/(stage+'.png')))
                    result['actions'].append(dict(action='back_from_'+stage, t=elapsed()))
                    QTest.mouseClick(pane.back_button, Qt.MouseButton.LeftButton)
                    stage = 'warm_forgot' if stage == 'register' else 'returned'
        elif stage == 'returned':
            result['actions'].append(dict(action='returned_login', t=elapsed()))
            finish()

    poll = QTimer(); poll.setInterval(20); poll.timeout.connect(tick); poll.start()
    capture = QTimer(); capture.setTimerType(Qt.TimerType.PreciseTimer)
    capture.setInterval(40); capture.timeout.connect(record_frame); capture.start()
    app.exec()
    return 1 if result['failure'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
