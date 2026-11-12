"""Opt-in, offline PDF OCR using the models bundled in rapidocr-onnxruntime.

Usage: extract_file(path, ocr=PdfOCR(), progress=lambda page, total: ...).
Create one PdfOCR per worker; it lazily reuses its CPU inference sessions.
No model downloader, URL input, Qt or database dependency is involved.
Cancellation is cooperative before/after native rendering and inference calls.
Missing dependencies/models raise OCR_UNAVAILABLE; inference raises OCR_FAILED.
Page count/pixel limits use the existing TOO_LARGE extraction error.
"""
from __future__ import annotations

from contextlib import contextmanager
from importlib.util import find_spec
from math import ceil, isfinite
from pathlib import Path
from threading import RLock

from .extract import ExtractError, _check


# PDFium calls must not run concurrently, even across different documents.
_PDFIUM_LOCK = RLock()
_MODELS = (
    'ch_PP-OCRv4_det_infer.onnx',
    'ch_ppocr_mobile_v2.0_cls_infer.onnx',
    'ch_PP-OCRv4_rec_infer.onnx',
)


class PdfOCR:
    def __init__(self, *, max_pages=300, dpi=180, max_pixels=20_000_000):
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError('max_pages 必须是正整数')
        if not isinstance(dpi, (int, float)) or not isfinite(dpi) or not 72 <= dpi <= 600:
            raise ValueError('dpi 必须在 72 到 600 之间')
        if type(max_pixels) is not int or max_pixels < 1:
            raise ValueError('max_pixels 必须是正整数')
        self.max_pages = max_pages
        self.dpi = dpi
        self.max_pixels = max_pixels
        self._engine = None

    def check_page_count(self, count):
        if count > self.max_pages:
            raise ExtractError('TOO_LARGE',
                f'PDF 共 {count} 页，超过 OCR 的 {self.max_pages} 页上限，请拆分后识别。')

    def _load_engine(self, cancel):
        _check(cancel)
        if self._engine is None:
            try:
                spec = find_spec('rapidocr_onnxruntime')
                if spec is None or spec.origin is None:
                    raise ImportError('rapidocr_onnxruntime')
                models = Path(spec.origin).parent / 'models'
                if not all((models / name).is_file() for name in _MODELS):
                    raise FileNotFoundError('bundled OCR models')
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR(
                    det_model_path=str(models / _MODELS[0]),
                    cls_model_path=str(models / _MODELS[1]),
                    rec_model_path=str(models / _MODELS[2]),
                    intra_op_num_threads=2, inter_op_num_threads=1,
                    det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False,
                    det_use_dml=False, cls_use_dml=False, rec_use_dml=False,
                    det_limit_side_len=1600, det_limit_type='max',
                )
            except Exception as exc:
                raise ExtractError('OCR_UNAVAILABLE',
                    '离线 OCR 组件或随包模型不可用，请检查已安装的 OCR 依赖；不会自动下载模型。') from exc
        _check(cancel)
        return self._engine

    @contextmanager
    def open_pdf(self, data, *, cancel=None):
        _check(cancel)
        try:
            import pypdfium2
        except (ImportError, OSError) as exc:
            raise ExtractError('OCR_UNAVAILABLE', 'PDF 渲染组件不可用，请检查 pypdfium2 安装。') from exc
        with _PDFIUM_LOCK:
            document = pypdfium2.PdfDocument(data, password='')
        try:
            with _PDFIUM_LOCK:
                self.check_page_count(len(document))
            _check(cancel)
            yield _PdfSession(self, document)
        finally:
            with _PDFIUM_LOCK:
                document.close()

    def recognize_image(self, image, *, cancel=None):
        """Recognize one already decoded image using the same pixel guard as PDF pages."""
        _check(cancel)
        width, height = image.size
        if width * height > self.max_pixels:
            raise ExtractError('TOO_LARGE', '图片尺寸超过 OCR 像素上限，请缩小图片后重试。')
        try:
            result, _ = self._load_engine(cancel)(image)
            _check(cancel)
            return '\n'.join(text.strip() for _, text, _confidence in (result or ()) if text.strip())
        except ExtractError:
            raise
        except Exception as exc:
            raise ExtractError('OCR_FAILED', '图片离线文字识别失败，请检查图片或稍后重试。') from exc


class _PdfSession:
    def __init__(self, owner, document):
        self.owner, self.document = owner, document

    def recognize_page(self, index, *, cancel=None):
        """Recognize a zero-based page, releasing image/page handles on all paths."""
        _check(cancel)
        engine = self.owner._load_engine(cancel)
        image = None
        try:
            with _PDFIUM_LOCK:
                page = self.document[index]
                try:
                    width, height = page.get_size()
                    scale = self.owner.dpi / 72
                    if ceil(width * scale) * ceil(height * scale) > self.owner.max_pixels:
                        raise ExtractError('TOO_LARGE',
                            f'第 {index + 1} 页渲染尺寸超过 OCR 像素上限，请缩小页面或降低识别分辨率。')
                    _check(cancel)
                    bitmap = page.render(scale=scale)
                    try:
                        # Copy: a PDFium bitmap owns the memory behind to_pil().
                        image = bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            _check(cancel)
            result, _ = engine(image)
            _check(cancel)
            lines = []
            for _, text, _confidence in result or ():
                _check(cancel)
                if text.strip():
                    lines.append(text.strip())
            return '\n'.join(lines)
        except ExtractError:
            raise
        except Exception as exc:
            raise ExtractError('OCR_FAILED',
                f'第 {index + 1} 页离线文字识别失败，请检查页面或稍后重试。') from exc
        finally:
            if image is not None:
                image.close()
