"""One non-leading clarification, durable across classroom restarts."""
import json
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.course import judge_agent as judge
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import Judgement, AttemptClosed
from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.course.store import CourseStore

DAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
PLAN = 'e' * 32
from tests.refactor.integration38.support import bank_question,seed_bank,context as call_context
Q = dict(bank_question('p0','m',answer='追加在末尾，改变原列表',chunk='c'), id='subjective', type='short', stem='说明列表追加的位置和是否改变原列表',
    options=[], answer='追加在末尾，改变原列表', reference='追加在末尾，改变原列表',
    required_points=[dict(point=p, accept=[p], reject=[]) for p in ('追加在末尾', '改变原列表')],
    point_ids=['p0'], chunk_ids=['c'], angle='apply')


def context(q=None, **kw):
    return dict(mode='test', question=q or Q, points=[dict(id='p0', title='Python列表', summary='操作')],
                reference=Q['reference'], excerpts={'c': '原文'}, require_evidence=True, **kw)


def raw(kind='insufficient', statuses=('hit', 'miss')):
    return dict(kind=kind, correct=None, wrong_points=[], reason='已说明位置但未说明是否改变原列表。',
        uncertain=False, checks=[dict(point_id='p0', point=p['point'], applies=True, status=s,
                                     evidence='放在末尾' if s=='hit' else '')
                                for p,s in zip(Q['required_points'], statuses)])


def test_insufficient_never_marks_an_error_and_after_one_probe_settles_missing():
    v=judge.parse_judgement(json.dumps(raw(),ensure_ascii=False), context())
    assert (v.kind,v.correct,v.wrong_points)==('insufficient',None,())
    second=judge.parse_judgement(json.dumps(raw(),ensure_ascii=False),context(information_followed_up=True))
    assert (second.kind,second.correct,second.wrong_points)==('answer',False,('p0',))
    wrong=raw(statuses=('hit','wrong'))
    with pytest.raises(AgentFormatError): judge.parse_judgement(json.dumps(wrong,ensure_ascii=False),context())
    with pytest.raises(AgentFormatError):
        judge.parse_judgement(json.dumps(dict(kind='insufficient',correct=None,wrong_points=[],reason='表达不足。')),
                             dict(context(),require_evidence=False))
    unclear=dict(kind='clarify',correct=None,wrong_points=[],reason='仍无法判定。',uncertain=False,checks=[])
    with pytest.raises(AgentFormatError):
        judge.parse_judgement(json.dumps(unclear,ensure_ascii=False),context(information_followed_up=True))


def test_partial_expression_defers_but_substantive_error_does_not():
    first=raw('answer'); first.update(correct=False,wrong_points=['p0'])
    v=judge.parse_judgement(json.dumps(first,ensure_ascii=False),context())
    assert judge.defer_incomplete(v,context()).kind=='insufficient'
    assert judge.defer_incomplete(v,context(information_followed_up=True)).kind=='answer'
    first['checks'][1]['status']='wrong'
    v=judge.parse_judgement(json.dumps(first,ensure_ascii=False),context())
    assert judge.defer_incomplete(v,context()).kind=='answer'


def test_legacy_requirements_and_process_missing_remain_separate():
    q=dict(Q, required_points=['追加在末尾','改变原列表'])
    checks=[dict(point_id='p0',requirement=p,applies=True,met=i==0,evidence='末尾' if i==0 else '')
            for i,p in enumerate(q['required_points'])]
    obj=dict(raw(),checks=checks)
    assert judge.parse_judgement(json.dumps(obj,ensure_ascii=False),context(q)).kind=='insufficient'
    obj.update(kind='process_missing',checks=[])
    assert judge.parse_judgement(json.dumps(obj,ensure_ascii=False),context(q)).kind=='process_missing'


def test_essay_missing_reason_does_not_conceal_factual_error():
    rubric=[dict(criterion=c,met='达到',not_met='未达到',required=i<2)
            for i,c in enumerate(('核心观点','没有事实错误','论据'))]
    q=dict(Q,type='essay',rubric=rubric)
    obj=dict(raw(),checks=[dict(point_id='p0',criterion=r['criterion'],status='met' if i<2 else 'not_met',evidence='观点')
                         for i,r in enumerate(rubric)])
    assert judge.parse_judgement(json.dumps(obj,ensure_ascii=False),context(q)).kind=='insufficient'
    obj['checks'][1]['status']='not_met'
    with pytest.raises(AgentFormatError):judge.parse_judgement(json.dumps(obj,ensure_ascii=False),context(q))


