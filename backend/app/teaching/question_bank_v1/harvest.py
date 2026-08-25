from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ...knowledge.inventory.models import canonical_semantic_key, stable_hash
from ..assessment_v2.models import (
    AssessmentQuestion,
    AssessmentReceipt,
    AssessmentSession,
    AssessmentType,
)
from ..evaluator_v2.models import (
    EvaluationEvidence,
    EvaluationOutcome,
    EvaluationRubric,
)
from .fingerprint import build_answer_key_id, create_question_candidate
from .models import (
    AnswerCriterion,
    QuestionAnswerKey,
    QuestionDifficulty,
    QuestionEvidenceReference,
    QuestionProvenance,
    QuestionSourceTier,
    RubricProvenance,
    TrustedQuestion,
)


_ASSESSMENT_DIFFICULTY = {
    AssessmentType.PRACTICE: QuestionDifficulty.EASY,
    AssessmentType.FORMATIVE: QuestionDifficulty.MEDIUM,
    AssessmentType.SUMMATIVE: QuestionDifficulty.HARD,
    AssessmentType.VERIFICATION: QuestionDifficulty.HARD,
}


@dataclass(frozen=True)
class AssessmentHistory:
    source_identity: str
    source_reference: str | None
    session_id: str
    questions: tuple[AssessmentQuestion, ...]
    rubrics: tuple[EvaluationRubric, ...]
    evidence: tuple[EvaluationEvidence, ...]
    receipts: tuple[AssessmentReceipt, ...]

    def __post_init__(self) -> None:
        if not self.source_identity.strip() or not self.session_id.strip():
            raise ValueError("assessment history source and session identities are required")

    @classmethod
    def from_session(
        cls,
        session: AssessmentSession,
        *,
        source_identity: str,
        source_reference: str | None = None,
    ) -> AssessmentHistory:
        return cls(
            source_identity=source_identity,
            source_reference=source_reference,
            session_id=session.session_id,
            questions=session.questions,
            rubrics=session.rubrics,
            evidence=session.evidence,
            receipts=session.receipts,
        )


@dataclass(frozen=True)
class AssessmentHarvestRejection:
    session_id: str
    question_id: str
    code: str

    def to_dict(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "question_id": self.question_id,
            "code": self.code,
        }


@dataclass(frozen=True)
class AssessmentHarvestResult:
    questions: tuple[TrustedQuestion, ...]
    rejections: tuple[AssessmentHarvestRejection, ...]
    scanned_questions: int
    qualifying_history_records: int


def _evidence_reference(
    evidence: EvaluationEvidence,
    *,
    source_identity: str,
    fallback_source_reference: str | None,
) -> QuestionEvidenceReference:
    return QuestionEvidenceReference(
        evidence_id=evidence.evidence_id,
        source_identity=source_identity,
        source_reference=evidence.source_reference or fallback_source_reference,
        content_hash=stable_hash("question-evidence", evidence.content),
        verified=evidence.verified,
    )


def _rubric_provenance(rubric: EvaluationRubric) -> RubricProvenance:
    criteria = tuple(
        AnswerCriterion(
            criterion_id=item.criterion_id,
            description=item.description,
            weight=item.weight,
            required=item.required,
        )
        for item in rubric.criteria
    )
    return RubricProvenance(
        rubric_id=rubric.rubric_id,
        evaluation_path=rubric.path.value,
        criteria=criteria,
        required_evidence_ids=rubric.required_evidence_ids,
        passing_score=rubric.passing_score,
        partial_credit=rubric.partial_credit,
    )


def _canonical_unique_text(values: Iterable[str]) -> tuple[str, ...]:
    by_key: dict[str, list[str]] = {}
    for value in values:
        key = canonical_semantic_key(value)
        if key:
            by_key.setdefault(key, []).append(value.strip())
    return tuple(min(by_key[key]) for key in sorted(by_key))


