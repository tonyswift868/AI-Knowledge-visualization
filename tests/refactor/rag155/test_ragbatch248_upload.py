"""248: wire-size batching, 413 subdivision, and upload repair contracts."""
import json
from hashlib import sha256
import threading

import httpx
import pytest

from refactor.learning.app import rag
from refactor.learning.app.bridge import BridgeError
from tests.refactor.rag155.test_rag155_backend import FakeCourseStore, FakeRagServer, service


def _post_chunks(fake):
    return [request for request in fake.requests
            if request.method == "POST" and request.url.path.endswith("/chunks")]


def _saved_document(instance, *, count=8, text="内容段落"):
    document_id = "248-" + "a" * 60
    instance.local.save_document(document_id, "资料标题中文", "来源\n名称.txt", [
        {"text": f"{text}-{index}", "page": index + 1, "section": "章节\t一"}
        for index in range(count)])
    return instance.local.document(document_id)


def test_ragbatch248_batches_by_actual_http_json_body_size(tmp_path, monkeypatch):
    fake = FakeRagServer()
    instance, _, _ = service(tmp_path, fake)
    try:
        row = _saved_document(instance, count=9,
                              text='中文"反斜线\\换行\n制表\t控制\u0001')
        monkeypatch.setattr(rag, "BATCH_BYTES", 900)
        result = instance._send_batches(row)
        requests = _post_chunks(fake)
        assert len(requests) > 1
        assert all(len(request.content) <= 900 for request in requests)
        payloads = [json.loads(request.content) for request in requests]
        sent = [chunk for payload in payloads for chunk in payload["chunks"]]
        assert [chunk["seq"] for chunk in sent] == list(range(9))
        assert any("中文" in request.content.decode("utf-8") for request in requests)
        assert result["status"] == "ready"
    finally:
        instance.close()


def test_ragbatch248_production_limit_leaves_margin_below_nine_mib():
    assert rag.BATCH_BYTES == 8 * 1024 * 1024


def test_ragbatch248_413_splits_current_batch_and_uploads_each_leaf_once(tmp_path, monkeypatch):
    fake = FakeRagServer()
    requests = []

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/chunks"):
            requests.append(request)
            payload = json.loads(request.content)
            if len(payload["chunks"]) > 2:
                return httpx.Response(413)
        return fake(request)

    instance, _, _ = service(tmp_path, handler)
    try:
        row = _saved_document(instance, count=8, text="x" * 30)
        monkeypatch.setattr(rag, "BATCH_BYTES", 50_000)
        result = instance._send_batches(row)
        assert result["status"] == "ready"
        # One initial rejected batch followed by four successful leaves.
        assert [len(json.loads(request.content)["chunks"]) for request in requests] == [8, 4, 2, 2, 4, 2, 2]
        accepted = [chunk["seq"] for request in requests
                    if len(json.loads(request.content)["chunks"]) <= 2
                    and request is not requests[0]
                    for chunk in json.loads(request.content)["chunks"]]
        assert sorted(accepted) == list(range(8))
        assert len(accepted) == len(set(accepted))
    finally:
        instance.close()


def test_ragbatch248_successful_prior_batch_is_not_resent_after_later_413(tmp_path, monkeypatch):
    fake = FakeRagServer()
    attempted = []
    calls = 0

    def handler(request):
        nonlocal calls
        if request.method == "POST" and request.url.path.endswith("/chunks"):
            calls += 1
            payload = json.loads(request.content)
            attempted.append([chunk["seq"] for chunk in payload["chunks"]])
            if calls == 2:
                return httpx.Response(413)
        return fake(request)

    instance, _, _ = service(tmp_path, handler)
    try:
        row = _saved_document(instance, count=4, text="q" * 220)
        monkeypatch.setattr(rag, "BATCH_BYTES", 700)
        instance._send_batches(row)
        # The first accepted request remains exactly once; only the rejected
        # second batch is divided and retried.
        successful_prefix = attempted[0]
        rejected_later_batch = attempted[1]
        assert len(successful_prefix) >= 1
        assert len(rejected_later_batch) >= 2
        assert not any(seq in successful_prefix for group in attempted[2:] for seq in group)
        assert sorted(seq for group in attempted[2:] for seq in group) == sorted(rejected_later_batch)
    finally:
        instance.close()


