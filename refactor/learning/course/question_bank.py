"""Original-source question schema and model-free selection (indices start at 0)."""
from datetime import datetime
from fractions import Fraction
import math
import unicodedata
import re

from . import answer_compare

KINDS = frozenset(('choice', 'multi', 'fill', 'short', 'essay', 'calc'))
PURPOSES = frozenset(('quick', 'module', 'direct', 'final', 'retest', 'free'))
FIELDS = frozenset(('id', 'module_id', 'point_ids', 'kind', 'stem', 'answer',
    'explanation', 'origin', 'source', 'answer_source', 'importance', 'uses', 'created_at'))
OBJECTIVE_KINDS = frozenset(('choice', 'multi', 'fill', 'calc'))
_SYNONYMS = (
    ('协议','网络协议','protocol','network protocol'),
    ('私有地址','私有IP地址','私网地址','内网地址','private','local','private IP','private IP address','local IP'),
    ('CPU','中央处理器','处理器','central processing unit','processor'),
    ('OS','操作系统','operating system'),
    ('RAM','随机存取存储器','random access memory'),
    ('数据库','database'), ('神经网络','neural network'),
    ('防火墙','firewall'), ('Pa','帕','帕斯卡'), ('N','牛顿','newton'),
    ('先进先出','FIFO','first in first out'), ('后进先出','LIFO','last in first out'),
)
# Ambiguity groups deliberately include overlapping concepts (e.g. protocol /
# network protocol and processor / CPU). They are NOT acceptable fill answers.
_SPELLINGS = (
    ('CPU','中央处理器','central processing unit'),
    ('OS','操作系统','operating system'),
    ('RAM','随机存取存储器','random access memory'),
    ('数据库','database'), ('神经网络','neural network'),
    ('防火墙','firewall'), ('Pa','帕','帕斯卡'), ('N','牛顿','newton'),
    ('先进先出','FIFO','first in first out'), ('后进先出','LIFO','last in first out'),
)


def _term(value):
    return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())


def aliases(value):
    normalized=_term(value)
    return next((group for group in _SYNONYMS if normalized in {_term(x) for x in group}), (value,))


def answer_spellings(value):
    normalized=_term(value)
    return next((group for group in _SPELLINGS if normalized in {_term(x) for x in group}), (value,))


def overlapping_options(left, right):
    """Conservative containment/synonym exclusion, never semantic guessing."""
    a,b=_term(left),_term(right)
    return not a or not b or a in b or b in a or bool({_term(x) for x in aliases(left)} & {_term(x) for x in aliases(right)})


def _occurs(term, text):
    pattern=re.escape(term)
    if re.search(r'[a-zA-Z]',term):pattern=r'(?<![a-zA-Z0-9])'+pattern+r'(?![a-zA-Z0-9])'
    return bool(re.search(pattern,text,re.I))


def safe_blank(sentence, word):
    return blank_rejection(sentence,word) is None


def parallel_answer_parts(sentence, word, parts):
    """Verify a complete, contiguous coordination, never infer meaning/types.

    Missing/partial lists, separate clauses and repeated/overlapping spans
    fall back to a single original-span answer. The locator only points to
    terms; conjunctions and complete coverage are checked here.
    """
    if blank_rejection(sentence,word) or not isinstance(parts,list) or len(parts)<2:return ()
    if any(not isinstance(p,str) or not p.strip() or sentence.count(p)!=1 for p in parts):return ()
    ordered=sorted(parts,key=word.find)
    if any(p not in word for p in ordered) or any(overlapping_options(a,b) for i,a in enumerate(ordered) for b in ordered[i+1:]):return ()
    if not word.startswith(ordered[0]) or not word.endswith(ordered[-1]):return ()
    gaps=[]
    for left,right in zip(ordered,ordered[1:]):
        end=word.find(left)+len(left);start=word.find(right)
        if start<end:return ()
        gaps.append(word[end:start].strip())
    chinese=all(re.fullmatch(r'、|(?:、)?(?:以及|和|及|与)',g) for g in gaps)
    english=all(g==',' for g in gaps[:-1]) and bool(re.fullmatch(r',?\s*and',gaps[-1],re.I))
    if not (chinese or english):return ()
    before,after=sentence.split(word,1)
    # A claimed answer cannot hide another sibling immediately outside it.
    if re.search(r'(?:、|,|以及|和|及|与|\band)\s*$',before,re.I):return ()
    if re.match(r'\s*(?:、|,\s*(?![;.]|$)|以及|和|及|与|\band\b)',after,re.I):return ()
    return tuple(ordered)


