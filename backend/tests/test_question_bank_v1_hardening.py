from __future__ import annotations

from app.teaching.question_bank_v1 import (
    QuestionAnswerKey,
    QuestionBankQuery,
    QuestionDifficulty,
    QuestionEvidenceReference,
    QuestionProvenance,
    QuestionSourceTier,
    QuestionValidationStatus,
    TrustedQuestionBankBuilder,
    build_answer_key_id,
    create_question_candidate,
)


def _candidate(
    index: int,
    *,
    prompt: str | None = None,
    answer: str | None = None,
    concept_id: str | None = None,
    source_identity: str | None = None,
    source_tier: QuestionSourceTier = QuestionSourceTier.T0_SOURCE_ORIGINAL,
    include_answer_evidence: bool = True,
):
    source = source_identity or f"source-{index}"
    resolved_answer = answer or f"answer-{index}"
    question_evidence_id = f"question-evidence:{source}"
    answer_evidence_id = f"answer-evidence:{source}"
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
        provenance_id=f"provenance:{source}:{index}",
        source_tier=source_tier,
        source_identity=source,
        source_question_id=f"source-question-{index}",
        question_evidence=(
            QuestionEvidenceReference(
                evidence_id=question_evidence_id,
                source_identity=source,
                verified=True,
            ),
        ),
        answer_evidence=(
            (
                QuestionEvidenceReference(
                    evidence_id=answer_evidence_id,
                    source_identity=source,
                    verified=True,
                ),
            )
            if include_answer_evidence
            else ()
        ),
        generator_identity=(
            "fixture-generator"
            if source_tier
            is QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED
            else None
        ),
    )
    return create_question_candidate(
        prompt=prompt or f"Scale question {index}: what is token {index}?",
        concept_ids=(concept_id or f"concept-{index % 97}",),
        difficulty=QuestionDifficulty.MEDIUM,
        source_tier=source_tier,
        answer_key=answer_key,
        provenance=(provenance,),
        validation_proof=True,
    )


def test_large_fixture_preserves_scale_and_fails_closed_on_unsafe_cases() -> None:
    baseline = [
        _candidate(
            index,
            concept_id=("long-tail-concept" if index == 999 else None),
        )
        for index in range(1_000)
    ]
    exact_duplicate = _candidate(
        10,
        prompt=baseline[10].prompt.upper(),
        answer="answer-10",
        concept_id=baseline[10].concept_ids[0],
        source_identity="duplicate-source",
    )
    conflicting_answer = _candidate(
        20,
        prompt=baseline[20].prompt,
        answer="conflicting-answer-20",
        concept_id=baseline[20].concept_ids[0],
        source_identity="conflict-source",
    )
    missing_answer_evidence = _candidate(
        1_000,
        concept_id="missing-answer-evidence-concept",
        include_answer_evidence=False,
    )
    multi_source_same_question = _candidate(
        30,
        prompt=baseline[30].prompt,
        answer="answer-30",
        concept_id=baseline[30].concept_ids[0],
        source_identity="second-source-for-question-30",
    )
    ai_generated = _candidate(
        1_001,
        concept_id="ai-generated-concept",
        source_tier=QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED,
    )
    candidates = tuple(
        [
            *baseline,
            exact_duplicate,
            conflicting_answer,
            missing_answer_evidence,
            multi_source_same_question,
            ai_generated,
        ]
    )

    forward = TrustedQuestionBankBuilder().build(candidates)
    reverse = TrustedQuestionBankBuilder().build(reversed(candidates))

    assert forward.to_dict() == reverse.to_dict()
    assert forward.receipt.input_count == 1_005
    assert forward.receipt.retained_count == 1_003
    assert forward.receipt.collapsed_count == 2
    assert forward.receipt.conflict_count == 1
    assert len(QuestionBankQuery(forward).by_concept("long-tail-concept")) == 1
    assert (
        len(
            QuestionBankQuery(forward).by_concept(
                "missing-answer-evidence-concept"
            )
        )
        == 1
    )
    unsafe = {
        item.question_id: item
        for item in forward.questions
        if item.validation_status is QuestionValidationStatus.NEEDS_VERIFICATION
    }
    assert missing_answer_evidence.question_id in unsafe
    assert ai_generated.question_id in unsafe
    conflict = forward.conflicts[0]
    assert set(conflict.question_ids) <= set(unsafe)
    merged_multi_source = next(
        item
        for item in forward.questions
        if item.fingerprint == baseline[30].fingerprint
    )
    assert {item.source_identity for item in merged_multi_source.provenance} == {
        "source-30",
        "second-source-for-question-30",
    }


def test_ai_evidence_cannot_upgrade_an_unproven_source_original() -> None:
    source_without_answer_evidence = _candidate(
        2_000,
        prompt="Shared question",
        answer="Shared answer",
        concept_id="shared-concept",
        include_answer_evidence=False,
    )
    ai_with_evidence = _candidate(
        2_001,
        prompt="Shared question",
        answer="Shared answer",
        concept_id="shared-concept",
        source_tier=QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED,
    )

    bank = TrustedQuestionBankBuilder().build(
        (source_without_answer_evidence, ai_with_evidence)
    )

    assert bank.receipt.collapsed_count == 1
    assert len(bank.questions) == 1
    assert bank.questions[0].source_tier is QuestionSourceTier.T0_SOURCE_ORIGINAL
    assert (
        bank.questions[0].validation_status
        is QuestionValidationStatus.NEEDS_VERIFICATION
    )