@pytest.mark.parametrize("status", [404, 500])
def test_ragbatch248_non_413_failure_is_not_split(tmp_path, monkeypatch, status):
    fake = FakeRagServer()
    attempted = []

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/chunks"):
            attempted.append(request)
            return httpx.Response(status)
        return fake(request)

    instance, _, _ = service(tmp_path, handler)
    try:
        row = _saved_document(instance, count=4)
        monkeypatch.setattr(rag, "BATCH_BYTES", 50_000)
        with pytest.raises(BridgeError) as error:
            instance._send_batches(row)
        assert error.value.code == "RAG_HTTP"
        assert len(attempted) == 1
        assert len(json.loads(attempted[0].content)["chunks"]) == 4
    finally:
        instance.close()


def test_ragbatch248_single_chunk_413_identifies_segment(tmp_path, monkeypatch):
    fake = FakeRagServer()
    attempted = []

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/chunks"):
            attempted.append(request)
            return httpx.Response(413)
        return fake(request)

    instance, _, _ = service(tmp_path, handler)
    try:
        row = _saved_document(instance, count=1, text="oversized" * 30)
        monkeypatch.setattr(rag, "BATCH_BYTES", 50_000)
        with pytest.raises(BridgeError) as error:
            instance._send_batches(row)
        assert error.value.code == "RAG_CHUNK_TOO_LARGE"
        assert "第 1 段" in error.value.message
        assert "资料太大" in error.value.message
        assert len(attempted) == 1
    finally:
        instance.close()


def test_ragbatch248_local_single_chunk_over_limit_fails_before_http(tmp_path, monkeypatch):
    fake = FakeRagServer()
    instance, _, _ = service(tmp_path, fake)
    try:
        row = _saved_document(instance, count=1, text="large" * 80)
        monkeypatch.setattr(rag, "BATCH_BYTES", 300)
        with pytest.raises(BridgeError) as error:
            instance._send_batches(row)
        assert error.value.code == "RAG_CHUNK_TOO_LARGE"
        assert "第 1 段" in error.value.message
        assert _post_chunks(fake) == []
    finally:
        instance.close()


def test_ragbatch248_repair_uses_wire_size_and_splits_413(tmp_path, monkeypatch):
    fake = FakeRagServer()
    attempted = []

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/chunks"):
            attempted.append(request)
            if len(json.loads(request.content)["chunks"]) > 2:
                return httpx.Response(413)
        return fake(request)

    instance, _, _ = service(tmp_path, handler)
    try:
        row = _saved_document(instance, count=8,
                              text='修复中文"\n控制\u0002' * 8)
        fake.documents[row["id"]] = {"title": row["title"], "source": row["source"],
            "total": 8, "chunks": {}, "status": "ready"}
        monkeypatch.setattr(rag, "BATCH_BYTES", 50_000)
        result = instance._send_repair_batches(row)
        assert result["status"] == "ready"
        assert all(len(request.content) <= 50_000 for request in attempted)
        assert [len(json.loads(request.content)["chunks"]) for request in attempted] == [8, 4, 2, 2, 4, 2, 2]
    finally:
        instance.close()


def test_ragbatch248_repair_batches_measure_manifest_metadata_in_wire_body(tmp_path, monkeypatch):
    fake = FakeRagServer()
    instance, _, _ = service(tmp_path, fake)
    try:
        row = _saved_document(instance, count=8, text="补传中文" * 8)
        fake.documents[row["id"]] = {"title": row["title"], "source": row["source"],
            "total": 8, "chunks": {}, "status": "ready"}
        monkeypatch.setattr(rag, "BATCH_BYTES", 1200)
        result = instance._send_repair_batches(row)
        requests = _post_chunks(fake)
        assert result["status"] == "ready"
        assert len(requests) > 1
        assert all(len(request.content) <= 1200 for request in requests)
        bodies = [json.loads(request.content) for request in requests]
        assert all(body["repair_existing"] is True for body in bodies)
        assert all(len(body["repair_manifest"]) == 8 for body in bodies)
        sent = [chunk["seq"] for body in bodies for chunk in body["chunks"]]
        assert sent == list(range(8))
    finally:
        instance.close()