def blank_rejection(sentence, word):
    """Literal/ambiguity check for any span, without semantic-role restrictions."""
    if not isinstance(sentence,str) or not isinstance(word,str) or not word.strip():return '原句或答案片段为空'
    if word not in sentence:return '答案片段不在原句中'
    if sentence.count(word)!=1:return '答案片段在句中出现多次'
    rest=sentence.replace(word,'',1)
    if any(_occurs(alias,rest) for alias in aliases(word)):return '句子其余部分含答案别称或重复写法'
    return None


def fixed_answer(value):
    """Short literal term/number/unit, never prose, a list or open explanation."""
    if not isinstance(value,str) or not value.strip():return False
    value=value.strip()
    if len(value)>32 or len(re.findall(r'[\u4e00-\u9fff]',value))>12:return False
    if re.search(r'[\n。；;，,!?！？:：]|例如|因为|或者|解释|说明|举例|能够|需要|应该|是指|\b(?:is|are|because|should)\b',value,re.I):return False
    return bool(re.fullmatch(r'[\w\u4e00-\u9fff.+%µμΩ°℃/²³^ -]+',value)) and len(value.split())<=3


def numeric_reference(value):
    if not isinstance(value,str) or len(value)>80:return None
    num=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:/\d+)?'
    match=re.fullmatch(r'\s*('+num+r')(?:\s*±\s*('+num+r'))?\s*([A-Za-zµμΩ°℃%][A-Za-z0-9µμΩ°℃%/·^²³_-]*|[\u4e00-\u9fff]{1,6})?\s*',value)
    if not match:return None
    try:
        number=Fraction(match[1]);tolerance=Fraction(match[2] or '0')
        if tolerance<0 or not math.isfinite(float(number)) or not math.isfinite(float(tolerance)):return None
    except (ValueError,ZeroDivisionError,OverflowError):return None
    return dict(numeric_answer=match[1],answer_unit=match[3] or '',tolerance=float(tolerance))


def objective_question(row):
    kind=row.get('kind')
    if kind not in OBJECTIVE_KINDS:return False
    if kind=='fill':
        return isinstance(row.get('answer'),list) and bool(row['answer']) and all(
            isinstance(group,list) and bool(group) and all(fixed_answer(v) for v in group) for group in row['answer'])
    if kind=='calc':
        parsed=numeric_reference(row.get('answer'))
        # Admit only what the classroom grader can actually check (units it knows).
        return (parsed is not None and all(row.get(key)==value for key,value in parsed.items())
                and answer_compare.can_grade(row))
    return True


