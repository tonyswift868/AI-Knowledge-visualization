"""PostgreSQL transaction visibility for RAG delete and search."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import uuid

import psycopg

from support_rag154 import API, chunk_payload, rag154_backends, rag154_headers


CHUNKS = [
    {"seq": 0, "text": "删资料事务可见性第一段：删除提交前必须仍可检索。", "page": 1, "section": "第一段"},
    {"seq": 1, "text": "删资料事务可见性第二段：删除提交后整份资料消失。", "page": 2, "section": "第二段"},
]
QUERY = "删资料事务可见性 第一段 第二段"


def _admin_url(backends: dict) -> str:
    return backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")


def _scope(backends: dict) -> str:
    return hashlib.sha256(
        (backends["issuer"] + "\0" + backends["subject_a"]).encode("utf-8")
    ).hexdigest()


def _upload(backends: dict, document_id: str):
    return backends["client"].post(
        f"{API}/documents/{document_id}/chunks",
        headers=rag154_headers(backends, request_id=f"upload-354-{document_id}"),
        json=chunk_payload(CHUNKS, title="删除可见性", source="local://rag-delete354"),
    )


def _search(backends: dict, document_id: str):
    return backends["client"].post(
        f"{API}/search",
        headers=rag154_headers(backends, request_id=f"search-354-{document_id}"),
        json={"document_ids": [document_id], "query": QUERY, "k": 10},
    )


def _delete(backends: dict, document_id: str, request_id: str):
    return backends["client"].delete(
        f"{API}/documents/{document_id}",
        headers=rag154_headers(backends, request_id=request_id),
    )


def _assert_full_search(response, document_id: str):
    assert response.status_code == 200, response.text
    passages = response.json()["passages"]
    assert {item["document_id"] for item in passages} == {document_id}
    assert {item["text"] for item in passages} == {chunk["text"] for chunk in CHUNKS}


def _counts(connection, scope: str, document_id: str) -> tuple[int, int, int, int, int]:
    return tuple(
        connection.execute(
            f"SELECT count(*) FROM {table} WHERE account_scope=%s AND {column}=%s",
            (scope, document_id),
        ).fetchone()[0]
        for table, column in (
            ("rag_documents", "document_id"),
            ("rag_chunks", "document_id"),
            ("rag_document_aliases", "requested_document_id"),
            ("rag_document_upload_manifests", "document_id"),
            ("rag_source_chunks", "document_id"),
        )
    )


def test_rag_delete354_uncommitted_api_delete_stays_old_until_commit(rag154_backends):
    """A blocked API delete cannot expose a half-deleted graph to search."""
    document_id = "delete354-commit-visibility"
    uploaded = _upload(rag154_backends, document_id)
    assert uploaded.status_code == 200, uploaded.text
    scope = _scope(rag154_backends)

    # Holding the row lock makes the actual RagService DELETE stop after it
    # enters its write transaction and before its commit.  Search uses another
    # connection and therefore observes the old committed graph.
    with psycopg.connect(_admin_url(rag154_backends)) as blocker:
        blocker.execute(
            "SELECT 1 FROM rag_documents WHERE account_scope=%s AND document_id=%s FOR UPDATE",
            (scope, document_id),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending_delete = pool.submit(_delete, rag154_backends, document_id, "delete-354-pending")
            before_commit = _search(rag154_backends, document_id)
            _assert_full_search(before_commit, document_id)
            blocker.commit()
            deleted = pending_delete.result(timeout=60)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["content_deleted"] is True

    after_commit = _search(rag154_backends, document_id)
    assert after_commit.status_code == 200, after_commit.text
    assert after_commit.json()["passages"] == []
    listed = rag154_backends["client"].get(
        f"{API}/documents", headers=rag154_headers(rag154_backends, request_id="list-354-committed")
    )
    assert listed.status_code == 200, listed.text
    assert document_id not in {row["document_id"] for row in listed.json()["documents"]}
    with psycopg.connect(_admin_url(rag154_backends)) as check:
        assert _counts(check, scope, document_id) == (0, 0, 0, 0, 0)


def test_rag_delete354_failed_promotion_rolls_back_and_retry_is_complete(rag154_backends):
    """A mid-promotion API failure rolls back every table, then a retry is atomic."""
    document_id = "delete354-failure-primary"
    alias_id = "delete354-failure-alias"
    uploaded = _upload(rag154_backends, document_id)
    assert uploaded.status_code == 200, uploaded.text
    alias = _upload(rag154_backends, alias_id)
    assert alias.status_code == 200, alias.text
    assert alias.json()["deduplicated"] is True
    scope = _scope(rag154_backends)
    function_name = "fail_rag_delete354_" + uuid.uuid4().hex[:12]
    trigger_name = "fail_rag_delete354_trigger_" + uuid.uuid4().hex[:12]
    admin_url = _admin_url(rag154_backends)
    with psycopg.connect(admin_url) as check:
        baseline = {
            document_id: _counts(check, scope, document_id),
            alias_id: _counts(check, scope, alias_id),
        }
    try:
        with psycopg.connect(admin_url) as admin:
            admin.execute(
                f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'forced visibility rollback'; END $$"
            )
            admin.execute(
                f"CREATE TRIGGER {trigger_name} BEFORE UPDATE OF document_id ON rag_chunks "
                f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
            )

        failed = _delete(rag154_backends, document_id, "delete-354-failing-promotion")
        assert failed.status_code == 503, failed.text
        _assert_full_search(_search(rag154_backends, document_id), document_id)
        with psycopg.connect(admin_url) as check:
            assert _counts(check, scope, document_id) == baseline[document_id]
            assert _counts(check, scope, alias_id) == baseline[alias_id]
    finally:
        with psycopg.connect(admin_url) as admin:
            admin.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON rag_chunks")
            admin.execute(f"DROP FUNCTION IF EXISTS {function_name}()")

    promoted = _delete(rag154_backends, document_id, "delete-354-retry-promotion")
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["content_deleted"] is False
    assert promoted.json()["canonical_id"] == alias_id
    _assert_full_search(_search(rag154_backends, alias_id), alias_id)
    deleted = _delete(rag154_backends, alias_id, "delete-354-final")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["content_deleted"] is True
    assert _search(rag154_backends, alias_id).json()["passages"] == []
    with psycopg.connect(admin_url) as check:
        assert _counts(check, scope, document_id) == (0, 0, 0, 0, 0)
        assert _counts(check, scope, alias_id) == (0, 0, 0, 0, 0)
