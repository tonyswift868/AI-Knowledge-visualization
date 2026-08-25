from __future__ import annotations

import asyncio
from typing import Any

from app.knowledge.inventory.batching import BatchingConfig
from app.knowledge.inventory.extractor import ExtractionConfig
from app.knowledge.inventory.models import KnowledgeType
from app.knowledge.pipeline_v2.metrics import (
    PipelineMetricScope,
    label_fixture_metrics,
    operational_metrics,
)
from app.knowledge.pipeline_v2.pipeline import KnowledgePipelineV2, KnowledgePipelineV2Config
from app.knowledge.pipeline_v2.progress import PipelineStatus
from app.knowledge.verification.batching import VerificationBatchConfig
from app.knowledge.verification.models import (
    MissClassification,
    SupportAnomalyState,
    SupportKnowledgeHint,
)
from app.knowledge.verification.verifier import RecallVerifierConfig
from app.sources.video.transcript_normalizer import TerminologyGlossary
from test_knowledge_inventory import StructuredFixtureProvider, _bundle, _candidate, _metadata
from test_knowledge_pipeline_v2 import StepClock, _covered_response, _empty_inventory
from test_knowledge_verification import StructuredVerificationFake, _miss


def _config(
    *,
    inventory_batch: int = 1,
    verification_batch: int = 1,
    max_characters: int = 1000,
) -> KnowledgePipelineV2Config:
    return KnowledgePipelineV2Config(
        inventory=ExtractionConfig(
            batching=BatchingConfig(
                max_segments=inventory_batch,
                max_characters=max_characters,
                overlap_segments=0,
            ),
            max_concurrency=4,
        ),
        verification=RecallVerifierConfig(
            batching=VerificationBatchConfig(
                max_evidence=verification_batch,
                max_characters=max_characters,
                overlap_evidence=0,
            ),
            max_concurrency=4,
        ),
    )


def _run(bundle, inventory_provider, verification_provider, *, config=None, support_hints=()):
    pipeline = KnowledgePipelineV2(
        inventory_provider,
        verification_provider,
        config=config or KnowledgePipelineV2Config(),
        clock=StepClock(0.02),
    )
    return asyncio.run(
        pipeline.run(
            bundle,
            TerminologyGlossary(()),
            source_metadata=_metadata(bundle),
            support_hints=support_hints,
        )
    )


def test_unknown_evidence_is_rejected_at_inventory_and_verification_boundaries() -> None:
    bundle = _bundle(["anchor evidence"])

    def inventory_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "batch_id": payload["batch_id"],
            "items": [_candidate("unknown", "Invented", ["unknown-evidence"])],
        }

    def verification_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss("unknown-miss", "Invented Miss", ["unknown-evidence"])
            ],
            "item_findings": [],
        }

    result = _run(
        bundle,
        StructuredFixtureProvider(inventory_handler),
        StructuredVerificationFake(verification_handler),
    )

    assert result.status is PipelineStatus.COMPLETE
    assert result.verified_bundle.inventory.items == ()
    assert result.verified_bundle.inventory.rejected_candidates[0].code == "UNKNOWN_EVIDENCE"
    assert result.verified_bundle.verification.rejected_candidates[0].code == "UNKNOWN_ANCHOR_EVIDENCE"
    assert result.verified_bundle.repair_patch.additions == ()
    assert result.map_handoff is not None and result.map_handoff.concepts == ()


def test_support_hint_requires_anchor_evidence_and_marks_possible_anchor_miss() -> None:
    bundle = _bundle(["The anchor explains stamped locks."])
    segment_id = bundle.segments[0].segment_id
    confirmed = SupportKnowledgeHint(
        hint_id="support-stamped-lock",
        canonical_name="StampedLock",
        knowledge_type=KnowledgeType.API,
        support_source_identity="support:course",
        support_evidence_ids=("support-evidence-1",),
        confidence=0.9,
        suspected_anchor_evidence_ids=(segment_id,),
    )
    unconfirmed = SupportKnowledgeHint(
        hint_id="support-phantom",
        canonical_name="Phantom Concept",
        knowledge_type=KnowledgeType.CONCEPT,
        support_source_identity="support:course",
        support_evidence_ids=("support-evidence-2",),
        confidence=0.9,
        suspected_anchor_evidence_ids=(),
    )

    def verification_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss(
                    "stamped-lock",
                    "StampedLock",
                    [segment_id],
                    knowledge_type="API",
                    support_hint_ids=[confirmed.hint_id],
                )
            ],
            "item_findings": [],
        }

    result = _run(
        bundle,
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(verification_handler),
        support_hints=(confirmed, unconfirmed),
    )

    addition = result.verified_bundle.repair_patch.additions[0]
    assert addition.classification is MissClassification.POSSIBLE_ANCHOR_MISS
    assert result.map_handoff is not None
    assert result.map_handoff.concepts[0].hints == (confirmed.hint_id,)
    anomalies = {
        item.hint_id: item.state
        for item in result.verified_bundle.verification.diagnostics.suspicious_support_anomalies
    }
    assert anomalies == {
        confirmed.hint_id: SupportAnomalyState.POSSIBLE_ANCHOR_MISS,
        unconfirmed.hint_id: SupportAnomalyState.POSSIBLE_ANCHOR_MISS_UNCONFIRMED,
    }
    assert all("OUT_OF_SCOPE" not in item.code for item in result.verified_bundle.verification.rejected_candidates)


