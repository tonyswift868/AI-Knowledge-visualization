"""RAG 263 preparation manager tests; all HTTP is handled by a gated fake."""
from hashlib import sha256
import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.rag import API, RagService


ROOT = Path(__file__).parents[3]
CA = ROOT / "refactor/desktop/resources/trust/account-api.pem"


def state_for(subject="rag263-user", token="rag263-fake-token"):
    issuer = "https://identity.example.test"
    parts = [sha256(value.encode()).hexdigest() for value in (issuer, subject)]
    account = "mfa1_" + sha256(("mentorflow.account.v1:" + ":".join(parts)).encode()).hexdigest()
    identity = SimpleNamespace(account_id=account, issuer=issuer, subject=subject,
        organization_id="rag263-org", workspace_id="rag263-workspace")
    return SimpleNamespace(identity=identity, access_token=token, server_state={
        "authenticated": True, "account": {"account_id": account, "subject_id": subject},
        "context": {"organization_id": identity.organization_id,
                    "workspace_id": identity.workspace_id}})


class GatedFakeTransport:
    """Minimal server. A gate makes uploads deterministic without real network."""
    def __init__(self):
        self.requests = []
        self.upload_ids = []
        self.documents = {}
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_upload = False
        self.fail_status = None

    def __call__(self, request):
        self.requests.append(request)
        if request.method == "GET" and request.url.path == API + "/documents":
            return httpx.Response(200, json={"documents": [
                {"document_id": doc_id, "status": row["status"],
                 "received_chunks": row["received"], "total_chunks": row["total"]}
                for doc_id, row in self.documents.items()]})
        if request.method == "POST" and request.url.path.endswith("/chunks"):
            document_id = request.url.path.split("/documents/", 1)[1].split("/", 1)[0]
            self.upload_ids.append(document_id)
            self.entered.set()
            if self.block_upload:
                assert self.release.wait(5), "test did not release the fake upload gate"
            if self.fail_status:
                status, self.fail_status = self.fail_status, None
                return httpx.Response(status, json={"error": "RAG263_FAKE_FAILURE"})
            body = json.loads(request.content)
            row = self.documents.setdefault(document_id,
                {"seqs": set(), "received": 0, "total": body["total_chunks"], "status": "uploading"})
            row["seqs"].update(chunk.get("seq") for chunk in body["chunks"])
            row["received"] = len(row["seqs"])
            row["total"] = body["total_chunks"]
            row["status"] = "ready" if row["received"] >= row["total"] else "uploading"
            return httpx.Response(200, json={"document_id": document_id,
                "requested_document_id": document_id, "status": row["status"],
                "received_chunks": row["received"], "total_chunks": row["total"]})
        return httpx.Response(404, json={"error": "unknown fake endpoint"})


class FakeCourseStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def list_plans(self, *, include_archived=False):
        return []

    def records(self, entity, plan_id=""):
        return []


class FakeGateway:
    def complete(self, *args, **kwargs):
        raise AssertionError("background preparation must not call the model")


def make_service(tmp_path, fake, *, subject="rag263-user", store=None):
    state = state_for(subject)
    service = RagService(store=store or FakeCourseStore(tmp_path / subject),
        account_id=state.identity.account_id, session_provider=lambda: state,
        api_base_url="https://account.example.test", ca_bundle=CA,
        gateway=FakeGateway(), http_transport=httpx.MockTransport(fake))
    return service


def add_doc(service, marker, chunks=2):
    doc_id = sha256(marker.encode()).hexdigest()
    service.local.save_document(doc_id, "本地测试资料", marker + ".txt",
        [{"text": f"fake body chunk {i} " + ("字" * 80)} for i in range(chunks)])
    return doc_id


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), "timed out waiting for preparation state"


def close_service(service):
    service.close()
    eventually(lambda: service.local._closed, timeout=7)


