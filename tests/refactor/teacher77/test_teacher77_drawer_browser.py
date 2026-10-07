"""Isolated real App/Classroom/Pixi/Live2D, local read-only assets, fake lesson/voice bridge."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))


def build_fixture(root):
    from refactor.learning.app.teacher_assets import TeacherAssetCatalog, PUBLIC_ROOT
    repo=Path(__file__).resolve().parents[3];here=Path(__file__).parent
    assets=TeacherAssetCatalog(PUBLIC_ROOT).status()
    if not assets['ready']:
        import pytest
        pytest.skip('Real local Tsubaki assets unavailable; no replacement/download')
    root.mkdir(parents=True,exist_ok=True)
    (root/'assets.json').write_text(json.dumps(assets,ensure_ascii=False),encoding='utf-8')
    (root/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (root/'entry.tsx').write_text('import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';import assets from "./assets.json";window.teacher77Assets=assets;await import('+json.dumps((here/'teacher77_browser.tsx').as_posix())+');',encoding='utf-8')
    bridge=json.dumps((here/'teacher77_bridge.ts').as_posix())
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((repo/'refactor/learning/web/vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',resolve:{alias:[{find:"../bridge",replacement:'+bridge+'},{find:"./bridge",replacement:'+bridge+'}]},build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}',encoding='utf-8')
    deps=Path('D:/MentorFlow/.local/web-deps')
    result=subprocess.run(['D:/MentorFlow/.local/node/node.exe',str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps.as_posix()),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr


def browser(root,shots=None):
    os.environ['QT_QPA_PLATFORM']='windows'
    os.environ['QSG_RHI_BACKEND']='opengl'
    os.environ.pop('QTWEBENGINE_CHROMIUM_FLAGS',None)
    from refactor.learning.app.teacher_assets import TeacherAssetCatalog,TeacherAssets,PUBLIC_ROOT
    from PySide6.QtCore import QUrl,QPoint,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineUrlRequestInterceptor
    app=QApplication([]);view=QWebEngineView();view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);profile=QWebEngineProfile(view)
    class Page(QWebEnginePage):
        def javaScriptConsoleMessage(self,level,message,line,source):
            if level.name=='ErrorMessageLevel':print('JS ERROR:',message,flush=True)
    page=Page(profile,view);view.setPage(page)
    catalog=TeacherAssetCatalog(PUBLIC_ROOT);assert catalog.status()['ready']
    assets=TeacherAssets(profile,catalog=catalog)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            url=info.requestUrl()
            if url.scheme() in ('http','https') or (url.scheme()=='mentorflow' and not assets.allows(url)):info.block(True)
    interceptor=Offline(page);profile.setUrlRequestInterceptor(interceptor)
    view.resize(1280,950);view.show()
    def pump(seconds=.02):
        end=time.monotonic()+seconds
        while time.monotonic()<end:app.processEvents();time.sleep(.003)
    def js(code):
        values=[];page.runJavaScript(code,values.append);end=time.monotonic()+5
        while not values and time.monotonic()<end:pump()
        assert values,'JS timeout'
        return values[0]
    def wait(code):
        end=time.monotonic()+60
        while not js(code):
            assert time.monotonic()<end,(code,js('document.body.innerText'))
            assert not js('document.querySelector(".teacher-stage")?.dataset.teacherStatus==="error"'),js('document.body.innerText')
            pump()
    def click(label):
        js('document.querySelector('+json.dumps('[aria-label="'+label+'"]')+').click();true')
        pump()
    def snapshot():return json.loads(js('JSON.stringify(window.teacher77.snapshot())'))
    def rect(selector):return json.loads(js('JSON.stringify((()=>{const r=document.querySelector('+json.dumps(selector)+').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}})())'))
    def drag(selector,dx,dy):
        box=rect(selector);point=QPoint(round(box['x']+box['width']/2),round(box['y']+box['height']/2))
        target=view.focusProxy() or view
        QTest.mousePress(target,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        QTest.mouseMove(target,point+QPoint(dx,dy),10);pump()
        QTest.mouseRelease(target,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point+QPoint(dx,dy));pump()

    def bound():
        pagebox=rect('.class-page');rail=rect('.teacher-rail');panel=rect('.teacher-panel');main=rect('.class-main')
        assert abs(rail['height']-pagebox['height'])<1 and abs(panel['height']-pagebox['height'])<1
        assert main['right']<=rail['x'] and rail['right']<=pagebox['right']+1
        assert not js('document.documentElement.scrollWidth>innerWidth')
        return rail
    results={}
    view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    wait('!!window.teacher77 && document.querySelector(".teacher-stage")?.dataset.teacherStatus==="ready"')
    assert js('!!document.querySelector(".teacher-edge") && !document.querySelector(".teacher-avatar") && !document.querySelector(".teacher-window")')
    assert bound()['width']==32
    assert rect('.class-main')['width']==rect('.class-page')['width']-40
    assert js('[...document.querySelectorAll(".class-main button")].every(el=>el.getBoundingClientRect().right<=document.querySelector(".teacher-rail").getBoundingClientRect().left)')
    first=snapshot();pump(.2);assert snapshot()['frames']==first['frames'] and not first['active']
    results['default_32px_edge_no_overlap_paused']=first
    click('展开老师');wait('window.teacher77.snapshot().active && window.teacher77.snapshot().frames>0')
    before=bound();drag('.teacher-panel-resize',-80,0);after=bound();assert after['width']>before['width']
    drag('.teacher-panel-resize',-2000,0);assert bound()['width']<=520
    drag('.teacher-panel-resize',2000,0);assert bound()['width']>=240
    drag('.teacher-panel-resize',-80,0)
    drag('.teacher-stage-canvas',30,20);wait('window.teacher77.snapshot().view.panX>0')
    js('document.querySelector(".teacher-stage-canvas").dispatchEvent(new WheelEvent("wheel",{deltaY:-300,bubbles:true,cancelable:true}));true')
    wait('window.teacher77.snapshot().view.scale>1')
    model=snapshot()['view'];width=bound()['width'];results['model_pan_zoom']=model
    click('收起老师');wait('!window.teacher77.snapshot().active');paused=snapshot()['frames'];pump(.2);assert snapshot()['frames']==paused
    js('window.teacher77.reopen();true');wait('document.querySelector(".teacher-stage")?.dataset.teacherStatus==="ready" && !!document.querySelector(".teacher-edge")')
    assert snapshot()['view']==model
    click('展开老师');wait('window.teacher77.snapshot().active');assert bound()['width']==width
    js('window.teacher77.reopen();true');wait('document.querySelector(".teacher-stage")?.dataset.teacherStatus==="ready" && !document.querySelector(".teacher-edge")')
    assert snapshot()['view']==model and bound()['width']==width
    results['reopened_width_state_model']=dict(width=width,view=model)
    js('document.querySelector(".teacher-stage-canvas").dispatchEvent(new MouseEvent("dblclick",{bubbles:true}));true')
    wait('window.teacher77.snapshot().view.scale===1 && window.teacher77.snapshot().view.panX===0')
    # True physics evaluation counter and parameter samples from before core model update.
    wait('window.teacher77.snapshot().physicsEnabled && window.teacher77.snapshot().physicsEvaluations>0')
    box=rect('.teacher-stage-canvas');target=view.focusProxy() or view
    QTest.mouseMove(target,QPoint(round(box['right']-5),round(box['y']+box['height']*.35)));pump(.5)
    js('window.dispatchEvent(new PointerEvent("pointermove",{clientX:'+str(box['right']-5)+',clientY:'+str(box['y']+box['height']*.35)+'}));true');pump(.5)
    phys1=snapshot();assert phys1['focus']['x']>0,phys1
    if shots:shots.mkdir(exist_ok=True);view.grab().save(str(shots/'physics-right.png'))
    QTest.mouseMove(target,QPoint(350,round(box['y']+box['height']*.7)));pump(.5)
    js('window.dispatchEvent(new PointerEvent("pointermove",{clientX:350,clientY:'+str(box['y']+box['height']*.7)+'}));true');pump(.5)
    phys2=snapshot();assert phys2['focus']['x']<0 and phys2['physicsEvaluations']>phys1['physicsEvaluations']
    different=[name for name,value in phys1['physicsValues'].items() if abs(value-phys2['physicsValues'].get(name,value))>.001]
    assert different,(phys1,phys2)
    if shots:view.grab().save(str(shots/'physics-left.png'))
    results['physics_samples']=dict(first=phys1,second=phys2,changed=different)
    events=[]
    for name,expected in (('greet','blush'),('nod','heart-eyes'),('think',None)):
        if name=='think':pump(4)
        js('window.teacher77.react('+json.dumps(name)+');true');pump(.35)
        if expected:
            wait('window.teacher77.snapshot().expression==='+json.dumps(expected))
            wait('window.teacher77.rendered()['+json.dumps('Button2' if name=='greet' else 'Button7')+']>.8')
        else:assert snapshot()['reaction']=='nod' and snapshot()['expression'] is None
        events.append(dict(event=name,snapshot=snapshot(),rendered=json.loads(js('JSON.stringify(window.teacher77.rendered())'))))
        if shots:view.grab().save(str(shots/f'reaction-{name}.png'))
    assert not any(p['name'] in ('cry','Cry','dark-face','no-highlights') for p in snapshot()['playback'])
    results['greeting_correct_encouragement']=events
    # Reduced motion disables physics and tracking, but keeps real TTS mouth updates.
    js('window.teacher77.reduced(true);true');wait('!window.teacher77.snapshot().physicsEnabled')
    reduced=snapshot()['physicsEvaluations'];pump(.2);assert snapshot()['physicsEvaluations']==reduced
    QTest.mouseMove(target,QPoint(100,100));pump(.3);assert abs(snapshot()['focus']['x'])<.001
    assert js('getComputedStyle(document.querySelector(".teacher-panel")).transitionDuration==="0s"')
    js('window.teacher77.react("nod");true');assert snapshot()['reaction'] is None
    js('window.teacher77.mouth(.7);true');pump(.3);assert snapshot()['mouth']>.5
    js('window.teacher77.mouth(0);true')
    results['reduced_physics_tracking_reactions_off']=True
    js('window.teacher77.reduced(false);true');wait('window.teacher77.snapshot().physicsEnabled')
    js('document.querySelector(".class-voice button").click();document.querySelector(".listen").click();true')
    wait('window.teacher77.log.some(c=>c.method==="speech.speak")');pump(.4)
    assert snapshot()['focus']['x']<0
    stops=js('window.teacher77.log.filter(c=>c.method==="speech.stop").length')
    click('收起老师');wait('!window.teacher77.snapshot().active');pump(.1)
    assert js('window.teacher77.log.filter(c=>c.method==="speech.stop").length')==stops
    click('展开老师');wait('!document.querySelector(".teacher-edge")')
    assert js('document.querySelector(".class-voice").textContent.includes("老师正在朗读")')
    results['speaking_looks_at_dialogue_collapse_does_not_stop']=True
    for theme in ('sakura','aurora'):
        view.resize(1280,950);js('window.teacher77.theme('+json.dumps(theme)+');true');pump(.3);bound()
        if shots:view.grab().save(str(shots/f'{theme}-expanded.png'))
        click('收起老师');wait('!window.teacher77.snapshot().active');pump(.3);bound()
        if shots:view.grab().save(str(shots/f'{theme}-collapsed.png'))
        click('展开老师')
    for width,height in ((600,800),(390,640),(1280,950)):
        js('window.teacher77.collapse(true);true');view.resize(width,height);pump(.3);bound()
        click('收起老师');pump(.2);assert bound()['width']==32
        click('展开老师');pump(.3);bound()
        js('(()=>{const composer=document.querySelector(".class-main .composer");composer.scrollTop=composer.scrollHeight;return true})()')
        assert js('(()=>{const composer=document.querySelector(".class-main .composer").getBoundingClientRect();const send=[...document.querySelectorAll(".class-main .composer button")].find(b=>b.textContent.includes("发送")).getBoundingClientRect();return send.bottom<=composer.bottom && send.right<=document.querySelector(".teacher-rail").getBoundingClientRect().left})()')
        if shots:view.grab().save(str(shots/f'width-{width}-expanded.png'))
    view.showFullScreen();pump(.2);bound();view.showNormal();pump(.2);bound()
    results['responsive_fullscreen_bounds']=True
    (root/'browser-result.json').write_text(json.dumps(dict(ok=True,real_live2d=True,results=results),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(ok=True,checks=list(results)),ensure_ascii=False))
    view.close();page.deleteLater();pump()


def test_teacher77_real_drawer_physics_and_expression_lifecycle(tmp_path):
    build_fixture(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=180)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    root=Path(sys.argv[1]);browser(root,shots=root/'screenshots' if '--shots' in sys.argv else None)
