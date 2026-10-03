"""Offline real React/Qt WebEngine qualification of the global update notice."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_update37_global_notice_in_real_browser(tmp_path):
    fixture = Path(__file__).parent
    local = Path(os.environ.get('MF_WEB_TOOLCHAIN', 'D:/MentorFlow/.local'))
    node, deps = local/'node/node.exe', local/'web-deps'
    if not node.is_file(): pytest.skip('shared web tools missing')
    (tmp_path/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (tmp_path/'entry.tsx').write_text(f'import {json.dumps((fixture/"update37_browser.tsx").as_posix())}', encoding='utf-8')
    config = f'''
import path from 'node:path'
import {{ pathToFileURL }} from 'node:url'
const deps = {json.dumps(deps.as_posix())}
const {{ default: react }} = await import(pathToFileURL(path.join(deps, 'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{ root: {json.dumps(tmp_path.as_posix())}, base: './', resolve: {{ alias: [
{{ find: './bridge', replacement: {json.dumps((fixture/'update37_browser_bridge.ts').as_posix())} }},
{{ find: '../bridge', replacement: {json.dumps((fixture/'update37_browser_bridge.ts').as_posix())} }}] }},
plugins: [react(), {{ name:'deps', enforce:'pre', async resolveId(source, importer, options) {{
if (!importer || source.startsWith('.') || source.startsWith('/') || source.includes(':') || importer.includes('node_modules')) return null
return this.resolve(source, path.join(deps, 'package.json'), {{ ...options, skipSelf:true }})
}} }}], build: {{ outDir:'dist', emptyOutDir:true }} }}
'''
    (tmp_path/'vite.config.mjs').write_text(config, encoding='utf-8')
    build = subprocess.run([str(node), str(deps/'node_modules/vite/bin/vite.js'), 'build', '--config', str(tmp_path/'vite.config.mjs')],
                           capture_output=True, text=True, encoding='utf-8', timeout=100)
    assert build.returncode == 0, build.stdout + build.stderr
    result = subprocess.run([sys.executable, '-B', str(Path(__file__)), str(tmp_path)],
                            capture_output=True, text=True, encoding='utf-8', timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))
    assert report['ok'], report
    print('UPDATE37_BROWSER=' + json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    # Reuse the existing small offline WebEngine driver; no product test seam.
    driver = Path(__file__).with_name('test_plan_read_browser.py')
    spec = importlib.util.spec_from_file_location('offline_browser_driver37', driver)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.run_browser(Path(sys.argv[1]))
