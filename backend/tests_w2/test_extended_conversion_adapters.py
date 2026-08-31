from __future__ import annotations

import io
import zipfile

import fitz
from openpyxl import Workbook
from pptx import Presentation

from app.conversion_adapters import (
    AudioTranscriptionAdapter,
    ConversionBudget,
    ConversionRegistry,
    ConversionSource,
    ImageOcrAdapter,
    PptxAdapter,
)


def _zip_bytes(files: dict[str, str | bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, value in files.items():
            archive.writestr(name, value.encode() if isinstance(value, str) else value)
    return output.getvalue()


def _pptx_bytes() -> bytes:
    presentation = Presentation()
    for title, body in (
        ("Graph Search", "Breadth first search uses a queue."),
        ("Complexity", "O(V + E)"),
    ):
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Scores"
    sheet.append(("Name", "Score"))
    sheet.append(("Ada", 42))
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _epub_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="OPS/package.opf"
                media-type="application/oebps-package+xml"/></rootfiles>
            </container>""",
        )
        archive.writestr(
            "OPS/package.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
            <package version="3.0" unique-identifier="book-id" xmlns="http://www.idpf.org/2007/opf">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:identifier id="book-id">urn:test:queues</dc:identifier>
                <dc:title>Queues</dc:title><dc:language>en</dc:language>
              </metadata>
              <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
              <spine><itemref idref="chapter"/></spine>
            </package>""",
        )
        archive.writestr(
            "OPS/chapter.xhtml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <html xmlns="http://www.w3.org/1999/xhtml"><head><title>Queues</title></head>
              <body><h1>Queues</h1><p>First in, first out.</p><script>ignore()</script></body>
            </html>""",
        )
    return output.getvalue()


def test_pptx_xlsx_and_epub_real_in_memory_samples_preserve_structure() -> None:
    registry = ConversionRegistry()
    pptx = registry.convert(ConversionSource(name="lesson.pptx", data=_pptx_bytes()))
    xlsx = registry.convert(ConversionSource(name="scores.xlsx", data=_xlsx_bytes()))
    epub = registry.convert(ConversionSource(name="book.epub", data=_epub_bytes()))

    assert [pptx.status, xlsx.status, epub.status] == ["ready", "ready", "ready"]
    assert [pptx.metadata.adapter, xlsx.metadata.adapter, epub.metadata.adapter] == [
        "docling-document",
        "docling-document",
        "docling-document",
    ]
    pptx_text = "\n".join(part.text for part in pptx.parts)
    assert "Graph Search" in pptx_text
    assert "Breadth first search uses a queue." in pptx_text
    assert "Complexity" in pptx_text
    assert "O(V + E)" in pptx_text

    xlsx_text = "\n".join(part.text for part in xlsx.parts)
    assert "Name" in xlsx_text
    assert "Score" in xlsx_text
    assert "Ada" in xlsx_text
    assert "42" in xlsx_text

    epub_text = "\n".join(part.text for part in epub.parts)
    assert "Queues" in epub_text
    assert "First in, first out." in epub_text
    assert "ignore()" not in epub_text
    assert all("converter:docling" in item.metadata.warnings for item in (pptx, xlsx, epub))


def test_complex_pdf_uses_layout_blocks_and_keeps_page_evidence() -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Left heading")
    page.insert_text((300, 72), "Right heading")
    page.insert_text((72, 110), "Left detail")
    page.insert_text((300, 110), "Right detail")
    raw = document.tobytes()
    document.close()

    result = ConversionRegistry().convert(ConversionSource(name="columns.pdf", data=raw))

    combined = "\n".join(part.text for part in result.parts)
    assert any(part.page_start == part.page_end == 1 for part in result.parts)
    assert "Left heading" in combined
    assert "Right detail" in combined
    assert "converter:docling" in result.metadata.warnings


def test_expanded_archive_budget_empty_detection_and_batch_failure_isolation() -> None:
    native_pptx_registry = ConversionRegistry((PptxAdapter(),))
    oversized = _zip_bytes({"ppt/slides/slide1.xml": " " * 10_000})
    budget_failure = native_pptx_registry.convert_safe(
        ConversionSource(name="large.pptx", data=oversized),
        budget=ConversionBudget(max_expanded_bytes=100, max_input_bytes=50_000),
    )
    assert budget_failure.failure.code == "EXPANDED_BUDGET_EXCEEDED"

    empty = native_pptx_registry.convert_safe(
        ConversionSource(
            name="empty.pptx",
            data=_zip_bytes({"ppt/slides/slide1.xml": '<p:sld xmlns:p="urn:p"/>'}),
        )
    )
    assert empty.failure.code == "EMPTY_CONVERSION"

    results = ConversionRegistry().convert_many_safe(
        (
            ConversionSource(name="broken.docx", data=b"not-a-document"),
            ConversionSource(name="valid.txt", data=b"usable evidence"),
        )
    )
    assert [item.status for item in results] == ["failed", "ready"]
    assert results[0].failure.code == "DOCX_PARSE_FAILED"
    assert results[1].parts[0].text == "usable evidence"


def test_source_code_is_preserved_and_multimodal_inputs_degrade_explicitly() -> None:
    registry = ConversionRegistry()
    code = registry.convert(
        ConversionSource(name="queue.py", data=b"def push(item):\n    return item\n")
    )
    image = ConversionRegistry(
        (ImageOcrAdapter(),), disabled=("rapidocr-image",)
    ).convert_safe(
        ConversionSource(name="scan.png", data=b"\x89PNG\r\n")
    )
    audio = ConversionRegistry(
        (AudioTranscriptionAdapter(),), disabled=("sensevoice-audio",)
    ).convert_safe(
        ConversionSource(name="lecture.mp3", data=b"ID3")
    )
    video = registry.convert_safe(ConversionSource(name="lesson.mp4", data=b"video"))

    assert code.metadata.adapter == "native-code"
    assert code.parts[0].kind == "source-code"
    assert code.parts[0].text.splitlines()[0] == "def push(item):"
    assert image.failure.code == "ADAPTER_UNAVAILABLE"
    assert image.failure.details["adapters"] == [
        {"adapter": "rapidocr-image", "reason": "disabled"}
    ]
    assert audio.failure.code == "ADAPTER_UNAVAILABLE"
    assert audio.failure.details["adapters"] == [
        {"adapter": "sensevoice-audio", "reason": "disabled"}
    ]
    assert video.failure.code == "VIDEO_UNSUPPORTED_IN_V1"
