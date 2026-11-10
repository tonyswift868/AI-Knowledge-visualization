"""Offline contract tests for 351-MF02 RAG timing diagnostics.

The service is exercised with a fake retriever and a fake streaming gateway;
no provider, server, user data or network is touched.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
import time

import pytest

from refactor.learning.app import rag
from refactor.learning.connections.errors import ModelCancelled, ModelError
from refactor import runtime_logging


QUESTION = "QUESTION_SECRET_351"
ANSWER = "ANSWER_SECRET_351"
API_KEY = "API_KEY_SECRET_351"


class FakeLocal:
    def __init__(self):
        self.saved = []

    def get_session(self, _session_id):
        return {"document_ids": ["doc-1"], "messages": []}

    def document(self, _document_id):
        return {
            "document_id": "doc-1",
            "title": "fixture",
            "status": "ready",
            "remote_upload_id": "remote-1",
            "current_content_sha256": "same",
            "uploaded_content_sha256": "same",
        }

    def append_turn(self, *args, **kwargs):
        self.saved.append((args, kwargs))


class FakeGateway:
    def __init__(self, mode="success"):
        self.mode = mode
        self.request_ids = []

    def get_configuration(self):
        return {"model": "fake-model-351"}

    def complete(self, purpose, messages, *, system=None, on_text=None, cancel=None,
                 request_id=""):
        assert purpose == "rag_qa"
        assert messages and QUESTION in messages[-1]["content"]
        assert API_KEY not in json.dumps(messages)
        assert request_id
        self.request_ids.append(request_id)
        if self.mode == "cancel":
            raise ModelCancelled()
        if self.mode == "timeout":
            error = ModelError("MODEL_TIMEOUT", "timeout")
            error.request_id = request_id
            error.model = "fake-model-351"
            raise error
        if self.mode == "failure":
            error = ModelError("MODEL_CONNECTION_FAILED", "failure")
            error.request_id = request_id
            error.model = "fake-model-351"
            raise error
        time.sleep(0.004)
        if on_text:
            on_text("ANSWER_")
        time.sleep(0.003)
        if on_text:
            on_text("SECRET_351")
        return SimpleNamespace(
            text=ANSWER,
            model="fake-model-351",
            request_id=request_id,
        )


def _service(monkeypatch, mode="success", *, retrieval_error=None):
    service = object.__new__(rag.RagService)
    service.local = FakeLocal()
    service.gateway = FakeGateway(mode)
    service._course_documents = lambda: None

    def search(_documents, _query, _cancel=None):
        time.sleep(0.004)
        if retrieval_error:
            raise retrieval_error
        return {
            "passages": [
                {"document_id": "doc-1", "seq": 0, "page": 1,
                 "section": "one", "text": "PASSAGE_SECRET_1", "title": "fixture"},
                {"document_id": "doc-1", "seq": 1, "page": 2,
                 "section": "two", "text": "PASSAGE_SECRET_2", "title": "fixture"},
            ],
            "source_warnings": [],
        }

    monkeypatch.setattr(service, "_search_documents", search)
    return service


def _timing_records(caplog):
    records = []
    for record in caplog.records:
        fields = getattr(record, "mf_fields", None)
        if isinstance(fields, dict) and (
                "retrieval_ms" in fields or fields.get("event") == "rag_timing"):
            records.append(dict(fields))
            continue
        raw = record.getMessage()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and (
                "retrieval_ms" in payload or payload.get("event") == "rag_timing"):
            records.append(payload)
    return records


def _assert_safe_record(record, *, passage_count=None, model=None, complete=False):
    encoded = json.dumps(record, ensure_ascii=False)
    for secret in (QUESTION, ANSWER, API_KEY, "PASSAGE_SECRET_1", "PASSAGE_SECRET_2"):
        assert secret not in encoded
    assert record["purpose"] == "rag_qa"
    assert isinstance(record["request_id"], str) and record["request_id"]
    if passage_count is not None:
        assert record["passage_count"] == passage_count
    if model is not None:
        assert record["model"] == model
    for key in ("retrieval_ms", "total_ms"):
        assert isinstance(record[key], (int, float))
        assert record[key] >= 0
    if complete:
        for key in ("first_token_ms", "generation_ms"):
            assert isinstance(record[key], (int, float))
            assert record[key] >= 0
    else:
        assert record["first_token_ms"] is None
        assert record["generation_ms"] is None


def test_success_records_four_segments_and_request_id(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    service = _service(monkeypatch)
    result = service._send_active("session-1", QUESTION,
                                  document_ids=["doc-1"], cancel=None)

    assert result["session"]["document_ids"] == ["doc-1"]
    records = _timing_records(caplog)
    assert len(records) == 1
    record = records[0]
    _assert_safe_record(record, complete=True)
    assert record["state"] == "SUCCEEDED"
    assert record["phase"] == "COMPLETE"
    assert record.get("code") in (None, "")
    assert record["passage_count"] == 2
    assert record["model"] == "fake-model-351"
    assert record["first_token_ms"] > 0
    assert record["generation_ms"] > 0
    assert record["total_ms"] >= max(record["retrieval_ms"],
                                      record["first_token_ms"],
                                      record["generation_ms"])
    assert abs(record["total_ms"] - sum(record[key] for key in
                                         ("retrieval_ms", "first_token_ms", "generation_ms"))) < 30


def test_retrieval_failure_records_stop_stage_and_no_content(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    service = _service(monkeypatch, retrieval_error=rag.BridgeError("RAG_HTTP", "retrieval failed"))
    with pytest.raises(rag.BridgeError):
        service._send_active("session-1", QUESTION, document_ids=["doc-1"], cancel=None)
    records = _timing_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record["state"] == "FAILED"
    assert record["phase"] == "RETRIEVAL"
    assert record["request_id"]
    assert record["passage_count"] == 0
    assert "retrieval failed" not in json.dumps(record)
    _assert_safe_record(record, passage_count=0)


@pytest.mark.parametrize(
    ("mode", "state", "phases"),
    [
        ("cancel", "CANCELLED", {"MODEL_FIRST_BYTE", "MODEL_GENERATION"}),
        ("timeout", "TIMEOUT", {"MODEL_FIRST_BYTE", "MODEL_GENERATION"}),
        ("failure", "FAILED", {"MODEL_FIRST_BYTE", "MODEL_GENERATION"}),
    ],
)
def test_model_end_states_each_write_one_safe_record(monkeypatch, caplog, mode, state, phases):
    caplog.set_level(logging.INFO)
    service = _service(monkeypatch, mode)
    with pytest.raises((rag.BridgeError, ModelError)):
        service._send_active("session-1", QUESTION, document_ids=["doc-1"], cancel=None)
    records = _timing_records(caplog)
    assert len(records) == 1
    record = records[0]
    _assert_safe_record(record)
    assert record["state"] == state
    assert record["phase"] in phases


def test_timing_event_is_written_to_mentorflow_log_without_content(tmp_path, monkeypatch):
    runtime_logging.configure(tmp_path)
    service = _service(monkeypatch)
    service._send_active("session-1", QUESTION, document_ids=["doc-1"], cancel=None)
    log_path = tmp_path / "logs" / "mentorflow.log"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = [row for row in rows if row.get("event") == "rag_timing"]
    assert len(records) == 1
    record = records[0]
    _assert_safe_record(record, passage_count=2, model="fake-model-351", complete=True)
    assert record["state"] == "SUCCEEDED" and record["phase"] == "COMPLETE"
