"""Exercise ClassroomMarkdown focus fallback in a real offline React Chromium page."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[3]
NODE = Path(r'D:\MentorFlow\.local\node\node.exe')
DEPS = Path(r'D:\MentorFlow\.local\web-deps')


def build(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    fixture = Path(__file__).with_name('style143_browser.tsx')
    (root / 'index.html').write_text(
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (root / 'entry.tsx').write_text(f'import {json.dumps(fixture.as_posix())}', encoding='utf-8')
    config = f'''import base from {json.dumps((REPO / 'refactor/learning/web/vite.config.mjs').as_uri())}
export default {{...base,root:{json.dumps(root.as_posix())},build:{{outDir:'dist',emptyOutDir:true}}}}
'''
    (root / 'vite.config.mjs').write_text(config, encoding='utf-8')
    result = subprocess.run([str(NODE), str(DEPS / 'node_modules/vite/bin/vite.js'),
        'build', '--config', str(root / 'vite.config.mjs')],
        env=dict(os.environ, MENTORFLOW_WEB_DEPS=str(DEPS)), capture_output=True,
        text=True, encoding='utf-8', errors='replace', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def browser(root: Path):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView

    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ('http', 'https'):
                info.block(True)

    app = QApplication([])
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    blocker = Offline(page)
    profile.setUrlRequestInterceptor(blocker)
    view.setPage(page)
    view.resize(960, 740)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(root / 'dist/index.html')))

    def pump():
        app.processEvents()
        time.sleep(.004)

    def js(code):
        result = []
        page.runJavaScript(code, result.append)
        deadline = time.monotonic() + 5
        while not result and time.monotonic() < deadline:
            pump()
        assert result, 'Chromium JavaScript callback timed out'
        return result[0]

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = js('JSON.stringify(window.style143Result||null)')
        if value and value != 'null':
            break
        pump()
    else:
        raise AssertionError('React fixture did not publish results')
    result = json.loads(value)
    (root / 'browser-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    assert result['ok'], result
    assert view.grab().save(str(root / 'style143-focus.png'))
    view.close()
    app.processEvents()


def test_style143_focus_no_highlight_and_many_highlights_browser(tmp_path):
    build(tmp_path)
    env = dict(os.environ)
    env['QT_QPA_PLATFORM'] = 'offscreen'
    env['QTWEBENGINE_CHROMIUM_FLAGS'] = '--disable-gpu'
    result = subprocess.run([sys.executable, '-B', str(Path(__file__)), '--browser', str(tmp_path)],
        env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    browser(Path(sys.argv[2]))
