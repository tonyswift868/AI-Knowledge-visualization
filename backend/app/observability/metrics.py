"""Bounded-cardinality operational metrics with Prometheus exposition."""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping


_DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
_TTFT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 3.0, 5.0, 10.0, 30.0)


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: str
    help: str
    kind: str
    labels: tuple[str, ...]
    allowed: Mapping[str, frozenset[str]]
    buckets: tuple[float, ...] = ()


def _values(*items: str) -> frozenset[str]:
    return frozenset((*items, "other"))


DEFINITIONS = {
    item.name: item
    for item in (
        MetricDefinition("mentorflow_http_requests_total", "HTTP requests", "counter", ("method", "route", "status_class", "feature"), {"method": _values("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"), "status_class": _values("2xx", "3xx", "4xx", "5xx"), "feature": _values("api", "auth", "documents", "health", "jobs", "plan", "provider", "review", "teacher", "unknown")}),
        MetricDefinition("mentorflow_http_request_duration_seconds", "HTTP latency", "histogram", ("method", "route", "feature"), {"method": _values("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"), "feature": _values("api", "auth", "documents", "health", "jobs", "plan", "provider", "review", "teacher", "unknown")}, _DURATION_BUCKETS),
        MetricDefinition("mentorflow_job_queue_depth", "Queued jobs", "gauge", ("queue",), {"queue": _values("default", "documents", "knowledge", "video")}),
        MetricDefinition("mentorflow_job_oldest_age_seconds", "Oldest queued job age", "gauge", ("queue",), {"queue": _values("default", "documents", "knowledge", "video")}),
        MetricDefinition("mentorflow_job_failures_total", "Job failures", "counter", ("queue", "reason"), {"queue": _values("default", "documents", "knowledge", "video"), "reason": _values("cancelled", "dependency", "timeout", "unhandled")}),
        MetricDefinition("mentorflow_document_processed_total", "Documents processed", "counter", ("stage", "status"), {"stage": _values("preflight", "conversion", "mapping", "handoff", "complete", "interrupted"), "status": _values("success", "partial", "failed", "limited", "unsupported", "duplicate", "interrupted")}),
        MetricDefinition("mentorflow_document_processing_duration_seconds", "Document batch duration", "histogram", ("status",), {"status": _values("success", "partial", "failed", "interrupted")}, _DURATION_BUCKETS),
        MetricDefinition("mentorflow_teacher_turns_total", "Teacher turns", "counter", ("route", "status"), {"route": _values("teacher-fast", "teacher-deep", "unknown"), "status": _values("success", "failed", "cancelled", "timeout")}),
        MetricDefinition("mentorflow_teacher_ttft_seconds", "Teacher time to first token", "histogram", ("route",), {"route": _values("teacher-fast", "teacher-deep", "unknown")}, _TTFT_BUCKETS),
        MetricDefinition("mentorflow_teacher_total_seconds", "Teacher total response time", "histogram", ("route",), {"route": _values("teacher-fast", "teacher-deep", "unknown")}, _DURATION_BUCKETS),
        MetricDefinition("mentorflow_provider_calls_total", "Provider calls", "counter", ("provider_route", "status"), {"provider_route": _values("claude", "deepseek", "fake", "gemini", "openai-compatible", "teacher-fast", "teacher-deep", "unknown"), "status": _values("success", "failed")}),
        MetricDefinition("mentorflow_provider_tokens_total", "Provider token usage", "counter", ("provider_route", "token_type"), {"provider_route": _values("claude", "deepseek", "fake", "gemini", "openai-compatible", "teacher-fast", "teacher-deep", "unknown"), "token_type": _values("prompt", "completion", "total", "cache_hit", "cache_miss")}),
        MetricDefinition("mentorflow_provider_cost_usd_total", "Provider cost in USD", "counter", ("provider_route", "cost_classification"), {"provider_route": _values("claude", "deepseek", "fake", "gemini", "openai-compatible", "teacher-fast", "teacher-deep", "unknown"), "cost_classification": _values("reported", "estimated", "unavailable")}),
        MetricDefinition("mentorflow_cache_requests_total", "Cache lookups", "counter", ("layer", "result"), {"layer": _values("provider", "result", "prefix", "retrieval"), "result": _values("hit", "miss", "corrupt", "unknown")}),
        MetricDefinition("mentorflow_db_pool_connections", "Database pool connections", "gauge", ("state",), {"state": _values("checked_in", "checked_out", "overflow", "size")}),
        MetricDefinition("mentorflow_rls_denials_total", "PostgreSQL RLS denials", "counter", ("resource",), {"resource": _values("workspace", "document", "job", "review", "plan", "audit")}),
        MetricDefinition("mentorflow_authorization_denials_total", "Authorization denials", "counter", ("feature", "reason"), {"feature": _values("api", "auth", "documents", "jobs", "plan", "review", "teacher", "unknown"), "reason": _values("missing_scope", "tenant_mismatch", "role_denied", "unauthenticated", "unknown")}),
        MetricDefinition("mentorflow_backup_freshness_seconds", "Age of last valid backup", "gauge", ("component",), {"component": _values("postgres", "objects", "keycloak")}),
        MetricDefinition("mentorflow_auth_attempts_total", "Authentication outcomes", "counter", ("result",), {"result": _values("success", "failed", "denied")}),
    )
}


