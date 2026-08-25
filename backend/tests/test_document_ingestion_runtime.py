from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from app.sources.document import (
    DoclingDependencyInfo,
    DocumentEvidenceBuilder,
    DocumentFormat,
    DocumentSourcePolicy,
    DocumentSourcePreflight,
    probe_docling_dependency,
)
from app.sources.document.ingestion import (
    SYNTHETIC_BENCHMARK_DISCLAIMER,
    DocumentBatchRequest,
    DocumentBatchStatus,
    DocumentConversionIdentity,
    DocumentFingerprint,
    DocumentIngestionApplication,
    DocumentIngestionCheckpoint,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentProvenanceGate,
    DocumentProvenanceGateError,
    run_synthetic_batch_benchmark,
)


AVAILABLE = DoclingDependencyInfo(
    available=True,
    version="2.119.0",
    module_file="E:/runtime/docling/__init__.py",
)


class _Value:
    def __init__(self, value: str):
        self.value = value


class _Ref:
    def __init__(self, value: str):
        self.cref = value


class _Box:
    l = 1.0
    t = 2.0
    r = 3.0
    b = 4.0
    coord_origin = _Value("TOPLEFT")


class _Provenance:
    def __init__(self, page_no: int = 1):
        self.page_no = page_no
        self.bbox = _Box()
        self.charspan = (0, 8)


@dataclass
class _Item:
    self_ref: str
    label: Any
    text: str
    parent: Any = None
    level: int | None = None
    prov: tuple[Any, ...] = ()
    content_layer: Any = _Value("body")


class _TableItem(_Item):
    def export_to_markdown(self, document: Any) -> str:
        return self.text


class _Document:
    def __init__(
        self,
        text: str,
        *,
        page: bool = True,
        section: bool = True,
        table: bool = False,
    ) -> None:
        provenance = (_Provenance(),) if page else ()
        items: list[tuple[Any, int]] = []
        if section:
            items.append(
                (
                    _Item(
                        "#/texts/0",
                        _Value("section_header"),
                        "Section",
                        parent=_Ref("#/body"),
                        level=1,
                        prov=provenance,
                    ),
                    1,
                )
            )
        item_class = _TableItem if table else _Item
        items.append(
            (
                item_class(
                    "#/tables/0" if table else "#/texts/1",
                    _Value("table" if table else "text"),
                    text,
                    parent=_Ref("#/body"),
                    prov=provenance,
                ),
                1,
            )
        )
        self.items = items
        self.name = "fixture"

    def iterate_items(self, with_groups: bool = False):
        assert with_groups is True
        yield from self.items


class _Status:
    def __init__(self, value: str):
        self.value = value


class _ConversionError:
    pass


class _Result:
    def __init__(
        self,
        status: str,
        document: Any = None,
        errors: tuple[Any, ...] = (),
    ) -> None:
        self.status = _Status(status)
        self.document = document
        self.errors = errors


class _BatchConverter:
    def __init__(self, outcomes: dict[str, Any]):
        self.outcomes = outcomes
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def convert_all(self, source: list[Path], **kwargs: Any):
        self.calls.append(([path.name for path in source], kwargs))
        for path in source:
            outcome = self.outcomes[path.name]
            if isinstance(outcome, BaseException):
                raise outcome
            yield outcome


class _PipelinePort:
    def __init__(self, *, failure: BaseException | None = None):
        self.failure = failure
        self.calls: list[Any] = []

    def submit(self, handoff: Any) -> str:
        self.calls.append(handoff)
        if self.failure is not None:
            raise self.failure
        return handoff.handoff_id


def _write(root: Path, name: str, payload: bytes) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _policy(root: Path, **changes: Any) -> DocumentSourcePolicy:
    values: dict[str, Any] = {"input_root": root.resolve()}
    values.update(changes)
    return DocumentSourcePolicy(**values)


