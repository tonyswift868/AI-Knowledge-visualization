"""Extract existing material questions, with literal evidence and no search.

Heading rules are the batch-69 audited rules. Counts are approximate block
overlap counts, not claims that every question assesses every linked goal.
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import re

from .extract import extract_bytes

ASK = re.compile(r'[?？]\s*$|^(?:什么|何谓|为什么|为何|怎么|如何|怎样|有哪些|能否|是否|什么时候|有什么|谈谈|说说|介绍一下|what\b|why\b|how\b|which\b|when\b)|(?:是什么|有哪些|哪几种|有何区别|如何|怎么|为什么|为何|是否|能否|有没有|了解吗)|(?:区别|差别|异同|优缺点|优势和劣势|不同之处)\s*$', re.I)
NONKNOW = re.compile(r'JavaGuide|Guide\s*哥|公众号|微信|PDF|赞赏|捐赠|联系我|什么时候毕业|大学期间|学校背景|薪资|工资|年薪|找工作|实习|简历|跳槽|秋招|春招|求职|没.{0,3}项目经验|面试准备|如何学习|怎么学|怎样学|学习路线|学习顺序|资料入口|书籍推荐|哪些书|这些书|这组文章|个人介绍|坚持写.{0,3}博客|为什么自称|要不要培训', re.I)
EXTRA_NONKNOW = {
    '怎么使用这份清单','还有公司在用的技术就要学吗？','写技术博客有哪些好处？','写哪些方向的博客比较好？',
    '有没有什么写作技巧分享？','我的知识星球评价如何？','我的知识星球能为你提供什么？','是否收费？','如何加入？',
    '复习时先抓住哪些问题？','什么程度才算会了','面试官想从项目里了解什么？','项目介绍应该怎么讲？',
    '架构图怎么讲？','怎么说明自己的职责？','技术选型怎么回答？','项目难点怎么讲？','性能优化怎么讲？',
    '线上故障怎么讲？','项目指标怎么准备？','常见项目追问怎么准备？','校招和社招的准备重点有什么不同？',
    '如何避免项目过度包装？','Java 后端面试哪些知识点是重点？','如何更高效地准备八股文？','如何获取？',
    '有没有还不错的项目推荐？','我跟着视频做的项目会被面试官嫌弃不？','项目做完后怎么准备项目深挖？',
    'AI Agent 项目怎么准备？','可以从哪些方向进行贡献？','如何丝滑地提交贡献？','面试结束后怎么复盘','知道如何获取招聘信息',
}
NUMBER = re.compile(r'^\s*(?:\d{1,3}[.．、)]|【例\s*\d{1,3}】)\s*')
OPTION = re.compile(r'(?m)^\s*[A-Ha-h][.．、)）:]\s*\S.*$')
ANSWER = re.compile(r'(?:参考答案|正确答案|答案|Answer|Solution)\s*[:：]\s*', re.I)


def plain(value):
    return re.sub(r'[*`#]', '', value).strip()


def knowledge_heading(text):
    title = plain(text)
    return bool(ASK.search(title)) and not NONKNOW.search(title) and title not in EXTRA_NONKNOW


def original_blocks(chunks):
    """Reassemble split original blocks; do not invent missing fragments."""
    pieces, owners = defaultdict(list), defaultdict(set)
    for chunk in chunks:
        fragments = chunk.get('block_fragments')
        if fragments:
            for fragment in fragments:
                key = (chunk['source_id'], fragment['index'])
                pieces[key].append(fragment); owners[key].add(chunk['id'])
        else:
            doc = extract_bytes(chunk['text'].encode('utf-8'), 'material.md')
            for index, block in enumerate(doc.blocks):
                key = (chunk['source_id'], f'{chunk["position"]:08d}:{chunk["id"]}:{index:06d}')
                pieces[key].append(dict(text=block.text, offset=0, kind=block.kind, section=list(block.section)))
                owners[key].add(chunk['id'])
    blocks = []
    for key in sorted(pieces, key=lambda k: (k[0], isinstance(k[1], str), k[1])):
        text = ''; complete = True
        for part in sorted(pieces[key], key=lambda f: (f.get('offset', 0), -len(f['text']))):
            offset = part.get('offset', 0)
            if offset > len(text): complete = False; break
            if offset + len(part['text']) > len(text): text += part['text'][len(text)-offset:]
        if not complete: continue
        first = pieces[key][0]
        blocks.append(dict(source_id=key[0], block_id=key[1], kind=first['kind'], section=first.get('section', []),
                           text=text, chunk_ids=sorted(owners[key])))
    return blocks


def _new_document(block):
    return block['text'].startswith('---\n') and re.search(r'(?m)^title:', block['text'])


def _candidate(stem, body, blocks, kind, sources):
    reference = '\n\n'.join(b['text'] for b in body)
    source_id = blocks[0]['source_id']
    chunks = sorted({cid for b in blocks for cid in b['chunk_ids']})
    digest = hashlib.sha256((source_id+'\n'+stem+'\n'+reference).encode()).hexdigest()
    source = sources.get(source_id, {})
    return dict(id='material-question-'+digest[:24], candidate_type=kind, stem=stem, reference=reference,
                chunk_ids=chunks, block_ids=[b['block_id'] for b in blocks], source_id=source_id,
                source_title=source.get('title', '资料原题'), source_url=source.get('final_url') or source.get('url') or '')


def candidates(chunks, sources=()):
    """Original Q&A headings plus numbered exercises with labelled answers.

    Answer paragraphs stop at question/sibling headings or a new document.
    Only the source's own answer markers can define an objective answer.
    """
    blocks = original_blocks(chunks); sources = {s['id']: s for s in sources}
    out = []; seen = set(); unpaired = []
    for index, block in enumerate(blocks):
        if block['kind'] != 'heading' or not knowledge_heading(block['text']): continue
        body = []
        for following in blocks[index+1:]:
            if following['source_id'] != block['source_id'] or _new_document(following): break
            if following['kind'] == 'heading':
                child, parent = following['section'], block['section']
                if ASK.search(plain(following['text'])) or len(child) <= len(parent) or child[:len(parent)] != parent: break
            body.append(following)
        substantive = [b for b in body if b['kind'] != 'heading' and not re.fullmatch(r'\s*!\[.*?\]\(.*?\)\s*', b['text'], re.S)
                       and not b['text'].startswith(('<!--', ':::'))]
        if not substantive: continue
        row = _candidate(block['text'].strip(), body, [block]+body, 'recall', sources)
        marker = ANSWER.search(row['reference'])
        if marker:
            options = list(OPTION.finditer(row['reference'][:marker.start()]))
            value = row['reference'][marker.end():].splitlines()[0].strip()
            if options and value:
                row.update(candidate_type='objective', answer=value, options=[m[0].strip() for m in options])
        if row['candidate_type'] == 'recall' and not any(len(b['text'].strip()) >= 10 for b in substantive): continue
        out.append(row); seen.add(row['id'])
    # Paragraphs can contain a whole numbered exercise, or adjacent original
    # blocks can contain its options and labelled answer. Never synthesize gaps.
    groups = []; pending = []
    for block in blocks:
        if re.search(r'(?m)^\s*(?:\d{1,3}[.．、)]|【例\s*\d{1,3}】)\s*', block['text']) or (
            block['kind'] != 'heading' and len(block['text']) < 300 and knowledge_heading(block['text'])):
            if pending: groups.append(pending)
            pending = [block]
        elif pending and block['source_id'] == pending[0]['source_id'] and block['kind'] != 'heading' and not _new_document(block):
            pending.append(block)
        else:
            if pending: groups.append(pending); pending = []
    if pending: groups.append(pending)
    for group in groups:
        # A single paragraph may hold multiple exercises. Retain only the
        # question's own answer, ending before the next numbered exercise.
        text = '\n\n'.join(b['text'] for b in group)
        starts = list(re.finditer(r'(?m)^\s*(?:\d{1,3}[.．、)]|【例\s*\d{1,3}】)\s*', text))
        if not starts: starts = [re.match(r'', text)]
        for i, start in enumerate(starts):
            span = text[start.start():starts[i+1].start() if i+1 < len(starts) else len(text)].strip()
            answer = ANSWER.search(span)
            if not answer:
                number = NUMBER.match(span)
                options = list(OPTION.finditer(span))
                if number and (len(options) >= 2 or re.search(r'_{2,}|（\s*）|\(\s*\)|□+', span)):
                    unpaired.append((number[0].strip().rstrip('.．、)'), span, group))
                continue
            question = span[:answer.start()].strip(); option_matches = list(OPTION.finditer(question))
            stem = question[:option_matches[0].start()].strip() if option_matches else question
            if NONKNOW.search(stem) or plain(NUMBER.sub('', stem)) in EXTRA_NONKNOW: continue
            value = span[answer.end():].splitlines()[0].strip()
            if not value: continue
            row = _candidate(stem, [], group, 'objective', sources)
            row.update(reference=span, answer=value, options=[m[0].strip() for m in option_matches])
            row['id'] = 'material-question-'+hashlib.sha256((row['source_id']+'\n'+span).encode()).hexdigest()[:24]
            if row['id'] not in seen: out.append(row); seen.add(row['id'])
    # Original numbered answer sections are common in uploaded worksheets.
    # Pair only explicit, unique numbers within the SAME obtained source.
    # Repeated numbers or another book/page's answer are not guessed by AI.
    answers = defaultdict(list); active = None; level = 0
    for block in blocks:
        if block['kind'] == 'heading':
            if re.fullmatch(r'(?:参考)?答案(?:与解析|及解析)?|(?:习题)?解答|answers?|solutions?', plain(block['text']), re.I):
                active = block['source_id']; level = len(block['section'])
            elif active and (block['source_id'] != active or len(block['section']) <= level):
                active = None
            continue
        if active != block['source_id'] or _new_document(block):
            active = None; continue
        for line in block['text'].splitlines():
            number = NUMBER.match(line)
            if number and line[number.end():].strip():
                answers[(active, number[0].strip().rstrip('.．、)'))].append((line[number.end():].strip(), line, block))
    occurrences = defaultdict(int)
    for number, span, group in unpaired: occurrences[(group[0]['source_id'], number)] += 1
    for number, span, group in unpaired:
        matches = answers[(group[0]['source_id'], number)]
        if occurrences[(group[0]['source_id'], number)] != 1 or len(matches) != 1: continue
        value, answer_line, answer_block = matches[0]
        options = list(OPTION.finditer(span))
        stem = span[:options[0].start()].strip() if options else span
        if NONKNOW.search(stem): continue
        row = _candidate(stem, [], group+[answer_block], 'objective', sources)
        row.update(reference=span+'\n\n'+answer_line, answer=value, options=[m[0].strip() for m in options],
                   link_chunk_ids=sorted({cid for b in group for cid in b['chunk_ids']}))
        row['id'] = 'material-question-'+hashlib.sha256((row['source_id']+'\n'+row['reference']).encode()).hexdigest()[:24]
        if row['id'] not in seen: out.append(row); seen.add(row['id'])
    return out


def references(point):
    return set(point.get('chunk_ids', ())) | {c['chunk_id'] for c in point.get('citations', ()) if c.get('chunk_id')}


def for_module(rows, points=None, chunk_ids=None):
    ids = set(chunk_ids or ())
    for point in points or (): ids.update(references(point))
    return [dict(row, point_ids=[p['id'] for p in points or () if references(p).intersection(row.get('link_chunk_ids',row['chunk_ids']))])
            for row in rows if ids.intersection(row.get('link_chunk_ids',row['chunk_ids']))]


def source_fingerprint(points, chunks):
    ids = {cid for point in points for cid in references(point)}
    sources = {c['source_id'] for c in chunks if c['id'] in ids}
    data = [(p['id'], sorted(references(p))) for p in points]
    # An original answer may continue beyond the heading's cited chunk.
    data += [(c['id'], c['text'], c.get('block_fragments')) for c in chunks if c['source_id'] in sources]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def literal(value, original):
    if isinstance(value, str):
        if not value or value not in original: raise ValueError('字段不是逐字原文')
        return value
    if not isinstance(value, dict) or set(value) - {'quote', 'end_quote'}: raise ValueError('原文指针格式无效')
    first, last = value.get('quote'), value.get('end_quote')
    if not isinstance(first, str) or not first or original.count(first) != 1: raise ValueError('原文起点不存在或不唯一')
    left = original.index(first)
    if last is None: return first
    if not isinstance(last, str) or not last or original.count(last) != 1: raise ValueError('原文终点不存在或不唯一')
    right = original.index(last)+len(last)
    if right < left+len(first): raise ValueError('原文指针顺序无效')
    return original[left:right]


def legacy_answer_units(reference):
    """The former sentence/list/table-row splitter for saved legacy questions."""
    units = []
    for line in reference.splitlines():
        value = line.strip()
        if not value or re.fullmatch(r"[\s>*_`|:=-]+", value):
            continue
        if value.startswith('#') or re.fullmatch(r'!\[.*?\]\(.*?\)', value):
            continue
        if '|' in value or re.match(r'^(?:>\s*)?(?:\*\*|__)?(?:[-*+]\s|\d+[.、)）]\s*)', value):
            parts = [value]
        else:
            parts = re.findall(r'.+?(?:[。！？!?]+[”’]?|\.(?=\s|$)|$)', value)
        for part in parts:
            part = part.strip()
            if part and part not in units:
                units.append(part)
    return units or [reference.strip()]


_ANSWER_HEADING = re.compile(r'^\s{0,3}#{1,6}\s+.+?\s*#*\s*$')
_ANSWER_BOLD_HEADING = re.compile(r'^\s*(?:\*\*|__)[^\n]+?(?:\*\*|__)\s*$')
_ANSWER_LIST = re.compile(r'^(\s*)(?:(?:\*\*|__)\s*)?(?:[-+*]|\d+[.)、])\s+')
_ANSWER_FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})')
_ANSWER_TABLE_RULE = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$')


def _answer_heading(line):
    value = line.rstrip('\r\n')
    if _ANSWER_HEADING.fullmatch(value):
        return True
    if _answer_list(value):
        return False
    return bool(_ANSWER_BOLD_HEADING.fullmatch(value)
                or (value.strip().endswith(('：', ':')) and '|' not in value))


def _answer_list(line):
    return _ANSWER_LIST.match(line.rstrip('\r\n'))


def _answer_is_heading(lines, index):
    line = lines[index].rstrip('\r\n')
    if _answer_heading(line):
        return True
    value = line.strip()
    if not value or _answer_list(value) or '|' in value or re.search(r'[。！？!?；;]$', value):
        return False
    following = index + 1
    while following < len(lines) and not lines[following].strip():
        following += 1
    if following >= len(lines):
        return False
    next_line = lines[following].rstrip('\r\n')
    # Extracted documents also contain plain labels (e.g. ASCII) without # or
    # bold markers. An unterminated stand-alone line introduces the next block;
    # keep its explanation with it rather than making a label-only checkbox.
    return bool(following > index + 1 or _answer_list(next_line) or _ANSWER_FENCE.match(next_line)
                or ('|' in next_line and following + 1 < len(lines)
                    and _ANSWER_TABLE_RULE.fullmatch(lines[following + 1].rstrip('\r\n'))))


def previous_answer_units(reference):
    """213 grouping algorithm, retained for legacy block projection.

    Return literal, continuous units grouped by complete source ideas.

    This small source-preserving adapter follows the Markdown structure already
    recognized by ``course.extract``. That parser does not retain source
    offsets or list nesting, so scan the supplied reference itself to keep each
    result an exact contiguous span. Headings and colon-ended introductions
    attach to their following content; a contiguous list or table stays whole;
    sentences within one paragraph are not split.
    """
    if not isinstance(reference, str) or not reference.strip():
        return []

    lines = reference.splitlines(keepends=True)
    offsets = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)

    spans = []
    pending_heading = None
    i = 0

    def emit(start, end):
        value = reference[start:end].strip()
        if value:
            spans.append((start, end, value))

    while i < len(lines):
        line = lines[i].rstrip('\r\n')
        if not line.strip():
            i += 1
            continue
        if _answer_is_heading(lines, i):
            if pending_heading is None:
                pending_heading = i
            i += 1
            continue

        start = offsets[pending_heading if pending_heading is not None else i]
        fence = _ANSWER_FENCE.match(line)
        if fence:
            marker_char = re.escape(fence.group(1)[0])
            close = re.compile(r'^\s{0,3}' + marker_char + r'{' + str(len(fence.group(1))) + r',}\s*$')
            end = i + 1
            while end < len(lines) and not close.fullmatch(lines[end].rstrip('\r\n')):
                end += 1
            if end < len(lines):
                end += 1
            emit(start, offsets[end] if end < len(lines) else len(reference))
            pending_heading = None
            i = end
            continue

        if ('|' in line and i + 1 < len(lines)
                and _ANSWER_TABLE_RULE.fullmatch(lines[i + 1].rstrip('\r\n'))):
            end = i + 2
            while end < len(lines) and lines[end].strip() and '|' in lines[end]:
                end += 1
            emit(start, offsets[end] if end < len(lines) else len(reference))
            pending_heading = None
            i = end
            continue

        if _answer_list(line):
            # A list is one checklist idea: keep sibling items, nested bullets,
            # and their unmarked explanatory/continuation lines together.
            end = i + 1
            while end < len(lines):
                value = lines[end].rstrip('\r\n')
                if _answer_is_heading(lines, end) or _ANSWER_FENCE.match(value):
                    break
                if value.strip() and '|' in value and end + 1 < len(lines) and _ANSWER_TABLE_RULE.fullmatch(lines[end + 1].rstrip('\r\n')):
                    break
                if not value.strip():
                    probe = end + 1
                    while probe < len(lines) and not lines[probe].strip():
                        probe += 1
                    if probe >= len(lines):
                        break
                    # Blank lines inside a list do not separate its siblings.
                    if _answer_list(lines[probe]) or lines[probe][:1].isspace():
                        end = probe
                        continue
                    break
                end += 1
            emit(start, offsets[end] if end < len(lines) else len(reference))
            pending_heading = None
            i = end
            continue

        end = i + 1
        while end < len(lines) and lines[end].strip():
            value = lines[end].rstrip('\r\n')
            if (_answer_is_heading(lines, end) or _answer_list(value) or _ANSWER_FENCE.match(value)
                    or ('|' in value and end + 1 < len(lines)
                        and _ANSWER_TABLE_RULE.fullmatch(lines[end + 1].rstrip('\r\n')))):
                break
            end += 1
        emit(start, offsets[end] if end < len(lines) else len(reference))
        pending_heading = None
        i = end

    # A fenced example illustrates nearby prose. Attach its literal span to an
    # adjacent idea rather than presenting a bare code sample as a checklist
    # item. If the complete answer is only code, preserve it as one unit.
    code = []
    titled_code = []
    for _, _, value in spans:
        content_lines = [line for line in value.splitlines() if line.strip()]
        had_title = False
        while content_lines and _answer_heading(content_lines[0]):
            had_title = True
            content_lines.pop(0)
        code.append(bool(content_lines and _ANSWER_FENCE.match(content_lines[0])))
        titled_code.append(had_title)
    merged = []
    i = 0
    while i < len(spans):
        start, end, value = spans[i]
        if code[i] and titled_code[i]:
            merged.append((start, end, value))
        elif code[i] and merged:
            previous_start, _, _ = merged[-1]
            merged[-1] = (previous_start, end, reference[previous_start:end].strip())
        elif code[i] and i + 1 < len(spans):
            next_end = spans[i + 1][1]
            merged.append((start, next_end, reference[start:next_end].strip()))
            i += 1
        else:
            merged.append((start, end, value))
        i += 1
    # Repeated literal paragraphs remain in the reference, but are one checklist
    # item. The bank schema has always required unique key-point strings.
    return list(dict.fromkeys(value for _, _, value in merged)) or [reference.strip()]


_ANSWER_SHORT_TITLE = re.compile(r'(?:特点|特性|特征|要求|原则|条件|要素|如下|包括|构成)$')
_ANSWER_SQL_LINE = re.compile(r'^\s*(?:SELECT|WITH|INSERT\s+INTO|UPDATE\s+\S|DELETE\s+FROM|CREATE\s+TABLE|EXPLAIN)\b', re.I)


def _answer_substantive_colon(value):
    """A colon may follow a complete definition, not only a section lead-in."""
    body = value.rstrip().rstrip('：:').strip()
    return bool(re.search(
        r'(?:称为|叫做|叫作|定义为|指的是|意味着|导致|会导致|造成|使得|属于|代表|在于|是)[^，,：:]+',
        body,
    ))


def _answer_is_heading214(lines, index):
    """214-local structural classifier; keep the archived 213 rules untouched."""
    value = lines[index].rstrip('\r\n').strip()
    if not value:
        return False
    if _answer_list(value):
        return False
    if _ANSWER_SQL_LINE.match(value):
        return False
    if _ANSWER_HEADING.fullmatch(value):
        # In JavaGuide, a Markdown # line immediately preceding SQL is a code
        # comment, not a document section heading.
        following = index + 1
        while following < len(lines) and not lines[following].strip():
            following += 1
        if following < len(lines) and _ANSWER_SQL_LINE.match(lines[following].strip()):
            return False
        return True
    if _ANSWER_BOLD_HEADING.fullmatch(value):
        return True
    if re.search(r'[。！？!?；;]$', value):
        return False
    if value.endswith(('：', ':')):
        return not _answer_substantive_colon(value)
    following = index + 1
    while following < len(lines) and not lines[following].strip():
        following += 1
    if following >= len(lines):
        return False
    next_value = lines[following].rstrip('\r\n').strip()
    return bool(following > index + 1 or _answer_list(next_value)
                or _ANSWER_FENCE.match(next_value)
                or ('|' in next_value and following + 1 < len(lines)
                    and _ANSWER_TABLE_RULE.fullmatch(lines[following + 1].rstrip('\r\n'))))


def _answer_clean_heading(value):
    value = re.sub(r'^\s{0,3}#{1,6}\s*', '', value.strip())
    value = re.sub(r'^(?:\*\*|__)(.*?)(?:\*\*|__)$', r'\1', value)
    return value.rstrip('#').strip()


def _answer_groups_with_metadata(reference):
    """Return source-literal answer ideas and display-only context headings.

    Sibling list items are independent ideas. Nested items and indented
    continuation lines stay with their parent. A title or lead-in is metadata,
    never a checkbox item. Table data rows are separate ideas, with the column
    header carried as display context. Every ``text`` remains one contiguous
    slice of ``reference`` so callers can locate it without rewritten text.
    """
    if not isinstance(reference, str) or not reference.strip():
        return []

    lines = reference.splitlines(keepends=True)
    offsets = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    groups = []
    heading_lines = []
    i = 0

    def emit(start_line, end_line, heading=None, heading_spans=()):
        start = offsets[start_line]
        end = offsets[end_line] if end_line < len(lines) else len(reference)
        text = reference[start:end].strip()
        if text:
            groups.append({'text': text, 'heading': heading or None, '_start': start, '_end': end,
                           '_heading_spans': list(heading_spans)})

    def skip_blank(index):
        while index < len(lines) and not lines[index].strip():
            index += 1
        return index

    def raw_heading(start, end):
        return ' '.join(_answer_clean_heading(lines[n]) for n in range(start, end)
                        if lines[n].strip() and not re.match(
                            r'^\s*(?:style|classDef|linkStyle)\s+\S+\s+.*(?:fill|stroke|color)\s*:', lines[n]))

    def line_span(start, end):
        return (offsets[start], offsets[end] if end < len(lines) else len(reference))

    while i < len(lines):
        line = lines[i].rstrip('\r\n')
        if not line.strip():
            i += 1
            continue
        if _answer_is_heading214(lines, i):
            heading_lines.append(i)
            i += 1
            continue

        heading = raw_heading(heading_lines[0], heading_lines[-1] + 1) if heading_lines else None
        heading_spans = [line_span(heading_lines[0], heading_lines[-1] + 1)] if heading_lines else []
        heading_lines.clear()

        # Java/MySQL examples commonly use an unfenced `#` comment followed by
        # SQL. Keep both source lines intact and let the following merge step
        # attach the example to its preceding definition/explanation.
        sql_line = i if _ANSWER_SQL_LINE.match(line.strip()) else None
        if line.lstrip().startswith('#'):
            probe = skip_blank(i + 1)
            if probe < len(lines) and _ANSWER_SQL_LINE.match(lines[probe].strip()):
                sql_line = probe
        if sql_line is not None:
            end = sql_line + 1
            while end < len(lines) and lines[end].strip():
                end += 1
            emit(i, end, heading, heading_spans)
            groups[-1]['_example'] = True
            i = end
            continue

        if _ANSWER_FENCE.match(line):
            fence = _ANSWER_FENCE.match(line).group(1)
            close = re.compile(r'^\s{0,3}' + re.escape(fence[0]) + r'{' + str(len(fence)) + r',}\s*$')
            end = i + 1
            while end < len(lines) and not close.fullmatch(lines[end].rstrip('\r\n')):
                end += 1
            if end < len(lines):
                end += 1
            emit(i, end, heading, heading_spans)
            i = end
            continue

        # A table row is a distinct answer idea; its column headings describe
        # the row and are kept as non-checkbox context.
        if ('|' in line and i + 1 < len(lines)
                and _ANSWER_TABLE_RULE.fullmatch(lines[i + 1].rstrip('\r\n'))):
            table_heading = lines[i].strip()
            table_heading_span = line_span(i, i + 1)
            end = i + 2
            while end < len(lines) and lines[end].strip() and '|' in lines[end]:
                emit(end, end + 1, ' · '.join(v for v in (heading, table_heading) if v),
                     [*heading_spans, table_heading_span])
                end += 1
            i = end
            continue

        if _answer_list(line):
            indent = len(line) - len(line.lstrip())
            items = []
            cursor = i
            while cursor < len(lines):
                current = lines[cursor].rstrip('\r\n')
                marker = _answer_list(current)
                if marker and len(marker.group(1)) == indent:
                    item_start = cursor
                    cursor += 1
                    while cursor < len(lines):
                        value = lines[cursor].rstrip('\r\n')
                        next_marker = _answer_list(value)
                        if next_marker and len(next_marker.group(1)) <= indent:
                            break
                        if _answer_is_heading214(lines, cursor) or _ANSWER_FENCE.match(value):
                            break
                        if not value.strip():
                            probe = skip_blank(cursor)
                            probe_line = lines[probe].rstrip('\r\n') if probe < len(lines) else ''
                            probe_marker = _answer_list(probe_line)
                            if probe >= len(lines) or (probe_marker and len(probe_marker.group(1)) <= indent):
                                cursor = probe
                                break
                            # Keep blank-separated nested/continuation text with
                            # this parent, but do not absorb a fresh paragraph.
                            if probe_marker or (probe_line[:1].isspace()):
                                cursor = probe
                                continue
                            cursor = probe
                            break
                        cursor += 1
                    items.append((item_start, cursor))
                    continue
                if not current.strip():
                    cursor += 1
                    continue
                break

            # Some documents encode a list title as its first bullet. Promote
            # only generic title words without descriptive punctuation and
            # only when another sibling supplies the actual list content.
            list_heading = heading
            list_heading_spans = list(heading_spans)
            if len(items) > 1:
                first_text = lines[items[0][0]].strip()
                first_value = _answer_list(first_text).string[_answer_list(first_text).end():].strip()
                compact = re.sub(r'[*_`]', '', first_value).strip().rstrip('：:')
                descriptive_punctuation = re.search(r'[，。；：,.;!?！？]', compact)
                if _ANSWER_SHORT_TITLE.search(compact) and not descriptive_punctuation:
                    list_heading = ' · '.join(v for v in (heading, compact) if v)
                    list_heading_spans.append(line_span(items[0][0], items[0][0] + 1))
                    items.pop(0)

            for item_start, item_end in items:
                # Exclude blanks between sibling items from the source span.
                while item_end > item_start and not lines[item_end - 1].strip():
                    item_end -= 1
                emit(item_start, item_end, list_heading, list_heading_spans)
            i = max(cursor, i + 1)
            continue

        # Ordinary prose is grouped by paragraph, never by sentence.
        end = i + 1
        while end < len(lines) and lines[end].strip():
            value = lines[end].rstrip('\r\n')
            if (_answer_is_heading214(lines, end) or _answer_list(value) or _ANSWER_FENCE.match(value)
                    or ('|' in value and end + 1 < len(lines)
                        and _ANSWER_TABLE_RULE.fullmatch(lines[end + 1].rstrip('\r\n')))):
                break
            end += 1
        emit(i, end, heading, heading_spans)
        i = end

    if heading_lines:
        # A reference consisting only of a heading remains answerable as a
        # single literal point; do not silently discard its original text.
        start = heading_lines[0]
        emit(start, len(lines), None)

    # A bare list label followed by its explanation is one answer idea, even
    # when extraction removed the list indentation. Keep the label and prose
    # in one literal source span; otherwise the label becomes a pointless
    # checkbox separated from the facts that explain it.
    attached = []
    index = 0
    while index < len(groups):
        group = groups[index]
        first_line = group['text'].splitlines()[0] if group['text'].splitlines() else ''
        marker = _answer_list(first_line)
        label = first_line[marker.end():].strip() if marker else ''
        is_label = bool(marker and label and not re.search(r'[，。；：,.;!?！？]', label))
        if not is_label or index + 1 >= len(groups):
            attached.append(group); index += 1; continue

        end_index = index + 1
        last = groups[index]
        while end_index < len(groups):
            following = groups[end_index]
            next_line = following['text'].splitlines()[0] if following['text'].splitlines() else ''
            if (following.get('heading') or _answer_list(next_line)
                    or reference[last['_end']:following['_start']].strip()):
                break
            last = following
            end_index += 1
        if last is groups[index]:
            attached.append(group); index += 1; continue
        group['_end'] = last['_end']
        group['text'] = reference[group['_start']:group['_end']].strip()
        attached.append(group)
        index = end_index
    groups = attached

    # A code block illustrates adjacent prose; keep it attached rather than
    # exposing a code-only checkbox unless the answer itself is just code.
    merged = []
    for index, group in enumerate(groups):
        content_lines = [line for line in group['text'].splitlines() if line.strip()]
        is_code = bool(group.get('_example') or
                       (content_lines and _ANSWER_FENCE.match(content_lines[0])))
        if is_code and group.get('heading'):
            # A title/lead-in is explanatory context for the example. Keep the
            # code as its own literal group and the explanation as metadata.
            merged.append(group)
        elif is_code and merged:
            previous = merged[-1]
            previous['_end'] = group['_end']
            previous['text'] = reference[previous['_start']:previous['_end']].strip()
        elif is_code and index + 1 < len(groups):
            # A leading example belongs with the following explanation.
            following = groups[index + 1]
            following['_start'] = group['_start']
            following['text'] = reference[following['_start']:following['_end']].strip()
            if group.get('heading') and not following.get('heading'):
                following['heading'] = group['heading']
        else:
            merged.append(group)

    # Drop exact duplicate groups, as the previous behavior did for duplicate
    # literal paragraphs. Do not deduplicate semantically similar content.
    unique = []
    seen = set()
    for group in merged:
        key = group['text']
        if key not in seen:
            unique.append({'text': group['text'], 'heading': group.get('heading'),
                           '_text_span': (group['_start'], group['_end']),
                           '_heading_spans': group.get('_heading_spans', [])})
            seen.add(key)
    return unique or [{'text': reference.strip(), 'heading': None}]


def answer_groups(reference):
    """Public answer groups; heading and source offsets stay out of stored rows."""
    return [{'text': group['text'], 'heading': group.get('heading')}
            for group in _answer_groups_with_metadata(reference)]


def _answer_group_metadata(reference):
    """Internal text/heading source spans for runtime projections and display."""
    groups = _answer_groups_with_metadata(reference)
    return [{
        'text': group['text'],
        'heading': group.get('heading'),
        'text_span': group.get('_text_span'),
        'heading_spans': [
            (start, end, reference[start:end])
            for start, end in group.get('_heading_spans', [])
        ],
    } for group in groups]


def answer_units(reference):
    """Compatibility projection: checkbox point text without display headings."""
    return [group['text'] for group in answer_groups(reference)]


def selected_units(reference, ids):
    units = answer_units(reference)
    if not isinstance(ids, list):
        return units
    if (not ids or any(type(i) is not int or not 1 <= i <= len(units) for i in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('要点编号须为不同且有效的本题完整意思块编号')
    return [units[i-1] for i in ids]


def request_candidate(candidate):
    """Wire-only copy: omit reference only when units preserve every nonspace.

    Headings, code fences, duplicate lines and punctuation are significant.
    The original candidate remains the authority for compilation and storage.
    """
    packet = dict(candidate)
    if candidate['candidate_type'] == 'recall':
        units = answer_units(candidate['reference'])
        packet['units'] = [dict(id=i+1, text=text) for i, text in enumerate(units)]
        compact = lambda text: re.sub(r'\s', '', text)
        if compact(candidate['reference']) == compact(''.join(units)):
            packet.pop('reference')
    return packet


def compile_question(raw, candidate, module_id):
    """Only locator fields can become content; module/point links are program owned."""
    kind = raw.get('kind')
    if kind != candidate['candidate_type']: raise ValueError('题型与原题不符')
    stem = literal(raw.get('stem'), candidate['stem'])
    if stem != candidate['stem']: raise ValueError('题干被截短或改写')
    reference = ''; key_points = []; options = []; answer = ''; subtype = None
    if kind == 'recall':
        # The program already owns the complete original question and answer.
        # A model's key-point formatting must never discard a material question.
        reference = candidate['reference']
        key_points = selected_units(reference, raw.get('key_point_ids'))

    else:
        options = [literal(v, candidate['reference']) for v in raw.get('options', [])]
        if options != candidate['options']: raise ValueError('选项被删改或与原题不符')
        answer = literal(raw.get('answer'), candidate['answer'])
        if answer != candidate['answer']: raise ValueError('标准答案被改写或截短')
        subtype = raw.get('objective_kind')
        if subtype not in ('choice','multi','fill','calc'): raise ValueError('客观题缺少原题型')
        if options and subtype not in ('choice','multi'): raise ValueError('选择题类型不符')
        if not options and subtype in ('choice','multi'): raise ValueError('选择题没有原文选项')
        if subtype == 'fill' and not re.search(r'_{2,}|（\s*）|\(\s*\)|□+', stem): raise ValueError('原题不是填空')
    source = dict(title=candidate['source_title'], url=candidate['source_url'],
                  locator='原文块 '+', '.join(map(str, candidate['block_ids'])),
                  chunk_ids=candidate['chunk_ids'], block_ids=candidate['block_ids'])
    return dict(id=candidate['id'], module_id=module_id, point_ids=candidate['point_ids'], kind=kind,
                objective_kind=subtype, stem=stem, options=options, answer=answer, reference=reference,
                key_points=key_points, source=source, answer_source=dict(source), origin='found', role='bank',
                importance='normal', explanation='', uses=[], created_at=datetime.now(timezone.utc).isoformat())


SYSTEM = '''你只按原文拆题，不出题、不改写、不补答案、不解题。每个candidate是程序已经切出的资料原题。
返回JSON {"questions":[{"candidate_id":"原id","kind":"recall或objective","stem":"完整原题干",
"options":["完整原选项"],"answer":"原标准答案","objective_kind":"choice/multi/fill/calc",
"key_point_ids":[1,2,3]}]}。
问答题的完整题干与参考答案由程序保留，不要抄参考答案或要点。units是程序按标题、段落、列表和表格结构整理的完整意思块；只选择能直接回答题干的必要要点，不能把一个意思块拆开或为了凑数多选，条数不设固定目标。
若未附reference，units按编号拼接就是完整参考答案（仅空白可能不同）；若附reference，保留其中未切成单位的标题、代码等上下文。要点仍只选择units编号。
客观题kind=objective，stem/options/answer逐字照输入，原来没答案不补。
长stem可用唯一原文指针 {"quote":"开头短片段","end_quote":"末尾短片段"}，程序展开完整原文。
不得改变候选归属、原题型、选项或答案。只返回上述短JSON，不写解释或新增练习。'''



def batches(rows, max_chars=18000, max_questions=4):
    batch = []; size = 0
    for row in rows:
        length = len(json.dumps(row, ensure_ascii=False))
        if batch and (len(batch) >= max_questions or size+length > max_chars):
            yield batch; batch = []; size = 0
        batch.append(row); size += length
    if batch: yield batch
