from hashlib import sha256

from tests.refactor.rag155.test_rag155_backend import FakeRagServer, service


def test_rag219_waits_for_all_adopted_chunks_then_repairs_early_ready_upload(tmp_path):
    fake = FakeRagServer()
    source_id = "source-219"
    source_digest = sha256(b"complete adopted source").hexdigest()
    source = {"id": source_id, "adopted": True, "chunk_count": 3,
              "sha256": source_digest, "title": "完整资料", "filename": "source.txt"}
    first = {"id": "chunk-0", "source_id": source_id, "position": 0, "text": "第一段"}
    rows = {"plan-a": [first]}
    store = type("Store", (), {
        "root": tmp_path / "account",
        "list_plans": lambda self, include_archived=False: [{"id": "plan-a", "title": "计划"}],
        "records": lambda self, entity, plan_id="": [source] if entity == "sources" else list(rows[plan_id]),
    })()
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        # A partial source must not become a one-chunk document on the server.
        partial = instance.sources()
        assert partial["plans"][0]["document_ids"] == [source_digest]
        assert len(instance.local.document(source_digest)["chunks"]) == 1
        # The partial row remains visible locally but must not be uploaded.
        pending = instance.ingest([source_digest])["documents"][0]
        assert pending["status"] == "not_uploaded"
        assert fake.documents == {}

        # Reproduce an earlier client having uploaded its available first chunk
        # and declared that one chunk as a complete document.
        fake.documents[source_digest] = {"title": "完整资料", "source": "source.txt",
            "total": 1, "chunks": {0: {"seq": 0, "text": "第三段", "page": None, "section": None}}, "status": "ready"}
        rows["plan-a"] = [
            first,
            {"id": "chunk-1", "source_id": source_id, "position": 1, "text": "第二段"},
            {"id": "chunk-2", "source_id": source_id, "position": 2, "text": "第三段"},
        ]
        complete = instance.sources()
        assert complete["plans"][0]["document_ids"] == [source_digest]
        visible = next(row for row in complete["documents"] if row["id"] == source_digest)
        assert visible["status"] == "ready" and visible["total_chunks"] == 3
        assert visible["uploaded_chunks"] == 3
        remote = fake.documents[source_digest]
        assert [remote["chunks"][i]["text"] for i in range(3)] == ["第三段", "第一段", "第二段"]
        uploaded = instance.ingest([source_digest])["documents"][0]
        assert uploaded["status"] == "ready"
        assert uploaded["total_chunks"] == uploaded["uploaded_chunks"] == 3
        assert sorted(remote["chunks"]) == [0, 1, 2]
        assert [remote["chunks"][i]["text"] for i in range(3)] == ["第三段", "第一段", "第二段"]
        assert len([r for r in fake.requests if r.method == "POST" and r.url.path.endswith("/chunks")]) == 1
    finally:
        instance.close()


def test_rag219_partial_duplicate_plan_cannot_shrink_complete_local_document(tmp_path):
    digest = sha256(b"shared complete source").hexdigest()
    full_source = {"id": "shared", "adopted": True, "chunk_count": 3,
                   "sha256": digest, "title": "共享资料", "filename": "shared.txt"}
    partial_source = {**full_source, "id": "shared-copy"}
    full_chunks = [{"id": f"full-{n}", "source_id": "shared", "position": n,
                    "text": f"第{n}段"} for n in range(3)]
    partial_chunks = [{"id": "partial-0", "source_id": "shared-copy", "position": 0,
                       "text": "第0段"}]
    store = type("Store", (), {
        "root": tmp_path / "account",
        "list_plans": lambda self, include_archived=False: [
            {"id": "complete-plan", "title": "完整"}, {"id": "partial-plan", "title": "部分"}],
        "records": lambda self, entity, plan_id="": (
            [full_source] if entity == "sources" and plan_id == "complete-plan" else
            [partial_source] if entity == "sources" else
            full_chunks if plan_id == "complete-plan" else partial_chunks),
    })()
    fake = FakeRagServer()
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        listed = instance.sources()
        assert listed["plans"][0]["document_ids"] == [digest]
        assert len(instance.local.document(digest)["chunks"]) == 3
        assert digest not in instance._incomplete_documents
    finally:
        instance.close()
