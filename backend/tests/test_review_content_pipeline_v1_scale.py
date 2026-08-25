from __future__ import annotations

from dataclasses import dataclass

from app.teaching.question_bank_v1 import (
    QuestionAnswerKey,
    QuestionBankQualityReporter,
    QuestionDifficulty,
    QuestionEvidenceReference,
    QuestionProvenance,
    QuestionRevisionCandidate,
    QuestionRevisionReason,
    QuestionSourceTier,
    QuestionVariantCandidate,
    ReviewContentIntakePipeline,
    ReviewCoveragePlanner,
    SafeQuestionVariantPipeline,
    TrustedQuestionBankBuilder,
    VariantValidationStatus,
    build_answer_key_id,
    create_question_candidate,
)


def _scale_question(
    index: int,
    *,
    source_identity: str,
    tier: QuestionSourceTier,
    prompt: str | None = None,
    answer: str | None = None,
    concept_id: str | None = None,
):
    resolved_prompt = prompt or f"Scale review question {index}"
    resolved_answer = answer or f"answer-{index}"
    resolved_concept = concept_id or f"concept-{index % 1_000:04d}"
    question_evidence_id = f"question-evidence:{source_identity}"
    answer_evidence_id = f"answer-evidence:{source_identity}"
    answer_key = QuestionAnswerKey(
        answer_key_id=build_answer_key_id(
            accepted_answers=(resolved_answer,),
            criteria=(),
        ),
        accepted_answers=(resolved_answer,),
        partial_answers=(),
        criteria=(),
        answer_evidence_ids=(answer_evidence_id,),
    )
    provenance = QuestionProvenance(
        provenance_id=f"provenance:{source_identity}:{index}",
        source_tier=tier,
        source_identity=source_identity,
        source_question_id=f"source-question:{index}",
        question_evidence=(
            QuestionEvidenceReference(
                evidence_id=question_evidence_id,
                source_identity=source_identity,
                verified=True,
            ),
        ),
        answer_evidence=(
            QuestionEvidenceReference(
                evidence_id=answer_evidence_id,
                source_identity=source_identity,
                verified=True,
            ),
        ),
    )
    return create_question_candidate(
        prompt=resolved_prompt,
        concept_ids=(resolved_concept,),
        difficulty=QuestionDifficulty.MEDIUM,
        source_tier=tier,
        answer_key=answer_key,
        provenance=(provenance,),
        validation_proof=True,
    )


def _scale_revision(
    question,
    *,
    source_identity: str,
    source_record_id: str,
    source_revision: str = "revision-1",
    source_revision_ordinal: int = 1,
    reason: QuestionRevisionReason = QuestionRevisionReason.INITIAL_IMPORT,
):
    return QuestionRevisionCandidate(
        question=question,
        source_identity=source_identity,
        source_record_id=source_record_id,
        source_revision=source_revision,
        source_revision_ordinal=source_revision_ordinal,
        revision_reason=reason,
        question_type="SHORT_ANSWER",
    )


@dataclass
class ScalePorts:
    documents: tuple[QuestionRevisionCandidate, ...] = ()
    assessments: tuple[QuestionRevisionCandidate, ...] = ()
    teacher: tuple[QuestionRevisionCandidate, ...] = ()
    manual: tuple[QuestionRevisionCandidate, ...] = ()

    def load_document_exercises(self):
        return self.documents

    def load_assessment_history(self):
        return self.assessments

    def load_teacher_validated_questions(self):
        return self.teacher

    def load_manual_trusted_questions(self):
        return self.manual


def _pipeline(ports: ScalePorts) -> ReviewContentIntakePipeline:
    return ReviewContentIntakePipeline(
        document_exercises=ports,
        assessment_history=ports,
        teacher_validated=ports,
        manual_trusted=ports,
    )


