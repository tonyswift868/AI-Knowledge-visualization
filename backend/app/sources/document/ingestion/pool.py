"""Persistent process-isolated Docling workers and a bounded local pool."""

from __future__ import annotations

import hashlib
import json
import platform
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from ..contracts import DocumentSourcePolicy, sha256_json
from .contracts import (
    DoclingCompileMode,
    DocumentPlatformCompatibilityReceipt,
    DocumentWorkerState,
)


DOCLING_WORKER_PROTOCOL = "mentorflow.docling-worker.v2"
DOCLING_WORKER_FRAME_PREFIX = b"MENTORFLOW_DOCLING_JSON "
HERON_REPOSITORY_FOLDER = "docling-project--docling-layout-heron"
HERON_MODEL_FILENAME = "model.safetensors"
MAX_LOCAL_WORKERS = 4


class DoclingWorkerExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        error_class: str,
        *,
        worker_id: str | None = None,
    ) -> None:
        super().__init__("Docling compatibility worker failed")
        self.code = code
        self.error_class = error_class
        self.worker_id = worker_id


@dataclass(frozen=True, slots=True)
class DocumentConversionWorkerOutcome:
    index: int
    source: Path
    worker_id: str | None
    response: dict[str, Any] | None
    duration_seconds: float
    worker_exit_code: int | None
    failure_code: str | None = None
    failure_class: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.failure_code is None and self.response is not None


WorkerCommandFactory = Callable[[Path], Sequence[str]]


