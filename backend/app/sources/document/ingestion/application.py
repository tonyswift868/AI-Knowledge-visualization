"""Docling ``convert_all`` runtime with per-document isolation and resume."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from ..contracts import (
    DoclingDependencyInfo,
    DocumentEvidenceBundle,
    DocumentPreflightResult,
    DocumentPreflightStatus,
    DocumentSourceCandidate,
    DocumentSourceMetadata,
    DocumentSourcePolicy,
    sha256_json,
)
from ..evidence import DocumentEvidenceBuildError, DocumentEvidenceBuilder
from ..preflight import DependencyProbe, DocumentSourcePreflight, probe_docling_dependency
from ..source import build_docling_converter
from .bridge import DocumentKnowledgePipelinePort, build_document_pipeline_handoff
from .contracts import (
    DocumentBatchRequest,
    DocumentBatchResult,
    DocumentBatchStatus,
    DocumentCheckpointEntry,
    DocumentConversionIdentity,
    DocumentFingerprint,
    DocumentIngestionCheckpoint,
    DocumentIngestionFailure,
    DocumentIngestionMetrics,
    DocumentIngestionProgress,
    DocumentIngestionReceipt,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentPipelineHandoff,
    DoclingRuntimeReceipt,
)
from .execution import (
    DoclingConversionRunner,
    DoclingExecutionEnvironment,
    WindowsDoclingCompatibilityPolicy,
)
from .exercises import DocumentExerciseExtractor
from .provenance import DocumentProvenanceGate, DocumentProvenanceGateError


class DoclingBatchConverterProtocol(Protocol):
    def convert_all(self, source: Sequence[Path], **kwargs: Any) -> Iterator[Any]: ...


BatchConverterFactory = Callable[[DocumentSourcePolicy], DoclingBatchConverterProtocol]
ProgressSink = Callable[[DocumentIngestionProgress], None]
StopPredicate = Callable[[DocumentIngestionProgress], bool]
MonotonicClock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class _PreparedDocument:
    index: int
    path: str | Path
    preflight: DocumentPreflightResult
    candidate: DocumentSourceCandidate
    fingerprint: DocumentFingerprint
    conversion_identity: DocumentConversionIdentity


def _status_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().casefold()


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


class DocumentBatchIngestion:
    """Application service joining document preflight, Docling, and handoff."""

    def __init__(
        self,
        policy: DocumentSourcePolicy,
        *,
        dependency_probe: DependencyProbe = probe_docling_dependency,
        converter_factory: BatchConverterFactory | None = None,
        pipeline_port: DocumentKnowledgePipelinePort | None = None,
        provenance_gate: DocumentProvenanceGate | None = None,
        execution_environment: DoclingExecutionEnvironment | None = None,
        compatibility_policy: WindowsDoclingCompatibilityPolicy | None = None,
        exercise_extractor: DocumentExerciseExtractor | None = None,
        clock: MonotonicClock = time.perf_counter,
    ) -> None:
        self.policy = policy
        self.dependency: DoclingDependencyInfo = dependency_probe()
        self.preflight = DocumentSourcePreflight(
            policy,
            dependency_probe=lambda: self.dependency,
        )
        self._converter_factory = converter_factory
        self._pipeline_port = pipeline_port
        self._provenance_gate = provenance_gate or DocumentProvenanceGate()
        self._execution_environment = execution_environment
        self._compatibility_policy = compatibility_policy
        self._exercise_extractor = exercise_extractor or DocumentExerciseExtractor()
        self._clock = clock

    def ingest(
        self,
        request: DocumentBatchRequest,
        *,
        checkpoint: DocumentIngestionCheckpoint | None = None,
        progress_sink: ProgressSink | None = None,
        should_stop: StopPredicate | None = None,
    ) -> DocumentBatchResult:
        checkpoint = checkpoint or DocumentIngestionCheckpoint()
        started = self._clock()
        durations = {
            "preflight": 0.0,
            "conversion": 0.0,
            "mapping": 0.0,
            "gate": 0.0,
            "handoff": 0.0,
        }
        counts = {"preflight": 0, "converted": 0, "evidence": 0}
        receipts_by_index: dict[int, DocumentIngestionReceipt] = {}
        bundle_by_identity: dict[str, DocumentEvidenceBundle] = {}
        handoff_by_identity: dict[str, DocumentPipelineHandoff] = {}
        progress: list[DocumentIngestionProgress] = []
        interrupted = False

        def emit(stage: DocumentIngestionStage, current_source: str | None) -> None:
            receipts = tuple(receipts_by_index.values())
            item = DocumentIngestionProgress(
                sequence=len(progress),
                request_id=request.request_id,
                stage=stage,
                total_documents=len(request.selected_documents),
                completed_documents=len(receipts),
                success_count=sum(
                    value.status is DocumentIngestionStatus.SUCCESS for value in receipts
                ),
                failed_count=sum(
                    value.status
                    in {
                        DocumentIngestionStatus.FAILED,
                        DocumentIngestionStatus.UNSUPPORTED,
                    }
                    for value in receipts
                ),
                limited_count=sum(
                    value.status is DocumentIngestionStatus.LIMITED for value in receipts
                ),
                duplicate_count=sum(
                    value.status is DocumentIngestionStatus.SKIPPED_DUPLICATE
                    for value in receipts
                ),
                current_source=current_source,
                elapsed_seconds=max(0.0, self._clock() - started),
            )
            progress.append(item)
            if progress_sink is not None:
                progress_sink(item)

        emit(DocumentIngestionStage.PREFLIGHT, None)
        if (
            self.policy.max_batch_files is not None
            and len(request.selected_documents) > self.policy.max_batch_files
        ):
            for index, path in enumerate(request.selected_documents):
                source_file = self._source_label(path)
                receipts_by_index[index] = self._failed_receipt(
                    index=index,
                    source_file=source_file,
                    source_identity=self._unresolved_identity(source_file),
                    stage=DocumentIngestionStage.PREFLIGHT,
                    error_class="DocumentBatchLimitError",
                    error_code="DOCUMENT_BATCH_SIZE_LIMIT",
                    message="Batch exceeds the caller-configured file-count limit",
                    retryable=False,
                )
                emit(DocumentIngestionStage.PREFLIGHT, source_file)
            return self._result(
                request=request,
                receipts_by_index=receipts_by_index,
                bundle_by_identity=bundle_by_identity,
                handoff_by_identity=handoff_by_identity,
                checkpoint=checkpoint,
                progress=progress,
                durations=durations,
                counts=counts,
                interrupted=False,
                runtime_receipts=(),
            )

        prepared: list[_PreparedDocument] = []
        seen_identity: dict[str, str] = {}
        for index, path in enumerate(request.selected_documents):
            source_file = self._source_label(path)
            before = self._clock()
            preflight = self.preflight.inspect(
                path,
                metadata=self._metadata_for(path, request),
            )
            durations["preflight"] += max(0.0, self._clock() - before)
            counts["preflight"] += 1
            candidate = preflight.candidate
            if candidate is None:
                unsupported = preflight.error_code == "DOCUMENT_FORMAT_UNSUPPORTED"
                status = (
                    DocumentIngestionStatus.UNSUPPORTED
                    if unsupported
                    else DocumentIngestionStatus.FAILED
                )
                receipts_by_index[index] = self._failed_receipt(
                    index=index,
                    source_file=source_file,
                    source_identity=self._unresolved_identity(source_file),
                    stage=DocumentIngestionStage.PREFLIGHT,
                    error_class=(
                        "UnsupportedDocumentFormat"
                        if unsupported
                        else "DocumentPreflightError"
                    ),
                    error_code=preflight.error_code or "DOCUMENT_PREFLIGHT_FAILED",
                    message=preflight.error_message or "Document preflight failed",
                    retryable=False,
                    status=status,
                )
                emit(DocumentIngestionStage.PREFLIGHT, source_file)
                continue

            fingerprint = DocumentFingerprint(
                sha256=candidate.source_sha256,
                byte_count=candidate.byte_count,
            )
            identity = DocumentConversionIdentity.create(
                fingerprint=fingerprint,
                source_type=candidate.format,
                conversion_config_identity=request.conversion_config_identity,
            )
            duplicate_of = seen_identity.get(identity.value)
            if duplicate_of is not None:
                receipts_by_index[index] = DocumentIngestionReceipt(
                    index=index,
                    source_file=candidate.source_file,
                    source_id=candidate.source_id,
                    status=DocumentIngestionStatus.SKIPPED_DUPLICATE,
                    stage=DocumentIngestionStage.COMPLETE,
                    fingerprint=fingerprint,
                    conversion_identity=identity,
                    limitations=preflight.limitations,
                    duplicate_of=duplicate_of,
                )
                emit(DocumentIngestionStage.COMPLETE, candidate.source_file)
                continue
            seen_identity[identity.value] = candidate.source_file

            completed = checkpoint.get(identity)
            if completed is not None:
                receipt = completed.receipt.resumed_copy(
                    index=index,
                    source_file=candidate.source_file,
                )
                if completed.bundle is not None:
                    bundle_by_identity[identity.value] = completed.bundle
                handoff = completed.handoff
                if handoff is None and completed.bundle is not None:
                    handoff = build_document_pipeline_handoff(
                        completed.bundle,
                        conversion_identity=identity,
                        limitations=receipt.limitations,
                    )
                if handoff is not None:
                    handoff_by_identity[identity.value] = handoff
                receipt = self._submit_handoff(
                    receipt,
                    handoff=handoff,
                    checkpoint=checkpoint,
                    durations=durations,
                    already_submitted=completed.handoff_submitted,
                )
                receipts_by_index[index] = receipt
                emit(receipt.stage, candidate.source_file)
                continue

            if (
                not preflight.accepted
                or preflight.status is DocumentPreflightStatus.DEPENDENCY_UNAVAILABLE
            ):
                receipts_by_index[index] = self._failed_receipt(
                    index=index,
                    source_file=candidate.source_file,
                    source_identity=identity.value,
                    stage=DocumentIngestionStage.PREFLIGHT,
                    error_class="DoclingDependencyUnavailable",
                    error_code=preflight.error_code or "DOCUMENT_PREFLIGHT_FAILED",
                    message=preflight.error_message or "Document preflight failed",
                    retryable=True,
                    source_id=candidate.source_id,
                    fingerprint=fingerprint,
                    conversion_identity=identity,
                )
                emit(DocumentIngestionStage.PREFLIGHT, candidate.source_file)
                continue

            if not preflight.conversion_ready:
                receipt = DocumentIngestionReceipt(
                    index=index,
                    source_file=candidate.source_file,
                    source_id=candidate.source_id,
                    status=DocumentIngestionStatus.LIMITED,
                    stage=DocumentIngestionStage.COMPLETE,
                    fingerprint=fingerprint,
                    conversion_identity=identity,
                    limitations=preflight.limitations,
                )
                receipts_by_index[index] = receipt
                checkpoint.record(
                    DocumentCheckpointEntry(
                        conversion_identity=identity,
                        receipt=receipt,
                        bundle=None,
                        handoff=None,
                    )
                )
                emit(DocumentIngestionStage.COMPLETE, candidate.source_file)
                continue

            prepared.append(
                _PreparedDocument(
                    index=index,
                    path=path,
                    preflight=preflight,
                    candidate=candidate,
                    fingerprint=fingerprint,
                    conversion_identity=identity,
                )
            )

        emit(DocumentIngestionStage.PREFLIGHT, None)
        converter: DoclingBatchConverterProtocol | None = None
        if prepared:
            try:
                if self._converter_factory is not None:
                    converter = self._converter_factory(self.policy)
                else:
                    converter = DoclingConversionRunner(
                        self.policy,
                        execution_environment=self._execution_environment,
                        compatibility_policy=self._compatibility_policy,
                        converter_factory=build_docling_converter,
                    )
            except Exception as error:
                for item in prepared:
                    receipts_by_index[item.index] = self._failed_receipt(
                        index=item.index,
                        source_file=item.candidate.source_file,
                        source_identity=item.conversion_identity.value,
                        stage=DocumentIngestionStage.CONVERSION,
                        error_class=type(error).__name__,
                        error_code="DOCLING_CONVERTER_INITIALIZATION_FAILED",
                        message="Docling converter could not be initialized",
                        retryable=True,
                        source_id=item.candidate.source_id,
                        fingerprint=item.fingerprint,
                        conversion_identity=item.conversion_identity,
                    )
                    emit(DocumentIngestionStage.CONVERSION, item.candidate.source_file)
                prepared = []

        pending_after_interrupt: list[_PreparedDocument] = []
        assert converter is not None or not prepared
        for offset in range(0, len(prepared), request.conversion_batch_size):
            chunk = prepared[offset : offset + request.conversion_batch_size]
            iterator, stream_failure = self._start_convert_all_chunk(
                converter,
                chunk,
                durations=durations,
            )
            for position, item in enumerate(chunk):
                result: Any | None = None
                if stream_failure is None and iterator is not None:
                    before = self._clock()
                    try:
                        result = next(iterator)
                    except StopIteration:
                        stream_failure = RuntimeError("Docling conversion stream ended")
                    except Exception as error:
                        stream_failure = error
                    durations["conversion"] += max(0.0, self._clock() - before)
                if stream_failure is not None:
                    receipts_by_index[item.index] = self._failed_receipt(
                        index=item.index,
                        source_file=item.candidate.source_file,
                        source_identity=item.conversion_identity.value,
                        stage=DocumentIngestionStage.CONVERSION,
                        error_class=type(stream_failure).__name__,
                        error_code="DOCLING_BATCH_STREAM_FAILED",
                        message="Docling batch stream did not return this document",
                        retryable=True,
                        source_id=item.candidate.source_id,
                        fingerprint=item.fingerprint,
                        conversion_identity=item.conversion_identity,
                    )
                    emit(DocumentIngestionStage.CONVERSION, item.candidate.source_file)
                    continue
                assert result is not None
                receipt, bundle, handoff = self._consume_conversion_result(
                    item,
                    result,
                    checkpoint=checkpoint,
                    durations=durations,
                    counts=counts,
                )
                receipts_by_index[item.index] = receipt
                if bundle is not None:
                    bundle_by_identity[item.conversion_identity.value] = bundle
                if handoff is not None:
                    handoff_by_identity[item.conversion_identity.value] = handoff
                emit(receipt.stage, item.candidate.source_file)
                if should_stop is not None and should_stop(progress[-1]):
                    interrupted = True
                    pending_after_interrupt.extend(chunk[position + 1 :])
                    pending_after_interrupt.extend(
                        prepared[offset + request.conversion_batch_size :]
                    )
                    break
            if interrupted:
                break

        if interrupted:
            for item in pending_after_interrupt:
                if item.index in receipts_by_index:
                    continue
                receipts_by_index[item.index] = self._failed_receipt(
                    index=item.index,
                    source_file=item.candidate.source_file,
                    source_identity=item.conversion_identity.value,
                    stage=DocumentIngestionStage.INTERRUPTED,
                    error_class="DocumentIngestionInterrupted",
                    error_code="DOCUMENT_INGESTION_INTERRUPTED",
                    message="Document remained unfinished when batch ingestion stopped",
                    retryable=True,
                    source_id=item.candidate.source_id,
                    fingerprint=item.fingerprint,
                    conversion_identity=item.conversion_identity,
                )
                emit(DocumentIngestionStage.INTERRUPTED, item.candidate.source_file)

        emit(
            DocumentIngestionStage.INTERRUPTED if interrupted else DocumentIngestionStage.COMPLETE,
            None,
        )
        return self._result(
            request=request,
            receipts_by_index=receipts_by_index,
            bundle_by_identity=bundle_by_identity,
            handoff_by_identity=handoff_by_identity,
            checkpoint=checkpoint,
            progress=progress,
            durations=durations,
            counts=counts,
            interrupted=interrupted,
            runtime_receipts=tuple(
                getattr(converter, "runtime_receipts", ()) if converter else ()
            ),
        )

    def _start_convert_all_chunk(
        self,
        converter: DoclingBatchConverterProtocol,
        chunk: Sequence[_PreparedDocument],
        *,
        durations: dict[str, float],
    ) -> tuple[Iterator[Any] | None, BaseException | None]:
        kwargs: dict[str, Any] = {"raises_on_error": False}
        if self.policy.hard_max_file_bytes is not None:
            kwargs["max_file_size"] = self.policy.hard_max_file_bytes
        if self.policy.hard_max_pages is not None:
            kwargs["max_num_pages"] = self.policy.hard_max_pages
        before = self._clock()
        try:
            iterator = iter(
                converter.convert_all(
                    [item.candidate.resolved_path for item in chunk],
                    **kwargs,
                )
            )
        except Exception as error:
            durations["conversion"] += max(0.0, self._clock() - before)
            return None, error
        durations["conversion"] += max(0.0, self._clock() - before)
        return iterator, None

    def _consume_conversion_result(
        self,
        item: _PreparedDocument,
        result: Any,
        *,
        checkpoint: DocumentIngestionCheckpoint,
        durations: dict[str, float],
        counts: dict[str, int],
    ) -> tuple[
        DocumentIngestionReceipt,
        DocumentEvidenceBundle | None,
        DocumentPipelineHandoff | None,
    ]:
        status_text = _status_text(getattr(result, "status", None))
        partial = status_text == "partial_success"
        if status_text not in {"success", "partial_success"}:
            errors = tuple(getattr(result, "errors", ()) or ())
            error_class = (
                str(getattr(errors[0], "error_class", type(errors[0]).__name__))
                if errors
                else "DoclingConversionError"
            )
            return (
                self._failed_receipt(
                    index=item.index,
                    source_file=item.candidate.source_file,
                    source_identity=item.conversion_identity.value,
                    stage=DocumentIngestionStage.CONVERSION,
                    error_class=error_class,
                    error_code="DOCLING_CONVERSION_FAILED",
                    message="Docling could not convert this document",
                    retryable=False,
                    source_id=item.candidate.source_id,
                    fingerprint=item.fingerprint,
                    conversion_identity=item.conversion_identity,
                ),
                None,
                None,
            )
        document = getattr(result, "document", None)
        if document is None:
            return (
                self._failed_receipt(
                    index=item.index,
                    source_file=item.candidate.source_file,
                    source_identity=item.conversion_identity.value,
                    stage=DocumentIngestionStage.CONVERSION,
                    error_class="DoclingConversionError",
                    error_code="DOCLING_DOCUMENT_MISSING",
                    message="Docling returned no structured document",
                    retryable=False,
                    source_id=item.candidate.source_id,
                    fingerprint=item.fingerprint,
                    conversion_identity=item.conversion_identity,
                ),
                None,
                None,
            )
        counts["converted"] += 1
        before = self._clock()
        try:
            bundle = DocumentEvidenceBuilder(
                docling_version=self.dependency.version or "unknown"
            ).build(document, item.candidate)
        except DocumentEvidenceBuildError as error:
            durations["mapping"] += max(0.0, self._clock() - before)
            return (
                self._failed_receipt(
                    index=item.index,
                    source_file=item.candidate.source_file,
                    source_identity=item.conversion_identity.value,
                    stage=DocumentIngestionStage.EVIDENCE_MAPPING,
                    error_class=type(error).__name__,
                    error_code=error.code,
                    message="Structured document evidence could not be mapped",
                    retryable=False,
                    source_id=item.candidate.source_id,
                    fingerprint=item.fingerprint,
                    conversion_identity=item.conversion_identity,
                ),
                None,
                None,
            )
        except Exception as error:
            durations["mapping"] += max(0.0, self._clock() - before)
            return (
                self._failed_receipt(
                    index=item.index,
                    source_file=item.candidate.source_file,
                    source_identity=item.conversion_identity.value,
                    stage=DocumentIngestionStage.EVIDENCE_MAPPING,
                    error_class=type(error).__name__,
                    error_code="DOCUMENT_EVIDENCE_MAPPING_FAILED",
                    message="Structured document evidence could not be mapped",
                    retryable=False,
                    source_id=item.candidate.source_id,
                    fingerprint=item.fingerprint,
                    conversion_identity=item.conversion_identity,
                ),
                None,
                None,
            )
        durations["mapping"] += max(0.0, self._clock() - before)
        counts["evidence"] += len(bundle.evidence)

        before = self._clock()
        try:
            gate_limitations = self._provenance_gate.validate(
                bundle,
                source_type=item.candidate.format,
            )
        except DocumentProvenanceGateError as error:
            durations["gate"] += max(0.0, self._clock() - before)
            return (
                self._failed_receipt(
                    index=item.index,
                    source_file=item.candidate.source_file,
                    source_identity=item.conversion_identity.value,
                    stage=DocumentIngestionStage.PROVENANCE_GATE,
                    error_class=type(error).__name__,
                    error_code=error.code,
                    message="Document evidence failed the provenance gate",
                    retryable=False,
                    source_id=item.candidate.source_id,
                    fingerprint=item.fingerprint,
                    conversion_identity=item.conversion_identity,
                ),
                None,
                None,
            )
        durations["gate"] += max(0.0, self._clock() - before)
        limitations = _unique(
            (
                *item.preflight.limitations,
                *bundle.warnings,
                *(('DOCLING_PARTIAL_SUCCESS',) if partial else ()),
                *gate_limitations,
            )
        )
        handoff = build_document_pipeline_handoff(
            bundle,
            conversion_identity=item.conversion_identity,
            limitations=limitations,
        )
        receipt = DocumentIngestionReceipt(
            index=item.index,
            source_file=item.candidate.source_file,
            source_id=item.candidate.source_id,
            status=(
                DocumentIngestionStatus.LIMITED
                if limitations
                else DocumentIngestionStatus.SUCCESS
            ),
            stage=DocumentIngestionStage.COMPLETE,
            fingerprint=item.fingerprint,
            conversion_identity=item.conversion_identity,
            bundle_id=bundle.bundle_id,
            evidence_count=len(bundle.evidence),
            limitations=limitations,
        )
        checkpoint.record(
            DocumentCheckpointEntry(
                conversion_identity=item.conversion_identity,
                receipt=receipt,
                bundle=bundle,
                handoff=handoff,
            )
        )
        receipt = self._submit_handoff(
            receipt,
            handoff=handoff,
            checkpoint=checkpoint,
            durations=durations,
            already_submitted=False,
        )
        return receipt, bundle, handoff

    def _submit_handoff(
        self,
        receipt: DocumentIngestionReceipt,
        *,
        handoff: DocumentPipelineHandoff | None,
        checkpoint: DocumentIngestionCheckpoint,
        durations: dict[str, float],
        already_submitted: bool,
    ) -> DocumentIngestionReceipt:
        if handoff is None or self._pipeline_port is None:
            return replace(receipt, handoff_submitted=already_submitted)
        if already_submitted:
            return replace(receipt, handoff_submitted=True)
        before = self._clock()
        try:
            self._pipeline_port.submit(handoff)
        except Exception as error:
            durations["handoff"] += max(0.0, self._clock() - before)
            identity = receipt.conversion_identity
            assert identity is not None
            return self._failed_receipt(
                index=receipt.index,
                source_file=receipt.source_file,
                source_identity=identity.value,
                stage=DocumentIngestionStage.HANDOFF,
                error_class=type(error).__name__,
                error_code="DOCUMENT_PIPELINE_HANDOFF_FAILED",
                message="Document pipeline handoff was not accepted",
                retryable=True,
                source_id=receipt.source_id,
                fingerprint=receipt.fingerprint,
                conversion_identity=identity,
                bundle_id=receipt.bundle_id,
                evidence_count=receipt.evidence_count,
                limitations=receipt.limitations,
                resumed=receipt.resumed,
            )
        durations["handoff"] += max(0.0, self._clock() - before)
        identity = receipt.conversion_identity
        assert identity is not None
        checkpoint.mark_handoff_submitted(identity)
        return replace(receipt, handoff_submitted=True)

    def _failed_receipt(
        self,
        *,
        index: int,
        source_file: str,
        source_identity: str,
        stage: DocumentIngestionStage,
        error_class: str,
        error_code: str,
        message: str,
        retryable: bool,
        status: DocumentIngestionStatus = DocumentIngestionStatus.FAILED,
        source_id: str | None = None,
        fingerprint: DocumentFingerprint | None = None,
        conversion_identity: DocumentConversionIdentity | None = None,
        bundle_id: str | None = None,
        evidence_count: int = 0,
        limitations: tuple[str, ...] = (),
        resumed: bool = False,
    ) -> DocumentIngestionReceipt:
        return DocumentIngestionReceipt(
            index=index,
            source_file=source_file,
            source_id=source_id,
            status=status,
            stage=stage,
            fingerprint=fingerprint,
            conversion_identity=conversion_identity,
            bundle_id=bundle_id,
            evidence_count=evidence_count,
            limitations=limitations,
            failure=DocumentIngestionFailure(
                error_class=error_class,
                source_identity=source_identity,
                source_file=source_file,
                stage=stage,
                retryable=retryable,
                error_code=error_code,
                message=message,
            ),
            resumed=resumed,
        )

    def _metadata_for(
        self,
        path: str | Path,
        request: DocumentBatchRequest,
    ) -> DocumentSourceMetadata | None:
        direct = request.metadata_by_path.get(path)
        if direct is not None:
            return direct
        target = str(path)
        for key, value in request.metadata_by_path.items():
            if str(key) == target:
                return value
        return None

    def _source_label(self, path: str | Path) -> str:
        raw = Path(path)
        candidate = raw if raw.is_absolute() else self.policy.input_root / raw
        try:
            resolved = candidate.resolve(strict=False)
            return resolved.relative_to(
                self.policy.input_root.resolve(strict=False)
            ).as_posix()
        except (OSError, RuntimeError, ValueError):
            normalized = str(path).replace("\\", "/").rstrip("/")
            return normalized.rsplit("/", 1)[-1] or "document"

    @staticmethod
    def _unresolved_identity(source_file: str) -> str:
        return f"unresolved_{sha256_json({'source_file': source_file})[:24]}"

    def _result(
        self,
        *,
        request: DocumentBatchRequest,
        receipts_by_index: dict[int, DocumentIngestionReceipt],
        bundle_by_identity: dict[str, DocumentEvidenceBundle],
        handoff_by_identity: dict[str, DocumentPipelineHandoff],
        checkpoint: DocumentIngestionCheckpoint,
        progress: list[DocumentIngestionProgress],
        durations: dict[str, float],
        counts: dict[str, int],
        interrupted: bool,
        runtime_receipts: tuple[DoclingRuntimeReceipt, ...],
    ) -> DocumentBatchResult:
        receipts = tuple(receipts_by_index[index] for index in sorted(receipts_by_index))
        if interrupted:
            status = DocumentBatchStatus.INTERRUPTED
        else:
            failure_count = sum(
                item.status
                in {DocumentIngestionStatus.FAILED, DocumentIngestionStatus.UNSUPPORTED}
                for item in receipts
            )
            limited_count = sum(
                item.status is DocumentIngestionStatus.LIMITED for item in receipts
            )
            useful_count = len(receipts) - failure_count
            if failure_count == 0 and limited_count == 0:
                status = DocumentBatchStatus.SUCCESS
            elif useful_count > 0:
                status = DocumentBatchStatus.PARTIAL
            else:
                status = DocumentBatchStatus.FAILED
        bundles = tuple(bundle_by_identity.values())
        return DocumentBatchResult(
            request_id=request.request_id,
            status=status,
            receipts=receipts,
            bundles=bundles,
            handoffs=tuple(handoff_by_identity.values()),
            checkpoint=checkpoint,
            progress=tuple(progress),
            metrics=DocumentIngestionMetrics(
                preflight_seconds=durations["preflight"],
                conversion_seconds=durations["conversion"],
                evidence_mapping_seconds=durations["mapping"],
                provenance_gate_seconds=durations["gate"],
                handoff_seconds=durations["handoff"],
                preflight_documents=counts["preflight"],
                converted_documents=counts["converted"],
                mapped_evidence_items=counts["evidence"],
            ),
            runtime_receipts=runtime_receipts,
            exercise_candidates=self._exercise_extractor.extract_batch(bundles),
        )


class DocumentIngestionApplication(DocumentBatchIngestion):
    """Product-facing name for the document batch ingestion application."""


__all__ = [
    "BatchConverterFactory",
    "DoclingBatchConverterProtocol",
    "DocumentBatchIngestion",
    "DocumentIngestionApplication",
    "MonotonicClock",
    "ProgressSink",
    "StopPredicate",
]
