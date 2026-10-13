"""Real React/MicButton in offline Qt Chromium; bridge is an explicit fake."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_voice109_restore_original_and_send_confirmed_text_browser(tmp_path):
    repo=Path(__file__).resolve().parents[3]
    deps=Path('D:/MentorFlow/.local/web-deps');node=Path('D:/MentorFlow/.local/node/node.exe')
    src=repo/'refactor/learning/web/src'
    (tmp_path/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    entry=Path(__file__).with_name('voice109_browser.tsx')
    (tmp_path/'entry.tsx').write_text('import '+json.dumps(entry.as_posix()),encoding='utf-8')
    aliases={k:(src/p).as_posix() for k,p in {'@asrHook':'voice/useVoiceInput.ts','@asrMic':'voice/MicButton.tsx','@asrTheme':'theme.css'}.items()}
    config=f'''
import path from 'node:path';import {{pathToFileURL}} from 'node:url'
const deps={json.dumps(deps.as_posix())}
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{root:{json.dumps(tmp_path.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),
{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;
return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:'dist'}}}}
'''
    (tmp_path/'vite.config.mjs').write_text(config,encoding='utf-8')
    result=subprocess.run([str(node),str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(tmp_path/'vite.config.mjs')],
                          capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],cwd=repo,
        env={**os.environ,'PYTHONPATH':str(repo)},capture_output=True,text=True,encoding='utf-8',timeout=50)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))
    assert report['ok'],report
    assert len(report['checks'])==6


if __name__=='__main__':
    from tests.refactor.teacher_voice_v2.test_voice_stream_browser import browser
    browser(Path(sys.argv[1]))
