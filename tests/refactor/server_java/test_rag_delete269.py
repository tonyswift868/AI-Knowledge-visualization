"""HTTP and PostgreSQL contract tests for logical deletion of shared RAG content."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import uuid

import psycopg

from support_rag154 import API, chunk_payload, rag154_backends, rag154_headers


def _upload(backends, doc_id: str, chunks: list[dict], *, subject=None, title="资料"):
    return backends["client"].post(
        f"{API}/documents/{doc_id}/chunks",
        headers=rag154_headers(
            backends, subject=subject or backends["subject_a"], request_id=f"upload-269-{doc_id}"
        ),
        json=chunk_payload(chunks, title=title, source="local://rag-delete269"),
    )


def _delete(backends, doc_id: str, *, subject=None, request_id="delete-269"):
    return backends["client"].delete(
        f"{API}/documents/{doc_id}",
        headers=rag154_headers(backends, subject=subject or backends["subject_a"], request_id=request_id),
    )


def _search(backends, doc_ids: list[str], query: str, *, subject=None):
    return backends["client"].post(
        f"{API}/search",
        headers=rag154_headers(backends, subject=subject or backends["subject_a"], request_id="search-269"),
        json={"document_ids": doc_ids, "query": query, "k": 10},
    )


def _counts(backends, subject: str, doc_ids: list[str]):
    client = backends["client"]
    headers = rag154_headers(backends, subject=subject, request_id="list-269")
    listing = client.get(f"{API}/documents", headers=headers)
    assert listing.status_code == 200, listing.text
    rows = listing.json()["documents"]
    wanted = set(doc_ids)
    ids = {row["document_id"] for row in rows if row["document_id"] in wanted}
    aliases = {alias for row in rows for alias in row["aliases"] if alias in wanted}
    id_array = list(wanted)
    account_scope = hashlib.sha256(
        (backends["issuer"] + "\0" + subject).encode("utf-8")
    ).hexdigest()
    with psycopg.connect(backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")) as db:
        chunk_count = db.execute(
            "SELECT count(*) FROM rag_chunks WHERE account_scope=%s AND document_id = ANY(%s)",
            (account_scope, id_array),
        ).fetchone()[0]
        document_count = db.execute(
            "SELECT count(*) FROM rag_documents WHERE account_scope=%s AND document_id = ANY(%s)",
            (account_scope, id_array),
        ).fetchone()[0]
        vector_count = db.execute(
            "SELECT count(*) FROM rag_chunks WHERE account_scope=%s AND document_id = ANY(%s) "
            "AND embedding IS NOT NULL", (account_scope, id_array),
        ).fetchone()[0]
    return ids, aliases, document_count, chunk_count, vector_count


def _manifest_count(backends, subject: str, doc_ids: list[str]) -> int:
    account_scope = hashlib.sha256(
        (backends["issuer"] + "\0" + subject).encode("utf-8")
    ).hexdigest()
    with psycopg.connect(backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")) as db:
        return db.execute(
            "SELECT count(*) FROM rag_document_upload_manifests "
            "WHERE account_scope=%s AND document_id = ANY(%s)",
            (account_scope, doc_ids),
        ).fetchone()[0]


def test_rag_delete269_canonical_promotion_keeps_each_alias_retrievable_and_replayable(rag154_backends):
    """Promotion preserves the selected content and each alias's original chunk manifest."""
    body = "Java 线程通过共享内存协作；volatile 提供可见性保证，但不保证复合操作原子性。"
    canonical_chunks = [
        {"seq": 0, "text": "Java 线程通过共享内存协作；", "page": 1, "section": "线程"},
        {"seq": 1, "text": "volatile 提供可见性保证，但不保证复合操作原子性。", "page": 2, "section": "volatile"},
    ]
    one_chunk_alias = [{"seq": 0, "text": body, "page": 8, "section": "线程与内存"}]
    assert _upload(rag154_backends, "delete269-primary", canonical_chunks).status_code == 200
    first_alias = _upload(rag154_backends, "delete269-a-alias", one_chunk_alias)
    second_alias = _upload(rag154_backends, "delete269-z-alias", canonical_chunks)
    assert first_alias.status_code == second_alias.status_code == 200
    assert first_alias.json()["deduplicated"] and second_alias.json()["deduplicated"]

    assert _search(rag154_backends, ["delete269-primary"], "volatile 可见性").json()["passages"]

    remove_primary = _delete(rag154_backends, "delete269-primary")
    assert remove_primary.status_code == 200, remove_primary.text
    assert remove_primary.json()["content_deleted"] is False
    assert remove_primary.json()["canonical_id"] == "delete269-a-alias"
    ids, aliases, docs, chunks, vectors = _counts(
        rag154_backends, rag154_backends["subject_a"], ["delete269-a-alias", "delete269-z-alias"]
    )
    assert "delete269-a-alias" in ids
    assert "delete269-z-alias" in aliases
    assert "delete269-primary" not in ids | aliases
    assert (docs, chunks, vectors) == (1, 2, 2)
    promoted_listing = rag154_backends["client"].get(
        f"{API}/documents", headers=rag154_headers(rag154_backends)
    ).json()["documents"]
    promoted_row = next(row for row in promoted_listing if row["document_id"] == "delete269-a-alias")
    assert (promoted_row["status"], promoted_row["total_chunks"], promoted_row["received_chunks"]) == (
        "ready", 1, 1
    )
    assert _manifest_count(rag154_backends, rag154_backends["subject_a"], ["delete269-a-alias"]) == 1

    # The added manifest table obeys the same account RLS boundary as documents.
    with psycopg.connect(rag154_backends["app_url"].replace("postgresql+psycopg://", "postgresql://")) as app_db:
        assert app_db.execute("SELECT count(*) FROM rag_document_upload_manifests").fetchone()[0] == 0
        app_db.execute("SELECT set_config('mentorflow.rag_account_scope',%s,false)", ("f" * 64,))
        assert app_db.execute("SELECT count(*) FROM rag_document_upload_manifests").fetchone()[0] == 0
        app_db.execute(
            "SELECT set_config('mentorflow.rag_account_scope',%s,false)",
            (hashlib.sha256((rag154_backends["issuer"] + "\0" + rag154_backends["subject_a"]).encode()).hexdigest(),),
        )
        assert app_db.execute("SELECT count(*) FROM rag_document_upload_manifests").fetchone()[0] == 1

    # The alias was originally uploaded as one chunk although its promoted
    # canonical content has two. Retrying the original request stays idempotent.
    replay = _upload(rag154_backends, "delete269-a-alias", one_chunk_alias)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "ready"
    assert replay.json()["document_id"] == "delete269-a-alias"
    assert replay.json()["deduplicated"] is True
    assert (replay.json()["received_chunks"], replay.json()["total_chunks"]) == (1, 1)
    remaining_alias_search = _search(
        rag154_backends, ["delete269-z-alias"], "volatile 原子性"
    )
    assert remaining_alias_search.status_code == 200, remaining_alias_search.text
    assert remaining_alias_search.json()["passages"]
    assert all(row["document_id"] == "delete269-z-alias"
               for row in remaining_alias_search.json()["passages"])
    detach = _delete(rag154_backends, "delete269-z-alias")
    assert detach.status_code == 200, detach.text
    assert detach.json()["deleted"] is True
    assert detach.json()["content_deleted"] is False
    assert detach.json()["canonical_id"] == "delete269-a-alias"
    retrieved = _search(rag154_backends, ["delete269-a-alias"], "volatile 原子性")
    assert retrieved.status_code == 200, retrieved.text
    assert retrieved.json()["passages"]
    assert all(row["document_id"] == "delete269-a-alias" for row in retrieved.json()["passages"])
    assert any("复合操作原子性" in row["text"] for row in retrieved.json()["passages"])

    # Last logical copy removes chunks and vectors, and the same body can be
    # uploaded again as fresh content.
    final = _delete(rag154_backends, "delete269-a-alias")
    assert final.status_code == 200, final.text
    assert final.json()["deleted"] is True and final.json()["content_deleted"] is True
    assert final.json()["canonical_id"] is None
    deleted_ids = ["delete269-primary", "delete269-a-alias", "delete269-z-alias"]
    assert _manifest_count(rag154_backends, rag154_backends["subject_a"], deleted_ids) == 0
    ids, aliases, docs, chunks, vectors = _counts(rag154_backends, rag154_backends["subject_a"], deleted_ids)
    assert not (ids | aliases) & set(deleted_ids)
    assert (docs, chunks, vectors) == (0, 0, 0)
    fresh = _upload(rag154_backends, "delete269-reuploaded", canonical_chunks)
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["status"] == "ready"
    assert _search(rag154_backends, ["delete269-reuploaded"], "volatile 可见性").json()["passages"]


