"""MF-03 regression coverage for chunk-count upload batching."""
import json
from itertools import accumulate

from refactor.learning.app import rag
from tests.refactor.rag155.test_rag155_backend import FakeRagServer, service


def _posts(server):
    return [request for request in server.requests
            if request.method == "POST" and request.url.path.endswith("/chunks")]


def _row(instance, count, text="tiny"):
    document_id = "266-" + "a" * 60
    instance.local.save_document(document_id, "266 测试资料", "266.txt", [
        {"text": f"{text}-{index}", "page": index + 1, "section": "章节"}
        for index in range(count)])
    return instance.local.document(document_id)


def test_rag266_1006_small_chunks_batch_by_count_and_report_each_receipt(tmp_path):
    server = FakeRagServer()
    instance, _, _ = service(tmp_path, server)
    try:
        row = _row(instance, 1006)
        progress = []
        result = instance._send_batches(row, on_progress=progress.append)
        requests = _posts(server)
        sizes = [len(json.loads(request.content)["chunks"]) for request in requests]
        assert len(sizes) > 1
        assert all(0 < size <= rag.BATCH_CHUNKS for size in sizes)
        assert sum(sizes) == 1006
        # Successful receipts advance monotonically at each request boundary.
        cumulative = list(accumulate(sizes))
        assert [event["uploaded_chunks"] for event in progress[:len(sizes)]] == cumulative
        assert cumulative[-1] == 1006
        assert len(progress) in {len(sizes), len(sizes) + 1}
        if len(progress) > len(sizes):
            assert progress[-1]["uploaded_chunks"] == 1006
        assert sum(len(request.content) for request in requests) < 8 * 1024 * 1024
        assert result["status"] == "ready"
    finally:
        instance.close()


def test_rag266_byte_limit_and_413_still_split_batches(tmp_path, monkeypatch):
    import httpx

    server = FakeRagServer()
    attempted = []

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/chunks"):
            attempted.append(request)
            if len(json.loads(request.content)["chunks"]) > 2:
                return httpx.Response(413)
        return server(request)

    instance, _, _ = service(tmp_path, handler)
    try:
        monkeypatch.setattr(rag, "BATCH_CHUNKS", 64)
        monkeypatch.setattr(rag, "BATCH_BYTES", 700)
        row = _row(instance, 8, text="字" * 30)
        assert instance._send_batches(row)["status"] == "ready"
        assert all(len(request.content) <= 700 for request in attempted)
        sizes = [len(json.loads(request.content)["chunks"]) for request in attempted]
        assert sizes[0] < 8  # The wire-size ceiling split the original payload.
        assert any(size > 2 for size in sizes)
        assert any(size == 2 for size in sizes)  # 413 recursively split the rejected part.
        successful = [chunk["seq"] for request in attempted[1:]
                      if len(json.loads(request.content)["chunks"]) <= 2
                      for chunk in json.loads(request.content)["chunks"]]
        assert sorted(successful) == list(range(8))
    finally:
        instance.close()


def test_rag266_repair_respects_chunk_count_limit(tmp_path, monkeypatch):
    server = FakeRagServer()
    instance, _, _ = service(tmp_path, server)
    try:
        row = _row(instance, 137)
        server.documents[row["id"]] = {"title": row["title"], "source": row["source"],
            "total": 137, "chunks": {}, "status": "ready"}
        monkeypatch.setattr(rag, "BATCH_CHUNKS", 64)
        monkeypatch.setattr(rag, "BATCH_BYTES", 8 * 1024 * 1024)
        progress = []
        assert instance._send_repair_batches(row, on_progress=progress.append)["status"] == "ready"
        requests = _posts(server)
        sizes = [len(json.loads(request.content)["chunks"]) for request in requests]
        assert sizes == [64, 64, 9]
        assert [event["uploaded_chunks"] for event in progress[:3]] == [64, 128, 137]
        assert len(progress) in {3, 4}
        if len(progress) == 4:
            assert progress[-1]["uploaded_chunks"] == 137
        assert all(json.loads(request.content)["repair_existing"] is True for request in requests)
    finally:
        instance.close()


