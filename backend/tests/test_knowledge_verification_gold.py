from __future__ import annotations

import asyncio
import time
import tracemalloc
from dataclasses import replace
from typing import Any

import pytest

from app.knowledge.inventory.models import KnowledgeType
from app.knowledge.verification.batching import VerificationBatchConfig, build_verification_plan
from app.knowledge.verification.evidence import build_transcript_verification_evidence
from app.knowledge.verification.metrics import GoldRecallExpectation, evaluate_verification
from app.knowledge.verification.models import CriticalConceptPolicy, CriticalConceptRule
from app.knowledge.verification.repair import AnchorRecallRepair
from app.knowledge.verification.verifier import RecallVerifier
from test_knowledge_inventory import _bundle
from test_knowledge_inventory_gold import GOLD_FIXTURE
from test_knowledge_verification import (
    StructuredVerificationFake,
    _inventory,
    _miss,
)


GOLD_INVENTORY_COUNT = 30
GOLD_MISSES = GOLD_FIXTURE[GOLD_INVENTORY_COUNT:]


def _gold_bundle():
    return _bundle(
        [f"The anchor lesson teaches {row.name} with independent evidence." for row in GOLD_FIXTURE]
    )


def _gold_inventory(bundle):
    return _inventory(
        bundle,
        [
            {
                "name": row.name,
                "knowledge_type": row.knowledge_type.value,
                "raw_names": row.aliases,
                "segment_index": index,
            }
            for index, row in enumerate(GOLD_FIXTURE[:GOLD_INVENTORY_COUNT])
        ],
    )


def _gold_handler(bundle):
    row_by_evidence_id = {
        segment.segment_id: GOLD_FIXTURE[index]
        for index, segment in enumerate(bundle.segments)
    }

    def handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        findings = [
            {
                "inventory_item_id": item["stable_item_id"],
                "status": "COVERED",
                "evidence_ids": item["evidence_ids"],
                "reason": "fixture evidence supports this inventory item",
                "confidence": 0.99,
            }
            for item in payload["inventory_items"]
        ]
        misses = []
        for evidence_id in payload["core_evidence_ids"]:
            row = row_by_evidence_id[evidence_id]
            if row not in GOLD_MISSES:
                continue
            misses.append(
                _miss(
                    "gold-" + evidence_id,
                    row.name,
                    [evidence_id],
                    knowledge_type=row.knowledge_type.value,
                    raw_names=list(row.aliases),
                    depth=row.depth.value,
                    confidence=0.99 if row.critical else 0.9,
                )
            )
        return {
            "attempt_id": payload["attempt_id"],
            "missed_candidates": misses,
            "item_findings": findings,
        }

    return handler


def test_40_item_gold_fixture_detects_all_10_anchor_misses() -> None:
    bundle = _gold_bundle()
    inventory = _gold_inventory(bundle)
    provider = StructuredVerificationFake(_gold_handler(bundle))
    critical_policy = CriticalConceptPolicy(
        tuple(
            CriticalConceptRule(row.name, row.knowledge_type, row.aliases)
            for row in GOLD_MISSES
            if row.critical
        )
    )
    result = asyncio.run(
        RecallVerifier(provider).verify_transcript(
            bundle,
            inventory,
            critical_policy=critical_policy,
        )
    )
    metrics = evaluate_verification(
        result,
        (
            GoldRecallExpectation(row.name, row.knowledge_type, row.critical)
            for row in GOLD_MISSES
        ),
    )
    patch = AnchorRecallRepair().build_patch(inventory, result)

    assert len(GOLD_FIXTURE) == 40
    assert metrics.expected_misses == 10
    assert metrics.critical_misses == 2
    assert metrics.detected_correct == 10
    assert metrics.false_positive == 0
    assert metrics.missed == 0
    assert metrics.critical_recall >= 0.98
    assert metrics.overall_recall >= 0.95
    assert metrics.precision >= 0.95
    assert len(result.covered) == 30
    assert len(result.missed_candidates) == 10
    assert len(patch.additions) == 10
    assert result.coverage.coverage_ratio == 1.0
    assert len(provider.calls) == 1


def test_verification_metrics_count_one_false_positive_and_one_miss() -> None:
    bundle = _gold_bundle()
    inventory = _gold_inventory(bundle)
    result = asyncio.run(
        RecallVerifier(StructuredVerificationFake(_gold_handler(bundle))).verify_transcript(
            bundle,
            inventory,
        )
    )
    replacement = replace(
        result.missed_candidates[-1],
        stable_candidate_id="kivc-fixture-false-positive",
        canonical_name="Not In Gold",
    )
    changed = replace(
        result,
        missed_candidates=result.missed_candidates[:-1] + (replacement,),
    )
    metrics = evaluate_verification(
        changed,
        (
            GoldRecallExpectation(row.name, row.knowledge_type, row.critical)
            for row in GOLD_MISSES
        ),
    )

    assert metrics.detected_correct == 9
    assert metrics.false_positive == 1
    assert metrics.missed == 1
    assert metrics.overall_recall == pytest.approx(0.9)
    assert metrics.precision == pytest.approx(0.9)


def test_10h_representative_verification_plan_is_bounded_without_provider_calls() -> None:
    bundle = _bundle([f"10h evidence {index} teaches concept {index}" for index in range(3600)])
    inventory = _inventory(bundle)
    evidence = build_transcript_verification_evidence(bundle)

    tracemalloc.start()
    started = time.perf_counter()
    plan = build_verification_plan(
        evidence,
        inventory,
        valid_inventory_item_ids=(),
        config=VerificationBatchConfig(max_evidence=64, max_characters=24_000),
    )
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert plan.total_evidence_count == 3600
    assert len(plan.batches) == 57
    assert [evidence_id for batch in plan.batches for evidence_id in batch.core_evidence_ids] == [
        item.evidence_id for item in evidence
    ]
    assert elapsed < 10.0
    assert peak < 128 * 1024 * 1024
