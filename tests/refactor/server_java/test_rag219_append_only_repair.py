"""Append-only recovery for ready RAG documents with an incomplete local source."""
from __future__ import annotations

from hashlib import sha256

from support_rag154 import API, rag154_backends, rag154_headers


def _chunk_digest(chunk: dict) -> str:
    return sha256(chunk["text"].encode("utf-8")).hexdigest()


def test_rag219_append_repair_preserves_nonprefix_existing_chunks_and_is_idempotent(rag154_backends):
    client = rag154_backends["client"]
    doc_id = "rag219-append-repair"
    chunks = [
        {"seq": 0, "text": "完整资料第一段", "page": 1, "section": "一"},
        {"seq": 1, "text": "完整资料第二段", "page": 2, "section": "二"},
        {"seq": 2, "text": "完整资料第三段", "page": 3, "section": "三"},
    ]
    headers = rag154_headers(rag154_backends, request_id="rag219-initial")
    old = client.post(f"{API}/documents/{doc_id}/chunks", headers=headers, json={
        "title": "完整资料", "source": "local://rag219", "total_chunks": 1,
        "chunks": [{**chunks[2], "seq": 0}],
    })
    assert old.status_code == 200, old.text
    assert old.json()["status"] == "ready"

    manifest = [_chunk_digest(chunk) for chunk in chunks]
    repair_body = {
        "title": "完整资料", "source": "local://rag219", "total_chunks": len(chunks),
        "repair_existing": True, "repair_manifest": manifest,
        "repair_content_sha256": sha256("".join(chunk["text"] for chunk in chunks).encode()).hexdigest(),
        "chunks": chunks,
    }
    repaired = client.post(f"{API}/documents/{doc_id}/chunks",
        headers=rag154_headers(rag154_backends, request_id="rag219-repair"), json=repair_body)
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["repair_complete"] is True
    assert repaired.json()["status"] == "ready"
    assert repaired.json()["received_chunks"] == repaired.json()["total_chunks"] == 3

    # The original block was the last local chunk, not a continuous local prefix.
    with __import__("psycopg").connect(rag154_backends["admin_url"].replace(
            "postgresql+psycopg://", "postgresql://")) as db:
        rows = db.execute("SELECT seq,text FROM rag_chunks WHERE document_id=%s ORDER BY seq", (doc_id,)).fetchall()
    assert rows == [(0, "完整资料第三段"), (1, "完整资料第一段"), (2, "完整资料第二段")]

    retry = client.post(f"{API}/documents/{doc_id}/chunks",
        headers=rag154_headers(rag154_backends, request_id="rag219-repair-retry"), json=repair_body)
    assert retry.status_code == 200, retry.text
    assert retry.json()["repair_complete"] is True
    assert retry.json()["received_chunks"] == 3
    with __import__("psycopg").connect(rag154_backends["admin_url"].replace(
            "postgresql+psycopg://", "postgresql://")) as db:
        assert db.execute("SELECT count(*) FROM rag_chunks WHERE document_id=%s", (doc_id,)).fetchone()[0] == 3
    legacy = client.post(f"{API}/documents/{doc_id}/chunks",
        headers=rag154_headers(rag154_backends, request_id="rag219-legacy-after-repair"), json={
            "title": "完整资料", "source": "local://rag219", "total_chunks": 3,
            "chunks": [{**chunks[0], "text": "被修改的旧段落"}],
        })
    assert legacy.status_code == 409


def test_rag219_same_text_different_chunking_does_not_append_duplicates(rag154_backends):
    client = rag154_backends["client"]
    doc_id = "rag219-rechunked"
    old_chunk = {"seq": 0, "text": "甲乙", "page": "1", "section": "全文"}
    created = client.post(f"{API}/documents/{doc_id}/chunks",
        headers=rag154_headers(rag154_backends, request_id="rag219-rechunked-old"), json={
            "title": "重分块", "source": "local://rag219-rechunked",
            "total_chunks": 1, "chunks": [old_chunk],
        })
    assert created.status_code == 200, created.text
    chunks = [
        {"seq": 0, "text": "甲", "page": "1", "section": "第一段"},
        {"seq": 1, "text": "乙", "page": "1", "section": "第二段"},
    ]
    result = client.post(f"{API}/documents/{doc_id}/chunks",
        headers=rag154_headers(rag154_backends, request_id="rag219-rechunked-repair"), json={
            "title": "重分块", "source": "local://rag219-rechunked", "total_chunks": 2,
            "repair_existing": True,
            "repair_manifest": [_chunk_digest(chunk) for chunk in chunks],
            "repair_content_sha256": sha256("甲乙".encode()).hexdigest(), "chunks": chunks,
        })
    assert result.status_code == 200, result.text
    assert result.json()["repair_complete"] is True
    assert result.json()["received_chunks"] == result.json()["total_chunks"] == 1
    with __import__("psycopg").connect(rag154_backends["admin_url"].replace(
            "postgresql+psycopg://", "postgresql://")) as db:
        assert db.execute("SELECT seq,text FROM rag_chunks WHERE document_id=%s", (doc_id,)).fetchall() == [(0, "甲乙")]


def test_rag219_repair_capability_is_advertised(rag154_backends):
    result = rag154_backends["client"].get(f"{API}/documents",
        headers=rag154_headers(rag154_backends, request_id="rag219-capability"))
    assert result.status_code == 200, result.text
    assert "append_only_chunk_repair_v1" in result.json()["capabilities"]


def test_rag219_repair_conflicts_preserve_existing_content(rag154_backends):
    client = rag154_backends["client"]
    doc_id = "rag219-preserve-conflict"
    original = {"seq": 0, "text": "保留原块", "page": "7", "section": "原位置"}
    extra = {"seq": 1, "text": "补充块", "page": "8", "section": "新增"}

    def upload(identifier, chunks, repair=False):
        body = {"title": "冲突保护", "source": "local://rag219-conflict",
                "total_chunks": len(chunks), "chunks": chunks}
        if repair:
            body.update(repair_existing=True, repair_manifest=[_chunk_digest(c) for c in chunks],
                        repair_content_sha256=sha256("".join(c["text"] for c in chunks).encode()).hexdigest())
        return client.post(f"{API}/documents/{identifier}/chunks",
                           headers=rag154_headers(rag154_backends), json=body)

    assert upload(doc_id, [original]).status_code == 200
    unrelated = [{"seq": 0, "text": "完全不同的内容"}]
    assert upload(doc_id, unrelated, repair=True).status_code == 409
    assert upload("rag219-existing-complete", [original, extra]).status_code == 200
    assert upload(doc_id, [original, extra], repair=True).status_code == 409
    with __import__("psycopg").connect(rag154_backends["admin_url"].replace(
            "postgresql+psycopg://", "postgresql://")) as db:
        assert db.execute("SELECT seq,text,page,section FROM rag_chunks WHERE document_id=%s", (doc_id,)).fetchall() == [
            (0, "保留原块", "7", "原位置")]
        assert db.execute("SELECT status,total_chunks FROM rag_documents WHERE document_id=%s", (doc_id,)).fetchone() == ("ready", 1)
