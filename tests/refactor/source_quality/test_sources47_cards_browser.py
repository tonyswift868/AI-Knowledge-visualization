"""Render actual source cards offline; fallback is visibly manual and labelled."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parents[3]))
from tests.refactor.plan37.test_plan37_browser_contract import browser


def test_sources47_card_coverage_warning_and_manual_adoption(tmp_path):
    repo = Path(__file__).parents[3]
    node = Path('D:/MentorFlow/.local/node/node.exe')
    deps = Path('D:/MentorFlow/.local/web-deps')
    tmp_path.joinpath('index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    fixture = Path(__file__).with_name('sources47_cards_fixture.tsx')
    tmp_path.joinpath('entry.tsx').write_text('import '+json.dumps(fixture.as_posix()), encoding='utf-8')
    config = ('import base from '+json.dumps((repo/'refactor/learning/web/vite.config.mjs').as_uri())+
        '; export default {...base,root:'+json.dumps(tmp_path.as_posix())+
        ',build:{outDir:"dist",emptyOutDir:true}}')
    config_path = tmp_path/'vite.config.mjs';config_path.write_text(config,encoding='utf-8')
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=deps.as_posix())
    result = subprocess.run([str(node), str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(config_path)],
        env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    assert result.returncode == 0,result.stdout+result.stderr
    result = subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],env=env,
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
    assert result.returncode == 0,result.stdout+result.stderr
    assert json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))['ok']


if __name__ == '__main__':
    browser(Path(sys.argv[1]))
