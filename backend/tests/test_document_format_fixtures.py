from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.sources.document import DocumentFormat, DocumentSourcePolicy
from app.sources.document.ingestion import (
    DoclingCompileMode,
    DoclingExecutionEnvironment,
    DocumentBatchRequest,
    DocumentBatchSubmissionStatus,
    DocumentCheckpointStore,
    DocumentIngestionApplication,
    DocumentIngestionStatus,
    DocumentLearningCorpusRuntime,
    DocumentWorkerState,
    WindowsDoclingCompatibilityPolicy,
)


FIXTURES = Path(__file__).parent / "fixtures" / "document_learning"
FORMAT_NAMES = (
    "learning-corpus.docx",
    "learning-corpus.pptx",
    "learning-corpus.html",
    "learning-corpus.md",
)


class _BatchPort:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def submit_batch(self, handoff: Any) -> str:
        self.calls.append(handoff)
        return handoff.handoff_id


def test_real_docx_pptx_html_markdown_runtime_preserves_format_provenance(
    tmp_path: Path,
) -> None:
    fixture_root = FIXTURES.resolve()
    worker_root = (tmp_path / "workers").resolve()
    port = _BatchPort()
    application = DocumentIngestionApplication(
        DocumentSourcePolicy(input_root=fixture_root),
        execution_environment=DoclingExecutionEnvironment(
            python_executable=Path(sys.executable).resolve(),
            scratch_root=worker_root,
            timeout_seconds=90,
            startup_timeout_seconds=30,
            shutdown_timeout_seconds=5,
            worker_count=2,
        ),
        compatibility_policy=WindowsDoclingCompatibilityPolicy(
            platform_name="win32",
            isolate_all_document_formats=True,
            msvc_path=None,
        ),
    )
    runtime = DocumentLearningCorpusRuntime(
        application,
        checkpoint_store=DocumentCheckpointStore(
            (tmp_path / "checkpoint.json").resolve()
        ),
        knowledge_pipeline_port=port,
    )
    request = DocumentBatchRequest(
        request_id="w6-four-format-runtime",
        selected_documents=tuple(fixture_root / name for name in FORMAT_NAMES),
        conversion_config_identity="w6-four-format-v1",
        conversion_batch_size=4,
    )

    result = runtime.run(request)

    assert all(
        receipt.status is DocumentIngestionStatus.LIMITED
        for receipt in result.ingestion.receipts
    )
    assert len(result.ingestion.bundles) == 4
    assert len(result.ingestion.handoffs) == 4
    assert len(result.knowledge_handoff.document_handoffs) == 4
    assert result.knowledge_handoff.completed_document_count == 4
    assert result.knowledge_handoff.failed_document_count == 0
    assert result.submission_receipt.status is DocumentBatchSubmissionStatus.SUCCESS
    assert len(port.calls) == 1

    platform_receipts = result.ingestion.platform_receipts
    assert len(platform_receipts) == 2
    assert all(
        receipt.state is DocumentWorkerState.STOPPED
        and receipt.startup_succeeded
        and receipt.clean_shutdown
        and receipt.compile_mode is DoclingCompileMode.NOT_APPLICABLE
        for receipt in platform_receipts
    )
    assert sum(receipt.jobs_completed for receipt in platform_receipts) == 4
    assert sum(receipt.jobs_failed for receipt in platform_receipts) == 0
    assert sum(receipt.network_attempts for receipt in platform_receipts) == 0
    assert sum(receipt.model_download_attempts for receipt in platform_receipts) == 0
    assert not list(worker_root.glob("mentorflow-docling-worker-*"))

    quality_by_format = {
        source.source_format: source for source in result.provenance_quality.sources
    }
    assert result.provenance_quality.exact_page_count == 0
    assert quality_by_format[DocumentFormat.DOCX].section_only_count > 0
    assert quality_by_format[DocumentFormat.DOCX].exact_page_count == 0
    assert quality_by_format[DocumentFormat.PPTX].slide_count > 0
    assert quality_by_format[DocumentFormat.PPTX].exact_page_count == 0
    assert quality_by_format[DocumentFormat.HTML].section_only_count > 0
    assert quality_by_format[DocumentFormat.MARKDOWN].section_only_count > 0
    assert result.provenance_quality.table_source_count == 3

    questions = {entry.question for entry in result.exercise_corpus.entries}
    assert "What preserves lineage?" in questions
    assert "Which item needs verification?" in questions
    assert "Question: Which token appears on this slide?" in questions
    assert "What binds a source item?" in questions
    assert "What preserves source order?" in questions
    assert "Question" not in questions
    assert "Exercises" not in questions
    assert (
        "Question and answer content with exact slide provenance." not in questions
    )
    assert result.exercise_corpus.unanswered_count == 4
    assert result.checkpoint_hash is not None
