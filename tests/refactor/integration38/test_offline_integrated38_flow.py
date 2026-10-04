"""Batch 38: fake model/search, real pipeline, materials, database and classroom."""
import json
import re
import socket
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from refactor.learning.app.bridge import CallContext, BridgeError
from refactor.learning.app.plan_flow import PlanFlow, report_markdown
from refactor.learning.app.classroom import Classroom, register
from refactor.learning.course import point_materials
from refactor.learning.course.research import FetchedContent
from refactor.learning.course.source_filter import load_lists
from refactor.learning.course.store import CourseStore
from refactor.learning.course.report_views import report_views
from refactor.learning.course.report_pdf import render_report_pdf
from tests.refactor.integration38.support import Agents, block, join_workers

DAY = date(2026, 10, 3)
# Events are stamped by the classroom clock; pin it to DAY so the report date check does not depend on the wall clock.
NOW = lambda: datetime(2026, 10, 3, 10, tzinfo=timezone.utc)
URL = 'https://docs.python.org/3/tutorial/datastructures.html'
QUOTE = '队列按先进先出的顺序处理元素，栈按后进先出的顺序处理元素。'
STEMS = ['队列按哪种顺序处理元素？', '队列的处理顺序是什么？', '请选择资料中队列的处理顺序。']
BODY = ('<html><head><title>Python 数据结构</title></head><body><h1>队列与栈</h1>'
    + ''.join('<p>'+QUOTE+'</p>' for _ in range(50))
    + ''.join(f'<p>{stem} A. 先进先出 B. 后进先出 C. 散列次序 答案：先进先出</p>' for stem in STEMS)
    + '<h1>其他数据结构</h1><p>哈希表按散列次序处理元素。栈按后进先出的顺序处理元素。其他数据结构有各自的存储和查找方法。</p>'
    + '</body></html>').encode()


class IntegratedModel(Agents):
    def complete(self, purpose, messages, **kw):
        if purpose in ('teacher','judge','profile','check_profile'):
            return super().complete(purpose,messages,**kw)
        self.calls.append(purpose)
        self.requests.append(dict(purpose=purpose,system=kw.get('system'),messages=messages))
        if purpose == 'sufficiency':
            from refactor.learning.course.planner_agents import PREVIEW_SYSTEM
            value = (dict(summary='理解队列与栈',modules=[dict(title='数据结构',summary='理解处理顺序')])
                if kw.get('system')==PREVIEW_SYSTEM else dict(enough=True,goal='队列与栈',focus=['顺序'],questions=[]))
        elif purpose == 'research':
            value=dict(sources=[dict(url=URL,title='Python 数据结构',why='官方文档',type='文档',modules=['数据结构'])])
        elif purpose == 'source_scoring':
            value=dict(scores=[dict(id=i,relevance=5,credibility=5,note='官方教程的数据结构原文')
                for i in re.findall(r'\[(r\d+)\]',messages[-1]['content'])])
        elif purpose == 'coverage_check': value=dict(covered=True,missing=[],message='')
        elif purpose == 'source_identity':
            # A file has no official URL; the fake model reads the fixture's
            # explicit publisher text instead of bypassing real classification.
            packet=json.loads(messages[-1]['content'])
            marker='本合成教材由测试教育平台编写发布。'
            value=(dict(type='education_platform',publisher='测试教育平台',quote=marker)
                if marker in packet['text'] else dict(type='unknown',publisher='',quote=''))
        elif purpose == 'outline' and kw.get('system') == __import__('refactor.learning.course.planner_agents',fromlist=['MATERIAL_STRUCTURE_SYSTEM']).MATERIAL_STRUCTURE_SYSTEM:
            packet=json.loads(messages[-1]['content']); section=packet['directory'][0]
            value=dict(summary='按资料目录理解处理顺序',modules=[dict(key=f'm{i}',title=title,summary='理解处理顺序',
                refs=[section['id']],chapters=[section['title']]) for i,title in enumerate(('队列基础','顺序比较','队列应用'))],missing=[])
        elif purpose == 'outline':
            chunks=block(messages,'【已取得的原文段落】')
            cid=next(c['chunk_id'] for c in chunks if QUOTE in c['text'])
            value=dict(stages=[dict(title='数据结构',modules=[dict(key=f'm{i}',title=title,summary='理解处理顺序',
                refs=['s1.1'],prerequisites=[],points=[dict(title=title,summary='理解处理顺序',
                citations=[dict(chunk_id=cid,quote=QUOTE)])]) for i,title in enumerate(('队列基础','顺序比较','队列应用'))])],message='从原文理解顺序。')
        elif purpose == 'point_materials':
            packet=json.loads(messages[-1]['content']);passages=packet['passages']
            found=[]
            for stem in STEMS:
                p=next(p for p in passages if stem in p['text'])
                found.append(dict(kind='choice',stem=stem,options=['先进先出','后进先出','散列次序'],answer=[0],
                    explanation='',question_chunk=p['id'],answer_chunk=p['id'],answer_quote='先进先出',
                    pairing='same_block',question_number='',answer_number=''))
            p=next(p for p in passages if QUOTE in p['text'])
            contrast=next(v for v in passages if '哈希表按散列次序' in v['text'])
            value=dict(passages=[p['id'] for p in passages],found=found,cloze=[
                # Keep the shared fixture's documented quick answer (队列).
                # Here the queried object is a processing order and its value
                # is the structure using it; contrast other orders/structures.
                dict(target='哪种结构按先进先出的顺序处理元素？',subject='先进先出',chunk_id=p['id'],sentence=QUOTE,word='队列',importance='key',contrasts=[
                    dict(chunk_id=contrast['id'],contrast_quote='栈按后进先出的顺序处理元素。',contrast_subject='后进先出',contrast_value='栈'),
                    dict(chunk_id=contrast['id'],contrast_quote='哈希表按散列次序处理元素。',contrast_subject='散列次序',contrast_value='哈希表')])])
        elif purpose == 'distractor_check':
            packet=json.loads(messages[-1]['content'])
            value=dict(questions=[dict(id=q['id'],checks=[dict(index=i,verdict='not_supported',quote='')
                for i in range(len(q['options'])) if i not in q['answer']]) for q in packet['questions']])
        else: raise AssertionError('unexpected purpose '+purpose)
        return SimpleNamespace(text=json.dumps(value,ensure_ascii=False),search_hits=(),citations=())


