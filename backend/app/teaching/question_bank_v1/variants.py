from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Literal, Protocol

from ...knowledge.inventory.models import stable_hash
from .models import (
    DIFFICULTY_RANK,
    QuestionSourceTier,
    QuestionValidationStatus,
    TrustedQuestion,
)


class VariantValidationStatus(str, Enum):
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class QuestionVariantCandidate:
    parent_question: TrustedQuestion
    proposed_question: TrustedQuestion
    generator_identity: str
    semantic_target_unchanged: bool
    answer_remains_valid: bool
    evidence_still_supports: bool

    def __post_init__(self) -> None:
        if not self.generator_identity.strip():
            raise ValueError("variant generator identity is required")
        if self.parent_question.question_id == self.proposed_question.question_id:
            raise ValueError("variant must differ from its parent question")


class QuestionVariantGeneratorPort(Protocol):
    def generate_variants(
        self,
        parent: TrustedQuestion,
    ) -> Iterable[QuestionVariantCandidate]: ...


@dataclass(frozen=True)
class VariantValidationCheck:
    name: str
    passed: bool

    def to_dict(self) -> dict[str, str | bool]:
        return {"name": self.name, "passed": self.passed}


@dataclass(frozen=True)
class VariantValidationResult:
    result_id: str
    parent_question_id: str
    variant_question_id: str
    status: VariantValidationStatus
    checks: tuple[VariantValidationCheck, ...]

    def __post_init__(self) -> None:
        if not self.result_id.strip():
            raise ValueError("variant validation result ID is required")
        if not self.checks or len({item.name for item in self.checks}) != len(
            self.checks
        ):
            raise ValueError("variant validation checks must be non-empty and unique")
        all_passed = all(item.passed for item in self.checks)
        if (self.status is VariantValidationStatus.VALIDATED) != all_passed:
            raise ValueError("variant validation status must match its checks")

    def to_dict(self) -> dict[str, object]:
        return {
            "result_id": self.result_id,
            "parent_question_id": self.parent_question_id,
            "variant_question_id": self.variant_question_id,
            "status": self.status.value,
            "checks": [item.to_dict() for item in self.checks],
        }


@dataclass(frozen=True)
class QuestionVariantBatch:
    batch_id: str
    results: tuple[VariantValidationResult, ...]
    accepted_questions: tuple[TrustedQuestion, ...]
    provider_calls: Literal[0] = 0

    def __post_init__(self) -> None:
        accepted_ids = tuple(item.question_id for item in self.accepted_questions)
        if len(accepted_ids) != len(set(accepted_ids)):
            raise ValueError("accepted variant question IDs must be unique")
        validated_ids = {
            item.variant_question_id
            for item in self.results
            if item.status is VariantValidationStatus.VALIDATED
        }
        if set(accepted_ids) != validated_ids:
            raise ValueError("accepted variants must match validated results")

    def to_dict(self) -> dict[str, object]:
        return {
            "batch_id": self.batch_id,
            "results": [item.to_dict() for item in self.results],
            "accepted_question_ids": [
                item.question_id for item in self.accepted_questions
            ],
            "provider_calls": self.provider_calls,
        }


