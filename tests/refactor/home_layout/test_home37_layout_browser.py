"""Render the real Home page and CSS at narrow/wide desktop sizes, offline."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def test_home37_long_titles_multiple_plans_and_class_actions(tmp_path):
    local = Path(os.environ.get('MF_WEB_TOOLCHAIN', 'D:/MentorFlow/.local'))
    node, deps = local / 'node/node.exe', local / 'web-deps'
    if not node.is_file() or not (deps / 'node_modules/vite/bin/vite.js').is_file():
        pytest.skip('shared offline web toolchain unavailable')
    fixture = Path(__file__).parent
    (tmp_path / 'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (tmp_path / 'entry.tsx').write_text(f'import {json.dumps((fixture / "home37_browser.tsx").as_posix())}', encoding='utf-8')
    aliases = [{'find': '../bridge', 'replacement': (fixture / 'home37_bridge.ts').as_posix()}]
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
    report = json.loads((tmp_path / 'home37-result.json').read_text(encoding='utf-8'))
    assert len(report) == 2 and all(r['ok'] for r in report), report


def run_browser(directory):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    app = QApplication([])
    view = QWebEngineView()
    view.resize(900, 1000)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(directory / 'dist/index.html')))
    results = []
    for width in (900, 1600):
        view.resize(width, 1000)
        until = time.monotonic() + 15
        result = None
        while time.monotonic() < until:
            app.processEvents()
            time.sleep(.01)
            pending = []
            view.page().runJavaScript('JSON.stringify(window.home37Check?.() || null)', pending.append)
            while not pending and time.monotonic() < until:
                app.processEvents()
                time.sleep(.002)
            if pending and pending[0] and pending[0] != 'null':
                result = json.loads(pending[0])
                if result.get('ready') and result.get('width') == width:
                    break
        results.append(result or {'ok': False, 'error': 'timeout', 'width': width})
        # Let Qt present the completed React frame before capturing it.
        settled = time.monotonic() + 1.5
        while time.monotonic() < settled:
            app.processEvents()
            time.sleep(.005)
        view.grab().save(str(directory / f'home-{width}.png'))
    (directory / 'home37-result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    view.close()
    app.processEvents()


if __name__ == '__main__':
    run_browser(Path(sys.argv[1]))
