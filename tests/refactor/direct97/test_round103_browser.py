"""Actual Chromium + fake bridge, CDP DOM operations; no OS input or focus."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))
NODE='D:/MentorFlow/.local/node/node.exe'
CDP=REPO/'scripts/release/rehearsal102/cdp.mjs'


def host(directory):
    from PySide6.QtCore import QTimer,QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    app=QApplication([]);view=QWebEngineView();profile=QWebEngineProfile(view);page=QWebEnginePage(profile,view)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            if info.requestUrl().scheme() in ('http','https'):info.block(True)
    block=Offline(page);profile.setUrlRequestInterceptor(block);view.setPage(page)
    view.resize(1200,950);view.show();view.setUrl(QUrl.fromLocalFile(str(directory/'dist/index.html')))
    timer=QTimer();timer.timeout.connect(lambda:app.quit() if (directory/'done').exists() else None);timer.start(100)
    app.exec()


def test_round103_actual_pages_anchor_answers_short_stream_and_upward_scroll(tmp_path):
    from tests.refactor.direct97.test_direct97_web import build
    build(tmp_path,'round103_browser.tsx','round103_bridge.ts')
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING='127.0.0.1:18863',
        QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT='http://127.0.0.1:18863',
        MF_UAT_ROOT=str(tmp_path),PYTHONDONTWRITEBYTECODE='1')
    def cdp(action,selector='',value=''):
        p=subprocess.run([NODE,str(CDP),action,selector,value],env=env,capture_output=True,text=True,encoding='utf8',timeout=45)
        assert p.returncode==0,p.stdout+p.stderr
        return json.loads(p.stdout)
    with (tmp_path/'qt.log').open('w',encoding='utf8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(tmp_path)],env=env,stdout=log,stderr=log)
        try:
            until=time.monotonic()+30
            while True:
                try:
                    if cdp('evaluate','!!window.run103'):break
                except AssertionError:
                    if time.monotonic()>until:raise
                time.sleep(.2)
            result=cdp('evaluate','window.run103()')
            assert result['ok'],result
            (tmp_path/'result103.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            shots=Path(os.environ.get('MF_103_SCREENSHOTS',str(tmp_path/'screenshots')));shots.mkdir(parents=True,exist_ok=True)
            for theme in ('sakura','aurora'):
                for mode in ('direct','classroom','chat','plan'):
                    cdp('evaluate',f'document.documentElement.dataset.theme={json.dumps(theme)}; window.show103({json.dumps(mode)}).then(()=>true)')
                    time.sleep(.7)
                    cdp('shot',str(shots/f'{mode}-{theme}.png'))
        finally:
            (tmp_path/'done').touch();child.wait(timeout=15)


if __name__=='__main__':host(Path(sys.argv[2]))
