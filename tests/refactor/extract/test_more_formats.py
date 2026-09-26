import datetime
import io
import threading
import zipfile

from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches
import pytest

from refactor.learning.course.extract import ExtractError, extract_bytes, extract_file
from test_extract import assert_positions


def pptx_bytes():
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = '第一章 模型基础'
    slide.placeholders[1].text = '学习目标：了解模型。'
    group = slide.shapes.add_group_shape()
    group.shapes.add_textbox(0, 0, Inches(3), Inches(1)).text = '组合中的正文'
    table = slide.shapes.add_table(2, 2, 0, Inches(3), Inches(5), Inches(1)).table
    table.cell(0, 0).text = '名称'; table.cell(0, 1).text = '作用'
    table.cell(1, 0).text = '注意力'; table.cell(1, 1).text = '关联上下文'
    slide.notes_slide.notes_text_frame.text = '教师备注：给出实际例子。'
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = '第二章 练习'
    slide.placeholders[1].text = '解释提示词的作用。'
    stream = io.BytesIO(); deck.save(stream)
    assert len(stream.getvalue()) < 200_000
    return stream.getvalue()


def xlsx_bytes():
    book = Workbook()
    sheet = book.active; sheet.title = '术语'
    sheet.append(['名称', '说明'])
    sheet.append([None, None])
    sheet.append(['注意力', '关联上下文'])
    sheet.append([None, '保留前置空格列'])
    sheet = book.create_sheet('统计')
    sheet.append(['数量', 3, False, datetime.date(2026, 9, 27)])
    sheet.append(['合计', '=1+2'])
    stream = io.BytesIO(); book.save(stream); book.close()
    assert len(stream.getvalue()) < 200_000
    return stream.getvalue()