class DocumentConversionWorker:
    """One persistent UTF-8 worker with explicit startup and shutdown probes."""

    def __init__(
        self,
        policy: DocumentSourcePolicy,
        *,
        execution_environment: Any,
        compatibility_policy: Any,
        worker_index: int,
        command_factory: WorkerCommandFactory | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if worker_index < 0:
            raise ValueError("worker_index must not be negative")
        self.policy = policy
        self.execution_environment = execution_environment
        self.compatibility_policy = compatibility_policy
        self.worker_index = worker_index
        self.worker_id = f"docworker_{worker_index}_{uuid.uuid4().hex[:12]}"
        self._command_factory = command_factory
        self._clock = clock
        self._state = DocumentWorkerState.FAILED
        self._process: subprocess.Popen[bytes] | None = None
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._stdout_queue: queue.Queue[bytes | None] = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_tail = bytearray()
        self._stderr_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._startup_response: dict[str, Any] | None = None
        self._startup_seconds = 0.0
        self._jobs_started = 0
        self._jobs_completed = 0
        self._jobs_failed = 0
        self._network_attempts = 0
        self._model_download_attempts = 0
        self._clean_shutdown = False
        self._timed_out = False
        self._crashed = False
        self._last_error_code: str | None = None
        self._last_error_class: str | None = None

    @property
    def state(self) -> DocumentWorkerState:
        return self._state

    @property
    def healthy(self) -> bool:
        return (
            self._state is DocumentWorkerState.READY
            and self._process is not None
            and self._process.poll() is None
        )

    @property
    def exit_code(self) -> int | None:
        return self._process.poll() if self._process is not None else None

    def start(self) -> DocumentPlatformCompatibilityReceipt:
        if self.healthy:
            return self.receipt()
        if self._process is not None or self._temporary_directory is not None:
            raise RuntimeError("worker instances cannot be restarted")
        started = self._clock()
        try:
            self._temporary_directory = tempfile.TemporaryDirectory(
                prefix="mentorflow-docling-worker-",
                dir=self.execution_environment.resolved_scratch_root(),
            )
            temporary_root = Path(self._temporary_directory.name)
            config_path = temporary_root / "worker-config.json"
            config_path.write_text(
                json.dumps(self._configuration(), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            backend_root = Path(__file__).resolve().parents[4]
            command = tuple(
                self._command_factory(config_path)
                if self._command_factory is not None
                else self._default_command(config_path)
            )
            self._process = subprocess.Popen(
                command,
                cwd=backend_root,
                env=self.execution_environment.child_environment(
                    backend_root=backend_root
                ),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                ),
            )
            assert self._process.stdout is not None
            assert self._process.stderr is not None
            self._stdout_thread = threading.Thread(
                target=self._pump_stdout,
                args=(self._process.stdout,),
                name=f"{self.worker_id}-stdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._pump_stderr,
                args=(self._process.stderr,),
                name=f"{self.worker_id}-stderr",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()
            response = self._read_frame(
                timeout_seconds=self.execution_environment.startup_timeout_seconds
            )
            self._validate_startup_response(response)
            self._startup_response = response
            self._update_attempt_counts(response)
            self._state = DocumentWorkerState.READY
        except queue.Empty as error:
            self._timed_out = True
            self._state = DocumentWorkerState.TIMED_OUT
            self._record_error("DOCLING_WORKER_STARTUP_TIMEOUT", type(error).__name__)
            self._abort_process()
            raise DoclingWorkerExecutionError(
                self._last_error_code or "DOCLING_WORKER_STARTUP_TIMEOUT",
                self._last_error_class or "TimeoutError",
                worker_id=self.worker_id,
            ) from error
        except Exception as error:
            if self._state is not DocumentWorkerState.TIMED_OUT:
                self._state = DocumentWorkerState.FAILED
            self._record_error(
                getattr(error, "code", "DOCLING_WORKER_STARTUP_FAILED"),
                getattr(error, "error_class", type(error).__name__),
            )
            self._abort_process()
            if isinstance(error, DoclingWorkerExecutionError):
                raise
            raise DoclingWorkerExecutionError(
                self._last_error_code or "DOCLING_WORKER_STARTUP_FAILED",
                self._last_error_class or type(error).__name__,
                worker_id=self.worker_id,
            ) from error
        finally:
            self._startup_seconds = max(0.0, self._clock() - started)
        return self.receipt()

    def convert(
        self,
        sources: Sequence[Path],
        conversion_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.healthy:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_NOT_READY",
                "DoclingWorkerStateError",
                worker_id=self.worker_id,
            )
        job_id = f"docjob_{uuid.uuid4().hex}"
        self._jobs_started += 1
        try:
            self._write_frame(
                {
                    "protocol": DOCLING_WORKER_PROTOCOL,
                    "event": "CONVERT",
                    "job_id": job_id,
                    "sources": [str(path) for path in sources],
                    "conversion_kwargs": conversion_kwargs,
                }
            )
        except (EOFError, OSError) as error:
            self._jobs_failed += 1
            self._crashed = True
            self._state = DocumentWorkerState.CRASHED
            self._record_error("DOCLING_WORKER_CRASHED", type(error).__name__)
            self._abort_process()
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_CRASHED",
                type(error).__name__,
                worker_id=self.worker_id,
            ) from error
        try:
            response = self._read_frame(
                timeout_seconds=self.execution_environment.timeout_seconds
            )
        except queue.Empty as error:
            self._jobs_failed += 1
            self._timed_out = True
            self._state = DocumentWorkerState.TIMED_OUT
            self._record_error("DOCLING_WORKER_TIMEOUT", type(error).__name__)
            self._abort_process()
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_TIMEOUT",
                type(error).__name__,
                worker_id=self.worker_id,
            ) from error
        except EOFError as error:
            self._jobs_failed += 1
            self._crashed = True
            self._state = DocumentWorkerState.CRASHED
            self._record_error("DOCLING_WORKER_CRASHED", type(error).__name__)
            self._abort_process()
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_CRASHED",
                type(error).__name__,
                worker_id=self.worker_id,
            ) from error
        except Exception as error:
            self._jobs_failed += 1
            self._state = DocumentWorkerState.FAILED
            self._record_error(
                "DOCLING_WORKER_PROTOCOL_MISMATCH",
                type(error).__name__,
            )
            self._abort_process()
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_PROTOCOL_MISMATCH",
                type(error).__name__,
                worker_id=self.worker_id,
            ) from error

        try:
            self._update_attempt_counts(response)
            protocol_valid = (
                response.get("protocol") == DOCLING_WORKER_PROTOCOL
                and response.get("event") == "CONVERSION"
                and response.get("job_id") == job_id
            )
        except (TypeError, ValueError) as error:
            protocol_valid = False
            protocol_error_class = type(error).__name__
        else:
            protocol_error_class = "DoclingWorkerProtocolError"
        if not protocol_valid:
            self._jobs_failed += 1
            self._state = DocumentWorkerState.FAILED
            self._record_error(
                "DOCLING_WORKER_PROTOCOL_MISMATCH",
                protocol_error_class,
            )
            self._abort_process()
            raise DoclingWorkerExecutionError(
                self._last_error_code,
                self._last_error_class or protocol_error_class,
                worker_id=self.worker_id,
            )
        if response.get("status") != "SUCCESS":
            self._jobs_failed += 1
            code = str(
                response.get("error_code") or "DOCLING_WORKER_EXECUTION_FAILED"
            )
            error_class = str(
                response.get("error_class") or "DoclingWorkerExecutionError"
            )
            self._record_error(code, error_class)
            raise DoclingWorkerExecutionError(
                code,
                error_class,
                worker_id=self.worker_id,
            )
        self._jobs_completed += 1
        return response

    def close(self) -> DocumentPlatformCompatibilityReceipt:
        process = self._process
        if process is None:
            self._cleanup_temporary_directory()
            return self.receipt()
        try:
            if self.healthy:
                self._write_frame(
                    {
                        "protocol": DOCLING_WORKER_PROTOCOL,
                        "event": "SHUTDOWN",
                    }
                )
                response = self._read_frame(
                    timeout_seconds=self.execution_environment.shutdown_timeout_seconds
                )
                if (
                    response.get("protocol") != DOCLING_WORKER_PROTOCOL
                    or response.get("event") != "SHUTDOWN_ACK"
                    or response.get("status") != "SUCCESS"
                ):
                    raise ValueError("invalid worker shutdown acknowledgement")
                self._update_attempt_counts(response)
                process.wait(
                    timeout=self.execution_environment.shutdown_timeout_seconds
                )
                self._clean_shutdown = process.returncode == 0
                if self._clean_shutdown:
                    self._state = DocumentWorkerState.STOPPED
                else:
                    self._state = DocumentWorkerState.FAILED
                    self._record_error(
                        "DOCLING_WORKER_SHUTDOWN_FAILED",
                        "DoclingWorkerShutdownError",
                    )
            else:
                self._terminate_process()
        except subprocess.TimeoutExpired as error:
            self._timed_out = True
            self._state = DocumentWorkerState.TIMED_OUT
            self._record_error(
                "DOCLING_WORKER_SHUTDOWN_TIMEOUT",
                type(error).__name__,
            )
            self._terminate_process()
        except Exception as error:
            self._state = DocumentWorkerState.FAILED
            self._record_error(
                "DOCLING_WORKER_SHUTDOWN_FAILED",
                type(error).__name__,
            )
            self._terminate_process()
        finally:
            try:
                self._terminate_process()
            finally:
                try:
                    self._finalize_process_resources()
                finally:
                    self._cleanup_temporary_directory()
        return self.receipt()

    def receipt(self) -> DocumentPlatformCompatibilityReceipt:
        response = self._startup_response or {}
        artifact_hash = response.get("artifact_sha256")
        compile_mode = DoclingCompileMode(
            response.get("compile_mode", self.compatibility_policy.compile_mode.value)
        )
        payload = {
            "worker_id": self.worker_id,
            "state": self._state.value,
            "platform": response.get("platform") or platform.platform(),
            "artifact_sha256": artifact_hash,
            "jobs_started": self._jobs_started,
            "jobs_completed": self._jobs_completed,
            "jobs_failed": self._jobs_failed,
            "error_code": self._last_error_code,
        }
        return DocumentPlatformCompatibilityReceipt(
            receipt_id=f"docworkerreceipt_{sha256_json(payload)[:32]}",
            worker_id=self.worker_id,
            state=self._state,
            platform=str(response.get("platform") or platform.platform()),
            process_isolated=True,
            startup_succeeded=self._startup_response is not None,
            utf8_mode=bool(response.get("utf8_mode")),
            default_encoding=str(response.get("default_encoding") or "unknown"),
            compile_mode=compile_mode,
            fallback_reason=(
                self.compatibility_policy.fallback_reason
                if compile_mode is not DoclingCompileMode.NOT_APPLICABLE
                else None
            ),
            msvc_available=self.compatibility_policy.msvc_available,
            artifact_available=bool(response.get("artifact_available")),
            artifact_sha256=str(artifact_hash) if artifact_hash else None,
            network_allowed=False,
            network_attempts=self._network_attempts,
            model_download_attempts=self._model_download_attempts,
            jobs_started=self._jobs_started,
            jobs_completed=self._jobs_completed,
            jobs_failed=self._jobs_failed,
            startup_seconds=self._startup_seconds,
            clean_shutdown=self._clean_shutdown,
            timed_out=self._timed_out,
            crashed=self._crashed,
            worker_exit_code=(
                self._process.poll() if self._process is not None else None
            ),
            error_class=self._last_error_class,
            error_code=self._last_error_code,
        )

    def _configuration(self) -> dict[str, Any]:
        return {
            "protocol": DOCLING_WORKER_PROTOCOL,
            "worker_id": self.worker_id,
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
            "disable_heron_torch_compile": (
                self.compatibility_policy.disable_heron_torch_compile
            ),
        }

    def _default_command(self, config_path: Path) -> tuple[str, ...]:
        return (
            str(self.execution_environment.python_executable),
            "-X",
            "utf8",
            "-m",
            "app.sources.document.ingestion.worker",
            "--serve",
            "--config",
            str(config_path),
        )

    def _validate_startup_response(self, response: dict[str, Any]) -> None:
        if (
            response.get("protocol") != DOCLING_WORKER_PROTOCOL
            or response.get("event") != "STARTUP"
            or response.get("status") != "READY"
            or response.get("worker_id") != self.worker_id
        ):
            raise DoclingWorkerExecutionError(
                str(response.get("error_code") or "DOCLING_WORKER_STARTUP_FAILED"),
                str(
                    response.get("error_class")
                    or "DoclingWorkerStartupProbeError"
                ),
                worker_id=self.worker_id,
            )
        if response.get("utf8_mode") is not True:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_UTF8_REQUIRED",
                "DoclingWorkerEncodingError",
                worker_id=self.worker_id,
            )
        if "utf" not in str(response.get("default_encoding", "")).casefold():
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_UTF8_REQUIRED",
                "DoclingWorkerEncodingError",
                worker_id=self.worker_id,
            )
        actual_compile = response.get("compile_mode")
        expected_artifact = hash_heron_artifact(self.policy)
        if self.policy.pdf_artifacts_path is not None and expected_artifact is None:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_ARTIFACT_UNAVAILABLE",
                "DoclingWorkerArtifactError",
                worker_id=self.worker_id,
            )
        accepted_compile_modes = {self.compatibility_policy.compile_mode.value}
        if expected_artifact is None:
            accepted_compile_modes.add(DoclingCompileMode.NOT_APPLICABLE.value)
        if actual_compile not in accepted_compile_modes:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_COMPILE_MODE_MISMATCH",
                "DoclingWorkerCompileModeError",
                worker_id=self.worker_id,
            )
        if expected_artifact is not None and (
            response.get("artifact_available") is not True
            or response.get("artifact_sha256") != expected_artifact
        ):
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_ARTIFACT_MISMATCH",
                "DoclingWorkerArtifactError",
                worker_id=self.worker_id,
            )
        if int(response.get("network_attempts", -1)) != 0:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_NETWORK_ATTEMPTED",
                "DoclingWorkerNetworkError",
                worker_id=self.worker_id,
            )
        if int(response.get("model_download_attempts", -1)) != 0:
            raise DoclingWorkerExecutionError(
                "DOCLING_WORKER_MODEL_DOWNLOAD_ATTEMPTED",
                "DoclingWorkerDownloadError",
                worker_id=self.worker_id,
            )

    def _write_frame(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise EOFError("worker process is not writable")
        data = DOCLING_WORKER_FRAME_PREFIX + json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        with self._write_lock:
            process.stdin.write(data)
            process.stdin.flush()

    def _read_frame(self, *, timeout_seconds: float) -> dict[str, Any]:
        deadline = self._clock() + timeout_seconds
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise queue.Empty
            line = self._stdout_queue.get(timeout=remaining)
            if line is None:
                raise EOFError("worker stdout closed")
            if not line.startswith(DOCLING_WORKER_FRAME_PREFIX):
                continue
            payload = line[len(DOCLING_WORKER_FRAME_PREFIX) :]
            return json.loads(payload.decode("utf-8"))

    def _pump_stdout(self, stream: BinaryIO) -> None:
        try:
            for line in iter(stream.readline, b""):
                self._stdout_queue.put(line)
        finally:
            self._stdout_queue.put(None)

    def _pump_stderr(self, stream: BinaryIO) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            with self._stderr_lock:
                self._stderr_tail.extend(chunk)
                if len(self._stderr_tail) > 65536:
                    del self._stderr_tail[:-65536]

    def _update_attempt_counts(self, response: dict[str, Any]) -> None:
        self._network_attempts = max(
            self._network_attempts,
            int(response.get("network_attempts", 0)),
        )
        self._model_download_attempts = max(
            self._model_download_attempts,
            int(response.get("model_download_attempts", 0)),
        )

    def _record_error(self, code: str, error_class: str) -> None:
        self._last_error_code = str(code)
        self._last_error_class = str(error_class)

    def _terminate_process(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def _abort_process(self) -> None:
        try:
            self._terminate_process()
        finally:
            try:
                self._finalize_process_resources()
            finally:
                self._cleanup_temporary_directory()

    def _finalize_process_resources(self) -> None:
        process = self._process
        if process is None:
            return
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        self._join_pumps()
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass
        self._join_pumps()

    def _join_pumps(self) -> None:
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=1)

    def _cleanup_temporary_directory(self) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None

    def __enter__(self) -> "DocumentConversionWorker":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


WorkerFactory = Callable[[int], DocumentConversionWorker]


class DocumentConversionWorkerPool:
    """Bounded worker pool; each lane owns one persistent local process."""

    def __init__(
        self,
        *,
        worker_count: int,
        worker_factory: WorkerFactory,
    ) -> None:
        if worker_count <= 0 or worker_count > MAX_LOCAL_WORKERS:
            raise ValueError(f"worker_count must be between 1 and {MAX_LOCAL_WORKERS}")
        self.worker_count = worker_count
        self._worker_factory = worker_factory
        self._workers: list[DocumentConversionWorker] = []
        self._active_workers: list[DocumentConversionWorker] = []
        self._next_worker_index = 0
        self._lock = threading.Lock()
        self._started = False
        self._closed = False

    @property
    def platform_receipts(self) -> tuple[DocumentPlatformCompatibilityReceipt, ...]:
        return tuple(worker.receipt() for worker in self._workers)

    def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("worker pool is already closed")
        failures: list[DoclingWorkerExecutionError] = []
        for _ in range(self.worker_count):
            worker = self._new_worker()
            try:
                worker.start()
            except DoclingWorkerExecutionError as error:
                failures.append(error)
                worker.close()
                continue
            self._active_workers.append(worker)
        self._started = True
        if not self._active_workers:
            first = failures[0] if failures else None
            raise DoclingWorkerExecutionError(
                first.code if first else "DOCLING_WORKER_POOL_STARTUP_FAILED",
                first.error_class if first else "DoclingWorkerPoolError",
                worker_id=first.worker_id if first else None,
            )

    def convert_all(
        self,
        sources: Sequence[Path],
        conversion_kwargs: dict[str, Any],
    ) -> tuple[DocumentConversionWorkerOutcome, ...]:
        self.start()
        indexed_sources = tuple(enumerate(sources))
        if not indexed_sources:
            return ()
        lanes: list[list[tuple[int, Path]]] = [
            [] for _ in range(len(self._active_workers))
        ]
        for position, item in enumerate(indexed_sources):
            lanes[position % len(lanes)].append(item)
        with ThreadPoolExecutor(
            max_workers=len(lanes),
            thread_name_prefix="mentorflow-docling-pool",
        ) as executor:
            futures = [
                executor.submit(self._run_lane, worker, lane, conversion_kwargs)
                for worker, lane in zip(self._active_workers, lanes, strict=True)
                if lane
            ]
            outcomes = [outcome for future in futures for outcome in future.result()]
        return tuple(sorted(outcomes, key=lambda item: item.index))

    def close(self) -> tuple[DocumentPlatformCompatibilityReceipt, ...]:
        if self._closed:
            return self.platform_receipts
        for worker in tuple(self._active_workers):
            worker.close()
        self._active_workers.clear()
        self._closed = True
        return self.platform_receipts

    def _run_lane(
        self,
        worker: DocumentConversionWorker,
        lane: list[tuple[int, Path]],
        conversion_kwargs: dict[str, Any],
    ) -> list[DocumentConversionWorkerOutcome]:
        outcomes: list[DocumentConversionWorkerOutcome] = []
        current = worker
        for offset, (index, source) in enumerate(lane):
            started = time.perf_counter()
            response: dict[str, Any] | None = None
            failure_code: str | None = None
            failure_class: str | None = None
            worker_id: str | None = current.worker_id
            replace_required = False
            try:
                response = current.convert((source,), conversion_kwargs)
            except DoclingWorkerExecutionError as error:
                failure_code = error.code
                failure_class = error.error_class
                worker_id = error.worker_id or current.worker_id
            except Exception as error:
                failure_code = "DOCLING_WORKER_UNEXPECTED_FAILURE"
                failure_class = type(error).__name__
                replace_required = True
            outcomes.append(
                DocumentConversionWorkerOutcome(
                    index=index,
                    source=source,
                    worker_id=worker_id,
                    response=response,
                    duration_seconds=max(0.0, time.perf_counter() - started),
                    worker_exit_code=(
                        current.exit_code
                    ),
                    failure_code=failure_code,
                    failure_class=failure_class,
                )
            )
            if (
                failure_code is not None
                and (not current.healthy or replace_required)
                and offset + 1 < len(lane)
            ):
                failed_worker = current
                current.close()
                replacement = self._new_worker()
                try:
                    replacement.start()
                except DoclingWorkerExecutionError:
                    replacement.close()
                    current = replacement
                    self._replace_active_worker(failed_worker, replacement)
                    continue
                current = replacement
                self._replace_active_worker(failed_worker, replacement)
        return outcomes

    def _replace_active_worker(
        self,
        failed: DocumentConversionWorker,
        replacement: DocumentConversionWorker,
    ) -> None:
        with self._lock:
            try:
                index = self._active_workers.index(failed)
            except ValueError:
                self._active_workers.append(replacement)
            else:
                self._active_workers[index] = replacement

    def _new_worker(self) -> DocumentConversionWorker:
        with self._lock:
            worker_index = self._next_worker_index
            self._next_worker_index += 1
        worker = self._worker_factory(worker_index)
        with self._lock:
            self._workers.append(worker)
        return worker

    def __enter__(self) -> "DocumentConversionWorkerPool":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def hash_heron_artifact(policy: DocumentSourcePolicy) -> str | None:
    artifacts = policy.pdf_artifacts_path
    if artifacts is None:
        return None
    path = artifacts / HERON_REPOSITORY_FOLDER / HERON_MODEL_FILENAME
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "DOCLING_WORKER_FRAME_PREFIX",
    "DOCLING_WORKER_PROTOCOL",
    "MAX_LOCAL_WORKERS",
    "DoclingWorkerExecutionError",
    "DocumentConversionWorker",
    "DocumentConversionWorkerOutcome",
    "DocumentConversionWorkerPool",
    "hash_heron_artifact",
]
