"""Owned offscreen Chromium, actual Classroom React, fake course bridge."""
import json, os, socket, subprocess, sys
from pathlib import Path
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))
NODE='D:/MentorFlow/.local/node/node.exe'

def build129(directory):
    from refactor.learning.app.classroom import Classroom
    from tests.refactor.concept114.test_concept114_classroom import concept_environment
    from tests.refactor.conversation37.test_grounded_conversation37 import act
    directory.mkdir(parents=True,exist_ok=True);here=Path(__file__).parent
    e=concept_environment(directory/'data')
    try:
        with patch.object(Classroom,'_materials',staticmethod(lambda:e.materials)):state=act(e,'next')
        state['preparation']=dict(done=3,total=22,text='正在准备题目',error=None)
        state['today']['is_late_night']=True
        (directory/'states129.json').write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
    finally:e.room.cancel_materials();e.store.close()
    deps=Path('D:/MentorFlow/.local/web-deps');optional=here.parent/'conversation37/conversation37_optional.tsx'
    aliases=[dict(find='../bridge',replacement=(here/'bridge129.ts').as_posix()),dict(find='./states129.json',replacement=(directory/'states129.json').as_posix())]
    aliases.extend(dict(find=k,replacement=optional.as_posix()) for k in ('../teacher/TeacherStage','../voice'))
    (directory/'entry.tsx').write_text('import '+json.dumps((here/'browser129.tsx').as_posix()),encoding='utf-8')
    (directory/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    config=f"""import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(deps.as_posix())};const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};"""
    (directory/'vite.config.mjs').write_text(config,encoding='utf-8')
    subprocess.run([NODE,str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(directory/'vite.config.mjs')],check=True,capture_output=True,text=True,encoding='utf-8',timeout=90)

def drive129(directory, *, before=False):
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1')
    with (directory/'qt129.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(directory)],env=env,stdout=log,stderr=log)
        try:
            result=subprocess.run([NODE,str(Path(__file__).with_name('browser129.mjs')),str(directory),*(['--before'] if before else [])],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert result.returncode==0,result.stdout+result.stderr
        finally:(directory/'done').touch();child.wait(timeout=15)

def test_layout129_real_react_popovers_status_wait_themes_and_narrow(tmp_path):
    build129(tmp_path);drive129(tmp_path)

if __name__=='__main__':
    from tests.refactor.skip127.test_skip127_browser import host127
    host127(Path(sys.argv[2]))
