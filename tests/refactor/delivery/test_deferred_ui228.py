"""Deferred directory activation stays a usable, non-error update offer."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from refactor.desktop.update_dialog import UpdateDialog
from refactor.desktop.updates import DesktopUpdates

MESSAGE = '新版已下载好，下次打开软件时完成更新。'


@pytest.fixture
def adapter228(tmp_path, monkeypatch):
    from refactor.desktop.delivery import per_user_install
    monkeypatch.setattr(per_user_install, 'schedule_updater_refresh', lambda *_: None)
    app = QApplication.instance() or QApplication([])
    gate = QWidget()
    core = SimpleNamespace(attach_shutdown_guard=lambda *_: None)
    ui = DesktopUpdates(app, tmp_path, gate, core=core)
    yield app, gate, ui
    ui.close()
    gate.close()


def test_deferred228_startup_keeps_login_and_pending_message(adapter228, monkeypatch):
    _, gate, ui = adapter228
    shown = []
    monkeypatch.setattr(ui, '_show_prompt', lambda: shown.append(True))
    ui._apply_startup_result(dict(state='HEALTHY', login_allowed=True, current='2.0.36',
        feed='NOT_CHECKED', deferred_update=True, deferred_target='2.0.37',
        deferred_message=MESSAGE, message='可以正常登录。'))
    assert gate.isEnabled()
    assert not ui.snapshot()['mandatory']
    assert ui.snapshot()['message'] == MESSAGE
    assert shown == [True]


def test_deferred228_background_ready_does_not_erase_next_launch_message(adapter228):
    _, _, ui = adapter228
    ui.state = dict(state='OPTIONAL_UPDATE', current='2.0.36', latest='2.0.37',
        deferred_update=True, deferred_message=MESSAGE, message=MESSAGE)
    ui._receive({'background_ready': {'state': 'UPDATE_DOWNLOADED',
                                     'ui_state': 'READY_TO_APPLY', 'prepared': True}})
    assert ui.snapshot()['message'] == MESSAGE


def test_deferred228_native_offer_preserves_message(adapter228):
    app, gate, ui = adapter228
    ui._prompt = UpdateDialog(gate)
    ui.state = dict(state='OPTIONAL_UPDATE', current='2.0.36', latest='2.0.37',
        deferred_update=True, deferred_message=MESSAGE, message=MESSAGE)
    ui._update_prompt()
    assert ui._prompt.message.text() == MESSAGE
    assert ui._prompt.install.isEnabled()
    ui._prompt.close()
    app.processEvents()


def test_deferred228_web_notice_has_pending_message_and_manual_retry(tmp_path):
    from tests.refactor.update194.test_settings_update194 import _prepare_page, NODE, DEPS
    _prepare_page(tmp_path)
    screenshots = Path(os.environ.get('UPDATE228_ARTIFACTS', str(tmp_path/'screenshots')))
    screenshots.mkdir(parents=True, exist_ok=True)
    temp = tmp_path/'temp'; temp.mkdir()
    local = tmp_path/'local'; local.mkdir()
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1',
           'QT_QPA_PLATFORM': 'offscreen', 'QTWEBENGINE_CHROMIUM_FLAGS': '--disable-gpu --remote-allow-origins=*',
           'TEMP': str(temp), 'TMP': str(temp), 'LOCALAPPDATA': str(local),
           'UPDATE228_ARTIFACTS': str(screenshots)}
    env['PATH'] = str(NODE.parent)+os.pathsep+env.get('PATH', '')
    build = subprocess.run([str(NODE), str(DEPS/'node_modules/vite/bin/vite.js'), 'build',
        '--config', str(tmp_path/'vite.config.mjs')], env=env, capture_output=True, text=True,
        encoding='utf-8', timeout=120)
    assert build.returncode == 0, build.stdout+build.stderr
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    env['QTWEBENGINE_REMOTE_DEBUGGING'] = f'127.0.0.1:{port}'
    env['MF_CDP_ENDPOINT'] = f'http://127.0.0.1:{port}'
    with (tmp_path/'qt228.log').open('w', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-B', str(Path(__file__)), '--host', str(tmp_path)],
                                 env=env, stdout=log, stderr=log)
        try:
            run = subprocess.run([str(NODE), str(Path(__file__).with_name('browser_deferred228.mjs')), str(tmp_path)],
                env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
            assert run.returncode == 0, run.stdout+run.stderr
            result = json.loads((tmp_path/'deferred228.json').read_text(encoding='utf-8'))
            assert result['checks'] == 6
            assert result['manualCheck']
        finally:
            (tmp_path/'done').touch()
            child.wait(timeout=15)


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from tests.refactor.skip127.test_skip127_browser import host127
    host127(Path(sys.argv[2]))
