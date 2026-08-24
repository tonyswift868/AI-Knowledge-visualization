from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from pydantic import BaseModel

from ...sources.video.transcript_evidence import (
    TranscriptEvidenceBundle,
    TranscriptSegment,
)
from ...sources.video.transcript_normalizer import TranscriptNormalizationResult
from .batching import (
    BatchingConfig,
    ExtractionBatch,
    build_extraction_plan,
)
from .models import (
    EXTRACTOR_VERSION,
    TEACHING_DEPTH_RANK,
    CandidateRejection,
    CoverageLedger,
    EvidenceReference,
    FailedBatch,
    InventoryStatus,
    KnowledgeInventory,
    KnowledgeInventoryItem,
    PartChapterContext,
    SourceMetadata,
    TeachingDepth,
    TimeRange,
    canonical_semantic_key,
    stable_item_id,
)
from .prompt import (
    ProviderBatchResponse,
    ProviderKnowledgeCandidate,
    build_inventory_prompt_envelope,
)


class StructuredKnowledgeProvider(Protocol):
    async def structured(
        self,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
        temperature: float = 0.2,
    ) -> BaseModel: ...


@dataclass(frozen=True)
class ExtractionConfig:
    batching: BatchingConfig = BatchingConfig()
    max_concurrency: int = 4
    max_prompt_characters: int = 300_000
    low_confidence_threshold: float = 0.5

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrency <= 32:
            raise ValueError("max_concurrency must be between 1 and 32")
        if not 10_000 <= self.max_prompt_characters <= 1_000_000:
            raise ValueError("max_prompt_characters must be between 10000 and 1000000")
        if not 0 <= self.low_confidence_threshold <= 1:
            raise ValueError("low_confidence_threshold must be between 0 and 1")


@dataclass(frozen=True)
class _BatchOutcome:
    batch: ExtractionBatch
    items: tuple[KnowledgeInventoryItem, ...] = ()
    rejections: tuple[CandidateRejection, ...] = ()
    failure_code: str | None = None
    provider_called: bool = False


def _error_code(error: Exception) -> str:
    raw = str(getattr(error, "code", "") or "PROVIDER_CALL_FAILED").upper()
    normalized = re.sub(r"[^A-Z0-9_]+", "_", raw).strip("_")
    return normalized[:100] or "PROVIDER_CALL_FAILED"


def _evidence_reference(segment: TranscriptSegment) -> EvidenceReference:
    return EvidenceReference(
        segment_id=segment.segment_id,
        part_index=segment.part_index,
        part_cid=segment.part_cid,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        original_segment_hash=segment.segment_hash,
    )


def _candidate_item(
    candidate: ProviderKnowledgeCandidate,
    *,
    source_revision: str,
    segment_by_id: dict[str, TranscriptSegment],
) -> KnowledgeInventoryItem:
    evidence_refs = tuple(
        sorted(
            (_evidence_reference(segment_by_id[segment_id]) for segment_id in candidate.evidence_ids),
            key=lambda item: (item.part_index, item.start_ms, item.end_ms, item.segment_id),
        )
    )
    time_ranges = tuple(
        TimeRange(item.part_index, item.start_ms, item.end_ms)
        for item in evidence_refs
    )
    mentioned_only = (
        candidate.mentioned_only
        or candidate.teaching_depth is TeachingDepth.MENTION_ONLY
    )
    return KnowledgeInventoryItem(
        stable_item_id=stable_item_id(
            source_revision=source_revision,
            canonical_name=candidate.canonical_name,
            knowledge_type=candidate.knowledge_type,
            evidence_ids=candidate.evidence_ids,
        ),
        canonical_name=candidate.canonical_name,
        raw_names=tuple(candidate.raw_names),
        knowledge_type=candidate.knowledge_type,
        short_description=candidate.short_description,
        teaching_depth=candidate.teaching_depth,
        confidence=candidate.confidence,
        evidence_refs=evidence_refs,
        source_parts=tuple(sorted({item.part_index for item in evidence_refs})),
        time_ranges=time_ranges,
        candidate_prerequisites=tuple(candidate.candidate_prerequisites),
        mentioned_only=mentioned_only,
        independently_teachable=candidate.independently_teachable and not mentioned_only,
        extractor_version=EXTRACTOR_VERSION,
    )


