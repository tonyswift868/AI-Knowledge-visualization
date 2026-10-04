"""Offline replays of batch 45's restart/combined-consent failures."""
import json

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.app.bridge import BridgeError
from refactor.learning.course import planner_agents as agents
from tests.refactor.plan37.test_grounded_plan37_flow import fixture, form, URL


RUN1_FORM = dict(goal='我不太懂计算机，想把各种术语概念都搞明白，不用上手操作，要生活化的例子',
    final_goal='以后交流时听得懂并能用自己的话说出常见计算机术语', level='zero', level_note='',
    start='2026-10-04', end='2026-10-10', daily_cap=60, weekday_weights=[1]*7, notes='')
RUN1_DIALOGUE = [
    dict(role='ai', text='“常见计算机术语”范围挺广的，你更想先搞懂哪一类？'),
    dict(role='user', text='我平时就是听别人聊天、看视频时老听到这些词，比如内存、硬盘、系统、软件、网络这些，分不清。想先搞懂这些最常见的，不用太深。'),
    dict(role='ai', text='这些词里，你最想先分清哪一组？你希望学到什么程度就算“搞明白”了？'),
    dict(role='user', text='最想先分清内存、硬盘、CPU 这三个，老听人说但完全分不清。学到能听懂别人聊天、自己也能用大白话说个大概就行，不用太深。'),
]
RUN4_CONFIRM = '这个模块预览可以，先按这个方向找资料吧。我想重点先看主体段论证推进、论据与解释的匹配、段落之间的逻辑衔接这几块。'
SUPPLEMENT = '重点先看主体段论证推进、论据与解释的匹配、段落之间的逻辑衔接'


def searched(fixture):
    flow, _, ctx, *_ = fixture
    pid=flow.current()['plan_id']
    flow.run(pid, lambda d,c:flow.submit_form(d,c,form()),ctx)
    flow.run(pid, lambda d,c:flow.chat(d,c,'确认并找资料'),ctx)
    return pid


def reopened(flow, fetcher):
    return PlanFlow(flow.services, flow.push, flow.study_day, fetcher=fetcher)


def test_plan47_run1_restart_adopts_saved_text_without_fetch(fixture):
    flow,_,ctx,*_=fixture
    pid=searched(fixture)
    card=flow.load(pid)['sources'][0]
    assert flow.load(pid)['_verified_sources']
    assert flow.services.store.records('sources',pid)==[]
    assert not any(r.entity=='sources' for r in flow.services.store.pending_records(10000))
    expected=flow._verified[pid][card['url']].document.chunks[0].text
    def forbidden(*args,**kwargs):
        pytest.fail('saved verified text must not require network after restart')
    fresh=reopened(flow,forbidden)
    result=fresh.run(pid,lambda d,c:fresh.decide(d,card['card_id'],True),ctx)['draft']
    assert result['sources'][0]['status']=='adopted'
    chunks=flow.services.store.records('source_chunks',pid)
    assert chunks[0]['text']==expected
    assert '_verified_sources' not in result
    assert '_verified_sources' not in flow.public(flow.load(pid))
    assert not any(r.entity=='sources' for r in flow.services.store.pending_records(10000)
                   if r.payload.get('adopted') is False)


@pytest.mark.parametrize('valid',[True,False])
def test_plan47_legacy_card_refetches_and_rechecks(fixture,valid):
    flow,_,ctx,*_=fixture
    pid=searched(fixture)
    d=flow.load(pid);d.pop('_verified_sources',None)
    flow._verified.clear();flow.save(d)
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        if not valid:
            from refactor.learning.course.research import ResearchFetchError
            raise ResearchFetchError('offline missing page')
        return flow.fetcher(url,**kwargs)
    fresh=reopened(flow,fetch)
    card=d['sources'][0]
    if valid:
        result=fresh.run(pid,lambda d,c:fresh.decide(d,card['card_id'],True),ctx)['draft']
        assert result['sources'][0]['status']=='adopted'
        assert fresh.load(pid)['_verified_sources']
    else:
        with pytest.raises(BridgeError,match='这份资料需要重新查找'):
            fresh.run(pid,lambda d,c:fresh.decide(d,card['card_id'],True),ctx)
        assert fresh.load(pid)['sources'][0]['status']=='new'
        assert not flow.services.store.records('sources',pid)
    assert calls==[URL]


