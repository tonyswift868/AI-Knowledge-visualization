from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from app.teaching.question_bank_v1 import (
    AnswerCriterion,
    AnswerVerificationPipeline,
    AnswerVerificationStatus,
    ExposureSafeQuestionSelector,
    QuestionAnswerKey,
    QuestionBankQualityReporter,
    QuestionDifficulty,
    QuestionEvidenceReference,
    QuestionExposureRecord,
    QuestionProvenance,
    QuestionRevisionCandidate,
    QuestionRevisionReason,
    QuestionSourceTier,
    QuestionValidationStatus,
    QuestionVariantCandidate,
    QuestionVersioningRegistry,
    ReviewContentFeedBuilder,
    ReviewContentIntakePipeline,
    ReviewContentSourceKind,
    ReviewCoverageGapCode,
    ReviewCoveragePlanner,
    ReviewQuestionMetadata,
    ReviewQuestionType,
    RubricProvenance,
    SafeQuestionVariantPipeline,
    TrustedQuestionBankBuilder,
    VariantValidationStatus,
    build_answer_key_id,
    create_question_candidate,
)


def _question(
    *,
    prompt: str,
    answer: str,
    concept_id: str,
    source_identity: str,
    tier: QuestionSourceTier = QuestionSourceTier.T0_SOURCE_ORIGINAL,
    difficulty: QuestionDifficulty = QuestionDifficulty.MEDIUM,
    answer_evidence: bool = True,
    rubric: bool = False,
    prior_history: bool = False,
    variant_of_question_id: str | None = None,
):
    question_evidence_id = f"question-evidence:{source_identity}"
    answer_evidence_id = f"answer-evidence:{source_identity}"
    criteria = (
        AnswerCriterion(
            criterion_id=f"criterion:{source_identity}",
            description="The answer matches the evidence-bound target.",
            weight=1.0,
            required=True,
        ),
    )
    rubric_provenance = (
        RubricProvenance(
            rubric_id=f"rubric:{source_identity}",
            evaluation_path="objective",
            criteria=criteria,
            required_evidence_ids=(answer_evidence_id,),
            passing_score=0.8,
            partial_credit=0.5,
        )
        if rubric
        else None
    )
    answer_key = QuestionAnswerKey(
        answer_key_id=build_answer_key_id(
            accepted_answers=(answer,),
            criteria=criteria if rubric else (),
        ),
        accepted_answers=(answer,),
        partial_answers=(),
        criteria=criteria if rubric else (),
        answer_evidence_ids=(answer_evidence_id,),
        rubric=rubric_provenance,
    )
    provenance = QuestionProvenance(
        provenance_id=f"provenance:{source_identity}:{prompt}",
        source_tier=tier,
        source_identity=source_identity,
        source_question_id=f"source-question:{source_identity}",
        question_evidence=(
            QuestionEvidenceReference(
                evidence_id=question_evidence_id,
                source_identity=source_identity,
                verified=True,
            ),
        ),
        answer_evidence=(
            (
                QuestionEvidenceReference(
                    evidence_id=answer_evidence_id,
                    source_identity=source_identity,
                    verified=True,
                ),
            )
            if answer_evidence
            else ()
        ),
        assessment_receipt_ids=(
            (f"assessment-receipt:{source_identity}",)
            if prior_history
            else ()
        ),
        rubric_id=(f"rubric:{source_identity}" if rubric else None),
        variant_of_question_id=(
            variant_of_question_id
            if tier is QuestionSourceTier.T2_VERIFIED_VARIANT
            else None
        ),
        generator_identity=(
            "offline-fixture-generator"
            if tier is QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED
            else None
        ),
    )
    return create_question_candidate(
        prompt=prompt,
        concept_ids=(concept_id,),
        difficulty=difficulty,
        source_tier=tier,
        answer_key=answer_key,
        provenance=(provenance,),
        validation_proof=True,
    )


def _revision(
    question,
    *,
    source_identity: str,
    source_record_id: str,
    source_revision: str = "revision-1",
    source_revision_ordinal: int = 1,
    reason: QuestionRevisionReason = QuestionRevisionReason.INITIAL_IMPORT,
    question_type: str = ReviewQuestionType.SHORT_ANSWER.value,
) -> QuestionRevisionCandidate:
    return QuestionRevisionCandidate(
        question=question,
        source_identity=source_identity,
        source_record_id=source_record_id,
        source_revision=source_revision,
        source_revision_ordinal=source_revision_ordinal,
        revision_reason=reason,
        question_type=question_type,
    )


@dataclass
class FakeIntakePorts:
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


