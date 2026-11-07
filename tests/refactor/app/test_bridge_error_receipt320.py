"""BUG-215 follow-up: optional model-error presentation cannot lose a reply."""
import json
import time
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from refactor.learning.app import handlers
from refactor.learning.app.bridge import AppBridge, BridgeError
from refactor.learning.connections.errors import ModelError
from .test_app_window_layer import qapp  # noqa: F401


class Gateway:
    def __init__(self, *, config_error=None):
        self.config_error = config_error

    def get_configuration(self):
        if self.config_error:
            raise self.config_error
        return None

    def list_presets(self):
        return []

    def search_supported(self):
        return False


class ReportingGateway(Gateway):
    def __init__(self, *, config_error=None, reporter):
        super().__init__(config_error=config_error)
        self.reporter = reporter

    def report_error(self, error, **kwargs):
        return self.reporter(error, **kwargs)


@pytest.fixture
def bridge_harness(qapp):
    sent, failed, session_failed, completed, futures = [], [], [], [], []
    bridge = AppBridge()
    bridge.event.connect(lambda raw: sent.append(json.loads(raw)))
    bridge.request_failed.connect(failed.append, Qt.ConnectionType.DirectConnection)
    bridge.request_failed_for_session.connect(
        lambda code, epoch: session_failed.append((code, epoch)), Qt.ConnectionType.DirectConnection)
    bridge.request_completed.connect(completed.append, Qt.ConnectionType.DirectConnection)
    submit = bridge._pool.submit

    def capture_submit(*args, **kwargs):
        future = submit(*args, **kwargs)
        futures.append(future)
        return future

    bridge._pool.submit = capture_submit
    window = SimpleNamespace(services=None)
    handlers.register(bridge, window)
    window.services = SimpleNamespace(gateway=Gateway())
    yield SimpleNamespace(bridge=bridge, window=window, sent=sent, failed=failed,
                          session_failed=session_failed, completed=completed, futures=futures)
    bridge.cancel_pending()
    bridge.close()
    bridge._pool.shutdown(wait=True)


def _call(harness, rid="model-list"):
    harness.bridge.call(json.dumps({"id": rid, "method": "models.list", "params": {}}))
    assert len(harness.futures) == 1
    return harness.futures[0]


def _responses(harness, rid):
    return [item for item in harness.sent if item.get("id") == rid and item.get("type") == "response"]


def _pump_briefly(qapp, predicate, timeout=0.4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.005)


def test_cancelled_model_error_gets_one_cancelled_receipt_and_cleans_pending(bridge_harness, qapp):
    entered, release = Event(), Event()

    class BlockingGateway(Gateway):
        def get_configuration(self):
            entered.set()
            assert release.wait(4)
            raise ModelError("MODEL_CONFIG_INVALID", "synthetic config read failure")

    bridge_harness.window.services.gateway = BlockingGateway()
    future = _call(bridge_harness, "cancelled-model-list")
    try:
        assert entered.wait(2)
        bridge_harness.bridge.suspend_session("NATIVE_SESSION_SUPERSEDED", "session rejected")
        release.set()
        escaped = future.exception(timeout=3)
        _pump_briefly(qapp, lambda: bool(_responses(bridge_harness, "cancelled-model-list")))
        responses = _responses(bridge_harness, "cancelled-model-list")
        assert escaped is None
        assert len(responses) == 1
        assert responses[0]["error"]["code"] == "CANCELLED"
        assert "model_fault" not in responses[0]["error"]
        assert "cancelled-model-list" not in bridge_harness.bridge._pending
        assert bridge_harness.failed == [] and bridge_harness.session_failed == []
    finally:
        release.set()


def test_reporter_exception_keeps_original_model_error_receipt(bridge_harness, qapp):
    original = ModelError("MODEL_CONFIG_INVALID", "synthetic configuration is invalid")

    def fail_reporter(_error, **_kwargs):
        raise BridgeError("NATIVE_SESSION_SUPERSEDED", "observer failure must stay local")

    bridge_harness.window.services.gateway = ReportingGateway(config_error=original, reporter=fail_reporter)
    future = _call(bridge_harness, "reporter-failed")
    assert future.exception(timeout=3) is None
    _pump_briefly(qapp, lambda: bool(_responses(bridge_harness, "reporter-failed")))
    response = _responses(bridge_harness, "reporter-failed")
    assert len(response) == 1 and not response[0]["ok"]
    assert response[0]["error"]["code"] == "MODEL_CONFIG_INVALID"
    assert response[0]["error"]["message"] == "synthetic configuration is invalid"
    assert bridge_harness.failed == ["MODEL_CONFIG_INVALID"]
    assert bridge_harness.bridge._session_error is None


def test_normal_model_formatter_keeps_fault_and_presentation(bridge_harness, qapp):
    bridge_harness.window.services.gateway = Gateway(
        config_error=ModelError("MODEL_CONFIG_INVALID", "synthetic configuration is invalid"))
    future = _call(bridge_harness, "formatted-model-error")
    assert future.exception(timeout=3) is None
    _pump_briefly(qapp, lambda: bool(_responses(bridge_harness, "formatted-model-error")))
    response = _responses(bridge_harness, "formatted-model-error")
    assert len(response) == 1 and not response[0]["ok"]
    fault = response[0]["error"]["model_fault"]
    assert fault["category"] == "software"
    assert fault["code"] == "MODEL_CONFIG_INVALID"
    assert response[0]["error"]["message"] == fault["message"]
    assert bridge_harness.failed == ["MODEL_CONFIG_INVALID"]


@pytest.mark.parametrize("reporter_throws", [False, True])
def test_cancellation_inside_reporter_suppresses_old_epoch_failure(bridge_harness, qapp, reporter_throws):
    entered, release = Event(), Event()

    def blocked_reporter(_error, **_kwargs):
        entered.set()
        assert release.wait(4)
        if reporter_throws:
            raise BridgeError("NATIVE_SESSION_SUPERSEDED", "observer failed after cancellation")
        return {"category": "software", "code": "MODEL_CONFIG_INVALID",
                "message": "obsolete model-fault presentation"}

    bridge_harness.window.services.gateway = ReportingGateway(
        config_error=ModelError("MODEL_CONFIG_INVALID", "synthetic configuration is invalid"),
        reporter=blocked_reporter)
    future = _call(bridge_harness, "cancel-during-reporter")
    try:
        assert entered.wait(2)
        bridge_harness.bridge.suspend_session("NATIVE_SESSION_SUPERSEDED", "session rejected")
        bridge_harness.bridge.begin_session_epoch()
        bridge_harness.bridge.require_session()
        release.set()
        assert future.exception(timeout=3) is None
        _pump_briefly(qapp, lambda: bool(_responses(bridge_harness, "cancel-during-reporter")))
        response = _responses(bridge_harness, "cancel-during-reporter")
        assert len(response) == 1
        assert response[0]["error"]["code"] == "CANCELLED"
        assert "model_fault" not in response[0]["error"]
        assert bridge_harness.failed == [] and bridge_harness.session_failed == []
        assert "cancel-during-reporter" not in bridge_harness.bridge._pending
    finally:
        release.set()