def test_plan47_verified_document_larger_than_settings_limit_reopens(fixture):
    flow,_,ctx,*_=fixture
    from tests.refactor.plan37.test_grounded_plan37_flow import QUOTE
    from refactor.learning.course.research import FetchedContent
    # Distinct paragraphs: 47-MF03's body cleanup drops repeated copies of the same text,
    # so a large document has to be large without repeating itself.
    paras=''.join(f'<p>{QUOTE} Example {i}: a load of {i} newtons on {i+1} square metres.</p>' for i in range(4000))
    body=('<html><title>Pressure OpenStax</title><h1>Pressure</h1>' + paras + '</html>').encode()
    flow.fetcher=lambda url,**kw:FetchedContent(url,url,'text/html',body,'2026-10-03T00:00:00Z')
    pid=searched(fixture)
    assert flow._file(pid).path.stat().st_size>262144
    fresh=reopened(flow,lambda *_args,**_kw:pytest.fail('should not fetch'))
    d=fresh.load(pid)
    fresh.run(pid,lambda d,c:fresh.decide(d,d['sources'][0]['card_id'],True,ctx=c),ctx)
    assert fresh.load(pid)['sources'][0]['status']=='adopted'


def test_plan47_legacy_changed_text_does_not_reuse_old_approval(fixture):
    flow,model,ctx,*_=fixture
    pid=searched(fixture)
    d=flow.load(pid);d.pop('_verified_sources',None);flow._verified.clear();flow.save(d)
    from refactor.learning.course.research import FetchedContent
    body=('<html><title>Pressure OpenStax</title><p>'+'其他课程的介绍，不再提供所选知识点的学习内容。'*100+'</p></html>').encode()
    fresh=reopened(flow,lambda url,**kw:FetchedContent(url,url,'text/html',body,'2026-10-03T00:00:00Z'))
    original=model.complete
    def complete(purpose,messages,**kw):
        if purpose=='source_scoring':
            from types import SimpleNamespace
            return SimpleNamespace(text=json.dumps(dict(scores=[dict(id='r0',credibility=5,relevance=1,note='重新获取的正文已不包含压强知识点，不能继续采用。')]),ensure_ascii=False))
        return original(purpose,messages,**kw)
    model.complete=complete
    with pytest.raises(BridgeError,match='这份资料需要重新查找'):
        fresh.run(pid,lambda d,c:fresh.decide(d,d['sources'][0]['card_id'],True,ctx=c),ctx)
    assert not flow.services.store.records('sources',pid)


@pytest.mark.parametrize('reverse',[False,True])
def test_plan47_run4_combined_confirmation_searches_only_new_preview(fixture,reverse):
    flow,model,ctx,*_=fixture
    pid=flow.current()['plan_id']
    flow.run(pid,lambda d,c:flow.submit_form(d,c,form()),ctx)
    searches=[]
    real_research=flow._research
    def research(d,c,*args,**kwargs):
        searches.append((d['phase'],dict(d['accepted_preview']),flow._effective_form(d)))
        return real_research(d,c,*args,**kwargs)
    flow._research=research
    original=model.complete
    def complete(purpose,messages,**kwargs):
        if purpose=='plan_intent':
            intents=[dict(kind='proceed',step='sources'),dict(kind='supplement',instruction=SUPPLEMENT)]
            if reverse:intents.reverse()
            from types import SimpleNamespace
            return SimpleNamespace(text=json.dumps(dict(intents=intents,reply=''),ensure_ascii=False))
        if kwargs.get('system')==agents.PREVIEW_SYSTEM:
            assert SUPPLEMENT in messages[0]['content']
        return original(purpose,messages,**kwargs)
    model.complete=complete
    result=flow.run(pid,lambda d,c:flow.chat(d,c,RUN4_CONFIRM),ctx)['draft']
    assert len(searches)==1
    assert searches[0][0]=='sources'
    assert searches[0][1]['version']==2
    assert SUPPLEMENT in searches[0][2]['notes']
    assert result['phase']=='sources' and result['accepted_preview']['version']==2
    kinds=[m['kind'] for m in result['messages']]
    assert max(i for i,k in enumerate(kinds) if k=='preview') < kinds.index('sources')
    assert '再确认' not in result['messages'][max(i for i,k in enumerate(kinds) if k=='preview')]['text']
    card=result['sources'][0]
    result=flow.run(pid,lambda d,c:flow.decide(d,card['card_id'],True),ctx)['draft']
    assert result['sources'][0]['status']=='adopted'


def test_plan47_invalid_decide_names_actual_preview_phase(fixture):
    flow,_,_,*_=fixture
    pid=searched(fixture);d=flow.load(pid);d['phase']='preview'
    with pytest.raises(BridgeError,match='确认大致模块'):
        flow.decide(d,d['sources'][0]['card_id'],True)


def test_plan47_run1_known_information_is_explicit_in_sufficiency_request():
    system,messages=agents.sufficiency_request(RUN1_FORM,RUN1_DIALOGUE)
    text=messages[0]['content']
    assert '【已经给出的信息，不再追问】' in text
    known=text.split('【已经给出的信息，不再追问】',1)[1].split('【完整对话】',1)[0]
    assert RUN1_FORM['final_goal'] in known and RUN1_DIALOGUE[-1]['text'] in known
    assert 'CPU' in known and '2026-10-04' in known and '60' in known
    assert '只追问真正缺失' in system
    assert '同一件事' in system and '达到什么程度' in system
