"""Small55's exact structure request; style is not a missing source chapter."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.course import planner_agents as agents
from refactor.learning.course import teacher_agent
from tests.refactor.plan51.test_plan51_selected_material_flow import flow, URL


def original():
    data = json.loads(Path(__file__).with_name('small55_structure_request.json').read_text(encoding='utf-8'))
    payload = json.loads(data['messages'][0]['content'])
    sections = [SimpleNamespace(id=s['id'], source_title=s['source'], title=s['title']) for s in payload['directory']]
    return data, payload, sections


def request(payload, sections):
    return agents.material_structure_request(payload['goal'], payload['form'], payload['rough_structure'],
        sections, previous=payload['previous'], instruction=payload['instruction'])


def fixed_model(payload, sections):
    """Deterministic reproduction of the captured model's coverage mistake."""
    system, messages = request(payload, sections)
    sent = json.loads(messages[0]['content'])
    content = json.dumps({k: v for k, v in sent.items() if k != 'teaching_preferences'}, ensure_ascii=False)
    response = json.loads(original()[0]['reply'])
    response['missing'] = []
    if '生活化' in content:
        response['missing'].append('用生活化例子区分这些概念（目录未体现相关章节）')
    if '数据库' in content:
        response['missing'].append('数据库')
    return agents.parse_material_structure(json.dumps(response, ensure_ascii=False), sections), sent, system


def test_plan56_small55_style_not_missing_and_original_inputs_unchanged():
    data, payload, sections = original()
    assert agents.parse_material_structure(data['reply'], sections)['missing']  # captured before
    before = copy.deepcopy(payload)
    value, sent, system = fixed_model(payload, sections)
    assert value['missing'] == []
    assert any('生活化' in s for s in sent['teaching_preferences'])
    assert '不是学习内容' in system and '不参与' in system
    assert payload == before  # raw goal/form still go to the teacher


def test_plan56_real_missing_database_remains_missing():
    _, payload, sections = original()
    payload['goal'] += '另外要学数据库。'
    value, _, _ = fixed_model(payload, sections)
    assert value['missing'] == ['数据库']


@pytest.mark.parametrize('style', ['少用术语', '不上手操作', '语气温和', '讲得浅一点'])
def test_plan56_rules_preserve_named_subject_next_to_style(style):
    _, payload, sections = original()
    payload['goal'] = '学习数据库，' + style
    payload['form'] = {'goal': payload['goal'], 'final_goal': '能区分关系型数据库和文档数据库。'}
    payload['rough_structure'] = {'summary': payload['goal'], 'modules': [{'title': '数据库', 'summary': style}]}
    payload['instruction'] = style
    _, sent, _ = fixed_model(payload, sections)
    assert style in sent['teaching_preferences']
    assert '数据库' in sent['goal'] and style not in sent['goal']
    assert '关系型数据库' in sent['form']['final_goal']


def test_plan56_notes_preference_reaches_existing_teacher_goal_field(flow):
    f, c, pid, _ = flow
    f.run(pid, lambda d,c:f.chat(d,c,URL), c)
    def outline(d,c):
        d['form']['notes'] += '\n少用术语'
        return f.material_structure(d,c)
    f.run(pid, outline, c)
    plan = f.services.store.get_plan(pid)
    assert '少用术语' in plan['final_goal']
    context = dict(plan=dict(goal=plan['goal'], final_goal=plan['final_goal'], level='零基础'),
        module=dict(title='模块', points=[dict(id='p',title='点',summary='计算机处理数据。')]), prefs={}, dialogue=[],
        excerpts={'c':'计算机处理数据。'}, current_points=[dict(id='p',title='点')], director='讲解。')
    _, messages = teacher_agent.teacher_request(context)
    assert '少用术语' in messages[0]['content']
    before = plan['final_goal']
    f.run(pid, outline, c)
    assert f.services.store.get_plan(pid)['final_goal'] == before
