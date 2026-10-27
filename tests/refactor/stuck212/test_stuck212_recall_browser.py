"""BUG-137 browser reproduction: long recall explanations must not bury the next action."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[3]
SCRATCH = Path(os.environ.get('MF_TEST_SCRATCH', 'D:/MentorFlow/.local/test-tmp/stuck212/ui'))
FIXTURE = Path(__file__).parent


def build(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    deps = Path('D:/MentorFlow/.local/web-deps')
    node = Path('D:/MentorFlow/.local/node/node.exe')
    aliases = [
        {'find': '../bridge', 'replacement': (FIXTURE / 'recall212_bridge.ts').as_posix()},
        {'find': '../teacher/TeacherStage', 'replacement': (REPO / 'tests/refactor/direct97/direct97_optional.tsx').as_posix()},
        {'find': '../voice', 'replacement': (REPO / 'tests/refactor/direct97/direct97_optional.tsx').as_posix()},
    ]
    (root / 'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (root / 'entry.tsx').write_text(
        'import ' + json.dumps((REPO / 'refactor/learning/web/src/theme.css').as_posix()) + ';import ' +
        json.dumps((FIXTURE / 'recall212_browser.tsx').as_posix()) + ';', encoding='utf-8')
    config = f'''import path from 'node:path'
import {{pathToFileURL}} from 'node:url'
const deps={json.dumps(deps.as_posix())}
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')))
export default {{root:{json.dumps(root.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},
plugins:[react(),{{name:'shared-deps',enforce:'pre',async resolveId(s,i,o){{
if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null
return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})
}}}}],build:{{outDir:'dist',emptyOutDir:true,target:'esnext'}}}}
'''
    (root / 'vite.config.mjs').write_text(config, encoding='utf-8')
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=deps.as_posix(), PYTHONDONTWRITEBYTECODE='1')
    built = subprocess.run([str(node), str(deps / 'node_modules/vite/bin/vite.js'), 'build', '--config', str(root / 'vite.config.mjs')],
                           env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    assert built.returncode == 0, built.stdout + built.stderr


def browser(root: Path, screenshots: Path):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView

    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ('http', 'https'):
                info.block(True)

    app = QApplication([])
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)
    interceptor = Offline(page)
    profile.setUrlRequestInterceptor(interceptor)
    view.setPage(page)
    view.resize(1280, 900)
    view.show()

    def pump(seconds=.025):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)

    def js(script):
        result = []
        page.runJavaScript(script, result.append)
        deadline = time.monotonic() + 6
        while not result and time.monotonic() < deadline:
            pump()
        assert result, f'JavaScript callback timeout: {script}'
        return result[0]

    def wait(script, label):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if js(script):
                return
            pump()
        details = js('JSON.stringify({progress:window.stuck212Progress||null,text:document.body.innerText.slice(-3000),buttons:[...document.querySelectorAll("button")].map(b=>({text:b.textContent,disabled:b.disabled}))})')
        raise AssertionError(f'timed out: {label}: {details}')

    view.setUrl(QUrl.fromLocalFile(str(root / 'dist/index.html')))
    wait('!!window.stuck212 && !!document.querySelector(".recall-question")', 'classroom renders')
    wait('!!window.stuck212Result', 'scenario matrix')
    result = js('JSON.stringify(window.stuck212Result)')
    screenshots.mkdir(parents=True, exist_ok=True)
    for theme in ('sakura', 'aurora'):
        js(f'document.documentElement.dataset.theme={json.dumps(theme)}')
        pump(.2)
        assert view.grab().save(str(screenshots / f'long-recall-{theme}.png'))
    view.close()
    pump()
    parsed = json.loads(result)
    assert parsed['ok'], parsed
    return parsed


def test_stuck212_long_recall_judgement_scroll_and_continue(tmp_path):
    root = SCRATCH / 'browser-build'
    shots = SCRATCH / 'screenshots'
    root.mkdir(parents=True, exist_ok=True)
    build(root)
    runner = subprocess.run([sys.executable, '-B', str(Path(__file__)), '--browser', str(root), '--shots', str(shots)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8'))
    assert runner.returncode == 0, runner.stdout + runner.stderr
    result_path = shots / 'result.json'
    result = json.loads(result_path.read_text(encoding='utf-8'))
    assert result['ok'], result
    assert set(result['results']) == {
        f'{scenario}-{outcome}' for scenario in ('check', 'module', 'practice', 'classroom')
        for outcome in ('wrong', 'right')
    } | {'interactive-check-wrong'}
    for name, metrics in result['results'].items():
        assert metrics['scrollable'], f'{name}: long transcript cannot scroll: {metrics}'
        assert metrics['outsideExplanationHeight'] == 0, f'{name}: explanation remains outside the chat scroller: {metrics}'
        assert metrics['outsideExplanationHeight'] == 0, f'{name}: explanation remains outside the chat scroller: {metrics}'
        assert metrics['merchantLineCount'] == 1, f'{name}: duplicate merchant line remains: {metrics}'
        if name.endswith('-right'):
            assert metrics['referenceVisible'], f'{name}: correct recall must retain its reference answer: {metrics}'
        if name == 'interactive-check-wrong':
            assert metrics['nextButton'] == '下一题', f'{name}: explicit next action missing: {metrics}'


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', type=Path)
    parser.add_argument('--shots', type=Path)
    args = parser.parse_args()
    try:
        result = browser(args.browser, args.shots)
        (args.shots / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        details = {'ok': False, 'error': repr(exc)}
        if args.shots:
            args.shots.mkdir(parents=True, exist_ok=True)
            (args.shots / 'result.json').write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
        raise
