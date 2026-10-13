"""Optional short model pass. Only phonetic edits survive; never submit text."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import re
import threading
import time

from pypinyin import lazy_pinyin
from .glossary import correct

SYSTEM = ('你是语音转写同音纠错器，不是答题老师。输入是学生已经说过的话。'
          '只改与本课词表读音接近的识别错字、英文术语拼写；保留口语、否定、犹豫和错误答案。'
          '不能根据老师问题替学生答题，不能补识别中完全没有的词，不能解释。'
          '拿不准原样返回。只输出纠正后的一句，不加引号。')
_ORAL = frozenset('那个 这个 不知道 没听懂 我不会 是的 对 不对 再说一次 我想想 不是这样'.split())


def _compact(text): return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', text.lower())


def needs_model(text, terms, question=''):
    if not question or not text.strip() or len(text) > 160 or _compact(text) in _ORAL: return False
    # A mangled Latin spelling can coexist with another correctly read term.
    latin=[_compact(m) for m in re.findall(r'[A-Za-z][A-Za-z -]*',text)]
    canonical=[_compact(t) for t in terms if re.search('[a-zA-Z]',t)]
    canonical.extend(word.lower() for t in terms for word in re.findall('[A-Za-z]+',t))
    if any(word not in canonical and any(.6<=SequenceMatcher(None,word,t).ratio()<1 for t in canonical)
           for word in latin if len(word)>=3 or (len(word)==2 and any(t.startswith(word) and 3<=len(t)<=4 for t in canonical))): return True
    # A correctly recognized lesson term is sufficient; do not polish speech.
    if re.search(r'来个[。!?！？]?$',text): return True
    if any(_compact(t) and _compact(t) in _compact(text) for t in terms): return False
    # Without any phonetic evidence, filling in a missing word would amount
    # to answering for the student. In particular, never map 那个 to Agent.
    for term in terms:
        if not re.fullmatch(r'[\u4e00-\u9fff]{2,8}',term): continue
        for run in re.findall(r'[\u4e00-\u9fff]+',text):
            for i in range(len(run)-len(term)+1):
                part=run[i:i+len(term)]
                if part not in _ORAL and SequenceMatcher(None,_sound(part),_sound(term)).ratio()>=.75:
                    return True
    return False


def _sound(text):
    return ''.join(lazy_pinyin(text.lower())).replace('ng','n')


def acceptable(original, candidate, terms, protected_terms=()):
    if not candidate or '\n' in candidate or len(candidate) > len(original)*1.5+8: return False
    if _compact(original)==_compact(candidate): return True
    # Never invent missing English terms from a wholly Chinese answer.
    if re.search('[a-zA-Z]',candidate) and not re.search('[a-zA-Z]',original): return False
    if not any(_compact(t) in _compact(candidate) for t in terms if _compact(t)): return False
    ops=SequenceMatcher(None,original.lower(),candidate.lower(),autojunk=False).get_opcodes()
    for word in protected_terms:
        for m in re.finditer(re.escape(word),original):
            if any(tag!='equal' and a<m.end() and m.start()<b for tag,a,b,_,_ in ops): return False
    # Validate an entire mixed-script term together: 香ra -> 向量RAG cannot
    # be judged from the isolated inserted 量 or G. Both parts need existing
    # phonetic evidence; a Chinese answer cannot acquire a missing English word.
    allowed=[]
    for term in terms:
        body=r'[ \t-]*'.join(re.escape(c) for c in re.sub(r'[ \t-]','',term))
        for m in re.finditer(body,candidate,re.I):
            pieces=[(a,b,c,d) for tag,a,b,c,d in ops if c<m.end() and m.start()<d]
            if not pieces: continue
            first,last=pieces[0],pieces[-1]
            a=first[0]+max(0,m.start()-first[2]) if first[1]-first[0]==first[3]-first[2] else first[0]
            b=last[1]-max(0,last[3]-m.end()) if last[1]-last[0]==last[3]-last[2] else last[1]
            before=original[a:b];after=m.group()
            if not before or (re.search('[A-Za-z]',after) and not re.search('[A-Za-z]',before)): continue
            if SequenceMatcher(None,_sound(before),_sound(after)).ratio()>=.7:
                allowed.append(m.span())
    for tag,a,b,c,d in ops:
        if tag=='equal': continue
        if c<d and any(start<=c and d<=end for start,end in allowed): continue
        before,after=original[a:b],candidate[c:d]
        if not _compact(before) and not _compact(after): continue
        # Spelling insertions/deletions inside a Latin word are allowed only
        # when the resulting word is an exact caller-supplied course term.
        if re.fullmatch('[a-zA-Z -]*', before+after):
            word=next((m.group() for m in re.finditer(r'[a-zA-Z][a-zA-Z -]*',candidate)
                       if m.start()<=c<=m.end()), '')
            if _compact(word) and any(_compact(word)==_compact(t) for t in terms): continue
        if not before or not after: return False
        if len(before)!=len(after): return False
        sounds_a,sounds_b=lazy_pinyin(before),lazy_pinyin(after)
        def sound(s): return re.sub(r'^l','n',s.replace('ng','n'))
        if any(SequenceMatcher(None,sound(x),sound(y)).ratio()<.65
               for x,y in zip(sounds_a,sounds_b)): return False
    return True


@dataclass(frozen=True)
class Correction:
    text: str
    called: bool = False
    outcome: str = 'local'
    seconds: float = 0.


def finish(raw, local, terms, question, gateway, *, cancel=None, timeout=3.,protected_terms=()):
    """Caller runs off GUI; hard return deadline even for an unresponsive seam.

    Cancel is per-call, never the gateway's global cancellation. Late results
    are ignored. Gateway still completes its normal usage/failure accounting.
    """
    if gateway is None or not needs_model(local,terms,question) or timeout<=0:
        return Correction(local)
    started=time.monotonic();done=threading.Event();token=threading.Event();box={}
    def run():
        try:
            client=gateway() if callable(gateway) else gateway
            box['result']=client.complete('asr_correction',[{'role':'user','content':json.dumps(
                dict(recognized=local,teacher_question=re.sub(r'\[([^]]*)\]\([^)]+\)',r'\1',question)[-600:],
                     terms=list(terms)[:40]),ensure_ascii=False)}],
                system=SYSTEM,max_tokens=80,cancel=token)
        except Exception: box['error']=True
        finally: done.set()
    threading.Thread(target=run,name='mf-asr-correction-call',daemon=True).start()
    deadline=started+min(3.,timeout)
    while not done.wait(min(.02,max(0.,deadline-time.monotonic()))):
        if (cancel is not None and cancel.is_set()) or time.monotonic()>=deadline:
            token.set()
            return Correction(local,True,'timeout',time.monotonic()-started)
    result=box.get('result');text=getattr(result,'text','').strip()
    accepted=acceptable(local,text,terms,protected_terms)
    return Correction(text if accepted else local,True,'accepted' if accepted else 'fallback',time.monotonic()-started)