class AssessmentQuestionHarvester:
    """Harvest only successfully evaluated Assessment questions as T1 candidates."""

    def harvest(self, histories: Iterable[AssessmentHistory]) -> AssessmentHarvestResult:
        candidates: list[TrustedQuestion] = []
        rejections: list[AssessmentHarvestRejection] = []
        scanned = 0
        qualifying_records = 0
        for history in histories:
            rubric_by_id = {item.rubric_id: item for item in history.rubrics}
            evidence_by_id = {item.evidence_id: item for item in history.evidence}
            receipts_by_question: dict[str, list[AssessmentReceipt]] = {}
            for receipt in history.receipts:
                result = receipt.evaluation_result
                if (
                    receipt.accepted
                    and result is not None
                    and result.outcome is EvaluationOutcome.CORRECT
                    and result.score is not None
                ):
                    receipts_by_question.setdefault(receipt.question_id, []).append(receipt)
            for question in history.questions:
                scanned += 1
                rubric = rubric_by_id.get(question.rubric_id)
                if rubric is None:
                    rejections.append(
                        AssessmentHarvestRejection(
                            session_id=history.session_id,
                            question_id=question.question_id,
                            code="MISSING_RUBRIC",
                        )
                    )
                    continue
                qualifying = tuple(
                    receipt
                    for receipt in receipts_by_question.get(question.question_id, ())
                    if receipt.evaluation_result is not None
                    and receipt.evaluation_result.assessment_id
                    == question.question_id
                    and receipt.evaluation_result.rubric_id == rubric.rubric_id
                    and receipt.evaluation_result.score is not None
                    and receipt.evaluation_result.score >= rubric.passing_score
                )
                if not qualifying:
                    rejections.append(
                        AssessmentHarvestRejection(
                            session_id=history.session_id,
                            question_id=question.question_id,
                            code="NO_SUCCESSFUL_VALIDATION_HISTORY",
                        )
                    )
                    continue
                criteria = tuple(
                    AnswerCriterion(
                        criterion_id=item.criterion_id,
                        description=item.description,
                        weight=item.weight,
                        required=item.required,
                    )
                    for item in rubric.criteria
                )
                if not rubric.accepted_answers and not criteria:
                    rejections.append(
                        AssessmentHarvestRejection(
                            session_id=history.session_id,
                            question_id=question.question_id,
                            code="ANSWER_KEY_MISSING",
                        )
                    )
                    continue
                answer_evidence_ids = tuple(
                    sorted(
                        {
                            *rubric.required_evidence_ids,
                            *(
                                evidence_id
                                for receipt in qualifying
                                if receipt.evaluation_result is not None
                                for evidence_id in receipt.evaluation_result.evidence_refs
                            ),
                        }
                    )
                )
                answer_key = QuestionAnswerKey(
                    answer_key_id=build_answer_key_id(
                        accepted_answers=_canonical_unique_text(
                            rubric.accepted_answers
                        ),
                        criteria=criteria,
                    ),
                    accepted_answers=_canonical_unique_text(
                        rubric.accepted_answers
                    ),
                    partial_answers=_canonical_unique_text(rubric.partial_answers),
                    criteria=criteria,
                    answer_evidence_ids=answer_evidence_ids,
                    rubric=_rubric_provenance(rubric),
                )
                question_evidence = tuple(
                    _evidence_reference(
                        evidence_by_id[evidence_id],
                        source_identity=history.source_identity,
                        fallback_source_reference=history.source_reference,
                    )
                    for evidence_id in question.evidence_ids
                    if evidence_id in evidence_by_id
                )
                answer_evidence = tuple(
                    _evidence_reference(
                        evidence_by_id[evidence_id],
                        source_identity=history.source_identity,
                        fallback_source_reference=history.source_reference,
                    )
                    for evidence_id in answer_evidence_ids
                    if evidence_id in evidence_by_id
                )
                receipt_ids = tuple(sorted(item.receipt_id for item in qualifying))
                provenance = QuestionProvenance(
                    provenance_id=stable_hash(
                        "qprov",
                        {
                            "assessment_receipt_ids": receipt_ids,
                            "question_id": question.question_id,
                            "session_id": history.session_id,
                            "source_identity": history.source_identity,
                        },
                    ),
                    source_tier=QuestionSourceTier.T1_PREVIOUSLY_VALIDATED,
                    source_identity=history.source_identity,
                    source_question_id=question.question_id,
                    question_evidence=question_evidence,
                    answer_evidence=answer_evidence,
                    assessment_session_id=history.session_id,
                    assessment_receipt_ids=receipt_ids,
                    rubric_id=rubric.rubric_id,
                )
                candidates.append(
                    create_question_candidate(
                        prompt=question.prompt,
                        concept_ids=(question.concept_id,),
                        difficulty=_ASSESSMENT_DIFFICULTY[question.assessment_type],
                        source_tier=QuestionSourceTier.T1_PREVIOUSLY_VALIDATED,
                        answer_key=answer_key,
                        provenance=(provenance,),
                        validation_proof=True,
                    )
                )
                qualifying_records += len(qualifying)
        return AssessmentHarvestResult(
            questions=tuple(candidates),
            rejections=tuple(rejections),
            scanned_questions=scanned,
            qualifying_history_records=qualifying_records,
        )
