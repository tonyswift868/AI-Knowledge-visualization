"""Real Chromium checks for the non-blocking optional-update offer."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

REPO = Path(__file__).resolve().parents[3]
NODE = Path('D:/MentorFlow/.local/node/node.exe')
DEPS = Path('D:/MentorFlow/.local/web-deps')


def build(directory):
    here = Path(__file__).parent
    aliases = [
        {'find':'../bridge','replacement':(here/'bridge239.ts').as_posix()},
        {'find':'./bridge','replacement':(here/'bridge239.ts').as_posix()},
    ]
    (directory/'entry.tsx').write_text('import '+json.dumps((here/'browser239.tsx').as_posix()),encoding='utf-8')
    (directory/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    config=f"""import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(DEPS.as_posix())};const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};"""
    (directory/'vite.config.mjs').write_text(config,encoding='utf-8')
    result=subprocess.run([str(NODE),str(DEPS/'node_modules/vite/bin/vite.js'),'build','--config',str(directory/'vite.config.mjs')],capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr


def test_optional_offer_is_inline_until_explicit_install_click(tmp_path):
    build(tmp_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',
             QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',
             MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1')
    with (tmp_path/'qt239.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(tmp_path)],env=env,stdout=log,stderr=log)
        try:
            result=subprocess.run([str(NODE),str(Path(__file__).with_name('browser239.mjs')),str(tmp_path)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert result.returncode==0,result.stdout+result.stderr+(tmp_path/'qt239.log').read_text(encoding='utf-8')[-4000:]
        finally:
            (tmp_path/'done').touch();child.wait(timeout=15)


if __name__=='__main__':
    sys.path.insert(0,str(REPO))
    from tests.refactor.skip127.test_skip127_browser import host127
    host127(Path(sys.argv[2]))
