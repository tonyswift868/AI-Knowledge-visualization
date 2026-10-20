"""Browser coverage for archived teacher chats and the collapsible chat list."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
NODE = 'D:/MentorFlow/.local/node/node.exe'
WEB_DEPS = Path('D:/MentorFlow/.local/web-deps')


def build175(root: Path):
    web = REPO / 'refactor/learning/web'
    here = Path(__file__).resolve().parent
    root.mkdir(parents=True, exist_ok=True)
    (root / 'index.html').write_text(
        '<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div>'
        '<script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (root / 'entry.tsx').write_text('import ' + json.dumps((here / 'chat175_browser.tsx').as_posix()), encoding='utf-8')
    (root / 'vite.config.mjs').write_text(
        'import base from ' + json.dumps((web / 'vite.config.mjs').as_uri()) +
        ';export default {...base,root:' + json.dumps(root.as_posix()) +
        ',build:{outDir:"dist",emptyOutDir:true}}', encoding='utf-8')
    env = dict(os.environ, MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps')
    result = subprocess.run([NODE, str(WEB_DEPS / 'node_modules/vite/bin/vite.js'), 'build', '--config', str(root / 'vite.config.mjs')],
                            env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def host175(root: Path):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from refactor.learning.app.bridge import AppBridge, BridgeError
    from tests.refactor.simulation.engine import ensure_offscreen_fonts

    app = QApplication([])
    ensure_offscreen_fonts()
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)

    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ('http', 'https'):
                info.block(True)

    blocker = Offline(page)
    profile.setUrlRequestInterceptor(blocker)
    view.setPage(page)
    bridge = AppBridge(page)
    sessions = {
        'alpha': dict(id='alpha', title='合成对话 Alpha', updated_at='2026-10-20T10:00:00', count=2, archived=False,
                      messages=[dict(role='user', content='合成提问一', seq=1), dict(role='assistant', content='合成回复一', seq=2)]),
        'beta': dict(id='beta', title='合成对话 Beta', updated_at='2026-10-19T10:00:00', count=2, archived=False,
                     messages=[dict(role='user', content='合成提问二', seq=1), dict(role='assistant', content='合成回复二', seq=2)]),
        'gamma': dict(id='gamma', title='已归档合成对话', updated_at='2026-10-18T10:00:00', count=2, archived=True,
                      messages=[dict(role='user', content='合成旧提问', seq=1), dict(role='assistant', content='合成旧回复', seq=2)]),
    }
    preference = {'list_collapsed': False}

    def summary(session):
        return {key: value for key, value in session.items() if key != 'messages'}

    def chat_list(params, _context):
        archived = bool(params.get('archived', False))
        items = [summary(s) for s in sessions.values() if s['archived'] == archived]
        return dict(sessions=sorted(items, key=lambda s: (s['updated_at'], s['id']), reverse=True))

    def archive(params, _context):
        session = sessions[params['session_id']]
        session['archived'] = bool(params['archived'])
        return dict(session=session)

    def create(_params, _context):
        session = dict(id='new', title='新对话', updated_at='2026-10-20T12:00:00', count=0, archived=False, messages=[])
        sessions[session['id']] = session
        return dict(session=session)

    def send(params, context):
        context.stream('替身正在生成。')
        while not (root / 'finish').exists():
            if context.cancel.wait(.01):
                raise BridgeError('CANCELLED', '已取消。')
        session = sessions[params['session_id']]
        session['archived'] = False
        session['messages'].extend([dict(role='user', content=params['text'], seq=3),
                                    dict(role='assistant', content='替身回复已完成。', seq=4)])
        session['count'] = 4
        session['updated_at'] = '2026-10-20T12:01:00'
        return dict(session=session)

    bridge.register('chat.list', chat_list)
    bridge.register('chat.get', lambda p, _c: dict(session=sessions[p['session_id']]))
    bridge.register('chat.create', create)
    bridge.register('chat.archive', archive)
    bridge.register('chat.delete', lambda p, _c: sessions.pop(p['session_id'], None) or dict(ok=True))
    bridge.register('chat.send', send)
    def ui_get(_params, _context):
        snapshot = preference['list_collapsed']
        if (root / 'delay-ui-get').exists():
            (root / 'ui-get-started').touch()
            while not (root / 'release-ui-get').exists():
                time.sleep(.01)
            (root / 'ui-get-returned').touch()
        return dict(list_collapsed=snapshot, available=True)

    def ui_set(params, _context):
        preference['list_collapsed'] = bool(params['list_collapsed'])
        if preference['list_collapsed']:
            (root / 'ui-set-true').touch()
        return dict(list_collapsed=preference['list_collapsed'], saved=True)

    bridge.register('chat.ui_get', ui_get)
    bridge.register('chat.ui_set', ui_set)
    channel = QWebChannel(page)
    channel.registerObject('mf', bridge)
    page.setWebChannel(channel)
    view.resize(1100, 820)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(root / 'dist/index.html')))
    timer = QTimer()
    timer.timeout.connect(lambda: app.quit() if (root / 'done').exists() else None)
    timer.start(100)
    try:
        app.exec()
    finally:
        bridge.close()


def drive175(root: Path, artifacts: Path):
    artifacts.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',
               QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',
               MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}', MF_CHAT175_ARTIFACTS=str(artifacts),
               PYTHONDONTWRITEBYTECODE='1')
    with (root / 'qt175.log').open('w', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-B', str(Path(__file__)), '--host', str(root)], env=env, stdout=log, stderr=log)
        try:
            result = subprocess.run([NODE, str(Path(__file__).with_name('chat175_browser.mjs')), str(root)],
                                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=100)
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            (root / 'done').touch()
            child.wait(timeout=15)
    return json.loads((root / 'browser-result175.json').read_text(encoding='utf-8'))


def test_chat175_archive_collapse_narrow_drawer_draft_and_stream(tmp_path, monkeypatch):
    artifacts = Path(os.environ.get('MF_CHAT175_ARTIFACTS', str(tmp_path / 'screenshots')))
    build175(tmp_path)
    result = drive175(tmp_path, artifacts)
    assert result['ok'], result
    assert {'list-light.png', 'archive-light.png', 'collapsed-light.png'} <= {p.name for p in artifacts.glob('*.png')}


def test_chat175_late_preference_snapshot_does_not_override_user_toggle(tmp_path, monkeypatch):
    root = tmp_path / 'preference'
    root.mkdir(parents=True, exist_ok=True)
    (root / 'delay-ui-get').touch()
    build175(root)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',
               QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',
               MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}', PYTHONDONTWRITEBYTECODE='1')
    with (root / 'qt-pref.log').open('w', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-B', str(Path(__file__)), '--host', str(root)], env=env, stdout=log, stderr=log)
        try:
            result = subprocess.run([NODE, str(Path(__file__).with_name('chat175_preference_race.mjs')), str(root)],
                                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=40)
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            (root / 'done').touch()
            child.wait(timeout=15)
    assert json.loads((root / 'preference-race-result.json').read_text(encoding='utf-8'))['collapsed'] is True


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--host':
        host175(Path(sys.argv[2]))
