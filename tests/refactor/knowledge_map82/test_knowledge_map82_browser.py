"""Real App/PlanDetail with a synthetic 541-point plan or an opt-in local copy."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def build(root, before=False):
    repo = Path(__file__).resolve().parents[3]
    here = Path(__file__).resolve().parent
    root.mkdir(parents=True, exist_ok=True)
    (root / 'index.html').write_text('<div id="root"></div><script src="qrc:///qtwebchannel/qwebchannel.js"></script><script>window.map82Native=' + json.dumps(bool(os.environ.get('MAP82_NATIVE_COPY'))) + ';window.map82Mode=' + json.dumps('before' if before else 'after') + '</script><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (root / 'entry.tsx').write_text('import ' + json.dumps((here / 'map82_browser.tsx').as_posix()), encoding='utf-8')
    aliases = [dict(find='../bridge', replacement=(here / 'map82_bridge.ts').as_posix()),
               dict(find='./bridge', replacement=(here / 'map82_bridge.ts').as_posix()),
               dict(find='./map82_data.json', replacement=(root / 'data.json').as_posix()),
               dict(find='../store', replacement=(repo / 'refactor/learning/web/src/store.ts').as_posix()),
               dict(find='../ui', replacement=(repo / 'refactor/learning/web/src/ui.tsx').as_posix()),
               dict(find='../voice', replacement=(repo / 'tests/refactor/conversation37/conversation37_optional.tsx').as_posix())]
    if os.environ.get('MAP82_DATA'):
        data=json.loads(Path(os.environ['MAP82_DATA']).read_text(encoding='utf8'))
    else:
        modules=[]
        for i,(size,count) in enumerate(zip((16,18,49,11,51,255,93,48),(0,0,2,0,19,82,36,0))):
            modules.append(dict(id=f'm{i}',title=f'模块 {i+1}',summary='学习目标与资料原文概念',learning_minutes=size*8,check_minutes=0,state='untested',question_count=count,passed_questions=0,
                                points=[dict(id=f'p{i}-{n}',title=f'知识点 {n+1}',summary='认识概念并理解适用场景',minutes=8,taught=False,citations=[dict(chunk_id='c1',title='合成测试资料',locator='段落1',quote='明确的概念原文',url=None)]) for n in range(size)]))
        data=dict(plan=dict(id='029b076315034c00bbd6842a5c9c31d9',title='ai-agent学习',goal='ai-agent学习',state='active',total_minutes=4328,window=dict(start='2026-10-07',end='2026-10-20',daily_cap_minutes=600,weekday_weights=[1]*7)),
                  stages=[dict(id='s1',title='全部模块',modules=modules)],sources=[],daily=dict(full_day=310))
    (root / 'data.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
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
    native_store = native_bridge = None
    if os.environ.get('MAP82_NATIVE_COPY'):
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
        from PySide6.QtWebChannel import QWebChannel
        from refactor.learning.app.bridge import AppBridge
        from refactor.learning.app.plan_flow import PlanFlow
        from refactor.learning.course.store import CourseStore
        from types import SimpleNamespace
        from datetime import date
        native_store=CourseStore('mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932',os.environ['MAP82_NATIVE_COPY'])
        flow=PlanFlow(SimpleNamespace(store=native_store),lambda *a:None,lambda:date(2026,10,8))
        native_bridge=AppBridge(page)
        native_bridge.register('plan.detail',lambda p,c:flow.detail(p['plan_id']))
        channel=QWebChannel(page);channel.registerObject('mf',native_bridge);page.setWebChannel(channel)
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

    wait('!!window.map82Result')
    result = json.loads(js('JSON.stringify(window.map82Result)'))
    (root / 'browser-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    assert result['ok'], result
    (root / 'screenshots').mkdir(exist_ok=True)
    for theme,mode in (() if os.environ.get('MAP82_NATIVE_COPY') else ((theme,mode) for theme in ('sakura','aurora') for mode in ('overview','network','running','source'))):
            js('window.map82Show(' + json.dumps(theme) + ',' + json.dumps(mode) + ');true')
            wait('window.map82ShotReady===true')
            assert not js('document.documentElement.scrollWidth>innerWidth'), 'horizontal overflow'
            color = ('#aa112' if theme == 'sakura' else '#bb223') + str(('overview','network','running','source').index(mode)+2)
            end = time.monotonic() + 5
            while True:
                picture = view.grab()
                if picture.toImage().pixelColor(0, 0).name() == color:
                    break
                assert time.monotonic() < end, 'new compositor frame timeout'
                pump()
            assert picture.save(str(root / 'screenshots' / f'{theme}-{mode}.png'))
    view.resize(600, 920)
    wait('innerWidth===600')
    assert not js('document.documentElement.scrollWidth>innerWidth'), 'narrow horizontal overflow'
    view.close()
    if native_bridge: native_bridge.close()
    if native_store: native_store.close()
    page.deleteLater()
    pump()
    print(json.dumps(result, ensure_ascii=False))


def test_knowledge_map82_interactive_states_and_markdown(tmp_path):
    build(tmp_path)
    result = subprocess.run([sys.executable, '-B', str(Path(__file__)), str(tmp_path)], capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    root=Path(sys.argv[1])
    if len(sys.argv)>2:
        build(root,before=sys.argv[2]=='before')
    browser(root)
