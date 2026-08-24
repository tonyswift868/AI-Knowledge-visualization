from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..inventory.models import KnowledgeType, canonical_semantic_key
from .models import VerificationMissedCandidate, VerificationResult


EvaluationKey = tuple[str, KnowledgeType]


@dataclass(frozen=True)
class GoldRecallExpectation:
    canonical_name: str
    knowledge_type: KnowledgeType
    critical: bool = False

    def __post_init__(self) -> None:
        if not canonical_semantic_key(self.canonical_name):
            raise ValueError("gold canonical_name is required")

    @property
    def key(self) -> EvaluationKey:
        return canonical_semantic_key(self.canonical_name), self.knowledge_type


@dataclass(frozen=True)
class VerificationFixtureMetrics:
    expected_misses: int
    critical_misses: int
    detected_correct: int
    false_positive: int
    missed: int
    critical_recall: float
    overall_recall: float
    precision: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_misses": self.expected_misses,
            "critical_misses": self.critical_misses,
            "detected_correct": self.detected_correct,
            "false_positive": self.false_positive,
            "missed": self.missed,
            "critical_recall": self.critical_recall,
            "overall_recall": self.overall_recall,
            "precision": self.precision,
        }


def _candidate_key(candidate: VerificationMissedCandidate) -> EvaluationKey:
    return canonical_semantic_key(candidate.canonical_name), candidate.knowledge_type


def evaluate_verification(
    verification: VerificationResult | Iterable[VerificationMissedCandidate],
    gold: Iterable[GoldRecallExpectation],
) -> VerificationFixtureMetrics:
    candidates = (
        verification.missed_candidates
        if isinstance(verification, VerificationResult)
        else tuple(verification)
    )
    expected_by_key: dict[EvaluationKey, GoldRecallExpectation] = {}
    for item in gold:
        if item.key in expected_by_key:
            raise ValueError("gold verification fixture contains duplicate keys")
        expected_by_key[item.key] = item
    expected = set(expected_by_key)
    critical = {key for key, item in expected_by_key.items() if item.critical}
    detected = {_candidate_key(candidate) for candidate in candidates}
    correct = expected & detected
    false_positive = detected - expected
    missed = expected - detected
    return VerificationFixtureMetrics(
        expected_misses=len(expected),
        critical_misses=len(critical),
        detected_correct=len(correct),
        false_positive=len(false_positive),
        missed=len(missed),
        critical_recall=(len(critical & detected) / len(critical) if critical else 1.0),
        overall_recall=(len(correct) / len(expected) if expected else 1.0),
        precision=(len(correct) / len(detected) if detected else (1.0 if not expected else 0.0)),
    )
