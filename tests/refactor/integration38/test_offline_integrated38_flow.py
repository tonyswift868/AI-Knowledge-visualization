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
MODULE_TITLES = ('队列基础', '顺序比较', '队列应用')
# Each synthetic module has its own original exercises. Previously this
# fixture reused the very same stems across modules, contrary to batch 66.
MODULE_STEMS = {title: [title+'：'+stem for stem in STEMS] for title in MODULE_TITLES}
BODY = ('<html><head><title>Python 数据结构</title></head><body>'
    + ''.join('<h1>'+title+'</h1><p>'+title+'：'+QUOTE+'</p>'
        + (''.join(f'<p>{i+1}. {stem}</p><p>A. 先进先出</p><p>B. 后进先出</p><p>C. 散列次序</p><p>答案：A</p>'
                   for i,stem in enumerate(MODULE_STEMS[title][:2])) + ('<h2>栈和队列有什么区别？</h2><p>栈遵循后进先出。队列遵循先进先出。删除元素的位置不同。</p>' if title=='顺序比较' else '') if title != '队列应用' else
           '<p>该模块只有说明，没有原资料题。队列可以用于排队任务。</p>')
        for title in MODULE_TITLES) + '</body></html>').encode()


class IntegratedModel(Agents):
    def complete(self, purpose, messages, **kw):
        if purpose in ('teacher','judge','profile','check_profile','point_links','point_difficulty'):
            return super().complete(purpose,messages,**kw)
        self.calls.append(purpose)
        self.requests.append(dict(purpose=purpose,system=kw.get('system'),messages=messages))
        from tests.refactor.wire121.model121 import directory_reply
        reply=directory_reply(purpose,messages,kw.get('system'))
        if reply is not None:return reply
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
                refs=[s['id'] for s in packet['directory'] if s['title']==title or title=='顺序比较' and s['title'].endswith(' / 栈和队列有什么区别？')],chapters=[title]) for i,title in enumerate(('队列基础','顺序比较','队列应用'))],missing=[])
        elif purpose == 'generate':
            title=re.search(r'【本模块】(.+?)：',messages[0]['content'])[1]
            alias=next(a for a,t in re.findall(r'\[(c\d+)\] [^\n]*\n(.*?)(?=\n\n\[c|\n\n【本次生成约束】)',messages[0]['content'],re.S) if QUOTE in t and title in t)
            aliases=[alias]
            if title=='顺序比较':
                aliases.extend(a for a,t in re.findall(r'\[(c\d+)\] [^\n]*\n(.*?)(?=\n\n\[c|\n\n【本次生成约束】)',messages[0]['content'],re.S) if '栈遵循后进先出。队列遵循先进先出。' in t)
            value=dict(points=[dict(title=title,summary='理解处理顺序',chunks=aliases,minutes=15,citations=[dict(chunk_id=alias,quote=QUOTE)])])
        elif purpose == 'outline':
            chunks=block(messages,'【已取得的原文段落】')
            cid=next(c['chunk_id'] for c in chunks if QUOTE in c['text'])
            value=dict(stages=[dict(title='数据结构',modules=[dict(key=f'm{i}',title=title,summary='理解处理顺序',
                refs=['s1.1'],prerequisites=[],points=[dict(title=title,summary='理解处理顺序',
                citations=[dict(chunk_id=cid,quote=QUOTE)])]) for i,title in enumerate(('队列基础','顺序比较','队列应用'))])],message='从原文理解顺序。')
        elif purpose == 'point_materials':
            # Locate only the program's already-cut original exercises.
            from tests.refactor.material_questions70.test_material_questions70_contract import raw70
            packet=json.loads(messages[-1]['content'])
            assert 'candidates' in packet and kw.get('enable_search') is False
            value=dict(questions=[raw70(candidate) for candidate in packet['candidates']])
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
    # Background preparation shares the fixture's pinned weekend clock, just
    # like the generated plan and classroom; the host weekday is irrelevant.
    flow._material_peak_now=lambda now=None:PlanFlow._material_peak_now(NOW())
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
    # This fixture's downstream scenarios assert the LEGACY opening/quick-test
    # contract. Keep a legacy course snapshot, explicitly, before persistence.
    # New concept creation and classroom fallback are exercised by wire121.
    legacy=flow.load(pid)
    for stage in legacy['outline']['stages']:
        for module in stage['modules']:
            for point in module['points']:
                for field in ('structure','subpoints','goal'):point.pop(field,None)
    flow.save(legacy)
    assert all(p['citations'] for s in d['outline']['stages'] for m in s['modules'] for p in m['points'])
    d=run(lambda d,c:flow.estimate(d));estimate=d['outline']['ranges']['learning']
    # The fixture has five original questions across three modules (2, 3, 0).
    # Module-test reserve is 10 minutes; final-check reserve rounds per module
    # to 5 + 8 + 0 = 13 minutes. Compare the total with the pinned source data.
    assert d['outline']['ranges']['total'][1] == estimate + 23
    d=run(lambda d,c:flow.generate(d,c));assert d['phase']=='ready'
    join_workers()
    # Prepared question associations refine the same content formula, rather than preserving the initial preview budget.
    learning=sum(p['minutes'] for p in store.records('points',pid))
    assert learning>=estimate
    assert store.get_plan(pid)['total_minutes']==learning+sum(m['check_minutes']+m['final_check_minutes'] for m in store.records('modules',pid))
    assert flow.public(flow.load(pid))['outline']['ranges']['total'][1] == store.get_plan(pid)['total_minutes']
    bank=store.records('question_bank',pid)
    assert len(bank)==5 and all(q.get('role')=='bank' for q in bank)
    assert sum(q['kind']=='recall' for q in bank)==1
    assert len({q['module_id'] for q in bank})==2  # Third module waits for entering the second.
    assert all(q['origin']=='found' and q['answer_source']['url']==URL for q in bank if q.get('role')=='bank')
    assert any(k=='plan_preparation' for k,_ in events)
    assert model.calls.count('generate') == 3
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
        act('back');v=act('reply',text='我以为队列是后进先出。')
        assert v['step']=='teaching' and not v['question']
        # Reopening retains ordinary dialogue, without a casual assessment.
        before=len(services.store.records('judgements',pid));v=act('state')
        assert len(services.store.records('judgements',pid))==before==0
        v=act('next');assert v['step']=='test_offer'
        v=act('test',kind='module_test');v=act('answer',value=[1]);v=act('answer',value=[0]);assert v['module']['state']=='failed'
        v=act('test',kind='retest');v=act('answer',value=[0]);assert v['module']['state']=='passed'
        v=act('next_module');assert v['can_skip']
        v=act('test',kind='skip_test')
        while v['question']:
            if v['question']['kind']=='recall':
                qid=v['question']['id'];v=registry.methods['class.recall_reveal'](dict(plan_id=pid,question_id=qid,answer='先进先出与后进先出'),ctx)
                assert v['reference'] and v['key_points']==[v['reference']]
                assert all(part in v['key_points'][0] for part in ('栈遵循后进先出。','队列遵循先进先出。','删除元素的位置不同。'))
                v=registry.methods['class.recall_submit'](dict(plan_id=pid,question_id=qid,
                    ticked=list(range(len(v['key_points']))),correct=True),ctx)
            else:v=act('answer',value=[0])
        assert v['module']['state']=='skipped'
        act('next_module');act('next');act('choose',option=0)
        v=act('reply',text='我会了');assert not v['question'] and v['step']=='check_offer'
        assert any('只学不考' in m.get('content','') for m in v['messages'])
        v=act('check');steps=0
        while v['step']!='completed':
            steps+=1;assert steps<20
            # Answer each final-check question with its stored key: the offline
            # fixture's correct option is not always index 0 once cloze options are sorted.
            if v['question']:
                stored=next(q for q in services.store.records('question_bank',pid) if q['id']==v['question']['id'])
                if stored['kind']=='recall':
                    registry.methods['class.recall_reveal'](dict(plan_id=pid,question_id=stored['id'],answer='自己的回答'),ctx)
                    v=registry.methods['class.recall_submit'](dict(plan_id=pid,question_id=stored['id'],ticked=list(range(len(stored['key_points']))),correct=True),ctx)
                else:v=act('answer',value=[0])
            else:v=act('check')
        report=flow.report(pid)['report'];views=report_views(report)
        titles={p['id']:p['title'] for p in services.store.records('points',pid)}
        assert report['plan']['actual_completion_date'] and report['point_comments']
        assert report_markdown(report,titles).startswith('# 学习报告')
        target=tmp_path/'report.pdf';render_report_pdf(report,titles,views,target)
        assert target.read_bytes().startswith(b'%PDF-')
        frozen=(services.store.load_mastery(pid).to_dict(),services.store.load_report(pid))
        v=act('practice',count=2)
        for correct in (False,True):
            if v['question']['kind']=='recall':
                qid=v['question']['id']
                v=registry.methods['class.recall_reveal'](dict(plan_id=pid,question_id=qid,answer='自己的回答'),ctx)
                v=registry.methods['class.recall_submit'](dict(plan_id=pid,question_id=qid,
                    ticked=list(range(len(v['key_points']))) if correct else [],correct=correct),ctx)
            else:v=act('answer',value=[0 if correct else 1])
        assert v['step']=='completed'
        assert frozen==(services.store.load_mastery(pid).to_dict(),services.store.load_report(pid))
        assert len(services.store.records('practice_results',pid))==2
        uses={u['purpose'] for q in services.store.records('question_bank',pid) for u in q['uses']}
        assert uses=={'module','direct','retest','final','free'}
    finally:
        join_workers()
        if services:services._closed=True;services.store.close()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_old_plan_detail_remains_readable_after_upgrade(tmp_path):
    from refactor.learning.course.ledger import PlanWindow
    store=CourseStore('old38',tmp_path);pid='a'*32
    try:
        store.create_plan(pid,goal='旧计划',window=PlanWindow(DAY,DAY+timedelta(days=1),60))
        # Preserve a real old persisted record, not an invalid new-plan fixture.
        row=store.get_plan(pid);row['material_version']=1
        with store.transaction():store._put('plans',pid,row)
        services=SimpleNamespace(store=store)
        detail=PlanFlow(services,lambda *a:None,lambda:DAY).detail(pid)
        assert not detail.get('unusable') and detail['plan']['id']==pid
        # This empty fixture was never started; its normal state error remains.
        with pytest.raises(BridgeError,match='这份计划还没开始'):
            Classroom(services,lambda:DAY,now=NOW)._course(pid)
        assert store.get_plan(pid)['material_version']==3
    finally:store.close()

