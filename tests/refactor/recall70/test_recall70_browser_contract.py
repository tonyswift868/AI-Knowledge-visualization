"""Real Classroom/App in offline Qt Chromium; only the bridge is replaced."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def build_fixture(root):
    repo = Path(__file__).resolve().parents[3]
    here = Path(__file__).parent
    root.mkdir(parents=True, exist_ok=True)
    (root/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (root/'entry.tsx').write_text('import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';import '+json.dumps((here/'recall70_browser.tsx').as_posix())+';', encoding='utf-8')
    bridge = json.dumps((here/'recall70_bridge.ts').as_posix())
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((repo/'refactor/learning/web/vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',resolve:{alias:[{find:"../bridge",replacement:'+bridge+'},{find:"./bridge",replacement:'+bridge+'}]},build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}', encoding='utf-8')
    deps = Path('D:/MentorFlow/.local/web-deps')
    result = subprocess.run(['D:/MentorFlow/.local/node/node.exe',str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps.as_posix()),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode == 0, result.stdout+result.stderr


def browser(root, shots=None):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = '--disable-gpu'
    from PySide6.QtCore import QUrl
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    app=QApplication([]); view=QWebEngineView(); profile=QWebEngineProfile(view); page=QWebEnginePage(profile,view); view.setPage(page)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ('http','https'): info.block(True)
    interceptor=Offline(page); profile.setUrlRequestInterceptor(interceptor)
    view.resize(1000,900); view.show()
    def pump(seconds=.02):
        end=time.monotonic()+seconds
        while time.monotonic()<end: app.processEvents(); time.sleep(.003)
    def js(code):
        values=[]; page.runJavaScript(code,values.append); end=time.monotonic()+4
        while not values and time.monotonic()<end: pump()
        assert values, 'JS timeout'
        return values[0]
    def wait(code):
        end=time.monotonic()+10
        while not js(code):
            assert time.monotonic()<end, code
            pump()
    def click(text):
        js('(()=>{const b=[...document.querySelectorAll("button")].find(e=>e.textContent.trim()==='+json.dumps(text)+');if(!b)throw Error("missing button");b.click();return true})()')
    def answer(value):
        js('(()=>{const e=document.querySelector(".recall-own textarea");Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(e,'+json.dumps(value)+');e.dispatchEvent(new Event("input",{bubbles:true}));return true})()')
        wait('document.querySelector(".recall-own textarea").value==='+json.dumps(value))
    view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    wait('!!document.querySelector(".recall-question") && !!window.recall70')
    assert not js('document.body.textContent.includes("不应提前展示")')
    assert js('[...document.querySelectorAll("button")].find(e=>e.textContent.includes("写好了")).disabled')
    click('写好了，看参考答案')
    assert js('window.recall70.calls.filter(c=>c.method==="class.recall_reveal").length') == 0
    answer('   ')
    assert js('[...document.querySelectorAll("button")].find(e=>e.textContent.includes("写好了")).disabled')
    answer('接收数据，处理后输出结果。')
    js('window.recall70.control.failReveal=true;true')
    click('写好了，看参考答案'); wait('!!document.querySelector(".recall-error")')
    assert not js('document.querySelector(".recall-own textarea").readOnly')
    assert not js('!!document.querySelector(".recall-reference")')
    js('window.recall70.control.failReveal=false;window.recall70.control.hold=true;true')
    click('写好了，看参考答案');wait('!!window.recall70.control.pending')
    click('写好了，看参考答案')
    assert js('window.recall70.calls.filter(c=>c.method==="class.recall_reveal").length') == 2
    js('window.recall70.control.pending();window.recall70.control.hold=false;true')
    wait('!!document.querySelector(".recall-reference")')
    assert js('document.querySelector(".recall-own textarea").readOnly')
    js('document.querySelector(".recall-own textarea").focus();document.querySelector(".recall-own textarea").select();true')
    QTest.keyClicks(view.focusProxy() or view, 'changed after reveal')
    pump()
    assert js('document.querySelector(".recall-own textarea").value') == '接收数据，处理后输出结果。'
    assert js('window.recall70.calls.filter(c=>c.method==="class.recall_reveal").at(-1).params.answer') == '接收数据，处理后输出结果。'
    js('document.querySelectorAll(".recall-point input")[0].click();true')
    pump(.1);click('算我答错');wait('!!document.querySelector(".recall-result")')
    assert js('document.querySelector(".recall-result").textContent.includes("你选择：算我答错")')
    assert js('document.querySelectorAll(".recall-point.missed").length') == 1
    assert js('[...document.querySelectorAll(".recall-point.missed b")].map(e=>e.textContent).join(",")') == '要点 1'
    assert js('[...document.querySelectorAll(".recall-point input")].every(e=>e.matches(":disabled"))')
    results=[]
    for theme in ('sakura','aurora'):
        for width in (1000,600,390):
            view.resize(width,900);js('window.recall70.theme('+json.dumps(theme)+');true')
            js('window.recall70.collapse('+('true' if width==390 else 'false')+');true')
            wait('innerWidth==='+str(width)+' && document.documentElement.dataset.theme==='+json.dumps(theme))
            pump(.15)
            audit=json.loads(js('JSON.stringify({width:innerWidth,page:document.documentElement.scrollWidth,card:document.querySelector(".recall-question").scrollWidth,client:document.querySelector(".recall-question").clientWidth})'))
            assert audit['page']<=width and audit['card']<=audit['client'], audit
            results.append(dict(audit,theme=theme))
            if shots and width in (1000,390):
                js('document.querySelector(".class-scroll").scrollTop=0;true');pump(.1)
                shots.mkdir(exist_ok=True);view.grab().save(str(shots/f'{theme}-recall-{width}.png'))
    click('下一题'); wait('!!document.querySelector(".class-options")')
    assert not js('!!document.querySelector(".recall-question")')
    click('A键盘');wait('document.body.textContent.includes("✓ 答对了")')
    assert js('JSON.stringify(window.recall70.calls.find(c=>c.method==="classroom.answer").params.value)')=='[0]'
    js('window.recall70.reset("recall70-all");true'); wait('!!document.querySelector(".recall-question")')
    assert not js('!!document.querySelector(".recall-reference")')
    answer('输入、处理、输出。');click('写好了，看参考答案');wait('!!document.querySelector(".recall-reference")')
    assert js('[...document.querySelectorAll(".recall-point input")].every(e=>e.checked)')
    click('算我答对');wait('!!document.querySelector(".recall-result.correct")')
    assert js('document.querySelector(".recall-result").textContent.includes("你选择：算我答对")')
    assert js('JSON.stringify(window.recall70.calls.filter(c=>c.method==="class.recall_submit").at(-1).params.ticked)')=='[0,1,2]'
    (root/'browser-result.json').write_text(json.dumps(dict(ok=True,results=results,calls=json.loads(js('JSON.stringify(window.recall70.calls)'))),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(ok=True,layouts=len(results)),ensure_ascii=False))
    view.close(); page.deleteLater();pump()


def test_recall70_answer_reveal_tick_submit_and_responsive_themes(tmp_path):
    build_fixture(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    root=Path(sys.argv[1]);browser(root,shots=root/'screenshots' if '--shots' in sys.argv else None)
