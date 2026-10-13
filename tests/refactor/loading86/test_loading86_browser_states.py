"""Use the existing QtWebEngine browser harness; paid/copy mode is explicit, never pytest default."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
sys.path.insert(0,str(REPO))  # Direct child browser process has the script directory, not repo, on sys.path.

def build(root):
    root.mkdir(parents=True,exist_ok=True)
    (root/'index.html').write_text('<div id="root"></div><script src="qrc:///qtwebchannel/qwebchannel.js"></script><script>window.loading86Native='+json.dumps(bool(os.environ.get('LOADING86_NATIVE_COPY')))+';window.loading86ReaderOnly='+json.dumps(bool(os.environ.get('LOADING86_READER_ONLY')))+';</script><script type="module" src="/entry.tsx"></script>',encoding='utf8')
    (root/'entry.tsx').write_text('import '+json.dumps((HERE/'loading86_browser.tsx').as_posix()),encoding='utf8')
    aliases=[dict(find='../voice',replacement=(REPO/'tests/refactor/conversation37/conversation37_optional.tsx').as_posix())]
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((REPO/'refactor/learning/web/vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',resolve:{alias:'+json.dumps(aliases)+'},build:{outDir:"dist",emptyOutDir:true}}',encoding='utf8')
    p=subprocess.run(['D:/MentorFlow/.local/node/node.exe','D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js','build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps'),capture_output=True,text=True,encoding='utf8',timeout=90)
    assert p.returncode==0,p.stdout+p.stderr

def browser(root):
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS','--disable-gpu')
    app=QApplication([])
    from tests.refactor.simulation.engine import ensure_offscreen_fonts
    ensure_offscreen_fonts()
    view=QWebEngineView();profile=QWebEngineProfile(view);page=QWebEnginePage(profile,view)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            if info.requestUrl().scheme() in ('http','https'):info.block(True)
    block=Offline(page);profile.setUrlRequestInterceptor(block);view.setPage(page)
    native=None
    if os.environ.get('LOADING86_NATIVE_COPY'):
        # Host setup lives in task output, not product code; explicit opt-in only.
        if os.environ.get('LOADING86_LIVE')!='1':raise AssertionError('native host requires explicit paid opt-in')
        import importlib.util
        setup=Path(os.environ['LOADING86_HOST'])
        spec=importlib.util.spec_from_file_location('loading86_host',setup);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        native=module.bind(page,os.environ['LOADING86_NATIVE_COPY'],root)
    view.resize(1280,920);view.show();view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    def pump():app.processEvents();time.sleep(.004)
    def js(code):
        values=[];page.runJavaScript(code,values.append);end=time.monotonic()+5
        while not values and time.monotonic()<end:pump()
        assert values,'JS callback timeout'
        return values[0]
    def wait(code,limit=40):
        end=time.monotonic()+limit
        while not js(code):
            assert time.monotonic()<end,(code,js('document.body.innerText'))
            pump()
    wait('!!window.loading86Result',limit=260 if native else 40)
    result=json.loads(js('JSON.stringify(window.loading86Result)'))
    (root/'browser-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    assert result['ok'],result
    if not native:
        shots=root/'screenshots';shots.mkdir(exist_ok=True)
        for theme in ('sakura','aurora'):
            for mode in ('medium','long','teacher','slow'):
                js('window.loading86Show('+json.dumps(theme)+','+json.dumps(mode)+');true');wait('window.loading86Ready===true')
                wait('Number(getComputedStyle(document.querySelector(".page")).opacity)>=.999')
                color=('#aa112' if theme=='sakura' else '#bb223')+str(('medium','long','teacher','slow').index(mode)+2);end=time.monotonic()+5
                while True:
                    image=view.grab()
                    if image.toImage().pixelColor(0,0).name()==color:break
                    assert time.monotonic()<end,'compositor frame';pump()
                assert image.save(str(shots/f'{theme}-{mode}.png'))
        view.resize(600,920);wait('innerWidth===600')
        assert not js('document.documentElement.scrollWidth>innerWidth'),'600px overflow'
        js('window.loading86Show("aurora","slow",true);true');wait('window.loading86Ready===true')
        assert js('getComputedStyle(document.querySelector(".wait-art svg")).animationName')=='none','reduced motion'
        assert view.grab().save(str(shots/'aurora-600-reduced.png'))
    view.close()
    if native:native.close()
    page.deleteLater();pump()
    print(json.dumps(result,ensure_ascii=False))

def test_loading86_clock_slots_stream_and_metadata(tmp_path):
    build(tmp_path)
    run=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],env=dict(os.environ,PYTHONIOENCODING='utf-8'),capture_output=True,text=True,encoding='utf8',errors='replace',timeout=100)
    assert run.returncode==0,run.stdout+run.stderr

if __name__=='__main__':
    target=Path(sys.argv[1])
    if not (target/'dist/index.html').is_file(): build(target)
    browser(target)