def test_mention_only_candidate_does_not_create_repair() -> None:
    bundle = _bundle(["Redis is named but not explained."])
    segment_id = bundle.segments[0].segment_id

    def verification_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss(
                    "redis-mention",
                    "Redis",
                    [segment_id],
                    mentioned_only=True,
                    independently_teachable=False,
                    depth="MENTION_ONLY",
                )
            ],
            "item_findings": [],
        }

    result = _run(
        bundle,
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(verification_handler),
    )

    assert result.status is PipelineStatus.COMPLETE
    assert result.verified_bundle.repair_patch.additions == ()
    assert result.verified_bundle.verification.rejected_candidates[0].code == "MENTION_ONLY_NOT_REPAIRABLE"


def test_failed_batch_is_isolated_and_partial_never_claims_complete() -> None:
    bundle = _bundle(["first evidence", "second evidence", "third evidence"])
    failed_id = bundle.segments[0].segment_id

    def verification_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        if payload["core_evidence_ids"] == [failed_id]:
            raise RuntimeError("fixture verification failure")
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [],
            "item_findings": [],
        }

    result = _run(
        bundle,
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(verification_handler),
        config=_config(),
    )

    assert result.status is PipelineStatus.PARTIAL
    assert result.receipt.progress_history[-1].status is PipelineStatus.PARTIAL
    assert result.verified_bundle.repair_patch.verification_status.value == "PARTIAL"
    assert result.receipt.processed_evidence_count == 2
    assert result.receipt.retry_count == 1
    assert len(result.receipt.failed_evidence_ranges) == 1
    failed = result.receipt.failed_evidence_ranges[0]
    assert failed.stage == "VERIFICATION"
    assert failed.evidence_ids == (failed_id,)
    assert failed.part_index == 1
    assert failed.start_ms == 0
    assert failed.end_ms == 1000
    assert failed.attempts == 2
    assert failed.final_attempt_id is not None


def test_all_failed_batches_produce_failed_status_without_handoff() -> None:
    bundle = _bundle(["first evidence", "second evidence"])

    def always_fail(_payload: dict[str, Any], _index: int) -> dict[str, Any]:
        raise RuntimeError("all verification batches fail")

    result = _run(
        bundle,
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(always_fail),
        config=_config(),
    )

    assert result.status is PipelineStatus.FAILED
    assert result.map_handoff is None
    assert result.receipt.progress_history[-1].status is PipelineStatus.FAILED
    assert result.receipt.processed_evidence_count == 0
    assert result.receipt.retry_count == 2
    assert len(result.receipt.failed_evidence_ranges) == 2
    assert all(item.attempts == 2 for item in result.receipt.failed_evidence_ranges)


def test_inventory_failure_keeps_pipeline_partial_even_when_verification_completes() -> None:
    bundle = _bundle(["inventory failure evidence", "successful inventory evidence"])
    failed_id = bundle.segments[0].segment_id

    def inventory_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        if payload["core_segment_ids"] == [failed_id]:
            raise RuntimeError("fixture inventory failure")
        return {"batch_id": payload["batch_id"], "items": []}

    result = _run(
        bundle,
        StructuredFixtureProvider(inventory_handler),
        StructuredVerificationFake(
            lambda payload, _index: {
                "attempt_id": payload["attempt_id"],
                "missed_candidates": [],
                "item_findings": [],
            }
        ),
        config=_config(),
    )

    assert result.verified_bundle.inventory.status.value == "PARTIAL"
    assert result.verified_bundle.verification.verification_status.value == "COMPLETE"
    assert result.status is PipelineStatus.PARTIAL
    assert result.receipt.progress_history[-1].status is PipelineStatus.PARTIAL
    assert len(result.receipt.failed_evidence_ranges) == 1
    assert result.receipt.failed_evidence_ranges[0].stage == "INVENTORY"
    assert result.receipt.failed_evidence_ranges[0].evidence_ids == (failed_id,)


