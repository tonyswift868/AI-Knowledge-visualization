from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import mimetypes
import os
import re
import threading
import time
import uuid
import wave
import zipfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Protocol, Sequence
from xml.etree import ElementTree

from .knowledge_policy import CONVERSION_POLICY
from .safe_xml import XMLSecurityError, fromstring as safe_xml_fromstring


def document_runtime_cache_root() -> Path:
    """Return the explicit document-model cache without using a user-profile default."""

    configured = os.getenv("MENTORFLOW_DOCUMENT_CACHE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "data" / "runtime-cache" / "documents").resolve()


def _configure_document_runtime_cache() -> Path:
    root = document_runtime_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    cache_locations = {
        "DOCLING_CACHE_DIR": root / "docling",
        "HF_HOME": root / "huggingface",
        "HF_HUB_CACHE": root / "huggingface" / "hub",
        "HUGGINGFACE_HUB_CACHE": root / "huggingface" / "hub",
        "TORCH_HOME": root / "torch",
        "TORCHINDUCTOR_CACHE_DIR": root / "torchinductor",
        "XDG_CACHE_HOME": root / "xdg",
    }
    for name, location in cache_locations.items():
        location.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(name, str(location))
    return root


@dataclass(frozen=True)
class ConversionBudget:
    max_input_bytes: int = CONVERSION_POLICY.maximum_input_bytes
    max_output_chars: int = CONVERSION_POLICY.maximum_output_characters
    timeout_seconds: float = CONVERSION_POLICY.timeout_seconds
    max_expanded_bytes: int = CONVERSION_POLICY.maximum_expanded_bytes
    max_archive_entries: int = CONVERSION_POLICY.maximum_archive_entries

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
    bbox: tuple[float, ...] | None = None
    audio_start_ms: int | None = None
    audio_end_ms: int | None = None


@dataclass(frozen=True)
class AdapterHealth:
    available: bool
    adapter: str
    version: str
    reason: str = ""
    supported_suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterialCapability:
    """Public capability receipt for one adapter and format family."""

    adapter: str
    adapter_version: str
    available: bool
    suffixes: tuple[str, ...] = ()
    mime_types: tuple[str, ...] = ()
    signatures: tuple[str, ...] = ()
    reason: str = ""
    rejected: bool = False


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
    signatures: tuple[bytes, ...] = ()
    rejects = False

    def can_handle(self, source: ConversionSource) -> bool:
        mime = source.mime_type.split(";", 1)[0].strip().lower()
        return source.suffix in self.suffixes or bool(mime and mime in self.mime_types)

    def signature_matches(self, raw: bytes | None) -> bool | None:
        """Return None when no bytes are available, otherwise inspect magic bytes."""
        if raw is None or not self.signatures:
            return None
        return any(raw.startswith(signature) for signature in self.signatures)

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


def _validate_text_quality(text: str) -> None:
    if not text.strip():
        raise ConversionError("EMPTY_CONVERSION", "Source contains no usable text")
    if "\x00" in text:
        raise ConversionError("TEXT_BINARY_MISMATCH", "Text contains NUL bytes and does not match a text format")
    sample = text[: CONVERSION_POLICY.text_quality_sample_characters]
    control_count = sum(
        1
        for character in sample
        if ord(character) < 32 and character not in {"\n", "\r", "\t", "\f"}
    )
    if (
        control_count / max(1, len(sample))
        > CONVERSION_POLICY.maximum_control_character_ratio
    ):
        raise ConversionError("TEXT_CONTROL_CHARACTERS", "Text contains an unsafe ratio of control characters")
    if (
        sample.count("\ufffd") / max(1, len(sample))
        > CONVERSION_POLICY.maximum_replacement_character_ratio
    ):
        raise ConversionError("TEXT_DECODE_UNRELIABLE", "Text contains too many decoding replacement characters")


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
    try:
        return safe_xml_fromstring(raw)
    except XMLSecurityError as exc:
        raise ConversionError(
            "XML_DTD_FORBIDDEN",
            "Document XML declarations and entities are unsupported",
        ) from exc
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
    suffixes = (".txt", ".rtf", ".log", ".ini", ".cfg")
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
        _validate_text_quality(text)
        parts = [ConvertedPart(text=text.strip(), ordinal=1, kind="text")]
        return self._ready_result(source, raw, parts, budget, cancellation, started, warnings=(f"encoding:{encoding}",))


class StructuredDataAdapter(NativeAdapter):
    """Render structured data as stable path-addressed evidence segments."""

    name = "native-structured-data"
    suffixes = (".json", ".jsonl", ".yaml", ".yml", ".xml", ".csv", ".tsv")
    mime_types = (
        "application/json",
        "application/yaml",
        "application/xml",
        "text/csv",
        "text/tab-separated-values",
        "text/xml",
    )

    @staticmethod
    def _json_parts(value: Any, path: str = "$") -> list[tuple[str, str]]:
        if isinstance(value, dict):
            result: list[tuple[str, str]] = []
            for key, child in value.items():
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                result.extend(StructuredDataAdapter._json_parts(child, f"{path}/{escaped}"))
            return result or [(path, "{}")]
        if isinstance(value, list):
            result = []
            for index, child in enumerate(value):
                result.extend(StructuredDataAdapter._json_parts(child, f"{path}/{index}"))
            return result or [(path, "[]")]
        return [(path, json.dumps(value, ensure_ascii=False))]

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
        _validate_text_quality(text)
        suffix = source.suffix
        values: list[tuple[str, str]] = []
        try:
            if suffix == ".json":
                values = self._json_parts(json.loads(text))
            elif suffix == ".jsonl":
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if line.strip():
                        values.extend(self._json_parts(json.loads(line), f"$/line/{line_number}"))
            elif suffix in {".yaml", ".yml"}:
                import yaml

                for document_index, document in enumerate(yaml.safe_load_all(text), start=1):
                    values.extend(self._json_parts(document, f"$/document/{document_index}"))
            elif suffix == ".xml":
                root = _xml(raw, code="XML_PARSE_FAILED")

                def walk(element: ElementTree.Element, path: str) -> None:
                    tag = element.tag.rsplit("}", 1)[-1]
                    current = f"{path}/{tag}"
                    for key, item in sorted(element.attrib.items()):
                        values.append((f"{current}/@{key}", str(item)))
                    if (element.text or "").strip():
                        values.append((current, (element.text or "").strip()))
                    for child in element:
                        walk(child, current)

                walk(root, "$")
            else:
                import csv

                delimiter = "\t" if suffix == ".tsv" else ","
                rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
                headers = rows[0] if rows else []
                for row_index, row in enumerate(rows[1:] if headers else rows, start=1):
                    rendered = {
                        (headers[index] if index < len(headers) and headers[index] else f"column_{index + 1}"): value
                        for index, value in enumerate(row)
                    }
                    values.append((f"$/row/{row_index}", json.dumps(rendered, ensure_ascii=False)))
        except (ValueError, ElementTree.ParseError) as exc:
            family = {
                ".json": "JSON",
                ".jsonl": "JSON Lines",
                ".yaml": "YAML",
                ".yml": "YAML",
                ".xml": "XML",
                ".csv": "CSV",
                ".tsv": "TSV",
            }.get(suffix, "结构化资料")
            raise ConversionError("STRUCTURED_DATA_INVALID", f"{family} 语法错误：{exc}") from exc
        parts = [
            ConvertedPart(text=f"{path}: {value}", ordinal=index, heading=path, kind="structured-path")
            for index, (path, value) in enumerate(values, start=1)
            if str(value).strip()
        ]
        return self._ready_result(source, raw, parts, budget, cancellation, started, warnings=(f"encoding:{encoding}",))


class CodeAdapter(NativeAdapter):
    name = "native-code"
    suffixes = (
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".c", ".h",
        ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".kts", ".sql",
        ".sh", ".ps1", ".bat", ".css", ".toml",
    )
    mime_types = ("application/json", "text/x-python", "text/x-script")

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
        _validate_text_quality(text)
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
        _validate_text_quality(text)
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
        _validate_text_quality(text)
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


class DoclingDocumentAdapter(NativeAdapter):
    """Optional Docling conversion for layout-aware document evidence.

    Docling stays an independently installable, pinned capability because its
    ML/document stack is substantially larger than the base runtime.  When it
    is present, the registry prefers it for document formats; when absent, the
    capability receipt reports that fact and the healthy native adapters may
    still serve formats they fully support.
    """

    name = "docling-document"
    version = CONVERSION_POLICY.docling.version
    suffixes = (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".epub")
    mime_types = (
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/html",
        "application/epub+zip",
    )
    dependency = "docling"
    signatures = (b"%PDF-",)

    @staticmethod
    def _normalized_coverage_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    def _supplement_native_pdf_text(
        self,
        raw: bytes,
        parts: Sequence[ConvertedPart],
        *,
        budget: ConversionBudget,
        cancellation: CancellationToken,
        started: float,
    ) -> tuple[list[ConvertedPart], int, int]:
        """Add native PDF blocks that Docling did not expose.

        Some mixed PDFs yield only OCR-derived Docling items even though other
        pages contain an intact text layer.  PyMuPDF is used only to recover
        those native text blocks; scanned pages remain Docling/RapidOCR output.
        """

        import fitz

        try:
            pdf_document = fitz.open(stream=raw, filetype="pdf")
        except Exception as exc:
            raise ConversionError(
                "PDF_PARSE_FAILED", f"PDF could not be parsed: {exc}"
            ) from exc

        merged = list(parts)
        paged_coverage: dict[int, str] = {}
        unpaged_coverage = self._normalized_coverage_text(
            "\n".join(part.text for part in parts if part.page_start is None)
        )
        for part in parts:
            if part.page_start is None:
                continue
            page_number = int(part.page_start)
            normalized = self._normalized_coverage_text(part.text)
            if normalized:
                current = paged_coverage.get(page_number, "")
                paged_coverage[page_number] = f"{current} {normalized}".strip()

        supplement_count = 0
        try:
            page_count = pdf_document.page_count
            for page_number, page in enumerate(pdf_document, start=1):
                self._checkpoint(
                    budget,
                    cancellation,
                    started,
                    output_chars=sum(len(part.text) for part in merged),
                )
                try:
                    blocks = page.get_text("blocks", sort=True)
                except Exception:
                    blocks = ()
                for block in sorted(
                    blocks,
                    key=lambda value: (
                        round(float(value[1]) / 4),
                        float(value[0]),
                    ),
                ):
                    if len(block) <= 4:
                        continue
                    text = str(block[4]).strip()
                    normalized = self._normalized_coverage_text(text)
                    if not normalized:
                        continue
                    page_coverage = paged_coverage.get(page_number, "")
                    if normalized in page_coverage or normalized in unpaged_coverage:
                        continue
                    merged.append(
                        ConvertedPart(
                            text=text,
                            ordinal=len(merged) + 1,
                            page_start=page_number,
                            page_end=page_number,
                            kind="pdf-native-supplement",
                            bbox=tuple(float(value) for value in block[:4]),
                        )
                    )
                    supplement_count += 1
                    paged_coverage[page_number] = (
                        f"{page_coverage} {normalized}".strip()
                    )
        finally:
            pdf_document.close()

        ordered = sorted(
            enumerate(merged),
            key=lambda item: (
                item[1].page_start is None,
                item[1].page_start or 0,
                item[0],
            ),
        )
        return (
            [replace(part, ordinal=index) for index, (_position, part) in enumerate(ordered, start=1)],
            page_count,
            supplement_count,
        )

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = (
            budget or ConversionBudget(),
            cancellation or CancellationToken(),
            time.monotonic(),
        )
        if not self.health().available:
            raise ConversionError("ADAPTER_UNAVAILABLE", self.health().reason)
        raw = self._load(source, budget, cancellation, started)
        is_pdf = source.suffix == ".pdf" or raw.startswith(b"%PDF-")
        if is_pdf:
            try:
                import fitz

                pdf_document = fitz.open(stream=raw, filetype="pdf")
            except Exception as exc:
                raise ConversionError(
                    "PDF_PARSE_FAILED", f"PDF could not be parsed: {exc}"
                ) from exc
            try:
                if pdf_document.needs_pass:
                    raise ConversionError(
                        "PDF_ENCRYPTED",
                        "Password-protected PDF documents require an unprotected copy",
                    )
            finally:
                pdf_document.close()
        try:
            _configure_document_runtime_cache()
            from docling.datamodel.base_models import DocumentStream, InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            converter = DocumentConverter()
            if is_pdf:
                policy = CONVERSION_POLICY.docling
                pipeline_options = PdfPipelineOptions(
                    document_timeout=budget.timeout_seconds,
                    do_ocr=policy.enable_pdf_ocr,
                    do_table_structure=policy.enable_table_structure,
                    ocr_options=RapidOcrOptions(
                        backend=policy.ocr_backend,
                        lang=list(policy.ocr_languages),
                        text_score=policy.ocr_text_score,
                        scale=policy.ocr_scale,
                    ),
                )
                pipeline_options.layout_options.engine_options.compile_model = (
                    policy.compile_layout_model
                )
                converter = DocumentConverter(
                    format_options={
                        InputFormat.PDF: PdfFormatOption(
                            pipeline_options=pipeline_options
                        )
                    }
                )

            result = converter.convert(
                DocumentStream(name=source.name, stream=io.BytesIO(raw))
            )
            document = result.document
        except Exception as exc:
            format_code = (
                "PDF_PARSE_FAILED"
                if is_pdf
                else {
                    ".docx": "DOCX_PARSE_FAILED",
                    ".pptx": "PPTX_PARSE_FAILED",
                    ".xlsx": "XLSX_PARSE_FAILED",
                    ".epub": "EPUB_PARSE_FAILED",
                    ".html": "HTML_PARSE_FAILED",
                    ".htm": "HTML_PARSE_FAILED",
                }.get(source.suffix, "DOCUMENT_PARSE_FAILED")
            )
            raise ConversionError(
                format_code,
                f"Docling could not convert the document: {exc}",
                retryable=False,
            ) from exc

        parts: list[ConvertedPart] = []
        for item, _level in document.iterate_items():
            cancellation.raise_if_cancelled()
            text = str(getattr(item, "text", "") or "").strip()
            if not text:
                continue
            provenance = list(getattr(item, "prov", None) or [])
            first = provenance[0] if provenance else None
            page = int(getattr(first, "page_no", 0) or 0) or None
            box = getattr(first, "bbox", None) if first is not None else None
            coords = tuple(
                float(getattr(box, key))
                for key in ("l", "t", "r", "b")
                if box is not None and getattr(box, key, None) is not None
            )
            parts.append(
                ConvertedPart(
                    text=text,
                    ordinal=len(parts) + 1,
                    heading=str(getattr(item, "label", "") or ""),
                    page_start=page,
                    page_end=page,
                    kind="docling-item",
                    bbox=coords if len(coords) == 4 else None,
                )
            )
            self._checkpoint(
                budget,
                cancellation,
                started,
                output_chars=sum(len(part.text) for part in parts),
            )
        if not parts:
            markdown = str(document.export_to_markdown() or "").strip()
            if markdown:
                parts = [ConvertedPart(text=markdown, ordinal=1, kind="docling-markdown")]
        pages = [part.page_end for part in parts if part.page_end is not None]
        page_count = max(pages, default=0)
        warnings = ["converter:docling"]
        if is_pdf:
            parts, page_count, supplement_count = self._supplement_native_pdf_text(
                raw,
                parts,
                budget=budget,
                cancellation=cancellation,
                started=started,
            )
            if supplement_count:
                warnings.append(f"pdf:native-supplement:{supplement_count}")
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


class PdfAdapter(NativeAdapter):
    name = "native-pdf"
    suffixes = (".pdf",)
    mime_types = ("application/pdf",)
    dependency = "fitz"
    signatures = (b"%PDF-",)

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
        if document.needs_pass:
            document.close()
            raise ConversionError(
                "PDF_ENCRYPTED",
                "PDF is password protected; provide an unlocked copy before generating a knowledge map",
            )
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
                elif importlib.util.find_spec("rapidocr") is not None:
                    try:
                        scale = CONVERSION_POLICY.docling.ocr_scale
                        ocr_raw = page.get_pixmap(
                            matrix=fitz.Matrix(scale, scale),
                            alpha=False,
                        ).tobytes("png")
                        page_parts = _ocr_parts(ocr_raw, ".png", page=index)
                    except Exception as exc:
                        warnings.append(f"page:{index}:ocr_failed:{type(exc).__name__}")
                    else:
                        base_ordinal = len(parts)
                        parts.extend(
                            ConvertedPart(
                                text=item.text,
                                ordinal=base_ordinal + offset,
                                heading=item.heading,
                                page_start=index,
                                page_end=index,
                                kind="pdf-ocr-line",
                                bbox=item.bbox,
                            )
                            for offset, item in enumerate(page_parts, start=1)
                        )
                        warnings.append(f"page:{index}:rapidocr")
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
    signatures = (b"PK",)

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
    signatures = (b"PK",)

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
    signatures = (b"PK",)

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
    signatures = (b"PK",)

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


_OCR_LOCK = threading.RLock()
_OCR_ENGINE: Any | None = None


def _rapidocr_engine() -> Any:
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE
    with _OCR_LOCK:
        if _OCR_ENGINE is None:
            from rapidocr import RapidOCR

            _OCR_ENGINE = RapidOCR()
        return _OCR_ENGINE


def _ocr_parts(raw: bytes, suffix: str, *, page: int | None = None) -> list[ConvertedPart]:
    runtime = Path(__file__).resolve().parents[2] / "runtime" / "conversion"
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / f"ocr-{uuid.uuid4().hex}{suffix or '.png'}"
    target.write_bytes(raw)
    try:
        with _OCR_LOCK:
            result = _rapidocr_engine()(str(target))
    finally:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
    raw_texts = getattr(result, "txts", None)
    raw_boxes = getattr(result, "boxes", None)
    texts = list(raw_texts) if raw_texts is not None else []
    boxes = list(raw_boxes) if raw_boxes is not None else []
    parts: list[ConvertedPart] = []
    for index, text in enumerate(texts, start=1):
        value = str(text).strip()
        if not value:
            continue
        box = boxes[index - 1] if index - 1 < len(boxes) else None
        flattened = tuple(float(item) for point in box for item in point) if box is not None else ()
        parts.append(
            ConvertedPart(
                text=value,
                ordinal=index,
                heading=f"OCR line {index}",
                page_start=page,
                page_end=page,
                kind="ocr-line",
                bbox=flattened or None,
            )
        )
    return parts


class ImageOcrAdapter(NativeAdapter):
    name = "rapidocr-image"
    version = "rapidocr-ppocrv6"
    suffixes = (".png", ".jpg", ".jpeg", ".webp")
    mime_types = (
        "image/png", "image/jpeg", "image/webp",
    )
    dependency = "rapidocr"
    signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")

    def signature_matches(self, raw: bytes | None) -> bool | None:
        if raw is None:
            return None
        return bool(
            raw.startswith(b"\x89PNG\r\n\x1a\n")
            or raw.startswith(b"\xff\xd8\xff")
            or (raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP")
        )

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
        parts = _ocr_parts(raw, source.suffix)
        if not parts:
            raise ConversionError("OCR_NO_TEXT", "Image OCR found no usable text", retryable=True)
        return self._ready_result(source, raw, parts, budget, cancellation, started)


class AudioTranscriptionAdapter(NativeAdapter):
    name = "sensevoice-audio"
    version = "sherpa-onnx-sensevoice-int8"
    suffixes = (".wav", ".mp3", ".m4a", ".ogg", ".webm")
    mime_types = ("audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/webm")
    signatures = (b"RIFF", b"ID3", b"OggS", b"\x1aE\xdf\xa3")

    def signature_matches(self, raw: bytes | None) -> bool | None:
        if raw is None:
            return None
        return bool(
            (raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WAVE")
            or raw.startswith(b"ID3")
            or raw.startswith(b"OggS")
            or raw.startswith(b"\x1aE\xdf\xa3")
        )

    def health(self) -> AdapterHealth:
        try:
            from . import asr_supervisor

            available = asr_supervisor.assets_ready()
        except Exception:
            available = False
        return AdapterHealth(
            available=available,
            adapter=self.name,
            version=self.version,
            reason="" if available else "SenseVoice assets or worker are unavailable",
            supported_suffixes=self.suffixes,
        )

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        from . import asr_supervisor

        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        raw = self._load(source, budget, cancellation, started)
        duration_ms: int | None = None
        if source.suffix == ".wav":
            try:
                with wave.open(io.BytesIO(raw), "rb") as audio:
                    duration_ms = round(audio.getnframes() * 1000 / max(1, audio.getframerate()))
            except (EOFError, wave.Error):
                duration_ms = None
        if not self.health().available:
            raise ConversionError("ADAPTER_UNAVAILABLE", self.health().reason, retryable=True)
        root = asr_supervisor.upload_root()
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"material-{uuid.uuid4().hex}{source.suffix or '.wav'}"
        target.write_bytes(raw)
        try:
            response = asr_supervisor.transcribe(target, timeout=budget.timeout_seconds)
        finally:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        text = str(response.get("text") or "").strip()
        if not response.get("ok") or not text:
            code = "AUDIO_NO_SPEECH" if response.get("blank_audio") else "AUDIO_TRANSCRIPTION_FAILED"
            raise ConversionError(code, str(response.get("error") or "Audio produced no usable transcript"), retryable=True)
        segments = response.get("segments") if isinstance(response.get("segments"), list) else []
        parts = [
            ConvertedPart(
                text=str(item.get("text") or "").strip(),
                ordinal=index,
                heading=f"Audio {index}",
                kind="audio-transcript",
                audio_start_ms=max(0, int(item.get("start_ms") or 0)),
                audio_end_ms=max(0, int(item.get("end_ms") or item.get("duration_ms") or duration_ms or 0)) or None,
            )
            for index, item in enumerate(segments, start=1)
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        if not parts:
            parts = [
                ConvertedPart(
                    text=text,
                    ordinal=1,
                    heading="Audio transcript",
                    kind="audio-transcript",
                    audio_start_ms=0,
                    audio_end_ms=max(0, int(response.get("duration_ms") or duration_ms or 0)) or None,
                )
            ]
        return self._ready_result(source, raw, parts, budget, cancellation, started)


class SafeZipAdapter(NativeAdapter):
    name = "safe-zip-container"
    suffixes = (".zip",)
    mime_types = ("application/zip",)
    signatures = (b"PK",)

    def convert(
        self,
        source: ConversionSource,
        *,
        budget: ConversionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ConversionResult:
        budget, cancellation, started = budget or ConversionBudget(), cancellation or CancellationToken(), time.monotonic()
        raw = self._load(source, budget, cancellation, started)
        parts: list[ConvertedPart] = []
        warnings: list[str] = []
        child_adapters = tuple(
            item
            for item in DEFAULT_ADAPTERS
            if not isinstance(item, SafeZipAdapter)
        )
        child_registry = ConversionRegistry(child_adapters)
        with _BoundedArchive(raw, budget) as archive:
            for member in archive.names():
                cancellation.raise_if_cancelled()
                if member.endswith("/"):
                    continue
                member_raw = archive.read(member)
                result = child_registry.convert_safe(
                    ConversionSource(name=member, data=member_raw, mime_type=mimetypes.guess_type(member)[0] or ""),
                    budget=ConversionBudget(
                        max_input_bytes=min(budget.max_input_bytes, budget.max_expanded_bytes),
                        max_output_chars=max(1, budget.max_output_chars - sum(len(item.text) for item in parts)),
                        timeout_seconds=max(
                            CONVERSION_POLICY.minimum_nested_timeout_seconds,
                            budget.timeout_seconds - (time.monotonic() - started),
                        ),
                        max_expanded_bytes=budget.max_expanded_bytes,
                        max_archive_entries=budget.max_archive_entries,
                    ),
                    cancellation=cancellation,
                )
                if result.status != "ready":
                    code = result.failure.code if result.failure else "CONVERSION_FAILED"
                    warnings.append(f"member:{member}:rejected:{code}")
                    continue
                for child in result.parts:
                    parts.append(
                        ConvertedPart(
                            text=child.text,
                            ordinal=len(parts) + 1,
                            heading=f"{member} :: {child.heading}".rstrip(" :"),
                            page_start=child.page_start,
                            page_end=child.page_end,
                            kind=f"zip/{child.kind}",
                            bbox=child.bbox,
                            audio_start_ms=child.audio_start_ms,
                            audio_end_ms=child.audio_end_ms,
                        )
                    )
        if not parts:
            raise ConversionError("ZIP_NO_SUPPORTED_CONTENT", "ZIP contains no supported readable members", details={"warnings": warnings})
        return self._ready_result(source, raw, parts, budget, cancellation, started, warnings=warnings)


class RejectedVideoAdapter(NativeAdapter):
    name = "video-rejected-v1"
    suffixes = (".mp4", ".mov", ".avi", ".mkv")
    mime_types = ("video/mp4", "video/quicktime", "video/x-msvideo", "video/x-matroska")
    rejects = True

    def health(self) -> AdapterHealth:
        return AdapterHealth(False, self.name, self.version, "VIDEO_UNSUPPORTED_IN_V1", self.suffixes)

    def convert(self, source: ConversionSource, **_: Any) -> ConversionResult:
        raise ConversionError(
            "VIDEO_UNSUPPORTED_IN_V1",
            "Video is outside the v1 material capability boundary; provide audio or a transcript instead",
        )


DEFAULT_ADAPTERS: tuple[ConversionAdapter, ...] = (
    StructuredDataAdapter(),
    CodeAdapter(),
    PlainTextAdapter(),
    MarkdownAdapter(),
    DoclingDocumentAdapter(),
    HtmlAdapter(),
    PdfAdapter(),
    DocxAdapter(),
    PptxAdapter(),
    XlsxAdapter(),
    EpubAdapter(),
    ImageOcrAdapter(),
    AudioTranscriptionAdapter(),
    SafeZipAdapter(),
    RejectedVideoAdapter(),
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
        declared = [adapter for adapter in self._adapters if adapter.can_handle(source)]
        raw: bytes | None = source.data
        if raw is None and source.path is not None:
            try:
                with source.path.open("rb") as handle:
                    raw = handle.read(CONVERSION_POLICY.signature_probe_bytes)
            except OSError:
                raw = None
        signature_matches = [
            adapter
            for adapter in self._adapters
            if adapter.signature_matches(raw) is True
        ]
        # ZIP is a container signature shared by Office and EPUB.  A declared
        # document MIME/suffix may disambiguate it; otherwise only the hardened
        # container adapter is safe to select.
        if raw is not None and raw.startswith(b"PK"):
            matching = declared or [
                adapter
                for adapter in signature_matches
                if adapter.name == "safe-zip-container"
            ]
        elif signature_matches:
            declared_with_matching_signature = [
                adapter for adapter in declared if adapter in signature_matches
            ]
            matching = declared_with_matching_signature or signature_matches
        else:
            matching = declared
        if not matching:
            raise ConversionError(
                "UNSUPPORTED_FORMAT",
                f"No conversion adapter supports '{source.suffix or source.mime_type}'",
                details={"source_name": source.name, "mime_type": source.mime_type},
            )
        reasons: list[dict[str, str]] = []
        for adapter in matching:
            if adapter.name in self._disabled:
                reasons.append({"adapter": adapter.name, "reason": "disabled"})
                continue
            if getattr(adapter, "rejects", False):
                return adapter
            health = adapter.health()
            if health.available:
                return adapter
            reasons.append({"adapter": adapter.name, "reason": health.reason})
        raise ConversionError(
            "ADAPTER_UNAVAILABLE",
            "All registered adapters for this material are unavailable",
            details={"adapters": reasons},
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

    def capabilities(self) -> tuple[MaterialCapability, ...]:
        """Report formats only when their adapter is healthy and registered.

        This is the single source of truth consumed by the material-capability
        API.  It deliberately reports disabled adapters as unavailable rather
        than silently dropping a format from the receipt.
        """
        result: list[MaterialCapability] = []
        for adapter in self._adapters:
            health = adapter.health()
            result.append(
                MaterialCapability(
                    adapter=adapter.name,
                    adapter_version=adapter.version,
                    available=bool(health.available and adapter.name not in self._disabled),
                    suffixes=tuple(getattr(adapter, "suffixes", ()) or health.supported_suffixes),
                    mime_types=tuple(getattr(adapter, "mime_types", ())),
                    signatures=tuple(
                        signature.hex() for signature in (getattr(adapter, "signatures", ()) or ())
                    ),
                    reason=("disabled" if adapter.name in self._disabled else health.reason),
                    rejected=bool(getattr(adapter, "rejects", False)),
                )
            )
        return tuple(result)

    def capability_report(self, source: ConversionSource | None = None) -> dict[str, Any]:
        """Return registry capabilities, optionally matched to one source."""
        capabilities = [asdict(item) for item in self.capabilities()]
        if source is None:
            return {"registry_version": "conversion-registry.v1", "capabilities": capabilities}
        raw: bytes | None = source.data
        if raw is None and source.path is not None:
            try:
                with source.path.open("rb") as handle:
                    raw = handle.read(CONVERSION_POLICY.signature_probe_bytes)
            except OSError:
                raw = None
        mime = source.mime_type.split(";", 1)[0].strip().lower()
        try:
            selected_adapter = self.select(source)
        except ConversionError:
            selected_adapter = None
        matches: list[dict[str, Any]] = []
        for adapter, item in zip(self._adapters, capabilities):
            suffix_match = source.suffix in item["suffixes"]
            mime_match = bool(mime and mime in item["mime_types"])
            signature_match = adapter.signature_matches(raw)
            if suffix_match or mime_match or signature_match is True:
                matches.append(
                    {
                        **item,
                        "suffix_match": suffix_match,
                        "mime_match": mime_match,
                        "signature_match": signature_match,
                        "selected": bool(
                            selected_adapter is adapter
                            and item["available"]
                            and not item["rejected"]
                        ),
                    }
                )
        return {
            "registry_version": "conversion-registry.v1",
            "source": {"name": source.name, "suffix": source.suffix, "mime_type": mime},
            "matches": matches,
            "supported": any(item["selected"] for item in matches),
        }

    # Short alias used by API clients that call this a report rather than a
    # capability receipt.
    report = capability_report
