"""Full-text follow-up, source date evidence, and fixed authority boundaries."""
from datetime import date
from types import SimpleNamespace

from refactor.learning.course.source_follow import lesson_links
from refactor.learning.course.source_quality import assess, assess_stored, content_identity, published_date, body_publication_date
from refactor.learning.course.research_pipeline import ResearchPipeline
from refactor.learning.course.research import FetchedContent
from refactor.learning.course.source_filter import RawHit


def test_only_same_host_lesson_downloads_are_discovered():
    base='https://ocw.mit.edu/courses/18-06/resources/lecture'
    html=b'''<a href="/courses/18-06/lesson.pdf" aria-label="Download transcript: English">English</a>
    <a href="https://evil.test/lesson.pdf" aria-label="Download file">bad</a>
    <a href="/courses/other.pdf">unlabelled</a>
    <a href="/courses/18-06/notes.txt" aria-label="Download notes">Notes</a>'''
    assert lesson_links(html,base)==['https://ocw.mit.edu/courses/18-06/lesson.pdf',
                                     'https://ocw.mit.edu/courses/18-06/notes.txt']


def test_pipeline_follows_full_transcript_but_never_adopts_entrance():
    root='https://ocw.mit.edu/courses/18-06/resources/lecture'
    child='https://ocw.mit.edu/courses/18-06/lecture-transcript.txt'
    calls=[]
    def fetch(url, **_):
        calls.append(url)
        if url==root:
            body=(b'<title>Linear Algebra eigenvalues eigenvectors</title><h1>Linear Algebra eigenvalues eigenvectors</h1>'
                  b'<p>Special matrices have eigenvalues and eigenvectors. Your browser does not support features required to play this video.</p>'
                  b'<a href="/courses/18-06/lecture-transcript.txt" aria-label="Download transcript">English</a>')
            return FetchedContent(url,url,'text/html',body,'')
        return FetchedContent(url,url,'text/plain',('Linear Algebra eigenvalues eigenvectors\n'
          'In this lecture, eigenvectors remain in the same direction under a matrix transformation. '*8).encode(),'')
    with ResearchPipeline(fetcher=fetch) as pipeline:
        result=pipeline.run([RawHit(root,'Linear Algebra eigenvalues eigenvectors')])
    assert calls==[root,child] and len(result.verified)==1
    item=result.verified[0]
    assert item.final_url==child and item.document.char_count>500
    assert assess(item,'linear algebra eigenvalues eigenvectors',today=date(2026,9,29))['eligible']


def test_landing_does_not_fall_back_to_metadata_or_cross_host():
    root='https://ocw.mit.edu/courses/18-06/resources/lecture'
    body=(b'<title>Linear Algebra eigenvalues eigenvectors</title><h1>Linear Algebra eigenvalues eigenvectors</h1>'
          b'<p>Special matrices have eigenvalues and eigenvectors. Your browser does not support features required to play this video.</p>'
          b'<a href="https://untrusted.test/lecture.pdf" aria-label="Download transcript">bad</a>')
    calls=[]
    def fetch(url, **_):
        calls.append(url)
        return FetchedContent(url,url,'text/html',body,'')
    with ResearchPipeline(fetcher=fetch) as pipeline:
        result=pipeline.run([RawHit(root,'Linear Algebra eigenvalues eigenvectors')])
    assert calls==[root]
    assert not assess(result.verified[0],'linear algebra eigenvalues eigenvectors')['eligible']


def test_visible_and_structured_dates_not_copyright_or_fetch_time():
    who='''<span class="timestamp">25 September 2025</span>
    <script>var pageSchemaObj={"dateModified":"2025-09-25T16:37:57+00:00",
      "datePublished":"2025-09-25T16:37:57+00:00","@context":"http://schema.org","@type":"Article"};</script>'''
    assert published_date(who)=='2025-09-25'
    assert published_date(who + '<span class="timestamp">10 September 2026</span>')=='2025-09-25'
    assert published_date('<p>Copyright 2026. Retrieved 2026-09-29.</p>') is None
    assert published_date('<script type="application/ld+json">{"@type":"Article","datePublished":"2025-08-14"}</script>')=='2025-08-14'
    assert body_publication_date('Published: July 31, 2025\nHypertension content')=='2025-07-31'
    assert body_publication_date('DALLAS, May 28, 2026 — High blood pressure is...')=='2026-05-28'


