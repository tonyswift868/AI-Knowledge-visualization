"""V2 creation: HTTP recommendations are never adopted material."""
import io
import json
import threading
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from pypdf import PdfReader, PdfWriter

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.app.bridge import CallContext, BridgeError
from refactor.learning.course import planner_agents as agents, plan_intent
from refactor.learning.course.extract import extract_bytes
from refactor.learning.course.source_filter import load_lists
from refactor.learning.course.store import CourseStore
from tests.refactor.integration38.test_offline_integrated38_flow import IntegratedModel, fetch, form, URL, QUOTE


@pytest.fixture
def flow(tmp_path):
    model = IntegratedModel()
    services = SimpleNamespace(store=CourseStore('plan51', tmp_path), gateway=model,
        account_id='plan51', data_root=tmp_path, catalog=SimpleNamespace(load_lists=load_lists))
    f = PlanFlow(services, lambda *a: None, lambda: date(2026, 10, 3), fetcher=fetch)
    c = CallContext('p51', threading.Event(), lambda *a: None)
    pid = f.current()['plan_id']
    f.run(pid, lambda d,c:f.submit_form(d,c,form()),c)
    f.run(pid, lambda d,c:f.chat(d,c,'确认并找资料'),c)
    yield f,c,pid,model
    services.store.close()


def test_plan51_recommendations_never_extract_or_adopt_and_more_calls_once(flow, monkeypatch):
    f,c,pid,model=flow
    draft=f.load(pid)
    assert draft['recommendations'] and not draft['sources']
    assert not f.services.store.records('source_chunks',pid)
    requests=[r for r in model.requests if r['purpose']=='research']
    assert len(requests)==1 and '默认3～5份' in requests[0]['system']
    monkeypatch.setattr('refactor.learning.course.materials.import_verified_source', lambda *a:pytest.fail('recommendation extraction'))
    f.run(pid,lambda d,c:f.more_sources(d,c,'多推荐几份'),c)
    assert model.calls.count('research')==2
    assert not {'coverage_check','source_scoring','research_follow'} & set(model.calls)


def test_plan51_http_reachability_redirect_removal_404_without_extract(flow,monkeypatch):
    f,c,pid,_=flow
    def reply(request):
        if request.url.path=='/redirect':return httpx.Response(302,headers={'Location':'https://example.org/book'})
        if request.url.path=='/gone':return httpx.Response(200,headers={'content-type':'text/html'},text='This book has been removed')
        if request.url.path=='/404':return httpx.Response(404)
        return httpx.Response(200,headers={'content-type':'application/pdf'},content=b'not an extracted book')
    monkeypatch.setattr('refactor.learning.course.research.ResearchService._client',lambda _:httpx.Client(transport=httpx.MockTransport(reply)))
    assert f._link_available('https://example.org/redirect',c.cancel)==(True,'https://example.org/book','')
    assert not f._link_available('https://example.org/gone',c.cancel)[0]
    assert not f._link_available('https://example.org/404',c.cancel)[0]
    assert f.services.store.records('sources',pid)==[]


def test_plan51_unavailable_recommendation_not_displayed(flow):
    f,c,pid,_=flow
    f.fetcher=lambda *a,**k:(_ for _ in ()).throw(OSError('unavailable'))
    before=len(f.load(pid)['recommendations'])
    f.run(pid,lambda d,c:f.more_sources(d,c),c)
    assert len(f.load(pid)['recommendations'])==before
    assert '暂时打不开' in f.load(pid)['messages'][-1]['text']


def test_plan51_failed_user_link_does_not_claim_material_was_read(flow):
    f,c,pid,_=flow
    f.fetcher=lambda *a,**k:(_ for _ in ()).throw(OSError('unavailable'))
    f.run(pid,lambda d,c:f._import_links(d,c,[URL]),c)
    draft=f.load(pid)
    assert not draft['sources'] and not f.services.store.records('sources',pid)
    assert draft['messages'][-1]['kind']=='error'
    assert '未能导入' in draft['messages'][-1]['text']


