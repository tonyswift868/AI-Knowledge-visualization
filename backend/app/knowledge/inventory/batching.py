from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ...sources.video.transcript_evidence import (
    TranscriptEvidenceBundle,
    TranscriptSegment,
)
from ...sources.video.transcript_normalizer import (
    TranscriptNormalizationResult,
    materialize_normalized_text,
)
from .models import EXTRACTOR_VERSION, PartChapterContext, SourceMetadata, stable_hash


DEFAULT_MAX_BATCH_SEGMENTS = 64
DEFAULT_MAX_BATCH_CHARACTERS = 24_000
DEFAULT_OVERLAP_SEGMENTS = 1


@dataclass(frozen=True)
class BatchingConfig:
    max_segments: int = DEFAULT_MAX_BATCH_SEGMENTS
    max_characters: int = DEFAULT_MAX_BATCH_CHARACTERS
    overlap_segments: int = DEFAULT_OVERLAP_SEGMENTS

    def __post_init__(self) -> None:
        if not 1 <= self.max_segments <= 512:
            raise ValueError("max_segments must be between 1 and 512")
        if not 1_000 <= self.max_characters <= 256_000:
            raise ValueError("max_characters must be between 1000 and 256000")
        if not 0 <= self.overlap_segments <= 8:
            raise ValueError("overlap_segments must be between 0 and 8")

    def identity_dict(self) -> dict[str, int]:
        return {
            "max_segments": self.max_segments,
            "max_characters": self.max_characters,
            "overlap_segments": self.overlap_segments,
        }


@dataclass(frozen=True)
class BatchEvidenceView:
    segment: TranscriptSegment
    normalized_text: str
    is_core: bool

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment.segment_id,
            "ordinal": self.segment.ordinal,
            "part_index": self.segment.part_index,
            "part_cid": self.segment.part_cid,
            "start_ms": self.segment.start_ms,
            "end_ms": self.segment.end_ms,
            "original_text": self.segment.original_text,
            "normalized_text": self.normalized_text,
            "original_segment_hash": self.segment.segment_hash,
            "is_core": self.is_core,
        }


