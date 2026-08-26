"""OpenTelemetry SDK bootstrap and safe business-span helpers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Iterator, Mapping

from .context import current_correlation
from .privacy import safe_span_attributes


@dataclass(frozen=True, slots=True)
class OpenTelemetryStatus:
    enabled: bool
    ready: bool
    exporter: str
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_STATUS = OpenTelemetryStatus(False, True, "disabled")


def _enabled() -> bool:
    return str(os.getenv("MENTORFLOW_OTEL_ENABLED") or "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def configure_open_telemetry(app: object, *, sqlalchemy_engine: object = None) -> OpenTelemetryStatus:
    """Export FastAPI, HTTPX, SQLAlchemy/PostgreSQL and metrics to OTLP."""

    global _STATUS
    if not _enabled():
        _STATUS = OpenTelemetryStatus(False, True, "disabled")
        return _STATUS
    endpoint = str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "http://127.0.0.1:4317").strip()
    service_name = str(os.getenv("OTEL_SERVICE_NAME") or "mentorflow-backend").strip()
    environment = str(os.getenv("MENTORFLOW_ENVIRONMENT") or "staging").strip()
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        _STATUS = OpenTelemetryStatus(True, False, "otlp-grpc", "SDK_DEPENDENCY_MISSING")
        if str(os.getenv("MENTORFLOW_OTEL_FAIL_OPEN") or "").casefold() not in {"1", "true", "yes"}:
            raise RuntimeError("OpenTelemetry is enabled but its SDK capability group is missing") from exc
        return _STATUS

    resource = Resource.create({
        "service.name": service_name[:64],
        "service.namespace": "mentorflow",
        "deployment.environment.name": environment[:32],
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith("http://"))))
    trace.set_tracer_provider(provider)
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, insecure=endpoint.startswith("http://")),
        export_interval_millis=max(5_000, int(os.getenv("MENTORFLOW_OTEL_METRIC_INTERVAL_MS") or "30000")),
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="/health/live,/health/ready,/health/startup,/internal/metrics",
    )
    HTTPXClientInstrumentor().instrument()
    if sqlalchemy_engine is not None:
        SQLAlchemyInstrumentor().instrument(engine=sqlalchemy_engine)
    _STATUS = OpenTelemetryStatus(True, True, "otlp-grpc")
    return _STATUS


def otel_status() -> OpenTelemetryStatus:
    return _STATUS


@contextmanager
def business_span(name: str, attributes: Mapping[str, object] | None = None) -> Iterator[object | None]:
    """Create an OTel span when available, keeping attribute values bounded."""

    context = current_correlation()
    correlation_attributes = {
        f"mentorflow.{key}": value
        for key, value in (context.log_fields() if context else {}).items()
    }
    safe_attributes = safe_span_attributes({**correlation_attributes, **(attributes or {})})
    try:
        from opentelemetry import trace
        tracer = trace.get_tracer("mentorflow.observability")
        with tracer.start_as_current_span(name, attributes=safe_attributes) as span:
            yield span
    except ImportError:
        yield None


__all__ = ["OpenTelemetryStatus", "business_span", "configure_open_telemetry", "otel_status"]