def _initial_scale_ports() -> ScalePorts:
    buckets: list[list[QuestionRevisionCandidate]] = [[], [], [], []]
    for index in range(10_000):
        source_identity = f"scale-source-{index}"
        bucket_index = index // 2_500
        tier = (
            QuestionSourceTier.T1_PREVIOUSLY_VALIDATED
            if bucket_index == 1
            else QuestionSourceTier.T0_SOURCE_ORIGINAL
        )
        question = _scale_question(
            index,
            source_identity=source_identity,
            tier=tier,
        )
        buckets[bucket_index].append(
            _scale_revision(
                question,
                source_identity=source_identity,
                source_record_id=f"scale-record-{index}",
            )
        )
    for index in range(100):
        source_identity = f"scale-duplicate-source-{index}"
        duplicate = _scale_question(
            index,
            source_identity=source_identity,
            tier=QuestionSourceTier.T0_SOURCE_ORIGINAL,
            prompt=f"SCALE review question {index}!",
            answer=f"ANSWER-{index}",
            concept_id=f"concept-{index % 1_000:04d}",
        )
        buckets[0].append(
            _scale_revision(
                duplicate,
                source_identity=source_identity,
                source_record_id=f"scale-duplicate-record-{index}",
            )
        )
    for index in range(100, 150):
        source_identity = f"scale-conflict-source-{index}"
        conflict = _scale_question(
            index,
            source_identity=source_identity,
            tier=QuestionSourceTier.T0_SOURCE_ORIGINAL,
            prompt=f"Scale review question {index}",
            answer=f"conflicting-answer-{index}",
            concept_id=f"concept-{index % 1_000:04d}",
        )
        buckets[0].append(
            _scale_revision(
                conflict,
                source_identity=source_identity,
                source_record_id=f"scale-conflict-record-{index}",
            )
        )
    return ScalePorts(
        documents=tuple(buckets[0]),
        assessments=tuple(buckets[1]),
        teacher=tuple(buckets[2]),
        manual=tuple(buckets[3]),
    )


def _reverse_ports(ports: ScalePorts) -> ScalePorts:
    return ScalePorts(
        documents=tuple(reversed(ports.documents)),
        assessments=tuple(reversed(ports.assessments)),
        teacher=tuple(reversed(ports.teacher)),
        manual=tuple(reversed(ports.manual)),
    )


def _revision_ports() -> ScalePorts:
    revisions = []
    for index in range(500, 600):
        source_identity = f"scale-source-{index}"
        revised = _scale_question(
            index,
            source_identity=source_identity,
            tier=QuestionSourceTier.T0_SOURCE_ORIGINAL,
            prompt=f"Scale review question {index} revised",
        )
        revisions.append(
            _scale_revision(
                revised,
                source_identity=source_identity,
                source_record_id=f"scale-record-{index}",
                source_revision="revision-2",
                source_revision_ordinal=2,
                reason=QuestionRevisionReason.SOURCE_REVISION,
            )
        )
    return ScalePorts(documents=tuple(revisions))


def _scale_variant(parent, index: int):
    provenance = QuestionProvenance(
        provenance_id=f"scale-variant-provenance-{index}",
        source_tier=QuestionSourceTier.T2_VERIFIED_VARIANT,
        source_identity=f"scale-fake-variant-generator-{index}",
        source_question_id=f"scale-variant-{index}",
        question_evidence=parent.provenance[0].question_evidence,
        answer_evidence=parent.provenance[0].answer_evidence,
        variant_of_question_id=parent.question_id,
    )
    question = create_question_candidate(
        prompt=f"Safe variant {index}: {parent.prompt}",
        concept_ids=parent.concept_ids,
        difficulty=parent.difficulty,
        source_tier=QuestionSourceTier.T2_VERIFIED_VARIANT,
        answer_key=parent.answer_key,
        provenance=(provenance,),
        validation_proof=True,
    )
    return QuestionVariantCandidate(
        parent_question=parent,
        proposed_question=question,
        generator_identity="scale-fake-generator",
        semantic_target_unchanged=True,
        answer_remains_valid=True,
        evidence_still_supports=True,
    )


class ScaleFakeVariantGenerator:
    def __init__(self, parents):
        self.candidates = {
            parent.question_id: _scale_variant(parent, index)
            for index, parent in enumerate(parents)
        }

    def generate_variants(self, parent):
        return (self.candidates[parent.question_id],)


