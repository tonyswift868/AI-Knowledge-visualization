"""Fixed real inputs + human-labelled substitutes; no claims about real model accuracy."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.connections.errors import ModelError, ModelCancelled
from refactor.learning.connections.gateway import PURPOSE_EFFORT, PURPOSE_MIN_OUTPUT, BACKGROUND_PURPOSES
from refactor.learning.course import planner_agents as agents
from tests.refactor.planner.semantic_fakes34 import wire
from tests.refactor.planner.test_planner_agents import FORM
from tests.refactor.planner.test_material_coverage32 import curriculum


def fixture():
    return json.loads((Path(__file__).parent/'fixtures/semantic34.json').read_text(encoding='utf-8-sig'))


def groups(case):
    modules=sorted(case['modules'],key=lambda m:m['position'])
    return [[dict(p,module_title=m['title']) for p in sorted(case['points'],key=lambda p:p['position'])
             if p['module_id']==m['id']] for m in modules]


@pytest.mark.parametrize('name,expected_pair', [('batch32',('n3','p3')), ('acceptance3c',('n2','p2'))])
def test_saved_outputs_replay_with_independent_human_labels(name,expected_pair):
    data=fixture(); prior,current=groups(data[name])
    assert not agents.cross_module_duplicates(current,prior)  # both old real misses
    calls=[]
    flow=PlanFlow(None,lambda *_:None,date.today)
    def ask(purpose,request,parse,*args,**kwargs):
        assert purpose=='plan_point_review' and kwargs['max_tokens']==16384
        calls.append(request)
        return parse(wire(request[1],duplicates=[expected_pair])),None
    flow._ask=ask
    matches=flow._meaning_duplicates(dict(plan_id='p'),current,prior,data['chunks'],None)
    assert len(calls)==1
    assert [(p['index'],p['previous_title']) for p in matches]==[(int(expected_pair[0][1:])-1,
                                                               prior[int(expected_pair[1][1:])-1]['title'])]
    assert matches[0]['method']=='semantic'


def test_old_three_independent_application_points_are_not_filtered_by_wiring():
    _,old=curriculum(); data=fixture()
    flow=PlanFlow(None,lambda *_:None,date.today)
    flow._ask=lambda purpose,request,parse,*args,**kw:(parse(wire(request[1])),None)
    assert not flow._meaning_duplicates(dict(plan_id='p'),old[2],old[0],data['chunks'],None)


def request():
    data=fixture();prior,current=groups(data['acceptance3c'])
    return agents.point_review_request(current,prior,data['chunks'])


def test_original_evidence_is_full_and_interned_once_not_a_title_similarity_score():
    system,messages,contract=request(); data=json.loads(messages[0]['content'])
    assert '已列好的同类方程' in system and '不替生成员补写' in system
    assert len(data['previous'])==3 and len(data['current'])==3
    assert len(data['evidence'])==len({c for p in [*groups(fixture()['acceptance3c'])[0],
                                                 *groups(fixture()['acceptance3c'])[1]] for c in p['chunk_ids']})
    assert all(e['text'] for e in data['evidence'])
    assert len(agents.parse_point_review(wire(messages),contract))==9


@pytest.mark.parametrize('fault',['missing','duplicate','unknown','relation','foreign_evidence','empty_reason'])
def test_incomplete_or_untraceable_review_cannot_pass(fault):
    _,messages,contract=request(); obj=json.loads(wire(messages))
    if fault=='missing':obj['comparisons'].pop()
    elif fault=='duplicate':obj['comparisons'][-1]=obj['comparisons'][0]
    elif fault=='unknown':obj['comparisons'][0]['point']='n999'
    elif fault=='relation':obj['comparisons'][0]['relation']='approve'
    elif fault=='foreign_evidence':obj['comparisons'][0]['point_evidence']=['not-cited']
    else:obj['comparisons'][0]['reason']=''
    with pytest.raises(agents.AgentFormatError):agents.parse_point_review(json.dumps(obj),contract)


def test_missing_cited_original_stops_before_model_call():
    data=fixture();prior,current=groups(data['acceptance3c'])
    with pytest.raises(agents.AgentFormatError,match='缺少所引用'):
        agents.point_review_request(current,prior,[])


@pytest.mark.parametrize('still_duplicate',[False,True])
def test_one_regeneration_and_independent_second_review(still_duplicate):
    data=fixture();prior,current=groups(data['acceptance3c'])
    chunks=data['chunks'];flow=PlanFlow(None,lambda *_:None,date.today)
    draft=dict(plan_id='p',form=dict(FORM,notes=''),goal='方程',outline=dict(stages=[dict(title='方程',modules=[])]))
    module=dict(title='应用',summary='应用',minutes=[45,60])
    calls=[];reviews=[]
    def ask(purpose,req,parse,*args,**kwargs):
        calls.append(purpose)
        if purpose=='plan_point_review':
            reviews.append(req)
            dupe=[('n2','p2')] if len(reviews)==1 or still_duplicate else []
            return parse(wire(req[1],duplicates=dupe)),None
        assert purpose=='generate'
        assert '独立' in req[1][-1]['content'] or '重复' in req[1][-1]['content']
        reverse={c['id']:f'c{i}' for i,c in enumerate(chunks,1)}
        changed=deepcopy(current)
        if not still_duplicate:
            changed[1]['title']='从价格相差情境独立建模'
            changed[1]['summary']='从未列式的价格问题确定未知量、选择变量并独立列方程解释关系。'
        payload=[dict(title=p['title'],summary=p['summary'],minutes=p['minutes'],
                      chunks=[reverse[c] for c in p['chunk_ids']]) for p in changed]
        return parse(json.dumps(dict(points=payload))),None
    flow._ask=ask
    if still_duplicate:
        with pytest.raises(agents.MaterialCoverageError,match='调整一次仍有跨模块重复'):
            flow._check_module_points(draft,module,chunks,None,current,prior,evidence=chunks)
    else:
        result=flow._check_module_points(draft,module,chunks,None,current,prior,evidence=chunks)
        assert result[1]['title']=='从价格相差情境独立建模'
    assert calls==['plan_point_review','generate','plan_point_review']


def test_failed_review_preserves_candidate_and_never_bypasses_into_generation():
    data=fixture();prior,current=groups(data['acceptance3c']);snapshot=deepcopy(current)
    flow=PlanFlow(None,lambda *_:None,date.today);calls=[]
    def ask(purpose,*args,**kwargs):
        calls.append(purpose);raise ModelError('MODEL_TIMEOUT','核对超时')
    flow._ask=ask
    with pytest.raises(ModelError):
        flow._check_module_points(dict(plan_id='p',form=FORM),dict(title='应用'),data['chunks'],None,current,prior)
    assert current==snapshot and calls==['plan_point_review']


def test_large_matrix_keeps_every_pair_and_cancel_stops_before_next_call():
    previous=[dict(title=f'旧技能{i}',summary=f'已学方法{i}',chunk_ids=[f'c{i}']) for i in range(65)]
    current=[dict(title='新迁移能力',summary='独立建模与验收',chunk_ids=['new'])]
    chunks=[dict(id=f'c{i}',text=f'完整原文{i}') for i in range(65)]+[dict(id='new',text='新任务的完整原文')]
    ctx=SimpleNamespace(cancel=threading.Event());flow=PlanFlow(None,lambda *_:None,date.today);sizes=[]
    def ask(purpose,req,parse,*args,**kwargs):
        sizes.append(len(json.loads(req[1][0]['content'])['previous']))
        return parse(wire(req[1])),None
    flow._ask=ask
    assert not flow._meaning_duplicates(dict(plan_id='p'),current,previous,chunks,ctx)
    assert sizes==[64,1]
    sizes.clear()
    def cancel_after_first(*args,**kwargs):
        result=ask(*args,**kwargs);ctx.cancel.set();return result
    flow._ask=cancel_after_first
    with pytest.raises(ModelCancelled):flow._meaning_duplicates(dict(plan_id='p'),current,previous,chunks,ctx)
    assert sizes==[64]


def test_new_purpose_uses_high_effort_and_background_review_allowance():
    assert PURPOSE_EFFORT['plan_point_review']=='high'
    assert PURPOSE_MIN_OUTPUT['plan_point_review']==16384
    assert 'plan_point_review' in BACKGROUND_PURPOSES
