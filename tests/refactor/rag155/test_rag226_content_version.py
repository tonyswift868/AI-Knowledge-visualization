"""226: same-count edits must select an immutable remote content version."""
from __future__ import annotations

import hashlib
import json
import threading

import httpx

from refactor.learning.app.rag import (API, RagLocalStore, RagService,
    _source_content_digest, _source_version_id)
from tests.refactor.rag155.test_rag155_backend import FakeRagServer, service


class StrictReadyRagServer(FakeRagServer):
    """Model Java's immutable ready ID contract; changed ready rows conflict."""
    def __call__(self, request):
        if request.method == "POST" and request.url.path.startswith(API + "/documents/") and request.url.path.endswith("/chunks"):
            requested = request.url.path.split("/documents/", 1)[1].split("/", 1)[0]
            canonical = self.aliases.get(requested, requested)
            existing = self.documents.get(canonical)
            if existing and existing["status"] == "ready":
                body = json.loads(request.content)
                incoming = {chunk["seq"]: chunk for chunk in body["chunks"]}
                if body.get("repair_existing") is not True:
                    current = existing["chunks"]
                    if (body["total_chunks"] != existing["total"] or any(
                            current.get(seq, {}).get("text") != chunk.get("text")
                            for seq, chunk in incoming.items())):
                        self.requests.append(request)
                        return httpx.Response(409, json={"error": "RAG_DOCUMENT_ID_CONFLICT"})
        return super().__call__(request)


def course_store(tmp_path, documents):
    """Build one synthetic account's adopted CourseStore-shaped rows."""
    from tests.refactor.rag155.test_rag155_backend import FakeCourseStore
    plans = []
    sources = {}
    chunks = {}
    for index, (document_id, text) in enumerate(documents):
        plan_id = f"plan-{index}"
        plans.append({"id": plan_id, "title": plan_id})
        sources[plan_id] = [{"id": f"source-{index}", "adopted": True,
            "chunk_count": 1, "sha256": document_id, "title": f"Doc {index}",
            "filename": f"doc-{index}.txt"}]
        chunks[plan_id] = [{"id": f"chunk-{index}", "source_id": f"source-{index}",
            "position": 0, "text": text, "page_start": 1, "section": ["正文"]}]
    return FakeCourseStore(tmp_path / "account", plans=plans, sources=sources, chunks=chunks)


def test_rag226_same_count_changed_text_retrieves_current_content(tmp_path):
    fake = StrictReadyRagServer()
    digest = hashlib.sha256(b"unchanged imported file bytes").hexdigest()
    store = course_store(tmp_path, [(digest, "初始解析正文。")])
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        original = instance.ingest([digest])["documents"][0]
        assert original["status"] == "ready"
        session = instance.create([digest])["session"]
        old_remote_id = instance.local.document(digest)["canonical_id"]
        assert fake.documents[old_remote_id]["chunks"][0]["text"] == "初始解析正文。"

        store._chunks["plan-0"][0]["text"] = "内容已按新解析结果改变，块数仍然是一。"
        expected_remote_id = _source_version_id(digest, _source_content_digest([
            {"text": store._chunks["plan-0"][0]["text"], "page": 1, "section": "正文"}]))
        fake.search_passages = [
            {"document_id": old_remote_id, "seq": 0, "text": "初始解析正文。", "page": 1, "section": "正文"},
            {"document_id": expected_remote_id, "seq": 0,
             "text": store._chunks["plan-0"][0]["text"], "page": 1, "section": "正文"},
        ]
        # Do not open/refresh the source list first: an existing chat must also
        # reconcile before querying, rather than using its stale cached mapping.
        instance.send(session["id"], "当前资料是什么？")
        current_remote_id = instance.local.document(digest)["canonical_id"]
        listed = instance.sources()
        active = next(row for row in listed["documents"] if row["id"] == digest)
        assert active["status"] == "ready"
        assert current_remote_id != old_remote_id
        assert fake.documents[current_remote_id]["chunks"][0]["text"] == store._chunks["plan-0"][0]["text"]

        assert fake.search_queries[-1]["document_ids"] == [current_remote_id]
        saved = instance.local.get_session(session["id"])["messages"][-1]
        assert [passage["text"] for passage in saved["passages"]] == [store._chunks["plan-0"][0]["text"]]
        assert fake.documents[old_remote_id]["chunks"][0]["text"] == "初始解析正文。"
        assert not any(request.method == "DELETE" for request in fake.requests)
    finally:
        instance.close()