def fetch(url,**kwargs):
    # All discovery, including directory candidates, is a fixed offline web.
    return FetchedContent(url,URL,'text/html',BODY,'2026-10-03T00:00:00Z')


def form():
    return dict(goal='队列与栈',final_goal='区分处理順序',level='beginner',level_note='',notes='',
        start=DAY.isoformat(),end=(DAY+timedelta(days=7)).isoformat(),daily_cap=60,
        weekday_weights=[1]*7,special_days=[],only_mine=False)


def build_plan(root, events):
    store=CourseStore('integration38',root);model=IntegratedModel()
    services=SimpleNamespace(store=store,gateway=model,account_id='integration38',data_root=root,
        catalog=SimpleNamespace(load_lists=load_lists),_closed=False,
        settings=SimpleNamespace(load=lambda:dict(teacher_style='warm',detail='brief',examples='few',analogy_domains=['life'],refresh_time='04:00')))
    flow=PlanFlow(services,lambda kind,data:events.append((kind,data)),lambda:DAY,fetcher=fetch)
    ctx=CallContext('offline38',threading.Event(),lambda _:None)
    pid=flow.current()['plan_id']
    def run(fn):
        result=flow.run(pid,fn,ctx);assert result['draft']['busy'] is None
        return result['draft']
    d=run(lambda d,c:flow.submit_form(d,c,form()));assert d['phase']=='preview'
    assert 'research' not in model.calls
    d=run(lambda d,c:flow.chat(d,c,'确认并找资料'));assert d['phase']=='sources'
    d=run(lambda d,c:flow.chat(d,c,URL))
    d=run(lambda d,c:flow.make_outline(d,c));assert d['phase']=='material_preview'
    d=run(lambda d,c:flow.chat(d,c,'确认结构'));assert d['outline']['grounded']
    assert all(p['citations'] for s in d['outline']['stages'] for m in s['modules'] for p in m['points'])
    d=run(lambda d,c:flow.estimate(d));estimate=d['outline']['ranges']['total'][1]
    d=run(lambda d,c:flow.generate(d,c));assert d['phase']=='ready'
    join_workers()
    assert store.get_plan(pid)['total_minutes']==estimate
    bank=store.records('question_bank',pid)
    assert len(bank)==9 and sum(q.get('role')=='bank' for q in bank)==6
    assert all(q['origin']=='found' and q['answer_source']['url']==URL for q in bank if q.get('role')=='bank')
    assert any(k=='plan_preparation' for k,_ in events)
    assert 'generate' not in model.calls
    flow.start_request(pid)
    return services,flow,model,pid