def test_rag_delete269_same_document_id_and_content_are_isolated_by_account(rag154_backends):
    shared_id = "delete269-shared-id"
    chunks_a = [{"seq": 0, "text": "账号甲的私有 Java 内容。", "page": 1, "section": "甲"}]
    chunks_b = [{"seq": 0, "text": "账号乙的独立 Java 内容。", "page": 1, "section": "乙"}]
    assert _upload(rag154_backends, shared_id, chunks_a, subject=rag154_backends["subject_a"]).status_code == 200
    assert _upload(rag154_backends, shared_id, chunks_b, subject=rag154_backends["subject_b"]).status_code == 200
    deleted = _delete(rag154_backends, shared_id, subject=rag154_backends["subject_a"])
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["content_deleted"] is True
    assert _search(rag154_backends, [shared_id], "账号乙 Java", subject=rag154_backends["subject_b"]).json()["passages"]
    assert _search(rag154_backends, [shared_id], "账号甲 Java", subject=rag154_backends["subject_a"]).json()["passages"] == []
    bob_list = rag154_backends["client"].get(
        f"{API}/documents", headers=rag154_headers(rag154_backends, subject=rag154_backends["subject_b"])
    )
    assert [row["document_id"] for row in bob_list.json()["documents"]] == [shared_id]
    assert _delete(rag154_backends, shared_id, subject=rag154_backends["subject_b"]).status_code == 200


