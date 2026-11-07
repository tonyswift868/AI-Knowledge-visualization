"""323: QA send persists source-specific provenance through the real RAG service."""
from __future__ import annotations

import json

import httpx
import pytest

from refactor.learning.app.rag import API, BridgeError
from tests.refactor.rag155.test_rag155_backend import FakeCourseStore, service
from tests.refactor.rag155.test_rag323_source_layout import LayoutServer, _digest, _remote_row


def _source_layouts(*ids):
    return [{"requested_document_id": document_id, "total_chunks": 1, "complete": True}
            for document_id in ids]


def _prepare_local(instance, document_id, remote_id, title, chunks, *, canonical_id=None):
    instance.local.save_document(document_id, title, f"{title}.txt", chunks, status="ready")
    row = instance.local.document(document_id)
    instance.local.set_uploaded_content(document_id, row["current_content_sha256"])
    instance.local.set_remote_state(document_id, canonical_id or remote_id, "ready", len(chunks), 1)
    instance.local.set_remote_upload_id(document_id, remote_id)
    instance.local.set_status(document_id, "ready", 1)


class WarningLayoutServer(LayoutServer):
    def __call__(self, request):
        if request.method == "POST" and request.url.path == API + "/search":
            self.requests.append(request)
            self.search_body = json.loads(request.content)
            return httpx.Response(200, json={"passages": self.passages,
                "source_warnings": ["某命中不能确认属于所选资料，未作为原文引用。"]})
        return super().__call__(request)


def test_rag323_qa_send_opts_in_and_searches_actual_remote_upload_id(tmp_path):
    logical_id = _digest("qa-logical-source")
    remote_id = "remote-upload-version-323"
    text = "QA检索命中的原文。"
    fake = LayoutServer([_remote_row(remote_id, aliases=[logical_id],
        layouts=_source_layouts(remote_id))], capabilities=["source_layout_v1"], passages=[{
            "document_id": remote_id, "requested_document_id": remote_id,
            "seq": 0, "text": text, "page": 17, "section": "当前来源章节"}])
    store = FakeCourseStore(tmp_path / "account")
    instance, _, gateway = service(tmp_path, fake, store=store)
    try:
        _prepare_local(instance, logical_id, remote_id, "QA资料", [
            {"text": text, "page": 17, "section": "当前来源章节"}])
        session = instance.create([logical_id])["session"]

        saved = instance.send(session["id"], "问题是什么？")["session"]["messages"][-1]

        assert fake.search_body["source_layout_v1"] is True
        assert fake.search_body["document_ids"] == [remote_id]
        assert saved["passages"] == [{"number": 1, "document_id": logical_id,
            "seq": 0, "text": text, "page": 17, "section": "当前来源章节", "title": "QA资料"}]
        assert gateway.calls[-1][1][-1]["content"].find("第 17 页；当前来源章节") >= 0
    finally:
        instance.close()


def test_rag323_qa_send_keeps_each_logical_source_over_shared_canonical(tmp_path):
    first, second = _digest("qa-first-source"), _digest("qa-second-source")
    canonical = "qa-shared-canonical"
    text = "两个来源的共享命中正文。"
    fake = LayoutServer([_remote_row(canonical, aliases=[first, second],
        layouts=_source_layouts(first, second))], capabilities=["source_layout_v1"], passages=[
            {"document_id": first, "requested_document_id": first, "seq": 0,
             "text": text, "page": 3, "section": "第一来源位置"},
            {"document_id": second, "requested_document_id": second, "seq": 0,
             "text": text, "page": 81, "section": "第二来源位置"},
        ])
    store = FakeCourseStore(tmp_path / "account")
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        _prepare_local(instance, first, first, "第一资料", [
            {"text": text, "page": 3, "section": "第一来源位置"}], canonical_id=canonical)
        _prepare_local(instance, second, second, "第二资料", [
            {"text": text, "page": 81, "section": "第二来源位置"}], canonical_id=canonical)
        session = instance.create([first, second])["session"]

        saved = instance.send(session["id"], "这两份资料怎么说？")["session"]["messages"][-1]

        assert fake.search_body["source_layout_v1"] is True
        assert fake.search_body["document_ids"] == [first, second]
        assert [(row["document_id"], row["title"], row["page"], row["section"])
                for row in saved["passages"]] == [
                    (first, "第一资料", 3, "第一来源位置"),
                    (second, "第二资料", 81, "第二来源位置"),
                ]
    finally:
        instance.close()


