from __future__ import annotations

from dataclasses import replace

import pytest

from app.teaching.assessment_v2 import (
    AssessmentCommitPlan,
    AssessmentQuestion,
    AssessmentReceipt,
    AssessmentSessionState,
    AssessmentType,
    VerificationQualification,
    VerificationStatus,
)
from app.teaching.contracts import HintDependency
from app.teaching.evaluator_v2 import (
    EvaluationEvidence,
    EvaluationOutcome,
    EvaluationPath,
    EvaluationResult,
    EvaluationRubric,
    RubricCriterion,
)
from app.teaching.question_bank_v1 import (
    AnswerCriterion,
    AssessmentHistory,
    AssessmentQuestionHarvester,
    QuestionAnswerKey,
    QuestionBankFilter,
    QuestionBankQuery,
    QuestionDifficulty,
    QuestionDuplicateResolver,
    QuestionEvidenceReference,
    QuestionProvenance,
    QuestionReviewHistory,
    QuestionSourceTier,
    QuestionValidationStatus,
    TrustedQuestionBankBuilder,
    build_answer_key_id,
    create_question_candidate,
)


def _candidate(
    *,
    prompt: str,
    answer: str,
    source_identity: str,
    tier: QuestionSourceTier = QuestionSourceTier.T0_SOURCE_ORIGINAL,
    concept_id: str = "concept-alpha",
    difficulty: QuestionDifficulty = QuestionDifficulty.MEDIUM,
    question_evidence: bool = True,
    answer_evidence: bool = True,
    validation_proof: bool = True,
):
    question_evidence_id = f"question-evidence:{source_identity}"
    answer_evidence_id = f"answer-evidence:{source_identity}"
    key = QuestionAnswerKey(
        answer_key_id=build_answer_key_id(
            accepted_answers=(answer,),
            criteria=(),
        ),
        accepted_answers=(answer,),
        partial_answers=(),
        criteria=(),
        answer_evidence_ids=(answer_evidence_id,),
    )
    provenance = QuestionProvenance(
        provenance_id=f"provenance:{source_identity}",
        source_tier=tier,
        source_identity=source_identity,
        source_question_id=f"source-question:{source_identity}",
        question_evidence=(
            (
                QuestionEvidenceReference(
                    evidence_id=question_evidence_id,
                    source_identity=source_identity,
                    source_reference=f"source://{source_identity}",
                    verified=True,
                ),
            )
            if question_evidence
            else ()
        ),
        answer_evidence=(
            (
                QuestionEvidenceReference(
                    evidence_id=answer_evidence_id,
                    source_identity=source_identity,
                    source_reference=f"source://{source_identity}",
                    verified=True,
                ),
            )
            if answer_evidence
            else ()
        ),
        variant_of_question_id=(
            "parent-question"
            if tier is QuestionSourceTier.T2_VERIFIED_VARIANT
            else None
        ),
        generator_identity=(
            "offline-test-generator"
            if tier is QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED
            else None
        ),
    )
    return create_question_candidate(
        prompt=prompt,
        concept_ids=(concept_id,),
        difficulty=difficulty,
        source_tier=tier,
        answer_key=key,
        provenance=(provenance,),
        validation_proof=validation_proof,
    )


def _verification() -> VerificationQualification:
    return VerificationQualification(
        status=VerificationStatus.NOT_QUALIFIED,
        policy_id="verification-policy.v1",
        assessment_type=AssessmentType.FORMATIVE,
        rubric_coverage=1.0,
        evidence_grounded=True,
        hint_free=True,
        hint_dependency=HintDependency.NONE,
        hint_dependency_clear=True,
        first_attempt=True,
        attempt_conditions_met=True,
        question_qualified=False,
        qualified_question_ids=(),
        required_question_count=2,
        reason="single result is history evidence, not a mastery decision",
    )


