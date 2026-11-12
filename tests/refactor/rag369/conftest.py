"""Synthetic, offline OCR fixtures for 369 import coverage."""
import io
from contextlib import contextmanager

from PIL import Image, ImageDraw


def image_bytes(text="RAG369 image text", fmt="PNG"):
    image = Image.new("RGB", (900, 240), "white")
    ImageDraw.Draw(image).text((30, 90), text, fill="black")
    stream = io.BytesIO()
    image.save(stream, format=fmt)
    return stream.getvalue()


def scanned_pdf(page_count=2):
    pages = []
    for index in range(page_count):
        image = Image.open(io.BytesIO(image_bytes(f"scan page {index + 1}"))).convert("RGB")
        pages.append(image)
    stream = io.BytesIO()
    pages[0].save(stream, format="PDF", save_all=True, append_images=pages[1:])
    for page in pages:
        page.close()
    return stream.getvalue()


class StubOCR:
    """Deterministic OCR substitute; no model or network access."""
    def __init__(self, pages=None, image_text="RAG369 image text", max_pages=300):
        self.pages = pages or {}
        self.image_text = image_text
        self.max_pages = max_pages

    def check_page_count(self, count):
        if count > self.max_pages:
            from refactor.learning.course.extract import ExtractError
            raise ExtractError("TOO_LARGE", f"PDF 共 {count} 页，超过 OCR 的 {self.max_pages} 页上限")

    @contextmanager
    def open_pdf(self, data, *, cancel=None):
        self.check_page_count(len(self.pages) or 2)
        yield self

    def recognize_page(self, index, *, cancel=None):
        return self.pages.get(index, f"RAG369 page {index + 1} text")

    def recognize_image(self, image, *, cancel=None):
        return self.image_text

