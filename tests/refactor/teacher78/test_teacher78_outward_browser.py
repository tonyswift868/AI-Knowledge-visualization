"""Real native QWidget/QWebChannel/renderer; fake lesson, no network or account access."""
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
    assert assets['ready']
    root.mkdir(parents=True,exist_ok=True)
    (root/'assets.json').write_text(json.dumps(assets,ensure_ascii=False),encoding='utf-8')
    (root/'index.html').write_text('<div id="root"></div><script src="qrc:///qtwebchannel/qwebchannel.js"></script><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    setup='const native=await new Promise(resolve=>new QWebChannel(qt.webChannelTransport,c=>resolve(c.objects.teacherWindow)));window.teacher78Native=params=>new Promise((resolve,reject)=>native.action(JSON.stringify(params),s=>{const value=JSON.parse(s);value.error?reject(new Error(value.error)):resolve(value)}));'
    (root/'entry.tsx').write_text('import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';import assets from "./assets.json";window.teacher78Assets=assets;'+setup+'await import('+json.dumps((here/'teacher78_browser.tsx').as_posix())+');window.teacher78NativeEvent=value=>window.teacher78.emit("teacher_window",value);',encoding='utf-8')
    bridge=json.dumps((here/'teacher78_bridge.ts').as_posix())
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((repo/'refactor/learning/web/vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',resolve:{alias:[{find:"../bridge",replacement:'+bridge+'},{find:"./bridge",replacement:'+bridge+'}]},build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}',encoding='utf-8')
    deps=Path('D:/MentorFlow/.local/web-deps')
    result=subprocess.run(['D:/MentorFlow/.local/node/node.exe',str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps.as_posix()),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr


def browser(root,shots=False):
    os.environ['QT_QPA_PLATFORM']='windows'
    os.environ['QSG_RHI_BACKEND']='opengl'
    os.environ.pop('QTWEBENGINE_CHROMIUM_FLAGS',None)
    from PySide6.QtCore import QUrl,QPoint,Qt,QObject,Slot,QRect
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineUrlRequestInterceptor
    from refactor.learning.app.teacher_assets import TeacherAssetCatalog,TeacherAssets,PUBLIC_ROOT
    from refactor.learning.app.window import _TeacherDrawer
    from refactor.learning.preferences.storage import AccountJSON
    app=QApplication([]);view=QWebEngineView();view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    view.setMinimumSize(1100,720)
    screen=app.primaryScreen().availableGeometry()
    view.setGeometry(screen.x()+32,screen.y()+32,1280,min(920,screen.height()-90))
    profile=QWebEngineProfile(view)
    class Page(QWebEnginePage):
        def javaScriptConsoleMessage(self,level,message,line,source):
            if level.name=='ErrorMessageLevel':print('JS ERROR:',message,flush=True)
    page=Page(profile,view);view.setPage(page)
    catalog=TeacherAssetCatalog(PUBLIC_ROOT)
    assert catalog.status()['ready']
    assets=TeacherAssets(profile,catalog=catalog)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            url=info.requestUrl()
            if url.scheme() in ('http','https') or (url.scheme()=='mentorflow' and not assets.allows(url)):info.block(True)
    interceptor=Offline(page);profile.setUrlRequestInterceptor(interceptor)
    drawer=_TeacherDrawer(view,AccountJSON('teacher78-browser',root,'teacher-drawer.json'),
        lambda value:page.runJavaScript('window.teacher78NativeEvent?.('+json.dumps(value)+')'))
    native_calls=[]
    class Native(QObject):
        @Slot(str,result=str)
        def action(self,text):
            params=json.loads(text);native_calls.append(params)
            try:return json.dumps({'teacher':drawer.action(params['action'],params)})
            except Exception as error:return json.dumps({'error':str(error)})
    native=Native(page);channel=QWebChannel(page);channel.registerObject('teacherWindow',native);page.setWebChannel(channel)
    view.show()
    def pump(seconds=.02):
        end=time.monotonic()+seconds
        while time.monotonic()<end:app.processEvents();time.sleep(.003)
    def js(code):
        values=[];page.runJavaScript(code,values.append);end=time.monotonic()+5
        while not values and time.monotonic()<end:pump()
        assert values,'JS timeout'
        return values[0]
    def wait(code):
        end=time.monotonic()+45
        while not js(code):
            assert time.monotonic()<end,(code,js('document.body.innerText'))
            pump()
    def rect(selector):return json.loads(js('JSON.stringify((()=>{const r=document.querySelector('+json.dumps(selector)+').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}})())'))
    def click(label):js('document.querySelector('+json.dumps('[aria-label="'+label+'"]')+').click();true');pump(.15)
    def drag(selector,dx):
        box=rect(selector);point=QPoint(round(box['x']+box['width']/2),round(box['y']+box['height']/2));target=view.focusProxy() or view
        QTest.mousePress(target,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        QTest.mouseMove(target,point+QPoint(dx,0),10);pump(.15)
        QTest.mouseRelease(target,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point+QPoint(dx,0));pump(.2)
    def shot(name):
        if shots:
            (root/'screenshots').mkdir(exist_ok=True)
            view.grab().save(str(root/'screenshots'/name))
    view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    wait('!!window.teacher78 && document.querySelector(".teacher-stage")?.dataset.teacherStatus==="ready" && !document.querySelector(".teacher-edge").disabled')
    before=QRect(view.geometry());chat=rect('.class-main');shot('ordinary-collapsed.png')
    click('展开老师');wait('document.querySelector(".teacher-rail").dataset.windowMode==="external" && !document.querySelector(".teacher-edge")')
    pump(.2)
    assert view.width()==before.width()+288
    assert abs(rect('.class-main')['width']-chat['width'])<1
    assert abs(rect('.teacher-panel')['height']-rect('.class-page')['height'])<1
    opened=QRect(view.geometry());opened_chat_width=rect('.class-main')['width'];shot('ordinary-expanded.png');shot('sakura-background.png')
    drag('.teacher-panel-resize',-60)
    assert view.width()>opened.width()
    assert abs(rect('.class-main')['width']-chat['width'])<1
    width=drawer.width
    js('window.teacher78.theme("aurora");true');pump(.2);shot('aurora-background.png')
    click('收起老师');wait('!!document.querySelector(".teacher-edge")')
    assert view.geometry()==before
    paused=js('window.teacher78.snapshot().frames');pump(.2);assert js('window.teacher78.snapshot().frames')==paused
    # Fresh page (harness clears localStorage) restores from native account-scoped JSON.
    click('展开老师');wait('!document.querySelector(".teacher-edge")')
    js('document.querySelector(".teacher-stage-canvas").dispatchEvent(new WheelEvent("wheel",{deltaY:-150,bubbles:true,cancelable:true}));true')
    wait('window.teacher78.snapshot().view.scale>1')
    pump(.2);saved_view=dict(drawer.view)
    loaded=[]
    page.loadFinished.connect(loaded.append)
    view.reload()
    deadline=time.monotonic()+45
    while not loaded:
        assert time.monotonic()<deadline,'reload timeout'
        pump()
    assert loaded[-1]
    wait('!!window.teacher78 && document.querySelector(".teacher-stage")?.dataset.teacherStatus==="ready" && !document.querySelector(".teacher-edge")')
    assert drawer.width==width and js('window.teacher78.snapshot().view.scale')==saved_view['scale']
    assert view.width()==before.width()+width-32
    click('收起老师');wait('!!document.querySelector(".teacher-edge")')
    view.showMaximized();pump(.2)
    maximum=QRect(view.geometry());max_chat=rect('.class-main')['width']
    click('展开老师');wait('!document.querySelector(".teacher-edge")')
    assert drawer.mode=='internal' and view.geometry()==maximum
    assert rect('.class-main')['width']<max_chat
    click('收起老师');wait('!!document.querySelector(".teacher-edge")');assert view.geometry()==maximum
    view.showNormal();pump(.2);assert view.geometry()==before
    assert any(p['action']=='teacher_open' and not p.get('restore') for p in native_calls)
    assert any(p['action']=='teacher_close' and not p.get('preserve') for p in native_calls)
    result=dict(ok=True,real_native_window=True,real_live2d=True,closed=[before.x(),before.y(),before.width(),before.height()],
        opened=[opened.x(),opened.y(),opened.width(),opened.height()],closed_chat_width=chat['width'],opened_chat_width=opened_chat_width,
        resized_drawer_width=width,native_calls=native_calls,saved_view=saved_view,screen=[screen.x(),screen.y(),screen.width(),screen.height()])
    (root/'browser-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    view.close();page.deleteLater();pump()


def test_teacher78_native_window_outward_bridge_and_background(tmp_path):
    build_fixture(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=180)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    browser(Path(sys.argv[1]),shots='--shots' in sys.argv)
