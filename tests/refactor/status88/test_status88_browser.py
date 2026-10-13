"""Actual PlanDetail/KnowledgeMap and Qt browser, with an explicit offline bridge."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))


def build(root):
    root.mkdir(parents=True, exist_ok=True)
    (root/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf8')
    (root/'entry.tsx').write_text('import '+json.dumps((HERE/'status88_browser.tsx').as_posix()), encoding='utf8')
    aliases = [dict(find='../bridge', replacement=(HERE/'status88_bridge.ts').as_posix()),
               dict(find='../voice', replacement=(REPO/'tests/refactor/conversation37/conversation37_optional.tsx').as_posix())]
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((REPO/'refactor/learning/web/vite.config.mjs').as_uri())+
        ';export default {...base,root:'+json.dumps(root.as_posix())+',resolve:{alias:'+json.dumps(aliases)+
        '},build:{outDir:"dist",emptyOutDir:true}}', encoding='utf8')
    p = subprocess.run(['D:/MentorFlow/.local/node/node.exe', 'D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js',
                        'build', '--config', str(root/'vite.config.mjs')],
                       env=dict(os.environ, MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps'),
                       capture_output=True, text=True, encoding='utf8', timeout=90)
    assert p.returncode == 0, p.stdout+p.stderr


def browser(root):
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from tests.refactor.simulation.engine import ensure_offscreen_fonts
    app = QApplication([])
    ensure_offscreen_fonts()
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ('http', 'https'): info.block(True)
    block = Offline(page)
    profile.setUrlRequestInterceptor(block)
    view.setPage(page); view.resize(1280, 1250); view.show()
    view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    def pump(): app.processEvents(); time.sleep(.004)
    def js(code):
        values = []; page.runJavaScript(code, values.append)
        end = time.monotonic()+5
        while not values and time.monotonic()<end: pump()
        assert values, 'JS callback timeout'
        return values[0]
    def wait(code):
        end = time.monotonic()+45
        while not js(code):
            assert time.monotonic()<end, js('document.body.innerText')
            pump()
    wait('!!window.status88Result')
    result = json.loads(js('JSON.stringify(window.status88Result)'))
    (root/'browser-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    assert result['ok'], result
    shots = root/'screenshots'; shots.mkdir(exist_ok=True)
    for theme in ('sakura', 'aurora'):
        for mode in ('minutes', 'links'):
            js('window.status88Show('+json.dumps(theme)+','+json.dumps(mode)+');true')
            wait('window.status88Ready===true')
            assert js('Array.from(document.querySelectorAll(".wait-long")).some(e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight})'), 'running notice outside screenshot'
            color = ('#aa112' if theme=='sakura' else '#bb223')+('2' if mode=='minutes' else '3')
            end = time.monotonic()+5
            while True:
                frame = view.grab()
                if frame.toImage().pixelColor(0,0).name() == color: break
                assert time.monotonic()<end, 'compositor did not update'
                pump()
            assert frame.save(str(shots/f'{theme}-{mode}.png'))
    view.close(); page.deleteLater(); pump()
    print(json.dumps(result, ensure_ascii=False))


def test_status88_page_open_completion_retry_light_and_dark(tmp_path):
    build(tmp_path)
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu',PYTHONIOENCODING='utf-8')
    run = subprocess.run([sys.executable, '-B', str(Path(__file__)), str(tmp_path)], env=env,
                         capture_output=True, text=True, encoding='utf8', errors='replace', timeout=100)
    assert run.returncode == 0, run.stdout+run.stderr


if __name__ == '__main__':
    target=Path(sys.argv[1])
    if not (target/'dist/index.html').is_file(): build(target)
    browser(target)
