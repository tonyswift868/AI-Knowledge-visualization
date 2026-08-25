from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, Callable

import pytest

from app.knowledge.inventory.models import (
    CandidateRejection,
    CoverageLedger,
    EvidenceReference,
    InventoryStatus,
    KnowledgeInventory,
    KnowledgeInventoryItem,
    KnowledgeType,
    TeachingDepth,
    TimeRange,
    stable_item_id,
)
from app.knowledge.verification.batching import (
    VerificationBatchConfig,
    build_verification_plan,
)
from app.knowledge.verification.evidence import build_transcript_verification_evidence
from app.knowledge.verification.models import (
    CriticalConceptPolicy,
    CriticalConceptRule,
    MissClassification,
    SupportAnomalyState,
    SupportKnowledgeHint,
    VerificationEvidence,
    VerificationStatus,
)
from app.knowledge.verification.prompt import (
    build_verification_cache_identity,
    build_verification_prompt_envelope,
)
from app.knowledge.verification.repair import AnchorRecallRepair
from app.knowledge.verification.verifier import RecallVerifier, RecallVerifierConfig
from app.sources.video.transcript_normalizer import (
    GlossaryTerm,
    TerminologyGlossary,
    normalize_transcript,
)
from test_knowledge_inventory import _bundle


def _reference(segment) -> EvidenceReference:
    return EvidenceReference(
        segment_id=segment.segment_id,
        part_index=segment.part_index,
        part_cid=segment.part_cid,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        original_segment_hash=segment.segment_hash,
    )


def _inventory(
    bundle,
    specs: list[dict[str, Any]] | None = None,
) -> KnowledgeInventory:
    items = []
    for index, spec in enumerate(specs or []):
        segment = bundle.segments[int(spec.get("segment_index", index))]
        knowledge_type = KnowledgeType(spec.get("knowledge_type", "CONCEPT"))
        name = str(spec["name"])
        evidence_ref = spec.get("evidence_ref") or _reference(segment)
        items.append(
            KnowledgeInventoryItem(
                stable_item_id=stable_item_id(
                    source_revision=bundle.source_revision,
                    canonical_name=name,
                    knowledge_type=knowledge_type,
                    evidence_ids=[evidence_ref.segment_id],
                ),
                canonical_name=name,
                raw_names=tuple(spec.get("raw_names", ())),
                knowledge_type=knowledge_type,
                short_description=f"{name} inventory explanation",
                teaching_depth=TeachingDepth(spec.get("depth", "CORE_EXPLANATION")),
                confidence=float(spec.get("confidence", 0.9)),
                evidence_refs=(evidence_ref,),
                source_parts=(evidence_ref.part_index,),
                time_ranges=(
                    TimeRange(evidence_ref.part_index, evidence_ref.start_ms, evidence_ref.end_ms),
                ),
                candidate_prerequisites=(),
                mentioned_only=bool(spec.get("mentioned_only", False)),
                independently_teachable=bool(spec.get("independently_teachable", True)),
            )
        )
    return KnowledgeInventory(
        source_identity=bundle.source_identity,
        source_revision=bundle.source_revision,
        status=InventoryStatus.COMPLETE,
        items=tuple(items),
        coverage=CoverageLedger(
            total_segment_count=len(bundle.segments),
            covered_segment_ids=tuple(segment.segment_id for segment in bundle.segments),
            failed_segment_ids=(),
            low_confidence_ranges=(),
        ),
        failed_batches=(),
        rejected_candidates=tuple[CandidateRejection, ...](),
        batch_count=1,
        successful_batches=1,
        provider_calls=1,
    )


def _miss(
    candidate_id: str,
    name: str,
    evidence_ids: list[str],
    *,
    knowledge_type: str = "CONCEPT",
    raw_names: list[str] | None = None,
    depth: str = "CORE_EXPLANATION",
    confidence: float = 0.9,
    support_hint_ids: list[str] | None = None,
    mentioned_only: bool = False,
    independently_teachable: bool = True,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "canonical_name": name,
        "raw_names": list(raw_names or []),
        "knowledge_type": knowledge_type,
        "short_description": f"{name} is independently taught by the anchor evidence",
        "teaching_depth": depth,
        "confidence": confidence,
        "evidence_ids": evidence_ids,
        "reason": f"Anchor evidence teaches {name} but inventory omits it",
        "support_hint_ids": list(support_hint_ids or []),
        "mentioned_only": mentioned_only,
        "independently_teachable": independently_teachable,
    }


