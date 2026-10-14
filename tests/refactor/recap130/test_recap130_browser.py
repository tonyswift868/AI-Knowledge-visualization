"""Real React/Chromium snapshots of the actual old/new classroom transitions."""
import json, os, re, socket, subprocess, sys, types
from pathlib import Path

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))
NODE='D:/MentorFlow/.local/node/node.exe'


def baseline130(relative,directory):
    source=subprocess.check_output(['git','show','5c8d18f9:'+relative],cwd=REPO,text=True,encoding='utf-8')
    parent=(REPO/relative).parent
    source=re.sub(r'''(["'])(\.{1,2}/[^"']+)\1''',lambda m:m[1]+(parent/m[2]).resolve().as_posix()+m[1],source)
    path=directory/Path(relative).name;path.write_text(source,encoding='utf-8')
    return path


def build130(directory, *, before=False):
    from tests.refactor.recap130.support130 import environment130, last_subpoint130, close130
    from tests.refactor.conversation37.test_grounded_conversation37 import act, PLAN
    directory.mkdir(parents=True,exist_ok=True);here=Path(__file__).parent
    e=environment130(directory/'data')
    try:
        if before:
            source=subprocess.check_output(['git','show','5c8d18f9:refactor/learning/app/classroom.py'],cwd=REPO,text=True,encoding='utf-8')
            module=types.ModuleType('refactor.learning.app.baseline130');module.__package__='refactor.learning.app'
            exec(compile(source,'baseline130.py','exec'),module.__dict__)
            e.room=module.Classroom(e.services,e.room.study_day,now=e.room.now);e.room._materials=lambda:e.materials
        last_subpoint130(e)
        for i in range(12):e.store.append_message(PLAN,'m0','assistant',f'之前的对话第 {i+1} 轮。'+('沿着原文看压力与面积之间的关系。'*6))
        first=e.room.run(PLAN,lambda *args:None)
        e.gateway.replies=['==压力相同，受力面积越小，压强越大。==\n\n'
            +'从相同的压力开始比较，这一步先看受力面积。我们把压力相同这个条件留下，比较针尖和手指接触物体的面积，注意比较的始终是单位面积上的压力。\n\n'
            +'把压力除以面积，就能得到压强。面积缩小时，同样大小的压力分配在更小的面积上，所以可以沿着这个关系理解公式，不用死背结果。\n\n'
            +'换一个生活里的场景也一样：先确认比较时压力相同，再看面积大小。条件变化时不要直接套用这一句话，我们要回到公式中逐项比较。']
        finished=act(e,'next');assert not finished['failure']
        (directory/'states130.json').write_text(json.dumps(dict(first=first,finished=finished),ensure_ascii=False),encoding='utf-8')
    finally:close130(e)
    deps=Path('D:/MentorFlow/.local/web-deps');optional=here.parent/'conversation37/conversation37_optional.tsx'
    classroom=REPO/'refactor/learning/web/src/pages/Classroom.tsx'
    aliases=[dict(find='./Classroom130',replacement=classroom.as_posix()),dict(find='../bridge',replacement=(here/'bridge130.ts').as_posix()),dict(find='./states130.json',replacement=(directory/'states130.json').as_posix())]
    aliases.extend(dict(find=k,replacement=optional.as_posix()) for k in ('../teacher/TeacherStage','../voice'))
    if before:
        aliases[0]['replacement']=baseline130('refactor/learning/web/src/pages/Classroom.tsx',directory).as_posix()
        old_hook=baseline130('refactor/learning/web/src/useRoundScroll.ts',directory)
        aliases.extend(dict(find=(REPO/'refactor/learning/web/src'/name).as_posix(),replacement=replacement) for name,replacement in [
            ('useRoundScroll',old_hook.as_posix()),('bridge',(here/'bridge130.ts').as_posix()),('teacher/TeacherStage',optional.as_posix()),('voice',optional.as_posix())])
    (directory/'entry.tsx').write_text('import '+json.dumps((here/'browser130.tsx').as_posix()),encoding='utf-8')
    (directory/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    config=f"""import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(deps.as_posix())};const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};"""
    (directory/'vite.config.mjs').write_text(config,encoding='utf-8')
    subprocess.run([NODE,str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(directory/'vite.config.mjs')],check=True,capture_output=True,text=True,encoding='utf-8',timeout=90)


def drive130(directory, *, before=False):
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1')
    with (directory/'qt130.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(directory)],env=env,stdout=log,stderr=log)
        try:
            result=subprocess.run([NODE,str(Path(__file__).with_name('browser130.mjs')),str(directory),*(['--before'] if before else [])],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert result.returncode==0,result.stdout+result.stderr
        finally:(directory/'done').touch();child.wait(timeout=15)


def test_recap130_real_browser_top_anchor_next_message_focus_and_user_scroll(tmp_path):
    build130(tmp_path);drive130(tmp_path)


if __name__=='__main__':
    from tests.refactor.skip127.test_skip127_browser import host127
    host127(Path(sys.argv[2]))