def _assessment_history(
    *,
    source_identity: str = "assessment-store",
    include_answer_evidence: bool = True,
    outcome: EvaluationOutcome = EvaluationOutcome.CORRECT,
    score: float = 1.0,
) -> AssessmentHistory:
    question = AssessmentQuestion(
        question_id="assessment-question-1",
        concept_id="concept-assessment",
        prompt="Which boundary makes an answer independently verifiable?",
        rubric_id="rubric-1",
        evidence_ids=("question-evidence",),
        assessment_type=AssessmentType.FORMATIVE,
    )
    rubric = EvaluationRubric(
        rubric_id="rubric-1",
        path=EvaluationPath.OBJECTIVE,
        accepted_answers=("Bind it to source evidence.",),
        partial_answers=("Cite a source.",),
        criteria=(
            RubricCriterion(
                criterion_id="criterion-evidence",
                description="The answer is bound to verified source evidence.",
                weight=1.0,
                required=True,
            ),
        ),
        required_evidence_ids=("answer-evidence",),
        passing_score=0.8,
    )
    evidence = [
        EvaluationEvidence(
            evidence_id="question-evidence",
            content="The source asks for independent verification boundaries.",
            source_reference="chapter-2#section-4",
            verified=True,
        )
    ]
    if include_answer_evidence:
        evidence.append(
            EvaluationEvidence(
                evidence_id="answer-evidence",
                content="An answer is independently verifiable when evidence-bound.",
                source_reference="chapter-2#section-5",
                verified=True,
            )
        )
    result = EvaluationResult(
        assessment_id=question.question_id,
        attempt_id="attempt-1",
        rubric_id=rubric.rubric_id,
        path=EvaluationPath.OBJECTIVE,
        outcome=outcome,
        score=score,
        confidence=0.99,
        reason="deterministic objective evaluation",
        evidence_refs=("answer-evidence",),
        provider_calls=0,
    )
    verification = _verification()
    commit_plan = AssessmentCommitPlan(
        plan_id="commit-plan-1",
        session_id="session-1",
        submission_id="submission-1",
        verification=verification,
        proposed_events=(),
        reason="history fixture does not execute learning-state writes",
    )
    receipt = AssessmentReceipt(
        receipt_id="assessment-receipt-1",
        session_id="session-1",
        submission_id="submission-1",
        attempt_id="attempt-1",
        question_id=question.question_id,
        attempt_number=1,
        accepted=True,
        state_before=AssessmentSessionState.ACTIVE,
        state_after=AssessmentSessionState.EVALUATED,
        evaluation_result=result,
        verification=verification,
        commit_plan=commit_plan,
        provider_calls=0,
        reason="accepted assessment history fixture",
        recorded_at_ms=100,
    )
    return AssessmentHistory(
        source_identity=source_identity,
        source_reference="assessment://session-1",
        session_id="session-1",
        questions=(question,),
        rubrics=(rubric,),
        evidence=tuple(evidence),
        receipts=(receipt,),
    )


def test_tier_policy_and_ai_direct_trust_guard() -> None:
    t0 = _candidate(prompt="T0 question", answer="A", source_identity="t0")
    t1 = _candidate(
        prompt="T1 question",
        answer="A",
        source_identity="t1",
        tier=QuestionSourceTier.T1_PREVIOUSLY_VALIDATED,
    )
    t2 = _candidate(
        prompt="T2 question",
        answer="A",
        source_identity="t2",
        tier=QuestionSourceTier.T2_VERIFIED_VARIANT,
    )
    t3 = _candidate(
        prompt="T3 question",
        answer="A",
        source_identity="t3",
        tier=QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED,
    )

    assert t0.validation_status is QuestionValidationStatus.TRUSTED
    assert t1.validation_status is QuestionValidationStatus.TRUSTED
    assert t2.validation_status is QuestionValidationStatus.VALIDATED
    assert t3.validation_status is QuestionValidationStatus.NEEDS_VERIFICATION
    with pytest.raises(ValueError, match="AI-generated question"):
        replace(t3, validation_status=QuestionValidationStatus.TRUSTED)
    with pytest.raises(ValueError, match="strongest provenance"):
        replace(t3, source_tier=QuestionSourceTier.T0_SOURCE_ORIGINAL)


def test_missing_answer_evidence_can_only_need_verification() -> None:
    question = _candidate(
        prompt="Question without answer provenance",
        answer="Unverified answer",
        source_identity="missing-answer",
        answer_evidence=False,
    )

    assert question.validation_status is QuestionValidationStatus.NEEDS_VERIFICATION
    with pytest.raises(ValueError, match="answer evidence"):
        replace(question, validation_status=QuestionValidationStatus.TRUSTED)


