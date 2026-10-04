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


def validate_question(row):
    # Previously saved B-line questions remain bank questions; no invented
    # quick questions are inferred from their old usage history.
    keys = set(row) - {'_field_versions', 'role', 'source_level', 'source_type', 'target'}
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
          and q.get('role', 'bank') == role and objective_question(q)]
    def rank(q):
        from .source_quality import question_priority
        times=[_time(u['at']) for u in q['uses'] if u['purpose']==purpose]
        priority=question_priority(q['source']['url'],q['source']['title'],
            authority=dict(level=q['source_level'],type=q.get('source_type','')) if q.get('source_level') else None)
        return (*priority,bool(times),max(times,default=0),q['created_at'],q['id'])
    return min(pool,key=rank) if pool else None


def review_points(practice_results):
    """Latest free-practice outcome; used by the report/plan summary too."""
    latest={}
    for row in sorted(practice_results,key=lambda r:(r['at'],r.get('sequence',0),r['id'])):
        for pid in row['point_ids']:
            latest[pid]=pid in row.get('wrong_points',[] if row['correct'] else row['point_ids'])
    return {pid for pid,needs_review in latest.items() if needs_review}