def original_text(value):
    """Only ignore whitespace/punctuation, not case, symbols, words or numbers."""
    return ''.join(c for c in unicodedata.normalize('NFKC', value)
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def contains_original(quote, text):
    needle = original_text(quote)
    return bool(needle) and needle in original_text(text)


def retire_legacy_question(row):
    if row.get('role') == 'retired': return row
    if row.get('subpoint_id') and checked_question(row): return row
    if row.get('kind') in ('objective', 'recall') and row.get('origin') == 'found': return row
    return dict(row, role='retired', retirement=dict(version=8, preparation_reset=False, previous_role=row.get('role', 'bank'),
        reason='旧格式题已退出考核，历史作答保留；新题只从资料原文准备。'))


def validate_question(row):
    if 'subpoint_id' in row:
        sid = row['subpoint_id']
        ids = row.get('point_ids', [])
        if (not isinstance(sid, str) or not isinstance(ids,list) or len(ids) != 1 or not isinstance(ids[0],str)
                or not re.fullmatch(re.escape(ids[0]) + r'-s[1-9][0-9]*', sid)):
            raise ValueError('小要点编号必须属于题目的知识点')
    if row.get('role') == 'retired':
        # Historical schemas (including type/angle instead of kind) are kept
        # verbatim. They are evidence, never a grading/selection input.
        for key in ('id', 'module_id', 'stem'):
            if not isinstance(row.get(key), str) or not row[key].strip(): raise ValueError('旧题记录无法读取')
        if not isinstance(row.get('point_ids'), list) or any(not isinstance(p,str) or not p for p in row['point_ids']):
            raise ValueError('旧题知识点记录无法读取')
        if not isinstance(row.get('retirement'), dict) or row['retirement'].get('version') != 8:
            raise ValueError('旧题退出记录无法读取')
        return row
    if row.get('kind') in ('objective', 'recall'):
        return _validate_material_question(row)
    # Previously saved B-line questions remain bank questions; no invented
    # quick questions are inferred from their old usage history.
    keys = set(row) - {'_field_versions', 'role', 'source_level', 'source_type', 'target',
        'cloze_subject', 'contrast_evidence', 'distractor_check', 'question_origin', 'subpoint_id'}
    if 'question_origin' in row and row['question_origin'] not in ('material','web'):
        raise ValueError('原题来源分类无效')
    if 'cloze_subject' in row and (not isinstance(row['cloze_subject'],str) or not row['cloze_subject'].strip()):
        raise ValueError('挖空对象无效')
    if 'contrast_evidence' in row:
        evidence=row['contrast_evidence']
        if not isinstance(evidence,list) or any(not isinstance(v,dict) or set(v)!=
            {'chunk_id','contrast_quote','contrast_subject','contrast_value'} or
            any(not isinstance(t,str) or not t.strip() for t in v.values()) for v in evidence):
            raise ValueError('对照原文证据无效')
    if 'distractor_check' in row:
        check=row['distractor_check']
        if not isinstance(check,dict) or set(check)!= {'state','checks','reason','at'} or check['state'] not in ('passed','rejected'):
            raise ValueError('干扰项检查记录无效')
        if not isinstance(check['checks'],list) or not isinstance(check['reason'],str):raise ValueError('干扰项检查记录无效')
        for item in check['checks']:
            if not isinstance(item,dict) or set(item)!= {'option','index','verdict','quote','removed'} or type(item['index']) is not int or item['index']<0 or type(item['removed']) is not bool:
                raise ValueError('干扰项逐项记录无效')
            if item['verdict'] not in ('also_supported','not_supported') or not all(isinstance(item[k],str) for k in ('option','quote')):
                raise ValueError('干扰项逐项记录无效')
        _time(check['at'])
    if 'target' in row and (row.get('origin')!='cloze' or not isinstance(row['target'],str) or not row['target'].strip()):
        raise ValueError('挖空掌握目标问题无效')
    if row.get('source_level') is not None and (type(row['source_level']) is not int or row['source_level'] not in (1,2,3)):
        raise ValueError('题目来源档位无效')
    if 'source_type' in row and not isinstance(row['source_type'],str):raise ValueError('题目来源类型无效')
    if row.get('kind')=='calc':
        keys-= {'numeric_answer','answer_unit','tolerance'}
        if any(k in row for k in ('numeric_answer','answer_unit','tolerance')) and not objective_question(row):
            raise ValueError('计算题必须有原文标准数值、单位和允许误差')
    if row.get('role', 'bank') not in ('bank', 'quick'): raise ValueError('题目角色无效')
    if row.get('role') == 'quick' and (row.get('kind') != 'choice' or row.get('origin') != 'cloze'):
        raise ValueError('随口问题必须为原文挖空单选')
    expected = FIELDS | ({'options'} if row.get('kind') in ('choice', 'multi') else set())
    if keys != expected: raise ValueError('题库字段不符合新版契约')
    if row['kind'] not in KINDS: raise ValueError('题型无效')
    for key in ('id', 'module_id', 'stem'):
        if not isinstance(row[key],str) or not row[key].strip(): raise ValueError(key+' 不能为空')
    ids=row['point_ids']
    if not isinstance(ids,list) or not ids or any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):
        raise ValueError('知识点编号无效')
    if row['origin'] not in ('found','cloze') or row['importance'] not in ('key','normal'):
        raise ValueError('题目来源/重要性无效')
    if not isinstance(row['explanation'],str): raise ValueError('解析必须是原文或空文本')
    for key in ('source','answer_source'):
        source=row[key]
        if not isinstance(source,dict) or set(source)!= {'title','url','locator'} or any(not isinstance(v,str) for v in source.values()):
            raise ValueError('缺少题目或答案出处')
        if not source['title'].strip() or not source['locator'].strip(): raise ValueError('出处无标题/位置')
    answer=row['answer']
    if row['kind'] in ('choice','multi'):
        options=row['options']
        if not isinstance(options,list) or len(options)<2 or any(not isinstance(o,str) or not o.strip() for o in options):
            raise ValueError('选项无效')
        if len({original_text(o) for o in options})!=len(options): raise ValueError('选项重复')
        if not isinstance(answer,list) or not answer or any(type(i) is not int or not 0<=i<len(options) for i in answer) or len(set(answer))!=len(answer):
            raise ValueError('答案下标无效')
        if row['kind']=='choice' and len(answer)!=1: raise ValueError('单选必须有唯一答案')
        if row['kind']=='multi' and row['origin']=='cloze' and len(answer)<2: raise ValueError('并列挖空多选须有至少两项正确答案')
    elif row['kind']=='fill':
        if not isinstance(answer,list) or not answer or any(not isinstance(a,list) or not a or any(not isinstance(t,str) or not t.strip() for t in a) for a in answer):
            raise ValueError('填空答案无效')
        blanks=re.findall(r'_{2,}|（\s*）|\(\s*\)|□+',row['stem'])
        if len(blanks)!=len(answer): raise ValueError('填空数量与答案数量不符')
    elif not isinstance(answer,str) or not answer.strip(): raise ValueError('必须有来源参考答案')
    if not isinstance(row['uses'],list): raise ValueError('使用记录无效')
    for use in row['uses']:
        if not isinstance(use,dict) or set(use)!= {'purpose','at'} or use['purpose'] not in PURPOSES: raise ValueError('使用记录无效')
        _time(use['at'])
    _time(row['created_at'])
    return row


