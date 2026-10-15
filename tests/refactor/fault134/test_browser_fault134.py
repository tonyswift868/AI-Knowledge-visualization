"""Actual React App/bridge/Toasts/Settings/Classroom in owned offscreen Qt Chromium."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

REPO=Path(__file__).resolve().parents[3]
WEB=REPO/'refactor/learning/web/src'
NODE='D:/MentorFlow/.local/node/node.exe'


def test_browser_fault134_themes_narrow_repeat_chip_class_and_settings(tmp_path):
    from tests.refactor.conversation37.test_grounded_conversation37 import make_environment,act
    here=Path(__file__).parent;e=make_environment(tmp_path/'data')
    e.room._materials=lambda:e.materials
    try:
        act(e,'next');state=act(e,'choose',0)
    finally:e.room.cancel_materials();e.store.close()
    state['can_skip']=False
    (tmp_path/'state134.json').write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
    (tmp_path/'entry.tsx').write_text('import '+json.dumps((here/'browser134.tsx').as_posix()),encoding='utf-8')
    (tmp_path/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    aliases=[dict(find='./state134.json',replacement=(tmp_path/'state134.json').as_posix()),
        dict(find='./MicButton133',replacement=(WEB/'voice/MicButton.tsx').as_posix()),
        dict(find='../teacher/TeacherStage',replacement=(here.parent/'conversation37/conversation37_optional.tsx').as_posix()),
        dict(find='../voice',replacement=(here.parent/'align133/voice133.tsx').as_posix())]
    deps=Path('D:/MentorFlow/.local/web-deps')
    (tmp_path/'vite.config.mjs').write_text(f"""import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(deps.as_posix())};const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(tmp_path.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};""",encoding='utf-8')
    build=subprocess.run([NODE,str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(tmp_path/'vite.config.mjs')],capture_output=True,text=True,encoding='utf-8',timeout=120)
    assert build.returncode==0,build.stdout+build.stderr
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1')
    with (tmp_path/'qt134.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(tmp_path)],env=env,stdout=log,stderr=log)
        try:
            result=subprocess.run([NODE,str(here/'browser134.mjs'),str(tmp_path)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
            assert result.returncode==0,result.stdout+result.stderr+(tmp_path/'qt134.log').read_text(encoding='utf-8')[-3000:]
        finally:(tmp_path/'done').touch();child.wait(timeout=15)


if __name__=='__main__':
    sys.path.insert(0,str(REPO))
    from tests.refactor.skip127.test_skip127_browser import host127
    host127(Path(sys.argv[2]))
