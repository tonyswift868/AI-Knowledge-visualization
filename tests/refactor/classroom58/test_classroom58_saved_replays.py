"""Frozen excerpts from small57: no network, private data or live model calls."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from refactor.learning.app.classroom import Classroom
from refactor.learning.course import teacher_agent, question_bank
from refactor.learning.course.mastery import MasteryBook
from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.course.judge_agent import Verdict


def fixture():
    return json.loads(Path(__file__).with_name('small57_excerpt.json').read_text(encoding='utf-8'))


def replay_room(data, prefix):
    events=data['mastery']['events']
    checks=[r for r in data['judgements'] if r['event']['context']=='check']
    cutoff=checks[prefix]['event']['seq'] if prefix<len(checks) else float('inf')
    history=[r for r in data['judgements'] if r['event']['seq']<cutoff]
    mastery=copy.deepcopy(data['mastery'])
    mastery['events']=[e for e in events if e['seq']<cutoff]
    book=MasteryBook.from_dict(mastery)
    bank=copy.deepcopy(data['bank'])
    for row in history:
        if row['event']['context']=='check':
            for q in bank:
                if q['id']==row['details']['question_id']:
                    q['uses'].append(dict(purpose='final',at=row['event']['at']))
    records={'judgements':history,'question_bank':bank,'question_answers':data['answers']}
    store=SimpleNamespace(records=lambda entity,plan:records[entity])
    course=SimpleNamespace(store=store,plan_id='saved57',points=data['points'],book=lambda:book,
        point=lambda pid:next(p for p in data['points'] if p['id']==pid),
        points_of=lambda mid:[p for p in data['points'] if p['module_id']==mid])
    room=Classroom(SimpleNamespace(),lambda:None)
    materials=SimpleNamespace(pick_question=lambda store,plan,pid,purpose,exclude_ids=():
        question_bank.select_question(bank,pid,purpose,exclude_ids))
    room._materials=lambda:materials
    room._show_question=lambda course,state,q,kind:state.update(shown=q)
    room._prepare_one=lambda *args:pytest.fail('saved bank has usable questions; do not prepare')
    return room,course,history


@pytest.mark.parametrize('prefix',[0,3,10,11,12,13])
def test_small57_final_replay_prefers_unasked_question_and_spacing(prefix):
    data=fixture();room,course,history=replay_room(data,prefix)
    state=dict(check_used=0,check=None,check_missing=[])
    room._next_check(course,state,None)
    q=state['shown']
    asked=[r['details']['question_id'] for r in history if r['event']['context']=='check']
    # Every frozen bank point has a second usable question. The late failures
    # came from repeatedly excluding that question just because of module use.
    if prefix in (10,11,12,13):assert q['id'] not in asked
    assert q['id'] not in asked[-3:]
    again=copy.deepcopy(state);again['check']=None;again.pop('shown')
    fresh,course,_=replay_room(data,prefix)
    fresh._next_check(course,again,None)
    assert again['shown']['id']==q['id']  # reopening does not reset gap history


def test_exhausted_bank_waits_three_other_questions_when_possible():
    data=fixture();room,course,history=replay_room(data,0)
    # All ten points remain eligible, with one question each, already asked.
    bank=course.store.records('question_bank','saved57')
    bank[:]=[next(q for q in bank if q['role']=='bank' and p['id'] in q['point_ids']) for p in course.points]
    history[:]=[dict(event=dict(seq=i,context='check'),details=dict(question_id=q['id'])) for i,q in enumerate(bank)]
    state=dict(check_used=0,check=None)
    room._next_check(course,state,None)
    assert state['shown']['id'] not in [q['id'] for q in bank[-3:]]


def test_one_remaining_question_relaxes_gap_and_does_not_deadlock():
    data=fixture();room,course,history=replay_room(data,13)
    pid=course.book().next_check_items()[0][0]
    bank=course.store.records('question_bank','saved57')
    bank[:]=[next(q for q in bank if q['role']=='bank' and pid in q['point_ids'])]
    history.append(dict(event=dict(seq=10**30,context='check'),details=dict(question_id=bank[0]['id'])))
    state=dict(check_used=0,check=None)
    room._next_check(course,state,None)
    assert state['shown']['id']==bank[0]['id']


def test_two_exhausted_questions_alternate_even_with_identical_use_times():
    data=fixture();room,course,history=replay_room(data,13)
    pid=course.book().next_check_items()[0][0]
    bank=course.store.records('question_bank','saved57')
    bank[:]=[q for q in bank if q['role']=='bank' and pid in q['point_ids']]
    assert len(bank)==2
    module_ids=room._test_questions(course,pid)
    older=next(q for q in bank if q['id'] in module_ids)
    latest=next(q for q in bank if q['id']!=older['id'])
    history.extend([dict(event=dict(seq=10**30+i,context='check'),details=dict(question_id=q['id']))
        for i,q in enumerate((older,latest))])
    for q in bank:q['uses']=[dict(purpose='final',at='2026-10-01T10:00:00+00:00')]
    state=dict(check_used=0,check=None)
    room._next_check(course,state,None)
    assert state['shown']['id']==older['id']


@pytest.mark.parametrize('index',[0,1])
def test_saved_uncited_chinese_replies_are_displayed(index):
    data=fixture()
    teaching=teacher_agent.parse_teaching(data['teacher_replies'][index],data['teacher_context'])
    assert teaching.speech==data['teacher_replies'][index].split('【选项】')[0].strip()
    assert len(teaching.options)==3


def test_invalid_ids_removed_and_uncited_paragraphs_stay():
    ctx=fixture()['teacher_context']|dict(opening=False)
    text='第一段没有编号。\n\n第二段[c1]，错误编号[c999]。'
    result=teacher_agent.parse_teaching(text,ctx)
    assert result.speech=='第一段没有编号。\n\n第二段[c1]，错误编号。'


def test_teacher_replay_never_retries_or_pastes_english_source():
    data=fixture();ctx=data['teacher_context'];calls=[]
    def complete(*args,**kwargs):
        calls.append(args);return SimpleNamespace(text=data['teacher_replies'][min(len(calls)-1,1)])
    store=SimpleNamespace(records=lambda *a:[])
    course=SimpleNamespace(store=store,plan_id='saved57',points=ctx['module']['points'],excerpts=lambda ids:ctx['excerpts'],point=lambda pid:next(p for p in ctx['module']['points'] if p['id']==pid))
    room=Classroom(SimpleNamespace(gateway=SimpleNamespace(complete=complete)),lambda:None)
    room._skeleton=lambda *a:(ctx['plan'],ctx['module'])
    room._prefs=lambda:ctx['prefs'];room._dialogue=lambda *a:[]
    result=room._teacher(course,{},None,ctx['director'],[ctx['current_points'][0]['id']],opening=True)
    assert len(calls)==1 and result.speech.startswith('刚才我们摸到了键盘')
    # Repeated genuine format failure is surfaced, never replaced by raw source.
    room.services.gateway.complete=lambda *a,**kw:SimpleNamespace(text='Only English.')
    with pytest.raises(AgentFormatError):room._teacher(course,{},None,ctx['director'],[ctx['current_points'][0]['id']],opening=True)


def test_uncited_delivery_uses_existing_original_cards():
    data=fixture();room,course,_=replay_room(data,0)
    pid=course.points[0]['id'];course.excerpts=lambda ids:{'original':'English source.'}
    class Transaction:
        def __enter__(self):pass
        def __exit__(self,*args):pass
    course.store.transaction=lambda:Transaction()
    room._focus=lambda *args:[pid]
    messages=[];room._say=lambda co,st,text,**kw:messages.append(text)
    room._deliver(course,{},teacher_agent.Teaching('中文讲解没有引用。',()))
    assert messages[0].startswith('中文讲解没有引用。')
    assert 'mf-source:original' in messages[0] and 'English source.' not in messages[0]


def test_recorded_angle_does_not_rotate_on_reused_question():
    data=fixture();room,course,_=replay_room(data,13)
    q=course.store.records('question_bank','saved57')[0]
    room._prior_verdict=lambda *args:None;room.now=lambda:datetime.now(timezone.utc)
    course.next_seq=lambda:10**30
    events=[];course.store.append_judgement=lambda plan,event,**kw:events.append(event)
    room._day=lambda st:None
    pending=dict(question=q,point_ids=q['point_ids'],answer_id='new-answer')
    verdict=Verdict('correct',True,(),'程序判')
    room._record(course,{},'check',pending,verdict,'正确')
    pending['answer_id']='another-answer'
    room._record(course,{},'check',pending,verdict,'正确')
    assert [e.angle for e in events]==['recall','recall']
