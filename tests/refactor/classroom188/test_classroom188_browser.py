"""BUG-121 frontend wait/gating behavior in real React Chromium with fake bridge."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
NODE = Path('D:/MentorFlow/.local/node/node.exe')
DEPS = Path('D:/MentorFlow/.local/web-deps')


def _room(step='teaching', *, title='模块一', module_id='m1', readiness=False, notice=False):
    return {
        'plan': {'id': 'a' * 32, 'goal': '学习压强', 'state': 'active'},
        'module': {'id': module_id, 'title': title, 'number': 1 if module_id == 'm1' else 2,
                   'total': 2, 'state': 'untested', 'navigation': []},
        'points': [{'id': f'{module_id}-p1', 'title': '面积与压强', 'minutes': 15,
                    'taught': False, 'state': 'untested', 'current': True}],
        'step': step, 'question': None,
        'messages': [{'id': f'{module_id}-lesson', 'role': 'assistant', 'kind': 'step',
                      'content': '老师照着讲义解释面积与压强。', 'sources': []}],
        'can_skip': True, 'red': [], 'more_modules': True, 'check': None,
        'conversation': {'point_id': f'{module_id}-p1', 'options': [], 'interrupted': False},
        'teaching_version': None, 'concept': None, 'direct_review': None,
        'preparation': {'done': 0, 'total': 4, 'text': '正在准备题目', 'error': None},
        'test_readiness': {'module_test': readiness, 'skip_test': readiness, 'retest': readiness, 'check': False},
        'material_notice': ({'module_id': module_id, 'kind': 'module_test',
                             'message': '这个模块的题还在准备，好了再考'} if notice else None),
        'preparation_error': None,
        'deferred_module_tests': [],
        'module_options': [
            {'module_id': 'm1', 'title': '模块一', 'current': module_id == 'm1', 'ready': readiness,
             'state': 'untested', 'step': step},
            {'module_id': 'm2', 'title': '模块二', 'current': module_id == 'm2', 'ready': True,
             'state': 'untested', 'step': 'teach'},
        ],
        'failure': None, 'explanation': None,
        'today': {'today_target': 30, 'today_confirmed': 0, 'today_tentative': 0,
                  'debt_minutes': 0, 'ahead_minutes': 0},
    }


def _write_states(directory):
    teaching = _room('teaching', readiness=False)
    test_offer = _room('test_offer', readiness=False, notice=True)
    test_offer['points'][0]['taught'] = True
    slow_test_offer = json.loads(json.dumps(test_offer))
    prep_error = _room('teach', readiness=False)
    prep_error['preparation_error'] = {'module_id': 'm1', 'message': '题目准备没有启动，课堂可以继续；需要检测时可重试。'}
    test_offer['deferred_module_tests'] = []
    module2 = _room('teach', title='模块二', module_id='m2')
    module2['deferred_module_tests'] = [{'module_id': 'm1', 'title': '模块一', 'ready': True,
                                         'kind': 'module_test', 'step': 'test_offer'}]
    module2['module_options'][0].update(current=False, state='deferred', step='test_offer')
    module2['module_options'][1]['current'] = True
    reply = _room('teaching', readiness=False)
    reply['messages'].append({'id': 'new-reply', 'role': 'assistant', 'kind': 'step_reply',
                              'content': '新回复已接上。', 'sources': []})
    testing = _room('testing', readiness=True)
    slow_objective = json.loads(json.dumps(testing))
    slow_objective['question'] = {'id': 'objective-q1', 'kind': 'objective', 'question_type': 'short',
                                  'purpose': 'module_test', 'source': {'id': 'c1', 'title': '资料',
                                  'url': None, 'locator': '第 1 段', 'text': '资料原文'}}
    testing['messages'].append({'id': 'test-ready', 'role': 'system', 'kind': 'notice',
                                'content': '检测题已准备好。', 'sources': []})
    preparing = _room('preparing_materials', readiness=False)
    json_path = directory / 'states188.json'
    json_path.write_text(json.dumps({'teaching': teaching, 'slowTeaching': teaching,
                                     'slowTestOffer': slow_test_offer, 'prepError': prep_error,
                                     'slowObjective': slow_objective,
                                     'testOffer': test_offer, 'module2': module2,
                                     'reply': reply, 'testing': testing, 'preparing': preparing},
                                    ensure_ascii=False), encoding='utf-8')
    return json_path


def _build(directory):
    directory.mkdir(parents=True, exist_ok=True)
    _write_states(directory)
    here = Path(__file__).parent
    aliases = [
        {'find': '../bridge', 'replacement': (here / 'bridge188.ts').as_posix()},
        {'find': '../teacher/TeacherStage', 'replacement': (here.parent / 'conversation37/conversation37_optional.tsx').as_posix()},
        {'find': '../voice', 'replacement': (here.parent / 'conversation37/conversation37_optional.tsx').as_posix()},
        {'find': './states188.json', 'replacement': (directory / 'states188.json').as_posix()},
    ]
    (directory / 'entry.tsx').write_text('import ' + json.dumps((here / 'browser188.tsx').as_posix()), encoding='utf-8')
    (directory / 'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    config = f"""import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(DEPS.as_posix())};
const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},
plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],
build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};"""
    (directory / 'vite.config.mjs').write_text(config, encoding='utf-8')
    subprocess.run([str(NODE), str(DEPS / 'node_modules/vite/bin/vite.js'), 'build', '--config', str(directory / 'vite.config.mjs')],
                   check=True, capture_output=True, text=True, encoding='utf-8', timeout=90)


def _browser(directory):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
               QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',
               QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',
               MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    log_path = directory / 'qt.log'
    with log_path.open('w', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-B', str(Path(__file__)), '--host', str(directory)],
                                 env=env, stdout=log, stderr=log)
        try:
            result = subprocess.run([str(NODE), str(Path(__file__).with_name('browser188.mjs')), str(directory)],
                                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=100)
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            (directory / 'done').touch()
            child.wait(timeout=15)


def test_classroom188_no_questions_wait_gate_and_slow_action_takeover(tmp_path):
    _build(tmp_path)
    _browser(tmp_path)
    result = json.loads((tmp_path / 'result188.json').read_text(encoding='utf-8'))
    assert result['ok'], result
    assert len(list((tmp_path / 'screenshots').glob('*.png'))) >= 4


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--host', type=Path)
    args = parser.parse_args()
    if args.host:
        from tests.refactor.skip127.test_skip127_browser import host127
        host127(args.host)