def test_real_materials_wait_resumes_once_with_mapping_context(tmp_path):
    import time
    services,flow,model,pid=build_plan(tmp_path,[])
    started,release=threading.Event(),threading.Event()
    original=model.complete
    try:
        module=services.store.records('modules',pid)[0];mid=module['id']
        with services.store.transaction():
            module.pop('question_extraction',None)
            services.store._put('modules',pid,module)
            for q in services.store.records('question_bank',pid):
                if q['module_id']==mid:
                    services.store._db.execute('DELETE FROM question_bank WHERE plan_key=? AND id=?',(pid,q['id']))
        def complete(purpose,*args,**kwargs):
            if purpose=='point_materials':started.set();assert release.wait(5)
            return original(purpose,*args,**kwargs)
        model.complete=complete
        before=model.calls.count('point_materials');begin=time.monotonic()
        flow._prepare_remaining(pid,[module],point_materials.prepare_module)
        assert time.monotonic()-begin<1 and started.wait(1)
        flow._prepare_remaining(pid,[module],point_materials.prepare_module)
        release.set();join_workers()
        assert flow._module_ready(pid,mid)
        requests=[r for r in model.requests if r['purpose']=='point_materials'][before:]
        assert len(requests)==1
        assert len(json.loads(requests[0]['messages'][-1]['content'])['candidates'])==2
        assert PlanFlow(services,lambda *a:None,lambda:DAY)._module_ready(pid,mid)
    finally:
        release.set();join_workers();services._closed=True;services.store.close()