def _intake_pipeline(ports: FakeIntakePorts) -> ReviewContentIntakePipeline:
    return ReviewContentIntakePipeline(
        document_exercises=ports,
        assessment_history=ports,
        teacher_validated=ports,
        manual_trusted=ports,
    )


def test_question_versioning_is_append_only_and_detects_silent_overwrite() -> None:
    first_question = _question(
        prompt="Original source question",
        answer="A",
        concept_id="concept-versioning",
        source_identity="document-1",
    )
    first = _revision(
        first_question,
        source_identity="document-1",
        source_record_id="exercise-7",
    )
    registry = QuestionVersioningRegistry()

    initial = registry.update((first,))
    idempotent = registry.update((first,), existing_families=initial.families)

    assert len(initial.families) == 1
    assert len(initial.added_versions) == 1
    assert idempotent.added_versions == ()
    assert idempotent.idempotent_version_ids == (
        initial.families[0].current_version_id,
    )

    revised_question = _question(
        prompt="Corrected source question",
        answer="A",
        concept_id="concept-versioning",
        source_identity="document-1",
    )
    revised = _revision(
        revised_question,
        source_identity="document-1",
        source_record_id="exercise-7",
        source_revision="revision-2",
        source_revision_ordinal=2,
        reason=QuestionRevisionReason.SOURCE_REVISION,
    )
    revision_result = registry.update(
        (revised,),
        existing_families=initial.families,
    )
    family = revision_result.families[0]

    assert len(family.versions) == 2
    assert family.versions[0] == initial.families[0].versions[0]
    assert family.current_version.question.prompt == "Corrected source question"
    assert (
        family.current_version.supersedes_version_id
        == family.versions[0].version_id
    )

    silent_overwrite = _revision(
        revised_question,
        source_identity="document-1",
        source_record_id="exercise-7",
    )
    conflict = registry.update(
        (silent_overwrite,),
        existing_families=initial.families,
    )

    assert conflict.added_versions == ()
    assert conflict.families == initial.families
    assert conflict.conflicts[0].code == (
        "SOURCE_REVISION_REUSED_WITH_DIFFERENT_CONTENT"
    )


def test_four_port_intake_is_deterministic_and_collapses_cross_source_duplicate() -> None:
    shared_document = _question(
        prompt="What is a trusted review item?",
        answer="An evidence-bound question.",
        concept_id="concept-shared",
        source_identity="document-source",
    )
    shared_manual = _question(
        prompt="WHAT is a trusted review item!",
        answer="an evidence-bound question",
        concept_id="concept-shared",
        source_identity="manual-source",
    )
    assessment = _question(
        prompt="What confirms prior validation?",
        answer="An accepted assessment receipt.",
        concept_id="concept-assessment",
        source_identity="assessment-source",
        tier=QuestionSourceTier.T1_PREVIOUSLY_VALIDATED,
        rubric=True,
        prior_history=True,
    )
    teacher = _question(
        prompt="What validates a safe variant?",
        answer="Evidence and bounded semantic change.",
        concept_id="concept-variant",
        source_identity="teacher-source",
    )
    ports = FakeIntakePorts(
        documents=(
            _revision(
                shared_document,
                source_identity="document-source",
                source_record_id="exercise-1",
            ),
        ),
        assessments=(
            _revision(
                assessment,
                source_identity="assessment-source",
                source_record_id="assessment-question-1",
            ),
        ),
        teacher=(
            _revision(
                teacher,
                source_identity="teacher-source",
                source_record_id="teacher-question-1",
            ),
        ),
        manual=(
            _revision(
                shared_manual,
                source_identity="manual-source",
                source_record_id="manual-question-1",
            ),
        ),
    )

    first = _intake_pipeline(ports).run()
    second = _intake_pipeline(ports).run()

    assert first.receipt.to_dict() == second.receipt.to_dict()
    assert first.receipt.input_count == 4
    assert first.receipt.accepted_count == 4
    assert first.receipt.family_count == 4
    assert first.bank.receipt.retained_count == 3
    assert first.bank.receipt.collapsed_count == 1
    shared = next(
        item
        for item in first.bank.questions
        if "concept-shared" in item.concept_ids
    )
    assert {item.source_identity for item in shared.provenance} == {
        "document-source",
        "manual-source",
    }
    assert tuple(sorted(item.source_kind for item in first.current_versions)) == (
        tuple(
            sorted(
                (
                    ReviewContentSourceKind.DOCUMENT_EXERCISE.value,
                    ReviewContentSourceKind.ASSESSMENT_HISTORY.value,
                    ReviewContentSourceKind.TEACHER_VALIDATED.value,
                    ReviewContentSourceKind.MANUAL_TRUSTED.value,
                )
            )
        )
    )


