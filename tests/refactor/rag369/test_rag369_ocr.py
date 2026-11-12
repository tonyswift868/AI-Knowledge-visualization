import threading
from hashlib import sha256

import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.course.extract import ExtractError, extract_bytes
from tests.refactor.rag369.conftest import StubOCR, image_bytes, scanned_pdf
from tests.refactor.rag320.test_rag_import320 import close_service, make_service, select


def test_scanned_pdf_ocr_keeps_page_provenance_and_searchable_text():
    result = extract_bytes(scanned_pdf(), "lesson.pdf", ocr=StubOCR({0: "alpha source text", 1: "beta source text"}))
    assert "alpha source text" in "\n".join(block.text for block in result.blocks)
    assert "beta source text" in "\n".join(block.text for block in result.blocks)
    assert {block.page for block in result.blocks} == {1, 2}
    assert result.chunks[0].page_start == 1 and result.chunks[-1].page_end == 2
    assert all("source text" in chunk.text for chunk in result.chunks)


def test_cancelled_pdf_ocr_does_not_return_a_partial_document():
    cancel = threading.Event()
    ocr = StubOCR({0: "first page text", 1: "second page text"})
    def stop_after_first(page, total):
        cancel.set()
    with pytest.raises(ExtractError) as caught:
        extract_bytes(scanned_pdf(), "cancel.pdf", ocr=ocr, cancel=cancel, progress=stop_after_first)
    assert caught.value.code == "CANCELLED"


def test_pdf_ocr_page_limit_has_actionable_message():
    with pytest.raises(ExtractError) as caught:
        extract_bytes(scanned_pdf(3), "too-many.pdf", ocr=StubOCR(max_pages=2))
    assert caught.value.code == "TOO_LARGE"
    assert "3" in caught.value.message and "2" in caught.value.message


@pytest.mark.parametrize("suffix,fmt,mime", [
    ("png", "PNG", "image/png"), ("jpg", "JPEG", "image/jpeg"),
    ("jpeg", "JPEG", "image/jpeg"), ("webp", "WEBP", "image/webp"),
    ("bmp", "BMP", "image/bmp"),
])
def test_png_and_jpg_images_are_ocr_importable_and_chunked(suffix, fmt, mime):
    raw = image_bytes("image searchable token", fmt)
    result = extract_bytes(raw, f"photo.{suffix}", ocr=StubOCR(image_text="image searchable token"))
    assert result.media_type == mime
    assert result.blocks[0].page == 1
    assert result.chunks[0].text == "image searchable token"
    assert result.chunks[0].page_start == result.chunks[0].page_end == 1


def test_blank_image_is_rejected_and_never_becomes_an_empty_document():
    with pytest.raises(ExtractError) as caught:
        extract_bytes(image_bytes(""), "blank.png", ocr=StubOCR(image_text=""))
    assert caught.value.code == "EMPTY"
    assert "没认出文字" in caught.value.message


def test_missing_ocr_component_is_explicit_for_image_upload(monkeypatch):
    from refactor.learning.course import ocr
    monkeypatch.setattr(ocr.PdfOCR, "_load_engine", lambda *args: (_ for _ in ()).throw(
        ExtractError("OCR_UNAVAILABLE", "离线 OCR 组件或随包模型不可用；不会自动下载模型。")))
    with pytest.raises(ExtractError) as caught:
        extract_bytes(image_bytes("missing model"), "photo.png", ocr=ocr.PdfOCR())
    assert caught.value.code == "OCR_UNAVAILABLE"
    assert "不会自动下载" in caught.value.message


def test_rag_upload_persists_ocr_pdf_and_images_with_page_citations(tmp_path, monkeypatch):
    from refactor.learning.course import ocr as ocr_module
    monkeypatch.setattr(ocr_module, "PdfOCR", lambda: StubOCR({0: "uploaded pdf token", 1: "second page token"},
                                                                image_text="uploaded image token"))
    service, _ = make_service(tmp_path)
    pdf = tmp_path / "upload.pdf"
    png = tmp_path / "upload.png"
    pdf.write_bytes(scanned_pdf())
    png.write_bytes(image_bytes("uploaded image token"))
    select(service, pdf, png)
    try:
        result = service.import_files([str(pdf), str(png)])
        assert result["errors"] == []
        assert len(result["documents"]) == 2
        pdf_doc = service.local.document(sha256(pdf.read_bytes()).hexdigest())
        image_doc = service.local.document(sha256(png.read_bytes()).hexdigest())
        assert any("uploaded pdf token" in chunk["text"] and chunk["page"] == 1
                   for chunk in pdf_doc["chunks"])
        assert any("uploaded image token" in chunk["text"] and chunk["page"] == 1
                   for chunk in image_doc["chunks"])
    finally:
        close_service(service)


def test_rag_pdf_retries_once_and_emits_page_progress_without_partial_row(tmp_path, monkeypatch):
    from refactor.learning.course import ocr as ocr_module

    monkeypatch.setattr(ocr_module, "PdfOCR", lambda: StubOCR({0: "retry page one", 1: "retry page two"}))
    service, _ = make_service(tmp_path)
    path = tmp_path / "retry.pdf"
    path.write_bytes(scanned_pdf())
    events = []
    select(service, path)
    try:
        result = service.import_files([str(path)], on_progress=events.append)
        assert result["errors"] == [] and result["documents"]
        assert [(event["page"], event["total"]) for event in events
                if event.get("kind") == "extract_progress"] == [(1, 2), (2, 2)]
        assert service.local.document(sha256(path.read_bytes()).hexdigest()) is not None

        cancel = threading.Event()
        cancel_path = tmp_path / "cancel.pdf"
        cancel_path.write_bytes(scanned_pdf(3))
        events.clear()
        select(service, cancel_path)
        with pytest.raises(BridgeError) as caught:
            service.import_files([str(cancel_path)], cancel=cancel,
                on_progress=lambda event: (events.append(event), cancel.set())
                if event.get("kind") == "extract_progress" else events.append(event))
        assert caught.value.code == "CANCELLED"
        assert service.local.document(sha256(cancel_path.read_bytes()).hexdigest()) is None
    finally:
        close_service(service)


def test_rag_blank_image_is_not_saved_and_duplicate_content_is_one_row(tmp_path, monkeypatch):
    from refactor.learning.course import ocr as ocr_module
    monkeypatch.setattr(ocr_module, "PdfOCR", lambda: StubOCR(image_text="image dedupe token"))
    service, _ = make_service(tmp_path)
    image = tmp_path / "same.png"
    image.write_bytes(image_bytes("image dedupe token"))
    blank = tmp_path / "blank.png"
    blank.write_bytes(image_bytes(""))
    try:
        select(service, image)
        first = service.import_files([str(image)])
        select(service, image)
        second = service.import_files([str(image)])
        assert len(first["documents"]) == len(second["documents"]) == 1
        assert len(service.local.documents()) == 1

        monkeypatch.setattr(ocr_module, "PdfOCR", lambda: StubOCR(image_text=""))
        select(service, blank)
        failed = service.import_files([str(blank)])
        assert failed["documents"] == [] and failed["errors"][0]["code"] == "RAG_EXTRACT_EMPTY"
        assert service.local.document(sha256(blank.read_bytes()).hexdigest()) is None
    finally:
        close_service(service)


