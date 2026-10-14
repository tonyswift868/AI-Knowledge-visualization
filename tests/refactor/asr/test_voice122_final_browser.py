"""Existing controlled voice input replaces previews, preserving base and undo."""
import json
from pathlib import Path
import subprocess
import sys


def test_voice122_whole_final_replaces_segmented_preview_browser(tmp_path):
    repo=Path(__file__).resolve().parents[3]
    src=repo/'refactor/learning/web/src'
    entry='''
import * as React from 'react';import {createRoot} from 'react-dom/client';
import {useVoiceInput} from '@asrHook';
const listeners=new Map();let id='';const sends=[];
const transport={available:()=>true,on:(n,f)=>{listeners.set(n,f);return()=>listeners.delete(n)},
call:async(n,p)=>{sends.push(n);if(n==='voice.status')return {state:'ready',installed:true};
if(n==='voice.start'){id=p.session_id;return {}};if(n==='voice.stop')return {
session_id:id,text:'向量那个。',raw_text:'项梁那个。',replacements:[[0,2,'项梁','向量']]};return {}}};
function App(){const [text,setText]=React.useState('原有回答');
const voice=useVoiceInput({value:text,onChange:setText,transport});window.v122={text,voice};
return <textarea value={text} onChange={e=>setText(e.target.value)}/>}
createRoot(document.getElementById('root')).render(<App/>);
const wait=async fn=>{const end=Date.now()+3000;while(!fn()){if(Date.now()>end)throw Error('timeout');await new Promise(r=>setTimeout(r,5))}};
const assert=(ok,msg)=>{if(!ok)throw Error(msg)};
async function run(){await wait(()=>!!window.v122);await window.v122.voice.start();
listeners.get('voice.partial')({session_id:id,text:'下料。 来个。',raw_text:'下料。 来个。',replacements:[]});
await wait(()=>window.v122.text==='原有回答 下料。 来个。');
await window.v122.voice.stop();await wait(()=>window.v122.text==='原有回答 向量那个。');
assert(!window.v122.text.includes('下料'),'preview must be replaced');
assert(window.v122.voice.originalText==='项梁那个。','undo raw must be whole pass');
window.v122.voice.undoCorrections();await wait(()=>window.v122.text==='原有回答 项梁那个。');
assert(!sends.some(n=>n.startsWith('class.')),'no auto-send');
window.result={ok:true,checks:['preview replaced','base preserved','whole raw undo','no auto send']}}
run().catch(e=>window.result={ok:false,error:String(e)});
'''
    (tmp_path/'entry.tsx').write_text(entry,encoding='utf-8')
    (tmp_path/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    deps=Path('D:/MentorFlow/.local/web-deps')
    config=f'''import base from {json.dumps((repo/'refactor/learning/web/vite.config.mjs').as_uri())};
export default {{...base,root:{json.dumps(tmp_path.as_posix())},resolve:{{...base.resolve,
alias:{{'@asrHook':{json.dumps((src/'voice/useVoiceInput.ts').as_posix())}}}}},build:{{outDir:'dist'}}}}'''
    (tmp_path/'vite.config.mjs').write_text(config,encoding='utf-8')
    import os
    result=subprocess.run(['D:/MentorFlow/.local/node/node.exe',str(deps/'node_modules/vite/bin/vite.js'),
        'build','--config',str(tmp_path/'vite.config.mjs')],capture_output=True,text=True,encoding='utf-8',
        errors='replace',timeout=90,env=dict(os.environ,MENTORFLOW_WEB_DEPS=str(deps)))
    assert result.returncode==0,result.stdout+result.stderr
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],cwd=repo,
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))['ok']


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    from tests.refactor.teacher_voice_v2.test_voice_stream_browser import browser
    browser(Path(sys.argv[1]))
