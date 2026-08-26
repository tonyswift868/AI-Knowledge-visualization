"""FastAPI integration for correlation, metrics, health and SLO reads."""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .context import CorrelationContext, correlation_scope, new_correlation_context
from .health import health_registry
from .metrics import normalize_route, registry
from .privacy import emit_event
from .runtime import business_span, otel_status
from .slo import error_budgets


_LOGGER = logging.getLogger("mentorflow.operations")
router = APIRouter()


def feature_from_path(path: str) -> str:
    value = str(path or "").casefold()
    for marker, feature in (
        ("/health", "health"),
        ("/document-import", "documents"),
        ("/teacher", "teacher"),
        ("/review", "review"),
        ("/plan", "plan"),
        ("/jobs", "jobs"),
        ("/provider", "provider"),
        ("/auth", "auth"),
    ):
        if marker in value:
            return feature
    return "api"


@contextmanager
def observe_http_request(request: Request) -> Iterator[CorrelationContext]:
    path = request.url.path
    feature = feature_from_path(path)
    context = new_correlation_context(
        request_id=request.headers.get("X-Request-ID"),
        traceparent=request.headers.get("traceparent"),
        feature=feature,
    )
    method = request.method.upper()
    route = normalize_route(path)
    started = time.perf_counter()
    status_code = 500
    with correlation_scope(context), business_span(
        f"{method} {route}",
        {"http.request.method": method, "http.route": route, "mentorflow.feature": feature},
    ):
        try:
            request.state.request_id = context.request_id
            request.state.trace_id = context.trace_id
            request.state.correlation = context
            yield context
            status_code = int(getattr(request.state, "response_status_code", 200))
        finally:
            duration = max(0.0, time.perf_counter() - started)
            status_class = f"{min(5, max(1, status_code // 100))}xx"
            registry.add("mentorflow_http_requests_total", method=method, route=route, status_class=status_class, feature=feature)
            registry.observe("mentorflow_http_request_duration_seconds", duration, method=method, route=route, feature=feature)
            if feature not in {"teacher", "provider", "health"} and path.startswith("/api"):
                error_budgets.record("non_ai_api_availability", status_code < 500)
                error_budgets.record("non_ai_api_latency", duration <= 0.5)
            emit_event(
                _LOGGER,
                "http_request_completed",
                method=method,
                route=route,
                feature=feature,
                status_code=status_code,
                duration_ms=int(duration * 1000),
                outcome="success" if status_code < 500 else "failed",
            )


def _require_metrics_token(authorization: str | None) -> None:
    expected = str(os.getenv("MENTORFLOW_METRICS_BEARER_TOKEN") or "").strip()
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="metrics authentication required")


@router.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "live", "service": "mentorflow-backend"}


@router.get("/health/startup")
def startup() -> JSONResponse:
    ready = health_registry.started
    return JSONResponse({"status": "started" if ready else "starting"}, status_code=200 if ready else 503)


@router.get("/health/ready")
def readiness() -> JSONResponse:
    payload = health_registry.readiness()
    return JSONResponse(payload, status_code=200 if payload["status"] == "ready" else 503)


@router.get("/internal/metrics")
def prometheus_metrics(authorization: str | None = Header(default=None)) -> PlainTextResponse:
    _require_metrics_token(authorization)
    return PlainTextResponse(registry.render_prometheus(), media_type="text/plain; version=0.0.4")


@router.get("/internal/slo")
def slo_read_model(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _require_metrics_token(authorization)
    return {"environment": "staging", "slos": error_budgets.snapshots()}


@router.get("/internal/observability/status")
def observability_status(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _require_metrics_token(authorization)
    return {"opentelemetry": otel_status().to_dict(), "correlation_contract": "mentorflow.correlation.v1"}


def install_observability(app: FastAPI) -> None:
    app.include_router(router)


__all__ = ["feature_from_path", "install_observability", "observe_http_request", "router"]
