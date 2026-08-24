from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .models import (
    KnowledgeInventory,
    KnowledgeInventoryItem,
    KnowledgeType,
    canonical_semantic_key,
)


EvaluationKey = tuple[str, KnowledgeType]


@dataclass(frozen=True)
class GoldKnowledgeExpectation:
    canonical_name: str
    knowledge_type: KnowledgeType
    critical: bool = False

    def __post_init__(self) -> None:
        if not canonical_semantic_key(self.canonical_name):
            raise ValueError("canonical_name is required")

    @property
    def evaluation_key(self) -> EvaluationKey:
        return canonical_semantic_key(self.canonical_name), self.knowledge_type


@dataclass(frozen=True)
class OfflineRecallMetrics:
    expected_items: int
    critical_items: int
    extracted_correct: int
    false_positive: int
    missed: int
    critical_recall: float
    overall_recall: float
    precision: float
    missed_keys: tuple[str, ...]
    false_positive_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_items": self.expected_items,
            "critical_items": self.critical_items,
            "extracted_correct": self.extracted_correct,
            "false_positive": self.false_positive,
            "missed": self.missed,
            "critical_recall": self.critical_recall,
            "overall_recall": self.overall_recall,
            "precision": self.precision,
            "missed_keys": list(self.missed_keys),
            "false_positive_keys": list(self.false_positive_keys),
        }


def _item_key(item: KnowledgeInventoryItem) -> EvaluationKey:
    return canonical_semantic_key(item.canonical_name), item.knowledge_type


def _display_key(key: EvaluationKey) -> str:
    return f"{key[1].value}:{key[0]}"


def evaluate_inventory(
    inventory: KnowledgeInventory | Iterable[KnowledgeInventoryItem],
    gold: Iterable[GoldKnowledgeExpectation],
) -> OfflineRecallMetrics:
    items = inventory.items if isinstance(inventory, KnowledgeInventory) else tuple(inventory)
    expected_by_key: dict[EvaluationKey, GoldKnowledgeExpectation] = {}
    for expectation in gold:
        if expectation.evaluation_key in expected_by_key:
            raise ValueError("gold fixture contains duplicate evaluation keys")
        expected_by_key[expectation.evaluation_key] = expectation

    expected = set(expected_by_key)
    critical = {
        key for key, expectation in expected_by_key.items() if expectation.critical
    }
    extracted = {_item_key(item) for item in items}
    correct = expected & extracted
    false_positive = extracted - expected
    missed = expected - extracted
    critical_correct = critical & extracted

    overall_recall = len(correct) / len(expected) if expected else 1.0
    critical_recall = len(critical_correct) / len(critical) if critical else 1.0
    precision = len(correct) / len(extracted) if extracted else (1.0 if not expected else 0.0)
    return OfflineRecallMetrics(
        expected_items=len(expected),
        critical_items=len(critical),
        extracted_correct=len(correct),
        false_positive=len(false_positive),
        missed=len(missed),
        critical_recall=critical_recall,
        overall_recall=overall_recall,
        precision=precision,
        missed_keys=tuple(sorted(_display_key(key) for key in missed)),
        false_positive_keys=tuple(
            sorted(_display_key(key) for key in false_positive)
        ),
    )
