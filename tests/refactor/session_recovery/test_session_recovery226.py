"""A late bridge auth error must not undo same-account session recovery."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from types import SimpleNamespace

import certifi
import httpx
import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication

from refactor.learning.app.bridge import AppBridge, BridgeError
from refactor.learning.app.sync import WindowSync
from refactor.learning.app.window import LearningWindow
from refactor.learning.course.store import CourseStore
from refactor.learning.services.session_guard import SessionGuard
from refactor.learning.sync_identity import PlanScope
from tests.refactor.sync_course.test_sync_course import Server


@pytest.fixture(scope="module")
def qt_app():
    # This module exercises WindowSync's QWidget-facing recovery path. Use the
    # offscreen platform so test collection never requires or takes a window.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _pump(app, condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    app.processEvents()
    assert condition(), "Qt work did not settle before timeout"


def _wait_worker_without_events(service, timeout=8.0):
    """Hold queued Qt callbacks while allowing the current sync worker to end."""
    deadline = time.monotonic() + timeout
    while service._future is not None and time.monotonic() < deadline:
        if service._future[0].done():
            break
        time.sleep(0.002)
    assert service._future is not None and service._future[0].done(), "sync worker did not finish"
    service._tick()


def _guard_context():
    issuer, subject = "https://identity.invalid/realm", "226-standalone-guard"
    hashes = ":".join(hashlib.sha256(value.encode()).hexdigest() for value in (issuer, subject))
    account_id = "mfa1_" + hashlib.sha256(("mentorflow.account.v1:" + hashes).encode()).hexdigest()
    login = SimpleNamespace(
        access_token="synthetic-guard-token",
        identity=SimpleNamespace(account_id=account_id, issuer=issuer, subject=subject,
                                 organization_id="org-226", workspace_id="workspace-226"),
        server_state={
            "authenticated": True,
            "account": {"account_id": account_id, "subject_id": subject},
            "context": {"organization_id": "org-226", "workspace_id": "workspace-226"},
        },
    )
    return {"account_id": account_id, "session_provider": lambda: login,
            "api_base_url": "https://identity.invalid", "ca_bundle": certifi.where()}


class _Window(QObject):
    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        self.settings = object()
        self.session_notice = None

    def set_session_notice(self, *_args, **_kwargs):
        pass

    def clear_session_notice(self):
        self.session_notice = None

    def settings_changed(self, *_args):
        pass


def test_guard_queued_error_is_fenced_by_guard_restart(qt_app):
    """The second queue boundary also protects standalone Guard restarts."""
    guard = SessionGuard(
        _guard_context(),
        http_transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"state": "active"})),
    )
    events = []
    guard.reauth_required.connect(lambda: events.append("reauth"))
    try:
        guard.set_session_epoch(7)
        guard.start()
        _pump(qt_app, lambda: guard._future is None and guard._active)

        guard.note_error("REAUTH_REQUIRED", 7)
        guard.stop()
        guard.start()  # same account session, newer Guard lifecycle epoch
        _pump(qt_app, lambda: guard._future is None and guard._active)
        assert events == []

        guard.note_error("REAUTH_REQUIRED", 7)
        _pump(qt_app, lambda: events == ["reauth"])
        assert not guard._active
    finally:
        guard.dispose()


def test_old_bridge_error_cannot_reauth_recovered_real_sync(qt_app, tmp_path):
    issuer, subject = "https://identity.invalid/realm", "226-user"
    hashes = ":".join(hashlib.sha256(value.encode()).hexdigest() for value in (issuer, subject))
    account_id = "mfa1_" + hashlib.sha256(("mentorflow.account.v1:" + hashes).encode()).hexdigest()
    scope = PlanScope(account_id, issuer, subject, "org-226", "workspace-226")
    login = SimpleNamespace(
        identity=SimpleNamespace(account_id=account_id, issuer=issuer, subject=subject,
                                 organization_id=scope.organization_id, workspace_id=scope.workspace_id),
        server_state={
            "authenticated": True,
            "account": {"account_id": account_id, "subject_id": subject},
            "context": {"organization_id": scope.organization_id, "workspace_id": scope.workspace_id},
        },
        access_token="synthetic-before-recovery",
    )
    server = Server(scope)
    reject_sync = {"enabled": False}
    api_paths = []

    def transport(request):
        api_paths.append(request.url.path)
        if request.url.path == "/api/enterprise/auth/native/session":
            return httpx.Response(200, json={"state": "active"})
        if reject_sync["enabled"] and request.url.path.startswith("/api/enterprise/sync/v1/"):
            return httpx.Response(401, json={"code": "REAUTH_REQUIRED"})
        return server.handle(request)

    store = CourseStore(account_id, tmp_path / "course-data")
    store.create_plan(plan_id="bug185-initial-plan", goal="initial sync")
    bridge = AppBridge()
    window = _Window(bridge)
    services = SimpleNamespace(
        account_id=account_id,
        session_provider=lambda: login,
        api_base_url="https://identity.invalid",
        ca_bundle=certifi.where(),
        settings=window.settings,
        store=store,
    )
    sync = WindowSync(window, services, http_transport=httpx.MockTransport(transport))
    holder = SimpleNamespace(_disposed=False, _sync=sync, services=services, bridge=bridge)
    guard_events = []
    sync.guard.reauth_required.connect(lambda: guard_events.append("reauth"))
    old_failure_delivered_by_worker = threading.Event()
    emitted = []
    bridge.request_failed_for_session.connect(
        lambda code, epoch: (emitted.append((code, epoch)), old_failure_delivered_by_worker.set()),
        Qt.ConnectionType.DirectConnection,
    )

    def expired_request(_params, _context):
        raise BridgeError("REAUTH_REQUIRED", "synthetic expired request")

    bridge.register("probe.expired", expired_request)
    try:
        # The real CourseStore, CourseSyncService, WindowSync and SessionGuard
        # finish an initial sync against the test suite's local protocol server.
        sync.start()
        _pump(qt_app, lambda: sync.service.status()["state"] == "synced"
              and not store.pending_records(20))
        assert sync.guard._active
        old_generation = sync._session_epoch

        # Let AppBridge finish an actual worker request, but do not pump Qt;
        # its request_failed_for_session delivery to WindowSync stays queued.
        bridge.call(json.dumps({"id": "queued-old-auth", "method": "probe.expired", "params": {}}))
        assert old_failure_delivered_by_worker.wait(3), "AppBridge did not produce the queued auth failure"
        assert emitted == [("REAUTH_REQUIRED", old_generation)]

        # Drive a real CourseSync 401 through its worker while preserving the
        # old AppBridge Qt event. The failure must stop syncing and retain the
        # newly created outbox row until the same account recovers.
        store.create_plan(plan_id="bug185-pending-plan", goal="preserve pending record")
        reject_sync["enabled"] = True
        sync.service.sync_now()
        _wait_worker_without_events(sync.service)
        failed_status = sync.service.status()
        assert failed_status["state"] == "reauth", failed_status
        assert not sync.guard._active
        assert store.pending_records(20), "401 must keep the unacknowledged CourseStore row"

        # Reuse the real retained WindowSync through LearningWindow's actual
        # session_recovered method; the old AppBridge signal is still queued.
        reject_sync["enabled"] = False
        login.access_token = "synthetic-after-recovery"
        LearningWindow.session_recovered(holder)
        recovered_generation = sync._session_epoch
        assert recovered_generation > old_generation
        assert holder._sync is sync
        assert sync.guard._active and sync.service._active
        assert sync.service.status()["state"] in {"syncing", "retrying", "synced"}

        # Drain the old Bridge -> WindowSync -> SessionGuard queue. The
        # outbox must then complete against the active local fixture server.
        _pump(qt_app, lambda: sync.service.status()["state"] == "synced"
              and not store.pending_records(20), timeout=12)
        qt_app.processEvents()
        assert sync.guard._active
        assert guard_events == []
        assert not store.pending_records(20)

        # A genuinely current auth error still traverses both real queued
        # boundaries and pauses sync, while retaining a new local plan.
        store.create_plan(plan_id="bug185-new-auth-plan", goal="new auth error remains effective")
        bridge.call(json.dumps({"id": "queued-current-auth", "method": "probe.expired", "params": {}}))
        _pump(qt_app, lambda: sync.service.status()["state"] == "reauth")
        assert guard_events == ["reauth"]
        assert not sync.guard._active and not sync.service._active
        assert store.pending_records(20), "current auth failure must not discard local pending records"
        assert any(path == "/api/enterprise/auth/native/session" for path in api_paths)
    finally:
        sync.dispose()
        bridge.close()
        store.close()
