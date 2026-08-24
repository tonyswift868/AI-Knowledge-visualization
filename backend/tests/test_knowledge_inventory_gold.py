from __future__ import annotations

import asyncio
import time
import tracemalloc
from dataclasses import dataclass, replace
from typing import Any

import pytest

from app.knowledge.inventory.batching import BatchingConfig, build_extraction_plan
from app.knowledge.inventory.extractor import extract_knowledge_inventory
from app.knowledge.inventory.metrics import GoldKnowledgeExpectation, evaluate_inventory
from app.knowledge.inventory.models import KnowledgeType, TeachingDepth
from test_knowledge_inventory import (
    StructuredFixtureProvider,
    _bundle,
    _candidate,
    _metadata,
)


@dataclass(frozen=True)
class GoldRow:
    name: str
    knowledge_type: KnowledgeType
    critical: bool = False
    depth: TeachingDepth = TeachingDepth.CORE_EXPLANATION
    mentioned_only: bool = False
    independently_teachable: bool = True
    aliases: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()


GOLD_FIXTURE = (
    GoldRow("JVM", KnowledgeType.CONCEPT, True, aliases=("Java Virtual Machine",)),
    GoldRow("Bytecode", KnowledgeType.TERM, True),
    GoldRow("ClassLoader", KnowledgeType.MECHANISM, True, prerequisites=("JVM",)),
    GoldRow("Heap", KnowledgeType.CONCEPT),
    GoldRow("Garbage Collection", KnowledgeType.ALGORITHM, True, aliases=("GC",)),
    GoldRow("HashMap", KnowledgeType.MECHANISM, True, aliases=("hash map",)),
    GoldRow("Load Factor", KnowledgeType.FORMULA),
    GoldRow("Resize Threshold", KnowledgeType.RULE),
    GoldRow("CAS", KnowledgeType.ALGORITHM, True, aliases=("Compare-And-Swap",)),
    GoldRow("AQS", KnowledgeType.MECHANISM, True),
    GoldRow("volatile", KnowledgeType.RULE),
    GoldRow("StampedLock", KnowledgeType.API, depth=TeachingDepth.BRIEF_EXPLANATION),
    GoldRow("CompletableFuture", KnowledgeType.API),
    GoldRow("Double-Checked Locking", KnowledgeType.CODE_PATTERN),
    GoldRow("Relational Model", KnowledgeType.CONCEPT, True),
    GoldRow("ACID", KnowledgeType.RULE, True),
    GoldRow("Transaction Isolation", KnowledgeType.CONCEPT, True),
    GoldRow("MVCC", KnowledgeType.MECHANISM, True),
    GoldRow("B+ Tree", KnowledgeType.ALGORITHM, True),
    GoldRow("Covering Index", KnowledgeType.BEST_PRACTICE),
    GoldRow("MySQL EXPLAIN", KnowledgeType.API),
    GoldRow("SQL Injection", KnowledgeType.WARNING, True),
    GoldRow(
        "Redis",
        KnowledgeType.TERM,
        depth=TeachingDepth.MENTION_ONLY,
        mentioned_only=True,
        independently_teachable=False,
    ),
    GoldRow("RDB Snapshot", KnowledgeType.MECHANISM),
    GoldRow("AOF", KnowledgeType.MECHANISM),
    GoldRow("Cache Penetration", KnowledgeType.LIMITATION),
    GoldRow("Distributed Lock", KnowledgeType.CODE_PATTERN),
    GoldRow("OSI Model", KnowledgeType.PREREQUISITE_HINT, prerequisites=("Networking Basics",)),
    GoldRow("TCP", KnowledgeType.CONCEPT, True),
    GoldRow("Three-Way Handshake", KnowledgeType.MECHANISM, True),
    GoldRow("Four-Way Close", KnowledgeType.MECHANISM),
    GoldRow("TIME_WAIT", KnowledgeType.TERM),
    GoldRow("Congestion Control", KnowledgeType.ALGORITHM, True),
    GoldRow("HTTP", KnowledgeType.CONCEPT, True),
    GoldRow("HTTP/2 Multiplexing", KnowledgeType.MECHANISM),
    GoldRow("TLS Background", KnowledgeType.PREREQUISITE_HINT),
    GoldRow("DNS Resolution", KnowledgeType.MECHANISM),
    GoldRow("REST Resource Design", KnowledgeType.BEST_PRACTICE),
    GoldRow("Idempotency", KnowledgeType.RULE),
    GoldRow("HTTP Retry Example", KnowledgeType.EXAMPLE, depth=TeachingDepth.PRACTICE_DEMONSTRATED),
)