def test_new_plan_through_report_pdf_and_free_check(tmp_path,monkeypatch,qapp):
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('offline test attempted network'))
    events=[];services=None
    try:
        services,flow,model,pid=build_plan(tmp_path,events)
        class Registry:
            methods={}
            def register(self,name,fn):self.methods[name]=fn
            def push(self,kind,data):events.append((kind,data))
        registry=Registry();register(registry,lambda:services,lambda:DAY,now=NOW)
        ctx=CallContext('class38',threading.Event(),lambda _:None)
        def act(action,**params):
            v=registry.methods['classroom.'+action](dict(plan_id=pid,**params),ctx)
            assert not v.get('failure'),v.get('failure')
            return v
        v=act('enter');assert v['step']=='teach'
        v=act('next');assert v['step']=='opening' and v['messages'][-1]['sources']
        v=act('choose',option=0);assert v['step']=='teaching'
        v=act('confused');assert v['messages'][-1]['sources']
        v=act('ask',text='我想问顺序。');assert v['conversation']['interrupted']
        act('back');v=act('next');assert v['question']['purpose']=='quick'
        v=act('answer',value=[1]);assert v['explanation']['open'] and not v['question'] and v['step']=='closing'
        v=act('why');assert v['explanation']['open']
        # Reopening retains the finished verdict/position, no duplicate grade.
        before=len(services.store.records('judgements',pid));v=act('state')
        assert len(services.store.records('judgements',pid))==before
        v=act('next');assert v['step']=='test_offer'
        v=act('test',kind='module_test');v=act('answer',value=[1]);assert v['module']['state']=='failed'
        v=act('test',kind='retest');v=act('answer',value=[0]);assert v['module']['state']=='passed'
        v=act('next_module');assert v['can_skip']
        v=act('test',kind='skip_test');v=act('answer',value=[0]);assert v['module']['state']=='skipped'
        act('next_module');act('next');act('choose',option=0)
        known=act('reply',text='我会了');assert known['question']['purpose']=='quick'
        quick=next(q for q in services.store.records('question_bank',pid) if q['id']==known['question']['id'])
        v=act('answer',value=quick['answer']);assert v['step']=='test_offer'
        act('test',kind='module_test');act('answer',value=[0])
        v=act('next_module');assert v['step']=='check_offer'
        v=act('check');steps=0
        while v['step']!='completed':
            steps+=1;assert steps<20
            # Answer each final-check question with its stored key: the offline
            # fixture's correct option is not always index 0 once cloze options are sorted.
            if v['question']:
                stored=next(q for q in services.store.records('question_bank',pid) if q['id']==v['question']['id'])
                v=act('answer',value=stored['answer'])
            else:v=act('check')
        report=flow.report(pid)['report'];views=report_views(report)
        titles={p['id']:p['title'] for p in services.store.records('points',pid)}
        assert report['plan']['actual_completion_date'] and report['point_comments']
        assert report_markdown(report,titles).startswith('# 学习报告')
        target=tmp_path/'report.pdf';render_report_pdf(report,titles,views,target)
        assert target.read_bytes().startswith(b'%PDF-')
        frozen=(services.store.load_mastery(pid).to_dict(),services.store.load_report(pid))
        v=act('practice',count=2);v=act('answer',value=[1]);v=act('answer',value=[0])
        assert v['step']=='completed'
        assert frozen==(services.store.load_mastery(pid).to_dict(),services.store.load_report(pid))
        assert len(services.store.records('practice_results',pid))==2
        uses={u['purpose'] for q in services.store.records('question_bank',pid) for u in q['uses']}
        assert uses=={'quick','module','direct','retest','final','free'}
    finally:
        join_workers()
        if services:services._closed=True;services.store.close()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_old_plan_opens_with_clear_unusable_state(tmp_path):
    from refactor.learning.course.ledger import PlanWindow
    store=CourseStore('old38',tmp_path);pid='a'*32
    try:
        store.create_plan(pid,goal='旧计划',window=PlanWindow(DAY,DAY+timedelta(days=1),60))
        # Preserve a real old persisted record, not an invalid new-plan fixture.
        row=store.get_plan(pid);row['material_version']=1
        with store.transaction():store._put('plans',pid,row)
        services=SimpleNamespace(store=store)
        detail=PlanFlow(services,lambda *a:None,lambda:DAY).detail(pid)
        assert detail['unusable'] and detail['message']=='旧版计划，已不可用'
        with pytest.raises(BridgeError,match='旧版计划，已不可用'):
            Classroom(services,lambda:DAY,now=NOW)._course(pid)
    finally:store.close()