class VariantValidationPolicy:
    def __init__(self, *, maximum_difficulty_shift: int = 1) -> None:
        if maximum_difficulty_shift < 0:
            raise ValueError("maximum difficulty shift cannot be negative")
        self.maximum_difficulty_shift = maximum_difficulty_shift

    def validate(
        self,
        candidate: QuestionVariantCandidate,
    ) -> VariantValidationResult:
        parent = candidate.parent_question
        proposed = candidate.proposed_question
        parent_answer_evidence = {
            evidence.identity_key
            for provenance in parent.provenance
            for evidence in provenance.verified_answer_evidence
        }
        proposed_answer_evidence = {
            evidence.identity_key
            for provenance in proposed.provenance
            for evidence in provenance.verified_answer_evidence
        }
        proposed_answer_evidence_ids = {
            evidence.evidence_id
            for provenance in proposed.provenance
            for evidence in provenance.verified_answer_evidence
        }
        parent_question_evidence = {
            evidence.identity_key
            for provenance in parent.provenance
            for evidence in provenance.verified_question_evidence
        }
        proposed_question_evidence = {
            evidence.identity_key
            for provenance in proposed.provenance
            for evidence in provenance.verified_question_evidence
        }
        lineage_valid = any(
            provenance.source_tier
            is QuestionSourceTier.T2_VERIFIED_VARIANT
            and provenance.variant_of_question_id == parent.question_id
            for provenance in proposed.provenance
        )
        answer_evidence_supported = bool(
            parent_answer_evidence & proposed_answer_evidence
        ) and set(proposed.answer_key.answer_evidence_ids) <= (
            proposed_answer_evidence_ids
        )
        question_evidence_supported = bool(
            parent_question_evidence & proposed_question_evidence
        )
        difficulty_shift = abs(
            DIFFICULTY_RANK[parent.difficulty]
            - DIFFICULTY_RANK[proposed.difficulty]
        )
        checks = (
            VariantValidationCheck(
                "PARENT_TRUSTED",
                parent.validation_status is QuestionValidationStatus.TRUSTED,
            ),
            VariantValidationCheck(
                "T2_TIER",
                proposed.source_tier
                is QuestionSourceTier.T2_VERIFIED_VARIANT,
            ),
            VariantValidationCheck(
                "T2_NOT_DIRECTLY_TRUSTED",
                proposed.validation_status
                is QuestionValidationStatus.VALIDATED,
            ),
            VariantValidationCheck("LINEAGE_VALID", lineage_valid),
            VariantValidationCheck(
                "SEMANTIC_TARGET_UNCHANGED",
                candidate.semantic_target_unchanged
                and parent.fingerprint.canonical_concept_ids
                == proposed.fingerprint.canonical_concept_ids,
            ),
            VariantValidationCheck(
                "ANSWER_REMAINS_VALID",
                candidate.answer_remains_valid
                and parent.answer_key.canonical_answer_signature
                == proposed.answer_key.canonical_answer_signature,
            ),
            VariantValidationCheck(
                "DIFFICULTY_SHIFT_BOUNDED",
                difficulty_shift <= self.maximum_difficulty_shift,
            ),
            VariantValidationCheck(
                "ANSWER_EVIDENCE_STILL_SUPPORTS",
                candidate.evidence_still_supports
                and answer_evidence_supported,
            ),
            VariantValidationCheck(
                "QUESTION_EVIDENCE_STILL_SUPPORTS",
                candidate.evidence_still_supports
                and question_evidence_supported,
            ),
        )
        status = (
            VariantValidationStatus.VALIDATED
            if all(item.passed for item in checks)
            else VariantValidationStatus.REJECTED
        )
        payload = {
            "checks": [item.to_dict() for item in checks],
            "generator_identity": candidate.generator_identity,
            "parent_question_id": parent.question_id,
            "variant_question_id": proposed.question_id,
        }
        return VariantValidationResult(
            result_id=stable_hash("vvr", payload),
            parent_question_id=parent.question_id,
            variant_question_id=proposed.question_id,
            status=status,
            checks=checks,
        )


class SafeQuestionVariantPipeline:
    """Injected-generator pipeline with a hard-coded zero Provider-call contract."""

    def __init__(
        self,
        generator: QuestionVariantGeneratorPort,
        *,
        policy: VariantValidationPolicy | None = None,
    ) -> None:
        self.generator = generator
        self.policy = policy or VariantValidationPolicy()

    def run(
        self,
        parents: Iterable[TrustedQuestion],
    ) -> QuestionVariantBatch:
        candidates = tuple(
            candidate
            for parent in sorted(parents, key=lambda item: item.question_id)
            for candidate in self.generator.generate_variants(parent)
        )
        ordered = tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.parent_question.question_id,
                    item.proposed_question.question_id,
                    item.generator_identity,
                ),
            )
        )
        proposed_ids = tuple(
            item.proposed_question.question_id for item in ordered
        )
        if len(proposed_ids) != len(set(proposed_ids)):
            raise ValueError("variant generator returned duplicate question IDs")
        results = tuple(self.policy.validate(item) for item in ordered)
        proposed_by_id = {
            item.proposed_question.question_id: item.proposed_question
            for item in ordered
        }
        accepted = tuple(
            proposed_by_id[item.variant_question_id]
            for item in results
            if item.status is VariantValidationStatus.VALIDATED
        )
        payload = {
            "provider_calls": 0,
            "results": [item.to_dict() for item in results],
        }
        return QuestionVariantBatch(
            batch_id=stable_hash("qvb", payload),
            results=results,
            accepted_questions=accepted,
        )


__all__ = [
    "QuestionVariantBatch",
    "QuestionVariantCandidate",
    "QuestionVariantGeneratorPort",
    "SafeQuestionVariantPipeline",
    "VariantValidationCheck",
    "VariantValidationPolicy",
    "VariantValidationResult",
    "VariantValidationStatus",
]
