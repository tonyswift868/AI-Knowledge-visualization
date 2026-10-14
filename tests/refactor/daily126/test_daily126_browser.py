import json
import os
from pathlib import Path
import subprocess
import sys

from refactor.learning.course.store import CourseStore
from tests.refactor.restructure114.test_restructure114_contract import seed
from tests.refactor.daily126.test_plan_daily126 import flow_for


def test_daily126_real_detail_browser_and_cap(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    here = Path(__file__).parent
    with CourseStore('browser126', tmp_path/'data') as store:
        seed(store)
        flow = flow_for(store)
        first = flow.change_window('p', dict(end='2026-10-22', daily_cap=10))
        store.replan_plan('p', flow.study_day(), cause='debt_spread')
        second = flow.detail('p')
    (tmp_path/'states126.json').write_text(json.dumps(dict(first=first, second=second), ensure_ascii=False), encoding='utf8')
    (tmp_path/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf8')
    (tmp_path/'entry.tsx').write_text('import '+json.dumps((here/'browser126.tsx').as_posix()), encoding='utf8')
    deps = Path('D:/MentorFlow/.local/web-deps')
    aliases = [dict(find='../bridge', replacement=(here/'bridge126.ts').as_posix()),
               dict(find='./states126.json', replacement=(tmp_path/'states126.json').as_posix()),
               dict(find='../voice', replacement=(repo/'tests/refactor/conversation37/conversation37_optional.tsx').as_posix())]
    config = ('import base from '+json.dumps((repo/'refactor/learning/web/vite.config.mjs').as_uri())+
              ';export default {...base,root:'+json.dumps(tmp_path.as_posix())+',resolve:{alias:'+json.dumps(aliases)+
              '},build:{outDir:"dist",emptyOutDir:true}}')
    (tmp_path/'vite.config.mjs').write_text(config, encoding='utf8')
    result = subprocess.run(['D:/MentorFlow/.local/node/node.exe', str(deps/'node_modules/vite/bin/vite.js'),
                             'build', '--config', str(tmp_path/'vite.config.mjs')],
                            env=dict(os.environ, MENTORFLOW_WEB_DEPS=str(deps)), capture_output=True, text=True, encoding='utf8', timeout=90)
    assert result.returncode == 0, result.stdout+result.stderr
    browser = repo/'tests/refactor/conversation37/test_conversation37_web.py'
    result = subprocess.run([sys.executable, '-B', str(browser), '--browser', str(tmp_path)],
                            capture_output=True, text=True, encoding='utf8', errors='replace', timeout=70)
    assert result.returncode == 0, result.stdout+result.stderr
