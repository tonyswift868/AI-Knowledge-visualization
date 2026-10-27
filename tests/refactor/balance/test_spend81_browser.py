"""Actual React Settings in an offline Qt Chromium, with light/dark captures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def test_spend81_real_settings_browser(tmp_path):
    fixture = Path(__file__).parent
    repo = fixture.parents[2]
    local = Path(os.environ.get('MF_WEB_TOOLCHAIN', 'D:/MentorFlow/.local'))
    node, deps = local / 'node/node.exe', local / 'web-deps'
    if not node.is_file():
        pytest.skip('shared offline web toolchain unavailable')
    (tmp_path / 'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (tmp_path / 'entry.tsx').write_text(f'import {json.dumps((repo / "refactor/learning/web/src/theme.css").as_posix())};\n'
                                     f'import {json.dumps((fixture / "spend81_browser.tsx").as_posix())}', encoding='utf-8')
    aliases = [{'find': '../bridge', 'replacement': (fixture / 'spend81_browser_bridge.ts').as_posix()}]
    config = f'''
import path from 'node:path'
import {{pathToFileURL}} from 'node:url'
const deps={json.dumps(deps.as_posix())}
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{root:{json.dumps(tmp_path.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},
plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(source,importer,options){{
if(!importer||source.startsWith('.')||source.startsWith('/')||source.includes(':')||importer.includes('node_modules'))return null
return this.resolve(source,path.join(deps,'package.json'),{{...options,skipSelf:true}})
}}}}],build:{{outDir:'dist',emptyOutDir:true}}}}
'''
    config_path = tmp_path / 'vite.config.mjs'
    config_path.write_text(config, encoding='utf-8')
    built = subprocess.run([str(node), str(deps / 'node_modules/vite/bin/vite.js'), 'build', '--config', str(config_path)],
                           capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert built.returncode == 0, built.stdout + built.stderr
    env = {**os.environ, 'QT_QPA_PLATFORM': 'windows' if os.name == 'nt' else 'offscreen'}
    child = subprocess.run([sys.executable, '-B', str(Path(__file__)), str(tmp_path)], env=env,
                           capture_output=True, text=True, encoding='utf-8', timeout=50)
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads((tmp_path / 'browser-result.json').read_text(encoding='utf-8'))
    assert result['ok'], result
    assert all((tmp_path / f'{name}.png').stat().st_size > 10000 for name in
               ('spend-sakura', 'spend-aurora', 'preset-sakura', 'preset-aurora'))


def browser(directory):
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    app = QApplication([])
    view = QWebEngineView(); view.resize(1400, 1120); view.show()
    load_results = []
    view.loadFinished.connect(load_results.append)
    view.setUrl(QUrl.fromLocalFile(str(directory / 'dist/index.html')))
    result = None
    captured = set()
    end = time.monotonic() + 40
    while not load_results and time.monotonic() < end:
        app.processEvents()
    assert load_results, 'Qt WebEngine page load did not finish'
    assert load_results[-1], 'Qt WebEngine page load failed'
    while time.monotonic() < end:
        app.processEvents(); time.sleep(.005); pending = []
        view.page().runJavaScript('JSON.stringify({result:window.result||null,capture:window.captureReady||null})', pending.append)
        while not pending and time.monotonic() < end:
            app.processEvents(); time.sleep(.002)
        if not pending or not pending[0]: continue
        state = json.loads(pending[0])
        name = state['capture']
        if name and name not in captured:
            assert view.grab().save(str(directory / f'{name}.png'))
            captured.add(name)
            view.page().runJavaScript('window.captured=' + json.dumps(name))
        if state['result']:
            result = state['result']; break
    (directory / 'browser-result.json').write_text(json.dumps(result or dict(ok=False, error='timeout'), ensure_ascii=False), encoding='utf-8')
    view.close(); app.processEvents()


if __name__ == '__main__':
    browser(Path(sys.argv[1]))