def _remote_repair_fixture(tmp_path, handler, server, *, with_store=False):
    source_id = "248-source"
    chunks = [{"id": f"chunk-{seq}", "source_id": source_id, "position": seq,
               "text": f"修复内容-{seq}", "page_start": seq + 1, "section": "章节"}
              for seq in range(4)]
    body_digest = sha256("\n\n".join(row["text"] for row in chunks).encode()).hexdigest()
    store = FakeCourseStore(tmp_path / "account", plans=[{"id": "plan-248", "title": "计划"}],
        sources={"plan-248": [{"id": source_id, "adopted": True, "sha256": body_digest,
                                "title": "资料248", "filename": "248.txt", "chunk_count": 4}]},
        chunks={"plan-248": chunks}) if with_store else None
    instance, _, _ = service(tmp_path, handler, store=store)
    doc_id = body_digest
    if with_store:
        instance._course_documents()
    else:
        instance.local.save_document(doc_id, "资料248", "248.txt", [
            {"text": row["text"], "page": row["page_start"], "section": row["section"]}
            for row in chunks])
    server.documents[doc_id] = {"title": "资料248", "source": "248.txt", "total": 1,
        "chunks": {0: {"seq": 0, "text": chunks[0]["text"], "page": 1, "section": "章节"}},
        "status": "ready"}
    return instance, doc_id


def test_ragbatch248_repair_capacity_failure_keeps_partial_success_without_fallback(tmp_path, monkeypatch):
    fake = FakeRagServer()
    attempted = []

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/chunks"):
            payload = json.loads(request.content)
            attempted.append((request.url.path, payload))
            seqs = [chunk["seq"] for chunk in payload["chunks"]]
            if len(seqs) > 2 or any(seq >= 2 for seq in seqs):
                return httpx.Response(413)
        return fake(request)

    instance, doc_id = _remote_repair_fixture(tmp_path, handler, fake)
    try:
        monkeypatch.setattr(rag, "BATCH_BYTES", 50_000)
        outcome = instance.ingest([doc_id])
        saved = instance.local.document(doc_id)
        assert saved["remote_upload_id"] == doc_id
        assert saved["canonical_id"] == doc_id
        assert saved["uploaded_chunks"] == 2
        assert len(attempted) == 4  # full rejection, first half success, second half and singleton rejection
        assert all(path.endswith(f"/documents/{doc_id}/chunks") for path, _ in attempted)
        assert all(payload.get("repair_existing") is True for _, payload in attempted)
        assert outcome["documents"][0]["status"] == "failed"
        assert outcome["documents"][0]["uploaded_chunks"] == 2
    finally:
        instance.close()


def test_ragbatch248_sources_capacity_error_does_not_change_remote_id_or_reupload(tmp_path, monkeypatch):
    fake = FakeRagServer()
    attempted = []

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/chunks"):
            payload = json.loads(request.content)
            attempted.append((request.url.path, payload))
            seqs = [chunk["seq"] for chunk in payload["chunks"]]
            if len(seqs) > 2 or any(seq >= 2 for seq in seqs):
                return httpx.Response(413)
        return fake(request)

    instance, doc_id = _remote_repair_fixture(tmp_path, handler, fake, with_store=True)
    try:
        monkeypatch.setattr(rag, "BATCH_BYTES", 50_000)
        listed = instance.sources()
        saved = instance.local.document(doc_id)
        assert saved["remote_upload_id"] == doc_id
        assert saved["uploaded_chunks"] == 2
        assert len(attempted) == 4
        assert all(path.endswith(f"/documents/{doc_id}/chunks") for path, _ in attempted)
        assert all(payload.get("repair_existing") is True for _, payload in attempted)
        visible = next(item for item in listed["documents"] if item["id"] == doc_id)
        assert visible["status"] == "failed"
        assert visible["uploaded_chunks"] == 2
        assert "太大" in visible["error"]
    finally:
        instance.close()


def test_ragbatch248_cancellation_keeps_prior_receipt_and_does_not_send_next_batch(tmp_path, monkeypatch):
    fake = FakeRagServer()
    instance, _, _ = service(tmp_path, fake)
    cancelled = threading.Event()
    progress = []
    try:
        row = _saved_document(instance, count=4, text="z" * 220)
        monkeypatch.setattr(rag, "BATCH_BYTES", 700)

        def on_progress(item):
            progress.append(item)
            if len(progress) == 1:
                cancelled.set()

        with pytest.raises(BridgeError) as error:
            instance._send_batches(row, cancel=cancelled, on_progress=on_progress)
        assert error.value.code == "CANCELLED"
        assert len(_post_chunks(fake)) == 1
        saved = instance.local.document(row["id"])
        assert saved["uploaded_chunks"] > 0
        assert saved["status"] == "uploading"
    finally:
        instance.close()