class StructuredVerificationFake:
    provider_name = "fake"
    model_name = "fake-verification-v1"

    def __init__(self, handler: Callable[[dict[str, Any], int], dict[str, Any]]):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    async def structured(self, messages, response_model, temperature=0.2):
        assert temperature == 0.0
        payload = json.loads(messages[1]["content"])["p2_batch_dynamic"]
        self.calls.append(payload)
        return response_model.model_validate(self.handler(payload, len(self.calls) - 1))


def _empty_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {"attempt_id": payload["attempt_id"], "missed_candidates": [], "item_findings": []}


def _run(bundle, inventory, provider, **kwargs):
    return asyncio.run(
        RecallVerifier(provider, config=kwargs.pop("config", RecallVerifierConfig())).verify_transcript(
            bundle,
            inventory,
            **kwargs,
        )
    )


def test_transcript_adapter_uses_normalized_view_but_preserves_original_evidence() -> None:
    bundle = _bundle(["hash map uses buckets"])
    normalization = asyncio.run(
        normalize_transcript(
            bundle,
            TerminologyGlossary((GlossaryTerm("HashMap", ("hash map",)),)),
        )
    )
    evidence = build_transcript_verification_evidence(bundle, normalization)

    assert evidence[0].original_text == "hash map uses buckets"
    assert evidence[0].normalized_text == "HashMap uses buckets"
    assert evidence[0].evidence_hash == bundle.segments[0].segment_hash


def test_critical_core_miss_creates_immutable_anchor_repair_patch() -> None:
    bundle = _bundle(["CAS atomically compares and updates a value."])
    inventory = _inventory(bundle)
    before = inventory.to_dict()
    policy = CriticalConceptPolicy((CriticalConceptRule("CAS", KnowledgeType.ALGORITHM),))

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss("cas-miss", "CAS", payload["core_evidence_ids"], knowledge_type="ALGORITHM")
            ],
            "item_findings": [],
        }

    result = _run(
        bundle,
        inventory,
        StructuredVerificationFake(handler),
        critical_policy=policy,
    )
    patch = AnchorRecallRepair().build_patch(inventory, result)

    assert result.verification_status is VerificationStatus.COMPLETE
    assert result.coverage.coverage_ratio == 1.0
    assert len(result.missed_candidates) == 1
    assert result.missed_candidates[0].critical is True
    assert result.diagnostics.missed_concept_classes == ("CRITICAL", "SINGLE_EVIDENCE_LONG_TAIL")
    assert len(patch.additions) == 1
    assert patch.additions[0].canonical_name == "CAS"
    assert inventory.to_dict() == before


def test_unknown_inventory_and_provider_evidence_are_rejected() -> None:
    bundle = _bundle(["grounded anchor text"])
    stale_ref = replace(_reference(bundle.segments[0]), segment_id="unknown-inventory-evidence")
    inventory = _inventory(bundle, [{"name": "Stale", "evidence_ref": stale_ref}])

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        assert payload["inventory_items"] == []
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [_miss("invented", "Invented", ["support-only-evidence"])],
            "item_findings": [],
        }

    result = _run(bundle, inventory, StructuredVerificationFake(handler))

    assert len(result.invalid_items) == 1
    assert result.missed_candidates == ()
    assert result.rejected_candidates[0].code == "UNKNOWN_ANCHOR_EVIDENCE"


def test_single_occurrence_long_tail_miss_survives_many_batches() -> None:
    bundle = _bundle([f"covered concept {index}" for index in range(100)] + ["StampedLock rare API"])
    inventory = _inventory(bundle)

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        rare = next(
            (item for item in payload["evidence"] if item["original_text"] == "StampedLock rare API"),
            None,
        )
        misses = (
            [_miss("tail", "StampedLock", [rare["evidence_id"]], knowledge_type="API")]
            if rare is not None
            else []
        )
        return {"attempt_id": payload["attempt_id"], "missed_candidates": misses, "item_findings": []}

    result = _run(
        bundle,
        inventory,
        StructuredVerificationFake(handler),
        config=RecallVerifierConfig(
            batching=VerificationBatchConfig(max_evidence=25, max_characters=4000),
        ),
    )

    assert {item.canonical_name for item in result.missed_candidates} == {"StampedLock"}
    assert result.coverage.coverage_ratio == 1.0
    assert result.diagnostics.batch_count == 5