def test_rag263_start_returns_before_upload_and_duplicate_start_shares_job(tmp_path):
    fake = GatedFakeTransport()
    fake.block_upload = True
    service = make_service(tmp_path, fake)
    document_id = add_doc(service, "same document")
    try:
        started = service.prepare([document_id])
        assert started["jobs"][0]["state"] == "queued"
        assert fake.entered.wait(2)
        duplicate = service.prepare([document_id], priority=True)
        assert duplicate["scope_id"] == started["scope_id"] == service.scope_id
        assert duplicate["jobs"][0]["id"] == started["jobs"][0]["id"]
        assert len(service.preparation([document_id])["jobs"]) == 1
    finally:
        fake.release.set()
        if service.preparation()["jobs"]:
            eventually(lambda: service.preparation()["jobs"][0]["state"] == "ready")
        close_service(service)


def test_rag263_priority_promotes_queued_document(tmp_path):
    fake = GatedFakeTransport()
    fake.block_upload = True
    service = make_service(tmp_path, fake)
    first, ordinary, priority = (add_doc(service, name, chunks=1)
                                 for name in ("running", "ordinary", "priority"))
    try:
        service.prepare([first])
        assert fake.entered.wait(2)
        service.prepare([ordinary])
        service.prepare([priority], priority=True)
        fake.release.set()
        eventually(lambda: all(job["state"] == "ready" for job in service.preparation()["jobs"]))
        assert fake.upload_ids[:3] == [first, priority, ordinary]
    finally:
        fake.release.set()
        close_service(service)


def test_rag263_explicit_cancel_and_waiter_cancel_remain_separate(tmp_path):
    fake = GatedFakeTransport()
    fake.block_upload = True
    service = make_service(tmp_path, fake)
    document_id = add_doc(service, "cancel two batches", chunks=4)
    try:
        job = service.prepare([document_id])["jobs"][0]
        assert fake.entered.wait(2)
        waiter_cancel = threading.Event()
        waiter_started = threading.Event()
        waiter_result = []
        def wait_for_job():
            waiter_started.set()
            try:
                service.wait_preparation([document_id], cancel=waiter_cancel)
            except BridgeError as exc:
                waiter_result.append(exc.code)
        waiter = threading.Thread(target=wait_for_job)
        waiter.start()
        assert waiter_started.wait(1)
        waiter_cancel.set()
        waiter.join(2)
        assert waiter_result == ["CANCELLED"]
        snapshot = service.preparation()
        assert len(snapshot["jobs"]) == 1 and snapshot["jobs"][0]["id"] == job["id"]
        assert snapshot["jobs"][0]["state"] == "running"
        cancelled = service.cancel_preparation([job["id"]])
        assert cancelled["jobs"][0]["state"] == "running"
        fake.release.set()
        eventually(lambda: service.preparation()["jobs"][0]["state"] == "cancelled")
        assert len(fake.upload_ids) == 1
    finally:
        fake.release.set()
        close_service(service)


def test_rag263_account_scope_isolation_and_close_stops_old_jobs(tmp_path):
    fake = GatedFakeTransport()
    fake.block_upload = True
    old = make_service(tmp_path, fake, subject="old-account")
    old_doc = add_doc(old, "old account doc")
    old_events = []
    old._progress_callback = old_events.append
    old_job = old.prepare([old_doc])["jobs"][0]
    assert fake.entered.wait(2)
    new = make_service(tmp_path, fake, subject="new-account")
    new_doc = add_doc(new, "new account doc")
    try:
        assert old.scope_id != new.scope_id
        assert new.preparation()["jobs"] == []
        old.close()
        event_count_before_close_finishes = len(old_events)
        fake.release.set()
        eventually(lambda: old.preparation()["jobs"][0]["state"] == "cancelled")
        eventually(lambda: old.local._closed, timeout=7)
        assert len(old_events) == event_count_before_close_finishes
        assert new.preparation()["jobs"] == []
        assert new.prepare([new_doc])["jobs"][0]["document_id"] == new_doc
        eventually(lambda: new.preparation()["jobs"][0]["state"] == "ready")
        assert old.preparation()["jobs"][0]["id"] == old_job["id"]
        assert all(row["document_id"] != old_doc for row in new.preparation()["jobs"])
    finally:
        fake.release.set()
        if not old.local._closed:
            close_service(old)
        close_service(new)


