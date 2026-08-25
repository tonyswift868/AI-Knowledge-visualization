from __future__ import annotations

from app.sources.document import (
    DOCUMENT_ADAPTER_NAME,
    DOCUMENT_ADAPTER_VERSION,
    DOCUMENT_EVIDENCE_SCHEMA,
    DocumentEvidence,
    DocumentEvidenceBundle,
    DocumentEvidenceKind,
    DocumentItemProvenance,
)
from app.sources.document.contracts import sha256_bytes, sha256_json
from app.sources.document.ingestion import (
    DocumentExerciseCandidateStatus,
    DocumentExerciseExtractor,
)


def _evidence(
    ordinal: int,
    content: str,
    *,
    kind: DocumentEvidenceKind = DocumentEvidenceKind.TEXT,
    page: int | None = 1,
    section: tuple[str, ...] = ("Exercises",),
) -> DocumentEvidence:
    content_hash = sha256_bytes(content.encode("utf-8"))
    return DocumentEvidence(
        evidence_id=f"docev_{ordinal}",
        source_id="docsrc_source",
        source_file="course.pdf",
        source_sha256="a" * 64,
        item_id=f"#/texts/{ordinal}",
        parent_item_id="#/body",
        ordinal=ordinal,
        hierarchy_level=1,
        kind=kind,
        content=content,
        content_hash=content_hash,
        section_path=section,
        content_layer="body",
        provenance=(
            DocumentItemProvenance(
                page_no=page,
                bbox=None,
                char_start=0,
                char_end=len(content),
            ),
        ),
    )


def _bundle(*evidence: DocumentEvidence) -> DocumentEvidenceBundle:
    payload = [item.to_dict() for item in evidence]
    bundle_hash = sha256_json(payload)
    return DocumentEvidenceBundle(
        schema=DOCUMENT_EVIDENCE_SCHEMA,
        adapter_name=DOCUMENT_ADAPTER_NAME,
        adapter_version=DOCUMENT_ADAPTER_VERSION,
        docling_version="2.119.0",
        bundle_id=f"docbundle_{bundle_hash[:32]}",
        bundle_hash=bundle_hash,
        source_id="docsrc_source",
        source_file="course.pdf",
        source_sha256="a" * 64,
        document_name="course.pdf",
        evidence=tuple(evidence),
    )


def test_explicit_question_and_answer_become_provenance_bound_t0_candidate() -> None:
    question = _evidence(1, "Question: Why is the sky blue?", page=3)
    answer = _evidence(2, "Answer: Light scattering makes it appear blue.", page=4)

    candidates = DocumentExerciseExtractor().extract(_bundle(question, answer))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.status is DocumentExerciseCandidateStatus.T0_CANDIDATE
    assert candidate.candidate_tier == "T0"
    assert candidate.source_generated is False
    assert candidate.source_file == "course.pdf"
    assert candidate.question_docling_item_id == question.item_id
    assert candidate.question_evidence_id == question.evidence_id
    assert candidate.question_page_numbers == (3,)
    assert candidate.question_section_path == ("Exercises",)
    assert candidate.answer is not None
    assert candidate.answer.docling_item_id == answer.item_id
    assert candidate.answer.evidence_id == answer.evidence_id
    assert candidate.answer.page_numbers == (4,)


def test_question_without_source_answer_needs_verification_and_is_never_trusted() -> None:
    question = _evidence(1, "Exercise: Prove the stated theorem.", page=5)

    candidate = DocumentExerciseExtractor().extract(_bundle(question))[0]

    assert candidate.status is DocumentExerciseCandidateStatus.NEEDS_ANSWER_VERIFICATION
    assert candidate.answer is None
    assert "TRUSTED" not in str(candidate.to_dict())


def test_exercise_section_first_item_is_candidate_without_inventing_text() -> None:
    prompt = _evidence(
        1,
        "Calculate the derivative of x squared.",
        section=("Example Problems",),
    )
    ordinary = _evidence(
        2,
        "This paragraph provides background only.",
        section=("Theory",),
    )

    candidates = DocumentExerciseExtractor().extract(_bundle(prompt, ordinary))

    assert len(candidates) == 1
    assert candidates[0].question == prompt.content
    assert candidates[0].extraction_basis == "EXERCISE_SECTION_FIRST_ITEM"
    assert candidates[0].source_generated is False


def test_source_qa_table_yields_paired_and_unanswered_candidates() -> None:
    table = _evidence(
        1,
        "| Question | Answer |\n"
        "|---|---|\n"
        "| What is 2 + 2? | 4 |\n"
        "| Name the source code. | |",
        kind=DocumentEvidenceKind.TABLE,
        page=7,
        section=("Q&A",),
    )

    candidates = DocumentExerciseExtractor().extract(_bundle(table))

    assert len(candidates) == 2
    assert candidates[0].question == "What is 2 + 2?"
    assert candidates[0].answer is not None
    assert candidates[0].answer.content == "4"
    assert candidates[0].answer.page_numbers == (7,)
    assert candidates[1].question == "Name the source code."
    assert (
        candidates[1].status
        is DocumentExerciseCandidateStatus.NEEDS_ANSWER_VERIFICATION
    )
    assert candidates[1].answer is None


def test_candidate_ids_are_deterministic_and_ordinary_prose_is_ignored() -> None:
    question = _evidence(1, "Quiz: Select the correct option.")
    answer = _evidence(2, "Explanation: Option B is stated in the source.")
    prose = _evidence(3, "This is a declarative background paragraph.", section=("Intro",))
    bundle = _bundle(question, answer, prose)
    extractor = DocumentExerciseExtractor()

    first = extractor.extract(bundle)
    second = extractor.extract(bundle)

    assert [item.candidate_id for item in first] == [item.candidate_id for item in second]
    assert len(first) == 1
    assert first[0].answer is not None