def test_examples_select_three_same_subject_type_deterministically_without_model():
    for subject in ('初中数学','历史','Python','科学'):
        for typ in ('choice','exact','short','fix_error'):
            q=dict(Q,type=typ)
            selected=judge.select_examples(q,subject)
            assert len(selected)==3 and selected==judge.select_examples(q,subject)
            assert all(e['subject']==subject and e['type']==typ for e in selected)
    assert len(judge.select_examples(dict(Q,type='essay'),'历史'))==3
    system,messages=judge.judge_request(context(), '末尾')
    assert '同类已判示例' in system and 'information_followed_up' not in messages[0]['content']
    system,messages=judge.judge_request(context(information_followed_up=True), '末尾')
    assert '"information_followed_up":true' in messages[0]['content']


def test_followup_only_uses_non_leading_templates_and_checks_answer_substrings():
    text=judge.information_prompt(Q)
    assert judge.followup_is_safe(text,Q)
    assert not judge.followup_is_safe('是否在末尾并改变原列表？',Q)
    # First neutral phrase would disclose this unusual question's answer.
    unusual=dict(Q,answer='具体说说',reference='必答要点：具体说说')
    assert judge.information_prompt(unusual) != '能再具体说说你的回答吗？'
    assert judge.followup_is_safe(judge.information_prompt(unusual),unusual)


@pytest.fixture
def room28(tmp_path):
    with CourseStore('batch28',tmp_path) as store:
        store.create_plan(PLAN,goal='Python',window=PlanWindow(DAY,DAY+timedelta(days=2),100))
        store.add_sources(PLAN,[dict(id='s',url='https://docs.python.org/3/tutorial/',adopted=True,chunks=[dict(chunk_id='c',text=Q['reference'])])])
        store.save_outline(PLAN,[dict(id='st',title='基础')],[dict(id='m',stage_id='st',title='列表')])
        store.save_generated_module(PLAN,'m',[dict(id=p,title='Python列表',summary='操作',minutes=10,chunk_ids=['c'])
            for p in ('p0','p1')],check_minutes=8,final_check_minutes=6,check_question_minutes=2,today=DAY)
        seed_bank(store,PLAN)
        from refactor.learning.course.question_bank import FIELDS
        store.add_bank_questions(PLAN,'m',[{k:v for k,v in Q.items() if k in FIELDS}])
        store.start_plan(PLAN,today=DAY)
        store.append_judgement(PLAN,Judgement('base',1,NOW,'module_test','m','base',(('p0',True),('p1',True))))
        store.append_attempt_closed(PLAN,AttemptClosed('closed',2,'m','base','module_test'))
        seen=[]
        def complete(purpose,messages,**kwargs):
            seen.append(messages[0]['content'])
            return SimpleNamespace(text=json.dumps(raw(),ensure_ascii=False))
        services=SimpleNamespace(store=store,account_id='batch28',data_root=tmp_path,
            settings=SimpleNamespace(load=lambda:dict(refresh_time='04:00')),gateway=SimpleNamespace(complete=complete))
        room=Classroom(services,lambda:DAY,now=lambda:NOW)
        for name in ('_teacher','_go_on','_prepare_later','_next_check','_prepare_bank_later','_ask_next','_ask_practice'):
            setattr(room,name,lambda *a,**k:None)
        yield room,store,seen


@pytest.mark.parametrize('kind',['casual','quick','module_test','retest','skip_test','check','practice'])
def test_one_followup_no_credit_before_answer_all_classroom_modes_and_restart(room28,kind):
    room,store,seen=room28
    co=room._course(PLAN)
    pending=dict(kind=kind,answer_id='answer28',point_ids=['p0'],question=dict(Q),asks=2)
    state=dict(version=2,module_id='m',step='testing',index=0,pending=pending,
        test=dict(kind=kind,attempt='a',index=0,queue=[dict(Q),dict(Q)],targets=['p0']),
        practice=dict(index=0,count=2,queue=[dict(Q),dict(Q)],resume=dict(module_id='m',step='teach')),
        check=dict(index=0,queue=[dict(Q)]))
    room._save(co,state)
    def handler(co,st,ctx,text,pending):
        result=room.perform(PLAN,'reply',call_context(),(text,))
        assert not result['failure'],result['failure']
    before=store.records('credits',PLAN)
    handler(co,state,None,'放在末尾',pending)
    assert room._file(PLAN).read({})['pending']['answer_id']==pending['answer_id'] and len(store.records('judgements',PLAN))==1
    assert store.records('credits',PLAN)==before and not store.records('question_answers',PLAN)
    prompt=next(r['content'] for r in store.records('messages',PLAN) if r['kind']=='question')
    assert judge.followup_is_safe(prompt,Q)
    # Read durable state, like a new process; second insufficient is settled.
    restored=room._file(PLAN).read({}); saved=restored['pending']
    handler(co,restored,None,'没有其他补充',saved)
    assert len(seen)==2 and '原回答：放在末尾' in seen[-1] and '没有其他补充' in seen[-1]
    assert sum(r['content']==prompt for r in store.records('messages',PLAN))==1
    assert len(store.records('practice_results' if kind=='practice' else 'judgements',PLAN))==(1 if kind=='practice' else 2)
    if kind!='practice':
        prior=room._prior_verdict(co,restored,saved)
        assert prior.correct is False
        room._record(co,restored,'check' if kind=='check' else 'casual' if kind=='casual' else
                     'quick_confirm' if kind=='quick' else kind,saved,prior,'没有其他补充')
        assert len(store.records('judgements',PLAN))==2