def test_rag_delete269_concurrent_duplicate_delete_is_idempotent_or_not_found(rag154_backends):
    doc_id = "delete269-concurrent-delete"
    chunks = [{"seq": 0, "text": "并发删除后不会留下半条记录。", "page": 1, "section": "事务"}]
    assert _upload(rag154_backends, doc_id, chunks).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda index: _delete(
            rag154_backends, doc_id, request_id=f"delete-269-race-{index}"
        ), range(2)))
    assert all(response.status_code in (200, 404) for response in results)
    assert all(response.status_code != 500 for response in results)
    ids, aliases, docs, chunk_count, vector_count = _counts(
        rag154_backends, rag154_backends["subject_a"], [doc_id]
    )
    assert doc_id not in ids | aliases
    assert (docs, chunk_count, vector_count) == (0, 0, 0)


def test_rag_delete269_concurrent_delete_with_aliases_leaves_one_consistent_promoted_graph(rag154_backends):
    doc_id = "delete269-concurrent-shared"
    chunks = [{"seq": 0, "text": "并发删主资料只会提升一个逻辑副本。", "page": 1, "section": "事务"}]
    assert _upload(rag154_backends, doc_id, chunks).status_code == 200
    for alias in ("delete269-concurrent-a", "delete269-concurrent-b"):
        result = _upload(rag154_backends, alias, chunks)
        assert result.status_code == 200 and result.json()["deduplicated"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda index: _delete(
            rag154_backends, doc_id, request_id=f"delete-269-shared-race-{index}"
        ), range(2)))
    assert sorted(response.status_code for response in results) in ([200, 404], [200, 200])
    assert all(response.status_code != 500 for response in results)
    ids, aliases, docs, chunk_count, vector_count = _counts(
        rag154_backends, rag154_backends["subject_a"], [doc_id, "delete269-concurrent-a", "delete269-concurrent-b"]
    )
    assert ids == {"delete269-concurrent-a"}
    assert aliases == {"delete269-concurrent-b"}
    assert (docs, chunk_count, vector_count) == (1, 1, 1)
    assert _search(rag154_backends, ["delete269-concurrent-b"], "逻辑副本").json()["passages"]


