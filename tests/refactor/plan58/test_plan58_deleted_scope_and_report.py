"""Replay small57's saved scope change and report; no network or real model."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow, report_markdown
from refactor.learning.course import planner_agents as agents
from tests.refactor.plan51.test_plan51_selected_material_flow import flow


def evidence():
    return json.loads(Path(__file__).with_name('small57_scope.json').read_text(encoding='utf-8'))


def sections(data):
    return agents.build_sections(data['sources'], {data['sources'][0]['id']: data['chunks']}, max_depth=None)


def removed(data):
    chosen = {r for m in data['selected']['modules'] for r in m['refs']}
    return [dict(kind='chapter', title=s.title, source_id=s.source_id)
            for s in sections(data) if s.id not in chosen]


def test_plan58_small57_change_persists_removed_chapters_on_reopen(flow, monkeypatch):
    f, ctx, pid, model = flow
    data = evidence()
    monkeypatch.setattr(f, '_sections', lambda d: (data['sources'], sections(data)))
    monkeypatch.setattr(model, 'complete', lambda *a, **k: SimpleNamespace(text=json.dumps(data['selected'])))
    d = f.load(pid)
    d['material_structure'] = copy.deepcopy(data['previous'])
    d['phase'] = 'material_preview'
    f.material_structure(d, ctx, data['instructions'][0])
    reopened = PlanFlow(f.services, f.push, f.study_day, fetcher=f.fetcher).load(pid)
    assert reopened['removed_content'] == removed(data)
    assert reopened['material_structure']['removed_content'] == removed(data)
    # A subsequent confirmation/revision must not forget prior exclusions.
    f.material_structure(reopened, ctx, data['instructions'][1], confirmed=True)
    assert f.load(pid)['removed_content'] == removed(data)


def generated(flow, monkeypatch, data, response=None):
    f, ctx, pid, model = flow
    captured = []
    def complete(purpose, messages, **kwargs):
        captured.append((kwargs['system'], messages))
        return SimpleNamespace(text=response or data['reply'])
    monkeypatch.setattr(model, 'complete', complete)
    module = dict(data['selected']['modules'][-1], id='m3', points=[])
    ids = {cid for s in sections(data) if s.id in module['refs'] for cid in s.chunk_ids}
    chunks = f._module_chunks([c for c in data['chunks'] if c['id'] in ids],
                             {s['id']: s['title'] for s in data['sources']})
    # source identity is needed to distinguish equal chapter names in two files.
    for c in chunks: c['source_id'] = data['sources'][0]['id']
    draft = dict(f.load(pid), material_map=True, removed_content=removed(data),
                 outline={'stages': [{'title': '概念', 'modules': [module]}]})
    result = f._generate_batch(draft, module, chunks, None, (1, 1), ctx, max_points=30)
    return result[0], captured


def test_plan58_small57_reply_drops_overview_only_point_keeps_four_devices(flow, monkeypatch):
    data = evidence()
    points, requests = generated(flow, monkeypatch, data)
    assert [p['title'] for p in points] == ['台式机与笔记本电脑', '平板电脑', '服务器']
    assert len(requests) == 1
    text = requests[0][1][0]['content']
    assert '不要提炼' in text and 'Other types of computers' in text and 'PCs and Macs' in text
    assert all(agents.resolve_paragraph(next(c for c in data['chunks'] if c['id'] == cite['chunk_id']), cite)
               == cite['quote'] for p in points for cite in p['citations'])


def test_plan58_deleted_title_rejected_even_with_retained_body(flow, monkeypatch):
    data = evidence()
    response = json.loads(data['reply'])
    response['points'][0].update(title='PCs and Macs', chunks=['c4'])
    points, _ = generated(flow, monkeypatch, data, json.dumps(response))
    assert len(points) == 3


def test_plan58_all_removed_uses_existing_single_format_retry(flow, monkeypatch):
    f, ctx, pid, model = flow
    data = evidence()
    count = []
    def complete(purpose, messages, **kwargs):
        count.append(messages)
        return SimpleNamespace(text=json.dumps({'points': json.loads(data['reply'])['points'][:1]}))
    monkeypatch.setattr(model, 'complete', complete)
    module = dict(data['selected']['modules'][-1], id='m3')
    chunks = f._module_chunks(data['chunks'][3:8], {data['sources'][0]['id']: '资料'})
    for c in chunks: c['source_id'] = data['sources'][0]['id']
    d = dict(f.load(pid), material_map=True, removed_content=removed(data),
             outline={'stages': [{'title': '概念', 'modules': [module]}]})
    with pytest.raises(Exception, match='格式|删除|范围'):
        f._generate_batch(d, module, chunks, None, (1, 1), ctx, max_points=30)
    assert len(count) == 2
    assert '删除' in count[-1][-1]['content'] or '范围' in count[-1][-1]['content']


def test_plan58_number_selection_records_only_actually_removed_modules(flow):
    f, _, pid, _ = flow
    data = evidence()
    d = f.load(pid)
    d['material_structure'] = copy.deepcopy(data['selected'])
    d['phase'] = 'material_preview'
    f._select_material_modules(d, dict(include=[1, 3], exclude=[], ignored=[]))
    assert any(r['title'] == '硬件与软件' for r in d['removed_content'])
    assert not any(r['title'] == '认识计算机' for r in d['removed_content'])
    assert len(d['material_structure']['modules']) == 2


def test_plan58_renumbered_module_keys_do_not_mark_retained_devices_as_deleted():
    data = evidence()
    value = copy.deepcopy(data['selected'])
    value['modules'] = [value['modules'][0], dict(value['modules'][2], key='m2')]
    draft = {}
    PlanFlow._remember_removed_content(draft, data['previous'], value, sections(data))
    deleted_modules = [row['title'] for row in draft['removed_content'] if row['kind'] == 'module']
    assert deleted_modules == ['硬件与软件']


def test_plan58_small57_full_map_replay_keeps_modules_and_directory_scope(flow, monkeypatch):
    f, ctx, pid, model = flow
    data = evidence()
    monkeypatch.setattr(f, '_sections', lambda d: (data['sources'], sections(data)))
    monkeypatch.setattr(f, '_adopted', lambda d: (data['sources'], {data['sources'][0]['id']: data['chunks']}))
    from tests.refactor.wire121.model121 import directory_reply
    monkeypatch.setattr(model, 'complete', lambda purpose,messages,**k: directory_reply(purpose,messages,k.get('system')))
    d = f.load(pid)
    d.update(phase='material_preview', material_structure=copy.deepcopy(data['selected']),
             removed_content=removed(data))
    f.make_outline(d, ctx)
    modules = agents.outline_modules(d['outline'])
    assert [m['title'] for m in modules] == [m['title'] for m in data['selected']['modules']]
    assert all(p['structure']=='concept_v1' and p['subpoints'] for m in modules for p in m['points'])
    assert all(s['notes']==[] for m in modules for p in m['points'] for s in p['subpoints'])
    assert not any('ATM' in p['summary'] or '计算器' in p['summary'] for m in modules for p in m['points'])


def test_plan58_other_source_and_retained_child_evidence_not_removed():
    rows = [dict(id='intro', source_id='a', section=['设备']),
            dict(id='deleted', source_id='a', section=['设备', '其他']),
            dict(id='keep', source_id='a', section=['设备', '台式机']),
            dict(id='unrelated', source_id='b', section=['设备', '其他'])]
    def p(title, ids):
        return dict(title=title, citations=[dict(chunk_id=i, quote=i) for i in ids], chunk_ids=ids)
    deletion = [dict(kind='chapter', source_id='a', title='设备 / 其他')]
    result = agents.filter_removed_content([p('已删依据', ['deleted']), p('概述依据', ['intro']),
        p('保留设备', ['intro', 'keep']), p('另一份材料', ['unrelated'])], rows, deletion)
    assert [r['title'] for r in result] == ['保留设备', '另一份材料']
    assert result[0]['chunk_ids'] == ['keep']
    assert result[1]['chunk_ids'] == ['unrelated']


def test_plan58_scope_survives_added_material_structure_invalidation(flow, monkeypatch):
    f, ctx, pid, model = flow
    data = evidence()
    d = f.load(pid)
    d.update(phase='material_preview', material_structure=dict(data['selected'], removed_content=removed(data)))
    f._invalidate_material_structure(d)
    assert d['removed_content'] == removed(data)
    requests = []
    def reply(purpose, messages, **kwargs):
        requests.append(json.loads(messages[0]['content']))
        return SimpleNamespace(text=json.dumps(data['selected']))
    monkeypatch.setattr(model, 'complete', reply)
    monkeypatch.setattr(f, '_sections', lambda d: (data['sources'], sections(data)))
    f.material_structure(d, ctx)
    assert requests[0]['removed_content'] == removed(data)
    assert f.load(pid)['material_structure']['removed_content'] == removed(data)


def report():
    return json.loads(Path(__file__).with_name('small57_report.json').read_text(encoding='utf-8'))


def test_plan58_small57_markdown_no_draft_failure_claim():
    data = report()
    text = report_markdown(data, {})
    assert '讲稿' not in text and '没通过核对' not in text
    assert '老师现场讲的知识点' in text
    assert all(p['title'] in text for p in data['live_points'])


def test_plan58_small57_pdf_no_draft_failure_claim(tmp_path):
    # Real Windows Qt fonts in an independent process, not a global offscreen app.
    script = tmp_path / 'render58.py'
    script.write_text('''import json, sys
from pathlib import Path
from PySide6.QtGui import QGuiApplication
from pypdf import PdfReader
from refactor.learning.course.report_pdf import render_report_pdf
from refactor.learning.course.report_views import report_views
app=QGuiApplication([])
d=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
render_report_pdf(d,{},report_views(d),Path(sys.argv[2]))
text="".join(p.extract_text() for p in PdfReader(sys.argv[2]).pages)
assert "讲稿" not in text and "没通过核对" not in text
assert "老师现场讲的知识点" in "".join(text.split())
''', encoding='utf-8')
    env = dict(os.environ, QT_QPA_PLATFORM='windows', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[3])
    proc = subprocess.run([sys.executable, '-B', str(script), str(Path(__file__).with_name('small57_report.json')),
                           str(tmp_path / 'report.pdf')], env=env, capture_output=True, text=True,
                          encoding='utf-8', timeout=45)
    assert proc.returncode == 0, proc.stderr
