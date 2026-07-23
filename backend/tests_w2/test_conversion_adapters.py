from __future__ import annotations

import io

import fitz
from docx import Document

import app.conversion_adapters as conversion_module
from app.conversion_adapters import (
    CancellationToken,
    ConversionBudget,
    ConversionRegistry,
    ConversionSource,
    DocxAdapter,
    HtmlAdapter,
    MarkdownAdapter,
    PdfAdapter,
    PlainTextAdapter,
)


def _pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def _docx_bytes(title: str, text: str) -> bytes:
    document = Document()
    document.add_heading(title, level=1)
    document.add_paragraph(text)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def test_all_native_adapters_implement_the_uniform_contract() -> None:
    adapters = (
        PlainTextAdapter(),
        MarkdownAdapter(),
        HtmlAdapter(),
        PdfAdapter(),
        DocxAdapter(),
    )
    for adapter in adapters:
        assert adapter.name
        assert adapter.version == "1.0.0"
        assert callable(adapter.can_handle)
        assert callable(adapter.convert)
        assert callable(adapter.metadata)
        assert callable(adapter.health)
        assert callable(adapter.failure)
        assert adapter.health().adapter == adapter.name
        assert adapter.health().version == adapter.version


def test_registry_converts_text_markdown_and_html_with_metadata() -> None:
    registry = ConversionRegistry()
    fixtures = (
        ConversionSource(name="notes.txt", data="plain evidence".encode()),
        ConversionSource(name="guide.md", data="# Graphs\n\nBreadth first search.".encode()),
        ConversionSource(
            name="lesson.html",
            data=b"<html><script>ignore()</script><h1>Trees</h1><p>Root and child nodes.</p></html>",
        ),
    )
    results = [registry.convert(source) for source in fixtures]

    assert [result.status for result in results] == ["ready", "ready", "ready"]
    assert [result.metadata.adapter for result in results] == [
        "native-text",
        "native-markdown",
        "native-html",
    ]
    assert results[1].parts[0].heading == "Graphs"
    assert results[2].parts[0].heading == "Trees"
    assert "ignore()" not in results[2].parts[0].text
    assert all(result.metadata.source_sha256 for result in results)
    assert all(result.failure is None for result in results)


def test_pdf_and_docx_use_the_same_result_contract() -> None:
    registry = ConversionRegistry()
    pdf = registry.convert(ConversionSource(name="lesson.pdf", data=_pdf_bytes("PDF evidence")))
    docx = registry.convert(
        ConversionSource(
            name="lesson.docx",
            data=_docx_bytes("DOCX heading", "DOCX evidence"),
        )
    )

    assert pdf.status == "ready"
    assert pdf.metadata.adapter == "native-pdf"
    assert pdf.metadata.page_count == 1
    assert pdf.parts[0].page_start == 1
    assert "PDF evidence" in pdf.parts[0].text

    assert docx.status == "ready"
    assert docx.metadata.adapter == "native-docx"
    assert docx.parts[0].heading == "DOCX heading"
    assert "DOCX evidence" in docx.parts[0].text


def test_input_and_output_budgets_fail_explicitly() -> None:
    registry = ConversionRegistry()
    source = ConversionSource(name="large.txt", data=b"123456")

    input_failure = registry.convert_safe(
        source,
        budget=ConversionBudget(max_input_bytes=5, max_output_chars=100, timeout_seconds=1),
    )
    assert input_failure.status == "failed"
    assert input_failure.failure.code == "INPUT_BUDGET_EXCEEDED"
    assert input_failure.failure.details["max_input_bytes"] == 5

    output_failure = registry.convert_safe(
        source,
        budget=ConversionBudget(max_input_bytes=100, max_output_chars=5, timeout_seconds=1),
    )
    assert output_failure.status == "failed"
    assert output_failure.failure.code == "OUTPUT_BUDGET_EXCEEDED"
    assert output_failure.failure.details["max_output_chars"] == 5


def test_time_budget_failure_is_retryable_and_structured(monkeypatch) -> None:
    clock = iter((0.0, 2.0))
    monkeypatch.setattr(conversion_module.time, "monotonic", lambda: next(clock))

    result = ConversionRegistry().convert_safe(
        ConversionSource(name="notes.txt", data=b"evidence"),
        budget=ConversionBudget(
            max_input_bytes=100,
            max_output_chars=100,
            timeout_seconds=1,
        ),
    )

    assert result.status == "failed"
    assert result.failure.code == "CONVERSION_TIMEOUT"
    assert result.failure.retryable is True


def test_cancellation_disabled_adapter_and_unsupported_format_are_explicit() -> None:
    token = CancellationToken()
    token.cancel()
    cancelled = ConversionRegistry().convert_safe(
        ConversionSource(name="notes.txt", data=b"evidence"),
        cancellation=token,
    )
    assert cancelled.status == "failed"
    assert cancelled.failure.code == "CONVERSION_CANCELLED"

    disabled = ConversionRegistry(disabled=("native-markdown",)).convert_safe(
        ConversionSource(name="notes.md", data=b"# Evidence")
    )
    assert disabled.status == "failed"
    assert disabled.failure.code == "ADAPTER_DISABLED"
    assert disabled.failure.adapter == "native-markdown"
    assert disabled.failure.details["adapter"] == "native-markdown"

    unsupported = ConversionRegistry().convert_safe(
        ConversionSource(name="book.epub", data=b"not supported")
    )
    assert unsupported.status == "failed"
    assert unsupported.failure.code == "UNSUPPORTED_FORMAT"


def test_declared_document_with_invalid_bytes_returns_structured_failure() -> None:
    result = ConversionRegistry().convert_safe(
        ConversionSource(name="broken.docx", data=b"not-a-docx")
    )
    assert result.status == "failed"
    assert result.failure.adapter == "native-docx"
    assert result.failure.code == "SOURCE_FORMAT_INVALID"
    assert result.parts == ()
