from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...knowledge.inventory.models import canonical_json, canonical_semantic_key


QUESTION_BANK_SCHEMA_VERSION = "mentorflow.trusted-question-bank.v1"
QUESTION_BANK_VERSION = "mentorflow.question-bank-compiler.v1"


class QuestionSourceTier(str, Enum):
    T0_SOURCE_ORIGINAL = "T0_SOURCE_ORIGINAL"
    T1_PREVIOUSLY_VALIDATED = "T1_PREVIOUSLY_VALIDATED"
    T2_VERIFIED_VARIANT = "T2_VERIFIED_VARIANT"
    T3_AI_GENERATED_UNVERIFIED = "T3_AI_GENERATED_UNVERIFIED"


SOURCE_TIER_RANK = {
    QuestionSourceTier.T0_SOURCE_ORIGINAL: 0,
    QuestionSourceTier.T1_PREVIOUSLY_VALIDATED: 1,
    QuestionSourceTier.T2_VERIFIED_VARIANT: 2,
    QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED: 3,
}


class QuestionValidationStatus(str, Enum):
    TRUSTED = "TRUSTED"
    VALIDATED = "VALIDATED"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    REJECTED = "REJECTED"


VALIDATION_STATUS_RANK = {
    QuestionValidationStatus.TRUSTED: 0,
    QuestionValidationStatus.VALIDATED: 1,
    QuestionValidationStatus.NEEDS_VERIFICATION: 2,
    QuestionValidationStatus.REJECTED: 3,
}


class QuestionDifficulty(str, Enum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"
    UNSPECIFIED = "UNSPECIFIED"


DIFFICULTY_RANK = {
    QuestionDifficulty.UNSPECIFIED: 0,
    QuestionDifficulty.EASY: 1,
    QuestionDifficulty.MEDIUM: 2,
    QuestionDifficulty.HARD: 3,
}


@dataclass(frozen=True)
class QuestionEvidenceReference:
    evidence_id: str
    source_identity: str
    source_reference: str | None = None
    page_number: int | None = None
    section: str | None = None
    timestamp_start_ms: int | None = None
    timestamp_end_ms: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    content_hash: str | None = None
    verified: bool = True

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.source_identity.strip():
            raise ValueError("evidence ID and source identity are required")
        if self.page_number is not None and self.page_number <= 0:
            raise ValueError("page number must be positive")
        timestamps = (self.timestamp_start_ms, self.timestamp_end_ms)
        if any(value is None for value in timestamps) and not all(
            value is None for value in timestamps
        ):
            raise ValueError("timestamp range must be complete or absent")
        if self.timestamp_start_ms is not None and (
            self.timestamp_start_ms < 0
            or self.timestamp_end_ms is None
            or self.timestamp_end_ms <= self.timestamp_start_ms
        ):
            raise ValueError("timestamp range is invalid")
        lines = (self.line_start, self.line_end)
        if any(value is None for value in lines) and not all(value is None for value in lines):
            raise ValueError("line range must be complete or absent")
        if self.line_start is not None and (
            self.line_start <= 0
            or self.line_end is None
            or self.line_end < self.line_start
        ):
            raise ValueError("line range is invalid")

    @property
    def identity_key(self) -> tuple[str, str]:
        return self.source_identity, self.evidence_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_identity": self.source_identity,
            "source_reference": self.source_reference,
            "page_number": self.page_number,
            "section": self.section,
            "timestamp_start_ms": self.timestamp_start_ms,
            "timestamp_end_ms": self.timestamp_end_ms,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "content_hash": self.content_hash,
            "verified": self.verified,
        }


@dataclass(frozen=True)
class AnswerCriterion:
    criterion_id: str
    description: str
    weight: float
    required: bool

    def __post_init__(self) -> None:
        if not self.criterion_id.strip() or not self.description.strip():
            raise ValueError("answer criterion identity and description are required")
        if not 0 < self.weight <= 1:
            raise ValueError("answer criterion weight must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "weight": self.weight,
            "required": self.required,
        }


