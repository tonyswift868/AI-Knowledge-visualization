"""Health, OTel, metric, dashboard, and alert staging probes."""

from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from ..privacy import UnsafeTelemetryContent, scan_telemetry_text
from .adapters import Clock, CommandRunner, HttpClient, record_command
from .models import CheckResult, EnterpriseStagingEvidenceBundle, VerificationStatus, utc_now
from .settings import VerificationSettings


_REQUIRED_METRICS = {
    "mentorflow_http_requests_total",
    "mentorflow_http_request_duration_seconds",
    "mentorflow_auth_attempts_total",
    "mentorflow_authorization_denials_total",
    "mentorflow_job_queue_depth",
    "mentorflow_job_oldest_age_seconds",
    "mentorflow_document_processed_total",
    "mentorflow_teacher_ttft_seconds",
    "mentorflow_teacher_total_seconds",
    "mentorflow_cache_requests_total",
    "mentorflow_db_pool_connections",
    "mentorflow_backup_freshness_seconds",
}
_REQUIRED_DASHBOARD_ROWS = {
    "System",
    "Identity",
    "Tenant Security",
    "Teacher/AI",
    "Jobs",
    "Documents",
    "Cost/Cache",
}
_REQUIRED_PANEL_TITLES = {
    "System - request and error rate",
    "Identity - authentication outcomes",
    "Tenant Security - authorization and RLS denials",
    "Teacher/AI - availability, TTFT and total latency",
    "Jobs - depth, age and failures",
    "Documents - throughput and failures",
    "Cost/Cache - cost, tokens and cache outcomes",
}
_FORBIDDEN_CARDINALITY_LABELS = {
    "document_id",
    "email",
    "job_id",
    "membership_id",
    "model",
    "organization_id",
    "prompt_version",
    "request_id",
    "subject_id",
    "trace_id",
    "user_id",
    "workspace_id",
}
_REQUIRED_ALERTS = {
    "MentorFlowServiceDown",
    "MentorFlowHighErrorRate",
    "MentorFlowQueueStuck",
    "MentorFlowWorkerFailureSpike",
    "MentorFlowBackupStale",
    "MentorFlowAuthDenialAnomaly",
    "MentorFlowTenantIsolationAnomaly",
    "MentorFlowProviderOutage",
}


def _result(
    check_id: str,
    category: str,
    status: VerificationStatus,
    summary: str,
    details: dict[str, object] | None = None,
    *,
    started_at: str | None = None,
    started: float | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        category=category,
        status=status,
        started_at=started_at or utc_now(),
        finished_at=utc_now(),
        duration_ms=max(0, int((time.perf_counter() - (started or time.perf_counter())) * 1000)),
        summary=summary,
        details=details or {},
    )


def _json_response(response_body: str) -> dict[str, object] | None:
    try:
        payload = json.loads(response_body)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def run_health_probe(settings: VerificationSettings, *, http: HttpClient) -> list[CheckResult]:
    results: list[CheckResult] = []
    expectations = (
        ("live", "live", 200),
        ("startup", "started", 200),
        ("ready", "ready", 200),
    )
    for name, expected_status, expected_code in expectations:
        started_at, started = utc_now(), time.perf_counter()
        response = http.request("GET", f"{settings.base_url}/health/{name}")
        payload = _json_response(response.body)
        actual = str((payload or {}).get("status") or "")
        passed = response.status_code == expected_code and actual == expected_status
        details: dict[str, object] = {
            "status_code": response.status_code,
            "reported_status": actual or "INVALID_JSON",
        }
        if name == "ready" and payload is not None:
            dependencies = payload.get("dependencies")
            if not isinstance(dependencies, list):
                passed = False
            else:
                dependency_codes: list[str] = []
                for item in dependencies:
                    if not isinstance(item, dict) or not bool(item.get("ready")):
                        passed = False
                    if isinstance(item, dict):
                        dependency_codes.append(str(item.get("code") or "UNKNOWN")[:64])
                details["dependency_codes"] = dependency_codes
        results.append(
            _result(
                f"health.{name}",
                "health",
                VerificationStatus.PASS if passed else VerificationStatus.FAIL,
                f"{name} probe passed" if passed else f"{name} probe failed",
                details,
                started_at=started_at,
                started=started,
            )
        )
    return results


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"} if value else {}


