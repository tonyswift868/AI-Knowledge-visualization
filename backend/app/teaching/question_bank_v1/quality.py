from __future__ import annotations

from dataclasses import dataclass

from ...knowledge.inventory.models import stable_hash
from .coverage import ReviewCoverageGapCode, ReviewCoveragePlan
from .models import (
    QuestionCount,
    QuestionSourceTier,
    QuestionValidationStatus,
    TrustedQuestionBank,
)


@dataclass(frozen=True)
class QuestionBankQualityReport:
    report_id: str
    bank_id: str
    question_count: int
    concept_count: int
    trusted_question_count: int
    trusted_coverage_ratio: float
    tier_distribution: tuple[QuestionCount, ...]
    answer_conflict_count: int
    concept_gaps: tuple[str, ...]
    source_gaps: tuple[str, ...]
    duplicate_ratio: float
    critical_concept_count: int
    critical_covered_count: int
    critical_coverage_ratio: float

    def __post_init__(self) -> None:
        counts = (
            self.question_count,
            self.concept_count,
            self.trusted_question_count,
            self.answer_conflict_count,
            self.critical_concept_count,
            self.critical_covered_count,
        )
        if not self.report_id.strip() or not self.bank_id.strip():
            raise ValueError("question quality report identities are required")
        if min(counts) < 0:
            raise ValueError("question quality counts cannot be negative")
        for value in (
            self.trusted_coverage_ratio,
            self.duplicate_ratio,
            self.critical_coverage_ratio,
        ):
            if not 0 <= value <= 1:
                raise ValueError("question quality ratios must be between zero and one")
        if sum(item.count for item in self.tier_distribution) != self.question_count:
            raise ValueError("question quality tier distribution is inconsistent")

    def to_dict(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "bank_id": self.bank_id,
            "question_count": self.question_count,
            "concept_count": self.concept_count,
            "trusted_question_count": self.trusted_question_count,
            "trusted_coverage_ratio": self.trusted_coverage_ratio,
            "tier_distribution": [
                item.to_dict() for item in self.tier_distribution
            ],
            "answer_conflict_count": self.answer_conflict_count,
            "concept_gaps": list(self.concept_gaps),
            "source_gaps": list(self.source_gaps),
            "duplicate_ratio": self.duplicate_ratio,
            "critical_concept_count": self.critical_concept_count,
            "critical_covered_count": self.critical_covered_count,
            "critical_coverage_ratio": self.critical_coverage_ratio,
        }


class QuestionBankQualityReporter:
    def __init__(self, *, minimum_sources_per_concept: int = 2) -> None:
        if minimum_sources_per_concept < 1:
            raise ValueError("minimum source count must be positive")
        self.minimum_sources_per_concept = minimum_sources_per_concept

    def build(
        self,
        bank: TrustedQuestionBank,
        coverage_plan: ReviewCoveragePlan,
    ) -> QuestionBankQualityReport:
        if coverage_plan.bank_id != bank.bank_id:
            raise ValueError("quality report bank and coverage plan do not match")
        trusted = tuple(
            item
            for item in bank.questions
            if item.validation_status is QuestionValidationStatus.TRUSTED
        )
        trusted_concepts = {
            concept_id
            for item in trusted
            for concept_id in item.concept_ids
            if concept_id in coverage_plan.concept_scope
        }
        concept_count = len(coverage_plan.concept_scope)
        trusted_coverage_ratio = len(trusted_concepts) / concept_count
        critical = set(coverage_plan.critical_concept_ids)
        critical_covered = critical & trusted_concepts
        critical_coverage_ratio = (
            len(critical_covered) / len(critical) if critical else 1.0
        )
        concept_gaps = tuple(
            sorted(
                {
                    item.concept_id
                    for item in coverage_plan.gaps
                    if item.code
                    in {
                        ReviewCoverageGapCode.NO_TRUSTED_QUESTION,
                        ReviewCoverageGapCode.INSUFFICIENT_QUESTIONS,
                        ReviewCoverageGapCode.CRITICAL_CONCEPT_UNCOVERED,
                    }
                }
            )
        )
        source_gaps: list[str] = []
        for concept_id in coverage_plan.concept_scope:
            sources = {
                provenance.source_identity
                for question in trusted
                if concept_id in question.concept_ids
                for provenance in question.provenance
                if provenance.source_tier
                is not QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED
            }
            if len(sources) < self.minimum_sources_per_concept:
                source_gaps.append(concept_id)
        duplicate_ratio = (
            bank.receipt.collapsed_count / bank.receipt.input_count
            if bank.receipt.input_count
            else 0.0
        )
        payload = {
            "answer_conflict_count": len(bank.conflicts),
            "bank_id": bank.bank_id,
            "concept_gaps": concept_gaps,
            "critical_concept_count": len(critical),
            "critical_covered_count": len(critical_covered),
            "critical_coverage_ratio": critical_coverage_ratio,
            "duplicate_ratio": duplicate_ratio,
            "question_count": len(bank.questions),
            "source_gaps": source_gaps,
            "tier_distribution": [
                item.to_dict() for item in bank.receipt.tier_counts
            ],
            "trusted_coverage_ratio": trusted_coverage_ratio,
            "trusted_question_count": len(trusted),
        }
        return QuestionBankQualityReport(
            report_id=stable_hash("qbqr", payload),
            bank_id=bank.bank_id,
            question_count=len(bank.questions),
            concept_count=concept_count,
            trusted_question_count=len(trusted),
            trusted_coverage_ratio=trusted_coverage_ratio,
            tier_distribution=bank.receipt.tier_counts,
            answer_conflict_count=len(bank.conflicts),
            concept_gaps=concept_gaps,
            source_gaps=tuple(source_gaps),
            duplicate_ratio=duplicate_ratio,
            critical_concept_count=len(critical),
            critical_covered_count=len(critical_covered),
            critical_coverage_ratio=critical_coverage_ratio,
        )


__all__ = [
    "QuestionBankQualityReport",
    "QuestionBankQualityReporter",
]