def _validate_material_question(row):
    """Batch-70 admission schema only; selection is owned by assessment."""
    required = FIELDS | {'options', 'reference', 'key_points', 'objective_kind'}
    keys = set(row) - {'_field_versions', 'role', 'subpoint_id'}
    if keys != required: raise ValueError('资料原题字段不符合契约')
    if row.get('origin') != 'found' or row.get('role', 'bank') != 'bank': raise ValueError('只接收资料自带题')
    for key in ('id', 'module_id', 'stem'):
        if not isinstance(row[key], str) or not row[key].strip(): raise ValueError(key+' 不能为空')
    if (not isinstance(row['point_ids'], list) or not row['point_ids']
            or any(not isinstance(p, str) or not p for p in row['point_ids'])
            or len(set(row['point_ids'])) != len(row['point_ids'])): raise ValueError('知识点编号无效')
    for key in ('source', 'answer_source'):
        source = row[key]
        if (not isinstance(source, dict) or set(source) != {'title','url','locator','chunk_ids','block_ids'}
                or any(not isinstance(source[k], str) for k in ('title','url','locator'))
                or not source['title'].strip() or not source['locator'].strip()
                or not isinstance(source['chunk_ids'], list) or not source['chunk_ids']
                or any(not isinstance(cid, str) or not cid for cid in source['chunk_ids'])
                or not isinstance(source['block_ids'], list) or not source['block_ids']): raise ValueError('原题缺少原文块出处')
    if not isinstance(row['options'], list) or any(not isinstance(v, str) or not v for v in row['options']): raise ValueError('原文选项无效')
    if not isinstance(row['answer'], str) or not isinstance(row['reference'], str): raise ValueError('原文答案无效')
    if row['kind'] == 'recall':
        from .material_questions import answer_units, legacy_answer_units, previous_answer_units
        points = row['key_points']
        # Accept saved literal fragments as well as current complete meaning
        # groups. Existing immutable answers still refer to their old indices.
        units = answer_units(row['reference']) + legacy_answer_units(row['reference']) + previous_answer_units(row['reference'])
        if (not row['reference'].strip() or row['answer'] or row['options'] or row['objective_kind'] is not None
                or not isinstance(points, list) or not points
                or any(not isinstance(p, str) or not p or p not in row['reference'] for p in points)
                or any(p not in units for p in points)
                or len(set(points)) != len(points)): raise ValueError('问答题要点必须逐字在参考答案里')
    else:
        if (not row['answer'].strip() or row['reference'] or row['key_points']
                or row['objective_kind'] not in OBJECTIVE_KINDS): raise ValueError('客观题原答案或类型无效')
        if row['objective_kind'] in ('choice','multi') and len(row['options']) < 2: raise ValueError('选择题缺少原文选项')
        if row['objective_kind'] not in ('choice','multi') and row['options']: raise ValueError('非选择题不应附选项')
    if row['importance'] not in ('key','normal') or not isinstance(row['explanation'], str): raise ValueError('题目元数据无效')
    if not isinstance(row['uses'], list): raise ValueError('使用记录无效')
    for use in row['uses']:
        if not isinstance(use, dict) or set(use) != {'purpose','at'} or use['purpose'] not in PURPOSES: raise ValueError('使用记录无效')
        _time(use['at'])
    _time(row['created_at'])
    return row


