from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.sources.document import (
    DOCUMENT_ADAPTER_NAME,
    DOCUMENT_ADAPTER_VERSION,
    DOCUMENT_EVIDENCE_SCHEMA,
    DoclingDependencyInfo,
    DocumentEvidence,
    DocumentEvidenceBundle,
    DocumentEvidenceKind,
    DocumentFormat,
    DocumentItemProvenance,
    DocumentSourcePolicy,
)
from app.sources.document.contracts import sha256_bytes, sha256_json
from app.sources.document.ingestion import (
    DocumentAnswerCandidate,
    DocumentBatchRequest,
    DocumentBatchSubmissionStatus,
    DocumentCheckpointIntegrityError,
    DocumentCheckpointStore,
    DocumentExerciseCandidate,
    DocumentExerciseCandidateStatus,
    DocumentExerciseCorpusBuilder,
    DocumentIngestionApplication,
    DocumentLearningCorpusRuntime,
    DocumentProvenanceQualityReporter,
    DocumentReviewContentFeedBuilder,
)


AVAILABLE = DoclingDependencyInfo(
    available=True,
    version="2.119.0",
    module_file="E:/runtime/docling/__init__.py",
)


def _answer(identity: str, content: str) -> DocumentAnswerCandidate:
    return DocumentAnswerCandidate(
        content=content,
        source_file="lesson.md",
        docling_item_id=f"#/texts/{identity}",
        evidence_id=f"answer-{identity}",
        page_numbers=(2,),
        section_path=("Exercises",),
        match_basis="FOLLOWING_EXPLICIT_SOURCE_ANSWER",
    )


def _candidate(
    identity: str,
    *,
    question: str = "Question: Why?",
    answer: DocumentAnswerCandidate | None = None,
) -> DocumentExerciseCandidate:
    return DocumentExerciseCandidate(
        candidate_id=f"candidate-{identity}",
        candidate_tier="T0",
        status=(
            DocumentExerciseCandidateStatus.T0_CANDIDATE
            if answer is not None
            else DocumentExerciseCandidateStatus.NEEDS_ANSWER_VERIFICATION
        ),
        question=question,
        source_id="source-1",
        source_revision="a" * 64,
        source_file="lesson.md",
        bundle_id="bundle-1",
        question_docling_item_id="#/texts/question",
        question_evidence_id="question-evidence",
        question_page_numbers=(1,),
        question_section_path=("Exercises",),
        answer=answer,
        extraction_basis="EXPLICIT_SOURCE_QUESTION_MARKER",
    )