def test_intake_rejects_unverified_ai_question_from_validated_ports() -> None:
    ai_question = _question(
        prompt="AI draft",
        answer="Draft answer",
        concept_id="concept-ai",
        source_identity="ai-source",
        tier=QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED,
    )
    ports = FakeIntakePorts(
        teacher=(
            _revision(
                ai_question,
                source_identity="ai-source",
                source_record_id="ai-draft-1",
            ),
        )
    )

    result = _intake_pipeline(ports).run()

    assert result.receipt.accepted_count == 0
    assert result.receipt.rejected_count == 1
    assert result.rejections[0].code == "TEACHER_VALIDATED_CANNOT_BE_T3"


def test_answer_verification_uses_all_trust_signals_and_conflict_wins() -> None:
    t1 = _question(
        prompt="Assessment-backed answer",
        answer="Verified",
        concept_id="concept-verification",
        source_identity="assessment",
        tier=QuestionSourceTier.T1_PREVIOUSLY_VALIDATED,
        rubric=True,
        prior_history=True,
    )
    source_a = _question(
        prompt="Multi-source answer",
        answer="Agreed",
        concept_id="concept-verification",
        source_identity="source-a",
    )
    source_b = _question(
        prompt="multi source answer!",
        answer="agreed",
        concept_id="concept-verification",
        source_identity="source-b",
    )
    single_source = _question(
        prompt="Single source answer",
        answer="Limited",
        concept_id="concept-verification",
        source_identity="single-source",
    )
    missing_evidence = _question(
        prompt="Missing answer evidence",
        answer="Unsupported",
        concept_id="concept-verification",
        source_identity="missing-source",
        answer_evidence=False,
    )
    conflict_a = _question(
        prompt="What is the conflicting threshold?",
        answer="0.8",
        concept_id="concept-verification",
        source_identity="conflict-a",
    )
    conflict_b = _question(
        prompt="What is the conflicting threshold?",
        answer="0.9",
        concept_id="concept-verification",
        source_identity="conflict-b",
    )
    bank = TrustedQuestionBankBuilder().build(
        (
            t1,
            source_a,
            source_b,
            single_source,
            missing_evidence,
            conflict_a,
            conflict_b,
        )
    )

    batch = AnswerVerificationPipeline().verify_bank(bank)
    by_question = {item.question_id: item for item in batch.results}
    merged = next(
        item
        for item in bank.questions
        if item.fingerprint == source_a.fingerprint
    )

    assert by_question[t1.question_id].status is AnswerVerificationStatus.TRUSTED
    assert (
        by_question[merged.question_id].status
        is AnswerVerificationStatus.TRUSTED
    )
    assert by_question[merged.question_id].agreeing_source_count == 2
    assert (
        by_question[single_source.question_id].status
        is AnswerVerificationStatus.VALIDATED
    )
    assert (
        by_question[missing_evidence.question_id].status
        is AnswerVerificationStatus.NEEDS_VERIFICATION
    )
    for question_id in bank.conflicts[0].question_ids:
        assert by_question[question_id].status is AnswerVerificationStatus.CONFLICT
        assert by_question[question_id].trusted is False


def _variant(
    parent,
    *,
    prompt: str,
    answer: str | None = None,
    difficulty: QuestionDifficulty | None = None,
    tier: QuestionSourceTier = QuestionSourceTier.T2_VERIFIED_VARIANT,
):
    resolved_answer = answer or parent.answer_key.accepted_answers[0]
    answer_key = QuestionAnswerKey(
        answer_key_id=build_answer_key_id(
            accepted_answers=(resolved_answer,),
            criteria=parent.answer_key.criteria,
        ),
        accepted_answers=(resolved_answer,),
        partial_answers=parent.answer_key.partial_answers,
        criteria=parent.answer_key.criteria,
        answer_evidence_ids=parent.answer_key.answer_evidence_ids,
        rubric=parent.answer_key.rubric,
    )
    provenance = QuestionProvenance(
        provenance_id=f"variant-provenance:{prompt}",
        source_tier=tier,
        source_identity="offline-variant-fixture",
        source_question_id="variant-fixture",
        question_evidence=parent.provenance[0].question_evidence,
        answer_evidence=parent.provenance[0].answer_evidence,
        variant_of_question_id=(
            parent.question_id
            if tier is QuestionSourceTier.T2_VERIFIED_VARIANT
            else None
        ),
        generator_identity=(
            "offline-variant-fixture"
            if tier is QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED
            else None
        ),
    )
    return create_question_candidate(
        prompt=prompt,
        concept_ids=parent.concept_ids,
        difficulty=difficulty or parent.difficulty,
        source_tier=tier,
        answer_key=answer_key,
        provenance=(provenance,),
        validation_proof=True,
    )