@pytest.mark.parametrize('missing_action',['add','delete','continue'])
def test_plan51_directory_preview_is_separate_confirmation_missing_choices(flow, missing_action):
    f,c,pid,model=flow
    f.run(pid,lambda d,c:f.chat(d,c,URL),c)
    d=f.run(pid,lambda d,c:f.make_outline(d,c),c)['draft']
    assert d['phase']=='material_preview' and d['outline'] is None
    sources,sections=f._sections(f.load(pid))
    assert d['material_structure']['modules'][0]['chapters']==[sections[0].title]
    draft=f.load(pid);draft['material_structure']['missing']=['未讲到的目标'];f.save(draft)
    result=f.run(pid,lambda d,c:f.resolve_missing(d,c,missing_action),c)['draft']
    if missing_action=='add': assert result['phase']=='sources' and result['outline'] is None
    else:
        assert result['phase']=='outline' and result['outline']['ranges']['learning']==45
        assert bool(result['material_structure']['missing'])==(missing_action=='continue')
    assert 'coverage_check' not in model.calls


def test_plan51_local_upload_reopen_and_same_url_import_used_for_creation_and_addition(flow,tmp_path):
    f,c,pid,model=flow
    path=tmp_path/'material.md';path.write_text('# 队列与栈\n\n'+QUOTE*30,encoding='utf-8')
    f.add_file(pid,path)
    reopened=PlanFlow(f.services,f.push,f.study_day,fetcher=fetch)
    d=reopened.load(pid)
    assert d['sources'][0]['kind']=='file' and d['sources'][0]['status']=='adopted'
    assert f.services.store.get_original(f.services.store.records('sources',pid)[0]['sha256'])==path.read_bytes()
    assert not any('original' in r.payload for r in f.services.store.pending_records(10000))
    reopened.run(pid,lambda d,c:reopened.chat(d,c,URL),c)
    assert len(reopened.load(pid)['sources'])==2


def test_plan51_chapter_names_exact_and_map_cannot_change_structure():
    section=agents.Section('s1.1','src','教材','第一章 / 方程',('c',),80,0,0)
    value=dict(summary='调整',modules=[dict(key='m1',title='方程',summary='学解法',refs=['s1.1'],chapters=['编的章节'])],missing=[])
    with pytest.raises(agents.AgentFormatError):agents.parse_material_structure(json.dumps(value),[section])
    value['modules'][0]['chapters']=[section.title]
    structure=agents.parse_material_structure(json.dumps(value),[section])
    with pytest.raises(agents.AgentFormatError):agents.parse_material_map(json.dumps({'stages':[]}),[section],[],[],structure)


def test_plan51_cross_material_point_dedup_merges_citations():
    sections=[agents.Section(f's{i}.1',f'src{i}',f'资料{i}','章节',(f'c{i}',),80,0,0) for i in (1,2)]
    sources=[dict(id=f'src{i}',title=f'资料{i}',url=None) for i in (1,2)]
    chunks=[dict(id=f'c{i}',source_id=f'src{i}',text=QUOTE,section=['章节'],page_start=None,page_end=None) for i in (1,2)]
    modules=[dict(key=f'm{i}',title=f'模块{i}',summary='学习',refs=[f's{i}.1'],prerequisites=[],
        points=[dict(title='处理顺序',summary='理解顺序',citations=[dict(chunk_id=f'c{i}',quote=QUOTE)])]) for i in (1,2)]
    structure=dict(modules=modules)
    parsed=agents.parse_material_map(json.dumps(dict(stages=[dict(title='学习',modules=modules)],message=''),ensure_ascii=False),sections,sources,chunks,structure)
    points=[p for m in agents.outline_modules(parsed) for p in m['points']]
    assert len(points)==1 and len(points[0]['citations'])==2


@pytest.mark.parametrize('text,confirmed',[
    ('确认，另外重点讲让步段和结尾。',True),
    ('这个模块预览可以，先按这个方向找资料吧。我想重点先看主体段论证推进、论据与解释的匹配、段落之间的逻辑衔接这几块。',True),
    ('另外重点讲让步段和结尾。',False),('不要确认，先改一下。',False)])
