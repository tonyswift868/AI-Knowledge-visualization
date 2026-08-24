from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from typing import Any, Callable

import pytest

from app.knowledge.inventory.batching import BatchingConfig, build_extraction_plan
from app.knowledge.inventory.extractor import ExtractionConfig, extract_knowledge_inventory
from app.knowledge.inventory.models import (
    InventoryStatus,
    KnowledgeType,
    SourceMetadata,
    TeachingDepth,
)
from app.knowledge.inventory.prompt import (
    build_inventory_cache_identity,
    build_inventory_prompt_envelope,
)
from app.sources.video.transcript_evidence import (
    SelectedTrackEvidence,
    TranscriptEvidenceBundle,
    TranscriptPartEvidence,
    TranscriptSegment,
)
from app.sources.video.transcript_normalizer import (
    GlossaryTerm,
    TerminologyGlossary,
    normalize_transcript,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bundle(rows: list[str | tuple[str, int]]) -> TranscriptEvidenceBundle:
    segments: list[TranscriptSegment] = []
    part_ordinals: dict[int, int] = {}
    for global_index, raw in enumerate(rows, start=1):
        text, part_index = raw if isinstance(raw, tuple) else (raw, 1)
        ordinal = part_ordinals.get(part_index, 0) + 1
        part_ordinals[part_index] = ordinal
        segment_hash = _digest(text)
        segments.append(
            TranscriptSegment(
                stable_segment_id=f"segment-{global_index}-{segment_hash[:12]}",
                ordinal=ordinal,
                part_index=part_index,
                part_cid=str(1000 + part_index),
                start_ms=(ordinal - 1) * 1000,
                end_ms=ordinal * 1000,
                original_text=text,
                language="zh-Hans",
                subtitle_kind="manual",
                source_reference="https://example.test/course",
                segment_hash=segment_hash,
            )
        )
    track = SelectedTrackEvidence(
        track_identity="track",
        payload_source_identity="fixture",
        language="zh-Hans",
        kind="manual",
        extension="srt",
        source="data",
        name=None,
    )
    segment_ids_by_part: dict[int, list[str]] = {}
    text_by_part: dict[int, list[str]] = {}
    for segment in segments:
        segment_ids_by_part.setdefault(segment.part_index, []).append(segment.segment_id)
        text_by_part.setdefault(segment.part_index, []).append(segment.original_text)
    parts = tuple(
        TranscriptPartEvidence(
            part_index=part_index,
            cid=str(1000 + part_index),
            title=f"课程 P{part_index}",
            duration_seconds=float(part_ordinals[part_index]),
            subtitle_identity=f"subtitle-{part_index}",
            selected_track=track,
            raw_payload_hash=_digest("\n".join(text_by_part[part_index])),
            segment_ids=tuple(segment_ids_by_part[part_index]),
            warnings=(),
        )
        for part_index in sorted(part_ordinals)
    )
    timeline_hash = _digest("\n".join(segment.segment_hash for segment in segments))
    bundle_hash = _digest("bundle:" + timeline_hash)
    return TranscriptEvidenceBundle(
        schema="mentorflow.transcript-evidence.v1",
        parser_version="mentorflow.subtitle-timeline.v1",
        bundle_id="bundle-" + bundle_hash,
        bundle_hash=bundle_hash,
        source_reference="https://example.test/course",
        platform="fixture",
        bvid="fixture-course",
        source_identity="fixture:course",
        source_revision="revision-" + timeline_hash,
        part_count=len(parts),
        subtitle_language="zh-Hans",
        subtitle_kind="manual",
        fetched_at="2026-08-25T00:00:00+00:00",
        raw_payload_hash=_digest("payload:" + timeline_hash),
        normalized_timeline_hash=timeline_hash,
        parts=parts,
        segments=tuple(segments),
        warnings=(),
    )


def _metadata(bundle: TranscriptEvidenceBundle) -> SourceMetadata:
    return SourceMetadata.from_bundle(bundle, title="知识编译课程")


def _candidate(
    candidate_id: str,
    name: str,
    evidence_ids: list[str],
    *,
    knowledge_type: str = "CONCEPT",
    raw_names: list[str] | None = None,
    depth: str = "CORE_EXPLANATION",
    confidence: float = 0.9,
    mentioned_only: bool = False,
    independently_teachable: bool = True,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "canonical_name": name,
        "raw_names": list(raw_names or []),
        "knowledge_type": knowledge_type,
        "short_description": f"{name} 的证据内教学说明",
        "teaching_depth": depth,
        "confidence": confidence,
        "evidence_ids": evidence_ids,
        "candidate_prerequisites": [],
        "mentioned_only": mentioned_only,
        "independently_teachable": independently_teachable,
    }


class StructuredFixtureProvider:
    provider_name = "fake"
    model_name = "fake-inventory-v1"

    def __init__(self, handler: Callable[[dict[str, Any], int], dict[str, Any]]):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    async def structured(self, messages, response_model, temperature=0.2):
        assert temperature == 0.0
        payload = json.loads(messages[1]["content"])["p2_batch_dynamic"]
        self.calls.append(payload)
        response = self.handler(payload, len(self.calls) - 1)
        return response_model.model_validate(response)


def _run(
    bundle: TranscriptEvidenceBundle,
    provider: StructuredFixtureProvider,
    **kwargs: Any,
):
    return asyncio.run(
        extract_knowledge_inventory(
            bundle,
            source_metadata=_metadata(bundle),
            provider=provider,
            **kwargs,
        )
    )


def test_batches_are_stable_bounded_part_local_and_overlap_only_for_continuity() -> None:
    bundle = _bundle(
        [(f"part one {index}", 1) for index in range(5)]
        + [(f"part two {index}", 2) for index in range(3)]
    )
    config = BatchingConfig(max_segments=2, max_characters=1000, overlap_segments=1)
    first = build_extraction_plan(bundle, source_metadata=_metadata(bundle), config=config)
    second = build_extraction_plan(bundle, source_metadata=_metadata(bundle), config=config)

    assert len(first.batches) == 5
    assert [batch.batch_id for batch in first.batches] == [batch.batch_id for batch in second.batches]
    assert [item for batch in first.batches for item in batch.core_segment_ids] == [
        segment.segment_id for segment in bundle.segments
    ]
    assert all(len(batch.core_segment_ids) <= 2 for batch in first.batches)
    assert all({item.segment.part_index for item in batch.evidence} == {batch.part_index} for batch in first.batches)
    assert any(len(batch.evidence) > len(batch.core_segment_ids) for batch in first.batches)


def test_prompt_prefix_and_cache_identity_are_stable_but_batches_do_not_collide() -> None:
    bundle = _bundle(["alpha concept", "beta concept", "gamma concept"])
    plan = build_extraction_plan(
        bundle,
        source_metadata=_metadata(bundle),
        config=BatchingConfig(max_segments=2, max_characters=1000),
    )
    envelopes = [build_inventory_prompt_envelope(batch) for batch in plan.batches]

    assert len({envelope.stable_prefix_hash for envelope in envelopes}) == 1
    assert len({envelope.p2_batch_dynamic for envelope in envelopes}) == 2
    identities = [
        build_inventory_cache_identity(batch, envelope, provider_family="fake", model="fixture")
        for batch, envelope in zip(plan.batches, envelopes, strict=True)
    ]
    assert identities[0].task_type == "knowledge_extract"
    assert identities[0].cache_key != identities[1].cache_key
    assert identities[0].cache_key == build_inventory_cache_identity(
        plan.batches[0], envelopes[0], provider_family="fake", model="fixture"
    ).cache_key


def test_extractor_merges_explicit_aliases_preserves_unique_long_tail_and_is_stable() -> None:
    bundle = _bundle(["CAS is compare and swap. StampedLock is mentioned once but explained."])
    segment_id = bundle.segments[0].segment_id

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "batch_id": payload["batch_id"],
            "items": [
                _candidate("cas-a", "CAS", [segment_id], raw_names=["Compare-And-Swap"]),
                _candidate("cas-b", "Compare-And-Swap", [segment_id], raw_names=["CAS"]),
                _candidate("tail", "StampedLock", [segment_id], knowledge_type="API"),
            ],
        }

    before = bundle.to_dict()
    first = _run(bundle, StructuredFixtureProvider(handler))
    second = _run(bundle, StructuredFixtureProvider(handler))

    assert first.status is InventoryStatus.COMPLETE
    assert len(first.items) == 2
    cas = next(item for item in first.items if item.knowledge_type is KnowledgeType.CONCEPT)
    assert {cas.canonical_name, *cas.raw_names} == {"CAS", "Compare-And-Swap"}
    assert any(item.canonical_name == "StampedLock" for item in first.items)
    assert [item.stable_item_id for item in first.items] == [item.stable_item_id for item in second.items]
    assert first.coverage.coverage_ratio == 1.0
    assert all(item.evidence_refs[0].original_segment_hash == bundle.segments[0].segment_hash for item in first.items)
    assert "original_text" not in first.items[0].to_dict()
    assert bundle.to_dict() == before