def _application(
    root: Path,
    converter: _BatchConverter,
    *,
    port: _PipelinePort | None = None,
    **policy_changes: Any,
) -> DocumentIngestionApplication:
    return DocumentIngestionApplication(
        _policy(root, **policy_changes),
        dependency_probe=lambda: AVAILABLE,
        converter_factory=lambda policy: converter,
        pipeline_port=port,
    )


def test_convert_all_is_used_and_bad_files_are_isolated(tmp_path: Path) -> None:
    good = _write(tmp_path, "good.md", b"# Good")
    bad = _write(tmp_path, "bad.docx", b"PK\x03\x04broken")
    partial = _write(tmp_path, "partial.html", b"<html><body>Partial</body></html>")
    audio = _write(tmp_path, "lesson.mp3", b"ID3")
    converter = _BatchConverter(
        {
            "good.md": _Result("success", _Document("Good")),
            "bad.docx": _Result("failure", errors=(_ConversionError(),)),
            "partial.html": _Result("partial_success", _Document("Partial")),
        }
    )
    port = _PipelinePort()

    result = _application(tmp_path, converter, port=port).ingest(
        DocumentBatchRequest(
            request_id="batch-isolation",
            selected_documents=(good, bad, partial, audio),
            conversion_config_identity="docling-2.119.0:local-safe",
        )
    )

    assert result.status is DocumentBatchStatus.PARTIAL
    assert [item.status for item in result.receipts] == [
        DocumentIngestionStatus.SUCCESS,
        DocumentIngestionStatus.FAILED,
        DocumentIngestionStatus.LIMITED,
        DocumentIngestionStatus.UNSUPPORTED,
    ]
    assert converter.calls == [
        (
            ["good.md", "bad.docx", "partial.html"],
            {"raises_on_error": False},
        )
    ]
    failed = result.receipts[1].failure
    assert failed is not None
    assert failed.error_class == "_ConversionError"
    assert failed.source_identity.startswith("docconv_")
    assert failed.stage is DocumentIngestionStage.CONVERSION
    assert failed.retryable is False
    unsupported = result.receipts[3].failure
    assert unsupported is not None
    assert unsupported.error_code == "DOCUMENT_FORMAT_UNSUPPORTED"
    assert len(port.calls) == 2
    assert port.calls[0].generic_evidence[-1]["page_start"] == 1


