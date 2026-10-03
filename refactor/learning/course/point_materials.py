"""Prepare source passages and answered questions, never generate or solve them.

ctx is a mapping: gateway (ModelGateway), cancel (Event), lists (SourceLists),
optional finder(point, passages, *, mode, cancel) -> JSON object,
search(query, *, cancel) -> hits, fetcher (ResearchPipeline byte fetcher), ocr.
finder replaces only the locator model in offline tests; search replaces only
discovery. Public pick/mark functions perform no model/network work.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
from threading import Lock, RLock
from weakref import WeakKeyDictionary

from .question_bank import contains_original, original_text, validate_question, select_question
from .source_filter import classify_source, load_lists, RawHit
from .research import directory_candidates, ResearchCancelled
from .research_pipeline import ResearchPipeline
from .materials import import_verified_source
from .store import StoreError
from .source_quality import terms
from ..connections.errors import ModelError

_locks = WeakKeyDictionary()
_locks_guard = Lock()


def _lock(store, plan, point):
    with _locks_guard:
        return _locks.setdefault(store, {}).setdefault((plan, point), RLock())


def _check(ctx):
    if ctx.get('cancel') is not None and ctx['cancel'].is_set(): raise ResearchCancelled()


def _json(text):
    if text.strip().startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    result = json.loads(text)
    if not isinstance(result, dict): raise ValueError('资料定位结果必须为 JSON 对象')
    return result


_SYSTEM = '''你是资料定位员，不是出题员或解题员。只从输入原文找、指出和配对，绝不写题、写答案、解题或改写原句。
返回严格 JSON，只有 passages（相关块 id 列表）、found（现成带答案题列表）、cloze（原文挖空指针列表）。
found 每项：kind(choice/multi/fill/short/essay/calc)、stem（题面原文）、options（选择题原选项）、answer（choice/multi 为从0开始的下标列表；fill 为每个空的原答案列表；其他为参考答案原文）、explanation（原解析或空）、question_chunk、answer_chunk、answer_quote（包含参考答案的原文）、pairing(same_block 或 numbered)、question_number、answer_number。
same_block 仅可用于同一块里该题紧接的“答案/解析/Solution/Answer”。跨块须 numbered，题号与答案号一致。配不上、不带答案就不返回。绝不能自行推算选项下标；必须依据明确的答案标记/原文选项。
cloze 每项：chunk_id、sentence（完整原句）、word（句中只出现一次的原词）、importance(key/normal)、keywords（同一资料同章节的其他同类原词指针）。定义句、公式和重点做 key；其他 normal。只指出，不改写。给出最多5个独立关键句供程序取3题，不选无关句或导航、学习目标清单。passages 必须真的解释当前知识点。找不到如实返回空数组。'''


def _locate(point, passages, ctx, mode):
    _check(ctx)
    wanted=terms(point.get('title','')+' '+point.get('summary',''))
    # Keep complete chunks, rank linked/relevant ones first. No whole textbook
    # in the prompt; a selected quote is still validated against original text.
    ranked=sorted(passages,key=lambda p:(p['id'] not in point.get('chunk_ids',[]),
        -sum(t in p['text'].casefold() for t in wanted),p['id']))
    selected=[];size=0
    for passage in ranked:
        if size+len(passage['text'])>32000: continue
        selected.append(passage);size+=len(passage['text'])
        if len(selected)>=16: break
    passages=selected
    if ctx.get('finder'):
        output = ctx['finder'](point, passages, mode=mode, cancel=ctx.get('cancel'))
    else:
        if ctx.get('gateway') is None: raise ValueError('准备资料需要 gateway 或离线 finder')
        result = ctx['gateway'].complete('point_materials', [{'role':'user','content':json.dumps(
            dict(point=point, mode=mode, passages=passages), ensure_ascii=False)}],
            system=_SYSTEM, cancel=ctx.get('cancel'), plan_id=ctx['plan_id'], max_tokens=8192)
        output = _json(result.text)
        if ctx.get('on_call'): ctx['on_call']('point_materials', result)
    _check(ctx)
    if not isinstance(output,dict) or any(not isinstance(output.get(k,[]),list) for k in ('passages','found','cloze')):
        raise ValueError('资料定位结果字段无效')
    return output


def _identity(source, chunks, ctx):
    body = '\n'.join(c['text'] for c in chunks)
    url = source.get('final_url') or source.get('url') or ''
    evidence = source.get('publisher_evidence')
    identity = classify_source(url, lists=ctx['lists'], page_text=body, publisher_evidence=evidence)
    if identity['level'] or identity['type']=='excluded': return identity
    if ctx.get('gateway') and body.strip():
        result = ctx['gateway'].complete('source_identity',[{'role':'user','content':json.dumps(
            dict(url=url,title=source['title'],text=body[:10000]),ensure_ascii=False)}],
            system='只判断页面原文可见发布者属于 government、school、national_education、textbook_publisher、open_textbook、official_documentation、education_platform 中哪一类。社区、转载、盗版或不能确认返回 unknown。返回 JSON {"type":"...","publisher":"...","quote":"原文关于发布主体/作者机构/备案的信息"}。不能依据域名猜测或编造证据。',
            cancel=ctx.get('cancel'),plan_id=ctx['plan_id'],max_tokens=2048)
        evidence = _json(result.text)
        if ctx.get('on_call'): ctx['on_call']('source_identity',result)
        identity = classify_source(url,lists=ctx['lists'],page_text=body,publisher_evidence=evidence)
        _check(ctx)
        if identity['level']:
            source['publisher_evidence']=evidence
            if source.get('id'):
                ctx['store'].set_source_publisher_evidence(ctx['plan_id'],source['id'],evidence)
    return identity


def _passages(store, plan_id, ctx):
    chunks = store.records('source_chunks',plan_id)
    output=[]
    for source in store.records('sources',plan_id):
        if not source['adopted']: continue
        own=[c for c in chunks if c['source_id']==source['id']]
        identity=_identity(source,own,ctx)
        if identity['level'] not in (1,2): continue
        for chunk in own:
            if not chunk['text'].strip(): continue
            section=' / '.join(chunk.get('section',[]))
            page=chunk.get('page_start')
            locator=(section+'；' if section else '')+(f'第{page}页；' if page else '')+f"正文块 {chunk['position']+1} [{chunk['id']}]"
            output.append(dict(id=chunk['id'],source_id=source['id'],text=chunk['text'],section=section,
                source=dict(title=source['title'],url=source.get('final_url') or source.get('url') or '',locator=locator),
                authority=identity))
    return output


def _base(point, kind, stem, answer, origin, question, answer_source, explanation='', importance='normal', options=None):
    row=dict(id='',module_id=point['module_id'],point_ids=[point['id']],kind=kind,stem=stem,answer=answer,
        explanation=explanation,origin=origin,source=dict(question['source']),answer_source=dict(answer_source['source']),
        importance=importance,uses=[],created_at=datetime.now(timezone.utc).isoformat())
    if kind in ('choice','multi'): row['options']=options
    content={k:v for k,v in row.items() if k not in ('id','created_at','uses')}
    row['id']='material-'+hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    validate_question(row)
    return row


def _numbered(text,number):
    return isinstance(number,str) and bool(re.search(r'(?:^|\n)\s*(?:第)?'+re.escape(number)+r'(?:[.．、):：]|题)',text))


def _found(item, point, by_id):
    q,a=by_id[item['question_chunk']],by_id[item['answer_chunk']]
    stem,quote=item['stem'],item['answer_quote']
    if not contains_original(stem,q['text']) or not contains_original(quote,a['text']): return None
    if item['pairing']=='same_block':
        if q['id']!=a['id']: return None
        # Same-block evidence must immediately follow this question, not another
        # question's answer elsewhere in a large chunk.
        # Locate the answer marker before looking for repeated option letters or
        # short answer words; searching quote first would pick option A itself.
        start=q['text'].find(stem)
        if start<0: return None
        tail=q['text'][start+len(stem):]
        marker=re.search(r'(?:【|\[)?(?:参考答案|答案|解析|Answer|Solution|解)(?:】|\])?\s*[:：\n]',tail)
        if not marker or len(tail[:marker.start()])>800: return None
        if re.search(r'(?:\n|^)\s*\d+[.、]',tail[:marker.start()]): return None
        answer_part=re.split(r'(?:\n|^)\s*\d+[.、]',tail[marker.end():],maxsplit=1)[0]
        if not contains_original(quote,answer_part): return None
    elif item['pairing']=='numbered':
        number=item.get('question_number')
        if number!=item.get('answer_number') or not _numbered(q['text'],number) or not _numbered(a['text'],number): return None
        # The pointed answer quote itself must carry the same number.
        if not _numbered(quote,number): return None
        if q['source_id']!=a['source_id']:
            # A number alone cannot pair questions from unrelated textbooks.
            from urllib.parse import urlsplit
            qp,ap=(urlsplit(p['source']['url']).path for p in (q,a))
            book=re.match(r'/books/([^/]+)/pages/',qp)
            if not book or not ap.startswith('/books/'+book[1]+'/pages/'): return None
    else: return None
    kind,answer=item['kind'],item['answer']
    options=item.get('options')
    if kind in ('choice','multi'):
        if not isinstance(options,list) or any(not contains_original(o,q['text']) for o in options): return None
        if not isinstance(answer,list) or any(type(i)is not int or not 0<=i<len(options) for i in answer): return None
        explicit=re.search(r'(?:答案|Answer|^\s*\d+[.、])\s*[:：]?\s*([A-Z]+)\b',quote)
        if re.fullmatch(r'[A-Z]+',quote.strip()): letters=quote.strip()
        else: letters=explicit[1] if explicit else ''
        if letters and {ord(letter)-65 for letter in letters}!=set(answer): return None
        for index in answer:
            # Explicit answer letter/index or the original correct option text.
            letter=chr(65+index)
            marked = (item['pairing']=='same_block' and bool(re.fullmatch(r'[A-Z]+',quote.strip()))
                      and letter in quote.strip())
            if not marked and not contains_original(options[index],quote) and not re.search(r'(?:答案|Answer|^\s*\d+[.、])\s*[:：]?\s*[A-Z]*'+letter+r'(?:[A-Z]*\b|[.。、])',quote): return None
    elif kind=='fill':
        if not isinstance(answer,list) or any(not isinstance(v,list) or not v or any(not contains_original(t,quote) for t in v) for v in answer): return None
    elif not isinstance(answer,str) or not contains_original(answer,quote): return None
    explanation=item.get('explanation','')
    if explanation and not contains_original(explanation,a['text']): return None
    return _base(point,kind,stem,answer,'found',q,a,explanation=explanation,options=options,
                 importance='key' if point.get('importance')=='key' else 'normal')


def _word_kind(word):
    if re.fullmatch(r'[\d.]+',word): return 'number'
    if re.fullmatch(r'[a-zA-Z]+\([^)]*\)|[a-zA-Z_]+(?:\.[a-zA-Z_]+)+',word): return 'code'
    if word in ('Pa','N','m','kg','s','kPa','MPa','牛','帕','千克','米','秒'): return 'unit'
    return 'term'


def _cloze(item,point,by_id,passages):
    chunk=by_id[item['chunk_id']]
    sentence,word=item['sentence'],item['word']
    # Exact quote and exact original word: even punctuation is retained in stem.
    if not isinstance(sentence,str) or not isinstance(word,str) or not word.strip() or sentence not in chunk['text'] or sentence.count(word)!=1:
        return None
    if len(sentence.strip())<12 or '____' in sentence: return None
    key=point.get('importance')=='key' or (item.get('importance')=='key' and bool(re.search(
        r'定义|称为|叫做|等于|是指|is\b|are\b|defined|returns?\b|removes?\b|adds?\b|inserts?\b|appends?\b|=',sentence,re.I)))
    stem=sentence.replace(word,'____',1)
    if key:
        return _base(point,'fill',stem,[[word]],'cloze',chunk,chunk,importance='key')
    # Model points to terms, program chooses original distractors. Same source,
    # same chapter, same kind; terms are never supplied as invented alternatives.
    same='\n'.join(p['text'] for p in passages if p['source_id']==chunk['source_id'] and p['section']==chunk['section'])
    candidates=[]
    for term in item.get('keywords',[]):
        if (isinstance(term,str) and term in same and _word_kind(term)==_word_kind(word)
                and original_text(term)!=original_text(word) and term not in sentence and term not in candidates):
            candidates.append(term)
    if len(candidates)<2: return None
    options=sorted([word,*candidates[:3]])
    return _base(point,'choice',stem,[options.index(word)],'cloze',chunk,chunk,options=options)


def _collect(items, converter, point, passages):
    by_id={p['id']:p for p in passages}; rows=[]
    for item in items:
        try:
            row=converter(item,point,by_id)
            if row is not None and row['id'] not in {q['id'] for q in rows}: rows.append(row)
        except (ValueError,KeyError,TypeError,IndexError): continue
    return rows


def _discover_questions(point, ctx):
    query=point.get('title',point.get('summary',''))+' '+ctx.get('stage','')+' 习题 真题 练习 答案'
    hits=directory_candidates(query,stage=ctx.get('stage',''),lists=ctx['lists'],limit=3)
    if ctx.get('search'):
        hits+=list(ctx['search'](query,cancel=ctx.get('cancel')))
    elif ctx.get('gateway'):
        result=ctx['gateway'].complete('research',[{'role':'user','content':query}],
            system='找现成带答案的练习，只找政府教育、正规教材出版社、大学、OpenStax、官方文档或成熟教育题库平台。不要出题或解题。',
            enable_search=True,cancel=ctx.get('cancel'),plan_id=ctx['plan_id'],max_tokens=2048)
        hits += [dict(url=h.url,title=h.title,snippet=h.snippet) for h in result.search_hits+result.citations]
        if ctx.get('on_call'): ctx['on_call']('research',result)
    if not hits: return
    _check(ctx)
    pipeline=ctx.get('pipeline')
    owned=pipeline is None
    pipeline=pipeline or ResearchPipeline(fetcher=ctx.get('fetcher'),max_sources=6,ocr=ctx.get('ocr'))
    try:
        result=pipeline.run(hits,lists=ctx['lists'],cancel=ctx.get('cancel'),
            accepted_urls=[s.get('url') for s in ctx['store'].records('sources',ctx['plan_id'])])
        _check(ctx)
        for source in result.verified:
            candidate=dict(url=source.url,final_url=source.final_url,title=source.title)
            identity=_identity(candidate,[dict(text=b.text) for b in source.document.blocks],ctx)
            if identity['level'] in (1,2):
                saved=import_verified_source(ctx['store'],ctx['plan_id'],source)
                if candidate.get('publisher_evidence'):
                    ctx['store'].set_source_publisher_evidence(ctx['plan_id'],saved['id'],candidate['publisher_evidence'])
    finally:
        if owned: pipeline.close()


def prepare_point(store, plan_id, point, ctx):
    """Idempotent, serialized per point; no model call holds the store lock.

    Failures/cancellation raise, never report a fabricated prepared count. A
    shortage returns its real counts; callers must show progress/missing-source
    state, not let a point with fewer than 2 questions enter an assessment.
    """
    ctx=dict(ctx,store=store,plan_id=plan_id,lists=ctx.get('lists') or load_lists())
    store.require_material_plan(plan_id)
    with _lock(store,plan_id,point['id']):
        _check(ctx)
        saved=next((p for p in store.records('points',plan_id) if p['id']==point['id']),None)
        if saved is None: raise StoreError('NOT_FOUND','知识点尚未保存。')
        point=saved
        rows=[q for q in store.records('question_bank',plan_id) if point['id'] in q['point_ids']]
        passages=_passages(store,plan_id,ctx)
        eligible={p['id'] for p in passages}
        linked=[cid for cid in point['chunk_ids'] if cid in eligible]
        if len(rows)>=2 and linked:
            return dict(passages=len(linked),questions=len(rows),cloze=sum(q['origin']=='cloze' for q in rows))
        if not passages: return dict(passages=0,questions=len(rows),cloze=sum(q['origin']=='cloze' for q in rows))
        located=_locate(point,passages,ctx,'adopted')
        # Explicit point mappings remain valid; model can only add original blocks.
        selected=list(dict.fromkeys(linked+[cid for cid in located.get('passages',[]) if cid in eligible]))
        found=_collect(located.get('found',[]),_found,point,passages)
        if sum(q['origin']=='found' for q in rows)+len(found)<2:
            try:
                _discover_questions(point,ctx)
                expanded=_passages(store,plan_id,ctx)
                if {p['id'] for p in expanded} != eligible:
                    extra=_locate(point,expanded,ctx,'answered_exercises')
                    selected+= [cid for cid in extra.get('passages',[]) if cid in {p['id'] for p in expanded}]
                    found+=_collect(extra.get('found',[]),_found,point,expanded)
                    located['cloze']=located.get('cloze',[])+extra.get('cloze',[])
                    passages=expanded
            except (ModelError,ValueError,OSError) as exc:
                # Missing external answered exercises cannot erase valid adopted
                # text pointers. Cancellation still fences every later write.
                _check(ctx)
                if getattr(exc,'code','')=='MODEL_CANCELLED': raise
                if ctx.get('on_status'):
                    ctx['on_status']('外部带答案题查找未完成，改用已核实的原文挖空：'+str(exc))
        selected=list(dict.fromkeys(selected))
        if not selected: return dict(passages=0,questions=len(rows),cloze=sum(q['origin']=='cloze' for q in rows))
        # Cloze pointers must refer to the passages actually linked to this point.
        by_id={p['id']:p for p in passages if p['id'] in selected}
        cloze=_collect(located.get('cloze',[]),lambda i,p,b:_cloze(i,p,by_id,passages),point,passages)
        candidates=found+cloze
        distinct={original_text(q['stem']) for q in rows}
        additions=[]
        for q in candidates:
            if len(rows)+len(additions)>=3: break
            if original_text(q['stem']) not in distinct:
                additions.append(q);distinct.add(original_text(q['stem']))
            if len(rows)+len(additions)>=3: break
        _check(ctx)
        with store.transaction():
            _check(ctx)
            store.link_point_sources(plan_id,point['id'],selected)
            store.add_bank_questions(plan_id,point['module_id'],additions)
            _check(ctx)
        rows=[q for q in store.records('question_bank',plan_id) if point['id'] in q['point_ids']]
        return dict(passages=len(selected),questions=len(rows),cloze=sum(q['origin']=='cloze' for q in rows))


def prepare_module(store,plan_id,module_id,ctx,progress):
    store.require_material_plan(plan_id)
    if not any(m['id']==module_id for m in store.records('modules',plan_id)):
        raise StoreError('NOT_FOUND','找不到当前计划的模块。')
    points=[p for p in store.records('points',plan_id) if p['module_id']==module_id]
    if not points: raise StoreError('NO_POINTS','该模块还没有已保存的知识点，不能报告资料准备完成。')
    totals=dict(passages=0,questions=0,cloze=0)
    for index,point in enumerate(points):
        _check(ctx)
        progress(index,len(points),'正在准备 '+point.get('title',point.get('summary',point['id'])))
        result=prepare_point(store,plan_id,point,ctx)
        for k in totals: totals[k]+=result[k]
        progress(index+1,len(points),f"已准备 {result['passages']} 段原文、{result['questions']} 道有出处的题")
    return totals


def pick_question(store,plan_id,point_id,purpose,exclude_ids=()):
    store.require_material_plan(plan_id)
    return select_question(store.records('question_bank',plan_id),point_id,purpose,exclude_ids)


def mark_used(store,plan_id,question_id,purpose):
    store.record_question_use(plan_id,question_id,purpose)


def uncovered_points(store,plan_id,ctx):
    """Per-point authority gate for the plan workflow, replacing source quotas."""
    ctx=dict(ctx,store=store,plan_id=plan_id,lists=ctx.get('lists') or load_lists())
    eligible={p['id'] for p in _passages(store,plan_id,ctx)}
    return [p['id'] for p in store.records('points',plan_id) if not eligible.intersection(p['chunk_ids'])]
