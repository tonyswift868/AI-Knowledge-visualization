"""Backend contract tests for the local-session RAG feature; all HTTP is mocked."""
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import httpx

from refactor.learning.app.bridge import CallContext
from refactor.learning.app.rag import API, RagService, RagTransport
from refactor.learning.sync_identity import PlanError

ROOT = Path(__file__).parents[3]
CA = ROOT / "refactor/desktop/resources/trust/account-api.pem"


def rag155_session(subject="rag155-user", token="rag155-token"):
    issuer = "https://identity.example.test"
    parts = [sha256(value.encode()).hexdigest() for value in (issuer, subject)]
    account = "mfa1_" + sha256(("mentorflow.account.v1:" + ":".join(parts)).encode()).hexdigest()
    identity = SimpleNamespace(account_id=account, issuer=issuer, subject=subject,
        organization_id="rag155-org", workspace_id="rag155-workspace")
    return SimpleNamespace(identity=identity, access_token=token, server_state={
        "authenticated": True, "account": {"account_id": account, "subject_id": subject},
        "context": {"organization_id": identity.organization_id, "workspace_id": identity.workspace_id}})


def rag155_chunk_digest(chunk):
    return sha256(str(chunk.get("text") or "").encode("utf-8")).hexdigest()


class FakeRagServer:
    """Callable httpx.MockTransport server implementing the RAG API draft."""
    def __init__(self):
        self.documents = {}
        self.aliases = {}
        self.requests = []
        self.search_passages = []
        self.search_queries = []
        self.fail_upload_status = None

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == API + "/documents":
            rows = []
            for document_id, state in self.documents.items():
                rows.append({"document_id": document_id, "title": state["title"], "source": state["source"],
                    "status": state["status"], "total_chunks": state["total"],
                    "received_chunks": len(state["chunks"]),
                    "aliases": [alias for alias, canonical in self.aliases.items() if canonical == document_id]})
            return httpx.Response(200, json={"documents": rows,
                "capabilities": ["append_only_chunk_repair_v1"]})
        if request.method == "POST" and "/documents/" in path and path.endswith("/chunks"):
            if self.fail_upload_status is not None:
                status, self.fail_upload_status = self.fail_upload_status, None
                return httpx.Response(status, json={"error": "synthetic failure"})
            document_id = path.split("/documents/", 1)[1].split("/", 1)[0]
            canonical = self.aliases.get(document_id, document_id)
            body = json.loads(request.content)
            state = self.documents.setdefault(canonical, {"title": body["title"], "source": body["source"],
                "total": body["total_chunks"], "chunks": {}, "status": "uploading"})
            if body.get("repair_existing") is True:
                for chunk in body["chunks"]:
                    seq = chunk.get("seq")
                    if (type(seq) is not int or not 0 <= seq < body["total_chunks"]
                            or body["repair_manifest"][seq] != rag155_chunk_digest(chunk)):
                        return httpx.Response(400, json={"error": "invalid repair chunk manifest"})
                desired = {}
                for digest in body["repair_manifest"]:
                    desired[digest] = desired.get(digest, 0) + 1
                current_text = "".join(chunk.get("text", "") for _, chunk in sorted(state["chunks"].items()))
                if sha256(current_text.encode("utf-8")).hexdigest() == body["repair_content_sha256"]:
                    return httpx.Response(200, json={"document_id": canonical,
                        "requested_document_id": document_id, "status": "ready",
                        "received_chunks": len(state["chunks"]), "total_chunks": len(state["chunks"]),
                        "deduplicated": False, "repair_complete": True})
                present = {}
                for chunk in state["chunks"].values():
                    digest = rag155_chunk_digest(chunk)
                    present[digest] = present.get(digest, 0) + 1
                if any(count > desired.get(digest, 0) for digest, count in present.items()):
                    return httpx.Response(409, json={"error": "RAG_DOCUMENT_REPAIR_CONFLICT"})
                present = {}
                for chunk in state["chunks"].values():
                    digest = rag155_chunk_digest(chunk)
                    present[digest] = present.get(digest, 0) + 1
                target = len(state["chunks"]) + sum(
                    max(0, count - present.get(digest, 0)) for digest, count in desired.items())
                state["total"] = target
                next_seq = max(state["chunks"], default=-1) + 1
                for chunk in body["chunks"]:
                    digest = rag155_chunk_digest(chunk)
                    if present.get(digest, 0) < desired.get(digest, 0):
                        state["chunks"][next_seq] = {**chunk, "seq": next_seq}
                        next_seq += 1
                        present[digest] = present.get(digest, 0) + 1
                complete = all(present.get(digest, 0) >= count for digest, count in desired.items())
                if complete:
                    state["total"] = len(state["chunks"])
                    state["status"] = "ready"
                    text = "".join(state["chunks"][i]["text"] for i in sorted(state["chunks"]))
                    state["content_sha256"] = sha256(text.encode("utf-8")).hexdigest()
                return httpx.Response(200, json={"document_id": canonical,
                    "requested_document_id": document_id, "status": "ready",
                    "received_chunks": len(state["chunks"]), "total_chunks": state["total"],
                    "deduplicated": False, "repair_complete": complete})
            state["total"] = body["total_chunks"]
            for chunk in body["chunks"]:
                if type(chunk.get("seq")) is not int or not 0 <= chunk["seq"] < state["total"]:
                    return httpx.Response(400, json={"error": "seq must be zero-based"})
                state["chunks"][chunk["seq"]] = chunk
            complete = sorted(state["chunks"]) == list(range(state["total"]))
            if complete:
                digest = sha256("".join(state["chunks"][i]["text"] for i in range(state["total"])).encode()).hexdigest()
                existing = next((key for key, value in self.documents.items()
                                 if key != canonical and value["status"] == "ready"
                                 and sha256("".join(value["chunks"][i]["text"] for i in range(value["total"])).encode()).hexdigest() == digest), None)
                if existing:
                    self.documents.pop(canonical, None)
                    self.aliases[document_id] = existing
                    canonical = existing
                    state = self.documents[canonical]
                    deduplicated = True
                else:
                    state["status"] = "ready"
                    state["content_sha256"] = digest
                    if document_id != canonical:
                        self.aliases[document_id] = canonical
                    deduplicated = False
            else:
                deduplicated = False
            return httpx.Response(200, json={"document_id": canonical, "requested_document_id": document_id,
                "status": state["status"], "received_chunks": len(state["chunks"]),
                "total_chunks": state["total"], "deduplicated": deduplicated})
        if request.method == "POST" and path == API + "/search":
            body = json.loads(request.content)
            self.search_queries.append(body)
            return httpx.Response(200, json={"passages": self.search_passages})
        return httpx.Response(404, json={"error": "unknown mock endpoint"})


