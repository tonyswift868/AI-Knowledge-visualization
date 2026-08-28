from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest
import yaml
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from app.observability.runtime import (
    collector_health_endpoint,
    configure_worker_open_telemetry,
    otel_status,
    probe_collector_health,
)


REPO_ROOT = Path(__file__).parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
OTLP_PORT = 18706
HEALTH_PORT = 13133
TEST_SECRET = "sk-ABCDEFGHIJKLMNOPQRSTUVWX"


class _CaptureHandler(BaseHTTPRequestHandler):
    payloads: dict[str, list[bytes]] = defaultdict(list)
    request_headers: list[dict[str, str]] = []
    lock = threading.Lock()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path == "/health":
            body = b'{"status":"healthy"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        with self.lock:
            self.payloads[self.path].append(body)
            self.request_headers.append(
                {key.casefold(): value for key, value in self.headers.items()}
            )
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def otlp_capture() -> Iterator[dict[str, list[bytes]]]:
    _CaptureHandler.payloads = defaultdict(list)
    _CaptureHandler.request_headers = []
    otlp = ThreadingHTTPServer(("127.0.0.1", OTLP_PORT), _CaptureHandler)
    health = ThreadingHTTPServer(("127.0.0.1", HEALTH_PORT), _CaptureHandler)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (otlp, health)
    ]
    for thread in threads:
        thread.start()
    try:
        yield _CaptureHandler.payloads
    finally:
        for server in (otlp, health):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)


def _resource_attributes(resource: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for attribute in resource.attributes:
        value = attribute.value
        field = value.WhichOneof("value")
        result[attribute.key] = getattr(value, field) if field else None
    return result


def _kv_attributes(attributes: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for attribute in attributes:
        value = attribute.value
        field = value.WhichOneof("value")
        result[attribute.key] = getattr(value, field) if field else None
    return result


def _run_component(component: str) -> subprocess.CompletedProcess[str]:
    shared = """
from app.observability.context import correlation_scope, new_correlation_context
from app.observability.runtime import emit_runtime_probe, force_flush_open_telemetry, shutdown_open_telemetry
context = new_correlation_context(request_id=COMPONENT + '-request', feature='api' if COMPONENT == 'backend' else 'jobs')
with correlation_scope(context, organization_id='org-e1-safe', workspace_id='workspace-e1-safe'):
    emit_runtime_probe(COMPONENT)
assert force_flush_open_telemetry(5000)
shutdown_open_telemetry(5000)
print(COMPONENT + '=EXPORTED')
"""
    if component == "backend":
        source = (
            "from fastapi import FastAPI\n"
            "from fastapi.testclient import TestClient\n"
            "from app.observability.runtime import configure_open_telemetry\n"
            "COMPONENT='backend'\n"
            "app=FastAPI()\n"
            "@app.get('/probe/{item_id}')\n"
            "def probe(item_id: str): return {'status':'ok'}\n"
            "configure_open_telemetry(app)\n"
            f"with TestClient(app) as client:\n"
            f"    response=client.get('/probe/{TEST_SECRET}?api_key={TEST_SECRET}', headers={{'Authorization':'Bearer {TEST_SECRET}'}})\n"
            "    assert response.status_code == 200\n"
            + shared
        )
    else:
        source = (
            "from app.observability.runtime import configure_worker_open_telemetry\n"
            "COMPONENT='worker'\n"
            "configure_worker_open_telemetry()\n"
            + shared
        )
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONPATH": str(BACKEND_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "MENTORFLOW_OTEL_ENABLED": "true",
            "MENTORFLOW_OTEL_ENDPOINT": f"http://127.0.0.1:{OTLP_PORT}",
            "MENTORFLOW_OTEL_HEALTH_ENDPOINT": f"http://127.0.0.1:{HEALTH_PORT}/health",
            "MENTORFLOW_OTEL_EXPORT_TIMEOUT_SECONDS": "2",
            "MENTORFLOW_OTEL_METRIC_INTERVAL_MS": "5000",
            "MENTORFLOW_ENVIRONMENT": "staging",
            "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Bearer {TEST_SECRET}",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"Authorization=Bearer {TEST_SECRET}",
            "OTEL_EXPORTER_OTLP_METRICS_HEADERS": f"Authorization=Bearer {TEST_SECRET}",
            "OTEL_EXPORTER_OTLP_LOGS_HEADERS": f"Authorization=Bearer {TEST_SECRET}",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        }
    )
    environment.pop("OTEL_SERVICE_NAME", None)
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )


