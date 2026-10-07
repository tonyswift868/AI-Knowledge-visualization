"""Actual Settings page on QWebEngine, connected to production handlers/storage."""
import json, os, subprocess, sys, time
from pathlib import Path


def build(root):
    repo=Path(__file__).resolve().parents[3]
    root.mkdir(parents=True,exist_ok=True)
    (root/'index.html').write_text('<div id="root"></div><script src="qrc:///qtwebchannel/qwebchannel.js"></script><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (root/'entry.tsx').write_text('import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';import '+json.dumps((Path(__file__).parent/'teacher81_browser.tsx').as_posix()),encoding='utf-8')
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((repo/'refactor/learning/web/vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',build:{outDir:"dist",emptyOutDir:true}}',encoding='utf-8')
    deps='D:/MentorFlow/.local/web-deps'
    result=subprocess.run(['D:/MentorFlow/.local/node/node.exe',deps+'/node_modules/vite/bin/vite.js','build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr


def test_teacher81_settings_real_page_and_formal_bridge(tmp_path):
    build(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],env=dict(os.environ,QT_QPA_PLATFORM='windows'),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))['ok']


def browser(root):
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QUrl
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from refactor.learning.app.bridge import AppBridge
    from refactor.learning.app.services import AppServices
    from refactor.learning.app import handlers
    from tests.refactor.app.test_app_window_layer import _Window
    from tests.refactor.teacher81.test_teacher81_presets import ACCOUNT, PLAN, preset
    app=QApplication([])
    services=AppServices(account_id=ACCOUNT,data_root=root,session_provider=lambda:None,api_base_url='https://api.invalid',ca_bundle=root/'unused.pem')
    first=services.settings.teacher_presets()['presets'][0]
    services.settings.save_teacher_presets([first,preset(),preset('teacher-3',name='沉稳老师',style='calm',persona='讲解有条理'),preset('teacher-4',name='严厉老师',style='strict',persona='直截了当')],'teacher-1',services.store)
    services.store.create_plan(PLAN,teacher_preset_id='teacher-2')
    bridge=AppBridge()
    class Window(_Window):
        def settings_changed(self,settings):bridge.push('settings',settings)
    handlers.register(bridge,Window(services))
    view=QWebEngineView();view.resize(1240,920);view.show()
    channel=QWebChannel(view.page());channel.registerObject('mf',bridge);view.page().setWebChannel(channel)
    view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    deadline=time.monotonic()+65;captured=False;result=None
    while time.monotonic()<deadline:
        app.processEvents();time.sleep(.01);pending=[]
        view.page().runJavaScript('JSON.stringify({result:window.result||null,capture:!!window.captureReady})',pending.append)
        while not pending and time.monotonic()<deadline:app.processEvents();time.sleep(.003)
        if not pending or not pending[0]:continue
        state=json.loads(pending[0])
        if state['capture'] and not captured:
            end=time.monotonic()+.5
            while time.monotonic()<end:app.processEvents();time.sleep(.01)
            view.grab().save(str(root/'settings-presets.png'));captured=True
            view.page().runJavaScript('window.captured=true')
        if state['result']:result=state['result'];break
    (root/'browser-result.json').write_text(json.dumps(result or {'ok':False,'error':'timeout'},ensure_ascii=False,indent=2),encoding='utf-8')
    view.close();bridge.close();services.close();app.processEvents()
    assert result and result['ok'],result


if __name__=='__main__':browser(Path(sys.argv[1]))
