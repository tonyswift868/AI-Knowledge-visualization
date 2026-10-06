"""Real NewPlan in offline Qt Chromium; bridge replacement is explicitly synthetic."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.refactor.plan61.test_plan61_graph_browser import build_fixture as previous, fixtures
from tests.refactor.plan37.test_plan37_browser_contract import browser


def data():
    value = fixtures()
    ranges = dict(total=[101,151],daily=[51,76],cap=400,over_cap=False,points=[8,12],learning=[64,96],
        module_check=[16,24],final_check=[21,31],input_tokens=[300,900],suggested_cap=80,suggested_end='2026-10-15')
    value['structure']['material_structure']['ranges'] = ranges
    for m in value['structure']['material_structure']['modules']: m['estimated_points']=[4,6]
    value['over'] = copy.deepcopy(value['structure'])
    value['over']['material_structure']['ranges'].update(cap=40,over_cap=True)
    value['outline']['outline']['ranges'].update(points=12,learning=96,suggested_cap=80,suggested_end='2026-10-15')
    value['outline']['estimate']=dict(modules=2,input_tokens=[0,0],output_tokens=[0,0],amount=None,model='')
    value['outline']['messages'].append(dict(id='estimate',role='ai',kind='estimate',text='精确预估'))
    return value


def build_fixture(root, value=None):
    previous(root, value or data())
    repo=Path(__file__).resolve().parents[3]
    entry='import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';import data from "./fixture.json";window.graph61Data=data;await import('+json.dumps(Path(__file__).with_name('estimate63_browser.tsx').as_posix())+');'
    (root/'entry.tsx').write_text(entry,encoding='utf-8')
    deps=Path('D:/MentorFlow/.local/web-deps')
    r=subprocess.run(['D:/MentorFlow/.local/node/node.exe',str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],
        env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps.as_posix()),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert r.returncode == 0, r.stdout+r.stderr


def test_plan63_structure_and_precise_cards_use_user_cap_and_host_form_actions(tmp_path):
    build_fixture(tmp_path)
    r=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=45)
    assert r.returncode==0,r.stdout+r.stderr


if __name__=='__main__':
    root=Path(sys.argv[1])
    browser(root,root if '--artifacts' in sys.argv else None)