def test_real_materials_wait_resumes_once_with_mapping_context(tmp_path):
    import time
    services,flow,model,pid=build_plan(tmp_path,[])
    room=Classroom(services,lambda:DAY,now=NOW)
    started,release=threading.Event(),threading.Event()
    events=[];room.material_event=events.append
    original=model.complete
    try:
        mid=services.store.records('modules',pid)[0]['id']
        # Only synthetic test rows: make the module cold, as on a new device.
        with services.store.transaction():
            for q in services.store.records('question_bank',pid):
                if q['module_id']==mid:
                    services.store._db.execute('DELETE FROM question_bank WHERE plan_key=? AND id=?',(pid,q['id']))
        def complete(purpose,*args,**kwargs):
            if purpose=='point_materials':
                started.set();assert release.wait(5)
            return original(purpose,*args,**kwargs)
        model.complete=complete
        ctx=CallContext('cold38',threading.Event(),lambda _:None)
        before=model.calls.count('point_materials')
        then=time.monotonic();room.perform(pid,'enter',ctx)
        assert started.wait(1)
        v=room.perform(pid,'next',ctx)
        assert v['step']=='preparing_materials' and time.monotonic()-then<1
        room.perform(pid,'next',ctx)  # Waiting never prepares the same point again.
        release.set();join_workers()
        v=room.run(pid,lambda *args:None,ctx)
        assert v['step']=='opening' and not v['failure'] and v['messages'][-1]['sources']
        assert model.calls.count('point_materials')==before+1 and events
        fresh=Classroom(services,lambda:DAY,now=NOW)
        assert fresh.run(pid,lambda *args:None,ctx)['step']=='opening'
    finally:
        release.set();room.cancel_materials();join_workers();services._closed=True;services.store.close()


def test_integration44_real_roles_and_uses_survive_store_reopen(tmp_path):
    """No autouse material boundary: select/mark/prepare are real and durable."""
    services,flow,model,pid=build_plan(tmp_path,[])
    try:
        point=services.store.records('points',pid)[0]
        before=len(model.calls)
        point_materials.prepare_point(services.store,pid,point,dict(gateway=model,cancel=threading.Event()))
        assert len(model.calls)==before  # Stored complete rows avoid locating again.
        quick=point_materials.pick_question(services.store,pid,point['id'],'quick')
        bank=point_materials.pick_question(services.store,pid,point['id'],'module')
        assert quick['role']=='quick' and bank['role']=='bank' and quick['id']!=bank['id']
        point_materials.mark_used(services.store,pid,quick['id'],'quick')
        point_materials.mark_used(services.store,pid,bank['id'],'module')
    finally:
        join_workers();services._closed=True;services.store.close()
    fresh=CourseStore('integration38',tmp_path)
    try:
        rows={q['id']:q for q in fresh.records('question_bank',pid)}
        assert rows[quick['id']]['uses'][0]['purpose']=='quick'
        assert rows[bank['id']]['uses'][0]['purpose']=='module'
        assert point_materials.pick_question(fresh,pid,point['id'],'quick')['id']==quick['id']
        assert point_materials.pick_question(fresh,pid,point['id'],'module')['id']!=bank['id']
    finally:fresh.close()
