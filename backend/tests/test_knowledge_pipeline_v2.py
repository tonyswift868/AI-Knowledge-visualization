from __future__ import annotations

import asyncio
from typing import Any

from app.knowledge.inventory.extractor import ExtractionConfig
from app.knowledge.inventory.models import KnowledgeType, SourceMetadata
from app.knowledge.pipeline_v2.handoff import ConceptVerificationStatus
from app.knowledge.pipeline_v2.pipeline import KnowledgePipelineV2, KnowledgePipelineV2Config
from app.knowledge.pipeline_v2.progress import PipelineStatus
from app.knowledge.verification.models import CriticalConceptPolicy, CriticalConceptRule
from app.sources.video.transcript_normalizer import GlossaryTerm, TerminologyGlossary
from test_knowledge_inventory import StructuredFixtureProvider, _bundle, _candidate, _metadata
from test_knowledge_verification import StructuredVerificationFake, _miss


class StepClock:
    def __init__(self, step: float = 0.05) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


def _covered_response(payload: dict[str, Any], _index: int) -> dict[str, Any]:
    return {
        "attempt_id": payload["attempt_id"],
        "missed_candidates": [],
        "item_findings": [
            {
                "inventory_item_id": item["stable_item_id"],
                "status": "COVERED",
                "evidence_ids": item["evidence_ids"],
                "reason": "fixture anchor evidence covers the item",
                "confidence": 0.99,
            }
            for item in payload["inventory_items"]
        ],
    }


def _empty_inventory(payload: dict[str, Any], _index: int) -> dict[str, Any]:
    return {"batch_id": payload["batch_id"], "items": []}


def _run(
    bundle,
    inventory_provider,
    verification_provider,
    *,
    glossary: TerminologyGlossary | None = None,
    pipeline_config: KnowledgePipelineV2Config = KnowledgePipelineV2Config(),
    critical_policy: CriticalConceptPolicy = CriticalConceptPolicy(),
    support_hints=(),
    clock=None,
    progress_sink=None,
):
    pipeline = KnowledgePipelineV2(
        inventory_provider,
        verification_provider,
        config=pipeline_config,
        clock=clock or StepClock(),
    )
    return asyncio.run(
        pipeline.run(
            bundle,
            glossary or TerminologyGlossary(()),
            source_metadata=_metadata(bundle),
            critical_policy=critical_policy,
            support_hints=support_hints,
            progress_sink=progress_sink,
        )
    )


def test_complete_pipeline_preserves_original_evidence_and_emits_progress() -> None:
    bundle = _bundle(
        [
            "hash map uses buckets and collision handling",
            "StampedLock provides optimistic reads",
        ]
    )
    before = bundle.to_dict()

    def inventory_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        first, second = payload["core_segment_ids"]
        return {
            "batch_id": payload["batch_id"],
            "items": [
                _candidate("hash-map", "HashMap", [first], raw_names=["hash map"]),
                _candidate("stamped-lock", "StampedLock", [second], knowledge_type="API"),
            ],
        }

    inventory_provider = StructuredFixtureProvider(inventory_handler)
    verification_provider = StructuredVerificationFake(_covered_response)
    emitted = []
    result = _run(
        bundle,
        inventory_provider,
        verification_provider,
        glossary=TerminologyGlossary((GlossaryTerm("HashMap", ("hash map",)),)),
        progress_sink=emitted.append,
    )

    assert result.status is PipelineStatus.COMPLETE
    assert bundle.to_dict() == before
    assert result.verified_bundle.evidence is bundle
    assert result.verified_bundle.normalization.corrections
    assert result.verified_bundle.inventory.items[0].evidence_refs[0].original_segment_hash
    assert len(result.verified_bundle.inventory.items) == 2
    assert result.verified_bundle.repair_patch.additions == ()
    assert result.map_handoff is not None
    assert len(result.map_handoff.concepts) == 2
    assert {item.canonical_name for item in result.map_handoff.concepts} == {
        "HashMap",
        "StampedLock",
    }
    assert [item.status for item in emitted] == [
        PipelineStatus.PREFLIGHTED,
        PipelineStatus.INVENTORY_RUNNING,
        PipelineStatus.VERIFYING,
        PipelineStatus.COMPLETE,
    ]
    assert emitted[2].estimated_remaining_ms is not None
    assert emitted[2].estimate_basis == "BATCH_THROUGHPUT_ESTIMATE"
    assert emitted[-1].remaining_batches == 0
    assert emitted[-1].processed_evidence_count == 2
    assert result.receipt.provider_calls == 2
    assert result.receipt.repair_count == 0
    assert inventory_provider.calls[0]["evidence"][0]["normalized_text"].startswith("HashMap")


