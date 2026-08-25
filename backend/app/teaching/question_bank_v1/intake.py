from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Protocol

from ...knowledge.inventory.models import stable_hash
from .bank import TrustedQuestionBankBuilder
from .models import (
    QuestionSourceTier,
    QuestionValidationStatus,
    TrustedQuestionBank,
)
from .versioning import (
    QuestionFamily,
    QuestionRevisionCandidate,
    QuestionVersion,
    QuestionVersionConflict,
    QuestionVersioningRegistry,
)


class ReviewContentSourceKind(str, Enum):
    DOCUMENT_EXERCISE = "DOCUMENT_EXERCISE"
    ASSESSMENT_HISTORY = "ASSESSMENT_HISTORY"
    TEACHER_VALIDATED = "TEACHER_VALIDATED"
    MANUAL_TRUSTED = "MANUAL_TRUSTED"


class DocumentExercisePort(Protocol):
    def load_document_exercises(
        self,
    ) -> Iterable[QuestionRevisionCandidate]: ...


class AssessmentHistoryPort(Protocol):
    def load_assessment_history(
        self,
    ) -> Iterable[QuestionRevisionCandidate]: ...


class TeacherValidatedQuestionPort(Protocol):
    def load_teacher_validated_questions(
        self,
    ) -> Iterable[QuestionRevisionCandidate]: ...


class ManualTrustedQuestionPort(Protocol):
    def load_manual_trusted_questions(
        self,
    ) -> Iterable[QuestionRevisionCandidate]: ...


@dataclass(frozen=True)
class ReviewContentIntakeRejection:
    source_kind: ReviewContentSourceKind
    source_identity: str
    source_record_id: str
    question_id: str
    code: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_kind": self.source_kind.value,
            "source_identity": self.source_identity,
            "source_record_id": self.source_record_id,
            "question_id": self.question_id,
            "code": self.code,
        }


@dataclass(frozen=True)
class ReviewContentSourceCount:
    source_kind: ReviewContentSourceKind
    count: int

    def to_dict(self) -> dict[str, str | int]:
        return {"source_kind": self.source_kind.value, "count": self.count}


@dataclass(frozen=True)
class ReviewContentIntakeReceipt:
    receipt_id: str
    input_count: int
    accepted_count: int
    rejected_count: int
    added_version_count: int
    idempotent_version_count: int
    version_conflict_count: int
    family_count: int
    bank_id: str
    source_counts: tuple[ReviewContentSourceCount, ...]

    def __post_init__(self) -> None:
        values = (
            self.input_count,
            self.accepted_count,
            self.rejected_count,
            self.added_version_count,
            self.idempotent_version_count,
            self.version_conflict_count,
            self.family_count,
        )
        if not self.receipt_id.strip() or not self.bank_id.strip():
            raise ValueError("intake receipt and bank IDs are required")
        if min(values) < 0:
            raise ValueError("intake receipt counts cannot be negative")
        if self.accepted_count + self.rejected_count != self.input_count:
            raise ValueError("intake receipt accounting is inconsistent")
        if sum(item.count for item in self.source_counts) != self.input_count:
            raise ValueError("intake source counts are inconsistent")

    def to_dict(self) -> dict[str, object]:
        return {
            "receipt_id": self.receipt_id,
            "input_count": self.input_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "added_version_count": self.added_version_count,
            "idempotent_version_count": self.idempotent_version_count,
            "version_conflict_count": self.version_conflict_count,
            "family_count": self.family_count,
            "bank_id": self.bank_id,
            "source_counts": [item.to_dict() for item in self.source_counts],
        }


@dataclass(frozen=True)
class ReviewContentIntakeResult:
    bank: TrustedQuestionBank
    families: tuple[QuestionFamily, ...]
    current_versions: tuple[QuestionVersion, ...]
    version_conflicts: tuple[QuestionVersionConflict, ...]
    rejections: tuple[ReviewContentIntakeRejection, ...]
    receipt: ReviewContentIntakeReceipt


def _intake_rejection_code(
    source_kind: ReviewContentSourceKind,
    candidate: QuestionRevisionCandidate,
) -> str | None:
    question = candidate.question
    if source_kind is ReviewContentSourceKind.DOCUMENT_EXERCISE:
        if question.source_tier is not QuestionSourceTier.T0_SOURCE_ORIGINAL:
            return "DOCUMENT_EXERCISE_REQUIRES_T0"
    elif source_kind is ReviewContentSourceKind.ASSESSMENT_HISTORY:
        if question.source_tier is not QuestionSourceTier.T1_PREVIOUSLY_VALIDATED:
            return "ASSESSMENT_HISTORY_REQUIRES_T1"
    elif source_kind is ReviewContentSourceKind.TEACHER_VALIDATED:
        if question.source_tier is QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED:
            return "TEACHER_VALIDATED_CANNOT_BE_T3"
        if question.validation_status not in {
            QuestionValidationStatus.TRUSTED,
            QuestionValidationStatus.VALIDATED,
        }:
            return "TEACHER_VALIDATED_REQUIRES_VALIDATION"
    elif source_kind is ReviewContentSourceKind.MANUAL_TRUSTED:
        if (
            question.source_tier is not QuestionSourceTier.T0_SOURCE_ORIGINAL
            or question.validation_status is not QuestionValidationStatus.TRUSTED
        ):
            return "MANUAL_TRUSTED_REQUIRES_TRUSTED_T0"
    return None


