import json,os,subprocess,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from refactor.learning.app.classroom import Classroom
from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.conversation37.test_grounded_conversation37 import act
from tests.refactor.conversation37.test_conversation37_web import browser

def build119(directory,states=None,entry=None):
    directory.mkdir(parents=True,exist_ok=True);here=Path(__file__).parent
    if states is None:
        e=concept_environment(directory/'data')
        try:
            with patch.object(Classroom,'_materials',staticmethod(lambda:e.materials)):
                e.gateway.replies=['==压力相同时面积越小压强越大。==\n\n补充说明可以展开慢慢看。\n\n你想先看哪个条件？']
                first=act(e,'next');act(e,'next');act(e,'next');finished=act(e,'next')
                source=dict(e.room._course('a'*32).passage_card('c0'),section=['压强'])
            states=dict(first=first,finished=finished,source=source)
        finally:e.room.cancel_materials();e.store.close()
    (directory/'states119.json').write_text(json.dumps(states,ensure_ascii=False),encoding='utf-8')
    deps=Path(r'D:\MentorFlow\.local\web-deps');optional=here.parent/'conversation37/conversation37_optional.tsx'
    aliases=[dict(find='../bridge',replacement=(here/'bridge119.ts').as_posix()),dict(find='./states119.json',replacement=(directory/'states119.json').as_posix())]
    aliases.extend(dict(find=k,replacement=optional.as_posix()) for k in ('../teacher/TeacherStage','../voice'))
    (directory/'entry.tsx').write_text('import '+json.dumps((entry or here/'browser119.tsx').as_posix()),encoding='utf-8')
    (directory/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    config=f"""import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(deps.as_posix())};const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};"""
    (directory/'vite.config.mjs').write_text(config,encoding='utf-8')
    result=subprocess.run([r'D:\MentorFlow\.local\node\node.exe',str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(directory/'vite.config.mjs')],capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr

def test_browser_focus119_wiring_persistence_stream_and_source(tmp_path):
    build119(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),'--browser',str(tmp_path)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr

if __name__=='__main__':browser(Path(sys.argv[2]))