def test_unknown_evidence_is_rejected_without_inventing_a_citation() -> None:
    bundle = _bundle(["A grounded concept"])

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "batch_id": payload["batch_id"],
            "items": [_candidate("bad", "Invented", ["segment-does-not-exist"])],
        }

    result = _run(bundle, StructuredFixtureProvider(handler))

    assert result.status is InventoryStatus.COMPLETE
    assert result.items == ()
    assert result.coverage.coverage_ratio == 1.0
    assert [item.code for item in result.rejected_candidates] == ["UNKNOWN_EVIDENCE"]


def test_invalid_structured_output_fails_closed() -> None:
    bundle = _bundle(["alpha", "beta"])

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {"batch_id": payload["batch_id"], "items": "not-a-list", "extra": True}

    result = _run(bundle, StructuredFixtureProvider(handler))

    assert result.status is InventoryStatus.FAILED
    assert result.items == ()
    assert result.coverage.coverage_ratio == 0.0
    assert result.failed_batches[0].code == "PROVIDER_CALL_FAILED"


def test_one_failed_batch_is_partial_and_cannot_claim_complete() -> None:
    bundle = _bundle(["alpha", "beta", "gamma", "delta"])

    def handler(payload: dict[str, Any], index: int) -> dict[str, Any]:
        if index == 1:
            raise TimeoutError("fixture timeout")
        return {"batch_id": payload["batch_id"], "items": []}

    provider = StructuredFixtureProvider(handler)
    result = _run(
        bundle,
        provider,
        config=ExtractionConfig(
            batching=BatchingConfig(max_segments=2, max_characters=1000),
            max_concurrency=1,
        ),
    )

    assert result.status is InventoryStatus.PARTIAL
    assert result.coverage.coverage_ratio == 0.5
    assert len(result.coverage.covered_segment_ids) == 2
    assert len(result.coverage.failed_segment_ids) == 2
    assert result.successful_batches == 1
    assert result.provider_calls == 2


