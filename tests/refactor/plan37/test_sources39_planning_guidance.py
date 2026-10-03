from types import SimpleNamespace
from refactor.learning.course.research import ResearchUploadRequired, material_upload_guidance
from tests.refactor.plan37.test_grounded_plan37_flow import fixture, form, adopt_and_map

PAGE='https://basic.smartedu.cn/tchMaterial/detail?contentId=aec6de38-33d2-417a-bbdb-e39154a046a9'
BOOK='义务教育教科书·物理八年级下册'


def test_sources39_textbook_failure_and_catalog_guidance_reach_chat_without_fake_source(fixture):
    flow,model,ctx,*_=fixture
    message=material_upload_guidance(PAGE,BOOK)
    flow.services.catalog.material_guidance=lambda *a,**kw:(message,)
    ask=flow._ask
    def research(purpose,*args,**kwargs):
        if purpose=='research':return [dict(url=PAGE,title=BOOK,why='教材')],SimpleNamespace(search_hits=[],citations=[])
        return ask(purpose,*args,**kwargs)
    flow._ask=research
    def fetch(*a,**kw):raise ResearchUploadRequired(message)
    flow.fetcher=fetch
    draft=flow.current();pid=draft['plan_id']
    flow.run(pid,lambda d,c:flow.submit_form(d,c,form()),ctx)
    flow.run(pid,lambda d,c:flow.chat(d,c,'确认并找资料'),ctx)
    saved=flow.load(pid)
    texts=[m['text'] for m in saved['messages']]
    assert sum(t==message for t in texts)==1
    assert BOOK in message and PAGE in message and '下载' in message and '上传资料' in message
    assert saved['sources']==[]
    flow.run(pid,lambda d,c:flow.more_sources(d,c),ctx)
    assert sum(m['text']==message for m in flow.load(pid)['messages'])==1


def test_sources39_upload_invalidates_and_rechecks_coverage_on_current_flow(fixture,tmp_path):
    flow,model,ctx,*_=fixture
    draft=adopt_and_map(fixture);pid=draft['plan_id']
    draft['coverage']=dict(covered=False,blocked=True,message='缺原文');flow.save(draft)
    path=tmp_path/'physics39.md';path.write_text('# Pressure\n作者：物理教研组\nPressure is the force divided by the area.\n'*5,encoding='utf-8')
    before=model.calls.count('coverage_check')
    flow.add_file(pid,path)
    assert 'coverage' not in flow.load(pid)
    flow.run(pid,lambda d,c:flow.make_outline(d,c),ctx)
    assert model.calls.count('coverage_check')==before+1
    assert flow.load(pid)['coverage']['covered']
