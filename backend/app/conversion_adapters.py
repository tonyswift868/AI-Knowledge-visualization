from __future__ import annotations

import hashlib
import importlib.util
import io
import mimetypes
import re
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Protocol, Sequence
from xml.etree import ElementTree


@dataclass(frozen=True)
class ConversionBudget:
    max_input_bytes: int = 20 * 1024 * 1024
    max_output_chars: int = 2_000_000
    timeout_seconds: float = 30.0
    max_expanded_bytes: int = 64 * 1024 * 1024
    max_archive_entries: int = 5_000

    def __post_init__(self) -> None:
        if self.max_input_bytes <= 0:
            raise ValueError("max_input_bytes must be positive")
        if self.max_output_chars <= 0:
            raise ValueError("max_output_chars must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_expanded_bytes <= 0:
            raise ValueError("max_expanded_bytes must be positive")
        if self.max_archive_entries <= 0:
            raise ValueError("max_archive_entries must be positive")


class CancellationToken:
    """Cooperative cancellation shared by every native adapter."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ConversionError("CONVERSION_CANCELLED", "Conversion was cancelled")


@dataclass(frozen=True)
class ConversionSource:
    name: str
    data: bytes | None = None
    path: Path | None = None
    mime_type: str = ""

    def __post_init__(self) -> None:
        if (self.data is None) == (self.path is None):
            raise ValueError("exactly one of data or path must be supplied")

    @property
    def suffix(self) -> str:
        return Path(self.name or (self.path.name if self.path else "")).suffix.lower()


@dataclass(frozen=True)
class ConvertedPart:
    text: str
    ordinal: int
    heading: str = ""
    page_start: int | None = None
    page_end: int | None = None
    kind: str = "text"


@dataclass(frozen=True)
class AdapterHealth:
    available: bool
    adapter: str
    version: str
    reason: str = ""
    supported_suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConversionMetadata:
    adapter: str
    adapter_version: str
    source_name: str
    source_sha256: str
    source_bytes: int
    mime_type: str
    output_chars: int
    part_count: int
    page_count: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConversionFailure:
    code: str
    message: str
    adapter: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversionResult:
    status: str
    parts: tuple[ConvertedPart, ...] = ()
    metadata: ConversionMetadata | None = None
    assets: tuple[dict[str, Any], ...] = ()
    failure: ConversionFailure | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConversionError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message)


class ConversionAdapter(Protocol):
    name: str
    version: str

    def can_handle(self, source: ConversionSource) -> bool: ...

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult: ...

    def metadata(
        self,
        source: ConversionSource,
        raw: bytes,
        parts: Sequence[ConvertedPart],
        *,
        page_count: int = 0,
        warnings: Sequence[str] = (),
    ) -> ConversionMetadata: ...

    def health(self) -> AdapterHealth: ...

    def failure(self, error: Exception) -> ConversionFailure: ...


class NativeAdapter:
    name = "native"
    version = "1.0.0"
    suffixes: tuple[str, ...] = ()
    mime_types: tuple[str, ...] = ()
    dependency: str | None = None

    def can_handle(self, source: ConversionSource) -> bool:
        mime = source.mime_type.split(";", 1)[0].strip().lower()
        return source.suffix in self.suffixes or bool(mime and mime in self.mime_types)

    def health(self) -> AdapterHealth:
        available = self.dependency is None or importlib.util.find_spec(self.dependency) is not None
        return AdapterHealth(
            available=available,
            adapter=self.name,
            version=self.version,
            reason="" if available else f"Optional dependency '{self.dependency}' is unavailable",
            supported_suffixes=self.suffixes,
        )

    def failure(self, error: Exception) -> ConversionFailure:
        if isinstance(error, ConversionError):
            return ConversionFailure(
                code=error.code,
                message=error.message,
                adapter=self.name,
                retryable=error.retryable,
                details=error.details,
            )
        return ConversionFailure(
            code="CONVERSION_FAILED",
            message=str(error) or error.__class__.__name__,
            adapter=self.name,
            retryable=False,
            details={"exception_type": error.__class__.__name__},
        )

    def metadata(
        self,
        source: ConversionSource,
        raw: bytes,
        parts: Sequence[ConvertedPart],
        *,
        page_count: int = 0,
        warnings: Sequence[str] = (),
    ) -> ConversionMetadata:
        mime = source.mime_type.split(";", 1)[0].strip().lower()
        if not mime:
            mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        return ConversionMetadata(
            adapter=self.name,
            adapter_version=self.version,
            source_name=source.name,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            source_bytes=len(raw),
            mime_type=mime,
            output_chars=sum(len(part.text) for part in parts),
            part_count=len(parts),
            page_count=page_count,
            warnings=tuple(warnings),
        )

    def _load(
        self,
        source: ConversionSource,
        budget: ConversionBudget,
        cancellation: CancellationToken,
        started: float,
    ) -> bytes:
        self._checkpoint(budget, cancellation, started)
        if source.data is not None:
            raw = source.data
        else:
            assert source.path is not None
            try:
                size = source.path.stat().st_size
            except OSError as exc:
                raise ConversionError(
                    "SOURCE_READ_FAILED",
                    f"Cannot inspect source: {exc}",
                    details={"path": str(source.path)},
                ) from exc
            if size > budget.max_input_bytes:
                raise ConversionError(
                    "INPUT_BUDGET_EXCEEDED",
                    "Source exceeds conversion input budget",
                    details={"source_bytes": size, "max_input_bytes": budget.max_input_bytes},
                )
            try:
                raw = source.path.read_bytes()
            except OSError as exc:
                raise ConversionError(
                    "SOURCE_READ_FAILED",
                    f"Cannot read source: {exc}",
                    details={"path": str(source.path)},
                ) from exc
        if len(raw) > budget.max_input_bytes:
            raise ConversionError(
                "INPUT_BUDGET_EXCEEDED",
                "Source exceeds conversion input budget",
                details={"source_bytes": len(raw), "max_input_bytes": budget.max_input_bytes},
            )
        self._checkpoint(budget, cancellation, started)
        return raw

    def _checkpoint(
        self,
        budget: ConversionBudget,
        cancellation: CancellationToken,
        started: float,
        *,
        output_chars: int = 0,
    ) -> None:
        cancellation.raise_if_cancelled()
        elapsed = time.monotonic() - started
        if elapsed > budget.timeout_seconds:
            raise ConversionError(
                "CONVERSION_TIMEOUT",
                "Conversion exceeded its time budget",
                retryable=True,
                details={"elapsed_seconds": round(elapsed, 4), "timeout_seconds": budget.timeout_seconds},
            )
        if output_chars > budget.max_output_chars:
            raise ConversionError(
                "OUTPUT_BUDGET_EXCEEDED",
                "Converted text exceeds output budget",
                details={"output_chars": output_chars, "max_output_chars": budget.max_output_chars},
            )

    def _ready_result(
        self,
        source: ConversionSource,
        raw: bytes,
        parts: Sequence[ConvertedPart],
        budget: ConversionBudget,
        cancellation: CancellationToken,
        started: float,
        *,
        page_count: int = 0,
        warnings: Sequence[str] = (),
    ) -> ConversionResult:
        output_chars = sum(len(part.text) for part in parts)
        self._checkpoint(budget, cancellation, started, output_chars=output_chars)
        if not parts or not any(part.text.strip() for part in parts):
            raise ConversionError("EMPTY_CONVERSION", "Converter produced no usable text")
        return ConversionResult(
            status="ready",
            parts=tuple(parts),
            metadata=self.metadata(
                source,
                raw,
                parts,
                page_count=page_count,
                warnings=warnings,
            ),
        )

    def convert_safe(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        try:
            return self.convert(source, budget=budget, cancellation=cancellation)
        except Exception as exc:
            return ConversionResult(status="failed", failure=self.failure(exc))


def _decode_text(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ConversionError("TEXT_ENCODING_UNSUPPORTED", "Text encoding could not be identified")


def _heading_sections(text: str, *, kind: str) -> list[ConvertedPart]:
    parts: list[ConvertedPart] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            parts.append(
                ConvertedPart(
                    text=body,
                    ordinal=len(parts) + 1,
                    heading=heading,
                    kind=kind,
                )
            )

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and stripped.lstrip("#").strip():
            flush()
            buffer.clear()
            heading = stripped.lstrip("#").strip()
            buffer.append(stripped)
        else:
            buffer.append(line)
    flush()
    return parts


def _natural_key(value: str) -> tuple[Any, ...]:
    return tuple(int(item) if item.isdigit() else item.lower() for item in re.split(r"(\d+)", value))


class _BoundedArchive:
    """Read document ZIP members without allowing zip bombs or path traversal."""

    def __init__(self, raw: bytes, budget: ConversionBudget) -> None:
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(raw))
        except (OSError, zipfile.BadZipFile) as exc:
            raise ConversionError("ARCHIVE_PARSE_FAILED", f"Document archive could not be opened: {exc}") from exc
        entries = self.archive.infolist()
        if len(entries) > budget.max_archive_entries:
            self.archive.close()
            raise ConversionError(
                "ARCHIVE_ENTRY_BUDGET_EXCEEDED",
                "Document archive contains too many entries",
                details={"entry_count": len(entries), "max_archive_entries": budget.max_archive_entries},
            )
        expanded = sum(item.file_size for item in entries)
        if expanded > budget.max_expanded_bytes:
            self.archive.close()
            raise ConversionError(
                "EXPANDED_BUDGET_EXCEEDED",
                "Expanded document exceeds conversion budget",
                details={"expanded_bytes": expanded, "max_expanded_bytes": budget.max_expanded_bytes},
            )
        self._names: dict[str, zipfile.ZipInfo] = {}
        for item in entries:
            normalized = item.filename.replace("\\", "/")
            path = PurePosixPath(normalized)
            if path.is_absolute() or ".." in path.parts:
                self.archive.close()
                raise ConversionError("ARCHIVE_PATH_INVALID", "Document archive contains an unsafe member path")
            if item.flag_bits & 0x1:
                self.archive.close()
                raise ConversionError("ARCHIVE_ENCRYPTED", "Encrypted document archives are unsupported")
            self._names[normalized] = item

    def names(self) -> tuple[str, ...]:
        return tuple(self._names)

    def read(self, name: str) -> bytes:
        normalized = str(PurePosixPath(name.replace("\\", "/")))
        item = self._names.get(normalized)
        if item is None:
            raise ConversionError("ARCHIVE_MEMBER_MISSING", f"Required document member is missing: {normalized}")
        return self.archive.read(item)

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> "_BoundedArchive":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _xml(raw: bytes, *, code: str) -> ElementTree.Element:
    upper = raw[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ConversionError("XML_DTD_FORBIDDEN", "Document XML declarations and entities are unsupported")
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ConversionError(code, f"Document XML could not be parsed: {exc}") from exc


def _xml_texts(root: ElementTree.Element, local_name: str = "t") -> list[str]:
    return [
        (element.text or "").strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == local_name and (element.text or "").strip()
    ]


class PlainTextAdapter(NativeAdapter):
    name = "native-text"
    suffixes = (".txt",)
    mime_types = ("text/plain",)

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        raw = self._load(source, budget, cancellation, started)
        text, encoding = _decode_text(raw)
        parts = [ConvertedPart(text=text.strip(), ordinal=1, kind="text")]
        return self._ready_result(source, raw, parts, budget, cancellation, started, warnings=(f"encoding:{encoding}",))


class CodeAdapter(NativeAdapter):
    name = "native-code"
    suffixes = (
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".c", ".h",
        ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".kts", ".sql",
        ".sh", ".ps1", ".yaml", ".yml", ".json", ".toml",
    )

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        raw = self._load(source, budget, cancellation, started)
        text, encoding = _decode_text(raw)
        part = ConvertedPart(text=text.rstrip(), ordinal=1, heading=source.name, kind="source-code")
        return self._ready_result(
            source, raw, (part,), budget, cancellation, started, warnings=(f"encoding:{encoding}",)
        )


class MarkdownAdapter(NativeAdapter):
    name = "native-markdown"
    suffixes = (".md", ".markdown")
    mime_types = ("text/markdown",)

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        raw = self._load(source, budget, cancellation, started)
        text, encoding = _decode_text(raw)
        parts = _heading_sections(text, kind="markdown")
        return self._ready_result(source, raw, parts, budget, cancellation, started, warnings=(f"encoding:{encoding}",))


class HtmlAdapter(NativeAdapter):
    name = "native-html"
    suffixes = (".html", ".htm")
    mime_types = ("text/html",)
    dependency = "bs4"

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        if not self.health().available:
            raise ConversionError("ADAPTER_UNAVAILABLE", self.health().reason)
        raw = self._load(source, budget, cancellation, started)
        text, encoding = _decode_text(raw)
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "html.parser")
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        parts: list[ConvertedPart] = []
        headings = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        if headings:
            for heading in headings:
                body: list[str] = []
                for sibling in heading.next_siblings:
                    if getattr(sibling, "name", "") in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                        break
                    value = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
                    if value:
                        body.append(value)
                combined = "\n".join(body).strip() or heading.get_text(" ", strip=True)
                parts.append(
                    ConvertedPart(
                        text=combined,
                        ordinal=len(parts) + 1,
                        heading=heading.get_text(" ", strip=True),
                        kind="html-section",
                    )
                )
                self._checkpoint(
                    budget,
                    cancellation,
                    started,
                    output_chars=sum(len(part.text) for part in parts),
                )
        else:
            plain = soup.get_text("\n", strip=True)
            parts = [ConvertedPart(text=plain, ordinal=1, kind="html")]
        return self._ready_result(source, raw, parts, budget, cancellation, started, warnings=(f"encoding:{encoding}",))


class PdfAdapter(NativeAdapter):
    name = "native-pdf"
    suffixes = (".pdf",)
    mime_types = ("application/pdf",)
    dependency = "fitz"

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        if not self.health().available:
            raise ConversionError("ADAPTER_UNAVAILABLE", self.health().reason)
        raw = self._load(source, budget, cancellation, started)
        if not raw.startswith(b"%PDF-"):
            raise ConversionError("SOURCE_FORMAT_INVALID", "PDF header does not match its declared format")
        import fitz

        try:
            document = fitz.open(stream=raw, filetype="pdf")
        except Exception as exc:
            raise ConversionError("PDF_PARSE_FAILED", f"PDF could not be parsed: {exc}") from exc
        parts: list[ConvertedPart] = []
        warnings: list[str] = []
        try:
            for index, page in enumerate(document, start=1):
                self._checkpoint(budget, cancellation, started)
                try:
                    blocks = page.get_text("blocks", sort=True)
                    text = "\n".join(
                        str(block[4]).strip()
                        for block in sorted(blocks, key=lambda block: (round(float(block[1]) / 4), float(block[0])))
                        if len(block) > 4 and str(block[4]).strip()
                    )
                    if text:
                        warnings.append(f"page:{index}:layout_blocks")
                except Exception:
                    text = page.get_text("text", sort=True).strip()
                    warnings.append(f"page:{index}:plain_text_fallback")
                if text:
                    parts.append(
                        ConvertedPart(
                            text=text,
                            ordinal=len(parts) + 1,
                            page_start=index,
                            page_end=index,
                            kind="pdf-page",
                        )
                    )
                    self._checkpoint(
                        budget,
                        cancellation,
                        started,
                        output_chars=sum(len(part.text) for part in parts),
                    )
            page_count = document.page_count
        finally:
            document.close()
        if not parts:
            raise ConversionError(
                "OCR_REQUIRED",
                "PDF contains no extractable text and requires OCR",
                details={"page_count": page_count},
            )
        return self._ready_result(
            source,
            raw,
            parts,
            budget,
            cancellation,
            started,
            page_count=page_count,
            warnings=warnings,
        )


class DocxAdapter(NativeAdapter):
    name = "native-docx"
    suffixes = (".docx",)
    mime_types = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",)
    dependency = "docx"

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        if not self.health().available:
            raise ConversionError("ADAPTER_UNAVAILABLE", self.health().reason)
        raw = self._load(source, budget, cancellation, started)
        if not raw.startswith(b"PK"):
            raise ConversionError("SOURCE_FORMAT_INVALID", "DOCX ZIP header does not match its declared format")
        from docx import Document

        try:
            document = Document(io.BytesIO(raw))
        except Exception as exc:
            raise ConversionError("DOCX_PARSE_FAILED", f"DOCX could not be parsed: {exc}") from exc
        parts: list[ConvertedPart] = []
        heading = ""
        buffer: list[str] = []

        def flush() -> None:
            text = "\n".join(buffer).strip()
            if text:
                parts.append(
                    ConvertedPart(
                        text=text,
                        ordinal=len(parts) + 1,
                        heading=heading,
                        kind="docx-section",
                    )
                )

        for paragraph in document.paragraphs:
            self._checkpoint(budget, cancellation, started)
            text = paragraph.text.strip()
            if not text:
                continue
            style = str(getattr(paragraph.style, "name", "") or "").lower()
            if style.startswith("heading"):
                flush()
                buffer.clear()
                heading = text
                buffer.append(text)
            else:
                buffer.append(text)
            self._checkpoint(
                budget,
                cancellation,
                started,
                output_chars=sum(len(part.text) for part in parts) + sum(len(item) for item in buffer),
            )
        flush()
        return self._ready_result(source, raw, parts, budget, cancellation, started)


class PptxAdapter(NativeAdapter):
    name = "native-pptx"
    suffixes = (".pptx",)
    mime_types = ("application/vnd.openxmlformats-officedocument.presentationml.presentation",)

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        raw = self._load(source, budget, cancellation, started)
        if not raw.startswith(b"PK"):
            raise ConversionError("SOURCE_FORMAT_INVALID", "PPTX ZIP header does not match its declared format")
        parts: list[ConvertedPart] = []
        with _BoundedArchive(raw, budget) as archive:
            slides = sorted(
                (name for name in archive.names() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                key=_natural_key,
            )
            if not slides:
                raise ConversionError("PPTX_STRUCTURE_INVALID", "PPTX contains no slide XML")
            for page, name in enumerate(slides, start=1):
                self._checkpoint(budget, cancellation, started)
                root = _xml(archive.read(name), code="PPTX_PARSE_FAILED")
                texts = _xml_texts(root)
                if texts:
                    parts.append(
                        ConvertedPart(
                            text="\n".join(texts),
                            ordinal=len(parts) + 1,
                            heading=texts[0][:160],
                            page_start=page,
                            page_end=page,
                            kind="pptx-slide",
                        )
                    )
                self._checkpoint(
                    budget,
                    cancellation,
                    started,
                    output_chars=sum(len(part.text) for part in parts),
                )
        return self._ready_result(
            source, raw, parts, budget, cancellation, started, page_count=len(slides)
        )


class XlsxAdapter(NativeAdapter):
    name = "native-xlsx"
    suffixes = (".xlsx",)
    mime_types = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)

    @staticmethod
    def _relationships(root: ElementTree.Element) -> dict[str, str]:
        return {
            str(item.attrib.get("Id")): str(item.attrib.get("Target"))
            for item in root
            if item.attrib.get("Id") and item.attrib.get("Target")
        }

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        raw = self._load(source, budget, cancellation, started)
        if not raw.startswith(b"PK"):
            raise ConversionError("SOURCE_FORMAT_INVALID", "XLSX ZIP header does not match its declared format")
        parts: list[ConvertedPart] = []
        with _BoundedArchive(raw, budget) as archive:
            names = set(archive.names())
            if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
                raise ConversionError("XLSX_STRUCTURE_INVALID", "XLSX workbook metadata is missing")
            workbook = _xml(archive.read("xl/workbook.xml"), code="XLSX_PARSE_FAILED")
            rels = self._relationships(
                _xml(archive.read("xl/_rels/workbook.xml.rels"), code="XLSX_PARSE_FAILED")
            )
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                shared_root = _xml(archive.read("xl/sharedStrings.xml"), code="XLSX_PARSE_FAILED")
                shared = [
                    " ".join(_xml_texts(item))
                    for item in shared_root
                    if item.tag.rsplit("}", 1)[-1] == "si"
                ]
            sheets = [
                (
                    item.attrib.get("name") or f"Sheet {index}",
                    item.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", ""),
                )
                for index, item in enumerate(workbook.iter(), start=1)
                if item.tag.rsplit("}", 1)[-1] == "sheet"
            ]
            for sheet_name, relation_id in sheets:
                self._checkpoint(budget, cancellation, started)
                target = rels.get(relation_id, "")
                target = target.lstrip("/")
                member = target if target.startswith("xl/") else f"xl/{target}"
                member = str(PurePosixPath(member))
                if member not in names:
                    continue
                sheet_root = _xml(archive.read(member), code="XLSX_PARSE_FAILED")
                rows: list[str] = []
                sheet_chars = 0
                for row in (item for item in sheet_root.iter() if item.tag.rsplit("}", 1)[-1] == "row"):
                    values: list[str] = []
                    for cell in (item for item in row if item.tag.rsplit("}", 1)[-1] == "c"):
                        cell_type = cell.attrib.get("t", "")
                        formula = next(
                            ((item.text or "") for item in cell if item.tag.rsplit("}", 1)[-1] == "f"),
                            "",
                        )
                        value = next(
                            ((item.text or "") for item in cell if item.tag.rsplit("}", 1)[-1] == "v"),
                            "",
                        )
                        if cell_type == "s" and value.isdigit() and int(value) < len(shared):
                            value = shared[int(value)]
                        elif cell_type == "inlineStr":
                            value = " ".join(_xml_texts(cell))
                        if formula:
                            value = f"={formula}" + (f" [{value}]" if value else "")
                        values.append(value.strip())
                    if any(values):
                        rendered_row = "\t".join(values)
                        rows.append(rendered_row)
                        sheet_chars += len(rendered_row)
                    self._checkpoint(
                        budget,
                        cancellation,
                        started,
                        output_chars=sum(len(part.text) for part in parts) + sheet_chars,
                    )
                if rows:
                    parts.append(
                        ConvertedPart(
                            text="\n".join(rows),
                            ordinal=len(parts) + 1,
                            heading=str(sheet_name)[:160],
                            kind="xlsx-sheet",
                        )
                    )
        return self._ready_result(source, raw, parts, budget, cancellation, started)


class EpubAdapter(NativeAdapter):
    name = "native-epub"
    suffixes = (".epub",)
    mime_types = ("application/epub+zip",)
    dependency = "bs4"

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        if not self.health().available:
            raise ConversionError("ADAPTER_UNAVAILABLE", self.health().reason)
        raw = self._load(source, budget, cancellation, started)
        if not raw.startswith(b"PK"):
            raise ConversionError("SOURCE_FORMAT_INVALID", "EPUB ZIP header does not match its declared format")
        from bs4 import BeautifulSoup

        parts: list[ConvertedPart] = []
        with _BoundedArchive(raw, budget) as archive:
            names = set(archive.names())
            if "META-INF/container.xml" not in names:
                raise ConversionError("EPUB_STRUCTURE_INVALID", "EPUB container metadata is missing")
            container = _xml(archive.read("META-INF/container.xml"), code="EPUB_PARSE_FAILED")
            rootfile = next(
                (
                    item.attrib.get("full-path", "")
                    for item in container.iter()
                    if item.tag.rsplit("}", 1)[-1] == "rootfile"
                ),
                "",
            )
            if not rootfile or rootfile not in names:
                raise ConversionError("EPUB_STRUCTURE_INVALID", "EPUB package document is missing")
            package = _xml(archive.read(rootfile), code="EPUB_PARSE_FAILED")
            manifest = {
                item.attrib.get("id", ""): item.attrib.get("href", "")
                for item in package.iter()
                if item.tag.rsplit("}", 1)[-1] == "item"
            }
            spine = [
                item.attrib.get("idref", "")
                for item in package.iter()
                if item.tag.rsplit("}", 1)[-1] == "itemref"
            ]
            base = PurePosixPath(rootfile).parent
            for item_id in spine:
                self._checkpoint(budget, cancellation, started)
                href = manifest.get(item_id, "")
                member = str(base / href)
                if not href or member not in names:
                    continue
                soup = BeautifulSoup(archive.read(member), "html.parser")
                for element in soup(["script", "style", "noscript"]):
                    element.decompose()
                text = soup.get_text("\n", strip=True)
                if text:
                    title = soup.find(["h1", "h2", "title"])
                    parts.append(
                        ConvertedPart(
                            text=text,
                            ordinal=len(parts) + 1,
                            heading=(title.get_text(" ", strip=True) if title else item_id)[:160],
                            kind="epub-section",
                        )
                    )
                self._checkpoint(
                    budget,
                    cancellation,
                    started,
                    output_chars=sum(len(part.text) for part in parts),
                )
        return self._ready_result(source, raw, parts, budget, cancellation, started)


class UnsupportedMediaAdapter(NativeAdapter):
    """Declare a safe degradation instead of pretending OCR/transcription happened."""

    name = "multimodal-deferred"
    suffixes = (
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
        ".wav", ".mp3", ".m4a", ".ogg", ".flac",
    )
    mime_types = (
        "image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp",
        "image/tiff", "audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/flac",
    )

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        cancellation = cancellation or CancellationToken()
        cancellation.raise_if_cancelled()
        media = "OCR" if source.suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"} else "transcription"
        raise ConversionError(
            "MULTIMODAL_EXTRACTION_REQUIRED",
            f"{source.suffix or source.mime_type} requires an explicit {media} adapter",
            retryable=False,
            details={"degradation": "metadata_only", "required_capability": media.lower()},
        )


DEFAULT_ADAPTERS: tuple[ConversionAdapter, ...] = (
    CodeAdapter(),
    PlainTextAdapter(),
    MarkdownAdapter(),
    HtmlAdapter(),
    PdfAdapter(),
    DocxAdapter(),
    PptxAdapter(),
    XlsxAdapter(),
    EpubAdapter(),
    UnsupportedMediaAdapter(),
)


class ConversionRegistry:
    def __init__(
        self,
        adapters: Sequence[ConversionAdapter] = DEFAULT_ADAPTERS,
        *,
        disabled: Sequence[str] = (),
    ) -> None:
        self._adapters = tuple(adapters)
        self._disabled = frozenset(disabled)
        names = [adapter.name for adapter in self._adapters]
        if len(names) != len(set(names)):
            raise ValueError("adapter names must be unique")

    def select(self, source: ConversionSource) -> ConversionAdapter:
        matching = [adapter for adapter in self._adapters if adapter.can_handle(source)]
        if not matching:
            raise ConversionError(
                "UNSUPPORTED_FORMAT",
                f"No conversion adapter supports '{source.suffix or source.mime_type}'",
                details={"source_name": source.name, "mime_type": source.mime_type},
            )
        adapter = matching[0]
        if adapter.name in self._disabled:
            raise ConversionError(
                "ADAPTER_DISABLED",
                f"Conversion adapter '{adapter.name}' is disabled",
                details={"adapter": adapter.name},
            )
        health = adapter.health()
        if not health.available:
            raise ConversionError(
                "ADAPTER_UNAVAILABLE",
                health.reason,
                details={"adapter": adapter.name},
            )
        return adapter

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        cancellation = cancellation or CancellationToken()
        cancellation.raise_if_cancelled()
        adapter = self.select(source)
        return adapter.convert(source, budget=budget, cancellation=cancellation)

    def convert_safe(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        try:
            return self.convert(source, budget=budget, cancellation=cancellation)
        except Exception as exc:
            adapter = next(
                (item for item in self._adapters if item.can_handle(source)),
                None,
            )
            if adapter is None:
                adapter = NativeAdapter()
                adapter.name = "registry"
            return ConversionResult(status="failed", failure=adapter.failure(exc))

    def convert_many_safe(
        self,
        sources: Sequence[ConversionSource],
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> tuple[ConversionResult, ...]:
        """Convert each source independently so one corrupt file cannot abort a batch."""
        return tuple(
            self.convert_safe(source, budget=budget, cancellation=cancellation)
            for source in sources
        )

    def health(self) -> tuple[AdapterHealth, ...]:
        return tuple(
            AdapterHealth(
                available=health.available and adapter.name not in self._disabled,
                adapter=health.adapter,
                version=health.version,
                reason=("disabled" if adapter.name in self._disabled else health.reason),
                supported_suffixes=health.supported_suffixes,
            )
            for adapter in self._adapters
            for health in (adapter.health(),)
        )
