from __future__ import annotations

import hashlib
import json
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.sources.document import (
    DoclingDependencyInfo,
    DocumentProcessingShape,
    DocumentSourceMetadata,
    DocumentSourcePolicy,
)
from app.sources.document.ingestion import (
    DocumentImportApplication,
    DocumentImportBatchState,
    DocumentIngestionApplication,
    DocumentKnowledgeHandoffStatus,
    DocumentPreviewLocationKind,
    DocumentUserPreflightStatus,
)


AVAILABLE = DoclingDependencyInfo(
    available=True,
    version="2.119.0",
    module_file="E:/runtime/docling/__init__.py",
)
FIXTURES = Path(__file__).parent / "fixtures" / "document_learning"


class _Value:
    def __init__(self, value: str):
        self.value = value


class _Ref:
    def __init__(self, value: str):
        self.cref = value


@dataclass
class _Provenance:
    page_no: int | None
    bbox: Any = None
    charspan: tuple[int, int] = (0, 40)


@dataclass
class _Item:
    self_ref: str
    label: Any
    text: str
    parent: Any = None
    level: int | None = None
    prov: tuple[Any, ...] = ()
    content_layer: Any = _Value("body")


class _Document:
    name = "document-import-fixture"

    def __init__(self, *, page_no: int | None = None) -> None:
        self.page_no = page_no

    def iterate_items(self, with_groups: bool = False):
        assert with_groups is True
        provenance = (_Provenance(self.page_no),)
        yield _Item(
            "#/texts/0",
            _Value("section_header"),
            "Exercises",
            parent=_Ref("#/body"),
            level=1,
            prov=provenance,
        ), 1
        yield _Item(
            "#/texts/1",
            _Value("text"),
            "Question: Which token is local?",
            parent=_Ref("#/texts/0"),
            prov=provenance,
        ), 1
        yield _Item(
            "#/texts/2",
            _Value("text"),
            "Answer: LOCAL-ONLY is the source token.",
            parent=_Ref("#/texts/0"),
            prov=provenance,
        ), 1
        yield _Item(
            "#/texts/3",
            _Value("text"),
            "Question: Which answer still needs verification?",
            parent=_Ref("#/texts/0"),
            prov=provenance,
        ), 1


class _SuccessResult:
    status = _Value("success")
    errors: tuple[Any, ...] = ()

    def __init__(self, *, page_no: int | None = None) -> None:
        self.document = _Document(page_no=page_no)


class _CrashError:
    error_class = "DoclingWorkerExecutionError"
    error_code = "DOCLING_WORKER_CRASHED"
    retryable = True


class _FailureResult:
    status = _Value("failure")
    document = None
    errors = (_CrashError(),)


class _Converter:
    def __init__(self, *, fail_names: set[str] | None = None) -> None:
        self.fail_names = fail_names or set()
        self.calls: list[list[str]] = []

    def convert_all(self, source: list[Path], **kwargs: Any):
        self.calls.append([path.name for path in source])
        for path in source:
            if path.name in self.fail_names:
                yield _FailureResult()
            else:
                yield _SuccessResult(
                    page_no=2 if path.suffix.casefold() == ".pptx" else None
                )


class _BlockingConverter(_Converter):
    def __init__(self) -> None:
        super().__init__()
        self.second_started = threading.Event()
        self.release_second = threading.Event()

    def convert_all(self, source: list[Path], **kwargs: Any):
        self.calls.append([path.name for path in source])
        for path in source:
            if path.name == "second.md":
                self.second_started.set()
                assert self.release_second.wait(timeout=10)
            yield _SuccessResult()


class _BatchPort:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def submit_batch(self, handoff: Any) -> str:
        self.calls.append(handoff)
        return handoff.handoff_id


def _application(
    root: Path,
    converter: _Converter,
    *,
    policy: DocumentSourcePolicy | None = None,
) -> DocumentIngestionApplication:
    return DocumentIngestionApplication(
        policy or DocumentSourcePolicy(input_root=root.resolve()),
        dependency_probe=lambda: AVAILABLE,
        converter_factory=lambda _policy: converter,
    )