def test_mention_only_false_positive_never_becomes_repair() -> None:
    bundle = _bundle(["This is a little like Redis, without teaching Redis."])
    inventory = _inventory(bundle)

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss(
                    "redis-mention",
                    "Redis",
                    payload["core_evidence_ids"],
                    knowledge_type="TERM",
                    depth="MENTION_ONLY",
                    mentioned_only=True,
                    independently_teachable=False,
                )
            ],
            "item_findings": [],
        }

    result = _run(bundle, inventory, StructuredVerificationFake(handler))
    patch = AnchorRecallRepair().build_patch(inventory, result)

    assert result.missed_candidates == ()
    assert result.rejected_candidates[0].code == "MENTION_ONLY_NOT_REPAIRABLE"
    assert patch.additions == ()


def test_failed_batch_isolation_produces_partial_and_never_complete() -> None:
    bundle = _bundle(["alpha", "beta", "gamma", "delta"])
    inventory = _inventory(bundle)

    def handler(payload: dict[str, Any], index: int) -> dict[str, Any]:
        if index == 1:
            raise TimeoutError("fixture timeout")
        return _empty_response(payload)

    result = _run(
        bundle,
        inventory,
        StructuredVerificationFake(handler),
        config=RecallVerifierConfig(
            batching=VerificationBatchConfig(max_evidence=2, max_characters=1000),
            max_concurrency=1,
            retry_failed_batches_once=False,
        ),
    )
    patch = AnchorRecallRepair().build_patch(inventory, result)

    assert result.verification_status is VerificationStatus.PARTIAL
    assert patch.verification_status is VerificationStatus.PARTIAL
    assert result.coverage.coverage_ratio == 0.5
    assert len(result.failed_batches) == 1
    assert len(result.diagnostics.failed_evidence_ranges) == 2


def test_all_failed_batches_produce_failed_status() -> None:
    bundle = _bundle(["alpha", "beta"])
    provider = StructuredVerificationFake(lambda _payload, _index: (_ for _ in ()).throw(TimeoutError()))
    result = _run(
        bundle,
        _inventory(bundle),
        provider,
        config=RecallVerifierConfig(retry_failed_batches_once=False),
    )

    assert result.verification_status is VerificationStatus.FAILED
    assert result.coverage.coverage_ratio == 0.0


def test_second_pass_retries_only_failed_batch_with_stable_identity() -> None:
    bundle = _bundle(["alpha", "beta", "gamma", "delta"])
    failed_core_id = bundle.segments[2].segment_id

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        if failed_core_id in payload["core_evidence_ids"] and payload["attempt"] == 1:
            raise TimeoutError("first pass only")
        return _empty_response(payload)

    provider = StructuredVerificationFake(handler)
    result = _run(
        bundle,
        _inventory(bundle),
        provider,
        config=RecallVerifierConfig(
            batching=VerificationBatchConfig(max_evidence=2, max_characters=1000),
            max_concurrency=1,
        ),
    )

    retry_calls = [call for call in provider.calls if call["attempt"] == 2]
    assert result.verification_status is VerificationStatus.COMPLETE
    assert result.diagnostics.retry_count == 1
    assert result.diagnostics.provider_calls == 3
    assert len(retry_calls) == 1
    assert retry_calls[0]["attempt_id"] != next(
        call["attempt_id"]
        for call in provider.calls
        if call["batch_id"] == retry_calls[0]["batch_id"] and call["attempt"] == 1
    )


