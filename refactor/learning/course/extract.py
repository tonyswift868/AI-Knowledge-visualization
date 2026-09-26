"""Offline document text with deterministic, source-relative positions.

The canonical full text is ``'\n\n'.join(block.text for block in blocks)``.
Offsets are Python character indexes into that text, with an exclusive end.
Chunks spanning a heading chain use its common section ancestor.
Separators between distinct chunks need not belong to either chunk. SHA-256
identifies the original bytes (UTF-8 for a str HTML input), not a filename/URL.
Cancellation is cooperative, including between third-party page/paragraph calls.
"""
from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


MAX_BYTES = 50 * 1024 * 1024
_MEDIA = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.md': 'text/markdown', '.markdown': 'text/markdown', '.txt': 'text/plain',
    '.csv': 'text/csv', '.json': 'application/json', '.html': 'text/html', '.htm': 'text/html',
}
_MESSAGES = {
    'UNSUPPORTED_TYPE': '暂不支持此文件格式，请使用 PDF、DOCX、Markdown、TXT、CSV、JSON 或 HTML。',
    'TOO_LARGE': '文件超过允许的大小，请拆分资料后再导入。',
    'ENCRYPTED': 'PDF 已加密，无法用空密码打开，请先解除密码保护。',
    'SCANNED_NO_TEXT': 'PDF 可提取的文字过少，可能是扫描件；本期不支持 OCR，请提供带文字的版本。',
    'CORRUPT': '文件无法读取或内容损坏，请检查格式、编码或重新导出文件。',
    'EMPTY': '资料中没有可提取的正文。',
    'CANCELLED': '正文提取已取消。',
}


@dataclass(frozen=True)
class TextBlock:
    text: str
    kind: str
    page: int | None
    section: tuple[str, ...]


@dataclass(frozen=True)
class SourceChunk:
    chunk_id: str
    text: str
    page_start: int | None
    page_end: int | None
    section: tuple[str, ...]
    char_start: int
    char_end: int


@dataclass(frozen=True)
class ExtractedDocument:
    title: str
    media_type: str
    sha256: str
    char_count: int
    blocks: tuple[TextBlock, ...]
    chunks: tuple[SourceChunk, ...]
    warnings: tuple[str, ...]


class ExtractError(Exception):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or _MESSAGES[code]
        super().__init__(self.message)


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise ExtractError('CANCELLED')