def _time(at):
    if not isinstance(at,str): raise ValueError('时间无效')
    parsed=datetime.fromisoformat(at.replace('Z','+00:00'))
    if parsed.tzinfo is None: raise ValueError('时间必须含时区')
    return parsed.timestamp()


def select_question(rows, point_id, purpose, exclude_ids=()):
    if purpose not in PURPOSES: raise ValueError('选题用途无效')
    excluded=set(exclude_ids)
    role = 'quick' if purpose == 'quick' else 'bank'
    pool=[q for q in rows if point_id in q['point_ids'] and q['id'] not in excluded
          and q.get('role', 'bank') == role and (assessment_question(q) if purpose!='quick' else objective_question(q) and checked_question(q))]
    def rank(q):
        times=[_time(u['at']) for u in q['uses'] if u['purpose']==purpose]
        # Selected original exercises first; then web exercises by authority.
        # Within that source band prefer unused questions and least recent use.
        return (q.get('question_origin')=='web',q.get('source_level',3) if q.get('question_origin')=='web' else 0,
                bool(times),max(times,default=0),q['created_at'],q['id'])
    return min(pool,key=rank) if pool else None


def assessment_question(row):
    """Only material-provided questions participate in question-based assessment."""
    return (row.get('kind') in ('objective','recall','choice','multi','fill','calc') and row.get('role','bank')=='bank'
            and (row.get('origin')!='cloze' or bool(row.get('subpoint_id')) and checked_question(row))
            and row.get('question_origin')!='web'
            and (row.get('kind') in ('objective','recall') or answer_compare.can_grade(row))
            and row.get('distractor_check',{}).get('state')!='rejected')


