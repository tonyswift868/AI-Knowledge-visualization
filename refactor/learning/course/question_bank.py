"""Original-source question schema and model-free selection (indices start at 0)."""
from datetime import datetime
import unicodedata
import re

KINDS = frozenset(('choice', 'multi', 'fill', 'short', 'essay', 'calc'))
PURPOSES = frozenset(('quick', 'module', 'direct', 'final', 'retest', 'free'))
FIELDS = frozenset(('id', 'module_id', 'point_ids', 'kind', 'stem', 'answer',
    'explanation', 'origin', 'source', 'answer_source', 'importance', 'uses', 'created_at'))


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
    keys = set(row) - {'_field_versions', 'role'}
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
          and q.get('role', 'bank') == role]
    def rank(q):
        times=[_time(u['at']) for u in q['uses'] if u['purpose']==purpose]
        return (bool(times),max(times,default=0),q['created_at'],q['id'])
    return min(pool,key=rank) if pool else None


def review_points(practice_results):
    """Latest free-practice outcome; used by the report/plan summary too."""
    latest={}
    for row in sorted(practice_results,key=lambda r:(r['at'],r.get('sequence',0),r['id'])):
        for pid in row['point_ids']:
            latest[pid]=pid in row.get('wrong_points',[] if row['correct'] else row['point_ids'])
    return {pid for pid,needs_review in latest.items() if needs_review}
