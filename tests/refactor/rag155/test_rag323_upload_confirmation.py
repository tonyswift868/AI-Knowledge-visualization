"""323: unknown upload receipts require replaying the complete current snapshot."""
from __future__ import annotations

import hashlib

from refactor.learning.app.rag import API
from tests.refactor.rag155.test_rag155_backend import service
from tests.refactor.rag155.test_rag226_content_version import (
    StrictReadyRagServer, course_store,
)


def _digest():
    return hashlib.sha256(b"rag323-source").hexdigest()


def _seed_ready(fake, document_id, chunks):
    fake.documents[document_id] = {
        "title": "Doc 0", "source": "doc-0.txt", "total": len(chunks),
        "chunks": {index: {"seq": index, "text": text, "page": 1, "section": "正文"}
                   for index, text in enumerate(chunks)},
        "status": "ready",
    }


def test_rag323_sources_keeps_unknown_ready_original_and_does_not_upload(tmp_path):
    digest = _digest()
    fake = StrictReadyRagServer()
    store = course_store(tmp_path, [(digest, "当前正文。")])
    _seed_ready(fake, digest, ["当前正文。"])
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        rows = instance.sources()["documents"]
        row = next(row for row in rows if row["id"] == digest)
        assert instance.local.document(digest)["remote_upload_id"] == digest
        assert row["status"] == "not_uploaded"
        assert not any(request.method == "POST" and request.url.path.startswith(API + "/documents/")
                       for request in fake.requests)
    finally:
        instance.close()


def test_rag323_ingest_replays_same_body_to_confirm_original_id(tmp_path):
    digest = _digest()
    fake = StrictReadyRagServer()
    store = course_store(tmp_path, [(digest, "当前正文。")])
    _seed_ready(fake, digest, ["当前正文。"])
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        result = instance.ingest([digest])["documents"][0]
        row = instance.local.document(digest)
        assert result["status"] == "ready"
        assert row["remote_upload_id"] == digest
        assert row["canonical_id"] == digest
        assert row["uploaded_content_sha256"] == row["current_content_sha256"]
        posts = [request for request in fake.requests if request.method == "POST"
                 and request.url.path.endswith("/chunks")]
        assert len(posts) == 1
        assert set(fake.documents) == {digest}
        assert fake.documents[digest]["chunks"][0]["text"] == "当前正文。"
    finally:
        instance.close()


def test_rag323_later_batch_conflict_prevents_early_snapshot_confirmation(tmp_path, monkeypatch):
    digest = _digest()
    fake = StrictReadyRagServer()
    store = course_store(tmp_path, [(digest, "占位正文。")])
    store._chunks["plan-0"] = [
        {"id": "chunk-0", "source_id": "source-0", "position": 0,
         "text": "当前第一块。", "page_start": 1, "section": ["正文"]},
        {"id": "chunk-1", "source_id": "source-0", "position": 1,
         "text": "当前第二块。", "page_start": 1, "section": ["正文"]},
    ]
    store._sources["plan-0"][0]["chunk_count"] = 2
    _seed_ready(fake, digest, ["当前第一块。", "远端旧第二块。"])
    instance, _, _ = service(tmp_path, fake, store=store)
    monkeypatch.setattr("refactor.learning.app.rag.BATCH_CHUNKS", 1)
    confirmed_targets = []

    def progress(_view):
        saved = instance.local.document(digest)
        if saved["uploaded_content_sha256"] == saved["current_content_sha256"]:
            confirmed_targets.append(saved["remote_upload_id"])

    try:
        result = instance.ingest([digest], on_progress=progress)["documents"][0]
        row = instance.local.document(digest)
        version_id = row["remote_upload_id"]
        assert result["status"] == "ready"
        assert version_id != digest
        assert row["uploaded_content_sha256"] == row["current_content_sha256"]
        assert fake.documents[digest]["chunks"][1]["text"] == "远端旧第二块。"
        assert [fake.documents[version_id]["chunks"][i]["text"] for i in range(2)] == [
            "当前第一块。", "当前第二块。"]
        original_posts = [request for request in fake.requests if request.method == "POST"
                          and request.url.path == f"{API}/documents/{digest}/chunks"]
        assert len(original_posts) == 2
        assert confirmed_targets and set(confirmed_targets) == {version_id}
    finally:
        instance.close()
