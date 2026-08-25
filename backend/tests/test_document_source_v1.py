from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.sources.document import (
    DoclingDependencyInfo,
    DoclingDocumentSource,
    DocumentBatchStatus,
    DocumentEvidenceBuildError,
    DocumentEvidenceBuilder,
    DocumentEvidenceKind,
    DocumentFileStatus,
    DocumentFormat,
    DocumentPreflightStatus,
    DocumentProcessingShape,
    DocumentSourceMetadata,
    DocumentSourcePolicy,
    DocumentSourcePreflight,
    build_docling_converter,
    probe_docling_dependency,
)


AVAILABLE = DoclingDependencyInfo(
    available=True,
    version="2.test",
    module_file="E:/runtime/docling/__init__.py",
)
UNAVAILABLE = DoclingDependencyInfo(
    available=False,
    version=None,
    error_code="DOCLING_UNAVAILABLE",
)
FIXED_TIME = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def _policy(root: Path, **changes: Any) -> DocumentSourcePolicy:
    values = {
        "input_root": root.resolve(),
        "shape_medium_file_bytes": 64,
        "shape_large_file_bytes": 1024,
        "shape_medium_units": 10,
        "shape_large_units": 100,
    }
    values.update(changes)
    return DocumentSourcePolicy(**values)


def _preflight(root: Path, **policy_changes: Any) -> DocumentSourcePreflight:
    return DocumentSourcePreflight(
        _policy(root, **policy_changes),
        dependency_probe=lambda: AVAILABLE,
    )


def _write_fixture(root: Path, name: str, payload: bytes) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


@pytest.mark.parametrize(
    ("name", "payload", "format_"),
    [
        ("guide.docx", b"PK\x03\x04word", DocumentFormat.DOCX),
        ("slides.pptx", b"PK\x03\x04ppt", DocumentFormat.PPTX),
        ("page.html", b"<!doctype html><html><body>ok</body></html>", DocumentFormat.HTML),
        ("notes.md", b"# Notes\n\nBody", DocumentFormat.MARKDOWN),
    ],
)
def test_preflight_supports_v1_non_pdf_formats(
    tmp_path: Path,
    name: str,
    payload: bytes,
    format_: DocumentFormat,
) -> None:
    path = _write_fixture(tmp_path, name, payload)

    result = _preflight(tmp_path).inspect(path)

    assert result.status is DocumentPreflightStatus.SUPPORTED
    assert result.conversion_ready is True
    assert result.candidate is not None
    assert result.candidate.format is format_
    assert result.candidate.source_file == name
    assert result.candidate.source_sha256
    assert result.estimate is not None


def test_pdf_preflight_is_model_download_safe_and_artifact_driven(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path, "paper.pdf", b"%PDF-1.7\nbody")
    without_artifacts = _preflight(tmp_path).inspect(
        path,
        metadata=DocumentSourceMetadata(page_count=12),
    )

    assert without_artifacts.status is DocumentPreflightStatus.SUPPORTED_WITH_LIMITATION
    assert without_artifacts.conversion_ready is False
    assert without_artifacts.estimate is not None
    assert without_artifacts.estimate.unit_kind == "pages"
    assert without_artifacts.estimate.unit_count == 12
    assert "PDF_OCR_DISABLED" in without_artifacts.limitations
    assert "PDF_LOCAL_ARTIFACTS_UNAVAILABLE" in without_artifacts.limitations

    artifacts = tmp_path / "docling-artifacts"
    artifacts.mkdir()
    with_artifacts = _preflight(
        tmp_path,
        pdf_artifacts_path=artifacts.resolve(),
        pdf_table_structure_enabled=True,
    ).inspect(path)
    assert with_artifacts.status is DocumentPreflightStatus.SUPPORTED_WITH_LIMITATION
    assert with_artifacts.conversion_ready is True
    assert "PDF_LOCAL_ARTIFACTS_UNAVAILABLE" not in with_artifacts.limitations
    assert "PDF_TABLE_MODEL_DISABLED" not in with_artifacts.limitations