def with_recall_key_points(row):
    """Migrate the current card's grouping without rewriting bank or history.

    A former selected fragment now selects its containing complete group. Old
    snapshots remain untouched, so saved tick/miss indices keep their meaning.
    No new sync fields or mutable version marker are needed for this projection.
    """
    if row.get('kind') != 'recall' or not row.get('reference'):
        return row
    from .material_questions import _answer_group_metadata, legacy_answer_units, previous_answer_units
    group_rows = _answer_group_metadata(row['reference'])
    groups = [group['text'] for group in group_rows]
    selected = row.get('key_points') or []
    spans = []
    for group in group_rows:
        start, end = group.get('text_span') or (0, len(row['reference']))
        # The parser spans include indentation/newlines that its literal text
        # strips for display. Trim only those boundaries, not source content.
        while start < end and row['reference'][start].isspace():
            start += 1
        while end > start and row['reference'][end - 1].isspace():
            end -= 1
        spans.append((start, end))
    indices = set()
    unmatched = False
    for part in selected:
        exact = next((i for i, group in enumerate(groups) if part == group), None)
        if exact is not None:
            indices.add(exact)
            continue
        # A 213 checklist block can contain several independent sibling items.
        # Expand only the items in that literal source span, never all later
        # mentions of a short label. Old title-only selections have no span in
        # the new answer text, so contribute context, not another checkbox.
        position = None
        if part in previous_answer_units(row['reference']):
            position = row['reference'].find(part)
        else:
            offset = 0
            for line in row['reference'].splitlines(keepends=True):
                if part in legacy_answer_units(line):
                    position = offset + line.index(part)
                    break
                offset += len(line)
        if position is None:
            unmatched = True
            continue
        end_position = position + len(part)
        covered = [i for i, (start, end) in enumerate(spans)
                   if (position <= start and end <= end_position)
                   or (start <= position < end and part in groups[i])]
        if not covered:
            # Selected old headings identify their own following explanation or
            # sibling list via original heading offsets, never all later
            # occurrences of a common label or the whole answer.
            covered = [i for i, group in enumerate(group_rows)
                       if any(start <= position < end for start, end, _ in group['heading_spans'])]
        indices.update(covered)
        if not covered:
            unmatched = True
    # Unknown older selections must not silently remove part of a question.
    # Preserve access to the complete literal answer groups in that case;
    # already revealed cards/history never pass through this projection.
    points = ([group for i, group in enumerate(groups) if i in indices] if not unmatched else groups) or groups
    return dict(row, key_points=points)


def recall_key_point_headings(row):
    """Display-only context aligned to current strings, never a sync field."""
    from .material_questions import answer_groups
    headings = {group['text']: group.get('heading') for group in answer_groups(row.get('reference', ''))}
    return [headings.get(point) for point in row.get('key_points', [])]


def assessment_questions(rows, module_id=None):
    eligible = [q for q in rows if assessment_question(q) and (module_id is None or q['module_id']==module_id)]
    # Concepts retain one objective and one cloze per subpoint. A subpoint may
    # also have several distinct source recall questions.
    selected = {}; recall_content = set(); result = []
    for q in eligible:
        sid=q.get('subpoint_id')
        if sid:
            group=(q['module_id'],sid)
            recall=q.get('kind')=='recall' and q.get('origin')=='found'
            cloze=q.get('origin')=='cloze'
            state=selected.setdefault(group,dict(objective=False,cloze=False))
            if recall:
                content=(group,original_text(q.get('stem','')),original_text(q.get('reference','')))
                if content in recall_content: continue
                recall_content.add(content)
            elif cloze:
                if state['cloze']: continue
                state['cloze']=True
            else:
                if state['objective']: continue
                state['objective']=True
        result.append(with_recall_key_points(q))
    return result


def _first_closed_detection(store, plan_id, module_id):
    """Return the earliest closed module/skip detection for this module."""
    rows = [row['event'] for row in store.records('attempt_closed', plan_id)
            if row.get('event', {}).get('module_id') == module_id
            and row.get('event', {}).get('kind') in ('module_test', 'skip_test')]
    return min(rows, key=lambda row: (row['seq'], row['event_id'])) if rows else None


def module_supplemental_cutoff(store, plan_id, module_id):
    """Get the first detection boundary without inventing a close timestamp.

    ``AttemptClosed`` intentionally has no timestamp or question list. The
    associated judgement times identify the last answer. A close without
    judgements has no reliable historical time, so its boundary is explicitly
    the current preparation start.
    """
    closed = _first_closed_detection(store, plan_id, module_id)
    if not closed:
        return None
    attempt_id, kind = closed['attempt_id'], closed['kind']
    answered_at = []
    for row in store.records('judgements', plan_id):
        event = row.get('event', {})
        if (event.get('module_id') == module_id and event.get('attempt_id') == attempt_id
                and event.get('context') == kind and event.get('at')):
            answered_at.append(event['at'])
    for row in store.records('question_answers', plan_id):
        if (row.get('module_id') == module_id and row.get('attempt_id') == attempt_id
                and row.get('context') == kind and row.get('at')):
            answered_at.append(row['at'])
    if answered_at:
        return dict(at=max(answered_at, key=_time), basis='last_answer')
    return dict(at=datetime.now().astimezone().isoformat(), basis='preparation_start')