def test_rag226_failed_replacement_keeps_old_mapping_but_blocks_stale_retrieval(tmp_path):
    fake = StrictReadyRagServer()
    digest = hashlib.sha256(b"same imported file").hexdigest()
    store = course_store(tmp_path, [(digest, "旧解析内容。")])
    instance, _, gateway = service(tmp_path, fake, store=store)
    try:
        instance.ingest([digest])
        session = instance.create([digest])["session"]
        old_id = instance.local.document(digest)["canonical_id"]
        store._chunks["plan-0"][0]["text"] = "新解析内容，仍然只有一个块。"

        fake.fail_upload_status = 503
        visible = next(row for row in instance.sources()["documents"] if row["id"] == digest)
        saved = instance.local.document(digest)
        assert visible["status"] == "failed"
        assert saved["canonical_id"] == old_id  # retain rollback/history mapping
        assert saved["uploaded_content_sha256"] != saved["current_content_sha256"]
        assert old_id in fake.documents and fake.documents[old_id]["status"] == "ready"

        fake.fail_upload_status = 503
        try:
            instance.send(session["id"], "新资料内容是什么？")
        except Exception as exc:
            assert getattr(exc, "code", None) == "RAG_DOCUMENT_NOT_READY"
        else:
            raise AssertionError("stale content must block model answer")
        assert gateway.calls == []
        assert fake.search_queries == []

        corrected = next(row for row in instance.sources()["documents"] if row["id"] == digest)
        assert corrected["status"] == "ready"
        assert instance.local.document(digest)["canonical_id"] != old_id
        assert old_id in fake.documents  # old remote version was not deleted
    finally:
        instance.close()


def test_rag226_content_fingerprint_includes_chunk_order_and_citations():
    first = [{"text": "same", "page": 1, "section": "A"},
             {"text": "body", "page": 2, "section": "B"}]
    assert _source_content_digest(first) != _source_content_digest(list(reversed(first)))
    assert _source_content_digest(first) != _source_content_digest([
        {"text": "same", "page": 9, "section": "A"}, first[1]])
    assert _source_content_digest(first) != _source_content_digest([
        first[0], {"text": "body", "page": 2, "section": "C"}])