def test_five_formats_are_selected_and_audio_video_are_explicitly_rejected(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    paths = (
        _write(tmp_path, "paper.pdf", b"%PDF-1.7\nfixture"),
        _write(tmp_path, "guide.docx", b"PK\x03\x04docx"),
        _write(tmp_path, "slides.pptx", b"PK\x03\x04pptx"),
        _write(tmp_path, "page.html", b"<!doctype html><html>page</html>"),
        _write(tmp_path, "notes.md", b"# Notes"),
        _write(tmp_path, "clip.mp4", b"video"),
        _write(tmp_path, "speech.wav", b"audio"),
    )
    converter = _BatchConverter(
        {
            path.name: _Result("success", _Document(path.stem))
            for path in paths[:5]
        }
    )

    result = _application(
        tmp_path,
        converter,
        pdf_artifacts_path=artifacts.resolve(),
    ).ingest(
        DocumentBatchRequest(
            request_id="five-formats",
            selected_documents=paths,
            conversion_config_identity="five-local-formats",
        )
    )

    converted_names = [name for call, _ in converter.calls for name in call]
    assert converted_names == [path.name for path in paths[:5]]
    assert [item.status for item in result.receipts[-2:]] == [
        DocumentIngestionStatus.UNSUPPORTED,
        DocumentIngestionStatus.UNSUPPORTED,
    ]
    assert result.receipts[0].status is DocumentIngestionStatus.LIMITED
    assert "PDF_OCR_DISABLED" in result.receipts[0].limitations


def test_identity_covers_content_source_type_and_conversion_configuration() -> None:
    fingerprint = DocumentFingerprint(sha256="a" * 64, byte_count=10)

    base = DocumentConversionIdentity.create(
        fingerprint=fingerprint,
        source_type=DocumentFormat.MARKDOWN,
        conversion_config_identity="config-a",
    )
    changed_content = DocumentConversionIdentity.create(
        fingerprint=DocumentFingerprint(sha256="b" * 64, byte_count=10),
        source_type=DocumentFormat.MARKDOWN,
        conversion_config_identity="config-a",
    )
    changed_type = DocumentConversionIdentity.create(
        fingerprint=fingerprint,
        source_type=DocumentFormat.HTML,
        conversion_config_identity="config-a",
    )
    changed_config = DocumentConversionIdentity.create(
        fingerprint=fingerprint,
        source_type=DocumentFormat.MARKDOWN,
        conversion_config_identity="config-b",
    )

    assert len(
        {base.value, changed_content.value, changed_type.value, changed_config.value}
    ) == 4


def test_duplicate_content_is_skipped_and_changed_content_gets_new_identity(
    tmp_path: Path,
) -> None:
    first = _write(tmp_path, "first.md", b"# Same")
    duplicate = _write(tmp_path, "nested/duplicate.md", b"# Same")
    converter = _BatchConverter(
        {"first.md": _Result("success", _Document("Same"))}
    )
    checkpoint = DocumentIngestionCheckpoint()
    initial = _application(tmp_path, converter).ingest(
        DocumentBatchRequest(
            request_id="duplicate",
            selected_documents=(first, duplicate),
            conversion_config_identity="stable-config",
        ),
        checkpoint=checkpoint,
    )

    assert initial.receipts[1].status is DocumentIngestionStatus.SKIPPED_DUPLICATE
    assert initial.receipts[1].duplicate_of == "first.md"
    assert converter.calls[0][0] == ["first.md"]
    old_identity = initial.receipts[0].conversion_identity

    first.write_bytes(b"# Changed")
    changed_converter = _BatchConverter(
        {"first.md": _Result("success", _Document("Changed"))}
    )
    changed = _application(tmp_path, changed_converter).ingest(
        DocumentBatchRequest(
            request_id="changed",
            selected_documents=(first,),
            conversion_config_identity="stable-config",
        ),
        checkpoint=checkpoint,
    )

    assert changed_converter.calls[0][0] == ["first.md"]
    assert changed.receipts[0].conversion_identity != old_identity


def test_interrupted_batch_resumes_in_new_application_without_reconversion(
    tmp_path: Path,
) -> None:
    paths = tuple(
        _write(tmp_path, f"doc-{index}.md", f"# Doc {index}".encode())
        for index in range(3)
    )
    first_converter = _BatchConverter(
        {path.name: _Result("success", _Document(path.stem)) for path in paths}
    )
    first_port = _PipelinePort()
    checkpoint = DocumentIngestionCheckpoint()

    first = _application(tmp_path, first_converter, port=first_port).ingest(
        DocumentBatchRequest(
            request_id="interrupt",
            selected_documents=paths,
            conversion_config_identity="resume-config",
            conversion_batch_size=1,
        ),
        checkpoint=checkpoint,
        should_stop=lambda progress: progress.completed_documents == 1,
    )

    assert first.status is DocumentBatchStatus.INTERRUPTED
    assert [call[0] for call in first_converter.calls] == [["doc-0.md"]]
    assert len(checkpoint.completed) == 1

    second_converter = _BatchConverter(
        {path.name: _Result("success", _Document(path.stem)) for path in paths[1:]}
    )
    second_port = _PipelinePort()
    resumed = _application(tmp_path, second_converter, port=second_port).ingest(
        DocumentBatchRequest(
            request_id="resume",
            selected_documents=paths,
            conversion_config_identity="resume-config",
            conversion_batch_size=1,
        ),
        checkpoint=checkpoint,
    )

    assert resumed.status is DocumentBatchStatus.SUCCESS
    assert resumed.receipts[0].resumed is True
    assert resumed.receipts[0].handoff_submitted is True
    assert [call[0] for call in second_converter.calls] == [
        ["doc-1.md"],
        ["doc-2.md"],
    ]
    assert len(second_port.calls) == 2
    assert len(checkpoint.completed) == 3


def test_handoff_failure_resumes_from_converted_bundle_and_retries_only_handoff(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, "handoff.md", b"# Handoff")
    converter = _BatchConverter(
        {"handoff.md": _Result("success", _Document("Evidence"))}
    )
    checkpoint = DocumentIngestionCheckpoint()
    failed = _application(
        tmp_path,
        converter,
        port=_PipelinePort(failure=RuntimeError("provider secret")),
    ).ingest(
        DocumentBatchRequest(
            request_id="handoff-fails",
            selected_documents=(path,),
            conversion_config_identity="handoff-config",
        ),
        checkpoint=checkpoint,
    )

    assert failed.receipts[0].status is DocumentIngestionStatus.FAILED
    assert failed.receipts[0].stage is DocumentIngestionStage.HANDOFF
    assert "provider secret" not in str(failed.to_dict())
    assert len(checkpoint.completed) == 1

    forbidden_converter = _BatchConverter({})
    recovered_port = _PipelinePort()
    recovered = _application(
        tmp_path,
        forbidden_converter,
        port=recovered_port,
    ).ingest(
        DocumentBatchRequest(
            request_id="handoff-retry",
            selected_documents=(path,),
            conversion_config_identity="handoff-config",
        ),
        checkpoint=checkpoint,
    )

    assert forbidden_converter.calls == []
    assert recovered.receipts[0].resumed is True
    assert recovered.receipts[0].status is DocumentIngestionStatus.SUCCESS
    assert recovered.receipts[0].handoff_submitted is True
    assert len(recovered_port.calls) == 1


def test_batch_stream_exception_preserves_yielded_result_and_isolates_remaining(
    tmp_path: Path,
) -> None:
    paths = tuple(
        _write(tmp_path, f"stream-{index}.md", f"# {index}".encode())
        for index in range(3)
    )
    converter = _BatchConverter(
        {
            paths[0].name: _Result("success", _Document("First")),
            paths[1].name: RuntimeError("stream exploded with secret"),
            paths[2].name: _Result("success", _Document("Never consumed")),
        }
    )

    result = _application(tmp_path, converter).ingest(
        DocumentBatchRequest(
            request_id="stream-failure",
            selected_documents=paths,
            conversion_config_identity="stream-config",
        )
    )

    assert result.receipts[0].status is DocumentIngestionStatus.SUCCESS
    assert [item.status for item in result.receipts[1:]] == [
        DocumentIngestionStatus.FAILED,
        DocumentIngestionStatus.FAILED,
    ]
    assert all(
        item.failure is not None and item.failure.retryable
        for item in result.receipts[1:]
    )
    assert "stream exploded" not in str(result.to_dict())


def test_provenance_gate_requires_source_identity_and_reports_missing_precision(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, "plain.md", b"Plain")
    preflight = DocumentSourcePreflight(
        _policy(tmp_path),
        dependency_probe=lambda: AVAILABLE,
    ).inspect(path)
    assert preflight.candidate is not None
    bundle = DocumentEvidenceBuilder(docling_version="2.119.0").build(
        _Document("No page", page=False, section=False),
        preflight.candidate,
    )
    gate = DocumentProvenanceGate()

    limitations = gate.validate(bundle, source_type=DocumentFormat.MARKDOWN)

    assert "SECTION_HIERARCHY_UNAVAILABLE" in limitations
    assert "EXACT_PAGE_PROVENANCE_UNAVAILABLE" in limitations
    generic = bundle.evidence[0].to_generic_evidence()
    assert generic["page_start"] is None
    assert generic["page_end"] is None

    bad_evidence = replace(bundle.evidence[0], source_file="other.md")
    mismatched = replace(bundle, evidence=(bad_evidence,))
    with pytest.raises(DocumentProvenanceGateError) as exc_info:
        gate.validate(mismatched, source_type=DocumentFormat.MARKDOWN)
    assert exc_info.value.code == "DOCUMENT_EVIDENCE_SOURCE_MISMATCH"

    hash_mismatch = replace(
        bundle,
        evidence=(replace(bundle.evidence[0], content="tampered"),),
    )
    with pytest.raises(DocumentProvenanceGateError) as hash_error:
        gate.validate(hash_mismatch, source_type=DocumentFormat.MARKDOWN)
    assert hash_error.value.code == "DOCUMENT_EVIDENCE_HASH_MISMATCH"


def test_progress_is_monotonic_and_finishes_at_one(tmp_path: Path) -> None:
    paths = tuple(
        _write(tmp_path, f"progress-{index}.md", f"# {index}".encode())
        for index in range(3)
    )
    converter = _BatchConverter(
        {path.name: _Result("success", _Document(path.stem)) for path in paths}
    )
    observed = []

    result = _application(tmp_path, converter).ingest(
        DocumentBatchRequest(
            request_id="progress",
            selected_documents=paths,
            conversion_config_identity="progress-config",
        ),
        progress_sink=observed.append,
    )

    assert observed == list(result.progress)
    assert [item.sequence for item in observed] == list(range(len(observed)))
    completed = [item.completed_documents for item in observed]
    assert completed == sorted(completed)
    assert observed[-1].fraction == 1.0
    assert observed[-1].stage is DocumentIngestionStage.COMPLETE


def test_synthetic_benchmark_records_10_50_100_fixture_throughput(
    tmp_path: Path,
) -> None:
    def run_case(count: int):
        root = tmp_path / str(count)
        root.mkdir()
        paths = tuple(
            _write(root, f"fixture-{index}.md", f"# {index}".encode())
            for index in range(count)
        )
        converter = _BatchConverter(
            {path.name: _Result("success", _Document(path.stem)) for path in paths}
        )
        return _application(root, converter).ingest(
            DocumentBatchRequest(
                request_id=f"benchmark-{count}",
                selected_documents=paths,
                conversion_config_identity="synthetic-fixture-only",
            )
        )

    report = run_synthetic_batch_benchmark(run_case)

    assert [sample.document_count for sample in report.samples] == [10, 50, 100]
    assert report.disclaimer == SYNTHETIC_BENCHMARK_DISCLAIMER
    assert "not evidence of real PDF performance" in report.disclaimer
    for sample in report.samples:
        assert sample.preflight_documents_per_second is not None
        assert sample.preflight_documents_per_second > 0
        assert sample.conversion_fixture_documents_per_second is not None
        assert sample.evidence_items_per_second is not None


def test_real_docling_convert_all_markdown_smoke_is_local_only(tmp_path: Path) -> None:
    dependency = probe_docling_dependency()
    if not dependency.available:
        pytest.skip("Docling is not available in this test runtime")
    path = _write(tmp_path, "real.md", b"# Real\n\nLocal markdown evidence.")

    result = DocumentIngestionApplication(_policy(tmp_path)).ingest(
        DocumentBatchRequest(
            request_id="real-docling-markdown",
            selected_documents=(path,),
            conversion_config_identity=f"docling-{dependency.version}:local-no-models",
        )
    )

    assert dependency.version == "2.119.0"
    assert result.receipts[0].status in {
        DocumentIngestionStatus.SUCCESS,
        DocumentIngestionStatus.LIMITED,
    }
    assert result.receipts[0].evidence_count >= 2
    assert result.bundles[0].docling_version == "2.119.0"


def test_real_docling_convert_all_docx_pptx_preserves_table_and_slide_provenance(
    tmp_path: Path,
) -> None:
    dependency = probe_docling_dependency()
    if not dependency.available:
        pytest.skip("Docling is not available in this test runtime")
    docx = pytest.importorskip("docx")
    pptx = pytest.importorskip("pptx")

    docx_path = tmp_path / "real.docx"
    document = docx.Document()
    document.add_heading("DOCX Heading", level=1)
    document.add_paragraph("DOCX paragraph")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    table.cell(1, 0).text = "1"
    table.cell(1, 1).text = "2"
    document.save(docx_path)

    pptx_path = tmp_path / "real.pptx"
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "PPTX Heading"
    slide.placeholders[1].text = "PPTX body"
    presentation.save(pptx_path)
    port = _PipelinePort()

    result = DocumentIngestionApplication(
        _policy(tmp_path),
        pipeline_port=port,
    ).ingest(
        DocumentBatchRequest(
            request_id="real-docling-office",
            selected_documents=(docx_path, pptx_path),
            conversion_config_identity=f"docling-{dependency.version}:local-office",
        )
    )

    assert len(result.bundles) == 2
    docx_kinds = {item.kind.value for item in result.bundles[0].evidence}
    assert {"heading", "text", "table"}.issubset(docx_kinds)
    assert any(item.content == "PPTX Heading" for item in result.bundles[1].evidence)
    assert any(
        provenance.page_no == 1
        for item in result.bundles[1].evidence
        for provenance in item.provenance
    )
    assert len(port.calls) == 2
    assert all(handoff.generic_evidence for handoff in port.calls)


def test_real_docling_complex_docx_table_maps_as_structured_evidence(
    tmp_path: Path,
) -> None:
    dependency = probe_docling_dependency()
    if not dependency.available:
        pytest.skip("Docling is not available in this test runtime")
    docx = pytest.importorskip("docx")

    path = tmp_path / "complex-table.docx"
    document = docx.Document()
    document.add_heading("Quarterly Metrics", level=1)
    table = document.add_table(rows=4, cols=3)
    table.cell(0, 0).merge(table.cell(0, 2)).text = "FY 2026"
    for column, value in enumerate(("Metric", "Q1", "Q2")):
        table.cell(1, column).text = value
    for column, value in enumerate(("Revenue", "100", "120")):
        table.cell(2, column).text = value
    for column, value in enumerate(("Retention", "91%", "93%")):
        table.cell(3, column).text = value
    document.save(path)

    result = DocumentIngestionApplication(_policy(tmp_path)).ingest(
        DocumentBatchRequest(
            request_id="real-complex-table",
            selected_documents=(path,),
            conversion_config_identity=f"docling-{dependency.version}:complex-table",
        )
    )

    tables = [item for item in result.bundles[0].evidence if item.kind.value == "table"]
    assert len(tables) == 1
    mapped = tables[0]
    assert mapped.item_id.startswith("#/tables/")
    assert all(value in mapped.content for value in ("FY 2026", "Revenue", "93%"))
    generic = mapped.to_generic_evidence()
    assert generic["document_kind"] == "table"
    assert generic["document_item_id"] == mapped.item_id
    assert generic["quote"] == mapped.content


def test_real_docling_convert_all_keeps_markdown_when_corrupt_docx_fails(
    tmp_path: Path,
) -> None:
    dependency = probe_docling_dependency()
    if not dependency.available:
        pytest.skip("Docling is not available in this test runtime")
    good = _write(tmp_path, "good.md", b"# Good\n\nEvidence remains available.")
    corrupt = _write(tmp_path, "corrupt.docx", b"PK\x03\x04broken-package")

    result = DocumentIngestionApplication(_policy(tmp_path)).ingest(
        DocumentBatchRequest(
            request_id="real-corrupt-isolation",
            selected_documents=(good, corrupt),
            conversion_config_identity=f"docling-{dependency.version}:corrupt-isolation",
        )
    )

    assert result.status is DocumentBatchStatus.PARTIAL
    assert result.receipts[0].status in {
        DocumentIngestionStatus.SUCCESS,
        DocumentIngestionStatus.LIMITED,
    }
    assert result.receipts[1].status is DocumentIngestionStatus.FAILED
    assert result.receipts[1].failure is not None
    assert result.receipts[1].failure.stage is DocumentIngestionStage.CONVERSION
    assert {bundle.source_file for bundle in result.bundles} == {"good.md"}