def test_dependency_unavailable_is_distinct_from_unsupported_format(tmp_path: Path) -> None:
    markdown = _write_fixture(tmp_path, "notes.md", b"# Notes")
    unknown = _write_fixture(tmp_path, "clip.mp4", b"not-video")
    preflight = DocumentSourcePreflight(
        _policy(tmp_path),
        dependency_probe=lambda: UNAVAILABLE,
    )

    missing = preflight.inspect(markdown)
    unsupported = preflight.inspect(unknown)

    assert missing.status is DocumentPreflightStatus.DEPENDENCY_UNAVAILABLE
    assert missing.error_code == "DOCLING_UNAVAILABLE"
    assert unsupported.status is DocumentPreflightStatus.REJECTED
    assert unsupported.error_code == "DOCUMENT_FORMAT_UNSUPPORTED"


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("bad.pdf", b"not a pdf"),
        ("bad.docx", b"not a zip"),
        ("bad.pptx", b"not a zip"),
        ("bad.html", b"plain text without html"),
        ("bad.md", b"\x00binary"),
    ],
)
def test_preflight_rejects_declared_format_byte_mismatches(
    tmp_path: Path,
    name: str,
    payload: bytes,
) -> None:
    result = _preflight(tmp_path).inspect(_write_fixture(tmp_path, name, payload))

    assert result.status is DocumentPreflightStatus.REJECTED
    assert result.error_code == "DOCUMENT_BYTES_INVALID"


def test_preflight_limits_and_processing_shape_are_caller_configured(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path, "large.md", b"# H\n" + b"x" * 120)
    warning = _preflight(
        tmp_path,
        warning_file_bytes=64,
        hard_max_file_bytes=1024,
        warning_sections=10,
        hard_max_sections=100,
    ).inspect(path, metadata=DocumentSourceMetadata(section_count=20))

    assert warning.status is DocumentPreflightStatus.SUPPORTED_WITH_LIMITATION
    assert warning.limitations == ("FILE_SIZE_WARNING", "UNIT_COUNT_WARNING")
    assert warning.estimate is not None
    assert warning.estimate.shape is DocumentProcessingShape.MEDIUM

    rejected = _preflight(tmp_path, hard_max_file_bytes=32).inspect(path)
    assert rejected.status is DocumentPreflightStatus.REJECTED
    assert rejected.error_code == "DOCUMENT_FILE_SIZE_LIMIT"

    page_limited = _write_fixture(tmp_path, "many.pdf", b"%PDF-1.7\nbody")
    pages = _preflight(tmp_path, hard_max_pages=5).inspect(
        page_limited,
        metadata=DocumentSourceMetadata(page_count=6),
    )
    assert pages.error_code == "DOCUMENT_UNIT_COUNT_LIMIT"


def test_input_root_blocks_parent_escape_absolute_escape_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = _write_fixture(tmp_path, "outside.md", b"# Outside")
    preflight = _preflight(root)

    parent_escape = preflight.inspect(Path("..") / "outside.md")
    absolute_escape = preflight.inspect(outside)

    assert parent_escape.error_code == "DOCUMENT_PATH_OUTSIDE_INPUT_ROOT"
    assert absolute_escape.error_code == "DOCUMENT_PATH_OUTSIDE_INPUT_ROOT"

    link = root / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        return
    assert preflight.inspect(link).error_code == "DOCUMENT_PATH_OUTSIDE_INPUT_ROOT"


def test_windows_nested_paths_are_normalized_and_source_ids_are_deterministic(
    tmp_path: Path,
) -> None:
    path = _write_fixture(tmp_path, "folder/notes.md", b"# Stable")
    preflight = _preflight(tmp_path)

    first = preflight.inspect(path)
    second = preflight.inspect(Path("folder") / "notes.md")

    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.source_file == "folder/notes.md"
    assert first.candidate.source_id == second.candidate.source_id


def test_markdown_sniff_accepts_utf8_codepoint_split_at_read_boundary(tmp_path: Path) -> None:
    payload = b"# H\n" + b"a" * (8191 - 4) + "你".encode("utf-8") + b"tail"
    path = _write_fixture(tmp_path, "boundary.md", payload)

    result = _preflight(tmp_path).inspect(path)

    assert result.status is DocumentPreflightStatus.SUPPORTED


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
    def __init__(self, *, page_no: int = 1, charspan: tuple[int, int] = (0, 5)):
        self.page_no = page_no
        self.bbox = _Box()
        self.charspan = charspan