class FakeCourseStore:
    def __init__(self, root, plans=(), sources=None, chunks=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._plans = list(plans)
        self._sources = sources or {}
        self._chunks = chunks or {}

    def list_plans(self, *, include_archived=False):
        return list(self._plans)

    def records(self, entity, plan_id=""):
        return list((self._sources if entity == "sources" else self._chunks).get(plan_id, []))


class FakeGateway:
    def __init__(self, text="按资料回答 [1]。\n\n资料外补充：补充示例。"):
        self.text = text
        self.calls = []

    def complete(self, purpose, messages, **kwargs):
        self.calls.append((purpose, messages, kwargs))
        if kwargs.get("on_text"):
            kwargs["on_text"]("按资料回答 ")
            kwargs["on_text"]("[1]。\n\n资料外补充：补充示例。")
        return SimpleNamespace(text=self.text)


def service(tmp_path, fake_server, *, store=None, gateway=None):
    state = rag155_session()
    store = store or FakeCourseStore(tmp_path / "account")
    gateway = gateway or FakeGateway()
    instance = RagService(store=store, account_id=state.identity.account_id,
        session_provider=lambda: state, api_base_url="https://account.example.test", ca_bundle=CA,
        gateway=gateway, http_transport=httpx.MockTransport(fake_server))
    return instance, store, gateway


def test_rag155_adopted_plan_sources_are_extracted_and_section_lists_normalized(tmp_path):
    digest = sha256(b"original document bytes").hexdigest()
    store = FakeCourseStore(tmp_path / "account", plans=[{"id": "plan-a", "title": "计划 A"}],
        sources={"plan-a": [
            {"id": "adopted", "adopted": True, "sha256": digest, "title": "教材", "url": "https://docs.test/a"},
            {"id": "suggested", "adopted": False, "sha256": sha256(b"suggestion").hexdigest(), "title": "未采用"},
        ]}, chunks={"plan-a": [
            {"id": "chunk-1", "source_id": "adopted", "position": 0, "text": "正文一", "page_start": 4, "section": ["第一章", "概念"]},
            {"id": "chunk-2", "source_id": "adopted", "position": 1, "text": "正文二", "page_start": 5, "section": ["第一章", "例子"]},
            {"id": "chunk-3", "source_id": "suggested", "position": 2, "text": "不应进入", "section": []},
        ]})
    instance, _, _ = service(tmp_path, FakeRagServer(), store=store)
    try:
        result = instance.sources()
        assert result["plans"] == [{"id": "plan-a", "title": "计划 A", "document_ids": [digest]}]
        document = next(row for row in result["documents"] if row["id"] == digest)
        assert document["total_chunks"] == 2 and document["status"] == "not_uploaded"
        saved = instance.local.document(digest)
        assert saved["chunks"][0]["page"] == 4
        assert saved["chunks"][0]["section"] == "第一章 / 概念"
        assert all(row["title"] != "未采用" for row in result["documents"])
    finally:
        instance.close()


def test_rag155_import_extracts_locally_and_upload_is_batched_and_idempotent(tmp_path, monkeypatch):
    fake = FakeRagServer()
    instance, _, _ = service(tmp_path, fake)
    path = tmp_path / "selected.md"
    path.write_text("# 章节\n\n" + ("资料文字。" * 180), encoding="utf-8")
    instance._picked_paths.add(str(path.resolve()))
    try:
        imported = instance.import_files([str(path.resolve())])
        document_id = imported["documents"][0]["id"]
        assert document_id == sha256(path.read_bytes()).hexdigest()
        assert len(fake.requests) == 0  # originals are extracted locally, never sent
        batch_id = sha256(b"separate batched document").hexdigest()
        instance.local.save_document(batch_id, "批量测试", "batch.txt", [
            {"text": f"分块 {index} " + ("文字" * 35), "page": index, "section": "测试章节"}
            for index in range(1, 9)])
        monkeypatch.setattr("refactor.learning.app.rag.BATCH_BYTES", 520)
        progress = []
        first = instance.ingest([batch_id], on_progress=progress.append)["documents"][0]
        upload_requests = [r for r in fake.requests if r.method == "POST" and r.url.path.endswith("/chunks")]
        assert len(upload_requests) > 1
        assert all(len(r.content) <= 520 for r in upload_requests)
        assert first["status"] == "ready" and first["uploaded_chunks"] == first["total_chunks"]
        assert progress[0]["status"] == "uploading"
        assert progress[-1]["status"] == "ready"
        before = len(upload_requests)
        second = instance.ingest([batch_id])["documents"][0]
        after = len([r for r in fake.requests if r.method == "POST" and r.url.path.endswith("/chunks")])
        assert second["status"] == "ready" and after == before
        assert sorted(fake.documents[batch_id]["chunks"]) == list(range(first["total_chunks"]))
        failed_id = sha256(b"retryable upload").hexdigest()
        instance.local.save_document(failed_id, "可重试", "retry.txt", [{"text": "chunk"}])
        fake.fail_upload_status = 503
        failed = instance.ingest([failed_id])["documents"][0]
        assert failed["status"] == "failed" and "HTTP 503" in failed["error"]
        recovered = instance.ingest([failed_id])["documents"][0]
        assert recovered["status"] == "ready" and recovered["uploaded_chunks"] == 1
    finally:
        instance.close()


def test_rag155_search_prompt_citations_supplement_stream_and_session_order_survive_reopen(tmp_path):
    fake = FakeRagServer()
    instance, store, gateway = service(tmp_path, fake)
    digest = sha256(b"rag source").hexdigest()
    instance.local.save_document(digest, "算法讲义", "算法讲义.md", [
        {"text": "向量检索按相似度排序。", "page": 7, "section": "第二章 / 检索"}])
    instance.local.set_status(digest, "ready", 1)
    fake.search_passages = [
        {"document_id": digest, "seq": 0, "text": "向量检索按相似度排序。", "page": 7, "section": "第二章 / 检索"},
        {"document_id": "not-selected", "seq": 1, "text": "不可泄漏", "page": 1},
    ]
    session = instance.create([digest])["session"]
    streamed = []
    try:
        first = instance.send(session["id"], "向量检索怎么排序？", on_text=streamed.append)["session"]
        second = instance.send(session["id"], "原文在哪一页？")["session"]
        assert gateway.calls[0][0] == "rag_qa"
        assert "只依据" in gateway.calls[0][2]["system"]
        assert "[1]《算法讲义》（第 7 页；第二章 / 检索）" in gateway.calls[0][1][-1]["content"]
        assert "向量检索怎么排序？" in gateway.calls[0][1][-1]["content"]
        assert "资料外补充：" in gateway.calls[0][2]["system"]
        assert streamed and first["messages"][-1]["passages"] == [{
            "number": 1, "document_id": digest, "seq": 0, "text": "向量检索按相似度排序。",
            "page": 7, "section": "第二章 / 检索", "title": "算法讲义"}]
        assert first["messages"][-1]["supplement"] is True
        rows = second["messages"]
        assert [row["role"] for row in rows] == ["user", "assistant", "user", "assistant"]
        instance.close()
        reopened, _, _ = service(tmp_path, fake, store=store)
        try:
            persisted = reopened.get(session["id"])["session"]
            assert [row["role"] for row in persisted["messages"]] == ["user", "assistant", "user", "assistant"]
            assert persisted["messages"][0]["content"] == "向量检索怎么排序？"
            assert persisted["messages"][1]["passages"][0]["page"] == 7
            cancelled = __import__("threading").Event()
            cancelled.set()
            from refactor.learning.app.bridge import BridgeError
            try:
                reopened.send(session["id"], "这条应被取消", cancel=cancelled)
            except BridgeError as exc:
                assert exc.code == "CANCELLED"
            else:
                raise AssertionError("cancelled send should not commit a turn")
            assert [row["content"] for row in reopened.get(session["id"])["session"]["messages"] if row["role"] == "user"] == [
                "向量检索怎么排序？", "原文在哪一页？"]
        finally:
            reopened.close()
    finally:
        if not instance._closed:
            instance.close()


def test_rag155_markdown_bold_supplement_prefix_is_detected_on_reopen(tmp_path):
    fake = FakeRagServer()
    instance, store, gateway = service(tmp_path, fake)
    try:
        document_id = "a" * 64
        instance.local.save_document(
            document_id, "讲义", "讲义.txt", [{"text": "资料原句。"}], status="ready")
        session = instance.create([document_id])["session"]
        # A stored legacy false value is repaired at read time from the visible
        # answer prefix, including the Markdown formatting emitted live.
        instance.local.append_turn(session["id"], "举例", "以下为**资料外补充**：生活例子。", [], False)
        assert instance.get(session["id"])["session"]["messages"][-1]["supplement"] is True
    finally:
        instance.close()


def test_rag155_canonical_dedup_alias_survives_restart_and_search_maps_passages(tmp_path):
    fake = FakeRagServer()
    instance, store, _ = service(tmp_path, fake)
    first_id, second_id = sha256(b"first source id").hexdigest(), sha256(b"second source id").hexdigest()
    canonical_chunks = [{"text": "同一份资料的正文。", "page": 3, "section": "第一章"}]
    differently_chunked = [
        {"text": "同一份", "page": 3, "section": "第一章"},
        {"text": "资料的正文。", "page": 3, "section": "第一章"},
    ]
    try:
        instance.local.save_document(first_id, "原计划资料", "first.txt", canonical_chunks)
        instance.local.save_document(second_id, "上传资料", "second.txt", differently_chunked)
        first = instance.ingest([first_id])["documents"][0]
        assert first["status"] == "ready"
        canonical = first_id
        second = instance.ingest([second_id])["documents"][0]
        assert second["status"] == "ready"
        assert second["total_chunks"] == 1 and second["uploaded_chunks"] == 1
        local_alias = instance.local.document(second_id)
        canonical = local_alias["canonical_id"]
        assert canonical == first_id
        assert local_alias["total_chunks"] == 2 and local_alias["canonical_total_chunks"] == 1
        assert fake.aliases[second_id] == canonical
        assert sorted(fake.documents[canonical]["chunks"]) == [0]
        instance.close()

        reopened, _, gateway = service(tmp_path, fake, store=store)
        try:
            source_list = reopened.sources()
            local_doc = next(doc for doc in source_list["documents"] if doc["id"] == second_id)
            assert local_doc["status"] == "ready" and local_doc["uploaded_chunks"] == 1
            assert reopened.local.document(second_id)["canonical_id"] == canonical
            after_sources = len(fake.requests)
            again = reopened.ingest([second_id])["documents"][0]
            assert again["status"] == "ready" and len(fake.requests) == after_sources + 1  # GET only
            fake.search_passages = [{"document_id": canonical, "seq": 0, "text": "同一份资料的正文。",
                                     "page": 3, "section": "第一章"}]
            session = reopened.create([second_id])["session"]
            answer = reopened.send(session["id"], "正文是什么？")["session"]["messages"][-1]
            assert fake.search_queries[-1]["document_ids"] == [second_id]
            assert answer["passages"][0]["document_id"] == second_id
            assert answer["passages"][0]["title"] == "上传资料"
            assert answer["passages"][0]["text"] == "同一份资料的正文。"
            assert answer["passages"][0]["seq"] is None
            assert answer["passages"][0]["page"] is None
            assert "出处未确认" in gateway.calls[-1][1][-1]["content"]
            assert gateway.calls[-1][0] == "rag_qa"
        finally:
            reopened.close()
    finally:
        if not instance._closed:
            instance.close()


def test_rag155_authoritative_remote_removal_allows_reingest(tmp_path):
    fake = FakeRagServer()
    instance, _, _ = service(tmp_path, fake)
    document_id = sha256(b"document to remove remotely").hexdigest()
    try:
        instance.local.save_document(document_id, "讲义", "讲义.txt", [{"text": "重新入库资料。"}])
        uploaded = instance.ingest([document_id])["documents"][0]
        assert uploaded["status"] == "ready"
        canonical = instance.local.document(document_id)["canonical_id"]
        fake.documents.pop(canonical)
        fake.aliases = {alias: value for alias, value in fake.aliases.items() if value != canonical}

        source = next(row for row in instance.sources()["documents"] if row["id"] == document_id)
        assert source["status"] == "not_uploaded" and source["uploaded_chunks"] == 0
        restored = instance.ingest([document_id])["documents"][0]
        assert restored["status"] == "ready" and restored["uploaded_chunks"] == 1
    finally:
        instance.close()


def test_rag155_uploading_status_wins_when_counts_equal_total():
    from refactor.learning.app.rag import RagService
    view = RagService._doc_view(
        {"id": "requested", "title": "资料", "source": "a.txt", "total_chunks": 2,
         "uploaded_chunks": 0, "status": "not_uploaded"},
        remote={"status": "uploading", "uploaded_chunks": 2, "total_chunks": 2})
    assert view["status"] == "uploading"
    assert view["uploaded_chunks"] == 2 and view["total_chunks"] == 2


def test_rag155_cancel_after_commit_does_not_return_cancelled(tmp_path, monkeypatch):
    fake = FakeRagServer()
    instance, _, _ = service(tmp_path, fake)
    document_id = sha256(b"cancel boundary").hexdigest()
    instance.local.save_document(document_id, "讲义", "讲义.txt", [{"text": "原句。"}], status="ready")
    session = instance.create([document_id])["session"]
    fake.search_passages = [{"document_id": document_id, "seq": 0, "text": "原句。", "page": None, "section": None}]
    cancel = __import__("threading").Event()
    append_turn = instance.local.append_turn

    def commit_then_cancel(*args, **kwargs):
        result = append_turn(*args, **kwargs)
        cancel.set()
        return result

    monkeypatch.setattr(instance.local, "append_turn", commit_then_cancel)
    try:
        result = instance.send(session["id"], "问题", cancel=cancel)
        assert result["session"]["messages"][-1]["content"] == "按资料回答 [1]。\n\n资料外补充：补充示例。"
    finally:
        instance.close()


def test_rag155_bridge_registers_expected_methods_and_offline_sources_keep_local_data(tmp_path):
    class Bridge:
        def __init__(self):
            self.handlers = {}
            self.events = []
        def register(self, name, fn, *, ui=False):
            self.handlers[name] = (fn, ui)
        def push(self, name, data):
            self.events.append((name, data))

    class BrokenRagServer:
        def __call__(self, request):
            raise httpx.ConnectError("offline", request=request)

    instance, _, _ = service(tmp_path, BrokenRagServer())
    instance.local.save_document("a" * 64, "本地资料", "local.txt", [{"text": "保存在本地"}])
    session = instance.local.create_session(["a" * 64], "本地会话")
    bridge = Bridge()
    from refactor.learning.app.rag import register
    register(bridge, lambda: SimpleNamespace(rag=instance), SimpleNamespace())
    try:
        source_fn, _ = bridge.handlers["rag.sources"]
        value = source_fn({}, CallContext("s", __import__("threading").Event(), lambda _: None))
        assert value["server_status"]["state"] == "unavailable"
        assert value["documents"][0]["title"] == "本地资料"
        assert value["sessions"][0]["id"] == session["id"]
        assert set(bridge.handlers) == {"rag.sources", "rag.ingest", "rag.pick_files", "rag.import_files",
            "rag.create", "rag.update_documents", "rag.document_usage", "rag.delete_document",
            "rag.rename_session", "rag.get", "rag.delete", "rag.send",
            "rag.prepare", "rag.preparation", "rag.cancel_preparation"}
        assert bridge.handlers["rag.pick_files"][1] is True
    finally:
        instance.close()


def test_rag155_sync_transport_retries_rotated_401_once_but_not_403(tmp_path):
    state = [rag155_session(token="old-token")]
    calls = []

    def rotate(request):
        calls.append(request.headers.get("authorization"))
        if request.headers.get("authorization") == "Bearer old-token":
            state[0] = rag155_session(token="new-token")
            return httpx.Response(401, json={"error": {"code": "REAUTH_REQUIRED"}})
        return httpx.Response(200, json={"documents": []})

    transport = RagTransport(account_id=state[0].identity.account_id,
        session_provider=lambda: state[0], api_base_url="https://account.example.test",
        ca_bundle=CA, http_transport=httpx.MockTransport(rotate))
    try:
        assert transport.request_json("GET", API + "/documents") == {"documents": []}
        assert calls == ["Bearer old-token", "Bearer new-token"]
    finally:
        transport.close()

    forbidden_calls = []
    def forbidden(request):
        forbidden_calls.append(request.headers.get("authorization"))
        return httpx.Response(403, json={"error": {"code": "REAUTH_REQUIRED"}})
    transport = RagTransport(account_id=state[0].identity.account_id,
        session_provider=lambda: state[0], api_base_url="https://account.example.test",
        ca_bundle=CA, http_transport=httpx.MockTransport(forbidden))
    try:
        try:
            transport.request_json("GET", API + "/documents")
        except PlanError as exc:
            assert exc.code == "REAUTH_REQUIRED"
        else:
            raise AssertionError("403 must remain an authentication failure")
        assert forbidden_calls == ["Bearer new-token"]
    finally:
        transport.close()