def _parse_traces(payloads: list[bytes]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for payload in payloads:
        request = ExportTraceServiceRequest.FromString(payload)
        for resource_spans in request.resource_spans:
            resource = _resource_attributes(resource_spans.resource)
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    result.append(
                        {
                            "service": resource.get("service.name"),
                            "name": span.name,
                            "trace_id": bytes(span.trace_id),
                            "attributes": _kv_attributes(span.attributes),
                        }
                    )
    return result


def _parse_logs(payloads: list[bytes]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for payload in payloads:
        request = ExportLogsServiceRequest.FromString(payload)
        for resource_logs in request.resource_logs:
            resource = _resource_attributes(resource_logs.resource)
            for scope_logs in resource_logs.scope_logs:
                for record in scope_logs.log_records:
                    result.append(
                        {
                            "service": resource.get("service.name"),
                            "trace_id": bytes(record.trace_id),
                            "body": record.body.string_value,
                            "attributes": _kv_attributes(record.attributes),
                        }
                    )
    return result


def _parse_metrics(payloads: list[bytes]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for payload in payloads:
        request = ExportMetricsServiceRequest.FromString(payload)
        for resource_metrics in request.resource_metrics:
            resource = _resource_attributes(resource_metrics.resource)
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    data = metric.WhichOneof("data")
                    points = getattr(metric, data).data_points if data else ()
                    for point in points:
                        result.append(
                            {
                                "service": resource.get("service.name"),
                                "name": metric.name,
                                "attributes": _kv_attributes(point.attributes),
                                "exemplar_trace_ids": {
                                    bytes(exemplar.trace_id)
                                    for exemplar in point.exemplars
                                    if exemplar.trace_id
                                },
                            }
                        )
    return result


def test_disabled_runtime_is_dependency_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MENTORFLOW_OTEL_ENABLED", raising=False)
    status = configure_worker_open_telemetry()
    assert status.to_dict() == {
        "enabled": False,
        "ready": True,
        "exporter": "disabled",
        "reason": None,
    }
    assert otel_status() == status


def test_public_or_credential_bearing_endpoints_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENTORFLOW_OTEL_ENABLED", "true")
    for endpoint in (
        "https://telemetry.example.com:4318",
        "http://user:password@127.0.0.1:4318",
        "http://127.0.0.1:4318?token=secret",
    ):
        monkeypatch.setenv("MENTORFLOW_OTEL_ENDPOINT", endpoint)
        with pytest.raises(ValueError, match="internal and credential-free"):
            configure_worker_open_telemetry()
    monkeypatch.setenv("MENTORFLOW_OTEL_ENDPOINT", f"http://127.0.0.1:{OTLP_PORT}")
    monkeypatch.setenv("OTEL_SERVICE_NAME", TEST_SECRET)
    with pytest.raises(ValueError, match="forbidden telemetry content"):
        configure_worker_open_telemetry()


def test_collector_unavailable_keeps_worker_process_alive() -> None:
    source = """
from app.observability.runtime import configure_worker_open_telemetry, emit_runtime_probe, force_flush_open_telemetry, probe_collector_health, shutdown_open_telemetry
status = configure_worker_open_telemetry()
emit_runtime_probe('worker')
healthy, reason = probe_collector_health()
force_flush_open_telemetry(1000)
shutdown_open_telemetry(1000)
print(f'PROCESS_ALIVE={status.ready};HEALTHY={healthy};REASON={reason}')
"""
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONPATH": str(BACKEND_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "MENTORFLOW_OTEL_ENABLED": "true",
            "MENTORFLOW_OTEL_ENDPOINT": "http://127.0.0.2:18706",
            "MENTORFLOW_OTEL_HEALTH_ENDPOINT": "http://127.0.0.2:13133/health",
            "MENTORFLOW_OTEL_EXPORT_TIMEOUT_SECONDS": "0.1",
            "MENTORFLOW_OTEL_METRIC_INTERVAL_MS": "5000",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "PROCESS_ALIVE=True;HEALTHY=False;REASON=OTEL_COLLECTOR_UNREACHABLE"
        in result.stdout
    )


def test_internal_health_probe_is_separate_from_sdk_status(
    otlp_capture: dict[str, list[bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MENTORFLOW_OTEL_HEALTH_ENDPOINT",
        f"http://127.0.0.1:{HEALTH_PORT}/health",
    )
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    assert collector_health_endpoint().endswith(f":{HEALTH_PORT}/health")
    assert probe_collector_health() == (True, "OTEL_COLLECTOR_HEALTHY")
    assert otlp_capture == {}


def test_backend_and_worker_export_correlated_secret_free_signals(
    otlp_capture: dict[str, list[bytes]],
) -> None:
    backend = _run_component("backend")
    worker = _run_component("worker")
    assert backend.returncode == 0, backend.stdout + backend.stderr
    assert worker.returncode == 0, worker.stdout + worker.stderr
    assert "backend=EXPORTED" in backend.stdout
    assert "worker=EXPORTED" in worker.stdout
    assert set(otlp_capture) == {"/v1/traces", "/v1/metrics", "/v1/logs"}

    traces = _parse_traces(otlp_capture["/v1/traces"])
    logs = _parse_logs(otlp_capture["/v1/logs"])
    metrics = _parse_metrics(otlp_capture["/v1/metrics"])
    expected_services = {"mentorflow-backend", "mentorflow-worker"}
    assert expected_services <= {item["service"] for item in traces}
    assert expected_services <= {item["service"] for item in logs}
    assert expected_services <= {item["service"] for item in metrics}

    for service in expected_services:
        probe_spans = [
            item
            for item in traces
            if item["service"] == service and item["name"] == "mentorflow.runtime.probe"
        ]
        probe_logs = [
            item
            for item in logs
            if item["service"] == service
            and json.loads(str(item["body"]))["event"] == "otel_runtime_probe"
        ]
        probe_metrics = [
            item
            for item in metrics
            if item["service"] == service
            and item["name"] in {
                "mentorflow.runtime.operations",
                "mentorflow.runtime.operation.duration",
            }
            and item["attributes"].get("operation") == "probe"
        ]
        assert probe_spans and probe_logs and probe_metrics
        trace_ids = {item["trace_id"] for item in probe_spans}
        assert trace_ids & {item["trace_id"] for item in probe_logs}
        assert trace_ids & set().union(
            *(item["exemplar_trace_ids"] for item in probe_metrics)
        )
        for span in probe_spans:
            assert span["attributes"]["mentorflow.organization_id"] == "org-e1-safe"
            assert span["attributes"]["mentorflow.workspace_id"] == "workspace-e1-safe"
        for metric in probe_metrics:
            assert "organization_id" not in metric["attributes"]
            assert "workspace_id" not in metric["attributes"]

    serialized = b"".join(
        payload
        for signal_payloads in otlp_capture.values()
        for payload in signal_payloads
    )
    assert TEST_SECRET.encode() not in serialized
    assert b"authorization" not in serialized.lower()
    assert _CaptureHandler.request_headers
    assert all(
        headers.get("x-mentorflow-telemetry") == "runtime"
        and "authorization" not in headers
        for headers in _CaptureHandler.request_headers
    )


def test_minimal_collector_and_compose_are_internal_bounded_and_complete() -> None:
    config_path = REPO_ROOT / "deploy" / "enterprise" / "staging" / "otel-collector.minimal.yml"
    compose_path = REPO_ROOT / "deploy" / "enterprise" / "staging" / "compose.orchestrator.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert set(config["service"]["pipelines"]) == {"traces", "metrics", "logs"}
    assert config["service"]["extensions"] == ["health_check"]
    assert config["extensions"]["health_check"]["endpoint"] == "0.0.0.0:13133"
    assert config["extensions"]["health_check"]["path"] == "/health"
    exporter = config["exporters"]["file/evidence"]
    assert exporter["create_directory"] is True
    assert exporter["directory_permissions"] == "0700"
    assert exporter["rotation"] == {
        "max_megabytes": 4,
        "max_days": 1,
        "max_backups": 2,
        "localtime": False,
    }
    collector = compose["services"]["otel-collector"]
    assert "ports" not in collector
    assert collector["networks"] == ["internal"]
    assert collector["mem_reservation"] == "${OTEL_COLLECTOR_MEMORY_RESERVATION:-64m}"
    assert collector["mem_limit"] == "${OTEL_COLLECTOR_MEMORY_LIMIT:-192m}"
    assert collector["cpus"] == "${OTEL_COLLECTOR_CPUS:-0.2}"
    assert compose["networks"]["internal"]["internal"] is True
    environment = compose["x-app-environment"]
    assert environment["MENTORFLOW_OTEL_ENABLED"] == "true"
    assert environment["MENTORFLOW_OTEL_ENDPOINT"] == "http://otel-collector:4318"
    assert environment["MENTORFLOW_OTEL_HEALTH_ENDPOINT"] == "http://otel-collector:13133/health"
