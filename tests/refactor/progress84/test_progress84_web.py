"""Real React Classroom state updates and narrow light/dark screenshots, offline."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.refactor.conversation37.test_conversation37_web import build


def test_progress84_browser_contract_and_theme_screenshots(tmp_path):
    build(tmp_path, Path(__file__).with_name('progress84_browser.tsx'))
    result = subprocess.run([sys.executable, '-B', str(Path(__file__)), str(tmp_path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list(tmp_path.glob('progress84-*.png'))) == 8


def browser(directory):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    app = QApplication([]); view = QWebEngineView()
    view.resize(600, 900); view.show()
    view.setUrl(QUrl.fromLocalFile(str(directory / 'dist/index.html')))
    def pump(): app.processEvents(); time.sleep(.002)
    def js(code):
        result = []; view.page().runJavaScript(code, result.append)
        deadline = time.monotonic() + 5
        while not result and time.monotonic() < deadline: pump()
        assert result, 'JS callback timeout'
        return result[0]
    deadline = time.monotonic() + 35
    result = None
    while time.monotonic() < deadline:
        pump(); raw = js('JSON.stringify(window.conversationResult || null)')
        if raw and raw != 'null': result = json.loads(raw); break
    assert result and result['ok'], result
    for theme in ('sakura', 'aurora'):
        for mode in ('debt', 'severe', 'today', 'ahead'):
            js(f'window.progress84Show({json.dumps(mode)}, {json.dumps(theme)})')
            until = time.monotonic() + 5
            while time.monotonic() < until:
                pump()
                if js(f'document.querySelector(".class-head [data-progress]")?.getAttribute("data-progress")==={json.dumps(mode)}'): break
            else: raise AssertionError('mode not rendered')
            settle = time.monotonic() + .2
            while time.monotonic() < settle: pump()
            assert not js('document.documentElement.scrollWidth > innerWidth'), 'page overflow'
            assert not js('Array.from(document.querySelectorAll(".class-head .progress-status")).some(e=>e.scrollWidth>e.clientWidth+1 || e.getBoundingClientRect().right>innerWidth)'), 'status overflow'
            assert view.grab().save(str(directory / f'progress84-{mode}-{theme}.png'))
    (directory / 'progress84-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    view.close(); app.processEvents()


if __name__ == '__main__': browser(Path(sys.argv[1]))