async def _extract_batch(
    batch: ExtractionBatch,
    *,
    provider: StructuredKnowledgeProvider,
    semaphore: asyncio.Semaphore,
    segment_by_id: dict[str, TranscriptSegment],
    max_prompt_characters: int,
) -> _BatchOutcome:
    envelope = build_inventory_prompt_envelope(batch)
    messages = envelope.messages()
    prompt_characters = sum(len(str(message.get("content", ""))) for message in messages)
    if prompt_characters > max_prompt_characters:
        return _BatchOutcome(batch=batch, failure_code="PROVIDER_INPUT_TOO_LARGE")

    try:
        async with semaphore:
            raw_response = await provider.structured(
                messages,
                ProviderBatchResponse,
                0.0,
            )
        response = (
            raw_response
            if isinstance(raw_response, ProviderBatchResponse)
            else ProviderBatchResponse.model_validate(raw_response)
        )
    except Exception as error:
        return _BatchOutcome(
            batch=batch,
            failure_code=_error_code(error),
            provider_called=True,
        )

    if response.batch_id != batch.batch_id:
        return _BatchOutcome(
            batch=batch,
            failure_code="STALE_PROVIDER_BATCH",
            provider_called=True,
        )

    allowed_ids = batch.allowed_evidence_ids
    items: list[KnowledgeInventoryItem] = []
    rejections: list[CandidateRejection] = []
    for candidate in response.items:
        unknown_ids = tuple(
            segment_id
            for segment_id in candidate.evidence_ids
            if segment_id not in allowed_ids or segment_id not in segment_by_id
        )
        if unknown_ids:
            rejections.append(
                CandidateRejection(
                    batch_id=batch.batch_id,
                    candidate_id=candidate.candidate_id,
                    code="UNKNOWN_EVIDENCE",
                    evidence_ids=unknown_ids,
                )
            )
            continue
        items.append(
            _candidate_item(
                candidate,
                source_revision=batch.source_metadata.source_revision,
                segment_by_id=segment_by_id,
            )
        )
    return _BatchOutcome(
        batch=batch,
        items=tuple(items),
        rejections=tuple(rejections),
        provider_called=True,
    )


def _merge_group(
    items: list[KnowledgeInventoryItem],
    *,
    source_revision: str,
) -> KnowledgeInventoryItem:
    preferred = max(
        items,
        key=lambda item: (
            TEACHING_DEPTH_RANK[item.teaching_depth],
            item.confidence,
            len(item.short_description),
            canonical_semantic_key(item.canonical_name),
            item.canonical_name,
        ),
    )
    evidence_by_id = {
        evidence.segment_id: evidence
        for item in items
        for evidence in item.evidence_refs
    }
    evidence_refs = tuple(
        sorted(
            evidence_by_id.values(),
            key=lambda item: (item.part_index, item.start_ms, item.end_ms, item.segment_id),
        )
    )
    raw_name_by_key: dict[str, str] = {}
    for item in items:
        for value in (*item.raw_names, item.canonical_name):
            key = canonical_semantic_key(value)
            if key and key != canonical_semantic_key(preferred.canonical_name):
                raw_name_by_key.setdefault(key, value)
    prerequisites = tuple(
        sorted(
            {value for item in items for value in item.candidate_prerequisites},
            key=lambda value: (canonical_semantic_key(value), value),
        )
    )
    depth = max(items, key=lambda item: TEACHING_DEPTH_RANK[item.teaching_depth]).teaching_depth
    mentioned_only = all(item.mentioned_only for item in items)
    time_ranges = tuple(
        TimeRange(item.part_index, item.start_ms, item.end_ms)
        for item in evidence_refs
    )
    return KnowledgeInventoryItem(
        stable_item_id=stable_item_id(
            source_revision=source_revision,
            canonical_name=preferred.canonical_name,
            knowledge_type=preferred.knowledge_type,
            evidence_ids=evidence_by_id,
        ),
        canonical_name=preferred.canonical_name,
        raw_names=tuple(raw_name_by_key[key] for key in sorted(raw_name_by_key)),
        knowledge_type=preferred.knowledge_type,
        short_description=preferred.short_description,
        teaching_depth=depth,
        confidence=max(item.confidence for item in items),
        evidence_refs=evidence_refs,
        source_parts=tuple(sorted({item.part_index for item in evidence_refs})),
        time_ranges=time_ranges,
        candidate_prerequisites=prerequisites,
        mentioned_only=mentioned_only,
        independently_teachable=(
            any(item.independently_teachable for item in items) and not mentioned_only
        ),
        extractor_version=EXTRACTOR_VERSION,
    )


