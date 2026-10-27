"""Real React pages in Qt Chromium, no network/model/user data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[3]


def build_fixture(root):
    root.mkdir(parents=True,exist_ok=True)
    here=Path(__file__).parent
    (root/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (root/'entry.tsx').write_text('import '+json.dumps((REPO/'refactor/learning/web/src/theme.css').as_posix())+';import '+json.dumps((here/'essay99_browser.tsx').as_posix())+';',encoding='utf-8')
    bridge=json.dumps((here/'essay99_bridge.ts').as_posix())
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((REPO/'refactor/learning/web/vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',resolve:{alias:[{find:"../bridge",replacement:'+bridge+'},{find:"./bridge",replacement:'+bridge+'}]},build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}',encoding='utf-8')
    deps=Path('D:/MentorFlow/.local/web-deps')
    r=subprocess.run(['D:/MentorFlow/.local/node/node.exe',str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps.as_posix()),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert r.returncode==0,r.stdout+r.stderr


def browser(root,shots=None):
    os.environ['QT_QPA_PLATFORM']='offscreen'
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS']='--disable-gpu'
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineUrlRequestInterceptor
    app=QApplication([]);view=QWebEngineView();profile=QWebEngineProfile(view);page=QWebEnginePage(profile,view);view.setPage(page)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            if info.requestUrl().scheme() in ('http','https'):info.block(True)
    interceptor=Offline(page);profile.setUrlRequestInterceptor(interceptor)
    view.resize(1200,1000);view.show()
    def pump(seconds=.04):
        end=time.monotonic()+seconds
        while time.monotonic()<end:app.processEvents();time.sleep(.002)
    def js(code):
        values=[];page.runJavaScript(code,values.append);end=time.monotonic()+4
        while not values and time.monotonic()<end:pump()
        assert values,'JS timeout'
        return values[0]
    def wait(code):
        end=time.monotonic()+12
        while not js(code):
            assert time.monotonic()<end,code
            pump()
    def click(label):
        assert js('(()=>{const b=[...document.querySelectorAll("button")].find(b=>b.textContent.trim()==='+json.dumps(label)+');if(!b||b.disabled)return false;b.click();return true})()'),label
        pump()
    def shot(name):
        if shots:
            shots.mkdir(parents=True,exist_ok=True);pump(.65);assert view.grab().save(str(shots/f'{name}.png'))
    view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    wait('!!window.essay99 && !!document.querySelector(".recall-question")')
    assert not js('[...document.querySelectorAll("button")].some(b=>b.textContent.trim()==="错题集")')
    for theme in ('sakura','aurora'):
        js('window.essay99.theme('+json.dumps(theme)+');window.essay99.page("module");true');pump(.2)
        wait('!!document.querySelector(".recall-own textarea") && !document.querySelector(".recall-own textarea").readOnly')
        js('(()=>{let e=document.querySelector(".recall-own textarea");Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(e,"接收输入，按程序处理，再输出结果。");e.dispatchEvent(new Event("input",{bubbles:true}));return true})()');pump()
        click('写好了，看参考答案');wait('document.querySelectorAll(".recall-point").length===10')
        for i in range(7,10):
            js(f'document.querySelectorAll(".recall-point input")[{i}].click();true')
            wait('!document.querySelector(".recall-point input").disabled')
        shot(theme+'-self-verdict')
        click('算我答对');wait('!!document.querySelector(".recall-result.correct")')
        assert js('document.querySelectorAll(".recall-point.missed").length')==3
        assert js('window.essay99.calls.filter(c=>c.method==="class.recall_submit").at(-1).params.correct') is True
        js('window.essay99.page("check");true');wait('[...document.querySelectorAll("button")].some(b=>b.textContent.trim()==="错题集")')
        shot(theme+'-final-entry')
        before=js('JSON.stringify(window.essay99.calls.filter(c=>c.method.startsWith("classroom.")&&c.method!=="classroom.wrong_answers"))')
        click('错题集');wait('!!document.querySelector("dialog[open]") && document.querySelectorAll(".wrong-question").length===2')
        shot(theme+'-final-list')
        js('document.querySelector(".wrong-question summary").click();true');pump()
        assert js('document.querySelector(".wrong-question").querySelectorAll(".wrong-attempt").length')==3
        assert js('document.querySelectorAll(".wrong-points .not-hit").length')==14
        shot(theme+'-red-cross-review')
        js('document.querySelectorAll(".wrong-question summary")[1].click();true');pump()
        assert js('document.querySelectorAll(".wrong-options .not-hit").length')==1
        assert js('document.querySelectorAll(".wrong-options .hit").length')==1
        click('关闭错题集');wait('!document.querySelector("dialog.wrong-answers")')
        assert before==js('JSON.stringify(window.essay99.calls.filter(c=>c.method.startsWith("classroom.")&&c.method!=="classroom.wrong_answers"))')
        js('window.essay99.page("completed");true');wait('[...document.querySelectorAll("button")].some(b=>b.textContent.trim()==="错题集") && !document.querySelector(".recall-question")')
        shot(theme+'-completed-entry');click('错题集');wait('document.querySelectorAll(".wrong-question").length===2');shot(theme+'-completed-list')
        click('关闭错题集')
        js('window.essay99.page("module");true');wait('!!document.querySelector(".recall-question")')
    view.resize(390,1000);pump(.2)
    assert js('document.documentElement.scrollWidth<=innerWidth')
    view.close();page.deleteLater();pump()
    print('essay99: self choice, 10 key points, red crosses, objective options, both entries, read-only close, light/dark and narrow PASS')


def test_essay99_browser_self_verdict_notebook_and_completed_entry(tmp_path):
    build_fixture(tmp_path)
    r=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=120)
    assert r.returncode==0,r.stdout+r.stderr


if __name__=='__main__':
    root=Path(sys.argv[1]);build_fixture(root);browser(root,Path(sys.argv[2]) if len(sys.argv)>2 else None)
