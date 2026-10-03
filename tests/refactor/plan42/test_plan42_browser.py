"""Real product pages/WebEngine; explicitly fake only the bridge's responses."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_plan42_preview_choices_and_material_warning_browser(tmp_path):
    here=Path(__file__).parent
    repo=Path(__file__).resolve().parents[3]
    local=Path(os.environ.get('MF_WEB_TOOLCHAIN','D:/MentorFlow/.local'))
    node=local/'node/node.exe';deps=local/'web-deps'
    if not node.is_file():pytest.skip('shared web tools unavailable')
    (tmp_path/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (tmp_path/'entry.tsx').write_text(f'import {json.dumps((repo/"refactor/learning/web/src/theme.css").as_posix())};\nimport {json.dumps((here/"plan42_browser.tsx").as_posix())}',encoding='utf-8')
    config=f"""
import path from 'node:path'
import {{pathToFileURL}} from 'node:url'
const deps={json.dumps(deps.as_posix())}
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{root:{json.dumps(tmp_path.as_posix())},base:'./',resolve:{{alias:[{{find:'../bridge',replacement:{json.dumps((here/'plan42_browser_bridge.ts').as_posix())}}}]}},
plugins:[react(),{{name:'shared-deps',enforce:'pre',async resolveId(source,importer,options){{
if(!importer||source.startsWith('.')||source.startsWith('/')||source.includes(':')||importer.includes('node_modules'))return null
return this.resolve(source,path.join(deps,'package.json'),{{...options,skipSelf:true}})
}}}}],build:{{outDir:'dist',emptyOutDir:true}}}}
"""
    (tmp_path/'vite.config.mjs').write_text(config,encoding='utf-8')
    build=subprocess.run([str(node),str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(tmp_path/'vite.config.mjs')],capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert build.returncode==0,build.stdout+build.stderr
    env={**os.environ,'QT_QPA_PLATFORM':'windows' if os.name=='nt' else 'offscreen'}
    child=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert child.returncode==0,child.stdout+child.stderr
    result=json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))
    assert result['ok'],result


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    from tests.refactor.app.test_update41_browser import browser
    browser(Path(sys.argv[1]))
