"""Backend contracts for the RAG sidebar redesign (all network calls mocked)."""
from hashlib import sha256
import json
import sqlite3

import httpx
import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.rag import API, RagLocalStore
from tests.refactor.rag155.test_rag155_backend import (
    FakeCourseStore, FakeRagServer, service,
)


def test_rag269_old_schema_migrates_repeatably_and_manual_title_survives_first_turn(tmp_path):
    path = tmp_path / "rag.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE rag_documents(
            id TEXT PRIMARY KEY,title TEXT NOT NULL,source TEXT NOT NULL,
            total_chunks INTEGER NOT NULL,uploaded_chunks INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'not_uploaded',error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL);
        CREATE TABLE rag_chunks(
            document_id TEXT NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,text TEXT NOT NULL,page INTEGER,section TEXT,
            PRIMARY KEY(document_id,seq));
        CREATE TABLE rag_sessions(
            id TEXT PRIMARY KEY,title TEXT NOT NULL,document_ids TEXT NOT NULL,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE rag_messages(
            id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES rag_sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,content TEXT NOT NULL,passages TEXT NOT NULL DEFAULT '[]',
            supplement INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
        INSERT INTO rag_sessions VALUES('old-session','旧标题','[]','t0','t0');
        INSERT INTO rag_messages VALUES('old-message','old-session','assistant','保留回答','[]',0,'t1');
    """)
    db.commit()
    db.close()

    local = RagLocalStore(path)
    try:
        assert local.get_session("old-session")["messages"][0]["content"] == "保留回答"
        assert local.get_session("old-session")["title_manual"] is False
        assert local._db.execute("SELECT state FROM rag_document_deletions").fetchall() == []
        renamed = local.rename_session("old-session", "我的课程笔记")
        assert renamed["title"] == "我的课程笔记" and renamed["title_manual"] is True
        local.append_turn("old-session", "这是第一个新问题", "新回答", [], False)
        assert local.get_session("old-session")["title"] == "我的课程笔记"
    finally:
        local.close()

    # Restarting is an idempotent migration and does not overwrite manual names.
    reopened = RagLocalStore(path)
    try:
        assert reopened.get_session("old-session")["title"] == "我的课程笔记"
        assert reopened.get_session("old-session")["count"] == 3
        assert reopened._db.execute("SELECT title_manual FROM rag_sessions WHERE id='old-session'").fetchone()[0] == 1
    finally:
        reopened.close()


def test_rag269_usage_counts_distinct_sessions_even_when_names_match(tmp_path):
    local = RagLocalStore(tmp_path / "rag.sqlite3")
    doc_id = sha256(b"source").hexdigest()
    try:
        local.save_document(doc_id, "教材", "upload.txt", [{"text": "正文"}])
        first = local.create_session([doc_id], "同名会话")
        second = local.create_session([], "同名会话")
        local.rename_session(first["id"], "同名会话")
        local.rename_session(second["id"], "同名会话")
        local.append_turn(second["id"], "问题", "回答", [{"document_id": doc_id, "text": "引用"}], False)
        usage = local.document_usage(doc_id)
        assert usage["session_names"] == ["同名会话", "同名会话"]
        assert len(usage["session_names"]) == 2
    finally:
        local.close()


def test_rag269_delete_transaction_rolls_back_session_and_passages_together(tmp_path):
    local = RagLocalStore(tmp_path / "rag.sqlite3")
    doc_id = sha256(b"rollback source").hexdigest()
    try:
        local.save_document(doc_id, "教材", "upload.txt", [{"text": "正文"}])
        session = local.create_session([doc_id], "会话")
        local.append_turn(session["id"], "问题", "回答", [{"document_id": doc_id, "text": "旧出处"}], False)
        local._db.execute("""CREATE TRIGGER fail_session_detach BEFORE UPDATE OF document_ids ON rag_sessions
            BEGIN SELECT RAISE(ABORT,'test rollback'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            local.finish_document_deletion(doc_id, content_deleted=True)
        assert local.get_session(session["id"])["document_ids"] == [doc_id]
        passage = local.get_session(session["id"])["messages"][1]["passages"][0]
        assert passage.get("deleted") is None
        assert local.document(doc_id) is not None
        assert local.pending_deletions() == []
    finally:
        local.close()


def test_rag269_deleted_passage_stays_deleted_after_same_sha_reimport(tmp_path):
    local = RagLocalStore(tmp_path / "rag.sqlite3")
    doc_id = sha256(b"same file").hexdigest()
    try:
        local.save_document(doc_id, "第一次标题", "upload.txt", [{"text": "原文"}])
        session = local.create_session([doc_id], "问答")
        local.append_turn(session["id"], "问题", "回答", [
            {"number": 1, "document_id": doc_id, "text": "原文", "title": "第一次标题"}], False)
        local.finish_document_deletion(doc_id, content_deleted=True)
        old_passage = local.get_session(session["id"])["messages"][1]["passages"][0]
        assert old_passage["deleted"] is True
        local.save_document(doc_id, "重新上传", "upload.txt", [{"text": "原文"}])
        old_passage = local.get_session(session["id"])["messages"][1]["passages"][0]
        assert old_passage["deleted"] is True
        assert old_passage["text"] == "原文"
        assert local.get_session(session["id"])["document_ids"] == []
        assert local.document(doc_id)["status"] == "not_uploaded"
    finally:
        local.close()


def test_rag269_empty_session_remains_and_cannot_send_until_source_added(tmp_path):
    fake = FakeRagServer()
    instance, _store, _gateway = service(tmp_path, fake)
    doc_id = sha256(b"empty source").hexdigest()
    try:
        instance.local.save_document(doc_id, "教材", "upload.txt", [{"text": "正文"}])
        session = instance.create([doc_id])["session"]
        assert instance.update_documents(session["id"], [])["session"]["document_ids"] == []
        assert instance.get(session["id"])["session"]["messages"] == []
        with pytest.raises(BridgeError, match="还没有资料"):
            instance.send(session["id"], "问题")
        assert instance.local.get_session(session["id"])["document_ids"] == []
    finally:
        instance.close()


class DeleteAliasServer(FakeRagServer):
    """Fake the 269 API: delete one alias or promote a remaining alias."""
    def __call__(self, request):
        if request.method == "DELETE" and request.url.path.startswith(API + "/documents/"):
            self.requests.append(request)
            target = request.url.path.rsplit("/", 1)[-1]
            canonical = self.aliases.get(target, target)
            if canonical not in self.documents:
                return httpx.Response(404, json={"error": "RAG_DOCUMENT_NOT_FOUND"})
            if target != canonical:
                self.aliases.pop(target, None)
                return httpx.Response(200, json={"deleted": True, "content_deleted": False,
                                                 "canonical_id": canonical})
            remaining = [alias for alias, owner in self.aliases.items() if owner == canonical]
            if not remaining:
                self.documents.pop(canonical)
                return httpx.Response(200, json={"deleted": True, "content_deleted": True,
                                                 "canonical_id": None})
            promoted = remaining[0]
            state = self.documents.pop(canonical)
            self.documents[promoted] = state
            self.aliases.pop(promoted, None)
            for alias, owner in list(self.aliases.items()):
                if owner == canonical:
                    self.aliases[alias] = promoted
            return httpx.Response(200, json={"deleted": True, "content_deleted": False,
                                             "canonical_id": promoted})
        return super().__call__(request)


def test_rag269_delete_canonical_promotes_shared_body_and_keeps_other_material_ready(tmp_path):
    fake = DeleteAliasServer()
    left = sha256(b"first file bytes").hexdigest()
    right = sha256(b"second file bytes").hexdigest()
    store = FakeCourseStore(tmp_path / "account")
    instance, _store, _gateway = service(tmp_path, fake, store=store)
    try:
        instance.local.save_document(left, "第一份", "first.txt", [{"text": "完全相同正文"}])
        instance.local.save_document(right, "第二份", "second.txt", [{"text": "完全相同正文"}])
        instance.ingest([left, right])
        assert len(fake.documents) == 1
        old_canonical = instance.local.document(left)["canonical_id"]
        assert old_canonical == instance.local.document(right)["canonical_id"]
        result = instance.delete_document(left)
        assert result["content_deleted"] is False
        assert result["canonical_id"] == right
        assert instance.local.document(left) is None
        survivor = instance.local.document(right)
        assert survivor["canonical_id"] == right
        assert survivor["status"] == "ready"
        assert right in fake.documents
        assert [request.url.path for request in fake.requests if request.method == "DELETE"] == [
            f"{API}/documents/{old_canonical}"]
        assert result["documents"] and all(row["id"] != left for row in result["documents"])
    finally:
        instance.close()


def test_rag269_plan_delete_only_resets_rag_projection_not_course_records(tmp_path):
    doc_id = sha256(b"adopted file bytes").hexdigest()
    store = FakeCourseStore(tmp_path / "account", plans=[{"id": "plan-1", "title": "学习计划"}],
        sources={"plan-1": [{"id": "src-1", "adopted": True, "sha256": doc_id,
                              "title": "课件", "url": "https://example.test/course"}]},
        chunks={"plan-1": [{"id": "chunk-1", "source_id": "src-1", "position": 0,
                            "text": "原始课程资料", "page_start": 1, "section": []}]})
    before = (json.loads(json.dumps(store._plans)), json.loads(json.dumps(store._sources)),
              json.loads(json.dumps(store._chunks)))
    fake = DeleteAliasServer()
    instance, _store, _gateway = service(tmp_path, fake, store=store)
    try:
        instance.sources()
        instance.ingest([doc_id])
        assert instance.local.document(doc_id)["status"] == "ready"
        result = instance.delete_document(doc_id)
        assert result["content_deleted"] is True
        assert store._plans == before[0]
        assert store._sources == before[1]
        assert store._chunks == before[2]
        row = next(item for item in result["documents"] if item["id"] == doc_id)
        assert row["origin"] == "plan" and row["status"] == "not_uploaded"
        assert instance.local.document(doc_id)["chunks"][0]["text"] == "原始课程资料"
    finally:
        instance.close()


def test_rag269_lost_delete_response_persists_until_authoritative_listing_reconciles(tmp_path):
    class LostDeleteReplyServer(DeleteAliasServer):
        def __init__(self):
            super().__init__()
            self.delete_count = 0

        def __call__(self, request):
            if request.method == "DELETE":
                self.delete_count += 1
                super().__call__(request)
                return httpx.Response(200, content=b"truncated")
            return super().__call__(request)

    fake = LostDeleteReplyServer()
    doc_id = sha256(b"lost delete response").hexdigest()
    instance, _store, _gateway = service(tmp_path, fake)
    try:
        instance.local.save_document(doc_id, "资料", "upload.txt", [{"text": "正文"}])
        instance.ingest([doc_id])
        with pytest.raises(BridgeError) as caught:
            instance.delete_document(doc_id)
        assert caught.value.code == "RAG_DELETE_PENDING"
        assert instance.local.document(doc_id) is not None
        assert instance.local.pending_deletions()[0]["state"] == "pending"
        instance.sources()  # the successful GET proves the remote copy is gone
        assert instance.local.document(doc_id) is None
        assert instance.local.pending_deletions() == []
        assert fake.delete_count == 1
    finally:
        instance.close()
