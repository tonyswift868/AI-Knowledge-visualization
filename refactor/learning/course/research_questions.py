"""Cut original exercise spans before model matching; never write questions.

Numbered questions, option lists and labelled/numbered answer sections are
retained. Prose/menus are not sent. Every span stays contiguous in its original
paragraph; separate answer keys retain their numbers for existing pairing.
"""
import hashlib
import re

_NUMBER = re.compile(r'(?m)^\s*(?:#{1,6}\s*)?(?:\d{1,4}[.．、)）]\s*|[（(]\d{1,4}[)）]\s*|(?:Question|Q)\s*\d+\s*[:.)]\s*)', re.I)
_OPTIONS = re.compile(r'(?:^|\s)[A-H][.．、)）:：]\s*|[（(][A-H][)）]\s*', re.M)
_ANSWER = re.compile(r'(?:参考答案|正确答案|答案|解析|Answer|Solution)\s*(?:[：:]|[】\]])', re.I)
_KEY = re.compile(r'^\s*\d{1,4}[.．、)）]\s*(?:[A-H](?:\s*[,、，]\s*[A-H])*\b|[\wµμΩ℃.%+-]{1,24}\s*[。.]?$)', re.M)


def question_blocks(passages):
    """Return only exact slices, with stable IDs and original source locators.

    This is a permissive shape filter, not a correctness checker. The existing
    locator/compiler still require a literal question and its own answer.
    An oversized exercise is omitted whole, never clipped into a shorter one.
    """
    output=[]
    for p in passages:
        text=p['text'];starts=[m.start() for m in _NUMBER.finditer(text)]
        boundaries=sorted(set([0]+starts+[len(text)]))
        for left,right in zip(boundaries,boundaries[1:]):
            span=text[left:right]
            if not (_OPTIONS.search(span) or _ANSWER.search(span) or _KEY.search(span)):
                continue
            if not span.strip() or len(span)>8000:continue
            identity=p['id']+'-question-'+hashlib.sha256(f'{left}:{right}:{span}'.encode()).hexdigest()[:16]
            row=dict(p,id=identity,original_id=p.get('original_id',p['id']),text=span,question_span=[left,right])
            # Material references keep the saved chunk's locator. Discovered
            # excerpts get a unique span locator and will be persisted as such.
            if p.get('question_origin')=='web':
                row['source']=dict(p['source'],locator=p['source']['locator']+f'；原文字符 {left}:{right}')
            output.append(row)
    return output


def bounded_questions(passages, points, *, max_chars=32000, max_blocks=48):
    """Rank within source bands and bound whole exercise spans, never prose."""
    from .source_quality import terms, question_priority
    wanted=terms(' '.join(p.get('title','')+' '+p.get('summary','') for p in points))
    def key(p):
        # Adopted material is never re-filtered by publisher status.
        priority=(0,False) if p.get('question_origin')!='web' else question_priority(
            p['source']['url'],p['source']['title'],authority=p.get('authority',{}))
        return priority+(-sum(t in p['text'].casefold() for t in wanted),p['id'])
    output=[];size=0
    for p in sorted(passages,key=key):
        if size+len(p['text'])>max_chars:continue
        output.append(p);size+=len(p['text'])
        if len(output)>=max_blocks:break
    return output
