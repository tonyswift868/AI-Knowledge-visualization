"""251 RAG session binding and automatic source preparation contracts."""
from hashlib import sha256
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.rag import RagLocalStore
from tests.refactor.rag155.test_rag155_backend import FakeRagServer, service


def _id(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _save(instance, title, body):
    document_id = _id(title)
    instance.local.save_document(document_id, title, title + ".txt", [{"text": body}])
    return document_id


def test_ragux251_create_unready_session_and_change_sources_keeps_all_turns(tmp_path):
    fake = FakeRagServer()
    instance, _, _ = service(tmp_path, fake)
    first = _save(instance, "第一份资料", "原文一")
    second = _save(instance, "第二份资料", "原文二")
    try:
        created = instance.create([first])["session"]
        assert created["title"] == "新会话"
        assert created["document_ids"] == [first]
        assert instance.local.document(first)["status"] == "not_uploaded"
        instance.local.append_turn(created["id"], "旧问题", "旧答案", [
            {"number": 1, "document_id": first, "seq": 0, "text": "原文一"}], False)

        updated = instance.update_documents(created["id"], [second])["session"]
        assert updated["id"] == created["id"]
        assert updated["document_ids"] == [second]
        assert updated["title"] == "旧问题"
        assert updated["messages"] == instance.get(created["id"])["session"]["messages"]
        assert updated["messages"][0]["content"] == "旧问题"
        assert updated["messages"][1]["passages"][0]["text"] == "原文一"
        assert fake.requests == []
    finally:
        instance.close()


def test_ragux251_send_prepares_selected_sources_reports_progress_then_queries(tmp_path):
    fake = FakeRagServer()
    instance, _, gateway = service(tmp_path, fake)
    document_id = _save(instance, "未准备资料", "需要先传给检索服务的原文。")
    session = instance.create([document_id])["session"]
    progress = []
    try:
        result = instance.send(session["id"], "资料里说什么？", on_progress=progress.append)["session"]
        assert len(progress) >= 2
        assert progress[0]["status"] == "uploading"
        assert progress[-1]["status"] == "ready"
        assert progress[-1]["uploaded_chunks"] == progress[-1]["total_chunks"] == 1
        assert instance.local.document(document_id)["status"] == "ready"
        assert len(fake.search_queries) == 1
        assert len(gateway.calls) == 1
        assert [item["role"] for item in result["messages"]] == ["user", "assistant"]
    finally:
        instance.close()


def test_ragux251_preparation_failure_or_cancel_never_calls_model_or_saves_turn(tmp_path):
    fake = FakeRagServer()
    instance, _, gateway = service(tmp_path, fake)
    failed_id = _save(instance, "准备失败资料", "正文")
    failed_session = instance.create([failed_id])["session"]
    fake.fail_upload_status = 503
    try:
        with pytest.raises(BridgeError) as failed:
            instance.send(failed_session["id"], "不要在准备失败后回答")
        assert failed.value.code == "RAG_DOCUMENT_NOT_READY"
        assert "HTTP 503" in failed.value.message
        assert gateway.calls == [] and fake.search_queries == []
        assert instance.get(failed_session["id"])["session"]["messages"] == []

        cancelled_id = _save(instance, "取消准备资料", "正文")
        cancelled_session = instance.create([cancelled_id])["session"]
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(BridgeError) as stopped:
            instance.send(cancelled_session["id"], "取消的问题", cancel=cancel)
        assert stopped.value.code == "CANCELLED"
        assert gateway.calls == [] and fake.search_queries == []
        assert instance.get(cancelled_session["id"])["session"]["messages"] == []
    finally:
        instance.close()


def test_ragux251_cancel_during_upload_stops_before_search_and_keeps_history_empty(tmp_path):
    class CancelAfterChunkServer(FakeRagServer):
        def __call__(self, request):
            response = super().__call__(request)
            if request.method == "POST" and request.url.path.endswith("/chunks"):
                cancel.set()
            return response

    cancel = threading.Event()
    fake = CancelAfterChunkServer()
    instance, _, gateway = service(tmp_path, fake)
    document_id = _save(instance, "上传时取消", "正文")
    session = instance.create([document_id])["session"]
    try:
        with pytest.raises(BridgeError) as stopped:
            instance.send(session["id"], "取消前的问题", cancel=cancel)
        assert stopped.value.code == "CANCELLED"
        assert any(request.url.path.endswith("/chunks") for request in fake.requests)
        assert fake.search_queries == [] and gateway.calls == []
        assert instance.get(session["id"])["session"]["messages"] == []
    finally:
        instance.close()


def test_ragux251_legacy_account_database_opens_and_keeps_existing_sessions(tmp_path):
    path = tmp_path / "legacy-rag.sqlite3"
    document_id = _id("legacy")
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE rag_documents(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
            total_chunks INTEGER NOT NULL, uploaded_chunks INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'not_uploaded', error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE rag_chunks(
            document_id TEXT NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL, text TEXT NOT NULL, page INTEGER, section TEXT,
            PRIMARY KEY(document_id, seq)
        );
        CREATE TABLE rag_sessions(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, document_ids TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE rag_messages(
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES rag_sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL, content TEXT NOT NULL, passages TEXT NOT NULL DEFAULT '[]',
            supplement INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        INSERT INTO rag_documents VALUES('LEGACY_ID','旧资料','legacy.txt',1,1,'ready','', '2026-01-01T00:00:00+00:00');
        INSERT INTO rag_chunks VALUES('LEGACY_ID',0,'旧正文',1,'旧章节');
        INSERT INTO rag_sessions VALUES('old-session','旧会话','["LEGACY_ID"]','2026-01-01','2026-01-01');
        INSERT INTO rag_messages VALUES('old-q','old-session','user','旧问题','[]',0,'2026-01-01');
        INSERT INTO rag_messages VALUES('old-a','old-session','assistant','旧答案','[]',0,'2026-01-01');
    """)
    db.commit()
    db.close()

    store = RagLocalStore(path)
    try:
        old = store.get_session("old-session")
        assert old["document_ids"] == ["LEGACY_ID"]
        assert [row["content"] for row in old["messages"]] == ["旧问题", "旧答案"]
        assert store.document("LEGACY_ID")["chunks"][0]["text"] == "旧正文"
        # The existing schema's content fingerprint migration remains intact.
        assert store.document("LEGACY_ID")["status"] == "not_uploaded"
        new_id = _id("new source")
        store.save_document(new_id, "新资料", "new.txt", [{"text": "新原文"}])
        new = store.create_session([new_id], "新会话")
        assert store.get_session(new["id"])["document_ids"] == [new_id]
    finally:
        store.close()


def test_ragux251_account_local_stores_do_not_share_same_session_id(tmp_path):
    first = RagLocalStore(tmp_path / "account-one" / "rag.sqlite3")
    second = RagLocalStore(tmp_path / "account-two" / "rag.sqlite3")
    try:
        source_id = _id("shared digest does not imply shared account data")
        first.save_document(source_id, "账号一资料", "one.txt", [{"text": "A"}])
        session = first.create_session([source_id], "账号一会话")
        with pytest.raises(BridgeError) as missing:
            second.get_session(session["id"])
        assert missing.value.code == "RAG_SESSION_NOT_FOUND"
        assert second.document(source_id) is None
    finally:
        first.close()
        second.close()


def test_ragux251_bridge_registers_session_source_update_action(tmp_path):
    class Bridge:
        def __init__(self):
            self.handlers = {}
            self.events = []
        def register(self, name, fn, *, ui=False):
            self.handlers[name] = (fn, ui)
        def push(self, name, data):
            self.events.append((name, data))

    instance, _, _ = service(tmp_path, FakeRagServer())
    source_id = _save(instance, "未准备资料", "正文")
    bridge = Bridge()
    from refactor.learning.app.rag import register
    register(bridge, lambda: SimpleNamespace(rag=instance), SimpleNamespace())
    try:
        assert "rag.update_documents" in bridge.handlers
        created = bridge.handlers["rag.create"][0]({"document_ids": [source_id]}, None)["session"]
        result = bridge.handlers["rag.update_documents"][0](
            {"session_id": created["id"], "document_ids": [source_id]}, None)
        assert result["session"]["id"] == created["id"]
    finally:
        instance.close()
