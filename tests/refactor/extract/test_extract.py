from dataclasses import FrozenInstanceError
import hashlib
import io
from pathlib import Path
import sys
import threading

from docx import Document
from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                          NameObject, NumberObject, TextStringObject)
import pytest

from refactor.learning.course.extract import (
    ExtractError, TextBlock, chunk_blocks, extract_bytes, extract_file, extract_html,
)


def pdf_bytes(pages, *, password=None):
    """Tiny, self-contained Unicode PDF generated without a font/network asset."""
    writer = PdfWriter()
    chars = sorted(set(''.join(pages)))
    cmap = DecodedStreamObject()
    mapping = '\n'.join(f'<{ord(char):04X}> <{ord(char):04X}>' for char in chars)
    cmap.set_data(('''/CIDInit /ProcSet findresource begin
12 dict begin begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /TestChinese def /CMapType 2 def
1 begincodespacerange <0000> <FFFF> endcodespacerange
''' + f'{len(chars)} beginbfchar\n{mapping}\nendbfchar\n' +
        'endcmap CMapName currentdict /CMap defineresource pop end end').encode())
    cid = DictionaryObject({
        NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/CIDFontType0'),
        NameObject('/BaseFont'): NameObject('/STSong-Light'),
        NameObject('/CIDSystemInfo'): DictionaryObject({NameObject('/Registry'): TextStringObject('Adobe'),
            NameObject('/Ordering'): TextStringObject('GB1'), NameObject('/Supplement'): NumberObject(0)}),
        NameObject('/DW'): NumberObject(1000),
    })
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type0'), NameObject('/BaseFont'): NameObject('/STSong-Light'),
        NameObject('/Encoding'): NameObject('/Identity-H'),
        NameObject('/DescendantFonts'): ArrayObject([writer._add_object(cid)]),
        NameObject('/ToUnicode'): writer._add_object(cmap)})
    font_ref = writer._add_object(font)
    for text in pages:
        page = writer.add_blank_page(width=595, height=842)
        if text:
            content = DecodedStreamObject()
            content.set_data(f'BT /F1 12 Tf 50 780 Td <{text.encode("utf-16-be").hex()}> Tj ET'.encode())
            page[NameObject('/Contents')] = writer._add_object(content)
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font_ref})})
    writer.add_metadata({'/Title': '中文合成教材'})
    if password is not None:
        writer.encrypt(password, algorithm='RC4-128')
    stream = io.BytesIO(); writer.write(stream)
    assert len(stream.getvalue()) < 200_000
    return stream.getvalue()


def word_bytes():
    document = Document()
    document.add_heading('第3章 大语言模型基础', 1)
    document.add_paragraph('本章介绍模型的基本结构。')
    document.add_heading('3.2 提示工程', 2)
    document.add_paragraph('提示需要明确任务、输入和输出。')
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = '方法'; table.cell(0, 1).text = '作用'
    table.cell(1, 0).text = '示例'; table.cell(1, 1).text = '说明预期输出'
    document.add_heading('第4章 评估', 1)
    document.add_paragraph('通过测试评估回答。')
    stream = io.BytesIO(); document.save(stream)
    assert len(stream.getvalue()) < 200_000
    return stream.getvalue()


def assert_positions(document):
    full = '\n\n'.join(block.text for block in document.blocks)
    assert document.char_count == len(full)
    previous = 0
    for number, chunk in enumerate(document.chunks, 1):
        assert chunk.chunk_id == f'{document.sha256[:12]}:{number:04d}'
        assert full[chunk.char_start:chunk.char_end] == chunk.text
        assert chunk.char_start >= previous and chunk.char_end > chunk.char_start
        assert not full[previous:chunk.char_start].strip()
        assert len(chunk.text) <= 2000
        previous = chunk.char_end
    assert not full[previous:].strip()


def test_multi_page_chinese_pdf_positions_and_title():
    pages = ['第一页介绍大语言模型的基本概念以及应用范围。知识来源应当准确可靠。',
             '第二页讨论提示工程的方法以及实例。学习时应当结合具体问题进行练习。']
    raw = pdf_bytes(pages)
    result = extract_bytes(raw, '中文.PDF')
    assert result.title == '中文合成教材' and result.media_type == 'application/pdf'
    assert [(b.text, b.page) for b in result.blocks] == [(pages[0], 1), (pages[1], 2)]
    assert result.chunks[0].page_start == 1 and result.chunks[0].page_end == 2
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert_positions(result)


