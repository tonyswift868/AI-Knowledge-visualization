"""Question/grade structures and a frozen human-labelled Chinese regression set."""
import copy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.course.answer_compare import compare_exact
from refactor.learning.course import judge_agent as judge, question_agent as question
from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.app.classroom import Classroom, _question_dict

SAMPLES = json.loads(Path(__file__).with_name('chinese_samples.json').read_text(encoding='utf-8'))['questions']
SAVED = json.loads(Path(__file__).with_name('saved_grades.json').read_text(encoding='utf-8'))


def context(sample):
    q=sample['question']
    return dict(mode='test',points=[dict(id=p,title=sample['subject'],summary='') for p in q['point_ids']],
                excerpts={cid: sample['source_text'] for cid in q['chunk_ids']},question=q,
                reference=q['reference'],required_points=q.get('required_points'),
                require_evidence=q['type'] in ('short','fix_error','essay'))


def test_prelabelled_samples_cover_every_subject_type_and_answer_category():
    assert len(SAMPLES)==30 and sum(len(s['cases']) for s in SAMPLES)==180
    for subject in ('初中数学','历史','Python'):
        for kind in ('exact','short','fix_error'):
            assert sum(s['subject']==subject and s['question']['type']==kind for s in SAMPLES)>=3
    assert sum(s['question']['type']=='essay' for s in SAMPLES)==3
    assert len({c['id'] for s in SAMPLES for c in s['cases']})==180


@pytest.mark.parametrize('saved',SAVED,ids=lambda r:r['id'])
def test_replay_all_real_fixed_answers_without_model_calls(saved):
    sample=next(s for s in SAMPLES if any(c['id']==saved['id'] for c in s['cases']))
    case=next(c for c in sample['cases'] if c['id']==saved['id'])
    if saved['method']=='program':
        assert compare_exact(sample['question'],case['reply']) is case['expected_correct']
    else:
        v=judge.parse_judgement(saved['raw'],context(sample))
        assert (v.kind,v.correct,list(v.wrong_points),v.uncertain)==(case['expected_kind'],case['expected_correct'],case['expected_wrong_points'],False)


@pytest.mark.parametrize('sample', SAMPLES, ids=lambda s:s['question']['id'])
def test_new_question_structure_round_trip(sample):
    q=sample['question']
    ctx=dict(purpose='bank',per_point=1,counts={p:1 for p in q['point_ids']},
             points=[dict(id=p,title=sample['subject'],summary='',chunk_ids=q['chunk_ids']) for p in q['point_ids']],
             excerpts={cid:sample['source_text'] for cid in q['chunk_ids']})
    parsed,=question.parse_questions(json.dumps({'questions':[q]},ensure_ascii=False),ctx)
    result=_question_dict(parsed)
    for k,v in q.items(): assert result[k]==v


@pytest.mark.parametrize('kind,answer,reply,expected',[
    ('number','0.3','0.30001',True),('number','0.3','0.301',False),
    ('number','0.5','1/2',True),('expression','2*(x+3)','6+2x',True),
    ('expression','(x+1)^2','x*x+2*x+1',True),('expression','x*x','x',False),
    ('expression','0','x-x',True),('expression','x-x','0',True),
    ('expression','3x=12','x=4',True),('expression','x/x','1',None),
    ('expression','sin(x)','x',None),('expression','x','__import__("os")',None),
    ('expression','x','x**1000000',None),('term','append',' ＡＰＰＥＮＤ ',True),
    ('term','append','追加',None),('text','hello','别的含义',None),
])
def test_program_comparison_is_conservative_and_does_not_execute(kind,answer,reply,expected):
    q=dict(type='exact',stem='计算或说出确定答案。',answer=answer,accepted=[answer],answer_kind=kind,tolerance=0.0001)
    assert compare_exact(q,reply) is expected


def test_equivalent_final_answer_does_not_bypass_requested_process():
    q=dict(type='exact',stem='写出移项后的那一步。',answer_kind='expression',answer='3x=12',accepted=['3x=12'])
    assert compare_exact(q,'x=4') is None


@pytest.mark.parametrize('statuses,passed,wrong', [(['hit','miss'],False,['b']),(['hit','wrong'],False,['b']),(['hit','hit'],True,[])])
def test_structured_multi_point_only_marks_actual_wrong_point(statuses,passed,wrong):
    sample=copy.deepcopy(SAMPLES[3]); q=sample['question'];q['point_ids']=['a','b']
    checks=[dict(point_id=pid,point=req['point'],status=status,evidence='学生原话')
            for pid,req,status in zip(q['point_ids'],q['required_points'],statuses)]
    value=dict(kind='answer',correct=passed,wrong_points=wrong,reason='逐条核对当前作答。',uncertain=False,checks=checks)
    assert judge.parse_judgement(json.dumps(value,ensure_ascii=False),context(sample)).wrong_points==tuple(wrong)
    value['correct']=not passed
    with pytest.raises(AgentFormatError):judge.parse_judgement(json.dumps(value),context(sample))


def test_missing_single_point_identifier_is_determined_by_question_not_guessed():
    sample=SAMPLES[12];q=sample['question']
    raw=dict(kind='answer',correct=False,wrong_points=q['point_ids'],reason='说成完全隔绝，方向相反。',uncertain=False,
             checks=[dict(point=q['required_points'][0]['point'],status='wrong',evidence='只会隔绝')])
    verdict=judge.parse_judgement(json.dumps(raw),context(sample))
    assert verdict.checks[0]['point_id']==q['point_ids'][0]


