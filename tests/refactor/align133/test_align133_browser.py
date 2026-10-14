"""Real Classroom, controlled streaming bridge and native Chromium scrolling."""
import json, os, re, socket, subprocess, sys
from pathlib import Path

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))
NODE='D:/MentorFlow/.local/node/node.exe'
WEB=REPO/'refactor/learning/web/src'


def source133(ref, relative, root):
    text=subprocess.check_output(['git','show',ref+':refactor/learning/web/src/'+relative],cwd=REPO,text=True,encoding='utf-8')
    parent=(WEB/relative).parent
    text=re.sub(r'''(["'])(\.{1,2}/[^"']+)\1''',lambda m:m[1]+(parent/m[2]).resolve().as_posix()+m[1],text)
    path=root/Path(relative).name;path.write_text(text,encoding='utf-8');return path


def build133(root,ref=None):
    from tests.refactor.conversation37.test_grounded_conversation37 import make_environment,act
    root.mkdir(parents=True,exist_ok=True);here=Path(__file__).parent
    e=make_environment(root/'data',first_points=2);e.room._materials=lambda:e.materials
    try:state=act(e,'next');state=act(e,'choose',0)
    finally:e.room.cancel_materials();e.store.close()
    state['can_skip']=False
    (root/'states133.json').write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
    teacher=here.parent/'conversation37/conversation37_optional.tsx'
    voice=here/'voice133.tsx';bridge=here/'bridge133.ts'
    aliases=[dict(find='./Classroom133',replacement=(WEB/'pages/Classroom.tsx').as_posix()),dict(find='./MicButton133',replacement=(WEB/'voice/MicButton.tsx').as_posix()),dict(find='../bridge',replacement=bridge.as_posix()),dict(find='./states133.json',replacement=(root/'states133.json').as_posix()),dict(find='../teacher/TeacherStage',replacement=teacher.as_posix()),dict(find='../voice',replacement=voice.as_posix())]
    if ref:
        aliases[0]['replacement']=source133(ref,'pages/Classroom.tsx',root).as_posix()
        aliases[1]['replacement']=source133(ref,'voice/MicButton.tsx',root).as_posix()
        aliases.extend(dict(find=(WEB/name).as_posix(),replacement=replacement) for name,replacement in [
            ('useRoundScroll',source133(ref,'useRoundScroll.ts',root).as_posix()),('classroom/Conversation',source133(ref,'classroom/Conversation.tsx',root).as_posix()),
            ('classroom/conversation.css',source133(ref,'classroom/conversation.css',root).as_posix()),('voice/voice.css',source133(ref,'voice/voice.css',root).as_posix()),
            ('bridge',bridge.as_posix()),('teacher/TeacherStage',teacher.as_posix()),('voice',voice.as_posix())])
    (root/'entry.tsx').write_text('import '+json.dumps((here/'browser133.tsx').as_posix()),encoding='utf-8')
    (root/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    deps=Path('D:/MentorFlow/.local/web-deps')
    (root/'vite.config.mjs').write_text(f"""import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(deps.as_posix())};const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(root.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};""",encoding='utf-8')
    subprocess.run([NODE,str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],check=True,capture_output=True,text=True,encoding='utf-8',timeout=90)


def drive133(root,*,probe=False,ordinary=False):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1')
    with (root/'qt133.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(root)],env=env,stdout=log,stderr=log)
        try:
            result=subprocess.run([NODE,str(Path(__file__).with_name('browser133.mjs')),str(root),*(['--probe'] if probe else []),*(['--ordinary'] if ordinary else [])],env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
            assert result.returncode==0,result.stdout+result.stderr
        finally:(root/'done').touch();child.wait(timeout=15)


def test_align133_real_three_turns_focus_voice_and_stream(tmp_path):
    build133(tmp_path);drive133(tmp_path)


if __name__=='__main__':
    from tests.refactor.skip127.test_skip127_browser import host127
    host127(Path(sys.argv[2]))