def test_interrupted_outbox_does_not_grade_or_emit_duplicate_question(room28,monkeypatch):
    room,store,seen=room28;co=room._course(PLAN)
    state=dict(version=2,module_id='m',index=0,step='testing',pending=dict(kind='module_test',answer_id='outbox',point_ids=['p0'],question=dict(Q)),
               test=dict(kind='module_test',attempt='a',index=0,queue=[Q,Q],targets=['p0']))
    room._save(co,state)
    append=store.append_message
    monkeypatch.setattr(store,'append_message',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('interrupted message write')))
    result=room.perform(PLAN,'reply',call_context(),('放在末尾',),request_id='first')
    assert result['failure'] and not room._file(PLAN).read({})['pending'].get('followup')
    assert len(store.records('judgements',PLAN))==1 and not store.records('messages',PLAN)
    monkeypatch.setattr(store,'append_message',append)
    result=room.perform(PLAN,'retry',call_context(),request_id='retry')
    assert not result['failure']
    result=room.perform(PLAN,'reply',call_context(),('放在末尾',),request_id='first')
    assert not result['failure'] and sum(r['kind']=='question' for r in store.records('messages',PLAN))==1


def test_supplement_can_pass_and_saved_event_contains_both_answers(room28):
    room,store,seen=room28; co=room._course(PLAN)
    pending=dict(kind='check',answer_id='successful',point_ids=['p0'],question=dict(Q))
    pending['question']['id']='subjective'
    state=dict(version=2,module_id='m',step='checking',phase='check',index=0,pending=pending,check=dict(index=0,queue=[dict(Q)]))
    room._save(co,state)
    result=room.perform(PLAN,'reply',call_context(),('放在末尾',))
    assert not result['failure']
    good=raw('answer',statuses=('hit','hit'));good.update(correct=True,wrong_points=[])
    good['checks'][1]['evidence']='会改变原列表'
    room.services.gateway=SimpleNamespace(complete=lambda *a,**k:SimpleNamespace(text=json.dumps(good,ensure_ascii=False)))
    result=room.perform(PLAN,'reply',call_context(),('会改变原列表',))
    assert not result['failure'],result['failure']
    row=next(r for r in store.records('judgements',PLAN) if r['id']=='successful')['details']
    assert '放在末尾' in row['answer'] and '会改变原列表' in row['answer']
    assert '原回答：放在末尾' in row['answer']
    assert co.book().point('p0').level==3
    prior=room._prior_verdict(co,state,pending)
    room._record(co,state,'check',pending,prior,'会改变原列表')
    assert len(store.records('judgements',PLAN))==2


def test_information_is_not_combined_into_a_replacement_question(room28):
    room,store,seen=room28;co=room._course(PLAN)
    pending=dict(answer_id='new',point_ids=['p0'],question=Q)
    room._judge(co,{},None,pending,'新答案')
    assert '旧答案' not in seen[-1] and '原回答：' not in seen[-1]


@pytest.mark.parametrize('row',json.loads(Path(__file__).with_name('received28.json').read_text(encoding='utf-8'))['rows'],
                         ids=lambda r:r['id'])
def test_current_real_responses_replay_without_more_requests(row):
    ctx=row['context']
    verdict=judge.parse_judgement(row['response'],ctx)
    verdict=judge.defer_incomplete(verdict,ctx)
    if ctx.get('information_followed_up'):
        assert verdict.kind=='answer' and not verdict.uncertain
    if verdict.kind=='insufficient':
        assert verdict.correct is None and not verdict.wrong_points
        assert judge.followup_is_safe(judge.information_prompt(ctx['question']),ctx['question'])
        assert judge.settle_insufficient(verdict,ctx).correct is False
