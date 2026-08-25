"""Application-level orchestration for the document learning corpus runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from ..contracts import DocumentFormat, sha256_json
from .application import DocumentIngestionApplication, ProgressSink, StopPredicate
from .checkpoint import DocumentCheckpointStore
from .contracts import (
    DocumentBatchRequest,
    DocumentBatchResult,
    DocumentIngestionCheckpoint,
    DocumentIngestionStatus,
    DocumentPipelineHandoff,
)
from .corpus import DocumentExerciseCorpus, DocumentExerciseCorpusBuilder
from .quality import (
    DocumentProvenanceQualityReport,
    DocumentProvenanceQualityReporter,
)
from .review import DocumentReviewContentFeed, DocumentReviewContentFeedBuilder


DOCUMENT_KNOWLEDGE_BATCH_SCHEMA = "mentorflow.document-knowledge-batch.v1"
DOCUMENT_LEARNING_RUNTIME_SCHEMA = "mentorflow.document-learning-runtime.v1"


class DocumentBatchSubmissionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True, slots=True)
class DocumentKnowledgeBatchHandoff:
    schema: str
    handoff_id: str
    request_id: str
    corpus_id: str
    review_feed_id: str
    provenance_quality_report_id: str
    document_handoffs: tuple[DocumentPipelineHandoff, ...]
    completed_document_count: int
    failed_document_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "handoff_id": self.handoff_id,
            "request_id": self.request_id,
            "corpus_id": self.corpus_id,
            "review_feed_id": self.review_feed_id,
            "provenance_quality_report_id": self.provenance_quality_report_id,
            "document_handoffs": [item.to_dict() for item in self.document_handoffs],
            "completed_document_count": self.completed_document_count,
            "failed_document_count": self.failed_document_count,
        }


@dataclass(frozen=True, slots=True)
class DocumentKnowledgeBatchSubmissionReceipt:
    receipt_id: str
    handoff_id: str
    status: DocumentBatchSubmissionStatus
    error_class: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status is DocumentBatchSubmissionStatus.FAILED and not self.error_code:
            raise ValueError("failed batch submission requires an error code")
        if self.status is not DocumentBatchSubmissionStatus.FAILED and (
            self.error_class is not None or self.error_code is not None
        ):
            raise ValueError("successful or unconfigured submission cannot include error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "handoff_id": self.handoff_id,
            "status": self.status.value,
            "error_class": self.error_class,
            "error_code": self.error_code,
        }


@runtime_checkable
class DocumentKnowledgeBatchPipelinePort(Protocol):
    def submit_batch(self, handoff: DocumentKnowledgeBatchHandoff) -> Any: ...


@dataclass(frozen=True, slots=True)
class DocumentLearningCorpusResult:
    schema: str
    ingestion: DocumentBatchResult
    exercise_corpus: DocumentExerciseCorpus
    provenance_quality: DocumentProvenanceQualityReport
    review_feed: DocumentReviewContentFeed
    knowledge_handoff: DocumentKnowledgeBatchHandoff
    submission_receipt: DocumentKnowledgeBatchSubmissionReceipt
    checkpoint_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "ingestion": self.ingestion.to_dict(),
            "exercise_corpus": self.exercise_corpus.to_dict(),
            "provenance_quality": self.provenance_quality.to_dict(),
            "review_feed": self.review_feed.to_dict(),
            "knowledge_handoff": self.knowledge_handoff.to_dict(),
            "submission_receipt": self.submission_receipt.to_dict(),
            "checkpoint_hash": self.checkpoint_hash,
        }
        payload["runtime_result_hash"] = sha256_json(payload)
        return payload


class DocumentLearningCorpusRuntime:
    """Compose ingestion, corpus, quality, review, and one batch handoff."""

    def __init__(
        self,
        application: DocumentIngestionApplication,
        *,
        checkpoint_store: DocumentCheckpointStore | None = None,
        knowledge_pipeline_port: DocumentKnowledgeBatchPipelinePort | None = None,
        corpus_builder: DocumentExerciseCorpusBuilder | None = None,
        provenance_reporter: DocumentProvenanceQualityReporter | None = None,
        review_feed_builder: DocumentReviewContentFeedBuilder | None = None,
    ) -> None:
        self.application = application
        self.checkpoint_store = checkpoint_store
        self.knowledge_pipeline_port = knowledge_pipeline_port
        self.corpus_builder = corpus_builder or DocumentExerciseCorpusBuilder()
        self.provenance_reporter = (
            provenance_reporter or DocumentProvenanceQualityReporter()
        )
        self.review_feed_builder = (
            review_feed_builder or DocumentReviewContentFeedBuilder()
        )

    def run(
        self,
        request: DocumentBatchRequest,
        *,
        concept_hints_by_candidate: Mapping[str, Sequence[str]] | None = None,
        progress_sink: ProgressSink | None = None,
        should_stop: StopPredicate | None = None,
    ) -> DocumentLearningCorpusResult:
        checkpoint = (
            self.checkpoint_store.load()
            if self.checkpoint_store is not None
            else DocumentIngestionCheckpoint()
        )
        ingestion = self.application.ingest(
            request,
            checkpoint=checkpoint,
            progress_sink=progress_sink,
            should_stop=should_stop,
        )
        checkpoint_hash = (
            self.checkpoint_store.save(checkpoint)
            if self.checkpoint_store is not None
            else None
        )
        corpus = self.corpus_builder.build(
            ingestion.exercise_candidates,
            concept_hints_by_candidate=concept_hints_by_candidate,
        )
        source_formats = self._source_formats(ingestion)
        quality = self.provenance_reporter.build(
            ingestion.bundles,
            source_formats=source_formats,
            corpus=corpus,
        )
        review_feed = self.review_feed_builder.build(corpus)
        knowledge_handoff = self._knowledge_handoff(
            ingestion,
            corpus=corpus,
            quality=quality,
            review_feed=review_feed,
        )
        submission_receipt = self._submit_batch(knowledge_handoff)
        return DocumentLearningCorpusResult(
            schema=DOCUMENT_LEARNING_RUNTIME_SCHEMA,
            ingestion=ingestion,
            exercise_corpus=corpus,
            provenance_quality=quality,
            review_feed=review_feed,
            knowledge_handoff=knowledge_handoff,
            submission_receipt=submission_receipt,
            checkpoint_hash=checkpoint_hash,
        )

    @staticmethod
    def _source_formats(result: DocumentBatchResult) -> dict[str, DocumentFormat]:
        return {
            receipt.source_file: receipt.conversion_identity.source_type
            for receipt in result.receipts
            if receipt.conversion_identity is not None
        }

    @staticmethod
    def _knowledge_handoff(
        result: DocumentBatchResult,
        *,
        corpus: DocumentExerciseCorpus,
        quality: DocumentProvenanceQualityReport,
        review_feed: DocumentReviewContentFeed,
    ) -> DocumentKnowledgeBatchHandoff:
        completed_statuses = {
            DocumentIngestionStatus.SUCCESS,
            DocumentIngestionStatus.LIMITED,
            DocumentIngestionStatus.SKIPPED_DUPLICATE,
        }
        completed_count = sum(
            receipt.status in completed_statuses for receipt in result.receipts
        )
        identity = {
            "schema": DOCUMENT_KNOWLEDGE_BATCH_SCHEMA,
            "request_id": result.request_id,
            "corpus_id": corpus.corpus_id,
            "review_feed_id": review_feed.feed_id,
            "quality_report_id": quality.report_id,
            "document_handoff_ids": [
                handoff.handoff_id for handoff in result.handoffs
            ],
        }
        return DocumentKnowledgeBatchHandoff(
            schema=DOCUMENT_KNOWLEDGE_BATCH_SCHEMA,
            handoff_id=f"docknowledgebatch_{sha256_json(identity)[:40]}",
            request_id=result.request_id,
            corpus_id=corpus.corpus_id,
            review_feed_id=review_feed.feed_id,
            provenance_quality_report_id=quality.report_id,
            document_handoffs=result.handoffs,
            completed_document_count=completed_count,
            failed_document_count=len(result.receipts) - completed_count,
        )

    def _submit_batch(
        self,
        handoff: DocumentKnowledgeBatchHandoff,
    ) -> DocumentKnowledgeBatchSubmissionReceipt:
        error_class: str | None = None
        error_code: str | None = None
        if self.knowledge_pipeline_port is None:
            status = DocumentBatchSubmissionStatus.NOT_CONFIGURED
        else:
            try:
                self.knowledge_pipeline_port.submit_batch(handoff)
                status = DocumentBatchSubmissionStatus.SUCCESS
            except Exception as error:
                status = DocumentBatchSubmissionStatus.FAILED
                error_class = type(error).__name__
                error_code = "DOCUMENT_KNOWLEDGE_BATCH_HANDOFF_FAILED"
        identity = {
            "handoff_id": handoff.handoff_id,
            "status": status.value,
            "error_code": error_code,
        }
        return DocumentKnowledgeBatchSubmissionReceipt(
            receipt_id=f"docknowledgereceipt_{sha256_json(identity)[:40]}",
            handoff_id=handoff.handoff_id,
            status=status,
            error_class=error_class,
            error_code=error_code,
        )


__all__ = [
    "DOCUMENT_KNOWLEDGE_BATCH_SCHEMA",
    "DOCUMENT_LEARNING_RUNTIME_SCHEMA",
    "DocumentBatchSubmissionStatus",
    "DocumentKnowledgeBatchHandoff",
    "DocumentKnowledgeBatchPipelinePort",
    "DocumentKnowledgeBatchSubmissionReceipt",
    "DocumentLearningCorpusResult",
    "DocumentLearningCorpusRuntime",
]
