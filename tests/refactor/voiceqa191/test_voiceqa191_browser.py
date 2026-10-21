"""BUG-124: real Classroom/RecallQuestion/voice hook in offline Chromium."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
NODE = Path('D:/MentorFlow/.local/node/node.exe')
DEPS = Path('D:/MentorFlow/.local/web-deps')


def _states(directory: Path) -> Path:
    from unittest.mock import patch
    from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, act, question, PLAN
    from refactor.learning.app.classroom import Classroom

    env = make_environment(directory / 'fixture-data')
    try:
        opened = act(env, 'next')
        if opened.get('step') == 'opening':
            act(env, 'choose', 0)
        teaching = env.room.run(PLAN, lambda course, state, ctx: None)

        q = question('p0', 'short')
        q.update(kind='recall', reference='压力相同时，面积越小，压强越大。',
                 key_points=['压力相同', '面积越小，压强越大'])
        env.materials.bank[:] = [q]
        with patch.object(Classroom, '_materials', staticmethod(lambda: env.materials)):
            def show(course, state, ctx):
                state.update(step='testing', test={'attempt': 'voiceqa191', 'kind': 'module_test',
                             'queue': [q], 'index': 0, 'question_ids': [], 'asked': []})
                env.room._show_question(course, state, q, 'module_test', number=1, total=1)
            recall = env.room.run(PLAN, show)
        assert recall['question']['kind'] == 'recall'
        assert teaching['step'] in {'teaching', 'teach'}
        path = directory / 'states191.json'
        path.write_text(json.dumps({'teaching': teaching, 'recall': recall}, ensure_ascii=False), encoding='utf-8')
        return path
    finally:
        env.services._closed = True
        env.room.cancel_materials(PLAN)
        flow = getattr(env.services, '_material_flow', None)
        if flow is not None:
            jobs = flow._material_jobs
            with jobs['guard']:
                timer = jobs['timers'].pop(PLAN, None)
                if timer is not None: timer.cancel()
                for (plan_id, _), job in list(jobs['jobs'].items()):
                    if plan_id == PLAN: job['cancel'].set()
                jobs['queued'].pop(PLAN, None)
            deadline = time.monotonic() + 3
            while PLAN in jobs['active'] and time.monotonic() < deadline: time.sleep(.005)
            assert PLAN not in jobs['active'], 'PlanFlow worker did not stop before Store close'
        env.store.close()


def _build(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    states = _states(directory)
    fixture = Path(__file__).parent
    src = REPO / 'refactor/learning/web/src'
    (directory / 'index.html').write_text(
        '<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (directory / 'entry.tsx').write_text(
        'window.qt={webChannelTransport:{}};import(' + json.dumps((fixture / 'browser191.tsx').as_posix()) + ')', encoding='utf-8')
    aliases = [
        {'find': '../bridge', 'replacement': (fixture / 'bridge191.ts').as_posix()},
        {'find': '../teacher/TeacherStage', 'replacement': (REPO / 'tests/refactor/conversation37/conversation37_optional.tsx').as_posix()},
        {'find': '../voice', 'replacement': (src / 'voice/index.ts').as_posix()},
        {'find': './states191.json', 'replacement': states.as_posix()},
    ]
    config = f'''import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(DEPS.as_posix())};
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},
plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],
build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};'''
    (directory / 'vite.config.mjs').write_text(config, encoding='utf-8')
    result = subprocess.run([str(NODE), str(DEPS / 'node_modules/vite/bin/vite.js'), 'build', '--config',
                             str(directory / 'vite.config.mjs')], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=90,
                             env=dict(os.environ, MENTORFLOW_WEB_DEPS=str(DEPS)))
    assert result.returncode == 0, result.stdout + result.stderr


def _browser(directory: Path):
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
               QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu', PYTHONIOENCODING='utf-8',
               PYTHONDONTWRITEBYTECODE='1')
    host_log = (directory / 'qt.log').open('w', encoding='utf-8')
    result = subprocess.run([sys.executable, '-B', str(Path(__file__)), '--host', str(directory)],
                            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=70)
    host_log.close()
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((directory / 'result191.json').read_text(encoding='utf-8'))
    assert report['ok'], report
    assert len(list((directory / 'screenshots').glob('*.png'))) == 2


def test_voice191_recall_answer_and_bottom_composer_are_independent_in_chromium(tmp_path):
    if not NODE.is_file() or not (DEPS / 'node_modules/vite/bin/vite.js').is_file():
        pytest.skip('共享前端工具不存在')
    _build(tmp_path)
    _browser(tmp_path)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--host', type=Path)
    args = parser.parse_args()
    if args.host:
        import json, time
        from PySide6.QtCore import QUrl
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineUrlRequestInterceptor
        class Offline(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info):
                if info.requestUrl().scheme() in {'http', 'https'}: info.block(True)
        app = QApplication([]); view = QWebEngineView(); page = QWebEnginePage(view)
        block = Offline(page); page.profile().setUrlRequestInterceptor(block); view.setPage(page)
        view.resize(1000, 780); view.show(); view.setUrl(QUrl.fromLocalFile(str(args.host / 'dist/index.html')))
        until = time.monotonic() + 45; found = []; screenshots_captured = False
        def pump(seconds=.02):
            stop=time.monotonic()+seconds
            while time.monotonic()<stop: app.processEvents();time.sleep(.002)
        while time.monotonic()<until and not found:
            rows=[];page.runJavaScript('JSON.stringify(window.result191||null)',rows.append)
            end=time.monotonic()+1
            while not rows and time.monotonic()<end:pump(.01)
            if rows and rows[0] and rows[0]!='null': found.append(json.loads(rows[0]))
            else:
                ready=[];page.runJavaScript('!!window.screenshotReady191',ready.append);pump(.01)
                if ready and ready[0] and not screenshots_captured:
                    state=[]
                    page.runJavaScript("(() => { const card=document.querySelector('[aria-label=\\\"问答题\\\"]'); const answer=card?.querySelector('[aria-label=\\\"先写下你的回答\\\"]'); return !!answer && !answer.readOnly && !!card.querySelector('[data-testid=\\\"recall-answer-mic\\\"]') && [...card.querySelectorAll('button')].some(b=>b.textContent.trim()==='写好了，看参考答案'); })()",state.append)
                    deadline=time.monotonic()+1
                    while not state and time.monotonic()<deadline:pump(.01)
                    if not state or state[0] is not True:
                        raise AssertionError(f'feature screenshot must show editable answer and answer microphone: {state!r}')
                    (args.host/'screenshots').mkdir(exist_ok=True)
                    for width,theme,name in [(1120,'sakura','recall-light.png'),(620,'aurora','recall-dark-narrow.png')]:
                        view.resize(width,780);page.runJavaScript(f'document.documentElement.dataset.theme={json.dumps(theme)}')
                        pump(.25)
                        rows=[];page.runJavaScript('document.documentElement.scrollWidth<=innerWidth',rows.append);pump(.05)
                        if not rows or rows[0] is not True: raise AssertionError(f'horizontal overflow at {width}px')
                        view.grab().save(str(args.host/'screenshots'/name))
                    screenshots_captured = True
                    page.runJavaScript('window.resumeAfterScreenshot191=true');pump(.05)
        report=found[0] if found else {'ok':False,'error':'browser harness timed out'}
        if not (args.host/'screenshots/recall-light.png').is_file(): report.setdefault('errors',[]).append('feature screenshot was not captured')
        (args.host/'result191.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        view.close();pump()
        if not report.get('ok'): raise SystemExit(json.dumps(report,ensure_ascii=False))