def run_metric_probe(settings: VerificationSettings, *, http: HttpClient) -> CheckResult:
    started_at, started = utc_now(), time.perf_counter()
    if settings.environment == "staging" and not settings.metrics_bearer_token:
        return _result(
            "metrics.catalogue",
            "metrics",
            VerificationStatus.BLOCKED,
            "staging metrics bearer token is not configured",
            {"required_metrics": sorted(_REQUIRED_METRICS)},
            started_at=started_at,
            started=started,
        )
    response = http.request(
        "GET",
        f"{settings.base_url}/internal/metrics",
        headers=_bearer(settings.metrics_bearer_token),
    )
    missing = sorted(name for name in _REQUIRED_METRICS if name not in response.body)
    observed_labels = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)=", response.body))
    cardinality_labels = sorted(observed_labels & _FORBIDDEN_CARDINALITY_LABELS)
    safe = True
    try:
        scan_telemetry_text(response.body)
    except UnsafeTelemetryContent:
        safe = False
    passed = response.status_code == 200 and not missing and not cardinality_labels and safe
    return _result(
        "metrics.catalogue",
        "metrics",
        VerificationStatus.PASS if passed else VerificationStatus.FAIL,
        "metric probe passed" if passed else "metric probe failed",
        {
            "status_code": response.status_code,
            "missing_metrics": missing,
            "forbidden_cardinality_labels": cardinality_labels,
            "sensitive_scan_passed": safe,
        },
        started_at=started_at,
        started=started,
    )


