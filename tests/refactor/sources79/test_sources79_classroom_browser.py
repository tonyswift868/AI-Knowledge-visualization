"""Real Classroom/Markdown/card DOM, explicit fake bridge, no network or model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def build(root):
    repo = Path(__file__).resolve().parents[3]
    here = Path(__file__).parent
    root.mkdir(parents=True, exist_ok=True)
    (root / 'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (root / 'entry.tsx').write_text('import ' + json.dumps((here / 'sources79_browser.tsx').as_posix()), encoding='utf-8')
    aliases = [dict(find='../bridge', replacement=(here / 'sources79_bridge.ts').as_posix()),
               dict(find='../voice', replacement=(repo / 'tests/refactor/conversation37/conversation37_optional.tsx').as_posix())]
    (root / 'vite.config.mjs').write_text('import base from ' + json.dumps((repo / 'refactor/learning/web/vite.config.mjs').as_uri()) + ';export default {...base,root:' + json.dumps(root.as_posix()) + ',resolve:{alias:' + json.dumps(aliases) + '},build:{outDir:"dist",emptyOutDir:true}}', encoding='utf-8')
    deps = Path('D:/MentorFlow/.local/web-deps')
    result = subprocess.run(['D:/MentorFlow/.local/node/node.exe', str(deps / 'node_modules/vite/bin/vite.js'), 'build', '--config', str(root / 'vite.config.mjs')],
                            env=dict(os.environ, MENTORFLOW_WEB_DEPS=str(deps)), capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def browser(root):
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
    block = Offline(page)
    profile.setUrlRequestInterceptor(block)
    view.setPage(page)
    view.resize(1280, 920)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(root / 'dist/index.html')))

    def pump():
        app.processEvents()
        time.sleep(.003)

    def js(code):
        values = []
        page.runJavaScript(code, values.append)
        end = time.monotonic() + 5
        while not values and time.monotonic() < end:
            pump()
        assert values, 'JS callback timeout'
        return values[0]

    def wait(code):
        end = time.monotonic() + 30
        while not js(code):
            assert time.monotonic() < end, (code, js('document.body.innerText'))
            pump()

    wait('!!window.sources79Result')
    result = json.loads(js('JSON.stringify(window.sources79Result)'))
    (root / 'browser-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    assert result['ok'], result
    (root / 'screenshots').mkdir(exist_ok=True)
    for theme in ('sakura', 'aurora'):
        for expanded in (False, True):
            js('window.sources79Show(' + json.dumps(theme) + ',' + json.dumps(expanded) + ');true')
            wait('window.sources79ShotReady===true')
            assert not js('document.documentElement.scrollWidth>innerWidth'), 'horizontal overflow'
            color = ('#bb2233' if expanded else '#aa1122') if theme == 'sakura' else ('#dd4455' if expanded else '#cc3344')
            end = time.monotonic() + 5
            while True:
                picture = view.grab()
                if picture.toImage().pixelColor(0, 0).name() == color:
                    break
                assert time.monotonic() < end, 'new compositor frame timeout'
                pump()
            assert picture.save(str(root / 'screenshots' / f'{theme}-{"expanded" if expanded else "collapsed"}.png'))
    view.resize(600, 920)
    wait('innerWidth===600')
    assert not js('document.documentElement.scrollWidth>innerWidth'), 'narrow horizontal overflow'
    view.close()
    page.deleteLater()
    pump()
    print(json.dumps(result, ensure_ascii=False))


def test_sources79_collapsed_grouped_citations_and_teacher_arrows(tmp_path):
    build(tmp_path)
    result = subprocess.run([sys.executable, '-B', str(Path(__file__)), str(tmp_path)], capture_output=True, text=True, encoding='utf-8', timeout=100)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    browser(Path(sys.argv[1]))