def test_rag226_legacy_ready_row_is_versioned_once(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE rag_documents(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
            total_chunks INTEGER NOT NULL, uploaded_chunks INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'not_uploaded', error TEXT NOT NULL DEFAULT '',
            canonical_id TEXT NOT NULL DEFAULT '', canonical_total_chunks INTEGER,
            updated_at TEXT NOT NULL);
        CREATE TABLE rag_chunks(document_id TEXT NOT NULL,seq INTEGER NOT NULL,text TEXT NOT NULL,
            page INTEGER,section TEXT,PRIMARY KEY(document_id,seq));
        CREATE TABLE rag_sessions(id TEXT PRIMARY KEY,title TEXT NOT NULL,document_ids TEXT NOT NULL,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE rag_messages(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,role TEXT NOT NULL,
            content TEXT NOT NULL,passages TEXT NOT NULL DEFAULT '[]',supplement INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL);
    """)
    db.execute("INSERT INTO rag_documents VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("legacy-doc", "旧资料", "old.txt", 1, 1, "ready", "", "legacy-canonical", 1, "now"))
    db.execute("INSERT INTO rag_chunks VALUES(?,?,?,?,?)", ("legacy-doc", 0, "旧正文", 3, "章节"))
    db.commit()
    db.close()

    migrated = RagLocalStore(path)
    try:
        row = migrated.document("legacy-doc")
        first_target = row["remote_upload_id"]
        assert row["status"] == "not_uploaded"
        assert first_target.startswith("content-v1-")
        assert row["current_content_sha256"] and not row["uploaded_content_sha256"]
    finally:
        migrated.close()
    reopened = RagLocalStore(path)
    try:
        row = reopened.document("legacy-doc")
        assert row["remote_upload_id"] == first_target
        assert row["status"] == "not_uploaded"
    finally:
        reopened.close()


def test_rag226_new_device_does_not_trust_same_count_remote_ready_row(tmp_path):
    fake = StrictReadyRagServer()
    digest = hashlib.sha256(b"source bytes known by both devices").hexdigest()
    fake.documents[digest] = {"title": "Doc 0", "source": "doc-0.txt", "total": 1,
        "chunks": {0: {"seq": 0, "text": "另一设备的旧解析正文。", "page": 1, "section": "正文"}},
        "status": "ready"}
    store = course_store(tmp_path, [(digest, "本设备当前采用的正文。")])
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        listed = instance.sources()
        visible = next(row for row in listed["documents"] if row["id"] == digest)
        saved = instance.local.document(digest)
        assert visible["status"] == "ready"
        assert saved["remote_upload_id"] != digest
        assert saved["canonical_id"] != digest
        assert saved["uploaded_content_sha256"] == saved["current_content_sha256"]
        assert fake.documents[saved["canonical_id"]]["chunks"][0]["text"] == "本设备当前采用的正文。"
        assert fake.documents[digest]["chunks"][0]["text"] == "另一设备的旧解析正文。"
        assert not any(request.method == "DELETE" for request in fake.requests)
    finally:
        instance.close()


def test_rag226_same_account_sources_share_canonical_without_deleting(tmp_path):
    fake = StrictReadyRagServer()
    left = hashlib.sha256(b"left source bytes").hexdigest()
    right = hashlib.sha256(b"right source bytes").hexdigest()
    store = course_store(tmp_path, [(left, "完全相同正文。"), (right, "完全相同正文。")])
    # Identical text can legitimately share a server canonical even when its
    # citation metadata differs; local content fingerprints must still differ.
    store._chunks["plan-1"][0]["page_start"] = 99
    store._chunks["plan-1"][0]["section"] = ["另一个章节"]
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        instance.ingest([left, right])
        left_row, right_row = instance.local.document(left), instance.local.document(right)
        assert left_row["canonical_id"] == right_row["canonical_id"]
        assert left_row["current_content_sha256"] != right_row["current_content_sha256"]
        assert len(fake.documents) == 1
        docs = instance.sources()["documents"]
        assert {row["id"] for row in docs} == {left, right}
        assert not any(request.method == "DELETE" for request in fake.requests)
    finally:
        instance.close()


def test_rag226_upload_receipt_cannot_confirm_a_newer_concurrent_snapshot(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class BlockingRagServer(StrictReadyRagServer):
        def __call__(self, request):
            if request.method == "POST" and request.url.path.endswith("/chunks"):
                entered.set()
                assert release.wait(5), "test upload was not released"
            return super().__call__(request)

    fake = BlockingRagServer()
    digest = hashlib.sha256(b"concurrent source bytes").hexdigest()
    store = course_store(tmp_path, [(digest, "上传开始时的旧快照。")])
    instance, _, _ = service(tmp_path, fake, store=store)
    outcome = []
    worker = threading.Thread(target=lambda: outcome.append(instance.ingest([digest])))
    try:
        worker.start()
        assert entered.wait(5), "upload did not start"
        store._chunks["plan-0"][0]["text"] = "上传途中更新的新快照。"
        instance._course_documents()
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        row = instance.local.document(digest)
        assert row["uploaded_content_sha256"] != row["current_content_sha256"]
        assert row["status"] == "ready"  # raw DB state may reflect old receipt
        assert outcome[0]["documents"][0]["status"] == "not_uploaded"
        listed = next(item for item in instance.sources()["documents"] if item["id"] == digest)
        assert listed["status"] == "ready"  # sources completed the new immutable version
        assert instance.local.document(digest)["uploaded_content_sha256"] == \
            instance.local.document(digest)["current_content_sha256"]
        assert len(fake.documents) == 2
    finally:
        release.set()
        worker.join(5)
        instance.close()


def test_rag226_repair_receipt_cannot_confirm_a_newer_concurrent_snapshot(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class BlockingRepairServer(StrictReadyRagServer):
        def __call__(self, request):
            if request.method == "POST" and request.url.path.endswith("/chunks"):
                body = json.loads(request.content)
                if body.get("repair_existing"):
                    entered.set()
                    assert release.wait(5), "test repair was not released"
            return super().__call__(request)

    fake = BlockingRepairServer()
    digest = hashlib.sha256(b"repair concurrent source").hexdigest()
    store = course_store(tmp_path, [(digest, "修复开始时第一块。")])
    store._sources["plan-0"][0]["chunk_count"] = 2
    store._chunks["plan-0"].append({"id": "chunk-1", "source_id": "source-0",
        "position": 1, "text": "修复开始时第二块。", "page_start": 2, "section": ["后续"]})
    instance, _, _ = service(tmp_path, fake, store=store)
    worker = None
    try:
        instance.ingest([digest])
        old_snapshot = instance.local.document(digest)["current_content_sha256"]
        canonical = instance.local.document(digest)["canonical_id"]
        fake.documents[canonical]["total"] = 1
        fake.documents[canonical]["chunks"] = {0: fake.documents[canonical]["chunks"][0]}
        repair_row = instance._document_chunks(digest)
        worker = threading.Thread(target=lambda: instance._send_repair_batches(repair_row))
        worker.start()
        assert entered.wait(5), "repair did not start"
        store._chunks["plan-0"][0]["text"] = "修复途中第一块已更新。"
        instance._course_documents()
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        row = instance.local.document(digest)
        assert old_snapshot == row["uploaded_content_sha256"]
        assert row["current_content_sha256"] != row["uploaded_content_sha256"]
        view = instance._doc_view(row)
        assert view["status"] == "not_uploaded"
    finally:
        release.set()
        if worker is not None:
            worker.join(5)
        instance.close()


def test_rag226_version_id_recovers_after_upload_receipt_was_lost(tmp_path):
    fake = StrictReadyRagServer()
    digest = hashlib.sha256(b"locally known source").hexdigest()
    store = course_store(tmp_path, [(digest, "已在远端准备好的内容。")])
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        instance._course_documents()
        local = instance.local.document(digest)
        version_id = _source_version_id(digest, local["current_content_sha256"])
        instance.local.set_remote_upload_id(digest, version_id)
        fake.documents[version_id] = {"title": "Doc 0", "source": "doc-0.txt", "total": 1,
            "chunks": {0: {"seq": 0, "text": "已在远端准备好的内容。", "page": 1, "section": "正文"}},
            "status": "ready"}

        result = instance.ingest([digest])["documents"][0]
        row = instance.local.document(digest)
        assert result["status"] == "ready"
        assert any(request.method == "POST" and request.url.path.endswith("/chunks")
                   for request in fake.requests)
        assert row["uploaded_content_sha256"] == row["current_content_sha256"]
        assert row["canonical_id"] == version_id
    finally:
        instance.close()


def test_rag226_stale_preoccupied_version_id_is_verified_then_replaced(tmp_path):
    fake = StrictReadyRagServer()
    digest = hashlib.sha256(b"cross-device same source").hexdigest()
    store = course_store(tmp_path, [(digest, "客户端当前正文。")])
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        instance._course_documents()
        local = instance.local.document(digest)
        version_id = _source_version_id(digest, local["current_content_sha256"])
        # Simulate a stale/corrupt ready row at the deterministic ID. The ID
        # alone must not certify its body; the server contract rejects mismatch.
        fake.documents[version_id] = {"title": "Doc 0", "source": "doc-0.txt", "total": 1,
            "chunks": {0: {"seq": 0, "text": "版本号相同但正文错误。", "page": 1, "section": "正文"}},
            "status": "ready"}
        instance.local.set_remote_upload_id(digest, version_id)

        result = instance.ingest([digest])["documents"][0]
        row = instance.local.document(digest)
        assert result["status"] == "ready"
        assert row["remote_upload_id"] != version_id
        assert fake.documents[version_id]["chunks"][0]["text"] == "版本号相同但正文错误。"
        assert fake.documents[row["canonical_id"]]["chunks"][0]["text"] == "客户端当前正文。"
        assert row["uploaded_content_sha256"] == row["current_content_sha256"]
    finally:
        instance.close()
