from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from tests.refactor.prep_retest import test_prep26_live_pipeline as probe
from refactor.learning.course import lesson_prep


def gateway(tmp_path,monkeypatch,rows=()):
    monkeypatch.setattr(probe,'OUT',tmp_path)
    prices=SimpleNamespace(estimate_cost=lambda *a,**k:SimpleNamespace(currency='CNY',high=Decimal('.20')))
    recorded=SimpleNamespace(inner=SimpleNamespace(price_catalog=prices,estimate_context=lambda:{}),
        config=dict(provider_id='deepseek',model='deepseek-flash'),rows=list(rows),calls=[])
    def complete(purpose,messages,**kwargs):
        recorded.calls.append(dict(purpose=purpose,**kwargs));return 'ok'
    recorded.complete=complete
    return probe.BoundedGateway(recorded,1),recorded


def test_prep26_budget_reserves_actual_gateway_floor_and_records_before_call(tmp_path,monkeypatch):
    bounded,recorded=gateway(tmp_path,monkeypatch)
    assert bounded.complete('lesson_prep',[dict(role='user',content='任务\n'+json.dumps(dict(id='p')))],max_tokens=16384,system='system')=='ok'
    assert recorded.calls[0]['max_tokens']==24576
    assert '最大预留' in (tmp_path/'report.md').read_text(encoding='utf-8')


def test_prep26_unknown_usage_keeps_full_reserve_and_never_calls(tmp_path,monkeypatch):
    bounded,recorded=gateway(tmp_path,monkeypatch,[dict(case='prep26-phase1',reserved='.41',peak_cost=None)])
    with pytest.raises(probe.BudgetStop):bounded.complete('lesson_review',[dict(role='user',content='task\n{}')])
    assert recorded.calls==[]
    assert not (tmp_path/'report.md').exists()


def test_prep26_offline_regression_replays_append_alias_and_condition():
    # Independently executable semantics of the previous human-audit failure.
    fruits=['apple'];alias=fruits
    assert fruits.append(['grape']) is None
    assert fruits==['apple',['grape']]
    fruits=fruits.append('pear')
    assert fruits is None and alias==['apple',['grape'],'pear']
    assert '题面必须明确' in lesson_prep.PREP_SYSTEM
    assert '不能把合法操作本身说成不生效' in lesson_prep.PREP_SYSTEM
    assert '只有题面明确要求' in lesson_prep.REVIEW_SYSTEM


def test_prep26_choice_reference_and_extra_schema_regression():
    from tests.refactor.guided_lessons.fixtures import wire
    from refactor.learning.course.planner_agents import AgentFormatError
    data=wire()
    data['questions'][0]['required_points']=[]
    with pytest.raises(AgentFormatError,match='required_points'):
        lesson_prep.parse_prep(json.dumps(data))
    data['questions'][0]['required_points']=['先入先出。']
    data['extrapolations']=[dict(text='引申',boundary='限于顺序')]
    with pytest.raises(AgentFormatError,match='extrapolation'):
        lesson_prep.parse_prep(json.dumps(data))
    data['extrapolations']=['生活类比只用于理解进入顺序。']
    assert lesson_prep.parse_prep(json.dumps(data)).questions[0].required_points
    for system in (lesson_prep.PREP_SYSTEM,lesson_prep.REVIEW_SYSTEM):
        assert '所有随口问（包括 choice）required_points' in system
        assert '字符串数组' in system


def test_prep26_guide_response_follows_question_and_unused_extra_is_removed():
    assert 'response 必须回答题面实际所问' in lesson_prep.PREP_SYSTEM
    assert '不能把“违反了哪一点”误当成' in lesson_prep.REVIEW_SYSTEM
    assert '不往清单加正文未讲的新知识' in lesson_prep.PREP_SYSTEM
    assert '优先删掉该项' in lesson_prep.REVIEW_SYSTEM
    a=[1,2];a.append([3,4]);a.extend([3,4])
    assert a==[1,2,[3,4],3,4]  # distinct from two independent initial lists
    assert '每种方案各从同一初值独立执行' in lesson_prep.PREP_SYSTEM
    assert '“分别”不替代这项前提' in lesson_prep.REVIEW_SYSTEM
