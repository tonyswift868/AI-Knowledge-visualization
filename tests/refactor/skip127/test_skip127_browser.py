"""Native CDP input into real React/Chromium; fake course bridge, no OS input."""
import json,os,socket,subprocess,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
REPO=Path(__file__).resolve().parents[3]
NODE='D:/MentorFlow/.local/node/node.exe'

def build127(directory):
    from refactor.learning.app.classroom import Classroom
    from tests.refactor.concept114.test_concept114_classroom import concept_environment
    from tests.refactor.conversation37.test_grounded_conversation37 import act
    directory.mkdir(parents=True,exist_ok=True);here=Path(__file__).parent
    e=concept_environment(directory/'data')
    try:
        with patch.object(Classroom,'_materials',staticmethod(lambda:e.materials)):state=act(e,'next')
        (directory/'states119.json').write_text(json.dumps(dict(first=state),ensure_ascii=False),encoding='utf-8')
    finally:e.room.cancel_materials();e.store.close()
    deps=Path('D:/MentorFlow/.local/web-deps');optional=here.parent/'conversation37/conversation37_optional.tsx'
    aliases=[dict(find='../bridge',replacement=(here/'bridge127.ts').as_posix()),dict(find='./states119.json',replacement=(directory/'states119.json').as_posix())]
    aliases.extend(dict(find=k,replacement=optional.as_posix()) for k in ('../teacher/TeacherStage','../voice'))
    (directory/'entry.tsx').write_text('import '+json.dumps((here/'browser127.tsx').as_posix()),encoding='utf-8')
    (directory/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    config=f"""import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(deps.as_posix())};const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};"""
    (directory/'vite.config.mjs').write_text(config,encoding='utf-8')
    subprocess.run([NODE,str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(directory/'vite.config.mjs')],check=True,capture_output=True,text=True,encoding='utf-8',timeout=90)

def host127(directory):
    from PySide6.QtCore import QTimer,QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from tests.refactor.simulation.engine import ensure_offscreen_fonts
    app=QApplication([]);ensure_offscreen_fonts();view=QWebEngineView();profile=QWebEngineProfile(view);page=QWebEnginePage(profile,view)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            if info.requestUrl().scheme() in ('http','https'):info.block(True)
    block=Offline(page);profile.setUrlRequestInterceptor(block);view.setPage(page)
    view.resize(1200,950);view.show();view.setUrl(QUrl.fromLocalFile(str(directory/'dist/index.html')))
    timer=QTimer();timer.timeout.connect(lambda:app.quit() if (directory/'done').exists() else None);timer.start(100);app.exec()

def test_skip127_native_drag_click_keyboard_confirm_reset_and_reduced_motion(tmp_path):
    build127(tmp_path)
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1')
    with (tmp_path/'qt127.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(tmp_path)],env=env,stdout=log,stderr=log)
        try:
            result=subprocess.run([NODE,str(Path(__file__).with_name('browser127.mjs')),str(tmp_path)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert result.returncode==0,result.stdout+result.stderr
        finally:(tmp_path/'done').touch();child.wait(timeout=15)

if __name__=='__main__':host127(Path(sys.argv[2]))
