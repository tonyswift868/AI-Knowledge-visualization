from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from ...sources.video.transcript_evidence import TranscriptEvidenceBundle
from ...sources.video.transcript_normalizer import (
    TranscriptNormalizationResult,
    materialize_normalized_text,
)
from .models import VerificationEvidence


def build_transcript_verification_evidence(
    bundle: TranscriptEvidenceBundle,
    normalization: TranscriptNormalizationResult | None = None,
) -> tuple[VerificationEvidence, ...]:
    """Build an immutable derived view while preserving original evidence identity."""

    if normalization is not None:
        if normalization.original_bundle_hash != bundle.bundle_hash:
            raise ValueError("normalization bundle hash does not match transcript evidence")
        if normalization.original_timeline_hash != bundle.normalized_timeline_hash:
            raise ValueError("normalization timeline hash does not match transcript evidence")
    corrections_by_segment: dict[str, list[object]] = defaultdict(list)
    candidates_by_segment: dict[str, list[object]] = defaultdict(list)
    if normalization is not None:
        for correction in normalization.corrections:
            corrections_by_segment[correction.segment_id].append(correction)
        for candidate in normalization.candidates:
            candidates_by_segment[candidate.segment_id].append(candidate)

    result = []
    for segment in bundle.segments:
        normalized_text = (
            materialize_normalized_text(
                segment,
                corrections_by_segment[segment.segment_id],
                candidates=candidates_by_segment[segment.segment_id],
            )
            if normalization is not None
            else segment.original_text
        )
        result.append(
            VerificationEvidence(
                evidence_id=segment.segment_id,
                source_identity=bundle.source_identity,
                source_revision=bundle.source_revision,
                source_kind="TRANSCRIPT",
                part_key=f"part:{segment.part_index}",
                part_index=segment.part_index,
                part_cid=segment.part_cid,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                original_text=segment.original_text,
                normalized_text=normalized_text,
                evidence_hash=segment.segment_hash,
            )
        )
    return tuple(result)


def validate_generic_evidence(
    evidence: Iterable[VerificationEvidence],
    *,
    source_identity: str,
    source_revision: str,
) -> tuple[VerificationEvidence, ...]:
    result = tuple(evidence)
    ids = [item.evidence_id for item in result]
    if len(ids) != len(set(ids)):
        raise ValueError("verification evidence contains duplicate IDs")
    if any(item.source_identity != source_identity for item in result):
        raise ValueError("verification evidence source identity mismatch")
    if any(item.source_revision != source_revision for item in result):
        raise ValueError("verification evidence source revision mismatch")
    return result
