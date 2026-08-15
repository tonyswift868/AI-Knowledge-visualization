from __future__ import annotations

import io
import zipfile

import pytest

from app import conversion_adapters
from app.conversion_adapters import (
    AdapterHealth,
    AudioTranscriptionAdapter,
    ConversionBudget,
    ConversionRegistry,
    ConversionSource,
    DoclingDocumentAdapter,
    ImageOcrAdapter,
)


def test_registry_uses_unique_magic_bytes_without_trusting_renamed_suffix() -> None:
    registry = ConversionRegistry()
    selected = registry.select(
        ConversionSource(name="renamed.bin", data=b"%PDF-1.7\nminimal")
    )
    assert selected.name in {"docling-document", "native-pdf"}


def test_unknown_zip_signature_uses_safe_container_instead_of_guessing_office() -> None:
    registry = ConversionRegistry()
    selected = registry.select(
        ConversionSource(name="renamed.bin", data=b"PK\x03\x04not-a-real-archive")
    )
    assert selected.name == "safe-zip-container"


def test_magic_bytes_override_a_misleading_text_suffix() -> None:
    registry = ConversionRegistry()
    selected = registry.select(
        ConversionSource(name="misleading.txt", data=b"%PDF-1.7\nminimal")
    )
    assert selected.name in {"docling-document", "native-pdf"}


def test_riff_signature_distinguishes_webp_from_wave() -> None:
    webp = b"RIFF\x10\x00\x00\x00WEBPVP8 "
    wave = b"RIFF\x10\x00\x00\x00WAVEfmt "
    assert ImageOcrAdapter().signature_matches(webp) is True
    assert ImageOcrAdapter().signature_matches(wave) is False
    assert AudioTranscriptionAdapter().signature_matches(webp) is False
    assert AudioTranscriptionAdapter().signature_matches(wave) is True


def test_pretty_json_keeps_path_and_sentinel_evidence():
    result = ConversionRegistry().convert_safe(
        ConversionSource(
            name="arbitrary-domain.json",
            mime_type="application/json",
            data=b'{\n  "domain": {\n    "sentinel": "ORCHID-742"\n  }\n}',
        )
    )

    assert result.status == "ready"
    assert any(item.heading == "$/domain/sentinel" for item in result.parts)
    assert "ORCHID-742" in "\n".join(item.text for item in result.parts)


def test_utf16_and_gb18030_are_accepted_without_loss():
    registry = ConversionRegistry()
    expected = "\u8de8\u9886\u57df\u54e8\u5175"

    for name, payload in (
        ("utf16.txt", expected.encode("utf-16")),
        ("gb18030.txt", expected.encode("gb18030")),
    ):
        result = registry.convert_safe(ConversionSource(name=name, data=payload, mime_type="text/plain"))
        assert result.status == "ready"
        assert result.parts[0].text == expected


def test_safe_zip_processes_members_and_reports_unsupported_members():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("notes/topic.txt", "TIDAL-991")
        archive.writestr("ignored/video.mp4", b"not-a-video")

    result = ConversionRegistry().convert_safe(
        ConversionSource(name="bundle.zip", data=payload.getvalue(), mime_type="application/zip")
    )

    assert result.status == "ready"
    assert "TIDAL-991" in "\n".join(item.text for item in result.parts)
    assert any("VIDEO_UNSUPPORTED_IN_V1" in item for item in result.metadata.warnings)


def test_safe_zip_rejects_traversal_and_archive_budget_overflow():
    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../escape.txt", "must not escape")
    unsafe = ConversionRegistry().convert_safe(
        ConversionSource(
            name="unsafe.zip",
            data=traversal.getvalue(),
            mime_type="application/zip",
        )
    )
    assert unsafe.status == "failed"
    assert unsafe.failure.code == "ARCHIVE_PATH_INVALID"

    crowded = io.BytesIO()
    with zipfile.ZipFile(crowded, "w") as archive:
        archive.writestr("one.txt", "one")
        archive.writestr("two.txt", "two")
    overflow = ConversionRegistry().convert_safe(
        ConversionSource(
            name="crowded.zip",
            data=crowded.getvalue(),
            mime_type="application/zip",
        ),
        budget=ConversionBudget(max_archive_entries=1),
    )
    assert overflow.status == "failed"
    assert overflow.failure.code == "ARCHIVE_ENTRY_BUDGET_EXCEEDED"


def test_video_is_explicitly_rejected():
    result = ConversionRegistry().convert_safe(
        ConversionSource(name="lesson.mp4", data=b"payload", mime_type="video/mp4")
    )

    assert result.status == "failed"
    assert result.failure.code == "VIDEO_UNSUPPORTED_IN_V1"
    capability = next(item for item in ConversionRegistry().capabilities() if item.adapter == "video-rejected-v1")
    assert capability.available is False
    assert capability.rejected is True
    assert capability.reason == "VIDEO_UNSUPPORTED_IN_V1"