def supplemental_question_ids(store, plan_id, module_id, *, cutoff=None):
    """Return valid post-detection questions not present in its original answers.

    This is a read projection for final-check routing. It adds no field to
    question rows and never changes the frozen detection question set.
    """
    module = next((row for row in store.records('modules', plan_id)
                   if row['id'] == module_id), None)
    cutoff = cutoff or (module or {}).get('question_extraction', {}).get('supplemental_after')
    closed = _first_closed_detection(store, plan_id, module_id)
    if not cutoff and closed:
        # Older modules may have a closed attempt but predate the persisted
        # preparation cutoff. A real last-answer instant is sufficient to
        # recover late questions read-only; a skip with no answers is not.
        boundary = module_supplemental_cutoff(store, plan_id, module_id)
        if boundary and boundary['basis'] == 'last_answer':
            cutoff = boundary['at']
    if not cutoff or not closed:
        return set()
    tested = set()
    for row in store.records('question_answers', plan_id):
        if (row.get('module_id') == module_id and row.get('attempt_id') == closed['attempt_id']
                and row.get('context') == closed['kind'] and row.get('question_id')):
            tested.add(row['question_id'])
    for row in store.records('judgements', plan_id):
        event = row.get('event', {})
        if (event.get('module_id') == module_id and event.get('attempt_id') == closed['attempt_id']
                and event.get('context') == closed['kind'] and event.get('question_id')):
            tested.add(event['question_id'])
    return {q['id'] for q in assessment_questions(store.records('question_bank', plan_id), module_id)
            if q['id'] not in tested and q.get('created_at') and _time(q['created_at']) > _time(cutoff)}


def grading_question(row):
    """Adapt literal objective answers to the existing deterministic grader."""
    if row.get('kind')!='objective':return row
    q=dict(row);answer=q.get('answer');options=q.get('options') or []
    if options:
        if isinstance(answer,list) and all(type(i) is int for i in answer):indices=answer
        elif isinstance(answer,str):
            # Grading must preserve signs and decimal points; provenance's
            # punctuation-free comparison would equate -2/2 and 1.5/15.
            indices=[i for i,text in enumerate(options) if answer_compare.normalize(text)==answer_compare.normalize(answer)]
            if not indices:
                proxy=dict(q,kind='multi');indices=answer_compare.choice_text(proxy,answer)
        else:indices=None
        if not indices:raise ValueError('标准答案无法对应资料选项。')
        q.update(kind='choice' if len(indices)==1 else 'multi',answer=indices)
    elif isinstance(answer,str) and answer_compare.can_grade(dict(q,kind='calc')):q['kind']='calc'
    elif isinstance(answer,str) and answer.strip():q.update(kind='fill',answer=[[answer]])
    else:raise ValueError('客观题缺少原文标准答案。')
    return q


def review_questions(practice_results):
    latest={}
    for row in sorted(practice_results,key=lambda r:(r['at'],r.get('sequence',0),r['id'])):
        latest[row['question_id']]=not row['correct']
    return {qid for qid,wrong in latest.items() if wrong}


def checked_question(row):
    """Original answered exercises are eligible; gaps need their retained review."""
    if row.get('origin')=='found':
        return objective_question(row) and row.get('distractor_check',{}).get('state')!='rejected'
    passed=objective_question(row) and isinstance(row.get('distractor_check'),dict) and row['distractor_check'].get('state')=='passed'
    if row.get('origin')=='cloze':
        passed=passed and bool(row.get('cloze_subject')) and len(row.get('contrast_evidence',[]))>=2
    return passed


def review_points(practice_results):
    """A point needs review while any associated question's latest answer is wrong."""
    latest={}
    for row in sorted(practice_results,key=lambda r:(r['at'],r.get('sequence',0),r['id'])):
        latest[row['question_id']]=row
    return {pid for row in latest.values() if not row['correct'] for pid in row['point_ids']}