def test_location_provenance_preserves_available_coordinates() -> None:
    evidence = QuestionEvidenceReference(
        evidence_id="located-evidence",
        source_identity="book-revision-7",
        source_reference="book://chapter-3",
        page_number=42,
        section="3.4",
        timestamp_start_ms=1_200,
        timestamp_end_ms=2_400,
        line_start=80,
        line_end=86,
        content_hash="sha256:example",
    )

    assert evidence.to_dict() == {
        "evidence_id": "located-evidence",
        "source_identity": "book-revision-7",
        "source_reference": "book://chapter-3",
        "page_number": 42,
        "section": "3.4",
        "timestamp_start_ms": 1_200,
        "timestamp_end_ms": 2_400,
        "line_start": 80,
        "line_end": 86,
        "content_hash": "sha256:example",
        "verified": True,
    }


def test_exact_duplicate_collapses_and_unions_multi_source_provenance() -> None:
    first = _candidate(
        prompt="What is evidence binding?",
        answer="A source-linked assertion.",
        source_identity="source-a",
        tier=QuestionSourceTier.T1_PREVIOUSLY_VALIDATED,
    )
    second = _candidate(
        prompt="  WHAT is evidence binding! ",
        answer="a source-linked assertion",
        source_identity="source-b",
        tier=QuestionSourceTier.T0_SOURCE_ORIGINAL,
    )

    bank = TrustedQuestionBankBuilder().build((first, second))

    assert bank.receipt.input_count == 2
    assert bank.receipt.retained_count == 1
    assert bank.receipt.collapsed_count == 1
    assert bank.receipt.conflict_count == 0
    merged = bank.questions[0]
    assert merged.source_tier is QuestionSourceTier.T0_SOURCE_ORIGINAL
    assert merged.validation_status is QuestionValidationStatus.TRUSTED
    assert merged.answer_key.answer_key_id == first.answer_key.answer_key_id
    assert {item.source_identity for item in merged.provenance} == {
        "source-a",
        "source-b",
    }
    assert set(merged.answer_key.answer_evidence_ids) == {
        "answer-evidence:source-a",
        "answer-evidence:source-b",
    }


def test_answer_conflict_is_explicit_and_fail_closed() -> None:
    first = _candidate(
        prompt="What is the threshold?",
        answer="0.8",
        source_identity="source-a",
    )
    second = _candidate(
        prompt="What is the threshold?",
        answer="0.9",
        source_identity="source-b",
    )

    bank = TrustedQuestionBankBuilder().build((first, second))

    assert bank.receipt.retained_count == 2
    assert bank.receipt.collapsed_count == 0
    assert bank.receipt.conflict_count == 1
    assert all(
        item.validation_status is QuestionValidationStatus.NEEDS_VERIFICATION
        for item in bank.questions
    )
    diagnostic = bank.conflicts[0]
    assert diagnostic.resolution_required is True
    assert diagnostic.source_identities == ("source-a", "source-b")
    assert len(diagnostic.answer_signatures) == 2


def test_similar_but_distinct_questions_are_not_merged_without_fuzzy_matcher() -> None:
    candidates = (
        _candidate(
            prompt="What is 2 + 2?",
            answer="4",
            source_identity="math-a",
        ),
        _candidate(
            prompt="What is 2 + 3?",
            answer="5",
            source_identity="math-b",
        ),
    )
    resolver = QuestionDuplicateResolver()

    resolution = resolver.resolve(candidates)

    assert resolver.fuzzy_matching_enabled is False
    assert resolution.input_count == 2
    assert resolution.collapsed_count == 0
    assert len(resolution.questions) == 2
    assert resolution.conflicts == ()


def test_endpoint_neutral_query_filters_history_and_trust() -> None:
    trusted_easy = _candidate(
        prompt="Easy trusted",
        answer="A",
        source_identity="easy",
        concept_id="concept-a",
        difficulty=QuestionDifficulty.EASY,
    )
    trusted_hard = _candidate(
        prompt="Hard trusted",
        answer="B",
        source_identity="hard",
        concept_id="concept-b",
        difficulty=QuestionDifficulty.HARD,
        tier=QuestionSourceTier.T1_PREVIOUSLY_VALIDATED,
    )
    unverified = _candidate(
        prompt="AI draft",
        answer="C",
        source_identity="ai",
        concept_id="concept-a",
        tier=QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED,
    )
    bank = TrustedQuestionBankBuilder().build(
        (trusted_easy, trusted_hard, unverified)
    )
    query = QuestionBankQuery(
        bank,
        history=(
            QuestionReviewHistory(
                question_id=trusted_easy.question_id,
                seen_count=2,
                wrong_count=1,
                passed_count=1,
            ),
            QuestionReviewHistory(
                question_id=trusted_hard.question_id,
                seen_count=1,
                passed_count=1,
            ),
        ),
    )

    assert {item.question_id for item in query.by_concept("concept-a")} == {
        trusted_easy.question_id,
        unverified.question_id,
    }
    assert query.by_difficulty(QuestionDifficulty.HARD) == (trusted_hard,)
    assert query.by_source_tier(
        QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED
    ) == (unverified,)
    assert query.never_seen() == (unverified,)
    assert query.previously_wrong() == (trusted_easy,)
    assert query.previously_passed() == (trusted_easy, trusted_hard)
    assert set(query.trusted_only()) == {trusted_easy, trusted_hard}
    assert query.select(
        QuestionBankFilter(
            concept_ids=("concept-a",),
            validation_statuses=(QuestionValidationStatus.TRUSTED,),
            previously_wrong=True,
        )
    ) == (trusted_easy,)