class ReviewContentIntakePipeline:
    """Deterministic four-port intake, independent of endpoint/runtime code."""

    def __init__(
        self,
        *,
        document_exercises: DocumentExercisePort,
        assessment_history: AssessmentHistoryPort,
        teacher_validated: TeacherValidatedQuestionPort,
        manual_trusted: ManualTrustedQuestionPort,
        versioning: QuestionVersioningRegistry | None = None,
        bank_builder: TrustedQuestionBankBuilder | None = None,
    ) -> None:
        self.document_exercises = document_exercises
        self.assessment_history = assessment_history
        self.teacher_validated = teacher_validated
        self.manual_trusted = manual_trusted
        self.versioning = versioning or QuestionVersioningRegistry()
        self.bank_builder = bank_builder or TrustedQuestionBankBuilder()

    def run(
        self,
        *,
        existing_families: Iterable[QuestionFamily] = (),
    ) -> ReviewContentIntakeResult:
        batches = (
            (
                ReviewContentSourceKind.DOCUMENT_EXERCISE,
                tuple(self.document_exercises.load_document_exercises()),
            ),
            (
                ReviewContentSourceKind.ASSESSMENT_HISTORY,
                tuple(self.assessment_history.load_assessment_history()),
            ),
            (
                ReviewContentSourceKind.TEACHER_VALIDATED,
                tuple(
                    self.teacher_validated.load_teacher_validated_questions()
                ),
            ),
            (
                ReviewContentSourceKind.MANUAL_TRUSTED,
                tuple(self.manual_trusted.load_manual_trusted_questions()),
            ),
        )
        accepted: list[QuestionRevisionCandidate] = []
        rejections: list[ReviewContentIntakeRejection] = []
        source_counts: list[ReviewContentSourceCount] = []
        for source_kind, candidates in batches:
            source_counts.append(
                ReviewContentSourceCount(source_kind, len(candidates))
            )
            for candidate in candidates:
                code = _intake_rejection_code(source_kind, candidate)
                if code is not None:
                    rejections.append(
                        ReviewContentIntakeRejection(
                            source_kind=source_kind,
                            source_identity=candidate.source_identity,
                            source_record_id=candidate.source_record_id,
                            question_id=candidate.question.question_id,
                            code=code,
                        )
                    )
                    continue
                accepted.append(candidate.with_source_kind(source_kind.value))
        versioning = self.versioning.update(
            accepted,
            existing_families=existing_families,
        )
        current_versions = tuple(
            sorted(
                (item.current_version for item in versioning.families),
                key=lambda item: (item.family_id, item.version_id),
            )
        )
        bank = self.bank_builder.build(
            item.question for item in current_versions
        )
        input_count = sum(item.count for item in source_counts)
        ordered_rejections = tuple(
            sorted(
                rejections,
                key=lambda item: (
                    item.source_kind.value,
                    item.source_identity,
                    item.source_record_id,
                    item.question_id,
                ),
            )
        )
        receipt_id = stable_hash(
            "rcir",
            {
                "bank_id": bank.bank_id,
                "families": [item.to_dict() for item in versioning.families],
                "rejections": [item.to_dict() for item in ordered_rejections],
                "source_counts": [item.to_dict() for item in source_counts],
                "version_conflicts": [
                    item.to_dict() for item in versioning.conflicts
                ],
            },
        )
        receipt = ReviewContentIntakeReceipt(
            receipt_id=receipt_id,
            input_count=input_count,
            accepted_count=len(accepted),
            rejected_count=len(ordered_rejections),
            added_version_count=len(versioning.added_versions),
            idempotent_version_count=len(versioning.idempotent_version_ids),
            version_conflict_count=len(versioning.conflicts),
            family_count=len(versioning.families),
            bank_id=bank.bank_id,
            source_counts=tuple(source_counts),
        )
        return ReviewContentIntakeResult(
            bank=bank,
            families=versioning.families,
            current_versions=current_versions,
            version_conflicts=versioning.conflicts,
            rejections=ordered_rejections,
            receipt=receipt,
        )


__all__ = [
    "AssessmentHistoryPort",
    "DocumentExercisePort",
    "ManualTrustedQuestionPort",
    "ReviewContentIntakePipeline",
    "ReviewContentIntakeReceipt",
    "ReviewContentIntakeRejection",
    "ReviewContentIntakeResult",
    "ReviewContentSourceCount",
    "ReviewContentSourceKind",
    "TeacherValidatedQuestionPort",
]
