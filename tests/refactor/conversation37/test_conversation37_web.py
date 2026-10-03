"""Real React + offline bridge snapshots built by the real Classroom/Store."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, act
from refactor.learning.app.classroom import Classroom

def build(directory):
    repo=Path(__file__).resolve().parents[3];fixture=Path(__file__).parent
    node=Path(r'D:\MentorFlow\.local\node\node.exe');deps=Path(r'D:\MentorFlow\.local\web-deps')
    if not node.is_file():pytest.skip('共享前端工具不存在')
    directory.mkdir(parents=True,exist_ok=True)
    e=make_environment(directory/'data')
    try:
        with patch.object(Classroom,'_materials',staticmethod(lambda:e.materials)):
            states={}
            for name,action,args in [('opening','next',()),('teaching','choose',(0,)),('confused','confused',()),
                ('ask','ask',('硬度相同吗？',)),('back','back',()),('question','next',()),('closing','answer',([0],)),('why','why',())]:
                states[name]=act(e,action,*args)
            (directory/'states.json').write_text(json.dumps(states,ensure_ascii=False),encoding='utf-8')
    finally:e.store.close()
    aliases=[dict(find=k,replacement=(fixture/v).as_posix()) for k,v in [
        ('../bridge','conversation37_bridge.ts'),('../teacher/TeacherStage','conversation37_optional.tsx'),('../voice','conversation37_optional.tsx')]]
    aliases.append(dict(find='./states.json',replacement=(directory/'states.json').as_posix()))
    (directory/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (directory/'entry.tsx').write_text(f'import {json.dumps((fixture/"conversation37_browser.tsx").as_posix())}',encoding='utf-8')
    config=f'''
import path from 'node:path'
import {{pathToFileURL}} from 'node:url'
const deps={json.dumps(deps.as_posix())}
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},
plugins:[react(),{{name:'shared-deps',enforce:'pre',async resolveId(s,i,o){{
if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null
return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})
}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}}
'''
    (directory/'vite.config.mjs').write_text(config,encoding='utf-8')
    result=subprocess.run([str(node),str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(directory/'vite.config.mjs')],
        capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode==0,result.stderr

def browser(directory):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS','--disable-gpu')
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            if info.requestUrl().scheme() in ('http','https'):info.block(True)
    app=QApplication([]);view=QWebEngineView();profile=QWebEngineProfile(view)
    page=QWebEnginePage(profile,view);block=Offline(page);profile.setUrlRequestInterceptor(block);view.setPage(page)
    view.resize(1440,900);view.show();view.setUrl(QUrl.fromLocalFile(str(directory/'dist/index.html')))
    def pump(seconds=.05):
        end=time.monotonic()+seconds
        while time.monotonic()<end:app.processEvents();time.sleep(.002)
    def js(code):
        rows=[];page.runJavaScript(code,rows.append);end=time.monotonic()+5
        while not rows and time.monotonic()<end:pump(.01)
        assert rows,'JS callback timeout'
        return rows[0]
    end=time.monotonic()+40;result=None
    while time.monotonic()<end:
        pump();value=js('JSON.stringify(window.conversationResult||null)')
        if value and value!='null':result=json.loads(value);break
    result=result or dict(ok=False,error='browser timeout')
    (directory/'ui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for width,theme in [(1440,'sakura'),(600,'aurora')]:
        view.resize(width,900);js(f'document.documentElement.dataset.theme={json.dumps(theme)}');pump(.2)
        assert view.grab().save(str(directory/f'classroom-{width}-{theme}.png'))
        assert not js('document.documentElement.scrollWidth>innerWidth'),'horizontal overflow'
    view.close();pump()
    assert result['ok'],result

def test_conversation37_browser_contract(tmp_path):
    build(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),'--browser',str(tmp_path)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    assert result.returncode==0,result.stdout+result.stderr

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--browser',type=Path);parser.add_argument('--build',type=Path)
    args=parser.parse_args()
    if args.build:build(args.build)
    if args.browser:browser(args.browser)