def test_plan51_explicit_confirmation_and_supplement(flow,text,confirmed):
    f,c,pid,_=flow;d=f.load(pid);d['phase']='preview'
    ctx=f._intent_context(d)
    parsed=plan_intent.parse_intents(json.dumps(dict(intents=[dict(kind='supplement',instruction=text)],reply=''),ensure_ascii=False),ctx,user_text=text)
    assert any(i.kind=='proceed' for i in parsed)==confirmed


def test_plan51_pdf_bookmark_directory_uses_pypdf(tmp_path):
    from tests.refactor.extract.test_extract import pdf_bytes
    reader=PdfReader(io.BytesIO(pdf_bytes(['Readable course material. '*6]*2)));writer=PdfWriter()
    for page in reader.pages:writer.add_page(page)
    first=writer.add_outline_item('Chapter One',0);writer.add_outline_item('Topic A',0,parent=first)
    writer.add_outline_item('Chapter Two',1)
    output=io.BytesIO();writer.write(output)
    doc=extract_bytes(output.getvalue(),'book.pdf')
    assert doc.blocks[0].section==('Chapter One','Topic A')
    assert doc.blocks[-1].section==('Chapter Two',)


def test_plan51_text_book_above_ocr_page_limit_imports_without_recognition(flow,tmp_path,monkeypatch):
    from tests.refactor.extract.test_extract import pdf_bytes
    f,c,pid,_=flow
    path=tmp_path/'textbook.pdf'
    path.write_bytes(pdf_bytes(['Readable material for study.']*301))
    monkeypatch.setattr('refactor.learning.course.ocr.PdfOCR.check_page_count',
        lambda *a:pytest.fail('text PDF should not start OCR'))
    f.add_file(pid,path)
    assert f.load(pid)['sources'][0]['status']=='adopted'
    assert max(c['page_end'] for c in f.services.store.records('source_chunks',pid))==301


def test_plan51_scan_fallback_only_for_scan_error(flow,tmp_path,monkeypatch):
    from refactor.learning.course.extract import ExtractError
    f,c,pid,_=flow
    path=tmp_path/'scan.pdf';path.write_bytes(b'synthetic scan')
    calls=[]
    def scanning(store,plan,path,**kwargs):
        calls.append(kwargs['ocr'])
        raise ExtractError('SCANNED_NO_TEXT' if not kwargs['ocr'] else 'CANCELLED')
    monkeypatch.setattr('refactor.learning.course.materials.import_file',scanning)
    with pytest.raises(ExtractError,match='取消'):f.add_file(pid,path)
    assert calls==[False,True]


def test_plan51_material_map_output_budget_covers_high_thinking(flow,monkeypatch):
    f,c,pid,model=flow
    original=model.complete;limits=[]
    def complete(purpose,messages,**kwargs):
        if kwargs.get('system')==agents.MAP_SYSTEM:
            limits.append(kwargs['max_tokens'])
            assert callable(kwargs.get('on_text'))
        return original(purpose,messages,**kwargs)
    monkeypatch.setattr(model,'complete',complete)
    f.run(pid,lambda d,c:f.chat(d,c,URL),c)
    f.run(pid,lambda d,c:f.make_outline(d,c),c)
    f.run(pid,lambda d,c:f.resolve_missing(d,c,'continue'),c)
    assert limits==[32768]


def test_plan51_directory_keeps_one_chapter_when_body_exceeds_legacy_batch_size(flow,tmp_path):
    f,c,pid,_=flow
    path=tmp_path/'long-chapter.md';path.write_text('# 一个完整章节\n\n'+(QUOTE+'\n\n')*500,encoding='utf-8')
    f.add_file(pid,path)
    _,sections=f._sections(f.load(pid))
    assert len(sections)==1 and sections[0].title=='一个完整章节'
    assert sections[0].chars>9000