def test_rag266_retry_of_legacy_ready_document_keeps_id_and_session_history(tmp_path):
    server = FakeRagServer()
    instance, _, _ = service(tmp_path, server)
    try:
        row = _row(instance, 1006)
        # Model the existing local record created after the old single request
        # was accepted; the server still has the original ID and all chunks.
        server.documents[row["id"]] = {"title": row["title"], "source": row["source"],
            "total": 1006, "chunks": {seq: {"seq": seq, "text": chunk["text"],
            "page": chunk["page"], "section": chunk["section"]}
            for seq, chunk in enumerate(row["chunks"])}, "status": "ready"}
        instance.local.set_uploaded_content(row["id"], row["current_content_sha256"])
        instance.local.set_remote_state(row["id"], row["id"], "ready", 1006, 1006)
        session = instance.local.create_session([row["id"]], "已有会话")
        instance.local.append_turn(session["id"], "旧问题", "旧回答",
                                   [{"text": "引用原文", "document_id": row["id"]}], False)
        history_before = instance.local.get_session(session["id"])
        before = instance.local.list_sessions()

        instance.prepare([row["id"]])
        result = instance.wait_preparation([row["id"]])

        assert result["documents"][0]["status"] == "ready"
        assert instance.local.document(row["id"])["remote_upload_id"] == row["id"]
        assert _posts(server) == []
        assert instance.local.list_sessions() == before
        assert instance.local.get_session(session["id"]) == history_before
    finally:
        instance.close()


def test_rag266_resume_partial_upload_reuses_id_replays_from_first_batch_idempotently(tmp_path, monkeypatch):
    from tests.refactor.rag263.test_rag263_background import eventually

    server = FakeRagServer()
    instance, _, _ = service(tmp_path, server)
    try:
        row = _row(instance, 1006)
        # Simulate the previous one-request upload being interrupted after a
        # prefix. The server keeps those seqs and accepts their replay safely.
        prefix = {seq: {"seq": seq, **chunk} for seq, chunk in enumerate(row["chunks"][:11])}
        server.documents[row["id"]] = {"title": row["title"], "source": row["source"],
            "total": 1006, "chunks": prefix, "status": "uploading"}
        instance.local.set_remote_state(row["id"], row["id"], "uploading", 11, 1006)
        session = instance.local.create_session([row["id"]], "部分上传时的会话")
        instance.local.append_turn(session["id"], "旧问题", "旧回答", [], False)
        history_before = instance.local.get_session(session["id"])
        monkeypatch.setattr(rag, "BATCH_CHUNKS", 64)

        instance.prepare([row["id"]])
        eventually(lambda: instance.preparation([row["id"]])["jobs"][0]["state"] == "ready")

        requests = _posts(server)
        assert requests
        assert all(request.url.path.endswith(f"/documents/{row['id']}/chunks") for request in requests)
        assert json.loads(requests[0].content)["chunks"][0]["seq"] == 0
        complete = server.documents[row["id"]]
        assert complete["status"] == "ready"
        assert len(complete["chunks"]) == 1006
        assert sorted(complete["chunks"]) == list(range(1006))
        assert instance.local.get_session(session["id"]) == history_before
    finally:
        instance.close()


def test_rag266_remote_ready_without_old_receipt_is_independent_of_batch_limit(tmp_path, monkeypatch):
    outcomes = []
    batches = []
    for label, limit in (("old", 2000), ("new", 64)):
        server = FakeRagServer()
        instance, _, _ = service(tmp_path / label, server)
        try:
            row = _row(instance, 1006)
            # The server accepted the former one-shot body, while this client's
            # local record has no success receipt or uploaded-content marker.
            server.documents[row["id"]] = {"title": row["title"], "source": row["source"],
                "total": 1006, "chunks": {seq: {"seq": seq, **chunk}
                for seq, chunk in enumerate(row["chunks"])}, "status": "ready"}
            session = instance.local.create_session([row["id"]], "远端已就绪的旧会话")
            instance.local.append_turn(session["id"], "历史问题", "历史回答",
                                       [{"text": "历史引用", "id": "citation-266"}], False)
            old_history = instance.local.get_session(session["id"])
            monkeypatch.setattr(rag, "BATCH_CHUNKS", limit)

            result = instance.ingest([row["id"]])

            assert result["documents"][0]["status"] == "ready"
            final_row = instance.local.document(row["id"])
            assert final_row["canonical_id"] == row["id"]
            assert set(server.documents) == {row["id"]}
            messages = instance.local.get_session(session["id"])["messages"]
            outcomes.append((final_row["canonical_id"],
                             final_row["remote_upload_id"],
                             [{key: message[key] for key in ("role", "content", "passages", "supplement")}
                              for message in messages]))
            batches.append([len(json.loads(request.content)["chunks"]) for request in _posts(server)])
            assert instance.local.get_session(session["id"]) == old_history
        finally:
            instance.close()

    assert outcomes[0][:2] == outcomes[1][:2]
    assert outcomes[0][2] == outcomes[1][2]
    assert batches == [[1006], [64] * 15 + [46]]