def test_critical_miss_becomes_derived_repair_and_handoff_lineage() -> None:
    bundle = _bundle(["Compare-and-swap atomically updates a value."])
    segment_id = bundle.segments[0].segment_id

    def verification_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss(
                    "cas-miss",
                    "Compare-And-Swap",
                    [segment_id],
                    knowledge_type="ALGORITHM",
                    raw_names=["CAS"],
                    confidence=0.99,
                )
            ],
            "item_findings": [],
        }

    result = _run(
        bundle,
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(verification_handler),
        critical_policy=CriticalConceptPolicy(
            (
                CriticalConceptRule(
                    "Compare-And-Swap",
                    KnowledgeType.ALGORITHM,
                    ("CAS",),
                ),
            )
        ),
    )

    assert result.status is PipelineStatus.COMPLETE
    assert result.verified_bundle.inventory.items == ()
    assert len(result.verified_bundle.repair_patch.additions) == 1
    addition = result.verified_bundle.repair_patch.additions[0]
    assert addition.critical is True
    assert addition.evidence_refs[0].segment_id == segment_id
    assert result.map_handoff is not None
    assert len(result.map_handoff.concepts) == 1
    concept = result.map_handoff.concepts[0]
    assert concept.verification_status is ConceptVerificationStatus.VERIFIED_REPAIR_CANDIDATE
    assert concept.repair_lineage[0].patch_id == result.verified_bundle.repair_patch.patch_id
    assert concept.evidence_links[0].original_evidence_hash == bundle.segments[0].segment_hash


def test_handoff_merges_explicit_duplicates_and_preserves_unique_long_tail() -> None:
    bundle = _bundle(["CAS is compare-and-swap. StampedLock optimistic reads are explained once."])
    segment_id = bundle.segments[0].segment_id

    def inventory_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "batch_id": payload["batch_id"],
            "items": [
                _candidate("cas-a", "CAS", [segment_id], raw_names=["Compare-And-Swap"]),
                _candidate("cas-b", "Compare-And-Swap", [segment_id], raw_names=["CAS"]),
                _candidate("tail", "StampedLock", [segment_id], knowledge_type="API"),
            ],
        }

    result = _run(
        bundle,
        StructuredFixtureProvider(inventory_handler),
        StructuredVerificationFake(_covered_response),
    )

    assert result.map_handoff is not None
    assert len(result.verified_bundle.inventory.items) == 2
    assert len(result.map_handoff.concepts) == 2
    assert {item.canonical_name for item in result.map_handoff.concepts} == {
        "Compare-And-Swap",
        "StampedLock",
    }


def test_handoff_payload_contains_no_summary_or_runtime_write_shape() -> None:
    bundle = _bundle(["A unique concept is independently taught."])
    segment_id = bundle.segments[0].segment_id

    def inventory_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "batch_id": payload["batch_id"],
            "items": [_candidate("unique", "Unique Concept", [segment_id])],
        }

    result = _run(
        bundle,
        StructuredFixtureProvider(inventory_handler),
        StructuredVerificationFake(_covered_response),
    )
    assert result.map_handoff is not None
    payload = result.map_handoff.to_dict()
    concept = payload["concepts"][0]

    assert set(concept) == {
        "stable_concept_id",
        "canonical_name",
        "raw_names",
        "knowledge_type",
        "teaching_depth",
        "evidence_links",
        "dependencies",
        "hints",
        "confidence",
        "verification_status",
        "repair_lineage",
    }
    assert "summary" not in str(payload).lower()
    assert "runtime" not in payload


def test_pipeline_cache_identity_is_stable_and_input_sensitive() -> None:
    bundle = _bundle(["cache identity evidence"])
    pipeline = KnowledgePipelineV2(
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(_covered_response),
    )
    glossary = TerminologyGlossary(())
    metadata = SourceMetadata.from_bundle(bundle, title="知识编译课程")
    first = pipeline.cache_identity(bundle, glossary, source_metadata=metadata)
    second = pipeline.cache_identity(bundle, glossary, source_metadata=metadata)
    changed = _bundle(["cache identity evidence changed"])

    assert first == second
    assert first != pipeline.cache_identity(
        changed,
        glossary,
        source_metadata=SourceMetadata.from_bundle(changed, title="知识编译课程"),
    )
    assert first.startswith("kpv2-cache-")


def test_pipeline_config_changes_cache_identity() -> None:
    bundle = _bundle(["config-sensitive identity"])
    default = KnowledgePipelineV2(
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(_covered_response),
    )
    changed = KnowledgePipelineV2(
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(_covered_response),
        config=KnowledgePipelineV2Config(
            inventory=ExtractionConfig(max_concurrency=1),
        ),
    )
    glossary = TerminologyGlossary(())

    assert default.cache_identity(bundle, glossary, source_metadata=_metadata(bundle)) != changed.cache_identity(
        bundle,
        glossary,
        source_metadata=_metadata(bundle),
    )
