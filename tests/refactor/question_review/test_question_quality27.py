"""Merged question contract and independent-review preflight must agree."""
from copy import deepcopy
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from tests.refactor.question_solver.fakes import response as solver_response, grade as solver_grade

from refactor.learning.course import question_agent as qa, question_review as qr
from refactor.learning.app.classroom import Classroom
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore


CTX = dict(purpose='bank', per_point=2, counts={'p': 1}, used_angles={'p': []},
           points=[dict(id='p', title='列表追加', summary='在原列表追加', chunk_ids=['c'])],
           excerpts={'c': 'append(x) adds x to the end of the original list.'})


def short():
    return dict(id='q', type='short', stem='请说明 append 对原列表的修改方式。', options=[],
                answer='在原列表末尾追加元素', reference='原文说明在原列表追加。',
                point_ids=['p'], chunk_ids=['c'], angle='recall',
                required_points=[dict(point='append 修改原列表', accept=['在原列表追加'], reject=[])])


def check(q):
    return qr.check_question(q, points=CTX['points'], excerpts=CTX['excerpts'])


def test_merged_parser_and_review_allow_empty_misconceptions():
    q = qa.parse_questions(json.dumps({'questions': [short()]}, ensure_ascii=False), CTX)[0]
    assert q.required_points[0]['reject'] == []
    assert not check(q)
    system, _ = qr.review_request(q, points=CTX['points'], excerpts=CTX['excerpts'])
    assert 'reject 有0–3个' in system


@pytest.mark.parametrize('field,size', [('accept', 0), ('accept', 4), ('reject', 4)])
def test_review_revised_requirement_example_bounds(field, size):
    q = short()
    q['required_points'][0][field] = ['示例'] * size
    assert 'invalid_required_point' in check(q)


def essay():
    return dict(short(), type='essay', stem='请分析列表追加的设计，结合原文提供观点与论据。',
                answer='基于列表操作的行为分析设计', rubric=[
                    dict(criterion=name, met='结论有原文依据', not_met='没有达到该项', required=required)
                    for name, required in [('核心观点', True), ('没有事实错误', True), ('资料论据', False)]])


def test_review_essay_revision_requires_exact_core_optional_and_distinct_criteria():
    q = essay()
    assert not check(q)
    bad = deepcopy(q); bad['rubric'][2]['required'] = True
    assert 'rubric_missing_optional' in check(bad)
    bad = deepcopy(q); bad['rubric'][0]['criterion'] = '部分核心观点'
    assert 'rubric_missing_core' in check(bad)
    bad = deepcopy(q); bad['rubric'].append(deepcopy(bad['rubric'][2]))
    assert 'duplicate_rubric' in check(bad)


def test_bank_duplicate_angle_skips_review_and_reissues_only_missing_quota(tmp_path):
    day = date(2026, 10, 1)
    plan = '27' * 16
    with CourseStore('quality27', tmp_path) as store:
        store.create_plan(plan, goal='列表追加', window=PlanWindow(day, day+timedelta(days=3), 100))
        store.add_sources(plan, [dict(id='s', url='https://docs.python.org/3/tutorial/datastructures.html',
                                       chunks=[dict(chunk_id='c', text=CTX['excerpts']['c'])])])
        store.save_outline(plan, [dict(id='st', title='列表')], [dict(id='m', stage_id='st', title='追加')])
        store.save_generated_module(plan, 'm', [dict(CTX['points'][0], minutes=10)],
                                    check_minutes=8, final_check_minutes=6, check_question_minutes=2, today=day)
        store.start_plan(plan, today=day)
        reviews, generations = [], []
        def complete(purpose, messages, **kwargs):
            if purpose == 'questions':
                task = json.loads(messages[-1]['content'].split('【本次出题要求】\n')[1])
                generations.append(task['counts'])
            extra = solver_response(purpose, messages)
            if extra is not None: return extra
            if purpose == 'judge': return solver_grade(messages)
            assert purpose == 'question_review'
            reviews.append(json.loads(messages[-1]['content'].split('\n', 1)[1])['question'])
            return SimpleNamespace(text='{"verdict":"approve","issues":[],"revised":null}')
        room = Classroom(SimpleNamespace(store=store, account_id='quality27', data_root=tmp_path,
                                         gateway=SimpleNamespace(complete=complete)), lambda: day)
        def questions(co, ctx, purpose, ids, **kwargs):
            generations.append(kwargs['counts'])
            if len(generations) == 1:
                base = dict(short(), type='choice', options=['追加到末尾', '删除首项', '替换首项', '创建新列表'],
                            answer='追加到末尾', required_points=None)
                return [dict(base, id='first', stem='append 会把元素放到哪个位置？'),
                        dict(base, id='duplicate-angle', stem='append 添加元素的位置是什么？')]
            return [dict(short(), id='written', angle='apply')]
        room._questions = questions
        room._bank_fill(room._course(plan), None, 'm')
        bank = store.records('question_bank', plan)
        assert len(bank) == 2 and {q['type'] for q in bank} == {'choice', 'short'}
        assert {q['angle'] for q in bank} == {'recall', 'apply'}
        assert len(reviews) == 2 and all(q['id'] != 'duplicate-angle' for q in reviews)
        assert generations == [{'p': 2}, {'p': 1}]
        room._bank_fill(room._course(plan), None, 'm')
        assert len(reviews) == 2 and len(generations) == 2
