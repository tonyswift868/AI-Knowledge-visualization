"""Real NewPlan + React Flow in Qt Chromium; only the host bridge is replaced."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.refactor.plan61.test_plan61_graph_browser import build_fixture as build_previous, fixtures
from tests.refactor.plan37.test_plan37_browser_contract import browser


def build_fixture(root, *, before=False, data=None):
    build_previous(root, data or fixtures())
    repo = Path(__file__).resolve().parents[3]
    entry = ('import ' + json.dumps((repo / 'refactor/learning/web/src/theme.css').as_posix())
             + ';import data from "./fixture.json";window.graph61Data=data;window.graph62Before='
             + json.dumps(before) + ';await import('
             + json.dumps((Path(__file__).parent / 'graph62_browser.tsx').as_posix()) + ');')
    (root / 'entry.tsx').write_text(entry, encoding='utf-8')
    deps = Path('D:/MentorFlow/.local/web-deps')
    result = subprocess.run(['D:/MentorFlow/.local/node/node.exe', str(deps / 'node_modules/vite/bin/vite.js'),
                             'build', '--config', str(root / 'vite.config.mjs')],
                            env=dict(os.environ, MENTORFLOW_WEB_DEPS=deps.as_posix()),
                            capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def test_plan62_panel_survives_refresh_canvas_and_switches_or_closes_explicitly(tmp_path):
    build_fixture(tmp_path)
    result = subprocess.run([sys.executable, '-B', str(Path(__file__)), str(tmp_path)],
                            capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    saved = json.loads((tmp_path / 'browser-result.json').read_text(encoding='utf-8'))
    assert saved['ok'] and all(row['survives_refresh'] for row in saved['modes'])


if __name__ == '__main__':
    browser(Path(sys.argv[1]))
