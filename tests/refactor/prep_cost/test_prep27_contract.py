from copy import deepcopy
from dataclasses import asdict
import json
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest

from refactor.learning.app.settings import DEFAULTS, validate
from refactor.learning.course import lesson_prep as lp
from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.connections.errors import ModelError, ModelCancelled
from tests.refactor.guided_lessons.fixtures import wire
from tests.refactor.guided_lessons.test_guided_classroom_contract import env, PLAN
from tests.refactor.prep_cost import test_prep27_real_cost as probe
from tests.refactor.prep_cost.prep27_cases import exam


def test_prep27_brief_default_preserves_explicit_preference_and_six_checks():
    assert DEFAULTS['detail']=='brief'
    assert validate({})['detail']=='brief'
    assert validate({'detail':'normal'})['detail']=='normal'
    assert '不省学习内容' in lp.PREP_SYSTEM
    for phrase in ('事实、数值','合理读法','独立/连续执行','真正标为引申','每个引导选项','两道随口问'):
        assert phrase in lp.REVIEW_SYSTEM
    context=dict(module=dict(id='m',title='队列'),point=dict(id='p',title='顺序',summary='先入先出'),excerpts={'c':'先入先出'})
    assert '本次只有一个知识点' in lp.prep_request(context)[0]
    assert '不能复制输入的 point/draft 外壳' in lp.REVIEW_SYSTEM
    assert '每道choice仍是4个互异字符串选项' in lp.REVIEW_SYSTEM


def test_prep27_local_validation_does_not_approve_semantics():
    data=wire();good=lp.parse_prep(json.dumps(data))
    assert lp.check_lesson(good)==()
    data['steps'][1]['text']=data['steps'][1]['text'].replace('[引申1]','')
    assert '引申清单未在正文标注' in lp.check_lesson(lp.parse_prep(json.dumps(data)))
    data=wire();data['steps'][1]['text']=data['steps'][1]['text'].replace('[引申1]','[引申2]')
    assert '正文引申编号与清单不一致' in lp.check_lesson(lp.parse_prep(json.dumps(data)))
    data=wire(code='a=[1]\na.pop(5)')
    assert 'IndexError' in lp.check_lesson(lp.parse_prep(json.dumps(data)))[0]
    # Labels don't prove factual accuracy or semantic annotation placement.
    data=wire();data['steps'][0]['text']='队列采用后入先出。'
    assert lp.check_lesson(lp.parse_prep(json.dumps(data)))==()


def test_prep27_batch_bad_sibling_does_not_discard_good_point(env):
    e=env;co=e.room._course(PLAN);points=co.points[:2];keep=[p['id'] for p in co.points]
    e.gateway.reviews=[AgentFormatError('bad sibling'),'approve']
    e.room._prepare_batch(co,points,keep)
    rows=e.store.load_lesson_cache(PLAN)['lesson_preps']
    assert rows[points[0]['id']]['status']=='review_pending'
    assert rows[points[1]['id']]['status']=='reviewed'
    assert e.gateway.calls==['lesson_prep','lesson_review']
    e.room._prepare_batch(co,points,keep)
    rows=e.store.load_lesson_cache(PLAN)['lesson_preps']
    assert all(rows[p['id']]['status']=='reviewed' for p in points)
    assert e.gateway.calls==['lesson_prep','lesson_review','lesson_review']
    assert rows[points[1]['id']]['total_reviews']==1


@pytest.mark.parametrize('failure',[ModelError('MODEL_TIMEOUT','synthetic'),ModelCancelled()])
def test_prep27_batch_transport_or_cancel_preserves_drafts_and_counters(env,failure):
    e=env;co=e.room._course(PLAN);points=co.points[:2];keep=[p['id'] for p in co.points]
    e.gateway.reviews=[failure]
    if isinstance(failure,ModelCancelled):
        with pytest.raises(ModelCancelled):e.room._prepare_batch(co,points,keep)
    else:e.room._prepare_batch(co,points,keep)
    rows=e.store.load_lesson_cache(PLAN)['lesson_preps']
    assert all(rows[p['id']]['lesson'] and rows[p['id']]['review_attempts']==1 for p in points)
    assert e.gateway.calls==['lesson_prep','lesson_review']
    e.room._prepare_batch(co,points,keep)
    assert all(e.store.load_lesson_cache(PLAN)['lesson_preps'][p['id']]['status']=='reviewed' for p in points)
    assert e.gateway.calls==['lesson_prep','lesson_review','lesson_review']