@pytest.mark.parametrize('pages', [[''], ['', ''], ['短文'], ['字' * 39, '']])
def test_blank_or_sparse_pdf_is_scanned(pages):
    with pytest.raises(ExtractError) as caught:
        extract_bytes(pdf_bytes(pages), 'scan.pdf')
    assert caught.value.code == 'SCANNED_NO_TEXT' and 'OCR' in caught.value.message


def test_pdf_average_threshold_and_empty_page_warning():
    result = extract_bytes(pdf_bytes(['字' * 40, '']), 'mixed.pdf')
    assert [b.page for b in result.blocks] == [1]
    assert result.warnings and '第 2 页' in result.warnings[0]


def test_encrypted_pdf_and_empty_password_pdf():
    raw = pdf_bytes(['中文正文内容。' * 8], password='synthetic-password')
    with pytest.raises(ExtractError) as caught:
        extract_bytes(raw, 'protected.pdf')
    assert caught.value.code == 'ENCRYPTED'
    assert extract_bytes(pdf_bytes(['中文正文内容。' * 8], password=''), 'open.pdf').blocks


def test_docx_heading_path_table_and_document_order():
    result = extract_bytes(word_bytes(), '课程.docx')
    assert result.title == '第3章 大语言模型基础'
    assert [b.kind for b in result.blocks] == ['heading', 'paragraph', 'heading', 'paragraph', 'table', 'heading', 'paragraph']
    assert result.blocks[3].section == ('第3章 大语言模型基础', '3.2 提示工程')
    assert result.blocks[4].text == '方法 | 作用\n示例 | 说明预期输出'
    assert result.blocks[-1].section == ('第4章 评估',)
    assert all(b.page is None for b in result.blocks)
    assert_positions(result)


def test_markdown_headings_atomic_code_and_lists():
    code = '    print("示例")\n' * 65
    raw = ('# 章节\n\n首段介绍。\n\n## 示例\n\n```python\n' + code + '```\n\n- 练习一\n- 练习二').encode()
    result = extract_bytes(raw, '课程.markdown')
    block = next(b for b in result.blocks if b.kind == 'code')
    assert block.text == code.rstrip('\n') and block.section == ('章节', '示例')
    assert sum(block.text in chunk.text for chunk in result.chunks) == 1
    assert result.blocks[-1].kind == 'list'
    assert '章节\n\n首段介绍。' in result.chunks[0].text
    assert_positions(result)


def test_markdown_unclosed_fence_keeps_content_and_warns():
    result = extract_bytes('## 标题\n```python\nx = 1\n# 这不是标题'.encode(), 'sample.md')
    assert result.blocks[-1].kind == 'code' and '# 这不是标题' in result.blocks[-1].text
    assert result.warnings and result.blocks[-1].section == ('标题',)


def test_markdown_heading_literal_hash_and_closing_marker():
    result = extract_bytes('# C#\n\n正文。\n\n## 示例 ###\n\n说明。'.encode(), 'sample.md')
    assert result.title == 'C#' and result.blocks[-1].section == ('C#', '示例')


def test_docx_derived_heading_style_and_skipped_level():
    from docx.enum.style import WD_STYLE_TYPE
    document = Document()
    style = document.styles.add_style('Course heading', WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = document.styles['Heading 1']
    document.add_paragraph('课程', style=style)
    document.add_heading('直接三级', 3)
    document.add_paragraph('这是首段。')
    stream = io.BytesIO(); document.save(stream)
    result = extract_bytes(stream.getvalue(), 'sample.docx')
    assert result.blocks[-1].section == ('课程', '直接三级')
    assert len(result.chunks) == 1 and result.chunks[0].section == ('课程',)


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'gb18030'])
@pytest.mark.parametrize('suffix', ['txt', 'csv', 'json'])
def test_text_encodings(encoding, suffix):
    text = {'txt': '中文资料\n\n第二段正文', 'csv': '名称,说明\n术语,中文定义', 'json': '{"标题": "中文资料"}'}[suffix]
    result = extract_bytes(text.encode(encoding), 'sample.' + suffix)
    assert '中文' in result.chunks[0].text
    assert '\ufeff' not in result.chunks[0].text
    assert_positions(result)


