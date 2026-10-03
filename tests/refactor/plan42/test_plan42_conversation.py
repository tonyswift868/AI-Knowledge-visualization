"""Unlimited dialogue, explicit preview consent and future MF-02 contract."""
import json
import threading
from datetime import date
from types import SimpleNamespace

import pytest

from refactor.learning.app.bridge import CallContext, BridgeError
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import planner_agents as agents, plan_intent
from refactor.learning.course.store import CourseStore
from tests.refactor.plan37.test_grounded_plan37_flow import form, fixture, adopt_and_map


class ConversationModel:
    def __init__(self):
        self.round = 0
        self.requests = []
        self.intent = {'kind': 'supplement', 'instruction': '只理解术语，不写代码，增加网络'}

    def complete(self, purpose, messages, **kw):
        self.requests.append((purpose, kw.get('system'), messages))
        if kw['system'] == agents.PREVIEW_SYSTEM:
            changed = '增加网络' in messages[-1]['content']
            value = dict(summary='零基础，以生活化例子理解计算机术语，不做操作。',
                         modules=[dict(title='网络' if changed else '硬件', summary='理解生活里的计算机概念')])
        elif purpose == 'sufficiency':
            self.round += 1
            value = dict(enough=self.round >= 6, questions=[] if self.round >= 6 else [f'第{self.round}轮：最想理解什么？'],
                         message='明白了，我们再确认一点。',options=[] if self.round >= 6 else ['交流时能解释','工作中能听懂'],
                         goal='理解计算机术语用于交流',focus=['计算机术语'])
        elif purpose == 'plan_intent':
            payload=json.loads(messages[-1]['content'].split('\n',1)[1])
            value=dict(intents=[{'kind':'answer'} if payload['ctx']['phase']=='questions' else self.intent],reply='')
        else:
            raise AssertionError('This dialogue must not search or generate: '+purpose)
        return SimpleNamespace(text=json.dumps(value,ensure_ascii=False))


@pytest.fixture
def conversation(tmp_path):
    store=CourseStore('plan42_synthetic',tmp_path)
    model=ConversationModel()
    services=SimpleNamespace(store=store,gateway=model,account_id='plan42_synthetic',data_root=tmp_path)
    flow=PlanFlow(services,lambda *_:None,lambda:date(2026,10,3))
    searches=[]
    flow._research=lambda d,c:searches.append(flow._effective_form(d))
    ctx=CallContext('plan42',threading.Event(),lambda _:None)
    pid=flow.current()['plan_id']
    yield flow,model,ctx,pid,searches
    store.close()


def say(flow,ctx,pid,text):
    return flow.run(pid,lambda d,c:flow.chat(d,c,text),ctx)['draft']


def test_plan42_six_rounds_preview_reopen_and_explicit_consent(conversation):
    flow,model,ctx,pid,searches=conversation
    d=flow.run(pid,lambda d,c:flow.submit_form(d,c,form()),ctx)['draft']
    assert d['phase']=='questions' and d['messages'][-1]['options']
    for n in range(5):
        d=say(flow,ctx,pid,f'回答第{n+1}轮，希望生活化讲清楚。')
        assert not searches
    assert model.round==6 and d['phase']=='preview'
    assert d['preview']['modules'][0]['title']=='硬件'
    flow2=PlanFlow(flow.services,lambda *_:None,flow.study_day)
    assert flow2.load(pid)['preview']==d['preview']
    with pytest.raises(BridgeError):flow.make_outline(d,ctx)
    d=say(flow,ctx,pid,'确认并找资料')
    assert d['phase']=='sources' and len(searches)==1
    assert '生活化例子' in searches[0]['notes'] and '不做操作' in searches[0]['notes']


def test_plan42_direct_start_does_not_force_more_questions_or_search(conversation):
    flow,model,ctx,pid,searches=conversation
    flow.run(pid,lambda d,c:flow.submit_form(d,c,form()),ctx)
    before=model.round
    d=say(flow,ctx,pid,'就这样吧')
    assert model.round==before and d['phase']=='preview' and not searches
    say(flow,ctx,pid,'直接开始')
    assert len(searches)==1


