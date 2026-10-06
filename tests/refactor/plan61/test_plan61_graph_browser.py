"""Build the actual NewPlan page, fake only its bridge, and run in Qt Chromium."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.refactor.plan37.test_plan37_browser_contract import browser


def fixtures():
    citation = dict(chunk_id='c1', title='原资料', locator='第 1 段', quote='计算机能处理数据。', url='https://example.test/source')
    modules = [dict(key=f'm{i}', title=f'模块 {i+1}', summary='学会解释资料中的概念和区别。', chapters=[f'依据章节 {j+1}' for j in range(90)],
                    refs=[f's1.{j}' for j in range(90)], ref_titles=['原资料'], prerequisites=[f'm{i-1}'] if i else [], minutes=[30,45],
                    points=[dict(title=f'点 {i}-{j}',summary='掌握目标：能解释这一概念。', citations=[citation]) for j in range(162 if i==0 else 1)]) for i in range(2)]
    base = dict(plan_id='graph61',goal='学习路线',form=None,busy=None,sources=[],generation={})
    structure = dict(base, phase='material_preview', outline=None, material_structure=dict(version=2,modules=modules,missing=['人工智能应用']), messages=[
        dict(id='s',role='ai',kind='sources',text='资料已导入'),dict(id='error',role='ai',kind='error',text='旧截断错误'),
        dict(id='old',role='ai',kind='material_preview',text='原来的超长章节正文',version=1),dict(id='current',role='ai',kind='material_preview',text='原来的超长章节正文',version=2)])
    outline = dict(base, phase='outline', messages=[dict(id='o',role='ai',kind='outline',text='完整知识地图',version=1)],
        outline=dict(version=1,stages=[dict(title='基础阶段',modules=modules)],ranges=dict(total=[100,120],daily=[20,30],cap=25,over_cap=True,suggested_end=None)))
    return dict(structure=structure,outline=outline)


def build_fixture(root, data=None):
    root.mkdir(parents=True,exist_ok=True)
    repo=Path(__file__).resolve().parents[3];here=Path(__file__).parent
    deps=Path('D:/MentorFlow/.local/web-deps');node=Path('D:/MentorFlow/.local/node/node.exe')
    (root/'fixture.json').write_text(json.dumps(data or fixtures(),ensure_ascii=False),encoding='utf-8')
    (root/'index.html').write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (root/'entry.tsx').write_text('import '+json.dumps((repo/'refactor/learning/web/src/theme.css').as_posix())+';\nimport data from "./fixture.json"; window.graph61Data=data;\nawait import('+json.dumps((here/'graph61_browser.tsx').as_posix())+');',encoding='utf-8')
    config='import base from '+json.dumps((repo/'refactor/learning/web/vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',resolve:{alias:[{find:"../bridge",replacement:'+json.dumps((here/'graph61_bridge.ts').as_posix())+'}]},build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}'
    (root/'vite.config.mjs').write_text(config,encoding='utf-8')
    r=subprocess.run([str(node),str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps.as_posix()),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert r.returncode==0,r.stdout+r.stderr


def test_plan61_interactive_graph_preserves_details_and_filters_resolved_errors(tmp_path):
    build_fixture(tmp_path)
    r=subprocess.run([sys.executable,'-B',str(Path(__file__)),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=45)
    assert r.returncode==0,r.stdout+r.stderr
    assert json.loads((tmp_path/'browser-result.json').read_text(encoding='utf-8'))['ok']


if __name__=='__main__':
    browser(Path(sys.argv[1]))