def test_rag_delete269_failure_during_promotion_rolls_back_every_table(rag154_backends):
    doc_id, alias = "delete269-rollback-primary", "delete269-rollback-alias"
    chunks = [{"seq": 0, "text": "失败的主文档删除必须完整回滚。", "page": 1, "section": "原子性"}]
    assert _upload(rag154_backends, doc_id, chunks).status_code == 200
    assert _upload(rag154_backends, alias, chunks).status_code == 200
    function_name = "fail_rag_delete269_" + uuid.uuid4().hex[:10]
    trigger_name = "fail_rag_delete269_trigger_" + uuid.uuid4().hex[:10]
    admin_url = rag154_backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(admin_url) as admin:
            admin.execute(
                f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'forced promotion failure'; END $$"
            )
            admin.execute(
                f"CREATE TRIGGER {trigger_name} BEFORE UPDATE OF document_id ON rag_chunks "
                f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
            )
        failed = _delete(rag154_backends, doc_id)
        assert failed.status_code == 503, failed.text
        ids, aliases, docs, chunk_count, vector_count = _counts(
            rag154_backends, rag154_backends["subject_a"], [doc_id, alias]
        )
        assert ids == {doc_id}
        assert aliases == {alias}
        assert (docs, chunk_count, vector_count) == (1, 1, 1)
        assert _manifest_count(rag154_backends, rag154_backends["subject_a"], [doc_id, alias]) == 0
    finally:
        with psycopg.connect(admin_url) as admin:
            admin.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON rag_chunks")
            admin.execute(f"DROP FUNCTION IF EXISTS {function_name}()")
    recovered = _delete(rag154_backends, doc_id)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["canonical_id"] == alias


def test_rag_delete269_upload_racing_delete_never_leaves_partial_content(rag154_backends):
    doc_id = "delete269-upload-delete-race"
    chunks = [
        {"seq": 0, "text": "上传与删除在同账号事务锁下串行。", "page": 1, "section": "事务"},
        {"seq": 1, "text": "提交后资料必须是完整可检索的。", "page": 2, "section": "一致性"},
    ]
    assert _upload(rag154_backends, doc_id, chunks).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        upload_future = pool.submit(_upload, rag154_backends, doc_id, chunks)
        delete_future = pool.submit(_delete, rag154_backends, doc_id, request_id="delete-269-upload-race")
        upload_result, delete_result = upload_future.result(), delete_future.result()
    assert upload_result.status_code in (200, 404)
    assert delete_result.status_code in (200, 404)
    ids, aliases, docs, chunk_count, vector_count = _counts(
        rag154_backends, rag154_backends["subject_a"], [doc_id]
    )
    assert doc_id not in aliases
    if doc_id in ids:
        assert (docs, chunk_count, vector_count) == (1, 2, 2)
        assert _search(rag154_backends, [doc_id], "资料完整可检索").json()["passages"]
    else:
        assert (docs, chunk_count, vector_count) == (0, 0, 0)


def test_rag_delete269_listing_and_search_observe_promotion_atomically(rag154_backends):
    primary, alias = "delete269-atomic-primary", "delete269-atomic-alias"
    chunks = [{"seq": 0, "text": "事务提交前后检索都应能解析同一份逻辑资料。", "page": 1, "section": "一致性"}]
    assert _upload(rag154_backends, primary, chunks).status_code == 200
    assert _upload(rag154_backends, alias, chunks).status_code == 200
    client = rag154_backends["client"]
    headers = rag154_headers(rag154_backends, request_id="read-during-promotion-269")
    start = __import__("threading").Event()

    def observe(_):
        start.wait(timeout=5)
        listed = client.get(f"{API}/documents", headers=headers)
        searched = _search(rag154_backends, [alias], "事务逻辑资料")
        return listed, searched

    with ThreadPoolExecutor(max_workers=5) as pool:
        reads = [pool.submit(observe, index) for index in range(4)]
        start.set()
        deleted = _delete(rag154_backends, primary, request_id="promote-269")
        observations = [future.result() for future in reads]
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["canonical_id"] == alias
    for listed, searched in observations:
        assert listed.status_code == searched.status_code == 200
        ids = {row["document_id"] for row in listed.json()["documents"]}
        assert ids & {primary, alias} in ({primary}, {alias})
        passages = searched.json()["passages"]
        assert passages
        assert all(row["document_id"] == alias for row in passages)
