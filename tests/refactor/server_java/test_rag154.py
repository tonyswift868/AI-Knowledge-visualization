"""Real HTTP and PostgreSQL contract tests for RAG154."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import uuid

import psycopg
import pytest

from support_rag154 import (
    API,
    _run_alembic,
    chunk_payload,
    rag154_backends,
    rag154_headers,
    _prepare_database,
)


def _upload(backends, doc_id: str, chunks: list[dict], *, title="资料", source="local://rag154",
            subject=None):
    return backends["client"].post(
        f"{API}/documents/{doc_id}/chunks",
        headers=rag154_headers(backends, subject=subject or backends["subject_a"],
                               request_id=f"upload-{doc_id}"),
        json=chunk_payload(chunks, title=title, source=source),
    )


def _tables(url: str):
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as connection:
        return connection.execute(
            "SELECT to_regclass('public.rag_documents'), to_regclass('public.rag_chunks'), "
            "to_regclass('public.rag_document_aliases')"
        ).fetchone()


def test_rag154_migration_upgrade_downgrade_and_reupgrade(rag154_backends):
    """The reversible 0045 chain round-trips; 0046/0047 remain forward-only."""
    postgres = rag154_backends["postgres"]
    name = "rag154_legacy_" + uuid.uuid4().hex[:12]
    _app_url, _admin_url, url = _prepare_database(
        postgres, name, rag154_backends["issuer"], target="0045_account_closure_rag"
    )
    assert all(_tables(url))
    # An already-current database remains current when upgrade is repeated.
    run_dir = rag154_backends["directory"]
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as connection:
        embedding_type = connection.execute(
            "SELECT format_type(atttypid,atttypmod) FROM pg_attribute "
            "WHERE attrelid='public.rag_chunks'::regclass AND attname='embedding'"
        ).fetchone()[0]
        hnsw_count = connection.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='rag_chunks' "
            "AND indexdef ILIKE '%USING hnsw%' AND indexdef ILIKE '%vector_cosine_ops%'"
        ).fetchone()[0]
        if os.environ.get("RAG154_VECTOR_BACKEND", "local-test") == "pgvector":
            assert embedding_type == "vector(512)"
            assert hnsw_count == 1
        else:
            assert embedding_type == "bytea"
            assert hnsw_count == 0
    _run_alembic(url, ["upgrade", "0045_account_closure_rag"], run_dir / "migration-idempotent-test.log", rag154_backends["issuer"])
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0045_account_closure_rag"
    _run_alembic(url, ["downgrade", "0043_native_sessions"], run_dir / "migration-down-test.log", rag154_backends["issuer"])
    assert _tables(url) == (None, None, None)
    _run_alembic(url, ["upgrade", "0045_account_closure_rag"], run_dir / "migration-up-test.log", rag154_backends["issuer"])
    assert all(_tables(url))

    # Current profile, upload-manifest and source-layout migrations are forward-only.
    # Existing profile data and additive RAG tables survive a refusal.
    current_url = rag154_backends["rollback_url"]
    admin = current_url.replace("mentorflow_migrator:local-migrator-154", "postgres")
    admin = admin.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(admin) as connection:
        connection.execute(
                "INSERT INTO public.account_profiles(account_scope,nickname) VALUES (%s,%s)",
                ("a" * 64, "keep-profile-0048"),
        )
    with pytest.raises(RuntimeError, match="Forward only: dropping RAG source chunk metadata"):
        _run_alembic(
            current_url, ["downgrade", "0045_account_closure_rag"],
            run_dir / "migration-0048-refused-downgrade.log", rag154_backends["issuer"],
        )
    with psycopg.connect(admin) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0048_rag_source_chunks"
        assert connection.execute("SELECT to_regclass('public.rag_document_upload_manifests')").fetchone()[0]
        assert connection.execute("SELECT to_regclass('public.rag_source_chunks')").fetchone()[0]
        assert connection.execute(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
            "WHERE oid='public.rag_source_chunks'::regclass"
        ).fetchone()[0]
        assert connection.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
            "AND tablename='rag_source_chunks' AND policyname='tenant_isolation_rag_source_chunks' "
            "AND qual ILIKE '%mentorflow.rag_account_scope%' "
            "AND with_check ILIKE '%mentorflow.rag_account_scope%'"
        ).fetchone()[0] == 1
        assert all(connection.execute(
            "SELECT has_table_privilege('mentorflow_app','public.rag_source_chunks',%s)", (privilege,)
        ).fetchone()[0] for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"))
        assert not connection.execute(
            "SELECT has_table_privilege('mentorflow_closure','public.rag_document_upload_manifests','SELECT')"
        ).fetchone()[0]
        assert not connection.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='public' "
            "AND tablename='rag_document_upload_manifests' AND policyname='account_closure_migration_read')"
        ).fetchone()[0]
        assert connection.execute(
            "SELECT nickname FROM public.account_profiles WHERE account_scope=%s", ("a" * 64,)
        ).fetchone()[0] == "keep-profile-0048"


def test_rag154_0047_to_0048_backfill_uses_only_unambiguous_canonical_layout(rag154_backends):
    """The migration role sees FORCE-RLS rows; promoted source metadata stays unknown."""
    postgres = rag154_backends["postgres"]
    name = "rag154_source_0048_" + uuid.uuid4().hex[:12]
    _app_url, admin_url, migration_url = _prepare_database(
        postgres, name, rag154_backends["issuer"], target="0047_rag_promoted_manifests"
    )
    admin = admin_url.replace("postgresql+psycopg://", "postgresql://")
    ordinary = ["  safe alpha \n", "稳定🙂\tbeta  "]
    promoted = ["promoted alpha", "promoted beta"]
    legacy_order = {
        "rag_documents": "account_scope,document_id",
        "rag_chunks": "account_scope,document_id,seq",
        "rag_document_upload_manifests": "account_scope,document_id",
        "rag_document_aliases": "account_scope,requested_document_id",
    }

    def snapshot_legacy(connection):
        return {
            table: connection.execute(
                f"SELECT * FROM public.{table} ORDER BY {order}"
            ).fetchall()
            for table, order in legacy_order.items()
        }

    def chunk_hashes(values):
        return [hashlib.sha256(value.encode("utf-8")).hexdigest() for value in values]

    with psycopg.connect(admin) as connection:
        for scope, document_id, values in (("a" * 64, "ordinary", ordinary), ("b" * 64, "promoted", promoted)):
            digest = hashlib.sha256(document_id.encode()).hexdigest()
            connection.execute(
                "INSERT INTO public.rag_documents(account_scope,document_id,content_sha256,title,source,total_chunks,status) "
                "VALUES (%s,%s,%s,%s,%s,2,'ready')",
                (scope, document_id, digest, document_id, "legacy"),
            )
            embedding_type = connection.execute(
                "SELECT format_type(a.atttypid,a.atttypmod) FROM pg_attribute a "
                "WHERE a.attrelid='public.rag_chunks'::regclass AND a.attname='embedding'"
            ).fetchone()[0]
            for seq, value in enumerate(values):
                if embedding_type == "bytea":
                    embedding = bytes(2048)
                    connection.execute(
                        "INSERT INTO public.rag_chunks(account_scope,document_id,seq,text,page,section,embedding) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (scope, document_id, seq, value, f"page-{document_id}-{seq}",
                         f"section-{document_id}-{seq}", embedding),
                    )
                else:
                    vector = "[" + ",".join("0" for _ in range(512)) + "]"
                    connection.execute(
                        "INSERT INTO public.rag_chunks(account_scope,document_id,seq,text,page,section,embedding) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s::vector)",
                        (scope, document_id, seq, value, f"page-{document_id}-{seq}",
                         f"section-{document_id}-{seq}", vector),
                    )
        hashes = chunk_hashes(promoted)
        connection.execute(
            "INSERT INTO public.rag_document_upload_manifests(account_scope,document_id,total_chunks,chunk_digests) "
            "VALUES (%s,'promoted',2,%s)", ("b" * 64, ",".join(hashes)),
        )
        connection.execute(
            "INSERT INTO public.rag_document_aliases(account_scope,requested_document_id,canonical_document_id,total_chunks,chunk_digests) "
            "VALUES (%s,'legacy-alias','ordinary',1,%s)",
            ("a" * 64, hashlib.sha256(b"alias with independent layout").hexdigest()),
        )
        legacy_before = snapshot_legacy(connection)

    run_dir = rag154_backends["directory"]
    _run_alembic(
        migration_url, ["upgrade", "0048_rag_source_chunks"],
        run_dir / "migration-0047-to-0048-source-layout.log", rag154_backends["issuer"],
    )
    with psycopg.connect(admin) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0048_rag_source_chunks"
        legacy_after_first = snapshot_legacy(connection)
        assert legacy_after_first == legacy_before
        rows = connection.execute(
            "SELECT account_scope,document_id,seq,text_sha256,byte_length,start_byte,end_byte,page,section "
            "FROM public.rag_source_chunks ORDER BY document_id,seq"
        ).fetchall()
        source_before_second = connection.execute(
            "SELECT * FROM public.rag_source_chunks ORDER BY account_scope,document_id,seq"
        ).fetchall()
        expected = []
        start = 0
        for seq, value in enumerate(ordinary):
            encoded = value.encode("utf-8")
            end = start + len(encoded)
            expected.append(("a" * 64, "ordinary", seq, hashlib.sha256(encoded).hexdigest(), len(encoded), start, end,
                             f"page-ordinary-{seq}", f"section-ordinary-{seq}"))
            start = end
        assert rows == expected
        assert connection.execute(
            "SELECT count(*) FROM public.rag_source_chunks WHERE document_id IN ('promoted','legacy-alias')"
        ).fetchone()[0] == 0
        assert not connection.execute(
            "SELECT has_table_privilege('mentorflow_closure','public.rag_document_upload_manifests','SELECT')"
        ).fetchone()[0]
        assert not connection.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='public' "
            "AND tablename='rag_document_upload_manifests' AND policyname='account_closure_migration_read')"
        ).fetchone()[0]

    _run_alembic(
        migration_url, ["upgrade", "0048_rag_source_chunks"],
        run_dir / "migration-0048-idempotent-source-layout.log", rag154_backends["issuer"],
    )
    with psycopg.connect(admin) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0048_rag_source_chunks"
        legacy_after_second = snapshot_legacy(connection)
        source_after_second = connection.execute(
            "SELECT * FROM public.rag_source_chunks ORDER BY account_scope,document_id,seq"
        ).fetchall()
        assert legacy_after_second == legacy_before
        assert source_after_second == source_before_second


def test_rag154_migration_requires_pgvector_to_be_preinstalled(rag154_backends):
    if os.environ.get("RAG154_VECTOR_BACKEND", "local-test") != "pgvector":
        pytest.skip("production pgvector requirement is exercised only with the official local pgvector build")
    run_dir = rag154_backends["directory"]
    postgres = rag154_backends["postgres"]
    name = "rag154_vector_" + uuid.uuid4().hex[:12]
    _app_url, _admin_url, url = _prepare_database(
        postgres, name, rag154_backends["issuer"], target="0045_account_closure_rag"
    )
    _run_alembic(url, ["downgrade", "0043_native_sessions"], run_dir / "vector-required-down.log", rag154_backends["issuer"])
    admin = url.replace("mentorflow_migrator:local-migrator-154", "postgres")
    admin = admin.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute("DROP EXTENSION vector")
    try:
        with pytest.raises(RuntimeError, match="Alembic upgrade .* failed"):
            _run_alembic(url, ["upgrade", "0045_account_closure_rag"], run_dir / "vector-required-failure.log", rag154_backends["issuer"])
        with (run_dir / "vector-required-failure.log").open(encoding="utf-8") as log:
            assert any("RAG_PGVECTOR_REQUIRED" in line for line in log)
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    _run_alembic(url, ["upgrade", "0045_account_closure_rag"], run_dir / "vector-required-restore.log", rag154_backends["issuer"])
    assert all(_tables(url))


def test_rag154_upload_is_idempotent_and_deduplicates_text_across_metadata(rag154_backends):
    chunks = [
        {"seq": 0, "text": "数据库事务保证一组操作要么全部成功，要么全部回滚。", "page": 1, "section": "事务"},
        {"seq": 1, "text": "索引可以加快数据库查询，但会增加写入与存储成本。", "page": 2, "section": "索引"},
    ]
    first = _upload(rag154_backends, "doc-primary", chunks, title="数据库教材", source="book://db")
    assert first.status_code == 200, first.text
    assert first.json() == {
        "document_id": "doc-primary", "requested_document_id": "doc-primary",
        "status": "ready", "received_chunks": 2, "total_chunks": 2, "deduplicated": False,
        "source_layout_status": "known", "source_layout_complete": True,
    }

    # Retrying the same identifier and exact payload is idempotent.
    retry = _upload(rag154_backends, "doc-primary", chunks, title="数据库教材", source="book://db")
    assert retry.status_code == 200, retry.text
    assert retry.json()["document_id"] == "doc-primary"
    assert retry.json()["status"] == "ready"

    # Content identity ignores display metadata and chunk location metadata.
    altered_metadata = [
        {"seq": 0, "text": chunks[0]["text"], "page": 88, "section": "另一个标题"},
        {"seq": 1, "text": chunks[1]["text"], "page": 89, "section": "另一个标题"},
    ]
    duplicate = _upload(rag154_backends, "doc-alias", altered_metadata,
                        title="换了标题", source="https://example.invalid/other")
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["document_id"] == "doc-primary"
    assert duplicate.json()["requested_document_id"] == "doc-alias"
    assert duplicate.json()["deduplicated"] is True
    documents = rag154_backends["client"].get(
        f"{API}/documents", headers=rag154_headers(rag154_backends)).json()["documents"]
    assert len(documents) == 1
    assert documents[0]["aliases"] == ["doc-alias"]
    with psycopg.connect(rag154_backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")) as db:
        assert db.execute("SELECT count(*) FROM rag_documents").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM rag_chunks").fetchone()[0] == 2


def test_rag154_account_isolation_and_delete_alias_vs_canonical(rag154_backends):
    client = rag154_backends["client"]
    alice_headers = rag154_headers(rag154_backends)
    baseline = {item["document_id"] for item in client.get(f"{API}/documents", headers=alice_headers).json()["documents"]}
    doc_id = "private-doc-a"
    uploaded = _upload(rag154_backends, doc_id,
                       [{"seq": 0, "text": "只有 Alice 账号可以访问这份资料。", "page": "1", "section": "隐私"}])
    assert uploaded.status_code == 200, uploaded.text
    with psycopg.connect(rag154_backends["app_url"].replace("postgresql+psycopg://", "postgresql://")) as app_db:
        # FORCE RLS denies direct app-role queries without repository-set scope,
        # and also with a different account scope.
        assert app_db.execute("SELECT count(*) FROM rag_documents").fetchone()[0] == 0
        app_db.execute("SELECT set_config('mentorflow.rag_account_scope',%s,false)", ("f" * 64,))
        assert app_db.execute("SELECT count(*) FROM rag_documents").fetchone()[0] == 0

    client = rag154_backends["client"]
    bob_headers = rag154_headers(rag154_backends, subject=rag154_backends["subject_b"])
    assert client.get(f"{API}/documents", headers=bob_headers).json()["documents"] == []
    hidden = client.post(f"{API}/search", headers=bob_headers,
                         json={"document_ids": [doc_id], "query": "Alice 的资料", "k": 5})
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["passages"] == []
    denied = client.delete(f"{API}/documents/{doc_id}", headers=bob_headers)
    assert denied.status_code == 404, denied.text

    # Deleting the alias detaches only that ID. Deleting its canonical promotes
    # the remaining alias, then deleting the final logical copy removes content.
    alias = _upload(rag154_backends, "private-doc-alias",
                    [{"seq": 0, "text": "只有 Alice 账号可以访问这份资料。", "page": 7, "section": "别名"}],
                    title="Other", source="local://other")
    assert alias.status_code == 200 and alias.json()["deduplicated"] is True
    detach = client.delete(f"{API}/documents/private-doc-alias",
                           headers=rag154_headers(rag154_backends))
    assert detach.status_code == 200 and detach.json()["content_deleted"] is False
    assert detach.json()["canonical_id"] == doc_id
    still_there = client.get(f"{API}/documents", headers=rag154_headers(rag154_backends)).json()["documents"]
    assert {item["document_id"] for item in still_there} == baseline | {doc_id}
    alias2 = _upload(rag154_backends, "private-doc-alias-2",
                     [{"seq": 0, "text": "只有 Alice 账号可以访问这份资料。", "page": 9, "section": "保留副本"}],
                     title="Other 2", source="local://other-2")
    assert alias2.status_code == 200 and alias2.json()["deduplicated"] is True
    deleted = client.delete(f"{API}/documents/{doc_id}", headers=rag154_headers(rag154_backends))
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["content_deleted"] is False
    assert deleted.json()["canonical_id"] == "private-doc-alias-2"
    promoted = client.get(f"{API}/documents", headers=alice_headers).json()["documents"]
    assert {item["document_id"] for item in promoted} == baseline | {"private-doc-alias-2"}
    assert "private-doc-alias-2" in {
        passage["document_id"] for passage in client.post(
            f"{API}/search", headers=alice_headers,
            json={"document_ids": ["private-doc-alias-2"], "query": "Alice 的资料", "k": 5},
        ).json()["passages"]
    }
    deleted_last = client.delete(f"{API}/documents/private-doc-alias-2", headers=alice_headers)
    assert deleted_last.status_code == 200
    assert deleted_last.json()["deleted"] is True
    assert deleted_last.json()["content_deleted"] is True
    assert deleted_last.json()["canonical_id"] is None
    after = client.get(f"{API}/documents", headers=alice_headers).json()["documents"]
    assert {item["document_id"] for item in after} == baseline


def test_rag154_search_filters_selected_documents_and_ranks_relevant_passages(rag154_backends):
    db_chunks = [
        {"seq": 0, "text": "PostgreSQL 使用事务确保多个数据库写入要么全部提交，要么全部回滚。", "page": 11, "section": "事务"},
        {"seq": 1, "text": "数据库索引使用额外空间换取查询速度，写入时也需要维护索引。", "page": 12, "section": "索引"},
    ]
    unrelated = [{"seq": 0, "text": "唐朝丝绸之路连接长安与中亚地区，促进商品和文化交流。", "page": 5, "section": "路线"}]
    assert _upload(rag154_backends, "search-db", db_chunks).status_code == 200
    assert _upload(rag154_backends, "search-history", unrelated, title="历史", source="local://history").status_code == 200
    client = rag154_backends["client"]
    headers = rag154_headers(rag154_backends)
    query = "PostgreSQL 数据库事务如何保证写入成功或回滚？"
    all_docs = client.post(f"{API}/search", headers=headers,
                           json={"document_ids": ["search-db", "search-history"], "query": query, "k": 3})
    assert all_docs.status_code == 200, all_docs.text
    passages = all_docs.json()["passages"]
    assert passages
    assert passages[0]["document_id"] == "search-db"
    assert "事务" in passages[0]["text"]
    scores = [item["score"] for item in passages]
    assert scores == sorted(scores, reverse=True)
    filtered = client.post(f"{API}/search", headers=headers,
                           json={"document_ids": ["search-db"], "query": query, "k": 10})
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["passages"]
    assert {item["document_id"] for item in filtered.json()["passages"]} == {"search-db"}
    assert all(isinstance(item["score"], (int, float)) for item in filtered.json()["passages"])


def test_rag154_http_body_accepts_exactly_9_mib_and_rejects_one_byte_more(rag154_backends):
    client = rag154_backends["client"]
    headers = rag154_headers(rag154_backends)
    payload = chunk_payload([{"seq": 0, "text": "边界内容", "page": None, "section": None}])
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # A trailing JSON whitespace byte is valid. Pad the raw request to exactly
    # the controller's documented 9 MiB cap without enlarging stored content.
    exact_body = compact + b" " * (9 * 1024 * 1024 - len(compact))
    assert len(exact_body) == 9 * 1024 * 1024
    accepted = client.post(f"{API}/documents/nine-mib/chunks", headers=headers, content=exact_body)
    assert accepted.status_code == 200, accepted.text[-1000:]
    assert accepted.json()["status"] == "ready"

    oversized_body = exact_body + b" "
    rejected = client.post(f"{API}/documents/nine-mib-plus-one/chunks", headers=headers,
                           content=oversized_body)
    assert rejected.status_code == 413, rejected.text[:1000]
    failure = rejected.json()
    assert failure["error"]["code"] == "RAG_REQUEST_TOO_LARGE"
    assert "request_id" in failure["error"]


def test_rag154_partial_batches_remain_hidden_until_complete(rag154_backends):
    client = rag154_backends["client"]
    headers = rag154_headers(rag154_backends, request_id="partial-upload")
    first = [{"seq": 0, "text": "第一段说明事务原子性。", "page": 1, "section": "事务"},
             {"seq": 1, "text": "第二段说明事务隔离级别。", "page": 2, "section": "隔离"}]
    body = {"title": "事务章节", "source": "book://tx", "total_chunks": 3, "chunks": first}
    initial = client.post(f"{API}/documents/partial-doc/chunks", headers=headers, json=body)
    assert initial.status_code == 200, initial.text
    assert initial.json()["status"] == "uploading"
    assert initial.json()["received_chunks"] == 2
    hidden = client.post(f"{API}/search", headers=headers,
                         json={"document_ids": ["partial-doc"], "query": "事务隔离", "k": 5})
    assert hidden.status_code == 200 and hidden.json()["passages"] == []

    retried = client.post(f"{API}/documents/partial-doc/chunks", headers=headers, json=body)
    assert retried.status_code == 200 and retried.json()["status"] == "uploading"
    last = {"title": "事务章节", "source": "book://tx", "total_chunks": 3,
            "chunks": [{"seq": 2, "text": "第三段说明事务提交与回滚。", "page": 3, "section": "提交"}]}
    completed = client.post(f"{API}/documents/partial-doc/chunks", headers=headers, json=last)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "ready"
    assert completed.json()["received_chunks"] == 3
    listing = client.get(f"{API}/documents", headers=headers).json()["documents"]
    assert next(item for item in listing if item["document_id"] == "partial-doc")["status"] == "ready"
    results = client.post(f"{API}/search", headers=headers,
                          json={"document_ids": ["partial-doc"], "query": "事务回滚", "k": 5})
    assert results.status_code == 200 and len(results.json()["passages"]) > 0


def test_rag154_content_alias_handles_different_chunk_boundaries(rag154_backends):
    client = rag154_backends["client"]
    headers = rag154_headers(rag154_backends, request_id="segmentation-dedup")
    # Both documents concatenate to identical UTF-8 text, but use different
    # chunk counts and boundaries; this must share one canonical content row.
    source_parts = ["JavaGuide 介绍 Java 后端，", "并说明 Agent 与 RAG。"]
    a_chunks = [{"seq": i, "text": part, "page": i + 1, "section": f"A-{i}"}
                for i, part in enumerate(source_parts)]
    a = client.post(f"{API}/documents/seg-a/chunks", headers=headers,
                    json={"title": "A", "source": "book://a", "total_chunks": 2, "chunks": a_chunks})
    assert a.status_code == 200 and a.json()["status"] == "ready", a.text

    b_text = ["JavaGuide ", "介绍 Java 后端，", "并说明 Agent 与 RAG。"]
    b_chunks = [{"seq": i, "text": text, "page": i + 20, "section": f"B-{i}"}
                for i, text in enumerate(b_text)]
    first = client.post(f"{API}/documents/seg-b/chunks", headers=headers,
                        json={"title": "B", "source": "book://b", "total_chunks": 3,
                              "chunks": b_chunks[:2]})
    assert first.status_code == 200 and first.json()["status"] == "uploading", first.text
    final = client.post(f"{API}/documents/seg-b/chunks", headers=headers,
                        json={"title": "B", "source": "book://b", "total_chunks": 3,
                              "chunks": b_chunks[2:]})
    assert final.status_code == 200 and final.json()["deduplicated"] is True, final.text
    assert final.json()["document_id"] == "seg-a"

    replay = client.post(f"{API}/documents/seg-b/chunks", headers=headers,
                         json={"title": "renamed", "source": "book://changed", "total_chunks": 3,
                               "chunks": b_chunks})
    assert replay.status_code == 200 and replay.json()["document_id"] == "seg-a", replay.text
    assert replay.json()["deduplicated"] is True
    partial_replay = client.post(f"{API}/documents/seg-b/chunks", headers=headers,
                                 json={"title": "B", "source": "book://b", "total_chunks": 3,
                                       "chunks": b_chunks[:1]})
    assert partial_replay.status_code == 200 and partial_replay.json()["status"] == "ready", partial_replay.text

    changed = [dict(b_chunks[0], text="Different content")]
    conflict = client.post(f"{API}/documents/seg-b/chunks", headers=headers,
                           json={"title": "B", "source": "book://b", "total_chunks": 3,
                                 "chunks": changed})
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "RAG_DOCUMENT_ID_CONFLICT"