@dataclass(frozen=True)
class RubricProvenance:
    rubric_id: str
    evaluation_path: str
    criteria: tuple[AnswerCriterion, ...]
    required_evidence_ids: tuple[str, ...]
    passing_score: float
    partial_credit: float

    def __post_init__(self) -> None:
        if not self.rubric_id.strip() or not self.evaluation_path.strip():
            raise ValueError("rubric identity and evaluation path are required")
        if len(self.required_evidence_ids) != len(set(self.required_evidence_ids)):
            raise ValueError("rubric evidence IDs must be unique")
        if not 0 <= self.passing_score <= 1 or not 0 <= self.partial_credit <= 1:
            raise ValueError("rubric scores must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rubric_id": self.rubric_id,
            "evaluation_path": self.evaluation_path,
            "criteria": [item.to_dict() for item in self.criteria],
            "required_evidence_ids": list(self.required_evidence_ids),
            "passing_score": self.passing_score,
            "partial_credit": self.partial_credit,
        }


@dataclass(frozen=True)
class QuestionAnswerKey:
    answer_key_id: str
    accepted_answers: tuple[str, ...]
    partial_answers: tuple[str, ...]
    criteria: tuple[AnswerCriterion, ...]
    answer_evidence_ids: tuple[str, ...]
    rubric: RubricProvenance | None = None

    def __post_init__(self) -> None:
        if not self.answer_key_id.strip():
            raise ValueError("answer_key_id is required")
        for field in ("accepted_answers", "partial_answers", "answer_evidence_ids"):
            values = tuple(getattr(self, field))
            if len(values) != len(set(values)):
                raise ValueError(f"{field} must be unique")
        if not self.accepted_answers and not self.criteria:
            raise ValueError("answer key requires accepted answers or rubric criteria")

    @property
    def canonical_answer_signature(self) -> str:
        return canonical_json(
            {
                "accepted_answers": sorted(
                    canonical_semantic_key(item) for item in self.accepted_answers
                ),
                "criteria": sorted(
                    (
                        canonical_semantic_key(item.description),
                        item.weight,
                        item.required,
                    )
                    for item in self.criteria
                ),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_key_id": self.answer_key_id,
            "accepted_answers": list(self.accepted_answers),
            "partial_answers": list(self.partial_answers),
            "criteria": [item.to_dict() for item in self.criteria],
            "answer_evidence_ids": list(self.answer_evidence_ids),
            "rubric": self.rubric.to_dict() if self.rubric is not None else None,
        }


@dataclass(frozen=True)
class QuestionProvenance:
    provenance_id: str
    source_tier: QuestionSourceTier
    source_identity: str
    source_question_id: str | None
    question_evidence: tuple[QuestionEvidenceReference, ...]
    answer_evidence: tuple[QuestionEvidenceReference, ...]
    assessment_session_id: str | None = None
    assessment_receipt_ids: tuple[str, ...] = ()
    rubric_id: str | None = None
    variant_of_question_id: str | None = None
    generator_identity: str | None = None

    def __post_init__(self) -> None:
        if not self.provenance_id.strip() or not self.source_identity.strip():
            raise ValueError("provenance ID and source identity are required")
        if len(self.assessment_receipt_ids) != len(set(self.assessment_receipt_ids)):
            raise ValueError("assessment receipt IDs must be unique")
        question_keys = tuple(item.identity_key for item in self.question_evidence)
        answer_keys = tuple(item.identity_key for item in self.answer_evidence)
        if len(question_keys) != len(set(question_keys)):
            raise ValueError("question provenance evidence must be unique")
        if len(answer_keys) != len(set(answer_keys)):
            raise ValueError("answer provenance evidence must be unique")
        if (
            self.source_tier is QuestionSourceTier.T2_VERIFIED_VARIANT
            and not self.variant_of_question_id
        ):
            raise ValueError("verified variant provenance requires a parent question")
        if (
            self.source_tier is QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED
            and not self.generator_identity
        ):
            raise ValueError("AI-generated provenance requires generator identity")

    @property
    def verified_question_evidence(self) -> tuple[QuestionEvidenceReference, ...]:
        return tuple(item for item in self.question_evidence if item.verified)

    @property
    def verified_answer_evidence(self) -> tuple[QuestionEvidenceReference, ...]:
        return tuple(item for item in self.answer_evidence if item.verified)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance_id": self.provenance_id,
            "source_tier": self.source_tier.value,
            "source_identity": self.source_identity,
            "source_question_id": self.source_question_id,
            "question_evidence": [item.to_dict() for item in self.question_evidence],
            "answer_evidence": [item.to_dict() for item in self.answer_evidence],
            "assessment_session_id": self.assessment_session_id,
            "assessment_receipt_ids": list(self.assessment_receipt_ids),
            "rubric_id": self.rubric_id,
            "variant_of_question_id": self.variant_of_question_id,
            "generator_identity": self.generator_identity,
        }


