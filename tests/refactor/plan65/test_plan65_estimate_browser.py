"""Real NewPlan and Qt Chromium, explicit fake bridge for offline assertions."""
import copy,json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from tests.refactor.plan63.test_plan63_estimate_browser import data as previous_data
from tests.refactor.plan61.test_plan61_graph_browser import build_fixture as previous_build
from tests.refactor.plan37.test_plan37_browser_contract import browser

def data():
    value=previous_data()
    base=value['structure']
    base['form']=dict(goal='ai-agent学习',final_goal='面试',level='beginner',level_note='',notes='',start='2026-10-06',end='2026-10-30',daily_cap=600,weekday_weights=[1]*7,special_days=[],only_mine=False)
    base['goal']='在 2026-10-06 至 2026-10-15 的 10 天内，掌握 Java 后端和 AI Agent，达到面试程度。'
    base['material_structure']['ranges'].update(total=[3428,19278],daily=[138,772],cap=600,over_cap=False,points=[272,1530],warning_level='none',
        typical=dict(points=987,learning=7896,total=[12437]*2,daily=[498]*2,cap=600,over_cap=False,suggested_cap=500,suggested_end=None))
    for m in base['material_structure']['modules']:m['estimated_point_count']=48
    value['possible']=copy.deepcopy(base);r=value['possible']['material_structure']['ranges']
    value['possible']['form']['daily_cap']=450
    r.update(cap=450,over_cap=True,warning_level='possible');r['typical'].update(cap=450,over_cap=True,suggested_end='2026-11-02')
    value['impossible']=copy.deepcopy(value['possible']);r=value['impossible']['material_structure']['ranges']
    value['impossible']['form'].update(end='2026-10-20',daily_cap=200)
    r.update(cap=200,daily=[229,1286],warning_level='impossible');r['typical'].update(cap=200,daily=[830]*2,suggested_cap=830,suggested_end='2026-12-07')
    return value

def build_fixture(root,value=None):
    previous_build(root,value or data())
    repo=Path(__file__).resolve().parents[3]
    entry='import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';import data from "./fixture.json";window.graph61Data=data;await import('+json.dumps(Path(__file__).with_name('estimate65_browser.tsx').as_posix())+');'
    (root/'entry.tsx').write_text(entry,encoding='utf-8')
    deps=Path('D:/MentorFlow/.local/web-deps')
    r=subprocess.run(['D:/MentorFlow/.local/node/node.exe',str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps.as_posix()),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert r.returncode==0,r.stdout+r.stderr

def test_plan65_browser_usual_warnings_and_current_header(tmp_path):
    build_fixture(tmp_path)
    r=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=45)
    assert r.returncode==0,r.stdout+r.stderr

if __name__=='__main__':browser(Path(sys.argv[1]))
