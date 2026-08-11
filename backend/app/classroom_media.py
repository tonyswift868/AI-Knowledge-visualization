from __future__ import annotations

import importlib.util
import mimetypes
import os
import sys
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .conversion_adapters import ConversionBudget, ConversionRegistry, ConversionSource


MAX_CLASSROOM_ATTACHMENT_BYTES = 32 * 1024 * 1024
MAX_CLASSROOM_ATTACHMENT_COUNT = 6
MAX_CLASSROOM_ATTACHMENT_BATCH_BYTES = 64 * 1024 * 1024
MAX_CLASSROOM_EXTRACTED_CHARS = 48_000
MAX_CLASSROOM_ARCHIVE_ENTRIES = 2_000
MAX_CLASSROOM_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_CLASSROOM_AUDIO_BYTES = 20 * 1024 * 1024

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_ARCHIVE_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".xml",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".py", ".js", ".ts",
    ".tsx", ".jsx", ".css", ".html", ".htm", ".sql", ".sh", ".ps1", ".bat",
    ".java", ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".rb", ".php",
}
DOCUMENT_SUFFIXES = {
    *TEXT_ARCHIVE_SUFFIXES,
    ".pdf", ".docx", ".pptx", ".xlsx", ".epub", ".zip",
}
CLASSROOM_ATTACHMENT_SUFFIXES = IMAGE_SUFFIXES | DOCUMENT_SUFFIXES
CLASSROOM_AUDIO_SUFFIXES = {".webm", ".ogg", ".wav", ".mp3", ".m4a"}


def safe_attachment_name(value: str) -> str:
    name = Path(str(value or "attachment.bin").replace("\\", "/")).name
    cleaned = "".join(
        "_" if ord(char) < 32 or char in '<>:"/\\|?*' else char
        for char in name
    ).strip(" .")
    return cleaned[:220] or "attachment.bin"


