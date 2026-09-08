"""Visible real MainWindow, isolated data and signed localhost feed.

The supervisor/bridge is a TEST replacement for the installed coordinator.
It proves GUI exit/relaunch only, not protected package install/UAC acceptance.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def environment(root):
    root.mkdir(parents=True, exist_ok=True)
    for key, path in {
        'MENTORFLOW_USER_DATA_ROOT': root/'data',
        'MENTORFLOW_ACCOUNT_VAULT_ROOT': root/'vault',
        'MENTORFLOW_DB': root/'data/app.db',
        'MENTORFLOW_ORM_DB': root/'data/orm.db',
        'MENTORFLOW_ACTIVE_UPLOAD_ROOT': root/'uploads',
        'MENTORFLOW_CLASSROOM_ASR_UPLOAD_ROOT': root/'asr',
        'MENTORFLOW_BACKUP_ROOT': root/'backup',
        'TEMP': root/'temp', 'TMP': root/'temp',
    }.items():
        os.environ[key] = str(path)
        (path.parent if path.suffix == '.db' else path).mkdir(parents=True, exist_ok=True)
    os.environ['MENTORFLOW_DEPLOYMENT_MODE'] = 'PERSONAL_LOCAL'
    os.environ['MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'windows'


def supervise(root, exercise=''):
    from desktop.tests.test_mature_update import Feed
    feed = Feed(root)
    # A multi-second transfer permits real cancel/progress observations.
    feed.publish('1.1.1', {**feed.old_files, '_internal/frontend/ui.txt': b'UI fixture update\n' * 500000})
    feed.signed['release_notes'] = '改进软件更新体验。\n下载可暂停，重启后自动返回。'
    feed.sign(feed.signed)
    feed.delay = .06
    doc = dict(origin=feed.origin, keys={k:base64.b64encode(v).decode() for k,v in feed.keys.items()})
    (root/'feed.json').write_text(json.dumps(doc), encoding='utf-8')
    current = '1.1.0'
    try:
        for launch in range(3):
            process = subprocess.Popen([sys.executable, '-m', __spec__.name, '--root', str(root), '--ui',
                '--version', current, '--exercise', exercise, '--launch-index', str(launch)])
            print(f'UI_PID={process.pid} VERSION={current}', flush=True)
            code = process.wait(timeout=900)
            print(f'UI_EXIT={code}', flush=True)
            if code != 0:
                return code
            request = root/'relaunch-request.json'
            if request.exists():
                current = json.loads(request.read_text())['version']
                request.unlink()
                continue
            if exercise == 'next-start' and launch == 0:
                # Simulate a later normal user launch, without changing the
                # signing key/feed/cache or reading a pending flag as authority.
                continue
            break
        return 0
    finally:
        if process.poll() is None:
            process.terminate(); process.wait(10)
        feed.close()


def ui(root, version, exercise='', launch_index=0):
    from PySide6.QtCore import QSettings, QTimer, QObject, Signal
    from PySide6.QtWidgets import QApplication
    from desktop.update_service import MatureUpdateService
    from desktop.update_download import HTTPTransport
    from desktop.update_controller import application_updater
    from desktop.main_window import MainWindow
    from desktop.account_identity import AccountProfile
    from desktop.style import STYLE

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(root/'settings'))
    app = QApplication([])
    app.setOrganizationName('MentorFlowUpdateUiFixture')
    app.setApplicationName('MentorFlowUpdateUiFixture')
    app.setStyleSheet(STYLE)
    from PySide6.QtWebEngineCore import QWebEngineProfile
    import desktop.main_window as window_module
    class FixtureWebProfile(QWebEngineProfile):
        def __init__(self, name, parent=None):
            super().__init__(name, parent)
            self.setCachePath(str(root/'web-cache'/name))
            self.setPersistentStoragePath(str(root/'web-storage'/name))
    window_module.QWebEngineProfile = FixtureWebProfile
    default = QWebEngineProfile.defaultProfile()
    default.setCachePath(str(root/'web-cache/default'))
    default.setPersistentStoragePath(str(root/'web-storage/default'))
    settings = QSettings('MentorFlow', 'MentorFlow')
    settings.setValue('tutorial/completed', '1')
    doc = json.loads((root/'feed.json').read_text())
    service = MatureUpdateService(install_root=root/'install', cache_root=root/'cache',
        current=version, metadata_url=doc['origin']+'/stable.json',
        public_keys={k:base64.b64decode(v) for k,v in doc['keys'].items()},
        transport=HTTPTransport(doc['origin'], fixture_http=True, total=180, read=3))
    class Api:
        session_token = ''
        class Client:
            is_closed = False
        client = Client()
        def active_workspaces(self, **_): return {'items': []}
        def workspaces(self): return []
        def runtime_policy(self): return {}
        def close(self): self.client.is_closed = True
    class ExitBridge(QObject):
        requested = Signal()
    bridge = ExitBridge()
    def handoff(*_):
        (root/'relaunch-request.json').write_text(json.dumps({'version': '1.1.1'}))
        bridge.requested.emit()
        return {'fixture_coordinator': True}
    service.apply_bridge = handoff
    controller = application_updater(lambda: service)
    window = MainWindow(Api(), {'id': 'ui-fixture', 'display_name': '更新体验验证'},
        doc['origin'], account_profile=AccountProfile.personal_local())
    window.setWindowTitle('MentorFlow · 更新体验验证 · '+version)
    bridge.requested.connect(window.close)
    window.show()
    window.navigation.setCurrentRow(7)
    def record():
        panel = window.system_page.update_panel
        (root/'visible-state.json').write_text(json.dumps({
            'pid': os.getpid(), 'version': version, 'state': panel.property('updateState'),
            'visible': window.isVisible(), 'text': panel.status.text(),
            'transfer': panel.transfer_label.text(), 'settings_page': window.pages.currentIndex(),
        }, ensure_ascii=False), encoding='utf-8')
    timer = QTimer(app); timer.setInterval(250); timer.timeout.connect(record); timer.start()
    QTimer.singleShot(0, record)
    if exercise:
        # In-process Qt integration test of the real, visible MainWindow.
        # No operating-system input injection or authentication is performed.
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        phase = ['initial']
        started = time.monotonic()
        failure = []
        events = []
        def evidence(event):
            events.append({'event':event, 'version':version, 'pid':os.getpid(),
                           'ms':round((time.monotonic()-started)*1000)})
            (root/f'ui-events-{launch_index}.json').write_text(json.dumps(events), encoding='utf-8')
            window.grab().save(str(root/f'ui-{launch_index}-{event}.png'))
        def exercise_tick():
            panel = window.system_page.update_panel
            state = panel.property('updateState')
            if time.monotonic()-started > 120:
                failure.append('UI cycle timed out'); window.close(); return
            if version == '1.1.1':
                if state == 'UP_TO_DATE':
                    assert panel.status.text() == '已是最新版本'
                    assert window._tutorial_overlay is None
                    assert window.pages.currentIndex() == 9
                    evidence('up-to-date'); window.close()
                return
            if launch_index > 0:
                return  # Pending startup must drive itself through the controller.
            if phase[0] == 'initial' and state == 'UPDATE_AVAILABLE':
                evidence('available')
                QTest.mouseClick(panel.download_button, Qt.MouseButton.LeftButton)
                phase[0] = 'first-download'
            elif phase[0] == 'first-download' and service.snapshot.get('actual_downloaded_bytes', 0) > 200000:
                evidence('progress')
                QTest.mouseClick(panel.cancel_button, Qt.MouseButton.LeftButton)
                phase[0] = 'cancelled'
            elif phase[0] == 'cancelled' and state == 'RECOVERABLE_ERROR' and not controller.busy:
                assert window.isVisible() and panel.download_button.isEnabled()
                evidence('cancelled')
                QTest.mouseClick(panel.download_button, Qt.MouseButton.LeftButton)
                phase[0] = 'retrying'
            elif phase[0] == 'retrying' and state == 'READY_TO_RESTART' and not controller.busy:
                assert panel.apply_button.isEnabled()
                evidence('ready')
                phase[0] = 'finished'
                if exercise == 'next-start':
                    window.close()
                else:
                    QTest.mouseClick(panel.apply_button, Qt.MouseButton.LeftButton)
        exercise_timer = QTimer(app); exercise_timer.setInterval(100)
        exercise_timer.timeout.connect(exercise_tick); exercise_timer.start()
    code = app.exec()
    controller.shutdown()
    if exercise and failure:
        raise AssertionError(failure[0])
    return code


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--ui', action='store_true')
    parser.add_argument('--version', default='1.1.0')
    parser.add_argument('--exercise', choices=('', 'restart', 'next-start'), default='')
    parser.add_argument('--launch-index', type=int, default=0)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to(Path('E:/Codex/Temp').resolve()):
        parser.error('fixture root must be under E:/Codex/Temp')
    environment(root)
    raise SystemExit(ui(root, args.version, args.exercise, args.launch_index) if args.ui else supervise(root, args.exercise))
