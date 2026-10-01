"""Regression rules derived from the existing public errors, not a holdout."""
import copy
import json
from pathlib import Path

import pytest

from refactor.learning.course import judge_agent as judge
from refactor.learning.course.answer_compare import compare_exact
from refactor.learning.course.planner_agents import AgentFormatError


OLD = Path(__file__).parents[1] / 'judge_rules'
SAMPLES = json.loads((OLD / 'chinese_samples.json').read_text(encoding='utf-8'))['questions']
CURRENT = json.loads(Path(__file__).with_name('calibrated_grades.json').read_text(encoding='utf-8'))


def ctx():
    q = copy.deepcopy(SAMPLES[3]['question'])
    return dict(mode='test', points=[dict(id=p,title='原有题目',summary='') for p in q['point_ids']],
                excerpts={'c':'参考'}, question=q, reference=q['reference'],
                required_points=q['required_points'], require_evidence=True)


def verdict(context, applicability=(True, False), statuses=('hit','miss')):
    checks = [dict(point_id=context['points'][0]['id'], point=r['point'], applies=a,
                   status=s, evidence='原话' if a else '')
              for r,a,s in zip(context['required_points'],applicability,statuses)]
    wrong = any(a and s != 'hit' for a,s in zip(applicability,statuses))
    return dict(kind='answer',correct=not wrong,
                wrong_points=[context['points'][0]['id']] if wrong else [],
                reason='核对题面要求和实际表达。',uncertain=False,checks=checks)


def test_extra_reference_detail_is_auditable_and_not_an_error():
    context=ctx(); raw=verdict(context)
    parsed=judge.parse_judgement(json.dumps(raw),context)
    assert parsed.correct and parsed.checks[1]['applies'] is False
    raw['checks'][1]['applies']=True
    with pytest.raises(AgentFormatError): judge.parse_judgement(json.dumps(raw),context)


@pytest.mark.parametrize('bad', ['all_excluded','excluded_hit','excluded_evidence','non_bool'])
def test_excluding_rubric_items_cannot_create_empty_or_inconsistent_pass(bad):
    context=ctx();raw=verdict(context)
    if bad=='all_excluded':
        for c in raw['checks']: c.update(applies=False,status='miss',evidence='')
    if bad=='excluded_hit':raw['checks'][1]['status']='hit'
    if bad=='excluded_evidence':raw['checks'][1]['evidence']='学生没说的'
    if bad=='non_bool':raw['checks'][1]['applies']=0
    with pytest.raises(AgentFormatError): judge.parse_judgement(json.dumps(raw),context)


def test_actual_miss_and_wrong_remain_errors_even_with_extra_reference_detail():
    context=ctx()
    for status in ('miss','wrong'):
        raw=verdict(context,statuses=(status,'miss'))
        v=judge.parse_judgement(json.dumps(raw),context)
        assert v.correct is False and v.wrong_points==tuple(context['question']['point_ids'])


def test_legacy_checks_still_mean_all_items_apply():
    context=ctx();raw=verdict(context,applicability=(True,True),statuses=('hit','hit'))
    for c in raw['checks']:c.pop('applies')
    assert judge.parse_judgement(json.dumps(raw),context).correct


def test_scoring_adaptation_is_general_and_does_not_contain_public_sample_ids():
    system,messages=judge.judge_request(ctx(),'原有简略回答')
    assert '指代' in system and 'applies' in system and '不能补全内容' in system
    assert 'public-' not in system and 'SciEntsBank' not in system
    assert 'uncertain和完整checks' in system


@pytest.mark.parametrize('answer,reply,kind,expected',[
    ('4','答案是 ４','number',True),('4','结果为 5','number',False),
    ('4','不是4','number',None),('4','答案是4，也可以是5','number',None),
    ('4','答：4，我肯定对','number',None),('x+1','the answer is 1+x','expression',True),
    ('append','答：APPEND','term',True),('append','apend','term',None),
])
def test_only_unambiguous_answer_labels_are_removed(answer,reply,kind,expected):
    q=dict(type='exact',stem='说出确定答案',answer=answer,accepted=[answer],answer_kind=kind)
    assert compare_exact(q,reply) is expected


@pytest.mark.parametrize('saved',CURRENT,ids=lambda r:r['id'])
def test_replay_received_calibration_results_against_original_labels(saved):
    sample=next(s for s in SAMPLES if any(c['id']==saved['id'] for c in s['cases']))
    case=next(c for c in sample['cases'] if c['id']==saved['id'])
    if saved['method']=='program':
        assert compare_exact(sample['question'],case['reply']) is case['expected_correct']
    else:
        q=sample['question']
        context=dict(mode='test',points=[dict(id=p,title=sample['subject'],summary='') for p in q['point_ids']],
            excerpts={c:sample['source_text'] for c in q['chunk_ids']},question=q,reference=q['reference'],
            required_points=q.get('required_points'),require_evidence=q['type'] in ('short','fix_error','essay'))
        v=judge.parse_judgement(saved['raw'],context)
        assert (v.kind,v.correct,list(v.wrong_points),v.uncertain)==(
            case['expected_kind'],case['expected_correct'],case['expected_wrong_points'],False)
