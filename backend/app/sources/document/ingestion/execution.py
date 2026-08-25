"""Process-isolated Windows compatibility runner for real Docling conversion."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
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
)


DOCLING_WORKER_PROTOCOL = "mentorflow.docling-worker.v1"
HERON_REPOSITORY_FOLDER = "docling-project--docling-layout-heron"
HERON_MODEL_FILENAME = "model.safetensors"


class DoclingWorkerExecutionError(RuntimeError):
    def __init__(self, code: str, error_class: str):
        super().__init__("Docling compatibility worker failed")
        self.code = code
        self.error_class = error_class


@dataclass(frozen=True, slots=True)
class DoclingExecutionEnvironment:
    """Child-process settings without mutating the parent or Windows globally."""

    python_executable: Path = field(default_factory=lambda: Path(sys.executable))
    scratch_root: Path | None = None
    timeout_seconds: int = 900

    def __post_init__(self) -> None:
        if not self.python_executable.is_absolute():
            raise ValueError("python_executable must be absolute")
        if self.scratch_root is not None and not self.scratch_root.is_absolute():
            raise ValueError("scratch_root must be absolute when provided")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

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
    disable_heron_torch_compile: bool = True
    msvc_path: str | None = field(default_factory=lambda: shutil.which("cl"))

    def requires_isolation(self, sources: Sequence[Path]) -> bool:
        return (
            self.platform_name == "win32"
            and self.isolate_pdf_conversion
            and any(path.suffix.casefold() == ".pdf" for path in sources)
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
        self.runtime_receipts: list[DoclingRuntimeReceipt] = []

    def convert_all(self, source: Sequence[Path], **kwargs: Any) -> Iterator[Any]:
        sources = tuple(Path(path).resolve(strict=False) for path in source)
        input_root = self.policy.input_root.resolve(strict=False)
        for path in sources:
            try:
                path.relative_to(input_root)
            except ValueError:
                raise ValueError("Docling runner source must remain inside input_root")
        if self.compatibility_policy.requires_isolation(sources):
            yield from self._convert_in_worker(sources, kwargs)
            return
        if self._converter is None:
            self._converter = self._converter_factory(self.policy)
        yield from self._converter.convert_all(sources, **kwargs)

    def _convert_in_worker(
        self,
        sources: tuple[Path, ...],
        kwargs: dict[str, Any],
    ) -> Iterator[_WorkerConversionResult]:
        artifact_path = self._heron_model_path()
        artifact_before = self._hash_file(artifact_path)
        source_files = tuple(self._source_label(path) for path in sources)
        backend_root = Path(__file__).resolve().parents[4]
        response: dict[str, Any] | None = None
        exit_code: int | None = None
        failure_code: str | None = None
        failure_class: str | None = None

        try:
            with tempfile.TemporaryDirectory(
                prefix="mentorflow-docling-worker-",
                dir=self.execution_environment.resolved_scratch_root(),
            ) as temporary_directory:
                temporary_root = Path(temporary_directory)
                request_path = temporary_root / "request.json"
                response_path = temporary_root / "response.json"
                request_payload = {
                    "protocol": DOCLING_WORKER_PROTOCOL,
                    "sources": [str(path) for path in sources],
                    "policy": {
                        "input_root": str(self.policy.input_root),
                        "pdf_artifacts_path": (
                            str(self.policy.pdf_artifacts_path)
                            if self.policy.pdf_artifacts_path is not None
                            else None
                        ),
                        "pdf_table_structure_enabled": (
                            self.policy.pdf_table_structure_enabled
                        ),
                    },
                    "conversion_kwargs": self._json_conversion_kwargs(kwargs),
                    "disable_heron_torch_compile": (
                        self.compatibility_policy.disable_heron_torch_compile
                    ),
                }
                request_path.write_text(
                    json.dumps(request_payload, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
                command = (
                    str(self.execution_environment.python_executable),
                    "-X",
                    "utf8",
                    "-m",
                    "app.sources.document.ingestion.worker",
                    "--request",
                    str(request_path),
                    "--response",
                    str(response_path),
                )
                completed = subprocess.run(
                    command,
                    cwd=backend_root,
                    env=self.execution_environment.child_environment(
                        backend_root=backend_root
                    ),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.execution_environment.timeout_seconds,
                    check=False,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW
                        if sys.platform == "win32"
                        else 0
                    ),
                )
                exit_code = completed.returncode
                if response_path.is_file():
                    response = json.loads(response_path.read_text(encoding="utf-8"))
                if exit_code != 0 or response is None:
                    failure_code = "DOCLING_WORKER_PROCESS_FAILED"
                    failure_class = "DoclingWorkerProcessError"
                elif response.get("status") != "SUCCESS":
                    failure_code = str(
                        response.get("error_code") or "DOCLING_WORKER_EXECUTION_FAILED"
                    )
                    failure_class = str(
                        response.get("error_class") or "DoclingWorkerExecutionError"
                    )
                else:
                    self._validate_worker_response(response, expected_count=len(sources))
        except subprocess.TimeoutExpired:
            failure_code = "DOCLING_WORKER_TIMEOUT"
            failure_class = "TimeoutExpired"
        except Exception as error:
            failure_code = getattr(error, "code", "DOCLING_WORKER_PROTOCOL_FAILED")
            failure_class = type(error).__name__

        artifact_after = self._hash_file(artifact_path)
        if artifact_before != artifact_after:
            failure_code = "DOCLING_ARTIFACT_HASH_CHANGED"
            failure_class = "DoclingArtifactIntegrityError"

        runtime_receipt = self._runtime_receipt(
            sources=source_files,
            response=response,
            exit_code=exit_code,
            artifact_before=artifact_before,
            artifact_after=artifact_after,
            failure_code=failure_code,
            failure_class=failure_class,
        )
        self.runtime_receipts.append(runtime_receipt)
        if failure_code is not None:
            raise DoclingWorkerExecutionError(
                code=failure_code,
                error_class=failure_class or "DoclingWorkerExecutionError",
            )
        assert response is not None
        for result in response["results"]:
            document_payload = result.get("document")
            yield _WorkerConversionResult(
                status=_WorkerStatus(str(result["status"])),
                document=(
                    DoclingDocument.model_validate(document_payload)
                    if document_payload is not None
                    else None
                ),
                errors=tuple(
                    _WorkerError(str(error.get("error_class") or "DoclingWorkerError"))
                    for error in result.get("errors", ())
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
        if response.get("utf8_mode") is not True:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_UTF8_REQUIRED",
                "DoclingWorkerEncodingError",
            )
        if "utf" not in str(response.get("default_encoding", "")).casefold():
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_UTF8_REQUIRED",
                "DoclingWorkerEncodingError",
            )
        expected_compile = self.compatibility_policy.compile_mode.value
        if response.get("compile_mode") != expected_compile:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_COMPILE_MODE_MISMATCH",
                "DoclingWorkerCompileModeError",
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

    def _runtime_receipt(
        self,
        *,
        sources: tuple[str, ...],
        response: dict[str, Any] | None,
        exit_code: int | None,
        artifact_before: str | None,
        artifact_after: str | None,
        failure_code: str | None,
        failure_class: str | None,
    ) -> DoclingRuntimeReceipt:
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
            platform=platform.platform(),
            process_isolated=True,
            utf8_mode=bool(response and response.get("utf8_mode")),
            default_encoding=str(
                response.get("default_encoding", "unknown")
                if response
                else "unknown"
            ),
            compile_mode=self.compatibility_policy.compile_mode,
            fallback_reason=self.compatibility_policy.fallback_reason,
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
        )

    def _heron_model_path(self) -> Path | None:
        artifacts = self.policy.pdf_artifacts_path
        if artifacts is None:
            return None
        path = artifacts / HERON_REPOSITORY_FOLDER / HERON_MODEL_FILENAME
        return path if path.is_file() else None

    @staticmethod
    def _hash_file(path: Path | None) -> str | None:
        if path is None:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

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