@dataclass
class _Item:
    self_ref: str
    label: Any
    text: str | None = None
    parent: Any = None
    level: int | None = None
    prov: tuple[Any, ...] = ()
    content_layer: Any = _Value("body")


class _TableItem(_Item):
    def __init__(self, self_ref: str, markdown: str, **kwargs: Any):
        super().__init__(self_ref=self_ref, label=_Value("table"), **kwargs)
        self.markdown = markdown

    def export_to_markdown(self, document: Any) -> str:
        assert document is not None
        return self.markdown


class _Document:
    def __init__(self, items: list[tuple[Any, int]], name: str = "fixture"):
        self.items = items
        self.name = name

    def iterate_items(self, with_groups: bool = False):
        assert with_groups is True
        yield from self.items

    def export_to_markdown(self) -> str:
        raise AssertionError("Evidence mapping must not flatten the whole document")


def _candidate(tmp_path: Path, name: str = "source.md", payload: bytes = b"# Source"):
    path = _write_fixture(tmp_path, name, payload)
    result = _preflight(tmp_path).inspect(path)
    assert result.candidate is not None
    return result.candidate


def _structured_document() -> _Document:
    return _Document(
        [
            (
                _Item(
                    "#/texts/0",
                    _Value("title"),
                    "Course",
                    parent=_Ref("#/body"),
                    prov=(_Provenance(page_no=1, charspan=(0, 6)),),
                ),
                1,
            ),
            (
                _Item(
                    "#/texts/1",
                    _Value("section_header"),
                    "Topic",
                    parent=_Ref("#/body"),
                    level=2,
                    prov=(_Provenance(page_no=2, charspan=(0, 5)),),
                ),
                1,
            ),
            (
                _Item(
                    "#/texts/2",
                    _Value("text"),
                    "Evidence paragraph",
                    parent=_Ref("#/body"),
                    prov=(
                        _Provenance(page_no=2, charspan=(0, 9)),
                        _Provenance(page_no=3, charspan=(9, 18)),
                    ),
                ),
                1,
            ),
            (
                _TableItem(
                    "#/tables/0",
                    "| A | B |\n|---|---|\n| 1 | 2 |",
                    parent=_Ref("#/body"),
                    prov=(_Provenance(page_no=3, charspan=(0, 9)),),
                ),
                1,
            ),
        ]
    )