class MetricRegistry:
    def __init__(self) -> None:
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
        self._lock = threading.RLock()

    def _labels(self, definition: MetricDefinition, labels: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
        if set(labels) != set(definition.labels):
            raise ValueError(f"labels for {definition.name} must be {definition.labels}")
        normalized: list[tuple[str, str]] = []
        for name in definition.labels:
            value = str(labels[name])
            allowed = definition.allowed.get(name)
            if allowed is not None and value not in allowed:
                value = "other"
            if name == "route":
                value = normalize_route(value)
            normalized.append((name, value))
        return tuple(normalized)

    def add(self, name: str, amount: float = 1.0, **labels: object) -> None:
        definition = DEFINITIONS[name]
        if definition.kind != "counter" or not math.isfinite(amount) or amount < 0:
            raise ValueError(f"invalid counter update for {name}")
        key = (name, self._labels(definition, labels))
        with self._lock:
            self._values[key] += float(amount)

    def set(self, name: str, value: float, **labels: object) -> None:
        definition = DEFINITIONS[name]
        if definition.kind != "gauge" or not math.isfinite(value):
            raise ValueError(f"invalid gauge update for {name}")
        key = (name, self._labels(definition, labels))
        with self._lock:
            self._values[key] = float(value)

    def observe(self, name: str, value: float, **labels: object) -> None:
        definition = DEFINITIONS[name]
        if definition.kind != "histogram" or not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid histogram observation for {name}")
        key = (name, self._labels(definition, labels))
        with self._lock:
            self._histograms[key].append(float(value))

    def reset(self) -> None:
        with self._lock:
            self._values.clear()
            self._histograms.clear()

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name, definition in DEFINITIONS.items():
                lines.extend((f"# HELP {name} {definition.help}", f"# TYPE {name} {definition.kind}"))
                for (metric_name, labels), value in sorted(self._values.items()):
                    if metric_name == name:
                        lines.append(f"{name}{_render_labels(labels)} {value:g}")
                for (metric_name, labels), values in sorted(self._histograms.items()):
                    if metric_name != name:
                        continue
                    running = 0
                    for bucket in definition.buckets:
                        running = sum(sample <= bucket for sample in values)
                        bucket_labels = (*labels, ("le", f"{bucket:g}"))
                        lines.append(f"{name}_bucket{_render_labels(bucket_labels)} {running}")
                    lines.append(f"{name}_bucket{_render_labels((*labels, ('le', '+Inf')))} {len(values)}")
                    lines.append(f"{name}_count{_render_labels(labels)} {len(values)}")
                    lines.append(f"{name}_sum{_render_labels(labels)} {sum(values):g}")
        return "\n".join(lines) + "\n"


def _render_labels(labels: Iterable[tuple[str, str]]) -> str:
    items = tuple(labels)
    if not items:
        return ""
    escaped = [f'{name}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"' for name, value in items]
    return "{" + ",".join(escaped) + "}"


def normalize_route(path: str) -> str:
    """Collapse unbounded IDs while retaining a small useful route family."""

    raw_path = "/" + "/".join(part for part in str(path or "/").split("/") if part)
    if raw_path.startswith("/api/jobs/"):
        return "/api/jobs/:id"
    parts = [part for part in raw_path.split("/") if part]
    stable: list[str] = []
    for part in parts[:4]:
        if len(part) > 24 or any(character.isdigit() for character in part):
            stable.append(":id")
        else:
            stable.append(part.casefold())
    route = "/" + "/".join(stable)
    approved = {
        "/api",
        "/api/document-import",
        "/api/jobs/:id",
        "/api/plan-product",
        "/api/provider-sessions",
        "/api/providers",
        "/api/review",
        "/api/teacher-live",
        "/api/workspaces",
        "/health/live",
        "/health/ready",
        "/health/startup",
        "/internal/metrics",
        "/internal/staging-verification",
        "/internal/slo",
    }
    for candidate in sorted(approved, key=len, reverse=True):
        if route.startswith(candidate):
            return candidate
    return "/api" if route.startswith("/api") else "other"


registry = MetricRegistry()


__all__ = ["DEFINITIONS", "MetricRegistry", "normalize_route", "registry"]
