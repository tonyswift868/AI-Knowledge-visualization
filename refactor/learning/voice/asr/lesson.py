"""Small, deterministic lesson vocabulary; no publisher/model calls or I/O.

The installed sherpa 1.12.31 HR API loads a precompiled FST, not an in-memory
course dictionary (homophone-replacer.cc). We reuse the existing MIT pypinyin
fallback rather than ship a Windows OpenFst/Pynini compiler with each lesson.
"""
from collections import Counter
import re

_LATIN = re.compile(r'[A-Za-z][A-Za-z0-9]*(?:[-+][A-Za-z0-9]+)*(?:[ \t]+[A-Za-z][A-Za-z0-9-]*){0,2}')
_HAN = re.compile(r'[\u4e00-\u9fff]{2,16}')
_COMMON = frozenset('这个 那个 这里 那里 什么 为什么 怎么 可以 不是 就是 还是 如果 因为 所以 我们 你们 他们 现在 然后 一个 一种 一些 这些 那些 需要 没有 已经 时候 问题 例如 比如 以及 可能 应该 主要 进行 使用 通过 不同 相同 当场 提前 决定'.split())


def vocabulary(titles=(), question='', passages=(), *, limit=160):
    """Titles first, then marked/named terms and repeated title subphrases.

    Only repeated substrings that also occur in a point title qualify. This
    avoids turning every common phrase in a long source into a correction.
    Capture once per recording; deterministic order and bounded payload.
    """
    titles = [str(t)[:120] for t in titles]
    passages = list(passages)
    sources = '\n'.join(str(p) for p in passages)[:60000]
    candidates = []
    for text in [*titles, question[:1600], sources]:
        candidates.extend(re.findall(r'\*\*([^*\n]{2,60})\*\*|`([^`\n]{2,60})`', text))
    marked = [part for pair in candidates for part in pair if part]
    result = []
    def add(term):
        term = term.strip(' ：:，,。.?？()（）/').strip()
        if 2 <= len(term) <= 60 and term not in _COMMON and term not in result:
            result.append(term)
    # Give the current point's repeated core nouns priority over a large
    # module's vocabulary (e.g. 向量 within 向量 RAG 的对比).
    for title in titles[:1]:
        for term in _LATIN.findall(title): add(term)
        for run in _HAN.findall(title):
            for width in (4,3,2):
                for i in range(len(run)-width+1):
                    word=run[i:i+width]
                    if sources.count(word)>=3: add(word)
    for text in [question[:1600],*(str(p)[:6000] for p in passages[:2])]:
        for term in _LATIN.findall(text):
            add(term)
            for word in term.split():
                if word.lower() not in {'and','with','the','of','to','is'}: add(word)
    for text in [*titles[:1], *marked, *titles[1:]]:
        for term in _LATIN.findall(text):
            add(term)
            for word in re.split(r'[ \t]+',term):
                if word.lower() not in {'and','with','the','of','to','is'}: add(word)
        for run in _HAN.findall(text):
            for term in re.split(r'与|以及|及其|及|的|和|什么是|是什么', run): add(term)
    # Source vocabulary protects already valid short nouns separately; only
    # repeated title substrings are candidates for spelling correction.
    for text in [question[:1600], sources]:
        for term in _LATIN.findall(text): add(term)
    repeated = Counter()
    for title in titles:
        for run in _HAN.findall(title):
            for width in (4, 3, 2):
                for i in range(len(run)-width+1):
                    word=run[i:i+width]
                    if word not in _COMMON and sources.count(word) >= 3:
                        repeated[word] = sources.count(word)
    for word in sorted(repeated, key=lambda w:(-len(w), -repeated[w], w)): add(word)
    # Ordinary speech cannot become a terminology candidate, even if quoted.
    return tuple(result[:limit])


def lesson_context(store, plan_id, module_id, point_id=''):
    points=[p for p in store.records('points', plan_id) if p.get('module_id') == module_id]
    if not points: return dict(terms=[], question='')
    current=next((p for p in points if p['id']==point_id), points[0])
    ordered=[current, *(p for p in points if p is not current)]
    ids={cid for p in ordered for cid in p.get('chunk_ids', [])}
    chunks={c['id']:c for c in store.records('source_chunks',plan_id) if c['id'] in ids}
    ordered_ids=list(dict.fromkeys(cid for p in ordered for cid in p.get('chunk_ids',[]) if cid in chunks))
    passages=[chunks[cid].get('text','') for cid in ordered_ids]
    messages=store.messages(plan_id,module_id)
    question=next((m.get('content','') for m in reversed(messages) if m.get('role')=='assistant'), '')
    return dict(terms=list(vocabulary([p.get('title','') for p in ordered], question, passages)),
                protected=list(protected_vocabulary(passages)),
                question=question[-1600:])


def protected_vocabulary(passages):
    """Exact source bigrams protect already-valid oral words from fuzzy edits.

    They are NOT correction targets. E.g. 节点/结构 must not become 阶段/架构
    just because both technical nouns have nearby pronunciations.
    """
    text='\n'.join(passages)[:60000]
    return tuple(sorted({run[i:i+2] for run in re.findall(r'[\u4e00-\u9fff]+',text)
                         for i in range(len(run)-1)})[:6000])