def test_duplicate_alias_misses_and_repairs_collapse_across_batches() -> None:
    bundle = _bundle(["CAS is taught", "Compare-And-Swap is the expanded term"])

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        first = payload["evidence"][0]
        name = "CAS" if "CAS" in first["original_text"] else "Compare-And-Swap"
        alias = "Compare-And-Swap" if name == "CAS" else "CAS"
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss("candidate-" + name, name, [first["evidence_id"]], raw_names=[alias])
            ],
            "item_findings": [],
        }

    inventory = _inventory(bundle)
    result = _run(
        bundle,
        inventory,
        StructuredVerificationFake(handler),
        config=RecallVerifierConfig(
            batching=VerificationBatchConfig(max_evidence=1, max_characters=1000, overlap_evidence=0),
        ),
    )
    patch = AnchorRecallRepair().build_patch(inventory, result)

    assert len(result.missed_candidates) == 1
    assert len(patch.additions) == 1
    assert len(patch.additions[0].evidence_refs) == 2
    assert patch.additions[0].stable_candidate_id == result.missed_candidates[0].stable_candidate_id
    assert {patch.additions[0].canonical_name, *patch.additions[0].raw_names} == {
        "CAS",
        "Compare-And-Swap",
    }


def test_support_hint_cannot_create_anchor_fact_without_anchor_evidence() -> None:
    bundle = _bundle(["anchor says nothing about StampedLock"])
    hint = SupportKnowledgeHint(
        hint_id="support-stamped-lock",
        canonical_name="StampedLock",
        knowledge_type=KnowledgeType.API,
        support_source_identity="support:course",
        support_evidence_ids=("support-evidence-1",),
        confidence=0.9,
    )

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss(
                    "support-only",
                    "StampedLock",
                    ["support-evidence-1"],
                    knowledge_type="API",
                    support_hint_ids=[hint.hint_id],
                )
            ],
            "item_findings": [],
        }

    result = _run(
        bundle,
        _inventory(bundle),
        StructuredVerificationFake(handler),
        support_hints=(hint,),
    )

    assert result.missed_candidates == ()
    assert result.rejected_candidates[0].code == "UNKNOWN_ANCHOR_EVIDENCE"
    anomaly = result.diagnostics.suspicious_support_anomalies[0]
    assert anomaly.state is SupportAnomalyState.POSSIBLE_ANCHOR_MISS_UNCONFIRMED
    assert "OUT_OF_SCOPE" not in json.dumps(result.to_dict())


def test_support_triggered_anchor_evidence_is_possible_anchor_miss() -> None:
    bundle = _bundle(["StampedLock provides optimistic read validation."])
    hint = SupportKnowledgeHint(
        hint_id="support-stamped-lock",
        canonical_name="StampedLock",
        knowledge_type=KnowledgeType.API,
        support_source_identity="support:course",
        support_evidence_ids=("support-evidence-1",),
        confidence=0.9,
    )

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss(
                    "support-triggered",
                    "StampedLock",
                    payload["core_evidence_ids"],
                    knowledge_type="API",
                    support_hint_ids=[hint.hint_id],
                )
            ],
            "item_findings": [],
        }

    result = _run(
        bundle,
        _inventory(bundle),
        StructuredVerificationFake(handler),
        support_hints=(hint,),
    )

    assert result.missed_candidates[0].classification is MissClassification.POSSIBLE_ANCHOR_MISS
    assert result.diagnostics.suspicious_support_anomalies[0].state is SupportAnomalyState.POSSIBLE_ANCHOR_MISS


def test_authoritative_inventory_alias_prevents_false_miss_and_support_anomaly() -> None:
    bundle = _bundle(["CAS atomically updates a value."])
    inventory = _inventory(
        bundle,
        [{"name": "Compare-And-Swap", "raw_names": ("CAS",)}],
    )
    hint = SupportKnowledgeHint(
        hint_id="support-cas",
        canonical_name="CAS",
        knowledge_type=KnowledgeType.CONCEPT,
        support_source_identity="support:course",
        support_evidence_ids=("support-cas-evidence",),
        confidence=0.9,
    )

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss(
                    "cas-false-miss",
                    "CAS",
                    payload["core_evidence_ids"],
                    support_hint_ids=[hint.hint_id],
                )
            ],
            "item_findings": [],
        }

    result = _run(
        bundle,
        inventory,
        StructuredVerificationFake(handler),
        support_hints=(hint,),
    )
    patch = AnchorRecallRepair().build_patch(inventory, result)

    assert result.missed_candidates == ()
    assert result.rejected_candidates[0].code == "ALREADY_IN_INVENTORY"
    assert result.diagnostics.suspicious_support_anomalies == ()
    assert patch.additions == ()


