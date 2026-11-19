"""RAG account-session sync and legacy projection checks for 465-MF02."""
from datetime import datetime, timezone
import httpx

from refactor.learning.app.rag import API, RagService
from refactor.learning.course.store import CourseStore
from refactor.learning.course.sync_course import CourseSync
from tests.refactor.rag155.test_rag155_backend import CA, FakeGateway, rag155_session

pytest_plugins = ("tests.refactor.course_integration.test_store_sync_roundtrip",)


NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc).isoformat()


def _session(session_id="rag-session"):
    return dict(id=session_id, title="资料问答", document_ids=["doc-1"],
                created_at=NOW, updated_at=NOW, title_manual=False)


def _message(message_id, session_id, role, content):
    return dict(id=message_id, session_id=session_id, role=role, content=content,
                passages=[], supplement=False, created_at=NOW)


def test_rag_session_round_trip_continue_and_tombstone(devices):
    _, transport, a, b = devices
    a.put_rag_session(_session())
    a.put_rag_message(_message("q1", "rag-session", "user", "question-one"))
    a.put_rag_message(_message("a1", "rag-session", "assistant", "answer-one"))
    assert CourseSync(a, transport).sync()["uploaded"] == 3

    CourseSync(b, transport).sync()
    assert b.rag_session("rag-session")["title"] == "资料问答"
    assert [row["content"] for row in b.rag_messages("rag-session")] == ["question-one", "answer-one"]

    b.put_rag_session({**_session(), "updated_at": "2026-11-19T12:01:00+00:00"})
    b.put_rag_message(_message("q2", "rag-session", "user", "question-two"))
    CourseSync(b, transport).sync()
    CourseSync(a, transport).sync()
    assert [row["content"] for row in a.rag_messages("rag-session")] == ["question-one", "answer-one", "question-two"]

    a.delete_rag_session("rag-session")
    CourseSync(a, transport).sync()
    CourseSync(b, transport).sync()
    assert b.records("rag_sessions") == []
    assert b.records("rag_messages") == []
    assert b.records("rag_message_parts") == []


def test_rag_message_over_256k_is_split_and_reconstructed(devices):
    _, transport, a, b = devices
    a.put_rag_session(_session("large"))
    content = "资料" * 160_000
    a.put_rag_message(_message("large-answer", "large", "assistant", content))
    parts = a.records("rag_message_parts")
    assert len(parts) > 1
    assert len(a.pending_records(1000)[1].payload.get("content", "").encode()) < 256 * 1024

    CourseSync(a, transport).sync()
    CourseSync(b, transport).sync()
    assert b.rag_messages("large")[0]["content"] == content


def test_two_legacy_devices_merge_distinct_sessions(devices):
    _, transport, a, b = devices
    a.import_rag_session("old-a", _session("old-a"), [_message("old-a-q", "old-a", "user", "from-a")])
    b.import_rag_session("old-b", _session("old-b"), [_message("old-b-q", "old-b", "user", "from-b")])
    CourseSync(a, transport).sync()
    CourseSync(b, transport).sync()
    CourseSync(a, transport).sync()
    assert {row["id"] for row in a.rag_sessions()} == {"old-a", "old-b"}
    assert {row["id"] for row in b.rag_sessions()} == {"old-a", "old-b"}


def test_legacy_rag_import_is_idempotent_and_preserves_messages(tmp_path):
    store = CourseStore("account-465", tmp_path)
    try:
        session = _session("legacy")
        message = _message("legacy-q", "legacy", "user", "legacy-session-content")
        store.import_rag_session("legacy", session, [message])
        first = store.pending_records(100)
        store.import_rag_session("legacy", session, [message])
        assert store.pending_records(100) == first
        assert store.rag_messages("legacy")[0]["content"] == "legacy-session-content"
    finally:
        store.close()


def test_synced_session_can_search_remote_chunks_without_local_file(tmp_path):
    state = rag155_session()
    store = CourseStore(state.identity.account_id, tmp_path)
    store.put_rag_session(_session("remote-only"))

    def server(request):
        if request.method == "GET" and request.url.path == API + "/documents":
            return httpx.Response(200, json={"documents": [{"document_id": "doc-1", "title": "远端资料",
                "source": "upload.txt", "status": "ready", "total_chunks": 1, "received_chunks": 1}],
                "capabilities": []})
        if request.method == "POST" and request.url.path == API + "/search":
            return httpx.Response(200, json={"passages": [{"document_id": "doc-1", "seq": 0,
                "text": "server chunk", "page": None, "section": None}]})
        return httpx.Response(404, json={"error": "unexpected"})

    service = RagService(store=store, account_id=state.identity.account_id,
        session_provider=lambda: state, api_base_url="https://account.example.test", ca_bundle=CA,
        gateway=FakeGateway("回答 [1]。"), http_transport=httpx.MockTransport(server))
    try:
        listing = service.sources()
        assert listing["sessions"][0]["id"] == "remote-only"
        assert listing["documents"][0]["status"] == "ready"
        result = service.send("remote-only", "这份资料说了什么？")["session"]
        assert result["messages"][-1]["passages"][0]["text"] == "server chunk"
    finally:
        service.stop_preparations()
        service.local.close()
        service.transport.close()
        store.close()