def epub_bytes(*, ncx=False, external=False):
    stream = io.BytesIO()
    ns = 'xmlns="http://www.idpf.org/2007/opf"'
    nav_manifest = ('<item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>' if ncx else
                    '<item id="toc" href="toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    # Deliberately differing ZIP and spine orders prove TOC reading order.
    chapter_one = 'https://must-not-fetch.test/ch1.xhtml' if external else 'text/ch1.xhtml'
    files = {
        'mimetype': 'application/epub+zip',
        'META-INF/container.xml': '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        'OEBPS/content.opf': f'''<package {ns} version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>中文课程</dc:title></metadata>
            <manifest>{nav_manifest}<item id="two" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
            <item id="one" href="{chapter_one}" media-type="application/xhtml+xml"/></manifest>
            <spine toc="toc"><itemref idref="two"/><itemref idref="one"/></spine></package>''',
        'OEBPS/text/ch2.xhtml': '<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>第二章 标题</title></head><body><h1>进阶</h1><p>进阶正文。</p></body></html>',
        'OEBPS/text/ch1.xhtml': '''<html><head><title>第一章 标题</title></head><body><nav>导航噪声</nav><main>
            <h1>基础</h1><p>第一章正文。</p><h2>术语</h2><table><tr><td>模型</td><td>定义</td></tr></table>
            <script src="https://must-not-fetch.test/a">脚本噪声</script></main></body></html>''',
    }
    if ncx:
        files['OEBPS/toc.ncx'] = '''<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
            <navPoint><navLabel><text>第一章 基础</text></navLabel><content src="text/ch1.xhtml#intro"/></navPoint>
            <navPoint><navLabel><text>第二章 进阶</text></navLabel><content src="text/ch2.xhtml"/></navPoint></navMap></ncx>'''
    else:
        files['OEBPS/toc.xhtml'] = '''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>
            <nav epub:type="toc"><ol><li><a href="text/ch1.xhtml#intro">第一章 基础</a></li>
            <li><a href="text/ch1.xhtml#terms">术语重复索引</a></li><li><a href="text/ch2.xhtml">第二章 进阶</a></li></ol></nav></body></html>'''
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content.encode('utf-8'))
    assert len(stream.getvalue()) < 200_000
    return stream.getvalue()


def rewrite_zip(data, path, content):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, 'w') as dest:
        for item in source.infolist():
            dest.writestr(item, content if item.filename == path else source.read(item))
    return out.getvalue()


def test_pptx_title_body_notes_group_and_tables():
    result = extract_bytes(pptx_bytes(), 'course.pptx')
    assert result.title == '第一章 模型基础'
    assert result.blocks[0].section == ('第 1 页 第一章 模型基础',)
    assert result.blocks[-1].section == ('第 2 页 第二章 练习',)
    assert all(b.page is None for b in result.blocks)
    assert '组合中的正文' in [b.text for b in result.blocks]
    table = next(b for b in result.blocks if b.kind == 'table')
    assert table.text == '名称 | 作用\n注意力 | 关联上下文'
    notes = next(b for b in result.blocks if '教师备注' in b.text)
    assert notes.section == ('第 1 页 第一章 模型基础', '备注')
    assert sum(b.text == '第一章 模型基础' for b in result.blocks) == 1
    assert_positions(result)


def test_title_only_slides_keep_distinct_chunk_sections_and_blank_slide_warning():
    deck = Presentation()
    for title in ('封面', '第二页', '第三页'):
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
    slide.placeholders[1].text = '第三页正文'
    deck.slides.add_slide(deck.slide_layouts[6])
    stream = io.BytesIO(); deck.save(stream)
    result = extract_bytes(stream.getvalue(), 'slides.pptx')
    assert [c.section for c in result.chunks] == [('第 1 页 封面',), ('第 2 页 第二页',), ('第 3 页 第三页',)]
    assert '第 4 页' in result.warnings[0]
    assert_positions(result)


@pytest.mark.parametrize('ncx', [False, True], ids=['epub3-nav', 'epub2-ncx'])
def test_epub_chapter_order_names_and_html_reuse(ncx):
    result = extract_bytes(epub_bytes(ncx=ncx), 'book.epub')
    assert result.title == '中文课程'
    assert result.blocks[0].section == ('第一章 基础', '基础')
    assert result.blocks[-1].section == ('第二章 进阶', '进阶')
    assert sum(b.text == '第一章正文。' for b in result.blocks) == 1
    assert all('噪声' not in b.text for b in result.blocks)
    table = next(b for b in result.blocks if b.kind == 'table')
    assert table.section == ('第一章 基础', '基础', '术语')
    assert table.text == '模型 | 定义'
    assert_positions(result)


def test_epub_remote_chapters_rejected_without_fetch():
    with pytest.raises(ExtractError) as caught:
        extract_bytes(epub_bytes(external=True), 'book.epub')
    assert caught.value.code == 'CORRUPT' and '包内' in caught.value.message


def test_epub_spine_fallback_and_missing_chapter():
    raw = rewrite_zip(epub_bytes(), 'OEBPS/toc.xhtml', b'<html/>')
    result = extract_bytes(raw, 'book.epub')
    assert result.blocks[0].section[0] == '第二章 标题'
    raw = rewrite_zip(raw, 'OEBPS/content.opf', b'<package><spine><itemref idref="missing"/></spine></package>')
    with pytest.raises(ExtractError) as caught:
        extract_bytes(raw, 'book.epub')
    assert caught.value.code == 'CORRUPT'


def test_xlsx_sheets_empty_rows_types_formula_and_column_positions():
    result = extract_bytes(xlsx_bytes(), 'table.xlsx')
    assert all(b.kind == 'table' and b.page is None for b in result.blocks)
    assert len(result.blocks) == 5
    assert result.blocks[1].text == '注意力 | 关联上下文'
    assert result.blocks[2].text == ' | 保留前置空格列'
    assert result.blocks[3].text.startswith('数量 | 3 | False | 2026-09-27')
    assert result.blocks[-1].text == '合计 | =1+2'
    assert result.blocks[0].section == ('术语',) and result.blocks[-1].section == ('统计',)
    assert '公式' in result.warnings[0]
    assert_positions(result)


def test_xlsx_row_limit_is_inclusive_and_not_silent_truncation(tmp_path):
    raw = xlsx_bytes()
    assert extract_bytes(raw, 'table.xlsx', max_sheet_rows=4).blocks
    path = tmp_path / 'table.xlsx'; path.write_bytes(raw)
    with pytest.raises(ExtractError) as caught:
        extract_file(path, max_sheet_rows=3)
    assert caught.value.code == 'TOO_LARGE' and '术语' in caught.value.message
    for limit in (0, -1, True):
        with pytest.raises(ValueError):
            extract_bytes(raw, 'table.xlsx', max_sheet_rows=limit)


def test_xlsx_stale_dimension_cannot_hide_data():
    import re
    raw = xlsx_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        sheet = archive.read('xl/worksheets/sheet1.xml')
    sheet = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:A1"', sheet)
    raw = rewrite_zip(raw, 'xl/worksheets/sheet1.xml', sheet)
    assert any('注意力' in b.text for b in extract_bytes(raw, 'table.xlsx').blocks)


def test_xlsx_long_row_respects_chunk_limit():
    book = Workbook(); book.active.append(['很长的资料内容' * 600, '第二列'])
    stream = io.BytesIO(); book.save(stream); book.close()
    result = extract_bytes(stream.getvalue(), 'long.xlsx')
    assert len(result.chunks) > 1 and result.warnings
    assert_positions(result)


def test_xlsx_cancel_during_actual_row_iteration(monkeypatch):
    from openpyxl.worksheet._read_only import ReadOnlyWorksheet
    original = ReadOnlyWorksheet.iter_rows
    event = threading.Event()
    def rows(sheet, *args, **kwargs):
        for index, row in enumerate(original(sheet, *args, **kwargs)):
            if index == 1:
                event.set()
            yield row
    monkeypatch.setattr(ReadOnlyWorksheet, 'iter_rows', rows)
    with pytest.raises(ExtractError) as caught:
        extract_bytes(xlsx_bytes(), 'table.xlsx', cancel=event)
    assert caught.value.code == 'CANCELLED'


def test_xlsx_empty_workbook_has_no_synthetic_content():
    book = Workbook(); stream = io.BytesIO(); book.save(stream); book.close()
    with pytest.raises(ExtractError) as caught:
        extract_bytes(stream.getvalue(), 'empty.xlsx')
    assert caught.value.code == 'EMPTY'


def test_epub_traversal_and_entities_do_not_read_files(tmp_path):
    raw = rewrite_zip(epub_bytes(), 'META-INF/container.xml',
        b'<container><rootfiles><rootfile full-path="../outside.opf"/></rootfiles></container>')
    with pytest.raises(ExtractError) as caught:
        extract_bytes(raw, 'book.epub')
    assert caught.value.code == 'CORRUPT'
    secret = tmp_path / 'private.txt'; secret.write_text('PRIVATE-CONTENT', encoding='utf-8')
    raw = rewrite_zip(epub_bytes(), 'OEBPS/toc.xhtml',
        f'''<!DOCTYPE html [<!ENTITY xxe SYSTEM "{secret.as_uri()}">]><html xmlns:epub="http://www.idpf.org/2007/ops">
        <nav epub:type="toc"><a href="text/ch1.xhtml">&xxe;</a></nav></html>'''.encode())
    result = extract_bytes(raw, 'book.epub')
    assert 'PRIVATE-CONTENT' not in repr(result)


def test_zip_expansion_limit(monkeypatch):
    from refactor.learning.course import extract
    monkeypatch.setattr(extract, 'MAX_BYTES', 10)
    with pytest.raises(ExtractError) as caught:
        extract_bytes(epub_bytes(), 'book.epub')
    assert caught.value.code == 'TOO_LARGE' and '解压' in caught.value.message


@pytest.mark.parametrize('filename,factory', [('slides.pptx', pptx_bytes),
    ('book.epub', epub_bytes), ('table.xlsx', xlsx_bytes)])
def test_new_formats_determinism_offsets_and_cancellation(filename, factory, tmp_path):
    raw = factory()
    path = tmp_path / filename; path.write_bytes(raw)
    first = extract_bytes(raw, filename)
    assert first == extract_bytes(raw, filename) == extract_file(path)
    assert_positions(first)
    class StepEvent(threading.Event):
        count = 0
        def is_set(self):
            self.count += 1
            if self.count == 8:
                self.set()
            return super().is_set()
    with pytest.raises(ExtractError) as caught:
        extract_bytes(raw, filename, cancel=StepEvent())
    assert caught.value.code == 'CANCELLED'


@pytest.mark.parametrize('extension', ['pptx', 'epub', 'xlsx'])
def test_corrupt_new_formats(extension):
    with pytest.raises(ExtractError) as caught:
        extract_bytes(b'not an archive', 'broken.' + extension)
    assert caught.value.code == 'CORRUPT'


def test_unknown_extension_still_unsupported():
    with pytest.raises(ExtractError) as caught:
        extract_bytes(b'unsupported', 'old-binary.ppt')
    assert caught.value.code == 'UNSUPPORTED_TYPE'
