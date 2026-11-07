"""Logical source chunk locations survive content dedupe and canonical promotion."""
from support_rag154 import API, rag154_backends, rag154_headers


def test_rag_bug220_alias_uses_its_own_layout_after_dedupe_and_promotion(rag154_backends):
    client = rag154_backends["client"]
    text = "共享正文：第一段内容。第二段内容。"
    a_chunks = [
        {"seq": 0, "text": "共享正文：第一段内容。", "page": 1, "section": "A-第一"},
        {"seq": 1, "text": "第二段内容。", "page": 2, "section": "A-第二"},
    ]
    b_chunks = [{"seq": 0, "text": text, "page": 88, "section": "B-整页"}]
    a = client.post(f"{API}/documents/bug220-a/chunks", headers=rag154_headers(rag154_backends, request_id="bug220-a"), json={
        "title": "A", "source": "local://bug220", "total_chunks": 2, "chunks": a_chunks,
    })
    assert a.status_code == 200, a.text
    b = client.post(f"{API}/documents/bug220-b/chunks", headers=rag154_headers(rag154_backends, request_id="bug220-b"), json={
        "title": "B", "source": "local://bug220", "total_chunks": 1, "chunks": b_chunks,
    })
    assert b.status_code == 200, b.text
    assert b.json()["deduplicated"] is True
    assert b.json()["source_layout_status"] == "known"

    listing = client.get(f"{API}/documents", headers=rag154_headers(rag154_backends, request_id="bug220-list"))
    assert listing.status_code == 200, listing.text
    canonical = next(row for row in listing.json()["documents"] if row["document_id"] == "bug220-a")
    layouts = {row["requested_document_id"]: row for row in canonical["source_layouts"]}
    assert layouts["bug220-b"] == {"requested_document_id": "bug220-b", "total_chunks": 1, "complete": True}

    result = client.post(f"{API}/search", headers=rag154_headers(rag154_backends, request_id="bug220-search-b"), json={
        "query": "第二段内容", "document_ids": ["bug220-b"], "k": 5,
    })
    assert result.status_code == 200, result.text
    assert result.json()["passages"]
    assert all(row["document_id"] == "bug220-b" for row in result.json()["passages"])
    assert result.json()["passages"][0]["seq"] == 0
    assert result.json()["passages"][0]["text"] == text
    assert result.json()["passages"][0]["page"] == "88"
    assert result.json()["passages"][0]["section"] == "B-整页"
    assert result.json()["passages"][0]["location_status"] == "known"

    both = client.post(f"{API}/search", headers=rag154_headers(rag154_backends, request_id="bug220-search-both"), json={
        "query": "第二段内容", "document_ids": ["bug220-a", "bug220-b"], "k": 5,
    })
    assert both.status_code == 200, both.text
    assert {row["document_id"] for row in both.json()["passages"]} == {"bug220-a", "bug220-b"}
    b_rows = [row for row in both.json()["passages"] if row["document_id"] == "bug220-b"]
    assert len(b_rows) == 1
    assert b_rows[0]["page"] == "88" and b_rows[0]["section"] == "B-整页"
    assert {row["seq"]: row["text"] for row in both.json()["passages"]
            if row["document_id"] == "bug220-a"} == {row["seq"]: row["text"] for row in a_chunks}
    first_only = client.post(f"{API}/search", headers=rag154_headers(rag154_backends), json={
        "query": "第二段内容", "document_ids": ["bug220-a", "bug220-b"], "k": 1,
    })
    assert first_only.status_code == 200, first_only.text
    assert first_only.json()["passages"] == both.json()["passages"][:1]

    opt_in = client.post(f"{API}/search", headers=rag154_headers(rag154_backends, request_id="bug220-search-v1"), json={
        "query": "第二段内容", "document_ids": ["bug220-b"], "k": 5, "source_layout_v1": True,
    })
    assert opt_in.status_code == 200, opt_in.text
    assert opt_in.json()["passages"][0]["document_id"] == "bug220-b"
    assert opt_in.json()["passages"][0]["source_layout_status"] == "known"
    bad_flag = client.post(f"{API}/search", headers=rag154_headers(rag154_backends, request_id="bug220-search-badflag"), json={
        "query": "第二段内容", "document_ids": ["bug220-b"], "source_layout_v1": "true",
    })
    assert bad_flag.status_code == 422

    # A historical one-chunk alias has no locations; its full-content digest still proves ownership.
    import psycopg
    with psycopg.connect(rag154_backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")) as db:
        db.execute("DELETE FROM rag_source_chunks WHERE document_id=%s", ("bug220-b",))
    unknown = client.post(f"{API}/search", headers=rag154_headers(rag154_backends), json={
        "query": "第二段内容", "document_ids": ["bug220-b"], "k": 5, "source_layout_v1": True})
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["passages"]
    assert all(row["document_id"] == "bug220-b" and row["seq"] is None
               and row["page"] is None and row["section"] is None
               and row["source_layout_status"] == "missing" for row in unknown.json()["passages"])
    restored = client.post(f"{API}/documents/bug220-b/chunks", headers=rag154_headers(rag154_backends), json={
        "title": "B", "source": "local://bug220", "total_chunks": 1, "chunks": b_chunks})
    assert restored.status_code == 200 and restored.json()["source_layout_complete"] is True, restored.text

    deleted = client.delete(f"{API}/documents/bug220-a", headers=rag154_headers(rag154_backends, request_id="bug220-promote"))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["canonical_id"] == "bug220-b"
    after = client.post(f"{API}/search", headers=rag154_headers(rag154_backends, request_id="bug220-after"), json={
        "query": "第二段内容", "document_ids": ["bug220-b"], "k": 5,
    })
    assert after.status_code == 200, after.text
    assert after.json()["passages"]
    assert after.json()["passages"][0]["document_id"] == "bug220-b"
    assert after.json()["passages"][0]["text"] == text
    assert after.json()["passages"][0]["page"] == "88"
    promoted_list = client.get(f"{API}/documents", headers=rag154_headers(rag154_backends)).json()["documents"]
    promoted = next(row for row in promoted_list if row["document_id"] == "bug220-b")
    assert promoted["source_layouts"] == [{"requested_document_id": "bug220-b", "total_chunks": 1, "complete": True}]


def test_rag_bug220_ready_upload_replay_is_idempotent_and_conflicting_metadata_fails(rag154_backends):
    client = rag154_backends["client"]
    chunks = [
        {"seq": 0, "text": "固定来源元数据校验正文", "page": "9", "section": "原章节"},
    ]
    created = client.post(f"{API}/documents/bug220-layout/chunks", headers=rag154_headers(rag154_backends, request_id="bug220-layout-create"), json={
        "title": "位置回填", "source": "local://bug220-layout", "total_chunks": 1, "chunks": chunks,
    })
    assert created.status_code == 200, created.text
    same = client.post(f"{API}/documents/bug220-layout/chunks", headers=rag154_headers(rag154_backends, request_id="bug220-layout-replay"), json={
        "title": "位置回填", "source": "local://bug220-layout", "total_chunks": 1, "chunks": chunks,
    })
    assert same.status_code == 200, same.text
    assert same.json()["source_layout_status"] == "known"
    changed = client.post(f"{API}/documents/bug220-layout/chunks", headers=rag154_headers(rag154_backends, request_id="bug220-layout-conflict"), json={
        "title": "位置回填", "source": "local://bug220-layout", "total_chunks": 1, "chunks": [{**chunks[0], "page": "10"}],
    })
    assert changed.status_code == 409, changed.text


def test_rag_bug220_partial_ready_replay_restores_an_unknown_layout(rag154_backends):
    import psycopg

    client = rag154_backends["client"]
    source = [
        {"seq": 0, "text": "旧位置回填第一块", "page": "41", "section": "新来源一"},
        {"seq": 1, "text": "旧位置回填第二块", "page": "42", "section": "新来源二"},
    ]
    common = {"title": "回填源", "source": "local://bug220-backfill", "total_chunks": 2}
    a = client.post(f"{API}/documents/bug220-backfill-a/chunks", headers=rag154_headers(rag154_backends), json={
        **common, "chunks": [{**source[0], "page": "1", "section": "A1"}, {**source[1], "page": "2", "section": "A2"}],
    })
    assert a.status_code == 200, a.text
    b = client.post(f"{API}/documents/bug220-backfill-b/chunks", headers=rag154_headers(rag154_backends), json={
        **common, "chunks": source,
    })
    assert b.status_code == 200, b.text
    with psycopg.connect(rag154_backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")) as db:
        db.execute("DELETE FROM rag_source_chunks WHERE document_id=%s", ("bug220-backfill-b",))
    missing = client.post(f"{API}/search", headers=rag154_headers(rag154_backends), json={
        "query": "旧位置回填第一块", "document_ids": ["bug220-backfill-b"], "k": 5, "source_layout_v1": True,
    })
    assert missing.status_code == 200, missing.text
    assert missing.json()["passages"]
    assert all(row["document_id"] == "bug220-backfill-b" for row in missing.json()["passages"])
    assert all(row["seq"] is None and row["page"] is None and row["section"] is None for row in missing.json()["passages"])
    assert all(row["source_layout_status"] == "missing" for row in missing.json()["passages"])

    first = client.post(f"{API}/documents/bug220-backfill-b/chunks", headers=rag154_headers(rag154_backends), json={
        **common, "chunks": [source[0]],
    })
    assert first.status_code == 200, first.text
    assert first.json()["source_layout_complete"] is False
    second = client.post(f"{API}/documents/bug220-backfill-b/chunks", headers=rag154_headers(rag154_backends), json={
        **common, "chunks": [source[1]],
    })
    assert second.status_code == 200, second.text
    assert second.json()["source_layout_complete"] is True


def test_rag_bug220_missing_optional_locations_replay_unchanged(rag154_backends):
    client = rag154_backends["client"]
    body = {"title": "无页码来源", "source": "local://bug220-null", "total_chunks": 1,
            "chunks": [{"seq": 0, "text": "原始来源没有页码或章节，但精确重传仍然幂等。"}]}
    for _ in range(2):
        response = client.post(f"{API}/documents/bug220-null/chunks",
                               headers=rag154_headers(rag154_backends), json=body)
        assert response.status_code == 200, response.text
        assert response.json()["source_layout_complete"] is True


def test_rag_bug220_old_alias_backfill_maps_prefix_after_canonical_append(rag154_backends):
    from hashlib import sha256
    import psycopg

    client = rag154_backends["client"]
    first = {"seq": 0, "text": "  原资料😀正文。\n", "page": "1", "section": "A原块"}
    tail = {"seq": 1, "text": "追加的新资料正文。", "page": "2", "section": "A新增"}
    for doc, chunk in [("bug220-prefix-a", first),
                       ("bug220-prefix-b", {**first, "page": "88", "section": "B原块"})]:
        response = client.post(f"{API}/documents/{doc}/chunks", headers=rag154_headers(rag154_backends), json={
            "title": "原资料", "source": "local://bug220-prefix", "total_chunks": 1, "chunks": [chunk]})
        assert response.status_code == 200, response.text
    chunks = [first, tail]
    repaired = client.post(f"{API}/documents/bug220-prefix-a/chunks",
                           headers=rag154_headers(rag154_backends), json={
        "title": "原资料", "source": "local://bug220-prefix", "total_chunks": 2,
        "repair_existing": True, "repair_manifest": [sha256(c["text"].encode()).hexdigest() for c in chunks],
        "repair_content_sha256": sha256("".join(c["text"] for c in chunks).encode()).hexdigest(), "chunks": chunks})
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["repair_complete"] is True
    with psycopg.connect(rag154_backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")) as db:
        db.execute("DELETE FROM rag_source_chunks WHERE document_id=%s", ("bug220-prefix-b",))
    backfilled = client.post(f"{API}/documents/bug220-prefix-b/chunks",
                             headers=rag154_headers(rag154_backends), json={
        "title": "原资料", "source": "local://bug220-prefix", "total_chunks": 1,
        "chunks": [{**first, "page": "88", "section": "B原块"}]})
    assert backfilled.status_code == 200, backfilled.text
    assert backfilled.json()["source_layout_complete"] is True
    selected = client.post(f"{API}/search", headers=rag154_headers(rag154_backends), json={
        "query": "原资料正文 追加的新资料正文", "document_ids": ["bug220-prefix-b"],
        "k": 5, "source_layout_v1": True})
    assert selected.status_code == 200, selected.text
    assert selected.json()["passages"]
    assert all(row["text"] == first["text"] and row["page"] == "88"
               for row in selected.json()["passages"])
