"""Render the actual Classroom with snapshots from the real offline state machine."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from tests.refactor.direct97.support97 import make,position,act,close


def build(directory, entry='direct97_browser.tsx', bridge='direct97_bridge.ts'):
    fixture=Path(__file__).parent
    deps=Path('D:/MentorFlow/.local/web-deps')
    node=Path('D:/MentorFlow/.local/node/node.exe')
    directory.mkdir(parents=True,exist_ok=True)
    e=make(directory/'casual',questions=0)
    try:
        position(e,1)
        states=dict(entry=act(e,'enter'),prompts=[],answers=[])
        states['prompts'].append(act(e,'start_test','skip_test'))
        for i in range(3):
            states['answers'].append(act(e,'reply','我按自己的理解说一句。'))
            if i<2:states['prompts'].append(act(e,'direct_next'))
        states.update(original=states['answers'][0],choice=states['answers'][-1],skipped=act(e,'direct_choice',True))
    finally:close(e)
    e=make(directory/'graded')
    try:states['graded']=act(e,'start_test','skip_test')
    finally:close(e)
    (directory/'states.json').write_text(json.dumps(states,ensure_ascii=False),encoding='utf-8')
    aliases=[dict(find=k,replacement=v.as_posix()) for k,v in [
        ('../bridge',fixture/bridge),
        ('../teacher/TeacherStage',fixture/'direct97_optional.tsx'),
        ('../voice',fixture/'direct97_optional.tsx'),
        ('./states.json',directory/'states.json')]]
    (directory/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (directory/'entry.tsx').write_text('import '+json.dumps((fixture/entry).as_posix()),encoding='utf-8')
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
    result=subprocess.run([str(node),str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(directory/'vite.config.mjs')],capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr


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
    view.resize(1200,950);view.show();view.setUrl(QUrl.fromLocalFile(str(directory/'dist/index.html')))
    def pump(seconds=.03):
        end=time.monotonic()+seconds
        while time.monotonic()<end:app.processEvents();time.sleep(.002)
    def js(code):
        result=[];page.runJavaScript(code,result.append);end=time.monotonic()+5
        while not result and time.monotonic()<end:pump()
        assert result,'JS callback timeout'
        return result[0]
    result=None;end=time.monotonic()+45
    while time.monotonic()<end:
        pump();value=js('JSON.stringify(window.direct97Result||null)')
        if value and value!='null':result=json.loads(value);break
    result=result or dict(ok=False,error='browser timeout')
    (directory/'ui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['ok'],result
    output=Path(os.environ.get('MF_DIRECT97_SCREENSHOTS',str(directory)))
    output.mkdir(parents=True,exist_ok=True)
    for theme in ('sakura','aurora'):
        for stage in ('entry','original','choice'):
            js(f'document.documentElement.dataset.theme={json.dumps(theme)};window.direct97Show({json.dumps(stage)})')
            end=time.monotonic()+5
            needle={'entry':'跳过该知识点','original':'对照原文，自己判断理解得对不对','choice':'不管刚才回答怎样'}[stage]
            while time.monotonic()<end and not js(f'document.body.textContent.includes({json.dumps(needle)})'):pump()
            assert js(f'document.body.textContent.includes({json.dumps(needle)})')
            pump(.2)
            assert view.grab().save(str(output/f'{stage}-{theme}.png'))
            assert not js('document.documentElement.scrollWidth>innerWidth'),'horizontal overflow'
    view.resize(600,950);pump(.2)
    assert not js('document.documentElement.scrollWidth>innerWidth'),'narrow horizontal overflow'
    view.close();pump()


def test_direct97_real_classroom_buttons_sources_choices_and_screenshots(tmp_path):
    build(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),'--browser',str(tmp_path)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=80)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--browser',type=Path)
    args=parser.parse_args()
    browser(args.browser)