class FakeVariantGenerator:
    def __init__(self, candidate_by_parent):
        self.candidate_by_parent = candidate_by_parent
        self.calls = []

    def generate_variants(self, parent):
        self.calls.append(parent.question_id)
        return (self.candidate_by_parent[parent.question_id],)


def test_safe_variant_pipeline_validates_all_boundaries_without_provider_calls() -> None:
    parent = _question(
        prompt="Explain evidence binding.",
        answer="Bind the answer to verified evidence.",
        concept_id="concept-variant",
        source_identity="variant-parent",
        difficulty=QuestionDifficulty.EASY,
        rubric=True,
    )
    proposed = _variant(
        parent,
        prompt="How would you describe evidence binding?",
        difficulty=QuestionDifficulty.MEDIUM,
    )
    candidate = QuestionVariantCandidate(
        parent_question=parent,
        proposed_question=proposed,
        generator_identity="fake-generator",
        semantic_target_unchanged=True,
        answer_remains_valid=True,
        evidence_still_supports=True,
    )
    generator = FakeVariantGenerator({parent.question_id: candidate})

    batch = SafeQuestionVariantPipeline(generator).run((parent,))

    assert generator.calls == [parent.question_id]
    assert batch.provider_calls == 0
    assert batch.results[0].status is VariantValidationStatus.VALIDATED
    assert batch.accepted_questions == (proposed,)
    assert proposed.source_tier is QuestionSourceTier.T2_VERIFIED_VARIANT
    assert proposed.validation_status is QuestionValidationStatus.VALIDATED


@pytest.mark.parametrize(
    ("candidate_update", "failed_check"),
    [
        ({"semantic_target_unchanged": False}, "SEMANTIC_TARGET_UNCHANGED"),
        ({"answer_remains_valid": False}, "ANSWER_REMAINS_VALID"),
        ({"evidence_still_supports": False}, "ANSWER_EVIDENCE_STILL_SUPPORTS"),
    ],
)
def test_unsafe_variant_is_rejected(candidate_update, failed_check) -> None:
    parent = _question(
        prompt="Original variant target",
        answer="Original answer",
        concept_id="concept-variant",
        source_identity="variant-parent",
        rubric=True,
    )
    proposed = _variant(parent, prompt="Proposed variant target")
    candidate = QuestionVariantCandidate(
        parent_question=parent,
        proposed_question=proposed,
        generator_identity="fake-generator",
        semantic_target_unchanged=True,
        answer_remains_valid=True,
        evidence_still_supports=True,
    )
    candidate = replace(candidate, **candidate_update)
    result = SafeQuestionVariantPipeline(
        FakeVariantGenerator({parent.question_id: candidate})
    ).run((parent,))

    assert result.results[0].status is VariantValidationStatus.REJECTED
    assert result.accepted_questions == ()
    checks = {item.name: item.passed for item in result.results[0].checks}
    assert checks[failed_check] is False


