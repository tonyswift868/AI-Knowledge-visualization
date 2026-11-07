from types import SimpleNamespace

import pytest
import hashlib

from refactor.learning.app import rag


class _Local:
    def __init__(self, docs):
        self.docs = docs

    def document(self, document_id):
        return self.docs.get(document_id)


class _Transport:
    def __init__(self, response=None, error=None):
        self.response = response or {"passages": []}
        self.error = error
        self.calls = []

    def request_json(self, method, path, *, payload=None, cancel=None):
        self.calls.append((method, path, payload))
        if self.error:
            raise self.error
        return self.response


class _Store:
    def __init__(self, sources=(), chunks=()):
        self.data = {"sources": list(sources), "source_chunks": list(chunks)}

    def records(self, entity, plan_id):
        return self.data[entity]


def _service(monkeypatch, docs, passages=None, error=None):
    service = object.__new__(rag.RagService)
    service._closed = False
    service._remote_documents_loaded = True
    service._remote_capabilities = set()
    service._source_layout_complete = {}
    service._incomplete_documents = set()
    for row in docs.values():
        # Ready stubs represent an already-confirmed local upload snapshot.
        row.setdefault("current_content_sha256", "confirmed-fixture")
        row.setdefault("uploaded_content_sha256", "confirmed-fixture")
    service.local = _Local(docs)
    service.transport = _Transport({"passages": passages or []}, error)
    service.store = _Store()
    service._course_documents = lambda: ([{"id": "plan-a", "document_ids": ["adopted", "not-ready"]}], {})
    return service


def test_plan_search_uses_adopted_plan_docs_and_filters_canonical_results(monkeypatch):
    text = "证据段"
    digest = hashlib.sha256(text.encode()).hexdigest()
    service = _service(monkeypatch, {
        digest: {"id": digest, "title": "课件", "status": "ready", "canonical_id": "canonical-a",
                 "chunks": [{"text": text, "page": 88, "section": "来源章节"}]},
        "not-ready": {"id": "not-ready", "title": "待索引", "status": "uploading", "canonical_id": "canonical-b"},
    }, [
        {"document_id": "canonical-a", "seq": 2, "text": text, "page": 4, "section": "章节一"},
        {"document_id": "unrelated", "seq": 0, "text": "越权段"},
    ])
    service.store = _Store([{"id": "source-a", "sha256": digest, "adopted": True}],
                           [{"id": "chunk-a", "source_id": "source-a", "position": 0, "text": text,
                             "page_start": 88, "section": ["来源章节"]}])
    service._course_documents = lambda: ([{"id": "plan-a", "document_ids": [digest, "not-ready"]}], {})

    result = service.search_plan("plan-a", "问题")

    assert result["status"] == "partial"
    assert result["passages"] == [{"chunk_id": "chunk-a", "text": text, "title": "课件", "page": 88,
                                   "section": "来源章节", "document_id": digest, "seq": 0}]
    method, path, payload = service.transport.calls[0]
    assert (method, path) == ("POST", rag.API + "/search")
    assert payload["document_ids"] == [digest]


def test_plan_search_reports_not_ready_without_remote_call():
    service = _service(None, {"adopted": {"id": "adopted", "status": "uploading"}})
    result = service.search_plan("plan-a", "问题")
    assert result["status"] == "not_ready"
    assert result["passages"] == []
    assert service.transport.calls == []


def test_plan_search_reports_unavailable_and_propagates_cancellation():
    service = _service(None, {"adopted": {"id": "adopted", "status": "ready", "canonical_id": "adopted"}},
                       error=rag.BridgeError("RAG_UNAVAILABLE", "down"))
    assert service.search_plan("plan-a", "问题")["status"] == "unavailable"

    class Cancel:
        def is_set(self):
            return True

    with pytest.raises(rag.BridgeError, match="已取消"):
        service.search_plan("plan-a", "问题", cancel=Cancel())


def test_plan_search_maps_only_exact_adopted_chunks_and_marks_partial(tmp_path, monkeypatch):
    import hashlib

    import httpx

    from refactor.learning.app.services import AppServices
    from tests.refactor.rag155.test_rag155_backend import CA, FakeGateway, rag155_session

    state = rag155_session()
    original = rag.RagTransport.__init__
    def transport(self, **kwargs):
        kwargs["http_transport"] = httpx.MockTransport(lambda request: httpx.Response(500))
        original(self, **kwargs)
    monkeypatch.setattr(rag.RagTransport, "__init__", transport)
    services = AppServices(account_id=state.identity.account_id, data_root=tmp_path,
        session_provider=lambda: state, api_base_url="https://rag226.invalid", ca_bundle=CA)
    gateway = FakeGateway()
    gateway.close = lambda: None
    services._gateway = gateway
    try:
        store = services.store
        plan_id = store.create_plan(goal="plan226")
        body_ready, body_waiting, body_unadopted = "采用的原文段落", "尚未索引段落", "未采用资料段落"
        ready_digest = hashlib.sha256(body_ready.encode()).hexdigest()
        waiting_digest = hashlib.sha256(body_waiting.encode()).hexdigest()
        unadopted_digest = hashlib.sha256(body_unadopted.encode()).hexdigest()
        store.add_sources(plan_id, [
            dict(id="1" * 32, kind="file", title="采用资料", adopted=True, sha256=ready_digest,
                 chunks=[dict(id="a" * 32, text=body_ready, page_start=3, section=["单元"])]),
            dict(id="2" * 32, kind="file", title="等待资料", adopted=True, sha256=waiting_digest,
                 chunks=[dict(id="b" * 32, text=body_waiting)]),
            dict(id="3" * 32, kind="file", title="未采用", adopted=False, sha256=unadopted_digest,
                 chunks=[dict(id="c" * 32, text=body_unadopted)]),
        ])
        service = services.rag
        plans, _ = service._course_documents()
        assert plans[0]["id"] == plan_id
        service.local.set_remote_state(ready_digest, "canonical-ready", "ready", 1, 1)
        # A matching chunk count/status alone is no longer enough. This fixture
        # represents a server-confirmed upload of the exact local content hash.
        ready_local = service.local.document(ready_digest)
        service.local.set_uploaded_content(ready_digest, ready_local["current_content_sha256"])
        service.local.set_remote_state(waiting_digest, "canonical-waiting", "uploading", 0, 1)
        service.transport.request_json = lambda method, path, *, payload=None, cancel=None: {
            "passages": [
                {"document_id": "canonical-ready", "seq": 0, "text": body_ready, "page": 3},
                {"document_id": "canonical-ready", "seq": 1, "text": "服务器无原文的改写"},
                {"document_id": "canonical-waiting", "seq": 0, "text": body_waiting},
                {"document_id": "foreign-canonical", "seq": 0, "text": body_unadopted},
            ]}

        result = service.search_plan(plan_id, "查找原文")

        assert result["status"] == "partial"
        assert result["passages"] == [
            {"chunk_id": "a" * 32, "text": body_ready, "title": "采用资料",
             "page": 3, "section": "单元", "document_id": ready_digest, "seq": 0},
            {"chunk_id": None, "text": "服务器无原文的改写", "title": "采用资料",
             "page": None, "section": None, "document_id": ready_digest, "seq": None,
             "source_layout_status": "missing"}]
    finally:
        services.close()