def test_image_ocr_preserves_line_bbox(monkeypatch):
    adapter = ImageOcrAdapter()
    monkeypatch.setattr(
        adapter,
        "health",
        lambda: AdapterHealth(True, adapter.name, adapter.version, supported_suffixes=adapter.suffixes),
    )
    monkeypatch.setattr(
        conversion_adapters,
        "_ocr_parts",
        lambda *_args, **_kwargs: [
            conversion_adapters.ConvertedPart(
                text="IMAGE-SENTINEL",
                ordinal=1,
                kind="ocr-line",
                bbox=(1.0, 2.0, 3.0, 4.0),
            )
        ],
    )

    result = adapter.convert(ConversionSource(name="scan.png", data=b"\x89PNG\r\n\x1a\n"))

    assert result.status == "ready"
    assert result.parts[0].bbox == (1.0, 2.0, 3.0, 4.0)


def _sentinel_png(text: str) -> bytes:
    pillow = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw, ImageFont

    image = pillow.new("RGB", (1400, 260), "white")
    ImageDraw.Draw(image).text(
        (40, 70),
        text,
        font=ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 72),
        fill="black",
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_real_rapidocr_image_and_scanned_pdf_keep_locators():
    pytest.importorskip("rapidocr")
    fitz = pytest.importorskip("fitz")
    registry = ConversionRegistry()
    png = _sentinel_png("SCANNED SENTINEL 583")

    image_result = registry.convert_safe(
        ConversionSource(name="sentinel.png", data=png, mime_type="image/png")
    )
    assert image_result.status == "ready", image_result.failure
    assert "SENTINEL" in " ".join(item.text for item in image_result.parts)
    assert all(item.bbox for item in image_result.parts)

    document = fitz.open()
    page = document.new_page(width=1400, height=260)
    page.insert_image(page.rect, stream=png)
    pdf = document.tobytes()
    document.close()
    pdf_result = registry.convert_safe(
        ConversionSource(name="scan.pdf", data=pdf, mime_type="application/pdf")
    )
    assert pdf_result.status == "ready", pdf_result.failure
    assert any(item.page_start == 1 and item.bbox for item in pdf_result.parts)
    if pdf_result.metadata.adapter == "docling-document":
        assert "converter:docling" in pdf_result.metadata.warnings
    else:
        assert "page:1:rapidocr" in pdf_result.metadata.warnings


def test_mixed_pdf_keeps_native_text_and_scanned_page_evidence():
    pytest.importorskip("rapidocr")
    fitz = pytest.importorskip("fitz")
    scanned = _sentinel_png("MIXED SCAN SENTINEL 584")
    document = fitz.open()
    native_page = document.new_page(width=1400, height=260)
    native_page.insert_text((72, 100), "MIXED NATIVE SENTINEL 927", fontsize=28)
    scan_page = document.new_page(width=1400, height=260)
    scan_page.insert_image(scan_page.rect, stream=scanned)
    payload = document.tobytes()
    document.close()

    result = ConversionRegistry().convert_safe(
        ConversionSource(name="mixed.pdf", data=payload, mime_type="application/pdf")
    )

    assert result.status == "ready", result.failure
    text = "\n".join(item.text for item in result.parts)
    assert "MIXED NATIVE SENTINEL 927" in text
    assert "MIXED" in text and "SENTINEL" in text and "584" in text
    assert {item.page_start for item in result.parts if item.page_start} >= {1, 2}
    native_evidence = [
        item for item in result.parts if "MIXED NATIVE SENTINEL 927" in item.text
    ]
    assert native_evidence
    assert all(item.page_start == 1 and item.bbox for item in native_evidence)
    assert result.metadata is not None
    assert result.metadata.page_count == 2


def test_real_docling_docx_preserves_arbitrary_content():
    pytest.importorskip("docling")
    from docx import Document

    output = io.BytesIO()
    document = Document()
    document.add_heading("Arbitrary Subject Delta", level=1)
    document.add_paragraph("Sentinel nebula-742 prerequisite relation evidence.")
    document.save(output)

    result = DoclingDocumentAdapter().convert(
        ConversionSource(
            name="arbitrary.docx",
            data=output.getvalue(),
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    )

    assert result.status == "ready", result.failure
    assert result.metadata.adapter == "docling-document"
    assert "converter:docling" in result.metadata.warnings
    assert "nebula-742" in "\n".join(item.text for item in result.parts)


def test_audio_transcription_preserves_time_range(monkeypatch, tmp_path):
    adapter = AudioTranscriptionAdapter()
    monkeypatch.setattr(
        adapter,
        "health",
        lambda: AdapterHealth(True, adapter.name, adapter.version, supported_suffixes=adapter.suffixes),
    )
    monkeypatch.setattr(conversion_adapters, "DEFAULT_ADAPTERS", (adapter,))
    from app import asr_supervisor

    monkeypatch.setattr(asr_supervisor, "upload_root", lambda: tmp_path)
    monkeypatch.setattr(
        asr_supervisor,
        "transcribe",
        lambda *_args, **_kwargs: {
            "ok": True,
            "text": "AUDIO-SENTINEL",
            "segments": [{"start_ms": 120, "end_ms": 910, "text": "AUDIO-SENTINEL"}],
        },
    )

    result = adapter.convert(ConversionSource(name="voice.wav", data=b"RIFF-audio"))

    assert result.status == "ready"
    assert result.parts[0].audio_start_ms == 120
    assert result.parts[0].audio_end_ms == 910