def test_mention_only_is_not_promoted_to_independently_teachable() -> None:
    bundle = _bundle(["This is a little like Redis, without explaining Redis."])
    segment_id = bundle.segments[0].segment_id

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "batch_id": payload["batch_id"],
            "items": [
                _candidate(
                    "redis-mention",
                    "Redis",
                    [segment_id],
                    knowledge_type="TERM",
                    depth="MENTION_ONLY",
                    mentioned_only=True,
                    independently_teachable=True,
                )
            ],
        }

    result = _run(bundle, StructuredFixtureProvider(handler))
    item = result.items[0]

    assert item.teaching_depth is TeachingDepth.MENTION_ONLY
    assert item.mentioned_only is True
    assert item.independently_teachable is False


def test_normalized_view_aids_extraction_but_citation_remains_original_evidence() -> None:
    bundle = _bundle(["hash map uses buckets"])
    normalization = asyncio.run(
        normalize_transcript(
            bundle,
            TerminologyGlossary((GlossaryTerm("HashMap", ("hash map",)),)),
        )
    )
    plan = build_extraction_plan(
        bundle,
        source_metadata=_metadata(bundle),
        normalization=normalization,
    )
    prompt_segment = plan.batches[0].dynamic_payload()["evidence"][0]

    assert prompt_segment["original_text"] == "hash map uses buckets"
    assert prompt_segment["normalized_text"] == "HashMap uses buckets"
    assert prompt_segment["original_segment_hash"] == bundle.segments[0].segment_hash


def test_source_revision_changes_item_identity() -> None:
    bundle = _bundle(["stable concept"])

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "batch_id": payload["batch_id"],
            "items": [_candidate("stable", "Stable Concept", payload["core_segment_ids"])],
        }

    first = _run(bundle, StructuredFixtureProvider(handler))
    changed = replace(bundle, source_revision=bundle.source_revision + "-changed")
    second = _run(changed, StructuredFixtureProvider(handler))

    assert first.items[0].stable_item_id != second.items[0].stable_item_id


def test_105_single_occurrence_items_survive_cross_batch_reduction() -> None:
    bundle = _bundle([f"UniqueKnowledge{index:03d}" for index in range(105)])

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        items = []
        for evidence in payload["evidence"]:
            if not evidence["is_core"]:
                continue
            items.append(
                _candidate(
                    "candidate-" + evidence["segment_id"],
                    evidence["original_text"],
                    [evidence["segment_id"]],
                    confidence=0.8,
                )
            )
        return {"batch_id": payload["batch_id"], "items": items}

    provider = StructuredFixtureProvider(handler)
    result = _run(
        bundle,
        provider,
        config=ExtractionConfig(
            batching=BatchingConfig(max_segments=25, max_characters=4000),
            max_concurrency=4,
        ),
    )

    assert result.status is InventoryStatus.COMPLETE
    assert len(result.items) == 105
    assert "UniqueKnowledge104" in {item.canonical_name for item in result.items}
    assert result.coverage.coverage_ratio == 1.0
    assert len(provider.calls) == 5


def test_low_confidence_ranges_are_exposed_without_discarding_the_item() -> None:
    bundle = _bundle(["rare edge condition"])
    segment_id = bundle.segments[0].segment_id

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "batch_id": payload["batch_id"],
            "items": [_candidate("rare", "Rare Edge", [segment_id], confidence=0.2)],
        }

    result = _run(bundle, StructuredFixtureProvider(handler))

    assert len(result.items) == 1
    assert result.coverage.low_confidence_ranges[0].start_ms == 0


def test_mismatched_source_or_normalizer_contract_is_rejected_before_provider_call() -> None:
    bundle = _bundle(["alpha"])
    provider = StructuredFixtureProvider(lambda payload, _index: {"batch_id": payload["batch_id"], "items": []})
    wrong_metadata = replace(_metadata(bundle), source_revision="wrong")

    with pytest.raises(ValueError, match="revision"):
        asyncio.run(
            extract_knowledge_inventory(
                bundle,
                source_metadata=wrong_metadata,
                provider=provider,
            )
        )
    assert provider.calls == []
