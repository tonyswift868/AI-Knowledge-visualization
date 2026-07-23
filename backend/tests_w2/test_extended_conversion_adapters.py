from __future__ import annotations

import io
import zipfile

import fitz

from app.conversion_adapters import (
    ConversionBudget,
    ConversionRegistry,
    ConversionSource,
)


def _zip_bytes(files: dict[str, str | bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, value in files.items():
            archive.writestr(name, value.encode() if isinstance(value, str) else value)
    return output.getvalue()


def _pptx_bytes() -> bytes:
    return _zip_bytes(
        {
            "ppt/slides/slide1.xml": """
                <p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld><a:t>Graph Search</a:t>
                <a:t>Breadth first search uses a queue.</a:t></p:cSld></p:sld>""",
            "ppt/slides/slide2.xml": """
                <p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld><a:t>Complexity</a:t>
                <a:t>O(V + E)</a:t></p:cSld></p:sld>""",
        }
    )


def _xlsx_bytes() -> bytes:
    return _zip_bytes(
        {
            "xl/workbook.xml": """
                <workbook xmlns="urn:x" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                <sheets><sheet name="Scores" sheetId="1" r:id="rId1"/></sheets></workbook>""",
            "xl/_rels/workbook.xml.rels": """
                <Relationships xmlns="urn:r"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>""",
            "xl/sharedStrings.xml": """
                <sst xmlns="urn:x"><si><t>Name</t></si><si><t>Ada</t></si></sst>""",
            "xl/worksheets/sheet1.xml": """
                <worksheet xmlns="urn:x"><sheetData>
                <row><c t="s"><v>0</v></c><c t="inlineStr"><is><t>Score</t></is></c></row>
                <row><c t="s"><v>1</v></c><c><f>40+2</f><v>42</v></c></row>
                </sheetData></worksheet>""",
        }
    )


def _epub_bytes() -> bytes:
    return _zip_bytes(
        {
            "mimetype": "application/epub+zip",
            "META-INF/container.xml": """
                <container xmlns="urn:c"><rootfiles>
                <rootfile full-path="OPS/package.opf"/></rootfiles></container>""",
            "OPS/package.opf": """
                <package xmlns="urn:o"><manifest>
                <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
                </manifest><spine><itemref idref="chapter"/></spine></package>""",
            "OPS/chapter.xhtml": """
                <html><head><title>Queues</title></head><body><h1>Queues</h1>
                <p>First in, first out.</p><script>ignore()</script></body></html>""",
        }
    )


def test_pptx_xlsx_and_epub_real_in_memory_samples_preserve_structure() -> None:
    registry = ConversionRegistry()
    pptx = registry.convert(ConversionSource(name="lesson.pptx", data=_pptx_bytes()))
    xlsx = registry.convert(ConversionSource(name="scores.xlsx", data=_xlsx_bytes()))
    epub = registry.convert(ConversionSource(name="book.epub", data=_epub_bytes()))

    assert pptx.metadata.adapter == "native-pptx"
    assert pptx.metadata.page_count == 2
    assert [part.heading for part in pptx.parts] == ["Graph Search", "Complexity"]
    assert pptx.parts[1].page_start == 2

    assert xlsx.metadata.adapter == "native-xlsx"
    assert xlsx.parts[0].heading == "Scores"
    assert "Name\tScore" in xlsx.parts[0].text
    assert "=40+2 [42]" in xlsx.parts[0].text

    assert epub.metadata.adapter == "native-epub"
    assert epub.parts[0].heading == "Queues"
    assert "First in, first out." in epub.parts[0].text
    assert "ignore()" not in epub.parts[0].text


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

    assert result.parts[0].page_start == result.parts[0].page_end == 1
    assert "Left heading" in result.parts[0].text
    assert "Right detail" in result.parts[0].text
    assert "page:1:layout_blocks" in result.metadata.warnings


def test_expanded_archive_budget_empty_detection_and_batch_failure_isolation() -> None:
    registry = ConversionRegistry()
    oversized = _zip_bytes({"ppt/slides/slide1.xml": " " * 10_000})
    budget_failure = registry.convert_safe(
        ConversionSource(name="large.pptx", data=oversized),
        budget=ConversionBudget(max_expanded_bytes=100, max_input_bytes=50_000),
    )
    assert budget_failure.failure.code == "EXPANDED_BUDGET_EXCEEDED"

    empty = registry.convert_safe(
        ConversionSource(
            name="empty.pptx",
            data=_zip_bytes({"ppt/slides/slide1.xml": '<p:sld xmlns:p="urn:p"/>'}),
        )
    )
    assert empty.failure.code == "EMPTY_CONVERSION"

    results = registry.convert_many_safe(
        (
            ConversionSource(name="broken.docx", data=b"not-a-document"),
            ConversionSource(name="valid.txt", data=b"usable evidence"),
        )
    )
    assert [item.status for item in results] == ["failed", "ready"]
    assert results[0].failure.code == "SOURCE_FORMAT_INVALID"
    assert results[1].parts[0].text == "usable evidence"


def test_source_code_is_preserved_and_multimodal_inputs_degrade_explicitly() -> None:
    registry = ConversionRegistry()
    code = registry.convert(
        ConversionSource(name="queue.py", data=b"def push(item):\n    return item\n")
    )
    image = registry.convert_safe(
        ConversionSource(name="scan.png", data=b"\x89PNG\r\n")
    )
    audio = registry.convert_safe(
        ConversionSource(name="lecture.mp3", data=b"ID3")
    )

    assert code.metadata.adapter == "native-code"
    assert code.parts[0].kind == "source-code"
    assert code.parts[0].text.splitlines()[0] == "def push(item):"
    assert image.failure.code == "MULTIMODAL_EXTRACTION_REQUIRED"
    assert image.failure.details["required_capability"] == "ocr"
    assert audio.failure.code == "MULTIMODAL_EXTRACTION_REQUIRED"
    assert audio.failure.details["required_capability"] == "transcription"
