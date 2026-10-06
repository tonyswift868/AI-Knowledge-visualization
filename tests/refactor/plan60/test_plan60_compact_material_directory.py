"""Large directory is a projection: canonical scope and content survive."""
import json
from pathlib import Path

import pytest

from refactor.learning.course import planner_agents as agents
from tests.refactor.plan51.test_plan51_selected_material_flow import flow


def large_sections():
    chunks = [dict(id=f'c{i}', position=i, text=f'正文{i}', section=['教材', f'章{i//20}', f'节{i}'])
              for i in range(400)]
    return agents.build_sections([dict(id='src', title='教材')], {'src': chunks},
                                 max_per_source=400, max_chars=100000, max_depth=None)


def reply(ids):
    return json.dumps(dict(summary='结构', modules=[dict(key='m1', title='学习', summary='学内容', refs=ids)], missing=[]))


def test_plan60_fold_expand_preserves_all_chapters_and_chunks():
    sections = large_sections()
    directory = agents.build_sections((), {}, directory_sections=sections)
    assert len(directory) == 20
    assert [c for s in directory for c in s.chunk_ids] == [c for s in sections for c in s.chunk_ids]
    request = agents.material_structure_request('学习', {}, None, sections)
    sent = json.loads(request[1][0]['content'])['directory']
    assert len(sent) == 20
    assert '"chapters"' not in request[0]
    parsed = agents.parse_material_structure(reply([s.id for s in directory]), sections)
    assert parsed['modules'][0]['refs'] == [s.id for s in sections]
    assert parsed['modules'][0]['chapters'] == [s.title for s in sections]
    with pytest.raises(agents.AgentFormatError):
        agents.parse_material_structure(reply(['s1.1']), sections)


def test_plan60_removed_child_cannot_return_inside_folded_parent():
    sections = large_sections()
    removed = [dict(kind='chapter', title=sections[3].title, source_id='src')]
    directory = agents.build_sections((), {}, directory_sections=sections, removed_content=removed)
    parsed = agents.parse_material_structure(reply([s.id for s in directory]), sections, removed_content=removed)
    assert sections[3].id not in parsed['modules'][0]['refs']
    assert len(parsed['modules'][0]['refs']) == 399
    assert sections[4].id in parsed['modules'][0]['refs']
    # Same chapter name in another source is not deleted.
    second = agents.Section('s2.1', 'other', '另一份', sections[3].title, ('other',), 4, 0, 0)
    directory = agents.build_sections((), {}, directory_sections=sections+[second], removed_content=removed)
    assert 's2.1' in [r for s in directory for r in s.members or (s.id,)]


def test_plan60_scope_is_applied_before_folding(flow, monkeypatch):
    f, ctx, pid, model = flow
    chunks = [dict(id=f'c{i}', position=i, text='正文', section=['教材', f'章{i//20}', f'节{i}']) for i in range(400)]
    monkeypatch.setattr(f, '_adopted', lambda d: ([dict(id='src', title='教材')], {'src': chunks}))
    d = f.load(pid)
    d['scope'] = dict(include=[f's1.{i}' for i in range(1, 301)], exclude=['s1.3'])
    _, sections = f._sections(d)
    directory = agents.build_sections((), {}, directory_sections=sections)
    ids = [r for s in directory for r in s.members or (s.id,)]
    assert len(ids) == 299 and 's1.3' not in ids and 's1.301' not in ids


def test_plan60_small57_request_and_host_chapters_unchanged():
    data = json.loads((Path(__file__).parents[1]/'plan58/small57_scope.json').read_text(encoding='utf-8'))
    sections = agents.build_sections(data['sources'], {data['sources'][0]['id']: data['chunks']}, max_depth=None)
    request = agents.material_structure_request('认识计算机', {}, None, sections)
    assert [s['id'] for s in json.loads(request[1][0]['content'])['directory']] == [s.id for s in sections]
    result = agents.parse_material_structure(json.dumps(data['selected']), sections)
    assert result['modules'] == [{k: v for k, v in row.items() if k in ('key','title','summary','refs','chapters')}
                                  for row in data['selected']['modules']]


def test_plan60_previous_chapters_not_duplicated_in_model_request():
    sections = large_sections()
    previous = agents.parse_material_structure(reply([s.id for s in agents.build_sections((), {}, directory_sections=sections)]), sections)
    request = agents.material_structure_request('学习', {}, previous, sections, previous=previous)
    value = json.loads(request[1][0]['content'])
    assert 'chapters' not in value['previous']['modules'][0]
    assert len(value['previous']['modules'][0]['refs']) == 20
    assert 'chapters' not in value['rough_structure']['modules'][0]


def test_plan60_flat_directory_has_limit_without_dropping_content():
    sections = [agents.Section(f's1.{i}', 'src', '资料', f'独立标题{i}', (f'c{i}',), 4, i, i) for i in range(350)]
    directory = agents.build_sections((), {}, directory_sections=sections)
    assert len(directory) == 150
    assert [r for s in directory for r in s.members] == [s.id for s in sections]