@dataclass(frozen=True)
class QuestionFingerprint:
    fingerprint_id: str
    canonical_prompt: str
    canonical_concept_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.fingerprint_id.strip() or not self.canonical_prompt:
            raise ValueError("question fingerprint identity and prompt are required")
        if not self.canonical_concept_ids:
            raise ValueError("question fingerprint requires concept IDs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint_id": self.fingerprint_id,
            "canonical_prompt": self.canonical_prompt,
            "canonical_concept_ids": list(self.canonical_concept_ids),
        }


@dataclass(frozen=True)
class TrustedQuestion:
    question_id: str
    prompt: str
    concept_ids: tuple[str, ...]
    difficulty: QuestionDifficulty
    source_tier: QuestionSourceTier
    validation_status: QuestionValidationStatus
    fingerprint: QuestionFingerprint
    answer_key: QuestionAnswerKey
    provenance: tuple[QuestionProvenance, ...]

    def __post_init__(self) -> None:
        if not self.question_id.strip() or not self.prompt.strip():
            raise ValueError("question identity and prompt are required")
        if not self.concept_ids or len(self.concept_ids) != len(set(self.concept_ids)):
            raise ValueError("question concept IDs must be non-empty and unique")
        if not self.provenance:
            raise ValueError("question provenance is required")
        provenance_ids = tuple(item.provenance_id for item in self.provenance)
        if len(provenance_ids) != len(set(provenance_ids)):
            raise ValueError("question provenance IDs must be unique")
        expected_tier = min(
            (item.source_tier for item in self.provenance),
            key=lambda item: SOURCE_TIER_RANK[item],
        )
        if self.source_tier is not expected_tier:
            raise ValueError("question source tier must match its strongest provenance")
        if self.fingerprint.canonical_concept_ids != tuple(
            sorted(canonical_semantic_key(item) for item in self.concept_ids)
        ):
            raise ValueError("question fingerprint concepts do not match the question")
        if (
            self.source_tier is QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED
            and self.validation_status
            in {QuestionValidationStatus.TRUSTED, QuestionValidationStatus.VALIDATED}
        ):
            raise ValueError("AI-generated question cannot be directly trusted or validated")
        if self.validation_status in {
            QuestionValidationStatus.TRUSTED,
            QuestionValidationStatus.VALIDATED,
        }:
            verified_question = {
                item.evidence_id
                for source in self.provenance
                for item in source.verified_question_evidence
            }
            verified_answer = {
                item.evidence_id
                for source in self.provenance
                for item in source.verified_answer_evidence
            }
            if not verified_question:
                raise ValueError("trusted or validated question requires question evidence")
            if not verified_answer:
                raise ValueError("trusted or validated question requires answer evidence")
            if not set(self.answer_key.answer_evidence_ids) <= verified_answer:
                raise ValueError("answer key evidence must be present in verified provenance")

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "prompt": self.prompt,
            "concept_ids": list(self.concept_ids),
            "difficulty": self.difficulty.value,
            "source_tier": self.source_tier.value,
            "validation_status": self.validation_status.value,
            "fingerprint": self.fingerprint.to_dict(),
            "answer_key": self.answer_key.to_dict(),
            "provenance": [item.to_dict() for item in self.provenance],
        }


@dataclass(frozen=True)
class AnswerConflictDiagnostics:
    fingerprint_id: str
    question_ids: tuple[str, ...]
    source_identities: tuple[str, ...]
    answer_signatures: tuple[str, ...]
    resolution_required: bool = True

    def __post_init__(self) -> None:
        if not self.fingerprint_id.strip():
            raise ValueError("answer conflict fingerprint is required")
        if len(self.question_ids) < 2 or len(set(self.question_ids)) < 2:
            raise ValueError("answer conflict requires distinct question candidates")
        if len(self.answer_signatures) < 2 or len(set(self.answer_signatures)) < 2:
            raise ValueError("answer conflict requires distinct answer signatures")
        if not self.source_identities:
            raise ValueError("answer conflict requires source identities")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint_id": self.fingerprint_id,
            "question_ids": list(self.question_ids),
            "source_identities": list(self.source_identities),
            "answer_signatures": list(self.answer_signatures),
            "resolution_required": self.resolution_required,
        }