def test_user_preflight_dto_is_safe_format_aware_and_selectable(tmp_path: Path) -> None:
    markdown = tmp_path / "lesson.md"
    markdown.write_text("# Exercises\n\nQuestion: Why?", encoding="utf-8")
    pdf = tmp_path / "lesson.pdf"
    shutil.copy2(FIXTURES / "learning-corpus.pdf", pdf)
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("plain text", encoding="utf-8")
    invalid_docx = tmp_path / "broken.docx"
    invalid_docx.write_bytes(b"not-an-office-archive")
    policy = DocumentSourcePolicy(
        input_root=tmp_path.resolve(),
        shape_medium_units=100,
        shape_large_units=300,
    )
    api = DocumentImportApplication(
        _application(tmp_path, _Converter(), policy=policy),
        state_root=(tmp_path / "state").resolve(),
    )

    selection = api.select(
        (markdown, pdf, unsupported, invalid_docx),
        metadata_by_path={markdown: DocumentSourceMetadata(section_count=400)},
    )
    assert selection.preflight_complete is False

    result = api.preflight(selection.selection_id)

    by_name = {item.display_name: item for item in result.candidates}
    assert by_name["lesson.md"].status is DocumentUserPreflightStatus.PROCESSABLE
    assert by_name["lesson.md"].processing_shape is DocumentProcessingShape.LARGE
    assert by_name["lesson.md"].selectable is True
    assert by_name["lesson.pdf"].status is DocumentUserPreflightStatus.LIMITED
    assert by_name["lesson.pdf"].format_label == "PDF"
    assert by_name["lesson.pdf"].selectable is False
    assert "PDF 需要已配置的本地解析模型" in " ".join(
        by_name["lesson.pdf"].limitation_messages
    )
    assert by_name["notes.txt"].status is DocumentUserPreflightStatus.UNPROCESSABLE
    assert by_name["notes.txt"].reason_code == "DOCUMENT_FORMAT_UNSUPPORTED"
    assert "Docling" not in (by_name["notes.txt"].reason_message or "")
    assert by_name["broken.docx"].reason_code == "DOCUMENT_BYTES_INVALID"
    assert result.selected_file_ids == (by_name["lesson.md"].file_id,)