def test_rag323_legacy_qa_keeps_text_but_marks_unproven_location_and_warnings(tmp_path):
    logical_id = _digest("qa-legacy-source")
    canonical = "qa-legacy-canonical"
    exact = "唯一完整的本地原文。"
    repeated = "重复原文段落。"
    fragment = "完整原文"
    fake = WarningLayoutServer([_remote_row(canonical, aliases=[logical_id], layouts=None)], passages=[
        {"document_id": canonical, "seq": 8, "text": exact,
         "page": 99, "section": "canonical位置"},
        {"document_id": canonical, "seq": 2, "text": fragment,
         "page": 4, "section": "片段位置"},
        {"document_id": canonical, "seq": 6, "text": repeated,
         "page": 5, "section": "不确定位置"},
    ])
    store = FakeCourseStore(tmp_path / "account")
    instance, _, gateway = service(tmp_path, fake, store=store)
    try:
        _prepare_local(instance, logical_id, logical_id, "旧服务器资料", [
            {"text": exact, "page": 12, "section": "本地唯一位置"},
            {"text": repeated, "page": 21, "section": "重复位置一"},
            {"text": repeated, "page": 22, "section": "重复位置二"},
        ], canonical_id=canonical)
        session = instance.create([logical_id])["session"]

        saved = instance.send(session["id"], "请说明出处？")["session"]["messages"][-1]
        persisted_doc = instance.local.document(logical_id)
        prompt = gateway.calls[-1][1][-1]["content"]

        assert "source_layout_v1" not in fake.search_body
        assert [(row["seq"], row["page"], row["section"], row.get("source_layout_status"))
                for row in saved["passages"]] == [
                    (0, 12, "本地唯一位置", None),
                    (None, None, None, "missing"),
                    (None, None, None, "missing"),
                ]
        assert [row["text"] for row in saved["passages"]] == [exact, fragment, repeated]
        assert "出处未确认" in prompt
        assert "部分命中无法确认属于所选资料" in prompt
        assert "某命中不能确认属于所选资料" not in prompt.split("检索提示：", 1)[0]
        assert persisted_doc is not None
        assert persisted_doc["status"] == "ready"
        assert persisted_doc["remote_upload_id"] == logical_id
    finally:
        instance.close()


def test_rag323_qa_reports_layout_conflict_without_new_version_or_answer(tmp_path):
    logical_id = _digest("qa-layout-conflict")

    class ConflictServer(LayoutServer):
        def __call__(self, request):
            if request.method == "POST" and request.url.path.endswith("/chunks"):
                self.requests.append(request)
                return httpx.Response(409, json={"error": {"code": "RAG_SOURCE_LAYOUT_CONFLICT"}})
            return super().__call__(request)

    fake = ConflictServer([_remote_row(logical_id, layouts=[{
        "requested_document_id": logical_id, "total_chunks": 1, "complete": False}])],
        capabilities=["source_layout_v1"])
    instance, _, gateway = service(tmp_path, fake)
    try:
        _prepare_local(instance, logical_id, logical_id, "原资料", [{"text": "原块不变。", "page": 88}])
        session = instance.create([logical_id])["session"]
        with pytest.raises(BridgeError, match="冲突") as failure:
            instance.send(session["id"], "请回答。")
        assert failure.value.code == "RAG_DOCUMENT_NOT_READY"
        assert instance.local.document(logical_id)["remote_upload_id"] == logical_id
        assert instance.get(session["id"])["session"]["messages"] == []
        assert gateway.calls == []
        posts = [request for request in fake.requests if request.method == "POST"]
        assert len(posts) == 1 and posts[0].url.path == f"{API}/documents/{logical_id}/chunks"
    finally:
        instance.close()