def _evidence(
    identity: str,
    *,
    source_file: str,
    kind: DocumentEvidenceKind = DocumentEvidenceKind.TEXT,
    page: int | None = None,
    section: tuple[str, ...] = (),
) -> DocumentEvidence:
    content = f"Evidence {identity}"
    return DocumentEvidence(
        evidence_id=f"evidence-{identity}",
        source_id=f"source-{source_file}",
        source_file=source_file,
        source_sha256="b" * 64,
        item_id=f"#/texts/{identity}",
        parent_item_id="#/body",
        ordinal=0,
        hierarchy_level=1,
        kind=kind,
        content=content,
        content_hash=sha256_bytes(content.encode("utf-8")),
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


def _bundle(source_file: str, *evidence: DocumentEvidence) -> DocumentEvidenceBundle:
    bundle_hash = sha256_json([item.to_dict() for item in evidence])
    return DocumentEvidenceBundle(
        schema=DOCUMENT_EVIDENCE_SCHEMA,
        adapter_name=DOCUMENT_ADAPTER_NAME,
        adapter_version=DOCUMENT_ADAPTER_VERSION,
        docling_version="2.119.0",
        bundle_id=f"bundle-{source_file}",
        bundle_hash=bundle_hash,
        source_id=f"source-{source_file}",
        source_file=source_file,
        source_sha256="b" * 64,
        document_name=source_file,
        evidence=tuple(evidence),
    )


def test_corpus_groups_sections_multiple_answers_unanswered_and_concept_hints() -> None:
    first = _candidate("first", answer=_answer("first", "Answer: One"))
    second = _candidate("second", answer=_answer("second", "Answer: Two"))
    unanswered = _candidate("third", question="Exercise: Explain the seam.")

    corpus = DocumentExerciseCorpusBuilder().build(
        (first, second, unanswered),
        concept_hints_by_candidate={
            first.candidate_id: ("runtime", "local inference"),
            second.candidate_id: ("runtime",),
            unanswered.candidate_id: ("seam",),
        },
    )

    assert len(corpus.entries) == 2
    paired = next(entry for entry in corpus.entries if entry.answers)
    pending = next(entry for entry in corpus.entries if not entry.answers)
    assert paired.has_multiple_answers is True
    assert [answer.content for answer in paired.answers] == [
        "Answer: One",
        "Answer: Two",
    ]
    assert paired.concept_hints == ("runtime", "local inference")
    assert len(paired.source_lineage) == 2
    assert pending.status is DocumentExerciseCandidateStatus.NEEDS_ANSWER_VERIFICATION
    assert pending.concept_hints == ("seam",)
    assert len(corpus.section_groups) == 1
    assert corpus.section_groups[0].entry_ids == tuple(
        entry.entry_id for entry in corpus.entries
    )
    assert corpus.unanswered_count == 1
    assert corpus.multiple_answer_count == 1
    assert "TRUSTED" not in str(corpus.to_dict())


def test_quality_report_counts_real_locations_without_inventing_pages() -> None:
    corpus = DocumentExerciseCorpusBuilder().build(
        (_candidate("paired", answer=_answer("paired", "Answer: source")),)
    )
    bundles = (
        _bundle("paper.pdf", _evidence("pdf", source_file="paper.pdf", page=3)),
        _bundle(
            "slides.pptx",
            _evidence("slide", source_file="slides.pptx", page=2),
        ),
        _bundle(
            "page.html",
            _evidence(
                "table",
                source_file="page.html",
                kind=DocumentEvidenceKind.TABLE,
                section=("Q&A",),
            ),
        ),
        _bundle("notes.md", _evidence("unknown", source_file="notes.md")),
    )

    report = DocumentProvenanceQualityReporter().build(
        bundles,
        source_formats={
            "paper.pdf": DocumentFormat.PDF,
            "slides.pptx": DocumentFormat.PPTX,
            "page.html": DocumentFormat.HTML,
            "notes.md": DocumentFormat.MARKDOWN,
        },
        corpus=corpus,
    )

    assert report.evidence_count == 4
    assert report.exact_page_count == 1
    assert report.slide_count == 1
    assert report.section_only_count == 1
    assert report.unknown_precise_location_count == 1
    assert report.table_source_count == 1
    assert report.linked_answer_count == 1
    notes = next(item for item in report.sources if item.source_file == "notes.md")
    assert notes.exact_page_count == 0
    assert notes.unknown_precise_location_count == 1


def test_quality_report_counts_table_sources_not_table_items() -> None:
    corpus = DocumentExerciseCorpusBuilder().build(())
    bundle = _bundle(
        "tables.html",
        _evidence(
            "table-one",
            source_file="tables.html",
            kind=DocumentEvidenceKind.TABLE,
            section=("Tables",),
        ),
        _evidence(
            "table-two",
            source_file="tables.html",
            kind=DocumentEvidenceKind.TABLE,
            section=("Tables",),
        ),
    )

    report = DocumentProvenanceQualityReporter().build(
        (bundle,),
        source_formats={"tables.html": DocumentFormat.HTML},
        corpus=corpus,
    )

    assert report.evidence_count == 2
    assert report.table_source_count == 1
    assert report.sources[0].table_source_count == 1


def test_review_feed_is_endpoint_neutral_and_preserves_lineage() -> None:
    corpus = DocumentExerciseCorpusBuilder().build(
        (_candidate("paired", answer=_answer("paired", "Answer: source")),)
    )

    feed = DocumentReviewContentFeedBuilder().build(corpus)

    assert len(feed.items) == 1
    assert feed.items[0].corpus_entry_id == corpus.entries[0].entry_id
    assert feed.items[0].source_lineage == corpus.entries[0].source_lineage
    assert feed.items[0].answers[0].evidence_id == "answer-paired"
    assert "W5" not in str(feed.to_dict())


class _Value:
    def __init__(self, value: str):
        self.value = value


class _Ref:
    def __init__(self, value: str):
        self.cref = value


class _Provenance:
    page_no = 1
    bbox = None
    charspan = (0, 20)


@dataclass
class _Item:
    self_ref: str
    label: Any
    text: str
    parent: Any = None
    level: int | None = None
    prov: tuple[Any, ...] = (_Provenance(),)
    content_layer: Any = _Value("body")


class _Document:
    name = "runtime-fixture"

    def iterate_items(self, with_groups: bool = False):
        assert with_groups is True
        yield _Item(
            "#/texts/0",
            _Value("section_header"),
            "Exercises",
            parent=_Ref("#/body"),
            level=1,
        ), 1
        yield _Item(
            "#/texts/1",
            _Value("text"),
            "Question: What is restored?",
            parent=_Ref("#/body"),
        ), 1
        yield _Item(
            "#/texts/2",
            _Value("text"),
            "Answer: Completed document evidence.",
            parent=_Ref("#/body"),
        ), 1


class _Result:
    status = _Value("success")
    document = _Document()
    errors: tuple[Any, ...] = ()


class _WorkerCrashError:
    error_class = "DoclingWorkerExecutionError"
    error_code = "DOCLING_WORKER_CRASHED"
    retryable = True


class _WorkerCrashResult:
    status = _Value("failure")
    document = None
    errors = (_WorkerCrashError(),)


class _Converter:
    def __init__(self, *, forbidden: bool = False) -> None:
        self.forbidden = forbidden
        self.calls: list[list[str]] = []

    def convert_all(self, source: list[Path], **kwargs: Any):
        if self.forbidden:
            raise AssertionError("restored document must not be reconverted")
        self.calls.append([path.name for path in source])
        yield from (_Result() for _path in source)


class _CrashMiddleConverter:
    def __init__(self, *, crash_middle: bool) -> None:
        self.crash_middle = crash_middle
        self.calls: list[list[str]] = []

    def convert_all(self, source: list[Path], **kwargs: Any):
        self.calls.append([path.name for path in source])
        for path in source:
            if self.crash_middle and path.name == "middle.md":
                yield _WorkerCrashResult()
            else:
                yield _Result()


class _BatchPort:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def submit_batch(self, handoff: Any) -> str:
        self.calls.append(handoff)
        return handoff.handoff_id


def _application(root: Path, converter: _Converter) -> DocumentIngestionApplication:
    return DocumentIngestionApplication(
        DocumentSourcePolicy(input_root=root.resolve()),
        dependency_probe=lambda: AVAILABLE,
        converter_factory=lambda policy: converter,
    )


def test_runtime_persists_checkpoint_restores_and_submits_one_batch(
    tmp_path: Path,
) -> None:
    source = tmp_path / "lesson.md"
    source.write_text("# Exercises", encoding="utf-8")
    checkpoint_path = (tmp_path / "state" / "checkpoint.json").resolve()
    store = DocumentCheckpointStore(checkpoint_path)
    converter = _Converter()
    port = _BatchPort()
    request = DocumentBatchRequest(
        request_id="document-learning-runtime",
        selected_documents=(source,),
        conversion_config_identity="runtime-v1",
    )

    first = DocumentLearningCorpusRuntime(
        _application(tmp_path, converter),
        checkpoint_store=store,
        knowledge_pipeline_port=port,
    ).run(request)

    assert converter.calls == [["lesson.md"]]
    assert checkpoint_path.is_file()
    assert first.checkpoint_hash is not None
    assert len(first.exercise_corpus.entries) == 1
    assert first.provenance_quality.exact_page_count == 3
    assert len(first.review_feed.items) == 1
    assert len(first.knowledge_handoff.document_handoffs) == 1
    assert first.submission_receipt.status is DocumentBatchSubmissionStatus.SUCCESS
    assert len(port.calls) == 1

    replay_port = _BatchPort()
    replay = DocumentLearningCorpusRuntime(
        _application(tmp_path, _Converter(forbidden=True)),
        checkpoint_store=store,
        knowledge_pipeline_port=replay_port,
    ).run(request)

    assert replay.ingestion.receipts[0].resumed is True
    assert replay.exercise_corpus.corpus_id == first.exercise_corpus.corpus_id
    assert replay.provenance_quality.report_id == first.provenance_quality.report_id
    assert len(replay_port.calls) == 1


def test_worker_crash_checkpoint_preserves_completed_docs_and_replays_only_failure(
    tmp_path: Path,
) -> None:
    sources = tuple(tmp_path / name for name in ("first.md", "middle.md", "last.md"))
    for index, source in enumerate(sources):
        source.write_text(f"# Exercises\n\nfixture {index}", encoding="utf-8")
    checkpoint_path = (tmp_path / "state" / "crash-checkpoint.json").resolve()
    store = DocumentCheckpointStore(checkpoint_path)
    request = DocumentBatchRequest(
        request_id="document-worker-crash-resume",
        selected_documents=sources,
        conversion_config_identity="runtime-crash-v1",
        conversion_batch_size=3,
    )
    crashing_converter = _CrashMiddleConverter(crash_middle=True)

    first = DocumentLearningCorpusRuntime(
        _application(tmp_path, crashing_converter),
        checkpoint_store=store,
    ).run(request)

    assert crashing_converter.calls == [["first.md", "middle.md", "last.md"]]
    assert [receipt.status.value for receipt in first.ingestion.receipts] == [
        "SUCCESS",
        "FAILED",
        "SUCCESS",
    ]
    failed = first.ingestion.receipts[1]
    assert failed.failure is not None
    assert failed.failure.error_code == "DOCLING_WORKER_CRASHED"
    assert failed.failure.retryable is True
    assert len(store.load().completed) == 2

    replay_converter = _CrashMiddleConverter(crash_middle=False)
    replay = DocumentLearningCorpusRuntime(
        _application(tmp_path, replay_converter),
        checkpoint_store=store,
    ).run(request)

    assert replay_converter.calls == [["middle.md"]]
    assert [receipt.resumed for receipt in replay.ingestion.receipts] == [
        True,
        False,
        True,
    ]
    assert all(
        receipt.status.value == "SUCCESS" for receipt in replay.ingestion.receipts
    )
    assert len(store.load().completed) == 3


def test_checkpoint_store_rejects_modified_payload(tmp_path: Path) -> None:
    path = (tmp_path / "checkpoint.json").resolve()
    store = DocumentCheckpointStore(path)
    runtime_checkpoint = store.load()
    store.save(runtime_checkpoint)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["checkpoint"]["schema"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DocumentCheckpointIntegrityError, match="hash mismatch"):
        store.load()
