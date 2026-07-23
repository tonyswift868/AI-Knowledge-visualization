from __future__ import annotations

import hashlib
import importlib.util
import io
import mimetypes
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class ConversionBudget:
    max_input_bytes: int = 20 * 1024 * 1024
    max_output_chars: int = 2_000_000
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_input_bytes <= 0:
            raise ValueError("max_input_bytes must be positive")
        if self.max_output_chars <= 0:
            raise ValueError("max_output_chars must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


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
        try:
            for index, page in enumerate(document, start=1):
                self._checkpoint(budget, cancellation, started)
                text = page.get_text("text").strip()
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
        return self._ready_result(source, raw, parts, budget, cancellation, started, page_count=page_count)


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


DEFAULT_ADAPTERS: tuple[ConversionAdapter, ...] = (
    PlainTextAdapter(),
    MarkdownAdapter(),
    HtmlAdapter(),
    PdfAdapter(),
    DocxAdapter(),
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