def test_single_concept_and_bilingual_equivalence_without_partial_word_match():
    def source(title, body):
        return SimpleNamespace(final_url='https://www.who.int/news-room/fact-sheets/detail/hypertension',
            title=title, reasons=(), published_at='2025-09-25',
            document=SimpleNamespace(blocks=[SimpleNamespace(text=body)],warnings=()))
    assert assess(source('Hypertension','Hypertension is high blood pressure. Hypertension can be managed.'),
                  'hypertension basics',today=date(2026,9,29))['eligible']
    assert assess(source('高血压','高血压是常见慢性病。高血压应定期检查。'),
                  'hypertension basics',today=date(2026,9,29))['eligible']
    assert not assess(source('Hypertensive heart','A hypertensive heart is not the same lesson. '*8),
                      'hypertension basics',today=date(2026,9,29))['eligible']


def test_resource_index_and_category_are_not_adopted():
    assert lesson_links(b'<a href="/lesson.pdf">View lesson</a>', 'https://takeielts.britishcouncil.org/lessons') == [
        'https://takeielts.britishcouncil.org/lesson.pdf']
    listings = [('https://takeielts.britishcouncil.org/teach-ielts/teaching-resources/lesson-plans-writing',
                 'Lesson plans - writing', 'View lesson: academic writing. View lesson: essay writing. ' * 8),
                ('https://learnenglish.britishcouncil.org/category/grammar/present-perfect-simple',
                 'Present perfect simple', 'Present perfect grammar and examples. ' * 8),
                ('https://takeielts.britishcouncil.org/', 'IELTS academic writing',
                 'IELTS academic writing practice tests are available. ' * 8)]
    for url, title, text in listings:
        result = assess_stored({'url': url, 'title': title}, [{'text': text}], 'IELTS academic writing')
        assert not result['eligible']


def test_localized_course_and_spec_mirrors_share_content_identity():
    assert content_identity(None) == ''  # a user-provided file has no URL
    assert content_identity('https://learn.microsoft.com/de-de/training/modules/a/lesson?ns-enrollment-id=x') == \
        content_identity('https://learn.microsoft.com/zh-cn/training/modules/a/lesson')
    assert content_identity('https://modelcontextprotocol.io/specification/2026-07-28/architecture/index.md') == \
        content_identity('https://modelcontextprotocol.io/specification/2025-03-26/architecture')
    assert content_identity('https://learn.microsoft.com/zh-cn/training/modules/a/other') != \
        content_identity('https://learn.microsoft.com/zh-cn/training/modules/a/lesson')


def test_chinese_original_python_lesson_matches_english_goal():
    source = {'url': 'https://learn.microsoft.com/zh-cn/training/modules/write-reusable-code-python/2-create-functions-with-parameters',
              'title': '使用参数创建函数'}
    blocks = [{'kind': 'heading', 'text': '使用参数创建函数'},
              {'kind': 'paragraph', 'text': 'Python 函数用 def 定义，参数是输入。函数可定义多个参数，调用函数时传入值。'}]
    result = assess_stored(source, blocks, 'Python functions parameters', level='beginner')
    assert result['eligible'] and result['credibility'] >= 4 and result['relevance'] >= 4


def test_protocol_revision_date_overrides_new_site_build_date():
    text = 'Model Context Protocol architecture uses clients and servers. Model Context Protocol architecture. '
    old = {'url': 'https://modelcontextprotocol.io/specification/2025-03-26/architecture',
           'title': 'Architecture - Model Context Protocol', 'published_at': '2026-09-28'}
    new = {**old, 'url': 'https://modelcontextprotocol.io/specification/2026-07-28/architecture'}
    chunks = [{'text': text * 5}]
    assert not assess_stored(old, chunks, 'Model Context Protocol architecture', today=date(2026,9,29))['eligible']
    assert assess_stored(new, chunks, 'Model Context Protocol architecture', today=date(2026,9,29))['eligible']


def test_current_protocol_index_follows_owned_basic_lesson():
    root = 'https://modelcontextprotocol.io/specification/2026-07-28'
    child = root + '/basic'
    body = b'<title>Specification - Model Context Protocol</title><h1>Specification</h1>' + \
           b'<p>Model Context Protocol architecture explains clients and servers. ' * 5 + \
           b'</p><a href="/specification/2026-07-28/basic">Overview</a>'
    def fetch(url, **_):
        if url == root:
            return FetchedContent(url, url, 'text/html', body, '')
        return FetchedContent(url, url, 'text/plain',
                              b'Model Context Protocol architecture has clients and servers. ' * 12, '')
    with ResearchPipeline(fetcher=fetch) as pipeline:
        result = pipeline.run([RawHit(root, 'Specification')])
    assert len(result.verified) == 1 and result.verified[0].final_url == child
