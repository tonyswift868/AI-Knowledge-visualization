"""OpenTelemetry SDK bootstrap and safe business-span helpers."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Iterator, Mapping
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from .context import current_correlation
from .privacy import (
    emit_event,
    safe_span_attributes,
    safe_telemetry_logger,
    scan_telemetry_text,
)


_TRUTHY = {"1", "true", "yes", "on"}
_SAFE_RESOURCE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_INTERNAL_DNS_SUFFIXES = (".internal", ".svc", ".svc.cluster.local")
_ALLOWED_COMPONENTS = {"backend", "worker"}
_ALLOWED_OPERATIONS = {
    "backup",
    "incident",
    "job",
    "probe",
    "provider",
    "request",
    "restore",
    "shutdown",
    "startup",
}
_ALLOWED_OUTCOMES = {"failed", "success", "unavailable"}
_RUNTIME_LOGGER = logging.getLogger("mentorflow.observability.runtime")


@dataclass(frozen=True, slots=True)
class OpenTelemetryStatus:
    enabled: bool
    ready: bool
    exporter: str
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class _RuntimeHandles:
    component: str
    tracer_provider: object
    meter_provider: object
    logger_provider: object
    log_handler: logging.Handler
    operation_counter: object
    operation_duration: object


_STATUS = OpenTelemetryStatus(False, True, "disabled")
_HANDLES: _RuntimeHandles | None = None
_LOCK = threading.RLock()


def otel_enabled() -> bool:
    return str(os.getenv("MENTORFLOW_OTEL_ENABLED") or "").strip().casefold() in _TRUTHY


def _fail_open() -> bool:
    return str(os.getenv("MENTORFLOW_OTEL_FAIL_OPEN") or "").strip().casefold() in _TRUTHY


def _bounded_number(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = str(os.getenv(name) or default).strip()
    try:
        value = float(raw)
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


def _safe_resource_value(value: object, *, fallback: str) -> str:
    candidate = str(value or fallback).strip()
    if not _SAFE_RESOURCE_VALUE.fullmatch(candidate):
        raise ValueError("OpenTelemetry resource value is invalid")
    scan_telemetry_text(candidate)
    return candidate


def _is_internal_host(hostname: str | None) -> bool:
    host = str(hostname or "").strip().casefold().rstrip(".")
    if host in {"localhost", "otel-collector"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return bool(host) and host.endswith(_INTERNAL_DNS_SUFFIXES)


def _validated_internal_url(value: object, *, allowed_ports: set[int]) -> str:
    candidate = str(value or "").strip()
    parsed = urlsplit(candidate)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("OpenTelemetry endpoint port is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not _is_internal_host(parsed.hostname)
        or port not in allowed_ports
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("OpenTelemetry endpoint must be internal and credential-free")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _otlp_base_endpoint() -> str:
    value = (
        os.getenv("MENTORFLOW_OTEL_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or "http://127.0.0.1:4318"
    )
    return _validated_internal_url(value, allowed_ports={4318, 18706})


def _signal_endpoint(base: str, signal: str) -> str:
    parsed = urlsplit(base)
    path = f"{parsed.path.rstrip('/')}/v1/{signal}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def collector_health_endpoint() -> str:
    configured = str(os.getenv("MENTORFLOW_OTEL_HEALTH_ENDPOINT") or "").strip()
    if configured:
        endpoint = _validated_internal_url(configured, allowed_ports={13133})
        parsed = urlsplit(endpoint)
        if parsed.path not in {"", "/", "/health"}:
            raise ValueError("OpenTelemetry health endpoint path is invalid")
        path = "/health" if parsed.path in {"", "/"} else parsed.path
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    base = urlsplit(_otlp_base_endpoint())
    host = f"[{base.hostname}]" if base.hostname and ":" in base.hostname else base.hostname
    return f"{base.scheme}://{host}:13133/health"


def probe_collector_health() -> tuple[bool, str]:
    """Probe only the internal Collector health extension."""

    try:
        request = Request(collector_health_endpoint(), method="GET")
        with build_opener(ProxyHandler({})).open(request, timeout=2.0) as response:
            response.read(512)
            healthy = int(getattr(response, "status", 0)) == 200
    except Exception:
        return False, "OTEL_COLLECTOR_UNREACHABLE"
    return healthy, "OTEL_COLLECTOR_HEALTHY" if healthy else "OTEL_COLLECTOR_UNHEALTHY"


def _sanitized_http_url(value: object, *, preserve_path: bool) -> str | None:
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = f"{host}:{port}" if port else host
    path = (parsed.path or "/") if preserve_path else "/[REDACTED]"
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def _internal_http_session() -> object:
    """Build an exporter session that cannot inherit proxies, netrc, or auth env."""

    import requests

    session = requests.Session()
    session.trust_env = False
    return session


def _sanitize_http_span(span: object, url: object, *, preserve_path: bool) -> None:
    if span is None or not getattr(span, "is_recording", lambda: False)():
        return
    sanitized = _sanitized_http_url(url, preserve_path=preserve_path)
    if sanitized is not None:
        sanitized_path = urlsplit(sanitized).path
        span.set_attribute("url.full", sanitized)
        span.set_attribute("http.url", sanitized)
        span.set_attribute("url.path", sanitized_path)
        span.set_attribute("http.target", sanitized_path)
    try:
        has_query = bool(urlsplit(str(url)).query)
    except ValueError:
        has_query = False
    span.set_attribute("url.query", "[REDACTED]" if has_query else "")


def _fastapi_server_request_hook(span: object, scope: Mapping[str, object]) -> None:
    scheme = str(scope.get("scheme") or "http")
    server = scope.get("server") or ("localhost", 80)
    host = str(server[0]) if isinstance(server, (tuple, list)) and server else "localhost"
    port = int(server[1]) if isinstance(server, (tuple, list)) and len(server) > 1 else 80
    path = str(scope.get("path") or "/")
    _sanitize_http_span(span, f"{scheme}://{host}:{port}{path}", preserve_path=False)
    query = scope.get("query_string")
    if query and getattr(span, "is_recording", lambda: False)():
        span.set_attribute("url.query", "[REDACTED]")


def _httpx_request_hook(span: object, request: object) -> None:
    _sanitize_http_span(span, getattr(request, "url", ""), preserve_path=False)


async def _httpx_async_request_hook(span: object, request: object) -> None:
    _httpx_request_hook(span, request)


def _configure(
    app: object | None,
    *,
    sqlalchemy_engine: object = None,
    component: str,
) -> OpenTelemetryStatus:
    global _HANDLES, _STATUS
    if component not in _ALLOWED_COMPONENTS:
        raise ValueError("unsupported OpenTelemetry component")
    if not otel_enabled():
        _STATUS = OpenTelemetryStatus(False, True, "disabled")
        return _STATUS
    with _LOCK:
        if _HANDLES is not None:
            if _HANDLES.component != component:
                raise RuntimeError("OpenTelemetry is already configured for another component")
            return _STATUS
        endpoint = _otlp_base_endpoint()
        service_name = _safe_resource_value(
            os.getenv("OTEL_SERVICE_NAME"),
            fallback=f"mentorflow-{component}",
        )
        environment = _safe_resource_value(
            os.getenv("MENTORFLOW_ENVIRONMENT"),
            fallback="staging",
        )
        timeout = _bounded_number(
            "MENTORFLOW_OTEL_EXPORT_TIMEOUT_SECONDS", 3.0, 0.1, 10.0
        )
        metric_interval = int(
            _bounded_number(
                "MENTORFLOW_OTEL_METRIC_INTERVAL_MS", 30_000, 5_000, 300_000
            )
        )
        try:
            from opentelemetry import metrics, trace
            from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                OTLPLogExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError as exc:
            _STATUS = OpenTelemetryStatus(True, False, "otlp-http", "SDK_DEPENDENCY_MISSING")
            if not _fail_open():
                raise RuntimeError(
                    "OpenTelemetry is enabled but its Python capability group is missing"
                ) from exc
            return _STATUS

        resource = Resource.create(
            {
                "service.name": service_name,
                "service.namespace": "mentorflow",
                "service.instance.id": component,
                "deployment.environment.name": environment,
            }
        )
        try:
            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=_signal_endpoint(endpoint, "traces"),
                        headers={"x-mentorflow-telemetry": "runtime"},
                        timeout=timeout,
                        session=_internal_http_session(),
                    )
                )
            )
            meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[
                    PeriodicExportingMetricReader(
                        OTLPMetricExporter(
                            endpoint=_signal_endpoint(endpoint, "metrics"),
                            headers={"x-mentorflow-telemetry": "runtime"},
                            timeout=timeout,
                            session=_internal_http_session(),
                        ),
                        export_interval_millis=metric_interval,
                        export_timeout_millis=int(timeout * 1000),
                    )
                ],
            )
            logger_provider = LoggerProvider(resource=resource)
            logger_provider.add_log_record_processor(
                BatchLogRecordProcessor(
                    OTLPLogExporter(
                        endpoint=_signal_endpoint(endpoint, "logs"),
                        headers={"x-mentorflow-telemetry": "runtime"},
                        timeout=timeout,
                        session=_internal_http_session(),
                    ),
                    schedule_delay_millis=1_000,
                    export_timeout_millis=int(timeout * 1000),
                    max_queue_size=512,
                    max_export_batch_size=128,
                )
            )
            trace.set_tracer_provider(tracer_provider)
            metrics.set_meter_provider(meter_provider)
            safe_logger = safe_telemetry_logger()
            safe_logger.setLevel(logging.INFO)
            safe_logger.propagate = False
            log_handler = LoggingHandler(
                level=logging.INFO,
                logger_provider=logger_provider,
            )
            safe_logger.addHandler(log_handler)
            meter = meter_provider.get_meter("mentorflow.observability.runtime")
            operation_counter = meter.create_counter(
                "mentorflow.runtime.operations",
                unit="{operation}",
                description="Bounded backend and worker runtime operations",
            )
            operation_duration = meter.create_histogram(
                "mentorflow.runtime.operation.duration",
                unit="ms",
                description="Bounded backend and worker operation duration",
            )
            if app is not None:
                FastAPIInstrumentor.instrument_app(
                    app,
                    tracer_provider=tracer_provider,
                    meter_provider=meter_provider,
                    server_request_hook=_fastapi_server_request_hook,
                    excluded_urls=(
                        "/health/live,/health/ready,/health/startup,"
                        "/internal/metrics"
                    ),
                    http_capture_headers_server_request=[],
                    http_capture_headers_server_response=[],
                )
            HTTPXClientInstrumentor().instrument(
                tracer_provider=tracer_provider,
                meter_provider=meter_provider,
                request_hook=_httpx_request_hook,
                async_request_hook=_httpx_async_request_hook,
            )
            if sqlalchemy_engine is not None:
                SQLAlchemyInstrumentor().instrument(
                    engine=sqlalchemy_engine,
                    tracer_provider=tracer_provider,
                    meter_provider=meter_provider,
                    enable_commenter=False,
                    enable_attribute_commenter=False,
                )
        except Exception as exc:
            _STATUS = OpenTelemetryStatus(True, False, "otlp-http", "CONFIGURATION_FAILED")
            if not _fail_open():
                raise RuntimeError("OpenTelemetry configuration failed") from exc
            return _STATUS

        _HANDLES = _RuntimeHandles(
            component=component,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            logger_provider=logger_provider,
            log_handler=log_handler,
            operation_counter=operation_counter,
            operation_duration=operation_duration,
        )
        _STATUS = OpenTelemetryStatus(True, True, "otlp-http")
        record_otel_operation(component, "startup", outcome="success")
        return _STATUS


def configure_open_telemetry(
    app: object,
    *,
    sqlalchemy_engine: object = None,
) -> OpenTelemetryStatus:
    """Export backend traces, metrics and safe structured logs over OTLP/HTTP."""

    return _configure(app, sqlalchemy_engine=sqlalchemy_engine, component="backend")


def configure_worker_open_telemetry(
    *,
    sqlalchemy_engine: object = None,
) -> OpenTelemetryStatus:
    """Configure the same bounded telemetry contract for the production worker."""

    return _configure(None, sqlalchemy_engine=sqlalchemy_engine, component="worker")


def otel_status() -> OpenTelemetryStatus:
    return _STATUS


def record_otel_operation(
    component: str,
    operation: str,
    *,
    outcome: str,
    duration_ms: float = 0.0,
) -> None:
    """Record only bounded, low-cardinality runtime metrics."""

    normalized_component = str(component).strip().casefold()
    normalized_operation = str(operation).strip().casefold()
    normalized_outcome = str(outcome).strip().casefold()
    if (
        normalized_component not in _ALLOWED_COMPONENTS
        or normalized_operation not in _ALLOWED_OPERATIONS
        or normalized_outcome not in _ALLOWED_OUTCOMES
    ):
        raise ValueError("unsupported OpenTelemetry metric attributes")
    handles = _HANDLES
    if handles is None:
        return
    attributes = {
        "component": normalized_component,
        "operation": normalized_operation,
        "outcome": normalized_outcome,
    }
    handles.operation_counter.add(1, attributes)
    handles.operation_duration.record(
        max(0.0, min(float(duration_ms), 300_000.0)),
        attributes,
    )


def emit_runtime_probe(component: str) -> None:
    """Emit one secret-free span/log/metric correlation sample for staging evidence."""

    feature = "api" if component == "backend" else "jobs"
    with business_span(
        "mentorflow.runtime.probe",
        {
            "mentorflow.feature": feature,
            "mentorflow.operation": "probe",
        },
    ):
        record_otel_operation(component, "probe", outcome="success")
        emit_event(
            _RUNTIME_LOGGER,
            "otel_runtime_probe",
            component=component,
            feature=feature,
            outcome="success",
        )


def force_flush_open_telemetry(timeout_millis: int = 5_000) -> bool:
    handles = _HANDLES
    if handles is None:
        return True
    timeout = max(100, min(int(timeout_millis), 10_000))
    results = []
    for provider in (
        handles.logger_provider,
        handles.meter_provider,
        handles.tracer_provider,
    ):
        force_flush = getattr(provider, "force_flush", None)
        if callable(force_flush):
            results.append(bool(force_flush(timeout_millis=timeout)))
    return all(results)


def shutdown_open_telemetry(timeout_millis: int = 5_000) -> None:
    global _HANDLES
    with _LOCK:
        handles = _HANDLES
        if handles is None:
            return
        record_otel_operation(handles.component, "shutdown", outcome="success")
        force_flush_open_telemetry(timeout_millis)
        safe_telemetry_logger().removeHandler(handles.log_handler)
        for provider in (
            handles.logger_provider,
            handles.meter_provider,
            handles.tracer_provider,
        ):
            shutdown = getattr(provider, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown(timeout_millis=timeout_millis)
                except TypeError:
                    shutdown()
        _HANDLES = None


@contextmanager
def business_span(
    name: str,
    attributes: Mapping[str, object] | None = None,
) -> Iterator[object | None]:
    """Create an OTel span when available, keeping attribute values bounded."""

    context = current_correlation()
    correlation_attributes = {
        f"mentorflow.{key}": value
        for key, value in (context.log_fields() if context else {}).items()
    }
    safe_attributes = safe_span_attributes(
        {**correlation_attributes, **(attributes or {})}
    )
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("mentorflow.observability")
        with tracer.start_as_current_span(name, attributes=safe_attributes) as span:
            yield span
    except ImportError:
        yield None


__all__ = [
    "OpenTelemetryStatus",
    "business_span",
    "collector_health_endpoint",
    "configure_open_telemetry",
    "configure_worker_open_telemetry",
    "emit_runtime_probe",
    "force_flush_open_telemetry",
    "otel_enabled",
    "otel_status",
    "probe_collector_health",
    "record_otel_operation",
    "shutdown_open_telemetry",
]
