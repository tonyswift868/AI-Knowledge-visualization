"""Render production recommendation cards, including absent metadata."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parents[3]))
from tests.refactor.plan37.test_plan37_browser_contract import browser


def test_recommend97_actual_cards_stars_video_and_links(tmp_path):
    repo = Path(__file__).parents[3]
    node = Path('D:/MentorFlow/.local/node/node.exe')
    deps = Path('D:/MentorFlow/.local/web-deps')
    (tmp_path/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (tmp_path/'entry.tsx').write_text('import '+json.dumps(Path(__file__).with_name('recommend97_cards_fixture.tsx').as_posix()), encoding='utf-8')
    (tmp_path/'vite.config.mjs').write_text('import base from '+json.dumps((repo/'refactor/learning/web/vite.config.mjs').as_uri())+
        ';export default {...base,root:'+json.dumps(tmp_path.as_posix())+',build:{outDir:"dist",emptyOutDir:true}}', encoding='utf-8')
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=deps.as_posix())
    built = subprocess.run([str(node), str(deps/'node_modules/vite/bin/vite.js'), 'build', '--config', str(tmp_path/'vite.config.mjs')],
                           env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    assert built.returncode == 0, built.stdout+built.stderr
    ran = subprocess.run([sys.executable, '-B', str(Path(__file__)), str(tmp_path)], env=env,
                         capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45)
    assert ran.returncode == 0, ran.stdout+ran.stderr
    assert json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))['ok']


if __name__ == '__main__':
    browser(Path(sys.argv[1]))
