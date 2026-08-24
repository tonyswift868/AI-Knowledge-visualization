"""Sparse, terminology-preserving corrections derived from transcript evidence.

The normalizer never mutates or reconstructs the immutable KA-02 evidence
bundle.  It scans locally for a small candidate set, optionally asks an
injected text provider about bounded batches, and stores only validated patches.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol

from .transcript_evidence import TranscriptEvidenceBundle, TranscriptSegment


NORMALIZER_VERSION = "mentorflow.terminology-normalizer.v1"
DEFAULT_CONTEXT_WINDOW = 1
MAX_CONTEXT_WINDOW = 2
DEFAULT_CORRECTION_BATCH_SIZE = 32
MAX_CORRECTION_BATCH_SIZE = 64
DEFAULT_FUZZY_SIMILARITY = 0.84
AUTO_GLOSSARY_CONFIDENCE = 0.95
AUTO_CONTEXTUAL_CONFIDENCE = 0.90
MAX_TERM_SPAN_LENGTH = 80
MAX_CONTEXT_CHARS = 1000
MAX_PROVIDER_PROMPT_CHARS = 256_000
MAX_PROVIDER_RESPONSE_CHARS = 256_000
MAX_PROVIDER_CORRECTIONS = 128
MAX_PROVIDER_REASON_CHARS = 500

_ASCII_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_-]{2,}(?![A-Za-z0-9_])")
_SENTENCE_DELIMITER_RE = re.compile(r"[\r\n。！？!?；;，：]|[.,:](?:\s|$)")
_SEMANTIC_CLAUSE_RE = re.compile(
    r"(?:\b(?:is|are|does|not|causes?|supports?)\b|不是|是线程|可以|不能|不会|导致)",
    re.IGNORECASE,
)

_PROVIDER_SYSTEM_PROMPT = """You identify terminology corrections only.
Return one JSON object with exactly one key, corrections. Each correction must
contain exactly segment_id, original_span, replacement, confidence, and reason.
Only choose a supplied candidate replacement. Never rewrite sentences, facts,
timestamps, segment IDs, parts, or ordering. Omit uncertain corrections."""


class CorrectionType(str, Enum):
    SAFE_DETERMINISTIC = "SAFE_DETERMINISTIC"
    HIGH_CONFIDENCE_CONTEXTUAL = "HIGH_CONFIDENCE_CONTEXTUAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class CorrectionOutcome(str, Enum):
    AUTO_APPLIED = "AUTO_APPLIED"
    SUGGESTED = "SUGGESTED"
    REJECTED = "REJECTED"
    UNCHANGED = "UNCHANGED"


class TextCorrectionProvider(Protocol):
    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str: ...


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


@dataclass(frozen=True)
class GlossaryTerm:
    canonical: str
    aliases: tuple[str, ...] = ()
    language: str = "und"
    source: str = "user-provided"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        canonical = _clean_text(self.canonical)
        if not canonical:
            raise ValueError("canonical terminology is required")
        try:
            confidence = float(self.confidence)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("terminology confidence must be numeric") from exc
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("terminology confidence must be between 0 and 1")
        aliases = tuple(
            sorted(
                dict.fromkeys(
                alias
                for raw_alias in self.aliases
                if (alias := _clean_text(raw_alias)) and alias.casefold() != canonical.casefold()
                ),
                key=str.casefold,
            )
        )
        if len(canonical) > MAX_TERM_SPAN_LENGTH or any(len(alias) > MAX_TERM_SPAN_LENGTH for alias in aliases):
            raise ValueError(f"terminology spans cannot exceed {MAX_TERM_SPAN_LENGTH} characters")
        object.__setattr__(self, "canonical", canonical)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "language", _clean_text(self.language) or "und")
        object.__setattr__(self, "source", _clean_text(self.source) or "unspecified")
        object.__setattr__(self, "confidence", confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical,
            "aliases": list(self.aliases),
            "language": self.language,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class TerminologyGlossary:
    terms: tuple[GlossaryTerm, ...]
    version: str = "v1"

    def __post_init__(self) -> None:
        terms = tuple(self.terms)
        canonical_keys = [term.canonical.casefold() for term in terms]
        if len(canonical_keys) != len(set(canonical_keys)):
            raise ValueError("glossary canonical terms must be unique")
        object.__setattr__(self, "terms", terms)
        object.__setattr__(self, "version", _clean_text(self.version) or "v1")

    @property
    def glossary_hash(self) -> str:
        return _sha256_json(
            {
                "version": self.version,
                "terms": [term.to_dict() for term in sorted(self.terms, key=lambda item: item.canonical.casefold())],
            }
        )


@dataclass(frozen=True)
class CorrectionCandidate:
    candidate_id: str
    segment_id: str
    original_span: str
    start_index: int
    end_index: int
    candidate_terms: tuple[str, ...]
    correction_type: CorrectionType
    confidence: float
    reason: str
    context_segment_ids: tuple[str, ...]
    source_segment_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "segment_id": self.segment_id,
            "original_span": self.original_span,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "candidate_terms": list(self.candidate_terms),
            "correction_type": self.correction_type.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "context_segment_ids": list(self.context_segment_ids),
            "source_segment_hash": self.source_segment_hash,
        }


@dataclass(frozen=True)
class TranscriptCorrection:
    patch_id: str
    candidate_id: str
    segment_id: str
    original_span: str
    replacement: str
    start_index: int
    end_index: int
    correction_type: CorrectionType
    outcome: CorrectionOutcome
    confidence: float
    reason: str
    context_segment_ids: tuple[str, ...]
    normalizer_version: str
    source_segment_hash: str
    provider: str | None = None
    model: str | None = None

    def identity_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "candidate_id": self.candidate_id,
            "segment_id": self.segment_id,
            "original_span": self.original_span,
            "replacement": self.replacement,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "correction_type": self.correction_type.value,
            "outcome": self.outcome.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "context_segment_ids": list(self.context_segment_ids),
            "normalizer_version": self.normalizer_version,
            "source_segment_hash": self.source_segment_hash,
            "provider": self.provider,
            "model": self.model,
        }


@dataclass(frozen=True)
class PatchRejection:
    code: str
    detail: str
    segment_id: str | None = None
    original_span: str | None = None
    batch_id: str | None = None
    patch_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "segment_id": self.segment_id,
            "original_span": self.original_span,
            "batch_id": self.batch_id,
            "patch_id": self.patch_id,
        }


@dataclass(frozen=True)
class CorrectionContextSegment:
    segment_id: str
    part_index: int
    part_cid: str | None
    start_ms: int
    end_ms: int
    original_text: str
    text_truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "part_index": self.part_index,
            "part_cid": self.part_cid,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "original_text": self.original_text,
            "text_truncated": self.text_truncated,
        }


@dataclass(frozen=True)
class CorrectionBatchItem:
    candidate: CorrectionCandidate
    part_title: str
    context: tuple[CorrectionContextSegment, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate.candidate_id,
            "segment_id": self.candidate.segment_id,
            "original_span": self.candidate.original_span,
            "start_index": self.candidate.start_index,
            "end_index": self.candidate.end_index,
            "candidate_terms": list(self.candidate.candidate_terms),
            "correction_type": self.candidate.correction_type.value,
            "candidate_confidence": self.candidate.confidence,
            "candidate_reason": self.candidate.reason,
            "part_title": self.part_title,
            "context": [segment.to_dict() for segment in self.context],
        }


@dataclass(frozen=True)
class CorrectionBatch:
    batch_id: str
    source_bundle_hash: str
    source_revision: str
    normalizer_version: str
    glossary_hash: str
    items: tuple[CorrectionBatchItem, ...]

    def messages(self) -> list[dict[str, Any]]:
        payload = {
            "source_bundle_hash": self.source_bundle_hash,
            "source_revision": self.source_revision,
            "normalizer_version": self.normalizer_version,
            "glossary_hash": self.glossary_hash,
            "items": [item.to_dict() for item in self.items],
            "response_schema": {
                "corrections": [
                    {
                        "segment_id": "string",
                        "original_span": "string",
                        "replacement": "one supplied candidate term",
                        "confidence": "0..1",
                        "reason": "short string",
                    }
                ]
            },
        }
        messages = [
            {"role": "system", "content": _PROVIDER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
        ]
        if sum(len(str(message["content"])) for message in messages) > MAX_PROVIDER_PROMPT_CHARS:
            raise ValueError("correction provider prompt exceeds the bounded size")
        return messages


@dataclass(frozen=True)
class NormalizationMetrics:
    total_segments: int
    candidate_segments: int
    candidate_ratio: float
    correction_batches: int
    provider_calls: int
    auto_applied: int
    suggested: int
    rejected: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_segments": self.total_segments,
            "candidate_segments": self.candidate_segments,
            "candidate_ratio": self.candidate_ratio,
            "correction_batches": self.correction_batches,
            "provider_calls": self.provider_calls,
            "auto_applied": self.auto_applied,
            "suggested": self.suggested,
            "rejected": self.rejected,
        }


@dataclass(frozen=True)
class NormalizationPlan:
    original_bundle_hash: str
    original_timeline_hash: str
    glossary_hash: str
    candidates: tuple[CorrectionCandidate, ...]
    deterministic_corrections: tuple[TranscriptCorrection, ...]
    local_suggestions: tuple[TranscriptCorrection, ...]
    batches: tuple[CorrectionBatch, ...]
    metrics: NormalizationMetrics


@dataclass(frozen=True)
class PatchValidationResult:
    accepted: tuple[TranscriptCorrection, ...]
    suggested: tuple[TranscriptCorrection, ...]
    rejections: tuple[PatchRejection, ...]


@dataclass(frozen=True)
class TranscriptNormalizationResult:
    normalizer_version: str
    original_bundle_hash: str
    original_timeline_hash: str
    normalized_hash: str
    candidates: tuple[CorrectionCandidate, ...]
    corrections: tuple[TranscriptCorrection, ...]
    suggestions: tuple[TranscriptCorrection, ...]
    review_required: tuple[CorrectionCandidate, ...]
    rejections: tuple[PatchRejection, ...]
    metrics: NormalizationMetrics
    provider_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalizer_version": self.normalizer_version,
            "original_bundle_hash": self.original_bundle_hash,
            "original_timeline_hash": self.original_timeline_hash,
            "normalized_hash": self.normalized_hash,
            "candidates": [item.to_dict() for item in self.candidates],
            "corrections": [item.identity_dict() for item in self.corrections],
            "suggestions": [item.identity_dict() for item in self.suggestions],
            "review_required": [item.to_dict() for item in self.review_required],
            "rejections": [item.to_dict() for item in self.rejections],
            "metrics": self.metrics.to_dict(),
            "provider_status": self.provider_status,
        }


def _span_pattern(alias: str) -> re.Pattern[str]:
    prefix = r"(?<![A-Za-z0-9_])" if alias[0].isascii() and alias[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9_])" if alias[-1].isascii() and alias[-1].isalnum() else ""
    return re.compile(prefix + re.escape(alias) + suffix, re.IGNORECASE)


def _candidate_id(
    bundle: TranscriptEvidenceBundle,
    segment: TranscriptSegment,
    start: int,
    end: int,
    terms: tuple[str, ...],
) -> str:
    return "correction-candidate-" + _sha256_json(
        {
            "bundle_hash": bundle.bundle_hash,
            "segment_id": segment.segment_id,
            "start": start,
            "end": end,
            "terms": list(terms),
            "normalizer_version": NORMALIZER_VERSION,
        }
    )


def _correction_for_candidate(
    candidate: CorrectionCandidate,
    replacement_text: str,
    *,
    outcome: CorrectionOutcome,
    confidence: float,
    reason: str,
    provider: str | None = None,
    model: str | None = None,
) -> TranscriptCorrection:
    patch_id = "transcript-correction-" + _sha256_json(
        {
            "candidate_id": candidate.candidate_id,
            "replacement": replacement_text,
            "outcome": outcome.value,
            "normalizer_version": NORMALIZER_VERSION,
            "provider": provider,
            "model": model,
        }
    )
    return TranscriptCorrection(
        patch_id=patch_id,
        candidate_id=candidate.candidate_id,
        segment_id=candidate.segment_id,
        original_span=candidate.original_span,
        replacement=replacement_text,
        start_index=candidate.start_index,
        end_index=candidate.end_index,
        correction_type=candidate.correction_type,
        outcome=outcome,
        confidence=float(confidence),
        reason=_clean_text(reason) or candidate.reason,
        context_segment_ids=candidate.context_segment_ids,
        normalizer_version=NORMALIZER_VERSION,
        source_segment_hash=candidate.source_segment_hash,
        provider=_clean_text(provider) or None,
        model=_clean_text(model) or None,
    )


def _context_ids(
    segments: tuple[TranscriptSegment, ...],
    index: int,
    window: int,
) -> tuple[str, ...]:
    current_part = segments[index].part_index
    return tuple(
        segments[cursor].segment_id
        for cursor in range(max(0, index - window), min(len(segments), index + window + 1))
        if segments[cursor].part_index == current_part
    )


def detect_correction_candidates(
    bundle: TranscriptEvidenceBundle,
    glossary: TerminologyGlossary,
    *,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    fuzzy_similarity: float = DEFAULT_FUZZY_SIMILARITY,
) -> tuple[CorrectionCandidate, ...]:
    """Scan once and return only suspicious spans; canonical text is omitted."""

    if not 0 <= context_window <= MAX_CONTEXT_WINDOW:
        raise ValueError(f"context_window must be between 0 and {MAX_CONTEXT_WINDOW}")
    if not 0 < fuzzy_similarity <= 1:
        raise ValueError("fuzzy_similarity must be between 0 and 1")

    alias_terms: dict[str, list[GlossaryTerm]] = {}
    alias_display: dict[str, str] = {}
    for term in glossary.terms:
        for alias in term.aliases:
            key = alias.casefold()
            alias_terms.setdefault(key, []).append(term)
            alias_display.setdefault(key, alias)
    alias_patterns = [
        (alias_display[key], tuple(values), _span_pattern(alias_display[key]))
        for key, values in sorted(alias_terms.items(), key=lambda item: (-len(item[0]), item[0]))
    ]
    fuzzy_forms: dict[str, tuple[str, ...]] = {}
    for term in glossary.terms:
        values = [term.canonical, *term.aliases]
        fuzzy_forms[term.canonical] = tuple(
            value.casefold() for value in values if " " not in value and value.isascii() and len(value) >= 4
        )

    candidates: list[CorrectionCandidate] = []
    segment_indexes: dict[str, int] = {}
    for segment_index, segment in enumerate(bundle.segments):
        segment_indexes[segment.segment_id] = segment_index
        text = segment.original_text
        raw_matches: list[tuple[int, int, str, tuple[GlossaryTerm, ...], float, str]] = []
        for alias, terms, pattern in alias_patterns:
            for match in pattern.finditer(text):
                raw_matches.append((match.start(), match.end(), match.group(0), terms, 1.0, "confirmed_alias"))

        occupied: list[tuple[int, int]] = []
        for start, end, original_span, terms, similarity, reason in sorted(
            raw_matches,
            key=lambda item: (item[0], -(item[1] - item[0]), item[2].casefold()),
        ):
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            canonical_terms = tuple(sorted({term.canonical for term in terms}, key=str.casefold))
            if len(canonical_terms) > 1:
                correction_type = CorrectionType.REVIEW_REQUIRED
                confidence = min(term.confidence for term in terms)
            else:
                confidence = terms[0].confidence
                correction_type = (
                    CorrectionType.SAFE_DETERMINISTIC
                    if confidence >= AUTO_GLOSSARY_CONFIDENCE
                    else CorrectionType.HIGH_CONFIDENCE_CONTEXTUAL
                )
            candidate_id = _candidate_id(bundle, segment, start, end, canonical_terms)
            candidates.append(
                CorrectionCandidate(
                    candidate_id=candidate_id,
                    segment_id=segment.segment_id,
                    original_span=original_span,
                    start_index=start,
                    end_index=end,
                    candidate_terms=canonical_terms,
                    correction_type=correction_type,
                    confidence=round(float(confidence) * similarity, 6),
                    reason=reason if len(canonical_terms) == 1 else "ambiguous_alias",
                    context_segment_ids=(),
                    source_segment_hash=segment.segment_hash,
                )
            )
            occupied.append((start, end))

        for match in _ASCII_TOKEN_RE.finditer(text):
            start, end = match.span()
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            token = match.group(0)
            token_key = token.casefold()
            scores: list[tuple[float, str]] = []
            for canonical, forms in fuzzy_forms.items():
                if token_key == canonical.casefold() or token_key in forms:
                    continue
                score = max((SequenceMatcher(None, token_key, form).ratio() for form in forms), default=0.0)
                if score >= fuzzy_similarity:
                    scores.append((score, canonical))
            if not scores:
                continue
            scores.sort(key=lambda item: (-item[0], item[1].casefold()))
            best = scores[0][0]
            terms = tuple(item[1] for item in scores if best - item[0] <= 0.015)
            correction_type = (
                CorrectionType.REVIEW_REQUIRED if len(terms) > 1 else CorrectionType.HIGH_CONFIDENCE_CONTEXTUAL
            )
            candidate_id = _candidate_id(bundle, segment, start, end, terms)
            candidates.append(
                CorrectionCandidate(
                    candidate_id=candidate_id,
                    segment_id=segment.segment_id,
                    original_span=token,
                    start_index=start,
                    end_index=end,
                    candidate_terms=terms,
                    correction_type=correction_type,
                    confidence=round(best, 6),
                    reason="fuzzy_term_variant" if len(terms) == 1 else "ambiguous_fuzzy_variant",
                    context_segment_ids=(),
                    source_segment_hash=segment.segment_hash,
                )
            )
            occupied.append((start, end))

    candidates.sort(
        key=lambda item: (
            segment_indexes[item.segment_id],
            item.start_index,
            item.end_index,
            item.candidate_id,
        )
    )
    return tuple(
        replace(
            candidate,
            context_segment_ids=_context_ids(
                bundle.segments,
                segment_indexes[candidate.segment_id],
                context_window,
            ),
        )
        for candidate in candidates
    )


def _batch_item(
    bundle: TranscriptEvidenceBundle,
    candidate: CorrectionCandidate,
    segment_by_id: Mapping[str, TranscriptSegment],
    part_titles: Mapping[int, str],
) -> CorrectionBatchItem:
    contexts: list[CorrectionContextSegment] = []
    for segment_id in candidate.context_segment_ids:
        segment = segment_by_id.get(segment_id)
        if segment is None:
            continue
        original_text = segment.original_text
        truncated = len(original_text) > MAX_CONTEXT_CHARS
        if truncated and segment.segment_id == candidate.segment_id:
            midpoint = (candidate.start_index + candidate.end_index) // 2
            start = max(0, min(len(original_text) - MAX_CONTEXT_CHARS, midpoint - MAX_CONTEXT_CHARS // 2))
            context_text = original_text[start : start + MAX_CONTEXT_CHARS]
        else:
            context_text = original_text[:MAX_CONTEXT_CHARS]
        contexts.append(
            CorrectionContextSegment(
                segment_id=segment.segment_id,
                part_index=segment.part_index,
                part_cid=segment.part_cid,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                original_text=context_text,
                text_truncated=truncated,
            )
        )
    segment = segment_by_id[candidate.segment_id]
    return CorrectionBatchItem(
        candidate=candidate,
        part_title=part_titles.get(segment.part_index, ""),
        context=tuple(contexts),
    )


def build_normalization_plan(
    bundle: TranscriptEvidenceBundle,
    glossary: TerminologyGlossary,
    *,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    batch_size: int = DEFAULT_CORRECTION_BATCH_SIZE,
    fuzzy_similarity: float = DEFAULT_FUZZY_SIMILARITY,
) -> NormalizationPlan:
    if not 1 <= batch_size <= MAX_CORRECTION_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_CORRECTION_BATCH_SIZE}")
    candidates = detect_correction_candidates(
        bundle,
        glossary,
        context_window=context_window,
        fuzzy_similarity=fuzzy_similarity,
    )
    deterministic: list[TranscriptCorrection] = []
    local_suggestions: list[TranscriptCorrection] = []
    contextual: list[CorrectionCandidate] = []
    for candidate in candidates:
        if candidate.correction_type is CorrectionType.SAFE_DETERMINISTIC:
            deterministic.append(
                _correction_for_candidate(
                    candidate,
                    candidate.candidate_terms[0],
                    outcome=CorrectionOutcome.AUTO_APPLIED,
                    confidence=candidate.confidence,
                    reason="confirmed glossary alias",
                )
            )
        else:
            contextual.append(candidate)
            if len(candidate.candidate_terms) == 1:
                local_suggestions.append(
                    _correction_for_candidate(
                        candidate,
                        candidate.candidate_terms[0],
                        outcome=CorrectionOutcome.SUGGESTED,
                        confidence=candidate.confidence,
                        reason="contextual confirmation required",
                    )
                )

    segment_by_id = {segment.segment_id: segment for segment in bundle.segments}
    part_titles = {part.part_index: part.title for part in bundle.parts}
    batches: list[CorrectionBatch] = []
    for start in range(0, len(contextual), batch_size):
        chunk = contextual[start : start + batch_size]
        items = tuple(_batch_item(bundle, candidate, segment_by_id, part_titles) for candidate in chunk)
        batch_id = "correction-batch-" + _sha256_json(
            {
                "bundle_hash": bundle.bundle_hash,
                "normalizer_version": NORMALIZER_VERSION,
                "glossary_hash": glossary.glossary_hash,
                "items": [item.to_dict() for item in items],
            }
        )
        batches.append(
            CorrectionBatch(
                batch_id=batch_id,
                source_bundle_hash=bundle.bundle_hash,
                source_revision=bundle.source_revision,
                normalizer_version=NORMALIZER_VERSION,
                glossary_hash=glossary.glossary_hash,
                items=items,
            )
        )

    candidate_segments = len({candidate.segment_id for candidate in candidates})
    total_segments = len(bundle.segments)
    metrics = NormalizationMetrics(
        total_segments=total_segments,
        candidate_segments=candidate_segments,
        candidate_ratio=(candidate_segments / total_segments if total_segments else 0.0),
        correction_batches=len(batches),
        provider_calls=0,
        auto_applied=len(deterministic),
        suggested=len(contextual),
        rejected=0,
    )
    return NormalizationPlan(
        original_bundle_hash=bundle.bundle_hash,
        original_timeline_hash=bundle.normalized_timeline_hash,
        glossary_hash=glossary.glossary_hash,
        candidates=candidates,
        deterministic_corrections=tuple(deterministic),
        local_suggestions=tuple(local_suggestions),
        batches=tuple(batches),
        metrics=metrics,
    )


def _rejection(
    code: str,
    detail: str,
    *,
    correction: TranscriptCorrection | None = None,
    batch_id: str | None = None,
    segment_id: str | None = None,
    original_span: str | None = None,
) -> PatchRejection:
    return PatchRejection(
        code=code,
        detail=detail,
        segment_id=segment_id or (correction.segment_id if correction else None),
        original_span=original_span or (correction.original_span if correction else None),
        batch_id=batch_id,
        patch_id=correction.patch_id if correction else None,
    )


def _is_local_term_patch(original_span: str, replacement_text: str) -> bool:
    if not original_span.strip() or not replacement_text.strip():
        return False
    if len(original_span) > MAX_TERM_SPAN_LENGTH or len(replacement_text) > MAX_TERM_SPAN_LENGTH:
        return False
    if _SENTENCE_DELIMITER_RE.search(original_span) or _SENTENCE_DELIMITER_RE.search(replacement_text):
        return False
    return not (_SEMANTIC_CLAUSE_RE.search(original_span) or _SEMANTIC_CLAUSE_RE.search(replacement_text))


def _candidate_authorizes_correction(
    candidate: CorrectionCandidate,
    correction: TranscriptCorrection,
) -> bool:
    if (
        candidate.segment_id != correction.segment_id
        or candidate.original_span != correction.original_span
        or candidate.start_index != correction.start_index
        or candidate.end_index != correction.end_index
        or candidate.source_segment_hash != correction.source_segment_hash
        or candidate.correction_type is not correction.correction_type
        or candidate.context_segment_ids != correction.context_segment_ids
        or correction.replacement not in candidate.candidate_terms
        or not isinstance(correction.confidence, (int, float))
        or isinstance(correction.confidence, bool)
        or not math.isfinite(float(correction.confidence))
        or not 0 <= float(correction.confidence) <= 1
    ):
        return False
    if candidate.correction_type is CorrectionType.SAFE_DETERMINISTIC:
        return correction.outcome is CorrectionOutcome.AUTO_APPLIED
    if candidate.correction_type is CorrectionType.HIGH_CONFIDENCE_CONTEXTUAL:
        return correction.outcome is CorrectionOutcome.SUGGESTED or (
            correction.outcome is CorrectionOutcome.AUTO_APPLIED
            and correction.confidence >= AUTO_CONTEXTUAL_CONFIDENCE
        )
    return correction.outcome is CorrectionOutcome.SUGGESTED


def validate_correction_patches(
    bundle: TranscriptEvidenceBundle,
    corrections: Iterable[TranscriptCorrection],
    *,
    candidates: Iterable[CorrectionCandidate],
) -> PatchValidationResult:
    segment_by_id = {segment.segment_id: segment for segment in bundle.segments}
    segment_order = {segment.segment_id: index for index, segment in enumerate(bundle.segments)}
    candidates_by_id: dict[str, list[CorrectionCandidate]] = {}
    for candidate in candidates:
        candidates_by_id.setdefault(candidate.candidate_id, []).append(candidate)
    valid: list[TranscriptCorrection] = []
    rejections: list[PatchRejection] = []
    for correction in corrections:
        if not isinstance(correction, TranscriptCorrection):
            rejections.append(_rejection("INVALID_PATCH", "patch contract is invalid"))
            continue
        if (
            not isinstance(correction.patch_id, str)
            or not correction.patch_id.strip()
            or not isinstance(correction.candidate_id, str)
            or not correction.candidate_id.strip()
        ):
            rejections.append(_rejection("INVALID_PATCH_ID", "patch and candidate IDs are required", correction=correction))
            continue
        if not isinstance(correction.correction_type, CorrectionType) or correction.outcome not in {
            CorrectionOutcome.AUTO_APPLIED,
            CorrectionOutcome.SUGGESTED,
        }:
            rejections.append(_rejection("INVALID_PATCH", "patch enum values are invalid", correction=correction))
            continue
        if not isinstance(correction.confidence, (int, float)) or isinstance(correction.confidence, bool):
            rejections.append(_rejection("INVALID_CONFIDENCE", "patch confidence must be numeric", correction=correction))
            continue
        if not math.isfinite(float(correction.confidence)) or not 0 <= float(correction.confidence) <= 1:
            rejections.append(_rejection("INVALID_CONFIDENCE", "patch confidence must be between 0 and 1", correction=correction))
            continue
        segment = segment_by_id.get(correction.segment_id)
        if segment is None:
            rejections.append(_rejection("UNKNOWN_SEGMENT_ID", "patch segment does not exist", correction=correction))
            continue
        matching_candidates = candidates_by_id.get(correction.candidate_id, [])
        if len(matching_candidates) != 1:
            rejections.append(_rejection("UNKNOWN_CANDIDATE", "patch candidate is not authoritative", correction=correction))
            continue
        candidate = matching_candidates[0]
        if not _candidate_authorizes_correction(candidate, correction):
            rejections.append(_rejection("CANDIDATE_MISMATCH", "patch does not match its detected candidate", correction=correction))
            continue
        if correction.normalizer_version != NORMALIZER_VERSION:
            rejections.append(_rejection("NORMALIZER_VERSION_MISMATCH", "patch version is stale", correction=correction))
            continue
        if correction.source_segment_hash != segment.segment_hash:
            rejections.append(_rejection("PATCH_STALE", "source segment hash changed", correction=correction))
            continue
        if not correction.replacement.strip():
            rejections.append(_rejection("EMPTY_REPLACEMENT", "replacement is empty", correction=correction))
            continue
        if correction.replacement == correction.original_span:
            rejections.append(_rejection("UNCHANGED_PATCH", "replacement is identical", correction=correction))
            continue
        if not _is_local_term_patch(correction.original_span, correction.replacement):
            rejections.append(_rejection("SEMANTIC_CONFLICT", "patch is not a local terminology edit", correction=correction))
            continue
        if not 0 <= correction.start_index < correction.end_index <= len(segment.original_text):
            rejections.append(_rejection("PATCH_STALE", "patch range is outside the source", correction=correction))
            continue
        if segment.original_text[correction.start_index : correction.end_index] != correction.original_span:
            rejections.append(_rejection("PATCH_STALE", "original span no longer matches exactly", correction=correction))
            continue
        valid.append(correction)

    conflict_indexes: set[int] = set()
    by_patch_id: dict[str, list[int]] = {}
    by_segment: dict[str, list[int]] = {}
    for index, correction in enumerate(valid):
        by_patch_id.setdefault(correction.patch_id, []).append(index)
        by_segment.setdefault(correction.segment_id, []).append(index)
    for indexes in by_patch_id.values():
        if len(indexes) > 1:
            conflict_indexes.update(indexes)
    for indexes in by_segment.values():
        for offset, left_index in enumerate(indexes):
            left = valid[left_index]
            for right_index in indexes[offset + 1 :]:
                right = valid[right_index]
                if left.start_index < right.end_index and left.end_index > right.start_index:
                    conflict_indexes.update((left_index, right_index))

    accepted: list[TranscriptCorrection] = []
    suggested: list[TranscriptCorrection] = []
    for index, correction in enumerate(valid):
        if index in conflict_indexes:
            rejections.append(_rejection("PATCH_CONFLICT", "duplicate or overlapping patch", correction=correction))
        elif correction.outcome is CorrectionOutcome.AUTO_APPLIED:
            accepted.append(correction)
        elif correction.outcome is CorrectionOutcome.SUGGESTED:
            suggested.append(correction)

    ordering = lambda item: (
        segment_order.get(item.segment_id, len(segment_order)),
        item.start_index,
        item.end_index,
        item.patch_id,
    )
    accepted.sort(key=ordering)
    suggested.sort(key=ordering)
    rejections.sort(key=lambda item: (item.segment_id or "", item.original_span or "", item.code, item.patch_id or ""))
    return PatchValidationResult(tuple(accepted), tuple(suggested), tuple(rejections))


def materialize_normalized_text(
    segment: TranscriptSegment,
    patches: Iterable[TranscriptCorrection],
    *,
    candidates: Iterable[CorrectionCandidate],
) -> str:
    """Materialize one derived view; never write to ``segment.original_text``."""

    candidate_by_id: dict[str, list[CorrectionCandidate]] = {}
    for candidate in candidates:
        candidate_by_id.setdefault(candidate.candidate_id, []).append(candidate)
    relevant = [
        patch
        for patch in patches
        if patch.segment_id == segment.segment_id
        and len(candidate_by_id.get(patch.candidate_id, ())) == 1
        and _candidate_authorizes_correction(candidate_by_id[patch.candidate_id][0], patch)
        and patch.outcome is CorrectionOutcome.AUTO_APPLIED
        and patch.normalizer_version == NORMALIZER_VERSION
        and patch.source_segment_hash == segment.segment_hash
        and 0 <= patch.start_index < patch.end_index <= len(segment.original_text)
        and segment.original_text[patch.start_index : patch.end_index] == patch.original_span
        and patch.replacement.strip()
        and _is_local_term_patch(patch.original_span, patch.replacement)
    ]
    conflicting: set[int] = set()
    for left_index, left in enumerate(relevant):
        for right_index in range(left_index + 1, len(relevant)):
            right = relevant[right_index]
            if (
                left.patch_id == right.patch_id
                or (left.start_index < right.end_index and left.end_index > right.start_index)
            ):
                conflicting.update((left_index, right_index))
    safe = [patch for index, patch in enumerate(relevant) if index not in conflicting]
    value = segment.original_text
    for patch in sorted(safe, key=lambda item: (item.start_index, item.end_index), reverse=True):
        value = value[: patch.start_index] + patch.replacement + value[patch.end_index :]
    return value


def normalized_transcript_hash(
    bundle: TranscriptEvidenceBundle,
    patches: Iterable[TranscriptCorrection],
    *,
    candidates: Iterable[CorrectionCandidate],
) -> str:
    validation = validate_correction_patches(bundle, patches, candidates=candidates)
    return _sha256_json(
        {
            "original_bundle_hash": bundle.bundle_hash,
            "original_timeline_hash": bundle.normalized_timeline_hash,
            "normalizer_version": NORMALIZER_VERSION,
            "patches": [
                {
                    "segment_id": patch.segment_id,
                    "original_span": patch.original_span,
                    "replacement": patch.replacement,
                    "start_index": patch.start_index,
                    "end_index": patch.end_index,
                    "source_segment_hash": patch.source_segment_hash,
                }
                for patch in validation.accepted
            ],
        }
    )


def _provider_response(
    response: str | Mapping[str, Any] | None,
    batch: CorrectionBatch,
    bundle: TranscriptEvidenceBundle,
    *,
    provider_name: str | None,
    model_name: str | None,
) -> tuple[list[TranscriptCorrection], list[PatchRejection]]:
    if response is None or (isinstance(response, str) and not response.strip()):
        return [], [_rejection("EMPTY_PROVIDER_RESPONSE", "provider response is empty", batch_id=batch.batch_id)]
    if isinstance(response, str) and len(response) > MAX_PROVIDER_RESPONSE_CHARS:
        return [], [_rejection("PROVIDER_RESPONSE_TOO_LARGE", "provider response exceeds the size limit", batch_id=batch.batch_id)]
    try:
        if isinstance(response, str):
            payload = json.loads(
                response,
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
        elif isinstance(response, Mapping):
            if len(_canonical_json_bytes(response)) > MAX_PROVIDER_RESPONSE_CHARS:
                return [], [
                    _rejection(
                        "PROVIDER_RESPONSE_TOO_LARGE",
                        "provider response exceeds the size limit",
                        batch_id=batch.batch_id,
                    )
                ]
            payload = dict(response)
        else:
            payload = None
    except (TypeError, ValueError):
        return [], [_rejection("INVALID_PROVIDER_JSON", "provider response is not valid JSON", batch_id=batch.batch_id)]
    if not isinstance(payload, Mapping) or set(payload) != {"corrections"}:
        return [], [_rejection("INVALID_PROVIDER_SCHEMA", "provider response schema is invalid", batch_id=batch.batch_id)]
    raw_corrections = payload.get("corrections")
    if not isinstance(raw_corrections, list):
        return [], [_rejection("INVALID_PROVIDER_SCHEMA", "corrections must be a list", batch_id=batch.batch_id)]
    if len(raw_corrections) > MAX_PROVIDER_CORRECTIONS:
        return [], [_rejection("TOO_MANY_PROVIDER_CORRECTIONS", "provider returned too many corrections", batch_id=batch.batch_id)]

    expected_keys = {"segment_id", "original_span", "replacement", "confidence", "reason"}
    segment_by_id = {segment.segment_id: segment for segment in bundle.segments}
    batch_candidates: dict[tuple[str, str], list[CorrectionCandidate]] = {}
    for item in batch.items:
        candidate = item.candidate
        batch_candidates.setdefault((candidate.segment_id, candidate.original_span), []).append(candidate)

    corrections: list[TranscriptCorrection] = []
    rejections: list[PatchRejection] = []
    for raw in raw_corrections:
        if not isinstance(raw, Mapping) or set(raw) != expected_keys:
            rejections.append(_rejection("INVALID_PROVIDER_ITEM", "provider correction fields are invalid", batch_id=batch.batch_id))
            continue
        if (
            not isinstance(raw.get("segment_id"), str)
            or not isinstance(raw.get("original_span"), str)
            or not isinstance(raw.get("replacement"), str)
            or not isinstance(raw.get("reason"), str)
            or not isinstance(raw.get("confidence"), (int, float))
            or isinstance(raw.get("confidence"), bool)
        ):
            rejections.append(_rejection("INVALID_PROVIDER_ITEM", "provider correction types are invalid", batch_id=batch.batch_id))
            continue
        segment_id = raw["segment_id"].strip()
        original_span = raw["original_span"]
        replacement_text = raw["replacement"]
        reason = raw["reason"].strip()
        confidence = float(raw["confidence"])
        if (
            len(segment_id) > 200
            or len(original_span) > MAX_TERM_SPAN_LENGTH
            or len(replacement_text) > MAX_TERM_SPAN_LENGTH
            or len(reason) > MAX_PROVIDER_REASON_CHARS
        ):
            rejections.append(
                _rejection(
                    "INVALID_PROVIDER_ITEM",
                    "provider correction fields exceed their size limits",
                    batch_id=batch.batch_id,
                    segment_id=segment_id,
                    original_span=original_span,
                )
            )
            continue
        segment = segment_by_id.get(segment_id)
        if segment is None:
            rejections.append(
                _rejection(
                    "UNKNOWN_SEGMENT_ID",
                    "provider referenced an unknown segment",
                    batch_id=batch.batch_id,
                    segment_id=segment_id,
                    original_span=original_span,
                )
            )
            continue
        if not replacement_text.strip():
            rejections.append(
                _rejection(
                    "EMPTY_REPLACEMENT",
                    "provider replacement is empty",
                    batch_id=batch.batch_id,
                    segment_id=segment_id,
                    original_span=original_span,
                )
            )
            continue
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            rejections.append(
                _rejection(
                    "INVALID_CONFIDENCE",
                    "provider confidence must be between 0 and 1",
                    batch_id=batch.batch_id,
                    segment_id=segment_id,
                    original_span=original_span,
                )
            )
            continue
        matching = batch_candidates.get((segment_id, original_span), [])
        if len(matching) != 1:
            if original_span not in segment.original_text:
                code = "PATCH_STALE"
            elif len(matching) > 1:
                code = "AMBIGUOUS_SPAN"
            else:
                code = "SEMANTIC_CONFLICT"
            rejections.append(
                _rejection(
                    code,
                    "provider attempted to edit a non-candidate span",
                    batch_id=batch.batch_id,
                    segment_id=segment_id,
                    original_span=original_span,
                )
            )
            continue
        candidate = matching[0]
        if replacement_text not in candidate.candidate_terms or not _is_local_term_patch(original_span, replacement_text):
            rejections.append(
                _rejection(
                    "SEMANTIC_CONFLICT",
                    "provider replacement is outside the supplied terminology candidates",
                    batch_id=batch.batch_id,
                    segment_id=segment_id,
                    original_span=original_span,
                )
            )
            continue
        outcome = (
            CorrectionOutcome.AUTO_APPLIED
            if candidate.correction_type is CorrectionType.HIGH_CONFIDENCE_CONTEXTUAL
            and len(candidate.candidate_terms) == 1
            and confidence >= AUTO_CONTEXTUAL_CONFIDENCE
            else CorrectionOutcome.SUGGESTED
        )
        corrections.append(
            _correction_for_candidate(
                candidate,
                replacement_text,
                outcome=outcome,
                confidence=confidence,
                reason=reason or "provider contextual correction",
                provider=provider_name,
                model=model_name,
            )
        )
    return corrections, rejections


async def normalize_transcript(
    bundle: TranscriptEvidenceBundle,
    glossary: TerminologyGlossary,
    *,
    provider: TextCorrectionProvider | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    batch_size: int = DEFAULT_CORRECTION_BATCH_SIZE,
    fuzzy_similarity: float = DEFAULT_FUZZY_SIMILARITY,
) -> TranscriptNormalizationResult:
    """Build sparse derived corrections; a missing/bad provider never harms evidence."""

    plan = build_normalization_plan(
        bundle,
        glossary,
        context_window=context_window,
        batch_size=batch_size,
        fuzzy_similarity=fuzzy_similarity,
    )
    proposed = list(plan.deterministic_corrections)
    provider_rejections: list[PatchRejection] = []
    provider_calls = 0
    provider_candidate_ids: set[str] = set()
    if provider is not None:
        provider_status = "INJECTED_PROVIDER_USED"
        resolved_provider_name = provider_name or _clean_text(getattr(provider, "provider_name", None)) or None
        resolved_model_name = model_name or _clean_text(getattr(provider, "model_name", None)) or None
        for batch in plan.batches:
            try:
                messages = batch.messages()
            except ValueError:
                provider_rejections.append(
                    _rejection("PROVIDER_INPUT_TOO_LARGE", "correction provider input exceeds its bound", batch_id=batch.batch_id)
                )
                continue
            provider_calls += 1
            try:
                raw_response = await provider.text(messages, 0.0)
            except Exception:
                provider_rejections.append(
                    _rejection("PROVIDER_CALL_FAILED", "correction provider call failed", batch_id=batch.batch_id)
                )
                continue
            corrections, rejections = _provider_response(
                raw_response,
                batch,
                bundle,
                provider_name=resolved_provider_name,
                model_name=resolved_model_name,
            )
            proposed.extend(corrections)
            provider_rejections.extend(rejections)
            provider_candidate_ids.update(correction.candidate_id for correction in corrections)
    elif plan.batches:
        provider_status = "PROVIDER_INTEGRATION_REQUIRED"
    else:
        provider_status = "NOT_REQUIRED"

    proposed.extend(
        suggestion
        for suggestion in plan.local_suggestions
        if suggestion.candidate_id not in provider_candidate_ids
    )
    validation = validate_correction_patches(bundle, proposed, candidates=plan.candidates)
    all_rejections = tuple(
        sorted(
            (*provider_rejections, *validation.rejections),
            key=lambda item: (item.batch_id or "", item.segment_id or "", item.code, item.patch_id or ""),
        )
    )
    normalized_hash = normalized_transcript_hash(bundle, validation.accepted, candidates=plan.candidates)
    review_required = tuple(
        candidate for candidate in plan.candidates if candidate.correction_type is CorrectionType.REVIEW_REQUIRED
    )
    suggested_candidate_ids = {
        *(suggestion.candidate_id for suggestion in validation.suggested),
        *(candidate.candidate_id for candidate in review_required),
    }
    metrics = NormalizationMetrics(
        total_segments=plan.metrics.total_segments,
        candidate_segments=plan.metrics.candidate_segments,
        candidate_ratio=plan.metrics.candidate_ratio,
        correction_batches=plan.metrics.correction_batches,
        provider_calls=provider_calls,
        auto_applied=len(validation.accepted),
        suggested=len(suggested_candidate_ids),
        rejected=len(all_rejections),
    )
    return TranscriptNormalizationResult(
        normalizer_version=NORMALIZER_VERSION,
        original_bundle_hash=bundle.bundle_hash,
        original_timeline_hash=bundle.normalized_timeline_hash,
        normalized_hash=normalized_hash,
        candidates=plan.candidates,
        corrections=validation.accepted,
        suggestions=validation.suggested,
        review_required=review_required,
        rejections=all_rejections,
        metrics=metrics,
        provider_status=provider_status,
    )


__all__ = [
    "AUTO_CONTEXTUAL_CONFIDENCE",
    "AUTO_GLOSSARY_CONFIDENCE",
    "CorrectionBatch",
    "CorrectionBatchItem",
    "CorrectionCandidate",
    "CorrectionContextSegment",
    "CorrectionOutcome",
    "CorrectionType",
    "GlossaryTerm",
    "NormalizationMetrics",
    "NormalizationPlan",
    "PatchRejection",
    "PatchValidationResult",
    "TerminologyGlossary",
    "TextCorrectionProvider",
    "TranscriptCorrection",
    "TranscriptNormalizationResult",
    "build_normalization_plan",
    "detect_correction_candidates",
    "materialize_normalized_text",
    "normalize_transcript",
    "normalized_transcript_hash",
    "validate_correction_patches",
]
