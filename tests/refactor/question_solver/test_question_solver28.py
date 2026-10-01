from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.course import question_solver as qs, question_review as qr
from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.gateway import PURPOSE_EFFORT, PURPOSE_MIN_OUTPUT, BACKGROUND_PURPOSES

Q=dict(id='q',type='choice',stem='列表 append 在哪里添加元素？',options=['末尾','开头','中间','不添加'],
       answer='末尾',reference='不许泄露参考',required_points=['不许泄露要点'],
       point_ids=['p'],chunk_ids=['c'],angle='recall')
POINTS=[dict(id='p',title='列表追加',summary='不许泄露摘要',chunk_ids=['c'])]
EXCERPTS={'c':'append adds an element to the end of a list.'}


def test_blind_request_only_public_fields_even_when_hidden_data_changes():
    _,messages=qs.solver_request(Q,EXCERPTS)
    public=json.loads(messages[-1]['content'].split('\n',1)[1])
    assert public=={k:Q[k] for k in ('type','stem','options')}
    changed=dict(Q,answer='开头',reference='其他参考',required_points=[dict(point='秘密',accept=['秘密'],reject=[])])
    assert qs.solver_request(changed,EXCERPTS)==qs.solver_request(Q,EXCERPTS)
    assert '不许泄露' not in json.dumps(messages,ensure_ascii=False)
    assert PURPOSE_EFFORT['question_solve']=='high'
    assert PURPOSE_MIN_OUTPUT['question_solve']==16384 and 'question_solve' in BACKGROUND_PURPOSES


@pytest.mark.parametrize('answerable,answer,options,expected',[
    (True,'末尾',[0],True),(True,'开头',[1],False),(True,'末尾',[0,1],False),(False,'',[],False)])
def test_choice_match_requires_unique_independent_answer(answerable,answer,options,expected):
    sol=qs.parse_solution(json.dumps(dict(answerable=answerable,answer=answer,viable_options=options,reason='资料依据')),Q)
    assert qs.compare_solution(Q,sol) is expected


@pytest.mark.parametrize('options',[[True],[-1],[4],[0,0]])
def test_solver_option_protocol_rejects_invalid_indices(options):
    with pytest.raises(AgentFormatError):
        qs.parse_solution(json.dumps(dict(answerable=True,answer='末尾',viable_options=options,reason='依据')),Q)


def test_exact_equivalence_and_unknown_not_assumed_pass():
    q=dict(Q,type='exact',options=[],stem='计算给定表达式的值。',answer='2',answer_kind='number',accepted=['2'],tolerance=0)
    assert qs.compare_solution(q,qs.Solution(True,'2.0',(),'数值')) is True
    assert qs.compare_solution(q,qs.Solution(True,'3',(),'数值')) is False
    assert qs.compare_solution(dict(q,answer_kind='term'),qs.Solution(True,'同义名称',(),'含义')) is None


def test_short_judge_failure_resumes_without_resolving():
    q=dict(Q,type='short',stem='请说明 append 如何修改原列表。',options=[],answer='在原列表修改',
           required_points=[dict(point='修改原列表',accept=['原地追加'],reject=[])])
    state={};calls=[];saves=[]
    def complete(purpose,messages,**kwargs):
        calls.append(purpose)
        if purpose=='question_solve':
            return SimpleNamespace(text=json.dumps(dict(answerable=True,answer='原地追加',viable_options=[],reason='原文说明')))
        if calls.count('judge')==1:raise ModelError('MODEL_TIMEOUT','超时')
        return SimpleNamespace(text=json.dumps(dict(kind='answer',correct=True,wrong_points=[],reason='已答到原地修改。',
            uncertain=False,checks=[dict(point_id='p',point='修改原列表',applies=True,status='hit',evidence='原地追加')])))
    gateway=SimpleNamespace(complete=complete)
    with pytest.raises(ModelError):qs.verify(gateway,q,points=POINTS,excerpts=EXCERPTS,state=state,save=lambda:saves.append(deepcopy(state)))
    assert qs.verify(gateway,q,points=POINTS,excerpts=EXCERPTS,state=state,save=lambda:None)
    assert calls==['question_solve','judge','judge'] and saves[-1]['solution']['answer']=='原地追加'


def test_essay_has_no_solver_and_written_ratio_excludes_exact_and_composites():
    with pytest.raises(ValueError):qs.solver_request(dict(Q,type='essay'),EXCERPTS)
    rows=[dict(Q,type='exact'),dict(Q,point_ids=['p','p2'],type='short')]
    slots,left=qr.written_allocation(rows,{'p':1,'p2':2,'p3':2})
    assert sum(slots.values())==2 and left==0  # ceil(0.3*(1+5))
    assert qr.written_allocation([dict(Q)]*8,{})[1]==3


CASES=json.loads((Path(__file__).parent/'data/saved_questions25_27.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['question']['id'])
def test_saved_questions_with_ideal_blind_fixture_not_a_real_quality_claim(case):
    """Replay original model drafts; fake independent results test the gate only."""
    from tests.refactor.question_solver.fakes import grade
    q=case['question']
    issues=qr.check_question(q,points=case['points'],excerpts=case['excerpts'])
    if issues:
        assert not case['expected_good']
        return
    def complete(purpose,messages,**kwargs):
        if purpose=='judge':return grade(messages)
        assert purpose=='question_solve'
        assert json.loads(messages[-1]['content'].split('\n',1)[1])=={k:q[k] for k in ('type','stem','options')}
        good=case['expected_good']
        return SimpleNamespace(text=json.dumps(dict(answerable=good,answer=q['answer'] if good else '',
            viable_options=[q['options'].index(q['answer'])] if good and q['type']=='choice' else [],
            reason='理想替身：已核对的原文证据' if good else '理想替身：资料不支持题面所问关系'),ensure_ascii=False))
    matched=qs.verify(SimpleNamespace(complete=complete),q,points=case['points'],excerpts=case['excerpts'],
                      state={},save=lambda:None)
    # Even an answer-visible reviewer saying approve cannot overrule a blind failure.
    verdict=qr.parse_review('{"verdict":"approve","issues":[],"revised":null}',q)
    assert (matched and verdict.verdict=='approve') is case['expected_good']
