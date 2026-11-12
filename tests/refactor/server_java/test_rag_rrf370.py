"""370-MF04 online keyword retrieval and dense/lexical RRF contracts."""
from __future__ import annotations

import hashlib

import psycopg

from support_rag154 import API, chunk_payload, rag154_backends, rag154_headers


def _upload(backends: dict, document_id: str, chunks: list[dict], *, subject: str | None = None):
    owner = subject or backends["subject_a"]
    return backends["client"].post(
        f"{API}/documents/{document_id}/chunks",
        headers=rag154_headers(backends, subject=owner, request_id=f"upload-rrf370-{document_id}"),
        json=chunk_payload(chunks, title="RRF370", source="local://rag-rrf370"),
    )


def _search(backends: dict, document_ids: list[str], query: str, *, subject: str | None = None, k: int = 10):
    owner = subject or backends["subject_a"]
    return backends["client"].post(
        f"{API}/search",
        headers=rag154_headers(backends, subject=owner, request_id=f"search-rrf370-{owner}"),
        json={"document_ids": document_ids, "query": query, "k": k},
    )


def _delete(backends: dict, document_id: str, *, subject: str | None = None):
    owner = subject or backends["subject_a"]
    return backends["client"].delete(
        f"{API}/documents/{document_id}",
        headers=rag154_headers(backends, subject=owner, request_id=f"delete-rrf370-{document_id}"),
    )


def _set_equal_test_vectors(backends: dict, document_id: str, subject: str) -> None:
    """Make the local bytea vector path tie by seq so the target is outside dense top-20."""
    if __import__("os").environ.get("RAG154_VECTOR_BACKEND", "local-test") != "local-test":
        raise AssertionError("RRF370 vector ranking fixture requires the local-test bytea backend")
    scope = hashlib.sha256((backends["issuer"] + "\0" + subject).encode("utf-8")).hexdigest()
    with psycopg.connect(backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")) as db:
        db.execute(
            "UPDATE rag_chunks SET embedding=%s WHERE account_scope=%s AND document_id=%s",
            (bytes(2048), scope, document_id),
        )


def test_rag_rrf370_keyword_recovers_target_outside_dense_top20(rag154_backends):
    document_id = "rrf370-keyword-recovery"
    chunks = [
        {"seq": seq, "text": f"无关向量候选 {seq} Java 基础说明。", "page": seq + 1, "section": "噪声"}
        for seq in range(21)
    ]
    chunks.append({
        "seq": 21,
        "text": "RRF370KEYWORDUNIQUE 只在这一段出现的关键词答案。",
        "page": 22,
        "section": "关键词答案",
    })
    uploaded = _upload(rag154_backends, document_id, chunks)
    assert uploaded.status_code == 200, uploaded.text
    _set_equal_test_vectors(rag154_backends, document_id, rag154_backends["subject_a"])

    response = _search(rag154_backends, [document_id], "RRF370KEYWORDUNIQUE", k=20)
    assert response.status_code == 200, response.text
    passages = response.json()["passages"]
    assert any(int(row["seq"]) == 21 for row in passages)
    assert all(row["document_id"] == document_id for row in passages)


def test_rag_rrf370_search_respects_selected_documents(rag154_backends):
    selected = "rrf370-selected"
    other = "rrf370-unselected"
    assert _upload(
        rag154_backends, selected,
        [{"seq": 0, "text": "选中资料的普通内容。", "page": 1, "section": "选中"}],
    ).status_code == 200
    assert _upload(
        rag154_backends, other,
        [{"seq": 0, "text": "RRF370SCOPEONLY 只属于未选资料。", "page": 1, "section": "未选"}],
    ).status_code == 200

    selected_only = _search(rag154_backends, [selected], "RRF370SCOPEONLY")
    assert selected_only.status_code == 200, selected_only.text
    assert all(row["document_id"] == selected for row in selected_only.json()["passages"])
    both = _search(rag154_backends, [selected, other], "RRF370SCOPEONLY")
    assert both.status_code == 200, both.text
    assert any(row["document_id"] == other for row in both.json()["passages"])


def test_rag_rrf370_deleted_document_is_not_keyword_visible(rag154_backends):
    document_id = "rrf370-deleted"
    assert _upload(
        rag154_backends, document_id,
        [{"seq": 0, "text": "RRF370DELETEDONLY 删除后不应再检索到。", "page": 1, "section": "删除"}],
    ).status_code == 200
    deleted = _delete(rag154_backends, document_id)
    assert deleted.status_code == 200, deleted.text
    response = _search(rag154_backends, [document_id], "RRF370DELETEDONLY")
    assert response.status_code == 200, response.text
    assert response.json()["passages"] == []


def test_rag_rrf370_accounts_cannot_see_each_other_keyword_matches(rag154_backends):
    document_id = "rrf370-account-shared-id"
    subject_a, subject_b = rag154_backends["subject_a"], rag154_backends["subject_b"]
    assert _upload(
        rag154_backends, document_id,
        [{"seq": 0, "text": "RRF370ACCOUNT_A 甲账号私有答案。", "page": 1, "section": "甲"}],
        subject=subject_a,
    ).status_code == 200
    assert _upload(
        rag154_backends, document_id,
        [{"seq": 0, "text": "RRF370ACCOUNT_B 乙账号私有答案。", "page": 1, "section": "乙"}],
        subject=subject_b,
    ).status_code == 200

    a_result = _search(rag154_backends, [document_id], "RRF370ACCOUNT_A", subject=subject_a)
    b_result = _search(rag154_backends, [document_id], "RRF370ACCOUNT_B", subject=subject_b)
    assert a_result.status_code == b_result.status_code == 200
    assert any("RRF370ACCOUNT_A" in row["text"] for row in a_result.json()["passages"])
    assert all("RRF370ACCOUNT_B" not in row["text"] for row in a_result.json()["passages"])
    assert any("RRF370ACCOUNT_B" in row["text"] for row in b_result.json()["passages"])
    assert all("RRF370ACCOUNT_A" not in row["text"] for row in b_result.json()["passages"])