def test_unsupported_and_low_confidence_findings_remain_distinct() -> None:
    bundle = _bundle(["HashMap uses buckets"])
    inventory = _inventory(bundle, [{"name": "HashMap", "confidence": 0.2}])
    item = inventory.items[0]

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [],
            "item_findings": [
                {
                    "inventory_item_id": item.stable_item_id,
                    "status": "UNSUPPORTED",
                    "evidence_ids": [bundle.segments[0].segment_id],
                    "reason": "fixture asks for stronger support",
                    "confidence": 0.4,
                }
            ],
        }

    result = _run(bundle, inventory, StructuredVerificationFake(handler))

    assert len(result.unsupported_items) == 1
    assert len(result.low_confidence) == 1
    assert result.unsupported_items[0].inventory_item_id == result.low_confidence[0].reference_id


def test_generic_evidence_contract_is_supported() -> None:
    evidence = (
        VerificationEvidence(
            evidence_id="generic-1",
            source_identity="document:one",
            source_revision="revision-1",
            source_kind="DOCUMENT_CHUNK",
            part_key="page:1",
            part_index=None,
            part_cid=None,
            start_ms=None,
            end_ms=None,
            original_text="A B+ tree keeps database indexes balanced.",
            normalized_text="A B+ tree keeps database indexes balanced.",
            evidence_hash="ab" * 32,
        ),
    )
    bundle = _bundle(["placeholder"])
    inventory = replace(
        _inventory(bundle),
        source_identity="document:one",
        source_revision="revision-1",
        coverage=CoverageLedger(1, ("generic-1",), (), ()),
    )

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": [
                _miss("btree", "B+ Tree", ["generic-1"], knowledge_type="ALGORITHM")
            ],
            "item_findings": [],
        }

    provider = StructuredVerificationFake(handler)
    result = asyncio.run(
        RecallVerifier(provider).verify(
            evidence,
            inventory,
            source_identity="document:one",
            source_revision="revision-1",
        )
    )

    assert result.verification_status is VerificationStatus.COMPLETE
    assert result.missed_candidates[0].canonical_name == "B+ Tree"


def test_prompt_prefix_cache_and_retry_identities_are_deterministic() -> None:
    bundle = _bundle(["alpha", "beta", "gamma"])
    inventory = _inventory(bundle)
    evidence = build_transcript_verification_evidence(bundle)
    plan = build_verification_plan(
        evidence,
        inventory,
        valid_inventory_item_ids=(),
        config=VerificationBatchConfig(max_evidence=2, max_characters=1000),
    )
    envelopes = [build_verification_prompt_envelope(batch, attempt=1) for batch in plan.batches]

    assert len({envelope.stable_prefix_hash for envelope in envelopes}) == 1
    first = plan.batches[0]
    assert first.attempt_id(1) == first.attempt_id(1)
    assert first.attempt_id(1) != first.attempt_id(2)
    identity_1 = build_verification_cache_identity(
        first,
        envelopes[0],
        attempt=1,
        provider_family="fake",
        model="fixture",
    )
    envelope_2 = build_verification_prompt_envelope(first, attempt=2)
    identity_2 = build_verification_cache_identity(
        first,
        envelope_2,
        attempt=2,
        provider_family="fake",
        model="fixture",
    )
    assert identity_1.task_type == "knowledge_verify"
    assert identity_1.cache_key != identity_2.cache_key


def test_invalid_structured_output_fails_safe_after_bounded_retry() -> None:
    bundle = _bundle(["alpha"])

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        return {"attempt_id": payload["attempt_id"], "missed_candidates": "bad", "extra": True}

    provider = StructuredVerificationFake(handler)
    result = _run(bundle, _inventory(bundle), provider)

    assert result.verification_status is VerificationStatus.FAILED
    assert len(provider.calls) == 2
    assert result.failed_batches[0].attempts == 2


def test_source_revision_mismatch_stops_before_provider_call() -> None:
    bundle = _bundle(["alpha"])
    inventory = replace(_inventory(bundle), source_revision="wrong")
    provider = StructuredVerificationFake(lambda payload, _index: _empty_response(payload))

    with pytest.raises(ValueError, match="revision"):
        _run(bundle, inventory, provider)
    assert provider.calls == []
