"""Real React/WebEngine screenshots and inherited mandatory policy presentation."""
import json, os, subprocess, sys, time
from pathlib import Path
import pytest


def test_update41_notice_in_real_webengine(tmp_path):
    fixture=Path(__file__).parent
    local=Path(os.environ.get('MF_WEB_TOOLCHAIN','D:/MentorFlow/.local'))
    node=local/'node/node.exe';deps=local/'web-deps'
    if not node.is_file(): pytest.skip('shared web tools missing')
    (tmp_path/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    theme=Path(__file__).resolve().parents[3]/'refactor/learning/web/src/theme.css'
    (tmp_path/'entry.tsx').write_text(f'import {json.dumps(theme.as_posix())};\nimport {json.dumps((fixture/"update41_browser.tsx").as_posix())}',encoding='utf-8')
    config=f'''
import path from 'node:path'
import {{pathToFileURL}} from 'node:url'
const deps={json.dumps(deps.as_posix())}
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{root:{json.dumps(tmp_path.as_posix())},base:'./',resolve:{{alias:[
{{find:'./bridge',replacement:{json.dumps((fixture/'update37_browser_bridge.ts').as_posix())}}},
{{find:'../bridge',replacement:{json.dumps((fixture/'update37_browser_bridge.ts').as_posix())}}}]}},
plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(source,importer,options){{
if(!importer||source.startsWith('.')||source.startsWith('/')||source.includes(':')||importer.includes('node_modules'))return null
return this.resolve(source,path.join(deps,'package.json'),{{...options,skipSelf:true}})
}}}}],build:{{outDir:'dist',emptyOutDir:true}}}}
'''
    (tmp_path/'vite.config.mjs').write_text(config,encoding='utf-8')
    build=subprocess.run([str(node),str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(tmp_path/'vite.config.mjs')],capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert build.returncode==0,build.stdout+build.stderr
    env={**os.environ,'QT_QPA_PLATFORM':'windows' if os.name=='nt' else 'offscreen'}
    child=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert child.returncode==0,child.stdout+child.stderr
    result=json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))
    assert result['ok'],result


def browser(directory):
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    app=QApplication([]);view=QWebEngineView();view.resize(1100,780);view.show()
    view.setUrl(QUrl.fromLocalFile(str(directory/'dist/index.html')))
    result=None;captured=False;until=time.monotonic()+30
    while time.monotonic()<until:
        app.processEvents();time.sleep(.005);pending=[]
        view.page().runJavaScript('JSON.stringify({result:window.result||null,capture:!!window.captureReady})',pending.append)
        while not pending and time.monotonic()<until:app.processEvents();time.sleep(.002)
        if not pending:continue
        state=json.loads(pending[0])
        if state['capture'] and not captured:
            # DOM readiness can precede the compositor. Capture the actual
            # owned test window on Windows, rather than a blank offscreen grab.
            settle=time.monotonic()+.6
            while time.monotonic()<settle:app.processEvents();time.sleep(.01)
            image=view.grab()  # render only this owned view, even when obscured
            image.save(str(directory/'web-update.png'));captured=True
            view.page().runJavaScript('window.captured=true')
        if state['result']:
            result=state['result'];break
    (directory/'browser-result.json').write_text(json.dumps(result or {'ok':False,'error':'timeout'},ensure_ascii=False),encoding='utf-8')
    view.close();app.processEvents()


if __name__=='__main__':browser(Path(sys.argv[1]))
