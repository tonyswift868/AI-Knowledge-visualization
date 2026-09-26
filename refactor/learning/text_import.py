"""Text-only extraction split from backend/app/active_teaching.py.

Keep its quality checks and JSON/CSV validation. UTF-16 is attempted only with
a BOM, avoiding the old fallback's accidental decoding of GB18030 as UTF-16.
No document models, network services or teaching pipeline are required.
"""
from __future__ import annotations

import csv
import io
import json

SUPPORTED_SUFFIXES = frozenset({".txt", ".md", ".csv", ".json"})
MAX_FILE_BYTES = 8 * 1024 * 1024
PREVIEW_CHARACTERS = 200_000


def _validate_text_quality(text: str) -> None:
    if not text.strip():
        raise ValueError("文件中没有可解析的正文")
    if "\x00" in text:
        raise ValueError("文本包含 NUL 字节，文件内容与文本格式不匹配")
    sample = text[:200_000]
    control_count = sum(
        1 for char in sample
        if ord(char) < 32 and char not in {"\n", "\r", "\t", "\f"}
    )
    if control_count / max(1, len(sample)) > 0.01:
        raise ValueError("文本控制字符比例异常，无法确认正文质量")
    if sample.count("\ufffd") / max(1, len(sample)) > 0.001:
        raise ValueError("文本包含过多解码替换字符，无法可靠解析")


def parse_text(raw: bytes, suffix: str) -> tuple[str, str]:
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("目前支持 TXT、Markdown、CSV 和 JSON 资料")
    encodings = ("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法识别文本编码（支持 UTF-8、带 BOM 的 UTF-16、GB18030）")
    _validate_text_quality(text)
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 语法错误（第 {exc.lineno} 行第 {exc.colno} 列）") from exc
        except RecursionError as exc:
            raise ValueError("JSON 嵌套层级过深，无法解析") from exc
    elif suffix == ".csv":
        try:
            has_data = False
            for row in csv.reader(io.StringIO(text), strict=True):
                has_data = has_data or any(cell.strip() for cell in row)
            if not has_data:
                raise ValueError("CSV 中没有可解析的数据")
        except csv.Error as exc:
            raise ValueError(f"CSV 语法错误：{exc}") from exc
    return text, encoding


def human_size(size_bytes: object) -> str:
    """Direct extraction from desktop/materials_page.py."""
    try:
        value = max(0, int(size_bytes or 0))
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"
