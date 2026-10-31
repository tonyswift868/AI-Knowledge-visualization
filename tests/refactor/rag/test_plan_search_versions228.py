"""Content-version confirmation contract for plan-scoped RAG search."""

import threading

from refactor.learning.app import rag


def test_plan_search_does_not_query_old_remote_body_before_new_content_is_confirmed(tmp_path):
    """An old ready remote ID cannot stand in for updated local source text."""
    source_id = "plan-source"
    document_id = "a" * 64
    old_text = "旧版资料正文，未确认的新版本不能返回这一段。"
    new_text = "更新后的资料正文，目前还未确认上传内容版本。"

    class PlanStore:
        def list_plans(self, *, include_archived=True):
            return [{"id": "plan-a", "title": "计划 A", "goal": "检索测试"}]

        def records(self, entity, plan_id):
            if entity == "sources":
                return [{"id": source_id, "sha256": document_id, "title": "资料 A",
                         "url": "https://example.invalid/a", "adopted": True, "chunk_count": 1}]
            if entity == "source_chunks":
                return [{"id": "new-chunk", "source_id": source_id, "position": 0,
                         "text": new_text, "page_start": 1, "section": []}]
            raise AssertionError(entity)

    class Transport:
        def __init__(self):
            self.calls = []

        def request_json(self, method, path, *, payload=None, cancel=None):
            self.calls.append((method, path, payload))
            # The server still has the old, previously ready body.
            return {"passages": [{"document_id": "canonical-old", "seq": 0, "text": old_text}]}

    service = object.__new__(rag.RagService)
    service._closed = False
    service._upload_lock = threading.RLock()  # created by __init__ since 263, which this test bypasses
    service._incomplete_documents = set()
    service._plan_documents = {}
    service.store = PlanStore()
    service.local = rag.RagLocalStore(tmp_path / "rag-version228.sqlite3")
    service.transport = Transport()
    try:
        service.local.save_document(document_id, "资料 A", "a.txt", [dict(text=old_text)], status="ready")
        old = service.local.document(document_id)
        service.local.set_uploaded_content(document_id, old["current_content_sha256"])
        service.local.set_remote_upload_id(document_id, "canonical-old")
        service.local.set_remote_state(document_id, "canonical-old", "ready", 1, 1)

        result = service.search_plan("plan-a", "新版资料正文")

        assert result == {"status": "not_ready", "passages": []}
        assert service.local.document(document_id)["status"] == "not_uploaded"
        assert service.transport.calls == []
    finally:
        service.local.close()
