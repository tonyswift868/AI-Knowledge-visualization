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
    (root/'entry.tsx').write_text('import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';import assets from "./assets.json";window.teacher76Assets=assets;await import('+json.dumps((here/'teacher76_browser.tsx').as_posix())+');',encoding='utf-8')
    bridge=json.dumps((here/'teacher76_bridge.ts').as_posix())
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
    def snapshot():return json.loads(js('JSON.stringify(window.teacher76.snapshot())'))
    def rect(selector):return json.loads(js('JSON.stringify((()=>{const r=document.querySelector('+json.dumps(selector)+').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}})())'))
    def drag(selector,dx,dy):
        box=rect(selector);point=QPoint(round(box['x']+box['width']/2),round(box['y']+box['height']/2))
        target=view.focusProxy() or view
        QTest.mousePress(target,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        QTest.mouseMove(target,point+QPoint(dx,dy),10);pump()
        QTest.mouseRelease(target,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point+QPoint(dx,dy));pump()
    def bound():
        box=rect('.teacher-window');composer=rect('.composer')
        assert box['x']>=7 and box['y']>=7 and box['right']<=js('innerWidth')-7 and box['bottom']<=composer['y']-7,(box,composer)
        return box
    results={}
    view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    wait('!!window.teacher76 && document.querySelector(".teacher-stage")?.dataset.teacherStatus==="ready"')
    assert js('!!document.querySelector(".teacher-avatar") && document.querySelector(".teacher-window").hidden')
    assert rect('.class-main')['width']==rect('.class-page')['width']
    first=snapshot();pump(.2);assert snapshot()['frames']==first['frames'] and not first['active']
    wait('!!document.querySelector(".teacher-avatar img")')
    results['default_closed_full_width_and_paused']=first
    click('展开老师');wait('window.teacher76.snapshot().active && window.teacher76.snapshot().frames>0')
    assert js('getComputedStyle(document.querySelector(".teacher-window")).animationName==="none"')
    before=bound();drag('.teacher-window-handle',-200,-60);after=bound();assert after['x']<before['x'] and after['y']<before['y']
    before=after;drag('.teacher-window-resize',80,40);after=bound();assert after['width']>before['width'] and after['height']>before['height']
    drag('.teacher-stage-canvas',30,20);wait('window.teacher76.snapshot().view.panX>0')
    js('document.querySelector(".teacher-stage-canvas").dispatchEvent(new WheelEvent("wheel",{deltaY:-300,bubbles:true,cancelable:true}));true')
    wait('window.teacher76.snapshot().view.scale>1')
    model=snapshot()['view'];results['model_pan_zoom']=model
    geometry=bound();click('收起老师');wait('!window.teacher76.snapshot().active')
    paused=snapshot()['frames'];pump(.2);assert snapshot()['frames']==paused
    results['collapsed_frames_stopped']=paused
    js('window.teacher76.reopen();true');wait('document.querySelector(".teacher-stage").dataset.teacherStatus==="ready" && !!document.querySelector(".teacher-avatar")')
    assert snapshot()['view']==model
    click('展开老师');wait('window.teacher76.snapshot().active');assert bound()==geometry
    js('window.teacher76.reopen();true');wait('document.querySelector(".teacher-stage").dataset.teacherStatus==="ready" && !document.querySelector(".teacher-window").hidden')
    assert snapshot()['view']==model and bound()==geometry
    results['reopened_geometry_and_model']=dict(geometry=geometry,view=model)
    js('document.querySelector(".teacher-stage-canvas").dispatchEvent(new MouseEvent("dblclick",{bubbles:true}));true')
    wait('window.teacher76.snapshot().view.scale===1 && window.teacher76.snapshot().view.panX===0 && window.teacher76.snapshot().view.panY===0')
    # Captured pointer events deliberately try to move/resize beyond both edges.
    drag('.teacher-window-handle',-2000,-2000);bound()
    drag('.teacher-window-handle',2000,2000);bound()
    drag('.teacher-window-resize',2000,2000);box=bound();assert box['width']<=560 and box['height']<=720
    drag('.teacher-window-resize',-2000,-2000);box=bound();assert box['width']>=220 and box['height']>=280
    results['drag_resize_limits']=box
    # Speech remains independent of stage visibility, using explicit fake playback events.
    js('document.querySelector(".class-voice button").click();true')
    js('document.querySelector(".listen").click();true');wait('window.teacher76.log.some(c=>c.method==="speech.speak")')
    stops=js('window.teacher76.log.filter(c=>c.method==="speech.stop").length')
    click('收起老师');wait('!window.teacher76.snapshot().active');pump(.1)
    assert js('window.teacher76.log.filter(c=>c.method==="speech.stop").length')==stops
    click('展开老师');wait('!document.querySelector(".teacher-window").hidden')
    assert js('document.querySelector(".class-voice").textContent.includes("老师正在朗读")')
    results['speech_not_stopped_on_collapse']=True
    drag('.teacher-window-resize',60,120)
    for theme in ('sakura','aurora'):
        view.resize(1280,950);js('window.teacher76.theme('+json.dumps(theme)+');true');pump(.1)
        # Put the ordinary 280 x 400 view below the app header for screenshots.
        drag('.teacher-window-handle',2000,-2000);bound()
        drag('.teacher-window-handle',0,80);bound()
        if shots:
            shots.mkdir(exist_ok=True);view.grab().save(str(shots/f'{theme}-expanded.png'))
        click('收起老师');wait('!window.teacher76.snapshot().active');pump(.1)
        avatar=rect('.teacher-avatar');assert avatar['bottom']<=rect('.composer')['y']-7
        if shots:view.grab().save(str(shots/f'{theme}-collapsed.png'))
        click('展开老师')
    for width,height in ((600,800),(390,640),(1280,950)):
        js('window.teacher76.collapse(true);true');view.resize(width,height);pump(.2);bound()
        assert not js('document.documentElement.scrollWidth>innerWidth')
    results['window_resize_stays_visible_and_above_input']=True
    view.showFullScreen();pump(.2);bound();view.showNormal();pump(.2);bound()
    results['fullscreen_bounds']=True
    (root/'browser-result.json').write_text(json.dumps(dict(ok=True,real_live2d=True,results=results),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(ok=True,checks=list(results)),ensure_ascii=False))
    view.close();page.deleteLater();pump()


def test_teacher76_real_floating_window_and_model_lifecycle(tmp_path):
    build_fixture(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=150)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    root=Path(sys.argv[1]);browser(root,shots=root/'screenshots' if '--shots' in sys.argv else None)
