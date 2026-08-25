"""Resumable Docling batch ingestion and KnowledgePipeline handoff contracts."""

from .application import (
    DoclingBatchConverterProtocol,
    DocumentBatchIngestion,
    DocumentIngestionApplication,
)
from .benchmark import (
    SYNTHETIC_BENCHMARK_DISCLAIMER,
    SyntheticBatchBenchmarkReport,
    SyntheticBatchBenchmarkSample,
    run_synthetic_batch_benchmark,
)
from .bridge import DocumentKnowledgePipelinePort, build_document_pipeline_handoff
from .contracts import (
    DOCUMENT_HANDOFF_SCHEMA,
    DOCUMENT_INGESTION_SCHEMA,
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
)
from .provenance import DocumentProvenanceGate, DocumentProvenanceGateError


__all__ = [
    "DOCUMENT_HANDOFF_SCHEMA",
    "DOCUMENT_INGESTION_SCHEMA",
    "SYNTHETIC_BENCHMARK_DISCLAIMER",
    "DoclingBatchConverterProtocol",
    "DocumentBatchIngestion",
    "DocumentBatchRequest",
    "DocumentBatchResult",
    "DocumentBatchStatus",
    "DocumentCheckpointEntry",
    "DocumentConversionIdentity",
    "DocumentFingerprint",
    "DocumentIngestionApplication",
    "DocumentIngestionCheckpoint",
    "DocumentIngestionFailure",
    "DocumentIngestionMetrics",
    "DocumentIngestionProgress",
    "DocumentIngestionReceipt",
    "DocumentIngestionStage",
    "DocumentIngestionStatus",
    "DocumentKnowledgePipelinePort",
    "DocumentPipelineHandoff",
    "DocumentProvenanceGate",
    "DocumentProvenanceGateError",
    "SyntheticBatchBenchmarkReport",
    "SyntheticBatchBenchmarkSample",
    "build_document_pipeline_handoff",
    "run_synthetic_batch_benchmark",
]