def test_retry_identity_and_pipeline_receipt_are_idempotent() -> None:
    bundle = _bundle(["first evidence", "second evidence"])
    failed_id = bundle.segments[0].segment_id

    def make_verifier():
        def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
            if payload["core_evidence_ids"] == [failed_id] and payload["attempt"] == 1:
                raise RuntimeError("retry this batch")
            return {
                "attempt_id": payload["attempt_id"],
                "missed_candidates": [],
                "item_findings": [],
            }

        return StructuredVerificationFake(handler)

    first_provider = make_verifier()
    second_provider = make_verifier()
    first = _run(
        bundle,
        StructuredFixtureProvider(_empty_inventory),
        first_provider,
        config=_config(),
    )
    second = _run(
        bundle,
        StructuredFixtureProvider(_empty_inventory),
        second_provider,
        config=_config(),
    )
    first_attempts = sorted(
        (item["batch_id"], item["attempt"], item["attempt_id"])
        for item in first_provider.calls
    )
    second_attempts = sorted(
        (item["batch_id"], item["attempt"], item["attempt_id"])
        for item in second_provider.calls
    )

    assert first.status is second.status is PipelineStatus.COMPLETE
    assert first_attempts == second_attempts
    assert first.receipt.retry_count == second.receipt.retry_count == 1
    assert first.receipt.cache_identity == second.receipt.cache_identity
    assert first.receipt.receipt_id == second.receipt.receipt_id


def test_duplicate_repair_candidates_collapse_across_overlapping_batches() -> None:
    bundle = _bundle(["CAS first explanation", "CAS continuation"])
    anchor_id = bundle.segments[0].segment_id
    config = KnowledgePipelineV2Config(
        inventory=ExtractionConfig(
            batching=BatchingConfig(max_segments=1, max_characters=1000, overlap_segments=1),
        ),
        verification=RecallVerifierConfig(
            batching=VerificationBatchConfig(
                max_evidence=1,
                max_characters=1000,
                overlap_evidence=1,
            )
        ),
    )

    def verification_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss(
                    "duplicate-cas-" + payload["batch_id"],
                    "Compare-And-Swap",
                    [anchor_id],
                    knowledge_type="ALGORITHM",
                    raw_names=["CAS"],
                )
            ],
            "item_findings": [],
        }

    result = _run(
        bundle,
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(verification_handler),
        config=config,
    )

    assert len(result.verified_bundle.verification.missed_candidates) == 1
    assert len(result.verified_bundle.repair_patch.additions) == 1
    assert result.map_handoff is not None and len(result.map_handoff.concepts) == 1


def test_ten_hour_style_evidence_runs_in_bounded_batches() -> None:
    bundle = _bundle([f"ten-hour synthetic evidence {index}" for index in range(3600)])
    result = _run(
        bundle,
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(
            lambda payload, _index: {
                "attempt_id": payload["attempt_id"],
                "missed_candidates": [],
                "item_findings": [],
            }
        ),
        config=_config(
            inventory_batch=64,
            verification_batch=64,
            max_characters=24_000,
        ),
    )

    assert result.status is PipelineStatus.COMPLETE
    assert result.receipt.total_evidence_count == 3600
    assert result.receipt.processed_evidence_count == 3600
    assert result.verified_bundle.inventory.batch_count == 57
    assert result.verified_bundle.verification.coverage.batch_count == 57
    assert result.receipt.total_batches == 114
    assert result.receipt.provider_calls == 114


def test_large_concept_count_preserves_all_unique_concepts() -> None:
    count = 1000
    bundle = _bundle([f"independent concept evidence {index}" for index in range(count)])
    name_by_id = {
        segment.segment_id: f"Unique Concept {index}"
        for index, segment in enumerate(bundle.segments)
    }

    def inventory_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "batch_id": payload["batch_id"],
            "items": [
                _candidate("candidate-" + evidence_id, name_by_id[evidence_id], [evidence_id])
                for evidence_id in payload["core_segment_ids"]
            ],
        }

    result = _run(
        bundle,
        StructuredFixtureProvider(inventory_handler),
        StructuredVerificationFake(_covered_response),
        config=_config(
            inventory_batch=64,
            verification_batch=64,
            max_characters=24_000,
        ),
    )

    assert result.status is PipelineStatus.COMPLETE
    assert len(result.verified_bundle.inventory.items) == count
    assert result.map_handoff is not None
    assert len(result.map_handoff.concepts) == count
    assert len({item.stable_concept_id for item in result.map_handoff.concepts}) == count


def test_fixture_and_product_operational_metric_labels_are_explicitly_distinct() -> None:
    fixture = label_fixture_metrics(
        {"critical_recall": 1.0, "overall_recall": 1.0, "precision": 1.0}
    )
    bundle = _bundle(["operational metric evidence"])
    result = _run(
        bundle,
        StructuredFixtureProvider(_empty_inventory),
        StructuredVerificationFake(
            lambda payload, _index: {
                "attempt_id": payload["attempt_id"],
                "missed_candidates": [],
                "item_findings": [],
            }
        ),
    )
    operational = operational_metrics(result.receipt)

    assert fixture.scope is PipelineMetricScope.OFFLINE_FIXTURE
    assert "not a product accuracy" in fixture.label
    assert fixture.accuracy_claim_allowed is False
    assert operational.scope is PipelineMetricScope.PRODUCT_OPERATIONAL_OBSERVATION
    assert "recall and precision are not inferred" in operational.label
    assert "critical_recall" not in operational.metrics
    assert operational.accuracy_claim_allowed is False
