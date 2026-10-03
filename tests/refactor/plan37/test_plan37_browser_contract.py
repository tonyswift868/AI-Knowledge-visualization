"""Offline Chromium renders the real NewPlan/PlanDetail pages with a fake bridge."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def build_fixture(root):
    deps = Path("D:/MentorFlow/.local/web-deps")
    node = Path("D:/MentorFlow/.local/node/node.exe")
    if not node.is_file(): pytest.skip("Shared web toolchain unavailable")
    root.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).parent
    (root / "index.html").write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (root / "entry.tsx").write_text('import ' + json.dumps((here / "plan37_browser.tsx").as_posix()), encoding="utf-8")
    config = '''import path from "node:path"
import { pathToFileURL } from "node:url"
const deps = DEPS
const { default: react } = await import(pathToFileURL(path.join(deps, "node_modules/@vitejs/plugin-react/dist/index.js")))
export default { root: ROOT, base: "./", resolve: { alias: [{find:"../bridge",replacement:BRIDGE}] },
 plugins:[react(), {name:"deps",enforce:"pre",async resolveId(source,importer,options) {
 if (!importer || source.startsWith(".") || source.startsWith("/") || source.includes(":")) return null
 if (importer.includes("node_modules")) return null
 return this.resolve(source,path.join(deps,"package.json"),{...options,skipSelf:true})
 }}],build:{outDir:"dist",emptyOutDir:true} }
'''.replace('DEPS', json.dumps(deps.as_posix())).replace('ROOT', json.dumps(root.as_posix())).replace('BRIDGE', json.dumps((here / "plan37_bridge.ts").as_posix()))
    (root / "vite.config.mjs").write_text(config, encoding="utf-8")
    result = subprocess.run([str(node), str(deps / 'node_modules/vite/bin/vite.js'), 'build', '--config', str(root / 'vite.config.mjs')], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def test_plan37_page_flow_with_checkboxes_quotes_and_time_gate(tmp_path):
    build_fixture(tmp_path)
    result = subprocess.run([sys.executable, '-B', str(Path(__file__)), str(tmp_path)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / 'browser-result.json').read_text(encoding='utf-8'))['ok']


def browser(root, artifacts=None):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ('http', 'https'): info.block(True)
    app = QApplication([])
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    interceptor = Offline(page)
    profile.setUrlRequestInterceptor(interceptor)
    view.setPage(page)
    view.resize(1280, 900)
    view.show()
    def pump(seconds=.05):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(.005)
    def evaluate(script):
        replies = []
        page.runJavaScript(script, replies.append)
        end = time.monotonic() + 3
        while not replies and time.monotonic() < end: pump()
        assert replies, 'JS timeout'
        return replies[0]
    view.setUrl(QUrl.fromLocalFile(str(root / 'dist/index.html')))
    result = None
    end = time.monotonic() + 30
    while time.monotonic() < end:
        pump()
        raw = evaluate('JSON.stringify(window.plan37Result || null)')
        if raw and raw != 'null':
            result = json.loads(raw)
            break
    result = result or {'ok': False, 'error': 'timed out'}
    if artifacts and result['ok']:
        for theme, width in [('sakura',1280),('aurora',1280),('sakura',600)]:
            view.resize(width,900)
            evaluate(f'window.plan37Show({json.dumps(theme)}); true')
            pump(.2)
            assert not evaluate('document.documentElement.scrollWidth > innerWidth'), theme
            view.grab().save(str(artifacts / f'plan-{theme}-{width}.png'))
    (root / 'browser-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    assert result['ok'], result
    view.close()
    page.deleteLater()
    pump()


if __name__ == '__main__':
    directory = Path(sys.argv[1])
    if '--artifacts' in sys.argv:
        build_fixture(directory)
    browser(directory, directory if '--artifacts' in sys.argv else None)
