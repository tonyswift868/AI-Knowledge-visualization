"""Conservative, offline corrections using only caller-provided course terms.

Chinese thresholds: >= 2 Han characters, the same number of syllables, ignoring
tones. Exact homophones qualify. Otherwise exactly ONE syllable may differ; it
must have the same nonempty initial (zh/ch/sh stay distinct from z/c/s) and at
most one spelling insertion/deletion/substitution. This admits duan -> dun in
扭断 -> 牛顿 but rejects wider guesses. Zero-initial syllables are not fuzzy
matched. Phrase-aware pypinyin readings are used, without trying every heteronym.

English matching ignores ASCII case and horizontal spaces, with Latin word
boundaries; it never changes letters or crosses punctuation/newlines. Single
character terms and mixed-script/punctuated Chinese titles are left alone.
If a canonical term already occurs anywhere in the input, it is protected and
we do not introduce more occurrences of that term. Equal-quality alternatives
are left unchanged; exact matches take priority over fuzzy matches, then longer
terms over shorter ones. These restrictions favor missed corrections over
silently changing an answer's meaning.

Offsets are zero-based Python character positions in the ORIGINAL text, end
exclusive (not UTF-16 offsets). Edits are sorted and never overlap. Neither the
input nor earlier edits are fed back into matching, so corrections cannot cascade.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from difflib import SequenceMatcher
import re

from pypinyin import Style, lazy_pinyin


Replacement = tuple[int, int, str, str]
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]+")
_ENGLISH = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]*(?:[ \t]+[A-Za-z0-9+#.\-]+)*\Z")
_INITIAL = re.compile(r"^(zh|ch|sh|[bpmfdtnlgkhjqxrzcs])")


def _initial(syllable: str) -> str:
    match = _INITIAL.match(syllable)
    return match.group() if match else ""


def _one_edit(a: str, b: str) -> bool:
    if len(a) == len(b):
        return sum(left != right for left, right in zip(a, b)) <= 1
    if len(a) > len(b):
        a, b = b, a
    if len(b) != len(a) + 1:
        return False
    return any(a == b[:i] + b[i + 1:] for i in range(len(b)))


def _score(source: tuple[str, ...], target: tuple[str, ...]) -> int | None:
    differences = [(a, b) for a, b in zip(source, target) if a != b]
    if not differences:
        return 0
    if len(differences) != 1:
        return None
    a, b = differences[0]
    if _initial(a) and _initial(a) == _initial(b) and _one_edit(a, b):
        return 1
    return None


@lru_cache(maxsize=32)
def _prepare(terms: tuple[str, ...]):
    chinese, english = {}, []
    for term in terms:
        if len(term) < 2:
            continue
        if _HAN.fullmatch(term):
            sounds = tuple(lazy_pinyin(term, style=Style.NORMAL))
            if len(sounds) == len(term) and all(re.fullmatch("[a-z]+", sound) for sound in sounds):
                chinese.setdefault(len(term), []).append((term, sounds))
        elif _ENGLISH.fullmatch(term):
            compact = re.sub(r"[ \t]", "", term)
            if len(compact) >= 2:
                body = r"[ \t]*".join(re.escape(char) for char in compact)
                pattern = re.compile(r"(?<![A-Za-z0-9_])" + body + r"(?![A-Za-z0-9_])", re.I | re.ASCII)
                english.append((term, pattern))
    return chinese, english


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    term: str
    score: int

    @property
    def rank(self):
        return self.score, -(self.end - self.start)


def correct(text: str, terms: Iterable[str], *, protect_existing=True, protected_terms=(), lesson_match=False) -> tuple[str, list[Replacement]]:
    """Return corrected text and reversible original-text edits; no I/O."""
    if not isinstance(text, str) or isinstance(terms, (str, bytes)):
        raise TypeError("text must be str and terms must be an iterable of strings")
    normalized = []
    for term in terms:
        if not isinstance(term, str):
            raise TypeError("each glossary term must be str")
        if term.strip():
            normalized.append(term.strip())
    chinese, english = _prepare(tuple(sorted(set(normalized))))
    if not chinese and not english:
        return text, []
    protected, candidates, present = [], [], set()
    for word in protected_terms:
        if len(word)>=2:
            protected.extend(match.span() for match in re.finditer(re.escape(word),text))
    for entries in chinese.values():
        for term, _ in entries:
            matches = list(re.finditer(re.escape(term), text))
            if matches:
                present.add(term)
                protected.extend(match.span() for match in matches)
    for term, pattern in english:
        matches = list(pattern.finditer(text))
        exact = [match for match in matches if match.group() == term]
        if exact:
            protected.extend(match.span() for match in exact)
            continue
        candidates.extend(_Candidate(*match.span(), term, 0) for match in matches)
        if lesson_match and len(re.sub(r'\s','',term))>=5:
            for run in re.finditer(r'[A-Za-z][A-Za-z -]*',text):
                compact=re.sub(r'[ -]','',run.group()).lower()
                target=re.sub(r'[ -]','',term).lower()
                if compact!=target and compact[:2]==target[:2] and SequenceMatcher(None,compact,target).ratio()>=.8:
                    candidates.append(_Candidate(*run.span(),term,2))
    for run in _HAN.finditer(text) if chinese else ():
        sounds = tuple(lazy_pinyin(run.group(), style=Style.NORMAL))
        if len(sounds) != len(run.group()):
            continue
        for width, entries in chinese.items():
            for offset in range(len(sounds) - width + 1):
                source = sounds[offset:offset + width]
                for term, target in entries:
                    if protect_existing and term in present:
                        continue
                    score = _score(source, target)
                    if score is None and lesson_match:
                        # Nasal endings are often missed together (下料 vs
                        # 向量). Each changed syllable must retain its initial
                        # and differ by at most one phonetic spelling edit.
                        pairs=[(a.replace('ng','n'),b.replace('ng','n')) for a,b in zip(source,target)]
                        diff=[(a,b) for a,b in pairs if a!=b]
                        if 1<=len(diff)<=2 and all(_initial(a) and _initial(a)==_initial(b) and _one_edit(a,b) for a,b in diff):
                            score=2
                    if score is not None:
                        start = run.start() + offset
                        candidates.append(_Candidate(start, start + width, term, score))
    def changes_protected(item, start, end):
        if not (item.start < end and start < item.end): return False
        if item.end-item.start != len(item.term): return True
        a,b=max(start,item.start),min(end,item.end)
        return text[a:b] != item.term[a-item.start:b-item.start]
    candidates = [item for item in candidates if not any(changes_protected(item,start,end)
                                                         for start,end in protected)]
    selected, ambiguous_spans = [], []
    for item in sorted(candidates, key=lambda item: (item.rank, item.start, item.term)):
        if any(item.start < end and start < item.end for start, end in ambiguous_spans):
            continue
        if any(item.start < other.end and other.start < item.end for other in selected):
            continue
        # Don't pick a spelling based on the caller's glossary ordering.
        alternatives = [other for other in candidates if other != item and other.rank == item.rank and
                        item.start < other.end and other.start < item.end]
        if alternatives:
            # A weaker guess must not win just because stronger candidates tie.
            ambiguous_spans.extend((entry.start, entry.end) for entry in [item, *alternatives])
            continue
        selected.append(item)
    edits = [(item.start, item.end, text[item.start:item.end], item.term)
             for item in sorted(selected, key=lambda item: item.start)]
    output, cursor = [], 0
    for start, end, _, replacement in edits:
        output.extend((text[cursor:start], replacement))
        cursor = end
    output.append(text[cursor:])
    return "".join(output), edits
