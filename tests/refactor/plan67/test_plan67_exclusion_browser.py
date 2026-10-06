"""Render actual NewPlan in Qt WebEngine, with an explicit offline bridge."""
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))

from tests.refactor.plan61.test_plan61_graph_browser import build_fixture as build_graph, fixtures
from tests.refactor.plan37.test_plan37_browser_contract import browser


def data():
    value=fixtures()
    value['structure']['material_structure']['excluded']=[
        dict(name='导航/学习路线',reason='学习安排不是知识',refs=['s1.90']),
        dict(name='求职/简历',reason='求职流程不是知识',refs=['s1.91'])]
    return value


def build_fixture(root, value=None):
    build_graph(root,value or data())
    repo=Path(__file__).resolve().parents[3]
    (root/'entry.tsx').write_text('import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';import data from "./fixture.json";window.graph61Data=data;await import('+json.dumps(Path(__file__).with_name('exclusion67_browser.tsx').as_posix())+');',encoding='utf-8')
    deps=Path('D:/MentorFlow/.local/web-deps')
    result=subprocess.run(['D:/MentorFlow/.local/node/node.exe',str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps.as_posix()),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr


def test_plan67_exclusions_visible_before_confirmation(tmp_path):
    build_fixture(tmp_path)
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=45)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':browser(Path(sys.argv[1]))
