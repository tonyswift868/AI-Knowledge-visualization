"""Batch47: offline lesson admission, module fallback and original-text hygiene."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import threading

import pytest

from refactor.learning.course import source_quality as q
from refactor.learning.course.source_filter import classify_source, load_lists, RawHit
from refactor.learning.course.source_follow import is_document_preview
from refactor.learning.course.extract import extract_html, clean_page_text
from refactor.learning.course.research_pipeline import ResearchPipeline, ResearchPool
from refactor.learning.course.research import FetchedContent
from refactor.learning.app.plan_flow import PlanFlow


def page(url, title='Python functions parameters', body=None):
    body = body or ('Python functions accept parameters and return reusable results. ' * 6)
    raw = f'<title>{title}</title><main><h1>{title}</h1><p>{body}</p></main>'.encode()
    return FetchedContent(url, url, 'text/html', raw, '')


def quality(grade, scopes, **kwargs):
    return dict(credibility=grade, relevance=5, eligible=True, covered_scopes=scopes, **kwargs)


def test_sources47_fallback_is_per_module_and_never_default():
    modules = [dict(title='函数'), dict(title='网络')]
    selected = q.lesson_selection([quality(5, [1]), quality(3, [1]), quality(3, [1, 2])], modules)
    assert [s['display'] for s in selected] == [True, False, True]
    assert [s['recommended'] for s in selected] == [True, False, False]
    assert not selected[2]['default_adopt'] and selected[2]['fallback_scopes'] == [2]
    assert selected[0]['note'] == '覆盖：函数'
    assert '一般网站，可能有错，请自行判断' in selected[2]['note']
    assert not q.lesson_selection([quality(3, [0])], modules)[0]['display']
    assert not q.lesson_selection([quality(3, [1])], modules, existing=[quality(4, [1])])[0]['display']
    stale = quality(5, [1], context=dict(scopes=['goal', '另外的模块 ']))
    assert q.lesson_selection([quality(3, [1])], modules, existing=[stale])[0]['display']


@pytest.mark.parametrize('url', [
    'https://developer.aliyun.com/article/772344', 'https://blog.csdn.net/a/article/1',
    'https://juejin.cn/post/1', 'https://zhihu.com/question/1', 'https://tieba.baidu.com/p/1',
    'https://cloud.tencent.com/developer/article/1', 'https://learn.microsoft.com/ja-jp/archive/blogs/a/b',
])
def test_sources47_community_identity(url):
    identity = classify_source(url, page_text='版权所有，侵权请联系我们。')
    assert identity['level'] == 3 and identity['type'] == 'community'
    assert '侵权' not in identity['publisher']


@pytest.mark.parametrize('text', [
    '版权所有：所有，如有侵权，请联系我们', '来源：不承担相应法律责任',
    '作者：购买课程请扫码', 'Copyright © 2026 Everyone', '来源：https://foo.example/x',
])
def test_sources47_legal_and_sales_text_is_not_publisher(text):
    assert classify_source('https://unknown.example/lesson', page_text=text)['publisher'] == ''


def test_sources47_visible_name_and_english_authority():
    assert classify_source('https://unknown.example/chapter', page_text='作者：王老师\n正文')['publisher'] == '王老师'
    assert classify_source('https://takeielts.britishcouncil.org/writing')['level'] == 1
    assert classify_source('https://cloud.tencent.com/document/product/1')['type'] != 'community'
    fetched = page('https://openstax.org/books/physics/pages/pressure', 'Pressure',
                   'Pressure describes pressure exerted by fluids. ' * 8)
    with ResearchPipeline(fetcher=lambda *a, **k: fetched) as pipeline:
        source = pipeline.run([RawHit(fetched.url)]).verified[0]
    result = q.assess(source, '压强', modules=[dict(title='压强', summary='压强概念')])
    assert result['credibility'] == 5 and result['eligible'] and 1 in result['covered_scopes']
    source = SimpleNamespace(final_url='https://university.edu.cn/chapter', title='计算机工作原理', reasons=(),
        document=SimpleNamespace(blocks=[SimpleNamespace(text='计算机的程序需要从外存调入内部存储器，CPU 执行内存中的程序。'*10)], warnings=()))
    result = q.assess(source, '认识电脑', modules=[dict(title='电脑的三个核心部件', summary='区分内存、硬盘、CPU')])
    assert 1 in result['covered_scopes']


def test_sources47_preview_rejected_but_uploaded_original_url_is_not_preview():
    url = 'https://www.51jiaoxi.com/doc-123.html'
    assert is_document_preview(url)
    assert not is_document_preview('https://www.51jiaoxi.com/original.pptx')
    with ResearchPipeline(fetcher=lambda u, **k: page(u, '二次方程课件')) as pipeline:
        result = pipeline.run([RawHit(url)])
    assert not result.verified and result.rejected[0].code == 'document_preview'
    assert '预览页，非原文' in result.rejected[0].message and '上传' in result.rejected[0].message
    source = SimpleNamespace(final_url=url, title='二次方程课件', reasons=(),
        document=SimpleNamespace(blocks=[SimpleNamespace(text='二次方程解法 '*50)], warnings=()))
    assert not q.assess(source, '二次方程')['eligible']


def test_sources47_catalogue_and_locale_copies():
    source = SimpleNamespace(final_url='https://library.edu.cn/opac/book/1', title='唐朝丝绸之路', reasons=(),
        document=SimpleNamespace(blocks=[SimpleNamespace(text='唐朝丝绸之路 '*50+'中图分类法：责任者附注：')], warnings=()))
    result = q.assess(source, '唐朝丝绸之路')
    assert not result['eligible'] and 'catalogue_not_original' in result['reasons']
    assert q.content_identity('https://learn.microsoft.com/ja-jp/archive/blogs/user_ed/computer-hardware') == q.content_identity('https://learn.microsoft.com/id-id/archive/blogs/user_ed/computer-hardware')
    source.final_url = 'https://university.edu.cn/chapter'
    source.title = '标题'
    result = q.assess(source, '唐朝丝绸之路')
    assert not result['eligible'] and 'title_unverified' in result['reasons']


def test_sources47_body_dedup_canonical_original_and_pool():
    urls = ['https://m.docs.python.org/3/functions?utm_source=x',
            'https://docs.python.org/3/functions', 'https://community.example/copy']
    with ResearchPipeline(fetcher=lambda u, **k: page(u)) as pipeline:
        result = pipeline.run([RawHit(u) for u in urls])
    assert len(result.verified) == 1 and result.verified[0].url == urls[1]
    assert len([r for r in result.rejected if r.code == 'duplicate_body']) == 2
    original = result.verified[0]
    mobile = replace(original, url=urls[0], final_url=urls[0])
    pool = ResearchPool(); pool.add([mobile]); pool.add([original])
    assert len(pool.available()) == 1 and pool.available()[0].url == urls[1]
    pool.mark_shown(urls[1]); assert not pool.available()
    assert q.body_identity('A real example. '*20) != q.body_identity('A different example. '*20)
    assert q.body_identity('body\n'*80) == q.body_identity('body '*80)
    assert q.body_identity('Python return Value. '*20) != q.body_identity('Python return value. '*20)


def test_sources47_cleanup_title_whole_copy_noise_and_math():
    text = 'Pressure is the force acting per unit area. It is measured in pascals. '*4
    raw = ('<title>图文详情</title><main><div class="site-menu">首页 下载 联系</div>'
           '<article><h1>Pressure and density</h1><p>'+text+'</p></article>'
           '<article><h1>Pressure and density</h1><p>'+text+'</p></article>'
           '<p>免责声明：侵权请联系我们</p><p>a@example.com</p></main>')
    doc = extract_html(raw, 'https://example.org/article')
    body = '\n'.join(b.text for b in doc.blocks)
    assert doc.title == 'Pressure and density'
    assert body.count(text.strip()) == 1
    assert '首页' not in body and '免责声明' not in body and '@' not in body
    math = '<main><p>Density: <math><semantics><mi>ρ</mi><annotation-xml><mi>ρ</mi></annotation-xml></semantics></math>.</p><p><math><semantics><mrow><mi>P</mi><mo>=</mo><mi>hρg</mi></mrow><annotation-xml><mi>P=hρg</mi></annotation-xml></semantics></math></p></main>'
    body = '\n'.join(b.text for b in extract_html(math, 'https://openstax.org/chapter').blocks)
    assert 'ρρ' not in body and body.count('P=hρg') == 1
    # A lesson discussing copyright/email or repeated mathematical symbols stays.
    assert clean_page_text('Email addresses contain @. Copyright protects works. ρρ') == 'Email addresses contain @. Copyright protects works. ρρ'


def test_sources47_show_batch_uses_preview_all_candidates_and_keeps_manual_state():
    urls = ['https://www.bbc.com/news/python', 'https://docs.python.org/3/functions',
            'https://www.bbc.com/news/network']
    def fetch(u, **k):
        return page(u, 'Network browser' if u.endswith('network') else 'Python functions parameters',
            'Network browser explain network browsing. '*10 if u.endswith('network')
            else ('Python functions parameters teaching. '+u+' ')*8)
    with ResearchPipeline(fetcher=fetch) as pipeline:
        sources = pipeline.run([RawHit(u) for u in urls]).verified
    pool = ResearchPool(); pool.add(sources)
    flow = PlanFlow.__new__(PlanFlow); flow._pools = {'p': pool}
    flow._lists = load_lists; flow.study_day = lambda: date(2026, 10, 4)
    flow.status = flow.save = lambda *a: None
    flow._ask = lambda *a, **k: ({'r2': dict(relevance=5, note='The original chapter explicitly explains network browsers.')}, None)
    draft = dict(plan_id='p', goal='Python functions parameters network browser', form=dict(level='beginner', final_goal='Understand programming and networks'),
        preview=dict(modules=[dict(title='Python functions parameters'), dict(title='Network browser')]),
        sources=[], research={}, messages=[])
    flow._show_batch(draft, SimpleNamespace(cancel=threading.Event()))
    assert {c['url'] for c in draft['sources']} == set(urls[1:])
    assert all(c['status'] == 'new' for c in draft['sources'])
    fallback = next(c for c in draft['sources'] if c['credibility'] == 3)
    assert not fallback['quality']['default_adopt'] and fallback['quality']['covered_modules'] == ['Network browser']
    # Adoption rechecks admission without preview fields. The saved display
    # audit still prevents a later third-band card replacing adopted authority.
    official = next(c for c in draft['sources'] if c['credibility'] == 5)
    official['status'] = 'adopted'
    official['quality'] = dict(eligible=True, credibility=5, covered_scopes=[0])
    assert flow._show_batch(draft, SimpleNamespace(cancel=threading.Event()), quiet=True) == 0