def run_otel_probe(
    settings: VerificationSettings,
    *,
    http: HttpClient,
    clock: Clock,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    if not settings.staging_verify_token:
        results.append(
            _result(
                "otel.synthetic_request",
                "otel",
                VerificationStatus.BLOCKED,
                "staging verification token is not configured",
            )
        )
        results.append(run_metric_probe(settings, http=http))
        return results

    status_headers = _bearer(settings.metrics_bearer_token)
    status_response = http.request(
        "GET",
        f"{settings.base_url}/internal/observability/status",
        headers=status_headers,
    )
    status_payload = _json_response(status_response.body) or {}
    otel_status = status_payload.get("opentelemetry")
    otel_ready = (
        status_response.status_code == 200
        and isinstance(otel_status, dict)
        and bool(otel_status.get("enabled"))
        and bool(otel_status.get("ready"))
    )
    results.append(
        _result(
            "otel.sdk_status",
            "otel",
            VerificationStatus.PASS if otel_ready else VerificationStatus.BLOCKED,
            "application OTel SDK is ready" if otel_ready else "application OTel SDK is disabled or unavailable",
            {
                "status_code": status_response.status_code,
                "enabled": bool(otel_status.get("enabled")) if isinstance(otel_status, dict) else False,
                "ready": bool(otel_status.get("ready")) if isinstance(otel_status, dict) else False,
                "exporter": str(otel_status.get("exporter") or "unknown") if isinstance(otel_status, dict) else "unknown",
            },
        )
    )

    trace_id = secrets.token_hex(16)
    parent_span_id = secrets.token_hex(8)
    request_id = f"e1-06b-{secrets.token_hex(12)}"
    headers = {
        **_bearer(settings.staging_verify_token),
        "traceparent": f"00-{trace_id}-{parent_span_id}-01",
        "X-Request-ID": request_id,
    }
    started_at, started = utc_now(), time.perf_counter()
    response = http.request(
        "POST",
        f"{settings.base_url}/internal/staging-verification/probe",
        headers=headers,
        json_body={"simulate_provider_outage": False},
        timeout_seconds=10.0,
    )
    payload = _json_response(response.body) or {}
    response_headers = {str(key).casefold(): str(value) for key, value in response.headers.items()}
    response_safe = True
    try:
        scan_telemetry_text(response.body)
    except UnsafeTelemetryContent:
        response_safe = False
    correlation_passed = (
        response.status_code == 200
        and payload.get("status") == "PROBED"
        and payload.get("request_id") == request_id
        and payload.get("trace_id") == trace_id
        and response_headers.get("x-request-id") == request_id
        and response_headers.get("x-trace-id") == trace_id
        and bool(payload.get("organization_id"))
        and bool(payload.get("workspace_id"))
        and bool(payload.get("job_id"))
        and response_safe
    )
    results.append(
        _result(
            "otel.synthetic_request",
            "otel",
            VerificationStatus.PASS if correlation_passed else VerificationStatus.FAIL,
            "synthetic correlated request passed" if correlation_passed else "synthetic correlated request failed",
            {
                "status_code": response.status_code,
                "request_id_match": payload.get("request_id") == request_id,
                "trace_id_match": payload.get("trace_id") == trace_id,
                "organization_present": bool(payload.get("organization_id")),
                "workspace_present": bool(payload.get("workspace_id")),
                "job_present": bool(payload.get("job_id")),
                "sensitive_scan_passed": response_safe,
            },
            started_at=started_at,
            started=started,
        )
    )

    trace_body = ""
    trace_status = 0
    deadline = clock.monotonic() + settings.trace_wait_seconds
    while clock.monotonic() <= deadline:
        trace_response = http.request(
            "GET",
            f"{settings.tempo_url}/api/v2/traces/{trace_id}",
            timeout_seconds=5.0,
        )
        trace_status = trace_response.status_code
        if trace_status == 200 and trace_response.body:
            trace_body = trace_response.body
            break
        clock.sleep(1.0)
    required_span_names = {
        "mentorflow.database.synthetic-select",
        "mentorflow.job.execute",
        "mentorflow.provider.receipt",
    }
    missing_spans = sorted(name for name in required_span_names if name not in trace_body)
    missing_correlation = sorted(
        label
        for label, value in (
            ("request_id", request_id),
            ("organization_id", payload.get("organization_id")),
            ("workspace_id", payload.get("workspace_id")),
            ("job_id", payload.get("job_id")),
        )
        if not value or str(value) not in trace_body
    )
    trace_safe = True
    try:
        scan_telemetry_text(trace_body)
    except UnsafeTelemetryContent:
        trace_safe = False
    trace_passed = trace_status == 200 and not missing_spans and not missing_correlation and trace_safe
    results.append(
        _result(
            "otel.tempo_trace",
            "otel",
            VerificationStatus.PASS if trace_passed else VerificationStatus.FAIL,
            "Tempo trace correlation passed" if trace_passed else "Tempo trace correlation failed",
            {
                "status_code": trace_status,
                "missing_spans": missing_spans,
                "missing_correlation": missing_correlation,
                "sensitive_scan_passed": trace_safe,
            },
        )
    )
    results.append(run_metric_probe(settings, http=http))
    return results


def run_dashboard_gate(settings: VerificationSettings, *, http: HttpClient) -> list[CheckResult]:
    results: list[CheckResult] = []
    dashboard_path = settings.repo_root / "ops" / "observability" / "grafana" / "dashboards" / "mentorflow-enterprise-e1.json"
    datasource_path = settings.repo_root / "ops" / "observability" / "grafana" / "provisioning" / "datasources" / "datasources.yml"
    started_at, started = utc_now(), time.perf_counter()
    try:
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
        datasource_text = datasource_path.read_text(encoding="utf-8")
    except (OSError, TypeError, ValueError):
        dashboard, datasource_text = {}, ""
    panels = dashboard.get("panels") if isinstance(dashboard, dict) else None
    rows = {
        str(panel.get("title"))
        for panel in panels or []
        if isinstance(panel, dict) and panel.get("type") == "row"
    }
    titles = {
        str(panel.get("title"))
        for panel in panels or []
        if isinstance(panel, dict) and panel.get("type") != "row"
    }
    expressions = sorted(
        {
            str(target.get("expr"))
            for panel in panels or []
            if isinstance(panel, dict)
            for target in panel.get("targets", [])
            if isinstance(target, dict) and target.get("expr")
        }
    )
    datasource_uids = {
        str((panel.get("datasource") or {}).get("uid"))
        for panel in panels or []
        if isinstance(panel, dict) and isinstance(panel.get("datasource"), dict)
    }
    missing_rows = sorted(_REQUIRED_DASHBOARD_ROWS - rows)
    missing_panels = sorted(_REQUIRED_PANEL_TITLES - titles)
    missing_datasources = sorted(uid for uid in datasource_uids if f"uid: {uid}" not in datasource_text)
    static_passed = bool(expressions) and not missing_rows and not missing_panels and not missing_datasources
    results.append(
        _result(
            "dashboard.static_definition",
            "dashboard",
            VerificationStatus.PASS if static_passed else VerificationStatus.FAIL,
            "dashboard definition gate passed" if static_passed else "dashboard definition gate failed",
            {
                "missing_rows": missing_rows,
                "missing_panels": missing_panels,
                "missing_datasources": missing_datasources,
                "query_count": len(expressions),
            },
            started_at=started_at,
            started=started,
        )
    )

    query_failures: list[int] = []
    for index, expression in enumerate(expressions, start=1):
        response = http.request(
            "GET",
            f"{settings.prometheus_url}/api/v1/query?query={quote(expression, safe='')}",
            timeout_seconds=5.0,
        )
        payload = _json_response(response.body) or {}
        if response.status_code != 200 or payload.get("status") != "success":
            query_failures.append(index)
    results.append(
        _result(
            "dashboard.promql_syntax",
            "dashboard",
            VerificationStatus.PASS if expressions and not query_failures else VerificationStatus.FAIL,
            "Prometheus accepted dashboard queries" if expressions and not query_failures else "Prometheus rejected dashboard queries",
            {"query_count": len(expressions), "failed_query_indexes": query_failures},
        )
    )

    if not settings.grafana_bearer_token:
        results.append(
            _result(
                "dashboard.grafana_rendered",
                "dashboard",
                VerificationStatus.BLOCKED,
                "Grafana service token is not configured",
            )
        )
    else:
        headers = _bearer(settings.grafana_bearer_token)
        datasource_failures: list[str] = []
        for uid in ("prometheus", "tempo", "loki"):
            response = http.request("GET", f"{settings.grafana_url}/api/datasources/uid/{uid}", headers=headers)
            health = http.request(
                "GET",
                f"{settings.grafana_url}/api/datasources/uid/{uid}/health",
                headers=headers,
            )
            if response.status_code != 200 or health.status_code != 200:
                datasource_failures.append(uid)
        search = http.request(
            "GET",
            f"{settings.grafana_url}/api/search?query={quote('MentorFlow Enterprise E1', safe='')}",
            headers=headers,
        )
        try:
            search_payload = json.loads(search.body)
        except (TypeError, ValueError):
            search_payload = []
        dashboard_found = isinstance(search_payload, list) and any(
            isinstance(item, dict) and item.get("uid") == "mentorflow-enterprise-e1"
            for item in search_payload
        )
        rendered_passed = not datasource_failures and search.status_code == 200 and dashboard_found
        results.append(
            _result(
                "dashboard.grafana_rendered",
                "dashboard",
                VerificationStatus.PASS if rendered_passed else VerificationStatus.FAIL,
                "Grafana datasources and dashboard are rendered" if rendered_passed else "Grafana provisioning is incomplete",
                {"missing_datasources": datasource_failures, "dashboard_found": dashboard_found},
            )
        )
    return results


def run_alert_harness(
    settings: VerificationSettings,
    bundle: EnterpriseStagingEvidenceBundle,
    *,
    runner: CommandRunner,
) -> list[CheckResult]:
    alerts_path = settings.repo_root / "ops" / "observability" / "alerts.yml"
    tests_path = settings.repo_root / "ops" / "observability" / "alert-tests.yml"
    try:
        alerts_text = alerts_path.read_text(encoding="utf-8")
    except OSError:
        alerts_text = ""
    missing = sorted(name for name in _REQUIRED_ALERTS if f"alert: {name}" not in alerts_text)
    static_passed = tests_path.is_file() and not missing
    results = [
        _result(
            "alerts.catalogue",
            "alerts",
            VerificationStatus.PASS if static_passed else VerificationStatus.FAIL,
            "alert catalogue and simulations are present" if static_passed else "alert catalogue is incomplete",
            {"missing_alerts": missing, "simulation_file_present": tests_path.is_file()},
        )
    ]
    if runner.which("promtool") is None:
        results.append(
            _result(
                "alerts.promtool",
                "alerts",
                VerificationStatus.BLOCKED,
                "promtool is missing; alert states were not evaluated",
            )
        )
        return results
    check = record_command(bundle, runner, ("promtool", "check", "rules", str(alerts_path)))
    test = record_command(bundle, runner, ("promtool", "test", "rules", str(tests_path)))
    passed = check.exit_code == 0 and test.exit_code == 0
    results.append(
        _result(
            "alerts.promtool",
            "alerts",
            VerificationStatus.PASS if passed else VerificationStatus.FAIL,
            "promtool alert simulations passed" if passed else "promtool alert simulations failed",
            {
                "check_command_id": bundle.commands[-2].command_id,
                "test_command_id": bundle.commands[-1].command_id,
                "expected_scenarios": [
                    "service_down",
                    "error_spike",
                    "queue_stuck",
                    "backup_stale",
                    "provider_outage",
                ],
            },
        )
    )
    return results


__all__ = [
    "run_alert_harness",
    "run_dashboard_gate",
    "run_health_probe",
    "run_metric_probe",
    "run_otel_probe",
]
