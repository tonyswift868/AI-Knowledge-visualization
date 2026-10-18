"""Prompt policy and canonical-ID retention; no pretend offline AI classification."""
import json

import pytest

from refactor.learning.course import planner_agents as agents


LEARNING = ('Java学习路线', '求职指导', '简历撰写', '面试准备', '项目表达', '面试心态与流程',
            '导航 / Java后端学习路线', 'Java常见面试问答')
UNRELATED = ('如何使用本资料', '目录说明', '关于作者', '推广与赞助', '资料常见问答 / 有没有 PDF',
             '资料常见问答 / 能不能转载', '资料常见问答 / 怎么贡献')


def sections(titles):
    return [agents.Section(f's1.{i}', 'book', 'JavaGuide', title, (f'c{i}',), 100, i, i)
            for i, title in enumerate(titles, 1)]


def test_structure81_prompt_narrows_automatic_exclusion_and_keeps_learning_without_questions():
    system, messages = agents.material_structure_request('Java后端求职与面试', {}, None,
                                                         sections(LEARNING + UNRELATED))
    exclude_rule = next(line for line in system.splitlines() if line.startswith('只排除和学习无关'))
    keep_rule = next(line for line in system.splitlines() if line.startswith('学习路线、求职、简历'))
    assert all(title in exclude_rule for title in ('如何使用本资料', '目录说明', '关于作者', '推广与赞助',
                                                  '有没有 PDF', '能不能转载', '怎么贡献'))
    assert all(title in keep_rule for title in ('学习路线', '求职', '简历', '面试准备', '项目表达', '面试心态与流程'))
    assert '没有题的模块只学不考' in keep_rule
    assert '不要把学习内容的问答误当作资料自身的常见问答' in exclude_rule
    assert not any(title in exclude_rule for title in ('学习路线', '求职', '简历', '面试准备', '项目表达'))
    packet = json.loads(messages[0]['content'])
    assert [row['title'] for row in packet['directory']] == list(LEARNING + UNRELATED)


@pytest.mark.parametrize('title', LEARNING)
def test_structure81_learning_titles_remain_modules_without_question_metadata(title):
    rows = sections((title, '关于作者', '资料常见问答 / 有没有 PDF'))
    # Explicit expected response exercises the product parser, not model capability.
    answer = dict(summary='保留学习内容', modules=[dict(key='m', title=title, summary='学习本节内容', refs=['s1.1'])],
                  excluded=[dict(name=s.title, reason='资料自身说明', refs=[s.id]) for s in rows[1:]], missing=[])
    parsed = agents.parse_material_structure(json.dumps(answer, ensure_ascii=False), rows)
    assert parsed['modules'][0]['chapters'] == [title]
    assert parsed['modules'][0]['refs'] == ['s1.1']
    assert all('s1.1' not in row['refs'] for row in parsed['excluded'])


@pytest.mark.parametrize('title', UNRELATED)
def test_structure81_unrelated_titles_can_be_excluded_without_losing_interview_preparation(title):
    rows = sections(('面试准备', title))
    answer = dict(summary='只排除资料自身说明', modules=[dict(key='m', title='面试准备', summary='准备面试', refs=['s1.1'])],
                  excluded=[dict(name=title, reason='与学习无关的资料自身说明', refs=['s1.2'])], missing=[])
    parsed = agents.parse_material_structure(json.dumps(answer, ensure_ascii=False), rows)
    assert parsed['excluded'][0]['name'] == title
    assert parsed['modules'][0]['chapters'] == ['面试准备']


def test_structure81_mixed_navigation_keeps_learning_children_and_explicit_user_deletions():
    rows = sections(('导航 / 学习路线', '导航 / 如何使用本资料', '求职 / 简历撰写'))
    system, messages = agents.material_structure_request('Java求职', {}, None, rows,
        removed_content=[dict(kind='module', title='用户明确不学的旧模块')])
    assert '不能用父编号代指其中一个无关小标题' in system
    assert 'refs留空，保留父编号用于知识模块' in system
    assert '不得因目录或概述中还有这些内容就重新加入' in system
    packet = json.loads(messages[0]['content'])
    assert packet['removed_content'] == [dict(kind='module', title='用户明确不学的旧模块')]
    assert any(row['title'] == '求职 / 简历撰写' for row in packet['directory'])
