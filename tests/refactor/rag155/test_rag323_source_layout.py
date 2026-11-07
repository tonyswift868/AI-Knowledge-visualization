"""323: client-side source-layout protocol compatibility contracts."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from refactor.learning.app.rag import API, _match_source_chunk
from tests.refactor.rag155.test_rag155_backend import FakeCourseStore, service
from tests.refactor.rag155.test_rag226_content_version import course_store


def test_rag323_local_provenance_requires_complete_unique_text():
    chunks = [{"text": " first body\n中文 ", "page": 88, "section": "local A"},
              {"text": "second body", "page": 99, "section": "local B"}]
    assert _match_source_chunk(chunks, " first body\n中文 ") == (0, chunks[0])
    assert _match_source_chunk(chunks, "second body") == (1, chunks[1])
    assert _match_source_chunk(chunks, "first body\n中文") is None
    assert _match_source_chunk(chunks, "body") is None
    assert _match_source_chunk([], "second body") is None


def test_rag323_repeated_text_needs_a_proven_logical_sequence():
    chunks = [{"text": "same body", "page": 2}, {"text": "same body", "page": 88}]
    assert _match_source_chunk(chunks, "same body") is None
    assert _match_source_chunk(chunks, "same body", 1) == (1, chunks[1])
    assert _match_source_chunk(chunks, "same body", -1) is None
    assert _match_source_chunk(chunks, "same body", True) is None
    assert _match_source_chunk(chunks, "different text", 1) is None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class LayoutServer:
    """Small fixed-contract MockTransport endpoint; no server implementation logic."""
    def __init__(self, documents, *, capabilities=(), passages=(), upload_response=None):
        self.documents = documents
        self.capabilities = list(capabilities)
        self.passages = list(passages)
        self.upload_response = upload_response or {}
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if request.method == "GET" and request.url.path == API + "/documents":
            return httpx.Response(200, json={"documents": self.documents,
                                             "capabilities": self.capabilities})
        if request.method == "POST" and request.url.path.endswith("/chunks"):
            return httpx.Response(200, json=self.upload_response)
        if request.method == "POST" and request.url.path == API + "/search":
            self.search_body = json.loads(request.content)
            return httpx.Response(200, json={"passages": self.passages})
        return httpx.Response(404, json={"error": "unexpected request"})


def _remote_row(document_id, *, aliases=(), layouts=(), total=1, received=1):
    row = {"document_id": document_id, "title": "Doc 0", "source": "doc-0.txt",
           "status": "ready", "total_chunks": total, "received_chunks": received,
           "aliases": list(aliases)}
    if layouts is not None:
        row["source_layouts"] = list(layouts)
    return row


def test_rag323_confirmed_body_repairs_incomplete_source_layout_then_opts_in(tmp_path):
    text = "独立来源正文。"
    digest = _digest("source-layout-repair")
    layout = {"requested_document_id": digest, "total_chunks": 1, "complete": False}
    fake = LayoutServer([_remote_row(digest, layouts=[layout])],
        capabilities=["source_layout_v1"],
        upload_response={"document_id": digest, "requested_document_id": digest,
                         "status": "ready", "received_chunks": 1, "total_chunks": 1,
                         "deduplicated": False, "source_layout_complete": True},
        passages=[{"document_id": digest, "requested_document_id": digest,
                   "seq": 0, "text": text, "page": 88, "section": "本来源章节"}])
    store = course_store(tmp_path, [(digest, text)])
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        instance._course_documents()
        row = instance.local.document(digest)
        instance.local.set_uploaded_content(digest, row["current_content_sha256"])
        instance.local.set_remote_state(digest, digest, "ready", 1, 1)

        result = instance.ingest([digest])["documents"][0]
        passages = instance.search_plan("plan-0", "正文")["passages"]

        assert result["status"] == "ready"
        assert instance.local.document(digest)["remote_upload_id"] == digest
        posts = [request for request in fake.requests if request.method == "POST"
                 and request.url.path.endswith("/chunks")]
        assert len(posts) == 1
        body = json.loads(posts[0].content)
        assert [(chunk["seq"], chunk["text"], chunk["page"], chunk["section"])
                for chunk in body["chunks"]] == [(0, text, 1, "正文")]
        assert fake.search_body["source_layout_v1"] is True
        assert fake.search_body["document_ids"] == [digest]
        assert passages[0]["chunk_id"] == "chunk-0"
        assert (passages[0]["seq"], passages[0]["page"], passages[0]["section"]) == (
            0, 88, "本来源章节")
    finally:
        instance.close()


def test_rag323_legacy_server_uses_unique_local_exact_match_without_opt_in(tmp_path):
    text = "唯一的本地原块正文。"
    digest = _digest("legacy-source")
    fake = LayoutServer([_remote_row(digest, layouts=None)],
        passages=[{"document_id": digest, "seq": 4, "text": text,
                   "page": 99, "section": "canonical位置"}])
    store = course_store(tmp_path, [(digest, text)])
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        instance._course_documents()
        local = instance.local.document(digest)
        instance.local.set_uploaded_content(digest, local["current_content_sha256"])
        instance.local.set_remote_state(digest, digest, "ready", 1, 1)

        result = instance.search_plan("plan-0", "正文")

        assert "source_layout_v1" not in fake.search_body
        assert result["passages"] == [{"chunk_id": "chunk-0", "text": text,
            "title": "Doc 0", "page": 1, "section": "正文",
            "document_id": digest, "seq": 0}]
    finally:
        instance.close()


def test_rag323_missing_layout_without_local_chunks_keeps_text_and_unknown_location(tmp_path):
    text = "远端命中的正文仍需可见。"
    digest = _digest("missing-source-chunks")
    fake = LayoutServer([_remote_row(digest, layouts=[{
        "requested_document_id": digest, "total_chunks": 1, "complete": False}])],
        capabilities=["source_layout_v1"], passages=[{
            "document_id": digest, "requested_document_id": digest,
            "seq": None, "text": text, "page": None, "section": None,
            "source_layout_status": "missing"}],
        upload_response={"document_id": digest, "requested_document_id": digest,
                         "status": "ready", "received_chunks": 1,
                         "total_chunks": 1, "source_layout_complete": False})
    store = FakeCourseStore(tmp_path / "account",
        plans=[{"id": "plan-missing", "title": "Plan"}],
        sources={"plan-missing": [{"id": "source-missing", "adopted": True,
            "chunk_count": 1, "sha256": digest, "title": "资料", "filename": "doc.txt"}]},
        chunks={"plan-missing": []})
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        instance.local.save_document(digest, "资料", "doc.txt", [{"text": "先前本地快照。"}], status="ready")
        local = instance.local.document(digest)
        instance.local.set_uploaded_content(digest, local["current_content_sha256"])
        instance.local.set_remote_upload_id(digest, digest)
        instance.local.set_remote_state(digest, digest, "ready", 1, 1)

        result = instance.search_plan("plan-missing", "正文")

        assert fake.search_body["source_layout_v1"] is True
        assert result["passages"] == [{"chunk_id": None, "text": text,
            "title": "资料", "page": None, "section": None,
            "document_id": digest, "seq": None,
            "source_layout_status": "missing"}]
        assert not any(request.method == "POST" and request.url.path.endswith("/chunks")
                       for request in fake.requests)
    finally:
        instance.close()


def test_rag323_shared_canonical_search_preserves_both_requested_source_ids(tmp_path):
    shared = "两个来源共有的完整正文。"
    first, second = _digest("logical-source-first"), _digest("logical-source-second")
    canonical = "shared-canonical"
    fake = LayoutServer([_remote_row(canonical, aliases=[first, second], layouts=[
        {"requested_document_id": first, "total_chunks": 1, "complete": True},
        {"requested_document_id": second, "total_chunks": 1, "complete": True}])],
        capabilities=["source_layout_v1"], passages=[
            {"document_id": first, "requested_document_id": first, "seq": 0,
             "text": shared, "page": 4, "section": "A章"},
            {"document_id": second, "requested_document_id": second, "seq": 0,
             "text": shared, "page": 9, "section": "B章"},
        ])
    store = FakeCourseStore(tmp_path / "account", plans=[{"id": "shared-plan", "title": "Shared"}],
        sources={"shared-plan": [
            {"id": "source-0", "adopted": True, "chunk_count": 1,
             "sha256": first, "title": "Doc 0", "filename": "doc-0.txt"},
            {"id": "source-1", "adopted": True, "chunk_count": 1,
             "sha256": second, "title": "Doc 1", "filename": "doc-1.txt"},
        ]}, chunks={"shared-plan": [
            {"id": "chunk-0", "source_id": "source-0", "position": 0,
             "text": shared, "page_start": 1, "section": ["正文"]},
            {"id": "chunk-1", "source_id": "source-1", "position": 0,
             "text": shared, "page_start": 2, "section": ["正文"]},
        ]})
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        instance._course_documents()
        for digest in (first, second):
            local = instance.local.document(digest)
            instance.local.set_uploaded_content(digest, local["current_content_sha256"])
            instance.local.set_remote_state(digest, canonical, "ready", 1, 1)

        result = instance.search_plan("shared-plan", "共有正文")

        assert fake.search_body["source_layout_v1"] is True
        assert fake.search_body["document_ids"] == [first, second]
        assert [(row["document_id"], row["chunk_id"], row["page"], row["section"])
                for row in result["passages"]] == [
                    (first, "chunk-0", 4, "A章"),
                    (second, "chunk-1", 9, "B章"),
                ]
    finally:
        instance.close()


def test_rag323_layout_backfill_keeps_raw_utf8_until_all_batches_arrive(tmp_path, monkeypatch):
    digest = _digest("raw-utf8-layout")
    chunks = [{"text": " \n甲😀 ", "page": 1, "section": "A"},
              {"text": "乙\n ", "page": 2, "section": "B"}]

    class BatchedLayoutServer(LayoutServer):
        def __init__(self):
            super().__init__([_remote_row(digest, total=2, received=2, layouts=[{
                "requested_document_id": digest, "total_chunks": 2, "complete": False}])],
                capabilities=["source_layout_v1"])
            self.parts = []

        def __call__(self, request):
            if request.method == "POST" and request.url.path.endswith("/chunks"):
                self.requests.append(request)
                self.parts.append(json.loads(request.content)["chunks"])
                return httpx.Response(200, json={"document_id": digest,
                    "requested_document_id": digest, "status": "ready",
                    "received_chunks": 2, "total_chunks": 2,
                    "source_layout_complete": len(self.parts) == 2})
            return super().__call__(request)

    fake = BatchedLayoutServer()
    instance, _, _ = service(tmp_path, fake)
    monkeypatch.setattr("refactor.learning.app.rag.BATCH_CHUNKS", 1)
    try:
        instance.local.save_document(digest, "原文", "raw.txt", chunks)
        row = instance.local.document(digest)
        instance.local.set_uploaded_content(digest, row["current_content_sha256"])
        instance.local.set_remote_state(digest, digest, "ready", 2, 2)
        completed = []
        result = instance.ingest([digest], on_progress=lambda _: completed.append(
            instance._source_layout_complete.get(digest)))["documents"][0]
        assert result["status"] == "ready"
        assert [part[0]["text"] for part in fake.parts] == [row["text"] for row in chunks]
        assert completed[0] is False and completed[-1] is True
        assert instance._source_layout_complete[digest] is True
        assert instance.local.document(digest)["remote_upload_id"] == digest
    finally:
        instance.close()


def test_rag323_layout_of_version_alias_does_not_repair_its_canonical(tmp_path):
    digest, target = _digest("local-source-with-version-alias"), "content-version-own-source"
    text = "相同全文，但本来源只有一个原块。"
    fake = LayoutServer([_remote_row(digest, aliases=[target], total=2, received=2, layouts=[
        {"requested_document_id": digest, "total_chunks": 2, "complete": True},
        {"requested_document_id": target, "total_chunks": 1, "complete": False}])],
        capabilities=["source_layout_v1"], upload_response={
            "document_id": digest, "requested_document_id": target, "status": "ready",
            "received_chunks": 2, "total_chunks": 2, "deduplicated": True,
            "source_layout_complete": True})
    instance, _, _ = service(tmp_path, fake)
    try:
        instance.local.save_document(digest, "本来源", "own.txt", [{"text": text, "page": 88}])
        row = instance.local.document(digest)
        instance.local.set_remote_upload_id(digest, target)
        instance.local.set_uploaded_content(digest, row["current_content_sha256"])
        instance.local.set_remote_state(digest, digest, "ready", 2, 2)
        assert instance.ingest([digest])["documents"][0]["status"] == "ready"
        posts = [request for request in fake.requests if request.method == "POST"]
        assert len(posts) == 1 and posts[0].url.path == f"{API}/documents/{target}/chunks"
        assert "repair_existing" not in json.loads(posts[0].content)
        assert instance.local.document(digest)["remote_upload_id"] == target
    finally:
        instance.close()


def test_rag323_plan_duplicate_text_uses_requested_source_sequence(tmp_path):
    digest, text = _digest("repeated-source-text"), "重复正文。"
    store = course_store(tmp_path, [(digest, text)])
    store._sources["plan-0"][0]["chunk_count"] = 2
    store._chunks["plan-0"].append({"id": "chunk-second", "source_id": "source-0",
        "position": 1, "text": text, "page_start": 88, "section": ["第二处"]})
    fake = LayoutServer([_remote_row(digest, total=2, received=2, layouts=[{
        "requested_document_id": digest, "total_chunks": 2, "complete": True}])],
        capabilities=["source_layout_v1"], passages=[{
            "document_id": digest, "seq": 1, "text": text, "page": 88, "section": "第二处"}])
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        instance._course_documents()
        row = instance.local.document(digest)
        instance.local.set_uploaded_content(digest, row["current_content_sha256"])
        instance.local.set_remote_state(digest, digest, "ready", 2, 2)
        passage = instance.search_plan("plan-0", "正文")["passages"][0]
        assert passage["chunk_id"] == "chunk-second"
        assert (passage["seq"], passage["page"], passage["section"]) == (1, 88, "第二处")
    finally:
        instance.close()


def test_rag323_layout_conflict_preserves_target_without_body_version(tmp_path):
    digest = _digest("layout-conflict")

    class RefusedLayoutServer(LayoutServer):
        def __call__(self, request):
            if request.method == "POST" and request.url.path.endswith("/chunks"):
                self.requests.append(request)
                return httpx.Response(409, json={"error": {"code": "RAG_SOURCE_LAYOUT_CONFLICT"}})
            return super().__call__(request)

    fake = RefusedLayoutServer([_remote_row(digest, layouts=[{
        "requested_document_id": digest, "total_chunks": 1, "complete": False}])],
        capabilities=["source_layout_v1"])
    instance, _, _ = service(tmp_path, fake)
    try:
        instance.local.save_document(digest, "布局", "layout.txt", [{"text": "正文不变。", "page": 88}])
        row = instance.local.document(digest)
        instance.local.set_uploaded_content(digest, row["current_content_sha256"])
        instance.local.set_remote_state(digest, digest, "ready", 1, 1)
        assert instance.ingest([digest])["documents"][0]["status"] == "failed"
        assert instance.local.document(digest)["remote_upload_id"] == digest
        posts = [request for request in fake.requests if request.method == "POST"]
        assert len(posts) == 1 and posts[0].url.path == f"{API}/documents/{digest}/chunks"
        assert instance._source_layout_complete[digest] is False
        assert instance.local.document(digest)["uploaded_content_sha256"] == row["current_content_sha256"]
    finally:
        instance.close()


@pytest.mark.parametrize("status,code", [(409, "RAG_SOURCE_LAYOUT_CONFLICT"), (503, "RAG_STORAGE_UNAVAILABLE")])
def test_rag323_repair_failure_only_versions_real_content_conflicts(tmp_path, status, code):
    digest = _digest(f"repair-error-{status}")

    class RefusedRepairServer(LayoutServer):
        def __call__(self, request):
            if request.method == "POST" and request.url.path.endswith("/chunks"):
                self.requests.append(request)
                return httpx.Response(status, json={"error": {"code": code}})
            return super().__call__(request)

    fake = RefusedRepairServer([_remote_row(digest)], capabilities=["append_only_chunk_repair_v1"])
    instance, _, _ = service(tmp_path, fake)
    try:
        instance.local.save_document(digest, "补传", "repair.txt", [{"text": "首块"}, {"text": "次块"}])
        assert instance.ingest([digest])["documents"][0]["status"] == "failed"
        assert instance.local.document(digest)["remote_upload_id"] == digest
        posts = [request for request in fake.requests if request.method == "POST"]
        assert len(posts) == 1 and json.loads(posts[0].content)["repair_existing"] is True
    finally:
        instance.close()
