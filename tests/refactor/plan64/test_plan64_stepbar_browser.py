"""Resize the real App/NewPlan in offline Qt Chromium, including its sidebar."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def build_fixture(root):
    repo=Path(__file__).resolve().parents[3]; here=Path(__file__).parent
    root.mkdir(parents=True,exist_ok=True)
    draft=dict(plan_id='step64', name='计算机与 AI 术语入门', goal='零基础，一周，用生活化例子理解常用概念。',
               form=None,phase='material_preview',busy=None,sources=[],generation={},outline=None,
               messages=[dict(id='hello',role='ai',kind='text',text='资料已选好，接下来按资料确认学习结构。')])
    (root/'fixture.json').write_text(json.dumps(draft,ensure_ascii=False),encoding='utf-8')
    (root/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (root/'entry.tsx').write_text('import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';import data from "./fixture.json";window.step64Data=data;await import('+json.dumps((here/'step64_browser.tsx').as_posix())+');',encoding='utf-8')
    bridge=json.dumps((here/'step64_bridge.ts').as_posix())
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((repo/'refactor/learning/web/vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',resolve:{alias:[{find:"../bridge",replacement:'+bridge+'},{find:"./bridge",replacement:'+bridge+'}]},build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}',encoding='utf-8')
    deps=Path('D:/MentorFlow/.local/web-deps')
    result=subprocess.run(['D:/MentorFlow/.local/node/node.exe',str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps.as_posix()),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr


def browser(root, before=False, shots=None):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
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
    view.resize(1280,900);view.show()
    def pump(seconds=.02):
        end=time.monotonic()+seconds
        while time.monotonic()<end:app.processEvents();time.sleep(.003)
    def js(code):
        values=[];page.runJavaScript(code,values.append);end=time.monotonic()+4
        while not values and time.monotonic()<end:pump()
        assert values,'JS timeout'
        return values[0]
    def wait(code):
        end=time.monotonic()+10
        while not js(code):
            assert time.monotonic()<end,code
            pump()
    view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    wait('!!window.step64Audit && document.querySelectorAll(".step-dot").length===6')
    results=[]
    for theme in ('sakura','aurora'):
        for width in (1280,1000,800,600):
            view.resize(width,900)
            js('window.step64Show('+json.dumps(theme)+',"material_preview");true')
            wait('innerWidth==='+str(width)+' && document.documentElement.dataset.theme==='+json.dumps(theme))
            pump(.1)  # Paint only; assertions below use measured layout, not elapsed-time success.
            audit=json.loads(js('JSON.stringify(window.step64Audit())'))
            audit['theme']=theme
            if before: assert audit['rows']==2,audit
            else: validate(audit)
            results.append(audit)
            if shots:
                shots.mkdir(exist_ok=True)
                view.grab().save(str(shots/f'{theme}-{width}.png'))
    if not before:
        for index, phase in enumerate(('questions','sources','material_preview','outline','generating','ready'),1):
            for width in (1280,600):
                view.resize(width,900)
                js('window.step64Show("sakura",'+json.dumps(phase)+');true')
                wait('innerWidth==='+str(width)+' && document.querySelector(".step-dot.on").title.includes('+json.dumps(f'第 {index} 步')+') && document.querySelector(".step-dot.on").getAttribute("aria-current")==="step"')
                pump()
                audit=json.loads(js('JSON.stringify(window.step64Audit())'));validate(audit)
                results.append(dict(audit,phase=phase))
        # A collapsed sidebar gives a wider content area; container queries follow it.
        view.resize(1000,900);js('window.step64Show("aurora","ready",true,1.4);true')
        wait('document.querySelector(".sidebar.collapsed") && innerWidth===1000')
        pump(.35)
        audit=json.loads(js('JSON.stringify(window.step64Audit())'));validate(audit)
        results.append(dict(audit,collapsed=True,scale=1.4))
    (root/'browser-result.json').write_text(json.dumps(dict(ok=True,before=before,results=results),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(ok=True,before=before,scenarios=len(results)),ensure_ascii=False))
    view.close();page.deleteLater();pump()


def validate(audit):
    assert len(audit['circles'])==6 and audit['rows']==1,audit
    assert not any(audit[key] for key in ('page_overflow','header_overflow','track_overflow')),audit
    assert audit['active_aria']=='step' and all(audit['step_titles']),audit
    head=audit['header'];track=audit['track']
    for circle in audit['circles']:
        assert circle['x']>=head['x'] and circle['right']<=head['right']+.5,audit
        assert circle['y']>=track['y'] and circle['bottom']<=head['bottom'],audit
    assert audit['title']['right']<=audit['abandon']['x']+.5,audit
    assert track['y']>=max(audit['title']['bottom'],audit['abandon']['bottom']),audit
    if audit['current']:
        assert audit['active_label'] in audit['current']['text'],audit
        assert audit['visible_labels']==0,audit
    else: assert audit['visible_labels']==6,audit


def test_plan64_stepbar_four_widths_two_themes_and_each_active_step(tmp_path):
    build_fixture(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    root=Path(sys.argv[1])
    browser(root,before='--before' in sys.argv,shots=root/'screenshots' if '--shots' in sys.argv else None)
