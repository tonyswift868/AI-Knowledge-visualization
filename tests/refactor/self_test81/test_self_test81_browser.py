"""Real React Tools/SelfTest + Qt bridge + product store/grading, offline model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]


def build_fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    (root/'index.html').write_text('<div id="root"></div><script src="qrc:///qtwebchannel/qwebchannel.js"></script><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (root/'entry.tsx').write_text('import '+json.dumps((Path(__file__).parent/'self_test81_browser.tsx').as_posix())+';',encoding='utf-8')
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((ROOT/'refactor/learning/web/vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}',encoding='utf-8')
    result=subprocess.run(['D:/MentorFlow/.local/node/node.exe','D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js','build','--config',str(root/'vite.config.mjs')],
        cwd=ROOT,env=dict(os.environ,MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps'),capture_output=True,text=True,encoding='utf-8',timeout=120)
    assert result.returncode==0,result.stdout+result.stderr


def browser(root, output):
    from types import SimpleNamespace
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication, QFileDialog
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from refactor.learning.app.bridge import AppBridge
    from refactor.learning.course.self_test import register
    from test_self_test81_flow import Locator, rows, OBJECTIVE, RECALL, MISSING
    app=QApplication([])
    view=QWebEngineView()
    profile=QWebEngineProfile(view); page=QWebEnginePage(profile,view); view.setPage(page)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ('http','https'): info.block(True)
    interceptor=Offline(page);profile.setUrlRequestInterceptor(interceptor)
    bridge=AppBridge(view)
    gateway=Locator(rows())
    current=SimpleNamespace(account_id='browser81',data_root=root/'data',gateway=gateway)
    register(bridge,lambda:current,view)
    # Import/edit renders the set before its follow-up list request completes.
    # Keep that real request in flight to exercise busy, rather than racing it.
    from dataclasses import replace
    from threading import Event
    listing=bridge._handlers['selftest.list']
    def delayed_list(*args,**kwargs):
        Event().wait(.25)  # Injected backend latency, never a UI readiness wait.
        return listing.function(*args,**kwargs)
    bridge._handlers['selftest.list']=replace(listing,function=delayed_list)
    channel=QWebChannel(page);channel.registerObject('mf',bridge);page.setWebChannel(channel)
    console=[]
    page.javaScriptConsoleMessage=lambda level,message,line,source: console.append(message)
    view.resize(1050,1050);view.show()
    def pump(seconds=.02):
        end=time.monotonic()+seconds
        while time.monotonic()<end: app.processEvents();time.sleep(.003)
    def js(code):
        value=[];page.runJavaScript(code,value.append);end=time.monotonic()+4
        while not value and time.monotonic()<end: pump()
        assert value,'JS timeout'
        return value[0]
    def wait(code):
        end=time.monotonic()+12
        while not js(code):
            assert time.monotonic()<end,(code,console)
            pump()
    def click(text):
        wait('(()=>{const b=[...document.querySelectorAll("button")].find(e=>e.textContent.trim()==='+json.dumps(text)+');return !!b&&!b.disabled})()')
        assert js('(()=>{let b=[...document.querySelectorAll("button")].find(e=>e.textContent.trim()==='+json.dumps(text)+');if(!b||b.disabled)return false;b.click();return true})()'),text
    def fill(selector,text):
        assert js('(()=>{let e=document.querySelector('+json.dumps(selector)+');if(!e)return false;Object.getOwnPropertyDescriptor(e.tagName==="TEXTAREA"?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,"value").set.call(e,'+json.dumps(text)+');e.dispatchEvent(new Event("input",{bubbles:true}));return true})()')
        wait('document.querySelector('+json.dumps(selector)+').value==='+json.dumps(text))
    output.mkdir(parents=True,exist_ok=True)
    def shots(stage):
        for width in (390,1050):
            view.resize(width,1050);wait('innerWidth==='+str(width))
            assert js('document.documentElement.scrollWidth<=innerWidth'),(stage,width)
        for theme in ('sakura','aurora'):
            color='#ab1201' if theme=='sakura' else '#ab1202'
            js('window.selfTest81.theme('+json.dumps(theme)+');(()=>{let e=document.getElementById("frame115");if(!e){e=document.createElement("div");e.id="frame115";document.body.append(e)}e.style.cssText="position:fixed;left:0;top:0;width:2px;height:2px;z-index:99999;background:'+color+'";return true})()')
            deadline=time.monotonic()+5
            while True:
                frame=view.grab()
                if frame.toImage().pixelColor(0,0).name()==color: break
                assert time.monotonic()<deadline,'new theme compositor frame not ready'
                pump()
            assert frame.save(str(output/f'{stage}-{theme}.png'))
    try:
        view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
        wait('!!window.selfTest81 && document.body.textContent.includes("上传题目自测")')
        # Real Tools entry. Paste import -> real model locator + compiler -> DB.
        assert js('(()=>{let b=[...document.querySelectorAll("button")].find(e=>e.classList.contains("tool-card")&&e.textContent.includes("上传题目自测"));b.click();return true})()')
        wait('document.body.textContent.includes("上传或粘贴题目")')
        click('上传或粘贴题目');fill('textarea[aria-label="题目和答案"]',OBJECTIVE+'\n\n'+RECALL+'\n\n'+MISSING)
        click('拆出原题');wait('!!document.querySelector(".self-test-import-result")')
        assert js('document.body.textContent.includes("识别出 2 道题（客观 1、问答 1），另有 1 道没有答案未加入")')
        shots('import')
        fill('.self-test-import-result input','浏览器自测练习');click('保存题集')
        wait('document.body.textContent.includes("保存名称")')
        # Remove and restore a missing answer through product APIs/UI.
        assert js('(()=>{document.querySelector(".self-test-missing details").open=true;return true})()')
        fill('.self-test-missing textarea','5');click('补上答案并加入')
        wait('document.body.textContent.includes("识别出 3 道题（客观 2、问答 1），另有 0 道没有答案未加入")')
        assert js('(()=>{let e=document.querySelector("select");Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set.call(e,"3");e.dispatchEvent(new Event("change",{bubbles:true}));return true})()')
        click('开始自测');wait('!!document.querySelector(".self-test-current")')
        assert js('document.body.textContent.includes("每题连续答对 3 遍")')
        shots('testing')
        # Exit and resume one real pending attempt; no new round.
        click('返回题集');wait('!!document.querySelector(".self-test-sets")')
        click('接着做');wait('document.body.textContent.includes("接着做") && !!document.querySelector(".self-test-import-result")')
        click('接着做');wait('!!document.querySelector(".self-test-current")')
        turns=0
        keypoint_shots=False
        while not js('!!document.querySelector(".self-test-complete")'):
            turns+=1;assert turns<30
            wait('!!document.querySelector(".self-test-current")')
            if js('!!document.querySelector(".recall-question")'):
                assert not js('!!document.querySelector(".recall-reference")')
                fill('.recall-own textarea','队列先进先出，队尾加入，队首移除。')
                click('写好了，看参考答案');wait('!!document.querySelector(".recall-reference")')
                if not keypoint_shots:
                    shots('keypoints');keypoint_shots=True
                js('document.querySelectorAll(".recall-point input").forEach(e=>e.click());true')
                wait('[...document.querySelectorAll(".recall-point input")].every(e=>e.checked)')
                click('提交')
            elif js('!!document.querySelector("input[type=radio]")'):
                js('document.querySelector("input[type=radio]").click();true');click('提交答案')
            else:
                fill('.self-test-objective input','5');click('提交答案')
            wait('!!document.querySelector(".self-test-verdict")')
            assert js('document.querySelector(".self-test-verdict").textContent.includes("答对了")')
            click('继续');wait('!document.querySelector(".self-test-verdict") || !!document.querySelector(".self-test-complete")')
        shots('completed')
        assert js('document.body.textContent.includes("答对 3 次 · 答错 0 次")')
        # Exercise upload via the actual UI file-token path and document importer.
        click('返回题集');wait('!!document.querySelector(".self-test-sets")')
        upload=root/'upload.md';upload.write_text(OBJECTIVE+'\n\n'+RECALL+'\n\n'+MISSING,encoding='utf-8')
        QFileDialog.getOpenFileName=lambda *args,**kwargs:(str(upload),'')
        click('上传或粘贴题目');click('选择文件');wait('document.body.textContent.includes("upload.md")')
        click('拆出原题');wait('!!document.querySelector(".self-test-import-result")')
        assert js('document.body.textContent.includes("识别出 2 道题")')
        assert not any('Error:' in message or 'Uncaught' in message for message in console),console
        result=dict(ok=True,turns=turns,themes=['sakura','aurora'],screenshots=8,model='offline literal locator',gateway_calls=len(gateway.calls),console=console)
        (output/'browser-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
    finally:
        bridge.close();view.close();page.setWebChannel(None);pump(.1)


def test_self_test81_real_browser_and_six_screenshots(tmp_path):
    build_fixture(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),'--browser',str(tmp_path),str(Path(os.environ.get('MENTORFLOW_TEST_ROOT',str(tmp_path)))/'screenshots')],
        cwd=ROOT,env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu',PYTHONPATH=str(ROOT)),
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    browser(Path(sys.argv[2]),Path(sys.argv[3]))
