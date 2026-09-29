"""Real React pages with an explicit failing plans.list bridge in offline Chromium."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def test_home_and_plans_read_failure_retry_and_stale_list(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    local = Path(os.environ.get('MF_WEB_TOOLCHAIN', 'D:/MentorFlow/.local'))
    node, deps = local / 'node/node.exe', local / 'web-deps'
    if not node.is_file() or not (deps / 'node_modules/vite/bin/vite.js').is_file():
        pytest.skip('shared offline web toolchain unavailable')
    fixture = Path(__file__).parent
    (tmp_path / 'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (tmp_path / 'entry.tsx').write_text(f'import {json.dumps((fixture / "plan_read_browser.tsx").as_posix())}', encoding='utf-8')
    aliases = [{'find': '../bridge', 'replacement': (fixture / 'plan_read_browser_bridge.ts').as_posix()}]
    config = f'''
import path from 'node:path'
import {{ pathToFileURL }} from 'node:url'
const deps = {json.dumps(deps.as_posix())}
const {{ default: react }} = await import(pathToFileURL(path.join(deps, 'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{ root: {json.dumps(tmp_path.as_posix())}, base: './', resolve: {{ alias: {json.dumps(aliases)} }},
plugins: [react(), {{ name: 'shared-deps', enforce: 'pre', async resolveId(source, importer, options) {{
 if (!importer || source.startsWith('.') || source.startsWith('/') || source.includes(':')) return null
 if (importer.includes('node_modules')) return null
 return this.resolve(source, path.join(deps, 'package.json'), {{ ...options, skipSelf: true }})
}} }}], build: {{ outDir: 'dist', emptyOutDir: true }} }}
'''
    config_path = tmp_path / 'vite.config.mjs'
    config_path.write_text(config, encoding='utf-8')
    build = subprocess.run([str(node), str(deps / 'node_modules/vite/bin/vite.js'), 'build', '--config', str(config_path)],
                           capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert build.returncode == 0, build.stdout + build.stderr
    browser = subprocess.run([sys.executable, '-B', str(Path(__file__)), str(tmp_path)],
                             capture_output=True, text=True, encoding='utf-8', timeout=50)
    assert browser.returncode == 0, browser.stdout + browser.stderr
    report = json.loads((tmp_path / 'browser-result.json').read_text(encoding='utf-8'))
    assert report['ok'], report


def run_browser(directory):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    app = QApplication([])
    view = QWebEngineView()
    view.resize(1000, 700); view.show()
    view.setUrl(QUrl.fromLocalFile(str(directory / 'dist/index.html')))
    result = []
    until = time.monotonic() + 35
    while time.monotonic() < until:
        app.processEvents(); time.sleep(.005)
        pending = []
        view.page().runJavaScript('JSON.stringify(window.result || null)', pending.append)
        while not pending and time.monotonic() < until:
            app.processEvents(); time.sleep(.002)
        if pending and pending[0] and pending[0] != 'null':
            result.append(json.loads(pending[0])); break
    (directory / 'browser-result.json').write_text(json.dumps(result[0] if result else {'ok': False, 'error': 'timeout'},
                                                       ensure_ascii=False), encoding='utf-8')
    view.close(); app.processEvents()


if __name__ == '__main__':
    run_browser(Path(sys.argv[1]))