@pytest.mark.parametrize('phase',['preview','sources','outline'])
def test_plan42_supplement_invalidates_derived_work_and_repreviews(conversation,phase):
    flow,model,ctx,pid,searches=conversation
    model.round=5
    flow.run(pid,lambda d,c:flow.submit_form(d,c,form()),ctx)
    if phase!='preview':say(flow,ctx,pid,'确认并找资料')
    d=flow.load(pid);d['phase']=phase;d['outline']={'stages':[], 'ranges':{},'agent':{}}
    d['estimate']={'amount':'obsolete'};d['coverage']={'covered':True};flow.save(d)
    count=len(searches)
    d=say(flow,ctx,pid,'可以，但不要硬件，我想增加网络，只理解术语，不写代码')
    assert d['phase']=='preview' and d['preview']['version']==2
    assert d['outline'] is None and 'estimate' not in d and 'coverage' not in d
    assert d['preview']['modules'][0]['title']=='网络' and len(searches)==count
    say(flow,ctx,pid,'确认并找资料')
    assert '只理解术语，不写代码，增加网络' in searches[-1]['notes']
    assert '只理解术语' in flow.services.store.get_plan(pid)['notes']
    request=agents.coverage_request(d['goal'],flow._effective_form(d),[],[])
    assert '只理解术语，不写代码，增加网络' in request[1][0]['content']


def test_plan42_preview_cancel_failure_does_not_leave_old_map(conversation):
    flow,model,ctx,pid,_=conversation
    model.round=5
    flow.run(pid,lambda d,c:flow.submit_form(d,c,form()),ctx)
    def broken(*args,**kwargs):raise agents.AgentFormatError('invalid fixture')
    original=agents.parse_preview;agents.parse_preview=broken
    try:
        d=flow.load(pid)
        with pytest.raises(Exception):flow._show_preview(d,ctx)
        saved=flow.load(pid)
        assert saved['phase']=='questions' and not saved.get('preview') and not saved.get('outline')
    finally:agents.parse_preview=original


def test_plan42_summary_survives_draft_deletion_and_store_reopen(fixture):
    flow,_,ctx,_,_=fixture
    d=adopt_and_map(fixture);pid=d['plan_id']
    prepare=flow._prepare_module
    def contract(store,pid,mid,ctx,progress):
        prepare(store,pid,mid,ctx,progress)
        return dict(points=1,cloze_points=1,cloze_heavy=True)
    flow._prepare_module=contract
    flow.estimate(d)
    flow.run(pid,lambda d,c:flow.generate(d,c),ctx)
    assert flow.detail(pid)['stages'][0]['modules'][0]['cloze_heavy']
    flow.start_request(pid)
    assert not flow.drafts()
    assert flow.detail(pid)['stages'][0]['modules'][0]['cloze_heavy']
    reopened=CourseStore(flow.services.account_id,flow.services.data_root)
    try:
        assert reopened.records('modules',pid)[0]['material_preparation']['cloze_heavy']
    finally:reopened.close()
    assert any(r.payload.get('material_preparation',{}).get('cloze_heavy')
               for r in flow.services.store.pending_records(10000) if r.entity=='modules')


def test_plan42_preview_and_intent_strict_contract():
    with pytest.raises(agents.AgentFormatError):
        agents.parse_preview(json.dumps(dict(summary='s',modules=[dict(title='m',summary='s',points=['invented'])])))
    ctx=dict(phase='preview',today='2026-10-03',form=form(),recent=[],cards=[],sections=[],modules=[],facts={})
    intents=plan_intent.parse_intents('{"intents":[{"kind":"proceed","step":"sources"}],"reply":""}',ctx)
    assert intents[0].params['step']=='sources'
    ctx['phase']='ready'
    with pytest.raises(agents.AgentFormatError):
        plan_intent.parse_intents('{"intents":[{"kind":"supplement","instruction":"改范围"}],"reply":""}',ctx)


def test_plan42_consent_cannot_transfer_to_revised_preview(conversation):
    flow,model,ctx,pid,searches=conversation
    model.round=5
    flow.run(pid,lambda d,c:flow.submit_form(d,c,form()),ctx)
    d=flow.load(pid);old_context=flow._intent_context(d)
    flow._carry_out(d,ctx,plan_intent.Intent('supplement',{'instruction':'增加网络'}),old_context)
    flow._carry_out(d,ctx,plan_intent.Intent('proceed',{'step':'sources'}),old_context)
    assert d['phase']=='preview' and not searches
    assert '确认' in d['messages'][-1]['text']
