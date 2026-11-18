from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
import threading

import httpx

from refactor.learning.app.rag import RagService
from refactor.learning.sync_transport import SyncTransport
from refactor.runtime_logging import DiagnosticFormatter


def _rows(caplog):
    return [json.loads(DiagnosticFormatter().format(record))
            for record in caplog.records
            if getattr(record, "mf_event", None) in {"rag_timing", "sync", "research"}]


def test_logs455_preparation_failure_has_safe_diagnostics(caplog):
    caplog.set_level(logging.INFO, logger="refactor.runtime")
    service = object.__new__(RagService)
    service._worker_local = SimpleNamespace(batch_index=3, request_id="job-455",
                                            preparation_started=0.0)
    error = type("UploadError", (), {"code": "RAG_HTTP", "http_status": 503})()
    service._log_preparation_failure("abcdef12-document-secret", error)
    row = next(row for row in _rows(caplog) if row.get("stage") == "prepare")
    assert row["code"] == "RAG_HTTP"
    assert row["http_status"] == 503
    assert row["batch"] == 3
    assert row["document_id"] == "abcdef12"
    assert row["request_id"] == "job-455"
    assert "document-secret" not in json.dumps(row)


def test_logs455_import_first_pass_failure_is_logged_without_filename(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="refactor.runtime")
    service = object.__new__(RagService)
    service._closed = False
    service._lock = threading.RLock()
    missing = tmp_path / "student-private-source.txt"
    service._picked_paths = {str(missing)}
    result = service.import_files([str(missing)])
    assert result["documents"] == []
    assert result["errors"][0]["code"] == "FILE_UNAVAILABLE"
    row = next(row for row in _rows(caplog) if row.get("stage") == "extract")
    assert row["code"] == "FILE_UNAVAILABLE"
    assert row["batch"] == 1
    encoded = json.dumps(row)
    assert "student-private-source.txt" not in encoded


def test_logs455_sync_503_keeps_server_code_and_safe_correlation(caplog):
    caplog.set_level(logging.INFO, logger="refactor.runtime")
    transport = object.__new__(SyncTransport)
    response = httpx.Response(
        503,
        headers={"X-Request-ID": "server-455"},
        json={"error": {"code": "SYNC_BATCH_LIMIT", "detail": "private body"}},
        request=httpx.Request("POST", "https://example.test/sync"),
    )
    transport._log_response("/api/enterprise/sync/v1/entities/bulk", response,
                            state="BATCH_HTTP_RESPONSE", client_request_id="client-455",
                            operation_id="batch-455")
    row = next(row for row in _rows(caplog) if row.get("state") == "BATCH_HTTP_RESPONSE")
    assert row["stage"] == "sync_upload"
    assert row["code"] == "SYNC_BATCH_LIMIT"
    assert row["http_status"] == 503
    assert row["client_request_id"] == "client-455"
    assert row["response_request_id"] == "server-455"
    assert "private body" not in json.dumps(row)


def test_logs455_formatter_drops_query_url_and_free_text():
    import logging

    record = logging.LogRecord("refactor.runtime", logging.INFO, __file__, 1,
                               "ignored", (), None)
    record.mf_event = "research"
    record.mf_fields = {
        "stage": "prepare",
        "url": "https://example.test/course?token=secret#answer",
        "reason": "HTTP 503 with private detail",
    }
    row = json.loads(DiagnosticFormatter().format(record))
    assert "url" not in row
    assert "secret" not in json.dumps(row)

