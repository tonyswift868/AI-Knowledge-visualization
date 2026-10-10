"""Existing main-window UpdateNotice renders a late startup result, offline."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_startup96_late_offer_in_existing_web_notice(tmp_path):
    from refactor.desktop.delivery.per_user_update_core import _startup_result
    status = {**_startup_result('OPTIONAL_UPDATE', current='2.0.5', latest='2.0.6',
              release_notes='启动反馈与登录窗口更新提示'), 'busy': False, 'available': True}
    repo = Path(__file__).resolve().parents[3]
    local = Path(os.environ.get('MF_WEB_TOOLCHAIN', 'D:/MentorFlow/.local'))
    deps = local/'web-deps'
    entry = Path(__file__).with_name('startup96_browser.tsx')
    bridge = tmp_path/'bridge96.ts'
    bridge.write_text('export async function call(m: string) { return '+json.dumps(status, ensure_ascii=False)+' }\nexport function on() { return () => {} }\nexport class BridgeFailure extends Error {}', encoding='utf-8')
    (tmp_path/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (tmp_path/'entry.tsx').write_text('import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';\nimport '+json.dumps(entry.as_posix()), encoding='utf-8')
    (tmp_path/'vite.config.mjs').write_text(f'''
import path from 'node:path'
import {{pathToFileURL}} from 'node:url'
const deps={json.dumps(deps.as_posix())}
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{root:{json.dumps(tmp_path.as_posix())},base:'./',resolve:{{alias:[
{{find:'./bridge',replacement:{json.dumps(bridge.as_posix())}}},
{{find:'../bridge',replacement:{json.dumps(bridge.as_posix())}}}]}},
plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(source,importer,options){{
if(!importer||source.startsWith('.')||source.startsWith('/')||source.includes(':')||importer.includes('node_modules'))return null
return this.resolve(source,path.join(deps,'package.json'),{{...options,skipSelf:true}})
}}}}],build:{{outDir:'dist',emptyOutDir:true}}}}
''', encoding='utf-8')
    built = subprocess.run([str(local/'node/node.exe'), str(deps/'node_modules/vite/bin/vite.js'),
        'build', '--config', str(tmp_path/'vite.config.mjs')], capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert built.returncode == 0, built.stdout+built.stderr
    driver = repo/'tests/refactor/app/test_update41_browser.py'
    child = subprocess.run([sys.executable, '-B', str(driver), str(tmp_path)], capture_output=True,
                           text=True, encoding='utf-8', timeout=40,
                           env={**os.environ, 'QT_QPA_PLATFORM': 'windows' if os.name == 'nt' else 'offscreen'})
    assert child.returncode == 0, child.stdout+child.stderr
    result = json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))
    assert result['ok'], result