def test_evidence_builder_preserves_structure_tables_and_provenance(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    builder = DocumentEvidenceBuilder(docling_version="2.test")

    bundle = builder.build(_structured_document(), candidate)

    assert [item.kind for item in bundle.evidence] == [
        DocumentEvidenceKind.HEADING,
        DocumentEvidenceKind.HEADING,
        DocumentEvidenceKind.TEXT,
        DocumentEvidenceKind.TABLE,
    ]
    paragraph = bundle.evidence[2]
    table = bundle.evidence[3]
    assert paragraph.section_path == ("Course", "Topic")
    assert paragraph.item_id == "#/texts/2"
    assert paragraph.parent_item_id == "#/body"
    assert paragraph.provenance[0].page_no == 2
    assert paragraph.provenance[1].page_no == 3
    assert paragraph.provenance[0].bbox is not None
    assert paragraph.provenance[0].bbox.coord_origin == "TOPLEFT"
    assert table.content.startswith("| A | B |")
    generic = table.to_generic_evidence()
    assert generic["source_file"] == "source.md"
    assert generic["page_start"] == 3
    assert generic["document_item_id"] == "#/tables/0"
    assert generic["document_provenance"][0]["char_end"] == 9
    from app.knowledge_map_v1 import EvidenceSegmentV1

    validated = EvidenceSegmentV1.model_validate(generic)
    assert validated.chunk_id == table.evidence_id


def test_evidence_ids_and_bundle_hash_are_deterministic(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    builder = DocumentEvidenceBuilder(docling_version="2.test")

    first = builder.build(_structured_document(), candidate)
    second = builder.build(_structured_document(), candidate)

    assert first.bundle_id == second.bundle_id
    assert first.bundle_hash == second.bundle_hash
    assert [item.evidence_id for item in first.evidence] == [
        item.evidence_id for item in second.evidence
    ]

    changed = _structured_document()
    changed.items[2][0].text = "Changed paragraph"
    third = builder.build(changed, candidate)
    assert third.evidence[2].evidence_id != first.evidence[2].evidence_id


def test_duplicate_docling_item_identity_fails_closed(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    document = _Document(
        [
            (_Item("#/texts/0", _Value("text"), "One"), 1),
            (_Item("#/texts/0", _Value("text"), "Two"), 1),
        ]
    )

    with pytest.raises(DocumentEvidenceBuildError) as exc_info:
        DocumentEvidenceBuilder(docling_version="2.test").build(document, candidate)

    assert exc_info.value.code == "DUPLICATE_DOCUMENT_ITEM_ID"


def test_large_synthetic_document_structure_maps_without_losing_identity(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    document = _Document(
        [
            (
                _Item(
                    f"#/texts/{index}",
                    _Value("text"),
                    f"Paragraph {index}",
                    prov=(_Provenance(page_no=index // 50 + 1),),
                ),
                1,
            )
            for index in range(5_000)
        ],
        name="large",
    )

    bundle = DocumentEvidenceBuilder(docling_version="2.test").build(document, candidate)

    assert len(bundle.evidence) == 5_000
    assert len({item.evidence_id for item in bundle.evidence}) == 5_000
    assert bundle.evidence[-1].item_id == "#/texts/4999"
    assert bundle.evidence[-1].provenance[0].page_no == 100


class _FakeStatus:
    def __init__(self, value: str):
        self.value = value


class _FakeError:
    def __init__(self, message: str):
        self.error_message = message


class _FakeResult:
    def __init__(
        self,
        status: str,
        document: Any = None,
        errors: tuple[Any, ...] = (),
    ):
        self.status = _FakeStatus(status)
        self.document = document
        self.errors = errors


class _FakeConverter:
    def __init__(self, outcomes: dict[str, Any]):
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def convert(self, source: Path, **kwargs: Any) -> Any:
        self.calls.append((source.name, kwargs))
        outcome = self.outcomes[source.name]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _text_document(text: str, *, name: str = "doc") -> _Document:
    return _Document([(_Item("#/texts/0", _Value("text"), text), 1)], name=name)


class _ExplodingTable(_TableItem):
    def export_to_markdown(self, document: Any) -> str:
        raise RuntimeError("secret=table-secret mapping failed")


def test_batch_preserves_success_when_other_files_fail_or_are_partial(tmp_path: Path) -> None:
    good = _write_fixture(tmp_path, "good.md", b"# Good")
    bad = _write_fixture(tmp_path, "bad.md", b"# Bad")
    partial = _write_fixture(tmp_path, "partial.md", b"# Partial")
    converter = _FakeConverter(
        {
            "good.md": _FakeResult("success", _text_document("Good evidence")),
            "bad.md": _FakeResult(
                "failure",
                errors=(
                    _FakeError(
                        "token=super-secret https://private.example/file "
                        r"C:\Users\person\secret.docx"
                    ),
                ),
            ),
            "partial.md": _FakeResult("partial_success", _text_document("Partial evidence")),
        }
    )
    source = DoclingDocumentSource(
        _policy(tmp_path),
        dependency_probe=lambda: AVAILABLE,
        converter_factory=lambda policy: converter,
        clock=lambda: FIXED_TIME,
    )

    result = source.convert_batch((good, bad, partial))

    assert result.receipt.status is DocumentBatchStatus.PARTIAL_SUCCESS
    assert result.receipt.success_count == 1
    assert result.receipt.partial_count == 1
    assert result.receipt.failure_count == 1
    assert result.receipt.evidence_count == 2
    assert len(result.bundles) == 2
    failed = result.receipt.files[1]
    assert failed.status is DocumentFileStatus.CONVERSION_FAILED
    assert failed.error_code == "DOCLING_CONVERSION_FAILED"
    assert "super-secret" not in (failed.error_message or "")
    assert "private.example" not in (failed.error_message or "")
    assert "C:\\Users" not in (failed.error_message or "")
    assert result.receipt.files[2].error_code == "DOCLING_PARTIAL_SUCCESS"
    assert result.receipt.files[2].error_message == "Docling reported partial conversion"
    assert "super-secret" not in result.receipt.to_json()
    assert [name for name, _ in converter.calls] == ["good.md", "bad.md", "partial.md"]


def test_duplicate_content_is_converted_once_and_receipted(tmp_path: Path) -> None:
    first = _write_fixture(tmp_path, "first.md", b"# Same")
    second = _write_fixture(tmp_path, "nested/second.md", b"# Same")
    converter = _FakeConverter(
        {
            "first.md": _FakeResult("success", _text_document("Same evidence")),
        }
    )
    source = DoclingDocumentSource(
        _policy(tmp_path),
        dependency_probe=lambda: AVAILABLE,
        converter_factory=lambda policy: converter,
        clock=lambda: FIXED_TIME,
    )

    result = source.convert_batch((first, second))

    assert result.receipt.status is DocumentBatchStatus.PARTIAL_SUCCESS
    assert result.receipt.success_count == 1
    assert result.receipt.duplicate_count == 1
    assert result.receipt.files[1].status is DocumentFileStatus.DUPLICATE
    assert result.receipt.files[1].duplicate_of == "first.md"
    assert len(converter.calls) == 1


def test_unsupported_file_does_not_discard_supported_batch_peer(tmp_path: Path) -> None:
    good = _write_fixture(tmp_path, "good.md", b"# Good")
    unsupported = _write_fixture(tmp_path, "audio.mp3", b"ID3-not-accepted")
    converter = _FakeConverter(
        {"good.md": _FakeResult("success", _text_document("Good evidence"))}
    )
    source = DoclingDocumentSource(
        _policy(tmp_path),
        dependency_probe=lambda: AVAILABLE,
        converter_factory=lambda policy: converter,
        clock=lambda: FIXED_TIME,
    )

    result = source.convert_batch((good, unsupported))

    assert result.receipt.status is DocumentBatchStatus.PARTIAL_SUCCESS
    assert result.receipt.success_count == 1
    assert result.receipt.failure_count == 1
    assert result.receipt.files[1].status is DocumentFileStatus.REJECTED
    assert result.receipt.files[1].error_code == "DOCUMENT_FORMAT_UNSUPPORTED"
    assert len(result.bundles) == 1
    assert [name for name, _ in converter.calls] == ["good.md"]


def test_dependency_and_pdf_artifact_failures_never_construct_converter(tmp_path: Path) -> None:
    markdown = _write_fixture(tmp_path, "notes.md", b"# Notes")
    pdf = _write_fixture(tmp_path, "paper.pdf", b"%PDF-1.7\nbody")
    calls = 0

    def forbidden_factory(policy: DocumentSourcePolicy):
        nonlocal calls
        calls += 1
        raise AssertionError("converter must not be constructed")

    unavailable = DoclingDocumentSource(
        _policy(tmp_path),
        dependency_probe=lambda: UNAVAILABLE,
        converter_factory=forbidden_factory,
        clock=lambda: FIXED_TIME,
    ).convert_one(markdown)
    no_artifacts = DoclingDocumentSource(
        _policy(tmp_path),
        dependency_probe=lambda: AVAILABLE,
        converter_factory=forbidden_factory,
        clock=lambda: FIXED_TIME,
    ).convert_one(pdf)

    assert unavailable.receipt.files[0].status is DocumentFileStatus.DEPENDENCY_UNAVAILABLE
    assert no_artifacts.receipt.files[0].status is DocumentFileStatus.CONVERSION_FAILED
    assert "PDF_LOCAL_ARTIFACTS_UNAVAILABLE" in no_artifacts.receipt.files[0].limitations
    assert calls == 0


def test_corrupt_conversion_and_exception_are_isolated(tmp_path: Path) -> None:
    corrupt = _write_fixture(tmp_path, "corrupt.docx", b"PK\x03\x04broken")
    exploding = _write_fixture(tmp_path, "exploding.md", b"# Explode")
    converter = _FakeConverter(
        {
            "corrupt.docx": _FakeResult("failure", errors=(_FakeError("corrupt package"),)),
            "exploding.md": RuntimeError("password=hunter2 conversion crashed"),
        }
    )
    source = DoclingDocumentSource(
        _policy(tmp_path),
        dependency_probe=lambda: AVAILABLE,
        converter_factory=lambda policy: converter,
        clock=lambda: FIXED_TIME,
    )

    result = source.convert_batch((corrupt, exploding))

    assert result.receipt.status is DocumentBatchStatus.FAILURE
    assert result.receipt.failure_count == 2
    assert result.receipt.files[0].error_code == "DOCLING_CONVERSION_FAILED"
    assert result.receipt.files[1].error_code == "DOCLING_CONVERSION_EXCEPTION"
    assert "hunter2" not in result.receipt.to_json()


def test_evidence_mapping_failure_is_isolated_and_redacted(tmp_path: Path) -> None:
    broken = _write_fixture(tmp_path, "broken.md", b"# Broken")
    good = _write_fixture(tmp_path, "good.md", b"# Good")
    broken_document = _Document(
        [(_ExplodingTable("#/tables/0", "unused"), 1)],
    )
    converter = _FakeConverter(
        {
            "broken.md": _FakeResult("success", broken_document),
            "good.md": _FakeResult("success", _text_document("Good evidence")),
        }
    )
    source = DoclingDocumentSource(
        _policy(tmp_path),
        dependency_probe=lambda: AVAILABLE,
        converter_factory=lambda policy: converter,
        clock=lambda: FIXED_TIME,
    )

    result = source.convert_batch((broken, good))

    assert result.receipt.status is DocumentBatchStatus.PARTIAL_SUCCESS
    assert result.receipt.files[0].error_code == "DOCUMENT_EVIDENCE_MAPPING_FAILED"
    assert "table-secret" not in result.receipt.to_json()
    assert result.receipt.files[1].status is DocumentFileStatus.SUCCESS


def test_single_file_receipt_is_deterministic_and_json_serializable(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path, "one.md", b"# One")

    def make_source() -> DoclingDocumentSource:
        converter = _FakeConverter(
            {"one.md": _FakeResult("success", _text_document("One evidence"))}
        )
        return DoclingDocumentSource(
            _policy(tmp_path),
            dependency_probe=lambda: AVAILABLE,
            converter_factory=lambda policy: converter,
            clock=lambda: FIXED_TIME,
        )

    first = make_source().convert_one(path)
    second = make_source().convert_one(path)

    assert first.receipt.status is DocumentBatchStatus.SUCCESS
    assert first.receipt.receipt_id == second.receipt.receipt_id
    assert first.receipt.created_at == "2026-08-25T08:00:00Z"
    assert json.loads(first.receipt.to_json())["evidence_count"] == 1


def test_batch_limit_is_config_driven_and_attempts_no_conversion(tmp_path: Path) -> None:
    first = _write_fixture(tmp_path, "one.md", b"# One")
    second = _write_fixture(tmp_path, "two.md", b"# Two")
    source = DoclingDocumentSource(
        _policy(tmp_path, max_batch_files=1),
        dependency_probe=lambda: AVAILABLE,
        converter_factory=lambda policy: pytest.fail("converter should not be built"),
        clock=lambda: FIXED_TIME,
    )

    result = source.convert_batch((first, second))

    assert result.receipt.status is DocumentBatchStatus.FAILURE
    assert result.receipt.failure_count == 2
    assert {item.error_code for item in result.receipt.files} == {"DOCUMENT_BATCH_SIZE_LIMIT"}


def test_converter_configuration_excludes_audio_video_ocr_vlm_and_remote_services(
    tmp_path: Path,
) -> None:
    pytest.importorskip("docling")
    from docling.datamodel.base_models import InputFormat

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    converter = build_docling_converter(
        _policy(
            tmp_path,
            pdf_artifacts_path=artifacts.resolve(),
            pdf_table_structure_enabled=True,
        )
    )

    assert set(converter.allowed_formats) == {
        InputFormat.PDF,
        InputFormat.DOCX,
        InputFormat.PPTX,
        InputFormat.HTML,
        InputFormat.MD,
    }
    assert InputFormat.AUDIO not in converter.allowed_formats
    assert InputFormat.VIDEO not in converter.allowed_formats
    options = converter.format_to_options[InputFormat.PDF].pipeline_options
    assert options.artifacts_path == artifacts.resolve()
    assert options.enable_remote_services is False
    assert options.allow_external_plugins is False
    assert options.do_ocr is False
    assert options.do_table_structure is True
    assert options.do_picture_classification is False
    assert options.do_picture_description is False
    assert options.do_chart_extraction is False
    html_options = converter.format_to_options[InputFormat.HTML].backend_options
    assert html_options.enable_remote_fetch is False
    assert html_options.enable_local_fetch is False
    assert html_options.render_page is False
    assert html_options.fetch_images is False
    assert converter.initialized_pipelines == {}


def test_real_docling_markdown_and_html_conversion_preserve_generic_evidence(
    tmp_path: Path,
) -> None:
    dependency = probe_docling_dependency()
    if not dependency.available:
        pytest.skip("Docling is not available in this runtime")
    markdown = _write_fixture(
        tmp_path,
        "real.md",
        b"# Heading\n\nParagraph.\n\n| A | B |\n|---|---|\n| 1 | 2 |",
    )
    html = _write_fixture(
        tmp_path,
        "real.html",
        b"<!doctype html><html><body><h1>HTML Heading</h1><p>Body</p></body></html>",
    )
    source = DoclingDocumentSource(
        _policy(tmp_path),
        dependency_probe=lambda: dependency,
        clock=lambda: FIXED_TIME,
    )

    result = source.convert_batch((markdown, html))

    assert result.receipt.status is DocumentBatchStatus.SUCCESS
    assert result.receipt.success_count == 2
    assert len(result.bundles) == 2
    markdown_kinds = {item.kind for item in result.bundles[0].evidence}
    assert DocumentEvidenceKind.HEADING in markdown_kinds
    assert DocumentEvidenceKind.TEXT in markdown_kinds
    assert DocumentEvidenceKind.TABLE in markdown_kinds
    assert all(
        item.to_generic_evidence()["source_file"] in {"real.md", "real.html"}
        for bundle in result.bundles
        for item in bundle.evidence
    )


def test_real_docling_docx_and_pptx_conversion_preserve_office_structure(tmp_path: Path) -> None:
    dependency = probe_docling_dependency()
    if not dependency.available:
        pytest.skip("Docling is not available in this runtime")
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

    source = DoclingDocumentSource(
        _policy(tmp_path),
        dependency_probe=lambda: dependency,
        clock=lambda: FIXED_TIME,
    )
    result = source.convert_batch(
        (docx_path, pptx_path),
        metadata_by_path={
            docx_path: DocumentSourceMetadata(section_count=2, table_count=1),
            pptx_path: DocumentSourceMetadata(slide_count=1),
        },
    )

    assert result.receipt.status is DocumentBatchStatus.SUCCESS
    assert result.receipt.success_count == 2
    assert {bundle.source_file for bundle in result.bundles} == {"real.docx", "real.pptx"}
    docx_kinds = {item.kind for item in result.bundles[0].evidence}
    assert DocumentEvidenceKind.HEADING in docx_kinds
    assert DocumentEvidenceKind.TEXT in docx_kinds
    assert DocumentEvidenceKind.TABLE in docx_kinds
    assert any(item.content == "PPTX Heading" for item in result.bundles[1].evidence)
    assert any(
        provenance.page_no == 1
        for item in result.bundles[1].evidence
        for provenance in item.provenance
    )


def test_real_docling_corrupt_docx_becomes_a_file_receipt_failure(tmp_path: Path) -> None:
    dependency = probe_docling_dependency()
    if not dependency.available:
        pytest.skip("Docling is not available in this runtime")
    corrupt = _write_fixture(tmp_path, "corrupt.docx", b"PK\x03\x04broken-package")
    source = DoclingDocumentSource(
        _policy(tmp_path),
        dependency_probe=lambda: dependency,
        clock=lambda: FIXED_TIME,
    )

    result = source.convert_one(corrupt)

    assert result.receipt.status is DocumentBatchStatus.FAILURE
    assert result.receipt.files[0].status is DocumentFileStatus.CONVERSION_FAILED
    assert result.receipt.files[0].error_code in {
        "DOCLING_CONVERSION_EXCEPTION",
        "DOCLING_CONVERSION_FAILED",
    }
