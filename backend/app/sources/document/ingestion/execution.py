"""Process-isolated Windows compatibility runner for real Docling conversion."""

from __future__ import annotations

import os
import platform
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docling_core.types.doc import DoclingDocument

from ..contracts import DocumentSourcePolicy, sha256_json
from ..source import build_docling_converter
from .contracts import (
    DoclingCompileMode,
    DoclingRuntimeReceipt,
    DoclingRuntimeStatus,
    DocumentPlatformCompatibilityReceipt,
)
from .pool import (
    DOCLING_WORKER_PROTOCOL,
    DoclingWorkerExecutionError,
    DocumentConversionWorker,
    DocumentConversionWorkerPool,
    hash_heron_artifact,
)


@dataclass(frozen=True, slots=True)
class DoclingExecutionEnvironment:
    """Child-process settings without mutating the parent or Windows globally."""

    python_executable: Path = field(default_factory=lambda: Path(sys.executable))
    scratch_root: Path | None = None
    timeout_seconds: float = 900
    startup_timeout_seconds: float = 120
    shutdown_timeout_seconds: float = 10
    worker_count: int = 1

    def __post_init__(self) -> None:
        if not self.python_executable.is_absolute():
            raise ValueError("python_executable must be absolute")
        if self.scratch_root is not None and not self.scratch_root.is_absolute():
            raise ValueError("scratch_root must be absolute when provided")
        if any(
            value <= 0
            for value in (
                self.timeout_seconds,
                self.startup_timeout_seconds,
                self.shutdown_timeout_seconds,
            )
        ):
            raise ValueError("worker timeout values must be positive")
        if self.worker_count <= 0 or self.worker_count > 4:
            raise ValueError("worker_count must be between 1 and 4")

    def resolved_scratch_root(self) -> Path:
        if self.scratch_root is not None:
            root = self.scratch_root
        elif sys.platform == "win32" and Path("E:/Codex/Temp").is_dir():
            root = Path("E:/Codex/Temp")
        else:
            root = Path(tempfile.gettempdir())
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    def child_environment(self, *, backend_root: Path) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "PIP_NO_INDEX": "1",
            }
        )
        current_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            str(backend_root)
            if not current_pythonpath
            else os.pathsep.join((str(backend_root), current_pythonpath))
        )
        return environment

    def to_dict(self) -> dict[str, Any]:
        return {
            "python_executable": str(self.python_executable),
            "scratch_root": str(self.resolved_scratch_root()),
            "timeout_seconds": self.timeout_seconds,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "shutdown_timeout_seconds": self.shutdown_timeout_seconds,
            "worker_count": self.worker_count,
            "utf8_flag": True,
            "environment_overrides": {
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "PIP_NO_INDEX": "1",
            },
        }


@dataclass(frozen=True, slots=True)
class WindowsDoclingCompatibilityPolicy:
    platform_name: str = field(default_factory=lambda: sys.platform)
    isolate_pdf_conversion: bool = True
    isolate_all_document_formats: bool = False
    disable_heron_torch_compile: bool = True
    msvc_path: str | None = field(default_factory=lambda: shutil.which("cl"))

    def requires_isolation(self, sources: Sequence[Path]) -> bool:
        if self.platform_name != "win32" or not sources:
            return False
        if self.isolate_all_document_formats:
            return True
        return self.isolate_pdf_conversion and any(
            path.suffix.casefold() == ".pdf" for path in sources
        )

    @property
    def compile_mode(self) -> DoclingCompileMode:
        if self.disable_heron_torch_compile:
            return DoclingCompileMode.DISABLED_UNCOMPILED_HERON
        return DoclingCompileMode.RUNTIME_DEFAULT

    @property
    def msvc_available(self) -> bool:
        return self.msvc_path is not None

    @property
    def fallback_reason(self) -> str | None:
        if not self.disable_heron_torch_compile:
            return None
        if not self.msvc_available:
            return "WINDOWS_MSVC_COMPILER_UNAVAILABLE"
        return "WINDOWS_PDF_WORKER_COMPILE_SAFETY_POLICY"


@dataclass(frozen=True, slots=True)
class _WorkerStatus:
    value: str


@dataclass(frozen=True, slots=True)
class _WorkerError:
    error_class: str
    error_code: str = "DOCLING_CONVERSION_FAILED"
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class _WorkerConversionResult:
    status: _WorkerStatus
    document: DoclingDocument | None
    errors: tuple[_WorkerError, ...]


ConverterFactory = Callable[[DocumentSourcePolicy], Any]