def test_integration44_real_roles_and_uses_survive_store_reopen(tmp_path):
    """Real module extraction, use persistence, reopen; no quick generation."""
    services,flow,model,pid=build_plan(tmp_path,[])
    try:
        first=services.store.records('modules',pid)[0]['id']
        point=next(p for p in services.store.records('points',pid) if p['module_id']==first)
        before=len(model.calls)
        point_materials.prepare_point(services.store,pid,point,dict(gateway=model,cancel=threading.Event()))
        assert len(model.calls)==before
        rows=[q for q in services.store.records('question_bank',pid) if q['module_id']==first]
        assert len(rows)==2 and all(q['role']=='bank' and q['kind']=='objective' for q in rows)
        assert not any(q.get('role')=='quick' for q in services.store.records('question_bank',pid))
        bank=rows[0]
        point_materials.mark_used(services.store,pid,bank['id'],'module')
        # The third source chapter contains no original exercises. Its completed
        # extraction is persisted without a model call or a fallback question.
        third=services.store.records('modules',pid)[2]['id']
        result=point_materials.prepare_module(services.store,pid,third,dict(gateway=model),lambda *a:None)
        assert result['questions']==0 and flow._module_ready(pid,third)
        assert len(model.calls)==before
    finally:
        join_workers();services._closed=True;services.store.close()
    with CourseStore('integration38',tmp_path) as fresh:
        rows={q['id']:q for q in fresh.records('question_bank',pid)}
        assert rows[bank['id']]['uses'][0]['purpose']=='module'
        assert point_materials.preparation_done(fresh,pid,first)
        assert point_materials.preparation_done(fresh,pid,third)