def test_coverage_quality_exposure_and_feed_are_endpoint_neutral() -> None:
    q1 = _question(
        prompt="A1",
        answer="A1",
        concept_id="concept-a",
        source_identity="source-a1",
    )
    q2 = _question(
        prompt="A2",
        answer="A2",
        concept_id="concept-a",
        source_identity="source-a2",
    )
    q3 = _question(
        prompt="A3",
        answer="A3",
        concept_id="concept-a",
        source_identity="source-a3",
    )
    q1_conflict = _question(
        prompt="A1",
        answer="Conflicting A1",
        concept_id="concept-a",
        source_identity="source-a-conflict",
    )
    c1 = _question(
        prompt="C1",
        answer="C1",
        concept_id="concept-c",
        source_identity="source-c1",
    )
    c1_duplicates = tuple(
        _question(
            prompt="C1!",
            answer="c1",
            concept_id="concept-c",
            source_identity=f"source-c1-duplicate-{index}",
        )
        for index in range(3)
    )
    c2 = _question(
        prompt="C2",
        answer="C2",
        concept_id="concept-c",
        source_identity="source-c2",
    )
    raw_candidates = (q1, q2, q3, q1_conflict, c1, *c1_duplicates, c2)
    bank = TrustedQuestionBankBuilder().build(raw_candidates)
    metadata = tuple(
        ReviewQuestionMetadata(
            question_id=item.question_id,
            question_type=ReviewQuestionType.SHORT_ANSWER,
        )
        for item in bank.questions
    )
    planner = ReviewCoveragePlanner(
        minimum_trusted_questions=2,
        maximum_duplicate_ratio=0.4,
        maximum_question_type_ratio=0.8,
    )

    plan = planner.plan(
        bank,
        concept_scope=("concept-c", "concept-b", "concept-a"),
        critical_concept_ids=("concept-b",),
        metadata=metadata,
        candidate_questions=raw_candidates,
    )
    gap_codes = {(item.concept_id, item.code) for item in plan.gaps}

    assert ("concept-b", ReviewCoverageGapCode.NO_TRUSTED_QUESTION) in gap_codes
    assert (
        "concept-b",
        ReviewCoverageGapCode.CRITICAL_CONCEPT_UNCOVERED,
    ) in gap_codes
    assert ("concept-c", ReviewCoverageGapCode.DUPLICATE_RATIO_HIGH) in gap_codes
    assert (
        "concept-a",
        ReviewCoverageGapCode.QUESTION_TYPE_CONCENTRATION,
    ) in gap_codes

    report = QuestionBankQualityReporter().build(bank, plan)

    assert report.answer_conflict_count == 1
    assert report.trusted_coverage_ratio == pytest.approx(2 / 3)
    assert report.critical_coverage_ratio == 0
    assert report.duplicate_ratio == pytest.approx(3 / 9)
    assert report.source_gaps == ("concept-b",)

    class FakeExposureHistory:
        def recent_exposures(self, learner_identity, *, limit):
            assert learner_identity == "learner-1"
            return (
                QuestionExposureRecord(q2.question_id, 5),
                QuestionExposureRecord(q3.question_id, 4),
            )[:limit]

        def total_exposure_count(self, learner_identity, question_id):
            return {q1.question_id: 0, q2.question_id: 3, q3.question_id: 1}.get(
                question_id,
                0,
            )

    simple_bank = TrustedQuestionBankBuilder().build((q1, q2, q3))
    selection = ExposureSafeQuestionSelector(recent_window=3).select(
        simple_bank,
        history=FakeExposureHistory(),
        learner_identity="learner-1",
        limit=2,
        concept_ids=("concept-a",),
    )

    assert tuple(item.question_id for item in selection.selected_questions) == (
        q1.question_id,
        q3.question_id,
    )
    assert selection.periodic_repeat_question_ids == (q3.question_id,)
    assert q2.question_id in selection.suppressed_question_ids


def test_review_content_feed_preserves_lineage_and_excludes_conflicts() -> None:
    document = _question(
        prompt="Feed question",
        answer="Feed answer",
        concept_id="concept-feed",
        source_identity="feed-document",
        rubric=True,
    )
    manual = _question(
        prompt="feed question!",
        answer="feed answer",
        concept_id="concept-feed",
        source_identity="feed-manual",
        rubric=True,
    )
    ports = FakeIntakePorts(
        documents=(
            _revision(
                document,
                source_identity="feed-document",
                source_record_id="feed-doc-1",
            ),
        ),
        manual=(
            _revision(
                manual,
                source_identity="feed-manual",
                source_record_id="feed-manual-1",
            ),
        ),
    )
    intake = _intake_pipeline(ports).run()
    verification = AnswerVerificationPipeline().verify_bank(intake.bank)
    metadata = tuple(
        ReviewQuestionMetadata.from_version(item)
        for item in intake.current_versions
    )
    plan = ReviewCoveragePlanner(minimum_trusted_questions=1).plan(
        intake.bank,
        concept_scope=("concept-feed",),
        metadata=metadata,
    )
    report = QuestionBankQualityReporter().build(intake.bank, plan)

    first = ReviewContentFeedBuilder().build(
        bank=intake.bank,
        families=intake.families,
        answer_verification=verification,
        coverage_plan=plan,
        quality_report=report,
    )
    second = ReviewContentFeedBuilder().build(
        bank=intake.bank,
        families=reversed(intake.families),
        answer_verification=verification,
        coverage_plan=plan,
        quality_report=report,
    )

    assert first.to_dict() == second.to_dict()
    assert first.provider_calls == 0
    assert len(first.items) == 1
    assert len(first.items[0].family_ids) == 2
    assert len(first.items[0].version_ids) == 2
    assert (
        first.items[0].answer_verification.status
        is AnswerVerificationStatus.TRUSTED
    )