def test_prep27_local_runtime_failure_never_spends_review(env):
    e=env;co=e.room._course(PLAN);point=co.points[0]
    calls=[]
    def ask(course,purpose,request,parse,max_tokens):
        calls.append(purpose)
        assert purpose=='lesson_prep'
        return lp.parse_prep(json.dumps(wire('k1',code='a=[1]\na.pop(5)')))
    e.room._prep_ask=ask
    e.room._prepare_point(co,point,[p['id'] for p in co.points])
    row=e.store.load_lesson_cache(PLAN)['lesson_preps'][point['id']]
    assert calls==['lesson_prep','lesson_prep']
    assert row['total_reviews']==0 and row['status']=='needs_live_teaching'


def test_prep27_batch_revision_is_not_implicitly_approved(env):
    e=env;co=e.room._course(PLAN);points=co.points[:2]
    e.gateway.reviews=['revise','approve','approve']
    e.room._prepare_batch(co,points,[p['id'] for p in co.points])
    rows=e.store.load_lesson_cache(PLAN)['lesson_preps']
    assert all(rows[p['id']]['status']=='reviewed' for p in points)
    assert [rows[p['id']]['total_reviews'] for p in points]==[2,1]
    assert e.gateway.calls==['lesson_prep','lesson_review','lesson_review']


def test_prep27_blinded_exam_covers_18_independent_semantic_cases():
    samples=[dict(module=dict(id='m',title='权威固定资料'),point=dict(id=f'p{i}',title='点',summary='定义',chunk_ids=['c']),
                  excerpts={'c':'资料'}) for i in range(6)]
    cases=exam(samples)
    assert Counter(c['category'] for c in cases)==dict(fact=2,number=2,ambiguity=2,premise=2,extension=2,response=2,clean=6)
    assert len({c['id'] for c in cases})==18
    assert all(lp.check_lesson(lp.parse_prep(json.dumps(c['draft']),chunk_ids=['c']))==() for c in cases)


def test_prep27_human_audit_counterexample_and_positive_condition_regression():
    # Recorded stage-1 approved manuscript says addition is never legal;
    # actual arithmetic disproves that universal claim without a model judge.
    assert Fraction(1,1)==Fraction(2,2)
    assert Fraction(2,3)!=Fraction(3,4)
    for system in (lp.PREP_SYSTEM,lp.REVIEW_SYSTEM):
        assert '充分条件' in system and '是否为0' in system
    assert '沿用至16世纪' in lp.PREP_SYSTEM


def test_prep27_budget_counts_unknown_usage_at_full_reserve(tmp_path,monkeypatch):
    prices=SimpleNamespace(estimate_cost=lambda *a,**k:SimpleNamespace(currency='CNY',high=Decimal('.20')))
    recorded=SimpleNamespace(inner=SimpleNamespace(price_catalog=prices,estimate_context=lambda:{}),
        config=dict(provider_id='deepseek',model='deepseek-flash'),rows=[dict(case='prep27-phase1',reserved='.55',peak_cost=None)])
    called=[];recorded.complete=lambda *a,**k:called.append(k)
    monkeypatch.setattr(probe,'OUT',tmp_path)
    bounded=probe.Gateway(recorded,1)
    with pytest.raises(probe.BudgetStop):bounded.complete('lesson_review',[],max_tokens=16384,system='fixed')
    assert called==[] and not (tmp_path/'report.md').exists()
    recorded.rows=[]
    bounded.complete('lesson_prep',[],max_tokens=16384,system='fixed')
    assert called[0]['max_tokens']==24576
    assert '付费前' in (tmp_path/'report.md').read_text(encoding='utf-8')
