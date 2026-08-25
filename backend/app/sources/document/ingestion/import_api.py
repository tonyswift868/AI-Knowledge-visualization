"""Independent product API for document selection, progress, and review."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..contracts import (
    DocumentFormat,
    DocumentPreflightResult,
    DocumentPreflightStatus,
    DocumentProcessingShape,
    DocumentSourceMetadata,
    canonical_json_bytes,
    sha256_json,
)
from .application import DocumentIngestionApplication
from .checkpoint import DocumentCheckpointStore
from .contracts import (
    DocumentBatchRequest,
    DocumentBatchStatus,
    DocumentIngestionProgress,
    DocumentIngestionStatus,
)
from .exercises import DocumentExerciseCandidateStatus
from .runtime import (
    DocumentBatchSubmissionStatus,
    DocumentKnowledgeBatchPipelinePort,
    DocumentLearningCorpusResult,
    DocumentLearningCorpusRuntime,
)


DOCUMENT_IMPORT_API_SCHEMA = "mentorflow.document-import-api.v1"
DOCUMENT_IMPORT_MANIFEST_SCHEMA = "mentorflow.document-import-manifest.v1"


class DocumentUserPreflightStatus(str, Enum):
    PROCESSABLE = "PROCESSABLE"
    LIMITED = "LIMITED"
    UNPROCESSABLE = "UNPROCESSABLE"


class DocumentImportBatchState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"

    @property
    def terminal(self) -> bool:
        return self in {
            DocumentImportBatchState.CANCELLED,
            DocumentImportBatchState.COMPLETED,
            DocumentImportBatchState.PARTIAL,
            DocumentImportBatchState.FAILED,
        }


class DocumentKnowledgeHandoffStatus(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class DocumentPreviewLocationKind(str, Enum):
    PAGE = "PAGE"
    SLIDE = "SLIDE"
    SECTION = "SECTION"
    UNKNOWN = "UNKNOWN"


_FORMAT_BY_SUFFIX = {
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".pptx": DocumentFormat.PPTX,
    ".html": DocumentFormat.HTML,
    ".htm": DocumentFormat.HTML,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
}

_FORMAT_LABELS = {
    DocumentFormat.PDF: "PDF",
    DocumentFormat.DOCX: "DOCX",
    DocumentFormat.PPTX: "PPTX",
    DocumentFormat.HTML: "HTML",
    DocumentFormat.MARKDOWN: "Markdown",
}

_PREFLIGHT_STATUS_LABELS = {
    DocumentUserPreflightStatus.PROCESSABLE: "可处理",
    DocumentUserPreflightStatus.LIMITED: "有限支持",
    DocumentUserPreflightStatus.UNPROCESSABLE: "不能处理",
}

_LIMITATION_MESSAGES = {
    "FILE_SIZE_WARNING": "文件较大，处理时间可能更长",
    "UNIT_COUNT_WARNING": "内容单元较多，处理时间可能更长",
    "PDF_OCR_DISABLED": "本次不执行 OCR",
    "PDF_TABLE_MODEL_DISABLED": "本次不启用 PDF 表格模型",
    "PDF_LOCAL_ARTIFACTS_UNAVAILABLE": (
        "PDF 需要已配置的本地解析模型；其它格式不受影响"
    ),
    "DOCLING_PARTIAL_SUCCESS": "解析器仅返回部分内容",
    "PRECISE_PAGE_UNAVAILABLE": "该格式只提供章节位置，不推断页码",
    "PRECISE_SLIDE_UNAVAILABLE": "未获得可验证的幻灯片编号",
}

_PREFLIGHT_FAILURE_MESSAGES = {
    "DOCUMENT_PATH_OUTSIDE_INPUT_ROOT": "文件不在允许的导入目录中",
    "DOCUMENT_PATH_INVALID": "文件路径无效",
    "DOCUMENT_FILE_NOT_FOUND": "找不到文件或文件不可读",
    "DOCUMENT_FILE_UNREADABLE": "文件不可读",
    "DOCUMENT_FORMAT_UNSUPPORTED": (
        "仅支持 PDF、DOCX、PPTX、HTML 和 Markdown"
    ),
    "DOCUMENT_EMPTY": "文件为空",
    "DOCUMENT_FILE_SIZE_LIMIT": "文件超过本次导入大小上限",
    "DOCUMENT_UNIT_COUNT_LIMIT": "文件超过本次导入内容规模上限",
    "DOCUMENT_BYTES_INVALID": "文件扩展名与实际内容不匹配",
    "DOCLING_UNAVAILABLE": "本机文档解析组件当前不可用",
}

_RESULT_FAILURE_MESSAGES = {
    **_PREFLIGHT_FAILURE_MESSAGES,
    "DOCUMENT_BATCH_SIZE_LIMIT": "选择的文件数量超过本次导入上限",
    "DOCLING_CONVERTER_INITIALIZATION_FAILED": "本机解析器启动失败，可稍后重试",
    "DOCLING_BATCH_STREAM_FAILED": "该文件的解析流意外结束，可单独重试",
    "DOCLING_CONVERSION_FAILED": "该文件未能完成解析",
    "DOCLING_WORKER_CRASHED": "该文件的本地解析进程异常退出，可单独重试",
    "DOCLING_WORKER_TIMEOUT": "该文件处理超时，可单独重试",
    "DOCLING_DOCUMENT_MISSING": "解析器未返回可预览的文档内容",
    "DOCUMENT_EVIDENCE_MAPPING_FAILED": "文档内容无法转换为来源证据",
    "DOCUMENT_PIPELINE_HANDOFF_FAILED": "文档已解析，但知识交接失败",
    "DOCUMENT_INGESTION_INTERRUPTED": "导入已取消，此文件尚未完成",
}


def _limitation_message(code: str) -> str:
    return _LIMITATION_MESSAGES.get(code, "存在需要用户确认的处理限制")


def _failure_message(code: str | None, *, preflight: bool = False) -> str:
    messages = _PREFLIGHT_FAILURE_MESSAGES if preflight else _RESULT_FAILURE_MESSAGES
    return messages.get(code or "", "此文件当前不能处理，请检查文件后重试")


@dataclass(frozen=True, slots=True)
class DocumentImportCandidateDTO:
    file_id: str
    source_file: str
    display_name: str
    format: DocumentFormat | None
    format_label: str
    size_bytes: int | None
    processing_shape: DocumentProcessingShape
    unit_kind: str | None
    unit_count: int | None
    status: DocumentUserPreflightStatus
    status_label: str
    selectable: bool
    conversion_ready: bool
    limitation_codes: tuple[str, ...]
    limitation_messages: tuple[str, ...]
    reason_code: str | None = None
    reason_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "source_file": self.source_file,
            "display_name": self.display_name,
            "format": self.format.value if self.format else None,
            "format_label": self.format_label,
            "size_bytes": self.size_bytes,
            "processing_shape": self.processing_shape.value,
            "unit_kind": self.unit_kind,
            "unit_count": self.unit_count,
            "status": self.status.value,
            "status_label": self.status_label,
            "selectable": self.selectable,
            "conversion_ready": self.conversion_ready,
            "limitation_codes": list(self.limitation_codes),
            "limitation_messages": list(self.limitation_messages),
            "reason_code": self.reason_code,
            "reason_message": self.reason_message,
        }


@dataclass(frozen=True, slots=True)
class DocumentImportSelectionDTO:
    schema: str
    selection_id: str
    preflight_complete: bool
    candidates: tuple[DocumentImportCandidateDTO, ...]
    selected_file_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "selection_id": self.selection_id,
            "preflight_complete": self.preflight_complete,
            "candidates": [item.to_dict() for item in self.candidates],
            "selected_file_ids": list(self.selected_file_ids),
        }


@dataclass(frozen=True, slots=True)
class DocumentImportProgressDTO:
    batch_id: str
    state: DocumentImportBatchState
    total_files: int
    completed_files: int
    processing_file: str | None
    success_files: int
    limited_files: int
    failed_files: int
    cancelled_files: int
    skipped_duplicate_files: int
    evidence_count: int
    knowledge_handoff_status: DocumentKnowledgeHandoffStatus
    elapsed_seconds: float
    attempt: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "state": self.state.value,
            "total_files": self.total_files,
            "completed_files": self.completed_files,
            "processing_file": self.processing_file,
            "success_files": self.success_files,
            "limited_files": self.limited_files,
            "failed_files": self.failed_files,
            "cancelled_files": self.cancelled_files,
            "skipped_duplicate_files": self.skipped_duplicate_files,
            "evidence_count": self.evidence_count,
            "knowledge_handoff_status": self.knowledge_handoff_status.value,
            "elapsed_seconds": self.elapsed_seconds,
            "attempt": self.attempt,
        }


@dataclass(frozen=True, slots=True)
class DocumentImportFileResultDTO:
    source_file: str
    format: DocumentFormat | None
    status: str
    resumed: bool
    evidence_count: int
    limitation_messages: tuple[str, ...]
    failure_code: str | None
    failure_reason: str | None
    duplicate_of: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "format": self.format.value if self.format else None,
            "status": self.status,
            "resumed": self.resumed,
            "evidence_count": self.evidence_count,
            "limitation_messages": list(self.limitation_messages),
            "failure_code": self.failure_code,
            "failure_reason": self.failure_reason,
            "duplicate_of": self.duplicate_of,
        }


@dataclass(frozen=True, slots=True)
class DocumentProvenancePreviewDTO:
    source_file: str
    evidence_id: str
    location_kind: DocumentPreviewLocationKind
    location_label: str
    page_or_slide_numbers: tuple[int, ...]
    section_path: tuple[str, ...]
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "evidence_id": self.evidence_id,
            "location_kind": self.location_kind.value,
            "location_label": self.location_label,
            "page_or_slide_numbers": list(self.page_or_slide_numbers),
            "section_path": list(self.section_path),
            "snippet": self.snippet,
        }


@dataclass(frozen=True, slots=True)
class DocumentExercisePreviewDTO:
    exercise_id: str
    question: str
    answer_candidates: tuple[str, ...]
    verification_status: DocumentExerciseCandidateStatus
    source_file: str
    location_kind: DocumentPreviewLocationKind
    location_label: str
    source_generated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "exercise_id": self.exercise_id,
            "question": self.question,
            "answer_candidates": list(self.answer_candidates),
            "verification_status": self.verification_status.value,
            "source_file": self.source_file,
            "location_kind": self.location_kind.value,
            "location_label": self.location_label,
            "source_generated": self.source_generated,
        }


@dataclass(frozen=True, slots=True)
class DocumentImportBatchResultDTO:
    schema: str
    batch_id: str
    state: DocumentImportBatchState
    progress: DocumentImportProgressDTO
    files: tuple[DocumentImportFileResultDTO, ...]
    provenance_preview: tuple[DocumentProvenancePreviewDTO, ...]
    exercise_preview: tuple[DocumentExercisePreviewDTO, ...]
    knowledge_handoff_status: DocumentKnowledgeHandoffStatus
    checkpoint_hash: str | None
    resumed_file_count: int

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "batch_id": self.batch_id,
            "state": self.state.value,
            "progress": self.progress.to_dict(),
            "files": [item.to_dict() for item in self.files],
            "provenance_preview": [
                item.to_dict() for item in self.provenance_preview
            ],
            "exercise_preview": [item.to_dict() for item in self.exercise_preview],
            "knowledge_handoff_status": self.knowledge_handoff_status.value,
            "checkpoint_hash": self.checkpoint_hash,
            "resumed_file_count": self.resumed_file_count,
        }
        payload["result_hash"] = sha256_json(payload)
        return payload


@dataclass(slots=True)
class _SelectionRecord:
    selection_id: str
    paths: tuple[Path, ...]
    metadata_by_path: dict[str, DocumentSourceMetadata]
    candidates: tuple[DocumentImportCandidateDTO, ...] = ()
    preflight_complete: bool = False


@dataclass(slots=True)
class _BatchRecord:
    batch_id: str
    selection_id: str
    request: DocumentBatchRequest
    selected_file_ids: tuple[str, ...]
    attempt: int
    state: DocumentImportBatchState = DocumentImportBatchState.QUEUED
    progress: DocumentImportProgressDTO | None = None
    result: DocumentImportBatchResultDTO | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    started_at: float = field(default_factory=time.perf_counter)
    error_code: str | None = None


class DocumentImportStateError(ValueError):
    pass


class DocumentImportApplication:
    """Run one isolated import batch with resumable checkpoint semantics."""

    def __init__(
        self,
        ingestion_application: DocumentIngestionApplication,
        *,
        state_root: Path,
        knowledge_pipeline_port: DocumentKnowledgeBatchPipelinePort | None = None,
        preview_items_per_file: int = 20,
    ) -> None:
        if not state_root.is_absolute():
            raise ValueError("document import state_root must be absolute")
        if preview_items_per_file <= 0:
            raise ValueError("preview_items_per_file must be positive")
        self.ingestion_application = ingestion_application
        self.state_root = state_root
        self.knowledge_pipeline_port = knowledge_pipeline_port
        self.preview_items_per_file = preview_items_per_file
        self._selections: dict[str, _SelectionRecord] = {}
        self._batches: dict[str, _BatchRecord] = {}
        self._active_batch_id: str | None = None
        self._lock = threading.RLock()
        self.state_root.mkdir(parents=True, exist_ok=True)

    def select(
        self,
        paths: Sequence[str | Path],
        *,
        metadata_by_path: Mapping[str | Path, DocumentSourceMetadata] | None = None,
    ) -> DocumentImportSelectionDTO:
        if not paths:
            raise ValueError("at least one document must be selected")
        selection_id = f"docselection_{uuid.uuid4().hex}"
        record = _SelectionRecord(
            selection_id=selection_id,
            paths=tuple(Path(path) for path in paths),
            metadata_by_path={
                str(key): value for key, value in (metadata_by_path or {}).items()
            },
        )
        with self._lock:
            self._selections[selection_id] = record
        return self._selection_dto(record)

    def preflight(self, selection_id: str) -> DocumentImportSelectionDTO:
        with self._lock:
            record = self._selection(selection_id)
            paths = record.paths
            metadata = dict(record.metadata_by_path)
        candidates = tuple(
            self._candidate_dto(
                selection_id,
                index,
                path,
                self.ingestion_application.preflight.inspect(
                    path,
                    metadata=metadata.get(str(path)),
                ),
            )
            for index, path in enumerate(paths)
        )
        with self._lock:
            record.candidates = candidates
            record.preflight_complete = True
            return self._selection_dto(record)

    def start_batch(
        self,
        selection_id: str,
        *,
        selected_file_ids: Sequence[str] | None = None,
        conversion_config_identity: str = "document-import-v1",
        conversion_batch_size: int = 1,
    ) -> DocumentImportProgressDTO:
        with self._lock:
            if self._active_batch_id is not None:
                raise DocumentImportStateError("another document batch is running")
            selection = self._selection(selection_id)
        if not selection.preflight_complete:
            self.preflight(selection_id)
        with self._lock:
            selection = self._selection(selection_id)
            by_id = {
                candidate.file_id: (candidate, path)
                for candidate, path in zip(
                    selection.candidates,
                    selection.paths,
                    strict=True,
                )
            }
            chosen = tuple(
                selected_file_ids
                if selected_file_ids is not None
                else (
                    candidate.file_id
                    for candidate in selection.candidates
                    if candidate.selectable
                )
            )
            if not chosen:
                raise DocumentImportStateError("no processable documents selected")
            if any(
                file_id not in by_id or not by_id[file_id][0].selectable
                for file_id in chosen
            ):
                raise DocumentImportStateError(
                    "batch selection contains an unprocessable document"
                )
            selected_paths = tuple(by_id[file_id][1] for file_id in chosen)
            selected_metadata = {
                path: selection.metadata_by_path[str(path)]
                for path in selected_paths
                if str(path) in selection.metadata_by_path
            }
            batch_id = f"docimport_{uuid.uuid4().hex}"
            record = _BatchRecord(
                batch_id=batch_id,
                selection_id=selection_id,
                request=DocumentBatchRequest(
                    request_id=batch_id,
                    selected_documents=selected_paths,
                    conversion_config_identity=conversion_config_identity,
                    metadata_by_path=selected_metadata,
                    conversion_batch_size=conversion_batch_size,
                ),
                selected_file_ids=chosen,
                attempt=1,
            )
            record.progress = self._initial_progress(record)
            self._batches[batch_id] = record
            self._active_batch_id = batch_id
            self._write_manifest(record)
            self._start_thread(record)
            return record.progress

    def progress(self, batch_id: str) -> DocumentImportProgressDTO:
        with self._lock:
            record = self._batch(batch_id)
            assert record.progress is not None
            return record.progress

    def result(self, batch_id: str) -> DocumentImportBatchResultDTO | None:
        with self._lock:
            return self._batch(batch_id).result

    def cancel(self, batch_id: str) -> DocumentImportProgressDTO:
        with self._lock:
            record = self._batch(batch_id)
            if record.state.terminal:
                assert record.progress is not None
                return record.progress
            record.cancel_event.set()
            record.state = DocumentImportBatchState.CANCELLING
            assert record.progress is not None
            record.progress = self._replace_progress_state(
                record.progress,
                DocumentImportBatchState.CANCELLING,
            )
            self._write_manifest(record)
            return record.progress

    def resume(self, batch_id: str) -> DocumentImportProgressDTO:
        with self._lock:
            if self._active_batch_id is not None:
                raise DocumentImportStateError("another document batch is running")
            manifest = self._load_manifest(batch_id)
            request = DocumentBatchRequest(
                request_id=batch_id,
                selected_documents=tuple(
                    Path(value) for value in manifest["selected_documents"]
                ),
                conversion_config_identity=str(
                    manifest["conversion_config_identity"]
                ),
                metadata_by_path={
                    Path(key): DocumentSourceMetadata(**value)
                    for key, value in manifest.get("metadata_by_path", {}).items()
                },
                conversion_batch_size=int(manifest["conversion_batch_size"]),
            )
            record = _BatchRecord(
                batch_id=batch_id,
                selection_id=str(manifest["selection_id"]),
                request=request,
                selected_file_ids=tuple(manifest.get("selected_file_ids", ())),
                attempt=int(manifest.get("attempt", 1)) + 1,
            )
            record.progress = self._initial_progress(record)
            self._batches[batch_id] = record
            self._active_batch_id = batch_id
            self._write_manifest(record)
            self._start_thread(record)
            return record.progress

    def wait(
        self,
        batch_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> DocumentImportBatchResultDTO | None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        with self._lock:
            thread = self._batch(batch_id).thread
        if thread is not None:
            thread.join(timeout=timeout_seconds)
            if thread.is_alive():
                raise TimeoutError("document import batch did not finish in time")
        return self.result(batch_id)

    def close(self, *, timeout_seconds: float = 30) -> None:
        with self._lock:
            running = tuple(
                record
                for record in self._batches.values()
                if record.thread is not None and record.thread.is_alive()
            )
            for record in running:
                record.cancel_event.set()
        for record in running:
            assert record.thread is not None
            record.thread.join(timeout=timeout_seconds)
            if record.thread.is_alive():
                raise TimeoutError("document import worker did not stop in time")

    def _start_thread(self, record: _BatchRecord) -> None:
        thread = threading.Thread(
            target=self._run_batch,
            args=(record,),
            name=f"{record.batch_id}-runtime",
            daemon=False,
        )
        record.thread = thread
        thread.start()

    def _run_batch(self, record: _BatchRecord) -> None:
        with self._lock:
            record.state = DocumentImportBatchState.RUNNING
            assert record.progress is not None
            record.progress = self._replace_progress_state(
                record.progress,
                DocumentImportBatchState.RUNNING,
            )
        checkpoint_store = DocumentCheckpointStore(
            self._checkpoint_path(record.batch_id)
        )
        runtime = DocumentLearningCorpusRuntime(
            self.ingestion_application,
            checkpoint_store=checkpoint_store,
            knowledge_pipeline_port=self.knowledge_pipeline_port,
        )

        def observe(progress: DocumentIngestionProgress) -> None:
            with self._lock:
                state = (
                    DocumentImportBatchState.CANCELLING
                    if record.cancel_event.is_set()
                    else DocumentImportBatchState.RUNNING
                )
                record.state = state
                record.progress = DocumentImportProgressDTO(
                    batch_id=record.batch_id,
                    state=state,
                    total_files=progress.total_documents,
                    completed_files=progress.completed_documents,
                    processing_file=progress.current_source,
                    success_files=progress.success_count,
                    limited_files=progress.limited_count,
                    failed_files=progress.failed_count,
                    cancelled_files=0,
                    skipped_duplicate_files=progress.duplicate_count,
                    evidence_count=progress.evidence_count,
                    knowledge_handoff_status=(
                        DocumentKnowledgeHandoffStatus.PENDING
                    ),
                    elapsed_seconds=progress.elapsed_seconds,
                    attempt=record.attempt,
                )

        try:
            runtime_result = runtime.run(
                record.request,
                progress_sink=observe,
                should_stop=lambda _progress: record.cancel_event.is_set(),
            )
            import_result = self._result_dto(record, runtime_result)
            with self._lock:
                record.result = import_result
                record.state = import_result.state
                record.progress = import_result.progress
                self._write_manifest(record)
        except Exception:
            with self._lock:
                record.error_code = "DOCUMENT_IMPORT_BATCH_FAILED"
                record.state = DocumentImportBatchState.FAILED
                prior = record.progress or self._initial_progress(record)
                record.progress = self._replace_progress_state(
                    prior,
                    DocumentImportBatchState.FAILED,
                    handoff_status=DocumentKnowledgeHandoffStatus.FAILED,
                )
                self._write_manifest(record)
        finally:
            with self._lock:
                if self._active_batch_id == record.batch_id:
                    self._active_batch_id = None

    def _result_dto(
        self,
        record: _BatchRecord,
        runtime_result: DocumentLearningCorpusResult,
    ) -> DocumentImportBatchResultDTO:
        ingestion = runtime_result.ingestion
        formats = {
            receipt.source_file: receipt.conversion_identity.source_type
            for receipt in ingestion.receipts
            if receipt.conversion_identity is not None
        }
        files = tuple(self._file_result(receipt) for receipt in ingestion.receipts)
        provenance = self._provenance_preview(runtime_result, formats)
        exercises = self._exercise_preview(runtime_result, formats)
        handoff_status = self._handoff_status(runtime_result)
        state = self._final_state(ingestion.status, files)
        cancelled_files = sum(item.status == "CANCELLED" for item in files)
        failed_files = sum(
            item.status in {"FAILED", "UNSUPPORTED"} for item in files
        )
        progress = DocumentImportProgressDTO(
            batch_id=record.batch_id,
            state=state,
            total_files=len(files),
            completed_files=len(files),
            processing_file=None,
            success_files=sum(item.status == "SUCCESS" for item in files),
            limited_files=sum(item.status == "LIMITED" for item in files),
            failed_files=failed_files,
            cancelled_files=cancelled_files,
            skipped_duplicate_files=sum(
                item.status == "SKIPPED_DUPLICATE" for item in files
            ),
            evidence_count=sum(item.evidence_count for item in files),
            knowledge_handoff_status=handoff_status,
            elapsed_seconds=max(0.0, time.perf_counter() - record.started_at),
            attempt=record.attempt,
        )
        return DocumentImportBatchResultDTO(
            schema=DOCUMENT_IMPORT_API_SCHEMA,
            batch_id=record.batch_id,
            state=state,
            progress=progress,
            files=files,
            provenance_preview=provenance,
            exercise_preview=exercises,
            knowledge_handoff_status=handoff_status,
            checkpoint_hash=runtime_result.checkpoint_hash,
            resumed_file_count=sum(item.resumed for item in files),
        )

    def _file_result(self, receipt: Any) -> DocumentImportFileResultDTO:
        failure_code = receipt.failure.error_code if receipt.failure else None
        status = receipt.status.value
        if failure_code == "DOCUMENT_INGESTION_INTERRUPTED":
            status = "CANCELLED"
        return DocumentImportFileResultDTO(
            source_file=receipt.source_file,
            format=(
                receipt.conversion_identity.source_type
                if receipt.conversion_identity is not None
                else _FORMAT_BY_SUFFIX.get(Path(receipt.source_file).suffix.casefold())
            ),
            status=status,
            resumed=receipt.resumed,
            evidence_count=receipt.evidence_count,
            limitation_messages=tuple(
                _limitation_message(code) for code in receipt.limitations
            ),
            failure_code=failure_code,
            failure_reason=(
                _failure_message(failure_code) if failure_code is not None else None
            ),
            duplicate_of=receipt.duplicate_of,
        )

    def _provenance_preview(
        self,
        runtime_result: DocumentLearningCorpusResult,
        formats: Mapping[str, DocumentFormat],
    ) -> tuple[DocumentProvenancePreviewDTO, ...]:
        preview: list[DocumentProvenancePreviewDTO] = []
        for bundle in runtime_result.ingestion.bundles:
            for evidence in bundle.evidence[: self.preview_items_per_file]:
                kind, label, numbers = self._location(
                    formats.get(bundle.source_file),
                    tuple(
                        sorted(
                            {
                                item.page_no
                                for item in evidence.provenance
                                if item.page_no is not None
                            }
                        )
                    ),
                    evidence.section_path,
                )
                preview.append(
                    DocumentProvenancePreviewDTO(
                        source_file=bundle.source_file,
                        evidence_id=evidence.evidence_id,
                        location_kind=kind,
                        location_label=label,
                        page_or_slide_numbers=numbers,
                        section_path=evidence.section_path,
                        snippet=" ".join(evidence.content.split())[:240],
                    )
                )
        return tuple(preview)

    def _exercise_preview(
        self,
        runtime_result: DocumentLearningCorpusResult,
        formats: Mapping[str, DocumentFormat],
    ) -> tuple[DocumentExercisePreviewDTO, ...]:
        preview: list[DocumentExercisePreviewDTO] = []
        for entry in runtime_result.exercise_corpus.entries:
            lineage = entry.source_lineage[0]
            kind, label, _numbers = self._location(
                formats.get(lineage.source_file),
                lineage.question_page_numbers,
                lineage.question_section_path,
            )
            preview.append(
                DocumentExercisePreviewDTO(
                    exercise_id=entry.entry_id,
                    question=entry.question,
                    answer_candidates=tuple(
                        answer.content for answer in entry.answers
                    ),
                    verification_status=entry.status,
                    source_file=lineage.source_file,
                    location_kind=kind,
                    location_label=label,
                    source_generated=entry.source_generated,
                )
            )
        return tuple(preview)

    @staticmethod
    def _location(
        format_: DocumentFormat | None,
        numbers: Sequence[int],
        section_path: Sequence[str],
    ) -> tuple[DocumentPreviewLocationKind, str, tuple[int, ...]]:
        normalized = tuple(numbers)
        if normalized and format_ is DocumentFormat.PPTX:
            label = "、".join(f"幻灯片 {number}" for number in normalized)
            return DocumentPreviewLocationKind.SLIDE, label, normalized
        if normalized:
            label = "、".join(f"第 {number} 页" for number in normalized)
            return DocumentPreviewLocationKind.PAGE, label, normalized
        if section_path:
            section = " / ".join(section_path)
            return DocumentPreviewLocationKind.SECTION, f"章节：{section}", ()
        return DocumentPreviewLocationKind.UNKNOWN, "位置未精确标注", ()

    @staticmethod
    def _handoff_status(
        result: DocumentLearningCorpusResult,
    ) -> DocumentKnowledgeHandoffStatus:
        return {
            DocumentBatchSubmissionStatus.SUCCESS: (
                DocumentKnowledgeHandoffStatus.SUCCESS
            ),
            DocumentBatchSubmissionStatus.FAILED: (
                DocumentKnowledgeHandoffStatus.FAILED
            ),
            DocumentBatchSubmissionStatus.NOT_CONFIGURED: (
                DocumentKnowledgeHandoffStatus.NOT_CONFIGURED
            ),
        }[result.submission_receipt.status]

    @staticmethod
    def _final_state(
        status: DocumentBatchStatus,
        files: Sequence[DocumentImportFileResultDTO],
    ) -> DocumentImportBatchState:
        if status is DocumentBatchStatus.INTERRUPTED:
            return DocumentImportBatchState.CANCELLED
        failed = sum(item.status in {"FAILED", "UNSUPPORTED"} for item in files)
        useful = len(files) - failed
        if failed == 0:
            return DocumentImportBatchState.COMPLETED
        if useful > 0:
            return DocumentImportBatchState.PARTIAL
        return DocumentImportBatchState.FAILED

    def _candidate_dto(
        self,
        selection_id: str,
        index: int,
        path: Path,
        result: DocumentPreflightResult,
    ) -> DocumentImportCandidateDTO:
        candidate = result.candidate
        format_ = candidate.format if candidate else _FORMAT_BY_SUFFIX.get(
            path.suffix.casefold()
        )
        estimate = result.estimate
        limitations = tuple(result.limitations)
        if result.accepted:
            status = (
                DocumentUserPreflightStatus.LIMITED
                if limitations or not result.conversion_ready
                else DocumentUserPreflightStatus.PROCESSABLE
            )
        else:
            status = DocumentUserPreflightStatus.UNPROCESSABLE
        source_file = (
            candidate.source_file if candidate is not None else self._source_label(path)
        )
        identity = {
            "selection_id": selection_id,
            "index": index,
            "source_file": source_file,
        }
        reason_code = None if result.accepted else result.error_code
        limitation_messages = tuple(
            _limitation_message(code) for code in limitations
        )
        reason_message = (
            None
            if result.accepted
            else _failure_message(reason_code, preflight=True)
        )
        return DocumentImportCandidateDTO(
            file_id=f"doccandidate_{sha256_json(identity)[:32]}",
            source_file=source_file,
            display_name=Path(source_file).name,
            format=format_,
            format_label=_FORMAT_LABELS.get(format_, "未知格式"),
            size_bytes=(
                candidate.byte_count
                if candidate is not None
                else estimate.byte_count if estimate is not None else None
            ),
            processing_shape=(
                estimate.shape
                if estimate is not None
                else DocumentProcessingShape.UNKNOWN
            ),
            unit_kind=estimate.unit_kind if estimate is not None else None,
            unit_count=estimate.unit_count if estimate is not None else None,
            status=status,
            status_label=_PREFLIGHT_STATUS_LABELS[status],
            selectable=result.accepted and result.conversion_ready,
            conversion_ready=result.conversion_ready,
            limitation_codes=limitations,
            limitation_messages=limitation_messages,
            reason_code=reason_code,
            reason_message=reason_message,
        )

    def _source_label(self, path: Path) -> str:
        root = self.ingestion_application.policy.input_root.resolve(strict=False)
        candidate = path if path.is_absolute() else root / path
        try:
            return candidate.resolve(strict=False).relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError):
            return path.name or "document"

    def _selection_dto(
        self,
        record: _SelectionRecord,
    ) -> DocumentImportSelectionDTO:
        return DocumentImportSelectionDTO(
            schema=DOCUMENT_IMPORT_API_SCHEMA,
            selection_id=record.selection_id,
            preflight_complete=record.preflight_complete,
            candidates=record.candidates,
            selected_file_ids=tuple(
                item.file_id for item in record.candidates if item.selectable
            ),
        )

    def _initial_progress(self, record: _BatchRecord) -> DocumentImportProgressDTO:
        return DocumentImportProgressDTO(
            batch_id=record.batch_id,
            state=DocumentImportBatchState.QUEUED,
            total_files=len(record.request.selected_documents),
            completed_files=0,
            processing_file=None,
            success_files=0,
            limited_files=0,
            failed_files=0,
            cancelled_files=0,
            skipped_duplicate_files=0,
            evidence_count=0,
            knowledge_handoff_status=DocumentKnowledgeHandoffStatus.PENDING,
            elapsed_seconds=0.0,
            attempt=record.attempt,
        )

    @staticmethod
    def _replace_progress_state(
        progress: DocumentImportProgressDTO,
        state: DocumentImportBatchState,
        *,
        handoff_status: DocumentKnowledgeHandoffStatus | None = None,
    ) -> DocumentImportProgressDTO:
        return DocumentImportProgressDTO(
            batch_id=progress.batch_id,
            state=state,
            total_files=progress.total_files,
            completed_files=progress.completed_files,
            processing_file=progress.processing_file,
            success_files=progress.success_files,
            limited_files=progress.limited_files,
            failed_files=progress.failed_files,
            cancelled_files=progress.cancelled_files,
            skipped_duplicate_files=progress.skipped_duplicate_files,
            evidence_count=progress.evidence_count,
            knowledge_handoff_status=(
                handoff_status or progress.knowledge_handoff_status
            ),
            elapsed_seconds=progress.elapsed_seconds,
            attempt=progress.attempt,
        )

    def _selection(self, selection_id: str) -> _SelectionRecord:
        try:
            return self._selections[selection_id]
        except KeyError as error:
            raise DocumentImportStateError("document selection was not found") from error

    def _batch(self, batch_id: str) -> _BatchRecord:
        try:
            return self._batches[batch_id]
        except KeyError as error:
            raise DocumentImportStateError("document batch was not found") from error

    def _manifest_path(self, batch_id: str) -> Path:
        return self.state_root / f"{batch_id}.manifest.json"

    def _checkpoint_path(self, batch_id: str) -> Path:
        return (self.state_root / f"{batch_id}.checkpoint.json").resolve()

    def _write_manifest(self, record: _BatchRecord) -> None:
        payload = {
            "schema": DOCUMENT_IMPORT_MANIFEST_SCHEMA,
            "batch_id": record.batch_id,
            "selection_id": record.selection_id,
            "selected_documents": [
                str(path) for path in record.request.selected_documents
            ],
            "selected_file_ids": list(record.selected_file_ids),
            "conversion_config_identity": (
                record.request.conversion_config_identity
            ),
            "conversion_batch_size": record.request.conversion_batch_size,
            "metadata_by_path": {
                str(path): metadata.to_dict()
                for path, metadata in record.request.metadata_by_path.items()
            },
            "attempt": record.attempt,
            "state": record.state.value,
            "error_code": record.error_code,
        }
        envelope = {
            "schema": DOCUMENT_IMPORT_MANIFEST_SCHEMA,
            "manifest_hash": sha256_json(payload),
            "manifest": payload,
        }
        path = self._manifest_path(record.batch_id)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f"{path.name}.",
                suffix=".tmp",
                dir=self.state_root,
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(canonical_json_bytes(envelope))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _load_manifest(self, batch_id: str) -> dict[str, Any]:
        path = self._manifest_path(batch_id)
        if not path.is_file():
            raise DocumentImportStateError("document batch manifest was not found")
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            payload = envelope["manifest"]
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as error:
            raise DocumentImportStateError(
                "document batch manifest is unreadable"
            ) from error
        if (
            envelope.get("schema") != DOCUMENT_IMPORT_MANIFEST_SCHEMA
            or not isinstance(payload, dict)
            or payload.get("schema") != DOCUMENT_IMPORT_MANIFEST_SCHEMA
            or payload.get("batch_id") != batch_id
            or envelope.get("manifest_hash") != sha256_json(payload)
        ):
            raise DocumentImportStateError(
                "document batch manifest failed integrity validation"
            )
        return payload

    def __enter__(self) -> "DocumentImportApplication":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


__all__ = [
    "DOCUMENT_IMPORT_API_SCHEMA",
    "DOCUMENT_IMPORT_MANIFEST_SCHEMA",
    "DocumentExercisePreviewDTO",
    "DocumentImportApplication",
    "DocumentImportBatchResultDTO",
    "DocumentImportBatchState",
    "DocumentImportCandidateDTO",
    "DocumentImportFileResultDTO",
    "DocumentImportProgressDTO",
    "DocumentImportSelectionDTO",
    "DocumentImportStateError",
    "DocumentKnowledgeHandoffStatus",
    "DocumentPreviewLocationKind",
    "DocumentProvenancePreviewDTO",
    "DocumentUserPreflightStatus",
]