def test_ten_thousand_question_pipeline_is_deterministic_at_scale() -> None:
    ports = _initial_scale_ports()

    forward = _pipeline(ports).run()
    reverse = _pipeline(_reverse_ports(ports)).run()

    assert forward.receipt.receipt_id == reverse.receipt.receipt_id
    assert forward.bank.bank_id == reverse.bank.bank_id
    assert forward.receipt.input_count == 10_150
    assert forward.receipt.accepted_count == 10_150
    assert forward.receipt.family_count == 10_150
    assert forward.bank.receipt.retained_count == 10_050
    assert forward.bank.receipt.collapsed_count == 100
    assert forward.bank.receipt.conflict_count == 50
    assert len(
        {
            concept_id
            for question in forward.bank.questions
            for concept_id in question.concept_ids
        }
    ) == 1_000

    revision_ports = _revision_ports()
    revised_forward = _pipeline(revision_ports).run(
        existing_families=forward.families
    )
    revised_reverse = _pipeline(_reverse_ports(revision_ports)).run(
        existing_families=reversed(reverse.families)
    )

    assert revised_forward.receipt.receipt_id == revised_reverse.receipt.receipt_id
    assert revised_forward.receipt.added_version_count == 100
    assert revised_forward.receipt.family_count == 10_150
    assert sum(len(item.versions) for item in revised_forward.families) == 10_250
    revised_family = next(
        item
        for item in revised_forward.families
        if item.source_identity == "scale-source-500"
    )
    assert len(revised_family.versions) == 2
    assert revised_family.versions[0].source_revision == "revision-1"
    assert revised_family.versions[1].source_revision == "revision-2"

    parents = tuple(
        item
        for item in revised_forward.bank.questions
        if item.source_tier is QuestionSourceTier.T0_SOURCE_ORIGINAL
        and item.validation_status.value == "TRUSTED"
        and len(item.provenance) == 1
    )[:100]
    generator = ScaleFakeVariantGenerator(parents)
    variants_forward = SafeQuestionVariantPipeline(generator).run(parents)
    variants_reverse = SafeQuestionVariantPipeline(generator).run(
        reversed(parents)
    )

    assert variants_forward.batch_id == variants_reverse.batch_id
    assert variants_forward.provider_calls == 0
    assert len(variants_forward.accepted_questions) == 100
    assert all(
        item.status is VariantValidationStatus.VALIDATED
        for item in variants_forward.results
    )

    expanded_forward = TrustedQuestionBankBuilder().build(
        (*revised_forward.bank.questions, *variants_forward.accepted_questions)
    )
    expanded_reverse = TrustedQuestionBankBuilder().build(
        (
            *reversed(revised_reverse.bank.questions),
            *reversed(variants_reverse.accepted_questions),
        )
    )
    assert expanded_forward.bank_id == expanded_reverse.bank_id
    scope = tuple(f"concept-{index:04d}" for index in range(1_000))
    critical = tuple(f"concept-{index:04d}" for index in range(0, 1_000, 100))
    planner = ReviewCoveragePlanner(
        minimum_trusted_questions=1,
        maximum_question_type_ratio=1.0,
    )
    plan_forward = planner.plan(
        expanded_forward,
        concept_scope=scope,
        critical_concept_ids=critical,
    )
    plan_reverse = planner.plan(
        expanded_reverse,
        concept_scope=reversed(scope),
        critical_concept_ids=reversed(critical),
    )
    report_forward = QuestionBankQualityReporter().build(
        expanded_forward,
        plan_forward,
    )
    report_reverse = QuestionBankQualityReporter().build(
        expanded_reverse,
        plan_reverse,
    )

    assert plan_forward.to_dict() == plan_reverse.to_dict()
    assert report_forward.to_dict() == report_reverse.to_dict()
    assert report_forward.concept_count == 1_000
    assert report_forward.critical_coverage_ratio == 1.0
    assert next(
        item.count
        for item in report_forward.tier_distribution
        if item.key == QuestionSourceTier.T2_VERIFIED_VARIANT.value
    ) == 100