@pytest.mark.parametrize("action", ["logout", "switch"])
def test_rag263_account_action_stops_preparations_before_deferred_signal(action, monkeypatch):
    from refactor.learning.app import window as window_module

    calls = []
    class Signal:
        def emit(self): calls.append("signal")
    class Method:
        def __init__(self, label): self.label = label
        def __call__(self): calls.append(self.label)
    rag = SimpleNamespace(stop_preparations=Method("stop"))
    fake_window = SimpleNamespace(
        speech=SimpleNamespace(reset=Method("speech-reset")),
        services=SimpleNamespace(_rag=rag),
        bridge=SimpleNamespace(cancel_pending=Method("bridge-cancel")),
        _sync=SimpleNamespace(stop=Method("sync-stop")),
        switch_requested=Signal(), logout_requested=Signal(),
        verify_requested=Signal(), settings_requested=Signal())
    deferred = []
    monkeypatch.setattr(window_module.QTimer, "singleShot",
        staticmethod(lambda delay, callback: deferred.append((delay, callback))))

    window_module.LearningWindow.request_account_action(fake_window, action)

    assert calls == ["speech-reset", "stop", "bridge-cancel", "sync-stop"]
    assert len(deferred) == 1 and deferred[0][0] == 0
    deferred[0][1]()
    assert calls[-1] == "signal"


def test_rag263_reopen_does_not_auto_upload_and_explicit_resume_starts(tmp_path, monkeypatch):
    import refactor.learning.app.rag as rag_module
    monkeypatch.setattr(rag_module, "BATCH_BYTES", 420)
    fake = GatedFakeTransport()
    fake.block_upload = True
    store = FakeCourseStore(tmp_path / "same-account")
    state = state_for("same-account")
    first = make_service(tmp_path, fake, subject="same-account", store=store)
    document_id = add_doc(first, "resume after restart", chunks=4)
    try:
        first.prepare([document_id])
        assert fake.entered.wait(2)
        first.stop_preparations()
        fake.release.set()
        eventually(lambda: first.preparation()["jobs"][0]["state"] == "cancelled")
        # A fresh service has a new task scope and does not enqueue the partial upload.
        close_service(first)
        before = len(fake.upload_ids)
        reopened = make_service(tmp_path, fake, subject="same-account", store=store)
        try:
            assert reopened.scope_id != first.scope_id
            assert reopened.preparation([document_id])["jobs"] == []
            assert len(fake.upload_ids) == before
            reopened_sources = reopened.sources()
            visible = next(row for row in reopened_sources["documents"] if row["id"] == document_id)
            assert visible["status"] != "ready"
            assert len(fake.upload_ids) == before
            resumed = reopened.prepare([document_id])
            assert resumed["jobs"][0]["state"] in ("queued", "running")
            fake.block_upload = False
            eventually(lambda: reopened.preparation()["jobs"][0]["state"] == "ready")
            assert len(fake.upload_ids) > before
        finally:
            close_service(reopened)
    finally:
        if not first._closed:
            close_service(first)


def test_rag263_failure_log_has_document_batch_and_error_code_without_body_or_token(tmp_path, caplog):
    fake = GatedFakeTransport()
    fake.fail_status = 503
    caplog.set_level(logging.INFO, logger="refactor.runtime")
    service = make_service(tmp_path, fake)
    secret_body = "RAG263_PRIVATE_DOCUMENT_SENTINEL"
    secret_token = "RAG263_BEARER_SECRET_SENTINEL"
    state = state_for("rag263-user", secret_token)
    service.transport._session_provider = lambda: state
    document_id = sha256(secret_body.encode()).hexdigest()
    service.local.save_document(document_id, "本地测试资料", "private.txt",
        [{"text": secret_body}])
    try:
        service.prepare([document_id])
        eventually(lambda: service.preparation()["jobs"][0]["state"] == "failed")
        fields = [record.mf_fields for record in caplog.records
                  if getattr(record, "mf_event", None) == "rag_timing"
                  and record.mf_fields.get("state") == "FAILED"
                  and record.mf_fields.get("stage") == "prepare"]
        authorization = [request.headers.get("Authorization", "") for request in fake.requests]
        assert any(secret_token in value for value in authorization)
        assert fields
        row = fields[-1]
        assert row["document_id"] == document_id[:8]
        assert row["batch"] == 1 and row["code"] == "RAG_HTTP" and row["http_status"] == 503
        assert secret_body not in repr(row) and secret_token not in repr(row)
    finally:
        close_service(service)

