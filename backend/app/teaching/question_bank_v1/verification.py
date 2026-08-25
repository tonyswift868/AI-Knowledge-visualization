from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from ...knowledge.inventory.models import stable_hash
from .models import (
    QuestionSourceTier,
    QuestionValidationStatus,
    TrustedQuestion,
    TrustedQuestionBank,
)


class AnswerVerificationStatus(str, Enum):
    TRUSTED = "TRUSTED"
    VALIDATED = "VALIDATED"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class AnswerVerificationCandidate:
    question: TrustedQuestion
    conflict_detected: bool = False
    additional_prior_validated_history_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        history_ids = self.additional_prior_validated_history_ids
        if len(history_ids) != len(set(history_ids)):
            raise ValueError("prior validated history IDs must be unique")

    @property
    def prior_validated_history_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    *self.additional_prior_validated_history_ids,
                    *(
                        receipt_id
                        for provenance in self.question.provenance
                        for receipt_id in provenance.assessment_receipt_ids
                    ),
                }
            )
        )


@dataclass(frozen=True)
class AnswerVerificationResult:
    result_id: str
    question_id: str
    question_snapshot_id: str
    status: AnswerVerificationStatus
    answer_evidence_complete: bool
    rubric_available: bool
    agreeing_source_count: int
    prior_validated_history_count: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.result_id,
                self.question_id,
                self.question_snapshot_id,
            )
        ):
            raise ValueError("answer verification result identities are required")
        if min(
            self.agreeing_source_count,
            self.prior_validated_history_count,
        ) < 0:
            raise ValueError("answer verification counts cannot be negative")
        if self.status is AnswerVerificationStatus.TRUSTED and (
            not self.answer_evidence_complete
            or self.status is AnswerVerificationStatus.CONFLICT
        ):
            raise ValueError("trusted answer verification must be evidence complete")

    @property
    def trusted(self) -> bool:
        return self.status is AnswerVerificationStatus.TRUSTED

    def to_dict(self) -> dict[str, object]:
        return {
            "result_id": self.result_id,
            "question_id": self.question_id,
            "question_snapshot_id": self.question_snapshot_id,
            "status": self.status.value,
            "answer_evidence_complete": self.answer_evidence_complete,
            "rubric_available": self.rubric_available,
            "agreeing_source_count": self.agreeing_source_count,
            "prior_validated_history_count": (
                self.prior_validated_history_count
            ),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class AnswerVerificationBatch:
    batch_id: str
    results: tuple[AnswerVerificationResult, ...]

    def __post_init__(self) -> None:
        question_ids = tuple(item.question_id for item in self.results)
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("answer verification results must be unique per question")

    def to_dict(self) -> dict[str, object]:
        return {
            "batch_id": self.batch_id,
            "results": [item.to_dict() for item in self.results],
        }


def _eligible_provenance(candidate: AnswerVerificationCandidate):
    return tuple(
        item
        for item in candidate.question.provenance
        if item.source_tier
        is not QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED
    )


class AnswerVerificationPolicy:
    """Conservative trust synthesis across evidence and corroboration signals."""

    def evaluate(
        self,
        candidate: AnswerVerificationCandidate,
    ) -> AnswerVerificationResult:
        question = candidate.question
        provenance = _eligible_provenance(candidate)
        verified_answer_ids = {
            evidence.evidence_id
            for source in provenance
            for evidence in source.verified_answer_evidence
        }
        required_answer_ids = set(question.answer_key.answer_evidence_ids)
        answer_evidence_complete = bool(required_answer_ids) and (
            required_answer_ids <= verified_answer_ids
        )
        rubric_available = question.answer_key.rubric is not None
        agreeing_sources = {
            source.source_identity
            for source in provenance
            if source.verified_answer_evidence
        }
        history_ids = candidate.prior_validated_history_ids
        source_agreement = len(agreeing_sources) >= 2
        prior_history = bool(history_ids)
        reasons: list[str] = []
        if candidate.conflict_detected:
            status = AnswerVerificationStatus.CONFLICT
            reasons.append("CONFLICTING_ANSWER_SIGNATURES")
        elif question.validation_status is QuestionValidationStatus.REJECTED:
            status = AnswerVerificationStatus.REJECTED
            reasons.append("QUESTION_REJECTED")
        elif question.source_tier is QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED:
            status = AnswerVerificationStatus.NEEDS_VERIFICATION
            reasons.append("T3_CANNOT_BE_DIRECTLY_TRUSTED")
        elif not answer_evidence_complete:
            status = AnswerVerificationStatus.NEEDS_VERIFICATION
            reasons.append("ANSWER_EVIDENCE_INCOMPLETE")
        else:
            corroboration_count = sum(
                (rubric_available, source_agreement, prior_history)
            )
            upstream_trusted = (
                question.validation_status is QuestionValidationStatus.TRUSTED
            )
            if question.source_tier is QuestionSourceTier.T2_VERIFIED_VARIANT:
                if corroboration_count >= 1:
                    status = AnswerVerificationStatus.VALIDATED
                    reasons.append("T2_VERIFIED_WITH_CORROBORATION")
                else:
                    status = AnswerVerificationStatus.NEEDS_VERIFICATION
                    reasons.append("T2_CORROBORATION_MISSING")
            elif corroboration_count >= 2 or (
                upstream_trusted and corroboration_count >= 1
            ):
                status = AnswerVerificationStatus.TRUSTED
                reasons.append("ANSWER_TRUST_SIGNALS_SUFFICIENT")
            elif corroboration_count >= 1 or upstream_trusted:
                status = AnswerVerificationStatus.VALIDATED
                reasons.append("ANSWER_HAS_LIMITED_CORROBORATION")
            else:
                status = AnswerVerificationStatus.NEEDS_VERIFICATION
                reasons.append("ANSWER_CORROBORATION_MISSING")
        if rubric_available:
            reasons.append("RUBRIC_AVAILABLE")
        if source_agreement:
            reasons.append("MULTI_SOURCE_AGREEMENT")
        if prior_history:
            reasons.append("PRIOR_VALIDATED_HISTORY")
        result_payload = {
            "agreeing_source_count": len(agreeing_sources),
            "answer_evidence_complete": answer_evidence_complete,
            "prior_validated_history_ids": history_ids,
            "question_id": question.question_id,
            "reasons": reasons,
            "rubric_available": rubric_available,
            "status": status.value,
        }
        question_snapshot_id = stable_hash("tqsnapshot", question.to_dict())
        result_payload["question_snapshot_id"] = question_snapshot_id
        return AnswerVerificationResult(
            result_id=stable_hash("avr", result_payload),
            question_id=question.question_id,
            question_snapshot_id=question_snapshot_id,
            status=status,
            answer_evidence_complete=answer_evidence_complete,
            rubric_available=rubric_available,
            agreeing_source_count=len(agreeing_sources),
            prior_validated_history_count=len(history_ids),
            reasons=tuple(reasons),
        )


class AnswerVerificationPipeline:
    def __init__(self, policy: AnswerVerificationPolicy | None = None) -> None:
        self.policy = policy or AnswerVerificationPolicy()

    def verify(
        self,
        candidates: Iterable[AnswerVerificationCandidate],
    ) -> AnswerVerificationBatch:
        by_question: dict[str, AnswerVerificationCandidate] = {}
        for candidate in candidates:
            question_id = candidate.question.question_id
            if question_id in by_question:
                raise ValueError(
                    "answer verification accepts one candidate per question"
                )
            by_question[question_id] = candidate
        results = tuple(
            self.policy.evaluate(by_question[key]) for key in sorted(by_question)
        )
        return AnswerVerificationBatch(
            batch_id=stable_hash(
                "avb",
                [item.to_dict() for item in results],
            ),
            results=results,
        )

    def verify_bank(
        self,
        bank: TrustedQuestionBank,
        *,
        additional_history_by_question: Mapping[str, tuple[str, ...]] | None = None,
    ) -> AnswerVerificationBatch:
        history = additional_history_by_question or {}
        conflict_fingerprints = {
            item.fingerprint_id for item in bank.conflicts
        }
        return self.verify(
            AnswerVerificationCandidate(
                question=question,
                conflict_detected=(
                    question.fingerprint.fingerprint_id in conflict_fingerprints
                ),
                additional_prior_validated_history_ids=history.get(
                    question.question_id,
                    (),
                ),
            )
            for question in bank.questions
        )


__all__ = [
    "AnswerVerificationBatch",
    "AnswerVerificationCandidate",
    "AnswerVerificationPipeline",
    "AnswerVerificationPolicy",
    "AnswerVerificationResult",
    "AnswerVerificationStatus",
]
