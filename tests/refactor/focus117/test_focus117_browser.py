"""Real Markdown/components in Qt Chromium; no bridge, models or external network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[3]


def build(root):
    root.mkdir(parents=True, exist_ok=True)
    (root/'entry.tsx').write_text('import '+json.dumps((Path(__file__).parent/'preview117.tsx').as_posix()), encoding='utf-8')
    (root/'index.html').write_text('<meta name="viewport" content="width=device-width, initial-scale=1"><div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    config = 'import base from '+json.dumps((REPO/'refactor/learning/web/vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',build:{outDir:"dist",emptyOutDir:true}}'
    (root/'vite.config.mjs').write_text(config, encoding='utf-8')
    result = subprocess.run(['D:/MentorFlow/.local/node/node.exe', 'D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js',
        'build', '--config', str(root/'vite.config.mjs')], env=dict(os.environ, MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps'),
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    assert result.returncode == 0, result.stdout+result.stderr


def browser(root):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ('http', 'https'): info.block(True)
    app=QApplication([]);view=QWebEngineView();profile=QWebEngineProfile(view)
    page=QWebEnginePage(profile, view);block=Offline(page)
    profile.setUrlRequestInterceptor(block);view.setPage(page)
    view.resize(1160, 900);view.show()
    view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    def pump():
        app.processEvents();time.sleep(.003)
    def js(code):
        values=[];page.runJavaScript(code, values.append);end=time.monotonic()+5
        while not values and time.monotonic()<end: pump()
        assert values, 'JS callback timeout'
        return values[0]
    def until(code):
        end=time.monotonic()+20
        while not js(code):
            assert time.monotonic()<end, (code, js('document.body.innerText'))
            pump()
    until('!!window.focus117Result')
    result=json.loads(js('JSON.stringify(window.focus117Result)'))
    (root/'browser-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['ok'], result
    shots=root/'screenshots';shots.mkdir(exist_ok=True)
    for index, (width, theme) in enumerate([(1160,'sakura'),(1160,'aurora'),(600,'sakura'),(600,'aurora')]):
        view.resize(width, 900)
        color=['#ad1170','#ad2270','#ad3370','#ad4470'][index]
        # A one-pixel fixture-only paint token verifies the compositor has the
        # final React/theme frame, rather than a preceding streaming snapshot.
        js('window.focus117Theme('+json.dumps(theme)+');'
           'var dot=document.getElementById("paint117")||document.body.appendChild(document.createElement("i"));'
           'dot.id="paint117";dot.style.cssText="position:fixed;left:0;top:0;width:1px;height:1px;z-index:9999;background:'+color+'";'
           'window.focus117Painted=false;requestAnimationFrame(()=>requestAnimationFrame(()=>{window.focus117Painted=true}));true')
        until(f'innerWidth==={width} && window.focus117Painted===true')
        assert not js('document.documentElement.scrollWidth>innerWidth'), 'Horizontal overflow'
        expected='#fbf4f7' if theme=='sakura' else '#0c0e1c'
        end=time.monotonic()+10
        while True:
            picture=view.grab()
            if picture.width()==width and picture.toImage().pixelColor(0,0).name()==color and picture.toImage().pixelColor(1,1).name()==expected: break
            assert time.monotonic()<end, 'Compositor frame timeout'
            pump()
        assert picture.save(str(shots/f'focus117-{theme}-{width}.png'))
    view.close();page.deleteLater();pump()
    print(json.dumps(result, ensure_ascii=False))


def test_focus117_highlight_focus_recap_and_legacy_browser(tmp_path):
    build(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),'--browser',str(tmp_path)],
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    assert result.returncode==0, result.stdout+result.stderr


if __name__=='__main__':
    browser(Path(sys.argv[2]))
