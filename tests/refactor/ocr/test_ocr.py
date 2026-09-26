import io
from pathlib import Path
import subprocess
import sys
import threading

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject
import pytest

from refactor.learning.course.extract import ExtractError, extract_bytes, extract_file
from refactor.learning.course.ocr import PdfOCR


def scanned_pdf(lines=('中文扫描资料', '人工智能学习', '知识需要练习')):
    font_path = next((p for p in (Path('C:/Windows/Fonts/msyh.ttc'),
        Path('C:/Windows/Fonts/simsun.ttc'),
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')) if p.exists()), None)
    assert font_path is not None, '生成中文扫描样本需要本机中文字体'
    font = ImageFont.truetype(str(font_path), 46)
    with Image.new('RGB', (1000, 1300), 'white') as image:
        draw = ImageDraw.Draw(image)
        for i, line in enumerate(lines):
            draw.text((70, 90 + 80 * i), line, font=font, fill='black')
        stream = io.BytesIO()
        image.save(stream, 'PDF', resolution=144)
    assert len(stream.getvalue()) < 200_000
    return stream.getvalue()


def blank_pdf(count=1):
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=595, height=842)
    stream = io.BytesIO(); writer.write(stream)
    return stream.getvalue()


def mixed_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 18 Tf 40 750 Td (Existing text layer must be preserved without OCR.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    writer.append(PdfReader(io.BytesIO(scanned_pdf())))
    writer.append(PdfReader(io.BytesIO(scanned_pdf(('第二页扫描内容', '课程知识整理')))))
    stream = io.BytesIO(); writer.write(stream)
    return stream.getvalue()


@pytest.fixture(scope='module')
def engine():
    return PdfOCR()


def test_default_scan_rejection_and_lazy_imports():
    with pytest.raises(ExtractError, match='OCR') as caught:
        extract_bytes(scanned_pdf(), 'scan.pdf')
    assert caught.value.code == 'SCANNED_NO_TEXT'
    probe = ("import sys; from refactor.learning.course.ocr import PdfOCR; PdfOCR(); "
             "bad = [x for x in sys.modules if x.startswith(('pypdfium2', 'rapidocr', "
             "'onnxruntime', 'PySide6', 'PyQt', 'sqlite3', 'sqlalchemy'))]; "
             "print(bad); raise SystemExit(bool(bad))")
    result = subprocess.run([sys.executable, '-B', '-c', probe], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[3])
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_bundled_chinese_ocr_pages_progress_positions_and_repeatability(engine, tmp_path):
    raw = mixed_pdf()
    file = tmp_path / 'mixed.pdf'; file.write_bytes(raw)
    progress = []
    result = extract_file(file, ocr=engine, progress=lambda *args: progress.append(args))
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert result.blocks[0].text == 'Existing text layer must be preserved without OCR.'
    by_page = {page: '\n'.join(b.text for b in result.blocks if b.page == page) for page in (1, 2, 3)}
    assert '人工智能' in by_page[2] and '中文扫描资料' in by_page[2]
    assert '第二页扫描内容' in by_page[3]
    assert len(result.warnings) == 2 and all('OCR' in w for w in result.warnings)
    assert '第 2 页' in result.warnings[0] and '第 3 页' in result.warnings[1]
    assert all('第 1 页' not in w for w in result.warnings)
    assert result == extract_bytes(raw, 'mixed.pdf', ocr=engine)
    full = '\n\n'.join(b.text for b in result.blocks)
    assert all(full[c.char_start:c.char_end] == c.text and len(c.text) <= 2000 for c in result.chunks)
    assert result.chunks[0].page_start == 1 and result.chunks[-1].page_end == 3


def test_mixed_pdf_calls_recognizer_only_for_empty_pages(monkeypatch):
    from refactor.learning.course.ocr import _PdfSession
    calls = []
    def recognize(self, index, *, cancel=None):
        calls.append(index)
        return '扫描正文'
    monkeypatch.setattr(_PdfSession, 'recognize_page', recognize)
    result = extract_bytes(mixed_pdf(), 'mixed.pdf', ocr=PdfOCR())
    assert calls == [1, 2]
    assert result.blocks[0].text.startswith('Existing text layer')


@pytest.mark.parametrize('native_text,expected_calls', [('CamScanner', [0]), ('Short real heading', [])])
def test_scan_watermark_is_not_body_text_but_short_real_heading_is(monkeypatch, native_text, expected_calls):
    from refactor.learning.course.ocr import _PdfSession
    writer = PdfWriter(); writer.append(PdfReader(io.BytesIO(scanned_pdf())))
    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(f'BT /NativeFont 10 Tf 20 20 Td ({native_text}) Tj ET'.encode())
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page['/Resources'][NameObject('/Font')] = DictionaryObject({NameObject('/NativeFont'): writer._add_object(font)})
    page[NameObject('/Contents')] = ArrayObject([page.raw_get('/Contents'), writer._add_object(stream)])
    out = io.BytesIO(); writer.write(out)
    calls = []
    def recognize(self, index, *, cancel=None):
        calls.append(index)
        return '扫描正文'
    monkeypatch.setattr(_PdfSession, 'recognize_page', recognize)
    if expected_calls:
        assert extract_bytes(out.getvalue(), 'scan.pdf', ocr=PdfOCR()).blocks[0].text == '扫描正文'
    else:
        with pytest.raises(ExtractError) as caught:
            extract_bytes(out.getvalue(), 'scan.pdf', ocr=PdfOCR())
        assert caught.value.code == 'SCANNED_NO_TEXT'
    assert calls == expected_calls


def test_progress_cancel_stops_before_next_page_and_closes_document(monkeypatch):
    from refactor.learning.course.ocr import _PdfSession
    event, sessions = threading.Event(), []
    def recognize(self, index, *, cancel=None):
        sessions.append(self.document)
        return '正文'
    def progress(page, total):
        assert (page, total) == (1, 3)
        event.set()
    monkeypatch.setattr(_PdfSession, 'recognize_page', recognize)
    with pytest.raises(ExtractError) as caught:
        extract_bytes(blank_pdf(3), 'scan.pdf', ocr=PdfOCR(), cancel=event, progress=progress)
    assert caught.value.code == 'CANCELLED' and len(sessions) == 1
    assert sessions[0].raw is None


def test_cancel_after_inference_returns_no_partial_document():
    event = threading.Event()
    engine = PdfOCR()
    def recognize(image):
        event.set()
        return [[None, '不能返回半份正文', 1.0]], None
    engine._engine = recognize
    with pytest.raises(ExtractError) as caught:
        extract_bytes(scanned_pdf(), 'scan.pdf', ocr=engine, cancel=event)
    assert caught.value.code == 'CANCELLED'


def test_pre_cancelled_never_loads_engine(monkeypatch):
    event = threading.Event(); event.set()
    monkeypatch.setattr(PdfOCR, '_load_engine', lambda *a: pytest.fail('loaded after cancel'))
    with pytest.raises(ExtractError) as caught:
        extract_bytes(blank_pdf(), 'scan.pdf', ocr=PdfOCR(), cancel=event)
    assert caught.value.code == 'CANCELLED'


def test_default_page_limit_checked_before_render(monkeypatch):
    monkeypatch.setattr(PdfOCR, 'open_pdf', lambda *a, **k: pytest.fail('render before page guard'))
    with pytest.raises(ExtractError) as caught:
        extract_bytes(blank_pdf(301), 'scan.pdf', ocr=PdfOCR())
    assert caught.value.code == 'TOO_LARGE' and '301' in caught.value.message and '300' in caught.value.message
    with pytest.raises(ExtractError) as caught:
        extract_bytes(blank_pdf(3), 'scan.pdf', ocr=PdfOCR(max_pages=2))
    assert caught.value.code == 'TOO_LARGE'


def test_page_limit_not_applied_when_ocr_disabled():
    # Disabled OCR keeps the original scan error even for a long document.
    with pytest.raises(ExtractError) as caught:
        extract_bytes(blank_pdf(301), 'scan.pdf')
    assert caught.value.code == 'SCANNED_NO_TEXT'


def test_encrypted_scan_requires_empty_password_before_ocr(monkeypatch):
    from refactor.learning.course.ocr import _PdfSession
    monkeypatch.setattr(_PdfSession, 'recognize_page', lambda *a, **k: '中文扫描正文')
    for password in ('secret', ''):
        writer = PdfWriter(); writer.append(PdfReader(io.BytesIO(scanned_pdf())))
        writer.encrypt(password, algorithm='RC4-128')
        stream = io.BytesIO(); writer.write(stream)
        if password:
            with pytest.raises(ExtractError) as caught:
                extract_bytes(stream.getvalue(), 'encrypted.pdf', ocr=PdfOCR())
            assert caught.value.code == 'ENCRYPTED'
        else:
            assert extract_bytes(stream.getvalue(), 'encrypted.pdf', ocr=PdfOCR()).blocks[0].text == '中文扫描正文'


def test_missing_bundled_models_clear_error_no_download(monkeypatch):
    from refactor.learning.course import ocr
    monkeypatch.setattr(ocr, 'find_spec', lambda name: None)
    with pytest.raises(ExtractError) as caught:
        extract_bytes(blank_pdf(), 'scan.pdf', ocr=PdfOCR())
    assert caught.value.code == 'OCR_UNAVAILABLE' and '不会自动下载' in caught.value.message


def test_inference_failure_and_pixel_guard():
    engine = PdfOCR()
    def fail(image):
        raise RuntimeError('synthetic inference failure')
    engine._engine = fail
    with pytest.raises(ExtractError) as caught:
        extract_bytes(blank_pdf(), 'scan.pdf', ocr=engine)
    assert caught.value.code == 'OCR_FAILED' and '第 1 页' in caught.value.message
    engine = PdfOCR(max_pixels=10)
    engine._engine = fail
    with pytest.raises(ExtractError) as caught:
        extract_bytes(blank_pdf(), 'scan.pdf', ocr=engine)
    assert caught.value.code == 'TOO_LARGE' and '像素' in caught.value.message


def test_blank_page_after_real_ocr_still_has_no_text(engine):
    with pytest.raises(ExtractError) as caught:
        extract_bytes(blank_pdf(), 'blank.pdf', ocr=engine)
    assert caught.value.code == 'SCANNED_NO_TEXT'


@pytest.mark.parametrize('settings', [{'max_pages': 0}, {'max_pages': True}, {'dpi': 0},
    {'dpi': float('inf')}, {'max_pixels': -1}])
def test_invalid_ocr_configuration(settings):
    with pytest.raises(ValueError):
        PdfOCR(**settings)