@pytest.mark.parametrize('statuses,correct', [(['met','met','met'],True),(['met','met','not_met'],False),(['not_met','met','met'],False)])
def test_essay_pass_line_is_computed_from_required_and_optional_criteria(statuses,correct):
    s=next(s for s in SAMPLES if s['question']['type']=='essay');q=s['question']
    checks=[dict(point_id=q['point_ids'][0],criterion=r['criterion'],status=status,evidence='原话')
            for r,status in zip(q['rubric'],statuses)]
    raw=dict(kind='answer',correct=correct,wrong_points=[] if correct else q['point_ids'],
             reason='核对论述评分细则。',uncertain=False,checks=checks)
    assert judge.parse_judgement(json.dumps(raw),context(s)).correct is correct


def test_independent_review_never_guesses_pass_and_default_is_off_until_proven_better():
    wrong=judge.Verdict('answer',False,('p',),'确实有错。')
    right=judge.Verdict('answer',True,(),'答到要点。')
    assert judge.review_verdict(wrong,wrong).correct is False
    assert judge.review_verdict(wrong,right).kind=='clarify'
    assert not judge.REVIEW_WRONG_DEFAULT


def test_classroom_review_is_a_second_isolated_call_without_first_reason():
    sample=SAMPLES[3];q=sample['question']
    room=Classroom(SimpleNamespace(store=SimpleNamespace(subscribe=lambda fn:lambda:None)),lambda:None,review_wrong=True)
    course=SimpleNamespace(point=lambda p:dict(id=p,title='点',summary=''),excerpts=lambda p:{'c':'资料'})
    calls=[]
    results=[judge.Verdict('answer',False,tuple(q['point_ids']),'遗漏第二项。'),judge.Verdict('answer',True,(),'实际全部答到。')]
    def ask(*args):calls.append(args[3]);return results[len(calls)-1]
    room._ask=ask
    verdict=room._judge(course,{},None,'test',dict(question=q,point_ids=q['point_ids']),'不能除以零，因为没有定义。')
    assert verdict.kind=='clarify' and len(calls)==2 and calls[0]==calls[1]
    assert '遗漏第二项' not in calls[1][1][0]['content']


def test_exact_wrong_reason_does_not_invent_the_learners_method_from_real_output():
    sample=SAMPLES[21];q=sample['question']
    saved=next(r for r in SAVED if r['id']=='q22-a6')
    room=Classroom(SimpleNamespace(store=SimpleNamespace(subscribe=lambda f:lambda:None),
        gateway=SimpleNamespace(complete=lambda *a,**k:SimpleNamespace(text=saved['raw']))),lambda:None)
    course=SimpleNamespace(plan_id='plan',point=lambda p:dict(id=p,title='len',summary=''),excerpts=lambda p:{'c':'len返回元素个数'})
    v=room._judge(course,{},None,'test',dict(question=q,point_ids=q['point_ids']),'30，我肯定对')
    assert v.correct is False and v.wrong_points==tuple(q['point_ids'])
    assert '求和' not in v.reason and '3' in v.reason


def test_invalid_structured_requirement_does_not_poison_other_bank_rows():
    s=copy.deepcopy(SAMPLES[3]);q=s['question'];q['required_points'][0]['accept']=[]
    ctx=dict(purpose='bank',per_point=1,points=[dict(id=q['point_ids'][0],title='数学',summary='',chunk_ids=q['chunk_ids'])],excerpts={'c4':'资料'})
    assert question.parse_questions(json.dumps({'questions':[q]},ensure_ascii=False),ctx,partial_bank=True)==[]


def test_free_practice_only_flags_actual_wrong_point_and_reads_legacy_records():
    from refactor.learning.course.question_bank import review_points
    rows=[dict(id='1',at='2026-10-01T00:00:00Z',point_ids=['a','b'],correct=False,wrong_points=['b'])]
    assert review_points(rows)=={'b'}
    rows[0].pop('wrong_points')
    assert review_points(rows)=={'a','b'}


def test_new_bank_fields_persist_and_sync_without_a_schema_rebuild(tmp_path):
    from datetime import date, timedelta
    from refactor.learning.course.store import CourseStore, StoreError
    from refactor.learning.course.ledger import PlanWindow
    day=date(2026,10,1);plan='a'*32
    with CourseStore('new-question',tmp_path) as a, CourseStore('new-question-peer',tmp_path) as b:
        a.create_plan(plan,goal='结构测试',window=PlanWindow(day,day+timedelta(days=3),100))
        a.add_sources(plan,[dict(id='s',url='https://example.invalid',chunks=[dict(chunk_id='c',text='参考')])])
        a.save_outline(plan,[dict(id='st',title='阶段')],[dict(id='m',stage_id='st',title='模块')])
        a.save_generated_module(plan,'m',[dict(id='p',title='点',summary='点',minutes=10,chunk_ids=['c'])],check_minutes=2,today=day)
        samples=[copy.deepcopy(SAMPLES[i]['question']) for i in (0,3,18)]
        for q in samples:q.update(point_ids=['p'],chunk_ids=['c'])
        ids=a.add_bank_questions(plan,'m',samples)
        rows=a.pending_records(100)
        b.apply_remote([(r, index+1) for index,r in enumerate(rows)])
        assert [r['type'] for r in b.records('question_bank',plan)]==[r['type'] for r in a.records('question_bank',plan)]
        assert next(r for r in b.records('question_bank',plan) if r['type']=='exact')['answer_kind']=='number'
        with pytest.raises(StoreError) as exc:
            a.add_bank_questions(plan,'m',[samples[-1]])
        assert exc.value.code=='MODULE_ESSAY_LIMIT'
        assert len(a.records('question_bank',plan))==3
        a.append_practice_result(plan,ids[1],'m',['p'],False,wrong_points=['p'])
        assert a.records('practice_results',plan)[0]['wrong_points']==['p']