def reduce_inventory_items(
    items: Iterable[KnowledgeInventoryItem],
    *,
    source_revision: str,
) -> tuple[KnowledgeInventoryItem, ...]:
    """Conservatively merge duplicates and preserve every unique candidate."""

    ordered = sorted(
        items,
        key=lambda item: (
            item.knowledge_type.value,
            canonical_semantic_key(item.canonical_name),
            item.stable_item_id,
        ),
    )
    BucketKey = tuple[str, str]
    buckets: dict[BucketKey, list[KnowledgeInventoryItem]] = {}
    aliases: dict[BucketKey, set[str]] = {}
    for item in ordered:
        bucket_key = (
            item.knowledge_type.value,
            canonical_semantic_key(item.canonical_name),
        )
        buckets.setdefault(bucket_key, []).append(item)
        aliases.setdefault(bucket_key, set()).update(
            key
            for key in (canonical_semantic_key(value) for value in item.raw_names)
            if key
        )

    parent: dict[BucketKey, BucketKey] = {key: key for key in buckets}

    def find(key: BucketKey) -> BucketKey:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: BucketKey, right: BucketKey) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    for key in sorted(buckets):
        knowledge_type, canonical_key = key
        for alias_key in sorted(aliases[key]):
            other = (knowledge_type, alias_key)
            if other in buckets and canonical_key in aliases[other]:
                union(key, other)

    components: dict[BucketKey, list[KnowledgeInventoryItem]] = {}
    for key in sorted(buckets):
        components.setdefault(find(key), []).extend(buckets[key])
    merged = [
        _merge_group(group, source_revision=source_revision)
        for _key, group in sorted(components.items())
    ]
    return tuple(
        sorted(
            merged,
            key=lambda item: (
                item.evidence_refs[0].part_index,
                item.evidence_refs[0].start_ms,
                canonical_semantic_key(item.canonical_name),
                item.stable_item_id,
            ),
        )
    )


async def extract_knowledge_inventory(
    bundle: TranscriptEvidenceBundle,
    *,
    source_metadata: SourceMetadata,
    provider: StructuredKnowledgeProvider,
    normalization: TranscriptNormalizationResult | None = None,
    part_contexts: Iterable[PartChapterContext] = (),
    config: ExtractionConfig = ExtractionConfig(),
) -> KnowledgeInventory:
    plan = build_extraction_plan(
        bundle,
        source_metadata=source_metadata,
        normalization=normalization,
        part_contexts=part_contexts,
        config=config.batching,
    )
    segment_by_id = {segment.segment_id: segment for segment in bundle.segments}
    semaphore = asyncio.Semaphore(config.max_concurrency)
    outcomes = await asyncio.gather(
        *(
            _extract_batch(
                batch,
                provider=provider,
                semaphore=semaphore,
                segment_by_id=segment_by_id,
                max_prompt_characters=config.max_prompt_characters,
            )
            for batch in plan.batches
        )
    )

    successful = [outcome for outcome in outcomes if outcome.failure_code is None]
    failed = [outcome for outcome in outcomes if outcome.failure_code is not None]
    items = reduce_inventory_items(
        (item for outcome in successful for item in outcome.items),
        source_revision=bundle.source_revision,
    )
    successful_core_ids = {
        segment_id
        for outcome in successful
        for segment_id in outcome.batch.core_segment_ids
    }
    failed_core_ids = {
        segment_id
        for outcome in failed
        for segment_id in outcome.batch.core_segment_ids
    }
    covered_ids = tuple(
        segment.segment_id
        for segment in bundle.segments
        if segment.segment_id in successful_core_ids
    )
    failed_ids = tuple(
        segment.segment_id
        for segment in bundle.segments
        if segment.segment_id in failed_core_ids
    )
    low_confidence_by_key = {
        (time_range.part_index, time_range.start_ms, time_range.end_ms): time_range
        for item in items
        if item.confidence < config.low_confidence_threshold
        for time_range in item.time_ranges
    }
    coverage = CoverageLedger(
        total_segment_count=plan.total_segment_count,
        covered_segment_ids=covered_ids,
        failed_segment_ids=failed_ids,
        low_confidence_ranges=tuple(
            low_confidence_by_key[key] for key in sorted(low_confidence_by_key)
        ),
    )
    if not failed:
        status = InventoryStatus.COMPLETE
    elif successful:
        status = InventoryStatus.PARTIAL
    else:
        status = InventoryStatus.FAILED
    return KnowledgeInventory(
        source_identity=bundle.source_identity,
        source_revision=bundle.source_revision,
        status=status,
        items=items,
        coverage=coverage,
        failed_batches=tuple(
            FailedBatch(
                batch_id=outcome.batch.batch_id,
                segment_ids=outcome.batch.core_segment_ids,
                code=outcome.failure_code or "PROVIDER_CALL_FAILED",
            )
            for outcome in failed
        ),
        rejected_candidates=tuple(
            rejection for outcome in successful for rejection in outcome.rejections
        ),
        batch_count=len(plan.batches),
        successful_batches=len(successful),
        provider_calls=sum(1 for outcome in outcomes if outcome.provider_called),
    )