def _newlines(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _decode(data, cancel):
    for encoding in ('utf-8', 'utf-8-sig', 'gb18030'):
        _check(cancel)
        try:
            text = data.decode(encoding).removeprefix('\ufeff')
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ExtractError('CORRUPT', '无法识别文本编码，请使用 UTF-8 或 GB18030 编码。')
    # Retain the existing text importer's rejection of binary/control noise.
    if '\x00' in text:
        raise ExtractError('CORRUPT')
    sample = text[:200_000]
    controls = sum(ord(char) < 32 and char not in '\r\n\t\f' for char in sample)
    if controls > len(sample) * .01:
        raise ExtractError('CORRUPT')
    return _newlines(text)


class _Sections:
    def __init__(self):
        self.headings = []

    @property
    def path(self):
        return tuple(text for _, text in self.headings)

    def heading(self, level, text):
        while self.headings and self.headings[-1][0] >= level:
            self.headings.pop()
        self.headings.append((level, text))
        return self.path


def _paragraphs(text, *, page=None, section=(), cancel=None):
    for paragraph in re.split(r'\n[\t ]*\n+', _newlines(text)):
        _check(cancel)
        paragraph = paragraph.strip()
        if paragraph:
            yield TextBlock(paragraph, 'paragraph', page, section)


def _pdf(data, cancel):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            opened = reader.decrypt('')
        except Exception:
            opened = False
        if not opened:
            raise ExtractError('ENCRYPTED')
    blocks, warnings, effective = [], [], 0
    count = len(reader.pages)
    if not count:
        raise ExtractError('EMPTY')
    for index, page in enumerate(reader.pages, 1):
        _check(cancel)
        text = page.extract_text() or ''
        _check(cancel)
        effective += sum(char.isalnum() for char in text)
        if not text.strip():
            warnings.append(f'第 {index} 页未提取到文字，可能是扫描页或空白页。')
        blocks.extend(_paragraphs(text, page=index, cancel=cancel))
    if effective < 20 * count:
        raise ExtractError('SCANNED_NO_TEXT')
    title = reader.metadata.title if reader.metadata else ''
    return blocks, title if isinstance(title, str) else '', warnings


def _word(data, cancel):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(io.BytesIO(data))
    sections, blocks, title = _Sections(), [], ''
    for element in document.element.body.iterchildren():
        _check(cancel)
        if element.tag == qn('w:p'):
            paragraph = Paragraph(element, document)
            text = _newlines(paragraph.text).strip()
            if not text:
                continue
            style, visited, level = paragraph.style, set(), None
            while style is not None and style.style_id not in visited:
                visited.add(style.style_id)
                match = re.fullmatch(r'(?:Heading|标题)\s*([1-9])', style.name, re.I)
                if match:
                    level = int(match[1])
                    break
                style = style.base_style
            if level:
                sections.heading(level, text)
                title = title or text
            blocks.append(TextBlock(text, 'heading' if level else 'paragraph', None, sections.path))
        elif element.tag == qn('w:tbl'):
            rows = []
            for row in Table(element, document).rows:
                _check(cancel)
                cells = [' '.join(cell.text.split()) for cell in row.cells]
                if any(cells):
                    rows.append(' | '.join(cells))
            if rows:
                blocks.append(TextBlock('\n'.join(rows), 'table', None, sections.path))
    return blocks, title, []


_HEADING = re.compile(r'^ {0,3}(#{1,6})[\t ]+(.+?)\s*$')
_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
_LIST = re.compile(r'^\s*(?:[-+*]|\d+[.)])\s+')
_TABLE_RULE = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$')


def _markdown(text, cancel):
    lines = text.split('\n')
    sections, blocks, warnings, title = _Sections(), [], [], ''
    paragraph = []

    def flush():
        if paragraph:
            value = '\n'.join(paragraph).strip()
            if value:
                blocks.append(TextBlock(value, 'list' if _LIST.match(value) else 'paragraph', None, sections.path))
            paragraph.clear()

    index = 0
    while index < len(lines):
        _check(cancel)
        line = lines[index]
        heading, fence = _HEADING.match(line), _FENCE.match(line)
        if fence:
            flush()
            marker, code = fence[1], []
            index += 1
            while index < len(lines):
                _check(cancel)
                if re.fullmatch(r' {0,3}' + re.escape(marker[0]) + '{' + str(len(marker)) + r',}\s*', lines[index]):
                    break
                code.append(lines[index]); index += 1
            else:
                warnings.append('Markdown 代码块未闭合，已保留其后的代码正文。')
            value = '\n'.join(code).strip('\n')
            if value.strip():
                blocks.append(TextBlock(value, 'code', None, sections.path))
        elif heading:
            flush()
            value = re.sub(r'[\t ]+#+[\t ]*$', '', heading[2]).strip()
            title = title or value
            blocks.append(TextBlock(value, 'heading', None, sections.heading(len(heading[1]), value)))
        elif index + 1 < len(lines) and '|' in line and _TABLE_RULE.fullmatch(lines[index + 1]):
            flush()
            table = [line]
            index += 1
            while index < len(lines) and lines[index].strip() and '|' in lines[index]:
                _check(cancel)
                table.append(lines[index]); index += 1
            blocks.append(TextBlock('\n'.join(table), 'table', None, sections.path))
            continue
        elif not line.strip():
            flush()
        else:
            if paragraph and bool(_LIST.match(line)) != bool(_LIST.match(paragraph[0])):
                flush()
            paragraph.append(line)
        index += 1
    flush()
    return blocks, title, warnings


_HTML_NOISE = frozenset('script style nav header footer aside form noscript iframe object embed svg canvas template button input select textarea'.split())
_HTML_NOISE_NAMES = frozenset('nav navbar navigation sidebar breadcrumbs breadcrumb site-header site-footer cookie-banner advertisement'.split())
_HTML_CONTAINER = frozenset('html body main article section div p h1 h2 h3 h4 h5 h6 ul ol li dl dt dd blockquote pre table figure figcaption hr address'.split())


def _html(html, cancel):
    from lxml import html as parser

    root = parser.document_fromstring(html, parser=parser.HTMLParser(no_network=True, remove_comments=True))
    title_nodes = root.xpath('//title')
    title = ' '.join(title_nodes[0].text_content().split()) if title_nodes else ''
    for node in list(root.iter()):
        _check(cancel)
        names = set((node.get('class', '') + ' ' + node.get('id', '')).lower().split())
        if node.tag in _HTML_NOISE or names & _HTML_NOISE_NAMES or node.get('role', '').lower() in {'navigation', 'banner', 'contentinfo', 'complementary', 'search'} or node.get('hidden') is not None or node.get('aria-hidden') == 'true':
            if node.getparent() is not None:
                node.drop_tree()
    mains = root.xpath('//main')
    articles = root.xpath('//article[not(ancestor::article)]')
    roots = mains[:1] or articles or root.xpath('//body') or [root]
    if not title:
        headings = [heading for node in roots for heading in node.xpath('.//h1')]
        title = ' '.join(headings[0].text_content().split()) if headings else ''
    sections, blocks = _Sections(), []

    def inline(node):
        _check(cancel)
        parts = [node.text or '']
        for child in node:
            parts.append('\n' if child.tag == 'br' else inline(child))
            parts.append(child.tail or '')
        return ''.join(parts)

    def add(value, kind='paragraph'):
        value = _newlines(value).strip('\n') if kind == 'code' else ' '.join(value.split())
        if value.strip():
            blocks.append(TextBlock(value, kind, None, sections.path))

    def walk(node):
        _check(cancel)
        tag = node.tag
        if re.fullmatch(r'h[1-6]', tag):
            value = ' '.join(inline(node).split())
            if value:
                sections.heading(int(tag[1]), value)
                add(value, 'heading')
        elif tag == 'pre':
            add(inline(node), 'code')
        elif tag == 'table':
            rows = []
            for row in node.xpath('.//tr'):
                _check(cancel)
                if row.xpath('ancestor::table[1]')[0] is not node:
                    continue
                cells = [' '.join(inline(cell).split()) for cell in row if cell.tag in ('td', 'th')]
                if any(cells):
                    rows.append(' | '.join(cells))
            if rows:
                blocks.append(TextBlock('\n'.join(rows), 'table', None, sections.path))
        elif tag in ('p', 'li', 'dt', 'dd'):
            add(inline(node), 'list' if tag == 'li' else 'paragraph')
        else:
            parts = [node.text or '']
            for child in node:
                _check(cancel)
                if child.tag in _HTML_CONTAINER:
                    add(''.join(parts)); parts.clear()
                    walk(child)
                else:
                    parts.append('\n' if child.tag == 'br' else inline(child))
                parts.append(child.tail or '')
            add(''.join(parts))

    for node in roots:
        walk(node)
    return blocks, title, []


def _common_section(sections):
    result = sections[0]
    for section in sections[1:]:
        end = 0
        while end < min(len(result), len(section)) and result[end] == section[end]:
            end += 1
        result = result[:end]
    return result


def _split(text, start, end, *, target, maximum, lines_only, cancel, first_min=0):
    while end - start > maximum:
        _check(cancel)
        limit = start + maximum
        if lines_only:
            cut = text.rfind('\n', start, limit) + 1
        else:
            desired = start + target
            floor = min(limit, max(start + 1, first_min))
            bounds = [m.end() + start for m in re.finditer(r'[。！？.!?\n][”’"\']?', text[start:limit]) if m.end() + start >= floor]
            before = [point for point in bounds if point <= desired]
            cut = before[-1] if before else bounds[0] if bounds else max(desired, floor)
        if cut <= start:
            cut = limit  # A single oversized code/table line still obeys max_chars.
        yield start, cut
        start = cut
    if start < end:
        yield start, end


def _chunks(blocks, *, source_sha256, target_chars, max_chars, cancel=None):
    if (type(target_chars) is not int or type(max_chars) is not int
            or not 0 < target_chars <= max_chars):
        raise ValueError('需要 0 < target_chars <= max_chars')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', source_sha256):
        raise ValueError('source_sha256 必须是完整的 SHA-256 十六进制摘要')
    blocks = tuple(blocks)
    spans, position = [], 0
    for block in blocks:
        _check(cancel)
        spans.append((position, position + len(block.text), block))
        position += len(block.text) + 2
    text = '\n\n'.join(block.text for block in blocks)
    units, index = [], 0
    while index < len(spans):
        _check(cancel)
        start, end, block = spans[index]
        members = [block]
        first_min = 0
        # Group a heading chain and its first prose block before normal packing.
        if block.kind == 'heading':
            while index + 1 < len(spans) and spans[index + 1][2].kind == 'heading':
                index += 1
                body_start, end, following = spans[index]; members.append(following)
                first_min = body_start + 1
            if index + 1 < len(spans) and spans[index + 1][2].kind in {'paragraph', 'list'}:
                index += 1
                body_start, end, following = spans[index]; members.append(following)
                first_min = body_start + 1
        section = _common_section([member.section for member in members])
        atomic = block.kind in {'table', 'code'}
        for left, right in _split(text, start, end, target=target_chars, maximum=max_chars, lines_only=atomic, cancel=cancel, first_min=first_min):
            if text[left:right].strip():
                units.append((left, right, section))
        index += 1
    ranges, current = [], None
    for start, end, section in units:
        _check(cancel)
        if current is not None:
            left, right, previous = current
            if previous != section or end - left > max_chars or right - left >= target_chars:
                ranges.append(current); current = None
        current = (start, end, section) if current is None else (current[0], end, section)
    if current is not None:
        ranges.append(current)
    chunks, span_index = [], 0
    for number, (start, end, section) in enumerate(ranges, 1):
        _check(cancel)
        while span_index < len(spans) and spans[span_index][1] <= start:
            span_index += 1
        pages, cursor = [], span_index
        while cursor < len(spans) and spans[cursor][0] < end:
            _check(cancel)
            if spans[cursor][2].page is not None:
                pages.append(spans[cursor][2].page)
            cursor += 1
        chunks.append(SourceChunk(f'{source_sha256[:12].lower()}:{number:04d}', text[start:end],
            min(pages) if pages else None, max(pages) if pages else None, section, start, end))
    return tuple(chunks)


def chunk_blocks(blocks, *, source_sha256: str, target_chars=1200, max_chars=2000) -> tuple[SourceChunk, ...]:
    return _chunks(blocks, source_sha256=source_sha256, target_chars=target_chars, max_chars=max_chars)


def _document(data, title, media_type, blocks, warnings, cancel):
    _check(cancel)
    blocks = tuple(block for block in blocks if block.text.strip())
    if not blocks:
        raise ExtractError('EMPTY')
    digest = hashlib.sha256(data).hexdigest()
    chunks = _chunks(blocks, source_sha256=digest, target_chars=1200, max_chars=2000, cancel=cancel)
    if any(block.kind in {'code', 'table'} and len(block.text) > 2000 for block in blocks):
        warnings.append('超长代码或表格已按行分块；单行超过上限时按字符切分，原始正文仍完整保留。')
    _check(cancel)
    return ExtractedDocument(title.strip(), media_type, digest,
        sum(len(block.text) for block in blocks) + 2 * (len(blocks) - 1), blocks, chunks, tuple(warnings))


def _size(size, max_bytes):
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError('max_bytes 必须是非负整数')
    if size > max_bytes:
        raise ExtractError('TOO_LARGE')


def extract_bytes(data: bytes, filename: str, *, max_bytes=MAX_BYTES, cancel=None) -> ExtractedDocument:
    _check(cancel)
    _size(len(data), max_bytes)
    suffix = Path(filename).suffix.lower()
    if suffix not in _MEDIA:
        raise ExtractError('UNSUPPORTED_TYPE')
    if not data:
        raise ExtractError('EMPTY')
    try:
        if suffix == '.pdf':
            blocks, title, warnings = _pdf(data, cancel)
        elif suffix == '.docx':
            blocks, title, warnings = _word(data, cancel)
        else:
            text = _decode(data, cancel)
            if not text.strip():
                raise ExtractError('EMPTY')
            title, warnings = '', []
            if suffix in ('.md', '.markdown'):
                blocks, title, warnings = _markdown(text, cancel)
            elif suffix in ('.html', '.htm'):
                blocks, title, warnings = _html(text, cancel)
            elif suffix == '.json':
                json.loads(text)
                blocks = [TextBlock(text.strip(), 'code', None, ())]
            elif suffix == '.csv':
                rows = []
                for row in csv.reader(io.StringIO(text), strict=True):
                    _check(cancel)
                    if any(cell.strip() for cell in row):
                        rows.append(' | '.join(cell.strip() for cell in row))
                blocks = [TextBlock('\n'.join(rows), 'table', None, ())] if rows else []
            else:
                blocks = list(_paragraphs(text, cancel=cancel))
        return _document(data, title or Path(filename).stem, _MEDIA[suffix], blocks, warnings, cancel)
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError('CORRUPT') from exc


def extract_file(path, *, max_bytes=MAX_BYTES, cancel=None) -> ExtractedDocument:
    _check(cancel)
    path = Path(path)
    if path.suffix.lower() not in _MEDIA:
        raise ExtractError('UNSUPPORTED_TYPE')
    try:
        _size(path.stat().st_size, max_bytes)
        data = bytearray()
        with path.open('rb') as stream:
            while True:
                _check(cancel)
                part = stream.read(min(64 * 1024, max_bytes - len(data) + 1))
                if not part:
                    break
                data.extend(part)
                _size(len(data), max_bytes)
        return extract_bytes(bytes(data), path.name, max_bytes=max_bytes, cancel=cancel)
    except OSError as exc:
        raise ExtractError('CORRUPT', '无法读取此资料文件，请确认文件存在且可以访问。') from exc


def extract_html(html: str | bytes, url: str, *, cancel=None) -> ExtractedDocument:
    """Extract supplied markup only. The URL is a title fallback, never fetched."""
    _check(cancel)
    data = html.encode('utf-8') if isinstance(html, str) else html
    _size(len(data), MAX_BYTES)
    if not data or not data.strip():
        raise ExtractError('EMPTY')
    try:
        blocks, title, warnings = _html(_decode(data, cancel), cancel)
        location = urlsplit(url)
        fallback = unquote(location.path.rstrip('/').split('/')[-1]) or location.hostname or '网页资料'
        return _document(data, title or fallback, 'text/html', blocks, warnings, cancel)
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError('CORRUPT') from exc