def test_html_noise_title_sections_and_no_fetch():
    html = '''<!doctype html><html><head><title>课程网页</title><style>恶意噪声</style></head>
    <body><header>页头噪声</header><nav><h1>导航标题</h1>导航噪声</nav><aside>侧栏噪声</aside>
    <main><h1>第一章</h1><p>这是真正的<b>中文正文</b>。</p><h2>1.1 内容</h2>
    <p>第二段正文。</p><script src="https://must-not-fetch.test/script">脚本噪声</script>
    <form>表单噪声</form><div role="navigation">菜单噪声</div><p hidden>隐藏噪声</p>
    <table><tr><th>项目</th><th>值</th></tr><tr><td>测试</td><td>通过</td></tr></table>
    <pre>    x = 1\n    y = 2</pre></main><footer>页脚噪声</footer></body></html>'''
    result = extract_html(html, 'https://must-not-fetch.test/course')
    assert result.title == '课程网页' and result.media_type == 'text/html'
    assert all('噪声' not in b.text and '导航' not in b.text for b in result.blocks)
    assert result.blocks[1].text == '这是真正的中文正文。'
    assert result.blocks[3].section == ('第一章', '1.1 内容')
    assert result.blocks[4].kind == 'table' and result.blocks[4].text == '项目 | 值\n测试 | 通过'
    assert result.blocks[5].kind == 'code' and result.blocks[5].text.startswith('    x')
    assert extract_bytes(html.encode(), 'sample.htm').blocks == result.blocks
    assert extract_html(html.encode(), 'https://must-not-fetch.test/course') == result
    assert_positions(result)


def test_html_article_fallback_h1_and_inline_text_once():
    result = extract_html('<nav><h1>不要此标题</h1></nav><article><h1>真正标题</h1><div>开头<b>加粗</b><p>段落</p>结尾</div></article><div>无关内容</div>', 'https://example.test/page')
    assert result.title == '真正标题'
    assert [b.text for b in result.blocks] == ['真正标题', '开头加粗', '段落', '结尾']
    assert_positions(result)


def test_html_nonsemantic_navigation_and_url_title_fallback():
    result = extract_html('<div class="navbar">导航</div><div id="sidebar">侧栏</div><div>正文资料</div>', 'https://example.test/%E8%AF%BE%E7%A8%8B')
    assert [block.text for block in result.blocks] == ['正文资料']
    assert result.title == '课程'


@pytest.mark.parametrize('filename,data', [('sample.pdf', b'%PDF-broken'), ('sample.docx', b'broken'),
    ('sample.txt', b'\xff'), ('sample.txt', b'\x00binary'), ('sample.json', b'{invalid}'), ('sample.csv', b'"unclosed')])
def test_corrupt_data(filename, data):
    with pytest.raises(ExtractError) as caught:
        extract_bytes(data, filename)
    assert caught.value.code == 'CORRUPT' and caught.value.message


@pytest.mark.parametrize('filename', ['sample.txt', 'sample.pdf', 'sample.md', 'sample.html', 'sample.docx'])
def test_empty_bytes(filename):
    with pytest.raises(ExtractError) as caught:
        extract_bytes(b'', filename)
    assert caught.value.code == 'EMPTY'


@pytest.mark.parametrize('suffix', ['txt', 'csv', 'json', 'html', 'md'])
def test_only_whitespace_is_empty(suffix):
    with pytest.raises(ExtractError) as caught:
        extract_bytes(b' \n\t', 'empty.' + suffix)
    assert caught.value.code == 'EMPTY'


def test_html_only_noise_is_empty():
    with pytest.raises(ExtractError) as caught:
        extract_html('<script>noise</script><nav>noise</nav>', 'https://example.test')
    assert caught.value.code == 'EMPTY'