@dataclass(frozen=True)
class ExtractionBatch:
    batch_id: str
    part_index: int
    core_segment_ids: tuple[str, ...]
    evidence: tuple[BatchEvidenceView, ...]
    source_metadata: SourceMetadata
    part_context: PartChapterContext
    original_bundle_hash: str
    normalized_hash: str

    @property
    def allowed_evidence_ids(self) -> frozenset[str]:
        return frozenset(item.segment.segment_id for item in self.evidence)

    def dynamic_payload(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "source": self.source_metadata.to_dict(),
            "part_context": self.part_context.to_dict(),
            "original_bundle_hash": self.original_bundle_hash,
            "normalized_hash": self.normalized_hash,
            "core_segment_ids": list(self.core_segment_ids),
            "evidence": [item.to_prompt_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class ExtractionPlan:
    batches: tuple[ExtractionBatch, ...]
    total_segment_count: int
    normalized_hash: str
    batching_config: BatchingConfig


def _validate_inputs(
    bundle: TranscriptEvidenceBundle,
    normalization: TranscriptNormalizationResult | None,
    source_metadata: SourceMetadata,
) -> None:
    if source_metadata.source_identity != bundle.source_identity:
        raise ValueError("source metadata identity does not match evidence bundle")
    if source_metadata.source_revision != bundle.source_revision:
        raise ValueError("source metadata revision does not match evidence bundle")
    segment_ids = [segment.segment_id for segment in bundle.segments]
    if len(segment_ids) != len(set(segment_ids)):
        raise ValueError("evidence bundle contains duplicate segment IDs")
    if normalization is not None:
        if normalization.original_bundle_hash != bundle.bundle_hash:
            raise ValueError("normalization bundle hash does not match evidence bundle")
        if normalization.original_timeline_hash != bundle.normalized_timeline_hash:
            raise ValueError("normalization timeline hash does not match evidence bundle")


def _part_context_map(
    bundle: TranscriptEvidenceBundle,
    part_contexts: Iterable[PartChapterContext],
) -> dict[int, PartChapterContext]:
    result = {item.part_index: item for item in part_contexts}
    for part in bundle.parts:
        result.setdefault(
            part.part_index,
            PartChapterContext(part_index=part.part_index, part_title=part.title),
        )
    return result


def _normalized_texts(
    bundle: TranscriptEvidenceBundle,
    normalization: TranscriptNormalizationResult | None,
) -> dict[str, str]:
    if normalization is None:
        return {segment.segment_id: segment.original_text for segment in bundle.segments}
    corrections_by_segment: dict[str, list[Any]] = {}
    candidates_by_segment: dict[str, list[Any]] = {}
    for correction in normalization.corrections:
        corrections_by_segment.setdefault(correction.segment_id, []).append(correction)
    for candidate in normalization.candidates:
        candidates_by_segment.setdefault(candidate.segment_id, []).append(candidate)
    return {
        segment.segment_id: materialize_normalized_text(
            segment,
            corrections_by_segment.get(segment.segment_id, ()),
            candidates=candidates_by_segment.get(segment.segment_id, ()),
        )
        for segment in bundle.segments
    }


def _partition_part(
    segments: list[TranscriptSegment],
    normalized: dict[str, str],
    config: BatchingConfig,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    characters = 0
    for index, segment in enumerate(segments):
        segment_characters = len(segment.original_text) + len(normalized[segment.segment_id])
        if index > start and (
            index - start >= config.max_segments
            or characters + segment_characters > config.max_characters
        ):
            ranges.append((start, index))
            start = index
            characters = 0
        characters += segment_characters
    if start < len(segments):
        ranges.append((start, len(segments)))
    return ranges


def build_extraction_plan(
    bundle: TranscriptEvidenceBundle,
    *,
    source_metadata: SourceMetadata,
    normalization: TranscriptNormalizationResult | None = None,
    part_contexts: Iterable[PartChapterContext] = (),
    config: BatchingConfig = BatchingConfig(),
) -> ExtractionPlan:
    """Build deterministic, part-local batches with bounded continuity overlap."""

    _validate_inputs(bundle, normalization, source_metadata)
    normalized = _normalized_texts(bundle, normalization)
    normalized_hash = (
        normalization.normalized_hash
        if normalization is not None
        else bundle.normalized_timeline_hash
    )
    contexts = _part_context_map(bundle, part_contexts)
    segments_by_part: dict[int, list[TranscriptSegment]] = {}
    for segment in bundle.segments:
        segments_by_part.setdefault(segment.part_index, []).append(segment)

    batches: list[ExtractionBatch] = []
    for part_index in sorted(segments_by_part):
        part_segments = segments_by_part[part_index]
        if part_index not in contexts:
            raise ValueError(f"part context missing for part {part_index}")
        for start, end in _partition_part(part_segments, normalized, config):
            context_start = max(0, start - config.overlap_segments)
            context_end = min(len(part_segments), end + config.overlap_segments)
            core_ids = tuple(segment.segment_id for segment in part_segments[start:end])
            evidence = tuple(
                BatchEvidenceView(
                    segment=segment,
                    normalized_text=normalized[segment.segment_id],
                    is_core=start <= index < end,
                )
                for index, segment in enumerate(
                    part_segments[context_start:context_end],
                    start=context_start,
                )
            )
            batch_id = stable_hash(
                "kib",
                {
                    "batching": config.identity_dict(),
                    "core_segment_ids": core_ids,
                    "evidence_segment_ids": [item.segment.segment_id for item in evidence],
                    "extractor_version": EXTRACTOR_VERSION,
                    "normalized_hash": normalized_hash,
                    "part_index": part_index,
                    "source_revision": bundle.source_revision,
                },
            )
            batches.append(
                ExtractionBatch(
                    batch_id=batch_id,
                    part_index=part_index,
                    core_segment_ids=core_ids,
                    evidence=evidence,
                    source_metadata=source_metadata,
                    part_context=contexts[part_index],
                    original_bundle_hash=bundle.bundle_hash,
                    normalized_hash=normalized_hash,
                )
            )

    return ExtractionPlan(
        batches=tuple(batches),
        total_segment_count=len(bundle.segments),
        normalized_hash=normalized_hash,
        batching_config=config,
    )