class DoclingConversionRunner:
    """Use a UTF-8, offline subprocess for Windows batches containing PDF."""

    def __init__(
        self,
        policy: DocumentSourcePolicy,
        *,
        execution_environment: DoclingExecutionEnvironment | None = None,
        compatibility_policy: WindowsDoclingCompatibilityPolicy | None = None,
        converter_factory: ConverterFactory = build_docling_converter,
    ) -> None:
        self.policy = policy
        self.execution_environment = execution_environment or DoclingExecutionEnvironment()
        self.compatibility_policy = (
            compatibility_policy or WindowsDoclingCompatibilityPolicy()
        )
        self._converter_factory = converter_factory
        self._converter: Any | None = None
        self._pool: DocumentConversionWorkerPool | None = None
        self.runtime_receipts: list[DoclingRuntimeReceipt] = []
        self.platform_receipts: list[DocumentPlatformCompatibilityReceipt] = []

    def convert_all(self, source: Sequence[Path], **kwargs: Any) -> Iterator[Any]:
        sources = tuple(Path(path).resolve(strict=False) for path in source)
        input_root = self.policy.input_root.resolve(strict=False)
        for path in sources:
            try:
                path.relative_to(input_root)
            except ValueError:
                raise ValueError("Docling runner source must remain inside input_root")
        if self.compatibility_policy.requires_isolation(sources):
            yield from self._convert_in_pool(sources, kwargs)
            return
        if self._converter is None:
            self._converter = self._converter_factory(self.policy)
        yield from self._converter.convert_all(sources, **kwargs)

    def close(self) -> None:
        if self._pool is not None:
            self.platform_receipts = list(self._pool.close())
            self._pool = None
        converter = self._converter
        self._converter = None
        close = getattr(converter, "close", None)
        if callable(close):
            close()

    def _convert_in_pool(
        self,
        sources: tuple[Path, ...],
        kwargs: dict[str, Any],
    ) -> Iterator[_WorkerConversionResult]:
        artifact_before = hash_heron_artifact(self.policy)
        if self._pool is None:
            self._pool = DocumentConversionWorkerPool(
                worker_count=self.execution_environment.worker_count,
                worker_factory=self._build_worker,
            )
        try:
            outcomes = self._pool.convert_all(
                sources,
                self._json_conversion_kwargs(kwargs),
            )
        except DoclingWorkerExecutionError as error:
            outcomes = ()
            for source in sources:
                self.runtime_receipts.append(
                    self._runtime_receipt(
                        source=source,
                        response=None,
                        worker_id=error.worker_id,
                        exit_code=None,
                        artifact_before=artifact_before,
                        artifact_after=hash_heron_artifact(self.policy),
                        duration_seconds=0.0,
                        failure_code=error.code,
                        failure_class=error.error_class,
                    )
                )
                yield self._failed_worker_result(error.code, error.error_class)
            return

        artifact_after = hash_heron_artifact(self.policy)
        artifact_changed = artifact_before != artifact_after
        for outcome in outcomes:
            response = outcome.response
            failure_code = outcome.failure_code
            failure_class = outcome.failure_class
            if artifact_changed:
                failure_code = "DOCLING_ARTIFACT_HASH_CHANGED"
                failure_class = "DoclingArtifactIntegrityError"
            converted_result: _WorkerConversionResult | None = None
            if failure_code is None and response is not None:
                try:
                    self._validate_worker_response(response, expected_count=1)
                    converted_result = self._deserialize_worker_result(
                        response["results"][0]
                    )
                except DoclingWorkerExecutionError as error:
                    failure_code = error.code
                    failure_class = error.error_class
            self.runtime_receipts.append(
                self._runtime_receipt(
                    source=outcome.source,
                    response=response,
                    worker_id=outcome.worker_id,
                    exit_code=outcome.worker_exit_code,
                    artifact_before=artifact_before,
                    artifact_after=artifact_after,
                    duration_seconds=outcome.duration_seconds,
                    failure_code=failure_code,
                    failure_class=failure_class,
                )
            )
            if failure_code is not None or response is None:
                yield self._failed_worker_result(
                    failure_code or "DOCLING_WORKER_EXECUTION_FAILED",
                    failure_class or "DoclingWorkerExecutionError",
                )
                continue
            assert converted_result is not None
            yield converted_result

    def _build_worker(self, worker_index: int) -> DocumentConversionWorker:
        return DocumentConversionWorker(
            self.policy,
            execution_environment=self.execution_environment,
            compatibility_policy=self.compatibility_policy,
            worker_index=worker_index,
        )

    @staticmethod
    def _failed_worker_result(
        error_code: str,
        error_class: str,
    ) -> _WorkerConversionResult:
        return _WorkerConversionResult(
            status=_WorkerStatus("failure"),
            document=None,
            errors=(
                _WorkerError(
                    error_class=error_class,
                    error_code=error_code,
                    retryable=True,
                ),
            ),
        )

    def _validate_worker_response(
        self,
        response: dict[str, Any],
        *,
        expected_count: int,
    ) -> None:
        if response.get("protocol") != DOCLING_WORKER_PROTOCOL:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_PROTOCOL_MISMATCH",
                "DoclingWorkerProtocolError",
            )
        if response.get("event") != "CONVERSION":
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_PROTOCOL_MISMATCH",
                "DoclingWorkerProtocolError",
            )
        if int(response.get("network_attempts", -1)) != 0:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_NETWORK_ATTEMPTED",
                "DoclingWorkerNetworkError",
            )
        if int(response.get("model_download_attempts", -1)) != 0:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_MODEL_DOWNLOAD_ATTEMPTED",
                "DoclingWorkerDownloadError",
            )
        if not isinstance(response.get("results"), list):
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_RESULTS_INVALID",
                "DoclingWorkerProtocolError",
            )
        if len(response["results"]) != expected_count:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_RESULT_COUNT_MISMATCH",
                "DoclingWorkerProtocolError",
            )

    @staticmethod
    def _deserialize_worker_result(
        result: Any,
    ) -> _WorkerConversionResult:
        try:
            if not isinstance(result, dict):
                raise TypeError("worker result must be an object")
            document_payload = result.get("document")
            errors_payload = result.get("errors", ())
            if not isinstance(errors_payload, list):
                raise TypeError("worker result errors must be a list")
            return _WorkerConversionResult(
                status=_WorkerStatus(str(result["status"])),
                document=(
                    DoclingDocument.model_validate(document_payload)
                    if document_payload is not None
                    else None
                ),
                errors=tuple(
                    _WorkerError(
                        error_class=str(
                            error.get("error_class") or "DoclingWorkerError"
                        ),
                        error_code=str(
                            error.get("error_code")
                            or "DOCLING_CONVERSION_FAILED"
                        ),
                        retryable=bool(error.get("retryable", False)),
                    )
                    for error in errors_payload
                    if isinstance(error, dict)
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_DOCUMENT_INVALID",
                type(error).__name__,
            ) from error

    def _runtime_receipt(
        self,
        *,
        source: Path,
        response: dict[str, Any] | None,
        worker_id: str | None,
        exit_code: int | None,
        artifact_before: str | None,
        artifact_after: str | None,
        duration_seconds: float,
        failure_code: str | None,
        failure_class: str | None,
    ) -> DoclingRuntimeReceipt:
        sources = (self._source_label(source),)
        platform_receipt = next(
            (
                item
                for item in (
                    self._pool.platform_receipts if self._pool is not None else ()
                )
                if item.worker_id == worker_id
            ),
            None,
        )
        payload = {
            "platform": platform.platform(),
            "sources": list(sources),
            "compile_mode": self.compatibility_policy.compile_mode.value,
            "artifact_before": artifact_before,
            "artifact_after": artifact_after,
            "exit_code": exit_code,
            "failure_code": failure_code,
        }
        return DoclingRuntimeReceipt(
            receipt_id=f"doclingrun_{sha256_json(payload)[:32]}",
            status=(
                DoclingRuntimeStatus.FAILED
                if failure_code is not None
                else DoclingRuntimeStatus.SUCCESS
            ),
            platform=(
                platform_receipt.platform if platform_receipt else platform.platform()
            ),
            process_isolated=True,
            utf8_mode=bool(platform_receipt and platform_receipt.utf8_mode),
            default_encoding=(
                platform_receipt.default_encoding
                if platform_receipt
                else "unknown"
            ),
            compile_mode=(
                platform_receipt.compile_mode
                if platform_receipt
                else self.compatibility_policy.compile_mode
            ),
            fallback_reason=(
                platform_receipt.fallback_reason
                if platform_receipt
                else self.compatibility_policy.fallback_reason
            ),
            msvc_available=self.compatibility_policy.msvc_available,
            network_allowed=False,
            network_attempts=int(response.get("network_attempts", 0) if response else 0),
            model_download_attempts=int(
                response.get("model_download_attempts", 0) if response else 0
            ),
            artifact_sha256_before=artifact_before,
            artifact_sha256_after=artifact_after,
            source_files=sources,
            worker_exit_code=exit_code,
            error_class=failure_class,
            error_code=failure_code,
            worker_id=worker_id,
            duration_seconds=duration_seconds,
        )

    def __enter__(self) -> "DoclingConversionRunner":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _source_label(self, source: Path) -> str:
        try:
            return source.relative_to(self.policy.input_root.resolve()).as_posix()
        except ValueError:
            return source.name

    @staticmethod
    def _json_conversion_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        allowed = {"raises_on_error", "max_num_pages", "max_file_size", "page_range"}
        unexpected = set(kwargs) - allowed
        if unexpected:
            raise ValueError("unsupported worker conversion options")
        payload: dict[str, Any] = {}
        for key, value in kwargs.items():
            payload[key] = list(value) if isinstance(value, tuple) else value
        return payload


__all__ = [
    "DOCLING_WORKER_PROTOCOL",
    "DoclingConversionRunner",
    "DoclingExecutionEnvironment",
    "DoclingWorkerExecutionError",
    "WindowsDoclingCompatibilityPolicy",
]