def test_unsupported_and_size_before_parse_or_read(tmp_path, monkeypatch):
    with pytest.raises(ExtractError) as caught:
        extract_bytes(b'anything', 'sample.exe')
    assert caught.value.code == 'UNSUPPORTED_TYPE'
    with pytest.raises(ExtractError) as caught:
        extract_bytes(b'123456', 'sample.pdf', max_bytes=5)
    assert caught.value.code == 'TOO_LARGE'
    file = tmp_path / 'large.txt'; file.write_bytes(b'123456')
    monkeypatch.setattr(Path, 'open', lambda *a, **kw: pytest.fail('Oversized input was opened'))
    with pytest.raises(ExtractError) as caught:
        extract_file(file, max_bytes=5)
    assert caught.value.code == 'TOO_LARGE'


def test_file_and_bytes_same_hash_chunks_and_offsets(tmp_path):
    data = ('# 标题\n\n' + '中文正文。' * 800).encode()
    file = tmp_path / 'example.md'; file.write_bytes(data)
    left, right = extract_file(file), extract_bytes(data, file.name)
    assert left == right == extract_file(file)
    assert len(left.chunks) > 1
    assert_positions(left)
    with pytest.raises(FrozenInstanceError): left.title = 'cannot mutate'
    with pytest.raises(FrozenInstanceError): left.blocks[0].text = 'cannot mutate'
    with pytest.raises(FrozenInstanceError): left.chunks[0].text = 'cannot mutate'


def test_input_byte_limit_is_inclusive_and_missing_file_has_user_error(tmp_path):
    assert extract_bytes(b'hello', 'sample.txt', max_bytes=5).char_count == 5
    with pytest.raises(ExtractError) as caught:
        extract_file(tmp_path / 'missing.txt')
    assert caught.value.code == 'CORRUPT' and '读取' in caught.value.message


@pytest.mark.parametrize('kind', ['code', 'table'])
def test_atomic_blocks_not_cut_below_limit_and_split_by_lines_above(kind):
    block = TextBlock(('abcdefghij\n' * 6).rstrip(), kind, None, ('节',))
    chunks = chunk_blocks([block], source_sha256='a' * 64, target_chars=20, max_chars=80)
    assert len(chunks) == 1 and chunks[0].text == block.text
    block = TextBlock('abcdefghij\n' * 30, kind, None, ('节',))
    chunks = chunk_blocks([block], source_sha256='a' * 64, target_chars=40, max_chars=80)
    assert ''.join(c.text for c in chunks) == block.text
    assert all(len(c.text) <= 80 for c in chunks)
    assert all(c.text.endswith('\n') for c in chunks[:-1])
    oversized_line = TextBlock('x' * 221, kind, None, ())
    assert all(len(c.text) <= 80 for c in chunk_blocks([oversized_line], source_sha256='b' * 64, target_chars=40, max_chars=80))


def test_heading_stays_with_start_of_oversized_first_paragraph():
    blocks = [TextBlock('章节标题', 'heading', None, ('章节标题',)),
              TextBlock('正文' * 100, 'paragraph', None, ('章节标题',))]
    chunks = chunk_blocks(blocks, source_sha256='c' * 64, target_chars=40, max_chars=60)
    assert chunks[0].text.startswith('章节标题\n\n正文')
    assert all(len(c.text) <= 60 for c in chunks)


def test_sentence_boundaries_section_changes_and_page_ranges():
    blocks = [TextBlock('这是一个句子。下一个句子！然后是问题？' * 8, 'paragraph', 1, ('甲',)),
              TextBlock('第二页正文。', 'paragraph', 2, ('甲',)),
              TextBlock('新节', 'heading', 3, ('乙',)), TextBlock('首段内容。', 'paragraph', 3, ('乙',))]
    chunks = chunk_blocks(blocks, source_sha256='d' * 64, target_chars=35, max_chars=55)
    assert all(len(c.text) <= 55 for c in chunks)
    assert chunks[0].text[-1] in '。！？'
    assert chunks[-1].section == ('乙',) and chunks[-1].text == '新节\n\n首段内容。'
    assert chunks[-1].page_start == chunks[-1].page_end == 3


