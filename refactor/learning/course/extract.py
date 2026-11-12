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
from contextlib import ExitStack
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import posixpath
import unicodedata
from urllib.parse import unquote, urlsplit
import zipfile


MAX_BYTES = 50 * 1024 * 1024
_MEDIA = {
    '.pdf': 'application/pdf',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.webp': 'image/webp', '.bmp': 'image/bmp',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.md': 'text/markdown', '.markdown': 'text/markdown', '.txt': 'text/plain',
    '.csv': 'text/csv', '.json': 'application/json', '.html': 'text/html', '.htm': 'text/html',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.epub': 'application/epub+zip',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}
_MESSAGES = {
    'UNSUPPORTED_TYPE': '暂不支持此文件格式，请使用 PDF、PNG、JPG/JPEG、WEBP、BMP、DOCX、PPTX、EPUB、XLSX、Markdown、TXT、CSV、JSON 或 HTML。',
    'TOO_LARGE': '文件超过允许的大小，请拆分资料后再导入。',
    'ENCRYPTED': 'PDF 已加密，无法用空密码打开，请先解除密码保护。',
    'SCANNED_NO_TEXT': 'PDF 可提取的文字过少，可能是扫描件；资料问答会自动尝试离线 OCR。',
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


def _pdf(data, cancel, ocr=None, progress=None):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            opened = reader.decrypt('')
        except Exception:
            opened = False
        if not opened:
            raise ExtractError('ENCRYPTED')
    blocks, warnings, effective, recognized_text = [], [], 0, False
    count = len(reader.pages)
    if not count:
        raise ExtractError('EMPTY')
    if ocr is not None:
        ocr.check_page_count(count)
    # pypdf owns bookmark parsing. The supplied chapter hierarchy is carried
    # into the existing blocks/chunks; PDFs without bookmarks keep the fallback.
    bookmarks = {}
    pending = [(reader.outline, ())]
    while pending:
        entries, parent = pending.pop()
        current = parent
        for entry in entries:
            _check(cancel)
            if isinstance(entry, list):
                pending.append((entry, current))
            else:
                current = parent + (entry.title,)
                page_number = reader.get_destination_page_number(entry)
                if page_number is not None: bookmarks.setdefault(page_number + 1, []).append(current)
    section = ()
    with ExitStack() as stack:
        recognizer = None
        for index, page in enumerate(reader.pages, 1):
            _check(cancel)
            text = clean_text(page.extract_text() or '')
            _check(cancel)
            # Scanner watermarks are not a body text layer. Do not use a general
            # short-text threshold: even a brief real heading must stay native.
            watermark_only = (ocr is not None and
                re.sub(r'\s+', '', text).casefold() in {'扫描全能王创建', 'camscanner', 'scannedwithcamscanner'}
                and bool(page.images))
            if ocr is not None and (not text.strip() or watermark_only):
                if recognizer is None:
                    recognizer = stack.enter_context(ocr.open_pdf(data, cancel=cancel))
                text = recognizer.recognize_page(index - 1, cancel=cancel)
                recognized_text |= any(char.isalnum() for char in text)
                _check(cancel)
                warnings.append(f'第 {index} 页已使用离线 OCR 识别，识别文字可能有误，请核对原页。')
            effective += sum(char.isalnum() for char in text)
            if not text.strip():
                warnings.append(f'第 {index} 页未提取到文字，可能是扫描页或空白页。')
            if index in bookmarks:
                section = max(bookmarks[index], key=len)
            blocks.extend(_paragraphs(text, page=index, section=section, cancel=cancel))
            if progress is not None:
                progress(index, count)
            _check(cancel)
    if effective < 20 * count and not recognized_text:
        raise ExtractError('SCANNED_NO_TEXT')
    title = reader.metadata.title if reader.metadata else ''
    return blocks, title if isinstance(title, str) else '', warnings


def _image(data, filename, cancel, ocr=None, progress=None):
    if ocr is None:
        raise ExtractError('SCANNED_NO_TEXT', '图片需要离线 OCR 才能提取文字。')
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            text = ocr.recognize_image(image, cancel=cancel)
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError('CORRUPT', '图片无法读取或内容损坏，请检查格式后重试。') from exc
    if progress is not None:
        progress(1, 1)
    if not text.strip():
        raise ExtractError('EMPTY', '图片里没认出文字。')
    return [TextBlock(text, 'paragraph', 1, ())], Path(filename).stem, []


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
            text = _newlines(_word_text(element, cancel)).strip()
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
                cells = [' '.join('\n'.join(_word_text(p._p, cancel) for p in cell.paragraphs).split()) for cell in row.cells]
                if any(cells):
                    rows.append(' | '.join(cells))
            if rows:
                blocks.append(TextBlock('\n'.join(rows), 'table', None, sections.path))
    return blocks, title, []


def _word_text(node, cancel):
    """python-docx paragraph.text omits Office Math (including fractions)."""
    _check(cancel)
    tag = str(node.tag).rsplit('}', 1)[-1]
    if tag in ('oMath', 'oMathPara'):
        return _math_text(_word_math(node, cancel), cancel)
    if tag in ('pPr', 'rPr', 'drawing', 'pict'):
        return ''
    if tag == 't':
        return node.text or ''
    if tag == 'tab':
        return '\t'
    if tag in ('br', 'cr'):
        return '\n'
    return ''.join(_word_text(child, cancel) for child in node)


def _word_math(node, cancel):
    """Convert common Office Math structures to the same linear serializer."""
    from lxml import etree
    _check(cancel)
    tag = str(node.tag).rsplit('}', 1)[-1]
    children = [c for c in node if not str(c.tag).rsplit('}', 1)[-1].endswith('Pr')]
    mapped = dict(f='mfrac', sSup='msup', sSub='msub', sSubSup='msubsup', t='mtext', oMath='math', oMathPara='math')
    if tag == 'rad':
        degree = next((c for c in children if str(c.tag).endswith('}deg')), None)
        base = next((c for c in children if str(c.tag).endswith('}e')), None)
        has_degree = degree is not None and ''.join(degree.itertext()).strip()
        result = etree.Element('mroot' if has_degree else 'msqrt')
        if base is not None: result.append(_word_math(base, cancel))
        if has_degree: result.append(_word_math(degree, cancel))
        return result
    result = etree.Element(mapped.get(tag, 'mrow'))
    if tag == 't': result.text = node.text or ''
    for child in children:
        result.append(_word_math(child, cancel))
    return result


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


def useful_title(title):
    return bool(title and title.strip().casefold() not in
                {'标题', '图文详情', '图文详情——科普中国资源服务', 'untitled', 'document', 'letter', '首页', '在线预览'})


def clean_page_text(text):
    """Remove standalone page boilerplate, never words inside lesson sentences."""
    lines = []
    for line in text.splitlines():
        if line.strip() in {'上一小节', '下一小节', '上一篇', '下一篇', '返回首页', '返回顶部', '打印本页'}:
            continue
        if re.match(r'^\s*(?:版权声明|免责声明|侵权联系|版权所有|Copyright\s*(?:©|20\d\d)|'
                    r'All rights reserved|联系邮箱\s*[:：]|购买提示\s*[:：])', line, re.I):
            continue
        if re.fullmatch(r'\s*[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\s*', line):
            continue
        lines.append(line)
    result = '\n'.join(lines).strip()
    # Responsive/print copies can duplicate the entire article. Only remove
    # an exact, substantial repeated span; mathematical repeated symbols stay.
    for copies in range(4, 1, -1):
        compact = result.strip()
        if len(compact) % copies == 0:
            part = compact[:len(compact)//copies]
            if len(part.strip()) >= 150 and part * copies == compact:
                return part.strip()
    nonempty = [line for line in result.splitlines() if line.strip()]
    for copies in range(4, 1, -1):
        size = len(nonempty)//copies
        if size and len(nonempty) == size * copies and nonempty == nonempty[:size] * copies:
            if len('\n'.join(nonempty[:size])) >= 150:
                return '\n'.join(nonempty[:size])
    return result
_HTML_CONTAINER = frozenset('html body main article section div p h1 h2 h3 h4 h5 h6 ul ol li dl dt dd blockquote pre table figure figcaption hr address'.split())


def _math_text(node, cancel):
    """Serialize presentation MathML, never concatenate its alternate encodings."""
    _check(cancel)
    tag = str(node.tag).rsplit('}', 1)[-1].rsplit(':', 1)[-1].lower()
    children = list(node)
    if tag in ('annotation', 'annotation-xml', 'mphantom'):
        return ''
    if tag == 'semantics':
        presentation = [c for c in children if str(c.tag).rsplit('}', 1)[-1] not in ('annotation', 'annotation-xml')]
        return _math_text(presentation[0], cancel) if presentation else ''
    values = [_math_text(c, cancel) for c in children]
    def operand(value):
        return value if re.fullmatch(r'[−+\-]?[\w.]+', value) else '(' + value + ')'
    if tag == 'mfrac' and len(values) == 2:
        return operand(values[0]) + '/' + operand(values[1])
    if tag in ('msup', 'msub') and len(values) == 2:
        # Presentation mtext is literal typography, not an algebraic mrow.
        # OpenStax prints N/m with a superscript 2 immediately after m (also
        # lb/in followed by 2). Adding parentheses invents (N/m)^2 and changes
        # the printed pressure unit. Keep this limited to literal unit ratios;
        # structured fractions/compound algebra still use operand grouping.
        base_tag=str(children[0].tag).rsplit('}',1)[-1].rsplit(':',1)[-1].lower()
        if (tag=='msup' and base_tag=='mtext' and re.fullmatch(r'(?:N|J|W|kg|lb)/(?:m|s|in)',values[0])
                and re.fullmatch(r'\d+',values[1])):
            return values[0]+'^'+values[1]
        return operand(values[0]) + ('^' if tag == 'msup' else '_') + operand(values[1])
    if tag == 'msubsup' and len(values) == 3:
        return operand(values[0]) + '_' + operand(values[1]) + '^' + operand(values[2])
    if tag == 'msqrt':
        return '√(' + ''.join(values) + ')'
    if tag == 'mroot' and len(values) == 2:
        return 'root(' + values[1] + ', ' + values[0] + ')'
    if tag == 'mfenced':
        separators = node.get('separators', ',').strip() or ','
        joined = ''.join(v + (separators[min(i, len(separators)-1)] if i < len(values)-1 else '') for i, v in enumerate(values))
        return node.get('open', '(') + joined + node.get('close', ')')
    if tag in ('munder', 'mover', 'munderover') and len(values) >= 2:
        marks = ('_', '^') if tag != 'mover' else ('^',)
        return values[0] + ''.join(mark + operand(v) for mark, v in zip(marks, values[1:]))
    if tag in ('mtable', 'mtr', 'mlabeledtr'):
        return ('; ' if tag == 'mtable' else ' | ').join(values)
    if tag == 'mspace':
        return ' '
    # XML indentation is not mathematical spacing. Actual mtext/mspace is.
    parts = [node.text if node.text and node.text.strip() else '']
    previous = None
    previous_value = ''
    for child, value in zip(children, values):
        child_tag = str(child.tag).rsplit('}', 1)[-1].rsplit(':', 1)[-1].lower()
        if child_tag == 'mfrac' and re.fullmatch(r'[−+\-]?\d+(?:\.\d+)?', previous_value):
            parts.append(' ')  # mixed number: 1 1/2, never 11/2
        elif child_tag == 'mi' and previous == 'mfrac':
            parts[-1] = '(' + parts[-1] + ')'
        parts.append(value)
        if child.tail and child.tail.strip():
            parts.append(child.tail)
        previous = child_tag
        previous_value = value
    return ''.join(parts)


def clean_text(text: str) -> str:
    """Repair what PDF text layers emit for math letters (BUG-43).

    pypdf can return mathematical italic letters as UTF-16 surrogate halves;
    lone halves cannot be encoded and broke saving a plan draft. Valid pairs
    are joined, lone halves become U+FFFD, and the Mathematical Alphanumeric
    Symbols block is folded to plain letters. Chinese full-width punctuation
    and every other character are left untouched.
    """
    if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        text = text.encode('utf-16', 'surrogatepass').decode('utf-16', 'replace')
    if any(0x1D400 <= ord(char) <= 0x1D7FF for char in text):
        text = ''.join(unicodedata.normalize('NFKC', char) if 0x1D400 <= ord(char) <= 0x1D7FF else char
                       for char in text)
    return text


def _normalize_html_math(root, cancel):
    from lxml import etree
    # MathJax/KaTeX carry a rendered tree plus hidden accessible MathML.
    # Prefer that structured tree, before the ordinary noise/aria filter.
    wrappers = root.xpath("//mjx-container | //span[contains(concat(' ', normalize-space(@class), ' '), ' katex ')]")
    for node in wrappers:
        math = node.xpath('.//math')
        if math and node.getparent() is not None:
            replacement = etree.Element('span')
            replacement.text, replacement.tail = _math_text(math[0], cancel), node.tail
            node.getparent().replace(node, replacement)
    for node in list(root.xpath('//math[not(ancestor::math)]')):
        _check(cancel)
        if node.getparent() is None:
            continue
        replacement = etree.Element('span')
        replacement.text, replacement.tail = _math_text(node, cancel), node.tail
        parent = node.getparent()
        # Some publishers also place a textual screen-reader copy alongside
        # MathML. Remove only marked copies in this formula's own wrapper.
        for sibling in list(parent) if parent.tag == 'span' else []:
            names = set(sibling.get('class', '').lower().split())
            if sibling is not node and names & {'sr-only', 'visually-hidden', 'screen-reader-only', 'mjx-assistive-mml'}:
                sibling.drop_tree()
        parent.replace(node, replacement)


def _html(html, cancel):
    from lxml import html as parser

    # XHTML inside EPUB commonly declares an encoding; decoding is already done.
    html = re.sub(r'^\s*<\?xml\b[^>]*\?>', '', html, count=1, flags=re.I)
    root = parser.document_fromstring(html, parser=parser.HTMLParser(no_network=True, remove_comments=True))
    title_nodes = root.xpath('//title')
    title = ' '.join(title_nodes[0].text_content().split()) if title_nodes else ''
    _normalize_html_math(root, cancel)
    for node in list(root.iter()):
        _check(cancel)
        names = set((node.get('class', '') + ' ' + node.get('id', '')).lower().split())
        noise_name = any(re.fullmatch(r'(?:site[-_])?(?:nav|navigation|menu|footer|copyright|disclaimer|toolbar|breadcrumbs?)(?:[-_].*)?', name) for name in names)
        if node.tag in _HTML_NOISE or names & _HTML_NOISE_NAMES or noise_name or node.get('role', '').lower() in {'navigation', 'banner', 'contentinfo', 'complementary', 'search'} or node.get('hidden') is not None or node.get('aria-hidden') == 'true':
            if node.getparent() is not None:
                node.drop_tree()
    mains = root.xpath('//main')
    articles = root.xpath('//article[not(ancestor::article)]')
    roots = mains[:1] or articles or root.xpath('//body') or [root]
    if not useful_title(title):
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
        if kind != 'code':
            value = clean_page_text(value)
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
    # Two copies of the same article (desktop/mobile or print layout).
    for copies in range(4, 1, -1):
        size = len(blocks)//copies
        keys = [(b.kind, b.text) for b in blocks]
        if size and len(blocks) == size * copies and keys == keys[:size] * copies and sum(len(b.text) for b in blocks[:size]) >= 150:
            blocks = blocks[:size]
            break
    seen, unique = set(), []
    for block in blocks:
        key = (block.kind, block.text)
        if len(block.text) >= 180 and key in seen:
            continue
        seen.add(key)
        unique.append(block)
    return unique, title, []


def _archive_guard(data, cancel):
    # These formats are ZIP containers. Bound expansion before library parsing.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        size = 0
        for item in archive.infolist():
            _check(cancel)
            size += item.file_size
            if size > 4 * MAX_BYTES:
                raise ExtractError('TOO_LARGE', '资料解压后的内容超过 200 MiB，请拆分后导入。')
            if item.flag_bits & 1:
                raise ExtractError('CORRUPT', '无法读取受密码保护的资料压缩内容，请解除保护后重试。')


def _slides(data, cancel):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    _archive_guard(data, cancel)
    presentation = Presentation(io.BytesIO(data))
    blocks, title, warnings = [], '', []

    def shapes(items, section, title_id=None):
        for shape in items:
            _check(cancel)
            if shape.shape_id == title_id:
                continue
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                shapes(shape.shapes, section)
            elif shape.has_table:
                rows = []
                for row in shape.table.rows:
                    _check(cancel)
                    cells = [' '.join(cell.text.split()) if not cell.is_spanned else '' for cell in row.cells]
                    if any(cells):
                        rows.append(' | '.join(cells))
                if rows:
                    blocks.append(TextBlock('\n'.join(rows), 'table', None, section))
            elif shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    _check(cancel)
                    text = _newlines(paragraph.text).strip()
                    if text:
                        blocks.append(TextBlock(text, 'list' if paragraph.level else 'paragraph', None, section))

    for number, slide in enumerate(presentation.slides, 1):
        _check(cancel)
        heading = slide.shapes.title
        name = ' '.join(heading.text.split()) if heading is not None else ''
        title = title or name
        label = f'第 {number} 页' + (f' {name}' if name else '')
        section = (label,)
        start = len(blocks)
        if name:
            blocks.append(TextBlock(name, 'heading', None, section))
        shapes(slide.shapes, section, heading.shape_id if heading is not None else None)
        # Accessing notes_slide unconditionally would create a new notes part.
        if slide.has_notes_slide:
            frame = slide.notes_slide.notes_text_frame
            if frame is not None and frame.text.strip():
                notes_section = section + ('备注',)
                blocks.append(TextBlock('备注', 'heading', None, notes_section))
                blocks.extend(_paragraphs(frame.text, section=notes_section, cancel=cancel))
        if len(blocks) == start:
            warnings.append(f'第 {number} 页幻灯片没有可提取的文字；图片文字不在 PPTX 提取范围内。')
    return blocks, title, warnings


def _workbook(data, cancel, max_rows):
    from openpyxl import load_workbook

    _archive_guard(data, cancel)
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
    blocks, warnings = [], []
    try:
        for sheet in workbook.worksheets:
            _check(cancel)
            # Ignore stale/forged dimensions. Stream actual rows, including gaps.
            sheet.reset_dimensions()
            has_formula = False
            for number, row in enumerate(sheet.iter_rows(), 1):
                _check(cancel)
                if number > max_rows:
                    raise ExtractError('TOO_LARGE',
                        f'工作表“{sheet.title}”超过 {max_rows} 行上限，请拆分或调整 max_sheet_rows。')
                cells = []
                for cell in row:
                    _check(cancel)
                    value = cell.value
                    has_formula |= cell.data_type == 'f'
                    if value is None:
                        text = ''
                    elif hasattr(value, 'isoformat'):
                        text = value.isoformat()
                    else:
                        text = str(value).strip()
                    cells.append(text)
                while cells and not cells[-1]:
                    cells.pop()
                if any(cells):
                    blocks.append(TextBlock(' | '.join(cells), 'table', None, (sheet.title,)))
            if has_formula:
                warnings.append(f'工作表“{sheet.title}”保留公式原文，未执行公式或读取外部链接。')
        return blocks, workbook.properties.title or '', warnings
    finally:
        workbook.close()


def _epub(data, cancel):
    from lxml import etree

    _archive_guard(data, cancel)
    blocks, warnings = [], []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        def xml(path):
            _check(cancel)
            return etree.fromstring(archive.read(path), etree.XMLParser(
                resolve_entities=False, no_network=True, load_dtd=False))

        def location(base, href):
            target = urlsplit(href)
            path = unquote(target.path)
            if target.scheme or target.netloc or not path or '\\' in path or path.startswith('/'):
                raise ExtractError('CORRUPT', 'EPUB 正文章节必须指向包内文件。')
            path = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
            if path == '..' or path.startswith('../'):
                raise ExtractError('CORRUPT', 'EPUB 章节路径无效。')
            return path

        container = xml('META-INF/container.xml')
        roots = container.findall('.//{*}rootfile')
        if not roots:
            raise ExtractError('CORRUPT')
        package_path = location('', roots[0].get('full-path', ''))
        package = xml(package_path)
        names = package.findall('./{*}metadata/{*}title')
        title = ''.join(names[0].itertext()).strip() if names else ''
        manifest = {item.get('id'): item for item in package.findall('./{*}manifest/{*}item')}
        spine = package.find('./{*}spine')
        if spine is None:
            raise ExtractError('CORRUPT')
        chapters = []
        for ref in spine.findall('./{*}itemref'):
            _check(cancel)
            item = manifest[ref.get('idref')]
            if ref.get('linear', 'yes') == 'no' or 'nav' in item.get('properties', '').split():
                continue
            if item.get('media-type') in {'application/xhtml+xml', 'text/html'}:
                path = location(package_path, item.get('href', ''))
                if path not in chapters:
                    chapters.append(path)
        # TOC order and labels; unlisted reading-order chapters follow the TOC.
        toc = {}
        nav_item = next((v for v in manifest.values() if 'nav' in v.get('properties', '').split()), None)
        if nav_item is not None:
            nav_path = location(package_path, nav_item.get('href', ''))
            nav_root = xml(nav_path)
            navs = nav_root.xpath('//*[local-name()="nav"]')
            for nav in navs:
                kind = nav.get('{http://www.idpf.org/2007/ops}type', '').split()
                if 'toc' not in kind and nav.get('role') != 'doc-toc':
                    continue
                for link in nav.xpath('.//*[local-name()="a"][@href]'):
                    _check(cancel)
                    path = location(nav_path, link.get('href'))
                    label = ' '.join(''.join(link.itertext()).split())
                    if label and path in chapters:
                        toc.setdefault(path, label)
        elif spine.get('toc'):
            ncx_item = manifest[spine.get('toc')]
            ncx_path = location(package_path, ncx_item.get('href', ''))
            for point in xml(ncx_path).findall('.//{*}navPoint'):
                _check(cancel)
                content = point.find('./{*}content')
                label_node = point.find('./{*}navLabel/{*}text')
                if content is not None and label_node is not None:
                    path = location(ncx_path, content.get('src', ''))
                    label = ' '.join(''.join(label_node.itertext()).split())
                    if label and path in chapters:
                        toc.setdefault(path, label)
        ordered = list(toc) + [path for path in chapters if path not in toc]
        for number, path in enumerate(ordered, 1):
            _check(cancel)
            content, chapter_title, _ = _html(_decode(archive.read(path), cancel), cancel)
            if not content:
                warnings.append(f'第 {number} 章没有可提取的正文。')
                continue
            name = toc.get(path) or chapter_title or f'第 {number} 章'
            for block in content:
                _check(cancel)
                section = block.section if block.section[:1] == (name,) else (name,) + block.section
                blocks.append(TextBlock(block.text, block.kind, None, section))
        return blocks, title, warnings


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
                next_section = spans[index + 1][2].section
                if next_section[:len(members[-1].section)] != members[-1].section:
                    break  # A new slide/chapter is not a child heading.
                index += 1
                body_start, end, following = spans[index]; members.append(following)
                first_min = body_start + 1
            if (index + 1 < len(spans) and spans[index + 1][2].kind in {'paragraph', 'list'}
                    and spans[index + 1][2].section[:len(members[-1].section)] == members[-1].section):
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


def extract_bytes(data: bytes, filename: str, *, max_bytes=MAX_BYTES, cancel=None,
                  ocr=None, progress=None, max_sheet_rows=10_000) -> ExtractedDocument:
    """OCR is opt-in with course.ocr.PdfOCR(); PDF progress is (page, total).

    max_sheet_rows limits physical rows per sheet (including empty rows), raising
    TOO_LARGE rather than silently returning truncated workbook content.
    """
    _check(cancel)
    _size(len(data), max_bytes)
    suffix = Path(filename).suffix.lower()
    if suffix not in _MEDIA:
        raise ExtractError('UNSUPPORTED_TYPE')
    if not data:
        raise ExtractError('EMPTY')
    if type(max_sheet_rows) is not int or max_sheet_rows < 1:
        raise ValueError('max_sheet_rows 必须是正整数')
    try:
        if suffix == '.pdf':
            blocks, title, warnings = _pdf(data, cancel, ocr, progress)
        elif suffix in {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}:
            blocks, title, warnings = _image(data, filename, cancel, ocr, progress)
        elif suffix == '.docx':
            blocks, title, warnings = _word(data, cancel)
        elif suffix == '.pptx':
            blocks, title, warnings = _slides(data, cancel)
        elif suffix == '.epub':
            blocks, title, warnings = _epub(data, cancel)
        elif suffix == '.xlsx':
            blocks, title, warnings = _workbook(data, cancel, max_sheet_rows)
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


def extract_file(path, *, max_bytes=MAX_BYTES, cancel=None, ocr=None, progress=None,
                 max_sheet_rows=10_000) -> ExtractedDocument:
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
        return extract_bytes(bytes(data), path.name, max_bytes=max_bytes, cancel=cancel,
                             ocr=ocr, progress=progress, max_sheet_rows=max_sheet_rows)
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