def test_bank_identity_and_output_are_deterministic_across_input_order() -> None:
    candidates = (
        _candidate(prompt="Question A", answer="A", source_identity="source-a"),
        _candidate(prompt="Question B", answer="B", source_identity="source-b"),
        _candidate(
            prompt="question a!",
            answer="a",
            source_identity="source-c",
        ),
    )

    forward = TrustedQuestionBankBuilder().build(candidates)
    reverse = TrustedQuestionBankBuilder().build(reversed(candidates))

    assert forward.bank_id == reverse.bank_id
    assert forward.receipt.receipt_id == reverse.receipt.receipt_id
    assert forward.to_dict() == reverse.to_dict()


def test_assessment_harvest_preserves_rubric_answer_and_history_provenance() -> None:
    history = _assessment_history()
    harvester = AssessmentQuestionHarvester()

    first = harvester.harvest((history,))
    second = harvester.harvest((history,))

    assert first.rejections == ()
    assert first.scanned_questions == 1
    assert first.qualifying_history_records == 1
    assert first.questions == second.questions
    question = first.questions[0]
    assert question.source_tier is QuestionSourceTier.T1_PREVIOUSLY_VALIDATED
    assert question.validation_status is QuestionValidationStatus.TRUSTED
    assert question.answer_key.accepted_answers == (
        "Bind it to source evidence.",
    )
    assert question.answer_key.rubric is not None
    assert question.answer_key.rubric.rubric_id == "rubric-1"
    assert question.answer_key.rubric.criteria == (
        AnswerCriterion(
            criterion_id="criterion-evidence",
            description="The answer is bound to verified source evidence.",
            weight=1.0,
            required=True,
        ),
    )
    provenance = question.provenance[0]
    assert provenance.assessment_session_id == "session-1"
    assert provenance.assessment_receipt_ids == ("assessment-receipt-1",)
    assert provenance.rubric_id == "rubric-1"
    assert {item.evidence_id for item in provenance.answer_evidence} == {
        "answer-evidence"
    }


def test_assessment_harvest_missing_answer_evidence_is_not_trusted() -> None:
    result = AssessmentQuestionHarvester().harvest(
        (_assessment_history(include_answer_evidence=False),)
    )

    assert result.rejections == ()
    assert len(result.questions) == 1
    assert (
        result.questions[0].validation_status
        is QuestionValidationStatus.NEEDS_VERIFICATION
    )
    assert result.questions[0].provenance[0].answer_evidence == ()


def test_assessment_harvest_rejects_unsuccessful_history() -> None:
    result = AssessmentQuestionHarvester().harvest(
        (
            _assessment_history(
                outcome=EvaluationOutcome.INCORRECT,
                score=0.0,
            ),
        )
    )

    assert result.questions == ()
    assert result.rejections[0].code == "NO_SUCCESSFUL_VALIDATION_HISTORY"


def test_assessment_harvest_rejects_cross_question_or_rubric_receipt() -> None:
    history = _assessment_history()
    receipt = history.receipts[0]
    assert receipt.evaluation_result is not None
    mismatched_result = receipt.evaluation_result.model_copy(
        update={"rubric_id": "unrelated-rubric"}
    )
    mismatched_receipt = receipt.model_copy(
        update={"evaluation_result": mismatched_result}
    )
    mismatched_history = replace(history, receipts=(mismatched_receipt,))

    result = AssessmentQuestionHarvester().harvest((mismatched_history,))

    assert result.questions == ()
    assert result.rejections[0].code == "NO_SUCCESSFUL_VALIDATION_HISTORY"