@pytest.mark.parametrize('target,maximum', [(1, 1), (3, 7), (1200, 2000)])
def test_chunk_limits_and_offsets_on_mixed_extreme_blocks(target, maximum):
    blocks = [TextBlock('很长的章节标题', 'heading', 1, ('章节',)),
              TextBlock('正文没有任何句号' * 451, 'paragraph', 1, ('章节',)),
              TextBlock('one line ' * 500, 'code', 2, ('章节',)),
              TextBlock('条目 | 说明\n' * 130, 'table', 2, ('章节',)),
              TextBlock('结束。', 'paragraph', 3, ('章节',))]
    chunks = chunk_blocks(blocks, source_sha256='f' * 64, target_chars=target, max_chars=maximum)
    full = '\n\n'.join(block.text for block in blocks)
    previous = 0
    for chunk in chunks:
        assert 0 < len(chunk.text) <= maximum
        assert full[chunk.char_start:chunk.char_end] == chunk.text
        assert not full[previous:chunk.char_start].strip()
        previous = chunk.char_end
    assert previous == len(full)


@pytest.mark.parametrize('target,maximum', [(0, 20), (30, 20), (1, -1)])
def test_invalid_chunk_configuration(target, maximum):
    with pytest.raises(ValueError):
        chunk_blocks([], source_sha256='f' * 64, target_chars=target, max_chars=maximum)


@pytest.mark.parametrize('call', [lambda e: extract_bytes(b'x', 'sample.txt', cancel=e),
    lambda e: extract_file('does-not-exist.pdf', cancel=e),
    lambda e: extract_html('<p>text</p>', 'https://example.test', cancel=e)])
def test_pre_cancelled_event(call):
    event = threading.Event(); event.set()
    with pytest.raises(ExtractError) as caught: call(event)
    assert caught.value.code == 'CANCELLED'


def test_pdf_cancellation_between_pages(monkeypatch):
    from pypdf import PageObject
    event = threading.Event()
    original = PageObject.extract_text
    calls = []
    def extract(page, *args, **kwargs):
        calls.append(True)
        value = original(page, *args, **kwargs)
        event.set()
        return value
    monkeypatch.setattr(PageObject, 'extract_text', extract)
    with pytest.raises(ExtractError) as caught:
        extract_bytes(pdf_bytes(['中文正文' * 12] * 3), 'cancel.pdf', cancel=event)
    assert caught.value.code == 'CANCELLED' and len(calls) == 1


def test_cancellation_during_chunking(monkeypatch):
    from refactor.learning.course import extract
    event = threading.Event()
    original = extract._split
    def split(*args, **kwargs):
        for span in original(*args, **kwargs):
            yield span
            event.set()
    monkeypatch.setattr(extract, '_split', split)
    with pytest.raises(ExtractError) as caught:
        extract_bytes(('很长的段落。' * 1000).encode(), 'cancel.txt', cancel=event)
    assert caught.value.code == 'CANCELLED'


@pytest.mark.parametrize('filename,data', [('sample.docx', word_bytes()),
    ('sample.md', ('# 标题\n\n正文。\n\n' * 20).encode()),
    ('sample.html', ('<p>中文正文</p>' * 30).encode())], ids=['docx', 'markdown', 'html'])
def test_cancellation_during_structured_parser_loops(filename, data):
    class StepEvent(threading.Event):
        checks = 0
        def is_set(self):
            self.checks += 1
            if self.checks == 6: self.set()
            return super().is_set()
    event = StepEvent()
    with pytest.raises(ExtractError) as caught:
        extract_bytes(data, filename, cancel=event)
    assert caught.value.code == 'CANCELLED'


def test_no_qt_or_database_imports():
    # Check in a fresh interpreter: other suites in the same pytest run may
    # already have loaded Qt, which says nothing about this module.
    import subprocess
    probe = ("import sys, refactor.learning.course.extract; "
             "bad = [n for n in sys.modules if n.startswith(('PySide6', 'PyQt', 'sqlite3', 'sqlalchemy'))]; "
             "print(bad); raise SystemExit(1 if bad else 0)")
    result = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[3])
    assert result.returncode == 0, result.stdout + result.stderr