def _gold_bundle():
    rows = []
    for row in GOLD_FIXTURE:
        if row.name == "Redis":
            rows.append("This component is a little like Redis; Redis itself is not explained.")
        elif row.name == "StampedLock":
            rows.append("StampedLock is a single-occurrence API with an independently useful explanation.")
        else:
            rows.append(f"The lesson teaches {row.name} with evidence and a concise explanation.")
    return _bundle(rows)


def _gold_provider_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
    core = [item for item in payload["evidence"] if item["is_core"]]
    items = []
    for index, (row, evidence) in enumerate(zip(GOLD_FIXTURE, core, strict=True)):
        candidate = _candidate(
            f"gold-{index}",
            row.name,
            [evidence["segment_id"]],
            knowledge_type=row.knowledge_type.value,
            raw_names=list(row.aliases),
            depth=row.depth.value,
            confidence=0.99 if row.critical else 0.9,
            mentioned_only=row.mentioned_only,
            independently_teachable=row.independently_teachable,
        )
        candidate["candidate_prerequisites"] = list(row.prerequisites)
        items.append(candidate)
    return {"batch_id": payload["batch_id"], "items": items}


def test_40_item_cross_domain_gold_fixture_meets_offline_development_gates() -> None:
    bundle = _gold_bundle()
    provider = StructuredFixtureProvider(_gold_provider_handler)
    inventory = asyncio.run(
        extract_knowledge_inventory(
            bundle,
            source_metadata=_metadata(bundle),
            provider=provider,
        )
    )
    gold = tuple(
        GoldKnowledgeExpectation(row.name, row.knowledge_type, row.critical)
        for row in GOLD_FIXTURE
    )
    metrics = evaluate_inventory(inventory, gold)

    assert len(GOLD_FIXTURE) == 40
    assert {row.knowledge_type for row in GOLD_FIXTURE} == set(KnowledgeType)
    assert metrics.expected_items == 40
    assert metrics.critical_items == 17
    assert metrics.extracted_correct == 40
    assert metrics.false_positive == 0
    assert metrics.missed == 0
    assert metrics.critical_recall >= 0.98
    assert metrics.overall_recall >= 0.95
    assert metrics.precision >= 0.95
    redis = next(item for item in inventory.items if item.canonical_name == "Redis")
    assert redis.mentioned_only is True
    assert redis.independently_teachable is False
    assert next(item for item in inventory.items if item.canonical_name == "StampedLock")
    assert len(provider.calls) == 1


def test_offline_metrics_count_one_miss_and_one_false_positive() -> None:
    bundle = _gold_bundle()
    inventory = asyncio.run(
        extract_knowledge_inventory(
            bundle,
            source_metadata=_metadata(bundle),
            provider=StructuredFixtureProvider(_gold_provider_handler),
        )
    )
    replacement = replace(
        inventory.items[-1],
        stable_item_id="ki-fixture-false-positive",
        canonical_name="Not In Gold",
    )
    changed = replace(inventory, items=inventory.items[:-1] + (replacement,))
    metrics = evaluate_inventory(
        changed,
        (
            GoldKnowledgeExpectation(row.name, row.knowledge_type, row.critical)
            for row in GOLD_FIXTURE
        ),
    )

    assert metrics.extracted_correct == 39
    assert metrics.false_positive == 1
    assert metrics.missed == 1
    assert metrics.overall_recall == pytest.approx(39 / 40)
    assert metrics.precision == pytest.approx(39 / 40)


@pytest.mark.parametrize(
    ("scale", "segment_count", "part_count"),
    [
        ("3h-representative", 1080, 1),
        ("10h-representative", 3600, 1),
        ("1000P-representative", 2000, 1000),
    ],
)
def test_synthetic_batching_performance_is_bounded(
    scale: str,
    segment_count: int,
    part_count: int,
) -> None:
    if part_count == 1:
        rows: list[str | tuple[str, int]] = [
            f"{scale} segment {index} teaches concept {index}"
            for index in range(segment_count)
        ]
    else:
        rows = [
            (f"{scale} part {part_index} segment {ordinal}", part_index)
            for part_index in range(1, part_count + 1)
            for ordinal in range(segment_count // part_count)
        ]
    bundle = _bundle(rows)

    tracemalloc.start()
    started = time.perf_counter()
    plan = build_extraction_plan(
        bundle,
        source_metadata=_metadata(bundle),
        config=BatchingConfig(max_segments=64, max_characters=24_000),
    )
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert plan.total_segment_count == segment_count
    assert [segment_id for batch in plan.batches for segment_id in batch.core_segment_ids] == [
        segment.segment_id for segment in bundle.segments
    ]
    assert len(plan.batches) < segment_count
    assert elapsed < 10.0
    assert peak < 128 * 1024 * 1024