def test_five_format_fixture_corpus_is_immutable_and_preflights_locally(
    tmp_path: Path,
) -> None:
    manifest = json.loads(
        (FIXTURES / "corpus-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema"] == "mentorflow.document-learning-fixture-corpus.v1"
    assert manifest["network_required"] is False
    assert {item["format"] for item in manifest["files"]} == {
        "pdf",
        "docx",
        "pptx",
        "html",
        "markdown",
    }
    for item in manifest["files"]:
        source = FIXTURES / item["file"]
        assert source.stat().st_size == item["bytes"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"]

    input_root = (tmp_path / "input").resolve()
    shutil.copytree(FIXTURES, input_root)
    artifacts = (tmp_path / "artifacts").resolve()
    model_root = artifacts / "docling-project--docling-layout-heron"
    model_root.mkdir(parents=True)
    (model_root / "model.safetensors").write_bytes(b"local-read-only-fixture")
    policy = DocumentSourcePolicy(
        input_root=input_root,
        pdf_artifacts_path=artifacts,
    )
    api = DocumentImportApplication(
        _application(input_root, _Converter(), policy=policy),
        state_root=(tmp_path / "state").resolve(),
    )
    sources = tuple(input_root / item["file"] for item in manifest["files"])

    preflight = api.preflight(api.select(sources).selection_id)

    assert {candidate.format_label for candidate in preflight.candidates} == {
        "PDF",
        "DOCX",
        "PPTX",
        "HTML",
        "Markdown",
    }
    by_format = {candidate.format_label: candidate for candidate in preflight.candidates}
    assert by_format["PDF"].status is DocumentUserPreflightStatus.LIMITED
    assert set(by_format["PDF"].limitation_codes) == {
        "PDF_OCR_DISABLED",
        "PDF_TABLE_MODEL_DISABLED",
    }
    assert all(
        candidate.selectable and candidate.conversion_ready
        for candidate in preflight.candidates
    )
    assert all(
        candidate.status is DocumentUserPreflightStatus.PROCESSABLE
        for label, candidate in by_format.items()
        if label != "PDF"
    )
    assert len(preflight.selected_file_ids) == 5
    api.close()


def test_batch_progress_partial_failure_and_previews_remain_per_file(
    tmp_path: Path,
) -> None:
    markdown = tmp_path / "lesson.md"
    markdown.write_text("# Exercises\n\nlesson", encoding="utf-8")
    slides = tmp_path / "slides.pptx"
    shutil.copy2(FIXTURES / "learning-corpus.pptx", slides)
    failing = tmp_path / "broken.md"
    failing.write_text("# Exercises\n\nbroken", encoding="utf-8")
    converter = _Converter(fail_names={"broken.md"})
    port = _BatchPort()
    api = DocumentImportApplication(
        _application(tmp_path, converter),
        state_root=(tmp_path / "state").resolve(),
        knowledge_pipeline_port=port,
    )
    selection = api.select((markdown, slides, failing))
    preflight = api.preflight(selection.selection_id)

    started = api.start_batch(
        selection.selection_id,
        selected_file_ids=preflight.selected_file_ids,
        conversion_batch_size=1,
    )
    result = api.wait(started.batch_id)

    assert result is not None
    assert result.state is DocumentImportBatchState.PARTIAL
    assert result.progress.total_files == 3
    assert result.progress.completed_files == 3
    assert result.progress.failed_files == 1
    assert result.progress.evidence_count > 0
    assert "fraction" not in result.progress.to_dict()
    assert result.knowledge_handoff_status is DocumentKnowledgeHandoffStatus.SUCCESS
    assert len(port.calls) == 1
    by_name = {item.source_file: item for item in result.files}
    assert by_name["broken.md"].failure_code == "DOCLING_WORKER_CRASHED"
    assert by_name["broken.md"].failure_reason == (
        "该文件的本地解析进程异常退出，可单独重试"
    )
    assert by_name["lesson.md"].evidence_count > 0
    assert by_name["slides.pptx"].evidence_count > 0
    assert any(
        item.source_file == "slides.pptx"
        and item.location_kind is DocumentPreviewLocationKind.SLIDE
        and item.location_label == "幻灯片 2"
        for item in result.provenance_preview
    )
    assert any(
        item.source_file == "lesson.md"
        and item.location_kind is DocumentPreviewLocationKind.SECTION
        for item in result.provenance_preview
    )
    assert any(
        item.verification_status.value == "NEEDS_ANSWER_VERIFICATION"
        and not item.answer_candidates
        and not item.source_generated
        for item in result.exercise_preview
    )
    api.close()


def test_cancel_then_fresh_application_resume_skips_completed_documents(
    tmp_path: Path,
) -> None:
    sources = tuple(tmp_path / name for name in ("first.md", "second.md", "third.md"))
    for index, source in enumerate(sources):
        source.write_text(f"# Exercises\n\nfixture {index}", encoding="utf-8")
    state_root = (tmp_path / "state").resolve()
    blocking = _BlockingConverter()
    first_api = DocumentImportApplication(
        _application(tmp_path, blocking),
        state_root=state_root,
    )
    selection = first_api.select(sources)
    preflight = first_api.preflight(selection.selection_id)
    started = first_api.start_batch(
        selection.selection_id,
        selected_file_ids=preflight.selected_file_ids,
        conversion_batch_size=1,
    )
    assert blocking.second_started.wait(timeout=10)

    cancelling = first_api.cancel(started.batch_id)
    assert cancelling.state is DocumentImportBatchState.CANCELLING
    blocking.release_second.set()
    cancelled = first_api.wait(started.batch_id)

    assert cancelled is not None
    assert cancelled.state is DocumentImportBatchState.CANCELLED
    assert cancelled.progress.cancelled_files == 1
    assert [item.source_file for item in cancelled.files if item.status == "CANCELLED"] == [
        "third.md"
    ]
    first_api.close()

    replay_converter = _Converter()
    resumed_api = DocumentImportApplication(
        _application(tmp_path, replay_converter),
        state_root=state_root,
    )
    resumed = resumed_api.resume(started.batch_id)
    replay = resumed_api.wait(resumed.batch_id)

    assert replay is not None
    assert replay.state is DocumentImportBatchState.COMPLETED
    assert replay.progress.attempt == 2
    assert replay.resumed_file_count == 2
    assert replay_converter.calls == [["third.md"]]
    assert [item.resumed for item in replay.files] == [True, True, False]
    resumed_api.close()