def test_rag323_unknown_citation_survives_real_store_close_and_service_reopen(tmp_path):
    from tests.refactor.rag263.test_rag263_background import close_service

    logical_id, canonical = _digest("persist-unknown-source"), "persist-unknown-canonical"
    fragment = "不能定位的原文片段"
    fake = LayoutServer([_remote_row(canonical, aliases=[logical_id], layouts=None)], passages=[{
        "document_id": canonical, "seq": 7, "text": fragment, "page": 99, "section": "他处位置"}])
    instance, store, _ = service(tmp_path, fake)
    reopened = None
    try:
        _prepare_local(instance, logical_id, logical_id, "保留引用", [
            {"text": f"前文，{fragment}，后文。", "page": 12}], canonical_id=canonical)
        session = instance.create([logical_id])["session"]
        saved = instance.send(session["id"], "原文是什么？")["session"]["messages"][-1]["passages"]
        assert len(saved) == 1 and saved[0]["source_layout_status"] == "missing"
        assert all(saved[0][key] is None for key in ("seq", "page", "section"))
        close_service(instance)
        with instance.local._lock:
            assert instance.local._closed
        assert not instance._job_worker.is_alive()
        requests_before_reopen = len(fake.requests)

        reopened, _, gateway = service(tmp_path, fake, store=store)
        loaded = reopened.get(session["id"])["session"]["messages"][-1]["passages"]

        assert loaded == saved
        assert loaded[0]["text"] == fragment
        assert all(loaded[0][key] is None for key in ("seq", "page", "section"))
        assert loaded[0]["source_layout_status"] == "missing"
        assert len(fake.requests) == requests_before_reopen
        assert gateway.calls == []
    finally:
        close_service(instance)
        if reopened is not None:
            close_service(reopened)


def test_rag323_missing_plan_hit_cannot_fabricate_classroom_evidence(tmp_path):
    from types import SimpleNamespace

    from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, PLAN, PARAGRAPH
    from tests.refactor.rag263.test_rag263_background import close_service

    logical_id = _digest(PARAGRAPH)
    fake = LayoutServer([_remote_row(logical_id, layouts=[{
        "requested_document_id": logical_id, "total_chunks": 1, "complete": False}])],
        capabilities=["source_layout_v1"], passages=[{
            "document_id": logical_id, "seq": None, "text": PARAGRAPH,
            "page": None, "section": None, "source_layout_status": "missing"}])
    producer_store = FakeCourseStore(tmp_path / "rag-account", plans=[{"id": PLAN}],
        sources={PLAN: [{"id": "partial-source", "adopted": True, "sha256": logical_id,
                        "chunk_count": 1, "title": "来源待同步"}]}, chunks={PLAN: []})
    instance, _, gateway = service(tmp_path, fake, store=producer_store)
    classroom = make_environment(tmp_path / "classroom-account")
    try:
        _prepare_local(instance, logical_id, logical_id, "来源待同步", [{"text": "先前缓存原块。"}])
        missing_result = instance.search_plan(PLAN, "原文")
        hit = missing_result["passages"][0]
        assert hit["chunk_id"] is None and hit["source_layout_status"] == "missing"
        assert (hit["seq"], hit["page"], hit["section"]) == (None, None, None)
        # Feed the actual normalized RAG result into the real consumer. Even
        # text matching another adopted chunk must not manufacture its ID.
        classroom.services.rag = SimpleNamespace(search_plan=lambda *args, **kwargs: missing_result)
        local_text = "现有本地fallback证据。"
        classroom.store.add_sources(PLAN, [{"id": "local-source", "kind": "file",
            "title": "本地资料", "filename": "fallback.txt", "sha256": _digest(local_text), "adopted": True,
            "chunks": [{"chunk_id": "local-fallback", "text": local_text}]}])
        course = classroom.room._course(PLAN)
        chunks, sources = course._source_rows()
        assert hit["text"] == chunks["c0"]["text"] and sources[chunks["c0"]["source_id"]]["adopted"]
        fallback = {"local-fallback": local_text}

        merged = classroom.room._qa_passages(course, "原文", fallback, None)

        assert merged == fallback and "c0" not in merged
        assert gateway.calls == [] and classroom.gateway.calls == []
    finally:
        close_service(instance)
        classroom.services._closed = True
        classroom.store.close()