def attachment_kind(filename: str, media_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_SUFFIXES or str(media_type).lower().startswith("image/"):
        return "image"
    if suffix == ".zip":
        return "archive"
    return "document"


def resolved_media_type(filename: str, declared: str = "") -> str:
    guessed = mimetypes.guess_type(filename)[0]
    return str(guessed or declared or "application/octet-stream").split(";", 1)[0]


def inspect_zip_bytes(data: bytes) -> dict[str, Any]:
    entry_count = 0
    file_count = 0
    expanded = 0
    names: list[str] = []
    import io

    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            for info in archive.infolist():
                entry_count += 1
                if entry_count > MAX_CLASSROOM_ARCHIVE_ENTRIES:
                    raise ValueError("ZIP 文件条目过多")
                if info.flag_bits & 0x1:
                    raise ValueError("暂不支持加密 ZIP")
                expanded += max(0, int(info.file_size or 0))
                if expanded > MAX_CLASSROOM_ARCHIVE_BYTES:
                    raise ValueError("ZIP 解压后的总大小超过 100 MB")
                normalized = PurePosixPath(str(info.filename).replace("\\", "/"))
                if normalized.is_absolute() or ".." in normalized.parts:
                    raise ValueError("ZIP 包含不安全的路径")
                if not info.is_dir():
                    file_count += 1
                if len(names) < 80:
                    names.append(str(info.filename)[:260])
    except zipfile.BadZipFile as exc:
        raise ValueError("文件不是有效的 ZIP 压缩包") from exc
    return {
        "entry_count": entry_count,
        "file_count": file_count,
        "expanded_bytes": expanded,
        "entry_preview": names,
    }


def validate_attachment_bytes(
    data: bytes,
    *,
    filename: str,
    declared_media_type: str,
) -> dict[str, Any]:
    safe_name = safe_attachment_name(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in CLASSROOM_ATTACHMENT_SUFFIXES:
        raise ValueError(
            "暂不支持这种附件；可上传图片、PDF、Office 文档、EPUB、ZIP、"
            "文本与常见代码文件"
        )
    if not data:
        raise ValueError("请选择非空文件")
    limit = 8 * 1024 * 1024 if suffix in IMAGE_SUFFIXES else MAX_CLASSROOM_ATTACHMENT_BYTES
    if len(data) > limit:
        raise ValueError(
            f"{safe_name} 超过 {'8' if suffix in IMAGE_SUFFIXES else '32'} MB"
        )
    metadata: dict[str, Any] = {}
    if suffix == ".zip":
        metadata["archive"] = inspect_zip_bytes(data)
    return {
        "filename": safe_name,
        "suffix": suffix,
        "media_type": resolved_media_type(safe_name, declared_media_type),
        "kind": attachment_kind(safe_name, declared_media_type),
        **metadata,
    }


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_zip_text(path: Path, budget: int) -> str:
    parts: list[str] = []
    used = 0
    with zipfile.ZipFile(path, "r") as archive:
        for info in archive.infolist():
            if used >= budget:
                break
            suffix = PurePosixPath(info.filename).suffix.lower()
            if info.is_dir() or info.flag_bits & 0x1 or suffix not in TEXT_ARCHIVE_SUFFIXES:
                continue
            remaining = max(0, budget - used)
            raw = archive.read(info)
            chunk = _decode_text(raw[: remaining * 4])[:remaining]
            if chunk:
                section = f"\n--- ZIP 内文件：{info.filename} ---\n{chunk}"
                parts.append(section)
                used += len(section)
    return "".join(parts)[:budget]


def extract_attachment_text(
    path: Path,
    *,
    filename: str,
    media_type: str,
    budget: int = MAX_CLASSROOM_EXTRACTED_CHARS,
) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "", "image"
    if suffix == ".zip":
        return _extract_zip_text(path, budget), "zip_text"
    result = ConversionRegistry().convert_safe(
        ConversionSource(name=filename, path=path, mime_type=media_type),
        budget=ConversionBudget(
            max_input_bytes=MAX_CLASSROOM_ATTACHMENT_BYTES,
            max_output_chars=budget,
            timeout_seconds=20,
            max_expanded_bytes=MAX_CLASSROOM_ARCHIVE_BYTES,
            max_archive_entries=MAX_CLASSROOM_ARCHIVE_ENTRIES,
        ),
    )
    if result.status != "ready":
        failure = result.failure
        code = failure.code if failure else "DOCUMENT_EXTRACTION_FAILED"
        return "", f"unreadable:{code}"
    sections = []
    used = 0
    for part in result.parts:
        remaining = max(0, budget - used)
        if remaining <= 0:
            break
        heading = f"\n--- {part.heading} ---\n" if part.heading else "\n"
        chunk = (heading + part.text)[:remaining]
        sections.append(chunk)
        used += len(chunk)
    return "".join(sections).strip(), (
        result.metadata.adapter if result.metadata else "document_text"
    )


_ASR_LOCK = threading.Lock()
_ASR_MODULE: Any | None = None
_OCR_LOCK = threading.Lock()
_OCR_ENGINE: Any | None = None


def _load_classroom_ocr() -> Any:
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE
    with _OCR_LOCK:
        if _OCR_ENGINE is not None:
            return _OCR_ENGINE
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise RuntimeError("本地 RapidOCR 组件尚未安装") from exc
        _OCR_ENGINE = RapidOCR()
        return _OCR_ENGINE


def extract_image_text(
    path: Path,
    *,
    budget: int = MAX_CLASSROOM_EXTRACTED_CHARS,
) -> tuple[str, str]:
    """Read screenshot text locally on CPU without a second remote provider."""

    try:
        engine = _load_classroom_ocr()
        with _OCR_LOCK:
            result = engine(str(path))
    except Exception as exc:
        return "", f"local_ocr_unavailable:{type(exc).__name__}"

    texts = [
        str(text).strip()
        for text in (getattr(result, "txts", None) or ())
        if str(text).strip()
    ]
    if not texts:
        return "", "local_ocr_empty"
    return "\n".join(texts)[:budget], "rapidocr_ppocrv6"


def _ai_agent_root() -> Path:
    return Path(
        os.getenv("MENTORFLOW_AI_AGENT_ROOT", "E:/aiPlay/ai_agent")
    ).expanduser().resolve()


def _load_ai_agent_asr() -> Any:
    global _ASR_MODULE
    if _ASR_MODULE is not None:
        return _ASR_MODULE
    with _ASR_LOCK:
        if _ASR_MODULE is not None:
            return _ASR_MODULE
        root = _ai_agent_root()
        module_path = root / "agent_core" / "local_asr_sensevoice_client.py"
        if not module_path.is_file():
            raise RuntimeError("本地 SenseVoice 组件尚未安装")
        spec = importlib.util.spec_from_file_location(
            "mentorflow_shared_sensevoice_client",
            module_path,
        )
        if not spec or not spec.loader:
            raise RuntimeError("无法加载本地 SenseVoice 组件")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _ASR_MODULE = module
        return module


def classroom_asr_status() -> dict[str, Any]:
    try:
        module = _load_ai_agent_asr()
        status = module.status()
        return {
            "available": bool(module.assets_ready()),
            "ready": bool(status.get("ok")),
            "engine": str(
                status.get("engine") or "sherpa-onnx-sensevoice-int8"
            ),
            "status": str(status.get("status") or (
                "ready" if status.get("ok") else "idle"
            )),
        }
    except Exception as exc:
        return {
            "available": False,
            "ready": False,
            "engine": "sherpa-onnx-sensevoice-int8",
            "status": "unavailable",
            "error": str(exc)[:240],
        }


def warm_classroom_asr() -> dict[str, Any]:
    try:
        module = _load_ai_agent_asr()
        result = module.warm(wait_seconds=0.15)
        return {
            "available": bool(module.assets_ready()),
            "ready": bool(result.get("ok")),
            "engine": str(
                result.get("engine") or "sherpa-onnx-sensevoice-int8"
            ),
            "status": str(result.get("status") or "starting"),
        }
    except Exception as exc:
        return {
            "available": False,
            "ready": False,
            "engine": "sherpa-onnx-sensevoice-int8",
            "status": "unavailable",
            "error": str(exc)[:240],
        }


def transcribe_classroom_audio(
    data: bytes,
    *,
    filename: str,
    language: str = "auto",
) -> dict[str, Any]:
    safe_name = safe_attachment_name(filename or "recording.webm")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in CLASSROOM_AUDIO_SUFFIXES:
        raise ValueError("录音格式不受支持")
    if not data:
        raise ValueError("没有录到声音")
    if len(data) > MAX_CLASSROOM_AUDIO_BYTES:
        raise ValueError("单次录音不能超过 20 MB")
    root = _ai_agent_root()
    upload_root = (root / "runtime" / "asr_uploads").resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    target = upload_root / (
        f"mentorflow_{int(time.time() * 1000)}_{os.getpid()}{suffix}"
    )
    target.write_bytes(data)
    try:
        module = _load_ai_agent_asr()
        result = module.transcribe(target, str(language or "auto")[:16], timeout=45)
        text = str(result.get("text") or "").strip()
        if not result.get("ok") or not text:
            error = "没有检测到有效语音" if result.get("blank_audio") else str(
                result.get("error") or "语音识别失败"
            )
            raise RuntimeError(error)
        return {
            "ok": True,
            "text": text[:20_000],
            "engine": str(
                result.get("engine") or "sherpa-onnx-sensevoice-int8"
            ),
            "audio_bytes": len(data),
            "temporary_audio_deleted": True,
        }
    finally:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