@dataclass(frozen=True)
class DuplicateResolutionResult:
    questions: tuple[TrustedQuestion, ...]
    conflicts: tuple[AnswerConflictDiagnostics, ...]
    input_count: int
    collapsed_count: int


@dataclass(frozen=True)
class QuestionCount:
    key: str
    count: int

    def __post_init__(self) -> None:
        if not self.key.strip() or self.count < 0:
            raise ValueError("question count key is required and count cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "count": self.count}


@dataclass(frozen=True)
class QuestionBankReceipt:
    receipt_id: str
    bank_id: str
    input_count: int
    retained_count: int
    collapsed_count: int
    conflict_count: int
    tier_counts: tuple[QuestionCount, ...]
    status_counts: tuple[QuestionCount, ...]
    compiler_version: str = QUESTION_BANK_VERSION

    def __post_init__(self) -> None:
        if not self.receipt_id.strip() or not self.bank_id.strip():
            raise ValueError("question bank receipt and bank IDs are required")
        if min(
            self.input_count,
            self.retained_count,
            self.collapsed_count,
            self.conflict_count,
        ) < 0:
            raise ValueError("question bank counts cannot be negative")
        if self.input_count - self.retained_count != self.collapsed_count:
            raise ValueError("question bank collapse accounting is inconsistent")
        tier_keys = tuple(item.key for item in self.tier_counts)
        status_keys = tuple(item.key for item in self.status_counts)
        if len(tier_keys) != len(set(tier_keys)):
            raise ValueError("question bank tier counts must have unique keys")
        if len(status_keys) != len(set(status_keys)):
            raise ValueError("question bank status counts must have unique keys")
        if sum(item.count for item in self.tier_counts) != self.retained_count:
            raise ValueError("question bank tier counts are inconsistent")
        if sum(item.count for item in self.status_counts) != self.retained_count:
            raise ValueError("question bank status counts are inconsistent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "bank_id": self.bank_id,
            "input_count": self.input_count,
            "retained_count": self.retained_count,
            "collapsed_count": self.collapsed_count,
            "conflict_count": self.conflict_count,
            "tier_counts": [item.to_dict() for item in self.tier_counts],
            "status_counts": [item.to_dict() for item in self.status_counts],
            "compiler_version": self.compiler_version,
        }


@dataclass(frozen=True)
class TrustedQuestionBank:
    bank_id: str
    questions: tuple[TrustedQuestion, ...]
    conflicts: tuple[AnswerConflictDiagnostics, ...]
    receipt: QuestionBankReceipt
    schema_version: str = QUESTION_BANK_SCHEMA_VERSION
    compiler_version: str = QUESTION_BANK_VERSION

    def __post_init__(self) -> None:
        if self.receipt.bank_id != self.bank_id:
            raise ValueError("question bank and receipt IDs do not match")
        question_ids = tuple(item.question_id for item in self.questions)
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question bank question IDs must be unique")
        if self.receipt.retained_count != len(self.questions):
            raise ValueError("question bank retained count does not match questions")
        if self.receipt.conflict_count != len(self.conflicts):
            raise ValueError("question bank conflict count does not match diagnostics")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "compiler_version": self.compiler_version,
            "bank_id": self.bank_id,
            "questions": [item.to_dict() for item in self.questions],
            "conflicts": [item.to_dict() for item in self.conflicts],
            "receipt": self.receipt.to_dict(),
        }


@dataclass(frozen=True)
class QuestionReviewHistory:
    question_id: str
    seen_count: int = 0
    wrong_count: int = 0
    passed_count: int = 0

    def __post_init__(self) -> None:
        if not self.question_id.strip():
            raise ValueError("review history question_id is required")
        if min(self.seen_count, self.wrong_count, self.passed_count) < 0:
            raise ValueError("review history counts cannot be negative")
        if self.wrong_count + self.passed_count > self.seen_count:
            raise ValueError("review outcomes cannot exceed seen count")
