"""FastAPI integration for correlation, metrics, health and SLO reads."""

from __future__ import annotations

import logging
import os
import secrets
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Iterator

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict

from .context import (
    CorrelationContext,
    correlation_scope,
    current_correlation,
    new_correlation_context,
)
from .health import health_registry
from .instrumentation import (
    observe_business_operation,
    observe_job,
    record_auth_attempt,
    record_authorization_denial,
    record_backup_freshness,
    record_db_pool,
    record_document_result,
    record_job_queued,
    record_provider_receipt,
    record_teacher_turn,
)
from .metrics import normalize_route, registry
from .privacy import emit_event
from .runtime import business_span, otel_status
from .slo import error_budgets


_LOGGER = logging.getLogger("mentorflow.operations")
router = APIRouter()


class StagingVerificationProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulate_provider_outage: bool = False


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


def _require_staging_verification(authorization: str | None) -> None:
    enabled = str(os.getenv("MENTORFLOW_STAGING_VERIFY_ENABLED") or "").casefold() in {
        "1",
        "true",
        "yes",
    }
    environment = str(os.getenv("MENTORFLOW_ENVIRONMENT") or "local").casefold()
    if not enabled or environment != "staging":
        raise HTTPException(status_code=404, detail="not found")
    expected = str(os.getenv("MENTORFLOW_STAGING_VERIFY_TOKEN") or "")
    if not expected:
        raise HTTPException(status_code=503, detail="staging verification token is not configured")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="staging verification authentication required")


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


@router.post("/internal/staging-verification/probe")
def staging_verification_probe(
    payload: StagingVerificationProbe,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """Exercise staging-only seams with synthetic metadata and a fake Provider."""

    _require_staging_verification(authorization)
    base = current_correlation() or new_correlation_context(feature="api")
    organization_id = str(
        os.getenv("MENTORFLOW_STAGING_VERIFY_ORGANIZATION_ID")
        or "e1-staging-synthetic-organization"
    )
    workspace_id = str(
        os.getenv("MENTORFLOW_STAGING_VERIFY_WORKSPACE_ID")
        or "e1-staging-synthetic-workspace"
    )
    job_id = f"e1-verify-{base.request_id[:32]}"
    with correlation_scope(
        base,
        organization_id=organization_id,
        workspace_id=workspace_id,
        feature="api",
    ) as scoped:
        if scoped.organization_id is None or scoped.workspace_id is None:
            raise HTTPException(status_code=503, detail="synthetic tenant identifiers are invalid")
        with observe_business_operation(
            "database",
            "synthetic-select",
            organization_id=organization_id,
            workspace_id=workspace_id,
        ):
            record_db_pool(size=4, checked_in=3, checked_out=1, overflow=0)
        record_job_queued(job_id, "document")
        with observe_job(job_id, "document", workspace_id=workspace_id):
            pass
        record_document_result(
            SimpleNamespace(
                status="success",
                receipts=(SimpleNamespace(status="success", stage="complete"),),
            ),
            elapsed_seconds=0.01,
        )
        record_teacher_turn(
            route="teacher-fast",
            status="success",
            ttft_ms=25.0,
            total_ms=60.0,
        )
        record_auth_attempt(False, denied=True)
        record_authorization_denial("api", "role_denied")
        record_backup_freshness("postgres", 60.0)
        record_provider_receipt(
            SimpleNamespace(
                provider="fake",
                provider_called=True,
                request_id=base.request_id,
                success=not payload.simulate_provider_outage,
                prompt_tokens=7,
                completion_tokens=3,
                total_tokens=10,
                prompt_cache_hit_tokens=2,
                prompt_cache_miss_tokens=5,
                estimated_cost_usd=None,
                local_result_cache_hit=True,
                local_cache_corrupt=False,
                cache_hit_ratio=0.2,
            )
        )
    return {
        "schema_version": "mentorflow.staging-verification-probe.v1",
        "status": "PROBED",
        "request_id": base.request_id,
        "trace_id": base.trace_id,
        "job_id": job_id,
        "organization_id": scoped.organization_id,
        "workspace_id": scoped.workspace_id,
        "provider_route": "fake",
        "provider_outage_simulated": payload.simulate_provider_outage,
    }


def install_observability(app: FastAPI) -> None:
    app.include_router(router)


__all__ = ["feature_from_path", "install_observability", "observe_http_request", "router"]
